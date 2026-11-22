#!/usr/bin/env python3
from opendbc.car import get_safety_config, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.mazda.carcontroller import CarController
from opendbc.car.mazda.carstate import CarState
from opendbc.car.mazda.values import CAR, LKAS_LIMITS


_CX5_EPS_SWAP_FW = {
  ('engine', 0x7E0): b'PX2H-188K2-E'.ljust(24, b'\x00'),
  ('transmission', 0x7E1): b'PYB2-21PS1-G'.ljust(24, b'\x00'),
  ('eps', 0x730): b'KSD5-3210X-C-00'.ljust(24, b'\x00'),
}


def _has_cx5_eps_swap(car_fw):
  observed = {(str(fw.ecu), fw.address, bytes(fw.fwVersion)) for fw in car_fw
              if fw.brand == 'mazda' and fw.bus == 0 and fw.subAddress == 0 and not fw.logging and
              (str(fw.ecu), fw.address) in _CX5_EPS_SWAP_FW}
  return observed == {(ecu, address, version) for (ecu, address), version in _CX5_EPS_SWAP_FW.items()}


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "mazda"
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.mazda)]
    ret.radarUnavailable = True
    ret.deprecated.enableBsm = True

    ret.dashcamOnly = candidate not in (CAR.MAZDA_CX5, CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021)

    ret.steerActuatorDelay = 0.1
    ret.steerLimitTimer = 0.8

    CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    eps_swap = candidate == CAR.MAZDA_CX5 and _has_cx5_eps_swap(car_fw)
    # Both low-speed profiles still use the EPS LKAS_BLOCK signal in CarState.
    if candidate != CAR.MAZDA_CX5_2022 and not eps_swap:
      ret.minSteerSpeed = LKAS_LIMITS.DISABLE_SPEED * CV.KPH_TO_MS

    ret.centerToFront = ret.wheelbase * 0.41

    return ret
