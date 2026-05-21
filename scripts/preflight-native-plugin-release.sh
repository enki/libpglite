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
  - native plugin build
  - native plugin package smoke test

ADR-0002 still owns the real native PGlite/Postgres runtime. This preflight
checks the facade/plugin/release boundary without claiming the native runtime is
complete.
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

echo "==> preflight ${release_version}: build native plugin"
cargo build -p libpglite-plugin-native --release

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

echo "==> preflight ${release_version}: dynamic plugin load check"
LIBPGLITE_TEST_PLUGIN_PATH="$plugin_binary" cargo test --features dynamic-loading --test dynamic_plugin

out_dir="${LIBPGLITE_RELEASE_OUT_DIR:-"$repo_root/dist/preflight-native-plugin"}"
rm -rf "$out_dir"
echo "==> preflight ${release_version}: package smoke test"
scripts/package-native-plugin-release.sh "$release_version" "$plugin_binary" "$out_dir"

echo "==> preflight ${release_version}: complete"
