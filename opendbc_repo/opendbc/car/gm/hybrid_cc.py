"""Exact Hybrid button bytes; caller owns physical and axis authorization."""
from dataclasses import dataclass

NEUTRAL = (0x10FF, 0x15EE, 0x1ADD, 0x1FCC)
RESUME = (0x55AE, 0x5F8C, 0x6F7C, 0x659E)
CANCEL = (0x60AF, 0x659E, 0x6A8D, 0x6F7C)
UNIQUE_NEUTRAL = {0x15EE: 1, 0x1FCC: 3}
TTL_NS = 100_000_000


def custom_bytes(action: str, phase: int, prefix: int) -> bytes:
  if action not in ('resume', 'cancel') or not 0 <= phase < 4 or prefix not in (1, 0x41):
    raise ValueError('Invalid Hybrid button tuple')
  word = (RESUME if action == 'resume' else CANCEL)[phase]
  return bytes((0, 0, 0, prefix, 0, word >> 8, word & 255))


def standard_set_bytes(counter: int) -> bytes:
  if not 0 <= counter < 4:
    raise ValueError('Invalid standard counter')
  checksum = 0xFF + counter * 0x4EF - 0x20
  return bytes((0, 0, 0, 1, counter, 0x30 | (checksum >> 8), checksum & 255))


@dataclass
class PhysicalSlot:
  phase: int | None = None
  last_admitted_phase: int | None = None
  needs_requalification: bool = False
  prefix: int = 1
  counter: int = 0
  observed_ns: int = 0
  credit_ns: int = 0
  last_tx_ns: int = 0

  def clear(self):
    self.phase = None
    self.credit_ns = 0

  def reset(self):
    self.clear()
    self.last_admitted_phase = None
    self.needs_requalification = False
    self.observed_ns = 0
    self.last_tx_ns = 0

  def requalify(self, *, physical_ready: bool) -> bool:
    if not self.needs_requalification or not physical_ready:
      return False
    self.clear()
    self.last_admitted_phase = None
    self.needs_requalification = False
    # Keep observation monotonicity and accepted TX cadence across source recovery.
    return True

  def observe(self, stamp: int, raw: bytes, *, bus: int):
    if bus != 0 or stamp <= 0 or stamp <= self.observed_ns:
      return
    if self.observed_ns and stamp - self.observed_ns > TTL_NS:
      self.needs_requalification = True
    self.observed_ns = stamp
    self.clear()
    if self.needs_requalification:
      return
    if len(raw) != 7 or raw[:3] != bytes(3) or raw[3] not in (1, 0x41) or raw[4] > 3:
      return
    phase = UNIQUE_NEUTRAL.get(raw[5] * 256 + raw[6])
    if phase is None or phase == self.last_admitted_phase:
      return
    self.last_admitted_phase = phase
    self.phase, self.prefix, self.counter = phase, raw[3], raw[4]
    self.credit_ns = stamp

  def expected(self, action: str, now: int, *, interval_ns: int, authorized: bool) -> bytes | None:
    if self.credit_ns > 0 and now - self.credit_ns > TTL_NS:
      self.clear()
      self.needs_requalification = True
    if (not authorized or self.needs_requalification or self.phase is None or self.credit_ns <= 0 or
        not 0 <= now - self.credit_ns <= TTL_NS or interval_ns < 0 or
        self.last_tx_ns and now - self.last_tx_ns < interval_ns):
      return None
    if action == 'resume':
      return custom_bytes(action, self.phase, self.prefix)
    if action == 'cancel':
      return custom_bytes(action, (self.phase + 1) % 4, self.prefix)
    if action == 'set':
      return standard_set_bytes((self.counter + 1) % 4)
    if action == 'gas_set':
      return standard_set_bytes(0)
    return None

  def accept(self, action: str, raw: bytes, now: int, *, interval_ns: int, authorized: bool) -> bool:
    expected = self.expected(action, now, interval_ns=interval_ns, authorized=authorized)
    if expected is None or raw != expected:
      return False
    self.credit_ns = 0
    self.last_tx_ns = now
    return True


def physical_semantic(raw: bytes, *, bus: int) -> str | None:
  """Exact owner decoding; custom packets never fall through generic high-nibble buttons."""
  if bus != 0 or len(raw) != 7 or raw[:3] != bytes(3) or raw[3] not in (1, 0x41) or raw[4] > 3:
    return None
  word = int.from_bytes(raw[5:7], 'big')
  unique = {0x15EE: 'neutral', 0x1FCC: 'neutral', 0x55AE: 'resume', 0x5F8C: 'resume',
            0x2ACD: 'main', 0x20EF: 'main', 0x60AF: 'cancel', 0x6A8D: 'cancel'}
  if word in unique:
    return unique[word]
  if raw == standard_set_bytes(raw[4]) or (raw[4] == 0 and word in (0x30DF, 0x35CE, 0x3ABD, 0x3FAC)):
    return 'set'
  return None


@dataclass(frozen=True)
class HybridPhysical:
  observed_ns: int
  source_ns: tuple[int, ...]
  button_credit_ns: int


@dataclass(frozen=True)
class HybridStatusPhysical:
  main_ns: int
  main_on: bool
  brake_pressed: bool
  accelerator_pressed: bool
  sensor_gas: bool
  camera_ns: tuple[int, ...]


class HybridButtons:
  def __init__(self):
    self.slot = PhysicalSlot()
    self.semantic = None
    self.reset_pending = False
    self.packets = []

  def observe_packets(self, packets):
    self.packets = [(stamp, bytes(raw), bus) for stamp, frames in packets
                    for address, raw, bus in frames if address == 0x1E1]

  def update(self, physical_ready, *, enable_ready=None):
    if enable_ready is None:
      enable_ready = physical_ready
    mapping = {'neutral': 1, 'resume': 2, 'set': 3, 'main': 5, 'cancel': 6}
    previous = self.semantic
    edges = []
    if not physical_ready:
      self.slot.clear()
      self.slot.needs_requalification = True
      self.semantic = None
      self.reset_pending = True
    elif not enable_ready:
      self.semantic = None
      self.reset_pending = True
    for stamp, raw, bus in self.packets:
      old_stamp = self.slot.observed_ns
      self.slot.observe(stamp, raw, bus=bus)
      if stamp <= old_stamp or bus != 0:
        continue
      if self.slot.needs_requalification:
        self.semantic = None
        self.reset_pending = True
        if physical_ready and enable_ready:
          self.slot.requalify(physical_ready=True)
        continue
      if not physical_ready or not enable_ready:
        continue
      semantic = physical_semantic(raw, bus=bus)
      if semantic is None:
        continue
      value = mapping[semantic]
      if self.semantic is None:
        self.semantic = value
        self.reset_pending = value != 1
        continue
      if self.reset_pending:
        if value == 1:
          self.semantic = 1
          self.reset_pending = False
        continue
      if value != self.semantic:
        edges.append((self.semantic, value))
        self.semantic = value
    self.packets = []
    return previous, self.semantic, edges


def sources_current(cs, now_ns):
  sources = cs.hybrid_sources
  profile = cs.hybrid_profile
  sensor_current = (not (profile.pedal and profile.longitudinal) or
                    cs.pedal_sensor_healthy and 0 < cs.pedal_sensor_ts_nanos <= now_ns <= cs.pedal_sensor_ts_nanos + TTL_NS)
  return bool(sensor_current and cs.out.canValid and not cs.out.canTimeout and sources and
              all(stamp > 0 and 0 <= now_ns - stamp <= limit for stamp, limit in sources))


def lateral_ready(cs, now_ns):
  from opendbc.car.structs import CarState
  return (sources_current(cs, now_ns) and cs.out.cruiseState.available and
          cs.out.gearShifter in (CarState.GearShifter.drive, CarState.GearShifter.low, CarState.GearShifter.manumatic) and
          not cs.out.steerFaultTemporary and not cs.out.steerFaultPermanent)


def rearm_ready(cs, now_ns):
  return (lateral_ready(cs, now_ns) and not cs.out.brakePressed and
          not cs.out.gasPressed and not cs.out.regenBraking)


def status_ready(cs, now_ns):
  profile = cs.hybrid_profile
  physical = cs.hybrid_status
  return bool(profile is not None and profile.pedal and profile.longitudinal and
              physical is not None and rearm_ready(cs, now_ns) and
              0 < physical.main_ns <= now_ns <= physical.main_ns + 300_000_000 and
              physical.main_on and not physical.brake_pressed and
              not physical.accelerator_pressed and not physical.sensor_gas and
              len(physical.camera_ns) == (0 if profile.removed else 2) and
              all(0 < stamp <= now_ns <= stamp + 1_000_000_000 for stamp in physical.camera_ns))


class HybridPedalCommand:
  def __init__(self, cp):
    self.cp = cp
    self.steady = 0.
    self.active_last = False
    self.recovery = False

  def withdraw(self):
    self.steady = 0.
    self.active_last = True
    self.recovery = True

  def update(self, accel, active, cs, *, stopping, resume, orientation, acc_tune=False):
    import math
    import numpy as np
    from opendbc.car import ACCELERATION_DUE_TO_GRAVITY
    from opendbc.car.gm.silverado_cc import pedal_fraction, pedal_slew
    from opendbc.car.gm.long_tune import acc_tune_limits
    if not active:
      self.steady = 0.
      self.active_last = False
      self.recovery = False
      return 0.
    if cs.vEgo < .25 and stopping and not resume:
      return 0.  # Original outer branch skips calc and retains its state.
    target = pedal_fraction(accel, cs.vEgo)
    self.steady = pedal_slew(target, self.steady, accel, cs.vEgo) if self.active_last else target
    self.active_last = True
    pitch = 0.
    if orientation is not None and len(orientation) == 3 and cs.vEgo > .25 and math.isfinite(orientation[1]):
      pitch = math.sin(orientation[1]) * ACCELERATION_DUE_TO_GRAVITY
      pitch = 0. if pitch > 0. and accel > 0. else min(pitch, .20)
    radius = .075 * self.cp.wheelbase + .1453
    drag = .5 * .30 * (1.05 * self.cp.wheelbase + .0679) * 1.225 * cs.vEgo ** 2
    switch = float(np.interp(cs.vEgo, [.5, 10.], [6150, 5500]))
    maximum = 2.
    if acc_tune:
      maximum, switch = acc_tune_limits(cs.vEgo, maximum, switch, 6150)
    scaled = radius * (self.cp.mass * float(np.clip(accel + pitch, -4., maximum)) + drag) + 6150
    gas = int(round(np.clip(scaled, 5500, 8191)))
    brake = int(round(np.interp(min((scaled - switch) / (radius * self.cp.mass), 0.), [-4., -1.], [400., 0.])))
    if brake > 0 or stopping:
      gas = 5500
    command = self.steady
    if gas > 5500 and cs.cruiseState.standstill and (cs.standstill or cs.vEgo < .3):
      command = 18. / 255.
    if self.recovery:
      command = min(command, pedal_slew(command, 0., accel, cs.vEgo))
      self.steady = command
      self.recovery = False
    return command


def policy_for(cp):
  from opendbc.car.gm.values import malibu_hybrid_profile
  from opendbc.car.gm.longitudinal import GMOrdinaryLongitudinalPolicy, _GMDefaultStopPolicy
  from opendbc.car.gm.cc_longitudinal import VoltCcEvidence
  import numpy as np
  profile = malibu_hybrid_profile(cp)
  if profile is None or not profile.longitudinal:
    return None

  class HybridPolicy(GMOrdinaryLongitudinalPolicy):
    stopping_decel_rate = float(np.float32(.8)) if profile.pedal else 1.
    ignore_cruise_standstill = profile.pedal
    kp = ((0., 5., 15., 35.), tuple(float(np.float32(v)) for v in (.095, .085, .065, .05))) if profile.pedal else ((0.,), (0.,))

    def stop_policy(self):
      return _GMDefaultStopPolicy(.25, VoltCcEvidence)

    def feedforward(self, target, speed, last_output):
      return target * float(np.float32(.2) if profile.pedal else 1.)
  return HybridPolicy()
