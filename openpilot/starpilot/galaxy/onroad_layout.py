import copy
import hashlib
import json
from collections.abc import Callable

from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.appearance_preferences import read_lead_info
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.onroad_customization import (
  MAX_BYTES, PARAM_KEY, customization_metadata, decode_document, default_document, validate_document,
)
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.galaxy.setting_descriptions import description

# Display preferences edited with the layout but saved in their own params. Groups order the editor.
DISPLAY_GROUPS = {
  "DeveloperSidebar": "Big layout",
  "ShowBrakeStatus": "widget", "StockConfidenceBallWidget": "widget",
  "HideLeadMarker": "Lead car", "LeadInfo": "Lead car",
  "CameraView": "Camera (Small layout)", "DriverCamera": "Camera (Small layout)",
  "HidePersonalityAlerts": "Alerts and borders", "SignalMetrics": "Alerts and borders", "BlindSpotMetrics": "Alerts and borders",
  "UseVienna": "Speed limits",
}


class LayoutChanged(Exception):
  pass


def _revision(raw: bytes | None, rows=()) -> str:
  digest = hashlib.sha256(b"\x00" if raw is None else b"\x01" + raw)
  for row in rows:
    digest.update(repr((row.key, row.source, row.related_source, row.dependencies)).encode())
  return digest.hexdigest()


class OnroadLayoutOwner:
  def __init__(self, params, parked: Callable[[], bool], vehicle_params=lambda: None):
    self.params = params
    self.parked = parked
    self.vehicle_params = vehicle_params

  def _display_rows(self):
    rows = AppearanceOwner(self.params, self.parked).snapshot(Profile.COMPACT, include_large=True).rows
    order = list(DISPLAY_GROUPS)
    return tuple(sorted((row for row in rows if row.key in DISPLAY_GROUPS), key=lambda row: order.index(row.key)))

  def snapshot(self) -> dict:
    raw, readable = read_saved(self.params, PARAM_KEY, MAX_BYTES)
    valid = readable
    document = default_document()
    if raw is not None:
      try:
        document = decode_document(raw)
      except (ValueError, TypeError, UnicodeError, RecursionError):
        valid = False
    parked = self.parked()
    rows = self._display_rows()
    lead_readable = read_lead_info(self.params).readable
    # The editor merges these into its draft as document["display"]; the saved layout never holds them.
    display = {
      "rows": [{"key": row.key, "label": row.label, "group": DISPLAY_GROUPS[row.key], "choices": list(row.choices),
                # Lead info follows the draft's Lead indicator, not the saved one.
                "available": row.available or row.key == "LeadInfo" and parked and lead_readable and bool(row.choices),
                "reason": description(row), "requires": "HideLeadMarker" if row.key == "LeadInfo" else ""} for row in rows],
      "values": {row.key: row.value for row in rows},
      "defaults": {row.key: row.default_value for row in rows if row.default_value is not None},
    }
    from openpilot.starpilot.car.ford.rear_fog import main_bus
    metadata = customization_metadata()
    for profile in metadata["profiles"].values():
      profile["widgets"]["rear_fog"]["hidden"] = main_bus(self.vehicle_params()) is None
    return {"document": document, "defaults": default_document(), "revision": _revision(raw, rows),
            "metadata": metadata, "editable": readable and parked, "valid": valid,
            "activeProfile": None, "display": display, "failed": []}

  def save(self, payload: dict, *, session_valid: Callable[[], bool]) -> dict:
    if (type(payload) is not dict or set(payload) != {"revision", "document"} or type(payload["revision"]) is not str or
        type(payload["document"]) is not dict):
      raise ValueError("Invalid layout request")
    document = copy.copy(payload["document"])
    display = document.pop("display", {})
    rows = {row.key: row for row in self._display_rows()}
    if (type(display) is not dict or not display.keys() <= rows.keys() or
        any(type(value) is not str or value != rows[key].value and value not in rows[key].choices
            for key, value in display.items())):
      raise ValueError("Invalid display preferences")
    document = validate_document(document)
    encoded = json.dumps(document, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_BYTES:
      raise ValueError("Layout is too large")
    source, readable = read_saved(self.params, PARAM_KEY, MAX_BYTES)
    if not readable:
      raise OSError("Saved layout is unavailable")
    if payload["revision"] != _revision(source, tuple(rows.values())):
      raise LayoutChanged("Saved layout changed; reload before saving")
    result = commit_exact(self.params, key=PARAM_KEY, max_bytes=MAX_BYTES, raw=encoded, expected=source,
                          authorized=lambda: session_valid() and self.parked(), temp_prefix=".onroad-layout-")
    if not result.committed or not result.verified:
      raise LayoutChanged("Layout could not be saved; check that the device is parked and reload")
    failed = self._save_display(display, rows, session_valid)
    return {**self.snapshot(), "failed": failed}

  def disable_map(self, session_valid: Callable[[], bool]) -> bool:
    """Turn the Big-screen map off when Android Auto has taken it; True when it was on."""
    raw, readable = read_saved(self.params, PARAM_KEY, MAX_BYTES)
    if not readable or raw is None:
      return False
    try:
      document = json.loads(raw)
      placed = document["layouts"]["large"]["nav_map"]
    except (ValueError, KeyError, TypeError):
      return False
    if type(placed) is not dict or placed.get("enabled") is not True:
      return False
    placed["enabled"] = False
    result = commit_exact(self.params, key=PARAM_KEY, max_bytes=MAX_BYTES,
                          raw=json.dumps(document, separators=(",", ":"), allow_nan=False).encode(), expected=raw,
                          authorized=lambda: session_valid() and self.parked(), temp_prefix=".onroad-layout-")
    return bool(result.committed and result.verified)

  def _save_display(self, display, rows, session_valid) -> list[str]:
    changes = [key for key, value in display.items() if value != rows[key].value]
    # Lead info can change only while the Lead indicator is shown.
    if "HideLeadMarker" in changes and "LeadInfo" in changes:
      changes.remove("LeadInfo")
      changes.insert(changes.index("HideLeadMarker") + (display["HideLeadMarker"] == "On"), "LeadInfo")
    owner = AppearanceOwner(self.params, lambda: session_valid() and self.parked())
    failed = []
    for key in changes:
      row = {row.key: row for row in self._display_rows()}[key]
      request = FeatureSettingsRequest(key, row.source, display[key], related_source=row.related_source,
                                       dependencies=row.dependencies)
      if not row.available or not owner.apply(request):
        failed.append(row.label)
    return failed
