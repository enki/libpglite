#!/usr/bin/env python3
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("prepare-native-pglite-link.sh")


class PrepareNativePgliteLinkTests(unittest.TestCase):
    def test_postgis_build_uses_controlled_static_prefix(self):
        text = SCRIPT.read_text()
        self.assertIn("build_native_postgis_extension", text)
        self.assertIn("--with-geosconfig=\"$postgis_config_wrapper_dir/geos-config\"", text)
        self.assertIn("PKG_CONFIG=\"$postgis_config_wrapper_dir/pkg-config\"", text)
        self.assertIn("--with-jsondir=\"$dependency_prefix\"", text)
        self.assertIn("BE_DLLLIBS=\"$native_extension_be_dlllibs\"", text)
        self.assertIn("native Postgres install prefix is missing PostGIS projection data", text)

    def test_postgis_replaces_only_the_previous_explicit_skip(self):
        text = SCRIPT.read_text()
        self.assertNotIn("native PGlite other extension build does not yet handle PostGIS", text)
        self.assertIn("if [[ \"$extension\" == \"postgis\" ]]; then", text)
        self.assertIn("build_native_postgis_extension", text)

    def test_backend_export_scanner_includes_common_data_symbols(self):
        text = SCRIPT.read_text()
        self.assertIn("awk '$2 ~ /^[TDBSC]$/ {print $3}'", text)

    def test_plpgsql_uses_extension_dynamic_lookup_flags(self):
        text = SCRIPT.read_text()
        self.assertIn(
            'find "$postgres_build_dir/src/pl/plpgsql/src" -maxdepth 1 -type f \\( -name \'*.dylib\' -o -name \'*.so\' \\) -delete',
            text,
        )
        self.assertIn(
            'make -C "$postgres_build_dir/src/pl/plpgsql/src" install \\\n'
            '    BE_DLLLIBS="$native_extension_be_dlllibs"',
            text,
        )


if __name__ == "__main__":
    unittest.main()
