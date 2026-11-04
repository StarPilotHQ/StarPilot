"""Cold native entry and preempted clock reads from the recorded d5 exit paths."""

import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openpilot.starpilot.ui.spacepilot import guard, ignition, native, session
from openpilot.starpilot.ui.tests.test_spacepilot_session import Clock, Vehicle, game_ui, offroad, setup as setup
from openpilot.starpilot.ui.tests.test_spacepilot_state import mature_gate, read
from openpilot.system.ui.lib.application import gui_app


def clock_reads(monkeypatch, *, delayed=1, backwards=False, offset=2_000_000_000):
  now = 10_000_000_000
  monos, boots = [], []
  for index in range(4):
    delta = -1 if backwards else 5_000_000 if index < delayed else 1_000
    monos.extend((now, now + delta))
    boots.append(now + offset)
    now += max(delta, 0)
  mono = Mock(side_effect=monos)
  # Keep background UI readers from consuming the scripted clock pairs.
  monkeypatch.setattr(guard, 'time', NS(monotonic_ns=mono, clock_gettime_ns=Mock(side_effect=boots), CLOCK_BOOTTIME=time.CLOCK_BOOTTIME))
  return mono


def test_clock_reads_leave_unrelated_process_clocks_untouched(monkeypatch):
  process_mono, process_boot = time.monotonic_ns, time.clock_gettime_ns
  mono = clock_reads(monkeypatch, delayed=3)
  assert time.monotonic_ns is process_mono
  assert time.clock_gettime_ns is process_boot
  time.monotonic_ns()
  time.clock_gettime_ns(time.CLOCK_BOOTTIME)
  assert mono.call_count == guard.time.clock_gettime_ns.call_count == 0
  with pytest.raises(ValueError, match='stable device clock'):
    guard.sample_clocks()
  assert mono.call_count == 6
  assert guard.time.clock_gettime_ns.call_count == 3


def witness_ui(monkeypatch):
  ui = game_ui()
  Clock().refresh(ui, 0)
  monkeypatch.setattr(native, 'ui_state', ui)
  monkeypatch.setattr(native.HARDWARE, 'get_device_type', lambda: 'mici')
  return ui


@pytest.mark.parametrize('caller', ['session', 'entry', 'ignition'])
@pytest.mark.parametrize('delayed', [1, 2])
def test_preempted_read_is_discarded_then_fresh_pair_used(monkeypatch, caller, delayed):
  ui = witness_ui(monkeypatch)
  game = session.GameSession(ui, Mock(), native_device=True)
  mono = clock_reads(monkeypatch, delayed=delayed)
  if caller == 'session':
    result = game._clocks()
    assert result[0] == 10_000_000_000 + delayed * 5_000_000 + 1_000
    assert result[1] - result[0] == 1_999_999_000
  elif caller == 'entry':
    witness = native.live_witness()
    assert witness is not None and witness.mono_ns == 10_000_000_000 + delayed * 5_000_000 + 1_000
  else:
    sample = ignition.sample_ignition(ui, native_device=True)
    assert sample is not None and sample.mono == 10_000_000_000 + delayed * 5_000_000 + 1_000
  assert mono.call_count == 2 * (delayed + 1)


@pytest.mark.parametrize('caller', ['session', 'entry', 'ignition'])
def test_three_preemptions_are_bounded_and_still_refuse(monkeypatch, caller):
  ui = witness_ui(monkeypatch)
  game = session.GameSession(ui, Mock(), native_device=True)
  mono = clock_reads(monkeypatch, delayed=3)
  if caller == 'session':
    with pytest.raises(ValueError, match='stable device clock'):
      game._clocks()
  elif caller == 'entry':
    assert native.live_witness() is None
  else:
    assert ignition.sample_ignition(ui, native_device=True) is None
  assert mono.call_count <= 6


def test_resumed_clock_is_not_hidden_by_successful_resample(monkeypatch):
  game = session.GameSession(NS(), Mock(), native_device=True)
  game._clock_offset = 2_000_000_000
  clock_reads(monkeypatch, offset=2_006_000_000)
  with pytest.raises(ValueError):
    game._clocks()
  assert game._clock_offset == 2_000_000_000


@pytest.mark.parametrize('caller', ['session', 'entry', 'ignition'])
def test_backwards_clock_never_retries_into_admission(monkeypatch, caller):
  ui = witness_ui(monkeypatch)
  game = session.GameSession(ui, Mock(), native_device=True)
  mono = clock_reads(monkeypatch, backwards=True)
  if caller == 'session':
    with pytest.raises(ValueError):
      game._clocks()
  elif caller == 'entry':
    assert native.live_witness() is None
  else:
    assert ignition.sample_ignition(ui, native_device=True) is None
  assert mono.call_count == 2


@pytest.mark.parametrize('change', ['none', 'drive', 'engaged', 'stale', 'timeout'])
def test_cold_reader_yields_to_next_ui_sample_without_request_or_input(setup, monkeypatch, change):
  game, ui, owner, clock = setup
  monkeypatch.setattr(native, 'ui_state', ui)
  monkeypatch.setattr(native, 'live_witness', lambda: read(ui, clock.now))
  monkeypatch.setattr(native, 'GameSession', lambda *_a, **_k: game)
  monkeypatch.setattr(native, 'device', Mock())
  monkeypatch.setattr(gui_app, '_nav_stack', [Mock()])
  monkeypatch.setattr(gui_app, '_nav_stack_ticks', [])
  monkeypatch.setattr(native, '_ignition_return', None)
  def cold_reader(cp):
    clock.now += 400_000_000  # Recorded first launch spent396ms in a UI frame.
    return Vehicle(cp)
  game._reader_factory = cold_reader
  page = native.SpacePilotPage(mature_gate(), Mock())
  gui_app.push_widget(page)
  try:
    page._tick()
    assert not page.closed and game.stage == session.Stage.VERIFYING
    assert owner.snapshot().mode == 'auto' and not game.playable()
    assert game.gate.since is None and not game.wheel.armed
    if change == 'timeout':
      clock.refresh(ui, session.TRANSITION_NS)
    elif change != 'stale':
      clock.refresh(ui)
    if change == 'drive':
      ui.sm['carState'].gearShifter = 'drive'
    elif change == 'engaged':
      ui.sm['selfdriveState'].enabled = True
    page._tick()
    if change != 'none':
      assert page.closed and game.stage == session.Stage.CLOSED and game.vehicle is None
      assert owner.snapshot().mode == 'auto'
      return
    assert game.gate.since == clock.now and game.stage == session.Stage.VERIFYING
    for _ in range(9):
      clock.refresh(ui)
      page._tick()
      assert owner.snapshot().mode == 'auto' and not game.playable()
    clock.refresh(ui)
    page._tick()
    assert game.stage == session.Stage.STOPPING and owner.snapshot().mode == 'offroad'
    assert not game.playable()
    offroad(ui)
    for _ in range(12):
      clock.refresh(ui)
      page._tick()
    assert game.stage == session.Stage.PLAYING and not page.closed and game.playable()
  finally:
    page.hide_event()


def test_playing_offroad_game_survives_preempted_read_without_bypassing_witness(monkeypatch):
  from openpilot.starpilot.ui.spacepilot.guard import read_offroad_witness
  ui = witness_ui(monkeypatch)
  offroad(ui)
  ui.sm['pandaStates'][0].ignitionLine = False
  game = session.GameSession(ui, Mock(), native_device=True)
  game.stage = session.Stage.PLAYING
  game._was_ignition = False
  game._clock_offset = 2_000_000_000
  game.gate.since = 9_000_000_000
  game.gate.previous = read_offroad_witness(ui, 10_000_000_000, 12_000_000_000, native_device=True)
  assert game.gate.previous is not None
  game.gate.ready = True
  mono = clock_reads(monkeypatch)
  assert not game.step()
  assert game.stage == session.Stage.PLAYING and game.gate.ready
  assert mono.call_count == 6
  assert not game.requested_offroad and game.vehicle is None
  game.close()
