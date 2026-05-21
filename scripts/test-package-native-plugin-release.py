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

    def test_linux_package_rewrites_rpaths_to_package_local_origin(self):
        text = SCRIPT.read_text()
        self.assertIn("require patchelf", text)
        self.assertIn("repair_linux_package_rpaths", text)
        self.assertIn("patchelf --set-rpath '$ORIGIN/postgres/lib' \"$plugin\"", text)
        self.assertIn("patchelf --set-rpath '$ORIGIN' \"$object\"", text)
        self.assertLess(
            text.index("repair_linux_package_rpaths \"$binary_stage\" \"$expected_plugin\""),
            text.index("dependency_report \\"),
        )

    def test_package_records_platform_baseline_diagnostic(self):
        text = SCRIPT.read_text()
        self.assertIn("platform-baseline.json", text)
        self.assertIn("LIBPGLITE_LINUX_BASELINE_ID", text)
        self.assertIn("LIBPGLITE_LINUX_BASELINE_VERSION_ID", text)
        self.assertIn("Linux native package baseline mismatch", text)
        self.assertIn('"platformBaseline": "diagnostics/platform-baseline.json"', text)
        self.assertIn('echo "platform_baseline=platform-baseline.json"', text)


if __name__ == "__main__":
    unittest.main()
