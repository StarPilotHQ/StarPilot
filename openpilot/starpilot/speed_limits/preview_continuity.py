"""Bounded continuity of an already accepted preview; never a new adoption."""

import math
from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits.map_source import _classify


class AcceptedPreviewHold:
  def __init__(self):
    self.anchor = None
    self.crossing = False
    self.active = False
    self.recovery_start = None

  @staticmethod
  def pair(frame):
    return (frame.producer_session, frame.generation, frame.source, frame.road_name, frame.speed_mps, frame.next_speed_mps)

  def keep(self, frame, *, accepted, now_boot_ns, speed, enabled):
    self.active = False
    old = self.anchor
    if old is None:
      return None
    prior, chosen = old
    if (
      not enabled
      or frame is None
      or accepted is None
      or accepted.candidate != chosen.candidate
      or not math.isfinite(speed)
      or not 0 <= speed <= 70
      or _classify(frame, now_boot_ns).kind != 'matched_limit_unqualified'
      or (frame.producer_session, frame.generation, frame.source, frame.road_name) != (prior.producer_session, prior.generation, prior.source, prior.road_name)
    ):
      self.anchor = None
      self.crossing = False
      return None
    if not all(math.isfinite(x) for x in (frame.next_distance_m, frame.next_speed_mps)):
      return None
    same = abs(frame.speed_mps - prior.speed_mps) < 0.1 and abs(frame.next_speed_mps - prior.next_speed_mps) < 0.1
    weak = frame.way_selection != 'current'
    crossed = abs(frame.speed_mps - prior.next_speed_mps) < 0.1
    near = prior.next_distance_m <= max(20.0, 2.0 * speed)
    missing_preview = (
      weak and abs(frame.speed_mps - prior.speed_mps) < 0.1 and frame.way_id == prior.way_id and frame.next_speed_mps == 0.0 and frame.next_distance_m == 0.0
    )
    eligible = same and (weak or frame.next_distance_m <= 0) or weak and crossed and near or missing_preview
    if not eligible:
      return None
    self.recovery_start = None
    self.crossing = True  # Weak jitter cannot renew the original one-second lease.
    age = (now_boot_ns - prior.event_mono_ns) / 1e9
    sample_age = (frame.event_mono_ns - prior.event_mono_ns) / 1e9
    progress = max(20.0, speed * sample_age + 10.0)
    distance_consistent = missing_preview or prior.next_distance_m - progress <= frame.next_distance_m <= prior.next_distance_m + max(20.0, 2.0 * speed)
    if 0 <= sample_age <= age <= 1.0 and frame.event_mono_ns > prior.event_mono_ns and distance_consistent:
      self.active = True
      return chosen
    return None

  def observe(self, frame, chosen, *, active):
    if frame is None:
      return
    if self.anchor is not None and self.pair(frame) != self.pair(self.anchor[0]):
      # A weak crossing is handled by keep(), not permitted to create an anchor.
      if frame.way_selection == 'current':
        self.anchor = None
        self.crossing = False
    # A full second of independently fresh current-road evidence ends the weak
    # episode. One isolated current frame must not renew an expired hold.
    if self.crossing and self.anchor is not None and active and frame.way_selection == 'current' and self.pair(frame) == self.pair(self.anchor[0]):
      if self.recovery_start is None:
        self.recovery_start = frame.event_mono_ns
      elif frame.event_mono_ns - self.recovery_start >= 1_000_000_000:
        self.crossing = False
        self.recovery_start = None
    elif frame.way_selection != 'current':
      self.recovery_start = None
    if (
      active
      and not self.crossing
      and frame.way_selection == 'current'
      and frame.road_name
      and frame.next_distance_m >= 0
      and chosen.kind is acc.ObservationKind.VALID
      and chosen.candidate is not None
      and abs(chosen.candidate.speed_mps - frame.next_speed_mps) < 0.1
    ):
      if self.anchor is None or frame.event_mono_ns > self.anchor[0].event_mono_ns:
        self.anchor = (frame, chosen)
