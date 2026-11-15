"""Driver targets by posted limit for the current drive; no source or actuator authority.

Remember manual speed (this drive): a speed you set yourself is kept for that posted limit until the drive ends
(the runtime resets between drives). An optional expiry forgets a target after that long under other limits.
"""

import math
from dataclasses import dataclass

EXPIRY_NS = 30_000_000_000
MAX_STEP_NS = 500_000_000


@dataclass
class RememberedTarget:
  target: float
  away_ns: int = 0


class PerLimitTargets:
  def __init__(self, expiry_ns: int | None = None):
    self.expiry_ns = expiry_ns
    self.entries = {}
    self.current = None
    self.last_known = None
    self.last_ns = None
    self.enabled = False

  @staticmethod
  def key(limit):
    return round(limit * 10) if type(limit) in (int, float) and math.isfinite(limit) and 1 <= limit <= 80 else None

  def clear(self):
    self.__init__(self.expiry_ns)

  def observe(self, now_ns, limit, *, enabled, fresh):
    if not enabled:
      self.clear()
      return
    if type(now_ns) is not int or now_ns < 0 or self.last_ns is not None and now_ns < self.last_ns:
      self.clear()
      return
    key = self.key(limit) if fresh else None
    dt = now_ns - self.last_ns if self.last_ns is not None else 0
    continuous = 0 <= dt <= MAX_STEP_NS
    for old, entry in list(self.entries.items()):
      # Only intervals bounded by fresh valid limits count as time elsewhere.
      if continuous and key is not None and self.current is not None and old != self.current:
        entry.away_ns += dt
      if self.expiry_ns is not None and entry.away_ns >= self.expiry_ns:
        del self.entries[old]
      elif old == key:
        entry.away_ns = 0
    self.current, self.last_ns, self.enabled = key, now_ns, True
    if key is not None:
      self.last_known = key

  def remember(self, target, limit=None):
    key = self.key(limit) if limit is not None else self.last_known
    if self.enabled and key is not None and type(target) in (int, float) and math.isfinite(target) and 8 / 3.6 <= target <= 145 / 3.6:
      self.entries[key] = RememberedTarget(target)

  def forget(self, limit=None):
    key = self.key(limit) if limit is not None else self.last_known
    self.entries.pop(key, None)

  def target(self, limit, now_ns=None):
    key = self.key(limit)
    entry = self.entries.get(key) if self.enabled else None
    if entry is None:
      return None
    elapsed = entry.away_ns
    # Forecast runs before the next current-limit observation. Do not use an
    # entry already expiring on this frame; unknown/stalled time never counts.
    if now_ns is not None and self.last_ns is not None and self.current not in (None, key):
      dt = now_ns - self.last_ns
      if 0 <= dt <= MAX_STEP_NS:
        elapsed += dt
    return entry.target if self.expiry_ns is None or elapsed < self.expiry_ns else None
