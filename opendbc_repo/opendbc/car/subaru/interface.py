import time

from opendbc.car import get_safety_config, structs, uds
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.subaru.carcontroller import CarController
from opendbc.car.subaru.carstate import CarState
from opendbc.car.subaru.values import CAR, GLOBAL_ES_ADDR, CanBus, SubaruFlags, SubaruSafetyFlags

GEN2_ANGLE_PORTS = frozenset((CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025, CAR.SUBARU_LEGACY_2025,
                              CAR.SUBARU_ASCENT_2023, CAR.SUBARU_FORESTER_2022, CAR.SUBARU_OUTBACK_2023))


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController

  def update(self, can_packets):
    source = self.CS.startup_sources
    if source is not None and self.CC.stop_start is not None and not self.CC.stop_start.done:
      can_packets = list(can_packets)
      mono_ns = time.monotonic_ns()
      boot_ns = time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))
      if time.monotonic_ns() - mono_ns <= 1_000_000:
        source.observe(can_packets, boot_ns=boot_ns, mono_ns=mono_ns)
      else:
        source.frames.clear()
        source.sequential.clear()
    avh = self.CS.avh_sources
    if avh is not None and not self.CC.avh_startup.done:
      can_packets = list(can_packets)
      mono_ns = time.monotonic_ns()
      boot_ns = time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))
      if time.monotonic_ns() - mono_ns <= 1_000_000:
        avh.observe(can_packets, boot_ns=boot_ns, mono_ns=mono_ns)
      else:
        avh.blocked = True
    result = super().update(can_packets)
    from opendbc.car.subaru.sng import selected
    if selected(self.CP):
      from opendbc.car.subaru.sng_source import capture
      capture(self, can_packets)
    return result

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate: CAR, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "subaru"
    ret.radarUnavailable = True
    # for HYBRID CARS to be upstreamed, we need:
    # - replacement for ES_Distance so we can cancel the cruise control
    # - to find the Cruise_Activated bit from the car
    # - proper panda safety setup (use the correct cruise_activated bit, throttle from Throttle_Hybrid, etc)
    ret.dashcamOnly = bool(ret.flags & (SubaruFlags.PREGLOBAL | SubaruFlags.HYBRID) or
                           (ret.flags & SubaruFlags.LKAS_ANGLE and candidate not in GEN2_ANGLE_PORTS))
    ret.autoResumeSng = False

    # Detect infotainment message sent from the camera
    if not (ret.flags & SubaruFlags.PREGLOBAL) and 0x323 in fingerprint[2]:
      ret.flags |= SubaruFlags.SEND_INFOTAINMENT.value

    if candidate == CAR.SUBARU_ASCENT_2023 and fingerprint[CanBus.camera].get(0x3C4) == 8:
      ret.flags |= SubaruFlags.ASCENT_LKAS_ALERT.value

    if ret.flags & SubaruFlags.PREGLOBAL:
      if 0x25c in fingerprint[0]:
        ret.flags |= SubaruFlags.HAS_BSM.value
      ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.subaruPreglobal)]
    else:
      if 0x228 in fingerprint[0]:
        ret.flags |= SubaruFlags.HAS_BSM.value
      ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.subaru)]
      if ret.flags & SubaruFlags.GLOBAL_GEN2:
        ret.safetyConfigs[0].safetyParam |= SubaruSafetyFlags.GEN2.value
      if candidate in GEN2_ANGLE_PORTS:
        ret.safetyConfigs[0].safetyParam |= SubaruSafetyFlags.LKAS_ANGLE.value
        if candidate == CAR.SUBARU_IMPREZA_2025:
          ret.safetyConfigs[0].safetyParam |= SubaruSafetyFlags.IMPREZA_2025_ANGLE.value
        if candidate in (CAR.SUBARU_LEGACY_2025, CAR.SUBARU_ASCENT_2023, CAR.SUBARU_OUTBACK_2023):
          ret.safetyConfigs[0].safetyParam |= SubaruSafetyFlags.FIXED_ANGLE_LIMITS.value
        if candidate == CAR.SUBARU_OUTBACK_2023:
          ret.safetyConfigs[0].safetyParam |= SubaruSafetyFlags.D_PLATFORM.value
        if candidate == CAR.SUBARU_ASCENT_2023:
          ret.safetyConfigs[0].safetyParam |= SubaruSafetyFlags.ANGLE_MAIN_BUS.value
          if ret.flags & SubaruFlags.ASCENT_LKAS_ALERT:
            ret.safetyConfigs[0].safetyParam |= SubaruSafetyFlags.ASCENT_LKAS_ALERT.value

    ret.steerLimitTimer = 0.4
    ret.steerActuatorDelay = 0.1

    if not (ret.flags & SubaruFlags.LKAS_ANGLE):
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    if ret.flags & SubaruFlags.LKAS_ANGLE:
      ret.steerControlType = structs.CarParams.SteerControlType.angle

    elif candidate == CAR.SUBARU_ASCENT:
      ret.steerActuatorDelay = 0.3  # end-to-end angle controller
      ret.lateralTuning.init('pid')
      ret.lateralTuning.pid.kf = 0.00003
      ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0., 20.], [0., 20.]]
      ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.0025, 0.1], [0.00025, 0.01]]

    elif candidate == CAR.SUBARU_IMPREZA:
      ret.steerActuatorDelay = 0.4  # end-to-end angle controller
      ret.lateralTuning.init('pid')
      ret.lateralTuning.pid.kf = 0.00005
      ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0., 20.], [0., 20.]]
      ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.2, 0.3], [0.02, 0.03]]

    elif candidate == CAR.SUBARU_IMPREZA_2020:
      ret.lateralTuning.init('pid')
      ret.lateralTuning.pid.kf = 0.00005
      ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0., 14., 23.], [0., 14., 23.]]
      ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.045, 0.042, 0.20], [0.04, 0.035, 0.045]]

    elif candidate == CAR.SUBARU_CROSSTREK_HYBRID:
      ret.steerActuatorDelay = 0.1

    elif candidate in (CAR.SUBARU_FORESTER, CAR.SUBARU_FORESTER_HYBRID):
      ret.lateralTuning.init('pid')
      ret.lateralTuning.pid.kf = 0.000038
      ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0., 14., 23.], [0., 14., 23.]]
      ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.01, 0.065, 0.2], [0.001, 0.015, 0.025]]

    elif candidate in (CAR.SUBARU_OUTBACK, CAR.SUBARU_LEGACY):
      ret.steerActuatorDelay = 0.1

    elif candidate in (CAR.SUBARU_FORESTER_PREGLOBAL, CAR.SUBARU_OUTBACK_PREGLOBAL_2018):
      # Outback 2018-2019 and Forester have reversed driver torque signal
      ret.safetyConfigs[0].safetyParam = SubaruSafetyFlags.PREGLOBAL_REVERSED_DRIVER_TORQUE.value

    elif candidate == CAR.SUBARU_LEGACY_PREGLOBAL:
      ret.steerActuatorDelay = 0.15

    elif candidate == CAR.SUBARU_OUTBACK_PREGLOBAL:
      pass
    else:
      raise ValueError(f"unknown car: {candidate}")

    # TODO: the longitudinal limits need to be speed-dependent, so alpha long is disabled for now
    # revert this in the PR that re-enables Subaru longitudinal: https://github.com/commaai/opendbc/pull/3689
    ret.alphaLongitudinalAvailable = False
    ret.openpilotLongitudinalControl = alpha_long and ret.alphaLongitudinalAvailable

    if ret.flags & SubaruFlags.GLOBAL_GEN2 and ret.openpilotLongitudinalControl:
      ret.flags |= SubaruFlags.DISABLE_EYESIGHT.value

    if ret.openpilotLongitudinalControl:
      ret.safetyConfigs[0].safetyParam |= SubaruSafetyFlags.LONG.value

    return ret

  @staticmethod
  def init(CP, can_recv, can_send, communication_control=None):
    if CP.flags & SubaruFlags.DISABLE_EYESIGHT:
      if communication_control is None:
        communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL, uds.CONTROL_TYPE.DISABLE_RX_DISABLE_TX, uds.MESSAGE_TYPE.NORMAL])
      disable_ecu(can_recv, can_send, bus=2, addr=GLOBAL_ES_ADDR, com_cont_req=communication_control)

  @staticmethod
  def deinit(CP, can_recv, can_send):
    communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL, uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX, uds.MESSAGE_TYPE.NORMAL])
    CarInterface.init(CP, can_recv, can_send, communication_control)
