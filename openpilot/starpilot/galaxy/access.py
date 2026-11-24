"""Local Galaxy credential owner. No transport or remote pairing is implied."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from collections.abc import Callable


class AccessStatus(StrEnum):
  UNCONFIGURED = "unconfigured"
  CONFIGURED_LOCAL = "configured_local"
  LEGACY_IMPORT_AVAILABLE = "legacy_import_available"
  UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AccessResult:
  status: AccessStatus
  detail: str = ""


class GalaxyAccessOwner:
  """Own one versioned verifier in a private directory; never serves credentials."""

  FILE = "access-v1.json"
  N, R, P = 1 << 14, 8, 1

  def __init__(self, root: Path, *, legacy_root: Path | None = None):
    self.root = root
    self.legacy_root = legacy_root

  def _legacy_available(self) -> bool:
    if self.legacy_root is None:
      return False
    paths = [self.legacy_root / name for name in ("glxyauth", "glxysession", "glxyslug")]
    def metadata(path: Path) -> os.stat_result | None:
      try:
        return path.lstat()
      except FileNotFoundError:
        return None

    entries = [metadata(path) for path in paths]
    present = [entry is not None for entry in entries]
    if any(present) and not all(present):
      raise ValueError("partial legacy credentials")
    if all(present) and any(not stat.S_ISREG(entry.st_mode) for entry in entries if entry is not None):
      raise ValueError("unsafe legacy credentials")
    return all(present)

  def legacy_available(self) -> bool | None:
    try:
      return self._legacy_available()
    except (OSError, ValueError):
      return None

  def _read(self) -> dict[str, object] | None:
    try:
      directory = self.root.lstat()
    except FileNotFoundError:
      return None
    if not stat.S_ISDIR(directory.st_mode) or directory.st_mode & 0o077:
      raise ValueError("credential directory is unsafe")
    path = self.root / self.FILE
    try:
      info = path.lstat()
    except FileNotFoundError:
      if any(entry.name.startswith(".access-") for entry in self.root.iterdir()):
        raise ValueError("incomplete credential write") from None
      return None
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 4096:
      raise ValueError("credential file is unsafe")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
      opened = os.fstat(stream.fileno())
      if not stat.S_ISREG(opened.st_mode) or opened.st_mode & 0o077 or opened.st_size > 4096:
        raise ValueError("credential file changed")
      record = json.load(stream)
    if not isinstance(record, dict) or set(record) != {"version", "algorithm", "salt", "verifier"} or \
       record["version"] != 1 or record["algorithm"] != "scrypt-n16384-r8-p1":
      raise ValueError("unsupported credential record")
    if not all(isinstance(record[key], str) and re.fullmatch(f"[0-9a-f]{{{size}}}", record[key])
               for key, size in (("salt", 32), ("verifier", 64))):
      raise ValueError("invalid credential record")
    return record

  def status(self) -> AccessResult:
    try:
      record = self._read()
      if record is not None:
        return AccessResult(AccessStatus.CONFIGURED_LOCAL)
      if self._legacy_available():
        return AccessResult(AccessStatus.LEGACY_IMPORT_AVAILABLE)
      return AccessResult(AccessStatus.UNCONFIGURED)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
      return AccessResult(AccessStatus.UNAVAILABLE, "Local credential state cannot be read")

  def verify(self, password: str) -> bool:
    return self.authenticate_generation(password) is not None

  @staticmethod
  def _generation(record: dict[str, object]) -> bytes:
    # Internal session binding only; never send this digest to a browser.
    return hashlib.sha256(bytes.fromhex(str(record["salt"])) + bytes.fromhex(str(record["verifier"]))).digest()

  def current_generation(self) -> bytes | None:
    try:
      record = self._read()
      if record is None:
        return None
      return self._generation(record)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
      return None

  def authenticate_generation(self, password: str) -> bytes | None:
    try:
      record = self._read()
      if record is None:
        return None
      derived = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(str(record["salt"])),
                               n=self.N, r=self.R, p=self.P, dklen=32)
      return self._generation(record) if hmac.compare_digest(derived, bytes.fromhex(str(record["verifier"]))) else None
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
      return None

  def _write(self, password: str, parked: Callable[[], bool], replace_generation: bytes | None = None) -> bool:
    salt = secrets.token_bytes(16)
    verifier = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=self.N, r=self.R, p=self.P, dklen=32)
    record = {"version": 1, "algorithm": "scrypt-n16384-r8-p1", "salt": salt.hex(), "verifier": verifier.hex()}
    if not parked():
      return False
    self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat.S_ISDIR(self.root.lstat().st_mode) or self.root.stat().st_mode & 0o077:
      return False
    fd, temporary = tempfile.mkstemp(prefix=".access-", dir=self.root)
    try:
      os.fchmod(fd, 0o600)
      with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(record, stream, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
      if not parked():
        return False
      if replace_generation is None:
        # A second writer cannot silently replace a record created after status().
        os.link(temporary, self.root / self.FILE)
      else:
        if self.current_generation() != replace_generation:
          return False
        os.replace(temporary, self.root / self.FILE)
      directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
      try:
        os.fsync(directory_fd)
      finally:
        os.close(directory_fd)
      return True
    finally:
      if os.path.exists(temporary):
        os.unlink(temporary)

  def configure(self, password: str, parked: Callable[[], bool]) -> bool:
    if not 8 <= len(password) <= 255 or not parked() or self.status().status != AccessStatus.UNCONFIGURED:
      return False
    try:
      return self._write(password, parked)
    except (OSError, ValueError, UnicodeError):
      return False

  def replace_for_pairing(self, password: str, parked: Callable[[], bool]) -> bool:
    """Set a new remote login password from an authorized, parked local pairing flow."""
    if not 8 <= len(password) <= 255 or not parked():
      return False
    status = self.status().status
    if status not in (AccessStatus.CONFIGURED_LOCAL, AccessStatus.LEGACY_IMPORT_AVAILABLE):
      return False
    generation = self.current_generation() if status == AccessStatus.CONFIGURED_LOCAL else None
    if status == AccessStatus.CONFIGURED_LOCAL and generation is None:
      return False
    try:
      return self._write(password, parked, replace_generation=generation)
    except (OSError, ValueError, UnicodeError):
      return False

  def snapshot_for_pairing(self) -> dict[str, object] | None:
    return self._read()

  def restore_failed_pairing(self, previous: dict[str, object] | None, changed_generation: bytes) -> bool:
    if self.current_generation() != changed_generation:
      return False
    path = self.root / self.FILE
    if previous is None:
      try:
        path.unlink()
        directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
          os.fsync(directory_fd)
        finally:
          os.close(directory_fd)
        return True
      except OSError:
        return False
    try:
      fd, temporary = tempfile.mkstemp(prefix=".access-", dir=self.root)
      try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
          json.dump(previous, stream, separators=(",", ":"))
          stream.flush()
          os.fsync(stream.fileno())
        if self.current_generation() != changed_generation:
          return False
        os.replace(temporary, path)
        directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
          os.fsync(directory_fd)
        finally:
          os.close(directory_fd)
        return True
      finally:
        if os.path.exists(temporary):
          os.unlink(temporary)
    except (OSError, ValueError, UnicodeError):
      return False

  def import_legacy(self, password: str, parked: Callable[[], bool]) -> bool:
    """Explicit migration needs the user's password; old SHA256 is never reused as a verifier."""
    status = self.status().status
    if not 6 <= len(password) <= 255 or not parked() or status not in \
       (AccessStatus.LEGACY_IMPORT_AVAILABLE, AccessStatus.CONFIGURED_LOCAL):
      return False
    try:
      if not self._legacy_available():
        return False
      assert self.legacy_root is not None
      fd = os.open(self.legacy_root / "glxyauth", os.O_RDONLY | os.O_NOFOLLOW)
      with os.fdopen(fd, "r", encoding="ascii") as stream:
        if os.fstat(stream.fileno()).st_size > 128:
          return False
        old_hash = stream.read().strip()
      if len(old_hash) != 64 or not re.fullmatch(r"[0-9a-f]{64}", old_hash):
        return False
      raw_matches = hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), old_hash)
      stripped_matches = hmac.compare_digest(hashlib.sha256(password.strip().encode()).hexdigest(), old_hash)
      if not (raw_matches or stripped_matches):
        return False
      generation = self.current_generation() if status == AccessStatus.CONFIGURED_LOCAL else None
      if status == AccessStatus.CONFIGURED_LOCAL and generation is None:
        return False
      return self._write(password if raw_matches else password.strip(), parked, replace_generation=generation)
    except (OSError, ValueError, UnicodeError):
      return False

  def remove(self, parked: Callable[[], bool]) -> bool:
    if not parked() or self.status().status != AccessStatus.CONFIGURED_LOCAL:
      return False
    try:
      if not parked():
        return False
      (self.root / self.FILE).unlink()
      return True
    except OSError:
      return False


def legacy_galaxy_root() -> Path | None:
  from openpilot.common.hardware import PC
  from openpilot.common.hardware.hw import Paths

  if os.environ.get("OPENPILOT_PREFIX"):
    return None
  if PC:
    return Path(Paths.comma_home()) / "starpilot/data/galaxy"
  return Path("/data/galaxy")


def default_owner() -> GalaxyAccessOwner:
  """Share the native UI credential location without importing UI modules."""
  from openpilot.starpilot.storage import galaxy_storage_root
  return GalaxyAccessOwner(galaxy_storage_root(), legacy_root=legacy_galaxy_root())
