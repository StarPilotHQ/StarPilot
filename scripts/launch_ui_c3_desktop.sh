#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -f .venv/bin/activate ]]; then
  echo "Missing .venv. Run tools/install_python_dependencies.sh first."
  exit 1
fi

default_jobs() {
  if command -v nproc >/dev/null 2>&1; then
    nproc
  elif command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.ncpu
  else
    echo 8
  fi
}

jobs="$(default_jobs)"
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
  jobs="$1"
  shift || true
fi

if [[ -d /opt/homebrew/bin ]]; then
  export PATH="/opt/homebrew/bin:${PATH}"
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
  # Ensure desktop host extension builds stay on Apple toolchain.
  export CC="/usr/bin/clang"
  export CXX="/usr/bin/clang++"
fi
unset CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH CPPFLAGS CFLAGS CXXFLAGS LDFLAGS

PY_BIN="${ROOT_DIR}/.venv/bin/python3"
if [[ ! -x "${PY_BIN}" ]]; then
  echo "Missing ${PY_BIN}. Run tools/install_python_dependencies.sh first."
  exit 1
fi
export PATH="${ROOT_DIR}/.venv/bin:${PATH}"

export PYTHONPATH="${ROOT_DIR}:${ROOT_DIR}/starpilot/third_party"
for d in "${ROOT_DIR}"/*_repo; do [[ -d "$d" ]] && export PYTHONPATH="${PYTHONPATH}:$d"; done
[[ -d "${ROOT_DIR}/third_party/acados" ]] && export PYTHONPATH="${PYTHONPATH}:${ROOT_DIR}/third_party/acados"
export OPENPILOT_ZMQ_NAMESPACE="${OPENPILOT_ZMQ_NAMESPACE:-desktop-c3-$$}"
export BIG=1
export NOBOARD=1
export SIMULATION=1
export SKIP_FW_QUERY=1
export USE_WEBCAM=1
export PRIME_TYPE="${PRIME_TYPE:-0}"
export SP_C3_FAKE_DRIVE_STATS="${SP_C3_FAKE_DRIVE_STATS:-1}"
if [[ "${SP_C3_FAKE_BLUETOOTH:-1}" =~ ^(1|true|yes|on)$ ]]; then
  export SP_ALLOW_DESKTOP_FAKE_BLUETOOTH=1
else
  export SP_ALLOW_DESKTOP_FAKE_BLUETOOTH=0
fi

backup_dir="$(mktemp -d /tmp/starpilot_c3_ui_backup.XXXXXX)"
backup_manifest="${backup_dir}/.artifact_manifest"
PRE_TRACKED_DIRTY="$(mktemp /tmp/starpilot_c3_pretracked.XXXXXX)"
POST_TRACKED_DIRTY="$(mktemp /tmp/starpilot_c3_posttracked.XXXXXX)"
FAKE_WIFI_PID=""

runtime_artifacts=(
  "common/params_pyx.so"
  "msgq/ipc_pyx.so"
  "msgq/visionipc/visionipc_pyx.so"
  "common/transformations/transformations.so"
  "selfdrive/pandad/pandad_api_impl.so"
  "selfdrive/pandad/pandad_api_impl.o"
  "selfdrive/pandad/can_list_to_can_capnp.o"
  "selfdrive/pandad/libcan_list_to_can_capnp.a"
  "selfdrive/controls/lib/lateral_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so"
  "selfdrive/controls/lib/lateral_mpc_lib/c_generated_code/libacados_ocp_solver_lat.dylib"
  "selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so"
  "selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libacados_ocp_solver_long.dylib"
)

collect_tracked_dirty() {
  if ! command -v git >/dev/null 2>&1; then
    return 1
  fi
  if ! git -C "${ROOT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 1
  fi
  git -C "${ROOT_DIR}" status --porcelain --untracked-files=no | sed -E 's/^.. //'
}

restore_runtime_artifacts() {
  if [[ -d "${backup_dir}" ]]; then
    if [[ -f "${backup_manifest}" ]]; then
      while IFS= read -r rel; do
        [[ -z "${rel}" ]] && continue
        if [[ -f "${backup_dir}/${rel}" ]]; then
          cp -f "${backup_dir}/${rel}" "${ROOT_DIR}/${rel}"
        else
          rm -f "${ROOT_DIR}/${rel}"
        fi
      done < "${backup_manifest}"
    fi
    rm -rf "${backup_dir}"
  fi
}

restore_new_tracked_changes() {
  if ! collect_tracked_dirty > "${POST_TRACKED_DIRTY}"; then
    return
  fi

  while IFS= read -r path; do
    [[ -z "${path}" ]] && continue
    if ! grep -Fxq "${path}" "${PRE_TRACKED_DIRTY}"; then
      git -C "${ROOT_DIR}" checkout -- "${path}" >/dev/null 2>&1 || true
    fi
  done < "${POST_TRACKED_DIRTY}"
}

cleanup() {
  if [[ -n "${FAKE_WIFI_PID}" ]]; then
    kill "${FAKE_WIFI_PID}" >/dev/null 2>&1 || true
    wait "${FAKE_WIFI_PID}" >/dev/null 2>&1 || true
  fi
  if [[ ! "${SP_KEEP_DESKTOP_RUNTIME_ARTIFACTS:-0}" =~ ^(1|true|yes|on)$ ]]; then
    restore_runtime_artifacts
    restore_new_tracked_changes
  fi
  rm -f "${PRE_TRACKED_DIRTY}" "${POST_TRACKED_DIRTY}"
}

collect_tracked_dirty > "${PRE_TRACKED_DIRTY}" || true
trap cleanup EXIT

backup_if_elf() {
  local rel="$1"
  local src="${ROOT_DIR}/${rel}"
  echo "${rel}" >> "${backup_manifest}"
  if [[ -f "${src}" ]]; then
    mkdir -p "${backup_dir}/$(dirname "${rel}")"
    cp -f "${src}" "${backup_dir}/${rel}"
  fi
}

for rel in "${runtime_artifacts[@]}"; do
  backup_if_elf "${rel}"
done

archive_is_aarch64_elf() {
  local path="$1"
  [[ -f "${path}" ]] || return 1
  if command -v readelf >/dev/null 2>&1; then
    readelf -h "${path}" 2>/dev/null | grep -iq "aarch64"
  else
    objdump -a "${path}" 2>/dev/null | head -n 12 | grep -iqE "elf64-littleaarch64|aarch64"
  fi
}

remove_if_elf() {
  local rel="$1"
  local path="${ROOT_DIR}/${rel}"
  if [[ -f "${path}" ]] && file "${path}" | grep -q "ELF"; then
    rm -f "${path}"
  fi
}

prepare_common_host_artifacts() {
  if archive_is_aarch64_elf "${ROOT_DIR}/common/libcommon.a"; then
    rm -f \
      "${ROOT_DIR}/common/libcommon.a" \
      "${ROOT_DIR}/common/"*.o \
      "${ROOT_DIR}/common/params_pyx.o" \
      "${ROOT_DIR}/common/transformations/transformations.o"
  fi
}

prepare_msgq_host_artifacts() {
  # Clear mixed-arch Python extension objects from both msgq trees. These
  # commonly conflict after switching between ./build (larch64) and ./c3 (macOS).
  remove_if_elf "msgq/ipc_pyx.o"
  remove_if_elf "msgq/visionipc/visionipc_pyx.o"
  remove_if_elf "msgq_repo/msgq/ipc_pyx.o"
  remove_if_elf "msgq_repo/msgq/visionipc/visionipc_pyx.o"

  if archive_is_aarch64_elf "${ROOT_DIR}/msgq_repo/libmsgq.a" || archive_is_aarch64_elf "${ROOT_DIR}/msgq_repo/libvisionipc.a"; then
    rm -f \
      "${ROOT_DIR}/msgq_repo/libmsgq.a" \
      "${ROOT_DIR}/msgq_repo/libvisionipc.a" \
      "${ROOT_DIR}/msgq_repo/msgq/"*.os \
      "${ROOT_DIR}/msgq_repo/msgq/visionipc/"*.os \
      "${ROOT_DIR}/msgq_repo/msgq/ipc_pyx.o" \
      "${ROOT_DIR}/msgq_repo/msgq/visionipc/visionipc_pyx.o"
  fi
}

prepare_pandad_host_artifacts() {
  remove_if_elf "selfdrive/pandad/pandad_api_impl.so"
  remove_if_elf "selfdrive/pandad/pandad_api_impl.o"
  remove_if_elf "selfdrive/pandad/can_list_to_can_capnp.o"

  # Host builds always regenerate this small archive locally. Device-built
  # variants are not reusable across platforms and can poison the link step.
  rm -f "${ROOT_DIR}/selfdrive/pandad/libcan_list_to_can_capnp.a"
}

prepare_acados_host_artifacts() {
  # Generated ACADOS solver sources can be identical between device and desktop
  # builds. SCons may therefore keep an AArch64 solver library from a previous
  # device build instead of relinking it for the host. Remove those stale
  # solver trees so they are rebuilt for the current architecture.
  local rel=""
  for rel in "selfdrive/controls/lib/lateral_mpc_lib/c_generated_code/libacados_ocp_solver_lat.so" \
             "selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libacados_ocp_solver_long.so"; do
    if archive_is_aarch64_elf "${ROOT_DIR}/${rel}"; then
      rm -rf "$(dirname "${ROOT_DIR}/${rel}")"
    fi
  done

  # The bundled x86_64 BLASFEO library is marked with an executable GNU_STACK.
  # Some Linux systems refuse to load it, causing both ACADOS MPC Python
  # extensions to fail at import time. Clear the executable-stack requirement
  # before loading the host ACADOS runtime.
  if [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]]; then
    local blasfeo="${ROOT_DIR}/third_party/acados/x86_64/lib/libblasfeo.so"

    if [[ -f "${blasfeo}" ]] && readelf -W -l "${blasfeo}" 2>/dev/null | grep -qE 'GNU_STACK.*RWE'; then
      if ! command -v patchelf >/dev/null 2>&1; then
        echo "patchelf is required to clear BLASFEO's executable stack flag."
        return 1
      fi

      echo "Clearing executable stack flag from x86_64 BLASFEO..."
      patchelf --clear-execstack "${blasfeo}"
    fi
  fi
}

python_ui_runtime_ok() {
  "${PY_BIN}" - <<'PY'
import pyray  # noqa: F401
import openpilot.common.params_pyx  # noqa: F401
import openpilot.common.transformations.transformations  # noqa: F401
import openpilot.selfdrive.pandad.pandad_api_impl  # noqa: F401
import msgq.ipc_pyx  # noqa: F401
import msgq.visionipc.visionipc_pyx  # noqa: F401
import openpilot.selfdrive.controls.lib.lateral_mpc_lib.c_generated_code.acados_ocp_solver_pyx  # noqa: F401
import openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.c_generated_code.acados_ocp_solver_pyx  # noqa: F401

assert hasattr(msgq.visionipc.visionipc_pyx.VisionBuf, "frame_id")
PY
}

sync_deps() {
  if command -v uv >/dev/null 2>&1; then
    UV_PROJECT_ENVIRONMENT=.venv uv sync --frozen --all-extras
  else
    echo "Missing uv. Install dependencies with tools/install_python_dependencies.sh."
    exit 1
  fi
}

sync_raylib() {
  if "${PY_BIN}" - <<'PY' >/dev/null 2>&1
import pyray  # noqa: F401
PY
  then
    return
  fi

  if command -v uv >/dev/null 2>&1; then
    UV_PROJECT_ENVIRONMENT=.venv uv pip install "raylib<5.5.0.3"
  else
    echo "Missing uv. Install dependencies with tools/install_python_dependencies.sh."
    exit 1
  fi
}

run_scons() {
  local jobs_arg="$1"
  shift || true
  local scons_bin="${ROOT_DIR}/.venv/bin/scons"
  if [[ -x "${scons_bin}" ]]; then
    SP_DISABLE_AUTO_DEVICE_SCONS=1 "${scons_bin}" -j"${jobs_arg}" "$@"
  elif "${PY_BIN}" -m SCons --version >/dev/null 2>&1; then
    SP_DISABLE_AUTO_DEVICE_SCONS=1 "${PY_BIN}" -m SCons -j"${jobs_arg}" "$@"
  else
    echo "SCons not found in .venv after sync."
    exit 1
  fi
}

kill_stale_c3_ui() {
  pkill -f "selfdrive/ui/ui.py" >/dev/null 2>&1 || true
}

start_fake_wifi() {
  if [[ ! "${SP_C3_FAKE_WIFI:-1}" =~ ^(1|true|yes|on)$ ]]; then
    export SP_ALLOW_DESKTOP_FAKE_WIFI=0
    return
  fi

  export SP_ALLOW_DESKTOP_FAKE_WIFI=1
  "${PY_BIN}" selfdrive/debug/fake_wifi.py --network wifi --strength great --interval 0.2 &
  FAKE_WIFI_PID=$!
}

seed_starpilot_theme() {
  "${PY_BIN}" - <<'PY'
from openpilot.starpilot.common.starpilot_functions import seed_desktop_theme_assets

seed_desktop_theme_assets()
PY
}

if ! python_ui_runtime_ok >/dev/null 2>&1; then
  echo "Preparing host Python UI runtime extensions..."
  sync_deps
  sync_raylib
  prepare_common_host_artifacts
  prepare_msgq_host_artifacts
  prepare_pandad_host_artifacts
  prepare_acados_host_artifacts
  remove_if_elf "common/params_pyx.so"
  remove_if_elf "common/transformations/transformations.so"
  remove_if_elf "msgq/ipc_pyx.so"
  remove_if_elf "msgq/visionipc/visionipc_pyx.so"
  remove_if_elf "selfdrive/controls/lib/lateral_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so"
  remove_if_elf "selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so"

  run_scons "${jobs}" common/params_pyx.so common/transformations/transformations.so
  # Building the Cython module pulls in the platform-specific acados solver
  # library via SCons dependencies, so the host path does not need to name the
  # solver shared library extension explicitly.
  run_scons "${jobs}" \
    selfdrive/controls/lib/lateral_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so \
    selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so

  # Build msgq extensions through the root SConstruct so they link against
  # StarPilot's common library instead of msgq_repo's standalone environment.
  run_scons "${jobs}" \
    msgq_repo/msgq/ipc_pyx.so \
    msgq_repo/msgq/visionipc/visionipc_pyx.so
fi

if ! python_ui_runtime_ok >/dev/null 2>&1; then
  "${PY_BIN}" - <<'PY'
import site
import sys
print("python:", sys.executable)
print("version:", sys.version)
print("site-packages:", site.getsitepackages())
PY
  echo "Unable to load Python UI runtime extensions after rebuild."
  exit 1
fi

if [[ "${SP_C3_COMPILE_ONLY:-0}" == "1" ]]; then
  echo "C3 runtime artifacts prepared."
  exit 0
fi

"${PY_BIN}" - <<'PY'
from openpilot.common.params import Params
from openpilot.system.version import terms_version, training_version

params = Params()
params.put("HasAcceptedTerms", terms_version)
params.put("CompletedTrainingVersion", training_version)
params.put_bool("OpenpilotEnabledToggle", True)
params.put_bool("IsDriverViewEnabled", False)
PY

seed_starpilot_theme
kill_stale_c3_ui
start_fake_wifi
"${PY_BIN}" selfdrive/ui/ui.py "$@"
