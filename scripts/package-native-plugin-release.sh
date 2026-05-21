#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${1:-${GITHUB_REF_NAME:-}}"
plugin_binary="${2:-}"
out_dir="${3:-"$repo_root/dist/native-plugin"}"
release_mode="${LIBPGLITE_RELEASE_MODE:-development}"

usage() {
  cat >&2 <<'USAGE'
usage: scripts/package-native-plugin-release.sh <version> <plugin-binary> [out-dir]

Packages a libpglite native plugin release asset and release metadata. The
plugin may still contain an unimplemented native runtime while ADR-0002 is open,
but the plugin ABI, package layout, metadata, and checksum contract are real.

Environment:
  LIBPGLITE_RELEASE_MODE=development|production
  LIBPGLITE_CONFORMANCE_DIR=<dir>         structured native conformance results
USAGE
}

if [[ "$version" == "-h" || "$version" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "$version" || -z "$plugin_binary" ]]; then
  usage
  exit 2
fi

if [[ ! -f "$plugin_binary" ]]; then
  echo "plugin binary not found: $plugin_binary" >&2
  exit 2
fi

case "$version" in
  v*) release_version="$version" ;;
  *) release_version="v$version" ;;
esac

case "$release_mode" in
  development|production) ;;
  *)
    echo "unsupported LIBPGLITE_RELEASE_MODE: $release_mode" >&2
    exit 2
    ;;
esac

runtime_status="native-runtime-pending-adr-0002"
if [[ "$release_mode" == "production" ]]; then
  mapfile -t open_adrs < <(find "$repo_root/docs" -maxdepth 1 -type f -name 'ADR-*.md' | LC_ALL=C sort)
  if (( ${#open_adrs[@]} > 0 )); then
    echo "production packaging is blocked while release-gating ADRs remain open:" >&2
    printf '  %s\n' "${open_adrs[@]#$repo_root/}" >&2
    exit 1
  fi
  runtime_status="runtime-ready"
fi

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 2
  fi
}

require git
require tar
require zstd
require python3
require nm
case "$(uname -s)" in
  Darwin)
    require otool
    require install_name_tool
    require codesign
    ;;
  Linux) require ldd ;;
esac

platform="$(uname -m)-$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$platform" in
  arm64-darwin) platform="aarch64-apple-darwin" ;;
  x86_64-darwin) platform="x86_64-apple-darwin" ;;
  aarch64-linux) platform="aarch64-unknown-linux-gnu" ;;
  x86_64-linux) platform="x86_64-unknown-linux-gnu" ;;
  *)
    echo "unsupported native plugin release platform: $platform" >&2
    exit 2
    ;;
esac

case "$platform" in
  *apple-darwin) expected_plugin="liblibpglite_plugin_native.dylib" ;;
  *linux-gnu) expected_plugin="liblibpglite_plugin_native.so" ;;
  *)
    echo "unsupported native plugin release target: $platform" >&2
    exit 2
    ;;
esac

if [[ "$(basename "$plugin_binary")" != "$expected_plugin" ]]; then
  echo "plugin binary must be named $expected_plugin for $platform, got $(basename "$plugin_binary")" >&2
  exit 2
fi

mkdir -p "$out_dir"

binary_asset="$out_dir/libpglite-plugin-native-${release_version}-${platform}.tar.zst"
source_asset="$out_dir/libpglite-plugin-native-${release_version}-source.tar.zst"
notice_asset="$out_dir/libpglite-plugin-native-${release_version}-NOTICE.txt"
inventory_asset="$out_dir/libpglite-plugin-native-${release_version}-licenses.json"
source_txt_asset="$out_dir/libpglite-plugin-native-${release_version}-SOURCE.txt"
checksums_asset="$out_dir/libpglite-plugin-native-${release_version}-checksums.txt"

sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

defined_symbols() {
  local binary="$1"
  case "$(uname -s)" in
    Darwin) nm -gU "$binary" | awk '{print $NF}' | sed 's/^_//' ;;
    Linux) nm -D --defined-only "$binary" | awk '{print $NF}' | sed 's/@@.*//' | sed 's/@.*//' ;;
    *) return 2 ;;
  esac
}

dependency_report() {
  local out="$1"
  local package_root="$2"
  local binary="$3"
  local postgres_lib_dir="$4"
  local json_out="$5"

  python3 "$repo_root/scripts/generate-native-dependency-manifest.py" \
    --package-root "$package_root" \
    --plugin "$binary" \
    --postgres-lib-dir "$postgres_lib_dir" \
    --text-out "$out" \
    --json-out "$json_out"
}

repair_macos_package_install_names() {
  local package_root="$1"
  local plugin_name="$2"
  local plugin="$package_root/$plugin_name"
  local package_lib_dir="$package_root/postgres/lib"

  [[ "$(uname -s)" == "Darwin" ]] || return 0

  install_name_tool -id "@rpath/$plugin_name" "$plugin"

  while IFS= read -r dylib; do
    install_name_tool -id "@loader_path/$(basename "$dylib")" "$dylib" || true
  done < <(find "$package_lib_dir" -maxdepth 1 -type f -name '*.dylib' | LC_ALL=C sort)

  while IFS= read -r object; do
    while IFS= read -r dependency; do
      case "$dependency" in
        "$postgres_install_prefix"/lib/*.dylib|"$package_lib_dir"/*.dylib)
          install_name_tool \
            -change "$dependency" "@loader_path/$(basename "$dependency")" \
            "$object"
          ;;
        /opt/homebrew/*/lib/libcrypto.*.dylib|/opt/homebrew/opt/openssl@*/lib/libcrypto.*.dylib)
          local dependency_name
          dependency_name="$(basename "$dependency")"
          if [[ ! -f "$package_lib_dir/$dependency_name" ]]; then
            cp "$dependency" "$package_lib_dir/$dependency_name"
            install_name_tool -id "@loader_path/$dependency_name" "$package_lib_dir/$dependency_name"
          fi
          install_name_tool -change "$dependency" "@loader_path/$dependency_name" "$object"
          ;;
      esac
    done < <(otool -L "$object" | awk 'NR > 1 {print $1}')
  done < <(
    {
      printf '%s\n' "$plugin"
      find "$package_lib_dir" -maxdepth 1 -type f -name '*.dylib'
    } | LC_ALL=C sort
  )

  while IFS= read -r object; do
    codesign --force --sign - "$object" >/dev/null 2>&1 || true
  done < <(
    {
      printf '%s\n' "$plugin"
      find "$package_lib_dir" -maxdepth 1 -type f -name '*.dylib'
    } | LC_ALL=C sort
  )
}

native_manifest="${LIBPGLITE_NATIVE_LINK_MANIFEST:-"$repo_root/target/native-pglite/$platform/libpglite_native_link_manifest.txt"}"
if [[ ! -f "$native_manifest" ]]; then
  echo "native link manifest not found: $native_manifest" >&2
  echo "run scripts/prepare-native-pglite-link.sh --build-postgres before packaging" >&2
  exit 1
fi

validate_plugin_exports() {
  local binary="$1"
  local required=(
    libpglite_plugin_abi_version
    libpglite_plugin_buffer_free
    libpglite_plugin_runtime_create
    libpglite_plugin_runtime_destroy
    libpglite_plugin_runtime_exec_protocol_raw
    libpglite_plugin_runtime_shutdown
  )

  local symbols
  symbols="$(defined_symbols "$binary" | sort -u)"
  for symbol in "${required[@]}"; do
    if ! grep -Fx "$symbol" <<<"$symbols" >/dev/null; then
      echo "native plugin is missing ABI symbol: $symbol" >&2
      exit 1
    fi
  done

  if [[ "$(uname -s)" == "Linux" ]]; then
    local allowed_backend_exports
    allowed_backend_exports="$(mktemp)"
    awk -F= '$1 == "backend_export_symbol" {print substr($0, length($1) + 2)}' "$native_manifest" | sort -u >"$allowed_backend_exports"
    local unexpected=0
    while read -r symbol; do
      [[ -z "$symbol" ]] && continue
      case "$symbol" in
        libpglite_plugin_abi_version|\
        libpglite_plugin_buffer_free|\
        libpglite_plugin_runtime_create|\
        libpglite_plugin_runtime_destroy|\
        libpglite_plugin_runtime_exec_protocol_raw|\
        libpglite_plugin_runtime_shutdown)
          ;;
        *)
          if ! grep -Fx "$symbol" "$allowed_backend_exports" >/dev/null; then
            echo "Linux native plugin exports symbol outside the host ABI and generated backend export set: $symbol" >&2
            unexpected=1
          fi
          ;;
      esac
    done <<<"$symbols"
    rm -f "$allowed_backend_exports"

    if [[ "$unexpected" != "0" ]]; then
      echo "Linux native plugin must export only the libpglite plugin C ABI plus generated backend symbols" >&2
      exit 1
    fi
  fi
}

validate_plugin_exports "$plugin_binary"

git_commit="$(git -C "$repo_root" rev-parse HEAD)"

manifest_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key {print substr($0, length(key) + 2)}' "$native_manifest"
}

postgres_install_prefix="$(manifest_value postgres_install_prefix)"
postgres_binary="$(manifest_value postgres_binary)"
initdb_binary="$(manifest_value initdb_binary)"
postgres_share_dir="$(manifest_value postgres_share_dir)"
postgres_lib_dir="$(manifest_value postgres_lib_dir)"
if [[ -z "$postgres_install_prefix" || ! -d "$postgres_install_prefix" ]]; then
  echo "native manifest does not provide a valid postgres_install_prefix: ${postgres_install_prefix:-<empty>}" >&2
  exit 1
fi
for file in "$postgres_binary" "$initdb_binary" \
  "$postgres_share_dir/postgres.bki" \
  "$postgres_share_dir/snowball_create.sql" \
  "$postgres_share_dir/extension/plpgsql.control"; do
  if [[ ! -f "$file" ]]; then
    echo "native Postgres prefix is missing required package file: $file" >&2
    exit 1
  fi
done
if [[ ! -d "$postgres_lib_dir" ]]; then
  echo "native Postgres prefix is missing lib directory: $postgres_lib_dir" >&2
  exit 1
fi
conformance_dir="${LIBPGLITE_CONFORMANCE_DIR:-}"
if [[ -z "$conformance_dir" || ! -d "$conformance_dir" ]]; then
  echo "LIBPGLITE_CONFORMANCE_DIR must point at structured native conformance results" >&2
  exit 1
fi
for result in raw-protocol tokio-postgres-client prefix-initialize prefix-resume; do
  if [[ ! -f "$conformance_dir/$result.json" || ! -f "$conformance_dir/$result.log" ]]; then
    echo "native conformance directory is missing required result pair: $result" >&2
    exit 1
  fi
done

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

binary_stage="$tmpdir/binary"
source_stage="$tmpdir/source/libpglite-${release_version}"
mkdir -p "$binary_stage" "$source_stage"

cp "$plugin_binary" "$binary_stage/"
cp -R "$postgres_install_prefix" "$binary_stage/postgres"
repair_macos_package_install_names "$binary_stage" "$expected_plugin"
validate_plugin_exports "$binary_stage/$expected_plugin"
plugin_checksum="$(sha256 "$binary_stage/$expected_plugin")"
diagnostics_stage="$binary_stage/diagnostics"
mkdir -p "$diagnostics_stage"

mkdir -p "$diagnostics_stage/conformance"
cp "$conformance_dir"/*.json "$diagnostics_stage/conformance/"
cp "$conformance_dir"/*.log "$diagnostics_stage/conformance/"
cp "$native_manifest" "$diagnostics_stage/native-link-manifest.txt"
extension_inventory="$(manifest_value extension_inventory)"
if [[ -n "$extension_inventory" && -f "$extension_inventory" ]]; then
  cp "$extension_inventory" "$diagnostics_stage/extension-inventory.txt"
else
  echo "native link manifest does not provide a readable extension_inventory: ${extension_inventory:-<empty>}" >&2
  exit 1
fi
defined_symbols "$binary_stage/$expected_plugin" | LC_ALL=C sort -u >"$diagnostics_stage/plugin-defined-symbols.txt"
awk -F= '$1 == "backend_export_symbol" {print substr($0, length($1) + 2)}' "$native_manifest" \
  | LC_ALL=C sort -u >"$diagnostics_stage/backend-export-symbols.txt"
dependency_report \
  "$diagnostics_stage/dependencies.txt" \
  "$binary_stage" \
  "$binary_stage/$expected_plugin" \
  "$binary_stage/postgres/lib" \
  "$diagnostics_stage/dependencies.json"
python3 - "$repo_root" "$native_manifest" "$diagnostics_stage/source-provenance.json" <<'PY'
import hashlib
import json
import pathlib
import sys

repo_root = pathlib.Path(sys.argv[1])
native_manifest = pathlib.Path(sys.argv[2])
out = pathlib.Path(sys.argv[3])

values: dict[str, list[str]] = {}
for line in native_manifest.read_text().splitlines():
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    values.setdefault(key, []).append(value)

patches = []
for rel in values.get("patch", []):
    path = repo_root / rel
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    patches.append({"path": rel, "sha256": digest})

provenance = {
    "format": "libpglite-native-source-provenance-v1",
    "postgresPglite": {
        "repository": values.get("source_repository", [""])[0],
        "ref": values.get("source_ref", [""])[0],
        "commit": values.get("source_commit", [""])[0],
    },
    "patchFingerprint": values.get("patch_fingerprint", [""])[0],
    "patches": patches,
}
out.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
PY
python3 - "$diagnostics_stage/runtime-lifecycle.json" <<'PY'
import json
import pathlib
import sys

lifecycle = {
    "format": "libpglite-native-runtime-lifecycle-v1",
    "contract": "single-start-per-process",
    "restartSupported": False,
    "concurrentRuntimeSupported": False,
    "secondStartupBehavior": "fails-before-entering-postgres",
    "shutdownBehavior": "runs-postgres-pglite-shutdown-hooks-once",
    "provenByConformance": ["raw-protocol"],
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(lifecycle, indent=2, sort_keys=True) + "\n")
PY
{
  echo "format=libpglite-native-build-provenance-v1"
  echo "target=$platform"
  echo "release_version=$release_version"
  echo "release_mode=$release_mode"
  echo "runtime_status=$runtime_status"
  echo "libpglite_git_commit=$git_commit"
  echo "plugin_filename=$expected_plugin"
  echo "plugin_sha256=$plugin_checksum"
  echo "native_manifest=native-link-manifest.txt"
  echo "extension_inventory=extension-inventory.txt"
  echo "dependency_manifest=dependencies.json"
  echo "packaged_at_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "uname=$(uname -a)"
  echo "rustc_begin"
  rustc -vV
  echo "rustc_end"
  echo "cc_begin"
  "${CC:-cc}" --version 2>&1 | sed -n '1,5p'
  echo "cc_end"
} >"$diagnostics_stage/build-provenance.txt"
python3 - "$binary_stage/libpglite-native-bundle.json" "$platform" "$release_version" "$git_commit" "$expected_plugin" "$plugin_checksum" "$runtime_status" "$release_mode" <<'PY'
import json
import pathlib
import sys

(
    out,
    target,
    release_version,
    git_commit,
    plugin_name,
    plugin_checksum,
    runtime_status,
    release_mode,
) = sys.argv[1:9]
bundle = {
    "target": target,
    "pluginAbiVersion": 1,
    "libpgliteReleaseVersion": release_version,
    "libpgliteGitCommit": git_commit,
    "releaseMode": release_mode,
    "runtimeStatus": runtime_status,
    "plugin": {
        "filename": plugin_name,
        "sha256": plugin_checksum,
    },
    "postgresPrefix": {
        "path": "postgres",
        "bin": "postgres/bin",
        "share": "postgres/share",
        "lib": "postgres/lib",
        "initdb": "postgres/bin/initdb",
        "postgres": "postgres/bin/postgres",
    },
    "diagnostics": {
        "path": "diagnostics",
        "buildProvenance": "diagnostics/build-provenance.txt",
        "nativeLinkManifest": "diagnostics/native-link-manifest.txt",
        "extensionInventory": "diagnostics/extension-inventory.txt",
        "pluginDefinedSymbols": "diagnostics/plugin-defined-symbols.txt",
        "backendExportSymbols": "diagnostics/backend-export-symbols.txt",
        "dependencies": "diagnostics/dependencies.txt",
        "dependencyManifest": "diagnostics/dependencies.json",
        "sourceProvenance": "diagnostics/source-provenance.json",
        "runtimeLifecycle": "diagnostics/runtime-lifecycle.json",
        "conformanceResults": "diagnostics/conformance",
    },
    "sourceArchive": f"libpglite-plugin-native-{release_version}-source.tar.zst",
    "noticeFile": f"libpglite-plugin-native-{release_version}-NOTICE.txt",
    "licenseInventory": f"libpglite-plugin-native-{release_version}-licenses.json",
    "sourceInstructions": f"libpglite-plugin-native-{release_version}-SOURCE.txt",
    "checksums": f"libpglite-plugin-native-{release_version}-checksums.txt",
}
pathlib.Path(out).write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
PY

"$repo_root/scripts/doctor-native-plugin-package.py" "$binary_stage"

tar -C "$binary_stage" --zstd -cf "$binary_asset" .

git -C "$repo_root" archive --format=tar HEAD | tar -C "$source_stage" -xf -
tar -C "$tmpdir/source" --zstd -cf "$source_asset" "libpglite-${release_version}"

cat >"$notice_asset" <<EOF
libpglite native plugin ${release_version}

This package contains the replaceable native plugin for libpglite.
The native PGlite/Postgres runtime implementation remains governed by ADR-0002
until that ADR is moved to docs/done/.
EOF

cat >"$source_txt_asset" <<EOF
Source for this release is available from:

  https://github.com/enki/libpglite

Repository commit:

  ${git_commit}

The accompanying source archive is:

  $(basename "$source_asset")
EOF

python3 - "$inventory_asset" <<'PY'
import json
import pathlib
import sys

inventory = {
    "project": "libpglite",
    "licenses": [
        {
            "name": "libpglite",
            "license": "Apache-2.0",
            "path": "LICENSE",
        }
    ],
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
PY

: >"$checksums_asset"
for asset in "$binary_asset" "$source_asset" "$notice_asset" "$inventory_asset" "$source_txt_asset"; do
  printf '%s  %s\n' "$(sha256 "$asset")" "$(basename "$asset")" >>"$checksums_asset"
done

echo "wrote $binary_asset"
echo "wrote $source_asset"
echo "wrote $notice_asset"
echo "wrote $inventory_asset"
echo "wrote $source_txt_asset"
echo "wrote $checksums_asset"
