"""Tesla Model 3/Y Vehicle Settings: the CAN add-on cable status and the one switch, Comma can set speed on Tesla
dashboard. The switch takes effect within a second, without a restart, and is shown grayed with the reason when the car
as it started cannot use it."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import pytest

from opendbc.car.structs import car
from opendbc.car.tesla.speed_sync import selected
from opendbc.car.tesla.values import CAR
from openpilot.common.params import Params, UnknownKeyName
from openpilot.starpilot.car.tesla import set_speed
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged, SettingsGateway, _projection
from openpilot.starpilot.tests.tesla_set_speed_fixture import tesla
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, FeatureRow, FeatureSettingsRequest, FeatureUiAction, value_request
from openpilot.starpilot.ui.tesla_set_speed_feature import (CABLE_HELP, CABLE_LINKS, CABLE_MISSING, KEYS, LONG_MISSING, RESTART_NEEDED,
                                                            SYNC, SYNC_HELP, SYNC_LABEL, TeslaSetSpeedFeature, addon_cable)
from openpilot.starpilot.ui.vehicle_bool import VEHICLE_BOOL_KEYS, confirmation_question
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences

CABLE_LABEL = 'Tesla CAN add-on cable'
RELEASE = 'Not available on release builds'
REMOVED_ROWS = ('Independent set speed', 'Override Tesla set-speed changes', "Match the Tesla's set speed", 'Gentle slowing',
                'Short scroll', 'Fast scroll')
MODELS = (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y)


def started(candidate=CAR.TESLA_MODEL_3, **changes):
  """A Model 3/Y as it normally starts: startup admits the scroll frame whenever the car is eligible."""
  return tesla(candidate, selected=True, **changes)


def other_car(brand, fingerprint):
  return car.CarParams.new_message(brand=brand, carFingerprint=fingerprint, pcmCruise=True, openpilotLongitudinalControl=True, flags=8)


@pytest.mark.parametrize('candidate', MODELS)
def test_the_cable_is_a_startup_fact_of_model_3_and_y(candidate):
  assert addon_cable(tesla(candidate)) is True and addon_cable(tesla(candidate, cable=False)) is False
  assert addon_cable(tesla(candidate, long=False)) is True, 'the cable does not depend on longitudinal control'


@pytest.mark.parametrize('make', [lambda: None, lambda: other_car('hyundai', 'HYUNDAI_IONIQ_5'),
                                  lambda: other_car('toyota', 'TOYOTA_RAV4_TSS2'), lambda: other_car('mock', 'TESLA_MODEL_3'),
                                  lambda: tesla(CAR.TESLA_MODEL_X), lambda: tesla(CAR.TESLA_MODEL_S_HW1),
                                  lambda: tesla(CAR.TESLA_MODEL_S_PREAP)])
def test_other_cars_have_no_cable_status_and_no_rows(tmp_path, make):
  cp = make()
  assert addon_cable(cp) is None
  owner = FeatureSettingsOwner(Params(str(tmp_path)), lambda group: True,
                               vehicle_fingerprint=lambda: getattr(cp, 'carFingerprint', None), vehicle_params=lambda: cp)
  feature = TeslaSetSpeedFeature(owner)
  assert feature.capability() is None and feature.rows(True) == () and not feature.active()
  labels = [row.label for row in owner.snapshot(FeaturePage.VEHICLE, parked=True, system_long=True, lateral_context=False, metric=False).rows]
  assert CABLE_LABEL not in labels and SYNC_LABEL not in labels


def test_something_that_is_not_car_parameters_has_no_cable_status():
  assert addon_cable(object()) is None


class TeslaSettings(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.use(started())

  def use(self, cp, parked=True):
    self.context = AuthorityContext(parked, cp, cp.as_reader().as_builder().to_bytes())
    self.gateway = SettingsGateway(self.params, self, clock=lambda: 100.0)

  def sample(self):
    return self.context

  def rows(self, page='vehicle'):
    return {row['label']: row for row in self.gateway.page(page, 'session', b'generation')['rows']}

  def labels(self, page='vehicle'):
    return [row['label'] for row in self.gateway.page(page, 'session', b'generation')['rows']]

  def intent(self, label, value, page='vehicle'):
    view = self.gateway.page(page, 'session', b'generation')
    index = next(i for i, row in enumerate(view['rows']) if row['label'] == label)
    return self.gateway.preview(view['view'], index, 0, 'session', b'generation', value=value)

  def save(self, label, value, page='vehicle'):
    return self.gateway.confirm(self.intent(label, value, page)['intent'], 'session', b'generation')

  def write_raw(self, key, raw):
    Path(self.params.get_param_path(key)).write_bytes(raw)

  def test_rows_defaults_and_cable_status(self):
    rows = self.rows()
    cable = rows[CABLE_LABEL]
    assert cable['value'] == 'Detected' and cable['status'] is True and cable['reason'] == CABLE_HELP, 'a status, shown normally'
    assert not cable['available'], 'nothing to change'
    assert [(link['label'], link['url']) for link in cable['links']] == list(CABLE_LINKS)
    assert [label for label, _ in CABLE_LINKS] == ['Buy the cable', 'Install guide']
    assert all(link['url'].startswith('https://') for link in cable['links'])
    sync = rows[SYNC_LABEL]
    assert SYNC_LABEL == 'Comma can set speed on Tesla dashboard'
    assert (sync['value'], sync['available'], sync['defaultValue']) == ('Off', True, 'Off')
    assert sync['reason'] == SYNC_HELP and 'links' not in sync and 'status' not in sync

  def test_the_help_says_what_the_switch_does_now(self):
    assert 'startup' not in SYNC_HELP and 'restart' not in SYNC_HELP, 'it applies at once'
    assert 'lag a second or two' in SYNC_HELP and 'must use the same units' in SYNC_HELP
    assert SYNC_HELP.startswith('StarPilot keeps its own set speed') and SYNC_HELP.endswith('Experimental.')
    assert len(SYNC_HELP) < 240 and len(CABLE_HELP) < 220, 'short enough to read at a glance'
    assert RESTART_NEEDED == 'Available after the next startup'

  def test_only_the_cable_row_is_a_status_for_galaxy(self):
    for page in ('vehicle', 'wheel', 'slc', 'hub'):
      for row in self.gateway.page(page, 'session', b'generation')['rows']:
        assert ('status' in row) is (row['label'] == CABLE_LABEL), (page, row['label'])
        assert ('links' in row) is (row['label'] == CABLE_LABEL), (page, row['label'])

  def test_the_cable_and_the_switch_come_first_of_the_tesla_rows(self):
    labels = self.labels()
    start = labels.index(CABLE_LABEL)
    assert labels[start:start + 4] == [CABLE_LABEL, SYNC_LABEL, 'Disengage AOL on Brake', 'Tesla Co-op Steering']
    assert 'Three-Finger AOL Tap' not in labels, 'the tap is chosen on its own row in Wheel Controls'

  def test_it_is_the_only_set_speed_setting(self):
    assert KEYS == frozenset(('TeslaSpeedSync',)) and SYNC == set_speed.PARAM == 'TeslaSpeedSync'
    for page in ('vehicle', 'wheel', 'slc'):
      labels = self.labels(page)
      for removed in REMOVED_ROWS:
        assert removed not in labels, (page, removed)
    for key in ('TeslaIndependentSetSpeed', 'TeslaOverrideSetSpeed', 'TeslaGentleSlowing'):
      with self.assertRaises(UnknownKeyName):
        self.params.get(key)

  def test_saving_on_and_off_takes_effect_without_a_restart(self):
    cp = self.context.cp
    assert selected(cp) and not set_speed.active(self.params, cp)
    intent = self.intent(SYNC_LABEL, 'On')
    assert intent['proposed'] == 'On' and 'comma can set speed on tesla dashboard' in intent['question'].lower()
    assert self.gateway.confirm(intent['intent'], 'session', b'generation') and self.params.get_bool(SYNC)
    assert self.rows()[SYNC_LABEL]['value'] == 'On'
    assert set_speed.active(self.params, cp), 'the comma owns the set speed on this car now'
    assert cp.safetyConfigs[0].safetyParam == 4097, "the car's parameters never change with the switch"
    assert self.save(SYNC_LABEL, 'Off') and not self.params.get_bool(SYNC)
    assert self.rows()[SYNC_LABEL]['value'] == 'Off' and not set_speed.active(self.params, cp)

  def test_startup_admits_the_scroll_frame_whatever_is_saved(self):
    for saved in (False, True):
      self.params.put_bool(SYNC, saved, block=True)
      cp = tesla()
      VehicleStartupPreferences.read(self.params, enabled=True).prepare(cp)
      assert selected(cp)
      self.use(cp)
      sync = self.rows()[SYNC_LABEL]
      assert sync['available'] and sync['value'] == ('On' if saved else 'Off') and sync['reason'] == SYNC_HELP

  def test_a_car_that_started_without_the_scroll_frame_needs_a_restart(self):
    # Safe Mode at startup, or settings changed since: the cable and longitudinal control are there, the bit is not.
    self.use(tesla())
    rows = self.rows()
    assert rows[CABLE_LABEL]['value'] == 'Detected'
    sync = rows[SYNC_LABEL]
    assert not sync['available'] and sync['reason'] == RESTART_NEEDED + '.\n' + SYNC_HELP
    with self.assertRaises(SettingsChanged):
      self.intent(SYNC_LABEL, 'On')
    self.params.put_bool(SYNC, True, block=True)
    assert self.rows()[SYNC_LABEL]['value'] == 'On' and not set_speed.active(self.params, self.context.cp), 'saved on, not in effect'

  def test_without_the_cable_it_is_grayed_with_the_reason(self):
    self.use(started(cable=False))
    rows = self.rows()
    assert rows[CABLE_LABEL]['value'] == 'Not detected' and rows[CABLE_LABEL]['status'] is True
    sync = rows[SYNC_LABEL]
    assert not sync['available'] and sync['value'] == 'Off'
    assert sync['reason'] == CABLE_MISSING + '.\n' + SYNC_HELP
    with self.assertRaises(SettingsChanged):
      self.intent(SYNC_LABEL, 'On')
    assert not self.params.get_bool(SYNC)

  def test_without_alpha_longitudinal_it_is_grayed_with_the_reason(self):
    self.use(started(long=False))
    rows = self.rows()
    assert rows[CABLE_LABEL]['value'] == 'Detected'
    sync = rows[SYNC_LABEL]
    assert not sync['available'] and sync['reason'] == LONG_MISSING + '.\n' + SYNC_HELP
    assert LONG_MISSING == 'Needs Alpha Longitudinal turned on'
    with self.assertRaises(SettingsChanged):
      self.intent(SYNC_LABEL, 'On')

  def test_on_a_release_build_it_is_grayed_with_the_reason(self):
    self.params.put_bool('IsReleaseBranch', True, block=True)
    sync = self.rows()[SYNC_LABEL]
    assert not sync['available'] and sync['reason'] == RELEASE + '.\n' + SYNC_HELP
    with self.assertRaises(SettingsChanged):
      self.intent(SYNC_LABEL, 'On')
    self.params.put_bool('IsReleaseBranch', False, block=True)
    assert self.rows()[SYNC_LABEL]['available']
    self.write_raw('IsReleaseBranch', b'unreadable')
    assert not self.rows()[SYNC_LABEL]['available'], 'an unreadable release marker counts as a release build'

  def test_the_first_missing_thing_is_the_reason_shown(self):
    self.params.put_bool('IsReleaseBranch', True, block=True)
    self.use(tesla(cable=False, long=False))
    assert self.rows()[SYNC_LABEL]['reason'].startswith(CABLE_MISSING + '.')
    self.use(tesla(long=False))
    assert self.rows()[SYNC_LABEL]['reason'].startswith(LONG_MISSING + '.')
    self.use(tesla())
    assert self.rows()[SYNC_LABEL]['reason'].startswith(RELEASE + '.')
    self.params.put_bool('IsReleaseBranch', False, block=True)
    assert self.rows()[SYNC_LABEL]['reason'].startswith(RESTART_NEEDED + '.')
    self.use(started())
    assert self.rows()[SYNC_LABEL]['reason'] == SYNC_HELP

  def test_a_choice_saved_earlier_still_shows_while_grayed(self):
    assert self.save(SYNC_LABEL, 'On')
    self.use(started(cable=False))
    sync = self.rows()[SYNC_LABEL]
    assert sync['value'] == 'On' and not sync['available'] and sync['reason'].startswith(CABLE_MISSING)
    assert not set_speed.active(self.params, self.context.cp)

  def test_an_invalid_saved_choice_is_shown_cannot_be_changed_and_is_off(self):
    self.write_raw(SYNC, b'2')
    sync = self.rows()[SYNC_LABEL]
    assert (sync['value'], sync['available'], sync['reason']) == ('Invalid saved choice', False, 'Invalid saved choice')
    assert not set_speed.requested(self.params) and not set_speed.active(self.params, self.context.cp)

  def test_it_can_only_be_changed_while_parked(self):
    intent = self.intent(SYNC_LABEL, 'On')
    self.context = replace(self.context, parked=False)
    assert not self.gateway.confirm(intent['intent'], 'session', b'generation'), 'confirmed after driving off: refused'
    assert not self.params.get_bool(SYNC)
    rows = self.rows()
    with self.assertRaises(SettingsChanged):
      self.intent(SYNC_LABEL, 'On')
    assert not rows[SYNC_LABEL]['available'] and rows[CABLE_LABEL]['value'] == 'Detected' and rows[CABLE_LABEL]['status'] is True

  def test_a_confirmation_does_not_survive_a_changed_car(self):
    intent = self.intent(SYNC_LABEL, 'On')
    self.use(started(cable=False))
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent['intent'], 'session', b'generation')
    assert not self.params.get_bool(SYNC)

  def test_model_y_has_the_same_rows(self):
    self.use(started(CAR.TESLA_MODEL_Y))
    rows = self.rows()
    assert rows[CABLE_LABEL]['value'] == 'Detected' and rows[SYNC_LABEL]['available']
    assert self.save(SYNC_LABEL, 'On') and self.params.get_bool(SYNC)
    assert set_speed.active(self.params, self.context.cp)


def test_galaxy_passes_on_a_status_and_only_https_links():
  links = (('Plain', 'http://example.com'), ('Script', 'javascript:alert(1)'), ('Safe', 'https://example.com/a'))
  projected = _projection(FeatureRow('', 'Status', 'Detected', status=True, links=links))
  assert projected['status'] is True and projected['links'] == [{'label': 'Safe', 'url': 'https://example.com/a'}]
  plain = _projection(FeatureRow('Key', 'Label', 'Off'))
  assert 'status' not in plain and 'links' not in plain, 'every other row is projected exactly as before'


class Owner:
  """The settings owner as the feature sees it, with a real saved-settings folder."""
  def __init__(self, path, cp):
    self.params = Params(str(path))
    self.cp, self.parked = cp, True

  def authority(self, group):
    return self.parked and group == 'parked_preferences'

  def vehicle_params(self):
    return self.cp

  def vehicle_fingerprint(self):
    return getattr(self.cp, 'carFingerprint', None)


def sync_request(feature, value='On', **changes):
  row = next(row for row in feature.rows(True) if row.key == SYNC)
  request = FeatureSettingsRequest(SYNC, row.source, value, confirmation=True, vehicle_fingerprint=feature.vehicle_fingerprint(),
                                   capability=row.capability)
  return replace(request, **changes)


def test_feature_rows_are_the_cable_status_then_the_switch(tmp_path):
  feature = TeslaSetSpeedFeature(Owner(tmp_path, started()))
  cable, sync = feature.rows(True)
  assert (cable.key, cable.label, cable.value, cable.status, cable.links, cable.available) == ('', CABLE_LABEL, 'Detected', True, CABLE_LINKS, False)
  assert (sync.key, sync.label, sync.value, sync.choices, sync.default_value, sync.available) == (SYNC, SYNC_LABEL, 'Off', ('Off', 'On'), 'Off', True)
  assert sync.capability == ('TESLA_MODEL_3', 'tesla', int(started().flags), True, True, (4097,)) and not sync.status and not sync.links
  assert not feature.rows(False)[1].available, 'not parked'


def test_the_feature_says_when_the_comma_owns_the_set_speed(tmp_path):
  owner = Owner(tmp_path, started())
  feature = TeslaSetSpeedFeature(owner)
  assert not feature.active()
  assert feature.apply(sync_request(feature)) and feature.active()
  owner.params.put_bool('SafeMode', True, block=True)
  assert not feature.active()
  owner.params.put_bool('SafeMode', False, block=True)
  owner.cp = tesla()
  assert not feature.active(), 'saved on, but the car started without the scroll frame'


@pytest.mark.parametrize('changes', [{'confirmation': False}, {'value': 'Auto'}, {'value': ''}, {'key': 'TeslaCoopSteering'},
                                     {'expected': b'1'}, {'expected': b'x'}, {'dependencies': (('IsMetric', None),)},
                                     {'related_source': b''}, {'display_unit': 'mph'}, {'direction': 1},
                                     {'vehicle_fingerprint': None}, {'vehicle_fingerprint': 'TESLA_MODEL_Y'}, {'capability': None},
                                     {'capability': ('TESLA_MODEL_3', 'tesla', 4, True, True, (4097,))},
                                     {'capability': ('TESLA_MODEL_3', 'tesla', 12, True, True, (1,))}])
def test_apply_refuses_any_request_that_is_not_exactly_the_row_shown(tmp_path, changes):
  owner = Owner(tmp_path, started())
  feature = TeslaSetSpeedFeature(owner)
  assert not feature.apply(sync_request(feature, **changes))
  assert not owner.params.get_bool(SYNC)
  assert feature.apply(sync_request(feature)) and owner.params.get_bool(SYNC), 'the unchanged request saves'


@pytest.mark.parametrize('reason', ['driving', 'cable', 'longitudinal', 'release', 'restart needed', 'other car'])
def test_apply_refuses_on_when_the_car_or_build_cannot_use_it(tmp_path, reason):
  owner = Owner(tmp_path, started())
  feature = TeslaSetSpeedFeature(owner)
  request = sync_request(feature)
  if reason == 'driving':
    owner.parked = False
  if reason in ('cable', 'longitudinal', 'restart needed'):
    owner.cp = {'cable': lambda: started(cable=False), 'longitudinal': lambda: started(long=False), 'restart needed': tesla}[reason]()
    request = replace(request, capability=feature.capability())
  if reason == 'release':
    owner.params.put_bool('IsReleaseBranch', True, block=True)
  if reason == 'other car':
    owner.cp = other_car('hyundai', 'HYUNDAI_IONIQ_5')
  assert not feature.apply(request)
  assert not owner.params.get_bool(SYNC)


@pytest.mark.parametrize('reason', ['cable', 'longitudinal', 'release', 'restart needed'])
def test_apply_still_lets_a_saved_on_be_turned_off_when_grayed(tmp_path, reason):
  owner = Owner(tmp_path, started())
  feature = TeslaSetSpeedFeature(owner)
  assert feature.apply(sync_request(feature))
  if reason == 'release':
    owner.params.put_bool('IsReleaseBranch', True, block=True)
  else:
    owner.cp = {'cable': lambda: started(cable=False), 'longitudinal': lambda: started(long=False), 'restart needed': tesla}[reason]()
  row = feature.rows(True)[1]
  assert row.value == 'On' and not row.available
  request = FeatureSettingsRequest(SYNC, row.source, 'Off', confirmation=True, vehicle_fingerprint=feature.vehicle_fingerprint(),
                                   capability=row.capability)
  assert feature.apply(request) and not owner.params.get_bool(SYNC)


def device_tap(tmp_path, value):
  """The request a tap on the switch row makes on the device's own screen, and its owner."""
  params = Params(str(tmp_path))
  params.put_bool(SYNC, value == 'Off', block=True)
  cp = started()
  owner = FeatureSettingsOwner(params, lambda group: True, vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
  state = owner.snapshot(FeaturePage.VEHICLE, parked=True, system_long=True, lateral_context=False, metric=False)
  row = next(row for row in state.rows if row.key == SYNC)
  assert row.available
  request = value_request(state, FeatureUiAction('change', row))
  assert request is not None and request.value == value and request.key == SYNC and not request.confirmation
  return params, owner, request


@pytest.mark.parametrize('value', ['On', 'Off'])
def test_a_confirmed_tap_on_the_device_screen_changes_the_switch(tmp_path, value):
  params, owner, request = device_tap(tmp_path, value)
  assert owner.apply(replace(request, confirmation=True))
  assert params.get_bool(SYNC) is (value == 'On')


@pytest.mark.parametrize('value', ['On', 'Off'])
def test_the_switch_can_be_changed_on_the_device_screen(tmp_path, value):
  # What runtime_app and feature_settings_compact do with a tapped row: keys in VEHICLE_BOOL_KEYS get a confirmation
  # dialog (and are then sent confirmed); every other key is sent as tapped.
  params, owner, request = device_tap(tmp_path, value)
  if request.key in VEHICLE_BOOL_KEYS:
    assert confirmation_question(request)
    request = replace(request, confirmation=True)
  assert owner.apply(request)
  assert params.get_bool(SYNC) is (value == 'On')
