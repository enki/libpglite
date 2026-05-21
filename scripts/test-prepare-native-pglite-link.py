#!/usr/bin/env python3
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("prepare-native-pglite-link.sh")
PATCH = pathlib.Path(__file__).parents[1] / "patches" / "postgres-pglite" / "0001-pglitec-native-portability.patch"


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

    def test_backend_export_scanner_includes_common_and_readonly_data_symbols(self):
        text = SCRIPT.read_text()
        self.assertIn("awk '$2 ~ /^[TDBSCR]$/ {print $3}'", text)

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

    def test_timezone_archive_excludes_cli_entrypoints(self):
        text = SCRIPT.read_text()
        self.assertIn("! -name 'zic.o'", text)
        self.assertIn("! -name 'zdump.o'", text)
        self.assertIn('ar -crs "$timezone_archive" "${timezone_objects[@]}"', text)

    def test_linux_uses_poll_latch_path_for_callback_socket(self):
        text = SCRIPT.read_text()
        self.assertIn('if [[ "$(uname -s)" == "Linux" ]]; then', text)
        self.assertIn("-DWAIT_USE_POLL", text)
        self.assertIn("-DWAIT_USE_SELF_PIPE", text)
        self.assertIn("#define poll pgl_poll", text)
        self.assertLess(text.index("#define poll pgl_poll"), text.index("-DWAIT_USE_POLL"))

    def test_socket_shims_are_defined_after_system_socket_headers(self):
        text = SCRIPT.read_text()
        self.assertIn("socket_shim_header=\"$object_dir/libpglite_native_socket_shims.h\"", text)
        self.assertIn("#include <sys/socket.h>", text)
        self.assertLess(text.index("#include <sys/socket.h>"), text.index("#define recv pgl_recv"))
        self.assertIn("#include <setjmp.h>", text)
        self.assertLess(text.index("#include <setjmp.h>"), text.index("#define siglongjmp pgl_siglongjmp"))
        self.assertIn("-include $socket_shim_header", text)
        self.assertNotIn("-Drecv=pgl_recv", text)
        self.assertNotIn("-Dpoll=pgl_poll", text)
        self.assertNotIn("-Dsiglongjmp=pgl_siglongjmp", text)

    def test_backend_archive_audits_socket_shim_binding(self):
        text = SCRIPT.read_text()
        self.assertIn("assert_backend_uses_pglite_socket_shims", text)
        self.assertIn("native Postgres backend archive still references libc socket APIs", text)
        self.assertIn("native Postgres backend archive is missing expected PGlite socket shim reference", text)
        self.assertIn("pgl_recv", text)
        self.assertIn("required_socket_shims=(pgl_recv pgl_send)", text)
        self.assertIn("required_socket_shims+=(pgl_poll)", text)
        self.assertIn("required_socket_shims+=(pgl_siglongjmp)", text)
        self.assertIn('nm -u -A "$backend_archive"', text)
        self.assertIn('symbol = $NF', text)
        self.assertIn('sub(/:[[:space:]]*(U[[:space:]]+)?_*[^[:space:]]+$/, "", location)', text)
        self.assertIn('grep -F ": $unexpected_symbol" "$undefined_with_objects"', text)
        self.assertIn("__longjmp_chk", text)
        self.assertIn("siglongjmp", text)

    def test_native_longjmp_patch_uses_buffer_identity_not_jmpbuf_bytes(self):
        text = PATCH.read_text()
        self.assertIn("(void *) env == (void *) postgresmain_sigjmp_buf", text)
        added_lines = "\n".join(line for line in text.splitlines() if line.startswith("+"))
        self.assertNotIn("memcmp(env, (void*)postgresmain_sigjmp_buf", added_lines)


if __name__ == "__main__":
    unittest.main()
