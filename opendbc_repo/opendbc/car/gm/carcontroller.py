from opendbc.car.gm.one_pedal import VoltOnePedal
from opendbc.car.gm.steering_authority import EPS_STATUS_TIMEOUT_NS
from opendbc.car.gm.values import gm_control_word, is_volt_one_pedal, camera_acc_pedal_profile, volt_cc_pedal_profile
from opendbc.car.gm.longitudinal import volt_sng_release
from opendbc.car.gm.auto_hold import AutoHold, config_for as auto_hold_config_for, hold_brake as estimate_hold_brake
from opendbc.car.gm.values import is_gm_auto_hold
from opendbc.car.gm.ordinary import demands as ascm_demands
from opendbc.car.gm.truck_longitudinal import TruckTuning, truck_tuning_supported
import math
from opendbc.car.gm.bolt_acc_brake_clock import BoltAccBrakeClock
import numpy as np
from opendbc.can import CANPacker
from opendbc.car import ACCELERATION_DUE_TO_GRAVITY, Bus, DT_CTRL, structs
from opendbc.car.lateral import apply_driver_steer_torque_limits
from opendbc.car.gm import gmcan
from opendbc.car.gm.startup_preferences import gateway_sources_current
from opendbc.car.gm.silverado_cc import SilveradoPedalCommand
from opendbc.car.gm.conventional_pedal_command import ConventionalPedalCommand
from opendbc.car.gm.aol import lateral_request as aol_lateral_request, qualified_gm
from opendbc.car.gm.conventional_pedal import (sources_current as conventional_pedal_sources_current,
                                               cancel_sources_current as conventional_pedal_cancel_sources_current)
from opendbc.car.gm.ordinary_cc import PhysicalObservation, ButtonCadence, button_request as ordinary_button_request
from opendbc.car.gm.cc_longitudinal import VoltCcPhysical, button_request, volt_cc_forward_gear
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.gm.values import (DBC, CanBus, CarControllerParams, CruiseButtons, GMFlags, GMSafetyFlags,
                                   is_volt_gateway_alternate_brake, is_volt_gateway_longitudinal, is_volt_gateway_profile, is_volt_ascm_longitudinal,
                                   is_conventional_cc_pedal_profile, is_silverado_cc_pedal_profile, is_ordinary_ascm_profile,
                                   is_ordinary_camera_profile, is_ordinary_camera_removed,
                                   is_ordinary_sdgm_profile,
                                   is_volt_camera_longitudinal, is_volt_camera_stock, is_volt_sdgm_profile, is_volt_camera_removed,
                                   NO_ACC_BOLT_CAR, is_bolt_pedal_profile, is_bolt_pedal_removed_profile,
                                   is_bolt_present_no_acc_pedal_profile, is_bolt_euv_longitudinal,
                                   is_volt_cc_longitudinal, is_volt_cc_profile, is_silverado_cc_stock_profile, is_ordinary_cc_profile,
                                   CC_GATEWAY_STOCK_CAR, uses_camera_stock_controls, CAR, BOLT_CC_WORDS, is_bolt_cc_profile)
from opendbc.car.interfaces import CarControllerBase

from opendbc.car.gm.hybrid_cc import (HybridPedalCommand, rearm_ready as hybrid_rearm_ready,
                                     lateral_ready as hybrid_lateral_ready, status_ready as hybrid_status_ready)
from opendbc.car.gm.values import malibu_hybrid_profile, is_volt_sdgm_accepted_envelope
from opendbc.car.gm.bolt_cc import BoltCcOwner, BoltCcProfile, auxiliary_messages
from opendbc.car.gm.volt_cc_pedal import VoltCcPedalCommand
from opendbc.car.gm.long_tune import acc_tune_limits

VisualAlert = structs.CarControl.HUDControl.VisualAlert
NetworkLocation = structs.CarParams.NetworkLocation
LongCtrlState = structs.CarControl.Actuators.LongControlState

# Camera cancels up to 0.1s after brake is pressed, ECM allows 0.5s
CAMERA_CANCEL_DELAY_FRAMES = 10
# Enforce a minimum interval between steering messages to avoid a fault
MIN_STEER_MSG_INTERVAL_MS = 15
PEDAL_SENSOR_TIMEOUT_NS = 100_000_000
STOCK_ACC_STATUS_TIMEOUT_NS = 300_000_000  # Three 10 Hz status periods.
CC_GATEWAY_CRUISE_TIMEOUT_NS = 300_000_000
CC_GATEWAY_BUTTON_TIMEOUT_NS = 100_000_000
CAMERA_STOCK_STATUS_TIMEOUT_NS = 300_000_000
BOLT_ACC_PEDAL_FRICTION_RELEASE_FRAMES = 5
VOLT_GRADE_CAR = {CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_2019, CAR.CHEVROLET_VOLT_CAMERA}
VOLT_GATEWAY_GRADE_STOP_SPEED = 0.75
VOLT_CAMERA_GRADE_STOP_SPEED = 0.25
VOLT_EV_THRESHOLD_BP = [1.29, 1.52, 1.55, 1.6, 1.7, 1.8, 2.0, 2.2, 2.5, 5.52, 9.6, 20.5, 23.5, 35.0]
VOLT_EV_THRESHOLD_V = [0.0, -0.14, -0.16, -0.18, -0.215, -0.255, -0.32, -0.41,
                       -0.5, -0.72, -0.895, -1.125, -1.145, -1.16]


def bolt_pedal_launch_gas(accel, speed, orientation, cp, *, acc_tune=False):
  pitch = 0.
  stop_speed = .35 if cp.carFingerprint == CAR.CHEVROLET_BOLT_CC_2022_2023 else .25
  if speed > stop_speed and orientation is not None and len(orientation) == 3 and math.isfinite(orientation[1]):
    pitch = math.sin(orientation[1]) * ACCELERATION_DUE_TO_GRAVITY
    pitch = 0. if pitch > 0. and accel > 0. else min(pitch, .20)
  radius = .075 * cp.wheelbase + .1453
  drag = .5 * .30 * (1.05 * cp.wheelbase + .0679) * 1.225 * speed ** 2
  maximum, switch = acc_tune_limits(speed, 2., float(np.interp(speed, [.5, 10.], [6150, 5500])), 6150)
  if not acc_tune:
    maximum = 2.
    switch = float(np.interp(speed, [.5, 10.], [6150, 5500]))
  scaled = radius * (cp.mass * float(np.clip(accel + pitch, -4., maximum)) + drag) + 6150
  gas = int(round(np.clip(scaled, 5500, 7168)))
  brake = int(round(np.interp(min((scaled - switch) / (radius * cp.mass), 0.), [-4., 0.], [400., 0.])))
  return gas > 5500 and brake == 0


def bolt_euv_demands(accel: float, speed: float, orientation_ned, mass: float,
                     wheelbase: float, max_brake: int, *, acc_tune=False) -> tuple[int, int]:
  """Original ordinary camera Bolt torque/grade law in current DBC engineering units."""
  pitch_accel = 0.0
  if speed > 0.25 and orientation_ned is not None and len(orientation_ned) == 3:
    pitch = orientation_ned[1]
    if math.isfinite(pitch):
      pitch_accel = math.sin(pitch) * ACCELERATION_DUE_TO_GRAVITY
      if pitch_accel > 0.0 and accel > 0.0:
        pitch_accel = 0.0
      else:
        pitch_accel = min(pitch_accel, 0.20)
  tire_radius = 0.075 * wheelbase + 0.1453
  drag = 0.5 * 0.30 * (1.05 * wheelbase + 0.0679) * 1.225 * speed ** 2
  maximum = acc_tune_limits(speed, 2., 6150, 6150)[0] if acc_tune else 2.
  scaled_torque = tire_radius * (mass * float(np.clip(accel + pitch_accel, -4.0, maximum)) + drag) + 6150
  gas = int(round(np.clip(scaled_torque, 5610, 8848))) - 6150
  brake_switch = int(round(np.interp(speed, [0.5, 10.0], [6150, 5610])))
  if acc_tune:
    brake_switch = acc_tune_limits(speed, 2., brake_switch, 6150)[1]
  brake_accel = min((scaled_torque - brake_switch) / (tire_radius * mass), 0.0)
  brake = int(round(np.interp(brake_accel, [-4.0, 0.0], [max_brake, 0])))
  return (-500 if brake > 0 else gas), brake


def volt_demands(accel: float, speed: float, orientation_ned, stopping_speed: float,
                         mass: float, wheelbase: float, accel_min: float, accel_max: float,
                         max_brake: int, *, min_gas: int = -650, max_gas: int = 2041, inactive_gas: int = -650) -> tuple[int, int]:
  """Original Volt demand law in current normalized gas units."""
  pitch_accel = 0.0
  if speed > stopping_speed and orientation_ned is not None and len(orientation_ned) == 3:
    pitch = orientation_ned[1]
    if math.isfinite(pitch):
      pitch_accel = math.sin(pitch) * ACCELERATION_DUE_TO_GRAVITY
      if pitch_accel > 0.0 and accel > 0.0:
        pitch_accel = 0.0
      else:
        pitch_accel = min(pitch_accel, 0.20)

  aero_accel = 0.5 * 0.30 * (1.05 * wheelbase + 0.0679) * 1.225 * speed ** 2 / mass
  gas_accel = float(np.clip(accel + aero_accel + pitch_accel, accel_min, accel_max))
  brake_accel = float(np.clip(accel + aero_accel + pitch_accel * np.interp(speed, [5.0, 10.0], [0.0, 1.0]),
                              accel_min, accel_max))
  threshold = float(np.interp(speed, VOLT_EV_THRESHOLD_BP, VOLT_EV_THRESHOLD_V))
  old_raw_gas = int(round(np.interp(gas_accel, [threshold, max(0.0, threshold), accel_max], [min_gas + 6150, 6150, max_gas + 6150])))
  brake = int(round(np.interp(brake_accel, [accel_min, threshold], [max_brake, 0])))
  gas = int(np.clip(old_raw_gas, min_gas + 6150, max_gas + 6150)) - 6150
  brake = int(np.clip(brake, 0, max_brake))
  return (inactive_gas if brake > 0 else gas), brake


def volt_grade_demands(accel: float, speed: float, orientation_ned, stopping_speed: float,
                       accel_min: float, accel_max: float) -> tuple[float, float]:
  pitch_accel = 0.0
  if speed > stopping_speed and orientation_ned is not None and len(orientation_ned) == 3:
    pitch = orientation_ned[1]
    if math.isfinite(pitch):
      pitch_accel = math.sin(pitch) * ACCELERATION_DUE_TO_GRAVITY
      if pitch_accel > 0.0 and accel > 0.0:
        pitch_accel = 0.0
      else:
        pitch_accel = min(pitch_accel, 0.20)
  gas_demand = float(np.clip(accel + pitch_accel, accel_min, accel_max))
  brake_scale = float(np.interp(speed, [5.0, 10.0], [0.0, 1.0]))
  brake_demand = float(np.clip(accel + pitch_accel * brake_scale, accel_min, accel_max))
  return gas_demand, brake_demand


def fixed_stopping_brake(long_active: bool, near_stop: bool, stopping: bool, resume: bool,
                         stop_accel: float, max_brake: int) -> int | None:
  if not (long_active and near_stop and stopping and not resume):
    return None
  return int(min(-100.0 * stop_accel, max_brake))


def bolt_pedal_fraction(accel: float, speed: float, paddle_pressed: bool) -> float:
  """Frozen speed-shaped pedal calibration; physical response still needs vehicle evidence."""
  gain = np.interp(speed,
                   [0.559, 1.678, 2.797, 3.916, 5.035, 6.154, 7.273, 8.392, 9.511, 10.63,
                    11.749, 12.868, 13.987, 15.106, 16.225, 17.344, 18.463, 19.582, 20.701, 21.820,
                    22.939, 24.058, 25.177, 26.296],
                   [1.01, 1.01, 1.02, 1.05, 1.08, 1.31, 1.33, 1.34, 1.35, 1.36,
                    1.37, 1.38, 1.39, 1.39, 1.40, 1.40, 1.41, 1.42, 1.43, 1.43,
                    1.44, 1.44, 1.45, 1.45])
  gain *= np.interp(speed, [0.0, 2.0, 4.0, 5.5, 8.0, 12.0], [0.92, 0.92, 0.93, 0.94, 0.96, 1.0])
  accel_gain = np.interp(speed, [0.0, 3.0, 8.0, 20.0], [0.47, 0.52, 0.57, 0.61])
  offset = np.interp(speed, [0.0, 1.0, 3.0, 6.0, 15.0, 30.0], [0.085, 0.11, 0.17, 0.23, 0.235, 0.23])
  accel = 0.0 if abs(accel) < 0.04 else accel
  scale = np.interp(abs(accel), [0.0, 0.35, 0.8, 1.5, 2.5],
                    [0.58, 0.68, 0.82, 0.93, 1.0] if accel >= 0 else [0.44, 0.54, 0.70, 0.89, 1.0])
  command = float(np.clip(offset + accel * scale * accel_gain / max(gain, 1e-3) if paddle_pressed else
                          offset + accel * scale * accel_gain, 0.0, 1.0))
  ceiling = np.interp(speed, [0.0, 1.0, 2.5, 4.5, 6.0, 8.0, 12.0], [0.20, 0.235, 0.29, 0.365, 0.52, 0.78, 1.0])
  return float(min(command, ceiling))


def bolt_pedal_slew(target: float, steady: float, accel: float, speed: float) -> float:
  urgency = float(np.clip(abs(accel) / 2.0, 0.0, 1.0))
  rise = np.interp(speed, [0.0, 3.0, 8.0, 20.0], [0.007, 0.012, 0.022, 0.036]) + 0.011 * urgency
  if accel > 0.0 and speed > 6.0:
    rise *= np.interp(abs(accel), [0.0, 0.12, 0.25, 0.45, 0.8], [0.55, 0.58, 0.68, 0.82, 1.0])
  if accel > 1.2:
    rise += np.interp(speed, [0.0, 4.0, 12.0, 25.0], [0.006, 0.005, 0.003, 0.002])
  fall = np.interp(speed, [0.0, 3.0, 8.0, 20.0], [0.008, 0.014, 0.026, 0.045]) + 0.015 * urgency
  return float(np.clip(target, steady - fall, steady + rise))


def bolt_acc_pedal_friction_brake(accel: float, speed: float, stopping: bool, low_speed_active: bool,
                                  mass: float, wheelbase: float, max_brake: int, *, acc_tune=False) -> tuple[int, bool]:
  tire_radius = 0.075 * wheelbase + 0.1453
  frontal_area = 1.05 * wheelbase + 0.0679
  aero_drag_force = 0.5 * 0.30 * frontal_area * 1.225 * speed ** 2
  maximum = acc_tune_limits(speed, 2., 6150, 6150)[0] if acc_tune else 2.
  accel_cmd = float(np.clip(accel, -4.0, maximum))
  scaled_torque = tire_radius * (mass * accel_cmd + aero_drag_force) + 6150
  stock_switch = int(round(np.interp(speed, [0.5, 10.0], [6150, 5500])))
  if acc_tune:
    stock_switch = acc_tune_limits(speed, 2., stock_switch, 6150)[1]
  planner_limit = float(np.interp(speed, [0.0, 1.5, 4.0, 8.0, 15.0, 30.0],
                                  [-0.93, -1.28, -1.98, -2.58, -2.86, -2.95]))
  planner_switch = int(round(tire_radius * (mass * planner_limit + aero_drag_force) + 6150))
  brake_switch = max(stock_switch, planner_switch)
  brake_accel = min((scaled_torque - brake_switch) / (tire_radius * mass), 0.0)
  brake = int(round(np.interp(brake_accel, [-4.0, 0.0], [max_brake, 0.0])))
  if brake > 0:
    full_brake_accel = min(-4.0 + aero_drag_force / mass + (6150 - brake_switch) / (tire_radius * mass), -0.1)
    corrected_scale = 4.0 / max(-full_brake_accel, 0.1)
    speed_gain = float(np.interp(speed, [0.0, 8.0, 15.0, 25.0], [1.0, 1.08, 1.2, 1.35]))
    onset_gain = float(np.interp(brake, [0.0, 5.0, 20.0, 60.0, 120.0, 240.0, max_brake],
                                 [0.0, 1.8, 1.65, 1.4, 1.22, 1.08, 1.0]))
    brake = int(round(np.clip(max(brake * corrected_scale * speed_gain * onset_gain,
                                  np.interp(speed, [0.0, 6.0, 8.0, 12.0, 18.0, 25.0],
                                            [0.0, 0.0, 4.0, 10.0, 20.0, 28.0])), 0, max_brake)))

  if brake <= 0:
    return 0, False
  engage_threshold = float(np.interp(speed, [0.0, 1.5, 3.0, 5.0, 8.0], [40.0, 20.0, 12.0, 10.0, 0.0]))
  release_threshold = float(np.interp(speed, [0.0, 1.5, 3.0, 5.0, 8.0], [0.0, 8.0, 6.0, 4.0, 0.0]))
  if not low_speed_active and brake < engage_threshold:
    return 0, False
  if low_speed_active and brake < release_threshold:
    return 0, False
  if stopping:
    stop_fade = float(np.interp(speed, [0.0, 0.6, 0.9, 1.2, 1.8, 2.8], [0.0, 0.0, 0.05, 0.12, 0.32, 0.78]))
    brake = int(round(brake * stop_fade))
    if brake <= 0 or brake < release_threshold:
      return 0, False
  return brake, True


def suburban_gateway_demands(accel, speed, orientation, cp, *, acc_tune=False):
  """Normal Suburban gateway demand, converted to current gas units."""
  pitch = 0.0
  if orientation is not None and len(orientation) == 3 and speed > 0.5 and math.isfinite(orientation[1]):
    pitch = math.sin(orientation[1]) * ACCELERATION_DUE_TO_GRAVITY
    pitch = 0.0 if pitch > 0.0 and accel > 0.0 else min(pitch, 0.20)
  radius = 0.075 * cp.wheelbase + 0.1453
  frontal = 1.05 * cp.wheelbase + 0.0679
  maximum = acc_tune_limits(speed, 2., 6150, 6150)[0] if acc_tune else 2.
  demand = float(np.clip(accel + pitch, -4.0, maximum))
  torque = radius * (cp.mass * demand + 0.5 * 0.30 * frontal * 1.225 * speed ** 2)
  gas = int(round(np.clip(torque + 6150, 5500, 7168))) - 6150
  brake = int(round(np.interp(min(torque / (radius * cp.mass), 0), [-4.0, 0.0], [400, 0])))
  return (-650 if brake > 0 else gas), brake


def bolt_acc_pedal_dashboard_sensor_ready(CS, now_nanos):
  return (CS.pedal_sensor_healthy and CS.pedal_sensor_ts_nanos > 0 and
          0 <= now_nanos - CS.pedal_sensor_ts_nanos <= PEDAL_SENSOR_TIMEOUT_NS and
          CS.stock_acc_status_ts_nanos > 0 and
          0 <= now_nanos - CS.stock_acc_status_ts_nanos <= STOCK_ACC_STATUS_TIMEOUT_NS)


def bolt_acc_pedal_dashboard_drive_ready(CS, now_nanos, *, allow_paddle=False):
  paddle_ns, paddle_gear = getattr(CS, "bolt_acc_dashboard_paddle", (0, False))
  paddle_display = (allow_paddle and paddle_gear and CS.out.regenBraking and
                    CS.out.gearShifter == structs.CarState.GearShifter.manumatic and
                    0 < paddle_ns <= now_nanos <= paddle_ns + PEDAL_SENSOR_TIMEOUT_NS)
  return ((CS.out.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low) or paddle_display) and
          CS.bolt_pedal_gear_ts_nanos > 0 and 0 <= now_nanos - CS.bolt_pedal_gear_ts_nanos <= 100_000_000 and
          CS.bolt_pedal_main_ts_nanos > 0 and 0 <= now_nanos - CS.bolt_pedal_main_ts_nanos <= 300_000_000)


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.long_pitch = True
    self.long_pitch_input = None
    self.bolt_removed_cancel_credit_used = 0
    self.bolt_pedal_removed = is_bolt_pedal_removed_profile(CP) or is_bolt_pedal_removed_profile(CP, stock_only=True)
    self.bolt_steering_ack_counter = is_bolt_pedal_profile(CP) or is_bolt_pedal_profile(CP, stock_only=True)
    self.bolt_present_no_acc_stock = is_bolt_present_no_acc_pedal_profile(CP, stock_only=True)
    self.truck_tuning = TruckTuning() if truck_tuning_supported(CP) else None
    self.truck_tuning_input = None
    self.gm_acc_tune_input = None
    self.gm_acc_tune = False
    self.start_time = 0.
    self.apply_torque_last = 0
    self.apply_gas = 0
    self.apply_brake = 0
    self.last_steer_frame = 0
    self.last_button_frame = 0
    self.cancel_counter = 0
    self.pedal_steady = 0.0
    self.pedal_active_last = False
    self.volt_sng = False
    self.volt_sng_plan_input = None
    self.volt_one_pedal = False
    self.volt_one_pedal_input = None
    self.gm_auto_hold = False
    self.gm_auto_hold_input = None
    self.gm_auto_hold_state = AutoHold(config=auto_hold_config_for(CP))
    self.regen_paddle_pressed = False
    self.maneuver_paddle_mode = "auto"
    self.maneuver_paddle_input = None
    self.bolt_paddle_switched = False
    self.bolt_regen_hold = False
    self.regen_press_count = 0
    self.regen_release_count = 0
    self.regen_min_on_frames = 0
    self.regen_min_off_frames = 0
    self.paddle_handoff_frames = 0
    self.bolt_acc_pedal_friction_release_frames = 0
    self.bolt_acc_pedal_friction_low_speed_active = False
    self.bolt_acc_pedal_stop_hold = False
    self.bolt_acc_pedal_friction_unavailable = False
    self.bolt_acc_brake_clock = (BoltAccBrakeClock() if int(CP.safetyConfigs[0].safetyParam) == 0x1CD and
                                CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and is_bolt_pedal_profile(CP) else None)

    self.lka_steering_cmd_counter = 0
    self.bolt_steering_pending: tuple[int, int] | None = None
    self.bolt_steering_receipt_seeded = False
    self.lka_icon_status_last = (False, False)

    self.hybrid_profile = malibu_hybrid_profile(self.CP)
    self.hybrid_command = HybridPedalCommand(self.CP) if self.hybrid_profile is not None else None
    self.params = CarControllerParams(self.CP)
    self.volt_one_pedal_state = VoltOnePedal(CP, self.params) if is_volt_one_pedal(CP) else None
    self.volt_gateway_profile = is_volt_gateway_profile(self.CP)
    self.volt_gateway_long = is_volt_gateway_longitudinal(self.CP)
    self.volt_ascm_long = is_volt_ascm_longitudinal(self.CP)
    self.ordinary_ascm_long = is_ordinary_ascm_profile(self.CP, longitudinal=True)
    self.conventional_pedal_profile = is_conventional_cc_pedal_profile(self.CP)
    self.conventional_cancel_credit_used = 0
    self.silverado_cc_pedal_profile = is_silverado_cc_pedal_profile(self.CP)
    self.silverado_pedal_command = SilveradoPedalCommand(self.CP) if self.silverado_cc_pedal_profile else None
    self.conventional_pedal_command = (ConventionalPedalCommand(self.CP)
                                       if self.conventional_pedal_profile and not self.silverado_cc_pedal_profile else None)
    self.volt_cc_pedal_profile = volt_cc_pedal_profile(self.CP)
    self.volt_cc_pedal_command = VoltCcPedalCommand() if self.volt_cc_pedal_profile is not None else None
    self.volt_cc_pedal_cancel_used = 0
    self.camera_pedal_profile = camera_acc_pedal_profile(self.CP)
    self.camera_pedal_launch = False
    self.camera_pedal_input = None
    self.longitudinal_maneuver_input = None
    self.longitudinal_maneuver_mode = False
    self.ordinary_camera_long = (is_ordinary_camera_profile(self.CP, longitudinal=True) or
                                 self.camera_pedal_profile is not None and not self.camera_pedal_profile.volt and self.camera_pedal_profile.longitudinal)
    self.ordinary_camera_stock = is_ordinary_camera_profile(self.CP)
    self.ordinary_camera_removed = (is_ordinary_camera_removed(self.CP) or
                                    self.camera_pedal_profile is not None and self.camera_pedal_profile.removed)
    self.ordinary_sdgm_long = is_ordinary_sdgm_profile(self.CP, longitudinal=True)
    self.volt_sdgm_long = is_volt_sdgm_profile(self.CP, longitudinal=True)
    self.volt_camera_removed = is_volt_camera_removed(self.CP)
    self.volt_camera_removed_long = is_volt_camera_removed(self.CP, longitudinal=True)
    self.volt_removed_cancel_credit_used = 0
    self.volt_camera_long = is_volt_camera_longitudinal(self.CP) or self.volt_camera_removed_long
    self.bolt_euv_long = is_bolt_euv_longitudinal(self.CP)
    self.volt_cc_profile = is_volt_cc_profile(self.CP)
    self.ordinary_cc_profile = is_ordinary_cc_profile(self.CP)
    self.ordinary_cc_cadence = ButtonCadence(self.CP.carFingerprint == CAR.CADILLAC_XT4_CC)
    self.volt_cc_long = is_volt_cc_longitudinal(self.CP)
    self.bolt_cc_profile = is_bolt_cc_profile(self.CP)
    self.bolt_cc_owner = (BoltCcOwner(BoltCcProfile(self.CP.carFingerprint,
                          camera_removed=self.CP.safetyConfigs[0].safetyParam == BOLT_CC_WORDS[self.CP.carFingerprint][1]))
                          if self.bolt_cc_profile else None)
    self.bolt_cc_last_cruise_source = self.bolt_cc_last_button_source = 0
    self.bolt_cc_metric = False
    self.bolt_cc_last_camera_source = 0
    self.volt_cc_prev_enabled = False
    self.volt_cc_cancel_pending = False
    self.volt_cc_last_cancel_frame = 0
    self.volt_cc_metric = False
    self.volt_cc_speed_ceiling_mps = None
    self.volt_cc_last_button_frame = 0
    self.volt_cc_last_direction_frame = None
    self.volt_cc_consumed_source_ns = 0

    # Match the normal full-ACC producer, not a model list: CC-only and standalone
    # pedal owners never replace this stream. Qualify once from the final config.
    self.startup_acc_keepalive = (qualified_gm(self.CP) and self.CP.openpilotLongitudinalControl and
                                  not self.CP.flags & GMFlags.CC_LONG and
                                  (not self.CP.flags & GMFlags.PEDAL_LONG or self.camera_pedal_profile is not None))
    self.packer_pt = CANPacker(DBC[self.CP.carFingerprint][Bus.pt])
    self.packer_obj = CANPacker(DBC[self.CP.carFingerprint][Bus.radar])
    self.packer_ch = CANPacker(DBC[self.CP.carFingerprint][Bus.chassis])

  def bolt_pedal_admission(self, CC, CS, now_nanos):
    age_ns = now_nanos - CS.pedal_sensor_ts_nanos
    sensor_ready = (CS.pedal_sensor_healthy and CS.pedal_sensor_ts_nanos > 0 and
                    0 <= age_ns <= PEDAL_SENSOR_TIMEOUT_NS)
    stock_acc = self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL
    stock_age = now_nanos - CS.stock_acc_status_ts_nanos if stock_acc else 0
    removed_stock_current = (not self.bolt_pedal_removed or
                             CS.bolt_pedal_removed_stock_ts_nanos > 0 and
                             0 <= now_nanos - CS.bolt_pedal_removed_stock_ts_nanos <= STOCK_ACC_STATUS_TIMEOUT_NS)
    owner_clear = (not stock_acc or
                   (CS.stock_acc_status_ts_nanos > 0 and 0 <= stock_age <= STOCK_ACC_STATUS_TIMEOUT_NS and
                    not CS.out.cruiseState.enabled and removed_stock_current and
                    (not self.bolt_pedal_removed or not CS.bolt_pedal_removed_acc_active)))
    gen2_drive = (self.CP.carFingerprint == CAR.CHEVROLET_BOLT_CC_2022_2023 and
                  int(self.CP.safetyConfigs[0].safetyParam) == 0x19D and
                  CS.out.gearShifter == structs.CarState.GearShifter.drive)
    in_regen_gear = CS.out.gearShifter == structs.CarState.GearShifter.low or gen2_drive
    drive_ready = in_regen_gear or (stock_acc and CS.out.gearShifter == structs.CarState.GearShifter.drive)
    # Panda requires current physical gear for every Bolt pedal generation.
    gear_ns = getattr(CS, "bolt_pedal_gear_ts_nanos", 0)
    drive_ready = drive_ready and gear_ns > 0 and 0 <= now_nanos - gear_ns <= 100_000_000
    if stock_acc:
      main_ns = getattr(CS, "bolt_pedal_main_ts_nanos", 0)
      drive_ready = drive_ready and main_ns > 0 and 0 <= now_nanos - main_ns <= 300_000_000

    active = (CC.longActive and sensor_ready and drive_ready and not CS.out.brakePressed and
              not CS.out.gasPressed and not CS.out.regenBraking and owner_clear and
              (not stock_acc or CS.out.cruiseState.available))
    return active, owner_clear, in_regen_gear

  def update_bolt_paddle(self, commanded_accel, measured_accel, speed, active):
    self.bolt_paddle_switched = False
    if not active:
      self.bolt_regen_hold = False
      self.regen_paddle_pressed = False
      self.regen_press_count = 0
      self.regen_release_count = 0
      self.regen_min_on_frames = 0
      self.regen_min_off_frames = 0
      return False

    speed_bp = [0., 4., 12., 25.]
    press_cmd = np.interp(speed, speed_bp, [-0.90, -0.82, -0.72, -0.65])
    release_cmd = np.interp(speed, speed_bp, [-0.10, -0.17, -0.24, -0.30])
    press_measured = np.interp(speed, speed_bp, [-0.95, -0.86, -0.76, -0.70])
    release_measured = np.interp(speed, speed_bp, [-0.16, -0.23, -0.30, -0.36])
    press_frames = int(round(np.interp(speed, speed_bp, [8., 6., 5., 4.])))
    release_frames = int(round(np.interp(speed, speed_bp, [18., 15., 12., 10.])))
    min_on = int(round(np.interp(speed, speed_bp, [34., 27., 20., 16.])))
    min_off = int(round(np.interp(speed, speed_bp, [16., 14., 12., 10.])))
    boost = int(round(np.interp(speed, [0., 6., 8., 20., 25.], [0., 0., 3., 3., 0.])))
    press_frames += boost
    release_frames += boost

    if self.bolt_regen_hold or commanded_accel <= press_cmd or measured_accel <= press_measured:
      self.regen_press_count += 1
    else:
      self.regen_press_count = max(self.regen_press_count - 1, 0)
    if not self.bolt_regen_hold and commanded_accel >= release_cmd and measured_accel >= release_measured:
      self.regen_release_count += 1
    else:
      self.regen_release_count = max(self.regen_release_count - 1, 0)
    if self.bolt_regen_hold and commanded_accel <= press_cmd - 0.30:
      self.regen_press_count = max(self.regen_press_count, press_frames)
    self.regen_min_on_frames = max(self.regen_min_on_frames - 1, 0)
    self.regen_min_off_frames = max(self.regen_min_off_frames - 1, 0)
    selected_before = self.regen_paddle_pressed
    if self.regen_paddle_pressed:
      if self.regen_min_on_frames == 0 and self.regen_release_count >= release_frames:
        self.regen_paddle_pressed = False
        self.regen_min_off_frames = min_off
        self.regen_release_count = 0
    elif self.regen_min_off_frames == 0 and self.regen_press_count >= press_frames:
      self.regen_paddle_pressed = True
      self.regen_min_on_frames = min_on
      self.regen_press_count = 0
    self.bolt_paddle_switched = self.regen_paddle_pressed != selected_before
    if self.maneuver_paddle_mode == "off":
      self.regen_paddle_pressed = False
      self.regen_press_count = self.regen_release_count = self.regen_min_on_frames = 0
    elif self.maneuver_paddle_mode == "force":
      self.regen_paddle_pressed = commanded_accel < -.02
    return self.regen_paddle_pressed

  def observe_steering_receipts(self, can_packets):
    for stamp, packets in can_packets:
      for address, raw, bus in packets:
        if address != 0x180 or len(raw) != 4 or bus not in (128, 192):
          continue
        counter = (raw[0] >> 4) & 3
        pending = self.bolt_steering_pending
        if pending is not None:
          if counter != pending[0] or stamp < pending[1]:
            continue
          self.bolt_steering_pending = None
          # A rejected packet never reached the bus; retry its counter.
          self.lka_steering_cmd_counter = (counter + 1) % 4 if bus == 128 else counter
        elif bus == 128 and not self.bolt_steering_receipt_seeded:
          self.lka_steering_cmd_counter = (counter + 1) % 4

  def update(self, CC, CS, now_nanos, *, startup_keepalive=False):
    if self.bolt_acc_brake_clock is not None and getattr(CS, "bolt_acc_pedal_friction_unavailable", False):
      # Do not keep requesting a rejected secondary brake or retry after a flag flap.
      # Only a new onroad controller can restore friction; pedal/regen keep their gates.
      self.bolt_acc_pedal_friction_unavailable = True
      self.apply_brake = 0
      self.bolt_acc_pedal_friction_low_speed_active = False
    if startup_keepalive:
      CC = structs.CarControl.new_message().as_reader()
    _projection = self.learning_projection_observer
    if _projection is not None:
      _projection.begin(CC.actuators.torque, self.frame)
    long_pitch = self.long_pitch
    if ((self.camera_pedal_profile is not None and self.camera_pedal_profile.volt or self.volt_cc_pedal_profile is not None)
        and self.frame % 25 == 0):
      self.longitudinal_maneuver_mode = bool(self.longitudinal_maneuver_input is not None and
                                           self.longitudinal_maneuver_input.update(now_nanos))
    if self.maneuver_paddle_input is not None and self.frame % 25 == 0:
      self.maneuver_paddle_mode = self.maneuver_paddle_input.update(now_nanos)
    pedal_hold_ready = (self.camera_pedal_profile is None or not self.camera_pedal_profile.volt or
                        (CS.pedal_sensor_healthy and 0 <= now_nanos - CS.pedal_sensor_ts_nanos <= PEDAL_SENSOR_TIMEOUT_NS))
    # Sample physical hold dwell at parser/controller cadence, before current brake demands.
    hold_brake = None
    one_pedal_enabled = (self.volt_one_pedal and is_volt_one_pedal(self.CP) and self.volt_one_pedal_input is not None and
                         self.volt_one_pedal_input.update(now_nanos))
    one_pedal_active = False
    if not startup_keepalive and is_gm_auto_hold(self.CP):
      hold_enabled = self.gm_auto_hold and self.gm_auto_hold_input is not None and self.gm_auto_hold_input.update(now_nanos)
      physical_current = (pedal_hold_ready and bool(CS.gm_auto_hold_sources) and
                          all(0 < stamp <= now_nanos and now_nanos - stamp <= limit for stamp, limit in CS.gm_auto_hold_sources) and
                          not CS.gm_auto_hold_unavailable)
      one_pedal_ready = (one_pedal_enabled and physical_current and CS.volt_one_pedal_mode and
                         0 < CS.volt_one_pedal_mode_ns <= now_nanos and now_nanos - CS.volt_one_pedal_mode_ns <= 300_000_000 and
                         CS.out.cruiseState.available and CS.gm_auto_hold_forward and
                         not CS.out.gasPressed and not CS.out.brakePressed and not CS.out.regenBraking)
      hold_brake = self.gm_auto_hold_state.update(
        CS.out, enabled=hold_enabled or one_pedal_enabled, sources_current=pedal_hold_ready and all(0 < stamp <= now_nanos and now_nanos - stamp <= limit
                                                         for stamp, limit in CS.gm_auto_hold_sources) and bool(CS.gm_auto_hold_sources) and
        not CS.gm_auto_hold_unavailable, long_active=CC.longActive, driver_brake=CS.gm_auto_hold_brake,
        controller_brake=self.apply_brake, wheel_ns=CS.gm_auto_hold_wheel_ns, now_ns=now_nanos,
        physical_forward=CS.gm_auto_hold_forward, moving=CS.gm_auto_hold_moving, sent_hold=CS.gm_auto_hold_engaged)
      if not hold_enabled:
        hold_brake = None
      one_pedal_active = (one_pedal_ready and self.gm_auto_hold_state.drive_ns >= 3_000_000_000 and
                          CS.out.canValid and not CS.out.canTimeout and not CC.longActive and
                          (CS.volt_one_pedal_moving or CS.volt_one_pedal_stopped))
      if hold_brake is None and not (one_pedal_active and CS.volt_one_pedal_stopped and
                                     (CS.out.standstill or CS.out.vEgo < .02)):
        CS.gm_auto_hold_engaged = False
      if self.volt_one_pedal_state is not None:
        self.volt_one_pedal_state.observe(eligible=one_pedal_ready and self.gm_auto_hold_state.drive_ns >= 3_000_000_000 and
                                          not CC.longActive, gas_pressed=CS.out.gasPressed,
                                          speed=CS.out.vEgo)
    self.gm_acc_tune = (self.gm_acc_tune_input is not None and self.gm_acc_tune_input.update(now_nanos))
    if self.truck_tuning is not None:
      self.truck_tuning.enabled = bool(self.truck_tuning_input is not None and
                                       self.truck_tuning_input.update(now_nanos))
    actuators = CC.actuators
    hud_control = CC.hudControl
    hud_alert = hud_control.visualAlert
    hud_v_cruise = hud_control.setSpeed
    if hud_v_cruise > 70:
      hud_v_cruise = 0

    # Send CAN commands.
    can_sends = []
    cc_gateway = self.CP.carFingerprint in CC_GATEWAY_STOCK_CAR or self.volt_cc_profile
    stock_cruise_fresh = (not cc_gateway or
                          (CS.cc_gateway_cruise_ts_nanos > 0 and
                           0 <= now_nanos - CS.cc_gateway_cruise_ts_nanos <= CC_GATEWAY_CRUISE_TIMEOUT_NS))
    stock_steer_ready = (not cc_gateway or
                         (stock_cruise_fresh and CS.out.cruiseState.available and CS.out.cruiseState.enabled and
                          not CS.out.brakePressed and not CS.out.gasPressed))
    aol_lateral = aol_lateral_request(self.CP, CC)
    if self.hybrid_profile is not None:
      stock_steer_ready = hybrid_lateral_ready(CS, now_nanos) and (aol_lateral or
                           CS.out.cruiseState.enabled and not CS.out.brakePressed and not CS.out.gasPressed)
    if is_silverado_cc_stock_profile(self.CP):
      sources = CS.silverado_stock_sources
      stock_steer_ready = (CS.out.canValid and not CS.out.canTimeout and len(sources) == 8 and
                          all(0 < stamp <= now_nanos <= stamp + limit for stamp, limit in sources) and
                          CS.out.cruiseState.available and (CS.out.cruiseState.enabled or aol_lateral) and
                          volt_cc_forward_gear(CS.out.gearShifter) and
                          (aol_lateral or not CS.out.brakePressed and not CS.out.gasPressed))

    if self.volt_cc_profile or self.ordinary_cc_profile:
      physical = getattr(CS, 'volt_cc_physical', None)
      stock_steer_ready = (isinstance(physical, (VoltCcPhysical, PhysicalObservation)) and physical.sources_current(now_nanos) and
                           CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and
                           (CS.out.cruiseState.enabled or aol_lateral) and volt_cc_forward_gear(CS.out.gearShifter))
    if self.ordinary_cc_profile:
      stock_steer_ready = (isinstance(physical, PhysicalObservation) and physical.sources_current(now_nanos) and
                          CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and
                          volt_cc_forward_gear(CS.out.gearShifter))
    if self.volt_gateway_profile and not self.CP.openpilotLongitudinalControl:
      stock_steer_ready = (gateway_sources_current(getattr(CS, "volt_gateway_source_ns", ()), now_nanos) and
                          CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and
                          (aol_lateral or not CS.out.brakePressed and not CS.out.regenBraking) and
                          CS.cruise_buttons != CruiseButtons.CANCEL)
    if uses_camera_stock_controls(self.CP):
      status_age = now_nanos - CS.camera_stock_status_ts_nanos
      stock_steer_ready = (CS.camera_stock_sources_valid and CS.camera_stock_status_ts_nanos > 0 and
                           0 <= status_age <= CAMERA_STOCK_STATUS_TIMEOUT_NS and
                           CS.out.cruiseState.available and (CS.out.cruiseState.enabled or aol_lateral) and
                           (aol_lateral or not CS.out.brakePressed and not CS.out.regenBraking) and
                           (aol_lateral or not CS.out.gasPressed or is_volt_camera_stock(self.CP) or is_volt_sdgm_profile(self.CP)
                               or is_ordinary_sdgm_profile(self.CP, longitudinal=False) or self.ordinary_camera_stock) and not CS.out.accFaulted)
    if self.volt_camera_long and not self.volt_camera_removed or self.volt_sdgm_long or self.ordinary_sdgm_long or self.ordinary_camera_long:
      status_age = now_nanos - CS.camera_stock_status_ts_nanos
      stock_steer_ready = (CS.camera_stock_sources_valid and CS.camera_stock_status_ts_nanos > 0 and
                           0 <= status_age <= CAMERA_STOCK_STATUS_TIMEOUT_NS and
                           CS.out.cruiseState.available)
    bolt_sources = getattr(CS, "bolt_cc_sources", ())
    bolt_sources_fresh = (len(bolt_sources) == 7 and all(stamp > 0 and 0 <= now_nanos - stamp <= 300_000_000
                                                     for stamp, raw in bolt_sources))
    if self.bolt_cc_profile:
      stock_steer_ready = (bolt_sources_fresh and CS.out.canValid and not CS.out.canTimeout and
                          CS.out.cruiseState.available and (aol_lateral or bool(bolt_sources[1][1][4] & 128)) and
                          CS.out.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low) and
                          (aol_lateral or not CS.out.brakePressed and not CS.out.regenBraking))
    if self.ordinary_camera_removed and self.camera_pedal_profile is None:
      stock_steer_ready = (CS.out.canValid and not CS.out.canTimeout and len(CS.ordinary_removed_sources) == 6 and
                           all(stamp > 0 and 0 <= now_nanos - stamp <= 300_000_000 for stamp in CS.ordinary_removed_sources))
    if self.camera_pedal_profile is not None and self.camera_pedal_profile.removed:
      sources = CS.camera_pedal_sources if self.camera_pedal_profile.longitudinal else CS.camera_pedal_sources[:6]
      stock_steer_ready = (CS.out.canValid and not CS.out.canTimeout and bool(sources) and
                           all(stamp > 0 and 0 <= now_nanos - stamp <= limit for stamp, limit in sources))
      if self.camera_pedal_profile.longitudinal:
        stock_steer_ready = (stock_steer_ready and CS.pedal_sensor_healthy and
                            0 <= now_nanos - CS.pedal_sensor_ts_nanos <= PEDAL_SENSOR_TIMEOUT_NS)
    if self.volt_camera_removed and self.camera_pedal_profile is None:
      stock_steer_ready = (CS.out.canValid and not CS.out.canTimeout and len(CS.volt_removed_sources) == 6 and
                           all(stamp > 0 and 0 <= now_nanos - stamp <= 300_000_000 for stamp in CS.volt_removed_sources))
    if self.conventional_pedal_profile:
      stock_steer_ready = conventional_pedal_sources_current(CS, now_nanos) and CS.out.cruiseState.available
    if self.bolt_pedal_removed:
      sources = CS.bolt_pedal_removed_sources
      stock_steer_ready = (CS.out.canValid and not CS.out.canTimeout and len(sources) == (9 if self.CP.openpilotLongitudinalControl else 7) and
                           all(stamp > 0 and 0 <= now_nanos - stamp <= limit for stamp, limit in sources))
      if self.CP.openpilotLongitudinalControl:
        stock_steer_ready = (stock_steer_ready and CS.pedal_sensor_healthy and
                            0 < CS.pedal_sensor_ts_nanos <= now_nanos <= CS.pedal_sensor_ts_nanos + PEDAL_SENSOR_TIMEOUT_NS)
    # An SDGM EPS can report inactive while the driver holds the brake. Emit
    # neutral for that measured pause so releasing the brake starts a new request.
    # Keep the existing acquisition deadline and genuine refusal latch intact.
    sdgm_brake_pause = False
    if self.volt_sdgm_long and CS.out.brakePressed:
      steering = CS.steering_authority
      sdgm_brake_pause = (steering.enabled and steering.status == 0 and
                          0 < steering.status_ns <= now_nanos <= steering.status_ns + EPS_STATUS_TIMEOUT_NS)
    lat_active = CC.latActive and stock_steer_ready and not CS.steering_authority.latched and not sdgm_brake_pause

    # Steering (Active: 50Hz, inactive: 10Hz)
    steer_step = self.params.STEER_STEP if lat_active else self.params.INACTIVE_STEER_STEP

    if (not self.bolt_pedal_removed and not (self.hybrid_profile is not None and self.hybrid_profile.removed) and
        self.CP.networkLocation == NetworkLocation.fwdCamera and
        not (self.conventional_pedal_profile and not self.silverado_cc_pedal_profile and self.CP.flags & GMFlags.NO_CAMERA)):
      # Also send at 50Hz:
      # - on startup, first few msgs are blocked
      # - until we're in sync with camera so counters align when relay closes, preventing a fault.
      #   openpilot can subtly drift, so this is activated throughout a drive to stay synced
      out_of_sync = (not self.volt_camera_removed and
                     self.lka_steering_cmd_counter % 4 != (CS.cam_lka_steering_cmd_counter + 1) % 4)
      if CS.loopback_lka_steering_cmd_ts_nanos == 0 or out_of_sync:
        steer_step = self.params.STEER_STEP

    if not self.bolt_steering_ack_counter:
      self.lka_steering_cmd_counter += 1 if CS.loopback_lka_steering_cmd_updated else 0

    # Avoid GM EPS faults when transmitting messages too close together: skip this transmit if we
    # received the ASCMLKASteeringCmd loopback confirmation too recently
    last_lka_steer_msg_ms = (now_nanos - CS.loopback_lka_steering_cmd_ts_nanos) * 1e-6
    if ((self.frame - self.last_steer_frame) >= steer_step and last_lka_steer_msg_ms > MIN_STEER_MSG_INTERVAL_MS and
        (not self.bolt_steering_ack_counter or self.bolt_steering_pending is None)):
      # Initialize ASCMLKASteeringCmd counter using the camera until we get a msg on the bus
      if (CS.loopback_lka_steering_cmd_ts_nanos == 0 and not self.bolt_steering_receipt_seeded and
          not self.bolt_pedal_removed and not (self.hybrid_profile is not None and self.hybrid_profile.removed)):
        self.lka_steering_cmd_counter = CS.pt_lka_steering_cmd_counter + 1

      if lat_active and not (self.ordinary_cc_profile and CC.enabled and not CS.out.cruiseState.enabled and not aol_lateral):
        new_torque = int(round(actuators.torque * self.params.STEER_MAX))
        if _projection is not None:
          _projection.input(new_torque, self.params.STEER_MAX, self.apply_torque_last, CS.out.steeringTorque, self.params, limiter='driver')
        apply_torque = apply_driver_steer_torque_limits(new_torque, self.apply_torque_last, CS.out.steeringTorque, self.params)
      else:
        apply_torque = 0

      self.last_steer_frame = self.frame
      self.apply_torque_last = apply_torque
      idx = self.lka_steering_cmd_counter % 4
      can_sends.append(gmcan.create_steering_control(self.packer_pt, CanBus.POWERTRAIN, apply_torque, idx, lat_active))
      if self.bolt_steering_ack_counter:
        self.bolt_steering_pending = (idx, now_nanos)
        self.bolt_steering_receipt_seeded = True
      if _projection is not None:
        _projection.emit('gm.ASCMLKASteeringCmd', apply_torque, lat_active, can_sends[-1])
      CS.steering_authority.command(now_nanos, lat_active)

    # Every full-ACC owner must service its replacement stream after safety takes
    # ownership. CC was replaced with a disabled command and startup excludes hold.
    if startup_keepalive and not self.startup_acc_keepalive:
      # Other profiles retain their steering-only startup path. Never run cruise-button, pedal,
      # brake, dashboard, or hold producers before normal controls are ready.
      new_actuators = actuators.as_builder()
      new_actuators.torque = 0.0
      new_actuators.torqueOutputCan = 0
      self.frame += 1
      if _projection is not None:
        _projection.finish(0.0)
      return new_actuators, can_sends

    status_bolt = self.CP.carFingerprint in NO_ACC_BOLT_CAR and (
      is_bolt_present_no_acc_pedal_profile(self.CP) or is_bolt_pedal_removed_profile(self.CP))
    status_hybrid = (self.hybrid_profile is not None and self.hybrid_profile.pedal and self.hybrid_profile.longitudinal and
                     hybrid_status_ready(CS, now_nanos))
    if (status_bolt or status_hybrid) and self.frame % 4 == 0:
      can_sends.append(gmcan.create_ecm_cruise_control_command(
        self.packer_pt, CanBus.POWERTRAIN, True, hud_v_cruise * CV.MS_TO_KPH))

    if self.hybrid_profile is not None:
      profile = self.hybrid_profile
      ready = hybrid_rearm_ready(CS, now_nanos)
      sensor_ready = (not profile.pedal or CS.pedal_sensor_healthy and
                      0 < CS.pedal_sensor_ts_nanos <= now_nanos <= CS.pedal_sensor_ts_nanos + 100_000_000)
      if profile.pedal and profile.longitudinal and self.frame % 4 == 0:
        if ready and sensor_ready and not CS.out.cruiseState.enabled:
          pedal = self.hybrid_command.update(
            actuators.accel, CC.enabled and CC.longActive, CS.out,
            stopping=actuators.longControlState == LongCtrlState.stopping, resume=CC.cruiseControl.resume,
            orientation=CC.orientationNED if long_pitch else None, acc_tune=self.gm_acc_tune)
        else:
          self.hybrid_command.withdraw()
          pedal = 0.
        self.apply_gas, self.apply_brake = pedal, 0
        can_sends.append(gmcan.create_pedal_command(self.packer_pt, pedal, (self.frame // 4) % 16))
      self.cancel_counter = self.cancel_counter + 1 if CC.cruiseControl.cancel else 0
      if profile.longitudinal:
        cancel = (CC.enabled if profile.pedal else not CC.enabled) and CS.out.cruiseState.enabled
      else:
        cancel = self.cancel_counter > CAMERA_CANCEL_DELAY_FRAMES
      slot = CS.hybrid_buttons.slot
      if slot.credit_ns <= self.volt_cc_consumed_source_ns:
        slot.credit_ns = 0
      action = None
      interval_ns = 40_000_001
      authorized = hybrid_lateral_ready(CS, now_nanos)
      if cancel:
        action = "cancel"
      elif (profile.longitudinal and not profile.pedal and CC.longActive and ready and
            CS.out.vEgo >= self.CP.minEnableSpeed and self.frame % 4 == 0):
        button, rate, self.apply_speed = ordinary_button_request(
          CS.out.vEgo, CS.out.cruiseState.speed, actuators.accel, self.CP.minEnableSpeed, self.volt_cc_metric)
        action = {CruiseButtons.RES_ACCEL: "resume", CruiseButtons.DECEL_SET: "set", CruiseButtons.CANCEL: "cancel"}.get(button)
        interval_ns = int(rate * 1e9) + 1
      elif (profile.longitudinal and not profile.pedal and CC.enabled and CS.out.gasPressed and
            self.frame % 52 == 0 and CC.hudControl.setSpeed > CS.out.vEgo > CS.out.cruiseState.speed and
            hybrid_lateral_ready(CS, now_nanos) and not CS.out.brakePressed and not CS.out.regenBraking):
        action, interval_ns = "gas_set", 520_000_000
      if action in ("cancel", "gas_set"):
        authorized = authorized and CS.out.cruiseState.enabled
      if action is not None:
        raw = slot.expected(action, now_nanos, interval_ns=interval_ns, authorized=authorized)
        if raw is not None and slot.accept(action, raw, now_nanos, interval_ns=interval_ns, authorized=authorized):
          can_sends.append((0x1E1, raw, CanBus.POWERTRAIN))
      if profile.removed and profile.longitudinal and self.frame % 100 == 0:
        can_sends += gmcan.create_adas_keepalive(CanBus.POWERTRAIN)
    elif self.volt_cc_pedal_profile is not None:
      profile = self.volt_cc_pedal_profile
      credit = CS.conventional_cancel_credit
      sources = CS.volt_cc_pedal_sources
      physical = (CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and
                  CS.out.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low) and
                  not CS.out.brakePressed and not CS.out.gasPressed and not CS.out.regenBraking)
      source_current = (len(sources) == (9 if profile.longitudinal else 8) and
                        all(stamp > 0 and 0 <= now_nanos - stamp <= limit for stamp, limit in sources))
      sensor_current = (not profile.longitudinal or CS.pedal_sensor_healthy and
                        0 <= now_nanos - CS.pedal_sensor_ts_nanos <= PEDAL_SENSOR_TIMEOUT_NS)
      runtime_allowed = self.camera_pedal_input is not None and self.camera_pedal_input.update(now_nanos)
      if profile.longitudinal and self.frame % 4 == 0:
        ready = runtime_allowed and physical and source_current and sensor_current
        pedal = 0.
        if ready:
          gas, _ = volt_demands(actuators.accel, CS.out.vEgo, CC.orientationNED if long_pitch else None,
                                .75, self.CP.mass, self.CP.wheelbase, -4., 2., 400)
          pedal = self.volt_cc_pedal_command.update(
            actuators.accel, CC.enabled and CC.longActive, CS.out,
            stopping=actuators.longControlState == LongCtrlState.stopping, resume=CC.cruiseControl.resume,
            gas_above_inactive=gas > -650 and actuators.longControlState != LongCtrlState.stopping,
            maneuver=self.longitudinal_maneuver_mode)
        else:
          if not runtime_allowed or not source_current or not sensor_current:
            self.volt_cc_pedal_command.prime_recovery()
          else:
            self.volt_cc_pedal_command.pause_recovery()
        self.apply_gas, self.apply_brake = pedal, 0
        can_sends.append(gmcan.create_pedal_command(self.packer_pt, pedal, (self.frame // 4) % 4))
      self.cancel_counter = self.cancel_counter + 1 if CC.cruiseControl.cancel else 0
      cancel = CS.out.cruiseState.enabled if profile.longitudinal else self.cancel_counter > CAMERA_CANCEL_DELAY_FRAMES
      if (cancel and CS.out.cruiseState.available and CS.out.cruiseState.enabled and
          CS.out.canValid and not CS.out.canTimeout and source_current and sensor_current and
          0 < credit.credit_ns <= now_nanos <= credit.credit_ns + 100_000_000 and
          credit.available(now_nanos, self.volt_cc_pedal_cancel_used) and
          (self.frame - self.last_button_frame) * DT_CTRL > .04):
        self.volt_cc_pedal_cancel_used = credit.credit_ns
        self.last_button_frame = self.frame
        can_sends.append(gmcan.create_buttons(self.packer_pt,
          CanBus.POWERTRAIN if profile.longitudinal else CanBus.CAMERA,
          (CS.buttons_counter + 1) % 4 if profile.longitudinal else CS.buttons_counter, CruiseButtons.CANCEL))
      if profile.longitudinal and profile.removed and self.frame % 100 == 0:
        can_sends += gmcan.create_adas_keepalive(CanBus.POWERTRAIN)
    elif self.silverado_cc_pedal_profile:
      sources_ready = conventional_pedal_sources_current(CS, now_nanos) and CS.pedal_sensor_healthy
      ready = (sources_ready and CS.out.cruiseState.available and CS.out.gearShifter in
               (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low) and
               not CS.out.brakePressed and not CS.out.gasPressed)
      if self.CP.openpilotLongitudinalControl and self.frame % 4 == 0:
        if ready:
          pedal, self.apply_gas, self.apply_brake = self.silverado_pedal_command.update(
            actuators.accel, CC.longActive, CS.out, stopping=actuators.longControlState == LongCtrlState.stopping,
            resume=CC.cruiseControl.resume, orientation=CC.orientationNED if long_pitch else None)
        else:
          if not sources_ready:
            self.silverado_pedal_command.prime_recovery()
          else:
            self.silverado_pedal_command.pause_recovery()
          pedal, self.apply_gas, self.apply_brake = 0., -650., 0
        self.pedal_steady = self.silverado_pedal_command.steady
        self.pedal_active_last = self.silverado_pedal_command.active_last
        can_sends.append(gmcan.create_pedal_command(self.packer_pt, pedal, (self.frame // 4) % 4))

    elif self.conventional_pedal_profile:
      sources_ready = conventional_pedal_sources_current(CS, now_nanos) and CS.pedal_sensor_healthy
      ready = (sources_ready and CS.out.cruiseState.available and CS.out.gearShifter in
               (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low) and
               not CS.out.brakePressed and not CS.out.gasPressed)
      if self.CP.openpilotLongitudinalControl and self.frame % 4 == 0:
        if ready:
          pedal, self.apply_gas, self.apply_brake = self.conventional_pedal_command.update(
            actuators.accel, CC.longActive and CC.enabled, CS.out, stopping=actuators.longControlState == LongCtrlState.stopping,
            resume=CC.cruiseControl.resume, orientation=CC.orientationNED if long_pitch else None)
        else:
          if not sources_ready:
            self.conventional_pedal_command.prime_recovery()
          else:
            self.conventional_pedal_command.pause_recovery()
          pedal, self.apply_gas, self.apply_brake = 0., -650., 0
        self.pedal_steady = self.conventional_pedal_command.steady
        self.pedal_active_last = self.conventional_pedal_command.active_last
        can_sends.append(gmcan.create_pedal_command(self.packer_pt, pedal, (self.frame // 4) % 4))
    elif self.CP.flags & GMFlags.PEDAL_LONG.value and self.CP.openpilotLongitudinalControl and self.camera_pedal_profile is None:
      active, stock_ownership_clear, in_regen_gear = self.bolt_pedal_admission(CC, CS, now_nanos)
      self.bolt_acc_pedal_stop_hold = (
        self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and is_bolt_pedal_profile(self.CP) and
        active and actuators.longControlState == LongCtrlState.stopping and not CC.cruiseControl.resume and
        (self.bolt_acc_pedal_stop_hold or CS.out.standstill)
      )
      if not active or not in_regen_gear or self.maneuver_paddle_mode == "off":
        self.bolt_regen_hold = False
      elif self.maneuver_paddle_mode == "force":
        self.bolt_regen_hold = actuators.accel < -.02
      else:
        press = np.interp(CS.out.vEgo, [0., 4., 12., 25.], [-0.95, -0.82, -0.70, -0.62])
        release = np.interp(CS.out.vEgo, [0., 4., 12., 25.], [-0.14, -0.22, -0.30, -0.36])
        if actuators.accel <= press:
          self.bolt_regen_hold = True
        elif actuators.accel >= release:
          self.bolt_regen_hold = False
      if self.frame % 4 == 0:
        stock_acc_variant = self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL
        friction_variant = stock_acc_variant and bool(self.CP.flags & GMFlags.PEDAL_LONG.value)
        main_ns = getattr(CS, "bolt_pedal_main_ts_nanos", 0)
        friction_main_on = (friction_variant and CS.out.cruiseState.available and stock_ownership_clear and
                            main_ns > 0 and 0 <= now_nanos - main_ns <= 300_000_000)
        if friction_variant:
          if active and friction_main_on and not self.bolt_acc_pedal_friction_unavailable:
            self.apply_brake, self.bolt_acc_pedal_friction_low_speed_active = bolt_acc_pedal_friction_brake(
              actuators.accel, CS.out.vEgo, actuators.longControlState == LongCtrlState.stopping,
              self.bolt_acc_pedal_friction_low_speed_active, self.CP.mass, self.CP.wheelbase, self.params.MAX_BRAKE, acc_tune=self.gm_acc_tune)
          else:
            self.apply_brake = 0
            self.bolt_acc_pedal_friction_low_speed_active = False
        fixed_stop = (active and is_bolt_pedal_profile(self.CP) and self.CP.carFingerprint in NO_ACC_BOLT_CAR and
                      CS.out.vEgo < .25 and actuators.longControlState == LongCtrlState.stopping and
                      not CC.cruiseControl.resume)
        paddle_pressed = False if fixed_stop else self.update_bolt_paddle(
          actuators.accel, CS.out.aEgo, CS.out.vEgo, active and in_regen_gear)
        paddle_switched = False if fixed_stop else self.bolt_paddle_switched
        if fixed_stop:
          pass
        elif active:
          target = bolt_pedal_fraction(actuators.accel, CS.out.vEgo, paddle_pressed)
          if self.pedal_active_last and not (paddle_switched and CS.out.vEgo > 1.0):
            self.pedal_steady = bolt_pedal_slew(target, self.pedal_steady, actuators.accel, CS.out.vEgo)
          else:
            self.pedal_steady = target
          self.pedal_active_last = True
        else:
          self.pedal_steady = 0.0
          self.pedal_active_last = False
        pedal = 0. if fixed_stop else self.pedal_steady
        if (is_bolt_pedal_profile(self.CP) and self.CP.carFingerprint in NO_ACC_BOLT_CAR and
            CS.out.cruiseState.standstill):
          stamp = getattr(CS, "bolt_pedal_standstill_ts_nanos", 0)
          main_stamp = getattr(CS, "bolt_pedal_main_ts_nanos", 0)
          gear_stamp = getattr(CS, "bolt_pedal_gear_ts_nanos", 0)
          cutoff = .35 if self.CP.carFingerprint == CAR.CHEVROLET_BOLT_CC_2022_2023 else .3
          if (active and CS.out.cruiseState.available and CS.out.cruiseState.standstill and
              0 < stamp <= now_nanos and now_nanos - stamp <= STOCK_ACC_STATUS_TIMEOUT_NS and
              0 < main_stamp <= now_nanos and now_nanos - main_stamp <= 300_000_000 and
              0 < gear_stamp <= now_nanos and now_nanos - gear_stamp <= 100_000_000 and
              (CS.out.standstill or math.isfinite(CS.out.vEgo) and CS.out.vEgo < cutoff) and
              actuators.longControlState != LongCtrlState.stopping and
              bolt_pedal_launch_gas(actuators.accel, CS.out.vEgo, CC.orientationNED if long_pitch else None,
                                    self.CP, acc_tune=self.gm_acc_tune)):
            pedal = 18. / 255.
        if self.bolt_acc_pedal_stop_hold:
          pedal = 0.0
          self.pedal_steady = 0.0
        can_sends.append(gmcan.create_pedal_command(self.packer_pt, pedal, (self.frame // 4) % 16))
        if CC.enabled:
          self.paddle_handoff_frames = 2
        feed_release = bool(self.paddle_handoff_frames)
        if not CC.enabled and self.paddle_handoff_frames:
          self.paddle_handoff_frames -= 1
        if (CC.enabled or feed_release) and in_regen_gear and not CS.out.regenBraking:
          spoof_pressed = paddle_pressed and CS.out.vEgo > 2.68
          gen2 = self.CP.carFingerprint in (CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL)
          can_sends.append(gmcan.create_bolt_regen_gear(self.packer_pt, spoof_pressed, gen2,
                                                       drive=CS.out.gearShifter == structs.CarState.GearShifter.drive))
          can_sends.append(gmcan.create_bolt_regen_paddle(self.packer_pt, spoof_pressed))
        self.apply_gas = pedal
        if friction_variant:
          if not friction_main_on:
            self.bolt_acc_pedal_friction_release_frames = 0
          elif self.apply_brake > 0:
            self.bolt_acc_pedal_friction_release_frames = BOLT_ACC_PEDAL_FRICTION_RELEASE_FRAMES
          elif self.bolt_acc_pedal_friction_release_frames > 0:
            self.bolt_acc_pedal_friction_release_frames -= 1
          if friction_main_on or self.bolt_acc_pedal_friction_release_frames > 0:
            if self.bolt_acc_brake_clock is not None:
              self.bolt_acc_brake_clock.prepare(
                now_nanos, self.apply_brake, active and friction_main_on and not self.bolt_acc_pedal_friction_unavailable,
                active and CS.out.vEgo < self.params.NEAR_STOP_BRAKE_PHASE,
                active and CS.out.standstill and actuators.longControlState == LongCtrlState.stopping)
            else:
              can_sends.append(gmcan.create_friction_brake_command(
                self.packer_ch, CanBus.POWERTRAIN, self.apply_brake, (self.frame // 4) % 4,
                active and friction_main_on, active and CS.out.vEgo < self.params.NEAR_STOP_BRAKE_PHASE,
                active and CS.out.standstill and actuators.longControlState == LongCtrlState.stopping, self.CP))
        else:
          self.apply_brake = 0
        dashboard_ready = (
          self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and
          is_bolt_pedal_profile(self.CP) and len(self.CP.safetyConfigs) == 1 and
          self.CP.safetyConfigs[0].safetyParam == 0x1CD and CS.out.canValid and not CS.out.canTimeout and
          bolt_acc_pedal_dashboard_sensor_ready(CS, now_nanos) and CS.out.cruiseState.available and
          bolt_acc_pedal_dashboard_drive_ready(CS, now_nanos, allow_paddle=True)
        )
        if dashboard_ready:
          fcw = 3 if hud_alert == VisualAlert.fcw else int(getattr(CS, "stock_fcw_alert", 0)) & 3
          if fcw == 0 and (CS.out.stockAeb or CS.out.stockFcw):
            fcw = 3
          can_sends.append(gmcan.create_acc_dashboard_command(
            self.packer_pt, CanBus.POWERTRAIN, CC.enabled, hud_v_cruise * CV.MS_TO_KPH,
            hud_control, False, cruise_state=2, fcw_alert=fcw))
      if self.bolt_acc_brake_clock is not None:
        main_ns = getattr(CS, "bolt_pedal_main_ts_nanos", 0)
        main_ready = (CS.out.cruiseState.available and stock_ownership_clear and
                      0 < main_ns <= now_nanos and now_nanos - main_ns <= 300_000_000)
        scheduled = self.bolt_acc_brake_clock.sample(
          now_nanos, getattr(CS, "bolt_acc_brake_clock_ns", 0), getattr(CS, "bolt_acc_brake_clock_counter", 0),
          active=bool(active and not self.bolt_acc_pedal_friction_unavailable), main_ready=main_ready,
          source_valid=bool(CS.out.canValid and not CS.out.canTimeout))
        if scheduled is not None:
          brake, counter, enabled, near_stop, full_stop = scheduled
          can_sends.append(gmcan.create_friction_brake_command(
            self.packer_ch, CanBus.POWERTRAIN, brake, counter, enabled, near_stop, full_stop, self.CP))
      if self.bolt_pedal_removed and self.frame % 100 == 0:
        can_sends += gmcan.create_adas_keepalive(CanBus.POWERTRAIN)
      # Stock ACC on the equipped variant is canceled while pedal control owns longitudinal.
      if (self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and CS.out.cruiseState.enabled and
          (not self.bolt_pedal_removed or stock_steer_ready and CS.bolt_pedal_removed_stock_ts_nanos > 0 and
           0 <= now_nanos - CS.bolt_pedal_removed_stock_ts_nanos <= STOCK_ACC_STATUS_TIMEOUT_NS) and
          (self.frame - self.last_button_frame) * DT_CTRL > 0.04):
        self.last_button_frame = self.frame
        can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.CAMERA, (CS.buttons_counter + 1) % 4, CruiseButtons.CANCEL))

    elif (self.CP.openpilotLongitudinalControl and not self.volt_cc_long and not self.ordinary_cc_profile and
          not self.bolt_cc_profile and not self.silverado_cc_pedal_profile):
      # Gas/regen, brakes, and UI commands - all at 25Hz
      if self.frame % 4 == 0:
        stopping = actuators.longControlState == LongCtrlState.stopping
        near_stop = CC.longActive and abs(CS.out.vEgo) < self.params.NEAR_STOP_BRAKE_PHASE
        if not CC.longActive:
          # ASCM sends max regen when not enabled
          self.apply_gas = self.params.INACTIVE_REGEN
          self.apply_brake = 0
          if self.truck_tuning is not None:
            self.truck_tuning.follow_accel = 0.0
        else:
          stop_brake = fixed_stopping_brake(CC.longActive, near_stop, stopping, CC.cruiseControl.resume,
                                           self.CP.stopAccel, self.params.MAX_BRAKE)
          if stop_brake is not None:
            self.apply_gas = self.params.INACTIVE_REGEN
            self.apply_brake = stop_brake
            if self.truck_tuning is not None:
              self.truck_tuning.follow_accel = 0.0
          else:
            if self.CP.carFingerprint == CAR.CHEVROLET_SUBURBAN:
              self.apply_gas, self.apply_brake = suburban_gateway_demands(
                actuators.accel, CS.out.vEgo, CC.orientationNED if long_pitch else None, self.CP, acc_tune=self.gm_acc_tune)
            elif self.ordinary_camera_long:
              self.apply_gas, self.apply_brake = ascm_demands(
                actuators.accel, CS.out.vEgo, CC.orientationNED if long_pitch else None, self.CP,
                min_gas=-540, max_gas=2698, inactive_gas=-500, brake_threshold=0.0, acc_tune=self.gm_acc_tune,
                truck_tuning=self.truck_tuning, lead_visible=CC.hudControl.leadVisible,
                set_speed_error=max(CC.hudControl.setSpeed - CS.out.vEgo, 0.0), stopping=stopping)
            elif self.ordinary_sdgm_long:
              self.apply_gas, self.apply_brake = ascm_demands(
                actuators.accel, CS.out.vEgo, CC.orientationNED, self.CP,
                min_gas=-540, max_gas=2698, inactive_gas=-500, brake_threshold=0.0,
                stop_speed=0.35 if self.CP.carFingerprint == CAR.CHEVROLET_BLAZER else 0.25, acc_tune=self.gm_acc_tune)
            elif self.ordinary_ascm_long:
              self.apply_gas, self.apply_brake = ascm_demands(
                actuators.accel, CS.out.vEgo, CC.orientationNED if long_pitch else None, self.CP, acc_tune=self.gm_acc_tune)
            elif self.bolt_euv_long:
              self.apply_gas, self.apply_brake = bolt_euv_demands(
                actuators.accel, CS.out.vEgo, CC.orientationNED if long_pitch else None,
                self.CP.mass, self.CP.wheelbase, self.params.MAX_BRAKE, acc_tune=self.gm_acc_tune)
            elif self.volt_gateway_long or self.volt_ascm_long or self.volt_camera_long or self.volt_sdgm_long:
              self.apply_gas, self.apply_brake = volt_demands(
                actuators.accel, CS.out.vEgo, CC.orientationNED if long_pitch else None,
                VOLT_CAMERA_GRADE_STOP_SPEED if self.volt_ascm_long or self.volt_camera_long or self.volt_sdgm_long else VOLT_GATEWAY_GRADE_STOP_SPEED,
                self.CP.mass, self.CP.wheelbase, self.params.ACCEL_MIN, self.params.ACCEL_MAX, self.params.MAX_BRAKE,
                min_gas=-540 if self.volt_camera_long or self.volt_sdgm_long else -650,
                max_gas=2698 if self.volt_camera_long or self.volt_sdgm_long else 2041,
                inactive_gas=-500 if self.volt_camera_long or self.volt_sdgm_long else -650)
            else:
              gas_demand = brake_demand = (min(actuators.accel, acc_tune_limits(CS.out.vEgo, self.params.ACCEL_MAX, 6150, 6150)[0])
                                           if self.gm_acc_tune else actuators.accel)
              if self.CP.carFingerprint in VOLT_GRADE_CAR:
                gas_demand, brake_demand = volt_grade_demands(
                  actuators.accel, CS.out.vEgo, CC.orientationNED if long_pitch else None,
                  VOLT_GATEWAY_GRADE_STOP_SPEED if self.CP.networkLocation == NetworkLocation.gateway else VOLT_CAMERA_GRADE_STOP_SPEED,
                  self.params.ACCEL_MIN, self.params.ACCEL_MAX)
              self.apply_gas = float(np.interp(gas_demand, self.params.GAS_LOOKUP_BP, self.params.GAS_LOOKUP_V))
              self.apply_brake = int(round(np.interp(brake_demand, self.params.BRAKE_LOOKUP_BP, self.params.BRAKE_LOOKUP_V)))
            if stopping:
              self.apply_gas = self.params.INACTIVE_REGEN

        idx = (self.frame // 4) % 4

        at_full_stop = CC.longActive and CS.out.standstill
        friction_brake_bus = CanBus.POWERTRAIN if is_volt_gateway_alternate_brake(self.CP) else CanBus.CHASSIS
        # GM Camera exceptions
        # TODO: can we always check the longControlState?
        if self.CP.networkLocation == NetworkLocation.fwdCamera and not (self.conventional_pedal_profile and self.CP.flags & GMFlags.NO_CAMERA):
          at_full_stop = at_full_stop and stopping
          friction_brake_bus = CanBus.CAMERA if self.volt_sdgm_long or self.ordinary_sdgm_long else CanBus.POWERTRAIN
        if self.camera_pedal_profile is not None:
          resume = actuators.longControlState != LongCtrlState.starting or CC.cruiseControl.resume
          at_full_stop = at_full_stop and not resume

        if (self.volt_camera_long and not self.volt_camera_removed or
            self.ordinary_camera_long and not self.ordinary_camera_removed):
          can_sends.append(gmcan.create_acc_2cd_command(CanBus.POWERTRAIN, idx))

        if self.volt_one_pedal_state is not None:
          one_pedal_brake = self.volt_one_pedal_state.update(
            eligible=one_pedal_active, speed=CS.out.vEgo, acceleration=CS.out.aEgo,
            steering_angle=CS.out.steeringAngleDeg, orientation=CC.orientationNED)
          if one_pedal_active:
            one_pedal_stop = CS.volt_one_pedal_stopped and (CS.out.standstill or CS.out.vEgo < .02)
            if hold_brake is None and one_pedal_stop and one_pedal_brake > 0:
              hold_brake = max(one_pedal_brake, self.gm_auto_hold_state.brake or estimate_hold_brake(
                0., one_pedal_brake, self.gm_auto_hold_state.config.minimum_brake))
            elif hold_brake is not None:
              hold_brake = max(hold_brake, one_pedal_brake)
            self.apply_gas = self.params.INACTIVE_REGEN
            self.apply_brake = max(self.apply_brake, one_pedal_brake)

        camera_pedal = 0.
        if self.camera_pedal_profile is not None:
          sources = CS.camera_pedal_sources
          runtime_allowed = not startup_keepalive and self.camera_pedal_input is not None and self.camera_pedal_input.update(now_nanos)
          ready = (runtime_allowed and len(sources) == (8 if self.camera_pedal_profile.volt else 7) and
                   all(stamp > 0 and 0 <= now_nanos - stamp <= limit for stamp, limit in sources) and
                   CS.pedal_sensor_healthy and 0 <= now_nanos - CS.pedal_sensor_ts_nanos <= PEDAL_SENSOR_TIMEOUT_NS and
                   CS.out.canValid and not CS.out.canTimeout and CS.camera_pedal_forward and
                   CS.out.cruiseState.available and not CS.out.brakePressed and not CS.out.gasPressed and not CS.out.accFaulted and
                   (not self.camera_pedal_profile.volt or not CS.out.regenBraking))
          launch = (ready and CC.enabled and CC.longActive and CC.cruiseControl.resume and
                    self.apply_gas > self.params.INACTIVE_REGEN and
                    CS.out.cruiseState.standstill and
                    (not self.camera_pedal_profile.volt or CS.out.standstill or
                     math.isfinite(CS.out.vEgo) and
                     CS.out.vEgo < (2. if self.longitudinal_maneuver_mode else
                                   .75 if self.camera_pedal_profile.topology == "gateway" else .3)) and
                    all(math.isfinite(speed) and 0 <= speed <=
                      (231 if self.camera_pedal_profile.volt and self.longitudinal_maneuver_mode else 34) * .0311
                      for speed in CS.camera_pedal_rear))
          if launch:
            camera_pedal = (float(np.interp(actuators.accel, [0., 1., 2.], [18. / 255., .11, .16]))
                            if self.camera_pedal_profile.volt and self.longitudinal_maneuver_mode else 18. / 255.)
          if not ready or launch:
            self.apply_gas, self.apply_brake = self.params.INACTIVE_REGEN, 0
          if not launch:
            can_sends.append(gmcan.create_pedal_command(self.packer_pt, 0., idx))
          self.camera_pedal_launch = launch

        one_pedal_braking = one_pedal_active and self.volt_one_pedal_state is not None and self.volt_one_pedal_state.brake > 0
        if one_pedal_braking:
          at_full_stop = at_full_stop or CS.out.cruiseState.standstill
          near_stop = near_stop or CS.out.vEgo < self.params.NEAR_STOP_BRAKE_PHASE

        # Resume releases only the gas/regen ACC-state bit; friction remains separately owned.
        acc_engaged = CC.enabled and not volt_sng_release(
          self.CP, self.volt_sng, CC, CS, now_nanos,
          plan_current=self.volt_sng_plan_input.update(now_nanos) if self.volt_sng and self.volt_sng_plan_input is not None else False)
        if self.camera_pedal_profile is not None and not ready:
          acc_engaged = False
        if is_volt_sdgm_accepted_envelope(self.CP):
          self.apply_gas = min(self.apply_gas, self.params.MAX_GAS)
        if hold_brake is None:
          can_sends.append(gmcan.create_gas_regen_command(self.packer_pt, CanBus.POWERTRAIN, self.apply_gas, idx, acc_engaged, at_full_stop))
        else:
          self.apply_brake = hold_brake
          at_full_stop = CS.out.cruiseState.standstill
          near_stop = CS.out.vEgo < self.params.NEAR_STOP_BRAKE_PHASE
          if self.volt_ascm_long and self.camera_pedal_profile is None:
            # Retain the inactive gas/regen cadence while friction owns the hold.
            can_sends.append(gmcan.create_gas_regen_command(
              self.packer_pt, CanBus.POWERTRAIN, self.params.INACTIVE_REGEN, idx, False, False))
        can_sends.append(gmcan.create_friction_brake_command(self.packer_ch, friction_brake_bus, self.apply_brake,
                                                             idx, CC.enabled, near_stop, at_full_stop, self.CP,
                                                             auto_hold=hold_brake is not None or one_pedal_braking))
        if self.camera_pedal_profile is not None and self.camera_pedal_launch:
          can_sends.append(gmcan.create_pedal_command(self.packer_pt, camera_pedal, idx))
        CS.gm_auto_hold_engaged = hold_brake is not None

        if self.bolt_euv_long:
          can_sends.append(gmcan.create_acc_2cd_command(CanBus.POWERTRAIN, idx))

        # Send dashboard UI commands (ACC status)
        send_fcw = hud_alert == VisualAlert.fcw
        camera_fcw = None
        if (self.ordinary_ascm_long or self.ordinary_sdgm_long or self.volt_ascm_long or self.volt_camera_long or
            self.volt_sdgm_long or self.ordinary_camera_long):
          camera_fcw = 3 if send_fcw else CS.stock_fcw_alert
          if camera_fcw == 0 and (CS.out.stockAeb or CS.out.stockFcw):
            camera_fcw = 3
        dashboard_state = 2 if (self.ordinary_camera_long or self.ordinary_ascm_long or self.ordinary_sdgm_long or self.volt_ascm_long or
                                self.volt_camera_long or self.volt_sdgm_long or self.CP.carFingerprint == CAR.CHEVROLET_SUBURBAN) else None
        if self.camera_pedal_profile is None or runtime_allowed or startup_keepalive:
          dashboard_enabled = CC.enabled and not (self.camera_pedal_profile is not None and
                                                  self.camera_pedal_profile.topology in ("ascm", "sdgm") and not ready)
          can_sends.append(gmcan.create_acc_dashboard_command(self.packer_pt, CanBus.POWERTRAIN, dashboard_enabled,
                                                             hud_v_cruise * CV.MS_TO_KPH, hud_control, send_fcw,
                                                             cruise_state=dashboard_state,
                                                             fcw_alert=camera_fcw,
                                                             acc_always_one=0 if ((self.ordinary_camera_long and
                                                                 (self.camera_pedal_profile is None or self.CP.carFingerprint not in
                                                                  (CAR.GMC_YUKON, CAR.CHEVROLET_SUBURBAN_CAMERA))) or
                                                                 self.ordinary_sdgm_long and self.CP.carFingerprint == CAR.CHEVROLET_BLAZER) else 1))

      # Radar needs to know current speed and yaw rate (50hz),
      # and that ADAS is alive (10hz)
      if (not self.CP.radarUnavailable and not (self.camera_pedal_profile is not None and
                                              self.camera_pedal_profile.topology == "gateway") and
          not self.volt_ascm_long and not self.volt_camera_long and not self.volt_sdgm_long
          and not self.ordinary_ascm_long and not self.ordinary_sdgm_long):
        tt = self.frame * DT_CTRL
        time_and_headlights_step = 10
        if self.frame % time_and_headlights_step == 0:
          idx = (self.frame // time_and_headlights_step) % 4
          can_sends.append(gmcan.create_adas_time_status(CanBus.OBSTACLE, int((tt - self.start_time) * 60), idx))
          can_sends.append(gmcan.create_adas_headlights_status(self.packer_obj, CanBus.OBSTACLE))

        speed_and_accelerometer_step = 2
        if self.frame % speed_and_accelerometer_step == 0:
          idx = (self.frame // speed_and_accelerometer_step) % 4
          can_sends.append(gmcan.create_adas_steering_status(CanBus.OBSTACLE, idx))
          can_sends.append(gmcan.create_adas_accelerometer_speed_status(CanBus.OBSTACLE, abs(CS.out.vEgo), idx))

      if (self.CP.networkLocation == NetworkLocation.gateway or self.volt_camera_removed_long or
          self.camera_pedal_profile is not None and
          self.camera_pedal_profile.topology == "gateway" and self.camera_pedal_profile.removed or
          self.ordinary_camera_long and self.ordinary_camera_removed) and \
          self.frame % self.params.ADAS_KEEPALIVE_STEP == 0:
        can_sends += gmcan.create_adas_keepalive(CanBus.POWERTRAIN)

    elif self.volt_camera_removed:
      self.cancel_counter = self.cancel_counter + 1 if CC.cruiseControl.cancel else 0
      credit = CS.volt_removed_credit_ns
      if (self.cancel_counter > CAMERA_CANCEL_DELAY_FRAMES and (self.frame - self.last_button_frame) * DT_CTRL > 0.04 and
          stock_steer_ready and CS.out.cruiseState.available and CS.out.cruiseState.enabled and
          credit > self.volt_removed_cancel_credit_used and 0 <= now_nanos - credit <= 100_000_000):
        self.last_button_frame = self.frame
        self.volt_removed_cancel_credit_used = credit
        can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.POWERTRAIN, CS.buttons_counter, CruiseButtons.CANCEL))
    elif (self.camera_pedal_profile is not None and self.camera_pedal_profile.topology in ("gateway", "ascm", "sdgm") and
          not self.camera_pedal_profile.longitudinal):
      self.cancel_counter = self.cancel_counter + 1 if CC.cruiseControl.cancel else 0
      credit = CS.volt_removed_credit_ns
      sources = CS.camera_pedal_sources
      physical_current = (len(sources) >= 5 and all(stamp > 0 and 0 <= now_nanos - stamp <= 300_000_000
                                                   for stamp, _ in sources[3:5]))
      if (self.cancel_counter > CAMERA_CANCEL_DELAY_FRAMES and (self.frame - self.last_button_frame) * DT_CTRL > .04 and
          physical_current and CS.out.canValid and not CS.out.canTimeout and
          CS.out.cruiseState.available and CS.out.cruiseState.enabled and
          credit > self.volt_removed_cancel_credit_used and 0 <= now_nanos - credit <= 100_000_000):
        self.last_button_frame = self.frame
        self.volt_removed_cancel_credit_used = credit
        can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.CAMERA, CS.buttons_counter, CruiseButtons.CANCEL))
    elif (not self.bolt_pedal_removed and not self.bolt_present_no_acc_stock and self.volt_cc_pedal_profile is None and not self.volt_cc_profile and
          self.hybrid_profile is None and not self.ordinary_cc_profile and not self.bolt_cc_profile and
          not self.volt_gateway_profile and not self.silverado_cc_pedal_profile):
      # While car is braking, cancel button causes ECM to enter a soft disable state with a fault status.
      # A delayed cancellation allows camera to cancel and avoids a fault when user depresses brake quickly
      cancel_requested = CC.cruiseControl.cancel
      if is_silverado_cc_stock_profile(self.CP):
        cancel_requested = cancel_requested and stock_cruise_fresh and CS.out.cruiseState.available and CS.out.cruiseState.enabled
      self.cancel_counter = self.cancel_counter + 1 if cancel_requested else 0

      # Stock longitudinal, integrated at camera
      if (self.frame - self.last_button_frame) * DT_CTRL > 0.04:
        button_fresh = (not cc_gateway or
                        (CS.cc_gateway_buttons_ts_nanos > 0 and
                         0 <= now_nanos - CS.cc_gateway_buttons_ts_nanos <= CC_GATEWAY_BUTTON_TIMEOUT_NS))
        if self.cancel_counter > CAMERA_CANCEL_DELAY_FRAMES and (not cc_gateway or
            (button_fresh and stock_cruise_fresh and CS.out.cruiseState.available and CS.out.cruiseState.enabled)):
          self.last_button_frame = self.frame
          cancel_bus = (CanBus.POWERTRAIN if cc_gateway or gm_control_word(self.CP) & GMSafetyFlags.SDGM_CANCEL_PT.value
                        else CanBus.CAMERA)
          can_sends.append(gmcan.create_buttons(self.packer_pt, cancel_bus, CS.buttons_counter, CruiseButtons.CANCEL))

    if (self.bolt_pedal_removed or self.bolt_present_no_acc_stock) and not self.CP.openpilotLongitudinalControl:
      self.cancel_counter = self.cancel_counter + 1 if CC.cruiseControl.cancel else 0
      credit = CS.conventional_cancel_credit
      stock_cancel_ready = stock_steer_ready
      if self.bolt_present_no_acc_stock:
        stock_cancel_ready = (CS.out.canValid and not CS.out.canTimeout and CS.pedal_sensor_healthy and
                              0 < CS.pedal_sensor_ts_nanos <= now_nanos <= CS.pedal_sensor_ts_nanos + PEDAL_SENSOR_TIMEOUT_NS and
                              bolt_acc_pedal_dashboard_drive_ready(CS, now_nanos) and CS.out.cruiseState.available and
                              CS.bolt_pedal_stock_active and 0 < CS.bolt_pedal_stock_ts_nanos <= now_nanos and
                              now_nanos - CS.bolt_pedal_stock_ts_nanos <= STOCK_ACC_STATUS_TIMEOUT_NS)
      if (self.cancel_counter > CAMERA_CANCEL_DELAY_FRAMES and stock_cancel_ready and
          (self.frame - self.last_button_frame) * DT_CTRL > .04 and
          credit.available(now_nanos, self.bolt_removed_cancel_credit_used) and
          0 <= now_nanos - credit.credit_ns <= 100_000_000):
        self.last_button_frame = self.frame
        self.bolt_removed_cancel_credit_used = credit.credit_ns
        can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.POWERTRAIN, (credit.counter + 1) % 4, CruiseButtons.CANCEL))

    if self.silverado_cc_pedal_profile:
      self.cancel_counter = self.cancel_counter + 1 if CC.cruiseControl.cancel else 0
      if (not self.CP.openpilotLongitudinalControl and self.cancel_counter > CAMERA_CANCEL_DELAY_FRAMES and
          (self.frame - self.last_button_frame) * DT_CTRL > .04 and
          conventional_pedal_sources_current(CS, now_nanos) and CS.out.cruiseState.available and
          CS.conventional_cancel_credit.available(now_nanos, self.conventional_cancel_credit_used) and
          0 <= now_nanos - CS.conventional_cancel_credit.credit_ns <= 100_000_000):
        self.last_button_frame = self.frame
        self.conventional_cancel_credit_used = CS.conventional_cancel_credit.credit_ns
        can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.CAMERA, CS.conventional_cancel_credit.counter, CruiseButtons.CANCEL))

    elif self.conventional_pedal_profile and not self.CP.openpilotLongitudinalControl:
      self.cancel_counter = self.cancel_counter + 1 if CC.cruiseControl.cancel else 0
      if (self.cancel_counter > CAMERA_CANCEL_DELAY_FRAMES and
          (self.frame - self.last_button_frame) * DT_CTRL > .04 and
          conventional_pedal_cancel_sources_current(CS, now_nanos) and
          CS.out.cruiseState.available and CS.out.cruiseState.enabled and
          CS.conventional_cancel_credit.available(now_nanos, self.conventional_cancel_credit_used)):
        self.last_button_frame = self.frame
        self.conventional_cancel_credit_used = CS.conventional_cancel_credit.credit_ns
        can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.CAMERA, CS.conventional_cancel_credit.counter, CruiseButtons.CANCEL))

    if (self.conventional_pedal_profile and self.CP.flags & GMFlags.NO_CAMERA and
        self.CP.openpilotLongitudinalControl and self.frame % 100 == 0):
      can_sends += gmcan.create_adas_keepalive(CanBus.POWERTRAIN)

    if (self.conventional_pedal_profile and self.CP.openpilotLongitudinalControl and
        conventional_pedal_sources_current(CS, now_nanos) and CS.out.cruiseState.available and
        CS.out.cruiseState.enabled and (self.frame - self.last_button_frame) * DT_CTRL > .04 and
        CS.conventional_cancel_credit.available(now_nanos, self.conventional_cancel_credit_used) and
        0 <= now_nanos - CS.conventional_cancel_credit.credit_ns <=
        (100_000_000 if self.silverado_cc_pedal_profile else 300_000_000)):
      self.last_button_frame = self.frame
      self.conventional_cancel_credit_used = CS.conventional_cancel_credit.credit_ns
      can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.POWERTRAIN,
                                           (CS.conventional_cancel_credit.counter + 1) % 4, CruiseButtons.CANCEL))

    if (is_bolt_present_no_acc_pedal_profile(self.CP) and CS.out.canValid and not CS.out.canTimeout and
        CS.pedal_sensor_healthy and 0 < CS.pedal_sensor_ts_nanos <= now_nanos and
        now_nanos - CS.pedal_sensor_ts_nanos <= PEDAL_SENSOR_TIMEOUT_NS and
        bolt_acc_pedal_dashboard_drive_ready(CS, now_nanos) and CS.out.cruiseState.available and
        CS.bolt_pedal_stock_active and 0 < CS.bolt_pedal_stock_ts_nanos <= now_nanos and
        now_nanos - CS.bolt_pedal_stock_ts_nanos <= STOCK_ACC_STATUS_TIMEOUT_NS and
        (self.frame - self.last_button_frame) * DT_CTRL > .04 and
        CS.conventional_cancel_credit.available(now_nanos, self.bolt_removed_cancel_credit_used) and
        0 <= now_nanos - CS.conventional_cancel_credit.credit_ns <= 100_000_000):
      self.last_button_frame = self.frame
      self.bolt_removed_cancel_credit_used = CS.conventional_cancel_credit.credit_ns
      can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.POWERTRAIN,
                                           (CS.conventional_cancel_credit.counter + 1) % 4, CruiseButtons.CANCEL))

    if (self.bolt_pedal_removed and self.CP.openpilotLongitudinalControl and self.CP.carFingerprint in NO_ACC_BOLT_CAR and
        stock_steer_ready and CS.bolt_pedal_removed_stock_active and (self.frame - self.last_button_frame) * DT_CTRL > .04 and
        CS.conventional_cancel_credit.available(now_nanos, self.bolt_removed_cancel_credit_used) and
        0 <= now_nanos - CS.conventional_cancel_credit.credit_ns <= 100_000_000):
      self.last_button_frame = self.frame
      self.bolt_removed_cancel_credit_used = CS.conventional_cancel_credit.credit_ns
      can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.POWERTRAIN, (CS.buttons_counter + 1) % 4, CruiseButtons.CANCEL))

    if self.CP.networkLocation == NetworkLocation.fwdCamera and not self.bolt_cc_profile and not self.volt_camera_removed:
      # Silence "Take Steering" alert sent by camera, forward PSCMStatus with HandsOffSWlDetectionStatus=1
      if self.frame % 10 == 0:
        can_sends.append(gmcan.create_pscm_status(self.packer_pt, CanBus.CAMERA, CS.pscm_status))

    if self.bolt_cc_profile:
      owner = self.bolt_cc_owner
      owner.frame = self.frame - 1
      if len(bolt_sources) == 7:
        cruise_stamp, cruise_raw = bolt_sources[1]
        button_stamp, button_raw = bolt_sources[2]
        if cruise_stamp > self.bolt_cc_last_cruise_source:
          owner.cruise(cruise_raw, cruise_stamp)
          self.bolt_cc_last_cruise_source = cruise_stamp
        if button_stamp > self.bolt_cc_last_button_source:
          owner.button(button_raw, button_stamp)
          self.bolt_cc_last_button_source = button_stamp
        if owner.profile.camera_required:
          camera_stamp, camera_raw = getattr(CS, "bolt_cc_camera_source", (0, b""))
          if camera_stamp > self.bolt_cc_last_camera_source:
            owner.camera(camera_raw, camera_stamp)
            self.bolt_cc_last_camera_source = camera_stamp
        if self.CP.openpilotLongitudinalControl:
          request = owner.update(now_nanos, CS.out.vEgo, CS.out.cruiseState.speed, actuators.accel,
                                 enabled=CC.enabled, long_active=CC.longActive,
                                 drive=CS.out.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low),
                                 brake=CS.out.brakePressed or CS.out.regenBraking, gas=CS.out.gasPressed,
                                 sources_current=bolt_sources_fresh and CS.out.canValid and not CS.out.canTimeout,
                                 metric=self.bolt_cc_metric, hud_speed=hud_v_cruise)
          if request:
            can_sends.append(request)
        else:
          owner.frame = self.frame
        pscm_stamp, pscm_raw = bolt_sources[0]
        can_sends.extend(auxiliary_messages(owner, pscm_raw, pscm_stamp, now_nanos))

    new_actuators = actuators.as_builder()
    new_actuators.torque = self.apply_torque_last / self.params.STEER_MAX
    new_actuators.torqueOutputCan = self.apply_torque_last
    new_actuators.gas = self.apply_gas
    new_actuators.brake = self.apply_brake

    if self.volt_cc_profile or self.ordinary_cc_profile:
      physical = getattr(CS, 'volt_cc_physical', None)
      if self.volt_cc_prev_enabled and not CC.enabled and CS.out.cruiseState.enabled:
        self.volt_cc_cancel_pending = True
      if CC.enabled or not CS.out.cruiseState.enabled or not CS.out.cruiseState.available:
        self.volt_cc_cancel_pending = False
      self.volt_cc_prev_enabled = bool(CC.enabled)
      cancel_ready = (isinstance(physical, (VoltCcPhysical, PhysicalObservation)) and physical.sources_current(now_nanos) and physical.neutral_button and
                      CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and CS.out.cruiseState.enabled)
      stock_cancel_requested = False
      if self.ordinary_cc_profile and not self.CP.openpilotLongitudinalControl:
        self.cancel_counter = self.cancel_counter + 1 if CC.cruiseControl.cancel else 0
        stock_cancel_requested = self.cancel_counter > CAMERA_CANCEL_DELAY_FRAMES
      cancel_sent = False
      if ((self.volt_cc_cancel_pending or stock_cancel_requested) and
          (not self.ordinary_cc_profile or self.CP.openpilotLongitudinalControl or stock_cancel_requested) and
          cancel_ready and (self.frame - self.volt_cc_last_cancel_frame) * DT_CTRL > .04 and
          physical.button_credit_ns > 0 and physical.button_credit_ns != self.volt_cc_consumed_source_ns and
          (not self.ordinary_cc_profile or self.ordinary_cc_cadence.can_send(self.frame, CruiseButtons.CANCEL))):
        can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.POWERTRAIN, (CS.buttons_counter + 1) % 4, CruiseButtons.CANCEL))
        if self.ordinary_cc_profile:
          self.ordinary_cc_cadence.note_sent(self.frame, CruiseButtons.CANCEL)
        self.volt_cc_last_cancel_frame = self.frame
        self.volt_cc_consumed_source_ns = physical.button_credit_ns
        self.volt_cc_cancel_pending = False
        cancel_sent = True

      ready = (isinstance(physical, (VoltCcPhysical, PhysicalObservation)) and physical.current(now_nanos) and
               CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and CS.out.cruiseState.enabled and
               volt_cc_forward_gear(CS.out.gearShifter) and CS.out.vEgo >= self.CP.minEnableSpeed and
               not CS.out.brakePressed and not CS.out.gasPressed and not CS.out.regenBraking)
      gas_set_ready = (self.CP.openpilotLongitudinalControl and isinstance(physical, (VoltCcPhysical, PhysicalObservation)) and physical.current(now_nanos) and
                       CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and CS.out.cruiseState.enabled and
                       volt_cc_forward_gear(CS.out.gearShifter) and CS.out.vEgo >= self.CP.minEnableSpeed and
                       CC.enabled and (not CC.longActive or self.ordinary_cc_profile) and CS.out.gasPressed and
                       not CS.out.brakePressed and not CS.out.regenBraking and self.frame % 52 == 0 and
                       all(math.isfinite(value) for value in (CS.out.cruiseState.speed, CS.out.vEgo, hud_v_cruise)) and
                       CS.out.cruiseState.speed < CS.out.vEgo < hud_v_cruise and
                       physical.button_credit_ns > 0 and physical.button_credit_ns != self.volt_cc_consumed_source_ns and
                       (not self.volt_cc_profile or self.volt_cc_last_direction_frame is None or
                        (self.frame - self.volt_cc_last_direction_frame) * DT_CTRL > .2) and
                       (not self.ordinary_cc_profile or self.ordinary_cc_cadence.can_send(self.frame, CruiseButtons.DECEL_SET)))
      if cancel_sent:
        self.volt_cc_last_button_frame = self.frame
      elif gas_set_ready:
        can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.POWERTRAIN, (CS.buttons_counter + 1) % 4, CruiseButtons.DECEL_SET))
        if self.ordinary_cc_profile:
          self.ordinary_cc_cadence.note_sent(self.frame, CruiseButtons.DECEL_SET)
        else:
          self.volt_cc_last_direction_frame = self.frame
        self.volt_cc_consumed_source_ns = physical.button_credit_ns
        self.volt_cc_last_button_frame = self.frame
      elif not self.CP.openpilotLongitudinalControl or not ready or not CC.longActive:
        if self.ordinary_cc_profile:
          self.ordinary_cc_cadence.reset_burst()
          self.ordinary_cc_cadence.last_frame = self.frame
        self.volt_cc_last_button_frame = self.frame
        if not self.ordinary_cc_profile:
          self.volt_cc_consumed_source_ns = physical.button_credit_ns if isinstance(physical, VoltCcPhysical) else 0
      elif self.ordinary_cc_profile and (self.frame % 4 == 0 or self.CP.carFingerprint == CAR.CADILLAC_XT4_CC):
        button, rate, self.apply_speed = ordinary_button_request(
          CS.out.vEgo, CS.out.cruiseState.speed, actuators.accel, self.CP.minEnableSpeed, self.volt_cc_metric)
        source_ns = physical.button_credit_ns
        if (source_ns > 0 and source_ns != self.volt_cc_consumed_source_ns and
            self.ordinary_cc_cadence.ready(self.frame, CS.buttons_counter, button, rate)):
          self.volt_cc_consumed_source_ns = source_ns
          can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.POWERTRAIN, (CS.buttons_counter + 1) % 4, button))
      elif self.frame % 4 == 0:
        cruise_kph = CS.out.vCruise
        if self.volt_cc_speed_ceiling_mps is not None:
          cruise_kph = min(cruise_kph, self.volt_cc_speed_ceiling_mps * CV.MS_TO_KPH)
        button, rate = button_request(CS.out.vEgo, CS.out.cruiseState.speed, actuators.accel, cruise_kph,
                                      bool(CC.hudControl.leadVisible), self.volt_cc_metric)
        source_ns = physical.button_credit_ns
        if ((self.frame - self.volt_cc_last_button_frame) * DT_CTRL > rate and
            source_ns > 0 and source_ns != self.volt_cc_consumed_source_ns):
          self.volt_cc_last_direction_frame = self.frame
          self.volt_cc_last_button_frame = self.frame
          self.volt_cc_consumed_source_ns = source_ns
          can_sends.append(gmcan.create_buttons(self.packer_pt, CanBus.POWERTRAIN, (CS.buttons_counter + 1) % 4, button))

    keepalive_step = 100 if self.CP.carFingerprint == CAR.CHEVROLET_MALIBU_CC else 200
    if (self.ordinary_cc_profile and
        self.CP.openpilotLongitudinalControl and self.frame % keepalive_step == 0):
      can_sends += gmcan.create_adas_keepalive(CanBus.POWERTRAIN)
    self.frame += 1
    if _projection is not None:
      _projection.finish(new_actuators.torque)
    return new_actuators, can_sends
