from opendbc.car.structs import car
from opendbc.car.honda.stock_aol import qualified as qualified_stock, BOSCH_WORDS, NIDEC_WORDS, temporary_restriction, ALT_RADAR_STOCK, CLASSIC_BOSCH
from openpilot.starpilot.car.honda.stock_intent import HondaStockCardIntent
from opendbc.car.honda.values import CAR, HondaFlags, HondaSafetyFlags
from openpilot.starpilot.aol.policy import AolVehiclePolicy


CLASSIC_BOSCH_AOL_CARS = frozenset((
  CAR.HONDA_NBOX_2G, CAR.HONDA_ACCORD, CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CIVIC_BOSCH_DIESEL,
  CAR.HONDA_CRV_5G, CAR.HONDA_CRV_HYBRID, CAR.ACURA_RDX_3G, CAR.HONDA_INSIGHT,
  CAR.HONDA_E, CAR.HONDA_E_ADVANCE,
))
_CLASSIC_BOSCH_AOL_IDS = frozenset(str(candidate) for candidate in CLASSIC_BOSCH_AOL_CARS)
_NON_CLASSIC_BOSCH_FLAGS = HondaFlags.BOSCH_RADARLESS | HondaFlags.BOSCH_CANFD | HondaFlags.BOSCH_ALT_RADAR
_AOL_SAFETY_PARAMS = frozenset((
  int(HondaSafetyFlags.BOSCH_LONG),
  int(HondaSafetyFlags.BOSCH_LONG | HondaSafetyFlags.ALT_BRAKE),
  int(HondaSafetyFlags.BOSCH_LONG | HondaSafetyFlags.AOL_BOSCH_LONG),
  int(HondaSafetyFlags.BOSCH_LONG | HondaSafetyFlags.ALT_BRAKE | HondaSafetyFlags.AOL_BOSCH_LONG),
))


def qualified_honda(CP) -> bool:
  rdx = CP.carFingerprint == CAR.ACURA_RDX_3G
  word = int(CP.safetyConfigs[0].safetyParam) if len(CP.safetyConfigs) == 1 else -1
  return (word in ((131, 163) if rdx else _AOL_SAFETY_PARAMS) and
          str(CP.carFingerprint) in _CLASSIC_BOSCH_AOL_IDS and
          CP.brand == 'honda' and bool(CP.flags & HondaFlags.BOSCH) and
          not bool(CP.flags & _NON_CLASSIC_BOSCH_FLAGS) and
          bool(CP.openpilotLongitudinalControl) and not bool(CP.pcmCruise) and
          not bool(CP.notCar) and not bool(CP.passive) and not bool(CP.dashcamOnly) and
          len(CP.safetyConfigs) == 1 and
          CP.safetyConfigs[0].safetyModel == car.CarParams.SafetyModel.hondaBosch and
          int(CP.safetyConfigs[0].safetyParam) in (_AOL_SAFETY_PARAMS | {131, 163}))


def policy_for(CP) -> AolVehiclePolicy:
  if qualified_stock(CP):
    safety_addition = 256 if CP.carFingerprint == CAR.HONDA_ODYSSEY_TWN else 0
    if CP.flags & HondaFlags.GAS_INTERCEPTOR:
      safety_addition |= int(HondaSafetyFlags.NIDEC_PEDAL_AOL)
    if CP.carFingerprint in CLASSIC_BOSCH and CP.openpilotLongitudinalControl:
      safety_addition |= int(HondaSafetyFlags.CLASSIC_LONG_AOL | HondaSafetyFlags.AOL_BOSCH_LONG)
    if CP.carFingerprint in ALT_RADAR_STOCK:
      if CP.carFingerprint == CAR.ACURA_MDX_4G:
        safety_addition |= int(HondaSafetyFlags.STOCK_TJA)
      if CP.openpilotLongitudinalControl:
        safety_addition |= int(HondaSafetyFlags.ALT_RADAR_AOL | HondaSafetyFlags.AOL_BOSCH_LONG)
    return AolVehiclePolicy(intent_supported=True, settings_supported=True, runtime_supported=True,
                            normal_runtime_supported=True, ordinary_axis_ack_required=qualified_stock(CP, marked_only=True), explicit_latch=True,
                            alternative_experience_addition=32,
                            safety_param_addition=safety_addition)
  qualified = qualified_honda(CP)
  return AolVehiclePolicy(
    intent_supported=qualified, settings_supported=qualified,
    runtime_supported=qualified and bool(int(CP.safetyConfigs[0].safetyParam) & 0x20),
    distance_personality=qualified,
    safety_param_addition=0x20 if qualified else 0,
  )


def native_profile_supported(model: int, param: int) -> bool:
  return ((model == int(car.CarParams.SafetyModel.hondaBosch) and param in BOSCH_WORDS | {34, 35, 163}) or
          (model == int(car.CarParams.SafetyModel.hondaNidec) and param in NIDEC_WORDS))


def native_accepts_cp(CP, model: int, param: int) -> bool:
  # Honda's native receipt checks the safety profile, then the shared exact CP match.
  if qualified_stock(CP, marked_only=True):
    return (native_profile_supported(model, param) and model == int(CP.safetyConfigs[-1].safetyModel.raw) and
            param == CP.safetyConfigs[-1].safetyParam)
  return qualified_honda(CP) and param == CP.safetyConfigs[0].safetyParam and param in (34, 35, 163)


def native_latch_rejected(CP, native):
  return qualified_stock(CP, marked_only=True) and native.requestedLateral and not native.lateralAllowed


def ordinary_axis_request_allowed(CP, CS):
  return qualified_stock(CP, marked_only=True) and CS.canValid and not CS.canTimeout and not CS.steerFaultPermanent


def retain_on_native_denial(CP, native, state):
  return temporary_restriction(CP, state)


def create_intent(CP, settings):
  if qualified_stock(CP):
    safety_addition = 256 if CP.carFingerprint == CAR.HONDA_ODYSSEY_TWN else 0
    if CP.flags & HondaFlags.GAS_INTERCEPTOR:
      safety_addition |= int(HondaSafetyFlags.NIDEC_PEDAL_AOL)
    if CP.carFingerprint in CLASSIC_BOSCH and CP.openpilotLongitudinalControl:
      safety_addition |= int(HondaSafetyFlags.CLASSIC_LONG_AOL | HondaSafetyFlags.AOL_BOSCH_LONG)
    if CP.carFingerprint in ALT_RADAR_STOCK:
      if CP.carFingerprint == CAR.ACURA_MDX_4G:
        safety_addition |= int(HondaSafetyFlags.STOCK_TJA)
      if CP.openpilotLongitudinalControl:
        safety_addition |= int(HondaSafetyFlags.ALT_RADAR_AOL | HondaSafetyFlags.AOL_BOSCH_LONG)
    return HondaStockCardIntent(CP, settings)
  from openpilot.starpilot.aol.intent import AolCardIntent
  return AolCardIntent(settings)


def aol_safety_config(CP):
  return CP.safetyConfigs[-1] if qualified_stock(CP, marked_only=True) else None


def physical_cancel_lateral_allowed(cp, cs, intent, native, *, settings, previous,
                                    source_ns, now_ns, axis_session_id) -> bool:
  """Let the mapped intent owner apply Cancel while retaining a granted LAT axis."""
  from openpilot.starpilot.aol.intent import ButtonType
  from openpilot.starpilot.aol.runtime import (AXIS_MAX_AGE_NS, INTENT_MAX_AGE_NS, SAFETY_MAX_AGE_NS,
                                               native_inventory_matches_cp)
  try:
    return bool(qualified_stock(cp, marked_only=True) and settings is not None and settings.enabled and not any(settings.cancel_actions) and
      previous is not None and previous.lateral_active and previous.native_acknowledged and
      intent is not None and intent.settingsQualified and intent.allowedLatch and intent.lateralArmed and
      not intent.pauseLateral and intent.producerSessionId and intent.carStateLogMonoTime == source_ns and
      0 < source_ns <= intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime and
      now_ns - source_ns <= AXIS_MAX_AGE_NS and now_ns - intent.observedMonoTime <= INTENT_MAX_AGE_NS and
      intent.validUntilMonoTime - intent.observedMonoTime <= SAFETY_MAX_AGE_NS and
      cs.canValid and not cs.canTimeout and cs.cruiseState.available and not cs.steerFaultTemporary and
      not cs.steerFaultPermanent and not cs.accFaulted and not cs.doorOpen and
      any(event.type == ButtonType.cancel for event in cs.buttonEvents) and
      native is not None and native.compatible and int(native.protocolVersion) == 1 and
      axis_session_id and native.axisSessionId == axis_session_id and native.requestedLateral and native.lateralAllowed and
      native_accepts_cp(cp, native.safetyModel, native.safetyParam) and native.pandaInventory and
      native_inventory_matches_cp(native, cp) and
      0 < native.observedMonoTime <= now_ns <= native.validUntilMonoTime and
      now_ns - native.observedMonoTime <= SAFETY_MAX_AGE_NS and
      native.validUntilMonoTime - native.observedMonoTime <= SAFETY_MAX_AGE_NS)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False

