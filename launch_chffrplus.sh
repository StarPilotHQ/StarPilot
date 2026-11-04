#!/usr/bin/env bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

source "$DIR/launch_env.sh"

function agnos_init {
  if [ "${AGNOS_UPDATE_POLICY:-auto}" = "retain" ] && [ "$(< /VERSION)" != "$AGNOS_VERSION" ]; then
    echo "This StarPilot build retains AGNOS $AGNOS_VERSION; installed OS does not match. No OS update was attempted."
    return 1
  fi

  # TODO: move this to agnos
  sudo rm -f /data/etc/NetworkManager/system-connections/*.nmmeta
  rm -f /data/scons_cache/config.lock

  # set success flag for current boot slot
  sudo abctl --set_success

  # TODO: do this without udev in AGNOS
  # udev does this, but sometimes we startup faster
  sudo chgrp gpu /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0
  sudo chmod 660 /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0

  # Check if AGNOS update is required
  if [ "$(< /VERSION)" != "$AGNOS_VERSION" ]; then
    AGNOS_PY="$DIR/openpilot/common/hardware/comma/agnos.py"
    MANIFEST="$DIR/openpilot/common/hardware/comma/agnos.json"
    if "$AGNOS_PY" --verify "$MANIFEST"; then
      sudo reboot
    fi
    while true; do
      "$DIR/openpilot/common/hardware/comma/updater" "$AGNOS_PY" "$MANIFEST"
    done
  fi

  # Add missing USB rules for C3's internal Panda; C3X/C4 use SPI.
  if [ "$(tr -d '\0' 2>/dev/null < /sys/firmware/devicetree/base/model)" = "comma tici" ]; then
    if ! sudo timeout -k 1s 5s sh -ec '
      mkdir -p /run/udev/rules.d
      cat > /run/udev/rules.d/99-starpilot-panda.rules <<EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="3801", ATTRS{idProduct}=="ddcc", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="3801", ATTRS{idProduct}=="ddee", MODE="0666"
EOF
      # Batch AGNOS GPIO permissions so coldplug does not block Panda USB events.
      if [ -f /etc/udev/rules.d/99-gpio.rules ] && ! mountpoint -q /etc/udev/rules.d/99-gpio.rules; then
        sed "s/{} \\\\;/{} +/g" /etc/udev/rules.d/99-gpio.rules > /run/udev/rules.d/99-gpio.rules
        mount --bind /run/udev/rules.d/99-gpio.rules /etc/udev/rules.d/99-gpio.rules
      fi
      udevadm control --reload-rules
      for product in ddcc ddee; do
        udevadm trigger --subsystem-match=usb --attr-match=idVendor=3801 --attr-match=idProduct="$product"
      done
    '; then
      echo "Panda USB permission setup failed; continuing startup."
    fi
  fi
}

function update_is_ready {
  [ -f "$1" ] && [ ! -L "$1" ]
}

# A validated legacy return records the OS it was staged against. Check it on
# every boot before swapping trees, including manual reboots.
# Keep this in sync with TRUSTED_LEGACY_RELEASES in updated.py.
TRUSTED_LEGACY_STARPILOT_COMMIT="4a553da9ac22a14e34513c011aed04c1e93b4619"

function staged_os_is_compatible {
  local staged="$1" marker="$1/.git/starpilot-retain-agnos" commit
  commit="$(git -C "$staged" rev-parse --verify 'HEAD^{commit}')" || return 1
  if [ "$commit" = "$TRUSTED_LEGACY_STARPILOT_COMMIT" ]; then
    [ -f "$marker" ] && [ -s "$marker" ] && [ ! -L "$marker" ] && [ -r /VERSION ] && [ "$(cat "$marker")" = "$(< /VERSION)" ]
  else
    [ ! -e "$marker" ] && [ ! -L "$marker" ]
  fi
}

function reset_update_os_environment {
  unset AGNOS_VERSION AGNOS_ACCEPTED_VERSIONS
}

function maybe_activate_legacy_agnos {
  local repo="$1" verifier="$2" manifest="$3" commit
  commit="$(git -C "$repo" rev-parse --verify 'HEAD^{commit}')" || return 1
  if [ "$commit" != "$TRUSTED_LEGACY_STARPILOT_COMMIT" ] && [ ! -L "$manifest" ]; then
    "$verifier" --verify "$manifest" && sudo reboot
  fi
}

function launch {
  # Remove orphaned git lock if it exists on boot
  [ -f "$DIR/.git/index.lock" ] && rm -f "$DIR/.git/index.lock"

  # Check to see if there's a valid overlay-based update available. Conditions
  # are as follows:
  #
  # 1. The DIR init file has to exist, with a newer modtime than anything in
  #    the DIR Git repo. This checks for local development work or the user
  #    switching branches/forks, which should not be overwritten.
  # 2. The FINALIZED consistent file has to exist, indicating there's an update
  #    that completed successfully and synced to disk.

  if [ -f "${DIR}/.overlay_init" ]; then
    find "${DIR}/.git" -newer "${DIR}/.overlay_init" | grep -q '.' 2> /dev/null
    if [ $? -eq 0 ]; then
      echo "${DIR} has been modified, skipping overlay update installation"
    else
      if update_is_ready "${STAGING_ROOT}/finalized/.overlay_consistent"; then
        if ! staged_os_is_compatible "${STAGING_ROOT}/finalized"; then
          echo "Staged update requires a different or unverified AGNOS version; skipping installation."
        elif [ ! -d /data/safe_staging/old_openpilot ]; then
          echo "Valid overlay update found, installing"
          LAUNCHER_LOCATION="${BASH_SOURCE[0]}"

          mv "$DIR" /data/safe_staging/old_openpilot
          mv "${STAGING_ROOT}/finalized" "$DIR"
          cd "$DIR"
          # releases before the openpilot/ move can't swap AGNOS on newer AGNOS, use ours
          maybe_activate_legacy_agnos "$DIR" /data/safe_staging/old_openpilot/openpilot/common/hardware/comma/agnos.py system/hardware/tici/agnos.json

          echo "Restarting launch script ${LAUNCHER_LOCATION}"
          reset_update_os_environment
          exec "${LAUNCHER_LOCATION}"
        else
          echo "openpilot backup found, not updating"
          # TODO: restore backup? This means the updater didn't start after swapping
        fi
      fi
    fi
  fi

  # handle pythonpath
  ln -sfn "$(pwd)" /data/pythonpath
  export PYTHONPATH="$PWD"

  # Dependency package symlinks for PYTHONPATH imports on device.
  # on PC these come from editable installs via pyproject.toml / uv.
  ln -sfn msgq_repo/msgq msgq
  ln -sfn opendbc_repo/opendbc opendbc
  ln -sfn rednose_repo/rednose rednose
  ln -sfn teleoprtc_repo/teleoprtc teleoprtc
  ln -sfn tinygrad_repo/tinygrad tinygrad
  ln -sfn jetlink_repo/jetlink jetlink

  # hardware specific init
  if [ -f /AGNOS ]; then
    agnos_init || return $?
    if ! "$DIR/scripts/install_boot_logo.sh"; then
      echo "StarPilot boot image update failed; continuing startup."
    fi
  fi

  # write tmux scrollback to a file
  tmux capture-pane -pq -S-1000 > /tmp/launch_log

  # start manager
  cd openpilot/system/manager
  if [ ! -f "$DIR/prebuilt" ]; then
    ./build.py
  fi
  ./manager.py

  # if broken, keep on screen error
  while true; do sleep 1; done
}

launch
