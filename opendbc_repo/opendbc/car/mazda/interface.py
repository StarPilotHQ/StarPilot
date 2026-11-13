#!/usr/bin/env python3
import math
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

# Opt-in steering gain for the sigmoid+linear torque table (Param MazdaSteerTorqueScale, default 1.0).
# The table converts the controller's whole lateral-acceleration command (feedforward + PID +
# friction) to torque, so this scales the whole controller, feedback included, not only the
# feedforward. It is an explicit per-car calibration, never a fingerprint default; validate the
# transient response on the car after changing it. Evidence that motivated it: a 2023 Mazda3 with
# a TI2 interceptor (drives 00000050-53) reached 67-89% of requested lateral acceleration in
# sustained highway curves and 45-65% at the 0.1-0.3 Hz weave, output unsaturated.
STEER_TORQUE_SCALE_PARAM = "MazdaSteerTorqueScale"
# 0.8 keeps every table covering the full [-1, 1] torque range (its assert); 1.5 caps the gain.
STEER_TORQUE_SCALE_MIN, STEER_TORQUE_SCALE_MAX = 0.8, 1.5


def steer_torque_scale(params=None) -> float:
  """The saved steering gain, or 1.0 when unset, unreadable, non-finite or out of range."""
  try:
    value = float((params or Params()).get(STEER_TORQUE_SCALE_PARAM, return_default=True))
  except Exception:
    return 1.0
  if not math.isfinite(value) or not STEER_TORQUE_SCALE_MIN <= value <= STEER_TORQUE_SCALE_MAX:
    return 1.0
  return value


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController

  def get_lataccel_torque_siglin(self) -> float:

    # Read once per interface: the forward and inverse tables must use the same gain.
    if getattr(self, '_steer_torque_scale', None) is None:
      self._steer_torque_scale = steer_torque_scale()
    scale = self._steer_torque_scale

    def torque_from_lateral_accel_siglin_func(lateral_acceleration: float) -> float:
      # The "lat_accel vs torque" relationship is assumed to be the sum of "sigmoid + linear" curves
      # An important thing to consider is that the slope at 0 should be > 0 (ideally >1)
      # This has big effect on the stability about 0 (noise when going straight)
      non_linear_torque_params = NON_LINEAR_TORQUE_PARAMS.get(self.CP.carFingerprint)
      assert non_linear_torque_params, "The params are not defined"
      a, b, c, _ = non_linear_torque_params
      sig_input = a * lateral_acceleration
      sig = np.sign(sig_input) * (1 / (1 + exp(-fabs(sig_input))) - 0.5)
      steer_torque = ((sig * b) + (lateral_acceleration * c)) * scale
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
        ret.deprecated.startingState = True
        ret.longitudinalTuning.deprecated.kpBP = [0., 5., 30.]
        ret.longitudinalTuning.deprecated.kpV = [1.3, 1.0, 0.7]
        ret.longitudinalTuning.kiBP = [0., 5., 20., 30.]
        ret.longitudinalTuning.kiV = [0.36, 0.23, 0.17, 0.1]
      if p.get_bool("NoMRCC"): # No Mazda Radar Cruise Control; Missing CRZ_CTRL signal
        ret.flags |= MazdaSafetyFlags.NO_MRCC.value
        ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.NO_MRCC.value
      if p.get_bool("NoFSC"):  # No Front Sensing Camera
        ret.flags |= MazdaSafetyFlags.NO_FSC.value
        ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.NO_FSC.value
      # Pure-stock GEN1 keeps safetyParam 0 (Dom stock-AOL convention); only
      # interceptor/variant GEN1 configurations carry the GEN1 bit.
      if ret.safetyConfigs[0].safetyParam & (MazdaSafetyFlags.TORQUE_INTERCEPTOR | MazdaSafetyFlags.RADAR_INTERCEPTOR |
                                             MazdaSafetyFlags.NO_MRCC | MazdaSafetyFlags.NO_FSC):
        ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.GEN1.value

    if candidate in GEN2:
      ret.steerActuatorDelay = 0.5
      ret.safetyConfigs[0].safetyParam |= MazdaSafetyFlags.GEN2.value
      ret.alphaLongitudinalAvailable = alpha_long
      ret.openpilotLongitudinalControl = True
      ret.stopAccel = -.5
      ret.deprecated.vEgoStarting = .2
      ret.longitudinalActuatorDelay = 0.35 # gas is 0.25s and brake looks like 0.5
      ret.longitudinalTuning.deprecated.kpBP = [0., 5., 35.]
      ret.longitudinalTuning.deprecated.kpV = [0.0, 0.0, 0.0]
      ret.longitudinalTuning.kiBP = [0., 35.]
      ret.longitudinalTuning.kiV = [0.1, 0.1]
      ret.deprecated.startingState = True
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

    # centerToFront comes from the platform CarSpecs ratio via CarInterfaceBase.get_params;
    # do not hardcode a single ratio for all Mazda platforms.

    return ret
