"""Windows launch routing for QA of an Edition profile on the shared runtime."""

import json
import os
from pathlib import Path
import re
import subprocess

from smoke_startup import MARKERS, owned_root, snapshot
from stage_runtime import file_info, read_json


SHARED_ENV = "LANMINECRAFT_QA_SHARED_REPORT"
CLIENT = "native/horizon-client.exe"
BOOTSTRAP = "native/launch-rust-edition.exe"


def require(condition, message):
    if not condition:
        raise ValueError(message)


class SharedLaunch:
    def __init__(self, candidate, report_path):
        self.candidate = candidate
        self.run = owned_root(candidate)
        require(owned_root(report_path) == self.run, "Shared smoke must be in the same registered run")
        report = read_json(report_path)
        lock = read_json(candidate / "tools/distribution-lock.json")
        require(report["status"] == "PASS" and report["entryPoint"] == "real common Windows bootstrap"
                and file_info(report_path) == lock["sharedProfileSmoke"],
                "Shared smoke does not match the proposed distribution lock")
        descriptor = read_json(candidate / "portable-pack.json")
        self.variant = descriptor["nativeRuntime"]["version"]
        self.spec = descriptor["nativeRuntime"]["sharedRuntime"]
        self.bootstrap = self.run / "runtime-store" / self.spec["archive"]["sha256"] / BOOTSTRAP
        self.client = self.bootstrap.with_name("horizon-client.exe")
        title = read_json(candidate / "native/presentation.json")["name"]
        self.overlay = self.run / "overlays" / title
        self.presentation = self.overlay / "native/presentation.json"
        roots = (self.client, self.bootstrap, self.overlay)
        require(all(path.resolve(strict=True) == path and owned_root(path) == self.run for path in roots),
                "Shared executable and overlay must be ordinary paths in the registered run")
        profile, = (item for item in report["profiles"] if item["windowTitle"] == title)
        require(profile["nativeVariant"] == self.variant and Path(profile["ancestry"][0]["path"]) == self.client
                and report["runtime"]["archiveSha256"] == self.spec["archive"]["sha256"],
                "Shared runtime profile differs from the executed child")
        require(len({item["nativeVariant"] for item in report["profiles"]}) == len(report["profiles"]),
                "Shared profile smoke lacks distinct native compatibility variants")
        for name, expected in ((CLIENT, report["runtime"]["client"]), (BOOTSTRAP, report["runtime"]["bootstrap"]),
                               ("portable-pack.json", profile["descriptor"]),
                               ("native/native-mods.json", profile["nativeRegistry"])):
            original = (self.overlay if name in ("portable-pack.json", "native/native-mods.json")
                        else self.client.parent.parent) / name
            require(file_info(candidate / name) == file_info(original) == expected,
                    f"The verified Edition profile changed: {name}")
        require(file_info(self.presentation) == file_info(candidate / "native/presentation.json"),
                "The Edition profile presentation changed")
        self.preparation = report["sharedPreparation"]
        self.resources = Path(self.preparation["resources"])
        self.receipt = self.resources.parent / "resources.json"
        self.prepared = self.resources.parent.parent
        require(self.resources.resolve(strict=True) == self.resources and owned_root(self.resources) == self.run
                and self.resources.parent.name == self.preparation["key"]
                and snapshot(self.receipt) == self.preparation["receipt"],
                "Shared prepared resources no longer match the executed profile")

    @classmethod
    def load(cls, candidate):
        report = os.environ.get(SHARED_ENV)
        return cls(candidate, Path(report).resolve(strict=True)) if report else None

    def environment(self, values, cache=None):
        values.update(LANMINECRAFT_NATIVE_PACK_ROOT=str(self.overlay),
                      LANMINECRAFT_NATIVE_VARIANT=self.variant,
                      LANMINECRAFT_BUILD_NAME=self.overlay.name)
        if cache is not None:
            require(owned_root(cache) == self.run and not cache.is_relative_to(self.prepared),
                    "Use a private bootstrap session cache in this registered task")
            values.update(LANMINECRAFT_NATIVE_PREPARED_CACHE=str(self.prepared),
                          LANMINECRAFT_NATIVE_CACHE=str(cache))
        return values

    def check_output(self, path):
        require(owned_root(path) == self.run and not path.is_relative_to(self.prepared),
                "Use a QA output in the same registered run outside the shared preparation")

    def ordinary_endpoint(self, cache):
        return cache / "process-temp" / "horizon-control.json"

    def observe(self, process, log):
        from smoke_shared_native import actual_child, used_path

        renderer, arguments = actual_child(process, self.client)
        require(used_path(arguments, "--resources") == self.resources
                and snapshot(self.receipt) == self.preparation["receipt"],
                "The shared bootstrap used another resource generation or replaced its receipt")
        count_match = re.search(MARKERS["cacheVerified"], log.read_text(encoding="utf-8", errors="replace"))
        require(count_match and int(count_match[1]) == self.preparation["files"],
                "The real shared bootstrap did not verify the prepared resource inventory")
        return {"bootstrapProcessId": process.pid, "nativeProcessId": renderer, "clientArguments": arguments,
                "sharedRuntimeArchiveSha256": self.spec["archive"]["sha256"]}


def stop_owned_tree(process):
    """Stop only this QA's still-owned bootstrap and any dependent child."""
    if process.poll() is None:
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=15, check=False)
        process.wait(timeout=15)
