#!/usr/bin/env python3
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("package-native-plugin-release.sh")


class PackageNativePluginReleaseTests(unittest.TestCase):
    def test_linux_defined_symbols_filters_gnu_version_node(self):
        text = SCRIPT.read_text()
        self.assertIn("nm -D --defined-only \"$binary\"", text)
        self.assertIn("grep -Ev '^LIBPGLITE_PLUGIN_NATIVE_[0-9]+$'", text)
        self.assertLess(
            text.index("sed 's/@.*//'"),
            text.index("grep -Ev '^LIBPGLITE_PLUGIN_NATIVE_[0-9]+$'"),
        )


if __name__ == "__main__":
    unittest.main()
