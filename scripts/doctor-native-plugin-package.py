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
    args = parser.parse_args()

    package = pathlib.Path(args.package)
    with maybe_extract(package) as root:
        doctor = Doctor(root, strict_relocatable=args.strict_relocatable)
        doctor.run()
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

        for name in ["raw-protocol", "tokio-postgres-client"]:
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

    def validate_extensions(self) -> None:
        inventory = self.diagnostic_path("extensionInventory")
        if inventory is None:
            return
        for line in nonempty_lines(inventory, self.errors):
            if not line.startswith("contrib_extension="):
                continue
            extension = line.split("=", 1)[1].split(";", 1)[0]
            control = self.root / "postgres" / "share" / "extension" / f"{extension}.control"
            if not control.is_file():
                self.errors.append(f"inventoried extension is missing control file: {extension}")

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


def looks_like_build_path(line: str) -> bool:
    if re.search(r"(/Users/|/home/|/private/var/|/tmp/|/var/folders/)", line):
        return True
    return False


if __name__ == "__main__":
    if shutil.which("tar") is None:
        raise SystemExit("missing required command: tar")
    raise SystemExit(main())
