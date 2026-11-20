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
NOTICE_SOURCES = Path(__file__).resolve().parent / "runtime-notices"
RUST_COPYRIGHT_SHA256 = "5647be074c8edf7339fd863055923a8fc80bc5610a8d4661ec3b767b9d392c27"
LICENSE_SHA256 = {
    "Rust-APACHE.txt": "62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a",
    "Rust-MIT.txt": "b71bd43a069ca0641a9ecfe585ca7b3c53b5cc1608f8b68321168698e28b5ea1",
    "GCC-COPYING.RUNTIME": "9d6b43ce4d8de0c878bf16b54d8e7a10d9bd42b75178153e3af6a815bdc90f74",
    "Winpthreads-COPYING.txt": "63263614cdd29f2f93cba85e992f041b31f9fc7b4033692f31269489a8a1b177",
}


def load(path):
    ordinary(path)
    return json.loads(path.read_bytes())


def identity(path):
    ordinary(path)
    return {"sizeBytes": path.stat().st_size, "sha256": digest(path)}


def runtime_notices(sysroot):
    rust_copyright = sysroot / "share/doc/rust/COPYRIGHT-library.html"
    sources = {NOTICE_SOURCES / name: expected for name, expected in LICENSE_SHA256.items()}
    sources[rust_copyright] = RUST_COPYRIGHT_SHA256
    for source, expected in sources.items():
        if identity(source)["sha256"] != expected:
            raise ValueError(f"The runtime notice differs from its pinned bytes: {source.name}")
    winlibs_notice = NOTICE_SOURCES / "WinLibs-runtime-NOTICE.md"
    return {NATIVE / "LICENSES" / source.name: source for source in (*sources, winlibs_notice)}


def runtime_files(run, sdk, build, receipt):
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
    sysroot = Path(subprocess.check_output([*rust, "--print", "sysroot"], text=True).strip())
    paths.update(runtime_notices(sysroot))
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
    runtime = runtime_files(run, sdk, build, receipt)
    expected = {name: identity(path) for name, path in runtime.items()}
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
        for name, source in runtime.items():
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
