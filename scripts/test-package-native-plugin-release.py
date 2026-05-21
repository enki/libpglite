#!/usr/bin/env python3
import os
import pathlib
import subprocess
import tempfile
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
        self.assertIn('echo "macos_deployment_target=$(manifest_value macos_deployment_target)"', text)
        self.assertIn('echo "linux_baseline_id=${LIBPGLITE_LINUX_BASELINE_ID:-ubuntu}"', text)
        self.assertIn(
            'echo "linux_baseline_version_id=${LIBPGLITE_LINUX_BASELINE_VERSION_ID:-24.04}"',
            text,
        )

    def test_build_provenance_names_release_diagnostics(self):
        text = SCRIPT.read_text()
        for line in [
            'echo "plugin_defined_symbols=plugin-defined-symbols.txt"',
            'echo "backend_export_symbols=backend-export-symbols.txt"',
            'echo "dependencies=dependencies.txt"',
            'echo "source_provenance=source-provenance.json"',
            'echo "runtime_lifecycle=runtime-lifecycle.json"',
            'echo "conformance_results=conformance"',
        ]:
            self.assertIn(line, text)

    def test_production_packaging_is_blocked_while_root_adrs_are_open(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir) / "repo"
            scripts = root / "scripts"
            docs = root / "docs"
            out = root / "out"
            scripts.mkdir(parents=True)
            docs.mkdir()
            script = scripts / SCRIPT.name
            script.write_text(SCRIPT.read_text())
            script.chmod(0o755)
            (docs / "ADR-9999-TEST-OPEN.md").write_text(
                "# ADR-9999: Test Open ADR\n\n"
                "Status: Open\n\n"
                "## Acceptance Criteria\n\n"
                "- test\n\n"
                "## Remaining Closure Criteria\n\n"
                "- test\n"
            )
            plugin = root / "liblibpglite_plugin_native.dylib"
            plugin.write_bytes(b"placeholder")
            env = os.environ.copy()
            env["LIBPGLITE_RELEASE_MODE"] = "production"
            result = subprocess.run(
                [str(script), "v0.0.0-test", str(plugin), str(out)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "production packaging is blocked while release-gating ADRs remain open",
            result.stderr,
        )
        self.assertIn("docs/ADR-9999-TEST-OPEN.md", result.stderr)
        self.assertNotIn("docs/ADR-0004-RUNTIME-READY-RELEASE-GATE.md", result.stderr)

    def test_production_packaging_requires_dependency_prefix_manifest(self):
        text = SCRIPT.read_text()
        self.assertIn(
            'if [[ "$release_mode" == "production" && -z "$dependency_prefix_manifest" ]]',
            text,
        )
        self.assertIn(
            "production package requires native_dependency_prefix_manifest",
            text,
        )
        self.assertLess(
            text.index("production package requires native_dependency_prefix_manifest"),
            text.index('diagnostics["dependencyPrefix"] = dependency_prefix_diagnostic'),
        )

    def test_package_doctor_runs_before_binary_archive_is_written(self):
        text = SCRIPT.read_text()
        self.assertLess(
            text.index('"$repo_root/scripts/doctor-native-plugin-package.py" "$binary_stage"'),
            text.index('tar -C "$binary_stage" --zstd -cf "$binary_asset" .'),
        )

    def test_runtime_package_prunes_postgres_server_headers(self):
        text = SCRIPT.read_text()
        self.assertIn('rm -rf "$binary_stage/postgres/include"', text)
        self.assertLess(
            text.index('rm -rf "$binary_stage/postgres/include"'),
            text.index('repair_macos_package_install_names "$binary_stage" "$expected_plugin"'),
        )


if __name__ == "__main__":
    unittest.main()
