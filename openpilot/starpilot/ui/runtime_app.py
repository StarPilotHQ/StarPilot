"""StarPilot shell mounted in the existing large and compact UI stacks."""

from __future__ import annotations

from openpilot.starpilot.ui.lane_change_feature import confirm_question as lane_change_question

from collections import deque
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from html import escape, unescape
import os
import math
from pathlib import Path
import time
import threading
from typing import Any, cast
from collections.abc import Callable

import pyray as rl
import openpilot.cereal.messaging as messaging
from opendbc.car.structs import car as car_schema

from openpilot.selfdrive.ui.layouts.main import MainLayout, MainState
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.ui_state import device as native_device, ui_state
from openpilot.starpilot.ui.vehicle_bool import VEHICLE_BOOL_KEYS, confirmation_question as vehicle_question
from openpilot.starpilot.ui.vehicle_large import SELECTION_PAGE, VehicleLarge
from openpilot.starpilot.ui.clip import placed_at
from openpilot.starpilot.ui.device_state import DeviceAction, DeviceRequest
from openpilot.starpilot.drive_state.owner import DriveStateOwner, Rejected as DriveStateRejected
from openpilot.starpilot.drive_state.evidence import PhysicalSource
from openpilot.starpilot.drive_state.control import DriveStateControl, LABELS
from openpilot.starpilot.storage import starpilot_storage_root
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.conditional_feature import confirmation_question as conditional_question
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.display_owner import AUTO_PREFIX as DISPLAY_AUTO_PREFIX, DisplayOwner
from openpilot.starpilot.ui.display_preferences import MASTER as DISPLAY_MASTER
from openpilot.starpilot.ui.developer_preview import parse_flags, preview_at
from openpilot.starpilot.ui.power_owner import POWER_KEYS, PowerOwner, confirm_question, power_row_change
from openpilot.starpilot.ui.pip_owner import FORMAT_PREFIX as PIP_FORMAT_PREFIX, PiPOwner, RESET as PIP_RESET
from openpilot.starpilot.ui.pip_preferences import read_pip
from openpilot.starpilot.ui.pip_render import PiPRenderer
from openpilot.starpilot.ui.pip_sidecam import Signals, Rect
from openpilot.starpilot.ui.onroad_customization import CAMERA_WIDGETS, fit_to_road, placement, widget_size
from openpilot.starpilot.ui.layout_preview_sidebar import SIDEBAR_WIDTH
from openpilot.starpilot.ui.pip_warning import PiPWarningSource
from openpilot.starpilot.spot_monitor.preferences import read_preferences as read_vasm_preferences
from openpilot.starpilot.speed_limits.vision.observation import clock_pair_ns
from openpilot.starpilot.ui.sounds_owner import AUTO_PREFIX as SOUND_AUTO_PREFIX, SoundsOwner
from openpilot.starpilot.ui.feature_settings_state import (
  FeatureInput, FeaturePage, FeatureRow, FeatureSettingsRequest, FeatureUiAction, row_change,
  feature_scroll, feature_parent_page, feature_parent_title, value_request, value_editor_state,
  SOUND_PRESETS as SOUND_PRESETS, SYSTEM_PRESETS, sound_editor_state,
)
from openpilot.starpilot.ui.lane_change_feature import KEYS as LANE_CHANGE_KEYS, RESET as LANE_CHANGE_RESET
from openpilot.starpilot.models.runtime import ModelStatusSource
from openpilot.starpilot.models.status import project_status
from openpilot.starpilot.ui.models_state import model_page, model_profile_choices, model_profile_request, home_model_label
from openpilot.starpilot.ui.maps_state import MapStatusSource
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner, default_owner
from openpilot.starpilot.ui.galaxy_access import GalaxyAccessFlow
from openpilot.starpilot.ui.home_state import HomeActionKind
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile, default_font_directory
from openpilot.starpilot.ui.startup import slc_action_transport_enabled, validate_artwork, validate_fonts
from openpilot.starpilot.speed_limits.vision_gate import diagnostic_choice_enabled
from openpilot.starpilot.ui.bluetooth_status import BluetoothStatusSource
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter, current_message, display_message
from openpilot.starpilot.ui.runtime_software import deliver_software
from openpilot.starpilot.ui.network_panel import NetworkPanelBridge
from openpilot.starpilot.ui.slc_action_dispatch import SlcActionDispatcher
from openpilot.starpilot.ui.onroad_state import AlertSize, BorderSignals, OnroadRequest, OnroadState, SlcUiRequest, is_personality_notice
from openpilot.starpilot.ui.onroad_favorites import OnroadFavorites
from openpilot.starpilot.ui.onroad_dm import DriverMonitorLayer
from openpilot.starpilot.favorites.owner import FavoritesOwner
from openpilot.starpilot.controllers.cruise_action import CruiseActionPublisher, ui_authority as cruise_action_authority
from openpilot.starpilot.controllers.wheel_actions import WheelConsumer
from openpilot.starpilot.system.starport.projection_bookmark import BookmarkReceiver
from openpilot.starpilot.system.starport.projection_slc_action import SlcActionReceiver
from openpilot.starpilot.controllers.mode_actions import ModeActionPublisher, producer_available, authority as mode_action_authority
from openpilot.starpilot.favorites.actions import (BOOKMARK, CYCLE_PERSONALITY, EXPERIMENTAL, INCREASE_SPEED, DECREASE_SPEED, SET_SPEED,
                                                  FORCE_COAST, PULSE_GLIDE, DISENGAGE, TRAFFIC, SWITCHBACK, SCREEN_OFF, mapped_actions)
from openpilot.starpilot.favorites.state import FavoriteAction
from openpilot.starpilot.favorites.layout_actions import layout_actions
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.feature_runtime import enabled as feature_enabled
from openpilot.starpilot.conditional_mode.preferences import PreferenceError, SavedPreferences, decode_preferences
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.ui_action import ConditionalUiActionOwner
from openpilot.starpilot.ui.settings_state import Destination, SettingsAction, SettingsActionKind
from openpilot.starpilot.ui.shell import ShellInput, ShellMode, ShellRequest, ShellSnapshot, ShellView
from openpilot.starpilot.ui.software_state import DownloadLabel, SoftwareAction, SoftwareRequest
from openpilot.starpilot.ui.toggles_state import Personality, ToggleKey, ToggleRequest
from openpilot.system.ui.lib.application import MouseEvent, MousePos, gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import button_item, text_item
from openpilot.starpilot.ui.compact_settings_page import StarCompactSettings

NATIVE_SETTINGS_PANELS = (Destination.DEVICE, Destination.SOFTWARE, Destination.BLUETOOTH, Destination.TOGGLES, Destination.DEVELOPER)
_FEATURE_SETTINGS_PANES = {
  Destination.DRIVING_CONTROLS: ("features", "feature", "features"),
  Destination.SOUNDS: ("sounds", "sounds", "sounds"),
  Destination.APPEARANCE: ("appearance", "appearance", "appearance"),
  Destination.SYSTEM: ("display", "display", "display"),
  Destination.DRIVING_MODEL: ("models", "model", "models"),
}


def validate_runtime_fonts(profile: Profile) -> None:
  """Validate bundled fonts or an explicit override before acquiring resources."""
  validate_fonts(profile, os.environ.get("STARPILOT_UI_FONT_DIR"))


def validate_runtime_assets() -> None:
  """Reject missing or changed bundled artwork before creating app widgets."""
  validate_artwork()


def render_stock_confidence(camera_owner: Any, rect: rl.Rectangle, state: OnroadState) -> None:
  camera_owner.render_stock_confidence_layer(rect, lateral_active=state.lateral_active)


@lru_cache(maxsize=1)
def _slc_action_publisher() -> messaging.PubMaster:
  """One producer for state-bound planner actions from the UI."""
  return messaging.PubMaster(["slcAction"])


def _galaxy_access_owner() -> GalaxyAccessOwner:
  return default_owner()


@dataclass
class _AolSave:
  context: tuple
  group: str
  cancel: threading.Event
  done: threading.Event
  saved: bool = False


def visual_border_state(state: OnroadState, signals: Signals) -> OnroadState:
  border = state.border_signals
  if border is None or not (signals.vasm_left or signals.vasm_right):
    return state
  return replace(state, border_signals=BorderSignals(
    border.left_blinker, border.right_blinker,
    border.left_blindspot or signals.vasm_left, border.right_blindspot or signals.vasm_right))


class StarShellSession:
  """Own native shell resources and one request/router boundary per app."""

  def __init__(self, profile: Profile, camera_owner: Any, *, network_layer: Any = None, settings_layer: Any = None):
    font_dir = os.environ.get("STARPILOT_UI_FONT_DIR")
    self.profile = profile
    self.network_layer = network_layer
    self.settings_layer = settings_layer
    self.fonts = BitmapFonts(profile, Path(font_dir) if font_dir else default_font_directory())
    self.pip_renderer = PiPRenderer("bubble")
    self.pip_warning = PiPWarningSource()
    self._pip_saved = None
    self._vasm_saved = None
    self._pip_read_ns: int | None = None
    self._pip_signals_cache = None
    try:
      self.camera_owner = camera_owner
      self.camera_paint = True
      def camera_layer(rect, state):
        if profile == Profile.COMPACT:
          camera_owner.render_camera_model_layer(rect, camera_view=state.appearance.camera_view,
                                                 reverse_driver=state.reverse_driver_camera,
                                                 lead_indicator=state.appearance.show_lead_indicator and
                                                                state.lead_indicator_source_fresh,
                                                 lead_info_mode=state.appearance.lead_info_mode,
                                                 lead_info_metric=state.appearance.lead_info_metric,
                                                 lateral_active=state.lateral_active,
                                                 road_style=state.customization["roadColors"]["compact"], paint=self.camera_paint)
        else:
          camera_owner.render_camera_model_layer(rect, camera_view=state.appearance.camera_view,
                                                 reverse_driver=state.reverse_driver_camera,
                                                 road_style=state.customization["roadColors"]["large"])
      self.view = ShellView(self.fonts, Path(__file__).parents[2] / "selfdrive/assets",
                            camera_layer=camera_layer,
                            pip_layer=self._render_pip)
      self.view.onroad.driver_monitor_layer = self._render_driver_monitor
      self._driver_monitor_layer = None
      if profile == Profile.COMPACT:
        self.view.onroad.stock_confidence_layer = lambda rect, state: render_stock_confidence(camera_owner, rect, state)
        self.view.onroad.stock_confidence_reset = camera_owner.reset_stock_confidence_layer
        self.view.onroad.extra_overlays = lambda rect, state: camera_owner.render_model_source_layer(rect, show_long_indicator=None)
      else:
        self.view.onroad.extra_overlays = lambda rect, state: camera_owner.render_model_source_layer(rect)
    except Exception:
      self.fonts.close()
      raise
    self.galaxy_access = _galaxy_access_owner()
    self.bluetooth_source = BluetoothStatusSource() if profile == Profile.COMPACT else None
    self.adapter = RuntimeSnapshotAdapter(ui_state, self.galaxy_access,
                                          self.bluetooth_source.snapshot if self.bluetooth_source is not None else None)
    self.galaxy_flow = GalaxyAccessFlow(self.galaxy_access, self.confirmed_offroad)
    self.feature_owner = FeatureSettingsOwner(ui_state.params, self._feature_authority,
                                               vehicle_fingerprint=lambda: getattr(ui_state.CP, "carFingerprint", None),
                                               vehicle_params=lambda: ui_state.CP,
                                               vision_development=lambda: diagnostic_choice_enabled(os.environ, ui_state.CP))
    self.sounds_owner = SoundsOwner(ui_state.params, self._settings_preference_authority)
    self.appearance_owner = AppearanceOwner(ui_state.params, lambda: self._settings_preference_authority() or self._favorite_authority())
    self.pip_owner = PiPOwner(ui_state.params, self._settings_preference_authority,
                             self.pip_renderer.frame_availability.available)
    self.appearance_page = "appearance"
    self.display_owner = DisplayOwner(ui_state.params, self._settings_preference_authority)
    self.model_source: ModelStatusSource | None = None
    self._home_model: tuple[float, str] | None = None
    self.map_source: MapStatusSource | None = None
    self.model_scroll = 0
    self.drive_state = DriveStateControl(DriveStateOwner(ui_state.params, starpilot_storage_root() / "drive-state"),
                                         PhysicalSource(ui_state.sm))
    self.system_power_action: Callable[[int], None] | None = None
    self.power_owner = PowerOwner(ui_state.params, self._settings_preference_authority)
    self._power_request_epoch = 0
    self._pip_request_epoch = 0
    self._lane_change_request_epoch = 0
    self.feature_page: str = FeaturePage.HUB
    self.feature_root_page: str = FeaturePage.HUB
    self.feature_scroll = 0
    self.feature_edit_key: str | None = None
    self.sounds_scroll = 0
    self.sounds_edit_key: str | None = None
    self.appearance_scroll = 0
    self.appearance_edit_key: str | None = None
    self.display_scroll = 0
    self.display_page = "system"
    self.display_edit_key: str | None = None
    self.slc_actions: SlcActionDispatcher | None = None
    if slc_action_transport_enabled(os.environ, ui_state.params):
      try:
        self.slc_actions = SlcActionDispatcher(_slc_action_publisher(), self._live_slc_message)
      except (OSError, RuntimeError):
        pass  # The shell stays readable and reports action unavailability.
    self.input = ShellInput(profile, self._emit,
                            native_panels=NATIVE_SETTINGS_PANELS if profile == Profile.LARGE and settings_layer is not None else ())
    if profile == Profile.LARGE:
      self.view.onroad.pressed_action = lambda: self.input.onroad.pressed_action
    self.selected = Destination.STAR
    self.sidebar_expanded = True
    self._onroad_page_width = 2160.0
    self.compact_y = 0.0
    self.compact_scroll_x = 0.0
    self.notice = ""
    self.notice_until = 0.0
    self._mode = ShellMode.HOME
    self._on_settings: Any = None
    self._on_home: Any = None
    self._on_pairing: Any = None
    self._on_compact_destination: Any = None
    self._on_destination_change: Any = None
    self._request_owner: Any = None
    self._request_emitted = False
    self._snapshot_cache: tuple[tuple, ShellSnapshot] | None = None
    self._rendered_settings: tuple[int, ShellSnapshot] | None = None
    self._settings_touch: ShellSnapshot | None = None
    self._visual_preview_flags = frozenset()
    raw_preview = os.environ.get("SP_ONROAD_VISUAL_PREVIEW", "")
    if raw_preview:
      from openpilot.tools.replay.onroad import PREFIX_RE
      if (os.environ.get("SP_HOST_RUNTIME") == "1" and
          PREFIX_RE.fullmatch(os.environ.get("OPENPILOT_PREFIX", "")) is not None):
        self._visual_preview_flags = parse_flags(raw_preview)
    self._visual_preview_start_ns = time.monotonic_ns()
    self._native_favorite_actions: Callable[[], dict] = dict
    # Native publisher for taps on the projected car screen.
    self._publish_bookmark: Callable[[], None] | None = None
    self._wheel_sock = messaging.sub_sock("slcCruiseEvent", conflate=False)
    self._aol_save = None
    self._aol_save_closed = False
    self._wheel_pending = deque(maxlen=32)
    self._wheel_consumer = WheelConsumer()
    self._wheel_personality = None
    self.conditional_actions: ConditionalUiActionOwner | None = None
    self.cruise_actions = CruiseActionPublisher()
    self.favorites_owner = FavoritesOwner(ui_state.params, self._favorite_actions, self._favorite_authority, self._favorite_render_actions,
                                         current_action=lambda key: self._resolve_favorite_actions(frozenset((key,))).get(key))
    self.favorites = OnroadFavorites(self.fonts, self._favorite_request)
    if profile == Profile.LARGE:
      self.favorites.prepare()
      self.view.onroad.quick_select_layer = lambda _content, _state: self.favorites.render(time.monotonic())
    self._favorite_snapshot = None
    self._favorite_data = None
    self._favorite_read_at = None
    self._favorite_include_options = False
    self._favorite_claimed = False
    self._projection_bookmarks = None
    self._projection_slc = None
    if getattr(ui_state, 'replay_clock', None) is None:
      try:
        self._projection_bookmarks = BookmarkReceiver()
      except OSError:
        pass  # Projection bookmarks stay unavailable without affecting the native UI.
      try:
        self._projection_slc = SlcActionReceiver()
      except OSError:
        pass  # Car-screen speed-limit taps stay unavailable without affecting the native UI.
    self.onroad_map = self.onroad_jetlink = None
    if profile == Profile.LARGE:
      from openpilot.starpilot.ui.onroad_map_layer import NativeMapLayer
      self.onroad_map = NativeMapLayer(self.fonts, ui_state)
      self.view.onroad.map_layer = self.onroad_map.render
      from openpilot.starpilot.ui.onroad_jetlink_layer import NativeJetlinkLayer
      self.onroad_jetlink = NativeJetlinkLayer(self.view.onroad.jetlink_status, ui_state)
      self.view.onroad.jetlink_status = self.onroad_jetlink

  def _favorite_authority(self, *, allow_personality_notice: bool = False) -> bool:
    if getattr(ui_state, "replay_clock", None) is not None:
      return False
    if getattr(self, "_projection_tap", False):
      return bool(ui_state.started and display_message(ui_state.sm, 'carState', time.monotonic_ns(),
                                                       after_frame=ui_state.started_frame) is not None)
    if self._mode != ShellMode.ONROAD:
      return False
    state = self.snapshot(ShellMode.ONROAD).onroad
    camera_allowed = (self.profile != Profile.COMPACT or not state.reversing or
                      state.appearance.camera_view in (CameraViewChoice.NONE, CameraViewChoice.DRIVER))
    alert_allowed = state.alert.size == AlertSize.NONE or allow_personality_notice and is_personality_notice(state.alert)
    return bool(self._mode == ShellMode.ONROAD and state.camera_available and alert_allowed and camera_allowed)

  def _bookmark_authority(self) -> bool:
    if (getattr(ui_state, "replay_clock", None) is not None or
        self._mode != ShellMode.ONROAD or not ui_state.started):
      return False
    car_state = display_message(ui_state.sm, 'carState', time.monotonic_ns(), after_frame=ui_state.started_frame)
    return bool(car_state is not None and car_state.canValid and not car_state.canTimeout)

  def _render_driver_monitor(self, rect, state):
    from openpilot.starpilot.ui.runtime_snapshot import display_message
    now = state.observed_ns or time.monotonic_ns()
    monitor = display_message(ui_state.sm, "driverMonitoringState", now, after_frame=ui_state.started_frame)
    driver = display_message(ui_state.sm, "driverStateV2", now, after_frame=ui_state.started_frame)
    if self._driver_monitor_layer is None:
      self._driver_monitor_layer = DriverMonitorLayer(self.profile)
    top_icons = (self.profile == Profile.COMPACT and state.alert.size == AlertSize.NONE and
                 state.appearance.camera_view != CameraViewChoice.DRIVER and not state.reverse_driver_camera and
                 self.view.onroad.compact_hud._set_speed_alpha.x > 0.01 and
                 state.customization["layouts"]["compact"]["max_speed"]["enabled"])
    self._driver_monitor_layer.render(state, monitor=monitor, driver=driver,
                                      fresh=monitor is not None and driver is not None,
                                      onroad=ui_state.is_onroad(), top_icons=top_icons)

  def _favorite_actions(self):
    return self._resolve_favorite_actions()

  def _favorite_render_actions(self, keys):
    return self._resolve_favorite_actions(keys)

  def _resolve_favorite_actions(self, keys=None):
    actions = mapped_actions(lambda page: self.feature_snapshot(page, favorite=True), self.feature_request,
                             lambda: self.appearance_owner.snapshot(self.profile), self.appearance_owner.apply, requested_keys=keys)
    actions.update(layout_actions(ui_state.params, str(self.profile),
                                  lambda: self._settings_preference_authority() or self._favorite_authority(),
                                  lambda: self.adapter.invalidate_appearance()))
    actions.update(self._native_favorite_actions())
    return actions if keys is None else {key: action for key, action in actions.items() if key in keys}

  def native_favorite_actions(self, bookmark, personality, experimental):
    state = self.snapshot(ShellMode.ONROAD).onroad
    available = self._favorite_authority()
    bookmark_available = self._bookmark_authority()
    conditional = self._conditional_favorite_active()
    long_available = bool(available and ui_state.CP is not None and ui_state.has_longitudinal_control and
                          not ui_state.params.get_bool("SafeMode"))
    drive = (ui_state.started_frame, getattr(ui_state.CP, "carFingerprint", None),
             getattr(ui_state.CP, "flags", None))
    current_personality = int(ui_state.personality)

    def guarded(callback):
      return self._favorite_authority() and callback()

    def guarded_long(callback):
      return bool(self._favorite_authority() and ui_state.CP is not None and ui_state.has_longitudinal_control and
                  not ui_state.params.get_bool("SafeMode") and callback())

    context = (self._conditional_action_owner().context(ui_state.sm, ui_state.CP, now_ns=time.monotonic_ns())
               if long_available and conditional else None)
    exp_available = bool(long_available and (not conditional or context is not None))
    def toggle_experimental():
      if self._conditional_favorite_active() != conditional:
        return False
      if conditional:
        return bool(context is not None and guarded_long(lambda: self._conditional_action_owner().dispatch(
          context, ui_state.sm, ui_state.CP, _slc_action_publisher(), now_ns=time.monotonic_ns())))
      return guarded_long(experimental)

    def cycle_personality(direction):
      if not (self._favorite_authority(allow_personality_notice=True) and ui_state.CP is not None and
              ui_state.has_longitudinal_control and not ui_state.params.get_bool("SafeMode")):
        return False
      try:
        raw, readable = read_saved(ui_state.params, "LongitudinalPersonality", 8)
      except (AttributeError, OSError, TypeError, ValueError):
        return False
      if not readable or raw not in (None, b"0", b"1", b"2"):
        return False
      current = int(raw) if raw is not None else 1  # Factory Standard when the setting is absent.
      return personality((current + direction) % 3)

    personality_available = bool(self._favorite_authority(allow_personality_notice=True) and ui_state.CP is not None and
                                 ui_state.has_longitudinal_control and not ui_state.params.get_bool("SafeMode"))
    self._wheel_personality = lambda: cycle_personality(-1)
    actions = {
      BOOKMARK: FavoriteAction(BOOKMARK, "Bookmark", available=bookmark_available, reason="" if bookmark_available else "Fresh onroad state required",
                               token=repr((drive, bookmark_available)),
                               invoke=lambda: self._bookmark_authority() and bookmark()),
      CYCLE_PERSONALITY: FavoriteAction(CYCLE_PERSONALITY, "Cycle Driving Personality", kind="enum",
                                      state_label=("Aggressive", "Standard", "Relaxed")[current_personality],
                                      available=personality_available, reason="" if personality_available else "Longitudinal control required",
                                      token=repr((drive, current_personality, personality_available)),
                                      invoke=lambda: cycle_personality(1)),
      EXPERIMENTAL: FavoriteAction(EXPERIMENTAL, "Experimental Mode", kind="toggle",
                                  state_label=("Overridden Chill" if (state.conditional_effective is not None and
                                                                   state.conditional_effective.reason == 'manual_chill') else
                                               "Forced Experimental" if (state.conditional_effective is not None and
                                                                          state.conditional_effective.reason == 'manual_experimental') else
                                               "Automatic Experimental" if (state.conditional_effective is not None and
                                                                             state.conditional_effective.effective_experimental) else
                                               "On" if state.experimental_enabled else "Off"), available=exp_available,
                                  reason=("Active longitudinal control and current conditional status required" if conditional else
                                          "Confirmation and longitudinal control required"),
                                  token=repr((drive, state.experimental_enabled, exp_available, conditional,
                                              context.token if context is not None else None)), invoke=toggle_experimental),
    }

    from openpilot.starpilot.controllers.selfie import SelfieCapture, authority as selfie_authority
    from openpilot.starpilot.controllers.wheel_actions import cp_fingerprint, eligible
    from openpilot.starpilot.favorites.actions import SELFIE
    if not hasattr(self, 'selfie_capture'):
      self.selfie_capture = SelfieCapture()
    selfie_drive = int(getattr(ui_state.sm['deviceState'], 'startedMonoTime', 0))
    selfie_fingerprint = cp_fingerprint(ui_state.CP) if eligible(ui_state.CP) else ''
    def selfie_permitted():
      return getattr(self, "_mode", None) == ShellMode.ONROAD and selfie_authority(ui_state.sm, ui_state.CP,
        drive_id=selfie_drive, fingerprint=selfie_fingerprint, now_ns=time.monotonic_ns())
    selfie_allowed = available and selfie_permitted() and not self.selfie_capture.busy
    actions[SELFIE] = FavoriteAction(SELFIE, "Selfie", available=selfie_allowed,
      reason="Current cabin camera and drive required" if not selfie_allowed else "",
      token=repr((selfie_drive, selfie_fingerprint, selfie_allowed)),
      invoke=lambda: self._favorite_authority() and self.selfie_capture.submit(selfie_permitted))

    from openpilot.selfdrive.ui.ui_state import device
    actions[SCREEN_OFF] = FavoriteAction(SCREEN_OFF, "Toggle Screen Off", kind="toggle",
      state_label="Off" if device.awake else "On", available=available,
      reason="Onroad only" if not available else "", token=repr((drive, available, device.awake)),
      invoke=lambda: guarded(device.toggle_screen_off))
    if not hasattr(self, 'mode_actions'):
      self.mode_actions = ModeActionPublisher()
    for key, label, mode, requested in ((TRAFFIC, "Traffic Mode", 'traffic', state.traffic_mode),
                                       (SWITCHBACK, "Switchback Mode", 'switchback', getattr(state, 'switchback_mode', False))):
      now_ns = time.monotonic_ns()
      allowed = (self._favorite_authority() and not ui_state.params.get_bool("SafeMode") and
                 mode_action_authority(ui_state.sm, ui_state.CP, now_ns, mode) and
                 producer_available(ui_state.sm, now_ns=now_ns))
      def toggle_mode(mode=mode):
        return bool(self._favorite_authority() and not ui_state.params.get_bool("SafeMode") and self.mode_actions.dispatch(
          mode, ui_state.sm, ui_state.CP, _slc_action_publisher(), now_ns=time.monotonic_ns()))
      actions[key] = FavoriteAction(key, label, kind="toggle", state_label="On" if requested else "Off",
        available=allowed, reason="Current qualified control required" if not allowed else "",
        token=repr((drive, mode, requested, allowed)), invoke=toggle_mode)

    for key, label, mode in ((FORCE_COAST, "Force Coasting", "coast"), (PULSE_GLIDE, "Pulse and Glide", "pulse"),
                             (DISENGAGE, "Disengage Openpilot", "disengage")):
      now_ns = time.monotonic_ns()
      allowed = (self._favorite_authority() and mode_action_authority(ui_state.sm, ui_state.CP, now_ns, mode) and
                 (mode == "disengage" or not ui_state.params.get_bool("SafeMode") and producer_available(ui_state.sm, now_ns=now_ns)))
      def apply_mode(mode=mode):
        return bool(self._favorite_authority() and (mode == "disengage" or not ui_state.params.get_bool("SafeMode")) and
                    self.mode_actions.dispatch(mode, ui_state.sm, ui_state.CP, _slc_action_publisher(), now_ns=time.monotonic_ns()))
      actions[key] = FavoriteAction(key, label, available=allowed, token=repr((drive, mode, allowed)),
                                   reason="Fresh onroad control required", invoke=apply_mode)
    if ui_state.CP is not None and not ui_state.CP.pcmCruise:
      current = self._favorite_authority() and cruise_action_authority(ui_state.sm, ui_state.CP, time.monotonic_ns())
      def change_speed(increase):
        return bool(not ui_state.params.get_bool("SafeMode") and self.cruise_actions.dispatch(
          increase, ui_state.sm, ui_state.CP, _slc_action_publisher(), now_ns=time.monotonic_ns()))
      for key, label, increase in ((INCREASE_SPEED, "Increase Speed", True), (DECREASE_SPEED, "Decrease Speed", False)):
        actions[key] = FavoriteAction(key, label, available=current and not ui_state.params.get_bool("SafeMode"),
                                     reason="Active software cruise control required", token=repr((drive, current)),
                                     invoke=lambda increase=increase: change_speed(increase))
      def set_speed(value):
        if not self._favorite_authority() or type(value) not in (int, float) or not math.isfinite(value):
          return False
        metric_raw, readable = read_saved(ui_state.params, "IsMetric", 8)
        if not readable or metric_raw not in (None, b"0", b"1"):
          return False
        metric = metric_raw == b"1"
        if not (8 <= value <= 145 if metric else 5 <= value <= 90):
          return False
        return bool(not ui_state.params.get_bool("SafeMode") and self.cruise_actions.dispatch(
          True, ui_state.sm, ui_state.CP, _slc_action_publisher(), now_ns=time.monotonic_ns(),
          target_mps=float(value) * (1 / 3.6 if metric else 0.44704)))
      actions[SET_SPEED] = FavoriteAction(SET_SPEED, "Set Speed To", available=current and not ui_state.params.get_bool("SafeMode"),
        reason="Active software cruise required; speed uses device units", token=repr((drive, current)), invoke_value=set_speed)
    return actions

  def _poll_projection_bookmark(self, now_ns):
    receiver = getattr(self, '_projection_bookmarks', None)
    if receiver is None:
      return
    # Bookmark taps use live onroad state, independent of this screen's page or alerts.
    # A render gap beyond the 20 ms control limit must not lose drained requests.
    publish = getattr(self, '_publish_bookmark', None)
    authorized = bool(publish is not None and ui_state.started and getattr(ui_state, "replay_clock", None) is None and
                      display_message(ui_state.sm, 'carState', now_ns, after_frame=ui_state.started_frame) is not None)
    stamp = int(ui_state.sm.logMonoTime['carState']) if authorized else 0
    for _ in range(receiver.drain(now_ns, stamp, authorized=authorized)):
      cast('Callable[[], None]', publish)()

  def _poll_projection_slc(self, now_ns):
    receiver = getattr(self, '_projection_slc', None)
    if receiver is None:
      return
    # Always drain; publish only when the live-state tap checks pass.
    for request in receiver.drain(now_ns):
      if not ui_state.started or getattr(ui_state, "replay_clock", None) is not None:
        continue
      if isinstance(request, OnroadRequest):
        self._projection_experimental(request.value)
      elif self.slc_actions is not None:
        self.slc_actions.dispatch(request)

  def _projection_experimental(self, requested: bool) -> None:
    """A car-screen wheel tap: the native experimental binding acts only if it is
    available and still in the state the car screen showed (the opposite of requested)."""
    self._projection_tap = True
    try:
      binding = self._native_favorite_actions().get(EXPERIMENTAL)
      current = self.snapshot(ShellMode.ONROAD).onroad.experimental_enabled
      if not (binding is not None and binding.available and binding.invoke is not None and
              current != requested and binding.invoke()):
        self._unavailable("experimental shortcut in this context")
    finally:
      self._projection_tap = False

  def _poll_wheel(self, now_ns):
    if getattr(ui_state, "replay_clock", None) is not None:
      return
    for _ in range(32):
      event = messaging.recv_one_or_none(self._wheel_sock)
      if event is None:
        break
      if event.valid and str(event.slcCruiseEvent.kind) == "wheelAction":
        self._wheel_pending.append(event)
    for _ in range(len(self._wheel_pending)):
      event = self._wheel_pending.popleft()
      wire = event.slcCruiseEvent.wheelAction
      if (int(wire.sourceCarStateMonoTime) > int(ui_state.sm.logMonoTime["carState"]) or
          int(wire.sourceCarControlMonoTime) > int(ui_state.sm.logMonoTime["carControl"])) and now_ns <= int(wire.validUntilMonoTime):
        self._wheel_pending.append(event)
        continue
      command = self._wheel_consumer.accept(event, ui_state.params, ui_state.CP, ui_state.sm, now_ns=now_ns)
      if command is None or command.action == 0:
        continue
      authorized = (self._bookmark_authority() if command.action == 8 else
                    self._favorite_authority(allow_personality_notice=command.action == 1))
      if not authorized:
        continue
      actions = self._native_favorite_actions()
      try:
        if command.action == 1:
          if self._wheel_personality is not None:
            self._wheel_personality()
        elif command.action in (5, 7, 8):
          key = {5: EXPERIMENTAL, 6: TRAFFIC, 7: SWITCHBACK, 8: BOOKMARK}[command.action]
          action = actions.get(key)
          if action is not None and action.available and action.invoke is not None:
            action.invoke()
        elif command.action == 10 and self.slc_actions is not None:
          from openpilot.starpilot.ui.onroad_state import SlcActionKind
          state = self._live_slc_message(now_ns)
          if state is not None and not state.hasPending:
            self.slc_actions.dispatch(SlcUiRequest(SlcActionKind.ADOPT, str(state.sessionId), int(state.decisionId),
                                                  int(state.presentationId), float(state.speedLimit)))
        elif command.action in (11, 12, 13):
          slot = self.favorites_owner.snapshot().slots[command.action - 11]
          if slot.available and slot.request is not None:
            self.favorites_owner.invoke(slot.request)
      except (OSError, RuntimeError, ValueError, TypeError):
        pass

  def _conditional_action_owner(self):
    if self.conditional_actions is None:
      self.conditional_actions = ConditionalUiActionOwner(ui_state.params)
    return self.conditional_actions

  def _conditional_favorite_active(self):
    if not feature_enabled(ui_state.params, ui_state.CP, "conditional", os.environ):
      return False
    try:
      raw = ui_state.params.get("ConditionalModeConfig")
      preferences = SavedPreferences() if raw is None else decode_preferences(raw)
      return preferences.mode != ModeChoice.STOCK
    except (PreferenceError, OSError, TypeError, ValueError):
      return True

  def _update_favorites(self, state, now):
    include_options = self.favorites.mode == "picker"
    if (self._favorite_read_at is None or now < self._favorite_read_at or now - self._favorite_read_at >= 1 or
        include_options and not getattr(self, "_favorite_include_options", False)):
      self._favorite_snapshot = self.favorites_owner.snapshot(include_options=include_options)
      self._favorite_data = asdict(self._favorite_snapshot)
      self._favorite_include_options = include_options
      self._favorite_read_at = now
    self.favorites.update(state, self._favorite_data, now)

  def _favorite_request(self, request) -> bool:
    if not self._favorite_authority():
      return False
    snapshot = self._favorite_snapshot
    if snapshot is None or snapshot.revision != request.revision:
      return False
    if request.kind == "activate":
      slot = snapshot.slots[request.index]
      success = slot.request is not None and self.favorites_owner.invoke(slot.request).success
    elif request.kind == "assign":
      success = self.favorites_owner.configure_index(request.index, request.key, request.revision)
    elif request.kind == "unassign":
      success = self.favorites_owner.clear_index(request.index, request.revision)
    else:
      return False
    if success:
      include_options = request.kind != "activate"
      self._favorite_snapshot = self.favorites_owner.snapshot(include_options=include_options)
      self._favorite_data = asdict(self._favorite_snapshot)
      self._favorite_include_options = include_options
      self.favorites.data = self._favorite_data
      self._favorite_read_at = time.monotonic()
    else:
      self._favorite_read_at = None
    return bool(success)


  def set_navigation(self, *, on_settings: Any, on_home: Any, on_pairing: Any = None, on_compact_destination: Any = None,
                     on_destination_change: Any = None) -> None:
    self._on_settings = on_settings
    self._on_home = on_home
    self._on_pairing = on_pairing
    self._on_compact_destination = on_compact_destination
    self._on_destination_change = on_destination_change

  def set_request_owner(self, owner: Any) -> None:
    self._request_owner = owner

  def confirmed_offroad(self) -> bool:
    return bool(ui_state.is_offroad() and self.adapter.confirmed_offroad())

  def connectivity_allowed(self) -> bool:
    # Connectivity setup also works on USB power without vehicle telemetry.
    return ui_state.is_offroad()

  def configuration_allowed(self) -> bool:
    drive_state = getattr(self, 'drive_state', None)
    return self.confirmed_offroad() or bool(drive_state is not None and drive_state.physical.allowed())

  def _settings_preference_authority(self) -> bool:
    return self._mode == ShellMode.SETTINGS

  def _feature_authority(self, group: str) -> bool:
    preference_authority = self._settings_preference_authority() or (
      group in ("preferences", "lane", "long", "toyota_cruise", "long_output", "slc", "aol") and self._favorite_authority())
    if group == "preferences":
      return preference_authority
    if group == "parked_preferences":
      return self._settings_preference_authority() and self.configuration_allowed()
    cp = ui_state.CP
    if cp is None or not getattr(cp, "carFingerprint", ""):
      return False
    if group in ("lane_change", "conditional", "conditional_wheel", "switchback_wheel", "aol_wheel",
                 "lane", "long", "toyota_cruise", "long_output", "slc", "torque", "aol", "vehicle", "toyota_radar"):
      if not preference_authority:
        return False
      if group == "toyota_cruise":
        from openpilot.starpilot.controllers.toyota_cruise import capability
        return capability(cp) is not None
      if group == "toyota_radar":
        from openpilot.starpilot.car.toyota.radar_preferences import capability
        return capability(cp) is not None
      if group == "vehicle":
        from opendbc.car.toyota.interface import toyota_auto_hold_supported
        return toyota_auto_hold_supported(cp)
      if group == "lane_change":
        return not cp.notCar and not cp.passive and not cp.dashcamOnly
      if group == "switchback_wheel":
        from openpilot.starpilot.feature_capabilities import lateral_supported
        return lateral_supported(cp)
      if group in ("conditional", "conditional_wheel", "long_output", "slc"):
        return bool(cp.openpilotLongitudinalControl and not cp.notCar and not cp.passive and not cp.dashcamOnly)
      if group in ("aol_wheel", "lane", "torque", "aol"):
        return not cp.notCar and not cp.passive and not cp.dashcamOnly
      return bool(cp.openpilotLongitudinalControl and not cp.notCar and
                  not cp.passive and not cp.dashcamOnly)
    return False

  def feature_snapshot(self, page: str | None = None, *, favorite: bool = False):
    self._poll_aol_save()
    state = self.feature_owner.snapshot(page or self.feature_page, parked=False if favorite else self.configuration_allowed(),
                                       system_long=self._feature_authority("long"),
                                       lateral_context=self._feature_authority("lane"), metric=bool(ui_state.is_metric),
                                       configure_while_driving=self._settings_preference_authority() or
                                       (favorite and self._favorite_authority()))
    if self.profile == Profile.LARGE and state.page == FeaturePage.VEHICLE:
      state = replace(state, rows=self._vehicle_selector().rows() + state.rows,
                      subtitle="Vehicle selection applies at the next startup.")
    if getattr(self, "_aol_save", None) is not None:
      state = replace(state, rows=tuple(replace(row, available=False, reason="Saving AOL preference…")
                                        if row.key == "AlwaysOnLateral" else row for row in state.rows))
    if self.profile == Profile.LARGE and not favorite:
      state = value_editor_state(state, getattr(self, "feature_edit_key", None) if page is None else None)
      if state.editor and state.rows[0].key in (*LANE_CHANGE_KEYS, "CustomCruise", "CustomCruiseLong", "ToyotaRadarMode"):
        state = replace(state, save_hint="Changes require confirmation")
    return state

  def _vehicle_selector(self) -> VehicleLarge:
    if getattr(self, "vehicle_selector", None) is None:
      self.vehicle_selector = VehicleLarge(self)
    return self.vehicle_selector

  def _aol_context(self):
    return self._mode, getattr(self, "selected", None), getattr(self, "feature_page", None)

  def _poll_aol_save(self) -> None:
    pending = getattr(self, "_aol_save", None)
    if pending is None:
      return
    if self._aol_context() != pending.context or not self._feature_authority(pending.group):
      pending.cancel.set()
    if not pending.done.is_set():
      self.notice, self.notice_until = "Saving AOL preference…", time.monotonic() + 3.0
      return
    self._aol_save = None
    self._snapshot_cache = None
    self._favorite_read_at = None
    if pending.saved:
      self.notice, self.notice_until = "AOL preference saved", time.monotonic() + 3.0
    else:
      self._unavailable("AOL preference was not saved; refresh settings")

  def _save_aol(self, request: FeatureSettingsRequest) -> bool:
    self._poll_aol_save()
    if getattr(self, "_aol_save_closed", False) or getattr(self, "_aol_save", None) is not None:
      return False
    group = "preferences" if request.value == "Off" else "aol"
    if not self._feature_authority(group):
      self._unavailable("AOL preference is unavailable in this context")
      return False
    pending = _AolSave(self._aol_context(), group, threading.Event(), threading.Event())
    self._aol_save = pending
    owner = self.feature_owner
    def authorized(group: str) -> bool:
      return (group == pending.group and not pending.cancel.is_set() and
              not getattr(self, "_aol_save_closed", False) and self._aol_context() == pending.context)
    writer = FeatureSettingsOwner(owner.params, authorized, vehicle_fingerprint=owner.vehicle_fingerprint,
                                  vehicle_params=owner._vehicle_params_source)
    def save() -> None:
      try:
        pending.saved = writer.apply(request)
      except Exception:
        pending.saved = False
      finally:
        pending.done.set()
    threading.Thread(target=save, name="aol-preference", daemon=True).start()
    self._snapshot_cache = None
    self.notice, self.notice_until = "Saving AOL preference…", time.monotonic() + 3.0
    return False  # The bool API reports a completed save, never an accepted job.

  def feature_request(self, request: FeatureSettingsRequest) -> bool:
    if request.key == "AlwaysOnLateral":
      return self._save_aol(request)
    ok = self.feature_owner.apply(request)
    self._snapshot_cache = None
    if not ok:
      self._unavailable("Saved conditional choice may have changed; refresh settings" if
                        request.key.startswith("conditional:") else
                        "Curve learning was not verified; refresh saved settings" if request.key in
                        ("curve_reset",) else "Saved setting changed or is unavailable; refresh settings")
    return ok

  def sounds_snapshot(self):
    state = self.sounds_owner.snapshot()
    if self.profile == Profile.LARGE:
      rows = tuple(row for row in state.rows if not row.key.startswith(SOUND_AUTO_PREFIX))
      return sound_editor_state(replace(state, rows=rows), self.sounds_edit_key)
    return state

  def sounds_request(self, request: FeatureSettingsRequest) -> bool:
    ok = self.sounds_owner.apply(request)
    self._snapshot_cache = None
    if not ok:
      self._unavailable("saved sound level changed or settings closed")
    return ok

  def appearance_snapshot(self):
    state = self.pip_snapshot() if getattr(self, "appearance_page", "appearance") == "pip" else self.appearance_owner.snapshot(self.profile)
    return value_editor_state(state, getattr(self, "appearance_edit_key", None)) if self.profile == Profile.LARGE else state

  def pip_snapshot(self):
    return self.pip_owner.snapshot()

  def appearance_request(self, request: FeatureSettingsRequest) -> bool:
    pip_request = request.key.startswith(("pip:", "PIPPreview"))
    ok = self.pip_owner.apply(request) if pip_request else self.appearance_owner.apply(request)
    if ok:
      self.adapter.invalidate_appearance()
      self._pip_read_ns = None
    self._snapshot_cache = None
    if not ok:
      self._unavailable("saved side-camera choice may have changed; refresh settings" if
                        pip_request and self.pip_owner.last_write.committed else
                        "saved display choice changed or settings closed")
    return ok

  def _pip_signals(self):
    now_ns = time.monotonic_ns()
    if (self._pip_read_ns is None or now_ns < self._pip_read_ns or
        now_ns - self._pip_read_ns >= 1_000_000_000):
      self._pip_saved = read_pip(ui_state.params)
      self._vasm_saved = read_vasm_preferences(ui_state.params)
      self._pip_read_ns = now_ns
    car = display_message(ui_state.sm, "carState", now_ns, after_frame=ui_state.started_frame)
    vasm = self._vasm_saved
    pair = clock_pair_ns() if car is not None else None
    vasm_enabled = (car is not None and car.canValid and car.gearShifter == car_schema.CarState.GearShifter.drive and
                    pair is not None and vasm is not None and vasm.readable and
                    vasm.valid and vasm.preferences.enabled)
    left_warning, right_warning = self.pip_warning.sample(
      enabled=vasm_enabled, settings_fingerprint=vasm.fingerprint if vasm_enabled else "",
      now_mono_ns=pair[0] if pair is not None else 0,
      now_boot_ns=pair[1] if pair is not None else 0)
    return Signals(car is not None, bool(car.leftBlinker) if car is not None else False,
                   bool(car.rightBlinker) if car is not None else False,
                   bool(car.leftBlindspot) if car is not None else False,
                   bool(car.rightBlindspot) if car is not None else False,
                   left_warning, right_warning)

  def _render_pip(self, rect: rl.Rectangle, state, *, submit=None) -> None:
    signals = getattr(self, "_pip_signals_cache", None) or self._pip_signals()
    saved = self._pip_saved
    if (saved is None or saved.enabled is not True or saved.mask is None or
        saved.invert is None or saved.on_blinker is None or saved.on_bsm is None):
      self.pip_renderer.deactivate()
      return
    camera_positions = {}
    for side, key in zip(("left", "right"), CAMERA_WIDGETS, strict=True):
      position = placement(state.customization, self.profile, key)
      if position["enabled"]:
        width, height = widget_size(state.customization, self.profile, key)
        camera_positions[side] = Rect(position["x"], position["y"], width, height)
    status = self.pip_renderer.render(rect, saved.mask, signals, enabled=True,
                                      on_blinker=saved.on_blinker, on_bsm=saved.on_bsm, invert=saved.invert,
                                      placements=camera_positions, **({"submit": submit} if submit is not None else {}))
    if status not in ("rendered", "inactive") and state.alert.size == AlertSize.NONE:
      self.fonts.draw("SIDE CAMERA UNAVAILABLE", FontRole.MEDIUM,
                      18 if self.profile == Profile.COMPACT else 25,
                      18 if self.profile == Profile.COMPACT else rect.x + 30,
                      180 if self.profile == Profile.COMPACT else rect.y + rect.height - 62,
                      rl.Color(245, 200, 155, 255))

  def display_snapshot(self):
    return self.display_owner.snapshot(self.profile)

  def power_snapshot(self):
    return self.power_owner.snapshot()

  def system_snapshot(self):
    display = self.display_snapshot()
    power = self.power_snapshot()
    maps = self.map_snapshot()
    state = replace(display, title="System", subtitle="Display, drive state, power, and offline maps.",
                    rows=display.rows + power.rows + maps.rows)
    if self.profile == Profile.LARGE:
      display_rows = tuple(replace(row, presets=SYSTEM_PRESETS[row.key], page=row.key) if row.key in SYSTEM_PRESETS else row
                           for row in display.rows if not row.key.startswith(DISPLAY_AUTO_PREFIX))
      power_rows = tuple(replace(row, presets=SYSTEM_PRESETS[row.key], page=row.key) if row.key in SYSTEM_PRESETS else row
                         for row in power.rows)
      power_rows = value_editor_state(replace(power, rows=power_rows), None).rows
      if getattr(self, "display_edit_key", None) is not None:
        row = next(row for row in display_rows + power_rows if row.key == self.display_edit_key)
        return replace(state, page=getattr(self, "display_page", "system"), title=row.label, subtitle=row.reason, rows=(replace(row, page=""),),
                       parent_title="System" if row.key in POWER_KEYS else "Custom Display Settings", editor=True,
                       save_hint="Changes require confirmation" if row.key in POWER_KEYS else state.save_hint)
      if getattr(self, "display_page", "system") == "display":
        return replace(display, title="Custom Display Settings", subtitle="Brightness and screen timing.",
                       rows=display_rows, parent_title="System")
      drive = self.drive_state.snapshot()
      drive_row = FeatureRow("drive_state", "Force Drive State", LABELS[drive["mode"]],
                             source=drive["revision"].encode() if drive["revision"] is not None else None,
                             available=drive["available"], reason=f"Device: {drive['effective'] or 'unavailable'}",
                             actions=tuple((label, drive["mode"] != mode)
                                           for mode, label in LABELS.items()))
      power_available = getattr(self, "system_power_action", None) is not None and not ui_state.engaged
      device_power = FeatureRow("device_power", "Device Power", "", available=power_available,
                                actions=(("Reboot", True), ("Power Off", power_available and ui_state.is_offroad() and self.confirmed_offroad())))
      master = replace(display_rows[0], page="display", reason=display_rows[0].reason or "Brightness and screen timing")
      state = replace(state, page="system", rows=(drive_row, device_power, master) + power_rows + maps.rows)
    return state

  def map_snapshot(self):
    if getattr(self, "map_source", None) is None:
      self.map_source = MapStatusSource()
    return self.map_source.snapshot()

  def model_snapshot(self):
    try:
      if self.model_source is None:
        self.model_source = ModelStatusSource(self.adapter.ui_state.params)
      return model_page(self.model_source.snapshot(), self.model_manager_snapshot())
    except Exception:
      # Model status is optional diagnostics; UI remains usable without IPC.
      return model_page(project_status(None, None, None, None, time.monotonic_ns()))

  def _model_manager(self):
    if getattr(self, 'model_manager', None) is None:
      from openpilot.starpilot.models.manager import ModelManager
      self.model_manager = ModelManager(parked=self.confirmed_offroad, refresh_catalog=True)
    return self.model_manager

  def model_manager_snapshot(self):
    return self._model_manager().snapshot()

  def model_manager_action(self, action, payload):
    return self._model_manager().action(action, payload)

  def _models_ui(self, action: FeatureUiAction) -> None:
    if self._mode != ShellMode.SETTINGS or self.selected != Destination.DRIVING_MODEL:
      return
    state = self.model_snapshot()
    if action.kind == "back":
      self.selected = Destination.STAR
      self.model_scroll = 0
      self.input.cancel()
    elif action.kind == "scroll":
      self.model_scroll = feature_scroll(self.model_scroll, action.direction, len(state.rows))
    elif action.kind == "open" and action.row in state.rows and action.row.page in ("models:small", "models:big"):
      from openpilot.system.ui.lib.application import gui_app
      from openpilot.system.ui.widgets import DialogResult
      from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
      profile = action.row.page.split(":", 1)[1]
      manager = self.model_manager_snapshot()
      choices = dict(model_profile_choices(manager, profile))
      current = manager["activeSmallModel" if profile == "small" else "activeBigModel"]
      current_label = next((label for label, model_id in choices.items() if model_id == current), "")

      def confirmed(result):
        if result != DialogResult.CONFIRM or self._mode != ShellMode.SETTINGS or self.selected != Destination.DRIVING_MODEL:
          return
        fresh = self.model_manager_snapshot()
        if fresh["activeSmallModel" if profile == "small" else "activeBigModel"] != current:
          self._unavailable("saved model changed; reopen the selection")
          return
        request = model_profile_request(fresh, profile, choices.get(dialog.selection, ""))
        if request is not None:
          try:
            self.model_manager_action("active", request)
          except (OSError, ValueError, RuntimeError) as error:
            self._unavailable(str(error))
        self._snapshot_cache = None

      dialog = MultiOptionDialog(f"Active {'Small' if profile == 'small' else 'Big'}", list(choices), current_label, callback=confirmed)
      gui_app.push_widget(dialog)
    self._snapshot_cache = None

  def power_request(self, request: FeatureSettingsRequest) -> bool:
    ok = self.power_owner.apply(request)
    self._snapshot_cache = None
    if not ok:
      self._unavailable("saved parked-power choice changed or settings closed")
    return ok

  def display_request(self, request: FeatureSettingsRequest) -> bool:
    ok = self.display_owner.apply(request)
    if ok:
      native_device.invalidate_display_preferences()
    self._snapshot_cache = None
    if not ok:
      self._unavailable("saved display choice changed or settings closed")
    return ok

  def _display_ui(self, action: FeatureUiAction) -> None:
    if self._mode != ShellMode.SETTINGS or self.selected != Destination.SYSTEM:
      return
    state = self.system_snapshot()
    if action.kind == "back":
      if state.editor:
        self.display_edit_key = None
      elif state.page == "display":
        self.display_page = "system"
        self.display_scroll = 0
      else:
        self.selected = Destination.STAR
        self.display_scroll = 0
      self.input.cancel()
    elif action.kind == "open" and not state.editor and action.row in state.rows:
      if action.row.key == DISPLAY_MASTER and action.row.page == "display":
        self.display_page = "display"
        self.display_scroll = 0
      elif action.row.available and action.row.presets:
        self.display_edit_key = action.row.key
      else:
        return
      self.input.cancel()
    elif action.kind == "scroll" and not state.editor:
      self.display_scroll = feature_scroll(self.display_scroll, action.direction, len(state.rows))
    elif action.kind in ("change", "action") and action.row is not None and action.row in state.rows:
      request = None
      if action.kind == "action" and action.row.key in ("drive_state", "device_power"):
        if not action.row.available or not 0 <= action.direction < len(action.row.actions) or not action.row.actions[action.direction][1]:
          return
        if action.row.key == "drive_state":
          self._drive_change(tuple(LABELS)[action.direction].value, action.row.source.decode() if action.row.source else None)
        elif self.system_power_action is not None:
          self.system_power_action(action.direction)
      elif state.editor:
        request = value_request(state, action)
      elif action.kind == "change" and not action.row.presets:
        request = power_row_change(action.row, action.direction) if action.row.key in POWER_KEYS else row_change(action.row, action.direction)
      else:
        return
      if request is not None:
        if request.key in POWER_KEYS:
          from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
          from openpilot.system.ui.widgets import DialogResult
          epoch = getattr(self, "_power_request_epoch", 0)
          def confirmed(result: DialogResult) -> None:
            if (result == DialogResult.CONFIRM and self._mode == ShellMode.SETTINGS and
                self.selected == Destination.SYSTEM and getattr(self, "display_page", "system") == "system" and
                epoch == getattr(self, "_power_request_epoch", 0)):
              self.power_request(replace(request, confirmation=True))
          gui_app.push_widget(ConfirmDialog(confirm_question(request), "Save", callback=confirmed))
        else:
          if self.display_request(request) and request.key == DISPLAY_MASTER and request.value == "On" and state.page == "system":
            self.display_page = "display"
            self.display_scroll = 0
            self.input.cancel()
    self._snapshot_cache = None

  def _drive_change(self, mode, revision, *, confirmation=False):
    if mode == "offroad" and not confirmation and (ui_state.started or self.drive_state.snapshot()["effective"] == "onroad"):
      from openpilot.system.ui.widgets import DialogResult
      destination = self.selected
      page = getattr(self, "display_page", "system")
      epoch = getattr(self, "_power_request_epoch", 0)
      pipeline = (bool(ui_state.started), ui_state.started_frame)
      def confirmed(result: DialogResult) -> None:
        if (result == DialogResult.CONFIRM and self._mode == ShellMode.SETTINGS and
            self.selected == destination and page == getattr(self, "display_page", "system") and
            epoch == getattr(self, "_power_request_epoch", 0) and
            pipeline == (bool(ui_state.started), ui_state.started_frame)):
          self._drive_change(mode, revision, confirmation=True)
      if self.profile == Profile.COMPACT:
        from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog
        gui_app.push_widget(BigConfirmationDialog("slide offroad",
          gui_app.texture("icons_mici/settings/device/reboot.png", 64, 64),
          lambda: confirmed(DialogResult.CONFIRM), red=True))
      else:
        from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
        gui_app.push_widget(ConfirmDialog("Switch to Offroad and stop driving services?", "Force Offroad", callback=confirmed))
      return
    try:
      self.drive_state.change(mode, revision, lambda: self._mode == ShellMode.SETTINGS)
    except DriveStateRejected as error:
      self.notice, self.notice_until = str(error), time.monotonic() + 3.0
    except (OSError, RuntimeError):
      self.notice, self.notice_until = "Drive state unavailable", time.monotonic() + 3.0
    self._snapshot_cache = None

  def _appearance_ui(self, action: FeatureUiAction) -> None:
    if self._mode != ShellMode.SETTINGS or self.selected != Destination.APPEARANCE:
      return
    state = self.appearance_snapshot()
    if action.kind == "back":
      if getattr(self, "appearance_edit_key", None) is not None:
        self.appearance_edit_key = None
        self._pip_request_epoch = getattr(self, "_pip_request_epoch", 0) + 1
      elif getattr(self, "appearance_page", "appearance") == "pip":
        self.appearance_page = "appearance"
        self._pip_request_epoch = getattr(self, "_pip_request_epoch", 0) + 1
        self.appearance_scroll = 0
      else:
        self.selected = Destination.STAR
        self.appearance_scroll = 0
      self.input.cancel()
    elif action.kind == "open" and not state.editor and action.row is not None and action.row in state.rows:
      if action.row.presets and action.row.available:
        self.appearance_edit_key = action.row.key
      elif action.row.page == "pip":
        self.appearance_page = "pip"
        self.appearance_scroll = 0
      else:
        return
      self.input.cancel()
    elif action.kind == "scroll" and not state.editor:
      self.appearance_scroll = feature_scroll(self.appearance_scroll, action.direction, len(state.rows))
    elif action.kind in ("change", "action") and action.row is not None and action.row in state.rows:
      request = value_request(state, action)
      if request is not None:
        self.appearance_request(request)
    elif action.kind == "reset" and action.row is not None and action.row in state.rows and \
         (action.row.key == PIP_RESET or action.row.key.startswith(PIP_FORMAT_PREFIX)):
      row = action.row
      request = FeatureSettingsRequest(row.key, row.source, "confirm", confirmation=True,
                                       dependencies=row.dependencies)
      epoch = getattr(self, "_pip_request_epoch", 0)
      from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
      from openpilot.system.ui.widgets import DialogResult
      def confirmed(result: DialogResult) -> None:
        if result == DialogResult.CONFIRM and self._mode == ShellMode.SETTINGS and \
           self.selected == Destination.APPEARANCE and self.appearance_page == "pip" and \
           epoch == getattr(self, "_pip_request_epoch", 0):
          self.appearance_request(request)
      size = row.key.removeprefix(PIP_FORMAT_PREFIX).replace("x", " × ")
      subject = ("Restore the default left and right camera crops?" if row.key == PIP_RESET else
                 f"Use the {size} camera format with proportional starting crops? " +
                 "Check alignment on the device.")
      question = (subject + " The saved preview switch will not change. If it is On, " +
                  "the preview may resume on the next drive when a fresh camera frame is available.")
      gui_app.push_widget(ConfirmDialog(question,
                                        "Restore", callback=confirmed))
    self._snapshot_cache = None

  def _sounds_ui(self, action: FeatureUiAction) -> None:
    if self._mode != ShellMode.SETTINGS or self.selected != Destination.SOUNDS:
      return
    state = self.sounds_snapshot()
    if action.kind == "back":
      if self.sounds_edit_key is not None:
        self.sounds_edit_key = None
      else:
        self.selected = Destination.STAR
        self.sounds_scroll = 0
      self.input.cancel()
    elif action.kind == "open" and state.page == "sounds:overview" and action.row in state.rows and action.row.available:
      self.sounds_edit_key = action.row.key
      self.input.cancel()
    elif action.kind == "scroll" and self.sounds_edit_key is None:
      self.sounds_scroll = feature_scroll(self.sounds_scroll, action.direction, len(state.rows))
    elif action.kind in ("change", "action") and state.page == "sounds" and action.row is not None and action.row in state.rows:
      request = value_request(state, action)
      if request is not None:
        self.sounds_request(request)
    self._snapshot_cache = None

  def _feature_ui(self, action: FeatureUiAction) -> None:
    if self._mode != ShellMode.SETTINGS or self.selected != Destination.DRIVING_CONTROLS:
      return
    state = self.feature_snapshot()
    if action.kind == "back":
      self._lane_change_request_epoch = getattr(self, "_lane_change_request_epoch", 0) + 1
      if getattr(self, "feature_edit_key", None) is not None:
        self.feature_edit_key = None
      else:
        parent = feature_parent_page(self.feature_page)
        if parent is None or self.feature_page == self.feature_root_page:
          self.selected = Destination.STAR
        else:
          self.feature_page = parent
        self.feature_scroll = 0
      self.input.cancel()
    elif action.kind == "scroll" and not state.editor:
      self.feature_scroll = feature_scroll(self.feature_scroll, action.direction, len(state.rows))
    elif action.kind == "open" and not state.editor and action.row in state.rows and action.row.page:
      self._lane_change_request_epoch = getattr(self, "_lane_change_request_epoch", 0) + 1
      if action.row.presets:
        if not action.row.available:
          return
        self.feature_edit_key = action.row.key
      elif action.row.page == SELECTION_PAGE:
        self._vehicle_selector().open(action.row.source)
      else:
        self.feature_page = action.row.page
        self.feature_scroll = 0
      self.input.cancel()
    elif action.kind in ("change", "action") and action.row is not None and action.row in state.rows:
      request = value_request(state, action)
      if request is not None:
        if request.key in VEHICLE_BOOL_KEYS:
          self._confirm_vehicle_bool(request)
        elif request.key in ("CustomCruise", "CustomCruiseLong", "ToyotaRadarMode"):
          self._confirm_vehicle_bool(request, f"Set {action.row.label.lower()} to {request.value} {action.row.unit}".rstrip() + "?")
        elif request.key in LANE_CHANGE_KEYS:
          self._confirm_lane_change(request)
        else:
          self.feature_request(request)
    elif action.kind == "reset" and action.row is not None and action.row in state.rows:
      self._confirm_feature_reset(action.row)
    self._snapshot_cache = None

  def _confirm_feature_reset(self, row) -> None:
    from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
    from openpilot.system.ui.widgets import DialogResult
    from openpilot.starpilot.ui.feature_settings_state import CURVE_CONFIRM_ACTIONS, CONDITIONAL_CONFIRM_ACTIONS, TORQUE_CONFIRM_ACTIONS, is_long_confirm_action
    from openpilot.starpilot.ui.long_profile_feature import long_confirm_question
    from openpilot.starpilot.ui.controller_feature import SETUP_ACTION, SETUP_QUESTION
    action_key = row.key if (row.key in TORQUE_CONFIRM_ACTIONS or row.key in ("slc_reset", LANE_CHANGE_RESET, SETUP_ACTION) or
                             row.key in CURVE_CONFIRM_ACTIONS | CONDITIONAL_CONFIRM_ACTIONS or is_long_confirm_action(row.key)) else "reset_profiles"
    if action_key in CONDITIONAL_CONFIRM_ACTIONS:
      self._confirm_conditional(FeatureSettingsRequest(action_key, row.source, "confirm", confirmation=True,
                                                       vehicle_fingerprint=row.vehicle_fingerprint,
                                                       capability=row.capability, dependencies=row.dependencies))
      return
    if action_key == LANE_CHANGE_RESET:
      self._confirm_lane_change(FeatureSettingsRequest(action_key, row.source, "confirm",
                                                       vehicle_fingerprint=row.vehicle_fingerprint, capability=row.capability))
      return
    question = {"torque_adopt": "Use the new torque editor for this vehicle model and configuration? Existing saved values will be kept but no longer used.",
                SETUP_ACTION: SETUP_QUESTION,
                "torque_reset": "Reset invalid torque profiles? Existing legacy values will be kept but remain inactive.",
                "torque_reset_profile": "Reset this vehicle model's custom torque profile to Vehicle/learned? Other models and legacy values remain saved.",
                "torque_gain_rebase": "Apply the saved steering response to the selected controller and current vehicle tune? " +
                                     "Friction and other models stay unchanged.",
                "torque_rebase": f"Reapply saved torque values ({row.value}) with the current vehicle tune? " +
                                 "Saved values must pass current limits.",
                "slc_reset": f"Reset speed-limit offsets to zero? {row.value}. " +
                             "Saved SLC control may resume if its switch is On; the switch will not change.",
                "curve_reset": "Reset saved Curve learning? Your Curve Speed Controller On/Off choice will stay the same.",
                "reset_profiles": "Reset invalid saved Long Planner profiles to defaults?"}.get(action_key, long_confirm_question(row))
    def confirmed(result: DialogResult) -> None:
      if result == DialogResult.CONFIRM:
        self.feature_request(FeatureSettingsRequest(action_key, row.source, "confirm", confirmation=True,
                                                     related_source=row.related_source,
                                                     vehicle_fingerprint=row.vehicle_fingerprint,
                                                     capability=row.capability, dependencies=row.dependencies))
    if action_key == "torque_adopt":
      button = "Continue"
    elif action_key in ("torque_rebase", "torque_gain_rebase"):
      button = "Review"
    elif action_key == SETUP_ACTION:
      button = "Prepare"
    else:
      button = "Restore" if action_key.startswith("long_repair:") else "Reset"
    gui_app.push_widget(ConfirmDialog(question, button, callback=confirmed))

  def _confirm_lane_change(self, request: FeatureSettingsRequest) -> None:
    from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
    from openpilot.system.ui.widgets import DialogResult
    epoch = self._lane_change_request_epoch
    question = lane_change_question(request)
    def confirmed(result: DialogResult) -> None:
      if (result == DialogResult.CONFIRM and self._mode == ShellMode.SETTINGS and
          self.selected == Destination.DRIVING_CONTROLS and self.feature_page == FeaturePage.LANE_CHANGE and
          epoch == self._lane_change_request_epoch):
        self.feature_request(replace(request, confirmation=True))
    gui_app.push_widget(ConfirmDialog(question, "Save", callback=confirmed))

  def _confirm_vehicle_bool(self, request: FeatureSettingsRequest, question: str | None = None) -> None:
    from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
    from openpilot.system.ui.widgets import DialogResult
    page = self.feature_page
    epoch = self._lane_change_request_epoch
    question = question or vehicle_question(request)
    def confirmed(result: DialogResult) -> None:
      if result == DialogResult.CONFIRM and self._mode == ShellMode.SETTINGS and \
         self.selected == Destination.DRIVING_CONTROLS and self.feature_page == page and \
         epoch == self._lane_change_request_epoch:
        self.feature_request(replace(request, confirmation=True))
    gui_app.push_widget(ConfirmDialog(question, "Save", callback=confirmed))


  def _confirm_conditional(self, request: FeatureSettingsRequest) -> None:
    from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
    from openpilot.system.ui.widgets import DialogResult
    page = self.feature_page
    epoch = self._lane_change_request_epoch
    question = conditional_question(request)
    def confirmed(result: DialogResult) -> None:
      if result == DialogResult.CONFIRM and self._mode == ShellMode.SETTINGS and \
         self.selected == Destination.DRIVING_CONTROLS and self.feature_page == page and \
         epoch == self._lane_change_request_epoch:
        self.feature_request(replace(request, confirmation=True))
    gui_app.push_widget(ConfirmDialog(question, "Save", callback=confirmed))

  def _live_slc_message(self, now_ns: int) -> Any | None:
    ui = self.adapter.ui_state
    if not ui.started:
      return None
    device = current_message(ui.sm, "deviceState", now_ns)
    pandas = current_message(ui.sm, "pandaStates", now_ns)
    if (device is None or not device.started or pandas is None or
        not any(p.ignitionLine or p.ignitionCan for p in pandas)):
      return None
    after = ui.started_frame
    car = current_message(ui.sm, "carState", now_ns, after_frame=after)
    control = current_message(ui.sm, "carControl", now_ns, after_frame=after)
    controls = current_message(ui.sm, "controlsState", now_ns, after_frame=after)
    selfdrive = current_message(ui.sm, "selfdriveState", now_ns, after_frame=after)
    cp = ui.CP
    if (car is None or not getattr(car, "canValid", False) or getattr(car, "canTimeout", True) or
        control is None or controls is None or selfdrive is None or cp is None):
      return None
    return current_message(ui.sm, "slcState", now_ns, after_frame=ui.started_frame)

  def snapshot(self, mode: ShellMode) -> ShellSnapshot:
    key = (self.adapter.ui_state.sm.frame, mode, self.selected,
           self.compact_y, self.compact_scroll_x, self.sidebar_expanded)
    if self._snapshot_cache is not None and self._snapshot_cache[0] == key:
      return self._snapshot_cache[1]
    snapshot = self.adapter.build(mode, self.selected, compact_y=self.compact_y,
                                  compact_scroll_x=self.compact_scroll_x, sidebar_expanded=self.sidebar_expanded,
                                  menu_only=self.profile == Profile.COMPACT and mode == ShellMode.SETTINGS,
                                  onroad_view_only=mode == ShellMode.ONROAD, stopped_timer_profile=str(self.profile))
    if mode == ShellMode.ONROAD and self.profile == Profile.LARGE:
      width = getattr(self, '_onroad_page_width', 2160.0) - (SIDEBAR_WIDTH if snapshot.onroad.appearance.show_developer_sidebar else 0)
      fitted = fit_to_road(snapshot.onroad.customization, width)
      if snapshot.onroad.viewport_width != width or fitted is not snapshot.onroad.customization:
        snapshot = replace(snapshot, onroad=replace(snapshot.onroad, viewport_width=width, customization=fitted))
    if mode == ShellMode.HOME:
      now = time.monotonic()
      previous = getattr(self, "_home_model", None)
      if previous is None or not 0 <= now - previous[0] < 1.0:
        try:
          if self.model_source is None:
            self.model_source = ModelStatusSource(self.adapter.ui_state.params)
          label = home_model_label(self.model_source.snapshot(), snapshot.home.commit if self.profile == Profile.COMPACT else "")
        except (OSError, RuntimeError, ValueError):
          label = "Driving model unavailable"
        self._home_model = (now, label)
      local_label = label if previous is None or not 0 <= now - previous[0] < 1.0 else previous[1]
      snapshot = replace(snapshot, home=replace(snapshot.home, model_label=snapshot.home.model_label or local_label))
    preview_flags = getattr(self, "_visual_preview_flags", frozenset())
    if mode == ShellMode.ONROAD and preview_flags:
      visual = preview_at(preview_flags,
                          max(0, time.monotonic_ns() - self._visual_preview_start_ns) / 1e9)
      snapshot = replace(snapshot, onroad=replace(snapshot.onroad, visual_preview=visual))
    if self.profile == Profile.COMPACT and mode == ShellMode.SETTINGS:
      drive = self.drive_state.snapshot()
      availability = tuple(replace(item, available=item.destination in
                                   (Destination.TOGGLES, Destination.DEVICE, Destination.SOFTWARE,
                                    Destination.DRIVING_MODEL, Destination.DRIVING_CONTROLS, Destination.APPEARANCE, Destination.GALAXY,
                                    Destination.NETWORK, Destination.BLUETOOTH, Destination.VEHICLE, Destination.DEVELOPER) or
                                   (item.destination == Destination.FORCE_DRIVE and drive["available"]) or
                                   snapshot.device.offroad and item.destination == Destination.PAIR)
                           for item in snapshot.settings.availability)
      next_mode = DriveStateControl.next_mode(drive)
      label = ("unavailable" if not drive["available"] else
               {"offroad": "Force Off-road", "onroad": "Force On-road", "auto": "Return to Auto"}[next_mode.value])
      availability = tuple(replace(item, request_value=next_mode.value, request_revision=drive["revision"], reason=None)
                           if item.destination == Destination.FORCE_DRIVE else item for item in availability)
      snapshot = replace(snapshot, settings=replace(snapshot.settings, availability=availability, nav_bar_alpha=0,
                                                   force_drive_label=label))
    if self.profile == Profile.LARGE and mode == ShellMode.SETTINGS and self.selected == Destination.DRIVING_CONTROLS:
      feature = self.feature_snapshot()
      if not feature.editor:
        self.feature_scroll = feature_scroll(self.feature_scroll, 0, len(feature.rows))
      feature = replace(feature, scroll=0 if feature.editor else self.feature_scroll, sidebar_expanded=self.sidebar_expanded,
                        parent_title=feature.parent_title if feature.editor else
                        "StarPilot" if self.feature_page == self.feature_root_page else feature_parent_title(self.feature_page))
      snapshot = replace(snapshot, features=feature)
    if self.profile == Profile.LARGE and mode == ShellMode.SETTINGS and self.selected == Destination.SOUNDS:
      sounds = self.sounds_snapshot()
      if self.sounds_edit_key is None:
        self.sounds_scroll = feature_scroll(self.sounds_scroll, 0, len(sounds.rows))
      sounds = replace(sounds, scroll=self.sounds_scroll if self.sounds_edit_key is None else 0, sidebar_expanded=self.sidebar_expanded)
      snapshot = replace(snapshot, sounds=sounds)
    if self.profile == Profile.LARGE and mode == ShellMode.SETTINGS and self.selected == Destination.APPEARANCE:
      appearance = self.appearance_snapshot()
      if not appearance.editor:
        self.appearance_scroll = feature_scroll(self.appearance_scroll, 0, len(appearance.rows))
      appearance = replace(appearance, scroll=0 if appearance.editor else self.appearance_scroll, sidebar_expanded=self.sidebar_expanded,
                           parent_title=appearance.parent_title if appearance.editor else
                           "Appearance" if getattr(self, "appearance_page", "appearance") == "pip" else "StarPilot")
      snapshot = replace(snapshot, appearance=appearance)
    if self.profile == Profile.LARGE and mode == ShellMode.SETTINGS and self.selected == Destination.SYSTEM:
      display = self.system_snapshot()
      if not display.editor:
        self.display_scroll = feature_scroll(self.display_scroll, 0, len(display.rows))
      display = replace(display, scroll=0 if display.editor else self.display_scroll, sidebar_expanded=self.sidebar_expanded)
      snapshot = replace(snapshot, display=display)
    if self.profile == Profile.LARGE and mode == ShellMode.SETTINGS and self.selected == Destination.DRIVING_MODEL:
      models = self.model_snapshot()
      self.model_scroll = feature_scroll(self.model_scroll, 0, len(models.rows))
      models = replace(models, scroll=self.model_scroll, sidebar_expanded=self.sidebar_expanded)
      snapshot = replace(snapshot, models=models)
    self._snapshot_cache = (key, snapshot)
    return snapshot

  def render(self, mode: ShellMode, rect: rl.Rectangle, parent_clip: rl.Rectangle | None = None) -> None:
    self._poll_aol_save()
    if mode == ShellMode.ONROAD and self.profile == Profile.LARGE:
      if rect.width != self._onroad_page_width:
        self._onroad_page_width = rect.width
        self._snapshot_cache = None
    snapshot = self.snapshot(mode)
    self._mode = mode
    if mode == ShellMode.ONROAD:
      snapshot = replace(snapshot, onroad=self.input.onroad.present(snapshot.onroad))
    if mode == ShellMode.ONROAD and self.slc_actions is not None:
      observation = snapshot.onroad.speed_limit
      snapshot = replace(snapshot, onroad=replace(snapshot.onroad, speed_limit=replace(
        observation, action_feedback=self.slc_actions.feedback(observation))))
    elif mode != ShellMode.ONROAD and self.slc_actions is not None:
      self.slc_actions.clear_feedback()
    rendered = getattr(self, "_rendered_settings", None)
    if mode == ShellMode.SETTINGS and self.profile == Profile.LARGE and (rendered is None or rendered[1].selected != snapshot.selected):
      if pane := _FEATURE_SETTINGS_PANES.get(snapshot.selected):
        getattr(self.view, pane[0]).reset()
    self._rendered_settings = (time.monotonic_ns(), snapshot) if mode == ShellMode.SETTINGS else None
    self._rendered_settings_pipeline = (bool(ui_state.started), ui_state.started_frame) if mode == ShellMode.SETTINGS else None
    if mode == ShellMode.ONROAD:
      self._update_favorites(snapshot.onroad, time.monotonic())
      self._poll_wheel(time.monotonic_ns())
      if self.profile == Profile.LARGE and self.favorites.is_open:
        snapshot = replace(snapshot, onroad=self.favorites.present(snapshot.onroad))
    else:
      self.favorites.cancel()
    if mode != ShellMode.ONROAD or not snapshot.onroad.camera_available:
      self.pip_warning.close()
      self._pip_signals_cache = None
      self._pip_saved = None
      self._vasm_saved = None
      self._pip_read_ns = None
      if renderer := getattr(self, "pip_renderer", None):
        renderer.deactivate()
    else:
      self._pip_signals_cache = self._pip_signals()
      snapshot = replace(snapshot, onroad=visual_border_state(snapshot.onroad, self._pip_signals_cache))
    # Scroller and NavWidget place compact pages at changing screen positions.
    with placed_at(rect, parent_clip):
      external = (self.profile == Profile.LARGE and mode == ShellMode.SETTINGS and
                  snapshot.selected in NATIVE_SETTINGS_PANELS and self.settings_layer is not None)
      pane = _FEATURE_SETTINGS_PANES.get(snapshot.selected)
      if pane and self.profile == Profile.LARGE:
        getattr(self.view, pane[0]).pressed_action = getattr(self.input, pane[2]).pressed_action
      drag_x = getattr(self.input, pane[2]).drag_x if pane else 0.0
      self.view.render(replace(snapshot, selected=Destination.NETWORK) if external else snapshot, drag_x=drag_x)
      self._pip_signals_cache = None
      if mode == ShellMode.ONROAD and self.profile == Profile.COMPACT:
        self.favorites.render(time.monotonic())
    network = (mode == ShellMode.SETTINGS and self.profile == Profile.LARGE and snapshot.selected == Destination.NETWORK and
               snapshot.settings.destination(Destination.NETWORK).available and self.network_layer is not None)
    if network:
      rail_width = 500 if snapshot.settings.sidebar_expanded else 0
      content = rl.Rectangle(rect.x + rail_width + 50, rect.y + 25,
                             rect.width - rail_width - 100, rect.height - 50)
      if not self.network_layer(content) and self.selected == Destination.NETWORK:
        self.input.cancel()
        self.selected = Destination.STAR
        self._snapshot_cache = None
        if self._on_destination_change is not None:
          self._on_destination_change(Destination.STAR)
        self._unavailable("network panel is unavailable")

    if external:
      rail_width = 500 if snapshot.settings.sidebar_expanded else 0
      content = rl.Rectangle(rect.x + rail_width + 50, rect.y + 25, rect.width - rail_width - 100, rect.height - 50)
      self.settings_layer(snapshot.selected, content)

    if external or network or self.notice:
      with placed_at(rect, parent_clip):
        if external or network:
          self.view.settings.render_rail(snapshot.settings, selected=snapshot.selected)
        if self.notice and time.monotonic() < self.notice_until:
          rl.draw_rectangle(0, self.profile.size[1] - 42, self.profile.size[0], 42, rl.Color(0, 0, 0, 220))
          self.fonts.draw(self.notice[:65], FontRole.MEDIUM, 23 if self.profile == Profile.COMPACT else 27,
                          12, self.profile.size[1] - 37)

  def _unavailable(self, label: str) -> None:
    self.notice = f"Unavailable: {label}"
    self.notice_until = time.monotonic() + 3.0

  def _emit(self, request: ShellRequest) -> None:
    self._request_emitted = True
    previous_destination = self.selected
    action = request.action
    if request.source == 'onroad' and getattr(action, 'kind', None) == 'cycle_personality':
      self._native_favorite_actions()
      if self._wheel_personality is not None:
        self._wheel_personality()
      return
    if request.source == "onroad" and getattr(action, "kind", None) == "set_experimental":
      binding = self._native_favorite_actions().get(EXPERIMENTAL)
      if not (binding is not None and binding.available and binding.invoke is not None and
              action.action_token == binding.token and binding.invoke()):
        self._unavailable("experimental shortcut in this context")
      return
    if request.source == "home":
      if action.kind == HomeActionKind.OPEN_SETTINGS and self._on_settings is not None:
        self.selected = Destination.STAR
        self._on_settings()
      elif action.kind == HomeActionKind.OPEN_TOGGLES and self.profile == Profile.LARGE:
        self.selected = Destination.TOGGLES
        self._on_settings()
      elif (action.kind == HomeActionKind.OPEN_PAIRING and self.profile == Profile.LARGE and
            self._on_pairing is not None and not self.snapshot(ShellMode.HOME).home.paired and self.confirmed_offroad()):
        self._on_pairing()
      elif action.kind == HomeActionKind.OPEN_PAIRING:
        self._unavailable("device pairing")
      else:
        self._unavailable("experimental mode owner")
    elif request.source == "settings":
      if action.kind == SettingsActionKind.CLOSE:
        if self.selected == Destination.DRIVING_CONTROLS and getattr(self, "feature_edit_key", None) is not None:
          self._feature_ui(FeatureUiAction("back"))
        elif self.selected == Destination.APPEARANCE and getattr(self, "appearance_edit_key", None) is not None:
          self._appearance_ui(FeatureUiAction("back"))
        elif self.selected == Destination.SYSTEM and (getattr(self, "display_edit_key", None) is not None or
                                                   getattr(self, "display_page", "system") == "display"):
          self._display_ui(FeatureUiAction("back"))
        elif self.selected != Destination.STAR:
          self.selected = Destination.STAR
        elif self._on_home is not None:
          self._on_home()
      elif action.kind == SettingsActionKind.TOGGLE_SIDEBAR:
        self.sidebar_expanded = not self.sidebar_expanded
      elif action.destination is not None:
        destination = action.destination.destination
        if destination == Destination.FORCE_DRIVE and action.destination.available:
          self._drive_change(action.destination.request_value, action.destination.request_revision)
        elif destination == Destination.STAR:
          self.selected = Destination.STAR
        elif action.destination.available and self.profile == Profile.LARGE:
          self.selected = destination
          if destination in (Destination.DRIVING_CONTROLS, Destination.VEHICLE):
            self.selected = Destination.DRIVING_CONTROLS
            self.feature_page = self.feature_root_page = FeaturePage.VEHICLE if destination == Destination.VEHICLE else FeaturePage.HUB
            self.feature_scroll = 0
            self.feature_edit_key = None
          elif destination == Destination.SOUNDS:
            self.sounds_scroll = 0
            self.sounds_edit_key = None
          elif destination == Destination.APPEARANCE:
            self.appearance_scroll = 0
            self.appearance_page = "appearance"
            self.appearance_edit_key = None
          elif destination == Destination.SYSTEM:
            self.display_scroll = 0
            self.display_page = "system"
            self.display_edit_key = None
          elif destination == Destination.DRIVING_MODEL:
            self.model_scroll = 0
        elif action.destination.available and self._on_compact_destination is not None:
          self._on_compact_destination(destination)
        else:
          self._unavailable(destination.value)
    elif (request.source in ("toggles", "device", "software") and self._mode == ShellMode.SETTINGS and
          self.profile == Profile.LARGE and self._request_owner is not None and self._request_owner(action)):
      self._snapshot_cache = None  # the owner acknowledged; read its state on the next frame
    elif request.source in ("features", "sounds", "appearance", "display", "models") and isinstance(action, FeatureUiAction) and action.kind == "details":
      self._feature_details(request.source, action)
    elif request.source == "features" and isinstance(action, FeatureUiAction):
      self._feature_ui(action)
    elif request.source == "sounds" and isinstance(action, FeatureUiAction):
      self._sounds_ui(action)
    elif request.source == "appearance" and isinstance(action, FeatureUiAction):
      self._appearance_ui(action)
    elif request.source == "display" and isinstance(action, FeatureUiAction):
      self._display_ui(action)
    elif request.source == "models" and isinstance(action, FeatureUiAction):
      self._models_ui(action)
    elif request.source == "slc" and isinstance(action, SlcUiRequest):
      if self.slc_actions is None or not self.slc_actions.dispatch(action):
        self._unavailable("speed limit action")
    else:
      self._unavailable(request.source + " request")
    if self.selected != previous_destination:
      self._power_request_epoch = getattr(self, "_power_request_epoch", 0) + 1
      self._pip_request_epoch = getattr(self, "_pip_request_epoch", 0) + 1
      self._lane_change_request_epoch = getattr(self, "_lane_change_request_epoch", 0) + 1
      if self._on_destination_change is not None:
        self.input.cancel()
        self._on_destination_change(self.selected)

  def _feature_details(self, source: str, action: FeatureUiAction) -> None:
    destination, snapshot = {"features": (Destination.DRIVING_CONTROLS, self.feature_snapshot),
                             "sounds": (Destination.SOUNDS, self.sounds_snapshot),
                             "appearance": (Destination.APPEARANCE, self.appearance_snapshot),
                             "display": (Destination.SYSTEM, self.system_snapshot),
                             "models": (Destination.DRIVING_MODEL, self.model_snapshot)}[source]
    if self._mode != ShellMode.SETTINGS or self.selected != destination:
      return
    state = snapshot()
    row = action.row
    if row is not None:
      if row not in state.rows:
        return
      value = row.value + (" " + row.unit if row.unit and row.value != "Auto" else "")
      parts = (row.label, value, row.reason, f"Set {row.repair_value}" if row.repair_value else "")
    else:
      parts = (state.title, state.subtitle)
    from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
    dialog = ConfirmDialog("".join(f"<p>{escape(part)}</p>" for part in parts if part), "Close", cancel_text="", rich=True)
    # Native scrolling preserves full explanations; decode literals after the HTML parser.
    for element in dialog._html_renderer.elements:
      element.content = unescape(element.content)
      element.font_size = 62  # Match size-50 bitmap body text at the large profile scale.
    self.input.cancel()
    gui_app.push_widget(dialog)

  def _input_snapshot(self, mode: ShellMode) -> ShellSnapshot:
    snapshot = self.snapshot(mode)
    if mode == ShellMode.ONROAD and self.profile == Profile.LARGE:
      binding = self._native_favorite_actions().get(EXPERIMENTAL)
      snapshot = replace(snapshot, onroad=replace(
        snapshot.onroad, experimental_available=bool(binding is not None and binding.available),
        experimental_action_token=binding.token if binding is not None else ""))
    return snapshot

  def _settings_press_snapshot(self) -> ShellSnapshot:
    rendered = getattr(self, "_rendered_settings", None)
    now_ns = time.monotonic_ns()
    if (rendered is not None and 0 <= now_ns - rendered[0] <= 250_000_000 and
        rendered[1].selected == self.selected and
        (not rendered[1].device.offroad or self.confirmed_offroad()) and
        getattr(self, "_rendered_settings_pipeline", None) == (bool(ui_state.started), ui_state.started_frame)):
      snapshot = rendered[1]
    else:
      snapshot = self.snapshot(ShellMode.SETTINGS)
    self._settings_touch = snapshot
    self._settings_pipeline = (bool(ui_state.started), ui_state.started_frame)
    return snapshot

  def _settings_gesture_snapshot(self) -> ShellSnapshot | None:
    snapshot = getattr(self, "_settings_touch", None)
    if snapshot is None or snapshot.selected != self.selected:
      self.cancel()
      return None
    if (pane := _FEATURE_SETTINGS_PANES.get(self.selected)) and self.profile == Profile.LARGE:
      state = getattr(snapshot, pane[0])
      edit_key = getattr(self, pane[1] + "_edit_key", None)
      scroll = 0 if edit_key is not None else getattr(self, pane[1] + "_scroll")
      if (state.scroll != scroll or
          state.editor != (edit_key is not None) or (state.editor and state.rows[0].key != edit_key) or
          state.page != getattr(self, pane[1] + "_page", state.page) or
          state.sidebar_expanded != self.sidebar_expanded):
        self.cancel()
        return None
    if snapshot.device.offroad and not self.confirmed_offroad():
      self.cancel()
      return None
    if getattr(self, "_settings_pipeline", None) != (bool(ui_state.started), ui_state.started_frame):
      self.cancel()
      return None
    return snapshot

  def press(self, mode: ShellMode, x: float, y: float) -> None:
    self._request_emitted = False
    self._mode = mode
    if mode == ShellMode.SETTINGS:
      snapshot = self._settings_press_snapshot()
      self.input.press(x, y, time.monotonic(), snapshot)
      if (pane := _FEATURE_SETTINGS_PANES.get(snapshot.selected)) and self.profile == Profile.LARGE:
        state = getattr(snapshot, pane[0])
        if getattr(self.view, pane[0]).reset(state) and FeatureInput._in_body(x, y, state):
          getattr(self.input, pane[2]).cancel_tap()
      return
    now, snapshot = time.monotonic(), self.snapshot(mode)
    self._favorite_claimed = False
    self._navigation_claimed = False
    self._onroad_claimed = False
    if mode == ShellMode.ONROAD:
      self._update_favorites(snapshot.onroad, now)
      if self.favorites.is_open or self.favorites.claims_corner(x, y):
        self._favorite_claimed = self.favorites.press(x, y, now)
        self.input.cancel()
        return
    self.input.press(x, y, now, self._input_snapshot(mode))
    self._onroad_claimed = mode == ShellMode.ONROAD and self.input.onroad.claimed
    if mode == ShellMode.ONROAD and not self.input.onroad.claimed:
      if self.view.onroad.navigation.press(x, y, snapshot.onroad):
        self._navigation_claimed = True
        self.input.cancel()
        return
      self._favorite_claimed = self.favorites.press(x, y, now)
      if self._favorite_claimed:
        self.input.cancel()

  def move(self, mode: ShellMode, x: float, y: float, *, timestamp: float | None = None) -> None:
    self._mode = mode
    if mode == ShellMode.SETTINGS:
      if snapshot := self._settings_gesture_snapshot():
        self.input.move(x, y, timestamp, snapshot)
      return
    if getattr(self, "_navigation_claimed", False):
      self.view.onroad.navigation.move(x, y, self.snapshot(mode).onroad)
      return
    if self._favorite_claimed:
      self._update_favorites(self.snapshot(mode).onroad, time.monotonic())
      self.favorites.move(x, y)
      return
    self.input.move(x, y, time.monotonic(), self._input_snapshot(mode))

  def release(self, mode: ShellMode, x: float, y: float, *, timestamp: float | None = None) -> bool:
    self._mode = mode
    if mode == ShellMode.SETTINGS:
      snapshot = self._settings_gesture_snapshot()
      self._settings_touch = None
      if snapshot is not None:
        self.input.release(x, y, timestamp, snapshot)
      return self._request_emitted
    if getattr(self, "_navigation_claimed", False):
      self._navigation_claimed = False
      self.view.onroad.navigation.release(x, y, self.snapshot(mode).onroad)
      return True
    if self._favorite_claimed:
      self._favorite_claimed = False
      now = time.monotonic()
      self._update_favorites(self.snapshot(mode).onroad, now)
      self.favorites.release(x, y, now)
      return True
    self.input.release(x, y, time.monotonic(), self._input_snapshot(mode))
    return self._request_emitted or getattr(self, '_onroad_claimed', False)

  def cancel(self) -> None:
    if pending := getattr(self, "_aol_save", None):
      pending.cancel.set()
    self._settings_touch = None
    if (pane := _FEATURE_SETTINGS_PANES.get(self.selected)) and self.profile == Profile.LARGE:
      getattr(self.view, pane[0]).reset()
    self._power_request_epoch = getattr(self, "_power_request_epoch", 0) + 1
    self._pip_request_epoch = getattr(self, "_pip_request_epoch", 0) + 1
    self._lane_change_request_epoch = getattr(self, "_lane_change_request_epoch", 0) + 1
    self.input.cancel()
    self.favorites.cancel()
    self.view.onroad.navigation.cancel()
    self._navigation_claimed = False
    self._favorite_claimed = False

  def close(self) -> None:
    if layer := getattr(self, 'onroad_jetlink', None):
      layer.close()
      self.onroad_jetlink = None
    if layer := getattr(self, 'onroad_map', None):
      layer.close()
      self.onroad_map = None
    if receiver := getattr(self, '_projection_bookmarks', None):
      receiver.close()
      self._projection_bookmarks = None
    if receiver := getattr(self, '_projection_slc', None):
      receiver.close()
      self._projection_slc = None
    self._aol_save_closed = True
    if pending := getattr(self, "_aol_save", None):
      pending.cancel.set()
    self.drive_state.physical.close()
    self.slc_actions = None
    self.galaxy_flow.close()
    if getattr(self, 'model_manager', None) is not None:
      self.model_manager.close()
    if self.bluetooth_source is not None:
      self.bluetooth_source.close()
    self.pip_warning.close()
    self.pip_renderer.close()
    if self.model_source is not None:
      self.model_source.close()
      self.model_source = None
    if self.map_source is not None:
      self.map_source.close()
      self.map_source = None
    self.view.close()
    self.fonts.close()


class StarShellPage(Widget):
  def __init__(self, session: StarShellSession, mode: ShellMode, *, on_background_tap: Any = None):
    super().__init__()
    self.session, self.mode = session, mode
    self.on_background_tap = on_background_tap
    self._press_pos: tuple[float, float] | None = None
    self._dragged = False
    self._touch_time: float | None = None

  def _render(self, rect: rl.Rectangle) -> None:
    self.session.render(self.mode, rect, self._parent_rect)
    if self.mode == ShellMode.ONROAD and self.session.profile == Profile.COMPACT:
      bookmark = self.session.camera_owner._bookmark_icon
      bookmark.set_touch_valid_callback(lambda: self._touch_valid() and abs(self.rect.x) < 1)
      bookmark.set_parent_rect(self._parent_rect or self.rect)
      bookmark.render(rect)

  def _handle_mouse_press(self, mouse_pos: MousePos) -> None:
    self._press_pos = (mouse_pos.x, mouse_pos.y)
    self._dragged = False
    self._touch_time = None
    self.session.press(self.mode, mouse_pos.x - self.rect.x, mouse_pos.y - self.rect.y)

  def _handle_mouse_event(self, mouse_event: MouseEvent) -> None:
    self._touch_time = mouse_event.t
    if mouse_event.left_down and (not mouse_event.left_pressed or self.mode == ShellMode.SETTINGS):
      self._track_background_drag(mouse_event.pos)
      self.session.move(self.mode, mouse_event.pos.x - self.rect.x, mouse_event.pos.y - self.rect.y, timestamp=mouse_event.t)
    elif mouse_event.left_released and not rl.check_collision_point_rec(mouse_event.pos, self.rect):
      self.session.cancel()

  def _handle_mouse_release(self, mouse_pos: MousePos) -> None:
    self._track_background_drag(mouse_pos)
    handled = self.session.release(self.mode, mouse_pos.x - self.rect.x, mouse_pos.y - self.rect.y, timestamp=self._touch_time)
    bookmark_handled = (self.mode == ShellMode.ONROAD and self.session.profile == Profile.COMPACT and
                        self.session.camera_owner._bookmark_icon.interacting())
    if not handled and not self._dragged and not bookmark_handled and self.on_background_tap is not None:
      self.on_background_tap()
    self._press_pos = None
    self._dragged = False

  def _track_background_drag(self, pos: MousePos) -> None:
    tolerance = 36 if self.session.profile == Profile.LARGE else 5
    if self._press_pos is not None and math.hypot(pos.x - self._press_pos[0], pos.y - self._press_pos[1]) > tolerance:
      self._dragged = True

  def hide_event(self) -> None:
    self.session.cancel()
    if self.mode == ShellMode.ONROAD and self.session.profile == Profile.COMPACT:
      self.session.camera_owner._bookmark_icon.hide_event()
    self._press_pos = None
    self._dragged = False
    super().hide_event()


class StarMainLayout(MainLayout):
  """Large existing app transitions/onboarding with full-canvas StarPilot views."""

  def __init__(self):
    validate_runtime_fonts(Profile.LARGE)
    validate_runtime_assets()
    super().__init__()
    self._native_onroad = self._layouts[MainState.ONROAD]
    from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
    native_network = self._layouts[MainState.SETTINGS]._panels[PanelType.NETWORK].instance
    self._network_bridge = NetworkPanelBridge(native_network, self._network_authority)
    self._large_panels = {
      destination: self._layouts[MainState.SETTINGS]._panels[panel].instance
      for destination, panel in ((Destination.DEVICE, PanelType.DEVICE), (Destination.SOFTWARE, PanelType.SOFTWARE),
                                  (Destination.TOGGLES, PanelType.TOGGLES), (Destination.DEVELOPER, PanelType.DEVELOPER))
    }
    self._large_destination = None
    try:
      self.star = StarShellSession(Profile.LARGE, self._native_onroad, network_layer=self._network_bridge.render, settings_layer=self._render_large_panel)
    except Exception:
      self._native_onroad.close()
      raise
    self.page = StarShellPage(self.star, ShellMode.HOME, on_background_tap=self._on_background_tap)
    device_scroller = self._large_panels[Destination.DEVICE]._scroller
    device_scroller._items.remove(self._large_panels[Destination.DEVICE]._power_off_btn)
    self.star.system_power_action = self._system_power_action
    device_scroller.add_widget(
      button_item("Galaxy", "OPEN", callback=self.star.galaxy_flow.open_large))
    device_scroller._items.insert(0, device_scroller._items.pop())
    from openpilot.starpilot.ui.replay_settings import open_replay
    device_scroller.add_widget(button_item("Replay", "OPEN", callback=lambda: open_replay(self.star)))
    device_scroller._items.insert(1, device_scroller._items.pop())
    toggle_items = self._large_panels[Destination.TOGGLES]._scroller._items
    toggle_items.insert(2, text_item("Safe Mode", "N/A"))
    from openpilot.common.hardware import HARDWARE
    from openpilot.starpilot.ui.starpilot_settings_adapter_large import StarPilotSettingsAdapterLarge
    for destination, owner in self._large_panels.items():
      if destination == Destination.DEVELOPER and HARDWARE.get_device_type() in ('mici', 'tici', 'tizi'):
        from openpilot.starpilot.ui.spacepilot.large import SpacePilotDeveloperAdapter
        self._large_panels[destination] = SpacePilotDeveloperAdapter(owner, self.star.fonts, self._settings_back, self._exit_spacepilot)
      else:
        self._large_panels[destination] = StarPilotSettingsAdapterLarge(owner, self.star.fonts, destination.value.title(), self._settings_back)
    self._network_bridge.presentation = StarPilotSettingsAdapterLarge(native_network, self.star.fonts, "Network", self._settings_back)
    self.star.set_navigation(on_settings=lambda: self._set_current_layout(MainState.SETTINGS),
                             on_home=self._set_mode_for_state,
                             on_pairing=self._show_pairing,
                             on_destination_change=self._network_destination)
    self.star.set_request_owner(self._deliver_settings_request)
    self.star._native_favorite_actions = self._native_favorites
    self.star._publish_bookmark = self._on_bookmark_clicked

  def _exit_spacepilot(self) -> None:
    gui_app.pop_widgets_to(self, instant=True)
    self._set_mode_for_state()

  def _native_favorites(self):
    from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
    return self.star.native_favorite_actions(
      lambda: (self._on_bookmark_clicked() or True),
      lambda index: self._panel_owner(PanelType.TOGGLES).request_personality(index),
      self._native_onroad._hud_renderer._exp_button.request_toggle)

  def _confirmed_offroad(self) -> bool:
    # Cached touch snapshots need fresh transport and native gates before effects.
    return bool(ui_state.is_offroad() and self.star.adapter.confirmed_offroad())

  def _arabesque_allowed(self) -> bool:
    return self._confirmed_offroad()

  def _system_power_action(self, index: int) -> None:
    from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
    def allowed() -> bool:
      return (self._current_mode == MainState.SETTINGS and self.star._mode == ShellMode.SETTINGS and
              self.star.selected == Destination.SYSTEM and getattr(self.star, "display_page", "system") == "system" and
              (index == 0 or self._confirmed_offroad()))
    if index in (0, 1) and allowed():
      owner = self._panel_owner(PanelType.DEVICE)
      (owner._reboot_prompt, owner._power_off_prompt)[index](allowed)

  def _show_pairing(self) -> None:
    if self._confirmed_offroad() and not ui_state.prime_state.is_paired():
      from openpilot.selfdrive.ui.widgets.pairing_dialog import PairingDialog
      gui_app.push_widget(PairingDialog())

  def _network_authority(self) -> bool:
    return self._current_mode == MainState.SETTINGS and self.star.selected == Destination.NETWORK

  def _render_large_panel(self, destination: Destination, rect) -> None:
    self._network_destination(destination)
    panel = self._large_panels.get(destination)
    if panel is not None and self._large_destination == destination:
      panel.render(rect)

  def _settings_back(self) -> None:
    self.star._emit(ShellRequest("settings", SettingsAction(SettingsActionKind.CLOSE)))

  def _leave_large_panel(self) -> None:
    if self._large_destination is not None:
      self._large_panels[self._large_destination].hide_event()
      self._large_destination = None

  def _network_destination(self, destination: Destination) -> None:
    if destination != self._large_destination:
      self._leave_large_panel()
      if destination == Destination.BLUETOOTH:
        if destination not in self._large_panels:
          from openpilot.starpilot.ui.bluetooth_large import BluetoothLarge
          from openpilot.starpilot.ui.starpilot_settings_adapter_large import StarPilotSettingsAdapterLarge
          self._large_panels[destination] = StarPilotSettingsAdapterLarge(
            BluetoothLarge(self.star.connectivity_allowed), self.star.fonts, "Bluetooth", self._settings_back)
      if destination in self._large_panels:
        self._large_destination = destination
        self._large_panels[destination].show_event()
    if destination == Destination.NETWORK:
      if not self._network_bridge.enter():
        self.star.selected = Destination.STAR
        self.star._unavailable("network panel is unavailable")
    else:
      self._network_bridge.leave()

  def _panel_owner(self, panel: Any) -> Any:
    return self._layouts[MainState.SETTINGS]._panels[panel].instance

  def _deliver_settings_request(self, request: ToggleRequest | DeviceAction | SoftwareAction) -> bool:
    if isinstance(request, ToggleRequest):
      return self.star.selected == Destination.TOGGLES and self._deliver_toggles(request)
    if isinstance(request, DeviceAction) and request.request == DeviceRequest.OPEN_GALAXY:
      return self.star.selected == Destination.DEVICE and self._deliver_device(request)
    if not self._confirmed_offroad():
      return False
    if isinstance(request, DeviceAction):
      return self.star.selected == Destination.DEVICE and self._deliver_device(request)
    if isinstance(request, SoftwareAction):
      return self.star.selected == Destination.SOFTWARE and self._deliver_software(request)
    return False

  def _deliver_toggles(self, request: ToggleRequest) -> bool:
    from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
    owner = self._panel_owner(PanelType.TOGGLES)
    if request.personality is not None:
      return owner.request_personality(list(Personality).index(request.personality))
    if request.key is None or request.value is None:
      return False
    param = {ToggleKey.ENABLED: "OpenpilotEnabledToggle", ToggleKey.EXPERIMENTAL: "ExperimentalMode",
             ToggleKey.DISENGAGE_ACCELERATOR: "DisengageOnAccelerator", ToggleKey.LANE_DEPARTURE: "IsLdwEnabled",
             ToggleKey.ALWAYS_ON_DM: "AlwaysOnDM", ToggleKey.RIGHT_HAND_DRIVING: "IsRhdDetected", ToggleKey.RECORD_FRONT: "RecordFront",
             ToggleKey.RECORD_AUDIO: "RecordAudio", ToggleKey.METRIC: "IsMetric"}.get(request.key)
    return bool(param is not None and owner.request_toggle(param, request.value))

  def _deliver_device(self, request: DeviceAction) -> bool:
    from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
    fresh = self.star.adapter.build(ShellMode.SETTINGS, Destination.DEVICE, now_ns=time.monotonic_ns()).device
    if request.request not in fresh.available_actions:
      return False
    owner = self._panel_owner(PanelType.DEVICE)
    if request.request == DeviceRequest.PREVIEW_DRIVER_CAMERA:
      from openpilot.selfdrive.ui.onroad.cabin_camera_dialog import CabinCameraDialog
      gui_app.push_widget(CabinCameraDialog())
    elif request.request == DeviceRequest.RESET_CALIBRATION:
      owner._reset_calibration_prompt(self._confirmed_offroad)
    elif request.request == DeviceRequest.OPEN_GALAXY:
      self.star.galaxy_flow.open_large()
    else:
      return False
    return True

  def _deliver_software(self, request: SoftwareAction) -> bool:
    return deliver_software(self, request, shell_mode=ShellMode, destination=Destination, clock=time,
                            software_request=SoftwareRequest, download_label=DownloadLabel)

  def _render_main_content(self) -> None:
    if self._current_mode == MainState.ARABESQUE or self._current_mode == MainState.HOME and ui_state.is_body:
      super()._render_main_content()
      return
    rect = self._rect
    if self._current_mode == MainState.ONROAD:
      self._update_layout_rects()
      if self._sidebar.is_visible:
        self._sidebar.render(self._sidebar_rect)
        if self._current_mode != MainState.ONROAD or not self._sidebar.is_visible:
          return
        rect = self._content_rect
    mode = {MainState.HOME: ShellMode.HOME, MainState.SETTINGS: ShellMode.SETTINGS,
            MainState.ONROAD: ShellMode.ONROAD}[self._current_mode]
    self.page.mode = mode
    self.page.render(rect)

  def _on_background_tap(self) -> None:
    if self._current_mode == MainState.ONROAD:
      self._on_onroad_clicked()

  def _set_current_layout(self, layout: MainState) -> None:
    if layout != MainState.SETTINGS and hasattr(self, "_network_bridge"):
      self._leave_large_panel()
      self._network_bridge.leave()
      if hasattr(self, "star") and self.star.selected == Destination.NETWORK:
        self.star.selected = Destination.STAR
        self.star.cancel()
        self.star._snapshot_cache = None
    changed = layout != self._current_mode
    if changed and hasattr(self, "page"):
      self.page.hide_event()
    super()._set_current_layout(layout)
    if changed and hasattr(self, "page") and layout != MainState.ARABESQUE:
      self.page.show_event()

  def open_settings(self, panel_type: Any) -> None:
    from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
    # Separate SettingsLayout from the shell's NetworkUI to avoid duplicate scans.
    native_panel = PanelType.DEVICE if panel_type == PanelType.NETWORK else panel_type
    super().open_settings(native_panel)
    if hasattr(self, "star"):
      if panel_type == PanelType.NETWORK:
        self.star.selected = Destination.NETWORK
        self._network_destination(Destination.NETWORK)
      elif self.star.selected == Destination.NETWORK:
        self._network_bridge.leave()
        self.star.selected = Destination.STAR
        self.star.cancel()
      self.star._snapshot_cache = None

  def _on_body_changed(self) -> None:
    if hasattr(self, "page"):
      self.page.hide_event()
    super()._on_body_changed()

  def close(self) -> None:
    super().close()
    self._leave_large_panel()
    self._network_bridge.leave()
    self.star.close()
    self._native_onroad.close()


class StarMiciMainLayout(MiciMainLayout):
  """Compact existing scroller/nav stack with StarPilot content pages."""

  def __init__(self):
    validate_runtime_fonts(Profile.COMPACT)
    validate_runtime_assets()
    super().__init__()
    self._native_onroad = self._car_onroad_layout
    self._compact_panels: dict[Destination, Widget] = {}
    try:
      self.star = StarShellSession(Profile.COMPACT, self._native_onroad)
    except Exception:
      self._native_onroad.close()
      raise
    self._home_layout = StarShellPage(self.star, ShellMode.HOME)
    self._car_onroad_layout = StarShellPage(self.star, ShellMode.ONROAD,
                                            on_background_tap=lambda: self._scroll_to(self._home_layout))
    self._settings_layout = StarCompactSettings(self.star)
    self._scroller.items[1] = self._home_layout
    self._scroller.items[2] = self._car_onroad_layout
    for page in (self._home_layout, self._car_onroad_layout):
      page.set_touch_valid_callback(lambda: self._scroller.scroll_panel.is_touch_valid() and self.enabled)
    for page in (self._home_layout, self._car_onroad_layout, self._settings_layout):
      page.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
    self.star.set_navigation(on_settings=lambda: gui_app.push_widget(self._settings_layout),
                             on_home=lambda: self._settings_layout.dismiss(),
                             on_compact_destination=self._open_compact_destination)
    self._scroller.set_scrolling_enabled(lambda: not self._native_onroad.is_swiping_left())
    self._on_body_changed()
    self.star._native_favorite_actions = self._native_favorites
    self.star._publish_bookmark = self._on_bookmark_clicked

  def _render(self, _) -> None:
    rect = _
    page = self._car_onroad_layout
    bookmark = self._native_onroad._bookmark_icon
    bookmark.set_rect(page.rect)
    bookmark.set_parent_rect(self.rect)
    if not (self.enabled and page.enabled and page.is_visible):
      bookmark.cancel_gesture()
    bookmark.process_gesture(can_start=(self.enabled and page.enabled and page.is_visible and page._touch_valid() and
                                       not self._scroller.is_auto_scrolling and abs(page.rect.x - self.rect.x) < 1))
    self.star.camera_paint = not self._settings_layout.covers_camera(rect)
    try:
      super()._render(rect)
    finally:
      self.star.camera_paint = True

  def _native_favorites(self):
    def panel():
      from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
      if Destination.TOGGLES not in self._compact_panels:
        self._compact_panels[Destination.TOGGLES] = TogglesLayoutMici()
      return self._compact_panels[Destination.TOGGLES]
    return self.star.native_favorite_actions(lambda: (self._on_bookmark_clicked() or True),
                                             lambda index: panel().request_personality(index),
                                             lambda: panel().request_experimental())

  def _handle_transitions(self):
    from openpilot.starpilot.ui.spacepilot.native import SpacePilotPage, Stage, live_witness
    page = gui_app.get_active_widget()
    if (isinstance(page, SpacePilotPage) and not page.closed and page.session.stage != Stage.CLOSED and
        live_witness() is not None):
      # Cancel only the cosmetic delayed startup redirect for a freshly parked
      # game. The base movement transition and the game's own gates still run.
      self._onroad_time_delay = None
    super()._handle_transitions()

  def _exit_spacepilot(self) -> None:
    # An unsafe game exit must reveal the existing road/alert presentation now,
    # including when engagement/alerts change while the car still reports Park.
    gui_app.pop_widgets_to(self, instant=True)
    self._scroller.scroll_to(self._onroad_layout.rect.x if ui_state.started else self._home_layout.rect.x, smooth=False)

  def _open_compact_destination(self, destination: Destination) -> None:
    if destination == Destination.BLUETOOTH:
      from openpilot.starpilot.ui.bluetooth_compact import BluetoothCompact
      panel = self._compact_panels.get(destination)
      if panel is None:
        panel = BluetoothCompact(self.star.connectivity_allowed)
        self._compact_panels[destination] = panel
      gui_app.push_widget(panel)
      return
    if destination == Destination.GALAXY:
      self.star.galaxy_flow.open_compact()
      return
    if destination == Destination.DRIVING_MODEL:
      from openpilot.starpilot.ui.models_compact import ModelsCompact
      if not hasattr(self, "_models_compact"):
        self._models_compact = ModelsCompact(self.star)
      self._models_compact.open()
      return
    if destination == Destination.DRIVING_CONTROLS:
      from openpilot.starpilot.ui.feature_settings_compact import FeatureSettingsCompact
      FeatureSettingsCompact(self.star).open(FeaturePage.HUB)
      return
    if destination == Destination.APPEARANCE:
      from openpilot.starpilot.ui.appearance_compact import AppearanceCompact
      AppearanceCompact(self.star).open()
      return
    if destination == Destination.VEHICLE:
      from openpilot.starpilot.ui.vehicle_compact import VehicleCompact
      gui_app.push_widget(VehicleCompact(self.star))
      return
    if destination == Destination.PAIR:
      self._open_compact_destination(Destination.DEVICE)
      self._compact_panels[Destination.DEVICE].scroll_to_pairing()
      return
    from openpilot.selfdrive.ui.mici.layouts.settings.device.device_layout import DeviceLayoutMici
    from openpilot.selfdrive.ui.mici.layouts.settings.developer import DeveloperLayoutMici
    from openpilot.selfdrive.ui.mici.layouts.settings.network.network_layout import NetworkLayoutMici
    from openpilot.selfdrive.ui.mici.layouts.settings.software import SoftwareLayoutMici
    from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
    panel_types = {Destination.TOGGLES: TogglesLayoutMici, Destination.NETWORK: NetworkLayoutMici,
                   Destination.DEVICE: DeviceLayoutMici, Destination.SOFTWARE: SoftwareLayoutMici,
                   Destination.DEVELOPER: DeveloperLayoutMici}
    panel_type = panel_types.get(destination)
    if panel_type is None:
      self.star._unavailable(destination.value)
      return
    panel = self._compact_panels.get(destination)
    if panel is None:
      panel = DeveloperLayoutMici(on_spacepilot_exit=self._exit_spacepilot) if destination == Destination.DEVELOPER else panel_type()
      if destination == Destination.DEVICE:
        from openpilot.starpilot.ui.display_compact import DisplayCompact
        from openpilot.starpilot.ui.replay_settings import replay_entry
        panel._scroller.add_widgets([DisplayCompact(self.star).entry_button(), replay_entry(self.star)])
      self._compact_panels[destination] = panel
    gui_app.push_widget(panel)

  def close(self) -> None:
    self.star.close()
    self._native_onroad.close()
