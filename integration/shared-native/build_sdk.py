"""Build one Windows GNU SDK runtime from the signed Horizon source and Edition layers."""

import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
import tarfile
import tempfile
import types

from apply import RECIPE, REVISION, SOURCE_SHA256, changes, sha
import lower_slots


UPSTREAM_BUILDER = "tools/prebuilt_game.py"
UPSTREAM_BUILDER_SHA256 = "ae751843ce9cc3eddcab0b2330898e0b6a8e4556de20a4e7dc7e7fed8406245a"
TOOLCHAIN = "1.99.1-x86_64-pc-windows-gnu"
HOST = "x86_64-pc-windows-gnu"
NATIVE_TEMPLATE = "crates/horizon-client/src/native_mod_entry.rs.template"
NATIVE_TEMPLATE_SHA256 = "1c77f20468fc65accc7d4334b36ff6ba71c964c8cbcc044636818240821d8349"


def import_overlay() -> types.ModuleType:
    path = RECIPE.parent / "steam/apply_horizon_overlay.py"
    spec = importlib.util.spec_from_file_location("edition_horizon_overlay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive_blobs(archive: Path, prefix: str, overrides: dict[str, bytes], paths=()):
    """Yield the signed regular author files plus the verified overlay bytes."""
    requested = set(paths)

    def wanted(name):
        if not requested:
            return True
        while name:
            if name in requested:
                return True
            name = name.rpartition("/")[0]
        return False

    if paths and all(path in overrides for path in paths):
        yield from ((path, overrides[path]) for path in paths)
        return
    seen = set()
    with tarfile.open(archive, "r|gz") as source:
        for member in source:
            if member.isdir():
                continue
            name = member.name.removeprefix(prefix)
            if name == "CLAUDE.md" and member.issym() and member.linkname == "AGENTS.md":
                continue  # Match upstream git archive's regular-file policy.
            relative = PurePosixPath(name)
            if (not member.isfile() or not member.name.startswith(prefix) or
                    relative.is_absolute() or ".." in relative.parts or name in seen):
                raise ValueError(f"Unexpected signed Horizon entry: {member.name}")
            seen.add(name)
            if wanted(name):
                data = overrides[name] if name in overrides else source.extractfile(member).read()
                yield name, data
    for name in sorted(overrides.keys() - seen):
        if wanted(name):
            yield name, overrides[name]


def seam_overrides(source: Path, base: dict[str, bytes], descriptor: Path) -> dict[str, bytes]:
    spec = json.loads(descriptor.read_bytes())
    if spec["schemaVersion"] != 1 or spec["horizon"] != REVISION:
        raise ValueError("Neutral native seams target another Horizon revision")
    root = descriptor.parent / "files"
    if root.resolve() != root or not root.is_dir():
        raise ValueError("Use the ordinary files/ directory next to the seam receipt")
    result = {}
    for name, entry in spec["files"].items():
        relative = PurePosixPath(name)
        if (not name or relative.as_posix() != name or relative.is_absolute() or
                not relative.parts or any(PureWindowsPath(part).is_reserved() or
                    part in (".", "..") or "\\" in part or ":" in part
                    or part.endswith((" ", ".")) for part in relative.parts)):
            raise ValueError(f"Invalid neutral seam path: {name!r}")
        before = base.get(name)
        if before is None and (source / name).is_file():
            before = (source / name).read_bytes()
        if (sha(before) if before is not None else None) != entry["beforeSha256"]:
            raise ValueError(f"Neutral seam input changed: {name}")
        path = root / name
        if path.resolve(strict=True) != path or not path.is_file() or path.is_symlink() or path.is_junction():
            raise ValueError(f"Neutral seam file is not ordinary: {name}")
        data = path.read_bytes()
        if sha(data) != entry["sha256"]:
            raise ValueError(f"Neutral seam output changed: {name}")
        result[name] = data
    return result


def configure(source: Path, archive: Path, transport: Path, rustc: str,
              *, seams: Path | None = None) -> tuple[types.ModuleType, dict]:
    overlay = import_overlay()
    if overlay.COMMIT != REVISION:
        raise ValueError("The Edition layer and the shared host recipe target different revisions")
    originals = overlay.locked_inputs(archive)
    steam = overlay.changes(originals, transport)
    overlay.apply_or_validate(source, originals, steam, False)
    overlay.verify_build_tree(source, archive, steam)
    path = source / UPSTREAM_BUILDER
    raw = path.read_bytes()
    if sha(raw) != UPSTREAM_BUILDER_SHA256:
        raise ValueError("Upstream SDK builder changed")
    shared = changes({name: (source / name).read_bytes() for name in SOURCE_SHA256})
    overrides = {**steam, **shared}
    if seams is not None:
        overrides.update(seam_overrides(source, overrides, seams))
        overrides.update(lower_slots.changes(source, overrides))
    policy = {
        "horizon": REVISION,
        "sourceArchiveSha256": overlay.SOURCE_SHA,
        "steamRecipeSha256": overlay.recipe_sha(),
        "sdkBuilderSha256": UPSTREAM_BUILDER_SHA256,
        "rustc": rustc,
        "host": HOST,
        "overlays": {name: sha(data) for name, data in sorted(overrides.items())},
    }
    # The SDK format expects 40 hex digits. This is a local build identity
    # derived from verified inputs, not a Git revision of the author's engine.
    build_id = sha(json.dumps(policy, sort_keys=True).encode("ascii"))[:40]
    sdk = types.ModuleType("horizon_shared_prebuilt")
    sdk.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), sdk.__dict__)
    sdk.head = lambda: build_id

    def tracked_blobs(root, identity, paths=()):
        if Path(root) != source or identity != build_id:
            raise ValueError("SDK requested source outside the verified snapshot")
        return archive_blobs(archive, overlay.PREFIX, overrides, paths)

    sdk.tracked_blobs = tracked_blobs
    if seams is not None:
        stage = sdk.stage

        def checked_stage(*args, **kwargs):
            staged = stage(*args, **kwargs)
            lower_slots.require_exports(staged / "functions.json", build_id, overrides)
            return staged

        sdk.stage = checked_stage
    return sdk, {"schemaVersion": 1, "abiBuildId": build_id, **policy}


def export_patch_source(source: Path, sdk: types.ModuleType, build_id: str, catalog: Path,
                        destination: Path) -> int:
    """Project the module bytes the exact SDK fingerprints before source instrumentation."""
    index = json.loads(catalog.read_bytes())
    if index["build"]["commit"] != build_id:
        raise ValueError("Patch source catalog belongs to another shared SDK")
    expected = {}
    for crate in index["crates"].values():
        for module in crate["modules"].values():
            for file in module:
                name = file["path"]
                relative = PurePosixPath(name)
                if (not relative.parts or relative.is_absolute() or relative.as_posix() != name or
                        any(part in (".", "..") or PureWindowsPath(part).is_reserved() or
                            part.endswith((".", " ")) or "\\" in part or ":" in part
                            for part in relative.parts)):
                    raise ValueError(f"Unexpected SDK patch source path: {name!r}")
                if expected.setdefault(name, file["sha256"]) != file["sha256"]:
                    raise ValueError(f"Conflicting SDK patch source hashes: {name}")
    if not expected:
        raise ValueError("No SDK patch source modules")
    expected[NATIVE_TEMPLATE] = NATIVE_TEMPLATE_SHA256
    written = set()
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix="patch-source-") as temporary:
        stage = Path(temporary) / "source"
        for name, data in sdk.tracked_blobs(source, build_id, tuple(expected)):
            if name not in expected or sha(data) != expected[name] or name in written:
                raise ValueError(f"SDK patch source bytes differ: {name}")
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            written.add(name)
        if missing := expected.keys() - written:
            raise ValueError(f"Missing SDK patch source: {sorted(missing)}")
        stage.rename(destination)
    return len(written)


def verify_target(target, attempt, proof, previous_abi):
    if (target.resolve(strict=True) != target or not target.is_dir() or target.is_junction()
            or not attempt.is_file() or attempt.is_symlink() or attempt.is_junction()):
        raise ValueError("The unfinished Cargo target is redirected or lacks its receipt")
    previous = json.loads(attempt.read_bytes())
    if previous_abi is None:
        if previous != proof:
            raise ValueError("The unfinished Cargo target belongs to another SDK build")
    else:
        unchanged = set(proof) - {"abiBuildId", "overlays"}
        if (not re.fullmatch(r"[0-9a-f]{40}", previous_abi)
                or previous_abi == proof["abiBuildId"] or previous.get("abiBuildId") != previous_abi
                or previous.keys() != proof.keys() or any(previous[key] != proof[key] for key in unchanged)):
            raise ValueError("Refreshing the Cargo target requires its exact old ABI and the same base inputs")

    prebuilt = target / "prebuilt"
    if prebuilt.resolve() != prebuilt:
        raise ValueError("The old staging parent is redirected")
    for staged in (prebuilt / "stage.next", prebuilt / "stage"):
        if staged.is_symlink() or staged.is_junction() or (
                staged.exists() and (staged.resolve(strict=True) != staged or not staged.is_dir())):
            raise ValueError(f"The old staging directory is redirected: {staged}")
        for directory, subdirectories, filenames in os.walk("\\\\?\\" + str(staged), followlinks=False):
            for name in subdirectories + filenames:
                path = Path(directory) / name
                if name.casefold() == ".git" or path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(f"The previous staging contains linked or source data: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=lambda value: Path(value).resolve(strict=True))
    parser.add_argument("--source-archive", required=True, type=lambda value: Path(value).resolve(strict=True))
    parser.add_argument("--steam-transport", required=True, type=lambda value: Path(value).resolve(strict=True))
    parser.add_argument("--run", required=True, type=lambda value: Path(value).resolve(strict=True))
    parser.add_argument("--seams", type=lambda value: Path(value).resolve(strict=True))
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--export-patch-source", action="store_true")
    cache = parser.add_mutually_exclusive_group()
    cache.add_argument("--reuse-target", action="store_true", help="reuse the matching SDK build's Cargo cache")
    cache.add_argument("--refresh-previous-abi", help="reuse a reviewed previous ABI's Cargo cache for changed overlays")
    args = parser.parse_args()
    if args.plan_only and args.export_patch_source:
        parser.error("--export-patch-source requires a staged SDK")
    if args.run.parent.name != "agent-runs" or args.run.parent.parent.name != "artifacts":
        raise ValueError("Use a registered LANMinecraft agent run")
    registration = json.loads((args.run / "RUN.json").read_bytes())
    if registration.get("status") != "active" or not registration.get("owner"):
        raise ValueError("Use an active registered agent run")
    out = args.run / "shared-sdk"
    target = args.run / "shared-sdk-cargo"
    receipt = args.run / "shared-sdk-build.json"
    attempt = args.run / "shared-sdk-attempt.json"
    patch_source = args.run / "shared-sdk-patch-source"
    outputs = [out, receipt]
    use_target = args.reuse_target or args.refresh_previous_abi is not None
    if not use_target:
        outputs.extend((target, attempt))
    if args.export_patch_source:
        outputs.append(patch_source)
    if not args.plan_only and any(
            path.exists() or path.is_symlink() or path.is_junction() for path in outputs):
        raise ValueError("Shared SDK output already exists in this run")
    rustc = subprocess.check_output(["rustup", "run", TOOLCHAIN, "rustc", "-vV"],
                                    encoding="utf-8").strip()
    if not re.search(r"^release: 1\.99\.1$", rustc, re.M) or f"host: {HOST}" not in rustc.splitlines():
        raise ValueError("The Windows GNU SDK requires the exact Rust 1.99.1 host")
    sdk, proof = configure(args.source, args.source_archive, args.steam_transport, rustc, seams=args.seams)
    if args.plan_only:
        print(json.dumps(proof, indent=2))
        return
    if use_target:
        verify_target(target, attempt, proof, args.refresh_previous_abi)
    if args.refresh_previous_abi is not None:
        with tempfile.NamedTemporaryFile(dir=attempt.parent, delete=False, mode="w", encoding="utf-8",
                                         newline="\n") as output:
            next_attempt = Path(output.name)
            output.write(json.dumps(proof, indent=2) + "\n")
        try:
            next_attempt.replace(attempt)
        finally:
            next_attempt.unlink(missing_ok=True)
    elif not use_target:
        with attempt.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(proof, indent=2) + "\n")
    os.environ.update(RUSTUP_TOOLCHAIN=TOOLCHAIN, CARGO_TARGET_DIR=str(target))
    sdk.build(out, False)
    identity = json.loads((out / "horizon-prebuilt.json").read_bytes())
    if identity["commit"] != proof["abiBuildId"] or identity["target"] != HOST:
        raise ValueError("Built SDK identity differs from its verified recipe")
    proof["sdkIdentitySha256"] = sha((out / "horizon-prebuilt.json").read_bytes())
    proof["clientSha256"] = identity["files"]["bin/horizon-client.exe"]
    if args.export_patch_source:
        catalog = out / "functions.json"
        proof["patchSourceFiles"] = export_patch_source(args.source, sdk, proof["abiBuildId"],
                                                       catalog, patch_source)
        proof["patchSourceCatalogSha256"] = sha(catalog.read_bytes())
    receipt.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(receipt)


if __name__ == "__main__":
    main()
