#!/usr/bin/env python3
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("preflight-native-plugin-release.sh")


class PreflightNativePluginReleaseTest(unittest.TestCase):
    def test_preflight_uses_controlled_dependency_prefix_for_native_prepare(self):
        text = SCRIPT.read_text()
        build = (
            "scripts/build-native-dependency-prefix.sh \\\n"
            "  --prefix \"$dependency_prefix\" \\\n"
            "  --sources \"$dependency_sources\" \\\n"
            "  --work-dir \"$dependency_work_dir\""
        )
        prepare = (
            "scripts/prepare-native-pglite-link.sh \\\n"
            "  --build-postgres \\\n"
            "  --out \"$manifest\" \\\n"
            "  --dependency-prefix \"$dependency_prefix\" \\\n"
            "  --fetch-other-extensions \\\n"
            "  --build-other-extensions"
        )

        self.assertIn("LIBPGLITE_NATIVE_BUILD_ROOT", text)
        self.assertIn("LIBPGLITE_NATIVE_DEPENDENCY_PREFIX", text)
        self.assertIn("LIBPGLITE_NATIVE_DEPENDENCY_SOURCES", text)
        self.assertIn("LIBPGLITE_NATIVE_DEPENDENCY_BUILD_DIR", text)
        self.assertIn("export LIBPGLITE_NATIVE_LINK_MANIFEST=\"$manifest\"", text)
        self.assertIn(build, text)
        self.assertIn(prepare, text)
        self.assertLess(text.index(build), text.index(prepare))


if __name__ == "__main__":
    unittest.main()
