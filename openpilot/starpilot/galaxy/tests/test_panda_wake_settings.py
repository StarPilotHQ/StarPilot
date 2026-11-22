import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.gm.interface import CarInterface as GMInterface
from opendbc.car.gm.values import CAR as GM
from opendbc.car.hyundai.interface import CarInterface as HKGInterface
from opendbc.car.hyundai.values import CAR as HKG, HyundaiFlags
from opendbc.car.tesla.interface import CarInterface as TeslaInterface
from opendbc.car.tesla.values import CAR as TESLA
from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged, SettingsGateway
from openpilot.starpilot.panda_wake import KEYS, FirmwareChoice, firmware_path, resolve, snapshot, supported_keys
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_request
from openpilot.starpilot.vehicle_selection import encode
from openpilot.starpilot.saved_source import read_saved


def vehicle(brand):
  interface, platform = {
    'gm': (GMInterface, GM.CHEVROLET_BOLT_EUV),
    'hyundai': (HKGInterface, HKG.KIA_EV6),
    'tesla': (TeslaInterface, TESLA.TESLA_MODEL_3),
  }[brand]
  fingerprint = gen_empty_fingerprint()
  car_fw = []
  if brand == 'hyundai':
    # First-generation EV6 captured-ADRV factory topology with the ADAS ECU.
    fingerprint[6][0x50], fingerprint[5][0x1cf] = 16, 8
    car_fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.adas)]
  return interface.get_params(platform, fingerprint, car_fw, True, False, False)


@pytest.fixture
def setup(tmp_path):
  params = Params(str(tmp_path / 'params'))
  state = SimpleNamespace(cp=vehicle('gm'), parked=True, galaxy=True)
  owner = FeatureSettingsOwner(params, lambda group: state.parked and group == 'parked_preferences',
                               vehicle_fingerprint=lambda: state.cp.carFingerprint if state.cp else None,
                               vehicle_params=lambda: state.cp, galaxy=lambda: state.galaxy)
  firmware = tmp_path / 'firmware'
  firmware.mkdir()
  for choice in (FirmwareChoice(), FirmwareChoice(gm=True), FirmwareChoice(hkg=True),
                 FirmwareChoice(can_only=True), FirmwareChoice(gm=True, can_only=True), FirmwareChoice(tesla=True)):
    for app in ('panda.bin.signed', 'panda_h7.bin.signed'):
      (firmware / choice.filename(app)).write_bytes(b'image')
  owner.panda_wake.firmware_directory = firmware
  owner.panda_wake.device_type = lambda: 'tizi'
  params.put('VehicleSelection', json.loads(encode(state.cp.carFingerprint)), block=True)
  return params, state, owner, firmware


@pytest.mark.parametrize('brand,keys', [
  ('gm', ('RemoteStartBootsComma', 'IgnoreIgnitionLine')),
  ('hyundai', ('HKGRemoteStartBootsComma',)),
  ('tesla', ('TeslaWakeOnCAN',)),
])
def test_registered_vehicle_identity_owns_wake_choices(setup, brand, keys):
  params, state, owner, _ = setup
  state.cp = vehicle(brand)
  params.put('VehicleSelection', json.loads(encode(state.cp.carFingerprint)), block=True)
  assert supported_keys(state.cp) == keys
  rows = owner.panda_wake.rows(True)
  assert tuple(row.key for row in rows) == keys
  assert all(row.available and row.value == 'Off' and row.default_value == 'Off' for row in rows)
  assert all(read_saved(params, key, 8)[0] is None for key in KEYS)


@pytest.mark.parametrize('mutation', [
  lambda state: setattr(state, 'cp', None),
  lambda state: setattr(state.cp, 'brand', 'tesla'),
  lambda state: setattr(state.cp, 'carFingerprint', 'CHEVROLET UNKNOWN'),
  lambda state: setattr(state.cp, 'notCar', True),
  lambda state: setattr(state, 'galaxy', False),
])
def test_unknown_or_native_ui_context_is_denied(setup, mutation):
  _, state, owner, _ = setup
  request = replace(row_request(owner.panda_wake.rows(True)[0], 'On'), confirmation=True,
                    vehicle_fingerprint=state.cp.carFingerprint)
  mutation(state)
  assert not owner.panda_wake.rows(True)
  assert not owner.apply(request)


def test_hkg_requires_canfd_but_not_system_long(setup):
  params, state, owner, _ = setup
  state.cp = vehicle('hyundai')
  params.put('VehicleSelection', json.loads(encode(state.cp.carFingerprint)), block=True)
  assert owner.panda_wake.rows(True)
  state.cp.openpilotLongitudinalControl = False
  assert owner.panda_wake.rows(True)
  state.cp.openpilotLongitudinalControl = True
  state.cp.flags &= ~int(HyundaiFlags.CANFD)
  assert not owner.panda_wake.rows(True)


def test_saved_choice_survives_other_vehicle_and_unsupported_cp(setup):
  params, state, _, _ = setup
  params.put_bool('RemoteStartBootsComma', True, block=True)
  assert resolve(state.cp, snapshot(params)) == FirmwareChoice(gm=True)
  state.cp = vehicle('tesla')
  assert resolve(state.cp, snapshot(params)) == FirmwareChoice()
  assert read_saved(params, 'RemoteStartBootsComma', 8)[0] == b'1'
  params.put_bool('TeslaWakeOnCAN', True, block=True)
  assert resolve(state.cp, snapshot(params)) == FirmwareChoice(tesla=True)
  assert read_saved(params, 'RemoteStartBootsComma', 8)[0] == b'1'


@pytest.mark.parametrize('raw', [b'true', b'1 ', b'2', b'\xff', b'1' * 9])
def test_corruption_is_preserved_and_cannot_enable_wake(setup, raw):
  params, state, owner, _ = setup
  Path(params.get_param_path('RemoteStartBootsComma')).write_bytes(raw)
  assert not any(row.available for row in owner.panda_wake.rows(True))
  assert resolve(state.cp, snapshot(params)) == FirmwareChoice()
  assert Path(params.get_param_path('RemoteStartBootsComma')).read_bytes() == raw


@pytest.mark.parametrize('revocation', ['parked', 'dependencies', 'selection', 'firmware'])
def test_final_commit_guard_rejects_changed_authority(setup, revocation):
  params, state, owner, firmware = setup
  row = owner.panda_wake.rows(True)[0]
  request = replace(row_request(row, 'On'), confirmation=True, vehicle_fingerprint=state.cp.carFingerprint)
  if revocation == 'parked':
    state.parked = False
  elif revocation == 'dependencies':
    params.put_bool('IgnoreIgnitionLine', True, block=True)
  elif revocation == 'selection':
    params.put('VehicleSelection', json.loads(encode(str(TESLA.TESLA_MODEL_3))), block=True)
  else:
    (firmware / 'panda_h7_remote.bin.signed').unlink()
  assert not owner.apply(request)
  assert read_saved(params, 'RemoteStartBootsComma', 8)[0] is None


def test_off_on_and_explicit_default_preserve_other_keys(setup):
  params, state, owner, _ = setup
  for value in ('On', 'Off'):
    row = owner.panda_wake.rows(True)[0]
    request = replace(row_request(row, value), confirmation=True, vehicle_fingerprint=state.cp.carFingerprint)
    assert owner.apply(request)
    assert read_saved(params, 'RemoteStartBootsComma', 8)[0] == (b'1' if value == 'On' else b'0')
    assert not owner.apply(request)
    assert read_saved(params, 'IgnoreIgnitionLine', 8)[0] is None


def test_missing_alternate_image_never_falls_back(setup):
  _, _, _, firmware = setup
  (firmware / 'panda_h7_remote.bin.signed').unlink()
  with pytest.raises(FileNotFoundError):
    firmware_path(firmware, 'panda_h7.bin.signed', FirmwareChoice(gm=True))
  with pytest.raises(ValueError):
    firmware_path(firmware, 'body_h7.bin.signed', FirmwareChoice(gm=True))
  assert firmware_path(firmware, 'panda_h7.bin.signed', FirmwareChoice()).endswith('panda_h7.bin.signed')


def test_gateway_confirm_binds_vehicle_and_session(setup, monkeypatch):
  params, state, _, firmware = setup
  from openpilot.starpilot.ui.panda_wake_feature import PandaWakeFeature
  original_init = PandaWakeFeature.__init__

  def init(feature, owner, *, galaxy):
    original_init(feature, owner, galaxy=galaxy, firmware_directory=firmware, device_type=lambda: 'tizi')

  monkeypatch.setattr(PandaWakeFeature, '__init__', init)

  class Context:
    def sample(self) -> AuthorityContext:
      return AuthorityContext(state.parked, state.cp, state.cp.as_reader().as_builder().to_bytes())

  context = Context()
  gateway = SettingsGateway(params, context, clock=lambda: 100)
  page = gateway.page('vehicle', 'session', b'generation')
  index = next(i for i, item in enumerate(page['rows']) if item['label'] == 'Remote Start Boots Comma')
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
  assert 'Panda firmware' in intent['question']
  with pytest.raises(SettingsChanged):
    gateway.confirm(intent['intent'], 'session', b'generation', session_valid=lambda: False)
  assert read_saved(params, 'RemoteStartBootsComma', 8)[0] is None
  page = gateway.page('vehicle', 'session', b'generation')
  index = next(i for i, item in enumerate(page['rows']) if item['label'] == 'Remote Start Boots Comma')
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
  assert gateway.confirm(intent['intent'], 'session', b'generation')
  assert read_saved(params, 'RemoteStartBootsComma', 8)[0] == b'1'


@pytest.mark.parametrize('device', ['unknown', 'pc'])
def test_unknown_hardware_admission_denied(setup, device):
  params, state, owner, _ = setup
  request = replace(row_request(owner.panda_wake.rows(True)[0], 'On'), confirmation=True,
                    vehicle_fingerprint=state.cp.carFingerprint)
  owner.panda_wake.device_type = lambda: device
  assert not owner.panda_wake.rows(True)
  assert not owner.apply(request)
  assert read_saved(params, 'RemoteStartBootsComma', 8)[0] is None


def test_auto_selection_uses_recognized_vehicle_wake_choice(setup):
  params, state, owner, _ = setup
  params.put_bool('RemoteStartBootsComma', True, block=True)
  params.put('VehicleSelection', json.loads(encode(None)), block=True)
  assert owner.panda_wake.rows(True)
  assert read_saved(params, 'RemoteStartBootsComma', 8)[0] == b'1'


@pytest.mark.parametrize('hardware', ['DOS', 'TRES', 'RED_PANDA', 'CUATRO'])
@pytest.mark.parametrize('choice', [FirmwareChoice(gm=True), FirmwareChoice(hkg=True), FirmwareChoice(tesla=True)])
@pytest.mark.parametrize('protocol_ok', [False, True])
def test_supported_hardware_uses_existing_alternate_firmware(setup, monkeypatch, hardware, choice, protocol_ok):
  params, _, _, firmware = setup
  from openpilot.selfdrive.pandad import pandad
  from panda import Panda
  params.put_bool('RemoteStartBootsComma', True, block=True)
  observed = []

  class FakePanda:
    F4_DEVICES = Panda.F4_DEVICES
    H7_DEVICES = Panda.H7_DEVICES
    HW_TYPE_DOS = Panda.HW_TYPE_DOS
    HW_TYPE_TRES = Panda.HW_TYPE_TRES
    HW_TYPE_RED_PANDA = Panda.HW_TYPE_RED_PANDA
    HW_TYPE_CUATRO = Panda.HW_TYPE_CUATRO
    bootstub = False

    def __init__(self, serial):
      assert serial == 'fixture'

    def get_type(self):
      return getattr(Panda, 'HW_TYPE_' + hardware)

    def get_mcu_type(self):
      return Panda.get_mcu_type(cast(Panda, self))

    @staticmethod
    def get_signature_from_firmware(path):
      observed.append(Path(path).name)
      return b'image'

    def is_internal(self):
      return True

    def get_version(self):
      return 'fixture'

    def get_signature(self):
      return b'image'

    def get_packets_versions(self):
      return pandad.PACKET_VERSIONS if protocol_ok else (0, 0)

    def close(self):
      pass

  monkeypatch.setattr(pandad, 'Panda', FakePanda)
  monkeypatch.setattr(pandad, 'FW_PATH', firmware)
  app = 'panda.bin.signed' if getattr(Panda, 'HW_TYPE_' + hardware) in Panda.F4_DEVICES else 'panda_h7.bin.signed'
  expected = choice.filename(app)
  if protocol_ok:
    assert pandad.flash_panda('fixture', choice) == expected
  else:
    with pytest.raises(pandad.PandaProtocolMismatch):
      pandad.flash_panda('fixture', choice)
  assert observed == [expected]
  assert read_saved(params, 'RemoteStartBootsComma', 8)[0] == b'1'


def test_gm_wake_is_independent_of_system_long_and_preserves_saved_intent(setup):
  params, state, owner, _ = setup
  params.put_bool('RemoteStartBootsComma', True, block=True)
  assert state.cp.openpilotLongitudinalControl
  assert owner.panda_wake.rows(True)
  state.cp.openpilotLongitudinalControl = False
  assert supported_keys(state.cp) == ('RemoteStartBootsComma', 'IgnoreIgnitionLine')
  assert owner.panda_wake.rows(True)
  assert resolve(state.cp, snapshot(params)) == FirmwareChoice(gm=True)
  assert read_saved(params, 'RemoteStartBootsComma', 8) == (b'1', True)


@pytest.mark.parametrize('brand', ['gm', 'hyundai', 'tesla'])
@pytest.mark.parametrize('device', ['tici', 'tizi', 'mici'])
@pytest.mark.parametrize('system_long', [False, True])
def test_auto_recognized_vehicle_can_save_and_select_wake_at_startup(setup, brand, device, system_long):
  from openpilot.starpilot.panda_wake import startup_choice
  from openpilot.starpilot.schema_cache import put_cache
  params, state, owner, _ = setup
  state.cp = vehicle(brand)
  state.cp.openpilotLongitudinalControl = system_long
  owner.panda_wake.device_type = lambda: device
  params.put('VehicleSelection', json.loads(encode(None)), block=True)
  put_cache(params, 'CarParamsPersistent', state.cp.as_reader(), block=True)
  row = owner.panda_wake.rows(True)[0]
  request = replace(row_request(row, 'On'), confirmation=True, vehicle_fingerprint=state.cp.carFingerprint)
  assert owner.apply(request)
  assert startup_choice(params) == FirmwareChoice(gm=brand == 'gm', hkg=brand == 'hyundai', tesla=brand == 'tesla')


@pytest.mark.parametrize('mutation', ['mismatch', 'selection_corrupt', 'cache_corrupt', 'cache_missing',
                                      'unknown', 'brand_mismatch', 'not_car', 'source_corrupt'])
def test_startup_rejects_invalid_or_mismatched_sources(setup, mutation):
  from openpilot.starpilot.panda_wake import startup_choice
  from openpilot.starpilot.schema_cache import put_cache
  params, state, _, _ = setup
  params.put_bool('RemoteStartBootsComma', True, block=True)
  if mutation == 'unknown':
    state.cp.carFingerprint = 'CHEVROLET UNKNOWN'
  elif mutation == 'brand_mismatch':
    state.cp.brand = 'tesla'
  elif mutation == 'not_car':
    state.cp.notCar = True
  put_cache(params, 'CarParamsPersistent', state.cp.as_reader(), block=True)
  if mutation == 'mismatch':
    params.put('VehicleSelection', json.loads(encode(str(TESLA.TESLA_MODEL_3))), block=True)
  elif mutation == 'selection_corrupt':
    Path(params.get_param_path('VehicleSelection')).write_bytes(b'invalid')
  elif mutation == 'cache_corrupt':
    Path(params.get_param_path('CarParamsPersistent')).write_bytes(b'invalid')
  elif mutation == 'cache_missing':
    params.remove('CarParamsPersistent')
  elif mutation == 'source_corrupt':
    Path(params.get_param_path('IgnoreIgnitionLine')).write_bytes(b'broken')
  assert startup_choice(params) == FirmwareChoice()


@pytest.mark.parametrize('change', ['selection', 'cache', 'setting'])
def test_startup_rejects_sources_changed_during_cache_validation(setup, monkeypatch, change):
  from openpilot.starpilot import schema_cache
  from openpilot.starpilot.panda_wake import startup_choice
  params, state, _, _ = setup
  params.put_bool('RemoteStartBootsComma', True, block=True)
  schema_cache.put_cache(params, 'CarParamsPersistent', state.cp.as_reader(), block=True)
  original = schema_cache.inspect_cache
  def inspect(key, raw):
    result = original(key, raw)
    if change == 'selection':
      params.put('VehicleSelection', json.loads(encode(None)), block=True)
    elif change == 'cache':
      params.remove('CarParamsPersistent')
    else:
      params.put_bool('IgnoreIgnitionLine', True, block=True)
    return result
  monkeypatch.setattr(schema_cache, 'inspect_cache', inspect)
  assert startup_choice(params) == FirmwareChoice()


def test_ioniq6_c4_with_implicit_auto_and_factory_long_can_enable_remote_climate(setup):
  from openpilot.starpilot.panda_wake import startup_choice
  from openpilot.starpilot.schema_cache import put_cache
  params, state, owner, _ = setup
  state.cp = HKGInterface.get_non_essential_params(HKG.HYUNDAI_IONIQ_6)
  state.cp.openpilotLongitudinalControl = False
  owner.panda_wake.device_type = lambda: 'mici'
  params.remove('VehicleSelection')
  put_cache(params, 'CarParamsPersistent', state.cp.as_reader(), block=True)
  row = owner.panda_wake.rows(True)[0]
  assert row.key == 'HKGRemoteStartBootsComma'
  request = replace(row_request(row, 'On'), confirmation=True, vehicle_fingerprint=state.cp.carFingerprint)
  assert owner.apply(request)
  assert startup_choice(params) == FirmwareChoice(hkg=True)
