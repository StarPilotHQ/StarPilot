"""Checked restoration of a withdrawn lower-limit preview.

The checkpoint never rolls back the action ledger or acceptance sequence IDs.
A live current-way match, an acknowledged preview command and unchanged driver
intent are all needed. Loss of evidence holds the set speed; it cannot turn
into automatic adoption of the higher original limit via another source.
"""

from dataclasses import dataclass
import math


@dataclass
class Checkpoint:
  candidate: object
  preview_identity: str
  previous_target: float
  previous_override: float | None
  started_ns: int
  expected: float
  command_id: int = 0
  pending_target: float | None = None
  pending_kind: str = ""
  acknowledged: bool = False
  permitted: bool = True


class PreviewRollback:
  def __init__(self):
    self.checkpoint = None
    self.ready = False
    self.in_preview = False
    self.last_ns = None

  def cancel(self):
    if self.checkpoint is not None:
      self.checkpoint.permitted = False
    self.ready = False

  def observe(
    self,
    *,
    now_ns,
    enabled,
    fresh,
    selected,
    manual,
    explicit,
    current,
    map_selected,
    map_fresh,
    preview,
    preview_identity,
    upcoming,
    accepted,
    previous_override,
  ):
    continuous = self.last_ns is None or 0 < now_ns - self.last_ns <= 500_000_000
    self.last_ns = now_ns
    self.ready = False
    self.in_preview = False
    if fresh and not enabled:
      self.checkpoint = None
      return
    if explicit:
      self.cancel()  # A request alone cannot release the hold; require a consumed receipt.
      return
    cp = self.checkpoint
    if cp is None:
      if (
        enabled
        and fresh
        and continuous
        and manual is None
        and map_selected
        and map_fresh
        and current is not None
        and accepted is not None
        and preview
        and preview_identity
        and 1 <= upcoming < current.speed_mps
        and abs(accepted.candidate.speed_mps - current.speed_mps) < 0.02
        and math.isfinite(selected)
        and selected > 0
      ):
        cp = self.checkpoint = Checkpoint(current, preview_identity, selected, previous_override, now_ns, selected)
      else:
        return
    # Confirmed entry into another current-limit zone ends preview ownership.
    # A producer/way change at the original numeric limit does NOT prove rollback.
    if map_fresh and current is not None and abs(current.speed_mps - cp.candidate.speed_mps) >= 0.02:
      self.checkpoint = None
      return
    if not continuous or not fresh or not enabled or manual is not None or now_ns - cp.started_ns > 120_000_000_000:
      self.cancel()
    allowed = abs(selected - cp.expected) <= 0.1
    if cp.pending_target is not None:
      allowed |= abs(selected - cp.pending_target) <= 0.5  # Card rounds display units before acknowledging.
    if not math.isfinite(selected) or not allowed:
      self.cancel()
    if map_fresh and current is not None and current != cp.candidate:
      self.cancel()
    if preview and preview_identity != cp.preview_identity:
      self.cancel()
    self.in_preview = bool(preview and preview_identity == cp.preview_identity and cp.permitted)
    self.ready = bool(
      cp.permitted
      and cp.acknowledged
      and fresh
      and enabled
      and map_selected
      and map_fresh
      and current == cp.candidate
      and not preview
      and not (1 <= upcoming < current.speed_mps)
      and (cp.pending_target is None or cp.pending_kind == 'previewRollback')
    )

  def issued(self, *, command_id, target, kind):
    cp = self.checkpoint
    if cp is not None and ((self.in_preview and target < cp.previous_target - 0.1) or kind == 'previewRollback'):
      cp.command_id = command_id
      cp.pending_target = target
      cp.pending_kind = kind

  def acknowledged(self, *, command_id, selected, kind):
    cp = self.checkpoint
    if cp is None or command_id != cp.command_id or cp.pending_target is None:
      return None
    if kind == 'previewRollback':
      previous_override = cp.previous_override if cp.permitted else None
      self.checkpoint = None
      self.ready = False
      return previous_override
    cp.expected = selected
    cp.pending_target = None
    cp.acknowledged = True
    return None

  @property
  def target(self):
    return self.checkpoint.previous_target if self.ready else None

  @property
  def blocks_commands(self):
    return self.checkpoint is not None and not self.in_preview and not self.ready
