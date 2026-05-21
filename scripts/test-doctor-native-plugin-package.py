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
            "format=libpglite-native-build-provenance-v1\n"
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


if __name__ == "__main__":
    unittest.main()
