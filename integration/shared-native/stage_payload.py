"""Complete a shared game candidate from the exact Windows SDK runtime slice."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from build_sdk import TOOLCHAIN
from profile import digest, ordinary, verify_sdk


ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path("native")
CLIENT = NATIVE / "horizon-client.exe"
BOOTSTRAP = NATIVE / "launch-rust-edition.exe"
FAMILY = NATIVE / "runtime-family.json"
MINGW = ("libgcc_s_seh-1.dll", "libwinpthread-1.dll")
MINGW_ARCHIVE_SHA256 = "62af32ea58acdea1318ea5dd8eb7017eaee694dd40b269d52a59c769dcfefb82"


def load(path):
    ordinary(path)
    return json.loads(path.read_bytes())


def identity(path):
    ordinary(path)
    return {"sizeBytes": path.stat().st_size, "sha256": digest(path)}


def runtime_binaries(run, sdk, build, receipt):
    paths = {CLIENT: sdk / "bin/horizon-client.exe"}
    for name in build["slices"]["runtime"]:
        path = Path(name)
        if path.parts[0] != "deps" or path.suffix.lower() != ".dll":
            continue
        source = sdk / path
        ordinary(source)
        if len(path.parts) != 2 or digest(source) != build["files"][name]:
            raise ValueError(f"The SDK runtime library differs from its approved bytes: {name}")
        paths[NATIVE / path.name] = source

    rust = ["rustup", "run", TOOLCHAIN, "rustc"]
    if subprocess.check_output([*rust, "-vV"], text=True).strip() != receipt["rustc"]:
        raise ValueError("Use the Rust toolchain that built the shared SDK")
    libdir = Path(subprocess.check_output([*rust, "--print", "target-libdir"], text=True).strip())
    std = list(libdir.glob("std-*.dll"))
    if len(std) != 1:
        raise ValueError("Expected the one shared Windows Rust standard library")
    paths[NATIVE / std[0].name] = std[0]
    proof = load(run / "mingw-toolchain-proof.json")
    if proof["archiveSha256"] != MINGW_ARCHIVE_SHA256 or proof["target"] != "x86_64-w64-mingw32":
        raise ValueError("Use the MinGW distribution pinned by the reviewed Horizon source")
    for name in MINGW:
        source = run / "toolchain/mingw64/bin" / name
        if identity(source)["sha256"] != proof["files"][name]:
            raise ValueError(f"The MinGW runtime differs from its approved bytes: {name}")
        paths[NATIVE / name] = source
    return paths


def stage(args):
    run = args.run
    registration = load(run / "RUN.json")
    if (run.parent.name != "agent-runs" or run.parent.parent.name != "artifacts" or
            registration.get("status") != "active" or not registration.get("owner")):
        raise ValueError("Use an active LANMinecraft agent run")
    candidate = run / "common-candidate"
    ordinary(candidate / NATIVE, directory=True)
    spec_path = run / "shared-runtime-spec.json"
    if spec_path.exists():
        raise ValueError("The shared runtime specification already exists")
    base = {Path(name): entry for name, entry in load(args.base_lock)["files"].items()
            if name.startswith("native/") and name != "native/presentation.json" and Path(name) != CLIENT}
    bootstrap = load(ROOT / "tools/bootstrap-lock.json")["executable"]
    base[BOOTSTRAP] = {key: bootstrap[key] for key in ("sizeBytes", "sha256")}
    common = {path.relative_to(candidate): identity(path) for path in (candidate / NATIVE).rglob("*")
              if not path.is_dir() or path.is_junction() or path.is_symlink()}
    if common != base:
        raise ValueError("The shared candidate must start with the exact accepted common inputs")

    sdk = run / "shared-sdk"
    receipt = load(run / "shared-sdk-build.json")
    build, _ = verify_sdk(sdk, receipt)
    binaries = runtime_binaries(run, sdk, build, receipt)
    expected = {name: identity(path) for name, path in binaries.items()}
    paths = [*common, *expected, FAMILY]
    if len(paths) != len({path.as_posix().casefold() for path in paths}):
        raise ValueError("The shared executable or library overlaps an installed game file")
    family = {"schemaVersion": 1, "id": args.id, "version": args.version, "platform": "windows-x64",
              "minecraftVersion": args.minecraft_version}
    owner, repo, tag = args.source
    spec = {key: family[key] for key in ("schemaVersion", "id", "version", "platform")}
    spec["source"] = {"owner": owner, "repo": repo, "tag": tag}
    created = []
    try:
        for name, source in binaries.items():
            target = candidate / name
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                created.append(target)
                shutil.copyfileobj(incoming, outgoing)
            if identity(target) != expected[name]:
                raise ValueError(f"Error copying the verified runtime file: {name}")
        with (candidate / FAMILY).open("x", encoding="utf-8", newline="\n") as output:
            created.append(candidate / FAMILY)
            output.write(json.dumps(family, indent=2) + "\n")
        common.update(expected)
        common[FAMILY] = identity(candidate / FAMILY)
        spec["files"] = [{"path": path.as_posix(), **entry} for path, entry in sorted(common.items())]
        with spec_path.open("x", encoding="utf-8", newline="\n") as output:
            created.append(spec_path)
            output.write(json.dumps(spec, indent=2) + "\n")
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise
    return spec_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "base-lock"):
        parser.add_argument(f"--{name}", required=True, type=lambda value: Path(value).resolve(strict=True))
    for name in ("id", "version", "minecraft-version"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--source", required=True, nargs=3, metavar=("OWNER", "REPO", "TAG"))
    print(stage(parser.parse_args()))


if __name__ == "__main__":
    main()
