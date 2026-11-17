"""Parked shutdown preferences use the shared Galaxy gateway fixture."""
from dataclasses import replace
import unittest

from openpilot.starpilot.galaxy.settings import SettingsChanged
from openpilot.starpilot.galaxy.tests import test_settings as settings_tests


class ParkedPowerSettingsTest(unittest.TestCase):
  setUp = settings_tests.SettingsGatewayTest.setUp
  page = settings_tests.SettingsGatewayTest.page

  def test_parked_power_sliders_preserve_defaults_and_save_policy(self):
    from openpilot.starpilot.power.offroad_preferences import KEY, read_saved, effective
    page = self.page('power')
    self.assertIsNone(self.params.get(KEY))
    self.assertEqual([(r['value'], r['minimum'], r['maximum'], r['step'], r['unit']) for r in page['rows'][1:]],
                     [('30', 1, 30, 1, 'h'), ('11.8', 11.8, 12.5, 0.1, 'V')])
    for index, value in ((1, 6), (2, 12.1), (0, 'On')):
      page = self.page('power')
      intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value=value)
      self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
    policy = effective(read_saved(self.params))
    self.assertEqual((policy.enabled, policy.delay_hours, policy.cutoff_tenths), (True, 6, 121))
    page = self.page('power')
    intent = self.gateway.preview(page['view'], 1, 0, self.session, self.generation, reset_default=True)
    self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
    self.assertEqual(read_saved(self.params).policy.delay_hours, 30)
    page = self.page('power')
    intent = self.gateway.preview(page['view'], 0, 0, self.session, self.generation, value='Stock')
    self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
    self.assertEqual((effective(read_saved(self.params)).delay_hours, effective(read_saved(self.params)).cutoff_tenths), (30, 118))

  def test_parked_power_refuses_invalid_values_stale_source_and_onroad_save(self):
    from openpilot.starpilot.power.offroad_preferences import KEY, to_value, PowerPolicy
    for index, value in ((1, 0), (1, 31), (1, 1.5), (2, 11.7), (2, 12.6), (2, 12.15)):
      page = self.page('power')
      with self.assertRaises(SettingsChanged):
        self.gateway.preview(page['view'], index, 0, self.session, self.generation, value=value)
    page = self.page('power')
    intent = self.gateway.preview(page['view'], 1, 0, self.session, self.generation, value=6)
    self.params.put(KEY, to_value(PowerPolicy(True, 10, 120)), block=True)
    self.assertFalse(self.gateway.confirm(intent['intent'], self.session, self.generation))
    page = self.page('power')
    intent = self.gateway.preview(page['view'], 1, 0, self.session, self.generation, value=6)
    self.context.value = replace(self.context.value, parked=False)
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent['intent'], self.session, self.generation)
    self.assertTrue(all(not row['available'] for row in self.page('power')['rows']))

