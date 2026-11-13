#!/usr/bin/env python3
from openpilot.starpilot.galaxy.companion import phone_uplink_active

import os
import re
import datetime
import subprocess
import shutil
import signal
import fcntl
import time
import threading
import atexit
from pathlib import Path

from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.common.time_helpers import system_time_valid
from openpilot.common.markdown import parse_markdown
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.common.hardware import AGNOS, HARDWARE
from openpilot.common.version import get_build_metadata
from openpilot.common.vendor_manifest import validate_revision
from openpilot.starpilot.software.update_control import UpdaterControlError, UpdaterControlServer
from openpilot.starpilot.software.preferences import AUTOMATIC_DOWNLOADS, download_permitted
from openpilot.starpilot.software.fast_update import (
  FastUpdateError, FastUpdateIncompatible, fetch_files, legacy_os_compatible, required_os_version, run as run_git,
  validate_legacy_return,
)
from openpilot.starpilot.software.history import MAX_COMMITS, METADATA_ROOT, commit_history
from openpilot.starpilot.software.auto_install import InstallSettings, cron_matches, scheduled_restart

LOCK_FILE = os.getenv("UPDATER_LOCK_FILE", "/tmp/safe_staging_overlay.lock")
STAGING_ROOT = os.getenv("UPDATER_STAGING_ROOT", "/data/safe_staging")

OVERLAY_UPPER = os.path.join(STAGING_ROOT, "upper")
OVERLAY_METADATA = os.path.join(STAGING_ROOT, "metadata")
OVERLAY_MERGED = os.path.join(STAGING_ROOT, "merged")
FINALIZED = os.path.join(STAGING_ROOT, "finalized")

OVERLAY_INIT = Path(os.path.join(BASEDIR, ".overlay_init"))

# do not allow to engage after this many hours onroad and this many routes
HOURS_NO_CONNECTIVITY_MAX = 27
ROUTES_NO_CONNECTIVITY_MAX = 84
# send an offroad prompt after this many hours onroad and this many routes
HOURS_NO_CONNECTIVITY_PROMPT = 23
ROUTES_NO_CONNECTIVITY_PROMPT = 80


class UserRequest:
  NONE = 0
  CHECK = 1
  FETCH = 2
  FAST = 3
  ROLLBACK = 4
  VERSIONS = 5
  DOWNLOAD_VERSION = 8
  VERSION = 6
  UPDATE = 7

class WaitTimeHelper:
  def __init__(self, metadata=None, idle_tick=None):
    self.ready_event = threading.Event()
    self.user_request = UserRequest.NONE
    self.request_lock = threading.RLock()
    self.request_generation = 0
    self.fast_target: tuple[str, str] | tuple[str, str, str] | None = None
    self.metadata = metadata
    self.idle_tick = idle_tick
    self.automatic_pending = False
    signal.signal(signal.SIGHUP, self.update_now)
    signal.signal(signal.SIGUSR1, self.check_now)
    self.control = UpdaterControlServer(self._control_request)
    try:
      self.control.start()
    except UpdaterControlError:
      cloudlog.warning('updater local control is unavailable; signal handlers remain active')
    else:
      atexit.register(self.control.close)

  def _request(self, request: int, *, branch=None, commit=None, selected_commit=None) -> None:
    with self.request_lock:
      self.user_request = request
      if request in (UserRequest.UPDATE, UserRequest.FAST, UserRequest.ROLLBACK, UserRequest.VERSIONS, UserRequest.VERSION, UserRequest.DOWNLOAD_VERSION):
        if not isinstance(branch, str) or not isinstance(commit, str):
          raise ValueError("Update source identity is required")
        if request in (UserRequest.VERSION, UserRequest.DOWNLOAD_VERSION):
          if not isinstance(selected_commit, str):
            raise ValueError("Selected version is required")
          self.fast_target = (branch, commit, selected_commit)
        else:
          self.fast_target = (branch, commit)
      else:
        self.fast_target = None
      self.request_generation += 1
      self.ready_event.set()

  def _control_request(self, action: str, *, branch=None, commit=None, selected_commit=None) -> None:
    if self.metadata is not None and action in ('check', 'versions'):
      self.metadata(branch=branch, commit=commit, download_after=action == 'check')
      return
    request = {'update': UserRequest.UPDATE, 'check': UserRequest.CHECK, 'download': UserRequest.FETCH,
               'fast': UserRequest.FAST, 'rollback': UserRequest.ROLLBACK,
               'versions': UserRequest.VERSIONS, 'version': UserRequest.VERSION, 'download-version': UserRequest.DOWNLOAD_VERSION}.get(action)
    if request is not None:
      self._request(request, branch=branch, commit=commit, selected_commit=selected_commit)

  def update_now(self, signum: int, frame) -> None:
    cloudlog.info("caught SIGHUP, attempting to downloading update")
    self._request(UserRequest.FETCH)

  def check_now(self, signum: int, frame) -> None:
    cloudlog.info("caught SIGUSR1, checking for updates")
    self._control_request('check')

  def current_request(self) -> tuple[int, int, tuple[str, str] | tuple[str, str, str] | None]:
    with self.request_lock:
      self.ready_event.clear()
      self.automatic_pending = False
      return self.user_request, self.request_generation, self.fast_target

  def wake_automatic(self):
    with self.request_lock:
      self.automatic_pending = True
      self.ready_event.set()

  def finish_request(self, generation: int) -> bool:
    with self.request_lock:
      if self.request_generation != generation:
        return False
      self.user_request = UserRequest.NONE
      self.fast_target = None
      if self.automatic_pending:
        return False
      self.ready_event.clear()
      return True

  def sleep(self, t: float) -> None:
    deadline = time.monotonic() + t
    while not self.ready_event.is_set():
      tick = getattr(self, 'idle_tick', None)
      if tick is not None:
        tick()
      remaining = deadline - time.monotonic()
      if remaining <= 0 or self.ready_event.wait(timeout=min(30, remaining)):
        return

def write_time_to_param(params, param) -> None:
  t = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
  params.put(param, t, block=True)

def run(cmd: list[str], cwd: str | None = None, *, download: bool = False, progress=None, cancelled=None, stdin=None, timeout=None) -> str:
  if cmd and cmd[0] == 'git':
    return run_git(cmd, cwd, timeout=timeout or (300 if download or 'fetch' in cmd or 'gc' in cmd else 120),
                   download=download, progress=progress, cancelled=cancelled, stdin=stdin)
  return subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT, encoding='utf8')


def set_consistent_flag(consistent: bool) -> None:
  os.sync()
  consistent_file = Path(os.path.join(FINALIZED, ".overlay_consistent"))
  if consistent:
    fd = os.open(consistent_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    os.close(fd)
  elif not consistent:
    consistent_file.unlink(missing_ok=True)
  os.sync()

def parse_release_notes(basedir: str) -> bytes:
  try:
    with open(os.path.join(basedir, "RELEASES.md"), "rb") as f:
      r = f.read().split(b'\n\n', 1)[0]  # Slice latest release notes
    try:
      return bytes(parse_markdown(r.decode("utf-8")), encoding="utf-8")
    except Exception:
      return r + b"\n"
  except FileNotFoundError:
    pass
  except Exception:
    cloudlog.exception("failed to parse release notes")
  return b""

def setup_git_options(cwd: str) -> None:
  # We sync FS object atimes (which NEOS doesn't use) and mtimes, but ctimes
  # are outside user control. Make sure Git is set up to ignore system ctimes,
  # because they change when we make hard links during finalize. Otherwise,
  # there is a lot of unnecessary churn. This appears to be a common need on
  # OSX as well: https://www.git-tower.com/blog/make-git-rebase-safe-on-osx/

  # We are using copytree to copy the directory, which also changes
  # inode numbers. Ignore those changes too.

  # Set protocol to the new version (default after git 2.26) to reduce data
  # usage on git fetch --dry-run from about 400KB to 18KB.
  git_cfg = [
    ("core.trustctime", "false"),
    ("core.checkStat", "minimal"),
    ("protocol.version", "2"),
    ("gc.auto", "0"),
    ("gc.autoDetach", "false"),
    ("submodule.recurse", "false"),
    ("fetch.recurseSubmodules", "false"),
  ]
  for option, value in git_cfg:
    run(["git", "config", option, value], cwd)


def dismount_overlay() -> None:
  if os.path.ismount(OVERLAY_MERGED):
    cloudlog.info("unmounting existing overlay")
    run(["sudo", "umount", "-l", OVERLAY_MERGED])


def init_overlay() -> None:

  # Re-create the overlay if BASEDIR/.git has changed since we created the overlay
  if OVERLAY_INIT.is_file() and os.path.ismount(OVERLAY_MERGED):
    git_dir_path = os.path.join(BASEDIR, ".git")
    new_files = run(["find", git_dir_path, "-newer", str(OVERLAY_INIT)])
    if not len(new_files.splitlines()):
      # A valid overlay already exists
      return
    else:
      cloudlog.info(".git directory changed, recreating overlay")

  cloudlog.info("preparing new safe staging area")

  params = Params()
  params.put_bool("UpdateAvailable", False, block=True)
  set_consistent_flag(False)
  dismount_overlay()
  run(["sudo", "rm", "-rf", STAGING_ROOT])
  if os.path.isdir(STAGING_ROOT):
    shutil.rmtree(STAGING_ROOT)

  for dirname in [STAGING_ROOT, OVERLAY_UPPER, OVERLAY_METADATA, OVERLAY_MERGED]:
    os.mkdir(dirname, 0o755)

  if os.lstat(BASEDIR).st_dev != os.lstat(OVERLAY_MERGED).st_dev:
    raise RuntimeError("base and overlay merge directories are on different filesystems; not valid for overlay FS!")

  # Leave a timestamped canary in BASEDIR to check at startup. The device clock
  # should be correct by the time we get here. If the init file disappears, or
  # critical mtimes in BASEDIR are newer than .overlay_init, continue.sh can
  # assume that BASEDIR has used for local development or otherwise modified,
  # and skips the update activation attempt.
  consistent_file = Path(os.path.join(BASEDIR, ".overlay_consistent"))
  if consistent_file.is_file():
    consistent_file.unlink()
  OVERLAY_INIT.touch()

  os.sync()
  overlay_opts = f"lowerdir={BASEDIR},upperdir={OVERLAY_UPPER},workdir={OVERLAY_METADATA}"

  mount_cmd = ["mount", "-t", "overlay", "-o", overlay_opts, "none", OVERLAY_MERGED]
  run(["sudo"] + mount_cmd)
  run(["sudo", "chmod", "755", os.path.join(OVERLAY_METADATA, "work")])

  git_diff = run(["git", "diff"], OVERLAY_MERGED)
  params.put("GitDiff", git_diff, block=True)
  cloudlog.info(f"git diff output:\n{git_diff}")


# Audited legacy release, pinned by Git commit identity. Other releases must
# satisfy the modern source manifest contract.
TRUSTED_LEGACY_RELEASES = {'StarPilot': frozenset({'c2e5e469ee8fefc3cffb514dc2e1a1fb82a421d6'})}
LEGACY_OS_MARKER = '.git/starpilot-retain-agnos'


def validate_update_revision(repo: str, revision: str, branch: str) -> bool:
  """Validate a modern source tree or an explicitly supported legacy return."""
  def git(*args):
    return run(['git', *args], repo, download='show' in args)

  if git('ls-tree', revision, '--', '.overlay_consistent').strip():
    raise ValueError('Source tree contains reserved update readiness marker')
  manifest_entry = git('ls-tree', revision, '--', 'upstream-sync.json').strip()
  commit = git('rev-parse', '--verify', f'{revision}^{{commit}}').strip()
  if not manifest_entry and commit in TRUSTED_LEGACY_RELEASES.get(branch, ()):
    validate_legacy_return(git, revision, branch)
    launch = git('show', f'{revision}:launch_env.sh')
    if not legacy_os_compatible(launch, HARDWARE.get_os_version()):
      raise FastUpdateIncompatible('Update requires a different or unverified operating system')
    return True
  if manifest_entry.split()[:1] == ['100644']:
    git('show', f'{revision}:upstream-sync.json')
  validate_revision(repo, revision)
  return False


def finalize_update(progress=None) -> None:
  """Take the current OverlayFS merged view and finalize a copy outside of
  OverlayFS, ready to be swapped-in at BASEDIR. Copy using shutil.copytree"""

  # Remove the update ready flag and any old updates
  cloudlog.info("creating finalized version of the overlay")
  set_consistent_flag(False)
  branch = run(["git", "symbolic-ref", "--short", "HEAD"], OVERLAY_MERGED).strip()
  legacy_return = validate_update_revision(OVERLAY_MERGED, "HEAD", branch)

  # Copy the merged overlay view and set the update ready flag
  if os.path.exists(FINALIZED):
    shutil.rmtree(FINALIZED)
  total = sum(not os.path.islink(os.path.join(root, name)) for root, _, files in os.walk(OVERLAY_MERGED) for name in files)
  copied = 0
  reported = 0.0
  roots = (Path(OVERLAY_UPPER), Path(BASEDIR)) if os.path.ismount(OVERLAY_MERGED) else (Path(OVERLAY_MERGED),)
  def copy_file(source, destination):
    nonlocal copied, reported
    relative = Path(source).relative_to(OVERLAY_MERGED)
    immutable = re.fullmatch(r'\.git/objects/(?:[0-9a-f]{2}/[0-9a-f]{38}|pack/pack-[0-9a-f]{40}\.(?:pack|idx))', relative.as_posix())
    linked = False
    if immutable:
      # Git objects are immutable; worktree files, refs and indexes remain independent copies.
      for root in roots:
        candidate = root / relative
        try:
          if not candidate.is_symlink():
            os.link(candidate, destination)
            linked = True
            break
        except OSError:
          pass
    if not linked:
      shutil.copy2(source, destination)
    copied += 1
    now = time.monotonic()
    if progress is not None and (now - reported >= 0.25 or copied == total):
      progress(f'Copying update files: {copied}/{total} ({copied * 100 // max(1, total)}%)', copied * 100 / max(1, total))
      reported = now
    return destination
  started = time.monotonic()
  def ignore_readiness_marker(directory, names):
    return {'.overlay_consistent'} if Path(directory) == Path(OVERLAY_MERGED) else set()
  shutil.copytree(OVERLAY_MERGED, FINALIZED, symlinks=True, copy_function=copy_file, ignore=ignore_readiness_marker)
  cloudlog.event('update copy complete', duration=time.monotonic() - started, files=copied)

  if progress is not None:
    progress('Verifying the prepared update...', None)
  run(["git", "reset", "--hard", "--no-recurse-submodules"], FINALIZED)
  validate_update_revision(FINALIZED, "HEAD", branch)
  marker = Path(FINALIZED, LEGACY_OS_MARKER)
  marker.unlink(missing_ok=True)
  if legacy_return:
    # Bind installation to the exact OS validated during staging. The launcher
    # rechecks this before swapping trees, even for an ordinary manual reboot.
    os_version = HARDWARE.get_os_version()
    if not legacy_os_compatible(run(['git', 'show', 'HEAD:launch_env.sh'], FINALIZED), os_version):
      raise FastUpdateIncompatible('Update requires a different or unverified operating system')
    with marker.open('x') as f:
      f.write(os_version + '\n')

  if progress is not None:
    progress('Syncing the prepared update to storage...', None)
  set_consistent_flag(True)
  cloudlog.info("done finalizing overlay")


def handle_agnos_update() -> None:
  cur_version = HARDWARE.get_os_version()
  config = run(["bash", "-c", r"unset AGNOS_VERSION AGNOS_UPDATE_POLICY; source launch_env.sh && " +
                "printf '%s\\n%s\\n' \"$AGNOS_VERSION\" \"${AGNOS_UPDATE_POLICY:-auto}\""], OVERLAY_MERGED).splitlines()
  if len(config) != 2 or not config[0] or config[1] not in ("auto", "retain"):
    set_consistent_flag(False)
    raise ValueError("Invalid AGNOS installation policy")
  updated_version, policy = config

  cloudlog.info(f"AGNOS version check: {cur_version} vs {updated_version} ({policy})")
  if cur_version == updated_version:
    return

  # A mismatched retained OS must not finalize an update or trigger flashing.
  set_consistent_flag(False)
  if policy == "retain":
    raise RuntimeError(f"This StarPilot build requires existing AGNOS {updated_version}; installed {cur_version}. " +
                       "No OS update was attempted.")

  from openpilot.common.hardware.comma.agnos import flash_agnos_update, get_target_slot_number
  cloudlog.info(f"Beginning background installation for AGNOS {updated_version}")
  manifest_path = os.path.join(OVERLAY_MERGED, "openpilot/common/hardware/comma/agnos.json")
  if not os.path.isfile(manifest_path):  # releases before the openpilot/ move
    manifest_path = os.path.join(OVERLAY_MERGED, "system/hardware/tici/agnos.json")
  target_slot_number = get_target_slot_number()
  flash_agnos_update(manifest_path, target_slot_number, cloudlog)


class Updater:
  def __init__(self, install_settings=None):
    self.params = Params()
    self.branches: dict[str, str] = {}
    self._branches_checked = False
    self._has_internet: bool = False
    self._progress_lock = threading.RLock()
    self._metadata_lock = threading.Lock()
    self._request_lock = threading.Lock()
    self._check_thread = None
    self._pending_check = None
    self._closed = threading.Event()
    self._checked_at = 0.0
    self._checked_branch = None
    self.wake_automatic = None
    self.install_settings = install_settings if install_settings is not None else InstallSettings()

  def maybe_install(self, idle=lambda: True) -> bool:
    from zoneinfo import ZoneInfo
    value = self.install_settings.read()
    now = datetime.datetime.now(ZoneInfo(value.get('timezone', 'UTC') if value else 'UTC'))
    if not value or not value['enabled'] or not system_time_valid() or not cron_matches(value['cron'], now):
      return False
    from openpilot.starpilot.drive_state.evidence import PhysicalSource
    source = PhysicalSource()
    def parked():
      deadline = time.monotonic() + 1.5
      while True:
        if source.update_ready(self.params.get_bool('IsOffroad')):
          return True
        if time.monotonic() >= deadline:
          return False
        time.sleep(0.02)
    def ready():
      if (not idle() or self.params.get('UpdaterState') != 'idle' or
          self._check_thread is not None or not self.update_ready or Path(FINALIZED, '.overlay_consistent').is_symlink()):
        return False
      validate_update_revision(FINALIZED, 'HEAD', self.get_branch(FINALIZED))
      return True
    try:
      return scheduled_restart(self.params, self.install_settings, ready, parked, now)
    except (OSError, ValueError, FastUpdateError, subprocess.SubprocessError):
      cloudlog.exception('Scheduled update restart was not admitted')
      return False
    finally:
      source.close()

  def _write_progress(self, **values):
    if not hasattr(self, '_progress_lock'):
      self._progress_lock = threading.RLock()
    with self._progress_lock:
      previous = self.params.get('UpdaterFastState')
      state = dict(previous) if isinstance(previous, dict) else {'version': 1, 'stage': 'complete', 'detail': ''}
      state.update(values)
      self.params.put('UpdaterFastState', state, block=True)

  def progress(self, stage, detail, *, background=False, percent=None):
    if percent is None and (match := re.search(r'(\d+)%', detail)):
      percent = min(100, int(match[1]))
    self._write_progress(version=1, stage=stage, detail=detail[:512], background=background, percent=percent)

  def request_check(self, *, branch=None, commit=None, download_after=False):
    with self._request_lock:
      download_after = download_after or bool(self._pending_check and self._pending_check[2])
      self._pending_check = (branch or self.target_branch, commit, download_after)
      if self._check_thread is None:
        self._check_thread = threading.Thread(target=self._run_checks, name='updater-metadata', daemon=True)
        self._check_thread.start()

  def _run_checks(self):
    while not self._closed.is_set():
      with self._request_lock:
        request, self._pending_check = self._pending_check, None
        if request is None:
          self._check_thread = None
          return
      branch, commit, download_after = request
      try:
        if commit is not None and self.get_commit_hash(BASEDIR) != commit:
          raise FastUpdateError('Installed revision changed; refresh and try again')
        self.check_for_update(branch)
        if (download_after and self.wake_automatic is not None and not self.params.get_bool('DoReboot') and
            download_permitted(self.params, manual=False) and self.update_available and not self.latest_update_ready):
          self.wake_automatic()
      except Exception as error:
        self._write_progress(check={'state': 'error', 'branch': branch, 'detail': str(error)[:512], 'completedAt': time.monotonic()})

  def close(self):
    self._closed.set()
    if self._check_thread is not None:
      self._check_thread.join(timeout=6)

  @property
  def has_internet(self) -> bool:
    return self._has_internet

  @property
  def target_branch(self) -> str:
    b: str | None = self.params.get("UpdaterTargetBranch")
    if b is None:
      b = self.get_branch(BASEDIR)
    b = {
      ("tizi", "release3"): "release-tizi",
      ("tizi", "release3-staging"): "release-tizi-staging",
      ("mici", "release3"): "release-mici",
      ("mici", "release3-staging"): "release-mici-staging",
    }.get((HARDWARE.get_device_type(), b), b)
    return b

  @property
  def update_ready(self) -> bool:
    consistent_file = Path(os.path.join(FINALIZED, ".overlay_consistent"))
    branch, branches = self.target_branch, self.branches
    if self._branches_checked and consistent_file.is_file() and branch in branches:
      branch_mismatch = self.get_branch(BASEDIR) != branch
      on_target_branch = self.get_branch(FINALIZED) == branch
      staged = getattr(self, '_staged_target', None)
      expected = staged[1] if staged is not None and staged[0] == branch else branches[branch]
      hash_mismatch = self.get_commit_hash(BASEDIR) != expected
      finalized_at_target = self.get_commit_hash(FINALIZED) == expected
      return ((hash_mismatch or branch_mismatch) and on_target_branch and finalized_at_target)
    return False

  @property
  def latest_update_ready(self) -> bool:
    staged = getattr(self, '_staged_target', None)
    return self.update_ready and (staged is None or staged == (self.target_branch, self.branches.get(self.target_branch)))

  @property
  def update_available(self) -> bool:
    branch, branches = self.target_branch, self.branches
    if self._branches_checked and branch in branches:
      hash_mismatch = self.get_commit_hash(BASEDIR) != branches[branch]
      branch_mismatch = self.get_branch(BASEDIR) != branch
      return hash_mismatch or branch_mismatch
    return False

  def get_branch(self, path: str) -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"], path).rstrip()

  def get_commit_hash(self, path: str = OVERLAY_MERGED) -> str:
    return run(["git", "rev-parse", "HEAD"], path).rstrip()

  def set_params(self, update_success: bool, failed_count: int, exception: str | None) -> None:
    self.params.put("UpdateFailedCount", failed_count, block=True)
    self.params.put("UpdaterTargetBranch", self.target_branch, block=True)

    self.params.put_bool("UpdaterFetchAvailable", self.update_available, block=True)
    if len(self.branches):
      self.params.put("UpdaterAvailableBranches", ','.join(self.branches.keys()), block=True)

    last_uptime_onroad = self.params.get("UptimeOnroad", return_default=True)
    last_route_count = self.params.get("RouteCount", return_default=True)
    if update_success:
      self.params.put("LastUpdateTime", datetime.datetime.now(datetime.UTC).replace(tzinfo=None), block=True)
      self.params.put("LastUpdateUptimeOnroad", last_uptime_onroad, block=True)
      self.params.put("LastUpdateRouteCount", last_route_count, block=True)
    else:
      last_uptime_onroad = self.params.get("LastUpdateUptimeOnroad", return_default=True)
      last_route_count = self.params.get("LastUpdateRouteCount", return_default=True)

    if exception is None:
      self.params.remove("LastUpdateException")
    else:
      self.params.put("LastUpdateException", exception, block=True)

    # Write out current and new version info
    def get_description(basedir: str) -> str:
      if not os.path.exists(basedir):
        return ""

      version = ""
      branch = ""
      commit = ""
      commit_date = ""
      try:
        branch = self.get_branch(basedir)
        commit = self.get_commit_hash(basedir)[:7]
        with open(os.path.join(basedir, "openpilot", "common", "version.h")) as f:
          version = f.read().split('"')[1]

        commit_unix_ts = run(["git", "show", "-s", "--format=%ct", "HEAD"], basedir).rstrip()
        dt = datetime.datetime.fromtimestamp(int(commit_unix_ts))
        commit_date = dt.strftime("%b %d")
      except Exception:
        cloudlog.exception("updater.get_description")
      return f"{version} / {branch} / {commit} / {commit_date}"
    self.params.put("UpdaterCurrentDescription", get_description(BASEDIR), block=True)
    self.params.put("UpdaterCurrentReleaseNotes", parse_release_notes(BASEDIR), block=True)
    self.params.put("UpdaterNewDescription", get_description(FINALIZED), block=True)
    self.params.put("UpdaterNewReleaseNotes", parse_release_notes(FINALIZED), block=True)
    self.params.put_bool("UpdateAvailable", self.update_ready, block=True)

    # Handle user prompt
    for alert in ("Offroad_UpdateFailed", "Offroad_ConnectivityNeeded", "Offroad_ConnectivityNeededPrompt"):
      set_offroad_alert(alert, False)

    dt_uptime_onroad = (self.params.get("UptimeOnroad", return_default=True) - last_uptime_onroad) / (60*60)
    dt_route_count = self.params.get("RouteCount", return_default=True) - last_route_count
    build_metadata = get_build_metadata()
    if failed_count > 15 and exception is not None and self.has_internet:
      if build_metadata.tested_channel:
        extra_text = "Ensure the software is correctly installed. Uninstall and re-install if this error persists."
      else:
        extra_text = exception
      set_offroad_alert("Offroad_UpdateFailed", True, extra_text=extra_text)
    elif failed_count > 0:
      if dt_uptime_onroad > HOURS_NO_CONNECTIVITY_MAX and dt_route_count > ROUTES_NO_CONNECTIVITY_MAX:
        set_offroad_alert("Offroad_ConnectivityNeeded", True)
      elif dt_uptime_onroad > HOURS_NO_CONNECTIVITY_PROMPT and dt_route_count > ROUTES_NO_CONNECTIVITY_PROMPT:
        remaining = max(HOURS_NO_CONNECTIVITY_MAX - dt_uptime_onroad, 1)
        set_offroad_alert("Offroad_ConnectivityNeededPrompt", True, extra_text=f"{remaining} hour{'' if remaining == 1 else 's'}.")

  def check_for_update(self, branch: str | None = None) -> None:
    branch = branch or self.target_branch
    with self._metadata_lock:
      self._write_progress(check={'state': 'checking', 'branch': branch, 'detail': 'Fetching version metadata...'})
      try:
        self._check_for_update(branch)
        self._checked_at, self._checked_branch = time.monotonic(), branch
        self._write_progress(check={'state': 'complete', 'branch': branch, 'detail': 'Version metadata refreshed.',
                                    'completedAt': time.monotonic()})
      except Exception as error:
        self._write_progress(check={'state': 'error', 'branch': branch, 'detail': str(error)[:512], 'completedAt': time.monotonic()})
        raise

  def _check_for_update(self, branch) -> None:
    cloudlog.info("checking for updates")

    excluded_branches = ('release2', 'release2-staging')

    self._branches_checked = False
    self.params.put("UpdaterOSUpdate", {}, block=True)
    metadata = Path(METADATA_ROOT)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    if not (metadata / 'HEAD').is_file():
      run(['git', 'init', '--bare', str(metadata)], str(metadata.parent))
    remote = run(['git', 'remote', 'get-url', 'origin'], BASEDIR).strip()
    run(['git', 'config', 'remote.origin.url', remote], str(metadata))
    setup_git_options(str(metadata))
    try:
      output = run(["git", "ls-remote", "--heads", "origin"], str(metadata), cancelled=self._closed.is_set, timeout=30)
    except subprocess.CalledProcessError:
      self._has_internet = False
      raise
    self._has_internet = True

    branches = {}
    for line in output.split('\n'):
      ls_remotes_re = r'(?P<commit_sha>\b[0-9a-f]{5,40}\b)(\s+)(refs\/heads\/)(?P<branch_name>.*$)'
      x = re.fullmatch(ls_remotes_re, line.strip())
      if x is not None and x.group('branch_name') not in excluded_branches:
        branches[x.group('branch_name')] = x.group('commit_sha')
    self.branches = branches
    cur_branch = self.get_branch(BASEDIR)
    cur_commit = self.get_commit_hash(BASEDIR)
    self._branches_checked = True
    new_branch = branch or self.target_branch
    new_commit = self.branches.get(new_branch)
    if new_commit is None:
      cloudlog.info("target branch %s is not published on origin; no update available", new_branch)
      self.params.put('UpdaterAvailableBranches', ','.join(self.branches), block=True)
      self.params.put_bool('UpdaterFetchAvailable', False, block=True)
      self.params.put_bool('UpdateAvailable', False, block=True)
      write_time_to_param(self.params, 'UpdaterLastCheckTime')
      return
    # Fetch history and trees, not artifact contents, for the version list.
    def metadata_progress(detail):
      self._write_progress(check={'state': 'checking', 'branch': branch, 'detail': detail[:512]})
    run(["git", "fetch", '--progress', f"--depth={MAX_COMMITS}", "--filter=blob:none", "--no-tags", "--no-recurse-submodules",
         "origin", f"+{new_commit}:refs/remotes/origin/{new_branch}"], str(metadata),
        progress=metadata_progress, cancelled=self._closed.is_set, timeout=30)
    run(["git", "update-ref", f"refs/starpilot/recent/{new_branch}", new_commit], str(metadata))
    if AGNOS:
      try:
        version = required_os_version(run(["git", "show", f"{new_commit}:launch_env.sh"], str(metadata),
                                          download=True, cancelled=self._closed.is_set, timeout=30))
        current_os = HARDWARE.get_os_version()
        if version and current_os:
          self.params.put("UpdaterOSUpdate", {"branch": new_branch, "required": version != current_os}, block=True)
      except (OSError, subprocess.SubprocessError):
        cloudlog.exception("Unable to check target operating system version")
    if (cur_branch, cur_commit) != (new_branch, new_commit):
      cloudlog.info(f"update available, {cur_branch} ({str(cur_commit)[:7]}) -> {new_branch} ({str(new_commit)[:7]})")
    else:
      cloudlog.info(f"up to date on {cur_branch} ({str(cur_commit)[:7]})")
    self.params.put("UpdaterAvailableBranches", ','.join(self.branches), block=True)
    self.params.put_bool("UpdaterFetchAvailable", self.update_available, block=True)
    self.params.put_bool("UpdateAvailable", self.update_ready, block=True)
    write_time_to_param(self.params, 'UpdaterLastCheckTime')

  def fast_update(self, branch: str, commit: str, selected_commit=None, *, rollback=False, versions_only=False, automatic=False, download_only=False) -> str:
    from openpilot.starpilot.drive_state.evidence import PhysicalSource
    from openpilot.starpilot.software.fast_update import fast_update

    source = PhysicalSource()

    def parked():
      deadline = time.monotonic() + 0.75
      while True:
        if source.update_ready(self.params.get_bool("IsOffroad")):
          return True
        if time.monotonic() >= deadline:
          return False
        time.sleep(0.02)

    def invalidate():
      Path(FINALIZED, ".overlay_consistent").unlink(missing_ok=True)
      OVERLAY_INIT.unlink(missing_ok=True)
      dismount_overlay()
      self.params.put_bool("UpdateAvailable", False, block=True)
      self._branches_checked = False

    def progress(stage, detail):
      self.progress(stage, detail)

    try:
      if versions_only:
        initial_branch = self.get_branch(BASEDIR)
        def admitted():
          if not parked() or self.get_commit_hash(BASEDIR) != commit or self.get_branch(BASEDIR) != initial_branch:
            raise FastUpdateError("Update source or parked state changed")
        admitted()
        run(["git", "check-ref-format", "--branch", branch], BASEDIR)
        progress('fetching', 'Fetching version metadata...')
        self.check_for_update(branch)
        admitted()
        if branch not in self.branches:
          raise FastUpdateError("Target branch is no longer available")
        progress('complete', 'Recent versions are ready to review.')
        return 'versions-listed'
      if download_only:
        def admitted():
          if not parked() or self.get_commit_hash(BASEDIR) != commit:
            raise FastUpdateError('Installed revision or parked state changed; refresh and try again')
        admitted()
        self.check_for_update(branch)
        members = commit_history(Path(METADATA_ROOT), self.branches.get(branch, ''), first_parent=True)
        if branch == self.get_branch(BASEDIR):
          members += commit_history(Path(BASEDIR), commit, first_parent=True)
        if not any(row['hash'] == selected_commit for row in members):
          raise FastUpdateError('Selected version is no longer in branch history; refresh and try again')
        admitted()
        self.params.put('UpdaterTargetBranch', branch, block=True)
        if not self.fetch_update(selected_commit=selected_commit, admitted=admitted):
          raise FastUpdateError('Selected version could not be downloaded')
        admitted()
        self.set_params(True, 0, None)
        return 'downloaded'
      def installed_progress(stage, detail):
        if stage == 'complete':
          self.params.put('UpdaterTargetBranch', self.get_branch(BASEDIR), block=True)
        progress(stage, detail)
      return fast_update(Path(BASEDIR), branch, expected_commit=commit, params=self.params, parked=parked,
                         current_os=HARDWARE.get_os_version(), invalidate=invalidate, rollback=rollback, progress=installed_progress,
                         selected_commit=selected_commit, versions_only=versions_only)
    except FastUpdateIncompatible:
      if not automatic:
        raise
      return self.normal_update(branch, commit, parked, progress)
    finally:
      source.close()

  def normal_update(self, branch: str, commit: str, parked, progress) -> str:
    def admitted():
      if not parked() or self.get_commit_hash(BASEDIR) != commit:
        raise FastUpdateError("Update source or parked state changed")

    admitted()
    progress("preparing", "Preparing full software update")
    self.params.put("UpdaterTargetBranch", branch, block=True)
    self._branches_checked = False
    self.params.put("UpdaterState", "checking...", block=True)
    if phone_uplink_active():
      raise FastUpdateError("Software downloads are disabled while using phone internet")
    if not self.fetch_update():
      raise FastUpdateError("Target branch is no longer available")
    admitted()
    self.params.put_bool("UpdateAvailable", True, block=True)
    self.params.put("UpdaterState", "updating...", block=True)
    progress("rebooting", "Update installed. Restarting device.")
    self.params.put_bool("DoReboot", True, block=True)
    return "reboot-requested"

  def fetch_update(self, *, background=False, selected_commit=None, admitted=None) -> bool:
    if phone_uplink_active():
      return False
    if not self._branches_checked or self._checked_branch != self.target_branch:
      self.check_for_update()
    if self.target_branch not in self.branches:
      cloudlog.info("skipping fetch of unpublished target branch %s", self.target_branch)
      return False

    cloudlog.info("attempting git fetch inside staging overlay")

    self.params.put("UpdaterState", "downloading...", block=True)
    self.progress('preparing', 'Preparing download staging...', background=background)
    init_overlay()
    self.progress('fetching', 'Downloading the selected version...', background=background)
    def git_progress(detail):
      self.progress('fetching', detail, background=background)

    # TODO: cleanly interrupt this and invalidate old update
    set_consistent_flag(False)
    self.params.put_bool("UpdateAvailable", False, block=True)

    setup_git_options(OVERLAY_MERGED)

    run(["git", "config", "--replace-all", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"], OVERLAY_MERGED)

    branch = self.target_branch
    # Validate using the running updater before checking out or executing any update.
    # A branch containing gitlinks or an invalid dependency manifest is not installable.
    fetched_commit = selected_commit or self.branches[branch]
    run(['git', 'fetch', '--progress', '--depth=1', '--filter=blob:none', '--no-tags', '--no-recurse-submodules',
         'origin', f'+{fetched_commit}:refs/remotes/origin/{branch}'], OVERLAY_MERGED, progress=git_progress)
    # Validate before checkout or executing any target code. Legacy returns are
    # restricted to known release branches and must already support this OS.
    legacy_return = validate_update_revision(OVERLAY_MERGED, fetched_commit, branch)
    def git(*args, stdin=None):
      return run(['git', *args], OVERLAY_MERGED, progress=git_progress, stdin=stdin)
    fetch_files(git, fetched_commit)
    run(["git", "update-ref", f"refs/starpilot/recent/{branch}", fetched_commit], OVERLAY_MERGED)

    if admitted is not None:
      admitted()
    cloudlog.info("git reset in progress")
    self.progress('applying', 'Writing downloaded files...', background=background)
    def apply_progress(detail):
      self.progress('applying', detail, background=background)
    installed_commit = self.get_commit_hash(BASEDIR)
    installed_branch = self.get_branch(BASEDIR)
    run(['git', 'update-ref', 'refs/starpilot/previous', installed_commit], OVERLAY_MERGED)
    run(['git', 'config', '--local', 'starpilot.previousVersion', f'{installed_commit} {installed_branch}'], OVERLAY_MERGED)
    cmds = [
      ["git", "checkout", '--progress', "--force", "--no-recurse-submodules", "-B", branch, fetched_commit],
      ["git", "branch", "--set-upstream-to", f"origin/{branch}"],
      ["git", "reset", "--hard", "--no-recurse-submodules"],
      ["git", "clean", "-xdff"],
    ]
    r = [run(cmd, OVERLAY_MERGED, download=cmd[1] in ('checkout', 'reset'), progress=apply_progress) for cmd in cmds]
    validate_update_revision(OVERLAY_MERGED, "HEAD", branch)
    cloudlog.info("git reset success: %s", '\n'.join(r))

    # TODO: show agnos download progress
    if AGNOS and not legacy_return:
      handle_agnos_update()

    # Create the finalized, ready-to-swap update
    self.params.put("UpdaterState", "finalizing update...", block=True)
    self.progress('finalizing', 'Preparing the downloaded update...', background=background)
    finalize_update(progress=lambda detail, percent: self.progress('finalizing', detail, background=background, percent=percent))
    self._staged_target = (branch, fetched_commit)
    self.progress('complete', 'Downloaded update is ready to install.', background=background)
    cloudlog.info("finalize success!")
    return True


def recover_fast_state(params):
  previous = params.get("UpdaterFastState")
  if not isinstance(previous, dict):
    return
  state = dict(previous)
  if state.get('check', {}).get('state') == 'checking':
    state['check'] = {**state['check'], 'state': 'error', 'completedAt': time.monotonic(),
                      'detail': 'Metadata check was interrupted when the updater restarted. Retry the check.'}
  if state.get('stage') not in ('complete', 'error'):
    state.update(version=1, stage='error',
                 detail='Update process restarted before completion; check the installed version and retry.')
  if state != previous:
    params.put('UpdaterFastState', state, block=True)


def main() -> None:
  params = Params()

  if params.get_bool("DisableUpdates"):
    cloudlog.warning("updates are disabled by the DisableUpdates param")
    exit(0)

  with open(LOCK_FILE, 'w') as ov_lock_fd:
    try:
      fcntl.flock(ov_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
      raise RuntimeError("couldn't get overlay lock; is another instance running?") from e

    # Check if we just performed an update
    if Path(os.path.join(STAGING_ROOT, "old_openpilot")).is_dir():
      cloudlog.event("update installed")

    if not params.get("InstallDate"):
      t = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
      params.put("InstallDate", t, block=True)

    updater = Updater()
    update_failed_count = max(0, params.get("UpdateFailedCount") or 0)
    wait_helper = WaitTimeHelper(metadata=updater.request_check)
    wait_helper.idle_tick = lambda: updater.maybe_install(lambda: not wait_helper.ready_event.is_set())
    updater.wake_automatic = wait_helper.wake_automatic
    atexit.register(updater.close)

    # invalidate old finalized update
    set_consistent_flag(False)

    recover_fast_state(params)
    # set initial state
    params.put("UpdaterState", "idle", block=True)

    # Run the update loop
    first_run = True
    while True:
      requested, request_generation, fast_target = wait_helper.current_request()

      # Attempt an update
      exception = None
      fast_requested = requested in (
        UserRequest.UPDATE, UserRequest.FAST, UserRequest.ROLLBACK, UserRequest.VERSIONS, UserRequest.VERSION, UserRequest.DOWNLOAD_VERSION,
      )
      try:
        if fast_requested:
          update_failed_count += 1
          params.put("UpdaterState", "downloading..." if requested == UserRequest.DOWNLOAD_VERSION else "updating...", block=True)
          if fast_target is None:
            raise ValueError("Update source identity is required")
          if requested == UserRequest.DOWNLOAD_VERSION:
            updater.fast_update(*fast_target, download_only=True)
          elif requested == UserRequest.VERSIONS:
            updater.fast_update(*fast_target, versions_only=True)
          elif requested == UserRequest.UPDATE:
            updater.fast_update(*fast_target, automatic=True)
          else:
            updater.fast_update(*fast_target, rollback=requested == UserRequest.ROLLBACK)
          if requested == UserRequest.ROLLBACK:
            params.put_bool(AUTOMATIC_DOWNLOADS, False, block=True)
          write_time_to_param(params, "UpdaterLastFetchTime")
          if requested != UserRequest.VERSIONS:
            write_time_to_param(params, "LastUpdateTime")
        else:
          # ensure we have some params written soon after startup
          updater.set_params(False, update_failed_count, exception)

          if not system_time_valid() or first_run:
            first_run = False
            wait_helper.sleep(60)
            continue

          update_failed_count += 1

          # check for update
          if requested == UserRequest.CHECK or updater._checked_branch != updater.target_branch or time.monotonic() - updater._checked_at > 60:
            updater.check_for_update()

          # download update
          last_fetch = params.get("UpdaterLastFetchTime")
          timed_out = last_fetch is None or (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - last_fetch > datetime.timedelta(days=3))
          user_requested_fetch = requested == UserRequest.FETCH
          if params.get_bool('DoReboot'):
            cloudlog.info('skipping fetch, restart pending')
          elif requested == UserRequest.CHECK:
            cloudlog.info('metadata-only check complete')
          elif phone_uplink_active():
            cloudlog.info("skipping fetch, phone uplink forbids updates")
          elif params.get_bool("NetworkMetered") and not timed_out and not user_requested_fetch:
            cloudlog.info("skipping fetch, connection metered")
          elif not download_permitted(params, manual=user_requested_fetch):
            cloudlog.info("skipping fetch, automatic downloads disabled")
          elif not user_requested_fetch and (updater.latest_update_ready or not updater.update_available):
            cloudlog.info("skipping fetch, checked version is already installed or staged")
          else:
            if updater.fetch_update(background=not user_requested_fetch):
              write_time_to_param(params, "UpdaterLastFetchTime")
        update_failed_count = 0
      except subprocess.CalledProcessError as e:
        cloudlog.event(
          "update process failed",
          cmd=e.cmd,
          output=e.output,
          returncode=e.returncode
        )
        exception = f"command failed: {e.cmd}\n{e.output}"
        if requested != UserRequest.VERSIONS:
          OVERLAY_INIT.unlink(missing_ok=True)
      except Exception as e:
        cloudlog.exception("uncaught updated exception, shouldn't happen")
        exception = str(e)
        if requested != UserRequest.VERSIONS:
          OVERLAY_INIT.unlink(missing_ok=True)

      try:
        update_successful = (update_failed_count == 0)
        if fast_requested:
          if exception is not None:
            updater.progress('error', exception)
          params.put("UpdateFailedCount", update_failed_count, block=True)
          if exception is None:
            params.remove("LastUpdateException")
          else:
            params.put("LastUpdateException", exception.encode("utf-8")[:4096].decode("utf-8", errors="ignore"), block=True)
          params.put("UpdaterState", "updating..." if params.get_bool("DoReboot") else "idle", block=True)
        else:
          params.put("UpdaterState", "idle", block=True)
          updater.set_params(update_successful, update_failed_count, exception)
      except Exception:
        cloudlog.exception("uncaught updated exception while setting params, shouldn't happen")

      # infrequent attempts if we successfully updated recently
      if not wait_helper.finish_request(request_generation):
        continue
      wait_helper.sleep(5*60 if update_failed_count > 0 else 1.5*60*60)


if __name__ == "__main__":
  main()
