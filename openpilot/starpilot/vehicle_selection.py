"""One source-bound vehicle choice; Auto retains native fingerprinting."""

from collections.abc import Callable
from dataclasses import dataclass
import json

from opendbc.car.mock.values import CAR as MOCK
from opendbc.car.values import PLATFORMS
from openpilot.starpilot.saved_document import WriteResult, commit_exact
from openpilot.starpilot.saved_source import read_saved


KEY = 'VehicleSelection'
MAX_BYTES = 512
VERSION = 1


@dataclass(frozen=True)
class VehicleChoice:
  platform: str
  make: str
  label: str


@dataclass(frozen=True)
class SelectionSnapshot:
  raw: bytes | None
  readable: bool
  valid: bool
  platform: str | None


def choices() -> tuple[VehicleChoice, ...]:
  result = []
  for platform, member in PLATFORMS.items():
    if platform == MOCK.MOCK:
      continue
    docs = member.config.car_docs
    make = docs[0].make if docs else member.__class__.__module__.split('.')[-2].title()
    label = docs[0].name if len(docs) == 1 else platform.replace('_', ' ').title()
    if platform == "TOYOTA_PRIUS":
      label = "Toyota Prius / Prius Prime 2016-20"
    elif platform == "TOYOTA_PRIUS_RETROFIT":
      label = "Toyota Prius 2016-20 (TSS2 EPS retrofit)"
    result.append(VehicleChoice(platform, make, label))
  return tuple(sorted(result, key=lambda item: (item.make, item.label, item.platform)))


def encode(platform: str | None) -> bytes:
  if platform is not None and (type(platform) is not str or platform not in PLATFORMS or platform == MOCK.MOCK):
    raise ValueError('unregistered vehicle')
  return json.dumps({'version': VERSION, 'platform': platform}, separators=(',', ':'), sort_keys=True).encode()


def decode(raw: bytes | None) -> str | None:
  if raw is None:
    return None

  def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError('duplicate key')
      result[key] = value
    return result

  document = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)
  if type(document) is not dict or set(document) != {'version', 'platform'} or type(document['version']) is not int or document['version'] != VERSION:
    raise ValueError('invalid vehicle selection')
  platform = document['platform']
  if platform is not None and (type(platform) is not str or platform not in PLATFORMS or platform == MOCK.MOCK):
    raise ValueError('unregistered vehicle')
  return platform


def read_selection(params) -> SelectionSnapshot:
  raw, readable = read_saved(params, KEY, MAX_BYTES)
  if not readable:
    return SelectionSnapshot(raw, False, False, None)
  try:
    return SelectionSnapshot(raw, True, True, decode(raw))
  except (UnicodeError, ValueError, TypeError):
    return SelectionSnapshot(raw, True, False, None)


def startup_candidate(params, *, developer_fingerprint: bool = False) -> str | None:
  if developer_fingerprint:
    return None
  snapshot = read_selection(params)
  # Invalid saved intent must not quietly select a different car through CAN.
  return snapshot.platform if snapshot.readable and snapshot.valid else MOCK.MOCK


class VehicleSelectionOwner:
  def __init__(self, params, authorized: Callable[[], bool]):
    self.params = params
    self.authorized = authorized

  def snapshot(self) -> SelectionSnapshot:
    return read_selection(self.params)

  def choices(self) -> tuple[VehicleChoice, ...]:
    return choices()

  def choose(self, expected_raw: bytes | None, platform: str | None) -> WriteResult:
    try:
      desired = encode(platform)
    except ValueError:
      return WriteResult(False, False)
    return commit_exact(self.params, key=KEY, max_bytes=MAX_BYTES, raw=desired,
                        expected=expected_raw, authorized=self.authorized,
                        temp_prefix='.vehicle-selection-')
