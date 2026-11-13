"""Disabled, source-aware boundary for v2 mapdOut evidence.

This module checks wire freshness and ordering. It deliberately never creates a
VALID or ABSENT SLC observation: a fresh map match is not road qualification.
"""

import math
from dataclasses import dataclass

from openpilot.cereal import log
from openpilot.starpilot.speed_limits import acceptance as acc


EVENT_MAX_AGE_NS = 250_000_000
COMPUTED_MAX_AGE_NS = 250_000_000
# The Galaxy app's phone fix is sparse; mapd itself holds it for up to 10 s.
GPS_MAX_AGE_NS = {'external': 500_000_000, 'internal': 2_000_000_000, 'car': 2_500_000_000, 'phone': 2_500_000_000}
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_RETIRED_SESSIONS = 16
MAX_UINT64 = (1 << 64) - 1
MAX_INT64 = (1 << 63) - 1
MAX_FLOAT32 = 3.4028234663852886e38
MATCHED = frozenset(('matchedLimit', 'matchedNoLimit'))
LOSSES = frozenset(('noGps', 'noCoverage', 'noMatch'))
WAY_SELECTIONS = frozenset(('current', 'predicted', 'possible', 'extended'))
UNKNOWN = acc.Observation(acc.ObservationKind.UNKNOWN)
STALE = acc.Observation(acc.ObservationKind.STALE)


@dataclass(frozen=True)
class MapFrame:
  event_valid: bool
  event_mono_ns: int
  version: int
  status: str
  source: str
  gps_mono_ns: int
  computed_mono_ns: int
  generation: int
  producer_session: int
  speed_mps: float
  tile_loaded: bool
  way_id: int
  way_selection: str
  preview_speed_mps: float = 0.0
  preview_distance_m: float = 0.0
  preview_boundary_id: str = ''
  preview_way_id: int = 0


@dataclass(frozen=True)
class MapEvidence:
  kind: str
  observation: acc.Observation
  speed_mps: float | None = None  # parsed value only; never SLC authority


@dataclass(frozen=True)
class MapDiagnostic:
  """Metadata from the tracker's accepted frame; never a control observation."""
  frame: MapFrame | None
  producer_restarts: int
  source_switches: int


def copy_frame(road, *, event_valid: bool, event_boot_ns: int) -> MapFrame:
  """Copy scalar wire evidence without retaining a mutable cereal reader."""
  try:
    return MapFrame(event_valid, event_boot_ns, int(road.sampleVersion), str(road.roadStatus),
      str(road.gpsSource), int(road.gpsMonoTime), int(road.computedMonoTime),
      int(road.sourceGeneration), int(road.producerSession), float(road.speedLimit),
      bool(road.tileLoaded), int(road.wayId), str(road.waySelectionType),
      float(getattr(road, 'previewSpeedLimit', 0.0)), float(getattr(road, 'previewDistance', 0.0)),
      str(getattr(road, 'previewBoundaryId', '')), int(getattr(road, 'previewWayId', 0)))
  except Exception as error:  # pycapnp lazy reads may raise KjException, not ValueError
    raise ValueError('invalid mapdOut reader') from error


def decode_event(raw: bytes) -> MapFrame:
  """Copy scalar evidence from an actual serialized host cereal Event."""
  if type(raw) is not bytes or not 0 < len(raw) <= MAX_EVENT_BYTES:
    raise ValueError('invalid mapdOut event size/type')
  try:
    with log.Event.from_bytes(raw) as event:
      if event.which() != 'mapdOut':
        raise ValueError('wrong Event variant')
      return copy_frame(event.mapdOut, event_valid=bool(event.valid), event_boot_ns=int(event.logMonoTime))
  except (ValueError, TypeError) as error:
    raise ValueError('invalid mapdOut event') from error
  except Exception as error:  # pycapnp reports malformed segment/pointer errors as KjException
    raise ValueError('invalid mapdOut event') from error


def _uint(value: object, *, positive: bool = False, maximum: int = MAX_UINT64) -> bool:
  return type(value) is int and (value > 0 if positive else value >= 0) and value <= maximum


def _ordering_fields(frame: object) -> bool:
  return (isinstance(frame, MapFrame) and type(frame.event_valid) is bool and type(frame.status) is str and
          type(frame.source) is str and type(frame.tile_loaded) is bool and type(frame.way_selection) is str and
          _uint(frame.event_mono_ns, positive=True) and _uint(frame.generation, positive=True) and
          _uint(frame.producer_session, positive=True))


def _trusted_wire_shape(frame: MapFrame, *, navigation_only: bool = False) -> bool:
  neutral_navigation = (navigation_only and isinstance(frame, MapFrame) and frame.status == 'unknown' and
                        not frame.event_valid and frame.way_id == 0 and frame.speed_mps == 0)
  if (not _ordering_fields(frame) or not _uint(frame.version, maximum=(1 << 16)-1) or frame.version != 2 or
      (frame.status not in MATCHED | LOSSES and not neutral_navigation) or frame.source not in (*GPS_MAX_AGE_NS, 'none') or
      frame.way_selection not in WAY_SELECTIONS | {'fail'} or not _uint(frame.gps_mono_ns) or
      not _uint(frame.computed_mono_ns, positive=True) or frame.computed_mono_ns > frame.event_mono_ns or
      type(frame.way_id) is not int or not -MAX_INT64-1 <= frame.way_id <= MAX_INT64 or
      type(frame.speed_mps) is not float or not math.isfinite(frame.speed_mps) or
      abs(frame.speed_mps) > MAX_FLOAT32):
    return False
  if frame.status == 'noGps':
    return ((frame.source == 'none' and frame.gps_mono_ns == 0) or
            (frame.source in GPS_MAX_AGE_NS and 0 < frame.gps_mono_ns <= frame.computed_mono_ns))
  return frame.source in GPS_MAX_AGE_NS and 0 < frame.gps_mono_ns <= frame.computed_mono_ns


def _current_at(frame: MapFrame, now_boot_ns: int) -> bool:
  if not _trusted_wire_shape(frame):
    return False
  if not (0 < frame.computed_mono_ns <= frame.event_mono_ns <= now_boot_ns and
          now_boot_ns - frame.event_mono_ns <= EVENT_MAX_AGE_NS and
          now_boot_ns - frame.computed_mono_ns <= COMPUTED_MAX_AGE_NS):
    return False
  if frame.status in MATCHED or frame.status in ('noCoverage', 'noMatch'):
    if (frame.source not in GPS_MAX_AGE_NS or not 0 < frame.gps_mono_ns <= frame.computed_mono_ns or
        now_boot_ns - frame.gps_mono_ns > GPS_MAX_AGE_NS[frame.source]):
      return False
  return True


def _classify(frame: MapFrame, now_boot_ns: int) -> MapEvidence:
  if not _current_at(frame, now_boot_ns):
    # A structurally malformed fresh message is UNKNOWN; expired evidence is
    # STALE. Neither has a numeric SLC candidate.
    timestamps = (frame.gps_mono_ns, frame.computed_mono_ns, frame.event_mono_ns)
    expired = (all(_uint(value) for value in timestamps) and frame.event_mono_ns <= now_boot_ns and
               (now_boot_ns - frame.event_mono_ns > EVENT_MAX_AGE_NS or
                now_boot_ns - frame.computed_mono_ns > COMPUTED_MAX_AGE_NS or
                (frame.source in GPS_MAX_AGE_NS and frame.gps_mono_ns > 0 and
                 now_boot_ns - frame.gps_mono_ns > GPS_MAX_AGE_NS[frame.source])))
    return MapEvidence('stale' if expired else 'unknown', STALE if expired else UNKNOWN)
  if frame.status == 'matchedLimit':
    if (not frame.event_valid or not frame.tile_loaded or frame.way_id <= 0 or
        frame.way_selection not in WAY_SELECTIONS or frame.speed_mps <= 0):
      return MapEvidence('unknown', UNKNOWN)
    return MapEvidence('matched_limit_unqualified', UNKNOWN, float(frame.speed_mps))
  if frame.status == 'matchedNoLimit':
    if (not frame.event_valid or not frame.tile_loaded or frame.way_id <= 0 or
        frame.way_selection not in WAY_SELECTIONS or frame.speed_mps != 0):
      return MapEvidence('unknown', UNKNOWN)
    return MapEvidence('matched_no_limit_unqualified', UNKNOWN)
  if frame.event_valid or frame.speed_mps != 0 or frame.way_id != 0 or frame.tile_loaded:
    return MapEvidence('unknown', UNKNOWN)
  if frame.status == 'noGps' and frame.source == 'none' and frame.gps_mono_ns != 0:
    return MapEvidence('unknown', UNKNOWN)
  return MapEvidence('loss', UNKNOWN)


class MapTracker:
  """Bounded in-memory ordering state; outputs never authorize map control."""

  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self._admitted_frame: MapFrame | None = None
    self._session: int | None = None
    self._generation = 0
    self._source = 'none'
    self._last_event_ns = 0
    self._last_now_ns = 0
    self._retired: set[int] = set()
    self._blocked = False
    self._current: MapFrame | None = None
    self._producer_restarts = 0
    self._source_switches = 0

  @property
  def admitted_frame(self) -> MapFrame | None:
    """Frame admitted by the last step's ordering checks; consumers must still validate its content and age."""
    return self._admitted_frame

  def diagnostic(self, *, now_boot_ns: int) -> MapDiagnostic:
    # The frame is the same immutable object that passed step() ordering and
    # freshness checks. Recheck age so even a caller between step() calls
    # cannot retain a stopped producer's old diagnostic frame.
    if not _uint(now_boot_ns, positive=True) or now_boot_ns < self._last_now_ns:
      raise ValueError('invalid map diagnostic clock')
    frame = self._current
    if frame is not None and _classify(frame, now_boot_ns).kind not in (
        'matched_limit_unqualified', 'matched_no_limit_unqualified', 'loss'):
      frame = None
    return MapDiagnostic(frame, self._producer_restarts, self._source_switches)

  def _held(self, now_boot_ns: int) -> MapEvidence:
    if self._current is None:
      return MapEvidence('unknown', UNKNOWN)
    result = _classify(self._current, now_boot_ns)
    if result.kind == 'stale':
      self._current = None
    return result

  def step(self, frame: MapFrame | None, *, received: bool, now_boot_ns: int,
           transport_alive: bool = True, navigation_only: bool = False) -> MapEvidence:
    self._admitted_frame = None
    if (not _uint(now_boot_ns, positive=True) or type(received) is not bool or type(transport_alive) is not bool or
        type(navigation_only) is not bool):
      raise ValueError('invalid map source clock/transport flags')
    if now_boot_ns < self._last_now_ns:
      self._blocked = True  # new drive/replay epoch requires explicit reset()
    self._last_now_ns = now_boot_ns
    if self._blocked:
      self._current = None
      return MapEvidence('unknown', UNKNOWN)
    if not transport_alive:
      self._current = None
      return MapEvidence('stale', STALE)
    if not received:
      return self._held(now_boot_ns)
    if not _ordering_fields(frame):
      self._current = None
      return MapEvidence('unknown', UNKNOWN)
    assert frame is not None
    if frame.producer_session in self._retired:
      return self._held(now_boot_ns)
    if self._session is not None:
      if frame.event_mono_ns <= self._last_event_ns:
        return self._held(now_boot_ns)  # old/duplicate packet cannot revoke or renew
    if not _trusted_wire_shape(frame, navigation_only=navigation_only):
      self._current = None  # unsupported/malformed wire cannot move trusted ordering
      return MapEvidence('unknown', UNKNOWN)
    if frame.event_mono_ns > now_boot_ns:
      self._current = None  # future packet is invalid, but cannot poison the high-water mark
      return MapEvidence('unknown', UNKNOWN)
    if self._session is not None:
      if frame.producer_session == self._session:
        if frame.generation < self._generation or (frame.generation == self._generation and frame.source != self._source):
          self._current = None
          self._blocked = True
          return MapEvidence('unknown', UNKNOWN)
      else:
        if len(self._retired) >= MAX_RETIRED_SESSIONS:
          self._current = None
          self._blocked = True
          return MapEvidence('unknown', UNKNOWN)
        self._retired.add(self._session)
        self._producer_restarts += 1
        self._current = None
    if frame.producer_session != self._session or frame.generation != self._generation:
      self._current = None
    if self._session == frame.producer_session and frame.generation != self._generation:
      self._source_switches += 1
    self._session = frame.producer_session
    self._generation = frame.generation
    self._source = frame.source
    self._last_event_ns = frame.event_mono_ns
    self._admitted_frame = frame
    result = _classify(frame, now_boot_ns)
    self._current = frame if result.kind in ('matched_limit_unqualified', 'matched_no_limit_unqualified', 'loss') else None
    return result
