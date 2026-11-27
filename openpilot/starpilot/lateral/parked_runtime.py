"""Saved parked-clearance preferences and fresh radar, model and car inputs, off by default."""

from dataclasses import dataclass
import math

from openpilot.cereal import log
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.scalar_clip import clip_scalar
from openpilot.starpilot.feature_capabilities import lateral_supported
from openpilot.starpilot.lateral.parked_clearance import (
  MAX_GAP, MAX_LIMIT, MAX_SHIFT, MIN_LIMIT, ParkedClearance, RadarPoint, left_room, oncoming_ahead, path_lateral,
)
from openpilot.starpilot.saved_source import read_saved

GAIN = 0.45
MAX_CORRECTION = 0.0012
DEFAULT_GAP = 1.5
DEFAULT_LIMIT = MAX_SHIFT
MAX_RADAR_GAP_S = 0.25
MAX_PATH_SLOPE = 0.1
MAX_AGE_NS = {'carState': 100_000_000, 'modelV2': 200_000_000, 'radarTracks': 200_000_000}


@dataclass(frozen=True)
class ParkedSettings:
  enabled: bool = False
  gap: float = DEFAULT_GAP
  limit: float = DEFAULT_LIMIT


def read_settings(params) -> ParkedSettings:
  try:
    enabled, readable = read_saved(params, 'ParkedClearance', 64)
    gap, gap_readable = read_saved(params, 'ParkedClearanceGap', 64)
    limit, limit_readable = read_saved(params, 'ParkedClearanceShift', 64)
    right_hand_drive, side_readable = read_saved(params, 'IsRhdDetected', 8)
    if (not readable or not gap_readable or not limit_readable or enabled not in (None, b'0', b'1') or
        not side_readable or right_hand_drive not in (None, b'0')):
      return ParkedSettings()
    value = DEFAULT_GAP if gap is None else float(gap)
    shift = DEFAULT_LIMIT if limit is None else float(limit)
    if not math.isfinite(value) or not 0 < value <= MAX_GAP or not math.isfinite(shift) or not MIN_LIMIT <= shift <= MAX_LIMIT:
      return ParkedSettings()
    return ParkedSettings(enabled == b'1', value, shift)
  except (OSError, ValueError):
    return ParkedSettings()


def runtime_supported(CP) -> bool:
  return bool(lateral_supported(CP) and getattr(CP, 'radarUnavailable', True) is False)


def _at(xs, ys, distance: float) -> float:
  value = path_lateral(xs, ys, distance, 0.0)
  if value is None:
    raise ValueError('Path does not cover distance')
  return -value


class ParkedClearanceHost:
  REFRESH_NS = 1_000_000_000
  REQUIRED = tuple(MAX_AGE_NS)

  def __init__(self, params, *, settings_reader=None):
    self.params = params
    self.settings_reader = settings_reader
    self.settings = ParkedSettings()
    self.detector = ParkedClearance()
    self.last_refresh_ns: int | None = None
    self.last_radar_ns = 0
    self.correction = 0.0
    self.shift = 0.0
    self.reason = 'inactive'

  def _stop(self, reason: str) -> float:
    self.detector.reset()
    self.last_radar_ns = 0
    self.correction = 0.0
    self.shift = 0.0
    self.reason = reason
    return 0.0

  def update(self, sm, *, now_ns: int, lateral_active: bool, maneuver: bool) -> float:
    if self.settings_reader is not None:
      self.settings = self.settings_reader()
    elif self.last_refresh_ns is None or not 0 <= now_ns - self.last_refresh_ns < self.REFRESH_NS:
      self.settings = read_settings(self.params)
      self.last_refresh_ns = now_ns
    if not self.settings.enabled:
      return self._stop('disabled')
    for name in self.REQUIRED:
      stamp = int(sm.logMonoTime[name])
      if not sm.valid[name] or not 0 <= now_ns - stamp <= MAX_AGE_NS[name]:
        return self._stop(f'{name}_stale')
    try:
      car, model, radar = sm['carState'], sm['modelV2'], sm['radarTracks']
      if (not lateral_active or maneuver or not car.canValid or car.canTimeout or car.steeringPressed or car.leftBlinker or car.rightBlinker or
          car.leftBlindspot or model.meta.laneChangeState != log.LaneChangeState.off or any(radar.errors.to_dict().values())):
        return self._stop('inactive')
      speed = float(car.vEgo)
      if not math.isfinite(speed) or speed <= 0:
        return self._stop('inactive')
      lookahead = clip_scalar(speed, 8.0, 35.0)
      radar_ns = int(sm.logMonoTime['radarTracks'])
      if radar_ns != self.last_radar_ns:
        dt = (radar_ns - self.last_radar_ns) / 1e9 if self.last_radar_ns else 1.0 / SERVICE_LIST['radarTracks'].frequency
        if not 0 < dt <= MAX_RADAR_GAP_S:
          self.detector.reset()
          dt = 1.0 / SERVICE_LIST['radarTracks'].frequency
        self.last_radar_ns = radar_ns
        xs, ys = list(model.position.x), list(model.position.y)
        points = []
        for point in radar.points:
          lateral = path_lateral(xs, ys, float(point.dRel), float(point.yRel))
          if lateral is not None:
            points.append(RadarPoint(int(point.trackId), float(point.dRel), lateral, float(point.vRel) + speed))
        center = _at(xs, ys, lookahead)
        if abs(center) > MAX_PATH_SLOPE * lookahead:
          return self._stop('curve')
        line, edge = model.laneLines[1], model.roadEdges[0]
        room = left_room(center - _at(list(line.x), list(line.y), lookahead), float(model.laneLineProbs[1]), float(model.laneLineStds[1]),
                         center - _at(list(edge.x), list(edge.y), lookahead), float(model.roadEdgeStds[0]), oncoming=oncoming_ahead(points))
        self.detector.update(points, speed, dt, self.settings.gap, room, True, self.settings.limit)
        self.reason = self.detector.reason
      self.correction = clip_scalar(-2.0 * self.detector.shift / lookahead ** 2 * GAIN, -MAX_CORRECTION * max(1.0, self.settings.limit / MAX_SHIFT), 0.0)
      self.shift = float(self.detector.shift)
      return self.correction
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
      return self._stop('invalid')
