"""Cruise MAIN assigned to Adopt speed limit is not also the Hyundai physical lateral toggle."""
import pytest
from opendbc.car import structs
from openpilot.starpilot.aol.intent import AolCardIntent, AolSettings, read_settings
from openpilot.starpilot.aol.tests.test_ioniq6_stock_repair import stock
from openpilot.starpilot.car.hyundai.aol import create_intent, qualified_ioniq6


def long_cp():
  cp = stock(False)
  cp.pcmCruise = False
  cp.openpilotLongitudinalControl = True
  cp.safetyConfigs[0].safetyParam = 0x8815
  assert qualified_ioniq6(cp)
  return cp


def press(owner, cs, button, now_ns, *, engaged=False):
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=True)]
  owner.update(cs, now_ns=now_ns, fault_active=False, standard_enabled=engaged)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=False)]
  owner.update(cs, now_ns=now_ns + 1, fault_active=False, standard_enabled=engaged)


def lateral(owner):
  return owner.allowed_latch and not owner.pause_lateral


def armed(owner):
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  owner.update(cs, now_ns=1, fault_active=False)
  press(owner, cs, 'lkas', 2)
  assert lateral(owner)
  return cs


@pytest.mark.parametrize('main_wheel_action', [False, True])
def test_ioniq6_main_assigned_to_adopt_keeps_lkas_armed_lateral(main_wheel_action):
  owner = create_intent(long_cp(), AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0), main_wheel_action=main_wheel_action))
  assert owner.independent_cruise_buttons
  cs = armed(owner)
  press(owner, cs, 'mainCruise', 10, engaged=True)
  assert lateral(owner) == main_wheel_action
  press(owner, cs, 'lkas', 20, engaged=True)
  assert lateral(owner) != main_wheel_action


def test_main_toggle_is_unchanged_without_independent_cruise_buttons():
  owner = AolCardIntent(AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0), main_wheel_action=True), explicit_latch=True)
  cs = armed(owner)
  press(owner, cs, 'mainCruise', 10, engaged=True)
  assert not lateral(owner)


class SavedParams:
  def __init__(self, values):
    self.values = values

  def get(self, key, return_default=False):
    return self.values.get(key)

  def get_bool(self, key):
    return self.values.get(key) == b'1'

  def get_param_path(self, key):
    return f'/nonexistent/{key}'


@pytest.mark.parametrize('lkas,main,main_wheel', [(b'0', b'10', True), (b'0', b'9', False), (b'10', b'0', False), (b'0', b'8', False)])
def test_only_main_adopt_is_marked_as_a_main_wheel_action(lkas, main, main_wheel):
  settings = read_settings(SavedParams({'AlwaysOnLateral': b'1', 'LKASButtonControl': lkas, 'MainCruiseButtonControl': main}))
  assert settings.main_wheel_action == main_wheel


@pytest.mark.parametrize('action,keeps_lateral', [(8, True), (9, False), (0, False)])
def test_tucson_lkas_bookmark_does_not_double_as_lateral_toggle(action, keeps_lateral):
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.hyundai.interface import CarInterface
  from opendbc.car.hyundai.values import CAR

  fingerprint = gen_empty_fingerprint()
  fingerprint[0][0x1AA] = 16
  cp = CarInterface.get_params(CAR.HYUNDAI_TUCSON_4TH_GEN, fingerprint, [], False, False, False)
  assert cp.safetyConfigs[0].safetyParam == 0x28
  cp.safetyConfigs[0].safetyParam |= 0x800
  settings = read_settings(SavedParams({'AlwaysOnLateral': b'1', 'LKASButtonControl': str(action).encode()}))
  owner = create_intent(cp, settings)
  assert owner.independent_cruise_buttons
  cs = structs.CarState(canValid=True, gearShifter='drive', cruiseState={'available': True})
  owner.update(cs, now_ns=1, fault_active=False)
  if keeps_lateral:
    press(owner, cs, 'lkas', 2)
    assert not lateral(owner) and owner.physical_arm_ns == 0
  cs.buttonEvents = []
  owner.observe_full_engagement(cs, enabled=False, active=False, source_ns=5, observation_valid=True, now_ns=5, fault_active=False)
  owner.observe_full_engagement(cs, enabled=True, active=True, source_ns=6, observation_valid=True, now_ns=6, fault_active=False)
  assert lateral(owner)
  press(owner, cs, 'lkas', 10, engaged=True)
  assert lateral(owner) == keeps_lateral
  assert owner.pause_lateral != keeps_lateral


@pytest.mark.parametrize('topology', ['hdai', 'hdaii'])
@pytest.mark.parametrize('alpha_long', [False, True])
@pytest.mark.parametrize('action', [0, 8, 9])
def test_mixed_palisade_lkas_bookmark_owns_only_the_wheel_action(tmp_path, topology, alpha_long, action):
  from opendbc.car.hyundai.blended_stock_aol import qualified, qualified_alpha
  from opendbc.car.hyundai.tests.test_palisade_2023 import params as palisade_params
  from openpilot.starpilot.controllers.wheel_actions import WheelPublisher

  cp = palisade_params(topology)
  cp.alternativeExperience = 32
  if alpha_long:
    cp.openpilotLongitudinalControl = True
    cp.pcmCruise = False
    cp.alphaLongitudinalAvailable = True
    cp.safetyConfigs[0].safetyParam |= 4
  assert (qualified_alpha if alpha_long else qualified)(cp, marked_only=True)
  saved = SavedParams({'AlwaysOnLateral': b'1', 'LKASButtonControl': str(action).encode(), 'MainCruiseButtonControl': b'0'})
  (tmp_path/'LKASButtonControl').write_bytes(saved.get('LKASButtonControl'))
  saved.get_param_path = lambda key: str(tmp_path/key)
  owner = create_intent(cp, read_settings(saved))
  cs = structs.CarState(canValid=True, gearShifter='drive', cruiseState={'available': True, 'enabled': True})
  now = 3_000_000_000
  publisher = WheelPublisher()
  owner.update(cs, now_ns=now, fault_active=False)
  assert publisher.observe(saved, cp, cs, now_ns=now, drive_id=1_000_000_000) == (('', 0),)
  if action == 8:
    press(owner, cs, 'lkas', now + 1)
    assert not owner.allowed_latch and owner.physical_arm_ns == 0
  cs.buttonEvents = []
  owner.observe_full_engagement(cs, enabled=False, active=False, source_ns=now + 3,
                                observation_valid=True, now_ns=now + 3, fault_active=False)
  owner.observe_full_engagement(cs, enabled=True, active=True, source_ns=now + 4,
                                observation_valid=True, now_ns=now + 4, fault_active=False)
  assert lateral(owner)
  edge = owner._last_latch_edge_ns
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(cs, now_ns=now + 10_000_000, fault_active=False, standard_enabled=True)
  actions = publisher.observe(saved, cp, cs, now_ns=now + 10_000_000, drive_id=1_000_000_000)
  assert actions == ((('LKASButtonControl', 8),) if action == 8 else ())
  assert lateral(owner) == (action == 8)
  if action == 8:
    assert not owner.pause_lateral and owner._last_latch_edge_ns == edge
    owner.update(cs, now_ns=now + 20_000_000, fault_active=True, standard_enabled=True)
    assert not lateral(owner)
