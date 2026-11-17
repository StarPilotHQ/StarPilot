from types import SimpleNamespace

import pytest

from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.ford.values import CAR as FORD
from openpilot.common.params import Params
from openpilot.selfdrive.car.tests.test_hyundai_aol import candidate
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.tests.test_sportage_alpha_aol import sportage
from openpilot.starpilot.car.ford.aol import create_intent as ford_intent
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.wheel_feature import WheelFeature


def owner(params, cp, *, configuration=False):
  return SimpleNamespace(params=params, vehicle_params=lambda: cp, configuration_vehicle=lambda: configuration,
                         configuration_longitudinal=lambda: False, longitudinal_available=lambda: cp.openpilotLongitudinalControl,
                         authority=lambda _: True)


@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('configuration', [False, True])
def test_ioniq6_lkas_and_main_are_visible_and_configurable_without_changing_saved_settings(tmp_path, alpha, configuration):
  params = Params(str(tmp_path))
  stock, long_cp = candidate(False)
  cp = long_cp if alpha else stock
  wheel = WheelFeature(owner(params, cp, configuration=configuration))
  for key in ('LKASButtonControl', 'MainCruiseButtonControl'):
    assert 'Toggle AOL' in wheel.choices(key)
    assert params.get(key) is None
    assert any(row.key == 'wheel:' + key for row in wheel.rows())


@pytest.mark.parametrize('alpha', [False, True])
def test_hkg_default_toggle_and_explicit_mapping_are_displayed_truthfully(tmp_path, alpha):
  params = Params(str(tmp_path))
  cp = sportage(alpha=alpha)
  wheel = WheelFeature(owner(params, cp))
  rows = {row.key: row for row in wheel.rows()}
  for key in ('LKASButtonControl', 'MainCruiseButtonControl'):
    assert rows['wheel:' + key].value == 'Toggle AOL'
    assert params.get(key) is None
  params.put('LKASButtonControl', 9, block=True)
  params.put('MainCruiseButtonControl', 0, block=True)
  rows = {row.key: row for row in wheel.rows()}
  assert rows['wheel:LKASButtonControl'].value == 'Toggle AOL'
  assert rows['wheel:MainCruiseButtonControl'].value == 'Off'
  assert params.get('LKASButtonControl') == 9
  assert params.get('MainCruiseButtonControl') == 0
  params.put('LKASButtonControl', 8, block=True)
  rows = {row.key: row for row in wheel.rows()}
  assert rows['wheel:LKASButtonControl'].value == 'Bookmark'
  assert rows['wheel:MainCruiseButtonControl'].value == 'Toggle AOL'
  assert params.get('LKASButtonControl') == 8


def test_mach_e_fixed_lkas_role_matches_actual_intent_and_cannot_claim_off(tmp_path):
  params = Params(str(tmp_path))
  cp = interfaces[FORD.FORD_MUSTANG_MACH_E_MK1].get_non_essential_params(FORD.FORD_MUSTANG_MACH_E_MK1)
  wheel = WheelFeature(owner(params, cp))
  rows = {row.key: row for row in wheel.rows()}
  lkas = rows['wheel:LKASButtonControl']
  assert lkas.value == 'Toggle AOL' and 'Toggle AOL' in lkas.choices
  assert lkas.available and 'also toggles AOL' in lkas.reason
  main = rows['wheel:MainCruiseButtonControl']
  assert main.value == 'Factory cruise / AOL'
  assert not main.available and not main.choices
  for key in ('LKASButtonControl', 'MainCruiseButtonControl'):
    assert params.get(key) is None
  intent = ford_intent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  state = structs.CarState(canValid=True, gearShifter='drive')
  state.cruiseState.available = True
  intent.update(state, now_ns=1, fault_active=False)
  state.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  intent.update(state, now_ns=2, fault_active=False)
  assert intent.allowed_latch


def test_ioniq6_actual_settings_owner_exposes_both_rows(tmp_path):
  params = Params(str(tmp_path))
  cp, _ = candidate(False)
  settings = FeatureSettingsOwner(params, lambda _: True, vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp)
  page = settings.snapshot('wheel', parked=True, system_long=True, lateral_context=True, metric=False)
  rows = {row.key: row for row in page.rows}
  for key in ('LKASButtonControl', 'MainCruiseButtonControl'):
    assert 'Toggle AOL' in rows['wheel:' + key].choices


@pytest.mark.parametrize('brand', ['chrysler', 'mazda', 'nissan', 'volkswagen'])
def test_factory_main_aol_role_is_visible_without_inventing_a_button_mapping(tmp_path, brand):
  from openpilot.starpilot.aol.vehicle import policy_for

  candidates = (interface.get_non_essential_params(identity) for identity, interface in interfaces.items()
                if interface.__module__.split('.')[-2] == brand)
  cp = next((candidate for candidate in candidates if policy_for(candidate).runtime_supported), None)
  assert cp is not None, brand
  params = Params(str(tmp_path))
  wheel = WheelFeature(owner(params, cp))
  rows = {row.key: row for row in wheel.rows()}
  main = rows['wheel:MainCruiseButtonControl']
  assert main.value == 'Factory cruise / AOL'
  assert not main.available and not main.choices
  assert params.get('MainCruiseButtonControl') is None
  assert not wheel.choices('MainCruiseButtonControl')


def test_mazda_cancel_assignment_reaches_wheel_publisher_without_missing_import(tmp_path):
  from opendbc.car.mazda.values import CAR as MAZDA
  from openpilot.starpilot.controllers.wheel_actions import WheelPublisher, button_reported, capture, eligible

  cp = interfaces[MAZDA.MAZDA_CX5_2022].get_non_essential_params(MAZDA.MAZDA_CX5_2022)
  cp.alternativeExperience = 32
  params = Params(str(tmp_path))
  params.put('CancelButtonControl', 8, block=True)
  assert eligible(cp)
  assert button_reported(cp, 'CancelButtonControl')
  publisher = WheelPublisher(params)
  state = structs.CarState(canValid=True, gearShifter='drive')
  publisher.observe(params, cp, state, now_ns=10_000_000, drive_id=1)
  state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  publisher.observe(params, cp, state, now_ns=20_000_000, drive_id=1)
  state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=False)]
  commands = publisher.observe(params, cp, state, now_ns=30_000_000, drive_id=1)
  assert commands == (('CancelButtonControl', 8),), (capture(params), publisher.mapping, publisher.cancel_tracker.__dict__, publisher.axis_keys)
