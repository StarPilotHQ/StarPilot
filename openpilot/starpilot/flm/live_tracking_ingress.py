"""Optional learner adapter for genuine fullrate controls and physical poses.

No controller update, native encoding, file polling or process wait occurs here.
Snapshot probe work is submitted separately; absent probes hold identification.
"""
from collections import deque
from openpilot.starpilot.flm.live_tracking import LiveTrackingOptimizer
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, full_profile_sha256
from openpilot.starpilot.flm.live_tracking_episodes import TrackingEpisodeBuilder
from openpilot.starpilot.flm.live_tracking_response import ResponseMonitor


class LiveTrackingIngress:
  def __init__(self, binding, certificate, accepted_full30, *, controller_domain):
    self.binding = binding
    self.replay = None
    self.optimizer = LiveTrackingOptimizer(binding, accepted_full30, controller_domain=controller_domain,
      response_certificate=certificate)
    self.monitor = ResponseMonitor(certificate)
    self.episodes = TrackingEpisodeBuilder(binding, certificate,controller_domain=controller_domain)
    self.last_pose_ns = 0
    self.latest_offer = None
    self.scoring_enabled = True
    # Original2s-old numeric endpoint plus its actual<=1s prestate prefix.
    # This ring grants no current permission; per-job prefix remains<=100ticks.
    self.control_history = deque(maxlen=301)
    self.last_coordinates = self.last_profile = None

  def profile(self, control):
    values = control.live_applied_coordinates
    if not values:
      if control.effect_trace is not None:
        control.effect_trace.validate()
        values = tuple(dict(control.effect_trace.applied)[name] for name in NAMES)
      elif control.applied_coordinates == (0., 0.):
        values = (0.,)*30
      else:
        raise ValueError('actual complete consumed tracking coordinates unavailable')
    if type(values) is not tuple or len(values) != 30:
      raise ValueError('actual thirty hook coordinates')
    if values != self.last_coordinates:
      self.last_profile = full_profile_sha256(self.binding.baseline_revision, dict(zip(NAMES, values, strict=True)))
      self.last_coordinates = values
    return self.last_profile

  def interrupt(self, reason, *, context_changed=False, preserve_completed=True):
    self.monitor.reset(reason)
    state = self.optimizer.offer_state
    preserve = bool(preserve_completed and not context_changed and (state is None or state.get('accepted') is True))
    self.episodes.interrupt(reason, preserve_completed=preserve)
    self.optimizer.interrupt(reason, context_changed=context_changed)
    self.control_history.clear()
    self.latest_offer = None

  def _retain_completed(self):
    state = self.optimizer.offer_state
    return state is None or state.get('accepted') is True

  def observe_source_control(self, control, *, now_ns, current_native):
    try:
      profile = self.profile(control)
    except (AttributeError, TypeError, ValueError):
      self.interrupt('actual_tracking_profile_unavailable')
      return False
    accepted = self.episodes.observe_source_control(control, profile_sha256=profile,
      now_ns=now_ns, current_native=current_native, preserve_completed=self._retain_completed())
    if not accepted and self.episodes.last_input_status == 'invalid':
      self.interrupt(self.episodes.reason)
    if accepted:
      state = self.optimizer.offer_state
      settled = self.optimizer.settled_ns
      if (state is not None and state['phase'] == 'trial' and state.get('accepted') is not True and
          settled is not None and control.control_ns > settled and
          profile != self.optimizer._hash(state['target'])):
        # An actual newer consumer has left an acknowledged target. Older
        # queued controls and the initial slew are not withdrawal evidence.
        # Discard the experiment; neither its lease nor its scores can resume.
        self.interrupt('acknowledged_trial_profile_withdrawn')
        return False
      self.control_history.append(control)
    return accepted

  def observe_control(self, control, output, *, profile_sha256, now_ns, current_native):
    # EVERY joined owner tick enters here, including the ticks between poses.
    accepted = self.episodes.observe_control(control, output, profile_sha256=profile_sha256,
      now_ns=now_ns, current_native=current_native, preserve_completed=self._retain_completed())
    if not accepted and getattr(self.episodes, 'last_input_status', 'invalid') == 'invalid':
      self.interrupt(self.episodes.reason)
    return accepted

  def observe_probe(self, result, *, now_ns):
    accepted = self.episodes.observe_probe(result, now_ns=now_ns)
    witness = self.monitor.witness(now_ns)
    if witness is not None and self.optimizer.response_current(witness, now_ns=now_ns):
      for episode in self.episodes.drain(now_ns=now_ns):
        if self.scoring_enabled:
          self.optimizer.observe_episode(episode, now_ns=now_ns)
    return accepted

  def original_pose_pair(self, ready, pose):
    lag = self.optimizer.lag_ns
    lower = [(c, o) for c, o in ready if o.apply_ns <= pose.source_lower_ns - lag]
    upper = [(c, o) for c, o in ready if o.apply_ns <= pose.source_upper_ns - lag]
    if not lower or not upper:
      return None
    pair = lower[-1]
    if not 0 <= pose.source_upper_ns - lag - pair[1].apply_ns <= 30_000_000:
      return None
    if pair[1].apply_ns != upper[-1][1].apply_ns:
      if not self.episodes.source_tracking:
        return None  # A plant response still requires one unambiguous input.
      # Source-only tracking compares desired and measured acceleration at the
      # pose time, not a fitted command->response. Retain the genuine earlier
      # causal emission across clock uncertainty within one source lineage.
      # A known slow map ramp must not withdraw physical-source freshness;
      # the episode builder independently discards windows across map changes. Never interpolate a command or replace its clocks.
      binding = self.binding
      lineage = (binding.identity, binding.source_epoch, binding.baseline_revision,
                 binding.controller_revision, binding.native_identity, binding.native_required)
      try:
        self.profile(pair[0])  # Require the actual complete consumed map, not equality.
        for control, output in ready:
          if pair[1].apply_ns <= output.apply_ns <= upper[-1][1].apply_ns:
            if ((control.identity, control.source_epoch, control.baseline_revision,
                 control.controller_revision, control.native_identity, control.native_required) != lineage
                or control.generation != pair[0].generation):
              return None
            self.profile(control)  # Unknown consumption inside the original interval still fails closed.
      except (AttributeError, TypeError, ValueError):
        return None
    return pair

  def observe_original_pose(self, pose, collector, *, now_ns, current_native):
    pair = self.original_pose_pair(collector.ready, pose)
    if pair is None:
      return None
    return self.observe_pose(pose, *pair, now_ns=now_ns, current_native=current_native)

  def observe_pose(self, pose, control, output, *, now_ns, current_native):
    # The caller passes the collector's original lag-associated pair. Neither
    # this adapter nor a 4Hz report chooses/interpolates a replacement pose.
    if pose.mono_ns <= self.last_pose_ns:
      return None
    self.last_pose_ns = pose.mono_ns
    if not self.monitor.observe(control, output, pose, now_ns=now_ns,
        binding=self.binding, current_native=current_native, replay=self.replay):
      state = self.optimizer.offer_state
      preserve = (self.monitor.reason == 'response_command_outside_identified_range' and
        (state is None or state.get('accepted') is True))
      self.optimizer.interrupt(self.monitor.reason)
      self.episodes.interrupt(self.monitor.reason, preserve_completed=preserve)
      return None
    witness = self.monitor.witness(now_ns)
    if witness is None:
      self.optimizer.interrupt('fresh_physical_residual_window_required')
      return None
    if not self.optimizer.response_current(witness, now_ns=now_ns):
      return None
    state=self.optimizer.offer_state
    from openpilot.starpilot.flm.live_tracking import discovery_needs_probes
    measurement_only = bool(self.optimizer.discovery is not None and not discovery_needs_probes(self.optimizer.discovery) or
      state is not None and state['phase']=='trial')
    self.episodes.defer_unprobed=bool(self.scoring_enabled and not measurement_only)
    if measurement_only and not self.episodes.release_measured_only():
      self.interrupt('measured_episode_backlog')
      return None
    self.episodes.observe_pose(pose, control, output, witness=witness, now_ns=now_ns,
      current_native=current_native, preserve_completed=self._retain_completed())
    for episode in self.episodes.drain(now_ns=now_ns):
      if self.scoring_enabled:
        self.optimizer.observe_episode(episode, now_ns=now_ns)
    # Renew only on a genuine new physical pose; no restamping stale evidence.
    self.latest_offer = self.optimizer.offer(now_ns, publication=False)
    return self.latest_offer

  def observe_application(self, application, *, now_ns):
    """Only the actual typed wire reader's current-owner acknowledgement."""
    from openpilot.starpilot.flm.live_trial_owner import LiveTrialApplication
    if (type(application) is not LiveTrialApplication or self.optimizer.offer_state is None or
        self.latest_offer is None or application.version != 1 or not 0 < application.sequence <= self.latest_offer.sequence):
      return False
    state = self.optimizer.offer_state
    if application.status == 'rejected':
      if (application.reason != 'pending_trial_interrupted' or application.binding != self.binding
          or state['phase'] not in ('trial', 'confirmed') or state.get('accepted', False)
          or application.trial_id != state['trial_id']
          or application.target_sequence != state.get('target_sequence', self.optimizer.sequence)
          or application.confirmed_target_sha256
          or not state['started_ns'] < application.control_ns <= now_ns <= application.control_ns+100_000_000):
        return False
      try:
        if full_profile_sha256(self.binding.baseline_revision, application.applied) != application.applied_sha256:
          return False
      except (TypeError, ValueError):
        return False
      # Withdraw the wire trial and retain only the existing inert-study rules.
      # A fresh baseline ACK and new ID are still required; no lease is renewed.
      self.interrupt('consumer_pending_trial_interrupted')
      return False
    return self.optimizer.acknowledge_application(trial_id=application.trial_id,
      target_sequence=application.target_sequence, phase=state['phase'],
      target_sha256=application.applied_sha256, applied_full30=application.applied,
      control_ns=application.control_ns, binding=application.binding, now_ns=now_ns,
      status=application.status, confirmed_target_sha256=application.confirmed_target_sha256)

  def report(self, now_ns):
    tracking = self.optimizer.report(now_ns)
    plot = self.episodes.tracking_plot()
    if plot is not None:
      plot = dict(plot, bindingCurrent=tracking['bindingCurrent'],
        ageSeconds=max(0., (now_ns-plot['points'][-1]['poseMonoTime'])/1e9))
    return {'tracking': tracking, 'trackingValues': self.optimizer.tracking_values(), 'trackingPlot': plot}
