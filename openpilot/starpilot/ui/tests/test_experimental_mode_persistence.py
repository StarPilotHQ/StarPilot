"""Saved full-time mode survives stock ACC without granting driving authority."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived import selfdrived
from openpilot.selfdrive.ui.layouts.settings import common, toggles as large
from openpilot.selfdrive.ui.mici.layouts.settings import toggles as compact
from openpilot.selfdrive.ui.mici.widgets import button
from openpilot.starpilot.aol.tests.test_ioniq6_stock_repair import stock
from openpilot.starpilot.conditional_mode.actions import stock_document
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage
from openpilot.starpilot.ui.raylib import rl
from openpilot.system.ui.lib.application import gui_app


class TestExperimentalPersistence(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.params = Params(directory.name)
    self.params.put("ConditionalModeConfig", json.loads(stock_document()), block=True)
    self.params.put_bool("AlphaLongitudinalEnabled", True, block=True)
    self.cp = stock(True)
    self.owner = FeatureSettingsOwner(self.params, lambda _group: True,
      vehicle_fingerprint=lambda: self.cp.carFingerprint, vehicle_params=lambda: self.cp)

  def saved_mode(self):
    rows = self.owner.snapshot(FeaturePage.CONDITIONAL, parked=True, system_long=False,
                               lateral_context=False, metric=False).rows
    return next(row.value for row in rows if row.key == "conditional:mode")

  def saved_raw(self):
    path = Path(self.params.get_param_path("ExperimentalMode"))
    return path.read_bytes() if path.exists() else None

  def drive(self):
    # Exercise the real constructor while keeping process IPC out of this test.
    with patch.object(selfdrived, "Params", return_value=self.params), \
         patch.object(selfdrived.messaging, "PubMaster"), \
         patch.object(selfdrived.messaging, "SubMaster"), \
         patch.object(selfdrived.messaging, "sub_sock"), \
         patch.object(selfdrived, "feature_enabled", return_value=False):
      return selfdrived.SelfdriveD(CP=self.cp.as_reader())

  def runtime_mode(self, drive):
    drive.refresh_saved_driving_mode(1_000_000_000)
    drive.update_conditional_mode(None)
    return drive.experimental_mode

  def test_stock_startup_preserves_full_time_mode_then_qualified_start_restores_it(self):
    self.params.put_bool("ExperimentalMode", True, block=True)
    before = self.cp.to_dict()
    for _ in range(2):
      drive = self.drive()
      self.assertFalse(self.runtime_mode(drive))
      self.assertEqual(self.saved_raw(), b"1")
      self.assertEqual(self.saved_mode(), "Experimental")
      self.assertTrue(self.params.get_bool("AlphaLongitudinalEnabled"))
      self.assertEqual(self.cp.to_dict(), before)
    self.cp.openpilotLongitudinalControl = True
    self.cp.pcmCruise = False
    self.assertTrue(self.runtime_mode(self.drive()))
    self.assertEqual(self.saved_raw(), b"1")

  def test_runtime_restrictions_do_not_rewrite_saved_preference(self):
    self.params.put_bool("ExperimentalMode", True, block=True)
    self.cp.openpilotLongitudinalControl = True
    self.cp.pcmCruise = False
    for field in ("passive", "dashcamOnly", "notCar"):
      with self.subTest(field=field):
        setattr(self.cp, field, True)
        self.assertFalse(self.runtime_mode(self.drive()))
        self.assertEqual(self.saved_raw(), b"1")
        setattr(self.cp, field, False)
    self.params.put_bool("SafeMode", True, block=True)
    self.assertFalse(self.runtime_mode(self.drive()))
    self.assertEqual(self.saved_raw(), b"1")

  def test_absent_or_off_choices_stay_off_across_stock_and_supported_startup(self):
    for saved in (None, False):
      with self.subTest(saved=saved):
        if saved is not None:
          self.params.put_bool("ExperimentalMode", saved, block=True)
        for available in (False, True):
          self.cp.openpilotLongitudinalControl = available
          self.cp.pcmCruise = not available
          self.assertFalse(self.runtime_mode(self.drive()))
          self.assertEqual(self.saved_raw(), None if saved is None else b"0")
          self.assertEqual(self.saved_mode(), "Chill")

  def test_both_settings_layouts_preserve_saved_mode_and_block_unsupported_toggles(self):
    ui = NS(params=self.params, CP=self.cp, has_longitudinal_control=False, engaged=False, started=False,
            add_engaged_transition_callback=Mock(), update_params=Mock(),
            sm={"selfdriveState": NS(experimentalMode=False)})
    for module in (large, compact, common):
      self.enterContext(patch.object(module, "ui_state", ui))
    self.enterContext(patch.object(large, "Params", return_value=self.params))
    self.enterContext(patch.object(button, "Params", return_value=self.params))
    self.enterContext(patch.object(gui_app, "font", return_value=rl.Font()))
    self.enterContext(patch.object(gui_app, "texture", return_value=NS(width=64, height=64)))
    self.params.put_bool("ExperimentalMode", True, block=True)
    large_panel, compact_panel = large.TogglesLayout(), compact.TogglesLayoutMici()
    for _ in range(2):
      large_panel._update_toggles()
      compact_panel._update_toggles()
      self.assertFalse(large_panel.request_toggle("ExperimentalMode", False))
      self.assertFalse(compact_panel.request_experimental())
      self.assertEqual(self.saved_raw(), b"1")
      self.assertEqual(self.saved_mode(), "Experimental")
      self.assertFalse(large_panel._toggles["ExperimentalMode"].action_item.enabled)
    self.cp.openpilotLongitudinalControl = ui.has_longitudinal_control = True
    self.cp.pcmCruise = False
    large_panel._update_toggles()
    compact_panel._update_toggles()
    self.assertTrue(large_panel._toggles["ExperimentalMode"].action_item.get_state())
    self.assertTrue(large_panel._toggles["ExperimentalMode"].action_item.enabled)
    self.assertTrue(compact_panel._experimental_btn._checked)
    self.assertTrue(compact_panel._experimental_btn.enabled)
    # An explicit user selection remains able to turn full-time Experimental off.
    self.assertTrue(large_panel.request_toggle("ExperimentalMode", False))
    self.assertEqual(self.saved_raw(), b"0")
    self.assertEqual(self.saved_mode(), "Chill")
