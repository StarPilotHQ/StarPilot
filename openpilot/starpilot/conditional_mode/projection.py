"""Read-only current-message projection for a future conditional mode owner.

No projected diagnostic below silently becomes a frozen CEM/CCM detector result.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import re
import time

from openpilot.cereal.services import SERVICE_LIST
from opendbc.car import structs
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.selfdrive.controls.lib.desire_helper import LANE_CHANGE_SPEED_MIN
from openpilot.starpilot.conditional_mode.chill_scene import ChillLead, ChillSceneDetector, ChillSceneFrame, ChillSceneObservation
from openpilot.starpilot.conditional_mode.curve import CurveDetector, frozen_raw_curve
from openpilot.starpilot.lead_tracking import LeadDetector, LeadObservation
from openpilot.starpilot.conditional_mode.policy import Authority, LeadEvidence, SceneEvidence
from openpilot.starpilot.conditional_mode.preferences import CEMOptions
from openpilot.starpilot.conditional_mode.signal_lane import MonoSource, SignalLaneFrame, SignalLaneTracker
from openpilot.starpilot.conditional_mode.slower_lead import SlowerLeadDetector, SlowerLeadFrame
from openpilot.starpilot.conditional_mode.stop import FORD_STOP_NAMES, StopFrame, StopLead, StopLightDetector, StopObservation
from openpilot.starpilot.conditional_mode.turn_scene import committed_turn_scene
from openpilot.starpilot.model_geometry import (
  STANDSTILL_MODEL_PREFIX_NOISE_M as STANDSTILL_MODEL_PREFIX_NOISE_M, STANDSTILL_SPEED_NOISE_MPS, normalized_origin,
  standstill_clear_horizon as _standstill_clear_horizon, standstill_release_speed,
)


SERVICES = ('carState', 'carControl', 'selfdriveState', 'controlsState', 'radarState', 'modelV2', 'longitudinalPlan', 'starpilotRadarState')
SOURCE_MAX_AGE_NS = 250_000_000
MODEL_EOF_MAX_AGE_NS = 150_000_000
CLOCK_PAIR_MAX_SKEW_NS = 1_000_000
KPH_TO_MPS = 1 / 3.6
V_CRUISE_UNSET_KPH = 255.0
FROZEN_CURVE_ACCEL_MPS2 = 1.3
# Recorded standstill paths dip up to 0.46 m below their running max and 0.15 m below zero.
STANDSTILL_PATH_JITTER_M = 0.5
FROZEN_RAW_STOP_DISTANCE_M = 5.0 * ModelConstants.T_IDXS[-1]


@dataclass(frozen=True)
class RawLead:
  """RadarState leadOne values, independent of qualified tracking/following."""

  present: bool
  radar: bool
  distance_m: float | None
  speed_mps: float | None
  accel_mps2: float | None
  model_probability: float | None
  relative_speed_mps: float | None = None


@dataclass(frozen=True)
class ObservedBool:
  value: bool
  observed_mono_ns: int


@dataclass(frozen=True)
class ObservedFloat:
  value: float
  observed_mono_ns: int


@dataclass(frozen=True)
class ConditionalOwnerContext:
  """Explicit owner evidence; every observation retains its original clock stamp.

  The caller must sample live option/scene owners without substituting an old
  saved preference or the time of a repeated SubMaster poll for that stamp.
  """

  traffic_mode: ObservedBool | None = None
  stop_sign_confirmed: ObservedBool | None = None
  forcing_stop: ObservedBool | None = None
  dashboard_stop_sign: ObservedBool | None = None
  pedal_override: ObservedBool | None = None
  model_stop_time_s: ObservedFloat | None = None
  slower_option: ObservedBool | None = None
  stopped_option: ObservedBool | None = None
  previous_experimental: ObservedBool | None = None
  committed_turn_scene: ObservedBool | None = None
  red_light: ObservedBool | None = None
  plan_forcing_stop: ObservedBool | None = None
  plan_should_stop: ObservedBool | None = None
  plan_allow_throttle: ObservedBool | None = None


@dataclass(frozen=True)
class ProjectedScene:
  scene: SceneEvidence
  authority: Authority | None
  fresh_services: frozenset[str]
  raw_lead: RawLead | None
  lead_observation: LeadObservation
  raw_driving_in_curve: bool | None
  raw_road_curve: bool | None
  model_horizon_m: float | None
  raw_model_stopped: bool | None
  current_model_should_stop: bool | None
  ready: bool
  stop_source_observed_mono_s: float | None = None
  slower_source_observed_mono_s: float | None = None
  chill_source_observed_mono_s: float | None = None
  chill_observation: ChillSceneObservation | None = None


def paired_clocks_ns() -> tuple[int, int, int] | None:
  """The model camera EOF is BOOTTIME; event/receipt stamps are MONOTONIC."""
  boot_clock = getattr(time, 'CLOCK_BOOTTIME', None)
  if boot_clock is None:
    return None
  for _ in range(2):
    try:
      before = time.monotonic_ns()
      boot = time.clock_gettime_ns(boot_clock)
      after = time.monotonic_ns()
    except OSError:
      return None
    skew = after - before
    if skew < 0:
      return None
    if skew <= CLOCK_PAIR_MAX_SKEW_NS:
      return (before + after) // 2, boot, skew
    # Scheduling can interrupt the sampler itself. Discard that measurement
    # and take one new pair; never widen the error bound or reuse a prior pair.
  return None


def _number(value: object, *, low: float = 0.0, high: float = 500.0) -> float | None:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return None
  try:
    number = float(value)
  except (OverflowError, ValueError):
    return None
  return number if math.isfinite(number) and low <= number <= high else None


def _boolean(value: object) -> bool | None:
  return value if type(value) is bool else None


def _field(source, field: str):
  try:
    return getattr(source, field)
  except (AttributeError, ValueError, RuntimeError):
    return None


def _ego_speed(car) -> float | None:
  speed = _number(_field(car, 'vEgo'), low=-STANDSTILL_SPEED_NOISE_MPS, high=80.0)
  if speed is None or speed >= 0.0:
    return speed
  # The wheel-speed Kalman estimate can undershoot zero while physically stopped.
  # Require both standstill and exactly zero raw wheel speed; moving or materially
  # negative estimates remain unavailable rather than gaining scene authority.
  if _field(car, 'standstill') is True and _number(_field(car, 'vEgoRaw'), high=80.0) == 0.0:
    return 0.0
  return None


def _fresh(sm, service: str, now_mono_ns: int, barrier_ns: int) -> bool:
  try:
    stamp = sm.logMonoTime[service]
    receipt = sm.recv_time[service]
    frequency = SERVICE_LIST[service].frequency
    limit = min(SOURCE_MAX_AGE_NS, max(round(DT_MDL * 1e9), round(2e9 / frequency))) if frequency > 0 else 0
    if type(stamp) is not int or not isinstance(receipt, (int, float)) or isinstance(receipt, bool):
      return False
    receipt_ns = int(receipt * 1e9)
    return (
      sm.seen[service]
      and sm.alive[service]
      and sm.valid[service]
      and barrier_ns < stamp <= now_mono_ns
      and barrier_ns < receipt_ns <= now_mono_ns
      and now_mono_ns - stamp <= limit
      and now_mono_ns - receipt_ns <= limit
    )
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return False


def _source_stamp(sm, service: str) -> int:
  return min(sm.logMonoTime[service], int(sm.recv_time[service] * 1e9))


def _owner_bool(source: ObservedBool | None, now_ns: int, barrier_ns: int) -> tuple[bool | None, int | None]:
  if not isinstance(source, ObservedBool) or type(source.value) is not bool or type(source.observed_mono_ns) is not int:
    return None, None
  stamp = source.observed_mono_ns
  if not barrier_ns < stamp <= now_ns or now_ns - stamp > SOURCE_MAX_AGE_NS:
    return None, None
  return source.value, stamp


def _owner_float(source: ObservedFloat | None, now_ns: int, barrier_ns: int, *, high: float) -> tuple[float | None, int | None]:
  if not isinstance(source, ObservedFloat) or type(source.observed_mono_ns) is not int:
    return None, None
  stamp = source.observed_mono_ns
  if not barrier_ns < stamp <= now_ns or now_ns - stamp > SOURCE_MAX_AGE_NS:
    return None, None
  return _number(source.value, high=high), stamp


def _horizon(model) -> float | None:
  try:
    distances = normalized_origin(tuple(model.position.x))
    if len(distances) != ModelConstants.IDX_N:
      return None
    parsed = tuple(_number(value, high=500.0) for value in distances)
    if any(value is None for value in parsed):
      return None
    finite = tuple(value for value in parsed if value is not None)
    if any(b < a for a, b in zip(finite, finite[1:], strict=False)):
      return None
    return min(finite[-1] + _bend_excess(model, finite), 500.0)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None


def _bend_excess(model, distances: tuple[float, ...]) -> float:
  """Arc length minus forward reach; a bend alone must not read as a shortening plan."""
  try:
    offsets = tuple(model.position.y)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return 0.0
  if len(offsets) != len(distances) or any(_number(value, low=-500.0, high=500.0) is None for value in offsets):
    return 0.0
  return sum(math.hypot(x1 - x0, y1 - y0) - (x1 - x0)
             for x0, x1, y0, y1 in zip(distances, distances[1:], offsets, offsets[1:], strict=False))


def _standstill_path_extent(model) -> float | None:
  """Uncommitted standstill hold only; too lenient for stop acquisition or release."""
  try:
    distances = normalized_origin(tuple(model.position.x))
    if len(distances) != ModelConstants.IDX_N:
      return None
    parsed = tuple(_number(value, low=-STANDSTILL_PATH_JITTER_M, high=500.0) for value in distances)
    if any(value is None for value in parsed):
      return None
    finite = tuple(value for value in parsed if value is not None)
    peak = 0.0
    for value in finite:
      peak = max(peak, value)
      if peak - value > STANDSTILL_PATH_JITTER_M:
        return None
    return peak
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None


def _stationary_launch_clear(model, car, lead: RawLead | None, horizon: float | None) -> bool:
  """Model launch proof for a stopped car; no confidence/color inference."""
  speed = _ego_speed(car)
  if (horizon is None or speed is None or speed > STANDSTILL_SPEED_NOISE_MPS or
      _field(car, 'standstill') is not True or _number(_field(car, 'vEgoRaw'), high=80.0) != 0.0 or
      lead is None or lead.present or _boolean(_field(_field(model, 'action'), 'shouldStop')) is not False):
    return False
  acceleration = _number(_field(_field(model, 'action'), 'desiredAcceleration'), low=-10.0, high=10.0)
  if acceleration is None or acceleration < 0.3:
    return False
  try:
    velocities = tuple(model.velocity.x)
    if len(velocities) != ModelConstants.IDX_N:
      return False
    parsed = tuple(_number(value, low=-0.05, high=100.0) for value in velocities)
    if any(value is None for value in parsed):
      return False
    finite = tuple(value for value in parsed if value is not None)
    tail = tuple(value for value, timestamp in zip(finite, ModelConstants.T_IDXS, strict=True) if timestamp >= 2.0)
    return (bool(tail) and all(value >= 0.3 for value in tail) and
            all(b >= a for a, b in zip(tail, tail[1:], strict=False)) and tail[-1] >= 5.0)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return False


def _lead(radar) -> RawLead | None:
  lead = _field(radar, 'leadOne')
  present = _boolean(_field(lead, 'present'))
  radar_match = _boolean(_field(lead, 'radar'))
  if present is None or radar_match is None:
    return None
  if not present:
    return RawLead(False, radar_match, None, None, None, None)
  distance = _number(_field(lead, 'dRel'))
  speed = _number(_field(lead, 'vLead'), low=-30.0, high=100.0)
  acceleration = _number(_field(lead, 'aLeadK'), low=-20.0, high=20.0)
  probability = _number(_field(lead, 'modelProb'), high=1.0)
  relative = _number(_field(lead, 'vRel'), low=-100.0, high=100.0)
  return RawLead(True, radar_match, distance, speed, acceleration, probability, relative)


class SceneProjector:
  """Stateful startup/resume barrier; no SubMaster socket ownership.

  The optional selected headway must come from the active longitudinal
  planner/MPC owner with that owner's same-cycle MONOTONIC observation stamp.
  Saved preference bytes and a receipt time fabricated by this projector are
  not evidence of the headway actually used for control.
  """

  def __init__(self):
    self.offset_ns: int | None = None
    self.sample_skew_ns = 0
    self.barrier_mono_ns = 0
    self.curve_detector = CurveDetector()
    self.lead_detector = LeadDetector()
    self.stop_detector = StopLightDetector()
    self.slower_lead_detector = SlowerLeadDetector()
    self.chill_detector = ChillSceneDetector()
    self.signal_lane_tracker = SignalLaneTracker()
    self.last_model_stamp_ns: int | None = None
    self.last_stop_stamp_s: float | None = None
    self.last_slower_stamp_s: float | None = None
    self.adjacent_session: str | None = None
    self.adjacent_sequence = 0
    self.adjacent_identity: tuple | None = None
    self.retired_adjacent_sessions: tuple[str, ...] = ()

  def _empty(self) -> ProjectedScene:
    self.curve_detector.reset()
    self.lead_detector.reset()
    self.stop_detector.invalidate()
    self.slower_lead_detector.reset()
    self.chill_detector.reset()
    self.signal_lane_tracker.reset()
    self.last_model_stamp_ns = None
    self.last_stop_stamp_s = None
    self.last_slower_stamp_s = None
    return ProjectedScene(SceneEvidence(), None, frozenset(), None, LeadObservation(None, None, None, None), None, None, None, None, None, False)

  def _adjacent_wire(self, sm, *, now_mono_ns: int, now_boot_ns: int, model, car) -> bool | None:
    """Consume only the new qualified envelope, never historical leadLeft/right."""
    try:
      if model is None or car is None or not sm.seen['starpilotRadarState'] or not sm.alive['starpilotRadarState']:
        return None
      envelope = sm['starpilotRadarState'].qualifiedAdjacent
      session = str(envelope.producerSessionId)
      sequence = int(envelope.sequence)
      status = str(envelope.status)
      event_stamp = sm.logMonoTime['starpilotRadarState']
      receipt_ns = int(sm.recv_time['starpilotRadarState'] * 1e9)
      if (envelope.version != 1 or not re.fullmatch(r'[0-9a-f]{32}', session) or sequence <= 0 or
          type(event_stamp) is not int or not self.barrier_mono_ns < event_stamp <= now_mono_ns or
          not self.barrier_mono_ns < receipt_ns <= now_mono_ns or
          now_mono_ns - event_stamp > SOURCE_MAX_AGE_NS or now_mono_ns - receipt_ns > SOURCE_MAX_AGE_NS or
          session in self.retired_adjacent_sessions or
          (session == self.adjacent_session and sequence < self.adjacent_sequence)):
        return None
      if status == 'unknown':
        if sm.valid['starpilotRadarState']:
          return None
        identity = (session, sequence, status)
        if sequence == self.adjacent_sequence and session == self.adjacent_session and identity != self.adjacent_identity:
          return None
        self._advance_adjacent(session, sequence, identity)
        return None
      if status not in ('clear', 'ambiguous') or not sm.valid['starpilotRadarState']:
        return None
      radar_ns, model_ns, car_ns, eof_ns = (int(envelope.radarTracksMonoTime), int(envelope.modelMonoTime),
                                            int(envelope.carStateMonoTime), int(envelope.cameraEofBootTime))
      observed_ns, expiry_ns = int(envelope.observedMonoTime), int(envelope.validUntilMonoTime)
      offset = self.offset_ns
      if (offset is None or any(stamp <= self.barrier_mono_ns or stamp > now_mono_ns for stamp in (radar_ns, model_ns, car_ns, observed_ns)) or
          not self.barrier_mono_ns + offset < eof_ns <= now_boot_ns or
          not 0 < expiry_ns - observed_ns <= SOURCE_MAX_AGE_NS or not observed_ns <= now_mono_ns <= expiry_ns or
          now_mono_ns - radar_ns > SOURCE_MAX_AGE_NS or now_mono_ns - model_ns > MODEL_EOF_MAX_AGE_NS or
          now_mono_ns - car_ns > SOURCE_MAX_AGE_NS or now_boot_ns - eof_ns > MODEL_EOF_MAX_AGE_NS or
          observed_ns > min(radar_ns, model_ns, car_ns, eof_ns - offset) or
          model_ns > sm.logMonoTime['modelV2'] or car_ns > sm.logMonoTime['carState'] or
          eof_ns > model.timestampEof or
          expiry_ns > min(radar_ns + SOURCE_MAX_AGE_NS, model_ns + MODEL_EOF_MAX_AGE_NS,
                          car_ns + SOURCE_MAX_AGE_NS, eof_ns - offset + MODEL_EOF_MAX_AGE_NS)):
        return None
      candidates = (envelope.left, envelope.right)
      for candidate in candidates:
        if candidate.present and (_number(candidate.distanceM, high=500.0) is None or
                                  _number(candidate.lateralM, low=-30.0, high=30.0) is None or
                                  _number(candidate.speedMps, low=-30.0, high=100.0) is None):
          return None
      identity = (session, sequence, status, radar_ns, model_ns, car_ns, eof_ns, observed_ns, expiry_ns,
                  *((bool(candidate.present), int(candidate.trackId), float(candidate.distanceM),
                     float(candidate.lateralM), float(candidate.speedMps)) for candidate in candidates))
      if session == self.adjacent_session and sequence == self.adjacent_sequence:
        if identity != self.adjacent_identity:
          return None
        return status == 'ambiguous'
      self._advance_adjacent(session, sequence, identity)
      return status == 'ambiguous'
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return None

  def _advance_adjacent(self, session: str, sequence: int, identity: tuple) -> None:
    if self.adjacent_session is not None and session != self.adjacent_session:
      self.retired_adjacent_sessions = (*self.retired_adjacent_sessions[-7:], self.adjacent_session)
    self.adjacent_session = session
    self.adjacent_sequence = sequence
    self.adjacent_identity = identity

  def project(
    self,
    sm,
    cp,
    *,
    now_mono_ns: int,
    now_boot_ns: int,
    sample_skew_ns: int,
    safe_mode: bool | None = None,
    selected_t_follow_s: float | None = None,
    selected_t_follow_observed_mono_ns: int | None = None,
    owner_context: ConditionalOwnerContext | None = None,
    signal_options: CEMOptions | None = None,
    cruise_ceiling_mps: float | None = None,
  ) -> ProjectedScene:
    if (
      type(now_mono_ns) is not int
      or type(now_boot_ns) is not int
      or type(sample_skew_ns) is not int
      or now_mono_ns <= 0
      or now_boot_ns <= 0
      or not 0 <= sample_skew_ns <= CLOCK_PAIR_MAX_SKEW_NS
    ):
      self.barrier_mono_ns = max(self.barrier_mono_ns, now_mono_ns if type(now_mono_ns) is int else 0)
      return self._empty()
    offset = now_boot_ns - now_mono_ns
    if self.offset_ns is None or abs(offset - self.offset_ns) > max(CLOCK_PAIR_MAX_SKEW_NS, self.sample_skew_ns + sample_skew_ns):
      self.offset_ns = offset
      self.sample_skew_ns = sample_skew_ns
      self.barrier_mono_ns = now_mono_ns
      return self._empty()
    self.sample_skew_ns = sample_skew_ns
    # The barrier is persistent: an optional missing radar/model service must
    # not block fresh car evidence, and a late pre-resume event must never be
    # accepted merely because other services have rearmed.
    fresh = frozenset(service for service in SERVICES if _fresh(sm, service, now_mono_ns, self.barrier_mono_ns))
    try:
      car = sm['carState'] if 'carState' in fresh else None
      controls = sm['controlsState'] if 'controlsState' in fresh else None
      control = sm['carControl'] if 'carControl' in fresh else None
      selfdrive = sm['selfdriveState'] if 'selfdriveState' in fresh else None
      radar = sm['radarState'] if 'radarState' in fresh else None
      model = sm['modelV2'] if 'modelV2' in fresh else None
      longitudinal_plan = sm['longitudinalPlan'] if 'longitudinalPlan' in fresh else None
    except (KeyError, TypeError, ValueError, OverflowError):
      return self._empty()
    speed = _ego_speed(car) if car is not None and _field(car, 'canValid') is True and _field(car, 'canTimeout') is False else None
    cruise_kph = _number(_field(car, 'vCruise'), high=V_CRUISE_UNSET_KPH) if speed is not None else None
    cruise = cruise_kph * KPH_TO_MPS if cruise_kph is not None and 0 < cruise_kph < V_CRUISE_UNSET_KPH else None
    if cruise_ceiling_mps is not None:
      cap = _number(cruise_ceiling_mps, high=V_CRUISE_UNSET_KPH * KPH_TO_MPS)
      cruise = min(cruise, cap) if cruise is not None and cap is not None and cap > 0 else None
    curvature = _number(_field(controls, 'curvature'), low=-1.0, high=1.0) if controls is not None else None
    raw_curve = abs(speed * speed * curvature) >= FROZEN_CURVE_ACCEL_MPS2 if speed is not None and curvature is not None else None
    horizon = None
    stop_horizon = None
    current_stop = None
    model_transport_current = False
    if model is not None:
      eof = _field(model, 'timestampEof')
      if type(eof) is int and 0 < eof <= now_boot_ns and now_boot_ns - eof <= MODEL_EOF_MAX_AGE_NS:
        model_transport_current = True
        horizon = _horizon(model)
        stop_horizon = horizon
        model_should_stop = _boolean(_field(_field(model, 'action'), 'shouldStop'))
        if (horizon is None and self.stop_detector.committed and model_should_stop is False and speed is not None and
            standstill_release_speed(_field(car, 'standstill'), _field(car, 'vEgo'), _field(car, 'vEgoRaw'))):
          # A stationary model can backstep millimeters near its origin before
          # projecting forward. Use that endpoint only to clear an existing stop;
          # Curve geometry and cold stop acquisition retain strict validation.
          stop_horizon = _standstill_clear_horizon(model)
        # A fresh discrete stop bit can corroborate an existing Ford stopped
        # hold even when geometry is unavailable. Geometry admission stays strict.
        current_stop = model_should_stop if (stop_horizon is not None or
          _field(cp, 'carFingerprint') in FORD_STOP_NAMES and _field(car, 'standstill') is True) else None
    lead = _lead(radar) if radar is not None else None
    car_valid = speed is not None
    headway_fresh = (
      type(selected_t_follow_observed_mono_ns) is int
      and self.barrier_mono_ns < selected_t_follow_observed_mono_ns <= now_mono_ns
      and now_mono_ns - selected_t_follow_observed_mono_ns <= SOURCE_MAX_AGE_NS
    )
    model_observed = min(float(sm.logMonoTime['modelV2']) / 1e9, float(sm.recv_time['modelV2'])) if model is not None else 0.0
    lead_observation = self.lead_detector.step(
      _field(radar, 'leadOne') if radar is not None else None,
      model,
      speed_mps=speed,
      t_follow_s=selected_t_follow_s,
      standstill=_boolean(_field(car, 'standstill')) if car_valid else None,
      observed_mono_s=model_observed,
      now_mono_s=now_mono_ns / 1e9,
      radar_fresh=radar is not None,
      model_fresh=model_transport_current,
      car_fresh=car_valid,
      headway_fresh=headway_fresh,
    )
    qualified_lead = None
    if lead is not None and lead_observation.tracked is not None:
      qualified_lead = LeadEvidence(
        lead.present, lead_observation.tracked, lead.radar, lead.distance_m, lead.speed_mps, lead.accel_mps2, lead.model_probability
      )
    context = owner_context if isinstance(owner_context, ConditionalOwnerContext) else ConditionalOwnerContext()

    def owner_bool(value: ObservedBool | None) -> tuple[bool | None, int | None]:
      return _owner_bool(value, now_mono_ns, self.barrier_mono_ns)

    traffic, traffic_stamp = owner_bool(context.traffic_mode)
    curve_mode = traffic
    raw_road = None
    filtered_curve = None
    curve_usable = False
    if (
      model is not None
      and horizon is not None
      and speed is not None
      and curvature is not None
      and type(_field(car, 'leftBlinker')) is bool
      and type(_field(car, 'rightBlinker')) is bool
    ):
      raw = frozen_raw_curve(model, speed, curvature, car.leftBlinker, car.rightBlinker)
      if raw is not None:
        raw_road, raw_curve = raw
        if type(curve_mode) is bool:
          curve_usable = True
          filtered_curve = self.curve_detector.step(
            observed_mono_ns=_source_stamp(sm, 'modelV2'), speed_mps=speed, raw_curve=raw, traffic_mode=curve_mode,
          )
    if not curve_usable:
      self.curve_detector.reset()
    sign, sign_stamp = owner_bool(context.stop_sign_confirmed)
    forcing, forcing_stamp = owner_bool(context.forcing_stop)
    dashboard_sign, dashboard_stamp = owner_bool(context.dashboard_stop_sign)
    pedal, pedal_stamp = owner_bool(context.pedal_override)
    model_time, model_time_stamp = _owner_float(context.model_stop_time_s, now_mono_ns, self.barrier_mono_ns, high=10.0)
    slower_option, slower_stamp = owner_bool(context.slower_option)
    stopped_option, stopped_stamp = owner_bool(context.stopped_option)
    if type(signal_options) is CEMOptions:
      # These are choices in the currently affirmed settings revision. They
      # have no sensor timestamp and cannot renew a stale model/radar frame.
      if context.model_stop_time_s is None:
        model_time = signal_options.model_stop_s if signal_options.stop_lights else 0.0
      if context.slower_option is None:
        slower_option = signal_options.lead and signal_options.slower_lead
      if context.stopped_option is None:
        stopped_option = signal_options.lead and signal_options.stopped_lead
    previous_experimental, previous_stamp = owner_bool(context.previous_experimental)
    committed_turn, turn_stamp = owner_bool(context.committed_turn_scene)
    red_light, red_stamp = owner_bool(context.red_light)
    plan_forcing_stop, plan_forcing_stamp = owner_bool(context.plan_forcing_stop)
    plan_should_stop, plan_should_stop_stamp = owner_bool(context.plan_should_stop)
    plan_allow_throttle, plan_allow_throttle_stamp = owner_bool(context.plan_allow_throttle)
    model_stamp = _source_stamp(sm, 'modelV2') if model_transport_current else None
    repeated_model = model_stamp is not None and model_stamp == self.last_model_stamp_ns
    car_stamp = _source_stamp(sm, 'carState') if car_valid else None
    radar_stamp = _source_stamp(sm, 'radarState') if radar is not None else None
    control_stamp = _source_stamp(sm, 'carControl') if control is not None else None
    selfdrive_stamp = _source_stamp(sm, 'selfdriveState') if selfdrive is not None else None
    longitudinal_stamp = _source_stamp(sm, 'longitudinalPlan') if longitudinal_plan is not None else None
    if context.committed_turn_scene is None and car_valid:
      committed_turn = committed_turn_scene(
        speed_mps=speed, standstill=_boolean(_field(car, 'standstill')),
        left_blinker=_boolean(_field(car, 'leftBlinker')),
        right_blinker=_boolean(_field(car, 'rightBlinker')),
        steering_angle_deg=_number(_field(car, 'steeringAngleDeg'), low=-720.0, high=720.0),
        driving_in_curve=raw_curve if horizon is not None else None,
      )
      if committed_turn is not None:
        # A known non-turn needs only the car sample. The active branch also
        # depends on the original model observation; polling cannot renew it.
        turn_stamp = min(car_stamp, model_stamp) if (car_stamp is not None and model_stamp is not None and
                     speed is not None and speed <= 15.0 * 0.44704 and
                     _field(car, 'standstill') is False and
                     (_field(car, 'leftBlinker') is True or _field(car, 'rightBlinker') is True)) else car_stamp
    lane_available = None
    if type(signal_options) is CEMOptions and model is not None and car_valid:
      try:
        lane_frame = SignalLaneFrame(
          model=model, model_valid=True, car_valid=True, ego_speed_mps=speed,
          minimum_lane_change_speed_mps=LANE_CHANGE_SPEED_MIN,
          signal_speed_mps=signal_options.signal_speed_mps,
          lane_detection_width_m=signal_options.signal_lane_width_m,
          signal_lane_detection=signal_options.signal_lane_detection,
          left_blinker=_field(car, 'leftBlinker'), right_blinker=_field(car, 'rightBlinker'),
          model_source=MonoSource(sm.logMonoTime['modelV2'], int(sm.recv_time['modelV2'] * 1e9)),
          car_source=MonoSource(sm.logMonoTime['carState'], int(sm.recv_time['carState'] * 1e9)),
          model_eof_boot_ns=_field(model, 'timestampEof'),
          now_mono_ns=now_mono_ns, now_boot_ns=now_boot_ns, expected_boot_minus_mono_ns=self.offset_ns,
          barrier_mono_ns=self.barrier_mono_ns, barrier_boot_ns=self.barrier_mono_ns + self.offset_ns,
          sample_skew_ns=sample_skew_ns,
        )
        lane_available = self.signal_lane_tracker.update(lane_frame).lane_available
      except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        self.signal_lane_tracker.reset()
    else:
      self.signal_lane_tracker.reset()

    def minimum_stamp(*stamps: int | None) -> float | None:
      valid = tuple(stamp for stamp in stamps if stamp is not None)
      return min(valid) / 1e9 if len(valid) == len(stamps) and valid else None

    stop_stamps = [model_stamp, car_stamp, radar_stamp, traffic_stamp, sign_stamp, forcing_stamp]
    if context.model_stop_time_s is not None:
      stop_stamps.append(model_time_stamp)
    if car_valid and _field(car, 'standstill') is True:
      stop_stamps.extend((dashboard_stamp, pedal_stamp))
    stop_stamp = minimum_stamp(*stop_stamps)
    if repeated_model and stop_stamp is not None and self.last_stop_stamp_s is not None:
      stop_stamp = min(stop_stamp, self.last_stop_stamp_s)
    stop_lead = None
    if lead is not None:
      stop_lead = StopLead(lead.present, lead.distance_m, lead.speed_mps, lead.radar, lead.model_probability, lead_observation.tracked)
    launch_horizon = stop_horizon if stop_horizon is not None else (
      _standstill_clear_horizon(model) if model_transport_current else None)
    stationary_launch_clear = _stationary_launch_clear(model, car, lead, launch_horizon)
    if stationary_launch_clear:
      # Continue a proven stationary launch after its stop commitment clears.
      # This bounded geometry cannot acquire a stop or qualify curve/lead data.
      stop_horizon, current_stop = launch_horizon, False
    stop_frame = StopFrame(
      observed_mono_s=stop_stamp if stop_stamp is not None else 0.0,
      now_mono_s=now_mono_ns / 1e9,
      speed_mps=speed,
      model_horizon_m=stop_horizon,
      model_stop_time_s=model_time,
      traffic_mode=traffic,
      stop_sign_confirmed=sign,
      forcing_stop=forcing,
      lead=stop_lead,
      standstill=_boolean(_field(car, 'standstill')) if car_valid else None,
      left_blinker=_boolean(_field(car, 'leftBlinker')) if car_valid else None,
      right_blinker=_boolean(_field(car, 'rightBlinker')) if car_valid else None,
      steering_angle_deg=_number(_field(car, 'steeringAngleDeg'), low=-720.0, high=720.0) if car_valid else None,
      driving_in_curve=raw_curve,
      car_fingerprint=_field(cp, 'carFingerprint'),
      dashboard_stop_sign=dashboard_sign,
      pedal_override=pedal,
      model_tick_mono_s=model_stamp / 1e9 if model_stamp is not None else None,
      model_should_stop=current_stop,
      stationary_launch_clear=stationary_launch_clear,
      longitudinal_stop_active=bool(_field(control, 'longActive') is True and _field(selfdrive, 'enabled') is True and
        _field(controls, 'longControlState') == structs.CarControl.Actuators.LongControlState.stopping),
      longitudinal_launch_active=bool(_field(control, 'enabled') is True and _field(control, 'longActive') is True and
        _field(selfdrive, 'enabled') is True and _field(controls, 'longControlState') in
        (structs.CarControl.Actuators.LongControlState.starting, structs.CarControl.Actuators.LongControlState.pid)),
      standstill_path_extent_m=(_standstill_path_extent(model) if model_transport_current and car_valid and
                                _field(car, 'standstill') is True else None),
    )
    if repeated_model:
      self.stop_detector.commit_clear_since_s = None
      stop_frame = replace(stop_frame, observed_mono_s=self.last_stop_stamp_s or 0.0)
    missing_transport = car is None or radar is None or model is None
    last_tick = self.stop_detector.last_model_tick_mono_s
    transport_alive = all(sm.alive.get(name, False) and sm.valid.get(name, False) for name in ('carState', 'modelV2', 'radarState'))
    if missing_transport and transport_alive and last_tick is not None and 0 <= now_mono_ns - round(last_tick * 1e9) <= SOURCE_MAX_AGE_NS:
      self.stop_detector.commit_clear_since_s = None
      # Withhold an observation while a source is absent; a brief scheduling
      # gap must not erase the detector's multi-second stop hysteresis.
      stop_observation = StopObservation(None, None, None, None)
    else:
      stop_observation = self.stop_detector.step(stop_frame)
    # Dashboard/pedal evidence is decisive only while stopped. The detector
    # checks both then; stale values cannot become a false hold observation.

    slower_stamp_ns = minimum_stamp(
      model_stamp,
      car_stamp,
      radar_stamp,
      control_stamp,
      selected_t_follow_observed_mono_ns if headway_fresh else None,
      traffic_stamp,
      sign_stamp,
      slower_stamp if context.slower_option is not None else model_stamp,
      stopped_stamp if context.stopped_option is not None else model_stamp,
      previous_stamp,
      turn_stamp,
    )
    if repeated_model and slower_stamp_ns is not None and self.last_slower_stamp_s is not None:
      slower_stamp_ns = min(slower_stamp_ns, self.last_slower_stamp_s)
    slower_frame = SlowerLeadFrame(
      observed_mono_s=slower_stamp_ns if slower_stamp_ns is not None else 0.0,
      now_mono_s=now_mono_ns / 1e9,
      speed_mps=speed,
      selected_follow_s=selected_t_follow_s if headway_fresh else None,
      long_active=_boolean(_field(control, 'longActive')) if control is not None else None,
      lead=qualified_lead,
      slower_option=slower_option,
      stopped_option=stopped_option,
      previous_experimental=previous_experimental,
      traffic_mode=traffic,
      stop_sign_confirmed=sign,
      committed_turn_scene=committed_turn,
      standstill=_boolean(_field(car, 'standstill')) if car_valid else None,
      model_tick_mono_s=model_stamp / 1e9 if model_stamp is not None else None,
    )
    if repeated_model:
      slower_frame = replace(slower_frame, observed_mono_s=self.last_slower_stamp_s or 0.0)
    slower_observation = self.slower_lead_detector.step(slower_frame)
    raw_model_stopped = horizon < FROZEN_RAW_STOP_DISTANCE_M if horizon is not None else None
    model_stopped = True if raw_model_stopped is True or forcing is True else False if raw_model_stopped is False and forcing is False else None
    chill_lead = ChillLead(
      lead.present, lead.distance_m, lead.speed_mps, lead.relative_speed_mps, lead.accel_mps2,
    ) if lead is not None else None
    chill_stop_stamp = minimum_stamp(model_stamp, car_stamp)
    plan_stamp = (min(plan_should_stop_stamp, plan_allow_throttle_stamp)
                  if plan_should_stop_stamp is not None and plan_allow_throttle_stamp is not None else
                  None if plan_should_stop is not None or plan_allow_throttle is not None else longitudinal_stamp)
    launch_stamp = minimum_stamp(
      model_stamp, car_stamp, radar_stamp, selfdrive_stamp, plan_stamp,
      sign_stamp, forcing_stamp, red_stamp, plan_forcing_stamp,
    )
    # StopObservation already checks its own original source age and model tick.
    # An older optional stop owner cannot erase an independently proven raw stop.
    chill_stamp = launch_stamp if launch_stamp is not None else chill_stop_stamp
    plan_should = (plan_should_stop if plan_should_stop is not None else
                   _boolean(_field(longitudinal_plan, 'shouldStop')) if longitudinal_plan is not None else None)
    plan_throttle = (plan_allow_throttle if plan_allow_throttle is not None else
                     _boolean(_field(longitudinal_plan, 'allowThrottle')) if longitudinal_plan is not None else None)
    chill_frame = ChillSceneFrame(
      observed_mono_s=chill_stamp if chill_stamp is not None else 0.0,
      model_tick_mono_s=model_stamp / 1e9 if model_stamp is not None else 0.0,
      now_mono_s=now_mono_ns / 1e9,
      speed_mps=speed,
      lead=chill_lead,
      tracking_lead=lead_observation.tracked,
      raw_model_stopped=raw_model_stopped,
      model_stopped=model_stopped,
      stop_light_model_detected=stop_observation.model_stopping,
      stop_light_detected=stop_observation.light_detected,
      stop_sign_confirmed=sign,
      forcing_stop=forcing,
      red_light=red_light,
      plan_forcing_stop=plan_forcing_stop,
      should_stop=plan_should,
      allow_throttle=plan_throttle,
      selfdrive_enabled=_boolean(_field(selfdrive, 'enabled')) if selfdrive is not None else None,
    )
    chill_observation = self.chill_detector.step(chill_frame)
    adjacent_ambiguous = self._adjacent_wire(sm, now_mono_ns=now_mono_ns, now_boot_ns=now_boot_ns,
                                             model=model, car=car if car_valid else None)
    if not repeated_model:
      self.last_model_stamp_ns = model_stamp
      self.last_stop_stamp_s = stop_stamp
      self.last_slower_stamp_s = slower_stamp_ns
    observed = None
    if car_valid:
      # Base speed/blinker evidence comes from carState. Its original producer
      # and receipt age cannot be renewed by repeated SubMaster polling; the
      # separate curve detector independently requires fresh controls/model.
      observed = min(float(sm.logMonoTime['carState']) / 1e9, float(sm.recv_time['carState']))
    scene = SceneEvidence(
      observed_mono_s=observed,
      speed_mps=speed,
      set_speed_mps=cruise,
      lead=qualified_lead,
      following_lead=lead_observation.following,
      left_blinker=_boolean(_field(car, 'leftBlinker')) if car_valid else None,
      right_blinker=_boolean(_field(car, 'rightBlinker')) if car_valid else None,
      lane_available=lane_available,
      standstill=_boolean(_field(car, 'standstill')) if car_valid else None,
      curve_detected=filtered_curve,
      slow_lead_detected=slower_observation.detected,
      stop_light_detected=stop_observation.light_detected,
      standstill_stop_hold=stop_observation.standstill_hold,
      traffic_mode=curve_mode,
      stop_sign_confirmed=sign,
      committed_stop=self.stop_detector.committed if stop_observation.light_detected is not None else None,
      forcing_stop=forcing,
      low_speed_stop_scene=chill_observation.low_speed_stop_scene,
      launch_candidate=chill_observation.launch_candidate,
      launch_forced_exit=True if chill_observation.forced_exit else False if chill_observation.launch_candidate is not None else None,
      launch_lead=chill_observation.launch_status == 'lead' if chill_observation.launch_candidate else None,
      adjacent_lead_ambiguous=adjacent_ambiguous,
    )
    authority = None
    if (
      car_valid
      and control is not None
      and selfdrive is not None
      and type(safe_mode) is bool
      and type(_field(cp, 'openpilotLongitudinalControl')) is bool
      and type(_field(cp, 'pcmCruise')) is bool
    ):
      enabled = _boolean(_field(selfdrive, 'enabled'))
      long_active = _boolean(_field(control, 'longActive'))
      lat_active = _boolean(_field(control, 'latActive'))
      if enabled is not None and long_active is not None and lat_active is not None:
        authority = Authority(True, bool(cp.openpilotLongitudinalControl), safe_mode, enabled, long_active, lat_active)
    return ProjectedScene(
      scene,
      authority,
      fresh,
      lead,
      lead_observation,
      raw_curve,
      raw_road,
      horizon,
      raw_model_stopped,
      current_stop,
      car_valid and authority is not None,
      stop_stamp,
      slower_stamp_ns,
      chill_stamp,
      chill_observation,
    )
