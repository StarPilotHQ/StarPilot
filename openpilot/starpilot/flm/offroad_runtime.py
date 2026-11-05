"""Cold-start route settings through the existing bounded controller stages.

No permissions, response witnesses, ACKs or source clocks are loaded from disk.
"""

import time

from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES as NAMES
from openpilot.starpilot.flm.offroad_store import RouteScope, startup_row
from openpilot.starpilot.flm.offroad_surface import RouteSurfaceEffects, STOP_REASONS, stop_marker


def configuration_data(cp, ci, controller, params, *, manual_status):
  from opendbc.car.vin import VIN_UNKNOWN, is_valid_vin
  from openpilot.starpilot.flm.runtime_adapter_registry import (
    selected_descriptor,
    baseline_values,
    baseline_revision,
    digest,
  )
  from openpilot.starpilot.lateral.controller_selection import selection_from_bytes
  from openpilot.starpilot.lateral.learning_preferences import KEY as PREFERENCE_KEY, read_preference
  from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, LEGACY_KEYS, MAX_DOCUMENT_BYTES, parse_document, interpret_legacy
  from openpilot.starpilot.saved_source import read_saved

  if manual_status != 'not_configured' or not is_valid_vin(str(cp.carVin)) or str(cp.carVin) == VIN_UNKNOWN:
    raise ValueError('Known vehicle and no manual coordinates required')
  descriptor = selected_descriptor(cp, ci, controller)
  if descriptor is None or descriptor.domain not in ('torque', 'torque_pid'):
    raise ValueError('Actual registered torque controller required')
  keys = tuple(
    dict.fromkeys(
      (
        PREFERENCE_KEY,
        'ForceAutoTuneOff',
        DOCUMENT_KEY,
        'AutoSteeringLearningReset',
        'LateralControllerSelection',
        'OpenpilotEnabledToggle',
        'SafeMode',
        'TurnAssist',
        *LEGACY_KEYS,
      )
    )
  )
  snapshots = tuple((key, read_saved(params, key, MAX_DOCUMENT_BYTES)) for key in keys)
  if any(not value[1] for _, value in snapshots):
    raise ValueError('Unreadable saved steering settings')
  raw = {key: value[0] for key, value in snapshots}
  if raw['OpenpilotEnabledToggle'] != b'1' or raw['SafeMode'] not in (None, b'0'):
    raise ValueError('Main controls disabled or Safe Mode selected')
  if raw['TurnAssist'] not in (None, b'0', b'1'):
    raise ValueError('Unreadable or invalid startup turn-assist choice')
  preference = read_preference(params, cp, preference_snapshot=(raw[PREFERENCE_KEY], True), legacy_snapshot=(raw['ForceAutoTuneOff'], True))
  if not preference.valid or not preference.enabled:
    raise ValueError('Automatic learning is off')
  selection = selection_from_bytes(cp, raw['LateralControllerSelection'])
  if selection.source == 'invalid' or descriptor.controller_mode not in (str(selection.mode), 'native'):
    raise ValueError('Actual controller does not match saved selection')
  fixed_manual = ()
  document = raw[DOCUMENT_KEY]
  if document is not None:
    profile = parse_document(document).get(str(cp.carFingerprint))
    if profile is not None:
      if profile.flm is not None or profile.manual_coordinates is not None or profile.geometry is not None and profile.geometry.learning == 'force_off':
        raise ValueError('Manual or explicitly disabled tuning selected')
      if cp.lateralTuning.which() == 'torque':
        tune = cp.lateralTuning.torque
        if profile.basis != (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction)):
          raise ValueError('Saved baseline basis changed')
        from openpilot.starpilot.lateral.torque_settings import resolve_document

        factor, friction, review = resolve_document(
          {str(cp.carFingerprint): profile}, str(cp.carFingerprint), (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
        )
        if review:
          raise ValueError('Manual scalar baseline requires review')
        if (factor is not None or friction is not None) and not (profile.geometry is not None and profile.geometry.learning == 'force_auto'):
          fixed_manual = (
            float(tune.latAccelFactor) if factor is None else factor,
            float(tune.latAccelOffset),
            float(tune.friction) if friction is None else friction,
          )
  elif cp.lateralTuning.which() == 'torque':
    tune = cp.lateralTuning.torque
    legacy = interpret_legacy(
      {key: raw[key] for key in LEGACY_KEYS}, (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction)), fingerprint=str(cp.carFingerprint)
    )
    if legacy.factor is not None or legacy.friction is not None:
      fixed_manual = (
        float(tune.latAccelFactor) if legacy.factor is None else legacy.factor,
        float(tune.latAccelOffset),
        float(tune.friction) if legacy.friction is None else legacy.friction,
      )
  reset = '0' if raw['AutoSteeringLearningReset'] is None else raw['AutoSteeringLearningReset'].decode('ascii')
  if not 1 <= len(reset) <= 64 or reset.strip('0123456789abcdef'):
    raise ValueError('Invalid reset generation')
  # Settings identity only. It does not describe current physical calibration.
  epoch = digest(('persistent_vehicle_source_v1', descriptor.cp_sha256, tuple((k, None if raw[k] is None else raw[k].hex()) for k in keys), str(cp.carVin)))
  numeric = baseline_values(controller)
  scope = RouteScope(
    descriptor.cp_sha256, descriptor.controller_revision, baseline_revision(descriptor, numeric), epoch, reset, descriptor.controller_mode, descriptor.domain
  ).validate()
  if tuple((key, read_saved(params, key, MAX_DOCUMENT_BYTES)) for key in keys) != snapshots:
    raise ValueError('Steering settings changed during startup')
  return scope, numeric, fixed_manual


def configuration_scope(cp, ci, controller, params, *, manual_status):
  scope, numeric, _ = configuration_data(cp, ci, controller, params, manual_status=manual_status)
  return scope, numeric


def fixed_manual_numeric(controller, values):
  """Expected immutable scalar configuration; never a control/authority write."""
  from types import SimpleNamespace
  from openpilot.starpilot.flm.native_baseline import _raw_effective
  from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values

  tune = controller.torque_params.as_reader().as_builder()
  factor, offset, friction = _raw_effective(controller, values)
  tune.latAccelFactor = factor
  tune.latAccelOffset = offset
  tune.friction = friction
  shadow = SimpleNamespace(
    steer_max=controller.steer_max,
    torque_params=tune,
    pid=SimpleNamespace(
      neg_limit=controller.lateral_accel_from_torque(-controller.steer_max, tune),
      pos_limit=controller.lateral_accel_from_torque(controller.steer_max, tune),
      _k_p=controller.pid._k_p,
      _k_i=controller.pid._k_i,
    ),
  )
  for name in ('ff_factor', 'kf'):
    if hasattr(controller, name):
      setattr(shadow, name, getattr(controller, name))
  return baseline_values(shadow)


class RouteEffects:
  """Exact cold-start configuration, guarded by existing per-frame stages."""

  def __init__(self, scope, values, numeric, *, previous=None, fixed_numeric=None):
    scope.validate()
    if type(values) is not tuple or tuple(name for name, _ in values) != tuple(NAMES):
      raise ValueError('Ordered full route map required')
    self.scope, self.values, self.numeric = scope, values, numeric
    self.fixed_numeric = fixed_numeric
    self.previous = values if previous is None else previous
    if tuple(n for n, _ in self.previous) != tuple(NAMES):
      raise ValueError('Full previous route map required')
    self.quality_stopped = False
    self.quality_markers = frozenset(stop_marker(scope.key(), dict(values), r) for r in STOP_REASONS)
    self.source = RouteSurfaceEffects(dict(values[:1]), scope.baseline_revision, accumulated=dict(values))
    self.original_effect_anchor = self.source.original_effect_anchor
    self.enabled = True
    self.frame_allowed = False
    self.admitted_ns = 0
    self.changed_calls = 0
    self.calls = 0
    self.status = 'configured_pending_actual_stage'

  def observe_current_status(self, reason):
    if self.quality_stopped:
      return
    if reason in self.quality_markers:
      # Revocation only. This cannot supply a frame token or fresh permission.
      self.admitted_ns = 0
      self.frame_allowed = False
      self.quality_stopped = True
      self.values = self.previous
      self.source = RouteSurfaceEffects(dict(self.values[:1]), self.scope.baseline_revision, accumulated=dict(self.values))
      self.original_effect_anchor = self.source.original_effect_anchor
      self.status = 'quality_guard_returned_to_prior_accepted_map'

  def admit_current_runtime(self, runtime):
    from openpilot.starpilot.flm.global_runtime import GlobalRuntime
    from openpilot.starpilot.flm.runtime_adapter_registry import SourceState

    self.admitted_ns = 0
    if type(runtime) is not GlobalRuntime or getattr(runtime.controller, 'route_flm_effects', None) is not self:
      return
    source = runtime.source
    descriptor = runtime.descriptor
    now = runtime.now_ns
    if (
      type(source) is SourceState
      and type(now) is int
      and source.configuration_known
      and source.enabled
      and not source.manual
      and runtime.eligible
      and runtime.active
      and runtime.healthy
      and source.persistent_source_epoch == self.scope.persistent_source_epoch
      and descriptor.cp_sha256 == self.scope.cp_sha256
      and descriptor.controller_revision == self.scope.controller_revision
      and descriptor.controller_mode == self.scope.controller_mode
      and descriptor.domain == self.scope.controller_domain
      and source.observed_ns <= now <= source.expires_ns <= source.observed_ns + 500_000_000
    ):
      # prepare() has already checked current native/pose/health/source clocks.
      # This token is single-frame, never loaded from the stored route map.
      self.admitted_ns = now

  def begin_frame(self, controller):
    from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values

    now = time.monotonic_ns()
    admitted = self.admitted_ns
    self.admitted_ns = 0
    self.frame_allowed = False
    try:
      current = baseline_values(controller)
      if current != self.numeric:
        from openpilot.starpilot.flm.native_baseline import accepts_refinement

        receipt = getattr(controller, 'native_learning_refinement', None)
        fixed = bool(self.fixed_numeric is not None and current == self.fixed_numeric and 0 < admitted <= now <= admitted + 30_000_000)
        if not fixed and not accepts_refinement(controller, receipt, before=self.numeric, after=current, now_ns=now):
          self.enabled = False
          self.status = 'baseline_changed'
        else:
          self.numeric = current
      self.source.begin_frame(now)
    except (AttributeError, TypeError, ValueError, OverflowError):
      self.enabled = False
      self.status = 'baseline_unavailable'
    self.frame_allowed = bool(self.enabled and 0 < admitted <= now <= admitted + 30_000_000)

  def _call(self, name, baseline, *args, **kwargs):
    if not self.enabled or not self.frame_allowed:
      return baseline
    result = getattr(self.source, name)(*args, **kwargs)
    self.calls += 1
    if result != baseline:
      self.changed_calls += 1
      self.status = 'actual_bounded_stage_changed'
    return result

  def selected_stage(self, frame, **kwargs):
    return self._call('selected_stage', (frame.baseline_ff, frame.baseline_threshold), frame, **kwargs)

  def selected_pid_stage(self, frame, **kwargs):
    return self._call('selected_pid_stage', frame.baseline_ff, frame, **kwargs)

  def selected_pid_error(self, frame, **kwargs):
    return self._call('selected_pid_error', frame.error_deg, frame, **kwargs)

  def selected_friction(self, baseline, **kwargs):
    return self._call('selected_friction', baseline, baseline, **kwargs)

  def selected_deadzone(self, *args, **kwargs):
    return self._call('selected_deadzone', args[1], *args, **kwargs)

  def selected_output(self, *args, **kwargs):
    return self._call('selected_output', args[4], *args, **kwargs)


def selected_route(parent):
  value = getattr(parent, 'route_flm_effects', None)
  return value if type(value) is RouteEffects and value.enabled and value.frame_allowed else None


def configure(cp, ci, controller, params, *, manual_status):
  controller.route_flm_scope = None
  controller.route_flm_scope_key = ''
  try:
    scope, numeric, fixed_manual = configuration_data(cp, ci, controller, params, manual_status=manual_status)
    controller.route_flm_scope = scope
    controller.route_flm_scope_key = scope.key()
    row, status = startup_row(params, scope)
    if row is None:
      return None, status
    values = tuple((n, float(row['values'][n])) for n in NAMES)
    previous = tuple((n, float(row['previous'][n])) for n in NAMES)
    fixed = None if not fixed_manual else fixed_manual_numeric(controller, fixed_manual)
    return RouteEffects(scope, values, numeric, previous=previous, fixed_numeric=fixed), 'configured_pending_actual_stage'
  except (AttributeError, KeyError, TypeError, ValueError, UnicodeError, OSError, OverflowError):
    return None, 'configuration_unavailable'
