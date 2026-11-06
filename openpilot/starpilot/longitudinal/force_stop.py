"""Force Stop's model approach, commitment, distance and driver-release policy."""

from dataclasses import dataclass
import math

from openpilot.common.realtime import DT_MDL

STANDSTILL_HOLD_NS = 2_000_000_000
STANDSTILL_CLEAR_NS = 750_000_000
STOP_COMMIT_TIME_S = .5
SIGN_CONFIRM_STEP_S = 2 * DT_MDL
SIGN_MAX_SPEED_MPS = 17.88
SIGN_MODEL_AGREE_M = 50.0
SIGN_SEED_M = 27.0
SIGN_ESTIMATE_HOLD_NS = 2_000_000_000
# Over a long model path a sign is only believed if stopping at its estimate would be a normal stop.
SIGN_LONG_PATH_MAX_DECEL = 2.5
SIGN_APPROACH_DECEL = 1.0
MODEL_APPROACH_DECEL = .65
# Stopped behind a car at a sign: the line is assumed one car length past that car's rear.
QUEUE_LEAD_MAX_M = 8.0
QUEUE_LEAD_LENGTH_M = 4.5
# A car still inside this margin past the line may just be moving up the queue, not through the intersection.
QUEUE_CLEAR_M = 5.0
QUEUE_SIGN_RECENT_NS = 10_000_000_000
QUEUE_MAX_SPEED_MPS = 6.0


@dataclass(frozen=True)
class StopTune:
  handoff_m: float = 6.0
  distance_bias_m: float = 0.0
  lead_veto_m: float = 75.0
  jerk_scale: float = 0.32
  reanchor_speed_tolerance: float | None = None
  low_speed_hold_mps: float | None = None


def vehicle_tune(fingerprint: str) -> StopTune:
  if fingerprint == 'TOYOTA_CAMRY_TSS2':
    return StopTune(handoff_m=4.5, distance_bias_m=6.0)
  if fingerprint == 'HYUNDAI_ELANTRA_2021':
    return StopTune(lead_veto_m=90.0, jerk_scale=0.8)
  if fingerprint == 'HYUNDAI_SANTA_FE_2022':
    return StopTune(reanchor_speed_tolerance=0.25, low_speed_hold_mps=2.5)
  return StopTune()


@dataclass(frozen=True)
class StopFrame:
  model_ns: int
  speed_mps: float
  horizon_m: float | None
  light: bool
  raw_model_stopped: bool
  model_should_stop: bool
  standstill: bool
  enabled: bool
  pedal_override: bool
  tracking_lead: bool
  lead_present: bool
  lead_distance_m: float
  lead_speed_mps: float
  driving_in_curve: bool
  road_curvature: float | None
  blinker: bool
  steering_angle_deg: float
  resume_requested: bool = False
  perception_available: bool = True
  sign: bool = False
  dashboard_sign: bool = False
  vision_distance_m: float | None = None


@dataclass(frozen=True)
class StopPlan:
  model_ns: int = 0
  speed_ceiling_mps: float | None = None
  obstacle_m: float | None = None
  forcing: bool = False
  should_stop: bool = False
  tracked_distance_m: float = 0.0
  approach_distance_m: float = 0.0
  jerk_scale: float = 1.0
  manual_hold: bool = False
  takeoff_light_observed: bool = False
  takeoff_light: bool = False
  resume_override: bool = False


class ForceStop:
  def __init__(self):
    self.reset()

  def reset(self):
    self.last_model_ns = 0
    self.timer = 0.0
    self.forcing = False
    self.from_light = False
    self.light_clear_ns = 0
    self.override_until_ns = 0
    self.resume_until_ns = 0
    self.entry_speed = None
    self.activation_gate = False
    self.stop_seen_ns = 0
    self.standstill_since_ns = 0
    self.standstill_clear_ns = 0
    self.standstill_reason = None
    self.sign_confirmed = False
    self.previously_enabled = False
    self.distance = 0.0
    self.distance_cap = 0.0
    self.queue_sign_ns = 0
    self.queue_line = None
    self._clear_sign_estimate()

  def _clear_sign_estimate(self):
    self.dash_distance = None
    self.vision_distance = None
    self.vision_samples = 0
    self.sign_evidence_ns = 0

  def _sign_estimate(self, frame: StopFrame, now: int, travel: float) -> float | None:
    if self.sign_evidence_ns and now - self.sign_evidence_ns > SIGN_ESTIMATE_HOLD_NS:
      self._clear_sign_estimate()
    if self.dash_distance is not None:
      self.dash_distance = max(0.0, self.dash_distance - travel)
    if self.vision_distance is not None:
      self.vision_distance = max(0.0, self.vision_distance - travel)
    vision = frame.vision_distance_m
    if vision is not None and math.isfinite(vision) and vision >= 0:
      self.vision_samples += 1
      self.vision_distance = vision if self.vision_distance is None else (
        self.vision_distance + (vision - self.vision_distance) / self.vision_samples)
    if frame.dashboard_sign or frame.sign and self.vision_distance is None:
      self.dash_distance = SIGN_SEED_M if self.dash_distance is None else min(self.dash_distance, SIGN_SEED_M)
    if frame.sign or vision is not None:
      self.sign_evidence_ns = now
    estimates = [value for value in (self.dash_distance, self.vision_distance) if value is not None]
    return sum(estimates) / len(estimates) if estimates else None

  def _queue_step(self, frame: StopFrame, now: int, travel: float, lead_blocked: bool):
    if frame.sign and lead_blocked:
      self.queue_sign_ns = now
    queued_lead = frame.lead_present and frame.lead_speed_mps < 1.0 and frame.lead_distance_m < QUEUE_LEAD_MAX_M
    arm = frame.standstill and self.queue_sign_ns and now - self.queue_sign_ns <= QUEUE_SIGN_RECENT_NS
    if queued_lead and (arm or self.queue_line is not None):
      self.queue_line = frame.lead_distance_m + QUEUE_LEAD_LENGTH_M
    elif self.queue_line is not None:
      self.queue_line -= travel
    if self.queue_line is not None and (frame.pedal_override or frame.resume_requested or self.queue_line <= 0 or
                                        frame.speed_mps > QUEUE_MAX_SPEED_MPS):
      self.queue_line = None
      self.queue_sign_ns = 0

  def _clear_standstill(self):
    self.standstill_since_ns = 0
    self.standstill_clear_ns = 0
    self.standstill_reason = None

  @property
  def can_commit_next_frame(self) -> bool:
    return self.timer + SIGN_CONFIRM_STEP_S >= STOP_COMMIT_TIME_S

  def step(self, frame: StopFrame, tune: StopTune, offset_ft: int = 0) -> StopPlan:
    now = frame.model_ns
    if (type(now) is not int or now <= 0 or now <= self.last_model_ns or
        self.last_model_ns and now - self.last_model_ns > 250_000_000):
      self.reset()
      return StopPlan()
    dt = (now - self.last_model_ns) / 1e9 if self.last_model_ns else DT_MDL
    self.last_model_ns = now
    if not frame.enabled:
      self.reset()
      return StopPlan(model_ns=now)
    speed = frame.speed_mps
    horizon = frame.horizon_m if frame.horizon_m is not None else math.nan
    curvature = frame.road_curvature if frame.road_curvature is not None else math.nan
    if (not all(math.isfinite(value) for value in (speed, frame.steering_angle_deg)) or
        frame.perception_available and not all(math.isfinite(value) for value in (horizon, curvature))):
      self.reset()
      return StopPlan()
    available = frame.perception_available
    sign_estimate = self._sign_estimate(frame, now, max(0.0, speed) * dt)
    lead = (frame.lead_present and frame.lead_distance_m < tune.lead_veto_m and frame.lead_speed_mps < speed + 2)
    self._queue_step(frame, now, max(0.0, speed) * dt, lead or frame.tracking_lead)
    if available and (frame.light or frame.raw_model_stopped) or frame.sign or self.queue_line is not None:
      if not frame.standstill:
        self.stop_seen_ns = now
    if frame.standstill:
      self.stop_seen_ns = 0
    stop_then_turn = bool(self.stop_seen_ns and now - self.stop_seen_ns < 4_000_000_000)
    turn = speed <= 18 * 0.44704 and frame.blinker and abs(frame.steering_angle_deg) >= 25 and not stop_then_turn
    curved = available and abs(curvature) >= .003 and not stop_then_turn
    model_active = available and horizon < (108.0 if self.activation_gate and frame.light else 100.0)
    self.activation_gate = model_active and frame.light
    permitted = (available and now >= self.override_until_ns and not frame.driving_in_curve and not turn and
                 not frame.tracking_lead and not lead)
    model_path = frame.light and model_active and permitted and not curved
    plausible = sign_estimate is None or speed * speed <= 2 * SIGN_LONG_PATH_MAX_DECEL * max(sign_estimate, 1.0)
    sign_path = (frame.sign and permitted and speed < SIGN_MAX_SPEED_MPS and
                 (horizon < SIGN_MODEL_AGREE_M or self.forcing and plausible))
    line_blocked = (self.queue_line is not None and frame.lead_present and
                    frame.lead_distance_m < self.queue_line + QUEUE_CLEAR_M)
    queue_path = (self.queue_line is not None and not line_blocked and not frame.standstill and available and
                  now >= self.override_until_ns and not frame.driving_in_curve and not turn)
    if sign_path or queue_path:
      self.sign_confirmed = True
    active = model_path or sign_path or queue_path
    if model_path:
      self.from_light = True
      self.light_clear_ns = 0
    elif not self.forcing:
      self.from_light = False
      self.light_clear_ns = 0
    stop_scene = available and (active or frame.raw_model_stopped)
    go_scene = available and horizon >= 50.0 and not frame.raw_model_stopped and not frame.model_should_stop
    held_before = self.previously_enabled and self.standstill_reason is not None
    engaged_stopped = not self.previously_enabled and frame.standstill
    if frame.standstill and self.sign_confirmed and self.standstill_reason != 'sign':
      self.standstill_since_ns = now
      self.standstill_reason = 'sign'
      self.distance = 0
    elif frame.standstill and (self.forcing or engaged_stopped and stop_scene) and self.standstill_reason is None:
      self.standstill_since_ns = now
      self.standstill_reason = 'unclassified'
      self.distance = 0
    if self.standstill_reason is not None:
      if not available or stop_scene:
        self.standstill_clear_ns = 0
      elif not self.standstill_clear_ns:
        self.standstill_clear_ns = now
    hold_expired = self.standstill_reason not in (None, 'sign') and (
      now - self.standstill_since_ns >= STANDSTILL_HOLD_NS or
      self.standstill_clear_ns > 0 and now - self.standstill_clear_ns >= STANDSTILL_CLEAR_NS)
    if held_before and frame.standstill and frame.resume_requested:
      self.resume_until_ns = now + 6_000_000_000
    if abs(speed) >= 2.0 or frame.lead_present or frame.tracking_lead:
      self.resume_until_ns = 0
    driver_release = frame.pedal_override or frame.resume_requested
    if self.standstill_reason is not None and (driver_release or hold_expired):
      self.override_until_ns = now + 10_000_000_000
      self._clear_standstill()
    held = self.standstill_reason is not None
    if not available and not self.forcing:
      self.timer = 0
    elif active and not frame.standstill:
      self.timer = min(2.0, self.timer + (SIGN_CONFIRM_STEP_S if frame.sign or queue_path else DT_MDL))
    elif turn and not self.forcing and not frame.standstill:
      self.timer = 0
    elif held:
      self.timer = max(self.timer, STOP_COMMIT_TIME_S)
    elif self.forcing and frame.standstill and not frame.sign and not frame.light and not frame.raw_model_stopped:
      self.timer = 0
    else:
      self.timer = max(0, self.timer - DT_MDL * .25)
    force = self.timer >= STOP_COMMIT_TIME_S or self.forcing and not frame.standstill or held
    light_cleared = (not held and self.forcing and self.from_light and not frame.standstill and not self.sign_confirmed and
                     available and not frame.light and not frame.sign and go_scene)
    low_speed_hold = (light_cleared and tune.low_speed_hold_mps is not None and self.entry_speed is not None and
                      speed <= tune.low_speed_hold_mps and speed < self.entry_speed - .25)
    if light_cleared and not low_speed_hold:
      if not self.light_clear_ns:
        self.light_clear_ns = now
      elif now - self.light_clear_ns >= 500_000_000:
        self.forcing = self.from_light = force = False
        self.timer = 0
        self.light_clear_ns = 0
    else:
      self.light_clear_ns = 0
    if self.forcing and frame.standstill and not force:
      self.override_until_ns = now + 10_000_000_000
    if driver_release and force:
      self.override_until_ns = now + 10_000_000_000
    overridden = now < self.override_until_ns
    offset = max(-20, min(20, offset_ft)) * .3048
    approach = 0.0
    ceiling = None
    if force and not overridden:
      if self.entry_speed is None and not frame.standstill:
        self.entry_speed = speed
      self.forcing |= not frame.standstill or held
      if held:
        self.distance = 0
        ceiling = 0.0
      else:
        self.distance = max(0.0, self.distance - speed * dt)
        if (available and not frame.sign and self.distance > max(tune.handoff_m, 40) and not frame.model_should_stop and
            horizon > self.distance + 3 and (tune.reanchor_speed_tolerance is None or self.entry_speed is None or
                                            speed >= self.entry_speed - tune.reanchor_speed_tolerance)):
          self.distance = horizon
        elif available:
          self.distance = min(self.distance, horizon)
        self.distance_cap = max(0.0, self.distance_cap - speed * dt)
        cap = self.distance_cap + 15 * min(self.distance_cap / 60, 1)
        self.distance = min(self.distance, cap)
        if sign_estimate is not None and (self.sign_confirmed or frame.sign and available and horizon < SIGN_MODEL_AGREE_M):
          self.distance = min(sign_estimate, cap)
        if self.queue_line is not None and self.sign_confirmed:
          self.distance = self.queue_line
        effective = self.distance + offset + tune.distance_bias_m
        decel = SIGN_APPROACH_DECEL if frame.sign else MODEL_APPROACH_DECEL
        ceiling = math.sqrt(2 * decel * max(0, effective - tune.handoff_m))
    else:
      self.forcing = False
      self.entry_speed = None
      self.sign_confirmed = False
      self._clear_standstill()
      self.distance = self.distance_cap = horizon if available else 0.0
      if frame.light and permitted and not curved and not overridden:
        approach = horizon
        effective = approach + offset + tune.distance_bias_m
        if effective > tune.handoff_m:
          ceiling = math.sqrt(2 * MODEL_APPROACH_DECEL * (effective - tune.handoff_m))
    self.previously_enabled = frame.enabled
    stop_length = self.distance if self.forcing else approach
    # The MPC obstacle includes its standing-distance term; adding it at the
    # native boundary avoids moving the actual stop line by that buffer.
    # The offset moves the handoff with the ceiling; otherwise a positive offset creeps with no stop target.
    obstacle = (stop_length * .93 + offset + tune.distance_bias_m
                if stop_length > 0 and stop_length + offset > tune.handoff_m else None)
    return StopPlan(now, ceiling, obstacle, self.forcing, bool(self.forcing and ceiling is not None and ceiling <= .5),
                    self.distance if self.forcing else 0.0, approach,
                    tune.jerk_scale if self.forcing or approach > 0 else 1.0, bool(held and self.forcing and not overridden),
                    resume_override=now < self.resume_until_ns)
