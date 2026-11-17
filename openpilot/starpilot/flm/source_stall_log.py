"""Bounded optional-worker evidence for a stalled source bootstrap; no admission."""

from openpilot.starpilot.flm.source_observation_report import refusal_report

WAIT_NS = 5_000_000_000
INTERVAL_NS = 60_000_000_000


class SourceStallLog:
  def __init__(self, emit=None):
    self.emit = emit
    self.started_ns = self.last_ns = None

  def observe(self, collector, *, now_ns, bootstrap_allowed, context_error=None, sm=None):
    try:
      if type(now_ns) is not int or now_ns <= 0:
        return
      if self.started_ns is None or now_ns < self.started_ns:
        self.started_ns = now_ns
        self.last_ns = None
      if now_ns - self.started_ns < WAIT_NS or self.last_ns is not None and now_ns - self.last_ns < INTERVAL_NS:
        return
      # Reserve the slot before any optional observation or logging can fail.
      self.last_ns = now_ns
      capture = getattr(collector, 'original_source_capture', None)
      ready = () if collector is None else collector.ready
      command, output = ready[-1] if ready else (None, None)
      error = None if context_error is None else (type(context_error).__name__ + ': ' + str(context_error))[:192]
      phase = ('bootstrap_deferred' if not bootstrap_allowed else 'collector_unavailable' if collector is None else
               'current_pair_unavailable' if not ready else 'original_capture_unavailable' if capture is None else
               'original_pose_unavailable' if not capture.rows else 'source_context_rejected' if error else 'source_context_pending')
      current_receiver = None
      if sm is not None and collector is not None:
        try:
          from openpilot.starpilot.flm.source_observation_report import _snapshot
          current_receiver = _snapshot(collector, sm, now_ns, 'stall_checkpoint')
        except Exception:
          pass  # Optional evidence must never suppress the existing stall log.
      evidence = {
        'observedMonoTime': now_ns, 'phase': phase, 'contextError': error,
        'scope': 'optional learner receiver; not a Controls publisher rejection or authority receipt',
        'readyPairs': len(ready), 'currentNative': bool(getattr(collector, '_learning_current_native_allowed', False)),
        'controlMonoTime': None if command is None else command.control_ns,
        'applyMonoTime': None if output is None else output.apply_ns,
        'poses': 0 if capture is None else len(capture.rows),
        'captureReason': None if capture is None else str(capture.reason)[:128],
        'captureRejected': 0 if capture is None else capture.rejected,
        'captureError': getattr(getattr(collector, 'adapter', None), 'source_plant_capture_error', None),
        'receiverCurrent': current_receiver,
        'sourceObservation': None if collector is None else refusal_report(collector),
      }
      if self.emit is None:
        from openpilot.common.swaglog import cloudlog
        cloudlog.event('auto_learn_source_stall', error=True, **evidence)
      else:
        self.emit(evidence)
    except Exception:
      # Logging cannot change collection, rejection, authority or shutdown.
      pass


def log_source_stall(actor, collector, *, now_ns, bootstrap_allowed, context_error=None, sm=None):
  try:
    logger = getattr(actor, '_source_stall_log', None)
    if logger is None:
      logger = actor._source_stall_log = SourceStallLog()
    logger.observe(collector, now_ns=now_ns, bootstrap_allowed=bootstrap_allowed, context_error=context_error, sm=sm)
  except Exception:
    pass
