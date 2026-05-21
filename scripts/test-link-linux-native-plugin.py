#!/usr/bin/env python3
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("link-linux-native-plugin.sh")


class LinkLinuxNativePluginTests(unittest.TestCase):
    def test_owns_single_gnu_ld_export_boundary(self):
        text = SCRIPT.read_text()
        self.assertIn("LIBPGLITE_PLUGIN_NATIVE_1 {", text)
        self.assertIn("backend_export_symbol", text)
        self.assertIn("-Wl,--version-script=\"$version_script\"", text)
        self.assertIn("local:", text)
        self.assertIn("*;", text)
        self.assertNotIn("-Wl,--exclude-libs,ALL", text)
        self.assertNotIn("cargo:", text)

    def test_links_rust_staticlib_and_manifest_inputs(self):
        text = SCRIPT.read_text()
        self.assertIn("rust_staticlib", text)
        self.assertIn("objects+=(\"$path\")", text)
        self.assertIn("archives+=(\"$path\")", text)
        self.assertIn("link_args+=(\"$value\")", text)
        self.assertIn("-Wl,--whole-archive", text)
        self.assertIn("\"$rust_staticlib\"", text)
        self.assertIn("\"${archives[@]}\"", text)

    def test_rejects_missing_manifest_shape(self):
        text = SCRIPT.read_text()
        self.assertIn("format=libpglite-native-link-manifest-v1", text)
        self.assertIn("contains no backend_export_symbol entries", text)
        self.assertIn("must provide object and archive inputs", text)


if __name__ == "__main__":
    unittest.main()
