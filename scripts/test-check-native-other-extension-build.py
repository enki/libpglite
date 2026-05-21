#!/usr/bin/env python3
import pathlib
import re
import unittest


SCRIPT = pathlib.Path(__file__).with_name("check-native-other-extension-build.sh")


class CheckNativeOtherExtensionBuildTests(unittest.TestCase):
    def test_postgis_gap_escape_hatch_is_explicit_opt_in(self):
        text = SCRIPT.read_text()
        self.assertIn("--allow-postgis-gap", text)
        self.assertIn("allow_postgis_gap=0", text)
        self.assertRegex(
            text,
            re.compile(r'allowed_gaps = \{"postgis"\} if allow_postgis_gap else set\(\)'),
        )

    def test_probe_materializes_and_builds_other_extensions(self):
        text = SCRIPT.read_text()
        self.assertIn("--fetch-other-extensions", text)
        self.assertIn("--build-other-extensions", text)
        self.assertIn("other_extension source is not materialized", text)
        self.assertIn("other_extension is missing installed control file", text)


if __name__ == "__main__":
    unittest.main()
