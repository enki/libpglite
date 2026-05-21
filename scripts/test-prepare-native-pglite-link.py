#!/usr/bin/env python3
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("prepare-native-pglite-link.sh")
PATCH = pathlib.Path(__file__).parents[1] / "patches" / "postgres-pglite" / "0001-pglitec-native-portability.patch"
RUNTIME_PATCH = pathlib.Path(__file__).parents[1] / "patches" / "postgres-pglite" / "0002-native-pglite-runtime-symbols.patch"


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

    def test_downstream_patches_are_git_apply_checked(self):
        text = SCRIPT.read_text()
        self.assertIn("patch_applier=git-apply-check-ceiling-v2", text)
        self.assertIn(
            'GIT_CEILING_DIRECTORIES="$repo_root" git -C "$patched_source" apply --check "$patch_file"',
            text,
        )
        self.assertIn(
            'GIT_CEILING_DIRECTORIES="$repo_root" git -C "$patched_source" apply "$patch_file"',
            text,
        )
        self.assertNotIn('patch -d "$patched_source" -p1 <"$patch_file"', text)

    def test_macos_deployment_target_invalidates_native_build_cache(self):
        text = SCRIPT.read_text()
        self.assertIn("build_env_fingerprint=\"source_commit=$source_commit", text)
        self.assertIn("macos_deployment_target=${MACOSX_DEPLOYMENT_TARGET:-}", text)
        self.assertIn('build_env_file="$postgres_build_dir/.libpglite-native-build-env"', text)
        self.assertIn(
            'if [[ ! -f "$build_env_file" || "$(cat "$build_env_file")" != "$build_env_fingerprint" ]]; then',
            text,
        )
        self.assertIn('rm -rf "$postgres_build_dir"', text)
        self.assertIn('printf \'%s\\n\' "$build_env_fingerprint" >"$build_env_file"', text)
        self.assertIn('echo "macos_deployment_target=$MACOSX_DEPLOYMENT_TARGET"', text)
        self.assertLess(
            text.index("macos_deployment_target=${MACOSX_DEPLOYMENT_TARGET:-}"),
            text.index('build_env_file="$postgres_build_dir/.libpglite-native-build-env"'),
        )
        self.assertLess(
            text.index('build_env_file="$postgres_build_dir/.libpglite-native-build-env"'),
            text.index('rm -rf "$postgres_build_dir"'),
        )
        self.assertLess(
            text.index('rm -rf "$postgres_build_dir"'),
            text.index('printf \'%s\\n\' "$build_env_fingerprint" >"$build_env_file"'),
        )

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
        self.assertIn("ssize_t pgl_recv(int fd, void *buf, size_t n, int flags);", text)
        self.assertIn(
            "ssize_t pgl_send(int fd, const void *buf, size_t n, int flags);",
            text,
        )
        self.assertIn("int pgl_connect(int socket, const struct sockaddr *address, socklen_t address_len);", text)
        self.assertIn("int pgl_fcntl(int fd, int cmd, ...);", text)
        self.assertIn("int pgl_poll(struct pollfd fds[], nfds_t nfds, int timeout);", text)
        self.assertIn("void pgl_siglongjmp(sigjmp_buf env, int val);", text)
        self.assertLess(text.index("ssize_t pgl_recv"), text.index("#define recv pgl_recv"))
        self.assertLess(text.index("int pgl_fcntl"), text.index("#define fcntl pgl_fcntl"))
        self.assertLess(text.index("int pgl_poll"), text.index("#define poll pgl_poll"))
        self.assertIn("#include <setjmp.h>", text)
        self.assertLess(text.index("#include <setjmp.h>"), text.index("#define siglongjmp pgl_siglongjmp"))
        self.assertIn("-include $socket_shim_header", text)
        self.assertNotIn("-Drecv=pgl_recv", text)
        self.assertNotIn("-Dpoll=pgl_poll", text)
        self.assertNotIn("-Dsiglongjmp=pgl_siglongjmp", text)

    def test_native_poll_patch_uses_host_poll_signature(self):
        text = PATCH.read_text()
        self.assertIn("#include <poll.h>", text)
        self.assertIn("typedef nfds_t pgl_poll_nfds_t;", text)
        self.assertIn("typedef ssize_t pgl_poll_nfds_t;", text)
        self.assertIn("int EMSCRIPTEN_KEEPALIVE pgl_poll(struct pollfd fds[], pgl_poll_nfds_t nfds, int timeout)", text)

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

    def test_native_runtime_patch_disables_postmaster_pipe_probe(self):
        text = RUNTIME_PATCH.read_text()
        self.assertIn("PostmasterIsAliveInternal(void)", text)
        self.assertIn("#if defined(__PGLITE__)\n+\treturn true;\n+#endif", text)
        self.assertIn("#elif defined(__PGLITE__)", text)


if __name__ == "__main__":
    unittest.main()
