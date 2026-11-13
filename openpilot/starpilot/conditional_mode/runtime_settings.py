"""Read-only, bounded-refresh owner for saved conditional-mode preferences.

The caller supplies monotonic observation time and invokes refresh off the
control-frame cadence. A current owner revision must be affirmed each sample;
neither this owner nor the codec writes Params or adopts legacy keys.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import fcntl
import os
from pathlib import Path
import secrets
from typing import Any

from openpilot.starpilot.conditional_mode.preferences import (
  MAX_DOCUMENT_BYTES, CCMOptions, CEMOptions, ModeSelection, PreferenceError,
  SavedPreferences, decode_preferences, saved_selection_for_drive,
)
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.conditional_mode.policy import ModeChoice


DOCUMENT_KEY = 'ConditionalModeConfig'
SAFE_MODE_KEY = 'SafeMode'
EXPERIMENTAL_KEY = 'ExperimentalMode'
REFRESH_NS = 1_000_000_000
REVERIFY_NS = REFRESH_NS // 2


class DocumentState(StrEnum):
  ABSENT = 'absent'
  VALID = 'valid'
  INVALID = 'invalid'
  READ_ERROR = 'read_error'


class SafeModeState(StrEnum):
  ABSENT_FALSE = 'absent_false'
  FALSE = 'false'
  TRUE = 'true'
  INVALID = 'invalid'
  READ_ERROR = 'read_error'


@dataclass(frozen=True)
class SettingsSnapshot:
  owner_token: str
  revision: int
  observed_mono_ns: int
  verified_mono_ns: int
  document_state: DocumentState
  safe_mode_state: SafeModeState
  document_raw: bytes | None
  safe_mode_raw: bytes | None
  preferences: SavedPreferences | None
  experimental_raw: bytes | None = None
  experimental_readable: bool = True


@dataclass(frozen=True)
class SettingsVerdict:
  status: str
  revision: int | None
  observed_mono_ns: int | None
  selection: ModeSelection | None
  cem: CEMOptions | None
  ccm: CCMOptions | None
  safe_mode: bool | None


def _read(params: Any, key: str, limit: int) -> tuple[bytes | None, bool]:
  try:
    return read_saved(params, key, limit)
  except (OSError, TypeError, ValueError):
    return b'', False



def read_mode_sources(params: Any, *, wait: bool = False, defer_busy: bool = False) -> tuple[
  tuple[bytes | None, bool], tuple[bytes | None, bool], tuple[bytes | None, bool]
]:
  """Read one coherent source pair. Only background callers may wait for a writer."""
  lock_fd = None
  try:
    root = Path(params.get_param_path(DOCUMENT_KEY)).parent.parent
    lock_fd = os.open(root / '.lock', os.O_CREAT | os.O_RDONLY, 0o775)
    fcntl.flock(lock_fd, fcntl.LOCK_SH if wait else fcntl.LOCK_SH | fcntl.LOCK_NB)
    return (_read(params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES),
            _read(params, EXPERIMENTAL_KEY, 8), _read(params, SAFE_MODE_KEY, 8))
  except BlockingIOError:
    if defer_busy:
      raise
    return (b'', False), (b'', False), (b'', False)
  except (AttributeError, OSError, TypeError, ValueError):
    return (b'', False), (b'', False), (b'', False)
  finally:
    if lock_fd is not None:
      os.close(lock_fd)


def experimental_available(cp: Any) -> bool:
  return bool(cp is not None and getattr(cp, 'openpilotLongitudinalControl', None) is True and
              not getattr(cp, 'passive', False) and not getattr(cp, 'dashcamOnly', False) and not getattr(cp, 'notCar', False))


def experimental_requested(raw: bytes | None, readable: bool, safe_raw: bytes | None, safe_readable: bool, cp: Any) -> bool:
  return bool(readable and raw == b'1' and safe_readable and safe_raw in (None, b'0') and experimental_available(cp))

def _document(raw: bytes | None, readable: bool) -> tuple[DocumentState, SavedPreferences | None]:
  if not readable:
    return DocumentState.READ_ERROR, None
  if raw is None:
    return DocumentState.ABSENT, SavedPreferences()
  try:
    preferences = decode_preferences(raw)
    if preferences.mode is ModeChoice.CCM:
      preferences = replace(preferences, mode=ModeChoice.CEM)
    return DocumentState.VALID, preferences
  except PreferenceError:
    return DocumentState.INVALID, None


def _safe_mode(raw: bytes | None, readable: bool) -> SafeModeState:
  if not readable:
    return SafeModeState.READ_ERROR
  if raw is None:
    return SafeModeState.ABSENT_FALSE
  if raw == b'0':
    return SafeModeState.FALSE
  if raw == b'1':
    return SafeModeState.TRUE
  return SafeModeState.INVALID


class ConditionalSettingsOwner:
  """One in-memory revision, reread before its one-second authority expires."""

  def __init__(self, params: Any):
    self.params = params
    self.owner_token = secrets.token_hex(16)
    self.current: SettingsSnapshot | None = None
    self.last_refresh_ns: int | None = None

  def _unavailable(self, now_mono_ns: int) -> SettingsSnapshot:
    previous = self.current
    snapshot = SettingsSnapshot(
      self.owner_token, 1 if previous is None else previous.revision + 1,
      now_mono_ns, now_mono_ns, DocumentState.READ_ERROR, SafeModeState.READ_ERROR,
      None, None, None, None, False,
    )
    self.current = snapshot
    self.last_refresh_ns = now_mono_ns
    return snapshot

  def refresh(self, now_mono_ns: int, *, force: bool = False, wait: bool = False) -> SettingsSnapshot:
    """Perform a bounded disk read when due; errors invalidate prior authority."""
    if type(now_mono_ns) is not int or now_mono_ns <= 0:
      return self._unavailable(0)
    if self.last_refresh_ns is not None and now_mono_ns < self.last_refresh_ns:
      return self._unavailable(now_mono_ns)
    if not force and self.current is not None and self.last_refresh_ns is not None and now_mono_ns - self.last_refresh_ns < REVERIFY_NS:
      return self.current

    try:
      sources = read_mode_sources(self.params, wait=wait, defer_busy=True)
    except BlockingIOError:
      # A paired write is not corrupt data. Keep the accepted revision while its
      # original authority is live; neither read unlocked bytes nor renew its age.
      return self.current if self.current is not None else self._unavailable(now_mono_ns)
    (document_raw, document_readable), (experimental_raw, experimental_readable), (safe_raw, safe_readable) = sources
    document_state, preferences = _document(document_raw, document_readable)
    safe_state = _safe_mode(safe_raw, safe_readable)
    previous = self.current
    same_source = bool(previous is not None and previous.document_raw == document_raw and
                       previous.safe_mode_raw == safe_raw and previous.document_state is document_state and
                       previous.safe_mode_state is safe_state and previous.preferences == preferences and
                       previous.experimental_raw == experimental_raw and previous.experimental_readable == experimental_readable)
    snapshot = SettingsSnapshot(
      self.owner_token,
      previous.revision if same_source and previous is not None else (1 if previous is None else previous.revision + 1),
      previous.observed_mono_ns if same_source and previous is not None else now_mono_ns,
      now_mono_ns,
      document_state, safe_state, document_raw, safe_raw, preferences, experimental_raw, experimental_readable,
    )
    self.current = snapshot
    self.last_refresh_ns = now_mono_ns
    return snapshot

  def affirm(self, snapshot: SettingsSnapshot | None, *, now_mono_ns: int) -> bool:
    """Validate the current owner revision without another Params read."""
    current = self.current
    verified = (current.verified_mono_ns if current is not None and type(now_mono_ns) is int and
                current.verified_mono_ns <= now_mono_ns else
                snapshot.verified_mono_ns if type(snapshot) is SettingsSnapshot else 0)
    return bool(
      type(snapshot) is SettingsSnapshot and current is not None and
      snapshot.owner_token == self.owner_token and snapshot.revision == current.revision and
      snapshot.observed_mono_ns == current.observed_mono_ns and
      current.document_state in (DocumentState.ABSENT, DocumentState.VALID) and
      current.safe_mode_state in (SafeModeState.ABSENT_FALSE, SafeModeState.FALSE) and
      snapshot.verified_mono_ns <= current.verified_mono_ns and
      (snapshot is current or replace(snapshot, verified_mono_ns=current.verified_mono_ns) == current) and
      type(now_mono_ns) is int and verified <= now_mono_ns <= verified + REFRESH_NS
    )

  def verdict(self, snapshot: SettingsSnapshot | None, *, now_mono_ns: int, drive_id: int) -> SettingsVerdict:
    """Supply saved options only while this exact owner revision is live."""
    current = self.current
    safe = (False if current is not None and current.safe_mode_state in (SafeModeState.ABSENT_FALSE, SafeModeState.FALSE)
            else True if current is not None and current.safe_mode_state is SafeModeState.TRUE else None)
    if not self.affirm(snapshot, now_mono_ns=now_mono_ns):
      status = ('read_error' if current is not None and (current.document_state is DocumentState.READ_ERROR or
                                                         current.safe_mode_state is SafeModeState.READ_ERROR) else
                'unavailable_document' if current is not None and current.document_state is DocumentState.INVALID else
                'invalid_safe_mode' if current is not None and current.safe_mode_state is SafeModeState.INVALID else
                'safe_mode' if safe is True else
                'stale_or_foreign_revision')
      return SettingsVerdict(status, current.revision if current is not None else None,
                             current.observed_mono_ns if current is not None else None,
                             None, None, None, safe)
    assert snapshot is not None and snapshot.preferences is not None
    selection = self._selection_for_drive(snapshot, drive_id)
    return SettingsVerdict('ready' if selection is not None else 'invalid_drive', snapshot.revision,
                           snapshot.observed_mono_ns, selection, snapshot.preferences.cem,
                           snapshot.preferences.ccm, False)

  def _selection_for_drive(self, snapshot: SettingsSnapshot, drive_id: int) -> ModeSelection | None:
    assert snapshot.preferences is not None
    return saved_selection_for_drive(snapshot.preferences, drive_id)
