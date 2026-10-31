"""Sole optional learning publisher; native torqued never waits for this process.

Actual controller baseline and independent physical model adapters are selected
from received source-bound evidence, never another process's startup preference.
"""

from collections.abc import Callable
from typing import cast
from openpilot.starpilot.flm.runtime_adapter_registry import SourceState

import os
import uuid
import time
from functools import partial

from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.flm.fullrate_collector import FullrateCollector, PairedDrainState, drain_pair, ControlEvidence, AppliedEvidence
from openpilot.starpilot.flm.learning_wire import read_control, read_applied, result_message, write_source_receipt
from openpilot.starpilot.flm.runtime_adapter_registry import SOURCE_LEASE_NS


class _SourceDelivery:
  """Consume an actual incidental SubMaster delivery before polling again.

  No SubMaster clock, updated/health flag or body is written. Final native
  refresh remains in place; its already-received input is not skipped by the
  next blocking poll. At most one pending latest delivery is retained.
  """
  def __init__(self):
    self.pending = False
    self.pose_header = 0
    self.evidence_poller = None
    self.evidence_sockets = ()

  def refresh(self, sm):
    sm.update(0)
    self.pending = self.pending or any(sm.updated.values())
    if sm.updated['deviceMotion']:
      self.pose_header = int(sm.logMonoTime['deviceMotion'])

  def next(self, sm, *, pending_evidence=()):
    if not self.pending:
      if pending_evidence:
        # A received pose may still need a genuinely newer consumed state.
        # Poll existing tapes without consuming/reordering their FIFO heads.
        sockets = (sm.sock['deviceMotion'], *pending_evidence)
        if len(sockets) != len(self.evidence_sockets) or any(a is not b for a, b in zip(sockets, self.evidence_sockets, strict=True)):
          self.evidence_poller = messaging.Poller()
          for socket in sockets:
            self.evidence_poller.registerSocket(socket)
          self.evidence_sockets = sockets
        self.evidence_poller.poll(10)
        sm.update(0)
      else:
        sm.update()
    header = self.pose_header or (int(sm.logMonoTime['deviceMotion']) if sm.updated['deviceMotion'] else 0)
    self.pending = False
    self.pose_header = 0
    return header


def source_receiver(services):
  # Full-rate control/application tapes retain their original FIFO rows. This optional
  # process conflates CarState, so its receive count cannot measure the 100Hz
  # publisher rate while it processes a batch. Keep the actual envelope,
  # source-age, state-bracket and native checks; do not fabricate freq_ok.
  # Wake on the actual motion/native/state delivery rather than waiting for
  # another CarState. All envelope, source-age and current authority checks
  # still run on the real received messages.
  queued_services = {'deviceMotion', 'driverMonitoringState'} & set(services)

  class SourceReceiver(messaging.SubMaster):
    def update_msgs(self, cur_time, msgs):
      latest = []
      received_extra = False
      for packet in msgs:
        if packet is not None and packet.which() in queued_services:
          service, received_at = packet.which(), cur_time
          # Count actual dequeued packets with actual receipt clocks. Keep only
          # the newest body for normal SubMaster state; never restamp a source.
          for _ in range(7):
            following = messaging.recv_one_or_none(self.sock[service])
            if following is None:
              break
            received_extra = True
            self.freq_tracker[service].record_recv_time(received_at)
            packet, received_at = following, time.monotonic()
        latest.append(packet)
      super().update_msgs(time.monotonic() if received_extra else cur_time, latest)

  sm = SourceReceiver(services, ignore_alive=['aolSafetyWire'],
    ignore_valid=['aolSafetyWire'], ignore_avg_freq=['aolSafetyWire', 'carState'])
  for service in queued_services:
    sm.sock[service] = messaging.sub_sock(service, conflate=False)
  if 'deviceMotion' in services:
    poller = messaging.Poller()
    poller.registerSocket(sm.sock['deviceMotion'])
    sm.poller = poller
    sm.non_polled_services = set(services) - {'deviceMotion'}
  return sm


MAX_OBJECTIVES = 12
MAX_CACHED_PROFILES = 14  # Six FF cells and four independently qualified centre speed bands.


def production_adapter_for_evidence(cp, evidence, key, *, source, source_plant_clock=None, source_plant_protocol=None):
  """Adopt the qualified event contract only in the optional live producer.

  Legacy/reference callers retain version2. The live event producer explicitly
  selects fresh feedback trial7 and whole-map restore8, never FD shadow.
  """
  from openpilot.starpilot.flm.clipping_quality import ZERO_REFERENCE_QUALITY
  from openpilot.starpilot.flm.runtime_adapter_registry import SourceState, adapter_for_evidence

  if not isinstance(source, SourceState) or not source.enabled or source.manual:
    return None
  from openpilot.starpilot.flm.feedback_replay import PROTOCOL as FEEDBACK_PROTOCOL
  if source_plant_protocol is None:
    source_plant_protocol = FEEDBACK_PROTOCOL
  adapter = adapter_for_evidence(cp, evidence, key, source=source,
    event_replay_version=3, event_clipping_quality=ZERO_REFERENCE_QUALITY,
    event_restore_version=8 if source_plant_protocol == FEEDBACK_PROTOCOL else 6)
  if source_plant_clock is not None or source_plant_protocol is not None:
    from openpilot.starpilot.flm.source_plant_runtime import enable
    from openpilot.starpilot.flm.source_plant_fit import PROTOCOL
    from openpilot.starpilot.flm.feedback_replay import PROTOCOL as FEEDBACK_PROTOCOL
    if source_plant_protocol not in (None,PROTOCOL,FEEDBACK_PROTOCOL):
      raise ValueError('unknown independent plant producer protocol')
    if adapter is not None and getattr(adapter,'event_collection',False):
      return enable(adapter,source_plant_clock,protocol=source_plant_protocol)
  return adapter


def _collect_source_control(collector, prepared, sm, cp, now_ns):
  """An arriving record cannot inherit the previous record's health verdict."""
  from openpilot.starpilot.flm.native_health import current_native_health
  collector._learning_current_native_allowed = current_native_health(sm, cp, prepared, now_ns)
  return collector.control(prepared, now_ns)


def _collect_applied_output(collector, applied, sm, cp, now_ns):
  """Each emission rechecks current native health for its original consumed CC."""
  from openpilot.starpilot.flm.native_health import current_native_health

  control = collector.controls.get(applied.control_ns, collector.recent_controls.get(applied.control_ns))
  collector._learning_current_native_allowed = bool(control is not None and current_native_health(sm, cp, control, now_ns))
  return collector.output(applied, now_ns)


def _configuration_calibration(sm, calibration_lifecycle, now):
  # Calibration describes the vehicle frame, not a per-message command lease.
  # Like torqued's PoseCalibrator, retain a valid calibrated transform between
  # deliveries. Sample freshness and current native authority are checked
  # independently when admitting physical observations and applying offers.
  stamp = int(sm.logMonoTime['extrinsicsCalibration'])
  receipt = int(round(sm.recv_time['extrinsicsCalibration'] * 1e9))
  if not (sm.seen['extrinsicsCalibration'] and sm.valid['extrinsicsCalibration'] and
          0 < stamp <= now and 0 < receipt <= now):
    return calibration_lifecycle.observe(None, current=False)
  return calibration_lifecycle.observe(sm['extrinsicsCalibration'], current=True)

def _source_config_binding(source):
  return (source.cp_sha256, source.source_epoch, source.reset, source.calibration_generation,
          source.enabled, source.manual, source.persistent_source_epoch, tuple(source.manual_baseline))


def _source_config_known(source, now_ns):
  import math
  from openpilot.starpilot.flm.runtime_adapter_registry import SourceState
  return bool(isinstance(source, SourceState) and type(source.configuration_known) is bool and source.configuration_known and
    type(source.enabled) is bool and type(source.manual) is bool and
    type(source.observed_ns) is int and type(source.expires_ns) is int and
    0 < source.observed_ns <= now_ns <= source.expires_ns <= source.observed_ns + SOURCE_LEASE_NS and
    type(source.cp_sha256) is str and len(source.cp_sha256) == 64 and
    not source.cp_sha256.strip('0123456789abcdef') and
    type(source.reset) is str and 1 <= len(source.reset) <= 64 and
    not source.reset.strip('0123456789abcdef') and
    type(source.calibration_generation) is str and (not source.calibration_generation or
      len(source.calibration_generation) == 32 and not source.calibration_generation.strip('0123456789abcdef')) and
    type(source.persistent_source_epoch) is str and (not source.persistent_source_epoch or
      len(source.persistent_source_epoch) == 64 and not source.persistent_source_epoch.strip('0123456789abcdef')) and
    type(source.manual_baseline) is tuple and len(source.manual_baseline) in (0, 3) and
    all(type(value) in (int, float) and math.isfinite(value) for value in source.manual_baseline))


def _pause_historical_collector(collector, reason):
  # Preserve only bounded, originally admitted historical storage/replay. The
  # partial packet/current joins and every current fit/physical lease are gone.
  collector.raw_points.clear()
  if hasattr(collector, '_pending_storage'):
    collector._pending_storage = ()
    collector._pending_storage_lineage = None
  collector.split(reason)
  collector.fit_result = collector.fit_validation = collector.result = collector.last_validation = None
  collector.last_qualified_response = 0
  collector.adapter.fit_profile = None
  collector.adapter.current_physical_valid = False
  collector.adapter.last_physical_ns = 0
  collector._physical_pose_queue = ()
  collector._physical_pose_context = None
  replay = getattr(collector.adapter, 'replay', None)
  if replay is not None:
    # Only a genuine next checkpoint may restart frame recording after a pause.
    replay.failed = True
    replay.cached_validation = replay.cached_key = None


def _early_tracking_offer(sm, cp, collector, actor, source, *, now_ns, clock):
  """Renew an existing source-only trial from real new motion before FIFO work."""
  if (collector is None or not collector.ready or actor is None or
      getattr(actor, 'source_tracking', False) is not True or actor.ingress is None or
      actor.certificate is None or actor.restore_pending or source is None or
      not source.enabled or source.manual or not _source_config_known(source, now_ns)):
    return None
  from openpilot.starpilot.flm.native_health import current_native_health
  if not current_native_health(sm, cp, collector.ready[-1][0], now_ns):
    return None
  from openpilot.starpilot.flm.source_plant_runtime import observe
  from openpilot.starpilot.flm.live_tracking_response import validate_response_witness
  observe(sm, cp, collector, now_ns, True)
  current = clock()
  offer = actor._current_publication_offer(current)
  if offer is None:
    return None
  pose = validate_response_witness(offer.response_witness, actor.certificate.binding, current).last_pose_ns
  if pose <= actor.last_publication_pose_ns:
    return None
  actor.last_publication_pose_ns = pose
  return offer


def _pending_pose_bracket_ready(collector, control, now_ns):
  """Scheduling hint only; the full original pose/native admission still runs."""
  from openpilot.starpilot.flm.original_source_capture import MAX_GAP_NS
  queue = getattr(collector, '_original_source_pose_queue', ())
  if not queue:
    return False
  pose = queue[0]
  stamp = getattr(pose, 'mono_ns', 0)
  upper = getattr(pose, 'source_upper_ns', 0) or stamp
  state = getattr(control, 'state_ns', 0)
  return (type(stamp) is int and type(upper) is int and type(state) is int and
          0 < stamp <= now_ns <= stamp + MAX_GAP_NS and 0 < upper <= state)


def tracking_message(source, *, now_ns, current_native, offer=None, restore=None):
  """Serialize existing owned evidence; never renew a trial or physical lease."""
  from openpilot.starpilot.flm.live_trial_wire import write_tracking_source, write_trial, write_restore
  message = messaging.new_message('starpilotSteeringTracking')
  message.valid = True
  write_tracking_source(message, source)
  current = bool(current_native is True and _source_config_known(source, now_ns) and source.enabled and not source.manual)
  if current and offer is not None and offer.observed_ns <= now_ns <= offer.expires_ns:
    write_trial(message, offer)
  elif current and restore is not None and restore.observed_ns <= now_ns <= restore.expires_ns:
    write_restore(message, restore)
  return message


class _LiveHistoryRestore:
  """Historical target only; fresh measured response and actual owner ack gate reuse."""
  def __init__(self, priors):
    self.priors = tuple(priors)
    self.response_recovery_generation = 0
    self.blocked_seed = None
    self.seed = self.prior = self.requalifier = self.offer = None
    self.restored = False
    self.last_pose_ns = self.last_publication_pose_ns = 0

  def reset(self):
    self.seed = self.prior = self.requalifier = self.offer = None
    self.restored = False
    self.last_pose_ns = self.last_publication_pose_ns = 0

  def observe_response_recovery(self, actor):
    generation = getattr(actor, 'response_recovery_generation', 0)
    if type(generation) is not int or generation <= self.response_recovery_generation:
      return False
    self.response_recovery_generation = generation
    # Quarantine ONLY this drive/source attempt; retain the immutable store.
    # reset() deliberately preserves this block across collector interruptions.
    self.blocked_seed = actor.seed if actor.seed is not None else self.seed
    self.reset()
    actor.hold_restore(False)
    return True

  @staticmethod
  def _pair(collector, pose, lag_ns) -> tuple[ControlEvidence, AppliedEvidence] | None:
    lower = tuple(pair for pair in collector.ready if pair[1].apply_ns <= pose.source_lower_ns-lag_ns)
    upper = tuple(pair for pair in collector.ready if pair[1].apply_ns <= pose.source_upper_ns-lag_ns)
    if not lower or not upper or lower[-1][1].apply_ns != upper[-1][1].apply_ns:
      return None
    return lower[-1]

  def _retry_local_scope(self, actor, collector, seed):
    """A NEW fresh local experiment; never expand an issued certificate.

    Only an unfinished historical restore may retry on input-domain departure.
    Residual failures, changed contexts/native denial and live trials retain
    their original fail-closed/quarantine paths. No monitor rows, command,
    lease, target acknowledgement or current permission cross this boundary.
    """
    from openpilot.starpilot.flm.live_tracking_requalification import RetainedResponseRequalifier, VERSION
    if (self.restored or not actor.restore_pending or self.prior is None or self.requalifier is None or
        actor.ingress is None or actor.certificate is None or actor.seed != seed or
        actor.ingress.optimizer.offer_state is not None or
        actor.ingress.monitor.reason != 'response_command_outside_identified_range' or
        actor.certificate.provenance != VERSION or
        actor.certificate.parent_model_revision != self.prior.certificate.model_revision):
      return False
    actor.interrupt('retained_local_input_scope_left', context_changed=True)
    actor.ingress = actor.certificate = actor.application = actor.initial_basis = None
    collector.live_tracking = None
    self.offer = None
    self.requalifier = RetainedResponseRequalifier(self.prior.certificate,self.prior.response_basis,seed)
    capture = collector.original_source_capture
    if capture is not None and capture.rows:
      self.last_pose_ns = max(self.last_pose_ns,capture.rows[-1].mono_ns)
    actor.hold_restore(True)
    return True

  def advance(self, actor, collector, source, *, now_ns, current_native, native_key, hardware_inventory):
    if collector is None or source is None or not collector.ready or current_native is not True:
      if self.requalifier is not None:
        self.requalifier.interrupt('retained_current_source_or_native_unavailable')
      return None
    from openpilot.starpilot.flm.live_trial_store import LiveConfirmedPrior, stable_binding, scope
    try:
      seed = actor.binding_for(collector, source)
      if self.blocked_seed is not None:
        if seed == self.blocked_seed:
          self.seed = seed
          actor.hold_restore(False)
          return None
        self.blocked_seed = None  # Actual source binding changed, not a clock.
      if seed != self.seed:
        self.reset()
        actor.hold_restore(False)
        self.seed = seed
        matches = tuple(prior for prior in self.priors if type(prior) is LiveConfirmedPrior and
          prior.historical_only is True and stable_binding(prior.binding) == stable_binding(seed))
        if len(matches) == 1:
          self.prior = matches[0]
          actor.hold_restore(True)
      if self.prior is None:
        return None
      self._retry_local_scope(actor, collector, seed)
      if actor.ingress is None and self.prior.response_basis is not None:
        from openpilot.starpilot.flm.live_tracking_requalification import RetainedResponseRequalifier
        if self.requalifier is None:
          self.requalifier = RetainedResponseRequalifier(self.prior.certificate, self.prior.response_basis, seed)
        capture = collector.original_source_capture
        if capture is not None and capture.rows:
          pose = capture.rows[-1]
          if pose.mono_ns > self.last_pose_ns:
            self.last_pose_ns = pose.mono_ns
            pair = self._pair(collector, pose, self.prior.certificate.lag_ns)
            if pair is not None:
              accepted = self.requalifier.observe(*pair, pose, now_ns=now_ns, current_native=current_native,
                native_key=native_key, hardware_inventory=hardware_inventory)
              if accepted and self.requalifier.persistent_residual_failure(now_ns):
                # No adopted ingress exists yet. This fresh physical failure
                # quarantines ONLY the current historical restore attempt.
                self.blocked_seed = seed
                self.reset()
                actor.hold_restore(False)
                return None
              if self.requalifier.reason == 'measured_calibration_vm_or_hardware_changed':
                # Keep the historical store, but do not retry an incompatible
                # basis forever or suppress genuine fresh calibration.
                self.prior = self.requalifier = self.offer = None
                actor.hold_restore(False)
                return None
            qualified = self.requalifier.qualified(now_ns)
            if qualified is not None:
              certificate, _ = qualified
              # A fresh ingress collects its OWN residual window before restore
              # publication; no retained monitor rows or permission are copied.
              actor.adopt_response(certificate, collector)
      if actor.ingress is None:
        return None  # Missing/incompatible basis honestly needs fresh bootstrap.
      witness = actor.ingress.monitor.witness(now_ns)
      if witness is None:
        return None
      from openpilot.starpilot.flm.live_tracking_response import validate_response_witness
      raw = validate_response_witness(witness, actor.certificate.binding, now_ns)
      if raw.last_pose_ns <= self.last_publication_pose_ns:
        return self.offer
      self.last_publication_pose_ns = raw.last_pose_ns
      from openpilot.starpilot.flm.live_trial_contract import LiveRestoreOffer, RESTORE_VERSION, MAX_LEASE_NS
      actor.ingress.optimizer.publication_sequence += 1
      self.offer = LiveRestoreOffer(RESTORE_VERSION, actor.certificate.binding, scope(self.prior.binding),
        self.prior.trial_id, self.prior.target, self.prior.target_sha256,
        actor.ingress.optimizer.publication_sequence, now_ns, min(now_ns+MAX_LEASE_NS, raw.valid_until_ns), witness).validate()
      return self.offer
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
      # An incompatible historical model never gets a fabricated current basis.
      self.requalifier = None
      return None

  def acknowledge(self, actor, application, *, now_ns):
    if self.offer is None or not actor.observe_restored(application, self.offer, now_ns=now_ns):
      return False
    self.restored = True
    actor.hold_restore(False)
    return True


def run(
  CP,
  sm,
  control_socket,
  applied_socket,
  pm,
  adapter_for_evidence,
  physical_observer,
  source_reader,
  collection_keys,
  objective_key,
  decode_fit_result,
  persistence,
  fitter,
  adapter_for_ford,
  *,
  clock=time.monotonic_ns,
  reporter=None,
  publication_source_reader: Callable[[int], SourceState | None] | None = None,
  live_actor=None,
  live_persistence=None,
  confirmed_history=(),
):
  from openpilot.starpilot.flm.native_health import current_native_health

  collector = None
  source = None
  last_accepted_source = None
  source_rejected = False
  source_cp_sha256 = None
  raw_context = active_key = pending_key = pending_token = None
  cached_reports = {}
  prior_attached = {}
  next_publish_ns = publication_sequence = 0
  unavailable_reason = 'no_current_controller_evidence'
  retained_event = pending_event = None
  live_offer = None
  previous_tracking_pose_header = 0
  next_tracking_status_ns = 0
  study_health_control = None
  saved_live_targets = set()
  live_history = _LiveHistoryRestore(confirmed_history)
  import gc
  gc_initially_enabled = gc.isenabled()
  trial_gc_deferred = False

  def clear(reason):
    nonlocal study_health_control
    nonlocal collector, raw_context, active_key, pending_key, pending_token, unavailable_reason, retained_event, pending_event, live_offer
    unavailable_reason = reason
    retained_event = pending_event = None
    if collector is not None:
      if reporter is not None:
        reporter.reset(reason)
      collector.split(reason, reset=True)
    study_health_control = None
    collector = None
    cached_reports.clear()
    prior_attached.clear()
    raw_context = active_key = pending_key = pending_token = None
    if fitter is not None:
      fitter.cancel(reason)
    if live_actor is not None:
      live_actor.interrupt(reason, context_changed=True)
      live_offer = None
      live_history.reset()

  def uncertain(reason):
    nonlocal unavailable_reason, retained_event, active_key, pending_key, pending_token, pending_event, live_offer
    unavailable_reason, retained_event, pending_event = reason, None, None
    if collector is not None:
      _pause_historical_collector(collector, reason)
    cached_reports.clear()
    active_key = pending_key = pending_token = None
    if fitter is not None:
      fitter.cancel(reason)
    if live_actor is not None:
      live_actor.interrupt(reason)
      live_offer = None

  source_delivery = _SourceDelivery()

  def source_current(*, read=False, publication=False):
    nonlocal source, source_rejected, source_cp_sha256, last_accepted_source
    now = clock()
    if source_rejected and not read:
      return False, now
    if read or source is None or not source.observed_ns <= now <= source.expires_ns:
      if not read:
        source_delivery.refresh(sm)
        now = clock()
      if publication:
        source_delivery.refresh(sm)
        now = clock()
      reader = publication_source_reader if publication else source_reader
      source = cast(Callable[[int], SourceState | None], reader)(now)
      now = clock()
    known = _source_config_known(source, now)
    if known and (not source.enabled or source.manual):
      source_rejected = True
      clear('manual_profile_override' if source.manual else 'learning_disabled')
      last_accepted_source = None
      return False, now
    valid = bool(known and source.enabled and not source.manual and
      type(source.source_epoch) is str and len(source.source_epoch) == 64 and
      not source.source_epoch.strip('0123456789abcdef') and
      (source_cp_sha256 is None or source.cp_sha256 == source_cp_sha256))
    if not valid:
      source_rejected = True
      if known and source_cp_sha256 is not None and source.cp_sha256 != source_cp_sha256:
        clear('actual_source_identity_changed')
      else:
        uncertain('settings_receipt_expired' if source is not None and type(source.expires_ns) is int and source.expires_ns < now
                  else 'source_configuration_uncertain')
      return False, now
    previous = last_accepted_source
    if previous is not None and _source_config_binding(previous) != _source_config_binding(source):
      clear('actual_source_identity_changed')
      source_rejected = True
      if previous.source_epoch != source.source_epoch:
        last_accepted_source = source
      return False, now
    last_accepted_source = source
    source_cp_sha256 = source.cp_sha256
    source_rejected = False
    return True, now

  def study_native_current(now_ns):
    return study_health_control is not None and current_native_health(sm, CP, study_health_control, now_ns)

  tracking_needs_native = False

  def current_tracking_offer():
    return live_offer

  def renew_tracking(*, refresh=False):
    nonlocal live_offer, next_tracking_status_ns, tracking_needs_native
    if live_offer is None or live_actor is None or live_history.offer is not None:
      tracking_needs_native = False
      return False
    if refresh:
      source_delivery.refresh(sm)
      if (not sm.updated['deviceMotion'] and not tracking_needs_native and
          not getattr(collector, '_original_source_pose_queue', ())):
        return False
    valid, stamp = source_current(read=refresh)
    if not valid or collector is None:
      tracking_needs_native = False
      return False
    tracking_needs_native = not (collector.ready and
      current_native_health(sm, CP, collector.ready[-1][0], stamp))
    if tracking_needs_native:
      return False
    early = _early_tracking_offer(sm, CP, collector, live_actor, source, now_ns=stamp, clock=clock)
    if early is None:
      return False
    live_offer = early
    valid, stamp = source_current()
    native = bool(valid and collector is not None and collector.ready and
      current_native_health(sm, CP, collector.ready[-1][0], stamp))
    message = tracking_message(source, now_ns=stamp, current_native=native, offer=live_offer)
    if live_offer is not None and int(message.starpilotSteeringTracking.trial.version) == 1:
      live_actor.mark_publication(live_offer)
    pm.send('starpilotSteeringTracking', message)
    next_tracking_status_ns = stamp + 50_000_000
    return True

  class Sink:
    def split(self, reason, **kwargs):
      if collector is not None:
        collector.split(reason, **kwargs)

    def overrun(self):
      if collector is not None:
        collector.overrun()

  sink = Sink()
  drain_state = PairedDrainState(control_socket, applied_socket)
  try:
    while True:
      state = (live_actor.ingress.optimizer.offer_state
               if live_actor is not None and live_actor.ingress is not None else None)
      defer_gc = bool(gc_initially_enabled and state is not None and state['phase'] == 'trial' and
                      not state.get('accepted', False) and clock() <= state['deadline_ns'])
      if defer_gc and not trial_gc_deferred:
        # This separate optional process must receive the real 100Hz tape during
        # the existing bounded trial. Reference counting continues normally;
        # cyclic collection resumes on rollback, completion or the same deadline.
        gc.disable()
        trial_gc_deferred = True
      elif trial_gc_deferred and not defer_gc:
        gc.enable()
        trial_gc_deferred = False
      # Parent handles bounded primitive ingest and live receipts. Physical
      # calibration and native sensitivity execute in bounded separate children.
      pending_poses = cast(tuple[object, ...], getattr(collector, '_original_source_pose_queue', ()))
      pending_fresh = False
      if pending_poses:
        from openpilot.starpilot.flm.original_source_capture import MAX_GAP_NS
        pending_stamp = getattr(pending_poses[0], 'mono_ns', 0)
        pending_now = clock()
        pending_fresh = type(pending_stamp) is int and 0 < pending_stamp <= pending_now <= pending_stamp + MAX_GAP_NS
      delivered_pose_header = (source_delivery.next(sm, pending_evidence=(control_socket, applied_socket))
                               if pending_fresh else source_delivery.next(sm))
      source_rejected = False
      now = clock()
      # This is an ORIGINAL header from the real poll or an already consumed
      # final refresh. Never wait for another pose while this one is pending.
      tracking_pose_header = delivered_pose_header if live_actor is not None else 0
      if not 0 < tracking_pose_header <= now:
        tracking_pose_header = 0
      source_valid, now = source_current(read=True)
      if source_valid and collector is None:
        unavailable_reason = 'no_current_controller_evidence'

      if collector is not None:
        # Geometry arrives at100Hz independently of the actual native emission
        # cadence. This Boolean is current owner health, never a fabricated CAN.
        previous = collector.ready[-1][0] if collector.ready else None
        if previous is not None:
          study_health_control = previous
        collector._learning_current_native_allowed = bool(source_valid and previous is not None and
          current_native_health(sm, CP, previous, now))

      # An existing trial can renew before FIFO work or as soon as its
      # missing original bracket/native pair is admitted within that work.
      if tracking_pose_header > 0 and renew_tracking():
        previous_tracking_pose_header = tracking_pose_header

      native_refreshed = False

      def ingest_current(evidence):
        nonlocal native_refreshed
        valid_source, stamp = source_current()
        if (valid_source and evidence is not None and not native_refreshed and
            not current_native_health(sm, CP, evidence, stamp)):
          # A queued native delivery may already revoke or renew the received
          # state. Consume it once for this bounded batch before excluding an
          # original tick; the final post-drain refresh remains mandatory.
          native_refreshed = True
          source_delivery.refresh(sm)
          valid_source, stamp = source_current(read=True)
        return valid_source, stamp

      def control(event, current, stamp):
        nonlocal collector, raw_context, active_key, unavailable_reason
        valid_source, received_now = source_current()
        if not valid_source:
          return
        if event.which() == 'starpilotLateralState' and event.starpilotLateralState.learningEvidence.version == 0:
          if collector is not None:
            collector.split('learning_evidence_delayed' if event.starpilotLateralState.active and
              study_native_current(received_now)
              else 'no_current_learning_evidence')
          return
        evidence = read_control(event)
        if (
          not source.enabled
          or source.manual
          or evidence.source_epoch != source.source_epoch
          or not 0 < evidence.source_receipt_ns <= evidence.control_ns <= received_now <= source.expires_ns
        ):
          clear('learning_disabled' if not source.enabled else 'manual_profile_override' if source.manual else 'controller_source_receipt_mismatch')
          return
        incoming = (
          evidence.identity,
          evidence.generation,
          evidence.domain,
          evidence.stage_revision,
          evidence.response_revision,
          evidence.source_epoch,
          evidence.controller_mode,
          evidence.policy,
          evidence.baseline_revision,
          evidence.controller_revision,
          evidence.turn_assist,
        )
        if incoming != raw_context:
          clear('actual_controller_identity_changed')
          raw_context = incoming
        if reporter is not None:
          reporter.control = (event, evidence)
        keys = tuple(collection_keys(evidence))
        if not 0 < len(keys) <= MAX_OBJECTIVES or len(set(keys)) != len(keys):
          clear('objective_registry_bound')
          return
        active_key = objective_key(evidence)
        if active_key not in keys:
          clear('current_objective_unavailable')
          return
        if collector is None:
          adapter = adapter_for_evidence(CP, evidence, active_key)
          if adapter is None:
            unavailable_reason = 'actual_adapter_unavailable'
            return
          if getattr(adapter, 'event_collection', False):
            from openpilot.starpilot.flm.event_collector import EventFullrateCollector
            collector = EventFullrateCollector(adapter)
            if getattr(adapter,'source_plant_protocol',None) is not None:
              from openpilot.starpilot.flm.source_plant_runtime import start_original_capture
              if not start_original_capture(collector):
                unavailable_reason = adapter.source_plant_capture_error
                if live_actor is not None:
                  # Preserve the actual startup refusal instead of the previous identity-reset status.
                  live_actor.reason = unavailable_reason
                  from openpilot.starpilot.flm.source_stall_log import log_source_stall
                  log_source_stall(live_actor, collector, now_ns=received_now, bootstrap_allowed=True, sm=sm)
                collector = None
                return
          else:
            collector = FullrateCollector(adapter)
        if hasattr(collector.adapter, 'bind_source'):
          collector.adapter.bind_source(source)
        collector._calibration_generation = source.calibration_generation
        collector.adapter.select_objective(active_key)
        # This records primitive actual frames/checkpoints ONLY. No selected
        # controller update, baseline probes or numerical fitting at100Hz.
        # Feedback describes the original controller consumption, including
        # context-only frames that cannot become fitting samples. Preserve its
        # exact decoded wire evidence before stage eligibility can decline it.
        if getattr(collector.adapter,'original_feedback_archive',None) is not None:
          from openpilot.starpilot.flm.feedback_archive import record_companion
          record_companion(collector,event,received_now,control=evidence)
        prepared = collector.adapter.prepare_control(evidence)
        if prepared is None:
          collector.split('actual_stage_capture_unqualified')
        else:
          # Re-evaluate current native admission against THIS arriving control.
          # The pre-drain reference can have expired while fresh controls were
          # queued; inheriting its Boolean rejects the fresh batch as well.
          # No old receipt or deadline is renewed by this current check.
          if live_actor is not None:
            original_collector = collector
            valid_source, received_now = ingest_current(prepared)
            if not valid_source or collector is not original_collector:
              return
          admitted = _collect_source_control(collector, prepared, sm, CP, received_now)
          # A renewal can occur inside this batch; decode against the current owned offer.
          offered = current_tracking_offer()
          if live_actor is not None and offered is not None:
            from openpilot.starpilot.flm.live_trial_wire import read_application
            application = read_application(event.starpilotLateralState, now_ns=received_now, offer=offered)
            if application is not None and current_native_health(sm, CP, evidence, received_now):
              live_actor.observe_application(application, now_ns=received_now)
          elif live_actor is not None and live_history.offer is not None:
            from openpilot.starpilot.flm.live_trial_wire import read_restore_application
            application = read_restore_application(event.starpilotLateralState, now_ns=received_now, offer=live_history.offer)
            if application is not None and current_native_health(sm, CP, evidence, received_now):
              live_history.acknowledge(live_actor, application, now_ns=received_now)
          if live_actor is not None and offered is not None and admitted:
            # Motion may arrive while the original FIFO is still draining.
            renew_tracking(refresh=True)

      def output(event, current, stamp):
        valid_source, received_now = source_current()
        if not valid_source:
          return
        if collector is not None:
          if getattr(collector.adapter,'original_feedback_archive',None) is not None:
            from openpilot.starpilot.flm.feedback_archive import record_companion
            record_companion(collector,event,received_now)
          if event.which() == 'starpilotCarState' and event.starpilotCarState.appliedEvidence.version == 0:
            collector.split('no_current_applied_evidence')
            return
          applied = read_applied(event)
          consumed = None
          if live_actor is not None:
            original_collector = collector
            consumed = collector.controls.get(applied.control_ns, collector.recent_controls.get(applied.control_ns))
            valid_source, received_now = ingest_current(consumed)
            if not valid_source or collector is not original_collector:
              return
          previous_pair = collector.ready[-1] if live_actor is not None and collector.ready else None
          _collect_applied_output(collector, applied, sm, CP, received_now)
          if (live_actor is not None and consumed is not None and collector.ready and collector.ready[-1] is not previous_pair and
              (tracking_needs_native or _pending_pose_bracket_ready(collector, consumed, clock()))):
            renew_tracking()

      drain_pair(control_socket, control, applied_socket, output, messaging.recv_one_or_none, sink, now, drain_state)
      if collector is not None:
        # Decoding queued frames may outlast the sampled shared inputs. Consume
        # real pending deliveries before pose/native checks; keep their original
        # headers and receipts for the next iteration instead of polling twice.
        source_delivery.refresh(sm)
      source_valid, now = source_current(read=collector is not None)
      if tracking_pose_header < source_delivery.pose_header <= now:
        tracking_pose_header = source_delivery.pose_header
      current_native = False
      if source_valid and collector is not None and collector.ready:
        current_native = current_native_health(sm, CP, collector.ready[-1][0], now)
        collector._learning_current_native_allowed = current_native
        if getattr(collector.adapter,'source_plant_protocol',None) is not None:
          from openpilot.starpilot.flm.source_plant_runtime import observe
          observe(sm,CP,collector,now,current_native)
        else:
          physical = physical_observer(sm, CP, collector, now)
          from openpilot.starpilot.flm.physical_observation import PendingPhysical
          if not current_native or physical is None:
            collector.split('physical_or_native_witness_unavailable')
          elif type(physical) is PendingPhysical:
            if not physical.fresh(now):
              collector.split('physical_or_native_witness_unavailable')
            elif physical.context_changed:
              # Discard prior current admission on a lineage/transform boundary;
              # waiting for new-context data must not restart its own queue.
              collector.last_qualified_response = 0
              collector.adapter.current_physical_valid = False
              collector.adapter.last_physical_ns = 0
            # Pending data is not physical evidence: no timestamp, model,
            # last_qualified_response or current admission is renewed here.
          else:
            collector.physical(physical, now)

      source_valid, now = source_current()
      current_native = bool(source_valid and collector is not None and collector.ready and
                            current_native_health(sm, CP, collector.ready[-1][0], now))
      token = ((raw_context, pending_key, collector.physical_context, collector.adapter.envelope.prior_full_profile_sha256)
               if getattr(collector.adapter, 'event_collection', False) else (raw_context, pending_key, collector.physical_context)) \
              if collector is not None and collector.ready and current_native else None
      if live_actor is None:
        report = fitter.poll(token=token, now_ns=now)
        if report is not None:
          decoded = decode_fit_result(report)
          if source_valid and token is not None and decoded is not None and collector is not None and token == pending_token:
            if reporter is not None:
              reporter.fitted(decoded[0], pending_key)
            if len(cached_reports) < MAX_CACHED_PROFILES or pending_key in cached_reports:
              cached_reports[pending_key] = (report, decoded, token)
              if getattr(collector.adapter, 'event_collection', False) and decoded[0].profile is not None:
                # A completed proposal owns its fitting objective independently
                # from the rotating capture selector. This is not live admission.
                pending_event = (cached_reports[pending_key], pending_key)
          pending_key = pending_token = None
        if fitter.child is None:
          pending_key = pending_token = None
          if collector is not None and current_native and active_key is not None:
            collector.adapter.select_objective(active_key)
            if collector.context is not None:
              scope = (collector.context[0], active_key, source.reset)
              cached_prior = prior_attached.get(scope)
              if cached_prior is None or cached_prior[0] != tuple(collector.adapter.parameters()):
                persistence.attach_prior(collector, identity=collector.context[0], source_epoch=source.source_epoch, reset=source.reset)
                prior_attached[scope] = (tuple(collector.adapter.parameters()), collector.prior_coefficients)
              else:
                collector.restore_prior(cached_prior[1])
            snapshot = collector.job_snapshot(now)
            if snapshot is not None:
              if getattr(collector.adapter,'source_plant_protocol',None) is not None:
                from openpilot.starpilot.flm.source_plant_runtime import submission_key
                submitted_key = submission_key(snapshot,collector,active_key)
              else:
                submitted_key = active_key
              token = ((raw_context, submitted_key, collector.physical_context, collector.adapter.envelope.prior_full_profile_sha256)
                       if getattr(collector.adapter, 'event_collection', False) else (raw_context, submitted_key, collector.physical_context))
              valid_source, now = source_current()
              current_native = bool(valid_source and collector is not None and collector.ready and
                                    current_native_health(sm, CP, collector.ready[-1][0], now))
              if valid_source and current_native and collector is not None and fitter.submit(snapshot, token=token, now_ns=now):
                if reporter is not None:
                  reporter.fit_attempts += 1
                collector.mark_job_submitted(now)
                pending_key, pending_token = submitted_key, token

      source_valid, now = source_current()
      current_native = bool(source_valid and collector is not None and collector.ready and
                            current_native_health(sm, CP, collector.ready[-1][0], now))
      if live_actor is not None:
        from openpilot.starpilot.flm.runtime_adapter_registry import current_authority, native_identity_key
        native_key = hardware_inventory = ()
        if current_native and collector is not None and collector.ready:
          control_evidence = collector.ready[-1][0]
          if control_evidence.native_required:
            allowed, actual_native, _ = current_authority(sm, CP, now_ns=now, required=True)
            if allowed:
              native_key = native_identity_key(actual_native)
          else:
            hardware_inventory = tuple((index, str(panda.pandaType)) for index, panda in enumerate(sm['pandaStates']))
        # Collection status carries no trial or restore authority. Send its
        # independently paced source receipt before optional snapshot work can
        # delay delivery. This neutral status is not a new physical witness;
        # the original settings lease and all trial/restore leases stay fixed.
        if (source_valid and live_offer is None and live_history.offer is None and
            not (publication_source_reader is not None and now >= next_publish_ns) and
            now + 10_000_000 >= next_tracking_status_ns):
          pm.send('starpilotSteeringTracking', tracking_message(source, now_ns=now,
            current_native=False, offer=None, restore=None))
          if next_tracking_status_ns == 0:
            next_tracking_status_ns = now
          next_tracking_status_ns += max(1, (now - next_tracking_status_ns) // 50_000_000 + 1) * 50_000_000
          previous_tracking_pose_header = max(previous_tracking_pose_header, tracking_pose_header)
        prior_restore_offer = live_history.offer
        if live_offer is None:
          live_history.advance(live_actor, collector, source if source_valid else None, now_ns=now,
            current_native=current_native, native_key=native_key, hardware_inventory=hardware_inventory)
        latest = live_actor.tick(collector, source if source_valid else None, now_ns=now,
          current_native=current_native, native_key=native_key, hardware_inventory=hardware_inventory,
          allow_bootstrap=live_history.requalifier is None,
          evidence_delay_only=bool(source_valid and study_native_current(now)))
        if live_history.observe_response_recovery(live_actor):
          # Clear cached trial/restore publication in this SAME parent tick.
          # No old history or accepted target is silently promoted afterward.
          live_offer = latest = None
        if latest is not None:
          live_offer = latest
          live_history.offer = None
        # Publish when a genuine qualified physical witness becomes available,
        # including when its raw camera header arrived in an earlier loop.
        # Both owners already gate offers on a strictly newer physical pose.
        # Waiting for another raw header can spend an additional camera period
        # from the original witness lease. Never restamp or renew an old offer.
        new_raw_pose = tracking_pose_header > previous_tracking_pose_header
        if new_raw_pose:
          previous_tracking_pose_header = tracking_pose_header
        # Optional work may have consumed the original witness/permission
        # deadline. A proposal must face the actual post-work clock and native
        # checks, never the time captured before that work started.
        source_valid, now = source_current()
        current_native = bool(source_valid and collector is not None and collector.ready and
                              current_native_health(sm, CP, collector.ready[-1][0], now))
        new_restore = live_history.offer is not None and live_history.offer is not prior_restore_offer
        # A genuinely newer qualified witness must reach its consumer now,
        # preserving its original short lease. Raw-pose delivery alone does not
        # renew that witness and must not create a second publication stream.
        # After a proposal expires, independently paced neutral status keeps
        # source health recoverable without granting any current correction.
        current_proposal = any(value is not None and value.observed_ns <= now <= value.expires_ns
          for value in (live_offer, live_history.offer))
        defer_idle_refresh = (publication_source_reader is not None and now >= next_publish_ns and
                              live_offer is None and live_history.offer is None)
        idle_status = source_valid and not current_proposal and not defer_idle_refresh and now + 10_000_000 >= next_tracking_status_ns
        source_withdrawal = not source_valid and _source_config_known(source, now)
        publish_tracking = latest is not None or new_restore or idle_status or new_raw_pose and source_withdrawal
        if publish_tracking:
          message = tracking_message(source, now_ns=now, current_native=current_native,
            offer=live_offer, restore=live_history.offer)
          if (live_offer is not None and int(message.starpilotSteeringTracking.trial.version) == 1 and
              hasattr(live_actor, 'mark_publication')):
            live_actor.mark_publication(live_offer)
          pm.send('starpilotSteeringTracking', message)
          if latest is not None or new_restore:
            next_tracking_status_ns = now + 50_000_000
          else:
            if next_tracking_status_ns == 0:
              next_tracking_status_ns = now
            next_tracking_status_ns += max(1, (now - next_tracking_status_ns) // 50_000_000 + 1) * 50_000_000
        if live_persistence is not None:
          inputs = live_actor.persistence_inputs(now)
          if inputs is not None:
            confirmed_offer, application, certificate = inputs
            saved_key = (confirmed_offer.binding, confirmed_offer.trial_id, confirmed_offer.target_sha256)
            if saved_key not in saved_live_targets:
              def save_authorized(expected_certificate=certificate):
                valid, check_now = source_current(read=True, publication=True)
                return bool(valid and collector is not None and collector.ready and
                  current_native_health(sm, CP, collector.ready[-1][0], check_now) and
                  live_actor.certificate is expected_certificate)
              if live_persistence.save_confirmed(confirmed_offer, application, certificate,
                  now_ns=now, current_binding=lambda: live_actor.certificate.binding if live_actor.certificate is not None else None,
                  authorized=save_authorized, response_basis=live_actor.initial_basis):
                saved_live_targets.add(saved_key)
                if len(saved_live_targets) > 64:
                  saved_live_targets.pop()

      if now >= next_publish_ns:
        if next_publish_ns == 0:
          next_publish_ns = now
        publication_sequence += 1
        if publication_source_reader is not None:
          source_valid, now = source_current(read=True, publication=True)
          # Forward the genuine settings read in the existing neutral-status
          # slot. An extra4Hz stream can exceed Controls' rolling frequency
          # bound. If earlier work crossed the publication deadline after a
          # status was already sent, preserve its next slot instead of doubling.
          if (source_valid and live_actor is not None and live_offer is None and live_history.offer is None and
              now + 10_000_000 >= next_tracking_status_ns):
            pm.send('starpilotSteeringTracking', tracking_message(source, now_ns=now,
              current_native=False, offer=None, restore=None))
            if next_tracking_status_ns == 0:
              next_tracking_status_ns = now
            next_tracking_status_ns += max(1, (now - next_tracking_status_ns) // 50_000_000 + 1) * 50_000_000
        if live_actor is not None:
          message = result_message(None, 'live-tracking', publication_sequence, now)
          value = message.autoSteeringLearning.learning
          value.status, value.reason = 'collecting', live_actor.reason
        elif source_valid and collector is not None and active_key is not None and raw_context is not None:
          cached = cached_reports.get(active_key)
          collector.fit_result = collector.fit_validation = None
          if getattr(collector.adapter, 'event_collection', False):
            if pending_event is not None:
              candidate, candidate_key = pending_event
              token_context = collector.physical_context
              if getattr(collector.adapter,'source_plant_protocol',None) is not None:
                from openpilot.starpilot.flm.source_plant_runtime import installed_report_context_current
                if installed_report_context_current(collector.adapter,candidate[0],collector.physical_context):
                  # Keep exact original submission custody while the installed
                  # proposal awaits a genuinely later physical/current proof.
                  token_context = candidate[2][2]
              expected = (raw_context, candidate_key, token_context,
                          collector.adapter.envelope.prior_full_profile_sha256)
              if candidate[2] != expected:
                pending_event = None
            selected_event = pending_event if pending_event is not None else retained_event
            if selected_event is not None and current_native:
              selected, selected_key = selected_event
              collector.adapter.select_objective(selected_key)
              installed = (collector.adapter.install_fit(selected[0], *selected[1], physical_context=collector.physical_context)
                           and collector.install_fit(*selected[1]))
              if not installed and selected_event is pending_event:
                pending_event = None
            collector.live_profile_sequence = max(collector.live_profile_sequence, publication_sequence)
            collector.revalidate_fit(now)
            if (selected_event is not None and collector.result is not None and
                collector.result.profile is not None):
              retained_event = selected_event
              if selected_event is pending_event:
                pending_event = None
            # While a structurally valid proposal awaits fresh physical proof,
            # do not alternate its model with the retained model each publish.
            # Same-model installation keeps the adapter's original current flags.
            # Restore only the fitting selector, not an owner or permission.
            collector.adapter.select_objective(active_key)
          else:
            if cached is not None and current_native:
              report, decoded, _ = cached
              collector.adapter.select_objective(active_key)
              if collector.adapter.install_fit(report, *decoded, physical_context=collector.physical_context):
                collector.install_fit(*decoded)
            collector.live_profile_sequence = max(collector.live_profile_sequence, publication_sequence)
            collector.revalidate_fit(now)
          publication_sequence = max(publication_sequence, collector.live_profile_sequence)
          publication_result = collector.result
          if (
            publication_result is None and cached is not None and current_native and cached[1][0].profile is None
            and cached[2] == ((raw_context, active_key, collector.physical_context,
                               collector.adapter.envelope.prior_full_profile_sha256)
                              if getattr(collector.adapter, 'event_collection', False)
                              else (raw_context, active_key, collector.physical_context))
          ):
            publication_result = cached[1][0]
          message = result_message(publication_result, collector.session, publication_sequence, now, replay_validation=collector.last_validation)
          persistence.save_current(collector, now_ns=now, source_epoch=source.source_epoch, reset=source.reset)
        else:
          message = messaging.new_message('autoSteeringLearning')
          value = message.autoSteeringLearning.learning
          value.version, value.status, value.reason, value.family = 1, 'unavailable', unavailable_reason, 'runtimeProfile'
          value.observedMonoTime, value.validUntilMonoTime = now, now + 500_000_000
          message.valid = True
        if publication_source_reader is not None:
          valid_publication_source, publication_now = source_current()
          if not valid_publication_source:
            # A reset/manual/vehicle/calibration transition must discard the
            # already prepared admission, never attach it to a new receipt.
            message = result_message(None, 'unavailable', publication_sequence, publication_now)
            message.autoSteeringLearning.learning.status = 'unavailable'
            message.autoSteeringLearning.learning.reason = 'publication_source_changed_or_unavailable'
          message.logMonoTime = publication_now
        write_source_receipt(message, source)
        pm.send('autoSteeringLearning', message)
        if reporter is not None:
          cached = cached_reports.get(active_key)
          reporter.publish(now_ns=now, message=message, collector=collector, source=source, objective=active_key,
            pending=live_actor.pending if live_actor is not None else fitter.child is not None, native_current=current_native,
            fit_result=None if cached is None else cached[1][0])
        # Preserve the original phase instead of adding poll jitter each time.
        # Work/read delays skip missed slots; one actual loop sends at most once.
        # This clock is scheduling only: source receipts and500ms leases above
        # keep their actual current read times, never a deadline/backdated time.
        publication_complete_ns = max(now, clock())
        next_publish_ns += ((publication_complete_ns-next_publish_ns)//250_000_000+1)*250_000_000
  finally:
    if trial_gc_deferred:
      gc.enable()
    reaped = True
    if live_actor is not None:
      reaped = live_actor.close('learner_parent_exit') is not False
    if fitter is not None:
      reaped = fitter.close('learner_parent_exit') is not False and reaped
    if not reaped:
      raise RuntimeError('learner child shutdown reaping unproved')


def main():
  # Imports belong only to the separate optional process. No workerthread/GIL
  # shared with realtime controller processes, no independent selection read.
  from openpilot.starpilot.flm.runtime_adapter_registry import (
    physical_observer,
    read_source,
    car_params_digest,
    collection_keys,
    objective_key,
    adapter_for_ford,
  )
  from openpilot.starpilot.flm.learner_factory import decode_fit_result
  from openpilot.starpilot.flm.persistence_module import PersistenceModule
  from openpilot.starpilot.flm.live_tracking_actor import LiveTrackingActor
  from openpilot.starpilot.flm.live_tracking_probe_process import ProbeProcess
  from openpilot.starpilot.flm.live_trial_store import LiveTrialPersistence, load_confirmed_history

  try:
    if hasattr(os, 'sched_setscheduler'):
      os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    os.setpriority(os.PRIO_PROCESS, 0, 0)
    from openpilot.starpilot.flm.learner_cpu import configure_cpu
    configure_cpu()
  except OSError:
    # An optional estimator must not inherit realtime scheduling on failure.
    return
  params = Params()
  CP = messaging.log_from_bytes(params.get('CarParams', block=True), car.CarParams)
  from opendbc.car.vin import is_valid_vin, VIN_UNKNOWN

  persistent_unit = is_valid_vin(str(CP.carVin)) and str(CP.carVin) != VIN_UNKNOWN
  services = [
    'deviceMotion',
    'extrinsicsCalibration',
    'carState',
    'pandaStates',
    'lateralDelay',
    'vehicleParameters',
    'driverMonitoringState',
    'onroadEvents',
    'aolSafetyWire',
  ]
  sm = source_receiver(services)
  control_socket = messaging.sub_sock('starpilotLateralState', conflate=False)
  applied_socket = messaging.sub_sock('starpilotCarState', conflate=False)
  pm = messaging.PubMaster(['autoSteeringLearning', 'starpilotSteeringTracking'])
  cp_sha256 = car_params_digest(CP)
  source_cache = [None]
  vehicle_session = uuid.uuid4().hex
  from openpilot.starpilot.flm.calibration_lifecycle import ProducerCalibrationLifecycle
  calibration_lifecycle = ProducerCalibrationLifecycle()

  def calibration_frame(now):
    return _configuration_calibration(sm, calibration_lifecycle, now)

  def current_source(now, frame):
    return read_source(CP, params, now_ns=now, cp_sha256=cp_sha256,
      calibration=() if frame is None else frame[1], vehicle_session=vehicle_session,
      calibration_generation='' if frame is None else frame[0])

  def source_reader(now):
    # Observe resets on every loop, but keep settings reads at receipt-renewal rate.
    frame = calibration_frame(now)
    generation = '' if frame is None else frame[0]
    if (source_cache[0] is None or generation != source_cache[0].calibration_generation or
        now - source_cache[0].observed_ns >= 250_000_000):
      source_cache[0] = current_source(now, frame)
    return source_cache[0]

  def publication_source_reader(now):
    # The regular 250ms read cache and independent 250ms publication cadence
    # can publish a receipt already330ms old; a normal next interval then
    # crosses its original500ms deadline. Read the actual snapshot here after
    # fitting/persistence work instead of extending that old observation.
    source_cache[0] = current_source(now, calibration_frame(now))
    return source_cache[0]

  def current_sources(identity, epoch):
    now = time.monotonic_ns()
    current = current_source(now, calibration_frame(now))
    return (identity if persistent_unit and epoch == current.source_epoch else '', current.enabled, current.manual, current.reset)

  from openpilot.starpilot.flm.training_report import TrainingReporter

  try:
    reporter = TrainingReporter(CP.carFingerprint)
  except (OSError, ValueError):
    reporter = None

  def current_adapter(cp, evidence, key):
    return production_adapter_for_evidence(cp, evidence, key, source=source_reader(time.monotonic_ns()))

  persistence = PersistenceModule(params, current_sources)
  import gc
  gc_was_enabled = gc.isenabled()
  if gc_was_enabled:
    # Reclaim startup cycles; temporary visitors release their own graphs.
    gc.collect()
    # Keep standard GC enabled; recursive temporary visitors release explicitly.
  try:
    run(
      CP,
      sm,
      control_socket,
      applied_socket,
      pm,
      current_adapter,
      partial(physical_observer, distinguish_pending=True),
      source_reader,
      collection_keys,
      objective_key,
      decode_fit_result,
      persistence,
      None,
      adapter_for_ford,
      reporter=reporter,
      publication_source_reader=publication_source_reader,
      live_actor=LiveTrackingActor(probe_process=ProbeProcess(),source_tracking=True),
      live_persistence=LiveTrialPersistence(params),
      confirmed_history=load_confirmed_history(params),
    )
  finally:
    # run() closes/reaps optional children before returning or propagating.
    if gc_was_enabled:
      gc.enable()


if __name__ == '__main__':
  main()
