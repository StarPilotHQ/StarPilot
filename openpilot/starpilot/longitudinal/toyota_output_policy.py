"""Vehicle-owned Toyota longitudinal target shaping.

Corolla and key-ready Sienna4 use their production profiles. The caller must supply current
validated lead data for Sienna and reset it whenever longitudinal control or
source authority is lost. It does not select a planner profile or grant control.
"""

from collections import OrderedDict
from dataclasses import dataclass
from typing import cast

import capnp
import math
import os
import time
import threading

import numpy as np

from opendbc.car import structs
from openpilot.cereal import log
from opendbc.car.toyota.values import CAR, EPS_SCALE, ToyotaFlags, ToyotaSafetyFlags
from openpilot.common.realtime import DT_CTRL


COROLLA = str(CAR.TOYOTA_COROLLA_TSS2)
SIENNA_4G = str(CAR.TOYOTA_SIENNA_4TH_GEN)
_ACCEL_MIN, _ACCEL_MAX = -5.0, 3.0
RADAR_MAX_AGE_NS = 100_000_000  # Two periods of the current 20 Hz radarState service.
CLOCK_PAIR_MAX_SKEW_NS = 5_000_000


def clock_pair_ns() -> tuple[int, int] | None:
  """Bracket BOOTTIME with MONOTONIC without comparing their raw epochs."""
  boot_clock = getattr(time, 'CLOCK_BOOTTIME', None)
  if boot_clock is None:
    return None
  try:
    before = time.monotonic_ns()
    boot = time.clock_gettime_ns(boot_clock)
    after = time.monotonic_ns()
  except OSError:
    return None
  return ((before + after) // 2, boot) if 0 <= after - before <= CLOCK_PAIR_MAX_SKEW_NS else None


@dataclass(frozen=True)
class Lead:
  present: bool
  distance_m: float
  lateral_m: float
  speed_mps: float
  accel_mps2: float


def leads_from_radar(radar, *, message_ns: int, receipt_ns: int, now_ns: int,
                     drive_id: int, valid: bool) -> tuple[Lead, Lead] | None:
  """Project both current RadarState leads, never treating missing transport as no lead."""
  if (valid is not True or any(type(stamp) is not int for stamp in (message_ns, receipt_ns, now_ns, drive_id)) or
      not 0 < drive_id < message_ns <= receipt_ns <= now_ns or
      now_ns - message_ns > RADAR_MAX_AGE_NS or now_ns - receipt_ns > RADAR_MAX_AGE_NS):
    return None
  rows = _radar_lead_rows(radar)
  return cast(tuple[Lead, Lead], tuple(Lead(*row) for row in rows)) if rows is not None else None


# Only immutable pure projection values are retained, never transport authority.
_RADAR_LEAD_ROWS = OrderedDict()
_RADAR_LEAD_LOCK = threading.Lock()
_RADAR_LEAD_CACHE_MAX = 8
_RADAR_LEAD_KEY_MAX_BYTES = 4096


_RADAR_LEAD_FIELDS = tuple(log.RadarState.schema.fields[name] for name in ('leadOne', 'leadTwo'))
_LEAD_VALUE_FIELDS = tuple(log.RadarState.LeadData.schema.fields[name] for name in ('present', 'dRel', 'yRel', 'vLead', 'aLeadK'))


def _decode_radar_lead_rows(radar):
  try:
    if type(radar) in (capnp._DynamicStructReader, capnp._DynamicStructBuilder) and radar.schema == log.RadarState.schema:
      present, distance, lateral, speed, accel = _LEAD_VALUE_FIELDS
      leads = tuple(Lead(item._get_by_field(present), item._get_by_field(distance), item._get_by_field(lateral),
                         item._get_by_field(speed), item._get_by_field(accel))
                    for item in (radar._get_by_field(field) for field in _RADAR_LEAD_FIELDS))
      if any(type(lead.present) is not bool for lead in leads):
        return None
    else:
      if any(type(item.present) is not bool for item in (radar.leadOne, radar.leadTwo)):
        return None
      leads = tuple(Lead(item.present, item.dRel, item.yRel, item.vLead, item.aLeadK)
                    for item in (radar.leadOne, radar.leadTwo))
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None
  if len(leads) != 2 or not ToyotaOutputPolicy._leads_valid(leads):
    return None
  return tuple((lead.present, lead.distance_m, lead.lateral_m, lead.speed_mps, lead.accel_mps2) for lead in leads)


def _radar_lead_rows(radar):
  # Custom fixtures retain their original decoding behavior. A builder is copied
  # on every call so mutation changes the key and cannot poison prior rows.
  if type(radar) not in (capnp._DynamicStructReader, capnp._DynamicStructBuilder):
    return _decode_radar_lead_rows(radar)
  try:
    if radar.schema != log.RadarState.schema:
      return _decode_radar_lead_rows(radar)
    snapshot = radar.as_builder() if type(radar) is capnp._DynamicStructReader else radar.as_reader().as_builder()
    key = snapshot.to_bytes()
  except (AttributeError, TypeError, ValueError, OverflowError, capnp.KjException):
    return _decode_radar_lead_rows(radar)
  if len(key) > _RADAR_LEAD_KEY_MAX_BYTES:
    return _decode_radar_lead_rows(snapshot)
  with _RADAR_LEAD_LOCK:
    rows = _RADAR_LEAD_ROWS.get(key)
    if rows is not None:
      _RADAR_LEAD_ROWS.move_to_end(key)
      return rows
  rows = _decode_radar_lead_rows(snapshot)
  if rows is not None:
    with _RADAR_LEAD_LOCK:
      _RADAR_LEAD_ROWS[key] = rows
      _RADAR_LEAD_ROWS.move_to_end(key)
      if len(_RADAR_LEAD_ROWS) > _RADAR_LEAD_CACHE_MAX:
        _RADAR_LEAD_ROWS.popitem(last=False)
  return rows


def eligible(cp) -> bool:
  """Only the two current Toyota controllers with actual openpilot long."""
  try:
    return (cp.brand == 'toyota' and str(cp.carFingerprint) in (COROLLA, SIENNA_4G) and
            cp.openpilotLongitudinalControl is True and not cp.dashcamOnly and
            not cp.passive and not cp.notCar)
  except AttributeError:
    return False


def production_enabled(cp) -> bool:
  if not eligible(cp):
    return False
  if str(cp.carFingerprint) == COROLLA:
    from openpilot.starpilot.car.toyota.aol import qualified
    return qualified(cp) and cp.safetyConfigs[0].safetyParam == 73
  required = ToyotaFlags.TSS2 | ToyotaFlags.NO_DSU | ToyotaFlags.SECOC | ToyotaFlags.RAISED_ACCEL_LIMIT
  allowed = required | ToyotaFlags.HYBRID | ToyotaFlags.HAS_BSM

  if (not cp.pcmCruise or not cp.secOcRequired or not cp.secOcKeyAvailable or cp.alternativeExperience not in (0, 32) or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or len(cp.safetyConfigs) != 1 or
      cp.flags & required != required or int(cp.flags) & ~int(allowed)):
    return False
  config = cp.safetyConfigs[0]
  expected = EPS_SCALE[CAR.TOYOTA_SIENNA_4TH_GEN] | int(ToyotaSafetyFlags.SECOC)
  return config.safetyModel == structs.CarParams.SafetyModel.toyota and config.safetyParam == expected


def development_enabled(cp) -> bool:
  return os.getenv('TOYOTA_LONG_OUTPUT_REPLAY_RUNTIME') == '1' and eligible(cp)


def _finite(value: object, low: float, high: float) -> bool:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return False
  try:
    number = float(value)
  except (TypeError, ValueError, OverflowError):
    return False
  return math.isfinite(number) and low <= number <= high


def _interp(value: float, points: tuple[float, ...], outputs: tuple[float, ...]) -> float:
  return float(np.interp(value, points, outputs))


class ToyotaOutputPolicy:
  def __init__(self, cp):
    if not eligible(cp):
      raise ValueError('unsupported Toyota longitudinal controller')
    self.corolla = str(cp.carFingerprint) == COROLLA
    self.reset()

  def reset(self) -> None:
    self.filtered_target = 0.0
    self.initialized = False

  @staticmethod
  def _leads_valid(leads: tuple[Lead, ...] | None) -> bool:
    return bool(leads is not None and len(leads) <= 4 and all(
      type(lead) is Lead and type(lead.present) is bool and
      _finite(lead.distance_m, 0.0, 300.0) and _finite(lead.lateral_m, -20.0, 20.0) and
      _finite(lead.speed_mps, -50.0, 80.0) and _finite(lead.accel_mps2, -20.0, 20.0)
      for lead in leads))

  def hold_stopped_lead(self, speed_mps, leads):
    return bool(self.corolla and math.isfinite(speed_mps) and speed_mps <= 0.5 and
                self._leads_valid(leads) and any(
                  lead.present and 0 < lead.distance_m <= 8.0 and
                  abs(lead.lateral_m) <= 1.75 and abs(lead.speed_mps) <= 0.35 for lead in leads))

  def target(self, a_target: float, v_ego: float, should_stop: bool, last_output_accel: float,
             *, leads: tuple[Lead, ...] | None = None) -> float | None:
    """Return a shaped target, or None for invalid/missing required evidence."""
    if (not _finite(a_target, _ACCEL_MIN, _ACCEL_MAX) or not _finite(v_ego, -math.inf, 70.0) or
        type(should_stop) is not bool or not _finite(last_output_accel, _ACCEL_MIN, _ACCEL_MAX)):
      self.reset()
      return None
    if self.corolla:
      return self._corolla(float(a_target), float(v_ego), should_stop, float(last_output_accel))
    if not self._leads_valid(leads):
      self.reset()
      return None
    assert leads is not None
    return self._sienna(float(a_target), float(v_ego), should_stop, leads)

  def _corolla(self, target: float, speed: float, should_stop: bool, previous_output: float) -> float:
    if should_stop or speed >= 3.0:
      self.reset()
      return target
    if not self.initialized:
      self.filtered_target = previous_output
      self.initialized = True
    if target <= -0.75 or target < self.filtered_target - 0.45:
      self.filtered_target = target
      return target
    tau = 0.18 if target < self.filtered_target else 0.30
    self.filtered_target += DT_CTRL / (tau + DT_CTRL) * (target - self.filtered_target)
    return self.filtered_target

  def _sienna(self, target: float, speed: float, should_stop: bool, leads: tuple[Lead, ...]) -> float:
    if should_stop:
      self.reset()
      return target
    centered = [lead for lead in leads if lead.present and abs(lead.lateral_m) <= 1.75 and lead.distance_m > 0.0]
    lead = min(centered, key=lambda item: item.distance_m) if centered else None
    comfort = False
    if lead is not None and speed >= 1.0:
      closing = max(0.0, speed - max(lead.speed_mps, 0.0))
      ttc = lead.distance_m / max(closing, 0.1) if closing > 0.1 else math.inf
      comfort = (lead.distance_m >= 7.0 and ttc >= 4.5 and closing <= 4.0 and
                 max(0.0, -lead.accel_mps2) <= 2.5)
    if speed < 12.0 and not comfort:
      if target > 0.0:
        if not self.initialized or (self.filtered_target < 0.0 and lead is None):
          self.filtered_target = 0.0
          self.initialized = True
        self.filtered_target += DT_CTRL / (0.50 + DT_CTRL) * (target - self.filtered_target)
        return self._cap_sienna_departure(self.filtered_target, speed, leads)
      if lead is not None:
        self.filtered_target = target
        self.initialized = True
        return target
      self.reset()
      return target
    bypass = target <= -2.5 if comfort else (
      target <= -0.75 or self.initialized and target < self.filtered_target - 0.65)
    if not self.initialized or bypass:
      self.filtered_target = target
      self.initialized = True
    else:
      tau = 0.24 if target < self.filtered_target else 0.32
      self.filtered_target += DT_CTRL / (tau + DT_CTRL) * (target - self.filtered_target)
    return self._cap_sienna_departure(self.filtered_target, speed, leads)

  @staticmethod
  def _cap_sienna_departure(target: float, speed: float, leads: tuple[Lead, ...]) -> float:
    if target <= 0.0 or speed >= 8.0:
      return target
    for lead in leads:
      if (lead.present and abs(lead.lateral_m) <= 1.75 and 0.0 < lead.distance_m <= 30.0 and
          lead.speed_mps > speed + 0.25):
        cap = _interp(speed, (0.0, 1.0, 3.0, 6.0, 8.0), (1.0, 1.15, 1.35, 1.55, 1.70))
        return min(target, cap)
    return target
