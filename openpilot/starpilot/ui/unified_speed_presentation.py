"""Set speed and source limit, retaining an accepted controller target through source gaps."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class UnifiedSpeedPresentation:
  mode: str
  max_text: str
  posted_text: str
  offset_text: str | None
  unit: str
  source: str
  pending: bool
  active_side: str
  limit_text: str
  status: str
  proposal_text: str
  override_text: str | None
  slc_target_text: str | None = None
  posted_retained: bool = False
  limit_in_use: bool = False
  set_limited: bool = False
  show_last: bool = False

  @property
  def max_status(self) -> str:
    return 'Not set' if self.max_text == '–' else 'Set speed'

  @property
  def limit_status(self) -> str:
    return self.status


def positive(value):
  return value is not None and math.isfinite(value) and value > 0


def retaining_limit(observation):
  return (str(observation.kind) != 'valid' and bool(getattr(observation, 'session_id', None)) and
          (positive(observation.last_received_speed_limit_mps) or observation.action_enabled and
           positive(observation.accepted_speed_limit_mps) and positive(observation.effective_cluster_target_mps)))


def displayed_limit_mps(observation):
  if str(observation.kind) == 'valid':
    return observation.speed_limit_mps
  if positive(observation.last_received_speed_limit_mps):
    return observation.last_received_speed_limit_mps
  return observation.accepted_speed_limit_mps if retaining_limit(observation) else None


def displayed_limit_source(observation):
  if str(observation.kind) == 'valid':
    return observation.source
  return observation.last_received_source if positive(observation.last_received_speed_limit_mps) else observation.accepted_source


def displayed_adjusted_limit_mps(observation):
  posted = displayed_limit_mps(observation)
  if not positive(posted):
    return None
  if str(observation.kind) != 'valid' and positive(observation.last_received_adjusted_limit_mps):
    return observation.last_received_adjusted_limit_mps
  if str(observation.kind) == 'valid':
    for value in (getattr(observation, 'presentation_adjusted_limit_mps', None), getattr(observation, 'pending_adjusted_limit_mps', None)):
      if positive(value):
        return value
  if observation.accepted_speed_limit_mps == posted:
    for value in (getattr(observation, 'accepted_adjusted_limit_mps', None), observation.effective_cluster_target_mps):
      if positive(value):
        return value
  return posted + (observation.offset_mps or 0)


def resolve_unified_speed(state, *, confirmation=False) -> UnifiedSpeedPresentation:
  obs = state.speed_limit
  factor = 3.6 if state.metric else 2.2369362921
  def speed(value):
    return str(round(value * factor)) if positive(value) else '–'
  posted = displayed_limit_mps(obs)
  target = displayed_adjusted_limit_mps(obs)
  source = displayed_limit_source(obs)
  active_target = obs.effective_cluster_target_mps
  pending = positive(obs.pending_speed_limit_mps)
  slc_enabled = (state.longitudinal_active and not state.longitudinal_overridden and obs.action_enabled and
                 not obs.driver_override_active)
  posted_retained = positive(posted) and (str(obs.kind) != 'valid' or obs.status == 'display_hold')
  show_last = posted_retained or obs.sign_recovering
  # A live controller may keep its accepted target after the source stops reporting.
  retained_target = bool(show_last and slc_enabled and not pending and obs.status != 'display_hold' and obs.session_id and
                         obs.accepted_source not in ('', 'none', 'unknown') and positive(obs.effective_cap_mps) and
                         positive(obs.accepted_speed_limit_mps) and positive(obs.accepted_adjusted_limit_mps) and
                         positive(active_target) and math.isclose(active_target, obs.accepted_adjusted_limit_mps, abs_tol=.001))
  if retained_target:
    posted, target, source = obs.accepted_speed_limit_mps, active_target, obs.accepted_source
    show_last = False
  slc_enabled = slc_enabled and (obs.source != 'unknown' or retained_target)
  slc_limiting = slc_enabled and obs.limiting_max_set
  if confirmation and pending:
    posted = obs.pending_speed_limit_mps
    posted_retained = False
    target = obs.pending_adjusted_limit_mps
    if not positive(target):
      target = posted + (obs.offset_mps or 0)
  show_max = state.customization.get('showMaxSpeed', True)
  has_slc = positive(posted) or str(obs.kind) == 'stale'
  mode = 'split' if show_max and has_slc else 'limit_only' if has_slc else 'max_only' if show_max else 'hidden'
  offset = None
  if state.show_slc_offset and positive(target) and positive(posted):
    adjustment = round(target * factor) - round(posted * factor)
    if adjustment:
      offset = f'{adjustment:+d}'
  status = 'Cruise off'
  if (confirmation and pending and not obs.automatic_adoption_pending and
      obs.action_enabled and obs.presentation_id and obs.presentation_id > 0):
    status = 'Confirm speed limit'
  elif (getattr(obs, 'status', '') == 'display_hold' or pending and obs.presentation_id == 0 or
      retaining_limit(obs) and not retained_target):
    status = 'Unavailable'
  elif not obs.action_enabled:
    status = 'Display only'
  elif state.longitudinal_overridden:
    status = 'Using accelerator'
  elif obs.driver_override_active:
    status = 'Set-speed override'
  elif pending:
    status = 'Adopting limit' if getattr(obs, 'automatic_adoption_pending', False) else 'Confirm speed limit'
  elif state.cruise_active and slc_limiting:
    status = 'Using speed limit'
  elif state.cruise_active:
    status = 'Using set speed'
  max_text = speed(state.cruise_kph / 3.6) if positive(state.cruise_kph) else '–'
  if confirmation and pending and obs.pending_source not in ('', 'none'):
    source = obs.pending_source
  proposal = None
  if pending:
    proposal = obs.pending_adjusted_limit_mps or (
      target if obs.pending_speed_limit_mps == posted else obs.pending_speed_limit_mps + (obs.offset_mps or 0))
  # A matching displayed number alone does not establish that this source is controlling.
  accepted_display = bool(not pending and not show_last and
                          positive(posted) and positive(target) and positive(active_target) and
                          positive(obs.accepted_speed_limit_mps) and source == obs.accepted_source and
                          math.isclose(posted, obs.accepted_speed_limit_mps, abs_tol=.001) and
                          math.isclose(target, active_target, abs_tol=.001))
  limit_in_use = slc_limiting and accepted_display
  # Compare cluster values in displayed units; equal-looking speeds need no instruction.
  set_limited = bool(slc_enabled and accepted_display and not obs.limiting_max_set and
                     positive(obs.effective_cap_mps) and positive(state.cruise_kph) and
                     round(active_target * factor) > round(state.cruise_kph / 3.6 * factor))
  return UnifiedSpeedPresentation(mode, max_text, speed(posted), offset, 'km/h' if state.metric else 'mph',
                                  source, pending,
                                  ('limit' if slc_limiting else 'max') if state.cruise_active else 'none',
                                  speed(target), status,
                                  speed(proposal),
                                  speed(obs.effective_cluster_target_mps) if obs.driver_override_active and
                                  obs.action_enabled and state.longitudinal_active and not state.longitudinal_overridden and
                                  obs.override_basis != 'pedal' and
                                  positive(obs.effective_cluster_target_mps) else None,
                                  speed(active_target) if slc_limiting and positive(active_target) else None,
                                  posted_retained, limit_in_use, set_limited, show_last)
