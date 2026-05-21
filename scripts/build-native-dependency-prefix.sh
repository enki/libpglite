#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
prefix="$repo_root/target/native-pglite/dependency-prefix"
sources="$repo_root/target/native-pglite/dependency-sources"
work_dir="$repo_root/target/native-pglite/dependency-build"
jobs="${LIBPGLITE_NATIVE_MAKE_JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
only=""
skip_fetch=0

usage() {
  cat >&2 <<'USAGE'
usage: scripts/build-native-dependency-prefix.sh [--prefix <path>] [--sources <path>] [--work-dir <path>] [--jobs <n>] [--only <name>] [--skip-fetch]

Builds the native equivalent of PGlite's /install/libs dependency prefix from
deps/native-pglite-dependencies.json. The default path is macOS-first; Linux
uses the same dependency order but is not release-baseline complete yet.

Environment:
  LIBPGLITE_NATIVE_MAKE_JOBS=<jobs>
  MACOSX_DEPLOYMENT_TARGET=<version>  default: 11.0 on Darwin
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      prefix="${2:-}"
      shift 2
      ;;
    --sources)
      sources="${2:-}"
      shift 2
      ;;
    --work-dir)
      work_dir="${2:-}"
      shift 2
      ;;
    --jobs)
      jobs="${2:-}"
      shift 2
      ;;
    --only)
      only="${2:-}"
      shift 2
      ;;
    --skip-fetch)
      skip_fetch=1
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

case "$prefix" in
  /*) ;;
  *) prefix="$repo_root/$prefix" ;;
esac
case "$sources" in
  /*) ;;
  *) sources="$repo_root/$sources" ;;
esac
case "$work_dir" in
  /*) ;;
  *) work_dir="$repo_root/$work_dir" ;;
esac

if [[ "$(uname -s)" == "Darwin" && -z "${MACOSX_DEPLOYMENT_TARGET:-}" ]]; then
  export MACOSX_DEPLOYMENT_TARGET=11.0
fi

for candidate in /opt/homebrew/opt/automake/bin /opt/homebrew/opt/libtool/bin; do
  if [[ -d "$candidate" ]]; then
    PATH="$candidate:$PATH"
  fi
done
export PATH

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    if [[ "$(uname -s)" == "Darwin" && "$1" == "automake" ]]; then
      echo "install the PGlite-aligned autotools prerequisite with: brew install automake" >&2
    fi
    exit 2
  fi
}

require_libtoolize() {
  if command -v glibtoolize >/dev/null 2>&1 || command -v libtoolize >/dev/null 2>&1; then
    return 0
  fi
  echo "missing required command: glibtoolize or libtoolize" >&2
  exit 2
}

refresh_config_scripts() {
  local dir="$1"
  local script candidate
  for script in config.guess config.sub; do
    if [[ ! -f "$dir/$script" ]]; then
      continue
    fi
    for candidate in \
      "/usr/share/misc/$script" \
      "/usr/share/automake/$script" \
      "/opt/homebrew/share/automake/$script" \
      /usr/share/automake-*/"$script" \
      /opt/homebrew/share/automake-*/"$script"; do
      if [[ -f "$candidate" ]]; then
        cp "$candidate" "$dir/$script"
        break
      fi
    done
  done
}

require cc
require cmake
require git
require make
require python3
require tar
require unzip

if [[ "$skip_fetch" != "1" ]]; then
  python3 "$repo_root/scripts/fetch-native-dependency-sources.py" --out "$sources"
else
  python3 "$repo_root/scripts/fetch-native-dependency-sources.py" --out "$sources" --verify-cache-only
fi

manifest="$sources/sources.json"
if [[ ! -f "$manifest" ]]; then
  echo "dependency source manifest is missing: $manifest" >&2
  exit 2
fi

source_value() {
  local name="$1"
  local key="$2"
  python3 - "$manifest" "$name" "$key" <<'PY'
import json
import sys

manifest, name, key = sys.argv[1:4]
data = json.loads(open(manifest).read())
for entry in data["sources"]:
    if entry["name"] == name:
        value = entry.get(key)
        if not isinstance(value, str) or not value:
            raise SystemExit(f"{name} has no {key}")
        print(value)
        raise SystemExit(0)
raise SystemExit(f"source entry not found: {name}")
PY
}

extract_archive() {
  local name="$1"
  local archive
  archive="$(source_value "$name" archive)"
  local src="$work_dir/src/$name"
  rm -rf "$src"
  mkdir -p "$src"
  case "$archive" in
    *.zip)
      local temp="$work_dir/src/${name}.unzip"
      rm -rf "$temp"
      mkdir -p "$temp"
      unzip -q "$archive" -d "$temp"
      local first
      first="$(find "$temp" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
      if [[ -z "$first" ]]; then
        echo "zip archive did not contain a source directory: $archive" >&2
        exit 1
      fi
      cp -R "$first"/. "$src"/
      rm -rf "$temp"
      ;;
    *)
      tar -xf "$archive" -C "$src" --strip-components 1
      ;;
  esac
  printf '%s\n' "$src"
}

git_source() {
  source_value "$1" checkout
}

run_step() {
  local name="$1"
  shift
  if [[ -n "$only" && "$only" != "$name" ]]; then
    return 0
  fi
  echo "==> dependency prefix: $name"
  "$@"
}

common_cflags="-O2 -fPIC"
common_cxxflags="-O2 -fPIC"
if [[ "$(uname -s)" == "Darwin" ]]; then
  common_cflags="$common_cflags -Werror=unguarded-availability-new"
  common_cxxflags="$common_cxxflags -Werror=unguarded-availability-new"
fi
common_env=(
  "CFLAGS=$common_cflags"
  "CXXFLAGS=$common_cxxflags"
  "CPPFLAGS=-I$prefix/include -I$prefix/include/libxml2"
  "LDFLAGS=-L$prefix/lib"
  "PKG_CONFIG_LIBDIR=$prefix/lib/pkgconfig:$prefix/share/pkgconfig"
)

fresh_prefix=1
if [[ -n "$only" ]]; then
  fresh_prefix=0
fi
if [[ "$fresh_prefix" == "1" ]]; then
  rm -rf "$prefix"
fi
mkdir -p "$prefix" "$work_dir/src" "$work_dir/build"

build_zlib() {
  local src
  src="$(extract_archive zlib)"
  (
    cd "$src"
    CC="${CC:-cc}" CFLAGS="$common_cflags" ./configure --static --prefix="$prefix"
    make -j"$jobs"
    make install
  )
}

build_libxml2() {
  require automake
  require_libtoolize
  local src
  src="$(extract_archive libxml2)"
  (
    cd "$src"
    ./autogen.sh --with-python=no --with-threads=no
    env "${common_env[@]}" ./configure \
      --enable-shared=no \
      --enable-static=yes \
      --with-python=no \
      --with-threads=no \
      --prefix="$prefix"
    make -j"$jobs"
    make install
  )
}

build_libxslt() {
  require automake
  require_libtoolize
  local src
  src="$(extract_archive libxslt)"
  (
    cd "$src"
    ./autogen.sh --with-python=no
    env "${common_env[@]}" ./configure \
      --enable-shared=no \
      --enable-static=yes \
      --with-python=no \
      --with-libxml-prefix="$prefix" \
      --with-pic=yes \
      --prefix="$prefix"
    make -j"$jobs"
    make install
  )
}

openssl_target() {
  case "$(uname -s):$(uname -m)" in
    Darwin:arm64) echo "darwin64-arm64-cc" ;;
    Darwin:x86_64) echo "darwin64-x86_64-cc" ;;
    Linux:x86_64) echo "linux-x86_64" ;;
    Linux:aarch64) echo "linux-aarch64" ;;
    *) echo "" ;;
  esac
}

build_openssl() {
  require perl
  local src target
  src="$(extract_archive openssl)"
  target="$(openssl_target)"
  if [[ -z "$target" ]]; then
    echo "unsupported OpenSSL target for $(uname -s) $(uname -m)" >&2
    exit 2
  fi
  (
    cd "$src"
    CFLAGS="$common_cflags" CXXFLAGS="$common_cxxflags" ./Configure \
      no-asm no-tests no-threads no-shared no-module \
      --prefix="$prefix" \
      "$target"
    make -j"$jobs"
    make install_sw install_ssldirs
  )
}

build_ossp_uuid() {
  local src
  src="$(extract_archive ossp-uuid)"
  (
    cd "$src"
    refresh_config_scripts "$src"
    env "${common_env[@]}" ./configure \
      --enable-shared=no \
      --enable-static=yes \
      --with-perl=no \
      --with-perl-compat=no \
      --with-php=no \
      --with-pic=yes \
      --prefix="$prefix"
    make -j"$jobs"
    make install || true
    ln -sf libuuid.a "$prefix/lib/libossp-uuid.a"
    mkdir -p "$prefix/include/ossp"
    # PostgreSQL checks <ossp/uuid.h> after Darwin system headers. OSSP's
    # installed uuid.h also names uuid_t, so expose a prefix-local wrapper that
    # preserves OSSP function names while keeping the abstract type distinct.
    python3 - "$prefix/include/uuid.h" "$prefix/include/ossp/uuid.h" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
text = source.read_text()
start = text.index("/* workaround conflicts with system headers */")
end = text.index("/* required system headers */")
text = text[:start] + "/* required system headers */\n#include <stddef.h>\n" + text[end + len("/* required system headers */\n"):]
text = text.replace("__UUID_H__", "__OSSP_UUID_H__")
text = text.replace("#define __OSSP_UUID_H__", "#define __OSSP_UUID_H__\n#define uuid_t ossp_uuid_t", 1)
out.write_text(text)
PY
  )
}

build_json_c() {
  local src build
  src="$(git_source json-c)"
  build="$work_dir/build/json-c"
  rm -rf "$build"
  cmake -S "$src" -B "$build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DENABLE_THREADING=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DDISABLE_EXTRA_LIBS=ON \
    -DBUILD_APPS=OFF \
    -DBUILD_TESTING=OFF
  cmake --build "$build" -j "$jobs"
  cmake --build "$build" --target install
}

build_libdeflate() {
  local src build
  src="$(git_source libdeflate)"
  build="$work_dir/build/libdeflate"
  rm -rf "$build"
  cmake -S "$src" -B "$build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DLIBDEFLATE_BUILD_SHARED_LIB=OFF \
    -DLIBDEFLATE_BUILD_TESTS=OFF
  cmake --build "$build" -j "$jobs"
  cmake --build "$build" --target install
}

build_libtiff() {
  require automake
  require_libtoolize
  local src
  src="$(extract_archive libtiff)"
  (
    cd "$src"
    ./autogen.sh
    env "${common_env[@]}" ./configure \
      --with-pic \
      --disable-webp \
      --disable-zstd \
      --disable-lzma \
      --disable-jbig \
      --disable-old-jpeg \
      --disable-jpeg \
      --disable-pixarlog \
      --disable-mdi \
      --disable-opengl \
      --disable-win32-io \
      --with-zlib-include-dir="$prefix/include" \
      --with-zlib-lib-dir="$prefix/lib" \
      --with-libdeflate-include-dir="$prefix/include" \
      --with-libdeflate-lib-dir="$prefix/lib" \
      --prefix="$prefix" \
      --enable-shared=no
    make -j"$jobs"
    make install
  )
}

build_sqlite() {
  local src
  src="$(extract_archive sqlite)"
  (
    cd "$src"
    env "${common_env[@]}" ./configure \
      --disable-shared \
      --disable-threadsafe \
      --prefix="$prefix"
    if [[ "$(uname -s)" == "Darwin" ]]; then
      # The macOS 15 SDK exposes strchrnul even when the deployment target is 11.0.
      perl -0pi -e 's/#define HAVE_STRCHRNUL 1/#define HAVE_STRCHRNUL 0/' sqlite_cfg.h
    fi
    make -j"$jobs"
    make install
  )
}

build_proj() {
  local src build
  src="$(extract_archive proj)"
  build="$work_dir/build/proj"
  rm -rf "$build"
  cmake -S "$src" -B "$build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DEMBED_RESOURCE_FILES=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DSQLite3_INCLUDE_DIR="$prefix/include" \
    -DSQLite3_LIBRARY="$prefix/lib/libsqlite3.a" \
    -DEXE_SQLITE3="$prefix/bin/sqlite3" \
    -DTIFF_LIBRARY="$prefix/lib/libtiff.a" \
    -DTIFF_INCLUDE_DIR="$prefix/include" \
    -DENABLE_CURL=OFF \
    -DBUILD_APPS=OFF \
    -DBUILD_TESTING=OFF
  cmake --build "$build" -j "$jobs"
  cmake --build "$build" --target install
}

build_geos() {
  local src build
  src="$(extract_archive geos)"
  build="$work_dir/build/geos"
  rm -rf "$build"
  cmake -S "$src" -B "$build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DBUILD_TESTING=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DBUILD_GEOSOP=OFF
  cmake --build "$build" -j "$jobs"
  cmake --build "$build" --target install
}

run_step zlib build_zlib
run_step libxml2 build_libxml2
run_step libxslt build_libxslt
run_step openssl build_openssl
run_step ossp-uuid build_ossp_uuid
run_step json-c build_json_c
run_step libdeflate build_libdeflate
run_step libtiff build_libtiff
run_step sqlite build_sqlite
run_step proj build_proj
run_step geos build_geos

if [[ -n "$only" ]]; then
  python3 "$repo_root/scripts/describe-native-dependency-prefix.py" \
    --prefix "$prefix" \
    --out "$prefix/native-dependency-prefix.json"
else
  python3 "$repo_root/scripts/describe-native-dependency-prefix.py" \
    --prefix "$prefix" \
    --out "$prefix/native-dependency-prefix.json" \
    --require-complete \
    --require-static
fi
echo "wrote $prefix/native-dependency-prefix.json"
