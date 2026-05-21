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

    def run(self) -> None:
        self.bundle = self.read_bundle()
        self.validate_bundle()
        self.validate_plugin()
        self.validate_postgres_prefix()
        self.validate_diagnostics()
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
        missing = sorted(ABI_SYMBOLS - actual_symbols)
        if missing:
            self.errors.append(f"plugin binary is missing ABI symbols: {', '.join(missing)}")

    def validate_postgres_prefix(self) -> None:
        prefix = self.bundle.get("postgresPrefix")
        if not isinstance(prefix, dict):
            self.errors.append("bundle postgresPrefix field must be an object")
            return
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
            "postgres/share/postgres.bki",
            "postgres/share/snowball_create.sql",
            "postgres/share/extension/plpgsql.control",
        ]:
            if not (self.root / rel).is_file():
                self.errors.append(f"required PostgreSQL prefix file is missing: {rel}")

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
        if native_manifest is not None:
            for line in nonempty_lines(native_manifest, self.errors):
                if line.startswith("patch="):
                    manifest_patch_paths.add(line.split("=", 1)[1])

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

    def validate_extensions(self) -> None:
        inventory = self.diagnostic_path("extensionInventory")
        if inventory is None:
            return
        for line in nonempty_lines(inventory, self.errors):
            key, fields = parse_inventory_line(line)
            if key == "contrib_extension":
                self.validate_contrib_extension(fields["name"])
            elif key == "other_extension" and fields.get("status") == "missing":
                message = (
                    "PGlite other extension submodule is missing from the native "
                    f"extension inventory: {fields['name']}"
                )
                if self.bundle.get("releaseMode") == "production":
                    self.errors.append(message)
                else:
                    self.warnings.append(message)

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
        if not build_paths:
            return

        message = (
            "dependency diagnostics include build-machine paths; this is allowed for "
            "development artifacts but blocks production relocatability"
        )
        if self.strict_relocatable or self.bundle.get("releaseMode") == "production":
            self.errors.append(message)
        else:
            self.warnings.append(message)

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

        command = [
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
        print("running package self-test:", " ".join(command))
        result = subprocess.run(command, cwd=repo_root, env=env, check=False)
        if result.returncode != 0:
            self.errors.append(f"package self-test failed with exit code {result.returncode}")

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
        symbols.add(symbol)
    return symbols


def read_text(path: pathlib.Path, errors: list[str]) -> str:
    try:
        return path.read_text()
    except Exception as err:
        errors.append(f"could not read {path}: {err}")
        return ""


def nonempty_lines(path: pathlib.Path, errors: list[str]) -> list[str]:
    return [line.strip() for line in read_text(path, errors).splitlines() if line.strip()]


def parse_inventory_line(line: str) -> tuple[str, dict[str, str]]:
    key, raw_value = line.split("=", 1)
    parts = raw_value.split(";")
    fields = {"name": parts[0]}
    for part in parts[1:]:
        if "=" not in part:
            continue
        field, value = part.split("=", 1)
        fields[field] = value
    return key, fields


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
