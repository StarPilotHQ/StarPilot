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
  def test_original_speed_limit_rows_refresh_with_parent_and_leave_control_off(self):
    with tempfile.TemporaryDirectory() as path:
      params = Params(path)
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

        # No Show speed limit signs switch: the layout's speed-limit widget decides whether signs show.
        self.assertEqual(controls(), ["automatic adoption"])
        self.assertFalse(any(item.label == "show speed limits" for item in page.items))
        self.assertFalse(params.get_bool("SpeedLimitController"))
        next(item for item in page.items if item.label == "automatic adoption").click()
        self.assertEqual(controls(), list(appearance_compact.SLC_VISUAL_LABELS.values()))
        self.assertTrue(params.get_bool("SLCConfirmation"))
        self.assertFalse(any(item.label in ('confirm lower limits', 'confirm higher limits') for item in page.items))
        next(item for item in page.items if item.label == "automatic adoption").click()
        self.assertEqual(controls(), ["automatic adoption"])
        self.assertFalse(params.get_bool("SLCConfirmation"))


if __name__ == "__main__":
  unittest.main()
