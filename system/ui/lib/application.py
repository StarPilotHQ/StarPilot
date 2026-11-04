import atexit
import cffi
import math
import os
import queue
import time
import signal
import sys
import pyray as rl
import threading
import platform
import subprocess
from contextlib import contextmanager
from collections.abc import Callable
from collections import deque
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple
from importlib.resources import as_file, files
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE, PC
from openpilot.system.ui.lib.multilang import multilang
from openpilot.common.realtime import Ratekeeper

DEVICE_TYPE = HARDWARE.get_device_type()
_DEFAULT_FPS = int(os.getenv("FPS", "60"))
FPS_LOG_INTERVAL = 5  # Seconds between logging FPS drops
FPS_DROP_THRESHOLD = 0.9  # FPS drop threshold for triggering a warning
FPS_CRITICAL_THRESHOLD = 0.5  # Critical threshold for triggering strict actions
MOUSE_THREAD_RATE = 140  # touch controller runs at 140Hz
DESKTOP_MOUSE_THREAD_RATE = int(os.getenv("DESKTOP_MOUSE_RATE", "500"))
DESKTOP_CLICK_DEBOUNCE = float(os.getenv("DESKTOP_CLICK_DEBOUNCE", "0.2"))
UI_IDLE_FPS = int(os.getenv("UI_IDLE_FPS", "0"))
UI_INTERACTION_FPS_DURATION = 1.25
MAX_TOUCH_SLOTS = 2
TOUCH_HISTORY_TIMEOUT = 3.0  # Seconds before touch points fade out

BIG_UI = os.getenv("BIG", "0") == "1"
MACOS = platform.system() == "Darwin"
ENABLE_VSYNC = os.getenv("ENABLE_VSYNC", "0") == "1"
MICI_FORCE_RENDER_TEXTURE = os.getenv("MICI_FORCE_RENDER_TEXTURE", "0") == "1"
TICI_FORCE_RENDER_TEXTURE = os.getenv("TICI_FORCE_RENDER_TEXTURE", "0") == "1"
BURN_IN_PREVENTION = os.getenv("BURN_IN_PREVENTION", "0" if PC else "1") == "1"
BURN_IN_SHIFT_INTERVAL = max(1.0, float(os.getenv("BURN_IN_SHIFT_INTERVAL", "180")))
BURN_IN_SHIFT_PIXELS = max(0, int(os.getenv("BURN_IN_SHIFT_PIXELS", "2")))
BURN_IN_SHIFT_TRANSITION_SECONDS = min(
  BURN_IN_SHIFT_INTERVAL,
  max(0.1, float(os.getenv("BURN_IN_SHIFT_TRANSITION_SECONDS", "1"))),
)
WHITE_LUMINANCE_CAP = min(1.0, max(0.0, float(os.getenv(
  "WHITE_LUMINANCE_CAP", "1.0"
))))
SHOW_FPS = os.getenv("SHOW_FPS") == "1"
SHOW_TOUCHES = os.getenv("SHOW_TOUCHES") == "1"
STRICT_MODE = os.getenv("STRICT_MODE") == "1"
SCALE = float(os.getenv("SCALE", "1.0"))
GRID_SIZE = int(os.getenv("GRID", "0"))
PROFILE_RENDER = int(os.getenv("PROFILE_RENDER", "0"))
PROFILE_STATS = int(os.getenv("PROFILE_STATS", "100"))  # Number of functions to show in profile output
RECORD = os.getenv("RECORD") == "1"
# Optional recording extensions for desktop replay only. The PC guard prevents
# these modes from being enabled on comma hardware.
RECORD_HUD_ONLY = PC and RECORD and os.getenv("RECORD_HUD_ONLY") == "1"
RECORD_CAMERA_ONLY = PC and RECORD and os.getenv("RECORD_CAMERA_ONLY") == "1"
RECORD_COMBINED = PC and RECORD and os.getenv("RECORD_COMBINED") == "1"
RECORD_METRIC = PC and RECORD and os.getenv("RECORD_METRIC") == "1"
RECORD_CAMERA_RESOLUTION = os.getenv("RECORD_CAMERA_RESOLUTION", "render").strip().lower() if (RECORD_CAMERA_ONLY or RECORD_COMBINED) else "render"
RECORD_OUTPUT = str(Path(os.getenv("RECORD_OUTPUT", "output")).with_suffix(".mkv" if (RECORD_HUD_ONLY or RECORD_CAMERA_ONLY) else ".mp4"))
RECORD_QUALITY = int(os.getenv("RECORD_QUALITY", "23"))  # Dynamic bitrate quality level (CRF); 0 is lossless (bigger size), max is 51, default is 23 for x264
RECORD_BITRATE = os.getenv("RECORD_BITRATE", "")  # Target bitrate e.g. "2000k" (overrides RECORD_QUALITY when set)
RECORD_SPEED = int(os.getenv("RECORD_SPEED", "1"))  # Speed multiplier
RECORD_DURATION = float(os.getenv("RECORD_DURATION", "0")) if PC and RECORD else 0.0
OFFSCREEN = os.getenv("OFFSCREEN") == "1"  # Disable FPS limiting for fast offline rendering


def _raylib_target_fps(fps: int) -> int:
  return 0 if OFFSCREEN or (DEVICE_TYPE == "mici" and not PC) else fps

GL_VERSION = """
#version 300 es
precision highp float;
"""
if platform.system() == "Darwin":
  GL_VERSION = """
    #version 330 core
  """

BURN_IN_MODE = "BURN_IN" in os.environ
BURN_IN_SHIFT_PATTERN = (
  (0, 0),
  (-1, 0),
  (-1, -1),
  (0, -1),
  (1, -1),
  (1, 0),
  (1, 1),
  (0, 1),
  (-1, 1),
)
BURN_IN_VERTEX_SHADER = GL_VERSION + """
in vec3 vertexPosition;
in vec2 vertexTexCoord;
uniform mat4 mvp;
out vec2 fragTexCoord;
void main() {
  fragTexCoord = vertexTexCoord;
  gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""
BURN_IN_FRAGMENT_SHADER = GL_VERSION + """
in vec2 fragTexCoord;
uniform sampler2D texture0;
out vec4 fragColor;
void main() {
  vec4 sampled = texture(texture0, fragTexCoord);
  float intensity = sampled.b;
  // Map blue intensity to green -> yellow -> red to highlight burn-in risk.
  vec3 start = vec3(0.0, 1.0, 0.0);
  vec3 middle = vec3(1.0, 1.0, 0.0);
  vec3 end = vec3(1.0, 0.0, 0.0);
  vec3 gradient = mix(start, middle, clamp(intensity * 2.0, 0.0, 1.0));
  gradient = mix(gradient, end, clamp((intensity - 0.5) * 2.0, 0.0, 1.0));
  fragColor = vec4(gradient, sampled.a);
}
"""
WHITE_LUMINANCE_FRAGMENT_SHADER = GL_VERSION + """
in vec2 fragTexCoord;
uniform sampler2D texture0;
uniform float whiteLuminanceCap;
out vec4 fragColor;
void main() {
  vec4 sampled = texture(texture0, fragTexCoord);
  float luminance = dot(sampled.rgb, vec3(0.2126, 0.7152, 0.0722));
  float chroma = max(max(sampled.r, sampled.g), sampled.b) - min(min(sampled.r, sampled.g), sampled.b);

  // Gently compress only near-white, low-saturation pixels. Saturated alert colors
  // and the vast majority of camera pixels pass through unchanged.
  float knee = max(0.0, whiteLuminanceCap - 0.05);
  if (luminance > knee) {
    float kneeRange = max(0.0001, 1.0 - knee);
    float targetLuminance = knee + (whiteLuminanceCap - knee) * ((luminance - knee) / kneeRange);
    float neutralAmount = 1.0 - smoothstep(0.08, 0.25, chroma);
    sampled.rgb *= mix(1.0, targetLuminance / max(luminance, 0.0001), neutralAmount);
  }

  fragColor = sampled;
}
"""

DEFAULT_TEXT_SIZE = 60
DEFAULT_TEXT_COLOR = rl.Color(255, 255, 255, int(255 * 0.9))

# Compensate for ascent/descent so migrated layouts keep their established alignment.
# The real scales for the fonts below range from 1.212 to 1.266
FONT_SCALE = 1.242 if BIG_UI else 1.16

ASSETS_DIR = files("openpilot.selfdrive").joinpath("assets")
FONT_DIR = ASSETS_DIR.joinpath("fonts")


class FontWeight(StrEnum):
  NORMAL = "Inter-Regular.fnt" if BIG_UI else "Inter-Medium.fnt"
  MEDIUM = "Inter-Medium.fnt"
  BOLD = "Inter-Bold.fnt"
  SEMI_BOLD = "Inter-SemiBold.fnt"
  UNIFONT = "unifont.fnt"
  BRAND = "como-heavy.fnt"

  # Small UI fonts
  DISPLAY_REGULAR = "Inter-Regular.fnt"
  ROMAN = "Inter-Regular.fnt"
  DISPLAY = "Inter-Bold.fnt"


def font_fallback(font: rl.Font) -> rl.Font:
  """Fall back to unifont for languages that require it."""
  if multilang.requires_unifont():
    try:
      if font.texture.id == gui_app.font(FontWeight.BRAND).texture.id:
        return font
    except (AttributeError, KeyError):
      pass
    return gui_app.font(FontWeight.UNIFONT)
  return font


class MousePos(NamedTuple):
  x: float
  y: float


class MousePosWithTime(NamedTuple):
  x: float
  y: float
  t: float


class MouseEvent(NamedTuple):
  pos: MousePos
  slot: int
  left_pressed: bool
  left_released: bool
  left_down: bool
  t: float


class FrameTiming(NamedTuple):
  frame_ms: float = 0.0
  cpu_ms: float = 0.0
  draw_ms: float = 0.0
  update_ms: float = 0.0
  present_ms: float = 0.0


class DesktopMouseSample(NamedTuple):
  pos: MousePos
  left_pressed: bool
  left_released: bool
  left_down: bool
  t: float


class DesktopMouseProvider:
  def sample(self) -> tuple[MousePos, bool]:
    raise NotImplementedError

  def close(self) -> None:
    pass

  @staticmethod
  def create() -> "DesktopMouseProvider | None":
    if MACOS:
      return MacOSDesktopMouseProvider()
    if platform.system() == "Linux":
      return LinuxDesktopMouseProvider()
    if platform.system() == "Windows":
      return WindowsDesktopMouseProvider()
    return None


class MacOSDesktopMouseProvider(DesktopMouseProvider):
  def __init__(self):
    import Quartz
    self._quartz = Quartz

  def sample(self) -> tuple[MousePos, bool]:
    q = self._quartz
    loc = q.CGEventGetLocation(q.CGEventCreate(None))
    left_down = (
      q.CGEventSourceButtonState(q.kCGEventSourceStateHIDSystemState, q.kCGMouseButtonLeft) or
      q.CGEventSourceButtonState(q.kCGEventSourceStateCombinedSessionState, q.kCGMouseButtonLeft)
    )
    return MousePos(loc.x, loc.y), bool(left_down)


class LinuxDesktopMouseProvider(DesktopMouseProvider):
  def __init__(self):
    from Xlib import X, display
    self._button_mask = X.Button1Mask
    self._display = display.Display()
    self._root = self._display.screen().root

  def sample(self) -> tuple[MousePos, bool]:
    data = self._root.query_pointer()._data
    return MousePos(data["root_x"], data["root_y"]), bool(data["mask"] & self._button_mask)

  def close(self) -> None:
    self._display.close()


class WindowsDesktopMouseProvider(DesktopMouseProvider):
  def __init__(self):
    import ctypes
    self._ctypes = ctypes
    self._user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
      _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    self._point_cls = POINT
    try:
      self._user32.SetProcessDPIAware()
    except AttributeError:
      pass

  def sample(self) -> tuple[MousePos, bool]:
    point = self._point_cls()
    self._user32.GetCursorPos(self._ctypes.byref(point))
    left_down = bool(self._user32.GetAsyncKeyState(0x01) & 0x8000)
    return MousePos(point.x, point.y), left_down


class MouseState:
  def __init__(self, scale: float = 1.0):
    self._scale = scale
    self._events: deque[MouseEvent] = deque(maxlen=MOUSE_THREAD_RATE)  # bound event list
    self._prev_mouse_event: list[MouseEvent | None] = [None] * MAX_TOUCH_SLOTS

    self._rk = Ratekeeper(MOUSE_THREAD_RATE, print_delay_threshold=None)
    self._lock = threading.Lock()
    self._exit_event = threading.Event()
    self._thread = None
    self._desktop_left_down = False
    self._desktop_click_active = False
    self._desktop_click_suppressed = False
    self._desktop_last_click_t = -math.inf
    self._desktop_samples: deque[DesktopMouseSample] = deque(maxlen=DESKTOP_MOUSE_THREAD_RATE)
    self._desktop_provider: DesktopMouseProvider | None = None

  def get_events(self) -> list[MouseEvent]:
    with self._lock:
      events = list(self._events)
      self._events.clear()
    return events

  def start(self):
    self._exit_event.clear()
    if self._thread is None or not self._thread.is_alive():
      self._thread = threading.Thread(target=self._run_thread, daemon=True)
      self._thread.start()

  def start_desktop_mouse_sampler(self):
    try:
      self._desktop_provider = DesktopMouseProvider.create()
    except Exception:
      cloudlog.exception("Failed to initialize desktop mouse sampler")
      self._desktop_provider = None

    if self._desktop_provider is None:
      return

    self._exit_event.clear()
    if self._thread is None or not self._thread.is_alive():
      self._thread = threading.Thread(target=self._run_desktop_thread, daemon=True)
      self._thread.start()

  def stop(self):
    self._exit_event.set()
    if self._thread is not None and self._thread.is_alive():
      self._thread.join()
    if self._desktop_provider is not None:
      self._desktop_provider.close()
      self._desktop_provider = None
    self._desktop_left_down = False
    self._desktop_click_active = False
    self._desktop_click_suppressed = False

  def _desktop_mouse_pos(self) -> MousePos:
    mouse_pos = rl.get_mouse_position()
    return MousePos(mouse_pos.x, mouse_pos.y)

  def _desktop_window_pos(self) -> MousePos:
    window_pos = rl.get_window_position()
    return MousePos(window_pos.x, window_pos.y)

  def _run_thread(self):
    while not self._exit_event.is_set():
      rl.poll_input_events()
      self._handle_mouse_event()
      self._rk.keep_time()

  def _run_desktop_thread(self):
    rk = Ratekeeper(DESKTOP_MOUSE_THREAD_RATE, print_delay_threshold=None)
    prev_pos: MousePos | None = None
    prev_left_down = False

    while not self._exit_event.is_set():
      try:
        assert self._desktop_provider is not None
        pos, left_down = self._desktop_provider.sample()
      except Exception:
        cloudlog.exception("Desktop mouse sampler failed")
        self._desktop_provider = None
        break

      left_pressed = left_down and not prev_left_down
      left_released = prev_left_down and not left_down
      if left_pressed or left_released or pos != prev_pos:
        with self._lock:
          self._desktop_samples.append(DesktopMouseSample(
            pos,
            left_pressed,
            left_released,
            left_down,
            time.monotonic(),
          ))

      prev_pos = pos
      prev_left_down = left_down
      rk.keep_time()

  def _get_desktop_samples(self) -> list[DesktopMouseSample]:
    with self._lock:
      samples = list(self._desktop_samples)
      self._desktop_samples.clear()
    return samples

  def _debounce_desktop_mouse_event(self, ev: MouseEvent) -> MouseEvent | None:
    if ev.left_pressed:
      if ev.t - self._desktop_last_click_t < DESKTOP_CLICK_DEBOUNCE:
        self._desktop_click_active = False
        self._desktop_click_suppressed = True
        return None

      self._desktop_click_active = True
      self._desktop_click_suppressed = False
      return ev

    if self._desktop_click_suppressed:
      if ev.left_released or not ev.left_down:
        self._desktop_click_suppressed = False
      return None

    if ev.left_released:
      if not self._desktop_click_active:
        return None

      self._desktop_click_active = False
      self._desktop_last_click_t = ev.t
      return ev

    if ev.left_down and not self._desktop_click_active:
      return None

    return ev

  def _handle_mouse_event(self):
    # TODO: read touch events from evdev directly to get real kernel timestamps.
    #  Polling at 140Hz with time.monotonic() causes timing jitter that makes scroll
    #  velocity oscillate (alternating high/low). Real timestamps would also let us
    #  detect swipe-stop-lift via event gaps instead of the fragile decel heuristic.
    if PC:
      if self._desktop_provider is not None:
        scale = self._scale if self._scale != 0 else 1.0
        window_pos = self._desktop_window_pos()
        for sample in self._get_desktop_samples():
          local_pos = MousePos(
            (sample.pos.x - window_pos.x) / scale,
            (sample.pos.y - window_pos.y) / scale,
          )
          event = self._debounce_desktop_mouse_event(MouseEvent(
            local_pos,
            0,
            sample.left_pressed,
            sample.left_released,
            sample.left_down,
            sample.t,
          ))
          if event is not None:
            self._append_mouse_event(event)
        return

      left_down = rl.is_mouse_button_down(rl.MouseButton.MOUSE_BUTTON_LEFT)
      left_pressed = (
        rl.is_mouse_button_pressed(rl.MouseButton.MOUSE_BUTTON_LEFT) or  # noqa: TID251
        (left_down and not self._desktop_left_down)
      )
      left_released = (
        rl.is_mouse_button_released(rl.MouseButton.MOUSE_BUTTON_LEFT) or  # noqa: TID251
        (self._desktop_left_down and not left_down)
      )
      self._append_mouse_event(MouseEvent(
        self._desktop_mouse_pos(),
        0,
        left_pressed,
        left_released,
        left_down,
        time.monotonic(),
      ))
      self._desktop_left_down = left_down
      return

    for slot in range(MAX_TOUCH_SLOTS):
      mouse_pos = rl.get_touch_position(slot)
      x = mouse_pos.x / self._scale if self._scale != 1.0 else mouse_pos.x
      y = mouse_pos.y / self._scale if self._scale != 1.0 else mouse_pos.y
      self._append_mouse_event(MouseEvent(
        MousePos(x, y),
        slot,
        rl.is_mouse_button_pressed(slot),  # noqa: TID251
        rl.is_mouse_button_released(slot),  # noqa: TID251
        rl.is_mouse_button_down(slot),
        time.monotonic(),
      ))

  def _append_mouse_event(self, ev: MouseEvent):
    if ev.left_pressed and ev.left_released:
      press_ev = MouseEvent(ev.pos, ev.slot, True, False, True, ev.t)
      release_ev = MouseEvent(ev.pos, ev.slot, False, True, False, ev.t)
      self._append_mouse_event(press_ev)
      self._append_mouse_event(release_ev)
      return

    # Only add changes
    prev = self._prev_mouse_event[ev.slot]
    if prev is None or ev[:-1] != prev[:-1]:
      with self._lock:
        self._events.append(ev)
      self._prev_mouse_event[ev.slot] = ev


class GuiApplication:
  def __init__(self, width: int | None = None, height: int | None = None):
    self._set_log_callback()

    self._fonts: dict[FontWeight, rl.Font] = {}
    self._width = width if width is not None else GuiApplication._default_width()
    self._height = height if height is not None else GuiApplication._default_height()

    # Near-source recording scale for the 536x240 Mici/C4 desktop canvas only.
    # Keeps the UI aspect/geometry intact; this is not a raw camera-resolution export.
    if (RECORD_CAMERA_ONLY or RECORD_COMBINED) and RECORD_CAMERA_RESOLUTION == "source" and self._width == 536:
      self._scale = 2.5
    elif PC and os.getenv("SCALE") is None:
      self._scale = self._calculate_auto_scale()
    else:
      self._scale = SCALE

    # Scale, then ensure dimensions are even
    self._scaled_width = int(self._width * self._scale)
    self._scaled_height = int(self._height * self._scale)
    self._scaled_width += self._scaled_width % 2
    self._scaled_height += self._scaled_height % 2
    self._pixel_scale_x = 1.0
    self._pixel_scale_y = 1.0
    self._render_texture_width = self._scaled_width
    self._render_texture_height = self._scaled_height

    self._render_texture: rl.RenderTexture | None = None
    self._burn_in_shader: rl.Shader | None = None
    self._white_luminance_shader: rl.Shader | None = None
    self._ffmpeg_proc: subprocess.Popen | None = None
    self._ffmpeg_queue: queue.Queue | None = None
    self._ffmpeg_thread: threading.Thread | None = None
    self._ffmpeg_stop_event: threading.Event | None = None
    self._recorded_frames = 0
    self._progress_hook: Callable[[str], None] | None = None
    self._textures: dict[str, rl.Texture] = {}
    self._cached_render_textures: dict[str, rl.RenderTexture] = {}
    self._pending_render_textures: dict[str, tuple[int, int, Callable[[], None]]] = {}
    self._target_fps: int = _DEFAULT_FPS
    self._full_target_fps: int = _DEFAULT_FPS
    self._idle_target_fps: int = max(10, _DEFAULT_FPS // 4)
    self._adaptive_rendering = False
    self._full_rate_rendering = False
    self._high_fps_until = 0.0
    self._last_fps_log_time: float = time.monotonic()
    self._burn_in_start_time = time.monotonic()
    self._frame = 0
    self.frame_timing = FrameTiming()
    self._window_close_requested = False
    self._nav_stack: list[object] = []
    self._nav_stack_ticks: list[Callable[[], None]] = []
    self._nav_stack_widgets_to_render = 1 if self.big_ui() else 2

    self._mouse = MouseState(self._scale)
    self._mouse_events: list[MouseEvent] = []
    self._last_mouse_event: MouseEvent = MouseEvent(MousePos(0, 0), 0, False, False, False, 0.0)

    self._should_render = True

    # Debug variables
    self._mouse_history: deque[MousePosWithTime] = deque(maxlen=MOUSE_THREAD_RATE)
    self._show_touches = SHOW_TOUCHES
    self._show_fps = SHOW_FPS
    self._grid_size = GRID_SIZE
    self._profile_render_frames = PROFILE_RENDER
    self._render_profiler = None
    self._render_profile_start_time = None

  @property
  def frame(self):
    return self._frame

  def set_show_touches(self, show: bool):
    self._show_touches = show

  def set_show_fps(self, show: bool):
    self._show_fps = show

  @property
  def show_touches(self) -> bool:
    return self._show_touches

  @property
  def target_fps(self):
    return self._target_fps

  def _set_target_fps(self, fps: int) -> None:
    fps = max(1, int(fps))
    if fps == self._target_fps:
      return
    rl.set_target_fps(_raylib_target_fps(fps))
    self._target_fps = fps

  def configure_adaptive_rendering(self, enabled: bool, idle_fps: int | None = None) -> None:
    """Enable low-rate rendering for static BIG-UI offroad screens.

    The normal target remains unchanged unless a caller opts in. This keeps
    MICI and all existing non-BIG layouts on their current scheduling path.
    """
    # Recording feeds raw frames to ffmpeg at the fixed full FPS, so changing
    # the producer rate would make idle portions play back too quickly.
    self._adaptive_rendering = bool(enabled and not OFFSCREEN and not RECORD)
    if idle_fps is None or idle_fps <= 0:
      idle_fps = UI_IDLE_FPS if UI_IDLE_FPS > 0 else max(10, self._full_target_fps // 4)
    self._idle_target_fps = min(self._full_target_fps, max(1, int(idle_fps)))
    self._full_rate_rendering = False
    self._high_fps_until = time.monotonic() + UI_INTERACTION_FPS_DURATION if self._adaptive_rendering else 0.0
    if self._adaptive_rendering:
      self._apply_render_mode()
    else:
      self._set_target_fps(self._full_target_fps)

  def request_high_fps(self, duration: float = UI_INTERACTION_FPS_DURATION) -> None:
    if not self._adaptive_rendering:
      return
    self._high_fps_until = max(self._high_fps_until, time.monotonic() + max(0.0, duration))
    self._apply_render_mode()

  def set_render_mode(self, active: bool) -> None:
    if not self._adaptive_rendering:
      return
    self._full_rate_rendering = active
    self._apply_render_mode()

  def _apply_render_mode(self) -> None:
    if not self._adaptive_rendering:
      return
    high_rate = self._full_rate_rendering or time.monotonic() < self._high_fps_until
    self._set_target_fps(self._full_target_fps if high_rate else self._idle_target_fps)

  def request_close(self):
    self._window_close_requested = True

  def _needs_render_texture(self) -> bool:
    return bool(
      (self._scale != 1.0 and not PC)
      or BURN_IN_MODE
      or RECORD
      or MICI_FORCE_RENDER_TEXTURE
      or TICI_FORCE_RENDER_TEXTURE
      or WHITE_LUMINANCE_CAP < 1.0
    )

  def init_window(self, title: str, fps: int = _DEFAULT_FPS):
    with self._startup_profile_context():
      def _request_close(sig, frame):
        self.request_close()
      signal.signal(signal.SIGINT, _request_close)
      atexit.register(self.close)

      flags = rl.ConfigFlags.FLAG_MSAA_4X_HINT
      if ENABLE_VSYNC:
        flags |= rl.ConfigFlags.FLAG_VSYNC_HINT
      rl.set_config_flags(flags)

      rl.init_window(self._scaled_width, self._scaled_height, title)
      screen_width = max(rl.get_screen_width(), 1)
      screen_height = max(rl.get_screen_height(), 1)
      self._pixel_scale_x = max(1.0, rl.get_render_width() / screen_width) if PC else 1.0
      self._pixel_scale_y = max(1.0, rl.get_render_height() / screen_height) if PC else 1.0
      self._render_texture_width = max(1, int(round(self._scaled_width * self._pixel_scale_x)))
      self._render_texture_height = max(1, int(round(self._scaled_height * self._pixel_scale_y)))

      needs_render_texture = self._needs_render_texture()
      if PC and self._scale != 1.0:
        rl.set_mouse_scale(1 / self._scale, 1 / self._scale)
      if PC:
        self._mouse.start_desktop_mouse_sampler()
      if needs_render_texture:
        if MICI_FORCE_RENDER_TEXTURE:
          cloudlog.warning("Forcing render texture path for mici UI")
        elif TICI_FORCE_RENDER_TEXTURE:
          cloudlog.warning("Forcing render texture path for tici UI")
        self._render_texture = rl.load_render_texture(self._render_texture_width, self._render_texture_height)
        rl.set_texture_filter(self._render_texture.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)

      if RECORD:
        output_fps = fps * RECORD_SPEED

        if RECORD_HUD_ONLY:
          # Desktop-only lossless RGBA recording. PNG preserves HUD transparency.
          ffmpeg_args = [
            "ffmpeg",
            "-v", "warning",
            "-nostats",
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{self._render_texture_width}x{self._render_texture_height}",
            "-r", str(fps),
            "-i", "pipe:0",
            "-vf", "vflip",
            "-r", str(output_fps),
            "-c:v", "png",
            "-pix_fmt", "rgba",
            "-y",
            "-f", "matroska",
            RECORD_OUTPUT,
          ]
        elif RECORD_CAMERA_ONLY:
          # Desktop-only lossless camera recording.
          ffmpeg_args = [
            "ffmpeg",
            "-v", "warning",
            "-nostats",
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{self._render_texture_width}x{self._render_texture_height}",
            "-r", str(fps),
            "-i", "pipe:0",
            "-vf", "vflip,format=yuv420p",
            "-r", str(output_fps),
            "-c:v", "ffvhuff",
            "-y",
            "-f", "matroska",
            RECORD_OUTPUT,
          ]
        else:
          # Preserve the native recording path for normal RECORD and COMBINED.
          ffmpeg_args = [
            "ffmpeg",
            "-v", "warning",          # Reduce ffmpeg log spam
            "-nostats",               # Suppress encoding progress
            "-f", "rawvideo",         # Input format
            "-pix_fmt", "rgba",       # Input pixel format
            "-s", f"{self._render_texture_width}x{self._render_texture_height}",  # Input resolution
            "-r", str(fps),           # Input frame rate
            "-i", "pipe:0",           # Input from stdin
            "-vf", "vflip,format=yuv420p",  # Flip vertically and convert to yuv420p
            "-r", str(output_fps),    # Output frame rate (for speed multiplier)
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(RECORD_QUALITY)
          ]
          if RECORD_BITRATE:
            # NOTE: custom bitrate overrides crf setting
            ffmpeg_args += ["-b:v", RECORD_BITRATE, "-maxrate", RECORD_BITRATE, "-bufsize", RECORD_BITRATE]
          ffmpeg_args += [
            "-y",                     # Overwrite existing file
            "-f", "mp4",              # Output format
            RECORD_OUTPUT,            # Output file path
          ]
        self._ffmpeg_proc = subprocess.Popen(ffmpeg_args, stdin=subprocess.PIPE)
        self._ffmpeg_queue = queue.Queue(maxsize=60)  # Buffer up to 60 frames
        self._ffmpeg_stop_event = threading.Event()
        self._ffmpeg_thread = threading.Thread(target=self._ffmpeg_writer_thread, daemon=True)
        self._ffmpeg_thread.start()

      rl.set_target_fps(_raylib_target_fps(fps))

      self._full_target_fps = fps
      self._target_fps = fps
      self._set_styles()
      self._load_fonts()
      self._patch_text_functions()
      self._patch_scissor_mode()
      if BURN_IN_MODE and self._burn_in_shader is None:
        self._burn_in_shader = rl.load_shader_from_memory(BURN_IN_VERTEX_SHADER, BURN_IN_FRAGMENT_SHADER)
      if WHITE_LUMINANCE_CAP < 1.0 and self._white_luminance_shader is None:
        self._white_luminance_shader = rl.load_shader_from_memory(BURN_IN_VERTEX_SHADER, WHITE_LUMINANCE_FRAGMENT_SHADER)
        cap_location = rl.get_shader_location(self._white_luminance_shader, "whiteLuminanceCap")
        cap_value = rl.ffi.new("float[]", [WHITE_LUMINANCE_CAP])
        rl.set_shader_value(self._white_luminance_shader, cap_location, cap_value,
                            rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT)

      if not PC:
        self._mouse.start()

  @contextmanager
  def _startup_profile_context(self):
    if "PROFILE_STARTUP" not in os.environ:
      yield
      return

    import cProfile
    import io
    import pstats

    profiler = cProfile.Profile()
    start_time = time.monotonic()
    profiler.enable()

    # do the init
    yield

    profiler.disable()
    elapsed_ms = (time.monotonic() - start_time) * 1e3

    stats_stream = io.StringIO()
    pstats.Stats(profiler, stream=stats_stream).sort_stats("cumtime").print_stats(25)
    print("\n=== Startup profile ===")
    print(stats_stream.getvalue().rstrip())

    green = "\033[92m"
    reset = "\033[0m"
    print(f"{green}UI window ready in {elapsed_ms:.1f} ms{reset}")
    sys.exit(0)

  def _ffmpeg_writer_thread(self):
    """Background thread that writes frames to ffmpeg."""
    while True:
      try:
        data = self._ffmpeg_queue.get(timeout=1.0)
        if data is None:  # Sentinel to stop
          break
        self._ffmpeg_proc.stdin.write(data)
      except queue.Empty:
        if self._ffmpeg_stop_event.is_set():
          break
        continue
      except Exception:
        break

  def push_widget(self, widget: object):
    if widget in self._nav_stack:
      cloudlog.warning("Widget already in stack, cannot push again!")
      return

    # disable previous widget to prevent input processing
    if len(self._nav_stack) > 0:
      prev_widget = self._nav_stack[-1]
      # TODO: change these to touch_valid
      prev_widget.set_enabled(False)

    self._nav_stack.append(widget)
    self.request_high_fps()
    widget.show_event()
    widget.set_enabled(True)

  def pop_widget(self, idx: int | None = None):
    # Pops widget instantly without animation
    if len(self._nav_stack) < 2:
      cloudlog.warning("At least one widget should remain on the stack, ignoring pop!")
      return

    idx_to_pop = len(self._nav_stack) - 1 if idx is None else idx
    if idx_to_pop <= 0 or idx_to_pop >= len(self._nav_stack):
      cloudlog.warning(f"Invalid index {idx_to_pop} to pop, ignoring!")
      return

    # only re-enable previous widget if popping top widget
    if idx_to_pop == len(self._nav_stack) - 1:
      prev_widget = self._nav_stack[idx_to_pop - 1]
      prev_widget.set_enabled(True)

    widget = self._nav_stack.pop(idx_to_pop)
    widget.hide_event()
    self.request_high_fps()

  def pop_widgets_to(self, widget: object, callback: Callable[[], None] | None = None, instant: bool = False):
    # Pops middle widgets instantly without animation then dismisses top, animated out if NavWidget
    if widget not in self._nav_stack:
      cloudlog.warning("Widget not in stack, cannot pop to it!")
      return

    # Nothing to pop, ensure we still run callback
    top_widget = self._nav_stack[-1]
    if top_widget == widget:
      if callback:
        callback()
      return

    # instantly pop widgets in between, then dismiss top widget for animation
    while len(self._nav_stack) > 1 and self._nav_stack[-2] != widget:
      self.pop_widget(len(self._nav_stack) - 2)

    if not instant:
      top_widget.dismiss(callback)
    else:
      self.pop_widget()

  def get_active_widget(self):
    if len(self._nav_stack) > 0:
      return self._nav_stack[-1]
    return None

  def widget_in_stack(self, widget: object) -> bool:
    return widget in self._nav_stack

  def add_nav_stack_tick(self, tick_function: Callable[[], None]):
    if tick_function not in self._nav_stack_ticks:
      self._nav_stack_ticks.append(tick_function)

  def remove_nav_stack_tick(self, tick_function: Callable[[], None]):
    if tick_function in self._nav_stack_ticks:
      self._nav_stack_ticks.remove(tick_function)

  def set_progress_hook(self, hook: Callable[[str], None] | None) -> None:
    self._progress_hook = hook

  def _mark_progress(self, phase: str) -> None:
    if self._progress_hook is not None:
      self._progress_hook(phase)

  def mark_progress(self, phase: str) -> None:
    """Expose lightweight phase markers to complex widgets."""
    self._mark_progress(phase)

  def set_should_render(self, should_render: bool):
    self._should_render = should_render
    if should_render:
      self.request_high_fps()

  def texture(self, asset_path: str, width: int | None = None, height: int | None = None,
              alpha_premultiply=False, keep_aspect_ratio=True, flip_x: bool = False) -> rl.Texture:
    if width is not None:
      width = round(width)
    if height is not None:
      height = round(height)

    cache_key = f"{asset_path}_{width}_{height}_{alpha_premultiply}_{keep_aspect_ratio}_{flip_x}"
    if cache_key in self._textures:
      return self._textures[cache_key]

    with as_file(ASSETS_DIR.joinpath(asset_path)) as fspath:
      image_obj = self._load_image_from_path(fspath.as_posix(), width, height, alpha_premultiply, keep_aspect_ratio, flip_x)
      texture_obj = self._load_texture_from_image(image_obj)

    # Set logical size so widget layout math stays at 1x coordinates.
    if width is not None and height is not None:
      texture_obj.width = width
      texture_obj.height = height

    self._textures[cache_key] = texture_obj
    return texture_obj

  def cached_render_texture(self, cache_key: str, width: int, height: int,
                            render: Callable[[], None]) -> object | None:
    """Return a cached texture, scheduling cache misses between frames.

    Raylib render-texture modes are not nestable. Widgets call this while the
    main framebuffer (often another render texture) is active, so cache misses
    must be populated after the frame has been presented.
    """
    cached = self._cached_render_textures.get(cache_key)
    if cached is not None:
      return cached.texture

    self._pending_render_textures.setdefault(
      cache_key, (max(1, int(width)), max(1, int(height)), render)
    )
    return None

  def _populate_render_texture_cache(self) -> None:
    pending = self._pending_render_textures
    self._pending_render_textures = {}
    for cache_key, (width, height, render) in pending.items():
      if cache_key in self._cached_render_textures:
        continue

      cached = rl.load_render_texture(max(1, int(width)), max(1, int(height)))
      began_texture_mode = False
      began_blend_mode = False
      try:
        rl.begin_texture_mode(cached)
        began_texture_mode = True
        rl.clear_background(rl.Color(0, 0, 0, 0))
        # Preserve straight alpha while RGB is accumulated premultiplied. The
        # resulting texture can then be composited with BLEND_ALPHA_PREMULTIPLY
        # without squaring translucent vector alpha.
        rl.rl_set_blend_factors_separate(
          rl.RL_SRC_ALPHA, rl.RL_ONE_MINUS_SRC_ALPHA,
          rl.RL_ONE, rl.RL_ONE_MINUS_SRC_ALPHA,
          rl.RL_FUNC_ADD, rl.RL_FUNC_ADD,
        )
        rl.begin_blend_mode(rl.BlendMode.BLEND_CUSTOM_SEPARATE)
        began_blend_mode = True
        render()
      except Exception:
        if began_blend_mode:
          rl.end_blend_mode()
        if began_texture_mode:
          rl.end_texture_mode()
        rl.unload_render_texture(cached)
        raise
      else:
        rl.end_blend_mode()
        rl.end_texture_mode()
      rl.set_texture_filter(cached.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      rl.set_texture_wrap(cached.texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)
      self._cached_render_textures[cache_key] = cached

  def _load_image_from_path(self, image_path: str, width: int | None = None, height: int | None = None,
                            alpha_premultiply: bool = False, keep_aspect_ratio: bool = True, flip_x: bool = False) -> rl.Image:
    """Load and resize an image, storing it for later automatic unloading."""
    image = rl.load_image(image_path)

    if alpha_premultiply:
      rl.image_alpha_premultiply(image)

    # Scale up load size for sharper rendering, capped at source resolution.
    if width is not None and height is not None:
      width = min(int(width * self._scale * self._pixel_scale_x), image.width)
      height = min(int(height * self._scale * self._pixel_scale_y), image.height)

    if width is not None and height is not None:
      same_dimensions = image.width == width and image.height == height

      # Resize with aspect ratio preservation if requested
      if not same_dimensions:
        if keep_aspect_ratio:
          orig_width = image.width
          orig_height = image.height

          scale_width = width / orig_width
          scale_height = height / orig_height

          # Calculate new dimensions
          scale = min(scale_width, scale_height)
          new_width = int(orig_width * scale)
          new_height = int(orig_height * scale)

          rl.image_resize(image, new_width, new_height)
        else:
          rl.image_resize(image, width, height)
    else:
      assert keep_aspect_ratio, "Cannot resize without specifying width and height"

    if flip_x:
      rl.image_flip_horizontal(image)

    return image

  def _load_texture_from_image(self, image: rl.Image) -> rl.Texture:
    """Send image to GPU and unload original image."""
    texture = rl.load_texture_from_image(image)
    # Set texture filtering to smooth the result
    rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
    # prevent artifacts from wrapping coordinates
    rl.set_texture_wrap(texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)

    rl.unload_image(image)
    return texture

  def close_ffmpeg(self):
    if self._ffmpeg_thread is not None:
      # Signal thread to stop, send sentinel, then wait for it to drain
      self._ffmpeg_stop_event.set()
      self._ffmpeg_queue.put(None)
      self._ffmpeg_thread.join(timeout=30)

    if self._ffmpeg_proc is not None:
      self._ffmpeg_proc.stdin.flush()
      self._ffmpeg_proc.stdin.close()
      try:
        self._ffmpeg_proc.wait(timeout=30)
      except subprocess.TimeoutExpired:
        self._ffmpeg_proc.terminate()
        self._ffmpeg_proc.wait()

  def close(self):
    if not rl.is_window_ready():
      return

    for texture in self._textures.values():
      rl.unload_texture(texture)
    self._textures = {}

    for render_texture in self._cached_render_textures.values():
      rl.unload_render_texture(render_texture)
    self._cached_render_textures = {}
    self._pending_render_textures = {}

    for font in self._fonts.values():
      rl.unload_font(font)
    self._fonts = {}

    if self._render_texture is not None:
      rl.unload_render_texture(self._render_texture)
      self._render_texture = None

    if self._burn_in_shader:
      rl.unload_shader(self._burn_in_shader)
      self._burn_in_shader = None

    if self._white_luminance_shader:
      rl.unload_shader(self._white_luminance_shader)
      self._white_luminance_shader = None

    if hasattr(rl, "_orig_begin_scissor_mode"):
      rl.begin_scissor_mode = rl._orig_begin_scissor_mode

    self._mouse.stop()

    self.close_ffmpeg()

    rl.close_window()

  @property
  def mouse_events(self) -> list[MouseEvent]:
    return self._mouse_events

  @property
  def last_mouse_event(self) -> MouseEvent:
    return self._last_mouse_event

  def render(self):
    try:
      if self._profile_render_frames > 0:
        import cProfile
        self._render_profiler = cProfile.Profile()
        self._render_profile_start_time = time.monotonic()
        self._render_profiler.enable()

      while not (self._window_close_requested or rl.window_should_close()):
        frame_start = time.monotonic()
        cpu_start = time.thread_time()
        self._mark_progress("gui_app.loop_start")
        self._apply_render_mode()
        if PC:
          # Thread is not used on PC, need to manually add mouse events.
          self._mouse._handle_mouse_event()

        # Store all mouse events for the current frame
        self._mouse_events = self._mouse.get_events()
        if len(self._mouse_events) > 0:
          self._last_mouse_event = self._mouse_events[-1]
          self.request_high_fps()

        # Skip rendering when screen is off
        if not self._should_render:
          self._mark_progress("gui_app.skip_render")
          if PC:
            rl.poll_input_events()
          time.sleep(1 / self._target_fps)
          yield False
          continue

        if self._render_texture:
          self._mark_progress("gui_app.before_begin_texture_mode")
          rl.begin_texture_mode(self._render_texture)
          self._mark_progress("gui_app.after_begin_texture_mode")
          self._mark_progress("gui_app.before_clear_background")
          if RECORD_HUD_ONLY:
            rl.clear_background(rl.Color(0, 0, 0, 0))
          else:
            rl.clear_background(rl.BLACK)
          self._mark_progress("gui_app.after_clear_background")
        else:
          self._mark_progress("gui_app.before_begin_drawing")
          rl.begin_drawing()
          self._mark_progress("gui_app.after_begin_drawing")
          self._mark_progress("gui_app.before_clear_background")
          rl.clear_background(rl.BLACK)
          self._mark_progress("gui_app.after_clear_background")

        render_scale_x = self._scale * (self._pixel_scale_x if self._render_texture else 1.0)
        render_scale_y = self._scale * (self._pixel_scale_y if self._render_texture else 1.0)
        needs_render_scale = render_scale_x != 1.0 or render_scale_y != 1.0
        direct_burn_in_shift = self._burn_in_shift() if self._render_texture is None else (0, 0)
        needs_render_transform = needs_render_scale or direct_burn_in_shift != (0, 0)
        if needs_render_transform:
          rl.rl_push_matrix()
          if needs_render_scale:
            rl.rl_scalef(render_scale_x, render_scale_y, 1.0)
          if direct_burn_in_shift != (0, 0):
            rl.rl_translatef(direct_burn_in_shift[0], direct_burn_in_shift[1], 0.0)

        # Allow a Widget to still run a function regardless of the stack depth
        self._mark_progress("gui_app.before_nav_ticks")
        for tick in self._nav_stack_ticks:
          tick()
        self._mark_progress("gui_app.after_nav_ticks")

        # Only render top widgets
        self._mark_progress("gui_app.before_widget_render")
        viewport = rl.Rectangle(0, 0, self.width, self.height)
        widgets = self._nav_stack[-self._nav_stack_widgets_to_render:]
        if len(widgets) > 1 and widgets[-1].covers_background(viewport):
          widgets = widgets[-1:]
        for widget in widgets:
          widget.render(rl.Rectangle(0, 0, self.width, self.height))
        self._mark_progress("gui_app.after_widget_render")

        self._mark_progress("gui_app.frame_ready")
        draw_end = time.monotonic()
        yield True
        update_end = time.monotonic()

        if needs_render_transform:
          rl.rl_pop_matrix()

        if self._render_texture:
          self._mark_progress("gui_app.end_texture_mode")
          rl.end_texture_mode()
          self._mark_progress("gui_app.before_present_begin_drawing")
          rl.begin_drawing()
          self._mark_progress("gui_app.after_present_begin_drawing")
          self._mark_progress("gui_app.before_present_clear_background")
          rl.clear_background(rl.BLACK)
          self._mark_progress("gui_app.after_present_clear_background")
          src_rect = rl.Rectangle(0, 0, float(self._render_texture_width), -float(self._render_texture_height))
          shift_x, shift_y = self._burn_in_shift()
          dst_rect = rl.Rectangle(shift_x, shift_y, float(self._scaled_width), float(self._scaled_height))
          texture = self._render_texture.texture
          if texture:
            self._mark_progress("gui_app.before_present_draw_texture")
            if BURN_IN_MODE and self._burn_in_shader:
              rl.begin_shader_mode(self._burn_in_shader)
              rl.draw_texture_pro(texture, src_rect, dst_rect, rl.Vector2(0, 0), 0.0, rl.WHITE)
              rl.end_shader_mode()
            elif self._white_luminance_shader:
              rl.begin_shader_mode(self._white_luminance_shader)
              rl.draw_texture_pro(texture, src_rect, dst_rect, rl.Vector2(0, 0), 0.0, rl.WHITE)
              rl.end_shader_mode()
            else:
              rl.draw_texture_pro(texture, src_rect, dst_rect, rl.Vector2(0, 0), 0.0, rl.WHITE)
            self._mark_progress("gui_app.after_present_draw_texture")

        if self._show_fps:
          rl.draw_fps(10, 10)

        if self._show_touches:
          self._draw_touch_points()

        if self._grid_size > 0:
          self._draw_grid()

        self._mark_progress("gui_app.before_end_drawing")
        rl.end_drawing()
        self._mark_progress("gui_app.after_end_drawing")
        present_end = time.monotonic()
        self._populate_render_texture_cache()

        if RECORD:
          image = rl.load_image_from_texture(self._render_texture.texture)
          data_size = image.width * image.height * 4
          data = bytes(rl.ffi.buffer(image.data, data_size))
          self._ffmpeg_queue.put(data)  # Async write via background thread
          # Stop after the requested number of recorded frames rather than wall-clock time.
          if RECORD_DURATION > 0:
            self._recorded_frames += 1
            if self._recorded_frames >= int(RECORD_DURATION * self._full_target_fps):
              self._window_close_requested = True
          rl.unload_image(image)

        self.frame_timing = FrameTiming(
          (time.monotonic() - frame_start) * 1000,
          (time.thread_time() - cpu_start) * 1000,
          (draw_end - frame_start) * 1000,
          (update_end - draw_end) * 1000,
          (present_end - update_end) * 1000,
        )
        self._monitor_fps()
        self._frame += 1
        self._mark_progress("gui_app.loop_idle")

        if self._profile_render_frames > 0 and self._frame >= self._profile_render_frames:
          self._output_render_profile()
    except KeyboardInterrupt:
      pass

  def _burn_in_shift(self, now: float | None = None) -> tuple[float, float]:
    if not BURN_IN_PREVENTION or BURN_IN_SHIFT_PIXELS == 0:
      return 0.0, 0.0

    elapsed = (time.monotonic() if now is None else now) - self._burn_in_start_time
    elapsed = max(0.0, elapsed)
    pattern_count = len(BURN_IN_SHIFT_PATTERN)
    cycle_elapsed = elapsed % (BURN_IN_SHIFT_INTERVAL * pattern_count)
    pattern_index = int(cycle_elapsed // BURN_IN_SHIFT_INTERVAL)
    segment_elapsed = cycle_elapsed - pattern_index * BURN_IN_SHIFT_INTERVAL

    # Blend into the next position at the end of each interval. This keeps the
    # burn-in protection active without teleporting the entire UI by two pixels.
    transition_start = BURN_IN_SHIFT_INTERVAL - BURN_IN_SHIFT_TRANSITION_SECONDS
    transition = min(1.0, max(0.0, (segment_elapsed - transition_start) / BURN_IN_SHIFT_TRANSITION_SECONDS))
    start_x, start_y = BURN_IN_SHIFT_PATTERN[pattern_index]
    end_x, end_y = BURN_IN_SHIFT_PATTERN[(pattern_index + 1) % pattern_count]
    x = start_x + (end_x - start_x) * transition
    y = start_y + (end_y - start_y) * transition
    return x * BURN_IN_SHIFT_PIXELS, y * BURN_IN_SHIFT_PIXELS

  def font(self, font_weight: FontWeight = FontWeight.NORMAL) -> rl.Font:
    return self._fonts[font_weight]

  @property
  def width(self):
    return self._width

  @property
  def height(self):
    return self._height

  def _load_fonts(self):
    for font_weight_file in FontWeight:
      with as_file(FONT_DIR) as fspath:
        fnt_path = fspath / font_weight_file
        font = rl.load_font(fnt_path.as_posix())
        if font_weight_file != FontWeight.UNIFONT:
          rl.gen_texture_mipmaps(font.texture)
          rl.set_texture_filter(font.texture, rl.TextureFilter.TEXTURE_FILTER_TRILINEAR)
        self._fonts[font_weight_file] = font
    rl.gui_set_font(self._fonts[FontWeight.NORMAL])

  def _set_styles(self):
    rl.gui_set_style(rl.GuiControl.DEFAULT, rl.GuiControlProperty.BORDER_WIDTH, 0)
    rl.gui_set_style(rl.GuiControl.DEFAULT, rl.GuiDefaultProperty.TEXT_SIZE, DEFAULT_TEXT_SIZE)
    rl.gui_set_style(rl.GuiControl.DEFAULT, rl.GuiDefaultProperty.BACKGROUND_COLOR, rl.color_to_int(rl.BLACK))
    rl.gui_set_style(rl.GuiControl.DEFAULT, rl.GuiControlProperty.TEXT_COLOR_NORMAL, rl.color_to_int(DEFAULT_TEXT_COLOR))
    rl.gui_set_style(rl.GuiControl.DEFAULT, rl.GuiControlProperty.BASE_COLOR_NORMAL, rl.color_to_int(rl.Color(50, 50, 50, 255)))

  def _patch_text_functions(self):
    # Wrap pyray text APIs to apply a global text size scale.
    if not hasattr(rl, "_orig_draw_text_ex"):
      rl._orig_draw_text_ex = rl.draw_text_ex

    def _draw_text_ex_scaled(font, text, position, font_size, spacing, tint):
      font = font_fallback(font)
      return rl._orig_draw_text_ex(font, text, position, font_size * FONT_SCALE, spacing, tint)

    rl.draw_text_ex = _draw_text_ex_scaled

  def _patch_scissor_mode(self):
    if not hasattr(rl, "_orig_begin_scissor_mode"):
      rl._orig_begin_scissor_mode = rl.begin_scissor_mode

    def _begin_scissor_mode(x, y, width, height):
      scale_x = self._scale * (self._pixel_scale_x if self._render_texture else 1.0)
      scale_y = self._scale * (self._pixel_scale_y if self._render_texture else 1.0)
      shift_x, shift_y = self._burn_in_shift() if self._render_texture is None else (0.0, 0.0)
      return rl._orig_begin_scissor_mode(
        int(round((x + shift_x) * scale_x)),
        int(round((y + shift_y) * scale_y)),
        int(math.ceil(width * scale_x)),
        int(math.ceil(height * scale_y)),
      )

    rl.begin_scissor_mode = _begin_scissor_mode

  def _set_log_callback(self):
    ffi_libc = cffi.FFI()
    ffi_libc.cdef("""
      int vasprintf(char **strp, const char *fmt, void *ap);
      void free(void *ptr);
    """)
    libc = ffi_libc.dlopen(None)

    @rl.ffi.callback("void(int, char *, void *)")
    def trace_log_callback(log_level, text, args):
      try:
        text_addr = int(rl.ffi.cast("uintptr_t", text))
        args_addr = int(rl.ffi.cast("uintptr_t", args))
        text_libc = ffi_libc.cast("char *", text_addr)
        args_libc = ffi_libc.cast("void *", args_addr)

        out = ffi_libc.new("char **")
        if libc.vasprintf(out, text_libc, args_libc) >= 0 and out[0] != ffi_libc.NULL:
          text_str = ffi_libc.string(out[0]).decode("utf-8", "replace")
          libc.free(out[0])
        else:
          text_str = rl.ffi.string(text).decode("utf-8", "replace")
      except Exception as e:
        text_str = f"[Log decode error: {e}]"

      if log_level == rl.TraceLogLevel.LOG_ERROR:
        cloudlog.error(f"raylib: {text_str}")
      elif log_level == rl.TraceLogLevel.LOG_WARNING:
        cloudlog.warning(f"raylib: {text_str}")
      elif log_level == rl.TraceLogLevel.LOG_INFO:
        cloudlog.info(f"raylib: {text_str}")
      elif log_level == rl.TraceLogLevel.LOG_DEBUG:
        cloudlog.debug(f"raylib: {text_str}")
      else:
        cloudlog.error(f"raylib: Unknown level {log_level}: {text_str}")

    # ensure we get all the logs forwarded to us
    rl.set_trace_log_level(rl.TraceLogLevel.LOG_DEBUG)

    # Store callback reference
    self._trace_log_callback = trace_log_callback
    rl.set_trace_log_callback(self._trace_log_callback)

  def _monitor_fps(self):
    fps = rl.get_fps()

    # Log FPS drop below threshold at regular intervals
    if fps < self._target_fps * FPS_DROP_THRESHOLD:
      current_time = time.monotonic()
      if current_time - self._last_fps_log_time >= FPS_LOG_INTERVAL:
        timing = self.frame_timing
        cloudlog.warning(f"FPS dropped below {self._target_fps}: {fps} " +
                         f"(frame={timing.frame_ms:.1f}ms cpu={timing.cpu_ms:.1f}ms " +
                         f"draw={timing.draw_ms:.1f}ms update={timing.update_ms:.1f}ms present={timing.present_ms:.1f}ms)")
        self._last_fps_log_time = current_time

    # Strict mode: terminate UI if FPS drops too much
    if STRICT_MODE and fps < self._target_fps * FPS_CRITICAL_THRESHOLD:
      cloudlog.error(f"FPS dropped critically below {fps}. Shutting down UI.")
      self.close_ffmpeg()
      os._exit(1)

  def _draw_touch_points(self):
    current_time = time.monotonic()

    for mouse_event in self._mouse_events:
      if mouse_event.left_pressed:
        self._mouse_history.clear()
      self._mouse_history.append(MousePosWithTime(mouse_event.pos.x * self._scale, mouse_event.pos.y * self._scale, current_time))

    # Remove old touch points that exceed the timeout
    while self._mouse_history and (current_time - self._mouse_history[0].t) > TOUCH_HISTORY_TIMEOUT:
      self._mouse_history.popleft()

    if self._mouse_history:
      mouse_pos = self._mouse_history[-1]
      rl.draw_circle(int(mouse_pos.x), int(mouse_pos.y), 15, rl.RED)
      for idx, mouse_pos in enumerate(self._mouse_history):
        perc = idx / len(self._mouse_history)
        color = rl.Color(min(int(255 * (1.5 - perc)), 255), int(min(255 * (perc + 0.5), 255)), 50, 255)
        rl.draw_circle(int(mouse_pos.x), int(mouse_pos.y), 5, color)

  def _draw_grid(self):
    grid_color = rl.Color(60, 60, 60, 255)
    # Draw vertical lines
    x = 0
    while x <= self._scaled_width:
      rl.draw_line(x, 0, x, self._scaled_height, grid_color)
      x += self._grid_size
    # Draw horizontal lines
    y = 0
    while y <= self._scaled_height:
      rl.draw_line(0, y, self._scaled_width, y, grid_color)
      y += self._grid_size

  def _output_render_profile(self):
    import io
    import pstats

    self._render_profiler.disable()
    elapsed_ms = (time.monotonic() - self._render_profile_start_time) * 1e3
    avg_frame_time = elapsed_ms / self._frame if self._frame > 0 else 0

    stats_stream = io.StringIO()
    pstats.Stats(self._render_profiler, stream=stats_stream).sort_stats("cumtime").print_stats(PROFILE_STATS)
    print("\n=== Render loop profile ===")
    print(stats_stream.getvalue().rstrip())

    green = "\033[92m"
    reset = "\033[0m"
    print(f"\n{green}Rendered {self._frame} frames in {elapsed_ms:.1f} ms{reset}")
    print(f"{green}Average frame time: {avg_frame_time:.2f} ms ({1000/avg_frame_time:.1f} FPS){reset}")
    sys.exit(0)

  def _calculate_auto_scale(self) -> float:
    if os.getenv("SP_HEADLESS_TEST") == "1":
      return 1.0

     # Create temporary window to query monitor info
    rl.init_window(1, 1, "")
    w, h = rl.get_monitor_width(0), rl.get_monitor_height(0)
    rl.close_window()

    if w == 0 or h == 0 or (w >= self._width and h >= self._height):
      return 1.0

    # Apply 0.95 factor for window decorations/taskbar margin
    return max(0.3, min(w / self._width, h / self._height) * 0.95)

  @staticmethod
  def _default_width() -> int:
    return 2160 if GuiApplication.big_ui() else 536

  @staticmethod
  def _default_height() -> int:
    return 1080 if GuiApplication.big_ui() else 240

  @staticmethod
  def big_ui() -> bool:
    return HARDWARE.get_device_type() in ('tici', 'tizi') or BIG_UI


gui_app = GuiApplication()
