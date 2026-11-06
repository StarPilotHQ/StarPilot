"""The compact Home keeps the installed date readable beside version/branch."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from openpilot.starpilot.ui.home import HomeView
from openpilot.starpilot.ui.presentation import FontRole, Profile
from openpilot.starpilot.ui.preview_home import reference_state


class TestHomeCommitDate(unittest.TestCase):
  def setUp(self):
    self.view = HomeView.__new__(HomeView)
    self.view.fonts = Mock(profile=Profile.COMPACT)
    self.view.fonts.measure.side_effect = lambda text, role, size: SimpleNamespace(width=len(text) * size * 0.6, height=size)
    self.view._icon = Mock()
    self.view._mode_atom = Mock()
    self.state = replace(reference_state(), branch="Dom", model_label="Baseline model (678af78)")

  def render(self, state):
    self.view.render(state)
    return {call.args[0]: call.args for call in self.view.fonts.draw.call_args_list}

  def test_installed_date_keeps_existing_labels_and_icons(self):
    drawn = self.render(self.state)
    self.assertIn("StarPilot", drawn)
    self.assertIn("6.7.7  Dom", drawn)
    self.assertIn(self.state.model_label, drawn)
    date = drawn["Sep 15"]
    self.assertEqual(date[1:3], (FontRole.ROMAN, 30))
    self.assertEqual(date[4], 98)
    self.assertEqual(date[3] + self.view._measure(*date[:3]).width, 526)
    self.view._icon.assert_any_call("icons_mici/settings.png", 48, 48, 8, 192, 0.9)
    self.view._icon.assert_any_call("icons_mici/settings/network/wifi_strength_slash.png", 50, 44, 76, 190.5, 0.9)
    self.view._mode_atom.assert_called_once_with(self.state.mode, 146, 192)

  def test_missing_date_preserves_full_version_row(self):
    state = replace(self.state, commit_date="", branch="a" * 28)
    drawn = self.render(state)
    self.assertEqual(set(drawn), {"StarPilot", f"{state.version}  {state.branch}", state.model_label})
    self.assertEqual(drawn[f"{state.version}  {state.branch}"][3:5], (6, 98))

  def test_long_branch_cannot_truncate_or_overlap_date(self):
    drawn = self.render(replace(self.state, branch="very-long-branch-" * 12))
    date = drawn["Sep 15"]
    version = next(args for args in drawn.values() if args[0].startswith(self.state.version))
    self.assertTrue(version[0].endswith("…"))
    self.assertLessEqual(version[3] + self.view._measure(*version[:3]).width + 16, date[3])
    self.assertEqual(drawn[self.state.model_label][2:5], (30, 6, 140))

  def test_large_profile_still_uses_large_renderer(self):
    self.view.fonts.profile = Profile.LARGE
    self.view._large = Mock()
    self.view.render(self.state)
    self.view._large.assert_called_once_with(self.state)
    self.view.fonts.draw.assert_not_called()


if __name__ == "__main__":
  unittest.main()
