"""Versioned automatic route metadata; old and new bounds remain distinct.

Legacy/live/manual coordinates stay thirty. The new route schema adds FIVE
explicit relative-threshold fields; no scalar is hidden in another knob's name.
No new Cereal field or native permission is introduced.
"""

from dataclasses import replace
import math
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, coordinate_bounds
from openpilot.starpilot.flm.offroad_surface import (
  REVISION,
  LEGACY_REVISION,
  ROUTE_NAMES,
  route_bounds,
)

BASES = ('torque-selected-stage-snapshot-v1', 'pid-selected-stage-snapshot-v2')
SCHEMAS = {base + '@' + revision: base for revision in (LEGACY_REVISION, REVISION) for base in BASES}
CONFIGURED_SCHEMAS = {base + '@' + REVISION + '+configured-v1': base for base in BASES}
SCHEMAS.update(CONFIGURED_SCHEMAS)
PREFIX = 'route.coordinate.'
CONFIGURED_PREFIX = 'route.configured.'
SCOPE_PREFIX = 'route.scope.'
MAX_ROUTE_STAGE_VALUES = 40 + 2 * len(ROUTE_NAMES) + 8


def _valid(values, scope, revision=REVISION):
  if revision not in (REVISION, LEGACY_REVISION):
    raise ValueError('Unknown route revision')
  names = NAMES if revision == LEGACY_REVISION else ROUTE_NAMES
  if type(values) is not tuple or tuple(n for n, _ in values) != tuple(names) or type(scope) is not str or len(scope) != 64 or scope.strip('0123456789abcdef'):
    raise ValueError('Complete original route fields and actual scope required')
  for name, value in values:
    low, high = coordinate_bounds(name) if revision == LEGACY_REVISION else route_bounds(name)
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
      raise ValueError('Recorded route value outside existing envelope')


def encode(evidence):
  if evidence.route_effect_revision:
    revision = evidence.route_effect_revision
    schema = evidence.stage_schema_revision
    suffix = '+configured-v1' if schema in CONFIGURED_SCHEMAS else ''
    if revision not in (REVISION, LEGACY_REVISION) or schema not in SCHEMAS or schema != SCHEMAS[schema] + '@' + revision + suffix:
      raise ValueError('Explicit matching route revision required')
    if evidence.live_applied_coordinates or evidence.effect_trace is not None:
      raise ValueError('Route and live trial metadata remain distinct')
    _valid(evidence.route_applied_coordinates, evidence.route_configuration_scope, revision)
    configured = getattr(evidence, 'route_configured_coordinates', ())
    if suffix:
      _valid(configured, evidence.route_configuration_scope, revision)
      configuration_values(evidence.route_applied_coordinates, configured, evidence.replay_inputs)
    elif configured:
      raise ValueError('Configured coordinates require their explicit recording version')
    if len(evidence.stage_values) > 40 or any(n.startswith('route.') for n, _ in evidence.stage_values):
      raise ValueError('Duplicate or overbound original stage metadata')
    words = tuple((SCOPE_PREFIX + str(i), float(int(evidence.route_configuration_scope[i * 8 : (i + 1) * 8], 16))) for i in range(8))
    settings = tuple((CONFIGURED_PREFIX + n, v) for n, v in configured)
    return evidence.stage_values + tuple((PREFIX + n, v) for n, v in evidence.route_applied_coordinates) + settings + words
  if (
    evidence.route_applied_coordinates or evidence.route_configuration_scope
    or evidence.route_configured_coordinates or evidence.stage_schema_revision in SCHEMAS
  ):
    raise ValueError('Route metadata cannot omit revision')
  return evidence.stage_values


def decode(schema, pairs):
  base, coordinates, revision, scope, _ = decode_configuration(schema, pairs)
  return base, coordinates, revision, scope


def decode_configuration(schema, pairs):
  if schema not in SCHEMAS:
    if any(n.startswith('route.') for n, _ in pairs):
      raise ValueError('Unknown route stage schema')
    return pairs, (), '', '', ()
  configured_version = schema in CONFIGURED_SCHEMAS
  revision = REVISION if configured_version else schema.rsplit('@', 1)[1]
  names = NAMES if revision == LEGACY_REVISION else ROUTE_NAMES
  tail = len(names) * (2 if configured_version else 1) + 8
  if not tail <= len(pairs) <= 40 + tail:
    raise ValueError('Recorded route cardinality for this exact version')
  base = pairs[:-tail]
  recorded = pairs[-tail:-8]
  if any(not name.startswith(PREFIX) for name, _ in recorded[:len(names)]) or any(
    not name.startswith(CONFIGURED_PREFIX) for name, _ in recorded[len(names):]
  ):
    raise ValueError('Explicit actual and configured coordinate fields required')
  coordinates = tuple((n.removeprefix(PREFIX), v) for n, v in recorded[:len(names)])
  configured = tuple((n.removeprefix(CONFIGURED_PREFIX), v) for n, v in recorded[len(names):])
  words = pairs[-8:]
  if any(n.startswith('route.') for n, _ in base):
    raise ValueError('Duplicate route field')
  if any(n != SCOPE_PREFIX + str(i) or not math.isfinite(v) or v != int(v) or not 0 <= v < 2**32 for i, (n, v) in enumerate(words)):
    raise ValueError('Actual scope words required')
  scope = ''.join(f'{int(v):08x}' for _, v in words)
  _valid(coordinates, scope, revision)
  if configured_version:
    _valid(configured, scope, revision)
  return base, coordinates, revision, scope, configured


def configuration_values(actual, configured, inputs):
  """Use an explicitly recorded setup, retaining its actual per-tick masking."""
  if not configured:
    return actual  # Legacy recordings never gain an inferred configuration.
  if actual != configured:
    flags = dict(inputs)
    if any(value for _, value in actual) or flags.get('active') != 1.0 or not any(
      flags.get(name) == 1.0 for name in ('safety_limited', 'curvature_limited')
    ):
      raise ValueError('Configured route differs without an original inhibited input')
  return configured


def with_actual_route(evidence, controller):
  scope = getattr(controller, 'route_flm_scope', None)
  if scope is None:
    return evidence
  if evidence.stage_schema_revision not in BASES:
    raise ValueError('Actual selected route stage required')
  from openpilot.starpilot.flm.offroad_runtime import RouteEffects

  owner = getattr(controller, 'route_flm_effects', None)
  values = tuple((name, 0.0) for name in ROUTE_NAMES)
  configured = values
  if owner is not None:
    if type(owner) is not RouteEffects or owner.scope != scope:
      raise ValueError('Unknown actual route owner')
    current = bool(
      owner.enabled
      and owner.frame_allowed
      and evidence.active
      and evidence.valid
      and not (evidence.manual or evidence.override or evidence.fault)
    )
    if current:
      configured = owner.values
      source = owner.source
      complete = (
        source.stage_calls == 1
        and source.output_calls == 1
        and (
          source.friction_calls == 1 and source.deadzone_calls == 1
          if scope.controller_domain == 'torque'
          else source.pid_error_calls == 1 and source.friction_calls == 0
        )
      )
      if complete:
        values = owner.values
      else:
        calls = (source.stage_calls, source.output_calls, source.friction_calls, source.deadzone_calls, source.pid_error_calls)
        inputs = dict(evidence.replay_inputs)
        # Published output feedback is computed AFTER the update. Only its
        # original input flags explain an inhibited, zero-hook controller tick.
        inhibited = inputs.get('active') == 1.0 and any(inputs.get(name) == 1.0 for name in ('safety_limited', 'curvature_limited'))
        if any(calls) or not inhibited:
          raise ValueError('Partial route execution cannot be recorded as applied')
  _valid(values, getattr(controller, 'route_flm_scope_key', ''))
  return replace(
    evidence,
    stage_schema_revision=evidence.stage_schema_revision + '@' + REVISION + '+configured-v1',
    route_applied_coordinates=values,
    route_configured_coordinates=configured,
    route_effect_revision=REVISION,
    route_configuration_scope=getattr(controller, 'route_flm_scope_key', ''),
  )
