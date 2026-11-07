"""Stage the verified Windows x64 Horizon runtime in a fresh candidate directory."""

import argparse
import base64
import hashlib
import json
import pathlib
import re
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCK = ROOT / "tools/upstream-lock.json"
STEAM_LOCK = ROOT / "tools/steam-lock.json"
SIGNATURES = ROOT / "tools/upstream"
RUNTIME_ARCHIVE = "horizon-windows-x64.zip"
SOURCE_ARCHIVE = "horizon-source.tar.gz"
ONBOARDING_SOURCE_SHA256 = "f395c83abe16e2c976630430c1935826d1e07b5edc3450eccb021e2b468bdc4e"
RUNTIME_PREFIX = "horizon-windows-x64/"
SUPPORT_PATHS = (
    "crates/horizon-prepare/",
    "exporters/",
    "mods/horizon/src/test/java/",
)
RUNTIME_FILES = {
    "horizon-client.exe", "horizon-prepare.exe", "LICENSE", "research-lock.json",
}
SOURCE_NOTICE_PATHS = (
    "THIRD_PARTY_NOTICES.md",
    "third_party/naga/LICENSE.APACHE",
    "third_party/naga/LICENSE.MIT",
    "third_party/wgpu-hal/LICENSE.APACHE",
    "third_party/wgpu-hal/LICENSE.MIT",
    "third_party/libmimalloc-sys/LICENSE.txt",
    "third_party/libmimalloc-sys/c_src/mimalloc/v3/LICENSE",
)
RECIPE_FILES = (
    "apply_horizon_overlay.py",
    "horizon-cargo.lock",
    "lanmc_presentation.rs",
    "lanmc_skins.rs",
    "lanmc_steam.rs",
    "rust-logo/NOTICE.txt",
    "rust-logo/artwork-lock.json",
    "rust-logo/render.py",
    "rust-logo/rust-edition.ico",
    "rust-logo/rust-logo.png",
    "rust-logo/rust-logo.svg",
)
LIBRARY_SOURCES = ("content.json", "mod-pages.json", "loaded-mods.json", "preview.jpg")
CONTROL_FILES = ("launcher/controls-preset.txt", "configureddefaults/options.txt")
STEAM_NOTICES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")
RUST_ARTWORK = ROOT / "integration/steam/rust-logo"
RUST_NOTICES = ("NOTICE.txt", "artwork-lock.json", "rust-logo.svg")
HEX256 = re.compile(r"[0-9a-f]{64}")
SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_.$-]+")
DEVICE_NAME = re.compile(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_info(path):
    with path.open("rb") as stream:
        return {"sizeBytes": path.stat().st_size, "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}


def verify_file(path, expected):
    info = file_info(path)
    if any(info[key] != expected[key] for key in info):
        raise ValueError(f"Size or SHA-256 mismatch for {path.name}")


def verify_pe(path, dll=False):
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"Linked native image: {path.name}")
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError(f"Not a PE image: {path.name}")
        stream.seek(struct.unpack_from("<I", header, 60)[0])
        pe = stream.read(26)
    if len(pe) != 26 or pe[:4] != b"PE\x00\x00":
        raise ValueError(f"Invalid PE header: {path.name}")
    architecture, = struct.unpack_from("<H", pe, 4)
    characteristics, magic = struct.unpack_from("<HH", pe, 22)
    if architecture != 0x8664 or magic != 0x20B or bool(characteristics & 0x2000) != dll:
        raise ValueError(f"Invalid Windows x64 image type: {path.name}")


def verify_onboarding_narrator(source, commit):
    path = f"horizon-{commit[:7]}/crates/horizon-client/src/title/onboarding.rs"
    entries = [entry for entry in source if entry.name == path]
    if len(entries) == 1 and entries[0].isfile() and entries[0].size <= 512 * 1024:
        # The reviewed file presents Narrator as Not Available and creates its button disabled.
        with source.extractfile(entries[0]) as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == ONBOARDING_SOURCE_SHA256:
                return
    raise ValueError("Horizon onboarding source changed. Review the inactive Narrator before staging.")


def verify_upstream(archives, openssl, scratch):
    lock = read_json(LOCK)
    commit = lock["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected an exact upstream commit")

    sums = (SIGNATURES / "SHA256SUMS").read_bytes()
    signed = f"horizon release {lock['signedTag']}\n".encode("ascii") + sums
    signature = base64.b64decode((SIGNATURES / "SHA256SUMS.sig").read_bytes().strip(), validate=True)
    key = base64.b64decode(lock["ed25519Key"], validate=True)
    if len(signature) != 64 or len(key) != 32:
        raise ValueError("Malformed Ed25519 key or signature")
    with tempfile.TemporaryDirectory(dir=scratch) as temporary:
        paths = [pathlib.Path(temporary) / name for name in ("message", "key.der", "signature")]
        for path, contents in zip(paths, (signed, bytes.fromhex("302a300506032b6570032100") + key, signature)):
            path.write_bytes(contents)
        subprocess.run(
            [openssl, "pkeyutl", "-verify", "-rawin", "-pubin", "-keyform", "DER",
             "-in", str(paths[0]), "-inkey", str(paths[1]), "-sigfile", str(paths[2])],
            check=True,
        )

    signed_hashes = {}
    for line in sums.decode("ascii").splitlines():
        digest, name = line.split("  ", 1)
        if not HEX256.fullmatch(digest) or name in signed_hashes:
            raise ValueError("Malformed signed release checksums")
        signed_hashes[name] = digest
    for asset in (RUNTIME_ARCHIVE, SOURCE_ARCHIVE):
        expected = lock["assets"][asset]
        if expected["sha256"] != signed_hashes.get(asset):
            raise ValueError(f"Pinned SHA-256 differs from the signed SHA-256 for {asset}")
        verify_file(archives / asset, expected)
    with tarfile.open(archives / SOURCE_ARCHIVE, "r:gz") as source:
        verify_onboarding_narrator(source, commit)
    return lock


def safe_path(path):
    parts = path.split("/")
    if len(path) > 512 or not all(
        len(part) <= 255 and SAFE_SEGMENT.fullmatch(part) and not part.endswith(".")
        and not DEVICE_NAME.fullmatch(part) for part in parts
    ):
        raise ValueError(f"Unsafe distribution path: {path}")
    return parts


def unpack_runtime(archive, source, destination, lock, include_controller):
    """Use an allowlist and copy regular entries rather than extracting the archives."""
    required = RUNTIME_FILES | {"horizon-ctl.exe"} if include_controller else RUNTIME_FILES
    copied = set()
    with zipfile.ZipFile(archive) as files:
        for entry in files.infolist():
            if entry.is_dir() or not entry.filename.startswith(RUNTIME_PREFIX):
                continue
            relative = entry.filename.removeprefix(RUNTIME_PREFIX)
            if relative not in required and not relative.startswith(SUPPORT_PATHS):
                continue
            parts = safe_path(relative)
            if relative.casefold() in copied or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError(f"Duplicate or linked runtime file: {relative}")
            copied.add(relative.casefold())
            path = destination.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with files.open(entry) as stream, path.open("xb") as output:
                shutil.copyfileobj(stream, output)
    if not all((destination / name).is_file() for name in required) or not all(
        any(name.startswith(prefix) for name in copied) for prefix in SUPPORT_PATHS
    ):
        raise ValueError("Upstream runtime archive is missing required preparation files")
    pins = read_json(destination / "research-lock.json")["minecraft"]
    if pins["version"] != lock["minecraftVersion"] or pins["java_major"] != lock["javaMajorVersion"]:
        raise ValueError("Upstream preparation pins differ from the pack lock")

    with tarfile.open(source, "r:gz") as files:
        for relative in SOURCE_NOTICE_PATHS:
            entry = files.getmember(f"horizon-{lock['commit'][:7]}/{relative}")
            if not entry.isfile() or entry.size > 512 * 1024:
                raise ValueError(f"Invalid source notice: {relative}")
            path = destination.joinpath(*safe_path(relative))
            path.parent.mkdir(parents=True, exist_ok=True)
            with files.extractfile(entry) as stream, path.open("xb") as output:
                shutil.copyfileobj(stream, output)


def verify_overlay(lock, receipt, client, steam_dll):
    from check_required_layers import verify_proof
    verify_proof(receipt)
    steam = read_json(STEAM_LOCK)
    recipe = "".join(
        f"{file_info(ROOT / 'integration/steam' / name)['sha256']}  {name}\n"
        for name in RECIPE_FILES
    )
    if (receipt["schemaVersion"] != 1 or receipt["upstreamCommit"] != lock["commit"] or
            receipt["upstreamSourceSha256"] != lock["assets"][SOURCE_ARCHIVE]["sha256"] or
            receipt["recipeSha256"] != hashlib.sha256(recipe.encode("ascii")).hexdigest() or
            receipt["steamTransportCommit"] != steam["source"]["commit"] or
            receipt["steamTransportSourceSha256"] != steam["source"]["overlaySourceSha256"]):
        raise ValueError("Overlay receipt does not bind the current source and recipe")
    verify_file(client, {"sizeBytes": receipt["clientSizeBytes"], "sha256": receipt["clientSha256"]})
    verify_file(steam_dll, {"sizeBytes": receipt["steamDllSizeBytes"], "sha256": receipt["steamDllSha256"]})
    verify_file(steam_dll, steam["dll"])
    verify_pe(client)
    verify_pe(steam_dll, dll=True)


def steam_notices(repo):
    source = read_json(STEAM_LOCK)["source"]
    reference = f"{source['commit']}:{source['path']}"
    command = ["git", "-C", str(repo)]
    tree = subprocess.check_output([*command, "rev-parse", reference], text=True).strip()
    if tree != source["tree"]:
        raise ValueError("Steam transport Git tree differs from the source lock")
    return {
        name: subprocess.check_output([*command, "show", f"{source['commit']}:{name}"])
        for name in STEAM_NOTICES
    }


def rust_notices():
    lock = read_json(RUST_ARTWORK / "artwork-lock.json")
    for name in ("rust-logo.svg", "rust-logo.png", "rust-edition.ico"):
        verify_file(RUST_ARTWORK / name, lock["files"][name])
    source = (RUST_ARTWORK / "rust-logo.svg").read_bytes()
    git_blob = hashlib.sha1(f"blob {len(source)}\0".encode("ascii") + source).hexdigest()
    if lock["license"] != "CC-BY-4.0" or lock["gitBlob"] != git_blob:
        raise ValueError("Rust logo source or license differs from the artwork lock")
    return {name: (RUST_ARTWORK / name).read_bytes() for name in RUST_NOTICES}


def stage_runtime(archives, stage, openssl, client=None, steam_dll=None, receipt_path=None,
                  bootstrap=None, steam_repo=None):
    if stage.exists():
        raise ValueError("Use a fresh staging path")
    lock = verify_upstream(archives, openssl, stage.parent)
    if receipt_path is not None:
        from check_required_layers import compute_proof, verify_proof
        verify_proof(read_json(receipt_path), compute_proof(archives, steam_repo / "source/native"))
        verify_overlay(lock, read_json(receipt_path), client, steam_dll)
        verify_pe(bootstrap)
        notices = {"e4steam": steam_notices(steam_repo), "rust": rust_notices()}
    with tempfile.TemporaryDirectory(dir=stage.parent) as temporary:
        candidate = pathlib.Path(temporary) / "candidate"
        native = candidate / "native"
        unpack_runtime(archives / RUNTIME_ARCHIVE, archives / SOURCE_ARCHIVE, native, lock, receipt_path is None)
        shutil.copy2(ROOT / "portable-pack.json", candidate / "portable-pack.json")
        for name in CONTROL_FILES:
            destination = candidate / name
            destination.parent.mkdir()
            shutil.copyfile(ROOT / name, destination)
        display_name = read_json(ROOT / "library/content.json")["name"]
        presentation = json.dumps({"name": display_name}, ensure_ascii=False, indent=2) + "\n"
        (native / "presentation.json").write_bytes(presentation.encode("utf-8"))
        if receipt_path is not None:
            shutil.copyfile(client, native / "horizon-client.exe")
            shutil.copyfile(steam_dll, native / "steam_api64.dll")
            shutil.copy2(receipt_path, candidate / "overlay-receipt.json")
            shutil.copyfile(bootstrap, native / "launch-rust-edition.exe")
            for component, entries in notices.items():
                notice_dir = native / "third_party" / component
                notice_dir.mkdir(parents=True)
                for name, contents in entries.items():
                    (notice_dir / name).write_bytes(contents)
        (candidate / "library").mkdir()
        for name in LIBRARY_SOURCES:
            source = ROOT / "library" / name
            if source.is_file():
                shutil.copy2(source, candidate / "library" / name)
        candidate.rename(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", required=True, type=pathlib.Path)
    parser.add_argument("--stage", required=True, type=pathlib.Path)
    parser.add_argument("--openssl", default="openssl")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--patched-client", type=pathlib.Path)
    parser.add_argument("--steam-dll", type=pathlib.Path)
    parser.add_argument("--overlay-receipt", type=pathlib.Path)
    parser.add_argument("--bootstrap", type=pathlib.Path)
    parser.add_argument("--steam-repo", type=pathlib.Path)
    args = parser.parse_args()
    overlay_paths = (args.patched_client, args.steam_dll, args.overlay_receipt, args.bootstrap, args.steam_repo)
    if any(overlay_paths) and not all(overlay_paths):
        parser.error("Supply the patched client, Steam DLL, overlay receipt, pack bootstrap and Steam repo together")
    try:
        if args.verify_only:
            lock = verify_upstream(args.archives, args.openssl, args.stage.parent)
            if args.overlay_receipt is not None:
                from check_required_layers import compute_proof, verify_proof
                verify_proof(read_json(args.overlay_receipt), compute_proof(args.archives, args.steam_repo / "source/native"))
                verify_overlay(lock, read_json(args.overlay_receipt), args.patched_client, args.steam_dll)
                verify_pe(args.bootstrap)
                steam_notices(args.steam_repo)
                rust_notices()
        else:
            stage_runtime(args.archives, args.stage, args.openssl, *overlay_paths)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
