from dataclasses import replace

import pytest
from openpilot.selfdrive.controls.lib.highway_correction_gain import KEY, read_gain
from openpilot.starpilot.lateral.tests.test_lane_runtime import ioniq_candidate
from openpilot.starpilot.saved_source import read_saved

from openpilot.starpilot.audio.alert_volume import AUTO, SPECS
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from unittest import TestCase

from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged
from openpilot.starpilot.galaxy.tests import test_settings as fixtures
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, row_default


class TestDefaultRequest(TestCase):
  def test_request_keeps_owner_sources_and_uses_declared_action(self):
    row = FeatureRow('torque:friction:value', 'Friction', '.2', b'saved', available=True,
                     related_source=b'related', vehicle_fingerprint='vehicle', capability=('capability',),
                     dependencies=(('dependency', b'old'),), display_unit='mph',
                     default_value='Vehicle/learned', default_key='torque:friction:mode')
    request = row_default(row)
    self.assertEqual((request.key, request.value, request.expected), ('torque:friction:mode', 'Vehicle/learned', b'saved'))
    self.assertEqual((request.related_source, request.vehicle_fingerprint, request.capability, request.dependencies, request.display_unit),
                     (row.related_source, row.vehicle_fingerprint, row.capability, row.dependencies, row.display_unit))
    self.assertTrue(request.confirmation)
    for changed in (replace(row, available=False), replace(row, page='child'), replace(row, key=''), replace(row, default_value=None)):
      self.assertIsNone(row_default(changed))


class TestSettingsDefaults(TestCase):
  setUp = fixtures.SettingsGatewayTest.setUp
  page = fixtures.SettingsGatewayTest.page

  def reset(self, page_name, label):
    page = self.page(page_name)
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == label)
    row = page['rows'][index]
    self.assertTrue(row['resetAvailable'], row)
    intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, reset_default=True)
    return row, intent

  def confirm(self, intent):
    return self.gateway.confirm(intent['intent'], self.session, self.generation)

  def test_torque_sliders_keep_defaults_without_duplicate_reset_rows(self):
    _, cp = ioniq_candidate()
    self.context.value = AuthorityContext(True, cp, cp.as_reader().as_builder().to_bytes())
    for label in ('Lat Accel', 'Friction'):
      page = self.page('torque')
      self.assertFalse(any('Reset to Default' in row['label'] for row in page['rows']))
      index = next(i for i, row in enumerate(page['rows']) if row['label'] == label)
      row = page['rows'][index]
      previous = float(row['value'])
      direction = -1 if previous >= row['maximum'] else 1
      intent = self.gateway.preview(page['view'], index, direction, self.session, self.generation)
      self.assertTrue(self.confirm(intent))
      row, intent = self.reset('torque', label)
      self.assertEqual(row['defaultValue'], 'Vehicle/learned')
      self.assertTrue(self.confirm(intent))
      restored = next(row for row in self.page('torque')['rows'] if row['label'] == label)
      self.assertAlmostEqual(float(restored['value']), previous)

  def test_tuning_preparation_switch_saves_state_and_restores_preferences(self):
    from openpilot.starpilot.lateral.tests.test_lane_runtime import ioniq_candidate
    _, cp = ioniq_candidate()
    self.context.value = AuthorityContext(True, cp, cp.as_reader().as_builder().to_bytes())
    self.params.put_bool('TurnAssist', True, block=True)
    for desired in ('On', 'Off'):
      page = self.page('torque')
      index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Prep My Vehicle for Tuning')
      row = page['rows'][index]
      self.assertEqual(row['choices'], ['Off', 'On'])
      self.assertFalse(row['confirm'])
      self.assertEqual(row['value'], 'Off' if desired == 'On' else 'On')
      intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value=desired)
      self.assertTrue(self.confirm(intent))
      current = next(row for row in self.page('torque')['rows'] if row['label'] == 'Prep My Vehicle for Tuning')
      self.assertEqual(current['value'], desired)
      self.assertEqual(self.params.get_bool('TurnAssist'), desired == 'Off')
    self.context.value = replace(self.context.value, parked=False)
    page = self.page('torque')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Prep My Vehicle for Tuning')
    self.assertFalse(page['rows'][index]['available'])
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page['view'], index, 0, self.session, self.generation, value='On')

  def test_boolean_and_numeric_defaults_are_owner_values(self):
    self.params.put_bool('LaneCentering', True, block=True)
    self.params.put_bool('LaneCenteringPauseOnSignal', False, block=True)
    row, intent = self.reset('lane', 'Pause on signal')
    self.assertEqual(row['defaultValue'], 'On')
    self.assertFalse(self.params.get_bool('LaneCenteringPauseOnSignal'))
    self.assertTrue(self.confirm(intent))
    self.assertTrue(self.params.get_bool('LaneCenteringPauseOnSignal'))

  def test_paired_appearance_reset_updates_both_flags(self):
    self.params.put_bool('ShowBrakeStatus', True, block=True)
    self.params.put_bool('PedalsOnUI', True, block=True)
    row, intent = self.reset('appearance', 'Wheel brake and acceleration colors')
    self.assertEqual(row['defaultValue'], 'Off')
    self.assertTrue(self.confirm(intent))
    self.assertFalse(self.params.get_bool('ShowBrakeStatus'))
    self.assertFalse(self.params.get_bool('PedalsOnUI'))

  def test_paired_dependency_changes_revoke_pending_reset(self):
    self.params.put_bool('ShowBrakeStatus', True, block=True)
    self.params.put_bool('PedalsOnUI', True, block=True)
    _, intent = self.reset('appearance', 'Wheel brake and acceleration colors')
    self.params.put_bool('PedalsOnUI', False, block=True)
    with self.assertRaises(SettingsChanged):
      self.confirm(intent)
    self.assertTrue(self.params.get_bool('ShowBrakeStatus'))

  def test_enum_and_inverted_boolean_reset_through_owner(self):
    self.params.put('CameraView', 0, block=True)
    row, intent = self.reset('appearance', 'Camera View')
    self.assertEqual(row['defaultValue'], 'Standard')
    self.assertTrue(self.confirm(intent))
    self.assertEqual(self.params.get('CameraView'), CameraViewChoice.STANDARD)
    self.params.put_bool('StockConfidenceBallWidget', True, block=True)
    row, intent = self.reset('appearance', 'Use StarPilot Widgets')
    self.assertEqual(row['defaultValue'], 'On')
    self.assertTrue(self.confirm(intent))
    self.assertFalse(self.params.get_bool('StockConfidenceBallWidget'))

  def test_sound_auto_default_retains_encoding_and_other_volumes(self):
    self.params.put('EngageVolume', 65, block=True)
    self.params.put('DisengageVolume', 80, block=True)
    label = SPECS['EngageVolume'][0]
    row, intent = self.reset('sounds', label)
    self.assertEqual(row['defaultValue'], 'Auto')
    self.assertTrue(self.confirm(intent))
    self.assertEqual(self.params.get('EngageVolume'), AUTO)
    self.assertEqual(self.params.get('DisengageVolume'), 80)

  def test_reset_cannot_supply_value_or_cross_session(self):
    page = self.page('appearance')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Use StarPilot Widgets')
    for kwargs in ({'value': 'Off'}, {'draft': {}}, {'value': True}):
      with self.assertRaises(SettingsChanged):
        self.gateway.preview(page['view'], index, 0, self.session, self.generation, reset_default=True, **kwargs)
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page['view'], index, 0, 'other-session', self.generation, reset_default=True)
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page['view'], index, 0, self.session, b'new-generation', reset_default=True)

  def test_vehicle_change_and_logout_revoke_reset(self):
    _, intent = self.reset('appearance', 'Use StarPilot Widgets')
    self.context.value = AuthorityContext(True, self.context.value.cp, b'changed-car')
    with self.assertRaises(SettingsChanged):
      self.confirm(intent)
    _, intent = self.reset('appearance', 'Use StarPilot Widgets')
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent['intent'], self.session, self.generation, session_valid=lambda: False)

  def test_no_defaults_on_navigation_or_unavailable_feature(self):
    cp = fixtures.ToyotaInterface.get_non_essential_params(fixtures.ToyotaCar.TOYOTA_COROLLA_TSS2)
    for name, value in vars(self.context.value.cp).items():
      if name not in ("lateralTuning", "safetyConfigs"):
        setattr(cp, name, value)
    self.assertEqual(cp.lateralTuning.to_dict(), self.context.value.cp.lateralTuning.to_dict())
    self.assertEqual([row.to_dict() for row in cp.safetyConfigs], [row.to_dict() for row in self.context.value.cp.safetyConfigs])
    self.context.value = replace(self.context.value, cp=cp)
    for row in self.page('hub')['rows']:
      self.assertIsNone(row['defaultValue'])
      self.assertFalse(row['resetAvailable'])
    self.context.value = AuthorityContext(True, None, None)
    page = self.page('slc')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Speed Limit Controller')
    self.assertFalse(page['rows'][index]['resetAvailable'])
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page['view'], index, 0, self.session, self.generation, reset_default=True)


class TestSettingsDefaultHttp(TestCase):
  setUp = fixtures.SettingsHttpTest.setUp
  stop = fixtures.SettingsHttpTest.stop
  request = fixtures.SettingsHttpTest.request
  login = fixtures.SettingsHttpTest.login

  def test_authenticated_reset_intent_requires_confirmation(self):
    self.params.put_bool('ShowBrakeStatus', True, block=True)
    cookie = self.login()
    status, page, _ = self.request('/api/settings/pages/appearance', cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Wheel brake and acceleration colors')
    payload = {'view': page['view'], 'row': index}
    self.assertEqual(self.request('/api/settings/reset-default', payload=payload)[0], 401)
    for invalid in (payload | {'value': 'On'}, payload | {'key': 'ShowBrakeStatus'}, payload | {'row': True}):
      self.assertEqual(self.request('/api/settings/reset-default', cookie=cookie, payload=invalid)[0], 400)
    self.assertNotEqual(self.request('/api/settings/reset-default', cookie=cookie, payload=payload, origin='https://elsewhere.example')[0], 200)
    status, intent, _ = self.request('/api/settings/reset-default', cookie=cookie, payload=payload)
    self.assertEqual(status, 200)
    self.assertIn('default', intent['question'])
    self.assertTrue(self.params.get_bool('ShowBrakeStatus'))
    body = {'intent': intent['intent'], 'confirmed': True}
    self.assertEqual(self.request('/api/settings/confirm', cookie=cookie, payload=body)[:2], (200, {'saved': True}))
    self.assertFalse(self.params.get_bool('ShowBrakeStatus'))
    self.assertEqual(self.request('/api/settings/confirm', cookie=cookie, payload=body)[0], 409)






@pytest.fixture
def gateway():
  fixture = fixtures.SettingsGatewayTest()
  fixture.setUp()
  _, cp = ioniq_candidate()
  fixture.context.value = AuthorityContext(True, cp, cp.as_reader().as_builder().to_bytes())
  try:
    yield fixture
  finally:
    fixture.doCleanups()


def slider(fixture):
  page = fixture.page('torque')
  index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Highway Smoothing')
  return page, index, page['rows'][index]


def test_default_off_at_left(gateway):
  _, _, row = slider(gateway)
  assert (row['value'], row['defaultValue'], row['minimum'], row['maximum'], row['unit']) == ('0', '0', 0, 100, '%')
  assert row['step'] == 1 and row['resetAvailable']
  assert '0% is off' in row['reason'] and 'higher values add more smoothing' in row['reason']
  assert read_gain(gateway.params) == 1
  assert read_saved(gateway.params, KEY, 16)[0] is None


@pytest.mark.parametrize(('saved', 'shown'), [(1., '100'), (3., '78'), (7., '33'), (10., '0')])
def test_saved_effect_is_preserved_without_migration(gateway, saved, shown):
  gateway.params.put(KEY, saved, block=True)
  before = read_saved(gateway.params, KEY, 16)[0]
  assert slider(gateway)[2]['value'] == shown
  assert read_gain(gateway.params) == saved / 10
  assert read_saved(gateway.params, KEY, 16)[0] == before


@pytest.mark.parametrize(('strength', 'gain'), [(0, 1.), (50, .55), (100, .1)])
def test_higher_strength_saves_more_smoothing_and_default_disables(gateway, strength, gain):
  page, index, _ = slider(gateway)
  intent = gateway.gateway.preview(page['view'], index, 0, gateway.session, gateway.generation, value=strength)
  assert gateway.gateway.confirm(intent['intent'], gateway.session, gateway.generation)
  assert read_gain(gateway.params) == pytest.approx(gain)
  assert slider(gateway)[2]['value'] == str(strength)
  page, index, _ = slider(gateway)
  intent = gateway.gateway.preview(page['view'], index, 0, gateway.session, gateway.generation, reset_default=True)
  assert gateway.gateway.confirm(intent['intent'], gateway.session, gateway.generation)
  assert read_gain(gateway.params) == 1
  assert slider(gateway)[2]['value'] == '0'


@pytest.mark.parametrize('value', [-1, 101, float('nan'), float('inf')])
def test_invalid_strength_is_refused(gateway, value):
  page, index, _ = slider(gateway)
  with pytest.raises(SettingsChanged):
    gateway.gateway.preview(page['view'], index, 0, gateway.session, gateway.generation, value=value)
  assert read_saved(gateway.params, KEY, 16)[0] is None


def test_stale_source_cannot_overwrite_a_newer_setting(gateway):
  page, index, _ = slider(gateway)
  intent = gateway.gateway.preview(page['view'], index, 0, gateway.session, gateway.generation, value=50)
  gateway.params.put(KEY, 3., block=True)
  try:
    assert not gateway.gateway.confirm(intent['intent'], gateway.session, gateway.generation)
  except SettingsChanged:
    pass
  assert read_gain(gateway.params) == .3
