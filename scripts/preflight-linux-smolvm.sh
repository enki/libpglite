#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version=""
smolvm="${LIBPGLITE_SMOLVM:-"$repo_root/../smolvm/target/release/smolvm"}"
image="${LIBPGLITE_LINUX_BASELINE_IMAGE:-ubuntu:24.04}"
jobs="${LIBPGLITE_NATIVE_MAKE_JOBS:-4}"
dry_run=0

usage() {
  cat >&2 <<'USAGE'
usage: scripts/preflight-linux-smolvm.sh [--dry-run] <version>

Runs the native release preflight inside an Ubuntu guest through ../smolvm.
This is the local Linux baseline lane for ADR-0006 while CI/release containers
are still being brought up.

Environment:
  LIBPGLITE_SMOLVM=<path>                  default: ../smolvm/target/release/smolvm
  LIBPGLITE_LINUX_BASELINE_IMAGE=<image>   default: ubuntu:24.04
  LIBPGLITE_NATIVE_MAKE_JOBS=<jobs>        default: 4 in the guest
  LIBPGLITE_POSTGRES_SOURCE_DIR=<path>      default: vendor/postgres-pglite or ../postgres-pglite
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -n "$version" ]]; then
        echo "unexpected argument: $1" >&2
        usage
        exit 2
      fi
      version="$1"
      shift
      ;;
  esac
done

if [[ -z "$version" ]]; then
  usage
  exit 2
fi

if [[ ! -x "$smolvm" ]]; then
  echo "smolvm binary is not executable: $smolvm" >&2
  echo "build or install smolvm, or set LIBPGLITE_SMOLVM=/path/to/smolvm" >&2
  exit 2
fi

smolvm_lib_dir="${LIBPGLITE_SMOLVM_LIB_DIR:-}"
if [[ -z "$smolvm_lib_dir" ]]; then
  smolvm_bin_dir="$(cd "$(dirname "$smolvm")" && pwd)"
  smolvm_repo_guess="$(cd "$smolvm_bin_dir/../.." && pwd)"
  smolvm_lib_dir="$smolvm_repo_guess/lib"
fi

smolvm_env=()
if [[ "$(uname -s)" == "Darwin" && -d "$smolvm_lib_dir" ]]; then
  smolvm_env=(env "DYLD_LIBRARY_PATH=$smolvm_lib_dir${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}")
fi

postgres_source_dir="${LIBPGLITE_POSTGRES_SOURCE_DIR:-}"
if [[ -z "$postgres_source_dir" ]]; then
  if [[ -d "$repo_root/vendor/postgres-pglite" ]]; then
    postgres_source_dir="$repo_root/vendor/postgres-pglite"
  elif [[ -d "$repo_root/../postgres-pglite" ]]; then
    postgres_source_dir="$repo_root/../postgres-pglite"
  fi
fi

postgres_volume=()
if [[ -n "$postgres_source_dir" ]]; then
  postgres_source_dir="$(cd "$postgres_source_dir" && pwd)"
  postgres_volume=(--volume "$postgres_source_dir:/mnt/postgres-pglite")
fi

guest_script='
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  autoconf \
  automake \
  bison \
  build-essential \
  ca-certificates \
  cmake \
  curl \
  flex \
  git \
  libtool \
  make \
  perl \
  pkg-config \
  python3 \
  tar \
  unzip \
  zstd
if ! id libpglite >/dev/null 2>&1; then
  useradd -m -s /bin/bash libpglite
fi
runuser -u libpglite -- bash -lc '"'"'
set -euo pipefail
if ! command -v cargo >/dev/null 2>&1; then
  curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
fi
. "$HOME/.cargo/env"
cd /mnt/libpglite
git config --global --add safe.directory /mnt/libpglite || true
if [[ -d /mnt/postgres-pglite ]]; then
  git config --global --add safe.directory /mnt/postgres-pglite || true
  export LIBPGLITE_POSTGRES_SOURCE_DIR=/mnt/postgres-pglite
fi
export CARGO_TARGET_DIR=/tmp/libpglite-cargo-target
export LIBPGLITE_NATIVE_BUILD_ROOT=/tmp/libpglite-native
export LIBPGLITE_RELEASE_OUT_DIR=/tmp/libpglite-dist
export LIBPGLITE_NATIVE_MAKE_JOBS='"$jobs"'
scripts/preflight-native-plugin-release.sh '"$version"'
'"'"'
'

cmd=(
  "${smolvm_env[@]}"
  "$smolvm"
  machine
  run
  --net
  --image "$image"
  --volume "$repo_root:/mnt/libpglite"
  "${postgres_volume[@]}"
  --
  bash
  -lc
  "$guest_script"
)

if [[ "$dry_run" == "1" ]]; then
  printf '%q ' "${cmd[@]}"
  printf '\n'
  exit 0
fi

"${cmd[@]}"
