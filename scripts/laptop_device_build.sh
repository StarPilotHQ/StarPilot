#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${ROOT_DIR}"

HOST_ROOT_DIR="${COMMA_HOST_ROOT_DIR:-${ROOT_DIR}}"
DOCKER_RUN_USER="${COMMA_DOCKER_RUN_USER:-$(id -u):$(id -g)}"
HOST_VENV_DIR="${COMMA_HOST_VENV_DIR:-${HOST_ROOT_DIR}/.venv-linux-arm64}"

# Make Docker Desktop binaries discoverable (docker + credential helpers) even
# when the caller shell PATH is minimal.
if [[ -d /Applications/Docker.app/Contents/Resources/bin ]]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:${PATH}"
fi

IMAGE_NAME="${COMMA_BUILD_IMAGE:-openpilot-local-arm64-builder:latest}"
SYSROOT_DIR_DEFAULT="${ROOT_DIR}/.comma_sysroot"
SYSROOT_DIR="${COMMA_SYSROOT_DIR:-${SYSROOT_DIR_DEFAULT}}"
HOST_SYSROOT_DIR="${COMMA_HOST_SYSROOT_DIR:-${SYSROOT_DIR}}"
HOST_CACHE_DIR="${COMMA_HOST_CACHE_DIR:-${HOST_ROOT_DIR}/.cache}"
HOST_DOCKERFILE_PATH="${COMMA_HOST_DOCKERFILE_PATH:-${HOST_ROOT_DIR}/tools/laptop_device_build/Dockerfile}"

# Docker treats a relative bind source as a named volume. Resolve accepted
# overrides against this checkout before any mount is assembled.
for path_name in HOST_VENV_DIR SYSROOT_DIR HOST_SYSROOT_DIR HOST_CACHE_DIR; do
  path_value="${!path_name}"
  if [[ "${path_value}" != /* ]]; then
    printf -v "${path_name}" '%s/%s' "${ROOT_DIR}" "${path_value}"
  fi
done

# A Docker bind mount must never point at a different checkout than the tree
# whose outputs this script scrubs and marks prebuilt. Keep all writable build
# locations under this checkout, including env-configured overrides.
[[ "$(cd "${HOST_ROOT_DIR}" && pwd -P)" == "$(cd "${ROOT_DIR}" && pwd -P)" ]] || {
  echo "ERROR: COMMA_HOST_ROOT_DIR must resolve to this checkout" >&2; exit 1;
}
for writable in "${SYSROOT_DIR}" "${HOST_SYSROOT_DIR}" "${HOST_VENV_DIR}" "${HOST_CACHE_DIR}"; do
  parent="$(cd "$(dirname "${writable}")" && pwd -P)" || exit 1
  [[ "${parent}" == "${ROOT_DIR}" ]] || {
    echo "ERROR: writable build paths must be direct children of this checkout: ${writable}" >&2; exit 1;
  }
  if [[ -e "${writable}" ]]; then
    [[ "$(cd "${writable}" && pwd -P)" == "${ROOT_DIR}"/* ]] || {
      echo "ERROR: writable build path resolves outside this checkout: ${writable}" >&2; exit 1;
    }
  fi
done
[[ "${HOST_SYSROOT_DIR}" == "${SYSROOT_DIR}" ]] || {
  echo "ERROR: COMMA_HOST_SYSROOT_DIR must match COMMA_SYSROOT_DIR" >&2; exit 1;
}

usage() {
  cat <<'EOF'
Usage:
  scripts/laptop_device_build.sh doctor
  scripts/laptop_device_build.sh verify-artifacts
  scripts/laptop_device_build.sh setup [device-host] [device-user] [ssh-port]
  scripts/laptop_device_build.sh setup-sysroot <device-host> [device-user] [ssh-port]
  scripts/laptop_device_build.sh setup-sysroot-agnos [manifest-path]
  scripts/laptop_device_build.sh setup-sysroot-local <system-image-path>
  scripts/laptop_device_build.sh build-image
  scripts/laptop_device_build.sh build [jobs] [scons args...]
  scripts/laptop_device_build.sh scons [--no-scrub] [scons args...]
  scripts/laptop_device_build.sh manager [jobs] [--no-build] [-- manager args...]
  scripts/laptop_device_build.sh shell

Modes:
  doctor        Check host/container prerequisites for laptop-side device builds.
  verify-artifacts Check complete device outputs without running a build.
  setup         One-time setup for laptop builds (venv + image + sysroot).
  setup-sysroot Sync required runtime/linker libs from a comma device over SSH.
  setup-sysroot-agnos Download AGNOS system image and extract sysroot locally.
  setup-sysroot-local Extract sysroot from an existing local AGNOS system image.
  build-image   Build the Linux/aarch64 container image used for device-target builds.
  build         Run the complete device-target build; set prebuilt only after artifact checks.
  scons         Run raw scons in the larch64 container (no prebuilt touch).
  manager       Explicitly run openpilot/system/manager/manager.py in the container.
  shell         Open an interactive shell in the build container with all mounts.

Environment overrides:
  COMMA_BUILD_IMAGE   Container image tag (default: openpilot-local-arm64-builder:latest)
  COMMA_SYSROOT_DIR   Sysroot location (default: ./.comma_sysroot)
  COMMA_AUTOSTART_DOCKER  Auto-launch Docker Desktop on macOS when needed (default: 1)
  RELEASE, CERT       For release firmware, set RELEASE to a nonempty value and CERT to
                      a readable certificate file; it is mounted read-only.
EOF
}

err() {
  echo "ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || err "Missing required command: $1"
}

detect_engine() {
  local docker_cmd=""
  if command -v docker >/dev/null 2>&1; then
    docker_cmd="$(command -v docker)"
  elif [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
    docker_cmd="/Applications/Docker.app/Contents/Resources/bin/docker"
  fi

  if [[ -n "${docker_cmd}" ]]; then
    # Needed for docker credential helpers (e.g. docker-credential-desktop).
    export PATH="$(dirname "${docker_cmd}"):${PATH}"
    if ! "${docker_cmd}" info >/dev/null 2>&1; then
      if [[ "$(uname -s)" == "Darwin" ]] && [[ "${COMMA_AUTOSTART_DOCKER:-1}" =~ ^(1|true|yes|on)$ ]]; then
        if [[ -d /Applications/Docker.app ]] && command -v open >/dev/null 2>&1; then
          echo "Docker daemon is not running. Launching Docker Desktop..." >&2
          open -g -a Docker >/dev/null 2>&1 || open -a Docker >/dev/null 2>&1 || true
          for ((i=0; i<300; i++)); do
            if "${docker_cmd}" info >/dev/null 2>&1; then
              break
            fi
            sleep 1
          done
        fi
      fi
      if ! "${docker_cmd}" info >/dev/null 2>&1; then
        err "Docker CLI found, but Docker daemon is not running. Start Docker Desktop (open -a Docker) and retry."
      fi
    fi
    echo "${docker_cmd}"
    return
  fi
  if command -v podman >/dev/null 2>&1; then
    local podman_cmd
    podman_cmd="$(command -v podman)"
    if ! "${podman_cmd}" info >/dev/null 2>&1; then
      err "Podman CLI found, but Podman service is not running."
    fi
    echo "${podman_cmd}"
    return
  fi
  err "No container runtime found (install docker or podman)."
}

build_container_image_cmd() {
  local engine="$1"
  shift
  local build_args=("$@")
  if [[ -n "${COMMA_BASE_IMAGE:-}" ]]; then
    build_args=(--build-arg "BASE_IMAGE=${COMMA_BASE_IMAGE}" "${build_args[@]}")
  fi

  if [[ "${engine##*/}" == "docker" ]] && "${engine}" buildx version >/dev/null 2>&1; then
    "${engine}" buildx build --load --platform linux/arm64 "${build_args[@]}"
    return
  fi

  "${engine}" build --platform linux/arm64 "${build_args[@]}"
}

ensure_image_exists() {
  local engine="$1"
  if ! "${engine}" image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
    echo "Container image ${IMAGE_NAME} not found. Building it now..."
    build_container_image_cmd "${engine}" -f "${HOST_DOCKERFILE_PATH}" -t "${IMAGE_NAME}" "${HOST_ROOT_DIR}"
  fi
  assert_image_arch "${engine}"
  ensure_image_capnp_version "${engine}"
}

assert_image_arch() {
  local engine="$1"
  local arch
  arch="$("${engine}" image inspect "${IMAGE_NAME}" --format '{{.Architecture}}' 2>/dev/null || true)"
  if [[ "${arch}" != "arm64" ]]; then
    err "Container image ${IMAGE_NAME} is '${arch:-unknown}', expected 'arm64'. Rebuild with: ${engine} build --pull --platform linux/arm64 -f tools/laptop_device_build/Dockerfile -t ${IMAGE_NAME} ."
  fi
}

expected_capnp_version() {
  if [[ -n "${COMMA_EXPECTED_CAPNP_VERSION:-}" ]]; then
    echo "${COMMA_EXPECTED_CAPNP_VERSION}"
    return
  fi

  local dockerfile_version=""
  dockerfile_version="$(sed -n 's/^ARG CAPNP_VERSION=\(.*\)$/\1/p' "${ROOT_DIR}/tools/laptop_device_build/Dockerfile" | head -n 1)"
  if [[ -n "${dockerfile_version}" ]]; then
    echo "${dockerfile_version}"
    return
  fi

  local header_path="${ROOT_DIR}/openpilot/cereal/gen/cpp/custom.capnp.h"
  if [[ ! -f "${header_path}" ]]; then
    err "Unable to determine expected Cap'n Proto version. Set COMMA_EXPECTED_CAPNP_VERSION or restore tools/laptop_device_build/Dockerfile ARG CAPNP_VERSION."
  fi

  local raw_version=""
  raw_version="$(sed -n 's/^#elif CAPNP_VERSION != \([0-9][0-9]*\)$/\1/p' "${header_path}" | head -n 1)"
  [[ -n "${raw_version}" ]] || err "Unable to determine expected Cap'n Proto version from ${header_path}."

  local major=$(( raw_version / 1000000 ))
  local minor=$(( (raw_version / 1000) % 1000 ))
  local micro=$(( raw_version % 1000 ))
  echo "${major}.${minor}.${micro}"
}

image_capnp_version() {
  local engine="$1"
  local version_output=""

  version_output="$("${engine}" run --rm --platform linux/arm64 "${IMAGE_NAME}" capnp --version 2>&1 || true)"
  printf '%s\n' "${version_output}" \
    | sed -nE 's/.*version[[:space:]]+([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' \
    | head -n 1
}

ensure_image_capnp_version() {
  local engine="$1"
  local expected actual
  expected="$(expected_capnp_version)"
  actual="$(image_capnp_version "${engine}")"

  if [[ "${actual}" == "${expected}" ]]; then
    return
  fi

  echo "Container image ${IMAGE_NAME} has Cap'n Proto ${actual:-unknown}, expected ${expected}. Rebuilding it now..."
  build_container_image_cmd "${engine}" -f "${HOST_DOCKERFILE_PATH}" -t "${IMAGE_NAME}" "${HOST_ROOT_DIR}"

  actual="$(image_capnp_version "${engine}")"
  if [[ "${actual}" != "${expected}" ]]; then
    err "Container image ${IMAGE_NAME} still has Cap'n Proto ${actual:-unknown} after rebuild; expected ${expected}."
  fi
}

assert_runtime_machine() {
  local engine="$1"
  local machine
  machine="$("${engine}" run --rm --platform linux/arm64 "${IMAGE_NAME}" bash -lc "readelf -h \$(command -v python3) | awk -F: '/Machine/ {gsub(/^ +/, \"\", \$2); print \$2}'" 2>/dev/null || true)"
  if [[ "${machine}" != "AArch64" ]]; then
    err "Container Python runtime is '${machine:-unknown}', expected 'AArch64'. This image can still build device artifacts, but cannot run manager with larch64 Python extensions. Rebuild from an arm64-capable base image before using 'manager' mode."
  fi
}

default_jobs() {
  if command -v nproc >/dev/null 2>&1; then
    nproc
  elif command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.ncpu
  else
    echo 8
  fi
}

build_container_image() {
  local engine
  engine="$(detect_engine)"
  build_container_image_cmd "${engine}" \
    -f "${HOST_DOCKERFILE_PATH}" \
    -t "${IMAGE_NAME}" "${HOST_ROOT_DIR}"
  assert_image_arch "${engine}"
}

sanitize_sysroot_headers() {
  # Device /usr/local/include can include older capnp/kj headers that conflict
  # with generated cereal headers in this tree. Keep ffmpeg headers, drop capnp.
  local removed=0
  local path=""
  for path in \
    "${SYSROOT_DIR}/usr/local/include/capnp" \
    "${SYSROOT_DIR}/usr/local/include/kj"; do
    if [[ -e "${path}" ]]; then
      rm -rf "${path}"
      removed=1
    fi
  done
  if [[ "${removed}" -eq 1 ]]; then
    echo "Sanitized sysroot headers: removed capnp/kj from usr/local/include"
  fi
}

ensure_sysroot_layout() {
  [[ -d "${SYSROOT_DIR}/usr/local/lib" ]] || err "Missing ${SYSROOT_DIR}/usr/local/lib. Run setup-sysroot first."
  [[ -d "${SYSROOT_DIR}/usr/local/include" ]] || err "Missing ${SYSROOT_DIR}/usr/local/include. Run setup-sysroot first."
  [[ -d "${SYSROOT_DIR}/lib/aarch64-linux-gnu" ]] || err "Missing ${SYSROOT_DIR}/lib/aarch64-linux-gnu. Run setup-sysroot first."
  [[ -d "${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu" ]] || err "Missing ${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu. Run setup-sysroot first."
  [[ -d "${SYSROOT_DIR}/usr/include" ]] || err "Missing ${SYSROOT_DIR}/usr/include. Run setup-sysroot first."
  [[ -d "${SYSROOT_DIR}/system/vendor/lib64" ]] || err "Missing ${SYSROOT_DIR}/system/vendor/lib64. Run setup-sysroot first."
  sanitize_sysroot_headers
  repair_sysroot_linker
}

is_aarch64_elf() {
  local path="${1:-}"
  [[ -f "${path}" ]] || return 1
  local desc
  desc="$(file -b "${path}" 2>/dev/null || true)"
  [[ "${desc}" == *"ELF 64-bit LSB"* && "${desc}" == *"ARM aarch64"* ]]
}

repair_sysroot_linker() {
  local root_ld="${SYSROOT_DIR}/lib/ld-linux-aarch64.so.1"
  local lib_ld="${SYSROOT_DIR}/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"
  local usr_ld="${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"

  # Some sysroot extraction paths can create this as an empty directory.
  if [[ -d "${lib_ld}" ]]; then
    rm -rf "${lib_ld}"
  fi

  # Ensure lib-path loader exists (symlink to usr/lib loader is fine).
  if [[ ! -e "${lib_ld}" && -f "${usr_ld}" ]]; then
    mkdir -p "$(dirname "${lib_ld}")"
    ln -s "../../usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1" "${lib_ld}"
  fi
  # Debian's libc.so linker script names the loader at /lib directly, while
  # AGNOS stores it under the multiarch directory.
  if [[ ! -e "${root_ld}" && -f "${lib_ld}" ]]; then
    ln -s "aarch64-linux-gnu/ld-linux-aarch64.so.1" "${root_ld}"
  fi
}

resolve_ldso_source() {
  local lib_ld="${SYSROOT_DIR}/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"
  local usr_ld="${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"

  repair_sysroot_linker

  if is_aarch64_elf "${lib_ld}"; then
    echo "${lib_ld}"
    return 0
  fi
  if is_aarch64_elf "${usr_ld}"; then
    echo "${usr_ld}"
    return 0
  fi

  err "Missing valid aarch64 dynamic loader in sysroot. Expected one of: ${lib_ld} or ${usr_ld}. Re-run setup-sysroot."
}

scrub_mixed_arch_artifacts() {
  # Desktop launchers can leave macOS objects with the same paths as larch64 outputs.
  # Remove known cross-target extension intermediates so device builds always relink.
  rm -f \
    "${ROOT_DIR}/.sconsign.dblite" \
    "${ROOT_DIR}/openpilot/common/libcommon.a" \
    "${ROOT_DIR}/openpilot/common/params.o" \
    "${ROOT_DIR}/openpilot/common/params_c.os" \
    "${ROOT_DIR}/openpilot/cereal/libcereal.a" \
    "${ROOT_DIR}/openpilot/cereal/libsocketmaster.a" \
    "${ROOT_DIR}/openpilot/cereal/gen/cpp/"*.capnp.c++ \
    "${ROOT_DIR}/openpilot/cereal/gen/cpp/"*.capnp.h \
    "${ROOT_DIR}/openpilot/cereal/gen/cpp/"*.capnp.o \
    "${ROOT_DIR}/openpilot/cereal/messaging/"*.o \
    "${ROOT_DIR}/openpilot/cereal/messaging/bridge" \
    "${ROOT_DIR}/msgq_repo/msgq/"*.os \
    "${ROOT_DIR}/msgq_repo/msgq/"*.o \
    "${ROOT_DIR}/msgq_repo/libmsgq.a" \
    "${ROOT_DIR}/msgq_repo/libvisionipc.a" \
    "${ROOT_DIR}/openpilot/starpilot/ui/native/_raylib_extra.so" \
    "${ROOT_DIR}/msgq_repo/msgq/_ipc.so" \
    "${ROOT_DIR}/msgq_repo/msgq/visionipc/"*.os \
    "${ROOT_DIR}/msgq_repo/msgq/visionipc/"*.o \
    "${ROOT_DIR}/msgq_repo/msgq/visionipc/_visionipc.so" \
    "${ROOT_DIR}/openpilot/common/libparams_c.so" \
    "${ROOT_DIR}/openpilot/selfdrive/pandad/can_list_to_can_capnp.o" \
    "${ROOT_DIR}/openpilot/selfdrive/pandad/libcan_list_to_can_capnp.a" \
    "${ROOT_DIR}/openpilot/selfdrive/pandad/pandad"
}

sync_sysroot_from_device() {
  local host="${1:-}"
  local user="${2:-comma}"
  local port="${3:-22}"
  [[ -n "${host}" ]] || err "setup-sysroot requires <device-host>."

  require_cmd rsync
  mkdir -p "${SYSROOT_DIR}/usr/local" "${SYSROOT_DIR}/lib" \
    "${SYSROOT_DIR}/usr/lib" "${SYSROOT_DIR}/system/vendor"
  local ssh_cmd="ssh -p ${port} -o StrictHostKeyChecking=accept-new"

  rsync -a --delete -e "${ssh_cmd}" "${user}@${host}:/usr/local/lib/" "${SYSROOT_DIR}/usr/local/lib/"
  rsync -a --delete -e "${ssh_cmd}" "${user}@${host}:/usr/local/include/" "${SYSROOT_DIR}/usr/local/include/"
  rsync -a --delete -e "${ssh_cmd}" "${user}@${host}:/lib/aarch64-linux-gnu/" "${SYSROOT_DIR}/lib/aarch64-linux-gnu/"
  rsync -a --delete -e "${ssh_cmd}" "${user}@${host}:/usr/lib/aarch64-linux-gnu/" "${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu/"
  rsync -a --delete -e "${ssh_cmd}" "${user}@${host}:/usr/include/" "${SYSROOT_DIR}/usr/include/"

  local vendor_src=""
  for candidate in /system/vendor/lib64 /vendor/lib64; do
    if ssh -p "${port}" -o StrictHostKeyChecking=accept-new "${user}@${host}" "test -d '${candidate}'"; then
      vendor_src="${candidate}"
      break
    fi
  done

  if [[ -n "${vendor_src}" ]]; then
    rsync -a --delete -e "${ssh_cmd}" "${user}@${host}:${vendor_src}/" "${SYSROOT_DIR}/system/vendor/lib64/"
  else
    local vendor_dst="${SYSROOT_DIR}/system/vendor/lib64"
    echo "Warning: no vendor lib64 dir found on device; falling back to usr/lib/aarch64-linux-gnu"
    if [[ -L "${vendor_dst}" || -f "${vendor_dst}" ]]; then
      rm -f "${vendor_dst}"
    elif [[ -d "${vendor_dst}" ]]; then
      rm -rf "${vendor_dst}"
    fi
    mkdir -p "${SYSROOT_DIR}/system/vendor"
    ln -s ../../usr/lib/aarch64-linux-gnu "${vendor_dst}"
  fi

  sanitize_sysroot_headers

  echo "Sysroot synced to: ${SYSROOT_DIR}"
}

setup_sysroot_from_agnos() {
  local manifest="${1:-openpilot/system/hardware/tici/agnos.json}"
  local engine
  engine="$(detect_engine)"
  ensure_image_exists "${engine}"
  mkdir -p "${SYSROOT_DIR}" "${ROOT_DIR}/.cache/agnos" "${HOST_CACHE_DIR}/agnos"

  "${engine}" run --rm --platform linux/arm64 \
    --user "${DOCKER_RUN_USER}" \
    -v "${HOST_ROOT_DIR}:/work" \
    -v "${HOST_SYSROOT_DIR}:/opt/tici-sysroot" \
    -v "${HOST_CACHE_DIR}:/work/.cache" \
    -w /work \
    "${IMAGE_NAME}" \
    /usr/bin/python3 tools/laptop_device_build/extract_sysroot_from_agnos.py \
      --manifest "${manifest}" \
      --output-dir /opt/tici-sysroot \
      --cache-dir /work/.cache/agnos
}

setup_sysroot_from_local_image() {
  local image_path="${1:-}"
  [[ -f "${image_path}" ]] || err "setup-sysroot-local requires an existing system image path."
  image_path="$(cd "$(dirname "${image_path}")" && pwd)/$(basename "${image_path}")"
  local engine
  engine="$(detect_engine)"
  ensure_image_exists "${engine}"
  mkdir -p "${SYSROOT_DIR}" "${HOST_CACHE_DIR}/agnos"
  "${engine}" run --rm --platform linux/arm64 \
    --user "${DOCKER_RUN_USER}" \
    -v "${HOST_ROOT_DIR}:/work" \
    -v "${HOST_SYSROOT_DIR}:/opt/tici-sysroot" \
    -v "${HOST_CACHE_DIR}:/work/.cache" \
    -v "${image_path}:/opt/source-system.img:ro" \
    -w /work \
    "${IMAGE_NAME}" \
    /usr/bin/python3 tools/laptop_device_build/extract_sysroot_from_agnos.py \
      --image-path /opt/source-system.img \
      --output-dir /opt/tici-sysroot \
      --cache-dir /work/.cache/agnos
}

run_larch64_scons() {
  local jobs="$1"
  shift || true
  local cert_path=""
  if [[ ${RELEASE+x} ]]; then
    [[ -n "${RELEASE}" ]] || err "RELEASE must be nonempty; unset it for a DEBUG build."
    [[ -n "${CERT:-}" ]] || err "RELEASE requires CERT to name a certificate file."
    require_cmd realpath
    cert_path="$(realpath "${CERT}")" || err "CERT cannot be resolved: ${CERT}"
    [[ -f "${cert_path}" && -r "${cert_path}" ]] || err "CERT must be a readable regular file: ${CERT}"
    [[ "${cert_path}" != *:* ]] || err "CERT path cannot contain a colon: ${CERT}"
  fi
  local scrub_mode="${COMMA_SCONS_SCRUB_MODE:-full}"
  if [[ "${1:-}" == "--no-scrub" ]]; then
    scrub_mode="none"
    shift || true
  fi
  local firmware_only=1
  local firmware_targets=0
  local arg
  for arg in "$@"; do
    case "${arg}" in
      panda/board/obj/panda.bin.signed|panda/board/obj/panda_h7.bin.signed|panda/board/obj/body_h7.bin.signed|panda/board/obj/bootstub.panda.bin|panda/board/obj/bootstub.panda_h7.bin)
        firmware_targets=$((firmware_targets + 1)) ;;
      -j*|--jobs=*|--verbose|--cache-disable) ;;
      *) firmware_only=0 ;;
    esac
  done
  [[ "${firmware_targets}" -gt 0 ]] || firmware_only=0
  local engine
  local started_at
  engine="$(detect_engine)"
  ensure_image_exists "${engine}"
  if [[ "${firmware_only}" -eq 0 ]]; then
    ensure_sysroot_layout
  fi
  if [[ "${scrub_mode}" != "none" ]]; then
    scrub_mixed_arch_artifacts
  fi
  started_at="$(date +%s)"

  echo "==> Starting larch64 scons build"
  echo "    jobs: ${jobs}"
  echo "    note: warp artifact precompile can take several minutes on first run"

  mkdir -p "${ROOT_DIR}/.cache/scons" "${HOST_CACHE_DIR}/scons" "${HOST_VENV_DIR}"
  # SConstruct selects the device build only when /AGNOS exists on arm64.
  # These are build-time file probes, not a simulated runtime device.
  : > "${HOST_CACHE_DIR}/build-agnos-marker"
  if [[ "${firmware_only}" -eq 0 ]]; then
    : > "${HOST_CACHE_DIR}/build-ion-marker"
  fi

  local extra_args=""
  local jobs_prefix="-j${jobs}"
  if [[ "$#" -gt 0 ]]; then
    for arg in "$@"; do
      case "${arg}" in
        -j|--jobs|-j*|--jobs=*)
          jobs_prefix=""
          ;;
      esac
      extra_args+=" $(printf '%q' "${arg}")"
    done
  fi

  local cmd
cmd="$(cat <<EOF
set -euo pipefail
export UV_CACHE_DIR=/work/.cache/uv
export UV_PYTHON_INSTALL_DIR=/work/.cache/uv-python
export SCONS_CACHE=/work/.cache/scons
if [[ "${firmware_only}" -eq 0 ]]; then
  export COMMA_BUILD_SYSROOT=/opt/tici-sysroot
fi
export LD_LIBRARY_PATH=/usr/local/lib:/usr/lib/aarch64-linux-gnu:/lib/aarch64-linux-gnu:/opt/tici-sysroot/usr/local/lib:/opt/tici-sysroot/usr/lib/aarch64-linux-gnu:/opt/tici-sysroot/lib/aarch64-linux-gnu:/system/vendor/lib64:\${LD_LIBRARY_PATH:-}
export TMPDIR=/tmp
export HOME=/tmp
export PARAMS_ROOT=/tmp/params
mkdir -p "\${UV_CACHE_DIR}"
mkdir -p "\${PARAMS_ROOT}"
UV_PROJECT_ENVIRONMENT=/work/.venv-linux-arm64 uv sync --frozen --all-extras
source /work/.venv-linux-arm64/bin/activate
export COMMA_LAPTOP_CROSS_BUILD=1
CACHE_FLAG="--cache-disable"
if [[ "\${COMMA_ENABLE_SCONS_CACHE:-0}" =~ ^(1|true|yes|on)$ ]]; then
  CACHE_FLAG=""
fi
scons ${jobs_prefix} \${CACHE_FLAG}${extra_args}
EOF
)"

  local mounts=(-v "${HOST_ROOT_DIR}:/work" -v "${HOST_VENV_DIR}:/work/.venv-linux-arm64"
                -v "${HOST_CACHE_DIR}/build-agnos-marker:/AGNOS:ro"
                -v "${HOST_CACHE_DIR}:/work/.cache" -v "${HOST_CACHE_DIR}/scons:/data/scons_cache")
  if [[ -n "${cert_path}" ]]; then
    mounts+=(-v "${cert_path}:/tmp/starpilot-signing-cert:ro"
             -e "RELEASE=${RELEASE}" -e "CERT=/tmp/starpilot-signing-cert")
  fi
  # A Git worktree's .git file can point outside the mounted checkout. Mount
  # only its shared metadata read-only so generated firmware versions retain
  # the actual source commit instead of silently becoming "unknown".
  if [[ -f "${ROOT_DIR}/.git" ]]; then
    local gitdir common_gitdir
    gitdir="$(sed -n 's/^gitdir: //p' "${ROOT_DIR}/.git" | head -n 1)"
    common_gitdir="${gitdir%/worktrees/*}"
    if [[ "${common_gitdir}" == /*/.git && -d "${common_gitdir}" ]]; then
      mounts+=(-v "${common_gitdir}:${common_gitdir}:ro")
    fi
  fi
  if [[ "${firmware_only}" -eq 0 ]]; then
    mounts+=(-v "${HOST_SYSROOT_DIR}:/opt/tici-sysroot:ro"
             -v "${HOST_SYSROOT_DIR}/system/vendor/lib64:/system/vendor/lib64:ro"
             -v "${HOST_CACHE_DIR}/build-ion-marker:/dev/ion:ro")
  fi
  "${engine}" run --rm --platform linux/arm64 --user "${DOCKER_RUN_USER}" \
    "${mounts[@]}" -w /work "${IMAGE_NAME}" bash -lc "${cmd}"

  echo "==> larch64 scons completed in $(( $(date +%s) - started_at ))s"
}

setup_host_venv() {
  if [[ ! -f "${ROOT_DIR}/.venv/bin/activate" ]]; then
    if command -v uv >/dev/null 2>&1; then
      (cd "${ROOT_DIR}" && uv sync --frozen --all-extras)
    else
      err "Missing host .venv and uv. Install uv, then run uv sync --frozen --all-extras."
    fi
  fi
}

setup_all() {
  local host="${1:-}"
  local user="${2:-comma}"
  local port="${3:-22}"

  setup_host_venv
  build_container_image

  if [[ -n "${host}" ]]; then
    sync_sysroot_from_device "${host}" "${user}" "${port}"
  else
    setup_sysroot_from_agnos "openpilot/system/hardware/tici/agnos.json"
  fi
}

run_larch64_build() {
  local jobs="${1:-$(default_jobs)}"
  shift || true
  rm -f "${ROOT_DIR}/prebuilt"
  echo "==> Complete device-target project build"
  run_larch64_scons "${jobs}" "$@"
  # Pass-through options and explicit targets are useful, but they may be a
  # dry run, clean, question, or partial build. Only the plain default graph
  # may claim a complete current build.
  if [[ "$#" -ne 0 ]]; then
    echo "Build options/targets were supplied; prebuilt remains unset."
    return 0
  fi
  # A successful SCons invocation alone is insufficient if stale/prebuilt model
  # files were omitted. These are the current device-runtime outputs, including
  # QCOM-compiled driving/DM models and both camera warp families.
  python3 -m tools.laptop_device_build.package_model_chunks --source "${ROOT_DIR}/openpilot/selfdrive/modeld/models" --destination "${ROOT_DIR}/openpilot/selfdrive/modeld/models"
  verify_device_artifacts
  local registry_engine
  registry_engine="$(detect_engine)"
  "${registry_engine}" run --rm --platform linux/arm64 --user "${DOCKER_RUN_USER}" \
    -v "${HOST_ROOT_DIR}:/work:ro" -w /work "${IMAGE_NAME}" \
    /usr/bin/python3 /work/tools/laptop_device_build/validate_params_registry.py /work
  python3 "${ROOT_DIR}/openpilot/common/prebuilt_manifest.py" "${ROOT_DIR}"
  touch "${ROOT_DIR}/prebuilt"
}

verify_device_artifacts() {
  python3 "${ROOT_DIR}/tools/laptop_device_build/validate_artifacts.py" "${ROOT_DIR}" ||
    err "Device artifacts are incomplete or wrong-backend; prebuilt was not set."
}

manager_artifacts_ready() {
  [[ -f "${ROOT_DIR}/prebuilt" ]] &&
  python3 "${ROOT_DIR}/tools/laptop_device_build/validate_artifacts.py" "${ROOT_DIR}" >/dev/null 2>&1
}

run_manager() {
  local jobs="$(default_jobs)"
  local auto_build=1
  local manager_args=()

  if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
    jobs="$1"
    shift || true
  fi

  while [[ $# -gt 0 ]]; do
    case "${1}" in
      --no-build)
        auto_build=0
        shift
        ;;
      --)
        shift || true
        manager_args=("$@")
        break
        ;;
      *)
        manager_args+=("$1")
        shift
        ;;
    esac
  done

  if [[ "${auto_build}" -eq 1 ]]; then
    echo "Checking current source with the complete device-target build before manager..."
    run_larch64_build "${jobs}"
  elif ! manager_artifacts_ready; then
    err "--no-build requires a prior verified device build."
  fi

  local engine
  engine="$(detect_engine)"
  ensure_image_exists "${engine}"
  assert_runtime_machine "${engine}"
  ensure_sysroot_layout
  mkdir -p "${ROOT_DIR}/.cache/scons" "${HOST_CACHE_DIR}/scons" "${HOST_VENV_DIR}"

  local manager_args_q=""
  if [[ "${#manager_args[@]}" -gt 0 ]]; then
    for arg in "${manager_args[@]}"; do
      manager_args_q+=" $(printf '%q' "${arg}")"
    done
  fi

  local cmd
cmd="$(cat <<EOF
set -euo pipefail
export UV_CACHE_DIR=/work/.cache/uv
export UV_PYTHON_INSTALL_DIR=/work/.cache/uv-python
export LD_LIBRARY_PATH=/usr/local/lib:/usr/lib/aarch64-linux-gnu:/lib/aarch64-linux-gnu:/opt/tici-sysroot/usr/local/lib:/opt/tici-sysroot/usr/lib/aarch64-linux-gnu:/opt/tici-sysroot/lib/aarch64-linux-gnu:/system/vendor/lib64:\${LD_LIBRARY_PATH:-}
export TMPDIR=/tmp
export HOME=/tmp
export PARAMS_ROOT=/tmp/params
mkdir -p "\${UV_CACHE_DIR}"
mkdir -p "\${PARAMS_ROOT}"
UV_PROJECT_ENVIRONMENT=/work/.venv-linux-arm64 uv sync --frozen --all-extras
source /work/.venv-linux-arm64/bin/activate
cd /work
python3 openpilot/system/manager/manager.py${manager_args_q}
EOF
)"

  "${engine}" run --rm --platform linux/arm64 \
    --user "${DOCKER_RUN_USER}" \
    -v "${HOST_ROOT_DIR}:/work" \
    -v "${HOST_VENV_DIR}:/work/.venv-linux-arm64" \
    -v "${HOST_SYSROOT_DIR}:/opt/tici-sysroot:ro" \
    -v "${HOST_SYSROOT_DIR}/system/vendor/lib64:/system/vendor/lib64:ro" \
    -v "${HOST_CACHE_DIR}:/work/.cache" \
    -v "${HOST_CACHE_DIR}/scons:/data/scons_cache" \
    -w /work \
    "${IMAGE_NAME}" bash -lc "${cmd}"
}

run_shell() {
  local engine
  engine="$(detect_engine)"
  ensure_image_exists "${engine}"
  ensure_sysroot_layout
  "${engine}" run --rm -it --platform linux/arm64 \
    --user "${DOCKER_RUN_USER}" \
    -v "${HOST_ROOT_DIR}:/work" \
    -v "${HOST_VENV_DIR}:/work/.venv-linux-arm64" \
    -v "${HOST_SYSROOT_DIR}:/opt/tici-sysroot:ro" \
    -v "${HOST_SYSROOT_DIR}/system/vendor/lib64:/system/vendor/lib64:ro" \
    -v "${HOST_CACHE_DIR}:/work/.cache" \
    -v "${HOST_CACHE_DIR}/scons:/data/scons_cache" \
    -w /work \
    "${IMAGE_NAME}" bash
}

doctor() {
  local failed=0

  if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
      echo "OK: docker daemon available"
    else
      echo "FAIL: docker CLI found but daemon unavailable"
      failed=1
    fi
  elif command -v podman >/dev/null 2>&1; then
    if podman info >/dev/null 2>&1; then
      echo "OK: podman service available"
    else
      echo "FAIL: podman CLI found but service unavailable"
      failed=1
    fi
  else
    echo "FAIL: install docker or podman"
    failed=1
  fi

  if command -v rsync >/dev/null 2>&1; then
    echo "OK: rsync found"
  else
    echo "WARN: rsync missing (needed only for setup-sysroot from a physical device)"
  fi

  if [[ -d "${SYSROOT_DIR}" ]]; then
    if [[ -d "${SYSROOT_DIR}/usr/local/lib" && -d "${SYSROOT_DIR}/usr/local/include" && -d "${SYSROOT_DIR}/lib/aarch64-linux-gnu" && -d "${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu" && -d "${SYSROOT_DIR}/usr/include" && -d "${SYSROOT_DIR}/system/vendor/lib64" ]]; then
      echo "OK: sysroot present at ${SYSROOT_DIR}"
    else
      echo "WARN: sysroot dir exists but is incomplete (${SYSROOT_DIR})"
      failed=1
    fi
  else
    echo "WARN: sysroot missing (${SYSROOT_DIR})"
    failed=1
  fi

  if [[ -f "${ROOT_DIR}/.venv/bin/activate" ]]; then
    echo "OK: host .venv present"
  else
    echo "WARN: host .venv missing (run scripts/laptop_device_build.sh setup)"
    failed=1
  fi

  if [[ "${failed}" -ne 0 ]]; then
    exit 1
  fi
}

main() {
  local mode="${1:-}"
  if [[ "${mode}" == build || "${mode}" == scons ]]; then
    local arg
    for arg in "$@"; do
      case "${arg}" in
        RELEASE=*|CERT=*)
          echo "ERROR: RELEASE and CERT must be environment variables, not SCons arguments." >&2
          echo "Use: RELEASE=1 CERT=/path/to/certificate ./build --panda 4" >&2
          exit 2
          ;;
      esac
    done
  fi
  case "${mode}" in
    doctor)
      doctor
      ;;
    package-models)
      shift
      python3 -m tools.laptop_device_build.package_model_chunks --source "${ROOT_DIR}/openpilot/selfdrive/modeld/models" --destination "${1:?destination directory required}"
      ;;
    verify-artifacts)
      verify_device_artifacts
      ;;
    setup)
      shift || true
      setup_all "${1:-}" "${2:-comma}" "${3:-22}"
      ;;
    setup-sysroot)
      shift
      sync_sysroot_from_device "${1:-}" "${2:-comma}" "${3:-22}"
      ;;
    setup-sysroot-agnos)
      shift || true
      setup_sysroot_from_agnos "${1:-openpilot/system/hardware/tici/agnos.json}"
      ;;
    setup-sysroot-local)
      shift || true
      setup_sysroot_from_local_image "${1:-}"
      ;;
    build-image)
      build_container_image
      ;;
    build)
      shift || true
      local jobs_arg="${1:-$(default_jobs)}"
      if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
        shift || true
      else
        jobs_arg="$(default_jobs)"
      fi
      [[ ! "${jobs_arg}" =~ ^0+$ ]] || { echo "ERROR: jobs must be positive" >&2; exit 2; }
      run_larch64_build "${jobs_arg}" "$@"
      ;;
    scons)
      shift || true
      run_larch64_scons "$(default_jobs)" "$@"
      ;;
    manager)
      shift || true
      run_manager "$@"
      ;;
    shell)
      run_shell
      ;;
    ""|-h|--help|help)
      usage
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
