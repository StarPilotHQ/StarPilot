"""Adapt forwarded phone guidance to the existing navigation instruction schema."""
import functools
import math
import re
from collections import deque
from typing import NamedTuple

ROAD_WORDS = {'n': 'north', 's': 'south', 'e': 'east', 'w': 'west', 'st': 'street', 'rd': 'road', 'blvd': 'boulevard',
              'ave': 'avenue', 'av': 'avenue', 'ln': 'lane', 'dr': 'drive', 'hwy': 'highway', 'pkwy': 'parkway', 'ct': 'court',
              'pl': 'place', 'cir': 'circle', 'ter': 'terrace', 'trl': 'trail', 'fwy': 'freeway', 'expy': 'expressway', 'sq': 'square',
              'jr': 'junior', 'sr': 'senior', 'mt': 'mount', 'ft': 'fort'}
CARDINALS = frozenset(('north', 'south', 'east', 'west'))
CONFIRMED = ('mapAligned', 'mapInferred')
VERIFIABLE = ('', 'turn', 'continue', 'off ramp', 'on ramp', 'fork')
JUNCTION_SPAN_M = 40.           # same-side branches this close together are one junction (divided roads)
HOLD_NS = 20_000_000_000        # a confirmed junction outlives mapd losing the road just before it
HOLD_PASSED_M = 50.
HOLD_KEEPS = ('mapUnavailable', 'mapNoNearbyJunction', 'mapUnverifiable')   # statuses that do not contradict a hold
ODOMETER_GAP_NS = 3_000_000_000


@functools.lru_cache(maxsize=256)
def road_key(name):
  """'S Valley View Blvd' and 'South Valley View Boulevard' are the same road; 'E St' and 'W St' are not."""
  tokens = [ROAD_WORDS.get(token, token) for token in re.findall(r'[a-z0-9]+', name.lower())]
  core = tuple(token for token in tokens if token not in CARDINALS)
  return (core, frozenset(tokens) & CARDINALS) if core else (tuple(tokens), frozenset())


def same_road(name, roads):
  core, cardinals = road_key(name)
  return bool(core) and any(core == other and (not cardinals or not others or cardinals == others) for other, others in roads)


@functools.lru_cache(maxsize=64)
def instruction_roads(text):
  text = re.sub(r'^(?:at|onto|towards?)\s+', '', text.strip(), flags=re.IGNORECASE)
  return frozenset(key for part in re.split(r'\s+/\s+|\s+towards?\s+', text) if (key := road_key(part))[0])


class Resolution(NamedTuple):
  status: str
  kind: str = ''
  side: str = ''
  distance: float = 0.            # metres to the matched junction at anchor_ns
  anchor_ns: int = 0              # CLOCK_MONOTONIC time of the GPS fix that measured it
  reason: str = ''                # diagnostic detail; never grants control

  @property
  def control(self):
    return self.status in CONFIRMED


def notification_instruction(phone, *, now_ns):
  """Use available notification fields without requiring a route or vehicle fix."""
  from openpilot.starpilot.navigation.phone import LEASE_NS, OBSERVATION_NS
  if phone is None:
    return None, 'noNotification'
  if not phone['active']:
    return None, 'inactiveNotification'
  if not 0 < phone['observed'] <= now_ns <= phone['observed'] + OBSERVATION_NS:
    return None, 'staleObservation'
  if not 0 < phone['received'] <= now_ns <= phone['received'] + LEASE_NS:
    return None, 'expiredLease'
  return {'text': phone['instruction'], 'maneuverType': phone.get('maneuverType', ''),
          'maneuverModifier': phone.get('maneuverModifier', ''),
          'distanceMeters': phone.get('distanceMeters') or 0,
          'remainingDistanceMeters': phone.get('remainingMeters') or 0,
          'remainingDurationSeconds': phone.get('remainingSeconds') or 0}, 'notificationGuidance'


MATCHED = ('matched_limit_unqualified', 'matched_no_limit_unqualified')


def navigation_frame(frame, now_boot_ns):
  """mapd's neutral ambiguous output, which may carry navigation-only junctions."""
  from openpilot.starpilot.speed_limits.map_source import COMPUTED_MAX_AGE_NS, EVENT_MAX_AGE_NS, GPS_MAX_AGE_NS
  return (frame.version == 2 and frame.status == 'unknown' and not frame.event_valid and frame.way_id == 0 and
          frame.speed_mps == 0 and frame.source in GPS_MAX_AGE_NS and
          0 < frame.gps_mono_ns <= frame.computed_mono_ns <= frame.event_mono_ns <= now_boot_ns and
          now_boot_ns - frame.event_mono_ns <= EVENT_MAX_AGE_NS and
          now_boot_ns - frame.computed_mono_ns <= COMPUTED_MAX_AGE_NS and
          now_boot_ns - frame.gps_mono_ns <= GPS_MAX_AGE_NS[frame.source])


class LocalMapCorroborator:
  """Optional geometry evidence from mapd's matched road; never route authority."""
  def __init__(self):
    from openpilot.starpilot.speed_limits.map_source import MapTracker
    self.tracker = MapTracker()
    self.frame = None
    self.branches = ()
    self.navigation_only = False
    self.last_event_ns = 0
    self.failure_logged = False
    self.updated_boot_ns = 0
    self.fresh = False
    self.held = None
    self.drive = 0
    self.speed = 0.
    self.speed_ns = 0
    self.odometer = deque(maxlen=128)   # (monotonic ns, metres) at each resolve
    self.map_reason = 'noMapFrame'

  def reset(self):
    self.tracker.reset()
    self.odometer.clear()
    self.frame, self.branches, self.last_event_ns, self.held = None, (), 0, None
    self.map_reason = 'noMapFrame'

  def update(self, road, *, valid, event_boot_ns, received, alive, now_boot_ns):
    from openpilot.starpilot.speed_limits.map_source import copy_frame
    self.updated_boot_ns, self.fresh = now_boot_ns, False
    try:
      frame = copy_frame(road, event_valid=valid, event_boot_ns=event_boot_ns) if received else None
      navigation_only = frame is not None and navigation_frame(frame, now_boot_ns)
      evidence = self.tracker.step(frame, received=received, now_boot_ns=now_boot_ns, transport_alive=alive,
                                   navigation_only=navigation_only)
      accepted = self.tracker.diagnostic(now_boot_ns=now_boot_ns).frame
      navigation_admitted = self.tracker.admitted_frame is frame and navigation_only
      if not alive:
        self.frame, self.branches = None, ()
        self.map_reason = 'mapTransportDead'
      elif frame is not None and frame.event_mono_ns > self.last_event_ns:
        self.last_event_ns = frame.event_mono_ns
        self.fresh = accepted is frame or navigation_admitted
        self.frame, self.branches = None, ()
        self.map_reason = 'mapFrameRejected:' + evidence.kind
        # A matched road (speed-limit grade), or mapd's navigation-only pick on an ambiguous fix.
        if accepted is frame and evidence.kind in MATCHED:
          self._take(road, frame, navigation_only=False)
        elif accepted is None and navigation_admitted:
          self._take(road, frame, navigation_only=True)
      if self.frame is not None and not (navigation_frame(self.frame, now_boot_ns) if self.navigation_only else
                                         accepted is self.frame):
        self.frame, self.branches = None, ()
        self.map_reason = 'mapFrameExpiredOrReplaced'
    except Exception:
      # Cereal's lazy readers can raise KjException. Bad optional map evidence
      # must neither retain old confirmation nor interrupt phone/Mapbox output.
      if not self.failure_logged:
        from openpilot.common.swaglog import cloudlog
        cloudlog.exception('notification navigation: mapdOut unreadable; map corroboration off')
        self.failure_logged = True
      self.tracker.reset()
      self.frame, self.branches, self.last_event_ns, self.held = None, (), 0, None
      self.map_reason = 'mapFrameUnreadable'

  def _take(self, road, frame, *, navigation_only):
    rows = road.navigationBranches
    if len(rows) > 32:
      self.map_reason = 'tooManyBranches'
      return
    branches = []
    for row in rows:
      junction = float(row.junctionLatitude), float(row.junctionLongitude)
      distance, angle, way_id = float(row.distanceMeters), float(row.angleDegrees), int(row.wayId)
      if (not all(math.isfinite(v) for v in (*junction, distance, angle)) or
          not -90 <= junction[0] <= 90 or not -180 <= junction[1] <= 180 or
          not 0 <= distance <= 1500 or not -180 <= angle <= 180 or way_id <= 0):
        self.map_reason = 'invalidBranchGeometry'
        return
      branches.append((junction, distance, angle, str(row.highwayClass), way_id, str(row.roadName)))
    self.frame, self.branches, self.navigation_only = frame, tuple(branches), navigation_only
    self.map_reason = 'noJunctionBranches' if not branches else ''

  def status(self, phone):
    return self.resolve(phone).status

  def resolve(self, phone, *, speed=None, now_ns=None):
    """Find the junction the phone means. Phone distances are rounded and seconds old; the junction's is neither."""
    kind, side = phone.get('maneuverType', ''), phone.get('maneuverModifier', '')
    distance = phone.get('distanceMeters')
    key = (phone.get('session'), phone.get('source'), phone.get('instruction', ''), phone.get('roadName', ''), kind, side)
    now_ns = phone['observed'] if now_ns is None else now_ns
    self._drive(speed, now_ns)
    driven = 0. if phone['observed'] >= now_ns else self._driven_since(phone['observed'])
    passed = False
    if distance is not None and driven is not None:
      from openpilot.starpilot.navigation.wire import PHONE_PASSED_SLACK_M
      passed = driven > distance + PHONE_PASSED_SLACK_M
      distance = max(0., distance - driven)
    result = self._match(phone, kind, side, distance)
    # Without the distance driven since the phone's reading, or well past it, any match may be the next junction.
    if result.control and (driven is None or passed):
      result = Resolution('mapUnverifiable', reason='distanceDrivenUnknown' if driven is None else 'phoneJunctionPassed')
    if result.control:
      result = result._replace(anchor_ns=now_ns - max(0, self.updated_boot_ns - self.frame.gps_mono_ns))
      self.held = key, result, now_ns
      return result
    if self.held is not None:
      held_key, held, since = self.held
      # A live mapd drops the road (or the junction falls behind) in the last metres of an approach it already
      # confirmed. Carry the junction forward by the distance driven while the phone still agrees with it.
      moved = 0. if held.anchor_ns >= now_ns else self._driven_since(held.anchor_ns)
      if (self.fresh and moved is not None and driven is not None and not passed and held_key == key and distance is not None and
          result.status in HOLD_KEEPS and 0 <= now_ns - since <= HOLD_NS):
        left = held.distance - moved
        if left >= -HOLD_PASSED_M and abs(left - distance) <= max(80., distance * .3):
          self.held = key, held._replace(distance=left, anchor_ns=now_ns), since
          return held._replace(distance=max(0., left), anchor_ns=now_ns)
      self.held = None
    return result

  def _drive(self, speed, now_ns):
    known = speed is not None and math.isfinite(speed)
    then, metres = self.odometer[-1] if self.odometer else (now_ns, 0.)
    if known:
      self.speed_ns = now_ns
    if not 0 <= now_ns - then <= ODOMETER_GAP_NS or not 0 <= now_ns - self.speed_ns <= ODOMETER_GAP_NS:
      self.odometer.clear()
      then, metres = now_ns, 0.
    if not known and not self.odometer:
      return
    # A missed speed reading coasts on the last one for up to ODOMETER_GAP_NS, then the odometer restarts.
    self.speed = max(0., speed) if known else self.speed
    self.odometer.append((now_ns, metres + self.speed * (now_ns - then) * 1e-9))

  def _driven_since(self, stamp_ns):
    """Metres driven since a moment, from the speed seen at each call; a stop at a light counts as none.
    None when the odometer does not reach back that far."""
    if not self.odometer:
      return None
    now_ns, total = self.odometer[-1]
    after_ns, after = now_ns, total
    for at_ns, metres in reversed(self.odometer):
      if at_ns <= stamp_ns:
        span = after_ns - at_ns
        return total - (metres + (after - metres) * (stamp_ns - at_ns) / span if span > 0 else metres)
      after_ns, after = at_ns, metres
    # Shortly before the odometer started (its first call): assume the speed then was the speed now.
    if after_ns - stamp_ns > ODOMETER_GAP_NS:
      return None
    return total - after + self.speed * (after_ns - stamp_ns) * 1e-9

  def _match(self, phone, kind, side, distance):
    if not self.branches:
      return Resolution('mapUnavailable', reason=self.map_reason)
    # Stored local tiles do not retain roundabout exit identity or merge topology.
    if distance is None or kind not in VERIFIABLE:
      return Resolution('mapUnverifiable', reason='phoneDistanceUnknown' if distance is None else 'unsupportedManeuver')
    roads = instruction_roads(phone.get('roadName') or ('' if kind else phone.get('instruction', '')))
    if not kind and not roads:
      return Resolution('mapUnverifiable', reason='phoneRoadUnknown')
    tolerance = max(60., min(200., distance * .3))
    nearby = [row for row in self.branches if abs(row[1] - distance) <= tolerance]
    if not nearby:
      return Resolution('mapNoNearbyJunction', reason='junctionDistanceMismatch')
    if not kind:
      # Arrow-only guidance names just the road: a turn onto it here is evidence of the turn, not of its side.
      fits = [row for row in nearby if 45 <= abs(row[2]) <= 150 and same_road(row[5], roads)]
      if not fits:
        return Resolution('mapUnverifiable', reason='noNamedTurnBranch')
      kind, status = 'turn', 'mapInferred'
    else:
      if kind == 'fork' and len({row[4] for row in nearby}) < 2:
        return Resolution('mapUnverifiable', reason='forkBranchesMissing')
      fits = [row for row in nearby if self._direction(kind, side, row)]
      if not fits:
        return Resolution('mapDifferentDirection', reason='maneuverDirectionMismatch')
      named = [row for row in fits if same_road(row[5], roads)]
      fits, status = named or fits, 'mapAligned'
    if max(row[1] for row in fits) - min(row[1] for row in fits) > JUNCTION_SPAN_M:
      return Resolution('mapAmbiguous', reason='multipleJunctionsMatch')
    return Resolution(status, kind, side, min(fits, key=lambda row: abs(row[1] - distance))[1])

  @staticmethod
  def _direction(kind, side, row):
    angle, road_class = row[2], row[3]
    if kind in ('off ramp', 'on ramp') and not road_class.endswith('Link'):
      return False
    if kind == 'fork' and abs(angle) > 75:
      return False
    if not side and kind in ('off ramp', 'on ramp'):
      return True
    if side == 'left':
      return -170 < angle <= -12
    if side == 'right':
      return 12 <= angle < 170
    if side == 'slightLeft':
      return -80 <= angle <= -5
    if side == 'slightRight':
      return 5 <= angle <= 80
    if side == 'sharpLeft':
      return -175 <= angle <= -70
    if side == 'sharpRight':
      return 70 <= angle <= 175
    if side == 'uturn':
      return abs(angle) >= 150
    if side == 'straight' or kind == 'continue':
      return abs(angle) <= 35
    return abs(angle) >= 20 if not side else False
