"""Classify ordinary host source loss without granting commands or engagement."""
from math import isfinite
from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.events import ET
from openpilot.starpilot.aol.intent_companion import MAX_COMPANIONS
from openpilot.starpilot.aol.runtime import INTENT_MAX_AGE_NS, SAFETY_MAX_AGE_NS, current_native
from openpilot.starpilot.aol.transport_pause import (cp_identity, healthy_transport, HOST_SOURCE_AVAILABILITY_EVENTS,
                                                   host_source_availability_only, stock_lateral_off, stock_lateral_user_event)
from openpilot.starpilot.nostalgia import physical_cancel

EventName = log.OnroadEvent.EventName
AlertLevel = log.DriverMonitoringState.AlertLevel


def stock_lateral_role(self, CS, native, intent=None, *, allow_independent_cancel=False, allow_independent_brake=False, allow_independent_gas=False, now_ns=0):
  """Current stock USER role while an established independent LAT owner remains."""
  cancel_role = False
  if allow_independent_cancel:
    from opendbc.car.hyundai.canfd_angle_aol import qualified as angle_qualified
    from openpilot.starpilot.car.hyundai.aol import physical_cancel_lateral_allowed

    cancel_role = bool(angle_qualified(self.CP, marked_only=True) and
      physical_cancel_lateral_allowed(self.CP, CS, intent, native, settings=self.aol_settings,
        previous=getattr(self, 'aol_axis_decision', None), source_ns=getattr(self, 'aol_car_state_log_ns', 0),
        now_ns=now_ns, axis_session_id=self.aol_session_id))
  brake_role = False
  if allow_independent_brake:
    from opendbc.car.hyundai.canfd_angle_aol import command_allowed
    from opendbc.car.hyundai.canfd_stock_aol import qualified as stock_canfd_qualified
    from openpilot.starpilot.car.toyota.aol import qualified as toyota_qualified, held_lateral_pedal

    settings = getattr(self, 'aol_settings', None)
    previous = getattr(self, 'aol_axis_decision', None)
    brake_role = held_lateral_pedal(self, CS) or bool(settings is not None and settings.enabled and CS.brakePressed and not CS.regenBraking and
      previous is not None and previous.lateral_active and previous.native_acknowledged and not previous.longitudinal_active and
      (toyota_qualified(self.CP, marked_only=True) and CS.standstill and not CS.cruiseState.enabled and
       CS.gearShifter == car.CarState.GearShifter.drive and isfinite(CS.vEgoRaw) and isfinite(CS.vEgo) or
       CS.vEgo >= settings.pause_brake_mps and command_allowed(self.CP, CS) or
       len(self.CP.safetyConfigs) == 1 and stock_canfd_qualified(self.CP, marked_only=True) and
       CS.gearShifter == car.CarState.GearShifter.drive and isfinite(CS.vEgoRaw) and isfinite(CS.vEgo) and
       (settings.pause_brake_mps <= 0 or CS.vEgo >= settings.pause_brake_mps or CS.standstill)))
  gas_role = False
  if allow_independent_gas:
    from opendbc.car.hyundai.canfd_stock_aol import qualified as stock_canfd_qualified

    settings = getattr(self, 'aol_settings', None)
    previous = getattr(self, 'aol_axis_decision', None)
    gas_role = bool(self.CP.brand == 'hyundai' and len(self.CP.safetyConfigs) == 1 and
      settings is not None and settings.enabled and CS.gasPressed and not CS.brakePressed and not CS.regenBraking and
      previous is not None and previous.lateral_active and previous.native_acknowledged and not previous.longitudinal_active and
      CS.gearShifter == car.CarState.GearShifter.drive and isfinite(CS.vEgoRaw) and isfinite(CS.vEgo) and
      stock_canfd_qualified(self.CP, marked_only=True))
  rules = self.events.event_rules
  stock_user = stock_lateral_user_event(self.CP)
  users = [name for name in self.events.names if ET.USER_DISABLE in rules.get(name, {})]
  if stock_user is None or not users or any((name != stock_user and not (cancel_role and name == EventName.buttonCancel) and
      not (brake_role and name == EventName.pedalPressed)) or
      ET.SOFT_DISABLE in rules.get(name, {}) or ET.IMMEDIATE_DISABLE in rules.get(name, {}) for name in users):
    return False
  anchor = getattr(self, 'aol_ordinary_source_anchor', None)
  if (anchor is None or not anchor[0][8] or anchor[0][7] or anchor[0][9] or self.enabled or self.active or
      self.state_machine.state != log.SelfdriveState.OpenpilotState.disabled or
      anchor[0][0] != self.aol_session_id or cp_identity(self.CP) != anchor[3] or
      getattr(self, 'aol_dm_lateral_inhibit', False) or
      not self.sm.all_checks(['modelV2', 'extrinsicsCalibration', 'driverMonitoringState']) or
      self.sm['extrinsicsCalibration'].calStatus != log.ExtrinsicsCalibration.Status.calibrated or
      self.sm['driverMonitoringState'].alertLevel == AlertLevel.three or
      self.sm['driverMonitoringState'].lockout or self.sm['driverMonitoringState'].alwaysOnLockout or
      not stock_lateral_off(self.CP, CS, native, allow_independent_cancel=cancel_role,
                            allow_independent_brake=brake_role,
                            allow_independent_gas=gas_role or brake_role and self.CP.brand == 'toyota' and CS.gasPressed)):
    return False
  accepted = intent if intent is not None else anchor[2]
  companion = getattr(self, 'aol_intent_companion', None)
  return bool(accepted is not None and accepted.settingsQualified and accepted.allowedLatch and accepted.lateralArmed and
    not accepted.pauseLateral and not accepted.pauseLongitudinal and
    accepted.producerSessionId == anchor[0][4] and companion is not None and companion.last_drain_ok and
    companion.integrity_epoch == anchor[0][5])


def stock_lateral_fault_proof(self, CS, message, now_ns):
  """Capture the stock role of this successfully published inactive fault."""
  published = self.aol_published_fault_proof
  intent = getattr(self, 'aol_last_intent', None)
  native = getattr(self, 'aol_decision_native', None)
  anchor = getattr(self, 'aol_ordinary_source_anchor', None)
  axis = message.aolAxisState
  if (published is None or anchor is None or anchor[1] is None or not message.valid or not axis.qualified or published[1] != 'critical' or
      any(published[4:]) or not stock_lateral_role(self, CS, native, intent) or
      self.aol_authority_lost or getattr(self, 'aol_availability_long_inhibit', False) or
      getattr(self, 'car_state_queue_invalid', False) or getattr(self, 'aol_selected_pair_future', False) or
      not getattr(self, 'aol_step_intent_drain_ok', True) or
      getattr(self, 'aol_step_intent_drain_count', 0) > MAX_COMPANIONS or
      not getattr(self, 'conditional_car_state_valid', False) or intent != anchor[2] or CS is not anchor[4] or
      axis.sourceCarStateMonoTime != intent.carStateLogMonoTime or
      not 0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime or
      now_ns - axis.sourceCarStateMonoTime > INTENT_MAX_AGE_NS or
      native is not getattr(self.aol_transport_pause, 'observed_native', None) or
      not 0 < native.observedMonoTime <= now_ns <= native.validUntilMonoTime or
      native.observedMonoTime < anchor[1].observedMonoTime or native.pandaSerial != anchor[1].pandaSerial or
      tuple(p.hardwareSerial for p in native.pandaInventory) != tuple(p.hardwareSerial for p in anchor[1].pandaInventory) or
      any(self.events.contains(kind) for kind in (ET.ENABLE, ET.OVERRIDE_LATERAL, ET.OVERRIDE_LONGITUDINAL))):
    return None
  events = self.events.to_msg()
  stock_user = stock_lateral_user_event(self.CP)
  host = [e for e in events if not (e.name == stock_user and e.userDisable and
    not e.softDisable and not e.immediateDisable)]
  if not host_source_availability_only(host) or not any(e.name == EventName.selfdrivedLagging for e in host):
    return None
  samples = getattr(self, 'car_state_samples', ()) or ((CS, True),)
  if any(not valid or not sample.canValid or sample.canTimeout or sample.steerFaultPermanent or
      sample.steerFaultTemporary or sample.accFaulted or sample.doorOpen or sample.seatbeltUnlatched or
      sample.brakePressed or sample.regenBraking or sample.gasPressed or physical_cancel(sample, self.CP) or
      sample.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.sport,
        car.CarState.GearShifter.low, car.CarState.GearShifter.brake) for sample, valid in samples):
    return None
  return (published, stock_user)


def ordinary_source_availability(self, CS, now_ns, native=None, *, replay: bool, native_provider=current_native):
  """Classify lost host source; never renew a command or change engagement."""
  anchor = getattr(self, 'aol_ordinary_source_anchor', None)
  companion = getattr(self, 'aol_intent_companion', None)
  events = self.events.to_msg()
  from openpilot.starpilot.car.gm.aol import held_sdgm_lateral_pedal
  from openpilot.starpilot.car.toyota.aol import held_lateral_pedal
  toyota_pedal = held_lateral_pedal(self, CS)
  held_pedal = held_sdgm_lateral_pedal(self, CS) or toyota_pedal
  if held_pedal:
    events = [event for event in events if not (event.name == EventName.pedalPressed and
      event.userDisable and not event.softDisable and not event.immediateDisable)]
  stock_user = None
  stock_gas = False
  if any(event.name in (EventName.pcmDisable, EventName.cruiseDisabled) and event.userDisable for event in events):
    # Resolve the native value once, as this classifier already does below.
    # Normal healthy frames never enter this nominal stock-role branch.
    native = native or native_provider(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_session_id)
    if stock_lateral_role(self, CS, native, allow_independent_brake=held_pedal, allow_independent_gas=True):
      stock_gas = bool(CS.gasPressed and anchor is not None and anchor[4].gasPressed)
      stock_user = stock_lateral_user_event(self.CP)
      events = [event for event in events if not (event.name == stock_user and event.userDisable and
        not event.softDisable and not event.immediateDisable)]
  if (replay or not self.aol_replay or not self.axis_transport_required or anchor is None or
      not self.initialized or getattr(self, 'aol_availability_long_inhibit', False) or self.aol_authority_lost or
      getattr(self, 'aol_fault_reason', 'none') not in ('none', 'availabilityPause', 'critical') or
      any(event.userDisable or event.immediateDisable for event in events) or
      any(e.softDisable and e.name not in HOST_SOURCE_AVAILABILITY_EVENTS
          for e in events) or self.events.contains(ET.ENABLE) or
      getattr(self, 'car_state_queue_invalid', False) or
      not getattr(self, 'aol_step_intent_drain_ok', True) or
      getattr(self, 'aol_step_intent_drain_count', 0) > MAX_COMPANIONS or companion is None or not companion.last_drain_ok or
      getattr(self, 'aol_selected_pair_future', False) or
      not self.sm.all_checks(['modelV2', 'extrinsicsCalibration', 'driverMonitoringState']) or
      self.sm['extrinsicsCalibration'].calStatus != log.ExtrinsicsCalibration.Status.calibrated or
      self.sm['driverMonitoringState'].alertLevel == AlertLevel.three or
      self.sm['driverMonitoringState'].lockout or self.sm['driverMonitoringState'].alwaysOnLockout):
    return False
  proof, prior_native, accepted, identity, accepted_state = anchor
  if accepted is None:
    return False
  # A source admitted by normal runtime in THIS step can expire only while
  # publishing its first host-SOFT fault. It has no prior critical publication.
  # Require the exact admitted object/state and original completed baseline;
  # stale historical inputs cannot enter this path or grant a new command.
  admitted_expiry = bool(getattr(self, 'aol_published_fault_reason', '') == 'none' and
    getattr(self, 'aol_step_input_proof', None) == proof and
    getattr(self, 'aol_last_intent', None) is accepted and
    getattr(self, 'conditional_car_state_valid', False) and CS is accepted_state and
    self.aol_car_state_log_ns == accepted.carStateLogMonoTime and
    host_source_availability_only(events))
  if getattr(self, 'aol_fault_reason', 'none') == 'critical' and not admitted_expiry:
    # A prior OFF/critical proof may describe ordinary SOFT loop lag rather
    # than an authority fault. Prove that exact published episode; never infer
    # it from a current healthy native snapshot or clear authority_lost here.
    published = getattr(self, 'aol_published_fault_proof', None)
    if (published is None or len(published) != 9 or published[0] != self.aol_session_id or
        published[1] != 'critical' or published[2] != getattr(self, 'aol_fault_event_ns', 0) or
        published[3] != tuple(getattr(self, 'events_prev', ()))):
      return False
    prior_names = published[3]
    stock_record = getattr(self, 'aol_published_stock_lateral_proof', None)
    if (stock_user is not None and stock_record is not None and len(stock_record) == 2 and
        stock_record[0] == published and stock_record[1] == stock_user):
      # This USER was qualified at the actual completed prior publication,
      # not inferred from today's healthy physical/native snapshot.
      prior_names = tuple(name for name in prior_names if name != stock_user)
    rules = self.events.event_rules
    prior_faults = [name for name in prior_names if any(kind in rules.get(name, {})
                    for kind in (ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE, ET.USER_DISABLE))]
    if (getattr(self, 'aol_published_fault_reason', '') != 'critical' or
        not getattr(self, 'aol_fault_event_ns', 0) or
        EventName.selfdrivedLagging not in prior_faults or
        not all(name in HOST_SOURCE_AVAILABILITY_EVENTS and
          not any(kind in rules.get(name, {}) for kind in (ET.IMMEDIATE_DISABLE, ET.USER_DISABLE))
          for name in prior_faults) or not host_source_availability_only(events) or
        any(published[4:])):
      return False
  source = self.aol_car_state_log_ns or getattr(self, 'car_state_retained_source_ns', 0)
  held_metadata = bool(not getattr(self, 'car_state_samples', ()) and CS is accepted_state and
                       getattr(self, 'aol_publication_retained_intent', None) is accepted)
  if not source and held_metadata:
    source = int(accepted.carStateLogMonoTime)
  if (proof[0] != self.aol_session_id or not (proof[8] or proof[9]) or accepted is None or source != int(accepted.carStateLogMonoTime) or
      not 0 < source <= now_ns or now_ns - source <= INTENT_MAX_AGE_NS or
      cp_identity(self.CP) != identity or companion.integrity_epoch != proof[5] or
      accepted is None or accepted.producerSessionId != proof[4] or
      prior_native is None or not prior_native.pandaInventory):
    return False
  samples = getattr(self, 'car_state_samples', ()) or ((CS,
    bool(getattr(self, 'car_state_retained_valid', False) or held_metadata)),)
  if any(not valid or not sample.canValid or sample.canTimeout or sample.steerFaultPermanent or
         sample.steerFaultTemporary or sample.accFaulted or sample.doorOpen or sample.seatbeltUnlatched or
         (sample.brakePressed or sample.regenBraking) and not held_pedal or
         sample.gasPressed and not (toyota_pedal or stock_gas) or
         stock_gas and sample.gasPressed != accepted_state.gasPressed or physical_cancel(sample, self.CP) or
         sample.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.sport,
                                   car.CarState.GearShifter.low, car.CarState.GearShifter.brake)
         for sample, valid in samples):
    return False
  def valid_metadata(stamp, value):
    if (value is None or value.carStateLogMonoTime != stamp or
        value.producerSessionId != proof[4] or not value.settingsQualified or
        value.allowedLatch != accepted.allowedLatch or value.lateralArmed != accepted.lateralArmed or
        value.pauseLongitudinal or value.pauseLateral and (proof[8] or not accepted.pauseLateral)):
      return False
    # Only the exact originally accepted tuple is identity after expiry.
    # Every newer raw/SM tuple keeps its own original shape and 200ms lease;
    # none supplies an active command or a renewed source/output deadline.
    return bool(stamp == accepted.carStateLogMonoTime and value == accepted or
      0 < stamp <= value.observedMonoTime <= now_ns <= value.validUntilMonoTime and
      0 < value.validUntilMonoTime - value.observedMonoTime <= SAFETY_MAX_AGE_NS)

  # The raw-only cache retains every distinct tuple in a <=16-message step.
  # Its exact validated receipts remain metadata when current() legitimately
  # prunes the separate 30ms source-pair cache. Check every original restriction
  # directly, including one no longer present in the conflated SM/sample cache.
  accounted = 0
  for stamp, (value, epoch, producer) in companion.raw_projections.items():
    if stamp < accepted.carStateLogMonoTime:
      continue
    if epoch != proof[5] or producer != proof[4] or not valid_metadata(stamp, value):
      return False
    accounted += 1
  if accounted < getattr(self, 'aol_step_intent_drain_count', 0):
    return False
  # A newer conflated SM sample may add a restriction but cannot remove any
  # already checked raw restriction. Invalid/reordered bursts also retain the
  # existing integrity-epoch and cumulative-drain vetoes above.
  for stamp, valid, value in companion.samples.values():
    if stamp > accepted.carStateLogMonoTime and (not valid or not valid_metadata(stamp, value)):
      return False
  latest = companion.latest
  raw = latest[2] if latest is not None and latest[1] else None
  if (latest is None or not accepted.carStateLogMonoTime <= latest[0] <= now_ns or
      not valid_metadata(latest[0], raw)):
    return False
  native = native or native_provider(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_session_id)
  lateral_intent_only = bool(proof[8] and not proof[9] and not self.enabled)
  if (native is None or held_pedal and (native.requestedLongitudinal or native.longitudinalAllowed) or
      lateral_intent_only and not (accepted.allowedLatch and accepted.lateralArmed and
        not accepted.pauseLateral and not getattr(self, 'aol_dm_lateral_inhibit', False) and
        native.requestedLateral and native.lateralAllowed) or
      not healthy_transport(self.sm, self.CP, CS, self.aol_session_id, now_ns, native) or
      native.observedMonoTime < prior_native.observedMonoTime or
      native.pandaSerial != prior_native.pandaSerial or
      tuple(p.hardwareSerial for p in native.pandaInventory) != tuple(p.hardwareSerial for p in prior_native.pandaInventory)):
    return False
  self.aol_ordinary_source_native = native
  return True


def fresh_ordinary_lag_continuation(self, CS, intent, native, now_ns):
  """Continue established standard axes; native/inputs still own every grant."""
  if (EventName.selfdrivedLagging not in self.events.names or not self.enabled or not self.active or
      self.state_machine.state != log.SelfdriveState.OpenpilotState.softDisabling or
      not self.initialized or not self.aol_replay or not self.axis_transport_required or
      self.aol_authority_lost or getattr(self, 'aol_availability_long_inhibit', False)):
    return False
  rules = self.events.event_rules
  faults = [name for name in self.events.names if any(kind in rules.get(name, {})
            for kind in (ET.USER_DISABLE, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE))]
  lag_rules = rules.get(EventName.selfdrivedLagging, {})
  if (faults != [EventName.selfdrivedLagging] or ET.SOFT_DISABLE not in lag_rules or
      ET.IMMEDIATE_DISABLE in lag_rules or ET.USER_DISABLE in lag_rules or self.events.contains(ET.ENABLE) or
      self.events.contains(ET.OVERRIDE_LATERAL) or self.events.contains(ET.OVERRIDE_LONGITUDINAL) or
      any(name != EventName.selfdrivedLagging and ET.NO_ENTRY in self.events.event_rules.get(name, {})
          for name in self.events.names)):
    return False
  anchor = getattr(self, 'aol_ordinary_source_anchor', None)
  companion = getattr(self, 'aol_intent_companion', None)
  if (anchor is None or intent is None or companion is None or not (anchor[0][8] or anchor[0][9]) or
      anchor[0][0] != self.aol_session_id or companion.integrity_epoch != anchor[0][5] or
      intent != anchor[2] or CS is not anchor[4] or intent.producerSessionId != anchor[0][4] or
      cp_identity(self.CP) != anchor[3] or not getattr(self, 'conditional_car_state_valid', False) or
      not 0 < self.aol_car_state_log_ns == intent.carStateLogMonoTime <= now_ns or
      now_ns - self.aol_car_state_log_ns > INTENT_MAX_AGE_NS or
      getattr(self, 'car_state_queue_invalid', False) or getattr(self, 'aol_selected_pair_future', False) or
      not getattr(self, 'aol_step_intent_drain_ok', True) or not companion.last_drain_ok or
      getattr(self, 'aol_step_intent_drain_count', 0) > MAX_COMPANIONS or
      not self.sm.all_checks(['modelV2', 'extrinsicsCalibration', 'driverMonitoringState']) or
      self.sm['extrinsicsCalibration'].calStatus != log.ExtrinsicsCalibration.Status.calibrated or
      self.sm['driverMonitoringState'].alertLevel == AlertLevel.three or
      self.sm['driverMonitoringState'].lockout or self.sm['driverMonitoringState'].alwaysOnLockout or
      native is None or native is not getattr(self.aol_transport_pause, 'observed_native', None) or
      native.observedMonoTime < anchor[1].observedMonoTime or native.pandaSerial != anchor[1].pandaSerial or
      tuple(p.hardwareSerial for p in native.pandaInventory) != tuple(p.hardwareSerial for p in anchor[1].pandaInventory)):
    return False
  if intent.pauseLongitudinal or intent.pauseLateral and anchor[0][8]:
    return False
  for stamp, (value, epoch, producer) in companion.raw_projections.items():
    if stamp < anchor[0][1]:
      continue
    if (epoch != anchor[0][5] or producer != anchor[0][4] or value is None or
        value.producerSessionId != anchor[0][4] or not value.settingsQualified or
        value.allowedLatch != intent.allowedLatch or value.lateralArmed != intent.lateralArmed or
        value.pauseLongitudinal or value.pauseLateral and (anchor[0][8] or not intent.pauseLateral) or
        value.carStateLogMonoTime != stamp or not 0 < stamp <= value.observedMonoTime <= now_ns <= value.validUntilMonoTime or
        not 0 < value.validUntilMonoTime - value.observedMonoTime <= SAFETY_MAX_AGE_NS):
      return False
  samples = getattr(self, 'car_state_samples', ()) or ((CS, True),)
  return not any(not valid or not sample.canValid or sample.canTimeout or sample.steerFaultPermanent or
    sample.steerFaultTemporary or sample.accFaulted or sample.doorOpen or sample.seatbeltUnlatched or
    sample.brakePressed or sample.regenBraking or sample.gasPressed or physical_cancel(sample, self.CP) or
    sample.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.sport,
                             car.CarState.GearShifter.low, car.CarState.GearShifter.brake)
    for sample, valid in samples)
