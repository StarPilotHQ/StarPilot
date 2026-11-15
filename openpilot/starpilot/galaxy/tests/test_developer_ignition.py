"""Vehicle placement reuses exact guarded signed Panda firmware choices."""

from dataclasses import replace
import json
import pytest
from openpilot.starpilot.galaxy.developer_settings import DeveloperSettingsOwner
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway, SettingsChanged
from openpilot.starpilot.galaxy.tests.test_panda_wake_settings import setup as setup, vehicle
from openpilot.starpilot.panda_wake import FirmwareChoice, snapshot, resolve, startup_choice
from openpilot.starpilot.schema_cache import put_cache
from openpilot.starpilot.ui.feature_settings_state import row_request
from openpilot.starpilot.vehicle_selection import encode


def developer(params, state, owner):
  return DeveloperSettingsOwner(params, lambda: state.parked)


def ignition_row(params, state, owner):
  return next(row for row in owner.panda_wake.rows(state.parked) if row.key == 'IgnoreIgnitionLine')


def test_absent_choice_is_off_and_does_not_write_or_force_ignition(setup):
  params, state, owner, _ = setup
  row = ignition_row(params, state, owner)
  assert row.available and row.value == row.default_value == 'Off' and row.source is None
  assert 'restart' in row.reason.lower()
  assert resolve(state.cp, snapshot(params)) == FirmwareChoice()
  assert not params.get_bool('IgnoreIgnitionLine')


def test_explicit_on_off_is_existing_source_bound_panda_transaction(setup):
  params, state, owner, _ = setup
  for value in ('On', 'Off'):
    row = ignition_row(params, state, owner)
    request = replace(row_request(row, value), confirmation=True, vehicle_fingerprint=state.cp.carFingerprint)
    assert owner.apply(request)
    assert params.get_bool('IgnoreIgnitionLine') == (value == 'On')
    assert resolve(state.cp, snapshot(params)) == FirmwareChoice(can_only=value == 'On')
    assert not owner.apply(request)


@pytest.mark.parametrize('revocation', ('onroad', 'unconfirmed', 'firmware', 'selection', 'dependencies'))
def test_vehicle_ignition_preserves_existing_final_commit_guards(setup, revocation):
  params, state, owner, firmware = setup
  request = replace(row_request(ignition_row(params, state, owner), 'On'), confirmation=True, vehicle_fingerprint=state.cp.carFingerprint)
  if revocation == 'onroad':
    state.parked = False
  elif revocation == 'unconfirmed':
    request = replace(request, confirmation=False)
  elif revocation == 'firmware':
    (firmware / 'panda_h7_can_ignition_only.bin.signed').unlink()
  elif revocation == 'selection':
    params.put('VehicleSelection', json.loads(encode(None)), block=True)
  else:
    params.put_bool('RemoteStartBootsComma', True, block=True)
  assert not owner.apply(request)
  assert not params.get_bool('IgnoreIgnitionLine')


@pytest.mark.parametrize('brand', ('hyundai', 'tesla'))
def test_vehicle_does_not_invent_unsupported_can_ignition_vehicle_contract(setup, brand):
  params, state, owner, _ = setup
  state.cp = vehicle(brand)
  params.put('VehicleSelection', json.loads(encode(state.cp.carFingerprint)), block=True)
  assert all(row.key != 'IgnoreIgnitionLine' for row in owner.panda_wake.rows(True))


def test_actual_gateway_vehicle_confirm_and_no_developer_duplicate(setup, monkeypatch):
  params, state, _, firmware = setup
  from openpilot.starpilot.ui.panda_wake_feature import PandaWakeFeature

  original = PandaWakeFeature.__init__

  def init(feature, owner, *, galaxy):
    original(feature, owner, galaxy=galaxy, firmware_directory=firmware, device_type=lambda: 'tizi')

  monkeypatch.setattr(PandaWakeFeature, '__init__', init)

  class Context:
    def sample(self):
      return AuthorityContext(state.parked, state.cp, state.cp.as_reader().as_builder().to_bytes())

  gateway = SettingsGateway(params, Context(), clock=lambda: 100)
  page = gateway.page('vehicle', 'session', b'generation')
  index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Use CAN Ignition Only')
  assert index == next(i for i, row in enumerate(page['rows']) if row['label'] == 'Remote Start Boots Comma') + 1
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
  assert 'Panda firmware' in intent['question'] and 'restart' in intent['question']
  with pytest.raises(SettingsChanged):
    gateway.confirm(intent['intent'], 'session', b'generation', session_valid=lambda: False)
  assert not params.get_bool('IgnoreIgnitionLine')
  page = gateway.page('vehicle', 'session', b'generation')
  index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Use CAN Ignition Only')
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
  assert gateway.confirm(intent['intent'], 'session', b'generation')
  assert params.get_bool('IgnoreIgnitionLine')
  assert all(row['label'] != 'Use CAN Ignition Only' for row in gateway.page('developer', 'session', b'generation')['rows'])


def test_startup_uses_exact_registered_cache_for_manual_and_auto(setup):
  params, state, _, _ = setup
  params.put_bool('IgnoreIgnitionLine', True, block=True)
  put_cache(params, 'CarParamsPersistent', state.cp.as_reader(), block=True)
  assert startup_choice(params) == FirmwareChoice(can_only=True)
  params.put('VehicleSelection', json.loads(encode(None)), block=True)
  assert startup_choice(params) == FirmwareChoice(can_only=True)
  assert params.get_bool('IgnoreIgnitionLine')


@pytest.mark.parametrize('device', ('tici', 'tizi', 'mici'))
@pytest.mark.parametrize('auto', (False, True))
@pytest.mark.parametrize('alpha', (False, True))
def test_2018_bolt_vehicle_ignition_auto_and_device_support(setup, monkeypatch, device, auto, alpha):
  from opendbc.car.gm.interface import CarInterface
  from opendbc.car.gm.values import CAR
  from openpilot.starpilot.ui.panda_wake_feature import PandaWakeFeature
  params, state, _, firmware = setup
  state.cp = CarInterface.get_non_essential_params(CAR.CHEVROLET_BOLT_CC_2018_2021)
  state.cp.openpilotLongitudinalControl = alpha
  params.put('VehicleSelection', json.loads(encode(None if auto else state.cp.carFingerprint)), block=True)
  put_cache(params, 'CarParamsPersistent', state.cp.as_reader(), block=True)
  original = PandaWakeFeature.__init__
  def init(feature, owner, *, galaxy):
    original(feature, owner, galaxy=galaxy, firmware_directory=firmware, device_type=lambda: device)
  monkeypatch.setattr(PandaWakeFeature, '__init__', init)
  class Context:
    def sample(self):
      return AuthorityContext(state.parked, state.cp, state.cp.as_reader().as_builder().to_bytes())
  gateway = SettingsGateway(params, Context(), clock=lambda: 100)
  page = gateway.page('vehicle', 'session', b'generation')
  index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Use CAN Ignition Only')
  assert index == next(i for i, row in enumerate(page['rows']) if row['label'] == 'Remote Start Boots Comma') + 1
  assert page['rows'][index]['available']
  assert all(row['label'] != 'Use CAN Ignition Only' for row in gateway.page('developer', 'session', b'generation')['rows'])
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
  assert 'restart' in intent['question'] and 'Panda firmware' in intent['question']
  assert gateway.confirm(intent['intent'], 'session', b'generation')
  assert startup_choice(params) == FirmwareChoice(can_only=True)


def test_developer_no_longer_accepts_ignition_requests(setup):
  params, state, owner, _ = setup
  request = replace(row_request(ignition_row(params, state, owner), 'On'), confirmation=True,
                    vehicle_fingerprint=state.cp.carFingerprint)
  assert not developer(params, state, owner).apply(request)
  assert not params.get_bool('IgnoreIgnitionLine')
