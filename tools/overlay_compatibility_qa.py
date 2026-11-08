"""Observe three real bootstrap launches with the shipped client-only policy.

No compatibility variable is injected by this test. The actual native bootstrap
must read presentation.json. Vulkan and the loaded native modules are observed
in its owned renderer process; registry and other applications are read-only.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from bootstrap_contract import validate_environment_defaults
from native_notices_qa import Session, registered_output, remove_fixture
from release_acceptance import execution_context
from stage_runtime import file_info, read_json


STARTUP = "actual bootstrap child; inherited defaults untouched; Vulkan modules inspected"


def module_snapshot(pid):
    script = (
        f"$p=Get-Process -Id {pid:d} -ErrorAction Stop; "
        "@{pid=[int]$p.Id; path=$p.Path; modules=@($p.Modules | ForEach-Object {"
        "@{name=$_.ModuleName; path=$_.FileName}})} | ConvertTo-Json -Depth 4 -Compress"
    )
    value = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return json.loads(value.stdout)


def exercise(candidate, controller, cache, output):
    context = execution_context(candidate)
    policy_path = candidate / "native/presentation.json"
    defaults = validate_environment_defaults(read_json(policy_path)["environmentDefaults"])
    inherited = {key: {"present": key in os.environ, "value": os.environ.get(key)}
                 for key in defaults}
    if any(value["present"] for value in inherited.values()):
        raise ValueError("An explicit inherited override prevents testing the default path")
    report = {"schemaVersion": 1, "status": "FAIL", "startup": STARTUP,
              "acceptanceContext": context, "environmentDefaults": defaults,
              "presentation": file_info(policy_path), "inheritedDefaults": inherited,
              "client": file_info(candidate / "native/horizon-client.exe"),
              "bootstrap": file_info(candidate / "native/launch-rust-edition.exe"),
              "runs": [], "fixtureRemoved": False}
    all_removed = True
    try:
        for index in range(1, 4):
            qa = output / f"run-{index}"
            qa.mkdir()
            session = Session(candidate, controller, cache, qa)
            success = False
            try:
                parent_before = dict(os.environ)
                session.start()
                if dict(os.environ) != parent_before:
                    raise AssertionError("QA changed its parent environment")
                page = session.control("ui")
                if page["screen"] != "title" or len(page["widgets"]) < 5:
                    raise AssertionError("The owned renderer did not reach its real title")
                modules = module_snapshot(session.renderer_pid)
                if modules["pid"] != session.renderer_pid or Path(modules["path"]).resolve() != session.client:
                    raise AssertionError("Module snapshot came from another process")
                names = {item["name"].casefold() for item in modules["modules"]}
                if "owclient.dll" in names or "vulkan-1.dll" not in names:
                    raise AssertionError("Overwolf client is loaded or Vulkan loader is absent")
                heartbeat = session.wait_heartbeat(0)
                observation = {"index": index, "arguments": session.arguments,
                               "nativeProcessId": session.renderer_pid,
                               "bootstrapProcessId": session.process.pid,
                               "processAncestry": session.ancestry,
                               "moduleSnapshot": modules,
                               "parentEnvironmentUnchanged": dict(os.environ) == parent_before,
                               "title": page, "observedUnixMs": time.time_ns() // 1_000_000,
                               "presentationHeartbeat": heartbeat}
                success = True
            finally:
                try:
                    session.stop(success)
                finally:
                    for folder in (session.game, session.directory, qa / "empty-profile"):
                        if folder.exists():
                            remove_fixture(folder, qa)
                    session.endpoint.unlink(missing_ok=True)
            log = (qa / "client.log").read_text(encoding="utf-8", errors="replace")
            backend = next((line for line in log.splitlines()
                            if line.startswith("Horizon renderer: ") and "(Vulkan," in line), None)
            if not backend or "Prepared Horizon native-client resources" in log:
                raise AssertionError("Expected a warm launch on the actual Vulkan backend")
            observation.update(exitCode=session.process.returncode, warm=True,
                               rendererBackendLine=backend, log=file_info(qa / "client.log"))
            report["runs"].append(observation)
        if execution_context(candidate) != context:
            raise AssertionError("Candidate changed during compatibility acceptance")
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        for qa in output.glob("run-*"):
            if any((qa / name).exists() for name in ("game", "session", "empty-profile")):
                all_removed = False
        report["fixtureRemoved"] = all_removed
        (output / "overlay-compatibility-qa.json").write_bytes(
            (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--controller", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise ValueError("This test needs the actual Windows x64 client")
    output = registered_output(args.out)
    output.mkdir(parents=True)
    exercise(args.candidate.resolve(strict=True), args.controller.resolve(strict=True),
             args.cache.resolve(strict=True), output)


if __name__ == "__main__":
    main()
