#!/usr/bin/env python3
from math import exp, fabs
import numpy as np

from opendbc.car import get_safety_config, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarInterfaceBase, TorqueFromLateralAccelCallbackType, LateralAccelFromTorqueCallbackType
from opendbc.car.mazda.carcontroller import CarController
from opendbc.car.mazda.carstate import CarState
from opendbc.car.mazda.values import CAR, LKAS_LIMITS, MazdaSafetyFlags, GEN1, GEN2, GEN3
from openpilot.common.params import Params

NON_LINEAR_TORQUE_PARAMS = {
  CAR.MAZDA_3_2019: (4.6, 0.6, 0.134, 0.3605),
  CAR.MAZDA_CX_30: (4.68689, 0.79999, 0.18244, 0.38763),
  CAR.MAZDA_CX_30_2023: (4.68689, 0.79999, 0.18244, 0.38763),
  CAR.MAZDA_CX_50: (4.68689, 0.79999, 0.18244, 0.38763)
}

class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController

  def get_lataccel_torque_siglin(self) -> float:

    def torque_from_lateral_accel_siglin_func(lateral_acceleration: float) -> float:
      # The "lat_accel vs torque" relationship is assumed to be the sum of "sigmoid + linear" curves
      # An important thing to consider is that the slope at 0 should be > 0 (ideally >1)
      # This has big effect on the stability about 0 (noise when going straight)
      non_linear_torque_params = NON_LINEAR_TORQUE_PARAMS.get(self.CP.carFingerprint)
      assert non_linear_torque_params, "The params are not defined"
      a, b, c, _ = non_linear_torque_params
      sig_input = a * lateral_acceleration
      sig = np.sign(sig_input) * (1 / (1 + exp(-fabs(sig_input))) - 0.5)
      steer_torque = (sig * b) + (lateral_acceleration * c)
      return float(steer_torque)

    lataccel_values = np.arange(-8.0, 8.0, 0.01)
    torque_values = [torque_from_lateral_accel_siglin_func(x) for x in lataccel_values]
    print(torque_values)
    assert min(torque_values) < -1 and max(torque_values) > 1, "The torque values should cover the range [-1, 1]"
    return torque_values, lataccel_values

  def torque_from_lateral_accel(self) -> TorqueFromLateralAccelCallbackType:
    if self.CP.carFingerprint in NON_LINEAR_TORQUE_PARAMS:
      torque_values, lataccel_values = self.get_lataccel_torque_siglin()

      def torque_from_lateral_accel_siglin(lateral_acceleration: float, torque_params: structs.CarParams.LateralTorqueTuning):
        return np.interp(lateral_acceleration, lataccel_values, torque_values)
      return torque_from_lateral_accel_siglin
    else:
      return self.torque_from_lateral_accel_linear

  def lateral_accel_from_torque(self) -> LateralAccelFromTorqueCallbackType:
    if self.CP.carFingerprint in NON_LINEAR_TORQUE_PARAMS:
      torque_values, lataccel_values = self.get_lataccel_torque_siglin()

      def lateral_accel_from_torque_siglin(torque: float, torque_params: structs.CarParams.LateralTorqueTuning):
        return np.interp(torque, torque_values, lataccel_values)
      return lateral_accel_from_torque_siglin
    else:
      return self.lateral_accel_from_torque_linear


  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "mazda"
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.mazda)]
    p = Params()

    ret.radarUnavailable = True
    ret.deprecated.enableBsm = True

    ret.dashcamOnly = False

    ret.steerLimitTimer = 0.8

    CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    if candidate not in (CAR.MAZDA_CX5_2022, CAR.MAZDA_3_2019, CAR.MAZDA_CX_30, CAR.MAZDA_CX_50, CAR.MAZDA_3_2023, CAR.MAZDA_CX_30_2023):
      ret.minSteerSpeed = LKAS_LIMITS.DISABLE_SPEED * CV.KPH_TO_MS

    if p.get_bool("ManualTransmission"):
      ret.flags |= MazdaSafetyFlags.MANUAL_TRANSMISSION.value
      ret.transmissionType = structs.CarParams.TransmissionType.manual
    else:
      ret.transmissionType = structs.CarParams.TransmissionType.automatic

    if candidate in GEN1:
      ret.steerActuatorDelay = 0.335
      ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.GEN1.value
      if p.get_bool("TorqueInterceptorEnabled"): # Torque Interceptor Installed
        ret.flags |= MazdaSafetyFlags.TORQUE_INTERCEPTOR.value
        ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.TORQUE_INTERCEPTOR.value
        ret.minSteerSpeed = 0.0
        ret.steerAtStandstill = True
      if p.get_bool("RadarInterceptorEnabled"): # Radar Interceptor Installed
        ret.flags |= MazdaSafetyFlags.RADAR_INTERCEPTOR.value
        ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.RADAR_INTERCEPTOR.value
        ret.alphaLongitudinalAvailable = alpha_long
        ret.openpilotLongitudinalControl = True
        ret.radarUnavailable = False
        ret.startingState = True
        ret.longitudinalTuning.kpBP = [0., 5., 30.]
        ret.longitudinalTuning.kpV = [1.3, 1.0, 0.7]
        ret.longitudinalTuning.kiBP = [0., 5., 20., 30.]
        ret.longitudinalTuning.kiV = [0.36, 0.23, 0.17, 0.1]
      if p.get_bool("NoMRCC"): # No Mazda Radar Cruise Control; Missing CRZ_CTRL signal
        ret.flags |= MazdaSafetyFlags.NO_MRCC.value
        ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.NO_MRCC.value
      if p.get_bool("NoFSC"):  # No Front Sensing Camera
        ret.flags |= MazdaSafetyFlags.NO_FSC.value
        ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.NO_FSC.value

    if candidate in GEN2:
      ret.steerActuatorDelay = 0.5
      ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.GEN2.value
      ret.alphaLongitudinalAvailable = alpha_long
      ret.openpilotLongitudinalControl = True
      ret.stopAccel = -.5
      ret.vEgoStarting = .2
      ret.longitudinalActuatorDelay = 0.35 # gas is 0.25s and brake looks like 0.5
      ret.longitudinalTuning.kpBP = [0., 5., 35.]
      ret.longitudinalTuning.kpV = [0.0, 0.0, 0.0]
      ret.longitudinalTuning.kiBP = [0., 35.]
      ret.longitudinalTuning.kiV = [0.1, 0.1]
      ret.startingState = True
      ret.steerAtStandstill = True
      ret.deprecated.enableBsm = False

    if candidate in GEN3:
      ret.steerActuatorDelay = 0.5
      ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.GEN3.value
      ret.alphaLongitudinalAvailable = False
      ret.openpilotLongitudinalControl = False
      if p.get_bool("ManualTransmission"):
        ret.flags |= MazdaSafetyFlags.MANUAL_TRANSMISSION.value
      ret.deprecated.enableBsm = False

    ret.centerToFront = ret.wheelbase * 0.41

    return ret
