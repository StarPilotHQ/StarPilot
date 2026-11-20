"""Estimator references stay scoped, truthful and separate from saved edits."""
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest
from opendbc.car.car_helpers import interfaces
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.starpilot.schema_cache import put_cache
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest, row_default
from openpilot.starpilot.ui.learned_steering import learned_references

NOW = 100_000_000_000


class Messages(SimpleNamespace):
  def __getitem__(self, name):
    return self.data[name]


def estimator_messages(cp):
  events = {}
  for service in ('lateralDelay', 'lateralTorqueParameters', 'vehicleParameters'):
    event = log.Event.new_message(valid=True, logMonoTime=NOW - 100_000_000)
    event.init(service)
    events[service] = event
  events['lateralDelay'].lateralDelay = {
    'version': 1, 'status': 'estimated', 'validBlocks': 10, 'lateralDelay': .34,
    'lateralDelayEstimate': .347, 'lateralDelayEstimateStd': .01, 'calPerc': 100}
  events['lateralTorqueParameters'].lateralTorqueParameters = {
    'version': 1, 'valid': True, 'useParams': True, 'totalBucketPoints': 1000,
    'latAccelFactorFiltered': cp.lateralTuning.torque.latAccelFactor * .9,
    'frictionCoefficientFiltered': cp.lateralTuning.torque.friction * 17 / 18, 'calPerc': 100}
  events['vehicleParameters'].vehicleParameters = {
    'valid': True, 'sensorValid': True, 'posenetValid': True, 'steerRatioValid': True, 'steerRatio': cp.steerRatio + .5}
  return events


def setup_sources(tmp_path, *, live=True):
  cp = interfaces['HYUNDAI_IONIQ_6'].get_non_essential_params('HYUNDAI_IONIQ_6')
  params = Params(str(tmp_path))
  events = estimator_messages(cp)
  put_cache(params, 'CarParamsPrevRoute', cp, block=True)
  for key, service in (('LiveDelay', 'lateralDelay'), ('LiveTorqueParameters', 'lateralTorqueParameters'),
                       ('LiveParametersV2', 'vehicleParameters')):
    put_cache(params, key, events[service], block=True)
  data = {service: getattr(event, service) for service, event in events.items()}
  data.update(carParams=cp, deviceState=SimpleNamespace(started=True))
  sm = Messages(data=data, seen=dict.fromkeys(data, True), alive=dict.fromkeys(data, True), valid=dict.fromkeys(data, True),
                logMonoTime=dict.fromkeys(data, NOW - 100_000_000), recv_time=dict.fromkeys(data, (NOW - 50_000_000) / 1e9))
  owner = FeatureSettingsOwner(params, lambda _: True, vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp,
                               learning_messages=lambda: sm if live else None)
  return cp, params, owner, sm, events


def test_live_estimates_do_not_replace_manual_or_default_values(tmp_path):
  cp, params, owner, sm, _ = setup_sources(tmp_path)
  from openpilot.starpilot.lateral.delay_preferences import KEY, replace_delay
  params.put(KEY, json.loads(replace_delay(None, cp, .61)), block=True)
  sm.data['lateralDelay'].lateralDelay = .61  # The applied manual projection retains the unmodified estimate.
  references = learned_references(owner, now_ns=NOW)
  assert references['steering_delay'] == 'Live learned · 0.347 s'
  assert references['torque:factor:value'] == 'Live learned · 2.700'
  assert references['torque:friction:value'] == 'Live learned · 0.085'
  assert references['torque:ratio:value'] == f'Live learned · {cp.steerRatio + .5:.2f}'
  row = next(row for row in owner.snapshot('torque/developer', parked=True, system_long=False,
             lateral_context=True, metric=False).rows if row.key == 'steering_delay')
  assert row.value == '0.61' and row.default_value == 'Automatic'
  assert row_default(row) is not None


@pytest.mark.parametrize('condition', ['offroad', 'old_stamp', 'old_receipt', 'invalid', 'dead', 'future', 'wrong_live_car'])
def test_unusable_live_sources_fall_back_to_explicit_last_learned(tmp_path, condition):
  cp, _, owner, sm, _ = setup_sources(tmp_path)
  if condition == 'offroad':
    sm.data['deviceState'].started = False
  elif condition == 'wrong_live_car':
    other = cp.as_reader().as_builder()
    other.carVin = 'OTHER_CAR'
    sm.data['carParams'] = other
  elif condition == 'old_stamp':
    sm.logMonoTime['lateralDelay'] = NOW - 3_000_000_000
  elif condition == 'old_receipt':
    sm.recv_time['lateralDelay'] = (NOW - 3_000_000_000) / 1e9
  elif condition == 'future':
    sm.logMonoTime['lateralDelay'] = NOW + 1
  else:
    getattr(sm, 'valid' if condition == 'invalid' else 'alive')['lateralDelay'] = False
  assert learned_references(owner, now_ns=NOW)['steering_delay'] == 'Last learned · 0.347 s'


@pytest.mark.parametrize('change', ['vehicle', 'controller', 'tune', 'delay', 'ratio', 'vin'])
def test_persisted_values_require_matching_vehicle_and_controller_basis(tmp_path, change):
  cp, _, owner, _, _ = setup_sources(tmp_path, live=False)
  if change == 'vehicle':
    cp.carFingerprint = 'KIA_EV6'
  elif change == 'controller':
    cp.lateralTuning.init('pid')
  elif change == 'tune':
    cp.lateralTuning.torque.latAccelFactor += .1
  elif change == 'delay':
    cp.steerActuatorDelay += .1
  elif change == 'ratio':
    cp.steerRatio += .1
  else:
    cp.carVin = 'OTHER_CAR'
  assert learned_references(owner, now_ns=NOW) == {}


@pytest.mark.parametrize('condition', ['unestimated', 'invalid', 'version', 'blocks', 'std', 'nan'])
def test_default_or_invalid_delay_is_not_labelled_learned(tmp_path, condition):
  _, params, owner, _, events = setup_sources(tmp_path, live=False)
  message = events['lateralDelay'].lateralDelay
  if condition in ('unestimated', 'invalid'):
    message.status = condition
  else:
    setattr(message, {'version': 'version', 'blocks': 'validBlocks', 'std': 'lateralDelayEstimateStd',
                     'nan': 'lateralDelayEstimate'}[condition], {'version': 2, 'blocks': 0, 'std': .2, 'nan': float('nan')}[condition])
  put_cache(params, 'LiveDelay', events['lateralDelay'], block=True)
  references = learned_references(owner, now_ns=NOW)
  assert 'steering_delay' not in references
  assert references['torque:factor:value'].startswith('Last learned')


def test_one_broken_cache_does_not_hide_other_estimates_or_modify_data(tmp_path):
  _, params, owner, _, _ = setup_sources(tmp_path, live=False)
  params.put('LiveDelay', b'broken cache', block=True)
  before = params.get('LiveDelay')
  references = learned_references(owner, now_ns=NOW)
  assert 'steering_delay' not in references
  assert references['torque:friction:value'] == 'Last learned · 0.085'
  assert params.get('LiveDelay') == before


def test_reference_refresh_is_not_a_setting_change_or_write_dependency(tmp_path):
  _, _, owner, _, _ = setup_sources(tmp_path, live=False)
  row = next(row for row in owner.snapshot('torque/developer', parked=True, system_long=False,
             lateral_context=True, metric=False).rows if row.key == 'steering_delay')
  refreshed = replace(row, learned_value='Live learned · 0.355 s')
  assert row == refreshed and repr(row) == repr(refreshed)
  request = FeatureSettingsRequest(row.key, row.source, '.62', vehicle_fingerprint=row.vehicle_fingerprint,
                                   capability=row.capability, dependencies=row.dependencies)
  assert owner.apply(request)


def test_fresh_collecting_progress_is_shown_without_inventing_a_learned_value(tmp_path):
  _, params, owner, sm, _ = setup_sources(tmp_path)
  params.remove('LiveDelay')
  params.remove('LiveTorqueParameters')
  sm.data['lateralTorqueParameters'].valid = False
  sm.data['lateralTorqueParameters'].calPerc = 14
  sm.data['lateralDelay'].status = 'unestimated'
  sm.data['lateralDelay'].calPerc = 22
  refs = learned_references(owner, now_ns=NOW)
  assert refs['torque:factor:value'] == refs['torque:friction:value'] == 'Learning · 14%'
  assert refs['steering_delay'] == 'Learning · 22%'


def test_current_progress_is_appended_to_historical_values_only_when_fresh(tmp_path):
  _, _, owner, sm, _ = setup_sources(tmp_path)
  sm.data['lateralTorqueParameters'].valid = False
  sm.data['lateralTorqueParameters'].calPerc = 14
  sm.data['lateralDelay'].status = 'unestimated'
  sm.data['lateralDelay'].calPerc = 22
  refs = learned_references(owner, now_ns=NOW)
  assert refs['torque:factor:value'] == 'Last learned · 2.700 · 14%'
  assert refs['torque:friction:value'] == 'Last learned · 0.085 · 14%'
  assert refs['steering_delay'] == 'Last learned · 0.347 s · 22%'
  sm.logMonoTime['lateralDelay'] = NOW - 3_000_000_000
  assert learned_references(owner, now_ns=NOW)['steering_delay'] == 'Last learned · 0.347 s'


@pytest.mark.parametrize('percent', [0, 50, 99])
def test_partial_live_estimate_keeps_its_value_and_adds_progress(tmp_path, percent):
  _, _, owner, sm, _ = setup_sources(tmp_path)
  sm.data['lateralTorqueParameters'].calPerc = percent
  sm.data['lateralDelay'].calPerc = percent
  refs = learned_references(owner, now_ns=NOW)
  assert refs['torque:factor:value'] == f'Live learned · 2.700 · {percent}%'
  assert refs['torque:friction:value'] == f'Live learned · 0.085 · {percent}%'
  assert refs['steering_delay'] == f'Live learned · 0.347 s · {percent}%'


@pytest.mark.parametrize('condition', ['disabled', 'bad_percent', 'wrong_version', 'wrong_car', 'offroad'])
def test_progress_requires_current_qualified_estimator_identity(tmp_path, condition):
  cp, _, owner, sm, _ = setup_sources(tmp_path)
  torque = sm.data['lateralTorqueParameters']
  torque.calPerc = 14
  if condition == 'disabled':
    torque.useParams = False
  elif condition == 'bad_percent':
    torque.calPerc = 101
  elif condition == 'wrong_version':
    torque.version = 2
  elif condition == 'wrong_car':
    other = cp.as_reader().as_builder()
    other.carVin = 'OTHER'
    sm.data['carParams'] = other
  else:
    sm.data['deviceState'].started = False
  assert '14%' not in learned_references(owner, now_ns=NOW).get('torque:factor:value', '')


def test_device_rows_and_galaxy_projection_share_current_progress(tmp_path, monkeypatch):
  from openpilot.starpilot.galaxy.settings import _projection
  _, _, owner, sm, _ = setup_sources(tmp_path)
  sm.data['lateralTorqueParameters'].valid = False
  sm.data['lateralTorqueParameters'].calPerc = 14
  sm.data['lateralDelay'].status = 'unestimated'
  sm.data['lateralDelay'].calPerc = 22
  monkeypatch.setattr('openpilot.starpilot.ui.learned_steering.time.monotonic_ns', lambda: NOW)
  rows = owner.snapshot('torque/developer', parked=True, system_long=False, lateral_context=True, metric=False).rows
  expected = {'steering_delay': '22%', 'torque:factor:value': '14%', 'torque:friction:value': '14%'}
  found = {row.key: row for row in rows if row.key in expected}
  assert found.keys() == expected.keys()
  for key, suffix in expected.items():
    assert found[key].learned_value.endswith(suffix)
    assert _projection(found[key])['learnedValue'] == found[key].learned_value
