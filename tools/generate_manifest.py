"""Package and verify an approved Minecraft Rust Edition release candidate."""

import argparse
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

from stage_runtime import (
    CONTROL_FILES, LIBRARY_SOURCES, LOCK, ROOT, file_info, read_json, safe_path,
    verify_file, verify_overlay, verify_pe,
)


FEATURES = ["native-runtime-v1", "native-steam-v1", "minecraft-26-world-layout-v1"]
RELEASE_LOCK = ROOT / "tools/distribution-lock.json"


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def inventory(candidate):
    native = candidate / "native"
    if not native.is_dir() or native.is_symlink() or native.is_junction():
        raise ValueError("A regular native payload directory is required")
    paths = {name: candidate / name for name in ("portable-pack.json", *CONTROL_FILES)}
    for entry in native.rglob("*"):
        if entry.is_symlink() or entry.is_junction():
            raise ValueError(f"Distribution cannot contain links: {entry.name}")
        if entry.is_file():
            relative = entry.relative_to(candidate).as_posix()
            safe_path(relative)
            paths[relative] = entry
    if len(paths) != len({name.casefold() for name in paths}):
        raise ValueError("Case-insensitive duplicate distribution paths")
    return {name: file_info(paths[name]) for name in sorted(paths)}


def describe_candidate(candidate):
    upstream = read_json(LOCK)
    files = inventory(candidate)

    runtime = read_json(candidate / "portable-pack.json")
    descriptor = read_json(ROOT / "portable-pack.json")
    if runtime != descriptor or runtime["nativeRuntime"]["version"] != upstream["version"]:
        raise ValueError("Runtime descriptor differs from the pinned Horizon build")
    if runtime["nativeRuntime"]["executable"] not in files or "native/steam_api64.dll" not in files:
        raise ValueError("The accepted runtime and its native Steam dependency are required")
    verify_pe(candidate / runtime["nativeRuntime"]["executable"])
    if any(path.lower().endswith("/horizon-launcher.exe") for path in files):
        raise ValueError("A separate Horizon launcher is forbidden")
    content = read_json(ROOT / "library/content.json")
    if content["nativeProject"] != {
        "name": "Horizon", "version": upstream["displayVersion"], "revision": upstream["commit"],
    }:
        raise ValueError("Library metadata does not describe the pinned project")
    if read_json(candidate / "native/presentation.json") != {"name": content["name"]}:
        raise ValueError("The native display name must come from library/content.json")
    preview = ROOT / "library/preview.jpg"
    with preview.open("rb") as stream:
        if stream.read(3) != b"\xff\xd8\xff":
            raise ValueError("The accepted game preview must be JPEG")
    receipt_file = ROOT / "tools/overlay-receipt.json"
    receipt_hash = file_info(receipt_file)
    verify_file(candidate / "overlay-receipt.json", receipt_hash)
    verify_overlay(
        upstream, read_json(candidate / "overlay-receipt.json"),
        candidate / "native/horizon-client.exe", candidate / "native/steam_api64.dll",
    )
    for name in LIBRARY_SOURCES:
        verify_file(candidate / "library" / name, file_info(ROOT / "library" / name))
    for name in CONTROL_FILES:
        verify_file(candidate / name, file_info(ROOT / name))
    distribution = {
        "schemaVersion": 1,
        "upstreamCommit": upstream["commit"],
        "preview": file_info(preview),
        "library/loaded-mods.json": file_info(ROOT / "library/loaded-mods.json"),
        "tools/overlay-receipt.json": receipt_hash,
        "files": files,
    }
    return runtime, content, distribution


def observed_inventory(candidate, report_path):
    report = read_json(report_path)
    files = inventory(candidate)
    descriptor = read_json(candidate / "portable-pack.json")
    presentation = read_json(candidate / "native/presentation.json")
    if (
        report["schemaVersion"] != 1 or
        report["client"] != files["native/horizon-client.exe"] or
        report["bootstrap"] != files[descriptor["nativeRuntime"]["executable"]] or
        report["windowTitle"] != presentation["name"] or
        {item["id"] for item in report["modsPage"]["widgets"]} != {"browse-mods", "open-mod-folder", "done"}
    ):
        raise ValueError("Smoke report does not prove the packaged native client and empty Mods page")
    return {
        "schemaVersion": 1,
        "minecraftVersion": descriptor["minecraftVersion"],
        "nativeRuntime": descriptor["nativeRuntime"],
        "nativeFiles": {name: details["sha256"] for name, details in files.items() if name.startswith("native/")},
        "mods": [],
    }


def zip_payload(out, candidate, covers, prefix):
    temporary = out / "runtime.tmp"
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in covers:
            entry = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (candidate / relative).read_bytes(), compresslevel=9)
    details = file_info(temporary)
    name = f"{prefix}-{details['sha256']}.zip"
    temporary.rename(out / name)
    return {"name": name, "kind": "zipRoot", **details, "covers": covers}


def load_library_tool(path):
    spec = importlib.util.spec_from_file_location("lanminecraft_pack_library", path)
    if spec is None or spec.loader is None:
        raise ValueError("Pass the current LANMinecraft tools/pack_library.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.resolve().parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def stage_release(candidate, out, revision, library_tool):
    runtime, content, distribution = describe_candidate(candidate)
    if distribution != read_json(RELEASE_LOCK):
        raise ValueError("Candidate differs from the explicitly approved distribution lock")
    files = distribution["files"]
    assets_dir = out / "assets"
    assets_dir.mkdir(parents=True)
    preset = CONTROL_FILES[0]
    assets = [
        zip_payload(assets_dir, candidate, [name for name in files if name != preset],
                    "minecraft-rust-edition-windows-x64"),
        zip_payload(assets_dir, candidate, [preset], "launcher"),
    ]
    library = library_tool.stage_library(candidate, out, revision)
    mod_count = len(read_json(candidate / "library/loaded-mods.json")["mods"])
    library_tool.verify_library(out, library)
    manifest = {
        "schemaVersion": 2,
        "revision": revision,
        "packId": content["id"],
        "name": content["name"],
        "relativePath": content["relativePath"],
        "minecraftVersion": runtime["minecraftVersion"],
        "nativeRuntime": runtime["nativeRuntime"],
        "requiredLauncherFeatures": FEATURES,
        "modCount": mod_count,
        "files": [{"path": name, **details} for name, details in files.items()],
        "assets": assets,
        "library": library,
    }
    (out / "pack-manifest.json").write_bytes(json_bytes(manifest))


def output_files(root):
    return {
        path.relative_to(root).as_posix(): file_info(path)
        for path in root.rglob("*") if path.is_file()
    }


def verify_revision(revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected an exact pack source commit")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True)
    if revision != head or status:
        raise ValueError("The release requires a clean checkout at the specified pack commit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--revision")
    parser.add_argument("--library-tool", type=pathlib.Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify-only", action="store_true")
    action.add_argument("--propose-lock", action="store_true")
    action.add_argument("--propose-inventory", type=pathlib.Path, metavar="SMOKE_JSON")
    args = parser.parse_args()
    if args.propose_inventory is None and args.library_tool is None:
        parser.error("--library-tool is required for packaging")
    if not args.propose_lock and args.propose_inventory is None and (args.out is None or args.revision is None):
        parser.error("--out and --revision are required for a release")
    try:
        if args.propose_inventory:
            observation = observed_inventory(args.candidate, args.propose_inventory)
            print(json_bytes(observation).decode("utf-8"), end="")
            return
        library_tool = load_library_tool(args.library_tool)
        if args.propose_lock:
            _, _, distribution = describe_candidate(args.candidate)
            library_tool.build_library(args.candidate, distribution["upstreamCommit"])
            print(json_bytes(distribution).decode("utf-8"), end="")
            return
        verify_revision(args.revision)
        if not args.verify_only and args.out.exists():
            raise ValueError("Use a fresh distribution path")
        with tempfile.TemporaryDirectory(dir=args.out.parent) as temporary:
            regenerated = pathlib.Path(temporary) / "dist"
            stage_release(args.candidate, regenerated, args.revision, library_tool)
            if args.verify_only:
                if output_files(regenerated) != output_files(args.out):
                    raise ValueError("Published candidate does not match regenerated distribution")
            else:
                regenerated.rename(args.out)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
