#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${1:-}"

usage() {
  cat >&2 <<'USAGE'
usage: scripts/preflight-native-plugin-release.sh <version>

Runs local release-boundary checks:

  - crate tests
  - ADR closure audit
  - dynamic-loading check
  - pinned native dependency prefix build
  - pinned postgres-pglite native PIC archive build
  - native plugin build linked against those archives
  - native plugin symbol-boundary checks
  - native plugin raw-protocol and tokio-postgres client checks
  - native plugin package smoke test

ADR-0002 still owns the real native PGlite/Postgres runtime. This preflight
checks the facade/plugin/release boundary and native link substrate without
claiming the remaining extension, dependency, platform, and release gates are
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
require rustc
require python3
require zstd
require nm
require cmake

cd "$repo_root"

sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

conformance_dir="$(mktemp -d)"

write_conformance_result() {
  local name="$1"
  local status="$2"
  local exit_code="$3"
  local started_at="$4"
  local ended_at="$5"
  local command="$6"
  local log_file="$7"
  local log_sha256="$8"
  local out_file="$9"

  python3 - "$name" "$status" "$exit_code" "$started_at" "$ended_at" "$command" "$(basename "$log_file")" "$log_sha256" "$out_file" <<'PY'
import json
import pathlib
import sys

(
    name,
    status,
    exit_code,
    started_at,
    ended_at,
    command,
    log_file,
    log_sha256,
    out,
) = sys.argv[1:10]
result = {
    "format": "libpglite-native-conformance-result-v1",
    "name": name,
    "status": status,
    "exitCode": int(exit_code),
    "startedAt": started_at,
    "endedAt": ended_at,
    "command": command,
    "log": log_file,
    "logSha256": log_sha256,
}
if name == "raw-protocol":
    result["cases"] = [
        "startup",
        "simple-query",
        "empty-query",
        "transaction-rollback",
        "transaction-commit",
        "recoverable-protocol-error",
        "extended-query",
        "parameterized-extended-query",
        "named-prepared-statement",
        "deterministic-shutdown",
    ]
pathlib.Path(out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
PY
}

run_conformance_check() {
  local name="$1"
  shift
  local log_file="$conformance_dir/$name.log"
  local result_file="$conformance_dir/$name.json"
  local started_at
  local ended_at
  local code
  started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "==> preflight ${release_version}: ${name}"
  set +e
  "$@" > >(tee "$log_file") 2>&1
  code=$?
  set -e
  ended_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  log_sha256="$(sha256 "$log_file")"
  if [[ "$code" == "0" ]]; then
    write_conformance_result "$name" "passed" "$code" "$started_at" "$ended_at" "$*" "$log_file" "$log_sha256" "$result_file"
  else
    write_conformance_result "$name" "failed" "$code" "$started_at" "$ended_at" "$*" "$log_file" "$log_sha256" "$result_file"
    echo "conformance check failed: $name" >&2
    echo "last 200 log lines from $log_file:" >&2
    tail -n 200 "$log_file" >&2 || true
    exit "$code"
  fi
}

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

echo "==> preflight ${release_version}: ADR closure audit"
python3 scripts/audit-adr-closure.py

echo "==> preflight ${release_version}: facade dependency boundary"
facade_tree="$(cargo tree -p libpglite --edges normal --no-default-features)"
if grep -E 'libpglite-native|libpglite-plugin-native' <<<"$facade_tree" >/dev/null; then
  echo "facade crate links native implementation crates with no default features" >&2
  echo "$facade_tree" >&2
  exit 1
fi

echo "==> preflight ${release_version}: doctor regression tests"
python3 scripts/test-build-native-dependency-prefix.py
python3 scripts/test-describe-native-dependency-prefix.py
python3 scripts/test-doctor-native-plugin-package.py
python3 scripts/test-fetch-native-dependency-sources.py
python3 scripts/test-generate-native-dependency-manifest.py
python3 scripts/test-inventory-native-pglite-extensions.py
python3 scripts/test-link-linux-native-plugin.py
python3 scripts/test-materialize-native-pglite-other-extensions.py
python3 scripts/test-native-patch-decisions.py
python3 scripts/test-package-native-plugin-release.py
python3 scripts/test-preflight-linux-smolvm.py
python3 scripts/test-plugin-build-rs.py
python3 scripts/test-prepare-native-pglite-link.py
python3 scripts/test-check-native-other-extension-build.py
python3 scripts/test-preflight-native-plugin-release.py

echo "==> preflight ${release_version}: dynamic-loading check"
cargo check --features dynamic-loading,client-tokio-postgres

native_build_root="${LIBPGLITE_NATIVE_BUILD_ROOT:-"$repo_root/target/native-pglite"}"
case "$native_build_root" in
  /*) ;;
  *) native_build_root="$repo_root/$native_build_root" ;;
esac
dependency_prefix="${LIBPGLITE_NATIVE_DEPENDENCY_PREFIX:-"$native_build_root/dependency-prefix"}"
case "$dependency_prefix" in
  /*) ;;
  *) dependency_prefix="$repo_root/$dependency_prefix" ;;
esac
dependency_sources="${LIBPGLITE_NATIVE_DEPENDENCY_SOURCES:-"$native_build_root/dependency-sources"}"
case "$dependency_sources" in
  /*) ;;
  *) dependency_sources="$repo_root/$dependency_sources" ;;
esac
dependency_work_dir="${LIBPGLITE_NATIVE_DEPENDENCY_BUILD_DIR:-"$native_build_root/dependency-build"}"
case "$dependency_work_dir" in
  /*) ;;
  *) dependency_work_dir="$repo_root/$dependency_work_dir" ;;
esac
manifest="${LIBPGLITE_NATIVE_LINK_MANIFEST:-"$native_build_root/$(rustc -vV | awk -F': ' '$1 == "host" {print $2}')/libpglite_native_link_manifest.txt"}"
case "$manifest" in
  /*) ;;
  *) manifest="$repo_root/$manifest" ;;
esac
export LIBPGLITE_NATIVE_LINK_MANIFEST="$manifest"

echo "==> preflight ${release_version}: pinned native dependency prefix"
scripts/build-native-dependency-prefix.sh \
  --prefix "$dependency_prefix" \
  --sources "$dependency_sources" \
  --work-dir "$dependency_work_dir"

echo "==> preflight ${release_version}: pinned postgres-pglite native archive build"
scripts/prepare-native-pglite-link.sh \
  --build-postgres \
  --out "$manifest" \
  --dependency-prefix "$dependency_prefix" \
  --fetch-other-extensions \
  --build-other-extensions
initdb_binary="$(awk -F= '$1 == "initdb_binary" {print substr($0, length($1) + 2)}' "$manifest")"
postgres_lib_dir="$(awk -F= '$1 == "postgres_lib_dir" {print substr($0, length($1) + 2)}' "$manifest")"
if [[ -z "$initdb_binary" || ! -x "$initdb_binary" ]]; then
  echo "native manifest does not provide an executable initdb_binary: ${initdb_binary:-<empty>}" >&2
  exit 1
fi
initdb_tempdir="$(mktemp -d)"
resume_tempdir="$(mktemp -d)"
trap 'rm -rf "$initdb_tempdir" "$resume_tempdir" "$conformance_dir"' EXIT
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

echo "==> preflight ${release_version}: build native plugin"
case "$(uname -s)" in
  Darwin)
    LIBPGLITE_NATIVE_LINK_PGLITE=1 cargo build -p libpglite-plugin-native --release
    ;;
  Linux)
    LIBPGLITE_NATIVE_LINK_PGLITE=1 cargo rustc -p libpglite-plugin-native --release --lib --crate-type staticlib
    scripts/link-linux-native-plugin.sh \
      "$manifest" \
      "$target_dir/release/liblibpglite_plugin_native.a" \
      "$plugin_binary"
    ;;
esac

if [[ ! -f "$plugin_binary" ]]; then
  echo "expected plugin binary was not built: $plugin_binary" >&2
  exit 1
fi

echo "==> preflight ${release_version}: native symbol boundary"
allowed_backend_exports="$(mktemp)"
awk -F= '$1 == "backend_export_symbol" {print substr($0, length($1) + 2)}' "$manifest" | sort -u >"$allowed_backend_exports"
case "$(uname -s)" in
  Darwin)
    unexpected_exports="$(
      nm -gU "$plugin_binary" |
        awk '{print $3}' |
        sed 's/^_//' |
        grep -Ev '^libpglite_plugin_' |
        grep -Fxv -f "$allowed_backend_exports" || true
    )"
    ;;
  Linux)
    unexpected_exports="$(
      nm -D --defined-only "$plugin_binary" |
        awk '{print $3}' |
        sed 's/@@.*//' |
        sed 's/@.*//' |
        grep -Ev '^LIBPGLITE_PLUGIN_NATIVE_[0-9]+$' |
        grep -Ev '^libpglite_plugin_' |
        grep -Fxv -f "$allowed_backend_exports" || true
    )"
    ;;
esac

if [[ -n "$unexpected_exports" ]]; then
  echo "native plugin exports symbols outside the host ABI and generated backend export set:" >&2
  echo "$unexpected_exports" >&2
  exit 1
fi

all_symbols="$(nm -a "$plugin_binary")"
if ! grep -q 'PostgresSingleUserMain' <<<"$all_symbols"; then
  echo "native plugin does not contain linked Postgres backend symbols" >&2
  exit 1
fi

postgres_prefix="$(awk -F= '$1 == "postgres_install_prefix" {print substr($0, length($1) + 2)}' "$manifest")"
run_conformance_check raw-protocol \
  env \
    LIBPGLITE_TEST_PLUGIN_PATH="$plugin_binary" \
    LIBPGLITE_TEST_POSTGRES_PREFIX="$postgres_prefix" \
    cargo test --features dynamic-loading --test dynamic_plugin

run_conformance_check tokio-postgres-client \
  env \
    LIBPGLITE_RUN_TOKIO_POSTGRES_CHILD=1 \
    LIBPGLITE_TEST_PLUGIN_PATH="$plugin_binary" \
    LIBPGLITE_TEST_POSTGRES_PREFIX="$postgres_prefix" \
    cargo test --features dynamic-loading,client-tokio-postgres --test dynamic_plugin \
      dynamic_plugin_tokio_postgres_client_child -- --nocapture

run_conformance_check prefix-initialize \
  env \
    LIBPGLITE_RUN_PREFIX_INITIALIZE_CHILD=1 \
    LIBPGLITE_TEST_PLUGIN_PATH="$plugin_binary" \
    LIBPGLITE_TEST_POSTGRES_PREFIX="$postgres_prefix" \
    LIBPGLITE_TEST_DATA_DIR="$resume_tempdir/pgdata" \
    cargo test --features dynamic-loading --test dynamic_plugin \
      dynamic_plugin_prefix_initialize_child -- --nocapture

run_conformance_check prefix-resume \
  env \
    LIBPGLITE_RUN_PREFIX_RESUME_CHILD=1 \
    LIBPGLITE_TEST_PLUGIN_PATH="$plugin_binary" \
    LIBPGLITE_TEST_POSTGRES_PREFIX="$postgres_prefix" \
    LIBPGLITE_TEST_DATA_DIR="$resume_tempdir/pgdata" \
    cargo test --features dynamic-loading --test dynamic_plugin \
      dynamic_plugin_prefix_resume_child -- --nocapture

out_dir="${LIBPGLITE_RELEASE_OUT_DIR:-"$repo_root/dist/preflight-native-plugin"}"
rm -rf "$out_dir"
echo "==> preflight ${release_version}: package smoke test"
LIBPGLITE_CONFORMANCE_DIR="$conformance_dir" \
  scripts/package-native-plugin-release.sh "$release_version" "$plugin_binary" "$out_dir"
echo "==> preflight ${release_version}: package doctor"
package_asset="$out_dir/libpglite-plugin-native-${release_version}-$(rustc -vV | awk -F': ' '$1 == "host" {print $2}').tar.zst"
scripts/doctor-native-plugin-package.py --strict-relocatable --self-test "$package_asset"

echo "==> preflight ${release_version}: complete"
