"""Publication boundaries and stale-evidence fault injection; no game or Cargo."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import generate_manifest as packer
import release_acceptance as gate
from stage_runtime import ROOT, read_json


class ReleaseAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.record = read_json(ROOT / "tools/release-acceptance.json")
        self.distribution = read_json(ROOT / "tools/distribution-lock.json")
        self.files = self.distribution["files"]
        self.receipt = read_json(ROOT / "tools/overlay-receipt.json")
        self.content = read_json(ROOT / "library/content.json")
        self.manifest = {"files": list(self.files), "assets": [{
            "name": "runtime.zip", "sha256": self.record["update"]["newAssetSha256"],
            "covers": list(self.files), "sizeBytes": self.record["update"]["retryPayloadBytes"],
        }]}

    def verify(self):
        gate.verify_startup(self.record, self.files, self.receipt, self.content["name"])

    def test_reviewed_exact_payload_and_archive_acceptance(self):
        self.verify()
        gate.verify_update(self.record, self.manifest)

    def test_changed_exe_bootstrap_dll_preparer_preset_and_descriptor_reject_old_qa(self):
        for name in ("native/horizon-client.exe", "native/launch-rust-edition.exe",
                     "native/steam_api64.dll", "native/horizon-prepare.exe",
                     "native/research-lock.json", "launcher/controls-preset.txt", "portable-pack.json"):
            with self.subTest(path=name):
                files = copy.deepcopy(self.files)
                files[name]["sha256"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "stale"):
                    gate.verify_startup(self.record, files, self.receipt, self.content["name"])

    def test_source_pin_and_recipe_changes_reject_old_qa_even_with_same_exe(self):
        for key in ("upstreamCommit", "steamTransportCommit", "recipeSha256"):
            with self.subTest(key=key):
                receipt = copy.deepcopy(self.receipt)
                receipt[key] = "0" * len(receipt[key])
                with self.assertRaisesRegex(ValueError, "stale"):
                    gate.verify_startup(self.record, self.files, receipt, self.content["name"])

    def test_not_run_steam_never_waives_required_multiplayer_cases(self):
        for key in ("commandsAllowed", "commandsDenied"):
            with self.subTest(key=key):
                record = copy.deepcopy(self.record)
                record["multiplayer"][key]["result"] = "NOT RUN"
                with self.assertRaisesRegex(ValueError, "permission scenario"):
                    gate.verify_startup(record, self.files, self.receipt, self.content["name"])

    def test_world_and_pause_regressions_are_rejected_despite_pass_summary(self):
        for mutation in ("position", "mode", "pause"):
            with self.subTest(mutation=mutation):
                record = copy.deepcopy(self.record)
                if mutation == "position": record["smoke"]["reopenedState"]["player"]["position"][0] += 20
                elif mutation == "mode": record["smoke"]["reopenedState"]["gamemode"] = "survival"
                else: record["smoke"]["pauseMenu"]["returnedToGame"] = False
                with self.assertRaises(ValueError):
                    gate.verify_startup(record, self.files, self.receipt, self.content["name"])

    def test_deferral_requires_user_decision(self):
        self.record["liveSteam"]["userDecision"] = ""
        with self.assertRaisesRegex(ValueError, "user decision"):
            self.verify()

    def test_cold_preparation_and_current_inputs_are_required(self):
        for mutation in ("cold", "preparer"):
            with self.subTest(mutation=mutation):
                record = copy.deepcopy(self.record)
                if mutation == "cold": record["preparation"]["selfContainedInputs"] = False
                else: record["preparation"]["preparer"]["sha256"] = "0" * 64
                with self.assertRaises(ValueError):
                    gate.verify_startup(record, self.files, self.receipt, self.content["name"])

    def test_public_server_policy_requires_fresh_ordinary_startup_and_manual_preservation(self):
        for mutation in ("absent", "old-payload", "manual", "harness", "incomplete"):
            with self.subTest(mutation=mutation):
                record = copy.deepcopy(self.record)
                if mutation == "absent": record.pop("serverList")
                elif mutation == "old-payload": record["serverList"]["acceptanceContext"] = {}
                elif mutation == "manual":
                    record["serverList"]["checks"]["manualEntriesAndFieldsRetainedAcrossRestart"] = "FAIL"
                elif mutation == "harness":
                    record["serverList"]["runs"][0]["arguments"].append("--control-socket=test.json")
                else: record["serverList"]["runs"].pop()
                with self.assertRaises(ValueError):
                    gate.verify_startup(record, self.files, self.receipt, self.content["name"])

    def test_update_report_cannot_accept_another_archive_or_incomplete_recovery(self):
        for key, value in (("newAssetSha256", "0" * 64), ("retry", "FAIL"),
                           ("personalSentinelsPreserved", 0), ("fixtureRemoved", False)):
            with self.subTest(key=key):
                record = copy.deepcopy(self.record)
                record["update"][key] = value
                with self.assertRaises(ValueError): gate.verify_update(record, self.manifest)

    def test_same_runtime_zip_cannot_reuse_update_qa_for_another_preset(self):
        self.record["update"]["targetFiles"]["launcher/controls-preset.txt"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "inventory/preset"):
            gate.verify_update(self.record, self.manifest)

    def test_absent_acceptance_record_is_not_an_optional_check(self):
        with patch.object(gate, "ACCEPTANCE", Path("nonexistent-acceptance.json")):
            with self.assertRaises(ValueError): gate.read_record()

    def test_metadata_only_description_does_not_invalidate_payload_acceptance(self):
        self.content["description"] = "Updated library prose"
        self.verify()

    def test_qa_phase_cannot_emit_a_publishable_manifest(self):
        record = copy.deepcopy(self.record)
        record["update"] = None
        tool = Mock()
        tool.stage_library.return_value = {"name": "library.zip"}
        def archive(out, source, covers, prefix):
            return {"name": prefix + ".zip", "sha256": self.manifest["assets"][0]["sha256"],
                    "sizeBytes": 1, "covers": covers}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(packer, "describe_candidate", return_value=(read_json(ROOT / "portable-pack.json"), self.content, self.distribution)), \
                patch.object(packer, "zip_payload", side_effect=archive), \
                patch.object(gate, "read_record", return_value=record):
            candidate = Path(temp) / "candidate"
            (candidate / "library").mkdir(parents=True)
            (candidate / "overlay-receipt.json").write_text(json.dumps(self.receipt), encoding="utf-8")
            (candidate / "library/loaded-mods.json").write_text('{"mods": []}', encoding="utf-8")
            out = Path(temp) / "qa"
            packer.stage_release(candidate, out, "a" * 40, tool, prepare_qa=True)
            self.assertTrue((out / "candidate-manifest.json").is_file())
            self.assertFalse((out / "pack-manifest.json").exists())
            production = Path(temp) / "production"
            with self.assertRaisesRegex(ValueError, "archive update QA missing"):
                packer.stage_release(candidate, production, "a" * 40, tool)
            self.assertFalse((production / "pack-manifest.json").exists())


class NativeNoticeEvidenceTests(unittest.TestCase):
    """Synthetic gate fault injection only; this never records a real runtime PASS."""

    def setUp(self):
        self.files = {"native/horizon-client.exe": {"sha256": "1"*64, "sizeBytes": 10},
                      "native/launch-rust-edition.exe": {"sha256": "2"*64, "sizeBytes": 20}}
        self.context = {"unitFixture": "not actual runtime evidence"}
        self.report = {
            "schemaVersion": 1, "status": "PASS", "acceptanceContext": self.context,
            "client": self.files["native/horizon-client.exe"],
            "bootstrap": self.files["native/launch-rust-edition.exe"],
            "startup": "exact bootstrap child; native-ui-v1; real presented unscaled screenshots",
            "fixtureRemoved": True, "checks": {key: "PASS" for key in gate.NATIVE_NOTICE_CHECKS},
            "nativeProcessId": 11, "bootstrapProcessId": 10,
            "processAncestry": [{"pid": 11, "parent": 10, "path": "C:/qa/native/horizon-client.exe"},
                                {"pid": 10, "parent": 9, "path": "C:/qa/native/launch-rust-edition.exe"}],
            "pauseUiBefore": {"screen": "paused", "widgets": [{"id": "return"}]},
            "pauseUiAfter": {"screen": "paused", "widgets": [{"id": "return"}]},
            "foregroundBeforeChat": 12, "foregroundAfterChat": 12,
            "technicalChat": ["1 player online"], "captures": [],
        }
        for filename, count in gate.NATIVE_NOTICE_CAPTURES.items():
            def beat(stamp):
                return {"backend": "native-ui-v1", "pid": 11, "timestampUnixMs": stamp,
                        "renderedCount": count, "transformations": 0, "error": ""}
            self.report["captures"].append({
                "file": filename, "sha256": "3"*64, "sizeBytes": 100,
                "pixels": {"count": count, "boxes": [[12, 100+i*110, 252, 200+i*110]
                            for i in range(count)], "size": [800, 600], "expectedPhysicalWidth": 240},
                "window": {"hwnd": 12, "title": "Unit pack", "client": [0, 0, 800, 600],
                           "monitor": [0, 0, 800, 600]},
                "capturedUnixMs": 1150, "heartbeatBeforeObservedUnixMs": 1100,
                "heartbeatAfterObservedUnixMs": 1250,
                "heartbeatBefore": beat(1000), "heartbeatAfter": beat(1200),
            })

    def verify(self, report):
        gate.verify_native_notices(report, self.files, self.context, "Unit pack")

    def test_complete_structural_fixture_is_accepted(self):
        self.verify(self.report)

    def test_stale_missing_foreign_unpresented_or_input_changing_evidence_is_rejected(self):
        mutations = {
            "missing": lambda r: r.clear(),
            "not-run": lambda r: r.update(status="NOT RUN"),
            "stale-payload": lambda r: r.update(acceptanceContext={}),
            "other-client": lambda r: r.update(client={}),
            "fixture-left": lambda r: r.update(fixtureRemoved=False),
            "root-is-not-child": lambda r: r.update(nativeProcessId=10),
            "foreign-child": lambda r: r["processAncestry"][0].update(parent=99),
            "no-png": lambda r: r["captures"][0].update(sha256=""),
            "capture-missing": lambda r: r["captures"].pop(),
            "wrong-pixels": lambda r: r["captures"][0]["pixels"].update(count=0),
            "foreign-heartbeat": lambda r: r["captures"][0]["heartbeatAfter"].update(pid=99),
            "not-native": lambda r: r["captures"][0]["heartbeatAfter"].update(backend="lwjgl3"),
            "stale-heartbeat": lambda r: r["captures"][0].update(heartbeatAfterObservedUnixMs=9999),
            "no-new-present": lambda r: r["captures"][0]["heartbeatAfter"].update(timestampUnixMs=1000),
            "renderer-error": lambda r: r["captures"][0]["heartbeatAfter"].update(error="failed"),
            "transformer-used": lambda r: r["captures"][0]["heartbeatAfter"].update(transformations=1),
            "fullscreen-not-run": lambda r: r["checks"].update(actualFullscreenVisibleNotice="NOT RUN"),
            "windowed-proof": lambda r: next(c for c in r["captures"] if c["file"] == "fullscreen-world.png")["window"].update(monitor=[0, 0, 1920, 1080]),
            "ui-captured-input": lambda r: r["pauseUiAfter"]["widgets"].append({"id": "notice"}),
            "focus-changed": lambda r: r.update(foregroundAfterChat=99),
            "no-real-chat": lambda r: r.update(technicalChat=[]),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                report = copy.deepcopy(self.report)
                mutate(report)
                with self.assertRaises(ValueError):
                    self.verify(report)


if __name__ == "__main__":
    unittest.main()
