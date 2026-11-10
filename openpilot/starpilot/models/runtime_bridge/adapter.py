"""One-time Dom protocol5 -> selected9d reconstruction; NOT artifact admission."""
from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import io
import json
import pickle
from pathlib import Path

_TABLE = json.loads(Path(__file__).with_name('ops-mapping.json').read_text())
DOM_OP_NAMES = tuple(row['Dom_name'] for row in _TABLE['rows'])
TARGET_OP_NAMES = tuple(name for name in DOM_OP_NAMES if name not in ('RETURNED', 'CONTIGUOUS'))
DOM_GWM_OPCODE_SHA256 = 'c3915ad6ba00b1d2ab130e49a9ce18ffc66a75e78da4742ed699e4c15b97f4ff'
ALLOWED_GLOBALS = frozenset((
  ('builtins', 'slice'), ('tinygrad.engine.jit', '_TinyJit'), ('tinygrad.engine.jit', 'CapturedJit'),
  ('tinygrad.tensor', 'Tensor'), ('tinygrad.uop.ops', 'UOp'), ('tinygrad.uop', 'Ops'),
  ('tinygrad.uop.ops', 'ParamArg'), ('tinygrad.dtype', 'DType'), ('tinygrad.dtype', 'AddrSpace'),
  ('tinygrad.device', 'Buffer'), ('tinygrad.uop.ops', 'CallInfo'), ('tinygrad.uop.ops', 'KernelInfo'),
  ('tinygrad.codegen.opt', 'Opt'), ('tinygrad.codegen.opt', 'OptOps'), ('tinygrad.renderer', 'Estimates'),
  ('tinygrad.uop.ops', 'ProgramInfo'), ('tinygrad.helpers', 'Target'), ('tinygrad.dtype', 'ConstFloat'),
  ('tinygrad.uop.ops', 'AxisType'),
))


class ArchivedOptOps(Enum):
  # Optimizer provenance ONLY. These are NOT aliases for current SPLIT.
  TC = 1
  UPCAST = 2
  LOCAL = 3
  GROUPTOP = 4
  PADTO = 5
  SWAP = 6


@dataclass(frozen=True)
class TargetAPI:
  Ops: object
  Buffer: object
  UOp: object
  ParamArg: object
  classes: dict


def selected9d_target():
  # Parent first selects/verifies9d and installs unchanged freshUSB guards.
  # This function chooses no runtime or device and installs no guard bypass.
  from tinygrad.device import Buffer
  from tinygrad.uop import Ops
  from tinygrad.uop.ops import UOp, ParamArg
  return TargetAPI(Ops, Buffer, UOp, ParamArg, {})


def map_dom_buffer_args(args):
  if len(args) not in (7, 10):
    raise ValueError('Unexpected Dom Buffer reconstruction arity')
  if type(args[6]) is not int or args[6] < 0:
    raise ValueError('Invalid Dom Buffer ownership counter')
  if len(args) == 10:
    if args[6] != 0 or any(x is not None for x in args[3:6]) or args[7] is None:
      raise ValueError('Unexpected Dom Buffer view reconstruction')
    if type(args[8]) is not int or args[8] < 0 or type(args[9]) is not bool:
      raise ValueError('Invalid Dom Buffer view offset/allocation')
  return args[:6] + args[7:]


class MigrationUnpickler(pickle.Unpickler):
  def __init__(self, opcodes, *, buffers, target, expected_opcode_sha256=DOM_GWM_OPCODE_SHA256):
    if not isinstance(opcodes, bytes) or not 0 < len(opcodes) <= 64 << 20:
      raise ValueError('Bounded immutable opcode bytes required')
    if hashlib.sha256(opcodes).hexdigest() != expected_opcode_sha256:
      raise ValueError('Source opcode identity differs from audited artifact')
    if tuple(target.Ops.__members__) != TARGET_OP_NAMES:
      raise ValueError('Target Ops differs from pinned9d source')
    self.target, self.counts = target, Counter()
    self.bound_buffers = []
    super().__init__(io.BytesIO(opcodes), buffers=buffers)

  def find_class(self, module, name):
    key = (module, name)
    if key not in ALLOWED_GLOBALS:
      raise ValueError('Unaudited Dom pickle global: ' + '.'.join(key))
    if key == ('tinygrad.uop', 'Ops'):
      return self._ops
    if key == ('tinygrad.device', 'Buffer'):
      return self._buffer
    if key == ('tinygrad.uop.ops', 'UOp'):
      return self._uop
    if key == ('tinygrad.codegen.opt', 'OptOps'):
      return ArchivedOptOps
    # ProgramInfo receives its ORIGINAL full state, including old name, in
    # the current class __dict__. No source field is stripped or retagged.
    return self.target.classes[key] if key in self.target.classes else super().find_class(module, name)

  def persistent_load(self, record):
    raise ValueError('Dom protocol5 must not contain arena persistent records')

  def _ops(self, value):
    if type(value) is not int or not 1 <= value <= len(DOM_OP_NAMES):
      raise ValueError('Unknown source Ops ordinal')
    name = DOM_OP_NAMES[value-1]
    if name not in self.target.Ops.__members__:
      raise ValueError('Source Ops has no admitted semantic mapping: ' + name)
    self.counts['Ops.' + name] += 1
    return self.target.Ops[name]

  def _buffer(self, *args):
    result = self.target.Buffer(*map_dom_buffer_args(args))
    self.counts['Buffer.' + str(len(args))] += 1
    return result

  def _uop(self, *args):
    if len(args) not in (5, 6):
      raise ValueError('Unexpected Dom UOp reconstruction arity')
    op, src, arg, tag, metadata = args[:5]
    if not isinstance(op, self.target.Ops) or not isinstance(src, tuple):
      raise ValueError('Invalid Dom UOp op/source reconstruction')
    if len(args) == 6:
      buffer = args[5]
      if op is not self.target.Ops.BUFFER or src or not isinstance(arg, self.target.ParamArg):
        raise ValueError('Realized source Buffer belongs only to exact BUFFER UOp')
      if not isinstance(buffer, self.target.Buffer) or getattr(arg, 'buffer', None) is not None:
        raise ValueError('Unexpected existing source buffer owner')
      if arg.size != buffer.size or arg.dtype != buffer.dtype or arg.device != buffer.device:
        raise ValueError('Source BUFFER metadata differs from exact realized Buffer')
      arg = replace(arg, buffer=buffer)
      self.bound_buffers.append(buffer)
      self.counts['realized_BUFFER_identity_transfer'] += 1
    self.counts['UOp.' + str(len(args))] += 1
    return self.target.UOp(op, src, arg, tag, metadata)


def inspect_stored_programs(artifact, target):
  """Inspect never-linked graph metadata only; NEVER access cap.linear."""
  if not isinstance(artifact, dict):
    raise ValueError('Unexpected Dom artifact root')
  captures = {key: value.captured for key, value in artifact.items() if getattr(value, 'captured', None) is not None}
  if len(captures) != 3 or 'run_policy' not in captures:
    raise ValueError('Expected exact driving policy plus two camera captures')
  reports = []
  for key, cap in captures.items():
    if 'linear' in cap.__dict__:
      raise ValueError('Source capture was already linked/executed')
    root, payloads, programs = cap._linear, Counter(), []
    for node in root.toposort():
      if node.op is target.Ops.SINK and getattr(node.arg, 'opts_to_apply', None) is not None:
        raise ValueError('Source requests unapplied optimizer choices')
      if node.op is not target.Ops.PROGRAM:
        continue
      device = node.arg.target.device
      if device != 'CPU':
        if not node.src or node.src[-1].op is not target.Ops.BINARY or not isinstance(node.src[-1].arg, bytes):
          raise ValueError('GPU source lacks already-compiled immutable BINARY')
        payloads[hashlib.sha256(node.src[-1].arg).hexdigest()] += 1
      programs.append({'device': device, 'name': getattr(node.arg, 'name', None), 'source_count': len(node.src)})
    reports.append({'capture': str(key), 'top_op': root.op.name, 'top_entries': len(root.src),
                    'programs': programs, 'GPU_binary_sha256_counts': dict(payloads)})
  return reports
