"""Exact-payload acceptance, independent of pack HEAD and library prose."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from stage_runtime import ROOT, read_json


ACCEPTANCE = ROOT / "tools/release-acceptance.json"


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def context_from_files(files, receipt):
    return {
        "upstreamCommit": receipt["upstreamCommit"],
        "steamTransportCommit": receipt["steamTransportCommit"],
        "recipeSha256": receipt["recipeSha256"],
        "overlayReceiptSha256": canonical_sha(receipt),
        "payloadInventorySha256": canonical_sha(files),
    }


def execution_context(candidate):
    # Import lazily: the packer also uses this module.
    from generate_manifest import inventory
    return context_from_files(inventory(candidate), read_json(candidate / "overlay-receipt.json"))


def require(condition, message):
    if not condition:
        raise ValueError("Release acceptance: " + message)


NATIVE_NOTICE_CHECKS = (
    "bootstrapChildPidAndTitle", "titleVisiblePixelsAndFreshPresentation",
    "threeCardsAndSevenSecondExpiry", "malformedAndTornKeepPriorUntilOriginalExpiry",
    "future1500msRetainedUntilItsTime", "dpiOnceAndGameGuiScaleUnchanged",
    "actualFullscreenVisibleNotice", "worldAndChatInputWhileVisible",
    "pauseMenuVisibleWithoutInputWidgets", "emptyQueueFreshHeartbeat",
    "sessionSentinelAndNoExtraMod", "exactCandidateUnchanged",
)
NATIVE_NOTICE_CAPTURES = {
    "title-one.png": 1, "title-three.png": 3, "expired-three.png": 0,
    "torn-snapshot-retains-card.png": 1, "torn-snapshot-original-expiry.png": 0,
    "future-not-yet-visible.png": 0, "future-notice-retained.png": 1,
    "dpi-one.png": 1, "dpi-two.png": 1, "fullscreen-world.png": 1,
    "world-chat-input-with-card.png": 1, "paused-world-three.png": 3,
    "paused-zero-fresh-heartbeat.png": 0,
}


def _notice_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _notice_rect(value):
    return (isinstance(value, list) and len(value) == 4 and all(map(_notice_int, value))
            and value[2] > value[0] and value[3] > value[1])


def _notice_main_title(value):
    if not isinstance(value, dict) or value.get("screen") != "title" \
            or not isinstance(value.get("widgets"), list) \
            or not all(isinstance(widget, dict) for widget in value["widgets"]):
        return False
    labels = {widget.get("label") for widget in value["widgets"] if widget.get("enabled") is True}
    return {"Singleplayer", "Multiplayer"}.issubset(labels) and not any(
        widget.get("id") in {"browse-mods", "open-mod-folder", "done"} for widget in value["widgets"])


def verify_native_notices(report, files, context, name):
    require(isinstance(report, dict) and report.get("schemaVersion") == 1
            and report.get("status") == "PASS", "native notice runtime QA missing")
    require(report.get("acceptanceContext") == context, "native notice report has another payload")
    require(report.get("client") == files["native/horizon-client.exe"]
            and report.get("bootstrap") == files["native/launch-rust-edition.exe"],
            "native notice report has another executable")
    require(report.get("startup") == "exact bootstrap child; native-ui-v1; real presented unscaled screenshots",
            "native notices were not tested through the exact bootstrap child")
    require(report.get("fixtureRemoved") is True
            and all(report.get("checks", {}).get(key) == "PASS" for key in NATIVE_NOTICE_CHECKS),
            "native notice scenarios or cleanup incomplete")
    native, bootstrap = report.get("nativeProcessId"), report.get("bootstrapProcessId")
    require(_notice_int(native) and native > 0 and _notice_int(bootstrap) and bootstrap > 0
            and native != bootstrap, "native notice child PID proof missing")
    ancestry = report.get("processAncestry", [])
    require(isinstance(ancestry, list) and 2 <= len(ancestry) <= 12
            and all(isinstance(row, dict) for row in ancestry)
            and ancestry[0].get("pid") == native and ancestry[-1].get("pid") == bootstrap
            and all(left.get("parent") == right.get("pid") for left, right in zip(ancestry, ancestry[1:]))
            and str(ancestry[0].get("path", "")).replace("\\", "/").rsplit("/", 1)[-1].lower() == "horizon-client.exe",
            "native notice process does not descend from the tested bootstrap")
    captures = report.get("captures", [])
    require(isinstance(captures, list) and len(captures) == len(NATIVE_NOTICE_CAPTURES)
            and all(isinstance(capture, dict) for capture in captures), "native notice captures incomplete")
    by_name = {capture.get("file"): capture for capture in captures}
    require(set(by_name) == set(NATIVE_NOTICE_CAPTURES), "native notice capture coverage differs")

    def presented(heartbeat, observed, expected_count):
        require(isinstance(heartbeat, dict) and _notice_int(observed), "native notice heartbeat evidence missing")
        stamp = heartbeat.get("timestampUnixMs")
        require(heartbeat.get("backend") == "native-ui-v1" and _notice_int(heartbeat.get("pid"))
                and heartbeat["pid"] == native and _notice_int(heartbeat.get("transformations"))
                and heartbeat["transformations"] == 0 and heartbeat.get("error") == ""
                and _notice_int(heartbeat.get("renderedCount"))
                and heartbeat["renderedCount"] == expected_count and _notice_int(stamp)
                and stamp > 0 and 0 <= observed-stamp <= 1500,
                "native notice heartbeat is stale, foreign or not actually presented")
        return stamp

    for filename, expected_count in NATIVE_NOTICE_CAPTURES.items():
        capture = by_name[filename]
        require(_notice_int(capture.get("sizeBytes")) and capture["sizeBytes"] > 8
                and isinstance(capture.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", capture["sha256"]) is not None,
                "native notice PNG evidence missing")
        pixels, window = capture.get("pixels", {}), capture.get("window", {})
        require(isinstance(pixels, dict) and isinstance(window, dict)
                and _notice_int(pixels.get("count")) and pixels["count"] == expected_count
                and isinstance(pixels.get("boxes"), list) and len(pixels["boxes"]) == expected_count,
                "native notice pixels do not match the presented card count")
        require(_notice_rect(window.get("client")) and _notice_int(window.get("hwnd"))
                and window["hwnd"] > 0 and window.get("title") == name,
                "native notice window proof differs")
        left, top, right, bottom = window["client"]
        require(pixels.get("size") == [right-left, bottom-top], "native notice image is not the real client size")
        for box in pixels["boxes"]:
            require(_notice_rect(box) and 0 <= box[0] < box[2] <= right-left
                    and 0 <= box[1] < box[3] <= bottom-top,
                    "native notice pixel bounds are outside the actual image")
            require(isinstance(pixels.get("expectedPhysicalWidth"), (int, float))
                    and abs((box[2]-box[0])-pixels["expectedPhysicalWidth"]) <= 5,
                    "native notice physical width/DPI proof differs")
        stamps = []
        for key, observed_key in (("heartbeatBefore", "heartbeatBeforeObservedUnixMs"),
                                  ("heartbeatAfter", "heartbeatAfterObservedUnixMs")):
            heartbeat, observed = capture.get(key), capture.get(observed_key)
            stamps.append(presented(heartbeat, observed, expected_count))
        captured = capture.get("capturedUnixMs")
        require(_notice_int(captured) and capture["heartbeatBeforeObservedUnixMs"] <= captured
                <= capture["heartbeatAfterObservedUnixMs"] and stamps[1] > stamps[0],
                "native notice capture has no subsequent presented frame")

    timing = report.get("timing", {})
    require(isinstance(timing, dict) and _notice_int(timing.get("lifeMs")) and timing["lifeMs"] == 7000,
            "native notice lifetime timing evidence missing")

    def lifetime_input(key):
        value = timing.get(key, {})
        require(isinstance(value, dict) and _notice_int(value.get("createdUnixMs"))
                and value["createdUnixMs"] > 0 and _notice_int(value.get("sentUnixMs"))
                and 0 <= value["sentUnixMs"]-value["createdUnixMs"] <= 1000,
                "native notice creation/send timing differs")
        return value, value["createdUnixMs"], value["createdUnixMs"] + 7000

    def observation(value, count, earliest, latest):
        require(isinstance(value, dict), "native notice timing observation missing")
        stamp = presented(value.get("heartbeat"), value.get("observedUnixMs"), count)
        require(earliest <= stamp <= value["observedUnixMs"] <= latest,
                "native notice observed outside its required lifetime interval")
        return stamp

    def capture_interval(filename, earliest, latest):
        capture = by_name[filename]
        require(earliest <= capture["heartbeatBefore"]["timestampUnixMs"]
                <= capture["heartbeatBeforeObservedUnixMs"] <= capture["capturedUnixMs"]
                <= capture["heartbeatAfterObservedUnixMs"] <= latest
                and capture["heartbeatAfter"]["timestampUnixMs"] >= earliest,
                "native notice capture is outside its creation/expiry interval")

    lifetime, created, expiry = lifetime_input("threeCards")
    capture_interval("title-three.png", lifetime["sentUnixMs"], created + 5999)
    observation(lifetime.get("nearExpiry"), 3, created + 6000, expiry - 1)
    expired = observation(lifetime.get("expired"), 0, expiry, expiry + 1000)
    capture_interval("expired-three.png", expired, expiry + 4000)

    torn, created, expiry = lifetime_input("tornSnapshot")
    invalid = torn.get("invalidSnapshots", [])
    require(isinstance(invalid, list) and len(invalid) == 2
            and all(isinstance(value, dict) for value in invalid),
            "native notice malformed snapshot observations missing")
    previous = torn["sentUnixMs"]
    for value, kind, delay in zip(invalid, ("invalid-utf8", "broken-unicode-escape"), (2000, 4000)):
        written = value.get("writtenUnixMs")
        require(value.get("kind") == kind and _notice_int(written)
                and max(previous, created + delay) <= written < expiry - 1000,
                "native notice malformed snapshot was not tested during the original lifetime")
        retained = observation(value, 1, written + 500, expiry - 1)
        previous = value["observedUnixMs"]
    capture_interval("torn-snapshot-retains-card.png", retained, expiry - 1)
    require(by_name["torn-snapshot-retains-card.png"]["capturedUnixMs"] >= previous,
            "native notice retained capture precedes the malformed snapshot observations")
    expired = observation(torn.get("expired"), 0, expiry, expiry + 1000)
    capture_interval("torn-snapshot-original-expiry.png", expired, expiry + 4000)

    future = timing.get("futureNotice", {})
    require(isinstance(future, dict) and all(_notice_int(future.get(key)) for key in
            ("queuedUnixMs", "createdUnixMs", "sentUnixMs"))
            and future["queuedUnixMs"] > 0
            and future["createdUnixMs"]-future["queuedUnixMs"] == 1500
            and future["queuedUnixMs"] <= future["sentUnixMs"] < future["createdUnixMs"],
            "native notice future skew was not the observed +1500 ms input")
    capture_interval("future-not-yet-visible.png", future["sentUnixMs"], future["createdUnixMs"] - 1)
    capture_interval("future-notice-retained.png", future["createdUnixMs"], future["createdUnixMs"] + 6999)

    dpi = report.get("dpi", {})
    one, two = by_name["dpi-one.png"]["pixels"], by_name["dpi-two.png"]["pixels"]
    first_width, second_width = (value["boxes"][0][2]-value["boxes"][0][0] for value in (one, two))
    require(isinstance(dpi, dict) and _notice_int(dpi.get("oneWidth"))
            and _notice_int(dpi.get("twoWidth")) and dpi["oneWidth"] == first_width
            and dpi["twoWidth"] == second_width and abs(second_width-2*first_width) <= 5
            and one["expectedPhysicalWidth"] == 240 and two["expectedPhysicalWidth"] == 480
            and one["size"][0] >= 264 and two["size"][0] >= 528,
            "native notice DPI did not scale the measured physical width exactly once")
    require(isinstance(dpi.get("guiScaleBefore"), str)
            and re.fullmatch(r"guiScale:[0-9]+", dpi["guiScaleBefore"]) is not None
            and dpi["guiScaleBefore"] == dpi.get("guiScaleAfter") == dpi.get("guiScale")
            and isinstance(dpi.get("uiBefore"), dict)
            and dpi["uiBefore"].get("screen") == "title"
            and isinstance(dpi["uiBefore"].get("widgets"), list) and dpi["uiBefore"]["widgets"]
            and dpi["uiBefore"] == dpi.get("uiAfter"),
            "native notice DPI changed game GUI scale/widgets or lacks before/after observations")
    title_before, title_after = report.get("titleUiBefore"), report.get("titleUiAfter")
    require(_notice_main_title(title_before) and _notice_main_title(title_after)
            and title_before == title_after == dpi["uiBefore"],
            "native notice title proof is not unchanged actual main title widgets")
    fullscreen = by_name["fullscreen-world.png"]["window"]
    require(_notice_rect(fullscreen.get("monitor"))
            and all(abs(a-b) <= 2 for a, b in zip(fullscreen["client"], fullscreen["monitor"])),
            "native notice fullscreen proof is not monitor-sized")
    before, after = report.get("pauseUiBefore"), report.get("pauseUiAfter")
    require(isinstance(before, dict) and before.get("screen") == "paused"
            and isinstance(before.get("widgets"), list) and before["widgets"] and before == after,
            "native notices changed game UI widgets or have no pause proof")
    require(_notice_int(report.get("foregroundBeforeChat"))
            and report["foregroundBeforeChat"] > 0
            and _notice_int(report.get("foregroundAfterChat"))
            and report["foregroundBeforeChat"] == report["foregroundAfterChat"]
                == fullscreen["hwnd"] == by_name["world-chat-input-with-card.png"]["window"]["hwnd"]
            and isinstance(report.get("technicalChat"), list)
            and any("online" in str(line).casefold() for line in report["technicalChat"]),
            "native notice input/focus and actual chat proof missing")


def verify_startup(record, files, receipt, name):
    require(record["schemaVersion"] == 1, "unknown schema")
    context = context_from_files(files, receipt)
    require(record["context"] == context, "stale payload, source recipe or Steam pin")
    smoke, pair, preparation = (record[key] for key in ("smoke", "multiplayer", "preparation"))
    for report in (smoke, pair):
        require(report["acceptanceContext"] == context, "report belongs to different execution inputs")
        require(report["client"] == files["native/horizon-client.exe"], "report has another EXE")
    require(smoke["bootstrap"] == files["native/launch-rust-edition.exe"], "bootstrap was not tested")
    require(smoke["windowTitle"] == name, "wrong window title")
    require({w["id"] for w in smoke["modsPage"]["widgets"]}
            == {"browse-mods", "open-mod-folder", "done"}, "Mods inventory differs")
    before, after = smoke["firstState"], smoke["reopenedState"]
    require(all(before[key] == after[key] for key in ("dimension", "gamemode")), "world state did not survive reopening")
    a, b = before["player"]["position"], after["player"]["position"]
    require(len(a) == len(b) == 3 and all(abs(x-y) <= 1 for x, y in zip(a, b)), "saved position differs")
    require(smoke["cacheReuse"]["secondLaunchReused"] is True, "warm bootstrap not checked")
    require(preparation["status"] == "PASS" and preparation["narration"] == "off"
            and preparation["resourceFiles"] > 0 and preparation["selfContainedInputs"] is True,
            "cold autonomous preparation/narration not checked")
    for key, path in (("bootstrap", "native/launch-rust-edition.exe"),
                      ("preparer", "native/horizon-prepare.exe"), ("researchLock", "native/research-lock.json")):
        require(preparation[key] == files[path], "stale preparation input: " + path)
    require(smoke["cacheReuse"]["receipt"] == preparation["resourceReceipt"], "cold/warm resource receipts differ")
    require(pair["status"] == "PASS", "multiplayer report failed/incomplete")
    require(pair["transport"] == "Windows loopback; real Horizon server; guest T/type/Enter input",
            "guest commands were not tested through chat input")
    for key, allowed, mode in (("commandsAllowed", True, "creative"), ("commandsDenied", False, "survival")):
        case = pair[key]
        require(case["result"] == "PASS" and case["hostAllowsCommands"] is allowed
                and case["guestFinalMode"] == mode, "guest permission scenario incomplete: " + key)
        require(case["pauseMenu"]["loopback"] == "PASS"
                and case["pauseMenu"]["returnedToGame"] is True, "one-click publication not verified")
    pause = smoke["pauseMenu"]
    require(pause["loopback"] == "PASS" and pause["returnedToGame"] is True
            and (pause["before"], pause["after"]) == ("Открыть для сети", "Жалобы"), "Russian pause menu not verified")
    servers = record.get("serverList")
    require(isinstance(servers, dict) and servers.get("status") == "PASS",
            "ordinary-startup server-list QA missing")
    require(servers.get("acceptanceContext") == context, "server-list report has another payload")
    require(servers.get("startup") == "ordinary client; Agent Control enabled only in technical extras",
            "public-server policy was not tested through ordinary startup")
    require(all(servers.get("checks", {}).get(key) == "PASS" for key in (
                "cleanFirstLaunchAndRestart", "manualEntriesAndFieldsRetainedAcrossRestart",
                "exactCandidateUnchanged", "steamDefaultAndExplicitSavedView",
                "hiddenSavedSelectionCannotJoinEditOrDelete", "manualEditorAndDirectInputAccessible",
                "titleReentryDefaultsToSteam", "legacyUnknownNbtPreservedByteForByte")),
            "public-server policy or manual servers not verified")
    require(servers.get("fixtureRemoved") is True, "server-list fixture was not removed")
    runs = servers.get("runs", [])
    harness = ("--control", "--control-socket", "--frames", "--benchmark", "--offscreen")
    require(len(runs) == 6 and all(run.get("exitCode") == 0 and run.get("arguments")
                and not any(argument == flag or argument.startswith(flag + "=")
                            for argument in run["arguments"] for flag in harness)
                for run in runs), "server-list report used harness startup or incomplete runs")
    verify_native_notices(record.get("nativeNotices"), files, context, name)
    # Steam deferral cannot waive any of the required checks above.
    steam = record["liveSteam"]
    require(steam["status"] in ("PASS", "NOT RUN"), "unknown live Steam status")
    if steam["status"] == "NOT RUN":
        require(bool(steam["userDecision"].strip()), "live Steam deferral has no explicit user decision")
    else:
        require(steam["acceptanceContext"] == context, "live Steam report has another payload")
        require(all(steam["checks"][key] == "PASS" for key in ("shiftTab", "invitation", "twoAccountJoin")),
                "live Steam scenarios incomplete")
    return context


def verify_update(record, manifest):
    report = record.get("update")
    require(isinstance(report, dict) and report.get("status") == "PASS", "exact archive update QA missing")
    runtime = next(a for a in manifest["assets"] if "native/horizon-client.exe" in a["covers"])
    require(report["newAssetSha256"] == runtime["sha256"], "update report tested another runtime archive")
    require(canonical_sha(report["targetFiles"]) == record["context"]["payloadInventorySha256"],
            "update report tested another managed-file inventory/preset")
    require(report["managedFileCount"] == len(manifest["files"]), "update inventory differs")
    for key in ("transferFallback", "persistentPendingAndOfflineRefusal", "retry", "idempotence"):
        require(report[key] == "PASS", "update/recovery scenario incomplete: " + key)
    require(report["fixtureRemoved"] is True and report["personalSentinelsPreserved"] >= 7,
            "personal settings/manual servers/progress preservation not verified")
    require(report["repeatPayloadBytes"] == 0, "identical retry downloaded data")


def read_record():
    if not ACCEPTANCE.is_file():
        raise ValueError("Release acceptance record is missing; run Windows QA before packaging")
    return read_json(ACCEPTANCE)


def git_json(revision, path):
    return json.loads(subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--smoke", required=True, type=Path)
    parser.add_argument("--multiplayer", required=True, type=Path)
    parser.add_argument("--servers", required=True, type=Path,
                        help="Six ordinary launches proving Steam-first views and saved-list preservation")
    parser.add_argument("--native-notices", required=True, type=Path,
                        help="Exact bootstrap child, final UI pixel/heartbeat/fullscreen/input acceptance")
    parser.add_argument("--preparation", type=Path, help="Separate cold proof, or coldPreparation in the smoke report")
    parser.add_argument("--update", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--import-reviewed", metavar="GIT_COMMIT")
    parser.add_argument("--defer-live-steam", metavar="USER_DECISION")
    parser.add_argument("--live-steam-report", type=Path)
    args = parser.parse_args()
    record = {"schemaVersion": 1, "context": execution_context(args.candidate),
              **{key: read_json(getattr(args, key)) for key in ("smoke", "multiplayer")},
              "serverList": read_json(args.servers),
              "nativeNotices": read_json(args.native_notices),
              "update": read_json(args.update) if args.update else None,
              "liveSteam": {"status": "NOT RUN", "userDecision": args.defer_live_steam or ""}}
    record["preparation"] = read_json(args.preparation) if args.preparation else record["smoke"]["coldPreparation"]
    if args.live_steam_report:
        require(not args.defer_live_steam, "select a live report or user deferral")
        record["liveSteam"] = read_json(args.live_steam_report)
    if args.import_reviewed:
        require(re.fullmatch(r"[0-9a-f]{40}", args.import_reviewed) is not None, "expected an exact reviewed Git commit")
        # Historical evidence has no automatic context field. It can be imported
        # only for the EXACT already-reviewed Git inventory, never a new build.
        old_files = git_json(args.import_reviewed, "tools/distribution-lock.json")["files"]
        old_receipt = git_json(args.import_reviewed, "tools/overlay-receipt.json")
        require(context_from_files(old_files, old_receipt) == record["context"], "reviewed snapshot differs from candidate")
        old_qa = git_json(args.import_reviewed, "tools/qa-results.json")
        require(old_qa["client"] == old_files["native/horizon-client.exe"]
                and old_qa["checks"]["exactFinalClientSaveReopen"] == "PASS"
                and old_qa["checks"]["guestCommandsViaTTypeEnterAllowed"] == "PASS"
                and old_qa["checks"]["guestCommandsDeniedWithoutCheats"] == "PASS", "snapshot was not accepted")
        require(old_qa["checks"]["autonomousColdPreparation"] == "PASS"
                and old_qa["preparedReceipt"] == record["preparation"]["resourceReceipt"]
                and old_qa["guestCommands"]["allowed"] == record["multiplayer"]["commandsAllowed"]
                and old_qa["guestCommands"]["denied"] == record["multiplayer"]["commandsDenied"],
                "historical evidence differs from the reviewed record")
        for key in ("smoke", "multiplayer"):
            record[key]["acceptanceContext"] = record["context"]
        record["preparation"]["selfContainedInputs"] = True
        if record["update"] is not None:
            require(old_qa["archiveAcceptance"]["runtimeArchiveSha256"] == record["update"]["newAssetSha256"]
                    and record["update"]["managedFileCount"] == len(old_files), "historical update differs")
            record["update"]["targetFiles"] = old_files
        record["provenance"] = {"kind": "imported historical reports; no new game run", "reviewedCommit": args.import_reviewed}
    from generate_manifest import inventory
    files = inventory(args.candidate)
    verify_startup(record, files, read_json(args.candidate / "overlay-receipt.json"),
                   read_json(args.candidate / "native/presentation.json")["name"])
    args.out.write_text(json.dumps(record, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
