#!/usr/bin/env python3
from opendbc.car import get_safety_config, structs
from opendbc.car.chrysler.carcontroller import CarController
from opendbc.car.chrysler.carstate import CarState
from opendbc.car.chrysler.radar_interface import RadarInterface
from opendbc.car.chrysler.values import CAR, CUSW_CARS, RAM_HD, RAM_DT, RAM_CARS, ChryslerFlags, ChryslerSafetyFlags
from opendbc.car.interfaces import CarInterfaceBase

# Stock radar track addresses used by RadarInterface (bus 1). Presence means the
# OEM forward radar ECU is talking — not that Pacifica fusion DBC scales/FCW are safe
# for this platform (see issue 26842). Kept for future WK2 validation / opt-in.
_CHRYSLER_RADAR_ADDRS = frozenset(range(0x2a2, 0x2b4 + 2, 2)) | frozenset(range(0x2c2, 0x2d4 + 2, 2))


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  DRIVABLE_GEARS = (structs.CarState.GearShifter.low,)

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "chrysler"

    # TODO: Chrysler CUSW in dashcam pending comma safety validation and a fix for LKAS fault on disengage
    ret.dashcamOnly = candidate in (RAM_HD | CUSW_CARS)

    # Radar parsing is forced off for Chrysler until per-platform validation.
    # History: https://github.com/commaai/openpilot/issues/26842 — false planner FCWs
    # on Pacifica Hybrid from noisy/incorrect fusion-radar parse (adeeb disabled in #27190;
    # haraschax noted tracks look wrong: slow non-stationary ghosts + missing true lead).
    # WK2-2019 has live bus1 track addrs, but that only proves ECU presence — not DBC scale
    # correctness or safe planner FCW behavior. Do not re-enable until a WK2 route is
    # offline-checked (vision lead vs radarTracks noise / false FCW). Keep OEM radar ECU up.
    ret.radarUnavailable = True
    ret.steerActuatorDelay = 0.1
    ret.steerLimitTimer = 0.4

    # safety config
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.chrysler)]
    if candidate in RAM_HD:
      ret.safetyConfigs[0].safetyParam |= ChryslerSafetyFlags.RAM_HD.value
    elif candidate in RAM_DT:
      ret.safetyConfigs[0].safetyParam |= ChryslerSafetyFlags.RAM_DT.value
    elif candidate in CUSW_CARS:
      ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.chryslerCusw)]

    CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)
    if candidate not in RAM_CARS:
      # Newer FW versions standard on the following platforms, or flashed by a dealer onto older platforms have a higher minimum steering speed.
      new_eps_platform = candidate in (CAR.CHRYSLER_PACIFICA_2019_HYBRID, CAR.CHRYSLER_PACIFICA_2020, CAR.JEEP_GRAND_CHEROKEE_2019, CAR.DODGE_DURANGO)
      new_eps_firmware = any(fw.ecu == 'eps' and fw.fwVersion[:4] >= b"6841" for fw in car_fw)
      if new_eps_platform or new_eps_firmware:
        ret.flags |= ChryslerFlags.HIGHER_MIN_STEERING_SPEED.value

    # Chrysler
    if candidate in (CAR.CHRYSLER_PACIFICA_2018, CAR.CHRYSLER_PACIFICA_2018_HYBRID, CAR.CHRYSLER_PACIFICA_2019_HYBRID,
                     CAR.CHRYSLER_PACIFICA_2020, CAR.DODGE_DURANGO):
      ret.lateralTuning.init('pid')
      ret.lateralTuning.pid.kpBP, ret.lateralTuning.pid.kiBP = [[9., 20.], [9., 20.]]
      ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.15, 0.30], [0.03, 0.05]]
      ret.lateralTuning.pid.kf = 0.00006

    # Jeep
    elif candidate == CAR.JEEP_CHEROKEE_5TH_GEN:
      ret.steerActuatorDelay = 0.15

    elif candidate in (CAR.JEEP_GRAND_CHEROKEE, CAR.JEEP_GRAND_CHEROKEE_2019):
      ret.steerActuatorDelay = 0.2
      ret.flags |= ChryslerFlags.JEEP_BRAKE_HOLD_AVAILABLE.value

      ret.lateralTuning.init('pid')
      ret.lateralTuning.pid.kpBP, ret.lateralTuning.pid.kiBP = [[9., 20.], [9., 20.]]
      # Slightly softer high-speed P/I than Pacifica to reduce lane ping-pong reports.
      ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.12, 0.22], [0.02, 0.035]]
      ret.lateralTuning.pid.kf = 0.00006

      if candidate == CAR.JEEP_GRAND_CHEROKEE_2019:
        ret.flags |= ChryslerFlags.ALPHA_LONG_AVAILABLE.value
        # Bus1 track addrs (_CHRYSLER_RADAR_ADDRS) are present on this car, but #26842 means
        # addr presence ≠ safe parse. Leave radarUnavailable=True until WK2 offline validation.
        # Alpha long is experimental: available outside release builds (and for docs generation).
        ret.alphaLongitudinalAvailable = (not is_release) or docs
        if alpha_long and ret.alphaLongitudinalAvailable:
          ret.openpilotLongitudinalControl = True
          ret.safetyConfigs[0].safetyParam |= ChryslerSafetyFlags.LONG_CONTROL.value
          ret.minEnableSpeed = -1.  # advertise auto-resume / SNG capability
          ret.autoResumeSng = True

    # Ram
    elif candidate == CAR.RAM_1500_5TH_GEN:
      ret.steerActuatorDelay = 0.2
      ret.minEnableSpeed = 14.5
      ret.minSteerSpeed = 0.5

    elif candidate == CAR.RAM_HD_5TH_GEN:
      ret.steerActuatorDelay = 0.2

    else:
      raise ValueError(f"Unsupported car: {candidate}")

    if ret.flags & ChryslerFlags.HIGHER_MIN_STEERING_SPEED:
      # TODO: allow these cars to steer down to 13 m/s if already engaged.
      # TODO: Durango 2020 may be able to steer to zero once above 38 kph
      ret.minSteerSpeed = 17.5  # m/s 17 on the way up, 13 on the way down once engaged.

    if candidate not in CUSW_CARS and 0x4FF in fingerprint[0]:
      ret.flags |= ChryslerFlags.STEERING_SPEED_BYPASS.value
      ret.minSteerSpeed = 0.

    ret.pcmCruise = not ret.openpilotLongitudinalControl

    ret.centerToFront = ret.wheelbase * 0.44
    if (0x62cc033 if candidate in CUSW_CARS else 0x2d0) in fingerprint[0]:
      ret.flags |= ChryslerFlags.HAS_BSM.value

    return ret
