# Copyright (c) 2026, firestar5683 and StarPilot contributors
# SPDX-License-Identifier: MIT
"""Canonical Dom FLM consumers and child-only exact-controller trial injection.

No saved tune/PID/native writes; admission remains in the controls runtime.
"""

from typing import cast

from dataclasses import dataclass
import math
from openpilot.starpilot.flm.adaptive_profile import ProfileOwner, ReplayValidation
from openpilot.starpilot.flm.canonical_surface_adapter import (
  CanonicalSurfaceEffects,
  NAMES,
  MANIFEST_SHA256,
  canonical_names,
  coordinate_bounds,
  original_anchor,
  parameters_for,
  full_original_anchor,
  full_profile_sha256,
)

REVISION = 'torque-selected-effect-v2'
POLICY = 'event_effect_coordinate_v2'
RESTORE_POLICY = 'event_effect_restore_v1'


def finite(v):
  return type(v) in (int, float) and math.isfinite(v)


@dataclass(frozen=True)
class EffectTrial:
  objective: object
  coordinates: tuple[float, ...]
  selected_baseline_revision: str
  original_anchor: object

  def basis(self):
    names = tuple(self.objective.names)
    if canonical_names(names) != names or tuple(self.objective.parameters) != parameters_for(names):
      raise ValueError('canonical effect objective required')
    expected = original_anchor(self.selected_baseline_revision, names)
    if self.objective.anchor_digest != expected:
      raise ValueError('recentered or foreign effect objective')
    payload = cast(dict[str, object], self.original_anchor)
    fields = {
      'version',
      'manifest_sha256',
      'full_original_anchor',
      'anchor_digest',
      'accumulated',
      'full_profile_sha256',
      'context_key',
      'cp_sha256',
      'controller_revision',
      'source_epoch',
      'reset_generation',
      'selected_baseline_revision',
      'persistent_source_epoch',
    }
    if type(payload) is not dict or set(payload) != fields:
      raise ValueError('versioned original effect context payload')
    if (
      type(payload['version']) is not int
      or payload['version'] != 1
      or payload['manifest_sha256'] != MANIFEST_SHA256
      or payload['anchor_digest'] != expected
      or payload['context_key'] != self.objective.context_key
      or payload['full_original_anchor'] != full_original_anchor(self.selected_baseline_revision)
      or payload['selected_baseline_revision'] != self.selected_baseline_revision
    ):
      raise ValueError('effect full-profile context')
    entries = cast(tuple[tuple[str, float], ...], payload['accumulated'])
    if type(entries) is not tuple or len(entries) != len(NAMES) or tuple(n for n, _ in entries) != NAMES:
      raise ValueError('exact full30 prior profile required')
    accumulated = dict(entries)
    if full_profile_sha256(self.selected_baseline_revision, entries) != payload['full_profile_sha256']:
      raise ValueError('prior full-profile digest')
    if type(self.coordinates) is not tuple or len(self.coordinates) != len(names):
      raise ValueError('effect coordinate count')
    for name, value in zip(names, self.coordinates, strict=True):
      lo, hi = coordinate_bounds(name)
      if not finite(value) or not lo <= value <= hi:
        raise ValueError('effect coordinate bound')
    return names, expected, accumulated

  def source(self):
    names, _, accumulated = self.basis()
    return CanonicalSurfaceEffects(dict(zip(names, self.coordinates, strict=True)), self.selected_baseline_revision, accumulated=accumulated)


@dataclass(frozen=True)
class EffectRestoreTrial(EffectTrial):
  """Explicit full historical-map counterfactual, NEVER an optimizer trial."""

  restore_envelope: object = None

  def basis(self):
    envelope = self.restore_envelope
    if envelope is None or envelope.version != 2 or envelope.operation != 'restore' or envelope.selected_baseline_revision != self.selected_baseline_revision:
      raise ValueError('explicit versioned restore envelope required')
    envelope.validate()
    names = canonical_names(tuple(self.objective.names))
    if (
      names != envelope.trial_names
      or self.objective.parameters != parameters_for(names)
      or self.objective.anchor_digest != envelope.trial_anchor
      or self.objective.context_key != envelope.context_key
      or self.original_anchor != envelope.replay_anchor()
      or tuple(self.coordinates) != tuple(dict(envelope.coordinates)[n] for n in names)
    ):
      raise ValueError('restore marker/source context differs from full map')
    return names, envelope.trial_anchor, dict(envelope.prior_coordinates)

  def source(self):
    names, _, _ = self.basis()
    values = dict(self.restore_envelope.coordinates)
    return CanonicalSurfaceEffects({n: values[n] for n in names}, self.selected_baseline_revision, accumulated=values)


@dataclass(frozen=True)
class EffectRestoreAblationTrial(EffectRestoreTrial):
  """Child-only causal group-off comparison; cannot become an admitted profile."""

  omitted_names: tuple[str, ...] = ()

  def source(self):
    from openpilot.starpilot.flm.canonical_surface_adapter import GROUPS

    names, _, _ = self.basis()
    if type(self.omitted_names) is not tuple or self.omitted_names not in tuple(GROUPS.values()):
      raise ValueError('one exact canonical group for causal ablation')
    values = dict(self.restore_envelope.coordinates)
    values.update(dict.fromkeys(self.omitted_names, 0.0))
    return CanonicalSurfaceEffects({n: values[n] for n in names}, self.selected_baseline_revision, accumulated=values)


class EffectOwner:
  """One original-anchor profile group, subordinate to actual runtime admission."""

  def __init__(self, identity, objective, *, selected_baseline_revision, physical_model_revision, original_anchor, applied_full=None, restore_envelope=None):
    self.objective = objective
    self.profile_policy = POLICY if restore_envelope is None else RESTORE_POLICY
    if restore_envelope is None:
      self.trial = EffectTrial(objective, (0.0,) * len(objective.names), selected_baseline_revision, original_anchor)
    else:
      coordinates = tuple(dict(restore_envelope.coordinates)[n] for n in objective.names)
      self.trial = EffectRestoreTrial(objective, coordinates, selected_baseline_revision, original_anchor, restore_envelope)
    self.prior_coordinates = self.trial.basis()[2]
    self.accepted_envelope = None
    self.baseline_revision = selected_baseline_revision
    self.physical_model_revision = physical_model_revision
    self.owner = ProfileOwner(
      identity,
      objective.parameters,
      baseline_revision=self.baseline_revision,
      physical_model_revision=physical_model_revision,
      profile_policy=self.profile_policy,
    )
    self.owner.applied = tuple(self.prior_coordinates[n] for n in objective.names)
    full = dict.fromkeys(NAMES, 0.0) if applied_full is None else dict(applied_full)
    full_profile_sha256(selected_baseline_revision, full)
    self.applied_full = full
    self.last_ns = None
    self.trace = None
    self.source = None
    self.source_values = None
    self.used = self.changed = False
    self.original_effect_anchor = objective.anchor_digest

  @property
  def applied_coordinates(self):
    return tuple(self.applied_full[n] for n in self.objective.names)

  def prepare(self, admission, *, active, current_context):
    self.used = self.changed = False
    cached_source, self.source = self.source, None
    self.trace = None
    p, v = admission.profile, admission.replay_validation
    from openpilot.starpilot.flm.full_surface_profile import FullSurfaceEnvelope

    envelope = getattr(p, 'effect_envelope', None)
    exact_envelope = False
    if isinstance(envelope, FullSurfaceEnvelope):
      from openpilot.starpilot.flm.effect_admission_cache import admit_context

      validated = admit_context(envelope, profile=p, **current_context)
      if self.accepted_envelope is None:
        if validated.replay_anchor() != self.trial.original_anchor:
          raise ValueError('foreign full profile trial context')
        self.accepted_envelope = validated
      exact_envelope = validated == self.accepted_envelope
    exact = (
      exact_envelope
      and p is not None
      and p.policy == self.profile_policy
      and p.parameters == self.objective.parameters
      and isinstance(v, ReplayValidation)
      and v.baseline_revision == self.baseline_revision
      and v.physical_model_revision == self.physical_model_revision
    )
    self.owner.tick(
      p if exact else None,
      now_ns=admission.now_ns,
      active=active,
      enabled=admission.enabled,
      manual=admission.manual,
      healthy=admission.healthy,
      replay_validation=v if exact else None,
    )
    current = bool(exact and self.owner.current_admission_valid and active and admission.enabled and not admission.manual and admission.healthy)
    if not isinstance(envelope, FullSurfaceEnvelope):
      self.applied_full = dict.fromkeys(NAMES, 0.0)
      self.source_values = None
      self.last_ns = admission.now_ns
      return
    from openpilot.starpilot.flm.effect_trace import FullEffectTrace, ordered, step_full, retain_sender_verification

    previous_ns = self.last_ns or 0
    before = dict(self.applied_full)
    target = dict(envelope.coordinates)
    self.applied_full = step_full(before, target, now_ns=admission.now_ns, previous_ns=previous_ns, qualified=current)
    self.last_ns = admission.now_ns
    self.trace = retain_sender_verification(
      FullEffectTrace(
        1,
        envelope,
        admission.now_ns,
        previous_ns,
        current,
        ordered(before),
        ordered(target),
        ordered(self.applied_full),
        full_profile_sha256(self.baseline_revision, self.applied_full),
      )
    )
    key = tuple(self.applied_full[n] for n in NAMES)
    if not current:
      self.source = None
      self.source_values = None
      self.applied_full = dict.fromkeys(NAMES, 0.0)
    elif cached_source is None or key != self.source_values:
      group = {n: self.applied_full[n] for n in self.objective.names}
      self.source = CanonicalSurfaceEffects(group, self.baseline_revision, accumulated=self.applied_full)
      self.source_values = key
    else:
      self.source = cached_source
    if self.source is not None:
      self.source.begin_frame(admission.now_ns)

  def _record(self, baseline, result):
    self.used = True
    self.changed = self.changed or result != baseline
    return result

  def selected_stage(self, frame, *, original_anchor):
    baseline = frame.baseline_ff, frame.baseline_threshold
    return baseline if self.source is None else self._record(baseline, self.source.selected_stage(frame, original_anchor=original_anchor))

  @property
  def pid_error_calls(self):
    return 0 if self.source is None else self.source.pid_error_calls

  def selected_pid_error(self, frame, *, original_anchor):
    baseline = frame.error_deg
    return baseline if self.source is None else self._record(baseline, self.source.selected_pid_error(frame, original_anchor=original_anchor))

  def selected_pid_stage(self, frame, *, original_anchor):
    baseline = frame.baseline_ff
    return baseline if self.source is None else self._record(baseline, self.source.selected_pid_stage(frame, original_anchor=original_anchor))

  def selected_friction(self, baseline, *, original_anchor):
    return baseline if self.source is None else self._record(baseline, self.source.selected_friction(baseline, original_anchor=original_anchor))

  def selected_deadzone(self, *args, **kwargs):
    return args[1] if self.source is None else self._record(args[1], self.source.selected_deadzone(*args, **kwargs))

  def selected_output(self, *args, **kwargs):
    return args[4] if self.source is None else self._record(args[4], self.source.selected_output(*args, **kwargs))


class EffectReplaySource:
  """Immutable trial through the same complete controller calls, child-only."""

  def __init__(self, trial):
    if not isinstance(trial, EffectTrial):
      raise ValueError('effect trial required')
    self.source = trial.source()
    self.source.allow_limited_profile = True
    self.original_effect_anchor = self.source.original_effect_anchor
    self.applied_coordinates = self.source.applied_coordinates

  @classmethod
  def from_coordinates(cls, coordinates, baseline_revision, *, accumulated):
    if cls is not EffectReplaySource:
      raise ValueError('exact numerical replay source required')
    obj = cls.__new__(cls)
    obj.source = CanonicalSurfaceEffects(coordinates, baseline_revision, accumulated=accumulated, allow_limited_profile=True)
    obj.original_effect_anchor = obj.source.original_effect_anchor
    obj.applied_coordinates = obj.source.applied_coordinates
    return obj

  def begin_frame(self, control_ns):
    self.source.begin_frame(control_ns)

  @property
  def stage_calls(self):
    return self.source.stage_calls

  @property
  def friction_calls(self):
    return self.source.friction_calls

  def selected_stage(self, *args, **kwargs):
    return self.source.selected_stage(*args, **kwargs)

  @property
  def pid_error_calls(self):
    return self.source.pid_error_calls

  def selected_pid_error(self, *args, **kwargs):
    return self.source.selected_pid_error(*args, **kwargs)

  def selected_pid_stage(self, *args, **kwargs):
    return self.source.selected_pid_stage(*args, **kwargs)

  def selected_friction(self, *args, **kwargs):
    return self.source.selected_friction(*args, **kwargs)

  def selected_deadzone(self, *args, **kwargs):
    return self.source.selected_deadzone(*args, **kwargs)

  def selected_output(self, *args, **kwargs):
    return self.source.selected_output(*args, **kwargs)


def _selected_source(parent):
  from openpilot.starpilot.flm.manual_coordinates import ManualEffects

  source = getattr(parent, 'manual_flm_effects', None)
  if source is not None:
    policy = getattr(getattr(parent, 'starpilot_extension', None), 'policy', None)
    if getattr(parent, 'flm_surface', None) is not None or getattr(policy, 'surface', None) is not None:
      return None
    return source if type(source) is ManualEffects else None
  if getattr(parent, 'route_flm_effects', None) is not None:
    from openpilot.starpilot.flm.offroad_runtime import selected_route

    return selected_route(parent)
  return getattr(parent, 'automatic_flm_effects', None)


def manual(parent):
  policy = getattr(getattr(parent, 'starpilot_extension', None), 'policy', None)
  return (
    getattr(parent, 'manual_flm_effects', None) is not None or getattr(parent, 'flm_surface', None) is not None or getattr(policy, 'surface', None) is not None
  )


def bounded_profile_on_limited_frame(parent):
  if getattr(parent, 'route_flm_effects', None) is not None:
    return False
  if getattr(parent, 'manual_flm_effects', None) is not None:
    return False
  # This is parameter shaping, not a native grant. The live owner must already
  # hold current source/native permission; replay uses the same bounded math.
  # Legacy residual owners and arbitrary callback objects remain inhibited.
  from openpilot.starpilot.flm.live_trial_owner import TrackingTrialOwner

  owner = getattr(parent, 'automatic_flm_effects', None)
  return (
    type(owner) in (TrackingTrialOwner, EffectReplaySource, EffectReplayRecordedSource)
    and type(owner.source) is CanonicalSurfaceEffects
    and owner.source.allow_limited_profile
  )


def apply_selected_stage(parent, frame):
  source = _selected_source(parent)
  if (
    source is None
    or not frame.valid()
    or not frame.active
    or frame.driver_override
    or (frame.limited and (frame.curvature_limited or not bounded_profile_on_limited_frame(parent)))
    or (manual(parent) and source is not getattr(parent, 'manual_flm_effects', None))
  ):
    return frame.baseline_ff, frame.baseline_threshold
  try:
    ff, threshold = source.selected_stage(frame, original_anchor=source.original_effect_anchor)
    # Canonical source owns per-knob envelopes; this boundary rejects nonfinite
    # values and sign reversals without recentering on an admitted trial.
    if not finite(ff) or not finite(threshold) or threshold <= 0 or ff * frame.baseline_ff < 0:
      return frame.baseline_ff, frame.baseline_threshold
    return ff, threshold
  except (AttributeError, TypeError, ValueError, OverflowError):
    return frame.baseline_ff, frame.baseline_threshold


def apply_selected_friction(parent, cs, setpoint, baseline, *, active, limited, curvature_limited=False):
  source = _selected_source(parent)
  if (
    source is None
    or not active
    or (limited and (curvature_limited or not bounded_profile_on_limited_frame(parent)))
    or cs.steeringPressed
    or (manual(parent) and source is not getattr(parent, 'manual_flm_effects', None))
    or not finite(baseline)
  ):
    return baseline
  try:
    candidate = source.selected_friction(float(baseline), original_anchor=source.original_effect_anchor)
    if not finite(candidate):
      return baseline
    return candidate
  except (AttributeError, TypeError, ValueError, OverflowError):
    return baseline


def apply_selected_deadzone(parent, cs, vm, baseline_accel, requested_curvature, *, active, limited, curvature_limited=False):
  source = _selected_source(parent)
  if (
    source is None
    or not active
    or (limited and (curvature_limited or not bounded_profile_on_limited_frame(parent)))
    or cs.steeringPressed
    or (manual(parent) and source is not getattr(parent, 'manual_flm_effects', None))
  ):
    return baseline_accel
  try:
    speed = float(cs.vEgo)
    derivative = abs(float(vm.calc_curvature(math.radians(1.0), speed, 0.0))) * speed**2
    friction_accel = abs(float(parent.torque_params.friction) * float(parent.torque_params.latAccelFactor))
    candidate = source.selected_deadzone(
      speed, float(baseline_accel), derivative, friction_accel, float(requested_curvature) * speed**2, original_anchor=source.original_effect_anchor
    )
    return candidate if finite(candidate) and candidate >= 0 else baseline_accel
  except (AttributeError, TypeError, ValueError, OverflowError):
    return baseline_accel


def apply_selected_output(parent, cs, vm, params, requested_curvature, result, *, active, limited, curvature_limited=False):
  source = _selected_source(parent)
  if (
    source is None
    or not active
    or (limited and (curvature_limited or not bounded_profile_on_limited_frame(parent)))
    or cs.steeringPressed
    or (manual(parent) and source is not getattr(parent, 'manual_flm_effects', None))
    or cs.vEgo < 0.1 * 0.44704
  ):
    return result
  command, angle, log = result
  try:
    speed = float(cs.vEgo)
    desired_angle = math.degrees(vm.get_steer_from_curvature(-float(requested_curvature), speed, float(params.roll)))
    actual_angle = float(cs.steeringAngleDeg) - float(params.angleOffsetDeg)
    # Selected policies return negative normalized torque; Dom's surface API
    # operates before that final sign conversion, in positive policy convention.
    candidate = source.selected_output(
      speed,
      float(requested_curvature) * speed**2,
      desired_angle,
      actual_angle,
      -float(command),
      abs(float(parent.torque_params.friction)),
      original_anchor=source.original_effect_anchor,
    )
    if not finite(candidate):
      return result
    candidate = -max(-parent.steer_max, min(parent.steer_max, candidate))
    if candidate != command:
      log.output = float(candidate)
    return candidate, angle, log
  except (AttributeError, TypeError, ValueError, OverflowError):
    return result


class EffectReplayRecordedSource(EffectReplaySource):
  """Reconstruct the actually applied full30 state, not the fitted target."""

  def __init__(self, trace):
    trace.validate()
    envelope = trace.envelope
    values = dict(trace.applied)
    group = {n: values[n] for n in envelope.trial_names}
    self.source = CanonicalSurfaceEffects(group, envelope.selected_baseline_revision, accumulated=values, allow_limited_profile=True)
    self.original_effect_anchor = self.source.original_effect_anchor
    self.applied_coordinates = self.source.applied_coordinates


class EffectReplayTransition:
  """One candidate's full30 slew history, reset ONLY at actual checkpoint windows.

  Every frame is retained; clipping/fault context is never turned into a fitting
  endpoint. The caller supplies the exact original trace when effects existed.
  """

  def __init__(self, trial):
    if isinstance(trial, EffectRestoreAblationTrial):
      raise ValueError('causal group-off analysis cannot become an admitted transition')
    if not isinstance(trial, EffectTrial):
      raise ValueError('typed original effect trial')
    self.trial = trial
    self.names, self.anchor, prior = trial.basis()
    self.original_target = dict(prior)
    self.target = dict(prior)
    self.recorded_effects_seen = False
    if isinstance(trial, EffectRestoreTrial):
      self.target = dict(trial.restore_envelope.coordinates)
    else:
      self.target.update(zip(self.names, trial.coordinates, strict=True))
    self.applied = None
    self.previous_ns = 0
    self.__source_key = self.__source = None

  def reset(self):
    self.applied = None
    self.previous_ns = 0
    self.recorded_effects_seen = False
    self.__source_key = self.__source = None

  def source_for_frame(self, control_ns, trace=None, *, new_owner=False):
    from openpilot.starpilot.flm.effect_trace import step_full

    if new_owner and self.applied is not None:
      raise ValueError('event new owner cannot reset an active transition')
    qualified = True
    if trace is not None:
      trace.validate()
      if trace.control_ns != control_ns:
        raise ValueError('actual owner prepare clock differs from replay')
      payload = self.trial.original_anchor
      envelope = trace.envelope
      if envelope.selected_baseline_revision != self.trial.selected_baseline_revision or any(
        getattr(envelope, key) != payload[key]
        for key in ('cp_sha256', 'controller_revision', 'source_epoch', 'reset_generation', 'context_key', 'persistent_source_epoch')
      ):
        raise ValueError('foreign recorded effect transition')
      if dict(trace.target) != self.original_target:
        raise ValueError('recorded accepted full target changed within event window')
      qualified = trace.qualified
      self.recorded_effects_seen = True
      if trace.previous_ns == 0:
        self.previous_ns = 0  # Actual owner/group switch uses its fixed first tick.
    elif self.recorded_effects_seen:
      qualified = False  # Actual profile withdrawal also withdraws the trial.
    elif self.applied is None and any(self.original_target.values()):
      raise ValueError('nonzero original profile has no exact recorded applied trace')
    if self.applied is None:
      self.applied = dict.fromkeys(NAMES, 0.0) if trace is None else dict(trace.before)
      self.previous_ns = 0 if new_owner or trace is None else trace.previous_ns
    self.applied = step_full(self.applied, self.target, now_ns=control_ns, previous_ns=self.previous_ns, qualified=qualified)
    self.previous_ns = control_ns
    source = EffectReplaySource.__new__(EffectReplaySource)
    # Per-transition geometry only: trace validation and exact step_full above
    # still run EVERY frame. Packed doubles preserve signed zero and full30.
    import struct

    key = self.trial.selected_baseline_revision, self.names, struct.pack('!30d', *(self.applied[n] for n in NAMES))
    if key != self.__source_key:
      group = {n: self.applied[n] for n in self.names}
      self.__source = CanonicalSurfaceEffects(group, self.trial.selected_baseline_revision, accumulated=self.applied, allow_limited_profile=True)
      self.__source_key = key
    source.source = self.__source
    # The ordinary _run -> begin_frame path still resets ALL call counters and
    # enforces the real frame clock. No mutable frame state crosses transitions.
    source.original_effect_anchor = source.source.original_effect_anchor
    source.applied_coordinates = source.source.applied_coordinates
    return source
