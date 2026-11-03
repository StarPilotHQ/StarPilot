"""Response bootstrap and live observation actor; no Controls or native owner.

No production bootstrap policy is inferred from the diagnostic180-second limit.
The daemon supplies a separately qualified process, otherwise collection holds.
"""

from dataclasses import replace
from typing import cast

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, full_profile_sha256
from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding
from openpilot.starpilot.flm.live_tracking_ingress import LiveTrackingIngress
from openpilot.starpilot.flm.live_tracking_response import certificate_from_dict, validate_certificate, validate_response_witness


def _original_probe_history(collector, ingress, control):
  """Native replay context is independent of scoring admission/warmup."""
  history = ingress.control_history
  if ingress.episodes.source_tracking:
    from itertools import islice
    from openpilot.starpilot.flm.original_context_receipts import OriginalContextReceipts

    context = getattr(collector.adapter, 'original_context_receipts', None)
    if context is not None:
      if type(context) is not OriginalContextReceipts or context.failed:
        raise ValueError('original probe context unavailable')
      # Existing original observations only. Bound the scan, including newer
      # lagged-response frames, and keep the same301-row checkpoint history.
      rows = tuple(row for row in islice(reversed(context.controls.values()), 402) if row.control_ns <= control.control_ns)
      return tuple(reversed(rows[:301]))
  return tuple(row for row in history if row.control_ns <= control.control_ns)


def _observed_probe_candidates(
  ready,
  prefix,
  points,
  *,
  after_ns,
  verify_limited=None,
  checkpoint_indices=None,
  stratified=False,
  same_checkpoint=False,
  phase_windows=(),
  phase_fallback_after_ns=None,
  fresh_limit=4,
  discovery_headroom=False,
):
  """Only distinct original emissions with actual independent endpoints."""
  if type(discovery_headroom) is not bool:
    raise ValueError('explicit numerical discovery sampling policy required')
  if type(fresh_limit) is not int or not 1 <= fresh_limit <= 4:
    raise ValueError('one to four original numerical endpoints')
  by_stamp = {row.control_ns: (index, row) for index, row in enumerate(prefix)}
  eligible = []
  starts = (0,) if checkpoint_indices is None else tuple(checkpoint_indices)
  for control, output in reversed(ready):
    entry = by_stamp.get(control.control_ns)
    if (
      entry is None
      or control.control_ns <= after_ns
      or (control.control_ns, output.apply_ns) not in points
      or output.limited
      and (verify_limited is None or not verify_limited(control, output))
      or control != entry[1]
      or any(control.control_ns == row[0].control_ns for row in eligible)
    ):
      continue
    anchors = tuple(index for index in starts if 0 <= index <= entry[0])
    if not anchors:
      continue
    original_prefix = prefix[anchors[-1] : entry[0] + 1]
    if not 1 <= len(original_prefix) <= 100:
      continue
    eligible.append((control, output, original_prefix))
  if phase_windows:
    for first, last, requested in phase_windows:
      if not first < last:
        continue

      def phase(stamp, first=first, last=last):
        return min(3, (stamp - first) * 4 // (last - first + 1))

      missing = set(range(4)) - {phase(stamp) for stamp in requested if first <= stamp <= last}
      buckets = {
        slot: [row for row in eligible if first <= row[0].control_ns <= last and row[0].control_ns not in requested and phase(row[0].control_ns) == slot]
        for slot in missing
      }
      # Do not spend bounded work on a window whose missing phase is no
      # longer available under the ORIGINAL source/replay/age requirements.
      if not missing or any(not values for values in buckets.values()):
        continue
      chosen = [
        min(values, key=lambda row: (row[1].limited, abs(8 * (row[0].control_ns - first) - (2 * slot + 1) * (last - first + 1)), row[0].control_ns))
        for slot, values in sorted(buckets.items())
      ]
      return tuple(sorted(chosen, key=lambda row: row[0].control_ns))
    if phase_fallback_after_ns is None:
      return ()
    eligible = [row for row in eligible if row[0].control_ns > phase_fallback_after_ns]
  if discovery_headroom and fresh_limit == 1 and eligible:
    # Bound serial live-stream work to one recent original measurement.
    # Closed-window repairs above still request every missing time stratum.
    return (min(eligible, key=lambda row: (row[1].limited, -row[0].control_ns)),)
  if same_checkpoint and eligible:
    # Each batch must retain one genuine original checkpoint. Process the
    # earliest available one first, so its watermark cannot skip a newer one.
    first_checkpoint = min(row[2][0].control_ns for row in eligible)
    eligible = [row for row in eligible if row[2][0].control_ns == first_checkpoint]
  # Prefer real original headroom BEFORE computing any perturbation or loss.
  # A native-clipped endpoint still contributes when its original CAN proof is
  # complete; no missing observation, changed limit, or numerical effect is filled.
  if stratified and len(eligible) > 4:
    first = min(row[0].control_ns for row in eligible)
    last = max(row[0].control_ns for row in eligible)
    selected = []
    for slot in range(4):
      bucket = [row for row in eligible if min(3, (row[0].control_ns - first) * 4 // (last - first + 1)) == slot]
      if bucket:
        selected.append(min(bucket, key=lambda row: (row[1].limited if discovery_headroom else False, row[0].control_ns)))
    if len(selected) < 4:
      remaining = [row for row in eligible if all(row[0].control_ns != used[0].control_ns for used in selected)]
      remaining.sort(key=lambda row: row[0].control_ns)
      selected.extend(remaining[: 4 - len(selected)])
  else:
    eligible.sort(key=lambda row: row[1].limited)
    selected = eligible[:4]
  selected = tuple(sorted(selected, key=lambda row: row[0].control_ns))
  if len(selected) > fresh_limit:
    # Clock-stratified work only. Missing whole-window phases are repaired
    # above without this fresh-stream cap; the original four-point proof stays.
    if discovery_headroom:
      # Discovery needs observable native response. Choose only among original
      # clock strata using limiter flags; no error, estimate or trial outcome
      # is read. This never filters the whole physical comparison score.
      from itertools import combinations

      return min(
        combinations(selected, fresh_limit),
        key=lambda rows: (sum(row[1].limited for row in rows), -(rows[-1][0].control_ns - rows[0][0].control_ns), tuple(row[0].control_ns for row in rows)),
      )
    indices = (len(selected) - 1,) if fresh_limit == 1 else tuple(i * (len(selected) - 1) // (fresh_limit - 1) for i in range(fresh_limit))
    return tuple(selected[i] for i in indices)
  return selected


def _original_probe_windows(episodes, profile_hash, group, names):
  """Closed ORIGINAL geometry may queue numbers before the last lagged pose.

  Every selected endpoint must ALREADY have its own independent physical pair.
  Whole-episode publication still waits for the unchanged full pose watermark.
  """
  windows = []
  for row in episodes.completed.values():
    if row['episode'].profile_sha256 == profile_hash and row['group'] == group and row['names'] == names:
      windows.append((min(p.control_ns for p in row['points']), max(p.control_ns for p in row['points']), frozenset(row['requested'])))
  for start, end, kind, _direction, profile in episodes.pending:
    if kind not in ('large', 'subtle') or profile != profile_hash:
      continue
    source_tracking = getattr(episodes, 'source_tracking', False)
    points = tuple(point for point in episodes.endpoints if start <= (point.reference_ns if source_tracking else point.control_ns) <= end)
    original_controls = {point.control_ns for point in points}
    requests = (
      {stamp: value for stamp, value in episodes.probe_requests.items() if stamp in original_controls}
      if source_tracking
      else {stamp: value for stamp, value in episodes.probe_requests.items() if start <= stamp <= end}
    )
    if len(points) < 4 or any(value[:2] != (group, names) for value in requests.values()):
      continue
    # Source windows use their reference clock for membership, then retain
    # each paired ORIGINAL command clock for actual native probe coverage.
    # No point or command is moved/restamped to fit the new window boundary.
    last = max(point.control_ns for point in points) if source_tracking else end
    windows.append((min(p.control_ns for p in points), last, frozenset(requests)))
  return tuple(sorted(windows, key=lambda row: row[0]))


def _group_direction_matches(group, kind, direction):
  # A large maneuver has one steering direction; opposite-side hooks are
  # correctly neutral there. A subtle weave contains both directions.
  from openpilot.starpilot.flm.live_tracking import _group_direction_matches as matches

  return matches(group, kind, direction)


def _probe_group_coverage(rows, names, *, kind, direction):
  from openpilot.starpilot.flm.live_tracking import _required

  matched = tuple(row for row in rows if row.kind == kind and row.direction == direction and getattr(row, 'native_gradient_complete', False))
  counts = {
    name: sum(
      any(
        feature.name == name
        and feature.feature_energy > 1e-10
        and feature.native_endpoints >= 4
        and feature.native_separated / feature.native_endpoints >= 0.09
        for feature in row.sensitivity
      )
      for row in matched
    )
    for name in names
  }
  # One unexcited maneuver never establishes that a hook cannot affect this car.
  # Count repeated WHOLE matching maneuvers, retaining the original3/9 protocol.
  unexcited = sum(
    all(any(feature.name == name and feature.native_endpoints >= 4 and feature.feature_energy <= 1e-10 for feature in row.sensitivity) for name in names)
    for row in matched
  )
  return max(counts.values(), default=0), not any(counts.values()) and unexcited >= _required(kind)


class LiveTrackingActor:
  def __init__(self, bootstrap_process=None, *, probe_process=None, source_tracking=False):
    if type(source_tracking) is not bool or source_tracking and bootstrap_process is not None:
      raise ValueError('source tracking has no plant bootstrap process')
    self.source_tracking = source_tracking
    self.process = bootstrap_process
    from openpilot.starpilot.flm.live_tracking_bootstrap_producer import ResponseSnapshotProcess

    self.snapshot_process = ResponseSnapshotProcess()
    self.probe_preparation = ResponseSnapshotProcess()
    self.probe_process = probe_process
    self.probe_token = None
    self.last_probe_control_ns = 0
    self.last_decode_ns = 0
    self.probe_queue = []
    self.probe_queue_key = None
    self.probe_group_index = self.probe_group_samples = 0
    self.probe_group_epoch = None
    self.probe_group_profile = None
    self.ingress = self.certificate = self.application = None
    self.seed = self.token = self.attempted_epoch = None
    self.pending_bootstrap_result = None
    self.pending_probe_results = []
    self.prepared_probe_context = None
    self.reason = 'response_model_required'
    self.diagnostic_result = None
    self.last_publication_pose_ns = 0
    self.initial_basis = None
    self.restore_pending = False
    self.response_recovery_generation = 0
    self.native_key = self.hardware_inventory = ()

  def interrupt(self, reason, *, context_changed=False):
    state = None if self.ingress is None else self.ingress.optimizer.offer_state
    retain_numeric = bool(not context_changed and self.seed is not None and self.token is not None and (state is None or state.get('accepted') is True))
    # An immutable numerical job keeps its ORIGINAL token and deadline. This
    # preserves no current source/native witness, active trial or command lease.
    if not retain_numeric:
      self.pending_probe_results.clear()
      self.pending_bootstrap_result = None
      self.snapshot_process.cancel(reason)
      self.probe_preparation.cancel(reason)
      if self.process is not None:
        self.process.cancel(reason)
      if self.probe_process is not None:
        self.probe_process.cancel(reason)
      self.probe_token = None
      self.prepared_probe_context = None
    self.probe_queue.clear()
    self.probe_queue_key = None
    if self.ingress is not None:
      self.ingress.interrupt(reason, context_changed=context_changed, preserve_completed=retain_numeric)
    self.reason = reason

  def close(self, reason='live_tracking_actor_closed'):
    self.pending_probe_results.clear()
    self.pending_bootstrap_result = None
    # Shutdown may wait for actual reaping; tick/interrupt only cancel/poll.
    # Always attempt both reapers, including when one fails unexpectedly.
    failure = None
    closed = True
    for process in (self.snapshot_process, self.probe_preparation, self.process, self.probe_process):
      if process is not None:
        try:
          closed = process.close(reason) and closed
        except BaseException as error:
          if failure is None:
            failure = error
          closed = False
    self.probe_token = None
    self.prepared_probe_context = None
    self.probe_queue.clear()
    self.probe_queue_key = None
    if self.ingress is not None:
      self.ingress.interrupt(reason, context_changed=True)
    self.reason = reason
    if failure is not None:
      raise failure
    return closed

  @property
  def pending(self):
    return any(process is not None and process.child is not None for process in (self.process, self.probe_process))

  def binding_for(self, collector, source):
    from openpilot.starpilot.flm.runtime_adapter_registry import digest, control_vm_regime

    control = collector.ready[-1][0]
    anchor = control.vehicle_model_anchor
    if len(anchor) != 3 or control_vm_regime(control) != anchor:
      raise ValueError('genuine current owner vehicle-model anchor required')
    return LiveTrialBinding(
      control.identity,
      source.cp_sha256,
      control.controller_revision,
      control.baseline_revision,
      source.source_epoch,
      source.reset,
      source.calibration_generation,
      digest(anchor),
      control.native_identity if control.native_required else '',
      'bootstrap-pending-v1',
      source.persistent_source_epoch,
      control.native_required,
    ).validate()

  def adopt_response(self, certificate, collector, accepted_full30=None):
    if self.process is not None and self.process.child is not None:
      self.process.cancel('independently_qualified_response_available')
    certificate = validate_certificate(certificate)
    if self.seed is None or replace(certificate.binding, physical_model_revision=self.seed.physical_model_revision) != self.seed:
      raise ValueError('bootstrap response belongs to another current source')
    accepted = tuple((name, 0.0) for name in NAMES) if accepted_full30 is None else accepted_full30
    self.ingress = LiveTrackingIngress(certificate.binding, certificate, accepted, controller_domain=collector.adapter.command_family)
    self.certificate = certificate
    collector.adapter.replay.source_controller_revision = collector.adapter.controller_revision
    self.ingress.replay = collector.adapter.replay
    self.ingress.episodes.replay = collector.adapter.replay
    self.probe_token = None
    self.last_probe_control_ns = 0
    collector.live_tracking = self.ingress
    self.ingress.scoring_enabled = not self.restore_pending
    from openpilot.starpilot.flm.live_tracking_requalification import basis_from_pose

    try:
      pose = collector.original_source_capture.rows[-1]
      self.initial_basis = basis_from_pose(
        certificate.binding, pose, now_ns=pose.completed_ns, native_key=self.native_key, hardware_inventory=self.hardware_inventory
      )
    except (AttributeError, IndexError, TypeError, ValueError):
      # Missing hardware provenance withholds cross-drive persistence, not
      # otherwise valid within-drive learning/current physical monitoring.
      self.initial_basis = None
    from openpilot.starpilot.flm.live_tracking_response import TrackingSourceContext

    self.reason = 'fresh_current_source_window_required' if type(certificate) is TrackingSourceContext else 'fresh_physical_residual_window_required'

  def hold_restore(self, pending):
    self.restore_pending = bool(pending)
    if self.ingress is not None:
      self.ingress.scoring_enabled = not self.restore_pending
      if self.restore_pending:
        self.ingress.optimizer.interrupt('historical_restore_pending')

  def _submit_probe_queue(self, token, now_ns):
    from openpilot.starpilot.flm.live_tracking_probe_process import make_probe_batch

    # The aggregate literal snapshot bound also applies to a batch. Split only
    # numerical work; four genuine endpoints per episode remain mandatory.
    for count in range(len(self.probe_queue), 0, -1):
      try:
        batch = make_probe_batch(tuple(item[0] for item in self.probe_queue[:count]), queued_ns=now_ns)
      except ValueError:
        continue
      if self.probe_process.submit_probe(batch, token=token, now_ns=now_ns):
        self.probe_token = token
        del self.probe_queue[:count]
        if not self.probe_queue:
          self.probe_queue_key = None
        return True
      return False
    self.probe_queue.clear()
    self.probe_queue_key = None
    self.reason = 'bounded_numerical_probe_transport_unavailable'
    return False

  def _probe_tick(self, collector, source, *, now_ns):
    """Queue optional numerical work; never update a controller in this actor."""
    if self.probe_process is None or self.ingress is None:
      return
    if self.restore_pending:
      self.probe_process.poll(token=None, now_ns=now_ns)
      self.probe_queue.clear()
      self.probe_queue_key = None
      return
    from openpilot.starpilot.flm.live_tracking_probe_process import make_probe_job, probe_replay_snapshot

    if self.process is not None and self.process.child is not None:
      return  # At most one optional reconstruction/identification child.
    optimizer = self.ingress.optimizer
    self.reason = optimizer.reason
    accepted = tuple((name, optimizer.accepted[name]) for name in NAMES)
    profile_hash = full_profile_sha256(self.certificate.binding.baseline_revision, dict(accepted))
    token = (self.token, self.certificate.model_revision, profile_hash)
    try:
      result = self.probe_process.poll_probe(token=token, binding=self.certificate.binding, now_ns=now_ns)
      completed, self.pending_probe_results = self.pending_probe_results, []
      if result is not None:
        completed.append(result)
      for result in completed:
        for row in result if type(result) is tuple else (result,):
          self.ingress.observe_probe(row, now_ns=now_ns)
          self.probe_group_samples += 1
      from openpilot.starpilot.flm.live_tracking import discovery_needs_probes

      if optimizer.discovery is not None and not discovery_needs_probes(optimizer.discovery):
        # Direction/coordinate selection already has genuine native probes.
        # Its NEW uncensored baseline needs measured episodes, not another
        # finite-difference replay of each endpoint. Reap optional old work
        # nonblocking; no original samples or native grants are synthesized.
        self.probe_preparation.cancel('independent_measurement_comparison')
        self.probe_process.cancel('independent_measurement_comparison')
        self.probe_queue.clear()
        self.probe_queue_key = self.prepared_probe_context = self.probe_token = None
        return
      # Do not consume a one-shot prepared result while the numerical child
      # cannot accept it. The original enqueue time/deadline remains binding;
      # one existing preparation pipe holds it without blocking ingestion.
      prepared = self.probe_preparation.poll(token=token, now_ns=now_ns) if self.probe_process.child is None else None
      if prepared is not None and prepared.get('numeric') is not None:
        expected, self.prepared_probe_context = self.prepared_probe_context, None
        from openpilot.starpilot.flm.live_tracking_probe_process import _context_digest

        if (
          expected is None
          or set(prepared) != {'numeric', 'job_sha256', 'context_sha256'}
          or prepared['context_sha256'] != _context_digest(expected)
          or type(prepared['job_sha256']) is not str
          or len(prepared['job_sha256']) != 64
          or any(char not in '0123456789abcdef' for char in prepared['job_sha256'])
          or any(not 0 < row['queued_ns'] <= now_ns <= row['queued_ns'] + 30_000_000_000 for row in expected['batch'])
        ):
          raise ValueError('owned inline numerical context or deadline differs')
        numeric = self.probe_process.decode_report(
          prepared['numeric'], expected=(prepared['job_sha256'], expected), prepared_sha=prepared['context_sha256'], binding=self.certificate.binding
        )
        for row in numeric if type(numeric) is tuple else (numeric,):
          self.ingress.observe_probe(row, now_ns=now_ns)
          self.probe_group_samples += 1
      if prepared is not None and prepared.get('prepared') is not None:
        expected, self.prepared_probe_context = self.prepared_probe_context, None
        if expected is None:
          raise ValueError('original parent numerical context unavailable')
        if self.probe_process.submit_prepared_probe(prepared['prepared'], expected=expected, token=token, now_ns=now_ns):
          self.probe_token = token
      if self.probe_preparation.pid is not None:
        return
      if self.probe_queue and (cast(tuple[object, ...], self.probe_queue_key)[0] != token or now_ns - self.probe_queue[0][1] > 2_000_000_000):
        self.probe_queue.clear()
        self.probe_queue_key = None
      if self.probe_process.child is not None or optimizer.offer_state is not None and optimizer.offer_state['phase'] == 'trial':
        return
      pair = collector.live_response_pair
      if pair is None:
        return
      control, output, _pose = pair
      from openpilot.starpilot.flm.live_tracking_response import verified_clipped_input

      def known_input(candidate, applied):
        return verified_clipped_input(collector.adapter.replay, candidate, applied, self.certificate.binding)

      if output.limited and not known_input(control, output):
        return  # Missing original CAN/limiter proof is never a numerical input.
      if control.control_ns <= self.last_probe_control_ns or self.ingress.profile(control) != profile_hash:
        return
      witness = self.ingress.monitor.witness(now_ns)
      if witness is None:
        return
      history = _original_probe_history(collector, self.ingress, control)
      checkpoints = [index for index, row in enumerate(history) if row.replay_snapshot]
      if not checkpoints:
        return
      # Retain every genuine checkpoint in the existing bounded history. Each
      # endpoint still selects its OWN latest preceding<=100-frame checkpoint.
      # Restricting the parent view to two anchors can hide the middle of a
      # maneuver while the serial numerical worker finishes an earlier batch.
      first_checkpoint = checkpoints[0]
      prefix = history[first_checkpoint:]
      checkpoint_indices = tuple(index - first_checkpoint for index in checkpoints if index >= first_checkpoint)
      # Use the worker's existing bounded full-checkpoint prefix. Restricting
      # admission to its first120ms starved4Hz sampling after170ms physical
      # lag. Every original frame is replayed; no missing endpoint is inferred.
      if not prefix or prefix[-1] != control or not 1 <= len(prefix) <= 301:
        if self.probe_queue:
          self._submit_probe_queue(token, now_ns)
        return
      unsupported = ()
      if collector.adapter.command_family == 'torque_pid':
        from openpilot.starpilot.flm.pid_stages import UNSUPPORTED

        unsupported = UNSUPPORTED
      kind = self.ingress.episodes.mode
      # Original prefix verification dominates optional numerical CPU. Measure
      # every supported hook from that same genuine replay, rather than letting
      # an unexcited first group starve all other coordinates. PID's four
      # unmeasurable friction hooks remain excluded, never reported as measured.
      group, names = 'all30', tuple(name for name in NAMES if name not in unsupported)
      if not names:
        return
      ep = self.ingress.episodes
      phase_windows = _original_probe_windows(ep, profile_hash, group, names)
      if kind not in ('large', 'subtle') and not phase_windows:
        return
      self.probe_group_epoch = (kind, self.ingress.episodes.start)
      queue_key = (token, prefix[0].control_ns, group, names)
      if self.probe_queue and (len(self.probe_queue) >= 2 or self.probe_queue_key != queue_key or now_ns - self.probe_queue[0][1] >= 250_000_000):
        self._submit_probe_queue(token, now_ns)
        return
      # Freeze up to four DISTINCT already-observed physical endpoints from
      # the same original checkpoint. A4Hz coordinator must not require four
      # separate process roundtrips within each short real maneuver.
      points = {(point.control_ns, point.apply_ns) for point in self.ingress.episodes.endpoints}
      selected = []
      original_ready = tuple(
        (candidate, applied) for candidate, applied in getattr(collector, 'probe_ready', collector.ready) if 0 <= now_ns - applied.apply_ns <= 2_000_000_000
      )
      # A scalar newest-selected watermark cannot describe unsampled holes.
      # Repair a known whole window's missing ORIGINAL time phases first,
      # including older unrequested endpoints still inside all existing bounds.
      # Selection inspects only source clocks/request identities, never error,
      # improvement, native effect magnitude or a predicted favorable result.
      # A completed/drained prior window cannot accept fresh requests.
      # Do not let its unrequested points consume the limited fresh-stream
      # batch slots; explicit whole-window repairs above remain independent.
      fresh_after = max(self.last_probe_control_ns, ep.start - 1)
      if phase_windows:
        candidates = _observed_probe_candidates(
          original_ready,
          prefix,
          points,
          after_ns=0,
          verify_limited=known_input,
          checkpoint_indices=checkpoint_indices,
          stratified=True,
          phase_windows=phase_windows,
          phase_fallback_after_ns=fresh_after if kind == 'large' else None,
          fresh_limit=4,
          discovery_headroom=True,
        )
      elif kind == 'large':
        candidates = _observed_probe_candidates(
          original_ready,
          prefix,
          points,
          after_ns=fresh_after,
          verify_limited=known_input,
          checkpoint_indices=checkpoint_indices,
          stratified=True,
          fresh_limit=4,
          discovery_headroom=True,
        )
      else:
        candidates = ()  # Short subtle windows are sampled as whole original batches.
      for candidate, applied, original_prefix in candidates:
        if self.ingress.episodes.request_probe(candidate.control_ns, group=group, names=names, queued_ns=now_ns):
          selected.append((candidate, applied, original_prefix))
      if not selected:
        return
      # Fork freezes the genuine source prefix without copying its nested
      # checkpoint graph on the ingestion loop. The original worker still
      # recomputes the complete controller/native evidence independently.
      from openpilot.starpilot.flm.live_tracking_probe_process import original_probe_context, prepare_probe_payloads

      expected = original_probe_context(selected, accepted, self.certificate.binding, source, group=group, names=names, now_ns=now_ns)

      def prepare_probe():
        import json
        import os
        import resource
        import time

        os.nice(10)

        soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        limit = min([20] + [value for value in (soft, hard) if value >= 0])
        resource.setrlimit(resource.RLIMIT_CPU, (limit, hard))
        memory_soft, memory_hard = resource.getrlimit(resource.RLIMIT_AS)
        memory_limit = min([2 * 1024 * 1024 * 1024] + [value for value in (memory_soft, memory_hard) if value >= 0])
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_hard))
        from openpilot.starpilot.flm.learner_cpu import configure_cpu
        configure_cpu(numerical=True)
        from openpilot.starpilot.flm.native_primitives import enable_fit_worker
        enable_fit_worker()
        from openpilot.starpilot.flm.live_tracking_probe_worker import evaluate_job, MAX_OUTPUT

        jobs = []
        for endpoint, emission, original_prefix in selected:
          snapshot = probe_replay_snapshot(collector.adapter.replay, original_prefix, endpoint_output=emission)
          jobs.append(
            make_probe_job(
              replay_snapshot=snapshot,
              control=endpoint,
              output=emission,
              accepted_full30=accepted,
              binding=self.certificate.binding,
              response_witness=witness,
              source=source,
              evidence_ns=now_ns,
              queued_ns=now_ns,
              current_vehicle_model_anchor=endpoint.vehicle_model_anchor,
              group=group,
              names=names,
              prefix_controls=original_prefix,
              _payload_only=True,
            )
          )
        # This existing child already owns a frozen original source snapshot.
        # Reconstruct/validate the same numerical job HERE instead of paying a
        # second Python startup and streaming it through another pipe.
        prepared = prepare_probe_payloads(tuple(jobs), queued_ns=now_ns, expected=expected)
        started_ns = time.monotonic_ns()
        if not now_ns <= started_ns <= now_ns + 2_000_000_000:
          raise ValueError('original numerical input deadline expired')
        numeric = evaluate_job(json.loads(prepared['wire']), now_ns=started_ns)
        if len(json.dumps(numeric, separators=(',', ':'), allow_nan=False).encode()) > MAX_OUTPUT:
          raise ValueError('original numerical output bound')
        return {'numeric': numeric, 'job_sha256': prepared['job_sha256'], 'context_sha256': prepared['context_sha256']}

      if self.probe_preparation.submit_preparation(prepare_probe, token=token, epoch=self.probe_group_epoch, now_ns=now_ns):
        self.prepared_probe_context = expected
        self.last_probe_control_ns = max(self.last_probe_control_ns, max(item[0].control_ns for item in selected))

    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      # Missing/unsupported source-state reconstruction is no sensitivity proof.
      self.reason = 'genuine_native_sensitivity_unavailable'

  def _advance_original_decoder(self, collector, now_ns):
    # Historical verification is independent of current collection admission. At most64 original messages per4Hz tick;
    # no skipped counter/checksum input, no endpoint or authority fabrication.
    if now_ns - self.last_decode_ns >= 250_000_000:
      self.last_decode_ns = now_ns
      history = collector.adapter.replay.emissions
      if history.events:
        try:
          history.decode(next(reversed(history.events.values())))
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
          pass  # Pending/invalid bytes still fail their exact verification.

  def _retire_identified_history(self, collector):
    """Retain recent prefixes for an exact source or identified response.

    Source tracking has no plant-bootstrap job requiring the pre-ingress tape.
    Retiring old history never admits a response, command or numerical trial.

    Pending workers own fork-frozen originals. Live endpoints are at most2s
    old with at most1s checkpoint history; retain4s plus its preceding original
    checkpoint. No decoder state, unconsumed CAN, certificate or episode score
    is replaced. Release at most32 rows per structure per tick.
    """
    binding = self.certificate.binding if self.certificate is not None else self.seed if self.source_tracking else None
    if binding is None or not self.source_tracking and self.ingress is None:
      return
    replay = collector.adapter.replay
    if (
      replay.raw_mode is not True
      or replay.identity != binding.identity
      or replay.baseline_revision != binding.baseline_revision
      or not replay.records
      or getattr(replay, 'trace_archive', None) is not None
    ):
      return
    cutoff = replay.records[-1].control_ns - 4_000_000_000
    anchors = [stamp for stamp, _ in replay.checkpoints if stamp <= cutoff]
    if not anchors:
      return
    anchor = anchors[-1]
    for _ in range(32):
      if not replay.records or replay.records[0].control_ns >= anchor:
        break
      removed = replay.records.popleft()
      replay.normalized.pop(removed.control_ns, None)
    oldest = replay.records[0].control_ns
    for _ in range(2):
      if len(replay.checkpoints) < 2 or replay.checkpoints[1][0] > oldest:
        break
      replay.checkpoints.popleft()
    emissions = replay.emissions
    for _ in range(32):
      if not emissions.events:
        break
      stamp = next(iter(emissions.events))
      event = emissions.events[stamp]
      if event.control_ns >= oldest or (emissions.decode_queue and emissions.decode_queue[0] <= stamp):
        break  # Never skip original input to the stateful CAN decoder.
      emissions.events.popitem(last=False)
      emissions.decode_errors.pop(stamp, None)
      if emissions.decoder is not None:
        emissions.decoder.verified.pop(stamp, None)
      if emissions.by_control.get(event.control_ns) is event:
        emissions.by_control.pop(event.control_ns)
    if emissions.events:
      first_frame = next(iter(emissions.events.values())).witness.frame + 1
      for _ in range(32):
        if not emissions.carriers or next(iter(emissions.carriers)) >= first_frame:
          break
        emissions.carriers.popitem(last=False)
    context = getattr(collector.adapter, 'original_context_receipts', None)
    if context is not None:
      # An unconsumed native emission can still require an older original
      # control. Keep it together with every retained replay/checkpoint row.
      keep_from = min(oldest, next(iter(emissions.events.values())).control_ns) if emissions.events else oldest
      for _ in range(32):
        if not context.controls or next(iter(context.controls)) >= keep_from:
          break
        context.controls.popitem(last=False)
      for _ in range(32):
        if not context.outputs or next(iter(context.outputs.values())).control_ns >= keep_from:
          break
        context.outputs.popitem(last=False)
    archive = getattr(collector.adapter, 'original_feedback_archive', None)
    if archive is not None:
      for records in (archive.publications, archive.consumptions):
        for _ in range(32):
          if not records or next(iter(records)) >= oldest:
            break
          records.popitem(last=False)

  def _same_bootstrap_source(self, source, now_ns):
    """Only retain historical computation; this is never native admission."""
    from openpilot.starpilot.flm.runtime_adapter_registry import SourceState

    return bool(
      self.seed is not None
      and self.token is not None
      and type(source) is SourceState
      and source.enabled is True
      and source.manual is False
      and source.configuration_known is True
      and all(type(stamp) is int for stamp in (source.observed_ns, source.expires_ns, now_ns))
      and 0 < source.observed_ns <= now_ns <= source.expires_ns <= source.observed_ns + 500_000_000
      and (source.cp_sha256, source.source_epoch, source.reset, source.calibration_generation, source.persistent_source_epoch)
      == (self.seed.cp_sha256, self.seed.source_epoch, self.seed.reset_generation, self.seed.calibration_generation, self.seed.persistent_source_epoch)
    )

  def tick(self, collector, source, *, now_ns, current_native, native_key=(), hardware_inventory=(), allow_bootstrap=True, evidence_delay_only=False):
    self.native_key, self.hardware_inventory = native_key, hardware_inventory
    snapshot_result = self.snapshot_process.poll(token=self.token if self._same_bootstrap_source(source, now_ns) else None, now_ns=now_ns)
    if self.probe_process is not None and self.ingress is None:
      # Continue actual reaping after a previous source change removed ingress.
      self.probe_process.poll(token=None, now_ns=now_ns)
    if (self.source_tracking and collector is not None and
        getattr(getattr(getattr(collector, 'adapter', None), 'replay', None), 'raw_mode', False) is True):
      # A missing current pair withdraws learning; it must not suspend bounded
      # historical decoding/retirement and grow the next collection pause.
      self._advance_original_decoder(collector, now_ns)
      self._retire_identified_history(collector)
    if collector is None or source is None or not collector.ready or current_native is not True:
      # Report the current missing input, not an earlier identity transition.
      # Retaining historical work below still supplies no current permission.
      self.reason = (
        'controller_collection_required' if collector is None else
        'current_settings_source_required' if source is None else
        'qualified_control_output_pair_required' if not collector.ready else
        'current_native_permission_unavailable'
      )
      # A frozen response job describes ORIGINAL source/native observations,
      # not permission to apply a correction now. Temporary input/native gaps
      # must not cancel that computation after expensive snapshot preparation.
      # Keep any completed report inert until a fresh exact binding and current
      # native admission below can independently admit the numerical model.
      same_source = collector is not None and self._same_bootstrap_source(source, now_ns)
      retain = same_source and self.ingress is None
      retain_probe = same_source and self.ingress is not None and self.certificate is not None and self.probe_token is not None
      preparation = self.probe_preparation
      # Inline numerical work owns the same original window as an external
      # probe. Retain only that matching, uncanceled, unexpired frozen job;
      # the interruption below still withdraws every live witness and offer.
      retain_preparation = bool(
        same_source and self.ingress is not None and self.certificate is not None
        and self.prepared_probe_context is not None and preparation.pid is not None
        and not preparation.canceled and 0 < preparation.started <= now_ns <= preparation.started + 30_000_000_000
        and preparation.token == (self.token, self.certificate.model_revision,
          full_profile_sha256(self.certificate.binding.baseline_revision, self.ingress.optimizer.accepted))
      )
      if self.process is not None:
        result = self.process.poll(token=self.token if retain else None, now_ns=now_ns)
        if retain and type(result) is dict:
          self.pending_bootstrap_result = result
      if not retain:
        self.pending_bootstrap_result = None
      if self.probe_process is not None:
        if retain_probe:
          # This is only an immutable historical derivative, never current
          # permission. Keep its original 30s deadline and exact job binding.
          result = self.probe_process.poll_probe(token=self.probe_token, binding=self.certificate.binding, now_ns=now_ns)
          if result is not None and len(self.pending_probe_results) < 4:
            self.pending_probe_results.append(result)
        else:
          self.pending_probe_results.clear()
          self.probe_process.poll(token=None, now_ns=now_ns)
      self.probe_queue.clear()
      self.probe_queue_key = None
      if self.ingress is not None:
        self.ingress.interrupt('current_evidence_delayed' if evidence_delay_only else 'current_source_or_native_permission_unavailable',
          preserve_completed=retain_probe or retain_preparation)
      return None
    try:
      seed = self.binding_for(collector, source)
    except (AttributeError, TypeError, ValueError):
      if self.process is not None:
        self.process.poll(token=None, now_ns=now_ns)
      if self.probe_process is not None:
        self.probe_process.poll(token=None, now_ns=now_ns)
      self.probe_queue.clear()
      self.probe_queue_key = None
      if self.ingress is not None:
        self.ingress.interrupt('actual_response_source_unavailable', context_changed=True)
      self.reason = 'actual_response_source_unavailable'
      return None
    token = tuple(vars(seed).values())
    if self.seed is not None and seed != self.seed:
      self.prepared_probe_context = None
      self.pending_probe_results.clear()
      self.pending_bootstrap_result = None
      if self.probe_process is not None:
        self.probe_process.poll(token=None, now_ns=now_ns)
      self.probe_queue.clear()
      self.probe_queue_key = None
      if self.ingress is not None:
        self.ingress.interrupt('source_configuration_changed', context_changed=True)
      self.ingress = self.certificate = None
      collector.live_tracking = None
      self.attempted_epoch = None
    self.seed, self.token = seed, token
    self._advance_original_decoder(collector, now_ns)
    if self.ingress is not None and self.ingress.monitor.persistent_residual_failure(now_ns):
      # This is numeric-model withdrawal, never admission of a replacement.
      # Preserve the per-epoch attempt latch: no outcome-based cohort search.
      self.interrupt('persistent_physical_response_drift', context_changed=True)
      self.response_recovery_generation += 1
      self.hold_restore(False)
      self.ingress = self.certificate = self.application = self.initial_basis = None
      collector.live_tracking = None
      capture = collector.original_source_capture
      if capture is not None:
        self.attempted_epoch = capture.epoch
      return None
    if self.process is not None:
      result, self.pending_bootstrap_result = self.pending_bootstrap_result, None
      polled = self.process.poll(token=token, now_ns=now_ns)
      if polled is not None:
        result = polled
      if type(result) is dict:
        if result.get('production_admissible') is not True:
          self.diagnostic_result = result
          self.reason = 'diagnostic_response_not_production_admission'
        elif result.get('status') == 'response_identified' and result.get('certificate') is not None:
          self.adopt_response(certificate_from_dict(result['certificate']), collector)
        else:
          self.reason = str(result.get('reason', 'response_identification_unavailable'))
    context_error = None
    if self.ingress is None and self.source_tracking and allow_bootstrap:
      from openpilot.starpilot.flm.live_tracking_response import source_context

      capture = collector.original_source_capture
      if capture is not None and capture.rows:
        try:
          context = source_context(seed, collector.ready[-1][0], capture.rows[-1], now_ns=now_ns)
          self.adopt_response(context, collector)
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
          context_error = error
          self.reason = 'current_original_tracking_sources_required'
    if self.ingress is None and self.source_tracking:
      from openpilot.starpilot.flm.source_stall_log import log_source_stall
      log_source_stall(self, collector, now_ns=now_ns, bootstrap_allowed=allow_bootstrap, context_error=context_error)
      self.reason = 'fresh_current_tracking_sources_required'
      return None
    if self.ingress is None:
      if not allow_bootstrap:
        self.reason = 'retained_response_requalification_pending'
        return None
      if self.process is None:
        self.reason = 'production_bootstrap_budget_not_yet_qualified'
        return None
      capture = collector.original_source_capture
      if not self.pending and capture is not None and self.attempted_epoch != capture.epoch:
        if type(snapshot_result) is dict and self.snapshot_process.epoch == capture.epoch and self.snapshot_process.token == token:
          snapshot = snapshot_result.get('snapshot')
          if snapshot is not None and self.process.submit(snapshot, token=token, now_ns=now_ns):
            self.attempted_epoch = capture.epoch
            self.reason = 'initial_independent_response_identification'
          elif snapshot is None:
            self.reason = str(snapshot_result.get('error', 'original_response_snapshot_not_ready'))
        elif self.snapshot_process.pid is None:
          self.snapshot_process.submit(collector, seed, token=token, now_ns=now_ns)
          self.reason = self.snapshot_process.reason
      return None
    if not self.source_tracking:
      self._retire_identified_history(collector)
    self._probe_tick(collector, source, now_ns=now_ns)
    if self.restore_pending:
      return None
    offer = self._current_publication_offer(now_ns)
    if offer is None:
      return None
    pose = validate_response_witness(offer.response_witness, self.certificate.binding, now_ns).last_pose_ns
    if pose <= self.last_publication_pose_ns:
      return None
    self.last_publication_pose_ns = pose
    return offer

  def _current_publication_offer(self, now_ns):
    from openpilot.starpilot.flm.live_tracking_response import response_witness_value, validate_response_witness

    offer = self.ingress.latest_offer
    if offer is None:
      return None
    try:
      # Structural validation of the owned old message is not current permission.
      offer.validate()
      old = response_witness_value(offer.response_witness)
      optimizer = self.ingress.optimizer
      try:
        current = validate_response_witness(optimizer.response_witness, self.certificate.binding, now_ns)
      except (TypeError, ValueError):
        self.ingress.interrupt('expired_response_witness' if optimizer._witness_expired(now_ns)
          and offer.binding == self.certificate.binding and old.certificate == self.certificate
          else 'fresh_response_witness_unavailable')
        return None
      state = optimizer.offer_state
      if offer.binding != self.certificate.binding or old.certificate != self.certificate or state is None:
        raise ValueError('trial publication identity changed')
      if offer.trial_id != state['trial_id']:
        prior = state.get('prior_accepted')
        if (
          prior is None
          or prior.get('accepted') is not True
          or (offer.trial_id, offer.phase, offer.target_sequence, offer.target_sha256)
          != (prior['trial_id'], prior['phase'], prior.get('target_sequence', optimizer.sequence), optimizer._hash(prior['target']))
        ):
          raise ValueError('cached offer is not the owned predecessor')
      state_changed = (
        offer.trial_id != state['trial_id']
        or offer.phase != state['phase']
        or offer.target_sequence != state.get('target_sequence', optimizer.sequence)
        or offer.target_sha256 != optimizer._hash(state['target'])
      )
      if state_changed or current.last_pose_ns > old.last_pose_ns:
        # Issue from a genuinely newer qualified pose; never renew the old
        # witness, the original trial deadline, or any native command lease.
        offer = optimizer.offer(now_ns, publication=False)
        if offer is None:
          raise ValueError('current trial publication unavailable')
        cast(LiveTrackingIngress, self.ingress).latest_offer = offer
      validate_response_witness(offer.response_witness, self.certificate.binding, now_ns)
      return offer
    except (AttributeError, TypeError, ValueError):
      self.ingress.interrupt('fresh_response_witness_unavailable')
      return None

  def mark_publication(self, offer):
    if self.ingress is not None:
      self.ingress.optimizer.mark_published(offer)

  def observe_application(self, application, *, now_ns):
    if self.ingress is None or not self.ingress.observe_application(application, now_ns=now_ns):
      return False
    self.application = application
    return True

  def observe_restored(self, application, offer, *, now_ns):
    from openpilot.starpilot.flm.live_trial_owner import LiveTrialApplication

    if self.ingress is None or type(application) is not LiveTrialApplication:
      return False
    if (
      application.status != 'restored'
      or application.binding != self.certificate.binding
      or application.trial_id != offer.history_trial_id
      or application.applied != offer.target
      or application.applied_sha256 != offer.target_sha256
      or not 0 < application.control_ns <= now_ns <= application.control_ns + 100_000_000
    ):
      return False
    validate_response_witness(offer.response_witness, self.certificate.binding, now_ns)
    if full_profile_sha256(self.certificate.binding.baseline_revision, dict(application.applied)) != offer.target_sha256:
      return False
    optimizer = self.ingress.optimizer
    previous = getattr(self.ingress, 'latest_restore_offer', None)
    if previous is not None:
      if (
        (previous.binding, previous.history_scope, previous.history_trial_id, previous.target_sha256)
        != (offer.binding, offer.history_scope, offer.history_trial_id, offer.target_sha256)
        or optimizer.accepted != dict(application.applied)
      ):
        return False  # An old restoration cannot replace a later accepted tune.
    else:
      # Adopt history once per fresh ingress. Repeated current restore ACKs
      # must not erase the new baseline or a subsequent independent trial.
      optimizer.accepted = dict(application.applied)
      optimizer.baseline.clear()
      optimizer.offer_state = None
      optimizer.future.clear()
    self.ingress.latest_restore_offer = offer
    self.application = application
    self.hold_restore(False)
    return True

  def persistence_inputs(self, now_ns):
    if self.ingress is None or self.application is None or self.ingress.latest_offer is None:
      return None
    offer = self.ingress.latest_offer
    if (
      offer.phase != 'confirmed'
      or self.application.confirmed_target_sha256 != offer.target_sha256
      or not self.ingress.optimizer.report(now_ns)['applicationAcknowledged']
    ):
      return None
    return offer, self.application, self.certificate
