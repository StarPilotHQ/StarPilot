"""Simulated engine speed and load from pedal travel, road speed and gear position."""
from __future__ import annotations

import math

from openpilot.starpilot.engine_sound.profiles import Profile

FREE_REV_CURVE = 0.7
ACCEL_FULL_LOAD = 3.0        # m/s^2 that counts as full load when openpilot is driving and the pedal is at rest
MIN_SHIFT_SECONDS = 0.45
SHIFT_CUT_SECONDS = 0.12
LIMITER_CUT_SECONDS = 0.07
LIMITER_DROP_RPM = 700.0
LOAD_SECONDS = 0.05


def _lerp(pair: tuple[float, float], x: float) -> float:
  return pair[0] + (pair[1] - pair[0]) * x


def _toward(value: float, target: float, dt: float, seconds: float) -> float:
  return target + (value - target) * math.exp(-dt / seconds)


class EngineModel:
  def __init__(self, profile: Profile):
    self.profile = profile
    self.rpm = profile.idle_rpm
    self.load = 0.0
    self.gear = 0
    self.since_shift = MIN_SHIFT_SECONDS
    self.cut = 0.0

  def update(self, dt: float, pedal: float, speed: float, accel: float, in_gear: bool) -> tuple[float, float]:
    """Advance by dt seconds. Returns (rpm, load 0..1)."""
    p = self.profile
    pedal = min(max(pedal, 0.0), 1.0)
    speed = abs(speed)
    self.since_shift += dt
    self.cut = max(self.cut - dt, 0.0)

    if not in_gear:
      demand = pedal
      target = p.idle_rpm + pedal ** FREE_REV_CURVE * (p.redline_rpm - p.idle_rpm)
      self.gear = 0
    else:
      demand = max(pedal, min(max(accel / ACCEL_FULL_LOAD, 0.0), 1.0))
      if p.gears:
        self._shift(speed, demand)
        target = max(speed * p.gears[self.gear], p.idle_rpm + demand * p.launch_slip_rpm)
      else:
        span = min(0.7 * speed / p.top_speed + 0.4 * demand, 1.0)
        target = p.idle_rpm + span * (p.redline_rpm - p.idle_rpm)
    target = min(max(target, p.idle_rpm), p.redline_rpm)

    if p.limiter and not in_gear and self.rpm >= p.redline_rpm - 60.0 and self.cut == 0.0:
      self.cut = LIMITER_CUT_SECONDS
    limiting = p.limiter and not in_gear and self.cut > 0.0
    if limiting:
      target = p.redline_rpm - LIMITER_DROP_RPM

    seconds = p.rise_seconds if target > self.rpm else p.fall_seconds
    if in_gear and p.gears:
      seconds = min(seconds, 0.12)
    self.rpm = _toward(self.rpm, target, dt, seconds * (0.3 if limiting else 1.0))

    load = 0.0 if limiting else demand * (0.25 if self.cut > 0.0 else 1.0)
    self.load = _toward(self.load, load, dt, LOAD_SECONDS)
    return self.rpm, self.load

  def _shift(self, speed: float, demand: float) -> None:
    p = self.profile
    if self.since_shift < MIN_SHIFT_SECONDS:
      return
    up, down = _lerp(p.upshift_rpm, demand), _lerp(p.downshift_rpm, demand)
    rpm = speed * p.gears[self.gear]
    gear = self.gear
    if rpm > up and gear < len(p.gears) - 1:
      gear += 1
    elif rpm < down and gear > 0 and speed * p.gears[gear - 1] < up * 0.92:
      gear -= 1
    if gear != self.gear:
      self.gear, self.since_shift, self.cut = gear, 0.0, SHIFT_CUT_SECONDS
