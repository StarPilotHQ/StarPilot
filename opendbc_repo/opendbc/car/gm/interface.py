#!/usr/bin/env python3
from math import fabs, exp
import numpy as np

from opendbc.car import get_safety_config, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm.radar_interface import RadarInterface, RADAR_HEADER_MSG, CAMERA_DATA_HEADER_MSG
from opendbc.car.gm.values import (
  ALT_ACCS,
  ASCM_INT,
  CAMERA_ACC_CAR,
  CAR,
  CC_ONLY_CAR,
  CC_REGEN_PADDLE_CAR,
  EV_CAR,
  GM_AUTO_HOLD_CARS,
  SDGM_CAR,
  CarControllerParams,
  CanBus,
  GMFlags,
  GMSafetyFlags,
)
from opendbc.car.interfaces import CarInterfaceBase, TorqueFromLateralAccelCallbackType, LateralAccelFromTorqueCallbackType
from opendbc.safety import ALTERNATIVE_EXPERIENCE
from openpilot.common.params import Params, UnknownKeyName
from openpilot.starpilot.common.testing_grounds import testing_ground

TransmissionType = structs.CarParams.TransmissionType
NetworkLocation = structs.CarParams.NetworkLocation

NON_LINEAR_TORQUE_PARAMS = {
  CAR.CHEVROLET_BOLT_ACC_2022_2023: {
    "left": [2.6531724862969748, 1.1, 0.1919764879840985, 0.0],
    "right": [2.7031724862969748, 1.0, 0.1469764879840985, 0.0],
  },
  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL: {
    "left": [2.6531724862969748, 1.1, 0.1919764879840985, 0.0],
    "right": [2.7031724862969748, 1.0, 0.1469764879840985, 0.0],
  },
  CAR.CHEVROLET_BOLT_CC_2022_2023: {
    "left": [2.6531724862969748, 1.1, 0.1919764879840985, 0.0],
    "right": [2.7031724862969748, 1.0, 0.1469764879840985, 0.0],
  },
  CAR.CHEVROLET_BOLT_CC_2018_2021: {
    "left": [1.8, 1.1, 0.27, 0.0],
    "right": [2.0, 1.0, 0.205, 0.0],
  },
  CAR.CHEVROLET_BOLT_CC_2017: {
    "left": [2.15, 1.00, 0.129, 0.0],
    "right": [2.15, 1.00, 0.145, 0.0],
  },
  CAR.GMC_ACADIA: {
    "left": [4.78003305, 1.0, 0.3122, 0.05591772],
    "right": [4.78003305, 1.0, 0.3122, 0.05591772],
  },
  CAR.CHEVROLET_SILVERADO: {
    "left": [3.8, 0.81, 0.24, 0.0465122],
    "right": [3.8, 0.81, 0.24, 0.0465122],
  },
  CAR.CHEVROLET_SILVERADO_CC: {
    "left": [3.8, 0.81, 0.24, 0.0465122],
    "right": [3.8, 0.81, 0.24, 0.0465122],
  },
  CAR.CADILLAC_XT4: {
    "left": [2.4, 0.95, 0.28, 0.0],
    "right": [2.4, 0.95, 0.28, 0.0],
  },
  CAR.CHEVROLET_VOLT: {
    "left": [1.525, 1.05, 0.155, 0.0],
    "right": [1.525, 0.95, 0.150, 0.0],
  },
  CAR.CHEVROLET_TRAX: {
    "left": [3.8060, 0.8282, 0.1702, 0],
    "right": [3.8060, 0.8282, 0.1702, 0],
  },
  CAR.BUICK_ENVISION_SDGM: {
    "left": [2.4, 1.05, 0.20, 0.0],
    "right": [2.4, 1.05, 0.20, 0.0],
  },
}

NON_LINEAR_TORQUE_PARAM_ALIASES = {
  CAR.CHEVROLET_VOLT_ASCM: CAR.CHEVROLET_VOLT,
  CAR.CHEVROLET_VOLT_CAMERA: CAR.CHEVROLET_VOLT,
  CAR.CHEVROLET_VOLT_CC: CAR.CHEVROLET_VOLT,
  CAR.CHEVROLET_VOLT_2019: CAR.CHEVROLET_VOLT,
}


def get_nonlinear_torque_params(car_fingerprint):
  source_fingerprint = NON_LINEAR_TORQUE_PARAM_ALIASES.get(car_fingerprint, car_fingerprint)
  return NON_LINEAR_TORQUE_PARAMS.get(source_fingerprint)

PEDAL_MSG = 0x201
CAM_MSG = 0x320
ACCELERATOR_POS_MSG = 0xBE

VOLT_LIKE_CARS = {
  CAR.CHEVROLET_VOLT,
  CAR.CHEVROLET_VOLT_2019,
  CAR.CHEVROLET_VOLT_ASCM,
  CAR.CHEVROLET_VOLT_CAMERA,
  CAR.CHEVROLET_VOLT_CC,
  CAR.CHEVROLET_MALIBU,
  CAR.CHEVROLET_MALIBU_ASCM,
  CAR.CHEVROLET_MALIBU_SDGM,
  CAR.CHEVROLET_MALIBU_CC,
  CAR.CHEVROLET_MALIBU_HYBRID_CC,
}

VOLT_LONG_TEST_TUNE_CARS = {
  CAR.CHEVROLET_VOLT,
  CAR.CHEVROLET_VOLT_2019,
  CAR.CHEVROLET_VOLT_ASCM,
  CAR.CHEVROLET_VOLT_CAMERA,
  CAR.CHEVROLET_VOLT_CC,
}

VOLT_BSM_CARS = {
  CAR.CHEVROLET_VOLT,
  CAR.CHEVROLET_VOLT_2019,
  CAR.CHEVROLET_VOLT_ASCM,
  CAR.CHEVROLET_VOLT_CAMERA,
  CAR.CHEVROLET_VOLT_CC,
}

BOLT_PEDAL_LONG_CARS = {
  CAR.CHEVROLET_BOLT_CC_2017,
  CAR.CHEVROLET_BOLT_CC_2018_2021,
  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
  CAR.CHEVROLET_BOLT_CC_2022_2023,
  CAR.CHEVROLET_MALIBU_HYBRID_CC,
}

# Cancel-button remap support uses the same safety path on all pedal-long Bolts,
# but only gen1 needs extra LKAS suppression in CarState.
BOLT_GEN1_CANCEL_PERSONALITY_CARS = {
  CAR.CHEVROLET_BOLT_CC_2017,
  CAR.CHEVROLET_BOLT_CC_2018_2021,
}
CANCEL_REMAP_DISTANCE_CARS = BOLT_GEN1_CANCEL_PERSONALITY_CARS | {
  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
  CAR.CHEVROLET_BOLT_CC_2022_2023,
}


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  @staticmethod
  def get_pid_accel_limits(CP, current_speed, cruise_speed):
    if CP.enableGasInterceptorDEPRECATED and bool(CP.flags & GMFlags.PEDAL_LONG.value):
      if CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL:
        accel_min = CarControllerParams.ACCEL_MIN
        accel_max = np.interp(current_speed, [0.0, 1.5, 4.0, 8.0, 15.0],
                              [0.54, 0.74, 1.03, 1.46, CarControllerParams.ACCEL_MAX])
      elif CP.carFingerprint in BOLT_PEDAL_LONG_CARS:
        accel_min = np.interp(current_speed, [0.0, 1.5, 4.0, 8.0, 15.0, 30.0],
                              [-0.93, -1.28, -1.98, -2.58, -2.86, -2.95])
        accel_max = np.interp(current_speed, [0.0, 1.5, 4.0, 8.0, 15.0],
                              [0.54, 0.74, 1.03, 1.46, CarControllerParams.ACCEL_MAX])
      else:
        accel_min = np.interp(current_speed, [0.0, 1.5, 4.0, 8.0, 15.0, 30.0],
                              [-0.95, -1.3, -1.85, -2.3, -2.6, -2.8])
        accel_max = np.interp(current_speed, [0.0, 1.5, 4.0, 8.0, 15.0],
                              [0.60, 0.85, 1.15, 1.60, CarControllerParams.ACCEL_MAX])
      return float(accel_min), float(accel_max)
    return CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX

  # Determined by iteratively plotting and minimizing error for f(angle, speed) = steer.
  @staticmethod
  def get_steer_feedforward_volt(desired_angle, v_ego):
    desired_angle *= 0.02904609
    sigmoid = desired_angle / (1 + fabs(desired_angle))
    return 0.10006696 * sigmoid * (v_ego + 3.12485927)

  def get_steer_feedforward_function(self):
    if self.CP.carFingerprint in VOLT_LIKE_CARS:
      return self.get_steer_feedforward_volt
    else:
      return CarInterfaceBase.get_steer_feedforward_default

  def get_lataccel_torque_siglin(self) -> float:

    def torque_from_lateral_accel_siglin_func(lateral_acceleration: float) -> float:
      # The "lat_accel vs torque" relationship is assumed to be the sum of "sigmoid + linear" curves
      # An important thing to consider is that the slope at 0 should be > 0 (ideally >1)
      # This has big effect on the stability about 0 (noise when going straight)
      non_linear_torque_params = get_nonlinear_torque_params(self.CP.carFingerprint)
      assert non_linear_torque_params, "The params are not defined"
      if isinstance(non_linear_torque_params, dict):
        side_key = "left" if lateral_acceleration >= 0 else "right"
        a, b, c, d = non_linear_torque_params[side_key]
      else:
        a, b, c, d = non_linear_torque_params
      sig_input = a * lateral_acceleration
      sig = np.sign(sig_input) * (1 / (1 + exp(-fabs(sig_input))) - 0.5)
      steer_torque = (sig * b) + (lateral_acceleration * c) + d
      return float(steer_torque)

    lataccel_values = np.arange(-5.0, 5.0, 0.01)
    torque_values = [torque_from_lateral_accel_siglin_func(x) for x in lataccel_values]
    assert min(torque_values) < -1 and max(torque_values) > 1, "The torque values should cover the range [-1, 1]"
    return torque_values, lataccel_values

  def torque_from_lateral_accel(self) -> TorqueFromLateralAccelCallbackType:
    if get_nonlinear_torque_params(self.CP.carFingerprint) is not None:
      torque_values, lataccel_values = self.get_lataccel_torque_siglin()

      def torque_from_lateral_accel_siglin(lateral_acceleration: float, torque_params: structs.CarParams.LateralTorqueTuning):
        return np.interp(lateral_acceleration, lataccel_values, torque_values)
      return torque_from_lateral_accel_siglin
    else:
      return self.torque_from_lateral_accel_linear

  def lateral_accel_from_torque(self) -> LateralAccelFromTorqueCallbackType:
    if get_nonlinear_torque_params(self.CP.carFingerprint) is not None:
      torque_values, lataccel_values = self.get_lataccel_torque_siglin()

      def lateral_accel_from_torque_siglin(torque: float, torque_params: structs.CarParams.LateralTorqueTuning):
        return np.interp(torque, torque_values, lataccel_values)
      return lateral_accel_from_torque_siglin
    else:
      return self.lateral_accel_from_torque_linear

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    params = Params()
    try:
      gm_pedal_longitudinal = params.get_bool("GMPedalLongitudinal")
    except UnknownKeyName:
      gm_pedal_longitudinal = True
    try:
      disable_openpilot_long = params.get_bool("DisableOpenpilotLongitudinal")
    except UnknownKeyName:
      disable_openpilot_long = False
    try:
      gm_auto_hold = params.get_bool("GMAutoHold")
    except UnknownKeyName:
      gm_auto_hold = False
    try:
      volt_one_pedal_mode = params.get_bool("VoltOnePedalMode")
    except UnknownKeyName:
      volt_one_pedal_mode = False

    ret.brand = "gm"
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.gm)]
    ret.autoResumeSng = False
    # Some Volt installs don't expose the BSM frame during startup fingerprinting.
    ret.enableBsm = 0x142 in fingerprint[CanBus.POWERTRAIN] or candidate in VOLT_BSM_CARS
    has_sascm = 0x2FF in fingerprint[CanBus.POWERTRAIN]
    if has_sascm:
      ret.flags |= GMFlags.SASCM.value

    if candidate in EV_CAR:
      ret.transmissionType = TransmissionType.direct
    else:
      ret.transmissionType = TransmissionType.automatic

    pedal_detected = PEDAL_MSG in fingerprint[CanBus.POWERTRAIN]
    pedal_long_enabled = pedal_detected and gm_pedal_longitudinal
    if pedal_long_enabled:
      ret.enableGasInterceptorDEPRECATED = True
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_GAS_INTERCEPTOR.value
      if candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023:
        # Keep ACC Bolts on stock ACC path even if a pedal frame appears.
        ret.enableGasInterceptorDEPRECATED = False
        ret.safetyConfigs[0].safetyParam &= ~GMSafetyFlags.FLAG_GM_GAS_INTERCEPTOR.value

    kaofui_cars = SDGM_CAR | ASCM_INT | VOLT_LIKE_CARS | {CAR.CHEVROLET_MALIBU_HYBRID_CC}
    ret.longitudinalTuning.kiBP = [5., 35.] if candidate in kaofui_cars else [5., 35., 60.]

    is_bolt_2022_2023_pedal = candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and ret.enableGasInterceptorDEPRECATED

    kaofui_camera_cars = {
      CAR.CHEVROLET_VOLT_CAMERA,
      CAR.CHEVROLET_MALIBU_HYBRID_CC,
    }
    bolt_cc_camera_cars = {
      CAR.CHEVROLET_BOLT_CC_2017,
      CAR.CHEVROLET_BOLT_CC_2018_2021,
      CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
      CAR.CHEVROLET_BOLT_CC_2022_2023,
    }
    is_camera_acc = candidate in CAMERA_ACC_CAR and candidate not in kaofui_cars and \
                    (candidate not in CC_ONLY_CAR or candidate in bolt_cc_camera_cars)

    if candidate in kaofui_camera_cars:
      ret.alphaLongitudinalAvailable = candidate not in (CC_ONLY_CAR | ASCM_INT | SDGM_CAR) or has_sascm
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and not docs
      ret.pcmCruise = True
      ret.minEnableSpeed = 5 * CV.KPH_TO_MS
      ret.minSteerSpeed = 10 * CV.KPH_TO_MS
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM.value

      ret.longitudinalTuning.kiV = [0.5, 0.5]
      ret.stoppingDecelRate = 1.0
      ret.vEgoStopping = 0.25
      ret.vEgoStarting = 0.25
      ret.stopAccel = -0.25

      if ret.alphaLongitudinalAvailable and alpha_long:
        ret.pcmCruise = False
        ret.openpilotLongitudinalControl = True
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM_LONG.value

    elif is_camera_acc:
      ret.alphaLongitudinalAvailable = candidate not in (CC_ONLY_CAR | ALT_ACCS) and not ret.enableGasInterceptorDEPRECATED
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.radarUnavailable = True
      ret.pcmCruise = not ret.enableGasInterceptorDEPRECATED
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM.value
      ret.minEnableSpeed = 5 * CV.KPH_TO_MS
      ret.minSteerSpeed = 10 * CV.KPH_TO_MS

      ret.longitudinalTuning.kiV = [0.5, 0.5, 0.5]
      ret.stoppingDecelRate = 1.0
      ret.vEgoStopping = 0.25
      ret.vEgoStarting = 0.25
      ret.stopAccel = -0.25

      if ret.alphaLongitudinalAvailable and alpha_long:
        ret.pcmCruise = False
        ret.openpilotLongitudinalControl = True
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM_LONG.value

    elif candidate in SDGM_CAR:
      ret.alphaLongitudinalAvailable = candidate not in (CC_ONLY_CAR | ASCM_INT | SDGM_CAR) or has_sascm
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and not docs
      ret.pcmCruise = True
      ret.minEnableSpeed = -1.
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_SDGM.value

      if ACCELERATOR_POS_MSG not in fingerprint[CanBus.POWERTRAIN]:
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_FORCE_BRAKE_C9.value
        ret.flags |= GMFlags.FORCE_BRAKE_C9.value

      ret.longitudinalTuning.kiV = [0.5, 0.5] if candidate in kaofui_cars else [0.5, 0.5, 0.5]
      ret.stoppingDecelRate = 1.0
      ret.vEgoStopping = 0.25
      ret.vEgoStarting = 0.25
      ret.stopAccel = -0.25

      if ret.alphaLongitudinalAvailable and alpha_long:
        ret.pcmCruise = False
        ret.openpilotLongitudinalControl = True
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM_LONG.value
      if is_bolt_2022_2023_pedal:
        ret.alphaLongitudinalAvailable = False
        ret.pcmCruise = False

    elif candidate in ASCM_INT:
      ret.alphaLongitudinalAvailable = candidate not in (CC_ONLY_CAR | ASCM_INT | SDGM_CAR) or has_sascm
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and not docs
      ret.pcmCruise = True
      ret.minEnableSpeed = 5 * CV.KPH_TO_MS
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM.value
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_ASCM_INT.value
      if ACCELERATOR_POS_MSG not in fingerprint[CanBus.POWERTRAIN]:
        ret.flags |= GMFlags.FORCE_BRAKE_C9.value
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_FORCE_BRAKE_C9.value

      ret.longitudinalTuning.kiV = [0.5, 0.5] if candidate in kaofui_cars else [0.5, 0.5, 0.5]
      ret.stoppingDecelRate = 1.0
      ret.vEgoStopping = 0.25
      ret.vEgoStarting = 0.25
      ret.stopAccel = -0.25

      if ret.alphaLongitudinalAvailable and alpha_long:
        ret.pcmCruise = False
        ret.openpilotLongitudinalControl = True
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM_LONG.value
      if is_bolt_2022_2023_pedal:
        ret.alphaLongitudinalAvailable = False
        ret.pcmCruise = False

    else:  # ASCM, OBD-II harness
      ret.openpilotLongitudinalControl = not disable_openpilot_long
      ret.networkLocation = NetworkLocation.gateway
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and CAMERA_DATA_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and not docs
      ret.pcmCruise = False
      ret.minEnableSpeed = 18 * CV.MPH_TO_MS
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS

      ret.longitudinalTuning.kiV = [0.5, 0.5] if candidate in kaofui_cars else [0.5, 0.5, 0.5]
      if candidate in kaofui_cars:
        ret.stoppingDecelRate = 3
        ret.vEgoStopping = 0.75
        ret.vEgoStarting = 0.75
        ret.stopAccel = -1.5

      if ret.enableGasInterceptorDEPRECATED:
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_ASCM_LONG.value

    if candidate in ALT_ACCS:
      ret.alphaLongitudinalAvailable = False
      ret.openpilotLongitudinalControl = False
      ret.minEnableSpeed = -1.

    # Start with a baseline tuning for all GM vehicles. Override tuning as needed in each model section below.
    ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
    ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.2], [0.00]]
    ret.lateralTuning.pid.kf = 0.00004   # full torque for 20 deg at 80mph means 0.00007818594
    ret.steerActuatorDelay = 0.1  # Default delay, not measured yet

    ret.steerLimitTimer = 0.4
    ret.radarTimeStepDEPRECATED = 0.15 if candidate == CAR.BUICK_LACROSSE else 0.0667
    ret.longitudinalActuatorDelay = 0.5  # large delay to initially start braking

    if candidate in (
      CAR.CHEVROLET_VOLT,
      CAR.CHEVROLET_VOLT_2019,
      CAR.CHEVROLET_VOLT_ASCM,
      CAR.CHEVROLET_VOLT_CAMERA,
      CAR.CHEVROLET_VOLT_CC,
    ):
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS

    if candidate in (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_CC, CAR.CHEVROLET_VOLT_CAMERA):
      ret.minEnableSpeed = -1

    if candidate == CAR.CHEVROLET_VOLT_2019 and not ret.openpilotLongitudinalControl:
      ret.minEnableSpeed = -1

    if candidate in VOLT_LIKE_CARS:
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)
      ret.steerActuatorDelay = 0.2
      if candidate == CAR.CHEVROLET_MALIBU_HYBRID_CC and ret.enableGasInterceptorDEPRECATED:
        ret.flags |= GMFlags.PEDAL_LONG.value

    elif candidate == CAR.GMC_ACADIA:
      ret.minEnableSpeed = -1.  # engage speed is decided by pcm
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.BUICK_ENVISION_SDGM:
      ret.minEnableSpeed = -1.  # engage speed is decided by pcm
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.BUICK_LACROSSE, CAR.BUICK_LACROSSE_ASCM, CAR.BUICK_LACROSSE_ASCM_19US):
      CarInterfaceBase.configure_torque_tune(CAR.BUICK_LACROSSE, ret.lateralTuning)
      if candidate == CAR.BUICK_LACROSSE_ASCM_19US:
        ret.minSteerSpeed = 28 * CV.MPH_TO_MS

    elif candidate == CAR.CADILLAC_ESCALADE:
      ret.minEnableSpeed = -1.  # engage speed is decided by pcm
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CADILLAC_ESCALADE_ASCM:
      CarInterfaceBase.configure_torque_tune(CAR.CADILLAC_ESCALADE, ret.lateralTuning)

    elif candidate in (CAR.CADILLAC_ESCALADE_ESV, CAR.CADILLAC_ESCALADE_ESV_2019, CAR.CADILLAC_ESCALADE_ESV_2019_ASCM):
      ret.minEnableSpeed = -1.  # engage speed is decided by pcm

      if candidate == CAR.CADILLAC_ESCALADE_ESV:
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[10., 41.0], [10., 41.0]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.13, 0.24], [0.01, 0.02]]
        ret.lateralTuning.pid.kf = 0.000045
      else:
        ret.steerActuatorDelay = 0.2
        torque_candidate = CAR.CADILLAC_ESCALADE_ESV_2019 if candidate == CAR.CADILLAC_ESCALADE_ESV_2019_ASCM else candidate
        CarInterfaceBase.configure_torque_tune(torque_candidate, ret.lateralTuning)

    elif candidate in (
      CAR.CHEVROLET_BOLT_ACC_2022_2023,
      CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
      CAR.CHEVROLET_BOLT_CC_2022_2023,
      CAR.CHEVROLET_BOLT_CC_2018_2021,
      CAR.CHEVROLET_BOLT_CC_2017,
    ):
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

      # Bolt-only lateral tuning overrides
      ret.lateralTuning.torque.kpDEPRECATED = 1.03
      ret.lateralTuning.torque.kiDEPRECATED = 1.07
      ret.lateralTuning.torque.kdDEPRECATED = 0.93
      ret.lateralTuning.torque.kfDEPRECATED = 0.02

      if candidate in (
        CAR.CHEVROLET_BOLT_CC_2018_2021,
        CAR.CHEVROLET_BOLT_ACC_2022_2023,
        CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
        CAR.CHEVROLET_BOLT_CC_2022_2023,
      ):
        # Apply 2018+/2022 generation-specific FF and Ki/Kd tweaks.
        ret.lateralTuning.torque.kiDEPRECATED *= 1.07
        ret.lateralTuning.torque.kdDEPRECATED *= 0.93
        ret.lateralTuning.torque.kfDEPRECATED *= 0.75

      if candidate == CAR.CHEVROLET_BOLT_CC_2017:
        ret.lateralTuning.torque.kpDEPRECATED *= 1.0
        ret.lateralTuning.torque.kiDEPRECATED *= 0.9
        ret.lateralTuning.torque.kdDEPRECATED *= 0.9
        ret.lateralTuning.torque.kfDEPRECATED = 0.026
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_BOLT_2017.value

      if ret.enableGasInterceptorDEPRECATED:
        # ACC Bolts use pedal for full longitudinal control, not just SNG.
        ret.flags |= GMFlags.PEDAL_LONG.value

    elif candidate in (CAR.CHEVROLET_SILVERADO, CAR.CHEVROLET_SILVERADO_CC):
      ret.minEnableSpeed = 0.
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.CHEVROLET_EQUINOX, CAR.CHEVROLET_EQUINOX_CC):
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.CHEVROLET_TRAILBLAZER, CAR.CHEVROLET_TRAILBLAZER_CC):
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.CADILLAC_XT4, CAR.CADILLAC_XT4_CC):
      ret.steerActuatorDelay = 0.2
      if not ret.openpilotLongitudinalControl:
        ret.minEnableSpeed = -1.
      ret.minSteerSpeed = 30 * CV.MPH_TO_MS
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CHEVROLET_VOLT_2019:
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.CHEVROLET_SUBURBAN, CAR.CHEVROLET_SUBURBAN_ASCM, CAR.CHEVROLET_SUBURBAN_CAMERA, CAR.CHEVROLET_SUBURBAN_CC):
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.GMC_YUKON_CC:
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CADILLAC_XT6:
      ret.steerActuatorDelay = 0.2
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.CADILLAC_XT5, CAR.CADILLAC_XT5_CC):
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.CHEVROLET_TRAVERSE, CAR.CHEVROLET_BLAZER):
      ret.steerActuatorDelay = 0.2
      if not ret.openpilotLongitudinalControl:
        ret.minEnableSpeed = -1.
      if candidate == CAR.CHEVROLET_BLAZER:
        # The Blazer builds brake torque noticeably later than the rest of the GM set.
        # A slightly larger planner delay estimate starts the request earlier and keeps
        # stopped-lead approaches from turning into a late, harsh max-brake catch-up.
        ret.longitudinalActuatorDelay = 0.7
        ret.longitudinalTuning.kpBP = [0.0, 4.0, 12.0, 35.0]
        ret.longitudinalTuning.kpV = [0.09, 0.075, 0.055, 0.040]
        ret.longitudinalTuning.kiBP = [0.0, 4.0, 12.0, 35.0]
        ret.longitudinalTuning.kiV = [0.03, 0.04, 0.055, 0.07]
        ret.minEnableSpeed = 5 * CV.KPH_TO_MS
        ret.stoppingDecelRate = 1.0
        ret.vEgoStopping = 0.35
        ret.vEgoStarting = 0.35
        ret.stopAccel = -0.30
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.BUICK_BABYENCLAVE:
      ret.steerActuatorDelay = 0.2
      if not ret.openpilotLongitudinalControl:
        ret.minEnableSpeed = -1.
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CADILLAC_CT6_CC:
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.GMC_YUKON:
      ret.steerActuatorDelay = 0.5
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.CHEVROLET_MALIBU, CAR.CHEVROLET_MALIBU_CC, CAR.CHEVROLET_MALIBU_HYBRID_CC):
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    if ret.enableGasInterceptorDEPRECATED:
      ret.autoResumeSng = True
      ret.minEnableSpeed = -1
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.openpilotLongitudinalControl = not disable_openpilot_long
      ret.pcmCruise = False
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM.value

    if ret.enableGasInterceptorDEPRECATED and (candidate in CC_ONLY_CAR or candidate in CAMERA_ACC_CAR):
      # Pedal-long path: only entered when pedal interceptor was actually enabled.
      ret.flags |= GMFlags.PEDAL_LONG.value
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_PEDAL_LONG.value

      if candidate == CAR.CHEVROLET_MALIBU_CC:
        ret.longitudinalTuning.kpBP = [0.0, 5.0, 35.0]
        ret.longitudinalTuning.kpV = [0.06, 0.05, 0.04]
        ret.longitudinalTuning.kiBP = [0.0, 5.0, 35.0]
        ret.longitudinalTuning.kiV = [0.0, 0.30, 0.45]
        ret.longitudinalTuning.kfDEPRECATED = 0.15
      else:
        ret.longitudinalTuning.kpBP = [0.0, 5.0, 15.0, 35.0]
        ret.longitudinalTuning.kpV = [0.09, 0.08, 0.06, 0.045]
        ret.longitudinalTuning.kiBP = [0.0, 3.0, 6.0, 35.0]
        ret.longitudinalTuning.kiV = [0.09, 0.13, 0.19, 0.28]

        if candidate in BOLT_PEDAL_LONG_CARS:
          ret.longitudinalTuning.kpV = [0.095, 0.085, 0.065, 0.050]
          ret.longitudinalTuning.kiV = [0.07, 0.10, 0.15, 0.24]
          ret.longitudinalTuning.kfDEPRECATED = 0.20
          ret.longitudinalActuatorDelay = 0.6
        else:
          ret.longitudinalTuning.kfDEPRECATED = 0.25

      if is_bolt_2022_2023_pedal:
        # Gen2 Bolt pedal-long should follow the no-ACC panda path.
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_NO_ACC.value
        ret.startingState = True
        ret.startAccel = 0.55
        ret.vEgoStarting = max(ret.vEgoStarting, 0.35)

      if candidate in (CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, CAR.CHEVROLET_MALIBU_HYBRID_CC):
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_BOLT_2022_PEDAL.value

      ret.stoppingDecelRate = 0.8

    if ret.enableGasInterceptorDEPRECATED and candidate == CAR.CHEVROLET_MALIBU_HYBRID_CC:
      # Keep Malibu Hybrid pedal on the same path as Bolt pedal-long behavior.
      ret.flags |= GMFlags.PEDAL_LONG.value
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_PEDAL_LONG.value
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_BOLT_2022_PEDAL.value
      ret.longitudinalTuning.kpBP = [0.0, 5.0, 15.0, 35.0]
      ret.longitudinalTuning.kpV = [0.095, 0.085, 0.065, 0.050]
      ret.longitudinalTuning.kiBP = [0.0, 3.0, 6.0, 35.0]
      ret.longitudinalTuning.kiV = [0.07, 0.10, 0.15, 0.24]
      ret.longitudinalTuning.kfDEPRECATED = 0.20
      ret.stoppingDecelRate = 0.8
      ret.minEnableSpeed = -1
      ret.pcmCruise = False
      ret.openpilotLongitudinalControl = not disable_openpilot_long

    volt_test_tune_active = (
      testing_ground.use_2 and
      ret.openpilotLongitudinalControl and
      candidate in VOLT_LONG_TEST_TUNE_CARS
    )
    if volt_test_tune_active:
      # Volt long can still fall back to an all-I tune on both the interceptor and
      # ASCM-int paths. The test-ground tune adds a modest P term, trims
      # mid/high-speed I memory, and uses a dedicated starting state so
      # stop-and-go launches do not wind up the PID.
      ret.longitudinalTuning.kpBP = [0.0, 4.0, 12.0, 35.0]
      ret.longitudinalTuning.kpV = [0.10, 0.072, 0.050, 0.040]
      ret.longitudinalTuning.kiBP = [0.0, 4.0, 12.0, 35.0]
      ret.longitudinalTuning.kiV = [0.025, 0.030, 0.040, 0.055]
      ret.startingState = True
      ret.startAccel = 1.15
      ret.vEgoStarting = max(ret.vEgoStarting, 0.35)

    if ret.openpilotLongitudinalControl and candidate in (CAR.CHEVROLET_SILVERADO, CAR.CHEVROLET_SILVERADO_CC) and not ret.enableGasInterceptorDEPRECATED:
      ret.longitudinalTuning.kpBP = [0.0, 5.0, 15.0, 35.0]
      ret.longitudinalTuning.kpV = [0.02, 0.03, 0.028, 0.022]
      ret.longitudinalTuning.kiBP = [0.0, 5.0, 15.0, 35.0]
      ret.longitudinalTuning.kiV = [0.20, 0.18, 0.13, 0.08]

    elif candidate in CC_ONLY_CAR and not ret.enableGasInterceptorDEPRECATED:
      ret.flags |= GMFlags.CC_LONG.value
      ret.alphaLongitudinalAvailable = False
      ret.openpilotLongitudinalControl = not disable_openpilot_long
      ret.pcmCruise = False
      if candidate not in (CAR.CHEVROLET_SILVERADO, CAR.CHEVROLET_SILVERADO_CC):
        ret.minEnableSpeed = 24 * CV.MPH_TO_MS
      ret.radarUnavailable = True
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_CC_LONG.value

      if candidate != CAR.CHEVROLET_MALIBU_HYBRID_CC:
        ret.longitudinalTuning.kpBP = [10.7, 10.8, 28.]  # 10.7 m/s == 24 mph
        ret.longitudinalTuning.kpV = [0., 5., 2.]
        ret.longitudinalTuning.deadzoneBPDEPRECATED = [0., 1.]
        ret.longitudinalTuning.deadzoneVDEPRECATED = [0.9, 0.9]
        ret.longitudinalActuatorDelay = 1.
        if candidate == CAR.CHEVROLET_MALIBU_CC:
          ret.longitudinalTuning.kpV = [0., 20., 20.]
        ret.longitudinalTuning.kiBP = [0.]
        ret.longitudinalTuning.kiV = [0.1]
        ret.stoppingDecelRate = 11.18  # == 25 mph/s (.04 rate)

    if candidate in CC_ONLY_CAR:
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_NO_ACC.value
      if candidate == CAR.CHEVROLET_VOLT_CC and ret.networkLocation == NetworkLocation.gateway:
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_VOLT_CC_GATEWAY.value

    if candidate in SDGM_CAR and ACCELERATOR_POS_MSG not in fingerprint[CanBus.POWERTRAIN]:
      ret.flags |= GMFlags.FORCE_BRAKE_C9.value
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_FORCE_BRAKE_C9.value

    # Exception for flashed cars, or cars whose camera was removed.
    missing_camera_msg = CAM_MSG not in fingerprint.get(CanBus.CAMERA, {})
    if (ret.networkLocation == NetworkLocation.fwdCamera or candidate in CC_ONLY_CAR) and missing_camera_msg and candidate not in (ASCM_INT | SDGM_CAR):
      ret.flags |= GMFlags.NO_CAMERA.value
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_NO_CAMERA.value

    if ACCELERATOR_POS_MSG not in fingerprint[CanBus.POWERTRAIN]:
      ret.flags |= GMFlags.NO_ACCELERATOR_POS_MSG.value
      if candidate in (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_CC) and ret.networkLocation == NetworkLocation.gateway:
        # Reuse the no-camera safety bit as an ASCM Volt selector for the alternate EBCM brake path.
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_NO_CAMERA.value

    try:
      remote_start_boots_comma = params.get_bool("RemoteStartBootsComma")
    except UnknownKeyName:
      remote_start_boots_comma = False

    if remote_start_boots_comma:
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_REMOTE_START_BOOTS_COMMA.value

    gm_stock_friction_brake_safety = (
      ret.openpilotLongitudinalControl and
      (
        (gm_auto_hold and candidate in GM_AUTO_HOLD_CARS) or
        (volt_one_pedal_mode and candidate in {
          CAR.CHEVROLET_VOLT,
          CAR.CHEVROLET_VOLT_2019,
          CAR.CHEVROLET_VOLT_ASCM,
          CAR.CHEVROLET_VOLT_CAMERA,
        })
      )
    )
    if gm_stock_friction_brake_safety:
      # marker on non-pedal paths. Auto hold and one-pedal can run while OP
      # longitudinal is configured but not currently active, so the bit must
      # be present regardless of the current long-control mode. Do not expose
      # the path at all when OP long is disabled in CarParams.
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_PANDA_PADDLE_SCHED.value

    volt_stock_one_pedal_safety = (
      ret.openpilotLongitudinalControl and
      volt_one_pedal_mode and
      candidate in {
        CAR.CHEVROLET_VOLT,
        CAR.CHEVROLET_VOLT_2019,
        CAR.CHEVROLET_VOLT_ASCM,
        CAR.CHEVROLET_VOLT_CAMERA,
      }
    )
    if volt_stock_one_pedal_safety:
      # Reuse the 3D1 scheduler bit as a Volt one-pedal marker on non-pedal
      # ACC paths. The bit is ignored by the actual 3D1 scheduler unless the
      # car is on a pedal-long CC-only path, so this stays isolated from Bolt.
      # Do not expose the path at all when OP long is disabled in CarParams.
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_PANDA_3D1_SCHED.value

    use_panda_3d1_sched = (
      ret.openpilotLongitudinalControl and
      ret.enableGasInterceptorDEPRECATED and
      bool(ret.flags & GMFlags.PEDAL_LONG.value) and
      candidate in CC_ONLY_CAR and
      candidate != CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL
    )
    if use_panda_3d1_sched:
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_PANDA_3D1_SCHED.value

    use_panda_paddle_sched = (
      ret.openpilotLongitudinalControl and
      ret.enableGasInterceptorDEPRECATED and
      bool(ret.flags & GMFlags.PEDAL_LONG.value) and
      candidate in CC_REGEN_PADDLE_CAR
    )
    if use_panda_paddle_sched:
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.FLAG_GM_PANDA_PADDLE_SCHED.value

    try:
      remap_cancel_to_distance_toggle = params.get_bool("RemapCancelToDistance")
    except UnknownKeyName:
      remap_cancel_to_distance_toggle = False

    remap_cancel_to_distance = (
      remap_cancel_to_distance_toggle and
      ret.openpilotLongitudinalControl and
      bool(ret.flags & GMFlags.PEDAL_LONG.value) and
      candidate in CANCEL_REMAP_DISTANCE_CARS
    )
    malibu_cancel_passthrough = (
      candidate == CAR.CHEVROLET_MALIBU_HYBRID_CC and
      ret.openpilotLongitudinalControl and
      bool(ret.flags & GMFlags.PEDAL_LONG.value)
    )
    if remap_cancel_to_distance or malibu_cancel_passthrough:
      ret.alternativeExperience |= ALTERNATIVE_EXPERIENCE.GM_REMAP_CANCEL_TO_DISTANCE

    if candidate == CAR.CHEVROLET_TRAX:
      ret.steerActuatorDelay = 0.46
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    return ret
