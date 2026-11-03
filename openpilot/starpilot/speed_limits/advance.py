"""One mapped boundary offered through the existing SLC decision owner."""
import math
from dataclasses import dataclass

from openpilot.starpilot.speed_limits import acceptance as acc, selection as sel
from openpilot.starpilot.speed_limits.map_observation import Upcoming


@dataclass
class Encounter:
  boundary_id: str
  from_mps: float
  speed_mps: float
  way_id: int
  started_ns: int | None
  approach_way_id: int
  crossed: bool = False
  declined: bool = False
  withdrawn: bool = False
  disabled: bool = False
  accepted_ns: int | None = None
  applied_preview: acc.AcceptedLimit | None = None
  retired: bool = False


class Advance:
  def __init__(self):
    self.encounter: Encounter | None = None
    self.boundary_id = ''
    self.started_ns: int | None = None
    self.retired_cap: acc.AcceptedLimit | None = None
    self.retired_pending_speed_mps: float | None = None

  def disable(self):
    if self.encounter is not None and not self.encounter.crossed:
      self.encounter.disabled = True

  def use_current(self, candidate):
    self.boundary_id, self.started_ns = '', None
    self.retired_cap = None
    self.retired_pending_speed_mps = None
    if self.encounter is not None:
      self.encounter.disabled = True
      self.encounter.withdrawn = False
      if candidate.speed_mps == self.encounter.speed_mps:
        self.encounter.declined = False
        self.encounter.accepted_ns = None

  def propose(self, observations, policy, upcoming: Upcoming | None, *, current_way_id: int,
              higher_seconds: int, lower_seconds: int, speed_mps: float | None, standstill: bool, can_offer: bool,
              confirmation_active: bool, now_ns: int, pending: acc.PendingDecision | None, live: acc.Candidate | None,
              current_unresolved: bool):
    self.boundary_id, self.started_ns = '', None
    self.retired_cap = None
    self.retired_pending_speed_mps = None
    current = observations[sel.Source.MAP].candidate
    encounter = self.encounter
    pending_preview = (pending is not None and encounter is not None and pending.candidate.source == 'map' and
                       pending.candidate.speed_mps == encounter.speed_mps)
    if encounter is not None and current_way_id:
      if not encounter.crossed and current_way_id == encounter.way_id and current is not None:
        self.retired_cap = encounter.applied_preview
        if encounter.retired or current.speed_mps != encounter.speed_mps:
          if pending_preview:
            self.retired_pending_speed_mps = encounter.speed_mps
          self.encounter = None
          return None
        encounter.crossed = True
      elif not encounter.crossed and (
          current_way_id not in (encounter.approach_way_id, encounter.way_id) or
          current is not None and not encounter.retired and current.speed_mps not in (encounter.from_mps, encounter.speed_mps)):
        if (current is not None and upcoming is not None and upcoming.way_id == encounter.way_id and
            upcoming.observation.candidate.speed_mps == encounter.speed_mps and current.speed_mps == encounter.from_mps):
          encounter.approach_way_id = current_way_id
        else:
          self.retired_cap = encounter.applied_preview
          if pending_preview:
            self.retired_pending_speed_mps = encounter.speed_mps
          if current_way_id == encounter.approach_way_id:
            encounter.applied_preview = None
            encounter.retired = True
          else:
            self.encounter = None
          return None
      elif encounter.crossed and current is not None and current.speed_mps != encounter.speed_mps:
        self.encounter = encounter = None

    held_wins = False
    if encounter is not None and not encounter.crossed and (not encounter.retired or encounter.applied_preview is not None):
      held = (encounter.applied_preview.candidate if encounter.applied_preview is not None else
              pending.candidate if pending_preview and not encounter.retired else None)
      if held is not None:
        # Compare projected Maps candidates without publishing them as fresh observations.
        comparison = dict(observations)
        comparison[sel.Source.MAP] = acc.Observation(acc.ObservationKind.VALID, held)
        fallback = current is None and live is not None and live.speed_mps != encounter.from_mps
        held_wins = not fallback and sel.select_limit(comparison, policy).selected_source is sel.Source.MAP
        pending_wins = held_wins
        if pending_preview and pending.candidate != held:
          comparison[sel.Source.MAP] = acc.Observation(acc.ObservationKind.VALID, pending.candidate)
          pending_wins = not fallback and sel.select_limit(comparison, policy).selected_source is sel.Source.MAP
        if not held_wins:
          self.retired_cap = encounter.applied_preview
          encounter.applied_preview = None
        if not pending_wins:
          encounter.retired = True
          if pending_preview and (live is None or live.speed_mps != encounter.speed_mps or held_wins):
            self.retired_pending_speed_mps = encounter.speed_mps
        if encounter.retired and not held_wins:
          return None
    if encounter is not None and not encounter.declined and encounter.accepted_ns is None:
      if not confirmation_active:
        encounter.started_ns = None
      elif encounter.started_ns is None:
        encounter.started_ns = now_ns
    if current is None:
      if encounter is not None and not encounter.crossed and (not encounter.retired or held_wins):
        return {source: acc.Observation(acc.ObservationKind.UNKNOWN)
                if observation.candidate is not None and observation.candidate.speed_mps == encounter.from_mps
                else observation for source, observation in observations.items()}
      return None
    # A tile refresh can change the boundary ID for the same road and limit.
    replaced = (encounter is not None and upcoming is not None and
                (upcoming.way_id != encounter.way_id or upcoming.observation.candidate.speed_mps != encounter.speed_mps))
    previous = encounter if replaced and not encounter.crossed else None
    if replaced and not (encounter.crossed or encounter.declined or encounter.disabled or encounter.retired) and encounter.accepted_ns is None:
      if pending_preview:
        self.retired_pending_speed_mps = encounter.speed_mps
      if encounter.applied_preview is None:
        self.encounter = encounter = None
      else:
        encounter.started_ns = None

    seconds = (higher_seconds if upcoming is not None and upcoming.observation.candidate.speed_mps > current.speed_mps
               else lower_seconds)
    pending_current = (pending is not None and live is not None and
                       pending.candidate.source == live.source and pending.candidate.speed_mps == live.speed_mps)
    # shortcut: use fresh GPS distance without propagation; revisit only if replay shows material timing error.
    current_first = current_unresolved and (encounter is None or encounter.crossed)
    eligible = (can_offer and not (pending_current or current_first) and type(seconds) is int and 0 < seconds <= 10 and not standstill and
                speed_mps is not None and type(speed_mps) in (int, float) and math.isfinite(speed_mps) and speed_mps > 0 and
                upcoming is not None and upcoming.distance_m <= speed_mps * seconds)
    if eligible:
      if encounter is None or replaced and not (encounter.crossed and pending_preview):
        proposed = dict(observations)
        proposed[sel.Source.MAP] = upcoming.observation
        if sel.select_limit(proposed, policy).selected_source is sel.Source.MAP:
          assert upcoming.observation.candidate is not None
          encounter = self.encounter = Encounter(upcoming.boundary_id, current.speed_mps,
            upcoming.observation.candidate.speed_mps, upcoming.way_id, now_ns, current_way_id,
            applied_preview=previous.applied_preview if previous is not None else None)
      elif not encounter.crossed:
        encounter.boundary_id = upcoming.boundary_id
    if encounter is None:
      return None
    if encounter.retired:
      return dict.fromkeys(sel.Source, acc.Observation(acc.ObservationKind.UNKNOWN)) if held_wins else None

    proposed = dict(observations)
    blocked = encounter.declined or encounter.withdrawn
    if encounter.crossed:
      if blocked:
        proposed = {source: acc.Observation(acc.ObservationKind.UNKNOWN)
                    if observation.candidate is not None and observation.candidate.speed_mps == encounter.speed_mps
                    else observation for source, observation in proposed.items()}
      else:
        if not encounter.disabled and sel.select_limit(observations, policy).selected_source is sel.Source.MAP:
          self.started_ns = encounter.started_ns
        return None
    elif (not blocked and not encounter.disabled and upcoming is not None and
          not (replaced and encounter is previous) and
          upcoming.boundary_id == encounter.boundary_id and
          upcoming.observation.candidate.speed_mps == encounter.speed_mps):
      proposed[sel.Source.MAP] = upcoming.observation
      if sel.select_limit(proposed, policy).selected_source is sel.Source.MAP:
        self.boundary_id, self.started_ns = encounter.boundary_id, encounter.started_ns
        return proposed
    else:
      # Losing a preview must not offer the still-current approach limit again.
      proposed[sel.Source.MAP] = acc.Observation(acc.ObservationKind.UNKNOWN)
    if held_wins:
      # A losing rival is neither an adoption candidate nor fresh evidence for a held preview.
      return dict.fromkeys(sel.Source, acc.Observation(acc.ObservationKind.UNKNOWN))
    chosen = sel.select_limit(proposed, policy).observation.candidate
    if not encounter.crossed and chosen is not None and chosen.speed_mps == encounter.from_mps:
      encounter.withdrawn = True
      proposed = {source: acc.Observation(acc.ObservationKind.UNKNOWN)
                  if source is sel.Source.MAP or observation.candidate is not None and observation.candidate.speed_mps == encounter.from_mps
                  else observation for source, observation in proposed.items()}
      self.boundary_id, self.started_ns = '', None
    return proposed

  def observe(self, result):
    encounter = self.encounter
    if encounter is None or result.acceptance is None:
      return
    decision = result.acceptance
    accepted = decision.state.accepted
    if encounter.crossed or accepted != encounter.applied_preview:
      encounter.applied_preview = None
    if encounter.retired:
      return
    selected = result.selection.observation.candidate if result.selection is not None else None
    selected = selected or decision.display_candidate
    if selected is not None and selected.speed_mps == encounter.speed_mps and selected.source == 'map':
      if decision.basis in ('driver_reject', 'timeout_reject', 'rejected'):
        encounter.declined = True
      if accepted is not None and accepted.candidate.speed_mps == encounter.speed_mps and decision.state.pending is None:
        if encounter.accepted_ns is None:
          encounter.accepted_ns = accepted.accepted_at_ns
        if (not encounter.crossed and decision.history_write == accepted and decision.basis != 'driver_adopt'):
          encounter.applied_preview = accepted
    elif selected is not None and not encounter.crossed and not encounter.disabled:
      encounter.withdrawn = True
    if not encounter.disabled and encounter.accepted_ns is not None and accepted is not None and accepted.accepted_at_ns != encounter.accepted_ns:
      encounter.withdrawn = True
