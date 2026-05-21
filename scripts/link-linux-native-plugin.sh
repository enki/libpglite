#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="${1:-}"
rust_staticlib="${2:-}"
out_so="${3:-}"

usage() {
  cat >&2 <<'USAGE'
usage: scripts/link-linux-native-plugin.sh <native-link-manifest> <rust-staticlib> <out-so>

Links the Linux native plugin from the Rust staticlib and the generated native
PGlite/Postgres link manifest. This owns the final GNU ld export boundary so the
host plugin ABI and generated backend extension symbols come from one version
script instead of conflicting with rustc's cdylib version script.
USAGE
}

if [[ "$manifest" == "-h" || "$manifest" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "$manifest" || -z "$rust_staticlib" || -z "$out_so" ]]; then
  usage
  exit 2
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Linux native plugin final link must run on Linux, got $(uname -s)" >&2
  exit 2
fi

if [[ ! -f "$manifest" ]]; then
  echo "native link manifest not found: $manifest" >&2
  exit 2
fi
if [[ ! -f "$rust_staticlib" ]]; then
  echo "Rust plugin staticlib not found: $rust_staticlib" >&2
  exit 2
fi

manifest_dir="$(cd "$(dirname "$manifest")" && pwd)"

resolve_manifest_path() {
  local path="$1"
  case "$path" in
    /*) printf '%s\n' "$path" ;;
    *) printf '%s\n' "$manifest_dir/$path" ;;
  esac
}

objects=()
archives=()
link_args=()
backend_exports=()
has_format=0

while IFS= read -r line; do
  [[ "$line" == *=* ]] || continue
  key="${line%%=*}"
  value="${line#*=}"
  case "$key" in
    format)
      if [[ "$value" == "libpglite-native-link-manifest-v1" ]]; then
        has_format=1
      fi
      ;;
    object)
      path="$(resolve_manifest_path "$value")"
      [[ -f "$path" ]] || { echo "manifest object is missing: $path" >&2; exit 1; }
      objects+=("$path")
      ;;
    archive|static)
      path="$(resolve_manifest_path "$value")"
      [[ -f "$path" ]] || { echo "manifest archive is missing: $path" >&2; exit 1; }
      archives+=("$path")
      ;;
    link_arg)
      link_args+=("$value")
      ;;
    backend_export_symbol)
      [[ -n "$value" ]] && backend_exports+=("$value")
      ;;
  esac
done <"$manifest"

if [[ "$has_format" != "1" ]]; then
  echo "native link manifest is missing format=libpglite-native-link-manifest-v1: $manifest" >&2
  exit 1
fi
if [[ "${#objects[@]}" -eq 0 || "${#archives[@]}" -eq 0 ]]; then
  echo "native link manifest must provide object and archive inputs for Linux final link" >&2
  exit 1
fi
if [[ "${#backend_exports[@]}" -eq 0 ]]; then
  echo "native link manifest contains no backend_export_symbol entries" >&2
  exit 1
fi

mkdir -p "$(dirname "$out_so")"
out_dir="$(cd "$(dirname "$out_so")" && pwd)"
out_so="$out_dir/$(basename "$out_so")"
version_script="$out_dir/libpglite-plugin-native.final.exports"

{
  echo "LIBPGLITE_PLUGIN_NATIVE_1 {"
  echo "    global:"
  echo "        libpglite_plugin_abi_version;"
  echo "        libpglite_plugin_buffer_free;"
  echo "        libpglite_plugin_runtime_create;"
  echo "        libpglite_plugin_runtime_destroy;"
  echo "        libpglite_plugin_runtime_exec_protocol_raw;"
  echo "        libpglite_plugin_runtime_shutdown;"
  printf '%s\n' "${backend_exports[@]}" | LC_ALL=C sort -u | sed 's/^/        /; s/$/;/'
  echo "    local:"
  echo "        *;"
  echo "};"
} >"$version_script"

"${CC:-cc}" \
  -shared \
  -o "$out_so" \
  -Wl,--version-script="$version_script" \
  -Wl,--no-undefined-version \
  "${objects[@]}" \
  -Wl,--whole-archive \
  "$rust_staticlib" \
  "${archives[@]}" \
  -Wl,--no-whole-archive \
  "${link_args[@]}" \
  -lutil \
  -lrt \
  -lpthread \
  -lm \
  -ldl

echo "wrote $out_so"
