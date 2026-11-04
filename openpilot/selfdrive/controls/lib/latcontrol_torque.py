from openpilot.starpilot.flm import torque_stages as auto_stages
import math
import numpy as np
from collections import deque

from openpilot.cereal import log
from opendbc.car import structs
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.lateral import FRICTION_THRESHOLD, get_friction
from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY, CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.common.pid import PIDController
from openpilot.starpilot.lateral.controller_selection import ControllerMode, default_selection, policy_for
from openpilot.starpilot.lateral.torque_shaping import KP as STARPILOT_KP
from openpilot.starpilot.lateral.torque_extension import apply_turn_assist, create_extension, create_turn_assist
from openpilot.starpilot.flm import live as flm

# At higher speeds (25+mph) we can assume:
# Lateral acceleration achieved by a specific car correlates to
# torque applied to the steering rack. It does not correlate to
# wheel slip, or to speed.

# This controller applies torque to achieve desired lateral
# accelerations. To compensate for the low speed effects the
# proportional gain is increased at low speeds by the PID controller.
# Additionally, there is friction in the steering wheel that needs
# to be overcome to move it at all, this is compensated for too.

KP = 0.8
KI = 0.15

INTERP_SPEEDS = [1, 1.5, 2.0, 3.0, 5, 7.5, 10, 15, 30]
KP_INTERP = [250, 120, 65, 30, 11.5, 5.5, 3.5, 2.0, KP]

LP_FILTER_CUTOFF_HZ = 1.2
JERK_LOOKAHEAD_SECONDS = 0.19
JERK_GAIN = 0.3
LAT_ACCEL_REQUEST_BUFFER_SECONDS = 1.0
VERSION = 1

class LatControlTorque(LatControl):
  def __init__(self, CP, CI, dt, *, controller_mode: ControllerMode | None = None, turn_assist: bool = False):
    super().__init__(CP, CI, dt)
    self.hkg_canfd_torque = bool(CP.brand == 'hyundai' and CP.flags & HyundaiFlags.CANFD and
                                CP.carFingerprint != CAR.HYUNDAI_IONIQ_6 and
                                CP.steerControlType == structs.CarParams.SteerControlType.torque and
                                not CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING and
                                not (CP.notCar or CP.passive or CP.dashcamOnly))
    self.controller_mode = default_selection(CP).mode if controller_mode is None else ControllerMode(controller_mode)
    self.controller_policy = policy_for(CP)
    if self.controller_mode == ControllerMode.STARPILOT and self.controller_policy is None:
      raise ValueError('No StarPilot torque policy for CarParams')
    self.torque_params = CP.lateralTuning.torque.as_builder()
    self.torque_from_lateral_accel = CI.torque_from_lateral_accel()
    self.lateral_accel_from_torque = CI.lateral_accel_from_torque()
    self.pid = PIDController([INTERP_SPEEDS, KP_INTERP], KI, rate=1/self.dt)
    self.update_limits()
    self.steering_angle_deadzone_deg = self.torque_params.steeringAngleDeadzoneDeg
    self.lat_accel_request_buffer_len = int(LAT_ACCEL_REQUEST_BUFFER_SECONDS / self.dt)
    self.lat_accel_request_buffer = deque([0.] * self.lat_accel_request_buffer_len , maxlen=self.lat_accel_request_buffer_len)
    self.lookahead_frames = int(JERK_LOOKAHEAD_SECONDS / self.dt)
    self.jerk_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * LP_FILTER_CUTOFF_HZ), self.dt)
    self.turn_assist = create_turn_assist(CP, turn_assist)
    self.starpilot_extension = create_extension(self, CP, self.controller_mode, self.controller_policy, turn_assist=turn_assist)
    if self.starpilot_extension is not None:
      self.pid._k_p = [[0.0], [STARPILOT_KP]]
    self.flm_source = flm.GmLiveSource(CP, self.controller_mode)
    self.flm_surface = None
    self.manual_flm_effects = None
    self.manual_flm_status = 'not_configured'
    from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values
    self.native_learning_baseline = baseline_values(self)
    self.native_learning_refinement = None
    self.native_learning_provenance = ()
    self.native_learning_last_tick = 0
    self.native_learning_source = None

  def update_torque_parameters(self, latAccelFactor, latAccelOffset, friction):
    if self.starpilot_extension is not None:
      latAccelFactor, latAccelOffset, friction = self.starpilot_extension.transform_torque_parameters(latAccelFactor, latAccelOffset, friction)
    self.torque_params.latAccelFactor = latAccelFactor
    self.torque_params.latAccelOffset = latAccelOffset
    self.torque_params.friction = friction
    self.update_limits()

  def update_limits(self):
    self.pid.set_limits(self.lateral_accel_from_torque(self.steer_max, self.torque_params),
                        self.lateral_accel_from_torque(-self.steer_max, self.torque_params))

  def friction(self, error, deadzone, threshold, CS, setpoint):
    if self.hkg_canfd_torque and not CS.steeringPressed:
      weight = np.interp(CS.vEgo, [50 * CV.MPH_TO_MS, 65 * CV.MPH_TO_MS], [0.0, 1.0])
      weight *= np.interp(abs(setpoint), [0.25, 0.65], [1.0, 0.0])
      threshold += max(np.interp(CS.vEgo, [65 * CV.MPH_TO_MS, 75 * CV.MPH_TO_MS], [0.65, 0.78]) - threshold, 0.0) * weight
      error = math.copysign(max(abs(error) - 0.04 * weight, 0.0), error)
    return get_friction(error, deadzone, threshold, self.torque_params)

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited, lat_delay):
    self.flm_surface = self.flm_source.sample(active=active, speed=CS.vEgo)
    if self.manual_flm_effects is not None:
      self.manual_flm_effects.begin_frame()
    if self.starpilot_extension is not None:
      result = self.starpilot_extension.update(active, CS, VM, params, steer_limited_by_safety,
                                                desired_curvature, curvature_limited, lat_delay)
    else:
      result = self.update_standard(active, CS, VM, params, steer_limited_by_safety,
                                    desired_curvature, curvature_limited, lat_delay)
    return auto_stages.apply_selected_output(self, CS, VM, params, desired_curvature, result,
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)

  def update_standard(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited, lat_delay,
                      *, friction_policy=None):
    pid_log = log.ControlsState.LateralTorqueState.new_message()
    pid_log.version = VERSION
    measured_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
    measurement = measured_curvature * CS.vEgo ** 2
    future_desired_lateral_accel = desired_curvature * CS.vEgo ** 2
    self.lat_accel_request_buffer.append(future_desired_lateral_accel)

    roll_compensation = params.roll * ACCELERATION_DUE_TO_GRAVITY
    curvature_deadzone = abs(VM.calc_curvature(math.radians(self.steering_angle_deadzone_deg + flm.deadband(self.flm_surface, CS.vEgo)), CS.vEgo, 0.0))
    lateral_accel_deadzone = curvature_deadzone * CS.vEgo ** 2
    lateral_accel_deadzone = auto_stages.apply_selected_deadzone(self, CS, VM, float(lateral_accel_deadzone), desired_curvature,
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)

    delay_frames = int(np.clip(lat_delay / self.dt + 1, 1, self.lat_accel_request_buffer_len))
    expected_lateral_accel = self.lat_accel_request_buffer[-delay_frames]
    setpoint = expected_lateral_accel
    error = setpoint - measurement

    lookahead_idx = int(np.clip(-delay_frames + self.lookahead_frames, -self.lat_accel_request_buffer_len+1, -2))
    raw_lateral_jerk = (self.lat_accel_request_buffer[lookahead_idx+1] - self.lat_accel_request_buffer[lookahead_idx-1]) / (2 * self.dt)
    desired_lateral_jerk = self.jerk_filter.update(raw_lateral_jerk)
    gravity_adjusted_future_lateral_accel = future_desired_lateral_accel - roll_compensation
    ff = gravity_adjusted_future_lateral_accel
    # latAccelOffset corrects roll compensation bias from device roll misalignment relative to car roll
    ff -= self.torque_params.latAccelOffset
    threshold = flm.base_threshold(self.flm_surface, CS.vEgo, FRICTION_THRESHOLD)
    ff, threshold = flm.stages(self.flm_surface, CS, setpoint, desired_lateral_jerk, ff, threshold)
    if friction_policy is not None:
      threshold = friction_policy.friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
    ff, threshold = auto_stages.apply_stages(self, CS, setpoint, desired_lateral_jerk, ff, threshold,
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)
    selected_friction = self.friction(error + JERK_GAIN * desired_lateral_jerk, lateral_accel_deadzone, threshold, CS, setpoint)
    ff += auto_stages.apply_selected_friction(self, CS, setpoint, float(selected_friction),
      active=active, limited=steer_limited_by_safety or curvature_limited, curvature_limited=curvature_limited)

    if not active:
      output_torque = 0.0
      pid_log.active = False
    else:
      # do error correction in lateral acceleration space, convert at end to handle non-linear torque responses correctly
      pid_log.error = float(error)

      freeze_integrator = steer_limited_by_safety or CS.steeringPressed or CS.vEgo < 5
      output_lataccel = self.pid.update(pid_log.error, speed=CS.vEgo, feedforward=ff, freeze_integrator=freeze_integrator)
      output_torque = self.torque_from_lateral_accel(output_lataccel, self.torque_params)

      output_torque = apply_turn_assist(self, CS, VM, params, desired_curvature, output_torque)
      output_torque = flm.angle_assist(self.flm_surface, CS, VM, params, desired_curvature, setpoint, desired_lateral_jerk, output_torque)

      if friction_policy is not None:
        output_torque *= friction_policy.output_scale(setpoint, CS.vEgo)

      pid_log.active = True
      pid_log.p = float(self.pid.p)
      pid_log.i = float(self.pid.i)
      pid_log.d = float(self.pid.d)
      pid_log.f = float(self.pid.f)
      pid_log.output = float(-output_torque) # TODO: log lat accel?
      pid_log.actualLateralAccel = float(measurement)
      pid_log.desiredLateralAccel = float(setpoint)
      pid_log.desiredLateralJerk = float(desired_lateral_jerk)
      pid_log.saturated = bool(self._check_saturation(self.steer_max - abs(output_torque) < 1e-3, CS, steer_limited_by_safety, curvature_limited))

    # TODO left is positive in this convention
    return -output_torque, 0.0, pid_log
