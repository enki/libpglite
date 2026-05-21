#!/usr/bin/env python3
import importlib.util
import pathlib
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("doctor-native-plugin-package.py")
SPEC = importlib.util.spec_from_file_location("doctor_native_plugin_package", SCRIPT)
assert SPEC is not None
doctor_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(doctor_module)


ABI_SYMBOLS = set(doctor_module.ABI_SYMBOLS)


class DoctorDiagnosticsTests(unittest.TestCase):
    def make_doctor(
        self,
        plugin_symbols: set[str],
        plugin_manifest_symbols: set[str],
        native_manifest_backend_symbols: set[str],
        backend_manifest_symbols: set[str],
    ):
        tempdir = tempfile.TemporaryDirectory()
        root = pathlib.Path(tempdir.name)
        diagnostics = root / "diagnostics"
        diagnostics.mkdir()
        (diagnostics / "build-provenance.txt").write_text(
            "\n".join(
                [
                    "format=libpglite-native-build-provenance-v1",
                    "target=test-target",
                    "release_version=v0.1.0",
                    "release_mode=development",
                    "runtime_status=native-runtime-pending-adr-0002",
                    "libpglite_git_commit=0123456789abcdef0123456789abcdef01234567",
                    "plugin_filename=liblibpglite_plugin_native.dylib",
                    "plugin_sha256=abc123",
                    "native_manifest=native-link-manifest.txt",
                    "extension_inventory=extension-inventory.txt",
                    "packaged_at_utc=2026-05-21T00:00:00Z",
                    "uname=test",
                    "rustc_begin",
                    "rustc_end",
                    "cc_begin",
                    "cc_end",
                ]
            )
            + "\n"
        )
        native_manifest_lines = ["format=libpglite-native-link-manifest-v1"]
        native_manifest_lines.extend(
            f"backend_export_symbol={symbol}"
            for symbol in sorted(native_manifest_backend_symbols)
        )
        (diagnostics / "native-link-manifest.txt").write_text(
            "\n".join(native_manifest_lines) + "\n"
        )
        (diagnostics / "extension-inventory.txt").write_text(
            "format=libpglite-native-extension-inventory-v1\n"
        )
        (diagnostics / "dependencies.txt").write_text(
            "format=libpglite-native-dependencies-v1\n"
        )
        (diagnostics / "plugin-defined-symbols.txt").write_text(
            "\n".join(sorted(plugin_manifest_symbols)) + "\n"
        )
        (diagnostics / "backend-export-symbols.txt").write_text(
            "\n".join(sorted(backend_manifest_symbols)) + "\n"
        )

        doctor = doctor_module.Doctor(root, strict_relocatable=True)
        doctor.bundle = {
            "target": "test-target",
            "libpgliteReleaseVersion": "v0.1.0",
            "releaseMode": "development",
            "runtimeStatus": "native-runtime-pending-adr-0002",
            "libpgliteGitCommit": "0123456789abcdef0123456789abcdef01234567",
            "plugin": {
                "filename": "liblibpglite_plugin_native.dylib",
                "sha256": "abc123",
            },
            "diagnostics": {
                "buildProvenance": "diagnostics/build-provenance.txt",
                "nativeLinkManifest": "diagnostics/native-link-manifest.txt",
                "extensionInventory": "diagnostics/extension-inventory.txt",
                "dependencies": "diagnostics/dependencies.txt",
                "pluginDefinedSymbols": "diagnostics/plugin-defined-symbols.txt",
                "backendExportSymbols": "diagnostics/backend-export-symbols.txt",
            }
        }
        doctor.actual_plugin_symbols = plugin_symbols
        return tempdir, doctor

    def test_plugin_symbol_diagnostic_must_match_actual_exports(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS | {"ActualBackendSymbol"},
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols={"ActualBackendSymbol"},
            backend_manifest_symbols={"ActualBackendSymbol"},
        )
        with tempdir:
            doctor.validate_diagnostics()

        self.assertIn(
            "pluginDefinedSymbols is stale; missing actual plugin symbols",
            "\n".join(doctor.errors),
        )

    def test_backend_symbol_diagnostic_must_match_native_manifest(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS | {"BackendA", "BackendB"},
            plugin_manifest_symbols=ABI_SYMBOLS | {"BackendA", "BackendB"},
            native_manifest_backend_symbols={"BackendA", "BackendB"},
            backend_manifest_symbols={"BackendA"},
        )
        with tempdir:
            doctor.validate_diagnostics()

        self.assertIn(
            "backendExportSymbols is missing native link manifest symbols",
            "\n".join(doctor.errors),
        )

    def test_backend_symbol_diagnostic_must_be_exported_by_plugin(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS | {"BackendA"},
            plugin_manifest_symbols=ABI_SYMBOLS | {"BackendA"},
            native_manifest_backend_symbols={"BackendA", "BackendB"},
            backend_manifest_symbols={"BackendA", "BackendB"},
        )
        with tempdir:
            doctor.validate_diagnostics()

        self.assertIn(
            "backendExportSymbols lists symbols not exported by plugin",
            "\n".join(doctor.errors),
        )

    def test_build_provenance_must_match_bundle(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        build_provenance = (
            pathlib.Path(tempdir.name) / "diagnostics" / "build-provenance.txt"
        )
        build_provenance.write_text(
            build_provenance.read_text().replace(
                "plugin_sha256=abc123", "plugin_sha256=stale"
            )
        )
        with tempdir:
            doctor.validate_build_provenance()

        self.assertIn(
            "build provenance plugin_sha256 mismatch",
            "\n".join(doctor.errors),
        )

    def test_build_provenance_must_name_current_diagnostic_files(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        build_provenance = (
            pathlib.Path(tempdir.name) / "diagnostics" / "build-provenance.txt"
        )
        build_provenance.write_text(
            build_provenance.read_text().replace(
                "native_manifest=native-link-manifest.txt",
                "native_manifest=old-native-link-manifest.txt",
            )
        )
        with tempdir:
            doctor.validate_build_provenance()

        self.assertIn(
            "build provenance native_manifest mismatch",
            "\n".join(doctor.errors),
        )


if __name__ == "__main__":
    unittest.main()
