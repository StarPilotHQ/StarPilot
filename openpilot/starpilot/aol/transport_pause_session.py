"""Session-scoped transport pause; no independent axis grant."""


def cp_identity(cp):
  return (str(cp.brand), str(cp.carFingerprint), int(cp.flags), int(cp.alternativeExperience),
    bool(cp.passive), bool(cp.dashcamOnly), bool(cp.notCar),
    bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
    tuple((int(c.safetyModel.raw), int(c.safetyParam)) for c in cp.safetyConfigs))


class TransportPause:
  def __init__(self, cp, session):
    from openpilot.starpilot.aol import transport_pause as transport
    self.cp = transport.cp_identity(cp)
    self.session = session
    self.active = False
    self.producer = ''
    self.epoch = None

  def observe(self, *, cp, session, sm, cs, native, now_ns, companion, previous_intent,
              intent, expired_healthy_source, previously_active, hard_fault, model_ready, user_disable):
    from openpilot.starpilot.aol import transport_pause as transport
    self.observed_native = None
    context_current = bool(transport.cp_identity(cp) == self.cp and session == self.session and model_ready and companion is not None)
    if context_current:
      self.observed_native = transport._healthy_transport_native(sm, cp, cs, session, now_ns, native)
      if self.observed_native is None and cp.brand == 'ford':
        from openpilot.starpilot.car.ford.gas_override import healthy_gas_transport_native
        self.observed_native = healthy_gas_transport_native(sm, cp, cs, session, now_ns)
    eligible = bool(context_current and not hard_fault and self.observed_native is not None)
    latest = companion.latest if companion is not None else None
    raw = transport.decode_intent(sm['aolIntentWire']) if sm.seen.get('aolIntentWire', False) else None
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


class AvailabilityPause:
  """Retain an accepted armed producer; this proof never enables output."""
  def __init__(self, cp, session):
    from openpilot.starpilot.aol import transport_pause as transport
    self.cp, self.session = transport.cp_identity(cp), session
    self.producer = ''
    self.epoch = None
    self.inventory = None
    self.native_ns = 0
    self.baseline_ns = 0
    self.active = False

  def observe(self, *, cp, session, sm, cs, native, now_ns, companion, intent,
              events, model_ready, source_current, prior_lateral_active, hard_fault, healthy_native, source_expired=False):
    from openpilot.starpilot.aol import transport_pause as transport
    try:
      healthy = healthy_native
      eligible = bool(transport.cp_identity(cp) == self.cp and
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
        sm_intent = transport.decode_intent(sm['aolIntentWire'])
        projection = bool(verified is not None and int(sm.logMonoTime['aolIntentWire']) == verified[0] and
          verified[2] == companion.integrity_epoch and verified[3] == companion.session and
          sm_intent == verified[1] and 0 < verified[0] <= now_ns <= verified[0] + transport.SAFETY_MAX_AGE_NS and
          sm_intent.settingsQualified and sm_intent.allowedLatch and sm_intent.lateralArmed and
          sm_intent.producerSessionId == intent.producerSessionId)
        if (raw is None or not raw.settingsQualified or not raw.allowedLatch or not raw.lateralArmed or
          raw.producerSessionId != intent.producerSessionId or
          not 0 < intent.carStateLogMonoTime <= now_ns or
          now_ns - intent.carStateLogMonoTime > transport.SAFETY_MAX_AGE_NS or
          not 0 < raw.carStateLogMonoTime <= now_ns or raw.observedMonoTime > now_ns or
          now_ns - raw.carStateLogMonoTime > transport.SAFETY_MAX_AGE_NS or
          (int(sm.logMonoTime['aolIntentWire']) != raw.carStateLogMonoTime and not projection) or
          not sm.seen['aolIntentWire'] or not sm.valid['aolIntentWire']):
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
      matched = bool(transport.availability_only(events) and self.producer == intent.producerSessionId and
        self.epoch == companion.integrity_epoch and self.inventory == inventory and
        healthy.observedMonoTime >= self.native_ns and self.baseline_ns > 0 and
        (self.active or 0 <= now_ns - self.baseline_ns <=
         (transport.SAFETY_MAX_AGE_NS if source_expired else transport.AXIS_MAX_AGE_NS)))
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
