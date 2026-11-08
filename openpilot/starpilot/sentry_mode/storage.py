"""Bounded durable Sentry events and captured camera evidence."""

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from openpilot.starpilot.storage import starpilot_storage_root
from openpilot.starpilot.stored_artifacts import DeletionLimit, remove_entry, usage


MAX_EVENTS = 512
MAX_EVENT_BYTES = 512
ID = re.compile(r"[0-9a-f]{32}\Z")
FIELDS = {"version", "eventId", "sessionId", "kind", "monoTimeNs", "wallTimeNs"}
BATTERY_FIELDS = {"voltage", "cutoff"}
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class StorageUnavailable(Exception):
  """No event was published by this call; recording cannot continue."""


@dataclass(frozen=True)
class RecordReceipt:
  event_id: str
  # False means published but persistence across power loss was not confirmed.
  durable: bool


def _private(info: os.stat_result, *, directory: bool = False) -> bool:
  return (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1) and \
    info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0


def _valid(value: object, event_id: str) -> bool:
  if type(value) is not dict:
    return False
  if any(type(key) is not str for key in value):
    return False
  fields = {key: item for key, item in value.items() if type(key) is str}
  battery = fields.get("kind") == "battery"
  base = FIELDS | BATTERY_FIELDS if battery else FIELDS
  if set(fields) not in (base, base | {"simulated"}) or ("simulated" in fields and fields["simulated"] is not True) or ID.fullmatch(event_id) is None:
    return False
  if battery and not all(type(fields[key]) is int and 0 < fields[key] < 1000 for key in BATTERY_FIELDS):
    return False
  version = fields["version"]
  session_id = fields["sessionId"]
  mono_time = fields["monoTimeNs"]
  wall_time = fields["wallTimeNs"]
  return (type(version) is int and version == 1 and
          fields["eventId"] == event_id and
          type(session_id) is str and ID.fullmatch(session_id) is not None and
          fields["kind"] in ("warning", "alarm", "selfie", "battery") and
          type(mono_time) is int and 0 <= mono_time < 2**63 and
          type(wall_time) is int and 0 <= wall_time < 2**63)


def _unique(pairs: list[tuple[str, object]]) -> dict:
  result: dict = {}
  for key, value in pairs:
    if key in result:
      raise ValueError("duplicate event field")
    result[key] = value
  return result


class EventStore:
  def __init__(self, root: Path | None = None):
    self.root = root if root is not None else starpilot_storage_root() / "sentry/events"
    self.session_id = uuid.uuid4().hex

  def _open_directory(self, *, create: bool = False) -> int:
    # Traverse from an already-open filesystem root. mkdir(parents=True) would
    # follow a replaced ancestor before the final O_NOFOLLOW check.
    if not self.root.is_absolute() or ".." in self.root.parts:
      raise StorageUnavailable("Event directory path is unavailable")
    fd = os.open("/", DIR_FLAGS)
    try:
      for component in self.root.parts[1:]:
        if create:
          try:
            os.mkdir(component, mode=0o700, dir_fd=fd)
          except FileExistsError:
            pass
        child = os.open(component, DIR_FLAGS, dir_fd=fd)
        os.close(fd)
        fd = child
      return fd
    except BaseException:
      os.close(fd)
      raise

  def _same_directory(self, fd: int) -> bool:
    try:
      current = self._open_directory()
      try:
        opened_info, current_info = os.fstat(fd), os.fstat(current)
        return (opened_info.st_dev, opened_info.st_ino) == (current_info.st_dev, current_info.st_ino)
      finally:
        os.close(current)
    except OSError:
      return False

  @contextmanager
  def _directory(self, *, create: bool = False):
    fd = self._open_directory(create=create)
    try:
      info = os.fstat(fd)
      if not _private(info, directory=True) or not self._same_directory(fd):
        raise StorageUnavailable("Event directory is unavailable")
      yield fd
    finally:
      os.close(fd)

  def record(self, kind: str, mono_time_ns: int, *, permitted: Callable[[], bool], images: dict | None = None, simulated: bool = False,
             details: dict | None = None) -> RecordReceipt:
    event_id = uuid.uuid4().hex
    value = {"version": 1, "eventId": event_id, "sessionId": self.session_id, "kind": kind,
             "monoTimeNs": mono_time_ns, "wallTimeNs": time.time_ns(), **(details or {})}
    if simulated:
      value["simulated"] = True
    if not _valid(value, event_id):
      raise ValueError("Invalid Sentry event")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_EVENT_BYTES:
      raise ValueError("Sentry event exceeds its storage limit")
    if not permitted():
      raise StorageUnavailable("Sentry is no longer armed")
    published = False
    try:
      with self._directory(create=True) as directory:
        lock = os.open(".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
          if not _private(os.fstat(lock)):
            raise StorageUnavailable("Event lock is unavailable")
          fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
          # Unknown entries and interrupted writes also consume capacity. Never
          # delete evidence, or scan an unbounded directory to admit a new event.
          count = 0
          with os.scandir(directory) as entries:
            for entry in entries:
              if ID.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False):
                try:
                  metadata = os.stat(entry.name + ".json", dir_fd=directory, follow_symlinks=False)
                  if _private(metadata):
                    continue  # Evidence directory and published metadata are one event.
                except OSError:
                  pass  # Interrupted evidence still consumes capacity.
              if entry.name != ".lock":
                count += 1
                if count >= MAX_EVENTS:
                  raise StorageUnavailable("Event storage is full")
          for camera, body in (images or {}).items():
            if (camera not in ("wide", "cabin") or not isinstance(body, bytes) or not 4 <= len(body) <= 1_000_000 or
                not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9")):
              raise ValueError("Invalid event image")
          if images:
            os.mkdir(event_id, mode=0o700, dir_fd=directory)
            image_directory = os.open(event_id, DIR_FLAGS, dir_fd=directory)
            try:
              for camera, body in images.items():
                fd = os.open(camera + ".jpg", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=image_directory)
                with os.fdopen(fd, "wb") as output:
                  output.write(body)
                  output.flush()
                  os.fsync(output.fileno())
              os.fsync(image_directory)
            finally:
              os.close(image_directory)
          temporary = f".{event_id}.tmp"
          fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
          try:
            with os.fdopen(fd, "wb") as output:
              output.write(raw)
              output.flush()
              os.fsync(output.fileno())
            if not self._same_directory(directory) or not permitted():
              raise StorageUnavailable("Sentry authority or event directory changed")
            # An atomic no-replace publication: a coincident ID cannot overwrite
            # an existing event, including an unexpected symlink.
            os.link(temporary, event_id + ".json", src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            published = True
          finally:
            os.unlink(temporary, dir_fd=directory)
          os.fsync(directory)
        finally:
          os.close(lock)
    except (OSError, StorageUnavailable) as error:
      if published:
        return RecordReceipt(event_id, durable=False)
      raise StorageUnavailable(str(error)) from error
    return RecordReceipt(event_id, durable=True)

  def delete(self, event_id=None, *, permitted=lambda: True):
    if event_id is not None and (type(event_id) is not str or ID.fullmatch(event_id) is None):
      raise ValueError('Invalid event selection')
    if not permitted():
      raise PermissionError('Galaxy session expired')
    more = False
    try:
      with self._directory() as directory:
        lock = os.open('.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
          if not _private(os.fstat(lock)):
            raise StorageUnavailable('Event lock is unavailable')
          fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
          if not permitted():
            raise PermissionError('Galaxy session expired')
          budget = [4096]
          if event_id is None:
            with os.scandir(directory) as entries:
              for entry in entries:
                if entry.name != '.lock':
                  try:
                    remove_entry(directory, entry.name, budget)
                  except DeletionLimit:
                    more = True
                    break
          else:
            for name in (event_id, '.' + event_id + '.tmp', event_id + '.json'):
              remove_entry(directory, name, budget)
          if not self._same_directory(directory):
            raise StorageUnavailable('Event directory changed')
          os.fsync(directory)
        finally:
          os.close(lock)
    except FileNotFoundError:
      pass
    except (OSError, DeletionLimit) as error:
      raise StorageUnavailable('Event cleanup could not be completed') from error
    return {'more': more}

  def snapshot(self) -> dict:
    result: dict = {"version": 1, "events": [], "incomplete": False, "capacity": MAX_EVENTS, "storageBytes": 0, "storageIncomplete": False}
    try:
      with self._directory() as directory:
        with os.scandir(directory) as entries:
          count = 0
          for entry in entries:
            if entry.name == ".lock" or (ID.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False)):
              continue
            count += 1
            if count > MAX_EVENTS:
              result["incomplete"] = True
              break
            event_id = entry.name.removesuffix(".json")
            if entry.name != event_id + ".json" or ID.fullmatch(event_id) is None:
              result["incomplete"] = True
              continue
            try:
              fd = os.open(entry.name, FILE_FLAGS, dir_fd=directory)
              try:
                if not _private(os.fstat(fd)):
                  raise ValueError("Invalid event file")
                raw = os.read(fd, MAX_EVENT_BYTES + 1)
              finally:
                os.close(fd)
              if len(raw) > MAX_EVENT_BYTES:
                raise ValueError("Event too large")
              value = json.loads(raw, object_pairs_hook=_unique)
              if not _valid(value, event_id):
                raise ValueError("Invalid event metadata")
              if value.get('simulated') and time.time_ns() - value['wallTimeNs'] > 600_000_000_000:
                os.unlink(entry.name, dir_fd=directory)
                continue
              result["events"].append(value)
            except (OSError, ValueError, TypeError, RecursionError):
              result["incomplete"] = True
        result["storageBytes"], result["storageIncomplete"] = usage(directory)
        if not self._same_directory(directory):
          raise StorageUnavailable("Event directory changed")
    except FileNotFoundError:
      return result
    except OSError as error:
      raise StorageUnavailable("Event storage unavailable") from error
    result["events"].sort(key=lambda event: (event["wallTimeNs"], event["eventId"]), reverse=True)
    return result

  def image(self, event_id: str, camera: str) -> bytes:
    if ID.fullmatch(event_id) is None or camera not in ("wide", "cabin"):
      raise StorageUnavailable("Unknown event image")
    with self._directory() as directory:
      # Only published events can expose images.
      event_fd = os.open(event_id + ".json", FILE_FLAGS, dir_fd=directory)
      try:
        if not _private(os.fstat(event_fd)):
          raise StorageUnavailable("Invalid event")
      finally:
        os.close(event_fd)
      images = os.open(event_id, DIR_FLAGS, dir_fd=directory)
      try:
        if not _private(os.fstat(images), directory=True):
          raise StorageUnavailable("Invalid image directory")
        fd = os.open(camera + ".jpg", FILE_FLAGS, dir_fd=images)
        try:
          if not _private(os.fstat(fd)):
            raise StorageUnavailable("Invalid image")
          body = os.read(fd, 1_000_001)
        finally:
          os.close(fd)
      finally:
        os.close(images)
    if not 4 <= len(body) <= 1_000_000 or not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9"):
      raise StorageUnavailable("Invalid image")
    return body

  def images(self, event_id: str) -> list[str]:
    if ID.fullmatch(event_id) is None:
      return []
    try:
      with self._directory() as directory:
        images = os.open(event_id, DIR_FLAGS, dir_fd=directory)
        try:
          if not _private(os.fstat(images), directory=True):
            return []
          result = []
          for camera in ("wide", "cabin"):
            try:
              info = os.stat(camera + ".jpg", dir_fd=images, follow_symlinks=False)
              if _private(info) and 4 <= info.st_size <= 1_000_000:
                result.append(camera)
            except OSError:
              pass
          return result
        finally:
          os.close(images)
    except (OSError, StorageUnavailable):
      return []
