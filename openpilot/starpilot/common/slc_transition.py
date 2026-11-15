"""Map approach identity and lower-limit distance between map samples."""

import math


def same_limit(a, b):
  return abs(float(a) - float(b)) < 0.1


MAP_MAX_AGE = 2.0
MAP_GAP_HOLD = 2.0  # seconds an active approach is kept while the map has no current limit


def map_distance_jump(previous, distance, speed):
  return previous is not None and distance > previous + max(20.0, 2.0 * speed)


class LowerLimitDistance:
  """Advance a lower preview between receipts without inventing a boundary."""

  def __init__(self):
    self.key = self.stamp = self.raw = self.remaining = self.last_now = None

  def update(self, current, upcoming, distance, road, speed, now, stamp):
    key = (current, upcoming, road)
    new = stamp != self.stamp
    reset = key != self.key or self.last_now is None or now < self.last_now or (new and map_distance_jump(self.raw, distance, speed))
    if reset:
      self.remaining = distance
    else:
      travel = max(0.0, min(speed, 70.0)) * min(max(0.0, now - self.last_now), MAP_MAX_AGE)
      self.remaining = min(max(0.0, self.remaining - travel), distance)
    # Wheel/along-way speed disagreement must not accumulate over kilometers
    # of preview. Fresh positions may correct upward within this bounded lead.
    self.remaining = max(self.remaining, distance - max(0.0, min(speed, 70.0)) * MAP_MAX_AGE)
    self.key, self.stamp, self.last_now = key, stamp, now
    if new or reset:
      self.raw = distance
    # Only a confirmed current-limit update can commit the zone or end its pin.
    return max(0.001, self.remaining)


class MapApproach:
  def __init__(self):
    self.key = None
    self.identity = ""
    self.serial = 0
    self.active = False
    self.phase = "stable"
    self.distance = None
    self.rolled_back = False
    self.seen = None
    self.last = None

  def hold(self, now, speed):
    """Keep an active approach through a short map gap, at most MAP_GAP_HOLD after the last valid sample.

    The boundary distance is carried forward from speed so a returning sample is still checked for a jump.
    Returns False, and ends the approach, once the gap is too long or the inputs are not usable.
    """
    if (
      self.active
      and self.seen is not None
      and self.last is not None
      and all(math.isfinite(v) for v in (now, speed))
      and 0 <= speed <= 70
      and 0 <= now - self.seen <= MAP_GAP_HOLD
      and now >= self.last
    ):
      if self.distance is not None:
        self.distance = max(0.0, self.distance - speed * (now - self.last))
      self.last = now
      return True
    self.update(0, 0, 0, '', 0, 0, False)
    return False

  def update(self, current, upcoming, distance, road, speed, trigger, valid, *, identity_distance=None, allow_activate=True, now=None):
    self.rolled_back = False
    self.seen = self.last = None
    valid = valid and all(math.isfinite(v) for v in (current, upcoming, distance, speed, trigger))
    if not valid or current < 1 or distance < 0:
      self.rolled_back = self.active
      if self.active:
        self.phase = "rollback"
      self.active = False
      return 0.0
    if self.active and self.key and same_limit(current, self.key[1]):
      self.active = False
      self.phase = "commit"
      # Publish the reached boundary before starting another approach. The next
      # planner frame may accept the following limit with a new checkpoint;
      # neither its key nor its preview may erase this commit in the same frame.
      return current
    if upcoming < 1 or same_limit(current, upcoming):
      if self.active:
        self.rolled_back = True
        self.phase = "rollback"
      self.active = False
      return current
    key = (current, upcoming)
    raw_distance = distance if identity_distance is None else identity_distance
    jumped = map_distance_jump(self.distance, raw_distance, speed)
    if key != self.key or jumped:
      self.rolled_back = self.active
      self.active = False
      self.key = key
      self.serial += 1
      self.identity = str(self.serial)
      self.phase = "stable"
    self.distance = raw_distance
    if now is not None and math.isfinite(now):
      self.seen = self.last = now
    if allow_activate and (distance == 0 or (trigger > 0 and distance <= trigger)):
      self.active = True
    if self.active:
      self.phase = "provisional"
      return upcoming
    return current
