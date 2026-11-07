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
                "exactCandidateUnchanged")), "public-server policy or manual servers not verified")
    require(servers.get("fixtureRemoved") is True, "server-list fixture was not removed")
    runs = servers.get("runs", [])
    harness = ("--control", "--control-socket", "--frames", "--benchmark", "--offscreen")
    require(len(runs) == 4 and all(run.get("exitCode") == 0 and run.get("arguments")
                and not any(argument == flag or argument.startswith(flag + "=")
                            for argument in run["arguments"] for flag in harness)
                for run in runs), "server-list report used harness startup or incomplete runs")
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
                        help="Four ordinary launches proving no public seeding and manual-list preservation")
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
