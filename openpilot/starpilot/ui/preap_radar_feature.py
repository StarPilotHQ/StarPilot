from opendbc.car import structs
from opendbc.car.tesla.values import CAR
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.car.tesla.radar_settings import DOCUMENT_KEY, MAX_BYTES, encode, read_radar_settings
from openpilot.starpilot.ui.feature_settings_state import SettingLevel, FeatureRow

KEYS = frozenset(("preap_radar:mode", "preap_radar:offset"))
FLOAT32_MAX = 3.4028234663852886e38


def capability(cp):
  if cp is None or cp.brand != "tesla" or cp.carFingerprint != CAR.TESLA_MODEL_S_PREAP:
    return None
  if (cp.flags or cp.notCar or cp.transmissionType == structs.CarParams.TransmissionType.manual or
      cp.fingerprintSource == structs.CarParams.FingerprintSource.fixed):
    return None
  configurations = tuple((str(s.safetyModel), int(s.safetyParam)) for s in cp.safetyConfigs)
  if len(configurations) != 1 or configurations[0] not in (("teslaPreap", 0), ("teslaPreap", 2), ("teslaPreap", 6), ("noOutput", 0)):
    return None
  return (cp.carFingerprint, configurations, bool(cp.dashcamOnly), bool(cp.passive))


class PreAPRadarFeature:
  def __init__(self, owner):
    self.owner = owner

  def rows(self):
    owner = self.owner
    capable = capability(owner.vehicle_params())
    if capable is None:
      return []
    saved = read_radar_settings(owner.params)
    source = dict(saved.sources).get(DOCUMENT_KEY)
    allowed = owner.authority("parked_preferences")
    mode = "Behind nosecone" if saved.behind_nosecone else "Front" if saved.enabled else "Off"
    return [FeatureRow("preap_radar:mode", "Hardware radar", mode if saved.valid else "Invalid saved configuration", source,
                       choices=("Off", "Front", "Behind nosecone") if saved.valid else (),
                       repair_value="" if saved.valid else "Off", available=allowed, capability=capable, vehicle_fingerprint=owner.vehicle_fingerprint(),
                       dependencies=saved.sources, reason="Parked hardware setup. Applies after restart.", level=SettingLevel.ADVANCED),
            FeatureRow("preap_radar:offset", "Radar lateral offset", str(saved.offset) if saved.valid else "Invalid saved configuration", source,
                       step=0.05 if saved.valid else 0.0, minimum=-FLOAT32_MAX, maximum=FLOAT32_MAX, unit="m",
                       repair_value="" if saved.valid else "0.0", available=allowed, capability=capable, vehicle_fingerprint=owner.vehicle_fingerprint(),
                       dependencies=saved.sources, reason="Parked hardware setup. Applies after restart.", level=SettingLevel.ADVANCED)]

  def apply(self, request):
    if request.key not in KEYS or request.related_source is not None or request.direction or request.display_unit:
      return False
    row = next((row for row in self.rows() if row.key == request.key), None)
    if row is None or not row.available or request.capability != row.capability or request.dependencies != row.dependencies or request.expected != row.source:
      return False
    saved = read_radar_settings(self.owner.params)
    try:
      if request.key == "preap_radar:mode":
        if request.value not in ("Off", "Front", "Behind nosecone") or (not saved.valid and request.value != "Off"):
          return False
        mode = {"Off": "off", "Front": "front", "Behind nosecone": "nosecone"}[request.value]
        raw = encode(mode, saved.offset if saved.valid else 0.0)
      else:
        if not saved.valid and request.value != "0.0":
          return False
        mode = "nosecone" if saved.behind_nosecone else "front" if saved.enabled else "off"
        raw = encode(mode, request.value)
    except (ValueError, TypeError, OverflowError):
      return False
    def authorized():
      fresh = next((row for row in self.rows() if row.key == request.key), None)
      return bool(fresh is not None and fresh.available and fresh.capability == request.capability and
                  fresh.dependencies == request.dependencies and self.owner.vehicle_fingerprint() == request.vehicle_fingerprint)
    result = commit_exact(self.owner.params, key=DOCUMENT_KEY, max_bytes=MAX_BYTES, raw=raw, expected=request.expected,
                          authorized=authorized, temp_prefix=".preap-radar-")
    return result.committed and result.verified
