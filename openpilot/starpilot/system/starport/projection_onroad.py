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
DRIVER_MONITOR_HOLD_NS = 350_000_000
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
  from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.onroad_customization import CAMERA_WIDGETS, placement, widget_size
  from openpilot.starpilot.ui.onroad_dm import DriverMonitorLayer
  from openpilot.starpilot.ui.onroad_map import MapFeed, MapOverlay
  from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert
  from openpilot.starpilot.ui.pip_preferences import read_pip
  from openpilot.starpilot.ui.pip_render import PiPRenderer
  from openpilot.starpilot.ui.pip_sidecam import Rect, Signals
  from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile, default_font_directory
  from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter, current_message, display_message
  from openpilot.starpilot.ui.shell import ShellMode
  from openpilot.starpilot.system.starport.projection_favorites import ProjectionFavorites
  from openpilot.starpilot.system.starport.projection_now_playing import ProjectionNowPlayingFeed
  from openpilot.starpilot.models.jetlink_status import JetlinkStatusFeed

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

  return SimpleNamespace(rl=rl, ui_state=ui_state, camera=ProjectionRoadCamera,
                         onroad=OnroadView, monitor=DriverMonitorLayer, fonts=BitmapFonts,
                         font_role=FontRole, profile=Profile, font_directory=default_font_directory,
                         adapter=RuntimeSnapshotAdapter, current_message=current_message, display_message=display_message,
                         shell_mode=ShellMode, pip_renderer=PiPRenderer, read_pip=read_pip, pip_signals=Signals,
                         pip_rect=Rect, pip_widgets=CAMERA_WIDGETS, placement=placement, widget_size=widget_size,
                         alert=OnroadAlert, alert_size=AlertSize, map_overlay=MapOverlay, map_feed=MapFeed,
                         favorites=ProjectionFavorites, standby_logo=standby_logo, now_playing_feed=ProjectionNowPlayingFeed,
                         jetlink_feed=JetlinkStatusFeed)


class ProjectionOnroad:
  """Separate renderer with projected actions; native UIState stays read-only."""

  def __init__(self, *, dependencies=None, viewport=None, customization=None, certificate_days=None, native_focus=None,
               layout_watcher=None):
    viewport = FALLBACK_VIEWPORT if viewport is None else viewport
    self.width, self.height = viewport
    self.customization = customization
    self._certificate_notice = certificate_notice(certificate_days)
    self._certificate_notice_until_ns: int | None = None
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
    self.native = dependencies or native_dependencies()
    native = self.native
    self._standby_logo = None
    self._standby_logo_tried = False
    if native.ui_state.projection_read_only is not True:
      raise RuntimeError('Projection UIState must be read-only')
    self._resources = ExitStack()
    try:
      self.fonts = native.fonts(native.profile.LARGE, native.font_directory(), headless_context=True)
      self._resources.callback(self.fonts.close)
      self.camera = native.camera()
      self.camera.large_ui_gamma_trial = bool((customization or {}).get('largeUiGammaTrial', False))
      self._resources.callback(self._close_camera)
      self.onroad = self.create_view(native.onroad, self.fonts, camera_layer=self._camera_layer, viewport=viewport)
      self._resources.callback(self._close_onroad)
      self.now_playing_feed = None
      if getattr(native, 'now_playing_feed', None) is not None:
        self.now_playing_feed = native.now_playing_feed()
        self._resources.callback(self.now_playing_feed.close)
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
      if getattr(native, 'pip_renderer', None) is not None:
        self.pip = native.pip_renderer("bubble")
        self._resources.callback(self.pip.close)
        self.onroad.pip_layer = self._pip_layer
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
        self._resources.callback(self.favorites.close)
    except BaseException:
      self._resources.close()
      raise

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

  def _camera_layer(self, rect, state):
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
    self.native.rl.rl_push_matrix()
    self.native.rl.rl_translatef(0, self.height - 1080, 0)
    try:
      self.monitor.render(state, monitor=monitor, driver=driver,
                          fresh=monitor is not None and driver is not None,
                          onroad=ui.is_onroad())
    finally:
      self.native.rl.rl_pop_matrix()

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
    self._base_customization = self._projection_customization = None
    self._base_appearance = self._projection_appearance = None
    self.camera.large_ui_gamma_trial = bool((customization or {}).get('largeUiGammaTrial', False))
    invalidate = getattr(self.adapter, 'invalidate_appearance', None)
    if invalidate is not None:
      invalidate()
    self._ensure_map()

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
    if self.now_playing_feed is not None:
      placed = (self.customization or {}).get('widgets', {}).get('now_playing', {})
      self.now_playing_feed.set_enabled(bool(placed.get('enabled')))
      self.onroad.now_playing.track = self.now_playing_feed.snapshot()
    if self.jetlink_feed is not None:
      placed = (self.customization or {}).get('widgets', {}).get('jetlink_status', {})
      self.jetlink_feed.set_enabled(bool(placed.get('enabled') and self.native.ui_state.started))
      self.onroad.jetlink_status.status = self.jetlink_feed.snapshot()
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

  def _pip_layer(self, rect, state, *, submit=None):
    """Shared camera crops, with Android Auto's own blind-spot trigger and placements."""
    native = self.native
    now_ns = time.monotonic_ns()
    if self._pip_read_ns is None or not 0 <= now_ns - self._pip_read_ns < 1_000_000_000:
      self._pip_saved = native.read_pip(native.ui_state.params)
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
    signals = native.pip_signals(car is not None,
                                 bool(car.leftBlinker) if car is not None else False,
                                 bool(car.rightBlinker) if car is not None else False,
                                 bool(car.leftBlindspot) if car is not None else False,
                                 bool(car.rightBlindspot) if car is not None else False)
    placements = {}
    for side, key in zip(('left', 'right'), native.pip_widgets, strict=True):
      position = native.placement(state.customization, 'large', key)
      if position['enabled']:
        width, height = native.widget_size(state.customization, 'large', key)
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
    if ui.started:
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
      state = self._with_bookmark_notice(self._with_certificate_notice(state, now_ns), now_ns)
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
      self._standby()
      self.exit.draw(self.exit.document_placement(self.customization))

  def handle_touches(self, events):
    placed = self.exit.document_placement(self.customization)
    for event in events:
      x, y = event.x * self.width, event.y * self.height
      if self.exit.touch(event.kind, x, y, placed):
        if self.favorites is not None:
          self.favorites.cancel()
        continue
      if self._slc_touch(event.kind, x, y):
        if self.favorites is not None:
          self.favorites.cancel()
        continue
      if self.favorites is None:
        continue
      if self._state is None or not self.native.ui_state.started:
        self.favorites.cancel()
        continue
      self.favorites.touch(event.kind, x, y, self._state, self.native.ui_state.started_frame)

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

  def cancel_touch(self):
    self.exit.cancel()
    if self._slc_input is not None:
      self._slc_input.cancel()
    if self.favorites is not None:
      self.favorites.cancel()

  def close(self):
    self._resources.close()
