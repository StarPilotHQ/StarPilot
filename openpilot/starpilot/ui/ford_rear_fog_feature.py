"""The Ford vehicle toggle edits the same visibility saved by Galaxy."""
import hashlib
import json

from openpilot.starpilot.car.ford.rear_fog import main_bus
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow
from openpilot.starpilot.ui.onroad_customization import MAX_BYTES, PARAM_KEY, decode_document, default_document

KEY = "FordRearFogIndicator"


def source_token(raw):
  return hashlib.sha256(b"\x00" if raw is None else b"\x01" + raw).digest()


class FordRearFogFeature:
  def __init__(self, owner):
    self.owner = owner

  def capability(self):
    cp = self.owner.vehicle_params()
    bus = main_bus(cp)
    return (str(cp.carFingerprint), bus) if bus is not None else None

  def saved(self):
    raw, readable = read_saved(self.owner.params, PARAM_KEY, MAX_BYTES)
    try:
      document = default_document() if raw is None else decode_document(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError):
      return raw, None
    return raw, document if readable else None

  def rows(self, parked):
    capability = self.capability()
    if capability is None:
      return ()
    raw, document = self.saved()
    enabled = document is not None and any(p["rear_fog"]["enabled"] for p in document["layouts"].values())
    return (FeatureRow(KEY, "Rear Fog Indicator", "On" if enabled else "Off", source_token(raw), choices=("Off", "On"),
                       available=parked and self.owner.authority("parked_preferences") and document is not None,
                       capability=capability, default_value="On",
                       reason="Shows fresh Ford rear-fog status only. Move or hide the widget per display in Galaxy."),)

  def apply(self, request):
    if (request.key != KEY or request.value not in ("Off", "On") or request.related_source is not None or
        request.display_unit or request.direction or request.dependencies):
      return False
    raw, document = self.saved()
    if document is None or request.expected != source_token(raw):
      return False
    def authorized():
      capability = self.capability()
      return (self.owner.authority("parked_preferences") and bool(request.vehicle_fingerprint) and
              self.owner.vehicle_fingerprint() == request.vehicle_fingerprint and capability is not None and
              capability == request.capability)
    for profile in document["layouts"].values():
      profile["rear_fog"]["enabled"] = request.value == "On"
    encoded = json.dumps(document, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_BYTES:
      return False
    result = commit_exact(self.owner.params, key=PARAM_KEY, max_bytes=MAX_BYTES, raw=encoded, expected=raw,
                          authorized=authorized, temp_prefix=".ford-rear-fog-")
    return result.committed and result.verified
