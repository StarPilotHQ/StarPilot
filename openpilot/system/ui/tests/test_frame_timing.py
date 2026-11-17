from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from openpilot.system.ui.lib import application


@pytest.fixture
def frame(monkeypatch):
  clock = SimpleNamespace(wall=100.0, cpu=20.0)

  def advance(wall, cpu):
    clock.wall += wall
    clock.cpu += cpu

  monkeypatch.setattr(application.time, "monotonic", lambda: clock.wall)
  thread_clock = Mock(side_effect=lambda: clock.cpu)
  monkeypatch.setattr(application.time, "thread_time", thread_clock)
  monkeypatch.setattr(application, "PC", False)
  monkeypatch.setattr(application, "RECORD", False)
  monkeypatch.setattr(application, "STRICT_MODE", False)
  app = application.GuiApplication()
  app._mouse = Mock()
  app._mouse.get_events.return_value = []
  app._scale = 1
  app._profile_render_frames = 0
  app._target_fps = 60
  app._last_fps_log_time = 0
  app._nav_stack_ticks = [Mock()]
  widget = Mock()
  widget.render.side_effect = lambda _: advance(0.010, 0.003)
  app._nav_stack = [widget]
  app._show_fps = app._show_touches = False
  app._grid_size = 0
  monkeypatch.setattr(application.rl, "window_should_close", Mock(side_effect=[False, True]))
  monkeypatch.setattr(application.rl, "begin_drawing", lambda: advance(0.004, 0.002))
  monkeypatch.setattr(application.rl, "clear_background", lambda _: None)
  monkeypatch.setattr(application.rl, "end_drawing", lambda: advance(0.100, 0.001))
  monkeypatch.setattr(application.rl, "get_frame_time", lambda: 1 / 60)
  monkeypatch.setattr(application.rl, "get_fps", lambda: 10)
  warning = Mock()
  monkeypatch.setattr(application.cloudlog, "warning", warning)
  return app, advance, thread_clock, warning


def test_tuple_lifecycle_separates_draw_update_and_presentation(frame):
  app, advance, _, warning = frame
  app._frame_timing_enabled = True
  loop = app.render()
  should_render, frame_time, cpu_time = next(loop)
  assert should_render and frame_time == 1 / 60
  assert cpu_time == pytest.approx(0.014)
  assert app.frame_timing is None  # Complete only after the caller resumes.
  advance(0.020, 0.006)
  with pytest.raises(StopIteration):
    next(loop)
  assert tuple(app.frame_timing) == pytest.approx((14, 5, 20, 6, 100, 1))
  app._nav_stack_ticks[0].assert_called_once_with()
  app._nav_stack[0].render.assert_called_once()
  assert app.frame == 1
  assert "present wall/cpu 100.0/1.0ms" in warning.call_args.args[0]


def test_disabled_timing_keeps_tuple_and_original_warning_without_cpu_clock(frame):
  app, advance, thread_clock, warning = frame
  app._frame_timing_enabled = False
  assert app.measure_frame_phase("camera", lambda: "rendered") == "rendered"
  loop = app.render()
  assert next(loop) == pytest.approx((True, 1 / 60, 0.014))
  advance(0.020, 0.006)
  with pytest.raises(StopIteration):
    next(loop)
  thread_clock.assert_not_called()
  assert app.frame_timing is None
  warning.assert_called_once_with("FPS dropped below 60: 10")


def test_nested_phases_record_only_this_frame_and_keep_fixed_names(frame, monkeypatch):
  app, advance, _, warning = frame
  app._frame_timing_enabled = True
  monkeypatch.setattr(application.rl, "window_should_close", Mock(side_effect=[False, False, True]))

  def first_draw(_):
    app.measure_frame_phase("camera", lambda: advance(0.030, 0.002))
    app.measure_frame_phase("camera", lambda: advance(0.004, 0.001))
    app.measure_frame_phase("model", lambda: advance(0.006, 0.004))
  app._nav_stack[0].render.side_effect = first_draw
  loop = app.render()
  assert next(loop)[0]
  app.measure_frame_phase("ui_state", lambda: app.measure_frame_phase("transition", lambda: advance(0.020, 0.001)))
  app.measure_frame_phase("controllers", lambda: advance(0.002, 0.001))
  assert next(loop)[0]
  assert set(app._frame_phases) == {"camera", "model"}  # Previous update phases were reset.
  with pytest.raises(StopIteration):
    next(loop)
  logged = warning.call_args.args[0]
  assert "camera wall/cpu 34.0/3.0ms" in logged
  assert "model wall/cpu 6.0/4.0ms" in logged
  assert "ui_state wall/cpu 20.0/1.0ms" in logged
  assert "transition wall/cpu 20.0/1.0ms" in logged
  assert "controllers wall/cpu 2.0/1.0ms" in logged
  assert len(app._frame_phases) <= len(application.FRAME_PHASES)
  with pytest.raises(ValueError):
    app.measure_frame_phase("arbitrary", lambda: None)


def test_screen_off_clears_stale_timing_without_render_or_warning(frame, monkeypatch):
  app, _, _, warning = frame
  app._frame_timing_enabled = True
  app.frame_timing = application.FrameTiming(1, 2, 3, 4, 5, 6)
  app._should_render = False
  monkeypatch.setattr(application.time, "sleep", lambda _: None)
  loop = app.render()
  assert next(loop) == (False, 0.0, 0.0)
  assert app.frame_timing is None
  with pytest.raises(StopIteration):
    next(loop)
  app._nav_stack[0].render.assert_not_called()
  app._nav_stack_ticks[0].assert_not_called()
  warning.assert_not_called()


def test_timing_warning_retains_existing_rate_limit(frame):
  app, _, _, warning = frame
  app.frame_timing = application.FrameTiming(1, 2, 3, 4, 5, 6)
  app._monitor_fps()
  app._monitor_fps()
  warning.assert_called_once()


def test_fps_debug_enables_phase_diagnostics_without_restarting(frame, monkeypatch):
  app, _, _, _ = frame
  monkeypatch.setattr(application, "UI_FRAME_TIMING", False)
  app.set_show_fps(True)
  assert app._show_fps and app._frame_timing_enabled
  app.set_show_fps(False)
  assert not app._show_fps and not app._frame_timing_enabled


@pytest.mark.parametrize("awake", [True, False])
def test_before_frame_runs_outside_drawing_even_asleep(frame, monkeypatch, awake):
  app, _, _, _ = frame
  app._should_render = awake
  events = []
  monkeypatch.setattr(application.time, "sleep", lambda _: None)
  monkeypatch.setattr(application.rl, "begin_drawing", lambda: events.append("draw"))
  loop = app.render(before_frame=lambda: events.append("callback"))
  assert next(loop)[0] is awake
  assert events == (["callback", "draw"] if awake else ["callback"])
  with pytest.raises(StopIteration):
    next(loop)


@pytest.mark.parametrize("awake", [True, False])
def test_ui_poll_precedes_every_frame_including_wake(frame, monkeypatch, awake):
  from openpilot.selfdrive.ui import ui
  app, _, _, _ = frame
  app._should_render = awake
  events = []
  monkeypatch.setattr(application.time, "sleep", lambda _: None)
  monkeypatch.setattr(ui, "gui_app", app)
  monkeypatch.setattr(ui, "ui_state", SimpleNamespace(update=lambda: events.append("messages")))
  monkeypatch.setattr(ui, "device", SimpleNamespace(awake=awake))
  controllers = SimpleNamespace(poll=lambda: events.append("controls"))
  preview = SimpleNamespace(poll=lambda: events.append("preview"))
  star = SimpleNamespace(_poll_projection_bookmark=lambda now: events.append("bookmark"),
                         _poll_projection_slc=lambda now: events.append("slc"),
                         _poll_wheel=lambda now: events.append("wheel"))
  app._nav_stack_ticks = [lambda: events.append("transition")]
  app._nav_stack[0].render.side_effect = lambda _: events.append("draw")
  loop = app.render(before_frame=lambda: ui.update_frame(preview, controllers, star))
  assert next(loop)[0] is awake
  assert events == ["messages", "bookmark", "slc"] + ([] if awake else ["wheel"]) + ["controls", "preview"] + (
    ["transition", "draw"] if awake else [])
  with pytest.raises(StopIteration):
    next(loop)


@pytest.mark.parametrize("awake", [True, False])
def test_projection_bookmark_is_saved_once_without_rendering_starpilot(frame, monkeypatch, awake):
  import ast
  import tempfile
  import time
  from pathlib import Path
  from openpilot.selfdrive.ui import ui
  from openpilot.starpilot.system.starport.projection_bookmark import BookmarkReceiver, BookmarkSender

  app, _, _, _ = frame
  app._should_render = awake
  monkeypatch.setattr(application.time, "sleep", lambda _: None)
  monkeypatch.setattr(application.rl, "window_should_close", Mock(side_effect=[False, False, True]))
  monkeypatch.setattr(ui, "gui_app", app)

  sm = SimpleNamespace(logMonoTime={'carState': 1})
  def update():
    sm.logMonoTime['carState'] = time.monotonic_ns()
  state = SimpleNamespace(started=True, started_frame=1, sm=sm, update=update)
  monkeypatch.setattr(ui, "ui_state", state)
  # Load the production dispatch without importing unrelated device-only UI libraries.
  source = Path(__file__).parents[3] / 'starpilot/ui/runtime_app.py'
  method = next(node for node in ast.walk(ast.parse(source.read_text()))
                if isinstance(node, ast.FunctionDef) and node.name == '_poll_projection_bookmark')
  namespace: dict[str, Any] = {'ui_state': state, 'display_message': Mock(return_value=object()), 'cast': cast}
  exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
  # Short Unix socket path for macOS. The real transport and native dispatch run
  # while the top widget is unrelated to StarPilot, or no widget is drawn at all.
  with tempfile.TemporaryDirectory(prefix='aa-bm-', dir='/tmp') as directory:
    path = Path(directory) / 'bookmark.sock'
    receiver, sender = BookmarkReceiver(path), BookmarkSender(path)
    publish = Mock()
    star = SimpleNamespace(_projection_bookmarks=receiver, _publish_bookmark=publish, _poll_projection_slc=Mock(), _poll_wheel=Mock())
    star._poll_projection_bookmark = lambda now: namespace['_poll_projection_bookmark'](star, now)
    loop = app.render(before_frame=lambda: ui.update_frame(star=star))
    try:
      assert sender.send(time.monotonic_ns())
      assert next(loop)[0] is awake
      publish.assert_called_once_with()
      assert app._nav_stack[0].render.call_count == int(awake)
      app._should_render = True
      assert next(loop)[0]
      publish.assert_called_once_with()  # Waking must not replay the request.
      with pytest.raises(StopIteration):
        next(loop)
    finally:
      loop.close()
      sender.close()
      receiver.close()


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("during_update", [False, True])
def test_timing_toggle_uses_one_decision_for_entire_frame(frame, monkeypatch, initial, during_update):
  app, _, thread_clock, _ = frame
  monkeypatch.setattr(application, "UI_FRAME_TIMING", False)
  monkeypatch.setattr(application.rl, "draw_fps", lambda *_: None)
  app.set_show_fps(initial)
  before = None if during_update else lambda: app.set_show_fps(not initial)
  loop = app.render(before_frame=before)
  next(loop)
  if during_update:
    app.set_show_fps(not initial)
  app.measure_frame_phase("camera", lambda: None)
  assert bool(app._frame_phases) is initial
  with pytest.raises(StopIteration):
    next(loop)
  assert (app.frame_timing is not None) is initial
  assert bool(thread_clock.call_count) is initial
  assert app._collect_frame_timing is None


def test_automatic_diagnostics_are_bounded_and_survive_fps_recovery(frame, monkeypatch):
  app, advance, _, warning = frame
  app._monitor_fps()  # Existing warning starts capture; preferences stay unchanged.
  assert not app._show_fps and not app._frame_timing_enabled
  assert app._auto_timing_end == 106
  app.frame_timing = application.FrameTiming(10, 2, 3, 1, 20, 1)
  app._frame_phases = {"camera": (5, 1)}
  for _ in range(application.AUTO_TIMING_MAX_SAMPLES + 10):
    app._monitor_fps()
  assert len(app._auto_timing_samples) == application.AUTO_TIMING_MAX_SAMPLES
  monkeypatch.setattr(application.rl, "get_fps", lambda: 60)
  advance(6.1, 0)
  app._monitor_fps()
  assert warning.call_count == 2
  summary = warning.call_args.args[0]
  assert "360 slow samples" in summary
  assert "camera wall/cpu median 5.0/1.0ms" in summary
  assert "nested phases are not additive" in summary
  assert app._auto_timing_end == 0 and not app._auto_timing_samples
  monkeypatch.setattr(application.rl, "get_fps", lambda: 10)
  app._monitor_fps()
  assert app._auto_timing_end == 0  # Cooldown applies despite a new warning.


def test_screen_off_abandons_automatic_window_without_summary(frame, monkeypatch):
  app, _, _, warning = frame
  app._monitor_fps()
  app._auto_timing_samples.append({"camera": (1, 1)})
  warning.reset_mock()
  app._should_render = False
  monkeypatch.setattr(application.time, "sleep", lambda _: None)
  loop = app.render()
  next(loop)
  with pytest.raises(StopIteration):
    next(loop)
  assert not app._auto_timing_end and not app._auto_timing_samples
  assert app.frame_timing is None
  warning.assert_not_called()


def test_auto_window_collects_real_frame_with_debug_disabled(frame):
  app, _, thread_clock, _ = frame
  app._frame_timing_enabled = False
  app._monitor_fps()
  app._nav_stack[0].render.side_effect = lambda _: app.measure_frame_phase("camera", lambda: None)
  loop = app.render()
  next(loop)
  with pytest.raises(StopIteration):
    next(loop)
  assert app.frame_timing is not None
  assert "camera" in app._auto_timing_samples[0]
  assert thread_clock.call_count > 0
  assert not app._frame_timing_enabled and not app._show_fps


def test_following_unmeasured_frame_clears_prior_payload(frame, monkeypatch):
  app, _, _, _ = frame
  monkeypatch.setattr(application.rl, "window_should_close", Mock(side_effect=[False, False, True]))
  monkeypatch.setattr(application.rl, "get_fps", lambda: 60)
  app._frame_timing_enabled = True
  loop = app.render()
  next(loop)
  app._frame_timing_enabled = False
  next(loop)
  assert app.frame_timing is None and not app._frame_phases
  with pytest.raises(StopIteration):
    next(loop)
  assert app.frame_timing is None
