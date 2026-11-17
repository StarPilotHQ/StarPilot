"""Native game and version gesture using the supported parked lifecycle owner."""

from collections.abc import Callable
import math
import time

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.hardware import HARDWARE
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.starpilot.ui.spacepilot.guard import ParkGate, Witness, read_offroad_witness, read_witness, sample_clocks
from openpilot.starpilot.ui.spacepilot.session import GameSession, Stage
from openpilot.starpilot.ui.spacepilot.ignition import IgnitionReturn
from openpilot.starpilot.drive_state.owner import DriveStateOwner
from openpilot.starpilot.storage import starpilot_storage_root
from openpilot.starpilot.ui.spacepilot.render import EXIT, SpaceRenderer, canvas_rect
from openpilot.starpilot.favorites.actions import INCREASE_SPEED, DECREASE_SPEED
from openpilot.starpilot.ui.spacepilot.state import FiveTaps, Flight
from openpilot.system.ui.lib.application import MouseEvent, MousePos, gui_app
from openpilot.system.ui.widgets import Widget


# This observer outlives page exit, but never a UI process restart. It owns only
# the exact game request and no game/socket/display resources.
_ignition_return: IgnitionReturn | None = None
# Covered widgets remain open until hide_event finishes; top-widget identity is
# insufficient to prove cleanup before a mode request.
_open_games: set['SpacePilotPage'] = set()


def poll_game_mode() -> None:
  """Called before rendering, including while the ordinary display is asleep."""
  global _ignition_return
  pending = _ignition_return
  if pending is None:
    return
  try:
    if pending.observe():
      for page in tuple(_open_games):
        page._exit(unsafe=True)
      pending.release(lambda: not _open_games)
  except Exception:
    cloudlog.exception('SpacePilot ignition return ended')
    pending.finished = True
  if pending.finished:
    _ignition_return = None


def live_witness() -> Witness | None:
  try:
    after, boot = sample_clocks()
    native_device = HARDWARE.get_device_type() in ('mici', 'tici', 'tizi')
    if ui_state.started:
      return read_witness(ui_state, after, boot, c4=native_device)
    return read_offroad_witness(ui_state, after, boot, native_device=native_device, preparing=True)
  except (AttributeError, OSError, ValueError):
    return None


class SpacePilotPage(Widget):
  def __init__(self, gate: ParkGate, on_unsafe: Callable[[], None]):
    super().__init__()
    self.gate = ParkGate()
    self.gate.since, self.gate.previous, self.gate.ready = gate.since, gate.previous, gate.ready
    self.session = GameSession(ui_state, DriveStateOwner(ui_state.params, starpilot_storage_root() / 'drive-state'),
                               native_device=HARDWARE.get_device_type() in ('mici', 'tici', 'tizi'))
    self.on_unsafe = on_unsafe
    self.flight = Flight(time.monotonic_ns())
    self.renderer = SpaceRenderer()
    self.controller_after_ns = time.monotonic_ns()
    self.controller_last_ns = 0
    self._return_registered = False
    self.last_tick = 0.
    self.closed = True
    self.touch_held = self.exit_pressed = False

  def show_event(self) -> None:
    super().show_event()
    self.closed = False
    _open_games.add(self)
    witness = live_witness()
    self.gate.update(witness)
    # A fresh Park sample can start preparation even if opening the page took
    # long enough to reset the UI dwell. GameSession earns its own full dwell
    # before requesting Offroad and again before accepting game input.
    if witness is None:
      self.session.close('Entry evidence changed')
    self.last_tick = time.monotonic()
    self.touch_held = self.exit_pressed = False
    gui_app.add_nav_stack_tick(self._tick)

  def hide_event(self) -> None:
    self.closed = True
    gui_app.remove_nav_stack_tick(self._tick)
    self.session.close()
    self.flight.close()
    self.gate.reset()
    self.touch_held = self.exit_pressed = False
    super().hide_event()
    _open_games.discard(self)

  def _safe(self) -> bool:
    return bool(not self.closed and gui_app.get_active_widget() is self and self.session.playable())

  def _tick(self) -> None:
    global _ignition_return
    if self.closed:
      return
    if gui_app.get_active_widget() is not self:
      self._exit(unsafe=True)
      return
    fire = self.session.step()
    if not self._return_registered and self.session.ignition_return is not None:
      _ignition_return = self.session.ignition_return
      self._return_registered = True
    if self.session.stage == Stage.CLOSED:
      cloudlog.info('SpacePilot ended: %s', self.session.reason)
      self._exit(unsafe=True)
      return
    # Renew only from this active, successfully checked page. No global timeout
    # override survives exit, a failed render, stopped ticks or UI restart.
    if not ui_state.started:
      device._reset_interactive_timeout()
    now = time.monotonic()
    if fire:
      self.flight.act(now)
    if self.session.stage == Stage.PLAYING:
      self.flight.update(now - self.last_tick)
    self.last_tick = now
    if self.session.stage != Stage.PLAYING:
      self.controller_after_ns = time.monotonic_ns()

  def _exit(self, *, unsafe: bool) -> None:
    if self.closed:
      return
    self.hide_event()
    gui_app.mouse_events.clear()
    if unsafe:
      self.on_unsafe()
    elif gui_app.get_active_widget() is self:
      gui_app.pop_widget()

  def capture_controller(self, key: str | None, timestamp_ns: int) -> None:
    # The shared input owner validates identity, press/release, duplicate and age.
    # Consume every action while this page owns the batch, including during exit.
    if (key not in (INCREASE_SPEED, DECREASE_SPEED) or timestamp_ns <= self.controller_after_ns or
        timestamp_ns - self.controller_last_ns < 120_000_000):
      return
    if not self._safe():
      if self.session.stage == Stage.PLAYING:
        self._exit(unsafe=True)
      return
    self.controller_last_ns = timestamp_ns
    self.flight.act(time.monotonic())

  def _in_exit(self, pos: MousePos) -> bool:
    x, y, w, h = EXIT
    rect = canvas_rect(self.rect)
    return bool(rl.check_collision_point_rec(pos, rl.Rectangle(rect.x + x * rect.width / 1072,
                rect.y + y * rect.height / 480, w * rect.width / 1072, h * rect.height / 480)))

  def _handle_mouse_press(self, pos: MousePos) -> None:
    if self.touch_held:
      return
    self.touch_held = True
    self.exit_pressed = self._in_exit(pos)
    if self.session.stage != Stage.PLAYING:
      return
    if not self._safe():
      self._exit(unsafe=True)
      return
    if not self.exit_pressed:
      self.flight.act(time.monotonic())

  def _handle_mouse_event(self, mouse_event: MouseEvent) -> None:
    event = mouse_event
    if event.left_released:
      self.touch_held = False
      if self.exit_pressed and self._in_exit(event.pos):
        self._exit(unsafe=False)
      self.exit_pressed = False

  def _render(self, rect: rl.Rectangle) -> None:
    if self.closed or gui_app.get_active_widget() is not self:
      self._exit(unsafe=True)
      return
    try:
      if self.session.stage == Stage.PLAYING:
        if not self._safe():
          self._exit(unsafe=True)
          return
        self.renderer.render(self.flight, rect, wheel_ready=bool(self.session.wheel.armed), wheel_available=self.session.vehicle is not None)
      else:
        self.renderer.preparing(rect, stopping=self.session.stage == Stage.STOPPING)
    except Exception:
      cloudlog.exception('SpacePilot render failed')
      self._exit(unsafe=True)


class VersionButton(BigButton):
  """Hidden gesture lives on the version number, using native touch cancellation."""
  def __init__(self, on_unsafe: Callable[[], None]):
    super().__init__('version', ui_state.params.get('Version') or 'N/A')
    self.on_unsafe = on_unsafe
    self.taps = FiveTaps()
    self.gate = ParkGate()
    self.press: MouseEvent | None = None
    self.completed: float | None = None

  def show_event(self) -> None:
    self.taps.reset()
    self.gate.reset()
    self.press = None
    self.completed = None
    self.set_value(ui_state.params.get('Version') or 'N/A')
    super().show_event()

  def hide_event(self) -> None:
    self.taps.reset()
    self.gate.reset()
    self.press = None
    self.completed = None
    super().hide_event()

  def _update_state(self) -> None:
    super()._update_state()
    witness = live_witness()
    self.gate.update(witness)
    if not self.enabled or witness is None:
      self.taps.reset()

  def _process_mouse_events(self) -> None:
    if not self._touch_valid():
      self.taps.reset()
      self.press = None
      self.completed = None
    for event in gui_app.mouse_events:
      if event.slot == 0 and event.left_pressed and not rl.check_collision_point_rec(event.pos, self._sub_label.rect):
        self.taps.reset()
    super()._process_mouse_events()

  def _handle_mouse_event(self, mouse_event: MouseEvent) -> None:
    event = mouse_event
    if event.left_pressed:
      self.press = event if rl.check_collision_point_rec(event.pos, self._sub_label.rect) else None
      self.completed = None
    elif self.press is not None:
      if (not 0 <= event.t - self.press.t <= .45 or
          math.hypot(event.pos.x - self.press.pos.x, event.pos.y - self.press.pos.y) > 12 or not self._touch_valid()):
        self.press = None
        self.taps.reset()
      elif event.left_released:
        self.completed = event.t if rl.check_collision_point_rec(event.pos, self._sub_label.rect) else None
        self.press = None

  def _handle_mouse_release(self, pos: MousePos) -> None:
    witness = live_witness()
    self.gate.update(witness)
    completed, self.completed = self.completed, None
    if completed is None or witness is None:
      self.taps.reset()
      return
    if self.taps.tap(completed):
      gui_app.mouse_events.clear()
      gui_app.push_widget(SpacePilotPage(self.gate, self.on_unsafe))
