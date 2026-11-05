#!/usr/bin/env python3
from math import fabs, exp
import numpy as np

from opendbc.car import Bus, get_safety_config, structs
from opendbc.car.pedal import supported_pedal_detected
from opendbc.car.gm.pedal_capability import pedal_candidate
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm.radar_interface import RadarInterface, RADAR_HEADER_MSG, CAMERA_DATA_HEADER_MSG
from opendbc.car.gm.values import (volt_cc_pedal_profile, CAR, CarControllerParams, EV_CAR, CAMERA_ACC_CAR, SDGM_CAR, ALT_ACCS,
                                   CanBus, GMSafetyFlags, GMFlags, PEDAL_BOLT_CAR, NO_ACC_BOLT_CAR, ASCM_INTERCEPT_CAR,
                                   SDGM_STOCK_CAR, SDGM_CANCEL_PT_CAR, ORDINARY_SDGM_CAR, CC_GATEWAY_STOCK_CAR,
                                   SILVERADO_CC_STOCK_SOURCES, MALIBU_CC_F1_SOURCES, MALIBU_CC_F1_WORD,
                                   ORDINARY_CC_CAR, ORDINARY_CC_WORD, SILVERADO_CC_PEDAL_WORDS,
                                   is_silverado_cc_pedal_profile, is_conventional_cc_pedal_profile,
                                   CAMERA_STOCK_CAR, ORDINARY_CAMERA_CAR, ORDINARY_CAMERA_ALPHA_CAR, camera_acc_pedal_profile,
                                       VOLT_BSM_CAR, VOLT_CC_F1_WORD, BOLT_CC_WORDS, is_bolt_cc_profile, BOLT_PEDAL_REMOVED_WORDS,
                                   is_bolt_pedal_removed_profile, is_bolt_present_no_acc_pedal_profile)
from opendbc.car.gm.values import (malibu_hybrid_profile, MALIBU_HYBRID_SOURCES,
                                   is_volt_sdgm_accepted_envelope, volt_sdgm_accepted_accel_max)
from opendbc.car.interfaces import CarInterfaceBase, TorqueFromLateralAccelCallbackType, LateralAccelFromTorqueCallbackType

TransmissionType = structs.CarParams.TransmissionType
NetworkLocation = structs.CarParams.NetworkLocation

NON_LINEAR_TORQUE_PARAMS = {
  CAR.CHEVROLET_BOLT_EUV: [2.6531724862969748, 1.0, 0.1919764879840985, 0.009054123646805178],
  CAR.GMC_ACADIA: [4.78003305, 1.0, 0.3122, 0.05591772],
  CAR.CHEVROLET_SILVERADO: [3.29974374, 1.0, 0.25571356, 0.0465122]
}


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  DRIVABLE_GEARS = (structs.CarState.GearShifter.sport, structs.CarState.GearShifter.low,
                    structs.CarState.GearShifter.eco, structs.CarState.GearShifter.manumatic)

  @classmethod
  def get_params(cls, candidate, fingerprint, car_fw, alpha_long, is_release, docs, *, auto_pedal=True):
    selected = pedal_candidate(candidate, fingerprint) if auto_pedal else candidate
    return super().get_params(selected, fingerprint, car_fw, alpha_long, is_release, docs)

  def apply(self, c, now_nanos=None, *, startup_keepalive=False):
    if startup_keepalive:
      return self.CC.update(c, self.CS, now_nanos, startup_keepalive=True)
    return super().apply(c, now_nanos)

  def update(self, can_packets):
    if self.CC.bolt_steering_ack_counter:
      self.CC.observe_steering_receipts(can_packets)
      self.CS.bolt_steering_pending = self.CC.bolt_steering_pending
    self.CS.steering_authority.observe(can_packets)
    if malibu_hybrid_profile(self.CP) is not None:
      self.CS.hybrid_buttons.observe_packets(can_packets)
    if (is_bolt_pedal_removed_profile(self.CP) or is_bolt_pedal_removed_profile(self.CP, stock_only=True)):
      self.CS.conventional_cancel_credit.observe(
        can_packets, clear_on_main_off=True,
        clear_on_driver_override=is_bolt_pedal_removed_profile(self.CP, stock_only=True))
    if (is_bolt_present_no_acc_pedal_profile(self.CP) or is_bolt_present_no_acc_pedal_profile(self.CP, stock_only=True)):
      self.CS.conventional_cancel_credit.observe(can_packets, clear_on_main_off=True, observe_gear=True)
    if volt_cc_pedal_profile(self.CP) is not None:
      self.CS.conventional_cancel_credit.observe(can_packets)
    if is_conventional_cc_pedal_profile(self.CP):
      self.CS.conventional_cancel_credit.observe(can_packets)
    if not is_bolt_cc_profile(self.CP):
      ret = super().update(can_packets)
      self.CS.steering_authority.update(ret, self.can_parsers[Bus.pt]._last_update_nanos)
      return ret
    adaptive = (self.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and
                self.CP.safetyConfigs[0].safetyParam == 0xC140)
    camera_source = getattr(self, "bolt_cc_camera_observed", (0, b""))
    camera_taint = getattr(self, "bolt_cc_camera_taint", 0)
    if adaptive:
      malformed = any(bus == 2 and address == 0x370 and (stamp <= 0 or len(raw) != 6)
                      for stamp, packets in can_packets for address, raw, bus in packets)
      if malformed:
        camera_source = (0, b"")
        camera_taint = max((stamp for stamp, packets in can_packets
                            if any(bus == 2 and address == 0x370 for address, raw, bus in packets)), default=0)
      filtered_camera = []
      for stamp, packets in can_packets:
        accepted = []
        for address, raw, bus in packets:
          if bus == 2 and address == 0x370:
            if malformed or stamp <= camera_taint:
              continue
            if stamp > camera_source[0]:
              camera_source = (stamp, bytes(raw))
              camera_taint = 0
          accepted.append((address, raw, bus))
        filtered_camera.append((stamp, accepted))
      can_packets = filtered_camera
      self.bolt_cc_camera_observed = camera_source
      self.bolt_cc_camera_taint = camera_taint
    lengths = {0x184: 8, 0x3D1: 8, 0x1E1: 7, 0xC9: 8, 0x1C4: 8, 0x1F5: 8, 0x34A: 5, 0xBD: 7}
    observed = getattr(self, "bolt_cc_observed", {})
    tainted = getattr(self, "bolt_cc_tainted", {})
    invalid = {address for stamp, packets in can_packets for address, raw, bus in packets
               if bus == 0 and address in lengths and (stamp <= 0 or len(raw) != lengths[address])}
    # A malformed selected source poisons its entire receive burst, even if
    # a later packet in the same update could be repacked into valid fields.
    for address in invalid:
      observed.pop(address, None)
      tainted[address] = max((stamp for stamp, packets in can_packets
                              if any(bus == 0 and observed_address == address for observed_address, raw, bus in packets)), default=0)
    filtered = []
    for stamp, packets in can_packets:
      accepted = []
      for address, raw, bus in packets:
        if bus == 0 and address in lengths:
          if address in invalid:
            continue
          if address in tainted:
            if stamp <= tainted[address]:
              continue
            tainted.pop(address)
          previous = observed.get(address)
          if previous is None or stamp > previous[0]:
            observed[address] = (stamp, bytes(raw))
        accepted.append((address, raw, bus))
      filtered.append((stamp, accepted))
    self.bolt_cc_observed = observed
    self.bolt_cc_tainted = tainted
    ret = super().update(filtered)
    self.CS.bolt_cc_sources = tuple(observed.get(address, (0, b"")) for address in
                                     (0x184, 0x3D1, 0x1E1, 0xC9, 0x1C4, 0x1F5, 0x34A))
    self.CS.bolt_cc_camera_source = camera_source
    if (adaptive and (camera_taint or camera_source[0] <= 0)) or tainted or any(stamp <= 0 for stamp, raw in self.CS.bolt_cc_sources):
      ret.canValid = False
    return ret

  @staticmethod
  def get_pid_accel_limits(CP, current_speed, cruise_speed):
    if is_volt_sdgm_accepted_envelope(CP):
      return CarControllerParams.ACCEL_MIN, volt_sdgm_accepted_accel_max(CP, current_speed)
    profile = camera_acc_pedal_profile(CP)
    if profile is not None and profile.longitudinal and profile.topology in ("gateway", "ascm", "sdgm"):
      return CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX
    if (profile is not None and profile.longitudinal) or (volt_cc_pedal_profile(CP) is not None and CP.openpilotLongitudinalControl):
      return (float(np.interp(current_speed, [0., 1.5, 4., 8., 15., 30.], [-.95, -1.3, -1.85, -2.3, -2.6, -2.8])),
              float(np.interp(current_speed, [0., 1.5, 4., 8., 15.], [.60, .85, 1.15, 1.60, 2.])))
    hybrid = malibu_hybrid_profile(CP)
    if is_silverado_cc_pedal_profile(CP) or hybrid is not None and hybrid.pedal and hybrid.longitudinal:
      return (float(np.interp(current_speed, [0., 1.5, 4., 8., 15., 30.], [-.95, -1.3, -1.85, -2.3, -2.6, -2.8])),
              float(np.interp(current_speed, [0., 1.5, 4., 8., 15.], [.60, .85, 1.15, 1.60, 2.])))
    if CP.flags & GMFlags.PEDAL_LONG.value:
      if CP.carFingerprint in PEDAL_BOLT_CAR:
        ceiling = float(np.interp(current_speed, [0., 1.5, 4., 8., 15.],
                                 [0.54, 0.74, 1.03, 1.46, CarControllerParams.ACCEL_MAX]))
        if CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL:
          return CarControllerParams.ACCEL_MIN, ceiling
        floor = float(np.interp(current_speed, [0., 1.5, 4., 8., 15., 30.],
                                [-0.93, -1.28, -1.98, -2.58, -2.86, -2.95]))
        return floor, ceiling
      floor = float(np.interp(current_speed, [0., 1.5, 4., 8., 15., 30.],
                              [-0.93, -1.28, -1.98, -2.58, -2.86, -2.95]))
      return floor, CarControllerParams.ACCEL_MAX
    return CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX

  # Determined by iteratively plotting and minimizing error for f(angle, speed) = steer.
  @staticmethod
  def get_steer_feedforward_volt(desired_angle, v_ego):
    desired_angle *= 0.02904609
    sigmoid = desired_angle / (1 + fabs(desired_angle))
    return 0.10006696 * sigmoid * (v_ego + 3.12485927)

  def get_steer_feedforward_function(self):
    if self.CP.carFingerprint in (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_CC):
      return self.get_steer_feedforward_volt
    else:
      return CarInterfaceBase.get_steer_feedforward_default

  def get_lataccel_torque_siglin(self) -> tuple[list[float], np.ndarray]:

    def torque_from_lateral_accel_siglin_func(lateral_acceleration: float) -> float:
      # The "lat_accel vs torque" relationship is assumed to be the sum of "sigmoid + linear" curves
      # An important thing to consider is that the slope at 0 should be > 0 (ideally >1)
      # This has big effect on the stability about 0 (noise when going straight)
      non_linear_torque_params = NON_LINEAR_TORQUE_PARAMS.get(self.CP.carFingerprint)
      assert non_linear_torque_params, "The params are not defined"
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
    ret.brand = "gm"
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.gm)]
    ret.autoResumeSng = False
    if 0x142 in fingerprint[CanBus.POWERTRAIN] or candidate in VOLT_BSM_CAR:
      ret.flags |= GMFlags.HAS_BSM.value

    if candidate in EV_CAR:
      ret.transmissionType = TransmissionType.direct
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.EV.value
    else:
      ret.transmissionType = TransmissionType.automatic

    ret.longitudinalTuning.kiBP = [5., 35.]
    ret.longitudinalActuatorDelay = 0.5

    if candidate == CAR.CHEVROLET_VOLT_CC:
      # Require the complete source layout for either automatic or manual identity.
      pt = fingerprint[CanBus.POWERTRAIN]
      f1 = 0xBE not in pt and pt.get(0xF1) == 6
      required = {0xF1 if f1 else 0xBE: 6, 0x3d1: 8, 0xc9: 8, 0x1e1: 7, 0x1f5: 8, 0x34a: 5, 0x1c4: 8, 0xbd: 7}
      if f1:
        # Admit the recorded no-ACC source graph, never a broken pedal/camera graph.
        required.update({0x184: 8, 0xD3: 2, 0x130: 1, 0x17D: 6, 0x210: 4, 0x236: 5})
      f1_layout = (not any(address in pt for address in (0x201, 0x2FF, 0x320)) and
                   not any(address in fingerprint[CanBus.CAMERA] for address in (0x180, 0x320, 0x370)) and
                   not fingerprint[CanBus.OBSTACLE])
      admitted = (not is_release and (not f1 or f1_layout) and
                  all(fingerprint[CanBus.POWERTRAIN].get(addr) == size for addr, size in required.items()))
      ret.networkLocation = NetworkLocation.gateway
      ret.radarUnavailable = True
      ret.alphaLongitudinalAvailable = False
      ret.openpilotLongitudinalControl = admitted
      ret.pcmCruise = not admitted
      ret.flags = int(GMFlags.CC_LONG | (GMFlags.NO_ACCELERATOR_POS_MSG if f1 else 0)) if admitted else 0
      ret.minEnableSpeed = 24 * CV.MPH_TO_MS
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS
      ret.longitudinalActuatorDelay = 1.0
      ret.longitudinalTuning.kiBP, ret.longitudinalTuning.kiV = [0.], [0.1]
      ret.stopAccel = -1.5
      if not admitted:
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.noOutput)]
      else:
        ret.safetyConfigs[0].safetyParam = VOLT_CC_F1_WORD if f1 else int(GMSafetyFlags.EV | GMSafetyFlags.NO_ACC)

    elif candidate in CC_GATEWAY_STOCK_CAR:
      # Start with stock PCM ownership; qualified button-control owners override below.
      ret.networkLocation = NetworkLocation.gateway
      ret.radarUnavailable = True
      ret.pcmCruise = True
      ret.alphaLongitudinalAvailable = False
      ret.openpilotLongitudinalControl = False
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.NO_ACC.value
      ret.minEnableSpeed = -1.
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS
      ret.longitudinalTuning.kiV = [2.4, 1.5]
      if candidate in ORDINARY_CC_CAR:
        ret.openpilotLongitudinalControl = True
        ret.pcmCruise = False
        ret.flags = int(GMFlags.CC_LONG)
        ret.safetyConfigs[0].safetyParam = ORDINARY_CC_WORD
        ret.minEnableSpeed = 24 * CV.MPH_TO_MS
        ret.longitudinalActuatorDelay = 1.0
        ret.longitudinalTuning.kiBP = [0.0]
        ret.longitudinalTuning.kiV = [0.1]
        ret.stopAccel = -1.5 if candidate == CAR.CHEVROLET_MALIBU_CC else -2.0

    elif candidate in ASCM_INTERCEPT_CAR:
      has_sascm = 0x2ff in fingerprint[CanBus.POWERTRAIN]
      has_accelerator_pos = 0xbe in fingerprint[CanBus.POWERTRAIN]
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE]
      ret.pcmCruise = True
      ret.minEnableSpeed = 5 * CV.KPH_TO_MS
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS
      ret.alphaLongitudinalAvailable = has_sascm and not is_release
      ret.openpilotLongitudinalControl = ret.alphaLongitudinalAvailable and alpha_long
      ret.pcmCruise = not ret.openpilotLongitudinalControl
      ret.safetyConfigs[0].safetyParam |= (GMSafetyFlags.HW_CAM | GMSafetyFlags.ASCM_INTERCEPT).value
      if not has_accelerator_pos:
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.ASCM_BRAKE_C9.value
      if not ret.radarUnavailable:
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.ASCM_RADAR.value
      if ret.openpilotLongitudinalControl:
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM_LONG.value
        if candidate == CAR.CHEVROLET_VOLT_ASCM:
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.VOLT_LONG.value
      ret.longitudinalTuning.kiV = [0.5, 0.5]
      ret.stopAccel = -0.25

    elif candidate == CAR.CHEVROLET_VOLT_2019:
      has_sascm = 0x2ff in fingerprint[CanBus.POWERTRAIN]
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE]
      ret.alphaLongitudinalAvailable = has_sascm and not is_release
      ret.openpilotLongitudinalControl = ret.alphaLongitudinalAvailable and alpha_long
      ret.pcmCruise = not ret.openpilotLongitudinalControl
      ret.safetyConfigs[0].safetyParam |= (GMSafetyFlags.HW_CAM | GMSafetyFlags.SDGM).value
      if 0xbe not in fingerprint[CanBus.POWERTRAIN]:
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.BRAKE_C9.value
      if ret.openpilotLongitudinalControl:
        ret.safetyConfigs[0].safetyParam |= (GMSafetyFlags.HW_CAM_LONG | GMSafetyFlags.VOLT_LONG).value
      ret.minEnableSpeed = -1.
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS
      ret.longitudinalTuning.kiV = [0.5, 0.5]
      ret.stopAccel = -0.25

    elif candidate in (CAMERA_ACC_CAR | SDGM_CAR):
      ret.alphaLongitudinalAvailable = (candidate not in (SDGM_CAR | CAMERA_STOCK_CAR) and
                                        (candidate not in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023) or not is_release))
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.radarUnavailable = True  # no radar
      ret.pcmCruise = True
      ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM.value
      if candidate in SDGM_STOCK_CAR:
        # Manual identity only: these fingerprints collide with existing GM platforms.
        # The observed SDGM path keeps longitudinal control with stock ACC.
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.SDGM.value
        if candidate in SDGM_CANCEL_PT_CAR:
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.SDGM_CANCEL_PT.value
        if 0xbe not in fingerprint[CanBus.POWERTRAIN]:
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.BRAKE_C9.value
        ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and not docs
      if candidate in ORDINARY_SDGM_CAR:
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.SDGM.value
        if candidate in SDGM_CANCEL_PT_CAR:
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.SDGM_CANCEL_PT.value
        if 0xbe not in fingerprint[CanBus.POWERTRAIN]:
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.BRAKE_C9.value
        ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and not docs
        ret.alphaLongitudinalAvailable = 0x2ff in fingerprint[CanBus.POWERTRAIN] and not is_release
        ret.longitudinalTuning.kiBP = [5.0, 35.0]
        ret.longitudinalTuning.kiV = [0.5, 0.5]
        ret.stopAccel = -0.25
        if alpha_long and ret.alphaLongitudinalAvailable:
          ret.openpilotLongitudinalControl = True
          ret.pcmCruise = False
          ret.safetyConfigs[0].safetyParam &= ~GMSafetyFlags.SDGM_CANCEL_PT.value
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM_LONG.value

      ret.minEnableSpeed = -1 if candidate in SDGM_CAR else 5 * CV.KPH_TO_MS
      ret.minSteerSpeed = 10 * CV.KPH_TO_MS
      if candidate in ORDINARY_SDGM_CAR:
        ret.minSteerSpeed = 7 * CV.MPH_TO_MS

      # Tuning for experimental long
      if candidate not in ORDINARY_SDGM_CAR:
        ret.longitudinalTuning.kiV = [2.0, 1.5]

      if alpha_long and ret.alphaLongitudinalAvailable and candidate not in (SDGM_STOCK_CAR | CAMERA_STOCK_CAR):
        ret.pcmCruise = False
        ret.openpilotLongitudinalControl = True
        ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.HW_CAM_LONG.value

      if (candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023 or
          candidate == CAR.CHEVROLET_BOLT_EUV and ret.openpilotLongitudinalControl):
        ret.longitudinalTuning.kiBP = [5.0, 35.0, 60.0]
        ret.longitudinalTuning.kiV = [0.5, 0.5, 0.5]
        ret.stopAccel = -0.25

      if candidate in ALT_ACCS:
        ret.alphaLongitudinalAvailable = False
        ret.openpilotLongitudinalControl = False
        ret.minEnableSpeed = -1.  # engage speed is decided by PCM

      if candidate in CAMERA_STOCK_CAR:
        # These explicit camera identities keep ACC with the OEM in both builds.
        ret.longitudinalTuning.kiBP = [5., 35.] if candidate == CAR.CHEVROLET_VOLT_CAMERA else [5., 35., 60.]
        ret.longitudinalTuning.kiV = [0.5, 0.5] if candidate == CAR.CHEVROLET_VOLT_CAMERA else [0.5, 0.5, 0.5]
        if candidate == CAR.CHEVROLET_VOLT_CAMERA:
          ret.minEnableSpeed = -1.
          ret.minSteerSpeed = 7 * CV.MPH_TO_MS

      if candidate in PEDAL_BOLT_CAR:
        ret.openpilotLongitudinalControl = False
        ret.safetyConfigs[0].safetyParam &= ~GMSafetyFlags.HW_CAM_LONG.value
        pedal_long = supported_pedal_detected(fingerprint, CanBus.POWERTRAIN, supported=True)
        if candidate in NO_ACC_BOLT_CAR:
          ret.pcmCruise = False
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.NO_ACC.value
        if candidate == CAR.CHEVROLET_BOLT_CC_2017:
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.BOLT_2017.value
        if candidate in (CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL):
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.BOLT_GEN2.value
        if pedal_long:
          ret.flags |= GMFlags.PEDAL_LONG.value
          ret.openpilotLongitudinalControl = True
          ret.pcmCruise = False
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.PEDAL_LONG.value
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.PADDLE_SCHED.value
          if candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL:
            ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.BOLT_ACC_PEDAL.value
          ret.longitudinalTuning.kiBP = [0., 3., 6., 35.]
          ret.longitudinalTuning.kiV = [0.07, 0.10, 0.15, 0.24]
          ret.longitudinalActuatorDelay = 0.6
          ret.stopAccel = -0.25
        elif candidate in NO_ACC_BOLT_CAR:
          # Ordinary cruise permits lateral control, but has no stock ACC.
          ret.openpilotLongitudinalControl = False
        ret.alphaLongitudinalAvailable = False
        ret.radarUnavailable = True
        ret.minEnableSpeed = -1.

    else:  # ASCM, OBD-II harness
      ret.openpilotLongitudinalControl = True
      ret.networkLocation = NetworkLocation.gateway
      # LRR messages can take up to a few seconds to start sending after ignition, check camera data as well which starts earlier
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and CAMERA_DATA_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and not docs
      ret.pcmCruise = False  # stock non-adaptive cruise control is kept off
      # supports stop and go, but initial engage must (conservatively) be above 18mph
      ret.minEnableSpeed = 18 * CV.MPH_TO_MS
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS

      # Tuning
      ret.longitudinalTuning.kiV = [2.4, 1.5]
      if candidate in ORDINARY_CC_CAR:
        ret.openpilotLongitudinalControl = True
        ret.pcmCruise = False
        ret.flags = int(GMFlags.CC_LONG)
        ret.safetyConfigs[0].safetyParam = ORDINARY_CC_WORD
        ret.minEnableSpeed = 24 * CV.MPH_TO_MS
        ret.longitudinalActuatorDelay = 1.0
        ret.longitudinalTuning.kiBP = [0.0]
        ret.longitudinalTuning.kiV = [0.1]
        ret.stopAccel = -1.5 if candidate == CAR.CHEVROLET_MALIBU_CC else -2.0
      if candidate == CAR.CHEVROLET_VOLT:
        ret.minEnableSpeed = -1.
        ret.longitudinalTuning.kiV = [0.5, 0.5]
        ret.stopAccel = -1.5
        if not ret.radarUnavailable:
          ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.VOLT_GATEWAY_LONG.value
          if 0xbe not in fingerprint[CanBus.POWERTRAIN]:
            ret.safetyConfigs[0].safetyParam |= GMSafetyFlags.VOLT_GATEWAY_ALT_BRAKE.value

    flashed_volt = False
    if candidate == CAR.CHEVROLET_VOLT and ret.radarUnavailable and not docs:
      pt, camera = fingerprint[CanBus.POWERTRAIN], fingerprint[CanBus.CAMERA]
      required = {0x184: 8, 0x34A: 5, 0x348: 5, 0x1C4: 8, 0xC9: 8, 0x1E1: 7,
                  0x1F5: 8, 0xBD: 7, 0xF1: 6, 0x232: 8}
      flashed_volt = (not is_release and all(pt.get(a) == length for a, length in required.items()) and
                      not any(a in pt for a in (0xBE, 0x201, 0x2FF, 0x320)) and
                      not any(a in camera for a in (0x180, 0x320, 0x370)) and not fingerprint[CanBus.OBSTACLE])
      if flashed_volt:
        # Reuse the plain OBD/F1 wire protocol; this is not stock cruise or a camera owner.
        ret.flags = int(ret.flags) & int(GMFlags.HAS_BSM)
        ret.alphaLongitudinalAvailable = True
        ret.openpilotLongitudinalControl = bool(alpha_long)
        ret.pcmCruise = False
        ret.safetyConfigs[0].safetyParam = int(GMSafetyFlags.EV | GMSafetyFlags.VOLT_GATEWAY_LONG |
                                             GMSafetyFlags.VOLT_GATEWAY_ALT_BRAKE)

    # These cars have been put into dashcam only due to both a lack of users and test coverage.
    # These cars likely still work fine. Once a user confirms each car works and a test route is
    # added to opendbc/car/tests/routes.py, we can remove it from this list.
    ret.dashcamOnly = candidate in {CAR.CADILLAC_ATS, CAR.HOLDEN_ASTRA, CAR.CHEVROLET_MALIBU, CAR.BUICK_REGAL} or \
                      (ret.networkLocation == NetworkLocation.gateway and ret.radarUnavailable and not flashed_volt)

    # Start with a baseline tuning for all GM vehicles. Override tuning as needed in each model section below.
    ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
    ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.2], [0.00]]
    ret.lateralTuning.pid.kf = 0.00004   # full torque for 20 deg at 80mph means 0.00007818594
    ret.steerActuatorDelay = 0.1  # Default delay, not measured yet

    ret.steerLimitTimer = 0.4

    if candidate in (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_CC, CAR.CHEVROLET_MALIBU_ASCM):
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)
      ret.steerActuatorDelay = 0.2

    elif candidate == CAR.GMC_ACADIA:
      ret.minEnableSpeed = -1.  # engage speed is decided by pcm
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.BUICK_LACROSSE, CAR.BUICK_LACROSSE_ASCM, CAR.BUICK_LACROSSE_ASCM_19US):
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)
      if candidate == CAR.BUICK_LACROSSE_ASCM_19US:
        ret.minSteerSpeed = 28 * CV.MPH_TO_MS

    elif candidate in (CAR.CADILLAC_ESCALADE, CAR.CADILLAC_ESCALADE_ASCM):
      if candidate == CAR.CADILLAC_ESCALADE:
        ret.minEnableSpeed = -1.  # engage speed is decided by pcm
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.CADILLAC_ESCALADE_ESV, CAR.CADILLAC_ESCALADE_ESV_2019, CAR.CADILLAC_ESCALADE_ESV_2019_ASCM):
      ret.minEnableSpeed = -1.  # engage speed is decided by pcm

      if candidate == CAR.CADILLAC_ESCALADE_ESV:
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[10., 41.0], [10., 41.0]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.13, 0.24], [0.01, 0.02]]
        ret.lateralTuning.pid.kf = 0.000045
      else:
        ret.steerActuatorDelay = 0.2
        CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023) or candidate in PEDAL_BOLT_CAR:
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CHEVROLET_SILVERADO:
      # On the Bolt, the ECM and camera independently check that you are either above 5 kph or at a stop
      # with foot on brake to allow engagement, but this platform only has that check in the camera.
      # TODO: check if this is split by EV/ICE with more platforms in the future
      if ret.openpilotLongitudinalControl:
        ret.minEnableSpeed = -1.
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CHEVROLET_EQUINOX:
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CHEVROLET_TRAILBLAZER:
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CADILLAC_XT4:
      ret.steerActuatorDelay = 0.2
      ret.minSteerSpeed = 30 * CV.MPH_TO_MS
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CHEVROLET_VOLT_2019:
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CHEVROLET_TRAVERSE:
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in SDGM_STOCK_CAR:
      ret.steerActuatorDelay = 0.2
      if candidate == CAR.CHEVROLET_BLAZER:
        ret.minEnableSpeed = 5 * CV.KPH_TO_MS
        ret.longitudinalActuatorDelay = 0.7
        ret.longitudinalTuning.kiBP = [0.0, 4.0, 12.0, 35.0]
        ret.longitudinalTuning.kiV = [0.03, 0.04, 0.055, 0.07]
        ret.stopAccel = -0.30
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.GMC_YUKON:
      ret.steerActuatorDelay = 0.5
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)
      ret.dashcamOnly = True  # Needs steerRatio, tireStiffness, and lat accel factor tuning

    elif candidate in (CAR.CHEVROLET_SUBURBAN, CAR.CHEVROLET_SUBURBAN_CAMERA, CAR.CHEVROLET_SUBURBAN_ASCM):
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CHEVROLET_VOLT_CAMERA:
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CHEVROLET_TRAX:
      ret.steerActuatorDelay = 0.46
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in CC_GATEWAY_STOCK_CAR:
      if candidate == CAR.CADILLAC_XT4_CC:
        ret.minSteerSpeed = 30 * CV.MPH_TO_MS
      if candidate not in (CAR.CADILLAC_CT6_CC, CAR.CHEVROLET_EQUINOX_CC, CAR.CHEVROLET_SILVERADO_CC):
        ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)
      ret.dashcamOnly = candidate not in ORDINARY_CC_CAR
      if candidate == CAR.CHEVROLET_SILVERADO_CC:
        pt = fingerprint.get(CanBus.POWERTRAIN, {})
        cam = fingerprint.get(CanBus.CAMERA, {})
        cc_long = (all(pt.get(address) == (8 if address == 0x17D else length)
                       for address, length in SILVERADO_CC_STOCK_SOURCES.items()) and
                   pt.get(0xBE) == 6 and 0x201 not in pt and
                   all(cam.get(address) == length for address, length in ((0x180, 4), (0x320, 6), (0x370, 6))) and
                   RADAR_HEADER_MSG not in fingerprint.get(CanBus.OBSTACLE, {}) and
                   CAMERA_DATA_HEADER_MSG not in fingerprint.get(CanBus.OBSTACLE, {}) and not docs)
        ret.dashcamOnly = not (cc_long or (
          all(pt.get(address) == length for address, length in SILVERADO_CC_STOCK_SOURCES.items()) and
          pt.get(0xBE) in (6, 7, 8) and 0x201 not in pt and
          0x320 not in fingerprint.get(CanBus.CAMERA, {}) and
          RADAR_HEADER_MSG not in fingerprint.get(CanBus.OBSTACLE, {}) and
          CAMERA_DATA_HEADER_MSG not in fingerprint.get(CanBus.OBSTACLE, {}) and not docs))
        if cc_long:
          # Retain StarPilot's conventional-cruise button owner for this complete source layout.
          ret.openpilotLongitudinalControl, ret.pcmCruise = True, False
          ret.flags |= int(GMFlags.CC_LONG)
          ret.safetyConfigs[0].safetyParam = ORDINARY_CC_WORD
          ret.minEnableSpeed = 24 * CV.MPH_TO_MS
          ret.longitudinalActuatorDelay = 1.
          ret.longitudinalTuning.kiBP, ret.longitudinalTuning.kiV = [0.], [.1]
          ret.stopAccel = -2.

    if candidate in CAMERA_STOCK_CAR:
      ret.dashcamOnly = True  # Require a recognized camera layout for manual identities.
    if candidate == CAR.CHEVROLET_VOLT_CAMERA and fingerprint[CanBus.CAMERA].get(0x320) == 6:
      # The camera-present layout is identified by its observed AEB message.
      ret.dashcamOnly = False
      ret.alphaLongitudinalAvailable = not is_release
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE]
      if alpha_long and not is_release:
        ret.openpilotLongitudinalControl = True
        ret.pcmCruise = False
        ret.safetyConfigs[0].safetyParam = int(GMSafetyFlags.EV | GMSafetyFlags.HW_CAM |
                                             GMSafetyFlags.HW_CAM_LONG | GMSafetyFlags.VOLT_LONG)
        ret.longitudinalTuning.kiBP = [5., 35.]
        ret.longitudinalTuning.kiV = [0.5, 0.5]
        ret.stopAccel = -0.25

    if candidate == CAR.CHEVROLET_VOLT_CAMERA and 0x320 not in fingerprint[CanBus.CAMERA]:
      pt = fingerprint[CanBus.POWERTRAIN]
      required = {0x184: 8, 0x34A: 5, 0x1C4: 8, 0xC9: 8, 0x1E1: 7}
      analog_present = pt.get(0xBE) == 6
      analog_alternate = 0xBE not in pt and pt.get(0xF1) == 6
      if all(pt.get(address) == length for address, length in required.items()) and (analog_present or analog_alternate):
        ret.flags = int(GMFlags.VOLT_CAMERA_REMOVED)
        if analog_alternate:
          ret.flags |= int(GMFlags.VOLT_CAMERA_NO_ACCEL_POS)
        ret.dashcamOnly = False
        ret.alphaLongitudinalAvailable = not is_release
        ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE]
        ret.openpilotLongitudinalControl = bool(alpha_long and not is_release)
        ret.pcmCruise = not ret.openpilotLongitudinalControl
        ret.safetyConfigs[0].safetyParam = 0xC151 if ret.openpilotLongitudinalControl else 0xC150
        if ret.openpilotLongitudinalControl:
          ret.longitudinalTuning.kiBP = [5., 35.]
          ret.longitudinalTuning.kiV = [0.5, 0.5]
          ret.stopAccel = -0.25

    if candidate in ORDINARY_CAMERA_CAR:
      camera_present = fingerprint.get(CanBus.CAMERA, {}).get(0x320) == 6
      f1_present = fingerprint.get(CanBus.POWERTRAIN, {}).get(0xF1) == 6
      be_length = fingerprint.get(CanBus.POWERTRAIN, {}).get(0xBE)
      source_present = f1_present and (be_length is None or be_length in (6, 7, 8))
      if be_length is None:
        ret.flags |= int(GMFlags.NO_ACCELERATOR_POS_MSG)
      camera_removed = 0x320 not in fingerprint.get(CanBus.CAMERA, {})
      required_pt = {0x184: 8, 0x34A: 5, 0x1C4: 8, 0xC9: 8, 0x1E1: 7}
      removed_sources = camera_removed and all(fingerprint.get(CanBus.POWERTRAIN, {}).get(a) == n
                                               for a, n in required_pt.items())
      layout_supported = camera_present or removed_sources
      if camera_removed:
        ret.flags |= int(GMFlags.NO_CAMERA)
      ret.dashcamOnly = not (layout_supported and source_present)
      ret.alphaLongitudinalAvailable = layout_supported and source_present and candidate in ORDINARY_CAMERA_ALPHA_CAR and not is_release
      ret.openpilotLongitudinalControl = bool(alpha_long and ret.alphaLongitudinalAvailable)
      ret.pcmCruise = not ret.openpilotLongitudinalControl
      ret.safetyConfigs[0].safetyParam = ((0xC173 if ret.openpilotLongitudinalControl else 0xC172) if camera_removed else
                                         (0xC170 if ret.openpilotLongitudinalControl else 0xC171))
      ret.minSteerSpeed = 10 * CV.KPH_TO_MS
      ret.minEnableSpeed = (0. if candidate == CAR.CHEVROLET_SILVERADO and ret.openpilotLongitudinalControl else
                            -1. if candidate in ALT_ACCS else 5 * CV.KPH_TO_MS)
      ret.longitudinalTuning.kiBP = [5., 35., 60.]
      ret.longitudinalTuning.kiV = [.5, .5, .5]
      ret.stopAccel = -.25
      if candidate == CAR.CHEVROLET_SILVERADO and ret.openpilotLongitudinalControl:
        ret.longitudinalTuning.kiBP = [0., 5., 15., 35.]
        ret.longitudinalTuning.kiV = [.20, .18, .13, .08]

    if candidate in ORDINARY_CAMERA_CAR and supported_pedal_detected(fingerprint, CanBus.POWERTRAIN, supported=True):
      pt = fingerprint.get(CanBus.POWERTRAIN, {})
      camera_length = fingerprint.get(CanBus.CAMERA, {}).get(0x320)
      removed = camera_length is None
      be = pt.get(0xBE) == 6
      f1 = 0xBE not in pt and pt.get(0xF1) == 6
      required = {0x184: 8, 0x34A: 5, 0x1E1: 7, 0x1C4: 8, 0xC9: 8}
      sources = all(pt.get(address) == length for address, length in required.items()) and (be or f1)
      ret.flags = int(GMFlags.PEDAL_LONG | (GMFlags.NO_CAMERA if removed else 0) |
                      (GMFlags.NO_ACCELERATOR_POS_MSG if f1 else 0))
      ret.dashcamOnly = not sources or camera_length not in (None, 6)
      ret.radarUnavailable = True
      ret.alphaLongitudinalAvailable = not ret.dashcamOnly and not is_release and pt.get(0x1F5) == 8
      ret.openpilotLongitudinalControl = ret.alphaLongitudinalAvailable
      ret.pcmCruise = not ret.openpilotLongitudinalControl
      words = {(False, False): (0xE100, 0xE110), (False, True): (0xE101, 0xE111),
               (True, False): (0xE102, 0xE112), (True, True): (0xE103, 0xE113)}
      ret.safetyConfigs[0].safetyParam = words[removed, f1][not ret.openpilotLongitudinalControl]
      ret.minEnableSpeed = -1. if ret.openpilotLongitudinalControl else (-1. if candidate in ALT_ACCS else 5 * CV.KPH_TO_MS)
      ret.autoResumeSng = ret.openpilotLongitudinalControl
      ret.longitudinalTuning.kiBP = [0., 3., 6., 35.]
      ret.longitudinalTuning.kiV = [.09, .13, .19, .28]
      ret.stopAccel = -.25

    if candidate == CAR.CHEVROLET_VOLT_2019 and supported_pedal_detected(fingerprint, CanBus.POWERTRAIN, supported=True):
      pt = fingerprint.get(CanBus.POWERTRAIN, {})
      be = pt.get(0xBE) in (6, 7, 8)
      f1 = 0xBE not in pt and pt.get(0xF1) == 6
      required = {0x184: 8, 0x34A: 5, 0x1E1: 7, 0x1C4: 8, 0xC9: 8, 0xBD: 7}
      sources = all(pt.get(address) == length for address, length in required.items()) and (be or f1)
      camera = fingerprint.get(CanBus.CAMERA, {})
      ret.flags = int(GMFlags.PEDAL_LONG | (GMFlags.NO_ACCELERATOR_POS_MSG if f1 else 0)) | (int(ret.flags) & int(GMFlags.HAS_BSM))
      ret.dashcamOnly = not sources or camera.get(0x180) != 4 or camera.get(0x370) != 6
      ret.alphaLongitudinalAvailable = has_sascm
      ret.openpilotLongitudinalControl = not ret.dashcamOnly and not is_release and pt.get(0x1F5) == 8
      ret.pcmCruise = False
      index = int(not ret.radarUnavailable) * 2 + int(f1)
      ret.safetyConfigs[0].safetyParam = (0xE500 if ret.openpilotLongitudinalControl else 0xE510) + index
      ret.minEnableSpeed = -1.
      ret.autoResumeSng = ret.openpilotLongitudinalControl
      ret.longitudinalTuning.kiBP, ret.longitudinalTuning.kiV = [5., 35.], [.5, .5]

    if candidate == CAR.CHEVROLET_VOLT_ASCM and supported_pedal_detected(fingerprint, CanBus.POWERTRAIN, supported=True):
      pt = fingerprint.get(CanBus.POWERTRAIN, {})
      be = pt.get(0xBE) in (6, 7, 8)
      f1 = 0xBE not in pt and pt.get(0xF1) == 6
      required = {0x184: 8, 0x34A: 5, 0x1E1: 7, 0x1C4: 8, 0xC9: 8, 0xBD: 7}
      sources = all(pt.get(address) == length for address, length in required.items()) and (be or f1)
      camera = fingerprint.get(CanBus.CAMERA, {})
      ret.flags = int(GMFlags.PEDAL_LONG | (GMFlags.NO_ACCELERATOR_POS_MSG if f1 else 0)) | (int(ret.flags) & int(GMFlags.HAS_BSM))
      ret.dashcamOnly = not sources or camera.get(0x180) != 4 or camera.get(0x370) != 6
      ret.alphaLongitudinalAvailable = has_sascm
      ret.openpilotLongitudinalControl = not ret.dashcamOnly and not is_release and pt.get(0x1F5) == 8
      ret.pcmCruise = False
      index = int(not ret.radarUnavailable) * 2 + int(f1)
      ret.safetyConfigs[0].safetyParam = (0xE400 if ret.openpilotLongitudinalControl else 0xE410) + index
      ret.minEnableSpeed = -1.
      ret.autoResumeSng = ret.openpilotLongitudinalControl
      ret.longitudinalTuning.kiBP, ret.longitudinalTuning.kiV = [5., 35.], [.5, .5]

    if candidate == CAR.CHEVROLET_VOLT and supported_pedal_detected(fingerprint, CanBus.POWERTRAIN, supported=True):
      pt = fingerprint.get(CanBus.POWERTRAIN, {})
      camera_length = fingerprint.get(CanBus.CAMERA, {}).get(0x320)
      removed = camera_length is None
      be = pt.get(0xBE) == 6
      f1 = 0xBE not in pt and pt.get(0xF1) == 6
      required = {0x184: 8, 0x34A: 5, 0x1E1: 7, 0x1C4: 8, 0xC9: 8, 0xBD: 7}
      sources = all(pt.get(address) == length for address, length in required.items()) and (be or f1)
      ret.flags = int(GMFlags.PEDAL_LONG | (GMFlags.NO_CAMERA if removed else 0) |
                      (GMFlags.NO_ACCELERATOR_POS_MSG if f1 else 0)) | (int(ret.flags) & int(GMFlags.HAS_BSM))
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.dashcamOnly = ret.dashcamOnly or not sources or camera_length not in (None, 6)
      ret.alphaLongitudinalAvailable = not ret.dashcamOnly and not is_release and pt.get(0x1F5) == 8
      ret.openpilotLongitudinalControl = ret.alphaLongitudinalAvailable
      ret.pcmCruise = False
      index = int(removed) * 2 + int(f1)
      ret.safetyConfigs[0].safetyParam = (0xE300 if ret.openpilotLongitudinalControl else 0xE310) + index
      ret.minEnableSpeed = -1.
      ret.autoResumeSng = ret.openpilotLongitudinalControl
      ret.longitudinalTuning.kiBP, ret.longitudinalTuning.kiV = [5., 35.], [.5, .5]

    if candidate == CAR.CHEVROLET_VOLT_CAMERA and supported_pedal_detected(fingerprint, CanBus.POWERTRAIN, supported=True):
      pt = fingerprint.get(CanBus.POWERTRAIN, {})
      camera_length = fingerprint.get(CanBus.CAMERA, {}).get(0x320)
      removed = camera_length is None
      be = pt.get(0xBE) == 6
      f1 = 0xBE not in pt and pt.get(0xF1) == 6
      required = {0x184: 8, 0x34A: 5, 0x1E1: 7, 0x1C4: 8, 0xC9: 8, 0xBD: 7}
      sources = all(pt.get(address) == length for address, length in required.items()) and (be or f1)
      ret.flags = int(GMFlags.PEDAL_LONG | (GMFlags.NO_CAMERA if removed else 0) |
                      (GMFlags.NO_ACCELERATOR_POS_MSG if f1 else 0))
      ret.dashcamOnly = not sources or camera_length not in (None, 6)
      ret.alphaLongitudinalAvailable = not ret.dashcamOnly and not is_release and pt.get(0x1F5) == 8
      ret.openpilotLongitudinalControl = ret.alphaLongitudinalAvailable
      ret.pcmCruise = not ret.openpilotLongitudinalControl
      index = int(removed) * 2 + int(f1)
      ret.safetyConfigs[0].safetyParam = (0xE200 if ret.openpilotLongitudinalControl else 0xE210) + index
      ret.minEnableSpeed = -1.
      ret.autoResumeSng = ret.openpilotLongitudinalControl
      ret.longitudinalTuning.kiBP = [0., 3., 6., 35.]
      ret.longitudinalTuning.kiV = [.09, .13, .19, .28]
      ret.stopAccel = -.25

    if candidate in (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_CAMERA,
                     CAR.CHEVROLET_VOLT_CC, CAR.CHEVROLET_VOLT_2019):
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS
    if candidate == CAR.CHEVROLET_VOLT_CC and supported_pedal_detected(fingerprint, CanBus.POWERTRAIN, supported=True):
      pt = fingerprint.get(CanBus.POWERTRAIN, {})
      cam = fingerprint.get(CanBus.CAMERA, {})
      be = pt.get(0xBE) in (6, 7, 8)
      f1 = 0xBE not in pt and pt.get(0xF1) == 6
      # Any known camera-owner frame rules out the removed-camera topology.
      removed = not any(address in cam for address in (0x180, 0x320, 0x370))
      camera_layout = removed or (cam.get(0x180) == 4 and cam.get(0x320) == 6 and
                                  cam.get(0x370, 6) == 6)
      required = {0x184: 8, 0x34A: 5, 0x1E1: 7, 0x1C4: 8, 0xC9: 8, 0x3D1: 8, 0xBD: 7}
      sources = (be or f1) and all(pt.get(a) == n for a, n in required.items())
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and CAMERA_DATA_HEADER_MSG not in fingerprint[CanBus.OBSTACLE]
      ret.flags = int(GMFlags.PEDAL_LONG | (GMFlags.NO_CAMERA if removed else 0) |
                      (GMFlags.NO_ACCELERATOR_POS_MSG if f1 else 0)) | (int(ret.flags) & int(GMFlags.HAS_BSM))
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.dashcamOnly = not sources or not camera_layout
      ret.alphaLongitudinalAvailable = not ret.dashcamOnly and not is_release and pt.get(0x1F5) == 8
      ret.openpilotLongitudinalControl = ret.alphaLongitudinalAvailable
      ret.pcmCruise = False
      if not ret.dashcamOnly:
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.gm)]
        ret.safetyConfigs[0].safetyParam = ((0xE600 if ret.openpilotLongitudinalControl else 0xE610) +
                                            4 * int(not ret.radarUnavailable) + 2 * int(removed) + int(f1))
      else:
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.noOutput)]
      ret.autoResumeSng = ret.openpilotLongitudinalControl
      ret.minEnableSpeed = -1.
      ret.stopAccel = -1.5
      ret.longitudinalTuning.kiBP, ret.longitudinalTuning.kiV = [0., 3., 6., 35.], [.09, .13, .19, .28]
    elif candidate == CAR.CHEVROLET_VOLT_CC:
      ret.dashcamOnly = not ret.openpilotLongitudinalControl
    if candidate in BOLT_CC_WORDS and not ret.flags & GMFlags.PEDAL_LONG.value:
      camera_removed = 0x180 not in fingerprint.get(CanBus.CAMERA, {})
      ret.flags |= GMFlags.CC_LONG.value
      ret.openpilotLongitudinalControl = True
      ret.alphaLongitudinalAvailable = False
      ret.pcmCruise = False
      ret.radarUnavailable = True
      ret.minEnableSpeed = 24 * CV.MPH_TO_MS
      ret.longitudinalActuatorDelay = 1.
      ret.longitudinalTuning.kiBP = [0.]
      ret.longitudinalTuning.kiV = [.1]
      ret.safetyConfigs[0].safetyParam = BOLT_CC_WORDS[candidate][camera_removed]
      ret.stopAccel = -0.25
    if candidate in ORDINARY_CC_CAR or candidate == CAR.CHEVROLET_SILVERADO_CC:
      if supported_pedal_detected(fingerprint, CanBus.POWERTRAIN, supported=True):
        camera_length = fingerprint.get(CanBus.CAMERA, {}).get(0x320)
        removed = camera_length is None
        be_length = fingerprint.get(CanBus.POWERTRAIN, {}).get(0xBE)
        sources = (fingerprint.get(CanBus.POWERTRAIN, {}).get(0xF1) == 6 and
                   (be_length is None or be_length in (6, 7, 8)))
        if candidate == CAR.CHEVROLET_SILVERADO_CC:
          pt = fingerprint.get(CanBus.POWERTRAIN, {})
          required = {0x184: 8, 0x34A: 5, 0xC9: 8, 0x3D1: 8, 0x1E1: 7, 0x1C4: 8, 0x1F5: 8}
          sources = (all(pt.get(address) == length for address, length in required.items()) and
                     (be_length in (6, 7, 8) or be_length is None and pt.get(0xF1) == 6) and
                     RADAR_HEADER_MSG not in fingerprint.get(CanBus.OBSTACLE, {}) and
                     CAMERA_DATA_HEADER_MSG not in fingerprint.get(CanBus.OBSTACLE, {}) and not docs)
        ret.flags = int(GMFlags.PEDAL_LONG | (GMFlags.NO_CAMERA if removed else 0) |
                        (GMFlags.NO_ACCELERATOR_POS_MSG if be_length is None else 0))
        ret.networkLocation = NetworkLocation.fwdCamera
        ret.radarUnavailable = True
        ret.dashcamOnly = not sources or camera_length not in (None, 6)
        ret.openpilotLongitudinalControl = not ret.dashcamOnly
        ret.pcmCruise = False
        ret.alphaLongitudinalAvailable = False
        ret.safetyConfigs[0].safetyParam = (SILVERADO_CC_PEDAL_WORDS[0][removed] if candidate == CAR.CHEVROLET_SILVERADO_CC else
                                           0xC181 if removed else 0xC180)
        ret.minEnableSpeed = -1.
        ret.autoResumeSng = True
        ret.longitudinalActuatorDelay = .5
        if candidate == CAR.CHEVROLET_SILVERADO_CC:
          ret.stopAccel = -2.
        if candidate == CAR.CHEVROLET_MALIBU_CC:
          ret.longitudinalTuning.kiBP = [0., 5., 35.]
          ret.longitudinalTuning.kiV = [0., .30, .45]
        else:
          ret.longitudinalTuning.kiBP = [0., 3., 6., 35.]
          ret.longitudinalTuning.kiV = [.09, .13, .19, .28]

    if candidate == CAR.CHEVROLET_SUBURBAN:
      ret.longitudinalTuning.kiBP = [5., 35., 60.]
      ret.longitudinalTuning.kiV = [0.5, 0.5, 0.5]
    if (candidate in BOLT_PEDAL_REMOVED_WORDS and supported_pedal_detected(fingerprint, CanBus.POWERTRAIN, supported=True) and
        0x320 not in fingerprint.get(CanBus.CAMERA, {})):
      pt = fingerprint.get(CanBus.POWERTRAIN, {})
      required = {0x184: 8, 0x34A: 5, 0x348: 5, 0xC9: 8, 0x1C4: 8, 0x1E1: 7,
                  0x1F5: 8, 0xBD: 7, 0x232: 8, 0x3D1: 8}
      sources = all(pt.get(address) == length for address, length in required.items()) and pt.get(0xBE) in (6, 7, 8)
      ret.flags = int(GMFlags.PEDAL_LONG | GMFlags.NO_CAMERA) | (int(ret.flags) & int(GMFlags.HAS_BSM))
      ret.dashcamOnly = not sources
      ret.openpilotLongitudinalControl = sources
      ret.pcmCruise = False
      ret.alphaLongitudinalAvailable = False
      if sources:
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.gm)]
        ret.safetyConfigs[0].safetyParam = BOLT_PEDAL_REMOVED_WORDS[candidate]
      else:
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.noOutput)]
    if (candidate == CAR.CHEVROLET_MALIBU_CC and 0x201 not in fingerprint[CanBus.POWERTRAIN] and
        0xBE not in fingerprint[CanBus.POWERTRAIN]):
      pt = fingerprint[CanBus.POWERTRAIN]
      admitted = (all(pt.get(address) == length for address, length in MALIBU_CC_F1_SOURCES.items()) and
                  RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and
                  CAMERA_DATA_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and not docs)
      ret.flags = int(GMFlags.CC_LONG | GMFlags.NO_ACCELERATOR_POS_MSG)
      ret.openpilotLongitudinalControl = admitted
      ret.pcmCruise = not admitted
      ret.dashcamOnly = not admitted
      ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.gm if admitted else structs.CarParams.SafetyModel.noOutput)]
      if admitted:
        ret.safetyConfigs[0].safetyParam = MALIBU_CC_F1_WORD
    if candidate == CAR.CHEVROLET_MALIBU_HYBRID_CC:
      pt, cam = fingerprint[CanBus.POWERTRAIN], fingerprint[CanBus.CAMERA]
      pedal, removed, alternate = 0x201 in pt, 0x320 not in cam, 0xBE not in pt
      admitted = (not docs and all(pt.get(address) == length for address, length in MALIBU_HYBRID_SOURCES.items()) and
                  (pt.get(0xF1) == 6 if alternate else pt.get(0xBE) in (6, 7, 8)) and
                  (not pedal or pt.get(0x201) == 6) and (removed or cam.get(0x180) == 4 and cam.get(0x320) == 6))
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.transmissionType = TransmissionType.direct
      ret.alphaLongitudinalAvailable = False
      ret.radarUnavailable = not pedal or RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE]
      ret.flags = int(GMFlags.CC_LONG | (GMFlags.PEDAL_LONG if pedal else 0) |
                      (GMFlags.NO_CAMERA if removed else 0) | (GMFlags.NO_ACCELERATOR_POS_MSG if alternate else 0))
      ret.openpilotLongitudinalControl, ret.pcmCruise = admitted, not admitted
      ret.dashcamOnly = not admitted
      ret.autoResumeSng = pedal and admitted
      ret.minEnableSpeed = -1. if pedal else 24 * CV.MPH_TO_MS
      ret.minSteerSpeed = 10 * CV.KPH_TO_MS
      ret.steerActuatorDelay = .2
      ret.longitudinalActuatorDelay = .5
      ret.stopAccel = -.25
      ret.longitudinalTuning.kiBP, ret.longitudinalTuning.kiV = [5., 35.], [.5, .5]
      if pedal:
        ret.longitudinalTuning.kiBP, ret.longitudinalTuning.kiV = [0., 3., 6., 35.], [.07, .10, .15, .24]
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)
      ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.gm if admitted else structs.CarParams.SafetyModel.noOutput,
                                           0xE800 + 2 * int(pedal) + int(removed) if admitted else 0)]
    if 0x142 in fingerprint[CanBus.POWERTRAIN] or candidate in VOLT_BSM_CAR:
      ret.flags |= GMFlags.HAS_BSM.value
    return ret
