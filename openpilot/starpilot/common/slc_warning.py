"""One-shot warning tied to a planned target transition, never to an audio queue."""

from collections import deque
import math
import time


class SpeedChangeWarning:
  def __init__(self):
    self.consumed = deque(maxlen=64)
    self.announced = deque(maxlen=64)
    self.primed = False
    self.event = None

  def update(self, now, identity, eta, target_change, eligible, lead_time):
    finite = all(math.isfinite(v) for v in (now, eta, lead_time))
    if not finite:
      identity, eligible = '', False
    if not self.primed:
      self.primed = True
      if identity and eta <= 0:
        self.consumed.append(identity)
    if self.event and (not eligible or identity != self.event['id'] or now > self.event['deadline']):
      self.event = None
    if identity and identity not in self.consumed:
      if not eligible and eta <= 0:
        self.consumed.append(identity)
      elif eligible and target_change and eta <= min(max(lead_time, 0.0), 10.0):
        self.consumed.append(identity)
        self.announced.append(identity)
        self.event = {'id': identity, 'issued': now, 'deadline': now + 0.25}
    return self.event


class AcceptedChangeWarning:
  """Immediate accepted numeric changes; never replay startup or re-engagement."""

  def __init__(self, scope):
    self.scope = scope
    self.sequence = 0
    self.previous = None
    self.was_eligible = False
    self.event = None

  def update(self, now, speed, *, eligible, announced=False):
    valid = speed is not None and math.isfinite(speed) and speed >= 1
    if self.event and (not eligible or now > self.event["deadline"]):
      self.event = None
    changed = valid and self.previous is not None and abs(speed - self.previous) >= 0.1
    if changed and eligible and self.was_eligible and not announced:
      self.sequence += 1
      self.event = {'id': f"{self.scope}:accepted:{self.sequence}", 'issued': now, 'deadline': now + 0.25}
    self.previous = speed if valid else None
    self.was_eligible = eligible
    return self.event


class WarningAudioGate:
  def __init__(self, started=None):
    self.started = time.monotonic() if started is None else started
    self.consumed = deque(maxlen=64)

  def consume(self, event, now, *, busy, enabled, fresh):
    identity = event.get('id', '')
    if not identity or identity in self.consumed:
      return False
    # Consume on arrival even when muted, busy or stale. Never replay later.
    self.consumed.append(identity)
    issued, deadline = event.get('issued', 0), event.get('deadline', 0)
    return bool(enabled and fresh and not busy and self.started <= issued <= now <= deadline <= issued + 0.251)


class WarningBoundaryPin:
  """Keep the warned approach visible until its boundary or invalidation."""

  def __init__(self):
    self.identity = ''
    self.issued = 0.0
    self.remaining = 0.0
    self.last_time = None
    self.seen = deque(maxlen=64)

  def update(self, now, event, identity, distance, speed, eligible):
    eligible = eligible and all(math.isfinite(x) for x in (now, distance, speed)) and distance > 0
    if self.last_time is not None and self.identity:
      self.remaining -= max(speed, 0.0) * min(max(now - self.last_time, 0.0), 0.5)
    self.last_time = now
    if not eligible or identity != self.identity or self.remaining <= 0:
      self.identity = ''
    if eligible and event and event['id'] == identity and identity not in self.seen:
      self.seen.append(identity)
      self.identity, self.issued = identity, event['issued']
      self.remaining = distance + max(50.0, 3.0 * max(speed, 0.0))
    return {'warningPinned': bool(self.identity), 'warningPinId': self.identity, 'warningPinIssued': self.issued if self.identity else 0.0}


class BoundaryWarningIdentity:
  """Retain only a warning ID through short, geographically consistent gaps.

  This never grants map/control authority. Missing way IDs cannot bridge a reset.
  A new producer, source, limit pair, boundary, distance jump or long gap rearms it.
  """

  def __init__(self):
    self.previous = None

  def update(self, identity, *, key, now, distance, speed):
    old = self.previous
    finite = all(math.isfinite(v) for v in (now, distance, speed))
    if not identity or key is None or not finite or not 0 <= distance <= 10000 or not 0 <= speed <= 70:
      return identity
    if old is not None:
      old_key, last, remaining, previous_id = old
      dt = now - last
      if key == old_key and 0 <= dt <= 2 and remaining - max(20.0, 70.0 * dt + 10.0) <= distance <= remaining + 20.0:
        identity = previous_id
    self.previous = (key, now, distance, identity)
    return identity
