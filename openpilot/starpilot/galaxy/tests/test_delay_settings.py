from pathlib import Path
from unittest import TestCase

from opendbc.car.car_helpers import interfaces
from openpilot.starpilot.galaxy.settings import AuthorityContext
from openpilot.starpilot.galaxy.tests import test_settings as fixtures
from openpilot.starpilot.lateral.delay_preferences import KEY, read_delay


class TestDelaySettings(TestCase):
  setUp = fixtures.SettingsGatewayTest.setUp
  page = fixtures.SettingsGatewayTest.page

  def test_longitudinal_developer_save_reset_and_vehicle_scope(self):
    from openpilot.starpilot.longitudinal.developer_tuning import FIELDS, read_tuning
    cp = interfaces['HONDA_CIVIC'].get_non_essential_params('HONDA_CIVIC')
    assert cp.openpilotLongitudinalControl
    self.context.value = AuthorityContext(True, cp, cp.to_bytes())
    self.assertIn('profiles/developer', [row.get('page') for row in self.page('hub')['rows']])
    for key, (label, _, minimum, maximum, _, _) in FIELDS.items():
      page = self.page('profiles/developer')
      index = next(i for i, row in enumerate(page['rows']) if row['label'] == label)
      chosen = round((minimum + maximum) / 2, 2)
      intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value=chosen)
      self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
      self.assertAlmostEqual(read_tuning(self.params, cp)[key], chosen)
      changed = cp.as_reader().as_builder()
      changed.flags ^= 1
      self.assertEqual(read_tuning(self.params, changed), {})
      page = self.page('profiles/developer')
      intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, reset_default=True)
      self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
      self.assertEqual(read_tuning(self.params, cp), {})

  def test_all_controllers_actual_gateway_save_reset_and_menu(self):
    for identity in ('HYUNDAI_IONIQ_6', 'HONDA_CIVIC', 'NISSAN_LEAF'):
      cp = interfaces[identity].get_non_essential_params(identity)
      self.context.value = AuthorityContext(True, cp, cp.to_bytes())
      hub = self.page('hub')
      lateral = [row['label'] for row in hub['rows'] if row.get('page') in ('aol', 'lane_change', 'lane', 'torque', 'torque/developer')]
      self.assertEqual(lateral, ['Always On Lateral', 'Lane Changes', 'Lane Centering', 'Steering & Torque', 'Developer Parameters'])
      page = self.page('torque/developer')
      self.assertEqual(page['title'], 'Developer Parameters')
      index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Actuator delay')
      self.assertEqual(page['rows'][index]['unit'], 's')
      self.assertTrue(page['rows'][index]['available'])
      previous = Path(self.params.get_param_path('TorqueOverrideDocument'))
      before = previous.read_bytes() if previous.exists() else None
      intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value=.6)
      self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
      self.assertEqual(read_delay(self.params, cp).value, .6)
      self.assertEqual(previous.read_bytes() if previous.exists() else None, before)
      page = self.page('torque/developer')
      intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, reset_default=True)
      self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
      self.assertIsNone(read_delay(self.params, cp).value)
      self.assertNotIn('Actuator delay', [row['label'] for row in self.page('torque')['rows']])
    self.assertTrue(Path(self.params.get_param_path(KEY)).exists())
