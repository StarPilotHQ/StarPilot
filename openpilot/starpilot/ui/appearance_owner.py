"""Parked, source-bound edits for supported onroad visibility controls."""

from collections.abc import Callable
from dataclasses import replace
import json

from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.onroad_customization import MAX_BYTES, PARAM_KEY, decode_document, default_document
from openpilot.starpilot.ui.appearance_preferences import (CAMERA_LABELS, DEFAULTS, LEAD_INFO_LABELS, MAX_RAW_BYTES,
                                                          RETIRED_DISPLAY_KEYS, read_camera_view, read_lead_info, read_visibility)
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, FeatureSettingsState
from openpilot.starpilot.ui.presentation import Profile


# Widget visibility, the stopped timer and Rainbow Road live in the onroad customization document.
LABELS = {
  "DeveloperSidebar": "Developer Sidebar",
  "HidePersonalityAlerts": "Hide Personality Alerts",
  "ShowBrakeStatus": "Wheel brake and acceleration colors",
  "DriverCamera": "Driver Camera on Reverse",
  "StockConfidenceBallWidget": "Use StarPilot Widgets",
  "HideLeadMarker": "Lead Indicator",
  "LeadInfo": "Lead Info",
  "SignalMetrics": "C4 amber signal border",
  "BlindSpotMetrics": "C4 red blind-spot border",
}


class AppearanceOwner:
  def __init__(self, params, parked: Callable[[], bool]):
    self.params = params
    self.parked = parked

  def snapshot(self, profile: Profile, *, include_large: bool = False) -> FeatureSettingsState:
    parked = self.parked()
    compact_only = ("StockConfidenceBallWidget", "DriverCamera", "HideLeadMarker", "LeadInfo")
    keys = tuple(key for key in DEFAULTS if key not in compact_only and key in LABELS) if profile == Profile.LARGE else \
      ("DriverCamera", "StockConfidenceBallWidget", "HidePersonalityAlerts", "ShowBrakeStatus",
       "HideLeadMarker", "LeadInfo", "SignalMetrics", "BlindSpotMetrics")
    if profile == Profile.COMPACT and include_large:
      keys = ("DeveloperSidebar", *keys)
    rows = []
    if profile == Profile.COMPACT:
      saved_camera = read_camera_view(self.params)
      camera_valid = saved_camera.value is not None and saved_camera.readable
      rows.append(FeatureRow("CameraView", "Camera View",
                             CAMERA_LABELS[saved_camera.value] if camera_valid else "Invalid saved choice",
                             source=saved_camera.raw, choices=CAMERA_LABELS, available=parked and saved_camera.readable,
                             reason="" if camera_valid else "Saved choice cannot be read" if not saved_camera.readable else "Choose Standard to repair",
                             repair_value="Standard" if saved_camera.readable and not camera_valid else ""))
    for key in keys:
      if key == "ShowBrakeStatus":
        saved, pedals = read_visibility(self.params, key), read_visibility(self.params, "PedalsOnUI")
        readable = saved.readable and pedals.readable
        valid = readable and saved.value is not None and pedals.value is not None
        value = ("On" if saved.value or pedals.value else "Off") if valid else "Invalid saved choice"
        rows.append(FeatureRow(key, LABELS[key], value, source=saved.raw, choices=("Off", "On"),
                               available=parked and readable, dependencies=(("PedalsOnUI", pedals.raw),),
                               reason="" if valid else "Saved choice cannot be read" if not readable else "Choose Off to repair",
                               repair_value="Off" if readable and not valid else ""))
        continue
      if key == "LeadInfo":
        saved = read_lead_info(self.params)
        marker = read_visibility(self.params, "HideLeadMarker")
        valid = saved.mode is not None and saved.readable
        marker_on = marker.readable and marker.value is False
        rows.append(FeatureRow(key, LABELS[key], LEAD_INFO_LABELS[saved.mode] if valid else "Invalid saved choice",
                               source=saved.flag.raw, related_source=saved.mode_raw,
                               choices=LEAD_INFO_LABELS if valid else (), available=parked and saved.readable and marker_on,
                               reason="Turn on Lead Indicator first" if not marker_on else
                                      "Saved choice cannot be read" if not saved.readable else
                                      "Choose Off to repair" if not valid else "",
                               dependencies=(("HideLeadMarker", marker.raw),),
                               repair_value="Off" if saved.readable and not valid else ""))
        continue
      saved = read_visibility(self.params, key)
      valid = saved.value is not None and saved.readable
      displayed = not saved.value if key in ("HideLeadMarker", "StockConfidenceBallWidget") else saved.value
      value = ("On" if displayed else "Off") if valid else "Invalid saved choice"
      reason = ("" if valid else "Saved choice cannot be read" if not saved.readable else "Choose default to repair")
      default_displayed = not DEFAULTS[key] if key in ("HideLeadMarker", "StockConfidenceBallWidget") else DEFAULTS[key]
      repair = ("On" if default_displayed else "Off") if saved.readable and not valid else ""
      rows.append(FeatureRow(key, LABELS[key], value, source=saved.raw, choices=("Off", "On"),
                             available=parked and saved.readable, reason=reason, repair_value=repair))
    defaults = {key: ('On' if (not value if key in ('HideLeadMarker', 'StockConfidenceBallWidget') else value) else 'Off')
                for key, value in DEFAULTS.items()}
    defaults.update(CameraView='Standard', LeadInfo='Off')
    rows = tuple(replace(row, default_value=defaults[row.key]) if row.key in defaults else row for row in rows)
    if profile == Profile.LARGE:
      raw, document = self._motion_source()
      rows += (FeatureRow('ReduceMotion', 'Reduce Quick Select motion',
                          ('On' if document.get('reduceMotion') else 'Off') if document is not None else 'Unavailable', source=raw,
                          choices=('Off', 'On'), available=parked and document is not None, default_value='Off',
                          reason='Show Quick Select without transitions' if document is not None else 'Repair the saved layout in Colors & Layout'),)
    return FeatureSettingsState(page="appearance", title="Onroad HUD" if profile == Profile.LARGE else "Visuals",
                                subtitle="Display preferences; border highlights apply to C4 only.",
                                rows=tuple(rows), parked=parked)

  def apply(self, request: FeatureSettingsRequest) -> bool:
    if request.key == 'ReduceMotion':
      raw, document = self._motion_source()
      if document is None or request.expected != raw or request.value not in ('Off', 'On'):
        return False
      document['reduceMotion'] = request.value == 'On'
      result = commit_exact(self.params, key=PARAM_KEY, max_bytes=MAX_BYTES,
                            raw=json.dumps(document, separators=(',', ':')).encode(), expected=raw,
                            authorized=self.parked, temp_prefix='.appearance-')
      return result.committed and result.verified
    if request.key == "ShowBrakeStatus":
      return self._save_wheel_feedback(request)
    if request.key == "LeadInfo":
      return self._save_lead_info(request)
    if request.key == "CameraView" and request.value in CAMERA_LABELS:
      raw = str(CAMERA_LABELS.index(request.value)).encode()
      max_bytes = 8
    elif request.key in DEFAULTS and request.key not in RETIRED_DISPLAY_KEYS and request.value in ("Off", "On"):
      desired = (request.value != "On") if request.key in ("HideLeadMarker", "StockConfidenceBallWidget") else request.value == "On"
      raw = b"1" if desired else b"0"
      max_bytes = MAX_RAW_BYTES
    else:
      return False
    result = commit_exact(self.params, key=request.key, max_bytes=max_bytes, raw=raw, expected=request.expected,
                          authorized=self.parked, temp_prefix=".appearance-")
    return result.committed and result.verified

  def _motion_source(self):
    raw, readable = read_saved(self.params, PARAM_KEY, MAX_BYTES)
    if not readable:
      return raw, None
    try:
      return raw, default_document() if raw is None else decode_document(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError):
      return raw, None

  def _save_wheel_feedback(self, request: FeatureSettingsRequest) -> bool:
    saved = {key: read_visibility(self.params, key) for key in ("ShowBrakeStatus", "PedalsOnUI")}
    if (request.value not in ("Off", "On") or any(not value.readable for value in saved.values()) or
        request.expected != saved["ShowBrakeStatus"].raw or
        request.dependencies != (("PedalsOnUI", saved["PedalsOnUI"].raw),) or
        any(value.value is None for value in saved.values()) and request.value != "Off"):
      return False
    expected = {key: value.raw for key, value in saved.items()}

    def authorized():
      current = {key: read_visibility(self.params, key) for key in expected}
      return self.parked() and all(value.readable and value.raw == expected[key] for key, value in current.items())

    # Enable the canonical flag before clearing its old alias; disable the alias first.
    desired = {"ShowBrakeStatus": b"1" if request.value == "On" else b"0", "PedalsOnUI": b"0"}
    order = ("ShowBrakeStatus", "PedalsOnUI") if request.value == "On" else ("PedalsOnUI", "ShowBrakeStatus")
    for key in order:
      if expected[key] == desired[key]:
        continue
      result = commit_exact(self.params, key=key, max_bytes=MAX_RAW_BYTES, raw=desired[key],
                            expected=expected[key], authorized=authorized, temp_prefix=".wheel-feedback-")
      if not result.committed or not result.verified:
        return False
      expected[key] = desired[key]
    return authorized()

  def _save_lead_info(self, request: FeatureSettingsRequest) -> bool:
    if request.value not in LEAD_INFO_LABELS:
      return False
    saved = read_lead_info(self.params)
    marker = read_visibility(self.params, "HideLeadMarker")
    if (not saved.readable or saved.flag.raw != request.expected or saved.mode_raw != request.related_source or
        not marker.readable or marker.value is not False or
        request.dependencies != (("HideLeadMarker", marker.raw),) or
        saved.mode is None and request.value != "Off"):
      return False

    desired_mode = str(LEAD_INFO_LABELS.index(request.value)).encode()
    desired_flag = b"0" if request.value == "Off" else b"1"
    flag_raw, mode_raw = saved.flag.raw, saved.mode_raw

    def authorized(expected_flag: bytes | None, expected_mode: bytes | None) -> bool:
      current = read_lead_info(self.params)
      current_marker = read_visibility(self.params, "HideLeadMarker")
      return (self.parked() and current.readable and current.flag.raw == expected_flag and
              current.mode_raw == expected_mode and current_marker.readable and current_marker.value is False and
              current_marker.raw == marker.raw)

    def write(key: str, raw: bytes, expected: bytes | None, expected_flag: bytes | None,
              expected_mode: bytes | None) -> bool:
      result = commit_exact(self.params, key=key, max_bytes=MAX_RAW_BYTES, raw=raw, expected=expected,
                            authorized=lambda: authorized(expected_flag, expected_mode), temp_prefix=".lead-info-")
      return result.committed and result.verified

    if desired_flag == b"0" and flag_raw != b"0":
      if not write("LeadInfo", b"0", flag_raw, flag_raw, mode_raw):
        return False
      flag_raw = b"0"
    if mode_raw != desired_mode:
      if not write("LeadInfoMode", desired_mode, mode_raw, flag_raw, mode_raw):
        return False
      mode_raw = desired_mode
    if desired_flag == b"1" and flag_raw != b"1":
      if not write("LeadInfo", b"1", flag_raw, flag_raw, mode_raw):
        return False
      flag_raw = b"1"
    return authorized(flag_raw, mode_raw)
