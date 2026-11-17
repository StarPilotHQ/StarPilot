from types import SimpleNamespace

import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_volt_flashed_obd import params as flashed_params, tick
from opendbc.car.gm.values import DBC
from openpilot.common.params import Params
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.tests.test_gm_startup_main import cases
from openpilot.starpilot.aol.vehicle import create_intent, policy_for
from openpilot.starpilot.controllers.wheel_actions import button_reported
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_request


def params():
  cp = flashed_params()
  cp.alternativeExperience = 32
  return cp


def parser():
  cp = params()
  return CarInterface(cp), CANPacker(DBC[cp.carFingerprint][Bus.pt])


def feed_lkas(ci, packer, levels, index, cruise=1):
  now = 1_000_000_000 + index * 10_000_000
  _, frames = tick(SimpleNamespace(update=lambda _: None), packer, now, index)
  frames = [m for m in frames if m[0] != 0x1E1]
  for level in levels:
    frames.append(packer.make_can_msg('ASCMSteeringButton', 0, {'ACCButtons': cruise, 'ACCAlwaysOne': 1,
      'LKAButton': level, 'RollingCounter': index % 4}))
  return ci.update([(now, frames)])


def test_parser_emits_each_lkas_edge_without_cruise_enable():
  ci, packer = parser()
  for i in range(40):
    cs = feed_lkas(ci, packer, [0], i)
  assert cs.canValid
  cs = feed_lkas(ci, packer, [1, 0, 1, 1, 0], 40)
  assert [(str(b.type), b.pressed) for b in cs.buttonEvents] == [('lkas', True), ('lkas', False), ('lkas', True), ('lkas', False)]
  assert not cs.buttonEnable and not cs.cruiseState.enabled
  assert not feed_lkas(ci, packer, [], 41).buttonEvents


def test_first_held_sample_is_not_a_new_press():
  ci, packer = parser()
  assert not feed_lkas(ci, packer, [1], 0).buttonEvents
  assert not feed_lkas(ci, packer, [1], 1).buttonEvents
  assert [(str(b.type), b.pressed) for b in feed_lkas(ci, packer, [0], 2).buttonEvents] == [('lkas', False)]


@pytest.mark.parametrize('mapping', [0, 3, 4, 9])
def test_main_arms_at_startup_and_lkas_only_performs_its_mapping(mapping):
  cp = params()
  intent = create_intent(cp, AolSettings(True, 0., mapping, 0, (0, 0, 0), (0, 0, 0)), policy_for(cp))
  cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=10.)
  cs.cruiseState.available = True
  intent.update(cs, now_ns=1, fault_active=False)
  assert intent.allowed_latch and not intent.pause_lateral and not intent.pause_longitudinal
  for pressed, now in [(True, 2), (False, 3)]:
    cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=pressed)]
    intent.update(cs, now_ns=now, fault_active=False)
  assert intent.allowed_latch and intent.pause_lateral == (mapping in (3, 9))
  assert intent.pause_longitudinal == (mapping == 4)
  cs.buttonEvents = []
  intent.update(cs, now_ns=4, fault_active=False)
  assert intent.pause_lateral == (mapping in (3, 9))
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  intent.update(cs, now_ns=5, fault_active=False)
  assert not intent.pause_lateral and not intent.pause_longitudinal and intent.allowed_latch
  cs.cruiseState.available = False
  cs.buttonEvents = []
  intent.update(cs, now_ns=6, fault_active=False)
  assert not intent.allowed_latch


def test_lkas_row_is_visible_and_assignment_uses_actual_settings_owner(tmp_path):
  cp = params()
  saved = Params(str(tmp_path))
  owner = FeatureSettingsOwner(saved, lambda _: True, vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp)
  page = owner.snapshot('wheel', parked=True, system_long=True, lateral_context=True, metric=False)
  row = next(r for r in page.rows if r.key == 'wheel:LKASButtonControl')
  assert row.value == 'Off' and row.available and 'Pause steering' in row.choices and 'Toggle AOL' in row.choices
  assert owner.apply(row_request(row, 'Pause steering'))
  assert saved.get('LKASButtonControl') == 3


def test_other_gm_profiles_keep_the_existing_lkas_contract():
  for cp in cases():
    assert not policy_for(cp).lkas_button_supported
    assert not button_reported(cp, 'LKASButtonControl')


def test_lkas_does_not_erase_a_simultaneous_set_release():
  ci, packer = parser()
  for i in range(40):
    feed_lkas(ci, packer, [0], i)
  feed_lkas(ci, packer, [0], 40, cruise=3)
  cs = feed_lkas(ci, packer, [1], 41)
  assert cs.buttonEnable
  assert [(str(b.type), b.pressed) for b in cs.buttonEvents] == [('decelCruise', False), ('lkas', True)]
