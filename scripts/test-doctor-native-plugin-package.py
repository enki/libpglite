#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


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
PGLITE_OTHER_EXTENSIONS = [
    "age",
    "pg_hashids",
    "pg_ivm",
    "pg_textsearch",
    "pg_uuidv7",
    "pgtap",
    "postgis",
    "vector",
]


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
                    "plugin_defined_symbols=plugin-defined-symbols.txt",
                    "backend_export_symbols=backend-export-symbols.txt",
                    "dependencies=dependencies.txt",
                    "dependency_manifest=dependencies.json",
                    "platform_baseline=platform-baseline.json",
                    "source_provenance=source-provenance.json",
                    "runtime_lifecycle=runtime-lifecycle.json",
                    "conformance_results=conformance",
                    "macos_deployment_target=11.0",
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
            "macos_deployment_target=11.0",
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
            "tool=otool -L\n"
            "binary=liblibpglite_plugin_native.dylib\n"
        )
        (diagnostics / "dependencies.json").write_text(
            json.dumps(
                {
                    "format": "libpglite-native-dependencies-v1",
                    "platform": "Darwin",
                    "tool": "otool -L",
                    "packageRoot": ".",
                    "objects": [
                        {
                            "path": "liblibpglite_plugin_native.dylib",
                            "kind": "plugin",
                            "toolExitCode": 0,
                            "dependencies": [
                                {
                                    "raw": "/usr/lib/libSystem.B.dylib",
                                    "path": "/usr/lib/libSystem.B.dylib",
                                    "classification": "platform",
                                }
                            ],
                        }
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        (diagnostics / "platform-baseline.json").write_text(
            json.dumps(
                {
                    "format": "libpglite-native-platform-baseline-v1",
                    "target": "test-target",
                    "system": "TestOS",
                    "machine": "test-machine",
                    "baseline": {
                        "kind": "test",
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
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
            "postgresPrefix": dict(doctor_module.POSTGRES_PREFIX_LAYOUT),
            "plugin": {
                "filename": "liblibpglite_plugin_native.dylib",
                "sha256": "abc123",
            },
            "diagnostics": {
                "buildProvenance": "diagnostics/build-provenance.txt",
                "nativeLinkManifest": "diagnostics/native-link-manifest.txt",
                "extensionInventory": "diagnostics/extension-inventory.txt",
                "dependencies": "diagnostics/dependencies.txt",
                "dependencyManifest": "diagnostics/dependencies.json",
                "platformBaseline": "diagnostics/platform-baseline.json",
                "pluginDefinedSymbols": "diagnostics/plugin-defined-symbols.txt",
                "backendExportSymbols": "diagnostics/backend-export-symbols.txt",
                "sourceProvenance": "diagnostics/source-provenance.json",
                "conformanceResults": "diagnostics/conformance",
            }
        }
        doctor.actual_plugin_symbols = plugin_symbols
        return tempdir, doctor

    def test_bundle_runtime_status_must_match_release_mode(self):
        cases = [
            (
                "production",
                "native-runtime-pending-adr-0002",
                "production package runtimeStatus must be 'runtime-ready'",
            ),
            (
                "development",
                "runtime-ready",
                "development package must not claim runtimeStatus 'runtime-ready'",
            ),
            (
                "development",
                "unknown-status",
                "unsupported runtimeStatus: 'unknown-status'",
            ),
        ]
        for release_mode, runtime_status, expected in cases:
            with self.subTest(release_mode=release_mode, runtime_status=runtime_status):
                tempdir, doctor = self.make_doctor(
                    plugin_symbols=ABI_SYMBOLS,
                    plugin_manifest_symbols=ABI_SYMBOLS,
                    native_manifest_backend_symbols=set(),
                    backend_manifest_symbols=set(),
                )
                doctor.bundle["releaseMode"] = release_mode
                doctor.bundle["runtimeStatus"] = runtime_status
                with tempdir:
                    doctor.validate_bundle()

                self.assertIn(expected, "\n".join(doctor.errors))

    def write_packaged_extension(
        self,
        doctor,
        extension: str,
        control_text: str,
        sql_files: dict[str, str],
        modules: list[str] | None = None,
    ):
        extension_dir = doctor.root / "postgres" / "share" / "extension"
        lib_dir = doctor.root / "postgres" / "lib"
        extension_dir.mkdir(parents=True, exist_ok=True)
        lib_dir.mkdir(parents=True, exist_ok=True)
        (extension_dir / f"{extension}.control").write_text(control_text)
        for name, text in sql_files.items():
            (extension_dir / name).write_text(text)
        for module in modules or []:
            (lib_dir / module).write_text("")

    def test_conformance_diagnostics_reject_missing_failed_and_stale_results(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        doctor.bundle["releaseMode"] = "production"
        doctor.bundle["runtimeStatus"] = "runtime-ready"
        doctor.bundle["diagnostics"]["conformanceResults"] = "diagnostics/conformance"
        conformance = pathlib.Path(tempdir.name) / "diagnostics" / "conformance"
        conformance.mkdir()
        (conformance / "raw-protocol.log").write_text("actual log\n")
        (conformance / "raw-protocol.json").write_text(
            json.dumps(
                {
                    "format": "libpglite-native-conformance-result-v1",
                    "name": "raw-protocol",
                    "status": "failed",
                    "exitCode": 1,
                    "log": "raw-protocol.log",
                    "logSha256": hashlib.sha256(b"different log\n").hexdigest(),
                }
            )
            + "\n"
        )
        with tempdir:
            doctor.validate_conformance()

        errors = "\n".join(doctor.errors)
        self.assertIn("conformance result raw-protocol.json did not pass", errors)
        self.assertIn("conformance result raw-protocol.json exitCode is not 0", errors)
        self.assertIn("conformance result raw-protocol.json logSha256 mismatch", errors)
        self.assertIn("conformance result is missing: tokio-postgres-client.json", errors)

    def test_raw_protocol_conformance_must_name_required_cases(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        doctor.bundle["diagnostics"]["conformanceResults"] = "diagnostics/conformance"
        conformance = pathlib.Path(tempdir.name) / "diagnostics" / "conformance"
        conformance.mkdir()
        (conformance / "raw-protocol.log").write_text("raw ok\n")
        (conformance / "raw-protocol.json").write_text(
            json.dumps(
                {
                    "format": "libpglite-native-conformance-result-v1",
                    "name": "raw-protocol",
                    "status": "passed",
                    "exitCode": 0,
                    "log": "raw-protocol.log",
                    "logSha256": hashlib.sha256(b"raw ok\n").hexdigest(),
                    "cases": ["startup", "simple-query"],
                }
            )
            + "\n"
        )
        with tempdir:
            doctor.validate_conformance()

        errors = "\n".join(doctor.errors)
        self.assertIn("raw-protocol conformance result is missing cases", errors)
        self.assertIn("empty-query", errors)
        self.assertIn("transaction-commit", errors)
        self.assertIn("parameterized-extended-query", errors)
        self.assertIn("deterministic-shutdown", errors)

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

    def test_defined_symbols_filters_linux_gnu_version_node(self):
        class Uname:
            sysname = "Linux"

        completed = mock.Mock(stdout="00000000 A LIBPGLITE_PLUGIN_NATIVE_1\n00000000 T libpglite_plugin_abi_version@@LIBPGLITE_PLUGIN_NATIVE_1\n")
        with mock.patch.object(doctor_module.os, "uname", return_value=Uname()):
            with mock.patch.object(doctor_module.subprocess, "run", return_value=completed):
                symbols = doctor_module.defined_symbols(pathlib.Path("plugin.so"))

        self.assertEqual(symbols, {"libpglite_plugin_abi_version"})

    def test_linux_plugin_symbol_boundary_rejects_accidental_exports(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS | {"BackendA", "AccidentalRustExport"},
            plugin_manifest_symbols=ABI_SYMBOLS | {"BackendA", "AccidentalRustExport"},
            native_manifest_backend_symbols={"BackendA"},
            backend_manifest_symbols={"BackendA"},
        )
        doctor.bundle["target"] = "x86_64-unknown-linux-gnu"
        with tempdir:
            doctor.validate_diagnostics()

        self.assertIn(
            "Linux pluginDefinedSymbols contains symbols outside the host ABI "
            "and generated backend export set: AccidentalRustExport",
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

    def test_backend_symbol_diagnostic_must_cover_full_extension_references(self):
        extension_backend_symbols = {
            extension: f"Backend_{extension}"
            for extension in PGLITE_OTHER_EXTENSIONS
        }
        actual_symbols = ABI_SYMBOLS | {"ManifestBackend"} | set(extension_backend_symbols.values())
        tempdir, doctor = self.make_doctor(
            plugin_symbols=actual_symbols,
            plugin_manifest_symbols=actual_symbols,
            native_manifest_backend_symbols={"ManifestBackend"},
            backend_manifest_symbols={"ManifestBackend"},
        )
        lib_dir = pathlib.Path(tempdir.name) / "postgres" / "lib"
        lib_dir.mkdir(parents=True)
        for extension in PGLITE_OTHER_EXTENSIONS:
            (lib_dir / f"{extension}.dylib").write_text("")

        def module_undefined_symbols(path: pathlib.Path) -> set[str]:
            return {extension_backend_symbols[path.stem]}

        with tempdir:
            with mock.patch.object(
                doctor_module, "undefined_symbols", side_effect=module_undefined_symbols
            ):
                doctor.validate_diagnostics()

        errors = "\n".join(doctor.errors)
        self.assertIn(
            "extension modules reference plugin-exported backend symbols missing from backendExportSymbols",
            errors,
        )
        for extension, symbol in extension_backend_symbols.items():
            self.assertIn(f"postgres/lib/{extension}.dylib: {symbol}", errors)

    def test_native_package_rejects_wasm_and_javascript_payloads(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        root = pathlib.Path(tempdir.name)
        (root / "postgres" / "share").mkdir(parents=True)
        (root / "postgres" / "share" / "pglite.wasm").write_bytes(b"wasm")
        (root / "postgres" / "share" / "pglite.mjs").write_text("export {}\n")
        (root / "postgres" / "lib" / "emscripten-module.o").parent.mkdir(parents=True)
        (root / "postgres" / "lib" / "emscripten-module.o").write_bytes(b"object")
        (root / "postgres" / "lib" / "backend-wasm2c-fallback.a").write_bytes(b"archive")
        with tempdir:
            doctor.validate_native_only_payload()

        errors = "\n".join(doctor.errors)
        self.assertIn("native package contains non-native payload: postgres/share/pglite.wasm", errors)
        self.assertIn("native package contains non-native payload: postgres/share/pglite.mjs", errors)
        self.assertIn(
            "native package contains non-native payload: postgres/lib/emscripten-module.o",
            errors,
        )
        self.assertIn(
            "native package contains non-native payload: postgres/lib/backend-wasm2c-fallback.a",
            errors,
        )

    def test_postgres_prefix_layout_must_match_package_contract(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        doctor.bundle["postgresPrefix"]["share"] = "share"
        with tempdir:
            doctor.validate_postgres_prefix()

        self.assertIn(
            "bundle postgresPrefix.share must be 'postgres/share', got 'share'",
            "\n".join(doctor.errors),
        )

    def test_postgres_prefix_text_metadata_rejects_build_machine_paths(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        root = pathlib.Path(tempdir.name)
        extension_dir = root / "postgres" / "share" / "extension"
        extension_dir.mkdir(parents=True)
        (extension_dir / "leaky.control").write_text(
            "default_version = '1.0'\ncomment = '/Users/paul/build/prefix'\n"
        )
        (extension_dir / "leaky--1.0.sql").write_text(
            "select '/tmp/libpglite-native/install';\n"
        )
        with tempdir:
            doctor.validate_postgres_prefix_text_paths()

        errors = "\n".join(doctor.errors)
        self.assertIn("PostgreSQL prefix text metadata contains build-machine paths", errors)
        self.assertIn("postgres/share/extension/leaky.control", errors)
        self.assertIn("postgres/share/extension/leaky--1.0.sql", errors)

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

    def test_build_provenance_must_name_all_release_diagnostics(self):
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
            build_provenance.read_text()
            .replace(
                "source_provenance=source-provenance.json",
                "source_provenance=old-source-provenance.json",
            )
            .replace(
                "conformance_results=conformance",
                "conformance_results=old-conformance",
            )
        )
        with tempdir:
            doctor.validate_build_provenance()

        errors = "\n".join(doctor.errors)
        self.assertIn("build provenance source_provenance mismatch", errors)
        self.assertIn("build provenance conformance_results mismatch", errors)

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

    def test_source_provenance_patch_fingerprint_must_be_full_sha1(self):
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
        provenance["patchFingerprint"] = "not-a-sha"
        source_provenance.write_text(json.dumps(provenance) + "\n")
        with tempdir:
            doctor.validate_source_provenance()

        self.assertIn(
            "source provenance patchFingerprint is not a full SHA-1",
            "\n".join(doctor.errors),
        )

    def test_malformed_structured_diagnostics_are_rejected(self):
        cases = [
            (
                "diagnostics/dependencies.json",
                lambda doctor: doctor.validate_dependencies(),
                "dependency manifest is not readable",
            ),
            (
                "diagnostics/platform-baseline.json",
                lambda doctor: doctor.validate_platform_baseline(),
                "platform baseline diagnostic is not readable",
            ),
            (
                "diagnostics/source-provenance.json",
                lambda doctor: doctor.validate_source_provenance(),
                "source provenance diagnostic is not readable",
            ),
            (
                "diagnostics/native-dependency-prefix.json",
                lambda doctor: doctor.validate_dependencies(),
                "dependency prefix manifest is not readable",
            ),
            (
                "diagnostics/runtime-lifecycle.json",
                lambda doctor: doctor.validate_lifecycle(),
                "runtime lifecycle diagnostic is not readable",
            ),
            (
                "diagnostics/conformance/raw-protocol.json",
                lambda doctor: doctor.validate_conformance(),
                "conformance result raw-protocol.json is not readable",
            ),
        ]
        for rel_path, validate, expected in cases:
            with self.subTest(rel_path=rel_path):
                tempdir, doctor = self.make_doctor(
                    plugin_symbols=ABI_SYMBOLS,
                    plugin_manifest_symbols=ABI_SYMBOLS,
                    native_manifest_backend_symbols=set(),
                    backend_manifest_symbols=set(),
                )
                root = pathlib.Path(tempdir.name)
                path = root / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{not json\n")
                if "native-dependency-prefix" in rel_path:
                    doctor.bundle["diagnostics"]["dependencyPrefix"] = rel_path
                if "runtime-lifecycle" in rel_path:
                    doctor.bundle["diagnostics"]["runtimeLifecycle"] = rel_path
                if "conformance/" in rel_path:
                    doctor.bundle["diagnostics"]["conformanceResults"] = (
                        "diagnostics/conformance"
                    )
                    (path.parent / "raw-protocol.log").write_text("")
                with tempdir:
                    validate(doctor)

                self.assertIn(expected, "\n".join(doctor.errors))

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

    def test_platform_baseline_must_match_bundle_target(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        baseline_path = pathlib.Path(tempdir.name) / "diagnostics" / "platform-baseline.json"
        baseline = json.loads(baseline_path.read_text())
        baseline["target"] = "other-target"
        baseline_path.write_text(json.dumps(baseline) + "\n")
        with tempdir:
            doctor.validate_platform_baseline()

        self.assertIn(
            "platform baseline target mismatch",
            "\n".join(doctor.errors),
        )

    def test_platform_baseline_requires_system_and_machine(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        baseline_path = pathlib.Path(tempdir.name) / "diagnostics" / "platform-baseline.json"
        baseline = json.loads(baseline_path.read_text())
        baseline["system"] = ""
        del baseline["machine"]
        baseline_path.write_text(json.dumps(baseline) + "\n")
        with tempdir:
            doctor.validate_platform_baseline()

        errors = "\n".join(doctor.errors)
        self.assertIn("platform baseline is missing system", errors)
        self.assertIn("platform baseline is missing machine", errors)

    def test_linux_platform_baseline_requires_ubuntu_2404(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        doctor.bundle["target"] = "aarch64-unknown-linux-gnu"
        baseline_path = pathlib.Path(tempdir.name) / "diagnostics" / "platform-baseline.json"
        baseline_path.write_text(
            json.dumps(
                {
                    "format": "libpglite-native-platform-baseline-v1",
                    "target": "aarch64-unknown-linux-gnu",
                    "system": "Linux",
                    "machine": "aarch64",
                    "baseline": {
                        "kind": "linux-distro",
                        "id": "debian",
                        "versionId": "12",
                    },
                    "osRelease": {
                        "id": "debian",
                        "versionId": "12",
                    },
                    "libcVersionLine": "ldd (Debian GLIBC) 2.36",
                }
            )
            + "\n"
        )
        with tempdir:
            doctor.validate_platform_baseline()

        self.assertIn(
            "Linux platform baseline must be ubuntu 24.04",
            "\n".join(doctor.errors),
        )

    def test_macos_platform_baseline_must_match_native_manifest(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        doctor.bundle["target"] = "aarch64-apple-darwin"
        baseline_path = pathlib.Path(tempdir.name) / "diagnostics" / "platform-baseline.json"
        baseline_path.write_text(
            json.dumps(
                {
                    "format": "libpglite-native-platform-baseline-v1",
                    "target": "aarch64-apple-darwin",
                    "system": "Darwin",
                    "machine": "arm64",
                    "baseline": {
                        "kind": "macos-deployment-target",
                        "deploymentTarget": "12.0",
                    },
                }
            )
            + "\n"
        )
        with tempdir:
            doctor.validate_platform_baseline()

        self.assertIn(
            "macOS platform baseline deployment target mismatch",
            "\n".join(doctor.errors),
        )

    def test_macos_build_provenance_must_match_native_manifest(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        doctor.bundle["target"] = "aarch64-apple-darwin"
        build_provenance = (
            pathlib.Path(tempdir.name) / "diagnostics" / "build-provenance.txt"
        )
        build_provenance.write_text(
            build_provenance.read_text().replace(
                "macos_deployment_target=11.0", "macos_deployment_target=12.0"
            )
        )
        with tempdir:
            doctor.validate_build_provenance()

        self.assertIn(
            "build provenance macos_deployment_target mismatch",
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

    def test_malformed_extension_inventory_lines_are_diagnostics(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
            extension_inventory_text=(
                "format=libpglite-native-extension-inventory-v1\n"
                "other_extension\n"
                "other_extension=;status=present\n"
                "unexpected_extension=vector\n"
            ),
        )
        with tempdir:
            doctor.validate_extensions()

        errors = "\n".join(doctor.errors)
        self.assertIn("extension inventory line is malformed: other_extension", errors)
        self.assertIn("extension inventory other_extension is missing name", errors)
        self.assertIn("extension inventory has unknown entry: unexpected_extension", errors)

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

    def test_present_other_extension_control_file_gate_covers_full_pglite_set(self):
        inventory_lines = ["format=libpglite-native-extension-inventory-v1"]
        for extension in PGLITE_OTHER_EXTENSIONS:
            inventory_lines.append(
                f"other_extension={extension};"
                f"source=pglite/other_extensions/{extension};"
                "submodule_state=?;"
                "submodule_commit=35ab919bf5da677709b2ebb8be07480bb25e97cf;"
                "status=present;"
                f"submodule_url=https://example.com/{extension}.git"
            )
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
            extension_inventory_text="\n".join(inventory_lines) + "\n",
        )
        with tempdir:
            doctor.validate_extensions()

        errors = "\n".join(doctor.errors)
        for extension in PGLITE_OTHER_EXTENSIONS:
            self.assertIn(
                f"inventoried extension is missing control file: {extension}",
                errors,
            )

    def test_present_other_extension_requires_default_version_install_sql(self):
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
        self.write_packaged_extension(
            doctor,
            "vector",
            "default_version = '0.8.1'\n",
            {"vector--0.1.0.sql": "select 1;\n"},
        )
        with tempdir:
            doctor.validate_extensions()

        self.assertIn(
            "extension has no SQL install path to default_version 0.8.1: vector",
            "\n".join(doctor.errors),
        )

    def test_present_other_extension_requires_referenced_native_module(self):
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
        self.write_packaged_extension(
            doctor,
            "vector",
            "default_version = '0.8.1'\nmodule_pathname = '$libdir/vector'\n",
            {
                "vector--0.8.1.sql": (
                    "create function vector_recv() returns void "
                    "as 'MODULE_PATHNAME';\n"
                )
            },
        )
        with tempdir:
            doctor.validate_extensions()

        self.assertIn(
            "extension vector references missing native module: vector",
            "\n".join(doctor.errors),
        )

    def test_present_postgis_requires_projection_data(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
            extension_inventory_text=(
                "format=libpglite-native-extension-inventory-v1\n"
                "other_extension=postgis;"
                "source=pglite/other_extensions/postgis;"
                "submodule_state=?;"
                "submodule_commit=35ab919bf5da677709b2ebb8be07480bb25e97cf;"
                "status=present;"
                "submodule_url=https://github.com/postgis/postgis.git\n"
            ),
        )
        self.write_packaged_extension(
            doctor,
            "postgis",
            "default_version = '3.5.2'\nmodule_pathname = '$libdir/postgis-3'\n",
            {"postgis--3.5.2.sql": "select '$libdir/postgis-3';\n"},
            modules=["postgis-3.dylib"],
        )
        with tempdir:
            doctor.validate_extensions()

        self.assertIn(
            "PostGIS projection data is missing: postgres/share/proj/proj.db",
            "\n".join(doctor.errors),
        )

    def test_present_postgis_requires_readable_projection_database(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
            extension_inventory_text=(
                "format=libpglite-native-extension-inventory-v1\n"
                "other_extension=postgis;"
                "source=pglite/other_extensions/postgis;"
                "submodule_state=?;"
                "submodule_commit=35ab919bf5da677709b2ebb8be07480bb25e97cf;"
                "status=present;"
                "submodule_url=https://github.com/postgis/postgis.git\n"
            ),
        )
        self.write_packaged_extension(
            doctor,
            "postgis",
            "default_version = '3.5.2'\nmodule_pathname = '$libdir/postgis-3'\n",
            {"postgis--3.5.2.sql": "select '$libdir/postgis-3';\n"},
            modules=["postgis-3.dylib"],
        )
        proj_dir = pathlib.Path(tempdir.name) / "postgres" / "share" / "proj"
        proj_dir.mkdir(parents=True)
        (proj_dir / "proj.db").write_text("not sqlite\n")
        with tempdir:
            doctor.validate_extensions()

        self.assertIn(
            "PostGIS projection data is not a readable SQLite database",
            "\n".join(doctor.errors),
        )

    def test_dependency_manifest_blocks_local_provider_in_strict_mode(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        dependency_manifest = pathlib.Path(tempdir.name) / "diagnostics" / "dependencies.json"
        manifest = json.loads(dependency_manifest.read_text())
        manifest["objects"][0]["dependencies"] = [
            {
                "raw": "/opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib",
                "path": "/opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib",
                "classification": "local-provider",
            }
        ]
        dependency_manifest.write_text(json.dumps(manifest) + "\n")
        with tempdir:
            doctor.validate_dependencies()

        self.assertIn(
            "dependency manifest contains non-relocatable or unresolved dependencies",
            "\n".join(doctor.errors),
        )

    def test_dependency_manifest_blocks_loader_relative_parent_traversal(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        dependency_manifest = pathlib.Path(tempdir.name) / "diagnostics" / "dependencies.json"
        manifest = json.loads(dependency_manifest.read_text())
        manifest["objects"][0]["dependencies"] = [
            {
                "raw": "@loader_path/../../outside/libcrypto.3.dylib",
                "path": "@loader_path/../../outside/libcrypto.3.dylib",
                "classification": "loader-relative",
            },
            {
                "raw": "$ORIGIN/../outside/libgeos.so",
                "path": "$ORIGIN/../outside/libgeos.so",
                "classification": "loader-relative",
            },
        ]
        dependency_manifest.write_text(json.dumps(manifest) + "\n")
        with tempdir:
            doctor.validate_dependencies()

        errors = "\n".join(doctor.errors)
        self.assertIn("loader-relative parent traversal", errors)
        self.assertIn("@loader_path/../../outside/libcrypto.3.dylib", errors)
        self.assertIn("$ORIGIN/../outside/libgeos.so", errors)

    def test_dependency_manifest_must_match_raw_dependency_report_objects(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        dependency_manifest = pathlib.Path(tempdir.name) / "diagnostics" / "dependencies.json"
        manifest = json.loads(dependency_manifest.read_text())
        manifest["objects"][0]["path"] = "postgres/lib/old.dylib"
        dependency_manifest.write_text(json.dumps(manifest) + "\n")
        with tempdir:
            doctor.validate_dependencies()

        self.assertIn(
            "dependency manifest does not correspond to dependencies.txt",
            "\n".join(doctor.errors),
        )

    def test_dependency_manifest_platform_must_match_bundle_target(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        doctor.bundle["target"] = "x86_64-unknown-linux-gnu"
        dependency_manifest = pathlib.Path(tempdir.name) / "diagnostics" / "dependencies.json"
        manifest = json.loads(dependency_manifest.read_text())
        manifest["platform"] = "Darwin"
        manifest["tool"] = "ldd"
        dependency_manifest.write_text(json.dumps(manifest) + "\n")
        with tempdir:
            doctor.validate_dependencies()

        self.assertIn(
            "dependency manifest platform mismatch",
            "\n".join(doctor.errors),
        )

    def test_dependency_manifest_tool_must_match_platform(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        dependency_manifest = pathlib.Path(tempdir.name) / "diagnostics" / "dependencies.json"
        manifest = json.loads(dependency_manifest.read_text())
        manifest["platform"] = "Darwin"
        manifest["tool"] = "ldd"
        dependency_manifest.write_text(json.dumps(manifest) + "\n")
        with tempdir:
            doctor.validate_dependencies()

        self.assertIn(
            "dependency manifest tool mismatch",
            "\n".join(doctor.errors),
        )

    def test_dependency_prefix_manifest_must_be_complete_when_present(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        diagnostics = pathlib.Path(tempdir.name) / "diagnostics"
        (diagnostics / "native-dependency-prefix.json").write_text(
            json.dumps(
                {
                    "format": "libpglite-native-dependency-prefix-v1",
                    "complete": False,
                    "missing": ["openssl:lib/libcrypto.a"],
                    "dependencies": [],
                }
            )
            + "\n"
        )
        doctor.bundle["diagnostics"]["dependencyPrefix"] = (
            "diagnostics/native-dependency-prefix.json"
        )
        with tempdir:
            doctor.validate_dependencies()

        errors = "\n".join(doctor.errors)
        self.assertIn("dependency prefix manifest is not complete", errors)
        self.assertIn("dependency prefix manifest dependencies must be nonempty", errors)

    def test_production_package_requires_dependency_prefix_diagnostic(self):
        tempdir, doctor = self.make_doctor(
            plugin_symbols=ABI_SYMBOLS,
            plugin_manifest_symbols=ABI_SYMBOLS,
            native_manifest_backend_symbols=set(),
            backend_manifest_symbols=set(),
        )
        doctor.bundle["releaseMode"] = "production"
        with tempdir:
            doctor.validate_dependencies()

        self.assertIn(
            "production package is missing diagnostics.dependencyPrefix",
            "\n".join(doctor.errors),
        )


if __name__ == "__main__":
    unittest.main()
