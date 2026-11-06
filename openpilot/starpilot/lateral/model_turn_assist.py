"""Rolling model turn encouragement and curvature handoff for Ioniq 6."""
import math
from openpilot.common.realtime import DT_CTRL
from openpilot.common.constants import CV

CURVATURE_HOLD_HARD_SPEED = 4.5 * CV.MPH_TO_MS
CURVATURE_HOLD_RELEASE_SPEED = 10.0 * CV.MPH_TO_MS
CURVATURE_HOLD_PLAN_SOURCE_SPEED = 2.0 * CV.MPH_TO_MS
CURVATURE_HOLD_RATCHET_RATE = 0.04
CURVATURE_HOLD_HANDOFF_FRAC = 0.75
CURVATURE_HOLD_HANDOFF_TIME = 0.3
CURVATURE_HOLD_DECAY_TAU = 2.0
CURVATURE_HOLD_SWEPT_EXIT = 0.9
CURVATURE_HOLD_EXIT_DECAY_TAU = 0.5
CURVATURE_HOLD_STANDSTILL_TIMEOUT = 30.0
CURVATURE_HOLD_PLAN_LOOKAHEAD_NEAR = 4.0
CURVATURE_HOLD_PLAN_LOOKAHEAD_FAR = 7.0
CURVATURE_HOLD_PLAN_SCALE = 0.85
CURVATURE_HOLD_PLAN_CAP = 0.12
CURVATURE_HOLD_ONSET_HEADING = 10.0
CURVATURE_HOLD_ONSET_NEAR = 1.5
CURVATURE_HOLD_ONSET_FAR_GATE = 5.0
CURVATURE_HOLD_ONSET_FAR = 100.0
CURVATURE_HOLD_REACH_MIN = 7.0
CURVATURE_HOLD_REACH_FULL = 12.0
CURVATURE_HOLD_OPPOSITE_RELEASE = 0.01
CURVATURE_HOLD_CONFIRM_MIN = 0.003
CURVATURE_HOLD_CONFIRM_SWEPT = 0.6
TWITCH_GUARD_MAX_SPEED = 4.0
TWITCH_GUARD_FADE_SPEED = 3.0
TWITCH_GUARD_PLAN_RATIO = 4.0
TWITCH_GUARD_FLOOR = 0.002
TWITCH_GUARD_STRAIGHT_LO = 0.005
TWITCH_GUARD_STRAIGHT_HI = 0.014
TWITCH_GUARD_MIN_REACH = 12.0
STOP_HOLD_MAX_SPEED = 2.5
STOP_HOLD_DIST = 1.5
STOP_HOLD_RELEASE_DIST = 3.0
STOP_HOLD_MIN_DECEL = 0.3
STOP_HOLD_MAX_CURVATURE = 0.01
STOP_GUARD_DIST = 5.0


def _plan_circle_curvature(xs, ys, lookahead: float) -> float:
  px, py = (0.0, 0.0)
  for x, y in zip(xs, ys, strict=False):
    try:
      x, y = (float(x), float(y))
    except (TypeError, ValueError, OverflowError):
      return 0.0
    if not (math.isfinite(x) and math.isfinite(y)):
      return 0.0
    px, py = (x, y)
    if math.hypot(x, y) >= lookahead:
      break
  d2 = px * px + py * py
  if d2 < 1.0:
    return 0.0
  return 2.0 * py / d2


def _plan_dual_probe(model_v2, d_near: float, d_far: float) -> float:
  xs, ys = (model_v2.position.x, model_v2.position.y)
  near = _plan_circle_curvature(xs, ys, d_near)
  far = _plan_circle_curvature(xs, ys, d_far)
  if near * far <= 0.0:
    return 0.0
  return near if abs(near) < abs(far) else far


def get_plan_spatial_curvature(model_v2) -> float:
  return _plan_dual_probe(model_v2, CURVATURE_HOLD_PLAN_LOOKAHEAD_NEAR, CURVATURE_HOLD_PLAN_LOOKAHEAD_FAR)


def get_plan_turn_onset_dist(model_v2) -> float:
  xs, ys = (model_v2.position.x, model_v2.position.y)
  n = min(len(xs), len(ys))
  for i in range(2, n):
    dx = xs[i] - xs[i - 1]
    dy = ys[i] - ys[i - 1]
    if abs(dx) < 0.001 and abs(dy) < 0.001:
      continue
    if abs(math.degrees(math.atan2(dy, dx))) > CURVATURE_HOLD_ONSET_HEADING:
      return math.hypot(xs[i], ys[i])
  return CURVATURE_HOLD_ONSET_FAR


def get_plan_reach(model_v2) -> float:
  try:
    xs = model_v2.position.x
    return float(xs[-1]) if len(xs) else 0.0
  except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
    return 0.0


def _plan_positions_are_finite(model_v2) -> bool:
  try:
    xs, ys = (model_v2.position.x, model_v2.position.y)
    return len(xs) == len(ys) and all((math.isfinite(float(x)) and math.isfinite(float(y)) for x, y in zip(xs, ys, strict=True)))
  except (AttributeError, TypeError, ValueError, OverflowError):
    return False


def limit_curvature_to_plan(model_v2, curvature: float, v_ego: float) -> float:
  if not (math.isfinite(curvature) and math.isfinite(v_ego)):
    return curvature
  if v_ego >= TWITCH_GUARD_MAX_SPEED or curvature == 0.0:
    return curvature
  if not _plan_positions_are_finite(model_v2):
    return curvature
  reach = get_plan_reach(model_v2)
  if not math.isfinite(reach) or reach < TWITCH_GUARD_MIN_REACH:
    return curvature
  plan = abs(_plan_circle_curvature(model_v2.position.x, model_v2.position.y, CURVATURE_HOLD_PLAN_LOOKAHEAD_FAR))
  if not math.isfinite(plan):
    return curvature
  straightness = (plan - TWITCH_GUARD_STRAIGHT_LO) / (TWITCH_GUARD_STRAIGHT_HI - TWITCH_GUARD_STRAIGHT_LO)
  limit = max(TWITCH_GUARD_PLAN_RATIO * plan * min(max(straightness, 0.0), 1.0), TWITCH_GUARD_FLOOR)
  if abs(curvature) <= limit:
    return curvature
  fade = (TWITCH_GUARD_MAX_SPEED - v_ego) / (TWITCH_GUARD_MAX_SPEED - TWITCH_GUARD_FADE_SPEED)
  fade = min(max(fade, 0.0), 1.0)
  return curvature + (math.copysign(limit, curvature) - curvature) * fade


def update_twitch_guard(armed: bool, v_ego: float, standstill: bool) -> bool:
  if not math.isfinite(v_ego):
    return False
  if standstill or abs(v_ego) <= 0.3:
    return True
  return armed and v_ego < TWITCH_GUARD_MAX_SPEED


def stopping_distance(v_ego: float, a_ego: float, max_speed: float = STOP_HOLD_MAX_SPEED) -> float:
  if not (math.isfinite(v_ego) and math.isfinite(a_ego)) or v_ego >= max_speed:
    return math.inf
  return v_ego ** 2 / (2.0 * max(-a_ego, 0.01))
TURN_LEAD_T = 1.3
TURN_LEAD_MIN_M = 4.0
TURN_LEAD_MAX_M = 14.0
TURN_LEAD_MIN_SPEED = 3.0
TURN_LEAD_FULL_SPEED = 4.0
TURN_LEAD_MAX_SPEED = 7.0
TURN_LEAD_SCALE = 0.85
TURN_LEAD_CAP = 0.12
TURN_LEAD_ENGAGED_FRAC = 0.5
TURN_LEAD_MODEL_OPPOSE = 0.003
TURN_LEAD_STOP_MARGIN = 1.5
TURN_LEAD_DECEL_GATE = -0.5


class ModelTurnAssist:

  def __init__(self):
    self.reset()

  def reset(self):
    self.turn_hold_curvature = 0.0
    self.turn_hold_standstill_t = 0.0
    self.turn_hold_swept = 0.0
    self.turn_hold_handoff_t = 0.0
    self.turn_blinker_swept = 0.0
    self.twitch_guard_armed = False
    self.stop_hold_curvature = None
    self.output_curvature = None
    self.curvature = 0.0
    self.turn_hold_done = False

  def update_stopped(self, CS, new_desired_curvature):
    if CS.leftBlinker or CS.rightBlinker or self.output_curvature is None:
      self.reset()
      return new_desired_curvature
    if CS.steeringPressed:
      self.output_curvature = self.curvature
    return self.output_curvature

  def update(self, CS, model_v2, CC, new_desired_curvature):
    blinker_dir = float(CS.rightBlinker) - float(CS.leftBlinker)
    stop_dist = stopping_distance(CS.vEgo, CS.aEgo)
    entering = CS.aEgo < -STOP_HOLD_MIN_DECEL and stop_dist < STOP_HOLD_DIST and abs(self.curvature) < STOP_HOLD_MAX_CURVATURE
    holding = self.stop_hold_curvature is not None and stop_dist < STOP_HOLD_RELEASE_DIST
    if blinker_dir != 0.0:
      self.twitch_guard_armed = False
    elif (CS.aEgo < -STOP_HOLD_MIN_DECEL and abs(self.curvature) < TWITCH_GUARD_STRAIGHT_LO and
          stopping_distance(CS.vEgo, CS.aEgo, TWITCH_GUARD_MAX_SPEED) < STOP_GUARD_DIST):
      self.twitch_guard_armed = True
    if CC.latActive and blinker_dir == 0.0 and self.turn_hold_curvature == 0.0 and (entering or holding):
      if CS.steeringPressed:
        self.stop_hold_curvature = self.curvature
      elif self.stop_hold_curvature is None:
        if self.output_curvature is not None:
          self.stop_hold_curvature = self.output_curvature
        elif self.twitch_guard_armed:
          self.stop_hold_curvature = limit_curvature_to_plan(model_v2, new_desired_curvature, CS.vEgo)
        else:
          self.stop_hold_curvature = new_desired_curvature
      new_desired_curvature = self.stop_hold_curvature
      self.twitch_guard_armed = True
    else:
      self.stop_hold_curvature = None
      if CC.latActive and self.twitch_guard_armed and (blinker_dir == 0.0) and (self.turn_hold_curvature == 0.0):
        new_desired_curvature = limit_curvature_to_plan(model_v2, new_desired_curvature, CS.vEgo)
    if blinker_dir == 0.0:
      self.turn_blinker_swept = 0.0
    else:
      self.turn_blinker_swept += max(CS.vEgo * self.curvature * blinker_dir, 0.0) * DT_CTRL
    if CS.vEgo >= CURVATURE_HOLD_RELEASE_SPEED:
      self.turn_hold_curvature = 0.0
      self.turn_hold_standstill_t = 0.0
      self.turn_hold_swept = 0.0
      self.turn_hold_handoff_t = 0.0
      self.turn_hold_done = False
    else:
      if self.turn_hold_curvature == 0.0:
        self.turn_hold_swept = 0.0
      else:
        self.turn_hold_swept += max(CS.vEgo * self.curvature * math.copysign(1.0, self.turn_hold_curvature), 0.0) * DT_CTRL
      turn_exiting = self.turn_hold_swept > CURVATURE_HOLD_SWEPT_EXIT
      if (CS.vEgo > CURVATURE_HOLD_HARD_SPEED or turn_exiting) and CC.latActive and (self.turn_hold_curvature != 0.0):
        hold_dir = math.copysign(1.0, self.turn_hold_curvature)
        model_mag = max(new_desired_curvature * hold_dir, 0.0)
        if model_mag < abs(self.turn_hold_curvature):
          decay_tau = CURVATURE_HOLD_EXIT_DECAY_TAU if turn_exiting else CURVATURE_HOLD_DECAY_TAU
          decayed = abs(self.turn_hold_curvature) + (model_mag - abs(self.turn_hold_curvature)) * (DT_CTRL / decay_tau)
          self.turn_hold_curvature = math.copysign(decayed, self.turn_hold_curvature)
      if CS.vEgo < 0.5:
        self.turn_hold_standstill_t += DT_CTRL
        if self.turn_hold_standstill_t > CURVATURE_HOLD_STANDSTILL_TIMEOUT:
          self.turn_hold_curvature = 0.0
        self.turn_hold_done = False
      else:
        self.turn_hold_standstill_t = 0.0
      if CC.latActive and self.turn_hold_curvature != 0.0 and (
          new_desired_curvature * math.copysign(1.0, self.turn_hold_curvature) < -CURVATURE_HOLD_OPPOSITE_RELEASE):
        self.turn_hold_curvature = 0.0
        self.turn_hold_done = True
      if CC.latActive and self.turn_hold_curvature != 0.0 and (
          new_desired_curvature * math.copysign(1.0, self.turn_hold_curvature) >= CURVATURE_HOLD_HANDOFF_FRAC * abs(self.turn_hold_curvature)):
        self.turn_hold_handoff_t += DT_CTRL
        if self.turn_hold_handoff_t > CURVATURE_HOLD_HANDOFF_TIME:
          self.turn_hold_curvature = 0.0
          self.turn_hold_done = True
      else:
        self.turn_hold_handoff_t = 0.0
      if blinker_dir == 0.0:
        self.turn_hold_done = False
      elif (CC.latActive and CS.steeringPressed and (CS.steeringTorque * blinker_dir < 0.0) and
            (self.curvature * blinker_dir > CURVATURE_HOLD_CONFIRM_MIN) and (self.turn_blinker_swept < CURVATURE_HOLD_CONFIRM_SWEPT)):
        self.turn_hold_done = False
      if blinker_dir != 0.0 and (not self.turn_hold_done):
        turn_candidate = new_desired_curvature if CC.latActive else 0.0
        if CC.latActive and CS.vEgo < CURVATURE_HOLD_PLAN_SOURCE_SPEED:
          plan_curvature = get_plan_spatial_curvature(model_v2) * CURVATURE_HOLD_PLAN_SCALE
          plan_curvature = max(min(plan_curvature, CURVATURE_HOLD_PLAN_CAP), -CURVATURE_HOLD_PLAN_CAP)
          onset = get_plan_turn_onset_dist(model_v2)
          onset_w = min(max((CURVATURE_HOLD_ONSET_FAR_GATE - onset) / (CURVATURE_HOLD_ONSET_FAR_GATE - CURVATURE_HOLD_ONSET_NEAR), 0.0), 1.0)
          reach = get_plan_reach(model_v2)
          reach_w = min(max((reach - CURVATURE_HOLD_REACH_MIN) / (CURVATURE_HOLD_REACH_FULL - CURVATURE_HOLD_REACH_MIN), 0.0), 1.0)
          plan_curvature *= onset_w * reach_w
          if plan_curvature * blinker_dir > turn_candidate * blinker_dir:
            turn_candidate = plan_curvature
        driver_confirmed = False
        if CC.latActive and CS.steeringPressed and (CS.steeringTorque * blinker_dir < 0.0) and (self.curvature * blinker_dir > CURVATURE_HOLD_CONFIRM_MIN):
          wound_curvature = max(min(self.curvature, CURVATURE_HOLD_PLAN_CAP), -CURVATURE_HOLD_PLAN_CAP)
          if wound_curvature * blinker_dir > turn_candidate * blinker_dir:
            turn_candidate = wound_curvature
            driver_confirmed = True
        if turn_candidate * blinker_dir > abs(self.turn_hold_curvature):
          new_mag = turn_candidate * blinker_dir
          if CS.vEgo > CURVATURE_HOLD_PLAN_SOURCE_SPEED and (not driver_confirmed):
            new_mag = min(new_mag, abs(self.turn_hold_curvature) + CURVATURE_HOLD_RATCHET_RATE * DT_CTRL)
          self.turn_hold_curvature = math.copysign(new_mag, turn_candidate)
        elif self.turn_hold_curvature * blinker_dir < 0.0:
          self.turn_hold_curvature = 0.0
      if CC.latActive and self.turn_hold_curvature != 0.0:
        hold_dir = math.copysign(1.0, self.turn_hold_curvature)
        if new_desired_curvature * hold_dir < abs(self.turn_hold_curvature):
          new_desired_curvature = self.turn_hold_curvature
    if (CC.latActive and (blinker_dir != 0.0) and (model_v2.meta.laneChangeState == 0) and
        (TURN_LEAD_MIN_SPEED <= CS.vEgo < TURN_LEAD_MAX_SPEED) and (new_desired_curvature * blinker_dir > -TURN_LEAD_MODEL_OPPOSE)):
      d_near = max(min(TURN_LEAD_T * CS.vEgo, TURN_LEAD_MAX_M), TURN_LEAD_MIN_M)
      turning = self.curvature * blinker_dir > CURVATURE_HOLD_CONFIRM_MIN
      stopping_short = not turning and CS.aEgo < TURN_LEAD_DECEL_GATE and CS.vEgo ** 2 / (2.0 * -CS.aEgo) < TURN_LEAD_STOP_MARGIN * d_near
      lead_curvature = 0.0 if stopping_short else _plan_dual_probe(model_v2, d_near, d_near + 3.0) * TURN_LEAD_SCALE
      lead_curvature = max(min(lead_curvature, TURN_LEAD_CAP), -TURN_LEAD_CAP)
      if lead_curvature * blinker_dir > 0.0:
        speed_w = min(max((CS.vEgo - TURN_LEAD_MIN_SPEED) / (TURN_LEAD_FULL_SPEED - TURN_LEAD_MIN_SPEED), 0.0), 1.0)
        engaged_ratio = abs(self.curvature) / abs(lead_curvature)
        engage_w = min(max((1.0 - engaged_ratio) / (1.0 - TURN_LEAD_ENGAGED_FRAC), 0.0), 1.0)
        lead_curvature *= speed_w * engage_w
        if lead_curvature * blinker_dir > max(new_desired_curvature * blinker_dir, 0.0):
          new_desired_curvature = lead_curvature
          if CS.vEgo < CURVATURE_HOLD_RELEASE_SPEED and (not self.turn_hold_done) and (lead_curvature * blinker_dir > abs(self.turn_hold_curvature)):
            held_mag = min(lead_curvature * blinker_dir, abs(self.turn_hold_curvature) + CURVATURE_HOLD_RATCHET_RATE * DT_CTRL)
            self.turn_hold_curvature = math.copysign(held_mag, lead_curvature)
    self.output_curvature = new_desired_curvature
    return new_desired_curvature


def bind_turn_assist(controller):
  """Bind policy ownership once while observing later policy replacements."""
  if hasattr(controller, "starpilot_extension"):
    return lambda: bool(getattr(getattr(getattr(controller, "starpilot_extension", None), "policy", None), "turn_assist", False))
  return lambda: bool(getattr(getattr(controller, "ioniq6_policy", None), "turn_assist", False))
