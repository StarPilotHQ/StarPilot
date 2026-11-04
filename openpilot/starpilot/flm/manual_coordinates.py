"""Explicit parked manual coordinates, separate from learned history and authority."""

from dataclasses import dataclass
import hashlib
import json
import math
import time

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, MANIFEST_SHA256, coordinate_bounds


def context(cp, controller):
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  from openpilot.starpilot.lateral.controller_selection import ControllerMode
  if (str(cp.steerControlType) != 'torque' or cp.lateralTuning.which() != 'torque'
      or cp.passive or cp.notCar or cp.dashcamOnly):
    raise ValueError('Manual coordinates require an active torque controller')
  return car_params_digest(cp), str(ControllerMode(controller)), MANIFEST_SHA256


@dataclass(frozen=True)
class ManualCoordinates:
  context: tuple[str, str, str]
  values: tuple[tuple[str, float], ...]

  def validate(self):
    if (type(self.context) is not tuple or len(self.context) != 3
        or any(type(v) is not str for v in self.context)
        or any(len(self.context[i]) != 64 or self.context[i].strip('0123456789abcdef') for i in (0, 2))
        or not self.context[1] or len(self.context[1]) > 32 or not self.context[1].isascii()
        or type(self.values) is not tuple or len(self.values) != len(NAMES)):
      raise ValueError('Invalid manual coordinate context')
    from openpilot.starpilot.lateral.controller_selection import ControllerMode
    ControllerMode(self.context[1])
    for name, row in zip(NAMES, self.values, strict=True):
      if type(row) is not tuple or len(row) != 2 or row[0] != name:
        raise ValueError('Exactly thirty ordered manual coordinates required')
      low, high = coordinate_bounds(name)
      if type(row[1]) not in (int, float) or not math.isfinite(row[1]) or not low <= row[1] <= high:
        raise ValueError('Manual coordinate outside the selected envelope')
    # Validate the complete composed surface as well as each individual value.
    from openpilot.starpilot.flm.canonical_surface_adapter import CanonicalSurfaceEffects
    CanonicalSurfaceEffects(dict(self.values[:1]), self.digest(), accumulated=dict(self.values))
    return self

  def digest(self):
    return hashlib.sha256(json.dumps((self.context, self.values), separators=(',', ':'), allow_nan=False).encode()).hexdigest()

  def document(self):
    self.validate()
    return {'context': list(self.context), 'values': dict(self.values)}

  @classmethod
  def from_document(cls, value):
    if (type(value) is not dict or set(value) != {'context', 'values'} or type(value['context']) is not list
        or type(value['values']) is not dict or set(value['values']) != set(NAMES)):
      raise ValueError('Invalid manual coordinate document')
    return cls(tuple(value['context']), tuple((n, value['values'][n]) for n in NAMES)).validate()


class ManualEffects:
  """Human-selected bounded math; never a live trial or a native permission."""

  def __init__(self, profile):
    from openpilot.starpilot.flm.canonical_surface_adapter import CanonicalSurfaceEffects
    if type(profile) is not ManualCoordinates:
      raise ValueError('Typed manual coordinates required')
    profile.validate()
    self.profile = profile
    self.source = CanonicalSurfaceEffects(dict(profile.values[:1]), profile.digest(), accumulated=dict(profile.values))
    self.original_effect_anchor = self.source.original_effect_anchor

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
        or profile.flm is not None or manual.context != context(cp, controller)):
      return None, 'context_changed'
    return ManualEffects(manual), 'configured'
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError, UnicodeError):
    return None, 'invalid'
