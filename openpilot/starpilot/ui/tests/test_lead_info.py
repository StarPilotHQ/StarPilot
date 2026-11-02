"""C4 lead text uses strict saved choices and the existing lead freshness boundary."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np
from openpilot.starpilot.ui.raylib import rl

from openpilot.common.params import Params
from openpilot.selfdrive.ui.mici.onroad import model_renderer as native
from openpilot.starpilot import saved_document
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.appearance_preferences import LeadInfoMode, onroad_appearance, read_lead_info
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.presentation import Profile


class LeadInfoTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    storage = tempfile.TemporaryDirectory()
    self.addCleanup(storage.cleanup)
    self.storage = Path(storage.name)
    self.parked = True
    self.owner = AppearanceOwner(self.params, lambda: self.parked)

  def raw(self, key):
    path = Path(self.params.get_param_path(key))
    return path.read_bytes() if path.exists() else None

  def row(self):
    return next(row for row in self.owner.snapshot(Profile.COMPACT).rows if row.key == "LeadInfo")

  def choose(self, value):
    row = self.row()
    return self.owner.apply(FeatureSettingsRequest(row.key, row.source, value, related_source=row.related_source,
                                                   dependencies=row.dependencies))

  def test_defaults_original_saved_combinations_and_marker_prerequisite(self):
    self.assertEqual(self.params.get_default_value("LeadInfo"), False)
    self.assertEqual(self.params.get_default_value("LeadInfoMode"), 2)
    self.assertEqual(read_lead_info(self.params).mode, LeadInfoMode.OFF)
    self.assertEqual(self.row().value, "Off")
    self.assertFalse(self.row().available)
    self.assertEqual(onroad_appearance(self.params).lead_info_mode, LeadInfoMode.OFF)
    self.params.put_bool("HideLeadMarker", False, block=True)
    self.assertTrue(self.row().available)
    for flag, mode, expected in ((b"0", b"2", LeadInfoMode.OFF), (b"1", b"1", LeadInfoMode.DISTANCE),
                                 (b"1", b"2", LeadInfoMode.SPEED), (b"1", b"0", LeadInfoMode.SPEED)):
      with self.subTest(flag=flag, mode=mode):
        Path(self.params.get_param_path("LeadInfo")).write_bytes(flag)
        Path(self.params.get_param_path("LeadInfoMode")).write_bytes(mode)
        self.assertEqual(read_lead_info(self.params).mode, expected)
        self.assertEqual(onroad_appearance(self.params).lead_info_mode, expected)
    self.params.put_bool("HideLeadMarker", True, block=True)
    self.assertFalse(self.row().available)
    self.assertEqual(onroad_appearance(self.params).lead_info_mode, LeadInfoMode.OFF)

  def test_manager_materializes_off_without_replacing_saved_choice(self):
    from openpilot.system.manager import manager

    class AfterDefaults(Exception):
      pass

    def initialize_defaults():
      with patch.object(manager, "Params", return_value=self.params), \
           patch.object(manager, "prepare_manager_start"), patch.object(manager, "starpilot_storage_root", return_value=self.storage), \
           patch.object(manager, "save_bootlog"), \
           patch.object(manager, "get_build_metadata", return_value=NS(release_channel=False)), \
           patch.object(manager.Paths, "shm_path", side_effect=AfterDefaults):
        with self.assertRaises(AfterDefaults):
          manager.manager_init()

    initialize_defaults()
    self.assertEqual((self.raw("LeadInfo"), self.raw("LeadInfoMode")), (b"0", b"2"))
    self.params.put_bool("HideLeadMarker", False, block=True)
    self.assertEqual(onroad_appearance(self.params).lead_info_mode, LeadInfoMode.OFF)
    self.assertTrue(self.choose("Distance"))
    initialize_defaults()
    self.assertEqual((self.raw("LeadInfo"), self.raw("LeadInfoMode")), (b"1", b"1"))

  def test_ordered_save_repair_parked_and_stale_sources(self):
    self.params.put_bool("HideLeadMarker", False, block=True)
    self.parked = False
    self.assertFalse(self.choose("Distance"))
    self.assertIsNone(self.raw("LeadInfo"))
    self.parked = True
    old = self.row()
    self.assertTrue(self.choose("Distance"))
    self.assertEqual((self.raw("LeadInfoMode"), self.raw("LeadInfo")), (b"1", b"1"))
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(old.key, old.source, "Speed",
                                                             related_source=old.related_source,
                                                             dependencies=old.dependencies)))
    self.assertTrue(self.choose("Speed"))
    self.assertEqual((self.raw("LeadInfoMode"), self.raw("LeadInfo")), (b"2", b"1"))
    self.assertTrue(self.choose("Off"))
    self.assertEqual((self.raw("LeadInfo"), self.raw("LeadInfoMode")), (b"0", b"0"))
    self.params.put_bool("HideLeadMarker", True, block=True)
    self.assertFalse(self.choose("Distance"))

  def test_invalid_and_unreadable_bytes_never_enable(self):
    self.params.put_bool("HideLeadMarker", False, block=True)
    mode = Path(self.params.get_param_path("LeadInfoMode"))
    mode.write_bytes(b"broken")
    row = self.row()
    self.assertEqual((row.value, row.repair_value), ("Invalid saved choice", "Off"))
    self.assertEqual(onroad_appearance(self.params).lead_info_mode, LeadInfoMode.OFF)
    self.assertFalse(self.choose("Distance"))
    self.assertTrue(self.choose("Off"))
    self.assertEqual((self.raw("LeadInfo"), mode.read_bytes()), (b"0", b"0"))
    mode.unlink()
    mode.mkdir()
    self.assertFalse(self.row().available)
    self.assertFalse(self.choose("Speed"))

  def test_partial_enable_failure_remains_off_and_requires_readback(self):
    self.params.put_bool("HideLeadMarker", False, block=True)
    actual_fsync = os.fsync
    calls = 0

    def revoke_after_first(fd):
      nonlocal calls
      actual_fsync(fd)
      calls += 1
      if calls == 2:
        self.parked = False

    with patch.object(saved_document.os, "fsync", side_effect=revoke_after_first):
      self.assertFalse(self.choose("Distance"))
    self.assertEqual((self.raw("LeadInfoMode"), self.raw("LeadInfo")), (b"1", None))
    self.assertEqual(onroad_appearance(self.params).lead_info_mode, LeadInfoMode.OFF)
    self.parked = True
    self.assertTrue(self.choose("Distance"))
    self.assertEqual(onroad_appearance(self.params).lead_info_mode, LeadInfoMode.DISTANCE)

  def test_finite_metric_and_imperial_text(self):
    lead = NS(present=True, dRel=20.0, vLead=12.5)
    format_info = native.ModelRenderer._format_lead_info
    self.assertEqual(format_info(lead, 1, True), "20 m")
    self.assertEqual(format_info(lead, 1, False), "66 ft")
    self.assertEqual(format_info(lead, 2, True), "45 km/h")
    self.assertEqual(format_info(lead, 2, False), "28 mph")
    for invalid in (None, float("nan"), float("inf"), "bad"):
      self.assertEqual(format_info(NS(present=True, dRel=invalid, vLead=12.5), 1, True), "")
      self.assertEqual(format_info(NS(present=True, dRel=20.0, vLead=invalid), 2, True), "")
    self.assertEqual(format_info(NS(present=False, dRel=20, vLead=12.5), 1, True), "")
    self.assertEqual(format_info(lead, 1, None), "")
    self.assertEqual(format_info(lead, 0, True), "")

  def test_native_text_is_centered_with_original_outline(self):
    rect = rl.Rectangle(10, 20, 400, 240)
    with patch.object(native.gui_app, "font", return_value=object()), \
         patch.object(native, "measure_text_cached", return_value=NS(x=80.0)), \
         patch.object(native.rl, "draw_text_ex") as draw:
      native.ModelRenderer._draw_lead_info(rect, "20 m")
    self.assertEqual(draw.call_count, 5)
    self.assertEqual([(call.args[2].x, call.args[2].y) for call in draw.call_args_list],
                     [(169.0, 41.0), (171.0, 41.0), (169.0, 43.0), (171.0, 43.0), (170.0, 42.0)])
    self.assertEqual([call.args[-1] for call in draw.call_args_list], [rl.BLACK] * 4 + [rl.WHITE])

  def test_native_text_needs_current_primary_bar_and_context_resets(self):
    renderer = native.ModelRenderer.__new__(native.ModelRenderer)
    renderer._lead_indicator_enabled = False
    renderer._lead_info_mode = 0
    renderer._lead_info_metric = None
    renderer._torque_filter = Mock()
    renderer._path = native.ModelPoints(raw_points=np.array([[20.0, 0.0, 0.0]], dtype=np.float32))
    renderer._path_offset_z = 0.0
    renderer._transform_dirty = False
    renderer._update_model = Mock()
    renderer._update_leads = Mock(side_effect=lambda *_: setattr(renderer, "_lead_vehicles", [
      NS(bar=np.ones((4, 2), dtype=np.float32), info=NS(present=True, dRel=20.0, vLead=10.0)), native.LeadVehicle()]))
    renderer._draw_lead_indicator = Mock()
    renderer._draw_lead_info = Mock()
    renderer._draw_lane_lines = Mock()
    renderer._draw_path = Mock()
    renderer._experimental_mode = False
    rect = rl.Rectangle(0, 0, 476, 240)
    lead = NS(present=True, dRel=20.0, yRel=0.0, vRel=-1.0, vLead=12.5)
    messages = {"carOutput": NS(actuatorsOutput=NS(torque=0.0)), "extrinsicsCalibration": NS(height=[]),
                "selfdriveState": NS(experimentalMode=False), "modelV2": NS(),
                "radarState": NS(leadOne=lead, leadTwo=NS(present=False))}

    class SubMaster:
      recv_frame = {"extrinsicsCalibration": 2, "modelV2": 2}
      updated = {"carParams": False, "modelV2": False, "radarState": False}
      valid = {"radarState": True}

      def __getitem__(self, key):
        return messages[key]

    sm = SubMaster()
    ui = NS(sm=sm, started_frame=1, status=native.UIStatus.DISENGAGED)
    with patch.object(native, "ui_state", ui), patch.object(native.ModelRenderer, "render", autospec=True,
                                                              side_effect=lambda self, _rect: self._render(_rect)):
      renderer.render_with_lead(rect, True, 1, True)
      renderer._draw_lead_info.assert_called_once_with(rect, "20 m")
      self.assertEqual((renderer._lead_info_mode, renderer._lead_info_metric), (0, None))
      renderer._draw_lead_info.reset_mock()
      sm.recv_frame["modelV2"] = 0
      renderer.render_with_lead(rect, True, 1, True)
      renderer._draw_lead_info.assert_not_called()
      sm.recv_frame["modelV2"] = 2
      renderer._path.raw_points = np.empty((0, 3), dtype=np.float32)
      renderer.render_with_lead(rect, True, 1, True)
      renderer._draw_lead_info.assert_not_called()


if __name__ == "__main__":
  unittest.main()
