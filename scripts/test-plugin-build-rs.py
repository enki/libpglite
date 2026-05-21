#!/usr/bin/env python3
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "plugin" / "build.rs"


class PluginBuildRsTests(unittest.TestCase):
    def test_linux_version_script_includes_generated_backend_exports(self):
        text = SCRIPT.read_text()
        self.assertIn("backend_export_symbols_from_manifest", text)
        self.assertIn("emit_linux_plugin_export_boundary(&backend_exports)", text)
        self.assertIn('line.strip_prefix("backend_export_symbol=")', text)
        self.assertIn("for symbol in backend_exports", text)
        self.assertIn("script.push_str(symbol)", text)

    def test_linux_version_script_is_not_abi_only_when_native_linking(self):
        text = SCRIPT.read_text()
        old_abi_only_call = "emit_linux_plugin_export_boundary();"
        self.assertNotIn(old_abi_only_call, text)
        self.assertIn("Some(read_native_manifest())", text)


if __name__ == "__main__":
    unittest.main()
