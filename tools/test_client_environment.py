import json
from pathlib import Path
import tempfile
import unittest

from client_environment import direct_client_environment


class DirectClientEnvironmentTests(unittest.TestCase):
    def test_published_policy_and_explicit_empty_alias_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            native = Path(folder)
            (native / "presentation.json").write_text(json.dumps({
                "name": "Technical client", "environmentDefaults": {"SAMPLE_LAYER": "1"}}))
            parent = {"PATH": "kept"}
            self.assertEqual(direct_client_environment(native / "client.exe", parent),
                             {"PATH": "kept", "SAMPLE_LAYER": "1"})
            self.assertEqual(parent, {"PATH": "kept"})
            explicit = {"sample_layer": "", "PATH": "kept"}
            self.assertEqual(direct_client_environment(native / "client.exe", explicit), explicit)

    def test_old_presentation_has_no_implicit_qa_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            native = Path(folder)
            (native / "presentation.json").write_text('{"name":"Technical client"}')
            self.assertEqual(direct_client_environment(native / "client.exe", {}), {})
