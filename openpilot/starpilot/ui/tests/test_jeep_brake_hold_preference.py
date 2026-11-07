"""Saved Jeep setting reaches exact startup permission and settings revision."""

from dataclasses import replace
import tempfile
import json
import unittest
from unittest.mock import patch
from opendbc.car import gen_empty_fingerprint
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR
from openpilot.common.params import Params, params_put, params_get, _copy_string
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
from openpilot.starpilot.galaxy import vehicle_configuration as configuration
from openpilot.starpilot.vehicle_selection import encode


def put_stored(params, key, value):
  assert type(value) is bytes
  if key == 'VehicleSelection':
    params.put(key, json.loads(value), block=True)
  elif value in (b'0', b'1'):
    params.put_bool(key, value == b'1', block=True)
  else:
    assert params_put(params.p, params.check_key(key), value, len(value), True) == 0


def get_raw(params, key):
  return _copy_string(params_get(params.p, params.check_key(key), False))


class TestJeepBrakeHoldPreference(unittest.TestCase):
  def test_real_owner_saved_setting_and_strict_next_startup(self):
    with tempfile.TemporaryDirectory() as directory:
      saved = Params(directory)
      cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE, gen_empty_fingerprint(), [], False, False, False)
      groups = {'vehicle': True, 'parked_preferences': True}
      owner = FeatureSettingsOwner(saved, lambda group: groups.get(group, True), vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
      row = next(
        row for row in owner.snapshot('vehicle', parked=True, system_long=False, lateral_context=True, metric=False).rows if row.key == 'JeepBrakeHold'
      )
      change = row_change(row)
      assert change is not None
      request = replace(change, confirmation=True)
      for group in groups:
        groups[group] = False
        self.assertFalse(owner.apply(request))
        self.assertIsNone(saved.get('JeepBrakeHold'))
        groups[group] = True
      self.assertTrue(owner.apply(request))
      self.assertEqual(get_raw(saved, 'JeepBrakeHold'), b'1')
      for raw, safe, enabled, word in (
        (b'1', b'0', True, 4),
        (b'0', b'0', True, 0),
        (b'bad', b'0', True, 0),
        (b'1', b'1', True, 0),
        (b'1', b'bad', True, 0),
        (b'1', b'0', False, 0),
      ):
        put_stored(saved, 'JeepBrakeHold', raw)
        put_stored(saved, 'SafeMode', safe)
        target = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE, gen_empty_fingerprint(), [], False, False, False)
        VehicleStartupPreferences.read(saved, enabled=enabled).prepare(target)
        self.assertEqual(target.safetyConfigs[0].safetyParam, word)
        self.assertEqual(get_raw(saved, 'JeepBrakeHold'), raw)
      cp.carFingerprint = CAR.DODGE_DURANGO
      self.assertFalse(owner.apply(request))

  def test_actual_galaxy_startup_interleaving_and_physical_revision(self):
    with tempfile.TemporaryDirectory() as directory:
      saved = Params(directory)
      put_stored(saved, 'VehicleSelection', encode(str(CAR.JEEP_GRAND_CHEROKEE)))
      saved.put_bool('OpenpilotEnabledToggle', True, block=True)
      saved.put_bool('JeepBrakeHold', False, block=True)
      real = configuration._factory_context

      def interleaved(*args):
        result = real(*args)
        saved.put_bool('JeepBrakeHold', True, block=True)
        return result

      with patch.object(configuration, '_factory_context', side_effect=interleaved):
        self.assertEqual(configuration.configuration_context(saved, None, None), (None, None))
      cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE, gen_empty_fingerprint(), [], False, False, False)
      _, first = configuration.configuration_context(saved, cp, b'physical')
      saved.put_bool('JeepBrakeHold', False, block=True)
      _, second = configuration.configuration_context(saved, cp, b'physical')
      self.assertNotEqual(first, second)


  def test_runtime_authority_for_2021_grand_cherokee(self):
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.feature_capabilities import vehicle_preferences_supported

    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, gen_empty_fingerprint(), [], False, False, False)
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session._mode = runtime_app.ShellMode.SETTINGS
    with tempfile.TemporaryDirectory() as directory, patch.object(runtime_app.ui_state, 'CP', cp):
      owner = FeatureSettingsOwner(Params(directory), session._feature_authority,
                                   vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
      with patch.object(session, 'configuration_allowed', return_value=True):
        row = next(row for row in owner.snapshot('vehicle', parked=True, system_long=False,
                                                 lateral_context=True, metric=False).rows if row.key == 'JeepBrakeHold')
        self.assertTrue(row.available)
        change = row_change(row)
        assert change is not None
        self.assertTrue(owner.apply(replace(change, confirmation=True)))
      with patch.object(session, 'configuration_allowed', return_value=False):
        self.assertFalse(owner.apply(replace(change, confirmation=True)))
      cp.passive = True
      self.assertFalse(session._feature_authority('vehicle'))
    cp.passive = False
    cp.carFingerprint = CAR.DODGE_DURANGO
    self.assertFalse(vehicle_preferences_supported(cp))
    self.assertFalse(vehicle_preferences_supported(None))
