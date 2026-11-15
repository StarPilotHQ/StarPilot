"""Set speed and the one speed limit the sign shows.

While SLC is on, including while the driver is on the gas, that is the accepted limit it uses;
otherwise it is the latest reading.
"""

from dataclasses import dataclass
import math

from openpilot.starpilot.speed_limits.speed_domain import offset_for_limit


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
  status: str
  proposal_text: str
  override_text: str | None
  posted_retained: bool = False
  limit_in_use: bool = False
  show_last: bool = False
  offset_configured: bool = False

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
  active_target = obs.effective_cluster_target_mps
  pending = positive(obs.pending_speed_limit_mps)
  slc_on = (state.longitudinal_active or state.longitudinal_overridden) and obs.action_enabled
  slc_engaged = slc_on and not state.longitudinal_overridden
  # SLC shows the limit it uses, even while the driver is on the gas.
  # A rejected reading or a source gap leaves that limit in place.
  using_accepted = bool(slc_on and not pending and obs.status != 'display_hold' and
                        obs.accepted_source not in ('', 'none', 'unknown') and
                        positive(obs.accepted_speed_limit_mps) and positive(obs.accepted_adjusted_limit_mps))
  if using_accepted:
    posted, target, source = obs.accepted_speed_limit_mps, obs.accepted_adjusted_limit_mps, obs.accepted_source
  else:
    posted, target, source = displayed_limit_mps(obs), displayed_adjusted_limit_mps(obs), displayed_limit_source(obs)
  posted_retained = positive(posted) and (str(obs.kind) != 'valid' or obs.status == 'display_hold')
  show_last = (posted_retained or obs.sign_recovering) and not using_accepted
  # SLC can only limit to a limit it has accepted; old replayed routes carry none.
  slc_limiting = (slc_engaged and not obs.driver_override_active and obs.limiting_max_set and
                  positive(obs.accepted_speed_limit_mps))
  # The sign turns green only while the controller's target is this sign's limit plus offset.
  limit_in_use = bool(slc_limiting and using_accepted and positive(active_target) and
                      math.isclose(active_target, target, abs_tol=.001))
  if confirmation and pending:
    posted = obs.pending_speed_limit_mps
    posted_retained = False
    target = obs.pending_adjusted_limit_mps
    if not positive(target):
      target = posted + (obs.offset_mps or 0)
    if obs.pending_source not in ('', 'none'):
      source = obs.pending_source
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
      retaining_limit(obs) and not using_accepted):
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
  proposal = None
  if pending:
    proposal = obs.pending_adjusted_limit_mps or (
      target if obs.pending_speed_limit_mps == posted else obs.pending_speed_limit_mps + (obs.offset_mps or 0))
  # The speed the driver chose over the limit; a pedal press alone is not shown.
  override = (speed(active_target) if slc_engaged and obs.driver_override_active and obs.override_basis != 'pedal' and
              positive(active_target) else None)
  return UnifiedSpeedPresentation(
    mode=mode, max_text=speed(state.cruise_kph / 3.6) if positive(state.cruise_kph) else '–',
    posted_text=speed(posted), offset_text=offset, unit='km/h' if state.metric else 'mph', source=source,
    pending=pending, active_side=('limit' if slc_limiting else 'max') if state.cruise_active else 'none',
    status=status, proposal_text=speed(proposal), override_text=override, posted_retained=posted_retained,
    limit_in_use=limit_in_use, show_last=show_last,
    offset_configured=state.slc_offset_schedule is not None and
                      offset_for_limit(posted, state.slc_offset_schedule) not in (None, 0.))
