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
PREFIX = 'route.coordinate.'
SCOPE_PREFIX = 'route.scope.'
MAX_ROUTE_STAGE_VALUES = 40 + len(ROUTE_NAMES) + 8


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
    if revision not in (REVISION, LEGACY_REVISION) or schema not in SCHEMAS or schema != SCHEMAS[schema] + '@' + revision:
      raise ValueError('Explicit matching route revision required')
    if evidence.live_applied_coordinates or evidence.effect_trace is not None:
      raise ValueError('Route and live trial metadata remain distinct')
    _valid(evidence.route_applied_coordinates, evidence.route_configuration_scope, revision)
    if len(evidence.stage_values) > 40 or any(n.startswith('route.') for n, _ in evidence.stage_values):
      raise ValueError('Duplicate or overbound original stage metadata')
    words = tuple((SCOPE_PREFIX + str(i), float(int(evidence.route_configuration_scope[i * 8 : (i + 1) * 8], 16))) for i in range(8))
    return evidence.stage_values + tuple((PREFIX + n, v) for n, v in evidence.route_applied_coordinates) + words
  if evidence.route_applied_coordinates or evidence.route_configuration_scope or evidence.stage_schema_revision in SCHEMAS:
    raise ValueError('Route metadata cannot omit revision')
  return evidence.stage_values


def decode(schema, pairs):
  if schema not in SCHEMAS:
    if any(n.startswith('route.') for n, _ in pairs):
      raise ValueError('Unknown route stage schema')
    return pairs, (), '', ''
  revision = schema.rsplit('@', 1)[1]
  names = NAMES if revision == LEGACY_REVISION else ROUTE_NAMES
  tail = len(names) + 8
  if not tail <= len(pairs) <= 40 + tail:
    raise ValueError('Recorded route cardinality for this exact version')
  base = pairs[:-tail]
  coordinates = tuple((n.removeprefix(PREFIX), v) for n, v in pairs[-tail:-8])
  words = pairs[-8:]
  if any(n.startswith('route.') for n, _ in base):
    raise ValueError('Duplicate route field')
  if any(n != SCOPE_PREFIX + str(i) or not math.isfinite(v) or v != int(v) or not 0 <= v < 2**32 for i, (n, v) in enumerate(words)):
    raise ValueError('Actual scope words required')
  scope = ''.join(f'{int(v):08x}' for _, v in words)
  _valid(coordinates, scope, revision)
  return base, coordinates, revision, scope


def with_actual_route(evidence, controller):
  scope = getattr(controller, 'route_flm_scope', None)
  if scope is None:
    return evidence
  if evidence.stage_schema_revision not in BASES:
    raise ValueError('Actual selected route stage required')
  from openpilot.starpilot.flm.offroad_runtime import RouteEffects

  owner = getattr(controller, 'route_flm_effects', None)
  values = tuple((name, 0.0) for name in ROUTE_NAMES)
  if owner is not None:
    if type(owner) is not RouteEffects or owner.scope != scope:
      raise ValueError('Unknown actual route owner')
    current = bool(
      owner.enabled
      and owner.frame_allowed
      and evidence.active
      and evidence.valid
      and not (evidence.manual or evidence.override or evidence.fault or evidence.limited)
    )
    if current:
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
      if not complete:
        raise ValueError('Partial route execution cannot be recorded as applied')
      values = owner.values
  _valid(values, getattr(controller, 'route_flm_scope_key', ''))
  return replace(
    evidence,
    stage_schema_revision=evidence.stage_schema_revision + '@' + REVISION,
    route_applied_coordinates=values,
    route_effect_revision=REVISION,
    route_configuration_scope=getattr(controller, 'route_flm_scope_key', ''),
  )
