from dataclasses import replace

from opendbc.car import structs
from opendbc.car.toyota.config_cache import cached_config
from opendbc.car.toyota.values import CAR, ToyotaFlags, ToyotaSafetyFlags, EPS_SCALE, uses_toyota_auto_hold_aeb
from openpilot.starpilot.aol.policy import AolVehiclePolicy
from openpilot.starpilot.aol.intent import AolCardIntent, AOL_TOGGLE

AOL_WORDS = frozenset((
  0xE449, 0xE649, 0x3049, 0x3249, 0x3449, 0x3649, 0xA042, 0xA049, 0xA242, 0xA249, 0xA449, 0xA649,
  0xA849, 0xA949, 0xA958, 0xA964, 0xAA49, 0xAB49, 0xAB58, 0xAB64, 0xB149, 0xB158,
  0xB164, 0xB349, 0xB358, 0xB364, 0xB449, 0xB549, 0xB558, 0xB564, 0xB649, 0xB749,
  0xB758, 0xB764, 0xB842, 0xB849, 0xBA42, 0xBA49, 0xE042, 0xE049, 0xE242, 0xE249,
  0xE84D, 0xE949, 0xEA4D, 0xEB49, 0xF049, 0xF04D, 0xF149, 0xF249, 0xF24D, 0xF349,
  0xF44D, 0xF549, 0xF64D, 0xF749,
  0x8949, 0x8B49,
  0x8849, 0x8A49, 0x9849, 0x9A49, 0x9842, 0x9A42, 0x8958, 0x8B58, 0x8964, 0x8B64, 0xC84D, 0xCA4D, 0xC949, 0xCB49,
  0x8042, 0x8242, 0xC042, 0xC242, 0xD049, 0xD249,
  0xC049, 0xC249,
  0x9449, 0x9549, 0x9558, 0x9564, 0x9649, 0x9749, 0x9758, 0x9764, 0xD44D, 0xD549, 0xD64D, 0xD749,
  0x5249, 0x5349, 0x5358, 0x5364, 0x564D, 0x5749, 0x6242, 0x6249,
  73, 585, 856, 4169, 4681, 2121, 2633, 6217, 0x9049, 0x9249,
  0xB049, 0xB249, 0x2449, 0x2649, 0x2049, 0x2249, 0x424D, 0x449, 0x649, 0x1449, 0x1649,
  0x8049, 0x8249, 0x8449, 0x8649, 0x9158, 0x9358, 0x9149,
  0x9349, 0x9164, 0x9364, 0xd149, 0xd349, 0xd04d, 0xd24d,
))


SECOC_TORQUE_CARS = frozenset((CAR.TOYOTA_SIENNA_4TH_GEN, CAR.TOYOTA_RAV4_PRIME, CAR.TOYOTA_YARIS))
RADAR_TORQUE_CARS = frozenset((CAR.TOYOTA_CHR_TSS2, CAR.TOYOTA_RAV4_TSS2_2022))
CAMERA_TORQUE_CARS = frozenset((
  CAR.TOYOTA_ALPHARD_TSS2, CAR.TOYOTA_AVALON_TSS2, CAR.TOYOTA_CAMRY_TSS2, CAR.TOYOTA_COROLLA_TSS2,
  CAR.TOYOTA_HIGHLANDER_TSS2, CAR.TOYOTA_PRIUS_TSS2, CAR.TOYOTA_RAV4_TSS2, CAR.TOYOTA_MIRAI,
  CAR.LEXUS_ES_TSS2, CAR.LEXUS_NX_TSS2, CAR.LEXUS_LC_TSS2, CAR.LEXUS_RX_TSS2,
  CAR.LEXUS_IS_TSS2, CAR.LEXUS_RC_TSS2,
))


def secoc_qualified(cp, *, marked_only=False):
  radar = cp is not None and cp.carFingerprint == CAR.TOYOTA_YARIS
  required = ToyotaFlags.TSS2 | ToyotaFlags.NO_DSU | ToyotaFlags.SECOC | ToyotaFlags.RAISED_ACCEL_LIMIT
  if radar:
    required |= ToyotaFlags.RADAR_ACC
    if cp.openpilotLongitudinalControl:
      required |= ToyotaFlags.DISABLE_RADAR
  allowed = required | ToyotaFlags.HYBRID | ToyotaFlags.HAS_BSM
  if (cp is None or cp.brand != 'toyota' or cp.carFingerprint not in SECOC_TORQUE_CARS or
      cp.notCar or cp.passive or cp.dashcamOnly or not cp.pcmCruise or (not cp.openpilotLongitudinalControl and not radar) or
      not cp.secOcRequired or not cp.secOcKeyAvailable or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or len(cp.safetyConfigs) != 1 or
      cp.alternativeExperience not in ((32,) if marked_only else (0, 32)) or
      cp.flags & required != required or int(cp.flags) & ~int(allowed)):
    return False
  config = cp.safetyConfigs[0]
  mode = (ToyotaSafetyFlags.SECOC_RADAR_LONG if cp.openpilotLongitudinalControl else
          ToyotaSafetyFlags.SECOC | ToyotaSafetyFlags.STOCK_LONGITUDINAL) if radar else ToyotaSafetyFlags.SECOC
  return (config.safetyModel == structs.CarParams.SafetyModel.toyota and
          config.safetyParam == EPS_SCALE[cp.carFingerprint] | int(mode))


def lexus_is_qualified(cp, *, marked_only=False):
  if (cp is None or cp.brand != 'toyota' or cp.carFingerprint != CAR.LEXUS_IS or
      cp.notCar or cp.passive or cp.dashcamOnly or not cp.pcmCruise or cp.openpilotLongitudinalControl or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or len(cp.safetyConfigs) != 1 or
      cp.flags & ToyotaFlags.UNSUPPORTED_DSU == 0 or
      int(cp.flags) & ~int(ToyotaFlags.UNSUPPORTED_DSU | ToyotaFlags.HYBRID) or
      cp.alternativeExperience not in ((32,) if marked_only else (0, 32))):
    return False
  config = cp.safetyConfigs[0]
  return config.safetyModel == structs.CarParams.SafetyModel.toyota and config.safetyParam == 0x424D


def radar_filter_qualified(cp, *, marked_only=False):
  from opendbc.car.toyota.radar_filter import configured
  if (not configured(cp) or cp.carFingerprint not in (CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_CAMRY, CAR.TOYOTA_CHR, CAR.TOYOTA_CHR_TSS2) or
      not cp.pcmCruise or cp.notCar or cp.passive or cp.dashcamOnly or
      cp.alternativeExperience not in ((32,) if marked_only else (0, 32))):
    return False
  required = ToyotaFlags.NO_DSU | ToyotaFlags.RADAR_CAN_FILTER
  if cp.carFingerprint in (CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_CHR_TSS2):
    required |= ToyotaFlags.TSS2 | ToyotaFlags.RADAR_ACC | ToyotaFlags.RAISED_ACCEL_LIMIT
  allowed = required | ToyotaFlags.HYBRID | ToyotaFlags.HAS_BSM | ToyotaFlags.FILTER_BUTTONS
  if cp.carFingerprint == CAR.TOYOTA_RAV4_TSS2_2022:
    allowed |= ToyotaFlags.ARS510_RADAR | ToyotaFlags.ARS510_FUSED
  return bool(cp.flags & required == required and not int(cp.flags) & ~int(allowed) and
              (not cp.flags & ToyotaFlags.ARS510_FUSED or cp.flags & ToyotaFlags.ARS510_RADAR))


def angle_qualified(cp, *, marked_only=False):
  from opendbc.car.toyota.angle_control import configured
  return configured(cp) and cp.alternativeExperience in ((32,) if marked_only else (0, 32))


def matrix_stock_qualified(cp, *, marked_only=False):
  if (cp is None or cp.brand != 'toyota' or cp.carFingerprint != CAR.TOYOTA_MATRIX_RETROFIT or
      cp.notCar or cp.passive or cp.dashcamOnly or not cp.pcmCruise or cp.openpilotLongitudinalControl or
      cp.secOcRequired or cp.flags != 0 or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or len(cp.safetyConfigs) != 1 or
      cp.alternativeExperience not in ((32,) if marked_only else (0, 32))):
    return False
  config = cp.safetyConfigs[0]
  return config.safetyModel == structs.CarParams.SafetyModel.toyota and config.safetyParam == 856


def stock_prius_retrofit_qualified(cp, *, marked_only=False):
  if (cp is None or cp.brand != 'toyota' or cp.carFingerprint != CAR.TOYOTA_PRIUS_RETROFIT or
      cp.notCar or cp.passive or cp.dashcamOnly or cp.secOcRequired or not cp.pcmCruise or
      cp.openpilotLongitudinalControl or cp.steerControlType != structs.CarParams.SteerControlType.torque or
      len(cp.safetyConfigs) != 1 or cp.flags & ~int(ToyotaFlags.ZSS) != int(ToyotaFlags.HYBRID) or
      cp.alternativeExperience not in ((32,) if marked_only else (0, 32))):
    return False
  config = cp.safetyConfigs[0]
  return config.safetyModel == structs.CarParams.SafetyModel.toyota and config.safetyParam == 585


@cached_config
def qualified(cp, *, marked_only=False):
  from opendbc.car.toyota import stock_aol
  if stock_aol.configured(cp):
    return cp.alternativeExperience in ((32,) if marked_only else (0, 32))
  from opendbc.car.toyota.pedal_composite import hold_base_cp
  base = hold_base_cp(cp)
  if base is not cp:
    from opendbc.car.toyota.interface import toyota_auto_hold_supported
    return toyota_auto_hold_supported(cp) and qualified(base, marked_only=marked_only)
  from opendbc.car.toyota import dsu_bypass
  if dsu_bypass.configured(cp):
    return cp.alternativeExperience in ((32,) if marked_only else (0, 32))
  from opendbc.car.toyota import pedal_composite
  if pedal_composite.configured(cp):
    return qualified(pedal_composite.parent_cp(cp), marked_only=marked_only)
  if stock_prius_retrofit_qualified(cp, marked_only=marked_only):
    return True
  if matrix_stock_qualified(cp, marked_only=marked_only):
    return True
  if angle_qualified(cp, marked_only=marked_only):
    return True
  from opendbc.car.toyota.advert_filter import configured as advert_filter_configured
  if advert_filter_configured(cp):
    return cp.alternativeExperience in ((32,) if marked_only else (0, 32))
  if radar_filter_qualified(cp, marked_only=marked_only):
    return True
  if lexus_is_qualified(cp, marked_only=marked_only):
    return True
  from opendbc.car.toyota import pedal
  if pedal.configured(cp):
    return cp.alternativeExperience in ((32,) if marked_only else (0, 32))
  from opendbc.car.toyota.rav4h_filter import configured as rav4h_filter_configured
  if rav4h_filter_configured(cp):
    hold_permission = 128 if cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD else 0
    allowed = (32 | hold_permission,) if marked_only else (32 | hold_permission, hold_permission)
    return cp.alternativeExperience in allowed
  if cp is not None and cp.carFingerprint in SECOC_TORQUE_CARS:
    return secoc_qualified(cp, marked_only=marked_only)
  from opendbc.car.toyota.prius_longitudinal import configured as prius_filter_configured
  if cp is not None and prius_filter_configured(cp):
    return cp.alternativeExperience in ((32,) if marked_only else (0, 32))
  if (cp is None or cp.brand != 'toyota' or cp.carFingerprint not in CAMERA_TORQUE_CARS | RADAR_TORQUE_CARS or
      cp.notCar or cp.passive or cp.dashcamOnly or not cp.pcmCruise or
      (cp.carFingerprint not in RADAR_TORQUE_CARS and not cp.openpilotLongitudinalControl) or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or
      not cp.flags & ToyotaFlags.TSS2 or cp.flags & (ToyotaFlags.SECOC | ToyotaFlags.ANGLE_CONTROL | ToyotaFlags.UNSUPPORTED_DSU) or
      len(cp.safetyConfigs) != 1):
    return False
  from opendbc.car.toyota.interface import toyota_auto_hold_supported
  hold = bool(cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
  hold_permission = 256 if uses_toyota_auto_hold_aeb(cp) else 128
  allowed_experience = (32 | hold_permission,) if hold else (32,)
  if not marked_only:
    allowed_experience += (0, hold_permission) if hold else (0,)
  if (cp.alternativeExperience not in allowed_experience or
      hold and not toyota_auto_hold_supported(cp)):
    return False
  required_flags = ToyotaFlags.TSS2 | ToyotaFlags.NO_DSU | ToyotaFlags.RAISED_ACCEL_LIMIT
  radar = cp.carFingerprint in RADAR_TORQUE_CARS
  if radar:
    required_flags |= ToyotaFlags.RADAR_ACC
    if cp.openpilotLongitudinalControl:
      required_flags |= ToyotaFlags.DISABLE_RADAR
    if hold:
      return False
  allowed_flags = required_flags | ToyotaFlags.HYBRID | ToyotaFlags.HAS_BSM
  if cp.carFingerprint == CAR.TOYOTA_RAV4_TSS2_2022 and not cp.openpilotLongitudinalControl:
    allowed_flags |= ToyotaFlags.ARS510_RADAR
  if cp.flags & required_flags != required_flags or int(cp.flags) & ~int(allowed_flags | ToyotaFlags.AUTO_BRAKE_HOLD):
    return False
  config = cp.safetyConfigs[0]
  expected = EPS_SCALE[cp.carFingerprint] | (int(ToyotaSafetyFlags.STOCK_LONGITUDINAL) if radar and not cp.openpilotLongitudinalControl else 0)
  return config.safetyModel == structs.CarParams.SafetyModel.toyota and config.safetyParam == expected


def policy_for(cp):
  from openpilot.starpilot.controllers.wheel_actions import button_reported, eligible

  admitted = qualified(cp)
  auxiliary_lkas = bool(eligible(cp) and cp.brand == 'toyota' and button_reported(cp, 'LKASButtonControl'))
  return AolVehiclePolicy(intent_supported=admitted, settings_supported=admitted, runtime_supported=admitted,
                          normal_runtime_supported=admitted, ordinary_axis_ack_required=qualified(cp, marked_only=True),
                          full_axis_runtime_required=admitted, fixed_cruise_buttons=admitted, distance_pause_only=admitted,
                          auxiliary_lkas_supported=auxiliary_lkas,
                          alternative_experience_addition=32 if admitted else 0)


def native_profile_supported(model, param):
  return model == int(structs.CarParams.SafetyModel.toyota) and param in AOL_WORDS


def native_accepts_cp(cp, model, param):
  return qualified(cp, marked_only=True) and native_profile_supported(model, param)


def native_latch_rejected(cp, native):
  return qualified(cp, marked_only=True) and native.requestedLateral and not native.lateralAllowed


def ordinary_axis_request_allowed(cp, state):
  return qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and not state.steerFaultPermanent


def retain_on_native_denial(cp, native, state):
  gears = structs.CarState.GearShifter
  return bool(state is not None and qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and
              not state.steerFaultPermanent and
              (state.gearShifter not in (gears.drive, gears.sport, gears.low, gears.brake) or state.steerFaultTemporary or state.vehicleSensorsInvalid))


class ToyotaCardIntent(AolCardIntent):
  def __init__(self, settings):
    effective = replace(settings, lkas_action=0, main_action=0,
                        distance_actions=tuple(0 if action == AOL_TOGGLE else action for action in settings.distance_actions),
                        cancel_actions=tuple(0 if action == AOL_TOGGLE else action for action in settings.cancel_actions),
                        mode_actions=tuple(0 if action == AOL_TOGGLE else action for action in settings.mode_actions),
                        custom_actions=tuple(0 if action == AOL_TOGGLE else action for action in settings.custom_actions))
    super().__init__(effective)


def create_intent(cp, settings):
  return ToyotaCardIntent(settings)


def configuration_settings_policy(cp):
  if qualified(cp):
    return None
  if (cp is not None and cp.brand == 'toyota' and cp.carFingerprint == CAR.TOYOTA_PRIUS_RETROFIT and
      not cp.notCar and not cp.passive and not cp.dashcamOnly and
      cp.steerControlType == structs.CarParams.SteerControlType.torque):
    return AolVehiclePolicy(settings_supported=True, fixed_cruise_buttons=True, distance_pause_only=True)
  return None


def held_lateral_pedal(owner, cs):
  """An already accepted pedal may retain LAT intent, never stale output."""
  from math import isfinite
  from openpilot.starpilot.nostalgia import physical_cancel
  anchor = getattr(owner, 'aol_ordinary_source_anchor', None)
  settings = getattr(owner, 'aol_settings', None)
  if not qualified(owner.CP, marked_only=True) or anchor is None:
    return False
  proof, native, accepted, _, state = anchor
  return bool(cs is state and not getattr(owner, 'car_state_samples', ()) and
    proof[8] and not proof[9] and not owner.enabled and not owner.active and
    native is not None and native.requestedLateral and native.lateralAllowed and
    not native.requestedLongitudinal and not native.longitudinalAllowed and
    accepted is not None and accepted.allowedLatch and accepted.lateralArmed and
    accepted.settingsQualified and not accepted.pauseLateral and not accepted.pauseLongitudinal and
    settings is not None and settings.enabled and
    not getattr(owner, 'aol_dm_lateral_inhibit', False) and
    cs.brakePressed != cs.gasPressed and not cs.regenBraking and
    cs.cruiseState.available and not cs.cruiseState.enabled and
    cs.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport,
                       structs.CarState.GearShifter.low, structs.CarState.GearShifter.brake) and
    isfinite(cs.vEgoRaw) and isfinite(cs.vEgo) and
    (not cs.brakePressed or cs.standstill or cs.vEgo >= settings.pause_brake_mps) and
    not cs.seatbeltUnlatched and not physical_cancel(cs, owner.CP))
