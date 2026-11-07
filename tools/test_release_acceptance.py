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


if __name__ == "__main__":
    unittest.main()
