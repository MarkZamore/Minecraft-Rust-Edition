"""Bind the pack's client environment policy to its reviewed portable bootstrap."""

import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_POLICY = "launcher/environment-defaults.json"
BOOTSTRAP_LOCK = "tools/bootstrap-lock.json"
BOOTSTRAP_EXECUTABLE = "native/launch-rust-edition.exe"
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}", re.ASCII)
SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)


def validate_environment_defaults(value):
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError("environmentDefaults must be an object with at most 16 entries")
    seen, total = set(), 0
    for name, default in value.items():
        if not isinstance(name, str) or NAME.fullmatch(name) is None:
            raise ValueError("Invalid environmentDefaults name")
        alias = name.lower()
        if alias in seen:
            raise ValueError("Case-insensitive duplicate environmentDefaults name")
        seen.add(alias)
        if not isinstance(default, str) or any(unicodedata.category(c) == "Cc" for c in default):
            raise ValueError("Invalid environmentDefaults string value")
        try:
            encoded = default.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("Invalid environmentDefaults UTF-8 string") from error
        if len(encoded) > 1024:
            raise ValueError("environmentDefaults value exceeds 1024 UTF-8 bytes")
        total += len(name) + len(encoded)
        if total > 8192:
            raise ValueError("environmentDefaults exceeds 8192 UTF-8 bytes")
    return dict(value)


def _ordinary(path):
    if not path.is_file() or path.resolve() != path.absolute():
        raise ValueError("Bootstrap contract requires an ordinary file: " + str(path))
    return path


def _json(path):
    def unique_members(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON member in bootstrap contract: " + key)
            result[key] = value
        return result

    return json.loads(_ordinary(path).read_text(encoding="utf-8"), object_pairs_hook=unique_members)


def load_environment_defaults(root=ROOT):
    return validate_environment_defaults(_json(root / ENVIRONMENT_POLICY))


def expected_presentation(name, root=ROOT):
    return {"name": name, "environmentDefaults": load_environment_defaults(root)}


def verify_presentation(value, name, root=ROOT):
    if not isinstance(value, dict) or "environmentDefaults" not in value:
        raise ValueError("Native presentation is missing its pack environment defaults")
    validate_environment_defaults(value["environmentDefaults"])
    if value != expected_presentation(name, root):
        raise ValueError("Native presentation differs from the pack display name or environment policy")


def _sha(path):
    with _ordinary(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def bootstrap_sources(root=ROOT):
    folder = root / "launcher/standalone"
    paths = [folder / name for name in ("Cargo.toml", "Cargo.lock")]
    paths.extend(folder / name for name in (".cargo/config", ".cargo/config.toml", "build.rs")
                 if (folder / name).exists())
    source = folder / "src"
    if not source.is_dir() or source.resolve() != source.absolute():
        raise ValueError("The portable bootstrap source directory is missing or linked")
    for directory, folders, names in os.walk(source, topdown=True, followlinks=False):
        for name in folders:
            path = Path(directory) / name
            if path.resolve() != path.absolute():
                raise ValueError("The portable bootstrap source contains a linked directory")
        paths.extend(Path(directory) / name for name in names if name.endswith(".rs"))
    if not (source / "main.rs").is_file():
        raise ValueError("The portable bootstrap main source is missing")
    return {path.relative_to(root).as_posix(): _sha(path) for path in sorted(paths)}


def source_fingerprint(files):
    return hashlib.sha256("".join(f"{digest}  {name}\n" for name, digest in sorted(files.items()))
                          .encode("utf-8")).hexdigest()


def verify_bootstrap(binary, root=ROOT, lock=None):
    lock = _json(root / BOOTSTRAP_LOCK) if lock is None else lock
    if not isinstance(lock, dict) or type(lock.get("schemaVersion")) is not int or lock["schemaVersion"] != 1:
        raise ValueError("Unsupported portable bootstrap lock")
    files = bootstrap_sources(root)
    if lock.get("sourceFiles") != files or lock.get("sourceSha256") != source_fingerprint(files):
        raise ValueError("Portable bootstrap lock does not cover the current source files")
    build = lock.get("build")
    if (not isinstance(build, dict)
            or build.get("target") != "x86_64-pc-windows-msvc"
            or build.get("profile") != "release"
            or build.get("rustflags") != "-C target-feature=+crt-static"
            or not isinstance(build.get("rustc"), str) or not build["rustc"].strip()
            or not isinstance(build.get("sourceCommit"), str)
            or re.fullmatch(r"[0-9a-f]{40}", build["sourceCommit"]) is None):
        raise ValueError("Portable bootstrap build provenance is incomplete or incompatible")
    executable = lock.get("executable")
    if (not isinstance(executable, dict) or executable.get("path") != BOOTSTRAP_EXECUTABLE
            or type(executable.get("sizeBytes")) is not int or executable["sizeBytes"] <= 0
            or not isinstance(executable.get("sha256"), str)
            or SHA256.fullmatch(executable["sha256"]) is None):
        raise ValueError("Portable bootstrap executable pin is invalid")
    if _ordinary(binary).stat().st_size != executable["sizeBytes"] or _sha(binary) != executable["sha256"]:
        raise ValueError("Portable bootstrap size or SHA-256 differs from the reviewed implementation")
    return lock
