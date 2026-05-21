#!/usr/bin/env python3
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("preflight-linux-smolvm.sh")


class PreflightLinuxSmolvmTests(unittest.TestCase):
    def test_uses_documented_ubuntu_baseline_and_repo_mount(self):
        text = SCRIPT.read_text()
        self.assertIn('image="${LIBPGLITE_LINUX_BASELINE_IMAGE:-ubuntu:24.04}"', text)
        self.assertIn('--volume "$repo_root:/mnt/libpglite"', text)
        self.assertIn("cd /mnt/libpglite", text)
        self.assertIn("LIBPGLITE_SMOLVM_LIB_DIR", text)
        self.assertIn("DYLD_LIBRARY_PATH=$smolvm_lib_dir", text)

    def test_mounts_postgres_source_and_marks_git_safe(self):
        text = SCRIPT.read_text()
        self.assertIn("LIBPGLITE_POSTGRES_SOURCE_DIR=<path>", text)
        self.assertIn('repo_root/../postgres-pglite', text)
        self.assertIn('--volume "$postgres_source_dir:/mnt/postgres-pglite"', text)
        self.assertIn("safe.directory /mnt/libpglite", text)
        self.assertIn("safe.directory /mnt/postgres-pglite", text)
        self.assertIn("export LIBPGLITE_POSTGRES_SOURCE_DIR=/mnt/postgres-pglite", text)

    def test_runs_release_preflight_as_unprivileged_user(self):
        text = SCRIPT.read_text()
        self.assertIn("useradd -m -s /bin/bash libpglite", text)
        self.assertIn("runuser -u libpglite -- bash -lc", text)
        self.assertLess(text.index("apt-get install"), text.index("runuser -u libpglite"))
        self.assertLess(
            text.index("runuser -u libpglite"),
            text.index("scripts/preflight-native-plugin-release.sh"),
        )

    def test_installs_linux_build_prerequisites_and_runs_release_preflight(self):
        text = SCRIPT.read_text()
        for package in [
            "autoconf",
            "automake",
            "build-essential",
            "cmake",
            "curl",
            "git",
            "libtool",
            "patchelf",
            "pkg-config",
            "python3",
            "zstd",
        ]:
            self.assertIn(package, text)
        self.assertIn("rustup.rs", text)
        self.assertIn("scripts/preflight-native-plugin-release.sh", text)
        self.assertIn("CARGO_TARGET_DIR=/tmp/libpglite-cargo-target", text)
        self.assertIn("LIBPGLITE_NATIVE_BUILD_ROOT=/tmp/libpglite-native", text)
        self.assertIn("LIBPGLITE_RELEASE_OUT_DIR=/tmp/libpglite-dist", text)


if __name__ == "__main__":
    unittest.main()
