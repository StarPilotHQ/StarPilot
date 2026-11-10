"""Guard legacy Chestnut CPU submissions while preserving compiled GPU payloads."""
from collections import Counter
from dataclasses import replace
import ctypes
import hashlib
from typing import TYPE_CHECKING, cast

from tinygrad import Device
from tinygrad.device import Buffer, HCQ_RUNTIME_DEV
from tinygrad.dtype import AddrSpace, dtypes
from tinygrad.engine.realize import lower_and_compile
from tinygrad.runtime.autogen import libusb
from tinygrad.renderer.cstyle import ClangRenderer
from tinygrad.runtime.support import hcq2
from tinygrad.runtime.support.hcq2 import HCQInfo
from tinygrad.uop.ops import AxisType, Ops, UOp, ProgramInfo
from openpilot.starpilot.models.runtime_bridge.qcom import Protection, byte_inventory

if TYPE_CHECKING:
  from openpilot.starpilot.models.runtime_bridge.legacy_types import LegacyBufferFactory, LegacyCall, LegacyHCQModule, LegacyUOpFactory


class USBProtection(Protection):
  ffi_names = {'libusb_control_transfer': 'sp_control', 'libusb_bulk_transfer': 'sp_bulk',
               'libusb_submit_transfer': 'sp_submit', 'libusb_handle_events_timeout': 'sp_events'}

  def __init__(self, fence_library, usb_library):
    super().__init__(fence_library)
    self.usb_library = ctypes.CDLL(str(usb_library))
    for name, offset in [('status', 16), ('length', 20), ('actual_length', 24)]:
      field = getattr(libusb.struct_libusb_transfer, name)
      if field.offset != offset or field.size != 4:
        raise ValueError('Unqualified libusb native transfer ABI')
    if ctypes.sizeof(ctypes.c_ulong) != 8 or ctypes.sizeof(ctypes.c_void_p) != 8:
      raise ValueError('Only the qualified 64-bit CPU host ABI is supported')
    self.names = [*self.ffi_names.values(), 'sp_loop_touch', 'sp_loop_check']
    addresses = [ctypes.cast(getattr(self.usb_library, name), ctypes.c_void_p).value for name in self.names]
    if any(address is None for address in addresses):
      raise ValueError('Missing native submission guard')
    self.usb_pointer = cast("LegacyBufferFactory", Buffer)(cast(str, HCQ_RUNTIME_DEV.value), len(addresses), dtypes.uint64,
                              initial_value=b''.join(address.to_bytes(8, 'little') for address in addresses))
    self.fresh_records = []

  def protect_call(self, call, *, fresh_source=False):
    if fresh_source:
      raise ValueError('Explicit fresh QCOM host sources are not qualified for the USB guard')
    info, body = call.arg.aux, call.body
    if info.device == ('QCOM',):
      # The already-qualified QCOM transformer accepts a stored CPU program.
      if body.op is Ops.SINK:
        raise ValueError('Unexpected fresh QCOM submission in Chestnut weight loading')
      return super().protect_call(call)
    if info.device != ('AMD',) or not Device['AMD'].is_usb:
      raise ValueError('Only the actual Chestnut USB AMD device is supported')
    if body.op is Ops.PROGRAM:
      if body.arg.target.device != 'CPU' or len(body.src) != 4:
        raise ValueError('Expected a stored CPU submission program')
      sink = body.src[0]
    elif body.op is Ops.SINK:
      sink = body
    else:
      raise ValueError('Unknown Chestnut host submission')
    if sink.op is not Ops.SINK or sink.arg.name != 'hcq_submit' or info.nargs != len(call.src) - 1:
      raise ValueError('Unknown host source or argument binding')
    before = byte_inventory(sink)
    params = {u: u.replace(arg=replace(u.arg, slot=u.arg.slot + 1)) for u in sink.toposort()
              if u.op is Ops.PARAM and u.arg.addrspace is AddrSpace.ALU}
    sink = sink.substitute(params, enter_calls=True)
    pointer = UOp.param(info.nargs, dtypes.uint64, len(self.names), cast(str, HCQ_RUNTIME_DEV.value), name='usb_guard_pointer')
    def callee(name):
      return UOp.custom_function(name, pointer.index(self.names.index(name)).load())
    weak = [u for u in sink.toposort() if u.op is Ops.RANGE and u.src and u.src[0].op is Ops.NOOP]
    if not 1 <= len(weak) < 256 or len({r.arg for r in weak}) != len(weak):
      raise ValueError('Unknown USB polling structure')
    specifications = {}
    for slot, loop in enumerate(weak):
      ends = [u for u in sink.toposort() if u.op is Ops.END and len(u.src) == 3 and u.src[1] is loop]
      if len(ends) != 1 or loop.arg[-1] is not AxisType.WEAK:
        raise ValueError('Unknown USB loop end')
      condition = ends[0].src[2]
      if len(loop.src) == 1 and condition.op is Ops.CMPLT:
        timeout = 30_000_000_000
      elif (condition.op is Ops.CMPNE and condition.src[1].dtype is dtypes.bool and
            condition.src[1].vmin == condition.src[1].vmax == 1 and
            condition.src[0].op is Ops.CMPNE and condition.src[0].src[0].op is Ops.LOAD and
            condition.src[0].src[1].vmin == condition.src[0].src[1].vmax == 0xff):
        # Fresh 9d eq(status,0xff) is exactly (status != 0xff) != True.
        # Classify it without changing the original polling expression.
        timeout = 10_000_000_000
      elif condition.op is Ops.CMPEQ and condition.src[1].vmin == condition.src[1].vmax == 0xff:
        timeout = 10_000_000_000
      elif condition.op is Ops.CMPLT and condition.src[0].vmin == condition.src[0].vmax == 1 and condition.src[1].op is Ops.AND:
        timeout = 1_000_000_000
      else:
        raise ValueError('Unqualified USB polling condition')
      specifications[loop.arg] = (slot, timeout)
    calls = [u for u in sink.toposort() if u.op is Ops.CALL and u.body.op is Ops.CUSTOM_FUNCTION]
    if any(u.body.arg not in {*self.ffi_names, 'memcpy'} for u in calls):
      raise ValueError('Unqualified native USB function')
    def scalar64(u):
      return UOp(Ops.CUSTOMI, src=(u,), arg=('((unsigned long)({0}))', dtypes.uint64))
    touched, substitutions = set(), {}
    for u in calls:
      if u.body.arg not in self.ffi_names:
        continue
      touches = []
      for loop in weak:
        if loop in u.ranges:
          slot, timeout = specifications[loop.arg]
          token = next((a for a in u.src[1:] if loop in a.ranges), None)
          if token is None:
            raise ValueError('Polling call has no original open-loop argument')
          touches.append(cast("LegacyCall", callee('sp_loop_touch')).call(UOp.const(slot, dtypes.uint64),
                           UOp.const(timeout, dtypes.uint64), scalar64(token), ret_dtype=dtypes.uint64))
          touched.add(loop.arg)
      marker = sum(touches[1:], touches[0]) if touches else UOp.const(0, dtypes.uint64)
      substitutions[u] = cast("LegacyCall", callee(self.ffi_names[u.body.arg])).call(
        *[scalar64(a) for a in (u.body.src[0], *u.src[1:])], marker, ret_dtype=dtypes.int32)
    if touched != set(specifications) or not substitutions:
      raise ValueError('A native polling loop has no entry guard')
    sink = sink.substitute(substitutions, enter_calls=True)
    guarded_ranges = [u for u in sink.toposort() if u.op is Ops.RANGE and u.src and u.src[0].op is Ops.NOOP]
    if len(guarded_ranges) != len(weak) or {r.arg for r in guarded_ranges} != set(specifications):
      raise ValueError('Native polling ranges changed')
    ends = {u: u.replace(src=(*u.src[:2], cast("LegacyCall", callee('sp_loop_check')).call(
            u.src[2].cast(dtypes.uint64), UOp.const(specifications[u.src[1].arg][0], dtypes.uint64),
            ret_dtype=dtypes.uint64).cast(dtypes.bool)))
            for u in sink.toposort() if u.op is Ops.END and len(u.src) == 3 and u.src[1].op is Ops.RANGE and u.src[1].arg in specifications}
    if len(ends) != len(weak):
      raise ValueError('Incomplete native polling protection')
    rewritten = sink.substitute(ends, enter_calls=True)
    for end in [u for u in rewritten.toposort() if u.op is Ops.END and len(u.src) == 3 and u.src[1].op is Ops.RANGE and u.src[1].arg in specifications]:
      check = end.src[2].src[0]
      if (check.op is not Ops.CALL or check.body.arg != 'sp_loop_check' or
          check.src[2].val != specifications[end.src[1].arg][0]):
        raise ValueError('Polling exit is not guarded')
    if byte_inventory(rewritten) != before:
      raise ValueError('Opaque device payload changed')
    if any(u.op is Ops.CALL and u.body.op in (Ops.SINK, Ops.PROGRAM) for u in rewritten.toposort()):
      raise ValueError('Nested device compilation in a CPU host')
    updated = call.replace(src=(rewritten, *call.src[1:], cast("LegacyUOpFactory", UOp).from_buffer(self.usb_pointer)),
                           arg=replace(call.arg, aux=replace(info, nargs=info.nargs + 1)))
    if updated.device != cast(str, HCQ_RUNTIME_DEV.value) or cast(str, HCQ_RUNTIME_DEV.value).split(':')[0] != 'CPU':
      raise ValueError('Refusing non-CPU compilation')
    compiled = lower_and_compile(UOp(Ops.LINEAR, src=(updated,))).src[0]
    host_renderer = Device[cast(str, HCQ_RUNTIME_DEV.value)].renderer
    if (compiled.body.arg.target.device != 'CPU' or not isinstance(host_renderer, ClangRenderer) or
        compiled.body.arg.target != host_renderer.target or byte_inventory(compiled.body.src[0]) != before):
      raise ValueError({'failure':'USB host-only compilation contract failed','target':repr(compiled.body.arg.target),
                        'actual_renderer':type(host_renderer).__name__, 'expected_target':repr(host_renderer.target),
                        'before_payloads':dict(before),'after_payloads':dict(byte_inventory(compiled.body.src[0]))})
    self.records.append({'kernels': len(info.kernels), 'fences': len(weak), 'usb_checked_calls': len(substitutions),
                         'gpu_payload_hashes_preserved': True, 'fresh': body.op is Ops.SINK})
    return compiled

  def install_fresh_guard(self):
    legacy_hcq = cast("LegacyHCQModule", hcq2)
    original = legacy_hcq.lower_and_compile
    if original is not lower_and_compile:
      raise ValueError('Host compiler was already replaced')
    def protect(linear: "UOp") -> "UOp":
      replacements = {u: self.protect_call(u) for u in linear.toposort()
                      if u.op is Ops.CALL and isinstance(u.arg.aux, HCQInfo) and u.body.op is Ops.SINK}
      self.fresh_records.append(len(replacements))
      prepared = linear.substitute(replacements, enter_calls=True)
      if any(u.op is Ops.CALL and u.body.op in (Ops.SINK, Ops.PROGRAM) and
             (u.body.op is Ops.SINK or not (isinstance(u.body.arg, ProgramInfo) and u.body.src and
              u.body.src[-1].op is Ops.BINARY)) for u in prepared.toposort()):
        raise ValueError('Refusing residual unqualified compilation during native weight loading')
      return original(prepared)
    legacy_hcq.lower_and_compile = protect

  def artifact(self, artifact):
    if artifact.get('execution_device') != 'AMD' or artifact.get('warp_device') != 'QCOM':
      raise ValueError('Unexpected Chestnut artifact backend')
    count = 0
    for jit in artifact.values():
      cap = getattr(jit, 'captured', None)
      if cap is None:
        continue
      if 'linear' in cap.__dict__:
        raise ValueError('Protection must precede captured execution')
      def payloads(root):
        cpu = {u.src[-1] for u in root.toposort() if u.op is Ops.PROGRAM and u.arg.target.device == 'CPU'}
        return Counter(hashlib.sha256(u.arg).hexdigest() for u in root.toposort() if u.op is Ops.BINARY and u not in cpu)
      for entry in cap._linear.src:
        call = entry.without_after
        if call.op is not Ops.CALL or not (call.body.op is Ops.COPY or isinstance(call.arg.aux, HCQInfo)):
          raise ValueError('Unexpected top-level captured schedule entry')
      replacements = {u: self.protect_call(u) for u in cap._linear.toposort()
                      if u.op is Ops.CALL and isinstance(u.arg.aux, HCQInfo)}
      if not replacements:
        raise ValueError('Missing native submission')
      proposed = cap._linear.substitute(replacements, enter_calls=True)
      if payloads(cap._linear) != payloads(proposed):
        raise ValueError('Captured opaque runtime payload changed')
      cap._linear = proposed
      count += 1
    if count != 3:
      raise ValueError('Expected policy and both camera captures')
    return artifact
