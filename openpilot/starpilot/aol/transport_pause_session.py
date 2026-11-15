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
