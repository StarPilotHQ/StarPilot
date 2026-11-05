"""Route-derived next-drive settings; never live trial history or a permission.

The offroad fitter supplies numerical evidence. This module only enforces
document integrity, exact-context startup selection and atomic parked updates.
"""

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path

from openpilot.starpilot.flm.canonical_surface_adapter import (
  NAMES as CANONICAL_NAMES,
  coordinate_bounds as canonical_bounds,
)
from openpilot.starpilot.flm.offroad_surface import (
  ROUTE_MANIFEST_SHA256,
  LEGACY_MANIFEST_SHA256,
  RouteSurfaceEffects,
  ROUTE_NAMES as NAMES,
  CURVE_NAMES,
  route_bounds as coordinate_bounds,
)
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved

NAMESPACE = 'routeSurfaces'
MAX_SCOPES = 8
MAX_ROUTE_STEP = 0.01
KEY = 'AutomaticSteeringProfiles'
MAX_BYTES = 32768


def _hash(value):
  return type(value) is str and len(value) == 64 and not value.strip('0123456789abcdef')


def _number(value):
  return type(value) in (int, float) and math.isfinite(value)


def _digest(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class RouteScope:
  cp_sha256: str
  controller_revision: str
  baseline_revision: str
  persistent_source_epoch: str
  reset_generation: str
  controller_mode: str
  controller_domain: str
  manifest_sha256: str = ROUTE_MANIFEST_SHA256

  def validate(self):
    if (
      any(not _hash(v) for v in (self.cp_sha256, self.baseline_revision, self.persistent_source_epoch))
      or type(self.controller_revision) is not str
      or not 1 <= len(self.controller_revision) <= 192
      or type(self.reset_generation) is not str
      or not 1 <= len(self.reset_generation) <= 64
      or type(self.controller_mode) is not str
      or not 1 <= len(self.controller_mode) <= 32
      or self.controller_domain not in ('torque', 'torque_pid')
      or self.manifest_sha256 not in (ROUTE_MANIFEST_SHA256, LEGACY_MANIFEST_SHA256)
    ):
      raise ValueError('Exact route tune controller/settings scope required')
    from openpilot.starpilot.lateral.controller_selection import ControllerMode

    if self.controller_mode != 'native':
      ControllerMode(self.controller_mode)
    return self

  def document(self):
    self.validate()
    return dict(self.__dict__)

  def key(self):
    return _digest(self.document())


def _coordinates(value, scope):
  names = CANONICAL_NAMES if scope.manifest_sha256 == LEGACY_MANIFEST_SHA256 else NAMES
  if type(value) is not dict or set(value) != set(names):
    raise ValueError('Complete route coordinates for this exact manifest required')
  for name in names:
    low, high = canonical_bounds(name) if scope.manifest_sha256 == LEGACY_MANIFEST_SHA256 else coordinate_bounds(name)
    if not _number(value[name]) or not low <= value[name] <= high:
      raise ValueError('Route coordinate outside original envelope')
  if scope.controller_domain == 'torque_pid':
    from openpilot.starpilot.flm.pid_stages import UNSUPPORTED

    if any(value.get(name, 0.0) != 0 for name in (*UNSUPPORTED, *CURVE_NAMES)):
      raise ValueError('Torque PID cannot use friction-only coordinates')
  RouteSurfaceEffects({NAMES[0]: value[NAMES[0]]}, scope.baseline_revision, accumulated=value)
  return value


def validate_row(row):
  fields = {'scope', 'values', 'previous', 'route_sha256s', 'source_start_ns', 'source_end_ns', 'analysis_sha256', 'completed_wall_ns', 'sequence', 'state'}
  if type(row) is not dict or set(row) != fields or type(row['scope']) is not dict:
    raise ValueError('Versioned route tune row required')
  try:
    scope = RouteScope(**row['scope']).validate()
  except TypeError as error:
    raise ValueError('Route tune scope shape') from error
  values, previous = (_coordinates(row[key], scope) for key in ('values', 'previous'))
  if any(abs(values[n] - previous[n]) > MAX_ROUTE_STEP + 1e-12 for n in values):
    raise ValueError('One route cannot accumulate more than its bounded step')
  if (
    type(row['route_sha256s']) is not list
    or not 1 <= len(row['route_sha256s']) <= 8
    or len(set(row['route_sha256s'])) != len(row['route_sha256s'])
    or any(not _hash(v) for v in row['route_sha256s'])
    or not _hash(row['analysis_sha256'])
    or type(row['source_start_ns']) is not int
    or type(row['source_end_ns']) is not int
    or not 0 < row['source_start_ns'] < row['source_end_ns']
    or type(row['completed_wall_ns']) is not int
    or row['completed_wall_ns'] <= 0
    or type(row['sequence']) is not int
    or not 1 <= row['sequence'] <= 1_000_000
    or row['state'] not in ('ready_for_next_drive', 'accepted_after_drive', 'rolled_back')
  ):
    raise ValueError('Route provenance/sequence required')
  return scope


def validate_rows(rows):
  if type(rows) is not dict or len(rows) > MAX_SCOPES:
    raise ValueError('Bounded route tune history required')
  for key, row in rows.items():
    if key != validate_row(row).key():
      raise ValueError('Route scope key differs from actual row')
  return rows


def remember(raw, row):
  from openpilot.starpilot.flm.learned_lifecycle import decode, encode

  scope = validate_row(row)
  doc = decode(raw)
  if doc['version'] == 1:
    doc = {'version': 3, 'profiles': doc['profiles'], 'fullSurfaces': {}, NAMESPACE: {}}
  elif doc['version'] == 2:
    doc = dict(doc, version=3, routeSurfaces={})
  rows = doc[NAMESPACE]
  previous = rows.get(scope.key())
  if previous is None:
    if len(rows) >= MAX_SCOPES or row['sequence'] != 1 or any(row['previous'].values()):
      raise ValueError('Route store full or missing exact initial baseline')
  else:
    if (
      row['sequence'] != previous['sequence'] + 1
      or row['previous'] != previous['values']
      or set(row['route_sha256s']) & set(previous['route_sha256s'])
      or row['completed_wall_ns'] <= previous['completed_wall_ns']
    ):
      raise ValueError('Stale baseline, repeated drive or reordered route')
  rows[scope.key()] = row
  return encode(doc)


class RouteStorage:
  def __init__(self, params):
    self.params = params
    self.directory = Path(params.get_param_path(KEY)).parent.resolve()

  def save(self, row, *, expected, authorized):
    """Stage a qualified, reversible NEXT-drive trial, not confirmed benefit.

    The caller owns independent tracking and ALL original controller/native
    checks. Actual improvement is assessed on a distinct completed next drive.
    """
    validate_row(row)
    if row['state'] != 'ready_for_next_drive':
      return False

    def permitted():
      return self.directory == Path(self.params.get_param_path(KEY)).parent.resolve() and self.directory.stat().st_uid == os.geteuid() and authorized()

    if not permitted():
      return False
    current, readable = read_saved(self.params, KEY, MAX_BYTES)
    if not readable or current != expected:
      return False
    desired = remember(expected, row)
    result = commit_exact(self.params, key=KEY, max_bytes=MAX_BYTES, raw=desired, expected=expected, authorized=permitted, temp_prefix='.route-steering-')
    return result.committed and result.verified

  def rollback(self, *, scope, expected, authorized):
    from openpilot.starpilot.flm.learned_lifecycle import decode, encode

    doc = decode(expected)
    row = doc.get(NAMESPACE, {}).get(scope.key())
    if row is None or validate_row(row) != scope or row['state'] != 'ready_for_next_drive':
      return False
    # Exact prior accepted map; no fit or synthetic new route is performed.
    previous = dict(row['previous'])
    row.update(values=previous, previous=dict(previous), state='rolled_back')
    desired = encode(doc)

    def permitted():
      return self.directory == Path(self.params.get_param_path(KEY)).parent.resolve() and self.directory.stat().st_uid == os.geteuid() and authorized()

    result = commit_exact(self.params, key=KEY, max_bytes=MAX_BYTES, raw=desired, expected=expected, authorized=permitted, temp_prefix='.route-rollback-')
    return result.committed and result.verified

  def resolve(self, *, scope, expected, outcome, route_sha256, completed_wall_ns, analysis_sha256, authorized):
    """One distinct completed drive resolves a provisional map exactly once."""
    from openpilot.starpilot.flm.learned_lifecycle import decode, encode

    if outcome not in ('accepted_after_drive', 'rolled_back') or not _hash(route_sha256) or not _hash(analysis_sha256):
      raise ValueError('Explicit assessed next-drive outcome required')
    doc = decode(expected)
    row = doc.get(NAMESPACE, {}).get(scope.key())
    if (
      row is None
      or validate_row(row) != scope
      or row['state'] not in ('ready_for_next_drive', 'accepted_after_drive')
      or row['state'] == 'accepted_after_drive'
      and outcome != 'rolled_back'
      or route_sha256 in row['route_sha256s']
      or type(completed_wall_ns) is not int
      or completed_wall_ns <= row['completed_wall_ns']
    ):
      return False
    if outcome == 'rolled_back':
      row['values'] = dict(row['previous'])
      row['previous'] = dict(row['previous'])
    row.update(state=outcome, route_sha256s=(row['route_sha256s'] + [route_sha256])[-8:], completed_wall_ns=completed_wall_ns, analysis_sha256=analysis_sha256)
    validate_row(row)
    desired = encode(doc)

    def permitted():
      return self.directory == Path(self.params.get_param_path(KEY)).parent.resolve() and self.directory.stat().st_uid == os.geteuid() and authorized()

    result = commit_exact(self.params, key=KEY, max_bytes=MAX_BYTES, raw=desired, expected=expected, authorized=permitted, temp_prefix='.route-outcome-')
    return result.committed and result.verified


def startup_row(params, scope):
  """Read once after actual CP/controller/settings selection; no stale retagging."""
  from openpilot.starpilot.flm.learned_lifecycle import decode

  scope.validate()
  raw, readable = read_saved(params, KEY, MAX_BYTES)
  if not readable:
    return None, 'unreadable'
  try:
    row = decode(raw).get(NAMESPACE, {}).get(scope.key())
    if row is None:
      return None, 'not_configured'
    if validate_row(row) != scope:
      return None, 'context_changed'
    return row, 'route_tune_loaded'
  except (KeyError, TypeError, ValueError, OverflowError):
    return None, 'invalid'


def startup_profile(params, scope):
  row, status = startup_row(params, scope)
  return (None if row is None else tuple((n, float(row['values'][n])) for n in NAMES)), status
