from opendbc.car import structs
from opendbc.car.mazda.stock_aol import qualified, temporary_restriction
from openpilot.starpilot.aol.policy import AolVehiclePolicy
from openpilot.starpilot.car.mazda.stock_intent import MazdaStockCardIntent


def policy_for(cp):
  admitted = qualified(cp)
  return AolVehiclePolicy(intent_supported=admitted, settings_supported=admitted, runtime_supported=admitted,
                          normal_runtime_supported=admitted, ordinary_axis_ack_required=qualified(cp, marked_only=True),
                          explicit_latch=admitted, alternative_experience_addition=32 if admitted else 0)


def native_profile_supported(model, param):
  return model == int(structs.CarParams.SafetyModel.mazda) and param == 0


def native_accepts_cp(cp, model, param):
  return qualified(cp, marked_only=True) and native_profile_supported(model, param)


def native_latch_rejected(cp, native):
  return qualified(cp, marked_only=True) and native.requestedLateral and not native.lateralAllowed


def ordinary_axis_request_allowed(cp, state):
  return qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and not state.steerFaultPermanent


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
    return bool(qualified(cp, marked_only=True) and settings is not None and settings.enabled and
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


def expected_stopped_lateral_denial(cp, state, native, *, sm, axis_session_id, now_ns):
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native

  return bool(qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and
    state.standstill and state.gearShifter == structs.CarState.GearShifter.drive and
    state.cruiseState.available and not state.steerFaultPermanent and not state.accFaulted and
    native is not None and native.pandaInventory and native.requestedLateral and not native.lateralAllowed and
    not native.requestedLongitudinal and not native.longitudinalAllowed and
    _configured_transport_native(sm, cp, axis_session_id, now_ns, native, allow_restricted_lateral=True) == native)
