#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${1:-}"

usage() {
  cat >&2 <<'USAGE'
usage: scripts/preflight-native-plugin-release.sh <version>

Runs local release-boundary checks:

  - crate tests
  - dynamic-loading check
  - pinned postgres-pglite native PIC archive build
  - native plugin build linked against those archives
  - native plugin symbol-boundary checks
  - native plugin package smoke test

ADR-0002 still owns the real native PGlite/Postgres runtime. This preflight
checks the facade/plugin/release boundary and native link substrate without
claiming the runtime lifecycle is complete.
USAGE
}

if [[ "$version" == "-h" || "$version" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "$version" ]]; then
  usage
  exit 2
fi

case "$version" in
  v*) release_version="$version" ;;
  *) release_version="v$version" ;;
esac

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 2
  fi
}

require cargo
require rustc
require python3
require zstd
require nm

cd "$repo_root"

crate_version="$(python3 - <<'PY'
import pathlib
import tomllib

manifest = tomllib.loads(pathlib.Path("Cargo.toml").read_text())
print(manifest["package"]["version"])
PY
)"

if [[ "v${crate_version}" != "$release_version" ]]; then
  echo "Cargo.toml version is ${crate_version}, release tag is ${release_version}; refusing divergent preflight" >&2
  exit 1
fi

echo "==> preflight ${release_version}: workspace tests"
cargo test --all-features --workspace

echo "==> preflight ${release_version}: dynamic-loading check"
cargo check --features dynamic-loading

echo "==> preflight ${release_version}: pinned postgres-pglite native archive build"
scripts/prepare-native-pglite-link.sh --build-postgres
manifest="$repo_root/target/native-pglite/$(rustc -vV | awk -F': ' '$1 == "host" {print $2}')/libpglite_native_link_manifest.txt"
initdb_binary="$(awk -F= '$1 == "initdb_binary" {print substr($0, length($1) + 2)}' "$manifest")"
postgres_lib_dir="$(awk -F= '$1 == "postgres_lib_dir" {print substr($0, length($1) + 2)}' "$manifest")"
if [[ -z "$initdb_binary" || ! -x "$initdb_binary" ]]; then
  echo "native manifest does not provide an executable initdb_binary: ${initdb_binary:-<empty>}" >&2
  exit 1
fi
initdb_tempdir="$(mktemp -d)"
trap 'rm -rf "$initdb_tempdir"' EXIT
echo "==> preflight ${release_version}: native initdb prefix smoke test"
DYLD_LIBRARY_PATH="$postgres_lib_dir${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}" \
LD_LIBRARY_PATH="$postgres_lib_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  "$initdb_binary" \
    -D "$initdb_tempdir/pgdata" \
    --allow-group-access \
    --encoding UTF8 \
    --locale=C \
    --locale-provider=libc \
    --auth=trust \
    --no-sync

echo "==> preflight ${release_version}: build native plugin"
LIBPGLITE_NATIVE_LINK_PGLITE=1 cargo build -p libpglite-plugin-native --release

case "$(uname -s)" in
  Darwin) plugin_name="liblibpglite_plugin_native.dylib" ;;
  Linux) plugin_name="liblibpglite_plugin_native.so" ;;
  *)
    echo "unsupported native plugin preflight OS: $(uname -s)" >&2
    exit 2
    ;;
esac

target_dir="${CARGO_TARGET_DIR:-"$repo_root/target"}"
case "$target_dir" in
  /*) ;;
  *) target_dir="$repo_root/$target_dir" ;;
esac
plugin_binary="$target_dir/release/$plugin_name"

if [[ ! -f "$plugin_binary" ]]; then
  echo "expected plugin binary was not built: $plugin_binary" >&2
  exit 1
fi

echo "==> preflight ${release_version}: native symbol boundary"
case "$(uname -s)" in
  Darwin)
    unexpected_exports="$(
      nm -gU "$plugin_binary" | awk '{print $3}' | grep -Ev '^_libpglite_plugin_' || true
    )"
    ;;
  Linux)
    unexpected_exports="$(
      nm -D --defined-only "$plugin_binary" | awk '{print $3}' | grep -Ev '^libpglite_plugin_' || true
    )"
    ;;
esac

if [[ -n "$unexpected_exports" ]]; then
  echo "native plugin exports non-ABI symbols:" >&2
  echo "$unexpected_exports" >&2
  exit 1
fi

all_symbols="$(nm -a "$plugin_binary")"
if ! grep -q 'PostgresSingleUserMain' <<<"$all_symbols"; then
  echo "native plugin does not contain linked Postgres backend symbols" >&2
  exit 1
fi

echo "==> preflight ${release_version}: dynamic plugin load check"
LIBPGLITE_TEST_PLUGIN_PATH="$plugin_binary" cargo test --features dynamic-loading --test dynamic_plugin

out_dir="${LIBPGLITE_RELEASE_OUT_DIR:-"$repo_root/dist/preflight-native-plugin"}"
rm -rf "$out_dir"
echo "==> preflight ${release_version}: package smoke test"
scripts/package-native-plugin-release.sh "$release_version" "$plugin_binary" "$out_dir"

echo "==> preflight ${release_version}: complete"
