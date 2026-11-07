"""Receipt-to-independent-source-proof publication boundary."""

import copy
import unittest

from check_required_layers import PROOF, verify_proof
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


if __name__ == "__main__":
    unittest.main()
