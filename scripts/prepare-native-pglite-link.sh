#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="${LIBPGLITE_POSTGRES_SOURCE_DIR:-}"
out="${LIBPGLITE_NATIVE_LINK_MANIFEST:-}"
build_postgres="${LIBPGLITE_BUILD_POSTGRES:-0}"
fetch_other_extensions="${LIBPGLITE_FETCH_OTHER_EXTENSIONS:-0}"
build_other_extensions="${LIBPGLITE_BUILD_OTHER_EXTENSIONS:-0}"
dependency_prefix="${LIBPGLITE_NATIVE_DEPENDENCY_PREFIX:-}"

if [[ "$(uname -s)" == "Darwin" && -z "${MACOSX_DEPLOYMENT_TARGET:-}" ]]; then
  export MACOSX_DEPLOYMENT_TARGET=11.0
fi

usage() {
  cat >&2 <<'USAGE'
usage: scripts/prepare-native-pglite-link.sh [--source-dir <path>] [--out <manifest>] [--build-postgres] [--fetch-other-extensions] [--build-other-extensions] [--dependency-prefix <path>]

Validates the pinned postgres-pglite source substrate and writes a native link
manifest. By default it validates the source and compiles PGlite-specific C
support. With --build-postgres, it also configures and compiles the pinned
Postgres backend as native PIC and emits release-grade archive inputs.

Environment:
  LIBPGLITE_POSTGRES_SOURCE_DIR=<path>
  LIBPGLITE_NATIVE_LINK_MANIFEST=<path>
  LIBPGLITE_BUILD_POSTGRES=1
  LIBPGLITE_FETCH_OTHER_EXTENSIONS=1
  LIBPGLITE_BUILD_OTHER_EXTENSIONS=1
  LIBPGLITE_NATIVE_DEPENDENCY_PREFIX=<path>
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
    --fetch-other-extensions)
      fetch_other_extensions=1
      shift
      ;;
    --build-other-extensions)
      fetch_other_extensions=1
      build_other_extensions=1
      shift
      ;;
    --dependency-prefix)
      dependency_prefix="${2:-}"
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
require make
require ar
require tar
require python3

sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

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
if [[ -n "$dependency_prefix" ]]; then
  case "$dependency_prefix" in
    /*) ;;
    *) dependency_prefix="$repo_root/$dependency_prefix" ;;
  esac
fi

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
case "$out" in
  /*) ;;
  *) out="$repo_root/$out" ;;
esac

build_dir="$(dirname "$out")"
patched_source="$build_dir/patched-postgres-pglite"
object_dir="$build_dir/objects"
extension_inventory="$build_dir/libpglite_native_extension_inventory.txt"
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

python3 "$repo_root/scripts/inventory-native-pglite-extensions.py" \
  --source-dir "$source_dir" \
  --out "$extension_inventory"

if [[ "$fetch_other_extensions" == "1" ]]; then
  python3 "$repo_root/scripts/materialize-native-pglite-other-extensions.py" \
    --inventory "$extension_inventory" \
    --out-root "$patched_source"
  python3 "$repo_root/scripts/inventory-native-pglite-extensions.py" \
    --source-dir "$patched_source" \
    --out "$extension_inventory"
fi

pglitec_object="$object_dir/pglitec.o"
cc -fPIC -O2 -DNDEBUG \
  -Dexit=libpglite_native_exit \
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

unique_contrib_sources_from_inventory() {
  awk -F'[=;]' '
    ($1 == "contrib_extension") {
      for (i = 2; i <= NF; i++) {
        if ($i == "source" && (i + 1) <= NF) {
          print $(i + 1)
        }
      }
    }
  ' "$extension_inventory" | LC_ALL=C sort -u
}

present_other_extensions_from_inventory() {
  awk -F'[=;]' '
    ($1 == "other_extension") {
      name = $2
      status = ""
      for (i = 3; i <= NF; i++) {
        if ($i == "status" && (i + 1) <= NF) {
          status = $(i + 1)
        }
      }
      if (status == "present") {
        print name
      }
    }
  ' "$extension_inventory" | LC_ALL=C sort -u
}

write_postgis_config_wrappers() {
  if [[ -z "$dependency_prefix" ]]; then
    echo "native PostGIS build requires --dependency-prefix" >&2
    exit 2
  fi
  local pkg_config_bin
  pkg_config_bin="$(command -v pkg-config)"
  postgis_config_wrapper_dir="$build_dir/postgis-config-wrappers"
  mkdir -p "$postgis_config_wrapper_dir"
  cat >"$postgis_config_wrapper_dir/geos-config" <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [[ "\${1:-}" == "--clibs" ]]; then
  "$dependency_prefix/bin/geos-config" --static-clibs | sed 's/-lstdc++/$native_postgis_cxx_lib/g'
else
  "$dependency_prefix/bin/geos-config" "\$@"
fi
EOF
  chmod +x "$postgis_config_wrapper_dir/geos-config"
  cat >"$postgis_config_wrapper_dir/pkg-config" <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [[ " \$* " == *" --libs "* && " \$* " == *" proj "* ]]; then
  PKG_CONFIG_LIBDIR="$dependency_prefix/lib/pkgconfig:$dependency_prefix/share/pkgconfig" "$pkg_config_bin" --static "\$@" | sed 's/-lstdc++/$native_postgis_cxx_lib/g'
else
  PKG_CONFIG_LIBDIR="$dependency_prefix/lib/pkgconfig:$dependency_prefix/share/pkgconfig" "$pkg_config_bin" "\$@"
fi
EOF
  chmod +x "$postgis_config_wrapper_dir/pkg-config"
}

build_native_postgis_extension() {
  if [[ -z "$dependency_prefix" ]]; then
    echo "native PostGIS build requires --dependency-prefix" >&2
    exit 2
  fi

  local extension_source="$patched_source/pglite/other_extensions/postgis"
  if [[ ! -d "$extension_source" ]]; then
    echo "native PGlite PostGIS source is missing: $extension_source" >&2
    exit 1
  fi

  write_postgis_config_wrappers

  python3 - "$extension_source/GNUmakefile.in" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text()
text = text.replace("SUBDIRS += @RASTER@ loader", "SUBDIRS += @RASTER@")
path.write_text(text)
PY

  (
    cd "$extension_source"
    if [[ ! -x ./configure ]]; then
      ./autogen.sh
    fi
    PROJ_VERSION=9.7.0 \
    PKG_CONFIG="$postgis_config_wrapper_dir/pkg-config" \
    PKG_CONFIG_LIBDIR="$dependency_prefix/lib/pkgconfig:$dependency_prefix/share/pkgconfig" \
    PATH="$dependency_prefix/bin:$PATH" \
    LDFLAGS="-L$dependency_prefix/lib" \
    CFLAGS="$native_dependency_cppflags" \
    CXXFLAGS="$native_dependency_cppflags" \
    ./configure \
      --with-pgconfig="$postgres_install_prefix/bin/pg_config" \
      --with-pic \
      --without-protobuf \
      --without-raster \
      --enable-static=no \
      --enable-shared=yes \
      --with-geosconfig="$postgis_config_wrapper_dir/geos-config" \
      --with-xml2config="$dependency_prefix/bin/xml2-config" \
      --with-jsondir="$dependency_prefix"

    make clean >/dev/null || true
    make \
      PG_CONFIG="$postgres_install_prefix/bin/pg_config" \
      prefix="$postgres_install_prefix" \
      exec_prefix="$postgres_install_prefix" \
      bindir="$postgres_install_prefix/bin" \
      BE_DLLLIBS="$native_extension_be_dlllibs" \
      LDFLAGS_SL="$native_postgis_ldflags_sl" \
      CFLAGS_SL="$native_dependency_cppflags" \
      CXXFLAGS_SL="$native_dependency_cppflags" \
      -j1
    make install \
      PG_CONFIG="$postgres_install_prefix/bin/pg_config" \
      prefix="$postgres_install_prefix" \
      exec_prefix="$postgres_install_prefix" \
      bindir="$postgres_install_prefix/bin" \
      BE_DLLLIBS="$native_extension_be_dlllibs" \
      LDFLAGS_SL="$native_postgis_ldflags_sl" \
      CFLAGS_SL="$native_dependency_cppflags" \
      CXXFLAGS_SL="$native_dependency_cppflags" \
      -j1
  )

  if [[ -d "$dependency_prefix/share/proj" ]]; then
    rm -rf "$postgres_install_prefix/share/proj"
    mkdir -p "$postgres_install_prefix/share"
    cp -R "$dependency_prefix/share/proj" "$postgres_install_prefix/share/proj"
  fi

  if [[ ! -f "$postgres_install_prefix/share/extension/postgis.control" ]]; then
    echo "native Postgres install prefix is missing PostGIS control file" >&2
    exit 1
  fi
  case "$(uname -s)" in
    Darwin) postgis_module="$postgres_install_prefix/lib/postgis-3.dylib" ;;
    Linux) postgis_module="$postgres_install_prefix/lib/postgis-3.so" ;;
    *) postgis_module="$postgres_install_prefix/lib/postgis-3" ;;
  esac
  if [[ ! -f "$postgis_module" ]]; then
    echo "native Postgres install prefix is missing PostGIS module: $postgis_module" >&2
    exit 1
  fi
  if [[ ! -f "$postgres_install_prefix/share/proj/proj.db" ]]; then
    echo "native Postgres install prefix is missing PostGIS projection data" >&2
    exit 1
  fi
}

native_extension_required_symbols() {
  if [[ ! -d "$postgres_install_prefix/lib" ]]; then
    return
  fi

  local undefined_symbols
  local defined_symbols
  undefined_symbols="$(mktemp)"
  defined_symbols="$(mktemp)"

  case "$(uname -s)" in
    Darwin)
      while IFS= read -r -d '' module; do
        nm -u "$module" 2>/dev/null || true
      done < <(find "$postgres_install_prefix/lib" -maxdepth 1 -type f -name '*.dylib' -print0) \
        | awk '{print $NF}' \
        | sed 's/^_//' \
        | grep -Ev '^(dyld_stub_binder|memcmp|memcpy|memmove|memset|strcmp|strlen|strncmp|strnlen|strchr|strrchr|strstr|strcasecmp|strncasecmp|malloc|calloc|realloc|free|abort|exit|__.*|_.*)$' \
        | LC_ALL=C sort -u >"$undefined_symbols"
      ;;
    Linux)
      while IFS= read -r -d '' module; do
        nm -u "$module" 2>/dev/null || true
      done < <(find "$postgres_install_prefix/lib" -maxdepth 1 -type f -name '*.so' -print0) \
        | awk '{print $NF}' \
        | sed 's/@.*//' \
        | grep -Ev '^(memcmp|memcpy|memmove|memset|strcmp|strlen|strncmp|strnlen|strchr|strrchr|strstr|strcasecmp|strncasecmp|malloc|calloc|realloc|free|abort|exit|__.*|_.*)$' \
        | LC_ALL=C sort -u >"$undefined_symbols"
      ;;
  esac

  nm -g "$pglitec_object" "$native_trap_object" \
    "$backend_archive" "$timezone_archive" "$common_archive" "$port_archive" 2>/dev/null \
    | awk '$2 ~ /^[TDBSC]$/ {print $3}' \
    | sed 's/^_//' \
    | LC_ALL=C sort -u >"$defined_symbols"

  comm -12 "$undefined_symbols" "$defined_symbols"
  rm -f "$undefined_symbols" "$defined_symbols"
}

if [[ "$build_postgres" == "1" ]]; then
  require pkg-config
  native_dependency_provider="host-pkg-config"
  native_dependency_prefix_manifest=""
  native_dependency_prefix_fingerprint=""
  native_dependency_packages=(libxslt libxml-2.0 zlib uuid)
  native_backend_link_packages=(libxslt libxml-2.0 zlib)
  native_crypto_packages=(openssl)
  native_uuid_impl="e2fs"
  if [[ -n "$dependency_prefix" ]]; then
    if [[ ! -d "$dependency_prefix" ]]; then
      echo "native dependency prefix not found: $dependency_prefix" >&2
      exit 2
    fi
    native_dependency_provider="libpglite-prefix"
    native_dependency_prefix_manifest="$build_dir/native-dependency-prefix.json"
    python3 "$repo_root/scripts/describe-native-dependency-prefix.py" \
      --prefix "$dependency_prefix" \
      --out "$native_dependency_prefix_manifest" \
      --require-complete \
      --require-static
    native_dependency_prefix_fingerprint="$(sha256 "$native_dependency_prefix_manifest")"
    export PKG_CONFIG_LIBDIR="$dependency_prefix/lib/pkgconfig:$dependency_prefix/share/pkgconfig"
    native_dependency_packages=(libxslt libxml-2.0 zlib)
    native_backend_link_packages=(libxslt libxml-2.0 zlib)
    native_crypto_packages=(openssl)
    native_uuid_impl="ossp"
  fi
  for package in "${native_dependency_packages[@]}" "${native_crypto_packages[@]}"; do
    if ! pkg-config --exists "$package"; then
      echo "missing native dependency pkg-config package required for extension parity: $package" >&2
      exit 2
    fi
  done

  native_dependency_cppflags="$(pkg-config --cflags "${native_dependency_packages[@]}" "${native_crypto_packages[@]}")"
  native_dependency_ldflags="$(pkg-config --libs-only-L "${native_backend_link_packages[@]}")"
  native_dependency_libs="$(pkg-config --libs-only-l "${native_backend_link_packages[@]}")"
  native_crypto_ldflags="$(pkg-config --libs-only-L "${native_crypto_packages[@]}")"
  native_crypto_libs="$(pkg-config --libs-only-l "${native_crypto_packages[@]}")"
  native_postgis_ldflags_sl=""
  if [[ -n "$dependency_prefix" ]]; then
    native_postgis_cxx_lib="-lstdc++"
    if [[ "$(uname -s)" == "Darwin" ]]; then
      native_postgis_cxx_lib="-lc++"
    fi
    native_postgis_ldflags_sl=(
      -L"$dependency_prefix/lib"
      -lgeos_c
      -lgeos
      -lproj
      -ljson-c
      -lsqlite3
      -ltiff
      -ldeflate
      -lz
      -lxml2
      "$native_postgis_cxx_lib"
      -lm
    )
    native_postgis_ldflags_sl="${native_postgis_ldflags_sl[*]}"
  fi
  native_extension_be_dlllibs=""
  if [[ "$(uname -s)" == "Darwin" ]]; then
    native_extension_be_dlllibs="-undefined dynamic_lookup"
  fi
  extension_inventory_fingerprint="$(sha256 "$extension_inventory")"

  build_env_fingerprint="source_commit=$source_commit
macos_deployment_target=${MACOSX_DEPLOYMENT_TARGET:-}
patch_fingerprint=$patch_fingerprint
extension_inventory_fingerprint=$extension_inventory_fingerprint
build_other_extensions=$build_other_extensions
native_dependency_provider=$native_dependency_provider
native_dependency_prefix=$dependency_prefix
native_dependency_prefix_fingerprint=$native_dependency_prefix_fingerprint
native_trap_fingerprint=$native_trap_fingerprint
pglite_copt=$pglite_copt
native_dependency_cppflags=$native_dependency_cppflags
native_dependency_ldflags=$native_dependency_ldflags
native_dependency_libs=$native_dependency_libs
native_crypto_ldflags=$native_crypto_ldflags
native_crypto_libs=$native_crypto_libs
native_postgis_cxx_lib=${native_postgis_cxx_lib:-}
native_postgis_ldflags_sl=$native_postgis_ldflags_sl
native_uuid_impl=$native_uuid_impl
native_extension_be_dlllibs=$native_extension_be_dlllibs"
  build_env_file="$postgres_build_dir/.libpglite-native-build-env"
  if [[ ! -f "$build_env_file" || "$(cat "$build_env_file")" != "$build_env_fingerprint" ]]; then
    rm -rf "$postgres_build_dir"
  fi
  mkdir -p "$postgres_build_dir"

  if [[ ! -x "$postgres_build_dir/config.status" ]]; then
    (
      cd "$postgres_build_dir"
      CPPFLAGS="$native_dependency_cppflags" \
      LDFLAGS="$native_dependency_ldflags" \
      LIBS="$native_dependency_libs" \
      "$patched_source/configure" \
        --without-readline \
        --without-icu \
        --without-llvm \
        --without-pam \
        --with-zlib \
        --without-openssl \
        --without-gssapi \
        --without-ldap \
        --with-libxml \
        --with-libxslt \
        --with-uuid="$native_uuid_impl" \
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
  find "$postgres_build_dir/src/pl/plpgsql/src" -maxdepth 1 -type f \( -name '*.dylib' -o -name '*.so' \) -delete
  make -C "$postgres_build_dir/src/pl/plpgsql/src" install \
    BE_DLLLIBS="$native_extension_be_dlllibs"
  make -C "$postgres_build_dir/src/bin/initdb" install
  make -C "$postgres_build_dir/src" install-local
  make -C "$postgres_build_dir/src/makefiles" install
  make -C "$postgres_build_dir/src/bin/pg_config" install

  while IFS= read -r contrib_source; do
    find "$postgres_build_dir/$contrib_source" -maxdepth 1 -type f -name '*.dylib' -delete
    make -C "$postgres_build_dir/$contrib_source" install \
      COPT="$pglite_copt" \
      LDFLAGS_SL="$native_dependency_ldflags $native_crypto_ldflags" \
      BE_DLLLIBS="$native_extension_be_dlllibs" \
      LIBS="$native_dependency_ldflags $native_dependency_libs $native_crypto_ldflags $native_crypto_libs"
  done < <(unique_contrib_sources_from_inventory)

  if [[ "$build_other_extensions" == "1" ]]; then
    while IFS= read -r extension; do
      if [[ "$extension" == "postgis" ]]; then
        build_native_postgis_extension
        continue
      fi
      extension_source="$patched_source/pglite/other_extensions/$extension"
      if [[ ! -d "$extension_source" ]]; then
        echo "native PGlite other extension source is missing: $extension_source" >&2
        exit 1
      fi
      make -C "$extension_source" clean \
        PG_CONFIG="$postgres_install_prefix/bin/pg_config" \
        OPTFLAGS="" >/dev/null || true
      make -C "$extension_source" \
        PG_CONFIG="$postgres_install_prefix/bin/pg_config" \
        OPTFLAGS="" \
        BE_DLLLIBS="$native_extension_be_dlllibs"
      make -C "$extension_source" install \
        PG_CONFIG="$postgres_install_prefix/bin/pg_config" \
        OPTFLAGS="" \
        BE_DLLLIBS="$native_extension_be_dlllibs"
      control_file="$postgres_install_prefix/share/extension/$extension.control"
      if [[ ! -f "$control_file" ]]; then
        echo "native Postgres install prefix is missing built PGlite other extension control file: $control_file" >&2
        exit 1
      fi
    done < <(present_other_extensions_from_inventory)
  fi

  for file in \
    "$postgres_install_prefix/bin/initdb" \
    "$postgres_install_prefix/bin/pg_config" \
    "$postgres_install_prefix/bin/postgres" \
    "$postgres_install_prefix/lib/pgxs/src/Makefile.global" \
    "$postgres_install_prefix/lib/pgxs/src/makefiles/pgxs.mk" \
    "$postgres_install_prefix/share/postgres.bki" \
    "$postgres_install_prefix/share/snowball_create.sql" \
    "$postgres_install_prefix/share/extension/plpgsql.control"; do
    if [[ ! -f "$file" ]]; then
      echo "native Postgres install prefix is missing required initdb/runtime file: $file" >&2
      exit 1
    fi
  done

  while IFS= read -r inventory_line; do
    case "$inventory_line" in
      contrib_extension=*)
        extension="${inventory_line#contrib_extension=}"
        extension="${extension%%;*}"
        control_file="$postgres_install_prefix/share/extension/$extension.control"
        if [[ ! -f "$control_file" ]]; then
          echo "native Postgres install prefix is missing inventoried contrib extension control file: $control_file" >&2
          exit 1
        fi
        ;;
    esac
  done <"$extension_inventory"

  printf '%s\n' "$build_env_fingerprint" >"$build_env_file"
fi

{
  echo "format=libpglite-native-link-manifest-v1"
  echo "source_repository=$source_repository"
  echo "source_ref=$pinned_ref"
  echo "source_commit=$source_commit"
  echo "source_dir=$source_dir"
  echo "patched_source_dir=$patched_source"
  echo "extension_inventory=$extension_inventory"
  while IFS= read -r inventory_line; do
    case "$inventory_line" in
      format=*) ;;
      *) echo "extension_$inventory_line" ;;
    esac
  done <"$extension_inventory"
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
    echo "patch_sha256=${patch_file#$repo_root/};sha256=$(sha256 "$patch_file")"
  done
  echo "patch_fingerprint=$patch_fingerprint"
  echo "object=$pglitec_object"
  echo "object=$native_trap_object"
  echo "native_exit_object=$native_exit_object"
  echo "native_trap_source=native/c/libpglite_native_trap.c"
  echo "native_trap_fingerprint=$native_trap_fingerprint"
  if [[ "$build_postgres" == "1" ]]; then
    echo "native_dependency_provider=$native_dependency_provider"
    if [[ -n "$dependency_prefix" ]]; then
      echo "native_dependency_prefix=$dependency_prefix"
      echo "native_dependency_prefix_manifest=$native_dependency_prefix_manifest"
      echo "native_dependency_prefix_manifest_sha256=$native_dependency_prefix_fingerprint"
    fi
    echo "archive=$backend_archive"
    echo "archive=$timezone_archive"
    echo "archive=$common_archive"
    echo "archive=$port_archive"
    for flag in $native_dependency_ldflags $native_dependency_libs; do
      case "$flag" in
        -L*|-l*) echo "link_arg=$flag" ;;
      esac
    done
    while IFS= read -r symbol; do
      [[ -n "$symbol" ]] || continue
      echo "backend_export_symbol=$symbol"
    done < <(native_extension_required_symbols)
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
