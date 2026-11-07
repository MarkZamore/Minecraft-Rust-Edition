"""Check public-server seeding and manual-list preservation in ordinary Windows launches.

Unlike --control-socket, enabling Agent Control only in the technical profile's
extras does not select synchronous/harness startup, so this exercises the path
that used to insert Horizon's public server recommendations.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time

from release_acceptance import execution_context


MANUAL_NAME = "Manual preservation QA"
MANUAL_ADDRESS = "manual.example.invalid:25565"


def manual_servers():
    def text(value):
        value = value.encode("utf-8")
        return struct.pack(">H", len(value)) + value

    def string(name, value):
        return b"\x08" + text(name) + text(value)

    entry = (string("name", MANUAL_NAME) + string("ip", MANUAL_ADDRESS)
             + b"\x01" + text("acceptTextures") + b"\x00"
             + b"\x03" + text("acceptedCodeOfConduct") + struct.pack(">i", 123456)
             + b"\x00")
    return b"\x0a\x00\x00\x09" + text("servers") + b"\x0a\x00\x00\x00\x01" + entry + b"\x00"


def ordinary_launch(client, controller, resources, qa, game, index):
    endpoint = qa / f"control-{index}.json"
    log = qa / f"client-{index}.log"
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.upper().startswith("LANMINECRAFT_") or key in {
            "HORIZON_DISPENSE_FIXTURE", "HORIZON_SCULK_FIXTURE",
            "HORIZON_COMMAND_SCRIPT", "HORIZON_NET_REPORT",
        }:
            del environment[key]
    environment.update(HORIZON_CONTROL=str(endpoint), HORIZON_NO_DISCORD="1")
    # No --control, --frames, --benchmark, --offscreen or fixture flag: normal startup.
    arguments = [str(client), "--resources", str(resources), "--game-dir", str(game),
                 "--home", str(game), "--name", "RustEditionServerListQA"]
    with log.open("wb") as output:
        process = subprocess.Popen(arguments, cwd=game, env=environment, stdout=output, stderr=output)

        def control(*words, timeout=120):
            result = subprocess.run(
                [str(controller), "--socket", str(endpoint), *words], cwd=game,
                capture_output=True, encoding="utf-8", timeout=timeout, check=True,
            )
            value = json.loads(result.stdout)
            if "error" in value:
                raise RuntimeError(value["error"])
            return value

        try:
            deadline = time.monotonic() + 120
            while not endpoint.is_file():
                if process.poll() is not None:
                    raise RuntimeError(f"Client exited before ready: {process.returncode}; see {log}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Ordinary startup did not open the technical control endpoint")
                time.sleep(0.25)
            control("wait-for", "screen", "title", "--timeout", "90")
            servers = control("servers")["servers"]
            control("quit", timeout=20)
            if process.wait(timeout=45) != 0:
                raise RuntimeError(f"Client exited with code {process.returncode}; see {log}")
            return {"servers": servers, "arguments": arguments, "exitCode": process.returncode}
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)


def clean_fixture(game, qa):
    if game.resolve().parent != qa.resolve() or game.name != "game":
        raise ValueError("Refusing cleanup outside the exact technical game directory")
    for folder, directories, files in os.walk(game, followlinks=False):
        for name in [*directories, *files]:
            path = Path(folder) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("Refusing to follow a link in the technical fixture")
    shutil.rmtree(game)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--controller", required=True, type=Path)
    parser.add_argument("--resources", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve(strict=True)
    client = candidate / "native/horizon-client.exe"
    controller = args.controller.resolve(strict=True)
    resources = args.resources.resolve(strict=True)
    qa = args.out.resolve()
    if qa.exists():
        raise ValueError("Use a fresh QA output directory in a registered task stand")
    qa.mkdir(parents=True)
    game = qa / "game"
    game.mkdir()
    # Fixture-only setting; it leaves the normal public-server startup path active.
    (game / "horizon-extras.txt").write_text("agentControl:true\n", encoding="utf-8")
    context = execution_context(candidate)
    report = {"schemaVersion": 1, "status": "FAIL", "acceptanceContext": context,
              "startup": "ordinary client; Agent Control enabled only in technical extras",
              "runs": [], "checks": {}}
    succeeded = False
    try:
        for index in (1, 2):
            run = ordinary_launch(client, controller, resources, qa, game, index)
            report["runs"].append(run)
            if run["servers"]:
                raise RuntimeError("A clean profile acquired automatic public-server entries")
            if any((game / name).exists() for name in
                   ["horizon_official_server.txt", "horizon_partner_servers.txt"]):
                raise RuntimeError("A clean profile acquired recommendation marker files")
        report["checks"]["cleanFirstLaunchAndRestart"] = "PASS"
        original = manual_servers()
        server_file = game / "servers.dat"
        server_file.write_bytes(original)
        # These legacy markers do not prove ownership of an individual server entry.
        markers = {"horizon_official_server.txt": b"manual.example.invalid:25565\n",
                   "horizon_partner_servers.txt": b"manual.example.invalid:25565\n"}
        for name, data in markers.items():
            (game / name).write_bytes(data)
        for index in (3, 4):
            run = ordinary_launch(client, controller, resources, qa, game, index)
            report["runs"].append(run)
            if run["servers"] != [{"name": MANUAL_NAME, "address": MANUAL_ADDRESS}]:
                raise RuntimeError("Manual server list changed across ordinary startup")
            if server_file.read_bytes() != original:
                raise RuntimeError("Manual servers.dat fields were rewritten")
            if any((game / name).read_bytes() != data for name, data in markers.items()):
                raise RuntimeError("Legacy recommendation markers were rewritten")
        report["checks"]["manualEntriesAndFieldsRetainedAcrossRestart"] = "PASS"
        report["manualServersSha256"] = hashlib.sha256(original).hexdigest()
        if execution_context(candidate) != context:
            raise RuntimeError("Candidate changed during acceptance")
        report["checks"]["exactCandidateUnchanged"] = "PASS"
        report["status"] = "PASS"
        succeeded = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        if succeeded:
            clean_fixture(game, qa)
            report["fixtureRemoved"] = True
            for index in (1, 2, 3, 4):
                (qa / f"client-{index}.log").unlink(missing_ok=True)
                (qa / f"control-{index}.json").unlink(missing_ok=True)
        (qa / "server-list-qa.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
