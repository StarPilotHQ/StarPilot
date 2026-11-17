#!/usr/bin/env bash

set -e
set -x


if [ -z "$SOURCE_DIR" ]; then
  echo "SOURCE_DIR must be set"
  exit 1
fi

if [ -z "$GIT_COMMIT" ]; then
  echo "GIT_COMMIT must be set"
  exit 1
fi

if [ -z "$TEST_DIR" ]; then
  echo "TEST_DIR must be set"
  exit 1
fi

if [ ! -d "$SOURCE_DIR" ] && [ -z "${SOURCE_REPO:-}" ]; then
  echo "SOURCE_REPO must name the repository to clone when SOURCE_DIR does not exist"
  exit 1
fi

# prevent storage from filling up
rm -rf /data/media/0/realdata/*

rm -rf /data/safe_staging/ || true
if [ -d /data/safe_staging/ ]; then
  sudo umount /data/safe_staging/merged/ || true
  rm -rf /data/safe_staging/ || true
fi

CONTINUE_PATH="/data/continue.sh"
tee "$CONTINUE_PATH" << EOF
#!/usr/bin/env bash

sudo abctl --set_success

# patch sshd config
sudo mount -o rw,remount /
sudo sed -i "s,/data/params/d/GithubSshKeys,/usr/comma/setup_keys," /etc/ssh/sshd_config
sudo systemctl daemon-reload
sudo systemctl restart ssh
sudo systemctl restart NetworkManager
sudo systemctl disable ssh-param-watcher.path
sudo systemctl disable ssh-param-watcher.service
sudo mount -o ro,remount /
sudo systemctl stop power_monitor

while true; do
  if ! sudo systemctl is-active -q ssh; then
    sudo systemctl start ssh
  fi
  sleep 5s
done

sleep infinity
EOF
chmod +x "$CONTINUE_PATH"

if pgrep -f launch_chffrplus.sh > /dev/null; then
  sudo systemctl stop comma
fi

safe_checkout() {
  # completely clean TEST_DIR

  cd "$SOURCE_DIR"
  local target_commit

  # cleanup orphaned locks
  find "$(git rev-parse --absolute-git-dir)" -type f -name "*.lock" -exec rm {} +

  git -c submodule.recurse=false fetch --no-tags --no-recurse-submodules -j4 --verbose --depth 1 origin "$GIT_COMMIT"
  target_commit="$(git rev-parse 'FETCH_HEAD^{commit}')"
  if [ -f tools/vendor/check.py ]; then
    python3 tools/vendor/check.py --revision "$target_commit"
  fi
  find . -maxdepth 1 -not -path './.git' -not -name '.' -not -name '..' -exec rm -rf '{}' \;
  git -c submodule.recurse=false reset --hard --no-recurse-submodules "$target_commit"
  git -c submodule.recurse=false checkout --force --no-recurse-submodules "$target_commit"
  git clean -xdff
  python3 tools/vendor/check.py


  echo "git checkout done, t=$SECONDS"
  du -hs "$SOURCE_DIR" "$SOURCE_DIR/.git"

  rsync -a --delete "$SOURCE_DIR" "$TEST_DIR"
}

unsafe_checkout() {( set -e
  # checkout directly in test dir, leave old build products

  cd "$TEST_DIR"
  local target_commit

  # cleanup orphaned locks
  find "$(git rev-parse --absolute-git-dir)" -type f -name "*.lock" -exec rm {} +

  git -c submodule.recurse=false fetch --no-tags --no-recurse-submodules -j8 --verbose --depth 1 origin "$GIT_COMMIT"
  target_commit="$(git rev-parse 'FETCH_HEAD^{commit}')"
  if [ -f tools/vendor/check.py ]; then
    python3 tools/vendor/check.py --revision "$target_commit"
  fi
  git -c submodule.recurse=false checkout --force --no-recurse-submodules "$target_commit"
  git -c submodule.recurse=false reset --hard --no-recurse-submodules "$target_commit"
  git clean -dff
  python3 tools/vendor/check.py

)}

export GIT_PACK_THREADS=8

# set up environment
if [ ! -d "$SOURCE_DIR" ]; then
  git -c submodule.recurse=false clone --no-recurse-submodules "$SOURCE_REPO" "$SOURCE_DIR"
fi

if [ ! -z "$UNSAFE" ]; then
  echo "trying unsafe checkout"
  set +e
  unsafe_checkout
  if [[ "$?" -ne 0 ]]; then
    safe_checkout
  fi
  set -e
else
  echo "doing safe checkout"
  safe_checkout
fi

# Vendored package symlinks for PYTHONPATH imports on device (same as launch_chffrplus.sh)
cd "$TEST_DIR"
ln -sfn msgq_repo/msgq msgq
ln -sfn opendbc_repo/opendbc opendbc
ln -sfn rednose_repo/rednose rednose
ln -sfn teleoprtc_repo/teleoprtc teleoprtc
ln -sfn tinygrad_repo/tinygrad tinygrad

echo "$TEST_DIR synced with $GIT_COMMIT, t=$SECONDS"
