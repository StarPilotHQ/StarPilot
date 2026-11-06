"""Separate unchanged source limiter hints from exact consumed estimation rows."""

from opendbc.car.steering_projection import TorqueProjection

RAW_TORQUE = 'torque-selected-stage-snapshot-v1'


def asynchronous_hint(control):
  """Only the registered raw torque source defines limited as this async hint."""
  try:
    return bool(
      control.limited
      and control.domain == 'normalized_torque'
      and control.stage_schema_revision in (RAW_TORQUE, 'pid-selected-stage-snapshot-v2')
      and not control.saturated
      and dict(control.replay_inputs)['curvature_limited'] == 0.0
    )
  except (AttributeError, KeyError, TypeError, ValueError):
    return False


def bounded_context_output(output):
  """Primitive pending context only; child must decode/recompute native limits."""
  try:
    witness = output.projection
    return bool(
      output.domain == 'normalized_torque'
      and isinstance(witness, TorqueProjection)
      and output.valid
      and output.active
      and not output.override
      and not output.fault
      and output.native_allowed
      and output.limited
      and witness.steer_requested
      and witness.reported_torque == output.applied
      and (
        witness.limiter_kind in ('driver', 'measured')
        and witness.unlimited_raw is not None
        and witness.unlimited_raw != witness.final_raw
        or witness.family == 'honda'
        and witness.mapping_kind == 'honda_lookup'
        and witness.limiter_kind == 'normalized_rate'
        and witness.unlimited_raw is None
      )
    )
  except (AttributeError, TypeError, ValueError):
    return False


def consumed_sources_current(control, output):
  """Two actual consumption instants, never a cross-process source-age proxy.

  Each state is fresh at its own consumer; the original command itself also
  reaches Card within30ms. This is evidence qualification, not driving authority.
  """
  try:
    stamps = control.state_ns, control.control_ns, output.state_ns, output.apply_ns
    return bool(
      all(type(stamp) is int and stamp > 0 for stamp in stamps)
      and control.state_ns <= control.control_ns <= output.apply_ns
      and control.state_ns <= output.state_ns <= output.apply_ns
      and control.control_ns - control.state_ns <= 30_000_000
      and output.apply_ns - output.state_ns <= 30_000_000
      and output.apply_ns - control.control_ns <= 30_000_000
    )
  except (AttributeError, TypeError, ValueError, OverflowError):
    return False


def exact_unclipped_pair(control, output):
  """Resolve one async hint only from its original consumed source and leases."""
  try:
    witness = output.projection
    if (
      not asynchronous_hint(control)
      or not isinstance(witness, TorqueProjection)
      or not output.valid
      or not output.active
      or output.override
      or output.fault
      or output.limited
      or not output.native_allowed
      or output.domain != control.domain
      or output.control_ns != control.control_ns
      or not consumed_sources_current(control, output)
      or not 0 < control.source_receipt_ns <= control.control_ns <= control.source_receipt_ns + 500_000_000
      or witness.requested_torque != control.requested
      or witness.reported_torque != output.applied
      or not witness.steer_requested
      or witness.unlimited_raw != witness.final_raw
    ):
      return False
    if control.native_required and (
      not control.native_allowed
      or not control.native_identity
      or not 0 < control.native_observed_ns <= control.control_ns <= output.apply_ns <= control.native_expires_ns
      or control.native_expires_ns - control.native_observed_ns > 200_000_000
    ):
      return False
    return True
  except (AttributeError, TypeError, ValueError):
    return False


def replay_context(frame):
  """Limited context never becomes a fitting sample or native permission."""
  try:
    return bool(
      frame.healthy
      and not frame.manual
      and frame.actual_lac_command is not None
      and frame.actual_stage_residual is not None
      and len(frame.actual_stage_residual) == 2
      and (
        frame.controller_domain == 'torque'
        and set(dict(frame.torque_values)) == {'effective_factor', 'offset', 'friction', 'pid_pos_limit', 'pid_neg_limit'}
        or frame.controller_domain == 'torque_pid'
        and not frame.torque_values
      )
      and (
        dict(frame.inputs)['curvature_limited'] == 0.0
        or frame.controller_domain == 'torque'
        and dict(frame.inputs)['curvature_limited'] == 1.0
        and dict(frame.inputs)['safety_limited'] == 1.0
      )
    )
  except (AttributeError, KeyError, TypeError, ValueError):
    return False
