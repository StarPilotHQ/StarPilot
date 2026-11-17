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
