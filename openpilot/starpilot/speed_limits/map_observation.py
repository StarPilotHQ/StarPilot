"""Current-road Maps projection; source ordering/freshness stays in MapTracker."""
import hashlib
import json
import math
import struct
from dataclasses import dataclass

from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import map_source as ms
from openpilot.starpilot.speed_limits.map_source import copy_frame as copy_frame


_LEGACY_KPH_TO_MPS = .277778


def _float32(value: float) -> float:
  return struct.unpack('f', struct.pack('f', value))[0]


_MAX_LEGACY_SPEED = _float32(250 * _LEGACY_KPH_TO_MPS)


def repair_legacy_map_speed(speed_mps: float) -> float:
  # shortcut: match the legacy converter through 250 km/h, revisit if higher posted limits need repair.
  if not 1 <= speed_mps <= _MAX_LEGACY_SPEED:
    return speed_mps
  posted_kph = round(speed_mps / _LEGACY_KPH_TO_MPS)
  if speed_mps == _float32(posted_kph * _LEGACY_KPH_TO_MPS):
    return _float32(posted_kph / 3.6)
  return speed_mps


@dataclass(frozen=True)
class Upcoming:
  observation: acc.Observation
  distance_m: float
  boundary_id: str
  way_id: int


class MapSource:
  """Plannerd's map observation owner; never grants longitudinal authority."""

  def __init__(self):
    self.tracker = ms.MapTracker()
    self.upcoming: Upcoming | None = None
    self.way_id = 0

  def reset(self):
    self.tracker.reset()
    self.upcoming = None
    self.way_id = 0

  def step(self, frame: ms.MapFrame | None, *, received: bool, now_boot_ns: int,
           transport_alive: bool, drive_session: str, car_fingerprint: str) -> acc.Observation:
    self.upcoming = None
    self.way_id = 0
    evidence = self.tracker.step(frame, received=received, now_boot_ns=now_boot_ns,
                                 transport_alive=transport_alive)
    if evidence.observation.kind is acc.ObservationKind.STALE:
      return evidence.observation
    accepted = self.tracker.diagnostic(now_boot_ns=now_boot_ns).frame
    if (evidence.kind not in ('matched_limit_unqualified', 'matched_no_limit_unqualified') or accepted is None or
        accepted.way_selection != 'current' or
        any(type(value) is not str or not 0 < len(value) <= 256 or not value.strip() or
            any(ord(c) < 32 for c in value) for value in (drive_session, car_fingerprint))):
      # Missing speed metadata, coverage/GPS loss and ambiguous/lookahead ways
      # are incomplete evidence, not a new limit or regulatory absence.
      return acc.Observation(acc.ObservationKind.UNKNOWN)
    self.way_id = accepted.way_id
    if evidence.kind == 'matched_no_limit_unqualified':
      return evidence.observation
    identity = hashlib.sha256(json.dumps((drive_session, car_fingerprint, accepted.producer_session,
      accepted.generation, accepted.source, accepted.way_id), separators=(',', ':')).encode()).hexdigest()
    boundary = accepted.preview_boundary_id
    speed = repair_legacy_map_speed(accepted.speed_mps)
    preview_speed = repair_legacy_map_speed(accepted.preview_speed_mps) if type(accepted.preview_speed_mps) is float else 0.
    if (type(boundary) is str and len(boundary) == 64 and all(c in '0123456789abcdef' for c in boundary) and
        math.isfinite(preview_speed) and preview_speed >= 1.0 and abs(preview_speed - speed) > .02 and
        type(accepted.preview_distance_m) is float and math.isfinite(accepted.preview_distance_m) and
        0 < accepted.preview_distance_m <= 500 and type(accepted.preview_way_id) is int and accepted.preview_way_id > 0):
      preview_identity = hashlib.sha256(f'{identity}:{boundary}'.encode()).hexdigest()
      observation = acc.Observation(acc.ObservationKind.VALID, acc.Candidate('map',
        acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value=preview_identity), preview_speed))
      self.upcoming = Upcoming(observation, accepted.preview_distance_m, boundary, accepted.preview_way_id)
    return acc.Observation(acc.ObservationKind.VALID, acc.Candidate('map',
      acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value=identity), speed))
