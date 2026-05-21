#!/usr/bin/env python3
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("build-native-dependency-prefix.sh")


class BuildNativeDependencyPrefixTests(unittest.TestCase):
    def test_accepts_macos_and_linux_libtoolize_names(self):
        text = SCRIPT.read_text()
        self.assertIn("require_libtoolize()", text)
        self.assertIn("command -v glibtoolize", text)
        self.assertIn("command -v libtoolize", text)
        self.assertNotIn("require glibtoolize", text)

    def test_refreshes_stale_autotools_platform_scripts_for_ossp_uuid(self):
        text = SCRIPT.read_text()
        self.assertIn("refresh_config_scripts()", text)
        self.assertIn("/usr/share/misc/$script", text)
        self.assertIn("/opt/homebrew/share/automake-*/\"$script\"", text)
        self.assertIn("refresh_config_scripts \"$src\"", text)


if __name__ == "__main__":
    unittest.main()
