"""Canonical exact-GM geometry projection and explicit learner intent."""
from dataclasses import dataclass
import time

from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, MAX_DOCUMENT_BYTES, GeometryProfile, parse_document
from openpilot.starpilot.saved_source import read_saved


@dataclass(frozen=True)
class GeometrySelection:
  profile: GeometryProfile | None = None
  force_off: bool = False

  @property
  def force_auto(self):
    return self.profile is not None and self.profile.learning == 'force_auto' and not self.force_off


def geometry_basis(CP):
  return float(CP.steerRatio), float(CP.steerActuatorDelay) + .2


def read_geometry(params, CP):
  from openpilot.starpilot.lateral.torque_runtime import gm_manual_supported_cp
  if not gm_manual_supported_cp(CP):
    return GeometrySelection()
  try:
    raw, readable = read_saved(params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
    if not readable or raw is None:
      return GeometrySelection()
    profile = parse_document(raw).get(str(CP.carFingerprint))
    tune = CP.lateralTuning.torque
    if (profile is None or profile.geometry is None or
        profile.basis != (tune.latAccelFactor, tune.latAccelOffset, tune.friction) or
        profile.geometry.basis != geometry_basis(CP)):
      return GeometrySelection()
    from openpilot.starpilot.lateral.learning_preferences import read_preference
    preference = read_preference(params, CP)
    if not preference.valid:
      return GeometrySelection()
    return GeometrySelection(profile.geometry, not preference.enabled or profile.geometry.learning == 'force_off')
  except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
    return GeometrySelection()


class GeometryPublicationOwner:
  """Project a copy only; estimator messages/state/cache remain upstream-owned."""
  def __init__(self, params, CP):
    self.params, self.CP = params, CP
    self.last_read_ns = None
    self.selected = GeometrySelection()
    self.delay_read_ns = None
    self.delay_selected = None

  def selection(self):
    now = time.monotonic_ns()
    if self.last_read_ns is None or not 0 <= now - self.last_read_ns < 1_000_000_000:
      self.selected = read_geometry(self.params, self.CP)
      self.last_read_ns = now
    return self.selected

  def parameters(self, original):
    selection = self.selection()
    profile = selection.profile
    if profile is None:
      return original
    ratio = profile.ratio.custom_value if profile.ratio.mode == 'custom' and not selection.force_auto else None
    if selection.force_off and ratio is None:
      ratio = profile.basis[0]
    if ratio is None and not selection.force_off:
      return original
    message = original.as_reader().as_builder()
    if ratio is not None:
      message.vehicleParameters.steerRatio = ratio
    if selection.force_off:
      message.vehicleParameters.stiffnessFactor = 1.
    return message

  def delay(self, original):
    from openpilot.starpilot.lateral.delay_preferences import read_delay
    now = time.monotonic_ns()
    if self.delay_read_ns is None or not 0 <= now - self.delay_read_ns < 1_000_000_000:
      self.delay_selected = read_delay(self.params, self.CP)
      self.delay_read_ns = now
    if self.delay_selected.value is not None:
      message = original.as_reader().as_builder()
      message.lateralDelay.lateralDelay = self.delay_selected.value
      return message
    return original
