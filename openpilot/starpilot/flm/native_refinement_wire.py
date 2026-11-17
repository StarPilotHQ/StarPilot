"""Bounded original native receipt metadata; never tuning or authority."""
import base64
import binascii
from dataclasses import dataclass
import math
import struct

FIELD = 'native_refinement_v1'
PREFIX = 'nr1:'
FORMAT = '!BQQBd9f'
ORDINARY_INPUTS = 32
EXTENDED_INPUTS = 33

# Only deep-immutable metadata tuples already checked in this process. Live
# source/native permission and receiver clocks remain outside this proof.
_INPUT_PROOFS: tuple[object, ...] = ()


@dataclass(frozen=True)
class NativeProof:
  source_ns: int
  applied_ns: int
  host: bool
  dt: float
  target: tuple[float, ...]
  before: tuple[float, ...]
  after: tuple[float, ...]


def encode(values):
  """Copy existing original provenance; no source lookup/fit/controller step."""
  if not values:
    return None
  if (type(values) is not tuple or len(values) < 14 or values[0] != 1. or
      any(type(value) not in (int,float) or not math.isfinite(value) for value in values)):
    raise ValueError('native compact original provenance')
  count = int(values[7])
  if (values[7] != count or not 5 <= count <= 32 or len(values) != 11+2*count or
      values[5] not in (0.,1.) or not values[6] > 0 or
      any(value < 0 or value > 0xffffffff or int(value) != value for value in values[1:5])):
    raise ValueError('native compact original shape')
  source = (int(values[1]) << 32) | int(values[2])
  applied = (int(values[3]) << 32) | int(values[4])
  native = (*values[8:11],*values[12:15],*values[12+count:15+count])
  if any(struct.unpack('!f',struct.pack('!f',value))[0] != value for value in native):
    raise ValueError('native compact exact Float32 values')
  raw = struct.pack(FORMAT,1,source,applied,int(values[5]),values[6],*native)
  return PREFIX+base64.b64encode(raw).decode('ascii')


def decode(value):
  if type(value) is not str or len(value) != 88 or not value.startswith(PREFIX):
    raise ValueError('native compact version/size')
  try:
    raw = base64.b64decode(value[len(PREFIX):],validate=True)
    version,source,applied,host,dt,*native = struct.unpack(FORMAT,raw)
  except (ValueError,struct.error,binascii.Error) as error:
    raise ValueError('native compact original bytes') from error
  if PREFIX+base64.b64encode(raw).decode('ascii') != value:
    raise ValueError('native compact canonical original bytes')
  if (version != 1 or host not in (0,1) or not 0 < source <= applied <= source+500_000_000 or
      not math.isfinite(dt) or dt <= 0 or host and dt > .05 or
      any(not math.isfinite(item) for item in native)):
    raise ValueError('native compact original fields')
  return NativeProof(source,applied,bool(host),dt,tuple(native[:3]),tuple(native[3:6]),tuple(native[6:9]))


def valid_inputs(values):
  """Legacy32 ordinary inputs, plus ONLY one recognized bounded extension."""
  global _INPUT_PROOFS
  if any(values is previous for previous in _INPUT_PROOFS):
    return True
  try:
    if (type(values) is not tuple or len(values) > EXTENDED_INPUTS or
        any(type(row) is not tuple or len(row) != 2 for row in values) or
        len({name for name,_ in values}) != len(values)):
      return False
    ordinary = tuple((name,value) for name,value in values if name != FIELD)
    if len(ordinary) > ORDINARY_INPUTS or any(type(name) is not str or not 0 < len(name) <= 96 or
        not (type(value) is bool or type(value) in (int,float) and math.isfinite(value) or
             type(value) is str and len(value) <= 96) for name,value in ordinary):
      return False
    proof = tuple((name,value) for name,value in values if name == FIELD)
    if proof:
      decode(proof[0][1])
    if not proof or type(proof[0][0]) is str:
      _INPUT_PROOFS = (*_INPUT_PROOFS[-3:], values)
    return True
  except (TypeError,ValueError,OverflowError):
    return False
