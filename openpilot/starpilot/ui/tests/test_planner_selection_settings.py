"""Fleet-wide saved planner preference independent of longitudinal capability."""
from pathlib import Path
import tempfile
import unittest

from openpilot.common.params import Params
from openpilot.starpilot.longitudinal.planner_selection import KEY, read_selection
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


class TestPlannerSelectionSettings(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    registered = Params(temporary.name)
    root = Path(registered.get_param_path('CustomPersonalities')).parent
    class Files:
      def get_param_path(self, key):
        return str(root / key)
      def get_default_value(self, key):
        return True if key == KEY else registered.get_default_value(key)
    self.params = Files()
    self.parked = True
    self.preferences = True
    self.groups = []
    def authority(group):
      self.groups.append(group)
      return self.preferences if group == 'preferences' else self.parked if group == 'parked_preferences' else False
    self.owner = FeatureSettingsOwner(self.params, authority, vehicle_fingerprint=lambda: None)

  def row(self, *, system_long=False):
    state = self.owner.snapshot('profiles', parked=self.parked, system_long=system_long,
                                lateral_context=False, metric=False)
    return next(row for row in state.rows if row.key == KEY)

  def test_default_row_available_without_vehicle_or_long_enable(self):
    row = self.row()
    self.assertEqual(row.label, 'Use StarPilot Longitudinal Planner')
    self.assertEqual(row.value, 'On')
    self.assertTrue(row.available)
    self.assertIsNone(row.vehicle_fingerprint)
    self.assertIn('next drive', row.reason)
    self.assertIn("comma's stock planner", row.reason)
    self.assertTrue(self.row(system_long=True).available)

  def test_saved_switch_uses_preferences_authority_and_exact_source_only(self):
    row = self.row()
    request = FeatureSettingsRequest(KEY, row.source, 'Off')
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), b'0')
    self.assertEqual(self.groups[-1], 'preferences')
    self.assertFalse(self.owner.apply(request))
    self.assertFalse(Path(self.params.get_param_path('ExperimentalLongitudinalEnabled')).exists())
    self.preferences = False
    self.assertFalse(self.row().available)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(KEY, b'0', 'On')))
    self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), b'0')

  def test_onroad_save_changes_persistent_choice_without_replacing_active_selection(self):
    self.parked = False
    active = read_selection(self.params)
    self.assertTrue(active.starpilot)
    row = self.row()
    self.assertTrue(row.available)
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(KEY, row.source, 'Off')))
    self.assertTrue(active.starpilot)
    self.assertFalse(read_selection(self.params).starpilot)
    self.assertFalse(Path(self.params.get_param_path('ExperimentalLongitudinalEnabled')).exists())


if __name__ == '__main__':
  unittest.main()
