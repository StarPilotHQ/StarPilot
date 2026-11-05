"""Explicit, immutable observations for the offline onroad presentation.

The producer owns freshness and units. A missing observation is never inferred
from a zero-valued speed or a default-initialized message.
"""

from openpilot.starpilot.ui.onroad_customization import PROFILES, default_document, offset, placement, widget_size

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
import math
from typing import Protocol

from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.weather import WeatherReading
from openpilot.starpilot.ui.large_speed_geometry import ACTION_BOUNDS, LIMIT_RECTS, confirmation_bounds, confirmation_buttons
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice, OnroadAppearance
from openpilot.starpilot.curve_speed.status import CurveObservation
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.ui.conditional_status import ConditionalDisplay
from openpilot.starpilot.ui.traffic_status import TrafficDisplay
from openpilot.starpilot.ui.developer_preview import OnroadVisualPreview
from openpilot.starpilot.ui.navigation_state import NavigationDisplay
from openpilot.starpilot.ui.live_developer_sidebar import DeveloperMetric


TOYOTA_NOTICE_STATUSES = frozenset(('raise_dash', 'release_plus', 'off_hold', 'off_resume'))


class ObservationKind(StrEnum):
  VALID = "valid"
  ABSENT = "absent"
  UNKNOWN = "unknown"
  STALE = "stale"


class AlertSize(StrEnum):
  NONE = "none"
  SMALL = "small"
  MID = "mid"
  FULL = "full"


class SlcActionKind(StrEnum):
  ACCEPT = "accept"
  REJECT = "reject"
  ADOPT = "adopt"


@dataclass(frozen=True)
class SlcUiRequest:
  kind: SlcActionKind
  session_id: str
  decision_id: int
  presentation_id: int
  candidate_speed_mps: float


@dataclass(frozen=True)
class SlcControl:
  request: SlcUiRequest
  label: str
  bounds: tuple[float, float, float, float]
  enabled: bool = True


@dataclass(frozen=True)
class SourceReading:
  source: str
  enabled: bool
  kind: str
  speed_mps: float | None = None


@dataclass(frozen=True)
class SpeedLimitObservation:
  kind: ObservationKind = ObservationKind.UNKNOWN
  source: str = "none"
  speed_limit_mps: float | None = None
  offset_mps: float | None = None
  pending_speed_limit_mps: float | None = None
  effective_cap_mps: float | None = None
  accepted_speed_limit_mps: float | None = None
  accepted_source: str = "none"
  pending_source: str = "none"
  effective_cluster_target_mps: float | None = None
  limiting_max_set: bool = False
  driver_override_active: bool = False
  session_id: str | None = None
  decision_id: int | None = None
  presentation_id: int | None = None
  status: str = ""
  action_enabled: bool = False
  action_sequence_id: int = 0
  action_status: str = ""
  source_readings: tuple[SourceReading, ...] = ()
  accepted_adjusted_limit_mps: float | None = None
  pending_adjusted_limit_mps: float | None = None
  presentation_adjusted_limit_mps: float | None = None
  override_basis: str = 'none'
  retained_override: bool = False
  command_action_sequence_id: int = 0
  command_status: str = ''
  action_feedback: str = ''
  automatic_adoption_pending: bool = False
  pending_since_ns: int = 0
  last_received_speed_limit_mps: float | None = None
  last_received_source: str = 'none'
  last_received_adjusted_limit_mps: float | None = None

  def __post_init__(self) -> None:
    for value in (self.speed_limit_mps, self.offset_mps, self.pending_speed_limit_mps,
                  self.effective_cap_mps, self.accepted_speed_limit_mps, self.effective_cluster_target_mps,
                  self.accepted_adjusted_limit_mps, self.pending_adjusted_limit_mps, self.presentation_adjusted_limit_mps,
                  self.last_received_speed_limit_mps, self.last_received_adjusted_limit_mps):
      if value is not None and not math.isfinite(value):
        raise ValueError("Speed-limit values must be finite")
    for value in (self.accepted_adjusted_limit_mps, self.pending_adjusted_limit_mps, self.presentation_adjusted_limit_mps):
      if value is not None and value <= 0:
        raise ValueError('Adjusted limits must be positive')
    if self.kind != ObservationKind.VALID and self.speed_limit_mps is not None:
      raise ValueError("Only a valid observation can carry a speed limit")
    if self.kind == ObservationKind.VALID and (self.speed_limit_mps is None or self.speed_limit_mps <= 0):
      raise ValueError("A valid observation needs a positive speed limit")


@dataclass(frozen=True)
class OnroadAlert:
  size: AlertSize = AlertSize.NONE
  text1: str = ""
  text2: str = ""
  critical: bool = False
  alert_type: str = ""
  user_prompt: bool = False
  visual_alert: int = 0


def is_lane_change_notice(alert: OnroadAlert) -> bool:
  return (alert.alert_type in ("preLaneChangeLeft/warning", "preLaneChangeRight/warning", "laneChange/warning") and
          alert.size in (AlertSize.SMALL, AlertSize.MID) and not alert.critical and not alert.user_prompt and alert.visual_alert == 0)


def is_personality_notice(alert: OnroadAlert) -> bool:
  return (alert.alert_type == "personalityChanged/warning" and alert.size in (AlertSize.SMALL, AlertSize.MID) and
          not alert.critical and not alert.user_prompt and alert.visual_alert == 0)


@dataclass(frozen=True)
class BorderSignals:
  left_blinker: bool
  right_blinker: bool
  left_blindspot: bool
  right_blindspot: bool


@dataclass(frozen=True)
class ConditionalLead:
  distance_m: float
  speed_mps: float

  def __post_init__(self) -> None:
    if (not math.isfinite(self.distance_m) or not 0 < self.distance_m <= 500 or
        not math.isfinite(self.speed_mps) or not 0 <= self.speed_mps <= 100):
      raise ValueError('Conditional lead metrics must be finite and physically bounded')


def conditional_lead_from_message(radar) -> ConditionalLead | None:
  """The caller supplies only a current display radar observation."""
  try:
    lead = radar.leadOne
    if lead.status is not True:
      return None
    return ConditionalLead(float(lead.dRel), float(lead.vLead))
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None


@dataclass(frozen=True)
class OnroadState:
  engaged: bool
  camera_available: bool
  speed_mps: float | None
  cruise_kph: float | None
  speed_limit: SpeedLimitObservation
  appearance: OnroadAppearance = field(default_factory=OnroadAppearance)
  alert: OnroadAlert = OnroadAlert()
  metric: bool = False
  show_slc_offset: bool = True
  experimental_available: bool = False
  experimental_enabled: bool = False
  torque_utilization: float = 0.0
  personality: int = 1
  lead_detected: bool = False
  gas_pressed_override: bool = False
  right_hand_drive: bool = False
  switchback_mode: bool = False
  traffic_mode: bool = False
  traffic_selected: bool = False
  traffic_display: TrafficDisplay | None = None
  aol_latched: bool | None = None
  lateral_active: bool = False
  longitudinal_active: bool = False
  longitudinal_overridden: bool = False
  driver_override_active: bool = False
  stock_cruise_active: bool = False
  slc_system_long_available: bool = False
  curve: CurveObservation | None = None
  show_curve_status: bool = False
  conditional_configured: ModeChoice | None = None
  navigation: NavigationDisplay | None = None
  conditional_perception: ConditionalDisplay | None = None
  conditional_effective: ConditionalDisplay | None = None
  conditional_lead: ConditionalLead | None = None
  visual_preview: OnroadVisualPreview | None = None
  border_signals: BorderSignals | None = None
  stopped_duration_s: int | None = None
  reverse_driver_camera: bool = False
  reversing: bool = False
  stock_confidence_source_fresh: bool = False
  stock_confidence_source_stamp_ns: int | None = None
  stock_confidence_drive_frame: int | None = None
  model_confidence: float | None = None
  lead_indicator_source_fresh: bool = False
  customization: dict = field(default_factory=default_document)
  wheel_acceleration: float | None = None
  torque_source_available: bool = True
  torque_drive_frame: int | None = None
  experimental_action_token: str = ""
  drive_frame: int | None = None
  observed_ns: int = 0
  viewport_width: float = 2160.0
  developer_metrics: tuple[DeveloperMetric, ...] = ()
  slc_prompt_open: bool = False
  quick_select_open: bool = False
  slc_wheel_confirmation_available: bool = False
  toyota_cruise_status: str = ''
  toyota_cruise_target_kph: float | None = None

  torque_is_demand: bool = False

  @property
  def cruise_active(self) -> bool:
    return self.longitudinal_active or self.longitudinal_overridden or self.stock_cruise_active

  stop_sign_observed_ns: int = 0
  weather_location: tuple[float, float] | None = None
  weather: WeatherReading | None = None

  def __post_init__(self) -> None:
    if self.speed_mps is not None and (not math.isfinite(self.speed_mps) or self.speed_mps < 0):
      raise ValueError("Speed must be finite and nonnegative")
    if self.cruise_kph is not None and (not math.isfinite(self.cruise_kph) or self.cruise_kph < 0):
      raise ValueError("Cruise speed must be finite and nonnegative")
    if not math.isfinite(self.torque_utilization) or not -1.0 <= self.torque_utilization <= 1.0:
      raise ValueError("Torque utilization must be normalized")
    if type(self.torque_is_demand) is not bool:
      raise ValueError("Steering demand kind must be Boolean")
    if not 0 <= self.personality <= 2:
      raise ValueError("Personality must be aggressive, standard, or relaxed")
    if self.stopped_duration_s is not None and (type(self.stopped_duration_s) is not int or self.stopped_duration_s <= 0):
      raise ValueError("Stopped duration must be a positive whole second")
    if self.model_confidence is not None and (not math.isfinite(self.model_confidence) or
                                               not 0 <= self.model_confidence <= 1):
      raise ValueError("Model confidence must be normalized")


@dataclass(frozen=True)
class OnroadRequest:
  kind: str
  value: bool | None = None
  action_token: str = ""


def manual_slc_confirmation(profile: Profile, state: OnroadState) -> bool:
  obs = state.speed_limit
  widget = 'speed_limit' if profile == Profile.COMPACT else 'cruise_limits'
  return bool((state.longitudinal_active or state.longitudinal_overridden) and
              state.alert.size == AlertSize.NONE and state.toyota_cruise_status not in TOYOTA_NOTICE_STATUSES and
              (profile != Profile.COMPACT or not state.reverse_driver_camera and state.appearance.camera_view != CameraViewChoice.DRIVER) and
              (obs.kind == ObservationKind.VALID or obs.presentation_id == 0) and
              obs.action_enabled and not obs.automatic_adoption_pending and
              obs.pending_speed_limit_mps is not None and obs.pending_speed_limit_mps > 0 and
              obs.session_id and obs.decision_id and obs.decision_id > 0 and
              obs.presentation_id is not None and obs.presentation_id >= 0 and
              placement(state.customization, profile, widget)['enabled'] and
              placement(state.customization, profile, 'speed_limit_actions')['enabled'])


def slc_accept_available(state: OnroadState) -> bool:
  obs = state.speed_limit
  return bool(obs.action_enabled and obs.kind == ObservationKind.VALID and obs.presentation_id and obs.presentation_id > 0)


def slc_action_bounds(profile: Profile, state: OnroadState):
  dx, dy = offset(state.customization, profile, 'speed_limit_actions')
  return tuple((left + dx, top + dy, right + dx, bottom + dy) for left, top, right, bottom in ACTION_BOUNDS[profile])


def slc_controls(profile: Profile, state: OnroadState) -> tuple[SlcControl, ...]:
  if manual_slc_confirmation(profile, state) and state.slc_prompt_open:
    obs = state.speed_limit
    bounds = speed_limit_bounds(profile, state)
    if bounds is None:
      return ()
    return tuple(SlcControl(SlcUiRequest(kind, obs.session_id or '', obs.decision_id or 0,
                                        obs.presentation_id or 0, obs.pending_speed_limit_mps or 0), label, area,
                            kind == SlcActionKind.REJECT or slc_accept_available(state))
                 for kind, label, area in zip((SlcActionKind.ACCEPT, SlcActionKind.REJECT), ('Accept', 'Reject'),
                                             confirmation_buttons(profile, bounds), strict=True))
  return ()


def slc_action_occludes(profile: Profile, state: OnroadState, bounds) -> bool:
  if manual_slc_confirmation(profile, state):
    area = speed_limit_bounds(profile, state)
    return area is not None and area[0] < bounds[2] and bounds[0] < area[2] and area[1] < bounds[3] and bounds[1] < area[3]
  notice = state.toyota_cruise_status in TOYOTA_NOTICE_STATUSES
  if state.alert.size != AlertSize.NONE or not notice:
    return False
  first, last = slc_action_bounds(profile, state)
  left, top, right, bottom = bounds
  return first[0] < right and left < last[2] and first[1] < bottom and top < last[3]


def _inside_widget(x, y, state, widget, bounds):
  if not placement(state.customization, Profile.LARGE, widget)["enabled"]:
    return False
  dx, dy = offset(state.customization, Profile.LARGE, widget)
  left, top, right, bottom = bounds
  if widget == "steering_wheel":
    left += state.viewport_width - 1860
    size, _ = widget_size(state.customization, Profile.LARGE, widget)
    right, bottom = left + size, top + size
  return left + dx <= x <= right + dx and top + dy <= y <= bottom + dy


def speed_limit_bounds(profile: Profile, state: OnroadState) -> tuple[float, float, float, float] | None:
  from openpilot.starpilot.ui.unified_speed_presentation import displayed_limit_mps
  if (displayed_limit_mps(state.speed_limit) is None or
      not state.appearance.show_speed_limit_sign and not state.speed_limit.action_enabled):
    return None
  widget = 'speed_limit' if profile == Profile.COMPACT else 'cruise_limits'
  saved = placement(state.customization, profile, widget)
  if not saved['enabled']:
    return None
  if manual_slc_confirmation(profile, state):
    return confirmation_bounds(profile, saved['x'], saved['y'], opened=state.slc_prompt_open)
  x, y, width, height = LIMIT_RECTS[profile]
  dx, dy = offset(state.customization, profile, widget)
  return x + dx - 12, y + dy - 12, x + dx + width + 12, y + dy + height + 8


def personality_bounds(profile: Profile, state: OnroadState) -> tuple[float, float, float, float] | None:
  position = placement(state.customization, profile, 'following_distance')
  if not position['enabled'] or state.appearance.camera_view == CameraViewChoice.DRIVER or state.reverse_driver_camera:
    return None
  width, height = widget_size(state.customization, profile, 'following_distance')
  x, y = position['x'], position['y']
  if profile == Profile.LARGE and state.right_hand_drive:
    widgets = PROFILES['large']['widgets']
    monitor = placement(state.customization, profile, 'driver_monitor')
    if all(position[axis] == widgets['following_distance']['default'][axis] and
           monitor[axis] == widgets['driver_monitor']['default'][axis] for axis in ('x', 'y')):
      bounds = PROFILES['large']['bounds']
      monitor_width, _ = widget_size(state.customization, profile, 'driver_monitor')
      x = 2 * bounds['x'] + bounds['width'] - monitor['x'] - monitor_width - 32 - width
  return x, y, x + width, y + height


class OnroadInput:
  """The onroad wheel emits intent only; the supplied state must be refreshed."""

  def __init__(self, emit: Callable[[OnroadRequest | SlcUiRequest], None], profile: Profile = Profile.LARGE, slop: float = 5):
    self.emit = emit
    self.profile = profile
    self.slop = slop
    self._press: tuple[float, float, bool, str] | None = None
    self._slc_press: SlcControl | None = None
    self._sign_press: tuple[tuple, tuple] | None = None
    self._personality_press: tuple[float, float, int | None, tuple] | None = None
    self._prompt: tuple | None = None

  def _pending_context(self, state: OnroadState) -> tuple | None:
    obs = state.speed_limit
    if manual_slc_confirmation(self.profile, state):
      return obs.session_id, obs.decision_id, obs.pending_speed_limit_mps
    return None

  def _personality_target(self, state: OnroadState) -> tuple | None:
    if (not state.camera_available or state.quick_select_open or
        state.alert.size != AlertSize.NONE and not is_personality_notice(state.alert) or
        self.profile == Profile.COMPACT and state.reversing and state.appearance.camera_view != CameraViewChoice.NONE):
      return None
    return personality_bounds(self.profile, state)

  def present(self, state: OnroadState) -> OnroadState:
    if self._prompt != self._pending_context(state):
      self._prompt = None
    return replace(state, slc_prompt_open=self._prompt is not None)

  @property
  def pressed_action(self):
    return self._slc_press.request if self._slc_press and self._slc_press.enabled else None

  @property
  def claimed(self) -> bool:
    return self._press is not None or self._slc_press is not None or self._sign_press is not None or self._personality_press is not None

  def press(self, x: float, y: float, state: OnroadState) -> None:
    self.cancel()
    state = self.present(state)
    controls = slc_controls(self.profile, state)
    for control in controls:
      left, top, right, bottom = control.bounds
      if left <= x <= right and top <= y <= bottom:
        self._slc_press = control
        return
    bounds, context = speed_limit_bounds(self.profile, state), self._pending_context(state)
    if bounds is not None and context is not None and bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
      self._sign_press = context, bounds
      return
    bounds = self._personality_target(state)
    if bounds is not None and bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
      self._personality_press = x, y, state.drive_frame, bounds
      return
    # Only the large-profile wheel is an experimental-mode touch target.
    if self.profile != Profile.LARGE:
      self.cancel()
      return
    inside = _inside_widget(x, y, state, "steering_wheel", (1588, 75, 1780, 267))
    self._press = (x, y, state.experimental_enabled, state.experimental_action_token) if inside and state.experimental_available else None

  def move(self, x: float, y: float, state: OnroadState) -> None:
    state = self.present(state)
    if self._personality_press:
      px, py, drive_frame, original_bounds = self._personality_press
      bounds = self._personality_target(state)
      if (abs(x - px) > self.slop or abs(y - py) > self.slop or drive_frame != state.drive_frame or bounds != original_bounds or
          bounds is None or not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])):
        self.cancel()
      return
    if self._sign_press:
      context, original_bounds = self._sign_press
      bounds = speed_limit_bounds(self.profile, state)
      if (context != self._pending_context(state) or bounds is None or bounds != original_bounds or
          not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])):
        self.cancel()
      return
    if self._slc_press:
      current = next((item for item in slc_controls(self.profile, state) if item == self._slc_press), None)
      in_control = current is not None and current.bounds[0] <= x <= current.bounds[2] and current.bounds[1] <= y <= current.bounds[3]
      if not in_control:
        self.cancel()
      return
    if self._press:
      px, py, previous, token = self._press
      if (abs(x - px) > self.slop or abs(y - py) > self.slop or not (_inside_widget(x, y, state, "steering_wheel", (1588, 75, 1780, 267)))
          or previous != state.experimental_enabled or token != state.experimental_action_token or not state.experimental_available):
        self.cancel()

  def release(self, x: float, y: float, state: OnroadState) -> None:
    self.move(x, y, state)
    if self._slc_press and self._slc_press.enabled:
      self.emit(self._slc_press.request)
      self._prompt = None
    if self._sign_press:
      self._prompt = self._sign_press[0]
    if self._personality_press:
      self.emit(OnroadRequest('cycle_personality'))
    if self._press:
      self.emit(OnroadRequest("set_experimental", not self._press[2], self._press[3]))
    self.cancel()

  def cancel(self, *, close_prompt: bool = False) -> None:
    self._press = None
    self._slc_press = None
    self._sign_press = None
    self._personality_press = None
    if close_prompt:
      self._prompt = None


class SlcStateMessage(Protocol):
  """The small UI-facing subset of the planned SLC schema message."""

  observationKind: str
  source: str
  speedLimit: float
  offset: float
  pendingSpeedLimit: float
  effectiveCap: float
  acceptedSpeedLimit: float
  hasPending: bool
  hasCeiling: bool
  hasAccepted: bool
  sessionId: str
  decisionId: int
  presentationId: int
  status: str
  enabled: bool
  actionSequenceId: int
  actionStatus: str


def source_readings_from_message(message):
  rows = []
  for row in getattr(message, 'sourceReadings', ()):
    source, kind = str(row.source), str(row.observationKind)
    if source not in ('dashboard', 'map', 'vision', 'online') or source in {item.source for item in rows}:
      continue
    value = float(row.speedLimit) if kind == 'valid' else None
    if value is not None and (not math.isfinite(value) or value <= 0):
      kind, value = 'unknown', None
    rows.append(SourceReading(source, bool(row.enabled), kind, value))
  return tuple(rows)


def speed_limit_from_message(message: SlcStateMessage | None) -> SpeedLimitObservation:
  """Map explicit SLC observation semantics without assuming a live service.

  The caller checks message freshness and passes ``None`` for missing or stale
  transport. Native numeric defaults cannot become a valid speed limit here.
  """
  if message is None:
    return SpeedLimitObservation(kind=ObservationKind.UNKNOWN)
  try:
    kind = ObservationKind(message.observationKind)
  except (ValueError, AttributeError):
    return SpeedLimitObservation(kind=ObservationKind.UNKNOWN, status="Invalid SLC observation kind")
  if kind != ObservationKind.VALID and not (message.hasPending or getattr(message, 'lastReceivedSpeedLimit', 0) > 0 or
      getattr(message, 'hasAccepted', False) and getattr(message, 'hasEffectiveClusterTarget', False)):
    return SpeedLimitObservation(kind=kind, source=message.source, status=message.status,
                                 source_readings=source_readings_from_message(message))
  try:
    live = kind == ObservationKind.VALID
    return SpeedLimitObservation(kind=kind, source=message.source, speed_limit_mps=message.speedLimit if live else None,
                                 source_readings=source_readings_from_message(message),
                                 offset_mps=message.offset,
                                 pending_speed_limit_mps=message.pendingSpeedLimit if message.hasPending else None,
                                 effective_cap_mps=message.effectiveCap if message.hasCeiling else None,
                                 accepted_speed_limit_mps=message.acceptedSpeedLimit if message.hasAccepted else None,
                                 accepted_source=str(getattr(message, "acceptedSource", "none")) or "none",
                                 pending_source=str(getattr(message, "pendingSource", "none")) or "none",
                                 effective_cluster_target_mps=(getattr(message, "effectiveClusterTarget", None)
                                   if getattr(message, "hasEffectiveClusterTarget", False) else None),
                                 limiting_max_set=bool(getattr(message, "isLimitingMaxSet", False)),
                                 driver_override_active=bool(getattr(message, "driverOverrideActive", False)),
                                 session_id=message.sessionId, decision_id=message.decisionId if live or message.hasPending else None,
                                 presentation_id=message.presentationId if live else 0 if message.hasPending else None, status=message.status,
                                 action_enabled=bool(getattr(message, "enabled", False)),
                                 action_sequence_id=int(getattr(message, "actionSequenceId", 0)),
                                 action_status=str(getattr(message, "actionStatus", "")),
                                 automatic_adoption_pending=bool(getattr(message, 'automaticAdoptionPending', False)),
                                 pending_since_ns=int(getattr(message, 'pendingSinceMonoTime', 0)),
                                 last_received_speed_limit_mps=getattr(message, 'lastReceivedSpeedLimit', 0) or None,
                                 last_received_source=str(getattr(message, 'lastReceivedSource', 'none')) or 'none',
                                 last_received_adjusted_limit_mps=getattr(message, 'lastReceivedAdjustedLimit', 0) or None,
                                 accepted_adjusted_limit_mps=(getattr(message, 'acceptedAdjustedLimit', None)
                                   if getattr(message, 'hasAcceptedAdjustedLimit', False) else None),
                                 pending_adjusted_limit_mps=(getattr(message, 'pendingAdjustedLimit', None)
                                   if getattr(message, 'hasPendingAdjustedLimit', False) else None),
                                 presentation_adjusted_limit_mps=(getattr(message, 'presentationAdjustedLimit', None)
                                   if getattr(message, 'hasPresentationAdjustedLimit', False) else None),
                                 override_basis=str(getattr(message, 'overrideBasis', 'none')),
                                 retained_override=bool(getattr(message, 'retainedOverride', False)),
                                 command_action_sequence_id=int(getattr(message, 'commandActionSequenceId', 0)),
                                 command_status=str(getattr(message, 'commandStatus', '')))
  except (ValueError, TypeError, AttributeError, OverflowError):
    return SpeedLimitObservation(kind=ObservationKind.UNKNOWN, source=message.source,
                                 status="Invalid SLC numeric observation")
