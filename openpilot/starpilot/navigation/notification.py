"""Adapt forwarded phone guidance to the existing navigation instruction schema."""


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
          'distanceMeters': phone.get('distanceMeters') or 0}, 'notificationGuidance'


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

  def update(self, road, *, valid, event_boot_ns, received, alive, now_boot_ns):
    from openpilot.starpilot.speed_limits.map_source import copy_frame
    try:
      frame = copy_frame(road, event_valid=valid, event_boot_ns=event_boot_ns) if received else None
      evidence = self.tracker.step(frame, received=received, now_boot_ns=now_boot_ns, transport_alive=alive)
      accepted = self.tracker.diagnostic(now_boot_ns=now_boot_ns).frame
      if not alive:
        self.frame, self.branches = None, ()
      elif frame is not None and frame.event_mono_ns > self.last_event_ns:
        self.last_event_ns = frame.event_mono_ns
        self.frame, self.branches = None, ()
        # A matched road (speed-limit grade), or mapd's navigation-only pick on an ambiguous fix.
        if accepted is frame and evidence.kind in MATCHED:
          self._take(road, frame, navigation_only=False)
        elif accepted is None and navigation_frame(frame, now_boot_ns):
          self._take(road, frame, navigation_only=True)
      if self.frame is not None and not (navigation_frame(self.frame, now_boot_ns) if self.navigation_only else
                                         accepted is self.frame):
        self.frame, self.branches = None, ()
    except Exception:
      # Cereal's lazy readers can raise KjException. Bad optional map evidence
      # must neither retain old confirmation nor interrupt phone/Mapbox output.
      if not self.failure_logged:
        from openpilot.common.swaglog import cloudlog
        cloudlog.exception('notification navigation: mapdOut unreadable; map corroboration off')
        self.failure_logged = True
      self.tracker.reset()
      self.frame, self.branches, self.last_event_ns = None, (), 0

  def _take(self, road, frame, *, navigation_only):
    import math
    rows = road.navigationBranches
    if len(rows) > 32:
      return
    branches = []
    for row in rows:
      junction = float(row.junctionLatitude), float(row.junctionLongitude)
      distance, angle, way_id = float(row.distanceMeters), float(row.angleDegrees), int(row.wayId)
      if (not all(math.isfinite(v) for v in (*junction, distance, angle)) or
          not -90 <= junction[0] <= 90 or not -180 <= junction[1] <= 180 or
          not 0 <= distance <= 1500 or not -180 <= angle <= 180 or way_id <= 0):
        return
      branches.append((junction, distance, angle, str(row.highwayClass), way_id))
    self.frame, self.branches, self.navigation_only = frame, tuple(branches), navigation_only

  def status(self, phone):
    if not self.branches:
      return 'mapUnavailable'
    distance = phone.get('distanceMeters')
    kind, side = phone.get('maneuverType', ''), phone.get('maneuverModifier', '')
    # Stored local tiles do not retain roundabout exit identity or merge topology.
    if distance is None or kind not in ('turn', 'continue', 'off ramp', 'on ramp', 'fork'):
      return 'mapUnverifiable'
    tolerance = max(60., min(200., distance * .3))
    nearby = [row for row in self.branches if abs(row[1] - distance) <= tolerance]
    if not nearby:
      return 'mapNoNearbyJunction'
    if len({row[0] for row in nearby}) > 1:
      return 'mapAmbiguous'
    if kind == 'fork' and len({row[4] for row in nearby}) < 2:
      return 'mapUnverifiable'
    for _, _, angle, road_class, _ in nearby:
      if kind in ('off ramp', 'on ramp') and not road_class.endswith('Link'):
        continue
      if kind == 'fork' and abs(angle) > 75:
        continue
      matches = {
        'left': -170 < angle <= -12, 'right': 12 <= angle < 170,
        'slightLeft': -80 <= angle <= -5, 'slightRight': 5 <= angle <= 80,
        'sharpLeft': -175 <= angle <= -70, 'sharpRight': 70 <= angle <= 175,
        'uturn': abs(angle) >= 150, 'straight': abs(angle) <= 35,
        '': abs(angle) <= 35 if kind == 'continue' else abs(angle) >= 20,
      }
      if (not side and kind in ('off ramp', 'on ramp')) or matches.get(side, False):
        return 'mapAligned'
    return 'mapDifferentDirection'
