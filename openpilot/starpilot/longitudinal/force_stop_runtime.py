"""Current-drive Force Stop owner with bounded preference reads and live scene inputs."""

from dataclasses import replace
import math

from openpilot.starpilot.conditional_mode.curve import frozen_raw_curve
from openpilot.starpilot.conditional_mode.preferences import CEMOptions, decode_preferences, PreferenceError
from openpilot.starpilot.conditional_mode.projection import _horizon
from openpilot.starpilot.conditional_mode.stop import (
  FROZEN_RAW_STOP_DISTANCE_M, STOP_MODEL_RELEASE_MARGIN_M, StopFrame as LightFrame, StopLead, StopLightDetector,
)
from openpilot.starpilot.lead_tracking import LeadDetector
from openpilot.starpilot.longitudinal.force_stop import ForceStop, StopFrame, StopPlan, vehicle_tune
from openpilot.starpilot.model_geometry import road_curvature, standstill_clear_horizon, standstill_release_speed
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.longitudinal.stop_resume import StopResume
from openpilot.starpilot.longitudinal.force_stop_braking import eligible as distance_braking_eligible
from openpilot.starpilot.speed_limits.vision.stop_observation import CONTROL_STOP_MAX_AGE_NS, StopConfirmation


KEY = 'ForceStops'
OFFSET_KEY = 'ForceStopDistanceOffset'
SOURCE_AGE_NS = 150_000_000


def standstill_takeoff_light(model, car) -> bool | None:
  """Fresh takeoff metadata only; Force Stop keeps its strict spatial policy."""
  if not standstill_release_speed(car.standstill, car.vEgo, car.vEgoRaw):
    return None
  should_stop = model.action.shouldStop
  if type(should_stop) is not bool:
    return None
  horizon = standstill_clear_horizon(model)
  if horizon is None:
    return None
  return should_stop or horizon < FROZEN_RAW_STOP_DISTANCE_M + STOP_MODEL_RELEASE_MARGIN_M


def dashboard_stop_sign(sm, *, now_ns: int, drive_id: int) -> bool:
  try:
    if not sm.seen['starpilotCarState'] or not sm.valid['starpilotCarState']:
      return False
    stamp = int(sm.logMonoTime['starpilotCarState'])
    return bool(drive_id < stamp <= now_ns and now_ns - stamp <= SOURCE_AGE_NS and
                sm['starpilotCarState'].dashboardStopSign > 0)
  except (AttributeError, KeyError, TypeError, ValueError):
    return False


class ForceStopRuntime:
  def __init__(self, params):
    self.params = params
    self.policy = ForceStop()
    self.resume = StopResume()
    self.vision_stop = StopConfirmation()
    self.vision_ranged_ns = 0
    self.light = StopLightDetector()
    self.lead = LeadDetector()
    self.plan = StopPlan()
    self.drive_id = 0
    self.identity = None
    self.sources = None
    self.next_refresh_ns = 0
    self.enabled = False
    self.takeoff_preferences_valid = False
    self.offset = 0
    self.options = CEMOptions()
    self.clock_offset = None

  def reset(self):
    self.vision_stop.reset()
    self.vision_ranged_ns = 0
    self.resume.reset()
    self.policy.reset()
    self.light.reset()
    self.lead.reset()
    self.plan = StopPlan()

  def _preferences(self, now_ns: int):
    if now_ns < self.next_refresh_ns:
      return
    self.next_refresh_ns = now_ns + 1_000_000_000
    keys = (KEY, OFFSET_KEY, 'SafeMode', 'ConditionalModeConfig')
    try:
      values = tuple(read_saved(self.params, key, 4096 if key == 'ConditionalModeConfig' else 16) for key in keys)
      if self.sources is not None and values[:3] != self.sources[:3]:
        self.reset()
      self.sources = values
      if not all(readable for _, readable in values):
        raise ValueError('unreadable Force Stop settings')
      master, offset, safe, conditional = (raw for raw, _ in values)
      if master not in (None, b'0', b'1') or safe not in (None, b'0', b'1'):
        raise ValueError('invalid Force Stop switch')
      self.offset = int(offset) if offset is not None else 0
      if not -20 <= self.offset <= 20:
        raise ValueError('invalid stop offset')
      self.options = decode_preferences(conditional).cem if conditional is not None else CEMOptions()
      self.enabled = master == b'1' and safe in (None, b'0')
      self.takeoff_preferences_valid = True
    except (OSError, RuntimeError, TypeError, ValueError, PreferenceError):
      self.enabled = False
      self.takeoff_preferences_valid = False
      self.reset()

  def sample(self, sm, cp, *, now_ns: int, now_boot_ns: int, drive_id: int,
             follow_seconds: float | None, traffic_mode: bool | None, takeoff_enabled: bool = False) -> StopPlan:
    self._preferences(now_ns)
    try:
      identity = (str(cp.carFingerprint), bool(cp.openpilotLongitudinalControl), bool(cp.passive),
                  bool(cp.dashcamOnly), bool(cp.notCar))
      if identity != self.identity or drive_id != self.drive_id:
        self.reset()
        self.identity, self.drive_id = identity, drive_id
      offset = now_boot_ns - now_ns
      if self.clock_offset is None or abs(offset - self.clock_offset) > 1_000_000:
        self.clock_offset = offset
        self.reset()
        return self.plan
      services = ('modelV2', 'carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState')
      current = all(sm.valid[name] and sm.alive[name] and
                    drive_id < sm.logMonoTime[name] <= now_ns and
                    0 <= now_ns - sm.logMonoTime[name] <= SOURCE_AGE_NS and
                    drive_id < int(sm.recv_time[name] * 1e9) <= now_ns and
                    0 <= now_ns - int(sm.recv_time[name] * 1e9) <= SOURCE_AGE_NS for name in services)
      car, control, state, model = (sm[name] for name in ('carState', 'carControl', 'selfdriveState', 'modelV2'))
      if ((not self.enabled and not takeoff_enabled) or (takeoff_enabled and not self.takeoff_preferences_valid) or
          not identity[1] or any(identity[2:]) or drive_id <= 0 or not current or
          not car.canValid or car.canTimeout or not (control.longActive or control.latActive) or
          sm['controlsState'].forceDecel or (traffic_mode is not False and not self.policy.forcing) or
          not 0 < model.timestampEof <= now_boot_ns or now_boot_ns - model.timestampEof > SOURCE_AGE_NS):
        self.reset()
        return self.plan
      stamp = int(sm.logMonoTime['modelV2'])
      horizon = _horizon(model)
      curvature = road_curvature(model, car.vEgo)
      raw_curve = frozen_raw_curve(model, car.vEgo, sm['controlsState'].curvature, car.leftBlinker, car.rightBlinker)
      lead = sm['radarState'].leadOne
      perception_available = horizon is not None and curvature is not None and raw_curve is not None
      tracked = None
      light_context = False
      if perception_available:
        tracked = self.lead.step(lead, model, speed_mps=float(car.vEgo), t_follow_s=follow_seconds,
                                 standstill=bool(car.standstill), observed_mono_s=stamp / 1e9, now_mono_s=now_ns / 1e9,
                                 radar_fresh=True, model_fresh=True, car_fresh=True, headway_fresh=follow_seconds is not None)
        perception_available = tracked.tracked is not None
      if perception_available:
        assert raw_curve is not None and tracked is not None
        stop_lead = StopLead(bool(lead.present), float(lead.dRel), float(lead.vLead), bool(lead.radar),
                             float(lead.modelProb), tracked.tracked)
        observation = self.light.step(LightFrame(
          observed_mono_s=min(int(sm.logMonoTime[name]) for name in services) / 1e9,
          now_mono_s=now_ns / 1e9, speed_mps=float(car.vEgo), model_horizon_m=horizon,
          model_stop_time_s=self.options.model_stop_s if self.options.stop_lights or takeoff_enabled else 0.0,
          traffic_mode=False, stop_sign_confirmed=False, forcing_stop=self.policy.forcing,
          lead=stop_lead, standstill=bool(car.standstill), left_blinker=bool(car.leftBlinker),
          right_blinker=bool(car.rightBlinker), steering_angle_deg=float(car.steeringAngleDeg),
          driving_in_curve=raw_curve[1], car_fingerprint=str(cp.carFingerprint), dashboard_stop_sign=False,
          pedal_override=bool(car.gasPressed), model_tick_mono_s=stamp / 1e9,
          model_should_stop=bool(model.action.shouldStop)))
        perception_available = observation.light_detected is not None
        light_context = observation.light_detected is True or (observation.standstill_hold is True and
                                                             observation.standstill_reason == 'light')
      else:
        self.light.reset()
        self.lead.reset()
      takeoff_observed, takeoff_light = perception_available, bool(light_context)
      if takeoff_enabled and not perception_available:
        fallback = standstill_takeoff_light(model, car)
        if fallback is not None:
          takeoff_observed, takeoff_light = True, fallback
      if takeoff_enabled and not self.enabled:
        # Perception may veto takeoff while Force Stop stays entirely inactive.
        self.resume.reset()
        self.policy.reset()
        self.plan = StopPlan(model_ns=stamp, takeoff_light_observed=takeoff_observed,
                             takeoff_light=takeoff_light)
        return self.plan
      # Model trajectories can oscillate around zero velocity at a predicted
      # stop. Invalid geometry cannot acquire a stop or prove that it is clear;
      # an existing commitment continues from fresh car odometry below.
      resume_requested = self.resume.consume(now_ns=now_ns, drive_id=drive_id, car_ns=int(sm.logMonoTime['carState']))
      # Keep model perception warm during AOL and a manual brake stop. Only
      # actual longitudinal ownership may produce a stop target or hold.
      if not control.longActive or not state.enabled or car.brakePressed:
        self.resume.reset()
        self.policy.reset()
        self.plan = StopPlan()
        return self.plan
      vision_sign = self.vision_stop.observe_message(
        sm, now_ns=now_ns, now_boot_ns=now_boot_ns, drive_id=drive_id)
      vision_fresh = bool(vision_sign and 0 <= now_ns - vision_sign <= CONTROL_STOP_MAX_AGE_NS)
      vision_distance = None
      if vision_fresh and vision_sign != self.vision_ranged_ns and self.vision_stop.distance_m is not None:
        self.vision_ranged_ns = vision_sign
        vision_distance = self.vision_stop.distance_m - float(car.vEgo) * (stamp - vision_sign) / 1e9
      dashboard_sign = dashboard_stop_sign(sm, now_ns=now_ns, drive_id=drive_id)
      # A stationary forecast tail alone does not identify a fixed stop line.
      frame = StopFrame(stamp, float(car.vEgo), horizon, light_context, horizon is not None and horizon < 50,
                        bool(model.action.shouldStop), bool(car.standstill), True, bool(car.gasPressed),
                        tracked is not None and tracked.tracked is True, bool(lead.present), float(lead.dRel), float(lead.vLead),
                        raw_curve[1] if raw_curve is not None else False, curvature[0] if curvature is not None else None,
                        bool(car.leftBlinker or car.rightBlinker), float(car.steeringAngleDeg), resume_requested=resume_requested,
                        perception_available=perception_available, sign=dashboard_sign or vision_fresh,
                        dashboard_sign=dashboard_sign, vision_distance_m=vision_distance,
                        moving_preview=bool(perception_available and not model.action.shouldStop and
                                            len(model.velocity.x) == len(model.position.x) and
                                            math.isfinite(model.velocity.x[-1])))
      tune = replace(vehicle_tune(str(cp.carFingerprint)), distance_control=distance_braking_eligible(cp))
      self.plan = self.policy.step(frame, tune, self.offset)
      if takeoff_enabled:
        self.plan = replace(self.plan, takeoff_light_observed=takeoff_observed, takeoff_light=takeoff_light)
      return self.plan
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
      self.reset()
      return self.plan
