"""Pure fresh offroad evidence shared by local saved-setting action owners.

Python deviceState stamps MONOTONIC; C++ pandad stamps BOOTTIME on Linux.
"""

from dataclasses import dataclass
import os


DEVICE_TTL_NS = 1_500_000_000  # 2 Hz publisher, allowing one missed frame.
PANDA_TTL_NS = 300_000_000    # 10 Hz publisher, allowing one missed frame.
RESUME_SKEW_NS = 5_000_000


def host_parked(environ=os.environ) -> bool:
  """./dev galaxy, ./c3 and ./c4 have no car or drive-state publishers; they are always parked.

  Only those launchers set SP_HOST_PARKED. Route replay (./onroad) keeps its recorded drive state.
  """
  prefix = environ.get("OPENPILOT_PREFIX", "")
  return (environ.get("SP_HOST_PARKED") == "1" and environ.get("SP_HOST_RUNTIME") == "1" and
          bool(prefix) and prefix == environ.get("SP_HOST_PREFIX"))


@dataclass(frozen=True)
class ParkedEvidence:
  manager_offroad: bool
  device_seen: bool
  device_alive: bool
  device_valid: bool
  device_started: bool
  device_stamp_mono_ns: int
  device_recv_mono_ns: int
  device_boot_minus_mono_at_recv_ns: int | None
  device_after_mono_ns: int  # Collector must set a startup/resume barrier before receiving evidence.
  panda_seen: bool
  panda_alive: bool
  panda_valid: bool
  panda_stamp_boot_ns: int
  panda_recv_mono_ns: int
  panda_ignition: tuple[bool, ...]


def fresh_offroad(evidence: ParkedEvidence, *, now_mono_ns: int, now_boot_ns: int, allow_bench: bool = False) -> bool:
  """Confirm effective offroad mode, including Force Offroad with ignition on."""
  e = evidence
  if (not e.manager_offroad or not all((e.device_seen, e.device_alive, e.device_valid)) or
      e.device_started or e.device_boot_minus_mono_at_recv_ns is None or
      min(e.device_stamp_mono_ns, e.device_recv_mono_ns) <= 0 or
      e.device_stamp_mono_ns <= e.device_after_mono_ns):
    return False
  device_mono_age = now_mono_ns - e.device_stamp_mono_ns
  device_boot_age = now_boot_ns - (e.device_stamp_mono_ns + e.device_boot_minus_mono_at_recv_ns)
  panda_boot_age = now_boot_ns - e.panda_stamp_boot_ns
  device_receipt_age = now_mono_ns - e.device_recv_mono_ns
  panda_receipt_age = now_mono_ns - e.panda_recv_mono_ns
  fresh_panda = (e.panda_seen and e.panda_alive and e.panda_valid and min(e.panda_stamp_boot_ns, e.panda_recv_mono_ns) > 0 and
                 0 <= panda_boot_age <= PANDA_TTL_NS and 0 <= panda_receipt_age <= PANDA_TTL_NS)
  has_panda = bool(e.panda_ignition)
  # A stale Panda report is not evidence that the device is on a bench.
  bench = allow_bench and not has_panda and (not e.panda_seen or fresh_panda)
  return (0 <= device_mono_age <= DEVICE_TTL_NS and
          -RESUME_SKEW_NS <= device_boot_age <= DEVICE_TTL_NS and
          0 <= device_receipt_age <= DEVICE_TTL_NS and
          (fresh_panda and has_panda or bench))


def fresh_parked(evidence: ParkedEvidence, *, now_mono_ns: int, now_boot_ns: int) -> bool:
  """Confirm fresh offroad mode with ignition off for operations that require it."""
  return not any(evidence.panda_ignition) and fresh_offroad(evidence, now_mono_ns=now_mono_ns, now_boot_ns=now_boot_ns)
