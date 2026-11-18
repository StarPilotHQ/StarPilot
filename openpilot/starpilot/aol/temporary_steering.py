"""Temporary EPS intent retention; native permission and original deadlines remain required."""
from openpilot.cereal import log
from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS


def temporary_steering_intent_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer, signature):
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native, stock_lateral_user_event
  from openpilot.starpilot.car.ford.aol import temporary_eps_restriction as ford_temporary_eps_restriction
  if cp.brand == 'ford' and cp.openpilotLongitudinalControl and not ford_temporary_eps_restriction(cp, cs):
    return False
  if (not latched or self.failed or self.paused or self.retention_started_ns or
    not self.session or str(axis.sessionId) != self.session or not cs.steerFaultTemporary or
    cs.steerFaultPermanent or not cs.canValid or cs.canTimeout or
    str(axis.faultReason) != 'none' or
    not producer or producer != self.availability_producer or
    str(getattr(axis, 'intentProducerSessionId', '')) != producer or
    int(getattr(axis, 'intentIntegrityEpoch', 0)) != self.availability_epoch or
    not self.lateral_baseline_ns or not self.lateral_inventory or
    not 0 < axis.sourceCarStateMonoTime <= stamp <= now_ns or
    now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS):
    return False
  withdrawn = not axis.desiredLateral and not axis.lateralActive
  previous = self.signature
  pending = bool(self.pending_event)
  all_off = bool(withdrawn and not axis.desiredLongitudinal and not axis.longitudinalActive and str(axis.mode) == 'off')
  event_ns = int(sm.logMonoTime['onroadEvents'])
  same_longitudinal = bool(previous is not None and
    (axis.longitudinalActive, axis.desiredLongitudinal) == (previous[10], previous[12]))
  withdrawal_role = bool(all_off or withdrawn and same_longitudinal and
    (cs.steeringPressed or self.temporary_eps_event_ns and previous is not None and not previous[9] and not previous[11]))
  if pending and (not withdrawal_role or previous is None or previous[5] != 'none' or
    previous[0] != self.session or previous[7] != self.session or
    previous[1] != self.sequence or previous[6] not in (self.pending_event, self.temporary_eps_event_ns) or
    previous[9] or previous[11] or not same_longitudinal or
    previous[14] != producer or previous[15] != self.availability_epoch or
    axis.faultEventMonoTime not in (self.pending_event, self.temporary_eps_event_ns) or
    max(axis.faultEventMonoTime, event_ns) != self.pending_event or
    axis.sourceCarStateMonoTime < previous[8] or stamp < previous[2] or
    not 0 < self.pending_event <= now_ns or now_ns - self.pending_event > AXIS_MAX_AGE_NS):
    return False
  # Prior ACTIVE identity in first temporary-EPS Card cycle: original source/output lease only;
  # No permission-deadline/baseline renewal.
  fresh_active_successor = bool(previous is not None and axis.sequence > self.sequence and
    previous[5] == 'none' and previous[9] and previous[11] and previous[13] and
    previous[6] == axis.faultEventMonoTime and previous[14] == producer and
    previous[15] == self.availability_epoch and
    axis.sourceCarStateMonoTime >= previous[8] and stamp >= previous[2] and
    0 < previous[8] <= previous[2] <= now_ns <= previous[4] and
    now_ns - previous[8] <= AXIS_MAX_AGE_NS and
    0 < self.lateral_source_ns <= now_ns and
    now_ns - self.lateral_source_ns <= AXIS_MAX_AGE_NS)
  repeated_native_first = bool(signature == previous and not axis.lateralActive and
    0 < now_ns <= self.hkg_eps_join_until_ns)
  held_original = bool(axis.desiredLateral and (axis.lateralActive or
    cp.brand == 'hyundai' and str(cp.steerControlType) == 'angle' and
    (fresh_active_successor or repeated_native_first)) and axis.nativeAcknowledged and
    (signature == previous or fresh_active_successor))
  if not withdrawn and not held_original:
    return False
  stock_user = stock_lateral_user_event(cp)
  stock_eps_user = bool(self.lateral_only_baseline and cp.pcmCruise and
    not axis.desiredLongitudinal and not axis.longitudinalActive and not cs.steeringDisengage and
    cs.cruiseState.available and not cs.cruiseState.enabled and stock_user is not None and
    any(event.name == stock_user and event.userDisable for event in sm['onroadEvents']))
  from openpilot.starpilot.car.subaru.aol import temporary_eps_brake_recovery
  subaru_brake = bool(stock_eps_user and temporary_eps_brake_recovery(
    cp, cs, previous_brake=bool(self.subaru_eps_brake_event_ns and self.subaru_eps_brake_event_ns == event_ns)))
  from opendbc.car import structs
  from opendbc.car.honda.stock_aol import qualified as honda_qualified
  # Existing LAT intent only: brake still removes LONG, and EPS still blocks output.
  honda_eps = bool(honda_qualified(cp, marked_only=True) and cs.steerFaultTemporary and
    self.lateral_only_baseline and not axis.desiredLongitudinal and not axis.longitudinalActive and
    cs.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport,
                      structs.CarState.GearShifter.low, structs.CarState.GearShifter.brake) and
    cs.cruiseState.available and not cs.accFaulted and not cs.doorOpen and
    not cs.vehicleSensorsInvalid and not cs.steeringDisengage and not cs.buttonEvents)
  honda_brake_release = bool(honda_eps and not cs.brakePressed and
    0 < event_ns == self.temporary_eps_event_ns == getattr(self, 'honda_eps_brake_event_ns', 0) and
    0 < getattr(self, 'honda_eps_source_ns', 0) <= now_ns <= self.honda_eps_source_ns + SAFETY_MAX_AGE_NS)
  # Seatbelt blocks normal LONG, not admitted LAT intent. Keep warnings/native/output checks.
  events = tuple(event for event in sm['onroadEvents'] if not (
    stock_eps_user and event.name == stock_user and event.userDisable and
    not event.softDisable and not event.immediateDisable) and not (
    (subaru_brake or honda_eps and (cs.brakePressed or honda_brake_release)) and
    event.name == log.OnroadEvent.EventName.pedalPressed and event.userDisable and
    not event.softDisable and not event.immediateDisable) and not (
    self.lateral_only_baseline and cs.seatbeltUnlatched and not axis.desiredLongitudinal and
    not axis.longitudinalActive and event.name == log.OnroadEvent.EventName.seatbeltNotLatched and
    event.softDisable and event.noEntry and not event.immediateDisable and not event.userDisable))
  if honda_eps and any(event.userDisable for event in events):
    return False
  temporary_names = (log.OnroadEvent.EventName.steerTempUnavailable,
    log.OnroadEvent.EventName.steerTempUnavailableSilent)
  if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
      any(event.immediateDisable or event.softDisable and event.name != temporary_names[0] for event in events) or
      pending and any(event.userDisable for event in events)):
    return False
  temporary_event = any(event.name in temporary_names for event in events)
  override_temporary = bool((cs.steeringPressed or 0 < event_ns == self.temporary_eps_event_ns) and not temporary_event and
    any(event.name == log.OnroadEvent.EventName.steerOverride for event in events) and
    not any(event.softDisable or event.userDisable for event in events))
  matched_withdrawal = bool((temporary_event or override_temporary) and axis.faultEventMonoTime == event_ns <= stamp)
  native_first_predecessor = bool(cp.brand == 'hyundai' and str(cp.steerControlType) == 'angle' and
    previous is not None and not previous[9] and previous[11] and previous[13] and
    not previous[10] and not previous[12] and 0 < now_ns <= self.hkg_eps_join_until_ns)
  original_active = bool(previous is not None and previous[0] == self.session and
    previous[7] == self.session and previous[5] == 'none' and
    (previous[9] or native_first_predecessor) and previous[11] and previous[13] and previous[6] == event_ns and
    axis.sourceCarStateMonoTime >= previous[8] and stamp >= previous[2] and
    previous[14] == producer and previous[15] == self.availability_epoch and
    0 < self.lateral_source_ns <= previous[8] <= previous[2] <= now_ns <= previous[4] and
    now_ns - previous[8] <= AXIS_MAX_AGE_NS)
  repeated_join = bool(self.temporary_eps_event_ns and previous is not None and withdrawn and
    not previous[9] and not previous[11] and same_longitudinal and
    previous[6] == self.temporary_eps_event_ns and (temporary_event or override_temporary) and
    axis.faultEventMonoTime != event_ns and
    min(axis.faultEventMonoTime, event_ns) == self.temporary_eps_event_ns and
    0 < max(axis.faultEventMonoTime, event_ns) <= now_ns and
    now_ns - max(axis.faultEventMonoTime, event_ns) <= AXIS_MAX_AGE_NS)
  pending_withdrawal = bool(withdrawal_role and not any(event.softDisable or event.userDisable for event in events) and
    not temporary_event and 0 < event_ns < axis.faultEventMonoTime <= stamp and
    now_ns - axis.faultEventMonoTime <= AXIS_MAX_AGE_NS and
    (pending or original_active and axis.sequence > self.sequence))
  if withdrawn and not (matched_withdrawal or pending_withdrawal or repeated_join):
    return False
  if held_original and not (
      axis.faultEventMonoTime == event_ns <= stamp and not any(event.softDisable for event in events) or
      (temporary_event or override_temporary) and 0 < axis.faultEventMonoTime < event_ns and
      now_ns - event_ns <= AXIS_MAX_AGE_NS):
    return False
  # Intent only: temporary EPS still blocks transport/availability health proofs.
  from openpilot.starpilot.car.nissan.aol import temporary_eps_restriction
  stock_ford_eps = bool(ford_temporary_eps_restriction(cp, cs) and
    all(not event.userDisable or event.name in (log.OnroadEvent.EventName.pcmDisable,
        log.OnroadEvent.EventName.pedalPressed) and not event.softDisable and not event.immediateDisable for event in events))
  from opendbc.car.mazda.stock_aol import qualified as mazda_qualified
  stock_mazda_eps = bool(mazda_qualified(cp, marked_only=True) and
    cs.cruiseState.available and not cs.cruiseState.enabled and
    cs.gearShifter == structs.CarState.GearShifter.drive and
    not cs.brakePressed and not cs.regenBraking and not cs.gasPressed and
    not cs.doorOpen and not cs.accFaulted and
    not cs.vehicleSensorsInvalid and not cs.steeringDisengage and not cs.buttonEvents and
    all(not event.userDisable or event.name == log.OnroadEvent.EventName.pcmDisable and
        not event.softDisable and not event.immediateDisable for event in events))
  from opendbc.car.rivian.stock_aol import temporary_eps_restriction as rivian_temporary_eps_restriction
  stock_rivian_eps = bool(rivian_temporary_eps_restriction(cp, cs) and
    all(not event.userDisable or event.name == log.OnroadEvent.EventName.pcmDisable and
        not event.softDisable and not event.immediateDisable for event in events))
  from openpilot.starpilot.car.volkswagen.aol import temporary_eps_restriction as volkswagen_temporary_eps_restriction
  stock_volkswagen_eps = bool(volkswagen_temporary_eps_restriction(cp, cs) and
    all(not event.userDisable or event.name == log.OnroadEvent.EventName.pcmDisable and
        not event.softDisable and not event.immediateDisable for event in events))
  from opendbc.car.subaru.stock_aol import temporary_restriction as subaru_temporary_restriction
  subaru_predecessor = bool(previous is not None and
    0 < previous[8] <= previous[2] <= now_ns <= previous[4] and
    now_ns - previous[8] <= AXIS_MAX_AGE_NS and
    (previous[9] and previous[11] and previous[13] or
     self.temporary_eps_event_ns and previous[6] == self.temporary_eps_event_ns))
  stock_subaru_eps = bool(subaru_temporary_restriction(cp, cs) and subaru_predecessor and
    cs.gearShifter == structs.CarState.GearShifter.drive and not cs.doorOpen and
    not cs.vehicleSensorsInvalid and not cs.steeringDisengage and not cs.buttonEvents and
    all(not event.userDisable or event.name == log.OnroadEvent.EventName.pcmDisable and
        not event.softDisable and not event.immediateDisable for event in events))
  from opendbc.car.hyundai.canfd_angle_aol import qualified as hkg_angle_qualified, temporary_restriction as hkg_angle_restriction
  hkg_angle_eps = bool(cp.brand == 'hyundai' and hkg_angle_qualified(cp, marked_only=True) and
    hkg_angle_restriction(cp, cs) and cs.gearShifter == structs.CarState.GearShifter.drive and
    not cs.accFaulted and not cs.doorOpen and not cs.vehicleSensorsInvalid and not cs.steeringDisengage and
    all(not event.userDisable for event in events))
  ford_alpha_eps = bool(stock_ford_eps and cp.openpilotLongitudinalControl)
  restricted_eps = bool((temporary_eps_restriction(cp, cs) or stock_ford_eps or stock_mazda_eps or stock_rivian_eps or
                        stock_volkswagen_eps or stock_subaru_eps) and self.lateral_only_baseline and
    not axis.desiredLongitudinal and not axis.longitudinalActive) or ford_alpha_eps or hkg_angle_eps or honda_eps
  native = _configured_transport_native(sm, cp, self.session, now_ns,
    require_longitudinal=not ford_alpha_eps or axis.longitudinalActive, allow_restricted_lateral=restricted_eps)
  if (native is None or not native.pandaInventory or
      stock_eps_user and (native.requestedLongitudinal or native.longitudinalAllowed) or
      held_original and not (native.requestedLateral and (native.lateralAllowed or
        restricted_eps and (ford_alpha_eps or hkg_angle_eps or not native.requestedLongitudinal and not native.longitudinalAllowed))) or
      axis.longitudinalActive and not native.longitudinalAllowed or
      native.observedMonoTime < self.lateral_native_ns or
      (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != self.lateral_inventory):
    return False
  if hkg_angle_eps and fresh_active_successor and axis.desiredLateral and not axis.lateralActive:
    self.hkg_eps_join_until_ns = min(previous[4], self.lateral_source_ns + AXIS_MAX_AGE_NS)
  if stock_volkswagen_eps and matched_withdrawal:
    self.volkswagen_eps_source_ns = int(axis.sourceCarStateMonoTime)
  if subaru_brake and cs.brakePressed:
    self.subaru_eps_brake_event_ns = event_ns
  if honda_eps and cs.brakePressed and (matched_withdrawal or held_original):
    self.honda_eps_brake_event_ns = event_ns
  if honda_eps and matched_withdrawal:
    self.honda_eps_source_ns = int(axis.sourceCarStateMonoTime)
  if cs.steerFaultTemporary and matched_withdrawal:
    self.temporary_eps_event_ns = int(axis.faultEventMonoTime)
    if cp.brand == 'ford':
      # Prior gas: accepted event receipt, not newer other-socket CS release.
      self.temporary_eps_gas_override = any(
        event.name == log.OnroadEvent.EventName.gasPressedOverride for event in events)
  if pending_withdrawal or repeated_join:
    # Exact physical-EPS join: intent only; newer/duplicate proofs cannot reanchor.
    if not pending:
      self.pending_event = max(int(axis.faultEventMonoTime), event_ns)
    self.awaiting = True
  elif pending:
    self.pending_event = 0
  return True


def honda_eps_recovery_intent_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer):
  """Join physical EPS transitions to their still-withdrawn producer proof.

  Only existing intent survives, for at most the original native lease window
  after the observed EPS source. Neither native permission nor output is granted.
  Recovery publications cannot move that deadline or renew healthy baselines.
  """
  from opendbc.car.honda.stock_aol import qualified
  from opendbc.car import structs
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native, stock_lateral_user_event

  source = getattr(self, 'honda_eps_source_ns', 0)
  previous = self.signature
  first_native_eps = bool(cs.steerFaultTemporary and axis.desiredLateral and
    previous is not None and previous[5] == 'none' and previous[9] and previous[11] and previous[13] and
    0 < previous[8] <= previous[2] <= now_ns <= previous[4] and
    now_ns - previous[8] <= SAFETY_MAX_AGE_NS)
  known_eps = bool(source and self.temporary_eps_event_ns)
  if first_native_eps and (not known_eps or self.lateral_source_ns > source):
    source = previous[8]
    known_eps = False
  event_successor = bool(known_eps and previous is not None and
    self.temporary_eps_event_ns <= previous[6] <= axis.faultEventMonoTime and
    0 < axis.faultEventMonoTime <= stamp and now_ns - axis.faultEventMonoTime <= AXIS_MAX_AGE_NS)
  if (cp.brand != 'honda' or not qualified(cp, marked_only=True) or
      not latched or self.failed or self.paused or self.pending_event or self.retention_started_ns or
      not self.lateral_only_baseline or not self.lateral_inventory or not self.lateral_baseline_ns or
      not 0 < source <= now_ns or now_ns - source > SAFETY_MAX_AGE_NS or
      not (known_eps or first_native_eps) or previous is None or
      previous[5] != 'none' or (previous[9] and not first_native_eps) or previous[10] or previous[12] or
      (previous[6] != self.temporary_eps_event_ns and not (first_native_eps or event_successor)) or
      not self.session or str(axis.sessionId) != self.session or
      not producer or producer != self.availability_producer or
      str(axis.intentProducerSessionId) != producer or int(axis.intentIntegrityEpoch) != self.availability_epoch or
      previous[14:16] != (producer, self.availability_epoch) or
      not cs.canValid or cs.canTimeout or (cs.steerFaultTemporary and not axis.desiredLateral) or cs.steerFaultPermanent or
      cs.accFaulted or cs.doorOpen or cs.seatbeltUnlatched or cs.vehicleSensorsInvalid or cs.steeringDisengage or
      (cs.brakePressed and not cs.steerFaultTemporary) or cs.regenBraking or cs.buttonEvents or not cs.cruiseState.available or cs.cruiseState.enabled and not cp.pcmCruise or
      cs.gearShifter not in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport,
                            structs.CarState.GearShifter.low, structs.CarState.GearShifter.brake) or
      str(axis.faultReason) != 'none' or
      str(axis.mode) != ('lateralOnly' if first_native_eps and axis.lateralActive else 'off') or
      not axis.nativeAcknowledged or
      (axis.lateralActive and not first_native_eps) or axis.longitudinalActive or axis.desiredLongitudinal or
      not source <= axis.sourceCarStateMonoTime <= stamp <= now_ns or
      axis.sourceCarStateMonoTime < previous[8] or stamp < previous[2] or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      axis.faultEventMonoTime < previous[6] or
      (axis.faultEventMonoTime != self.temporary_eps_event_ns and not (first_native_eps or event_successor)) or
      not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents']):
    return False
  event_ns = int(sm.logMonoTime['onroadEvents'])
  brake_release = bool(known_eps and not cs.brakePressed and
    event_ns == self.temporary_eps_event_ns == getattr(self, 'honda_eps_brake_event_ns', 0))
  stock_user = stock_lateral_user_event(cp) if not cs.cruiseState.enabled else None
  events = tuple(e for e in sm['onroadEvents'] if not (
    e.name == stock_user and e.userDisable and not e.softDisable and not e.immediateDisable) and not (
    (cs.steerFaultTemporary and cs.brakePressed or brake_release) and
    e.name == log.OnroadEvent.EventName.pedalPressed and e.userDisable and
    not e.softDisable and not e.immediateDisable))
  if (event_ns != axis.faultEventMonoTime or not 0 < event_ns <= stamp or
      now_ns - event_ns > 1_500_000_000 or
      any(e.immediateDisable or e.userDisable or e.softDisable and
          e.name != log.OnroadEvent.EventName.steerTempUnavailable for e in events)):
    return False
  native = _configured_transport_native(sm, cp, self.session, now_ns, allow_restricted_lateral=True)
  retained = bool(native is not None and native.pandaInventory and (native.requestedLateral or known_eps) and
    (not native.lateralAllowed or first_native_eps and axis.lateralActive and native.requestedLateral) and
    not native.requestedLongitudinal and not native.longitudinalAllowed and
    native.observedMonoTime >= self.lateral_native_ns and
    self.lateral_inventory == (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)))
  if retained:
    if first_native_eps and not known_eps:
      self.honda_eps_source_ns = source
    self.temporary_eps_event_ns = event_ns
    if cs.steerFaultTemporary and cs.brakePressed:
      self.honda_eps_brake_event_ns = event_ns
  return retained

