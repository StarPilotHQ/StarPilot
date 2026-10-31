from opendbc.car import structs
from opendbc.car.mazda.stock_aol import (GEN2_AOL_SAFETY_PARAM, admitted, gen2_qualified, qualified,
                                        temporary_restriction)
from openpilot.starpilot.aol.policy import AolVehiclePolicy
from openpilot.starpilot.car.mazda.stock_intent import MazdaStockCardIntent


def _profile_for(cp, model, param):
  """Native profile this car is admitted under.

  Parameter 0 is the stock GEN1 profile and only a stock GEN1 car may claim it;
  the GEN2 profile is resolved through its own predicate.
  """
  if model != int(structs.CarParams.SafetyModel.mazda):
    return False
  if param == 0:
    return qualified(cp, marked_only=True)
  return param == GEN2_AOL_SAFETY_PARAM and gen2_qualified(cp, marked_only=True)


def policy_for(cp):
  fits = admitted(cp)
  return AolVehiclePolicy(intent_supported=fits, settings_supported=fits, runtime_supported=fits,
                          normal_runtime_supported=fits, ordinary_axis_ack_required=admitted(cp, marked_only=True),
                          explicit_latch=fits, alternative_experience_addition=32 if fits else 0)


def native_profile_supported(model, param):
  # Driver monitoring uses this car-independent query when deciding whether
  # native lateral actuation counts as engagement. Keep it in exact agreement
  # with the two native Panda profiles; _profile_for() still qualifies each CP.
  return model == int(structs.CarParams.SafetyModel.mazda) and param in (0, GEN2_AOL_SAFETY_PARAM)


def native_accepts_cp(cp, model, param):
  return admitted(cp, marked_only=True) and _profile_for(cp, model, param)


def native_latch_rejected(cp, native):
  return admitted(cp, marked_only=True) and native.requestedLateral and not native.lateralAllowed


def ordinary_axis_request_allowed(cp, state):
  return admitted(cp, marked_only=True) and state.canValid and not state.canTimeout and not state.steerFaultPermanent


def retain_on_native_denial(cp, native, state):
  return temporary_restriction(cp, state)


def create_intent(cp, settings):
  return MazdaStockCardIntent(cp, settings)


def physical_cancel_lateral_allowed(cp, cs, intent, native, *, settings, previous,
                                    source_ns, now_ns, axis_session_id) -> bool:
  """Let the mapped intent owner apply Cancel while retaining a granted LAT axis."""
  from openpilot.starpilot.aol.intent import ButtonType
  from openpilot.starpilot.aol.runtime import (AXIS_MAX_AGE_NS, INTENT_MAX_AGE_NS, SAFETY_MAX_AGE_NS,
                                               native_inventory_matches_cp)
  try:
    return bool(admitted(cp, marked_only=True) and settings is not None and settings.enabled and
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
