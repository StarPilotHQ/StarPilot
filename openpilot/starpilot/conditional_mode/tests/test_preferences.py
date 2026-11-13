"""Strict conditional-mode document and read-only frozen-Params migration tests."""

from dataclasses import replace
import json
import math
import unittest

from openpilot.starpilot.conditional_mode.policy import ModeChoice, ModeSettings
from openpilot.starpilot.conditional_mode.preferences import (
  CCMOptions,
  CEMOptions,
  MAX_DOCUMENT_BYTES,
  PreferenceError,
  SavedPreferences,
  decode_preferences,
  encode_preferences,
  manual_for_drive,
  propose_legacy_adoption,
  saved_selection_for_drive,
  selection_for_drive,
)


class ConditionalModePreferencesTest(unittest.TestCase):
  def test_factory_default_is_cem_and_explicit_stock_round_trip_is_canonical(self):
    default = SavedPreferences()
    self.assertIs(default.mode, ModeChoice.CEM)
    self.assertEqual(default.cem, CEMOptions())
    self.assertEqual(default.ccm, CCMOptions())
    raw = encode_preferences(default)
    self.assertEqual(decode_preferences(raw), default)
    self.assertEqual(encode_preferences(decode_preferences(raw)), raw)
    self.assertTrue(raw.startswith(b'{"ccm":'))
    self.assertNotIn(b'SafeMode', raw)
    self.assertLess(len(raw), MAX_DOCUMENT_BYTES)
    stock = SavedPreferences(mode=ModeChoice.STOCK)
    self.assertIs(decode_preferences(encode_preferences(stock)).mode, ModeChoice.STOCK)

  def test_all_options_survive_and_policy_projection_is_explicit(self):
    preferences = SavedPreferences(
      mode=ModeChoice.CEM,
      cem=CEMOptions(speed_mps=10.5, speed_with_lead_mps=7.0, signal_speed_mps=5.0,
                     open_road=True, curves=True, curves_with_lead=True, lead=True,
                     slower_lead=False, stopped_lead=True, stop_lights=False,
                     model_stop_s=6.5, signal_lane_detection=False,
                     signal_lane_width_m=3.7, persist_manual=True),
      ccm=CCMOptions(speed_mps=20.0, speed_with_lead_mps=15.0,
                     set_speed_margin_mps=2.0, lead=False, launch_assist=True,
                     persist_manual=True),
    )
    self.assertEqual(decode_preferences(encode_preferences(preferences)), preferences)
    settings = preferences.mode_settings()
    self.assertIsInstance(settings, ModeSettings)
    self.assertEqual((settings.cem_speed_mps, settings.cem_speed_with_lead_mps,
                      settings.cem_signal_mps), (10.5, 7.0, 5.0))
    self.assertFalse(settings.cem_stop)  # CEStopLights gates CEModelStopTime.
    self.assertEqual((settings.ccm_speed_mps, settings.ccm_speed_with_lead_mps,
                      settings.ccm_set_speed_margin_mps), (20.0, 15.0, 2.0))
    self.assertFalse(settings.ccm_lead)
    self.assertTrue(settings.ccm_launch)
    self.assertFalse(replace(preferences, cem=replace(preferences.cem, model_stop_s=0.0)).mode_settings().cem_stop)

  def test_drive_binding_preserves_existing_typed_api_and_does_not_grant_authority(self):
    preferences = SavedPreferences(mode=ModeChoice.CCM)
    selection = saved_selection_for_drive(preferences, 42)
    self.assertEqual(selection, selection_for_drive(ModeChoice.CEM, preferences.mode_settings(), 42))
    self.assertIsNone(saved_selection_for_drive(preferences, 0))
    self.assertIsNone(saved_selection_for_drive(preferences, True))
    self.assertIsNone(manual_for_drive('force_chill', 42, 100))
    self.assertFalse(hasattr(selection, 'safe_mode'))
    self.assertFalse(hasattr(selection, 'system_long_capable'))

  def test_malformed_document_never_falls_back_or_silently_rewrites(self):
    good = json.loads(encode_preferences(SavedPreferences()))
    invalid = []
    for key in ('version', 'mode', 'cem', 'ccm'):
      document = dict(good)
      document.pop(key)
      invalid.append(document)
    for key, value in (('extra', 1), ('version', 2), ('version', True),
                       ('mode', 'conditional_chill_unknown'), ('mode', 1)):
      invalid.append(dict(good, **{key: value}))
    for section, name, value in (
      ('cem', 'speed_mps', True), ('cem', 'speed_mps', -1),
      ('cem', 'speed_mps', 50.0), ('cem', 'model_stop_s', 9.1),
      ('cem', 'signal_lane_width_m', 15.1), ('cem', 'open_road', 1),
      ('ccm', 'set_speed_margin_mps', 9.0), ('ccm', 'lead', 'false'),
    ):
      document = json.loads(json.dumps(good))
      document[section][name] = value
      invalid.append(document)
    missing = json.loads(json.dumps(good))
    del missing['cem']['slower_lead']
    invalid.append(missing)
    extra = json.loads(json.dumps(good))
    extra['ccm']['unknown'] = False
    invalid.append(extra)
    for document in invalid:
      with self.subTest(document=document):
        with self.assertRaises(PreferenceError):
          decode_preferences(json.dumps(document))
    for raw in (b'\xff', '{"version":1,"version":1}',
                '{"value":NaN}', b'x' * (MAX_DOCUMENT_BYTES + 1),
                'not-json', ''):
      with self.subTest(raw=raw):
        with self.assertRaises(PreferenceError):
          decode_preferences(raw)
    with self.assertRaises(PreferenceError):
      encode_preferences(replace(SavedPreferences(), cem=replace(CEMOptions(), speed_mps=math.nan)))
    with self.assertRaises(PreferenceError):
      encode_preferences(replace(SavedPreferences(), ccm=replace(CCMOptions(), speed_mps=10**350)))

  def test_legacy_adoption_requires_explicit_mode_bits_and_unit_system(self):
    # Frozen params_keys.h stores numeric values in display units; old
    # starpilot_variables.py multiplied by MPH_TO_MS/KPH_TO_MS at runtime.
    imperial = propose_legacy_adoption({
      'ConditionalExperimental': b'1', 'ConditionalChill': b'0',
      'CESpeed': b'25.0', 'CESpeedLead': b'15.0', 'CESignalSpeed': b'12.0',
      'CCMSpeed': b'45.0', 'CCMSetSpeedMargin': b'3.0',
      'CEModelStopTime': b'7.7', 'CEStopLights': b'1',
      'CESlowerLead': b'0', 'CEStoppedLead': b'1',
      'CESignalLaneDetection': b'1', 'LaneDetectionWidth': b'12.0',
      'PersistExperimentalState': b'1', 'PersistChillState': b'0',
    }, units='imperial')
    self.assertIs(imperial.preferences.mode, ModeChoice.CEM)
    self.assertAlmostEqual(imperial.preferences.cem.speed_mps, 25 * 0.44704)
    self.assertAlmostEqual(imperial.preferences.cem.signal_lane_width_m, 12 * 0.3048)
    self.assertEqual(imperial.preferences.cem.model_stop_s, 7.7)
    self.assertFalse(imperial.preferences.cem.slower_lead)
    self.assertTrue(imperial.preferences.cem.stopped_lead)
    self.assertTrue(imperial.preferences.cem.persist_manual)
    self.assertFalse(imperial.preferences.ccm.persist_manual)
    self.assertIn('CESpeed', imperial.source_keys)
    self.assertEqual(decode_preferences(encode_preferences(imperial.preferences)), imperial.preferences)

    metric = propose_legacy_adoption({
      'ConditionalExperimental': False, 'ConditionalChill': True,
      'CCMSpeed': '72.0', 'CCMSpeedLead': '54.0',
      'CCMSetSpeedMargin': '10.0', 'LaneDetectionWidth': '3.5',
    }, units='metric')
    self.assertIs(metric.preferences.mode, ModeChoice.CCM)
    self.assertEqual(metric.preferences.ccm.speed_mps, 20.0)
    self.assertEqual(metric.preferences.ccm.speed_with_lead_mps, 15.0)
    self.assertAlmostEqual(metric.preferences.ccm.set_speed_margin_mps, 10 / 3.6)
    self.assertEqual(metric.preferences.cem.signal_lane_width_m, 3.5)

    stock = propose_legacy_adoption({'ConditionalExperimental': '0',
                                     'ConditionalChill': '0'}, units='imperial')
    self.assertIs(stock.preferences.mode, ModeChoice.STOCK)

  def test_legacy_adoption_rejects_ambiguity_corruption_and_safe_mode_coupling(self):
    base = {'ConditionalExperimental': '0', 'ConditionalChill': '1'}
    bad = (
      ({'ConditionalExperimental': '1', 'ConditionalChill': '1'}, 'imperial'),
      ({'ConditionalExperimental': '1'}, 'imperial'),
      ({**base, 'SafeMode': '0'}, 'imperial'),
      ({**base, 'CCMSpeed': '1e2'}, 'imperial'),
      ({**base, 'CCMSpeed': 'nan'}, 'imperial'),
      ({**base, 'CCMSpeed': '100'}, 'imperial'),
      ({**base, 'CCMSetSpeedMargin': '16'}, 'imperial'),
      ({**base, 'CCMSetSpeedMargin': '31'}, 'metric'),
      ({**base, 'CEModelStopTime': '9.1'}, 'metric'),
      ({**base, 'CEStopLights': 'true'}, 'metric'),
      ({**base, 'LaneDetectionWidth': '15.1'}, 'metric'),
      (base, 'unknown'),
    )
    for values, units in bad:
      with self.subTest(values=values, units=units):
        with self.assertRaises(PreferenceError):
          propose_legacy_adoption(values, units=units)


if __name__ == '__main__':
  unittest.main()
