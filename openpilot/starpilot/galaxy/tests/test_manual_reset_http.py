"""Manual reset uses the real HTTP preview/confirm path in private settings."""

from pathlib import Path

from openpilot.starpilot.galaxy.settings import AuthorityContext
from openpilot.starpilot.galaxy.tests.settings_http_fixture import SettingsHttpFixture
from openpilot.starpilot.saved_source import read_saved


class ManualResetHttpTest(SettingsHttpFixture):
  def test_manual_reset_http_uses_row_choice_and_preserves_history(self):
    from opendbc.car.hyundai.interface import CarInterface
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, parse_document

    cp = CarInterface.get_non_essential_params(CAR.HYUNDAI_IONIQ_6).as_reader()
    self.context.value = AuthorityContext(True, cp, cp.as_builder().to_bytes())
    history = Path(self.params.get_param_path("AutomaticSteeringLiveTrials"))
    history.write_bytes(b"unit-only inert history preservation sentinel")
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/tuning", cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page["rows"]) if row.get("coordinate") == "center_taper_max")
    status, intent, _ = self.request("/api/settings/preview", cookie=cookie,
                                    payload={"view": page["view"], "row": index, "value": 1})
    self.assertEqual(status, 200)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                 payload={"intent": intent["intent"], "confirmed": True})[:2], (200, {"saved": True}))
    raw, readable = read_saved(self.params, DOCUMENT_KEY, 16384)
    self.assertTrue(readable)
    assert raw is not None
    manual = parse_document(raw)[str(cp.carFingerprint)].manual_coordinates
    assert manual is not None
    self.assertEqual(dict(manual.values)["center_taper_max"], .01)

    status, page, _ = self.request("/api/settings/pages/tuning", cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Reset manual tune")
    status, intent, _ = self.request("/api/settings/preview", cookie=cookie,
                                    payload={"view": page["view"], "row": index, "direction": 0})
    self.assertEqual(status, 200)
    self.assertEqual(intent["proposed"], "Reset")
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                 payload={"intent": intent["intent"], "confirmed": True})[:2], (200, {"saved": True}))
    raw, readable = read_saved(self.params, DOCUMENT_KEY, 16384)
    self.assertTrue(readable)
    assert raw is not None
    self.assertIsNone(parse_document(raw)[str(cp.carFingerprint)].manual_coordinates)
    self.assertEqual(history.read_bytes(), b"unit-only inert history preservation sentinel")
    status, page, _ = self.request("/api/settings/pages/tuning", cookie=cookie)
    self.assertEqual(status, 200)
    self.assertFalse(any(row["label"] == "Reset manual tune" for row in page["rows"]))
    coordinates = [row for row in page["rows"] if row.get("coordinate")]
    self.assertEqual(len(coordinates), 30)
    self.assertTrue(all(float(row["value"]) == 0 for row in coordinates))
