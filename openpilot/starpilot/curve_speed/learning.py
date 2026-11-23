"""Pure learned comfort curve; the runtime owns permission to learn and persist."""

from bisect import bisect_left
from dataclasses import dataclass
import math
from typing import cast, TypeGuard


BUCKET_COUNT = 24
MIN_CURVATURE = 0.0005
MAX_CURVATURE = 0.02
GRID = tuple(MIN_CURVATURE * (MAX_CURVATURE / MIN_CURVATURE) ** (i / (BUCKET_COUNT - 1)) for i in range(BUCKET_COUNT))
KEYS = tuple(str(round(k, 6)) for k in GRID)
CURVATURES = tuple(float(key) for key in KEYS)
LOG_GRID = tuple(math.log(k) for k in GRID)
PRIOR_X = (0.001, 0.003, 0.01, 0.03, 0.1)
PRIOR_Y = (1.5, 1.8, 2.2, 2.6, 2.9)
MIN_COMFORT = 1.2
MAX_COMFORT = 3.2
HISTORY_WEIGHT = 600
PRIOR_WEIGHT = 100
CALIBRATION_SAMPLES = 200
NUDGE_WEIGHT = 20
MAX_COUNT = 2**53 - 1
MAX_SAVED_BUCKETS = 256


def finite_number(value: object) -> TypeGuard[int | float]:
  if type(value) not in (int, float):
    return False
  try:
    return math.isfinite(cast(int | float, value))
  except OverflowError:
    return False


def interpolate(x: float, xs: tuple[float, ...], ys: tuple[float, ...]) -> float:
  index = bisect_left(xs, x)
  if index == 0:
    return ys[0]
  if index == len(xs):
    return ys[-1]
  fraction = (x - xs[index - 1]) / (xs[index] - xs[index - 1])
  return ys[index - 1] * (1 - fraction) + ys[index] * fraction


def bucket_index(curvature: float) -> int:
  if not finite_number(curvature):
    raise ValueError("curvature must be finite")
  log_k = math.log(min(max(abs(curvature), MIN_CURVATURE), MAX_CURVATURE))
  return min(range(BUCKET_COUNT), key=lambda i: abs(LOG_GRID[i] - log_k))


def monotone_fit(values: list[float], weights: list[int]) -> tuple[float, ...]:
  """Weighted pool-adjacent-violators fit, non-decreasing in list order."""
  if len(values) != len(weights) or any(not finite_number(v) for v in values) or any(type(w) is not int or w <= 0 for w in weights):
    raise ValueError("fit requires finite values and positive integer weights")
  blocks: list[tuple[float, int, int]] = []
  for value, weight in zip(values, weights, strict=True):
    blocks.append((value, weight, 1))
    while len(blocks) >= 2 and blocks[-2][0] > blocks[-1][0]:
      right, left = blocks.pop(), blocks.pop()
      total = left[1] + right[1]
      mean = left[0] * (left[1] / total) + right[0] * (right[1] / total)
      blocks.append((mean, total, left[2] + right[2]))
  return tuple(mean for mean, _weight, size in blocks for _ in range(size))


@dataclass(frozen=True)
class Bucket:
  average: float
  count: int


@dataclass(frozen=True)
class LoadResult:
  curve: 'LearnedCurve'
  valid: bool
  reason: str
  migrated: bool = False


class LearnedCurve:
  """No Params or vehicle access. A successful write acknowledges a revision."""

  def __init__(self):
    self._buckets: dict[int, Bucket] = {}
    self.revision = 0
    self._saved_revision = 0
    self._fit = tuple(interpolate(k, PRIOR_X, PRIOR_Y) for k in CURVATURES)

  @classmethod
  def load(cls, document: object) -> LoadResult:
    curve = cls()
    if document is None:
      return LoadResult(curve, True, "empty")
    if type(document) is not dict:
      return LoadResult(curve, False, "invalid_document")
    data = cast(dict[object, object], document)
    legacy = "version" not in data
    if not legacy:
      if set(data) != {"version", "buckets"} or type(data["version"]) is not int or data["version"] != 1:
        return LoadResult(curve, False, "unsupported_document")
      document = data["buckets"]
    if type(document) is not dict or len(document) > MAX_SAVED_BUCKETS:
      return LoadResult(curve, False, "invalid_buckets")
    merged: dict[int, Bucket] = {}
    try:
      for key, value in cast(dict[object, object], document).items():
        if not isinstance(key, str) or len(key) > 64 or type(value) is not dict or set(value) != {"average", "count"}:
          raise ValueError("invalid bucket")
        curvature = float(key)
        sample = cast(dict[str, object], value)
        average, count = sample["average"], sample["count"]
        if not finite_number(average) or average < 0 or type(count) is not int or not 0 < count <= MAX_COUNT:
          raise ValueError("invalid sample")
        index = bucket_index(curvature)
        old = merged.get(index, Bucket(0.0, 0))
        total = old.count + count
        if total > MAX_COUNT:
          raise ValueError("sample count overflow")
        merged[index] = Bucket(old.average * (old.count / total) + average * (count / total), total)
    except (ValueError, TypeError, OverflowError):
      # Never partially normalize a corrupt document and later overwrite it.
      return LoadResult(curve, False, "invalid_sample")
    curve._buckets = merged
    curve._rebuild()
    return LoadResult(curve, True, "legacy" if legacy else "loaded", migrated=legacy)

  @property
  def dirty(self) -> bool:
    return self.revision != self._saved_revision

  def acknowledge_saved(self, revision: int) -> None:
    if type(revision) is not int or not self._saved_revision <= revision <= self.revision:
      raise ValueError("invalid saved revision")
    self._saved_revision = revision

  def document(self) -> dict:
    return {"version": 1, "buckets": {KEYS[i]: {"average": item.average, "count": item.count}
                                      for i, item in sorted(self._buckets.items())}}

  @property
  def progress(self) -> float:
    return 100 * sum(min(item.count / CALIBRATION_SAMPLES, 1.0) for item in self._buckets.values()) / BUCKET_COUNT

  @property
  def average_comfort(self) -> float:
    count = sum(item.count for item in self._buckets.values())
    return sum(self._fit[i] * (item.count / count) for i, item in self._buckets.items()) if count else 2.0

  def comfort(self, curvature: float) -> float:
    if not finite_number(curvature):
      raise ValueError("curvature must be finite")
    return interpolate(abs(curvature), CURVATURES, self._fit)

  def observe(self, curvature: float, lateral_acceleration: float) -> None:
    self._record(curvature, lateral_acceleration, 1)

  def nudge(self, curvature: float, lateral_acceleration: float) -> None:
    if not finite_number(lateral_acceleration):
      raise ValueError("sample must be finite")
    self._record(curvature, min(max(lateral_acceleration, MIN_COMFORT), MAX_COMFORT), NUDGE_WEIGHT)

  def _record(self, curvature: float, sample: float, weight: int) -> None:
    index = bucket_index(curvature)
    if not finite_number(sample) or sample < 0:
      raise ValueError("sample must be finite and nonnegative")
    old = self._buckets.get(index, Bucket(sample, 0))
    history = min(old.count, HISTORY_WEIGHT)
    total = history + weight
    self._buckets[index] = Bucket(old.average * (history / total) + sample * (weight / total), min(old.count + weight, MAX_COUNT))
    self.revision += 1
    self._rebuild()

  def _rebuild(self) -> None:
    values, weights = [], []
    for i, curvature in enumerate(CURVATURES):
      prior = interpolate(curvature, PRIOR_X, PRIOR_Y)
      sample = self._buckets.get(i, Bucket(prior, 0))
      confidence = sample.count / (sample.count + PRIOR_WEIGHT)
      values.append(min(max(confidence * sample.average + (1 - confidence) * prior, MIN_COMFORT), MAX_COMFORT))
      weights.append(sample.count + PRIOR_WEIGHT)
    # Constrain apex speed, not comfort: a sharper curve is never taken faster, but a
    # driver may corner harder on gentle highway curves than on tight street ones.
    speeds = monotone_fit([math.sqrt(value / k) for value, k in zip(values[::-1], CURVATURES[::-1], strict=True)], weights[::-1])[::-1]
    self._fit = tuple(min(max(speed * speed * k, MIN_COMFORT), MAX_COMFORT) for speed, k in zip(speeds, CURVATURES, strict=True))
