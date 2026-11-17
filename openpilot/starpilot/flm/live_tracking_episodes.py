"""Whole fullrate geometry joined to independent, unmodified physical endpoints.

This adapter grants no driving permission and never interpolates a pose. Its
inputs must come from the collector's original source pair, not UI telemetry.
"""

from collections import OrderedDict, deque
from typing import cast
from dataclasses import dataclass, replace
import hashlib
import math
import struct

from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, PROBE_GROUPS, probe_names, full_profile_sha256
from openpilot.starpilot.flm.fullrate_collector import ControlEvidence, AppliedEvidence
from openpilot.starpilot.flm.live_trial_contract import MAX_STEP
from openpilot.starpilot.flm.live_tracking import TrackingEpisode, HookSensitivity
from openpilot.starpilot.flm.live_tracking_response import validate_certificate, validate_response_witness, physical_calibration_generation
from openpilot.starpilot.flm.original_source_capture import OriginalSourcePose, validate_pose
from openpilot.starpilot.flm.torque_eligibility import consumed_sources_current, exact_unclipped_pair, asynchronous_hint

MAX_CONTROL_GAP_NS = 50_000_000
MAX_POSE_GAP_NS = 100_000_000
MAX_EVENT_NS = 10_000_000_000
HISTORY_NS = 12_000_000_000
PROBE_DEADLINE_NS = 30_000_000_000
MAX_COMPLETED = 16
MAX_PROBE_REQUESTS = MAX_COMPLETED * 240
INPUT_WINDOW_NS = 2_000_000_000
INPUT_RESTART_STEP_NS = 50_000_000


def _finite(value):
  return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class _Tick:
  control: ControlEvidence
  output: AppliedEvidence | None
  profile: str
  geometry: float
  goal: float


@dataclass(frozen=True)
class _Endpoint:
  control_ns: int
  apply_ns: int
  pose_ns: int
  speed: float
  goal: float
  error: float
  measured: float
  reference_ns: int = 0


def tracking_reference_force(control):
  """Actual selected controller target, not its incoming future FF request.

  The torque controller's request buffer already aligns its PID target with
  actuator delay. PID torque publishes its own actual kinematic target in the
  same stage field. Retain the existing original roll/force convention and
  independent actual-pose conversion; no reference time is shifted or inferred.
  """
  stage = dict(control.stage_values)
  inputs = dict(control.replay_inputs)
  setpoint, roll = stage['setpoint'], inputs['roll']
  if len(stage) != len(control.stage_values) or not _finite(setpoint) or not _finite(roll):
    raise ValueError('actual finite controller tracking target required')
  return float(setpoint) - ACCELERATION_DUE_TO_GRAVITY * math.sin(float(roll))


def tracking_reference_acceleration(control):
  setpoint = dict(control.stage_values)['setpoint']
  if not _finite(setpoint):
    raise ValueError('actual finite controller tracking target required')
  return float(setpoint)


def tracking_reversal_rate(errors, duration_s, model_rmse):
  # Count complete excursions above the SAME minimum error that discovery
  # can act on. Milligravity crossings of a sampled/quantized reference are
  # not evidence of vehicle weave. Original RMS and drift tests still see
  # every unfiltered endpoint; this changes only the oscillation counter.
  threshold = max(0.015, 3 * model_rmse)
  signs = [1 if error > threshold else -1 if error < -threshold else 0 for error in errors]
  nonzero = [sign for sign in signs if sign]
  return sum(left != right for left, right in zip(nonzero, nonzero[1:], strict=False)) / duration_s


class TrackingEpisodeBuilder:
  """Bounded operation-local joins, retaining true100Hz geometry and pose rate.

  observe_source_control is called for EVERY actual owner tick. Only actual
  emissions enter observe_control; no transmission is inferred. observe_pose
  receives the collector's exact lag-associated control/output pair and fresh
  response witness. Completed windows wait for a real physical watermark;
  time passing alone can never invent their missing independent endpoints.
  """

  def __init__(self, binding, response_certificate, *, controller_domain='torque'):
    binding.validate()
    certificate = validate_certificate(response_certificate)
    if certificate.binding != binding:
      raise ValueError('episode physical model binding')
    if type(controller_domain) is not str or controller_domain not in ('torque', 'torque_pid'):
      raise ValueError('actual controller domain required')
    from openpilot.starpilot.flm.pid_stages import UNSUPPORTED

    self.numeric_probe_names = tuple(name for name in NAMES if controller_domain != 'torque_pid' or name not in UNSUPPORTED)
    self.defer_unprobed = False
    self.numerical_observed_ns = 0
    from openpilot.starpilot.flm.live_tracking_response import TrackingSourceContext

    self.source_tracking = type(certificate) is TrackingSourceContext
    self.binding, self.certificate = binding, certificate
    self.replay = None
    self.ticks = OrderedDict()
    self.emissions = {}
    self.emission_count = 0
    self.endpoints = deque(maxlen=320)
    self.probes = OrderedDict()
    self.probe_received = {}
    self.profile_keys = OrderedDict()
    self.probe_requests = OrderedDict()
    self.completed = OrderedDict()
    self.closed_through_ns = 0
    self.pending = deque(maxlen=16)
    self.ready = deque(maxlen=16)
    self.last_pose_ns = self.watermark = 0
    self.reason = 'whole_tracking_geometry_required'
    self.last_input_status = 'ignored'
    self.plot = None
    self._clear_geometry()

  def _clear_geometry(self):
    self.start = self.last = self.neutral = self.prior_sign = self.first_sign = self.reversals = 0
    self.mode = self.profile = self.last_frame = None
    if not hasattr(self, 'input_window_origin'):
      self.input_window_origin = None
    self.input_window_boundary = self.input_window_wait_until = None
    self.input_window_min = self.input_window_max = self.input_window_sum = 0.0
    self.input_window_count = 0

  def interrupt(self, reason, *, preserve_completed=False):
    # Completed windows contain ORIGINAL endpoints and original probe deadlines.
    # Keeping those numbers supplies no current witness, offer or permission.
    completed = self.completed.copy() if preserve_completed else OrderedDict()
    retained_keys = set().union(*(row['requested'] for row in completed.values())) if completed else set()
    requests = OrderedDict((key, value) for key, value in self.probe_requests.items() if key in retained_keys)
    ready = tuple(self.ready) if preserve_completed else ()
    closed = self.closed_through_ns if preserve_completed else 0
    self.ticks.clear()
    self.emissions.clear()
    self.emission_count = 0
    self.endpoints.clear()
    self.probes.clear()
    self.probe_received.clear()
    self.probe_requests.clear()
    self.completed.clear()
    self.closed_through_ns = 0
    self.pending.clear()
    self.ready.clear()
    self.last_pose_ns = self.watermark = 0
    self.numerical_observed_ns = 0
    self.plot = None
    self._clear_geometry()
    self.completed.update(completed)
    self.probe_requests.update(requests)
    self.ready.extend(ready)
    self.closed_through_ns = closed
    self.reason = reason
    self.last_input_status = 'invalid'

  def _source_control(self, control, current_native):
    b = self.binding
    if (
      type(control) is not ControlEvidence
      or current_native is not True
      or (control.identity, control.source_epoch, control.baseline_revision, control.controller_revision, control.native_identity, control.native_required)
      != (b.identity, b.source_epoch, b.baseline_revision, b.controller_revision, b.native_identity, b.native_required)
      or control.domain != 'normalized_torque'
      or not control.enabled
      or not control.active
      or not control.valid
      or control.manual
      or control.override
      or control.fault
      or control.saturated
      or control.model_wobble
      or control.limited
      and not asynchronous_hint(control)
      or not 0 < control.state_ns <= control.control_ns <= control.state_ns + 30_000_000
      or not 0 < control.source_receipt_ns <= control.control_ns <= control.source_receipt_ns + 500_000_000
      or b.native_required
      and (
        not control.native_allowed
        or not 0 < control.native_observed_ns <= control.control_ns <= control.native_expires_ns <= control.native_observed_ns + 200_000_000
      )
    ):
      raise ValueError('episode original source/driver/controller/native exclusion')

  def _pair(self, control, output, current_native):
    self._source_control(control, current_native)
    b = self.binding
    from openpilot.starpilot.flm.live_tracking_response import verified_clipped_input

    observed_clip = verified_clipped_input(self.replay, control, output, b) if type(output) is AppliedEvidence and output.limited else False
    if (
      type(output) is not AppliedEvidence
      or control.limited
      and not exact_unclipped_pair(control, output)
      and not observed_clip
      or not output.active
      or not output.valid
      or output.override
      or output.fault
      or output.limited
      and not observed_clip
      or output.domain != control.domain
      or output.control_ns != control.control_ns
      or not consumed_sources_current(control, output)
      or b.native_required
      and (not output.native_allowed or output.apply_ns > control.native_expires_ns)
    ):
      raise ValueError('episode actual native pair/driver/limiter exclusion')

  def _actual_coordinates(self, control):
    # Legacy selected coordinates remain neutral TWO-space even while a full30
    # owner is active. Never reinterpret those two numbers as a full profile.
    legacy = control.applied_coordinates
    if type(legacy) is not tuple or len(legacy) != 2 or any(not _finite(value) or value != 0 for value in legacy):
      raise ValueError('old selected residual cannot impersonate full30 state')
    live = control.live_applied_coordinates
    if type(live) is not tuple:
      raise ValueError('exact numerical live consumed map')
    if live:
      if control.effect_trace is not None or len(live) != len(NAMES) or not all(_finite(value) for value in live):
        raise ValueError('one actual full30 owner required')
      return live
    if control.effect_trace is not None:
      from openpilot.starpilot.flm.effect_trace import FullEffectTrace

      trace = control.effect_trace
      if type(trace) is not FullEffectTrace:
        raise ValueError('actual old full30 trace required')
      trace.validate()
      envelope = trace.envelope
      b = self.binding
      if (
        (
          envelope.cp_sha256,
          envelope.controller_revision,
          envelope.source_epoch,
          envelope.reset_generation,
          envelope.selected_baseline_revision,
          envelope.persistent_source_epoch,
        )
        != (b.cp_sha256, b.controller_revision, b.source_epoch, b.reset_generation, b.baseline_revision, b.persistent_source_epoch)
        or trace.control_ns != control.effect_prepare_ns
        or not control.state_ns <= trace.control_ns <= control.control_ns
        or trace.qualified
        and (
          control.applied_profile_sequence <= 0
          or not 0
          < control.applied_profile_observed_ns
          <= control.control_ns
          <= control.applied_profile_expires_ns
          <= control.applied_profile_observed_ns + 500_000_000
        )
      ):
        raise ValueError('actual old full30 trace source or lease changed')
      return tuple(value for _, value in trace.applied)
    # No full owner and genuinely neutral legacy inputs mean neutral full30;
    # a pending offer/report is NEVER used to fill unseen applied coordinates.
    return (0.0,) * len(NAMES)

  def observe_source_control(self, control, *, profile_sha256, now_ns, current_native, preserve_completed=False):
    """EVERY genuine controller tick, independently of CAN transmit cadence."""
    self.last_input_status = 'ignored'
    try:
      self._source_control(control, current_native)
      if type(now_ns) is not int or not 0 <= now_ns - control.control_ns <= MAX_POSE_GAP_NS or control.domain != 'normalized_torque':
        raise ValueError('episode current actual complete profile')
      actual_coordinates = self._actual_coordinates(control)
      profile_key = struct.pack('!30d', *actual_coordinates)
      if profile_key not in self.profile_keys:
        self.profile_keys[profile_key] = full_profile_sha256(self.binding.baseline_revision, dict(zip(NAMES, actual_coordinates, strict=True)))
        if len(self.profile_keys) > 4:
          self.profile_keys.popitem(last=False)
      if self.profile_keys[profile_key] != profile_sha256:
        raise ValueError('episode current actual complete profile')
      stage = dict(control.stage_values)
      inputs = dict(control.replay_inputs)
      frame = inputs['control_frame']
      if not _finite(frame) or frame < 0 or int(frame) != frame:
        raise ValueError('actual original owner frame sequence')
      geometry = stage['setpoint']
      goal = tracking_reference_acceleration(control) if self.source_tracking else tracking_reference_force(control)
      if len(stage) != len(control.stage_values) or not _finite(geometry):
        raise ValueError('episode original stage geometry')
      stamp = control.control_ns
      if self.last and stamp <= self.last:
        old = self.ticks.get(stamp)
        if old is not None and old.control == control and old.profile == profile_sha256 and old.geometry == geometry and old.goal == goal:
          return False
        raise ValueError('episode control clock regression/duplicate mutation')
      if self.last and (stamp - self.last > MAX_CONTROL_GAP_NS or profile_sha256 != self.profile or frame != cast(float, self.last_frame) + 1):
        self.interrupt('episode control gap or actual profile changed', preserve_completed=preserve_completed)
      self.ticks[stamp] = _Tick(control, None, profile_sha256, geometry, goal)
      self.profile = profile_sha256
      self._geometry(stamp, geometry)
      self.last = stamp
      self.last_frame = frame
      if len(self.ticks) > 1400:
        raise ValueError('bounded original fullrate history')
      while self.ticks and stamp - next(iter(self.ticks)) > HISTORY_NS:
        old, _ = self.ticks.popitem(last=False)
        self.emission_count -= len(self.emissions.pop(old, {}))
        self.probes.pop(old, None)
        self.probe_received.pop(old, None)
        if not any(old in row['requested'] for row in self.completed.values()):
          self.probe_requests.pop(old, None)
      self._finish()
      self.last_input_status = 'accepted'
      return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      self.interrupt('episode original control unqualified', preserve_completed=preserve_completed)
      return False

  def observe_control(self, control, output, *, profile_sha256, now_ns, current_native, preserve_completed=False):
    """Attach only an actual emitted native/CarOutput pair, never fill a tick."""
    self.last_input_status = 'ignored'
    try:
      self._pair(control, output, current_native)
      if type(now_ns) is not int or not 0 <= now_ns - output.apply_ns <= MAX_POSE_GAP_NS:
        raise ValueError('episode actual emission receipt stale')
      # Backwards compatibility for genuine100Hz emitting platforms. Slower
      # platforms MUST feed observe_source_control separately for EVERY tick.
      if control.control_ns > self.last:
        if not self.observe_source_control(
          control, profile_sha256=profile_sha256, now_ns=now_ns, current_native=current_native, preserve_completed=preserve_completed
        ):
          return False
      tick = self.ticks.get(control.control_ns)
      if tick is None:
        return False  # An old emission cannot recreate discarded source time.
      if tick.control != control or tick.profile != profile_sha256:
        raise ValueError('actual emission differs from original source tick')
      # One consumed CC can be emitted on multiple genuine Card ticks. Bind
      # each original application time rather than replacing or inventing one.
      prior = self.emissions.get(control.control_ns, {}).get(output.apply_ns)
      if prior is not None:
        if prior == output:
          return False
        raise ValueError('changed duplicate actual emission')
      if self.emission_count >= 2800:
        raise ValueError('bounded original emission history')
      self.emissions.setdefault(control.control_ns, {})[output.apply_ns] = output
      self.emission_count += 1
      self.ticks[control.control_ns] = replace(tick, output=output)
      self.last_input_status = 'accepted'
      return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      self.interrupt('episode actual native join unqualified', preserve_completed=preserve_completed)
      return False

  def _complete(self, end):
    if self.start and 200_000_000 <= end - self.start <= MAX_EVENT_NS:
      self.pending.append((self.start, end, self.mode, self.first_sign, self.profile))

  def _input_window(self, stamp, goal):
    if self.input_window_origin is None:
      self.input_window_origin = stamp
      self.input_window_boundary = stamp + INPUT_WINDOW_NS
      self.start = stamp
    elif not self.start:
      if self.input_window_wait_until is None:
        elapsed = stamp - self.input_window_origin
        self.input_window_wait_until = self.input_window_origin + ((elapsed + INPUT_RESTART_STEP_NS - 1) // INPUT_RESTART_STEP_NS) * INPUT_RESTART_STEP_NS
      if stamp < self.input_window_wait_until:
        return  # Skip the partial window. Recovery slots remain on the original input clock; no loss/outcome selects a start.
      self.start = stamp
      self.input_window_boundary = self.input_window_wait_until + INPUT_WINDOW_NS
      self.input_window_wait_until = None
    if stamp >= self.input_window_boundary:
      if self.input_window_count:
        signed = self.input_window_sum / self.input_window_count
        self.first_sign = 1 if signed > 0.03 else -1 if signed < -0.03 else 0
        same_direction = self.input_window_min >= 0 or self.input_window_max <= 0
        self.mode = 'large' if self.first_sign and same_direction and max(abs(self.input_window_min), abs(self.input_window_max)) >= 0.25 else 'subtle'
        self._complete(self.last)  # Last ORIGINAL observed control, not a synthetic boundary tick.
      self.start = stamp
      self.input_window_boundary += ((stamp - self.input_window_boundary) // INPUT_WINDOW_NS + 1) * INPUT_WINDOW_NS
      self.input_window_min = self.input_window_max = self.input_window_sum = 0.0
      self.input_window_count = 0
    self.input_window_min = min(self.input_window_min, goal)
    self.input_window_max = max(self.input_window_max, goal)
    self.input_window_sum += goal
    self.input_window_count += 1
    self.mode = 'subtle'

  def _geometry(self, stamp, goal):
    if self.source_tracking:
      self._input_window(stamp, goal)
      return
    sign = 1 if goal > 0.03 else -1 if goal < -0.03 else 0
    if abs(goal) >= 0.25:
      self.neutral = 0
      if self.mode != 'large':
        self.start, self.mode, self.first_sign, self.reversals = stamp, 'large', sign, 0
      elif sign != self.first_sign:
        self._complete(stamp - 1)
        self.start, self.first_sign = stamp, sign
      self.prior_sign = sign
      return
    if self.mode == 'large':
      if not sign:
        self.neutral = self.neutral or stamp
        if stamp - self.neutral >= 500_000_000:
          self._complete(stamp)
          self.start = self.neutral = self.prior_sign = self.first_sign = self.reversals = 0
          self.mode = None
      elif sign != self.first_sign:
        self._complete(stamp - 1)
        self.start = self.neutral = self.prior_sign = self.first_sign = self.reversals = 0
        self.mode = None
      else:
        self.neutral = 0
      return
    if sign and self.prior_sign and sign != self.prior_sign:
      if self.mode is None:
        self.start, self.mode, self.first_sign, self.reversals = stamp, 'subtle', sign, 0
      else:
        self.reversals += 1
        if self.reversals == 2 and sign == self.first_sign:
          self._complete(stamp - 1)
          self.start, self.first_sign, self.reversals = stamp, sign, 0
    if self.mode == 'subtle' and stamp - self.start > MAX_EVENT_NS:
      self.start = self.first_sign = self.reversals = 0
      self.mode = None
    if sign:
      self.prior_sign = sign

  def request_probe(self, control_ns, *, group, names, queued_ns):
    """Register ONLY after owned immutable prepared-job enqueue; no permission.

    The actor queue is bounded to four genuine prepared endpoint jobs. This
    reserves numerical work only, never child execution or native permission.
    queued_ns is the real parent clock, independent of archived source stamps.
    First request fixes a 30s numerical deadline; subsequent requests cannot
    extend it. Register during the window, before it has been drained.
    """
    if (
      type(control_ns) is not int
      or type(queued_ns) is not int
      or queued_ns <= 0
      or type(group) is not str
      or group not in PROBE_GROUPS
      or type(names) is not tuple
      or not 1 <= len(names) <= len(PROBE_GROUPS[group])
      or any(type(name) is not str for name in names)
      or probe_names(names) != names
      or any(name not in PROBE_GROUPS[group] for name in names)
    ):
      return False
    tick = self.ticks.get(control_ns)
    if tick is None or tick.output is None:
      return False
    old = self.probe_requests.get(control_ns)
    if old is not None:
      return old[:2] == (group, names)  # Never refresh the original deadline.
    if len(self.probe_requests) >= MAX_PROBE_REQUESTS:
      return False
    for row in self.completed.values():
      if row['episode'].start_ns <= control_ns <= row['episode'].end_ns:
        if row['group'] != group or row['names'] != names:
          return False
        row['requested'].add(control_ns)
        self.probe_requests[control_ns] = (group, names, queued_ns)
        return True  # Existing first-request deadline remains immutable.
    # A ready/drained episode cannot be amended or counted a second time.
    if control_ns <= self.closed_through_ns or any(e.start_ns <= control_ns <= e.end_ns for e in self.ready):
      return False
    self.probe_requests[control_ns] = (group, names, queued_ns)
    return True

  def observe_probe(self, result, *, now_ns=None):
    from openpilot.starpilot.flm.live_tracking_probe import validate_probe_result

    try:
      validate_probe_result(result, self.binding)
      request = self.probe_requests.get(result.control_ns)
      if request is not None and (
        type(now_ns) is not int
        or not request[2] <= now_ns <= request[2] + PROBE_DEADLINE_NS
        or result.group != request[0]
        or tuple(row.name for row in result.rows) != request[1]
      ):
        return False
      for row in self.completed.values():
        point = next((point for point in row['points'] if point.control_ns == result.control_ns), None)
        if point is None:
          continue
        if (
          request is None
          or result.control_ns not in row['requested']
          or now_ns > row['deadline']
          or result.apply_ns != point.apply_ns
          or result.profile_sha256 != row['episode'].profile_sha256
          or result.model_revision != self.certificate.model_revision
        ):
          return False
        row['probes'][result.control_ns] = result
        row.pop('_wait_signature', None)
        self._release_completed(now_ns)
        return True
      tick = self.ticks.get(result.control_ns)
      if (
        tick is None
        or result.apply_ns not in self.emissions.get(result.control_ns, {})
        or result.profile_sha256 != tick.profile
        or result.model_revision != self.certificate.model_revision
      ):
        return False
      self.probes[result.control_ns] = result
      if request is not None:
        self.probe_received[result.control_ns] = now_ns
      return True
    except (AttributeError, TypeError, ValueError):
      return False

  @staticmethod
  def _gradient_rows(points, probes):
    rows = {name: [] for name in NAMES}
    seen = set()
    for point in points:
      key = point.control_ns, point.apply_ns
      probe = probes.get(point.control_ns)
      if key in seen or probe is None or getattr(probe, 'apply_ns', None) != point.apply_ns:
        continue
      seen.add(key)
      for row in probe.rows:
        if row.name in rows and getattr(row, 'status', None) in ('separated', 'quantized'):
          rows[row.name].append((point, row))
    return rows

  @staticmethod
  def _gradient_coverage(points, probes, requested, rows):
    if not points or set(probes) != set(requested):
      return {}
    first, last = min(p.control_ns for p in points), max(p.control_ns for p in points)
    slots = {p.control_ns: min(3, (p.control_ns - first) * 4 // (last - first + 1)) for p in points}
    required = set(slots.values())
    return {name: len(values) >= 4 and {slots[point.control_ns] for point, _ in values} == required for name, values in rows.items()}

  @staticmethod
  def _native_gradient_complete(points, probes, requested, names):
    rows = TrackingEpisodeBuilder._gradient_rows(points, probes)
    coverage = TrackingEpisodeBuilder._gradient_coverage(points, probes, requested, rows)
    return bool(names) and all(coverage.get(name, False) for name in names)

  def _sensitivities(self, points, probes, *, requested=None):
    rows = self._gradient_rows(points, probes)
    coverage = self._gradient_coverage(points, probes, set(probes) if requested is None else requested, rows)
    def directed_effect(row, side):
      coordinate = getattr(row, side + '_coordinate') - row.baseline_coordinate
      native = getattr(row, side + '_delivered') - row.baseline_delivered
      native_width = row.plus_delivered - row.minus_delivered
      if coordinate == 0 or native_width == 0:
        return 0.0
      # Recover the same measured/native conversion used by the secant,
      # then measure THIS direction against the encoded baseline. A visible
      # opposite probe cannot make a quantized selected direction visible.
      gain = abs(row.physical_derivative * (row.plus_coordinate - row.minus_coordinate) / native_width)
      return abs(native) * gain * MAX_STEP / abs(coordinate)

    return tuple(
      HookSensitivity(
        name,
        sum(point.error * row.physical_derivative for point, row in values),
        sum(row.physical_derivative**2 for _, row in values),
        sum(row.native_separated for _, row in values),
        len(values),
        math.sqrt(sum(row.max_step_physical_delta**2 for _, row in values) / len(values)),
        coverage.get(name, False),
        sum(row.minus_delivered != row.baseline_delivered for _, row in values),
        sum(row.plus_delivered != row.baseline_delivered for _, row in values),
        math.sqrt(sum(directed_effect(row, 'minus')**2 for _, row in values) / len(values)),
        math.sqrt(sum(directed_effect(row, 'plus')**2 for _, row in values) / len(values)),
      )
      for name, values in rows.items()
      if values
    )

  def _release_completed(self, now_ns):
    if type(now_ns) is not int or now_ns <= 0:
      return  # Missing real clock holds; never fabricate a numerical timeout.
    from openpilot.starpilot.flm.live_tracking_probe import validate_probe_result

    for key, row in tuple(self.completed.items()):
      wait_signature = len(row['probes']), len(row['requested'])
      if now_ns < row['deadline'] and (set(row['probes']) != row['requested'] or row.get('_wait_signature') == wait_signature):
        continue  # Only cache a decision to WAIT, never a proof to publish.
      valid = {}
      for control_ns, probe in row['probes'].items():
        try:
          validate_probe_result(probe, self.binding)
          point = next(point for point in row['points'] if point.control_ns == control_ns)
          if (
            control_ns in row['requested']
            and probe.apply_ns == point.apply_ns
            and probe.profile_sha256 == row['episode'].profile_sha256
            and probe.model_revision == self.certificate.model_revision
            and probe.group == row['group']
            and tuple(item.name for item in probe.rows) == row['names']
          ):
            valid[control_ns] = probe
        except (AttributeError, TypeError, ValueError, StopIteration):
          pass
      supported = all(sum(any(item.name == name for item in probe.rows) for probe in valid.values()) >= 4 for name in row['names'])
      sensitivities = self._sensitivities(row['points'], valid, requested=row['requested'])
      # One unavailable coordinate cannot invalidate another independently
      # complete native gradient. Every proposed coordinate checks its own flag.
      gradient_complete = any(feature.native_gradient_complete for feature in sensitivities)
      if not gradient_complete and now_ns < row['deadline']:
        row['_wait_signature'] = wait_signature
        continue
      if (supported and gradient_complete) or now_ns >= row['deadline']:
        episode = replace(row['episode'], sensitivity=sensitivities, native_gradient_complete=gradient_complete).validate()
        self.ready.append(episode)
        self.closed_through_ns = max(self.closed_through_ns, episode.end_ns)
        self.plot = (episode, row['points'])
        del self.completed[key]
        for point in row['points']:
          self.probe_requests.pop(point.control_ns, None)
        self.reason = 'qualified_whole_tracking_episode' if gradient_complete else 'whole_episode_probe_deadline_partial_support'

  def observe_pose(self, pose, control, output, *, witness, now_ns, current_native, preserve_completed=False):
    self.last_input_status = 'ignored'
    try:
      self._pair(control, output, current_native)
      raw_witness = validate_response_witness(witness, self.binding, now_ns)
      if raw_witness.certificate != self.certificate or (raw_witness.last_control_ns, raw_witness.last_apply_ns, raw_witness.last_pose_ns) != (
        control.control_ns,
        output.apply_ns,
        pose.mono_ns,
      ):
        raise ValueError('episode current physical model or exact residual pair changed')
      if type(pose) is not OriginalSourcePose:
        raise ValueError('exact original pose')
      validate_pose(pose, now_ns)
      b, lag = self.binding, self.certificate.lag_ns
      if (
        (pose.identity, pose.source_epoch, pose.baseline_revision, pose.controller_revision, pose.calibration, pose.native_identity, pose.native_required)
        != (b.identity, b.source_epoch, b.baseline_revision, b.controller_revision, physical_calibration_generation(b), b.native_identity, b.native_required)
        or not 0 <= pose.source_upper_ns - lag - output.apply_ns <= 30_000_000
        or output.apply_ns > pose.source_lower_ns - lag
        or not 0 <= now_ns - pose.mono_ns <= MAX_POSE_GAP_NS
      ):
        raise ValueError('episode lag/source interval changed')
      # A genuine lagged pair from BEFORE a source/profile boundary cannot
      # count toward the new lineage. It also must not repeatedly erase its
      # new 100Hz history while the physical lag catches up.
      if self.ticks and control.control_ns < next(iter(self.ticks)):
        return False
      tick = self.ticks.get(control.control_ns)
      if (
        tick is None
        or tick.control != control
        or self.emissions.get(control.control_ns, {}).get(output.apply_ns) != output
        or dict(control.stage_values).get('setpoint') != tick.geometry
        or (tracking_reference_acceleration(control) if self.source_tracking else tracking_reference_force(control)) != tick.goal
      ):
        raise ValueError('episode pose not in exact observed owner history')
      if pose.mono_ns <= self.last_pose_ns:
        return False
      if self.endpoints and control.control_ns <= self.endpoints[-1].control_ns:
        return False
      if self.last_pose_ns and pose.mono_ns - self.last_pose_ns > MAX_POSE_GAP_NS:
        raise ValueError('episode independent pose gap')
      # Plant identification associates a command with its later response.
      # Tracking accuracy instead compares the desired and actual state at
      # the SAME observed time, as the existing controller reports do.
      # Keep the causal command/native probe below; use a distinct original
      # reference control, never an interpolated or future desired sample.
      reference = next((self.ticks[stamp] for stamp in reversed(self.ticks) if stamp <= pose.source_lower_ns), None)
      if reference is None or pose.source_upper_ns - reference.control.control_ns > 30_000_000 or reference.profile != tick.profile:
        self.last_input_status = 'pending'
        return False
      self._source_control(reference.control, current_native)
      goal = tracking_reference_acceleration(reference.control) if self.source_tracking else tracking_reference_force(reference.control)
      if goal != reference.goal:
        raise ValueError('original tracking reference changed')
      measured = pose.speed_mps * pose.yaw_rate
      if not self.source_tracking:
        measured -= ACCELERATION_DUE_TO_GRAVITY * math.sin(pose.roll_rad)
      if not _finite(measured) or pose.speed_mps <= 0:
        raise ValueError('episode finite independent response')
      self.endpoints.append(
        _Endpoint(control.control_ns, output.apply_ns, pose.mono_ns, pose.speed_mps, goal, goal - measured, measured, reference.control.control_ns)
      )
      self.last_pose_ns = pose.mono_ns
      self.watermark = pose.source_lower_ns if self.source_tracking else pose.source_lower_ns - lag
      self.numerical_observed_ns = now_ns  # Already validated original pose/witness receipt clock.
      self._finish()
      self.last_input_status = 'accepted'
      return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      self.interrupt('episode original physical join unqualified', preserve_completed=preserve_completed)
      return False

  def _finish(self):
    while self.pending and self.watermark > self.pending[0][1] + 30_000_000:
      start, end, kind, direction, profile = self.pending.popleft()
      ticks = [tick for stamp, tick in self.ticks.items() if start <= stamp <= end]
      points = [point for point in self.endpoints if start <= (point.reference_ns if self.source_tracking else point.control_ns) <= end]
      if not ticks or not 4 <= len(points) <= 240:
        self.reason = 'whole_episode_independent_endpoints_required'
        continue
      stamps = [tick.control.control_ns for tick in ticks]
      gaps = [right - left for left, right in zip(stamps, stamps[1:], strict=False)]
      max_gap = max(gaps, default=0)
      if (
        stamps[0] != start
        or not 0 < max_gap <= MAX_CONTROL_GAP_NS
        or any(tick.profile != profile for tick in ticks)
        or max(point.speed for point in points) > 1.15 * min(point.speed for point in points)
      ):
        continue
      from openpilot.starpilot.flm.live_tracking_probe import validate_probe_result

      qualified_probes = {}
      for point in points:
        probe = self.probes.get(point.control_ns)
        if probe is not None:
          try:
            validate_probe_result(probe, self.binding)
            if (
              probe.control_ns == point.control_ns
              and probe.apply_ns == point.apply_ns
              and probe.profile_sha256 == profile
              and probe.model_revision == self.certificate.model_revision
            ):
              qualified_probes[point.control_ns] = probe
          except (AttributeError, TypeError, ValueError):
            pass  # Numerical support disappears; never invent a derivative.
      goal_rms = math.sqrt(sum(point.goal**2 for point in points) / len(points))
      bins = [[] for _ in range(8)]
      for tick in ticks:
        index = min(7, (tick.control.control_ns - start) * 8 // (end - start + 1))
        bins[index].append(tick.goal)
      scale = max(0.1, goal_rms) if self.source_tracking else goal_rms
      if scale <= 0 or any(not values for values in bins):
        self.reason = 'whole_episode_actual_goal_shape_required'
        continue
      goal_shape = tuple(sum(values) / len(values) / scale for values in bins)
      sensitivities = self._sensitivities(points, qualified_probes)
      errors = [point.error for point in points]
      from openpilot.starpilot.flm.live_tracking_response import TrackingSourceContext

      # Prediction residuals do not define the size of observed tracking weave.
      floor = 0.0 if type(self.certificate) is TrackingSourceContext else self.certificate.heldout_rmse
      reversal_hz = tracking_reversal_rate(errors, (end - start) / 1e9, floor)
      digest = hashlib.sha256(
        repr(
          (
            ('fixed-input-kinematic-windows-v1' if self.source_tracking else 'live-whole-tracking-v4-actionable-error-excursions'),
            self.binding,
            profile,
            start,
            end,
            kind,
            tuple((point.control_ns, point.apply_ns, point.pose_ns, point.reference_ns) for point in points),
          )
        ).encode()
      ).hexdigest()
      episode = TrackingEpisode(
        self.binding,
        profile,
        digest,
        start,
        end,
        kind,
        direction,
        min(point.speed for point in points),
        max(point.speed for point in points),
        goal_rms,
        math.sqrt(sum(error**2 for error in errors) / len(errors)),
        reversal_hz,
        len(points),
        max_gap,
        sensitivities,
        goal_shape,
        normalization_accel=scale if self.source_tracking else None,
      )
      episode.validate()
      requests = {point.control_ns: self.probe_requests[point.control_ns] for point in points if point.control_ns in self.probe_requests}
      defer = (
        (kind == 'subtle' or self.source_tracking)
        and getattr(self, 'defer_unprobed', False) is True
        and type(getattr(self, 'numerical_observed_ns', 0)) is int
        and self.numerical_observed_ns > 0
      )
      if requests or defer:
        if requests:
          group, names, _ = next(iter(requests.values()))
          deadline = min(value[2] for value in requests.values()) + PROBE_DEADLINE_NS
        else:
          # A short whole maneuver fits inside the EXISTING historical input
          # age. Keep its ORIGINAL endpoints for one bounded numerical batch,
          # rather than closing it before the4Hz coordinator can request them.
          group, names = 'all30', self.numeric_probe_names
          deadline = self.numerical_observed_ns + PROBE_DEADLINE_NS
        if any(value[:2] != (group, names) for value in requests.values()) or len(self.completed) >= MAX_COMPLETED:
          self.reason = 'whole_episode_probe_group_or_backlog_unavailable'
          continue
        self.completed[episode.episode_id] = {
          'episode': episode,
          'points': tuple(points),
          'group': group,
          'names': names,
          'requested': set(requests),
          'deadline': deadline,
          'probes': {
            key: value for key, value in qualified_probes.items() if key in requests and requests[key][2] <= self.probe_received.get(key, 0) <= deadline
          },
        }
      else:
        self.ready.append(episode)
        self.closed_through_ns = max(self.closed_through_ns, episode.end_ns)
        self.plot = (episode, tuple(points))
      self.reason = 'qualified_whole_tracking_episode'

  def release_measured_only(self):
    """Release already validated measurements without inventing derivatives.

    Only the coordinator's post-selection comparison path calls this. Every
    episode already passed its original control/native/CAN/pose/clock checks.
    Optional counterfactual probes are not actual actuation permission and
    cannot delay an uncensored baseline or a later measured keep/revert score.
    """
    if len(self.ready) + len(self.completed) > cast(int, self.ready.maxlen):
      self.interrupt('measured_episode_backlog')
      return False
    for key, row in tuple(self.completed.items()):
      episode = replace(row['episode'], sensitivity=(), native_gradient_complete=False).validate()
      self.ready.append(episode)
      self.closed_through_ns = max(self.closed_through_ns, episode.end_ns)
      self.plot = (episode, row['points'])
      for point in row['points']:
        self.probe_requests.pop(point.control_ns, None)
      del self.completed[key]
    self.probe_requests.clear()
    return True

  def drain(self, *, now_ns=None):
    self._release_completed(now_ns)
    # Optional native probes may finish after a later unprobed maneuver.
    # Preserve source order before the optimizer's strict chronological check;
    # never replace source time with numerical completion time. The original
    # probe deadline and bounded ready deque still bound this wait.
    if self.source_tracking and self.ready:
      # Fixed source windows are independently usable. An earlier incomplete
      # numerical job must not hold every later complete window for 30 seconds.
      # Drop it by source order alone, without inspecting tracking error or
      # native sensitivity; late numerical results cannot resurrect it.
      through = max(episode.end_ns for episode in self.ready)
      for key, row in tuple(self.completed.items()):
        if row['episode'].end_ns <= through:
          for point in row['points']:
            self.probe_requests.pop(point.control_ns, None)
          del self.completed[key]
    barrier = min((row['episode'].start_ns for row in self.completed.values()), default=None)
    ordered = sorted(self.ready, key=lambda episode: episode.start_ns)
    result = tuple(episode for episode in ordered if barrier is None or episode.end_ns < barrier)
    self.ready.clear()
    self.ready.extend(episode for episode in ordered if barrier is not None and episode.end_ns >= barrier)
    return result

  def tracking_plot(self):
    """Last completed episode's actual paired samples; never an interpolated line."""
    if self.plot is None:
      return None
    episode, points = self.plot
    indices = tuple(range(len(points))) if len(points) <= 64 else tuple(index * (len(points) - 1) // 63 for index in range(64))
    selected = tuple(points[index] for index in indices)
    return {
      'version': 'live-tracking-paired-plot-v1',
      'episodeId': episode.episode_id,
      'profileSha256': episode.profile_sha256,
      'lagNs': self.certificate.lag_ns,
      'units': 'm/s²',
      'sampled': len(selected) != len(points),
      'totalEndpoints': len(points),
      'points': [
        {
          'controlMonoTime': point.control_ns,
          'applyMonoTime': point.apply_ns,
          'poseMonoTime': point.pose_ns,
          'desiredForceAccel': point.goal,
          'measuredForceAccel': point.measured,
          'errorForceAccel': point.error,
        }
        for point in selected
      ],
    }

