"""Temporary EPS intent retention; native permission and original deadlines remain required."""
from openpilot.cereal import log
from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS


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
  held_original = bool(axis.desiredLateral and (axis.lateralActive or
    cp.brand == 'hyundai' and str(cp.steerControlType) == 'angle' and fresh_active_successor) and axis.nativeAcknowledged and
    (signature == previous or fresh_active_successor))
  if not withdrawn and not held_original:
    return False
  stock_user = stock_lateral_user_event(cp)
  stock_eps_user = bool(self.lateral_only_baseline and cp.pcmCruise and
    not axis.desiredLongitudinal and not axis.longitudinalActive and not cs.steeringDisengage and
    cs.cruiseState.available and not cs.cruiseState.enabled and stock_user is not None and
    any(event.name == stock_user and event.userDisable for event in sm['onroadEvents']))
  # Seatbelt blocks normal LONG, not admitted LAT intent. Keep warnings/native/output checks.
  events = tuple(event for event in sm['onroadEvents'] if not (
    stock_eps_user and event.name == stock_user and event.userDisable and
    not event.softDisable and not event.immediateDisable) and not (
    self.lateral_only_baseline and cs.seatbeltUnlatched and not axis.desiredLongitudinal and
    not axis.longitudinalActive and event.name == log.OnroadEvent.EventName.seatbeltNotLatched and
    event.softDisable and event.noEntry and not event.immediateDisable and not event.userDisable))
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
  original_active = bool(previous is not None and previous[0] == self.session and
    previous[7] == self.session and previous[5] == 'none' and
    previous[9] and previous[11] and previous[13] and previous[6] == event_ns and
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
  from opendbc.car import structs
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
    not axis.desiredLongitudinal and not axis.longitudinalActive) or ford_alpha_eps or hkg_angle_eps
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
  if stock_volkswagen_eps and matched_withdrawal:
    self.volkswagen_eps_source_ns = int(axis.sourceCarStateMonoTime)
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

