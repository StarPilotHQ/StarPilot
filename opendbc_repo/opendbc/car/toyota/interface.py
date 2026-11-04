from opendbc.car import Bus, structs, get_safety_config, uds
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.carcontroller import CarController, uses_rav4_hybrid_sdsu_longitudinal
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import Ecu, CAR, DBC, ToyotaFlags, CarControllerParams, TSS2_CAR, RADAR_ACC_CAR, NO_DSU_CAR, \
                                                  MIN_ACC_SPEED, EPS_SCALE, NO_STOP_TIMER_CAR, ANGLE_CONTROL_CAR, \
                                                  ToyotaSafetyFlags, LEGACY_PRIUS_CAR, TOYOTA_AUTO_HOLD_CARS, \
                                                  uses_toyota_auto_hold_aeb, ARS510_RADAR_CAR
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.safety import ALTERNATIVE_EXPERIENCE
from openpilot.common.params import Params

SteerControlType = structs.CarParams.SteerControlType


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  @classmethod
  def get_params(cls, candidate, fingerprint, car_fw, alpha_long, is_release, docs, starpilot_toggles):
    ret = super().get_params(candidate, fingerprint, car_fw, alpha_long, is_release, docs, starpilot_toggles)
    radar_mode = getattr(starpilot_toggles, "toyota_radar_mode_requested", 0)
    if candidate in ARS510_RADAR_CAR and radar_mode in (1, 2):
      ret.flags |= ToyotaFlags.ARS510_RADAR.value
      if radar_mode == 2:
        ret.flags |= ToyotaFlags.ARS510_SUMMARIES.value
      ret.radarUnavailable = False
    return ret

  @staticmethod
  def get_pid_accel_limits(CP, current_speed, cruise_speed):
    return CarControllerParams(CP).ACCEL_MIN, CarControllerParams(CP).ACCEL_MAX

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "toyota"
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.toyota)]
    ret.safetyConfigs[0].safetyParam = EPS_SCALE[candidate]

    if candidate == CAR.LEXUS_IS:
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.ALT_CRUISE.value

    # BRAKE_MODULE is on a different address for these cars
    if DBC[candidate][Bus.pt] == "toyota_new_mc_pt_generated":
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.ALT_BRAKE.value

    if ret.flags & ToyotaFlags.SECOC.value:
      ret.secOcRequired = True
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.SECOC.value
      ret.dashcamOnly = is_release

    if candidate in ANGLE_CONTROL_CAR:
      ret.steerControlType = SteerControlType.angle
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.LTA.value

      # LTA control can be more delayed and winds up more often
      ret.steerActuatorDelay = 0.18
      ret.steerLimitTimer = 0.8
    else:
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

      ret.steerActuatorDelay = 0.12  # Default delay, Prius has larger delay
      ret.steerLimitTimer = 0.4

    stop_and_go = candidate in TSS2_CAR

    # Detect smartDSU, which intercepts ACC_CMD from the DSU (or radar) allowing
    # openpilot to send it. 0x2AA is sent by a similar device which intercepts
    # the radar instead of DSU on NO_DSU_CARs.
    use_sdsu = bool(0x2FF in fingerprint[0] or (0x2AA in fingerprint[0] and candidate in NO_DSU_CAR))
    if use_sdsu:
      # sDSU / radar filter hardware needs the Toyota safety long-filter TX set.
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.LONG_FILTER.value

    # A DSU bypass adapter reroutes the stock DSU messages to the camera bus.
    # These messages are normally absent there on pre-TSS2 platforms.
    camera_fingerprint = fingerprint.get(2, {})
    has_dsu_bypass = 0x343 in camera_fingerprint or 0x4CB in camera_fingerprint
    late_prius_camera = candidate in LEGACY_PRIUS_CAR and any(
      fw.ecu == Ecu.fwdCamera and bytes(fw.fwVersion).startswith(b'8646F4705') for fw in car_fw
    )
    if candidate in (CAR.LEXUS_IS, CAR.TOYOTA_CAMRY) or late_prius_camera:
      native_acc_fingerprints = (fingerprint.get(0, {}), fingerprint.get(1, {}))
      has_dsu_bypass = ((0x343 in camera_fingerprint and not any(0x343 in native_bus for native_bus in native_acc_fingerprints)) or
                        (0x4CB in camera_fingerprint and 0x4CB not in fingerprint.get(0, {})))
    if not use_sdsu and candidate not in TSS2_CAR and has_dsu_bypass:
      ret.flags |= ToyotaFlags.DSU_BYPASS.value

    # In TSS2 cars, the camera does long control
    found_ecus = [fw.ecu for fw in car_fw]

    if Ecu.hybrid in found_ecus:
      ret.flags |= ToyotaFlags.HYBRID.value

    if candidate in LEGACY_PRIUS_CAR:
      stop_and_go = True
      ret.flags |= ToyotaFlags.HYBRID.value
      # Only give steer angle deadzone to for bad angle sensor prius
      for fw in car_fw:
        if fw.ecu == "eps" and not fw.fwVersion == b'8965B47060\x00\x00\x00\x00\x00\x00':
          ret.steerActuatorDelay = 0.14
          CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning, steering_angle_deadzone_deg=0.3)

    elif candidate in (CAR.LEXUS_RX, CAR.LEXUS_RX_TSS2):
      stop_and_go = True
      ret.wheelSpeedFactor = 1.035

    elif candidate in (CAR.TOYOTA_AVALON, CAR.TOYOTA_AVALON_2019, CAR.TOYOTA_AVALON_TSS2):
      # starting from 2019, all Avalon variants have stop and go
      # https://engage.toyota.com/static/images/toyota_safety_sense/TSS_Applicability_Chart.pdf
      stop_and_go = candidate != CAR.TOYOTA_AVALON

    elif candidate in ANGLE_CONTROL_CAR:
      ret.lateralTuning.init('pid')
      ret.lateralTuning.pid.kiBP = [0.0]
      ret.lateralTuning.pid.kpBP = [0.0]
      ret.lateralTuning.pid.kpV = [0.6]
      ret.lateralTuning.pid.kiV = [0.1]
      ret.lateralTuning.pid.kf = 0.00007818594

      # 2019+ RAV4 TSS2 uses two different steering racks and specific tuning seems to be necessary.
      # See https://github.com/commaai/openpilot/pull/21429#issuecomment-873652891
      for fw in car_fw:
        if fw.ecu == "eps" and (fw.fwVersion.startswith(b'\x02') or fw.fwVersion in [b'8965B42181\x00\x00\x00\x00\x00\x00']):
          ret.lateralTuning.pid.kpV = [0.15]
          ret.lateralTuning.pid.kiV = [0.05]
          ret.lateralTuning.pid.kf = 0.00004
          break

    elif candidate in (CAR.TOYOTA_CHR, CAR.TOYOTA_CAMRY, CAR.TOYOTA_SIENNA, CAR.LEXUS_CTH, CAR.LEXUS_NX):
      # TODO: Some of these platforms are not advertised to have full range ACC, do they really all have sng?
      stop_and_go = True

    ret.centerToFront = ret.wheelbase * 0.44

    # TODO: Some TSS-P platforms have BSM, but are flipped based on region or driving direction.
    # Detect flipped signals and enable for C-HR and others
    ret.enableBsm = 0x3F6 in fingerprint[0] and candidate in TSS2_CAR

    # No radar dbc for cars without DSU which are not TSS 2.0
    # TODO: make an adas dbc file for dsu-less models
    ret.radarUnavailable = Bus.radar not in DBC[candidate] or candidate in (NO_DSU_CAR - TSS2_CAR - {CAR.TOYOTA_CAMRY})
    if candidate == CAR.TOYOTA_CAMRY:
      ret.radarTimeStepDEPRECATED = 0.1

    # Since we don't yet parse radar on TSS2/TSS-P radar-based ACC cars, gate
    # longitudinal behind the alpha-long toggle.
    if candidate in (RADAR_ACC_CAR | NO_DSU_CAR):
      ret.alphaLongitudinalAvailable = use_sdsu or candidate in RADAR_ACC_CAR

      if not use_sdsu:
        # Disabling radar is only supported on TSS2 radar-ACC cars.
        if alpha_long and candidate in RADAR_ACC_CAR:
          ret.flags |= ToyotaFlags.DISABLE_RADAR.value
      else:
        use_sdsu = use_sdsu and alpha_long

    # openpilot longitudinal enabled by default:
    #  - non-(TSS2 radar ACC cars) w/ smartDSU or radar filter installed
    #  - TSS2 cars with camera sending ACC_CONTROL where we can block it
    # openpilot longitudinal behind experimental long toggle:
    #  - TSS2 radar ACC cars w/ smartDSU or radar filter installed
    #  - TSS2 radar ACC cars (disables radar)

    ret.openpilotLongitudinalControl = (use_sdsu or
                                        bool(ret.flags & ToyotaFlags.DSU_BYPASS.value) or
                                        candidate in (TSS2_CAR - RADAR_ACC_CAR) or
                                        bool(ret.flags & ToyotaFlags.DISABLE_RADAR.value))

    ret.autoResumeSng = ret.openpilotLongitudinalControl and candidate in NO_STOP_TIMER_CAR
    ret.enableGasInterceptorDEPRECATED = 0x201 in fingerprint[0] and ret.openpilotLongitudinalControl
    if ret.enableGasInterceptorDEPRECATED:
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.GAS_INTERCEPTOR.value

    toyota_auto_hold = Params(return_defaults=True).get_bool("ToyotaAutoHold")
    if toyota_auto_hold and ret.openpilotLongitudinalControl and candidate in TOYOTA_AUTO_HOLD_CARS:
      ret.alternativeExperience |= (ALTERNATIVE_EXPERIENCE.ALLOW_AEB
                                    if uses_toyota_auto_hold_aeb(ret)
                                    else ALTERNATIVE_EXPERIENCE.TOYOTA_AUTO_HOLD)
      ret.flags |= ToyotaFlags.AUTO_BRAKE_HOLD.value

    if not ret.openpilotLongitudinalControl:
      ret.safetyConfigs[0].safetyParam |= ToyotaSafetyFlags.STOCK_LONGITUDINAL.value

    # min speed to enable ACC. if car can do stop and go, then set enabling speed
    # to a negative value, so it won't matter.
    rav4_hybrid_sdsu_long_defaults = uses_rav4_hybrid_sdsu_longitudinal(ret)
    ret.minEnableSpeed = -1. if (stop_and_go or ret.enableGasInterceptorDEPRECATED or rav4_hybrid_sdsu_long_defaults) else MIN_ACC_SPEED

    prius_long_defaults = candidate in LEGACY_PRIUS_CAR and ret.openpilotLongitudinalControl
    camry_hybrid_long_defaults = (candidate == CAR.TOYOTA_CAMRY and ret.openpilotLongitudinalControl and
                                  bool(ret.flags & ToyotaFlags.HYBRID.value))

    if candidate in TSS2_CAR or ret.enableGasInterceptorDEPRECATED or prius_long_defaults:
      ret.flags |= ToyotaFlags.RAISED_ACCEL_LIMIT.value

      ret.vEgoStopping = 0.25
      ret.vEgoStarting = 0.25
      ret.stoppingDecelRate = 0.3  # reach stopping target smoothly

      # Hybrids have much quicker longitudinal actuator response
      if ret.flags & ToyotaFlags.HYBRID.value:
        ret.longitudinalActuatorDelay = 0.05

    if camry_hybrid_long_defaults or rav4_hybrid_sdsu_long_defaults:
      # The THS eCVT responds much faster than the legacy non-TSS2 ICE tune.
      ret.longitudinalActuatorDelay = 0.05
      ret.vEgoStopping = 0.25
      ret.vEgoStarting = 0.25
      ret.stoppingDecelRate = 0.3

    if candidate == CAR.TOYOTA_HIGHLANDER and ret.openpilotLongitudinalControl and not ret.flags & ToyotaFlags.HYBRID.value:
      ret.longitudinalActuatorDelay = 0.4

    if candidate == CAR.TOYOTA_SIENNA and ret.openpilotLongitudinalControl:
      ret.longitudinalActuatorDelay = 0.5

    if ret.enableGasInterceptorDEPRECATED:
      # Pedal/SDSU Toyotas feel best with a softer final stop clamp.
      ret.longitudinalActuatorDelay = max(ret.longitudinalActuatorDelay, 0.2)
      ret.stopAccel = -1.5

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
