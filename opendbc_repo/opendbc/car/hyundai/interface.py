from opendbc.car.hyundai.ioniq5pe_long_profile import required as pe_long_required, candidate as pe_long_candidate
from opendbc.car.pedal import supported_pedal_detected
from opendbc.car.hyundai.classic_scc_aol import CLASSIC_SCC_IDS
from opendbc.car import Bus, get_safety_config, structs, uds
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.non_scc_aol import NON_SCC_IDS
from opendbc.car.hyundai.values import (is_blended, is_blended_alpha, HyundaiFlags, CAR, DBC, HyundaiSafetyFlags, CANFD_ANGLE_MODEL_BITS,
                                        CANFD_ANGLE_OBSERVED_ADAS_BIT, HYUNDAI_MRR35_RADAR_DBC, HYUNDAI_MRR30_RADAR_DBC,
                                        HYUNDAI_GV70_RADAR_DBC, HYUNDAI_G90_RADAR_DBC, HYUNDAI_MRREVO14F_RADAR_DBC)
from opendbc.car.hyundai.radar_interface import RADAR_START_ADDR, MRR35_RADAR_START_ADDR, MRR30_RADAR_START_ADDR, radar_bus, \
                                               stinger_2022_radar_available
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.carstate import CarState
from opendbc.car.hyundai.radar_interface import RadarInterface, blended_track_radar

ButtonType = structs.CarState.ButtonEvent.Type
Ecu = structs.CarParams.Ecu

# Cancel button can sometimes be ACC pause/resume button, main button can also enable on some cars
ENABLE_BUTTONS = (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.cancel, ButtonType.mainCruise)
KONA_NON_SCC_FCA_RADAR_ADDR = 0x602


def detect_kona_non_scc_radar_fca(candidate, fingerprint, car_fw) -> bool:
  return candidate == CAR.HYUNDAI_KONA_NON_SCC and (
    any(fw.ecu == Ecu.fwdRadar for fw in car_fw) or KONA_NON_SCC_FCA_RADAR_ADDR in fingerprint[1]
  )


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  @staticmethod
  def requires_lateral_body_dimensions(cp):
    # The shared platform identifier does not establish a body/camera envelope.
    return cp.carFingerprint == CAR.HYUNDAI_IONIQ_6

  @staticmethod
  def get_lateral_body_dimensions(cp):
    from opendbc.car.hyundai.lane_body import dimensions
    return dimensions(cp)

  @staticmethod
  def startup_required(cp):
    from opendbc.car.hyundai.g90_startup import required
    from opendbc.car.hyundai.ev6_startup import required as ev6_required
    from opendbc.car.hyundai.gv70_startup import required as gv70_required
    from opendbc.car.hyundai.ev9_startup import required as ev9_required
    from opendbc.car.hyundai.torque_ev_startup import required as torque_ev_required
    from opendbc.car.hyundai.blended_longitudinal import startup_required as blended_required
    from opendbc.car.hyundai.legacy_long_startup import required as legacy_required
    from opendbc.car.hyundai.ordinary_classic_startup import required as ordinary_classic_required
    from opendbc.car.hyundai.radar_hda1_startup import required as radar_hda1_required
    from opendbc.car.hyundai.radar_hda2_startup import required as radar_hda2_required
    from opendbc.car.hyundai.generic_hda2_startup import required as generic_hda2_required
    from opendbc.car.hyundai.gv60_startup import required as gv60_required
    return (gv60_required(cp) or generic_hda2_required(cp) or radar_hda2_required(cp) or radar_hda1_required(cp) or ordinary_classic_required(cp) or
            pe_long_required(cp) or legacy_required(cp) or blended_required(cp) or required(cp) or
            ev6_required(cp) or gv70_required(cp) or ev9_required(cp) or torque_ev_required(cp))

  @staticmethod
  def startup_owner(cp, callbacks, *, requested):
    from opendbc.car.hyundai.ioniq5pe_startup import PEPendingStartup
    pe_cp = pe_long_candidate(cp, enabled=requested, is_release=False)
    if pe_cp is not None:
      return PEPendingStartup(pe_cp, callbacks, stock_cp=cp)
    from opendbc.car.hyundai.legacy_long_aol import candidate as legacy_candidate
    from opendbc.car.hyundai.legacy_long_startup import LegacyLongStartup
    legacy_cp = legacy_candidate(cp, requested=requested)
    if legacy_cp is not None:
      return LegacyLongStartup(legacy_cp, callbacks, stock_cp=cp)
    from opendbc.car.hyundai.g90_startup import G90Startup, required
    if required(cp):
      return G90Startup(cp, callbacks) if requested else None
    from opendbc.car.hyundai.ordinary_classic_startup import OrdinaryClassicStartup, required as ordinary_classic_required
    if ordinary_classic_required(cp):
      return OrdinaryClassicStartup(cp, callbacks) if requested else None
    from opendbc.car.hyundai.ev6_startup import EV6Startup, required as ev6_required
    if ev6_required(cp):
      return EV6Startup(cp, callbacks) if requested else None
    from opendbc.car.hyundai.gv70_startup import GV70Startup, required as gv70_required
    if gv70_required(cp):
      return GV70Startup(cp, callbacks) if requested else None
    from opendbc.car.hyundai.ev9_longitudinal import candidate as ev9_candidate, qualified as ev9_qualified
    from opendbc.car.hyundai.ev9_pending_startup import EV9PendingStartup
    ev9_cp = ev9_candidate(cp, enabled=requested, is_release=False)
    if ev9_qualified(ev9_cp):
      return EV9PendingStartup(ev9_cp, callbacks, stock_cp=cp) if requested else None
    from opendbc.car.hyundai.torque_ev_startup import candidate as torque_ev_candidate, TorqueEVStartup
    torque_cp = torque_ev_candidate(cp, requested=requested, is_release=False)
    if torque_cp is not None:
      return TorqueEVStartup(torque_cp, callbacks, stock_cp=cp)
    from opendbc.car.hyundai.radar_hda1_startup import RadarHDA1Startup, required as radar_hda1_required
    if radar_hda1_required(cp):
      return RadarHDA1Startup(cp, callbacks) if requested else None
    from opendbc.car.hyundai.radar_hda2_startup import RadarHDA2Startup, required as radar_hda2_required
    if radar_hda2_required(cp):
      return RadarHDA2Startup(cp, callbacks) if requested else None
    from opendbc.car.hyundai.gv60_startup import GV60Startup, required as gv60_required
    if gv60_required(cp):
      return GV60Startup(cp, callbacks) if requested else None
    from opendbc.car.hyundai.generic_hda2_startup import GenericHDA2Startup, required as generic_hda2_required
    if generic_hda2_required(cp):
      return GenericHDA2Startup(cp, callbacks) if requested else None
    from opendbc.car.hyundai.blended_longitudinal import startup_owner
    return startup_owner(cp, callbacks, requested=requested)

  DRIVABLE_GEARS = (structs.CarState.GearShifter.sport, structs.CarState.GearShifter.manumatic)

  @staticmethod
  def get_pid_accel_limits(CP, current_speed, cruise_speed):
    from opendbc.car.hyundai.ev9_longitudinal import qualified as ev9_long_qualified
    if ev9_long_qualified(CP):
      return -3.5, 2.2
    if is_blended_alpha(CP):
      return -3.5, 3.5
    return CarInterfaceBase.get_pid_accel_limits(CP, current_speed, cruise_speed)

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "hyundai"

    if is_blended(ret):
      cam_can = CanBus(None, fingerprint).CAM
      lka_steering = any(fw.ecu == Ecu.adas for fw in car_fw) or 0x50 in fingerprint[cam_can]
      if lka_steering:
        ret.flags |= HyundaiFlags.CANFD_LKA_STEER_MSG.value

    if ret.flags & HyundaiFlags.CANFD:
      # Shared configuration for CAN-FD cars

      # "LKA steering" if LKAS or LKAS_ALT messages are seen coming from the camera.
      # Generally means our LKAS message is forwarded to another ECU (commonly ADAS ECU)
      # that finally retransmits our steering command in LFA or LFA_ALT to the MDPS.
      # "LFA steering" if camera directly sends LFA to the MDPS
      cam_can = CanBus(None, fingerprint).CAM
      lka_steering = 0x50 in fingerprint[cam_can] or 0x110 in fingerprint[cam_can]
      CAN = CanBus(None, fingerprint, lka_steering)

      ret.alphaLongitudinalAvailable = not (ret.flags & HyundaiFlags.CANFD_NO_RADAR_DISABLE)
      if (candidate == CAR.HYUNDAI_IONIQ_6 and not lka_steering and
          fingerprint[CAN.CAM].get(0x1A0) == 32 and fingerprint[CAN.ECAN].get(0x1CF) == 8):
        ret.alphaLongitudinalAvailable = True
      if ret.flags & HyundaiFlags.CANFD_ANGLE_STEERING:
        # These manual angle profiles retain stock SCC in both builds.
        ret.alphaLongitudinalAvailable = False
      if lka_steering and Ecu.adas not in [fw.ecu for fw in car_fw]:
        # this needs to be figured out for cars without an ADAS ECU
        ret.alphaLongitudinalAvailable = False

      ret.deprecated.enableBsm = 0x1ba in fingerprint[CAN.ECAN] or candidate == CAR.KIA_EV9
      if candidate == CAR.HYUNDAI_IONIQ_6:
        ret.deprecated.enableBsm = fingerprint[CAN.ECAN].get(0x1ba) == 24
        ret.steerAtStandstill = True

      # Check if the car is hybrid. Only HEV/PHEV cars have 0xFA on E-CAN.
      if 0xFA in fingerprint[CAN.ECAN] or candidate == CAR.KIA_CARNIVAL_HEV_4TH_GEN:
        ret.flags |= HyundaiFlags.HYBRID.value

      if lka_steering:
        # detect LKA steering
        ret.flags |= HyundaiFlags.CANFD_LKA_STEER_MSG.value
        if 0x110 in fingerprint[CAN.CAM]:
          ret.flags |= HyundaiFlags.CANFD_LKA_STEER_MSG_ALT.value
        if (candidate in (CAR.KIA_CARNIVAL_2025, CAR.KIA_CARNIVAL_HEV_4TH_GEN) and
            0x1aa in fingerprint[CAN.ECAN] and 0x1cf not in fingerprint[CAN.ECAN]):
          ret.flags |= HyundaiFlags.CANFD_ALT_BUTTONS.value
      else:
        # no LKA steering
        if 0x1cf not in fingerprint[CAN.ECAN]:
          ret.flags |= HyundaiFlags.CANFD_ALT_BUTTONS.value
        if not ret.flags & HyundaiFlags.CANFD_RADAR_SCC:
          ret.flags |= HyundaiFlags.CANFD_CAMERA_SCC.value
        if ret.flags & HyundaiFlags.CANFD_ANGLE_STEERING and candidate != CAR.KIA_EV6_2025 and 0xCB in fingerprint[CAN.CAM]:
          ret.flags |= HyundaiFlags.SEND_LFA.value

      # The Sportage 2026 identity is documented only without HDA II. A
      # hybrid fuel source belongs to its separate HEV identity.
      if candidate == CAR.KIA_SPORTAGE_2026 and (lka_steering or 0xFA in fingerprint[CAN.ECAN]):
        ret.dashcamOnly = True

      # Some LKA steering cars have alternative messages for gear checks
      # ICE cars do not have 0x130; GEARS message on 0x40 or 0x70 instead
      if 0x130 not in fingerprint[CAN.ECAN]:
        if 0x40 not in fingerprint[CAN.ECAN]:
          ret.flags |= HyundaiFlags.CANFD_ALT_GEARS_2.value
        else:
          ret.flags |= HyundaiFlags.CANFD_ALT_GEARS.value

      if candidate in (CAR.KIA_EV6, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN) and CAN.offset >= 8:
        ret.dashcamOnly = True
      cfgs = [get_safety_config(structs.CarParams.SafetyModel.hyundaiCanfd), ]
      if CAN.ECAN >= 4:
        cfgs.insert(0, get_safety_config(structs.CarParams.SafetyModel.noOutput))
      ret.safetyConfigs = cfgs

      if ret.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_LKA_STEER_MSG.value
        if ret.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT:
          ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_LKA_STEER_MSG_ALT.value
      if ret.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_ALT_BUTTONS.value
      if ret.flags & HyundaiFlags.CANFD_CAMERA_SCC:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CAMERA_SCC.value
      if ret.flags & HyundaiFlags.CCNC and not lka_steering and not ret.flags & HyundaiFlags.CANFD_ANGLE_STEERING:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CCNC.value
      if ret.flags & HyundaiFlags.CANFD_ANGLE_STEERING:
        ret.steerControlType = structs.CarParams.SteerControlType.angle
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_ANGLE_STEERING.value
        ret.safetyConfigs[-1].safetyParam |= CANFD_ANGLE_MODEL_BITS[str(candidate)]
        if ret.flags & HyundaiFlags.SEND_LFA:
          ret.safetyConfigs[-1].safetyParam |= CANFD_ANGLE_OBSERVED_ADAS_BIT
    else:
      # Shared configuration for non CAN-FD cars
      ret.alphaLongitudinalAvailable = not (ret.flags & (HyundaiFlags.LEGACY | HyundaiFlags.UNSUPPORTED_LONGITUDINAL | HyundaiFlags.NON_SCC))
      if is_blended(ret):
        # A prepublication owner alone may promote this exact stock CP.
        ret.alphaLongitudinalAvailable = False
      pt_bus = CanBus(None, fingerprint, bool(ret.flags & HyundaiFlags.CANFD_LKA_STEER_MSG)).ECAN if is_blended(ret) else 0
      ret.deprecated.enableBsm = 0x58b in fingerprint[pt_bus]

      # Send LFA message on cars with HDA
      if 0x485 in fingerprint[2]:
        ret.flags |= HyundaiFlags.SEND_LFA.value

      # These cars use the FCA11 message for the AEB and FCW signals, all others use SCC12
      if 0x38d in fingerprint[pt_bus] or 0x38d in fingerprint[2]:
        ret.flags |= HyundaiFlags.USE_FCA.value
      if detect_kona_non_scc_radar_fca(candidate, fingerprint, car_fw):
        ret.flags |= HyundaiFlags.NON_SCC_RADAR_FCA.value

      if ret.flags & HyundaiFlags.LEGACY:
        # these cars require a special panda safety mode due to missing counters and checksums in the messages
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.hyundaiLegacy)]
      else:
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.hyundai, 0)]

      if is_blended(ret):
        ret.safetyConfigs[0].safetyParam |= HyundaiSafetyFlags.CAN_CANFD_BLENDED.value
        if lka_steering:
          ret.safetyConfigs[0].safetyParam |= HyundaiSafetyFlags.CANFD_LKA_STEER_MSG.value

      if ret.flags & HyundaiFlags.CAMERA_SCC:
        ret.safetyConfigs[0].safetyParam |= HyundaiSafetyFlags.CAMERA_SCC.value
      if candidate in (CAR.HYUNDAI_ELANTRA_2024, CAR.HYUNDAI_ELANTRA_HEV_2024):
        ret.safetyConfigs[0].safetyParam |= HyundaiSafetyFlags.CAN_REFRESH_MSGS.value

      # These cars have the LFA button on the steering wheel
      if 0x391 in fingerprint[0] or (candidate in NON_SCC_IDS | CLASSIC_SCC_IDS and
                                     0x50c in fingerprint[0]):
        ret.flags |= HyundaiFlags.HAS_LDA_BUTTON.value
      if ret.flags & HyundaiFlags.NON_SCC:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.NON_SCC.value
        if ret.flags & HyundaiFlags.HAS_LDA_BUTTON and (candidate not in NON_SCC_IDS or
                                                        0x391 in fingerprint[0]):
          # Ordinary safety still requires its existing BCM source. The AOL
          # profile alone may select the observed alternative CLU source.
          ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.HAS_LDA_BUTTON.value

    if candidate == CAR.KIA_RAY_EV and fingerprint[2].get(0x485) == 8:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CAN_REFRESH_MSGS.value

    if is_blended(ret) and 0x2AA in fingerprint[0]:
      ret.minSteerSpeed = 0.0
      ret.flags &= ~HyundaiFlags.MIN_STEER_32_MPH.value
      ret.steerAtStandstill = True

    # Common lateral control setup

    ret.centerToFront = ret.wheelbase * 0.4
    ret.steerActuatorDelay = 0.1
    ret.steerLimitTimer = 0.4
    if not (ret.flags & HyundaiFlags.CANFD_ANGLE_STEERING):
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    if ret.flags & HyundaiFlags.ALT_LIMITS:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.ALT_LIMITS.value

    if ret.flags & HyundaiFlags.ALT_LIMITS_2:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.ALT_LIMITS_2.value

      # see https://github.com/commaai/opendbc/pull/1137/
      ret.dashcamOnly = True

    # Common longitudinal control setup

    radar_start = KONA_NON_SCC_FCA_RADAR_ADDR if candidate == CAR.HYUNDAI_KONA_NON_SCC else RADAR_START_ADDR
    if DBC[ret.carFingerprint].get(Bus.radar) == HYUNDAI_G90_RADAR_DBC:
      ret.radarUnavailable = not all(fingerprint[1].get(addr) == 8 for addr in range(0x500, 0x520))
    elif blended_track_radar(ret) or DBC[ret.carFingerprint].get(Bus.radar) == HYUNDAI_GV70_RADAR_DBC:
      ret.radarUnavailable = not all(fingerprint[0].get(addr) == 32 for addr in range(0x210, 0x220))
    elif DBC[ret.carFingerprint].get(Bus.radar) == HYUNDAI_MRREVO14F_RADAR_DBC:
      ret.radarUnavailable = fingerprint[1].get(KONA_NON_SCC_FCA_RADAR_ADDR) != 8
    elif DBC[ret.carFingerprint].get(Bus.radar) == HYUNDAI_MRR30_RADAR_DBC:
      ret.radarUnavailable = fingerprint[radar_bus(ret)].get(MRR30_RADAR_START_ADDR) != 32
    elif DBC[ret.carFingerprint].get(Bus.radar) == HYUNDAI_MRR35_RADAR_DBC:
      ret.radarUnavailable = fingerprint[radar_bus(ret)].get(MRR35_RADAR_START_ADDR) != 24
    elif candidate == CAR.KIA_STINGER_2022:
      ret.radarUnavailable = not stinger_2022_radar_available(fingerprint)
    else:
      ret.radarUnavailable = radar_start not in fingerprint[1] or Bus.radar not in DBC[ret.carFingerprint]
    from opendbc.car.hyundai.torque_ev_startup import topology_owned as torque_ev_topology
    if torque_ev_topology(ret):
      # The prepared owner alone promotes the saved developer choice before CI construction.
      ret.alphaLongitudinalAvailable = not is_release
      alpha_long = False
    from opendbc.car.hyundai.legacy_long_aol import CARS as legacy_long_cars
    ret.openpilotLongitudinalControl = alpha_long and ret.alphaLongitudinalAvailable and candidate not in legacy_long_cars
    if (candidate in (CAR.KIA_CARNIVAL_2025, CAR.KIA_CARNIVAL_HEV_4TH_GEN) and
        ret.flags & HyundaiFlags.CANFD_ALT_BUTTONS and 0x1aa in fingerprint[CAN.ECAN] and
        not ret.openpilotLongitudinalControl):
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CARNIVAL_ALT_RESUME.value
    ret.pcmCruise = not ret.openpilotLongitudinalControl
    ret.longitudinalActuatorDelay = 0.5
    if candidate in (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9):
      if candidate == CAR.HYUNDAI_IONIQ_5_PE:
        ret.longitudinalActuatorDelay = 0.35
        ret.steerAtStandstill = True
      # Only the source-derived stock-SCC LKA_ALT topology is admitted.
      if (CAN.ECAN != 1 or CAN.ACAN != 0 or CAN.CAM != 2 or fingerprint[CAN.CAM].get(0x110) != 32 or
          fingerprint[CAN.CAM].get(0x362) != 32 or fingerprint[CAN.ECAN].get(0x1cf) != 8 or
          fingerprint[CAN.ECAN].get(0x35) != 32 or fingerprint[CAN.ECAN].get(0x1a0) != 32 or
          ret.flags & (HyundaiFlags.HYBRID | HyundaiFlags.CANFD_ALT_BUTTONS | HyundaiFlags.CANFD_CAMERA_SCC | HyundaiFlags.SEND_LFA)):
        ret.dashcamOnly = True

    if ret.openpilotLongitudinalControl:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.LONG.value
      if (ret.flags & HyundaiFlags.CANFD and ret.flags & HyundaiFlags.CANFD_LKA_STEER_MSG and
          not ret.flags & HyundaiFlags.CANFD_ANGLE_STEERING and candidate in (
            CAR.KIA_CARNIVAL_4TH_GEN, CAR.KIA_CARNIVAL_2025,
            CAR.KIA_CARNIVAL_HEV_4TH_GEN, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN)):
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_LKAS_DAMPING_100.value
    if ret.flags & HyundaiFlags.HYBRID:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.HYBRID_GAS.value
    elif ret.flags & HyundaiFlags.EV:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.EV_GAS.value
    elif ret.flags & HyundaiFlags.FCEV:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.FCEV_GAS.value

    if (supported_pedal_detected(fingerprint, 0, supported=candidate == CAR.KIA_RAY_EV) and
        ret.safetyConfigs[-1].safetyParam == int(HyundaiSafetyFlags.NON_SCC | HyundaiSafetyFlags.EV_GAS |
                                               HyundaiSafetyFlags.HAS_LDA_BUTTON | HyundaiSafetyFlags.CAN_REFRESH_MSGS)):
      ret.alphaLongitudinalAvailable = True
      ret.openpilotLongitudinalControl = True
      ret.pcmCruise = False
      ret.radarUnavailable = True
      ret.autoResumeSng = False
      ret.minEnableSpeed = -1.0
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.LONG.value
      ret.longitudinalTuning.kiBP = [0.0, 3.0, 6.0, 35.0]
      ret.longitudinalTuning.kiV = [0.07, 0.10, 0.15, 0.24]

    if candidate == CAR.KIA_RAY_EV and not ret.openpilotLongitudinalControl:
      ret.dashcamOnly = True

    # Car specific configuration overrides

    if candidate == CAR.HYUNDAI_ELANTRA_HEV_2024:
      ret.longitudinalActuatorDelay = 0.22

    if is_blended(ret):
      ret.openpilotLongitudinalControl = False
      ret.pcmCruise = True
      ret.stopAccel = -0.85
      from opendbc.car.hyundai.blended_longitudinal import mixed_startup_qualified
      ret.alphaLongitudinalAvailable = mixed_startup_qualified(ret, is_release=is_release)

    if candidate == CAR.KIA_OPTIMA_G4_FL:
      ret.steerActuatorDelay = 0.2

    # Dashcam cars are missing a test route, or otherwise need validation
    # TODO: Optima Hybrid 2017 uses a different SCC12 checksum
    if candidate in (CAR.KIA_OPTIMA_H,):
      ret.dashcamOnly = True

    if candidate == CAR.KIA_EV6_2025 and ret.flags & (HyundaiFlags.CANFD_LKA_STEER_MSG | HyundaiFlags.HYBRID):
      ret.dashcamOnly = True
      ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.noOutput)]

    # Advertise only the exact developer stock profile. Startup still owns the
    # saved-choice promotion, so availability never selects LONG during fingerprinting.
    if candidate in (CAR.KIA_EV9, CAR.HYUNDAI_IONIQ_5_PE):
      from opendbc.car.hyundai.ccnc_ev_stock import qualified as ev9_stock_qualified
      ret.alphaLongitudinalAvailable = not is_release and ev9_stock_qualified(ret)

    from opendbc.car.hyundai.legacy_long_aol import CARS as legacy_long_cars, topology_owned
    if candidate in legacy_long_cars:
      ret.openpilotLongitudinalControl = False
      ret.pcmCruise = True
      ret.alphaLongitudinalAvailable = not is_release and topology_owned(ret)

    from opendbc.car.hyundai.gv60_startup import select_profile as gv60_select_profile
    gv60_select_profile(ret, is_release=is_release)

    return ret

  def update(self, can_packets):
    if self.CS.daw.enabled:
      can_packets = list(can_packets)
      self.CS.daw.update(can_packets)
    recovery = getattr(getattr(self, 'CC', None), 'angle_tx_recovery', None)
    if recovery is not None:
      can_packets = list(can_packets)
      recovery.observe(can_packets, self.CC.frame)
    if (self.CS.pe_ready is not None or self.CS.gv70_camera_lead is not None or self.CS.ev9_camera_lead is not None or
        self.CS.ioniq6_camera_lead is not None or self.CS.torque_ev_camera_lead is not None or
        self.CS.forte_lkas_sources is not None or self.CS.stock_lfa_status is not None):
      # This isolated optional parser must never join required controls health.
      can_packets = list(can_packets)
      if self.CS.gv70_camera_lead is not None:
        self.CS.gv70_camera_lead.update(can_packets)
      if self.CS.pe_ready is not None:
        self.CS.pe_ready.observe(can_packets)
      if self.CS.ev9_camera_lead is not None:
        self.CS.ev9_camera_lead.update(can_packets)
      if self.CS.ioniq6_camera_lead is not None:
        self.CS.ioniq6_camera_lead.update(can_packets)
      if self.CS.torque_ev_camera_lead is not None:
        self.CS.torque_ev_camera_lead.update(can_packets)
      if self.CS.forte_lkas_sources is not None:
        self.CS.forte_lkas_sources.update(can_packets)
      if self.CS.stock_lfa_status is not None:
        self.CS.stock_lfa_status.update(can_packets)
    ret = super().update(can_packets)
    self.CS.finalize_blended_cancel(ret)
    return ret

  @staticmethod
  def init(CP, can_recv, can_send, communication_control=None):
    # 0x80 silences response
    if communication_control is None:
      communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL, 0x80 | uds.CONTROL_TYPE.DISABLE_RX_DISABLE_TX, uds.MESSAGE_TYPE.NORMAL])

    if CP.openpilotLongitudinalControl and CP.carFingerprint != CAR.KIA_RAY_EV and not (CP.flags & (HyundaiFlags.CANFD_CAMERA_SCC | HyundaiFlags.CAMERA_SCC)):
      addr, bus = 0x7d0, CanBus(CP).ECAN if CP.flags & HyundaiFlags.CANFD else 0
      if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG.value:
        addr, bus = 0x730, CanBus(CP).ECAN
      disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)

    # for blinkers
    if CP.flags & HyundaiFlags.CANFD_ENABLE_BLINKERS:
      disable_ecu(can_recv, can_send, bus=CanBus(CP).ECAN, addr=0x7B1, com_cont_req=communication_control)

  @staticmethod
  def deinit(CP, can_recv, can_send):
    communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL, 0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX, uds.MESSAGE_TYPE.NORMAL])
    CarInterface.init(CP, can_recv, can_send, communication_control)
