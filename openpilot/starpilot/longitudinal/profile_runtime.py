"""Validated saved longitudinal profiles resolved for the native planner."""

from __future__ import annotations

import math
from dataclasses import astuple, dataclass

from openpilot.common.constants import CV
from opendbc.car.structs import car
from opendbc.car.interfaces import ACCEL_MIN, ACCEL_MAX
from openpilot.starpilot.longitudinal.accel_profile import A_CRUISE_MAX_VALS_TRAFFIC_ALL, interpolate_accel_profile
from openpilot.starpilot.longitudinal.profile_document import (
  DEFAULT_DECELERATION_PROFILE, active_personality_id, interpolate_category_curve, is_truck_fingerprint,
)
from openpilot.starpilot.longitudinal.profile_preferences import (
  FOLLOW_MIN_SECONDS, FOLLOW_MAX_SECONDS, NAMES, SCALARS, TRAFFIC_JERK_SUFFIXES,
  read_document_value, read_master_value, read_profile_health, read_traffic_health,
)

JERK_MIN = 0.25
JERK_MAX = 2.0
LEGACY_FOLLOW_BP = (45.0 * CV.MPH_TO_MS, 70.0 * CV.MPH_TO_MS)
TRAFFIC_BP = (0.0, 25.0)  # m/s, not mph.
TRAFFIC_CRUISE_BRAKE_MAGNITUDE = 1.2 * 0.35  # Magnitude of A_CRUISE_MIN * 0.35.


@dataclass(frozen=True)
class ProfileSettings:
  document: dict | None
  profile_enabled: dict[str, bool]
  follow: dict[str, tuple[float, float]]
  jerk: dict[str, tuple[float, float, float, float, float]]


@dataclass(frozen=True)
class TrafficSettings:
  valid: bool
  reason: str
  document: dict | None
  profile_enabled: bool
  follow: tuple[float, float]
  jerk: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ProfileTuning:
  personality_id: str
  follow_seconds: float
  acceleration_jerk: float
  deceleration_jerk: float
  speed_jerk: float
  speed_decrease_jerk: float
  danger_jerk: float
  acceleration_max: float | None = None
  cruise_brake_magnitude: float | None = None
  traffic_braking_custom: bool = False
  custom_acceleration: bool = False


@dataclass(frozen=True)
class AppliedProfile:
  follow_seconds: float
  acceleration_max: float
  cruise_brake_magnitude: float
  acceleration_jerk: float
  deceleration_jerk: float
  speed_jerk: float
  speed_decrease_jerk: float
  danger_jerk: float


def _move(current: float, target: float, step: float) -> float:
  return min(max(target, current - step), current + step)


class ProfileSmoother:
  """Bound live setting/personality changes and fade invalid input to stock."""
  def __init__(self):
    self.applied: AppliedProfile | None = None

  def sample(self, target: ProfileTuning | None, personality, dt: float, *, traffic_mode: bool | None = False) -> AppliedProfile | None:
    if type(dt) not in (int, float) or not math.isfinite(dt) or not 0.0 < dt <= 0.25:
      self.applied = None
      return None
    if type(traffic_mode) is not bool:
      self.applied = None
      return None
    personality_id = active_personality_id(traffic_mode, personality)
    if personality_id not in ('traffic', 'aggressive', 'standard', 'relaxed'):
      self.applied = None
      return None
    if target is not None:
      values = (target.follow_seconds, target.acceleration_jerk, target.deceleration_jerk,
                target.speed_jerk, target.speed_decrease_jerk, target.danger_jerk)
      valid = (target.personality_id == personality_id and
               type(target.traffic_braking_custom) is bool and
               all(type(value) in (int, float) and math.isfinite(value) for value in values) and
               FOLLOW_MIN_SECONDS <= target.follow_seconds <= FOLLOW_MAX_SECONDS and
               all(JERK_MIN <= value <= JERK_MAX for value in values[1:]) and
               (target.acceleration_max is None or
                (type(target.acceleration_max) in (int, float) and math.isfinite(target.acceleration_max) and
                 0.0 <= target.acceleration_max <= ACCEL_MAX)) and
               (target.cruise_brake_magnitude is None or
                (type(target.cruise_brake_magnitude) in (int, float) and math.isfinite(target.cruise_brake_magnitude) and
                 (0.35 if traffic_mode else 0.5) <= target.cruise_brake_magnitude <= abs(ACCEL_MIN))))
      if not valid:
        target = None
    if traffic_mode and target is None:
      self.applied = None
      return None
    default_follow = {'traffic': 0.75, 'aggressive': 1.25, 'standard': 1.45, 'relaxed': 1.75}[personality_id]
    default_jerk = 0.5 if personality_id == 'aggressive' else 1.0
    default = AppliedProfile(default_follow, ACCEL_MAX, 1.2, default_jerk, default_jerk,
                             default_jerk, default_jerk, 1.0)
    if target is None and self.applied is None:
      return None
    if target is None:
      desired = default
    else:
      desired = AppliedProfile(target.follow_seconds,
                               min(target.acceleration_max if target.acceleration_max is not None else ACCEL_MAX, ACCEL_MAX),
                               min(target.cruise_brake_magnitude if target.cruise_brake_magnitude is not None else 1.2,
                                   abs(ACCEL_MIN)),
                               target.acceleration_jerk, target.deceleration_jerk,
                               target.speed_jerk, target.speed_decrease_jerk, target.danger_jerk)
    if self.applied is None and traffic_mode:
      # The first Traffic frame fades from the ordinary native personality,
      # not from Traffic's low-speed endpoint (which may be below this target).
      ordinary_id = active_personality_id(False, personality)
      ordinary_follow = {'aggressive': 1.25, 'standard': 1.45, 'relaxed': 1.75}.get(ordinary_id)
      if ordinary_follow is None:
        return None
      ordinary_jerk = 0.5 if ordinary_id == 'aggressive' else 1.0
      previous = AppliedProfile(ordinary_follow, ACCEL_MAX, 1.2, ordinary_jerk, ordinary_jerk,
                                ordinary_jerk, ordinary_jerk, 1.0)
    else:
      previous = self.applied or default
    follow_step = max(dt, 0.0) * 1.0
    accel_step = max(dt, 0.0) * 2.0
    jerk_step = max(dt, 0.0) * 2.0
    self.applied = AppliedProfile(
      _move(previous.follow_seconds, desired.follow_seconds, follow_step),
      _move(previous.acceleration_max, desired.acceleration_max, accel_step),
      _move(previous.cruise_brake_magnitude, desired.cruise_brake_magnitude, accel_step),
      _move(previous.acceleration_jerk, desired.acceleration_jerk, jerk_step),
      _move(previous.deceleration_jerk, desired.deceleration_jerk, jerk_step),
      _move(previous.speed_jerk, desired.speed_jerk, jerk_step),
      _move(previous.speed_decrease_jerk, desired.speed_decrease_jerk, jerk_step),
      _move(previous.danger_jerk, desired.danger_jerk, jerk_step),
    )
    if target is None and all(abs(a - b) < 1e-8 for a, b in zip(astuple(self.applied), astuple(default), strict=True)):
      self.applied = None
    return self.applied


def read_settings(params) -> ProfileSettings | None:
  """A saved preference is inert unless the host explicitly opts into this runtime."""
  master = read_master_value(params)
  if not master.valid or master.value is not True:
    return None
  health = read_profile_health(params, master=master)
  if not health.dependencies_valid:
    return None
  try:
    profile_enabled = dict.fromkeys(NAMES, True)
    follow = {name: (health.number(SCALARS[name][0]), health.number(SCALARS[name][1])) for name in NAMES}
    jerk = {name: (health.number(SCALARS[name][2]) / 100.0, health.number(SCALARS[name][3]) / 100.0,
                   health.number(SCALARS[name][4]) / 100.0, health.number(SCALARS[name][5]) / 100.0,
                   health.number(SCALARS[name][6]) / 100.0) for name in NAMES}
    return ProfileSettings(health.document, profile_enabled, follow, jerk)
  except ValueError:
    return None


def read_traffic_settings(params) -> TrafficSettings:
  health = read_traffic_health(params)
  if not health.valid:
    return TrafficSettings(False, health.reason, None, False, (0.75, 1.6), ())
  follow = (health.number('TrafficFollow'), health.number('RelaxedFollow'))
  jerk = tuple((health.number('Traffic' + suffix) / 100.0,
                health.number('Relaxed' + suffix) / 100.0) for suffix in TRAFFIC_JERK_SUFFIXES)
  return TrafficSettings(True, 'valid', health.document if health.master_on else None,
                         health.profile_enabled if health.master_on else False, follow, jerk)


def _traffic_value(points: tuple[float, float], speed: float) -> float:
  ratio = min(max((speed - TRAFFIC_BP[0]) / (TRAFFIC_BP[1] - TRAFFIC_BP[0]), 0.0), 1.0)
  return float(points[0] + (points[1] - points[0]) * ratio)


def resolve(settings: ProfileSettings | TrafficSettings | None, personality, v_ego: float, CP,
            *, traffic_mode: bool | None = False) -> ProfileTuning | None:
  if settings is None or type(traffic_mode) is not bool or not math.isfinite(v_ego) or v_ego < 0:
    return None
  personality_id = active_personality_id(traffic_mode, personality)
  if traffic_mode:
    if not isinstance(settings, TrafficSettings) or not settings.valid or len(settings.jerk) != 5:
      return None
    follow = _traffic_value(settings.follow, v_ego)
    acceleration_max = interpolate_accel_profile(v_ego, A_CRUISE_MAX_VALS_TRAFFIC_ALL)
    cruise_brake_magnitude = TRAFFIC_CRUISE_BRAKE_MAGNITUDE
    traffic_braking_custom = False
    document = settings.document
    if document is not None and document['enabled'] and settings.profile_enabled:
      categories = document['profiles']['traffic']
      ev_tuning = CP.transmissionType == car.CarParams.TransmissionType.direct
      truck_tuning = is_truck_fingerprint(str(CP.carFingerprint)) and not ev_tuning
      for category in ('following', 'acceleration', 'braking'):
        config = categories[category]
        if config['preset'] in ('dom_default', 'selected_profile') or (category != 'following' and not config.get('legacyActivation', False)):
          continue
        value = interpolate_category_curve(category, v_ego, config, ev_tuning, truck_tuning)
        if category == 'following':
          follow = value
        elif category == 'acceleration':
          acceleration_max = value
        else:
          cruise_brake_magnitude = value
          traffic_braking_custom = True
    if (not all(math.isfinite(value) for value in (follow, acceleration_max, cruise_brake_magnitude)) or
        not FOLLOW_MIN_SECONDS <= follow <= FOLLOW_MAX_SECONDS or
        acceleration_max < 0.0 or not 0.35 <= cruise_brake_magnitude <= abs(ACCEL_MIN)):
      return None
    acceleration, deceleration, speed, speed_decrease, danger = (
      _traffic_value(points, v_ego) for points in settings.jerk
    )
    return ProfileTuning('traffic', follow, acceleration, deceleration, speed, speed_decrease, danger,
                         min(acceleration_max, ACCEL_MAX), cruise_brake_magnitude,
                         traffic_braking_custom=traffic_braking_custom)
  if not isinstance(settings, ProfileSettings):
    return None
  if personality_id not in settings.follow:
    return None
  low, high = settings.follow[personality_id]
  follow = float(low + (high - low) * min(max((v_ego - LEGACY_FOLLOW_BP[0]) /
                                             (LEGACY_FOLLOW_BP[1] - LEGACY_FOLLOW_BP[0]), 0.0), 1.0))
  acceleration_max = None
  cruise_brake_magnitude = None
  custom_acceleration = False
  document = settings.document
  if document is not None and document['enabled'] and settings.profile_enabled[personality_id]:
    categories = document['profiles'][personality_id]
    ev_tuning = CP.transmissionType == car.CarParams.TransmissionType.direct
    truck_tuning = is_truck_fingerprint(str(CP.carFingerprint)) and not ev_tuning
    for category in ('following', 'acceleration', 'braking'):
      config = categories[category]
      if config['preset'] in ('dom_default', 'selected_profile') or (category != 'following' and not config.get('legacyActivation', False)):
        continue
      value = interpolate_category_curve(category, v_ego, config, ev_tuning, truck_tuning)
      if not math.isfinite(value):
        return None
      if category == 'following':
        follow = value
      elif category == 'acceleration':
        custom_acceleration = config['preset'] == 'custom'
        acceleration_max = min(max(value, 0.0), ACCEL_MAX)
      else:
        cruise_brake_magnitude = min(max(value, 0.5), abs(ACCEL_MIN))
  acceleration_jerk, deceleration_jerk, speed_jerk, speed_decrease_jerk, danger_jerk = settings.jerk[personality_id]
  return ProfileTuning(personality_id, min(max(follow, FOLLOW_MIN_SECONDS), FOLLOW_MAX_SECONDS),
                       acceleration_jerk, deceleration_jerk, speed_jerk, speed_decrease_jerk, danger_jerk,
                       acceleration_max, cruise_brake_magnitude, custom_acceleration=custom_acceleration)


@dataclass(frozen=True)
class SelectedProfileTuning:
  acceleration_max: float | None = None
  cruise_brake_magnitude: float | None = None
  braking_style: str = 'standard'
  extra_stopped_gap_m: float = 0.0


@dataclass(frozen=True)
class GlobalPowertrainPreset:
  ev: bool
  truck: bool


def read_global_powertrain_preset(params, CP) -> GlobalPowertrainPreset | None:
  from opendbc.car.gm.feature_capabilities import longitudinal_supported
  from opendbc.car.gm.truck_longitudinal import truck_tuning_supported
  from openpilot.starpilot.saved_source import read_saved
  if not longitudinal_supported(CP):
    return None
  try:
    ev, ev_readable = read_saved(params, 'EVTuning', 8)
    truck, truck_readable = read_saved(params, 'TruckTuning', 8)
    if not ev_readable or not truck_readable or ev not in (None, b'0', b'1') or truck not in (None, b'0', b'1'):
      return None
    if not truck_tuning_supported(CP):
      truck = None  # A saved choice from another truck cannot activate this owner.
    if ev is None and truck is None:
      return None  # Absence preserves the existing final-CP response.
    truck_selected = truck == b'1' and truck_tuning_supported(CP)
    return GlobalPowertrainPreset((CP.transmissionType == car.CarParams.TransmissionType.direct if ev is None else ev == b'1') and not truck_selected,
                                  truck_selected)
  except (OSError, RuntimeError, TypeError, ValueError):
    return None


def selected_profiles_requested(document: dict | None) -> bool:
  if document is None:
    return False
  if (document.get('extraStoppedGapM', 0.) > 0 or document['selectedAccelerationProfile'] != 'dom_default' or
      document['selectedDecelerationProfile'] != 'dom_default'):
    return True
  return any(config['preset'] not in ('dom_default', 'selected_profile') and not config.get('legacyActivation', False)
             for profile in document['profiles'].values() for category, config in profile.items() if category != 'following')


def resolve_selected_profiles(document: dict | None, personality, v_ego: float, CP, *, traffic_mode: bool | None = False,
                              legacy: ProfileTuning | None = None, global_powertrain: GlobalPowertrainPreset | None = None) -> SelectedProfileTuning | None:
  """Resolve only acceleration/braking; never enable following or jerk tuning.

  Migration provenance preserves the former master/curve gates until the user
  explicitly selects inheritance or an override. Global deceleration retains
  the historical response scale; explicit personality curves retain theirs.
  """
  identity = active_personality_id(traffic_mode, personality)
  if document is None or identity is None or not math.isfinite(v_ego) or v_ego < 0:
    return None
  ev = CP.transmissionType == car.CarParams.TransmissionType.direct
  truck = is_truck_fingerprint(str(CP.carFingerprint)) and not ev
  acceleration = braking = None
  style = 'standard'
  for category in ('acceleration', 'braking'):
    config = document['profiles'][identity][category]
    preset = config['preset']
    inherited = preset == 'selected_profile'
    if config.get('legacyActivation', False):
      # Old active overrides are already applied by the original smoother.
      active_value = getattr(legacy, 'acceleration_max' if category == 'acceleration' else 'cruise_brake_magnitude', None)
      if active_value is not None or category == 'acceleration' or traffic_mode is True:
        continue
      inherited = True  # Old global response was ordinary-cruise-only.
    if inherited:
      preset = document['selectedAccelerationProfile' if category == 'acceleration' else 'selectedDecelerationProfile']
    if preset == 'dom_default':
      if category == 'acceleration' and inherited and traffic_mode is False and global_powertrain is not None:
        preset = 'standard'  # Original explicit EV/Truck choice owns the global Standard fallback.
      else:
        continue
    if category == 'braking' and inherited:
      value = {'standard': 1.2, 'eco': 0.6, 'sport': 2.4}[preset]
    else:
      selected_ev, selected_truck = ev, truck
      if category == 'acceleration' and inherited and traffic_mode is False and global_powertrain is not None:
        selected_ev, selected_truck = global_powertrain.ev, global_powertrain.truck
      value = interpolate_category_curve(category, v_ego,
                                        {'preset': preset, 'curve': []} if inherited else config, selected_ev, selected_truck)
    if not math.isfinite(value):
      return None
    if category == 'acceleration':
      acceleration = min(max(value, 0.0), ACCEL_MAX)
    else:
      braking = min(max(value, 0.35 if traffic_mode else 0.5), abs(ACCEL_MIN))
      style = preset if preset in ('eco', 'sport') else 'standard'
  return SelectedProfileTuning(acceleration, braking, style, document.get("extraStoppedGapM", 0.))


class ProfileHost:
  """Bounded local Params refresh; a failed read expires the previous tuning."""
  REFRESH_NS = 1_000_000_000
  MAX_AGE_NS = 2_000_000_000

  def __init__(self, params):
    self.params = params
    self.disabled = False
    self.selected_document: dict | None = None
    self.selected_powertrain: GlobalPowertrainPreset | None = None
    self.selected_attempt_ns = -self.REFRESH_NS
    self.selected_success_ns = -1
    self.settings: ProfileSettings | TrafficSettings | None = None
    self.last_attempt_ns = -self.REFRESH_NS
    self.last_success_ns = -1

    self.last_mode: bool | None = None
    self.global_braking_response: str | None = None
    self.global_last_attempt_ns = -self.REFRESH_NS
    self.global_last_success_ns = -1

  def sample_selected(self, now_ns: int, personality, v_ego: float, CP, *, traffic_mode: bool | None = False,
                      legacy: ProfileTuning | None = None) -> SelectedProfileTuning | None:
    if now_ns < 0 or now_ns < self.selected_attempt_ns:
      self.selected_document = None
      self.selected_powertrain = None
      self.selected_attempt_ns = now_ns - self.REFRESH_NS
      self.selected_success_ns = -1
    if now_ns - self.selected_attempt_ns >= self.REFRESH_NS:
      self.selected_attempt_ns = now_ns
      try:
        saved = read_document_value(self.params)
        self.selected_document = saved.value if saved.valid and isinstance(saved.value, dict) else None
        self.selected_powertrain = read_global_powertrain_preset(self.params, CP)
        self.selected_success_ns = now_ns if saved.valid else -1
      except (OSError, RuntimeError, ValueError, TypeError, KeyError):
        self.selected_document = None
        self.selected_powertrain = None
        self.selected_success_ns = -1
    if self.selected_success_ns < 0 or now_ns - self.selected_success_ns > self.MAX_AGE_NS:
      return None
    return resolve_selected_profiles(self.selected_document, personality, v_ego, CP,
                                     traffic_mode=traffic_mode, legacy=legacy, global_powertrain=self.selected_powertrain)

  def sample_global_braking(self, now_ns: int) -> str | None:
    if now_ns < 0 or now_ns < self.global_last_attempt_ns:
      self.global_braking_response = None
      self.global_last_attempt_ns = now_ns - self.REFRESH_NS
      self.global_last_success_ns = -1
    if now_ns - self.global_last_attempt_ns >= self.REFRESH_NS:
      self.global_last_attempt_ns = now_ns
      try:
        saved = read_document_value(self.params)
        if saved.valid and saved.value is None:
          self.global_braking_response = DEFAULT_DECELERATION_PROFILE
        elif saved.valid and isinstance(saved.value, dict):
          response = saved.value.get("selectedDecelerationProfile")
          self.global_braking_response = response if isinstance(response, str) else None
        else:
          self.global_braking_response = None
        self.global_last_success_ns = now_ns if self.global_braking_response is not None else -1
      except (OSError, RuntimeError, ValueError, TypeError, KeyError):
        self.global_braking_response = None
        self.global_last_success_ns = -1
    if self.global_last_success_ns < 0 or now_ns - self.global_last_success_ns > self.MAX_AGE_NS:
      return None
    return self.global_braking_response

  def sample(self, now_ns: int, personality, v_ego: float, CP, *, traffic_mode: bool | None = False) -> ProfileTuning | None:
    if type(traffic_mode) is not bool:
      self.disabled = False
      self.settings = None
      self.last_attempt_ns = now_ns - self.REFRESH_NS
      self.last_success_ns = -1
      self.last_mode = None
      return None
    if self.last_mode is not traffic_mode:
      self.settings = None
      self.last_attempt_ns = now_ns - self.REFRESH_NS
      self.last_success_ns = -1
      self.last_mode = traffic_mode
    if now_ns < 0 or now_ns < self.last_attempt_ns:
      self.disabled = False
      self.settings = None
      self.last_attempt_ns = now_ns - self.REFRESH_NS
      self.last_success_ns = -1
    if now_ns - self.last_attempt_ns >= self.REFRESH_NS:
      self.last_attempt_ns = now_ns
      try:
        master = read_master_value(self.params)
        self.disabled = (not traffic_mode and master.valid and master.value is False and
                         read_document_value(self.params).valid)
        self.settings = read_traffic_settings(self.params) if traffic_mode else read_settings(self.params)
        self.last_success_ns = now_ns
      except (OSError, RuntimeError, ValueError, TypeError):
        self.disabled = False
        self.settings = None
    if self.last_success_ns < 0 or now_ns - self.last_success_ns > self.MAX_AGE_NS:
      self.disabled = False
      return None
    return resolve(self.settings, personality, v_ego, CP, traffic_mode=traffic_mode)
