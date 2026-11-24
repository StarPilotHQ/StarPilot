"""Parked requests to the existing updater and manager."""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpilot.common.basedir import BASEDIR
from openpilot.common.hardware import COMMA_HARDWARE
from openpilot.common.params import Params
from openpilot.common.vendor_manifest import validate_revision
from openpilot.starpilot.galaxy.software_status import SoftwareStatus, SoftwareUnavailable
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.software.update_control import UpdaterControlError
from openpilot.starpilot.software.preferences import AUTOMATIC_DOWNLOADS, automatic_downloads
from openpilot.starpilot.software.auto_install import DEFAULT_CRON, InstallSettings, cron_fields, next_restart
from openpilot.starpilot.software.history import METADATA_ROOT, commit_history, downloaded_versions, previous_version, release_notes, recent_versions


FINALIZED = Path(os.environ.get("UPDATER_STAGING_ROOT", "/data/safe_staging")) / "finalized"
BRANCH_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/+@-]{0,127}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
MAX_BRANCH_LIST = 32768
MAX_BRANCHES = 256
SOURCE_ACTIONS = frozenset(("update", "fast", "rollback", "versions", "version", "download-version"))
PROCESS_TITLE = b"openpilot.system.updated.updated"


class SoftwareOperationError(Exception):
  def __init__(self, message: str, status: int = 409):
    super().__init__(message)
    self.status = status


class UpdaterProcess:
  """Resolve the exact manager child before using its updater-owned control."""

  def __init__(self, messages=None, *, control=None, proc_root: Path = Path("/proc"),
               mono_ns: Callable[[], int] = time.monotonic_ns):
    if control is None:
      from openpilot.starpilot.software import update_control
      control = update_control
    self.messages = messages
    self.control = control
    self.proc_root = proc_root
    self.mono_ns = mono_ns
    self.created_ns = mono_ns()
    self.lock = threading.Lock()

  def _manager_pid(self) -> int:
    if self.messages is None:
      from openpilot.cereal import messaging
      self.messages = messaging.SubMaster(["managerState"])
    sm = self.messages
    sm.update(0)
    now = self.mono_ns()
    received = int(sm.recv_time["managerState"] * 1e9)
    published = int(sm.logMonoTime["managerState"])
    age = now - received
    if (not sm.seen["managerState"] or not sm.alive["managerState"] or not sm.valid["managerState"] or
        not 0 <= age <= 3_000_000_000 or received <= self.created_ns or
        not 0 <= now - published <= 3_000_000_000 or published <= self.created_ns):
      raise SoftwareOperationError("Updater manager status is unavailable", 503)
    matches = [p for p in sm["managerState"].processes if p.name == "updated"]
    if len(matches) != 1 or not matches[0].running or not matches[0].shouldBeRunning or int(matches[0].pid) <= 1:
      raise SoftwareOperationError("Updater is not running", 503)
    return int(matches[0].pid)

  def _proc_identity(self, pid: int) -> int:
    base = self.proc_root / str(pid)
    try:
      raw = (base / "stat").read_text()
      right = raw.rfind(")")
      if right < 0:
        raise ValueError
      fields = raw[right + 2:].split()
      start = int(fields[19])
      cmdline = (base / "cmdline").read_bytes().split(b"\0", 1)[0]
      if start <= 0 or cmdline != PROCESS_TITLE:
        raise ValueError
      return start
    except (OSError, ValueError, IndexError):
      raise SoftwareOperationError("Updater process identity is unavailable", 503) from None

  def current_identity(self) -> tuple[int, int] | None:
    with self.lock:
      try:
        pid = self._manager_pid()
        start = self._proc_identity(pid)
        if self._manager_pid() == pid and self._proc_identity(pid) == start and self.control.available(pid, start):
          return pid, start
      except (SoftwareOperationError, UpdaterControlError, OSError, RuntimeError, ValueError):
        pass
      return None

  def available(self) -> bool:
    return self.current_identity() is not None

  def send(self, action: str, *, branch=None, commit=None, selected_commit=None) -> tuple[int, int]:
    if action not in ("check", "download", "update", "fast", "rollback", "versions", "version", "download-version"):
      raise SoftwareOperationError("Invalid updater command", 400)
    with self.lock:
      pid = self._manager_pid()
      start = self._proc_identity(pid)
      if self._manager_pid() != pid or self._proc_identity(pid) != start:
        raise SoftwareOperationError("Updater process changed", 503)
      try:
        if action in ('version', 'download-version'):
          self.control.send(pid, start, action, branch=branch, commit=commit, selected_commit=selected_commit)
        elif action in ("update", "fast", "rollback", "versions"):
          self.control.send(pid, start, action, branch=branch, commit=commit)
        else:
          self.control.send(pid, start, action)
      except (UpdaterControlError, OSError, RuntimeError, ValueError):
        raise SoftwareOperationError("Updater control is unavailable", 503) from None
      return pid, start

  def close(self) -> None:
    with self.lock:
      if self.messages is not None:
        for sock in getattr(self.messages, "sock", {}).values():
          close = getattr(sock, "close", None)
          if close:
            close()
        self.messages = None


def _git_identity(path: Path) -> tuple[str, str] | None:
  try:
    branch = subprocess.run(["git", "-C", str(path), "symbolic-ref", "--quiet", "--short", "HEAD"],
                            check=True, capture_output=True, text=True, timeout=2).stdout.strip()
    commit = subprocess.run(["git", "-C", str(path), "rev-parse", "--verify", "HEAD^{commit}"],
                            check=True, capture_output=True, text=True, timeout=2).stdout.strip()
    if not BRANCH_PATTERN.fullmatch(branch) or not COMMIT_PATTERN.fullmatch(commit):
      return None
    return branch, commit
  except (OSError, subprocess.SubprocessError):
    return None


class SoftwareOperations:
  def __init__(self, params=None, status=None, parked=None, process=None, *, finalized=FINALIZED,
               installed=BASEDIR, git_identity=_git_identity, clock=time.monotonic, history_reader=commit_history, refresh=None,
               metadata=METADATA_ROOT, install_settings=None):
    self.params = params if params is not None else Params()
    self.status = status if status is not None else SoftwareStatus(self.params)
    if parked is None:
      from openpilot.starpilot.drive_state.evidence import PhysicalSource
      self.context = PhysicalSource()
      physical = self.context
      self.parked = lambda: physical.update_ready(self._flag('IsOffroad') is True)
    else:
      self.context = None
      self.parked = parked
    self.process = process if process is not None else UpdaterProcess()
    self.refresh = refresh
    self.finalized = Path(finalized)
    self.staging = self.finalized.parent / 'merged'
    self.metadata = Path(metadata)
    self.install_settings = install_settings if install_settings is not None else InstallSettings()
    self.installed = Path(installed)
    self.git_identity = git_identity
    self.clock = clock
    self.history_reader = history_reader
    self._history_key = None
    self._history_at = float('-inf')
    self._history = None
    self.lock = threading.RLock()
    self.selected_target: str | None = None
    self.request: dict | None = None
    self._baseline: tuple | None = None
    self._started = 0.0
    self._fast_commit = None
    self._request_owner = None

  def _param(self, key: str, limit: int = 256) -> str | None:
    try:
      value = self.params.get(key)
      if isinstance(value, bytes):
        value = value.decode("utf-8")
      if value is not None and (type(value) is not str or len(value.encode()) > limit or not value.isprintable()):
        raise ValueError
      return value
    except (OSError, ValueError, UnicodeError, TypeError):
      raise SoftwareOperationError("Updater status is unavailable", 503) from None

  def _branches(self) -> list[str]:
    raw = self._param("UpdaterAvailableBranches", MAX_BRANCH_LIST)
    if raw is None or not raw:
      return []
    entries = raw.split(",")
    if len(entries) > MAX_BRANCHES:
      return []
    return list(dict.fromkeys(branch for branch in entries if BRANCH_PATTERN.fullmatch(branch)))

  def _flag(self, key: str) -> bool | None:
    try:
      raw, readable = read_saved(self.params, key, 8)
      if not readable:
        return None
      if raw is None:
        return False
      return raw == b"1" if raw in (b"0", b"1") else None
    except (OSError, ValueError, TypeError):
      return None

  @staticmethod
  def _valid_branch(branch: object) -> bool:
    if type(branch) is not str or not BRANCH_PATTERN.fullmatch(branch):
      return False
    try:
      return subprocess.run(["git", "check-ref-format", "--branch", branch], capture_output=True,
                            timeout=2).returncode == 0
    except (OSError, subprocess.SubprocessError):
      return False

  def _parked(self) -> bool:
    try:
      return self._flag("IsOffroad") is True and bool(self.parked())
    except (OSError, RuntimeError, ValueError, TypeError):
      return False

  def _ready(self, status: dict, target: str | None) -> bool:
    try:
      marker = self.finalized.joinpath(".overlay_consistent").lstat()
      regular_marker = stat.S_ISREG(marker.st_mode)
    except OSError:
      regular_marker = False
    if (target is None or status["updater"]["targetBranch"] != target or
        status["updater"]["finalizedUpdateReady"] is not True or
        status["updater"]["state"] != "idle" or
        not regular_marker):
      return False
    final = self.git_identity(self.finalized)
    current = self.git_identity(self.installed)
    if final is None or current is None or final[0] != target or final == current:
      return False
    description = self._param("UpdaterNewDescription")
    parts = description.split(" / ") if description else []
    if len(parts) != 4 or parts[1] != target or not final[1].startswith(parts[2]) or len(parts[2]) != 7:
      return False
    try:
      validate_revision(self.finalized, final[1])
    except (OSError, ValueError, subprocess.SubprocessError):
      return False
    return True

  def _refresh_request(self, status: dict, current_owner=None, *, parked=False) -> None:
    request = self.request
    if self._baseline is None or request is None or request["state"] != "pending" or request["action"] == "install":
      return
    state = status["updater"]["state"]
    check = (status['updater'].get('fast') or {}).get('check')
    if request['action'] in ('check', 'versions') and check is not None:
      completed = check.get('completedAt')
      if (type(completed) in (int, float) and completed >= self._started and
          (request['target'] is None or check['branch'] == request['target'])):
        if check['state'] == 'complete':
          request.update(state='complete', error=None)
        elif check['state'] == 'error':
          request.update(state='failed', error=check['detail'])
      elif current_owner is not None and self._request_owner is not None and current_owner != self._request_owner:
        request.update(state='failed', error='Metadata check was interrupted when the updater restarted. Retry the check.')
      elif self.clock() - self._started > 90:
        request.update(state='failed', error='Metadata check did not report completion. Retry the check.')
      return
    if (request["action"] not in SOURCE_ACTIONS and request["target"] is not None and
        status["updater"]["targetBranch"] != request["target"]):
      request.update(state="failed", error="Updater target changed")
    elif state == "idle" and status["updater"]["failedCount"] is not None and self._baseline[2] is not None and \
         status["updater"]["failedCount"] > self._baseline[2]:
      error = "Updater reported a failure"
      try:
        error = self._param("LastUpdateException", 4096) or error
      except SoftwareOperationError:
        pass
      request.update(state="failed", error=error)
    elif request['action'] == 'versions' and state == 'idle' and status['updater']['failedCount'] == 0 and \
         status['updater']['lastFetchAt'] is not None and status['updater']['lastFetchAt'] != self._baseline[1]:
      request.update(state='complete', error=None)
    elif state == "idle" and status["updater"]["failedCount"] == 0 and \
         status["updater"]["lastSuccessAt"] is not None and \
         status["updater"]["lastSuccessAt"] != self._baseline[0]:
      if request["action"] in SOURCE_ACTIONS:
        if status["updater"]["lastFetchAt"] != self._baseline[1]:
          installed = self.git_identity(self.installed)
          restarting = self._flag("DoReboot") is True or installed is not None and installed[1] != self._fast_commit
          if request['action'] == 'download-version':
            if self._ready(status, request['target']) and self.git_identity(self.finalized) == (request['target'], request['selectedCommit']):
              request.update(state='complete', error=None)
            else:
              request.update(state='failed', error='Selected version was not prepared; refresh and try again')
          elif request['action'] == 'versions':
            request.update(state='complete', error=None)
          else:
            request.update(state="complete", error=None, outcome="restarting" if restarting else "up_to_date")
      elif request["action"] == "check" or status["updater"]["lastFetchAt"] != self._baseline[1]:
        request.update(state="complete", error=None)
      elif self._ready(status, request["target"]):
        request.update(state="complete", error=None)
      else:
        request.update(state="failed", error="No new download was reported for this branch")
    if request["state"] != "pending":
      return
    if (state == "idle" and self._flag("DoReboot") is False and current_owner is not None and
          ((self._request_owner is not None and current_owner != self._request_owner) or
           (self._request_owner is None and parked and
            status["updater"].get("fast") == {"stage": "error", "detail":
              "Update process restarted before completion; check the installed version and retry."}))):
      request.update(state="failed", error="Update was interrupted when the updater stopped. Retry while parked.")
    elif self.clock() - self._started > 900:
      request.update(state="failed", error="Updater did not report completion")

  def _rollback_available(self):
    try:
      result = subprocess.run(["git", "-C", str(self.installed), "rev-parse", "--verify", "refs/starpilot/previous^{commit}"],
                              check=True, capture_output=True, text=True, timeout=2)
      branch = subprocess.run(["git", "-C", str(self.installed), "config", "--local", "--get", "starpilot.previousVersion"],
                              check=True, capture_output=True, text=True, timeout=2).stdout.strip()
      saved = branch.split(' ', 1)
      return bool(len(saved) == 2 and saved[0] == result.stdout.strip() and COMMIT_PATTERN.fullmatch(saved[0]) and self._valid_branch(saved[1]))
    except (OSError, subprocess.SubprocessError):
      return False

  def snapshot(self, *, include_history: bool = True) -> dict:
    with self.lock:
      try:
        status = self.status.snapshot()
      except SoftwareUnavailable:
        raise SoftwareOperationError("Updater status is unavailable", 503) from None
      identity_reader = getattr(self.process, "current_identity", None)
      current_owner = identity_reader() if callable(identity_reader) else None
      process = current_owner is not None if callable(identity_reader) else self.process.available()
      try:
        fresh = self.refresh is None or self.refresh() is True
      except (OSError, RuntimeError, ValueError, TypeError):
        fresh = False
      if not fresh:
        admission = {'allowed': False, 'reason': 'Current vehicle status refresh failed'}
      elif self.context is not None:
        admission = self.context.update_status(self._flag('IsOffroad') is True)
      else:
        admission = {'allowed': self._parked(),
                     'reason': 'Switch the device Offroad and confirm the vehicle is parked before updating'}
      parked = bool(admission['allowed'])
      self._refresh_request(status, current_owner, parked=parked)
      branches = self._branches()
      updater_idle = status["updater"]["state"] == "idle"
      pending = self.request is not None and self.request["state"] == "pending"
      fast = status['updater'].get('fast') or {}
      metadata_busy = fast.get('check', {}).get('state') == 'checking' or pending and self.request['action'] in ('check', 'versions')
      disabled_state = self._flag("DisableUpdates")
      disabled = disabled_state is not False
      reboot = self._flag("DoReboot") is not False
      usable = parked and updater_idle and not disabled and not pending and not reboot and process and not metadata_busy
      can_check = (parked and not disabled and not reboot and process and not metadata_busy and
                   status['updater']['state'] in ('idle', 'checking...', 'downloading...', 'finalizing update...') and
                   (not pending or self.request['action'] in ('download', 'download-version')))
      target = self.selected_target or status["updater"]["targetBranch"] or status["installed"]["branch"]
      selected = target is not None and target in branches and status["updater"]["targetBranch"] == target
      reason = None
      if not parked:
        reason = admission['reason']
      elif disabled:
        reason = "Updates are disabled"
      elif reboot:
        reason = "Reboot is pending"
      elif not process:
        reason = "Getting the updater ready…" if COMMA_HARDWARE else "Updater status is unavailable"
      elif pending or not updater_idle or metadata_busy:
        reason = "Updater is busy"
      fast = status["updater"].get("fast") or {}
      active = (status['updater']['state'] == 'updating...' or
                status['updater']['state'] in ('downloading...', 'finalizing update...') and
                fast.get('stage') not in ('complete', 'error'))
      stage = fast["stage"] if fast and active else status["updater"]["state"]
      if stage == "idle":
        stage = None
      detail = fast["detail"] if fast and active else ""
      if not process and not updater_idle:
        stage = "unavailable"
        detail = ("Waiting for the device updater service…" if COMMA_HARDWARE else
                  "Update checks require the device updater service, which is unavailable when Galaxy runs alone on a development computer.")
      elif self.request is not None and self.request.get("error") == "Updater did not report completion":
        stage, detail = "timed_out", "Request timed out; the updater may still be running"
      install = self.install_settings.read()
      prepared = self.git_identity(self.finalized) if status["updater"]["finalizedUpdateReady"] else None
      restart = next_restart(install["cron"], datetime.now(ZoneInfo(install.get("timezone", "UTC")))) if install and install["enabled"] and prepared else None
      return {
        "repository": self._repository(),
        "updaterAvailable": process, "progress": {"stage": stage, "detail": detail, "percent": fast.get('percent') if active else None,
                                                  **({'background': fast['background']} if active and 'background' in fast else {})},
        "parked": parked, "availableBranches": branches, "selectedTarget": target,
        "canCheck": can_check, "canFastUpdate": usable and self._valid_branch(status["installed"]["branch"]), "canSelect": usable and bool(branches),
        "canRollback": usable and self._rollback_available(),
        "canDownload": usable and selected, "canInstall": usable and selected and self._ready(status, target),
        "reason": reason, "request": dict(self.request) if self.request is not None else None,
        "automaticDownloads": automatic_downloads(self.params), "canConfigure": parked and not reboot,
        "automaticInstalls": install['enabled'] if install else None,
        "restartSchedule": install['cron'] if install else DEFAULT_CRON,
        "installPreferenceRevision": install['revision'] if install else None,
        "restartTimezone": install.get("timezone", "UTC") if install else "UTC",
        "preparedCommit": prepared[1] if prepared else None,
        "nextRestartAt": restart.isoformat() if restart else None,
        **({"history": self._history_snapshot(status)} if include_history else {}),
      }

  def _repository(self) -> str | None:
    remote = self._param('GitRemote') or ''
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?', remote)
    return match[1] if match else None

  def _history_snapshot(self, status: dict) -> dict:
    branch = self.selected_target or status['updater']['targetBranch'] or status['installed']['branch']
    key = (status['installed']['commit'], self._param('UpdaterNewDescription'), branch,
           status['updater']['lastFetchAt'], status['updater'].get('lastCheckedAt'), status['updater']['state'])
    now = self.clock()
    if self._history is not None and self._history_key == key and 0 <= now - self._history_at < 30.:
      return self._history
    recent = recent_versions(self.installed, branch)
    staged_recent = recent_versions(self.staging, branch)
    if staged_recent['head'] is not None:
      recent = staged_recent
    elif recent['head'] is None:
      recent = recent_versions(self.finalized, branch)
    metadata = recent_versions(self.metadata, branch)
    if metadata['head'] is not None:
      recent = metadata
    installed = self.git_identity(self.installed)
    downloaded = self.git_identity(self.finalized)
    current_rows = self.history_reader(self.installed, installed[1], first_parent=True) if installed else []
    previous = previous_version(self.installed)
    downloaded_rows = (self.history_reader(self.finalized, downloaded[1])
                       if downloaded and downloaded != installed else [])
    # Readiness and installation still require _ready(); a history entry never
    # grants permission to boot or execute a revision.
    self._history = {
      'installed': current_rows, 'downloaded': downloaded_rows, 'recent': recent, 'previous': previous,
      'downloadedCommits': downloaded_versions((self.staging if self.staging.exists() else self.finalized, self.installed),
                                              (row['hash'] for row in [*recent['entries'], *current_rows, *([previous] if previous else [])])),
      'currentReleaseNotes': release_notes(self.params, 'UpdaterCurrentReleaseNotes'),
      'downloadedReleaseNotes': release_notes(self.params, 'UpdaterNewReleaseNotes') if downloaded_rows else None,
    }
    self._history_at, self._history_key = now, key
    return self._history

  def _preferences(self, payload: dict, authorized: Callable[[], bool]) -> dict:
    expected = payload.get('expectedAutomaticDownloads')
    enabled = payload.get('automaticDownloads')
    if (set(payload) != {'action', 'automaticDownloads', 'expectedAutomaticDownloads'} or
        type(enabled) is not bool or expected is not None and type(expected) is not bool):
      raise SoftwareOperationError('Invalid software preferences', 400)
    with self.lock:
      if not authorized():
        raise SoftwareOperationError('Session expired', 403)
      if not self._parked() or self._flag('DoReboot') is not False:
        raise SoftwareOperationError('Park the vehicle to change update preferences', 409)
      if automatic_downloads(self.params) is not expected:
        raise SoftwareOperationError('Update preference changed; refresh before saving', 409)
      if not authorized() or not self._parked() or self._flag('DoReboot') is not False:
        raise SoftwareOperationError('Session or parked state changed', 403)
      if automatic_downloads(self.params) is not expected:
        raise SoftwareOperationError('Update preference changed; refresh before saving', 409)
      self.params.put_bool(AUTOMATIC_DOWNLOADS, enabled, block=True)
      if automatic_downloads(self.params) is not enabled:
        raise SoftwareOperationError('Update preference could not be verified', 503)
      return self.snapshot()

  def _install_preferences(self, payload: dict, authorized: Callable[[], bool]) -> dict:
    if (set(payload) not in ({'action', 'automaticInstalls', 'restartSchedule', 'expectedRevision'},
                             {'action', 'automaticInstalls', 'restartSchedule', 'expectedRevision', 'restartTimezone'}) or
        type(payload['automaticInstalls']) is not bool or type(payload['restartSchedule']) is not str or
        payload['expectedRevision'] is not None and
        (type(payload['expectedRevision']) is not str or not re.fullmatch('[0-9a-f]{32}', payload['expectedRevision']))):
      raise SoftwareOperationError('Invalid install preferences', 400)
    try:
      cron_fields(payload['restartSchedule'])
      if 'restartTimezone' in payload:
        if not isinstance(payload['restartTimezone'], str) or len(payload['restartTimezone']) > 128:
          raise ValueError('Invalid timezone')
        ZoneInfo(payload['restartTimezone'])
    except (ValueError, ZoneInfoNotFoundError) as error:
      raise SoftwareOperationError(str(error), 400) from error
    with self.lock:
      if not authorized():
        raise SoftwareOperationError('Session expired', 403)
      if not self._parked() or self._flag('DoReboot') is not False:
        raise SoftwareOperationError('Park the vehicle to change install preferences', 409)
      def admitted():
        return authorized() and self._parked() and self._flag('DoReboot') is False
      try:
        self.install_settings.save(payload['automaticInstalls'], payload['restartSchedule'], payload['expectedRevision'], admitted,
                                   timezone=payload.get('restartTimezone'))
      except PermissionError as error:
        raise SoftwareOperationError(str(error), 403) from error
      except ValueError as error:
        raise SoftwareOperationError(str(error), 409) from error
      return self.snapshot()

  def action(self, action: str, payload: object, *, authorized: Callable[[], bool]) -> dict:
    if (type(payload) is not dict or type(action) is not str or
        action not in ("check", "download", "select", "install", "preferences", "install-preferences", "update", "fast", "rollback",
                       "versions", "version", "download-version")):
      raise SoftwareOperationError("Invalid software action", 400)
    assert isinstance(payload, dict)
    payload = dict(payload)
    if payload.get("action") != action or any(type(key) is not str for key in payload):
      raise SoftwareOperationError("Invalid software action", 400)
    if action == 'preferences':
      return self._preferences(payload, authorized)
    if action == 'install-preferences':
      return self._install_preferences(payload, authorized)
    fields = set(payload) - {"action"}
    if action == "check":
      if fields:
        raise SoftwareOperationError("Invalid software action", 400)
      branch = None
    else:
      pinned_install = action == "install" and fields != {"branch"}
      required_fields = {"branch", "expectedCommit", "selectedCommit"} if action in ("version", "download-version") or pinned_install else {"branch"}
      if fields != required_fields or not self._valid_branch(payload.get("branch")):
        raise SoftwareOperationError("Invalid branch", 400)
      if pinned_install and any(type(payload[key]) is not str or not COMMIT_PATTERN.fullmatch(payload[key])
                                for key in ('expectedCommit', 'selectedCommit')):
        raise SoftwareOperationError("Invalid selected version", 400)
      branch = payload["branch"]
      assert isinstance(branch, str)
    with self.lock:
      if not authorized():
        raise SoftwareOperationError("Session expired", 403)
      metadata_action = action in ('check', 'versions')
      view = self.snapshot(include_history=action in ('version', 'download-version'))
      if not view["parked"]:
        raise SoftwareOperationError("Vehicle is not safely parked", 409)
      if metadata_action and not view['canCheck'] or not metadata_action and view["reason"] is not None:
        raise SoftwareOperationError(view["reason"], 503 if view["reason"] == "Updater status is unavailable" else 409)
      if action not in SOURCE_ACTIONS and branch is not None and branch not in view["availableBranches"]:
        raise SoftwareOperationError("Branch is not in the current updater list", 409)
      if (action not in SOURCE_ACTIONS and action not in ("check", "select") and
          (branch != view["selectedTarget"] or not view["canDownload"])):
        raise SoftwareOperationError("Selected updater target changed", 409)
      if action in SOURCE_ACTIONS:
        identity = self.git_identity(self.installed)
        if (identity is None or not self._valid_branch(identity[0]) or
            (action in ("update", "fast", "versions", "version", "download-version") and
             branch != identity[0] and branch not in view["availableBranches"]) or
            (action == "rollback" and (branch != identity[0] or not view["canRollback"]))):
          raise SoftwareOperationError("Installed checkout or update target is unavailable", 409)
      if action in ('version', 'download-version'):
        target = payload['selectedCommit']
        recent = view['history']['recent']
        selectable = [*recent['entries'], *(view['history']['installed'] if branch == identity[0] else [])]
        if (payload['expectedCommit'] != identity[1] or type(target) is not str or not COMMIT_PATTERN.fullmatch(target) or
            recent['branch'] != branch or not any(row['hash'] == target for row in selectable)):
          raise SoftwareOperationError('Selected version or installed revision changed; refresh and try again', 409)
      if action == "install" and not view["canInstall"]:
        raise SoftwareOperationError("Finalized update does not match selected branch", 409)
      if not authorized() or not self._parked():
        raise SoftwareOperationError("Session or parked state changed", 403)
      current = self.status.snapshot()
      if (not metadata_action and current["updater"]["state"] != "idle" or self._flag("DisableUpdates") is not False or
          self._flag("DoReboot") is not False or not self.process.available() or
          (action not in SOURCE_ACTIONS and branch is not None and branch not in self._branches())):
        raise SoftwareOperationError("Updater state changed", 409)
      if action == 'versions':
        self.selected_target = branch
      if action == "select":
        self.params.put("UpdaterTargetBranch", branch, block=True)
        self.selected_target = branch
        return self.snapshot()
      accepted_owner = None
      request_started = self.clock()
      if action == "install":
        status = self.status.snapshot()
        if not self._ready(status, branch) or self._flag("DoReboot") is not False:
          raise SoftwareOperationError("Update is no longer ready", 409)
        if not authorized() or not self._parked():
          raise SoftwareOperationError("Session or parked state changed", 403)
        if 'selectedCommit' in payload and (status['installed']['commit'] != payload['expectedCommit'] or
            self.git_identity(self.installed) != (status['installed']['branch'], payload['expectedCommit']) or
            self.git_identity(self.finalized) != (branch, payload['selectedCommit'])):
          raise SoftwareOperationError("Selected version changed; refresh before installing", 409)
        self.params.put_bool("DoReboot", True, block=True)
      else:
        status = self.status.snapshot()
        baseline = (status["updater"]["lastSuccessAt"], status["updater"]["lastFetchAt"],
                    status["updater"]["failedCount"])
        if not authorized() or not self._parked():
          raise SoftwareOperationError("Session or parked state changed", 403)
        if action in SOURCE_ACTIONS:
          latest_identity = self.git_identity(self.installed)
          if latest_identity is None or latest_identity != identity:
            raise SoftwareOperationError("Installed checkout changed while preparing the update; retry when it finishes", 409)
          if action in ('version', 'download-version') and identity[1] != payload['expectedCommit']:
            raise SoftwareOperationError('Installed revision changed before version request; refresh and try again', 409)
          self._fast_commit = identity[1]
        if action in SOURCE_ACTIONS:
          if action in ('version', 'download-version'):
            accepted_owner = self.process.send(action, branch=branch, commit=identity[1], selected_commit=payload['selectedCommit'])
          else:
            accepted_owner = self.process.send(action, branch=branch, commit=identity[1])
        else:
          accepted_owner = self.process.send(action)
      self.request = {"id": secrets.token_hex(8), "action": action,
                      "target": branch if branch is not None else status["updater"]["targetBranch"],
                      "state": "pending", "error": None}
      if action in ('version', 'download-version', 'install') and 'selectedCommit' in payload:
        self.request['selectedCommit'] = payload['selectedCommit']
      self._baseline = baseline if action != "install" else None
      self._request_owner = accepted_owner
      self._started = request_started
      return self.snapshot(include_history=not metadata_action)

  def close(self) -> None:
    if self.context is not None:
      self.context.close()
    self.process.close()
