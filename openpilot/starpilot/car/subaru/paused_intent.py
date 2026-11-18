"""Retain established Subaru OFF intent; this proof never grants a command."""
from dataclasses import dataclass
from math import isfinite

from opendbc.car import structs
from opendbc.car.subaru.stock_aol import qualified
from opendbc.car.subaru.values import CAR
from openpilot.cereal import log
from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS
from openpilot.starpilot.aol.wire import IntentState, SafetyState


@dataclass(frozen=True)
class StoppedIntentProof:
  session: str
  cp: tuple
  epoch: int
  intent: IntentState
  state: object
  native: SafetyState


def stopped_state(cp, state):
  return bool(cp.carFingerprint == CAR.SUBARU_IMPREZA_2020 and qualified(cp, marked_only=True) and
    state.canValid and not state.canTimeout and state.standstill and state.brakePressed and
    isfinite(state.vEgo) and isfinite(state.vEgoRaw) and
    state.gearShifter == structs.CarState.GearShifter.drive and state.cruiseState.available and
    not state.cruiseState.enabled and not state.gasPressed and not state.regenBraking and
    not state.buttonEvents and not state.steerFaultTemporary and not state.steerFaultPermanent and
    not state.accFaulted and not state.doorOpen and not state.seatbeltUnlatched and
    not state.vehicleSensorsInvalid and not state.steeringDisengage)


def native_off(native):
  return bool(native is not None and native.pandaInventory and not any((native.requestedLateral,
    native.lateralAllowed, native.requestedLongitudinal, native.longitudinalAllowed)))


def record_stopped_intent(sd, state, message, now_ns):
  from openpilot.starpilot.aol.transport_pause import _healthy_transport_native, cp_identity
  sd.aol_stopped_intent_proof = None
  axis = message.aolAxisState
  intent = getattr(sd, 'aol_last_intent', None)
  companion = getattr(sd, 'aol_intent_companion', None)
  if (not stopped_state(sd.CP, state) or not getattr(sd, 'aol_ever_active', False) or sd.aol_authority_lost or sd.enabled or sd.active or
      not message.valid or not axis.qualified or str(axis.faultReason) != 'none' or str(axis.mode) != 'off' or
      str(axis.sessionId) != sd.aol_session_id or str(axis.faultSessionId) != sd.aol_session_id or
      axis.observedMonoTime != now_ns or axis.validUntilMonoTime != now_ns + AXIS_MAX_AGE_NS or
      any((axis.desiredLateral, axis.desiredLongitudinal, axis.lateralActive, axis.longitudinalActive)) or
      not axis.nativeAcknowledged or intent is None or not intent.settingsQualified or
      not intent.allowedLatch or not intent.lateralArmed or intent.pauseLongitudinal or
      companion is None or not companion.last_drain_ok or companion.session != intent.producerSessionId or
      axis.intentProducerSessionId != intent.producerSessionId or axis.intentIntegrityEpoch != companion.integrity_epoch or
      not 0 < axis.sourceCarStateMonoTime == intent.carStateLogMonoTime <= now_ns or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS):
    return
  native = _healthy_transport_native(sd.sm, sd.CP, state, sd.aol_session_id, now_ns)
  if native_off(native):
    sd.aol_stopped_intent_proof = StoppedIntentProof(sd.aol_session_id, cp_identity(sd.CP),
      companion.integrity_epoch, intent, state, native)


def retain_stopped_source(sd, state, intent, source_ns, now_ns):
  from openpilot.selfdrive.selfdrived.events import ET
  from openpilot.starpilot.aol.transport_pause import _healthy_transport_native, cp_identity, HOST_SOURCE_AVAILABILITY_EVENTS
  proof = getattr(sd, 'aol_stopped_intent_proof', None)
  companion = getattr(sd, 'aol_intent_companion', None)
  decision = sd.aol_axis_decision
  if (proof is None or not stopped_state(sd.CP, state) or state is not proof.state or intent != proof.intent or
      sd.aol_session_id != proof.session or cp_identity(sd.CP) != proof.cp or not getattr(sd, 'aol_ever_active', False) or
      sd.aol_authority_lost or sd.enabled or sd.active or
      getattr(sd, 'aol_fault_reason', 'none') not in ('none', 'availabilityPause') or
      any((decision.desired_lateral, decision.desired_longitudinal, decision.lateral_active, decision.longitudinal_active)) or
      companion is None or not companion.last_drain_ok or companion.session != proof.intent.producerSessionId or
      companion.integrity_epoch != proof.epoch or not getattr(sd, 'aol_step_intent_drain_ok', True) or
      getattr(sd, 'car_state_queue_invalid', False) or getattr(sd, 'aol_selected_pair_future', False) or
      not sd.sm.all_checks(['modelV2', 'extrinsicsCalibration', 'driverMonitoringState']) or
      sd.sm['extrinsicsCalibration'].calStatus != log.ExtrinsicsCalibration.Status.calibrated or
      sd.sm['driverMonitoringState'].lockout or sd.sm['driverMonitoringState'].alwaysOnLockout or
      sd.sm['driverMonitoringState'].alertLevel == log.DriverMonitoringState.AlertLevel.three or
      source_ns != proof.intent.carStateLogMonoTime or not 0 < source_ns <= now_ns <= proof.intent.validUntilMonoTime or
      not AXIS_MAX_AGE_NS < now_ns - source_ns <= SAFETY_MAX_AGE_NS or
      any(event.userDisable and event.name != log.OnroadEvent.EventName.pcmDisable or event.immediateDisable or
          event.softDisable and event.name not in HOST_SOURCE_AVAILABILITY_EVENTS for event in sd.events.to_msg()) or
      sd.events.contains(ET.ENABLE)):
    return False
  latest = companion.latest
  raw = latest[2] if latest is not None and latest[1] else None
  if (raw is None or raw.producerSessionId != proof.intent.producerSessionId or not raw.settingsQualified or
      not raw.allowedLatch or not raw.lateralArmed or raw.pauseLongitudinal or
      raw.pauseLateral != proof.intent.pauseLateral or not source_ns <= raw.carStateLogMonoTime <= now_ns or
      not 0 < raw.observedMonoTime <= now_ns <= raw.validUntilMonoTime):
    return False
  for stamp, (value, epoch, producer) in companion.raw_projections.items():
    if stamp >= source_ns and (epoch != proof.epoch or producer != proof.intent.producerSessionId or value is None or
        not value.settingsQualified or not value.allowedLatch or not value.lateralArmed or value.pauseLongitudinal or
        value.pauseLateral != proof.intent.pauseLateral):
      return False
  native = _healthy_transport_native(sd.sm, sd.CP, state, proof.session, now_ns)
  return bool(native_off(native) and native.observedMonoTime >= proof.native.observedMonoTime and
    native.pandaSerial == proof.native.pandaSerial and
    tuple(slot.hardwareSerial for slot in native.pandaInventory) == tuple(slot.hardwareSerial for slot in proof.native.pandaInventory))


def retain_stopped_source_feedback(feedback, sm, cp, state, axis, stamp, now_ns, *, latched, producer):
  from openpilot.starpilot.aol.transport_pause import _healthy_transport_native, cp_identity, host_source_availability_only
  previous = feedback.signature
  if (not stopped_state(cp, state) or not latched or feedback.failed or feedback.paused or feedback.pending_event or
      feedback.retention_started_ns or feedback.source_retention_started_ns or not feedback.ever_lateral_active or
      previous is None or not feedback.session or cp_identity(cp) != feedback.cp or
      previous[0] != feedback.session or previous[7] != feedback.session or previous[5] != 'none' or
      any(previous[9:13]) or not previous[13] or not producer or previous[14] != producer or
      not 0 < previous[8] <= previous[2] <= now_ns or not 0 < feedback.healthy_feedback_ns <= now_ns or
      previous[2] - previous[8] > AXIS_MAX_AGE_NS or previous[4] != previous[2] + AXIS_MAX_AGE_NS or
      now_ns - previous[8] > SAFETY_MAX_AGE_NS or
      not sm.seen['aolAxisState'] or not sm.valid['aolAxisState'] or not sm.alive['aolAxisState'] or
      not axis.qualified or str(axis.mode) != 'off' or str(axis.faultReason) != 'availabilityPause' or
      axis.sourceCarStateMonoTime != 0 or any((axis.desiredLateral, axis.desiredLongitudinal,
        axis.lateralActive, axis.longitudinalActive, axis.nativeAcknowledged)) or
      str(axis.sessionId) != feedback.session or str(axis.faultSessionId) != feedback.session or
      axis.intentProducerSessionId != producer or axis.intentIntegrityEpoch != previous[15] or
      axis.sequence <= feedback.sequence or not previous[2] < stamp == axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime or
      axis.validUntilMonoTime - stamp != AXIS_MAX_AGE_NS):
    return False
  event_ns = int(sm.logMonoTime['onroadEvents'])
  events = [e for e in sm['onroadEvents'] if not (e.name == log.OnroadEvent.EventName.pcmDisable and
    e.userDisable and not e.softDisable and not e.immediateDisable)]
  if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < event_ns == axis.faultEventMonoTime <= stamp or now_ns - event_ns > 1_500_000_000 or
      not host_source_availability_only(events)):
    return False
  native = _healthy_transport_native(sm, cp, state, feedback.session, now_ns)
  return bool(native_off(native) and native.observedMonoTime >= feedback.healthy_native_ns and
    feedback.healthy_inventory == (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)))


def retain_paused_restriction(feedback, sm, cp, state, axis, stamp, now_ns, *, latched, producer):
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native
  previous = feedback.signature
  if (cp.carFingerprint != CAR.SUBARU_IMPREZA_2020 or not qualified(cp, marked_only=True) or
      not latched or feedback.failed or feedback.paused or feedback.pending_event or
      feedback.retention_started_ns or feedback.source_retention_started_ns or not feedback.ever_lateral_active or
      previous is None or not feedback.session or not producer or
      previous[0] != previous[7] or previous[0] != feedback.session or previous[5] != 'none' or
      any(previous[9:13]) or not previous[13] or previous[14:16] != (producer, int(axis.intentIntegrityEpoch)) or
      axis.intentProducerSessionId != producer or str(axis.sessionId) != feedback.session or
      str(axis.faultReason) != 'none' or str(axis.mode) != 'off' or not axis.nativeAcknowledged or
      any((axis.desiredLateral, axis.lateralActive, axis.desiredLongitudinal, axis.longitudinalActive)) or
      not 0 < previous[8] <= previous[2] <= now_ns <= previous[4] or now_ns - previous[8] > AXIS_MAX_AGE_NS or
      not previous[8] <= axis.sourceCarStateMonoTime <= stamp <= now_ns or now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      not state.steerFaultTemporary or state.steerFaultPermanent or not state.canValid or state.canTimeout or
      state.accFaulted or state.doorOpen or state.seatbeltUnlatched or state.vehicleSensorsInvalid or
      state.steeringDisengage or state.buttonEvents or state.gasPressed or state.regenBraking or
      state.gearShifter != structs.CarState.GearShifter.drive or not state.cruiseState.available or state.cruiseState.enabled or
      not 0 < feedback.healthy_feedback_ns <= now_ns or not feedback.healthy_inventory):
    return False
  event_ns = int(sm.logMonoTime['onroadEvents'])
  if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < axis.faultEventMonoTime == event_ns <= stamp or now_ns - event_ns > 1_500_000_000 or
      any(event.immediateDisable or
          event.softDisable and event.name != log.OnroadEvent.EventName.steerTempUnavailable or
          event.userDisable and not (event.name == log.OnroadEvent.EventName.pcmDisable or
            state.brakePressed and event.name == log.OnroadEvent.EventName.pedalPressed) for event in sm['onroadEvents'])):
    return False
  native = _configured_transport_native(sm, cp, feedback.session, now_ns, allow_restricted_lateral=True)
  return bool(native_off(native) and native.observedMonoTime >= feedback.healthy_native_ns and
    feedback.healthy_inventory == (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)))
