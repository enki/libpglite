#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile


ABI_SYMBOLS = {
    "libpglite_plugin_abi_version",
    "libpglite_plugin_buffer_free",
    "libpglite_plugin_runtime_create",
    "libpglite_plugin_runtime_destroy",
    "libpglite_plugin_runtime_exec_protocol_raw",
    "libpglite_plugin_runtime_shutdown",
}
RAW_PROTOCOL_CASES = {
    "startup",
    "simple-query",
    "transaction-rollback",
    "transaction-commit",
    "recoverable-protocol-error",
    "extended-query",
    "parameterized-extended-query",
    "deterministic-shutdown",
}
POSTGRES_PREFIX_LAYOUT = {
    "path": "postgres",
    "bin": "postgres/bin",
    "share": "postgres/share",
    "lib": "postgres/lib",
    "initdb": "postgres/bin/initdb",
    "postgres": "postgres/bin/postgres",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a packaged libpglite native plugin artifact."
    )
    parser.add_argument("package", help="extracted package directory or .tar.zst asset")
    parser.add_argument(
        "--strict-relocatable",
        action="store_true",
        help="fail on build-machine dependency paths even for development packages",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the packaged plugin/prefix runtime smoke test from the artifact",
    )
    args = parser.parse_args()

    package = pathlib.Path(args.package)
    with maybe_extract(package) as root:
        doctor = Doctor(root, strict_relocatable=args.strict_relocatable)
        doctor.run()
        if args.self_test and not doctor.errors:
            doctor.run_self_test()
        return doctor.finish()


class maybe_extract:
    def __init__(self, package: pathlib.Path):
        self.package = package
        self.tempdir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> pathlib.Path:
        if self.package.is_dir():
            return self.package
        if not self.package.is_file():
            raise SystemExit(f"package not found: {self.package}")

        self.tempdir = tempfile.TemporaryDirectory()
        subprocess.run(
            ["tar", "--zstd", "-xf", str(self.package), "-C", self.tempdir.name],
            check=True,
        )
        return pathlib.Path(self.tempdir.name)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.tempdir is not None:
            self.tempdir.cleanup()


class Doctor:
    def __init__(self, root: pathlib.Path, strict_relocatable: bool):
        self.root = root
        self.strict_relocatable = strict_relocatable
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.bundle: dict = {}
        self.actual_plugin_symbols: set[str] | None = None

    def run(self) -> None:
        self.bundle = self.read_bundle()
        self.validate_bundle()
        self.validate_plugin()
        self.validate_postgres_prefix()
        self.validate_native_only_payload()
        self.validate_diagnostics()
        self.validate_build_provenance()
        self.validate_platform_baseline()
        self.validate_source_provenance()
        self.validate_lifecycle()
        self.validate_conformance()
        self.validate_extensions()
        self.validate_dependencies()

    def finish(self) -> int:
        for warning in self.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for error in self.errors:
            print(f"error: {error}", file=sys.stderr)
        if self.errors:
            return 1
        print(f"libpglite native package ok: {self.root}")
        return 0

    def read_bundle(self) -> dict:
        bundle_path = self.root / "libpglite-native-bundle.json"
        if not bundle_path.is_file():
            self.errors.append("missing libpglite-native-bundle.json")
            return {}
        try:
            with bundle_path.open() as handle:
                bundle = json.load(handle)
        except Exception as err:
            self.errors.append(f"bundle JSON is not readable: {err}")
            return {}
        if not isinstance(bundle, dict):
            self.errors.append("bundle JSON root must be an object")
            return {}
        return bundle

    def validate_bundle(self) -> None:
        required = [
            "target",
            "pluginAbiVersion",
            "libpgliteReleaseVersion",
            "libpgliteGitCommit",
            "releaseMode",
            "runtimeStatus",
            "plugin",
            "postgresPrefix",
            "diagnostics",
        ]
        for key in required:
            if key not in self.bundle:
                self.errors.append(f"bundle is missing {key}")

        release_mode = self.bundle.get("releaseMode")
        if release_mode not in {"development", "production"}:
            self.errors.append(f"unsupported releaseMode: {release_mode!r}")
        if self.bundle.get("pluginAbiVersion") != 1:
            self.errors.append("pluginAbiVersion must be 1")

    def validate_plugin(self) -> None:
        plugin = self.bundle.get("plugin")
        if not isinstance(plugin, dict):
            self.errors.append("bundle plugin field must be an object")
            return
        filename = plugin.get("filename")
        expected_sha = plugin.get("sha256")
        if not isinstance(filename, str) or not filename:
            self.errors.append("bundle plugin.filename is missing")
            return

        plugin_path = self.root / filename
        if not plugin_path.is_file():
            self.errors.append(f"plugin binary is missing: {filename}")
            return
        actual_sha = sha256(plugin_path)
        if expected_sha != actual_sha:
            self.errors.append(
                f"plugin checksum mismatch: bundle={expected_sha} actual={actual_sha}"
            )

        actual_symbols = defined_symbols(plugin_path)
        self.actual_plugin_symbols = actual_symbols
        missing = sorted(ABI_SYMBOLS - actual_symbols)
        if missing:
            self.errors.append(f"plugin binary is missing ABI symbols: {', '.join(missing)}")

    def validate_postgres_prefix(self) -> None:
        prefix = self.bundle.get("postgresPrefix")
        if not isinstance(prefix, dict):
            self.errors.append("bundle postgresPrefix field must be an object")
            return
        for key, expected in POSTGRES_PREFIX_LAYOUT.items():
            if prefix.get(key) != expected:
                self.errors.append(
                    f"bundle postgresPrefix.{key} must be {expected!r}, got {prefix.get(key)!r}"
                )
        for key in ["path", "bin", "share", "lib", "initdb", "postgres"]:
            value = prefix.get(key)
            if not isinstance(value, str) or not value:
                self.errors.append(f"bundle postgresPrefix.{key} is missing")
                continue
            path = self.root / value
            if key in {"path", "bin", "share", "lib"}:
                if not path.is_dir():
                    self.errors.append(f"postgresPrefix.{key} directory is missing: {value}")
            elif not path.is_file():
                self.errors.append(f"postgresPrefix.{key} file is missing: {value}")

        for rel in [
            "postgres/bin/pg_config",
            "postgres/lib/pgxs/src/Makefile.global",
            "postgres/lib/pgxs/src/makefiles/pgxs.mk",
            "postgres/share/postgres.bki",
            "postgres/share/snowball_create.sql",
            "postgres/share/extension/plpgsql.control",
        ]:
            if not (self.root / rel).is_file():
                self.errors.append(f"required PostgreSQL prefix file is missing: {rel}")

        self.validate_postgres_prefix_text_paths()

    def validate_postgres_prefix_text_paths(self) -> None:
        postgres = self.root / "postgres"
        if not postgres.is_dir():
            return
        text_suffixes = {".control", ".sql", ".conf", ".sample", ".txt"}
        leaked_paths: list[str] = []
        for path in sorted(postgres.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in text_suffixes:
                continue
            text = read_text(path, self.errors)
            if looks_like_build_path(text):
                leaked_paths.append(path.relative_to(self.root).as_posix())
        if leaked_paths:
            message = (
                "PostgreSQL prefix text metadata contains build-machine paths: "
                + ", ".join(leaked_paths[:10])
            )
            if self.strict_relocatable or self.bundle.get("releaseMode") == "production":
                self.errors.append(message)
            else:
                self.warnings.append(message)

    def validate_native_only_payload(self) -> None:
        forbidden_suffixes = {".wasm", ".js", ".mjs", ".bc"}
        forbidden_fragments = {"emscripten", "wasm2c"}
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            name = path.name.lower()
            if path.suffix.lower() in forbidden_suffixes:
                self.errors.append(f"native package contains non-native payload: {rel}")
                continue
            if any(fragment in name for fragment in forbidden_fragments):
                self.errors.append(f"native package contains non-native payload: {rel}")

    def validate_diagnostics(self) -> None:
        diagnostics = self.bundle.get("diagnostics")
        if not isinstance(diagnostics, dict):
            self.errors.append("bundle diagnostics field must be an object")
            return

        required = {
            "buildProvenance": "format=libpglite-native-build-provenance-v1",
            "nativeLinkManifest": "format=libpglite-native-link-manifest-v1",
            "extensionInventory": "format=libpglite-native-extension-inventory-v1",
            "dependencies": "format=libpglite-native-dependencies-v1",
        }
        for key, header in required.items():
            path = self.diagnostic_path(key)
            if path is None:
                continue
            text = read_text(path, self.errors)
            if header not in text.splitlines()[:3]:
                self.errors.append(f"diagnostic {key} has wrong or missing format header")

        for key in ["pluginDefinedSymbols", "backendExportSymbols"]:
            path = self.diagnostic_path(key)
            if path is None:
                continue
            lines = nonempty_lines(path, self.errors)
            if not lines:
                self.errors.append(f"diagnostic {key} is empty")

        symbols_path = self.diagnostic_path("pluginDefinedSymbols")
        if symbols_path is not None:
            symbols = set(nonempty_lines(symbols_path, self.errors))
            missing = sorted(ABI_SYMBOLS - symbols)
            if missing:
                self.errors.append(
                    f"pluginDefinedSymbols is missing ABI symbols: {', '.join(missing)}"
                )
            actual_symbols = self.actual_plugin_symbols
            if actual_symbols is not None:
                missing_from_manifest = sorted(actual_symbols - symbols)
                stale_manifest_symbols = sorted(symbols - actual_symbols)
                if missing_from_manifest:
                    self.errors.append(
                        "pluginDefinedSymbols is stale; missing actual plugin symbols: "
                        f"{format_symbol_delta(missing_from_manifest)}"
                    )
                if stale_manifest_symbols:
                    self.errors.append(
                        "pluginDefinedSymbols is stale; lists symbols absent from plugin: "
                        f"{format_symbol_delta(stale_manifest_symbols)}"
                    )

        backend_symbols_path = self.diagnostic_path("backendExportSymbols")
        native_manifest_path = self.diagnostic_path("nativeLinkManifest")
        if backend_symbols_path is not None:
            backend_symbols = set(nonempty_lines(backend_symbols_path, self.errors))
            if self.actual_plugin_symbols is not None:
                missing_exports = sorted(backend_symbols - self.actual_plugin_symbols)
                if missing_exports:
                    self.errors.append(
                        "backendExportSymbols lists symbols not exported by plugin: "
                        f"{format_symbol_delta(missing_exports)}"
                    )
                self.validate_extension_backend_symbol_claims(backend_symbols)
            if native_manifest_path is not None:
                manifest_symbols = set(
                    manifest_values(native_manifest_path, "backend_export_symbol", self.errors)
                )
                missing_from_diagnostic = sorted(manifest_symbols - backend_symbols)
                stale_diagnostic_symbols = sorted(backend_symbols - manifest_symbols)
                if missing_from_diagnostic:
                    self.errors.append(
                        "backendExportSymbols is missing native link manifest symbols: "
                        f"{format_symbol_delta(missing_from_diagnostic)}"
                    )
                if stale_diagnostic_symbols:
                    self.errors.append(
                        "backendExportSymbols lists symbols absent from native link manifest: "
                        f"{format_symbol_delta(stale_diagnostic_symbols)}"
                    )
            self.validate_linux_plugin_symbol_boundary(backend_symbols)

    def validate_extension_backend_symbol_claims(self, backend_symbols: set[str]) -> None:
        if self.actual_plugin_symbols is None:
            return
        lib_dir = self.root / "postgres" / "lib"
        if not lib_dir.is_dir():
            return
        missing_claims: list[str] = []
        for module in native_extension_modules(lib_dir):
            rel = module.relative_to(self.root).as_posix()
            for symbol in sorted(undefined_symbols(module)):
                if symbol in ABI_SYMBOLS:
                    continue
                if symbol in self.actual_plugin_symbols and symbol not in backend_symbols:
                    missing_claims.append(f"{rel}: {symbol}")
        if missing_claims:
            self.errors.append(
                "extension modules reference plugin-exported backend symbols missing "
                "from backendExportSymbols: "
                + "; ".join(missing_claims[:10])
            )

    def validate_linux_plugin_symbol_boundary(self, backend_symbols: set[str]) -> None:
        target = self.bundle.get("target")
        if not isinstance(target, str) or not target.endswith("linux-gnu"):
            return
        symbols_path = self.diagnostic_path("pluginDefinedSymbols")
        if symbols_path is None:
            return
        plugin_symbols = {
            symbol
            for symbol in nonempty_lines(symbols_path, self.errors)
            if not re.fullmatch(r"LIBPGLITE_PLUGIN_NATIVE_[0-9]+", symbol)
        }
        allowed = ABI_SYMBOLS | backend_symbols
        unexpected = sorted(plugin_symbols - allowed)
        if unexpected:
            self.errors.append(
                "Linux pluginDefinedSymbols contains symbols outside the host ABI "
                "and generated backend export set: "
                f"{format_symbol_delta(unexpected)}"
            )

    def validate_build_provenance(self) -> None:
        path = self.diagnostic_path("buildProvenance")
        if path is None:
            return

        values = parse_key_value_file(path, self.errors)
        expected = {
            "target": self.bundle.get("target"),
            "release_version": self.bundle.get("libpgliteReleaseVersion"),
            "release_mode": self.bundle.get("releaseMode"),
            "runtime_status": self.bundle.get("runtimeStatus"),
            "libpglite_git_commit": self.bundle.get("libpgliteGitCommit"),
        }
        for key, expected_value in expected.items():
            actual_value = values.get(key)
            if not isinstance(expected_value, str) or not expected_value:
                continue
            if actual_value != expected_value:
                self.errors.append(
                    f"build provenance {key} mismatch: "
                    f"bundle={expected_value!r} provenance={actual_value!r}"
                )

        plugin = self.bundle.get("plugin")
        if isinstance(plugin, dict):
            expected_filename = plugin.get("filename")
            expected_sha = plugin.get("sha256")
            if isinstance(expected_filename, str) and values.get("plugin_filename") != expected_filename:
                self.errors.append(
                    "build provenance plugin_filename mismatch: "
                    f"bundle={expected_filename!r} provenance={values.get('plugin_filename')!r}"
                )
            if isinstance(expected_sha, str) and values.get("plugin_sha256") != expected_sha:
                self.errors.append(
                    "build provenance plugin_sha256 mismatch: "
                    f"bundle={expected_sha!r} provenance={values.get('plugin_sha256')!r}"
                )

        diagnostics = self.bundle.get("diagnostics")
        if isinstance(diagnostics, dict):
            native_manifest = diagnostics.get("nativeLinkManifest")
            if isinstance(native_manifest, str):
                expected_native_manifest = pathlib.PurePosixPath(native_manifest).name
                if values.get("native_manifest") != expected_native_manifest:
                    self.errors.append(
                        "build provenance native_manifest mismatch: "
                        f"bundle={expected_native_manifest!r} "
                        f"provenance={values.get('native_manifest')!r}"
                    )

            extension_inventory = diagnostics.get("extensionInventory")
            if isinstance(extension_inventory, str):
                expected_extension_inventory = pathlib.PurePosixPath(extension_inventory).name
                if values.get("extension_inventory") != expected_extension_inventory:
                    self.errors.append(
                        "build provenance extension_inventory mismatch: "
                        f"bundle={expected_extension_inventory!r} "
                        f"provenance={values.get('extension_inventory')!r}"
                    )

            dependency_manifest = diagnostics.get("dependencyManifest")
            if isinstance(dependency_manifest, str):
                expected_dependency_manifest = pathlib.PurePosixPath(
                    dependency_manifest
                ).name
                if values.get("dependency_manifest") != expected_dependency_manifest:
                    self.errors.append(
                        "build provenance dependency_manifest mismatch: "
                        f"bundle={expected_dependency_manifest!r} "
                        f"provenance={values.get('dependency_manifest')!r}"
                    )

            platform_baseline = diagnostics.get("platformBaseline")
            if isinstance(platform_baseline, str):
                expected_platform_baseline = pathlib.PurePosixPath(
                    platform_baseline
                ).name
                if values.get("platform_baseline") != expected_platform_baseline:
                    self.errors.append(
                        "build provenance platform_baseline mismatch: "
                        f"bundle={expected_platform_baseline!r} "
                        f"provenance={values.get('platform_baseline')!r}"
                    )

            target = self.bundle.get("target")
            if isinstance(target, str) and target.endswith("apple-darwin"):
                native_manifest_path = self.diagnostic_path("nativeLinkManifest")
                expected_deployment_target = None
                if native_manifest_path is not None:
                    expected_deployment_target = first_manifest_value(
                        native_manifest_path, "macos_deployment_target", self.errors
                    )
                if not values.get("macos_deployment_target"):
                    self.errors.append(
                        "build provenance is missing macos_deployment_target"
                    )
                elif values.get("macos_deployment_target") != expected_deployment_target:
                    self.errors.append(
                        "build provenance macos_deployment_target mismatch: "
                        f"manifest={expected_deployment_target!r} "
                        f"provenance={values.get('macos_deployment_target')!r}"
                    )
            elif isinstance(target, str) and target.endswith("linux-gnu"):
                if values.get("linux_baseline_id") != "ubuntu" or values.get(
                    "linux_baseline_version_id"
                ) != "24.04":
                    self.errors.append(
                        "build provenance Linux baseline mismatch: "
                        f"{values.get('linux_baseline_id')!r} "
                        f"{values.get('linux_baseline_version_id')!r}"
                    )

            dependency_prefix = diagnostics.get("dependencyPrefix")
            if isinstance(dependency_prefix, str):
                expected_dependency_prefix = pathlib.PurePosixPath(dependency_prefix).name
                if values.get("dependency_prefix") != expected_dependency_prefix:
                    self.errors.append(
                        "build provenance dependency_prefix mismatch: "
                        f"bundle={expected_dependency_prefix!r} "
                        f"provenance={values.get('dependency_prefix')!r}"
                    )

        packaged_at = values.get("packaged_at_utc")
        if not isinstance(packaged_at, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", packaged_at
        ):
            self.errors.append("build provenance packaged_at_utc is missing or invalid")

        for key in ["uname", "rustc_begin", "rustc_end", "cc_begin", "cc_end"]:
            if key not in values:
                self.errors.append(f"build provenance is missing {key}")

    def validate_platform_baseline(self) -> None:
        path = self.diagnostic_path("platformBaseline")
        if path is None:
            return
        try:
            with path.open() as handle:
                baseline = json.load(handle)
        except Exception as err:
            self.errors.append(f"platform baseline diagnostic is not readable: {err}")
            return
        if not isinstance(baseline, dict):
            self.errors.append("platform baseline diagnostic root must be an object")
            return
        if baseline.get("format") != "libpglite-native-platform-baseline-v1":
            self.errors.append("platform baseline diagnostic has wrong format")
        target = self.bundle.get("target")
        if baseline.get("target") != target:
            self.errors.append(
                "platform baseline target mismatch: "
                f"bundle={target!r} diagnostic={baseline.get('target')!r}"
            )
        if isinstance(target, str) and target.endswith("linux-gnu"):
            expected = baseline.get("baseline")
            os_release = baseline.get("osRelease")
            if not isinstance(expected, dict):
                self.errors.append("Linux platform baseline is missing baseline object")
                return
            if expected.get("kind") != "linux-distro":
                self.errors.append("Linux platform baseline kind must be linux-distro")
            if expected.get("id") != "ubuntu" or expected.get("versionId") != "24.04":
                self.errors.append(
                    "Linux platform baseline must be ubuntu 24.04: "
                    f"{expected.get('id')!r} {expected.get('versionId')!r}"
                )
            if not isinstance(os_release, dict):
                self.errors.append("Linux platform baseline is missing osRelease object")
            else:
                if os_release.get("id") != expected.get("id") or os_release.get(
                    "versionId"
                ) != expected.get("versionId"):
                    self.errors.append("Linux platform baseline osRelease mismatch")
            libc_line = baseline.get("libcVersionLine")
            if not isinstance(libc_line, str) or not libc_line:
                self.errors.append("Linux platform baseline is missing libcVersionLine")
        elif isinstance(target, str) and target.endswith("apple-darwin"):
            expected = baseline.get("baseline")
            if not isinstance(expected, dict):
                self.errors.append("macOS platform baseline is missing baseline object")
                return
            if expected.get("kind") != "macos-deployment-target":
                self.errors.append(
                    "macOS platform baseline kind must be macos-deployment-target"
                )
            deployment_target = expected.get("deploymentTarget")
            if not isinstance(deployment_target, str) or not deployment_target:
                self.errors.append("macOS platform baseline is missing deploymentTarget")
            native_manifest = self.diagnostic_path("nativeLinkManifest")
            manifest_deployment_target = None
            if native_manifest is not None:
                manifest_deployment_target = first_manifest_value(
                    native_manifest, "macos_deployment_target", self.errors
                )
            if deployment_target != manifest_deployment_target:
                self.errors.append(
                    "macOS platform baseline deployment target mismatch: "
                    f"manifest={manifest_deployment_target!r} "
                    f"diagnostic={deployment_target!r}"
                )

    def validate_source_provenance(self) -> None:
        path = self.diagnostic_path("sourceProvenance")
        if path is None:
            return
        try:
            with path.open() as handle:
                provenance = json.load(handle)
        except Exception as err:
            self.errors.append(f"source provenance diagnostic is not readable: {err}")
            return
        if not isinstance(provenance, dict):
            self.errors.append("source provenance diagnostic root must be an object")
            return
        if provenance.get("format") != "libpglite-native-source-provenance-v1":
            self.errors.append("source provenance diagnostic has wrong format")
        postgres_pglite = provenance.get("postgresPglite")
        if not isinstance(postgres_pglite, dict):
            self.errors.append("source provenance postgresPglite must be an object")
        else:
            for key in ["repository", "ref", "commit"]:
                value = postgres_pglite.get(key)
                if not isinstance(value, str) or not value:
                    self.errors.append(f"source provenance postgresPglite.{key} is missing")
            commit = postgres_pglite.get("commit")
            if isinstance(commit, str) and not re.fullmatch(r"[0-9a-f]{40}", commit):
                self.errors.append("source provenance postgresPglite.commit is not a full SHA-1")

        patches = provenance.get("patches")
        if not isinstance(patches, list) or not patches:
            self.errors.append("source provenance patches must be a nonempty list")
            return

        native_manifest = self.diagnostic_path("nativeLinkManifest")
        manifest_patch_paths: set[str] = set()
        manifest_patch_digests: dict[str, str] = {}
        if native_manifest is not None:
            manifest_source = {
                "repository": first_manifest_value(native_manifest, "source_repository", self.errors),
                "ref": first_manifest_value(native_manifest, "source_ref", self.errors),
                "commit": first_manifest_value(native_manifest, "source_commit", self.errors),
            }
            if isinstance(postgres_pglite, dict):
                for key, expected_value in manifest_source.items():
                    actual_value = postgres_pglite.get(key)
                    if expected_value and actual_value != expected_value:
                        self.errors.append(
                            f"source provenance postgresPglite.{key} mismatch: "
                            f"manifest={expected_value!r} provenance={actual_value!r}"
                        )

            manifest_fingerprint = first_manifest_value(
                native_manifest, "patch_fingerprint", self.errors
            )
            provenance_fingerprint = provenance.get("patchFingerprint")
            if manifest_fingerprint and provenance_fingerprint != manifest_fingerprint:
                self.errors.append(
                    "source provenance patchFingerprint mismatch: "
                    f"manifest={manifest_fingerprint!r} "
                    f"provenance={provenance_fingerprint!r}"
                )

            manifest_patch_paths = set(manifest_values(native_manifest, "patch", self.errors))
            manifest_patch_digests = manifest_patch_sha256s(native_manifest, self.errors)

            patches_missing_digests = sorted(manifest_patch_paths - manifest_patch_digests.keys())
            digest_without_patch = sorted(manifest_patch_digests.keys() - manifest_patch_paths)
            if patches_missing_digests:
                self.errors.append(
                    "native link manifest is missing patch_sha256 entries for patches: "
                    f"{', '.join(patches_missing_digests)}"
                )
            if digest_without_patch:
                self.errors.append(
                    "native link manifest has patch_sha256 entries without patch entries: "
                    f"{', '.join(digest_without_patch)}"
                )

        seen_paths: set[str] = set()
        for patch in patches:
            if not isinstance(patch, dict):
                self.errors.append("source provenance patch entry must be an object")
                continue
            rel = patch.get("path")
            digest = patch.get("sha256")
            if not isinstance(rel, str) or not rel:
                self.errors.append("source provenance patch.path is missing")
            elif rel in seen_paths:
                self.errors.append(f"source provenance repeats patch path: {rel}")
            else:
                seen_paths.add(rel)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                self.errors.append(f"source provenance patch has invalid sha256: {rel}")
            elif isinstance(rel, str) and rel in manifest_patch_digests:
                expected_digest = manifest_patch_digests[rel]
                if digest != expected_digest:
                    self.errors.append(
                        f"source provenance patch sha256 mismatch for {rel}: "
                        f"manifest={expected_digest} provenance={digest}"
                    )

        missing = sorted(manifest_patch_paths - seen_paths)
        extra = sorted(seen_paths - manifest_patch_paths)
        if missing:
            self.errors.append(
                f"source provenance is missing native manifest patches: {', '.join(missing)}"
            )
        if extra:
            self.errors.append(
                f"source provenance lists patches absent from native manifest: {', '.join(extra)}"
            )

    def validate_lifecycle(self) -> None:
        path = self.diagnostic_path("runtimeLifecycle")
        if path is None:
            return
        try:
            with path.open() as handle:
                lifecycle = json.load(handle)
        except Exception as err:
            self.errors.append(f"runtime lifecycle diagnostic is not readable: {err}")
            return
        if not isinstance(lifecycle, dict):
            self.errors.append("runtime lifecycle diagnostic root must be an object")
            return
        expected = {
            "format": "libpglite-native-runtime-lifecycle-v1",
            "contract": "single-start-per-process",
            "restartSupported": False,
            "concurrentRuntimeSupported": False,
            "secondStartupBehavior": "fails-before-entering-postgres",
        }
        for key, value in expected.items():
            if lifecycle.get(key) != value:
                self.errors.append(f"runtime lifecycle diagnostic has wrong {key}")
        proven_by = lifecycle.get("provenByConformance")
        if not isinstance(proven_by, list) or "raw-protocol" not in proven_by:
            self.errors.append(
                "runtime lifecycle diagnostic must cite raw-protocol conformance"
            )

    def validate_conformance(self) -> None:
        diagnostics = self.bundle.get("diagnostics")
        if not isinstance(diagnostics, dict):
            return
        value = diagnostics.get("conformanceResults")
        if not isinstance(value, str) or not value:
            self.errors.append("bundle diagnostics.conformanceResults is missing")
            return
        conformance_dir = self.root / value
        if not conformance_dir.is_dir():
            self.errors.append(f"conformance diagnostics directory is missing: {value}")
            return

        for name in [
            "raw-protocol",
            "tokio-postgres-client",
            "prefix-initialize",
            "prefix-resume",
        ]:
            result_path = conformance_dir / f"{name}.json"
            log_path = conformance_dir / f"{name}.log"
            if not result_path.is_file():
                self.errors.append(f"conformance result is missing: {name}.json")
                continue
            if not log_path.is_file():
                self.errors.append(f"conformance log is missing: {name}.log")
            try:
                with result_path.open() as handle:
                    result = json.load(handle)
            except Exception as err:
                self.errors.append(f"conformance result {name}.json is not readable: {err}")
                continue
            if result.get("format") != "libpglite-native-conformance-result-v1":
                self.errors.append(f"conformance result {name}.json has wrong format")
            if result.get("name") != name:
                self.errors.append(f"conformance result {name}.json has wrong name")
            if result.get("status") != "passed":
                self.errors.append(f"conformance result {name}.json did not pass")
            if result.get("exitCode") != 0:
                self.errors.append(f"conformance result {name}.json exitCode is not 0")
            if result.get("log") != f"{name}.log":
                self.errors.append(f"conformance result {name}.json points at wrong log")
            expected_log_sha = result.get("logSha256")
            if not isinstance(expected_log_sha, str) or not expected_log_sha:
                self.errors.append(f"conformance result {name}.json is missing logSha256")
            elif log_path.is_file() and expected_log_sha != sha256(log_path):
                self.errors.append(f"conformance result {name}.json logSha256 mismatch")
            if name == "raw-protocol":
                cases = result.get("cases")
                if not isinstance(cases, list) or not all(
                    isinstance(case, str) for case in cases
                ):
                    self.errors.append("raw-protocol conformance result is missing cases")
                else:
                    missing_cases = sorted(RAW_PROTOCOL_CASES - set(cases))
                    if missing_cases:
                        self.errors.append(
                            "raw-protocol conformance result is missing cases: "
                            + ", ".join(missing_cases)
                        )

    def validate_extensions(self) -> None:
        inventory = self.diagnostic_path("extensionInventory")
        if inventory is None:
            return
        for line in nonempty_lines(inventory, self.errors):
            key, fields = parse_inventory_line(line)
            if key == "contrib_extension":
                self.validate_contrib_extension(fields["name"])
            elif key == "other_extension":
                self.validate_other_extension_inventory(fields)

    def validate_other_extension_inventory(self, fields: dict[str, str]) -> None:
        extension = fields["name"]
        source = fields.get("source")
        if source != f"pglite/other_extensions/{extension}":
            self.errors.append(
                f"PGlite other extension has wrong source path: {extension}"
            )

        commit = fields.get("submodule_commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            self.errors.append(
                f"PGlite other extension is missing pinned submodule commit: {extension}"
            )

        url = fields.get("submodule_url")
        if not isinstance(url, str) or not url.startswith("https://"):
            self.errors.append(
                f"PGlite other extension is missing submodule URL: {extension}"
            )

        status = fields.get("status")
        if status not in {"present", "missing"}:
            self.errors.append(
                f"PGlite other extension has invalid status: {extension}"
            )
        elif status == "missing":
            message = (
                "PGlite other extension submodule is missing from the native "
                f"extension inventory: {extension}"
            )
            if self.bundle.get("releaseMode") == "production":
                self.errors.append(message)
            else:
                self.warnings.append(message)
        else:
            self.validate_contrib_extension(extension)
            if extension == "postgis":
                self.validate_postgis_runtime_data()

    def validate_postgis_runtime_data(self) -> None:
        proj_db = self.root / "postgres" / "share" / "proj" / "proj.db"
        if not proj_db.is_file():
            self.errors.append(
                "PostGIS projection data is missing: postgres/share/proj/proj.db"
            )

    def validate_contrib_extension(self, extension: str) -> None:
        extension_dir = self.root / "postgres" / "share" / "extension"
        lib_dir = self.root / "postgres" / "lib"
        control = extension_dir / f"{extension}.control"
        if not control.is_file():
            self.errors.append(f"inventoried extension is missing control file: {extension}")
            return

        control_text = read_text(control, self.errors)
        default_version = control_value(control_text, "default_version")

        sql_files = sorted(extension_dir.glob(f"{extension}--*.sql"))
        if not sql_files:
            self.errors.append(f"extension has no SQL files: {extension}")
        elif default_version is None:
            self.errors.append(f"extension control is missing default_version: {extension}")
        elif not extension_can_install_version(extension, sql_files, default_version):
            self.errors.append(
                f"extension has no SQL install path to default_version {default_version}: {extension}"
            )

        module_names = set()
        module_pathname = control_value(control_text, "module_pathname")
        if module_pathname is not None:
            module_names.add(module_pathname.removeprefix("$libdir/"))
        sql_texts = [read_text(path, self.errors) for path in sql_files]
        for sql_text in sql_texts:
            module_names.update(
                match.removeprefix("$libdir/")
                for match in re.findall(r"\$libdir/[A-Za-z0-9_.+-]+", sql_text)
            )
        if not module_names and any("MODULE_PATHNAME" in text for text in sql_texts):
            module_names.add(extension)

        for module_name in sorted(module_names):
            if not native_module_exists(lib_dir, module_name):
                self.errors.append(
                    f"extension {extension} references missing native module: {module_name}"
                )

    def validate_dependencies(self) -> None:
        dependencies = self.diagnostic_path("dependencies")
        if dependencies is None:
            return
        lines = nonempty_lines(dependencies, self.errors)
        joined = "\n".join(lines)
        if "tool=otool -L" not in joined and "tool=ldd" not in joined:
            self.errors.append("dependencies diagnostic does not identify otool or ldd")

        build_paths = [
            line
            for line in lines
            if looks_like_build_path(line) and not line.startswith("binary=")
        ]
        if build_paths:
            message = (
                "dependency diagnostics include build-machine paths; this is allowed for "
                "development artifacts but blocks production relocatability"
            )
            if self.strict_relocatable or self.bundle.get("releaseMode") == "production":
                self.errors.append(message)
            else:
                self.warnings.append(message)

        dependency_manifest = self.diagnostic_path("dependencyManifest")
        if dependency_manifest is None:
            return
        try:
            with dependency_manifest.open() as handle:
                manifest = json.load(handle)
        except Exception as err:
            self.errors.append(f"dependency manifest is not readable: {err}")
            return
        if not isinstance(manifest, dict):
            self.errors.append("dependency manifest root must be an object")
            return
        if manifest.get("format") != "libpglite-native-dependencies-v1":
            self.errors.append("dependency manifest has wrong format")
        tool = manifest.get("tool")
        if tool not in {"otool -L", "ldd"}:
            self.errors.append(f"dependency manifest has unsupported tool: {tool!r}")
        objects = manifest.get("objects")
        if not isinstance(objects, list) or not objects:
            self.errors.append("dependency manifest objects must be a nonempty list")
            return

        package_object_seen = False
        bad_dependency_paths: list[str] = []
        for obj in objects:
            if not isinstance(obj, dict):
                self.errors.append("dependency manifest object entry must be an object")
                continue
            path = obj.get("path")
            kind = obj.get("kind")
            if not isinstance(path, str) or not path:
                self.errors.append("dependency manifest object path is missing")
            elif path in joined:
                package_object_seen = True
            if kind not in {"plugin", "postgres-lib"}:
                self.errors.append(f"dependency manifest object has unsupported kind: {kind!r}")
            if obj.get("toolExitCode") not in {0, None}:
                self.errors.append(f"dependency scan failed for object: {path}")
            dependencies_value = obj.get("dependencies")
            if not isinstance(dependencies_value, list):
                self.errors.append(f"dependency manifest object has invalid dependencies: {path}")
                continue
            for dependency in dependencies_value:
                if not isinstance(dependency, dict):
                    self.errors.append(f"dependency manifest dependency must be an object: {path}")
                    continue
                raw = dependency.get("raw")
                classification = dependency.get("classification")
                if not isinstance(raw, str) or not raw:
                    self.errors.append(f"dependency manifest dependency raw path is missing: {path}")
                if classification not in {
                    "package",
                    "platform",
                    "loader-relative",
                    "local-provider",
                    "build-machine",
                    "absolute-external",
                    "missing",
                    "unknown",
                }:
                    self.errors.append(
                        f"dependency manifest has unsupported classification: {classification!r}"
                    )
                    continue
                if classification in {
                    "local-provider",
                    "build-machine",
                    "absolute-external",
                    "missing",
                    "unknown",
                }:
                    bad_dependency_paths.append(f"{path}: {raw} ({classification})")

        if not package_object_seen:
            self.errors.append("dependency manifest does not correspond to dependencies.txt")
        if bad_dependency_paths:
            message = (
                "dependency manifest contains non-relocatable or unresolved dependencies: "
                + "; ".join(bad_dependency_paths[:10])
            )
            if self.strict_relocatable or self.bundle.get("releaseMode") == "production":
                self.errors.append(message)
            else:
                self.warnings.append(message)

        prefix_manifest = self.diagnostic_path_if_present("dependencyPrefix")
        if prefix_manifest is None and self.bundle.get("releaseMode") == "production":
            self.errors.append(
                "production package is missing diagnostics.dependencyPrefix"
            )
            return
        if prefix_manifest is not None:
            try:
                with prefix_manifest.open() as handle:
                    prefix = json.load(handle)
            except Exception as err:
                self.errors.append(f"dependency prefix manifest is not readable: {err}")
                return
            if not isinstance(prefix, dict):
                self.errors.append("dependency prefix manifest root must be an object")
                return
            if prefix.get("format") != "libpglite-native-dependency-prefix-v1":
                self.errors.append("dependency prefix manifest has wrong format")
            if prefix.get("complete") is not True:
                self.errors.append("dependency prefix manifest is not complete")
            dependencies_value = prefix.get("dependencies")
            if not isinstance(dependencies_value, list) or not dependencies_value:
                self.errors.append("dependency prefix manifest dependencies must be nonempty")

    def run_self_test(self) -> None:
        if shutil.which("cargo") is None:
            self.errors.append("package self-test requires cargo in PATH")
            return

        plugin = self.bundle.get("plugin")
        if not isinstance(plugin, dict):
            self.errors.append("package self-test cannot read bundle plugin field")
            return
        plugin_filename = plugin.get("filename")
        if not isinstance(plugin_filename, str) or not plugin_filename:
            self.errors.append("package self-test cannot read bundle plugin.filename")
            return
        plugin_path = self.root / plugin_filename
        if not plugin_path.is_file():
            self.errors.append(f"package self-test plugin is missing: {plugin_filename}")
            return

        postgres_prefix = self.bundle.get("postgresPrefix")
        if not isinstance(postgres_prefix, dict):
            self.errors.append("package self-test cannot read bundle postgresPrefix field")
            return
        postgres_prefix_path = postgres_prefix.get("path")
        if not isinstance(postgres_prefix_path, str) or not postgres_prefix_path:
            self.errors.append("package self-test cannot read bundle postgresPrefix.path")
            return
        postgres_root = self.root / postgres_prefix_path
        if not postgres_root.is_dir():
            self.errors.append(
                f"package self-test Postgres prefix is missing: {postgres_prefix_path}"
            )
            return

        repo_root = pathlib.Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["RUST_TEST_THREADS"] = "1"
        env["LIBPGLITE_TEST_PLUGIN_PATH"] = str(plugin_path)
        env["LIBPGLITE_TEST_POSTGRES_PREFIX"] = str(postgres_root)

        explicit_prefix_command = [
            "cargo",
            "test",
            "--features",
            "dynamic-loading",
            "--test",
            "dynamic_plugin",
            "dynamic_plugin_executes_queries_and_contrib_extensions_when_native_prefix_is_available",
            "--",
            "--nocapture",
        ]
        print("running package self-test:", " ".join(explicit_prefix_command))
        result = subprocess.run(
            explicit_prefix_command, cwd=repo_root, env=env, check=False
        )
        if result.returncode != 0:
            self.errors.append(f"package self-test failed with exit code {result.returncode}")

        bundled_env = os.environ.copy()
        bundled_env["RUST_TEST_THREADS"] = "1"
        bundled_env["LIBPGLITE_TEST_PLUGIN_DIR"] = str(self.root)
        bundled_env["LIBPGLITE_RUN_BUNDLED_PREFIX_CHILD"] = "1"
        bundled_env.pop("LIBPGLITE_TEST_PLUGIN_PATH", None)
        bundled_env.pop("LIBPGLITE_TEST_POSTGRES_PREFIX", None)
        bundled_prefix_command = [
            "cargo",
            "test",
            "--features",
            "dynamic-loading",
            "--test",
            "dynamic_plugin",
            "dynamic_plugin_uses_bundled_postgres_prefix_from_plugin_dir",
            "--",
            "--nocapture",
        ]
        print("running package self-test:", " ".join(bundled_prefix_command))
        result = subprocess.run(
            bundled_prefix_command, cwd=repo_root, env=bundled_env, check=False
        )
        if result.returncode != 0:
            self.errors.append(
                f"package bundled-prefix self-test failed with exit code {result.returncode}"
            )

    def diagnostic_path(self, key: str) -> pathlib.Path | None:
        diagnostics = self.bundle.get("diagnostics")
        if not isinstance(diagnostics, dict):
            return None
        value = diagnostics.get(key)
        if not isinstance(value, str) or not value:
            self.errors.append(f"bundle diagnostics.{key} is missing")
            return None
        path = self.root / value
        if not path.is_file():
            self.errors.append(f"diagnostic file is missing: {value}")
            return None
        return path

    def diagnostic_path_if_present(self, key: str) -> pathlib.Path | None:
        diagnostics = self.bundle.get("diagnostics")
        if not isinstance(diagnostics, dict):
            return None
        value = diagnostics.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            self.errors.append(f"bundle diagnostics.{key} is invalid")
            return None
        path = self.root / value
        if not path.is_file():
            self.errors.append(f"diagnostic file is missing: {value}")
            return None
        return path


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def defined_symbols(binary: pathlib.Path) -> set[str]:
    system = os.uname().sysname
    if system == "Darwin":
        command = ["nm", "-gU", str(binary)]
    elif system == "Linux":
        command = ["nm", "-D", "--defined-only", str(binary)]
    else:
        return set()

    result = subprocess.run(command, text=True, capture_output=True, check=False)
    symbols: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        symbol = parts[-1].removeprefix("_")
        symbol = symbol.split("@", 1)[0]
        if re.fullmatch(r"LIBPGLITE_PLUGIN_NATIVE_[0-9]+", symbol):
            continue
        symbols.add(symbol)
    return symbols


def undefined_symbols(binary: pathlib.Path) -> set[str]:
    result = subprocess.run(
        ["nm", "-u", str(binary)], text=True, capture_output=True, check=False
    )
    symbols: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        symbol = parts[-1].removeprefix("_")
        symbol = symbol.split("@", 1)[0]
        if symbol:
            symbols.add(symbol)
    return symbols


def native_extension_modules(lib_dir: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in lib_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".dylib", ".so"}
    )


def read_text(path: pathlib.Path, errors: list[str]) -> str:
    try:
        return path.read_text()
    except Exception as err:
        errors.append(f"could not read {path}: {err}")
        return ""


def nonempty_lines(path: pathlib.Path, errors: list[str]) -> list[str]:
    return [line.strip() for line in read_text(path, errors).splitlines() if line.strip()]


def parse_key_value_file(path: pathlib.Path, errors: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in nonempty_lines(path, errors):
        if "=" not in line:
            values.setdefault(line, "")
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def manifest_values(path: pathlib.Path, key: str, errors: list[str]) -> list[str]:
    values: list[str] = []
    for line in nonempty_lines(path, errors):
        if not line.startswith(f"{key}="):
            continue
        values.append(line.split("=", 1)[1])
    return values


def first_manifest_value(path: pathlib.Path, key: str, errors: list[str]) -> str | None:
    values = manifest_values(path, key, errors)
    if not values:
        return None
    if len(values) > 1:
        errors.append(f"native link manifest repeats singleton key: {key}")
    return values[0]


def manifest_patch_sha256s(path: pathlib.Path, errors: list[str]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for value in manifest_values(path, "patch_sha256", errors):
        rel, fields = parse_semicolon_fields(value)
        digest = fields.get("sha256")
        if not rel:
            errors.append("native link manifest patch_sha256 entry is missing path")
            continue
        if rel in digests:
            errors.append(f"native link manifest repeats patch_sha256 path: {rel}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"native link manifest patch_sha256 has invalid digest: {rel}")
            continue
        digests[rel] = digest
    return digests


def format_symbol_delta(symbols: list[str], limit: int = 20) -> str:
    if len(symbols) <= limit:
        return ", ".join(symbols)
    shown = ", ".join(symbols[:limit])
    return f"{shown}, ... ({len(symbols) - limit} more)"


def parse_inventory_line(line: str) -> tuple[str, dict[str, str]]:
    key, raw_value = line.split("=", 1)
    name, fields = parse_semicolon_fields(raw_value)
    fields["name"] = name
    return key, fields


def parse_semicolon_fields(raw_value: str) -> tuple[str, dict[str, str]]:
    parts = raw_value.split(";")
    fields = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        field, value = part.split("=", 1)
        fields[field] = value
    return parts[0], fields


def control_value(control_text: str, key: str) -> str | None:
    for line in control_text.splitlines():
        match = re.match(rf"^\s*{re.escape(key)}\s*=\s*'([^']+)'\s*$", line)
        if match:
            return match.group(1)
    return None


def native_module_exists(lib_dir: pathlib.Path, module_name: str) -> bool:
    module = pathlib.PurePosixPath(module_name).name
    candidates = [
        lib_dir / module,
        lib_dir / f"{module}.dylib",
        lib_dir / f"{module}.so",
    ]
    return any(candidate.is_file() for candidate in candidates)


def extension_can_install_version(
    extension: str, sql_files: list[pathlib.Path], version: str
) -> bool:
    base_versions: set[str] = set()
    upgrade_edges: dict[str, set[str]] = {}
    pattern = re.compile(
        rf"^{re.escape(extension)}--(.+?)(?:--(.+?))?\.sql$"
    )
    for sql_file in sql_files:
        match = pattern.match(sql_file.name)
        if match is None:
            continue
        source_version, target_version = match.groups()
        if target_version is None:
            base_versions.add(source_version)
        else:
            upgrade_edges.setdefault(source_version, set()).add(target_version)

    if version in base_versions:
        return True

    seen = set(base_versions)
    pending = list(base_versions)
    while pending:
        current = pending.pop()
        for next_version in upgrade_edges.get(current, set()):
            if next_version == version:
                return True
            if next_version not in seen:
                seen.add(next_version)
                pending.append(next_version)
    return False


def looks_like_build_path(line: str) -> bool:
    if re.search(r"(/Users/|/home/|/private/var/|/tmp/|/var/folders/|/opt/homebrew/)", line):
        return True
    return False


if __name__ == "__main__":
    if shutil.which("tar") is None:
        raise SystemExit("missing required command: tar")
    raise SystemExit(main())
