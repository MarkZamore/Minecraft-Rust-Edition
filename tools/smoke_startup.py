"""Measure three ordinary Horizon menu starts on an owned warm cache."""

import argparse
from collections import deque
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import struct
import subprocess
import tempfile
import threading
import time

from stage_runtime import file_info


MARKERS = {
    "cacheVerified": r"Verified (\d+) cached Horizon resource files in ([\d.]+)s",
    "loadingScreen": r"Horizon: loading screen shown after (\d+) ms",
    "titleArtwork": r"Horizon: loading the title artwork after (\d+) ms",
    "firstFrame": r"Horizon: first frame after (\d+) ms",
    "archiveWritten": r"Horizon: wrote horizon-data\.bin in (\d+) ms",
    "archiveRejected": r"Rebuilding only the generated Horizon data archive:",
}


def owned_root(path):
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError(f"Fixture path passes through a link: {ancestor}")
    for ancestor in path.parents:
        if ancestor.parent.name == "agent-runs" and (ancestor / "RUN.json").is_file():
            return ancestor
    raise ValueError("Fixture paths must be inside a registered artifacts/agent-runs directory")


def snapshot(path):
    return {**file_info(path), "lastWriteNs": path.stat().st_mtime_ns}


def first_payload_offset(archive):
    size = archive.stat().st_size
    with archive.open("rb") as stream:
        magic, version, count, path_len, source_len, _ = struct.unpack("<8sIIQQQ", stream.read(40))
        index = 40 + ((path_len + 7) & ~7) + ((source_len + 7) & ~7)
        if magic != b"HZDATA\0\0" or version != 2 or not 0 < count <= (size - index) // 24:
            raise ValueError("Expected a nonempty Horizon v2 data archive")
        stream.seek(index)
        for _ in range(count):
            _, _, offset, length = struct.unpack("<IIQQ", stream.read(24))
            if length and index + count * 24 <= offset < offset + length <= size:
                return offset
        raise ValueError("Expected a nonempty archive member")


def run_client(bootstrap, game, cache, absent_minecraft, timeout, *, expect_archive, expect_repair):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("LANMINECRAFT_", "HORIZON_", "STEAM"))
    }
    environment["LANMINECRAFT_NATIVE_CACHE"] = str(cache)
    environment["RUST_LOG"] = "info"
    started = time.monotonic()
    process = subprocess.Popen(
        [str(bootstrap), "--game-dir", str(game), "--minecraft-dir", str(absent_minecraft)],
        cwd=game, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    events = {}
    received = Queue()
    tail = deque(maxlen=12)

    def collect():
        with process.stdout:
            for line in process.stdout:
                received.put((time.monotonic(), line.rstrip()))

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    required = {"cacheVerified", "loadingScreen", "titleArtwork", "firstFrame"}
    if expect_archive:
        required.add("archiveWritten")
    if expect_repair:
        required.add("archiveRejected")
    try:
        while not required <= events.keys():
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"Missing startup events: {sorted(required - events.keys())}")
            try:
                timestamp, line = received.get(timeout=0.2)
            except Empty:
                if process.poll() is not None:
                    raise RuntimeError(f"Bootstrap exited before ready: {process.returncode}")
                continue
            tail.append(line[-300:])
            for name, pattern in MARKERS.items():
                if name not in events and (match := re.search(pattern, line)):
                    events[name] = {
                        "wallMilliseconds": round((timestamp - started) * 1000, 1),
                        "reported": match.groups(),
                    }
        if process.poll() is not None:
            raise RuntimeError(f"Bootstrap did not stay alive at the menu: {process.returncode}")
        if ("archiveRejected" in events) != expect_repair:
            raise RuntimeError("Unexpected archive rejection during normal startup")
        return events
    except (RuntimeError, TimeoutError) as error:
        raise RuntimeError(f"{error}\nOwned client log tail:\n" + "\n".join(tail)) from error
    finally:
        if process.poll() is None:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=15,
            )
        process.wait(timeout=15)
        reader.join(timeout=3)


def smoke(args):
    bootstrap, cache, qa = (Path(os.path.abspath(path)) for path in (args.bootstrap, args.cache, args.qa))
    owner = owned_root(qa)
    if owned_root(bootstrap) != owner or owned_root(cache) != owner:
        raise ValueError("The bootstrap, shared cache and output must belong to the same task")
    if cache in qa.parents or qa in cache.parents:
        raise ValueError("The output and warm resource cache must be separate")
    client = bootstrap.with_name("horizon-client.exe")
    bootstrap_info = file_info(bootstrap)
    client_info = file_info(client)
    if (bootstrap_info["sha256"] != args.bootstrap_sha256.lower() or
            client_info["sha256"] != args.client_sha256.lower()):
        raise ValueError("Bootstrap or client bytes differ from the approved candidate")

    receipt = cache / "resources.json"
    resource_index = cache / "resources" / "horizon-resources.json"
    archive = resource_index.with_name("horizon-data.bin")
    if archive.exists() or archive.is_symlink():
        raise ValueError("Use an already prepared cache before its first v2 archive is generated")
    expected_key = ":".join(
        file_info(bootstrap.with_name(name))["sha256"]
        for name in ("horizon-prepare.exe", "research-lock.json")
    )
    if json.loads(receipt.read_text(encoding="utf-8"))["key"] != expected_key:
        raise ValueError("The warm cache does not belong to this verified preparation recipe")
    original_receipt = snapshot(receipt)
    original_index = snapshot(resource_index)

    qa.mkdir(parents=True, exist_ok=False)
    report = {
        "bootstrap": bootstrap_info, "client": client_info, "resourceReceipt": original_receipt,
        "resourceIndex": original_index,
        "conditions": "Ordinary menu startup, no control socket or Steam profile. Timings use process logs.",
    }

    def checkpoint(phase):
        report["phase"] = phase
        (qa / "startup.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    # An absent explicit Minecraft folder stops a failed warm check before it
    # can discover a personal installation or rebuild the shared loose cache.
    absent = qa / "no-minecraft-input"
    with tempfile.TemporaryDirectory(prefix="game-", dir=qa) as directory:
        game = Path(directory)
        (game / "horizon-extras.txt").write_text("discordPresence:false\n", encoding="utf-8")
        checkpoint("generating")
        report["generated"] = run_client(
            bootstrap, game, cache, absent, args.timeout, expect_archive=True, expect_repair=False)
        pristine = snapshot(archive)
        report["archive"] = pristine
        offset = first_payload_offset(archive)
        checkpoint("checking warm reuse")
        report["reused"] = run_client(
            bootstrap, game, cache, absent, args.timeout, expect_archive=False, expect_repair=False)
        if snapshot(archive) != pristine:
            raise RuntimeError("Warm startup rewrote or changed the generated archive")

        checkpoint("checking repair")
        with archive.open("r+b") as stream:
            stream.seek(offset)
            original_byte = stream.read(1)
            stream.seek(offset)
            stream.write(bytes([original_byte[0] ^ 1]))
        tampered = snapshot(archive)
        if tampered["sizeBytes"] != pristine["sizeBytes"] or tampered["sha256"] == pristine["sha256"]:
            raise RuntimeError("The fixture did not create a same-size archive corruption")
        try:
            report["repaired"] = run_client(
                bootstrap, game, cache, absent, args.timeout, expect_archive=True, expect_repair=True)
        finally:
            if archive.is_file() and snapshot(archive) == tampered:
                with archive.open("r+b") as stream:
                    stream.seek(offset)
                    stream.write(original_byte)
    restored = snapshot(archive)
    if (restored["sha256"], restored["sizeBytes"]) != (pristine["sha256"], pristine["sizeBytes"]):
        raise RuntimeError("The exact generated archive bytes were not restored")
    if snapshot(receipt) != original_receipt or snapshot(resource_index) != original_index:
        raise RuntimeError("Archive repair modified the loose-resource receipt or index")
    if file_info(bootstrap) != bootstrap_info or file_info(client) != client_info:
        raise RuntimeError("Candidate bytes changed during startup verification")
    report.update(tampered=tampered, restored=restored)
    checkpoint("passed")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", required=True, type=Path)
    parser.add_argument("--bootstrap-sha256", required=True)
    parser.add_argument("--client-sha256", required=True)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--qa", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=360)
    smoke(parser.parse_args())
