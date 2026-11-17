"""Bounded historical profile storage and current application display."""

from collections.abc import Callable
from typing import cast

from openpilot.starpilot.flm.adaptive_profile import ApplicationOutcome
import json
import hashlib
import math

KEY = 'AutomaticSteeringProfiles'
MAX_BYTES = 32768
MAX_PROFILES = 32


def _pairs(items):
  result = {}
  for key, value in items:
    if key in result:
      raise ValueError('Duplicate field')
    result[key] = value
  return result


def _hex(value):
  return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def decode(raw):
  if raw is None:
    return {'version': 1, 'profiles': {}}
  if type(raw) is not bytes or not raw or len(raw) > MAX_BYTES:
    raise ValueError('Invalid profile document')
  try:
    doc = json.loads(raw, object_pairs_hook=_pairs)
  except (RecursionError, UnicodeError) as error:
    raise ValueError('Invalid profile document') from error
  if (
    type(doc) is not dict
    or set(doc)
    != (
      {'version', 'profiles'}
      if doc.get('version') == 1
      else {'version', 'profiles', 'fullSurfaces', 'routeSurfaces'}
      if doc.get('version') == 3
      else {'version', 'profiles', 'fullSurfaces'}
    )
    or type(doc['version']) is not int
    or doc['version'] not in (1, 2, 3)
    or type(doc['profiles']) is not dict
    or len(doc['profiles']) > MAX_PROFILES
  ):
    raise ValueError('Invalid profile document')
  for identity, row in doc['profiles'].items():
    if (
      not _hex(identity)
      or type(row) is not dict
      or set(row) != {'identity', 'reset', 'policy', 'parameters', 'coefficients', 'validation'}
      or not _hex(row['identity'])
      or type(row['reset']) is not str
      or not row['reset']
      or len(row['reset']) > 64
      or row['policy'] != 'passive_stage_ridge_v1'
      or not _hex(row['validation'])
      or type(row['parameters']) is not list
      or not 0 < len(row['parameters']) <= 24
      or type(row['coefficients']) is not list
      or len(row['coefficients']) != len(row['parameters'])
    ):
      raise ValueError('Invalid stored profile')
    names = set()
    for parameter, value in zip(row['parameters'], row['coefficients'], strict=True):
      if (
        type(parameter) is not list
        or len(parameter) != 2
        or any(type(x) is not str or not x or len(x) > 128 for x in parameter)
        or parameter[0] in names
        or (type(value) is not int and type(value) is not float)
        or not math.isfinite(value)
      ):
        raise ValueError('Invalid stored coordinate')
      names.add(parameter[0])
    expected_scope = hashlib.sha256(json.dumps([row['identity'], row['parameters']], separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if identity != expected_scope:
      raise ValueError('Stored profile scope mismatch')
  if doc['version'] in (2, 3):
    from openpilot.starpilot.flm.full_surface_store import validate_rows

    validate_rows(doc['fullSurfaces'])
  if doc['version'] == 3:
    from openpilot.starpilot.flm.offroad_store import validate_rows as validate_routes

    validate_routes(doc['routeSurfaces'])
  return doc


def encode(doc):
  raw = json.dumps(doc, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
  decode(raw)
  return raw


def scope_key(identity, parameters):
  if not _hex(identity):
    raise ValueError('Invalid source identity')
  return hashlib.sha256(json.dumps([identity, [[p.name, p.stage_revision] for p in parameters]], separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def remember(raw, profile, *, reset, validation_sha256):
  """Existing low-rate producer owner only; caller independently admits profile."""
  doc = decode(raw)
  row = {
    'identity': profile.identity,
    'reset': reset,
    'policy': profile.policy,
    'parameters': [[p.name, p.stage_revision] for p in profile.parameters],
    'coefficients': list(profile.coefficients),
    'validation': validation_sha256,
  }
  scope = scope_key(profile.identity, profile.parameters)
  if scope not in doc['profiles'] and len(doc['profiles']) == MAX_PROFILES:
    raise ValueError('Profile store is full')
  doc['profiles'][scope] = row
  return encode(doc)


def historical(raw, *, identity, parameters, reset):
  """Returns historical coefficients only: never a fresh core Profile/receipt."""
  row = decode(raw)['profiles'].get(scope_key(identity, parameters))
  if row is None or row['identity'] != identity or row['reset'] != reset or row['parameters'] != [[p.name, p.stage_revision] for p in parameters]:
    return None
  values = tuple(row['coefficients'])
  if any(not p.low <= v <= p.high or abs(v) > p.max_step for p, v in zip(parameters, values, strict=True)):
    return None
  return values


def reset_scope(raw, identity):
  doc = decode(raw)
  doc['profiles'] = {scope: row for scope, row in doc['profiles'].items() if row['identity'] != identity}
  return encode(doc)


def display(*, identity, now_ns, enabled, manual, eligible, active, healthy, application=None, collecting=False):
  if not eligible:
    return 'paused', 'Adapter unavailable'
  if not enabled:
    return 'paused', 'Learning is off'
  if manual:
    return 'paused', 'Manual tuning selected'
  if not active or not healthy:
    return 'paused', 'Waiting for active, healthy steering'
  if (
    isinstance(application, ApplicationOutcome)
    and application.status == 'applied'
    and _hex(identity)
    and application.identity == identity
    and type(application.profile_sequence) is int
    and application.profile_sequence >= 0
    and type(application.observed_ns) is int
    and type(now_ns) is int
    and 0 < application.observed_ns <= now_ns <= application.observed_ns + 500_000_000
    and application.actual_coordinates
    and any(v != 0 for v in application.actual_coordinates)
    and all(type(v) in (int, float) and math.isfinite(v) for v in application.actual_coordinates)
  ):
    return 'applied', 'Qualified learning is applied'
  return ('collecting', 'Collecting driving evidence') if collecting else ('paused', 'Waiting for current evidence')


class StorageOwner:
  """Low-rate existing producer-process work; never called by controller tick."""

  def __init__(self, params):
    self.params = params

  def save(self, profile, *, reset, replay_validation, authorized, full_binding=None):
    from openpilot.starpilot.flm.adaptive_profile import replay_accepts
    from dataclasses import asdict
    import hashlib
    from openpilot.starpilot.saved_document import commit_exact
    from openpilot.starpilot.saved_source import read_saved

    if not replay_accepts(replay_validation, profile):
      return False
    validation_sha256 = hashlib.sha256(json.dumps(asdict(replay_validation), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    raw, readable = read_saved(self.params, KEY, MAX_BYTES)
    if not readable or not authorized():
      return False
    try:
      if any(not p.low <= v <= p.high or abs(v) > p.max_step for p, v in zip(profile.parameters, profile.coefficients, strict=True)):
        return False
      if profile.policy in ('event_effect_coordinate_v2', 'event_effect_restore_v1'):
        from openpilot.starpilot.flm.full_surface_store import remember_full

        if (
          profile.effect_envelope is None
          or profile.effect_envelope.reset_generation != reset
          or profile.effect_envelope.source_binding() != full_binding
          or replay_validation.baseline_revision != profile.effect_envelope.selected_baseline_revision
        ):
          return False
        desired = remember_full(raw, profile, validation_sha256=validation_sha256)
      else:
        desired = remember(raw, profile, reset=reset, validation_sha256=validation_sha256)
    except (AttributeError, TypeError, ValueError, OverflowError):
      return False
    result = commit_exact(self.params, key=KEY, max_bytes=MAX_BYTES, raw=desired, expected=raw, authorized=authorized, temp_prefix='.automatic-steering-')
    return result.committed and result.verified


class ProducerPersistence:
  """Invoked after publication by the existing learner process, at most once/second."""

  def __init__(self, params):
    self.storage = StorageOwner(params)
    self.last_ns = None
    self.saved = None

  def update(self, profile, replay_validation, *, now_ns, reset, current_sources, current_full_binding=None):
    try:
      return self._update(profile, replay_validation, now_ns=now_ns, reset=reset, current_sources=current_sources, current_full_binding=current_full_binding)
    except (AttributeError, TypeError, ValueError, OverflowError, OSError, RuntimeError):
      return False

  def _update(self, profile, replay_validation, *, now_ns, reset, current_sources, current_full_binding=None):
    from openpilot.starpilot.flm.adaptive_profile import replay_accepts

    if type(now_ns) is not int or now_ns <= 0:
      return False
    if self.last_ns is not None and (now_ns < self.last_ns or now_ns - self.last_ns < 1_000_000_000):
      return False
    self.last_ns = now_ns
    if not replay_accepts(replay_validation, profile):
      return False
    captured = current_sources()
    # Source owner provides exact current identity plus enable/manual/reset state.
    if captured != (profile.identity, True, False, reset):
      return False
    token = (
      profile.identity,
      tuple(profile.parameters),
      tuple(profile.coefficients),
      reset,
      replay_validation.baseline_revision,
      replay_validation.physical_model_revision,
      None if getattr(profile, 'effect_envelope', None) is None else profile.effect_envelope.full_profile_sha256,
    )
    if token == self.saved:
      return False

    full_binding = None
    if profile.policy in ('event_effect_coordinate_v2', 'event_effect_restore_v1'):
      if current_full_binding is None:
        return False
      full_binding = current_full_binding()
      if profile.effect_envelope is None or profile.effect_envelope.source_binding() != full_binding:
        return False

    def authorized():
      return current_sources() == captured and (full_binding is None or cast(Callable[[], object], current_full_binding)() == full_binding)

    committed = self.storage.save(profile, reset=reset, replay_validation=replay_validation, authorized=authorized, full_binding=full_binding)
    if committed:
      self.saved = token
    return committed

  def full_prior(self, *, identity, binding, reset, current_sources):
    from openpilot.starpilot.flm.full_surface_store import load_prior
    from openpilot.starpilot.saved_source import read_saved

    try:
      captured = current_sources()
      if captured != (identity, True, False, reset) or binding[3] != reset:
        return None
      raw, readable = read_saved(self.storage.params, KEY, MAX_BYTES)
      if not readable:
        return None
      prior = load_prior(raw, binding=binding)
      return prior if current_sources() == captured else None
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError, OSError, RuntimeError):
      return None

  def prior(self, *, identity, parameters, reset, current_sources):
    try:
      return self._prior(identity=identity, parameters=parameters, reset=reset, current_sources=current_sources)
    except (AttributeError, TypeError, ValueError, OverflowError, OSError, RuntimeError):
      return None

  def _prior(self, *, identity, parameters, reset, current_sources):
    """Historical ridge centre only. Fresh collector/model/replay still required."""
    from openpilot.starpilot.saved_source import read_saved

    captured = current_sources()
    if captured != (identity, True, False, reset):
      return None
    raw, readable = read_saved(self.storage.params, KEY, MAX_BYTES)
    if not readable:
      return None
    try:
      values = historical(raw, identity=identity, parameters=parameters, reset=reset)
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None
    return values if current_sources() == captured else None


class SettingsLeaseCache:
  """Source objects were validated by read_source_receipt; no Params/JSON hot IO."""

  def __init__(self, cp_sha256):
    self.cp_sha256 = cp_sha256
    self.source = None
    self.received_ns = self.last_ns = None

  def install(self, source, *, received_ns):
    from openpilot.starpilot.flm.runtime_adapter_registry import SourceState

    if (
      not isinstance(source, SourceState)
      or source.cp_sha256 != self.cp_sha256
      or type(received_ns) is not int
      or not 0 < source.observed_ns <= received_ns <= source.expires_ns <= source.observed_ns + 500_000_000
    ):
      self.source = self.received_ns = None
      return False
    self.source, self.received_ns = source, received_ns
    return True

  def current(self, *, now_ns):
    if type(now_ns) is not int or now_ns <= 0 or self.last_ns is not None and now_ns < self.last_ns:
      self.source = self.received_ns = None
      self.last_ns = None
      return None
    self.last_ns = now_ns
    if (
      self.source is None
      or self.received_ns is None
      or not 0 <= now_ns - self.received_ns <= 500_000_000
      or not self.source.observed_ns <= now_ns <= self.source.expires_ns
    ):
      return None
    return self.source
