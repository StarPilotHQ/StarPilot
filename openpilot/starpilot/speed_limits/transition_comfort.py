"""SLC-only fixed comfort ceilings, before protective candidate arbitration."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Adjustment:
  now_ns: int
  target: float
  direction: int
  limit: float
  # The target is SLC's ceiling beneath the driver's SET (or SET where that is lower), not a set speed SLC wrote.
  ceiling: bool = False


class TransitionComfort:
  def __init__(self):
    self.baseline = None
    self.target = None
    self.started = self.deadline = self.release_until = 0
    self.direction = 0
    self.armed = self.pedal_was_pressed = False

  def step(self, result, frame, *, settings, selected, speed, acknowledged, manual, reengage, selected_acknowledged=False, limits=None, ceiling=False):
    """selected: the set speed SLC wrote and card acknowledged or, with ceiling=True, the speed the ceiling model is
    driving to this frame (the lower of the driver's SET and the accepted limit + offset)."""
    now = frame.now_ns
    valid_limits = (
      isinstance(limits, dict)
      and set(limits) == {'acceleration', 'braking'}
      and all(type(v) in (int, float) and math.isfinite(v) and 0.1 <= v <= 3.5 for v in limits.values())
    )
    if (
      not (settings.limit_acceleration or settings.limit_braking)
      or not frame.authority.can_control
      or frame.host.force_decel
      or result.errors
      or reengage
      or not all(math.isfinite(v) for v in (selected, speed))
      or not 1 <= selected <= 80
    ):
      self.__init__()
      return None
    accepted = result.state.acceptance.accepted
    candidate = result.selection.observation.candidate if result.selection is not None else None
    eligible = (
      accepted is not None
      and candidate is not None
      and accepted.candidate.speed_mps == candidate.speed_mps
      and result.domain is not None
      and result.domain.context is not None
    )
    if eligible:
      key = (accepted.candidate.source, accepted.candidate.speed_mps)
      old, self.baseline = self.baseline, key
      if old is not None and abs(old[1] - key[1]) >= 0.1:
        # Preserve the existing policy for a recalled/manual target present
        # before this transition. Intervention after arming is retained below.
        if result.state.override.persistent_selected_mps is not None:
          self.target = None
          self.direction = 0
          return None
        self.target = selected if ceiling else result.domain.context.effective_cluster_mps
        error = self.target - speed
        self.direction = 1 if error > 0.05 else -1 if error < -0.05 else 0
        self.started = now
        self.release_until = 0
        self.pedal_was_pressed = False
        self.deadline = now + int(min(600.0, max(30.0, 1.5 * abs(error) / 0.1 + 10.0)) * 1e9)
        self.armed = False
    if self.target is None or not self.started <= now <= self.deadline:
      self.target = None
      return None
    # After SLC has handed over a target, the observed selected speed owns it.
    # Wheel/Bluetooth/pedal targets may revise it without canceling the episode.
    # No manual-only change can create an episode or extend its original expiry.
    if not self.armed:
      # A ceiling needs no hand-over to card: it applies in the planner on the frame it is published.
      self.armed = bool(ceiling or (acknowledged or selected_acknowledged) and abs(selected - self.target) <= 0.15)
      if not self.armed:
        if now - self.started > 1_000_000_000:
          self.target = None
        return None
    if abs(selected - self.target) > 0.01:
      self.target = selected
      error = selected - speed
      self.direction = 1 if error > 0.05 else -1 if error < -0.05 else 0
    if self.pedal_was_pressed and not frame.pedal_pressed:
      self.release_until = now + 500_000_000  # Allow the existing pedal capture to reach card.
    self.pedal_was_pressed = frame.pedal_pressed
    if frame.pedal_pressed:
      return None
    if self.direction * (self.target - speed) <= 0.05:
      if not manual and now >= self.release_until:
        self.target = None
      return None
    # A temporarily unqualified profile frame suppresses output, not the
    # accepted transition. Fresh settings can resume it before its old deadline.
    if not valid_limits:
      return None
    # Each direction is capped only while its own switch (Use SLC acceleration / braking limits) is on.
    if not (settings.limit_acceleration if self.direction > 0 else settings.limit_braking):
      return None
    return Adjustment(now, self.target, self.direction, limits['acceleration' if self.direction > 0 else 'braking'], bool(ceiling))


def ceiling(request, *, now_ns, selected, speed, blocked):
  if (
    not isinstance(request, Adjustment)
    or blocked
    or request.direction not in (-1, 1)
    or not 0 <= now_ns - request.now_ns <= 100_000_000
    or not all(math.isfinite(x) for x in (request.target, request.limit, selected, speed))
    or not 0.1 <= request.limit <= 3.5
    or type(request.ceiling) is not bool
    or not request.ceiling
    and abs(selected - request.target) > 0.15
    or request.direction * (request.target - speed) <= 0.05
  ):
    return None
  return request.limit
