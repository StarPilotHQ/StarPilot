from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.system.ui.lib.application import gui_app
from openpilot.common.filter_simple import BounceFilter
from openpilot.system.ui.widgets.nav_widget import NavBar, NavWidget, NAV_BAR_MARGIN, NAV_BAR_HEIGHT


class NavScreen(NavWidget):
  def _render(self, _):
    pass


@pytest.fixture
def viewport():
  return rl.Rectangle(0, 0, gui_app.width, gui_app.height)


@pytest.fixture
def screen(monkeypatch, viewport):
  monkeypatch.setattr(rl, "draw_rectangle_rec", lambda *_: None)
  monkeypatch.setattr(rl, "get_time", lambda: 10.0)
  monkeypatch.setattr(rl, "get_frame_time", lambda: 1 / 60)
  monkeypatch.setattr(gui_app, "_target_fps", 60)
  monkeypatch.setattr(gui_app, "_show_touches", False)
  monkeypatch.setattr(gui_app, "_mouse_events", [])
  screen = NavScreen()
  screen.set_rect(viewport)
  monkeypatch.setattr(screen._nav_bar, "render", lambda: None)
  return screen


@pytest.mark.parametrize("fps", [20, 30, 60])
def test_show_animation_duration_tracks_elapsed_time(monkeypatch, screen, viewport, fps):
  monkeypatch.setattr(rl, "get_frame_time", lambda: 1 / fps)
  shown = []
  screen.set_shown_callback(lambda: shown.append(True))
  screen.show_event()
  for _frames in range(1, fps * 3):
    screen.render(viewport)
    if shown:
      break

  assert shown == [True]
  assert screen._y_pos_filter.x == screen._y_pos_filter.velocity.x == 0
  assert _frames / fps == pytest.approx(35 / 60, abs=1 / fps)


@pytest.mark.parametrize("fps", [20, 30, 60])
def test_dismiss_animation_duration_tracks_elapsed_time(monkeypatch, screen, viewport, fps):
  monkeypatch.setattr(rl, "get_frame_time", lambda: 1 / fps)
  popped, dismissed, backed = [], [], []
  monkeypatch.setattr(gui_app, "pop_widget", lambda: popped.append(True))
  screen.set_back_callback(lambda: backed.append(True))
  screen.dismiss(lambda: dismissed.append(True))
  for _frames in range(1, fps * 3):
    screen.render(viewport)
    if popped:
      break

  assert _frames / fps == pytest.approx(13 / 60, abs=1 / fps)
  assert popped == dismissed == [True]
  assert not backed


def test_show_animation_retains_original_sixty_fps_motion(screen):
  reference = BounceFilter(gui_app.height, 0.1, 1 / 60, bounce=1)
  screen.show_event()
  for _ in range(20):
    reference.update(0.0)
    screen._update_state()
    assert screen._y_pos_filter.x == pytest.approx(reference.x)
    assert screen._y_pos_filter.velocity.x == pytest.approx(reference.velocity.x)


@pytest.mark.parametrize("fps", [20, 30, 60])
def test_navigation_bar_fade_tracks_elapsed_time(monkeypatch, screen, fps):
  monkeypatch.setattr(rl, "get_frame_time", lambda: 1 / fps)
  monkeypatch.setattr(rl, "draw_rectangle_rounded", lambda *_: None)
  monkeypatch.setattr(rl, "draw_rectangle_rounded_lines_ex", lambda *_: None)
  bar = NavBar()
  bar.set_alpha(0.0)
  for _ in range(fps // 2):
    bar._render(bar.rect)
  assert bar._alpha_filter.x == pytest.approx((1 - bar._alpha_filter.alpha) ** 30)


@pytest.mark.parametrize("fps", [20, 30, 60])
def test_navigation_bar_slide_tracks_elapsed_time(monkeypatch, screen, viewport, fps):
  monkeypatch.setattr(rl, "get_frame_time", lambda: 1 / fps)
  screen._nav_bar_y_filter.x = -NAV_BAR_MARGIN - NAV_BAR_HEIGHT
  for _ in range(fps // 2):
    screen.render(viewport)
  remaining = (1 - screen._nav_bar_y_filter.alpha) ** 30
  assert screen._nav_bar_y_filter.x == pytest.approx(NAV_BAR_MARGIN - (2 * NAV_BAR_MARGIN + NAV_BAR_HEIGHT) * remaining)


def test_long_frame_uses_bounded_spring_steps(monkeypatch, screen):
  screen.show_event()
  monkeypatch.setattr(rl, "get_frame_time", lambda: 0.1)
  screen._update_state()
  expected = screen._y_pos_filter.x, screen._y_pos_filter.velocity.x

  screen.show_event()
  monkeypatch.setattr(rl, "get_frame_time", lambda: 5.0)
  screen._update_state()
  assert (screen._y_pos_filter.x, screen._y_pos_filter.velocity.x) == pytest.approx(expected)


@pytest.mark.parametrize("elapsed", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_frame_duration_uses_default_step(monkeypatch, screen, elapsed):
  screen.show_event()
  screen._update_state()
  expected = screen._y_pos_filter.x, screen._y_pos_filter.velocity.x

  screen.show_event()
  monkeypatch.setattr(rl, "get_frame_time", lambda: elapsed)
  screen._update_state()
  assert (screen._y_pos_filter.x, screen._y_pos_filter.velocity.x) == pytest.approx(expected)


@pytest.mark.parametrize("gesture_end", ["release", "horizontal_cancel"])
def test_drag_reveal_does_not_rehide_during_initial_delay(monkeypatch, screen, viewport, gesture_end):
  from openpilot.system.ui.lib.application import MousePos, MouseEvent
  now = [10.0]
  monkeypatch.setattr(rl, "get_time", lambda: now[0])
  screen.show_event()
  start = MousePos(100, 20)
  screen._handle_mouse_event(MouseEvent(start, 0, True, False, True, now[0]))
  screen.render(viewport)
  assert screen._nav_bar_y_filter.x > 0
  now[0] += 0.05
  if gesture_end == "release":
    screen._handle_mouse_event(MouseEvent(start, 0, False, True, False, now[0]))
  else:
    screen._handle_mouse_event(MouseEvent(MousePos(180, 20), 0, False, False, True, now[0]))
  assert screen._drag_start_pos is None
  screen.render(viewport)
  assert screen._nav_bar_y_filter.x >= NAV_BAR_MARGIN
  screen.show_event()
  screen.render(viewport)
  assert screen._nav_bar_y_filter.x == -NAV_BAR_MARGIN - NAV_BAR_HEIGHT
