from pathlib import Path
import unittest

from opendbc.car.car_helpers import interfaces
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.lateral.delay_preferences import KEY, read_delay, scope
from openpilot.starpilot.lateral.gm_geometry_runtime import GeometryPublicationOwner, geometry_basis
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, FieldChoice, replace_geometry, serialize_document
from openpilot.starpilot.ui.delay_feature import DelayFeature
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest, row_default


class TestSteeringDelay(unittest.TestCase):
  def setUp(self):
    self.prefix = OpenpilotPrefix()
    self.prefix.__enter__()
    self.addCleanup(self.prefix.__exit__, None, None, None)
    self.params = Params()
    self.cp = interfaces['HYUNDAI_IONIQ_6'].get_non_essential_params('HYUNDAI_IONIQ_6')
    self.parked = True
    self.owner = FeatureSettingsOwner(self.params, lambda _: self.parked,
      vehicle_fingerprint=lambda: str(self.cp.carFingerprint), vehicle_params=lambda: self.cp)

  def row(self):
    return DelayFeature(self.owner).row()

  def request(self, value):
    row = self.row()
    return FeatureSettingsRequest(row.key, row.source, value, capability=row.capability,
      vehicle_fingerprint=str(self.cp.carFingerprint), dependencies=row.dependencies)

  def reset_request(self):
    request = row_default(self.row())
    assert request is not None
    return request

  def delay(self, value=.53):
    message = messaging.new_message('lateralDelay', valid=True)
    message.lateralDelay.lateralDelay = value
    message.lateralDelay.status = 'estimated'
    return message

  def test_all_controllers_visible_adjacent_and_default_is_total(self):
    hub = self.owner.snapshot('hub', parked=True, system_long=False, lateral_context=True, metric=False).rows
    lateral = [row.label for row in hub if row.page in ('aol', 'lane_change', 'lane', 'torque', 'torque/developer')]
    self.assertEqual(lateral, ['Always On Lateral', 'Lane Changes', 'Lane Centering', 'Steering and Torque', 'Developer Parameters'])
    for identity in ('HYUNDAI_IONIQ_6', 'HONDA_CIVIC', 'NISSAN_LEAF', 'CHEVROLET_BOLT_CC_2022_2023'):
      self.cp = interfaces[identity].get_non_essential_params(identity)
      rows = self.owner.snapshot('torque/developer', parked=True, system_long=False, lateral_context=True, metric=False).rows
      index = next(i for i, r in enumerate(rows) if r.key == 'steering_delay')
      scalar = [i for i, r in enumerate(rows) if r.key.startswith(('torque:factor:', 'torque:friction:'))]
      if scalar:
        self.assertEqual(index, max(scalar) + 1)
      self.assertAlmostEqual(float(rows[index].value), self.cp.steerActuatorDelay + .2, places=2)
      self.assertEqual(rows[index].unit, 's')
      self.assertTrue(rows[index].available)
      self.assertEqual(self.owner.snapshot('torque/developer', parked=True, system_long=False,
        lateral_context=True, metric=False).title, 'Developer Parameters')
      main = self.owner.snapshot('torque', parked=True, system_long=False, lateral_context=True, metric=False).rows
      self.assertFalse(any(row.key.startswith(('torque:factor:', 'torque:friction:', 'torque:gain:', 'torque:ratio:')) or
                           row.key == 'steering_delay' for row in main))
      self.assertFalse(any(r.key.startswith(('torque:full_delay:', 'torque:automatic_delay:')) for r in rows))
    self.cp.notCar = True
    self.assertIsNone(self.row())

  def test_manual_reset_does_not_change_torque_learning_or_estimator(self):
    self.params.put_bool('ForceAutoTuneOff', False, block=True)
    publisher = GeometryPublicationOwner(self.params, self.cp)
    original = self.delay()
    self.assertIs(publisher.delay(original), original)
    self.assertTrue(self.owner.apply(self.request('.62')))
    publisher.delay_read_ns = None
    result = publisher.delay(original)
    self.assertAlmostEqual(result.lateralDelay.lateralDelay, .62, places=6)
    self.assertEqual(result.lateralDelay.status, original.lateralDelay.status)
    self.assertAlmostEqual(original.lateralDelay.lateralDelay, .53, places=6)
    self.assertFalse(self.params.get_bool('ForceAutoTuneOff'))
    self.assertFalse(Path(self.params.get_param_path('SteeringLearningPreferences')).exists())
    self.assertFalse(Path(self.params.get_param_path(DOCUMENT_KEY)).exists())
    reset = row_default(self.row())
    self.assertIsNotNone(reset)
    assert reset is not None
    self.assertTrue(self.owner.apply(reset))
    publisher.delay_read_ns = None
    self.assertIs(publisher.delay(original), original)
    self.assertTrue(self.owner.apply(self.request(str(scope(self.cp)[-1]))))
    self.assertIsNone(read_delay(self.params, self.cp).value)

  def test_vehicle_controller_and_basis_changes_and_persistence(self):
    self.assertTrue(self.owner.apply(self.request('.61')))
    raw = Path(self.params.get_param_path(KEY)).read_bytes()
    self.assertEqual(read_delay(Params(), self.cp).value, .61)
    prior = self.cp
    self.cp = interfaces['HONDA_CIVIC'].get_non_essential_params('HONDA_CIVIC')
    self.assertIsNone(read_delay(self.params, self.cp).value)
    self.assertTrue(self.owner.apply(self.request('.55')))
    self.assertEqual(read_delay(self.params, prior).value, .61)
    self.cp = prior
    stale = self.request('.6')
    self.cp.steerActuatorDelay += .01
    self.assertEqual(read_delay(self.params, self.cp).source, 'changed')
    self.assertFalse(self.owner.apply(stale))
    self.assertTrue(self.owner.apply(self.reset_request()))
    self.assertIsNone(read_delay(self.params, self.cp).value)
    self.assertIn(b'.61', raw)

  def test_invalid_stale_and_onroad_requests_preserve_data(self):
    for value in ('nan', 'inf', '-.1', '0', '1.01'):
      self.assertFalse(self.owner.apply(self.request(value)), value)
    self.assertTrue(self.owner.apply(self.request('.6')))
    stale = self.request('.7')
    self.assertTrue(self.owner.apply(self.request('.65')))
    self.assertFalse(self.owner.apply(stale))
    raw = Path(self.params.get_param_path(KEY)).read_bytes()
    self.parked = False
    self.assertFalse(self.row().available)
    self.assertFalse(self.owner.apply(self.request('.7')))
    self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), raw)
    self.parked = True
    Path(self.params.get_param_path(KEY)).write_bytes(b'{')
    self.assertFalse(read_delay(self.params, self.cp).valid)
    self.assertFalse(self.owner.apply(self.request('.6')))
    self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), b'{')

  def test_compact_automatic_button_resets_the_same_guarded_delay(self):
    from unittest.mock import Mock, patch
    from openpilot.starpilot.ui.feature_settings_compact import FeatureSettingsCompact
    self.assertTrue(self.owner.apply(self.request('.6')))
    class Button:
      def __init__(self, *args):
        self.label = args[0]
      def set_click_callback(self, callback):
        self.callback = callback
    adapter = FeatureSettingsCompact(Mock(feature_request=self.owner.apply))
    refreshed = []
    with patch('openpilot.starpilot.ui.feature_settings_compact.BigButton', Button):
      buttons = adapter._editable(self.row(), lambda: refreshed.append(True), Mock())
    self.assertEqual(len(buttons), 3)
    self.assertIn('automatic', buttons[-1].label)
    buttons[-1].callback()
    self.assertIsNone(read_delay(self.params, self.cp).value)
    self.assertEqual(refreshed, [True])

  def test_existing_gm_delay_and_ratio_are_preserved_until_explicit_reset(self):
    from openpilot.starpilot.lateral.tests.test_volt_torque_policy import params
    from opendbc.car.gm.values import CAR
    self.cp = params(CAR.CHEVROLET_VOLT_CAMERA)
    tune = self.cp.lateralTuning.torque
    basis = (tune.latAccelFactor, tune.latAccelOffset, tune.friction)
    profiles = replace_geometry({}, str(self.cp.carFingerprint), basis, geometry_basis(self.cp), 'full_delay', FieldChoice('custom', .7))
    profiles = replace_geometry(profiles, str(self.cp.carFingerprint), basis, geometry_basis(self.cp), 'automatic_delay', False)
    profiles = replace_geometry(profiles, str(self.cp.carFingerprint), basis, geometry_basis(self.cp), 'ratio', FieldChoice('custom', self.cp.steerRatio * 1.1))
    raw = serialize_document(profiles)
    Path(self.params.get_param_path(DOCUMENT_KEY)).write_bytes(raw)
    publisher = GeometryPublicationOwner(self.params, self.cp)
    original = self.delay()
    self.assertEqual(read_delay(self.params, self.cp).source, 'legacy')
    self.assertAlmostEqual(publisher.delay(original).lateralDelay.lateralDelay, .7, places=6)
    self.assertTrue(self.owner.apply(self.reset_request()))
    publisher.delay_read_ns = None
    self.assertIs(publisher.delay(original), original)
    self.assertEqual(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes(), raw)
    self.assertEqual(read_delay(self.params, self.cp).source, 'automatic')


if __name__ == '__main__':
  unittest.main()
