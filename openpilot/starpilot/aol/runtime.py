"""Bounded AOL axis requests and native acknowledgment checks.

The requester and the actuator are deliberately separate: a native permission
cannot be required before the host sends its first desired-axis request.
"""

from __future__ import annotations

from dataclasses import dataclass

from opendbc.car.structs import car
from openpilot.starpilot.aol.panda_faults import blocking_fault_mask
from openpilot.starpilot.aol.vehicle import native_admission_key, native_matches_cp, native_profile_supported
from openpilot.starpilot.aol.wire import INTENT_SERVICE, SAFETY_SERVICE, IntentState, decode_intent, decode_safety

INTENT_MAX_AGE_NS = 30_000_000
SAFETY_MAX_AGE_NS = 200_000_000
AXIS_MAX_AGE_NS = 30_000_000


@dataclass(frozen=True)
class AxisDecision:
  desired_lateral: bool = False
  desired_longitudinal: bool = False
  lateral_active: bool = False
  longitudinal_active: bool = False
  native_acknowledged: bool = False

  @property
  def mode(self) -> str:
    if self.lateral_active and self.longitudinal_active:
      return 'combined'
    if self.lateral_active:
      return 'lateralOnly'
    if self.longitudinal_active:
      return 'longitudinalOnly'
    return 'off'


def current_intent(sm, *, car_state_ns: int, now_ns: int, previous: IntentState | None = None, companion=None, drain=True):
  if companion is not None:
    return companion.current(sm, source_ns=car_state_ns, now_ns=now_ns, drain=drain)
  if not (sm.valid[INTENT_SERVICE] and sm.alive[INTENT_SERVICE] and sm.seen[INTENT_SERVICE]):
    return None
  intent = decode_intent(sm[INTENT_SERVICE])
  if intent is None:
    return None
  # Intent is published before its carState wakeup. If the carState socket
  # times out exactly between those sends, only the already accepted companion
  # for that *same retained carState* may bridge the publication boundary.
  # Revalidate its original lease; never accept a newer intent for older CAN.
  if (intent.carStateLogMonoTime > car_state_ns and previous is not None and
      previous.carStateLogMonoTime == car_state_ns and intent.settingsQualified and
      intent.producerSessionId == previous.producerSessionId and
      int(sm.logMonoTime[INTENT_SERVICE]) == intent.carStateLogMonoTime and
      intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime and
      now_ns - intent.observedMonoTime <= INTENT_MAX_AGE_NS):
    intent = previous
    message_ns = int(previous.carStateLogMonoTime)
  else:
    message_ns = int(sm.logMonoTime[INTENT_SERVICE])
  observed = int(intent.observedMonoTime)
  produced_for = int(intent.carStateLogMonoTime)
  if (not intent.settingsQualified or not intent.producerSessionId or produced_for <= 0 or
      message_ns != produced_for or
      produced_for > car_state_ns or car_state_ns - produced_for > INTENT_MAX_AGE_NS or
      observed > now_ns or now_ns > int(intent.validUntilMonoTime) or
      now_ns - observed > INTENT_MAX_AGE_NS):
    return None
  return intent


def native_inventory_matches_cp(state, CP) -> bool:
  """Bind the selected serial to the ordered same-read configuration inventory.

  Historical v1 wires retain single-Panda compatibility only; they carry no
  inventory qualification. Multi-Panda admission requires coherent production v2
  with the exact configuration and one owner.
  The source PandaStates stamp is BOOTTIME provenance, never a monotonic lease.
  """
  if not state.pandaInventory:
    return state.sourcePandaStatesMonoTime == 0 and len(CP.safetyConfigs) == 1
  try:
    slots = state.pandaInventory
    configs = CP.safetyConfigs
    if state.sourcePandaStatesMonoTime <= 0 or not 1 <= len(slots) <= 2 or len(slots) != len(configs):
      return False
    serials = [slot.hardwareSerial for slot in slots]
    if any(not serial for serial in serials) or len(set(serials)) != len(serials):
      return False
    owners = []
    for index, (slot, config) in enumerate(zip(slots, configs, strict=True)):
      if slot.safetyRxChecksInvalid or slot.heartbeatLost or blocking_fault_mask(slot.faults, CP):
        return False
      if (slot.slotIndex != index or slot.safetyModel != int(config.safetyModel.raw) or
          slot.safetyParam != int(config.safetyParam) or slot.alternativeExperience != int(CP.alternativeExperience)):
        return False
      if str(config.safetyModel) in ('silent', 'noOutput'):
        if slot.safetyParam != 0 or slot.controlsAllowed:
          return False
      else:
        owners.append(slot)
    return bool(len(owners) == 1 and owners[0].hardwareSerial == state.pandaSerial and
                owners[0].safetyModel == state.safetyModel and owners[0].safetyParam == state.safetyParam)
  except (AttributeError, TypeError, ValueError, OverflowError, IndexError):
    return False


def _clear_native_admission(sm):
  try:
    sm._aol_native_admission = None
  except Exception:
    # Custom/slotted owners may not support this optional optimization.
    pass


def _native_admission_matches(sm, CP, state, raw):
  key = None
  try:
    cp_key = native_admission_key(CP) if state.pandaInventory else None
    if cp_key is not None and isinstance(raw, bytes):
      key = (raw, int(sm.logMonoTime[SAFETY_SERVICE]), cp_key)
      if getattr(sm, '_aol_native_admission', None) == key:
        return True
  except Exception:
    # Cache plumbing may never replace or prevent the original validation.
    key = None
  matches = (native_matches_cp(CP, int(state.safetyModel), int(state.safetyParam)) and
             native_inventory_matches_cp(state, CP))
  try:
    # Re-read mutable CP on a miss before binding the successful result.
    sm._aol_native_admission = key if matches and key is not None and native_admission_key(CP) == key[2] else None
  except Exception:
    _clear_native_admission(sm)
  return matches


def current_native(sm, CP, *, now_ns: int, axis_session_id: str | None = None):
  if not (sm.valid[SAFETY_SERVICE] and sm.alive[SAFETY_SERVICE] and sm.seen[SAFETY_SERVICE]):
    _clear_native_admission(sm)
    return None
  raw = sm[SAFETY_SERVICE]
  raw = bytes(raw) if isinstance(raw, (bytes, bytearray, memoryview)) else raw
  state = decode_safety(raw)
  if state is None:
    _clear_native_admission(sm)
    return None
  if (not _native_admission_matches(sm, CP, state, raw) or
      not state.compatible or int(state.protocolVersion) != 1 or not state.pandaSerial or not state.axisSessionId or
      (axis_session_id is not None and str(state.axisSessionId) != axis_session_id) or
      int(sm.logMonoTime[SAFETY_SERVICE]) > now_ns or
      now_ns - int(sm.logMonoTime[SAFETY_SERVICE]) > SAFETY_MAX_AGE_NS or
      int(state.observedMonoTime) > now_ns or now_ns > int(state.validUntilMonoTime)):
    _clear_native_admission(sm)
    return None
  if state.pandaInventory and (int(sm.logMonoTime[SAFETY_SERVICE]) != state.observedMonoTime or
                               state.observedMonoTime <= 0 or
                               state.validUntilMonoTime - state.observedMonoTime > SAFETY_MAX_AGE_NS):
    _clear_native_admission(sm)
    return None
  return state


def known_reverse_pause(car_state, samples=()) -> bool:
  """Include the known Neutral transition into and out of Reverse."""
  states = [car_state, *(state for state, _ in samples)]
  gears = car.CarState.GearShifter
  return bool(all(valid for _, valid in samples) and
              any(state.gearShifter in (gears.neutral, gears.reverse) for state in states) and
              all(state.canValid and not state.canTimeout and state.gearShifter in
                  (gears.drive, gears.low, gears.sport, gears.brake, gears.neutral, gears.reverse) for state in states))


def decide_axes(*, standard_lateral: bool, standard_longitudinal: bool, intent,
                native, car_state, initialized: bool, model_ready: bool,
                no_entry: bool, immediate_disable: bool, dm_lockout: bool,
                pause_brake_mps: float, lateral_inhibit: bool = False, gear_pause: bool = False) -> AxisDecision:
  if intent is None or not initialized or not car_state.canValid or immediate_disable or no_entry or dm_lockout:
    return AxisDecision()
  gear = car_state.gearShifter
  driving = not gear_pause and gear not in (car.CarState.GearShifter.neutral, car.CarState.GearShifter.park,
                         car.CarState.GearShifter.reverse, car.CarState.GearShifter.unknown)
  lateral_safe = (driving and model_ready and not lateral_inhibit and not car_state.steerFaultPermanent and
                  not car_state.steerFaultTemporary and
                  (pause_brake_mps <= 0 or not car_state.brakePressed or car_state.vEgo >= pause_brake_mps or car_state.standstill))
  desired_lateral = bool(lateral_safe and not intent.pauseLateral and (standard_lateral or intent.allowedLatch))
  desired_longitudinal = bool(driving and standard_longitudinal and not car_state.doorOpen and
                              not car_state.seatbeltUnlatched and not intent.pauseLongitudinal)
  lateral_ack = native is not None and bool(native.requestedLateral) == desired_lateral
  longitudinal_ack = native is not None and bool(native.requestedLongitudinal) == desired_longitudinal
  return AxisDecision(
    desired_lateral=desired_lateral, desired_longitudinal=desired_longitudinal,
    lateral_active=bool(desired_lateral and lateral_ack and native.lateralAllowed),
    longitudinal_active=bool(desired_longitudinal and longitudinal_ack and native.longitudinalAllowed),
    native_acknowledged=bool(native is not None),
  )


def current_axis(sm, *, now_ns: int):
  if not (sm.valid['aolAxisState'] and sm.alive['aolAxisState'] and sm.seen['aolAxisState']):
    return None
  state = sm['aolAxisState']
  if (not state.qualified or not state.sessionId or
      int(sm.logMonoTime['aolAxisState']) > now_ns or
      now_ns - int(sm.logMonoTime['aolAxisState']) > AXIS_MAX_AGE_NS or
      int(state.observedMonoTime) > now_ns or now_ns > int(state.validUntilMonoTime)):
    return None
  return state


def refresh_expired_lateral_snapshot(sm, *, now_ns: int, allow_combined: bool = False) -> bool:
  """Receive a replacement AOL proof; an expired proof never gains time."""
  axis = sm['aolAxisState']
  if (sm.seen['aolAxisState'] and sm.valid['aolAxisState'] and sm.alive['aolAxisState'] and
      axis.qualified and axis.nativeAcknowledged and axis.sessionId and
      axis.lateralActive and axis.desiredLateral and
      ((not axis.longitudinalActive and not axis.desiredLongitudinal) or
       (allow_combined and axis.longitudinalActive and axis.desiredLongitudinal)) and
      0 < sm.logMonoTime['aolAxisState'] <= axis.observedMonoTime <= axis.validUntilMonoTime < now_ns):
    # All normal authority, source, physical and native gates run on the newly
    # received snapshot. An empty receive leaves the expired proof untouched.
    header_ns = sm.logMonoTime['aolAxisState']
    sm.update(0)
    if allow_combined and sm.logMonoTime['aolAxisState'] == header_ns:
      from openpilot.cereal import messaging
      # GM EPS can refuse a one-frame withdrawal. Wait at most one control tick
      # for a new proof; all permission and physical checks still run afterward.
      poller = messaging.Poller()
      poller.registerSocket(sm.sock['aolAxisState'])
      poller.poll(10)
      sm.update(0)
    return True
  return False


def monitoring_lateral_engaged(sm, *, now_ns: int) -> bool:
  axis = current_axis(sm, now_ns=now_ns)
  if axis is None or not axis.lateralActive or not axis.nativeAcknowledged:
    return False
  if not (sm.valid[SAFETY_SERVICE] and sm.seen[SAFETY_SERVICE] and sm.alive[SAFETY_SERVICE]):
    return False
  native = decode_safety(sm[SAFETY_SERVICE])
  if native is None:
    return False
  return bool(native.compatible and int(native.protocolVersion) == 1 and
              native.axisSessionId == axis.sessionId and
              native_profile_supported(int(native.safetyModel), int(native.safetyParam)) and
              int(sm.logMonoTime[SAFETY_SERVICE]) <= now_ns and
              now_ns - int(sm.logMonoTime[SAFETY_SERVICE]) <= SAFETY_MAX_AGE_NS and
              int(native.observedMonoTime) <= now_ns <= int(native.validUntilMonoTime) and
              native.requestedLateral and native.lateralAllowed)


def ordinary_lateral_requested(active: bool, CS, CP) -> bool:
  """The standard lateral output gate, shared by request and actuation owners."""
  standstill = abs(CS.vEgo) <= max(CP.minSteerSpeed, 0.3) or CS.standstill
  return bool(active and not CS.steerFaultTemporary and not CS.steerFaultPermanent and
              (not standstill or CP.steerAtStandstill))


def decide_ordinary_axis(*, requested: bool, native) -> AxisDecision:
  """Acknowledgment cannot create driver intent or an independent axis."""
  acknowledged = bool(native is not None and native.requestedLateral == requested and
                      not native.requestedLongitudinal)
  return AxisDecision(desired_lateral=bool(requested),
                      lateral_active=bool(requested and acknowledged and native.lateralAllowed),
                      native_acknowledged=acknowledged)


def ordinary_axis_acknowledged(sm, CP, *, now_ns: int, on_validated_native=None) -> bool:
  axis = current_axis(sm, now_ns=now_ns)
  native = current_native(sm, CP, now_ns=now_ns,
                          axis_session_id=str(axis.sessionId) if axis is not None else None)
  acknowledged = bool(axis is not None and native is not None and axis.nativeAcknowledged and
                      axis.desiredLateral and axis.lateralActive and not axis.desiredLongitudinal and
                      native.requestedLateral and not native.requestedLongitudinal and native.lateralAllowed)
  if acknowledged and on_validated_native is not None:
    try:
      on_validated_native(native)
    except Exception:
      pass  # Optional evidence observation cannot change actuator admission.
  return acknowledged
