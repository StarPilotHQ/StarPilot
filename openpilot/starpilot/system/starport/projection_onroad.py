"""Large onroad projection with navigation and drive-bookmark actions."""

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import time

from openpilot.starpilot.system.starport.identity import EXPIRY_WARNING_DAYS
from openpilot.starpilot.system.starport.projection_exit import ProjectionExit
from openpilot.starpilot.system.starport.projection_geometry import FALLBACK_VIEWPORT
from openpilot.starpilot.system.starport.projection_map_lease import MapLeaseWriter

CERTIFICATE_NOTICE_NS = 10_000_000_000  # how long the expiry heads-up stays at the start of a drive
BOOKMARK_NOTICE_NS = 2_000_000_000  # how long "Bookmark Saved" stays after the drive is bookmarked
MODE_NOTICE_NS = 2_000_000_000  # how long the driving-mode banner stays after the mode changes
RECONNECT_NOTICE_NS = 8_000_000_000  # how long "why it reconnected" stays once the car screen is back
DRIVER_MONITOR_HOLD_NS = 350_000_000
BIRDS_EYE_SETTINGS_NS = 1_000_000_000  # birds-eye-only mode: how often road colors and longitudinal control are re-read
BIRDS_EYE_CARD_MARGIN = (28, 20)  # birds-eye-only mode: the now-playing card's gap to the HUD's strip and torque bar, px at 1080 rows
CONTENTS = ('birds_eye', 'full')  # what the car screen shows while driving (drive_render.stream_content)
STANDBY_LOGO = Path(__file__).parents[3] / 'selfdrive/assets/images/starpilot_logo.png'
BUBBLE_REDUNDANT_ALERTS = frozenset(('preLaneChangeLeft', 'preLaneChangeRight', 'laneChange',
                                     'laneChangeBlocked', 'laneChangeBlockedLoud'))


def driving_mode_label(state) -> str | None:
  """The driving mode the driver chose, or None while it cannot be read.

  Conditional mode's own automatic switching is not a change of mode, so it all reads as one label;
  only a driver override (a wheel or screen tap) changes it."""
  from openpilot.starpilot.conditional_mode.policy import ModeChoice
  choice = getattr(state, 'conditional_configured', None)
  if choice in (ModeChoice.CEM, ModeChoice.CCM):
    effective = getattr(state, 'conditional_effective', None)
    if effective is None:
      if getattr(state, 'engaged', True):
        return None  # not reported between planner updates
      # Disengaged, nothing is reported and nothing switches by itself: the flag is the driver's choice.
      experimental = getattr(state, 'experimental_enabled', None)
      return None if not isinstance(experimental, bool) else 'Experimental Mode' if experimental else 'Chill Mode'
    if effective.reason == 'manual_experimental':
      return 'Experimental Mode'
    if effective.reason == 'manual_chill':
      return 'Chill Mode'
    return 'Conditional Experimental' if choice is ModeChoice.CEM else 'Conditional Chill'
  experimental = getattr(state, 'experimental_enabled', None)
  if not isinstance(experimental, bool):
    return None
  return 'Experimental Mode' if experimental else 'Chill Mode'


def certificate_notice(days_left: int | None) -> str:
  """The car-screen heads-up for a certificate in its last two weeks; empty otherwise."""
  if days_left is None or not 0 <= days_left <= EXPIRY_WARNING_DAYS:
    return ""
  when = "Within a Day" if days_left == 0 else f"in {days_left} Day{'' if days_left == 1 else 's'}"
  return f"Heads Up: AA Certificate Expires {when}"


def apply_plain_camera_look(camera, rl):
  """The car screen keeps the plain camera look: the shader's non-road path, not the comma UI's engagement grading.

  The comma UI fades the road camera when disengaged (color_strength) and a car screen reads that as washed out.
  """
  camera._color_strength_val[0] = 1.0
  camera._road_camera_val[0] = 0
  rl.set_shader_value(camera.shader, camera._color_strength_loc, camera._color_strength_val, rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT)
  rl.set_shader_value(camera.shader, camera._road_camera_loc, camera._road_camera_val, rl.ShaderUniformDataType.SHADER_UNIFORM_INT)


def native_dependencies():
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.cereal.visionipc import VisionStreamType
  from openpilot.common.transformations.camera import view_frame_from_device_frame
  from openpilot.selfdrive.ui.onroad.augmented_road_view import AugmentedRoadView, BORDER_COLORS
  from openpilot.selfdrive.ui.onroad.cameraview import CameraView
  from openpilot.selfdrive.ui.onroad.model_renderer import ModelRenderer
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer, BitmapText
  from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.onroad_customization import CAMERA_WIDGETS, placement, widget_size
  from openpilot.starpilot.ui.onroad_dm import DriverMonitorLayer
  from openpilot.starpilot.ui.onroad_map import MapFeed, live_overlay
  from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert
  from openpilot.starpilot.ui.pip_preferences import read_pip
  from openpilot.starpilot.ui.pip_render import PiPRenderer
  from openpilot.starpilot.ui.pip_sidecam import Rect, Signals
  from openpilot.starpilot.ui.pip_warning import PiPWarningSource
  from openpilot.starpilot.spot_monitor.preferences import read_preferences as read_vasm_preferences
  from openpilot.starpilot.speed_limits.vision.observation import clock_pair_ns
  from opendbc.car.structs import car as car_schema
  from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile, default_font_directory
  from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter, current_message, display_message
  from openpilot.starpilot.ui.shell import ShellMode
  from openpilot.starpilot.system.starport.projection_favorites import ProjectionFavorites
  from openpilot.starpilot.system.starport.projection_now_playing import ProjectionNowPlayingFeed
  from openpilot.starpilot.system.starport.media_audio import music_control
  from openpilot.starpilot.models.jetlink_status import JetlinkStatusFeed
  from openpilot.starpilot.ui.onroad_jetlink import gpu_inputs

  class ProjectionRoadCamera(AugmentedRoadView):
    """Current native calibrated camera/model without stock action widgets."""

    def __init__(self):
      stream = VisionStreamType.VISION_STREAM_NARROW_ROAD
      try:
        CameraView.__init__(self, 'camerad', stream)
        self._set_placeholder_color(BORDER_COLORS[UIStatus.DISENGAGED])
        self.device_camera = None
        self.view_from_calib = view_frame_from_device_frame.copy()
        self.view_from_wide_calib = view_frame_from_device_frame.copy()
        self._matrix_cache_key = (0, 0.0, 0.0, stream)
        self._cached_matrix = None
        self._content_rect = rl.Rectangle()
        self.model_renderer = ModelRenderer()
      except BaseException:
        callbacks = ui_state._offroad_transition_callbacks
        if self._offroad_transition in callbacks:
          callbacks.remove(self._offroad_transition)
        try:
          CameraView.close(self)
        except (AttributeError, RuntimeError):
          pass
        raise

    def _update_texture_color_filtering(self):
      if getattr(self, 'large_ui_gamma_trial', False):
        return super()._update_texture_color_filtering()
      apply_plain_camera_look(self, rl)

  standby_logo = standby_logo_loader(rl)
  return SimpleNamespace(rl=rl, ui_state=ui_state, camera=ProjectionRoadCamera,
                         onroad=OnroadView, monitor=DriverMonitorLayer, fonts=BitmapFonts,
                         font_role=FontRole, profile=Profile, font_directory=default_font_directory,
                         adapter=RuntimeSnapshotAdapter, current_message=current_message, display_message=display_message,
                         shell_mode=ShellMode, pip_renderer=PiPRenderer, read_pip=read_pip, pip_signals=Signals,
                         pip_warning=PiPWarningSource, read_vasm=read_vasm_preferences, clock_pair_ns=clock_pair_ns,
                         car_schema=car_schema,
                         pip_rect=Rect, pip_widgets=CAMERA_WIDGETS, placement=placement, widget_size=widget_size,
                         alert=OnroadAlert, alert_size=AlertSize, map_overlay=live_overlay, map_feed=MapFeed,
                         favorites=ProjectionFavorites, standby_logo=standby_logo, now_playing_feed=ProjectionNowPlayingFeed,
                         now_playing_control=music_control, jetlink_feed=JetlinkStatusFeed, gpu_inputs=gpu_inputs,
                         birds_eye=BirdsEyeRenderer, birds_eye_text=BitmapText)


def birds_eye_card_placement(placed, width, height, view):
  """The now-playing placement for the birds-eye screen: the bottom left of `view` (x, y, width, height: the HUD's
  view rect), right of the acceleration strip and above the torque bar, whatever the layout's own position (that
  is for the camera composition). A copy; the layout's dict, and its size, opacity and enabled, are unchanged."""
  from openpilot.starpilot.system.starport.projection_layout import now_playing_size
  left, bottom = (round(value * height / 1080) for value in BIRDS_EYE_CARD_MARGIN)
  _, card_h = now_playing_size(placed)
  return {**placed, 'x': view[0] + left, 'y': view[1] + view[3] - bottom - card_h}


def birds_eye_dependencies():
  """What the birds-eye-only car screen draws: the birds-eye view with its HUD and now-playing card, the standby
  screen and its exit button.

  ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL (operator-requested video optimization): no camera, VisionIPC,
  model renderer, OnroadView, map, picture-in-picture, driver monitor or Jetlink feed is imported, so none is built.
  The HUD and the now-playing card, feed and control are imported only when built.
  """
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer, BitmapText
  from openpilot.selfdrive.ui.ui_state import ui_state
  from openpilot.starpilot.ui.onroad_customization import read_customization
  from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile, default_font_directory

  def road_style(ui):
    """The comma's own road colors for the large layout, as the full composition draws them."""
    return read_customization(ui.params).get('roadColors', {}).get('large', {})

  def long_control(ui):
    """openpilot longitudinal control from the live carParams, else the saved CarParams; None when unknown."""
    sm = ui.sm
    if sm.seen['carParams']:
      return bool(sm['carParams'].openpilotLongitudinalControl)
    raw = ui.params.get('CarParams')
    if not raw:
      return None
    from openpilot.cereal import messaging
    from opendbc.car.structs import car
    return bool(messaging.log_from_bytes(raw, car.CarParams).openpilotLongitudinalControl)

  def birds_eye_hud(text, source):
    """The acceleration bar and torque arc over the birds-eye view (imported only when built)."""
    from openpilot.starpilot.system.starport.birds_eye_hud import BirdsEyeHud
    return BirdsEyeHud(rl, text, source)

  def now_playing_widget(fonts):
    """The camera composition's now-playing card, drawn by itself."""
    from openpilot.starpilot.ui.onroad_now_playing import NowPlayingWidget
    return NowPlayingWidget(fonts)

  def now_playing_feed():
    from openpilot.starpilot.system.starport.projection_now_playing import ProjectionNowPlayingFeed
    return ProjectionNowPlayingFeed(fetch=lambda url: None)  # the glass card shows no cover, so none is downloaded

  def now_playing_control(action, args=None):
    from openpilot.starpilot.system.starport.media_audio import music_control
    music_control(action, args)

  def now_playing_touch(send, feedback):
    from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
    return NowPlayingTouch(send, feedback)

  standby_logo = standby_logo_loader(rl)
  return SimpleNamespace(rl=rl, ui_state=ui_state, fonts=BitmapFonts, font_role=FontRole, profile=Profile,
                         font_directory=default_font_directory, standby_logo=standby_logo,
                         birds_eye=BirdsEyeRenderer, birds_eye_text=BitmapText, road_style=road_style,
                         long_control=long_control, birds_eye_hud=birds_eye_hud,
                         now_playing_widget=now_playing_widget, now_playing_feed=now_playing_feed,
                         now_playing_control=now_playing_control, now_playing_touch=now_playing_touch)


def standby_logo_loader(rl):
  def standby_logo(height):
    """The StarPilot crescent, shrunk once on the CPU to its standby size; None if it cannot be loaded."""
    image = rl.load_image(str(STANDBY_LOGO))
    try:
      if image.data is None or image.width <= 0 or image.height <= 0:
        return None
      if image.height > height:
        rl.image_resize(image, round(image.width * height / image.height), height)
      texture = rl.load_texture_from_image(image)
    finally:
      if image.data is not None:
        rl.unload_image(image)
    if texture.id == 0:
      return None
    rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
    return texture
  return standby_logo


class ProjectionOnroad:
  """Separate renderer with projected actions; native UIState stays read-only.

  ``content='full'`` is the camera composition. ``content='birds_eye'`` (the default stream_content) draws only
  the birds-eye view over the whole frame while driving and builds nothing else (see birds_eye_dependencies).
  """

  def __init__(self, *, dependencies=None, viewport=None, customization=None, certificate_days=None, native_focus=None,
               layout_watcher=None, content='full', reconnect_notice=""):
    if content not in CONTENTS:
      raise ValueError(f'unknown projection content {content!r}')
    self.content = content
    viewport = FALLBACK_VIEWPORT if viewport is None else viewport
    self.width, self.height = viewport
    self.customization = customization
    self._certificate_notice = certificate_notice(certificate_days)
    self._certificate_notice_until_ns: int | None = None
    self._reconnect_notice = reconnect_notice
    self._reconnect_notice_until_ns: int | None = None
    self._bookmark_notice_until_ns: int | None = None
    self._mode_label: str | None = None  # the drive's last known driving mode
    self._mode_engaged: bool | None = None
    self._mode_notice: tuple[str, int] | None = None  # (banner text, shown until)
    self._base_customization = None
    self._projection_customization = None
    self._base_appearance = None
    self._projection_appearance = None
    self.layout_watcher = layout_watcher
    self.native_focus = native_focus or (lambda: None)
    self.bookmark = lambda: False
    # Set by current_car_ui to publish a validated speed-limit action; absent, the buttons draw but do nothing.
    self.slc_action = lambda request: False
    self._slc_input = None
    self.camera_stream = None  # the camera stream the last frame drew, or None
    self.native = dependencies or (birds_eye_dependencies() if content == 'birds_eye' else native_dependencies())
    native = self.native
    self._standby_logo = None
    self._standby_logo_tried = False
    if native.ui_state.projection_read_only is not True:
      raise RuntimeError('Projection UIState must be read-only')
    self._resources = ExitStack()
    try:
      self.fonts = native.fonts(native.profile.LARGE, native.font_directory(), headless_context=True)
      self._resources.callback(self.fonts.close)
      if content == 'birds_eye':
        self._init_birds_eye_only()
        return
      self.camera = native.camera()
      self.camera.large_ui_gamma_trial = bool((customization or {}).get('largeUiGammaTrial', False))
      self._resources.callback(self._close_camera)
      self.onroad = self.create_view(native.onroad, self.fonts, camera_layer=self._camera_layer, viewport=viewport)
      self._resources.callback(self._close_onroad)
      if getattr(self.onroad, 'uniden', None):
        self.onroad.uniden.runtime_active = True
        self._resources.callback(self.onroad.uniden.close)
      self.now_playing_feed = None
      if getattr(native, 'now_playing_feed', None) is not None:
        self.now_playing_feed = native.now_playing_feed()
        self._resources.callback(self.now_playing_feed.close)
      self.now_playing_touch = None
      if self.now_playing_feed is not None and getattr(native, 'now_playing_control', None) is not None:
        from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
        self.now_playing_touch = NowPlayingTouch(native.now_playing_control, getattr(self.onroad, 'now_playing', None))
      self.jetlink_feed = None
      if getattr(native, 'jetlink_feed', None) is not None:
        self.jetlink_feed = native.jetlink_feed()
        self._resources.callback(self.jetlink_feed.close)
      self.exit = ProjectionExit(native.rl, lambda: self.native_focus())
      self.onroad.projection_exit_layer = self.exit.render
      self.monitor = native.monitor(native.profile.LARGE)
      self._monitor_pair = None
      self._monitor_pair_ns = None
      self.onroad.driver_monitor_layer = self._driver_monitor_layer
      from openpilot.starpilot.ui.onroad_state import OnroadInput
      def emit_slc(request) -> None:
        self.slc_action(request)
      self._slc_input = OnroadInput(emit_slc, native.profile.LARGE, slop=30)
      self.onroad.pressed_action = lambda: self._slc_input.pressed_action
      self.pip = None
      self._pip_saved = None
      self._pip_read_ns = None
      self._vasm_saved = None
      self.pip_warning = None
      if getattr(native, 'pip_renderer', None) is not None:
        self.pip = native.pip_renderer("bubble")
        self._resources.callback(self.pip.close)
        if getattr(native, 'pip_warning', None) is not None:
          self.pip_warning = native.pip_warning()
          self._resources.callback(self.pip_warning.close)
        self.onroad.pip_layer = self._pip_layer
      self.birds_eye = None
      # A tap on the car screen switches panel/full for this connection only; the saved layout is
      # Galaxy-owned and only written while parked, so the projection never writes it.
      self._birds_eye_display = None
      self._birds_eye_press = None
      self._ensure_birds_eye()
      self.map = None
      self.map_feed = None
      self._map_lease = MapLeaseWriter()
      self._resources.callback(self._release_map)
      self._ensure_map()
      self.adapter = native.adapter(native.ui_state)
      # Personality notices and the reverse camera are decided inside the adapter, so it needs AA's choices too.
      self.adapter.appearance_override = self._own_appearance
      self._state = None
      self.favorites = None
      if getattr(native, 'favorites', None) is not None:
        self.favorites = native.favorites(lambda: native.ui_state.started)
        self.favorites.bookmark = lambda: self.bookmark()
        self.favorites.card_bounds = getattr(getattr(self.onroad, 'navigation', None), 'bounds', None)
        self.favorites.map_rect = lambda: self._map_placement() if self.map is not None else None
        self._resources.callback(self.favorites.close)
    except BaseException:
      self._resources.close()
      raise

  def _init_birds_eye_only(self):
    """Birds-eye-only mode: the view, its HUD and the now-playing card; everything else the full composition owns
    stays None, so nothing else is drawn or polled."""
    native = self.native
    self.camera = self.onroad = self.monitor = self.pip = self.map = self.map_feed = self.adapter = None
    self.now_playing_feed = self.now_playing_touch = self.jetlink_feed = self.favorites = None
    self.pip_warning = self._vasm_saved = None
    self._map_lease = None
    self._monitor_pair = self._monitor_pair_ns = None
    self._pip_saved = self._pip_read_ns = None
    self._state = None
    self._birds_eye_display = self._birds_eye_press = None
    self._birds_eye_settings_ns: int | None = None
    self._road_style: dict = {}
    self.exit = ProjectionExit(native.rl, lambda: self.native_focus())
    ui = native.ui_state
    text = native.birds_eye_text(self.fonts) if getattr(native, 'birds_eye_text', None) is not None else None
    self.birds_eye = native.birds_eye(text=text, source=lambda: (ui.sm, ui.started_frame))
    self.hud = None
    if getattr(native, 'birds_eye_hud', None) is not None:
      self.hud = native.birds_eye_hud(text, lambda: (ui.sm, getattr(ui, 'CP', None), ui.started_frame))
      self._resources.callback(self.hud.close)
    self.now_playing = None
    self._birds_eye_card = None  # (the card's render/touch state, its placement inside the view) for this layout
    widget, feed = getattr(native, 'now_playing_widget', None), getattr(native, 'now_playing_feed', None)
    if widget is not None and feed is not None:
      self.now_playing = widget(self.fonts)
      self._resources.callback(self.now_playing.close)
      from openpilot.selfdrive.ui.onroad import birds_eye_math
      # The card is glass like the view's label and the HUD's bars, not the camera composition's tinted wash.
      self.now_playing.glass = (birds_eye_math.GLASS_FILL, birds_eye_math.GLASS_RIM, birds_eye_math.GLASS_RIM_PX)
      self.now_playing_feed = feed()
      self._resources.callback(self.now_playing_feed.close)
      control, touch = getattr(native, 'now_playing_control', None), getattr(native, 'now_playing_touch', None)
      if control is not None and touch is not None:
        self.now_playing_touch = touch(control, self.now_playing)

  def _birds_eye_now_playing(self):
    """(state, placed) the birds-eye card renders and hit-tests with, built once per layout; (None, None) when the
    layout places no card. `placed` is the layout's placement moved clear of the HUD's strip and dock."""
    if self._birds_eye_card is None:
      placed = (self.customization or {}).get('widgets', {}).get('now_playing')
      if not isinstance(placed, dict) or 'x' not in placed or 'y' not in placed:
        self._birds_eye_card = (None, None)
      else:
        if self.hud is not None:
          placed = birds_eye_card_placement(placed, self.width, self.height, self.hud.view_rect(self.width, self.height))
        state = SimpleNamespace(customization={'layouts': {'large': {'now_playing': placed}}},
                                alert=SimpleNamespace(size='none'), viewport_width=self.width)
        self._birds_eye_card = (state, placed)
    return self._birds_eye_card

  def _refresh_birds_eye_settings(self, now_ns):
    """Road colors and longitudinal control change rarely; re-read them once a second, never per frame."""
    last = self._birds_eye_settings_ns
    if last is not None and 0 <= now_ns - last < BIRDS_EYE_SETTINGS_NS:
      return
    self._birds_eye_settings_ns = now_ns
    native, ui = self.native, self.native.ui_state
    if getattr(native, 'road_style', None) is not None:
      try:
        style = native.road_style(ui)
        self._road_style = style if isinstance(style, dict) else {}
      except Exception:
        self._road_style = {}
    if getattr(native, 'long_control', None) is not None:
      try:
        long_control = native.long_control(ui)
      except Exception:
        long_control = None
      if long_control is not None:
        self.birds_eye.long_control = long_control

  def _render_birds_eye_only(self):
    """The birds-eye view over the logical frame, right of the HUD's acceleration strip and running on under its
    torque bar; no border or brand text."""
    view = self.birds_eye
    view.panel = False
    view.opacity = 1.0
    view.parent_clip = None
    view.metric = bool(getattr(self.native.ui_state, 'is_metric', True))
    view.road_style = self._road_style
    hud = getattr(self, 'hud', None)
    rect = (0, 0, self.width, self.height)
    if hud is not None:
      rect = (getattr(hud, 'render_rect', None) or hud.view_rect)(self.width, self.height)
    view.render(self.native.rl.Rectangle(*rect))
    if hud is not None:
      hud.metric = view.metric
      hud.render(self.width, self.height)
    if self.now_playing is not None:
      state, _ = self._birds_eye_now_playing()
      if state is not None:
        self.now_playing.render(state)

  @staticmethod
  def create_view(view_factory, fonts, *, viewport, **layers):
    """Production AA composition, shared with the parked theme preview."""
    return view_factory(fonts, Path(__file__).parents[3] / 'selfdrive/assets',
                        projection_viewport=viewport, **layers)

  def _close_camera(self):
    callbacks = self.native.ui_state._offroad_transition_callbacks
    callback = self.camera._offroad_transition
    try:
      callbacks.remove(callback)
    except ValueError:
      pass
    self.camera.close()

  def _close_onroad(self):
    # Raylib's window-ready flag stays false for a valid headless EGL context.
    # The large steering wheel texture is owned by this view and must be freed.
    wheel = self.onroad.steering_wheel
    texture = getattr(wheel, '_texture', None)
    if texture is not None:
      self.native.rl.unload_texture(texture)
      wheel._texture = None
    self.onroad.close()

  def _birds_eye_placement(self):
    """The enabled birds-eye placement from this connection's layout, or None."""
    # getattr: callers that build the view without __init__ (camera policy tests) have neither attribute.
    customization = getattr(self, 'customization', None)
    if customization is None or getattr(self, 'birds_eye', None) is None:
      return None
    placed = customization['widgets'].get('birds_eye')
    return placed if placed is not None and placed.get('enabled') is True else None

  def birds_eye_display(self):
    """'overlay', 'full', or None when the birds-eye view is off."""
    if getattr(self, 'content', 'full') == 'birds_eye':
      return 'full'  # birds-eye-only content: always the whole frame, whatever the layout says
    placed = self._birds_eye_placement()
    if placed is None:
      return None
    return getattr(self, '_birds_eye_display', None) or placed.get('display', 'overlay')

  def _ensure_birds_eye(self):
    """Build the birds-eye renderer the first time a layout turns it on; it stays for the connection."""
    native = self.native
    placed = (self.customization or {}).get('widgets', {}).get('birds_eye')
    if (self.birds_eye is not None or placed is None or placed.get('enabled') is not True or
        getattr(native, 'birds_eye', None) is None):
      return
    ui = native.ui_state
    text = native.birds_eye_text(self.fonts) if getattr(native, 'birds_eye_text', None) is not None else None
    self.birds_eye = native.birds_eye(text=text, source=lambda: (ui.sm, ui.started_frame))
    model_renderer = getattr(self.camera, 'model_renderer', None)
    self.birds_eye.long_control = bool(getattr(model_renderer, '_longitudinal_control', True))
    self.onroad.birds_eye_layer = self._birds_eye_layer

  def _draw_birds_eye(self, rect, state, *, panel, opacity=1.0, parent_clip=None):
    view = self.birds_eye
    view.panel = panel
    view.opacity = opacity
    view.parent_clip = parent_clip
    view.metric = bool(getattr(self.native.ui_state, 'is_metric', True))
    # The camera's model renderer follows carParams as they arrive; the view follows it every frame.
    model_renderer = getattr(getattr(self, 'camera', None), 'model_renderer', None)
    if model_renderer is not None:
      view.long_control = bool(getattr(model_renderer, '_longitudinal_control', True))
    try:
      # In full display the camera does not draw, so its model renderer never sees a new carParams.
      sm = self.native.ui_state.sm
      if sm.seen['carParams']:
        view.long_control = bool(sm['carParams'].openpilotLongitudinalControl)
    except (AttributeError, KeyError, TypeError):
      pass
    customization = getattr(state, 'customization', None) or {}
    view.road_style = customization.get('roadColors', {}).get('large', {})
    view.render(rect)

  def _birds_eye_layer(self, rect, state):
    """Overlay panel, above the camera and below the HUD widgets; full mode draws in the camera layer instead."""
    placed = self._birds_eye_placement()
    if placed is None or self.birds_eye_display() != 'overlay':
      return
    rl = self.native.rl
    self._draw_birds_eye(rl.Rectangle(placed['x'], placed['y'], placed['width'], placed['height']), state,
                         panel=True, opacity=placed['opacity'] / 100.0, parent_clip=rect)

  def _birds_eye_rect(self):
    display = self.birds_eye_display()
    if display == 'full':
      return 30, 30, self.width - 60, self.height - 60
    if display == 'overlay':
      placed = self._birds_eye_placement()
      return placed['x'], placed['y'], placed['width'], placed['height']
    return None

  def _birds_eye_touch(self, kind, x, y) -> bool:
    """A tap on the birds-eye view switches panel/full; True while it owns the touch."""
    if kind == 'cancel' or self._state is None or not self.native.ui_state.started:
      claimed = self._birds_eye_press is not None
      self._birds_eye_press = None
      return claimed
    if kind == 'down':
      self._birds_eye_press = None
      area = self._birds_eye_rect()
      if area is None or not (area[0] <= x <= area[0] + area[2] and area[1] <= y <= area[1] + area[3]):
        return False
      # Buttons drawn over the view keep their taps.
      if self.favorites is not None and self.favorites.hit(x, y, self._state) is not None:
        return False
      self._birds_eye_press = (x, y)
      return True
    if self._birds_eye_press is None:
      return False
    px, py = self._birds_eye_press
    if abs(x - px) > 30 or abs(y - py) > 30:
      self._birds_eye_press = None
      return True
    if kind == 'up':
      self._birds_eye_press = None
      self._birds_eye_display = 'overlay' if self.birds_eye_display() == 'full' else 'full'
    return True

  def _camera_layer(self, rect, state):
    if self.birds_eye_display() == 'full':
      # The birds-eye view replaces the camera; no camera frame is drawn, so none is paced.
      self._draw_birds_eye(rect, state, panel=False, parent_clip=rect)
      self.camera_stream = None
      return
    # The car screen's own lead choices; without them the marker stays off rather than following the comma.
    appearance = getattr(state, 'appearance', None)
    lead_display = ((appearance.show_lead_indicator, int(appearance.lead_info_mode), appearance.lead_info_metric)
                    if appearance is not None else (False, 0, None))
    self.camera.render_camera_model_layer(rect, camera_view=state.appearance.camera_view,
                                          reverse_driver=state.reverse_driver_camera,
                                          road_style=state.customization['roadColors']['large'], lead_display=lead_display)
    # The camera can switch narrow/wide while rendering. Pace the stream actually
    # drawn, not the previous selection (whose next frame may arrive later).
    self.camera_stream = self.camera.stream_type

  def _driver_monitor_layer(self, rect, state):
    ui = self.native.ui_state
    now_ns = time.monotonic_ns()
    # The two services are published independently. Requiring both to land in
    # the same narrow control-freshness window made the AA graphic fade in and
    # out. Keep the last jointly valid display observation across a brief gap.
    monitor = self.native.display_message(ui.sm, 'driverMonitoringState', now_ns, after_frame=ui.started_frame)
    driver = self.native.display_message(ui.sm, 'driverStateV2', now_ns, after_frame=ui.started_frame)
    if monitor is not None and driver is not None:
      self._monitor_pair = monitor, driver
      self._monitor_pair_ns = now_ns
    elif (self._monitor_pair is not None and self._monitor_pair_ns is not None and
          0 <= now_ns - self._monitor_pair_ns <= DRIVER_MONITOR_HOLD_NS):
      monitor, driver = self._monitor_pair
    else:
      self._monitor_pair = None
      self._monitor_pair_ns = None
    self.monitor.render(state, monitor=monitor, driver=driver,
                        fresh=monitor is not None and driver is not None,
                        onroad=ui.is_onroad(), y_offset=self.height - 1080)

  def _ensure_map(self):
    """Build the map overlay the first time a layout turns it on; it stays for the connection."""
    native = self.native
    if self.map is None and self._map_placement() is not None and getattr(native, 'map_overlay', None) is not None:
      self.map = native.map_overlay(fonts=self.fonts)
      self.map_feed = native.map_feed()
      self.onroad.map_layer = self._map_layer

  def _release_map(self):
    if self.map is not None:
      self.map.close()
    self.map = self.map_feed = None
    self.onroad.map_layer = None
    self._map_lease.release()

  def apply_layout(self, customization):
    """Switch to a newly saved Android Auto layout without reconnecting."""
    self.customization = customization
    if self.content == 'birds_eye':
      self._birds_eye_card = None  # the exit button and the now-playing card read the layout
      return
    self._base_customization = self._projection_customization = None
    self._base_appearance = self._projection_appearance = None
    self.camera.large_ui_gamma_trial = bool((customization or {}).get('largeUiGammaTrial', False))
    invalidate = getattr(self.adapter, 'invalidate_appearance', None)
    if invalidate is not None:
      invalidate()
    self._ensure_map()
    self._birds_eye_display = None  # a newly saved layout's display choice wins over an earlier tap
    self._ensure_birds_eye()

  def _own_appearance(self, appearance):
    if self.customization is None:
      return appearance
    from openpilot.starpilot.system.starport.projection_layout import projection_appearance
    return projection_appearance(appearance, self.customization)

  def _check_layout(self):
    # The watcher's own thread does all file work; this only takes a finished layout.
    changed = self.layout_watcher.take() if self.layout_watcher is not None else None
    if changed is not None:
      self.apply_layout(changed)

  def _map_placement(self):
    """The enabled map overlay's placement from this connection's layout, or None."""
    if self.customization is None:
      return None
    placed = self.customization['widgets'].get('nav_map')
    return placed if placed is not None and placed['enabled'] else None

  def prepare(self):
    """Before the frame's render target is bound: offscreen map work happens here."""
    self._check_layout()
    if self.content == 'birds_eye':
      started = self.native.ui_state.started
      if started:
        self._refresh_birds_eye_settings(time.monotonic_ns())
      if self.now_playing_feed is not None:
        # Polled only while the card can show: driving, with the card placed.
        _, placed = self._birds_eye_now_playing()
        self.now_playing_feed.set_enabled(bool(started and placed is not None and placed.get('enabled')))
        self.now_playing.track = self.now_playing_feed.snapshot()
        self.now_playing.artwork = self.now_playing_feed.artwork()
      return
    if self.now_playing_feed is not None:
      placed = (self.customization or {}).get('widgets', {}).get('now_playing', {})
      self.now_playing_feed.set_enabled(bool(placed.get('enabled')))
      self.onroad.now_playing.track = self.now_playing_feed.snapshot()
      self.onroad.now_playing.artwork = self.now_playing_feed.artwork()
    if self.jetlink_feed is not None:
      placed = (self.customization or {}).get('widgets', {}).get('jetlink_status', {})
      self.jetlink_feed.set_enabled(bool(placed.get('enabled') and self.native.ui_state.started))
      self.onroad.jetlink_status.status = self.jetlink_feed.snapshot()
      if (gpu_inputs := getattr(self.native, 'gpu_inputs', None)) is not None:
        self.onroad.jetlink_status.gpu, self.onroad.jetlink_status.chestnut = gpu_inputs(self.native.ui_state)
    if self.favorites is not None:
      self.favorites.refresh()
      self.onroad.navigation_favorites.document = self.favorites.document
      self.onroad.navigation_favorites.error = self.favorites.error
    placed = self._map_placement()
    if self.map is not None and placed is None:
      self._release_map()
    if self.map is None or placed is None or not self.native.ui_state.started:
      return
    self._map_lease.touch()
    document = self.favorites.document if self.favorites is not None else None
    requested = bool(document['destination']) if document is not None else None
    from openpilot.starpilot.system.starport.projection_favorites import destination_label
    data = self.map_feed.read(self.native.ui_state.sm, navigation_requested=requested,
                              destination=destination_label(document) if document is not None else None)
    self.map.prepare(data, placed['width'], placed['height'])

  def _map_layer(self, rect, state):
    placed = self._map_placement()
    if placed is not None and self.map is not None:
      self.map.draw(self.native.rl.Rectangle(placed['x'], placed['y'], placed['width'], placed['height']),
                    placed['opacity'] / 100.0)
      if state is not None and self.favorites is not None:
        self.onroad.navigation_favorites.render_map_end(placed['x'], placed['y'], placed['width'], placed['height'],
                                                        state.customization['layouts']['large'])

  def _pip_layer(self, rect, state, *, submit=None):
    """Shared camera crops, with Android Auto's own blind-spot trigger and placements."""
    native = self.native
    now_ns = time.monotonic_ns()
    if self._pip_read_ns is None or not 0 <= now_ns - self._pip_read_ns < 1_000_000_000:
      self._pip_saved = native.read_pip(native.ui_state.params)
      self._vasm_saved = native.read_vasm(native.ui_state.params) if self.pip_warning is not None else None
      self._pip_read_ns = now_ns
    saved = self._pip_saved
    # AA can show warnings with the comma preview off; only crops and mirroring are shared.
    on_bsm = (self.customization or {}).get('blindSpotCamera', False) is True
    on_blinker = saved is not None and saved.enabled is True and saved.on_blinker is True
    if saved is None or saved.mask is None or saved.invert is None or not (on_bsm or on_blinker):
      self.pip.deactivate()
      self._set_pip_showing(False)
      return
    ui = native.ui_state
    # Display freshness: carState is 100 Hz, so the 20 ms control window often lapses within one drawn frame.
    car = native.display_message(ui.sm, 'carState', now_ns, after_frame=ui.started_frame)
    vasm = self._vasm_saved
    pair = native.clock_pair_ns() if car is not None and self.pip_warning is not None else None
    vasm_enabled = (self.pip_warning is not None and car is not None and car.canValid and
                    car.gearShifter == native.car_schema.CarState.GearShifter.drive and
                    pair is not None and vasm is not None and vasm.readable and vasm.valid and vasm.preferences.enabled)
    left_warning, right_warning = (False, False)
    if self.pip_warning is not None:
      left_warning, right_warning = self.pip_warning.sample(
        enabled=vasm_enabled, settings_fingerprint=vasm.fingerprint if vasm_enabled else "",
        now_mono_ns=pair[0] if pair is not None else 0, now_boot_ns=pair[1] if pair is not None else 0)
    signals = native.pip_signals(car is not None,
                                 bool(car.leftBlinker) if car is not None else False,
                                 bool(car.rightBlinker) if car is not None else False,
                                 bool(car.leftBlindspot) if car is not None else False,
                                 bool(car.rightBlindspot) if car is not None else False,
                                 left_warning, right_warning)
    placements = {}
    for side, key in zip(('left', 'right'), native.pip_widgets, strict=True):
      position = native.placement(state.customization, 'large', key)
      if position['enabled']:
        width, height = ((position['width'], position['height']) if 'width' in position else
                         native.widget_size(state.customization, 'large', key))
        placements[side] = native.pip_rect(position['x'], position['y'], width, height)
    result = self.pip.render(rect, saved.mask, signals, enabled=True, on_blinker=on_blinker,
                             on_bsm=on_bsm, invert=saved.invert, placements=placements,
                             **({"submit": submit} if submit is not None else {}))
    self._set_pip_showing(result == 'rendered')

  def _set_pip_showing(self, showing):
    alert = getattr(self.onroad, 'alert', None)
    if alert is not None:
      alert.text_only_alert_names = BUBBLE_REDUNDANT_ALERTS if showing else frozenset()

  def _with_certificate_notice(self, state, now_ns):
    """Show the certificate heads-up for the first seconds of each drive; a real alert always wins."""
    if not self._certificate_notice:
      return state
    if self._certificate_notice_until_ns is None:
      self._certificate_notice_until_ns = now_ns + CERTIFICATE_NOTICE_NS
    sizes = self.native.alert_size
    if now_ns >= self._certificate_notice_until_ns or state.alert.size != sizes.NONE:
      return state
    return replace(state, alert=self.native.alert(size=sizes.SMALL, text1=self._certificate_notice,
                                                  alert_type='androidAutoCertificate/warning'))

  def _with_reconnect_notice(self, state, now_ns):
    """Say why the car screen just came back (the comma's Wi-Fi switched networks); a real alert always wins."""
    if not self._reconnect_notice:
      return state
    if self._reconnect_notice_until_ns is None:
      self._reconnect_notice_until_ns = now_ns + RECONNECT_NOTICE_NS
    sizes = self.native.alert_size
    if now_ns >= self._reconnect_notice_until_ns or state.alert.size != sizes.NONE:
      return state
    return replace(state, alert=self.native.alert(size=sizes.SMALL, text1=self._reconnect_notice,
                                                  alert_type='androidAutoReconnected/info'))

  def bookmark_saved(self, now_ns=None):
    """A bookmark was published for this drive (from any button): confirm it on the car screen."""
    self._bookmark_notice_until_ns = (time.monotonic_ns() if now_ns is None else now_ns) + BOOKMARK_NOTICE_NS

  def _with_bookmark_notice(self, state, now_ns):
    """Show "Bookmark Saved" briefly after a bookmark; a real alert always wins."""
    until = self._bookmark_notice_until_ns
    if until is None or now_ns >= until:
      return state
    sizes = self.native.alert_size
    if state.alert.size != sizes.NONE:
      return state
    return replace(state, alert=self.native.alert(size=sizes.SMALL, text1='Bookmark Saved',
                                                  alert_type='androidAutoBookmark/saved'))

  def _with_mode_notice(self, state, now_ns):
    """Show the new driving mode briefly when it changes during a drive; a real alert always wins."""
    label = driving_mode_label(state)
    engaged = bool(getattr(state, 'engaged', True))
    if engaged != self._mode_engaged:
      self._mode_engaged = engaged
      self._mode_label = None  # engaging or disengaging is not a mode change
    if label is not None:
      # Only the driver's own choice is announced, never the planner handing back to conditional mode.
      if self._mode_label is not None and label != self._mode_label and not label.startswith('Conditional'):
        self._mode_notice = (label, now_ns + MODE_NOTICE_NS)
      self._mode_label = label
    if self._mode_notice is None or now_ns >= self._mode_notice[1]:
      return state
    sizes = self.native.alert_size
    if state.alert.size != sizes.NONE:
      return state
    return replace(state, alert=self.native.alert(size=sizes.SMALL, text1=self._mode_notice[0],
                                                  alert_type='androidAutoDrivingMode/changed'))

  def _standby(self):
    rl = self.native.rl
    purple = rl.Color(188, 132, 255, 255)  # StarPilot purple, as on the comma's moon
    rl.draw_rectangle(0, 0, int(self.width), int(self.height), rl.Color(10, 10, 22, 255))
    panel_height = self.height - 280
    panel = rl.Rectangle(120, 140, self.width - 240, panel_height)
    rl.draw_rectangle_rounded(panel, 0.06, 16, rl.Color(26, 16, 46, 255))
    rl.draw_rectangle_rounded_lines_ex(panel, 0.06, 16, 4, purple)
    logo = self._standby_texture(int(panel_height * 0.6))
    text_x = 220
    if logo is not None:
      rl.draw_texture(logo, 200, int((self.height - logo.height) / 2), rl.WHITE)
      text_x = 200 + logo.width + 70
    middle = self.height / 2
    self.fonts.draw('StarPilot', self.native.font_role.BRAND, 112, text_x, middle - 130, purple)
    self.fonts.draw('Ready when driving', self.native.font_role.MEDIUM, 55, text_x + 5, middle + 20, rl.Color(222, 204, 255, 255))

  def _standby_texture(self, height):
    """Load the logo the first time standby shows; a missing logo leaves the text-only screen."""
    if not self._standby_logo_tried:
      self._standby_logo_tried = True
      load = getattr(self.native, 'standby_logo', None)
      if load is not None:
        try:
          self._standby_logo = load(height)
        except Exception:
          self._standby_logo = None
        if self._standby_logo is not None:
          self._resources.callback(self._unload_standby_logo)
    return self._standby_logo

  def _unload_standby_logo(self):
    if self._standby_logo is not None:
      self.native.rl.unload_texture(self._standby_logo)
      self._standby_logo = None

  def render(self):
    ui = self.native.ui_state
    self.camera_stream = None
    if ui.started and self.content == 'birds_eye':
      self._state = None
      self._render_birds_eye_only()
    elif ui.started:
      now_ns = time.monotonic_ns()
      timer_enabled = bool((self.customization or {}).get('widgets', {}).get('stopped_timer', {}).get('enabled', False))
      state = self.adapter.build(self.native.shell_mode.ONROAD, now_ns=now_ns, stopped_timer_enabled=timer_enabled).onroad
      if self.customization is not None:
        from openpilot.starpilot.system.starport.projection_layout import projection_customization
        if state.customization is not self._base_customization:
          self._projection_customization = projection_customization(self.customization, state.customization)
          self._base_customization = state.customization
        if state.appearance is not self._base_appearance:
          # The car screen's display options live in the AA layout, not the comma's settings.
          self._projection_appearance = self._own_appearance(state.appearance)
          self._base_appearance = state.appearance
        state = replace(state, customization=self._projection_customization, appearance=self._projection_appearance,
                        reverse_driver_camera=False)
      # Touch hit-tests read viewport_width; the view draws at the car canvas width (1920 for a 1280x720 head unit).
      if state.viewport_width != self.width:
        state = replace(state, viewport_width=self.width)
      state = self._with_certificate_notice(self._with_reconnect_notice(state, now_ns), now_ns)
      state = self._with_bookmark_notice(state, now_ns)
      self._state = self._with_mode_notice(state, now_ns)
      if self._slc_input is not None:
        self._state = self._slc_input.present(self._state)
      self.onroad.render(self._state)
      self.fonts.draw('StarPilot', self.native.font_role.BRAND, 30, self.width - 210, self.height - 90)
    else:
      self._state = None
      if self.favorites is not None:
        self.favorites.cancel()
      self._certificate_notice_until_ns = None  # the next drive shows the heads-up again
      self._mode_label = self._mode_engaged = self._mode_notice = None  # a drive's starting mode is not a change
      self._monitor_pair = None
      self._monitor_pair_ns = None
      if self.pip is not None:
        self.pip.deactivate()
        self._set_pip_showing(False)
      if getattr(self.onroad, "uniden", None):
        self.onroad.uniden.feed.set_enabled(False)
      self._standby()
      self.exit.draw(self.exit.document_placement(self.customization))

  def handle_touches(self, events):
    placed = self.exit.document_placement(self.customization)
    if self.content == 'birds_eye':
      # Driving, only the now-playing card takes taps (the exit button is not drawn); on standby only the exit button does.
      started = self.native.ui_state.started
      touch = self.now_playing_touch
      for event in events:
        x, y = event.x * self.width, event.y * self.height
        if started:
          self.exit.cancel()
          if touch is not None:
            state, card = self._birds_eye_now_playing()
            touch.touch(event.kind, x, y, state, card, self.now_playing.showing())
        else:
          if touch is not None:
            touch.cancel()
          self.exit.touch(event.kind, x, y, placed)
      return
    for event in events:
      x, y = event.x * self.width, event.y * self.height
      if self.exit.touch(event.kind, x, y, placed):
        if self.favorites is not None:
          self.favorites.cancel()
        continue
      if getattr(self.onroad, 'uniden', None) and self.onroad.uniden.touch(event.kind, x, y, self._state):
        if self.favorites is not None:
          self.favorites.cancel()
        continue
      if self._slc_touch(event.kind, x, y):
        if self.favorites is not None:
          self.favorites.cancel()
        continue
      if self._now_playing_touch(event.kind, x, y):
        if self.favorites is not None:
          self.favorites.cancel()
        continue
      # After the card: the view may lie under it (always in full display), and the card keeps its taps.
      if self._birds_eye_touch(event.kind, x, y):
        if self.favorites is not None:
          self.favorites.cancel()
        continue
      if self.favorites is None:
        continue
      if self._state is None or not self.native.ui_state.started:
        self.favorites.cancel()
        continue
      self.favorites.touch(event.kind, x, y, self._state, self.native.ui_state.started_frame)

  def _now_playing_touch(self, kind, x, y) -> bool:
    touch = self.now_playing_touch
    if touch is None:
      return False
    state = self._state if self.native.ui_state.started else None
    placed = state.customization['layouts']['large'].get('now_playing') if state is not None else None
    track = self.onroad.now_playing.showing()
    if state is not None and self.favorites is not None:
      from openpilot.starpilot.ui.onroad_now_playing import card_layout
      shape = card_layout(placed, track, state.viewport_width)
      if shape is not None and self.favorites.hit(x, y, state, overlay=('now_playing', shape[0])) != 'now_playing':
        touch.cancel()
        return False
    return touch.touch(kind, x, y, state, placed, track)

  def _slc_touch(self, kind, x, y) -> bool:
    """Speed-limit confirmation taps; True while one owns the touch."""
    gesture = self._slc_input
    if gesture is None:
      return False
    state = self._state
    if kind == 'cancel' or not hasattr(state, 'speed_limit') or not self.native.ui_state.started:
      claimed = gesture.claimed
      gesture.cancel()
      return claimed
    if kind == 'down':
      gesture.press(x, y, state)
    elif kind == 'move':
      gesture.move(x, y, state)
    elif kind == 'up':
      claimed = gesture.claimed
      gesture.release(x, y, state)
      return claimed
    return gesture.claimed

  def draw_touch_setup(self, x, y, done=False):
    """One tap on this dot tells StarPort how the car reports touches."""
    rl, role = self.native.rl, self.native.font_role.BOLD
    if done:
      text, size = "Touch is set up", 64
      width = self.fonts.measure(text, role, size).width
      text_x = max(30, x - 90 - width)
      rl.draw_rectangle_rounded(rl.Rectangle(text_x - 24, y - size / 2 - 16, width + 48, size + 32), .4, 8, rl.Color(15, 13, 23, 200))
      self.fonts.draw(text, role, size, text_x, y - size / 2)
      return
    rl.draw_circle(int(x), int(y), 56, rl.Color(15, 13, 23, 200))
    rl.draw_circle_lines(int(x), int(y), 56, rl.Color(199, 174, 247, 255))
    rl.draw_circle(int(x), int(y), 22, rl.Color(199, 174, 247, 255))
    text, size = "Tap the dot to set up touch", 64
    width = self.fonts.measure(text, role, size).width
    text_x = max(30, x - 90 - width)
    rl.draw_rectangle_rounded(rl.Rectangle(text_x - 24, y - size / 2 - 16, width + 48, size + 32), .4, 8, rl.Color(15, 13, 23, 200))
    self.fonts.draw(text, role, size, text_x, y - size / 2)

  def cancel_touch(self):
    if getattr(self.onroad, 'uniden', None):
      self.onroad.uniden.touch('cancel', 0, 0, None)
    self.exit.cancel()
    self._birds_eye_press = None
    if self.now_playing_touch is not None:
      self.now_playing_touch.cancel()
    if self._slc_input is not None:
      self._slc_input.cancel()
    if self.favorites is not None:
      self.favorites.cancel()

  def close(self):
    self._resources.close()
