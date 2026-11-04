"""C4 Vehicle presents only current car-parameter facts."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.saved_document import WriteResult
from openpilot.starpilot.ui.vehicle_compact import VehicleCompact, selection_label, vehicle_identity
from openpilot.starpilot.vehicle_selection import SelectionSnapshot, VehicleChoice


class TestVehicleCompact(unittest.TestCase):
  def test_vehicle_selection_uses_native_offroad_without_telemetry(self):
    import tempfile
    from openpilot.common.params import Params
    from opendbc.car.hyundai.values import CAR
    with tempfile.TemporaryDirectory() as directory:
      ui = NS(params=Params(directory), started=False, sm=None)
      ui.is_offroad = lambda: not ui.started
      with patch("openpilot.starpilot.ui.vehicle_compact.ui_state", ui), \
           patch("openpilot.starpilot.ui.vehicle_compact.NavScroller.__init__", return_value=None), \
           patch.object(VehicleCompact, "_populate"):
        page = VehicleCompact()
      self.assertTrue(page.owner.choose(None, str(CAR.KIA_XCEED_PHEV)).verified)
      saved = page.owner.snapshot()
      ui.started = True
      self.assertFalse(page.owner.choose(saved.raw, None).committed)
      self.assertEqual(page.owner.snapshot(), saved)
      ui.started = False
      self.assertTrue(page.owner.choose(saved.raw, None).verified)
      self.assertIsNone(page.owner.snapshot().platform)

  def test_reported_car_identity_does_not_claim_live_control_availability(self):
    self.assertEqual(vehicle_identity(None), "not reported")
    self.assertEqual(vehicle_identity(NS(carFingerprint="MOCK", brand="mock")), "not reported")
    self.assertEqual(vehicle_identity(NS(carFingerprint="HYUNDAI_IONIQ_6", brand="hyundai", passive=True)),
                     "hyundai ioniq 6")
    self.assertEqual(vehicle_identity(NS(carFingerprint="HYUNDAI_IONIQ_6", brand="hyundai", passive=False,
                                         dashcamOnly=False, notCar=False)),
                     "hyundai ioniq 6")

  def test_elantra_refresh_identity_shows_supported_years(self):
    self.assertEqual(vehicle_identity(NS(carFingerprint="HYUNDAI_ELANTRA_HEV_2024")),
                     "hyundai elantra hybrid 2024-2026")

  def test_saved_selection_never_conflates_invalid_with_auto(self):
    choices = (VehicleChoice("HYUNDAI_IONIQ_6", "Hyundai", "Hyundai Ioniq 6"),)
    self.assertEqual(selection_label(SelectionSnapshot(None, True, True, None), choices), "auto detection")
    self.assertEqual(selection_label(SelectionSnapshot(b"bad", True, False, None), choices), "needs review")
    self.assertEqual(selection_label(SelectionSnapshot(None, False, False, None), choices), "unavailable")
    self.assertEqual(selection_label(SelectionSnapshot(b"saved", True, True, "HYUNDAI_IONIQ_6"), choices),
                     "hyundai ioniq 6")

  def test_selection_uses_exact_displayed_source_and_requires_verified_write(self):
    page = VehicleCompact.__new__(VehicleCompact)
    old = SelectionSnapshot(b"old", True, True, None)
    new = SelectionSnapshot(b"new", True, True, "HYUNDAI_IONIQ_6")
    page.selection = old
    self.enterContext(patch.object(page, "owner", NS(choose=Mock(return_value=WriteResult(True, True)), snapshot=Mock(return_value=new)), create=True))
    with patch.object(VehicleCompact, "_populate") as populate, \
         patch("openpilot.starpilot.ui.vehicle_compact.gui_app.pop_widgets_to") as pop:
      page._choose("HYUNDAI_IONIQ_6")
    page.owner.choose.assert_called_once_with(b"old", "HYUNDAI_IONIQ_6")
    self.assertEqual(page.selection, new)
    populate.assert_called_once_with()
    pop.assert_called_once_with(page)

    page.owner.choose.return_value = WriteResult(True, False)
    with patch.object(VehicleCompact, "_populate"), \
         patch("openpilot.starpilot.ui.vehicle_compact.gui_app.pop_widgets_to") as pop, \
         patch("openpilot.starpilot.ui.vehicle_compact.gui_app.push_widget") as push, \
         patch("openpilot.starpilot.ui.vehicle_compact.BigDialog", return_value="error"):
      page._choose(None)
    pop.assert_not_called()
    push.assert_called_once_with("error")

  def test_vehicle_route_opens_native_page(self):
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.ui.settings_state import Destination
    layout = runtime_app.StarMiciMainLayout.__new__(runtime_app.StarMiciMainLayout)
    layout.star = Mock()
    with patch("openpilot.starpilot.ui.vehicle_compact.VehicleCompact", return_value="vehicle page") as page, \
         patch.object(runtime_app.gui_app, "push_widget") as push:
      layout._open_compact_destination(Destination.VEHICLE)
    page.assert_called_once_with(layout.star)
    push.assert_called_once_with("vehicle page")
