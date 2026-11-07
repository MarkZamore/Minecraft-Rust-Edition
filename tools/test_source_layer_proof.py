"""Receipt-to-independent-source-proof publication boundary."""

import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from check_required_layers import PROOF, verify_proof
import stage_runtime
from stage_runtime import ROOT, read_json


class SourceLayerProofTests(unittest.TestCase):
    def test_current_exact_receipt_is_covered(self):
        verify_proof(read_json(ROOT / "tools/overlay-receipt.json"))

    def test_stale_hash_in_a_still_listed_file_cannot_pass(self):
        receipt = read_json(ROOT / "tools/overlay-receipt.json")
        receipt["patchedFiles"]["crates/horizon-client/src/game.rs"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "patchedFiles"):
            verify_proof(receipt)

    def test_removed_required_layer_or_changed_policy_needs_a_new_proof(self):
        for mutation in ("layer", "policy"):
            with self.subTest(mutation=mutation):
                proof = copy.deepcopy(read_json(PROOF))
                if mutation == "layer": proof["requiredLayers"].pop()
                else: proof["policySha256"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "policy or required layer"):
                    verify_proof(read_json(ROOT / "tools/overlay-receipt.json"), proof)

    def test_stage_verifies_canonical_recipe_independent_of_file_enumeration(self):
        receipt = read_json(ROOT / "tools/overlay-receipt.json")
        lock = read_json(stage_runtime.LOCK)
        client, steam_dll = Path("unused-client.exe"), Path("unused-steam.dll")
        original_info = stage_runtime.file_info

        def changed_recipe(path):
            info = original_info(path)
            if path == ROOT / "integration/steam/apply_horizon_overlay.py":
                return {**info, "sha256": "0" * 64}
            return info

        # The receipt comes from the real producer's sorted tree index. Binary
        # checks are unrelated to this boundary and need no runtime fixture.
        with patch.object(stage_runtime, "RECIPE_FILES", tuple(reversed(stage_runtime.RECIPE_FILES))), \
                patch.object(stage_runtime, "verify_file") as verify_file, \
                patch.object(stage_runtime, "verify_pe") as verify_pe:
            stage_runtime.verify_overlay(lock, receipt, client, steam_dll)
            self.assertEqual(verify_file.call_count, 3)
            self.assertEqual(verify_pe.call_count, 2)
            with patch.object(stage_runtime, "file_info", side_effect=changed_recipe):
                with self.assertRaisesRegex(ValueError, "current source and recipe"):
                    stage_runtime.verify_overlay(lock, receipt, client, steam_dll)
            self.assertEqual(verify_file.call_count, 3, "Changed recipe reached binary verification")


if __name__ == "__main__":
    unittest.main()
