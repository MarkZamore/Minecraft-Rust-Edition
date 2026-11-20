import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from apply import REVISION, SOURCE_SHA256, changes, sha
from build_sdk import HOST, NATIVE_TEMPLATE, export_patch_source, seam_overrides
from lower_slots import expected_targets, require_exports, temperature_delegate
from profile import make_record, publish
from slots import inspect_slots


class SharedNativeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sdk = self.root / "sdk"
        self.native = self.root / "pack/native"
        self.native.mkdir(parents=True)
        exe = self.sdk / "bin/horizon-client.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"shared client")
        self.abi = "a" * 40
        self.rustc = f"host: {HOST}\nrelease: 1.99.1"
        client_sha = sha(exe.read_bytes())
        sdk = {"commit": self.abi, "rustc": self.rustc, "target": HOST, "side": "client",
               "files": {"bin/horizon-client.exe": client_sha},
               "slices": {"runtime": ["bin/horizon-client.exe", "deps/horizon_mod_host.dll"]}}
        raw = json.dumps(sdk).encode("utf-8")
        (self.sdk / "horizon-prebuilt.json").write_bytes(raw)
        self.permit = {"horizon": REVISION, "rustc": self.rustc, "abiBuildId": self.abi,
                       "sdkIdentitySha256": sha(raw), "clientSha256": client_sha}

    def dll(self, mod, *, abi=None, kind="mod"):
        library = self.root / f"{mod}.dll"
        library.write_bytes(f"test {mod}".encode("utf-8"))
        stamp = {"file": library.name, "kind": kind, "mod": mod, "sha256": "",
                 "identity": f"{abi or self.abi} {mod} 1.0 1.99.1"}
        library.with_suffix(".identity.json").write_text(json.dumps(stamp), encoding="utf-8")
        if kind == "mod":
            manifest = self.native / "horizon-native-mods" / mod / "horizon-mod.toml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(f'id = "{mod}"\nversion = "1.0"\n[native]\nhorizon = "{self.abi}"\n',
                                encoding="utf-8")
        return library

    def test_empty_edition_profile_binds_the_same_sdk(self):
        record, rows = make_record(self.sdk, self.permit, self.native, [])
        publish(self.native, record, rows)
        self.assertEqual(record["abiBuildId"], self.abi)
        self.assertEqual(record["libraries"], [])
        self.assertEqual(sorted(p.name for p in self.native.iterdir()), ["native-mods.json"])

    def test_profile_copies_only_pack_dlls_in_dependency_and_patch_order(self):
        dependency = self.dll("dependency")
        dependent = self.dll("dependent")
        patch = self.dll("horizon_patch", kind="patch")
        record, rows = make_record(self.sdk, self.permit, self.native, [patch, dependency, dependent])
        publish(self.native, record, rows)
        self.assertEqual([row["mod"] for row in record["libraries"]],
                         ["dependency", "dependent", "horizon_patch"])
        for library in (dependency, dependent, patch):
            self.assertEqual((self.native / library.name).read_bytes(), library.read_bytes())
        self.assertFalse((self.native / "horizon-client.exe").exists())

    def test_profile_refuses_foreign_abi_and_replaced_client(self):
        alien = self.dll("alien", abi="b" * 40)
        with self.assertRaisesRegex(ValueError, "library identity"):
            make_record(self.sdk, self.permit, self.native, [alien])
        (self.sdk / "bin/horizon-client.exe").write_bytes(b"different client")
        with self.assertRaisesRegex(ValueError, "shared client"):
            make_record(self.sdk, self.permit, self.native, [])

    def test_slot_report_normalizes_lifetimes_and_preserves_refusals(self):
        root = self.root / "bundles"
        bundle = root / "sample"
        bundle.mkdir(parents=True)
        target = "horizon_client_render::Mesher::work"
        manifest = bundle / "horizon-mixin.toml"
        manifest.write_text("""[mod]
id = "sample"

[[injection]]
id = "slot"
kind = "HEAD"
target = "horizon_client_render::Mesher<'_>::work"
""", encoding="utf-8")
        index = {"build": {"commit": self.abi}, "functions": {}, "refused": {}}
        report = inspect_slots(root, index)
        row = report["lowerCrateRequirements"][0]
        self.assertEqual((row["function"], row["status"]), (target, "MISSING"))
        index["refused"][target] = {"code": "E_MIXIN_NOT_REPLACEABLE", "why": "unmarked"}
        self.assertEqual(inspect_slots(root, index)["lowerCrateRequirements"][0]["status"], "REFUSED")
        del index["refused"][target]
        index["functions"][target] = {"file": "mesh.rs", "signature_hash": "c" * 64}
        row = inspect_slots(root, index)["lowerCrateRequirements"][0]
        self.assertEqual((row["source"], row["status"]), ("mesh.rs", "AVAILABLE"))

    def test_recipe_refuses_unpinned_source_before_transforming(self):
        with self.assertRaisesRegex(ValueError, "differs from Horizon"):
            changes({name: b"modified" for name in SOURCE_SHA256})

    def test_seams_bind_both_input_and_output_bytes(self):
        neutral = self.root / "neutral"
        path = neutral / "files" / "core.rs"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"neutral slot")
        descriptor = neutral / "seams.json"
        spec = {"schemaVersion": 1, "horizon": REVISION, "files": {
            "core.rs": {"beforeSha256": sha(b"base"), "sha256": sha(path.read_bytes())},
        }}
        descriptor.write_text(json.dumps(spec), encoding="utf-8")
        self.assertEqual(seam_overrides(self.root, {"core.rs": b"base"}, descriptor),
                         {"core.rs": b"neutral slot"})
        with self.assertRaisesRegex(ValueError, "input changed"):
            seam_overrides(self.root, {"core.rs": b"next"}, descriptor)
        path.write_bytes(b"changed slot")
        with self.assertRaisesRegex(ValueError, "output changed"):
            seam_overrides(self.root, {"core.rs": b"base"}, descriptor)

    def test_lower_slot_exports_bind_build_and_source_spans(self):
        path = self.sdk / "functions.json"
        targets = expected_targets()
        self.assertEqual(len(targets), 26)
        sources = {source: b"#[replaceable]\nfn source() {}\n" for source in targets.values()}
        table = {"build": {"commit": self.abi}, "refused": {}, "functions": {
            target: {"file": source, "span": [0, len(sources[source])], "sha256": sha(sources[source])}
            for target, source in targets.items()
        }}
        path.write_text(json.dumps(table), encoding="utf-8")
        require_exports(path, self.abi, sources)
        with self.assertRaisesRegex(ValueError, "another SDK build"):
            require_exports(path, "b" * 40, sources)
        target, source = next(iter(targets.items()))
        sources[source] += b"// material outside indexed span"
        require_exports(path, self.abi, sources)
        sources[source] = b" " + sources[source]
        with self.assertRaisesRegex(ValueError, "source span differs"):
            require_exports(path, self.abi, sources)
        table["refused"][target] = {"why": "unmarked"}
        path.write_text(json.dumps(table), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unmarked"):
            require_exports(path, self.abi, sources)

    def test_temperature_delegate_preserves_public_receiver_and_legacy_store(self):
        data = b"""impl BiomeTemperatureSettings {
    pub fn height_adjusted(self, position: BlockPos, sea_level: i32) -> f32 {
        let base = self.modifier.modify(position, self.base_temperature);
        let start = sea_level.wrapping_add(17);
        base - start as f32
    }
}
"""
        self.assertEqual(temperature_delegate(data), b"""impl BiomeTemperatureSettings {
    pub fn height_adjusted(self, position: BlockPos, sea_level: i32) -> f32 {
        height_adjusted(self, position, sea_level)
    }
}

fn height_adjusted(settings: BiomeTemperatureSettings, position: BlockPos, sea_level: i32) -> f32 {
    let base = settings.modifier.modify(position, settings.base_temperature);
    let start = sea_level.wrapping_add(17);
    base - start as f32
}
""")

    def test_patch_source_projects_exact_sdk_files_and_removes_failed_output(self):
        catalog = self.sdk / "functions.json"
        target = "crates/horizon-client/src/game.rs"
        source_bytes = {target: b"original module", NATIVE_TEMPLATE: b"author template"}
        catalog.write_text(json.dumps({"build": {"commit": self.abi}, "crates": {
            "horizon_client": {"modules": {"game": [
                {"path": target, "sha256": sha(source_bytes[target])}
            ]}}
        }}), encoding="utf-8")
        provider = types.ModuleType("signed_source")
        provider.tracked_blobs = lambda _, build, paths: (
            (name, source_bytes[name]) for name in paths if build == self.abi
        )
        destination = self.root / "patch-source"
        with patch("build_sdk.NATIVE_TEMPLATE_SHA256", sha(source_bytes[NATIVE_TEMPLATE])):
            self.assertEqual(export_patch_source(self.root, provider, self.abi, catalog, destination), 2)
            self.assertEqual((destination / target).read_bytes(), source_bytes[target])
            self.assertEqual((destination / NATIVE_TEMPLATE).read_bytes(), source_bytes[NATIVE_TEMPLATE])
            source_bytes[target] = b"staged module"
            failed = self.root / "failed"
            with self.assertRaisesRegex(ValueError, "bytes differ"):
                export_patch_source(self.root, provider, self.abi, catalog, failed)
            self.assertFalse(failed.exists())


if __name__ == "__main__":
    unittest.main()
