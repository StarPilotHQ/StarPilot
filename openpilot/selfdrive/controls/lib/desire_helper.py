from openpilot.cereal import log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.starpilot.lateral.lane_change_preferences import LaneChangePolicy, MINIMUM_SPEED_MPS

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.
LANE_CHANGE_START_TIME = 0.5

class DesireHelper:
  def __init__(self, policy: LaneChangePolicy | None = None):
    self.policy = policy if policy is not None else LaneChangePolicy()
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.prev_one_blinker = False
    self.lane_change_performed = False
    self.desire = log.Desire.none
    self.pre_lane_change_timer = 0.0
    self.auto_signal_blocked = False
    self.last_signal_direction = LaneChangeDirection.none
    self.auto_status = "manualRequired"

  @staticmethod
  def get_lane_change_direction(CS):
    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

  def lane_change_speed(self):
    return max(MINIMUM_SPEED_MPS, self.policy.minimum_speed_mps,
               20 * CV.MPH_TO_MS if self.policy.turn_signal_guidance else 0.0)

  def waiting_for_lane(self, carstate):
    return (self.policy.enabled and carstate.vEgo >= self.lane_change_speed() and
            carstate.leftBlinker != carstate.rightBlinker and
            (self.lane_change_state == LaneChangeState.preLaneChange or
             (self.lane_change_state == LaneChangeState.off and not self.prev_one_blinker)))

  def update(self, carstate, lateral_active, lane_change_prob, *, auto_evidence=False, engaged=False, navigation_turn=False, route_turn=False):
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    if not carstate.leftBlinker and not carstate.rightBlinker:
      self.lane_change_performed = False
      self.auto_signal_blocked = False
    signal_direction = self.get_lane_change_direction(carstate) if one_blinker else LaneChangeDirection.none
    if self.policy.auto_lane_change and (carstate.leftBlinker or carstate.rightBlinker) and (
      not one_blinker or not lateral_active or not engaged or
      (self.last_signal_direction not in (LaneChangeDirection.none, signal_direction))):
      self.auto_signal_blocked = True
    self.last_signal_direction = signal_direction
    self.auto_status = "manualRequired"
    below_lane_change_speed = v_ego < self.lane_change_speed()

    if not self.policy.enabled or not lateral_active or below_lane_change_speed or self.lane_change_timer > LANE_CHANGE_TIME_MAX:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
      self.pre_lane_change_timer = 0.0
    else:
      if navigation_turn and not route_turn and self.lane_change_state == LaneChangeState.preLaneChange:
        self.lane_change_state = LaneChangeState.off
        self.lane_change_direction = LaneChangeDirection.none
        self.pre_lane_change_timer = 0.0
      if (self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and
          not below_lane_change_speed and (route_turn or not navigation_turn) and not (self.policy.one_per_signal and self.lane_change_performed)):
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_timer = 0.0
        self.pre_lane_change_timer = 0.0
        # Initialize lane change direction to prevent UI alert flicker
        self.lane_change_direction = self.get_lane_change_direction(carstate)

      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Update lane change direction
        self.lane_change_direction = self.get_lane_change_direction(carstate)

        torque_applied = carstate.steeringPressed and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                              (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))
        if not (self.policy.auto_lane_change and engaged and auto_evidence and not blindspot_detected and
                not self.auto_signal_blocked):
          self.pre_lane_change_timer = 0.0
        else:
          self.pre_lane_change_timer += DT_MDL
        auto_ready = (self.policy.auto_lane_change and engaged and auto_evidence and not self.auto_signal_blocked and
                      self.pre_lane_change_timer >= self.policy.auto_delay_s)
        if blindspot_detected:
          self.auto_status = "blindspotBlocked"
        elif self.policy.auto_lane_change and engaged:
          self.auto_status = "waitingForDelay" if auto_evidence and not self.auto_signal_blocked else "laneUnavailable"

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
          self.pre_lane_change_timer = 0.0
        elif (torque_applied or auto_ready) and not blindspot_detected and not (self.policy.one_per_signal and self.lane_change_performed):
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.lane_change_timer = 0.0
          self.pre_lane_change_timer = 0.0
          if self.policy.one_per_signal:
            self.lane_change_performed = True

      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        self.lane_change_timer += DT_MDL

        if lane_change_prob < 0.02 and self.lane_change_timer >= LANE_CHANGE_START_TIME:
          self.lane_change_timer = 0.0
          if one_blinker and not route_turn and not (self.policy.one_per_signal and self.lane_change_performed):
            self.lane_change_state = LaneChangeState.preLaneChange
            self.lane_change_direction = self.get_lane_change_direction(carstate)
          else:
            self.lane_change_state = LaneChangeState.off
            self.lane_change_direction = LaneChangeDirection.none

    self.prev_one_blinker = one_blinker and lateral_active

    self.desire = log.Desire.none
    if self.lane_change_state == LaneChangeState.laneChangeStarting:
      if self.lane_change_direction == LaneChangeDirection.left:
        self.desire = log.Desire.laneChangeLeft
      elif self.lane_change_direction == LaneChangeDirection.right:
        self.desire = log.Desire.laneChangeRight
