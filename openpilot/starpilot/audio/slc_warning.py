"""One-shot SLC warning; a busy, muted or expired event is consumed, never queued."""

from openpilot.starpilot.common.slc_warning import WarningAudioGate


class SlcWarning:
  def __init__(self, started=None):
    self.gate = WarningAudioGate(started)

  def update(self, sm, now_ns, *, busy, enabled):
    try:
      value = sm['slcState']
      stamp = int(sm.logMonoTime['slcState'])
      event = {
        'id': str(value.changeWarningId),
        'issued': int(value.changeWarningIssuedMonoTime) / 1e9,
        'deadline': int(value.changeWarningDeadlineMonoTime) / 1e9,
      }
      fresh = bool(
        sm.valid['slcState']
        and sm.alive['slcState']
        and 0 < stamp <= now_ns <= stamp + 100_000_000
        and value.enabled
        and sm.valid['selfdriveState']
        and sm.alive['selfdriveState']
        and sm['selfdriveState'].enabled
      )
      return self.gate.consume(event, now_ns / 1e9, busy=busy, enabled=enabled, fresh=fresh)
    except (KeyError, AttributeError, TypeError, ValueError):
      return False
