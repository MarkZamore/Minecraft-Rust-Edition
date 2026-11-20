"""Compare composed native mixin targets with the exact SDK function index."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import tomllib


LIFETIMES = re.compile(r"<\s*'(?:_|[A-Za-z][A-Za-z0-9_]*)(?:\s*,\s*'(?:_|[A-Za-z][A-Za-z0-9_]*))*\s*,?\s*>")
TEMPERATURE_METHOD = "horizon_world::worldgen::biome_temperature::BiomeTemperatureSettings::height_adjusted"
TEMPERATURE_SLOT = "horizon_world::worldgen::biome_temperature::height_adjusted"


def key(target: str) -> str:
    # Current lower-crate rows use ordinary paths and lifetime parameters.
    # Trait-qualified and generic targets require Horizon's own normalizer.
    result = LIFETIMES.sub("", target)
    if "<" in result or ">" in result or not re.fullmatch(r"[A-Za-z0-9_:]+", result):
        raise ValueError(f"Use the Horizon patch compiler for target {target!r}")
    return result


def inspect_slots(bundles: Path, index: dict | None = None) -> dict:
    rows = []
    kinds = Counter()
    owners = set()
    for manifest in sorted(bundles.glob("*/horizon-mixin.toml")):
        package = tomllib.loads(manifest.read_text(encoding="utf-8"))
        owner = package["mod"]["id"]
        if owner in owners:
            raise ValueError(f"Repeated mixin manifest for {owner}")
        owners.add(owner)
        for injection in package.get("injection", []):
            kind = injection["kind"]
            kinds[kind] += 1
            target = injection.get("target")
            if not target:
                raise ValueError(f"{owner}:{injection['id']} still needs its named hook resolved")
            if target.startswith("horizon_client::"):
                continue
            sdk_target = target
            if (owner, injection["id"], target) == ("tectonic_horizon", "snow-height", TEMPERATURE_METHOD):
                sdk_target = TEMPERATURE_SLOT
            normalized = key(sdk_target)
            row = {"mod": owner, "id": injection["id"], "kind": kind, "target": target,
                   "function": normalized, "status": "UNVERIFIED"}
            if index is not None:
                if refusal := index["refused"].get(normalized):
                    row.update(status="REFUSED", refusal=refusal)
                elif function := index["functions"].get(normalized):
                    row.update(status="AVAILABLE", source=function["file"],
                               signatureSha256=function["signature_hash"])
                else:
                    row["status"] = "MISSING"
            rows.append(row)
    if not owners or not kinds:
        raise ValueError("No prepared native mixin manifests found")
    return {"buildId": index["build"]["commit"] if index is not None else None,
            "bundles": len(owners), "injections": dict(sorted(kinds.items())),
            "lowerCrateRequirements": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", type=Path, required=True)
    parser.add_argument("--functions", type=Path)
    parser.add_argument("--build-id", help="Require the exact SDK ABI identity in functions.json")
    parser.add_argument("--require", action="store_true")
    args = parser.parse_args()
    if (args.require or args.build_id) and args.functions is None:
        parser.error("--require and --build-id need --functions")
    index = json.loads(args.functions.read_bytes()) if args.functions else None
    report = inspect_slots(args.bundles, index)
    if args.build_id and report["buildId"] != args.build_id:
        raise ValueError("Function index belongs to another shared SDK")
    print(json.dumps(report, indent=2))
    if args.require and any(row["status"] != "AVAILABLE" for row in report["lowerCrateRequirements"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
