import json
import tempfile
import unittest
from pathlib import Path

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.vehicle_configuration import configuration_context
from openpilot.starpilot.vehicle_selection import encode
from openpilot.starpilot.lateral.torque_runtime import production_supported_cp


class TestVehicleConfiguration(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)

  def select(self, identity):
    self.params.put('VehicleSelection', json.loads(encode(identity)), block=True)

  def test_ioniq_configuration_without_cache_or_vehicle(self):
    self.select('HYUNDAI_IONIQ_6')
    for alpha in (False, True):
      self.params.put_bool('AlphaLongitudinalEnabled', alpha, block=True)
      cp, token = configuration_context(self.params, None, None)
      self.assertEqual(cp.carFingerprint, 'HYUNDAI_IONIQ_6')
      self.assertTrue(production_supported_cp(cp))
      self.assertTrue(token.startswith(b'vehicle-configuration:'))
      for key in ('CarParams', 'CarParamsPersistent', 'CarParamsCache'):
        self.assertFalse(Path(self.params.get_param_path(key)).exists())

  def test_changed_selection_and_preferences_change_token(self):
    self.select('HYUNDAI_IONIQ_6')
    _, first = configuration_context(self.params, None, None)
    self.params.put_bool('AlphaLongitudinalEnabled', True, block=True)
    _, second = configuration_context(self.params, None, None)
    self.assertNotEqual(first, second)
    self.select('CHEVROLET_BOLT_CC_2018_2021')
    cp, third = configuration_context(self.params, None, None)
    self.assertEqual(cp.carFingerprint, 'CHEVROLET_BOLT_CC_2018_2021')
    self.assertNotEqual(second, third)
    from opendbc.car.gm.values import GMFlags
    self.assertFalse(cp.flags & GMFlags.PEDAL_LONG)

  def test_passive_physical_configuration_is_only_cloned_for_saved_editor(self):
    self.select('HYUNDAI_IONIQ_6')
    cp, _ = configuration_context(self.params, None, None)
    physical = cp.as_builder()
    physical.passive = True
    returned, token = configuration_context(self.params, physical.as_reader(), b'physical-cache')
    self.assertFalse(returned.passive)
    self.assertTrue(physical.passive)
    self.assertTrue(token.endswith(b'physical-cache'))
    physical.dashcamOnly = True
    refused, _ = configuration_context(self.params, physical.as_reader(), b'unsupported-cache')
    self.assertTrue(refused.dashcamOnly)

  def test_matching_physical_configuration_is_preserved(self):
    self.select('HYUNDAI_IONIQ_6')
    cp, _ = configuration_context(self.params, None, None)
    returned, token = configuration_context(self.params, cp, b'physical-cache')
    self.assertIs(returned, cp)
    self.assertTrue(token.endswith(b'physical-cache'))

  def test_camera_interceptor_manual_choice_does_not_invent_hardware(self):
    from opendbc.car.gm.tests.test_camera_acc_pedal import CAMERA_IDS
    from opendbc.car.gm.values import CAR, GMFlags, camera_acc_pedal_profile
    from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
    from openpilot.starpilot.ui.feature_settings_state import row_change
    from dataclasses import replace
    self.params.put_bool('OpenpilotEnabledToggle', True, block=True)
    for identity in (*CAMERA_IDS, CAR.CHEVROLET_VOLT_CAMERA):
      self.select(str(identity))
      cp, token = configuration_context(self.params, None, None)
      self.assertEqual(cp.carFingerprint, identity)
      self.assertIsNotNone(token)
      self.assertIsNone(camera_acc_pedal_profile(cp))
      self.assertFalse(cp.flags & GMFlags.PEDAL_LONG)
      owner = FeatureSettingsOwner(self.params, lambda _: True, vehicle_fingerprint=lambda identity=identity: identity,
                                   vehicle_params=lambda cp=cp: cp, configuration_vehicle=lambda: True)
      page = owner.snapshot('vehicle', parked=True, system_long=False, lateral_context=True, metric=False)
      pedal = next(row for row in page.rows if row.key == 'GMPedalLongitudinal')
      self.assertEqual(pedal.value, 'Not detected')
      self.assertFalse(pedal.available)
      pitch = next(row for row in page.rows if row.key == 'LongPitch')
      request = row_change(pitch)
      assert request is not None
      self.assertTrue(owner.apply(replace(request, confirmation=True)))
      self.assertIsNone(camera_acc_pedal_profile(cp))
      for key in ('CarParams', 'CarParamsPersistent', 'CarParamsCache'):
        self.assertFalse(Path(self.params.get_param_path(key)).exists())

  def test_auto_preserves_physical_and_malformed_fails_closed(self):
    self.select(None)
    physical = object()
    self.assertEqual(configuration_context(self.params, physical, b'cache'), (physical, b'cache'))
    self.params.put('VehicleSelection', {}, block=True)
    self.assertEqual(configuration_context(self.params, physical, b'cache'), (None, None))

  def test_actual_context_only_uses_manual_selection_effectively_offroad(self):
    from unittest.mock import patch
    from openpilot.starpilot.galaxy.settings import LiveContextSource
    self.select('HYUNDAI_IONIQ_6')
    context = LiveContextSource(self.params)
    self.addCleanup(context.close)
    with patch.object(context, 'configuration_allowed', return_value=True), patch.object(context, 'offroad', return_value=False):
      self.assertIsNone(context.sample().cp)
    with patch.object(context, 'configuration_allowed', return_value=True), patch.object(context, 'offroad', return_value=True):
      self.assertEqual(context.sample().cp.carFingerprint, 'HYUNDAI_IONIQ_6')

  def test_actual_settings_view_invalidated_by_selection_change(self):
    from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway
    self.select('HYUNDAI_IONIQ_6')
    params = self.params

    class Context:
      def sample(self):
        cp, token = configuration_context(params, None, None)
        return AuthorityContext(True, cp, token)

    gateway = SettingsGateway(params, Context())
    self.addCleanup(gateway.close)
    ctx = gateway.context.sample()
    owner = gateway._owner(ctx, live=True)
    self.assertTrue(owner.authority('torque'))
    self.select('CHEVROLET_BOLT_CC_2018_2021')
    self.assertFalse(owner.authority('torque'))

  def test_manual_selection_exposes_and_saves_real_torque_sliders(self):
    from unittest.mock import patch
    from openpilot.starpilot.galaxy.settings import LiveContextSource, SettingsGateway
    from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY
    self.select('HYUNDAI_IONIQ_6')
    context = LiveContextSource(self.params)
    gateway = SettingsGateway(self.params, context)
    self.addCleanup(gateway.close)
    with patch.object(context, 'offroad', return_value=True):
      page = gateway.page('torque', 'local', b'generation')
      self.assertFalse(any(row['label'] in ('Lat Accel', 'Friction', 'Actuator delay') for row in page['rows']))
      page = gateway.page('torque/developer', 'local', b'generation')
      friction = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Friction')
      self.assertTrue(page['rows'][friction]['available'])
      self.assertTrue(any(row['label'] == 'Lat Accel' and row['available'] for row in page['rows']))
      current = float(page['rows'][friction]['value'])
      direction = -1 if current >= page['rows'][friction]['maximum'] else 1
      intent = gateway.preview(page['view'], friction, direction, 'local', b'generation')
      self.assertTrue(gateway.confirm(intent['intent'], 'local', b'generation'))
      self.assertIsNotNone(self.params.get(DOCUMENT_KEY))
      for key in ('CarParams', 'CarParamsPersistent', 'CarParamsCache'):
        self.assertFalse(Path(self.params.get_param_path(key)).exists())

  def test_all_configuration_dependencies_are_registered(self):
    from openpilot.starpilot.galaxy.vehicle_configuration import STARTUP_KEYS
    for key in STARTUP_KEYS:
      with self.subTest(key=key):
        self.params.get_type(key)
