"""Prepare an SDK-bound native-mods.json and verified pack-local DLLs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tomllib

from apply import REVISION, sha
from build_sdk import HOST


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def ordinary(path: Path, *, directory: bool = False) -> None:
    matches = path.is_dir() if directory else path.is_file()
    if not matches or path.is_symlink() or path.is_junction() or path.resolve() != Path(os.path.abspath(path)):
        raise ValueError(f"Expected an ordinary {'directory' if directory else 'file'}: {path}")


def verify_sdk(sdk: Path, permit: dict) -> tuple[dict, str]:
    path = sdk / "horizon-prebuilt.json"
    ordinary(path)
    raw = path.read_bytes()
    identity_hash = sha(raw)
    build = json.loads(raw)
    if (permit["horizon"] != REVISION or identity_hash != permit["sdkIdentitySha256"] or
            build["commit"] != permit["abiBuildId"] or build["rustc"] != permit["rustc"] or
            build["target"] != HOST or build["side"] != "client"):
        raise ValueError("The supplied SDK does not match its shared build receipt")
    executable = sdk / "bin/horizon-client.exe"
    ordinary(executable)
    if digest(executable) != permit["clientSha256"] or build["files"]["bin/horizon-client.exe"] != permit["clientSha256"]:
        raise ValueError("The shared client does not match its receipt")
    return build, identity_hash


def make_record(sdk: Path, permit: dict, native: Path, libraries: list[Path]) -> tuple[dict, list]:
    build, identity_hash = verify_sdk(sdk, permit)
    ordinary(native, directory=True)
    abi = build["commit"]
    version = re.search(r"^release: (\S+)$", build["rustc"], re.M).group(1)
    reserved = {Path(name).name.casefold() for name in build["slices"]["runtime"]}
    names = set()
    mods = set()
    rows = []
    for library in libraries:
        ordinary(library)
        name = library.name
        key = name.casefold()
        if library.suffix.lower() != ".dll" or key in reserved or key in names:
            raise ValueError(f"Unexpected or repeated pack DLL: {name}")
        stamp_path = library.with_suffix(".identity.json")
        ordinary(stamp_path)
        stamp = json.loads(stamp_path.read_bytes())
        mod = stamp["mod"]
        parts = stamp["identity"].split()
        if (stamp["file"] != name or len(parts) != 4 or parts[:2] != [abi, mod] or
                parts[-1] != version or not re.fullmatch(r"[a-z][a-z0-9_-]*", mod) or
                stamp["kind"] not in {"mod", "patch"} or mod in mods):
            raise ValueError(f"{name}: library identity is not from the shared ABI build")
        if stamp["kind"] == "patch":
            if mod != "horizon_patch":
                raise ValueError(f"{name}: the patch library has the wrong identity")
        else:
            manifest = native / "horizon-native-mods" / mod / "horizon-mod.toml"
            ordinary(manifest)
            package = tomllib.loads(manifest.read_text(encoding="utf-8"))
            if package["id"] != mod or package["version"] != parts[2] or package["native"]["horizon"] != abi:
                raise ValueError(f"{manifest}: the package is not the one compiled for this shared ABI")
        names.add(key)
        mods.add(mod)
        rows.append((library, {"file": name, "sha256": digest(library), "kind": stamp["kind"],
                               "mod": mod, "identity": stamp["identity"]}))
    rows.sort(key=lambda item: item[1]["kind"] == "patch")
    record = {"abiBuildId": abi, "sdkIdentitySha256": identity_hash,
              "clientSha256": permit["clientSha256"], "libraries": [row for _, row in rows]}
    return record, rows


def publish(native: Path, record: dict, rows: list) -> None:
    marker = native / "native-mods.json"
    if marker.exists() or marker.is_symlink():
        raise ValueError(f"Pack native-mods.json already exists: {marker}")
    for library, row in rows:
        output = native / row["file"]
        if output.exists() or output.is_symlink():
            ordinary(output)
            if digest(output) != row["sha256"]:
                raise ValueError(f"Pack DLL changed: {output}")
        else:
            with library.open("rb") as source, output.open("xb") as target:
                shutil.copyfileobj(source, target)
            if digest(output) != row["sha256"]:
                raise ValueError(f"Pack DLL changed during staging: {output}")
    with marker.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(record, output, indent=2)
        output.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--sdk-receipt", required=True, type=Path)
    parser.add_argument("--pack-root", required=True, type=Path)
    parser.add_argument("--library", action="append", type=Path, default=[])
    args = parser.parse_args()
    native = args.pack_root / "native"
    record, rows = make_record(args.sdk, json.loads(args.sdk_receipt.read_bytes()), native, args.library)
    publish(native, record, rows)
    print(native / "native-mods.json")


if __name__ == "__main__":
    main()
