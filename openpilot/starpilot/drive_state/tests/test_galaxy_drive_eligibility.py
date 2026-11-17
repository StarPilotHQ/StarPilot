from types import SimpleNamespace as NS
from unittest.mock import Mock
from typing import cast

import pytest

from openpilot.starpilot.drive_state.control import DriveStateControl
from openpilot.starpilot.drive_state.owner import DriveStateOwner, Mode, Rejected
from openpilot.starpilot.drive_state.tests.test_owner import Params, BOOT
from openpilot.starpilot.drive_state.evidence import PhysicalSource
from openpilot.starpilot.drive_state.tests.test_evidence import Messages


@pytest.fixture
def fixture():
  now = [1_000_000_000, 10_000_000_000]
  messages = Messages()
  source = PhysicalSource(messages, mono=lambda: now[0], boot=lambda: sum(now))
  now[0] = 2_100_000_000
  try:
    yield NS(now=now, sm=messages, source=source)
  finally:
    source.close()


def test_force_onroad_never_uses_park_admission(tmp_path):
  params = Params()
  owner = DriveStateOwner(params, tmp_path / 'owner', alive=lambda _: True)
  owner.initialize(pid=1, birth=2, boot=BOOT)
  physical = Mock(side_effect=AssertionError('Onroad does not require Park'))
  revision = owner.snapshot().revision
  assert revision is not None
  state = owner.request('onroad', expected_revision=revision,
                        authorized=lambda: True, override_allowed=physical)
  assert state.mode == Mode.ONROAD
  physical.assert_not_called()


def test_force_offroad_rechecks_actual_park_before_commit(tmp_path):
  params = Params()
  owner = DriveStateOwner(params, tmp_path / 'owner', alive=lambda _: True)
  owner.initialize(pid=1, birth=2, boot=BOOT)
  baseline = list(params.writes)
  revision = owner.snapshot().revision
  assert revision is not None
  with pytest.raises(Rejected, match='Park'):
    owner.request('offroad', expected_revision=revision,
                  authorized=lambda: True, override_allowed=lambda: False)
  assert params.writes == baseline
  values = iter((True, False))
  revision = owner.snapshot().revision
  assert revision is not None
  with pytest.raises(Rejected):
    owner.request('offroad', expected_revision=revision,
                  authorized=lambda: True, override_allowed=lambda: next(values))
  assert params.writes == baseline


def test_requested_mode_never_claims_actual_update_authority(tmp_path):
  params = Params()
  owner = DriveStateOwner(params, tmp_path / 'owner', alive=lambda _: True)
  owner.initialize(pid=1, birth=2, boot=BOOT)
  physical = NS(allowed=lambda: False)
  control = DriveStateControl(owner, physical, effective=lambda: False)
  state = control.snapshot()
  assert not state['overrideAllowed']
  assert control.next_mode(state) == Mode.OFFROAD
  control.change('onroad', state['revision'], lambda: True)
  assert control.snapshot()['mode'] == 'onroad'
  assert control.snapshot()['effective'] == 'offroad'


def test_normal_garage_flow_current_nooutput_and_manager_offroad(fixture):
  f = fixture
  f.sm.values['deviceState'] = NS(started=False)
  for table, value in ((f.sm.seen, True), (f.sm.alive, True), (f.sm.valid, True),
                       (f.sm.logMonoTime, 2_000_000_000), (f.sm.recv_time, 2.0)):
    table['deviceState'] = value
  assert f.source.allowed()
  assert f.source.update_ready(True)
  assert not f.source.update_ready(False)
  f.sm.values['deviceState'].started = True
  assert not f.source.update_ready(True)
  f.sm.values['deviceState'].started = False
  f.sm.values['pandaStates'][0].controlsAllowed = True
  assert not f.source.update_ready(True)


def test_forced_offroad_does_not_borrow_old_park_while_ignition_on(fixture):
  f = fixture
  f.sm.values['pandaStates'][0].ignitionLine = True
  f.sm.values['pandaStates'][0].safetyModel = 'toyota'
  f.sm.values['deviceState'] = NS(started=False)
  for table, value in ((f.sm.seen, True), (f.sm.alive, True), (f.sm.valid, True),
                       (f.sm.logMonoTime, 2_000_000_000), (f.sm.recv_time, 2.0)):
    table['deviceState'] = value
  f.sm.logMonoTime['carState'] = 1_000_000_000
  status = f.source.update_status(True)
  assert not status['allowed']
  assert 'ignition' in status['reason']
  f.sm.values['pandaStates'][0].ignitionLine = False
  f.sm.values['pandaStates'][0].safetyModel = 'noOutput'
  for table, value in ((f.sm.seen, True), (f.sm.alive, True), (f.sm.valid, True),
                       (f.sm.logMonoTime, 2_000_000_000), (f.sm.recv_time, 2.0)):
    table['deviceState'] = value
  assert f.source.update_ready(True)


@pytest.mark.parametrize('fault', ['moving', 'drive_gear', 'can_invalid', 'enabled', 'active'])
def test_live_park_guards_remain_authoritative(fixture, fault):
  f = fixture
  f.sm.values['pandaStates'][0].ignitionLine = True
  if fault == 'moving':
    f.sm.values['carState'].standstill = False
  elif fault == 'drive_gear':
    f.sm.values['carState'].gearShifter = 'drive'
  elif fault == 'can_invalid':
    f.sm.values['carState'].canValid = False
  else:
    setattr(f.sm.values['selfdriveState'], fault, True)
  assert not f.source.allowed()


def test_authenticated_remote_force_drive_modes_use_shared_owner():
  from contextlib import ExitStack
  from openpilot.starpilot.drive_state.tests.test_http import ForceHttpTest
  case = ForceHttpTest(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    case.setUp()
    case.allowed = False
    result = case.drive_action('onroad', remote=True)
    assert result[0] == 200 and result[1]['mode'] == 'onroad'
    assert result[1]['effective'] == 'offroad'
    assert case.drive_action('offroad', remote=True)[0] == 409
    assert case.drive.snapshot().mode == Mode.ONROAD
    case.allowed = True
    result = case.drive_action('offroad', remote=True)
    assert result[0] == 200 and result[1]['mode'] == 'offroad'
    assert case.drive.snapshot().mode == Mode.OFFROAD
    case.control.change('offroad', case.drive.snapshot().revision, lambda: True)
    assert case.drive.snapshot().mode == Mode.OFFROAD


def test_update_snapshot_keeps_one_admission_and_matching_reason():
  from contextlib import ExitStack
  from openpilot.starpilot.galaxy.tests.test_software_operations import SoftwareOperationsTest
  case = SoftwareOperationsTest(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    case.setUp()
    context = NS(update_status=Mock(side_effect=[{'allowed': False, 'reason': 'Current Panda status is stale'},
                                               {'allowed': True, 'reason': None}]), close=Mock())
    case.owner.context = cast(PhysicalSource, context)
    case.owner.parked = lambda: context.update_status(True)['allowed']
    view = case.owner.snapshot(include_history=False)
    assert not view['parked'] and not view['canFastUpdate'] and not view['canInstall']
    assert view['reason'] == 'Current Panda status is stale'
    context.update_status.assert_called_once_with(True)


@pytest.mark.parametrize('moving', [False, True])
@pytest.mark.parametrize('engaged', [False, True])
def test_local_force_offroad_with_ignition_on_keeps_park_authority_separate(fixture, tmp_path, moving, engaged):
  f = fixture
  f.sm.values['pandaStates'][0].ignitionLine = True
  f.sm.values['deviceState'] = NS(started=True)
  car = f.sm.values['carState']
  car.standstill, car.gearShifter, car.vEgo = not moving, 'drive' if moving else 'park', 15.0 if moving else 0.0
  f.sm.values['selfdriveState'].enabled = engaged
  f.sm.values['selfdriveState'].active = engaged
  owner = DriveStateOwner(Params(), tmp_path / 'local', alive=lambda _: True)
  owner.initialize(pid=1, birth=2, boot=BOOT)
  control = DriveStateControl(owner, f.source, effective=lambda: f.sm.values['deviceState'].started)
  status = control.snapshot()
  assert status['overrideAllowed'] is (not moving and not engaged)
  assert control.next_mode(status) == Mode.OFFROAD
  control.cycle(status, lambda: True)
  from openpilot.starpilot.drive_state.resolver import should_start
  f.sm.values['deviceState'].started = should_start(owner.snapshot().mode, {'ignition': True}, {'ready': True}, already_started=True)
  assert owner.snapshot().mode == Mode.OFFROAD
  assert control.snapshot()['effective'] == 'offroad'
  assert f.source.allowed() is (not moving and not engaged)
  if moving or engaged:
    assert not f.source.update_ready(True)


def test_galaxy_force_offroad_preserves_exact_rejection_reason_and_auto_recovery(tmp_path):
  params = Params()
  owner = DriveStateOwner(params, tmp_path / 'reason', alive=lambda _: True)
  owner.initialize(pid=1, birth=2, boot=BOOT)
  physical = NS(status=Mock(return_value={'allowed': False, 'reason': 'Current speed and gear are unavailable'}))
  control = DriveStateControl(owner, physical, effective=lambda: False)
  revision = owner.snapshot().revision
  before = list(params.writes)
  with pytest.raises(Rejected, match='Current speed and gear are unavailable'):
    control.change_galaxy('offroad', revision, lambda: True)
  assert params.writes == before
  assert owner.snapshot().mode == Mode.AUTO
  control.change_galaxy('auto', revision, lambda: True)
  assert owner.snapshot().mode == Mode.AUTO
