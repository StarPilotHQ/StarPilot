from opendbc.car import structs
from opendbc.car.volkswagen.stock_aol import qualified as stock_qualified, safety_config as aol_safety_config, AOL_WORD, MANUAL_WORD, DIRECT_WORD, AOL_WORDS
from opendbc.car.volkswagen.long_aol import qualified as long_qualified, WORDS as LONG_WORDS
from opendbc.car.volkswagen.meb_modes import qualified as meb_qualified, profile_word as meb_word, base_word as meb_base_word, AOL_WORDS as MEB_WORDS
from opendbc.car.volkswagen.pq_aol import qualified as pq_qualified, WORDS as PQ_WORDS
from openpilot.starpilot.aol.intent import AolCardIntent, AOL_TOGGLE, ButtonType
from openpilot.starpilot.aol.policy import AolVehiclePolicy


def qualified(cp, *, marked_only=False):
  return (stock_qualified(cp, marked_only=marked_only) or long_qualified(cp, marked_only=marked_only) or
          meb_qualified(cp, marked_only=marked_only) or pq_qualified(cp, marked_only=marked_only))



def lateral_output_inhibited(cp, state):
  from openpilot.starpilot.aol.runtime import ordinary_lateral_requested
  return bool(qualified(cp, marked_only=True) and
              not ordinary_lateral_requested(True, state, cp))


def temporary_restriction(cp, state):
  gears = structs.CarState.GearShifter
  return bool(state is not None and qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and
              not state.steerFaultPermanent and (state.steerFaultTemporary or lateral_output_inhibited(cp, state) or
              state.gearShifter not in (gears.drive, gears.sport, gears.eco, gears.manumatic)))


def below_engage_lateral_allowed(cp, state, intent):
  """A manual cruise minimum does not disarm already armed lateral intent."""
  return bool(long_qualified(cp, marked_only=True) and
    cp.transmissionType == structs.CarParams.TransmissionType.manual and
    state is not None and state.canValid and not state.canTimeout and
    not state.steerFaultPermanent and not state.steerFaultTemporary and state.cruiseState.available and
    state.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport,
                         structs.CarState.GearShifter.eco, structs.CarState.GearShifter.manumatic) and
    intent is not None and intent.allowedLatch and not intent.pauseLateral)


def policy_for(cp):
  if not qualified(cp):
    return AolVehiclePolicy()
  word = AOL_WORD | (MANUAL_WORD if cp.transmissionType == structs.CarParams.TransmissionType.manual else 0)
  word |= DIRECT_WORD if cp.transmissionType == structs.CarParams.TransmissionType.direct else 0
  if meb_qualified(cp):
    word = meb_word(cp) & ~meb_base_word(cp)
  return AolVehiclePolicy(intent_supported=True, settings_supported=True, runtime_supported=True,
                         normal_runtime_supported=True, ordinary_axis_ack_required=qualified(cp, marked_only=True),
                         explicit_latch=True, lkas_button_supported=False, safety_param_addition=word,
                         alternative_experience_addition=32)


def native_profile_supported(model, param):
  return (model == int(structs.CarParams.SafetyModel.volkswagen) and param in AOL_WORDS | LONG_WORDS or
          model == int(structs.CarParams.SafetyModel.volkswagenMeb) and param in MEB_WORDS or
          model == int(structs.CarParams.SafetyModel.volkswagenPq) and param in PQ_WORDS)


def native_accepts_cp(cp, model, param):
  config = aol_safety_config(cp)
  return (qualified(cp, marked_only=True) and config is not None and native_profile_supported(model, param) and
          model == int(config.safetyModel.raw) and param == config.safetyParam)


def native_latch_rejected(cp, native):
  return qualified(cp, marked_only=True) and native.requestedLateral and not native.lateralAllowed


def ordinary_axis_request_allowed(cp, state):
  return qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and not state.steerFaultPermanent


def retain_on_native_denial(cp, native, state):
  return temporary_restriction(cp, state)


class VolkswagenStockCardIntent(AolCardIntent):
  observe_stock_engagement = True

  def __init__(self, cp, settings):
    self.cp = cp
    self.cruise_lateral = stock_qualified(cp) or long_qualified(cp) or pq_qualified(cp)
    self.physical_eps_pause = False
    self.eps_recovery_ns = 0
    self.main_seen = False
    self.main_startup_pending = True
    self.main_available = False
    self.main_neutral = False
    self.main_pulse = False
    self.startup_arm_ns = 0
    self.reverse_pause = False
    super().__init__(settings, explicit_latch=True, lkas_button_supported=False,
                     preserve_unassigned_cancel=self.cruise_lateral)

  def update(self, CS, **kwargs):
    from openpilot.starpilot.aol.runtime import SAFETY_MAX_AGE_NS
    now_ns = kwargs.get('now_ns', 0)
    self.reverse_pause = bool(CS.canValid and not CS.canTimeout and CS.gearShifter == structs.CarState.GearShifter.reverse)
    if temporary_eps_restriction(self.cp, CS):
      self.physical_eps_pause = True
      self.eps_recovery_ns = 0
    elif self.physical_eps_pause and _eps_physical_state(self.cp, CS):
      self.physical_eps_pause = False
      self.eps_recovery_ns = now_ns
    elif not _eps_physical_state(self.cp, CS):
      self.physical_eps_pause = False
      self.eps_recovery_ns = 0
    rejection_ns = kwargs.get('native_rejection_ns', 0)
    if (kwargs.get('fault_active') is False and 0 < rejection_ns <= self.eps_recovery_ns <= now_ns and
        now_ns - self.eps_recovery_ns <= SAFETY_MAX_AGE_NS):
      kwargs['native_rejection_ns'] = 0
    state = structs.CarState(**CS.to_dict())
    events = []
    for event in CS.buttonEvents:
      if event.type == ButtonType.mainCruise and self.settings.main_action != AOL_TOGGLE:
        if event.pressed:
          self._perform(self.settings.main_action)
      else:
        events.append(structs.CarState.ButtonEvent(**event.to_dict()))
    available = bool(state.cruiseState.available)
    if (not self.settings.enabled or self.settings.main_action == AOL_TOGGLE or self.settings.lkas_action == AOL_TOGGLE or
        (state.canValid and not state.canTimeout and (state.steerFaultPermanent or state.accFaulted)) or
        any(event.type == ButtonType.cancel and event.pressed for event in CS.buttonEvents) and
        (not self.cruise_lateral or any(self.settings.cancel_actions))):
      self.main_startup_pending = False
    if (self.cruise_lateral and self.settings.enabled and state.canValid and not state.canTimeout and
        not state.steerFaultPermanent and not state.accFaulted and available and self.main_neutral and
        not self.main_available and self.settings.main_action != AOL_TOGGLE and self.settings.lkas_action != AOL_TOGGLE):
      self.main_startup_pending = True
    default_main_edge = (available and (self.main_startup_pending or (self.main_neutral and not self.main_available)) and
                         self.settings.main_action != AOL_TOGGLE and self.settings.lkas_action != AOL_TOGGLE)
    if state.canValid and not state.canTimeout:
      if self.main_pulse:
        events.append(structs.CarState.ButtonEvent(type=ButtonType.mainCruise, pressed=False))
        self.main_pulse = False
      if not available:
        self.main_neutral = True
      elif self.main_seen and self.main_neutral and not self.main_available and self.settings.main_action == AOL_TOGGLE:
        events.append(structs.CarState.ButtonEvent(type=ButtonType.mainCruise, pressed=True))
        self.main_pulse = True
        self.main_neutral = False
      self.main_seen = True
      self.main_available = available
    else:
      self.main_neutral = False
      self.main_pulse = False
    state.buttonEvents = events
    super().update(state.as_reader(), **kwargs)
    if (default_main_edge and state.canValid and not state.canTimeout and self.settings.enabled and
        not state.steerFaultPermanent and not state.accFaulted and not self._fault_inhibit and
        (not self.cruise_lateral or not lateral_output_inhibited(self.cp, state))):
      self.allowed_latch = True
      if self.main_startup_pending and not self.startup_arm_ns:
        self.startup_arm_ns = now_ns
      self.main_startup_pending = False
      self.main_neutral = False
    if not available:
      if state.canValid and not state.canTimeout:
        self.main_startup_pending = False
      self.allowed_latch = False


def create_intent(cp, settings):
  return VolkswagenStockCardIntent(cp, settings)


def _eps_physical_state(cp, state):
  try:
    gears = structs.CarState.GearShifter
    return bool(qualified(cp, marked_only=True) and state is not None and state.canValid and not state.canTimeout and
      not state.steerFaultPermanent and state.cruiseState.available and not state.cruiseState.enabled and
      state.gearShifter in (gears.drive, gears.sport, gears.eco, gears.manumatic) and
      not state.brakePressed and not state.regenBraking and not state.gasPressed and not state.doorOpen and
      not state.seatbeltUnlatched and not state.accFaulted and not state.vehicleSensorsInvalid and
      not state.steeringDisengage and not state.buttonEvents)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False


def temporary_eps_restriction(cp, state):
  """Exact already armed LAT-only EPS withdrawal; never output permission."""
  return bool(_eps_physical_state(cp, state) and state.steerFaultTemporary)


def retain_eps_recovery(feedback, sm, cp, state, axis, now_ns, *, latched, producer):
  """Join only an older EPS denial to its verified physical recovery; grant no axis."""
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native
  try:
    last = int(getattr(feedback, 'volkswagen_eps_source_ns', 0))
    stamp = int(sm.logMonoTime['aolAxisState'])
    if (not latched or not _eps_physical_state(cp, state) or state.steerFaultTemporary or
        feedback.failed or feedback.paused or feedback.pending_event or feedback.retention_started_ns or
        not feedback.temporary_eps_event_ns or not feedback.lateral_only_baseline or
        not feedback.lateral_baseline_ns or not feedback.lateral_inventory or
        not producer or producer != feedback.availability_producer or
        axis.intentProducerSessionId != producer or axis.intentIntegrityEpoch != feedback.availability_epoch or
        not feedback.session or str(axis.sessionId) != feedback.session or axis.faultSessionId != axis.sessionId or
        str(axis.faultReason) != 'none' or str(axis.mode) not in ('off', 'lateralOnly') or
        axis.lateralActive or axis.desiredLongitudinal or axis.longitudinalActive or
        not 0 < last <= axis.sourceCarStateMonoTime <= stamp == axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime or
        now_ns - last > SAFETY_MAX_AGE_NS or now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
        axis.validUntilMonoTime - axis.observedMonoTime > AXIS_MAX_AGE_NS or
        not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents']):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    temporary = (log.OnroadEvent.EventName.steerTempUnavailable, log.OnroadEvent.EventName.steerTempUnavailableSilent)
    if (not 0 < feedback.temporary_eps_event_ns <= event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        any(event.immediateDisable or event.softDisable and event.name not in temporary or
            event.userDisable and not (cp.pcmCruise and event.name == log.OnroadEvent.EventName.pcmDisable)
            for event in sm['onroadEvents'])):
      return False
    native = _configured_transport_native(sm, cp, feedback.session, now_ns,
                                         require_longitudinal=False, allow_restricted_lateral=True)
    return bool(native is not None and native.pandaInventory and
      feedback.lateral_native_ns <= native.observedMonoTime <= last and
      not native.lateralAllowed and not native.requestedLongitudinal and not native.longitudinalAllowed and
      (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) == feedback.lateral_inventory)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False


def physical_cancel_lateral_allowed(cp, cs, intent, native, *, settings, previous,
                                    source_ns, now_ns, axis_session_id) -> bool:
  """Let the mapped intent owner apply Cancel while retaining a granted LAT axis."""
  from openpilot.starpilot.aol.intent import ButtonType
  from openpilot.starpilot.aol.runtime import (AXIS_MAX_AGE_NS, INTENT_MAX_AGE_NS, SAFETY_MAX_AGE_NS,
                                               native_inventory_matches_cp)
  try:
    return bool((stock_qualified(cp, marked_only=True) or long_qualified(cp, marked_only=True) or pq_qualified(cp, marked_only=True)) and
      settings is not None and settings.enabled and not any(settings.cancel_actions) and
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



def longitudinal_speed_event(cp, state):
  """A manual cruise speed minimum does not revoke eligible independent steering."""
  return bool(long_qualified(cp, marked_only=True) and cp.transmissionType == structs.CarParams.TransmissionType.manual and
              state is not None and state.canValid and not state.canTimeout and state.cruiseState.available and
              not state.steerFaultPermanent and not state.steerFaultTemporary and not state.accFaulted and
              state.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport,
                                   structs.CarState.GearShifter.eco, structs.CarState.GearShifter.manumatic) and
              not lateral_output_inhibited(cp, state) and 0 < cp.minEnableSpeed and
              abs(state.vEgo) <= cp.minEnableSpeed + 0.5)


def lateral_immediate_disable(cp, state, intent, events):
  from openpilot.cereal import log
  retained = bool(intent is not None and intent.allowedLatch and intent.lateralArmed and not intent.pauseLateral and
                  longitudinal_speed_event(cp, state))
  return any(event.immediateDisable and not (retained and event.name == log.OnroadEvent.EventName.speedTooLow)
             for event in events)


def intent_disarming_fault(cp, events, state, **context):
  from openpilot.cereal import log
  from openpilot.starpilot.car.gm.aol import intent_disarming_fault as classify
  if longitudinal_speed_event(cp, state):
    events = [event for event in events if event.name != log.OnroadEvent.EventName.speedTooLow]
  return classify(cp, events, state, **context)


def awaiting_initial_request(cp, cs, sm, native, feedback, intent, *, now_ns, producer):
  """A cold MAIN request may await its first native response, never output."""
  from openpilot.starpilot.aol.runtime import SAFETY_MAX_AGE_NS, AXIS_MAX_AGE_NS, current_axis
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native, cp_identity
  try:
    if not (stock_qualified(cp, marked_only=True) or long_qualified(cp, marked_only=True)):
      return False
    axis = current_axis(sm, now_ns=now_ns)
    arm = intent.startup_arm_ns
    event_ns = int(sm.logMonoTime['onroadEvents'])
    return bool(intent.settings.enabled and intent.settings.main_action != AOL_TOGGLE and intent.settings.lkas_action != AOL_TOGGLE and
      intent.allowed_latch and not intent.pause_lateral and not feedback.ever_lateral_active and
      not feedback.failed and not feedback.paused and not feedback.pending_event and not feedback.retention_started_ns and
      feedback.cp == cp_identity(cp) and
      0 < arm <= now_ns <= arm + SAFETY_MAX_AGE_NS and
      cs.canValid and not cs.canTimeout and cs.cruiseState.available and not lateral_output_inhibited(cp, cs) and
      not cs.accFaulted and not cs.vehicleSensorsInvalid and not cs.doorOpen and not cs.seatbeltUnlatched and
      not cs.steeringDisengage and not cs.brakePressed and not cs.regenBraking and not cs.buttonEvents and
      cs.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport,
                        structs.CarState.GearShifter.eco, structs.CarState.GearShifter.manumatic) and
      axis is not None and axis.sessionId == feedback.session and axis.faultSessionId == axis.sessionId and
      axis.sequence > 0 and axis.sequence >= feedback.sequence and feedback.signature is not None and
      feedback.signature[14:16] == (producer, int(axis.intentIntegrityEpoch)) and
      axis.observedMonoTime == int(sm.logMonoTime['aolAxisState']) and
      0 < axis.validUntilMonoTime - axis.observedMonoTime <= AXIS_MAX_AGE_NS and
      axis.faultReason == 'none' and not axis.desiredLongitudinal and
      not axis.lateralActive and not axis.longitudinalActive and
      axis.intentProducerSessionId == producer and producer and
      0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime <= now_ns and
      now_ns - axis.sourceCarStateMonoTime <= AXIS_MAX_AGE_NS and
      native is not None and not native.lateralAllowed and not native.requestedLongitudinal and
      not native.longitudinalAllowed and native.observedMonoTime <= now_ns and
      _configured_transport_native(sm, cp, axis.sessionId, now_ns, native,
        require_longitudinal=False, allow_restricted_lateral=True) is not None and
      sm.seen['onroadEvents'] and sm.valid['onroadEvents'] and sm.alive['onroadEvents'] and
      0 < event_ns <= now_ns and now_ns - event_ns <= 1_500_000_000 and
      not any(event.immediateDisable or event.softDisable or event.userDisable for event in sm['onroadEvents']))
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return False


def retain_reverse_withdrawal(feedback, sm, cp, cs, axis, now_ns, *, latched, producer):
  """Keep the original LAT intent while the Reverse OFF acknowledgment arrives."""
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import SAFETY_MAX_AGE_NS
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native
  try:
    if (not qualified(cp, marked_only=True) or not latched or feedback.failed or feedback.paused or feedback.pending_event or
        feedback.retention_started_ns or not feedback.lateral_only_baseline or not feedback.lateral_inventory or
        not producer or producer != feedback.availability_producer or axis.intentProducerSessionId != producer or
        axis.intentIntegrityEpoch != feedback.availability_epoch or axis.sessionId != feedback.session or
        axis.faultReason != 'none' or axis.mode != 'off' or axis.desiredLateral or axis.lateralActive or
        axis.desiredLongitudinal or axis.longitudinalActive or
        not 0 < feedback.lateral_source_ns <= feedback.lateral_baseline_ns <= now_ns or
        now_ns - feedback.lateral_source_ns > SAFETY_MAX_AGE_NS or
        cs.gearShifter not in (structs.CarState.GearShifter.reverse, structs.CarState.GearShifter.drive,
                              structs.CarState.GearShifter.sport, structs.CarState.GearShifter.eco, structs.CarState.GearShifter.manumatic) or
        not cs.canValid or cs.canTimeout or
        not cs.cruiseState.available or cs.steerFaultTemporary or cs.steerFaultPermanent or cs.accFaulted or
        cs.vehicleSensorsInvalid or cs.steeringDisengage or cs.gasPressed or cs.doorOpen or cs.buttonEvents):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        any(event.immediateDisable or
            event.softDisable and event.name != log.OnroadEvent.EventName.wrongGear or
            event.userDisable and event.name not in (log.OnroadEvent.EventName.reverseGear, log.OnroadEvent.EventName.pcmDisable)
            for event in sm['onroadEvents'])):
      return False
    native = _configured_transport_native(sm, cp, feedback.session, now_ns,
      require_longitudinal=False, allow_restricted_lateral=True)
    recovering = cs.gearShifter != structs.CarState.GearShifter.reverse
    reverse_ns = getattr(feedback, 'volkswagen_reverse_source_ns', 0)
    retained = bool(native is not None and native.pandaInventory and native.requestedLateral and
      (not recovering or 0 < native.observedMonoTime <= reverse_ns <= now_ns) and
      (not native.lateralAllowed or not recovering) and not native.requestedLongitudinal and not native.longitudinalAllowed and
      native.observedMonoTime >= feedback.lateral_native_ns and
      (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)) == feedback.lateral_inventory and
      all(not slot.controlsAllowed for slot in native.pandaInventory) and
      all(not panda.controlsAllowed for panda in sm['pandaStates']))
    if retained:
      feedback.volkswagen_reverse_retained_native_ns = int(native.observedMonoTime)
      if not recovering:
        feedback.volkswagen_reverse_source_ns = now_ns
    return retained
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return False
