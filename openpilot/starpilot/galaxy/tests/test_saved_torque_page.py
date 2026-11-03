import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import LiveContextSource, SettingsChanged, SettingsGateway
from openpilot.starpilot.lateral.controller_selection import learning_allowed
from openpilot.starpilot.lateral.learning_preferences import read_preference


class TestSavedTorquePage(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.params.put('LateralControllerSelection', {'vehicles': {'HYUNDAI_IONIQ_6': {'brand': 'hyundai', 'mode': 'starpilot'}}, 'version': 1}, block=True)
    self.params.put('TorqueOverrideDocument', {'schemaVersion': 1, 'vehicles': {'HYUNDAI_IONIQ_6': {
      'basis': {'friction': 0.09000000357627869, 'latAccelFactor': 3.0, 'latAccelOffset': 0.0},
      'factor': {'customValue': 2.1, 'mode': 'source'}, 'friction': {'customValue': None, 'mode': 'source'},
    }}}, block=True)
    self.params.put_bool('OpenpilotEnabledToggle', True, block=True)
    self.params.put_bool('AlphaLongitudinalEnabled', True, block=True)
    self.params.put_bool('ForceAutoTuneOff', True, block=True)
    self.context = LiveContextSource(self.params)
    self.gateway = SettingsGateway(self.params, self.context)
    self.addCleanup(self.gateway.close)
    offroad = patch.object(self.context, 'offroad', return_value=True)
    self.offroad = offroad.start()
    self.addCleanup(offroad.stop)

  def page(self, name='torque'):
    return self.gateway.page(name, 'session', b'generation')

  def test_retained_tune_restores_full_flat_page_and_can_save(self):
    page = self.page()
    rows = {row['label']: row for row in page['rows']}
    self.assertEqual(set(rows), {'Turn Signal Guidance', 'Steering Controller', 'Turn Assist', 'Prep My Vehicle for Tuning',
                                'Use StarPilot Auto Learn', 'Lat Accel', 'Lateral acceleration — Reset to Default',
                                'Friction', 'Friction — Reset to Default'})
    aol_rows = {row['label']: row for row in self.page('aol')['rows']}
    for label in ('Pause steering below', 'Steering resume delay', 'Pause only while signaling'):
      self.assertNotIn(label, rows)
      self.assertTrue(aol_rows[label]['available'], label)
    self.assertIn('Saved vehicle settings', page['subtitle'])
    self.assertFalse(any(row['page'] for row in page['rows']))
    for label in ('Steering Controller', 'Turn Assist', 'Prep My Vehicle for Tuning', 'Lat Accel', 'Friction'):
      self.assertTrue(rows[label]['available'], label)
    learning = rows['Use StarPilot Auto Learn']
    self.assertTrue(learning['available'])
    self.assertEqual(learning['value'], 'Off')
    self.assertTrue(learning['reason'].startswith(
      'StarPilot’s FLM tuning will attempt to automatically correct torque profiles as you drive.'))
    self.assertIn('controller eligibility are initialized when driving processes start', learning['reason'])
    cp = self.context.sample().cp
    assert cp is not None
    self.assertFalse(learning_allowed(self.params, cp))
    learning_index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Use StarPilot Auto Learn')
    learning_intent = self.gateway.preview(page['view'], learning_index, 0, 'session', b'generation', value='On')
    self.assertTrue(self.gateway.confirm(learning_intent['intent'], 'session', b'generation'))
    preference = read_preference(self.params, cp)
    self.assertTrue(preference.enabled)
    self.assertEqual(preference.source, 'scope')
    self.assertTrue(self.params.get_bool('ForceAutoTuneOff'))
    self.assertTrue(learning_allowed(self.params, cp), 'Scoped On allows native refinement with either selected controller')
    page = self.page()

    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Friction')
    intent = self.gateway.preview(page['view'], index, 1, 'session', b'generation')
    self.assertTrue(self.gateway.confirm(intent['intent'], 'session', b'generation'))
    self.assertAlmostEqual(self.params.get('TorqueOverrideDocument')['vehicles']['HYUNDAI_IONIQ_6']['friction']['customValue'], .10)
    for key in ('VehicleSelection', 'CarParams', 'CarParamsPersistent', 'CarParamsCache'):
      self.assertFalse(Path(self.params.get_param_path(key)).exists(), key)

  def test_saved_vehicle_configures_all_pages_but_never_onroad(self):
    self.assertTrue(self.gateway._context('torque').editing_saved_vehicle)
    self.assertEqual(self.context.sample().cp.carFingerprint, 'HYUNDAI_IONIQ_6')
    self.assertEqual(self.gateway._context('slc').cp.carFingerprint, 'HYUNDAI_IONIQ_6')
    slc = next(row for row in self.page('slc')['rows'] if row['label'] == 'Confirm limits')
    self.assertTrue(slc['available'])
    self.offroad.return_value = False
    with patch.object(self.context, 'configuration_allowed', return_value=True):
      self.assertIsNone(self.gateway._context('torque').cp)

  def test_pending_edit_is_revoked_when_offroad_context_ends(self):
    page = self.page()
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Friction')
    intent = self.gateway.preview(page['view'], index, 1, 'session', b'generation')
    self.offroad.return_value = False
    with patch.object(self.context, 'configuration_allowed', return_value=True), self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent['intent'], 'session', b'generation')
    self.assertEqual(self.params.get('TorqueOverrideDocument')['vehicles']['HYUNDAI_IONIQ_6']['friction']['mode'], 'source')


  def test_turn_signal_guidance_is_on_and_saves_off_through_galaxy(self):
    from openpilot.starpilot.lateral.lane_change_preferences import read_saved
    page = self.page()
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Turn Signal Guidance')
    self.assertEqual(page['rows'][index]['value'], 'On')
    preview = self.gateway.preview(page['view'], index, 0, 'session', b'generation', value='Off')
    self.assertTrue(self.gateway.confirm(preview['intent'], 'session', b'generation'))
    self.assertFalse(read_saved(self.params).policy.turn_signal_guidance)
