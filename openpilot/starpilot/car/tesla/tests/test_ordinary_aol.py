"""Standard and screen harnesses select independent, exact AOL profiles."""
from dataclasses import replace
import pytest
from opendbc.car import structs
from opendbc.car.tesla.values import CAR
from opendbc.car.tesla.screen_button import apply_modern_aol, modern_aol_supported, modern_aol_qualified, screen_tap_supported
from opendbc.car.tesla.tests.test_screen_button import screen_params
from openpilot.common.params import Params
from openpilot.starpilot.aol.intent import AolSettings, ButtonType
from openpilot.starpilot.car.tesla.aol import policy_for, native_accepts_cp
from openpilot.starpilot.car.tesla.screen_intent import TeslaScreenCardIntent
from openpilot.starpilot.car.tesla.tests.test_screen_intent import state
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
from openpilot.starpilot.galaxy.tests.test_tesla_screen_settings import vehicle
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway


@pytest.mark.parametrize('candidate', (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y))
@pytest.mark.parametrize('harness', (False, True))
@pytest.mark.parametrize('screen', (False, True))
@pytest.mark.parametrize('long', (False, True))
@pytest.mark.parametrize('brake', (False, True))
def test_profiles_settings_and_exact_native_identity(candidate, harness, screen, long, brake):
  cp = screen_params(candidate, harness=harness, long=long)
  assert modern_aol_supported(cp) and policy_for(cp).settings_supported
  assert screen_tap_supported(cp) is harness
  apply_modern_aol(cp, True, screen, brake)
  expected = (512 if harness and screen else 2048) + int(long) + 1024 * brake
  assert cp.safetyConfigs[0].safetyParam == expected
  assert modern_aol_qualified(cp) and policy_for(cp).runtime_supported
  cp.alternativeExperience = 32
  assert native_accepts_cp(cp, 10, expected)
  assert not native_accepts_cp(cp, 10, expected ^ 1024)
  apply_modern_aol(cp, False, screen, brake)
  assert cp.safetyConfigs[0].safetyParam == int(long)
  assert not modern_aol_qualified(cp)


@pytest.mark.parametrize('brake', (False, True))
@pytest.mark.parametrize('cooperative', (False, True))
def test_actual_startup_selection_is_frozen_and_master_off_clears(tmp_path, brake, cooperative):
  params = Params(str(tmp_path))
  for key, value in (('AlwaysOnLateral', True), ('TeslaAOLDisengageOnBrake', brake), ('TeslaCoopSteering', cooperative)):
    params.put_bool(key, value, block=True)
  saved = VehicleStartupPreferences.read(params, enabled=True)
  cp = screen_params(harness=False)
  saved.prepare(cp)
  word = 2048 + 1024 * brake + 256 * cooperative
  assert cp.safetyConfigs[0].safetyParam == word
  params.put_bool('TeslaAOLDisengageOnBrake', not brake, block=True)
  saved.finalize(cp)
  assert cp.safetyConfigs[0].safetyParam == word
  params.put_bool('AlwaysOnLateral', False, block=True)
  VehicleStartupPreferences.read(params, enabled=True).prepare(cp)
  assert cp.safetyConfigs[0].safetyParam == 256 * cooperative
  assert not policy_for(cp).runtime_supported


@pytest.mark.parametrize('brake', (False, True))
def test_ordinary_intent_brake_rearm_and_screen_input_refusal(brake):
  cp = screen_params(harness=False)
  apply_modern_aol(cp, True, False, brake)
  owner = TeslaScreenCardIntent(cp, AolSettings(True, 0, 9, 0, (0, 0, 0), (0, 0, 0)))
  owner.update(state().as_reader(), now_ns=1)
  assert owner.allowed_latch
  owner.update(state(press=True).as_reader(), now_ns=2)
  assert owner.allowed_latch, 'screen input must have no authority on the standard harness'
  owner.update(state(brake=True).as_reader(), now_ns=3)
  assert owner.allowed_latch is not brake
  owner.update(state().as_reader(), now_ns=4)
  assert owner.allowed_latch is not brake
  owner.update(state(enabled=True).as_reader(), now_ns=5)
  assert owner.allowed_latch
  cs = state(enabled=True)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=ButtonType.cancel, pressed=True)]
  owner.update(cs.as_reader(), now_ns=6)
  assert not owner.allowed_latch
  owner.settings = replace(owner.settings, enabled=False)
  owner.update(state(enabled=True).as_reader(), now_ns=7)
  assert not owner.allowed_latch


@pytest.mark.parametrize('harness', (False, True))
def test_actual_ui_master_and_brake_toggle_with_either_harness(tmp_path, harness):
  params = Params(str(tmp_path))
  cp = vehicle(harness=harness)
  class Owner:
    context = AuthorityContext(True, cp, cp.to_bytes())
    def sample(self):
      return self.context
  owner = Owner()
  gateway = SettingsGateway(params, owner, clock=lambda: 100.0)
  page = gateway.page('vehicle', 'session', b'generation')
  labels = {row['label'] for row in page['rows']}
  assert 'Disengage AOL on Brake' in labels
  assert ('Three-Finger AOL Tap' in labels) is harness
  aol = gateway.page('aol', 'session', b'generation')
  assert next(row for row in aol['rows'] if row['label'] == 'Enable Always On Lateral')['available']
  index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Disengage AOL on Brake')
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
  assert gateway.confirm(intent['intent'], 'session', b'generation')
  assert params.get_bool('TeslaAOLDisengageOnBrake')
  assert cp.safetyConfigs[0].safetyParam == 0
