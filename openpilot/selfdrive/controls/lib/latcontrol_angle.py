import math

from openpilot.cereal import log
from opendbc.car.honda.values import CAR as HONDA_CAR, HondaFlags, accord_angle_at_own_ratio
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.starpilot.car.subaru.angle_tune import _ascent_angle_tracking_target, _ascent_low_speed_angle_target

# TODO This is speed dependent
STEER_ANGLE_SATURATION_THRESHOLD = 2.5  # Degrees


class LatControlAngle(LatControl):
  def __init__(self, CP, CI, dt):
    super().__init__(CP, CI, dt)
    self.is_ascent = CP.brand == 'subaru' and CP.carFingerprint == 'SUBARU_ASCENT_2023'
    self.ascent_angle_target = None
    self.sat_check_min_speed = 5.
    # Accord on the angle-loop EPS: the EPS angle loop lags 0.3-0.6 s, so a raw angle-error rule flags ordinary turn
    # entries; its carcontroller limits the setpoint itself (lateral accel/jerk, error clip), so use carOutput instead
    self.is_accord_angle_loop = CP.brand == "honda" and bool(CP.flags & HondaFlags.EPS_ANGLE_LOOP_FW)
    self.use_steer_limited_by_safety = CP.brand in ("tesla", "hyundai") or self.is_accord_angle_loop
    self.is_accord = CP.carFingerprint == HONDA_CAR.HONDA_ACCORD

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited, lat_delay):
    angle_log = log.ControlsState.LateralAngleState.new_message()

    if not active:
      angle_log.active = False
      angle_steers_des = float(CS.steeringAngleDeg)
      self.ascent_angle_target = angle_steers_des
    else:
      angle_log.active = True
      angle_steers_des = math.degrees(VM.get_steer_from_curvature(-desired_curvature, CS.vEgo, params.roll))
      if self.is_accord:
        # controlsd took sR from the rack map at the MEASURED angle; re-evaluate it at the desired angle
        angle_steers_des = accord_angle_at_own_ratio(angle_steers_des, VM.sR)
      angle_steers_des += params.angleOffsetDeg
      if self.is_ascent:
        if self.ascent_angle_target is None:
          self.ascent_angle_target = float(CS.steeringAngleDeg)
        self.ascent_angle_target = _ascent_low_speed_angle_target(
          angle_steers_des, self.ascent_angle_target, CS.vEgo, bool(CS.steeringPressed), self.dt)
        angle_steers_des = _ascent_angle_tracking_target(
          self.ascent_angle_target, CS.steeringAngleDeg, CS.vEgo, bool(CS.steeringPressed))

    if self.use_steer_limited_by_safety:
      # these cars' carcontrollers calculate max lateral accel and jerk, so we can rely on carOutput for saturation
      angle_control_saturated = steer_limited_by_safety
    else:
      # for cars which use a method of limiting torque such as a torque signal (Nissan and Toyota)
      # or relying on EPS (Ford Q3), carOutput does not capture maxing out torque  # TODO: this can be improved
      angle_control_saturated = abs(angle_steers_des - CS.steeringAngleDeg) > STEER_ANGLE_SATURATION_THRESHOLD
    angle_log.saturated = bool(self._check_saturation(angle_control_saturated, CS, False, curvature_limited))
    angle_log.steeringAngleDeg = float(CS.steeringAngleDeg)
    angle_log.steeringAngleDesiredDeg = angle_steers_des
    return 0, float(angle_steers_des), angle_log
