#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dependency_prefix="${LIBPGLITE_NATIVE_DEPENDENCY_PREFIX:-"$repo_root/target/native-pglite/dependency-prefix"}"
build_dependency_prefix=1
allow_postgis_gap=0

usage() {
  cat >&2 <<'USAGE'
usage: scripts/check-native-other-extension-build.sh [--allow-postgis-gap] [--skip-dependency-prefix-build]

Builds the controlled native dependency prefix, materializes pinned PGlite
other_extensions, builds the native PGXS extensions, and verifies that the
generated Postgres prefix contains the installed extension artifacts.

By default every inventoried PGlite other_extension must be built. The
--allow-postgis-gap flag is retained only for diagnosing older partial builds;
normal macOS proof runs must not use it.

Environment:
  LIBPGLITE_NATIVE_DEPENDENCY_PREFIX=<path>
  LIBPGLITE_NATIVE_MAKE_JOBS=<jobs>
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-postgis-gap)
      allow_postgis_gap=1
      shift
      ;;
    --skip-dependency-prefix-build)
      build_dependency_prefix=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

case "$dependency_prefix" in
  /*) ;;
  *) dependency_prefix="$repo_root/$dependency_prefix" ;;
esac

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 2
  fi
}

require python3
require rustc

cd "$repo_root"

if [[ "$build_dependency_prefix" == "1" ]]; then
  scripts/build-native-dependency-prefix.sh --prefix "$dependency_prefix"
fi

scripts/prepare-native-pglite-link.sh \
  --build-postgres \
  --dependency-prefix "$dependency_prefix" \
  --fetch-other-extensions \
  --build-other-extensions

target="$(rustc -vV | awk -F': ' '$1 == "host" {print $2}')"
manifest="$repo_root/target/native-pglite/$target/libpglite_native_link_manifest.txt"

python3 - "$manifest" "$allow_postgis_gap" <<'PY'
import pathlib
import re
import sys

manifest = pathlib.Path(sys.argv[1])
allow_postgis_gap = sys.argv[2] == "1"
errors: list[str] = []
warnings: list[str] = []

if not manifest.is_file():
    raise SystemExit(f"native link manifest is missing: {manifest}")

values: dict[str, str] = {}
other_extensions: list[dict[str, str]] = []
for raw in manifest.read_text().splitlines():
    if "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    values.setdefault(key, value)
    if key == "extension_other_extension":
        name, *field_parts = value.split(";")
        fields = {"name": name}
        for part in field_parts:
            if "=" not in part:
                continue
            field_key, field_value = part.split("=", 1)
            fields[field_key] = field_value
        other_extensions.append(fields)

postgres_prefix = pathlib.Path(values.get("postgres_install_prefix", ""))
if not postgres_prefix.is_dir():
    errors.append(f"postgres_install_prefix is missing: {postgres_prefix}")

extension_dir = postgres_prefix / "share" / "extension"
lib_dir = postgres_prefix / "lib"
allowed_gaps = {"postgis"} if allow_postgis_gap else set()

if not other_extensions:
    errors.append("manifest has no PGlite other_extensions entries")

for extension in other_extensions:
    name = extension["name"]
    if extension.get("status") != "present":
        errors.append(f"other_extension source is not materialized: {name}")
        continue

    control = extension_dir / f"{name}.control"
    if not control.is_file():
        message = f"other_extension is missing installed control file: {name}"
        if name in allowed_gaps:
            warnings.append(message)
            continue
        errors.append(message)
        continue

    sql_files = sorted(extension_dir.glob(f"{name}--*.sql"))
    if not sql_files:
        errors.append(f"other_extension has no installed SQL files: {name}")

    control_text = control.read_text(errors="replace")
    module_names = set()
    module_match = re.search(
        r"(?m)^\s*module_pathname\s*=\s*['\"]?([^'\"\s]+)", control_text
    )
    if module_match is not None:
        module_names.add(module_match.group(1).removeprefix("$libdir/"))
    sql_texts = [path.read_text(errors="replace") for path in sql_files]
    for sql_text in sql_texts:
        module_names.update(
            match.removeprefix("$libdir/")
            for match in re.findall(r"\$libdir/[A-Za-z0-9_.+-]+", sql_text)
        )
    if not module_names and any("MODULE_PATHNAME" in sql_text for sql_text in sql_texts):
        module_names.add(name)

    for module in sorted(module_names):
        candidates = [
            lib_dir / module,
            lib_dir / f"{module}.dylib",
            lib_dir / f"{module}.so",
            lib_dir / f"{module}.bundle",
        ]
        if not any(candidate.is_file() for candidate in candidates):
            errors.append(f"other_extension references missing native module: {name} -> {module}")

if warnings:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

if errors:
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    raise SystemExit(1)

built = sorted(
    extension["name"]
    for extension in other_extensions
    if extension["name"] not in allowed_gaps
)
print("native PGlite other_extension build ok:", ", ".join(built))
if allowed_gaps:
    print("allowed native parity gap:", ", ".join(sorted(allowed_gaps)))
PY
