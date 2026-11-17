"""Explicit live tracking trial protocol, distinct from replay fit acceptance."""

from dataclasses import dataclass
from collections import OrderedDict
import math
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, canonical_names, coordinate_bounds, full_profile_sha256

VERSION = 'live-tracking-trial-v1'
MAX_STEP = 0.05
MAX_LEASE_NS = 500_000_000
SLEW_PER_SECOND = 0.005
# A one-percent source experiment ramps at the unchanged 0.5%/s slew.
# Native probes must measure this same amplitude before selection.
SOURCE_EXPERIMENT_STEP = 2 * SLEW_PER_SECOND
MAX_TRIAL_NS = 120_000_000_000


def _text(value, maximum=96):
  return type(value) is str and 0 < len(value) <= maximum and value.isascii() and value.isprintable()


def _digest(value):
  return type(value) is str and len(value) == 64 and not value.strip('0123456789abcdef')


@dataclass(frozen=True)
class LiveTrialBinding:
  identity: str
  cp_sha256: str
  controller_revision: str
  baseline_revision: str
  source_epoch: str
  reset_generation: str
  calibration_generation: str
  vehicle_model_generation: str
  native_identity: str
  physical_model_revision: str
  persistent_source_epoch: str
  native_required: bool = False

  def validate(self):
    if type(self) is not LiveTrialBinding or not all(_digest(value) for value in (self.identity, self.cp_sha256, self.baseline_revision, self.source_epoch)):
      raise ValueError('live tracking identity binding')
    if self.persistent_source_epoch != '' and not _digest(self.persistent_source_epoch):
      raise ValueError('optional persistent source identity')
    if type(self.native_required) is not bool or (not _digest(self.native_identity) if self.native_required else self.native_identity != ''):
      raise ValueError('live ordinary/required native domain')
    if not all(
      _text(value, 192)
      for value in (self.controller_revision, self.reset_generation, self.calibration_generation, self.vehicle_model_generation, self.physical_model_revision)
    ):
      raise ValueError('live tracking source/model generation')
    return self


@dataclass(frozen=True)
class LiveTrialOffer:
  version: str
  binding: LiveTrialBinding
  trial_id: str
  sequence: int
  phase: str
  group: str
  names: tuple[str, ...]
  baseline: tuple[tuple[str, float], ...]
  target: tuple[tuple[str, float], ...]
  baseline_sha256: str
  target_sha256: str
  started_ns: int
  observed_ns: int
  expires_ns: int
  response_witness: object = None
  target_sequence: int = 1
  deadline_ns: int = 0

  def validate(self):
    return validate_offer(self)


def full_coordinates(rows, baseline_revision):
  if (
    type(rows) is not tuple
    or len(rows) != len(NAMES)
    or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str or type(row[1]) not in (int, float) or not math.isfinite(row[1]) for row in rows)
    or tuple(row[0] for row in rows) != NAMES
  ):
    raise ValueError('exact immutable live full30 coordinates')
  for name, value in rows:
    low, high = coordinate_bounds(name)
    if not low <= value <= high:
      raise ValueError('original baseline25percent canonical bound')
  return full_profile_sha256(baseline_revision, rows)


_OWNED_OFFER_CONTENT = OrderedDict()


def _content_refs(offer):
  # Deep values were validated as exact immutable primitives before retention.
  # No receiver permission, freshness or time verdict is retained here.
  cls = type(offer)
  if cls not in (LiveTrialOffer, LiveRestoreOffer) or cls.__mro__ != (cls, object) or LiveTrialBinding.__mro__ != (LiveTrialBinding, object):
    return None
  if '__getattr__' in vars(cls) or '__getattribute__' in vars(cls):
    return None
  own = object.__getattribute__(offer, '__dict__')
  if type(own) is not dict or type(own.get('binding')) is not LiveTrialBinding:
    return None
  binding = object.__getattribute__(own['binding'], '__dict__')
  if (
    type(own) is not dict
    or type(binding) is not dict
    or set(own) != set(cls.__dataclass_fields__)
    or set(binding) != set(LiveTrialBinding.__dataclass_fields__)
  ):
    return None
  if (
    '__getattr__' in vars(LiveTrialOffer)
    or '__getattribute__' in vars(LiveTrialOffer)
    or '__getattr__' in vars(LiveTrialBinding)
    or '__getattribute__' in vars(LiveTrialBinding)
  ):
    return None
  for checked_class in (cls, LiveTrialBinding):
    for name in checked_class.__dataclass_fields__:
      value = vars(checked_class).get(name)
      if value is not None and hasattr(type(value), '__get__'):
        return None
  return (
    own,
    binding,
    tuple(own[name] for name in cls.__dataclass_fields__),
    tuple(binding[name] for name in LiveTrialBinding.__dataclass_fields__),
  )


def _content_current(offer):
  saved = _OWNED_OFFER_CONTENT.get(id(offer))
  current = _content_refs(offer)
  return bool(
    saved is not None
    and saved[0] is offer
    and current is not None
    and current[0] is saved[1][0]
    and current[1] is saved[1][1]
    and all(a is b for a, b in zip(current[2], saved[1][2], strict=True))
    and all(a is b for a, b in zip(current[3], saved[1][3], strict=True))
  )


def validate_offer(offer):
  if _content_refs(offer) is None:
    raise ValueError('owned plain live offer shape')
  if _content_current(offer):
    return offer
  if type(offer) is not LiveTrialOffer or type(offer.version) is not str or offer.version != VERSION or type(offer.binding) is not LiveTrialBinding:
    raise ValueError('explicit live tracking protocol')
  offer.binding.validate()
  from openpilot.starpilot.flm.live_tracking_response import validate_response_witness

  witness = validate_response_witness(offer.response_witness, offer.binding, offer.observed_ns)
  if (
    not _digest(offer.trial_id)
    or type(offer.sequence) is not int
    or not 0 < offer.sequence < 2**64
    or type(offer.phase) is not str
    or offer.phase not in ('trial', 'confirmed', 'rollback')
    or not _text(offer.group, 64)
  ):
    raise ValueError('live trial identity/sequence/phase')
  if type(offer.names) is not tuple or any(type(name) is not str for name in offer.names) or canonical_names(offer.names) != offer.names:
    raise ValueError('one to four canonical live trial coordinates')
  from openpilot.starpilot.flm.canonical_surface_adapter import GROUPS

  if offer.group not in GROUPS or not set(offer.names).issubset(GROUPS[offer.group]):
    raise ValueError('live canonical group')
  if (
    not _digest(offer.baseline_sha256)
    or not _digest(offer.target_sha256)
    or full_coordinates(offer.baseline, offer.binding.baseline_revision) != offer.baseline_sha256
    or full_coordinates(offer.target, offer.binding.baseline_revision) != offer.target_sha256
  ):
    raise ValueError('live full30 baseline/target hash')
  if any(
    type(v) is not int for v in (offer.started_ns, offer.observed_ns, offer.expires_ns)
  ) or not 0 < offer.started_ns <= offer.observed_ns < offer.expires_ns <= min(offer.observed_ns + MAX_LEASE_NS, witness.valid_until_ns):
    raise ValueError('live trial immutable start/current lease')
  before = dict(offer.baseline)
  for name, value in offer.target:
    if name not in offer.names and value != before[name] or abs(value - before[name]) > MAX_STEP + 1e-12:
      raise ValueError('small selected live trial step')
  if (
    type(offer.target_sequence) is not int
    or not 0 < offer.target_sequence < 2**64
    or type(offer.deadline_ns) is not int
    or not offer.started_ns < offer.deadline_ns <= offer.started_ns + MAX_TRIAL_NS
  ):
    raise ValueError('immutable live target sequence/deadline')
  if offer.phase == 'trial' and offer.observed_ns > offer.deadline_ns:
    raise ValueError('live pending deadline exhausted')
  if offer.phase == 'rollback' and offer.target != offer.baseline:
    raise ValueError('rollback exact immutable baseline only')
  refs = _content_refs(offer)
  if refs is not None:
    _OWNED_OFFER_CONTENT[id(offer)] = (offer, refs)
    _OWNED_OFFER_CONTENT.move_to_end(id(offer))
    while len(_OWNED_OFFER_CONTENT) > 8:
      _OWNED_OFFER_CONTENT.popitem(last=False)
  return offer


RESTORE_VERSION = 'live-tracking-restore-v1'


@dataclass(frozen=True)
class LiveRestoreOffer:
  version: str
  binding: LiveTrialBinding
  history_scope: str
  history_trial_id: str
  target: tuple[tuple[str, float], ...]
  target_sha256: str
  sequence: int
  observed_ns: int
  expires_ns: int
  response_witness: object

  def validate(self):
    if _content_refs(self) is None:
      raise ValueError('owned plain restore offer shape')
    if _content_current(self):
      return self
    if type(self) is not LiveRestoreOffer or type(self.version) is not str or self.version != RESTORE_VERSION or type(self.binding) is not LiveTrialBinding:
      raise ValueError('separate confirmed-history restore protocol')
    self.binding.validate()
    if (
      not _digest(self.history_scope)
      or not _digest(self.target_sha256)
      or not _digest(self.history_trial_id)
      or full_coordinates(self.target, self.binding.baseline_revision) != self.target_sha256
    ):
      raise ValueError('restore full30 historical target')
    if (
      type(self.sequence) is not int
      or not 0 < self.sequence < 2**64
      or type(self.observed_ns) is not int
      or type(self.expires_ns) is not int
      or not 0 < self.observed_ns < self.expires_ns <= self.observed_ns + MAX_LEASE_NS
    ):
      raise ValueError('restore sequence/lease')
    from openpilot.starpilot.flm.live_tracking_response import validate_response_witness

    witness = validate_response_witness(self.response_witness, self.binding, self.observed_ns)
    if self.expires_ns > witness.valid_until_ns:
      raise ValueError('restore cannot extend current response witness')
    refs = _content_refs(self)
    if refs is not None:
      _OWNED_OFFER_CONTENT[id(self)] = (self, refs)
      _OWNED_OFFER_CONTENT.move_to_end(id(self))
      while len(_OWNED_OFFER_CONTENT) > 8:
        _OWNED_OFFER_CONTENT.popitem(last=False)
    return self
