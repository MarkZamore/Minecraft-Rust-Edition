"""Policy/source/binary drift must block a staged portable runtime."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bootstrap_contract as contract
import check_required_layers as source_layers
import generate_manifest as packer
import stage_runtime as stage


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


class EnvironmentPolicyTests(unittest.TestCase):
    def test_pack_policy_keeps_the_vendor_scoped_layer_opt_out(self):
        self.assertEqual(contract.load_environment_defaults(), {"DISABLE_VULKAN_OW_OVERLAY_LAYER": "1"})

    def test_policy_rejects_ambiguous_types_names_values_and_windows_aliases(self):
        invalid = [None, [], "value", {"": "1"}, {"1NAME": "1"}, {"NAME-WITH-DASH": "1"},
                   {"\u0418\u041c\u042f": "1"}, {"A" * 65: "1"}, {"A": 1}, {"A": True},
                   {"A": None}, {"A": "\0"}, {"A": "\n"}, {"A": "\x7f"}, {"A": "\x85"},
                   {"A": "\ud800"}, {"Option": "1", "OPTION": "0"}]
        for value in invalid:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                contract.validate_environment_defaults(value)

    def test_limits_count_utf8_bytes_and_allow_explicit_empty_strings(self):
        good = {"A" * 64: "", "B": "\u0430" * 512}
        self.assertEqual(contract.validate_environment_defaults(good), good)
        for value in ({"B": "\u0430" * 513}, {"B": "x" * 1025},
                      {f"A{i}": "" for i in range(17)},
                      {f"A{i:015}": "x" * 1009 for i in range(8)}):
            with self.subTest(value=repr(value)[:60]), self.assertRaises(ValueError):
                contract.validate_environment_defaults(value)
        boundary = {f"A{i:015}": "x" * 1008 for i in range(8)}
        self.assertEqual(sum(len(k) + len(v) for k, v in boundary.items()), 8192)
        self.assertEqual(contract.validate_environment_defaults(boundary), boundary)

    def test_source_policy_is_mandatory_and_duplicate_json_members_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                contract.load_environment_defaults(root)
            path = root / contract.ENVIRONMENT_POLICY
            path.parent.mkdir(parents=True)
            path.write_text('{"A":"1","A":"2"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
                contract.load_environment_defaults(root)

    def test_presentation_cannot_omit_change_or_hide_the_declared_policy(self):
        expected = contract.expected_presentation("Minecraft Rust Edition")
        contract.verify_presentation(expected, expected["name"])
        for mutation in ("omit", "value", "name", "extra"):
            value = copy.deepcopy(expected)
            if mutation == "omit": value.pop("environmentDefaults")
            elif mutation == "value": value["environmentDefaults"]["DISABLE_VULKAN_OW_OVERLAY_LAYER"] = "0"
            elif mutation == "name": value["name"] = "Another pack"
            else: value["ignoredEnvironmentDefaults"] = value.pop("environmentDefaults")
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                contract.verify_presentation(value, expected["name"])


class BootstrapLockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "launcher/standalone"
        (source / "src").mkdir(parents=True)
        for name, data in {"Cargo.toml": b"reviewed manifest\n", "Cargo.lock": b"reviewed dependencies\n",
                           "src/main.rs": b"fn main() {}\n"}.items():
            (source / name).write_bytes(data)
        write_json(self.root / contract.ENVIRONMENT_POLICY, {"LAYER_OPT_OUT": "1"})
        self.binary = self.root / contract.BOOTSTRAP_EXECUTABLE
        self.binary.parent.mkdir()
        self.binary.write_bytes(b"reviewed portable image")
        sources = contract.bootstrap_sources(self.root)
        self.lock = {"schemaVersion": 1, "sourceFiles": sources,
                     "sourceSha256": contract.source_fingerprint(sources),
                     "executable": {"path": contract.BOOTSTRAP_EXECUTABLE,
                                    "sizeBytes": self.binary.stat().st_size,
                                    "sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest()},
                     "build": {"rustc": "rustc fixture", "target": "x86_64-pc-windows-msvc",
                               "profile": "release", "rustflags": "-C target-feature=+crt-static",
                               "sourceCommit": "1" * 40}}
        write_json(self.root / contract.BOOTSTRAP_LOCK, self.lock)

    def test_exact_source_binary_and_reordered_source_map_are_accepted(self):
        lock = copy.deepcopy(self.lock)
        lock["sourceFiles"] = dict(reversed(list(lock["sourceFiles"].items())))
        self.assertEqual(contract.verify_bootstrap(self.binary, self.root, lock), lock)

    def test_old_binary_fails_even_with_the_same_file_size(self):
        self.binary.write_bytes(b"x" * self.binary.stat().st_size)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            contract.verify_bootstrap(self.binary, self.root)

    def test_changed_dependencies_or_source_and_unlisted_new_module_fail(self):
        for name in ("Cargo.lock", "src/main.rs", "src/new_module.rs", "build.rs", ".cargo/config.toml"):
            path = self.root / "launcher/standalone" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            old = path.read_bytes() if path.exists() else None
            path.write_bytes(b"changed source\n")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "current source"):
                contract.verify_bootstrap(self.binary, self.root)
            if old is None: path.unlink()
            else: path.write_bytes(old)

    def test_incomplete_profile_source_coverage_and_executable_pin_fail(self):
        for mutation in ("schema-bool", "profile", "target", "flags", "commit", "rustc", "path",
                         "size-bool", "hash", "source-coverage", "source-fingerprint"):
            lock = copy.deepcopy(self.lock)
            if mutation == "schema-bool": lock["schemaVersion"] = True
            elif mutation == "profile": lock["build"].pop("profile")
            elif mutation == "target": lock["build"]["target"] = "i686-pc-windows-msvc"
            elif mutation == "flags": lock["build"]["rustflags"] = ""
            elif mutation == "commit": lock["build"]["sourceCommit"] = "main"
            elif mutation == "rustc": lock["build"].pop("rustc")
            elif mutation == "path": lock["executable"]["path"] = "native/other.exe"
            elif mutation == "size-bool": lock["executable"]["sizeBytes"] = True
            elif mutation == "hash": lock["executable"]["sha256"] = "not-a-hash"
            elif mutation == "source-coverage": lock["sourceFiles"].pop("launcher/standalone/Cargo.lock")
            else: lock["sourceSha256"] = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                contract.verify_bootstrap(self.binary, self.root, lock)

    def test_stage_rejects_old_pe_accepted_bootstrap_before_extracting_payload(self):
        self.binary.write_bytes(b"old bootstrap")
        write_json(self.root / "overlay.json", {})
        with patch.object(stage, "ROOT", self.root), \
                patch.object(stage, "verify_upstream", return_value={}), \
                patch.object(source_layers, "compute_proof", return_value={}), \
                patch.object(source_layers, "verify_proof"), \
                patch.object(stage, "verify_overlay"), patch.object(stage, "verify_pe"), \
                patch.object(stage, "unpack_runtime") as unpack:
            with self.assertRaisesRegex(ValueError, "reviewed implementation"):
                stage.stage_runtime(self.root / "archives", self.root / "output", "unused-openssl",
                                    self.binary, self.binary, self.root / "overlay.json",
                                    self.binary, self.root)
            unpack.assert_not_called()

    def test_packer_rejects_dropped_policy_before_overlay_or_library_packaging(self):
        runtime = {"nativeRuntime": {"executable": contract.BOOTSTRAP_EXECUTABLE, "version": "fixture"}}
        upstream = {"version": "fixture", "displayVersion": "Fixture", "commit": "2" * 40}
        content = {"name": "Fixture pack", "nativeProject": {
            "name": "Horizon", "version": "Fixture", "revision": "2" * 40}}
        candidate = self.root / "candidate"
        (candidate / "native").mkdir(parents=True)
        (candidate / contract.BOOTSTRAP_EXECUTABLE).write_bytes(self.binary.read_bytes())
        write_json(self.root / "portable-pack.json", runtime)
        write_json(candidate / "portable-pack.json", runtime)
        write_json(self.root / "library/content.json", content)
        write_json(self.root / "tools/upstream-lock.json", upstream)
        write_json(candidate / "native/presentation.json", {"name": content["name"]})
        files = {contract.BOOTSTRAP_EXECUTABLE: {}, "native/steam_api64.dll": {}}
        with patch.object(packer, "ROOT", self.root), \
                patch.object(packer, "LOCK", self.root / "tools/upstream-lock.json"), \
                patch.object(packer, "inventory", return_value=files), \
                patch.object(packer, "verify_pe"), patch.object(packer, "verify_overlay") as overlay:
            with self.assertRaisesRegex(ValueError, "missing its pack environment"):
                packer.describe_candidate(candidate)
            overlay.assert_not_called()


if __name__ == "__main__":
    unittest.main()
