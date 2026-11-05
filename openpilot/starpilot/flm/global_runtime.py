"""Controls-owned profile admission and capture, with no settings or fit I/O."""

import math
from collections import OrderedDict
from types import SimpleNamespace
from typing import cast
from openpilot.starpilot.flm.adaptive_profile import ApplicationOutcome, Parameter, ProfileOwner
from openpilot.starpilot.flm.runtime_adapter_registry import (
  selected_descriptor,
  baseline_values,
  baseline_revision,
  selected_identity,
  SOURCE_LEASE_NS,
  native_identity_key,
  native_identity_digest,
  vm_regime,
)
from openpilot.starpilot.flm.learning_wire import read_runtime_admission, read_source_receipt
from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
from openpilot.starpilot.flm.torque_adapter import Objective, Admission, TorqueStageOwner


def native_witness_current(native, *, now_ns, active):
  # Observer copies the exact already-validated native object. No verifier or
  # inventory/transport traversal belongs to this per-frame guard.
  try:
    observed, expires = int(native.observedMonoTime), int(native.validUntilMonoTime)
    return bool(active and native.requestedLateral and native.lateralAllowed and 0 < observed <= now_ns <= expires <= observed + 200_000_000)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return False


SERVICE = 'autoSteeringLearning'
TRACKING_SERVICE = 'starpilotSteeringTracking'
NON_TORQUE_SCHEMAS = {
  'pid': 'pid-ff-pre-pid-v1',
  'angle': 'angle-no-offset-v1',
  'curvature': 'curvature-ff-pre-pid-v1',
  'ford_curvature': 'ford-requested-pre-trim-v1',
}


class GlobalRuntime:
  def __init__(self, cp, ci, controller, *, confirmed_live_history=()):
    # Construct AFTER actual LaC selection, startup native/manual configuration.
    self.cp, self.ci, self.controller = cp, ci, controller
    from openpilot.starpilot.flm.calibration_lifecycle import ConsumerCalibrationLifecycle

    self._calibration_lifecycle = ConsumerCalibrationLifecycle()
    self._calibration_pending: tuple[str, int] | None = None
    self.health_services = ('carState', 'modelV2', 'vehicleParameters', 'lateralDelay', 'driverMonitoringState', 'extrinsicsCalibration')
    try:
      self.descriptor = selected_descriptor(cp, ci, controller)
      if self.descriptor is not None and self.descriptor.domain not in ('torque', 'torque_pid'):
        self.descriptor = None
      self.initial_values = baseline_values(controller) if self.descriptor is not None else ()
      self.baseline_revision = baseline_revision(self.descriptor, self.initial_values) if self.descriptor is not None else ''
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      self.descriptor, self.initial_values, self.baseline_revision = None, (), ''
    self.source = self.profile = self.validation = self.stage = self.frame = None
    self.native_refined = False
    from openpilot.starpilot.flm.learned_lifecycle import SettingsLeaseCache

    self.settings_cache = SettingsLeaseCache(self.descriptor.cp_sha256 if self.descriptor is not None else '')
    self.identity = ''
    self.identity_source_epoch = ''
    self.generation = 0
    self.owner = self.torque_owner = self.centre_source = None
    self.effect_owner = self.effect_envelope = None
    self.effect_full_hash = ''
    self.coefficient = 0.0
    self.session = ''
    self.outer_sequence = -1
    self._runtime_admission_cache = None
    self.live_trial_owner = None
    self._live_trial_delivery_cache = None
    self._confirmed_live_history = tuple(confirmed_live_history)
    self.live_vehicle_model_generation = ''
    self._live_vm_anchor = None
    self.last_now = 0
    self.used = False
    self.eligible = False
    self.stage_effect = False
    self.reason = 'initializing'
    self.outcome = None
    self._pending_trial_interrupted = False
    self.snapshot = ()
    self.snapshot_sequence = 0
    self.frame_sequence = 0
    self.snapshot_ns = 0
    self.last_capture_frame = 0
    self.replay_inputs = ()
    self.observation = None
    self.prior_policy_output = self.prior_policy_ff = 0.0
    self.native_identity_cache = None
    self.native_identity = ''
    self.profile_native_identity = ''
    self.replaced_native_model_revision = ''
    self.vm_anchor = None
    self.replaced_vm_model_revision = ''
    self.centre_frame = None
    self.centre_ack = None
    self.centre_history = OrderedDict()
    self.centre_raw = ()
    self.centre_result = (0.0, 0.0)
    self.torque_result = (0.0, 0.0)
    if self.descriptor is not None and self.descriptor.domain == 'torque':
      from openpilot.starpilot.flm.torque_observation import TorqueObservation

      self.observation = TorqueObservation()
      controller.automatic_flm_observation = self.observation
    if self.descriptor is not None and self.descriptor.domain == 'torque_pid':
      from openpilot.starpilot.flm.pid_stages import PidObservation

      self.observation = PidObservation()
      controller.automatic_flm_observation = self.observation
    if self.descriptor is not None:
      controller.automatic_flm_adapter = self
      if self.descriptor.domain == 'torque':
        controller.automatic_flm_stages = self
        controller.automatic_flm_centre = self
      elif self.descriptor.domain != 'ford_curvature':
        controller.response_adapter = self

  def _neutral(self, reason):
    self.controller.automatic_flm_effects = None
    if self.live_trial_owner is not None:
      self.live_trial_owner.neutral(reason)
    if self.effect_owner is not None:
      self.effect_owner.source = None
      self.effect_owner.trace = None
      self.effect_owner.applied_full = dict.fromkeys(self.effect_owner.applied_full, 0.0)
    self.coefficient = 0.0
    if self.owner is not None:
      self.owner.applied = (0.0,) * len(self.owner.parameters)
    if self.torque_owner is not None:
      self.torque_owner.owner.applied = (0.0,) * len(self.torque_owner.owner.parameters)
    if self.centre_source is not None:
      self.centre_source.owner.owner.applied = (0.0, 0.0)
    self.reason = reason

  def prepare(self, sm, *, now_ns, active, healthy, controller=None, manual_current=False, native_required=False, native_witness=None):
    """Bounded current source/admission checks; invoked immediately before LaC."""
    self.used, self.stage, self.frame, self.eligible = False, None, None, False
    self.outcome = None
    self._pending_trial_interrupted = False
    self.controller.automatic_flm_effects = None
    route = getattr(self.controller, 'route_flm_effects', None)
    if route is not None:
      route.admitted_ns = 0
      route.frame_allowed = False
    self.source = None
    # Capture a fresh V2 generation and calibration interruption before ANY
    # unrelated adapter/native/vehicle health gate can return.
    receipt_service = SERVICE
    if (
      getattr(self.controller, 'route_flm_scope', None) is None
      and TRACKING_SERVICE in sm.seen
      and sm.seen[TRACKING_SERVICE]
      and int(sm[TRACKING_SERVICE].version) != 0
    ):
      receipt_service = TRACKING_SERVICE
    live_delivery = receipt_service == TRACKING_SERVICE
    tracker = self._calibration_lifecycle
    early_source = None
    try:
      if (
        type(now_ns) is int
        and now_ns > 0
        and self.descriptor is not None
        and sm.seen[receipt_service]
        and sm.alive[receipt_service]
        and sm.valid[receipt_service]
        and sm.freq_ok[receipt_service]
        and 0 <= now_ns - int(round(sm.recv_time[receipt_service] * 1e9)) <= SOURCE_LEASE_NS
      ):
        envelope = sm[receipt_service]
        receipt = envelope.sourceReceipt if live_delivery else envelope.learning.sourceReceipt
        if receipt.version in (2, 3):
          if live_delivery:
            from openpilot.starpilot.flm.live_trial_wire import read_tracking_source

            early_source = read_tracking_source(envelope, now_ns=now_ns, cp_sha256=self.descriptor.cp_sha256)
          else:
            early_source = read_source_receipt(SimpleNamespace(valid=True, autoSteeringLearning=envelope), now_ns=now_ns, cp_sha256=self.descriptor.cp_sha256)
      stamp = int(sm.logMonoTime['extrinsicsCalibration'])
      receipt = int(round(sm.recv_time['extrinsicsCalibration'] * 1e9))
      current_calibration = bool(
        type(now_ns) is int
        and now_ns > 0
        and sm.all_checks(['extrinsicsCalibration'])
        and 0 < stamp <= now_ns <= stamp + 500_000_000
        and 0 < receipt <= now_ns <= receipt + 500_000_000
      )
      if not tracker.generation:
        from openpilot.starpilot.flm.calibration_lifecycle import calibrated_basis

        pending = self._calibration_pending
        generation = early_source.calibration_generation if early_source is not None else None
        if pending is not None and generation is not None and generation != pending[0]:
          tracker.retire(pending[0])
          pending = self._calibration_pending = None
        calibration = calibrated_basis(sm['extrinsicsCalibration'])
        frequency_only = bool(
          not current_calibration
          and type(now_ns) is int
          and now_ns > 0
          and sm.seen['extrinsicsCalibration']
          and sm.alive['extrinsicsCalibration']
          and sm.valid['extrinsicsCalibration']
          and not sm.freq_ok['extrinsicsCalibration']
          and 0 < stamp <= now_ns <= stamp + 500_000_000
          and 0 < receipt <= now_ns <= receipt + 500_000_000
          and calibration is not None
        )
        defer_binding = bool(
          frequency_only
          and generation is not None
          and not tracker.exhausted
          and generation not in tracker.rejected
          and (pending is None or calibration is not None and calibration[1] >= pending[1])
        )
        if defer_binding:
          assert generation is not None and calibration is not None
          self._calibration_pending = (generation, calibration[1])
        else:
          # Invalid/stale first calibration and pending regressions retain the
          # original capture-before-observe rejection. No pending row admits.
          if generation is None and pending is not None and not current_calibration:
            generation = pending[0]
          if generation is not None:
            tracker.capture(generation)
            if pending is not None and generation == pending[0]:
              tracker.blocks = pending[1]
            self._calibration_pending = None
      if not sm.valid['extrinsicsCalibration'] or stamp > now_ns or receipt > now_ns:
        tracker.reject(now_ns=now_ns)
      tracker.observe(sm['extrinsicsCalibration'], current=current_calibration, now_ns=now_ns)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      if not tracker.generation:
        if early_source is not None:
          tracker.capture(early_source.calibration_generation)
        elif self._calibration_pending is not None:
          tracker.capture(self._calibration_pending[0])
        self._calibration_pending = None
      tracker.reject(now_ns=now_ns)
    self.outcome = None
    self._pending_trial_interrupted = False
    self.centre_frame = None
    self.centre_raw = ()
    self.centre_result = (0.0, 0.0)
    self.torque_result = (0.0, 0.0)
    self.stage_effect = False
    self.centre_ack = None
    policy = getattr(getattr(self.controller, 'starpilot_extension', None), 'policy', None)
    self.live_manual = bool(
      manual_current
      or getattr(self.controller, 'manual_flm_effects', None) is not None
      or getattr(self.controller, 'manual_flm_status', 'not_configured') not in ('configured', 'not_configured')
      or getattr(self.controller, 'flm_surface', None) is not None
      or getattr(policy, 'surface', None) is not None
    )
    self.native_required = bool(native_required)
    self.native_witness = native_witness
    key = native_identity_key(native_witness)
    if self.native_identity_cache is None or key != self.native_identity_cache[0]:
      self.native_identity_cache = (key, native_identity_digest(key))
    self.native_identity = self.native_identity_cache[1]
    self.active, self.healthy, self.now_ns = bool(active), bool(healthy), now_ns
    self._live_sm = sm
    if self.descriptor is None or controller is not None and controller is not self.controller:
      self._neutral('actual_adapter_unavailable')
      return
    try:
      current_values = baseline_values(self.controller)
      if current_values != self.initial_values:
        from openpilot.starpilot.flm.native_baseline import accepts_refinement

        receipt = getattr(self.controller, 'native_learning_refinement', None)
        trusted = accepts_refinement(self.controller, receipt, before=self.initial_values, after=current_values, now_ns=now_ns)
        self.initial_values = current_values
        if trusted:
          self.native_refined = True
          self._neutral('native_baseline_pending_probe')
        else:
          self.baseline_revision = baseline_revision(self.descriptor, current_values)
          self.identity_source_epoch = ''
          self.profile = self.validation = self.torque_owner = self.owner = self.centre_source = None
          self.snapshot_ns = 0
          self._neutral('baseline_changed')
    except (AttributeError, TypeError, ValueError, OverflowError):
      self._neutral('baseline_unavailable')
      return
    clock_valid = type(now_ns) is int and now_ns > 0 and (not self.last_now or 0 < now_ns - self.last_now <= 50_000_000)
    self.last_now = now_ns
    try:
      if self.native_required and not native_witness_current(self.native_witness, now_ns=now_ns, active=active):
        raise ValueError('native_lease_unavailable')
      if not clock_valid or not sm.all_checks(list(self.health_services)) or not healthy:
        raise ValueError('unhealthy_clock_or_vehicle')
      # Fixed cardinality original-source and receipt checks. A recent profile
      # never turns old vehicle/model messages into fresh controller evidence.
      age_limits = (
        ('carState', 100_000_000),
        ('modelV2', 150_000_000),
        ('vehicleParameters', 150_000_000),
        ('driverMonitoringState', 150_000_000),
        ('lateralDelay', 500_000_000),
        ('extrinsicsCalibration', 500_000_000),
      )
      for service, maximum in age_limits:
        stamp = int(sm.logMonoTime[service])
        receipt = int(round(sm.recv_time[service] * 1e9))
        if not (sm.seen[service] and 0 < stamp <= now_ns <= stamp + maximum and 0 < receipt <= now_ns <= receipt + maximum):
          if service == 'extrinsicsCalibration' and hasattr(self, '_calibration_lifecycle'):
            self._calibration_lifecycle.unavailable(now_ns=now_ns)
          raise ValueError('stale_original_source')
      if self.controller.sat_time > 0:
        raise ValueError('controller_saturation')
      if not (sm.seen[receipt_service] and sm.alive[receipt_service] and sm.valid[receipt_service] and (live_delivery or sm.freq_ok[receipt_service])):
        raise ValueError('source_unavailable')
      received = int(round(sm.recv_time[receipt_service] * 1e9))
      if not 0 <= now_ns - received <= SOURCE_LEASE_NS:
        raise ValueError('stale_receipt')
      envelope = sm[receipt_service]
      event = SimpleNamespace(valid=True, autoSteeringLearning=envelope)
      if live_delivery:
        from openpilot.starpilot.flm.live_trial_wire import read_tracking_source

        source = early_source if early_source is not None else read_tracking_source(envelope, now_ns=now_ns, cp_sha256=self.descriptor.cp_sha256)
      else:
        source = early_source if early_source is not None else read_source_receipt(event, now_ns=now_ns, cp_sha256=self.descriptor.cp_sha256)
      if (
        source is None
        or source.cp_sha256 != self.descriptor.cp_sha256
        or not 0 < source.observed_ns <= now_ns <= source.expires_ns <= source.observed_ns + SOURCE_LEASE_NS
      ):
        raise ValueError('source_identity_or_clock')
      calibration = sm['extrinsicsCalibration']
      if source.calibration_generation:
        if (
          len(source.calibration) != 3
          or not all(math.isfinite(value) for value in source.calibration)
          or not tracker.admit(source.calibration_generation, source.observed_ns)
        ):
          raise ValueError('calibration_identity_changed')
      elif str(calibration.calStatus) != 'calibrated' or source.calibration != tuple(float(value) for value in calibration.rpyCalib):
        raise ValueError('calibration_identity_changed')
      if not self.settings_cache.install(source, received_ns=received) or self.settings_cache.current(now_ns=now_ns) is None:
        raise ValueError('source_cache')
      if source.source_epoch != self.identity_source_epoch:
        identity = selected_identity(self.descriptor, source.source_epoch, self.baseline_revision)
        self.identity_source_epoch = source.source_epoch
        self.identity = identity
        self.generation += 1
        self.owner = self.torque_owner = self.centre_source = None
        self.effect_owner = self.effect_envelope = None
        self.live_trial_owner = None
        self.effect_full_hash = ''
        self.profile = self.validation = None
        self.snapshot_ns = self.snapshot_sequence = 0
        self.session, self.outer_sequence = '', -1
        self.centre_history.clear()
      self.source = source
      self.eligible = bool(source.enabled and not source.manual and not self.live_manual and active and healthy)
      if not source.enabled or source.manual or self.live_manual or not active:
        raise ValueError('manual_or_off_or_inactive')
      # Current configuration is enough to record the unchanged controller.
      # A slow optional publisher must not stop the tape needed to learn.
      # Frequency still gates EVERY trial, restore and legacy application below.
      if live_delivery and not sm.freq_ok[receipt_service]:
        raise ValueError('source_frequency_unavailable')
      route = getattr(self.controller, 'route_flm_effects', None)
      if route is not None:
        from openpilot.starpilot.flm.offroad_runtime import RouteEffects

        if type(route) is not RouteEffects:
          raise ValueError('unknown_route_configuration_owner')
        if not live_delivery:
          route.observe_current_status(str(envelope.learning.reason))
        route.admit_current_runtime(self)
        self._neutral('offroad_route_recording')
        return
      live_offer, live_restore = self._current_live_trial(sm, envelope) if live_delivery else (None, None)
      if live_offer is not None and live_restore is not None:
        raise ValueError('simultaneous trial and history restore')
      if live_offer is not None or live_restore is not None:
        self._prepare_live_trial(live_offer or live_restore, source, now_ns=now_ns, active=active, healthy=healthy, restoring=live_restore is not None)
        self.reason = 'qualified'
        return
      if live_delivery:
        raise ValueError('live_tracking_collecting')
      if self.effect_owner is self.live_trial_owner and self.live_trial_owner is not None:
        self.live_trial_owner.neutral('live_offer_withdrawn')
        self.effect_owner = None
      admission = self._current_runtime_admission(sm, event)
      if admission is None:
        raise ValueError('collecting')
      profile, validation = admission
      if self.native_required and self.profile_native_identity and self.native_identity != self.profile_native_identity:
        if self.validation is not None:
          self.replaced_native_model_revision = self.validation.physical_model_revision
        self.owner = self.torque_owner = self.centre_source = None
        self.effect_owner = self.effect_envelope = None
        self.effect_full_hash = ''
      if self.native_required and validation.physical_model_revision == self.replaced_native_model_revision:
        raise ValueError('native_identity_replaced')
      if validation.physical_model_revision == self.replaced_vm_model_revision:
        raise ValueError('vehicle_model_material_change')
      if (validation.native_baseline_values or self.native_refined) and validation.native_baseline_values != current_values:
        raise ValueError('native_baseline_pending_probe')
      if profile.identity != self.identity or validation.baseline_revision != self.baseline_revision:
        raise ValueError('profile_baseline_identity')
      outer = envelope.learning
      session, sequence = str(outer.session), int(outer.sequence)
      if not session or len(session) > 64 or sequence < 0:
        raise ValueError('session')
      if session == self.session and (
        sequence < self.outer_sequence or sequence == self.outer_sequence and (profile != self.profile or validation != self.validation)
      ):
        raise ValueError('changed_or_reordered_admission')
      if session != self.session:
        self.owner = self.torque_owner = self.centre_source = None
        self.effect_owner = self.effect_envelope = None
        self.effect_full_hash = ''
      self.session, self.outer_sequence = session, sequence
      self.profile, self.validation = profile, validation
      if self.native_required:
        self.profile_native_identity = self.native_identity
      if self.descriptor.domain in ('torque', 'torque_pid') and profile.policy in ('event_effect_coordinate_v2', 'event_effect_restore_v1'):
        self._prepare_effect(profile, validation, source, now_ns=now_ns, active=active, healthy=healthy)
      elif self.descriptor.domain == 'torque_pid':
        raise ValueError('PID requires qualified full-surface effect policy')
      elif self.descriptor.domain == 'torque':
        self.effect_owner = self.effect_envelope = None
        self.effect_full_hash = ''
        names = [p.name.split('.') for p in profile.parameters]
        if len(names) != 2 or any(len(row) != 4 for row in names) or names[0][0] != 'centre' and (names[0][2] != 'left' or names[1][2] != 'right'):
          raise ValueError('torque_objective')
        if names[0][0] != 'feedforward':
          raise ValueError('unsupported_torque_objective')
        self.centre_source = None
        objective = Objective(names[0][0], names[0][1], names[0][3])
        if profile.parameters != objective.parameters:
          raise ValueError('torque_parameter_schema')
        if (
          self.torque_owner is None
          or self.torque_owner.objective != objective
          or self.torque_owner.physical_model_revision != validation.physical_model_revision
        ):
          self.torque_owner = TorqueStageOwner(
            self.identity, objective, baseline_revision=self.baseline_revision, physical_model_revision=validation.physical_model_revision
          )
        self.torque_owner.prepare(Admission(profile, now_ns, source.enabled, source.manual, healthy, validation))
      else:
        parameters = self.response_parameters()
        if profile.parameters != parameters:
          raise ValueError('domain_parameter_schema')
        if self.owner is None or self.owner.physical_model_revision != validation.physical_model_revision:
          self.owner = ProfileOwner(
            self.identity, parameters, baseline_revision=self.baseline_revision, physical_model_revision=validation.physical_model_revision
          )
        self.coefficient = self.owner.tick(
          profile, now_ns=now_ns, active=active, enabled=source.enabled, manual=source.manual, healthy=healthy, replay_validation=validation
        )[0]
      self.reason = 'qualified'
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
      self._neutral('unavailable')
      self._pending_trial_interrupted = str(error) == 'interrupted pending trial cannot resume'

  def _current_live_trial(self, sm, envelope):
    from capnp.lib.capnp import _DynamicStructReader
    from openpilot.starpilot.flm.live_trial_wire import read_trial, read_restore

    if int(envelope.version) != 1:
      raise ValueError('unknown live tracking delivery version')
    frame = sm.recv_frame[TRACKING_SERVICE]
    if type(frame) is not int or not isinstance(envelope, _DynamicStructReader):
      self._live_trial_delivery_cache = None
      return read_trial(envelope), read_restore(envelope)
    key = (frame, int(sm.logMonoTime[TRACKING_SERVICE]), float(sm.recv_time[TRACKING_SERVICE]))
    cached = self._live_trial_delivery_cache
    if cached is not None and cached[0] == key and cached[1] is envelope:
      return cached[2]
    value = (read_trial(envelope), read_restore(envelope))
    self._live_trial_delivery_cache = (key, envelope, value)
    return value

  def _prepare_live_trial(self, offer, source, *, now_ns, active, healthy, restoring=False):
    from openpilot.starpilot.flm.live_trial_owner import TrackingTrialOwner
    from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding

    # Live experiments always require the actual current native permission,
    # ordinary Panda permission and typed axis grants remain distinct domains.
    if self.native_required:
      if not native_witness_current(self.native_witness, now_ns=now_ns, active=active):
        raise ValueError('live_native_permission_unavailable')
    else:
      from openpilot.starpilot.flm.runtime_adapter_registry import current_authority

      if not current_authority(self._live_sm, self.cp, now_ns=now_ns, required=False)[0]:
        raise ValueError('live_ordinary_native_permission_unavailable')
    if not self.live_vehicle_model_generation:
      raise ValueError('live_vehicle_model_unobserved')
    expected = LiveTrialBinding(
      self.identity,
      self.descriptor.cp_sha256,
      self.descriptor.controller_revision,
      self.baseline_revision,
      source.source_epoch,
      source.reset,
      source.calibration_generation,
      self.live_vehicle_model_generation,
      self.native_identity if self.native_required else '',
      offer.binding.physical_model_revision,
      source.persistent_source_epoch,
      self.native_required,
    ).validate()
    from openpilot.starpilot.flm.live_tracking_response import validate_response_witness

    validate_response_witness(offer.response_witness, expected, now_ns)
    if offer.binding != expected:
      raise ValueError('live_current_source_binding_changed')
    if self.descriptor.domain == 'torque_pid':
      from openpilot.starpilot.flm.pid_stages import SUPPORTED, coordinates_supported

      if (not restoring and any(name not in SUPPORTED for name in offer.names)) or not coordinates_supported(dict(offer.target)):
        raise ValueError('PID live trial requires one of26 supported hooks')
    owner = self.live_trial_owner
    if owner is None or owner.binding != expected:
      if owner is not None:
        owner.neutral('live_binding_changed')
      owner = TrackingTrialOwner(expected, domain=self.descriptor.domain)
      self.live_trial_owner = owner
    permission = bool(active and healthy and self.eligible)
    if restoring:
      from openpilot.starpilot.flm.live_trial_store import LiveConfirmedPrior, scope, stable_binding

      priors = tuple(
        prior
        for prior in self._confirmed_live_history
        if type(prior) is LiveConfirmedPrior
        and prior.historical_only is True
        and scope(prior.binding) == offer.history_scope
        and stable_binding(prior.binding) == stable_binding(expected)
      )
      if len(priors) != 1 or not owner.prepare_restore(offer, priors[0], now_ns=now_ns, permission=permission, binding=expected):
        raise ValueError('confirmed_history_not_currently_restorable')
    elif not owner.prepare(offer, now_ns=now_ns, permission=permission, binding=expected):
      raise ValueError('live_current_permission_unavailable')
    self.owner = self.torque_owner = self.centre_source = None
    self.profile = self.validation = None
    self.effect_envelope = None
    self.effect_owner = owner
    self.effect_full_hash = offer.target_sha256
    self.controller.automatic_flm_effects = owner

  def _current_runtime_admission(self, sm, event):
    # SubMaster alone owns recv_frame: a new delivery ALWAYS changes this key.
    # Keep the original immutable reader alive so Python ID reuse cannot hit it.
    # All current source/native/configuration/lease checks still run outside
    # this parse-only cache on every Controls iteration.
    try:
      from capnp.lib.capnp import _DynamicStructReader

      reader = sm[SERVICE]
      frame = sm.recv_frame[SERVICE]
      if type(frame) is not int or frame < 0 or not isinstance(reader, _DynamicStructReader):
        raise ValueError('no owned immutable SubMaster delivery')
      key = (frame, int(sm.logMonoTime[SERVICE]), float(sm.recv_time[SERVICE]))
      cached = self._runtime_admission_cache
      if cached is not None and cached[0] == key and cached[1] is reader:
        return cached[2]
      result = read_runtime_admission(event)
      self._runtime_admission_cache = (key, reader, result)
      return result
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      self._runtime_admission_cache = None
      return read_runtime_admission(event)

  def _prepare_effect(self, profile, validation, source, *, now_ns, active, healthy):
    from openpilot.starpilot.flm.canonical_surface_adapter import GROUPS, full_profile_sha256, NAMES
    from openpilot.starpilot.flm.full_surface_profile import FullSurfaceEnvelope
    from openpilot.starpilot.flm.event_objective import EffectObjective
    from openpilot.starpilot.flm.selected_effects import EffectOwner

    envelope = profile.effect_envelope
    if not isinstance(envelope, FullSurfaceEnvelope):
      raise ValueError('missing typed full30 envelope')
    if self.descriptor.domain == 'torque_pid':
      from openpilot.starpilot.flm.pid_stages import coordinates_supported

      if not coordinates_supported(dict(envelope.coordinates)):
        raise ValueError('PID profile requires an unavailable physical stage')
    from openpilot.starpilot.flm.effect_admission_cache import admit_context

    group = next((g for g, names in GROUPS.items() if set(envelope.trial_names).issubset(names)), None)
    if group is None:
      raise ValueError('unsupported canonical effect group')
    objective = EffectObjective(group, envelope.trial_names, envelope.trial_anchor, source.source_epoch)
    current_hash = self.effect_full_hash or full_profile_sha256(self.baseline_revision, dict.fromkeys(NAMES, 0.0))
    # A byte-identical accepted envelope may renew its live profile lease. A new
    # proposal must compare-and-swap the exact previously accepted target.
    renewing = envelope == self.effect_envelope
    context = {
      'cp_sha256': self.descriptor.cp_sha256,
      'controller_revision': self.descriptor.controller_revision,
      'source_epoch': source.source_epoch,
      'reset_generation': source.reset,
      'selected_baseline_revision': self.baseline_revision,
      'context_key': source.source_epoch,
      'persistent_source_epoch': source.persistent_source_epoch,
      'current_full_profile_sha256': envelope.prior_full_profile_sha256 if renewing else current_hash,
    }
    admit_context(envelope, profile=profile, **context)
    previous = cast(EffectOwner | None, self.effect_owner)
    if previous is None or previous.objective != objective or previous.physical_model_revision != validation.physical_model_revision or not renewing:
      owner = EffectOwner(
        self.identity,
        objective,
        selected_baseline_revision=self.baseline_revision,
        physical_model_revision=validation.physical_model_revision,
        original_anchor=envelope.replay_anchor(),
        applied_full=None if previous is None else previous.applied_full,
        restore_envelope=envelope if profile.policy == 'event_effect_restore_v1' else None,
      )
    else:
      owner = previous
    owner.prepare(Admission(profile, now_ns, source.enabled, source.manual, healthy, validation), active=active, current_context=context)
    if not owner.owner.current_admission_valid:
      raise ValueError('effect proof not currently admitted')
    self.torque_owner = self.centre_source = None
    self.effect_owner, self.effect_envelope = owner, envelope
    self.effect_full_hash = envelope.full_profile_sha256
    self.controller.automatic_flm_effects = owner

  def response_parameters(self):
    return (Parameter('response', self.descriptor.stage_revision, -0.03, 0.03, 0.03, 0.005, 1e-5 if self.descriptor.domain == 'curvature' else 0.01),)

  def _read_centre_ack(self, event, now_ns):
    return None

  def begin_frame(self, cs, vm, params, *, active, safety_limited, desired_curvature, curvature_limited, lat_delay, raw_model_curvature, model_source_ns):
    self.frame_sequence += 1
    if self.observation is not None:
      self.observation.begin_frame()
    if self.descriptor is None or not self.eligible:
      self.snapshot = self.replay_inputs = ()
      return
    try:
      from openpilot.starpilot.flm.controller_replay import MAX_CHECKPOINT_NS, capture_snapshot, frame_input

      self.snapshot = ()
      if (
        self.identity
        and self.descriptor.domain != 'ford_curvature'
        and (not self.snapshot_ns or self.frame_sequence != self.last_capture_frame + 1 or self.now_ns - self.snapshot_ns >= MAX_CHECKPOINT_NS // 2)
      ):
        if self.descriptor.domain in ('torque', 'torque_pid'):
          self.snapshot = capture_snapshot(self.controller, vm).values
        else:
          self.snapshot = ()
        self.snapshot_ns = self.now_ns
        self.snapshot_sequence += 1
      self.replay_inputs = frame_input(
        cs,
        vm,
        params,
        active=active,
        safety_limited=safety_limited,
        desired_curvature=desired_curvature,
        curvature_limited=curvature_limited,
        lat_delay=lat_delay,
      ) + (
        ('control_frame', float(self.frame_sequence)),
        ('raw_model_curvature', float(raw_model_curvature)),
        ('model_source_ns.high', float(int(model_source_ns) >> 32)),
        ('model_source_ns.low', float(int(model_source_ns) & 0xFFFFFFFF)),
      )
      # Original receipt metadata only. No controller update, model, new native
      # permission or full checkpoint is produced here.
      provenance = getattr(self.controller, 'native_learning_provenance', ())
      if provenance:
        from openpilot.starpilot.flm.native_refinement_wire import FIELD, encode

        compact = encode(tuple(provenance))
        if compact is not None:
          self.replay_inputs += ((FIELD, compact),)
      regime = vm_regime(dict(self.replay_inputs), self.vm_anchor)
      if regime is None or self.vm_anchor is not None and regime != self.vm_anchor:
        if self.validation is not None:
          self.replaced_vm_model_revision = self.validation.physical_model_revision
        self.eligible = False
        self._neutral('vehicle_model_material_change')
      self.vm_anchor = regime
      if regime != self._live_vm_anchor:
        from openpilot.starpilot.flm.runtime_adapter_registry import digest

        self.live_vehicle_model_generation = '' if regime is None else digest(regime)
        self._live_vm_anchor = regime
      if self.observation is not None:
        self.observation.begin_frame()
        policy = getattr(getattr(self.controller, 'starpilot_extension', None), 'policy', None)
        self.prior_policy_output = float(getattr(policy, 'prev_output_torque', getattr(policy, 'previous_output', 0.0)))
        prior_ff = getattr(policy, 'previous_feedforward', 0.0)
        self.prior_policy_ff = 0.0 if prior_ff is None else float(prior_ff)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      self.snapshot, self.replay_inputs = (), ()
      self._neutral('snapshot_unavailable')

  def stage_value(self, baseline):
    """Actual non-torque hook before feedback or live angle offset."""
    if not math.isfinite(baseline):
      self._neutral('invalid_stage')
      return baseline
    self.used = True
    self.stage = float(baseline)
    self.stage_effect = bool(baseline * self.coefficient)
    return baseline if self.coefficient == 0.0 else baseline * (1 + self.coefficient)

  def torque_residual(self, frame, *, stage_revision):
    self.stage, self.used = frame, True
    policy = getattr(getattr(self.controller, 'starpilot_extension', None), 'policy', None)
    if getattr(self.controller, 'flm_surface', None) is not None or getattr(policy, 'surface', None) is not None:
      self.live_manual = True
      self.eligible = False
      self._neutral('manual_surface')
      return 0.0, 0.0
    if self.torque_owner is None or self.reason != 'qualified':
      return 0.0, 0.0
    result = self.torque_owner.torque_residual(frame, stage_revision=stage_revision)
    self.torque_result = tuple(result)
    self.stage_effect = bool(result[0] * frame.baseline_ff or result[1] * frame.baseline_threshold)
    return result

  @property
  def torque_applied_coordinates(self):
    return self.torque_owner.owner.applied if self.torque_owner is not None else (0.0, 0.0)

  @property
  def applied_coordinates(self):
    if self.effect_owner is not None and self.controller.automatic_flm_effects is self.effect_owner:
      return self.effect_owner.applied_coordinates
    if self.centre_source is not None:
      return self.centre_source.owner.owner.applied
    return self.torque_owner.owner.applied if self.torque_owner is not None else (0.0, 0.0)

  def observe_centre_stage(
    self, *, speed, setpoint, jerk, baseline_threshold, baseline_deadzone, friction_error, friction_scale=1.0, friction_parent_wrapper=False
  ):
    values = (
      ('baseline_threshold', float(baseline_threshold)),
      ('baseline_deadzone', float(baseline_deadzone)),
      ('friction_error', float(friction_error)),
      ('friction_scale', float(friction_scale)),
      ('friction_parent_wrapper', float(bool(friction_parent_wrapper))),
    )
    self.centre_raw = values if all(math.isfinite(value) for _, value in values) else ()

  def centre_residual(self, frame, *, stage_revision):
    self.centre_frame = frame
    if self.centre_source is None or self.reason != 'qualified':
      return 0.0, 0.0
    self.used = True
    result = self.centre_source.centre_residual(frame, stage_revision=stage_revision)
    self.centre_result = tuple(result)
    self.stage_effect = False
    return result

  def finish_centre_stage(self, *, actual_friction, baseline_friction):
    if self.centre_source is not None and all(math.isfinite(float(value)) for value in (actual_friction, baseline_friction)):
      self.stage_effect = bool(float(actual_friction) != float(baseline_friction))

  def torque_deadband(self, speed, *, stage_revision, setpoint=0.0, jerk=0.0):
    if self.torque_owner is None or self.reason != 'qualified':
      return 0.0
    self.used = True
    result = self.torque_owner.torque_deadband(speed, stage_revision=stage_revision, setpoint=setpoint, jerk=jerk)
    self.stage_effect = bool(result)
    return result

  def finish_frame(self, cs, vm, params, lac_log, **kwargs):
    try:
      self._finish_frame(cs, vm, params, lac_log, **kwargs)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
      self.frame = None
      self._neutral('optional_capture_unavailable')

  def _finish_frame(self, cs, vm, params, lac_log, *, desired_curvature, curvature_limited, lat_delay, requested, selected_command=None):
    """Capture the actual same-frame controller inputs; never controller clones."""
    if self.descriptor is None or not self.eligible:
      self.frame = None
      return
    if not all(math.isfinite(float(value)) for value in (desired_curvature, lat_delay, requested, requested if selected_command is None else selected_command)):
      self.healthy = False
      self._neutral('invalid_output')
      self.frame = None
      return
    values = baseline_values(self.controller)
    p, i, d = (float(getattr(lac_log, key, 0.0)) for key in ('p', 'i', 'd'))
    self.frame = (
      cs,
      vm,
      params,
      lac_log,
      float(desired_curvature),
      bool(curvature_limited),
      float(lat_delay),
      float(requested),
      values,
      p,
      i,
      d,
      float(requested if selected_command is None else selected_command),
    )
    if self.descriptor is None:
      return
    if self.controller.automatic_flm_effects is self.effect_owner and self.effect_owner is not None:
      self.used = self.used or self.effect_owner.used
      self.stage_effect = self.stage_effect or self.effect_owner.changed
    coordinates = (self.coefficient,) if self.descriptor.domain not in ('torque', 'torque_pid') else self.applied_coordinates
    effect = self.effect_owner if self.controller.automatic_flm_effects is self.effect_owner else None
    trace = effect.trace if effect is not None else None
    has_effect = any(effect.applied_full.values()) if effect is not None else any(coordinates)
    applied = bool(self.used and self.stage_effect and self.reason == 'qualified' and has_effect and self.active and self.healthy)
    if effect is self.live_trial_owner and effect is not None:
      application = effect.application
      if application is not None:
        actual = bool(
          effect.used and effect.frame_complete(self.descriptor.domain) and self.active and self.healthy and self.eligible and self.reason == 'qualified'
        )
        self.outcome = effect.acknowledge_frame(actual)
      return
    self.outcome = ApplicationOutcome(
      self.identity,
      self.profile.sequence if self.profile is not None else 0,
      self.now_ns,
      'applied' if applied else 'collecting' if self.eligible else 'paused',
      coordinates,
      'actual_stage_used' if applied else self.reason,
      self.baseline_revision,
      self.validation.physical_model_revision if self.validation is not None else '',
      trace.envelope.manifest_sha256 if trace is not None else '',
      trace.envelope.full_original_anchor if trace is not None else '',
      trace.applied_full_profile_sha256 if trace is not None else '',
    )

    if (
      self.centre_ack is not None
      and self.centre_source is not None
      and self.centre_frame is not None
      and self.used
      and self.reason == 'qualified'
      and self.active
      and self.healthy
      and self.eligible
    ):
      ack = self.centre_ack
      self.outcome = ApplicationOutcome(
        ack.identity,
        ack.profile_sequence,
        ack.control_ns,
        'applied',
        tuple(ack.actual_coordinates),
        'actual_centre_friction_observed',
        ack.baseline_revision,
        ack.physical_model_revision,
      )

  def capture_evidence(self, *, control_ns, state_ns, active, limited):
    if self.descriptor is None or self.frame is None or self.source is None or not self.identity:
      return None
    cs, vm, params, log, desired, curvature_limited, delay, requested, values, p, i, d, raw_command = self.frame
    if self.descriptor.domain in ('torque', 'torque_pid'):
      # Exact policy-specific normalized counterfactual trace is mandatory.
      trace = self.observation.finish_frame(
        self.controller, log, selected_command=raw_command, prior_policy_output=self.prior_policy_output, prior_policy_ff=self.prior_policy_ff
      )
      if trace is None:
        return None
      schema, stage_values, coordinates = trace
      if self.controller.automatic_flm_effects is not None:
        # Legacy FF coordinate space remains exact neutral; full30 is explicit.
        coordinates = (0.0, 0.0)
      if self.centre_raw:
        stage_values = tuple(stage_values) + self.centre_raw
      if self.descriptor.domain == 'torque_pid':
        stage_values = tuple(stage_values) + (('actual_delta_ff', self.observation.learned_ff - self.observation.stage.baseline_ff),)
      else:
        stage_values = tuple(stage_values) + (
          ('actual_delta_ff', self.torque_result[0]),
          ('actual_delta_threshold', self.torque_result[1]),
          ('centre_threshold_delta', self.centre_result[0]),
          ('centre_extra_deadband_deg', self.centre_result[1]),
          ('centre_parameter_left', self.applied_coordinates[0] if self.centre_source is not None else 0.0),
          ('centre_parameter_right', self.applied_coordinates[1] if self.centre_source is not None else 0.0),
        )
      if self.centre_source is not None:
        coordinates = (0.0, 0.0)
      # Raw basis is identified by schema; separate learner actual replay turns
      # it into normalized same-state command sensitivities before collection.
      stage = self.observation.stage
      from openpilot.starpilot.flm.torque_adapter import gates

      objective = (
        self.torque_owner.objective
        if self.torque_owner is not None
        else Objective(
          'feedforward',
          'low' if cs.vEgo < 25 else 'high',
          'steady' if abs(stage.setpoint * stage.jerk) < 0.03 else 'turn_in' if stage.setpoint * stage.jerk > 0 else 'unwind',
        )
      )
      features = tuple(stage.baseline_ff * value for value in gates(objective, stage.speed, stage.setpoint, stage.jerk))
      prediction, response_revision = requested, 'torque-independent-local-physical-v1'
      command_domain = 'normalized_torque'
    elif self.descriptor.domain == 'ford_curvature':
      # Ford's actual final path angle belongs to the downstream Card host;
      # generic LatControlAngle output is not a valid curvature stage trace.
      return None
    else:
      if self.stage is None:
        return None
      actual_stage = dict(getattr(self.controller, 'response_stage', ()))
      baseline_stage = actual_stage.get('baseline_stage', float(self.stage))
      p, i, d = (actual_stage.get(key, fallback) for key, fallback in zip(('p', 'i', 'd'), (p, i, d), strict=True))
      features, coordinates = (baseline_stage,), (self.coefficient,)
      pre_limit = p + i + d + baseline_stage
      actual_pre_limit = actual_stage.get(
        'pre_limit', pre_limit + baseline_stage * self.coefficient if self.descriptor.domain in ('pid', 'curvature') else raw_command
      )
      prediction = pre_limit if self.descriptor.domain in ('pid', 'curvature') else requested - self.stage * self.coefficient
      schema, response_revision, command_domain = (
        self.descriptor.stage_revision,
        'passive-physical-command-response-v1',
        ('normalized_torque' if self.descriptor.domain == 'pid' else self.descriptor.domain),
      )
      if self.descriptor.domain in ('pid', 'curvature'):
        limited = bool(limited or abs(pre_limit + self.stage * self.coefficient - raw_command) > 1e-6)
      stage_values = (
        ('baseline_stage', baseline_stage),
        ('p', p),
        ('i', i),
        ('d', d),
        ('pre_limit', actual_pre_limit),
        ('desired_physical', actual_stage.get('desired_physical', -desired)),
        ('speed', float(cs.vEgo)),
        ('steering_angle', float(cs.steeringAngleDeg)),
        ('steering_rate', float(cs.steeringRateDeg)),
        ('angle_offset', float(params.angleOffsetDeg)),
        ('roll', float(params.roll)),
        ('steer_ratio', float(vm.sR)),
        ('desired_curvature', desired),
        ('lat_delay', delay),
        ('selected_command', raw_command),
        ('desired_angle_deg', baseline_stage + float(params.angleOffsetDeg)),
        ('desired_physical_accel', desired * float(cs.vEgo) ** 2),
        ('applied_stage', baseline_stage * (1 + self.coefficient)),
      )
    native_observed = int(getattr(self.native_witness, 'observedMonoTime', 0))
    native_expires = int(getattr(self.native_witness, 'validUntilMonoTime', 0))
    native_allowed = bool(
      self.native_required
      and self.native_witness is not None
      and active
      and getattr(self.native_witness, 'requestedLateral', False)
      and getattr(self.native_witness, 'lateralAllowed', False)
      and 0 < native_observed <= control_ns <= native_expires
    )
    if self.native_required and not native_allowed:
      self.eligible = False
      self._neutral('native_lease_unavailable')
      self.outcome = ApplicationOutcome(
        self.identity,
        self.profile.sequence if self.profile is not None else 0,
        self.now_ns,
        'paused',
        (0.0,) * len(coordinates),
        'native_lease_unavailable',
        self.baseline_revision,
        self.validation.physical_model_revision if self.validation is not None else '',
      )
    current_profile = bool(
      self.profile is not None
      and self.reason == 'qualified'
      and 0 < self.profile.observed_ns <= control_ns <= self.profile.expires_ns <= self.profile.observed_ns + SOURCE_LEASE_NS
    )
    if current_profile and self.centre_source is not None:
      key = (
        self.profile.sequence,
        self.profile.observed_ns,
        self.profile.expires_ns,
        self.source.source_epoch,
        tuple(self.applied_coordinates),
        self.baseline_revision,
        self.validation.physical_model_revision,
      )
      self.centre_history[control_ns] = (key, min(self.profile.expires_ns, self.source.expires_ns), tuple(p.name for p in self.profile.parameters))
      while len(self.centre_history) > 64:
        self.centre_history.popitem(last=False)
    # This is the actual hook-consumed map, never the offered target. The
    # preparation stamp and publication stamp are distinct but same-frame.
    from openpilot.starpilot.flm.live_trial_owner import LiveTrialApplication

    live_values = ()
    live_owner = self.live_trial_owner
    if (
      self._pending_trial_interrupted
      and live_owner is not None
      and self.controller.automatic_flm_effects is None
      and active
      and self.healthy
      and self.eligible
      and self.source.enabled
      and not (self.source.manual or self.live_manual)
      and 0 <= control_ns - self.now_ns <= 30_000_000
      and self.source.observed_ns <= control_ns <= self.source.expires_ns
      and (not self.native_required or native_allowed)
    ):
      # The real controller refused this pending generation. Report rejection
      # after current same-frame evidence, never fabricate an application ACK.
      self.outcome = live_owner.rejected_pending(control_ns=control_ns)
    application = self.outcome
    current_offer = None if live_owner is None else (live_owner.restore_offer or live_owner.offer)
    if (
      type(application) is LiveTrialApplication
      and live_owner is not None
      and current_offer is not None
      and self.effect_owner is live_owner
      and self.controller.automatic_flm_effects is live_owner
      and live_owner.frame_complete(self.descriptor.domain)
      and application.status in ('applied', 'restored')
      and application.binding == live_owner.binding
      and application.control_ns == self.now_ns
      and 0 <= control_ns - application.control_ns <= 30_000_000
      and current_offer.observed_ns <= application.control_ns <= current_offer.expires_ns
      and self.source.observed_ns <= control_ns <= self.source.expires_ns
      and active
      and self.healthy
      and self.eligible
      and self.reason == 'qualified'
      and self.source.enabled
      and not (self.source.manual or self.live_manual)
      and (not self.native_required or native_allowed)
    ):
      live_values = tuple(value for _, value in application.applied)
    if live_owner is not None and self.controller.automatic_flm_effects is live_owner and not live_values:
      # A consumed but unqualified live map is unknown, never legacy neutral.
      return None
    self.last_capture_frame = self.frame_sequence
    evidence = ControlEvidence(
      self.identity,
      self.generation,
      control_ns,
      state_ns,
      command_domain,
      self.descriptor.stage_revision,
      response_revision,
      requested,
      tuple(features),
      prediction,
      self.source.enabled,
      bool(self.source.manual or self.live_manual),
      active,
      bool(cs.canValid and not cs.canTimeout and self.healthy and self.eligible),
      bool(cs.steeringPressed),
      bool(cs.steerFaultTemporary or cs.steerFaultPermanent),
      bool(log.saturated),
      bool(limited or curvature_limited),
      False,
      False,
      schema,
      tuple(stage_values),
      tuple(coordinates),
      source_epoch=self.source.source_epoch,
      source_receipt_ns=self.source.observed_ns,
      controller_mode=self.descriptor.controller_mode,
      policy=self.descriptor.policy,
      baseline_revision=self.baseline_revision,
      baseline_values=values,
      controller_revision=self.descriptor.controller_revision,
      replay_snapshot=self.snapshot,
      replay_inputs=self.replay_inputs,
      snapshot_sequence=self.snapshot_sequence,
      turn_assist=self.descriptor.turn_assist,
      applied_parameter_names=tuple(p.name for p in self.profile.parameters) if current_profile and self.controller.automatic_flm_effects is None else (),
      native_required=self.native_required,
      native_observed_ns=native_observed,
      native_expires_ns=native_expires,
      native_allowed=native_allowed,
      native_identity=self.native_identity,
      applied_profile_sequence=self.profile.sequence if current_profile else 0,
      applied_profile_observed_ns=self.profile.observed_ns if current_profile else 0,
      applied_profile_expires_ns=self.profile.expires_ns if current_profile else 0,
      effect_trace=self.effect_owner.trace if self.controller.automatic_flm_effects is self.effect_owner and self.effect_owner is not None else None,
      effect_prepare_ns=self.now_ns,
      vehicle_model_anchor=() if self.vm_anchor is None else tuple(self.vm_anchor),
      live_applied_coordinates=live_values,
    )
    from openpilot.starpilot.flm.route_recording import with_actual_route

    try:
      return with_actual_route(evidence, self.controller)
    except (AttributeError, TypeError, ValueError, OverflowError):
      self.reason = 'route_frame_metadata_unavailable'
      return None
