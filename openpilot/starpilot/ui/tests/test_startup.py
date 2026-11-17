"""The UI choice is final before Raylib creates a window."""

import unittest
from unittest.mock import patch

from openpilot.starpilot.ui import startup
from openpilot.starpilot.ui.presentation import Profile


class TestStartup(unittest.TestCase):
  def test_both_profiles_select_custom_with_exact_assets(self):
    for profile in (Profile.LARGE, Profile.COMPACT):
      with self.subTest(profile=profile), patch.object(startup, "validate_fonts") as fonts, \
           patch.object(startup, "validate_artwork") as artwork:
        choice = startup.select_ui(profile, {"STARPILOT_UI_FONT_DIR": "/reviewed"})
        self.assertTrue(choice.custom)
        fonts.assert_called_once_with(profile, "/reviewed")
        artwork.assert_called_once_with()

  def test_bundled_fonts_select_custom_and_explicit_bad_bundle_fails(self):
    for profile in (Profile.LARGE, Profile.COMPACT):
      with self.subTest(profile=profile), patch.object(startup, "validate_artwork") as artwork:
        choice = startup.select_ui(profile, {})
        self.assertTrue(choice.custom)
        artwork.assert_called_once_with()
      with patch.object(startup, "validate_artwork") as artwork:
        choice = startup.select_ui(profile, {"STARPILOT_UI_FONT_DIR": "/missing-fonts"})
        self.assertFalse(choice.custom)
        artwork.assert_not_called()
      with patch.object(startup, "validate_fonts"), patch.object(startup, "validate_artwork", side_effect=ValueError("bad hash")):
        self.assertIn("bad hash", startup.select_ui(profile, {"STARPILOT_UI_FONT_DIR": "/bad"}).reason)

  def test_force_fails_and_upstream_override_skips_preflight(self):
    with self.assertRaisesRegex(RuntimeError, "Forced StarPilot UI"):
      startup.select_ui(Profile.LARGE, {"STARPILOT_UI_DEV": "1", "STARPILOT_UI_FONT_DIR": "/missing-fonts"})
    with patch.object(startup, "validate_fonts") as fonts, patch.object(startup, "validate_artwork") as artwork:
      choice = startup.select_ui(Profile.COMPACT, {"STARPILOT_UI": "upstream", "STARPILOT_UI_DEV": "1"})
      self.assertFalse(choice.custom)
      fonts.assert_not_called()
      artwork.assert_not_called()

  def test_main_selects_before_window(self):
    from openpilot.selfdrive.ui import ui

    events: list[str] = []
    def selection(profile, environment):
      events.append(f"select:{profile}")
      return startup.UiSelection(False, "fixture")

    def window(_):
      events.append("window")

    with patch.object(ui, "select_ui", side_effect=selection), patch.object(ui.gui_app, "init_window", side_effect=window), \
         patch.object(ui.gui_app, "render", return_value=iter(())), patch.object(ui, "MainLayout") as layout, \
         patch.object(ui, "MiciMainLayout", return_value=object()), patch.object(ui.messaging, "PubMaster"), \
         patch.object(ui, "config_realtime_process"), patch.object(ui, "BIG_UI", True):
      ui.main()
    self.assertEqual(events, ["select:large", "window"])
    layout.return_value.close.assert_called_once_with()

    with patch.object(ui, "select_ui", side_effect=RuntimeError("bad reviewed font")), \
         patch.object(ui.gui_app, "init_window") as initialize, patch.object(ui, "config_realtime_process"):
      with self.assertRaisesRegex(RuntimeError, "bad reviewed font"):
        ui.main()
      initialize.assert_not_called()

  def test_display_scheduling_preserves_camera_precedence_and_other_devices(self):
    from openpilot.selfdrive.ui import ui

    for device, hardware in (("mici", True), ("tici", True), ("tizi", True), ("pc", False)):
      with self.subTest(device=device), \
           patch.object(ui, "COMMA_HARDWARE", hardware), patch.object(ui.HARDWARE, "get_device_type", return_value=device), \
           patch.object(ui, "select_ui", return_value=startup.UiSelection(False, "fixture")), \
           patch.object(ui.gui_app, "init_window"), \
           patch.object(ui.gui_app, "render", return_value=iter(((False, 0., 0.), (True, .01, .005), (True, .01, .005)))), \
           patch.object(ui, "MainLayout"), patch.object(ui, "MiciMainLayout", return_value=object()), \
           patch.object(ui.messaging, "PubMaster"), patch.object(ui, "config_realtime_process") as realtime, \
           patch.object(ui, "drop_realtime") as normal, patch.object(ui.os, "setpriority") as nice, \
           patch.object(ui.os, "sched_getscheduler", return_value=0, create=True), \
           patch.object(ui.os, "SCHED_OTHER", 0, create=True), patch.object(ui.os, "getpriority", return_value=5 if device == "mici" else 0), \
           patch.object(ui.os, "sched_getaffinity", return_value={0}, create=True), \
           patch.object(ui, "set_core_affinity") as affinity:
        ui.main()
        if device == "mici":
          realtime.assert_not_called()
          self.assertEqual(normal.call_count, 2)
          self.assertEqual(nice.call_count, 2)
          self.assertTrue(all(call.args == (ui.os.PRIO_PROCESS, 0, 5) for call in nice.call_args_list))
          self.assertEqual(set(affinity.call_args_list[0].args[0]), {0, 1, 2, 7})
          self.assertTrue(all(set(call.args[0]) == {0, 1, 2, 7} for call in affinity.call_args_list[1:]))
          self.assertEqual(affinity.call_count, 3)
        else:
          realtime.assert_called_once_with(0, ui.Priority.UI)
          if hardware:
            normal.assert_called_once_with()
            nice.assert_called_once_with(ui.os.PRIO_PROCESS, 0, 0)
          else:
            normal.assert_not_called()
            nice.assert_not_called()
          self.assertEqual(affinity.call_count, 2 if hardware else 0)
          if hardware:
            self.assertTrue(all(call.args == ([6],) for call in affinity.call_args_list))

  def test_slc_publisher_gate_is_independent_of_ui_selection(self):
    self.assertTrue(startup.slc_action_transport_enabled({"STARPILOT_UI_DEV": "1"}))
    self.assertTrue(startup.slc_action_transport_enabled({"SLC_REPLAY_RUNTIME": "1"}))
    self.assertTrue(startup.slc_action_transport_enabled({"STARPILOT_UI": "upstream", "SLC_REPLAY_RUNTIME": "1"}))


if __name__ == "__main__":
  unittest.main()
