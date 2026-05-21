#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="${LIBPGLITE_POSTGRES_SOURCE_DIR:-}"
out="${LIBPGLITE_NATIVE_LINK_MANIFEST:-}"
build_postgres="${LIBPGLITE_BUILD_POSTGRES:-0}"

if [[ "$(uname -s)" == "Darwin" && -z "${MACOSX_DEPLOYMENT_TARGET:-}" ]]; then
  export MACOSX_DEPLOYMENT_TARGET=11.0
fi

usage() {
  cat >&2 <<'USAGE'
usage: scripts/prepare-native-pglite-link.sh [--source-dir <path>] [--out <manifest>] [--build-postgres]

Validates the pinned postgres-pglite source substrate and writes a native link
manifest. By default it validates the source and compiles PGlite-specific C
support. With --build-postgres, it also configures and compiles the pinned
Postgres backend as native PIC and emits release-grade archive inputs.

Environment:
  LIBPGLITE_POSTGRES_SOURCE_DIR=<path>
  LIBPGLITE_NATIVE_LINK_MANIFEST=<path>
  LIBPGLITE_BUILD_POSTGRES=1
  LIBPGLITE_NATIVE_MAKE_JOBS=<jobs>
  MACOSX_DEPLOYMENT_TARGET=<version>  default: 11.0 on Darwin
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
    --build-postgres)
      build_postgres=1
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

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 2
  fi
}

require git
require cc
require patch
require make
require ar
require tar

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
mkdir -p "$build_dir" "$object_dir"

patch_fingerprint="$(cd "$repo_root" && git hash-object patches/postgres-pglite/*.patch | git hash-object --stdin)"
patched_source_fingerprint="source_commit=$source_commit
patch_fingerprint=$patch_fingerprint"
patched_source_fingerprint_file="$patched_source/.libpglite-patched-source-fingerprint"
if [[ ! -f "$patched_source_fingerprint_file" || "$(cat "$patched_source_fingerprint_file")" != "$patched_source_fingerprint" ]]; then
  rm -rf "$patched_source"
  mkdir -p "$patched_source"
  git -C "$source_dir" archive --format=tar HEAD | tar -xf - -C "$patched_source"
  for patch_file in "$repo_root"/patches/postgres-pglite/*.patch; do
    [[ -e "$patch_file" ]] || continue
    patch -d "$patched_source" -p1 <"$patch_file" >/dev/null
  done
  printf '%s\n' "$patched_source_fingerprint" >"$patched_source_fingerprint_file"
fi

pglitec_object="$object_dir/pglitec.o"
cc -fPIC -O2 -DNDEBUG \
  -c "$patched_source/pglite/src/pglitec/pglitec.c" \
  -o "$pglitec_object"
native_exit_object="$object_dir/libpglite_native_exit.o"
cc -fPIC -O2 -DNDEBUG \
  -c "$repo_root/native/c/libpglite_native_trap.c" \
  -o "$native_exit_object"
native_trap_object="$object_dir/libpglite_native_trap.o"
cc -fPIC -O2 -DNDEBUG -DLIBPGLITE_NATIVE_BACKEND_TRAMPOLINES \
  -c "$repo_root/native/c/libpglite_native_trap.c" \
  -o "$native_trap_object"

make_jobs="${LIBPGLITE_NATIVE_MAKE_JOBS:-}"
if [[ -z "$make_jobs" ]]; then
  if command -v getconf >/dev/null 2>&1; then
    make_jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
  fi
  if [[ -z "$make_jobs" ]] && command -v sysctl >/dev/null 2>&1; then
    make_jobs="$(sysctl -n hw.ncpu 2>/dev/null || true)"
  fi
  make_jobs="${make_jobs:-4}"
fi

pglite_copt="-fPIC -O2 -DNDEBUG -D__PGLITE__ \
-Dsystem=pgl_system -Dpopen=pgl_popen -Dpclose=pgl_pclose \
-Dgeteuid=pgl_geteuid -Dgetuid=pgl_getuid -Dgetpwuid=pgl_getpwuid \
-Dexit=pgl_exit \
-Dmunmap=pgl_munmap \
-Dfcntl=pgl_fcntl \
-Datexit=pgl_atexit \
-Dsetsockopt=pgl_setsockopt -Dgetsockopt=pgl_getsockopt -Dgetsockname=pgl_getsockname \
-Drecv=pgl_recv -Dsend=pgl_send -Dconnect=pgl_connect \
-Dpoll=pgl_poll \
-Dshmget=pgl_shmget -Dshmat=pgl_shmat -Dshmdt=pgl_shmdt -Dshmctl=pgl_shmctl \
-Dlongjmp=pgl_longjmp -Dsiglongjmp=pgl_siglongjmp \
-Wno-macro-redefined -Wno-incompatible-pointer-types"

postgres_build_dir="$build_dir/postgres-build"
postgres_install_prefix="$postgres_build_dir/install"
backend_archive="$build_dir/libpglite_postgres_backend.a"
timezone_archive="$build_dir/libpglite_postgres_timezone.a"
common_archive="$postgres_build_dir/src/common/libpgcommon_srv.a"
port_archive="$postgres_build_dir/src/port/libpgport_srv.a"
native_trap_fingerprint="$(git -C "$repo_root" hash-object "$repo_root/native/c/libpglite_native_trap.c")"

if [[ "$build_postgres" == "1" ]]; then
  build_env_fingerprint="source_commit=$source_commit
macos_deployment_target=${MACOSX_DEPLOYMENT_TARGET:-}
patch_fingerprint=$patch_fingerprint
native_trap_fingerprint=$native_trap_fingerprint
pglite_copt=$pglite_copt"
  build_env_file="$postgres_build_dir/.libpglite-native-build-env"
  if [[ ! -f "$build_env_file" || "$(cat "$build_env_file")" != "$build_env_fingerprint" ]]; then
    rm -rf "$postgres_build_dir"
  fi
  mkdir -p "$postgres_build_dir"

  if [[ ! -x "$postgres_build_dir/config.status" ]]; then
    (
      cd "$postgres_build_dir"
      "$patched_source/configure" \
        --without-readline \
        --without-icu \
        --without-llvm \
        --without-pam \
        --without-zlib \
        --without-openssl \
        --without-gssapi \
        --without-ldap \
        --without-libxml \
        --without-libxslt \
        --without-systemd \
        --disable-nls \
        --prefix="$postgres_install_prefix"
    )
  fi

  make -C "$postgres_build_dir/src/backend" generated-headers submake-libpgport
  make -C "$postgres_build_dir/src/backend" postgres \
    COPT="$pglite_copt" \
    LDFLAGS_EX="$pglitec_object $native_exit_object" \
    -j"$make_jobs"

  backend_objects=()
  while IFS= read -r object; do
    backend_objects+=("$object")
  done < <(find "$postgres_build_dir/src/backend" -type f -name '*.o' | LC_ALL=C sort)
  if [[ "${#backend_objects[@]}" -eq 0 ]]; then
    echo "native Postgres backend build produced no object files" >&2
    exit 1
  fi

  rm -f "$backend_archive"
  ar -crs "$backend_archive" "${backend_objects[@]}"

  timezone_objects=()
  while IFS= read -r object; do
    timezone_objects+=("$object")
  done < <(find "$postgres_build_dir/src/timezone" -maxdepth 1 -type f -name '*.o' | LC_ALL=C sort)
  if [[ "${#timezone_objects[@]}" -eq 0 ]]; then
    echo "native Postgres backend build produced no timezone object files" >&2
    exit 1
  fi

  rm -f "$timezone_archive"
  ar -crs "$timezone_archive" "${timezone_objects[@]}"

  for archive in "$backend_archive" "$timezone_archive" "$common_archive" "$port_archive"; do
    if [[ ! -f "$archive" ]]; then
      echo "native Postgres backend build is missing archive: $archive" >&2
      exit 1
    fi
  done

  make -C "$postgres_build_dir/src/include" install
  make -C "$postgres_build_dir/src/interfaces/libpq" install
  make -C "$postgres_build_dir/src/backend" install \
    COPT="$pglite_copt" \
    LDFLAGS_EX="$pglitec_object $native_exit_object"
  make -C "$postgres_build_dir/src/backend/snowball" install
  make -C "$postgres_build_dir/src/pl/plpgsql/src" install
  make -C "$postgres_build_dir/src/bin/initdb" install

  for file in \
    "$postgres_install_prefix/bin/initdb" \
    "$postgres_install_prefix/bin/postgres" \
    "$postgres_install_prefix/share/postgres.bki" \
    "$postgres_install_prefix/share/snowball_create.sql" \
    "$postgres_install_prefix/share/extension/plpgsql.control"; do
    if [[ ! -f "$file" ]]; then
      echo "native Postgres install prefix is missing required initdb/runtime file: $file" >&2
      exit 1
    fi
  done

  printf '%s\n' "$build_env_fingerprint" >"$build_env_file"
fi

{
  echo "format=libpglite-native-link-manifest-v1"
  echo "source_repository=$source_repository"
  echo "source_ref=$pinned_ref"
  echo "source_commit=$source_commit"
  echo "source_dir=$source_dir"
  echo "patched_source_dir=$patched_source"
  if [[ "$build_postgres" == "1" ]]; then
    echo "status=native-postgres-archive-built"
  else
    echo "status=source-validated"
  fi
  for file in "${required_files[@]}"; do
    echo "required_file=$file"
  done
  for patch_file in "$repo_root"/patches/postgres-pglite/*.patch; do
    [[ -e "$patch_file" ]] || continue
    echo "patch=${patch_file#$repo_root/}"
  done
  echo "patch_fingerprint=$patch_fingerprint"
  echo "object=$pglitec_object"
  echo "object=$native_trap_object"
  echo "native_exit_object=$native_exit_object"
  echo "native_trap_source=native/c/libpglite_native_trap.c"
  echo "native_trap_fingerprint=$native_trap_fingerprint"
  if [[ "$build_postgres" == "1" ]]; then
    echo "archive=$backend_archive"
    echo "archive=$timezone_archive"
    echo "archive=$common_archive"
    echo "archive=$port_archive"
    echo "postgres_build_dir=$postgres_build_dir"
    echo "postgres_install_prefix=$postgres_install_prefix"
    echo "initdb_binary=$postgres_install_prefix/bin/initdb"
    echo "postgres_binary=$postgres_install_prefix/bin/postgres"
    echo "postgres_share_dir=$postgres_install_prefix/share"
    echo "postgres_lib_dir=$postgres_install_prefix/lib"
    echo "make_jobs=$make_jobs"
    if [[ -n "${MACOSX_DEPLOYMENT_TARGET:-}" ]]; then
      echo "macos_deployment_target=$MACOSX_DEPLOYMENT_TARGET"
    fi
  else
    echo "note=run with --build-postgres or LIBPGLITE_BUILD_POSTGRES=1 to generate backend archives"
  fi
} >"$out"

echo "wrote $out"
