from collections.abc import Callable

from openpilot.starpilot.car.gm.radar_recovery import KEY, capability
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import SettingLevel, FeatureRow


class VoltRadarFeature:
  def __init__(self, owner, *, galaxy: Callable[[], bool]):
    self.owner = owner
    self.galaxy = galaxy

  def capability(self):
    return capability(self.owner.vehicle_params()) if self.galaxy() else None

  def rows(self):
    binding = self.capability()
    if binding is None:
      return ()
    raw, readable = read_saved(self.owner.params, KEY, 8)
    valid = readable and raw in (None, b"0", b"1")
    return (FeatureRow(KEY, "Radar Recovery Alert", ("On" if raw == b"1" else "Off") if valid else "Invalid saved choice",
                       source=raw, choices=("Off", "On") if valid else (), default_value="Off", capability=binding,
                       available=valid and self.owner.authority("preferences"),
                       reason="Play a chime and show a brief message when a radar fault clears. You must re-engage manually.", level=SettingLevel.ADVANCED),)

  def apply(self, request):
    if (request.key != KEY or request.value not in ("Off", "On") or request.expected not in (None, b"0", b"1") or
        request.dependencies or request.related_source is not None or request.display_unit or request.direction):
      return False

    def authorized():
      return (self.owner.authority("preferences") and request.capability is not None and
              request.capability == self.capability() and bool(request.vehicle_fingerprint) and
              request.vehicle_fingerprint == self.owner.vehicle_fingerprint())

    return commit_exact(self.owner.params, key=KEY, max_bytes=8, expected=request.expected,
                        raw=b"1" if request.value == "On" else b"0", authorized=authorized,
                        temp_prefix=".volt-radar-").verified
