"""Actual touch dispatch and navigation around a newly parked game launch."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.spacepilot import native
from openpilot.starpilot.ui.spacepilot.state import FiveTaps
from openpilot.starpilot.ui.tests.test_spacepilot_large import adapter as adapter
from openpilot.starpilot.ui.tests.test_spacepilot_session import setup as setup, offroad
from openpilot.starpilot.ui.tests.test_spacepilot_state import mature_gate, parked_ui, read
from openpilot.system.ui.lib.application import MouseEvent, MousePos, gui_app


@pytest.mark.parametrize('spacing', [.06, .2, .7, 1.2])
def test_five_releases_accept_natural_fast_and_slow_paces(spacing):
  taps = FiveTaps()
  for index in range(4):
    assert not taps.tap(10 + index * spacing)
  assert taps.tap(10 + 4 * spacing)


def test_touch_noise_and_expired_sequences_still_do_not_launch():
  taps = FiveTaps()
  for now in (10., 10., 10.01, 10.02, 10.03):
    assert not taps.tap(now)
  assert taps.count == 1
  for now in (12., 12.2, 12.4, 12.6):
    assert not taps.tap(now)
  assert taps.count == 4
  assert taps.tap(12.8)


@pytest.fixture
def version(monkeypatch):
  monkeypatch.setattr(gui_app, 'texture', lambda *_a, **_k: NS(width=64, height=64))
  monkeypatch.setattr(gui_app, 'font', lambda *_a: rl.Font())
  monkeypatch.setattr(native, 'ui_state', NS(params=NS(get=lambda _: '0.10.3')))
  monkeypatch.setattr(native, 'live_witness', lambda: read(parked_ui()))
  widget = native.VersionButton(Mock())
  widget.set_rect(rl.Rectangle(0, 0, 402, 180))
  widget._sub_label.set_rect(rl.Rectangle(40, 110, 322, 46))
  widget.show_event()
  yield widget
  widget.hide_event()


def events(pos, index):
  now = 10 + index * .2
  return [MouseEvent(pos, 0, True, False, True, now), MouseEvent(pos, 0, False, True, False, now + .04)]


@pytest.mark.parametrize('batched', [False, True])
@pytest.mark.parametrize('profile', ['c4', 'big'])
def test_original_touch_times_and_valid_immature_park_count_all_five(version, adapter, monkeypatch, batched, profile):
  widget = version if profile == 'c4' else adapter
  widget.gate.reset()
  bounds = version._sub_label.rect if profile == 'c4' else adapter._version_bounds()
  pos = MousePos(bounds.x + 20, bounds.y + 10)
  monkeypatch.setattr(native.time, 'monotonic', lambda: 11.)
  pushed = Mock()
  monkeypatch.setattr(gui_app, 'push_widget', pushed)
  if batched:
    widget._update_state()
    monkeypatch.setattr(gui_app, '_mouse_events', [e for i in range(5) for e in events(pos, i)])
    widget._process_mouse_events()
  else:
    for i in range(5):
      widget._update_state()
      monkeypatch.setattr(gui_app, '_mouse_events', events(pos, i))
      widget._process_mouse_events()
  pushed.assert_called_once()
  game = pushed.call_args.args[0]
  assert isinstance(game, native.SpacePilotPage)
  assert not game.gate.ready
  assert game.session.stage == native.Stage.VERIFYING
  game.session.close()


@pytest.mark.parametrize('profile', ['c4', 'big'])
def test_repeated_release_without_new_press_does_not_count(version, adapter, monkeypatch, profile):
  widget = version if profile == 'c4' else adapter
  bounds = version._sub_label.rect if profile == 'c4' else adapter._version_bounds()
  pos = MousePos(bounds.x + 20, bounds.y + 10)
  push = Mock()
  monkeypatch.setattr(gui_app, 'push_widget', push)
  monkeypatch.setattr(gui_app, '_mouse_events', events(pos, 0))
  widget._process_mouse_events()
  for i in range(1, 10):
    monkeypatch.setattr(gui_app, '_mouse_events', [events(pos, i)[1]])
    widget._process_mouse_events()
  assert widget.taps.count == 1
  push.assert_not_called()


@pytest.mark.parametrize('brake_pressed', [False, True])
@pytest.mark.parametrize('delay', [0, 400_000_000])
def test_fresh_park_page_prepares_after_dwell_reset_and_keeps_actual_owner_gates(setup, monkeypatch, brake_pressed, delay):
  game, ui, owner, clock = setup
  ui.sm['carState'].brakePressed = brake_pressed
  monkeypatch.setattr(native, 'ui_state', ui)
  monkeypatch.setattr(native, 'live_witness', lambda: read(ui, clock.now))
  monkeypatch.setattr(native, 'GameSession', lambda *_a, **_k: game)
  monkeypatch.setattr(gui_app, '_nav_stack', [Mock()])
  monkeypatch.setattr(gui_app, '_nav_stack_ticks', [])
  gate = mature_gate() if delay else native.ParkGate()
  page = native.SpacePilotPage(gate, Mock())
  clock.refresh(ui, delay)
  gui_app.push_widget(page)
  try:
    page._tick()
    assert not page.closed and game.stage == native.Stage.VERIFYING
    assert owner.snapshot().mode == 'auto' and not game.playable()
    assert game.gate.since is None  # Reader setup yields before using this frame's snapshot.
    clock.refresh(ui)
    page._tick()
    assert owner.snapshot().mode == 'auto' and not game.playable()
    assert game.gate.since == clock.now
    for _ in range(9):
      clock.refresh(ui)
      page._tick()
      assert owner.snapshot().mode == 'auto' and not game.playable()
    clock.refresh(ui)
    page._tick()
    assert game.stage == native.Stage.STOPPING and owner.snapshot().mode == 'offroad'
    assert not game.playable()
    offroad(ui)
    for _ in range(12):
      clock.refresh(ui)
      page._tick()
    assert game.stage == native.Stage.PLAYING and game.playable()
    assert not page.closed
  finally:
    page.hide_event()


@pytest.mark.parametrize('condition', ['park', 'brake', 'drive', 'speed', 'alert', 'stale', 'closed', 'other_widget'])
def test_actual_c4_startup_redirect_only_yields_to_fresh_open_parked_game(monkeypatch, condition):
  from openpilot.starpilot.ui import runtime_app
  from openpilot.selfdrive.ui.mici.layouts import main
  ui = parked_ui()
  ui.sm['carState'].brakePressed = condition == 'brake'
  if condition == 'drive':
    ui.sm['carState'].gearShifter = 'drive'
  elif condition == 'speed':
    ui.sm['carState'].standstill = False
    ui.sm['carState'].vEgo = .2
  elif condition == 'alert':
    ui.sm['selfdriveState'].alertStatus = 'critical'
  elif condition == 'stale':
    ui.sm.logMonoTime['carState'] -= 1_000_000_000
  monkeypatch.setattr(native, 'live_witness', lambda: read(ui))
  monkeypatch.setattr(main, 'ui_state', ui)
  monkeypatch.setattr(rl, 'get_time', lambda: 10.)
  owner = object.__new__(runtime_app.StarMiciMainLayout)
  owner._prev_onroad, owner._prev_standstill = True, True
  owner._onroad_time_delay = 5.
  owner._onboarding_window = Mock()
  page = object.__new__(native.SpacePilotPage)
  page.closed = condition == 'closed'
  page.session = Mock(stage=native.Stage.CLOSED if page.closed else native.Stage.VERIFYING)
  monkeypatch.setattr(gui_app, 'get_active_widget', lambda: object() if condition == 'other_widget' else page)
  monkeypatch.setattr(gui_app, 'widget_in_stack', lambda _: False)
  pop = Mock()
  monkeypatch.setattr(gui_app, 'pop_widgets_to', pop)
  owner._handle_transitions()
  assert owner._onroad_time_delay is None
  if condition in ('park', 'brake'):
    pop.assert_not_called()
  else:
    assert pop.called


def test_unsafe_park_at_show_still_closes_before_request(setup, monkeypatch):
  game, ui, owner, clock = setup
  ui.sm['carState'].gearShifter = 'drive'
  monkeypatch.setattr(native, 'ui_state', ui)
  monkeypatch.setattr(native, 'live_witness', lambda: read(ui, clock.now))
  monkeypatch.setattr(native, 'GameSession', lambda *_a, **_k: game)
  monkeypatch.setattr(gui_app, '_nav_stack', [Mock()])
  monkeypatch.setattr(gui_app, '_nav_stack_ticks', [])
  page = native.SpacePilotPage(mature_gate(), Mock())
  gui_app.push_widget(page)
  page._tick()
  assert page.closed and game.stage == native.Stage.CLOSED
  assert owner.snapshot().mode == 'auto'
