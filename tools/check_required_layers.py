"""Verify the signed source and mandatory overlay outcomes before building."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys

from stage_runtime import ROOT, SOURCE_ARCHIVE, file_info, read_json, verify_upstream


INTEGRATION = ROOT / "integration/steam"
sys.path.insert(0, str(INTEGRATION))
from required_layers import REQUIRED_LAYER_IDS, validate_patched_sources, validate_receipt_coverage


PROOF = ROOT / "tools/required-layer-proof.json"


def compute_proof(archives, transport):
    spec = importlib.util.spec_from_file_location("edition_overlay", INTEGRATION / "apply_horizon_overlay.py")
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    patched = recipe.changes(recipe.locked_inputs(archives / SOURCE_ARCHIVE), transport)
    validate_patched_sources(patched)
    return {"schemaVersion": 1, "upstreamCommit": recipe.COMMIT,
            "upstreamSourceSha256": recipe.SOURCE_SHA, "recipeSha256": recipe.recipe_sha(),
            "steamTransportCommit": recipe.TRANSPORT_COMMIT,
            "steamTransportSourceSha256": recipe.TRANSPORT_SHA,
            "policySha256": file_info(INTEGRATION / "required_layers.py")["sha256"],
            "requiredLayers": sorted(REQUIRED_LAYER_IDS), "patchedFiles": recipe.index(patched)}


def verify_proof(receipt, proof=None):
    proof = read_json(PROOF) if proof is None else proof
    validate_receipt_coverage(receipt)
    for key in ("schemaVersion", "upstreamCommit", "upstreamSourceSha256", "recipeSha256",
                "steamTransportCommit", "steamTransportSourceSha256", "patchedFiles"):
        if proof[key] != receipt[key]:
            raise ValueError("Mandatory layers: stale source outcome proof: " + key)
    if (proof["policySha256"] != file_info(INTEGRATION / "required_layers.py")["sha256"]
            or proof["requiredLayers"] != sorted(REQUIRED_LAYER_IDS)):
        raise ValueError("Mandatory layers: policy or required layer list changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", required=True, type=Path)
    parser.add_argument("--steam-transport", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--openssl", default="openssl")
    args = parser.parse_args()
    verify_upstream(args.archives, args.openssl, args.out.parent)
    proof = compute_proof(args.archives, args.steam_transport)
    args.out.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
