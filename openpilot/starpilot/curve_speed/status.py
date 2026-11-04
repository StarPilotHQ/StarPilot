"""Independent curve diagnostics in the existing single-owner planner event."""

from dataclasses import dataclass, replace
import math
import secrets

from openpilot.cereal import messaging
from openpilot.starpilot.curve_speed.learning import finite_number

STATUS_LIFETIME_NS = 100_000_000
CURVATURE_DISPLAY_TAU_S = 0.6
CURVATURE_DISPLAY_HOLD_NS = 1_000_000_000


@dataclass(frozen=True)
class CurveObservation:
  configured: bool
  candidate_mps: float | None
  ceiling_mps: float | None
  applied: bool
  controlling: bool
  training: bool
  progress: float
  comfort_accel: float
  binding_distance: float
  reason: str
  planner_status: str
  persistence_status: str
  glow: bool
  curve_only: bool
  road_curvature: float | None = None


class StatusPublisher:
  def __init__(self):
    self.session_id = secrets.token_hex(16)
    self.sequence = 0

  def attach(self, event, host, result, planner, *, now_ns: int, model_ns: int, persistence_status: str,
             road_curvature: float | None = None):
    curve_only = event is None
    if event is None:
      event = messaging.new_message('slcState')
      event.valid = True
      event.logMonoTime = now_ns
    runtime = host.runtime
    self.sequence += 1
    # Zero storage values have meaning only behind their explicit presence bit.
    event.slcState.curve = {
      'version': 1, 'sessionId': self.session_id, 'sequence': self.sequence,
      'observedMonoTime': now_ns, 'validUntilMonoTime': now_ns + STATUS_LIFETIME_NS,
      'modelMonoTime': int(model_ns), 'configured': bool(runtime.enabled),
      'documentValid': bool(runtime.document_valid),
      'hasCandidate': result.candidate_mps is not None, 'candidateMps': float(result.candidate_mps or 0.0),
      'hasCeiling': result.ceiling_mps is not None, 'ceilingMps': float(result.ceiling_mps or 0.0),
      'applied': bool(planner.last_curve_ceiling_applied),
      'controlling': bool(runtime.was_controlling and planner.last_curve_ceiling_applied),
      'training': bool(result.training), 'calibrationProgress': float(result.progress),
      'comfortAccel': float(runtime.curve.average_comfort), 'bindingDistance': float(result.binding_distance_m),
      'reason': result.reason if result.ceiling_mps is not None else result.learning_reason,
      'plannerStatus': planner.last_curve_ceiling_status,
      'persistenceStatus': persistence_status, 'glow': bool(runtime.glow), 'curveOnly': curve_only,
      'hasRoadCurvature': road_curvature is not None, 'roadCurvature': float(road_curvature or 0.0),
    }
    return event


def observation(state, now_ns: int) -> CurveObservation | None:
  """Outer SLC availability never substitutes for the curve's own contract."""
  try:
    value = state.curve
    stamp, expiry, model = int(value.observedMonoTime), int(value.validUntilMonoTime), int(value.modelMonoTime)
    if (value.version != 1 or not value.sessionId or len(value.sessionId) > 64 or value.sequence <= 0 or
        type(now_ns) is not int or not 0 < stamp <= now_ns <= expiry or
        not 0 < expiry - stamp <= STATUS_LIFETIME_NS or not 0 < model <= stamp or stamp - model > 150_000_000):
      return None
    candidate = float(value.candidateMps) if value.hasCandidate else None
    ceiling = float(value.ceilingMps) if value.hasCeiling else None
    progress, comfort, distance = float(value.calibrationProgress), float(value.comfortAccel), float(value.bindingDistance)
    curvature = float(value.roadCurvature) if value.hasRoadCurvature else None
    if (any(not finite_number(speed) or not 0 < speed <= 100 for speed in (candidate, ceiling) if speed is not None) or
        not finite_number(progress) or not 0 <= progress <= 100 or not finite_number(comfort) or not 1.19 <= comfort <= 3.21 or
        not finite_number(distance) or distance < 0 or curvature is not None and not finite_number(curvature)):
      return None
    if (value.applied and (not value.configured or not value.documentValid or ceiling is None or
                          str(value.plannerStatus) != 'selected') or
        value.controlling and not value.applied or value.training and value.applied):
      return None
    if any(len(str(text)) > 80 for text in (value.reason, value.plannerStatus, value.persistenceStatus)):
      return None
    return CurveObservation(bool(value.configured), candidate, ceiling, bool(value.applied), bool(value.controlling),
                            bool(value.training), progress, comfort, distance, str(value.reason), str(value.plannerStatus),
                            str(value.persistenceStatus), bool(value.glow), bool(value.curveOnly), curvature)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None


class CurvatureDisplay:
  """Smooth published curvature for the glow, label, and gauge colors.

  roadCurvature is a raw per-model-frame max over the horizon and drops out
  whenever there is no candidate; the green-to-red ramp turned that jitter
  into a strobing border. Short dropouts hold the last value.
  """

  def __init__(self):
    self._value: float | None = None
    self._stamp_ns = 0

  def update(self, curve: CurveObservation | None, now_ns: int) -> CurveObservation | None:
    if curve is None:
      return None
    raw, gap = curve.road_curvature, now_ns - self._stamp_ns
    if self._value is None or not 0 <= gap <= CURVATURE_DISPLAY_HOLD_NS:
      self._value = raw
      self._stamp_ns = now_ns
      return curve
    if raw is None:
      return replace(curve, road_curvature=self._value)
    self._value += (raw - self._value) * (1.0 - math.exp(-gap / 1e9 / CURVATURE_DISPLAY_TAU_S))
    self._stamp_ns = now_ns
    return replace(curve, road_curvature=self._value)
