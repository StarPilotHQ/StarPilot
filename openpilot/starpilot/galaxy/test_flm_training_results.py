"""Training history is observational and never restores an applied profile."""
import json
import os

import pytest

from openpilot.starpilot.flm.training_report import READ_MAX_BYTES
from openpilot.starpilot.galaxy.flm_training_results import FlmTrainingResults

NOW = 20_000_000_000


def report():
  return {'version': 1, 'observedMonoTime': NOW, 'vehicle': 'CHEVROLET_VOLT_ASCM', 'session': 'a' * 32,
          'current': {'enabled': True, 'status': 'collecting', 'reason': 'independent_evidence',
                      'progress': {'samples': 52, 'blocks': 4, 'requiredBlocks': 16},
                      'fit': {'status': 'rejected', 'reason': 'actual_controller_replay', 'trainWindows': 8, 'heldoutWindows': 4},
                      'application': {'applied': True, 'reason': 'qualified_profile'},
                      'details': {'cohort': 'high.left.steady', 'rawPoints': 52}},
          'history': [{'time': '12.5', 'status': 'rejected', 'reason': 'actual_controller_replay', 'applied': True}]}


def write(path, value):
  path.write_text(json.dumps(value))
  path.chmod(0o600)


def test_actual_counters_fit_reason_and_history_are_read_without_mutation(tmp_path):
  path = tmp_path / 'training.json'
  data = report()
  write(path, data)
  before = path.read_bytes(), path.stat().st_mtime_ns
  owner = FlmTrainingResults(path, clock=lambda: NOW + 100_000_000)
  actual = owner.snapshot()
  assert set(actual) == {'version', 'current', 'history'}
  assert actual['current']['fresh'] and actual['current']['ageSeconds'] == .1
  assert actual['current']['progress'] == data['current']['progress']
  assert actual['current']['fit'] == data['current']['fit']
  assert actual['current']['details'] == data['current']['details']
  assert actual['current']['application'] == data['current']['application']
  assert actual['history'] == data['history']
  assert before == (path.read_bytes(), path.stat().st_mtime_ns)


def test_stale_ram_remains_visible_offroad_without_claiming_current_application(tmp_path):
  path = tmp_path / 'training.json'
  data = report()
  write(path, data)
  actual = FlmTrainingResults(path, clock=lambda: NOW + 3_000_000_000).snapshot()
  assert not actual['current']['fresh'] and actual['current']['ageSeconds'] == 3.
  assert actual['current']['progress']['blocks'] == 4
  assert actual['current']['application'] == {'applied': False, 'reason': 'report_stale'}
  assert actual['history'][0]['applied']  # Explicitly historical, never a current command claim.


def test_null_fit_application_and_unknown_enabled_are_truthful(tmp_path):
  path = tmp_path / 'training.json'
  data = report()
  data['current'].update({'fit': None, 'application': None, 'enabled': None})
  write(path, data)
  actual = FlmTrainingResults(path, clock=lambda: NOW).snapshot()
  assert actual['current']['fit'] is actual['current']['application'] is actual['current']['enabled'] is None


@pytest.mark.parametrize('field,value', (
  ('progress', {'samples': True, 'blocks': 4, 'requiredBlocks': 16}),
  ('progress', {'samples': 52, 'blocks': -1, 'requiredBlocks': 16}),
  ('application', {'applied': 'yes', 'reason': 'qualified'}),
  ('fit', {'status': 'rejected', 'reason': 'actual_controller_replay', 'trainWindows': -1, 'heldoutWindows': 2}),
  ('details', []), ('enabled', 1), ('status', None),
))
def test_malformed_progress_or_application_never_supplies_telemetry(tmp_path, field, value):
  path = tmp_path / 'training.json'
  data = report()
  data['current'][field] = value
  write(path, data)
  assert FlmTrainingResults(path, clock=lambda: NOW).snapshot() == {'version': 1, 'current': None, 'history': []}


@pytest.mark.parametrize('raw', (
  b'{}', b'[]', b'{', b'{"version":1,"version":1,"observedMonoTime":1}',
  b'{"version":true,"observedMonoTime":1}',
  b'{"version":1,"observedMonoTime":1,"value":NaN}',
  b'{"version":1,"observedMonoTime":20000000001}', b'x' * (READ_MAX_BYTES + 1),
))
def test_bounded_report_validation_fails_closed(tmp_path, raw):
  path = tmp_path / 'training.json'
  path.write_bytes(raw)
  assert FlmTrainingResults(path, clock=lambda: NOW).snapshot() == {'version': 1, 'current': None, 'history': []}


def test_history_bound_is_not_silently_truncated(tmp_path):
  path = tmp_path / 'training.json'
  data = report()
  data['history'] *= 21
  write(path, data)
  assert FlmTrainingResults(path, clock=lambda: NOW).snapshot()['current'] is None


def test_missing_symlink_fifo_and_writable_report_never_block_or_supply_data(tmp_path):
  path = tmp_path / 'training.json'
  owner = FlmTrainingResults(path, clock=lambda: NOW)
  assert owner.snapshot()['current'] is None
  source = tmp_path / 'source.json'
  write(source, report())
  path.symlink_to(source)
  assert owner.snapshot()['current'] is None
  path.unlink()
  os.mkfifo(path)
  assert owner.snapshot()['current'] is None
  path.unlink()
  write(path, report())
  path.chmod(0o666)
  assert owner.snapshot()['current'] is None


def test_unknown_fit_window_counts_are_not_reported_as_zero(tmp_path):
  path = tmp_path / 'training.json'
  data = report()
  data['current']['fit'].update(trainWindows=None, heldoutWindows=None)
  write(path, data)
  assert FlmTrainingResults(path, clock=lambda: NOW).snapshot()['current']['fit']['trainWindows'] is None


def test_invalid_namespace_only_disables_report(monkeypatch):
  monkeypatch.setenv('OPENPILOT_PREFIX', '../not-a-report-namespace')
  assert FlmTrainingResults(clock=lambda: NOW).snapshot() == {'version': 1, 'current': None, 'history': []}


class ChoiceParams:
  def __init__(self, root):
    self.root = root

  def get_param_path(self, key):
    return str(self.root / key)

  def put(self, key, value, **_kwargs):
    (self.root / key).write_bytes(value)


def choice_fixture(tmp_path, *, parked=True, enabled=True):
  from opendbc.car.structs import car
  from openpilot.starpilot.schema_cache import put_cache
  from openpilot.starpilot.lateral.learning_preferences import KEY, replace_preference
  params = ChoiceParams(tmp_path)
  cp = car.CarParams.new_message(brand='hyundai', carFingerprint='HYUNDAI_IONIQ_6')
  params.put('IsOffroad', b'1' if parked else b'0')
  params.put(KEY, replace_preference(None, cp, enabled))
  if parked:
    put_cache(params, 'CarParamsPersistent', cp)
  else:
    params.put('CarParams', cp.to_bytes())
  return params, cp


@pytest.mark.parametrize('parked', (True, False))
@pytest.mark.parametrize('enabled', (True, False))
def test_choice_is_independent_of_a_missing_or_stopped_learner(tmp_path, parked, enabled):
  params, _cp = choice_fixture(tmp_path, parked=parked, enabled=enabled)
  before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
  snapshot = FlmTrainingResults(tmp_path / 'missing-report', params=params).snapshot()
  assert snapshot['current'] is None
  assert snapshot['preference'] == {'version': 1, 'valid': True, 'enabled': enabled, 'parked': parked,
                                    'vehicle': 'HYUNDAI_IONIQ_6'}
  assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}


def test_scoped_on_is_not_overridden_by_legacy_off(tmp_path):
  params, _cp = choice_fixture(tmp_path)
  params.put('ForceAutoTuneOff', b'1')
  assert FlmTrainingResults(tmp_path / 'missing', params=params).snapshot()['preference']['enabled'] is True


@pytest.mark.parametrize('key,raw', (('IsOffroad', b'wat'), ('CarParamsPersistent', b'bad'),
  ('SteeringLearningPreferences', b'{'), ('CarParamsPersistent', b'x' * (1024 * 1024 + 1))))
def test_unreadable_choice_is_unknown_not_off(tmp_path, key, raw):
  params, _cp = choice_fixture(tmp_path)
  params.put(key, raw)
  actual = FlmTrainingResults(tmp_path / 'missing', params=params).snapshot()['preference']
  assert actual['valid'] is False and actual['enabled'] is None


def test_driving_without_current_vehicle_does_not_borrow_parked_identity(tmp_path):
  params, _cp = choice_fixture(tmp_path)
  params.put('IsOffroad', b'0')
  actual = FlmTrainingResults(tmp_path / 'missing', params=params).snapshot()['preference']
  assert actual['valid'] is False and actual['enabled'] is None and actual['parked'] is False


@pytest.mark.parametrize('key', ('IsOffroad', 'SteeringLearningPreferences', 'CarParamsPersistent'))
def test_mode_or_choice_change_during_read_is_unknown(tmp_path, monkeypatch, key):
  from openpilot.starpilot import saved_source
  params, _cp = choice_fixture(tmp_path)
  original = saved_source.read_saved
  calls = 0

  def changed(owner, name, limit):
    nonlocal calls
    if name == key:
      calls += 1
      if calls == 2:
        return b'changed-during-read', True
    return original(owner, name, limit)

  monkeypatch.setattr(saved_source, 'read_saved', changed)
  actual = FlmTrainingResults(tmp_path / 'missing', params=params).snapshot()['preference']
  assert actual['valid'] is False and actual['enabled'] is None
