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

    def test_linux_preflight_uses_staticlib_final_link_boundary(self):
        text = SCRIPT.read_text()
        self.assertIn("cargo rustc -p libpglite-plugin-native --release --lib --crate-type staticlib", text)
        self.assertIn("scripts/link-linux-native-plugin.sh \\", text)
        self.assertIn('"$target_dir/release/liblibpglite_plugin_native.a"', text)
        self.assertIn('"$plugin_binary"', text)
        self.assertLess(
            text.index("cargo rustc -p libpglite-plugin-native --release --lib --crate-type staticlib"),
            text.index("scripts/link-linux-native-plugin.sh \\"),
        )
        self.assertIn("grep -Ev '^LIBPGLITE_PLUGIN_NATIVE_[0-9]+$'", text)

    def test_failed_conformance_prints_log_tail(self):
        text = SCRIPT.read_text()
        self.assertIn("conformance check failed: $name", text)
        self.assertIn("last 200 log lines from $log_file", text)
        self.assertIn('tail -n 200 "$log_file"', text)


if __name__ == "__main__":
    unittest.main()
