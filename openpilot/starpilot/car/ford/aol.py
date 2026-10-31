from dataclasses import replace
from opendbc.car import structs
from opendbc.car.ford.aol import qualified, AOL_WORDS, MANUAL, temporary_restriction, aol_safety_config, profile_word
from openpilot.starpilot.aol.intent import AolCardIntent, AOL_TOGGLE
from openpilot.starpilot.aol.policy import AolVehiclePolicy


def policy_for(cp):
  if qualified(cp):
    return AolVehiclePolicy(intent_supported=True, settings_supported=True, runtime_supported=True,
                            normal_runtime_supported=True, ordinary_axis_ack_required=qualified(cp, marked_only=True),
                            explicit_latch=True, alternative_experience_addition=32,
                            safety_param_addition=MANUAL if cp.transmissionType == structs.CarParams.TransmissionType.manual else 0)
  return AolVehiclePolicy()


def native_profile_supported(model, param):
  return model == int(structs.CarParams.SafetyModel.ford) and profile_word(param) in AOL_WORDS


def native_accepts_cp(cp, model, param):
  return qualified(cp, marked_only=True) and native_profile_supported(model, param) and param == aol_safety_config(cp).safetyParam


def native_latch_rejected(cp, native):
  return qualified(cp, marked_only=True) and native.requestedLateral and not native.lateralAllowed


def ordinary_axis_request_allowed(cp, cs):
  return qualified(cp, marked_only=True) and cs.canValid and not cs.canTimeout and not cs.steerFaultPermanent


def retain_on_native_denial(cp, native, state):
  return temporary_restriction(cp, state)


class FordCardIntent(AolCardIntent):
  observe_stock_engagement = True
  def __init__(self, cp, settings):
    self.cp = cp
    self.main_seen = False
    self.main_available = False
    self.main_neutral = False
    self.main_pulse = False
    self.main_pending_ns = 0
    self.main_idle_transition_qualified = False
    effective = settings
    self.auto_main = settings.lkas_action != AOL_TOGGLE and settings.main_action != AOL_TOGGLE
    if self.auto_main:
      effective = replace(settings, main_action=AOL_TOGGLE)
    super().__init__(effective, explicit_latch=True)

  def observe_startup_readiness(self, sm, state, *, native, panda_ready, now_ns, producer):
    """Qualify only the older, negotiated MAIN-off response at a new MAIN edge."""
    from openpilot.cereal import log
    from openpilot.starpilot.aol.transport_pause import _healthy_transport_native
    self.main_idle_transition_qualified = False
    if not (self.main_seen and not self.main_available and self.main_neutral and state.cruiseState.available):
      return
    try:
      if (not qualified(self.cp, marked_only=True) or not panda_ready or
          not state.canValid or state.canTimeout or state.steerFaultTemporary or state.steerFaultPermanent or
          state.accFaulted or state.doorOpen or state.vehicleSensorsInvalid or state.steeringDisengage or
          state.gearShifter != structs.CarState.GearShifter.drive or not producer or
          not sm.seen['aolAxisState'] or not sm.valid['aolAxisState'] or not sm.alive['aolAxisState']):
        return
      axis = sm['aolAxisState']
      stamp = int(sm.logMonoTime['aolAxisState'])
      if (not axis.qualified or not axis.sessionId or axis.faultSessionId != axis.sessionId or axis.sequence <= 0 or
          axis.intentProducerSessionId != producer or stamp != axis.observedMonoTime or
          not 0 < axis.sourceCarStateMonoTime <= stamp < now_ns <= axis.validUntilMonoTime or
          now_ns - axis.sourceCarStateMonoTime > 30_000_000 or axis.validUntilMonoTime - stamp > 30_000_000 or
          str(axis.faultReason) != 'none' or str(axis.mode) != 'off' or axis.nativeAcknowledged or
          axis.desiredLateral or axis.desiredLongitudinal or axis.lateralActive or axis.longitudinalActive or
          not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents']):
        return
      event_ns = int(sm.logMonoTime['onroadEvents'])
      if not 0 < event_ns <= stamp or now_ns - event_ns > 1_500_000_000:
        return
      main_off = False
      for event in sm['onroadEvents']:
        if event.immediateDisable or event.softDisable:
          return
        main_off |= event.name == log.OnroadEvent.EventName.wrongCarMode and event.userDisable
        if event.userDisable and not (event.name in (log.OnroadEvent.EventName.wrongCarMode, log.OnroadEvent.EventName.pcmDisable) or
            event.name == log.OnroadEvent.EventName.pedalPressed and (state.brakePressed or state.regenBraking)):
          return
      current = _healthy_transport_native(sm, self.cp, state, str(axis.sessionId), now_ns)
      self.main_idle_transition_qualified = bool(main_off and native is not None and current is not None and current.pandaInventory and
        not current.requestedLateral and not current.lateralAllowed and not current.requestedLongitudinal and not current.longitudinalAllowed)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
      self.main_idle_transition_qualified = False

  def update(self, CS, **kwargs):
    state = structs.CarState(**CS.to_dict())
    events = [structs.CarState.ButtonEvent(**event.to_dict()) for event in CS.buttonEvents]
    available = bool(state.cruiseState.available)
    healthy = state.canValid and not state.canTimeout
    now_ns = kwargs.get('now_ns', 0)
    pending_ns, self.main_pending_ns = self.main_pending_ns, 0
    prior_idle_ok, self.main_idle_transition_qualified = self.main_idle_transition_qualified, False
    cancelled = any(event.type == structs.CarState.ButtonEvent.Type.cancel for event in events)
    if healthy:
      if self.main_pulse:
        events.append(structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.mainCruise, pressed=False))
        self.main_pulse = False
      if (pending_ns and 0 < now_ns - pending_ns <= 30_000_000 and available and
          self.settings.enabled and not cancelled and not state.steerFaultPermanent and not state.accFaulted):
        events.append(structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.mainCruise, pressed=True))
        self.main_pulse = True
      if not self.main_seen or available != self.main_available:
        if not available:
          self.main_neutral = True
        elif (self.main_neutral and not cancelled and self.settings.enabled and
              not state.steerFaultPermanent and not state.accFaulted and
              (kwargs.get('fault_active') is not True or prior_idle_ok)):
          if now_ns > 0:
            # Let the prior MAIN-off response clear before this synthetic pulse.
            # Deliver once on the next healthy frame; never renew the original
            # input window or Panda's independently timed physical MAIN token.
            self.main_pending_ns = now_ns
          else:
            events.append(structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.mainCruise, pressed=True))
            self.main_pulse = True
      self.main_seen = True
      self.main_available = available
    if self.auto_main and not available:
      self.allowed_latch = False
    if not any(self.settings.cancel_actions):
      # Unassigned CANCEL belongs to cruise. Keep it in the published CarState
      # and ordinary event path, but it is not a separate lateral toggle.
      events = [event for event in events if event.type != structs.CarState.ButtonEvent.Type.cancel]
    state.buttonEvents = events
    if self.auto_main and self.main_pulse:
      # Automatic MAIN-on arms AOL even if ordinary cruise engaged first.
      kwargs['standard_enabled'] = False
    super().update(state.as_reader(), **kwargs)


def create_intent(cp, settings):
  return FordCardIntent(cp, settings)


def temporary_eps_restriction(cp, state):
  """Ford physical EPS pause after LONG is off; intent only, no native permission."""
  try:
    return bool(qualified(cp, marked_only=True) and
      state.steerFaultTemporary and not state.steerFaultPermanent and state.canValid and not state.canTimeout and
      state.cruiseState.available and not state.cruiseState.enabled and
      state.gearShifter == structs.CarState.GearShifter.drive and
      not state.regenBraking and
      not state.doorOpen and not state.seatbeltUnlatched and not state.accFaulted and
      not state.vehicleSensorsInvalid and not state.steeringDisengage and not state.buttonEvents)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False


def temporary_eps_pedal_recovery(cp, state):
  """Retain only existing LAT intent through EPS recovery with a pedal held."""
  try:
    return bool(qualified(cp, marked_only=True) and
      (state.brakePressed or state.gasPressed) and not state.steerFaultTemporary and not state.steerFaultPermanent and
      state.canValid and not state.canTimeout and state.cruiseState.available and not state.cruiseState.enabled and
      state.gearShifter == structs.CarState.GearShifter.drive and not state.regenBraking and
      not state.doorOpen and not state.seatbeltUnlatched and not state.accFaulted and
      not state.vehicleSensorsInvalid and not state.steeringDisengage and not state.buttonEvents)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False


def physical_cancel_lateral_allowed(cp, cs, intent, native, *, settings, previous,
                                    source_ns, now_ns, axis_session_id) -> bool:
  """Cruise CANCEL may retain only an already active, independently granted LAT axis."""
  from openpilot.starpilot.aol.intent import ButtonType
  from openpilot.starpilot.aol.runtime import (AXIS_MAX_AGE_NS, INTENT_MAX_AGE_NS, SAFETY_MAX_AGE_NS,
                                               native_inventory_matches_cp)
  try:
    return bool(qualified(cp, marked_only=True) and settings is not None and settings.enabled and not any(settings.cancel_actions) and
      previous is not None and previous.lateral_active and previous.native_acknowledged and
      intent is not None and intent.settingsQualified and intent.allowedLatch and intent.lateralArmed and
      not intent.pauseLateral and intent.producerSessionId and intent.carStateLogMonoTime == source_ns and
      0 < source_ns <= intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime and
      now_ns - source_ns <= AXIS_MAX_AGE_NS and now_ns - intent.observedMonoTime <= INTENT_MAX_AGE_NS and
      intent.validUntilMonoTime - intent.observedMonoTime <= SAFETY_MAX_AGE_NS and
      cs.canValid and not cs.canTimeout and cs.cruiseState.available and not cs.steerFaultTemporary and
      not cs.steerFaultPermanent and not cs.accFaulted and not cs.doorOpen and not cs.seatbeltUnlatched and
      not cs.vehicleSensorsInvalid and cs.gearShifter == structs.CarState.GearShifter.drive and
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
