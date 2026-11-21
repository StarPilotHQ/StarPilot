from types import SimpleNamespace as NS
import pytest
from typing import Any
from openpilot.starpilot.drive_state.evidence import PhysicalSource


class Messages:
  def __init__(self):
    self.values: dict[str, Any] = {
      'pandaStates': [NS(pandaType='uno', controlsAllowed=False, ignitionLine=False, ignitionCan=False, safetyModel='noOutput')],
      'carState': NS(canValid=True, canTimeout=False, standstill=True, gearShifter='park'),
      'selfdriveState': NS(enabled=False, active=False),
    }
    keys = self.values
    self.seen = dict.fromkeys(keys, True)
    self.alive = dict.fromkeys(keys, True)
    self.valid = dict.fromkeys(keys, True)
    self.logMonoTime = dict.fromkeys(keys, 2_000_000_000)
    self.logMonoTime['pandaStates'] += 10_000_000_000
    self.recv_time = dict.fromkeys(keys, 2.0)

  def __getitem__(self, key):
    return self.values[key]


@pytest.fixture
def fixture():
  now = [1_000_000_000, 10_000_000_000]
  messages = Messages()
  source = PhysicalSource(messages, mono=lambda: now[0], boot=lambda: sum(now))
  now[0] = 2_100_000_000
  return NS(now=now, sm=messages, source=source)


def test_genuine_ignition_off_needs_all_panda_no_output_not_pipeline_state(fixture):
  f = fixture
  assert f.source.allowed()
  f.sm.values['deviceState'] = NS(started=True)
  assert f.source.allowed()
  f.sm.values['pandaStates'][0].safetyModel = 'toyota'
  assert not f.source.allowed()


@pytest.mark.parametrize('field,value', [('controlsAllowed', True), ('pandaType', 'unknown')])
def test_unknown_or_actuation_authority_never_admits(fixture, field, value):
  setattr(fixture.sm.values['pandaStates'][0], field, value)
  assert not fixture.source.allowed()


def test_live_can_park_disabled_can_admit_with_ignition_on_and_started(fixture):
  f = fixture
  f.sm.values['pandaStates'][0].ignitionLine = True
  f.sm.values['pandaStates'][0].safetyModel = 'toyota'
  f.sm.values['deviceState'] = NS(started=True)
  assert f.source.allowed()
  for message, field, value in [
    ('carState', 'canValid', False),
    ('carState', 'canTimeout', True),
    ('carState', 'standstill', False),
    ('carState', 'gearShifter', 'drive'),
    ('selfdriveState', 'enabled', True),
    ('selfdriveState', 'active', True),
  ]:
    before = getattr(f.sm.values[message], field)
    setattr(f.sm.values[message], field, value)
    assert not f.source.allowed()
    setattr(f.sm.values[message], field, before)


def test_stale_forced_offroad_cannot_supply_park_authority(fixture):
  f = fixture
  f.sm.values['pandaStates'][0].ignitionLine = True
  f.sm.logMonoTime['carState'] = 1_000_000_000
  f.sm.values['deviceState'] = NS(started=False)
  assert not f.source.allowed()


@pytest.mark.parametrize('service', ['pandaStates', 'carState', 'selfdriveState'])
def test_missing_invalid_stale_future_sources_fail_closed(fixture, service):
  f = fixture
  f.sm.values['pandaStates'][0].ignitionLine = True
  for table, value in [(f.sm.valid, False), (f.sm.alive, False), (f.sm.seen, False), (f.sm.logMonoTime, 0), (f.sm.recv_time, 100.0)]:
    before = table[service]
    table[service] = value
    assert not f.source.allowed()
    table[service] = before


def test_resume_and_collector_start_need_post_boundary_evidence(fixture):
  f = fixture
  f.sm.values['pandaStates'][0].ignitionLine = True
  assert f.source.allowed()
  f.now[1] += 20_000_000_000
  assert not f.source.allowed()
  f.sm.logMonoTime['pandaStates'] = sum(f.now)
  f.sm.recv_time['pandaStates'] = f.now[0] / 1e9
  assert not f.source.allowed()
  f.now[0] += 10_000_000
  for key in f.sm.values:
    if key == 'deviceState':
      continue
    f.sm.logMonoTime[key] = sum(f.now) if key == 'pandaStates' else f.now[0]
    f.sm.recv_time[key] = f.now[0] / 1e9
  assert f.source.allowed()
  f.source.close()
  assert not f.source.allowed()


def test_real_capnp_physical_fields_and_fresh_effective_state(fixture):
  from openpilot.cereal import log

  f = fixture
  panda_event = log.Event.new_message()
  pandas = panda_event.init('pandaStates', 1)
  pandas[0].pandaType = 'uno'
  pandas[0].safetyModel = 'noOutput'
  f.sm.values['pandaStates'] = panda_event.as_reader().pandaStates
  assert f.source.allowed()
  device = log.Event.new_message().init('deviceState')
  device.started = True
  f.sm.values['deviceState'] = device.as_reader()
  for table, value in ((f.sm.seen, True), (f.sm.alive, True), (f.sm.valid, True), (f.sm.logMonoTime, 2_000_000_000), (f.sm.recv_time, 2.0)):
    table['deviceState'] = value
  assert f.source.effective() is True
  f.sm.logMonoTime['deviceState'] = 1
  assert f.source.effective() is None
  f.now[1] += 10_000_000_000
  assert f.source.effective() is None


def test_park_disengaged_does_not_confuse_native_permission_with_active_controls(fixture):
  f = fixture
  f.sm.values['pandaStates'][0].ignitionLine = True
  f.sm.values['pandaStates'][0].safetyModel = 'hyundaiCanfd'
  f.sm.values['pandaStates'][0].controlsAllowed = True
  f.sm.values['deviceState'] = NS(started=True)
  assert f.source.allowed()
  f.sm.values['selfdriveState'].active = True
  assert not f.source.allowed()
  f.sm.values['selfdriveState'].active = False
  f.sm.values['carState'].gearShifter = 'drive'
  assert not f.source.allowed()


def offroad_device(f):
  f.sm.values['deviceState'] = NS(started=False)
  for table, value in ((f.sm.seen, True), (f.sm.alive, True), (f.sm.valid, True),
                       (f.sm.logMonoTime, 2_000_000_000), (f.sm.recv_time, 2.0)):
    table['deviceState'] = value


def test_update_forced_offroad_does_not_require_stopped_driving_publishers(fixture):
  f = fixture
  offroad_device(f)
  f.sm.values['pandaStates'][0].ignitionLine = True
  f.sm.values.pop('carState')
  f.sm.values.pop('selfdriveState')
  assert not f.source.allowed()
  assert f.source.update_ready(True)
  assert not f.source.update_ready(False)
  f.sm.values['deviceState'].started = True
  assert not f.source.update_ready(True)


@pytest.mark.parametrize('field,value', [('controlsAllowed', True), ('safetyModel', 'hyundaiCanfd'),
                                         ('pandaType', 'unknown')])
def test_forced_offroad_update_requires_native_no_output(fixture, field, value):
  f = fixture
  offroad_device(f)
  f.sm.values['pandaStates'][0].ignitionLine = True
  f.sm.values.pop('carState')
  setattr(f.sm.values['pandaStates'][0], field, value)
  assert not f.source.update_ready(True)


@pytest.mark.parametrize('service', ['pandaStates', 'deviceState'])
def test_forced_offroad_update_rechecks_freshness(fixture, service):
  f = fixture
  offroad_device(f)
  f.sm.values['pandaStates'][0].ignitionLine = True
  assert f.source.update_ready(True)
  f.sm.logMonoTime[service] = 1
  assert not f.source.update_ready(True)


@pytest.mark.parametrize('ignition', [False, True])
@pytest.mark.parametrize('gear', ['park', 'drive', 'reverse', 'neutral', 'unknown'])
def test_galaxy_requires_fresh_explicit_park_even_with_ignition_off(fixture, ignition, gear):
  f = fixture
  f.sm.values['pandaStates'][0].ignitionLine = ignition
  f.sm.values['carState'].gearShifter = gear
  assert f.source.status(require_park=True)['allowed'] == (gear == 'park')
  f.sm.logMonoTime['carState'] = 1
  assert not f.source.status(require_park=True)['allowed']


def test_actuation_recovery_requires_ignition_off_even_in_park(fixture):
  f = fixture
  assert f.source.status(require_ignition_off=True)['allowed']
  for field in ('ignitionLine', 'ignitionCan'):
    setattr(f.sm.values['pandaStates'][0], field, True)
    assert f.source.allowed()  # Other callers retain normal parked semantics.
    assert not f.source.status(require_ignition_off=True)['allowed']
    setattr(f.sm.values['pandaStates'][0], field, False)
  f.sm.alive['pandaStates'] = False
  assert not f.source.status(require_ignition_off=True)['allowed']
