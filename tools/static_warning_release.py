"""Static release authorization for the exact November 16 menu-only update."""

import json
import subprocess

from release_acceptance import verify_startup, verify_update
from stage_runtime import ROOT, file_info


BASE = "34d18e6956912ba5412e17eefdd5889f814e6cee"
BASE_MANIFEST_SHA = "c6da36d897ade557ad8027192484f16f9f2e870367869fe21df2dc7a2ba30495"
RECIPE_SHA = "3136e8496a553cc3ab98637bae70482dff7ee64ed797665069ab81f7bdd8b510"
CLIENT_SHA = "70ca11bd2beace848ebaadb88c31fbcec9d1284c687e55af24fd83ec00a5813b"
CLIENT = "native/horizon-client.exe"
TITLE = "crates/horizon-client/src/title.rs"
TITLE_TESTS = "crates/horizon-client/src/title/tests.rs"


def reviewed_base(path):
    return json.loads(subprocess.check_output(
        ["git", "show", f"{BASE}:{path}"], cwd=ROOT,
    ))


def verify_warning_release(manifest_path, files, receipt, name):
    if file_info(manifest_path)["sha256"] != BASE_MANIFEST_SHA:
        raise ValueError("Static warning release requires the exact published predecessor")
    manifest = json.loads(manifest_path.read_bytes())
    published = {row["path"]: {"sizeBytes": row["sizeBytes"], "sha256": row["sha256"]}
                 for row in manifest["files"]}
    accepted = reviewed_base("tools/release-acceptance.json")
    base_receipt = reviewed_base("tools/overlay-receipt.json")
    verify_startup(accepted, published, base_receipt, name)
    verify_update(accepted, manifest)

    if (files.keys() != published.keys()
            or {path for path in files if files[path] != published[path]} != {CLIENT}
            or files[CLIENT]["sha256"] != CLIENT_SHA
            or receipt["recipeSha256"] != RECIPE_SHA):
        raise ValueError("Static warning release permits only the approved client replacement")

    before = base_receipt["patchedFiles"]
    after = receipt["patchedFiles"]
    if (after.keys() - before.keys() != {TITLE_TESTS}
            or before.keys() - after.keys()
            or {path for path in before if before[path] != after[path]} != {TITLE}):
        raise ValueError("Static warning release permits only the title action and its test")
    changed = {"recipeSha256", "patchedFiles", "clientSha256", "clientSizeBytes"}
    if ({key: value for key, value in receipt.items() if key not in changed}
            != {key: value for key, value in base_receipt.items() if key not in changed}):
        raise ValueError("Static warning release requires all other build inputs to match")
