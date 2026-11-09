"""Actual torque emission coordinates and chronological encoder branches.

Parent record() copies bounded typed metadata only. CAN decoding, encoder/filter
steps and finite differences belong to the child or existing bounded4Hz probe.
"""

from typing import cast

from collections import OrderedDict, deque
from dataclasses import dataclass, asdict
from types import SimpleNamespace
import math

from openpilot.starpilot.flm.torque_eligibility import bounded_context_output
from openpilot.starpilot.flm.clipping_quality import (
  RECORDED_DEBT, STATE_ALIGNED_RELIEF, EXACT_EMISSION_QUALITY, ZERO_REFERENCE_QUALITY,
  clipping_quality, debt_is_admitted, native_relief_is_admitted, zero_reference_relief_is_admitted,
  STRICT_IDENTIFICATION, NATIVE_SHADOW_IDENTIFICATION, identification_policy as validate_identification_policy,
)

from opendbc.car.steering_projection import (
  CODECS,
  TorqueProjection,
  float32,
  normalized_applied_command,
  project_candidate,
  projection_from_dict,
  projection_to_dict,
  _shaped_input,
)

MAX_EMISSIONS = 60_000
MAX_JOB_EMISSIONS = 12000


def projection(value):
  if isinstance(value, TorqueProjection):
    return value
  if isinstance(value, (tuple, list)) and len(value) == len(TorqueProjection._fields):
    value = dict(zip(TorqueProjection._fields, value, strict=True))
  result = projection_from_dict(value)
  if result is None:
    raise ValueError('bounded actual torque projection')
  return result


def delivered(output):
  """Delivered codec coordinate; reportedTorque remains separate evidence."""
  value = normalized_applied_command(projection(output.projection))
  if value is None:
    raise ValueError('delivered torque coordinate unavailable')
  return value


@dataclass(frozen=True)
class Emission:
  control_ns: int
  state_ns: int
  apply_ns: int
  witness: TorqueProjection
  identity: str
  source_epoch: str
  baseline_revision: str
  valid: bool
  active: bool
  override: bool
  fault: bool
  limited: bool
  native_allowed: bool
  native_required: bool
  native_observed_ns: int
  native_expires_ns: int
  native_identity: str


# Audited packet constructors use final raw torque plus unchanged source/CP,
# enable/status/counter fields. These source files are included by the existing
# implementation_revision identity. No unknown codec/native state gains support.
EXACT_EMISSION_CODECS = CODECS & frozenset((
  'gm.ASCMLKASteeringCmd', 'honda.STEERING_CONTROL', 'toyota.STEERING_LKA',
  'hyundai.LKAS11', 'hyundai.CANFD_TORQUE', 'hyundai.BLENDED_TORQUE', 'hyundai.BLENDED_LKAS11',
))


def exact_local_emission(witness, reference_raw, candidate_raw, recorded_debt, reference_debt, candidate_debt):
  """Local encoded equality only; NEVER equal prefix/plant/Civic hidden state.

  Caller retains the decoded original source/packet and projects BOTH requests
  using the full same candidate native prestate. Immutable original enable,
  frame/counter/status/address/bus/codec context stays fixed in this projection.
  Only the audited packet domains below may use this extra quality clause.
  """
  debts = recorded_debt, reference_debt, candidate_debt
  return bool(projection_to_dict(witness) is not None and witness.steer_requested is True and
    witness.codec_id in EXACT_EMISSION_CODECS and
    type(reference_raw) is int and type(candidate_raw) is int and
    candidate_raw == reference_raw == witness.final_raw and
    all(type(value) in (int, float) and math.isfinite(value) and value != 0 for value in debts) and
    (recorded_debt > 0) == (reference_debt > 0) == (candidate_debt > 0))


class EncoderBranch:
  """Own previous raw/normalized torque and modified-Civic state per branch."""

  def __init__(self, *, quality_contract=RECORDED_DEBT, identification_policy=STRICT_IDENTIFICATION, allow_unclipped_equal_output=False):
    self.allow_unclipped_equal_output = allow_unclipped_equal_output
    self.quality_contract = clipping_quality(quality_contract)
    self.identification_policy = validate_identification_policy(identification_policy)
    if (self.identification_policy == NATIVE_SHADOW_IDENTIFICATION and
        self.quality_contract not in (STATE_ALIGNED_RELIEF, EXACT_EMISSION_QUALITY, ZERO_REFERENCE_QUALITY)):
      raise ValueError('shadow identification requires combined quality proof')
    self.previous = None
    self.civic = None
    self.last_frame = None
    self.last_apply_ns = 0
    self.family = None

  def step(self, event, request, *, actual=False):
    witness = event.witness
    shadow = self.identification_policy == NATIVE_SHADOW_IDENTIFICATION
    if shadow and (actual or not witness.steer_requested):
      raise ValueError('shadow identification cannot grant original native evidence')
    if (
      self.last_frame is not None
      and witness.frame <= self.last_frame
      or self.last_apply_ns
      and event.apply_ns <= self.last_apply_ns
      or self.family is not None
      and self.family != (witness.family, witness.fingerprint, witness.flags, witness.codec_id)
    ):
      raise ValueError('encoder chronology/selection changed')
    self.family = (witness.family, witness.fingerprint, witness.flags, witness.codec_id)
    if self.previous is None:
      self.previous = witness.previous_torque
    if actual and self.previous != witness.previous_torque:
      raise ValueError('missing actual previous encoder state')
    shape = None
    if witness.shape:
      if self.civic is None:
        self.civic = tuple(witness.shape[:4])
      if actual and self.civic != tuple(witness.shape[:4]):
        raise ValueError('missing actual Civic filter transition')
      shape = self.civic + tuple(witness.shape[4:])
    elif self.civic is not None:
      raise ValueError('Civic state disappeared')
    raw = project_candidate(witness, request, previous=self.previous, shape=shape)
    honda_limited_source = None
    if witness.family == 'honda' and witness.limiter_kind == 'normalized_rate':
      from opendbc.car import DT_CTRL, rate_limit
      import numpy as np

      source = _shaped_input(witness, float32(request), shape)
      params = SimpleNamespace(**dict(witness.limits))
      honda_limited_source = rate_limit(source, self.previous,
        -params.STEER_DELTA_DOWN * DT_CTRL, params.STEER_DELTA_UP * DT_CTRL)
      if raw is None and (event.limited or shadow) and witness.mapping_kind == 'honda_lookup' and (witness.unlimited_raw is None or shadow):
        from opendbc.car.steering_projection import project_request
        if shadow and project_request(witness, request, shape=shape) is None:
          raise ValueError('shadow native request support unavailable')
        # Honda clips in normalized units BEFORE its integer lookup encoder.
        # Keep clipped original context, never an estimation endpoint, and
        # compare direct demand under the explicit quality contract; the
        # recorded clipping-direction veto remains on EVERY candidate tick.
        original_limited = rate_limit(witness.codec_input_torque, witness.previous_torque,
          -params.STEER_DELTA_DOWN * DT_CTRL, params.STEER_DELTA_UP * DT_CTRL)
        original_demand = witness.codec_input_torque - original_limited
        reference_demand = original_demand
        reference_source, reference_limited = witness.codec_input_torque, original_limited
        if self.quality_contract != RECORDED_DEBT and not actual and not shadow:
          # Use BOTH requests with the SAME complete candidate pre-tick Civic
          # state and normalized limiter previous value, never recorded shaping.
          reference_source = _shaped_input(witness, float32(witness.requested_torque), shape)
          if not math.isfinite(reference_source):
            raise ValueError('paired native request support unavailable')
          reference_limited = rate_limit(reference_source, self.previous,
            -params.STEER_DELTA_DOWN * DT_CTRL, params.STEER_DELTA_UP * DT_CTRL)
          reference_demand = reference_source - reference_limited
        candidate_demand = source - honda_limited_source
        raw = int(np.interp(-honda_limited_source * witness.effective_max, witness.lookup_x, witness.lookup_y))
        admitted = debt_is_admitted(original_demand, reference_demand, candidate_demand, self.quality_contract)
        if not admitted and self.quality_contract in (STATE_ALIGNED_RELIEF, EXACT_EMISSION_QUALITY, ZERO_REFERENCE_QUALITY) and not shadow and event.limited:
          reference_raw = int(np.interp(-reference_limited * witness.effective_max, witness.lookup_x, witness.lookup_y))
          recorded_output = normalized_applied_command(witness)
          reference_output = normalized_applied_command(witness._replace(final_raw=reference_raw))
          candidate_output = normalized_applied_command(witness._replace(final_raw=raw))
          # Require actual unshaped source relief too; the full candidate Civic
          # filter must independently preserve the native-source relief order.
          requested = float32(request)
          admitted = (requested * witness.requested_torque > 0 and abs(requested) < abs(witness.requested_torque) and
            native_relief_is_admitted(witness.codec_input_torque, reference_source, source,
              recorded_output, reference_output, candidate_output, original_demand, reference_demand, candidate_demand))
        if not admitted and self.quality_contract == ZERO_REFERENCE_QUALITY and not actual and not shadow and event.limited:
          admitted = (witness.codec_id in EXACT_EMISSION_CODECS and witness.steer_requested is True and
            type(raw) is int and type(reference_raw) is int and raw == reference_raw and
            requested * witness.requested_torque > 0 and abs(requested) < abs(witness.requested_torque) and
            zero_reference_relief_is_admitted(witness.codec_input_torque, reference_source, source,
              recorded_output, reference_output, candidate_output, original_demand, reference_demand, candidate_demand))
        if not admitted and self.quality_contract in (EXACT_EMISSION_QUALITY, ZERO_REFERENCE_QUALITY) and not actual and not shadow and event.limited:
          admitted = exact_local_emission(witness, reference_raw, raw, original_demand, reference_demand, candidate_demand)
        if not shadow and (not original_demand or not actual and not admitted):
          raise ValueError('candidate encoder clipping regression')
    if (raw is None and self.allow_unclipped_equal_output and not actual and not shadow and not event.limited and
        witness.mapping_kind == 'round' and not witness.shape and witness.steer_requested and
        witness.unlimited_raw == witness.final_raw and witness.limiter_kind in ('driver', 'measured')):
      from opendbc.car.steering_projection import project_request
      from opendbc.car.lateral import apply_driver_steer_torque_limits, apply_meas_steer_torque_limits

      requested_raw = project_request(witness, request)
      reference_raw = project_request(witness, witness.requested_torque)
      # Replaying a different history can engage a rate limiter that was idle
      # on the original drive. BOTH requests must produce the EXACT same native
      # output from this SAME candidate prestate, with no larger request.
      if (reference_raw == witness.unlimited_raw and requested_raw is not None and reference_raw is not None and
          (requested_raw == reference_raw or requested_raw * reference_raw > 0 and abs(requested_raw) < abs(reference_raw))):
        params = SimpleNamespace(STEER_MAX=witness.effective_max, **dict(witness.limits))
        if witness.limiter_kind == 'driver':
          candidate_output = apply_driver_steer_torque_limits(requested_raw, self.previous, witness.measured_torque, params, int(witness.effective_max))
          reference_output = apply_driver_steer_torque_limits(reference_raw, self.previous, witness.measured_torque, params, int(witness.effective_max))
        else:
          candidate_output = apply_meas_steer_torque_limits(requested_raw, self.previous, witness.measured_torque, params)
          reference_output = apply_meas_steer_torque_limits(reference_raw, self.previous, witness.measured_torque, params)
        if candidate_output == reference_output:
          raw = candidate_output
    if raw is None and (event.limited or shadow) and witness.limiter_kind in ('driver', 'measured'):
      from opendbc.car.steering_projection import project_request
      from opendbc.car.lateral import apply_driver_steer_torque_limits, apply_meas_steer_torque_limits

      requested_raw = project_request(witness, request, shape=shape)
      if requested_raw is not None:
        params = SimpleNamespace(STEER_MAX=witness.effective_max, **dict(witness.limits))
        if witness.limiter_kind == 'driver':
          raw = apply_driver_steer_torque_limits(requested_raw, self.previous, witness.measured_torque, params, int(witness.effective_max))
        else:
          raw = apply_meas_steer_torque_limits(requested_raw, self.previous, witness.measured_torque, params)
        original_demand = witness.unlimited_raw - witness.final_raw
        reference_demand = original_demand
        if self.quality_contract != RECORDED_DEBT and not actual and not shadow:
          reference_request = project_request(witness, witness.requested_torque, shape=shape)
          if reference_request is None:
            raise ValueError('paired native request support unavailable')
          if witness.limiter_kind == 'driver':
            reference_raw = apply_driver_steer_torque_limits(reference_request, self.previous,
              witness.measured_torque, params, int(witness.effective_max))
          else:
            reference_raw = apply_meas_steer_torque_limits(reference_request, self.previous, witness.measured_torque, params)
          reference_demand = reference_request - reference_raw
        candidate_demand = requested_raw - raw
        admitted = debt_is_admitted(original_demand, reference_demand, candidate_demand, self.quality_contract)
        if not admitted and self.quality_contract in (STATE_ALIGNED_RELIEF, EXACT_EMISSION_QUALITY, ZERO_REFERENCE_QUALITY) and not shadow and event.limited:
          admitted = native_relief_is_admitted(witness.unlimited_raw, reference_request, requested_raw,
            witness.final_raw, reference_raw, raw, original_demand, reference_demand, candidate_demand)
        if not admitted and self.quality_contract == ZERO_REFERENCE_QUALITY and not actual and not shadow and event.limited:
          admitted = (witness.codec_id in EXACT_EMISSION_CODECS and witness.steer_requested is True and
            type(raw) is int and type(reference_raw) is int and raw == reference_raw and
            zero_reference_relief_is_admitted(witness.unlimited_raw, reference_request, requested_raw,
              witness.final_raw, reference_raw, raw, original_demand, reference_demand, candidate_demand))
        if not admitted and self.quality_contract in (EXACT_EMISSION_QUALITY, ZERO_REFERENCE_QUALITY) and not actual and not shadow and event.limited:
          admitted = exact_local_emission(witness, reference_raw, raw, original_demand, reference_demand, candidate_demand)
        if not actual and not shadow and not admitted:
          raise ValueError('candidate encoder clipping regression')
    if raw is None:
      raise ValueError('candidate changed an actual limiter/support')
    shaped = _shaped_input(witness, float32(request), shape) if witness.family == 'honda' else None
    if actual and (
      float32(request) != witness.requested_torque
      or raw != witness.final_raw
      or witness.unlimited_raw != witness.final_raw and not event.limited
      or witness.family == 'honda'
      and shaped != witness.codec_input_torque
    ):
      raise ValueError('actual emitted encoder mismatch')
    if witness.shape:
      from opendbc.car.honda.modified_civic_steering import get_civic_bosch_modified_steering_pressed

      lpf, previous, filtered, was_pressed, speed, pressed, driver = cast(tuple[float, float, float, bool, float, bool, float], shape)
      filtered, pressed = get_civic_bosch_modified_steering_pressed(pressed, driver, float32(request), filtered, was_pressed)
      if pressed:
        raise ValueError('candidate driver override')
      self.civic = (shaped, float32(request), filtered, pressed)
    self.previous = honda_limited_source if witness.family == 'honda' else raw
    self.last_frame, self.last_apply_ns = witness.frame, event.apply_ns
    value = normalized_applied_command(witness._replace(final_raw=raw))
    if value is None or not math.isfinite(value):
      raise ValueError('candidate encoder coordinate unavailable')
    return value


  def candidate_reported_torque(self, event):
    """Exact registered CC actuator report after this SAME native branch step.

    Honda reports the shaped/rate-limited normalized INPUT; inverse CAN lookup
    is a distinct physical coordinate. Other registered round codecs report
    final raw torque divided by the same effective maximum, all Float32 on CO.
    """
    witness=event.witness
    if (self.last_frame!=witness.frame or self.last_apply_ns!=event.apply_ns or
        self.family!=(witness.family,witness.fingerprint,witness.flags,witness.codec_id) or
        witness.codec_id not in CODECS or self.previous is None):
      raise ValueError('candidate reported CO missing same native step')
    if witness.family=='honda' and witness.mapping_kind=='honda_lookup':
      value=float32(self.previous)
    elif witness.family!='honda' and witness.mapping_kind=='round' and not witness.shape:
      value=float32(float(self.previous)/witness.effective_max)
    else:
      raise ValueError('unproven candidate reported CO codec')
    if not math.isfinite(value):
      raise ValueError('nonfinite candidate reported CO')
    return value


class EmissionHistory:
  def __init__(self, cp, *, identity='', baseline_revision=''):
    self.cp = cp
    self.identity, self.baseline_revision = identity, baseline_revision
    self.events = OrderedDict()
    self.by_control = {}
    self.decoder = None
    self.decode_queue = deque()
    self.decode_errors = OrderedDict()
    self.decode_history_truncated = False
    self.carriers = OrderedDict()

  def record_carrier(self, output):
    # Envelope continuity only. This is neither a command/application timestamp
    # nor a native/physical grant; a hold keeps its exact previous witness.
    try:
      frame, observed = int(output.carrier_frame), int(output.carrier_observed_ns)
      if frame <= 0 or not 0 < output.apply_ns <= observed:
        return False
      value = (observed, output.apply_ns)
      if frame in self.carriers:
        return self.carriers[frame] == value
      if self.carriers and frame <= next(reversed(self.carriers)):
        return False
      self.carriers[frame] = value
      while len(self.carriers) > MAX_EMISSIONS:
        self.carriers.popitem(last=False)
      return True
    except (AttributeError, TypeError, ValueError, OverflowError):
      return False

  def record(self, control, output):
    """Primitive parent append only; this never packs/decodes/steps a controller."""
    try:
      witness = projection(output.projection)
      flags = (output.valid, output.active, output.override, output.fault, output.limited, output.native_allowed, control.native_required)
      if (
        any(type(v) is not bool for v in flags)
        or output.domain != 'normalized_torque'
        or control.identity == ''
        or self.identity
        and self.identity != control.identity
        or self.baseline_revision
        and self.baseline_revision != control.baseline_revision
        or output.control_ns != control.control_ns
        or witness.requested_torque != control.requested
        or witness.reported_torque != output.applied
        or witness.family != str(self.cp.brand)
        or witness.fingerprint != str(self.cp.carFingerprint)
        or witness.flags != int(self.cp.flags)
        or not 0 < output.control_ns <= output.apply_ns
        or not 0 < output.state_ns <= output.apply_ns
        or output.override
        or output.fault
        or output.limited and not bounded_context_output(output)
        or not output.active
        or not output.valid
        or not output.native_allowed
        or not witness.steer_requested
      ):
        return False
      required = bool(control.native_required)
      if required and (
        not control.native_allowed
        or not 0 < control.native_observed_ns <= control.control_ns <= output.apply_ns <= control.native_expires_ns
        or control.native_expires_ns > control.native_observed_ns + 200_000_000
        or len(control.native_identity) != 64
      ):
        return False
      event = Emission(
        output.control_ns,
        output.state_ns,
        output.apply_ns,
        witness,
        control.identity,
        control.source_epoch,
        control.baseline_revision,
        output.valid,
        output.active,
        output.override,
        output.fault,
        output.limited,
        output.native_allowed,
        required,
        control.native_observed_ns,
        control.native_expires_ns,
        control.native_identity,
      )
      old = self.events.get(event.apply_ns)
      if old is not None:
        return old == event
      if self.events and event.apply_ns <= next(reversed(self.events)):
        return False
      if getattr(output, 'carrier_frame', witness.frame + 1) != witness.frame + 1:
        return False
      self.events[event.apply_ns] = event
      self.decode_queue.append(event.apply_ns)
      self.by_control[event.control_ns] = event
      while len(self.events) > MAX_EMISSIONS:
        _, removed = self.events.popitem(last=False)
        self.decode_errors.pop(removed.apply_ns, None)
        if self.decode_queue and self.decode_queue[0] <= removed.apply_ns:
          self.decode_queue.popleft()
          self.decode_history_truncated = True
        if self.decoder is not None:
          self.decoder.verified.pop(removed.apply_ns, None)
        if self.by_control.get(removed.control_ns) == removed:
          self.by_control.pop(removed.control_ns, None)
      return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return False

  def event(self, control_ns):
    return self.by_control.get(control_ns)

  def verify(self, control, output):
    event = self.events.get(output.apply_ns)
    if (
      event is None
      or event.control_ns != control.control_ns
      or event.identity != control.identity
      or event.source_epoch != control.source_epoch
      or event.baseline_revision != control.baseline_revision
      or event.witness != projection(output.projection)
    ):
      raise ValueError('actual emitted source unavailable')
    self.decode(event)
    value = EncoderBranch().step(event, control.requested, actual=True)
    if value != delivered(output):
      raise ValueError('actual delivered coordinate mismatch')
    return event

  def decode(self, event):
    # CANParser has stateful counter checks. A query for an occasional clipped
    # input must consume the intervening original emissions too, rather than
    # presenting that sparse query stream as the actual CAN sequence.
    if self.events.get(event.apply_ns) != event or self.decode_history_truncated:
      raise ValueError('actual CAN decoder original history unavailable')
    if self.decoder is None:
      self.decoder = EmissionDecoder(self.cp)
    for _ in range(64):
      if not self.decode_queue or self.decode_queue[0] > event.apply_ns:
        break
      stamp = self.decode_queue.popleft()
      original = self.events[stamp]
      try:
        self.decoder.check(original)
      except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
        self.decode_errors[stamp] = str(error)
    if event.apply_ns in self.decode_errors:
      raise ValueError(self.decode_errors[event.apply_ns])
    if event.apply_ns not in self.decoder.verified:
      raise ValueError('actual CAN decoder original sequence pending')

  def one_apply(self, apply_ns, request):
    event = self.events.get(apply_ns)
    if event is None:
      raise ValueError('no actual emission for control')
    self.decode(event)
    return EncoderBranch().step(event, request)

  def trace_apply(self, apply_ns, baseline, plus, minus, epsilon):
    if not 0 < epsilon <= 0.03:
      raise ValueError('encoder secant envelope')
    base = self.one_apply(apply_ns, baseline)
    features = tuple((self.one_apply(apply_ns, a) - self.one_apply(apply_ns, b)) / (2 * epsilon) for a, b in zip(plus, minus, strict=True))
    return base, features

  def rollout(self, requests, start_ns, end_ns, windows=(), *, quality_contract=RECORDED_DEBT,
              identification_policy=STRICT_IDENTIFICATION, feedback_reported=None, allow_unclipped_equal_output=False):
    """Advance on EVERY actual emission, including unscored warmup emissions."""
    branch = EncoderBranch(quality_contract=quality_contract, identification_policy=identification_policy,
                           allow_unclipped_equal_output=allow_unclipped_equal_output)
    if feedback_reported is not None and (type(feedback_reported) is not dict or feedback_reported):
      raise ValueError('empty private candidate reported CO sink required')
    values = {}
    previous = None
    last_window = None
    for event in self.events.values():
      if event.control_ns < start_ns or event.control_ns > end_ns:
        continue
      if event.control_ns not in requests:
        raise ValueError('missing consumed controller source')
      window = next((i for i, (a, b) in enumerate(windows) if a <= event.control_ns <= b), 0)
      if window != last_window:
        branch = EncoderBranch(quality_contract=quality_contract, identification_policy=identification_policy,
                           allow_unclipped_equal_output=allow_unclipped_equal_output)
        previous = None
      self.decode(event)
      value = branch.step(event, requests[event.control_ns])
      if feedback_reported is not None:
        feedback_reported[event.apply_ns]=branch.candidate_reported_torque(event)
      values[event.apply_ns] = value
      last_window = window
      if previous is not None and event.apply_ns - previous > 100_000_000:
        raise ValueError('actual emitted command gap')
      previous = event.apply_ns
    return values

  def snapshot(self, windows):
    rows = []
    for event in self.events.values():
      if any(a <= event.control_ns <= b for a, b in windows):
        values = asdict(event)
        values['witness'] = projection_to_dict(event.witness)
        rows.append(values)
    if len(rows) > MAX_JOB_EMISSIONS:
      raise ValueError('actual emission worker bound')
    carriers = []
    for a, b in windows:
      emitted = [event for event in self.events.values() if a <= event.control_ns <= b]
      if not emitted:
        continue
      first, last = emitted[0].witness.frame + 1, emitted[-1].witness.frame + 1
      carriers.extend((frame, *value) for frame, value in self.carriers.items() if first <= frame <= last)
    if len(carriers) > MAX_JOB_EMISSIONS:
      raise ValueError('actual carrier worker bound')
    return {'events': tuple(rows), 'carriers': tuple(dict.fromkeys(carriers))}

  def load(self, data, windows):
    rows = data['events']
    if len(rows) > MAX_JOB_EMISSIONS:
      raise ValueError('actual emission payload bound')
    for row in rows:
      values = dict(row)
      witness = projection(values['witness'])
      values['witness'] = witness
      event = Emission(**values)
      source = SimpleNamespace(
        identity=event.identity,
        source_epoch=event.source_epoch,
        baseline_revision=event.baseline_revision,
        requested=witness.requested_torque,
        control_ns=event.control_ns,
        native_required=event.native_required,
        native_allowed=event.native_allowed,
        native_observed_ns=event.native_observed_ns,
        native_expires_ns=event.native_expires_ns,
        native_identity=event.native_identity,
      )
      output = SimpleNamespace(
        domain='normalized_torque',
        control_ns=event.control_ns,
        state_ns=event.state_ns,
        apply_ns=event.apply_ns,
        applied=witness.reported_torque,
        projection=witness,
        override=event.override,
        fault=event.fault,
        limited=event.limited,
        active=event.active,
        valid=event.valid,
        native_allowed=event.native_allowed,
      )
      if not self.record(source, output):
        raise ValueError('actual emission snapshot order/source')
    # Saved flags are exclusions, never a substitute for byte/state replay.
    carrier_rows = data['carriers']
    if len(carrier_rows) > MAX_JOB_EMISSIONS:
      raise ValueError('actual carrier payload bound')
    carriers = {}
    for frame, observed, applied in carrier_rows:
      if any(type(v) is not int or v <= 0 for v in (frame, observed, applied)) or applied > observed or frame in carriers:
        raise ValueError('actual carrier metadata')
      carriers[frame] = (observed, applied)
      self.carriers[frame] = (observed, applied)
    for a, b in windows:
      events = [e for e in self.events.values() if a <= e.control_ns <= b]
      if not events:
        continue
      first, last = events[0].witness.frame + 1, events[-1].witness.frame + 1
      if any(frame not in carriers for frame in range(first, last + 1)):
        raise ValueError('missing actual Card carrier tick')
      previous_observed = 0
      by_frame = {event.witness.frame + 1: event for event in events}
      if len(by_frame) != len(events):
        raise ValueError('duplicate actual emission frame')
      latest = None
      for frame in range(first, last + 1):
        observed, applied = carriers[frame]
        if frame in by_frame:
          latest = by_frame[frame]
        if latest is None or applied != latest.apply_ns:
          raise ValueError('carrier points to missing/newer actual emission')
        if observed <= previous_observed:
          raise ValueError('actual carrier clock order')
        previous_observed = observed
      for event in events:
        if carriers[event.witness.frame + 1][1] != event.apply_ns:
          raise ValueError('actual emission/carrier binding')
    actual = EncoderBranch()
    last_window = None
    for event in self.events.values():
      window = next((i for i, (a, b) in enumerate(windows) if a <= event.control_ns <= b), None)
      if window is None:
        raise ValueError('actual emission outside selected fullrate windows')
      if window != last_window:
        actual = EncoderBranch()
      self.decode(event)
      actual.step(event, event.witness.requested_torque, actual=True)
      last_window = window


class EmissionDecoder:
  def __init__(self, cp):
    from opendbc.car import Bus
    from opendbc.car.car_helpers import interfaces

    car_state = interfaces[str(cp.carFingerprint)].CarState(cp)
    parsers = car_state.get_can_parsers(cp)
    self.dbc = parsers[Bus.pt].dbc_name
    self.parsers = {}
    self.verified = OrderedDict()

  def check(self, event):
    from opendbc.can import CANParser

    witness = event.witness
    if event.apply_ns in self.verified:
      return
    key = (witness.can_address, witness.can_bus)
    if key not in self.parsers:
      self.parsers[key] = CANParser(self.dbc, [(witness.can_address, 0)], witness.can_bus)
    parser = self.parsers[key]
    updated = parser.update([event.apply_ns, [(witness.can_address, witness.can_data, witness.can_bus)]])
    if witness.can_address not in updated:
      raise ValueError('actual CAN decoder source')
    values = parser.vl[witness.can_address]
    family = witness.family
    if family == 'hyundai':
      if witness.codec_id in ('hyundai.LKAS11', 'hyundai.BLENDED_LKAS11'):
        raw, active = values['CR_Lkas_StrToqReq'], bool(values['CF_Lkas_ActToi'])
      elif witness.codec_id == 'hyundai.BLENDED_TORQUE':
        raw, active = values['TORQUE_REQUEST'], bool(values['STEER_REQ'])
      else:
        raw, active = values['StrTqReqVal'], bool(values['ActToiSta'])
    elif family == 'honda':
      raw, active = values['STEER_TORQUE'], bool(values['STEER_TORQUE_REQUEST'])
    elif family == 'toyota':
      raw, active = values['STEER_TORQUE_CMD'], bool(values['STEER_REQUEST'])
    elif family == 'gm':
      raw, active = values['LKASteeringCmd'], bool(values['LKASteeringCmdActive'])
    elif family == 'volkswagen':
      if 'HCA_01_LM_Offset' in values:
        raw, active = values['HCA_01_LM_Offset'] * (-1 if values['HCA_01_LM_OffSign'] else 1), bool(values['HCA_01_Sendestatus'])
      else:
        raw, active = values['LM_Offset'] * (-1 if values['LM_OffSign'] else 1), values['HCA_Status'] == 7
    elif family == 'subaru':
      if witness.codec_id == 'subaru.PREGLOBAL_ES_LKAS':
        raw, active = values['LKAS_Command'], bool(values['LKAS_Active'])
      else:
        raw, active = values['LKAS_Output'], bool(values['LKAS_Request'])
    elif family == 'mazda':
      raw, active = values['LKAS_REQUEST'], witness.steer_requested
    elif family == 'chrysler':
      raw, active = values['STEERING_TORQUE'], bool(values['LKAS_CONTROL_BIT'])
    elif family == 'mg':
      raw, active = values['LKAReqToqHSC2'], bool(values['LKAReqToqStsHSC2'])
    elif family == 'rivian':
      raw, active = values['ACM_lkaStrToqReq'], bool(values['ACM_lkaActToi'])
    else:
      raise ValueError('unknown actual torque codec')
    if int(raw) != witness.final_raw or active is not witness.steer_requested:
      raise ValueError('actual steering CAN bytes mismatch')
    self.verified[event.apply_ns] = True
    while len(self.verified) > MAX_EMISSIONS:
      self.verified.popitem(last=False)
