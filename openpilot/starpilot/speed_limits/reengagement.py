"""SLC engagement intent, independent of accelerator press/release.

Memory is local to one drive and a continuously observed current-limit zone.
It never stores actuator authority or uses lookahead as a new current zone.
"""

import math


class Engagement:
  def __init__(self):
    self.zone = None
    self.manual = None
    self.remembered = None
    self.was_enabled = False
    self.pending = False
    self.deadline = 0
    self.floor = 0.0
    self.target = None
    self.last_ns = None

  def observe(self, *, now_ns, zone, enabled, fresh, selected, manual=None, remember=False, match=False, explicit=False):
    continuous = self.last_ns is None or 0 < now_ns - self.last_ns <= 500_000_000
    self.last_ns = now_ns
    if not fresh or not continuous or zone is None or zone != self.zone:
      self.manual = self.remembered = None
      self.target = None
      if not fresh or not continuous:
        self.pending = False
    self.zone = zone if fresh else None
    if not remember:
      self.remembered = None
    if manual is not None or explicit:
      self.pending = False
      self.target = None
      self.remembered = None
      self.manual = manual if not explicit and zone is not None else None
    if fresh:
      if self.was_enabled and not enabled:
        self.remembered = self.manual if remember and match and zone is not None else None
        self.pending = False
        self.target = None
      if enabled and not self.was_enabled:
        self.pending = match and zone is not None and continuous
        self.deadline = now_ns + 5_000_000_000
        self.floor = selected
        self.target = None
      self.was_enabled = enabled
    if not enabled or not match or now_ns > self.deadline:
      self.pending = False

  def resolve(self, *, limit_target, provisional, now_ns, keep_faster=False):
    if not self.pending or now_ns > self.deadline or self.zone is None:
      return None
    if self.target is None:
      # Keep faster: engaging above the limit target keeps that speed; otherwise the limit target wins.
      self.target = self.remembered if self.remembered is not None and not provisional else max(self.floor, limit_target) if keep_faster else limit_target
    return self.target if math.isfinite(self.target) and self.target > 0 else None

  def applied(self):
    self.pending = False


class CurrentLimitZone:
  """Same posted limit may move between fresh producers without a new zone.

  Producer restarts, missing current limits and actual limit changes still
  invalidate recall. This is driver-intent continuity, never source authority.
  """

  def __init__(self):
    self.limit = None
    self.generation = 0
    self.producers = {}

  def observe(self, source, producer, limit):
    if source is None or limit is None or not math.isfinite(limit) or limit < 1:
      if self.limit is not None:
        self.generation += 1
      self.limit = None
      return None
    restarted = source in self.producers and self.producers[source] != producer
    if restarted or self.limit is None or abs(limit - self.limit) >= 0.1:
      self.generation += 1
    self.producers[source] = producer
    self.limit = limit
    return self.generation
