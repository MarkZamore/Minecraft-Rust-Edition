"""Check Steam-first server views and saved-list preservation in ordinary Windows launches.

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
STEAM_WIDGET = "steam-worlds"
SAVED_WIDGET = "saved-servers"
REFRESH_WIDGET = "server-list-refresh"
BACK_WIDGET = "server-list-back"
LEGACY_SERVERS = [
    {"name": "Horizon Official", "address": "play.playhorizon.dev"},
    {"name": "Afri-Network (South Africa)", "address": "afri-network.co.za"},
    {"name": MANUAL_NAME, "address": MANUAL_ADDRESS},
]


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


def legacy_servers():
    """Two author-seeded entries and an unrelated manual row with unknown NBT."""
    def text(value):
        value = value.encode("utf-8")
        return struct.pack(">H", len(value)) + value

    def string(name, value):
        return b"\x08" + text(name) + text(value)

    entries = []
    for row in LEGACY_SERVERS:
        entry = string("name", row["name"]) + string("ip", row["address"])
        if row["name"] == MANUAL_NAME:
            entry += (b"\x01" + text("acceptTextures") + b"\x00"
                      + b"\x03" + text("acceptedCodeOfConduct") + struct.pack(">i", 123456)
                      + string("futureEditionMetadata", "preserve-unknown-row-v1"))
        entries.append(entry + b"\x00")
    unknown_root = b"\x04" + text("unownedFutureCounter") + struct.pack(">q", 1234567890123)
    return (b"\x0a\x00\x00\x09" + text("servers") + b"\x0a"
            + struct.pack(">i", len(entries)) + b"".join(entries) + unknown_root + b"\x00")


def widget(state, labels, kind=None):
    matches = [node for node in state["widgets"]
               if node.get("label") in labels and (kind is None or node["kind"] == kind)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one widget {labels!r}, got {matches!r}; ui={state!r}")
    return matches[0]


def widget_id(state, identifier):
    matches = [node for node in state["widgets"] if node["id"] == identifier]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one stable widget {identifier!r}; ui={state!r}")
    return matches[0]


def ui_summary(state):
    return [{key: node[key] for key in ("id", "kind", "label", "enabled", "selected") if key in node}
            for node in state["widgets"]]


class DesktopKeys:
    """Ask the UI driver for real keys without changing the tested client.

    The native control protocol rejects key commands while the title is up.
    Each atomic request must be serviced against the observed, owned window;
    the UI driver writes a matching acknowledgement only after its key action.
    """

    def __init__(self, directory, process, client, title, run):
        self.directory, self.process = directory, process
        self.client, self.title, self.run = client, title, run
        self.evidence = []

    def press(self, key):
        if self.directory is None:
            raise ValueError("Server-view keys require --ui-key-requests and a desktop UI driver")
        self.directory.mkdir(parents=True, exist_ok=True)
        identifier = f"run-{self.run}-key-{len(self.evidence) + 1}"
        created = time.time_ns() // 1_000_000
        request = {"id": identifier, "action": "press_key", "key": key,
                   "processId": self.process.pid, "executable": str(self.client),
                   "title": self.title, "run": self.run,
                   "createdUnixMs": created, "timeoutMs": 120_000}
        path = self.directory / f"{identifier}.request.json"
        acknowledgement = self.directory / f"{identifier}.ack.json"
        if path.exists() or acknowledgement.exists():
            raise ValueError("Stale desktop-key checkpoint: " + identifier)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(request, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        print(json.dumps({"desktopKeyRequest": str(path), **request}), flush=True)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Owned client exited while waiting for desktop key " + identifier)
            try:
                ack = json.loads(acknowledgement.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(0.1)
                continue
            performed = ack.get("performedUnixMs")
            if (ack.get("id") != identifier or ack.get("status") != "PASS"
                    or ack.get("processId") != self.process.pid or ack.get("key") != key
                    or not isinstance(performed, int) or isinstance(performed, bool)
                    or not created <= performed <= created + request["timeoutMs"]):
                raise RuntimeError("Desktop key was not acknowledged for the owned native window: " + repr(ack))
            self.evidence.append({"request": request, "acknowledgement": ack})
            # Wait for the window event to reach the ordinary UI before querying
            # a new widget tree; the UI query still waits for the next draw.
            time.sleep(0.25)
            return
        raise TimeoutError("Desktop key acknowledgement timed out: " + identifier)


def check_server_views(control, game, original, desktop_keys):
    """Exercise actual drawn rows and ordinary click/key routes, without joining a server."""
    evidence = {}
    expected_names = {row["name"] for row in LEGACY_SERVERS}

    def ui():
        return control("ui")

    def click(labels, kind=None):
        node = widget(ui(), labels, kind)
        if not node["enabled"]:
            raise RuntimeError(f"Expected enabled widget {node!r}")
        control("click", node["id"])
        return ui()

    def click_id(identifier):
        node = widget_id(ui(), identifier)
        if not node["enabled"]:
            raise RuntimeError(f"Expected enabled widget {node!r}")
        control("click", node["id"])
        return ui()

    def unchanged():
        if control("servers")["servers"] != LEGACY_SERVERS:
            raise RuntimeError("Stored legacy/manual rows changed during view navigation")
        if (game / "servers.dat").read_bytes() != original:
            raise RuntimeError("View navigation or status pings rewrote unknown/saved NBT")

    def steam_default(state):
        steam = widget_id(state, STEAM_WIDGET)
        saved = widget_id(state, SAVED_WIDGET)
        for identifier in (REFRESH_WIDGET, BACK_WIDGET):
            if not widget_id(state, identifier)["enabled"]:
                raise RuntimeError(f"List navigation widget is disabled: {identifier}")
        if steam.get("selected") is not True and steam["enabled"]:
            raise RuntimeError("Steam view is not selected by default")
        if saved.get("selected") is True or not saved["enabled"]:
            raise RuntimeError("Saved servers opened implicitly")
        shown_names = {node.get("label") for node in state["widgets"] if node["kind"] == "row"}
        if shown_names & expected_names:
            raise RuntimeError("A saved server is drawn in the default Steam view")
        for names in ({"Join Server"}, {"Edit"}, {"Delete"}):
            if widget(state, names, "button")["enabled"]:
                raise RuntimeError(f"Unselected/hidden saved entry can trigger {names!r}")
        unchanged()

    click({"Multiplayer"}, "button")
    state = ui()
    steam_default(state)
    evidence["defaultSteam"] = ui_summary(state)
    # A stale hidden selection must not activate a saved row via Enter.
    desktop_keys.press("Enter")
    steam_default(ui())
    desktop_keys.press("Delete")
    steam_default(ui())
    evidence["defaultEnterAndDeleteStayedInSteamView"] = True
    click_id(REFRESH_WIDGET)
    steam_default(ui())
    desktop_keys.press("F5")
    steam_default(ui())
    evidence["defaultRefreshStayedInSteamView"] = True
    click_id(SAVED_WIDGET)
    state = ui()
    if widget_id(state, SAVED_WIDGET).get("selected") is not True and widget_id(state, SAVED_WIDGET)["enabled"]:
        raise RuntimeError("Explicit Saved view was not selected")
    shown_names = {node.get("label") for node in state["widgets"] if node["kind"] == "row"}
    if not expected_names.issubset(shown_names):
        raise RuntimeError(f"Explicit Saved view lost rows: {expected_names - shown_names!r}")
    evidence["savedView"] = ui_summary(state)
    unchanged()
    click_id(REFRESH_WIDGET)
    state = ui()
    if widget_id(state, SAVED_WIDGET).get("selected") is not True and widget_id(state, SAVED_WIDGET)["enabled"]:
        raise RuntimeError("Refreshing Saved implicitly returned to the Steam view")
    desktop_keys.press("F5")
    state = ui()
    if widget_id(state, SAVED_WIDGET).get("selected") is not True and widget_id(state, SAVED_WIDGET)["enabled"]:
        raise RuntimeError("F5 in Saved implicitly returned to the Steam view")
    shown_names = {node.get("label") for node in state["widgets"] if node["kind"] == "row"}
    if not expected_names.issubset(shown_names):
        raise RuntimeError("Refreshing Saved lost stored entries")
    unchanged()
    evidence["savedRefreshPreservedViewAndData"] = True
    click({MANUAL_NAME}, "row")
    state = ui()
    for names in ({"Join Server"}, {"Edit"}, {"Delete"}):
        if not widget(state, names, "button")["enabled"]:
            raise RuntimeError(f"Selected manual entry cannot use {names!r}")
    evidence["savedSelection"] = ui_summary(state)
    # Opening editors and cancelling is read-only with respect to servers.dat.
    click({"Edit"}, "button")
    state = ui()
    if len([node for node in state["widgets"] if node["kind"] == "field"]) != 2:
        raise RuntimeError("The saved-server editor is not accessible")
    click({"Cancel"}, "button")
    click({"Direct Connection"}, "button")
    # The author records this edit box by stable ID without a label in the
    # control tree; its separately drawn "Server Address" text is not a widget.
    field = widget_id(ui(), "field-0")
    if field["kind"] != "field" or not field["enabled"]:
        raise RuntimeError("The direct-address edit box is not accessible")
    control("click", field["id"])
    desktop_keys.press("Ctrl+A")
    desktop_keys.press("Backspace")
    if widget(ui(), {"Join Server"}, "button")["enabled"]:
        raise RuntimeError("Direct-address field did not clear")
    control("type", MANUAL_ADDRESS)
    if not widget(ui(), {"Join Server"}, "button")["enabled"]:
        raise RuntimeError("Manual direct-address input no longer enables joining")
    evidence["manualDirectInput"] = ui_summary(ui())
    click({"Cancel"}, "button")
    click({"Add Server"}, "button")
    if len([node for node in ui()["widgets"] if node["kind"] == "field"]) != 2:
        raise RuntimeError("Adding a manual server is not accessible")
    click({"Cancel"}, "button")
    # Leave Saved with a selected row; avoid a second click on an existing
    # selection, which would join it through the author's double-click route.
    if widget_id(ui(), SAVED_WIDGET)["enabled"]:
        click_id(SAVED_WIDGET)
    if not widget(ui(), {"Join Server"}, "button")["enabled"]:
        click({MANUAL_NAME}, "row")
    click_id(STEAM_WIDGET)
    steam_default(ui())
    desktop_keys.press("Enter")
    steam_default(ui())
    evidence["selectionResetOnSteamSwitch"] = True
    # Re-enter through the actual title button, even after the last view was Saved.
    click_id(SAVED_WIDGET)
    click_id(BACK_WIDGET)
    click({"Multiplayer"}, "button")
    steam_default(ui())
    evidence["titleReentryDefaultsToSteam"] = True
    click_id(BACK_WIDGET)
    unchanged()
    evidence["unknownNbtRetainedByteForByte"] = True
    evidence["desktopKeys"] = desktop_keys.evidence
    return evidence


def ordinary_launch(client, controller, resources, qa, game, index, build_name, visual_check=False,
                    view_original=None, ui_key_requests=None):
    endpoint = qa / f"control-{index}.json"
    log = qa / f"client-{index}.log"
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.upper().startswith("LANMINECRAFT_") or key in {
            "HORIZON_DISPENSE_FIXTURE", "HORIZON_SCULK_FIXTURE",
            "HORIZON_COMMAND_SCRIPT", "HORIZON_NET_REPORT",
        }:
            del environment[key]
    environment.update(HORIZON_CONTROL=str(endpoint), HORIZON_NO_DISCORD="1",
                       LANMINECRAFT_BUILD_NAME=build_name)
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
            if visual_check:
                print("Ready for visual window-icon check", flush=True)
                time.sleep(40)
            servers = control("servers")["servers"]
            views = check_server_views(control, game, view_original,
                                       DesktopKeys(ui_key_requests, process, client, build_name, index)) \
                if view_original is not None else None
            control("quit", timeout=20)
            if process.wait(timeout=45) != 0:
                raise RuntimeError(f"Client exited with code {process.returncode}; see {log}")
            return {"servers": servers, "arguments": arguments, "exitCode": process.returncode,
                    **({"serverViews": views} if views is not None else {})}
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
    parser.add_argument("--ui-key-requests", type=Path,
                        help="Desktop-key request/acknowledgement directory inside --out; required for view checks")
    parser.add_argument("--visual-check", action="store_true",
                        help="Hold the first ordinary title screen for 40 seconds for visual review")
    args = parser.parse_args()
    candidate = args.candidate.resolve(strict=True)
    client = candidate / "native/horizon-client.exe"
    build_name = json.loads((candidate / "native/presentation.json").read_text(encoding="utf-8"))["name"]
    controller = args.controller.resolve(strict=True)
    resources = args.resources.resolve(strict=True)
    qa = args.out.resolve()
    if qa.exists():
        raise ValueError("Use a fresh QA output directory in a registered task stand")
    qa.mkdir(parents=True)
    ui_key_requests = args.ui_key_requests.resolve() if args.ui_key_requests else None
    if ui_key_requests is not None and not ui_key_requests.is_relative_to(qa):
        raise ValueError("Desktop-key checkpoints must stay inside the fresh QA output directory")
    game = qa / "game"
    game.mkdir()
    # Fixture-only setting; it leaves the normal public-server startup path active.
    (game / "horizon-extras.txt").write_text("agentControl:true\n", encoding="utf-8")
    (game / "options.txt").write_text(
        "version:5023\nlang:en_us\nonboardAccessibility:false\nskipMultiplayerWarning:true\nnarrator:0\n",
        encoding="utf-8")
    context = execution_context(candidate)
    report = {"schemaVersion": 1, "status": "FAIL", "acceptanceContext": context,
              "startup": "ordinary client; Agent Control enabled only in technical extras",
              "runs": [], "checks": {}}
    succeeded = False
    try:
        for index in (1, 2):
            run = ordinary_launch(client, controller, resources, qa, game, index,
                                  build_name, args.visual_check and index == 1)
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
            run = ordinary_launch(client, controller, resources, qa, game, index, build_name)
            report["runs"].append(run)
            if run["servers"] != [{"name": MANUAL_NAME, "address": MANUAL_ADDRESS}]:
                raise RuntimeError("Manual server list changed across ordinary startup")
            if server_file.read_bytes() != original:
                raise RuntimeError("Manual servers.dat fields were rewritten")
            if any((game / name).read_bytes() != data for name, data in markers.items()):
                raise RuntimeError("Legacy recommendation markers were rewritten")
        report["checks"]["manualEntriesAndFieldsRetainedAcrossRestart"] = "PASS"
        report["manualServersSha256"] = hashlib.sha256(original).hexdigest()
        original = legacy_servers()
        server_file.write_bytes(original)
        markers = {"horizon_official_server.txt": b"play.playhorizon.dev\n",
                   "horizon_partner_servers.txt": b"afri-network.co.za\n"}
        for name, data in markers.items():
            (game / name).write_bytes(data)
        for index in (5, 6):
            run = ordinary_launch(client, controller, resources, qa, game, index, build_name,
                                  view_original=original, ui_key_requests=ui_key_requests)
            report["runs"].append(run)
            if run["servers"] != LEGACY_SERVERS or server_file.read_bytes() != original:
                raise RuntimeError("Legacy/manual data changed across ordinary view-check startup")
            if any((game / name).read_bytes() != data for name, data in markers.items()):
                raise RuntimeError("View navigation changed ambiguous legacy markers")
        report["checks"]["steamDefaultAndExplicitSavedView"] = "PASS"
        report["checks"]["hiddenSavedSelectionCannotJoinEditOrDelete"] = "PASS"
        report["checks"]["manualEditorAndDirectInputAccessible"] = "PASS"
        report["checks"]["titleReentryDefaultsToSteam"] = "PASS"
        report["checks"]["legacyUnknownNbtPreservedByteForByte"] = "PASS"
        report["legacyServersSha256"] = hashlib.sha256(original).hexdigest()
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
            for index in (1, 2, 3, 4, 5, 6):
                (qa / f"client-{index}.log").unlink(missing_ok=True)
                (qa / f"control-{index}.json").unlink(missing_ok=True)
        (qa / "server-list-qa.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
