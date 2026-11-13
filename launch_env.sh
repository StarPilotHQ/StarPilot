#!/usr/bin/env bash

# Device Params must not depend on the platform used to compile native libraries.
if [ -f /AGNOS ] && [ -z "${PARAMS_ROOT:-}" ]; then
  export PARAMS_ROOT="/data/params"
fi

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

# Use the dependency environment supplied by the retained custom image.
if [ -x /usr/local/venv/bin/python3 ]; then
  export PATH="/usr/local/venv/bin:$PATH"
fi

# models get lower priority than ui
# - ui is ~5ms
# - modeld is 20ms
# - DM is 10ms
# in order to run ui at 60fps (16.67ms), we need to allow
# it to preempt the model workloads. we have enough
# headroom for this until ui is moved to the CPU.
export QCOM_PRIORITY=12

if [ -z "$AGNOS_VERSION" ]; then
  export AGNOS_VERSION="20.5.1"
fi

export AGNOS_UPDATE_POLICY="auto"
# C3 selects its complete matching firmware chain in the AGNOS manifest loader.
# Unknown hardware retains its installed OS.
if [ -f /AGNOS ] || [ -e /sys/firmware/devicetree/base/model ]; then
  case "$(tr -d '\0' 2>/dev/null < /sys/firmware/devicetree/base/model)" in
    "comma tici"|"comma tizi"|"comma mici") ;;
    *) export AGNOS_UPDATE_POLICY="retain" ;;
  esac
fi

export STAGING_ROOT="/data/safe_staging"

