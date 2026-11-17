"""Native display projection of saved choice and selfdrived's actual mode receipt."""

from dataclasses import dataclass

from openpilot.starpilot.conditional_mode.effective_status import EffectiveModeAck, LIFETIME_NS, observation
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import MAX_DOCUMENT_BYTES, PreferenceError, decode_preferences
from openpilot.starpilot.saved_source import read_saved


@dataclass(frozen=True)
class ConditionalDisplay:
  choice: ModeChoice
  effective_experimental: bool
  reason: str
  status_code: int
  planner_session: str
  planner_sequence: int


def configured_choice(params) -> ModeChoice | None:
  """Read saved configuration only; it says nothing about active control."""
  try:
    raw, readable = read_saved(params, 'ConditionalModeConfig', MAX_DOCUMENT_BYTES)
    if not readable:
      return None
    choice = ModeChoice.CEM if raw is None else decode_preferences(raw).mode
    return ModeChoice.CEM if choice is ModeChoice.CCM else choice
  except (AttributeError, PreferenceError, OSError, TypeError, ValueError):
    return None


class ConditionalDisplayProjector:
  """Show fresh acknowledgments while the current selfdrive mode agrees."""

  def __init__(self):
    self.drive_id = 0
    self.session: str | None = None
    self.sequence = 0
    self.last: EffectiveModeAck | None = None
    self.retired: tuple[str, ...] = ()

  def reset(self) -> None:
    self.drive_id = 0
    self.session = None
    self.sequence = 0
    self.last = None
    self.retired = ()

  def project(self, state, *, now_ns: int, event_ns: int, selfdrive_ns: int, drive_id: int,
              selfdrive_experimental: bool, selfdrive_enabled: bool, long_active: bool,
              car_valid: bool, system_long: bool) -> ConditionalDisplay | None:
    if type(drive_id) is not int or drive_id <= 0:
      self.reset()
      return None
    if self.drive_id != drive_id:
      self.reset()
      self.drive_id = drive_id
    # A render frame can outlast the control receipt. Its display remains bounded
    # separately; callers issuing commands still use the original receipt expiry.
    display_fresh = (type(event_ns) is int and type(now_ns) is int and
                     0 < event_ns <= now_ns <= event_ns + 200_000_000)
    value = observation(state, event_ns) if state is not None and display_fresh else None
    if (value is None or type(event_ns) is not int or event_ns != value.observed_ns or
        type(selfdrive_ns) is not int or not 0 < selfdrive_ns <= now_ns <= selfdrive_ns + 200_000_000 or
        value.selfdrive_state_ns > selfdrive_ns + LIFETIME_NS or
        type(selfdrive_experimental) is not bool or type(selfdrive_enabled) is not bool or
        type(long_active) is not bool or type(car_valid) is not bool or type(system_long) is not bool or
        value.drive_id != drive_id or value.effective_experimental is not selfdrive_experimental):
      return None
    if value.session in self.retired:
      return None
    if self.session is not None and value.session != self.session:
      self.retired = (*self.retired[-7:], self.session)
      self.session = value.session
      self.sequence = value.sequence
      self.last = value
      return None  # One new-source barrier after selfdrived restart.
    if value.session == self.session:
      if value.sequence < self.sequence or value.sequence == self.sequence and value != self.last:
        return None
      if value.sequence > self.sequence and self.last is not None and value.observed_ns <= self.last.observed_ns:
        return None
    self.session = value.session
    self.sequence = value.sequence
    self.last = value
    if (not value.accepted or not selfdrive_enabled or not long_active or not car_valid or not system_long or
        value.planner_session is None or value.reason is None):
      return None
    return ConditionalDisplay(value.choice, value.effective_experimental, value.reason, value.status_code,
                              value.planner_session, value.planner_sequence)


class ConditionalPerceptionProjector:
  """Display the planner's current scene without treating it as control approval."""

  def __init__(self):
    self.drive_id = 0
    self.session = None
    self.sequence = 0
    self.last = None
    self.retired = ()

  def project(self, state, *, now_ns: int, event_ns: int, drive_id: int,
              choice: ModeChoice | None, lateral_active: bool, selfdrive_enabled: bool,
              car_valid: bool, system_long: bool) -> ConditionalDisplay | None:
    from openpilot.starpilot.conditional_mode.status import observation as scene_observation

    if type(drive_id) is not int or drive_id <= 0:
      self.__init__()
      return None
    if drive_id != self.drive_id:
      self.__init__()
      self.drive_id = drive_id
    fresh = (type(now_ns) is int and type(event_ns) is int and
             drive_id <= event_ns <= now_ns <= event_ns + 200_000_000)
    # Other planner owners can create the envelope before this scene is sampled.
    # Bound both clocks for presentation; command consumers retain proposal expiry.
    try:
      scene_ns = int(state.conditionalMode.observedMonoTime) if state is not None else 0
    except (AttributeError, TypeError, ValueError, OverflowError):
      scene_ns = 0
    scene_fresh = fresh and drive_id <= scene_ns <= now_ns <= scene_ns + 200_000_000
    value = scene_observation(state, scene_ns) if scene_fresh else None
    if (value is None or value.drive_id != drive_id or
        value.choice is not choice or choice not in (ModeChoice.CEM, ModeChoice.CCM) or
        value.status not in ('proposed', 'inactive_axis') or not car_valid or not system_long or
        not (lateral_active or selfdrive_enabled) or value.session in self.retired):
      return None
    if self.session is not None and value.session != self.session:
      self.retired = (*self.retired[-7:], self.session)
      self.session, self.sequence, self.last = value.session, value.sequence, value
      return None
    if value.session == self.session and (
        value.sequence < self.sequence or value.sequence == self.sequence and value != self.last or
        value.sequence > self.sequence and self.last is not None and value.observed_ns <= self.last.observed_ns):
      return None
    self.session, self.sequence, self.last = value.session, value.sequence, value
    return ConditionalDisplay(value.choice, False, value.reason, value.status_code, value.session, value.sequence)
