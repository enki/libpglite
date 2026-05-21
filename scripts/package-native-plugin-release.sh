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
  LIBPGLITE_RUNTIME_READY_CONFORMANCE=1  required for production mode
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
  if [[ "${LIBPGLITE_RUNTIME_READY_CONFORMANCE:-0}" != "1" ]]; then
    echo "production packaging requires LIBPGLITE_RUNTIME_READY_CONFORMANCE=1 after ADR-0002 and ADR-0003 conformance passes" >&2
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
          echo "Linux native plugin exports non-ABI symbol: $symbol" >&2
          unexpected=1
          ;;
      esac
    done <<<"$symbols"

    if [[ "$unexpected" != "0" ]]; then
      echo "Linux native plugin must export only the libpglite plugin C ABI" >&2
      exit 1
    fi
  fi
}

validate_plugin_exports "$plugin_binary"

git_commit="$(git -C "$repo_root" rev-parse HEAD)"
plugin_checksum="$(sha256 "$plugin_binary")"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

binary_stage="$tmpdir/binary"
source_stage="$tmpdir/source/libpglite-${release_version}"
mkdir -p "$binary_stage" "$source_stage"

cp "$plugin_binary" "$binary_stage/"
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
    "sourceArchive": f"libpglite-plugin-native-{release_version}-source.tar.zst",
    "noticeFile": f"libpglite-plugin-native-{release_version}-NOTICE.txt",
    "licenseInventory": f"libpglite-plugin-native-{release_version}-licenses.json",
    "sourceInstructions": f"libpglite-plugin-native-{release_version}-SOURCE.txt",
    "checksums": f"libpglite-plugin-native-{release_version}-checksums.txt",
}
pathlib.Path(out).write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
PY

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
