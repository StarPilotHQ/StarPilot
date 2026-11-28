from openpilot.cereal import log
from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS
from openpilot.starpilot.aol.transport_pause_conditions import stock_lateral_off


def temporary_lag_intent_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer, signature, healthy_native, stock_user_event):
  reason = str(axis.faultReason)
  events = sm['onroadEvents']
  faults = [event for event in events if event.immediateDisable or event.softDisable]
  isolated = bool(len(faults) == 1 and faults[0].name == log.OnroadEvent.EventName.selfdrivedLagging and
    faults[0].softDisable and not faults[0].immediateDisable)
  recovering = bool(cp.brand == 'gm' and not faults and reason == 'none' and
    0 < self.lag_withdrawal_ns <= now_ns <= self.lag_withdrawal_ns + SAFETY_MAX_AGE_NS and
    axis.desiredLateral and not axis.lateralActive and axis.nativeAcknowledged and
    not axis.desiredLongitudinal and not axis.longitudinalActive)
  if not recovering and not isolated and (reason != 'critical' or faults):
    return False
  previous = self.signature
  joining = bool(self.pending_event and previous is not None and previous[5] == 'critical' and previous[6] == self.pending_event)
  if (not latched or self.failed or self.paused or self.pending_event and not joining or self.retention_started_ns or
    not self.session or str(axis.sessionId) != self.session or reason not in ('none', 'critical') or
    not producer or producer != self.availability_producer or
    str(getattr(axis, 'intentProducerSessionId', '')) != producer or
    int(getattr(axis, 'intentIntegrityEpoch', 0)) != self.availability_epoch or
    not self.lateral_baseline_ns or not self.lateral_inventory or
    not cs.canValid or cs.canTimeout or cs.steerFaultTemporary or cs.steerFaultPermanent or cs.accFaulted or
    not 0 < axis.sourceCarStateMonoTime <= stamp <= now_ns or
    now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS):
    return False
  event_ns = int(sm.logMonoTime['onroadEvents'])
  if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
      joining and (axis.faultEventMonoTime != self.pending_event or now_ns - self.pending_event > AXIS_MAX_AGE_NS)):
    return False
  withdrawn = bool(reason == 'critical' and not axis.desiredLateral and not axis.desiredLongitudinal and
    not axis.lateralActive and not axis.longitudinalActive)
  matched = bool(withdrawn and isolated and axis.faultEventMonoTime == event_ns <= stamp)
  held_original = bool(reason == 'none' and isolated and signature == previous and
    axis.desiredLateral and axis.lateralActive and axis.nativeAcknowledged and
    0 < axis.faultEventMonoTime < event_ns and now_ns - event_ns <= AXIS_MAX_AGE_NS)
  known_lag = bool(isolated and previous is not None and previous[5] == 'critical' and previous[6] == event_ns)
  pending = bool(withdrawn and event_ns < axis.faultEventMonoTime <= stamp and
    now_ns - axis.faultEventMonoTime <= AXIS_MAX_AGE_NS and
    (joining or known_lag or not faults and previous is not None and previous[5] == 'none' and
     0 <= now_ns - self.lateral_baseline_ns <= AXIS_MAX_AGE_NS))
  if not (matched or held_original or pending or recovering):
    return False
  from openpilot.starpilot.aol.vehicle import policy_for
  if not policy_for(cp).intent_supported:
    return False
  native = healthy_native(sm, cp, cs, self.session, now_ns)
  if native is None and cp.brand == 'ford':
    from openpilot.starpilot.car.ford.gas_override import healthy_gas_transport_native
    native = healthy_gas_transport_native(sm, cp, cs, self.session, now_ns)
  if (native is None or not native.pandaInventory or
      held_original and not (native.requestedLateral and native.lateralAllowed) or
      native.observedMonoTime < self.lateral_native_ns or
      (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != self.lateral_inventory):
    return False
  users = [event for event in events if event.userDisable]
  if users:
    from opendbc.car.gm.values import is_volt_sdgm_accepted_envelope, is_bolt_pedal_profile
    from openpilot.starpilot.nostalgia import physical_cancel
    gm_pedal = bool(self.lateral_only_baseline and (is_volt_sdgm_accepted_envelope(cp) or is_bolt_pedal_profile(cp)) and
      (cs.brakePressed or cs.regenBraking) and cs.cruiseState.available and
      not cs.doorOpen and not cs.seatbeltUnlatched and not cs.steeringDisengage and not physical_cancel(cs, cp) and
      not axis.desiredLongitudinal and not axis.longitudinalActive and
      not native.requestedLongitudinal and not native.longitudinalAllowed and
      all(event.name == log.OnroadEvent.EventName.pedalPressed and event.noEntry and not event.softDisable and not event.immediateDisable for event in users))
    stock_user = stock_user_event(cp)
    gm_stock_withdrawal = bool(cp.brand == 'gm' and cp.pcmCruise and self.lateral_only_baseline and
      (withdrawn or recovering) and cs.cruiseState.available and not cs.cruiseState.enabled and
      not cs.brakePressed and not cs.regenBraking and not cs.gasPressed and
      not cs.doorOpen and not cs.seatbeltUnlatched and not cs.steeringDisengage and
      not physical_cancel(cs, cp) and not native.requestedLateral and not native.lateralAllowed and
      not native.requestedLongitudinal and not native.longitudinalAllowed and
      all(event.name == stock_user and not event.softDisable and not event.immediateDisable for event in users))
    if not gm_pedal and not gm_stock_withdrawal and (not getattr(self, 'lateral_only_baseline', False) or stock_user is None or
        any(event.name != stock_user or event.softDisable or event.immediateDisable for event in users) or
        not stock_lateral_off(cp, cs, native)):
      return False
  if pending:
    if not self.pending_event:
      self.pending_event = int(axis.faultEventMonoTime)
    self.awaiting = True
  elif joining:
    self.pending_event = 0
  if matched and cp.brand == 'gm':
    self.lag_withdrawal_ns = stamp
  return True
