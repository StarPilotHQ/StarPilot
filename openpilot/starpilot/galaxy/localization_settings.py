"""Global language, display units and initial driver-monitoring wheel side."""

import json
from importlib.resources import files

from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState
from openpilot.starpilot.ui.slc_offset_feature import SlcOffsetOwner


LANGUAGES = json.loads(files("openpilot.selfdrive.ui").joinpath("translations/languages.json").read_text(encoding="utf-8"))


class LocalizationSettingsOwner:
  def __init__(self, params, authorized, parked):
    self.params = params
    self.authorized = authorized
    self.parked = parked
    self.units = SlcOffsetOwner(params, parked, lambda: None)

  def _wheel_side_allowed(self):
    return self.authorized() and self.parked() and not self.params.get_bool("IsDriverViewEnabled")

  def snapshot(self):
    rows = []
    for key, label, reason in (
      ("IsMetric", "Use Metric", "On uses kilometers; Off uses miles. Saved speed-limit offsets keep their physical values."),
      ("IsRhdDetected", "Right-Hand Drive", "Sets the initial driver-monitoring side for the next drive. Automatic wheel-side detection continues."),
    ):
      raw, readable = read_saved(self.params, key, 8)
      valid = readable and raw in (None, b"0", b"1")
      allowed = self.authorized() if key == "IsMetric" else self._wheel_side_allowed()
      if key == "IsRhdDetected" and not allowed:
        reason = "Park the vehicle and close driver camera preview before changing the steering-wheel side."
      rows.append(FeatureRow(key, label, ("On" if raw == b"1" else "Off") if valid else "Invalid saved value",
                             raw, ("Off", "On") if valid else (), available=valid and allowed,
                             reason=reason if valid else "Invalid saved value", default_value="Off"))
    raw, readable = read_saved(self.params, "LanguageSetting", 32)
    code = (raw or b"en").decode("utf-8", errors="replace").removeprefix("main_")
    name = next((name for name, value in LANGUAGES.items() if value == code), "English")
    rows.append(FeatureRow("LanguageSetting", "Language", name, raw, tuple(LANGUAGES),
                           available=readable and self.authorized() and self.parked(),
                           reason="Display language for Galaxy and the device. Untranslated text uses English. Park before changing language.",
                           default_value="English"))
    return FeatureSettingsState("localization", "Localization", "Language, units and steering-wheel side for every vehicle.",
                                tuple(rows), self.parked())

  def apply(self, request):
    if request.key == "LanguageSetting":
      if request.value not in LANGUAGES:
        return False
      result = commit_exact(self.params, key=request.key, max_bytes=32,
                            raw=LANGUAGES[request.value].encode("utf-8"), expected=request.expected,
                            authorized=lambda: self.authorized() and self.parked(), temp_prefix=".language-")
      return result.committed and result.verified
    if request.value not in ("Off", "On") or request.expected not in (None, b"0", b"1") or not self.authorized():
      return False
    if request.key == "IsMetric":
      return self.units.change_units(request.value == "On", request.expected)
    if request.key != "IsRhdDetected" or not self._wheel_side_allowed():
      return False
    result = commit_exact(self.params, key=request.key, max_bytes=8,
                          raw=b"1" if request.value == "On" else b"0", expected=request.expected,
                          authorized=self._wheel_side_allowed, temp_prefix=".wheel-side-")
    if not result.committed or not result.verified:
      return False
    self.params.put_bool("OnroadCycleRequested", True, block=True)
    return True
