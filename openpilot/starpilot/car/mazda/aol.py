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


# Tolerance for treating a delayed denial as the stop's: the sample must have been taken no later
# than this after the host's last standstill frame. In 39 logged stops (drives 4f-53) panda's
# stop denials began from 14 ms before the host's first standstill frame and ended 4-95 ms before
# its last, so real stop samples fall well inside it. It is a tolerance, not proof of cause: a
# denial for another reason sampled within it also passes, which is why every other check (no
# steering or ACC fault, longitudinal still granted, same panda, session and fresh sample) stays.
STOP_SAMPLE_MARGIN_NS = 50_000_000
# How long after the last standstill frame a delayed stop sample is still explained by the
# stop: the card's restriction release grace, so host and card agree.
STOP_HISTORY_NS = 250_000_000


def note_standstill(history, cp, cs, cs_ns, session):
  """(session, ns) of the host's last standstill frame in Drive for this AOL session.

  A different session discards the history, so a stop never explains a later session's denial.
  """
  try:
    if (cs.standstill and session and cs_ns > 0 and cp.brand == "mazda" and cs.canValid and not cs.canTimeout and
        cs.gearShifter == structs.CarState.GearShifter.drive):
      return (session, int(cs_ns))
  except (AttributeError, TypeError, ValueError):
    return None
  return history if history is not None and history[0] == session else None


def expected_stopped_lateral_denial(cp, cs, native, *, sm, axis_session_id, now_ns, stop_history=None) -> bool:
  """GEN2 with openpilot longitudinal: panda's lateral withdrawal at a stop is not lost permission.

  Panda withholds lateral while the car is stopped (its vehicle_moving gate) and keeps its
  AOL token. With steering-only AOL the driver is on the brake at that moment, so the
  driver-override exemption already covers it. With cruise engaged openpilot is braking,
  and the withdrawal read as a lost grant (controlsMismatch, TAKE CONTROL IMMEDIATELY).
  Lateral still goes inactive on the same frame and needs a fresh panda grant to return;
  this only stops the withdrawal being a fault.

  The denial is treated as the stop's when the host reads standstill now, or when panda took
  the sample no later than STOP_SAMPLE_MARGIN_NS after the host's last standstill frame
  (stop_history, from note_standstill) and the car left standstill at most STOP_HISTORY_NS ago:
  a short stop can end before panda's delayed sample arrives. Host standstill (SPEED < 0.1 m/s) covers panda's not-moving gate
  (<= 0.1 km/h). Everything else stays a fault: a steering fault, an ACC fault, not in
  Drive, a stale or different panda or session, or longitudinal also withdrawn
  (_configured_transport_native refuses a requested-but-denied longitudinal axis).
  """
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native
  try:
    # Cheap per-frame state first: this runs on every frame lateral is desired, and the
    # admission check reads CarParams (about 100 us on the device).
    if native is None or not native.requestedLateral or native.lateralAllowed:
      return False
    stopped_ns = (int(stop_history[1]) if stop_history is not None and axis_session_id and
                  stop_history[0] == axis_session_id else 0)
    stopped = bool(cs.standstill or
      0 < stopped_ns <= now_ns <= stopped_ns + STOP_HISTORY_NS and
      0 < native.observedMonoTime <= stopped_ns + STOP_SAMPLE_MARGIN_NS)
    return bool(stopped and
      cs.canValid and not cs.canTimeout and not cs.steerFaultTemporary and not cs.steerFaultPermanent and
      not cs.accFaulted and cs.gearShifter == structs.CarState.GearShifter.drive and native.pandaInventory and
      cp.brand == "mazda" and cp.openpilotLongitudinalControl and gen2_qualified(cp, marked_only=True) and
      _configured_transport_native(sm, cp, axis_session_id, now_ns, native,
                                   allow_restricted_lateral=True) == native)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False
