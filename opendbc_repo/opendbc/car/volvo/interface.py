from opendbc.car import Bus, structs, get_safety_config
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.volvo.carcontroller import CarController
from opendbc.car.volvo.carstate import CarState
from opendbc.car.volvo.radar_interface import RadarInterface
from opendbc.car.volvo.values import CAR, DBC, VolvoC1PlatformConfig, VolvoEUCDPlatformConfig, VolvoSafetyFlags

TransmissionType = structs.CarParams.TransmissionType

SAFETY_VOLVO = structs.CarParams.SafetyModel.volvo

PSCM_RELATED_ADDR = 0x17
PSCM_RELATED_BUS = 2  # Bus.party


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface  # Delphi ESR 2.5 on the V60; the others have no radar DBC

  def __init__(self, CP):
    self.is_eucd = isinstance(CAR(CP.carFingerprint).config, VolvoEUCDPlatformConfig)
    if self.is_eucd:
      from opendbc.car.volvo.eucd_carstate import CarStateEUCD
      from opendbc.car.volvo.eucd_carcontroller import CarControllerEUCD
      self.CarState = CarStateEUCD
      self.CarController = CarControllerEUCD
    super().__init__(CP)

  def update(self, can_packets):
    # CANParser only exposes decoded signals; PSCM_RELATED status validation also
    # needs the raw bytes (checksums and length), so keep the newest frame for CarState.
    can_packets = list(can_packets)
    if not self.is_eucd and not self.CS.is_c1:
      for _, frames in can_packets:
        for address, data, bus in frames:
          if address == PSCM_RELATED_ADDR and bus == PSCM_RELATED_BUS:
            self.CS.pscm_related_raw = bytes(data)
    return super().update(can_packets)

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = 'volvo'
    platform = CAR(candidate).config
    c1 = isinstance(platform, VolvoC1PlatformConfig)
    eucd = isinstance(platform, VolvoEUCDPlatformConfig)
    safety_param = VolvoSafetyFlags.C1.value if c1 else VolvoSafetyFlags.EUCD.value if eucd else 0
    ret.safetyConfigs = [get_safety_config(SAFETY_VOLVO, safety_param)]
    ret.dashcamOnly = False
    ret.steerActuatorDelay = 0.2 if (c1 or eucd) else 0.3
    ret.steerLimitTimer = 0.8 if eucd else 0.1
    ret.steerAtStandstill = not c1
    ret.steerControlType = structs.CarParams.SteerControlType.angle
    ret.radarUnavailable = Bus.radar not in DBC[candidate]
    ret.alphaLongitudinalAvailable = eucd
    if eucd:
      # OP long: stock ACC engages and owns the set speed; OP replaces FSM3
      ret.openpilotLongitudinalControl = alpha_long
      if ret.openpilotLongitudinalControl:
        ret.longitudinalActuatorDelay = 0.5  # FSM3 accel to aEgo delay
        # Integral-only accel loop, the car brakes short of OP's request (route 89d)
        ret.longitudinalTuning.kiBP = [0.]
        ret.longitudinalTuning.kiV = [0.2]
    ret.pcmCruise = True
    if c1:
      ret.transmissionType = TransmissionType.automatic
    return ret
