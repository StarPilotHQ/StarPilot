"""Explicit parked manual coordinates, separate from learned history and authority."""

from dataclasses import dataclass
import hashlib
import json
import math
import time

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, MANIFEST_SHA256, coordinate_bounds


def context(cp, controller, *, recovered=False):
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  from openpilot.starpilot.lateral.controller_selection import ControllerMode
  if (str(cp.steerControlType) != 'torque' or cp.lateralTuning.which() != 'torque'
      or cp.passive or cp.notCar or cp.dashcamOnly):
    raise ValueError('Manual coordinates require an active torque controller')
  from openpilot.starpilot.flm.offroad_surface import ROUTE_MANIFEST_SHA256
  return car_params_digest(cp), str(ControllerMode(controller)), ROUTE_MANIFEST_SHA256 if recovered else MANIFEST_SHA256


def baseline_settings(cp, params):
  """Selected scalar/gain/geometry policy, excluding the recovered coordinates."""
  from dataclasses import replace
  from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, MAX_DOCUMENT_BYTES, parse_document, serialize_document, PlatformProfile, FieldChoice
  from openpilot.starpilot.saved_source import read_saved
  raw, readable = read_saved(params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
  if not readable:
    raise ValueError('Readable selected baseline settings required')
  tune = cp.lateralTuning.torque
  basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
  fingerprint = str(cp.carFingerprint)
  profiles = {} if raw is None else parse_document(raw)
  profile = profiles.get(fingerprint, PlatformProfile(basis, FieldChoice(), FieldChoice()))
  return hashlib.sha256(serialize_document({fingerprint: replace(profile, manual_coordinates=None)})).hexdigest()


@dataclass(frozen=True)
class ManualCoordinates:
  context: tuple[str, str, str]
  values: tuple[tuple[str, float], ...]
  compatibility: str = ''

  def validate(self):
    from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES, route_bounds, RouteSurfaceEffects
    recovered = bool(self.compatibility)
    names = ROUTE_NAMES if recovered else NAMES
    if recovered and (type(self.compatibility) is not str or len(self.compatibility) != 64 or self.compatibility.strip('0123456789abcdef')):
      raise ValueError('Recovered manual tune needs its selected steering identity')
    if (type(self.context) is not tuple or len(self.context) != 3
        or any(type(v) is not str for v in self.context)
        or any(len(self.context[i]) != 64 or self.context[i].strip('0123456789abcdef') for i in (0, 2))
        or not self.context[1] or len(self.context[1]) > 32 or not self.context[1].isascii()
        or type(self.values) is not tuple or len(self.values) != len(names)):
      raise ValueError('Invalid manual coordinate context')
    from openpilot.starpilot.lateral.controller_selection import ControllerMode
    ControllerMode(self.context[1])
    for name, row in zip(names, self.values, strict=True):
      if type(row) is not tuple or len(row) != 2 or row[0] != name:
        raise ValueError('Exactly thirty ordered manual coordinates required')
      low, high = route_bounds(name) if recovered else coordinate_bounds(name)
      if type(row[1]) not in (int, float) or not math.isfinite(row[1]) or not low <= row[1] <= high:
        raise ValueError('Manual coordinate outside the selected envelope')
    # Validate the complete composed surface as well as each individual value.
    from openpilot.starpilot.flm.canonical_surface_adapter import CanonicalSurfaceEffects
    effects = RouteSurfaceEffects if recovered else CanonicalSurfaceEffects
    effects(dict(self.values[:1]), self.digest(), accumulated=dict(self.values))
    return self

  def digest(self):
    payload = (self.context, self.values, self.compatibility) if self.compatibility else (self.context, self.values)
    return hashlib.sha256(json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

  def document(self):
    self.validate()
    return dict(context=list(self.context), values=dict(self.values), **({'compatibility': self.compatibility} if self.compatibility else {}))

  @classmethod
  def from_document(cls, value):
    if (type(value) is not dict or set(value) not in ({'context', 'values'}, {'context', 'values', 'compatibility'}) or type(value['context']) is not list
        or type(value['values']) is not dict):
      raise ValueError('Invalid manual coordinate document')
    from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES
    names = ROUTE_NAMES if value.get('compatibility') else NAMES
    if set(value['values']) != set(names):
      raise ValueError('Complete manual coordinates required')
    return cls(tuple(value['context']), tuple((n, value['values'][n]) for n in names), value.get('compatibility', '')).validate()


class ManualEffects:
  """Human-selected bounded math; never a live trial or a native permission."""

  def __init__(self, profile):
    from openpilot.starpilot.flm.canonical_surface_adapter import CanonicalSurfaceEffects
    if type(profile) is not ManualCoordinates:
      raise ValueError('Typed manual coordinates required')
    profile.validate()
    self.profile = profile
    from openpilot.starpilot.flm.offroad_surface import RouteSurfaceEffects
    effects = RouteSurfaceEffects if profile.compatibility else CanonicalSurfaceEffects
    self.source = effects(dict(profile.values[:1]), profile.digest(), accumulated=dict(profile.values))
    self.original_effect_anchor = self.source.original_effect_anchor

  def compatible(self, cp, ci, controller, params=None):
    if not self.profile.compatibility:
      return True
    from openpilot.starpilot.flm.runtime_adapter_registry import compatibility_for, digest
    try:
      if params is None:
        return False
      value = dict(compatibility_for(cp, ci, controller), settings_sha256=baseline_settings(cp, params))
      return self.profile.compatibility == digest(value)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
      return False

  def begin_frame(self):
    self.source.begin_frame(time.monotonic_ns())

  def selected_stage(self, *args, **kwargs):
    return self.source.selected_stage(*args, **kwargs)

  def selected_friction(self, *args, **kwargs):
    return self.source.selected_friction(*args, **kwargs)

  def selected_deadzone(self, *args, **kwargs):
    return self.source.selected_deadzone(*args, **kwargs)

  def selected_output(self, *args, **kwargs):
    return self.source.selected_output(*args, **kwargs)


def load(cp, controller, params=None):
  """Load once at controller startup. Unreadable is distinct from absent."""
  from openpilot.common.params import Params
  from openpilot.starpilot.saved_source import read_saved
  from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, MAX_DOCUMENT_BYTES, parse_document
  params = Params() if params is None else params
  raw, readable = read_saved(params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
  if not readable:
    return None, 'unreadable'
  if raw is None:
    return None, 'not_configured'
  try:
    profile = parse_document(raw).get(str(cp.carFingerprint))
    manual = None if profile is None else profile.manual_coordinates
    if manual is None or not any(v for _, v in manual.values):
      return None, 'not_configured'
    tune = cp.lateralTuning.torque
    if (profile.basis != (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
        or profile.flm is not None or manual.context != context(cp, controller, recovered=bool(manual.compatibility))):
      return None, 'context_changed'
    return ManualEffects(manual), 'configured'
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError, UnicodeError):
    return None, 'invalid'
