"""Initial response-only export, independent of old group-fit cadence.

Slot counts are availability, never an identification/admission declaration.
The child still verifies the complete authentic prefix/native/source experiment.
"""
from typing import cast


import base64
from dataclasses import asdict

from openpilot.starpilot.flm.live_tracking_bootstrap import VERSION


def _epoch_checkpoints(checkpoints, poses, cutoff_ns):
  """Retain the original first-pose anchor and every checkpoint in its epoch."""
  from openpilot.starpilot.flm.controller_replay import Snapshot
  from openpilot.starpilot.flm.original_source_capture import OriginalSourcePose

  if (
    type(checkpoints) is not tuple
    or len(checkpoints) > 600
    or type(poses) is not tuple
    or not poses
    or type(poses[0]) is not OriginalSourcePose
    or type(poses[0].mono_ns) is not int
    or poses[0].mono_ns <= 0
    or type(cutoff_ns) is not int
    or cutoff_ns <= poses[0].mono_ns
    or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not int or row[0] <= 0 or type(row[1]) is not Snapshot for row in checkpoints)
  ):
    raise ValueError('genuine bounded epoch checkpoint metadata')
  stamps = tuple(stamp for stamp, _ in checkpoints)
  if any(right <= left for left, right in zip(stamps, stamps[1:], strict=False)):
    raise ValueError('original checkpoint order')
  preceding = tuple(stamp for stamp in stamps if stamp <= poses[0].mono_ns)
  if not preceding:
    return ()
  origin = preceding[-1]
  return tuple(row for row in checkpoints if origin <= row[0] < cutoff_ns)


def _epoch_rows(rows, kind, stamp_name, start_ns, cutoff_ns, *, order_name=None):
  """A fixed epoch view, preserving every original row and validating history."""
  if (
    type(rows) is not tuple
    or len(rows) > 60000
    or type(start_ns) is not int
    or type(cutoff_ns) is not int
    or not 0 < start_ns < cutoff_ns
    or any(type(row) is not kind for row in rows)
  ):
    raise ValueError('bounded original epoch source metadata')
  stamps = tuple(getattr(row, stamp_name) for row in rows)
  if any(type(stamp) is not int or stamp <= 0 for stamp in stamps):
    raise ValueError('ordered original epoch source metadata')
  if order_name is None:
    ordered = stamps
  else:
    from openpilot.starpilot.flm.torque_emission_replay import Emission

    if kind is not Emission or stamp_name != 'control_ns' or order_name != 'apply_ns':
      raise ValueError('only actual emissions have separate application ordering')
    ordered = tuple(row.apply_ns for row in rows)
    if any(right < left for left, right in zip(stamps, stamps[1:], strict=False)):
      raise ValueError('original emitted command clock regression')
  if any(type(stamp) is not int or stamp <= 0 for stamp in (*stamps, *ordered)) or any(
    right <= left for left, right in zip(ordered, ordered[1:], strict=False)
  ):
    raise ValueError('ordered original epoch source metadata')
  return tuple(row for row, stamp in zip(rows, stamps, strict=True) if start_ns <= stamp < cutoff_ns)


def response_snapshot(collector, binding, now_ns):
  from openpilot.starpilot.flm.controller_replay import pack_primitive_snapshot, unpack_primitive_snapshot
  from openpilot.starpilot.flm.source_plant_fit import SOURCE_COMPLETE_SLOT_POLICY as BOOTSTRAP_SLOT_POLICY
  from openpilot.starpilot.flm.source_plant_runtime import _current_fit_pair_reason, _snapshot_from_packet

  adapter, capture = collector.adapter, collector.original_source_capture
  binding.validate()
  readiness = {
    'reason': 'raw_prerequisites_unavailable',
    'observedMonoTime': now_ns,
    'rawPoses': 0 if capture is None else len(capture.rows),
    'readyPairs': len(collector.ready),
    'captureEpoch': None if capture is None else capture.epoch,
    'captureStartNs': None if capture is None else capture.epoch_start_ns,
    'captureCutoffNs': None if capture is None else capture.epoch_cutoff_ns,
  }
  collector._live_bootstrap_readiness = readiness
  if capture is None or not capture.rows or not collector.ready or adapter.envelope is None:
    return None
  current_decline = _current_fit_pair_reason(collector, now_ns, physical_binding=binding)
  if current_decline is not None:
    readiness['reason'] = current_decline
    return None
  from openpilot.starpilot.flm.live_tracking_bootstrap_cohorts import (
    VERSION as COHORT_VERSION,
    CHECK_INTERVAL_NS,
    select_first_cohort,
    source_quality_keys,
    new_schedule_cache,
    _literal,
  )
  import pickle

  # Bound OPTIONAL scheduling work to1Hz, never the100Hz control loop. A memo
  # retains examined anchors only under fresh exact append-only source values.
  if getattr(collector, '_live_bootstrap_selection_epoch', None) == capture.epoch and now_ns < getattr(collector, '_live_bootstrap_selection_next_ns', 0):
    previous = getattr(collector, '_live_bootstrap_last_selection', None)
    if type(previous) is dict:
      readiness['previousSelection'] = previous.copy()
    readiness['reason'] = 'selection_throttled'
    readiness['throttled'] = True
    return None
  collector._live_bootstrap_selection_epoch = capture.epoch
  collector._live_bootstrap_selection_next_ns = now_ns + CHECK_INTERVAL_NS
  retained_checkpoints = tuple(adapter.replay.checkpoints)
  checkpoints = _epoch_checkpoints(retained_checkpoints, tuple(capture.rows), capture.epoch_cutoff_ns)
  readiness['retainedCheckpoints'] = len(retained_checkpoints)
  # Before a fixed cohort can exist, rebuilding/pickling the entire archive
  # cannot produce a job. Use only the original checkpoint and pose clocks to
  # defer that optional work; admission and every full quality check stay below.
  from openpilot.starpilot.flm.controller_replay import Snapshot
  from openpilot.starpilot.flm.original_source_capture import OriginalSourcePose
  from openpilot.starpilot.flm.source_plant_fit import FIRST_SLOT_NS, BLOCK_NS, PURGE_NS

  duration_ns = FIRST_SLOT_NS + 7 * (BLOCK_NS + PURGE_NS) + BLOCK_NS
  if (
    0 < len(checkpoints) <= 128
    and all(type(row) is tuple and len(row) == 2 and type(row[0]) is int and row[0] > 0 and type(row[1]) is Snapshot for row in checkpoints)
    and all(right[0] > left[0] for left, right in zip(checkpoints, checkpoints[1:], strict=False))
    and type(capture.rows[0]) is OriginalSourcePose
    and type(capture.rows[-1]) is OriginalSourcePose
  ):
    preceding = tuple(stamp for stamp, _ in checkpoints if stamp <= capture.rows[0].mono_ns)
    if preceding:
      earliest_end_ns = preceding[-1] + duration_ns
      if now_ns < earliest_end_ns or capture.rows[-1].source_lower_ns < earliest_end_ns:
        readiness.update(
          reason='fixed_cohort_clock_incomplete',
          requiredCohortNs=duration_ns,
          earliestCompleteNs=earliest_end_ns,
          checkpoints=len(checkpoints),
          throttled=False,
        )
        collector._live_bootstrap_last_selection = readiness.copy()
        return None
  if not checkpoints:
    readiness['reason'] = 'original_epoch_checkpoint_unavailable'
    return None
  # The recorder serves all objectives for ten minutes. This response job is
  # one original100s epoch, including its preceding genuine checkpoint. Old
  # history must not consume that epoch's unchanged12000-row source budget.
  from openpilot.starpilot.flm.controller_replay import ReplayFrame
  from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
  from openpilot.starpilot.flm.torque_emission_replay import Emission

  origin_ns = checkpoints[0][0]
  frames = _epoch_rows(tuple(adapter.replay.records), ReplayFrame, 'control_ns', origin_ns, capture.epoch_cutoff_ns)
  poses = tuple(capture.rows)
  emissions = _epoch_rows(tuple(adapter.replay.emissions.events.values()), Emission, 'control_ns', origin_ns, capture.epoch_cutoff_ns, order_name='apply_ns')
  controls = _epoch_rows(tuple(adapter.original_context_receipts.controls.values()), ControlEvidence, 'control_ns', origin_ns, capture.epoch_cutoff_ns)
  readiness.update(
    {
      'reason': 'checking_fresh_source_keys',
      'checkpoints': len(checkpoints),
      'frames': len(frames),
      'emissions': len(emissions),
      'controls': len(controls),
      'throttled': False,
    }
  )
  collector._live_bootstrap_last_selection = readiness.copy()
  try:
    keys = source_quality_keys(frames, poses, emissions, checkpoints, controls, diagnostics=readiness)
  finally:
    collector._live_bootstrap_last_selection = readiness.copy()
  token = pickle.dumps(_literal((capture.epoch, capture.epoch_start_ns, capture.epoch_cutoff_ns, tuple(vars(binding).items()))), protocol=5)
  # Feedback is original source input, not a fit outcome. Select the first
  # complete source experiment instead of repeatedly exporting an early prefix
  # with missing consumption receipts. The child still verifies every receipt.
  feedback = adapter.original_feedback_archive
  if feedback is None or feedback.failed:
    readiness['reason'] = 'original_feedback_source_unavailable'
    return None
  frame_stamps = tuple(frame.control_ns for frame in frames)
  from bisect import bisect_left

  unavailable = []
  for stamp, _ in checkpoints:
    crossing = bisect_left(frame_stamps, stamp + duration_ns)
    first = bisect_left(frame_stamps, stamp)
    if crossing >= len(frames) or first >= len(frames):
      continue
    source_ids = frame_stamps[first : crossing + 1]
    if (
      feedback.poison_through_ns
      and source_ids[0] <= feedback.poison_through_ns + 100_000_000
      or any(source_ids[0] - 100_000_000 <= bad <= source_ids[-1] + 100_000_000 for bad in feedback.failures)
      or any(identity not in feedback.consumptions for identity in source_ids)
    ):
      unavailable.append(stamp)
  unavailable = tuple(unavailable)
  token = pickle.dumps((token, unavailable), protocol=5)
  cache = new_schedule_cache(getattr(collector, '_live_bootstrap_cohort_cache', None), token, keys)
  collector._live_bootstrap_cohort_cache = cache
  selection = select_first_cohort(
    checkpoints=checkpoints,
    frames=frames,
    poses=poses,
    emissions=emissions,
    controls=controls,
    binding=binding,
    epoch=capture.epoch,
    epoch_start_ns=capture.epoch_start_ns,
    cutoff_ns=capture.epoch_cutoff_ns,
    now_ns=now_ns,
    cache=cache,
    diagnostics=readiness,
    unavailable_anchors=unavailable,
    source_complete=True,
  )
  collector._live_bootstrap_last_selection = readiness.copy()
  if selection is None:
    return None
  start = selection['start_ns']
  # Freeze the already selected experiment. Later frames belong to live
  # collection, not this response-only job, and may contain unrelated gaps.
  end_frame = next((frame for frame in frames if frame.control_ns >= selection['end_ns']), None)
  records = () if end_frame is None else tuple(frame for frame in frames if start <= frame.control_ns <= end_frame.control_ns)
  if not records or len(records) > 12000 or records[-1].control_ns < selection['end_ns']:
    readiness['reason'] = 'selected_original_frame_coverage_unavailable'
    return None
  # Keep the actual currently selected factory/objective/FullSurfaceEnvelope.
  # A response-only job never rotates the capture selector or invents an
  # objective in order to bypass the original factory/native checks.
  schedule = {'version': 'live-response-bootstrap-source-v1', 'objective_key': adapter.key}
  wire = adapter.original_source_packet(collector, ((start, records[-1].control_ns),), now_ns)
  wrapper = _snapshot_from_packet(collector, wire, now_ns, schedule, response_only=True)
  if wrapper is None:
    readiness['reason'] = 'original_response_packet_wrapper_unavailable'
    return None
  payload = unpack_primitive_snapshot(base64.b64decode(wrapper['snapshot_b64'], validate=True))
  payload['live_job_version'] = VERSION
  payload['live_slot_policy'] = BOOTSTRAP_SLOT_POLICY
  payload['live_binding'] = asdict(binding)
  payload['live_cohort_version'] = COHORT_VERSION
  payload['live_cohort_selection'] = selection
  packed = pack_primitive_snapshot(payload)
  result = dict(wrapper, live_job_version=VERSION, snapshot_b64=base64.b64encode(packed).decode('ascii'))
  if len(result['snapshot_b64']) > 6 * 1024 * 1024:
    readiness['reason'] = 'response_transport_bound_exceeded'
    raise ValueError('complete response bootstrap transport bound')
  readiness['reason'] = 'response_snapshot_ready_not_child_qualified'
  return result


class ResponseSnapshotProcess:
  """Prepare immutable historical input off the live ingestion loop.

  Linux copy-on-write captures a single-threaded estimator's exact objects.
  The child never reads IPC or grants authority; its bounded JSON output still
  goes through the original independent response worker and current admission.
  """

  def __init__(self):
    self.pid = None
    self.fd = None
    self.token = None
    self.epoch = None
    self.started = self.last_started = 0
    self.output = bytearray()
    self.reason = 'idle'
    self.canceled = False

  def submit(self, collector, binding, *, token, now_ns):
    if self.pid is not None or now_ns - self.last_started < 5_000_000_000:
      return False
    capture = collector.original_source_capture
    if capture is None or not capture.rows:
      return False
    from openpilot.starpilot.flm.source_plant_fit import FIRST_SLOT_NS, BLOCK_NS, PURGE_NS

    duration = FIRST_SLOT_NS + 7 * (BLOCK_NS + PURGE_NS) + BLOCK_NS
    if capture.rows[-1].source_lower_ns - capture.rows[0].mono_ns < duration:
      return False

    def prepare():
      snapshot = response_snapshot(collector, binding, now_ns)
      return {'snapshot': snapshot, 'readiness': getattr(collector, '_live_bootstrap_readiness', None)}

    return self.submit_preparation(prepare, token=token, epoch=capture.epoch, now_ns=now_ns)

  def submit_preparation(self, prepare, *, token, epoch, now_ns):
    """One frozen optional estimator job; never an IPC or authority reader."""
    import os

    if self.pid is not None or not callable(prepare):
      return False
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    parent = os.getpid()
    try:
      pid = os.fork()
    except OSError:
      os.close(read_fd)
      os.close(write_fd)
      self.reason = 'snapshot_child_unavailable'
      self.last_started = now_ns
      return False
    if pid == 0:
      import ctypes
      import json
      import resource
      import signal

      try:
        os.close(read_fd)
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
          os._exit(1)
        os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
        os.nice(10)
        soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        limit = min([30] + [x for x in (soft, hard) if x >= 0])
        resource.setrlimit(resource.RLIMIT_CPU, (limit, hard))
        try:
          result = prepare()
        except Exception as error:
          result = {'snapshot': None, 'error': type(error).__name__ + ': ' + str(error)[:512]}
        payload = json.dumps(result, separators=(',', ':'), allow_nan=False).encode()
        if len(payload) > 8 * 1024 * 1024:
          os._exit(1)
        position = 0
        while position < len(payload):
          position += os.write(write_fd, payload[position : position + 65536])
        os.close(write_fd)
        os._exit(0)
      except BaseException:
        os._exit(1)
    os.close(write_fd)
    os.set_blocking(read_fd, False)
    self.pid, self.fd, self.token, self.epoch = pid, read_fd, token, epoch
    self.started = self.last_started = now_ns
    self.output = bytearray()
    self.canceled = False
    self.reason = 'preparing_historical_snapshot'
    return True

  def cancel(self, reason):
    import os
    import signal

    if self.pid is not None:
      try:
        os.kill(self.pid, signal.SIGKILL)
      except ProcessLookupError:
        pass
    self.reason = reason
    self.canceled = True

  def poll(self, *, token, now_ns):
    import os
    import json

    if self.pid is None:
      return None
    if token != self.token or now_ns - self.started > 30_000_000_000:
      self.cancel('snapshot_context_changed' if token != self.token else 'snapshot_wall_limit')
    eof = False
    for _ in range(16):
      try:
        part = os.read(cast(int, self.fd), 65536)
      except BlockingIOError:
        break
      if not part:
        eof = True
        break
      self.output.extend(part)
      if len(self.output) > 8 * 1024 * 1024:
        self.cancel('snapshot_output_bound')
        break
    pid, status = os.waitpid(self.pid, os.WNOHANG)
    if not pid:
      return None
    # Drain the bounded remainder after exit; never accept partial output.
    while not eof and len(self.output) <= 8 * 1024 * 1024:
      try:
        part = os.read(cast(int, self.fd), 65536)
      except BlockingIOError:
        break
      if not part:
        eof = True
        break
      self.output.extend(part)
    accepted = (
      not self.canceled
      and token == self.token
      and now_ns - self.started <= 30_000_000_000
      and os.waitstatus_to_exitcode(status) == 0
      and eof
      and len(self.output) <= 8 * 1024 * 1024
    )
    result = None
    if accepted:
      try:
        result = json.loads(self.output)
      except (ValueError, UnicodeError):
        pass
    os.close(cast(int, self.fd))
    self.pid = self.fd = None
    self.output.clear()
    return result

  def close(self, reason='snapshot_closed'):
    import os

    self.cancel(reason)
    if self.pid is not None:
      os.waitpid(self.pid, 0)
      os.close(cast(int, self.fd))
      self.pid = self.fd = None
      self.output.clear()
    return True
