"""How the Tesla Model 3/Y rows join the settings owner: through two subclasses of features it already builds, so every
other car's Vehicle Settings, Wheel Controls and Speed Limit Controller pages stay exactly as they were, and each Tesla
key reaches its own feature."""
from dataclasses import replace
import json
from unittest.mock import patch

import pytest
from opendbc.car.car_helpers import interfaces
from opendbc.car.values import BRANDS
from openpilot.common.params import Params
from openpilot.starpilot.common.car_control_config import PARAM
from openpilot.starpilot.favorites.actions import BOOKMARK
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway
from openpilot.starpilot.speed_limits.runtime_settings import ADVANCE_KEYS, PEDAL_OVERRIDE_KEY
from openpilot.starpilot.tests.tesla_set_speed_fixture import tesla
from openpilot.starpilot.ui import feature_settings_owner
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, FeatureSettingsRequest
from openpilot.starpilot.ui.tesla_controls_feature import KEYS as CONTROL_KEYS, TeslaControlsFeature
from openpilot.starpilot.ui.tesla_rows import KEYS, TeslaVehicleRows, TeslaWheelRows
from openpilot.starpilot.ui.tesla_screen_feature import KEYS as SCREEN_KEYS, TeslaScreenFeature
from openpilot.starpilot.ui.tesla_set_speed_feature import KEYS as SET_SPEED_KEYS, SYNC_LABEL, TeslaSetSpeedFeature
from openpilot.starpilot.ui.wheel_feature import WheelFeature

MODELS = ('TESLA_MODEL_3', 'TESLA_MODEL_Y')
# One platform of every brand, and every Tesla that is not a Model 3 or Model Y.
OTHER_CARS = sorted({str(next(iter(brand))) for brand in BRANDS if str(next(iter(brand))) not in MODELS} |
                    {'TESLA_MODEL_X', 'TESLA_MODEL_S_HW1', 'TESLA_MODEL_S_PREAP', 'HYUNDAI_IONIQ_5', 'TOYOTA_RAV4_TSS2'})
PAGES = (FeaturePage.VEHICLE, FeaturePage.WHEEL, FeaturePage.SLC)
CABLE_LABEL = 'Tesla CAN add-on cable'
RULE_KEYS = (*ADVANCE_KEYS, PEDAL_OVERRIDE_KEY)


class ScreenRowsBefore(TeslaScreenFeature):
  """The Tesla screen rows as the owner built them before the set-speed rows joined."""


class WheelBefore(WheelFeature):
  """The wheel rows as the owner added them before the Tesla rows joined: after whatever the page already had."""
  def page(self, rows):
    return [*rows, *self.rows()]


def other(platform):
  return None if platform is None else interfaces[platform].get_non_essential_params(platform)


def switch_on(params):
  params.put_bool('TeslaSpeedSync', True, block=True)
  return params


def build_owner(params, cp, *, before=False):
  """The settings owner for `cp`; before=True wires the two features the owner built before the Tesla rows were added."""
  owner = FeatureSettingsOwner(params, lambda group: True, vehicle_fingerprint=lambda: getattr(cp, 'carFingerprint', None),
                               vehicle_params=lambda: cp)
  if before:
    vars(owner).update(tesla_screen=ScreenRowsBefore(owner), wheel=WheelBefore(owner))
  return owner


def pages(owner):
  return {(page, parked, long): owner.snapshot(page, parked=parked, system_long=long, lateral_context=False, metric=False)
          for page in PAGES for parked in (True, False) for long in (True, False)}


class Context:
  def __init__(self, cp):
    self.context = AuthorityContext(True, cp, None if cp is None else cp.as_reader().as_builder().to_bytes())

  def sample(self):
    return self.context


def galaxy_rows(params, cp, page):
  gateway = SettingsGateway(params, Context(cp), clock=lambda: 100.0)
  return json.dumps(gateway.page(page, 'session', b'generation')['rows'], sort_keys=True).encode()


def test_the_owner_builds_the_two_tesla_subclasses_in_place_of_the_features_it_had(tmp_path):
  owner = build_owner(Params(str(tmp_path)), tesla(selected=True))
  assert type(owner.tesla_screen) is TeslaVehicleRows and isinstance(owner.tesla_screen, TeslaScreenFeature)
  assert type(owner.wheel) is TeslaWheelRows and isinstance(owner.wheel, WheelFeature)
  assert type(owner.tesla_screen.set_speed) is TeslaSetSpeedFeature and type(owner.tesla_screen.controls) is TeslaControlsFeature
  assert type(owner.wheel.tesla) is TeslaControlsFeature
  assert not hasattr(owner, 'tesla_set_speed') and not hasattr(owner, 'tesla_controls')
  assert KEYS == SCREEN_KEYS | SET_SPEED_KEYS | CONTROL_KEYS == feature_settings_owner.TESLA_SCREEN_KEYS
  assert SET_SPEED_KEYS == {'TeslaSpeedSync'} and SCREEN_KEYS == {'TeslaAOLDisengageOnBrake'}
  assert not SCREEN_KEYS & SET_SPEED_KEYS and not SCREEN_KEYS & CONTROL_KEYS and not SET_SPEED_KEYS & CONTROL_KEYS


@pytest.mark.parametrize('platform', [None, *OTHER_CARS])
def test_vehicle_wheel_and_speed_limit_pages_are_unchanged_for_other_cars_and_for_no_car(tmp_path, platform):
  params = switch_on(Params(str(tmp_path)))  # the switch saved on by a Tesla owner means nothing to another car
  for name in RULE_KEYS:
    params.put(name, 1, block=True)
  now = pages(build_owner(params, other(platform)))
  before = pages(build_owner(params, other(platform), before=True))
  assert now == before
  assert repr(now).encode() == repr(before).encode(), 'byte for byte'
  for state in now.values():
    assert not any(row.status or row.links or row.key in KEYS or row.key.startswith('tesla_input:') for row in state.rows)
    assert CABLE_LABEL not in [row.label for row in state.rows] and SYNC_LABEL not in [row.label for row in state.rows]
  assert not build_owner(params, other(platform)).tesla_screen.set_speed.active()


@pytest.mark.parametrize('platform', [None, 'HYUNDAI_IONIQ_5', 'TOYOTA_RAV4_TSS2', 'TESLA_MODEL_X', 'TESLA_MODEL_S_HW1', 'TESLA_MODEL_S_PREAP'])
@pytest.mark.parametrize('page', ['vehicle', 'wheel', 'slc'])
def test_galaxy_gets_the_same_bytes_for_other_cars_and_for_no_car(tmp_path, platform, page):
  params = switch_on(Params(str(tmp_path)))
  now = galaxy_rows(params, other(platform), page)
  with patch.object(feature_settings_owner, 'TeslaVehicleRows', ScreenRowsBefore), \
       patch.object(feature_settings_owner, 'TeslaWheelRows', WheelBefore):
    before = galaxy_rows(params, other(platform), page)
  assert now == before
  assert all('status' not in row and 'links' not in row for row in json.loads(now)), 'no key the page did not have before'


@pytest.mark.parametrize('model', MODELS)
@pytest.mark.parametrize('switch', [False, True])
def test_a_tesla_gets_its_rows_above_the_screen_rows_and_below_the_wheel_buttons(tmp_path, model, switch):
  params = Params(str(tmp_path))
  params.put_bool('TeslaSpeedSync', switch, block=True)
  cp = tesla(model, selected=True)
  now, before = pages(build_owner(params, cp)), pages(build_owner(params, cp, before=True))
  for key, state in now.items():
    labels, old = [row.label for row in state.rows], [row.label for row in before[key].rows]
    if key[0] == FeaturePage.VEHICLE:
      at = labels.index(CABLE_LABEL)
      assert labels[at:at + 2] == [CABLE_LABEL, SYNC_LABEL] and labels[at + 2] == 'Disengage AOL on Brake'
      assert labels[:at] + labels[at + 2:] == old, 'two rows added, nothing else moved'
    elif key[0] == FeaturePage.WHEEL:
      added = labels[len(old):]
      assert labels[:len(old)] == old and len(added) == 14
      assert added[0] == 'Right scroll wheel / Scroll up' and added[-1] == 'Tesla screen / Three-finger tap'
    else:
      assert labels == old, 'the Speed Limit Controller page has the same rows'
    kept = [row for row in state.rows if row.label not in (CABLE_LABEL, SYNC_LABEL) and
            not row.label.startswith(('Right scroll wheel /', 'Right stalk /', 'Tesla screen /'))]
    assert kept == list(before[key].rows), 'the rows it had are the same rows, whatever the switch says'


# Adopt limits early and After an accelerator override work with the switch on, so their rows are the stock ones.

@pytest.mark.parametrize('model', MODELS)
def test_the_speed_limit_controller_page_is_the_stock_page_with_the_switch_on(tmp_path, model):
  params = Params(str(tmp_path))
  params.put_bool('SpeedLimitController', True, block=True)
  params.put(ADVANCE_KEYS[0], 3, block=True)
  cp = tesla(model, selected=True)

  def page(owner):
    return owner.snapshot(FeaturePage.SLC, parked=True, system_long=True, lateral_context=False, metric=False).rows

  off = page(build_owner(params, cp))
  switch_on(params)
  owner = build_owner(params, cp)
  assert owner.tesla_screen.set_speed.active()
  assert page(owner) == off == page(build_owner(params, cp, before=True))
  rows = {row.key: row for row in off if row.key in RULE_KEYS}
  assert list(rows) == list(RULE_KEYS) and all(row.available for row in rows.values())
  assert rows[ADVANCE_KEYS[0]].value == '3 s'


STEP_ROWS = ['Short press', 'Hold']


@pytest.mark.parametrize('model', MODELS)
@pytest.mark.parametrize('cable', [True, False])
def test_galaxy_leaves_the_cruise_step_rows_out_for_a_model_3_or_y(tmp_path, model, cable):
  # Galaxy shows Short press and Hold above the wheel rows for every car. StarPilot never steps a Model 3/Y's set speed
  # from cruise buttons (the scroll wheel steps are fixed), so they are left out there, cable or not.
  rows = json.loads(galaxy_rows(Params(str(tmp_path)), tesla(model, cable=cable, selected=cable), 'wheel'))
  labels = [row['label'] for row in rows]
  assert not [label for label in labels if label in STEP_ROWS]
  assert labels[0] == 'Right scroll wheel / Scroll up' and labels[-1] == 'Tesla screen / Three-finger tap'
  with patch.object(feature_settings_owner, 'TeslaWheelRows', WheelBefore):
    before = [row['label'] for row in json.loads(galaxy_rows(Params(str(tmp_path)), tesla(model, cable=cable, selected=cable), 'wheel'))]
  assert before[:2] == STEP_ROWS, 'they were the first two rows'


@pytest.mark.parametrize('platform', ['HYUNDAI_IONIQ_5', 'TESLA_MODEL_X', 'TESLA_MODEL_S_HW1', 'TESLA_MODEL_S_PREAP'])
def test_galaxy_keeps_the_cruise_step_rows_for_other_cars(tmp_path, platform):
  labels = [row['label'] for row in json.loads(galaxy_rows(Params(str(tmp_path)), other(platform), 'wheel'))]
  assert labels[:2] == STEP_ROWS


def tesla_owner(tmp_path):
  params = Params(str(tmp_path))
  cp = tesla(selected=True)
  return params, cp, build_owner(params, cp)


def page_row(owner, page, key):
  state = owner.snapshot(page, parked=True, system_long=True, lateral_context=False, metric=False)
  return next(row for row in state.rows if row.key == key)


def request_for(row, value):
  return FeatureSettingsRequest(row.key, row.source, value, confirmation=True, vehicle_fingerprint=row.vehicle_fingerprint,
                                capability=row.capability, dependencies=row.dependencies, display_unit=row.display_unit)


ROUTES = [('TeslaSpeedSync', 'set_speed'), ('tesla_input:right_wheel_left', 'controls'), ('tesla_input:right_wheel_left_hold', 'controls'),
          ('tesla_input:right_wheel_left:speed', 'controls'), ('tesla_input:right_stalk_down_tap_engaged', 'controls'),
          ('tesla_input:screen_three_finger_tap', 'controls'), ('TeslaAOLDisengageOnBrake', 'screen')]


@pytest.mark.parametrize('key,target', ROUTES)
def test_each_tesla_key_reaches_its_own_feature_through_the_owner(tmp_path, key, target):
  _, _, owner = tesla_owner(tmp_path)
  request = FeatureSettingsRequest(key, None, 'On', confirmation=True, vehicle_fingerprint='TESLA_MODEL_3')
  with patch.object(owner.tesla_screen.set_speed, 'apply', return_value=True) as set_speed, \
       patch.object(owner.tesla_screen.controls, 'apply', return_value=True) as controls, \
       patch.object(TeslaScreenFeature, 'apply', return_value=True) as screen, \
       patch.object(owner.wheel, 'apply', return_value=True) as wheel, \
       patch.object(owner.wheel.tesla, 'apply', return_value=True) as wheel_tesla, \
       patch.object(owner.tesla_coop, 'apply', return_value=True) as coop:
    assert owner.apply(request) is True
  calls = {'set_speed': set_speed.call_count, 'controls': controls.call_count, 'screen': screen.call_count}
  assert calls == {name: int(name == target) for name in calls}
  assert {'set_speed': set_speed, 'controls': controls, 'screen': screen}[target].call_args.args[-1] is request
  assert not wheel.called and not wheel_tesla.called and not coop.called


@pytest.mark.parametrize('key', ['tesla_input:right_wheel_up', 'tesla_input:right_wheel_press', 'tesla_input:', 'tesla_input:unknown',
                                 'tesla_input:indicator_left_half', 'TeslaIndependentSetSpeed', 'TeslaOverrideSetSpeed',
                                 'TeslaGentleSlowing', 'TeslaAOLScreenTap'])
def test_keys_that_are_not_settings_reach_no_tesla_feature(tmp_path, key):
  params, _, owner = tesla_owner(tmp_path)
  request = FeatureSettingsRequest(key, None, 'On', confirmation=True, vehicle_fingerprint='TESLA_MODEL_3')
  with patch.object(owner.tesla_screen.set_speed, 'apply', return_value=True) as set_speed, \
       patch.object(owner.tesla_screen.controls, 'apply', return_value=True) as controls, \
       patch.object(TeslaScreenFeature, 'apply', return_value=True) as screen:
    assert not owner.apply(request)
  assert not set_speed.called and not controls.called and not screen.called
  assert params.get(PARAM) is None


def test_a_wheel_button_key_still_reaches_the_wheel_feature(tmp_path):
  _, _, owner = tesla_owner(tmp_path)
  request = FeatureSettingsRequest('wheel:anything', None, 'Off')
  with patch.object(WheelFeature, 'apply', return_value=True) as wheel, \
       patch.object(owner.tesla_screen.controls, 'apply', return_value=True) as controls:
    assert owner.apply(request) is True
  assert wheel.call_count == 1 and not controls.called


def test_a_set_speed_request_is_saved_through_the_owner_and_turns_the_rule_on(tmp_path):
  params, cp, owner = tesla_owner(tmp_path)
  row = page_row(owner, FeaturePage.VEHICLE, 'TeslaSpeedSync')
  assert row.available and row.vehicle_fingerprint == 'TESLA_MODEL_3' and not owner.tesla_screen.set_speed.active()
  assert owner.apply(request_for(row, 'On')) and params.get_bool('TeslaSpeedSync')
  assert page_row(owner, FeaturePage.VEHICLE, 'TeslaSpeedSync').value == 'On'
  assert owner.tesla_screen.set_speed.active(), 'the same owner sees it at once'
  assert page_row(owner, FeaturePage.SLC, PEDAL_OVERRIDE_KEY).available, 'After an accelerator override stays in use'
  assert not owner.apply(request_for(row, 'On')), 'the row shown before the save is stale'
  assert cp.safetyConfigs[0].safetyParam == 4097, "the car's parameters do not change"


def test_a_control_request_is_saved_through_the_owner(tmp_path):
  params, _, owner = tesla_owner(tmp_path)
  row = page_row(owner, FeaturePage.WHEEL, 'tesla_input:right_wheel_left')
  assert owner.apply(request_for(row, 'Bookmark'))
  assert params.get(PARAM) == {'version': 1, 'vehicle': 'TESLA_MODEL_3', 'bindings': {
    'right_wheel_left': {'key': BOOKMARK, 'value': None, 'label': 'Bookmark'}}}
  assert page_row(owner, FeaturePage.WHEEL, 'tesla_input:right_wheel_left').value == 'Bookmark'
  assert not params.get_bool('TeslaSpeedSync')


def test_the_tap_row_in_wheel_controls_is_the_only_place_its_always_on_lateral_switch_is_set(tmp_path):
  params, _, owner = tesla_owner(tmp_path)
  for parked in (True, False):
    state = owner.snapshot(FeaturePage.VEHICLE, parked=parked, system_long=True, lateral_context=False, metric=False)
    assert 'TeslaAOLScreenTap' not in [row.key for row in state.rows]
    assert 'Three-Finger AOL Tap' not in [row.label for row in state.rows]
  tap = page_row(owner, FeaturePage.WHEEL, 'tesla_input:screen_three_finger_tap')
  assert owner.apply(request_for(tap, 'Always On Lateral on/off')) and params.get_bool('TeslaAOLScreenTap')
  tap = page_row(owner, FeaturePage.WHEEL, 'tesla_input:screen_three_finger_tap')
  assert tap.value == 'Always On Lateral on/off'
  assert owner.apply(request_for(tap, 'Unassigned')) and params.get('TeslaAOLScreenTap') is False
  assert page_row(owner, FeaturePage.WHEEL, 'tesla_input:screen_three_finger_tap').value == 'Unassigned'
  switch = FeatureSettingsRequest('TeslaAOLScreenTap', None, 'On', confirmation=True, vehicle_fingerprint='TESLA_MODEL_3',
                                  capability=owner.tesla_screen.capability())
  assert not owner.apply(switch) and params.get('TeslaAOLScreenTap') is False, 'the old Vehicle Settings switch is gone'


@pytest.mark.parametrize('platform', [None, 'HYUNDAI_IONIQ_5', 'TESLA_MODEL_X'])
def test_tesla_requests_through_the_owner_save_nothing_for_another_car(tmp_path, platform):
  params = Params(str(tmp_path))
  cp = other(platform)
  owner = build_owner(params, cp)
  (tmp_path / 'tesla').mkdir()
  tesla_rows = tesla_owner(tmp_path / 'tesla')[2]
  sync = replace(request_for(page_row(tesla_rows, FeaturePage.VEHICLE, 'TeslaSpeedSync'), 'On'),
                 vehicle_fingerprint=getattr(cp, 'carFingerprint', None))
  control = replace(request_for(page_row(tesla_rows, FeaturePage.WHEEL, 'tesla_input:right_wheel_left'), 'Bookmark'),
                    vehicle_fingerprint=getattr(cp, 'carFingerprint', None))
  assert not owner.apply(sync) and not owner.apply(control)
  assert not params.get_bool('TeslaSpeedSync') and params.get(PARAM) is None
