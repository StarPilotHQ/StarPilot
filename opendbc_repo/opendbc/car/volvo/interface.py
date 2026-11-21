from collections import Counter, deque

from opendbc.car import structs, get_safety_config
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.volvo.carstate import CarState
from opendbc.car.volvo.carcontroller import CarController
from opendbc.car.volvo.helpers import pscm_status_validation_class
from opendbc.car.volvo.values import CAR, VolvoC1PlatformConfig, VolvoSPAPlatformConfig, VolvoSafetyFlags


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController

  def __init__(self, CP):
    if not isinstance(CAR(CP.carFingerprint).config, VolvoC1PlatformConfig):
      from opendbc.car.volvo.cma_carstate import CarStateCMA
      from opendbc.car.volvo.cma_carcontroller import CarControllerCMA
      self.CarState = CarStateCMA
      self.CarController = CarControllerCMA
    super().__init__(CP)
    self.stock_relay = deque(maxlen=128)
    self.pscm_status_class = None

  def update(self, can_packets):
    state = super().update(can_packets)
    if self.CP.carFingerprint == CAR.POLESTAR_2:
      for _timestamp, frames in can_packets:
        for address, data, bus in frames:
          if ((bus == 0 and address in (0x58, 0x57, 0x69, 0x90, 0x67, 0x97, 0x92)) or
              (bus == 2 and address in (0x16, 0x17))) and len(data) == 8:
            if bus == 2 and address == 0x17:
              status_class = pscm_status_validation_class(data)
              if status_class != self.pscm_status_class:
                self.stock_relay = deque((frame for frame in self.stock_relay if frame[0] != 0x17), maxlen=128)
              self.pscm_status_class = status_class
              self.CS.pscm_status_supported = status_class == (8, True, True)
              if not self.CS.pscm_status_supported:
                self.CS.pscm_rearm_required = True
            self.stock_relay.append((address, data, 2 if bus == 0 else 0))
    return state

  def apply(self, c, now_nanos=None):
    actuators, messages = super().apply(c, now_nanos)
    if self.CP.carFingerprint == CAR.POLESTAR_2:
      if not self.CC.lat_active:
        # Panda only retains four source packets per address. After a backlog,
        # resume each stream with its newest packet instead of bursting stale copies.
        counts = Counter(address for address, _, _ in self.stock_relay)
        if max(counts.values(), default=0) > 4:
          latest = {frame[0]: index for index, frame in enumerate(self.stock_relay)}
          messages = [frame for index, frame in enumerate(self.stock_relay) if index == latest[frame[0]]]
        else:
          messages = list(self.stock_relay)
      self.stock_relay.clear()
    return actuators, messages

  @staticmethod
  def _get_params(ret, candidate, fingerprint, car_fw, alpha_long, is_release, docs):
    ret.brand = 'volvo'
    platform = CAR(candidate).config
    c1 = isinstance(platform, VolvoC1PlatformConfig)
    safety_param = VolvoSafetyFlags.C1.value if c1 else VolvoSafetyFlags.SPA.value if isinstance(platform, VolvoSPAPlatformConfig) else 0
    if candidate == CAR.POLESTAR_2:
      safety_param |= VolvoSafetyFlags.LCA2_MOD15.value
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.volvo, safety_param)]
    ret.steerActuatorDelay = 0.2 if c1 else 0.3
    ret.steerLimitTimer = 0.1
    ret.steerAtStandstill = not c1
    ret.steerControlType = structs.CarParams.SteerControlType.angle
    ret.radarUnavailable = True
    ret.alphaLongitudinalAvailable = False
    ret.pcmCruise = True
    if c1:
      ret.transmissionType = structs.CarParams.TransmissionType.automatic
    return ret
