from dataclasses import dataclass
import math

ARRIVED_DISPLAY_M = 10.0   # this close to the final "arrive" maneuver, the card reads "Arrived"


@dataclass(frozen=True)
class NavigationDisplay:
  key: tuple[str, int]
  text: str
  maneuver_type: str
  modifier: str
  distance_m: float
  remaining_distance_m: float
  remaining_seconds: float
  arrived: bool = False
  then_type: str = ''
  then_modifier: str = ''
  then_distance_m: float | None = None
  lanes: tuple = ()


def navigation_display(message, *, now_ns: int, drive_id: int) -> NavigationDisplay | None:
  if message is None or drive_id <= 0:
    return None
  try:
    if (not message.enabled or message.status not in ('guiding', 'arrived') or
        message.startedMonoTime != drive_id or not message.sessionId or
        not drive_id <= message.frameMonoTime <= now_ns <= message.frameMonoTime + 3_000_000_000 or
        not drive_id <= message.locationMonoTime <= now_ns <= message.locationMonoTime + 3_000_000_000):
      return None
    instruction = message.instruction
    distances = (instruction.distanceMeters, instruction.remainingDistanceMeters, instruction.remainingDurationSeconds)
    if not all(math.isfinite(value) and value >= 0 for value in distances):
      return None
    arrived = message.status == 'arrived' or (
      instruction.maneuverType == 'arrive' and instruction.distanceMeters <= ARRIVED_DISPLAY_M)
    text = 'You have arrived' if arrived else ' '.join(instruction.text.split())[:240]
    if not text:
      return None
    lanes = tuple((lane.active, tuple(lane.directions), lane.activeDirection) for lane in list(instruction.lanes)[:8])
    then = message.nextManeuver
    then_type = '' if arrived else then.maneuverType
    then_distance = then.distanceMeters if then_type and math.isfinite(then.distanceMeters) else None
    return NavigationDisplay((message.sessionId, drive_id), text, instruction.maneuverType,
                             instruction.maneuverModifier, *distances, arrived,
                             then_type, then.maneuverModifier if then_type else '', then_distance,
                             () if arrived else lanes)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None


def distance_text(meters: float, metric: bool) -> str:
  if metric:
    if meters < 1000:
      step = 25 if meters < 500 else 50
      return f'{round(meters / step) * step} m'
    kilometers = meters / 1000
    return f'{kilometers:.0f} km' if kilometers >= 10 else f'{kilometers:.1f} km'
  feet = meters * 3.28084
  if feet < 1000:
    step = 10 if feet <= 100 else 50
    return f'{round(feet / step) * step} ft'
  miles = feet / 5280
  return f'{miles:.0f} mi' if miles >= 10 else f'{miles:.1f} mi'


def official_navigation_display(instruction, route, *, destination, drive_id: int) -> NavigationDisplay | None:
  """Read-only presentation of comma nav alpha; never rewrites StarPilot navigation or map/SLC sources.

  The caller supplies transport-valid, fresh, post-transition messages. This
  adapter only uses a route with a destination and tags its display identity.
  """
  if instruction is None or route is None or destination is None or drive_id <= 0:
    return None
  try:
    latitude, longitude = destination['latitude'], destination['longitude']
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
      return None
    coordinates = route.coordinates
    if len(coordinates) < 2:
      return None
    endpoints = tuple((point.longitude, point.latitude) for point in (coordinates[0], coordinates[-1]))
    if not all(-180 <= lon <= 180 and -90 <= lat <= 90 for lon, lat in endpoints):
      return None
    distances = (instruction.maneuverDistance, instruction.distanceRemaining, instruction.timeRemaining)
    if not all(math.isfinite(value) for value in distances):
      return None
    text = ' '.join(instruction.maneuverPrimaryText.split())[:240]
    if not text:
      return None
    # Destination and route endpoints keep card collapse stable across periodic
    # route resends, but reset it for a new trip or changed route.
    identity = f'comma:{longitude},{latitude}:{len(coordinates)}:{endpoints}'
    return NavigationDisplay((identity, drive_id), text, instruction.maneuverType,
                             instruction.maneuverModifier, max(0., distances[0]),
                             max(0., distances[1]), max(0., distances[2]))
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return None
