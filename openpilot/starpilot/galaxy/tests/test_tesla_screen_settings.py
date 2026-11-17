from dataclasses import replace
import tempfile
import unittest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.values import CAR, CANBUS
from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged, SettingsGateway


def vehicle(*, harness=True, marker=True, bus=1, length=8):
  fingerprint = gen_empty_fingerprint()
  if marker:
    fingerprint[CANBUS.autopilot_party][0x489] = 8
  if harness:
    fingerprint[bus][0x3DF] = length
  return CarInterface.get_params(CAR.TESLA_MODEL_3, fingerprint, [], False, False, False)


class TestTeslaScreenSettings(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    cp = vehicle()
    self.context = AuthorityContext(True, cp, cp.to_bytes())
    self.gateway = SettingsGateway(self.params, self, clock=lambda: 100.0)

  def sample(self) -> AuthorityContext:
    return self.context

  def page(self, page='vehicle'):
    return self.gateway.page(page, 'session', b'generation')

  def intent(self, *, key='Three-Finger AOL Tap', value='On', reset=False):
    page = self.page()
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == key)
    return self.gateway.preview(page['view'], index, 0, 'session', b'generation',
                                value=None if reset else value, reset_default=reset)

  def confirm(self, intent):
    return self.gateway.confirm(intent['intent'], 'session', b'generation')

  def test_detected_harness_exposes_preferences_without_granting_native_permission(self):
    page = self.page()
    self.assertEqual({row['label'] for row in page['rows']}, {'Three-Finger AOL Tap', 'Disengage AOL on Brake', 'Alpha Longitudinal', 'Tesla Co-op Steering'})
    self.assertTrue(all(row['available'] and row['value'] == 'Off' and row['defaultValue'] == 'Off' for row in page['rows']))
    self.assertIn('Vehicle Settings', {row['label'] for row in self.page('hub')['rows']})
    master = next(row for row in self.page('aol')['rows'] if row['label'] == 'Enable Always On Lateral')
    self.assertTrue(master['available'], 'The user must be able to enable AOL before requesting screen gestures')
    for key in ('Three-Finger AOL Tap', 'Disengage AOL on Brake'):
      intent = self.intent(key=key)
      self.assertIn('next startup', intent['question'])
      self.assertTrue(self.confirm(intent))
    self.assertTrue(self.params.get_bool('TeslaAOLScreenTap'))
    self.assertTrue(self.params.get_bool('TeslaAOLDisengageOnBrake'))
    self.assertEqual(self.context.cp.safetyConfigs[0].safetyParam, 0, 'Saving preferences does not mutate live native authority')
    self.gateway = SettingsGateway(self.params, self, clock=lambda: 100.0)
    self.assertTrue(self.confirm(self.intent(reset=True)))
    self.assertFalse(self.params.get_bool('TeslaAOLScreenTap'))
    self.assertTrue(self.params.get_bool('TeslaAOLDisengageOnBrake'))

  def test_absent_wrong_bus_or_unqualified_car_hides_screen_controls(self):
    for options in ({'harness': False}, {'marker': False}, {'bus': 0}, {'length': 7}):
      with self.subTest(options=options):
        cp = vehicle(**options)
        self.context = AuthorityContext(True, cp, cp.to_bytes())
        labels = {row['label'] for row in self.page()['rows']}
        self.assertNotIn('Three-Finger AOL Tap', labels)
        self.assertEqual('Disengage AOL on Brake' in labels, options != {'marker': False})

  def test_stale_vehicle_or_onroad_change_rejects_saved_intent(self):
    intent = self.intent()
    before = self.params.get('TeslaAOLScreenTap')
    self.context = replace(self.context, parked=False)
    self.assertFalse(self.confirm(intent))
    self.assertEqual(self.params.get('TeslaAOLScreenTap'), before)
    self.assertFalse(any(row['available'] for row in self.page()['rows']))
    self.context = replace(self.context, parked=True)
    intent = self.intent()
    cp = vehicle(harness=False)
    self.context = AuthorityContext(True, cp, cp.to_bytes())
    with self.assertRaises(SettingsChanged):
      self.confirm(intent)
    self.assertFalse(self.params.get_bool('TeslaAOLScreenTap'))
