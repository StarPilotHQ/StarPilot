"""Sibling fork-carrier codec; never edits legacy learning evidence helpers."""
from capnp.lib.capnp import _DynamicStructReader, _DynamicStructBuilder
from openpilot.cereal import custom
from openpilot.starpilot.flm.feedback_observation import (
  CoPublication, CoConsumption, validate_publication, validate_consumption,
)

_PUB = (
  ('version','version'),('cardSession','session'),('publicationSequence','sequence'),
  ('publicationMonoTime','publication_ns'),('sourceCarStateMonoTime','output_state_ns'),
  ('outputKnown','output_known'),('outputControlMonoTime','output_control_ns'),
  ('outputApplyMonoTime','output_apply_ns'),('outputNativeIdentity','native_identity'),
  ('outputCarrierSequence','output_carrier_sequence'),('reportedTorque','reported_torque'),
  ('outputValid','valid'),('coBodySha256','co_body_sha256'),('applyEvidenceSha256','applied_sha256'),
  ('sourceEpoch','source_epoch'),('cpSha256','cp_sha256'),('carrierObservedMonoTime','carrier_observed_ns'),
)
_CON = (
  ('version','version'),('controlMonoTime','control_ns'),('consumedPublicationMonoTime','publication_ns'),
  ('consumedRecvFrame','recv_frame'),('consumedReceiptMonoTime','receipt_ns'),('consumedSeen','seen'),
  ('consumedValid','valid'),('coBodySha256','co_body_sha256'),('reportedTorque','reported_torque'),
  ('requestTorque','requested_torque'),('latActive','lat_active'),('inputSafetyLimited','input_limited'),
  ('publishedSafetyLimited','published_limited'),('priorPublishControlMonoTime','prior_publish_ns'),
  ('sourceEpoch','source_epoch'),('cpSha256','cp_sha256'),
)
_DATA = {'session':16,'co_body_sha256':32,'applied_sha256':32}
_BOOL = {'output_known','valid','seen','lat_active','input_limited','published_limited'}
_FLOAT = {'reported_torque','requested_torque'}
_TEXT = {'source_epoch','cp_sha256','native_identity'}


def _carrier(parent, schema):
  if type(parent) not in (_DynamicStructReader,_DynamicStructBuilder) or parent.schema != schema:
    raise ValueError('exact reserved feedback carrier schema')


def _write(row, fields):
  values = {}
  for wire, name in fields:
    value = getattr(row, name)
    values[wire] = bytes.fromhex(value) if name in _DATA else value
  return values


def _read(row, fields):
  values = {}
  for wire,name in fields:
    raw = getattr(row,wire)
    if name in _DATA:
      value = bytes(raw)
      if len(value) != _DATA[name] and not (name == 'applied_sha256' and len(value) == 0):
        raise ValueError('exact feedback Data length')
      values[name] = value.hex()
    elif name in _BOOL:
      values[name] = bool(raw)
    elif name in _FLOAT:
      values[name] = float(raw)
    elif name in _TEXT:
      if len(str(raw)) > 64:
        raise ValueError('feedback Text bound')
      values[name] = str(raw)
    else:
      values[name] = int(raw)
  return values


def write_publication(parent, row):
  _carrier(parent,custom.StarPilotCarState.schema)
  validate_publication(row)
  parent.feedbackPublication = _write(row,_PUB)


def read_publication(parent):
  _carrier(parent,custom.StarPilotCarState.schema)
  row = parent.feedbackPublication
  if row.version == 0:
    return None
  return validate_publication(CoPublication(**_read(row,_PUB)))


def write_consumption(parent, row):
  _carrier(parent,custom.StarPilotLateralState.schema)
  validate_consumption(row)
  parent.feedbackConsumption = _write(row,_CON)


def read_consumption(parent):
  _carrier(parent,custom.StarPilotLateralState.schema)
  row = parent.feedbackConsumption
  if row.version == 0:
    return None
  return validate_consumption(CoConsumption(**_read(row,_CON)))
