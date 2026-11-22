import math

from opendbc.car.structs import car
from opendbc.car import apply_hysteresis
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET

def ready(cs, cc):
  return (cc.enabled and cs.canValid and not cs.canTimeout and cs.gearShifter == car.CarState.GearShifter.drive and
          not cs.standstill and not cs.gasPressed and not cs.brakePressed and not cs.accFaulted and
          cs.cruiseState.available and cs.cruiseState.enabled)


def current(sm, name, now_ns):
  return (name in sm.seen and sm.seen[name] and sm.valid[name] and sm.alive[name] and
          0 <= now_ns - int(sm.logMonoTime[name]) <= 150_000_000 and
          0 <= now_ns - int(sm.recv_time[name] * 1e9) <= 150_000_000)


ButtonType = car.CarState.ButtonEvent.Type

SEND_BUTTON_NONE = 0
SEND_BUTTON_INCREASE = 1
SEND_BUTTON_DECREASE = 2

HYST_GAP = 0.0
INCREASE_INACTIVE_TIMER = 0.12
DECREASE_INACTIVE_TIMER = 0.05
LEAD_INCREASE_INACTIVE_TIMER = 0.05
MANUAL_BUTTON_INACTIVE_TIMER = 0.5
LEAD_RECOVERY_LOOKAHEAD_POINTS = 4
LEAD_RECOVERY_HOLD_BUFFER_MS = 1.5 * CV.MPH_TO_MS
LEAD_COAST_BUFFER_MS = 1.0 * CV.MPH_TO_MS
LEAD_EXTRA_COAST_BUFFER_FACTOR = 0.6
LEAD_EXTRA_COAST_BUFFER_MAX_MS = 3.0 * CV.MPH_TO_MS
LEAD_EXTRA_COAST_HEADWAY_MIN_S = 1.5
LEAD_EXTRA_COAST_HEADWAY_MAX_S = 3.0
LEAD_CLOSING_REL_SPEED_MIN_MS = 0.5 * CV.MPH_TO_MS
LEAD_PROACTIVE_COAST_HEADWAY_MAX_S = 4.0
LEAD_DEPARTURE_REL_SPEED_MIN_MS = 1.0 * CV.MPH_TO_MS
LEAD_DEPARTURE_HEADWAY_MIN_S = 1.8
LEAD_DEPARTURE_HEADWAY_MAX_S = 4.5
LEAD_DEPARTURE_BOOST_MIN_MS = 1.25 * CV.MPH_TO_MS
LEAD_DEPARTURE_BOOST_MAX_MS = 3.0 * CV.MPH_TO_MS
LEAD_DEPARTURE_BOOST_FACTOR = 0.50
LEAD_DEPARTURE_PLAN_POINTS = 3

CRUISE_BUTTON_TIMERS = {
  int(ButtonType.decelCruise): 0,
  int(ButtonType.accelCruise): 0,
  int(ButtonType.setCruise): 0,
  int(ButtonType.resumeCruise): 0,
  int(ButtonType.cancel): 0,
  int(ButtonType.mainCruise): 0,
}


def select_redneck_target_speed(v_cruise_kph: float, speed_cluster_ms: float,
                                starpilot_target_speed_ms: float, plan_speeds_ms: list[float],
                                lookahead_points: int, allow_plan_decrease: bool = True,
                                lead_present: bool = False, lead_distance_m: float = 0.0,
                                lead_rel_speed_ms: float = 0.0,
                                slc_target_speed_ms: float = 0.0) -> float:
  target_speed_ms = float(speed_cluster_ms)
  if slc_target_speed_ms > 0:
    target_speed_ms = float(slc_target_speed_ms)
    # SLC is an upper bound for the button-spammed stock setpoint. A driver-set
    # speed below the posted target must still be able to slow the car down.
    if 0 < v_cruise_kph < V_CRUISE_UNSET:
      target_speed_ms = min(target_speed_ms, float(v_cruise_kph) * CV.KPH_TO_MS)
  elif v_cruise_kph > 0:
    target_speed_ms = float(v_cruise_kph) * CV.KPH_TO_MS
  elif starpilot_target_speed_ms > 0:
    target_speed_ms = float(starpilot_target_speed_ms)

  if allow_plan_decrease and len(plan_speeds_ms) > 0:
    lead_closing = lead_present and lead_rel_speed_ms < -LEAD_CLOSING_REL_SPEED_MIN_MS
    if lead_present and not lead_closing and target_speed_ms > speed_cluster_ms and plan_speeds_ms[0] > speed_cluster_ms:
      recovery_lookahead_points = min(len(plan_speeds_ms), LEAD_RECOVERY_LOOKAHEAD_POINTS)
      recovery_target_speed_ms = max(speed_cluster_ms, min(plan_speeds_ms[:recovery_lookahead_points]))
      departure_boost_ms = get_lead_departure_boost_ms(
        speed_cluster_ms,
        lead_distance_m,
        lead_rel_speed_ms,
        plan_speeds_ms,
      )
      if departure_boost_ms > 0.0:
        recovery_target_speed_ms = max(recovery_target_speed_ms, speed_cluster_ms + departure_boost_ms)
      return min(target_speed_ms, recovery_target_speed_ms)

    decrease_target_speed_ms = min(plan_speeds_ms[:lookahead_points])
    lead_headway_s = lead_distance_m / speed_cluster_ms if lead_distance_m > 0.0 and speed_cluster_ms > 0.1 else float("inf")
    proactive_coast = lead_closing and lead_headway_s <= LEAD_PROACTIVE_COAST_HEADWAY_MAX_S

    if not proactive_coast and lead_present and target_speed_ms > speed_cluster_ms and \
        decrease_target_speed_ms >= speed_cluster_ms - LEAD_RECOVERY_HOLD_BUFFER_MS:
      return speed_cluster_ms

    if lead_present and (decrease_target_speed_ms < speed_cluster_ms or proactive_coast):
      decrease_target_speed_ms = max(0.0, decrease_target_speed_ms - get_lead_coast_buffer_ms(
        speed_cluster_ms,
        lead_distance_m,
        lead_rel_speed_ms,
      ))

    if proactive_coast and target_speed_ms > speed_cluster_ms and \
        decrease_target_speed_ms >= speed_cluster_ms - LEAD_RECOVERY_HOLD_BUFFER_MS:
      return speed_cluster_ms

    if decrease_target_speed_ms < target_speed_ms:
      return decrease_target_speed_ms

  return target_speed_ms


def get_lead_coast_buffer_ms(speed_cluster_ms: float, lead_distance_m: float, lead_rel_speed_ms: float) -> float:
  lead_closing_speed_ms = max(-float(lead_rel_speed_ms), 0.0)
  if lead_closing_speed_ms <= 0.0:
    return LEAD_COAST_BUFFER_MS

  headway_factor = 0.0
  if lead_distance_m > 0.0 and speed_cluster_ms > 0.1:
    headway_s = lead_distance_m / speed_cluster_ms
    headway_factor = min(max(
      (LEAD_EXTRA_COAST_HEADWAY_MAX_S - headway_s) /
      (LEAD_EXTRA_COAST_HEADWAY_MAX_S - LEAD_EXTRA_COAST_HEADWAY_MIN_S),
      0.0,
    ), 1.0)

  extra_buffer_ms = min(LEAD_EXTRA_COAST_BUFFER_MAX_MS, lead_closing_speed_ms * LEAD_EXTRA_COAST_BUFFER_FACTOR)
  return LEAD_COAST_BUFFER_MS + extra_buffer_ms * (0.5 + (0.5 * headway_factor))


def get_lead_departure_boost_ms(speed_cluster_ms: float, lead_distance_m: float, lead_rel_speed_ms: float,
                                plan_speeds_ms: list[float]) -> float:
  if lead_rel_speed_ms < LEAD_DEPARTURE_REL_SPEED_MIN_MS or speed_cluster_ms <= 0.1 or lead_distance_m <= 0.0:
    return 0.0

  plan_points = min(len(plan_speeds_ms), LEAD_DEPARTURE_PLAN_POINTS)
  if plan_points <= 0 or min(plan_speeds_ms[:plan_points]) <= speed_cluster_ms:
    return 0.0

  headway_s = lead_distance_m / speed_cluster_ms
  if headway_s < LEAD_DEPARTURE_HEADWAY_MIN_S:
    return 0.0

  headway_factor = min(max(
    (headway_s - LEAD_DEPARTURE_HEADWAY_MIN_S) /
    (LEAD_DEPARTURE_HEADWAY_MAX_S - LEAD_DEPARTURE_HEADWAY_MIN_S),
    0.0,
  ), 1.0)
  extra_boost_ms = max(lead_rel_speed_ms - LEAD_DEPARTURE_REL_SPEED_MIN_MS, 0.0) * LEAD_DEPARTURE_BOOST_FACTOR
  boost_ms = LEAD_DEPARTURE_BOOST_MIN_MS + extra_boost_ms * (0.5 + (0.5 * headway_factor))
  return min(boost_ms, LEAD_DEPARTURE_BOOST_MAX_MS)


def get_minimum_set_speed(is_metric: bool) -> int:
  return 30 if is_metric else 20


def update_manual_button_timers(CS: car.CarState, button_timers: dict[int, int]) -> None:
  for button_type in button_timers:
    if button_timers[button_type] > 0:
      button_timers[button_type] += 1

  for event in CS.buttonEvents:
    button_type = event.type.raw if hasattr(event.type, "raw") else int(event.type)
    if button_type in button_timers:
      button_timers[button_type] = 1 if event.pressed else 0


class RedneckCruise:
  def __init__(self, CP, FPCP):
    self.CP = CP
    self.FPCP = FPCP

    self.v_target = 0
    self.v_target_ms_last = 0.0
    self.v_cruise_cluster = 0
    self.v_cruise_min = 0
    self.state = "inactive"
    self.pre_active_timer = 0
    self.is_ready = False
    self.is_ready_prev = False
    self.cruise_button_timers = dict(CRUISE_BUTTON_TIMERS)

  @staticmethod
  def _send_button_for_state(state: str) -> int:
    if state == "increasing":
      return SEND_BUTTON_INCREASE
    if state == "decreasing":
      return SEND_BUTTON_DECREASE
    return SEND_BUTTON_NONE

  def _reset(self) -> None:
    self.state = "inactive"
    self.pre_active_timer = 0
    self.is_ready = False
    self.is_ready_prev = False

  def _update_calculations(self, CS: car.CarState, v_target_ms: float, is_metric: bool) -> None:
    speed_conv = CV.MS_TO_KPH if is_metric else CV.MS_TO_MPH
    ms_conv = CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS

    self.v_target_ms_last = apply_hysteresis(v_target_ms, self.v_target_ms_last, HYST_GAP * ms_conv)
    self.v_target = round(self.v_target_ms_last * speed_conv)
    self.v_cruise_min = get_minimum_set_speed(is_metric)
    self.v_cruise_cluster = round(CS.cruiseState.speedCluster * speed_conv)

  def _update_readiness(self, CS: car.CarState, CC: car.CarControl) -> None:
    update_manual_button_timers(CS, self.cruise_button_timers)
    button_pressed = any(0 < timer <= int(MANUAL_BUTTON_INACTIVE_TIMER / DT_CTRL) for timer in self.cruise_button_timers.values())
    stock_cruise_ready = (not self.CP.pcmCruise or self.CP.openpilotLongitudinalControl or CS.cruiseState.enabled)
    self.is_ready = (CC.enabled and stock_cruise_ready and not CC.cruiseControl.override and
                     not CC.cruiseControl.cancel and not CC.cruiseControl.resume and not button_pressed)

  def _desired_state(self) -> str:
    if self.v_target > self.v_cruise_cluster:
      return "increasing"
    if self.v_target < self.v_cruise_cluster and self.v_cruise_cluster > self.v_cruise_min:
      return "decreasing"
    return "holding"

  @staticmethod
  def _get_pre_active_frames(state: str, lead_present: bool) -> int:
    if state == "decreasing":
      timer = DECREASE_INACTIVE_TIMER
    elif lead_present:
      timer = LEAD_INCREASE_INACTIVE_TIMER
    else:
      timer = INCREASE_INACTIVE_TIMER
    return int(timer / DT_CTRL)

  def _arm_pre_active(self, desired_state: str, lead_present: bool) -> None:
    if desired_state == "holding":
      self.state = "holding"
      self.pre_active_timer = 0
      return

    self.state = "preActive"
    self.pre_active_timer = self._get_pre_active_frames(desired_state, lead_present)

  def _update_state_machine(self, lead_present: bool) -> int:
    desired_state = self._desired_state()

    if not self.is_ready:
      self.state = "inactive"
      self.pre_active_timer = 0
    elif self.state == "inactive":
      if not self.is_ready_prev:
        self._arm_pre_active(desired_state, lead_present)
    elif self.state == "preActive":
      if desired_state == "holding":
        self.state = "holding"
        self.pre_active_timer = 0
      else:
        desired_frames = self._get_pre_active_frames(desired_state, lead_present)
        self.pre_active_timer = max(0, min(self.pre_active_timer, desired_frames) - 1)
        if self.pre_active_timer <= 0:
          self.state = desired_state
    elif self.state == "holding":
      if desired_state != "holding":
        self._arm_pre_active(desired_state, lead_present)
    elif self.state != desired_state:
      if desired_state == "holding":
        self.state = "holding"
      else:
        self._arm_pre_active(desired_state, lead_present)

    return self._send_button_for_state(self.state)

  def run(self, CS: car.CarState, CC: car.CarControl, v_target_ms: float, is_metric: bool,
          lead_present: bool = False) -> tuple[int, int]:
    if self.FPCP.pcmCruiseSpeed or not self.FPCP.redneckCruiseAvailable:
      self._reset()
      return SEND_BUTTON_NONE, 0

    self._update_calculations(CS, v_target_ms, is_metric)
    self._update_readiness(CS, CC)
    send_button = self._update_state_machine(lead_present)

    self.is_ready_prev = self.is_ready
    return send_button, self.v_target


  def update(self, card, cs, cc, now_ns):
    update(card, cs, cc, now_ns)


def configure(card, services):
  from opendbc.car.hyundai.software_cruise import selected
  if card.CI.CC is None or not selected(card.CP):
    return None
  from types import SimpleNamespace
  from openpilot.cereal import messaging
  services.extend(name for name in ('longitudinalPlan', 'selfdriveState', 'radarState') if name not in services)
  card.sm = messaging.SubMaster(services)
  return RedneckCruise(card.CP, SimpleNamespace(redneckCruiseAvailable=True, pcmCruiseSpeed=False))


def update(card, cs, cc, now_ns):
  controller = card.CI.CC
  controller.software_cruise_command = 0
  owner = card.redneck_cruise
  now_ns = int(now_ns)
  services = ("carControl", "longitudinalPlan", "selfdriveState")
  if (not ready(cs, cc) or not all(current(card.sm, name, now_ns) for name in services) or
      not card.sm["selfdriveState"].active or not card.startup_panda_configured() or
      not all(ps.controlsAllowed and not ps.faults for ps in card.sm["pandaStates"])):
    owner._reset()
    return

  ceiling_kph = float(cs.vCruise)
  acceleration = float(cc.actuators.accel)
  cluster = float(cs.cruiseState.speedCluster)
  if not all(math.isfinite(value) for value in (ceiling_kph, acceleration, cluster, cs.vEgo)) or not 0 < ceiling_kph < V_CRUISE_UNSET:
    owner._reset()
    return

  plan = card.sm["longitudinalPlan"]
  from openpilot.starpilot.speed_limits.physical_actions import active_ceiling
  cap = active_ceiling(card.sm, now_ns) if "slcState" in card.sm.seen and card.slc_control_enabled else None
  if cap is not None:
    limit = card.sm["slcState"]
    cap = float(limit.effectiveClusterTarget) if limit.hasEffectiveClusterTarget else None
    if cap is not None and (not math.isfinite(cap) or not 0 < cap < V_CRUISE_UNSET * CV.KPH_TO_MS):
      cap = None
  if cap is None:
    target = min(ceiling_kph * CV.KPH_TO_MS, max(0.0, cs.vEgo * 1.01 + 3 * acceleration))
  else:
    lead_distance = lead_rel_speed = 0.0
    if current(card.sm, "radarState", now_ns):
      lead = card.sm["radarState"].leadOne
      if lead.present and math.isfinite(lead.dRel) and math.isfinite(lead.vRel):
        lead_distance, lead_rel_speed = float(lead.dRel), float(lead.vRel)
    speeds = [float(speed) for speed in plan.speeds if math.isfinite(speed)]
    target = select_redneck_target_speed(ceiling_kph, cluster, 0.0, speeds, len(speeds) if plan.hasLead else 10,
      bool(plan.hasLead or plan.shouldStop or str(plan.longitudinalPlanSource) != "cruise"),
      bool(plan.hasLead), lead_distance, lead_rel_speed, cap)
  command, _ = owner.run(cs, cc, target, card.is_metric, lead_present=bool(plan.hasLead))
  controller.software_cruise_command = command
  controller.software_cruise_command_ns = now_ns
