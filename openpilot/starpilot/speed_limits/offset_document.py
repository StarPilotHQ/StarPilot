"""Speed-limit offset ranges and adjustments stored in fixed SI units."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import cast

from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import speed_domain as sd

MPH_TO_MPS = 0.44704
KPH_TO_MPS = 1.0 / 3.6
IMPERIAL_BOUNDS = (0.0, 11.2, 15.2, 19.6, 24.1, 28.6, 33.1, 44.2)
METRIC_BOUNDS = (0.0, 8.1, 13.6, 16.4, 21.9, 27.5, 33.1, 38.9)
MAX_DOCUMENT_BYTES = 4096


@dataclass(frozen=True)
class OffsetDocument:
  bounds_mps: tuple[float, ...]
  offsets_mps: tuple[float, ...]

  def schedule(self) -> sd.OffsetSchedule:
    bands = tuple(sd.OffsetBand(self.bounds_mps[i], self.bounds_mps[i + 1], self.offsets_mps[i]) for i in range(7))
    return sd.OffsetSchedule((*bands, sd.OffsetBand(self.bounds_mps[-1], None, 0.0)))


@dataclass(frozen=True)
class NeedsReview:
  """Saved offsets require an explicit reset before control can resume."""


NEEDS_REVIEW = NeedsReview()


def _finite(value: object) -> float:
  if not acc.finite(value):
    raise ValueError('SLC offset value must be finite')
  return float(value)


def default_document(metric: bool) -> OffsetDocument:
  if type(metric) is not bool:
    raise ValueError('invalid SLC offset units')
  return OffsetDocument(METRIC_BOUNDS if metric else IMPERIAL_BOUNDS, (0.0,) * 7)


def validate(value: object) -> OffsetDocument | NeedsReview:
  if value is NEEDS_REVIEW:
    return NEEDS_REVIEW
  if isinstance(value, OffsetDocument):
    value = {'version': 1, 'bounds_mps': value.bounds_mps, 'offsets_mps': value.offsets_mps}
  if not isinstance(value, dict) or type(value) is not dict:
    raise ValueError('invalid SLC offset document version')
  data = cast(dict[str, object], value)
  if type(data.get('version')) is not int or data['version'] != 1:
    raise ValueError('invalid SLC offset document version')
  if data.keys() == {'version', 'state'} and data['state'] == 'needs_review':
    return NEEDS_REVIEW
  if data.keys() != {'version', 'bounds_mps', 'offsets_mps'}:
    raise ValueError('invalid SLC offset document fields')
  bounds, offsets = data['bounds_mps'], data['offsets_mps']
  if not isinstance(bounds, (list, tuple)) or not isinstance(offsets, (list, tuple)) or \
     type(bounds) not in (list, tuple) or type(offsets) not in (list, tuple) or len(bounds) != 8 or len(offsets) != 7:
    raise ValueError('invalid SLC offset document shape')
  bounds_si = tuple(_finite(bound) for bound in bounds)
  offsets_si = tuple(_finite(offset) for offset in offsets)
  if bounds_si[0] != 0.0 or any(next_bound <= bound for bound, next_bound in zip(bounds_si[:-1], bounds_si[1:], strict=True)):
    raise ValueError('invalid SLC offset boundaries')
  return OffsetDocument(bounds_si, offsets_si)


def decode(raw: bytes) -> OffsetDocument | NeedsReview:
  if len(raw) > MAX_DOCUMENT_BYTES:
    raise ValueError('SLC offset document too large')
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError('duplicate SLC offset document field')
      result[key] = value
    return result
  def reject_nonfinite(_value):
    raise ValueError('nonfinite SLC offset document JSON')
  try:
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_nonfinite)
  except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
    raise ValueError('invalid SLC offset document JSON') from error
  return validate(value)


def to_value(document: OffsetDocument | NeedsReview) -> dict[str, object]:
  checked = validate(document)
  return ({'version': 1, 'state': 'needs_review'} if checked is NEEDS_REVIEW else
          {'version': 1, 'bounds_mps': list(checked.bounds_mps), 'offsets_mps': list(checked.offsets_mps)})


def encode(document: OffsetDocument | NeedsReview) -> bytes:
  return json.dumps(to_value(document), sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
