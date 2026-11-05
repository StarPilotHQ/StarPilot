from opendbc.car.config_cache import cached_config
from dataclasses import dataclass, field, replace
from enum import Enum, IntFlag
from math import isfinite
from types import MappingProxyType

from opendbc.car import Bus, PlatformConfig, DbcDict, Platforms, CarSpecs
from opendbc.car.structs import CarParams
from opendbc.car.docs_definitions import CarDocs, CarFootnote, CarHarness, CarParts, Column, SupportType
from opendbc.car.fw_query_definitions import FwQueryConfig, Request, StdQueries

Ecu = CarParams.Ecu


class CarControllerParams:
  STEER_MAX = 300  # GM limit is 3Nm. Used by carcontroller to generate LKA output
  STEER_STEP = 3  # Active control frames per command (~33hz)
  INACTIVE_STEER_STEP = 10  # Inactive control frames per command (10hz)
  STEER_DELTA_UP = 10  # Delta rates require review due to observed EPS weakness
  STEER_DELTA_DOWN = 15
  STEER_DRIVER_ALLOWANCE = 65
  STEER_DRIVER_MULTIPLIER = 4
  STEER_DRIVER_FACTOR = 100
  NEAR_STOP_BRAKE_PHASE = 0.5  # m/s

  # Heartbeat for dash "Service Adaptive Cruise" and "Service Front Camera"
  ADAS_KEEPALIVE_STEP = 100
  CAMERA_KEEPALIVE_STEP = 100

  # Allow small margin below -3.5 m/s^2 from ISO 15622:2018 since we
  # perform the closed loop control, and might need some
  # to apply some more braking if we're on a downhill slope.
  # Our controller should still keep the 2 second average above
  # -3.5 m/s^2 as per planner limits
  ACCEL_MAX = 2.  # m/s^2
  ACCEL_MIN = -4.  # m/s^2

  def __init__(self, CP):
    if CP.carFingerprint == CAR.CHEVROLET_SILVERADO_CC:
      self.NEAR_STOP_BRAKE_PHASE = .25
    if CP.carFingerprint == CAR.CHEVROLET_BOLT_CC_2017:
      self.STEER_MAX = 450
      self.STEER_DELTA_UP = 15
      self.STEER_DELTA_DOWN = 34
      self.STEER_DRIVER_ALLOWANCE = 78
      self.STEER_DRIVER_MULTIPLIER = 6
    # Gas/brake lookups
    self.MAX_BRAKE = 400  # ~ -4.0 m/s^2 with regen

    if is_volt_camera_longitudinal(CP) or is_volt_camera_removed(CP, longitudinal=True) or is_volt_sdgm_profile(CP, longitudinal=True):
      self.MAX_GAS = 2698.0
      self.MAX_ACC_REGEN = -540.0
      self.INACTIVE_REGEN = -500.0
      self.NEAR_STOP_BRAKE_PHASE = 0.25
      max_regen_acceleration = 0.0
    elif is_volt_longitudinal(CP):
      self.MAX_GAS = 2041.0  # Original Volt raw 8191, normalized by raw - 6150.
      self.MAX_ACC_REGEN = -650.0
      self.INACTIVE_REGEN = -650.0
      max_regen_acceleration = -1.0
      if is_volt_ascm_longitudinal(CP):
        self.NEAR_STOP_BRAKE_PHASE = 0.25
    elif is_bolt_euv_longitudinal(CP):
      self.MAX_GAS = 2698.0  # Original camera raw8848 minus coast6150, exact wire units.
      self.MAX_ACC_REGEN = -540.0
      self.INACTIVE_REGEN = -500.0
      self.NEAR_STOP_BRAKE_PHASE = 0.25
      max_regen_acceleration = 0.0
    elif is_ordinary_camera_profile(CP, longitudinal=True) or (camera_acc_pedal_profile(CP) is not None and CP.openpilotLongitudinalControl):
      self.MAX_GAS = 2698.0
      self.MAX_ACC_REGEN = -540.0
      self.INACTIVE_REGEN = -500.0
      self.NEAR_STOP_BRAKE_PHASE = 0.25
      max_regen_acceleration = 0.0
    elif is_ordinary_sdgm_profile(CP, longitudinal=True):
      self.MAX_GAS = 2698.0
      self.MAX_ACC_REGEN = -540.0
      self.INACTIVE_REGEN = -500.0
      self.NEAR_STOP_BRAKE_PHASE = 0.25
      max_regen_acceleration = 0.0
    elif is_ordinary_ascm_profile(CP, longitudinal=True):
      self.MAX_GAS = 2041.0
      self.MAX_ACC_REGEN = self.INACTIVE_REGEN = -650.0
      self.NEAR_STOP_BRAKE_PHASE = 0.25
      max_regen_acceleration = -0.1
    elif CP.carFingerprint in (CAMERA_ACC_CAR | SDGM_CAR | ASCM_INTERCEPT_CAR):
      self.MAX_GAS = 1346.0
      self.MAX_ACC_REGEN = -540.0
      self.INACTIVE_REGEN = -500.0
      # Camera ACC vehicles have no regen while enabled.
      # Camera transitions to MAX_ACC_REGEN from zero gas and uses friction brakes instantly
      max_regen_acceleration = 0.

    else:
      self.MAX_GAS = 1018.0  # Safety limit, not ACC max. Stock ACC >2042 from standstill.
      self.MAX_ACC_REGEN = -650.0  # Max ACC regen is slightly less than max paddle regen
      self.INACTIVE_REGEN = -650.0
      # ICE has much less engine braking force compared to regen in EVs,
      # lower threshold removes some braking deadzone
      max_regen_acceleration = -1. if CP.carFingerprint in EV_CAR else -0.1

    if is_volt_sdgm_accepted_envelope(CP):
      self.MAX_GAS = 2041.0

    self.GAS_LOOKUP_BP = [max_regen_acceleration, 0., self.ACCEL_MAX]
    self.GAS_LOOKUP_V = [self.MAX_ACC_REGEN, 0., 2698.0 if is_volt_sdgm_accepted_envelope(CP) else self.MAX_GAS]

    self.BRAKE_LOOKUP_BP = [self.ACCEL_MIN, max_regen_acceleration]
    self.BRAKE_LOOKUP_V = [self.MAX_BRAKE, 0.]


class GMSafetyFlags(IntFlag):
  HW_CAM = 1
  HW_CAM_LONG = 2
  EV = 4
  PEDAL_LONG = 8
  NO_ACC = 16
  BOLT_2017 = 32
  BOLT_ACC_PEDAL = 64
  PADDLE_SCHED = 128
  VOLT_AUTO_HOLD = 128  # Contextual only on exact gateway Volt OP-long words.
  BOLT_GEN2 = 256
  ASCM_INTERCEPT = 512
  ASCM_BRAKE_C9 = 1024
  BRAKE_C9 = 1024  # Shared with SDGM; the source is selected by topology.
  ASCM_RADAR = 2048
  SDGM = 4096
  SDGM_CANCEL_PT = 8192
  VOLT_LONG = 16384
  VOLT_GATEWAY_LONG = VOLT_LONG  # Existing gateway profiles retain their numeric word.
  VOLT_GATEWAY_ALT_BRAKE = 32768


class GMFlags(IntFlag):
  PEDAL_LONG = 1
  HAS_BSM = 16
  CC_LONG = 2
  NO_CAMERA = 4
  VOLT_CAMERA_REMOVED = NO_CAMERA
  NO_ACCELERATOR_POS_MSG = 8
  VOLT_CAMERA_NO_ACCEL_POS = NO_ACCELERATOR_POS_MSG


_GM_CONTROL_FLAGS_MASK = ~int(GMFlags.HAS_BSM)
_GM_PEDAL_LONG_FLAG = int(GMFlags.PEDAL_LONG)
_GM_NO_CAMERA_FLAG = int(GMFlags.NO_CAMERA)
_GM_NO_ACCELERATOR_POS_FLAG = int(GMFlags.NO_ACCELERATOR_POS_MSG)
_BOLT_REMOVED_CONTROL_FLAGS = _GM_PEDAL_LONG_FLAG | _GM_NO_CAMERA_FLAG
_CC_PEDAL_ALLOWED_FLAGS = _BOLT_REMOVED_CONTROL_FLAGS | _GM_NO_ACCELERATOR_POS_FLAG


VOLT_ONE_PEDAL_BASE_WORDS = (0x4084, 0xC084, 0x4287, 0x4687, 0x4A87, 0x4E87, 0x4087, 0x5087, 0x5487, 0xC1D1, 0xC1D3)
VOLT_ONE_PEDAL_WORDS = {start + index: word for start in (0xD100, 0xD110)
                        for index, word in enumerate(VOLT_ONE_PEDAL_BASE_WORDS)}


def gm_control_word(cp: CarParams) -> int:
  word = int(cp.safetyConfigs[0].safetyParam)
  if word in VOLT_ONE_PEDAL_WORDS:
    index = word - (0xD110 if word >= 0xD110 else 0xD100)
    identity = (CAR.CHEVROLET_VOLT if index < 2 else CAR.CHEVROLET_VOLT_ASCM if index < 6 else
                CAR.CHEVROLET_VOLT_2019 if index in (7, 8) else CAR.CHEVROLET_VOLT_CAMERA)
    network = CarParams.NetworkLocation.gateway if index < 2 else CarParams.NetworkLocation.fwdCamera
    if (cp.brand == 'gm' and cp.transmissionType == CarParams.TransmissionType.direct and
        cp.carFingerprint == identity and cp.networkLocation == network):
      return VOLT_ONE_PEDAL_WORDS[word]
    return word
  hybrid = malibu_hybrid_profile(cp)
  if hybrid is not None:
    return 5 | (8 if hybrid.pedal and hybrid.longitudinal else 0)
  if word in _BOLT_REMOVED_SAFETY_WORDS and (is_bolt_pedal_removed_profile(cp) or is_bolt_pedal_removed_profile(cp, stock_only=True)):
    return int(BOLT_PEDAL_STOCK_WORDS[cp.carFingerprint] if not cp.openpilotLongitudinalControl else BOLT_PEDAL_WORDS[cp.carFingerprint])
  if word in VOLT_CC_PEDAL_PROFILES and volt_cc_pedal_profile(cp) is not None:
    return 5
  if word in CAMERA_ACC_PEDAL_PROFILES:
    profile = camera_acc_pedal_profile(cp)
    if profile is not None:
      if profile.volt:
        if profile.topology == "sdgm":
          word = 0x5007 if profile.longitudinal else 0x1005
          word |= 0x400 if profile.brake_source == BrakeSource.F1 else 0
          return word | (0x80 if profile.auto_hold or profile.one_pedal else 0)
        if profile.topology == "ascm":
          word = 0x4207 if profile.longitudinal else 0x205
          word |= int(GMSafetyFlags.BRAKE_C9) if profile.brake_source == BrakeSource.F1 else 0
          word |= int(GMSafetyFlags.ASCM_RADAR) if profile.radar else 0
          return word | (0x80 if profile.auto_hold or profile.one_pedal else 0)
        if profile.topology == "gateway":
          return (0xC084 if profile.brake_source == BrakeSource.F1 else 0x4084) if (
            profile.auto_hold or profile.one_pedal) else (0xC004 if profile.brake_source == BrakeSource.F1 else 0x4004)
        if not profile.longitudinal:
          return 0xC150 if profile.removed else 5
        held = profile.auto_hold or profile.one_pedal
        return ((0xC1D3 if profile.brake_source == BrakeSource.F1 else 0xC1D1) if held else 0xC151) if profile.removed else (0x4087 if held else 0x4007)
      return ((0xC173 if profile.longitudinal else 0xC172) if profile.removed else
              (0xC170 if profile.longitudinal else 0xC171))
    return word
  return word


def is_volt_one_pedal(cp: CarParams) -> bool:
  try:
    profile = camera_acc_pedal_profile(cp)
    if profile is not None and profile.volt:
      return profile.longitudinal and profile.one_pedal
    return (int(cp.safetyConfigs[0].safetyParam) in VOLT_ONE_PEDAL_WORDS and
            cp.transmissionType == CarParams.TransmissionType.direct and is_gm_auto_hold(cp))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def apply_volt_one_pedal(cp: CarParams, enabled: bool, auto_hold_enabled: bool) -> None:
  profile = camera_acc_pedal_profile(cp)
  if profile is not None and profile.volt:
    if profile.longitudinal:
      cp.safetyConfigs[0].safetyParam = volt_camera_pedal_word(profile, auto_hold_enabled, enabled)
    return
  if not is_volt_longitudinal(cp) or cp.transmissionType != CarParams.TransmissionType.direct:
    return
  cp.safetyConfigs[0].safetyParam = gm_control_word(cp)
  apply_gm_auto_hold(cp, enabled or auto_hold_enabled)
  word = int(cp.safetyConfigs[0].safetyParam)
  if enabled and word in VOLT_ONE_PEDAL_BASE_WORDS:
    cp.safetyConfigs[0].safetyParam = (0xD110 if auto_hold_enabled else 0xD100) + VOLT_ONE_PEDAL_BASE_WORDS.index(word)


def control_flags(cp: CarParams) -> int:
  """Exclude only the informational BSM bit from exact control-profile admission."""
  flags = int(cp.flags) & _GM_CONTROL_FLAGS_MASK
  profile = camera_acc_pedal_profile(cp)
  if profile is not None and profile.volt:
    flags &= ~int(GMFlags.PEDAL_LONG)
    if not profile.removed:
      flags &= ~int(GMFlags.NO_ACCELERATOR_POS_MSG)
  return flags


def uses_camera_stock_controls(cp: CarParams) -> bool:
  profile = camera_acc_pedal_profile(cp)
  if profile is not None:
    return not profile.longitudinal and not profile.removed
  return (is_ordinary_camera_profile(cp, longitudinal=False) or is_ordinary_sdgm_profile(cp, longitudinal=False) or is_volt_sdgm_profile(cp) or
          cp.carFingerprint in CAMERA_STOCK_CAR and not is_volt_camera_longitudinal(cp) and not is_volt_camera_removed(cp) or
          cp.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023 and not cp.openpilotLongitudinalControl or
          cp.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and is_bolt_pedal_profile(cp, stock_only=True))


def requires_camera_state_sources(cp: CarParams) -> bool:
  hybrid = malibu_hybrid_profile(cp)
  if hybrid is not None:
    return False
  if is_bolt_pedal_removed_profile(cp) or is_bolt_pedal_removed_profile(cp, stock_only=True):
    return False
  profile = camera_acc_pedal_profile(cp)
  if profile is not None:
    return not profile.removed
  return (uses_camera_stock_controls(cp) or is_ordinary_camera_profile(cp, longitudinal=True) or is_ordinary_sdgm_profile(cp, longitudinal=True) or
          is_volt_camera_longitudinal(cp) or is_volt_sdgm_profile(cp, longitudinal=True))


def is_bolt_euv_longitudinal(cp: CarParams) -> bool:
  """Exact factory-ACC Bolt camera-long owner; never an interceptor profile."""
  try:
    return (cp.brand == 'gm' and cp.carFingerprint in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023) and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and cp.alphaLongitudinalAvailable and
            cp.openpilotLongitudinalControl and not cp.pcmCruise and cp.radarUnavailable and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and control_flags(cp) == 0 and
            len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            int(cp.safetyConfigs[0].safetyParam) == int(GMSafetyFlags.EV | GMSafetyFlags.HW_CAM | GMSafetyFlags.HW_CAM_LONG))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_flashed_profile(cp: CarParams) -> bool:
  """Manual plain-Volt OBD owner: flashed, no camera/radar, F1 brake input."""
  try:
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT and
            cp.networkLocation == CarParams.NetworkLocation.gateway and cp.radarUnavailable and
            cp.alphaLongitudinalAvailable and not cp.pcmCruise and control_flags(cp) == 0 and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and len(cp.safetyConfigs) == 1 and
            cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            gm_control_word(cp) == int(GMSafetyFlags.EV | GMSafetyFlags.VOLT_GATEWAY_LONG | GMSafetyFlags.VOLT_GATEWAY_ALT_BRAKE))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_gateway_profile(cp: CarParams) -> bool:
  """Exact radar-qualified gateway owner, independently of the startup speed choice."""
  try:
    if is_volt_flashed_profile(cp):
      return True
    profile = camera_acc_pedal_profile(cp)
    if profile is not None and profile.topology == "gateway":
      return True
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT and
            cp.networkLocation == CarParams.NetworkLocation.gateway and
            not cp.pcmCruise and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and not cp.radarUnavailable and
            control_flags(cp) == 0 and
            len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            gm_control_word(cp) in (
              int(GMSafetyFlags.EV | GMSafetyFlags.VOLT_GATEWAY_LONG),
              int(GMSafetyFlags.EV | GMSafetyFlags.VOLT_GATEWAY_LONG | GMSafetyFlags.VOLT_GATEWAY_ALT_BRAKE),
              0x4084, 0xC084))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_gateway_longitudinal(cp: CarParams) -> bool:
  return is_volt_gateway_profile(cp) and cp.openpilotLongitudinalControl


def is_lacrosse_gateway_profile(cp: CarParams) -> bool:
  """Exact ICE LaCrosse gateway owner, including its disabled longitudinal choice."""
  try:
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.BUICK_LACROSSE and
            cp.networkLocation == CarParams.NetworkLocation.gateway and not cp.pcmCruise and
            cp.transmissionType == CarParams.TransmissionType.automatic and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and not cp.radarUnavailable and control_flags(cp) == 0 and
            len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            gm_control_word(cp) in (0, 0x80))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_gm_auto_hold(cp: CarParams) -> bool:
  return ((is_volt_gateway_longitudinal(cp) or is_volt_ascm_longitudinal(cp) or is_volt_camera_longitudinal(cp) or
           is_volt_sdgm_profile(cp, longitudinal=True) or is_volt_camera_removed(cp, longitudinal=True) or
           is_lacrosse_gateway_profile(cp) and cp.openpilotLongitudinalControl) and
          gm_control_word(cp) in (0x4084, 0xC084, 0x4287, 0x4687, 0x4A87, 0x4E87, 0x4087, 0x5087, 0x5487,
                                                 0xC1D1, 0xC1D3, 0x80))


def apply_gm_auto_hold(cp: CarParams, enabled: bool) -> None:
  profile = camera_acc_pedal_profile(cp)
  if profile is not None and profile.volt:
    if profile.longitudinal:
      cp.safetyConfigs[0].safetyParam = volt_camera_pedal_word(profile, enabled, profile.one_pedal)
    return
  if is_volt_camera_removed(cp, longitudinal=True):
    cp.safetyConfigs[0].safetyParam = (0xC1D3 if cp.flags & GMFlags.NO_ACCELERATOR_POS_MSG else 0xC1D1) if enabled else 0xC151
    return
  if (is_volt_gateway_profile(cp) or is_volt_ascm_longitudinal(cp) or is_volt_camera_longitudinal(cp) or
      is_volt_sdgm_profile(cp, longitudinal=True) or is_lacrosse_gateway_profile(cp)):
    word = gm_control_word(cp)
    cp.safetyConfigs[0].safetyParam = (word | int(GMSafetyFlags.VOLT_AUTO_HOLD)) if enabled and cp.openpilotLongitudinalControl else (
      word & ~int(GMSafetyFlags.VOLT_AUTO_HOLD))


VOLT_ASCM_REQUIRED_FLAGS = int(GMSafetyFlags.EV | GMSafetyFlags.HW_CAM | GMSafetyFlags.HW_CAM_LONG |
                               GMSafetyFlags.ASCM_INTERCEPT | GMSafetyFlags.VOLT_LONG)
VOLT_ASCM_OPTIONAL_FLAGS = int(GMSafetyFlags.ASCM_BRAKE_C9 | GMSafetyFlags.ASCM_RADAR)
VOLT_ASCM_ALLOWED_FLAGS = VOLT_ASCM_REQUIRED_FLAGS | VOLT_ASCM_OPTIONAL_FLAGS


def is_volt_ascm_longitudinal(cp: CarParams) -> bool:
  """Admit the exact ASCM Volt longitudinal and physical interceptor configurations."""
  try:
    profile = camera_acc_pedal_profile(cp)
    if profile is not None and profile.topology == "ascm":
      return profile.longitudinal
    if len(cp.safetyConfigs) != 1:
      return False
    flags = gm_control_word(cp)
    if flags in (0x4287, 0x4687, 0x4A87, 0x4E87):
      flags &= ~int(GMSafetyFlags.VOLT_AUTO_HOLD)
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT_ASCM and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and cp.alphaLongitudinalAvailable and
            cp.openpilotLongitudinalControl and not cp.pcmCruise and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and control_flags(cp) == 0 and
            cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            flags & VOLT_ASCM_REQUIRED_FLAGS == VOLT_ASCM_REQUIRED_FLAGS and not flags & ~VOLT_ASCM_ALLOWED_FLAGS and
            bool(flags & int(GMSafetyFlags.ASCM_RADAR)) is not cp.radarUnavailable)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_ascm_stock(cp: CarParams) -> bool:
  """Exact non-pedal ASCM Volt steering owner with factory longitudinal control."""
  try:
    configs = cp.safetyConfigs
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT_ASCM and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and cp.pcmCruise and
            not cp.openpilotLongitudinalControl and not cp.passive and not cp.dashcamOnly and
            not cp.notCar and control_flags(cp) == 0 and len(configs) == 1 and
            configs[0].safetyModel == CarParams.SafetyModel.gm and
            int(configs[0].safetyParam) in (0x205, 0x605, 0xA05, 0xE05) and
            bool(int(configs[0].safetyParam) & int(GMSafetyFlags.ASCM_RADAR)) is not cp.radarUnavailable)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_camera_removed(cp: CarParams, *, longitudinal=None) -> bool:
  """The exact manual Volt with PT state sources and no factory camera."""
  try:
    is_long = bool(cp.openpilotLongitudinalControl)
    allowed_flags = int(GMFlags.VOLT_CAMERA_REMOVED | GMFlags.VOLT_CAMERA_NO_ACCEL_POS)
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT_CAMERA and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and
            bool(cp.flags & GMFlags.VOLT_CAMERA_REMOVED) and not control_flags(cp) & ~allowed_flags and
            (longitudinal is None or is_long == longitudinal) and cp.pcmCruise != is_long and
            (not is_long or cp.alphaLongitudinalAvailable) and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and len(cp.safetyConfigs) == 1 and
            cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            (gm_control_word(cp) == (0xC151 if is_long else 0xC150) or
             is_long and gm_control_word(cp) == (0xC1D3 if cp.flags & GMFlags.NO_ACCELERATOR_POS_MSG else 0xC1D1)))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_camera_stock(cp: CarParams) -> bool:
  """Camera-present manual stock owner, without borrowing another GM identity."""
  try:
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT_CAMERA and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and cp.pcmCruise and
            not cp.openpilotLongitudinalControl and not cp.passive and not cp.dashcamOnly and
            not cp.notCar and control_flags(cp) == 0 and len(cp.safetyConfigs) == 1 and
            cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and gm_control_word(cp) == 5)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_camera_longitudinal(cp: CarParams) -> bool:
  """Exact manual camera Volt development owner; stock and other topologies stay separate."""
  try:
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT_CAMERA and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and cp.alphaLongitudinalAvailable and
            cp.openpilotLongitudinalControl and not cp.pcmCruise and not cp.passive and not cp.dashcamOnly and
            not cp.notCar and control_flags(cp) == 0 and len(cp.safetyConfigs) == 1 and
            cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            gm_control_word(cp) in (0x4007, 0x4087))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_sdgm_profile(cp: CarParams, *, longitudinal=False) -> bool:
  try:
    profile = camera_acc_pedal_profile(cp)
    if profile is not None and profile.topology == "sdgm":
      return profile.longitudinal == longitudinal
    words = (0x5007, 0x5407, 0x5087, 0x5487) if longitudinal else (0x1005, 0x1405)
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT_2019 and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and not cp.passive and not cp.dashcamOnly and
            not cp.notCar and control_flags(cp) == 0 and cp.openpilotLongitudinalControl == longitudinal and
            cp.pcmCruise != longitudinal and (not longitudinal or cp.alphaLongitudinalAvailable) and
            len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            gm_control_word(cp) in words)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_sdgm_accepted_envelope(cp: CarParams) -> bool:
  return (camera_acc_pedal_profile(cp) is None and is_volt_sdgm_profile(cp, longitudinal=True) and
          gm_control_word(cp) in (0x5007, 0x5407, 0x5087, 0x5487))


def volt_sdgm_accepted_accel_max(cp: CarParams, speed: float) -> float:
  try:
    mass, wheelbase = cp.mass, cp.wheelbase
    if not (isfinite(mass) and mass > 0.0 and isfinite(wheelbase) and wheelbase > 0.0 and
            isfinite(speed) and speed >= 0.0):
      return 0.0
    # Invert the unchanged positive demand map, including its aero term.
    aero_accel = 0.5 * 0.30 * (1.05 * wheelbase + 0.0679) * 1.225 * speed ** 2 / mass
    if not isfinite(aero_accel):
      return 0.0
    return max(0.0, CarControllerParams.ACCEL_MAX * 2041.0 / 2698.0 - aero_accel)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return 0.0


def is_volt_longitudinal(cp: CarParams) -> bool:
  return (is_volt_gateway_longitudinal(cp) or is_volt_ascm_longitudinal(cp) or is_volt_camera_longitudinal(cp) or
          is_volt_sdgm_profile(cp, longitudinal=True) or is_volt_camera_removed(cp, longitudinal=True))


def is_volt_gateway_alternate_brake(cp: CarParams) -> bool:
  """The exact gateway Volt profile using EBCM pedal input and PT friction output."""
  return is_volt_gateway_profile(cp) and bool(gm_control_word(cp) & GMSafetyFlags.VOLT_GATEWAY_ALT_BRAKE)


class Footnote(Enum):
  SETUP = CarFootnote(
    "See more setup details for <a href=\"https://github.com/commaai/openpilot/wiki/gm\" target=\"_blank\">GM</a>.",
    Column.MAKE, setup_note=True)


@dataclass
class GMCarDocs(CarDocs):
  package: str = "Adaptive Cruise Control (ACC)"

  def init_make(self, CP: CarParams):
    if (CP.carFingerprint in ASCM_INTERCEPT_CAR or CP.carFingerprint in CC_GATEWAY_STOCK_CAR or
        CP.carFingerprint in (CAR.CHEVROLET_VOLT_CC, CAR.CHEVROLET_MALIBU_HYBRID_CC)):
      self.car_parts = CarParts.common([CarHarness.custom])
    elif CP.networkLocation == CarParams.NetworkLocation.fwdCamera:
      if CP.carFingerprint in SDGM_CAR:
        self.car_parts = CarParts.common([CarHarness.gmsdgm])
      else:
        self.car_parts = CarParts.common([CarHarness.gm])
    else:
      self.footnotes.insert(0, Footnote.SETUP)
      self.car_parts = CarParts.common([CarHarness.obd_ii])


@dataclass
class GMCommunityCarDocs(GMCarDocs):
  merged: bool = False
  support_type: SupportType = SupportType.COMMUNITY
  support_link: str = "#community"


@dataclass(frozen=True, kw_only=True)
class GMCarSpecs(CarSpecs):
  tireStiffnessFactor: float = 0.444  # not optimized yet


@dataclass
class GMPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: {
    Bus.pt: 'gm_global_a_powertrain_generated',
    Bus.radar: 'gm_global_a_object',
    Bus.chassis: 'gm_global_a_chassis',
  })


class CAR(Platforms):
  HOLDEN_ASTRA = GMPlatformConfig(
    [GMCommunityCarDocs("Holden Astra (OBD-II Harness) 2017")],
    GMCarSpecs(mass=1363, wheelbase=2.662, steerRatio=15.7, centerToFrontRatio=0.4),
  )
  CHEVROLET_VOLT = GMPlatformConfig(
    [GMCarDocs("Chevrolet Volt (OBD-II Harness) 2017-18", "ACC or flashed bypass (alpha long)", support_type=SupportType.COMMUNITY, support_link="#community",
               min_enable_speed=0, video="https://youtu.be/QeMCN_4TFfQ")],
    GMCarSpecs(mass=1607, wheelbase=2.69, steerRatio=15.7, centerToFrontRatio=0.45, tireStiffnessFactor=1.0),
  )
  CHEVROLET_VOLT_CC = GMPlatformConfig(
    [GMCarDocs("Chevrolet Volt (CC)", "Manual development configuration; installation must be verified",
               merged=False, support_type=SupportType.COMMUNITY, support_link="#community")],
    CHEVROLET_VOLT.specs, dbc_dict=CHEVROLET_VOLT.dbc_dict,
  )
  CHEVROLET_VOLT_ASCM = GMPlatformConfig(
    [GMCarDocs("Chevrolet Volt (ASCM Harness) 2017-18", "Adaptive Cruise Control (ACC); custom ASCM harness install",
               support_type=SupportType.COMMUNITY, support_link="#community")], CHEVROLET_VOLT.specs,
  )
  CADILLAC_ATS = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac ATS Premium Performance (OBD-II Harness) 2018")],
    GMCarSpecs(mass=1601, wheelbase=2.78, steerRatio=15.3),
  )
  CHEVROLET_MALIBU = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Malibu Premier (OBD-II Harness) 2017")],
    GMCarSpecs(mass=1496, wheelbase=2.83, steerRatio=15.8, centerToFrontRatio=0.4),
  )
  CHEVROLET_MALIBU_ASCM = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Malibu (ASCM Harness) 2017-19")], CHEVROLET_MALIBU.specs,
  )
  GMC_ACADIA = GMPlatformConfig(
    [GMCommunityCarDocs("GMC Acadia (OBD-II Harness) 2018", video="https://www.youtube.com/watch?v=0ZN6DdsBUZo")],
    GMCarSpecs(mass=1975, wheelbase=2.86, steerRatio=14.4, centerToFrontRatio=0.4),
  )
  GMC_ACADIA_ASCM = GMPlatformConfig(
    [GMCommunityCarDocs("GMC Acadia (ASCM Harness) 2018")], GMC_ACADIA.specs,
  )
  BUICK_LACROSSE = GMPlatformConfig(
    [GMCommunityCarDocs("Buick LaCrosse (OBD-II Harness) 2017-19", "Driver Confidence Package 2")],
    GMCarSpecs(mass=1712, wheelbase=2.91, steerRatio=15.8, centerToFrontRatio=0.4),
  )
  BUICK_LACROSSE_ASCM = GMPlatformConfig(
    [GMCommunityCarDocs("Buick LaCrosse (ASCM Harness) 2017-19")], BUICK_LACROSSE.specs,
  )
  BUICK_LACROSSE_ASCM_19US = GMPlatformConfig(
    [GMCommunityCarDocs("Buick LaCrosse US (ASCM Harness) 2019")], BUICK_LACROSSE.specs,
  )
  BUICK_REGAL = GMPlatformConfig(
    [GMCommunityCarDocs("Buick Regal Essence (OBD-II Harness) 2018")],
    GMCarSpecs(mass=1714, wheelbase=2.83, steerRatio=14.4, centerToFrontRatio=0.4),
  )
  CADILLAC_ESCALADE = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac Escalade (OBD-II Harness) 2017", "Driver Assist Package")],
    GMCarSpecs(mass=2564, wheelbase=2.95, steerRatio=17.3),
  )
  CADILLAC_ESCALADE_ASCM = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac Escalade (ASCM Harness) 2018")], CADILLAC_ESCALADE.specs,
  )
  CADILLAC_ESCALADE_ESV = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac Escalade ESV (OBD-II Harness) 2016", "Adaptive Cruise Control (ACC) & LKAS")],
    GMCarSpecs(mass=2739, wheelbase=3.302, steerRatio=17.3, tireStiffnessFactor=1.0),
  )
  CADILLAC_ESCALADE_ESV_2019 = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac Escalade ESV (OBD-II Harness) 2019", "Adaptive Cruise Control (ACC) & LKAS")],
    CADILLAC_ESCALADE_ESV.specs,
  )
  CADILLAC_ESCALADE_ESV_2019_ASCM = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac Escalade ESV Platinum (ASCM Harness) 2019")], CADILLAC_ESCALADE_ESV_2019.specs,
  )
  CHEVROLET_SUBURBAN = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Suburban (OBD-II Harness)", "Configuration-specific install; observed CAN sources required")],
    CarSpecs(mass=2731, wheelbase=3.302, steerRatio=17.3, centerToFrontRatio=0.49),
  )
  CHEVROLET_SUBURBAN_ASCM = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Suburban Premier (ASCM Harness) 2016-20")],
    CHEVROLET_SUBURBAN.specs,
  )
  CHEVROLET_BOLT_EUV = GMPlatformConfig(
    [
      GMCarDocs("Chevrolet Bolt EUV 2022-23", "Premier or Premier Redline Trim, without Super Cruise Package", video="https://youtu.be/xvwzGMUA210"),
      GMCarDocs("Chevrolet Bolt EV 2022-23", "2LT Trim with Adaptive Cruise Control Package"),
    ],
    GMCarSpecs(mass=1669, wheelbase=2.63779, steerRatio=16.8, centerToFrontRatio=0.4, tireStiffnessFactor=1.0),
  )
  CHEVROLET_BOLT_ACC_2022_2023 = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Bolt ACC 2022-23", "ACC; recognized camera layout required")],
    CHEVROLET_BOLT_EUV.specs,
  )
  CHEVROLET_BOLT_CC_2017 = GMPlatformConfig(
    [GMCarDocs("Chevrolet Bolt EV 2017", "Cruise control with installed pedal interceptor")],
    CHEVROLET_BOLT_EUV.specs,
  )
  CHEVROLET_BOLT_CC_2018_2021 = GMPlatformConfig(
    [GMCarDocs("Chevrolet Bolt EV 2018-21", "Cruise control with installed pedal interceptor")],
    CHEVROLET_BOLT_EUV.specs,
  )
  CHEVROLET_BOLT_CC_2022_2023 = GMPlatformConfig(
    [GMCarDocs("Chevrolet Bolt EV (cruise control with pedal interceptor) 2022-23", "Cruise control with installed pedal interceptor"),
     GMCarDocs("Chevrolet Bolt EUV (cruise control with pedal interceptor) 2022-23", "Cruise control with installed pedal interceptor")],
    CHEVROLET_BOLT_EUV.specs,
  )
  CHEVROLET_BOLT_ACC_2022_2023_PEDAL = GMPlatformConfig(
    [GMCarDocs("Chevrolet Bolt EV (ACC with pedal interceptor) 2022-23", "ACC with installed pedal interceptor"),
     GMCarDocs("Chevrolet Bolt EUV (ACC with pedal interceptor) 2022-23", "ACC with installed pedal interceptor")],
    CHEVROLET_BOLT_EUV.specs,
  )
  CHEVROLET_SILVERADO = GMPlatformConfig(
    [
      GMCarDocs("Chevrolet Silverado 1500 2020-21", "Safety Package II"),
      GMCarDocs("GMC Sierra 1500 2020-21", "Driver Alert Package II", video="https://youtu.be/5HbNoBLzRwE"),
    ],
    GMCarSpecs(mass=2994, wheelbase=3.75, steerRatio=16.3, tireStiffnessFactor=1.0),
  )
  CHEVROLET_EQUINOX = GMPlatformConfig(
    [GMCarDocs("Chevrolet Equinox 2019-22")],
    GMCarSpecs(mass=1588, wheelbase=2.72, steerRatio=14.4, centerToFrontRatio=0.4),
  )
  CHEVROLET_TRAILBLAZER = GMPlatformConfig(
    [GMCarDocs("Chevrolet Trailblazer 2021-22")],
    GMCarSpecs(mass=1345, wheelbase=2.64, steerRatio=16.8, centerToFrontRatio=0.4, tireStiffnessFactor=1.0),
  )
  CADILLAC_XT4 = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac XT4 (SDGM Harness) 2023", "Driver Assist Package")],
    GMCarSpecs(mass=1660, wheelbase=2.78, steerRatio=14.4, centerToFrontRatio=0.4),
  )
  CADILLAC_XT5 = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac XT5 (SDGM Harness) 2022", "Driver Assist Package")],
    GMCarSpecs(mass=1810, wheelbase=2.86, steerRatio=16.34, centerToFrontRatio=0.5, tireStiffnessFactor=1.0),
  )
  CADILLAC_XT6 = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac XT6 (SDGM Harness) 2020", "Driver Assist Package")],
    GMCarSpecs(mass=2050, wheelbase=2.86, steerRatio=16.5, centerToFrontRatio=0.4),
  )
  CHEVROLET_BLAZER = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Blazer (SDGM Harness) 2019-25", "Driver Assist Package")],
    GMCarSpecs(mass=1850, wheelbase=3.10, steerRatio=17.9, centerToFrontRatio=0.4, tireStiffnessFactor=1.0),
  )
  CHEVROLET_VOLT_2019 = GMPlatformConfig(
    [GMCarDocs("Chevrolet Volt (SDGM Harness) 2019", "Adaptive Cruise Control (ACC) & LKAS",
               support_type=SupportType.COMMUNITY, support_link="#community")],
    GMCarSpecs(mass=1607, wheelbase=2.69, steerRatio=15.7, centerToFrontRatio=0.45),
  )
  CHEVROLET_TRAVERSE = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Traverse (SDGM Harness) 2022-23", "RS, Premier, or High Country Trim")],
    GMCarSpecs(mass=1955, wheelbase=3.07, steerRatio=17.9, centerToFrontRatio=0.4),
  )
  CHEVROLET_MALIBU_SDGM = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Malibu (SDGM Harness) 2019", "SDGM Harness")],
    CHEVROLET_MALIBU.specs,
  )
  BUICK_BABYENCLAVE = GMPlatformConfig(
    [GMCommunityCarDocs("Buick Baby Enclave (SDGM Harness) 2020-23", "Driver Assist Package")],
    GMCarSpecs(mass=2050, wheelbase=2.86, steerRatio=16.0, centerToFrontRatio=0.5, tireStiffnessFactor=1.0),
  )
  GMC_YUKON = GMPlatformConfig(
    [GMCarDocs("GMC Yukon 2019-20", "Adaptive Cruise Control (ACC) & LKAS")],
    GMCarSpecs(mass=2490, wheelbase=2.94, steerRatio=17.3, centerToFrontRatio=0.5, tireStiffnessFactor=1.0),
  )
  CHEVROLET_SUBURBAN_CAMERA = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Suburban (Camera Harness)", "Manual camera configuration; recognized CAN layout required")],
    CarSpecs(mass=2731, wheelbase=3.302, steerRatio=17.3, centerToFrontRatio=0.49),
  )
  CHEVROLET_TRAX = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Trax (Camera Harness)", "Manual camera configuration; recognized CAN layout required")],
    CarSpecs(mass=1365, wheelbase=2.7, steerRatio=16.4, centerToFrontRatio=0.4),
  )
  CHEVROLET_VOLT_CAMERA = GMPlatformConfig(
    [GMCarDocs("Chevrolet Volt (Camera Harness)", "Manual configuration; recognized camera layout required",
               merged=False, support_type=SupportType.COMMUNITY, support_link="#community")],
    GMCarSpecs(mass=1607, wheelbase=2.69, steerRatio=15.7, centerToFrontRatio=0.45, tireStiffnessFactor=1.0),
    dbc_dict=CHEVROLET_VOLT.dbc_dict,
  )
  CADILLAC_CT6_CC = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac CT6 (CC)", "Cruise control; configuration-specific installation")],
    GMCarSpecs(mass=2358, wheelbase=3.11, steerRatio=17.7, centerToFrontRatio=0.4, tireStiffnessFactor=1.0),
  )
  CADILLAC_XT4_CC = GMPlatformConfig([GMCommunityCarDocs("Cadillac XT4 (CC)", "Cruise control; configuration-specific installation")], CADILLAC_XT4.specs)
  CADILLAC_XT5_CC = GMPlatformConfig(
    [GMCommunityCarDocs("Cadillac XT5 (CC)", "Cruise control; configuration-specific installation")],
    replace(CADILLAC_XT5.specs, tireStiffnessFactor=1.0),
  )
  CHEVROLET_EQUINOX_CC = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Equinox (CC)", "Cruise control; configuration-specific installation")],
    CHEVROLET_EQUINOX.specs,
  )
  CHEVROLET_MALIBU_HYBRID_CC = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Malibu Hybrid (CC)", "Cruise control; configuration-specific installation")],
    GMCarSpecs(mass=1450, wheelbase=2.8, steerRatio=15.8, centerToFrontRatio=0.4),
  )
  CHEVROLET_MALIBU_CC = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Malibu (CC)", "Cruise control; configuration-specific installation")],
    GMCarSpecs(mass=1450, wheelbase=2.8, steerRatio=18.25,
                                                                  centerToFrontRatio=0.4, tireStiffnessFactor=0.997),
  )
  CHEVROLET_SILVERADO_CC = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Silverado (CC)", "Cruise control; configuration-specific installation"),
     GMCommunityCarDocs("GMC Sierra 1500 No-ACC 2020-21", "Cruise control; configuration-specific installation")],
    GMCarSpecs(mass=2994, wheelbase=3.75, steerRatio=16.3, tireStiffnessFactor=1.0),
  )
  CHEVROLET_SUBURBAN_CC = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Suburban (CC)", "Cruise control; configuration-specific installation")],
    replace(CHEVROLET_SUBURBAN_ASCM.specs, tireStiffnessFactor=1.0),
  )
  CHEVROLET_TRAILBLAZER_CC = GMPlatformConfig(
    [GMCommunityCarDocs("Chevrolet Trailblazer (CC)", "Cruise control; configuration-specific installation")],
    CHEVROLET_TRAILBLAZER.specs,
  )
  GMC_YUKON_CC = GMPlatformConfig(
    [GMCommunityCarDocs("GMC Yukon (CC)", "Cruise control; configuration-specific installation")],
    GMCarSpecs(mass=2541, wheelbase=2.95, steerRatio=16.3, centerToFrontRatio=0.4, tireStiffnessFactor=1.0),
  )


class CruiseButtons:
  INIT = 0
  UNPRESS = 1
  RES_ACCEL = 2
  DECEL_SET = 3
  MAIN = 5
  CANCEL = 6


class AccState:
  OFF = 0
  ACTIVE = 1
  FAULTED = 3
  STANDSTILL = 4


class CanBus:
  POWERTRAIN = 0
  OBSTACLE = 1
  CAMERA = 2
  CHASSIS = 2
  LOOPBACK = 128
  DROPPED = 192


# In a Data Module, an identifier is a string used to recognize an object,
# either by itself or together with the identifiers of parent objects.
# Each returns a 4 byte hex representation of the decimal part number. `b"\x02\x8c\xf0'"` -> 42790951
GM_BOOT_SOFTWARE_PART_NUMER_REQUEST = b'\x1a\xc0'  # likely does not contain anything useful
GM_SOFTWARE_MODULE_1_REQUEST = b'\x1a\xc1'
GM_SOFTWARE_MODULE_2_REQUEST = b'\x1a\xc2'
GM_SOFTWARE_MODULE_3_REQUEST = b'\x1a\xc3'

# Part number of XML data file that is used to configure ECU
GM_XML_DATA_FILE_PART_NUMBER = b'\x1a\x9c'
GM_XML_CONFIG_COMPAT_ID = b'\x1a\x9b'  # used to know if XML file is compatible with the ECU software/hardware

# This DID is for identifying the part number that reflects the mix of hardware,
# software, and calibrations in the ECU when it first arrives at the vehicle assembly plant.
# If there's an Alpha Code, it's associated with this part number and stored in the DID $DB.
GM_END_MODEL_PART_NUMBER_REQUEST = b'\x1a\xcb'
GM_END_MODEL_PART_NUMBER_ALPHA_CODE_REQUEST = b'\x1a\xdb'
GM_BASE_MODEL_PART_NUMBER_REQUEST = b'\x1a\xcc'
GM_BASE_MODEL_PART_NUMBER_ALPHA_CODE_REQUEST = b'\x1a\xdc'
GM_FW_RESPONSE = b'\x5a'

GM_FW_REQUESTS = [
  GM_BOOT_SOFTWARE_PART_NUMER_REQUEST,
  GM_SOFTWARE_MODULE_1_REQUEST,
  GM_SOFTWARE_MODULE_2_REQUEST,
  GM_SOFTWARE_MODULE_3_REQUEST,
  GM_XML_DATA_FILE_PART_NUMBER,
  GM_XML_CONFIG_COMPAT_ID,
  GM_END_MODEL_PART_NUMBER_REQUEST,
  GM_END_MODEL_PART_NUMBER_ALPHA_CODE_REQUEST,
  GM_BASE_MODEL_PART_NUMBER_REQUEST,
  GM_BASE_MODEL_PART_NUMBER_ALPHA_CODE_REQUEST,
]

GM_RX_OFFSET = 0x400

FW_QUERY_CONFIG = FwQueryConfig(
  fw_version_regex=br"[\x00-\xff]+",
  requests=[request for req in GM_FW_REQUESTS for request in [
    Request(
      [StdQueries.SHORT_TESTER_PRESENT_REQUEST, req],
      [StdQueries.SHORT_TESTER_PRESENT_RESPONSE, GM_FW_RESPONSE + bytes([req[-1]])],
      rx_offset=GM_RX_OFFSET,
      bus=0,
      logging=True,
    ),
  ]],
  extra_ecus=[(Ecu.fwdCamera, 0x24b, None)],
)

# TODO: detect most of these sets live
PEDAL_BOLT_CAR = {
  CAR.CHEVROLET_BOLT_CC_2017,
  CAR.CHEVROLET_BOLT_CC_2018_2021,
  CAR.CHEVROLET_BOLT_CC_2022_2023,
  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
}
NO_ACC_BOLT_CAR = PEDAL_BOLT_CAR - {CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL}
VOLT_BSM_CAR = {CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_2019,
                CAR.CHEVROLET_VOLT_CAMERA}
EV_CAR = {CAR.CHEVROLET_MALIBU_HYBRID_CC, CAR.CHEVROLET_VOLT_CC, CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_2019,
          CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023} | PEDAL_BOLT_CAR

ASCM_INTERCEPT_CAR = {
  CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_MALIBU_ASCM, CAR.GMC_ACADIA_ASCM,
  CAR.BUICK_LACROSSE_ASCM, CAR.BUICK_LACROSSE_ASCM_19US, CAR.CADILLAC_ESCALADE_ASCM,
  CAR.CADILLAC_ESCALADE_ESV_2019_ASCM, CAR.CHEVROLET_SUBURBAN_ASCM,
}

# We're integrated at the camera with VOACC on these cars (instead of ASCM w/ OBD-II harness)
CAMERA_STOCK_CAR = {CAR.CHEVROLET_SUBURBAN_CAMERA,
                    CAR.CHEVROLET_TRAX, CAR.CHEVROLET_VOLT_CAMERA}
CAMERA_ACC_CAR = ({CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023, CAR.CHEVROLET_SILVERADO, CAR.CHEVROLET_EQUINOX,
                   CAR.CHEVROLET_TRAILBLAZER, CAR.GMC_YUKON} | PEDAL_BOLT_CAR | CAMERA_STOCK_CAR)

# Alt ASCMActiveCruiseControlStatus
ALT_ACCS = {CAR.GMC_YUKON, CAR.CHEVROLET_SUBURBAN_CAMERA}

# We're integrated at the Safety Data Gateway Module on these cars
SDGM_STOCK_CAR = {
  CAR.CADILLAC_XT5, CAR.CADILLAC_XT6, CAR.BUICK_BABYENCLAVE,
  CAR.CHEVROLET_BLAZER, CAR.CHEVROLET_MALIBU_SDGM,
}
SDGM_CANCEL_PT_CAR = {CAR.CADILLAC_XT4, CAR.CADILLAC_XT5, CAR.CADILLAC_XT6, CAR.BUICK_BABYENCLAVE}
SDGM_CAR = {CAR.CADILLAC_XT4, CAR.CHEVROLET_VOLT_2019, CAR.CHEVROLET_TRAVERSE} | SDGM_STOCK_CAR

CC_GATEWAY_STOCK_CAR = {
  CAR.CADILLAC_CT6_CC, CAR.CADILLAC_XT4_CC, CAR.CADILLAC_XT5_CC,
  CAR.CHEVROLET_EQUINOX_CC, CAR.CHEVROLET_MALIBU_CC, CAR.CHEVROLET_SILVERADO_CC,
  CAR.CHEVROLET_SUBURBAN_CC, CAR.CHEVROLET_TRAILBLAZER_CC, CAR.GMC_YUKON_CC,
}

STEER_THRESHOLD = 1.0

DBC = CAR.create_dbc_map()


VOLT_CC_F1_WORD = 0xC163


def is_volt_cc_f1_profile(cp: CarParams) -> bool:
  return is_volt_cc_profile(cp) and bool(cp.flags & GMFlags.NO_ACCELERATOR_POS_MSG)


def is_volt_cc_profile(cp: CarParams) -> bool:
  """Exact development-only conventional-cruise owner, with either startup speed choice."""
  try:
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT_CC and
            cp.networkLocation == CarParams.NetworkLocation.gateway and not cp.alphaLongitudinalAvailable and
            not cp.pcmCruise and cp.radarUnavailable and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and
            control_flags(cp) == int(GMFlags.CC_LONG | (GMFlags.NO_ACCELERATOR_POS_MSG if cp.flags & GMFlags.NO_ACCELERATOR_POS_MSG else 0)) and
            len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            int(cp.safetyConfigs[0].safetyParam) ==
            (VOLT_CC_F1_WORD if cp.flags & GMFlags.NO_ACCELERATOR_POS_MSG else int(GMSafetyFlags.EV | GMSafetyFlags.NO_ACC)))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_volt_cc_longitudinal(cp: CarParams) -> bool:
  return is_volt_cc_profile(cp) and cp.openpilotLongitudinalControl


BOLT_CC_WORDS = {
  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL: (0xC140, 0xC141),
  CAR.CHEVROLET_BOLT_CC_2017: (0xC110, 0xC111),
  CAR.CHEVROLET_BOLT_CC_2018_2021: (0xC120, 0xC121),
  CAR.CHEVROLET_BOLT_CC_2022_2023: (0xC130, 0xC131),
}

def is_bolt_cc_profile(cp):
  return (cp.brand == "gm" and cp.carFingerprint in BOLT_CC_WORDS and
          len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
          cp.networkLocation == CarParams.NetworkLocation.fwdCamera and
          not (cp.passive or cp.dashcamOnly or cp.notCar or cp.alphaLongitudinalAvailable) and
          bool(cp.flags & GMFlags.CC_LONG.value) and not cp.flags & GMFlags.PEDAL_LONG.value and
          not cp.pcmCruise and cp.safetyConfigs[0].safetyParam is not None and
          cp.safetyConfigs[0].safetyParam in BOLT_CC_WORDS[cp.carFingerprint])

_BOLT_PEDAL_BASE = GMSafetyFlags.HW_CAM | GMSafetyFlags.EV | GMSafetyFlags.PEDAL_LONG | GMSafetyFlags.PADDLE_SCHED
BOLT_PEDAL_WORDS = MappingProxyType({
  CAR.CHEVROLET_BOLT_CC_2017: _BOLT_PEDAL_BASE | GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_2017,
  CAR.CHEVROLET_BOLT_CC_2018_2021: _BOLT_PEDAL_BASE | GMSafetyFlags.NO_ACC,
  CAR.CHEVROLET_BOLT_CC_2022_2023: _BOLT_PEDAL_BASE | GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_GEN2,
  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL: _BOLT_PEDAL_BASE | GMSafetyFlags.BOLT_ACC_PEDAL | GMSafetyFlags.BOLT_GEN2,
})
BOLT_PEDAL_STOCK_WORDS = MappingProxyType({**BOLT_PEDAL_WORDS,
                                         CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL: GMSafetyFlags.HW_CAM | GMSafetyFlags.EV})


BOLT_PEDAL_REMOVED_CARS = (CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021,
                           CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL)
BOLT_PEDAL_REMOVED_WORDS = MappingProxyType({candidate: 0xE700 + index for index, candidate in enumerate(BOLT_PEDAL_REMOVED_CARS)})
BOLT_PEDAL_REMOVED_STOCK_WORDS = MappingProxyType({candidate: 0xE710 + index for index, candidate in enumerate(BOLT_PEDAL_REMOVED_CARS)})
_BOLT_REMOVED_SAFETY_WORDS = frozenset((*BOLT_PEDAL_REMOVED_WORDS.values(), *BOLT_PEDAL_REMOVED_STOCK_WORDS.values()))


def is_bolt_pedal_removed_profile(cp, *, stock_only=False):
  words = BOLT_PEDAL_REMOVED_STOCK_WORDS if stock_only else BOLT_PEDAL_REMOVED_WORDS
  try:
    return (cp.brand == 'gm' and cp.carFingerprint in words and
            int(cp.flags) & _GM_CONTROL_FLAGS_MASK == _BOLT_REMOVED_CONTROL_FLAGS and
            cp.transmissionType == CarParams.TransmissionType.direct and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and cp.radarUnavailable and
            not (cp.passive or cp.dashcamOnly or cp.notCar or cp.alphaLongitudinalAvailable) and
            bool(cp.openpilotLongitudinalControl) is not stock_only and bool(cp.pcmCruise) is stock_only and
            len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            int(cp.safetyConfigs[0].safetyParam) == words[cp.carFingerprint])
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_bolt_pedal_profile(cp, *, stock_only=False):
  if cp.brand != 'gm' or cp.carFingerprint not in BOLT_PEDAL_WORDS:
    return False
  return _bolt_pedal_profile(cp, stock_only=stock_only)


@cached_config
def _bolt_pedal_profile(cp, *, stock_only=False):
  """Exact selected interceptor profile, including its startup authority reduction."""
  if is_bolt_pedal_removed_profile(cp, stock_only=stock_only):
    return True
  words = BOLT_PEDAL_STOCK_WORDS if stock_only else BOLT_PEDAL_WORDS
  return (cp.brand == 'gm' and cp.carFingerprint in words and
          cp.networkLocation == CarParams.NetworkLocation.fwdCamera and
          not (cp.passive or cp.dashcamOnly or cp.notCar or cp.alphaLongitudinalAvailable) and
          bool(cp.openpilotLongitudinalControl) is not stock_only and bool(cp.pcmCruise) is stock_only and
          int(cp.flags) & _GM_CONTROL_FLAGS_MASK == _GM_PEDAL_LONG_FLAG and len(cp.safetyConfigs) == 1 and
          cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
          cp.safetyConfigs[0].safetyParam == words[cp.carFingerprint])


def is_bolt_present_no_acc_pedal_profile(cp, *, stock_only=False):
  """Exact camera-present conventional Bolt interceptor cancellation owner."""
  return (cp.carFingerprint in NO_ACC_BOLT_CAR and is_bolt_pedal_profile(cp, stock_only=stock_only) and
          not is_bolt_pedal_removed_profile(cp, stock_only=stock_only))


def is_bolt_pedal_stock_denied(cp):
  """Recognize only the saved-disable topology denial, for recovery UI."""
  return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and
          cp.networkLocation == CarParams.NetworkLocation.fwdCamera and
          cp.passive and cp.dashcamOnly and not cp.notCar and not cp.alphaLongitudinalAvailable and
          not cp.openpilotLongitudinalControl and cp.pcmCruise and control_flags(cp) == GMFlags.PEDAL_LONG.value and
          len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.noOutput and
          cp.safetyConfigs[0].safetyParam == 0)


ORDINARY_ASCM_CAR = ASCM_INTERCEPT_CAR - {CAR.CHEVROLET_VOLT_ASCM}


def is_ordinary_ascm_profile(cp, *, longitudinal):
  """Exact ordinary ASCM source and authority selection."""
  try:
    required = int(GMSafetyFlags.HW_CAM | GMSafetyFlags.ASCM_INTERCEPT)
    if longitudinal:
      required |= int(GMSafetyFlags.HW_CAM_LONG)
    optional = int(GMSafetyFlags.ASCM_BRAKE_C9 | GMSafetyFlags.ASCM_RADAR)
    configs = cp.safetyConfigs
    word = int(configs[0].safetyParam)
    return (cp.brand == "gm" and cp.carFingerprint in ORDINARY_ASCM_CAR and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and control_flags(cp) == 0 and
            cp.openpilotLongitudinalControl == longitudinal and cp.pcmCruise != longitudinal and
            (not longitudinal or cp.alphaLongitudinalAvailable) and
            len(configs) == 1 and configs[0].safetyModel == CarParams.SafetyModel.gm and
            word & ~optional == required and
            bool(word & int(GMSafetyFlags.ASCM_RADAR)) == (not cp.radarUnavailable))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


ORDINARY_SDGM_CAR = SDGM_CAR - {CAR.CHEVROLET_VOLT_2019}

def is_ordinary_sdgm_profile(cp, *, longitudinal):
  """Exact ordinary SDGM source and authority selection."""
  try:
    required = int(GMSafetyFlags.HW_CAM | GMSafetyFlags.SDGM)
    if longitudinal:
      required |= int(GMSafetyFlags.HW_CAM_LONG)
    elif cp.carFingerprint in SDGM_CANCEL_PT_CAR:
      required |= int(GMSafetyFlags.SDGM_CANCEL_PT)
    optional = int(GMSafetyFlags.BRAKE_C9)
    configs = cp.safetyConfigs
    return (cp.brand == 'gm' and cp.carFingerprint in ORDINARY_SDGM_CAR and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and control_flags(cp) == 0 and
            cp.openpilotLongitudinalControl == longitudinal and cp.pcmCruise != longitudinal and
            (not longitudinal or cp.alphaLongitudinalAvailable) and
            len(configs) == 1 and configs[0].safetyModel == CarParams.SafetyModel.gm and
            int(configs[0].safetyParam) & ~optional == required)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


SILVERADO_CC_STOCK_SOURCES = {
  0x184: 8, 0x17D: 6, 0x348: 5, 0x34A: 5, 0x232: 8, 0x1E5: 8,
  0x1F5: 8, 0x1C4: 8, 0xC9: 8, 0x140: 3, 0x12A: 8, 0x1F1: 8,
  0x1E1: 7, 0x3D1: 8,
}


def is_silverado_cc_stock_profile(cp):
  """Observed conventional-cruise Silverado, without interceptor authority."""
  try:
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_SILVERADO_CC and
            cp.networkLocation == CarParams.NetworkLocation.gateway and
            cp.transmissionType == CarParams.TransmissionType.automatic and cp.radarUnavailable and
            cp.pcmCruise and not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and control_flags(cp) == 0 and
            len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            int(cp.safetyConfigs[0].safetyParam) == int(GMSafetyFlags.NO_ACC))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


ORDINARY_CC_CAR = CC_GATEWAY_STOCK_CAR - {CAR.CHEVROLET_SILVERADO_CC}
ORDINARY_CC_WORD = 0xC160

MALIBU_CC_F1_WORD = 0xC161
MALIBU_CC_F1_STOCK_WORD = 0xC162
MALIBU_CC_F1_SOURCES = {0x184: 8, 0x34A: 5, 0x348: 5, 0x1E1: 7, 0xF1: 6, 0x1C4: 8,
                        0xC9: 8, 0x3D1: 8, 0x1F5: 8, 0x232: 8, 0x1E5: 8, 0x140: 3,
                        0x12A: 8, 0x1F1: 8, 0x17D: 6}


def is_malibu_cc_f1_profile(cp):
  try:
    configs = cp.safetyConfigs
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_MALIBU_CC and
            cp.networkLocation == CarParams.NetworkLocation.gateway and cp.radarUnavailable and
            not cp.alphaLongitudinalAvailable and not cp.passive and not cp.dashcamOnly and not cp.notCar and
            control_flags(cp) == int(GMFlags.CC_LONG | GMFlags.NO_ACCELERATOR_POS_MSG) and len(configs) == 1 and
            configs[0].safetyModel == CarParams.SafetyModel.gm and
            int(configs[0].safetyParam) == (MALIBU_CC_F1_WORD if cp.openpilotLongitudinalControl else MALIBU_CC_F1_STOCK_WORD) and
            cp.pcmCruise == (not cp.openpilotLongitudinalControl))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_ordinary_cc_profile(cp):
  """Exact non-Volt conventional-cruise source; no pedal ownership."""
  if is_malibu_cc_f1_profile(cp):
    return True
  try:
    configs = cp.safetyConfigs
    return (cp.brand == 'gm' and cp.carFingerprint in (ORDINARY_CC_CAR | {CAR.CHEVROLET_SILVERADO_CC}) and
            cp.networkLocation == CarParams.NetworkLocation.gateway and cp.radarUnavailable and not cp.pcmCruise and
            not cp.alphaLongitudinalAvailable and not cp.passive and not cp.dashcamOnly and not cp.notCar and
            control_flags(cp) == int(GMFlags.CC_LONG) and len(configs) == 1 and
            configs[0].safetyModel == CarParams.SafetyModel.gm and int(configs[0].safetyParam) == ORDINARY_CC_WORD)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


ORDINARY_CAMERA_ALPHA_CAR = frozenset((CAR.CHEVROLET_SILVERADO, CAR.CHEVROLET_EQUINOX,
                                      CAR.CHEVROLET_TRAILBLAZER, CAR.CHEVROLET_TRAX))
ORDINARY_CAMERA_CAR = ORDINARY_CAMERA_ALPHA_CAR | frozenset((CAR.GMC_YUKON, CAR.CHEVROLET_SUBURBAN_CAMERA))


CAMERA_ACC_PEDAL_CAR = ORDINARY_CAMERA_CAR | frozenset((CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_2019))


class BrakeSource(Enum):
  BE = "BE"
  F1 = "F1"


@dataclass(frozen=True)
class CameraAccPedalProfile:
  removed: bool
  brake_source: BrakeSource
  longitudinal: bool
  volt: bool = False
  auto_hold: bool = False
  one_pedal: bool = False
  topology: str = "camera"
  radar: bool = False


CAMERA_ACC_PEDAL_PROFILES = MappingProxyType({
  **{start + index: CameraAccPedalProfile(False, BrakeSource.F1 if index % 2 else BrakeSource.BE,
                                       start != 0xE510, True, hold, one, "sdgm", index >= 2)
     for start, hold, one in ((0xE500, False, False), (0xE510, False, False),
                              (0xE520, True, False), (0xE540, False, True), (0xE560, True, True))
     for index in range(4)},
  **{start + index: CameraAccPedalProfile(False, BrakeSource.F1 if index % 2 else BrakeSource.BE,
                                       start != 0xE410, True, hold, one, "ascm", index >= 2)
     for start, hold, one in ((0xE400, False, False), (0xE410, False, False),
                              (0xE420, True, False), (0xE440, False, True), (0xE460, True, True))
     for index in range(4)},
  **{start + index: CameraAccPedalProfile(index >= 2, BrakeSource.F1 if index % 2 else BrakeSource.BE,
                                       start != 0xE310, True, hold, one, "gateway")
     for start, hold, one in ((0xE300, False, False), (0xE310, False, False),
                              (0xE320, True, False), (0xE340, False, True), (0xE360, True, True))
     for index in range(4)},
  **{start + index: CameraAccPedalProfile(index >= 2, BrakeSource.F1 if index % 2 else BrakeSource.BE,
                                       start != 0xE210, True, hold, one)
     for start, hold, one in ((0xE200, False, False), (0xE210, False, False),
                              (0xE220, True, False), (0xE240, False, True), (0xE260, True, True))
     for index in range(4)},
  0xE100: CameraAccPedalProfile(False, BrakeSource.BE, True),
  0xE101: CameraAccPedalProfile(False, BrakeSource.F1, True),
  0xE102: CameraAccPedalProfile(True, BrakeSource.BE, True),
  0xE103: CameraAccPedalProfile(True, BrakeSource.F1, True),
  0xE110: CameraAccPedalProfile(False, BrakeSource.BE, False),
  0xE111: CameraAccPedalProfile(False, BrakeSource.F1, False),
  0xE112: CameraAccPedalProfile(True, BrakeSource.BE, False),
  0xE113: CameraAccPedalProfile(True, BrakeSource.F1, False),
})


def volt_camera_pedal_word(profile, auto_hold: bool, one_pedal: bool) -> int:
  start = 0xE260 if auto_hold and one_pedal else 0xE240 if one_pedal else 0xE220 if auto_hold else 0xE200
  if not profile.longitudinal:
    start = 0xE210
  if profile.topology == "gateway":
    start += 0x100
  elif profile.topology == "ascm":
    start += 0x200
  elif profile.topology == "sdgm":
    start += 0x300
  return start + int(profile.radar if profile.topology in ("ascm", "sdgm") else profile.removed) * 2 + int(profile.brake_source == BrakeSource.F1)


def camera_acc_pedal_profile(cp):
  try:
    if cp.brand != 'gm' or cp.carFingerprint not in CAMERA_ACC_PEDAL_CAR:
      return None
    if len(cp.safetyConfigs) != 1:
      return None
    profile = CAMERA_ACC_PEDAL_PROFILES.get(int(cp.safetyConfigs[0].safetyParam))
    if profile is None:
      return None
    flags = (_GM_PEDAL_LONG_FLAG | (_GM_NO_CAMERA_FLAG if profile.removed else 0) |
             (_GM_NO_ACCELERATOR_POS_FLAG if profile.brake_source == BrakeSource.F1 else 0))
    identity = (CAR.CHEVROLET_VOLT if profile.topology == "gateway" else
                CAR.CHEVROLET_VOLT_ASCM if profile.topology == "ascm" else
                CAR.CHEVROLET_VOLT_2019 if profile.topology == "sdgm" else CAR.CHEVROLET_VOLT_CAMERA)
    if (cp.brand == 'gm' and cp.carFingerprint in (frozenset((identity,)) if profile.volt else ORDINARY_CAMERA_CAR) and
        cp.transmissionType == (CarParams.TransmissionType.direct if profile.volt else CarParams.TransmissionType.automatic) and
        cp.networkLocation == CarParams.NetworkLocation.fwdCamera and (profile.volt or cp.radarUnavailable) and
        (profile.topology != "gateway" or not cp.radarUnavailable) and
        (profile.topology not in ("ascm", "sdgm") or profile.radar != cp.radarUnavailable) and
        not cp.passive and not cp.dashcamOnly and not cp.notCar and int(cp.flags) & _GM_CONTROL_FLAGS_MASK == flags and
        cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
        bool(cp.openpilotLongitudinalControl) == profile.longitudinal and
        bool(cp.pcmCruise) == (not profile.longitudinal and profile.topology == "camera") and
        (not profile.volt or not profile.longitudinal or profile.topology in ("ascm", "sdgm") or cp.alphaLongitudinalAvailable)):
      return profile
  except (AttributeError, IndexError, TypeError, ValueError):
    pass
  return None

def is_ordinary_camera_profile(cp, *, longitudinal=False):
  try:
    return (cp.brand == 'gm' and cp.carFingerprint in ORDINARY_CAMERA_CAR and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and cp.radarUnavailable and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and
            control_flags(cp) in (0, int(GMFlags.NO_ACCELERATOR_POS_MSG), int(GMFlags.NO_CAMERA),
                             int(GMFlags.NO_CAMERA | GMFlags.NO_ACCELERATOR_POS_MSG)) and
            bool(cp.openpilotLongitudinalControl) is longitudinal and bool(cp.pcmCruise) is not longitudinal and
            len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            (not longitudinal or cp.carFingerprint in ORDINARY_CAMERA_ALPHA_CAR and cp.alphaLongitudinalAvailable) and
            int(cp.safetyConfigs[0].safetyParam) == ((0xC173 if longitudinal else 0xC172) if cp.flags & GMFlags.NO_CAMERA else
                                                      (0xC170 if longitudinal else 0xC171)))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def is_ordinary_camera_removed(cp):
  try:
    return is_ordinary_camera_profile(cp, longitudinal=cp.openpilotLongitudinalControl) and bool(cp.flags & GMFlags.NO_CAMERA)
  except (AttributeError, TypeError, ValueError):
    return False


CONVENTIONAL_CC_PEDAL_WORDS = (0xC180, 0xC181)
CONVENTIONAL_CC_PEDAL_STOCK_WORDS = (0xC186, 0xC187)


def is_conventional_cc_pedal_profile(cp):
  if is_silverado_cc_pedal_profile(cp):
    return True
  try:
    if cp.brand != 'gm' or cp.carFingerprint not in ORDINARY_CC_CAR:
      return False
    flags = control_flags(cp)
    removed = bool(flags & GMFlags.NO_CAMERA)
    return (cp.brand == 'gm' and cp.carFingerprint in ORDINARY_CC_CAR and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and cp.radarUnavailable and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and
            bool(flags & _GM_PEDAL_LONG_FLAG) and not flags & ~_CC_PEDAL_ALLOWED_FLAGS and
            not cp.pcmCruise and len(cp.safetyConfigs) == 1 and
            cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
            int(cp.safetyConfigs[0].safetyParam) ==
              (CONVENTIONAL_CC_PEDAL_WORDS if cp.openpilotLongitudinalControl else CONVENTIONAL_CC_PEDAL_STOCK_WORDS)[removed])
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


SILVERADO_CC_PEDAL_WORDS = ((0xC182, 0xC183), (0xC184, 0xC185))


def is_silverado_cc_pedal_profile(cp):
  """Exact Silverado interceptor owner, including its distinct stock cancellation."""
  try:
    if cp.brand != 'gm' or cp.carFingerprint != CAR.CHEVROLET_SILVERADO_CC:
      return False
    flags = control_flags(cp)
    removed = bool(flags & GMFlags.NO_CAMERA)
    word = SILVERADO_CC_PEDAL_WORDS[not cp.openpilotLongitudinalControl][removed]
    return (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_SILVERADO_CC and
            cp.networkLocation == CarParams.NetworkLocation.fwdCamera and cp.radarUnavailable and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and
            bool(flags & _GM_PEDAL_LONG_FLAG) and not flags & ~_CC_PEDAL_ALLOWED_FLAGS and
            not cp.pcmCruise and not cp.alphaLongitudinalAvailable and len(cp.safetyConfigs) == 1 and
            cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and int(cp.safetyConfigs[0].safetyParam) == word)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


@dataclass(frozen=True)
class VoltCcPedalProfile:
  longitudinal: bool
  radar: bool
  removed: bool
  brake_source: BrakeSource


VOLT_CC_PEDAL_PROFILES = MappingProxyType({
  start + index: VoltCcPedalProfile(start == 0xE600, index >= 4, bool(index & 2),
                                  BrakeSource.F1 if index & 1 else BrakeSource.BE)
  for start in (0xE600, 0xE610) for index in range(8)
})


def volt_cc_pedal_profile(cp):
  try:
    if len(cp.safetyConfigs) != 1:
      return None
    profile = VOLT_CC_PEDAL_PROFILES.get(int(cp.safetyConfigs[0].safetyParam))
    if profile is None:
      return None
    flags = int(GMFlags.PEDAL_LONG | (GMFlags.NO_CAMERA if profile.removed else 0) |
                (GMFlags.NO_ACCELERATOR_POS_MSG if profile.brake_source == BrakeSource.F1 else 0))
    if (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_VOLT_CC and
        cp.transmissionType == CarParams.TransmissionType.direct and
        cp.networkLocation == CarParams.NetworkLocation.fwdCamera and
        not cp.passive and not cp.dashcamOnly and not cp.notCar and
        int(cp.flags) & ~int(GMFlags.HAS_BSM) == flags and
        cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
        cp.openpilotLongitudinalControl == profile.longitudinal and not cp.pcmCruise and
        profile.radar != cp.radarUnavailable):
      return profile
  except (AttributeError, IndexError, TypeError, ValueError):
    pass
  return None


@dataclass(frozen=True)
class MalibuHybridProfile:
  pedal: bool
  longitudinal: bool
  removed: bool


MALIBU_HYBRID_PROFILES = MappingProxyType({
  0xE800 + index: MalibuHybridProfile(index in (2, 3), index < 4, bool(index & 1))
  for index in range(6)
})
MALIBU_HYBRID_SOURCES = {0x184: 8, 0x34A: 5, 0x348: 5, 0x1E1: 7, 0x1C4: 8,
                        0xC9: 8, 0x3D1: 8, 0x1F5: 8, 0x232: 8, 0x1E5: 8, 0x140: 3,
                        0x12A: 8, 0x1F1: 8, 0x17D: 6, 0xBD: 7}


def malibu_hybrid_profile(cp):
  try:
    if len(cp.safetyConfigs) != 1:
      return None
    profile = MALIBU_HYBRID_PROFILES.get(int(cp.safetyConfigs[0].safetyParam))
    if profile is None:
      return None
    flags = int(GMFlags.CC_LONG | (GMFlags.NO_CAMERA if profile and profile.removed else 0))
    if cp.flags & GMFlags.PEDAL_LONG:
      flags |= int(GMFlags.PEDAL_LONG)
    if cp.flags & GMFlags.NO_ACCELERATOR_POS_MSG:
      flags |= int(GMFlags.NO_ACCELERATOR_POS_MSG)
    if (profile is not None and cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_MALIBU_HYBRID_CC and
        cp.networkLocation == CarParams.NetworkLocation.fwdCamera and
        cp.transmissionType == CarParams.TransmissionType.direct and not cp.alphaLongitudinalAvailable and
        cp.steerControlType == CarParams.SteerControlType.torque and cp.lateralTuning.which() == 'torque' and
        not cp.passive and not cp.dashcamOnly and not cp.notCar and control_flags(cp) == flags and
        cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.gm and
        cp.openpilotLongitudinalControl == profile.longitudinal and cp.pcmCruise == (not profile.longitudinal) and
        (not profile.longitudinal or bool(cp.flags & GMFlags.PEDAL_LONG) == profile.pedal) and
        (bool(cp.flags & GMFlags.PEDAL_LONG) or cp.radarUnavailable)):
      return profile
  except (AttributeError, IndexError, TypeError, ValueError):
    pass
  return None
