import unittest
import tempfile
import json
from pathlib import Path
from openpilot.starpilot.lateral.tests.test_inferred_torque_overrides import SavedParams
from openpilot.starpilot.settings_retirement import retire_settings, PROFILE_FLAGS
from openpilot.starpilot.longitudinal.profile_document import default_personality_profiles, profile_document, migrate_profile_document
from openpilot.starpilot.ui.appearance_preferences import RETIRED_DISPLAY_KEYS
from openpilot.starpilot.ui.onroad_customization import PARAM_KEY as LAYOUT_KEY, default_document, read_customization


class TestSettingsRetirement(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.params = SavedParams(self.directory.name)

  def test_inactive_cruise_features_do_not_become_enabled(self):
    self.params.put_bool('QOLLongitudinal', False)
    self.params.put_bool('ForceStops', True)
    self.params.put_bool('ReverseCruise', True)
    self.params.put('CustomCruise', 7.0)
    retire_settings(self.params)
    self.assertEqual(self.params.get('ForceStops'), b'0')
    self.assertEqual(self.params.get('ReverseCruise'), b'0')
    self.assertEqual(self.params.get('CustomCruise'), b'1.0')
    self.assertIsNone(self.params.get('QOLLongitudinal'))
    self.params.put_bool('ForceStops', True)
    retire_settings(self.params)
    self.assertEqual(self.params.get('ForceStops'), b'1')
  def test_active_cruise_values_and_steering_values_survive(self):
    self.params.put_bool('QOLLongitudinal', True)
    self.params.put_bool('ForceStops', True)
    self.params.put('CustomCruise', 7.0)
    self.params.put('SteerLatAccel', 3.2)
    self.params.put_bool('AdvancedLateralTune', False)
    retire_settings(self.params)
    self.assertEqual(self.params.get('ForceStops'), b'1')
    self.assertEqual(self.params.get('CustomCruise'), b'7.0')
    self.assertEqual(self.params.get('SteerLatAccel'), b'3.2')
    self.assertIsNone(self.params.get('AdvancedLateralTune'))
  def test_inactive_legacy_curve_does_not_activate(self):
    profiles = default_personality_profiles(False)
    profiles['standard']['following'] = {'preset': 'custom', 'curve': [2.0]*10}
    profiles['standard']['acceleration'] = {'preset': 'sport', 'curve': [], 'legacyActivation': True}
    profiles['relaxed']['following'] = {'preset': 'custom', 'curve': [2.5]*10}
    document = profile_document(profiles, enabled=True)
    self.params.put('LongitudinalPersonalityProfiles', document)
    self.params.put_bool('StandardPersonalityProfile', False)
    self.params.put_bool('RelaxedPersonalityProfile', True)
    retire_settings(self.params)
    result = migrate_profile_document(self.params.get('LongitudinalPersonalityProfiles'))
    assert result is not None
    assert result is not None
    self.assertEqual(result['profiles']['standard']['following']['preset'], 'dom_default')
    self.assertEqual(result['profiles']['standard']['acceleration']['preset'], 'selected_profile')
    self.assertEqual(result['profiles']['relaxed']['following']['curve'], [2.5]*10)
    self.assertTrue(all(self.params.get(key) is None for key in PROFILE_FLAGS))
    before = self.params.get('LongitudinalPersonalityProfiles')
    retire_settings(self.params)
    self.assertEqual(self.params.get('LongitudinalPersonalityProfiles'), before)
  def test_invalid_documents_retained_and_unconfigured_allowed(self):
    self.params.put_bool('StandardPersonalityProfile', False)
    self.params.put('LongitudinalPersonalityProfiles', 'bad')
    self.assertTrue(retire_settings(self.params))
    self.assertEqual(self.params.get('LongitudinalPersonalityProfiles'), b'bad')
    self.assertIsNone(self.params.get('StandardPersonalityProfile'))
    self.params.put('LongitudinalPersonalityProfiles', {})
    retire_settings(self.params)
    self.assertIsNone(self.params.get('StandardPersonalityProfile'))
  def test_unsafe_switch_and_invalid_evidence_are_bounded_and_preserved(self):
    self.params.put('RetiredSettingsEvidence', 'not-json')
    self.params.put('QOLLongitudinal', 'x' * 100000)
    self.params.put_bool('ForceStops', True)
    issues = retire_settings(self.params)
    self.assertTrue(any('existing evidence retained' in issue for issue in issues))
    self.assertEqual(self.params.get('RetiredSettingsEvidence'), b'not-json')
    self.assertEqual(self.params.get('ForceStops'), b'0')
    self.assertIsNone(self.params.get('QOLLongitudinal'))

  def test_absent_parent_is_off_only_on_first_reconciliation(self):
    self.params.put_bool('ForceStops', True)
    self.params.put_bool('ReverseCruise', True)
    profiles = default_personality_profiles(False)
    profiles['standard']['following'] = {'preset': 'custom', 'curve': [2.0]*10}
    self.params.put('LongitudinalPersonalityProfiles', profile_document(profiles, enabled=True))
    retire_settings(self.params)
    self.assertEqual(self.params.get('ForceStops'), b'0')
    self.assertEqual(self.params.get('ReverseCruise'), b'0')
    result = migrate_profile_document(self.params.get('LongitudinalPersonalityProfiles'))
    assert result is not None
    self.assertEqual(result['profiles']['standard']['following']['preset'], 'dom_default')
    self.params.put_bool('ForceStops', True)
    self.params.put_bool('ReverseCruise', True)
    self.params.put('LongitudinalPersonalityProfiles', profile_document(profiles, enabled=True))
    retire_settings(self.params)
    self.assertEqual(self.params.get('ForceStops'), b'1')
    self.assertEqual(self.params.get('ReverseCruise'), b'1')
    result = migrate_profile_document(self.params.get('LongitudinalPersonalityProfiles'))
    assert result is not None
    self.assertEqual(result['profiles']['standard']['following']['curve'], [2.0]*10)

  def test_fresh_namespace_completes_and_preserves_later_choice(self):
    retire_settings(self.params)
    state = json.loads(self.params.get('SettingsRetirementState'))
    self.assertEqual(set(state['completed']), {'cruise', 'profiles', 'display'})
    self.params.put_bool('ForceStops', True)
    retire_settings(self.params)
    self.assertEqual(self.params.get('ForceStops'), b'1')


  def write(self, key, raw):
    Path(self.params.get_param_path(key)).write_bytes(raw)

  def test_display_switches_fold_into_layout_once(self):
    for key, raw in (("HideSpeed", b"1"), ("HideMaxSpeed", b"1"), ("HideSteeringWheel", b"1"), ("HideDMIcon", b"1"),
                     ("EnableTorqueBarWidget", b"0"), ("StoppedTimer", b"1"), ("RainbowPath", b"1")):
      self.write(key, raw)
    custom = default_document()
    custom["roadColors"]["compact"]["pathMode"] = "color"
    self.params.put(LAYOUT_KEY, custom)
    retire_settings(self.params)
    document = read_customization(self.params)
    large, compact = document["layouts"]["large"], document["layouts"]["compact"]
    self.assertEqual([large[key]["enabled"] for key in ("current_speed", "steering_wheel", "driver_monitor", "torque_bar")],
                     [False, False, False, False])
    # The small UI never honored the speed and wheel switches.
    self.assertEqual([compact[key]["enabled"] for key in ("steering_wheel", "driver_monitor", "torque_bar", "stopped_timer")],
                     [True, False, False, True])
    self.assertIs(document["showMaxSpeed"], False)
    self.assertEqual((document["roadColors"]["large"]["pathMode"], document["roadColors"]["compact"]["pathMode"]), ("rainbow", "color"))
    for key in RETIRED_DISPLAY_KEYS:
      self.assertIsNone(self.params.get(key))
    # Completed: a later layout edit survives even if a default switch file reappears.
    document["layouts"]["large"]["current_speed"]["enabled"] = True
    self.params.put(LAYOUT_KEY, document)
    self.write("HideSpeed", b"1")
    retire_settings(self.params)
    self.assertTrue(read_customization(self.params)["layouts"]["large"]["current_speed"]["enabled"])

  def test_default_and_corrupt_switches_leave_layout_bytes_alone(self):
    self.write("HideSpeed", b"0")
    self.write("EnableTorqueBarWidget", b"bad")
    retire_settings(self.params)
    self.assertIsNone(self.params.get(LAYOUT_KEY))
    self.assertIsNone(self.params.get("EnableTorqueBarWidget"))

  def test_invalid_layout_is_kept_and_unreadable_layout_retries(self):
    self.write(LAYOUT_KEY, b'{"version": 99}')
    self.write("HideSpeed", b"1")
    issues = retire_settings(self.params)
    self.assertEqual(self.params.get(LAYOUT_KEY), b'{"version": 99}')
    self.assertEqual(self.params.get("HideSpeed"), b"1")
    self.assertNotIn("display", json.loads(self.params.get("SettingsRetirementState"))["completed"])
    self.assertIn("Invalid onroad layout kept; display switches were not carried over", issues)
    self.params.put(LAYOUT_KEY, default_document())
    retire_settings(self.params)
    self.assertFalse(read_customization(self.params)["layouts"]["large"]["current_speed"]["enabled"])
    self.assertIsNone(self.params.get("HideSpeed"))

    other = tempfile.TemporaryDirectory()
    self.addCleanup(other.cleanup)
    self.params = SavedParams(other.name)
    Path(self.params.get_param_path(LAYOUT_KEY)).mkdir()
    self.write("HideSpeed", b"1")
    retire_settings(self.params)
    self.assertNotIn("display", json.loads(self.params.get("SettingsRetirementState"))["completed"])
    self.assertEqual(self.params.get("HideSpeed"), b"1")


if __name__ == '__main__':
  unittest.main()


class TestHighwaySmoothingEnrollment(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.params = SavedParams(self.directory.name)

  def test_new_and_existing_off_or_custom_strength_migrate_once(self):
    for saved in (None, 10., 1., 5.5, 3.7):
      with self.subTest(saved=saved), tempfile.TemporaryDirectory() as root:
        params = SavedParams(root)
        params.put("SteerFriction", .123)
        if saved is not None:
          params.put("HighwaySmoothingLevel", saved)
        retire_settings(params)
        self.assertEqual(params.get("HighwaySmoothingLevel"), b"3.7")
        self.assertEqual(params.get("HighwaySmoothingDefaultApplied"), b"1")
        self.assertEqual(params.get("SteerFriction"), b"0.123")
        retire_settings(SavedParams(root))
        self.assertEqual(params.get("HighwaySmoothingLevel"), b"3.7")
        for chosen in (10., 6., 1.):
          params.put("HighwaySmoothingLevel", chosen)
          retire_settings(SavedParams(root))
          self.assertEqual(params.get("HighwaySmoothingLevel"), str(chosen).encode())

  def test_invalid_enrollment_marker_does_not_reenable_smoothing(self):
    for marker in (b"0", b"invalid"):
      self.params.put("HighwaySmoothingLevel", 10.)
      Path(self.params.get_param_path("HighwaySmoothingDefaultApplied")).write_bytes(marker)
      issues = retire_settings(self.params)
      self.assertTrue(any("smoothing enrollment" in issue for issue in issues))
      self.assertEqual(self.params.get("HighwaySmoothingLevel"), b"10.0")
      self.assertEqual(self.params.get("HighwaySmoothingDefaultApplied"), marker)

  def test_failed_strength_write_is_not_marked_complete(self):
    from unittest.mock import patch
    original_put = self.params.put
    def put(key, value, block=True):
      if key == "HighwaySmoothingLevel":
        raise OSError("disk full")
      original_put(key, value, block=block)
    with patch.object(self.params, "put", side_effect=put), self.assertRaises(OSError):
      retire_settings(self.params)
    self.assertIsNone(self.params.get("HighwaySmoothingDefaultApplied"))
    retire_settings(self.params)
    self.assertEqual(self.params.get("HighwaySmoothingLevel"), b"3.7")
    self.assertEqual(self.params.get("HighwaySmoothingDefaultApplied"), b"1")
