"""Quick Select toggles for display switches retired into the onroad layout.

The legacy keys and labels stay, so slots saved before the retirement keep working.
Each toggle edits the active profile's layout, guarded by the exact saved bytes.
"""

import hashlib
import json
from collections.abc import Callable

from openpilot.starpilot.favorites.state import FavoriteAction
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.onroad_customization import (
  MAX_BYTES, PARAM_KEY, STOPPED_TIMER_WIDGET, decode_document, default_document, validate_document,
)


def _widget(key):
  def get(document, profile):
    return document["layouts"][profile][key]["enabled"]
  def put(document, profile, value):
    document["layouts"][profile][key]["enabled"] = value
  return get, put


def _max_speed():
  # The large card hides its MAX line; the small UI has a separate MAX widget.
  def get(document, profile):
    return document.get("showMaxSpeed", True) if profile == "large" else document["layouts"][profile]["max_speed"]["enabled"]
  def put(document, profile, value):
    if profile == "large":
      document["showMaxSpeed"] = value
    else:
      document["layouts"][profile]["max_speed"]["enabled"] = value
  return get, put


def _rainbow():
  def get(document, profile):
    return document["roadColors"][profile].get("pathMode") == "rainbow"
  def put(document, profile, value):
    document["roadColors"][profile]["pathMode"] = "rainbow" if value else "acceleration"
  return get, put


# key: (label, accessor, inverted, profiles). Inverted keys read On when the widget is hidden, as before.
TOGGLES = {
  "HideSpeed": ("Hide Current Speed", _widget("current_speed"), True, ("large",)),
  "HideMaxSpeed": ("Hide MAX Speed", _max_speed(), True, ("large", "compact")),
  "HideSteeringWheel": ("Hide Steering Wheel", _widget("steering_wheel"), True, ("large", "compact")),
  "HideDMIcon": ("Hide Driver Monitoring Icon", _widget("driver_monitor"), True, ("large", "compact")),
  "EnableTorqueBarWidget": ("Show Torque Bar", _widget("torque_bar"), False, ("large", "compact")),
  "StoppedTimer": ("Stopped Timer", _widget(STOPPED_TIMER_WIDGET), False, ("compact",)),
  "RainbowPath": ("Rainbow Road", _rainbow(), False, ("large", "compact")),
}


def layout_actions(params, profile: str, authorized: Callable[[], bool],
                   on_change: Callable[[], None] = lambda: None, *, requested_keys=None) -> dict[str, FavoriteAction]:
  if requested_keys is not None and not TOGGLES.keys() & requested_keys:
    return {}
  raw, readable = read_saved(params, PARAM_KEY, MAX_BYTES)
  document = None
  if readable:
    try:
      document = decode_document(raw) if raw is not None else default_document()
    except (ValueError, TypeError, UnicodeError, RecursionError):
      document = None
  actions = {}
  for key, (label, (get, put), inverted, profiles) in TOGGLES.items():
    if requested_keys is not None and key not in requested_keys:
      continue
    supported = profile in profiles
    value = (get(document, profile) != inverted) if document is not None and supported else None
    reason = ("" if value is not None else "Only on the small UI" if not supported and profile == "large" else
              "Only on the large UI" if not supported else "Saved layout needs repair in Galaxy")
    token = hashlib.sha256(repr((key, profile, raw)).encode()).hexdigest()

    def invoke(get=get, put=put, inverted=inverted, expected=raw):
      current, fresh = read_saved(params, PARAM_KEY, MAX_BYTES)
      if not fresh or current != expected or not authorized():
        return False
      changed = decode_document(current) if current is not None else default_document()
      put(changed, profile, not get(changed, profile))
      encoded = json.dumps(validate_document(changed), separators=(",", ":"), allow_nan=False).encode()
      result = commit_exact(params, key=PARAM_KEY, max_bytes=MAX_BYTES, raw=encoded, expected=current,
                            authorized=authorized, temp_prefix=".favorite-layout-")
      if result.committed and result.verified:
        on_change()
        return True
      return False

    available = value is not None and authorized()
    actions[key] = FavoriteAction(key, label, "toggle", "Unavailable" if value is None else "On" if value else "Off",
                                  available, "" if available else reason or "Available while the driving screen is shown",
                                  token, invoke if available else None, "Visual")
  return actions
