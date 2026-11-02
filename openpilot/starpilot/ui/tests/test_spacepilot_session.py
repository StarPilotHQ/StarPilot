"""Exclusive game admission, real drive-state requests, and unsafe lifecycle exits."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.drive_state.owner import DriveStateOwner
from openpilot.starpilot.ui.spacepilot import session
from openpilot.starpilot.ui.spacepilot.guard import DRIVING_PROCESSES, read_offroad_witness
from openpilot.starpilot.ui.tests.test_spacepilot_state import parked_ui


class Clock:
  now = 10_000_000_000
  offset = 2_000_000_000

  def refresh(self, ui, step=100_000_000):
    self.now += step
    for name in ui.sm:
      ui.sm.valid[name] = ui.sm.alive[name] = True
      ui.sm.recv_frame[name] = 10
      ui.sm.recv_time[name] = self.now / 1e9
      ui.sm.logMonoTime[name] = self.now + (self.offset if name == 'pandaStates' else 0)


class Vehicle:
  def __init__(self, cp):
    self.cp = cp
    self.fresh = True
    self.closed = False
    self.sample = NS(source_boot_ns=1)
    self.samples = ()

  def poll(self):
    result, self.samples = self.samples, ()
    return result

  def ready(self, *_):
    return self.fresh and not self.closed

  def close(self):
    self.closed = True


def game_ui(*, ignition=True):
  ui = parked_ui()
  ui.params = NS(get_bool=lambda _: not ui.started)
  ui.sm['deviceState'].thermalStatus = 'ok'
  ui.sm['managerState'] = NS(processes=[NS(name=name, running=True) for name in DRIVING_PROCESSES])
  ui.sm['carParams'] = ui.CP
  ui.sm['managerState'].processes.append(NS(name='pandad', running=True, pid=42))
  ui.sm['pandaStates'][0].uptime = 100
  ui.sm['pandaStates'][0].safetyModel = 'hyundai'
  ui.sm['pandaStates'][0].ignitionLine = ignition
  return ui


def offroad(ui):
  ui.started = ui.sm['deviceState'].started = False
  for process in ui.sm['managerState'].processes:
    if process.name in DRIVING_PROCESSES:
      process.running = False
  ui.sm['pandaStates'][0].safetyModel = 'noOutput'


@pytest.fixture
def setup(monkeypatch, tmp_path):
  clock = Clock()
  ui = game_ui()
  clock.refresh(ui, 0)
  params = Params(str(tmp_path / 'params'))
  owner = DriveStateOwner(params, tmp_path / 'drive-state')
  owner.initialize_manager()
  monkeypatch.setattr(session.time, 'monotonic_ns', lambda: clock.now)
  monkeypatch.setattr(session.time, 'clock_gettime_ns', lambda _: clock.now + clock.offset)
  monkeypatch.setattr(session.GameSession, '_clocks', lambda _: (clock.now, clock.now + clock.offset))
  game = session.GameSession(ui, owner, native_device=True, reader_factory=Vehicle)
  game.started_ns = clock.now
  yield game, ui, owner, clock
  game.close()


def enter(game, ui, clock, *, request=True):
  for _ in range(12):
    game.step()
    clock.refresh(ui)
  if request:
    assert game.stage == session.Stage.STOPPING
    assert game.requested_offroad
  offroad(ui)
  for _ in range(12):
    game.step()
    clock.refresh(ui)
  assert game.stage == session.Stage.PLAYING, game.reason


def test_actual_owner_request_waits_for_processes_nooutput_then_stays_offroad(setup):
  game, ui, owner, clock = setup
  initial = owner.snapshot()
  for _ in range(12):
    assert not game.step()
    clock.refresh(ui)
  assert owner.snapshot().mode == 'offroad' and owner.snapshot().revision != initial.revision
  assert game.stage == session.Stage.STOPPING and not game.playable()
  ui.started = ui.sm['deviceState'].started = False
  for _ in range(12):
    game.step()
    clock.refresh(ui)
    assert not game.playable()
  offroad(ui)
  for _ in range(12):
    game.step()
    clock.refresh(ui)
  assert game.playable()
  vehicle = game.vehicle
  state = owner.snapshot()
  game.close()
  assert vehicle.closed and game.vehicle is None
  assert owner.snapshot() == state and state.mode == 'offroad'


def test_offroad_ignition_off_touch_never_creates_vehicle_reader_or_changes_mode(setup):
  game, ui, owner, clock = setup
  ui.sm['pandaStates'][0].ignitionLine = False
  offroad(ui)
  initial = owner.snapshot()
  enter(game, ui, clock, request=False)
  assert game.vehicle is None and game.playable()
  assert owner.snapshot() == initial
  assert not game.wheel.armed


def test_offroad_with_ignition_requires_independent_fresh_park(setup):
  game, ui, owner, clock = setup
  offroad(ui)
  game.step()
  game.vehicle.fresh = False
  for _ in range(20):
    clock.refresh(ui)
    game.step()
  assert not game.playable() and game.stage == session.Stage.VERIFYING
  game.vehicle.fresh = True
  enter(game, ui, clock, request=False)
  assert game.playable()
  assert owner.snapshot().mode == 'auto'


@pytest.mark.parametrize('change', ['source', 'receiver', 'future', 'invalid', 'dead'])
@pytest.mark.parametrize('service', ['deviceState', 'pandaStates', 'managerState'])
def test_active_game_exits_for_every_lost_transport(setup, service, change):
  game, ui, _, clock = setup
  enter(game, ui, clock)
  if change == 'source':
    ui.sm.logMonoTime[service] -= 2_000_000_000
  elif change == 'receiver':
    ui.sm.recv_time[service] -= 2
  elif change == 'future':
    ui.sm.logMonoTime[service] += 2_000_000_000
  else:
    getattr(ui.sm, 'valid' if change == 'invalid' else 'alive')[service] = False
  assert not game.playable()
  game.step()
  assert game.stage == session.Stage.CLOSED and game.vehicle is None


@pytest.mark.parametrize('change', ['ignition', 'controls', 'safety', 'fault', 'harness', 'process', 'missing_process',
                                   'duplicate_process', 'started', 'raw_stale', 'thermal', 'drive_mode'])
def test_unsafe_exit_and_no_automatic_restore(setup, change):
  game, ui, owner, clock = setup
  enter(game, ui, clock)
  panda = ui.sm['pandaStates'][0]
  if change == 'ignition':
    panda.ignitionLine = False
  elif change == 'controls':
    panda.controlsAllowed = True
  elif change == 'safety':
    panda.safetyModel = 'hyundai'
  elif change == 'fault':
    panda.heartbeatLost = True
  elif change == 'harness':
    panda.harnessStatus = 'notConnected'
  elif change == 'process':
    ui.sm['managerState'].processes[0].running = True
  elif change == 'missing_process':
    ui.sm['managerState'].processes.pop(0)  # Remove a required driving process, not the added pandad fixture.
  elif change == 'duplicate_process':
    ui.sm['managerState'].processes.append(ui.sm['managerState'].processes[0])
  elif change == 'started':
    ui.started = ui.sm['deviceState'].started = True
  elif change == 'thermal':
    ui.sm['deviceState'].thermalStatus = 'overheated'
  elif change == 'raw_stale':
    game.vehicle.fresh = False
  else:
    owner.request('auto', expected_revision=owner.snapshot().revision, authorized=lambda: True, override_allowed=lambda: True)
  expected = owner.snapshot()
  assert not game.playable()
  game.step()
  assert game.stage == session.Stage.CLOSED
  assert owner.snapshot() == expected


def test_wheel_requires_post_admission_release_and_does_not_repeat(setup):
  game, ui, _, clock = setup
  enter(game, ui, clock)
  def edge(pressed):
    clock.refresh(ui, 140_000_000)
    game.vehicle.samples = (NS(source_boot_ns=clock.now + clock.offset, buttons=(('accelCruise', pressed),)),)
    return game.step()
  assert not edge(True)
  assert not edge(False)
  assert edge(True)
  assert not edge(True)
  assert not edge(False)
  assert edge(True)


def test_current_vehicle_identity_required_and_forced_flag_alone_never_admits(setup):
  game, ui, owner, clock = setup
  initial = owner.snapshot()
  ui.sm.recv_frame['carParams'] = ui.started_frame
  game.step()
  assert game.stage == session.Stage.CLOSED and owner.snapshot() == initial
  offroad(ui)
  assert read_offroad_witness(ui, clock.now, clock.now + clock.offset, native_device=True) is None
  assert read_offroad_witness(ui, clock.now, clock.now + clock.offset, native_device=True, preparing=True) is not None


def test_pending_transition_timeout_and_constructor_error_cleanup(setup):
  game, ui, owner, clock = setup
  game._reader_factory = Mock(side_effect=ValueError('Unsupported decoder'))
  game.step()
  assert game.stage == session.Stage.CLOSED and owner.snapshot().mode == 'auto'
  timed = session.GameSession(ui, owner, native_device=True, reader_factory=Vehicle)
  timed.started_ns = clock.now - session.TRANSITION_NS - 1
  timed.step()
  assert timed.stage == session.Stage.CLOSED and timed.vehicle is None


def test_device_resume_invalidates_session_clock(monkeypatch):
  game = session.GameSession(NS(), NS(), native_device=True)
  mono, boot = [10_000_000_000], [12_000_000_000]
  monkeypatch.setattr(session.time, 'monotonic_ns', lambda: mono[0])
  monkeypatch.setattr(session.time, 'clock_gettime_ns', lambda _: boot[0])
  assert game._clocks() == (mono[0], boot[0])
  boot[0] += 6_000_000
  with pytest.raises(ValueError, match='resumed'):
    game._clocks()


def test_offroad_witness_accepts_actual_schema_and_refuses_actual_thermal_faults(setup):
  from openpilot.cereal import messaging
  game, ui, _, clock = setup
  offroad(ui)
  ui.sm['pandaStates'][0].ignitionLine = False
  for name in ('deviceState', 'pandaStates', 'managerState'):
    event = messaging.new_message(name, 1 if name == 'pandaStates' else None, valid=True)
    if name == 'pandaStates':
      event.pandaStates[0] = vars(ui.sm[name][0])
    elif name == 'managerState':
      event.managerState.processes = [vars(p) for p in ui.sm[name].processes]
    else:
      event.deviceState = vars(ui.sm[name])
    ui.sm[name] = getattr(event.as_reader(), name)
  assert read_offroad_witness(ui, clock.now, clock.now + clock.offset, native_device=True) is not None
  for thermal in ('overheated', 'critical'):
    message = ui.sm['deviceState'].as_builder()
    message.thermalStatus = thermal
    ui.sm['deviceState'] = message.as_reader()
    assert read_offroad_witness(ui, clock.now, clock.now + clock.offset, native_device=True) is None


@pytest.fixture
def caller(setup, monkeypatch):
  """Actual UI before-frame/native page/session/owner with synthetic vehicle feeds."""
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.selfdrive.ui import ui as caller_ui
  from openpilot.starpilot.ui.spacepilot import native
  from openpilot.starpilot.ui.tests.test_spacepilot_state import mature_gate, read
  from openpilot.system.ui.lib.application import gui_app
  game, ui, owner, clock = setup
  monkeypatch.setattr(native, 'ui_state', ui)
  monkeypatch.setattr(caller_ui, 'ui_state', ui)
  monkeypatch.setattr(native, 'GameSession', lambda *_a, **_k: game)
  monkeypatch.setattr(native, 'live_witness', lambda: read(ui, clock.now))
  monkeypatch.setattr(native, '_ignition_return', None)
  monkeypatch.setattr(native, 'device', Mock())
  root = Mock()
  active = [root]
  monkeypatch.setattr(gui_app, 'get_active_widget', lambda: active[0])
  page = native.SpacePilotPage(mature_gate(), lambda: active.__setitem__(0, root))
  page.set_rect(rl.Rectangle(0, 0, 1072, 480))
  active[0] = page
  page.show_event()
  ui.update = Mock()
  def frame(*, advance=100_000_000, refresh=True, render=True):
    if refresh:
      clock.refresh(ui, advance)
    else:
      clock.now += advance
    caller_ui.update_frame(game_mode=native.poll_game_mode)
    if render and not page.closed:
      page._tick()
  for _ in range(12):
    frame()
  assert game.stage == session.Stage.STOPPING and native._ignition_return is not None
  offroad(ui)
  for _ in range(12):
    frame()
  assert game.stage == session.Stage.PLAYING
  yield NS(game=game, ui=ui, owner=owner, clock=clock, native=native, page=page, frame=frame, active=active, root=root)
  page.hide_event()


@pytest.mark.parametrize('exit_first', [False, True])
def test_confirmed_key_off_releases_only_game_request_even_with_screen_asleep(caller, exit_first):
  from openpilot.starpilot.drive_state.resolver import should_start
  c = caller
  original = c.owner.snapshot()
  if exit_first:
    c.page._exit(unsafe=True)
    assert c.owner.snapshot() == original
    assert c.native._ignition_return is not None
  c.ui.sm['pandaStates'][0].ignitionLine = False
  c.frame()
  assert c.page.closed and c.game.vehicle is None
  assert c.owner.snapshot() == original  # One off sample is never sufficient.
  for _ in range(9):
    c.frame(render=False)
    assert c.owner.snapshot() == original
  c.frame(render=False)
  assert c.owner.snapshot().mode == 'auto'
  assert c.native._ignition_return is None
  assert not should_start(c.owner.snapshot().mode, {'ignition': False}, {'ready': True}, already_started=False)
  assert should_start(c.owner.snapshot().mode, {'ignition': True}, {'ready': True}, already_started=False)


@pytest.mark.parametrize('change', ['empty', 'stale_source', 'stale_receiver', 'invalid', 'dead', 'future', 'reordered',
                                   'fault', 'harness', 'panda_restart', 'pandad_restart', 'missing_process', 'topology',
                                   'clock_resume', 'ui_stall', 'external_revision', 'manager_restart', 'no_output', 'driving_process'])
def test_key_off_unknown_or_discontinuous_evidence_never_releases(caller, change):
  c = caller
  p = c.ui.sm['pandaStates'][0]
  p.ignitionLine = False
  if change == 'empty':
    c.ui.sm['pandaStates'] = []
  elif change in ('stale_source', 'stale_receiver', 'future', 'reordered'):
    if change == 'stale_receiver':
      c.ui.sm.recv_time['pandaStates'] -= 1.
    else:
      c.ui.sm.logMonoTime['pandaStates'] += 1_000_000_000 if change == 'future' else -1_000_000_000 if change == 'stale_source' else -1
  elif change in ('invalid', 'dead'):
    getattr(c.ui.sm, 'valid' if change == 'invalid' else 'alive')['pandaStates'] = False
  elif change == 'fault':
    p.heartbeatLost = True
  elif change == 'harness':
    p.harnessStatus = 'notConnected'
  elif change == 'panda_restart':
    p.uptime = 0
  elif change == 'pandad_restart':
    next(p for p in c.ui.sm['managerState'].processes if p.name == 'pandad').pid += 1
  elif change == 'missing_process':
    c.ui.sm['managerState'].processes.pop()
  elif change == 'topology':
    c.ui.sm['pandaStates'].append(NS(**vars(p)))
  elif change == 'clock_resume':
    c.clock.offset += 6_000_000
  elif change == 'external_revision':
    c.owner.request('auto', expected_revision=c.owner.snapshot().revision, authorized=lambda: True, override_allowed=lambda: True)
    c.owner.request('offroad', expected_revision=c.owner.snapshot().revision, authorized=lambda: True, override_allowed=lambda: True)
  elif change == 'manager_restart':
    c.owner.initialize_manager()
  elif change == 'no_output':
    p.safetyModel = 'hyundai'
  elif change == 'driving_process':
    c.ui.sm['managerState'].processes[0].running = True
  expected = c.owner.snapshot()
  c.frame(refresh=False, advance=400_000_000 if change == 'ui_stall' else 0)
  assert c.native._ignition_return is None
  for _ in range(15):
    c.frame(render=False)  # Reconnecting/refreshing cannot revive discarded authority.
  assert c.owner.snapshot() == expected


def test_transient_off_and_multiple_pandas_require_sustained_all_off(setup, monkeypatch):
  from openpilot.starpilot.ui.spacepilot.ignition import IgnitionReturn
  game, ui, owner, clock = setup
  ui.sm['pandaStates'].append(NS(**vars(ui.sm['pandaStates'][0])))
  for _ in range(12):
    game.step()
    if game.ignition_return is not None:
      game.ignition_return.observe()
    clock.refresh(ui)
  pending = game.ignition_return
  assert isinstance(pending, IgnitionReturn) and not pending.finished
  offroad(ui)
  for p in ui.sm['pandaStates']:
    p.safetyModel = 'noOutput'
  ui.sm['pandaStates'][0].ignitionLine = False
  for _ in range(15):
    clock.refresh(ui)
    assert not pending.observe()
  assert pending.off_since is None  # Other Panda is still on.
  ui.sm['pandaStates'][1].ignitionLine = False
  for _ in range(5):
    clock.refresh(ui)
    assert not pending.observe()
  ui.sm['pandaStates'][0].ignitionCan = True
  clock.refresh(ui)
  assert not pending.observe() and pending.off_since is None
  ui.sm['pandaStates'][0].ignitionCan = False
  for _ in range(10):
    clock.refresh(ui)
    assert not pending.observe()
  clock.refresh(ui)
  assert pending.observe()
  initial = owner.snapshot()
  pending.release(lambda: False)
  assert owner.snapshot() == initial  # An active game cannot authorize Auto.


def test_owner_rechecks_ignition_during_auto_transaction(caller, monkeypatch):
  c = caller
  c.ui.sm['pandaStates'][0].ignitionLine = False
  for _ in range(10):
    c.frame()
  original = c.owner.snapshot()
  request = c.owner.request
  def changed_before_write(mode, **kwargs):
    assert c.page.closed
    c.ui.sm['pandaStates'][0].ignitionLine = True
    return request(mode, **kwargs)
  monkeypatch.setattr(c.owner, 'request', changed_before_write)
  c.frame(render=False)
  assert c.owner.snapshot() == original
  assert c.native._ignition_return is None


def test_ui_restart_does_not_adopt_existing_offroad_request(caller, monkeypatch):
  c = caller
  original = c.owner.snapshot()
  c.page.hide_event()
  monkeypatch.setattr(c.native, '_ignition_return', None)  # New UI process has no game ownership.
  c.ui.sm['pandaStates'][0].ignitionLine = False
  for _ in range(15):
    c.frame(render=False)
  assert c.owner.snapshot() == original


@pytest.mark.parametrize('service', ['pandaStates', 'deviceState', 'managerState'])
@pytest.mark.parametrize('change', ['source', 'receipt', 'invalid', 'dead', 'future'])
def test_key_off_requires_all_offroad_source_and_receipt_evidence(caller, service, change):
  c = caller
  original = c.owner.snapshot()
  c.ui.sm['pandaStates'][0].ignitionLine = False
  if change == 'source':
    c.ui.sm.logMonoTime[service] -= 2_000_000_000
  elif change == 'receipt':
    c.ui.sm.recv_time[service] -= 2
  elif change == 'future':
    c.ui.sm.logMonoTime[service] += 2_000_000_000
  else:
    getattr(c.ui.sm, 'valid' if change == 'invalid' else 'alive')[service] = False
  c.frame(refresh=False, advance=0)
  assert c.native._ignition_return is None
  for _ in range(12):
    c.frame(render=False)
  assert c.owner.snapshot() == original


def test_busy_owner_refuses_auto_once_without_retry(caller):
  import fcntl
  c = caller
  original = c.owner.snapshot()
  c.ui.sm['pandaStates'][0].ignitionLine = False
  for _ in range(10):
    c.frame()
  with (c.owner.root / '.lock').open('r+') as lease:
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    c.frame(render=False)
    assert c.native._ignition_return is None
  for _ in range(12):
    c.frame(render=False)
  assert c.owner.snapshot() == original


@pytest.mark.parametrize('rendering', [True, False])
def test_covered_game_is_closed_before_auto_in_actual_nav_caller(caller, monkeypatch, rendering):
  from openpilot.system.ui.lib.application import gui_app
  c = caller
  monkeypatch.setattr(gui_app, '_nav_stack', [c.root, c.page])
  monkeypatch.setattr(gui_app, 'get_active_widget', type(gui_app).get_active_widget.__get__(gui_app))
  c.page.on_unsafe = lambda: gui_app.pop_widgets_to(c.root, instant=True)
  modal = Mock()
  gui_app.push_widget(modal)
  assert gui_app.get_active_widget() is modal
  assert not c.page.closed and c.game.vehicle is not None
  reader = c.game.vehicle
  # Reach confirmed key-off before the next nav tick (also happens when asleep).
  c.ui.sm['pandaStates'][0].ignitionLine = False
  for _ in range(10):
    c.frame(render=False)
    assert c.owner.snapshot().mode == 'offroad' and not c.page.closed
  request = c.owner.request
  calls = []
  def checked_request(mode, **kwargs):
    calls.append((mode, c.page.closed, c.game.stage, c.game.vehicle is None, reader.closed,
                  c.page._tick not in gui_app._nav_stack_ticks, not c.page.flight.gates))
    return request(mode, **kwargs)
  monkeypatch.setattr(c.owner, 'request', checked_request)
  monkeypatch.setattr(gui_app, '_should_render', rendering)
  c.frame(render=gui_app._should_render)
  assert calls == [('auto', True, session.Stage.CLOSED, True, True, True, True)]
  assert c.owner.snapshot().mode == 'auto'


def test_failed_covered_page_cleanup_cannot_authorize_auto(caller, monkeypatch):
  c = caller
  c.ui.sm['pandaStates'][0].ignitionLine = False
  for _ in range(10):
    c.frame(render=False)
  reader = c.game.vehicle
  with monkeypatch.context() as patch:
    patch.setattr(reader, 'close', Mock(side_effect=OSError('fixture cleanup failure')))
    c.frame(render=False)
  assert c.owner.snapshot().mode == 'offroad'
  assert c.native._ignition_return is None
  assert c.page in c.native._open_games  # Incomplete cleanup remains a refusal.
  c.page.hide_event()
  assert c.page not in c.native._open_games
