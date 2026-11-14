"""Native SLC offset editor and global unit-change owner over strict SI documents."""

from __future__ import annotations

from ctypes import c_float
from dataclasses import dataclass
import math
import threading

from openpilot.starpilot.speed_limits import offset_document as od
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, SLC_OFFSET_KEYS
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.saved_document import commit_exact

DOCUMENT_KEY = "SLCOffsetSchedule"
_LOCK = threading.RLock()


def native_parked(ui) -> bool:
  from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
  adapter = getattr(ui, '_slc_offroad_adapter', None)
  if adapter is None:
    adapter = RuntimeSnapshotAdapter(ui)
    ui._slc_offroad_adapter = adapter
  return adapter.confirmed_offroad()


@dataclass(frozen=True)
class OffsetSnapshot:
  raw_document: bytes | None
  raw_unit: bytes | None
  raw_control: bytes | None
  document: od.OffsetDocument | od.NeedsReview | None
  metric: bool | None
  valid: bool


def _strict_unit(raw: bytes | None) -> bool | None:
  if raw is None or raw == b"0":
    return False
  if raw == b"1":
    return True
  return None


def _unit_factor(metric: bool) -> float:
  return od.KPH_TO_MPS if metric else od.MPH_TO_MPS


# Each band's offset is limited to +/-20 in the comma's speed units (mph or km/h).
OFFSET_LIMIT = 20


def _minimum_offset(document: od.OffsetDocument, index: int, metric: bool) -> float:
  # A band may not offset below its own lowest speed, so the adjusted limit stays positive.
  boundary = -document.bounds_mps[index + 1] / _unit_factor(metric)
  return max(-OFFSET_LIMIT, math.floor(boundary) + 1) if math.isfinite(boundary) else -OFFSET_LIMIT


def _display(value: float) -> str:
  return str(round(value))


def _whole_limit_cutoff(bound_mps: float, factor: float) -> int:
  """First whole posted limit on or above the saved cutoff."""
  nearest = round(bound_mps / factor)
  # SLC source readings arrive as Float32.
  return nearest + (c_float(nearest * factor).value < bound_mps)


def _capability(cp, configuration_longitudinal=False) -> tuple | None:
  try:
    if cp is None or not cp.carFingerprint or not (cp.openpilotLongitudinalControl or configuration_longitudinal) or \
       cp.notCar or cp.dashcamOnly or cp.passive:
      return None
    return (str(cp.carFingerprint), bool(cp.openpilotLongitudinalControl or configuration_longitudinal), bool(cp.pcmCruise and not configuration_longitudinal),
            bool(cp.notCar), bool(cp.dashcamOnly), bool(cp.passive))
  except (AttributeError, TypeError, ValueError):
    return None


class SlcOffsetOwner:
  def __init__(self, params, parked, vehicle_params, repair_parked=None, *, configuration_longitudinal=lambda: False):
    self.params = params
    self.parked = parked
    self.repair_parked = repair_parked or parked
    self.vehicle_params = vehicle_params
    self.configuration_longitudinal = configuration_longitudinal

  def capability(self) -> tuple | None:
    return _capability(self.vehicle_params(), self.configuration_longitudinal())

  def _raw(self, key: str) -> bytes | None:
    return read_saved(self.params, key, od.MAX_DOCUMENT_BYTES)[0]

  def _readable(self, key: str) -> bool:
    return read_saved(self.params, key, od.MAX_DOCUMENT_BYTES)[1]

  def snapshot(self) -> OffsetSnapshot:
    raw_doc = self._raw(DOCUMENT_KEY)
    raw_unit = self._raw("IsMetric")
    raw_control = self._raw("SpeedLimitController")
    metric = _strict_unit(raw_unit)
    try:
      if not all(self._readable(key) for key in (DOCUMENT_KEY, "IsMetric", "SpeedLimitController")):
        raise ValueError("Unreadable SLC setting")
      if raw_doc is not None:
        document = od.decode(raw_doc)
      elif metric is not None:
        document = od.default_document(metric)
      else:
        document = None
      return OffsetSnapshot(raw_doc, raw_unit, raw_control, document, metric,
                            document is not None and metric is not None)
    except (ValueError, OSError, OverflowError):
      return OffsetSnapshot(raw_doc, raw_unit, raw_control, None, metric, False)

  def percentage(self) -> bool:
    """Offset type Percentage: one percentage of every limit instead of the speed ranges."""
    raw = self._raw("SLCOffsetType")
    return raw == b"1"

  def ready_to_enable(self) -> bool:
    snap = self.snapshot()
    return snap.valid and isinstance(snap.document, od.OffsetDocument)

  def rows(self, allowed: bool, *, repair_allowed: bool | None = None) -> tuple[bool, tuple[FeatureRow, ...]]:
    snap = self.snapshot()
    capable = self.capability()
    available = allowed and capable is not None
    repair_available = available if repair_allowed is None else repair_allowed and capable is not None
    deps = (("IsMetric", snap.raw_unit), ("SpeedLimitController", snap.raw_control))
    if isinstance(snap.document, od.OffsetDocument):
      mode = "Percentage of the limit" if self.percentage() else "Fixed speed ranges"
    elif snap.document is od.NEEDS_REVIEW:
      mode = "Saved offsets need review; SLC control is paused"
    else:
      mode = "Invalid saved offsets; SLC control is paused"
    rows = [FeatureRow("", "Offset policy", mode)]
    usable = isinstance(snap.document, od.OffsetDocument) and snap.metric is not None
    # Percentage offsets do not use the speed ranges; their one setting sits with the Offset type row.
    if usable and not self.percentage():
      unit = "km/h" if snap.metric else "mph"
      factor = _unit_factor(snap.metric)
      scale = 1 / factor
      display_bounds = tuple(_whole_limit_cutoff(bound, factor) for bound in snap.document.bounds_mps)
      for index, (lower, next_lower) in enumerate(zip(display_bounds[:-1], display_bounds[1:], strict=True)):
        upper = next_lower - 1
        if lower > upper:
          label = f"Band {index + 1} offset (no whole-number limits)"
        else:
          limits = str(lower) if lower == upper else f"{lower}–{upper}"
          label = f"{limits} {unit} offset"
        value = _display(snap.document.offsets_mps[index] * scale)
        rows.append(FeatureRow(SLC_OFFSET_KEYS[index], label, value, snap.raw_document,
                               step=1, unit=unit,
                               minimum=_minimum_offset(snap.document, index, snap.metric), maximum=OFFSET_LIMIT,
                               available=available,
                               reason="Ranges show whole-number posted limits. Unsafe reductions use the unadjusted limit",
                               related_source=snap.raw_unit, capability=capable, dependencies=deps,
                               display_unit=unit, default_value="0"))
      rows.append(FeatureRow("", f"{display_bounds[-1]} {unit} and above", "Zero offset"))
    elif not usable:
      for index, key in enumerate(SLC_OFFSET_KEYS, start=1):
        label = f"Band {index} offset (invalid saved units)" if snap.metric is None else f"Band {index} offset"
        rows.append(FeatureRow(key, label, "Invalid saved offset or document", snap.raw_document,
                               reason="Unavailable until saved source is repaired"))
    reset_needed = snap.raw_document is not None or snap.document is None
    reset_available = (repair_available and reset_needed and snap.metric is not None and
                       all(self._readable(key) for key, _ in deps) and self._readable(DOCUMENT_KEY))
    if reset_needed:
      reset_ranges = ("Keep saved speed ranges" if isinstance(snap.document, od.OffsetDocument) else
                      f"Use default {'km/h' if snap.metric else 'mph'} speed ranges" if snap.metric is not None else
                      "Repair saved units first")
      rows.append(FeatureRow("slc_reset", "Reset saved offsets", reset_ranges, snap.raw_document,
                             available=reset_available, related_source=snap.raw_unit, capability=capable,
                             dependencies=deps))
    return snap.valid and isinstance(snap.document, od.OffsetDocument), tuple(rows)

  def _same(self, request: FeatureSettingsRequest, snap: OffsetSnapshot, *, require_capability: bool) -> bool:
    expected_deps = (("IsMetric", snap.raw_unit), ("SpeedLimitController", snap.raw_control))
    authority = self.repair_parked if request.key == "slc_reset" else self.parked
    return (authority() and (not require_capability or request.capability is not None and request.capability == self.capability()) and
            request.dependencies == expected_deps and
            self._raw(DOCUMENT_KEY) == request.expected and self._readable(DOCUMENT_KEY) and
            self._raw("IsMetric") == request.related_source and self._readable("IsMetric") and
            all(self._raw(key) == raw and self._readable(key) for key, raw in request.dependencies) and
            snap.raw_control == self._raw("SpeedLimitController"))

  def apply(self, request: FeatureSettingsRequest) -> bool:
    if request.key not in ("slc_reset", *SLC_OFFSET_KEYS):
      return False
    with _LOCK:
      snap = self.snapshot()
      if not self._same(request, snap, require_capability=True):
        return False
      try:
        if request.key == "slc_reset":
          if not request.confirmation or snap.metric is None or \
             request.expected is None and isinstance(snap.document, od.OffsetDocument):
            return False
          if isinstance(snap.document, od.OffsetDocument):
            result = od.OffsetDocument(snap.document.bounds_mps, (0.0,) * 7)
          else:
            result = od.default_document(snap.metric)
        else:
          if not isinstance(snap.document, od.OffsetDocument) or snap.metric is None or self.percentage() or \
             request.direction not in (-1, 0, 1) or request.display_unit != ("km/h" if snap.metric else "mph"):
            return False
          index = SLC_OFFSET_KEYS.index(request.key)
          current = snap.document.offsets_mps[index]
          low, high = _minimum_offset(snap.document, index, snap.metric), float(OFFSET_LIMIT)
          factor = _unit_factor(snap.metric)
          candidate = (round(float(request.value)) * factor if request.direction == 0 else
                       max(low, min(high, round(current / factor) + request.direction)) * factor)
          if not math.isfinite(candidate) or not low * factor <= candidate <= high * factor:
            return False
          new_offsets = list(snap.document.offsets_mps)
          new_offsets[index] = candidate
          result = od.OffsetDocument(snap.document.bounds_mps, tuple(new_offsets))
        value = od.to_value(result)
        if not self._same(request, snap, require_capability=True):
          return False
        self.params.put(DOCUMENT_KEY, value, block=True)
        saved = self._raw(DOCUMENT_KEY)
        return saved is not None and od.decode(saved) == result
      except (OSError, ValueError, TypeError, OverflowError):
        return False

  def change_units(self, desired: bool, expected_unit: bytes | None) -> bool:
    """Change display units without changing the saved physical offsets."""
    if type(desired) is not bool:
      return False
    with _LOCK:
      result = commit_exact(self.params, key="IsMetric", max_bytes=8,
                            raw=b"1" if desired else b"0", expected=expected_unit,
                            authorized=lambda: self._readable("IsMetric") and self._raw("IsMetric") == expected_unit,
                            temp_prefix=".display-units-")
      return result.committed and result.verified
