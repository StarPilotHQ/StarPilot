"""The C4 lead setting uses current radar evidence and the upstream native lead bar."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.mici.onroad import model_renderer as native


class LeadIndicatorTests(unittest.TestCase):
  def test_native_default_off_and_temporary_opt_in_including_stock_longitudinal(self):
    renderer = native.ModelRenderer.__new__(native.ModelRenderer)
    renderer._lead_indicator_enabled = False
    renderer._longitudinal_control = False
    renderer._torque_filter = Mock()
    renderer._path = native.ModelPoints(raw_points=np.array([[20.0, 0.0, 0.0]], dtype=np.float32))
    renderer._path_offset_z = 0.0
    renderer._transform_dirty = False
    renderer._update_model = Mock()
    renderer._update_leads = Mock(side_effect=lambda *_: setattr(
      renderer, "_lead_vehicles", [NS(bar=np.ones((4, 2), dtype=np.float32), info=NS(present=True, dRel=20.0, vLead=10.0))]))
    renderer._draw_lead_indicator = Mock()
    renderer._draw_lane_lines = Mock()
    renderer._draw_path = Mock()
    renderer._experimental_mode = False
    rect = rl.Rectangle(0, 0, 476, 240)
    radar = NS(leadOne=NS(present=False), leadTwo=NS(present=False))
    messages = {"carOutput": NS(actuatorsOutput=NS(torque=0.0)), "extrinsicsCalibration": NS(height=[]),
                "selfdriveState": NS(experimentalMode=False), "modelV2": NS(), "radarState": radar}
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
      renderer.render(rect)
      renderer._update_leads.assert_not_called()
      renderer._draw_lead_indicator.assert_not_called()
      renderer.render_with_lead(rect, True)
      renderer._update_leads.assert_called_once()
      renderer._draw_lead_indicator.assert_called_once()
      self.assertFalse(renderer._lead_indicator_enabled)

      renderer._path.raw_points = np.empty((0, 3), dtype=np.float32)
      renderer._update_leads.reset_mock()
      renderer._draw_lead_indicator.reset_mock()
      renderer.render_with_lead(rect, True)
      self.assertFalse(renderer._lead_vehicles[0].bar.size, "empty current path clears the old marker")
      renderer._update_leads.assert_not_called()
      renderer._draw_lead_indicator.assert_not_called()

      renderer.render_with_lead(rect, False)
      renderer._update_leads.assert_not_called()
      renderer._draw_lead_indicator.assert_not_called()

  def test_temporary_context_clears_after_render_exception(self):
    renderer = native.ModelRenderer.__new__(native.ModelRenderer)
    renderer._lead_indicator_enabled = False
    with patch.object(native.ModelRenderer, "render", side_effect=RuntimeError("lost frame")):
      with self.assertRaisesRegex(RuntimeError, "lost frame"):
        renderer.render_with_lead(rl.Rectangle(0, 0, 476, 240), True)
    self.assertFalse(renderer._lead_indicator_enabled)

  def test_only_present_finite_leads_are_projected(self):
    from openpilot.starpilot.ui.tests.test_lead_bar import fixture
    for field, value in (("dRel", float("nan")), ("yRel", float("inf")), ("vRel", "bad"), ("dRel", -1.)):
      renderer, sm = fixture()
      setattr(sm["radarState"].leadOne, field, value)
      renderer._update_leads(sm)
      self.assertFalse(renderer._lead_vehicles[0].bar.size)
      renderer._get_lead_bar.assert_not_called()
    renderer, sm = fixture()
    renderer._update_leads(sm)
    self.assertTrue(renderer._lead_vehicles[0].bar.size)


if __name__ == "__main__":
  unittest.main()
