"""Parked Sentry arming and motion escalation without hardware or I/O."""

from dataclasses import dataclass
import math


ARM_DELAY_SECONDS = 90.0
LOOP_INTERVAL_SECONDS = 0.1
ALARM_TRIGGER_COUNT = 25
ALARM_TIME_SECONDS = 30.0
RESET_TIME_SECONDS = 60.0
MAX_SAMPLE_AGE_SECONDS = 0.5
MAX_UPDATE_GAP_SECONDS = 0.5  # 10 Hz owner may drop a few ticks, not a sleep/resume interval.
MAX_ACCELERATION_AXIS = 200.0  # Larger than plausible vehicle acceleration; rejects overflow and corrupt samples.


@dataclass(frozen=True)
class Settings:
  sensitivity: float = 0.04
  warning_time_seconds: float = 1.0

  def __post_init__(self) -> None:
    if type(self.sensitivity) not in (int, float) or not math.isfinite(self.sensitivity) or \
       not 0.005 <= self.sensitivity <= 1.0:
      raise ValueError("Sentry sensitivity must be within 0.005–1.0")
    if type(self.warning_time_seconds) not in (int, float) or not math.isfinite(self.warning_time_seconds) or \
       not 0.1 <= self.warning_time_seconds <= 10.0:
      raise ValueError("Sentry warning time must be within 0.1–10 seconds")

  @property
  def warning_trigger_count(self) -> int:
    # Count 10 Hz samples; this threshold is not elapsed time.
    return max(1, math.ceil(self.warning_time_seconds / LOOP_INTERVAL_SECONDS))


@dataclass(frozen=True)
class MotionSample:
  mono_time: float
  acceleration: tuple[float, float, float]

  def __post_init__(self) -> None:
    if type(self.mono_time) not in (int, float) or not math.isfinite(self.mono_time) or self.mono_time < 0 or \
       not isinstance(self.acceleration, tuple) or len(self.acceleration) != 3 or \
       any(type(axis) not in (int, float) or not math.isfinite(axis) or abs(axis) > MAX_ACCELERATION_AXIS
           for axis in self.acceleration):
      raise ValueError("Motion sample needs a finite monotonic timestamp and three finite axes")


@dataclass(frozen=True)
class Inputs:
  enabled: bool
  offroad: bool
  voltage_ok: bool
  device_fresh: bool
  ignition_off: bool
  sample: MotionSample | None = None
  sample_pending: bool = False

  def __post_init__(self) -> None:
    if any(type(value) is not bool for value in (self.enabled, self.offroad, self.voltage_ok,
                                                self.device_fresh, self.ignition_off, self.sample_pending)) or \
       (self.sample is not None and not isinstance(self.sample, MotionSample)):
      raise ValueError("Sentry authority inputs must be explicit booleans and a validated motion sample")


@dataclass(frozen=True)
class Decision:
  state: str
  event: str | None = None
  seconds_remaining: int | None = None


class SentryPolicy:
  def __init__(self, settings: Settings | None = None):
    self.settings = settings if settings is not None else Settings()
    self.last_now: float | None = None
    self.arm_started: float | None = None
    self.last_sample_time: float | None = None
    self.previous_magnitude: float | None = None
    self.trigger_count = 0
    self.trigger_started: float | None = None
    self.alarm_triggered = False

  def _clear_motion(self) -> None:
    self.last_sample_time = None
    self.previous_magnitude = None
    self.trigger_count = 0
    self.trigger_started = None
    self.alarm_triggered = False

  def _disarm(self) -> None:
    self.arm_started = None
    self._clear_motion()

  def update(self, now: float, inputs: Inputs) -> Decision:
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
      self._disarm()
      self.last_now = None
      return Decision("unavailable")
    if self.last_now is not None and now < self.last_now:
      self._disarm()
      self.last_now = now
      return Decision("unavailable")
    if self.last_now is not None and now - self.last_now > MAX_UPDATE_GAP_SECONDS:
      self._disarm()
      self.last_now = now
      return Decision("unavailable")
    self.last_now = now

    if not inputs.enabled:
      self._disarm()
      return Decision("disabled")
    if not inputs.offroad:
      self._disarm()
      return Decision("disabled_onroad")
    if not inputs.ignition_off:
      self._disarm()
      return Decision("disabled_ignition")
    if not inputs.voltage_ok:
      self._disarm()
      return Decision("low_voltage")
    if not inputs.device_fresh:
      self._disarm()
      return Decision("unavailable")

    sample = inputs.sample
    # A poll with no new message keeps arming while the last sample is fresh.
    if sample is None and inputs.sample_pending and self.arm_started is not None and \
       self.last_sample_time is not None and 0 <= now - self.last_sample_time <= MAX_SAMPLE_AGE_SECONDS:
      remaining = ARM_DELAY_SECONDS - (now - self.arm_started)
      return Decision("arming", seconds_remaining=max(0, math.ceil(remaining))) if remaining > 0 else Decision("armed")
    if sample is None or sample.mono_time > now or now - sample.mono_time > MAX_SAMPLE_AGE_SECONDS or \
       (self.last_sample_time is not None and sample.mono_time <= self.last_sample_time):
      last_sample_time = self.last_sample_time
      self._disarm()
      # A duplicate may still be younger than the age limit on the next tick.
      self.last_sample_time = last_sample_time
      return Decision("sensor_unavailable")
    self.last_sample_time = sample.mono_time

    if self.arm_started is None:
      self.arm_started = now
    elapsed = now - self.arm_started
    if elapsed < ARM_DELAY_SECONDS:
      return Decision("arming", seconds_remaining=max(0, int(ARM_DELAY_SECONDS - elapsed)))

    magnitude = math.hypot(*sample.acceleration)
    previous = self.previous_magnitude
    self.previous_magnitude = magnitude
    if previous is None:
      return Decision("armed")

    # An expired motion window cannot turn into a late alarm on this sample.
    if self.trigger_started is not None and now - self.trigger_started >= RESET_TIME_SECONDS:
      self.trigger_count = 0
      self.trigger_started = None
      self.alarm_triggered = False

    if abs(magnitude - previous) <= self.settings.sensitivity:
      return Decision("armed")
    self.trigger_count += 1
    if self.trigger_started is None:
      self.trigger_started = now
    if self.trigger_count == self.settings.warning_trigger_count:
      return Decision("armed", event="warning")
    if self.trigger_count > ALARM_TRIGGER_COUNT and now - self.trigger_started >= ALARM_TIME_SECONDS and not self.alarm_triggered:
      self.alarm_triggered = True
      return Decision("armed", event="alarm")
    return Decision("armed")
