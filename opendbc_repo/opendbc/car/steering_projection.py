"""Optional observations of existing torque encoding; never owns actuation."""

from typing import Any, NamedTuple
from types import SimpleNamespace
import math
import struct

import numpy as np

CODECS = frozenset(
  (
    'hyundai.LKAS11',
    'hyundai.CANFD_TORQUE',
    'hyundai.BLENDED_TORQUE',
    'hyundai.BLENDED_LKAS11',
    'gm.ASCMLKASteeringCmd',
    'honda.STEERING_CONTROL',
    'toyota.STEERING_LKA',
    'volkswagen.HCA_TORQUE',
    'subaru.ES_LKAS',
    'subaru.PREGLOBAL_ES_LKAS',
    'mazda.CAM_LKAS',
    'mazda.EPS_LKAS',
    'chrysler.LKAS_COMMAND',
    'mg.FVCM_HSC2_FrP03',
    'rivian.ACM_lkaHbaCmd',
  )
)


class TorqueProjection(NamedTuple):
  version: int
  family: str
  fingerprint: str
  flags: int
  codec_id: str
  mapping_kind: str
  frame: int
  requested_torque: float
  codec_input_torque: float
  unlimited_raw: int | None
  final_raw: int
  effective_max: float
  lookup_x: tuple[float, ...]
  lookup_y: tuple[float, ...]
  limiter_kind: str
  previous_torque: float
  measured_torque: float
  limits: tuple[tuple[str, float], ...]
  shape: tuple[float | bool, ...]
  steer_requested: bool
  can_address: int
  can_bus: int
  can_data: bytes
  reported_torque: float


def projection_from_dict(data: Any) -> TorqueProjection | None:
  """Decode bounded observation metadata; this grants no output permission."""
  try:
    if type(data) is not dict or set(data) != set(TorqueProjection._fields):
      return None
    values = dict(data)
    integer_bounds = {
      'version': (1, 1),
      'flags': (0, 2**64 - 1),
      'frame': (0, 2**64 - 1),
      'final_raw': (-(2**31), 2**31 - 1),
      'can_address': (0, 2**32 - 1),
      'can_bus': (0, 255),
    }
    for name, (lower, upper) in integer_bounds.items():
      if type(values[name]) is not int or not lower <= values[name] <= upper:
        return None
    if values['unlimited_raw'] is not None and (type(values['unlimited_raw']) is not int or not -(2**31) <= values['unlimited_raw'] < 2**31):
      return None
    for name in ('family', 'fingerprint', 'codec_id', 'mapping_kind', 'limiter_kind'):
      if type(values[name]) is not str or not 0 < len(values[name]) <= 256:
        return None
    if (
      values['codec_id'] not in CODECS
      or not values['codec_id'].startswith(values['family'] + '.')
      or values['mapping_kind'] != ('honda_lookup' if values['family'] == 'honda' else 'round')
      or values['limiter_kind']
      != ('normalized_rate' if values['family'] == 'honda' else 'measured' if values['family'] in ('toyota', 'chrysler') else 'driver')
      or type(values['steer_requested']) is not bool
      or type(values['can_data']) is not bytes
      or not 0 < len(values['can_data']) <= 64
    ):
      return None
    for name in ('requested_torque', 'codec_input_torque', 'effective_max', 'previous_torque', 'measured_torque', 'reported_torque'):
      if type(values[name]) not in (int, float) or not math.isfinite(values[name]):
        return None
      values[name] = float(values[name])
    if values['effective_max'] <= 0:
      return None
    for name in ('lookup_x', 'lookup_y'):
      rows = values[name]
      if type(rows) not in (list, tuple) or len(rows) > 16 or any(type(v) not in (int, float) or not math.isfinite(v) for v in rows):
        return None
      values[name] = tuple(float(v) for v in rows)
    xs, ys = values['lookup_x'], values['lookup_y']
    if values['family'] == 'honda':
      if not 2 <= len(xs) == len(ys) <= 16 or any(a >= b for a, b in zip(xs, xs[1:], strict=False)) or any(a >= b for a, b in zip(ys, ys[1:], strict=False)):
        return None
    elif xs or ys:
      return None
    rows = values['limits']
    expected = ('STEER_DELTA_UP', 'STEER_DELTA_DOWN')
    if values['limiter_kind'] == 'driver':
      expected += ('STEER_DRIVER_ALLOWANCE', 'STEER_DRIVER_MULTIPLIER', 'STEER_DRIVER_FACTOR')
    elif values['limiter_kind'] == 'measured':
      expected += ('STEER_ERROR_MAX',)
    if type(rows) not in (list, tuple) or len(rows) != len(expected):
      return None
    limits = []
    for row, name in zip(rows, expected, strict=True):
      if type(row) not in (list, tuple) or len(row) != 2 or row[0] != name or type(row[1]) not in (int, float) or not math.isfinite(row[1]) or row[1] < 0:
        return None
      limits.append((name, float(row[1])))
    values['limits'] = tuple(limits)
    shape = values['shape']
    if type(shape) not in (list, tuple) or len(shape) not in (0, 7) or (shape and values['family'] != 'honda'):
      return None
    for index, value in enumerate(shape):
      if index in (3, 5):
        if type(value) is not bool:
          return None
      elif type(value) not in (int, float) or not math.isfinite(value):
        return None
    values['shape'] = tuple(v if i in (3, 5) else float(v) for i, v in enumerate(shape))
    return TorqueProjection(**values)
  except Exception:
    return None


def projection_to_dict(witness: Any) -> dict | None:
  if not isinstance(witness, TorqueProjection):
    return None
  data = witness._asdict()
  return data if projection_from_dict(data) is not None else None


def float32(value: float) -> float:
  return struct.unpack('!f', struct.pack('!f', value))[0]


class TorqueProjectionObserver:
  """Cached optional seam. A missing emission never receives a fresh witness."""

  def __init__(self, CP: Any):
    self.family, self.fingerprint, self.flags = str(CP.brand), str(CP.carFingerprint), int(CP.flags)
    self.current: TorqueProjection | None = None
    self.pending: tuple | None = None
    self.emitted: tuple | None = None
    self.shape: tuple = ()
    self.frame = 0
    self.requested = 0.0

  def begin(self, requested: float, frame: int) -> None:
    self.current = self.pending = self.emitted = None
    self.shape = ()
    try:
      self.requested, self.frame = float32(float(requested)), int(frame)
    except Exception:
      self.requested = math.nan

  def capture_honda(self, modified: Any, CS: Any) -> None:
    try:
      if modified is not None:
        self.shape = (
          float(modified.torque_lpf),
          float(modified.prev_torque_cmd),
          float(modified.steering_pressed_filter_s),
          bool(modified.steering_pressed_robust_prev),
          float(CS.out.vEgo),
          bool(CS.out.steeringPressed),
          float(CS.out.steeringTorque),
        )
    except Exception:
      self.shape = (math.nan,)

  def input(
    self,
    raw: int | None,
    maximum: float,
    previous: float,
    measured: float,
    params: Any,
    *,
    limiter: str = 'driver',
    mapping: str = 'round',
    codec_input: float | None = None,
    lookup_x: Any = (),
    lookup_y: Any = (),
    limited_input: float | None = None,
  ) -> None:
    try:
      names = ('STEER_DELTA_UP', 'STEER_DELTA_DOWN')
      if limiter == 'driver':
        names += ('STEER_DRIVER_ALLOWANCE', 'STEER_DRIVER_MULTIPLIER', 'STEER_DRIVER_FACTOR')
      elif limiter == 'measured':
        names += ('STEER_ERROR_MAX',)
      elif limiter != 'normalized_rate':
        raise ValueError('unknown limiter')
      if not len(lookup_x) == len(lookup_y) <= 16:
        raise ValueError('unbounded lookup')
      limits = tuple((name, float(getattr(params, name))) for name in names)
      source = self.requested if codec_input is None else float(codec_input)
      # Honda encodes after its normalized rate limiter. If that limiter changed
      # the input, the observation remains transparent but cannot train a fit.
      if mapping == 'honda_lookup':
        raw = int(raw) if source == limited_input else None
      self.pending = (
        None if raw is None else int(raw),
        float(maximum),
        float(previous),
        float(measured),
        limiter,
        mapping,
        source,
        tuple(float(v) for v in lookup_x),
        tuple(float(v) for v in lookup_y),
        limits,
      )
    except Exception:
      self.pending = None

  def emit(self, codec_id: str, final_raw: int, requested: bool, packet: Any) -> None:
    try:
      if self.pending is None:
        return
      address, data, bus = packet
      if codec_id not in CODECS or type(data) is not bytes or not 0 < len(data) <= 64:
        raise ValueError('unbounded CAN observation')
      self.emitted = (str(codec_id), int(final_raw), bool(requested), int(address), int(bus), data)
    except Exception:
      self.emitted = None

  def finish(self, reported: float) -> None:
    try:
      if self.pending is None or self.emitted is None:
        return
      raw, maximum, previous, measured, limiter, mapping, source, xs, ys, limits = self.pending
      codec, final, requested, address, bus, data = self.emitted
      self.current = TorqueProjection(
        1,
        self.family,
        self.fingerprint,
        self.flags,
        codec,
        mapping,
        self.frame,
        self.requested,
        source,
        raw,
        final,
        maximum,
        xs,
        ys,
        limiter,
        previous,
        measured,
        limits,
        self.shape,
        requested,
        address,
        bus,
        data,
        float32(float(reported)),
      )
    except Exception:
      self.current = None


def _shaped_input(witness: TorqueProjection, requested: float, shape: tuple | None) -> float:
  if not witness.shape:
    return requested
  if shape is None:
    if float32(requested) != witness.requested_torque:
      raise ValueError('modified Civic requires a coherent candidate filter state')
    shape = witness.shape
  if len(shape) != 7:
    raise ValueError('missing modified Civic state')
  from opendbc.car.honda.modified_civic_steering import get_civic_bosch_modified_steering_pressed, get_civic_bosch_modified_torque_lpf_tau

  lpf, previous, filtered, was_pressed, speed, raw_pressed, driver = shape
  _, pressed = get_civic_bosch_modified_steering_pressed(raw_pressed, driver, requested, filtered, was_pressed)
  if pressed:
    raise ValueError('driver override')
  tau = get_civic_bosch_modified_torque_lpf_tau(requested, previous, speed)
  from opendbc.car import DT_CTRL

  alpha = DT_CTRL / (tau + DT_CTRL)
  return alpha * requested + (1.0 - alpha) * lpf


def project_request(witness: TorqueProjection, requested: float, *, shape: tuple | None = None) -> int | None:
  """Worker-side exact encoder conversion, not a physical response model."""
  try:
    if not isinstance(witness, TorqueProjection) or witness.version != 1 or witness.codec_id not in CODECS:
      return None
    requested = float32(float(requested))
    if not math.isfinite(requested) or not math.isfinite(witness.effective_max) or witness.effective_max <= 0:
      return None
    if witness.mapping_kind == 'round' and witness.family != 'honda' and not witness.shape:
      return int(round(requested * witness.effective_max))
    if witness.mapping_kind == 'honda_lookup' and witness.family == 'honda':
      xs, ys = witness.lookup_x, witness.lookup_y
      if not 2 <= len(xs) == len(ys) <= 16 or not all(math.isfinite(v) for v in xs + ys):
        return None
      if any(a >= b for a, b in zip(xs, xs[1:], strict=False)) or any(a >= b for a, b in zip(ys, ys[1:], strict=False)):
        return None
      source = _shaped_input(witness, requested, shape)
      return int(np.interp(-source * witness.effective_max, xs, ys))
    return None
  except Exception:
    return None


def normalized_applied_command(witness: TorqueProjection) -> float | None:
  """Worker-side delivered encoder coordinate; never validates a plant model."""
  try:
    if projection_to_dict(witness) is None:
      return None
    if witness.family == 'honda':
      # Honda sends the inverse-sign lookup output while reporting the input.
      # Refuse extrapolation rather than invent an unseen command conversion.
      if not witness.lookup_y[0] <= witness.final_raw <= witness.lookup_y[-1]:
        return None
      return -float(np.interp(witness.final_raw, witness.lookup_y, witness.lookup_x)) / witness.effective_max
    return witness.final_raw / witness.effective_max
  except Exception:
    return None


def project_candidate(witness: TorqueProjection, requested: float, *, previous: float | None = None, shape: tuple | None = None) -> int | None:
  """Worker-side conversion with original limiter functions, never a cap change.

  Stateful candidate replay supplies its own previous command/filter state.
  Omitting it is only a one-frame check against the recorded original context.
  """
  try:
    raw = project_request(witness, requested, shape=shape)
    if raw is None or not witness.steer_requested:
      return None
    last = witness.previous_torque if previous is None else previous
    params = SimpleNamespace(STEER_MAX=witness.effective_max, **dict(witness.limits))
    from opendbc.car.lateral import apply_driver_steer_torque_limits, apply_meas_steer_torque_limits

    if witness.limiter_kind == 'driver':
      limited = apply_driver_steer_torque_limits(raw, last, witness.measured_torque, params, int(witness.effective_max))
    elif witness.limiter_kind == 'measured':
      limited = apply_meas_steer_torque_limits(raw, last, witness.measured_torque, params)
    elif witness.limiter_kind == 'normalized_rate':
      from opendbc.car import DT_CTRL, rate_limit

      source = _shaped_input(witness, float32(float(requested)), shape)
      limited_source = rate_limit(source, last, -params.STEER_DELTA_DOWN * DT_CTRL, params.STEER_DELTA_UP * DT_CTRL)
      return raw if source == limited_source else None
    else:
      return None
    return raw if raw == limited else None
  except Exception:
    return None


def inspect_projection(CI: Any, CC: Any, output: Any, witness: Any) -> bool | None:
  """Exact observed encoder consistency. Health/authority are separate vetoes."""
  try:
    if (
      not isinstance(witness, TorqueProjection)
      or witness.version != 1
      or type(CI.CC.learning_projection_observer) is not TorqueProjectionObserver
      or witness is not CI.CC.learning_projection
    ):
      return None
    from opendbc.car.car_helpers import interfaces

    factory = interfaces[str(CI.CP.carFingerprint)]
    if (
      type(CI) is not factory
      or type(CI.CC) is not factory.CarController
      or witness.family != str(CI.CP.brand)
      or witness.fingerprint != str(CI.CP.carFingerprint)
      or witness.flags != int(CI.CP.flags)
      or str(CI.CP.steerControlType) in ('angle', 'curvature')
      or witness.codec_id not in CODECS
      or not witness.codec_id.startswith(witness.family + '.')
      or witness.mapping_kind != ('honda_lookup' if witness.family == 'honda' else 'round')
      or not math.isfinite(witness.effective_max)
      or witness.effective_max <= 0
      or float32(float(CC.actuators.torque)) != witness.requested_torque
    ):
      return None
    if not CC.latActive or not witness.steer_requested or witness.unlimited_raw is None:
      return False
    if (
      witness.final_raw != witness.unlimited_raw or int(output.torqueOutputCan) != witness.final_raw or float32(float(output.torque)) != witness.reported_torque
    ):
      return False
    expected = witness.codec_input_torque if witness.mapping_kind == 'honda_lookup' else witness.final_raw / witness.effective_max
    return float32(expected) == witness.reported_torque
  except Exception:
    return None


def install_observer(CI: Any) -> bool:
  """Startup-only; caller owns the cached feature enable/disable decision."""
  try:
    from opendbc.car.car_helpers import interfaces

    factory = interfaces[str(CI.CP.carFingerprint)]
    if (
      type(CI) is not factory
      or type(CI.CC) is not factory.CarController
      or str(CI.CP.steerControlType) in ('angle', 'curvature')
      or CI.CP.notCar
      or CI.CP.passive
      or CI.CP.dashcamOnly
    ):
      return False
    CI.CC.learning_projection_observer = TorqueProjectionObserver(CI.CP)
    return True
  except Exception:
    return False
