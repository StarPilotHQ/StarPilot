from opendbc.car import ACCEL_MAX_TAKEOFF
from opendbc.car import Bus, structs, get_safety_config, uds
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.carcontroller import CarController
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import Ecu, CAR, DBC, ToyotaFlags, CarControllerParams, MIN_ACC_SPEED, \
                                                  EPS_SCALE, ToyotaSafetyFlags, TOYOTA_AUTO_HOLD_CARS, uses_toyota_auto_hold_aeb
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.safety import ALTERNATIVE_EXPERIENCE

SteerControlType = structs.CarParams.SteerControlType


def toyota_auto_hold_supported(CP) -> bool:
  from opendbc.car.toyota import pedal, pedal_composite
  if not (CP.brand == "toyota" and CP.carFingerprint in TOYOTA_AUTO_HOLD_CARS and
          CP.openpilotLongitudinalControl and not CP.flags & ToyotaFlags.SECOC and
          not CP.notCar and not CP.dashcamOnly and not CP.passive and len(CP.safetyConfigs) == 1 and
          CP.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.toyota):
    return False
  parent = pedal_composite.parent_cp(CP)
  return (any(module.configured(parent) for module in pedal_composite.parent_modules()) or
          pedal.configured(CP) or
          ((parent.safetyConfigs[0].safetyParam & 0xFF) == EPS_SCALE[parent.carFingerprint] and
           not parent.safetyConfigs[0].safetyParam & ~(0xFF | int(ToyotaSafetyFlags.ALT_BRAKE) | int(ToyotaSafetyFlags.LTA))))


def apply_toyota_auto_hold(CP, enabled: bool) -> bool:
  if CP.brand != "toyota":
    return False
  CP.flags &= ~int(ToyotaFlags.AUTO_BRAKE_HOLD)
  CP.alternativeExperience &= ~(ALTERNATIVE_EXPERIENCE.TOYOTA_AUTO_HOLD | ALTERNATIVE_EXPERIENCE.TOYOTA_AEB_HOLD)
  admitted = enabled and toyota_auto_hold_supported(CP)
  if admitted:
    CP.flags |= int(ToyotaFlags.AUTO_BRAKE_HOLD)
    CP.alternativeExperience |= (ALTERNATIVE_EXPERIENCE.TOYOTA_AEB_HOLD if uses_toyota_auto_hold_aeb(CP)
                                 else ALTERNATIVE_EXPERIENCE.TOYOTA_AUTO_HOLD)
  return bool(admitted)


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  DRIVABLE_GEARS = (structs.CarState.GearShifter.sport, structs.CarState.GearShifter.brake)

  @staticmethod
  def get_pid_accel_limits(CP, current_speed, cruise_speed, *, faster_takeoffs=False):
    return CarControllerParams(CP).ACCEL_MIN, ACCEL_MAX_TAKEOFF if faster_takeoffs else CarControllerParams(CP).ACCEL_MAX

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    from opendbc.car.toyota import pedal_composite, prius_legacy_pedal
    legacy_prius_pedal = prius_legacy_pedal.hardware_supported(candidate, fingerprint, car_fw)
    composite_pedal = legacy_prius_pedal or pedal_composite.hardware_supported(candidate, fingerprint, car_fw)
    if composite_pedal:
      fingerprint = pedal_composite.without_pedal(fingerprint)
    ret.brand = "toyota"
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.toyota)]
    ret.safetyConfigs[0].safetyParam = EPS_SCALE[candidate]
    if candidate == CAR.LEXUS_IS:
      ret.safetyConfigs[0].safetyParam |= int(ToyotaSafetyFlags.ALT_CRUISE)

    # BRAKE_MODULE is on a different address for these cars
    if DBC[candidate][Bus.pt] == "toyota_new_mc_pt_generated":
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.ALT_BRAKE.value

    if ret.flags & ToyotaFlags.SECOC.value:
      ret.secOcRequired = True
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.SECOC.value
      ret.dashcamOnly = is_release

    if ret.flags & ToyotaFlags.ANGLE_CONTROL:
      ret.steerControlType = SteerControlType.angle
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.LTA.value

      # LTA control can be more delayed and winds up more often
      ret.steerActuatorDelay = 0.18
      ret.steerLimitTimer = 0.8
    else:
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

      ret.steerActuatorDelay = 0.12  # Default delay, Prius has larger delay
      ret.steerLimitTimer = 0.4

    from opendbc.car.toyota import prius_retrofit_advert
    retrofit_advert = prius_retrofit_advert.hardware_supported(candidate, fingerprint, car_fw)
    smart_dsu = (candidate in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT) and
                 (fingerprint.get(0, {}).get(0x2FF) == 8 or retrofit_advert) and
                 any(fw.ecu == Ecu.eps and bytes(fw.fwVersion) == b"8965B47070\x00\x00\x00\x00\x00\x00"
                     for fw in car_fw))
    if candidate in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT) and fingerprint.get(0, {}).get(0x23) == 8:
      ret.flags |= int(ToyotaFlags.ZSS)
    stop_and_go = bool(ret.flags & ToyotaFlags.TSS2)

    # In TSS2 cars, the camera does long control
    found_ecus = [fw.ecu for fw in car_fw]

    if Ecu.hybrid in found_ecus:
      ret.flags |= ToyotaFlags.HYBRID.value

    if candidate in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT):
      stop_and_go = True
      ret.flags |= ToyotaFlags.HYBRID.value
      # Only give steer angle deadzone to for bad angle sensor prius
      for fw in car_fw:
        if fw.ecu == "eps" and not fw.fwVersion == b'8965B47060\x00\x00\x00\x00\x00\x00':
          ret.steerActuatorDelay = 0.14
          CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning, steering_angle_deadzone_deg=0.3)
        # 2021+ TSS2 steering rack swapped into a TSS-P car, not supported
        if candidate == CAR.TOYOTA_PRIUS and not smart_dsu and fw.ecu == "eps" and fw.fwVersion == b'8965B47070\x00\x00\x00\x00\x00\x00':
          ret.dashcamOnly = True

    elif candidate in (CAR.LEXUS_RX, CAR.LEXUS_RX_TSS2):
      stop_and_go = True
      ret.wheelSpeedFactor = 1.035

    elif candidate in (CAR.TOYOTA_AVALON, CAR.TOYOTA_AVALON_2019, CAR.TOYOTA_AVALON_TSS2):
      # starting from 2019, all Avalon variants have stop and go
      # https://engage.toyota.com/static/images/toyota_safety_sense/TSS_Applicability_Chart.pdf
      stop_and_go = candidate != CAR.TOYOTA_AVALON

    elif candidate in (CAR.TOYOTA_CHR, CAR.TOYOTA_CAMRY, CAR.TOYOTA_SIENNA, CAR.LEXUS_CTH, CAR.LEXUS_LS, CAR.LEXUS_NX):
      # TODO: Some of these platforms are not advertised to have full range ACC, do they really all have sng?
      stop_and_go = True

    ret.centerToFront = ret.wheelbase * 0.44

    # TODO: Some TSS-P platforms have BSM, but are flipped based on region or driving direction.
    # Detect flipped signals and enable for C-HR and others
    if 0x3F6 in fingerprint[0] and ret.flags & ToyotaFlags.TSS2:
      ret.flags |= ToyotaFlags.HAS_BSM.value

    ret.radarUnavailable = Bus.radar not in DBC[candidate]
    if candidate == CAR.TOYOTA_CAMRY:
      ret.radarUnavailable = False
      ret.deprecated.radarTimeStep = 0.1

    from opendbc.car.toyota.radar_filter import hardware_supported as radar_filter_supported
    radar_filter = radar_filter_supported(candidate, fingerprint)
    if radar_filter:
      ret.flags |= int(ToyotaFlags.RADAR_CAN_FILTER)
      if fingerprint.get(0, {}).get(0x2FF) == 4 and ret.steerControlType == SteerControlType.torque:
        ret.flags |= int(ToyotaFlags.FILTER_BUTTONS)
      ret.safetyConfigs[0].safetyParam |= int(ToyotaSafetyFlags.LONG_FILTER)

    # since we don't yet parse radar on TSS2 radar-based ACC cars, gate longitudinal behind alpha toggle
    if ret.flags & ToyotaFlags.RADAR_ACC or radar_filter:
      ret.alphaLongitudinalAvailable = True

      if alpha_long and not radar_filter:
        ret.flags |= ToyotaFlags.DISABLE_RADAR.value

    # openpilot longitudinal enabled by default:
    #  - TSS2 cars with camera sending ACC_CONTROL where we can block it
    # openpilot longitudinal behind alpha long toggle:
    #  - TSS2 radar ACC cars (disables radar)

    ret.openpilotLongitudinalControl = ((bool(ret.flags & ToyotaFlags.TSS2) and not (ret.flags & ToyotaFlags.RADAR_ACC)) or
                                        bool(ret.flags & ToyotaFlags.DISABLE_RADAR.value) or (radar_filter and alpha_long))

    if candidate == CAR.TOYOTA_YARIS and ret.flags & ToyotaFlags.DISABLE_RADAR:
      ret.safetyConfigs[0].safetyParam |= int(ToyotaSafetyFlags.SECOC_RADAR_LONG)

    from opendbc.car.toyota import matrix_filter
    manual_matrix_filter = any(matrix_filter.hardware_supported(candidate, fingerprint, car_fw, length=size) for size in (4, 8))
    if candidate in (CAR.TOYOTA_MATRIX_RETROFIT, CAR.TOYOTA_PRIUS_RETROFIT):
      # These manually selected retrofits currently own stock PCM longitudinal.
      # Observed takeover hardware needs its paired controller/native port.
      camera_fp = fingerprint.get(2, {})
      bypass = 0x343 in camera_fp or 0x4CB in camera_fp
      # A camera-bus copy of a still-present native DSU is not a takeover.
      if candidate == CAR.TOYOTA_PRIUS_RETROFIT:
        bypass = ((0x343 in camera_fp and not any(0x343 in fingerprint.get(bus, {}) for bus in (0, 1))) or
                  (0x4CB in camera_fp and 0x4CB not in fingerprint[0]))
      smart_dsu = (candidate == CAR.TOYOTA_PRIUS_RETROFIT and
                   (fingerprint.get(0, {}).get(0x2FF) == 8 or retrofit_advert) and
                   any(fw.ecu == Ecu.eps and bytes(fw.fwVersion) == b"8965B47070\x00\x00\x00\x00\x00\x00"
                       for fw in car_fw))
      takeover = 0x2FF in fingerprint[0] or bypass
      if smart_dsu:
        ret.flags |= int(ToyotaFlags.LONG_FILTER)
        ret.safetyConfigs[0].safetyParam |= int(ToyotaSafetyFlags.LONG_FILTER)
      elif takeover and not manual_matrix_filter:
        ret.dashcamOnly = True
      ret.openpilotLongitudinalControl = smart_dsu
      ret.alphaLongitudinalAvailable = False

    if smart_dsu:
      ret.flags |= int(ToyotaFlags.HYBRID | ToyotaFlags.LONG_FILTER)
      ret.safetyConfigs[0].safetyParam = 0x8049  # EPS73 status uses the existing EPS8 smartDSU receiver.
      ret.openpilotLongitudinalControl = True
    from opendbc.car.toyota import legacy_advert_filter
    legacy_advert = legacy_advert_filter.hardware_supported(candidate, fingerprint, car_fw)
    from opendbc.car.toyota.rav4h_filter import hardware_supported, LONG_WORD
    rav4h_sdsu = hardware_supported(candidate, fingerprint, car_fw)
    if rav4h_sdsu:
      ret.flags |= int(ToyotaFlags.LONG_FILTER)
      if candidate == CAR.TOYOTA_RAV4H:
        ret.flags |= int(ToyotaFlags.HYBRID)
      ret.safetyConfigs[0].safetyParam = LONG_WORD
      ret.openpilotLongitudinalControl = True
      ret.alphaLongitudinalAvailable = False
      if candidate == CAR.TOYOTA_RAV4H:
        stop_and_go = True
    elif candidate == CAR.TOYOTA_RAV4H and 0x2FF in fingerprint.get(0, {}) and not legacy_advert:
      ret.dashcamOnly = True
    from opendbc.car.toyota import eps8_sdsu
    eps8_filter = eps8_sdsu.hardware_supported(candidate, fingerprint, car_fw)
    if eps8_filter:
      eps8_sdsu.apply(ret, alpha_long)
    from opendbc.car.toyota import legacy_sdsu
    legacy_filter = legacy_sdsu.hardware_supported(candidate, fingerprint, car_fw)
    if legacy_filter:
      legacy_sdsu.apply(ret)
    from opendbc.car.toyota import advert_filter
    advertised_filter = advert_filter.hardware_supported(candidate, fingerprint, car_fw)
    if advertised_filter:
      from opendbc.car.toyota import camera_2aa_filter
      advert_filter.apply(ret, camera_twoaa=camera_2aa_filter.hardware_supported(candidate, fingerprint, car_fw))
      if legacy_advert and candidate == CAR.TOYOTA_RAV4H:
        stop_and_go = True
    # Toyota EPS accepts lateral commands down to zero. Keep the existing
    # native/axis/fault gates; a stock cruise stop does not withdraw steering.
    ret.steerAtStandstill = ret.minSteerSpeed <= 0.
    ret.autoResumeSng = ret.openpilotLongitudinalControl and (
      (rav4h_sdsu and candidate in (CAR.TOYOTA_RAV4H, CAR.TOYOTA_HIGHLANDER, CAR.TOYOTA_SIENNA)) or
      (eps8_filter and bool(ret.flags & ToyotaFlags.TSS2)) or
      (legacy_filter and candidate == CAR.TOYOTA_PRIUS_V) or
      (advertised_filter and bool(ret.flags & ToyotaFlags.TSS2)) or not ret.flags & ToyotaFlags.LONG_FILTER and not advertised_filter)

    if legacy_advert:
      ret.autoResumeSng = ret.openpilotLongitudinalControl and candidate in legacy_advert_filter.AUTO_RESUME_CARS

    if radar_filter and not ret.flags & ToyotaFlags.TSS2:
      ret.autoResumeSng = False  # Preserve the pre-TSS2 stock stop timer.

    if not ret.openpilotLongitudinalControl:
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.STOCK_LONGITUDINAL.value

    # min speed to enable ACC. if car can do stop and go, then set enabling speed
    # to a negative value, so it won't matter.
    ret.minEnableSpeed = -1. if stop_and_go else MIN_ACC_SPEED

    if ret.flags & ToyotaFlags.TSS2 or ret.flags & ToyotaFlags.LONG_FILTER and not (rav4h_sdsu or eps8_filter or legacy_filter):
      ret.flags |= ToyotaFlags.RAISED_ACCEL_LIMIT.value

      # Hybrids have much quicker longitudinal actuator response
      if ret.flags & ToyotaFlags.HYBRID.value:
        ret.longitudinalActuatorDelay = 0.05

    # Classify the original rack after generic tuning so current numerical CP fields stay exact.
    from opendbc.car.toyota import prius_sdsu
    if prius_sdsu.hardware_supported(candidate, fingerprint, car_fw):
      prius_sdsu.apply(ret)
    from opendbc.car.toyota import prius_advert_filter
    if prius_advert_filter.hardware_supported(candidate, fingerprint, car_fw):
      prius_advert_filter.apply(ret)
    from opendbc.car.toyota import dsu_bypass
    if dsu_bypass.hardware_supported(candidate, fingerprint, car_fw):
      dsu_bypass.apply(ret)
    from opendbc.car.toyota import pedal
    if ret.openpilotLongitudinalControl and pedal.hardware_supported(candidate, fingerprint, car_fw):
      ret.flags |= int(ToyotaFlags.GAS_INTERCEPTOR)
      ret.safetyConfigs[0].safetyParam = pedal.long_word(candidate)
    if composite_pedal:
      if legacy_prius_pedal:
        dsu_bypass.apply(ret)
      assert pedal_composite.apply(ret, legacy_prius=legacy_prius_pedal)
    from opendbc.car.toyota import stock_aol
    if stock_aol.hardware_supported(candidate, fingerprint):
      stock_aol.apply(ret)
    from opendbc.car.toyota.dom_defaults import apply_defaults
    apply_defaults(ret)
    from opendbc.car.toyota.ars510_preferences import configure_radar
    configure_radar(ret)
    return ret

  @staticmethod
  def init(CP, can_recv, can_send, communication_control=None):
    # disable radar if alpha longitudinal toggled on radar-ACC car
    if CP.flags & ToyotaFlags.DISABLE_RADAR.value:
      if communication_control is None:
        communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL, uds.CONTROL_TYPE.ENABLE_RX_DISABLE_TX, uds.MESSAGE_TYPE.NORMAL])
      disable_ecu(can_recv, can_send, bus=0, addr=0x750, sub_addr=0xf, com_cont_req=communication_control)

  @staticmethod
  def deinit(CP, can_recv, can_send):
    # re-enable radar if alpha longitudinal toggled on radar-ACC car
    communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL, uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX, uds.MESSAGE_TYPE.NORMAL])
    CarInterface.init(CP, can_recv, can_send, communication_control)
