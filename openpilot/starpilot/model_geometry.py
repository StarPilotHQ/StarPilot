"""Validated shared model geometry; no control authority or mutable state."""

import math

ORIGIN_ROUNDOFF_M = 1e-6
STANDSTILL_SPEED_NOISE_MPS = 0.01
STANDSTILL_RELEASE_SPEED_MPS = 0.05
STANDSTILL_MODEL_PREFIX_NOISE_M = 0.01


def normalized_origin(distances: tuple) -> tuple:
  """Only remove sub-micrometre model-origin roundoff; callers validate all geometry."""
  if distances and _finite(distances[0], low=-ORIGIN_ROUNDOFF_M, high=ORIGIN_ROUNDOFF_M) is not None and distances[0] != 0:
    return (0.0, *distances[1:])
  return distances


def standstill_release_speed(standstill: object, v_ego: object, v_ego_raw: object) -> bool:
  speed = _finite(v_ego, low=-STANDSTILL_SPEED_NOISE_MPS, high=STANDSTILL_RELEASE_SPEED_MPS)
  raw = _finite(v_ego_raw, low=0.0, high=STANDSTILL_RELEASE_SPEED_MPS)
  return standstill is True and speed is not None and raw is not None and (speed >= 0.0 or raw == 0.0)


def _finite(value: object, *, low: float, high: float) -> float | None:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return None
  try:
    result = float(value)
  except (OverflowError, ValueError):
    return None
  return result if math.isfinite(result) and low <= result <= high else None


def road_curvature(model: object, speed_mps: float) -> tuple[float, float] | None:
  """Exact frozen max-|orientationRate.z * velocity.x| selection.

  The original helper divides by max(vEgo, 1)^2 and floors time at 1 s.
  Invalid or incomplete current model arrays remain unknown.
  """
  speed = _finite(speed_mps, low=0.0, high=80.0)
  if speed is None:
    return None
  try:
    rates = tuple(model.orientationRate.z)
    times = tuple(model.orientationRate.t)
    velocities = tuple(model.velocity.x)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None
  if not len(rates) == len(times) == len(velocities) == 33:
    return None
  triples = []
  for rate, timestamp, velocity in zip(rates, times, velocities, strict=True):
    r = _finite(rate, low=-10.0, high=10.0)
    t = _finite(timestamp, low=0.0, high=20.0)
    v = _finite(velocity, low=0.0, high=100.0)
    if r is None or t is None or v is None:
      return None
    triples.append((r * v, t))
  acceleration, timestamp = max(triples, key=lambda pair: abs(pair[0]))
  return acceleration / max(speed, 1.0) ** 2, max(timestamp, 1.0)


def standstill_clear_horizon(model) -> float | None:
  """Stop-release endpoint only; this never qualifies full spatial geometry."""
  try:
    distances = normalized_origin(tuple(model.position.x))
    if len(distances) != 33 or distances[0] != 0.0:
      return None
    parsed = tuple(_finite(value, low=-STANDSTILL_MODEL_PREFIX_NOISE_M, high=500.0) for value in distances)
    if any(value is None for value in parsed):
      return None
    finite = tuple(value for value in parsed if value is not None)
    forward_tail = False
    backtrack = 0.0
    for previous, current in zip(finite, finite[1:], strict=False):
      if current < previous:
        if forward_tail or max(abs(previous), abs(current)) > STANDSTILL_MODEL_PREFIX_NOISE_M:
          return None
        backtrack += previous - current
        if backtrack > STANDSTILL_MODEL_PREFIX_NOISE_M:
          return None
      if current > STANDSTILL_MODEL_PREFIX_NOISE_M:
        forward_tail = True
    return finite[-1] if forward_tail else None
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None
