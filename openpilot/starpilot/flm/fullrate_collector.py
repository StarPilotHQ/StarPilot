"""Bounded fullrate command ownership and independent physical response join.

Runs in a separate learning process, never inside Controls/Card or torqued.
Inputs are decoded custom evidence produced by actual owners; no Params/default
controller selection is inferred here. Domain adapters qualify response inverse.
"""

from typing import TypeGuard, cast

from collections import Counter, OrderedDict, deque
from openpilot.starpilot.flm.torque_eligibility import asynchronous_hint, bounded_context_output, exact_unclipped_pair, consumed_sources_current
from openpilot.starpilot.flm.native_refinement_wire import valid_inputs

from dataclasses import dataclass, replace
import math
import re
import uuid

from openpilot.starpilot.flm.adaptive_profile import Block, Sample, Result, evaluate, replay_accepts, readmit_profile

_IDENTITY_HEX64 = re.compile(r'[0-9a-f]{64}')

MAX_PENDING = 128
MAX_DRAIN = 32
MAX_POINTS = 240
MAX_RAW_POINTS = 80  # 16*80/20Hz*100Hz +16*100 warmup frames <=8160.
MAX_BLOCKS = 24
MAX_AGE_NS = 100_000_000
JOIN_NS = 30_000_000
ACTIVE_NS = 2_000_000_000
EXCLUSION_NS = 1_000_000_000
MAX_WIRE_BYTES = 32768
MAX_SNAPSHOT_WIRE_BYTES = 131072


def finite(value: object) -> TypeGuard[int | float]:
  return (type(value) is int or type(value) is float) and math.isfinite(value)


def replay_values(value, bound):
  return bool(
    type(value) is tuple
    and len(value) <= bound
    and len({name for name, _ in value}) == len(value)
    and all(type(name) is str and 0 < len(name) <= 96 and (type(item) is bool or finite(item) or type(item) is str and len(item) <= 96) for name, item in value)
  )


def identity(value):
  return type(value) is str and len(value) == 64 and _IDENTITY_HEX64.fullmatch(value) is not None


@dataclass(frozen=True)
class ControlEvidence:
  identity: str
  generation: int
  control_ns: int
  state_ns: int
  domain: str
  stage_revision: str
  response_revision: str
  requested: float
  features: tuple[float, ...]
  baseline_prediction: float
  enabled: bool
  manual: bool
  active: bool
  valid: bool
  override: bool
  fault: bool
  saturated: bool
  limited: bool
  model_wobble: bool
  controller_chatter: bool
  stage_schema_revision: str = ''
  stage_values: tuple[tuple[str, float], ...] = ()
  applied_coordinates: tuple[float, ...] = ()
  source_epoch: str = ''
  source_receipt_ns: int = 0
  controller_mode: str = ''
  policy: str = ''
  baseline_revision: str = ''
  baseline_values: tuple[float, ...] = ()
  controller_revision: str = ''
  replay_snapshot: tuple[tuple[str, object], ...] = ()
  replay_inputs: tuple[tuple[str, object], ...] = ()
  snapshot_sequence: int = 0
  turn_assist: bool = False
  applied_parameter_names: tuple[str, ...] = ()
  native_required: bool = False
  native_observed_ns: int = 0
  native_expires_ns: int = 0
  native_allowed: bool = False
  native_identity: str = ''
  applied_profile_sequence: int = 0
  applied_profile_observed_ns: int = 0
  applied_profile_expires_ns: int = 0
  effect_trace: object | None = None
  effect_prepare_ns: int = 0
  vehicle_model_anchor: tuple[float, ...] = ()
  live_applied_coordinates: tuple[float, ...] = ()
  route_applied_coordinates: tuple[tuple[str, float], ...] = ()
  route_effect_revision: str = ''
  route_configuration_scope: str = ''


@dataclass(frozen=True)
class AppliedEvidence:
  control_ns: int
  state_ns: int
  apply_ns: int
  domain: str
  applied: float
  valid: bool
  active: bool
  override: bool
  fault: bool
  limited: bool
  native_allowed: bool
  projection: object = None
  carrier_frame: int = 0
  carrier_observed_ns: int = 0


@dataclass(frozen=True)
class PhysicalEvidence:
  mono_ns: int
  received_ns: int
  calibration: str
  model_generation: str
  lag_generation: str
  lag_ns: int
  speed_mps: float
  yaw_rate: float
  roll_rad: float
  steering_angle_deg: float
  valid: bool
  override: bool
  fault: bool
  # Current physical and authority witnesses, not historical healthy booleans.
  native_allowed: bool
  source_boot_ns: int = 0
  clock_offset_ns: int = 0
  clock_uncertainty_ns: int = 0


@dataclass(frozen=True)
class RawPoint:
  control_ns: int
  apply_ns: int
  mono_ns: int
  control: ControlEvidence
  output: AppliedEvidence
  physical: dict
  physical_context: tuple


@dataclass(frozen=True)
class RawBlock:
  identity: str
  block_id: str
  cohort: str
  stage_revision: str
  response_revision: str
  samples: tuple[RawPoint, ...]


class FullrateCollector:
  _calibration_generation: str

  def __init__(self, adapter):
    self.adapter = adapter
    self.live_tracking = None
    self.live_response_pair = None
    self._last_live_ingest_ns = 0
    self.session = uuid.uuid4().hex
    self.sequence = self.block_sequence = 0
    self.controls = OrderedDict()
    self.applied = OrderedDict()
    self.recent_controls = OrderedDict()
    self.recent_applied = OrderedDict()
    self.ready = deque(maxlen=MAX_PENDING)
    # Historical NUMERICAL inputs only. Pending/live joins keep MAX_PENDING;
    # the actor still enforces original2s age, source/native identity and expiry.
    self.probe_ready = deque(maxlen=256)
    self.last_carrier_frame = 0
    self.blocks = deque(maxlen=MAX_BLOCKS)
    self.points = []
    self.raw_points = []
    self.context = self.physical_context = self.cohort = None
    self.active_since = self.last_apply = self.last_response = 0
    self.excluded_until = 0
    self.last_qualified_response = 0
    self.last_fit = 0
    self.fit_result = self.fit_validation = self.last_validation = None
    self.prior_coefficients = None
    self.live_profile_sequence = 0
    self.fit_block_sequence = 0
    self.result = None
    self.reason = 'initializing'
    self.training_interruptions = Counter()
    self.dropped = 0
    self.sealed = False
    self.model_block_sequence = self.model_points = 0
    self.model_sealed = False

  def enable_original_source_capture(self, *, clock_contract=None):
    """Explicit private experiment; never called by the production parent."""
    from openpilot.starpilot.flm.original_source_capture import OriginalSourceCapture, OriginalSourceClockContract

    if getattr(self, 'original_source_capture', None) is not None:
      raise ValueError('original source capture already enabled')
    if clock_contract is None:
      from openpilot.starpilot.flm.producer_clock_equivalence import pinned_default_contract

      clock_contract = pinned_default_contract()
    if not isinstance(clock_contract, OriginalSourceClockContract):
      raise ValueError('independently pinned original producer-clock contract required')
    clock_contract.validate()
    enable_context = getattr(self.adapter, 'enable_original_context_capture', None)
    if enable_context is None:
      raise ValueError('actual original context-only archive required')
    enable_context()
    self.original_source_clock_contract = clock_contract
    self.original_source_capture = OriginalSourceCapture()

  def begin_original_source_experiment(self, receipt):
    capture = getattr(self, 'original_source_capture', None)
    if capture is None:
      raise ValueError('original source capture not enabled')
    capture.begin_epoch(receipt)

  def reset_original_source_capture(self, reason, *, hard=False):
    capture = getattr(self, 'original_source_capture', None)
    if capture is None:
      return
    pending = getattr(self, '_original_source_pose_queue', ())
    if hard:
      # Revoke every previously seen/pending source publication. Clearing seen
      # alone would recapture the same old KF publication after OFF/ON.
      floor = max(
        (
          getattr(self, '_original_source_revoked_before_ns', 0),
          getattr(self, '_original_source_seen_pose', 0),
          *(row.mono_ns for row in pending),
          *(row.mono_ns for row in capture.rows[-1:]),
        )
      )
      self._original_source_revoked_before_ns = floor
      for name in (
        '_original_source_pose_context',
        '_original_source_clock',
        '_original_source_calibrator',
        '_original_source_vm_anchor',
        '_original_source_equivalence',
      ):
        self.__dict__.pop(name, None)
      self._original_source_seen_pose = 0
      self._original_source_clock_generation = getattr(self, '_original_source_clock_generation', 0) + 1
    self._original_source_pose_queue = ()
    capture.reset(reason, hard=hard)

  def original_source_pose(self, value, now_ns):
    capture = getattr(self, 'original_source_capture', None)
    if capture is None:
      return False
    # The 5120 raw-row cap includes legacy retained rows and the <=4 original
    # pending poses. No second 5120-row allowance is created by this opt-in.
    retained = len(self.raw_points) + sum(len(block.samples) for block in self.blocks)
    pending = len(getattr(self, '_original_source_pose_queue', ()))
    return capture.add(value, now_ns, retained_raw_rows=retained + pending)

  def split(self, reason, *, reset=False):
    self.reset_original_source_capture(reason, hard=reset)
    if self.live_tracking is not None:
      self.live_tracking.interrupt(reason, context_changed=reset)
    self.live_response_pair = None
    if reason in self.training_interruptions or len(self.training_interruptions) < 24:
      self.training_interruptions[reason] += 1
    self.controls.clear()
    self.applied.clear()
    self.recent_controls.clear()
    self.recent_applied.clear()
    self.ready.clear()
    self.probe_ready.clear()
    self.last_carrier_frame = 0
    self.points.clear()
    self.raw_points.clear()
    self.active_since = self.last_apply = self.last_response = self.last_qualified_response = 0
    self.cohort = None
    self.sealed = False
    self.model_block_sequence += 1
    self.model_points = 0
    self.model_sealed = False
    self.reason = reason
    self.result = self.last_validation = None
    if reset:
      self.blocks.clear()
      self.result = self.fit_result = self.fit_validation = None
      self.physical_context = None
      self.fit_block_sequence = 0

  def _retain_original_pair(self, command, output):
    # Called only AFTER existing source/receiver/native admission above.
    # Keep the exact same objects and clocks; this supplies no new proof.
    pair = (command, output)
    self.ready.append(pair)
    self.probe_ready.append(pair)

  def overrun(self):
    self.dropped += 1
    self.split('fullrate_overrun')

  def _retains_controller_chatter(self, value):
    # Legacy/custom collection never opts into the event-only trial contract.
    return False

  def control(self, value, now_ns):
    if isinstance(value, ControlEvidence) and (value.manual or not value.enabled):
      self.reset_original_source_capture('original_source_disabled_or_manual', hard=True)
    if isinstance(value, ControlEvidence) and value.effect_trace is not None:
      try:
        from openpilot.starpilot.flm.effect_trace import FullEffectTrace

        trace = value.effect_trace
        if not isinstance(trace, FullEffectTrace):
          raise ValueError('typed full effect trace')
        trace.validate()
        if (
          trace.control_ns != value.effect_prepare_ns
          or trace.envelope.source_epoch != value.source_epoch
          or trace.envelope.selected_baseline_revision != value.baseline_revision
          or trace.envelope.controller_revision != value.controller_revision
          or any(value.applied_coordinates)
        ):
          raise ValueError('full effect trace source mismatch')
        if trace.qualified and (
          value.applied_profile_sequence <= 0
          or not 0
          < value.applied_profile_observed_ns
          <= value.control_ns
          <= value.applied_profile_expires_ns
          <= value.applied_profile_observed_ns + 500_000_000
        ):
          raise ValueError('applied full30 lacks original profile lease')
      except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        self.split('full_effect_trace_invalid')
        return
    if (
      not isinstance(value, ControlEvidence)
      or not identity(value.identity)
      or type(value.generation) is not int
      or value.generation < 0
      or type(value.control_ns) is not int
      or type(value.state_ns) is not int
      or not 0 < value.state_ns <= value.control_ns <= now_ns
      or value.domain != 'normalized_torque'
      and value.control_ns - value.state_ns > JOIN_NS
      or not 1 <= len(value.features) <= 8
      or not all(finite(v) for v in (value.requested, value.baseline_prediction, *value.features, *value.applied_coordinates))
      or len(value.stage_values) > 40
      or not value.stage_schema_revision
      or len({name for name, _ in value.stage_values}) != len(value.stage_values)
      or any(not isinstance(name, str) or not 0 < len(name) <= 64 or not finite(v) for name, v in value.stage_values)
      or len(value.applied_coordinates) != len(value.features)
      or not identity(value.source_epoch)
      or not identity(value.baseline_revision)
      or type(value.source_receipt_ns) is not int
      or not 0 < value.source_receipt_ns <= value.control_ns
      or value.control_ns - value.source_receipt_ns > 500_000_000
      or not value.controller_mode
      or not value.policy
      or not value.controller_revision
      or not replay_values(value.replay_snapshot, 1024)
      or not valid_inputs(value.replay_inputs)
      or type(value.snapshot_sequence) is not int
      or value.snapshot_sequence < 0
      or type(value.turn_assist) is not bool
      or type(value.native_required) is not bool
      or type(value.native_allowed) is not bool
      or type(value.native_observed_ns) is not int
      or type(value.native_expires_ns) is not int
      or any(type(v) is not int or v < 0 for v in (value.applied_profile_sequence, value.applied_profile_observed_ns, value.applied_profile_expires_ns))
      or type(value.native_identity) is not str
      or value.native_identity
      and not identity(value.native_identity)
      or len(value.applied_parameter_names) > 8
      or len(set(value.applied_parameter_names)) != len(value.applied_parameter_names)
      or (
        value.applied_parameter_names
        and (
          value.applied_profile_sequence <= 0
          or not 0
          < value.applied_profile_observed_ns
          <= value.control_ns
          <= value.applied_profile_expires_ns
          <= value.applied_profile_observed_ns + 500_000_000
        )
      )
      or any(type(name) is not str or not 0 < len(name) <= 96 for name in value.applied_parameter_names)
      or not 1 <= len(value.baseline_values) <= 32
      or not all(finite(v) for v in value.baseline_values)
      or not value.stage_revision
      or not value.response_revision
    ):
      self.split('invalid_control')
      return False
    context = (
      value.identity,
      value.generation,
      value.domain,
      value.stage_revision,
      value.response_revision,
      value.source_epoch,
      value.controller_mode,
      value.policy,
      value.baseline_revision,
      value.controller_revision,
      value.turn_assist,
    )
    if self.context != context:
      self.split('actual_controller_identity_changed', reset=True)
      self.context = context
    if value.override:
      self.excluded_until = now_ns + EXCLUSION_NS
    if (
      not value.enabled
      or value.manual
      or not value.active
      or not value.valid
      or value.override
      or value.fault
      or value.saturated
      or value.limited
      and not (getattr(self.adapter, 'raw_collection', False) and asynchronous_hint(value))
      or value.model_wobble
      or value.controller_chatter
      and not self._retains_controller_chatter(value)
      or now_ns < self.excluded_until
    ):
      self.split('ineligible_control')
      return False
    if now_ns - value.control_ns > MAX_AGE_NS or value.control_ns - value.state_ns > JOIN_NS:
      # Receiver or normalized source-state delay still rejects this row.
      # Only inert study data may survive; native refusal is not a timing gap.
      if value.native_required and (
        not value.native_allowed
        or not identity(value.native_identity)
        or not 0 < value.native_observed_ns <= value.control_ns <= value.native_expires_ns <= value.native_observed_ns + 200_000_000
      ):
        self.split('original_native_admission_unavailable')
        return False
      self.split('stale_control')
      return False
    previous = self.recent_controls.get(value.control_ns)
    if previous is not None:
      if previous != value:
        self.split('changed_duplicate_control', reset=True)
        return False
      return True
    # A joined command leaves controls but remains part of this producer tape.
    # Cross-stream ingestion must not hide its ordering boundary after a join.
    if self.recent_controls and value.control_ns <= next(reversed(self.recent_controls)):
      self.split('reordered_control')
      return False
    self._last_live_ingest_ns = now_ns
    if self.live_tracking is not None:
      self.live_tracking.observe_source_control(value, now_ns=now_ns, current_native=getattr(self, '_learning_current_native_allowed', False))
    self.controls[value.control_ns] = value
    self.recent_controls[value.control_ns] = value
    while len(self.recent_controls) > MAX_PENDING:
      self.recent_controls.popitem(last=False)
    while len(self.controls) > MAX_PENDING:
      self.controls.popitem(last=False)
    self._join(value.control_ns)
    return True

  def output(self, value, now_ns):
    context_only = getattr(self.adapter, 'observe_original_applied', None)
    if context_only is not None:
      context_only(value)  # Original owner receipt, never ready/plant permission.
    if (
      not isinstance(value, AppliedEvidence)
      or not finite(value.applied)
      or any(type(n) is not int for n in (value.control_ns, value.state_ns, value.apply_ns))
      or not 0 < value.control_ns <= value.apply_ns <= now_ns
      or not 0 < value.state_ns <= value.apply_ns
      or value.apply_ns - value.control_ns > MAX_AGE_NS
    ):
      self.split('invalid_applied_output')
      return False
    if value.override:
      self.excluded_until = now_ns + EXCLUSION_NS
    if (
      not value.valid
      or not value.active
      or value.override
      or value.fault
      or value.limited
      and not (getattr(self.adapter, 'raw_collection', False) and bounded_context_output(value))
      or now_ns < self.excluded_until
    ):
      self.split('ineligible_applied_output')
      return False
    if value.domain == 'normalized_torque':
      if (
        type(value.carrier_frame) is not int
        or value.carrier_frame <= 0
        or type(value.carrier_observed_ns) is not int
        or not value.apply_ns <= value.carrier_observed_ns <= now_ns
        or self.last_carrier_frame
        and value.carrier_frame not in (self.last_carrier_frame, self.last_carrier_frame + 1)
      ):
        self.split('actual_carrier_gap_or_clock', reset=True)
        return False
      record = getattr(self.adapter, 'record_actual_carrier', None)
      if record is None or not record(value):
        self.split('actual_carrier_unavailable', reset=True)
        return False
      self.last_carrier_frame = value.carrier_frame
    if now_ns - value.apply_ns > MAX_AGE_NS:
      if value.domain != self.adapter.command_domain:
        self.split('consumed_source_mismatch', reset=True)
        return False
      self.split('stale_applied_output')
      return False
    key = (value.control_ns, value.apply_ns)
    previous = self.recent_applied.get(key)
    if previous is not None:
      if replace(value, carrier_frame=previous.carrier_frame, carrier_observed_ns=previous.carrier_observed_ns) != previous:
        self.split('changed_duplicate_output', reset=True)
        return False
      return True
    self._last_live_ingest_ns = now_ns
    self.applied[key] = value
    self.recent_applied[key] = value
    while len(self.recent_applied) > MAX_PENDING:
      self.recent_applied.popitem(last=False)
    while len(self.applied) > MAX_PENDING:
      self.applied.popitem(last=False)
    self._join(value.control_ns)
    return True

  def _join(self, stamp):
    command = self.controls.get(stamp, self.recent_controls.get(stamp))
    if command is None:
      return
    for key in tuple(self.applied):
      if key[0] == stamp:
        output = self.applied.pop(key)
        self.controls.pop(stamp, None)
        self._join_emission(command, output)

  def _join_emission(self, command, output):
    if output.domain != self.adapter.command_domain:
      self.split('consumed_source_mismatch', reset=True)
      return
    current_sources = (
      consumed_sources_current(command, output)
      if command.domain == 'normalized_torque'
      else command.state_ns <= output.state_ns <= output.apply_ns and output.state_ns - command.state_ns <= JOIN_NS
    )
    if not current_sources:
      from openpilot.starpilot.flm.unchanged_output import redundant_late_output

      if redundant_late_output(self, command, output):
        # The original on-time pair remains untouched. This repeat creates no
        # ready row, sample, application time, physical witness or permission.
        return
      # This pair is unusable, but it does not change the identity/provenance
      # of independently sealed historical samples. Withdraw current admission
      # and discard the partial window; real context changes still hard-reset.
      self.split('consumed_source_mismatch', reset=command.domain != 'normalized_torque')
      return
    if command.domain == 'normalized_torque':
      projection = output.projection
      if (
        projection is None or projection.requested_torque != command.requested or projection.reported_torque != output.applied or not projection.steer_requested
      ):
        self.split('actual_encoder_projection_unavailable')
        return
    if command.native_required:
      if (
        not command.native_allowed
        or not identity(command.native_identity)
        or not 0 < command.native_observed_ns <= command.control_ns <= output.apply_ns <= command.native_expires_ns
        or command.native_expires_ns - command.native_observed_ns > 200_000_000
      ):
        self.split('original_native_admission_unavailable')
        return
      # Evidence-only reconstructed permission is bound to this exact consumed
      # CC and original validated native lease, never another newer grant.
      output = replace(output, native_allowed=True)
    if not output.native_allowed:
      self.split('applied_native_witness_unavailable')
      return
    if self.last_apply and (output.apply_ns <= self.last_apply or output.apply_ns - self.last_apply > MAX_AGE_NS):
      self.split('applied_gap_or_reorder')
    if self.active_since == 0:
      self.active_since = output.apply_ns
    self.last_apply = output.apply_ns
    if command.domain == 'normalized_torque':
      record = getattr(self.adapter, 'record_actual_emission', None)
      if record is None or not record(command, output):
        self.split('actual_emission_recorder_unavailable', reset=True)
        return
    self._retain_original_pair(command, output)
    if self.live_tracking is not None:
      try:
        profile = self.live_tracking.profile(command)
        self.live_tracking.observe_control(
          command, output, profile_sha256=profile, now_ns=self._last_live_ingest_ns, current_native=getattr(self, '_learning_current_native_allowed', False)
        )
      except (AttributeError, KeyError, TypeError, ValueError):
        self.live_tracking.interrupt('actual_tracking_native_pair_unqualified')

  def physical(self, value, now_ns):
    if (
      not isinstance(value, PhysicalEvidence)
      or type(value.mono_ns) is not int
      or type(value.received_ns) is not int
      or type(value.lag_ns) is not int
      or not 0 < value.mono_ns <= value.received_ns <= now_ns
      or now_ns - value.mono_ns > MAX_AGE_NS
      or not 0 <= value.lag_ns <= 500_000_000
      or not value.calibration
      or not value.model_generation
      or not value.lag_generation
      or not all(finite(v) for v in (value.speed_mps, value.yaw_rate, value.roll_rad, value.steering_angle_deg))
      or not self.adapter.physical_observable(value)
      or not value.valid
      or value.override
      or value.fault
      or not value.native_allowed
    ):
      if isinstance(value, PhysicalEvidence) and value.override:
        self.excluded_until = now_ns + EXCLUSION_NS
      self.split('ineligible_physical_response')
      return False
    physical_context = (value.calibration, value.model_generation, value.lag_generation, value.lag_ns)
    if self.physical_context is None:
      self.physical_context = physical_context
    elif physical_context != self.physical_context:
      self.split('physical_generation_changed', reset=True)
      self.physical_context = physical_context
      return False
    if value.mono_ns <= self.last_response:
      return False
    if self.last_response and value.mono_ns - self.last_response > MAX_AGE_NS:
      self.split('physical_response_gap')
      return False
    self.last_response = value.mono_ns
    target = value.mono_ns - value.lag_ns
    if not self.active_since or target - self.active_since < ACTIVE_NS or now_ns < self.excluded_until:
      return False
    eligible = [(control, output) for control, output in self.ready if output.apply_ns <= target]
    if not eligible:
      return False
    command, output = eligible[-1]
    if target - output.apply_ns > JOIN_NS:
      self.split('unmatched_physical_response')
      return False
    # Actual adapter independently converts physical data back into its applied
    # request domain, checking exact frozen model/stage identity. Never P/I error.
    applied_command = output.applied
    if output.domain == 'normalized_torque':
      from openpilot.starpilot.flm.torque_emission_replay import delivered

      try:
        applied_command = delivered(output)
      except (AttributeError, TypeError, ValueError, OverflowError):
        self.split('actual_delivered_coordinate_unavailable')
        return False
    physical = {
      'yaw_rate': value.yaw_rate,
      'roll_rad': value.roll_rad,
      'lateral_accel': value.speed_mps * value.yaw_rate - 9.81 * math.sin(value.roll_rad),
      'curvature': value.yaw_rate / value.speed_mps if value.speed_mps > 0 else None,
      'steering_angle_deg': value.steering_angle_deg,
      'speed_mps': value.speed_mps,
      'physical_context': physical_context,
      'applied_command': applied_command,
      'response_ns': value.mono_ns,
      'source_boot_ns': value.source_boot_ns,
      'clock_offset_ns': value.clock_offset_ns,
      'clock_uncertainty_ns': value.clock_uncertainty_ns,
    }
    raw = bool(getattr(self.adapter, 'raw_collection', False))
    cohort = self.adapter.cohort(command, physical)
    if not cohort or not isinstance(cohort, str) or len(cohort) > 96:
      complete = getattr(self.adapter, 'raw_maneuver_complete', None)
      if cohort is None and raw and complete is not None and complete(command, physical):
        self._seal_raw()
        self.raw_points.clear()
        self.cohort, self.sealed = None, False
        self.reason = 'waiting_for_excited_maneuver'
        return False
      self.split('response_cohort_unqualified')
      return False
    if getattr(self.adapter, 'raw_collection', False):
      if self.cohort is not None and self.cohort != cohort:
        self._seal_raw()
        self.raw_points.clear()
        self.sealed = False
      self.cohort = cohort
      if output.limited or command.limited and not exact_unclipped_pair(command, output):
        self.reason = 'waiting_for_unclipped_estimation'
        return False
      if not self.sealed:
        self.raw_points.append(RawPoint(command.control_ns, output.apply_ns, value.mono_ns, command, output, physical, physical_context))
        if len(self.raw_points) == MAX_RAW_POINTS:
          self._seal_raw()
          self.sealed = True
      # Raw healthy response supports only live metadata freshness. All model
      # fitting/inversion/stage normalization/heldout replay belongs to child.
      self.last_qualified_response = value.mono_ns
      return True
    if self.cohort is not None and self.cohort != cohort:
      self.split('maneuver_cohort_changed')
      return False
    self.cohort = cohort
    if not self.model_sealed:
      self.adapter.observe_model(
        command,
        physical,
        output.applied,
        physical_context,
        command_ns=output.apply_ns,
        response_ns=value.mono_ns,
        block_id=f'{self.session}:model:{self.model_block_sequence}',
      )
      self.model_points += 1
      self.model_sealed = self.model_points >= MAX_POINTS
    target_value = self.adapter.response_target(command, physical, output.applied, physical_context)
    if not finite(target_value):
      self.reason = 'independent_physical_model_collecting'
      return False
    self.last_qualified_response = value.mono_ns
    if self.sealed:
      return False
    self.points.append(Sample(value.mono_ns, command.control_ns, output.apply_ns, command.features, target_value - command.baseline_prediction, 1.0))
    if len(self.points) == MAX_POINTS:
      self.blocks.append(
        Block(command.identity, f'{self.session}:{self.block_sequence}', cohort, command.stage_revision, command.response_revision, tuple(self.points))
      )
      self.block_sequence += 1
      self.points.clear()
      self.sealed = True
    return True

  def _seal_raw(self):
    if 40 <= len(self.raw_points) <= MAX_RAW_POINTS and self.context is not None:
      control = self.raw_points[0].control
      self.blocks.append(
        RawBlock(
          control.identity,
          f'{self.session}:{self.block_sequence}',
          cast(str, self.cohort),
          control.stage_revision,
          control.response_revision,
          tuple(self.raw_points),
        )
      )
      self.block_sequence += 1
      self.raw_points.clear()

  def fit(self, now_ns):
    # This method belongs only to the separate learning process. Finite evidence
    # and<=8 coefficients bound work. An owner worker must enforce hard joblimits.
    if (
      self.context is None
      or self.block_sequence == self.fit_block_sequence
      or len(self.blocks) < 8
      or self.last_fit
      and now_ns - self.last_fit < 60_000_000_000
    ):
      return None
    self.sequence += 1
    self.fit_result = evaluate(
      self.context[0],
      tuple(self.adapter.parameters()),
      tuple(self.blocks),
      now_ns=now_ns,
      sequence=self.sequence,
      response_revision=self.context[4],
      required_cohorts=tuple(self.adapter.required_cohorts()),
      prior=self.prior_coefficients,
    )
    self.fit_block_sequence, self.last_fit = self.block_sequence, now_ns
    if self.fit_result.profile is not None and self.ready:
      control, output = self.ready[-1]
      self.fit_validation = self.adapter.revalidate_fit(self.fit_result.profile, control, output, self.physical_context, tuple(self.blocks))
    return self.fit_result

  def restore_prior(self, coefficients):
    parameters = tuple(self.adapter.parameters())
    if coefficients is None:
      self.prior_coefficients = None
      return
    if (
      type(coefficients) is not tuple
      or len(coefficients) != len(parameters)
      or not all(finite(v) and p.low <= v <= p.high and abs(v) <= p.max_step for p, v in zip(parameters, coefficients, strict=True))
    ):
      raise ValueError('historical coefficient bounds')
    self.prior_coefficients = coefficients

  def job_snapshot(self, now_ns):
    if (
      self.context is None
      or self.block_sequence == self.fit_block_sequence
      or len(self.blocks) < (16 if getattr(self.adapter, 'raw_collection', False) else 8)
      or self.last_fit
      and now_ns - self.last_fit < 60_000_000_000
    ):
      return None
    if not self.ready or self.physical_context is None:
      return None
    control, output = self.ready[-1]
    self.sequence += 1
    try:
      snapshot = self.adapter.fit_job_snapshot(
        tuple(self.blocks),
        now_ns=now_ns,
        sequence=self.sequence,
        prior=self.prior_coefficients,
        control=control,
        output=output,
        physical_context=self.physical_context,
      )
    except (ValueError, OverflowError, MemoryError):
      # Oversized whole chronology is rejected, never thinned. Throttle this
      # failed serialization attempt without consuming the sealed block cursor.
      self.reason = 'fit_snapshot_unavailable'
      self.last_fit = now_ns
      return None
    if snapshot is None:
      return None
    return snapshot

  def mark_job_submitted(self, now_ns):
    self.fit_block_sequence, self.last_fit = self.block_sequence, now_ns

  def install_fit(self, result, validation):
    if (
      not isinstance(result, Result)
      or result.profile is None
      or self.context is None
      or result.profile.identity != self.context[0]
      or result.profile.parameters != tuple(self.adapter.parameters())
      or not replay_accepts(validation, result.profile)
    ):
      return False
    self.fit_result, self.fit_validation = result, validation
    return True

  def revalidate_fit(self, now_ns):
    # Renew LIVE admission only with fresh exact source/response/model context.
    # Original train/heldout IDs and candidate coefficients stay immutable.
    fit = self.fit_result
    if (
      fit is None
      or fit.profile is None
      or self.context is None
      or not self.ready
      or not 0 < self.last_apply <= now_ns
      or now_ns - self.last_apply > MAX_AGE_NS
      or not 0 < self.last_qualified_response <= now_ns
      or now_ns - self.last_qualified_response > MAX_AGE_NS
      or now_ns < self.excluded_until
      or self.physical_context is None
      or fit.profile.identity != self.context[0]
    ):
      self.result = self.last_validation = None
      return None
    control, output = self.ready[-1]
    if (
      (
        control.identity,
        control.generation,
        control.domain,
        control.stage_revision,
        control.response_revision,
        control.source_epoch,
        control.controller_mode,
        control.policy,
        control.baseline_revision,
        control.controller_revision,
        control.turn_assist,
      )
      != self.context
      or not 0 < control.source_receipt_ns <= now_ns <= control.source_receipt_ns + 500_000_000
      or (control.native_required and (not control.native_allowed or not 0 < control.native_observed_ns <= now_ns <= control.native_expires_ns))
      or not control.enabled
      or control.manual
      or not control.active
      or not control.valid
      or control.override
      or control.fault
      or control.saturated
      or control.limited
      and not exact_unclipped_pair(control, output)
      or control.model_wobble
      or control.controller_chatter
      or not output.active
      or not output.valid
      or output.override
      or output.fault
      or output.limited
      or not output.native_allowed
    ):
      self.result = self.last_validation = None
      return None
    self.live_profile_sequence = max(self.live_profile_sequence, fit.profile.sequence) + 1
    # Only the registered event adapter accepts the collector's trusted clock;
    # legacy/custom adapter signatures and conservative calls stay unchanged.
    from openpilot.starpilot.flm.event_collection import EventCollectionAdapter

    admission_clock = {'now_ns': now_ns} if isinstance(self.adapter, EventCollectionAdapter) else {}
    admission = self.adapter.current_admission(fit.profile, control, output, self.physical_context, **admission_clock)
    admitted = readmit_profile(fit.profile, self.fit_validation, admission, now_ns=now_ns, sequence=self.live_profile_sequence)
    if admitted is None:
      self.result = self.last_validation = None
      return None
    profile, validation = admitted
    self.last_validation = validation
    self.result = Result('qualified', fit.reason, profile)
    return self.result


def _check_evidence_wire(event):
  wire_bytes = len(event.as_builder().to_bytes())
  if wire_bytes > MAX_SNAPSHOT_WIRE_BYTES:
    raise ValueError('wire_bound')
  if wire_bytes > MAX_WIRE_BYTES:
    kind = event.which()
    control_snapshot = kind == 'starpilotLateralState' and bool(len(event.starpilotLateralState.learningEvidence.replaySnapshot))
    ford_events = (
      kind == 'starpilotCarState'
      and event.starpilotCarState.fordLearningEvidence.version == 1
      and 0 < len(event.starpilotCarState.fordLearningEvidence.rows) <= 4
    )
    if not (control_snapshot or ford_events):
      raise ValueError('non_snapshot_wire_bound')


def drain(socket, decode, receive, collector, now_ns):
  """Nonblocking bounded existing transport; overflow rejects whole maneuver."""
  for _ in range(MAX_DRAIN):
    event = receive(socket)
    if event is None:
      return True
    try:
      _check_evidence_wire(event)
      decode(event, collector, now_ns)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      collector.split('malformed_evidence', reset=True)
      return False
  # The bounded lookahead is observation only; an overrun cannot silently pass.
  if receive(socket) is not None:
    collector.overrun()
    return False
  return True


class _DrainOverrun(Exception):
  pass


class PairedDrainState:
  """Run-owned FIFO lookahead, bounded to one original event per socket."""

  def __init__(self, control_socket, applied_socket):
    self.sockets = (control_socket, applied_socket)
    self.heads: tuple = (None, None)


def drain_pair(control_socket, control, applied_socket, applied, receive, collector, now_ns, state=None):
  """Merge two bounded FIFO tapes by their original publisher headers.

  Only the two queue heads compete; never sort a producer's own tape. A held
  command can be republished by Card, so embedded control/apply clocks are not
  transport ordering keys. Headers choose order only: the original callbacks
  still decode the untouched event and resample source/native/receiver clocks.
  Equal headers put control first, matching the previous causal preference.
  Malformed input remains a maneuver boundary. Stateless overflow cannot
  retain its lookahead and still rejects the maneuver. Run-owned state yields
  at the same batch bound without losing input; both original heads face the
  same wire and current receipt checks on the next bounded drain.
  """
  sockets, callbacks = (control_socket, applied_socket), (control, applied)
  retained = type(state) is PairedDrainState
  if state is None:
    state = PairedDrainState(*sockets)
  pending = list(state.heads)
  state.heads = (None, None)
  counts = [0, 0]

  def head(index):
    event = pending[index][0] if pending[index] is not None else receive(sockets[index])
    pending[index] = None
    if event is None:
      return None
    _check_evidence_wire(event)
    stamp = event.logMonoTime
    if type(stamp) is not int or stamp <= 0:
      raise ValueError('invalid_publisher_header')
    pending[index] = (event, stamp)
    # Keep the bounded lookahead and the other FIFO's head, not another batch.
    if counts[index] == MAX_DRAIN:
      raise _DrainOverrun
    return pending[index]

  try:
    if any(a is not b for a, b in zip(state.sockets, sockets, strict=True)):
      raise ValueError('changed_evidence_sockets')
    head(0)
    head(1)
    while pending[0] is not None or pending[1] is not None:
      index = 0 if pending[1] is None or (pending[0] is not None and pending[0][1] <= pending[1][1]) else 1
      event, _stamp = pending[index]
      callbacks[index](event, collector, now_ns)
      counts[index] += 1
      pending[index] = None
      head(index)
  except _DrainOverrun:
    state.heads = tuple(pending)
    if not retained:
      collector.overrun()
    return False
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    collector.split('malformed_evidence', reset=True)
    return False
  return True
