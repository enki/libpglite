#!/usr/bin/env python3
import importlib.util
import json
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
PATCH_PATH = "patches/postgres-pglite/0001-test.patch"
PATCH_SHA256 = "a" * 64
SOURCE_COMMIT = "0123456789abcdef0123456789abcdef01234567"


class DoctorDiagnosticsTests(unittest.TestCase):
    def make_doctor(
        self,
        plugin_symbols: set[str],
        plugin_manifest_symbols: set[str],
        native_manifest_backend_symbols: set[str],
        backend_manifest_symbols: set[str],
        extension_inventory_text: str = "format=libpglite-native-extension-inventory-v1\n",
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
                    f"libpglite_git_commit={SOURCE_COMMIT}",
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
        native_manifest_lines = [
            "format=libpglite-native-link-manifest-v1",
            "source_repository=https://github.com/electric-sql/postgres-pglite",
            "source_ref=pglite-test",
            f"source_commit={SOURCE_COMMIT}",
            f"patch={PATCH_PATH}",
            f"patch_sha256={PATCH_PATH};sha256={PATCH_SHA256}",
            "patch_fingerprint=1234567890abcdef1234567890abcdef12345678",
        ]
        native_manifest_lines.extend(
            f"backend_export_symbol={symbol}"
            for symbol in sorted(native_manifest_backend_symbols)
        )
        (diagnostics / "native-link-manifest.txt").write_text(
            "\n".join(native_manifest_lines) + "\n"
        )
        (diagnostics / "extension-inventory.txt").write_text(extension_inventory_text)
        (diagnostics / "dependencies.txt").write_text(
            "format=libpglite-native-dependencies-v1\n"
        )
        (diagnostics / "plugin-defined-symbols.txt").write_text(
            "\n".join(sorted(plugin_manifest_symbols)) + "\n"
        )
        (diagnostics / "backend-export-symbols.txt").write_text(
            "\n".join(sorted(backend_manifest_symbols)) + "\n"
        )
        (diagnostics / "source-provenance.json").write_text(
            json.dumps(
                {
                    "format": "libpglite-native-source-provenance-v1",
                    "postgresPglite": {
                        "repository": "https://github.com/electric-sql/postgres-pglite",
                        "ref": "pglite-test",
                        "commit": SOURCE_COMMIT,
                    },
                    "patchFingerprint": "1234567890abcdef1234567890abcdef12345678",
                    "patches": [
                        {
                            "path": PATCH_PATH,
                            "sha256": PATCH_SHA256,
                        }
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

        doctor = doctor_module.Doctor(root, strict_relocatable=True)
        doctor.bundle = {
            "target": "test-target",
            "libpgliteReleaseVersion": "v0.1.0",
            "releaseMode": "development",
            "runtimeStatus": "native-runtime-pending-adr-0002",
            "libpgliteGitCommit": SOURCE_COMMIT,
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
                "sourceProvenance": "diagnostics/source-provenance.json",
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

    def test_source_provenance_patch_sha256_must_match_native_manifest(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        source_provenance = (
            pathlib.Path(tempdir.name) / "diagnostics" / "source-provenance.json"
        )
        provenance = json.loads(source_provenance.read_text())
        provenance["patches"][0]["sha256"] = "b" * 64
        source_provenance.write_text(json.dumps(provenance) + "\n")
        with tempdir:
            doctor.validate_source_provenance()

        self.assertIn(
            "source provenance patch sha256 mismatch",
            "\n".join(doctor.errors),
        )

    def test_source_provenance_identity_must_match_native_manifest(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        source_provenance = (
            pathlib.Path(tempdir.name) / "diagnostics" / "source-provenance.json"
        )
        provenance = json.loads(source_provenance.read_text())
        provenance["postgresPglite"]["commit"] = "f" * 40
        source_provenance.write_text(json.dumps(provenance) + "\n")
        with tempdir:
            doctor.validate_source_provenance()

        self.assertIn(
            "source provenance postgresPglite.commit mismatch",
            "\n".join(doctor.errors),
        )

    def test_other_extension_inventory_requires_submodule_provenance(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
            extension_inventory_text=(
                "format=libpglite-native-extension-inventory-v1\n"
                "other_extension=vector;"
                "source=pglite/other_extensions/vector;"
                "submodule_state=-;"
                "submodule_commit=;"
                "status=missing\n"
            ),
        )
        with tempdir:
            doctor.validate_extensions()

        errors = "\n".join(doctor.errors)
        self.assertIn("missing pinned submodule commit: vector", errors)
        self.assertIn("missing submodule URL: vector", errors)

    def test_missing_other_extension_is_warning_only_for_development(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
            extension_inventory_text=(
                "format=libpglite-native-extension-inventory-v1\n"
                "other_extension=vector;"
                "source=pglite/other_extensions/vector;"
                "submodule_state=-;"
                "submodule_commit=35ab919bf5da677709b2ebb8be07480bb25e97cf;"
                "status=missing;"
                "submodule_url=https://github.com/pgvector/pgvector.git\n"
            ),
        )
        with tempdir:
            doctor.validate_extensions()

        self.assertFalse(doctor.errors)
        self.assertIn(
            "PGlite other extension submodule is missing",
            "\n".join(doctor.warnings),
        )

    def test_missing_other_extension_blocks_production(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
            extension_inventory_text=(
                "format=libpglite-native-extension-inventory-v1\n"
                "other_extension=vector;"
                "source=pglite/other_extensions/vector;"
                "submodule_state=-;"
                "submodule_commit=35ab919bf5da677709b2ebb8be07480bb25e97cf;"
                "status=missing;"
                "submodule_url=https://github.com/pgvector/pgvector.git\n"
            ),
        )
        doctor.bundle["releaseMode"] = "production"
        with tempdir:
            doctor.validate_extensions()

        self.assertIn(
            "PGlite other extension submodule is missing",
            "\n".join(doctor.errors),
        )

    def test_present_other_extension_requires_packaged_control_files(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
            extension_inventory_text=(
                "format=libpglite-native-extension-inventory-v1\n"
                "other_extension=vector;"
                "source=pglite/other_extensions/vector;"
                "submodule_state=?;"
                "submodule_commit=35ab919bf5da677709b2ebb8be07480bb25e97cf;"
                "status=present;"
                "submodule_url=https://github.com/pgvector/pgvector.git\n"
            ),
        )
        with tempdir:
            doctor.validate_extensions()

        self.assertIn(
            "inventoried extension is missing control file: vector",
            "\n".join(doctor.errors),
        )


if __name__ == "__main__":
    unittest.main()
