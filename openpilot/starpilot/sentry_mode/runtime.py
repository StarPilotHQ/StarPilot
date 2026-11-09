"""Default-off parked motion observation; hardwared and sensord retain ownership."""

from collections.abc import Callable
import math
import signal
import threading
import time
from typing import Any

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.parked_evidence import ParkedEvidence, RESUME_SKEW_NS, fresh_parked
from openpilot.starpilot.power.offroad_preferences import read_saved as read_power, effective as effective_power
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.sentry_mode.policy import Inputs, MotionSample, SentryPolicy
from openpilot.starpilot.sentry_mode.preferences import read_preferences
from openpilot.starpilot.sentry_mode.status import RuntimeStatus
from openpilot.starpilot.sentry_mode.storage import StorageUnavailable


PERIPHERAL_TTL_NS = 1_500_000_000  # C++ 2 Hz publisher; one missed frame.
SAMPLE_TTL_NS = 500_000_000
TICK_SECONDS = 0.1
BATTERY_WARNING_TENTHS = 2
BATTERY_RECOVERED_TENTHS = 4
BATTERY_HOLD_NS = 60_000_000_000
BATTERY_CRITICAL_HOLD_NS = 10_000_000_000
BATTERY_REPEAT_NS = 3600 * 1_000_000_000


def recent_battery_warning(events: list[dict], now_ns: int) -> bool:
  return any(event["kind"] == "battery" and 0 <= now_ns - event["wallTimeNs"] <= BATTERY_REPEAT_NS for event in events)


def boot_time_ns() -> int:
  return time.clock_gettime_ns(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))


class SentryRuntime:
  def __init__(self, params: Params, messages: Any, *, mono_clock: Callable[[], int] = time.monotonic_ns,
               boot_clock: Callable[[], int] = boot_time_ns,
               record: Callable[[str, int, Callable[[], bool]], Any] | None = None, status: RuntimeStatus | None = None,
               battery: Callable[[int, int], Any] | None = None):
    self.params, self.messages = params, messages
    self.mono_clock, self.boot_clock = mono_clock, boot_clock
    self.record = record
    self.status = status
    self.battery = battery
    self.battery_low_since: int | None = None
    self.battery_warned = False
    self.policy = SentryPolicy()
    self.saved_raw: bytes | None = None
    self.device_after_mono_ns = mono_clock()
    self.sensor_after_mono_ns = self.device_after_mono_ns
    self.device_offset_ns: int | None = None
    self.last_pair_offset_ns: int | None = None
    self.last_sample_ns = 0
    self.recording_state = "idle"
    self.power: tuple[int | None, int | None] = (None, None)

  def _recording_state(self, state: str) -> None:
    if state != self.recording_state:
      self.recording_state = state
      if state not in ("idle", "stored"):
        cloudlog.warning(f"sentry motion recording: {state}")

  def _clocks(self) -> tuple[int, int] | None:
    before = self.mono_clock()
    boot = self.boot_clock()
    after = self.mono_clock()
    if after < before or after - before > RESUME_SKEW_NS:
      self.device_after_mono_ns = max(self.device_after_mono_ns, after)
      self.sensor_after_mono_ns = max(self.sensor_after_mono_ns, after)
      self.device_offset_ns = None
      self.policy = SentryPolicy(self.policy.settings)
      return None
    offset = boot - (before + after) // 2
    if self.last_pair_offset_ns is not None and abs(offset - self.last_pair_offset_ns) > RESUME_SKEW_NS:
      self.device_after_mono_ns = max(self.device_after_mono_ns, after)
      self.sensor_after_mono_ns = max(self.sensor_after_mono_ns, after)
      self.device_offset_ns = None
      self.policy = SentryPolicy(self.policy.settings)
    self.last_pair_offset_ns = offset
    return after, boot

  def _fresh(self, name: str, *, now_mono_ns: int, now_boot_ns: int, ttl_ns: int) -> bool:
    sm = self.messages
    stamp = int(sm.logMonoTime[name])
    receipt = int(sm.recv_time[name] * 1e9)
    if not (sm.seen[name] and sm.alive[name] and sm.valid[name] and stamp > 0 and receipt > 0):
      return False
    # C++ pandad stamps BOOTTIME; receipt is SubMaster MONOTONIC.
    age = now_boot_ns - stamp
    return 0 <= age <= ttl_ns and 0 <= now_mono_ns - receipt <= ttl_ns

  def _authority(self) -> tuple[bool, bool, bool, bool, int]:
    """Return offroad, ignition-off, voltage-safe, device-fresh, current MONO ns."""
    self.power = (None, None)
    clocks = self._clocks()
    if clocks is None:
      return False, False, False, False, self.mono_clock()
    now_ns, boot_ns = clocks
    sm = self.messages
    offroad, offroad_readable = read_saved(self.params, "IsOffroad", 8)
    shutdown, shutdown_readable = read_saved(self.params, "DoShutdown", 8)
    manager_offroad = offroad_readable and offroad == b"1" and shutdown_readable and shutdown in (None, b"0")
    try:
      pandas = sm["pandaStates"]
      ignition = tuple(bool(p.ignitionLine or p.ignitionCan) for p in pandas)
      known_pandas = bool(pandas) and all(str(p.pandaType) != "unknown" for p in pandas)
      ignition_off = known_pandas and not any(ignition)
      if sm.updated["deviceState"] and int(sm.logMonoTime["deviceState"]) > self.device_after_mono_ns:
        self.device_offset_ns = self.last_pair_offset_ns
      evidence = ParkedEvidence(
        manager_offroad, bool(sm.seen["deviceState"]), bool(sm.alive["deviceState"]), bool(sm.valid["deviceState"]),
        bool(sm["deviceState"].started), int(sm.logMonoTime["deviceState"]),
        int(sm.recv_time["deviceState"] * 1e9), self.device_offset_ns, self.device_after_mono_ns,
        bool(sm.seen["pandaStates"]), bool(sm.alive["pandaStates"]), bool(sm.valid["pandaStates"]),
        int(sm.logMonoTime["pandaStates"]), int(sm.recv_time["pandaStates"] * 1e9), ignition,
      )
      device_fresh = known_pandas and fresh_parked(evidence, now_mono_ns=now_ns, now_boot_ns=boot_ns)
      peripheral = sm["peripheralState"]
      power_saved = read_power(self.params)
      cutoff_mv = effective_power(power_saved).cutoff_tenths * 100
      voltage_fresh = self._fresh("peripheralState", now_mono_ns=now_ns, now_boot_ns=boot_ns, ttl_ns=PERIPHERAL_TTL_NS) and \
                      str(peripheral.pandaType) != "unknown"
      self.power = (round(int(peripheral.voltage) / 100) if voltage_fresh else None,
                    cutoff_mv // 100 if power_saved.readable and power_saved.valid else None)
      voltage_ok = (power_saved.readable and power_saved.valid and voltage_fresh and int(peripheral.voltage) > cutoff_mv and
                    int(sm["deviceState"].carBatteryCapacityUwh) > 0)
      return bool(manager_offroad and not sm["deviceState"].started), ignition_off, bool(voltage_ok), device_fresh, now_ns
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError, RuntimeError):
      return bool(manager_offroad), False, False, False, now_ns

  def _sample(self, now_ns: int) -> MotionSample | None:
    sm = self.messages
    try:
      if not (sm.updated["accelerometer"] and sm.seen["accelerometer"] and sm.alive["accelerometer"] and
              sm.valid["accelerometer"]):
        return None
      event = sm["accelerometer"]
      stamp = int(event.timestamp)
      envelope = int(sm.logMonoTime["accelerometer"])
      receipt = int(sm.recv_time["accelerometer"] * 1e9)
      if (stamp <= max(self.last_sample_ns, self.sensor_after_mono_ns) or not 0 < stamp <= envelope <= now_ns or
          now_ns - stamp > SAMPLE_TTL_NS or now_ns - envelope > SAMPLE_TTL_NS or
          not 0 <= now_ns - receipt <= SAMPLE_TTL_NS):
        return None
      values = tuple(float(v) for v in event.acceleration.v)
      if len(values) != 3 or any(not math.isfinite(v) for v in values):
        return None
      sample = MotionSample(stamp / 1e9, values)
      self.last_sample_ns = stamp
      return sample
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return None

  def _permitted(self, expected_raw: bytes) -> bool:
    self.messages.update(0)
    offroad, ignition_off, voltage_ok, fresh, now_ns = self._authority()
    self._sample(now_ns)
    saved = read_preferences(self.params)
    return (saved.readable and saved.valid and saved.preferences.enabled and saved.raw == expected_raw and
            offroad and ignition_off and voltage_ok and fresh and
            self.policy.arm_started is not None and self.policy.last_sample_time is not None and
            0 <= now_ns - self.last_sample_ns <= SAMPLE_TTL_NS)

  def tick(self) -> Any:
    self.messages.update(0)
    saved = read_preferences(self.params)
    active = (saved.readable and saved.valid and
              saved.preferences.enabled)
    if saved.raw != self.saved_raw:
      self.policy = SentryPolicy(saved.preferences.settings)
      self.saved_raw = saved.raw
    offroad, ignition_off, voltage_ok, fresh, now_ns = self._authority()
    sample = self._sample(now_ns) if active else None
    sm = self.messages
    sample_pending = bool(not sm.updated["accelerometer"] and sm.seen["accelerometer"] and
                          sm.alive["accelerometer"] and sm.valid["accelerometer"])
    decision = self.policy.update(now_ns / 1e9, Inputs(active, offroad, voltage_ok, fresh, ignition_off, sample, sample_pending))
    expected_raw = saved.raw
    if decision.event is not None and self.record is not None and expected_raw is not None and self._permitted(expected_raw):
      try:
        receipt = self.record(decision.event, now_ns, lambda: self._permitted(expected_raw))
        self._recording_state("stored" if getattr(receipt, "durable", False) else "durability_unknown")
      except Exception:
        # No retry: storage may have published before its durability result failed.
        self._recording_state("unavailable")
    self._battery_warning(active and offroad and ignition_off and fresh, now_ns)
    if self.status is not None:
      self.status.publish(decision, saved.raw, self.recording_state, power=self.power)
    return decision

  def _battery_warning(self, parked: bool, now_ns: int) -> None:
    voltage, cutoff = self.power
    if not parked or voltage is None or cutoff is None:
      self.battery_low_since = None
      return
    if voltage >= cutoff + BATTERY_RECOVERED_TENTHS:
      self.battery_warned = False
    if voltage > cutoff + BATTERY_WARNING_TENTHS or self.battery_warned:
      self.battery_low_since = None
      return
    if self.battery_low_since is None:
      self.battery_low_since = now_ns
    if now_ns - self.battery_low_since < (BATTERY_CRITICAL_HOLD_NS if voltage <= cutoff else BATTERY_HOLD_NS):
      return
    self.battery_warned, self.battery_low_since = True, None
    if self.battery is not None:
      try:
        self.battery(voltage, cutoff)
      except StorageUnavailable:
        cloudlog.exception("sentry motion battery warning failed")

  def close(self) -> None:
    for socket in getattr(self.messages, "sock", {}).values():
      close = getattr(socket, "close", None)
      if close is not None:
        close()


def main() -> None:
  from openpilot.cereal import messaging
  from openpilot.starpilot.sentry_mode.storage import EventStore

  stop = threading.Event()
  previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
  for number in previous:
    signal.signal(number, lambda _signal, _frame: stop.set())
  messages = messaging.SubMaster(["accelerometer", "deviceState", "pandaStates", "peripheralState"])
  store = EventStore()
  from openpilot.starpilot.galaxy.camera_snapshot import CameraSnapshot, SnapshotUnavailable
  camera = CameraSnapshot()
  def record(kind, stamp, permitted):
    images = {}
    for name in ("wide", "cabin"):
      try:
        images[name] = camera.capture(name, permitted=permitted)
      except SnapshotUnavailable:
        if not permitted():
          raise
    return store.record(kind, stamp, permitted=permitted, images=images)
  def battery(voltage, cutoff):
    if recent_battery_warning(store.snapshot()["events"], time.time_ns()):
      return
    store.record("battery", time.monotonic_ns(), permitted=lambda: True, details={"voltage": voltage, "cutoff": cutoff})
  runtime = SentryRuntime(Params(), messages, record=record, status=RuntimeStatus(), battery=battery)
  try:
    while not stop.is_set():
      started = time.monotonic()
      runtime.tick()
      stop.wait(max(0.0, TICK_SECONDS - (time.monotonic() - started)))
  finally:
    runtime.close()
    for number, handler in previous.items():
      signal.signal(number, handler)


if __name__ == "__main__":
  main()
