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


class LocalMapCorroborator:
  """Optional geometry evidence from mapd's matched road; never route authority."""
  def __init__(self):
    from openpilot.starpilot.speed_limits.map_source import MapTracker
    self.tracker = MapTracker()
    self.frame = None
    self.branches = ()

  def update(self, road, *, valid, event_boot_ns, received, alive, now_boot_ns):
    import math
    from openpilot.starpilot.speed_limits.map_source import copy_frame
    try:
      frame = copy_frame(road, event_valid=valid, event_boot_ns=event_boot_ns) if received else None
      evidence = self.tracker.step(frame, received=received, now_boot_ns=now_boot_ns, transport_alive=alive)
      accepted = self.tracker.diagnostic(now_boot_ns=now_boot_ns).frame
      if frame is not None and accepted is frame:
        self.frame, self.branches = None, ()
        if evidence.kind in ('matched_limit_unqualified', 'matched_no_limit_unqualified'):
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
          self.frame, self.branches = frame, tuple(branches)
      if accepted is not self.frame:
        self.frame, self.branches = None, ()
    except Exception:
      # Cereal's lazy readers can raise KjException. Bad optional map evidence
      # must neither retain old confirmation nor interrupt phone/Mapbox output.
      self.tracker.reset()
      self.frame, self.branches = None, ()

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
