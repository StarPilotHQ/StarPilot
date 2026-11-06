"""Historical core metadata for statistics, NEVER full replay or authority.

The numerical/native admission rereads and validates every selected original
full ControlEvidence and AppliedEvidence. This reader does not decode unused
replay vectors or assert that they are valid.
"""

from dataclasses import dataclass
import math
from openpilot.starpilot.flm.route_recording import SCHEMAS, decode


@dataclass(frozen=True)
class RecordedControlMetadata:
  control_ns: int
  state_ns: int
  source_receipt_ns: int
  native_observed_ns: int
  native_expires_ns: int
  enabled: bool
  active: bool
  valid: bool
  manual: bool
  override: bool
  fault: bool
  saturated: bool
  limited: bool
  native_required: bool
  native_allowed: bool
  controller_mode: str
  controller_revision: str
  baseline_revision: str
  baseline_values: tuple
  vehicle_model_anchor: tuple
  route_applied_coordinates: tuple
  route_effect_revision: str
  route_configuration_scope: str
  full_replay_validated: bool = False
  stage_schema_revision: str = ''
  replay_inputs: tuple = ()


def _inhibited_inputs(row, schema):
  # Only two original flags are needed here; full replay stays a separate check.
  if not row.limited or schema.split('@', 1)[0] != 'torque-selected-stage-snapshot-v1':
    return ()
  from openpilot.starpilot.flm.native_refinement_wire import EXTENDED_INPUTS

  if len(row.replayInputs) > EXTENDED_INPUTS:
    return ()
  inputs = {}
  for value in row.replayInputs:
    name = str(value.name)
    if name not in ('active', 'safety_limited'):
      continue
    kind = value.which()
    if name in inputs or kind not in ('number', 'boolean'):
      return ()
    number = float(value.number) if kind == 'number' else float(value.boolean)
    if number not in (0.0, 1.0):
      return ()
    inputs[name] = number
  return tuple(inputs.items()) if set(inputs) == {'active', 'safety_limited'} else ()


def read_control_metadata(event):
  if not event.valid or event.starpilotLateralState.learningEvidence.version != 1:
    raise ValueError('Original metadata envelope/version required')
  row = event.starpilotLateralState.learningEvidence
  mode, revision, baseline = str(row.controllerMode), str(row.controllerRevision), str(row.baselineRevision)
  values = tuple(float(v) for v in row.baselineValues)
  anchor = tuple(float(v) for v in row.vehicleModelAnchor)
  if (
    not 1 <= len(mode) <= 32
    or not 1 <= len(revision) <= 192
    or len(baseline) != 64
    or baseline.strip('0123456789abcdef')
    or not 1 <= len(values) <= 32
    or any(not math.isfinite(v) for v in values)
    or len(anchor) not in (0, 3)
    or any(not math.isfinite(v) or not v for v in anchor)
  ):
    raise ValueError('Finite original controller context required')
  schema = str(row.stageSchemaRevision)
  if schema in SCHEMAS:
    _, coordinates, route_revision, scope = decode(schema, tuple((str(v.name), float(v.value)) for v in row.stageValues))
  else:
    if '@route-' in schema or len(row.stageValues) > 40 or any(str(v.name).startswith('route.') for v in row.stageValues):
      raise ValueError('Unknown/overbound route metadata')
    coordinates, route_revision, scope = (), '', ''
  return RecordedControlMetadata(
    int(row.controlMonoTime),
    int(row.sourceStateMonoTime),
    int(row.sourceReceiptMonoTime),
    int(row.nativeObservedMonoTime),
    int(row.nativeValidUntilMonoTime),
    bool(row.enabled),
    bool(row.active),
    bool(row.valid),
    bool(row.manual),
    bool(row.override),
    bool(row.fault),
    bool(row.saturated),
    bool(row.limited),
    bool(row.nativeRequired),
    bool(row.nativeAllowed),
    mode,
    revision,
    baseline,
    values,
    anchor,
    coordinates,
    route_revision,
    scope,
    stage_schema_revision=schema,
    replay_inputs=_inhibited_inputs(row, schema),
  )
