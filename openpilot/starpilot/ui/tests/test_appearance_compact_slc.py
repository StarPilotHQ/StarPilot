"""Original compact Visuals speed-limit controls use the shared saved owner."""

from unittest.mock import Mock
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.ui import appearance_compact
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.presentation import Profile


class Button:
  def __init__(self, label, value):
    self.label, self.value, self.click = label, value, None

  def set_click_callback(self, callback):
    self.click = callback


class Scroller:
  def __init__(self):
    self._scroller = self
    self.items = []

  def add_widgets(self, cards):
    self.items.extend(cards)


class CompactVisualsSlcTests(unittest.TestCase):
  def test_sign_choice_stays_editable_with_system_long_off(self):
    with tempfile.TemporaryDirectory() as path:
      params = Params(path)
      params.put_bool("ShowSpeedLimits", False, block=True)
      appearance = AppearanceOwner(params, lambda: True)
      feature = FeatureSettingsOwner(params, lambda group: group == "preferences", vehicle_fingerprint=lambda: None)

      class Session:
        def appearance_snapshot(self):
          return appearance.snapshot(Profile.COMPACT)

        def feature_snapshot(self, page):
          return feature.snapshot(page, parked=True, system_long=False, lateral_context=False, metric=False)

        def feature_request(self, request):
          return feature.apply(request)

      shown = []
      with (
        patch.object(appearance_compact, "BigButton", Button),
        patch.object(appearance_compact, "GreyBigButton", Button),
        patch.object(appearance_compact, "NavScroller", Scroller),
        patch.object(appearance_compact.gui_app, "push_widget", shown.append),
      ):
        appearance_compact.AppearanceCompact(
          Mock(
            spec=appearance_compact.AppearanceSession,
            appearance_snapshot=Session().appearance_snapshot,
            feature_snapshot=Session().feature_snapshot,
            feature_request=Session().feature_request,
          )
        ).open()
        sign = next(item for item in shown[0].items if item.label == "show speed limits")
        self.assertIsNotNone(sign.click)
        sign.click()
        self.assertTrue(params.get_bool("ShowSpeedLimits"))
        self.assertFalse(params.get_bool("SpeedLimitController"))

  def test_original_speed_limit_rows_refresh_with_parent_and_leave_control_off(self):
    with tempfile.TemporaryDirectory() as path:
      params = Params(path)
      params.put_bool("ShowSpeedLimits", False, block=True)
      parked = True
      appearance = AppearanceOwner(params, lambda: parked)
      feature = FeatureSettingsOwner(params, lambda group: parked, vehicle_fingerprint=lambda: "TEST CAR")

      class Session:
        def appearance_snapshot(self):
          return appearance.snapshot(Profile.COMPACT)

        def pip_snapshot(self):
          raise AssertionError("Side Camera was not opened")

        def appearance_request(self, request):
          return appearance.apply(request)

        def feature_snapshot(self, page):
          return feature.snapshot(page, parked=parked, system_long=parked, lateral_context=False, metric=False)

        def feature_request(self, request):
          return feature.apply(request)

      shown = []
      with (
        patch.object(appearance_compact, "BigButton", Button),
        patch.object(appearance_compact, "GreyBigButton", Button),
        patch.object(appearance_compact, "NavScroller", Scroller),
        patch.object(appearance_compact.gui_app, "push_widget", shown.append),
      ):
        appearance_compact.AppearanceCompact(
          Mock(
            spec=appearance_compact.AppearanceSession,
            appearance_snapshot=Session().appearance_snapshot,
            feature_snapshot=Session().feature_snapshot,
            feature_request=Session().feature_request,
          )
        ).open()
        page = shown[0]

        def controls():
          return [item.label for item in page.items if item.label in appearance_compact.SLC_VISUAL_LABELS.values()]

        self.assertEqual(controls(), ["show speed limits", "confirm limits"])
        next(item for item in page.items if item.label == "show speed limits").click()
        self.assertTrue(params.get_bool("ShowSpeedLimits"))
        self.assertFalse(params.get_bool("SpeedLimitController"))
        for mode in (1, 3, 4, 0):
          next(item for item in page.items if item.label == "confirm limits").click()
          self.assertEqual(params.get("SLCConfirmation"), mode)
        self.assertEqual(controls(), ["show speed limits", "confirm limits"])


if __name__ == "__main__":
  unittest.main()
