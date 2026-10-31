"""Keep Ford lateral-only intent during a short command lease withdrawal; no grant."""
from opendbc.car import structs
from opendbc.car.ford.aol import qualified
from openpilot.cereal import log
from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS, current_native
from openpilot.starpilot.aol.transport_pause import cp_identity


def retain_command_pause_intent(feedback, sm, cp, cs, axis, now_ns, *, latched, producer):
  """Retain only the original fresh LAT-only proof, without renewing any lease."""
  try:
    if (not qualified(cp, marked_only=True) or not latched or
        feedback.cp != cp_identity(cp) or feedback.failed or feedback.paused or feedback.pending_event or
        feedback.awaiting or feedback.retention_started_ns or feedback.retention_pending or feedback.retention_qualified or
        feedback.source_retention_started_ns or not feedback.lateral_only_baseline or not feedback.lateral_inventory or
        not cs.canValid or cs.canTimeout or cs.steerFaultTemporary or cs.steerFaultPermanent or cs.vehicleSensorsInvalid or
        cs.accFaulted or cs.steeringDisengage or cs.brakePressed or cs.gasPressed or cs.regenBraking or
        cs.doorOpen or cs.seatbeltUnlatched or cs.buttonEvents or cs.gearShifter != structs.CarState.GearShifter.drive or
        not cs.cruiseState.available):
      return False
    previous = feedback.signature
    stamp = int(sm.logMonoTime['aolAxisState'])
    if (previous is None or not producer or producer != feedback.availability_producer or
        feedback.availability_epoch is None or not feedback.session or
        previous[0] != feedback.session or previous[7] != feedback.session or previous[5] != 'none' or
        not previous[9] or previous[10] or not previous[11] or previous[12] or not previous[13] or
        previous[14] != producer or previous[15] != feedback.availability_epoch or
        not 0 < previous[8] <= previous[2] == previous[3] <= previous[4] < now_ns or
        now_ns - previous[8] > SAFETY_MAX_AGE_NS or
        not 0 < feedback.lateral_source_ns <= feedback.lateral_baseline_ns <= now_ns or
        now_ns - feedback.lateral_source_ns > SAFETY_MAX_AGE_NS or
        not sm.seen['aolAxisState'] or not sm.valid['aolAxisState'] or not sm.alive['aolAxisState'] or
        str(axis.sessionId) != feedback.session or str(axis.faultSessionId) != feedback.session or
        str(axis.intentProducerSessionId) != producer or axis.intentIntegrityEpoch != feedback.availability_epoch or
        str(axis.faultReason) != 'none' or not axis.qualified or not axis.nativeAcknowledged or
        not axis.desiredLateral or axis.desiredLongitudinal or axis.longitudinalActive or
        not 0 < previous[8] <= axis.sourceCarStateMonoTime <= stamp == axis.observedMonoTime <= now_ns or
        now_ns - axis.sourceCarStateMonoTime > SAFETY_MAX_AGE_NS or
        not stamp <= axis.validUntilMonoTime <= stamp + AXIS_MAX_AGE_NS or
        axis.sequence < feedback.sequence or axis.sequence == feedback.sequence and stamp != previous[2] or
        not previous[6] <= axis.faultEventMonoTime <= stamp):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        any(e.immediateDisable or e.softDisable or e.userDisable and e.name != log.OnroadEvent.EventName.pcmDisable
            for e in sm['onroadEvents'])):
      return False
    native = current_native(sm, cp, now_ns=now_ns, axis_session_id=feedback.session)
    if (native is None or native.requestedLateral or native.lateralAllowed or native.requestedLongitudinal or
        native.longitudinalAllowed or not native.pandaInventory or not native.sourcePandaStatesMonoTime or
        native.observedMonoTime < feedback.lateral_native_ns or
        (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != feedback.lateral_inventory or
        any(p.controlsAllowed for p in native.pandaInventory) or
        not sm.seen['pandaStates'] or not sm.valid['pandaStates'] or not sm.alive['pandaStates'] or
        int(sm.logMonoTime['pandaStates']) <= 0 or len(sm['pandaStates']) != len(cp.safetyConfigs)):
      return False
    return all(int(p.safetyModel.raw) == int(c.safetyModel.raw) and p.safetyParam == c.safetyParam and
               p.alternativeExperience == cp.alternativeExperience and not p.controlsAllowed and
               not p.safetyRxChecksInvalid and not p.heartbeatLost and not p.faults
               for p, c in zip(sm['pandaStates'], cp.safetyConfigs, strict=True))
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False


def retain_gear_withdrawal_intent(feedback, sm, cp, cs, axis, now_ns, *, latched, producer):
  """Native reverse withdrawal may precede the 10Hz request echo; retain intent, never an output grant."""
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native
  try:
    previous = feedback.signature
    stamp = int(sm.logMonoTime['aolAxisState'])
    if (not qualified(cp, marked_only=True) or not latched or feedback.failed or feedback.paused or
        feedback.pending_event or feedback.retention_started_ns or feedback.source_retention_started_ns or
        feedback.cp != cp_identity(cp) or not feedback.session or previous is None or
        previous[0] != feedback.session or previous[7] != feedback.session or previous[5] != 'none' or
        previous[9:13] != (True, False, True, False) or not previous[13] or
        not producer or previous[14] != producer or
        not 0 < previous[8] <= previous[2] <= feedback.healthy_feedback_ns <= now_ns or
        now_ns - previous[8] > SAFETY_MAX_AGE_NS or
        cs.gearShifter not in (structs.CarState.GearShifter.reverse, structs.CarState.GearShifter.neutral) or
        not cs.canValid or cs.canTimeout or cs.steerFaultTemporary or cs.steerFaultPermanent or
        cs.vehicleSensorsInvalid or cs.accFaulted or cs.doorOpen or cs.seatbeltUnlatched or
        cs.steeringDisengage or not cs.cruiseState.available or cs.buttonEvents or
        not sm.seen['aolAxisState'] or not sm.alive['aolAxisState'] or
        str(axis.sessionId) != feedback.session or str(axis.faultSessionId) != feedback.session or
        str(axis.intentProducerSessionId) != producer or axis.intentIntegrityEpoch != previous[15] or
        str(axis.faultReason) != 'none' or str(axis.mode) != 'off' or
        axis.desiredLateral or axis.desiredLongitudinal or axis.lateralActive or axis.longitudinalActive or
        axis.sequence <= feedback.sequence or not previous[2] < stamp == axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime or
        axis.validUntilMonoTime - stamp != AXIS_MAX_AGE_NS):
      return False
    current = (sm.valid['aolAxisState'] and axis.qualified and axis.nativeAcknowledged and
      0 < axis.sourceCarStateMonoTime <= stamp and now_ns - axis.sourceCarStateMonoTime <= AXIS_MAX_AGE_NS)
    gap = (not sm.valid['aolAxisState'] and not axis.qualified and not axis.nativeAcknowledged and axis.sourceCarStateMonoTime == 0)
    event_ns = int(sm.logMonoTime['onroadEvents'])
    if (not (current or gap) or not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns == axis.faultEventMonoTime <= stamp or now_ns - event_ns > 1_500_000_000 or
        any(event.immediateDisable or event.softDisable and event.name != log.OnroadEvent.EventName.wrongGear or
            event.userDisable and event.name not in (log.OnroadEvent.EventName.reverseGear, log.OnroadEvent.EventName.pcmDisable)
            for event in sm['onroadEvents'])):
      return False
    native = _configured_transport_native(sm, cp, feedback.session, now_ns, allow_restricted_lateral=True)
    return bool(native is not None and native.pandaInventory and native.requestedLateral and
      not native.lateralAllowed and not native.requestedLongitudinal and not native.longitudinalAllowed and
      native.observedMonoTime >= feedback.healthy_native_ns and feedback.healthy_inventory ==
        (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)))
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False
