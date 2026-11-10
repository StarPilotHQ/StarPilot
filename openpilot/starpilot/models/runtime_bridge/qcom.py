"""Guard legacy QCOM CPU submissions without changing compiled GPU payloads."""
from collections import Counter
from dataclasses import replace
import ctypes
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from tinygrad.device import Buffer, HCQ_RUNTIME_DEV
from tinygrad.dtype import AddrSpace, dtypes
from tinygrad.engine.realize import lower_and_compile
from tinygrad.uop.ops import AxisType, Ops, UOp
from tinygrad.runtime.support.hcq2 import HCQInfo

if TYPE_CHECKING:
  from openpilot.starpilot.models.runtime_bridge.legacy_types import LegacyBufferFactory, LegacyCall, LegacyUOpFactory


def byte_inventory(root):
  return Counter(hashlib.sha256(u.arg).hexdigest() for u in root.toposort()
                 if u.op is Ops.BINARY and isinstance(u.arg, bytes))


class Protection:
  def __init__(self, library):
    self.library = ctypes.CDLL(str(Path(library).resolve()))
    self.function = self.library.starpilot_bounded_wait
    self.function.argtypes = (ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint64)
    self.function.restype = ctypes.c_uint64
    address = ctypes.cast(self.function, ctypes.c_void_p).value
    assert address is not None
    self.pointer = cast("LegacyBufferFactory", Buffer)(cast(str, HCQ_RUNTIME_DEV.value), 1, dtypes.uint64,
                          initial_value=address.to_bytes(8, 'little'))
    self.records = []

  def protect_call(self, call, *, fresh_source=False):
    info, program = call.arg.aux, call.body
    if not isinstance(info, HCQInfo) or info.device != ('QCOM',):
      raise ValueError('Only the explicit QCOM compatibility path is supported')
    if fresh_source:
      if program.op is not Ops.SINK:
        raise ValueError('Expected an explicit fresh QCOM CPU host source')
      sink = program
    else:
      if program.op is not Ops.PROGRAM or program.arg.target.device != 'CPU' or len(program.src) != 4:
        raise ValueError('Expected a stored CPU host submission')
      sink = program.src[0]
    if sink.op is not Ops.SINK or sink.arg.name != 'hcq_submit':
      raise ValueError('Expected the exact host submission source')
    if info.nargs != len(call.src) - 1:
      raise ValueError('Host argument binding changed')
    before = byte_inventory(sink)
    # New pointer is appended to buffers; named ALU arguments follow all buffers.
    params = {u: u.replace(arg=replace(u.arg, slot=u.arg.slot + 1)) for u in sink.toposort()
              if u.op is Ops.PARAM and u.arg.addrspace is AddrSpace.ALU}
    sink = sink.substitute(params, enter_calls=True)
    pointer = UOp.param(info.nargs, dtypes.uint64, 1, cast(str, HCQ_RUNTIME_DEV.value), name='bounded_wait_pointer')
    callee = UOp.custom_function('starpilot_bounded_wait', pointer.index(0).load())
    replacements, seen_loops = {}, set()
    for end in sink.toposort():
      if end.op is not Ops.END or len(end.src) != 3:
        continue
      done, loop, condition = end.src
      if loop.op is not Ops.RANGE or loop.arg != (0, AxisType.WEAK) or loop.src != (UOp(Ops.NOOP),):
        continue
      if (done.op is not Ops.LOAD or condition.op is not Ops.CMPLT or condition.src[0] is not done or
          done.dtype != dtypes.uint64 or condition.src[1].dtype != dtypes.uint64):
        raise ValueError('Unknown conditional fence')
      index = done.src[0]
      if index.op is not Ops.INDEX or len(index.src) != 2 or index.src[1].val != 0:
        raise ValueError('Unknown signal index')
      signal = index.src[0].without_after
      if signal.op is not Ops.PARAM or signal.dtype != dtypes.uint64 or not 0 <= signal.arg.slot < info.nargs:
        raise ValueError('Unknown timeline parameter')
      outer = call.src[1 + signal.arg.slot].without_after
      if outer.op is not Ops.PARAM or outer.tag != 'timeline' or tuple(outer.device) != ('QCOM',):
        raise ValueError('Timeline is not the native QCOM fence')
      target = condition.src[1]
      if loop in target.toposort():
        raise ValueError('Fence target depends on its polling loop')
      signal_index = index.substitute({loop: UOp(Ops.NOOP)}, enter_calls=True)
      replacements[end] = cast("LegacyCall", callee).call(signal_index, target, ret_dtype=dtypes.uint64)
      seen_loops.add(loop)
    weak = {u for u in sink.toposort() if u.op is Ops.RANGE and u.src and u.src[0].op is Ops.NOOP}
    if len(replacements) != 1 or weak != seen_loops:
      raise ValueError('Host fence shape is not exactly the qualified QCOM shape')
    rewritten = sink.substitute(replacements, enter_calls=True)
    if byte_inventory(rewritten) != before:
      raise ValueError('Opaque GPU payload changed')
    if any(u.op is Ops.RANGE and u.src and u.src[0].op is Ops.NOOP for u in rewritten.toposort()):
      raise ValueError('An unbounded host loop remains')
    if any(u.op is Ops.CALL and u.body.op in (Ops.SINK, Ops.PROGRAM) for u in rewritten.toposort()):
      raise ValueError('Nested compilation request in host source')
    # The only compilation request is this CPU SINK.
    new_call = call.replace(src=(rewritten, *call.src[1:], cast("LegacyUOpFactory", UOp).from_buffer(self.pointer)),
                            arg=replace(call.arg, aux=replace(info, nargs=info.nargs + 1)))
    if new_call.device != cast(str, HCQ_RUNTIME_DEV.value) or cast(str, HCQ_RUNTIME_DEV.value).split(':')[0] != 'CPU':
      raise ValueError('Refusing any non-CPU host compilation')
    compiled = lower_and_compile(UOp(Ops.LINEAR, src=(new_call,))).src[0]
    if compiled.body.arg.target.device != 'CPU' or byte_inventory(compiled.body.src[0]) != before:
      raise ValueError('Host-only compilation contract failed')
    self.records.append({'kernels': len(info.kernels), 'fences': len(replacements),
                         'gpu_payload_hashes_preserved': True, 'new_globals': compiled.body.arg.globals})
    return compiled

  def artifact(self, artifact):
    if artifact.get('execution_device') != 'QCOM':
      raise ValueError('Chestnut bridge is not qualified')
    count = 0
    for jit in artifact.values():
      cap = getattr(jit, 'captured', None)
      if cap is None:
        continue
      if 'linear' in cap.__dict__:
        raise ValueError('Protection must precede captured execution')
      for entry in cap._linear.src:
        call = entry.without_after
        if call.op is not Ops.CALL or not (call.body.op is Ops.COPY or isinstance(call.arg.aux, HCQInfo)):
          raise ValueError('Unexpected top-level captured schedule entry')
      old_cpu = {u.src[-1] for u in cap._linear.toposort() if u.op is Ops.PROGRAM and u.arg.target.device == 'CPU'}
      old_payload = Counter(hashlib.sha256(u.arg).hexdigest() for u in cap._linear.toposort()
                            if u.op is Ops.BINARY and u not in old_cpu)
      replacements = {u: self.protect_call(u) for u in cap._linear.toposort()
                      if u.op is Ops.CALL and isinstance(u.arg.aux, HCQInfo)}
      if not replacements:
        raise ValueError('Captured graph has no qualified native submission')
      proposed = cap._linear.substitute(replacements, enter_calls=True)
      new_cpu = {u.src[-1] for u in proposed.toposort() if u.op is Ops.PROGRAM and u.arg.target.device == 'CPU'}
      new_payload = Counter(hashlib.sha256(u.arg).hexdigest() for u in proposed.toposort()
                            if u.op is Ops.BINARY and u not in new_cpu)
      if old_payload != new_payload:
        raise ValueError('Captured opaque runtime payload changed')
      self.records[-1]['opaque_payload_count'] = sum(old_payload.values())
      cap._linear = proposed
      count += 1
    if count != 3:
      raise ValueError('Expected policy and both camera captures')
    return artifact
