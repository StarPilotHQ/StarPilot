"""Expiring, namespace-bound motion-owner status; never grants runtime authority."""

import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from openpilot.common.hardware.hw import Paths
from openpilot.starpilot.parked_evidence import RESUME_SKEW_NS
from openpilot.starpilot.sentry_mode.preferences import SavedPreferences
from openpilot.starpilot.sentry_mode.storage import EventStore, FILE_FLAGS, StorageUnavailable, _private, valid_tenths, volts
from openpilot.starpilot.storage import starpilot_storage_root


def boot_clock():
  return time.clock_gettime_ns(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))


TTL_NS = 1_500_000_000
STATES = {
  "disabled": ("Off", "Motion monitoring is disabled"),
  "disabled_onroad": ("Not armed", "The car is on or the device is shutting down"),
  "disabled_ignition": ("Not armed", "Waiting for confirmation that the ignition is off"),
  "low_voltage": ("Not armed", "Battery is low or its charge cannot be checked"),
  "unavailable": ("Waiting", "Checking that the car is parked"),
  "sensor_unavailable": ("Waiting for motion sensor", "No recent sensor readings"),
  "arming": ("Arming", "Monitoring begins after 90 seconds parked"),
  "armed": ("Monitoring motion", "Watching for movement while parked"),
}


def boot_id() -> str:
  try:
    return str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
  except (OSError, ValueError):
    return ""


def low_voltage_reason(voltage, cutoff, fallback: str) -> str:
  if not valid_tenths(voltage):
    return "Battery voltage cannot be read"
  if not valid_tenths(cutoff):
    return f"Battery is {volts(voltage)}; the low-battery cutoff cannot be read"
  if voltage <= cutoff:
    return f"Battery is {volts(voltage)}; needs above {volts(cutoff)}"
  return f"{fallback} (battery {volts(voltage)})"


class RuntimeStatus(EventStore):
  def __init__(self, root: Path | None = None, *, clock=time.monotonic_ns, boot=boot_id, boot_clock=boot_clock):
    namespace = hashlib.sha256(str(starpilot_storage_root()).encode()).hexdigest()[:24]
    super().__init__(root if root is not None else Path(Paths.shm_path()) / f"starpilot-sentry-{namespace}")
    self.clock, self.boot, self.boot_clock = clock, boot, boot_clock
    self.last_published = None
    self.last_value = None

  def publish(self, decision, source: bytes | None, recording: str, power: tuple[int | None, int | None] = (None, None)) -> None:
    identity = self.boot()
    if not identity or decision.state not in STATES:
      return
    current = (decision, source, recording, power)
    now = self.clock()
    if current == self.last_value and self.last_published is not None and 0 <= now - self.last_published < 500_000_000:
      return
    value = {"state": decision.state, "remaining": decision.seconds_remaining,
             "source": hashlib.sha256(source or b"").hexdigest(), "boot": identity,
             "stamp": now, "bootStamp": self.boot_clock(), "recording": recording,
             "voltage": power[0], "cutoff": power[1]}
    raw = json.dumps(value, separators=(",", ":")).encode()
    temporary = f".{uuid.uuid4().hex}"
    try:
      with self._directory(create=True) as directory:
        try:
          fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
          with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
          os.replace(temporary, "status.json", src_dir_fd=directory, dst_dir_fd=directory)
          self.last_published, self.last_value = now, current
        finally:
          try:
            os.unlink(temporary, dir_fd=directory)
          except FileNotFoundError:
            pass
    except (OSError, StorageUnavailable):
      pass

  def runtime_snapshot(self, saved: SavedPreferences) -> tuple[str, str]:
    if not saved.readable or not saved.valid:
      return "Unavailable", "Repair the saved motion settings before monitoring"
    if not saved.preferences.enabled:
      return STATES["disabled"]
    try:
      with self._directory() as directory:
        fd = os.open("status.json", FILE_FLAGS, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
          if not _private(os.fstat(stream.fileno())):
            raise ValueError("Unsafe status")
          raw = stream.read(1025)
      if len(raw) > 1024:
        raise ValueError("Oversized status")
      value = json.loads(raw)
      now = self.clock()
      boot_now = self.boot_clock()
      if (type(value) is not dict or type(value.get("stamp")) is not int or
          type(value.get("bootStamp")) is not int or
          not 0 <= boot_now - value["bootStamp"] <= TTL_NS or
          abs((boot_now - value["bootStamp"]) - (now - value["stamp"])) > RESUME_SKEW_NS or
          not 0 <= now - value["stamp"] <= TTL_NS or not self.boot() or value.get("boot") != self.boot() or
          value.get("source") != hashlib.sha256(saved.raw or b"").hexdigest() or
          value.get("state") not in STATES):
        raise ValueError("Stale status")
      label, reason = STATES[value["state"]]
      if value["state"] == "arming":
        remaining = value.get("remaining")
        if type(remaining) is not int or not 0 <= remaining <= 90:
          raise ValueError("Invalid countdown")
        label = f"Arming · {remaining}s"
      if value["state"] == "low_voltage":
        reason = low_voltage_reason(value.get("voltage"), value.get("cutoff"), reason)
      if value.get("recording") in ("unavailable", "durability_unknown"):
        reason += "; motion events could not be saved reliably"
      return label, reason
    except (OSError, StorageUnavailable, ValueError, TypeError, KeyError, RecursionError):
      return "Waiting for monitor", "The monitor has not responded recently"
