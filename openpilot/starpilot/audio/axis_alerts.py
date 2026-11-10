from openpilot.cereal import log
from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, current_axis, current_intent
from openpilot.starpilot.aol.wire import INTENT_SERVICE

AudibleAlert = log.SelfdriveState.AudibleAlert
COALESCE_NS = 250_000_000


class _Transition:
  def __init__(self):
    self.session = None
    self.sequence = 0
    self.active = False
    self.observed_at = 0

  def update(self, session, sequence: int, active: bool, now_ns: int) -> int:
    changed = 0
    if session is None:
      self.session = None
    elif session != self.session or now_ns - self.observed_at > COALESCE_NS:
      self.session, self.sequence, self.active, self.observed_at = session, sequence, active, now_ns
    elif sequence > self.sequence:
      changed = int(active) - int(self.active)
      self.sequence, self.active, self.observed_at = sequence, active, now_ns
    return changed


class _LateralTransition:
  """Announce a requested episode only after its first effective activation."""
  def __init__(self):
    self.session = None
    self.sequence = 0
    self.observed_at = 0
    self.requested = False
    self.active = False
    self.request_ns = 0
    self.announce = False
    self.axis_session = None
    self.axis_sequence = 0
    self.axis_source_ns = 0

  def update(self, intent, requested: bool, axis, now_ns: int) -> int:
    if intent is None:
      self.session = None
      return 0
    baseline = intent.producerSessionId != self.session or now_ns - self.observed_at > COALESCE_NS
    if not baseline and intent.sequence <= self.sequence:
      return 0
    if baseline:
      self.session = intent.producerSessionId
      self.requested, self.active = requested, False
      self.request_ns, self.announce = 0, False
    elif requested and not self.requested:
      self.request_ns, self.announce = intent.carStateLogMonoTime, True

    activated = False
    if axis is not None:
      restarted = self.axis_session is not None and axis.sessionId != self.axis_session
      if axis.sessionId != self.axis_session:
        self.axis_session, self.axis_sequence, self.axis_source_ns = axis.sessionId, 0, 0
      if restarted:
        # A restarted producer supplies a baseline, not a new driver gesture.
        self.announce = False
      if axis.sequence > self.axis_sequence:
        source_ns = int(axis.sourceCarStateMonoTime)
        source_current = (axis.intentProducerSessionId == intent.producerSessionId and
                          0 < source_ns <= axis.observedMonoTime <= now_ns and
                          now_ns - source_ns <= AXIS_MAX_AGE_NS)
        activated = bool(source_current and
                         source_ns >= max(self.request_ns, self.axis_source_ns) and
                         axis.nativeAcknowledged and axis.desiredLateral and axis.lateralActive)
        self.axis_sequence = axis.sequence
        if source_current:
          self.axis_source_ns = max(self.axis_source_ns, source_ns)

    changed = 0
    if not requested:
      changed = -int(self.active)
      self.active, self.announce = False, False
    elif activated and not self.active:
      changed = int(self.announce)
      self.active, self.announce = True, False
    # Temporary suppression does not end the accepted driver-intent episode.
    # Safety/refusal sounds remain owned by selfdriveState, not this chime latch.
    self.requested, self.sequence, self.observed_at = requested, intent.sequence, now_ns
    return changed


class AxisAlerts:
  def __init__(self):
    self.lateral = _LateralTransition()
    self.longitudinal = _Transition()
    self.last_sound = AudibleAlert.none
    self.last_sound_at = 0
    self.native_sound = AudibleAlert.none
    self.steering_session = None
    self.steering_warned = False
    self.steering_recovered_since = 0

  def temporary_steering_sound(self, sm, intent, native_sound, now_ns, state_fresh):
    if intent is None or not state_fresh:
      self.steering_recovered_since = 0
      return native_sound
    if intent.producerSessionId != self.steering_session or not intent.lateralArmed or intent.pauseLateral:
      self.steering_session = intent.producerSessionId
      self.steering_warned = False
      self.steering_recovered_since = 0
    temporary = sm['selfdriveState'].alertType == 'steerTempUnavailableSilent/warning'
    axis = current_axis(sm, now_ns=now_ns) if 'aolAxisState' in sm.logMonoTime else None
    # Brief recovery or a competing alert must not replay the same warning.
    # This clock affects audio only; fault events and steering gates stay owned upstream.
    if axis is not None and axis.nativeAcknowledged and axis.lateralActive and not temporary:
      if not self.steering_recovered_since:
        self.steering_recovered_since = now_ns
      elif now_ns - self.steering_recovered_since >= COALESCE_NS:
        self.steering_warned = False
    else:
      self.steering_recovered_since = 0
    if temporary and intent.lateralArmed and not intent.pauseLateral and native_sound == AudibleAlert.prompt:
      if self.steering_warned:
        return AudibleAlert.none
      self.steering_warned = True
    return native_sound

  def update(self, sm, native_sound: int, now_ns: int) -> int:
    intent = (current_intent(sm, car_state_ns=int(sm.logMonoTime[INTENT_SERVICE]), now_ns=now_ns)
              if INTENT_SERVICE in sm.logMonoTime else None)
    # AOL arming is not activation. Confirm its first active axis, then retain
    # that episode through temporary suppression without replaying engagement.
    state_ns = int(sm.logMonoTime.get('selfdriveState', 0))
    state_fresh = (state_ns > 0 and 0 <= now_ns - state_ns <= 100_000_000 and
                   sm.valid['selfdriveState'] and sm.alive['selfdriveState'])
    lateral_known = intent is not None and (intent.pauseLateral or intent.lateralArmed or state_fresh)
    axis = current_axis(sm, now_ns=now_ns) if 'aolAxisState' in sm.logMonoTime else None
    lateral = self.lateral.update(intent if lateral_known else None,
                                  bool(intent and not intent.pauseLateral and
                                       (intent.lateralArmed or (state_fresh and sm['selfdriveState'].active))), axis, now_ns)
    longitudinal = self.longitudinal.update(
      intent.producerSessionId if intent is not None and state_fresh else None,
      intent.sequence if intent is not None else 0,
      bool(intent is not None and state_fresh and sm['selfdriveState'].enabled and not intent.pauseLongitudinal), now_ns)
    extra_sound = (AudibleAlert.disengage if min(lateral, longitudinal) < 0 else
                   AudibleAlert.engage if max(lateral, longitudinal) > 0 else AudibleAlert.none)

    native_sound = self.temporary_steering_sound(sm, intent, native_sound, now_ns, state_fresh)

    if native_sound != AudibleAlert.none:
      if native_sound != self.native_sound and native_sound in (AudibleAlert.engage, AudibleAlert.disengage):
        self.last_sound, self.last_sound_at = native_sound, now_ns
      self.native_sound = native_sound
      return native_sound

    self.native_sound = native_sound
    if extra_sound != AudibleAlert.none:
      if extra_sound == self.last_sound and now_ns - self.last_sound_at < COALESCE_NS:
        return AudibleAlert.none
      self.last_sound, self.last_sound_at = extra_sound, now_ns
    return extra_sound
