from opendbc.car import structs
from opendbc.car.mazda.stock_aol import admitted, temporary_restriction
from openpilot.selfdrive.car.cruise import CRUISE_LONG_PRESS
from openpilot.starpilot.aol.intent import AolCardIntent, AOL_TOGGLE, ButtonType

# Panda withholds lateral while a temporary restriction holds (standstill: its
# vehicle_moving gate; a TI2 pause; not in Drive) and keeps its AOL token. Its
# status reaches the host late, and the host may judge a sample up to
# SAFETY_MAX_AGE_NS (200 ms) old. A denial seen just after the restriction ends was
# sampled while it held, so it is not a revocation. Past this grace a denial is
# real again.
RESTRICTION_RELEASE_GRACE_NS = 250_000_000


class MazdaStockCardIntent(AolCardIntent):
  observe_stock_engagement = True

  def __init__(self, cp, settings):
    super().__init__(settings, explicit_latch=True)
    self.cp = cp
    self.main_seen = False
    self.startup_main_pending = True
    self.startup_main_source_ns = 0
    self.startup_native_ready = False
    self.startup_binding = None
    self.restricted_ns = 0
    self.main_available = False
    self.main_neutral = False
    self.main_pulse = False
    self.cancel_held = False
    self.cancel_ticks = 0

  def observe_startup_readiness(self, sm, CS, *, native, panda_ready, now_ns, producer):
    from openpilot.starpilot.aol.intent import disarming_fault
    from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS
    from openpilot.starpilot.aol.transport_pause import _healthy_transport_native, availability_only

    self.startup_native_ready = False
    if not self.startup_main_pending or not admitted(self.cp, marked_only=True):
      return
    if CS.steerFaultPermanent or CS.accFaulted:
      self.startup_main_pending = False
      self.startup_main_source_ns = 0
      self.startup_binding = None
      return
    try:
      event_ns = int(sm.logMonoTime['onroadEvents'])
      events_current = bool(sm.seen['onroadEvents'] and sm.valid['onroadEvents'] and sm.alive['onroadEvents'] and
        0 < event_ns <= now_ns and now_ns - event_ns <= 1_500_000_000)
      events = sm['onroadEvents']
      faults = [event for event in events if disarming_fault([event], CS)]
      if events_current and faults and not availability_only(faults):
        self.startup_main_pending = False
        self.startup_main_source_ns = 0
        self.startup_binding = None
        return
      axis = sm['aolAxisState']
      native = (_healthy_transport_native(sm, self.cp, CS, str(axis.sessionId), now_ns, native)
                if native is not None else None)
      self.startup_native_ready = bool(events_current and panda_ready and native is not None and native.pandaInventory and
        not native.requestedLateral and not native.lateralAllowed and not native.requestedLongitudinal and not native.longitudinalAllowed and
        all(not slot.controlsAllowed for slot in native.pandaInventory) and
        sm.seen['aolAxisState'] and sm.valid['aolAxisState'] and sm.alive['aolAxisState'] and
        int(sm.logMonoTime['aolAxisState']) == axis.observedMonoTime and
        axis.validUntilMonoTime - axis.observedMonoTime <= AXIS_MAX_AGE_NS and
        axis.qualified and axis.nativeAcknowledged and str(axis.faultReason) == 'none' and
        str(axis.sessionId) == native.axisSessionId and producer and axis.intentProducerSessionId == producer and
        not axis.desiredLateral and not axis.desiredLongitudinal and not axis.lateralActive and not axis.longitudinalActive and
        0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime and
        now_ns - axis.sourceCarStateMonoTime <= AXIS_MAX_AGE_NS)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      self.startup_native_ready = False
    if self.startup_native_ready:
      binding = (str(axis.sessionId), producer, int(axis.intentIntegrityEpoch), native.pandaSerial,
                 tuple(slot.hardwareSerial for slot in native.pandaInventory))
      if binding != self.startup_binding:
        self.startup_main_source_ns = 0
        self.startup_binding = binding
    else:
      self.startup_binding = None
      self.startup_main_source_ns = 0

  def cancel_action(self, action):
    if action == AOL_TOGGLE and self.settings.enabled:
      self.allowed_latch = not self.allowed_latch
      if self.allowed_latch:
        self.pause_lateral = False
    else:
      self._perform(action)

  def update(self, CS, **kwargs):
    state = structs.CarState(**CS.to_dict())
    events = []
    for event in CS.buttonEvents:
      if event.type == ButtonType.cancel:
        if event.pressed:
          self.cancel_held = True
          self.cancel_ticks = 0
        else:
          if self.cancel_held and 0 < self.cancel_ticks < CRUISE_LONG_PRESS:
            self.cancel_action(self.settings.cancel_actions[0])
          self.cancel_held = False
          self.cancel_ticks = 0
      elif event.type in (ButtonType.mainCruise, ButtonType.lkas):
        action = self.settings.main_action if event.type == ButtonType.mainCruise else self.settings.lkas_action
        if action == AOL_TOGGLE:
          events.append(structs.CarState.ButtonEvent(**event.to_dict()))
        elif event.pressed:
          self._perform(action)
      else:
        events.append(structs.CarState.ButtonEvent(**event.to_dict()))
    available = bool(state.cruiseState.available)
    healthy = state.canValid and not state.canTimeout
    if healthy:
      if self.main_pulse:
        events.append(structs.CarState.ButtonEvent(type=ButtonType.mainCruise, pressed=False))
        self.main_pulse = False
      if not available:
        self.main_neutral = True
        if self.settings.enabled and not state.steerFaultPermanent and not state.accFaulted:
          # A fault may consume one-time startup eligibility. A healthy MAIN-off
          # observation lets the driver deliberately reauthorize with a fresh
          # MAIN-on edge; recovery alone never reopens independent arming.
          self.startup_main_pending = True
          self.startup_main_source_ns = 0
          self.startup_binding = None
      elif self.main_seen and self.main_neutral and not self.main_available:
        events.append(structs.CarState.ButtonEvent(type=ButtonType.mainCruise, pressed=True))
        self.main_pulse = True
        self.main_neutral = False
      self.main_seen = True
      self.main_available = available
    else:
      self.cancel_held = False
      self.cancel_ticks = 0
      self.main_neutral = False
    startup_main = False
    if self.startup_main_pending and healthy:
      eligible = bool(available and not state.cruiseState.enabled and self.settings.enabled and
        self.main_action != AOL_TOGGLE and self.lkas_action != AOL_TOGGLE and
        not state.steerFaultPermanent and not state.accFaulted and
        not (self.cancel_held and any(self.settings.cancel_actions)))
      source_ns = kwargs.get('now_ns', 0)
      if not eligible:
        if available or not self.settings.enabled or state.steerFaultPermanent or state.accFaulted:
          self.startup_main_pending = False
          self.startup_main_source_ns = 0
          self.startup_binding = None
      elif self.startup_native_ready and kwargs.get('fault_active'):
        self.startup_main_pending = False
        self.startup_main_source_ns = 0
        self.startup_binding = None
      elif self.startup_native_ready and self.startup_main_source_ns and source_ns > self.startup_main_source_ns:
        # The first valid sample initializes the transport feedback pipeline.
        # Confirm MAIN on a subsequent fresh sample before admitting startup intent.
        self.startup_main_pending = False
        startup_main = True
      elif self.startup_native_ready and source_ns > 0:
        self.startup_main_source_ns = source_ns
    state.buttonEvents = events
    now_ns = kwargs.get('now_ns', 0)
    if getattr(self.cp, 'brand', None) == 'mazda' and temporary_restriction(self.cp, state):
      self.restricted_ns = now_ns
    elif (kwargs.get('native_rejection_ns', 0) and self.restricted_ns and
          0 <= now_ns - self.restricted_ns <= RESTRICTION_RELEASE_GRACE_NS):
      kwargs['native_rejection_ns'] = 0
    super().update(state.as_reader(), **kwargs)
    if startup_main and not self._fault_inhibit:
      self.allowed_latch = True
      self._last_latch_edge_ns = kwargs.get('now_ns', 0)
    if not available:
      self.allowed_latch = False
    if self.cancel_held:
      self.cancel_ticks += 1
      if self.cancel_ticks == CRUISE_LONG_PRESS:
        self.cancel_action(self.settings.cancel_actions[1])
      elif self.cancel_ticks == CRUISE_LONG_PRESS * 5:
        self.cancel_action(self.settings.cancel_actions[2])
