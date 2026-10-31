"""Reason-qualified intent retention; this module never grants an axis."""
from openpilot.cereal import log
from openpilot.starpilot.aol.panda_faults import blocking_fault_mask, blocking_fault_list
from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS, current_native, native_inventory_matches_cp
from openpilot.starpilot.aol.wire import SafetyState, decode_intent


def cp_identity(cp):
  return (str(cp.brand), str(cp.carFingerprint), int(cp.flags), int(cp.alternativeExperience),
    bool(cp.passive), bool(cp.dashcamOnly), bool(cp.notCar),
    bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
    tuple((int(c.safetyModel.raw), int(c.safetyParam)) for c in cp.safetyConfigs))


def _configured_transport_native(sm, cp, session, now_ns, native=None, *, require_longitudinal=True, allow_restricted_lateral=False):
  try:
    validated_here = native is None
    native = native or current_native(sm, cp, now_ns=now_ns, axis_session_id=session)
    if (not session or native is None or str(native.axisSessionId) != session or
        native.requestedLateral and not native.lateralAllowed and not allow_restricted_lateral or
        require_longitudinal and native.requestedLongitudinal and not native.longitudinalAllowed):
      return None
    coherent = bool(native.pandaInventory)
    if coherent and (int(sm.logMonoTime['aolSafetyWire']) != native.observedMonoTime or
      not 0 < native.observedMonoTime <= now_ns <= native.validUntilMonoTime or
      now_ns - native.observedMonoTime > SAFETY_MAX_AGE_NS or
      native.validUntilMonoTime - native.observedMonoTime > SAFETY_MAX_AGE_NS):
      return None
    # Inventory checked; validate supplied-native structure.
    if coherent and (not validated_here and not native_inventory_matches_cp(native, cp) or
      any(p.safetyRxChecksInvalid or p.heartbeatLost or blocking_fault_mask(p.faults, cp) or
          (require_longitudinal and native.requestedLongitudinal and str(config.safetyModel) not in ('silent', 'noOutput') and
           not p.controlsAllowed)
          for p, config in zip(native.pandaInventory, cp.safetyConfigs, strict=True))):
      return None
    # v2: exact producer-read health, monotonic 200ms lease; separate conflated BOOTTIME PandaStates: latest veto.
    stamp = int(sm.logMonoTime['pandaStates'])
    if not (sm.seen['pandaStates'] and sm.valid['pandaStates'] and sm.alive['pandaStates'] and
            stamp > 0 and (coherent or stamp <= now_ns and now_ns - stamp <= SAFETY_MAX_AGE_NS)):
      return None
    pandas = sm['pandaStates']
    if not cp.safetyConfigs or len(pandas) != len(cp.safetyConfigs):
      return None
    return native if all(int(p.safetyModel.raw) == int(c.safetyModel.raw) and p.safetyParam == c.safetyParam and
      p.alternativeExperience == cp.alternativeExperience and not p.safetyRxChecksInvalid and
      not p.heartbeatLost and not blocking_fault_list(p.faults, cp) and
      (not require_longitudinal or not native.requestedLongitudinal or str(c.safetyModel) in ('silent', 'noOutput') or p.controlsAllowed)
      for p, c in zip(pandas, cp.safetyConfigs, strict=True)) else None
  except (AttributeError, KeyError, IndexError, TypeError, ValueError):
    return None


def _healthy_transport_native(sm, cp, cs, session, now_ns, native=None, *, require_longitudinal=True):
  try:
    if not cs.canValid or cs.canTimeout or cs.steerFaultTemporary or cs.steerFaultPermanent:
      return None
    return _configured_transport_native(sm, cp, session, now_ns, native,
      require_longitudinal=require_longitudinal)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError):
    return None


def _newer_combined_intent_native(sm, cp, cs, axis, now_ns):
  # New FULL may precede prior LAT LONG ACK: intent only; output needs current native proof/original axis deadlines.
  if (not axis.nativeAcknowledged or not axis.desiredLateral or not axis.lateralActive or
      not axis.desiredLongitudinal or not cs.cruiseState.enabled or
      cs.brakePressed or cs.gasPressed or cs.accFaulted):
    return None
  native = _healthy_transport_native(sm, cp, cs, str(axis.sessionId), now_ns, require_longitudinal=False)
  if (native is None or not native.pandaInventory or not native.requestedLateral or not native.lateralAllowed or
      not native.requestedLongitudinal or not native.longitudinalAllowed or
      not 0 < int(sm.logMonoTime['pandaStates']) < native.sourcePandaStatesMonoTime or
      any(not slot.controlsAllowed for slot, config in zip(native.pandaInventory, cp.safetyConfigs, strict=True)
          if str(config.safetyModel) not in ('silent', 'noOutput'))):
    return None
  return native


def independent_lateral_health(sm, cp, cs, session, now_ns):
  native = _healthy_transport_native(sm, cp, cs, session, now_ns, require_longitudinal=False)
  return native if native is not None and native.requestedLateral and native.lateralAllowed else None


def healthy_transport(sm, cp, cs, session, now_ns, native=None):
  return _healthy_transport_native(sm, cp, cs, session, now_ns, native) is not None


AVAILABILITY_EVENTS = (log.OnroadEvent.EventName.commIssue, log.OnroadEvent.EventName.commIssueAvgFreq)


def availability_only(events):
  faults = [event for event in events if event.immediateDisable or event.softDisable]
  return bool(faults and all(event.name in AVAILABILITY_EVENTS and event.softDisable and
    not event.immediateDisable for event in faults))


HOST_SOURCE_AVAILABILITY_EVENTS = (*AVAILABILITY_EVENTS, log.OnroadEvent.EventName.selfdrivedLagging)


def host_source_availability_only(events):
  faults = [event for event in events if event.immediateDisable or event.softDisable]
  return bool(faults and not any(event.userDisable for event in events) and
    all(event.name in HOST_SOURCE_AVAILABILITY_EVENTS and event.softDisable and
        not event.immediateDisable for event in faults))


def stock_lateral_user_event(cp):
  if not cp.pcmCruise:
    return None
  if cp.brand == 'honda':
    from opendbc.car.honda.stock_aol import qualified
    return log.OnroadEvent.EventName.cruiseDisabled if qualified(cp, marked_only=True) else None
  return log.OnroadEvent.EventName.pcmDisable


def stock_lateral_off(cp, cs, native, *, allow_independent_cancel=False, allow_independent_brake=False,
  allow_independent_gas=False):
  from openpilot.starpilot.nostalgia import physical_cancel
  from opendbc.car.hyundai.canfd_angle_aol import qualified as angle_qualified

  from opendbc.car.hyundai.canfd_stock_aol import qualified as stock_canfd_qualified

  from openpilot.starpilot.car.toyota.aol import qualified as toyota_qualified

  independent_angle = angle_qualified(cp, marked_only=True)
  independent_brake = independent_angle or (allow_independent_brake and (
    toyota_qualified(cp, marked_only=True) and not cs.cruiseState.enabled or
    cp.brand == 'hyundai' and len(cp.safetyConfigs) == 1 and stock_canfd_qualified(cp, marked_only=True)))
  return bool(cp.pcmCruise and cs.cruiseState.available and
    (not cs.cruiseState.enabled or independent_brake and allow_independent_brake and cs.brakePressed) and
    cs.canValid and not cs.canTimeout and not cs.steerFaultPermanent and not cs.steerFaultTemporary and
    not cs.accFaulted and not cs.doorOpen and not cs.seatbeltUnlatched and
    (not cs.brakePressed or independent_brake and allow_independent_brake) and
    not cs.regenBraking and (not cs.gasPressed or independent_angle or
      allow_independent_gas and (toyota_qualified(cp, marked_only=True) or
        cp.brand == 'hyundai' and stock_canfd_qualified(cp, marked_only=True))) and
    (not physical_cancel(cs, cp) or independent_angle and allow_independent_cancel) and
    native is not None and native.requestedLateral and native.lateralAllowed and not native.requestedLongitudinal)


class AvailabilityPause:
  """Retain an accepted armed producer; this proof never enables output."""
  def __init__(self, cp, session):
    self.cp, self.session = cp_identity(cp), session
    self.producer = ''
    self.epoch = None
    self.inventory = None
    self.native_ns = 0
    self.baseline_ns = 0
    self.active = False

  def observe(self, *, cp, session, sm, cs, native, now_ns, companion, intent,
              events, model_ready, source_current, prior_lateral_active, hard_fault, healthy_native, source_expired=False):
    try:
      healthy = healthy_native
      eligible = bool(cp_identity(cp) == self.cp and
        session == self.session and not hard_fault and model_ready and (source_current or source_expired) and companion is not None and
        companion.last_drain_ok and intent is not None and intent.allowedLatch and intent.lateralArmed and
        healthy is not None and healthy.requestedLateral and healthy.lateralAllowed and healthy.pandaInventory)
      if not eligible:
        self.active = False
        self.baseline_ns = 0
        return False
      if source_expired:
        latest = companion.latest
        raw = latest[2] if latest is not None and latest[1] else None
        verified = companion.verified_sm_projection
        sm_intent = decode_intent(sm['aolIntentWire'])
        projection = bool(verified is not None and int(sm.logMonoTime['aolIntentWire']) == verified[0] and
          verified[2] == companion.integrity_epoch and verified[3] == companion.session and
          sm_intent == verified[1] and 0 < verified[0] <= now_ns <= verified[0] + SAFETY_MAX_AGE_NS and
          sm_intent.settingsQualified and sm_intent.allowedLatch and sm_intent.lateralArmed and
          sm_intent.producerSessionId == intent.producerSessionId)
        if (raw is None or not raw.settingsQualified or not raw.allowedLatch or not raw.lateralArmed or
          raw.producerSessionId != intent.producerSessionId or
          not 0 < intent.carStateLogMonoTime <= now_ns or
          now_ns - intent.carStateLogMonoTime > SAFETY_MAX_AGE_NS or
          not 0 < raw.carStateLogMonoTime <= now_ns or raw.observedMonoTime > now_ns or
          now_ns - raw.carStateLogMonoTime > SAFETY_MAX_AGE_NS or
          (int(sm.logMonoTime['aolIntentWire']) != raw.carStateLogMonoTime and not projection) or
          not sm.seen['aolIntentWire'] or not sm.valid['aolIntentWire'] or not sm.alive['aolIntentWire']):
          self.active = False
          return False
      inventory = (healthy.pandaSerial, tuple(p.hardwareSerial for p in healthy.pandaInventory))
      faults = [event for event in events if event.immediateDisable or event.softDisable]
      if not faults:
        recovery_handoff = bool(self.active and source_current and not source_expired and self.baseline_ns > 0 and
          self.producer == intent.producerSessionId and self.epoch == companion.integrity_epoch and
          self.inventory == inventory and healthy.observedMonoTime >= self.native_ns)
        self.active = False
        if prior_lateral_active or recovery_handoff:
          self.producer, self.epoch = intent.producerSessionId, companion.integrity_epoch
          self.inventory, self.native_ns, self.baseline_ns = inventory, healthy.observedMonoTime, now_ns
        return False
      matched = bool(availability_only(events) and self.producer == intent.producerSessionId and
        self.epoch == companion.integrity_epoch and self.inventory == inventory and
        healthy.observedMonoTime >= self.native_ns and self.baseline_ns > 0 and
        (self.active or 0 <= now_ns - self.baseline_ns <=
         (SAFETY_MAX_AGE_NS if source_expired else AXIS_MAX_AGE_NS)))
      self.active = matched
      if matched:
        self.native_ns = healthy.observedMonoTime
      else:
        self.baseline_ns = 0
      return matched
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
      self.active = False
      self.baseline_ns = 0
      return False


class TransportPause:
  def __init__(self, cp, session):
    self.cp = cp_identity(cp)
    self.session = session
    self.active = False
    self.producer = ''
    self.epoch = None

  def observe(self, *, cp, session, sm, cs, native, now_ns, companion, previous_intent,
              intent, expired_healthy_source, previously_active, hard_fault, model_ready, user_disable):
    # Per observation; adjacent producer only.
    self.observed_native = None
    context_current = bool(cp_identity(cp) == self.cp and session == self.session and model_ready and companion is not None)
    if context_current:
      self.observed_native = _healthy_transport_native(sm, cp, cs, session, now_ns, native)
    eligible = bool(context_current and not hard_fault and self.observed_native is not None)
    latest = companion.latest if companion is not None else None
    raw = decode_intent(sm['aolIntentWire']) if sm.seen.get('aolIntentWire', False) else None
    previous = previous_intent
    producer = self.producer if self.active else str(previous.producerSessionId) if previous is not None else ''
    eligible = bool(eligible and latest is not None and latest[1] and raw is not None and
      sm.valid['aolIntentWire'] and sm.alive['aolIntentWire'] and
      raw.settingsQualified and raw.producerSessionId == producer and
      latest[2].producerSessionId == producer and
      int(sm.logMonoTime['aolIntentWire']) == raw.carStateLogMonoTime and
      0 < raw.carStateLogMonoTime <= now_ns and raw.observedMonoTime <= now_ns and
      (self.epoch is None or self.epoch == companion.integrity_epoch))
    if self.active:
      if not eligible:
        self.active = False
        return False, True
      if user_disable or intent is not None and not intent.allowedLatch:
        self.active = False
        self.epoch = companion.integrity_epoch
        return False, False
      return True, False
    if (eligible and expired_healthy_source and previously_active and previous is not None and
        previous.allowedLatch and previous.lateralArmed and raw.allowedLatch and raw.lateralArmed and
        not user_disable):
      self.active = True
      self.producer = producer
      self.epoch = companion.integrity_epoch
      return True, False
    if intent is not None and companion is not None:
      self.epoch = companion.integrity_epoch
    return False, False


class TransportPauseFeedback:
  """Card accepts pause provenance only after a healthy same-session baseline."""
  def __init__(self, cp):
    self.cp = cp_identity(cp)
    self.session = ''
    self.sequence = 0
    self.signature = None
    self.paused = False
    self.qualified = False
    self.pending_event = 0
    self.awaiting = False
    self.failed = False
    self.lateral_baseline_ns = 0
    self.ever_lateral_active = False
    self.lag_withdrawal_ns = 0
    self.cold_main_wait_ns = 0
    self.cold_main_sparse_waiting = False
    self.ccnc_main_off_source_ns = 0
    self.cold_park_waiting = False
    self.lateral_only_baseline = False
    self.temporary_eps_gas_override = False
    self.lateral_source_ns = 0
    self.lateral_inventory = None
    self.lateral_native_ns = 0
    self.longitudinal_cancel_ns = 0
    self.last_cancel_source_ns = 0
    self.honda_brake_ns = 0
    self.gm_withdrawal_ns = 0
    self.retention_qualified = False
    self.retention_pending = False
    self.retention_event_ns = 0
    self.retention_started_ns = 0
    self.retention_verified = False
    self.availability_producer = ''
    self.availability_epoch = None
    self.source_retention_started_ns = 0
    self.ordinary_warning_retained = False
    self.ordinary_warning_qualified = False
    self.ordinary_warning_pending_baseline = None
    self.healthy_feedback_ns = 0
    self.healthy_native_ns = 0
    self.healthy_inventory = None

  def _first_ford_gas_lateral_grant(self, sm, cp, cs, axis, now_ns, *, latched, producer):
    from opendbc.car import structs
    from opendbc.car.ford.values import CAR
    from opendbc.car.ford.aol import qualified as ford_qualified
    first_long = (ford_qualified(cp, marked_only=True) and cp.openpilotLongitudinalControl and
      cp.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1)
    pending_ack = str(axis.mode) == 'off' and not axis.lateralActive
    if (not first_long or not latched or self.failed or self.paused or
      self.pending_event or self.retention_started_ns or self.source_retention_started_ns or
      self.lateral_baseline_ns or not self.session or str(axis.sessionId) != self.session or
      not producer or str(axis.intentProducerSessionId) != producer or int(axis.intentIntegrityEpoch) < 0 or
      self.signature is None or self.signature[14:16] != (producer, int(axis.intentIntegrityEpoch)) or
      str(axis.faultReason) != 'none' or not (pending_ack or str(axis.mode) == 'lateralOnly' and axis.lateralActive) or
      not axis.nativeAcknowledged or not axis.desiredLateral or not axis.desiredLongitudinal or axis.longitudinalActive or
      not 0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      not cs.gasPressed or cs.brakePressed or cs.regenBraking or not cs.cruiseState.available or
      not cs.cruiseState.enabled or not cs.canValid or cs.canTimeout or cs.steerFaultTemporary or
      cs.steerFaultPermanent or cs.accFaulted or cs.vehicleSensorsInvalid or cs.steeringDisengage or
      cs.doorOpen or cs.seatbeltUnlatched or cs.gearShifter not in
        (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport, structs.CarState.GearShifter.low) or
      not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < int(sm.logMonoTime['onroadEvents']) <= now_ns or
      now_ns - int(sm.logMonoTime['onroadEvents']) > 1_500_000_000 or
      any(event.immediateDisable or event.softDisable or event.userDisable for event in sm['onroadEvents'])):
      return None
    native = independent_lateral_health(sm, cp, cs, self.session, now_ns)
    # Pre-ACK newer actual 10Hz LAT grant: intent only, never denied LONG.
    if (native is None or pending_ack and native.observedMonoTime <= axis.observedMonoTime or
        not native.pandaInventory or not native.requestedLongitudinal or native.longitudinalAllowed or
        self.healthy_inventory != (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory))):
      return None
    return native

  def _native_long_withdrawal_lateral_current(self, sm, cp, cs, axis, now_ns, *, latched, producer):
    from openpilot.starpilot.nostalgia import physical_cancel
    from openpilot.starpilot.car.gm.aol import native_accepts_cp
    from openpilot.starpilot.car.hyundai.aol import early_native_long_withdrawal_supported, classic_brake_withdrawal
    gm = cp.brand == 'gm' and native_accepts_cp(cp, int(cp.safetyConfigs[0].safetyModel.raw), int(cp.safetyConfigs[0].safetyParam))
    from openpilot.starpilot.car.hyundai.aol import qualified_ccnc_long_aol as ev9_qualified
    from opendbc.car import structs
    ev9 = cp.brand == 'hyundai' and ev9_qualified(cp, marked_only=True)
    from opendbc.car.ford.aol import qualified as ford_qualified
    from opendbc.car.ford.values import CAR
    # Before 10Hz LONG echo: physical pedal/cancel retains only real independent LAT intent.
    ford_withdrawal = bool(cp.brand == 'ford' and ford_qualified(cp, marked_only=True) and cp.openpilotLongitudinalControl and
      (cs.brakePressed or not cs.cruiseState.enabled or physical_cancel(cs, cp) or
       not axis.desiredLongitudinal and not axis.longitudinalActive) and not (cs.brakePressed and cs.gasPressed) and not cs.regenBraking and
      cs.cruiseState.available and not cs.doorOpen and not cs.seatbeltUnlatched and
      not cs.vehicleSensorsInvalid and cs.gearShifter == structs.CarState.GearShifter.drive)
    # Mach-E angle/curvature share native independent axes: intent-only 10Hz LONG withdrawal/recovery echo join.
    # Fresh LAT grant; all health/fault checks required.
    ford_mache_long = bool(ford_qualified(cp, marked_only=True) and cp.openpilotLongitudinalControl and
      cp.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1)
    early_long = gm or cp.brand == 'hyundai' and early_native_long_withdrawal_supported(cp) or ev9 or ford_withdrawal or ford_mache_long
    from openpilot.starpilot.car.gm.aol import f1_conventional_lateral_owner, gm_physical_pedal_withdrawal
    gm_pedal = gm_physical_pedal_withdrawal(cp, cs) and not f1_conventional_lateral_owner(cp)
    event_ns = int(sm.logMonoTime['onroadEvents'])
    main_off_recovery = bool(0 < event_ns <= self.ccnc_main_off_source_ns <= now_ns and
      now_ns - self.ccnc_main_off_source_ns <= AXIS_MAX_AGE_NS)
    ev9_main_off = bool(ev9 and (not cs.cruiseState.available or main_off_recovery) and
      not cs.brakePressed and not cs.gasPressed and not cs.doorOpen and not cs.seatbeltUnlatched and
      cs.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport,
                        structs.CarState.GearShifter.low, structs.CarState.GearShifter.brake))
    if (not latched or self.failed or self.paused or self.pending_event or self.retention_started_ns or
      not self.session or str(axis.sessionId) != self.session or
      not producer or producer != self.availability_producer or
      str(getattr(axis, 'intentProducerSessionId', '')) != producer or
      int(getattr(axis, 'intentIntegrityEpoch', 0)) != self.availability_epoch or
      not self.lateral_inventory or not self.lateral_baseline_ns or
      str(axis.faultReason) != 'none' or not early_long and str(axis.mode) != 'lateralOnly' or not axis.nativeAcknowledged or
      not axis.desiredLateral or not axis.lateralActive or axis.longitudinalActive and not early_long or
      not 0 < self.lateral_source_ns <= axis.sourceCarStateMonoTime <= axis.observedMonoTime <= now_ns or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      not cs.canValid or cs.canTimeout or cs.steerFaultTemporary or cs.steerFaultPermanent or cs.accFaulted or
      cs.steeringDisengage or physical_cancel(cs, cp) and not ford_withdrawal or
      not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < int(sm.logMonoTime['onroadEvents']) <= now_ns or
      now_ns - int(sm.logMonoTime['onroadEvents']) > 1_500_000_000 or
      any(event.immediateDisable or event.softDisable or
          event.userDisable and not (ev9_main_off and event.name == log.OnroadEvent.EventName.wrongCarMode or
            (gm_pedal or classic_brake_withdrawal(cp, cs)) and event.name == log.OnroadEvent.EventName.pedalPressed or
            ford_withdrawal and event.name in (log.OnroadEvent.EventName.pedalPressed, log.OnroadEvent.EventName.pcmDisable,
              log.OnroadEvent.EventName.buttonCancel))
          for event in sm['onroadEvents'])):
      return None
    native = independent_lateral_health(sm, cp, cs, self.session, now_ns)
    if (native is None or not native.pandaInventory or not native.requestedLongitudinal or native.longitudinalAllowed or
        native.observedMonoTime < self.lateral_native_ns or
        (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)) != self.lateral_inventory):
      return None
    owners = [index for index, config in enumerate(cp.safetyConfigs)
              if str(config.safetyModel) not in ('silent', 'noOutput')]
    if not (len(owners) == 1 and (early_long or not native.pandaInventory[owners[0]].controlsAllowed and
      not sm['pandaStates'][owners[0]].controlsAllowed)):
      return None
    return native

  def _ordinary_warning_intent_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer):
    from openpilot.starpilot.aol.vehicle import policy_for
    from openpilot.starpilot.nostalgia import physical_cancel
    from opendbc.car import structs

    pending_baseline = self.ordinary_warning_pending_baseline
    pending = bool(pending_baseline is not None and self.pending_event)
    if (not latched or self.failed or self.paused or self.pending_event and not pending or self.retention_started_ns or
      not self.session or not self.lateral_baseline_ns or not self.lateral_inventory or
      not (policy := policy_for(cp)).intent_supported or
      policy.cruise_main_required and not cs.cruiseState.available or
      str(axis.sessionId) != self.session or
      not producer or producer != self.availability_producer or axis.intentProducerSessionId != producer or
      axis.intentIntegrityEpoch != self.availability_epoch or
      not cs.canValid or cs.canTimeout or cs.steerFaultPermanent or cs.accFaulted or cs.doorOpen or
      cs.steeringDisengage or
      cs.gearShifter not in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport,
                            structs.CarState.GearShifter.low, structs.CarState.GearShifter.brake) or
      not 0 < self.lateral_source_ns <= axis.sourceCarStateMonoTime <= stamp <= now_ns or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      str(axis.faultReason) not in ('none', 'availabilityPause') or
      not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents']):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    events = sm['onroadEvents']
    if (not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        any(event.immediateDisable for event in events)):
      return False
    warning = any(event.softDisable for event in events)
    recovering = bool(getattr(self, 'ordinary_warning_retained', False) and not warning)
    stock_user = stock_lateral_user_event(cp)
    # Family button/pedal latch clear: no rearm.
    if any(event.userDisable and event.name != stock_user and not (
        event.name == log.OnroadEvent.EventName.pedalPressed and (cs.brakePressed or cs.regenBraking) or
        event.name == log.OnroadEvent.EventName.buttonCancel and (physical_cancel(cs, cp) or
          cp.brand == 'gm' and 0 < self.gm_withdrawal_ns == event_ns <= now_ns and
          now_ns - event_ns <= SAFETY_MAX_AGE_NS)) for event in events):
      return False
    prior = pending_baseline if pending else self.signature
    repeated_join = bool(warning and self.ordinary_warning_retained and prior is not None and
      prior[5] == 'availabilityPause' and not any((prior[9], prior[10], prior[12])) and
      prior[14:16] == (producer, self.availability_epoch) and
      str(axis.faultReason) == 'availabilityPause' and not axis.lateralActive and
      not axis.longitudinalActive and not axis.desiredLongitudinal and
      (axis.desiredLateral, axis.nativeAcknowledged) == (prior[11], prior[13]) and
      0 < axis.faultEventMonoTime <= stamp and
      0 < prior[6] == min(axis.faultEventMonoTime, event_ns) < max(axis.faultEventMonoTime, event_ns) <= now_ns and
      now_ns - max(axis.faultEventMonoTime, event_ns) <= AXIS_MAX_AGE_NS)
    if pending and (not 0 < self.pending_event <= now_ns or
        now_ns - self.pending_event > AXIS_MAX_AGE_NS or
        (max(axis.faultEventMonoTime, event_ns) != self.pending_event if repeated_join else
         axis.faultEventMonoTime != self.pending_event)):
      return False
    # Pre-event SOFT: intent only, fixed original 30ms join; no newer/duplicate withdrawal reanchoring.
    axis_first = bool(str(axis.faultReason) == 'availabilityPause' and
      not (axis.lateralActive or axis.longitudinalActive or axis.desiredLateral or
           axis.desiredLongitudinal or axis.nativeAcknowledged) and
      prior is not None and prior[5] == 'none' and prior[9] and prior[11] and prior[13] and
      prior[14:16] == (producer, self.availability_epoch) and
      0 < prior[8] <= now_ns and (pending or now_ns <= prior[4] and now_ns - prior[8] <= AXIS_MAX_AGE_NS) and
      event_ns < axis.faultEventMonoTime <= stamp and now_ns - axis.faultEventMonoTime <= AXIS_MAX_AGE_NS)
    if not warning and not recovering and not axis_first:
      return False
    active_join = bool(warning and prior is not None and str(axis.faultReason) == 'none' and
      axis.lateralActive and axis.desiredLateral and axis.nativeAcknowledged and
      prior[9] and prior[11] and prior[13] and prior[14:16] == (producer, self.availability_epoch) and
      0 < prior[8] <= now_ns <= prior[4] and now_ns - prior[8] <= AXIS_MAX_AGE_NS and
      0 < axis.faultEventMonoTime < event_ns and now_ns - event_ns <= AXIS_MAX_AGE_NS)
    withdrawn = bool(not axis.lateralActive and not axis.longitudinalActive and
      not axis.desiredLongitudinal and
      (warning and str(axis.faultReason) == 'availabilityPause' and axis.faultEventMonoTime == event_ns <= stamp or
       recovering and str(axis.faultReason) == 'none'))
    if not active_join and not withdrawn and not axis_first and not repeated_join:
      return False
    if pending and not axis_first and not repeated_join and not (withdrawn and warning and event_ns == self.pending_event):
      return False
    native = _configured_transport_native(sm, cp, self.session, now_ns,
      allow_restricted_lateral=withdrawn or axis_first or repeated_join or bool(cs.steerFaultTemporary),
      require_longitudinal=not (withdrawn or axis_first or repeated_join))
    if (native is None or not native.pandaInventory or
        (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)) != self.lateral_inventory or
        native.observedMonoTime < self.lateral_native_ns or
        active_join and not (native.requestedLateral and native.lateralAllowed)):
      return False
    if axis_first or repeated_join:
      if not pending:
        self.pending_event = max(int(axis.faultEventMonoTime), event_ns)
        self.ordinary_warning_pending_baseline = prior
      self.awaiting = True
      return True
    if pending:
      self.pending_event = 0
      self.ordinary_warning_pending_baseline = None
    self.ordinary_warning_retained = True
    return True

  def _temporary_host_intent_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer):
    from openpilot.starpilot.nostalgia import physical_cancel
    if physical_cancel(cs, cp) or cs.steeringDisengage:
      return False
    belt_eps_recovery = bool(not cs.steerFaultTemporary and self.lateral_only_baseline and
      getattr(self, 'temporary_eps_event_ns', 0) and axis.faultEventMonoTime == self.temporary_eps_event_ns and
      not axis.desiredLongitudinal and not axis.longitudinalActive)
    events = tuple(event for event in sm['onroadEvents'] if not (
      belt_eps_recovery and cs.seatbeltUnlatched and event.name == log.OnroadEvent.EventName.seatbeltNotLatched and
      event.softDisable and event.noEntry and not event.immediateDisable and not event.userDisable))
    faults = [event for event in events if event.immediateDisable or event.softDisable]
    names = (log.OnroadEvent.EventName.selfdrivedLagging, log.OnroadEvent.EventName.locationdTemporaryError)
    location_fault = bool(any(event.name == log.OnroadEvent.EventName.locationdTemporaryError for event in faults) and
      all(event.softDisable and not event.immediateDisable and event.name in
          (log.OnroadEvent.EventName.locationdTemporaryError, log.OnroadEvent.EventName.commIssue) for event in faults))
    temporary = location_fault or bool(len(faults) == 1 and faults[0].name in names and
      faults[0].softDisable and not faults[0].immediateDisable)
    eps_recovery = bool(not cs.steerFaultTemporary and getattr(self, 'temporary_eps_event_ns', 0) and
      axis.faultEventMonoTime == self.temporary_eps_event_ns and
      all(not event.immediateDisable and
          (not event.softDisable or event.name == log.OnroadEvent.EventName.steerTempUnavailable) for event in events))
    users = [event for event in events if event.userDisable]
    if not (temporary or eps_recovery or users):
      return False
    from opendbc.car.chrysler.aol_contract import qualified as chrysler_qualified
    from openpilot.starpilot.car.nissan.aol import qualified as nissan_qualified
    from opendbc.car.ford.aol import qualified as ford_qualified
    from opendbc.car.rivian.stock_aol import qualified as rivian_qualified
    from openpilot.starpilot.car.volkswagen.aol import qualified as volkswagen_qualified
    from opendbc.car.mazda.stock_aol import qualified as mazda_qualified
    from opendbc.car.subaru.stock_aol import qualified as subaru_qualified
    from opendbc.car.hyundai.canfd_angle_aol import qualified as angle_qualified
    from opendbc.car.hyundai.canfd_stock_aol import qualified as stock_canfd_qualified
    from opendbc.car.hyundai.classic_scc_aol import qualified as stock_classic_qualified
    from opendbc.car import structs
    gears = structs.CarState.GearShifter

    # PCM-off != AOL toggle: after output withdrawal retain prior intent only for
    # location faults/matched EPS recovery; native/session/source checks remain.
    stock_angle = cp.brand == 'hyundai' and angle_qualified(cp, marked_only=True)
    stock_hkg = cp.brand == 'hyundai' and (stock_angle or stock_canfd_qualified(cp, marked_only=True) or
      stock_classic_qualified(cp, marked_only=True))
    stock_user = stock_lateral_user_event(cp)
    stock_temporary_user = bool((location_fault or eps_recovery) and (stock_hkg or cp.brand == 'chrysler' and chrysler_qualified(cp, marked_only=True) or
      cp.brand == 'ford' and ford_qualified(cp, marked_only=True) or cp.brand == 'nissan' and nissan_qualified(cp, marked_only=True)
      or cp.brand == 'subaru' and subaru_qualified(cp, marked_only=True)
      or cp.brand == 'mazda' and mazda_qualified(cp, marked_only=True)
      or cp.brand == 'rivian' and rivian_qualified(cp, marked_only=True)
      or cp.brand == 'volkswagen' and volkswagen_qualified(cp, marked_only=True)) and users and
      not axis.desiredLongitudinal and not axis.longitudinalActive and
      cs.cruiseState.available and not cs.cruiseState.enabled and
      cs.gearShifter in (gears.drive, gears.low, gears.sport, gears.brake) and
      not cs.doorOpen and (not cs.seatbeltUnlatched or belt_eps_recovery) and not cs.brakePressed and not cs.regenBraking and
      (not cs.gasPressed or stock_angle) and
      all(event.name == stock_user and not event.softDisable and
          not event.immediateDisable for event in users))
    from openpilot.starpilot.car.ford.aol import temporary_eps_pedal_recovery
    stock_temporary_user |= bool(cp.brand == 'ford' and eps_recovery and temporary_eps_pedal_recovery(cp, cs) and users and
      not axis.desiredLongitudinal and not axis.longitudinalActive and
      all(event.name in (stock_user, log.OnroadEvent.EventName.pedalPressed) and
          not event.softDisable and not event.immediateDisable for event in users))
    from openpilot.starpilot.car.gm.aol import f1_conventional_lateral_owner, gm_physical_pedal_withdrawal
    # Receipt-bound GM owns F1 physical withdrawal.
    pedal = bool(not f1_conventional_lateral_owner(cp) and
      (cs.brakePressed or gm_physical_pedal_withdrawal(cp, cs)) and users and all(
      event.name == log.OnroadEvent.EventName.pedalPressed and
      not event.softDisable and not event.immediateDisable for event in users) and not faults)
    if axis.longitudinalActive and not location_fault or not (temporary or eps_recovery or pedal):
      return False
    if (not latched or self.failed or self.paused or self.pending_event or self.retention_started_ns or
      not self.session or str(axis.sessionId) != self.session or
      not producer or producer != self.availability_producer or
      str(getattr(axis, 'intentProducerSessionId', '')) != producer or
      int(getattr(axis, 'intentIntegrityEpoch', 0)) != self.availability_epoch or
      not self.lateral_inventory or not self.lateral_baseline_ns or
      not cs.canValid or cs.canTimeout or cs.steerFaultPermanent or cs.accFaulted or cs.steerFaultTemporary or
      users and not (pedal or stock_temporary_user) or str(axis.faultReason) not in ('none', 'critical') or
      not 0 < self.lateral_source_ns <= axis.sourceCarStateMonoTime <= stamp <= now_ns or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents']):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    if not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000:
      return False
    withdrawn = not axis.desiredLateral and not axis.lateralActive and not axis.desiredLongitudinal and not axis.longitudinalActive
    event_first = bool(temporary and str(axis.faultReason) == 'none' and
      self.signature is not None and self.signature[9] and self.signature[11] and self.signature[13] and
      axis.sequence >= self.sequence and stamp >= self.signature[2] and
      temporary and axis.desiredLateral and axis.lateralActive and axis.nativeAcknowledged and
      0 < axis.faultEventMonoTime < event_ns and now_ns - event_ns <= AXIS_MAX_AGE_NS)
    if not (event_first or temporary and axis.faultEventMonoTime == event_ns and
      (withdrawn or str(axis.faultReason) == 'none' and axis.desiredLateral and
       axis.lateralActive and axis.nativeAcknowledged) or eps_recovery and withdrawn or
      pedal and str(axis.faultReason) == 'none' and axis.desiredLateral and axis.lateralActive and
      not axis.desiredLongitudinal and not axis.longitudinalActive and
      axis.nativeAcknowledged and axis.faultEventMonoTime == event_ns):
      return False
    native = _configured_transport_native(sm, cp, self.session, now_ns, require_longitudinal=not pedal)
    if (native is None or not native.pandaInventory or native.observedMonoTime < self.lateral_native_ns or
        (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != self.lateral_inventory or
        native.requestedLateral and not native.lateralAllowed or
        pedal and not (native.requestedLateral and native.lateralAllowed) or
        axis.lateralActive and not (native.requestedLateral and native.lateralAllowed) or
        axis.longitudinalActive and not (native.requestedLongitudinal and native.longitudinalAllowed)):
      return False
    from openpilot.starpilot.aol.vehicle import policy_for
    if not policy_for(cp).intent_supported:
      return False
    if pedal and not temporary and not eps_recovery and not native.requestedLongitudinal:
      # Healthy LAT-only brake: baseline refresh. Exception: pending native LONG withdrawal.
      return False
    self.temporary_host_qualified = True
    return True

  def _temporary_lag_intent_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer, signature):
    reason = str(axis.faultReason)
    events = sm['onroadEvents']
    faults = [event for event in events if event.immediateDisable or event.softDisable]
    isolated = bool(len(faults) == 1 and faults[0].name == log.OnroadEvent.EventName.selfdrivedLagging and
      faults[0].softDisable and not faults[0].immediateDisable)
    # Healthy skips policy/native/provenance. Pre-event critical proof grants no lag exemption.
    recovering = bool(cp.brand == 'gm' and not faults and reason == 'none' and
      0 < self.lag_withdrawal_ns <= now_ns <= self.lag_withdrawal_ns + SAFETY_MAX_AGE_NS and
      axis.desiredLateral and not axis.lateralActive and axis.nativeAcknowledged and
      not axis.desiredLongitudinal and not axis.longitudinalActive)
    if not recovering and not isolated and (reason != 'critical' or faults):
      return False
    previous = self.signature
    joining = bool(self.pending_event and previous is not None and previous[5] == 'critical' and
                   previous[6] == self.pending_event)
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
        joining and
          (axis.faultEventMonoTime != self.pending_event or now_ns - self.pending_event > AXIS_MAX_AGE_NS)):
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
    native = _healthy_transport_native(sm, cp, cs, self.session, now_ns)
    if (native is None or not native.pandaInventory or
        held_original and not (native.requestedLateral and native.lateralAllowed) or
        native.observedMonoTime < self.lateral_native_ns or
        (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != self.lateral_inventory):
      return False
    users = [event for event in events if event.userDisable]
    if users:
      from opendbc.car.gm.values import is_volt_sdgm_accepted_envelope, is_bolt_pedal_profile
      from openpilot.starpilot.nostalgia import physical_cancel
      # Accepted Volt SDGM/Bolt LAT-only brake/regen: pedalPressed/USER_DISABLE.
      # Source-qualified temporary lag: no axis/lease, only intent.
      gm_pedal = bool(self.lateral_only_baseline and (is_volt_sdgm_accepted_envelope(cp) or is_bolt_pedal_profile(cp)) and
        (cs.brakePressed or cs.regenBraking) and cs.cruiseState.available and
        not cs.doorOpen and not cs.seatbeltUnlatched and not cs.steeringDisengage and not physical_cancel(cs, cp) and
        not axis.desiredLongitudinal and not axis.longitudinalActive and
        not native.requestedLongitudinal and not native.longitudinalAllowed and
        all(event.name == log.OnroadEvent.EventName.pedalPressed and event.noEntry and
            not event.softDisable and not event.immediateDisable for event in users))
      stock_user = stock_lateral_user_event(cp)
      # GM host-lag LAT-off -> PCM-off/USER. Exact fresh OFF: intent only, no permission/output.
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
      # Latest verified inactive proof, not fault age; lag renews no output/native permission/source lease/healthy baseline.
      self.lag_withdrawal_ns = stamp
    return True

  def _ford_gas_release_eps_join_current(self, sm, cp, cs, axis, stamp, now_ns, *, producer, latched):
    from openpilot.starpilot.car.ford.aol import temporary_eps_restriction
    gas_was_pressed = self.temporary_eps_gas_override
    previous = self.signature
    if (cp.brand != 'ford' or cp.openpilotLongitudinalControl or
      not cp.safetyConfigs[-1].safetyParam & 128 or
      not latched or not gas_was_pressed or cs.gasPressed or
      not temporary_eps_restriction(cp, cs) or not self.lateral_only_baseline or
      self.failed or self.paused or self.pending_event or self.retention_started_ns or
      not self.ever_lateral_active or cp_identity(cp) != self.cp or previous is None or
      not self.temporary_eps_event_ns or not self.lateral_inventory or not self.lateral_baseline_ns or
      previous[0] != self.session or previous[7] != self.session or previous[1] != self.sequence or
      previous[5] != 'none' or any(previous[9:13]) or not previous[13] or
      previous[6] != self.temporary_eps_event_ns or
      previous[14] != producer or previous[15] != self.availability_epoch or
      not 0 < previous[8] <= previous[2] <= now_ns <= previous[4] or
      now_ns - previous[8] > AXIS_MAX_AGE_NS):
      return False
    if (not axis.qualified or str(axis.sessionId) != self.session or str(axis.faultSessionId) != self.session or
      str(axis.intentProducerSessionId) != producer or int(axis.intentIntegrityEpoch) != self.availability_epoch or
      int(axis.sequence) < self.sequence or str(axis.faultReason) != 'none' or str(axis.mode) != 'off' or
      not axis.nativeAcknowledged or any((axis.desiredLateral,axis.lateralActive,axis.desiredLongitudinal,axis.longitudinalActive)) or
      not sm.seen['aolAxisState'] or not sm.valid['aolAxisState'] or not sm.alive['aolAxisState'] or
      not previous[8] <= axis.sourceCarStateMonoTime <= stamp == axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      axis.validUntilMonoTime - stamp > AXIS_MAX_AGE_NS):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    events = sm['onroadEvents']
    temporary_names = ('steerTempUnavailable','steerTempUnavailableSilent')
    if (axis.faultEventMonoTime == event_ns or
      min(axis.faultEventMonoTime,event_ns) != self.temporary_eps_event_ns or
      not 0 < max(axis.faultEventMonoTime,event_ns) <= now_ns or
      now_ns-max(axis.faultEventMonoTime,event_ns) > AXIS_MAX_AGE_NS or
      not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      now_ns-event_ns > 1_500_000_000 or
      not any(str(e.name)in temporary_names for e in events) or
      any(e.immediateDisable or e.softDisable and str(e.name)!='steerTempUnavailable' or
          e.userDisable and str(e.name)!='pcmDisable' or
          str(e.name)not in (*temporary_names,'pcmDisable','gasPressedOverride','steerOverride') for e in events)):
      return False
    native = _configured_transport_native(sm,cp,self.session,now_ns,allow_restricted_lateral=True)
    return bool(native is not None and native.pandaInventory and
      not any((native.requestedLateral,native.lateralAllowed,native.requestedLongitudinal,native.longitudinalAllowed)) and
      native.observedMonoTime>=self.lateral_native_ns and
      self.lateral_inventory==(native.pandaSerial,tuple(p.hardwareSerial for p in native.pandaInventory)))

  def _temporary_steering_intent_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer, signature):
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
    held_original = bool(axis.desiredLateral and axis.lateralActive and axis.nativeAcknowledged and
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
    from openpilot.starpilot.car.ford.aol import temporary_eps_restriction as ford_temporary_eps_restriction
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
    restricted_eps = bool((temporary_eps_restriction(cp, cs) or stock_ford_eps or stock_mazda_eps or stock_rivian_eps or
                          stock_volkswagen_eps) and self.lateral_only_baseline and
      not axis.desiredLongitudinal and not axis.longitudinalActive)
    native = _configured_transport_native(sm, cp, self.session, now_ns, allow_restricted_lateral=restricted_eps)
    if (native is None or not native.pandaInventory or
        stock_eps_user and (native.requestedLongitudinal or native.longitudinalAllowed) or
        held_original and not (native.requestedLateral and (native.lateralAllowed or
          restricted_eps and not native.requestedLongitudinal and not native.longitudinalAllowed)) or
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

  def _honda_brake_lateral_current(self, sm, cp, cs, axis, now_ns, *, latched, brake_source_ns):
    from opendbc.car.honda.stock_aol import qualified
    from opendbc.car.structs import car

    source_ns = self.honda_brake_ns or (brake_source_ns if cs.brakePressed else 0)
    if (not qualified(cp, marked_only=True) or not latched or self.failed or self.paused or self.pending_event or
      not self.session or str(axis.sessionId) != self.session or
      not self.healthy_feedback_ns or not 0 <= now_ns - self.healthy_feedback_ns <= AXIS_MAX_AGE_NS or
      not 0 < source_ns <= now_ns or now_ns - source_ns > SAFETY_MAX_AGE_NS or
      (not self.honda_brake_ns and now_ns - source_ns > AXIS_MAX_AGE_NS) or
      cs.accFaulted or cs.seatbeltUnlatched or
      cs.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.sport,
                            car.CarState.GearShifter.low, car.CarState.GearShifter.brake) or
      str(axis.faultReason) != 'none' or not axis.desiredLateral or not axis.lateralActive or
      (not axis.nativeAcknowledged and (axis.desiredLongitudinal or axis.longitudinalActive)) or
      not 0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime or
      axis.observedMonoTime - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS):
      return None
    event_ns = int(sm.logMonoTime['onroadEvents'])
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        any(e.immediateDisable or e.softDisable for e in sm['onroadEvents'])):
      return None
    native = independent_lateral_health(sm, cp, cs, self.session, now_ns)
    if (native is None or not native.requestedLateral or not native.lateralAllowed or
        not native.requestedLongitudinal or native.observedMonoTime < self.healthy_native_ns or
        (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != self.healthy_inventory):
      return None
    self.honda_brake_ns = source_ns
    return native

  def _longitudinal_cancel_current(self, sm, cp, cs, axis, now_ns, *, latched, cancel_source_ns, withdrawal_required=True) -> SafetyState | None:
    from opendbc.car.hyundai.ev6_aol import qualified
    from openpilot.starpilot.car.hyundai.aol import qualified_ccnc_long_aol as ev9_qualified
    from opendbc.car.structs import car

    if (not latched or self.failed or self.paused or self.pending_event or
        not self.session or str(axis.sessionId) != self.session or
        not (qualified(cp, marked_only=True) or ev9_qualified(cp, marked_only=True)) or
        not self.lateral_baseline_ns or not 0 <= now_ns - self.lateral_baseline_ns <= AXIS_MAX_AGE_NS or
        str(axis.faultReason) != 'none' or not axis.nativeAcknowledged or
        not axis.desiredLateral or not axis.lateralActive or cs.accFaulted or
        not cs.canValid or cs.canTimeout or cs.steerFaultTemporary or cs.steerFaultPermanent):
      return None
    cancel = any(e.type == car.CarState.ButtonEvent.Type.cancel and e.pressed for e in cs.buttonEvents)
    fresh_cancel = bool(cancel and self.last_cancel_source_ns < cancel_source_ns <= now_ns and
      now_ns - cancel_source_ns <= AXIS_MAX_AGE_NS)
    if not self.longitudinal_cancel_ns and not fresh_cancel:
      return None
    if not withdrawal_required and (self.longitudinal_cancel_ns or not fresh_cancel):
      return None
    if self.longitudinal_cancel_ns and not 0 <= now_ns - self.longitudinal_cancel_ns <= SAFETY_MAX_AGE_NS:
      return None
    event_ns = int(sm.logMonoTime['onroadEvents'])
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        any(e.immediateDisable or e.softDisable for e in sm['onroadEvents'])):
      return None
    native = current_native(sm, cp, now_ns=now_ns, axis_session_id=self.session)
    if (native is None or not native.pandaInventory or not native.requestedLateral or not native.lateralAllowed or
        not native.requestedLongitudinal or withdrawal_required and native.longitudinalAllowed or
        native.observedMonoTime < self.lateral_native_ns or
        not native_inventory_matches_cp(native, cp) or
        (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != self.lateral_inventory):
      return None
    stamp = int(sm.logMonoTime['pandaStates'])
    pandas = sm['pandaStates']
    if (not sm.seen['pandaStates'] or not sm.valid['pandaStates'] or not sm.alive['pandaStates'] or stamp <= 0 or
        len(pandas) != len(cp.safetyConfigs)):
      return None
    # controlsAllowed: normal/LONG-off; keep latest raw config/fault vetoes + exact producer-read inventory.
    current_pandas = all(int(p.safetyModel.raw) == int(c.safetyModel.raw) and p.safetyParam == c.safetyParam and
      p.alternativeExperience == cp.alternativeExperience and not p.safetyRxChecksInvalid and
      not p.heartbeatLost and not blocking_fault_list(p.faults, cp) and
      (str(c.safetyModel) not in ('silent', 'noOutput') or not p.controlsAllowed)
      for p, c in zip(pandas, cp.safetyConfigs, strict=True))
    return native if current_pandas else None


  def _availability_retention(self, sm, cp, cs, axis, stamp, now_ns, *, latched, current, continuity, intent_producer_session):
    reason = str(axis.faultReason)
    events = sm['onroadEvents']
    if reason not in ('none', 'availabilityPause') or reason == 'none' and not availability_only(events):
      return False
    if not (latched and current and continuity and not self.paused and not self.pending_event and
      cp_identity(cp) == self.cp and intent_producer_session and
      getattr(axis, 'intentProducerSessionId', '') == intent_producer_session == self.availability_producer and
      int(getattr(axis, 'intentIntegrityEpoch', 0)) == self.availability_epoch and
      self.session and
      self.lateral_inventory and axis.desiredLateral and
      (str(axis.faultReason) == 'none' or not axis.desiredLongitudinal) and
      axis.nativeAcknowledged and (
        0 < axis.sourceCarStateMonoTime <= stamp and stamp - axis.sourceCarStateMonoTime <= AXIS_MAX_AGE_NS or
        reason == 'availabilityPause' and axis.sourceCarStateMonoTime == 0 and
        not axis.lateralActive and not axis.longitudinalActive)):
      return False
    native = _healthy_transport_native(sm, cp, cs, str(axis.sessionId), now_ns)
    if (native is None or not native.requestedLateral or not native.lateralAllowed or
        native.observedMonoTime < self.lateral_native_ns or
        (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != self.lateral_inventory):
      return False
    # Qualified stock USER: LONG-off, not LAT cancel; inactive/source-zero needs accepted LAT-only baseline/current real native.
    previous = self.signature
    from openpilot.starpilot.car.toyota.aol import qualified as toyota_qualified
    toyota_pedal = bool(toyota_qualified(cp, marked_only=True) and cs.brakePressed != cs.gasPressed and
      not cs.regenBraking and self.lateral_only_baseline and
      not native.requestedLongitudinal and not native.longitudinalAllowed)
    stock_user = stock_lateral_user_event(cp)
    stock_lateral = bool(reason == 'availabilityPause' and axis.sourceCarStateMonoTime == 0 and
      not axis.lateralActive and not axis.longitudinalActive and not axis.desiredLongitudinal and
      stock_lateral_off(cp, cs, native, allow_independent_brake=toyota_pedal,
                        allow_independent_gas=toyota_pedal and cs.gasPressed) and previous is not None and
      previous[0] == self.session and previous[7] == self.session and
      previous[14] == intent_producer_session and previous[15] == self.availability_epoch and
      (previous[11] and not previous[12] and previous[13] and not previous[10] and
       (previous[9] and previous[5] == 'none' or (self.retention_verified or self.retention_started_ns) and
        previous[5] == 'availabilityPause' and previous[8] == 0 and not previous[9] and
        previous[6] == axis.faultEventMonoTime) or
       # Critical signature: matched temporary lag only; unmatched joins keep pending_event.
       # Else fail/clear LAT-only baseline before signature change.
       self.lateral_only_baseline and not self.failed and not self.pending_event and
       (previous[5] == 'critical' or previous[5] == 'availabilityPause' and self.ordinary_warning_retained) and
       previous[1] == self.sequence and not any(previous[9:14]) and
       0 < previous[6] <= previous[2] <= axis.faultEventMonoTime and
       0 < self.lateral_source_ns <= previous[8] <= previous[2] and
       previous[2] - previous[8] <= AXIS_MAX_AGE_NS and
       0 <= now_ns - previous[8] <= SAFETY_MAX_AGE_NS))
    if stock_lateral:
      users = [event for event in events if event.userDisable]
      if any((event.name != stock_user and not (toyota_pedal and event.name == log.OnroadEvent.EventName.pedalPressed)) or
             event.softDisable or event.immediateDisable for event in users):
        return False
      events = [event for event in events if not ((event.name == stock_user or
        toyota_pedal and event.name == log.OnroadEvent.EventName.pedalPressed) and
        event.userDisable and not event.softDisable and not event.immediateDisable)]
    if cp.brand == 'gm':
      from openpilot.starpilot.car.gm.aol import sdgm_lateral_pedal_source_zero
      if sdgm_lateral_pedal_source_zero(self, cp, cs, axis, native, now_ns,
          producer=intent_producer_session):
        events = [event for event in events if not (event.name == log.OnroadEvent.EventName.pedalPressed and
          event.userDisable and not event.softDisable and not event.immediateDisable)]
    event_ns = int(sm.logMonoTime['onroadEvents'])
    faults = [event for event in events if event.immediateDisable or event.softDisable]
    live_event = bool(sm.valid['onroadEvents'] and sm.alive['onroadEvents'] and
      0 < event_ns <= now_ns and now_ns - event_ns <= 1_500_000_000)
    host_source_off = bool(reason == 'availabilityPause' and axis.sourceCarStateMonoTime == 0 and
      not axis.lateralActive and not axis.longitudinalActive)
    issue = live_event and (availability_only(events) or host_source_off and host_source_availability_only(events))
    if reason not in ('none', 'availabilityPause') or faults and not issue:
      return False
    typed = bool(reason == 'availabilityPause' and not axis.lateralActive and not axis.longitudinalActive and
                 0 < axis.faultEventMonoTime <= stamp)
    source_expired = bool(typed and axis.sourceCarStateMonoTime == 0)
    # Original 200ms source admission only, never output. Verified event/proof + current real native LAT
    # Fixed producer/epoch/config/inventory: fresh OFF/source-zero intent only. Missing/mismatched events
    # keep fixed 30ms join; other deadlines/branches unchanged.
    continuing_intent = bool(source_expired and self.retention_verified and issue)
    if source_expired and not (0 < self.lateral_source_ns <= now_ns and
        (now_ns <= self.lateral_source_ns + SAFETY_MAX_AGE_NS or continuing_intent)):
      return False
    baseline_age_limit = SAFETY_MAX_AGE_NS if source_expired else AXIS_MAX_AGE_NS
    matched = typed and issue and axis.faultEventMonoTime == event_ns
    occurrence = max(int(axis.faultEventMonoTime) if typed else 0, event_ns if issue else 0)
    if not occurrence or occurrence < self.retention_event_ns:
      return False
    if matched:
      if not self.retention_verified and not 0 <= now_ns - self.lateral_baseline_ns <= baseline_age_limit:
        return False
      if self.retention_started_ns and now_ns - self.retention_started_ns > AXIS_MAX_AGE_NS:
        return False
      self.retention_verified = True
      self.retention_started_ns = 0
      self.retention_event_ns = occurrence
    else:
      if not self.retention_started_ns:
        if not self.retention_verified and not 0 <= now_ns - self.lateral_baseline_ns <= baseline_age_limit:
          return False
        self.retention_started_ns = now_ns
        self.retention_event_ns = occurrence
      if occurrence != self.retention_event_ns or now_ns - self.retention_started_ns > AXIS_MAX_AGE_NS:
        return False
    self.retention_qualified, self.retention_pending = matched, not matched
    self.lateral_native_ns = native.observedMonoTime
    return True

  def _availability_recovery_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, current, continuity, producer):
    event_ns = int(sm.logMonoTime['onroadEvents'])
    # Pending healthy SOFT join needs both owners + original deadlines/source/event/producer/epoch/inventory
    # and current native LAT grant.
    verified_pause = self.retention_verified or self.ordinary_warning_retained
    if (not latched or self.failed or self.paused or self.pending_event or not verified_pause or
      not self.retention_started_ns or not 0 <= now_ns - self.retention_started_ns <= AXIS_MAX_AGE_NS or
      not current or not continuity or str(axis.sessionId) != self.session or
      not producer or producer != self.availability_producer or
      str(getattr(axis, 'intentProducerSessionId', '')) != producer or
      int(getattr(axis, 'intentIntegrityEpoch', 0)) != self.availability_epoch or
      str(axis.faultReason) != 'none' or not axis.nativeAcknowledged or
      not axis.desiredLateral or not axis.lateralActive or
      not 0 < axis.sourceCarStateMonoTime <= stamp <= now_ns or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < event_ns == axis.faultEventMonoTime <= stamp):
      return False
    events = sm['onroadEvents']
    faults = [event for event in events if event.userDisable or event.softDisable or event.immediateDisable]
    combined = bool(axis.desiredLongitudinal and axis.longitudinalActive)
    if combined:
      # Event-first: recorded NEW occurrence, verified inactive/source-zero pause, exact join;
      # no pending-deadline/old-command-lease renewal.
      previous = self.signature
      if (previous is None or previous[0] != self.session or previous[5] != 'availabilityPause' or
        previous[7] != self.session or previous[8] != 0 or previous[9] or previous[10] or
        not previous[11] or previous[12] or not previous[13] or
        previous[14] != producer or previous[15] != self.availability_epoch or
        not 0 < previous[6] < event_ns or self.retention_event_ns != event_ns or
        len(faults) != 1 or faults[0].name != log.OnroadEvent.EventName.selfdrivedLagging or
        not faults[0].softDisable or faults[0].userDisable or faults[0].immediateDisable or
        any(event.noEntry and event.name != log.OnroadEvent.EventName.selfdrivedLagging for event in events)):
        return False
    elif (axis.desiredLongitudinal or axis.longitudinalActive or
          not self.retention_event_ns < event_ns or faults):
      return False
    native = _healthy_transport_native(sm, cp, cs, self.session, now_ns)
    return bool(native is not None and native.requestedLateral and native.lateralAllowed and
      (not combined or native.requestedLongitudinal and native.longitudinalAllowed) and
      native.observedMonoTime >= self.lateral_native_ns and
      (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) == self.lateral_inventory)

  def _new_expired_combined_source(self, sm, cp, axis, stamp, now_ns, signature):
    previous = self.signature
    if (now_ns <= signature[4] or previous is None or self.retention_started_ns or
      not (sm.seen['aolAxisState'] and sm.valid['aolAxisState'] and sm.alive['aolAxisState']) or
      not axis.desiredLongitudinal or not axis.longitudinalActive or
      not all(previous[index] for index in (9, 10, 11, 12, 13)) or previous[5] != 'none' or
      signature[1] <= self.sequence or stamp <= previous[2] or
      not 0 < self.lateral_source_ns <= previous[8] < signature[8] <= stamp or
      stamp - signature[8] > AXIS_MAX_AGE_NS or
      signature[4] != stamp + AXIS_MAX_AGE_NS):
      return False
    from openpilot.starpilot.car.hyundai.aol import qualified_ioniq6
    return qualified_ioniq6(cp)

  def _source_availability_retention(self, sm, cp, cs, axis, stamp, now_ns, *, latched, continuity, producer, signature):
    held = bool(signature == self.signature and not self.retention_started_ns)
    output_missing = not (sm.seen['aolAxisState'] and sm.valid['aolAxisState'] and sm.alive['aolAxisState'])
    matched_lag = bool((str(axis.faultReason) == 'critical' or
      str(axis.faultReason) == 'availabilityPause' and self.ordinary_warning_retained) and signature == self.signature and
      self.lateral_only_baseline and not self.failed and not self.pending_event and
      signature[0] == self.session and signature[7] == self.session and signature[1] == self.sequence and
      signature[14] == producer == self.availability_producer and signature[15] == self.availability_epoch and
      not any(signature[9:14]) and 0 < signature[6] <= stamp and
      0 < self.lateral_source_ns <= signature[8] <= stamp and stamp - signature[8] <= AXIS_MAX_AGE_NS)
    if (not latched or self.failed or self.paused or self.pending_event or not self.session or
      cp_identity(cp) != self.cp or not continuity or
      str(axis.sessionId) != self.session or str(axis.faultSessionId) != self.session or
      not axis.qualified or (str(axis.faultReason) != 'none' or not axis.nativeAcknowledged or
        not axis.desiredLateral or not axis.lateralActive) and not matched_lag or
      (axis.desiredLongitudinal or axis.longitudinalActive) and not held and
        not self._new_expired_combined_source(sm, cp, axis, stamp, now_ns, signature) or
      (not sm.seen['aolAxisState'] or not sm.valid['aolAxisState']) and not held or
      int(axis.observedMonoTime) != stamp or not 0 < axis.sourceCarStateMonoTime <= stamp <= now_ns or
      not 0 <= axis.validUntilMonoTime - stamp <= AXIS_MAX_AGE_NS or
      now_ns - axis.sourceCarStateMonoTime > SAFETY_MAX_AGE_NS or
      not (stamp - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or now_ns > axis.validUntilMonoTime or
           held and output_missing) or
      not producer or getattr(axis, 'intentProducerSessionId', '') != producer or
      producer != self.availability_producer or
      int(getattr(axis, 'intentIntegrityEpoch', 0)) != self.availability_epoch):
      return False
    if not 0 < self.lateral_source_ns <= now_ns <= self.lateral_source_ns + SAFETY_MAX_AGE_NS:
      return False
    if not self.source_retention_started_ns:
      if not self.lateral_baseline_ns or not 0 <= now_ns - self.lateral_baseline_ns <= SAFETY_MAX_AGE_NS:
        return False
      self.source_retention_started_ns = now_ns
    if not 0 <= now_ns - self.source_retention_started_ns <= SAFETY_MAX_AGE_NS:
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    events = sm['onroadEvents']
    faults = [e for e in events if e.immediateDisable or e.softDisable]
    users = [e for e in events if e.userDisable]
    stock_user = stock_lateral_user_event(cp)
    nominal_users = bool(stock_user is not None and users and all(e.name == stock_user and
      not e.softDisable and not e.immediateDisable for e in users))
    host_events = [e for e in events if not (e.name == stock_user and e.userDisable and
      not e.softDisable and not e.immediateDisable)]
    stock_event_first = bool(signature == self.signature and
      not axis.desiredLongitudinal and not axis.longitudinalActive and
      now_ns > axis.validUntilMonoTime and nominal_users and host_source_availability_only(host_events) and
      0 < axis.faultEventMonoTime < event_ns <= now_ns and now_ns - event_ns <= AXIS_MAX_AGE_NS)
    stock_pending = bool(cp.pcmCruise and self.retention_started_ns and signature == self.signature and
      not axis.desiredLongitudinal and not axis.longitudinalActive and
      0 < axis.faultEventMonoTime < self.retention_event_ns)
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        bool(faults) and not stock_event_first or stock_pending and not stock_event_first or
        matched_lag and not stock_event_first):
      return False
    native = _healthy_transport_native(sm, cp, cs, self.session, now_ns)
    inactive_native = bool(held and self.source_retention_started_ns and native is not None and
      not native.requestedLateral and not native.lateralAllowed and
      not native.requestedLongitudinal and not native.longitudinalAllowed)
    if (native is None or not native.pandaInventory or
        (not native.requestedLateral or not native.lateralAllowed) and not inactive_native or
        (axis.desiredLongitudinal or axis.longitudinalActive) and not held and
          not (native.requestedLongitudinal and native.longitudinalAllowed) or
        native.observedMonoTime < self.lateral_native_ns or
        (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != self.lateral_inventory):
      return False
    if stock_event_first:
      if not stock_lateral_off(cp, cs, native):
        return False
      if self.retention_started_ns:
        if (self.retention_event_ns != event_ns or
            not 0 <= now_ns - self.retention_started_ns <= AXIS_MAX_AGE_NS):
          return False
      else:
        self.retention_started_ns = now_ns
        self.retention_event_ns = event_ns
      self.retention_pending = True
    return True

  def _event_first_retention(self, sm, cp, axis, stamp, now_ns, *, latched, signature, native):
    if now_ns <= axis.validUntilMonoTime or signature != self.signature:
      return False
    if (not latched or self.failed or self.paused or not self.session or native is None or
      cp_identity(cp) != self.cp or
      str(axis.sessionId) != self.session or
      str(axis.faultSessionId) != self.session or str(axis.faultReason) != 'none' or
      not axis.qualified or not axis.nativeAcknowledged or not axis.desiredLateral or not axis.lateralActive or
      not sm.seen['aolAxisState'] or not sm.valid['aolAxisState'] or not sm.alive['aolAxisState'] or
      not 0 < axis.sourceCarStateMonoTime <= stamp <= now_ns or
      not self.pending_event and now_ns - stamp > AXIS_MAX_AGE_NS or
      stamp - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      axis.validUntilMonoTime != axis.sourceCarStateMonoTime + AXIS_MAX_AGE_NS or
      not stamp <= axis.validUntilMonoTime < stamp + AXIS_MAX_AGE_NS or
      native.observedMonoTime < self.healthy_native_ns or
      (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != self.healthy_inventory):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    faults = [event for event in sm['onroadEvents'] if event.immediateDisable or event.softDisable]
    isolated = bool(len(faults) == 1 and faults[0].name == log.OnroadEvent.EventName.controlsMismatch and
                    faults[0].immediateDisable)
    if (not isolated or not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < axis.faultEventMonoTime < event_ns <= now_ns or now_ns - event_ns > AXIS_MAX_AGE_NS):
      return False
    if not self.pending_event:
      if not self.healthy_feedback_ns or not 0 <= now_ns - self.healthy_feedback_ns <= AXIS_MAX_AGE_NS:
        return False
      self.pending_event = event_ns
    if event_ns != self.pending_event or now_ns - self.pending_event > AXIS_MAX_AGE_NS:
      return False
    self.awaiting = True
    return True

  def _angle_stop_intent_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer):
    from opendbc.car.structs import car
    from opendbc.car.hyundai.canfd_angle_aol import qualified, temporary_restriction
    from opendbc.car.mazda.stock_aol import qualified as mazda_qualified, temporary_restriction as mazda_restriction
    from opendbc.car.subaru.stock_aol import temporary_restriction as subaru_restriction

    from opendbc.car.hyundai.values import CAR

    # Exact profile admission remains in the configured native receipt below.
    ccnc_stop = bool((cs.standstill or 0 <= cs.vEgoRaw <= .3) and cp.openpilotLongitudinalControl and
      cp.carFingerprint in (CAR.KIA_EV9, CAR.HYUNDAI_IONIQ_5_PE) and not cs.accFaulted and not cs.steeringDisengage)
    combined_stop = bool(ccnc_stop and axis.desiredLongitudinal and axis.longitudinalActive)
    subaru_stop = cs.standstill and subaru_restriction(cp, cs)
    if (not (qualified(cp, marked_only=True) or mazda_qualified(cp, marked_only=True) or subaru_stop or ccnc_stop) or
      cs.gearShifter != car.CarState.GearShifter.drive or
      not (temporary_restriction(cp, cs) or mazda_restriction(cp, cs) or subaru_stop) or cs.steerFaultTemporary or cs.steerFaultPermanent or
      not latched or self.failed or self.paused or self.pending_event or self.retention_started_ns or
      not (self.lateral_only_baseline or ccnc_stop) or not self.lateral_inventory or not self.lateral_baseline_ns or
      not self.session or axis.sessionId != self.session or str(axis.faultReason) != 'none' or
      not producer or producer != self.availability_producer or
      axis.intentProducerSessionId != producer or axis.intentIntegrityEpoch != self.availability_epoch or
      not (axis.desiredLateral or subaru_stop and not axis.lateralActive) or not axis.nativeAcknowledged or
      (axis.desiredLongitudinal or axis.longitudinalActive) and not combined_stop or
      not 0 < axis.sourceCarStateMonoTime <= stamp <= now_ns or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        any(event.immediateDisable or event.softDisable or event.userDisable and
            event.name not in (log.OnroadEvent.EventName.pcmDisable, log.OnroadEvent.EventName.pedalPressed)
            for event in sm['onroadEvents'])):
      return False
    native = _configured_transport_native(sm, cp, self.session, now_ns, allow_restricted_lateral=True)
    return bool(native is not None and native.pandaInventory and native.requestedLateral and
      not native.lateralAllowed and
      (not native.requestedLongitudinal and not combined_stop or
       combined_stop and native.requestedLongitudinal and native.longitudinalAllowed) and
      native.observedMonoTime >= self.lateral_native_ns and
      (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) == self.lateral_inventory)

  def _inactive_gear_source_gap(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer):
    from opendbc.car import structs
    previous = self.signature
    if (not latched or self.failed or self.paused or self.pending_event or self.retention_started_ns or
      self.source_retention_started_ns or not self.ever_lateral_active or previous is None or
      not self.session or cp_identity(cp) != self.cp or previous[0] != self.session or
      previous[7] != self.session or previous[5] != 'none' or any(previous[9:13]) or not previous[13] or
      not producer or previous[14] != producer or
      not 0 < previous[8] <= previous[2] <= self.healthy_feedback_ns <= now_ns or
      now_ns - previous[8] > SAFETY_MAX_AGE_NS or
      cs.gearShifter not in (structs.CarState.GearShifter.reverse, structs.CarState.GearShifter.neutral) or
      not cs.canValid or cs.canTimeout or cs.steerFaultTemporary or cs.steerFaultPermanent or
      cs.accFaulted or cs.vehicleSensorsInvalid or cs.doorOpen or cs.seatbeltUnlatched or
      cs.steeringDisengage or not cs.cruiseState.available or cs.buttonEvents or
      not sm.seen['aolAxisState'] or not sm.alive['aolAxisState'] or sm.valid['aolAxisState'] or
      axis.qualified or axis.sourceCarStateMonoTime != 0 or str(axis.mode) != 'off' or
      axis.desiredLateral or axis.desiredLongitudinal or axis.lateralActive or axis.longitudinalActive or
      axis.nativeAcknowledged or str(axis.faultReason) != 'none' or
      str(axis.sessionId) != self.session or str(axis.faultSessionId) != self.session or
      str(axis.intentProducerSessionId) != producer or int(axis.intentIntegrityEpoch) != previous[15] or
      axis.sequence <= self.sequence or not previous[2] < stamp == axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime or
      axis.validUntilMonoTime - stamp != AXIS_MAX_AGE_NS):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    allowed = (log.OnroadEvent.EventName.wrongGear, log.OnroadEvent.EventName.reverseGear,
      log.OnroadEvent.EventName.preEnableStandstill, log.OnroadEvent.EventName.pedalPressed,
      log.OnroadEvent.EventName.gasPressedOverride, log.OnroadEvent.EventName.steerOverride,
      log.OnroadEvent.EventName.pcmDisable)
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns == axis.faultEventMonoTime <= stamp or now_ns - event_ns > 1_500_000_000 or
        any(event.name not in allowed or event.immediateDisable or
            event.softDisable and event.name != log.OnroadEvent.EventName.wrongGear or
            event.userDisable and event.name not in (log.OnroadEvent.EventName.reverseGear,
              log.OnroadEvent.EventName.pedalPressed, log.OnroadEvent.EventName.pcmDisable)
            for event in sm['onroadEvents'])):
      return False
    native = _healthy_transport_native(sm, cp, cs, self.session, now_ns)
    return bool(native is not None and native.pandaInventory and
      not (native.requestedLateral or native.lateralAllowed or native.requestedLongitudinal or native.longitudinalAllowed) and
      native.observedMonoTime >= self.healthy_native_ns and self.healthy_inventory ==
        (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)))

  def _inactive_source_pause_current(self, sm, cp, cs, axis, stamp, now_ns, *, latched, producer):
    from opendbc.car import structs
    previous = self.signature
    if (not latched or self.failed or self.paused or self.pending_event or self.retention_started_ns or
      self.source_retention_started_ns or not self.ever_lateral_active or previous is None or
      not self.session or cp_identity(cp) != self.cp or previous[0] != self.session or
      previous[7] != self.session or previous[5] != 'none' or not previous[11] or not previous[13] or
      not producer or previous[14] != producer or
      not 0 < previous[8] <= previous[2] <= self.healthy_feedback_ns <= now_ns or
      now_ns - previous[8] > SAFETY_MAX_AGE_NS or
      cs.gearShifter not in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low,
                            structs.CarState.GearShifter.sport, structs.CarState.GearShifter.brake) or
      not cs.canValid or cs.canTimeout or cs.steerFaultTemporary or cs.steerFaultPermanent or
      cs.accFaulted or cs.vehicleSensorsInvalid or cs.doorOpen or cs.seatbeltUnlatched or
      cs.steeringDisengage or not cs.cruiseState.available or cs.buttonEvents or
      not sm.seen['aolAxisState'] or not sm.alive['aolAxisState'] or not sm.valid['aolAxisState'] or
      not axis.qualified or axis.sourceCarStateMonoTime != 0 or str(axis.mode) != 'off' or
      axis.desiredLateral or axis.desiredLongitudinal or axis.lateralActive or axis.longitudinalActive or
      axis.nativeAcknowledged or str(axis.faultReason) != 'availabilityPause' or
      str(axis.sessionId) != self.session or str(axis.faultSessionId) != self.session or
      str(axis.intentProducerSessionId) != producer or int(axis.intentIntegrityEpoch) != previous[15] or
      axis.sequence <= self.sequence or not previous[2] < stamp == axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime or
      axis.validUntilMonoTime - stamp != AXIS_MAX_AGE_NS or not 0 < axis.faultEventMonoTime <= stamp):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    events = sm['onroadEvents']
    matched = event_ns == axis.faultEventMonoTime
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        (not host_source_availability_only(events) if matched else
         any(event.immediateDisable or event.softDisable or event.userDisable for event in events))):
      return False
    native = _healthy_transport_native(sm, cp, cs, self.session, now_ns)
    if native is None or not native.pandaInventory:
      return False
    granted = bool(native.requestedLateral and native.lateralAllowed and
      native.requestedLongitudinal == previous[12] and native.longitudinalAllowed == previous[12])
    withdrawn = not (native.requestedLateral or native.lateralAllowed or native.requestedLongitudinal or native.longitudinalAllowed)
    return bool((granted or withdrawn) and native.observedMonoTime >= self.healthy_native_ns and
      self.healthy_inventory == (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)))

  def observe(self, sm, cp, cs, now_ns, *, latched=True, cancel_source_ns=0, brake_source_ns=0,
              gm_withdrawal_source_ns=0, intent_producer_session='', main_arm_ns=0, startup_calibration_pending=False, intent_paused=False):
    self.ordinary_warning_qualified = False
    self.temporary_host_qualified = False
    self.qualified = False
    self.awaiting = False
    self.retention_qualified = False
    self.retention_pending = False
    try:
      axis = sm['aolAxisState']
      if not latched or cp_identity(cp) != self.cp or self.session and str(axis.sessionId) != self.session:
        self.ordinary_warning_retained = False
        self.ordinary_warning_pending_baseline = None
        self.temporary_eps_event_ns = 0
        self.ccnc_main_off_source_ns = 0
        self.cold_park_waiting = False
        self.cold_main_sparse_waiting = False
      stamp = int(sm.logMonoTime['aolAxisState'])
      reason = str(axis.faultReason)
      signature = (str(axis.sessionId), int(axis.sequence), stamp, int(axis.observedMonoTime),
        int(axis.validUntilMonoTime), reason, int(axis.faultEventMonoTime), str(axis.faultSessionId),
        int(axis.sourceCarStateMonoTime), bool(axis.lateralActive), bool(axis.longitudinalActive),
        bool(axis.desiredLateral), bool(axis.desiredLongitudinal), bool(axis.nativeAcknowledged),
        str(getattr(axis, 'intentProducerSessionId', '')), int(getattr(axis, 'intentIntegrityEpoch', 0)))
      current = bool(sm.seen['aolAxisState'] and sm.valid['aolAxisState'] and sm.alive['aolAxisState'] and
        axis.qualified and axis.sessionId and axis.sequence > 0 and stamp == axis.observedMonoTime and
        0 < stamp <= now_ns <= axis.validUntilMonoTime and now_ns - stamp <= AXIS_MAX_AGE_NS and
        axis.validUntilMonoTime - stamp <= AXIS_MAX_AGE_NS and axis.faultSessionId == axis.sessionId and
        cp_identity(cp) == self.cp)
      continuity = bool(not self.session or axis.sessionId == self.session and
        (axis.sequence > self.sequence or axis.sequence == self.sequence and signature == self.signature))
      if not current and continuity and self._inactive_gear_source_gap(sm, cp, cs, axis, stamp, now_ns, latched=latched,
          producer=intent_producer_session):
        return False
      if current and continuity and reason == 'availabilityPause' and self._inactive_source_pause_current(
          sm, cp, cs, axis, stamp, now_ns, latched=latched, producer=intent_producer_session):
        self.retention_qualified = int(sm.logMonoTime['onroadEvents']) == axis.faultEventMonoTime
        self.retention_pending = not self.retention_qualified
        return False
      from openpilot.starpilot.car.gm.aol import sdgm_alpha_lateral_owner
      if (sdgm_alpha_lateral_owner(cp) and latched and current and continuity and self.signature is not None and
          signature[14:16] != self.signature[14:16]):
        self.failed = True
        return True
      if ((self.cold_main_wait_ns or self.cold_park_waiting) and current and continuity and self.signature is not None and
          signature[14:16] != self.signature[14:16]):
        self.failed = True
        return True
      if (cp.brand == 'gm' and current and continuity and latched and not self.failed and
          not self.ever_lateral_active and not self.pending_event and reason == 'none' and
          axis.nativeAcknowledged and not any(signature[9:13]) and
          intent_producer_session and signature[14] == intent_producer_session and
          (self.signature is None or signature[14:16] == self.signature[14:16]) and
          0 < axis.sourceCarStateMonoTime <= stamp and now_ns - axis.sourceCarStateMonoTime <= AXIS_MAX_AGE_NS):
        from openpilot.starpilot.car.gm.aol import cold_park_readiness
        native = _healthy_transport_native(sm, cp, cs, str(axis.sessionId), now_ns)
        event_ns = int(sm.logMonoTime['onroadEvents'])
        if (axis.faultEventMonoTime == event_ns and sm.seen['onroadEvents'] and
            sm.valid['onroadEvents'] and sm.alive['onroadEvents'] and
            0 < event_ns <= stamp and now_ns - event_ns <= 1_500_000_000 and
            cold_park_readiness(cp, cs, native, sm['onroadEvents'])):
          self.cold_park_waiting = True
          self.session = str(axis.sessionId)
          self.sequence, self.signature = int(axis.sequence), signature
          self.temporary_host_qualified = True
          return False
      if cp.brand == 'ford' and latched:
        from openpilot.starpilot.car.ford.command_pause import retain_command_pause_intent
        if retain_command_pause_intent(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session):
          return False
      if (cp.brand == 'chrysler' and current and continuity and latched and
          not self.failed and not self.ever_lateral_active):
        from openpilot.starpilot.car.chrysler.aol import cold_calibration_intent_current
        native = _healthy_transport_native(sm, cp, cs, str(axis.sessionId), now_ns)
        if cold_calibration_intent_current(self, sm, cp, cs, axis, native, now_ns,
            pending=startup_calibration_pending, producer=intent_producer_session):
          self.session = str(axis.sessionId)
          self.sequence, self.signature = int(axis.sequence), signature
          return False
      if current and continuity and 0 < main_arm_ns <= now_ns <= main_arm_ns + AXIS_MAX_AGE_NS:
        from openpilot.starpilot.car.gm.aol import prior_main_off_intent_current
        if prior_main_off_intent_current(self, sm, cp, cs, axis, now_ns,
            latched=latched, main_arm_ns=main_arm_ns, producer=intent_producer_session):
          self.sequence, self.signature = int(axis.sequence), signature
          return False
      if cp.brand == 'gm' and current and continuity and latched:
        from openpilot.starpilot.car.gm.aol import cold_gm_main_intent_current
        native = (_healthy_transport_native(sm, cp, cs, str(axis.sessionId), now_ns)
                  if cp.brand == 'gm' else None)
        if cold_gm_main_intent_current(self, sm, cp, cs, axis, native, now_ns,
            producer=intent_producer_session):
          self.session = str(axis.sessionId)
          self.sequence, self.signature = int(axis.sequence), signature
          event_ns = int(sm.logMonoTime['onroadEvents'])
          if not self.cold_main_wait_ns:
            self.cold_main_wait_ns = event_ns
          self.cold_main_sparse_waiting |= now_ns - event_ns > AXIS_MAX_AGE_NS
          self.temporary_host_qualified = True
          return False
      if (cp.brand == 'gm' and current and continuity and latched and not self.failed and
          not self.ever_lateral_active and not self.pending_event):
        from openpilot.starpilot.car.gm.aol import cold_main_readiness
        native = _healthy_transport_native(sm, cp, cs, str(axis.sessionId), now_ns)
        event_ns = int(sm.logMonoTime['onroadEvents'])
        recovering = bool(reason == 'none' and axis.desiredLateral and not axis.desiredLongitudinal and
          not axis.lateralActive and not axis.longitudinalActive and axis.nativeAcknowledged and
          0 < self.cold_main_wait_ns <= now_ns and
          (self.cold_main_sparse_waiting or now_ns <= self.cold_main_wait_ns + SAFETY_MAX_AGE_NS) and
          0 < axis.sourceCarStateMonoTime <= stamp and now_ns - axis.sourceCarStateMonoTime <= AXIS_MAX_AGE_NS)
        if ((reason == 'availabilityPause' and not any(signature[9:13]) or recovering) and
            intent_producer_session and signature[14] == intent_producer_session and
            (self.signature is None or signature[14:16] == self.signature[14:16]) and
            axis.faultEventMonoTime == event_ns and sm.seen['onroadEvents'] and
            sm.valid['onroadEvents'] and sm.alive['onroadEvents'] and 0 < event_ns <= stamp and
            now_ns - event_ns <= AXIS_MAX_AGE_NS and
            cold_main_readiness(cp, cs, native, sm['onroadEvents'], recovering=recovering)):
          if recovering:
            self.cold_main_wait_ns = 0
            self.cold_main_sparse_waiting = False
          else:
            self.cold_main_wait_ns = event_ns
          self.session = str(axis.sessionId)
          self.sequence, self.signature = int(axis.sequence), signature
          self.temporary_host_qualified = True
          return False
      if (self.cold_main_wait_ns and current and continuity and reason == 'none' and
          axis.nativeAcknowledged and axis.desiredLateral and not axis.lateralActive):
        event_ns = int(sm.logMonoTime['onroadEvents'])
        recovery_ahead = bool(sm.seen['onroadEvents'] and sm.valid['onroadEvents'] and
          sm.alive['onroadEvents'] and 0 < event_ns < axis.faultEventMonoTime <= stamp and
          now_ns - event_ns <= AXIS_MAX_AGE_NS)
        if not recovery_ahead:
          self.failed = True
          return True
      if current and continuity and self._angle_stop_intent_current(sm, cp, cs, axis, stamp, now_ns,
          latched=latched, producer=intent_producer_session):
        self.sequence, self.signature = int(axis.sequence), signature
        return False
      if current and continuity and (reason == 'critical' or self.lag_withdrawal_ns and not axis.lateralActive or
          getattr(sm, 'updated', {}).get('onroadEvents', False)) and self._temporary_lag_intent_current(sm, cp, cs, axis, stamp, now_ns,
          latched=latched, producer=intent_producer_session, signature=signature):
        self.sequence, self.signature = int(axis.sequence), signature
        return False
      if (cp.brand == 'gm' and self.lag_withdrawal_ns and current and continuity and reason == 'none' and
          axis.desiredLateral and not axis.lateralActive):
        self.failed = True
        return True
      if cp.brand == 'volkswagen' and current and continuity:
        from openpilot.starpilot.car.volkswagen.aol import retain_eps_recovery
        if retain_eps_recovery(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session):
          self.sequence, self.signature = int(axis.sequence), signature
          return False
      if current and continuity and self._ordinary_warning_intent_current(sm, cp, cs, axis, stamp, now_ns,
          latched=latched, producer=intent_producer_session):
        self.ordinary_warning_qualified = any(event.softDisable for event in sm['onroadEvents'])
        self.sequence, self.signature = int(axis.sequence), signature
        return False
      if current and continuity and self._temporary_host_intent_current(sm, cp, cs, axis, stamp, now_ns,
          latched=latched, producer=intent_producer_session):
        self.sequence, self.signature = int(axis.sequence), signature
        return False
      if (cp.brand == 'ford' and current and continuity and
          self._ford_gas_release_eps_join_current(sm, cp, cs, axis, stamp, now_ns,
            latched=latched, producer=intent_producer_session)):
        return False
      if current and continuity and self._temporary_steering_intent_current(sm, cp, cs, axis, stamp, now_ns,
          latched=latched, producer=intent_producer_session, signature=signature):
        self.sequence, self.signature = int(axis.sequence), signature
        return False
      if cp.brand == 'honda' and current and continuity:
        from opendbc.car.honda.stock_aol import qualified as honda_stock_qualified
        from openpilot.starpilot.aol.reverse_intent import retain_reverse_intent

        if retain_reverse_intent(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session,
            qualified_cp=honda_stock_qualified(cp, marked_only=True),
            extra_user_events=(log.OnroadEvent.EventName.cruiseDisabled,),
            informational_events=(log.OnroadEvent.EventName.userBookmarkNotPaired,), allow_withdrawal_join=True):
          return False
      if cp.brand == 'mazda' and current and continuity:
        from opendbc.car.mazda.stock_aol import qualified as mazda_qualified
        from openpilot.starpilot.aol.reverse_intent import retain_reverse_intent

        if retain_reverse_intent(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session,
            qualified_cp=mazda_qualified(cp, marked_only=True),
            informational_events=(log.OnroadEvent.EventName.userBookmarkNotPaired,), allow_withdrawal_join=True):
          return False
      if cp.brand == 'rivian' and current and continuity:
        from opendbc.car.rivian.stock_aol import qualified as rivian_qualified
        from openpilot.starpilot.aol.reverse_intent import retain_reverse_intent

        if retain_reverse_intent(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session,
            qualified_cp=rivian_qualified(cp, marked_only=True),
            informational_events=(log.OnroadEvent.EventName.userBookmarkNotPaired,), allow_withdrawal_join=True):
          return False
      if cp.brand == 'nissan' and current and continuity:
        from openpilot.starpilot.car.nissan.aol import qualified as nissan_qualified
        from openpilot.starpilot.aol.reverse_intent import retain_reverse_intent

        if retain_reverse_intent(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session,
            qualified_cp=nissan_qualified(cp, marked_only=True),
            informational_events=(log.OnroadEvent.EventName.userBookmarkNotPaired,), allow_withdrawal_join=True):
          return False
      if cp.brand == 'subaru' and current and continuity:
        from opendbc.car.subaru.stock_aol import qualified as subaru_qualified
        from openpilot.starpilot.aol.reverse_intent import retain_reverse_intent

        if retain_reverse_intent(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session,
            qualified_cp=subaru_qualified(cp, marked_only=True),
            informational_events=(log.OnroadEvent.EventName.userBookmarkNotPaired,), allow_withdrawal_join=True):
          return False
      if cp.brand == 'ford' and current and continuity:
        from openpilot.starpilot.car.ford.reverse_intent import retain_reverse_intent
        from openpilot.starpilot.car.ford.physical_pause import retain_physical_pause_intent
        from openpilot.starpilot.car.ford.gas_override import retained_gas_lateral_native

        if retain_reverse_intent(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session):
          return False
        if retain_physical_pause_intent(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session):
          return False
        gas_native = retained_gas_lateral_native(self, sm, cp, cs, axis, now_ns, latched=latched, producer=intent_producer_session)
        if gas_native is not None:
          self.lateral_baseline_ns = stamp
          self.lateral_source_ns = int(axis.sourceCarStateMonoTime)
          self.lateral_native_ns = int(gas_native.observedMonoTime)
          self.sequence, self.signature = int(axis.sequence), signature
          return False
      brake_native = (self._honda_brake_lateral_current(sm, cp, cs, axis, now_ns,
        latched=latched, brake_source_ns=brake_source_ns) if current and continuity else None)
      if brake_native is not None:
        self.healthy_feedback_ns = stamp
        self.healthy_native_ns = int(brake_native.observedMonoTime)
        self.sequence, self.signature = int(axis.sequence), signature
        return False
      if current and continuity and (gm_withdrawal_source_ns or self.gm_withdrawal_ns):
        from openpilot.starpilot.car.gm.aol import retained_physical_longitudinal_intent
        gm_native = retained_physical_longitudinal_intent(self, sm, cp, cs, axis, now_ns,
          latched=latched, source_ns=gm_withdrawal_source_ns, producer=intent_producer_session)
        if gm_native is not None:
          self.healthy_feedback_ns = stamp
          self.healthy_native_ns = int(gm_native.observedMonoTime)
          self.sequence, self.signature = int(axis.sequence), signature
          return False
      if self._source_availability_retention(sm, cp, cs, axis, stamp, now_ns,
          latched=latched, continuity=continuity, producer=intent_producer_session, signature=signature):
        self.sequence, self.signature = int(axis.sequence), signature
        return False
      if not self.failed and self._availability_retention(sm, cp, cs, axis, stamp, now_ns,
          latched=latched, current=current, continuity=continuity, intent_producer_session=intent_producer_session):
        self.sequence, self.signature = int(axis.sequence), signature
        return False
      if self._availability_recovery_current(sm, cp, cs, axis, stamp, now_ns,
          latched=latched, current=current, continuity=continuity, producer=intent_producer_session):
        self.retention_started_ns = self.retention_event_ns = 0
        self.retention_verified = False
      if self.retention_started_ns:
        self.retention_started_ns = self.retention_event_ns = 0
        self.retention_verified = False
        self.failed = True
        self.lateral_only_baseline = False
        return True
      if reason != 'availabilityPause' and not availability_only(sm['onroadEvents']):
        self.retention_started_ns = self.retention_event_ns = 0
        self.retention_verified = False
      native = _healthy_transport_native(sm, cp, cs, str(axis.sessionId), now_ns)
      if (native is None and intent_paused and cs.canValid and not cs.canTimeout and
          not cs.steerFaultPermanent and not any(signature[9:13])):
        native = _configured_transport_native(sm, cp, str(axis.sessionId), now_ns)
      from opendbc.car import structs
      previous = self.signature
      inactive = bool(not self.failed and self.session and previous is not None and
        previous[5] == 'none' and previous[14:16] == signature[14:16] and
        intent_producer_session and signature[14] == intent_producer_session and
        (intent_paused or bool(getattr(self, 'temporary_eps_event_ns', 0)) or not any(previous[9:13])) and
        str(axis.mode) == 'off' and not any(signature[9:13]) and
        cs.cruiseState.available and not cs.accFaulted and not cs.vehicleSensorsInvalid and
        not cs.doorOpen and not cs.steeringDisengage and
        (cs.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport,
                           structs.CarState.GearShifter.low, structs.CarState.GearShifter.brake) or
         intent_paused and cs.gearShifter in (structs.CarState.GearShifter.reverse, structs.CarState.GearShifter.neutral)) and
        sm.seen['onroadEvents'] and sm.valid['onroadEvents'] and sm.alive['onroadEvents'] and
        0 < int(sm.logMonoTime['onroadEvents']) <= now_ns and
        now_ns - int(sm.logMonoTime['onroadEvents']) <= 1_500_000_000 and
        all(not event.immediateDisable and
            (not event.softDisable or intent_paused and event.name in
             (log.OnroadEvent.EventName.wrongGear, log.OnroadEvent.EventName.steerTempUnavailable) or
             cs.seatbeltUnlatched and event.name == log.OnroadEvent.EventName.seatbeltNotLatched and
             event.noEntry and not event.userDisable) and
            (not event.userDisable or event.name in
             (log.OnroadEvent.EventName.pcmDisable, log.OnroadEvent.EventName.buttonCancel,
              log.OnroadEvent.EventName.reverseGear) or intent_paused and event.name == log.OnroadEvent.EventName.wrongGear)
            for event in sm['onroadEvents']) and
        native is not None and native.pandaInventory and self.healthy_inventory ==
          (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)) and
        not native.requestedLateral and not native.lateralAllowed and
        not native.requestedLongitudinal and not native.longitudinalAllowed)
      from openpilot.starpilot.car.gm.aol import main_off_idle_native_baseline
      gm_main_off = bool(not latched and current and continuity and
        sm.seen['onroadEvents'] and sm.valid['onroadEvents'] and sm.alive['onroadEvents'] and
        0 < int(sm.logMonoTime['onroadEvents']) <= now_ns and
        now_ns - int(sm.logMonoTime['onroadEvents']) <= 1_500_000_000 and
        axis.faultEventMonoTime == int(sm.logMonoTime['onroadEvents']) and
        main_off_idle_native_baseline(cp, cs, axis, native, sm['onroadEvents'], producer=intent_producer_session))
      baseline = bool(reason == 'none' and (axis.nativeAcknowledged or inactive or gm_main_off) and
        0 < axis.sourceCarStateMonoTime <= stamp and
        stamp - axis.sourceCarStateMonoTime <= AXIS_MAX_AGE_NS)
      if native is None and latched and current and continuity and baseline:
        native = _newer_combined_intent_native(sm, cp, cs, axis, now_ns)
      lateral_native = (self._native_long_withdrawal_lateral_current(sm, cp, cs, axis, now_ns,
        latched=latched, producer=intent_producer_session)
        if native is None and current and continuity and baseline else None)
      first_ford_grant = (self._first_ford_gas_lateral_grant(sm, cp, cs, axis, now_ns,
        latched=latched, producer=intent_producer_session)
        if native is None and lateral_native is None and current and continuity and baseline else None)
      if first_ford_grant is not None:
        lateral_native = first_ford_grant
        self.availability_producer = str(axis.intentProducerSessionId)
        self.availability_epoch = int(axis.intentIntegrityEpoch)
        self.lateral_inventory = (lateral_native.pandaSerial, tuple(slot.hardwareSerial for slot in lateral_native.pandaInventory))
        self.ever_lateral_active = True
      if lateral_native is not None:
        from openpilot.starpilot.car.hyundai.aol import qualified_ccnc_long_aol
        if not cs.cruiseState.available and qualified_ccnc_long_aol(cp, marked_only=True):
          self.ccnc_main_off_source_ns = now_ns
        self.lateral_baseline_ns = stamp
        self.lateral_source_ns = int(axis.sourceCarStateMonoTime)
        self.lateral_native_ns = int(lateral_native.observedMonoTime)
        self.sequence, self.signature = int(axis.sequence), signature
        return False
      transport_healthy = native is not None
      cancel_native = (self._longitudinal_cancel_current(sm, cp, cs, axis, now_ns, latched=latched,
                                         cancel_source_ns=cancel_source_ns)
        if current and continuity and baseline and not transport_healthy else None)
      if self._event_first_retention(sm, cp, axis, stamp, now_ns, latched=latched,
          signature=signature, native=native):
        return False
      current = bool(current and (transport_healthy or cancel_native is not None))
      if current and baseline and not latched:
        # Healthy idle ends cold MAIN recovery; cleared-intent wait cannot block next request.
        self.cold_main_wait_ns = 0
        self.cold_main_sparse_waiting = False
        self.cold_park_waiting = False
        self.temporary_eps_event_ns = 0
        self.session = ''
        self.pending_event = 0
        self.failed = False
        self.longitudinal_cancel_ns = 0
        continuity = True
      if self.failed:
        self.longitudinal_cancel_ns = 0
        return True
      if cancel_native is not None:
        if not self.longitudinal_cancel_ns:
          self.longitudinal_cancel_ns = int(cancel_source_ns)
          self.last_cancel_source_ns = int(cancel_source_ns)
        self.lateral_baseline_ns = now_ns
        self.lateral_source_ns = int(axis.sourceCarStateMonoTime)
        self.lateral_native_ns = int(cancel_native.observedMonoTime)
        self.sequence = int(axis.sequence)
        self.signature = signature
        return False
      if transport_healthy:
        if not latched or native is not None and not native.requestedLongitudinal:
          self.gm_withdrawal_ns = 0
        if native is not None and not native.requestedLongitudinal:
          self.honda_brake_ns = 0
        if (not latched or native is not None and not native.requestedLongitudinal or
            self.longitudinal_cancel_ns and now_ns - self.longitudinal_cancel_ns > SAFETY_MAX_AGE_NS):
          self.longitudinal_cancel_ns = 0
      if not current or not continuity or reason not in ('none', 'transportPause'):
        self.longitudinal_cancel_ns = 0
        hard = (self.paused or bool(self.pending_event) or reason == 'critical' or
                bool(self.session and (latched or not continuity)))
        self.paused = False
        self.pending_event = 0
        self.failed = hard
        self.lateral_only_baseline = bool(self.lateral_only_baseline and not hard)
        return hard
      known = bool(self.session)
      if reason == 'none':
        if not baseline:
          hard = self.paused or bool(self.pending_event) or bool(self.session and latched)
          self.paused = False
          self.pending_event = 0
          self.failed = hard
          self.lateral_only_baseline = bool(self.lateral_only_baseline and not hard)
          return hard
        event_ns = int(sm.logMonoTime['onroadEvents'])
        faults = [e for e in sm['onroadEvents'] if e.immediateDisable or e.softDisable]
        isolated = bool(len(faults) == 1 and faults[0].name == log.OnroadEvent.EventName.controlsMismatch and
                        faults[0].immediateDisable)
        if known and isolated and sm.valid['onroadEvents'] and 0 < axis.faultEventMonoTime < event_ns <= now_ns:
          if not self.pending_event:
            self.pending_event = event_ns
          if event_ns != self.pending_event or now_ns - self.pending_event > AXIS_MAX_AGE_NS:
            self.pending_event = 0
            self.paused = False
            self.failed = True
            self.lateral_only_baseline = False
            return True
          self.awaiting = True
          return False
        if self.pending_event:
          self.pending_event = 0
          self.paused = False
          self.failed = True
          self.lateral_only_baseline = False
          return True
        cancel_pending = self._longitudinal_cancel_current(sm, cp, cs, axis, now_ns, latched=latched,
          cancel_source_ns=cancel_source_ns, withdrawal_required=False)
        if cancel_pending is not None:
          self.longitudinal_cancel_ns = int(cancel_source_ns)
          self.last_cancel_source_ns = int(cancel_source_ns)
        self.source_retention_started_ns = 0
        self.healthy_feedback_ns = now_ns
        self.healthy_native_ns = int(native.observedMonoTime) if native is not None else 0
        self.healthy_inventory = ((native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory))
          if native is not None else None)
        self.session = str(axis.sessionId)
        lateral_baseline = bool(axis.desiredLateral and axis.lateralActive and native is not None and
          native.pandaInventory and native.requestedLateral and native.lateralAllowed)
        self.lateral_baseline_ns = now_ns if lateral_baseline else 0
        self.lateral_source_ns = int(axis.sourceCarStateMonoTime) if lateral_baseline else 0
        self.availability_producer = (str(getattr(axis, 'intentProducerSessionId', '')) if lateral_baseline and
          intent_producer_session and getattr(axis, 'intentProducerSessionId', '') == intent_producer_session else '')
        self.availability_epoch = int(getattr(axis, 'intentIntegrityEpoch', 0)) if self.availability_producer else None
        self.lateral_only_baseline = bool(lateral_baseline and self.availability_producer and
          not axis.desiredLongitudinal and not axis.longitudinalActive and not native.requestedLongitudinal)
        if lateral_baseline and native is not None:
          self.ordinary_warning_retained = False
          self.ever_lateral_active = True
          self.lag_withdrawal_ns = 0
          self.cold_main_wait_ns = 0
          self.cold_main_sparse_waiting = False
          self.cold_park_waiting = False
          self.temporary_eps_event_ns = 0
          self.lateral_native_ns = int(native.observedMonoTime)
          self.lateral_inventory = (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory))
        else:
          self.lateral_native_ns = 0
          self.lateral_inventory = None
        self.paused = False
      else:
        provenance = bool(known and not axis.lateralActive and not axis.longitudinalActive and
          not axis.desiredLongitudinal and 0 < axis.faultEventMonoTime <= stamp)
        pending = bool(provenance and axis.faultEventMonoTime > sm.logMonoTime['onroadEvents'])
        if pending and not self.pending_event:
          self.pending_event = int(axis.faultEventMonoTime)
        self.qualified = bool(provenance and
          axis.faultEventMonoTime == sm.logMonoTime['onroadEvents'] and
          sm.valid['onroadEvents'] and axis.faultEventMonoTime <= now_ns)
        if self.pending_event and (axis.faultEventMonoTime != self.pending_event or
          now_ns - self.pending_event > AXIS_MAX_AGE_NS):
          self.pending_event = 0
          self.paused = False
          self.failed = True
          self.lateral_only_baseline = False
          return True
        if not self.qualified and not pending:
          self.failed = True
          self.lateral_only_baseline = False
          return True
        if self.qualified:
          self.pending_event = 0
        self.paused = True
      self.sequence = int(axis.sequence)
      self.signature = signature
      return False
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
      self.longitudinal_cancel_ns = 0
      hard = self.paused or bool(self.pending_event) or bool(self.session and latched)
      self.paused = False
      self.pending_event = 0
      self.failed = hard
      self.lateral_only_baseline = bool(self.lateral_only_baseline and not hard)
      return hard
