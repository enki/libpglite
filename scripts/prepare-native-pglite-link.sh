#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="${LIBPGLITE_POSTGRES_SOURCE_DIR:-}"
out="${LIBPGLITE_NATIVE_LINK_MANIFEST:-}"

usage() {
  cat >&2 <<'USAGE'
usage: scripts/prepare-native-pglite-link.sh [--source-dir <path>] [--out <manifest>]

Validates the pinned postgres-pglite source substrate and writes a native link
manifest skeleton. This does not claim the native PIC build is complete; object
and archive link inputs are added by the remaining ADR-0002 work.

Environment:
  LIBPGLITE_POSTGRES_SOURCE_DIR=<path>
  LIBPGLITE_NATIVE_LINK_MANIFEST=<path>
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir)
      source_dir="${2:-}"
      shift 2
      ;;
    --out)
      out="${2:-}"
      shift 2
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

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 2
  fi
}

require git
require cc
require patch

pin_file="$repo_root/PGLITE_POSTGRES_SOURCE"
if [[ ! -f "$pin_file" ]]; then
  echo "missing PGlite Postgres source pin: $pin_file" >&2
  exit 2
fi

pin_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key {print substr($0, length(key) + 2)}' "$pin_file"
}

pinned_repository="$(pin_value repository)"
pinned_ref="$(pin_value ref)"
pinned_commit="$(pin_value commit)"

if [[ -z "$pinned_repository" || -z "$pinned_ref" || -z "$pinned_commit" ]]; then
  echo "PGLITE_POSTGRES_SOURCE must define repository, ref, and commit" >&2
  exit 2
fi

if [[ -z "$source_dir" ]]; then
  if [[ -d "$repo_root/vendor/postgres-pglite/.git" || -f "$repo_root/vendor/postgres-pglite/build-pglite.sh" ]]; then
    source_dir="$repo_root/vendor/postgres-pglite"
  elif [[ -d "$repo_root/../postgres-pglite/.git" ]]; then
    source_dir="$repo_root/../postgres-pglite"
  else
    echo "postgres-pglite source not found; set LIBPGLITE_POSTGRES_SOURCE_DIR or populate vendor/postgres-pglite" >&2
    exit 2
  fi
fi

case "$source_dir" in
  /*) ;;
  *) source_dir="$repo_root/$source_dir" ;;
esac

if [[ ! -d "$source_dir" ]]; then
  echo "postgres-pglite source directory not found: $source_dir" >&2
  exit 2
fi

source_commit="$(git -C "$source_dir" rev-parse HEAD 2>/dev/null || true)"
source_repository="$(git -C "$source_dir" remote get-url origin 2>/dev/null || true)"
if [[ "$source_commit" != "$pinned_commit" ]]; then
  echo "postgres-pglite source commit mismatch: expected $pinned_commit, got ${source_commit:-<unknown>}" >&2
  exit 1
fi
if [[ "$source_repository" != "$pinned_repository" ]]; then
  echo "postgres-pglite source repository mismatch: expected $pinned_repository, got ${source_repository:-<unknown>}" >&2
  exit 1
fi

required_files=(
  build-pglite.sh
  pglite/src/pglitec/pglitec.c
  pglite/static/included.pglite.exports
  src/backend/Makefile
  src/backend/tcop/postgres.c
  src/backend/tcop/backend_startup.c
)

for file in "${required_files[@]}"; do
  if [[ ! -f "$source_dir/$file" ]]; then
    echo "postgres-pglite source is missing required file: $file" >&2
    exit 1
  fi
done

if [[ -z "$out" ]]; then
  target="$(rustc -vV | awk -F': ' '$1 == "host" {print $2}')"
  out="$repo_root/target/native-pglite/$target/libpglite_native_link_manifest.txt"
fi

build_dir="$(dirname "$out")"
patched_source="$build_dir/patched-postgres-pglite"
object_dir="$build_dir/objects"
mkdir -p "$patched_source/pglite/src/pglitec" "$object_dir"

cp "$source_dir/pglite/src/pglitec/pglitec.c" "$patched_source/pglite/src/pglitec/pglitec.c"
for patch_file in "$repo_root"/patches/postgres-pglite/*.patch; do
  [[ -e "$patch_file" ]] || continue
  patch -d "$patched_source" -p1 <"$patch_file" >/dev/null
done

pglitec_object="$object_dir/pglitec.o"
cc -fPIC -O2 -DNDEBUG \
  -c "$patched_source/pglite/src/pglitec/pglitec.c" \
  -o "$pglitec_object"

{
  echo "format=libpglite-native-link-manifest-v1"
  echo "source_repository=$source_repository"
  echo "source_ref=$pinned_ref"
  echo "source_commit=$source_commit"
  echo "source_dir=$source_dir"
  echo "status=source-validated"
  for file in "${required_files[@]}"; do
    echo "required_file=$file"
  done
  echo "patch=patches/postgres-pglite/0001-pglitec-native-portability.patch"
  echo "object=$pglitec_object"
  echo "note=ADR-0002 still owns Postgres backend archive generation"
} >"$out"

echo "wrote $out"
