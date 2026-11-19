"""The UI choice is final before Raylib creates a window."""

import os
import subprocess
import sys
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

  def test_display_scheduling_is_background_before_window_on_both_profiles(self):
    from openpilot.selfdrive.ui import ui

    for device, hardware in (("mici", True), ("tici", True), ("tizi", True), ("pc", False)):
      for big_ui in (False, True):
        events = []
        def window(_, events=events, hardware=hardware):
          events.append("window")
          if hardware:
            self.assertEqual(events[:4], ["normal", "nice", "affinity", "window"])
        with self.subTest(device=device, big_ui=big_ui), \
             patch.object(ui, "COMMA_HARDWARE", hardware), patch.object(ui, "BIG_UI", big_ui), \
             patch.object(ui.HARDWARE, "get_device_type", return_value=device), \
             patch.object(ui, "select_ui", return_value=startup.UiSelection(False, "fixture")), \
             patch.object(ui.gui_app, "init_window", side_effect=window), \
             patch.object(ui.gui_app, "render", return_value=iter(((False, 0., 0.), (True, .01, .005), (True, .01, .005)))), \
             patch.object(ui, "MainLayout"), patch.object(ui, "MiciMainLayout", return_value=object()), \
             patch.object(ui.messaging, "PubMaster"), \
             patch.object(ui, "config_realtime_process", side_effect=lambda *_, events=events: events.append("realtime")) as realtime, \
             patch.object(ui, "drop_realtime", side_effect=lambda events=events: events.append("normal")) as normal, \
             patch.object(ui.os, "setpriority", side_effect=lambda *_, events=events: events.append("nice")) as nice, \
             patch.object(ui.os, "sched_getscheduler", return_value=0, create=True), \
             patch.object(ui.os, "SCHED_OTHER", 0, create=True), \
             patch.object(ui.os, "getpriority", return_value=5 if hardware else 0), \
             patch.object(ui.os, "sched_getaffinity", return_value={0}, create=True), \
             patch.object(ui, "set_core_affinity", side_effect=lambda _, events=events: events.append("affinity")) as affinity:
          ui.main()
          if hardware:
            realtime.assert_not_called()
            self.assertEqual(events[:4], ["normal", "nice", "affinity", "window"])
            self.assertEqual(normal.call_count, 2)
            self.assertEqual(nice.call_count, 2)
            self.assertTrue(all(call.args == (ui.os.PRIO_PROCESS, 0, 5) for call in nice.call_args_list))
            self.assertEqual(affinity.call_count, 3)
            expected_cores = {0, 1, 2, 7}
            self.assertTrue(all(set(call.args[0]) == expected_cores for call in affinity.call_args_list))
          else:
            realtime.assert_called_once_with(0, ui.Priority.UI)
            self.assertEqual(events, ["realtime", "window"])
            normal.assert_not_called()
            nice.assert_not_called()
            affinity.assert_not_called()

  def test_slc_publisher_gate_is_independent_of_ui_selection(self):
    self.assertTrue(startup.slc_action_transport_enabled({"STARPILOT_UI_DEV": "1"}))
    self.assertTrue(startup.slc_action_transport_enabled({"SLC_REPLAY_RUNTIME": "1"}))
    self.assertTrue(startup.slc_action_transport_enabled({"STARPILOT_UI": "upstream", "SLC_REPLAY_RUNTIME": "1"}))

  @unittest.skipUnless(sys.platform == "linux", "Linux thread scheduling")
  def test_graphics_workers_inherit_background_scheduling(self):
    if not os.sched_getaffinity(0).intersection({0, 1, 2, 7}):
      self.skipTest("Runner has none of the display cores")
    code = '''
import os, sys, threading
from unittest.mock import patch
from openpilot.common import realtime
from openpilot.selfdrive.ui import ui
from openpilot.starpilot.ui import startup
device, big = sys.argv[1], sys.argv[2] == "1"
expected = os.sched_getaffinity(0).intersection({0, 1, 2, 7})
observed = []
def state():
  return os.sched_getscheduler(0), os.getpriority(os.PRIO_PROCESS, 0), os.sched_getaffinity(0)
def window(_):
  assert state() == (os.SCHED_OTHER, 5, expected), state()
  worker = threading.Thread(target=lambda: observed.append(state()))
  worker.start()
  worker.join(5)
  assert not worker.is_alive()
with patch.object(realtime, "PC", False), patch.object(ui, "COMMA_HARDWARE", True), \\
     patch.object(ui.HARDWARE, "get_device_type", return_value=device), \\
     patch.object(ui, "BIG_UI", big), \\
     patch.object(ui, "select_ui", return_value=startup.UiSelection(False, "fixture")), \\
     patch.object(ui.gui_app, "init_window", side_effect=window), \\
     patch.object(ui.gui_app, "render", return_value=iter(())), \\
     patch.object(ui, "MainLayout"), patch.object(ui, "MiciMainLayout"), \\
     patch.object(ui.messaging, "PubMaster"):
  ui.main()
assert observed == [(os.SCHED_OTHER, 5, expected)], observed
'''
    for device in ("tici", "tizi"):
      for big_ui in (False, True):
        with self.subTest(device=device, big_ui=big_ui):
          subprocess.run([sys.executable, "-B", "-c", code, device, str(int(big_ui))], check=True, timeout=20)


if __name__ == "__main__":
  unittest.main()
