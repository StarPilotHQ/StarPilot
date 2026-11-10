"""Admit original Dom artifacts after current guarded CPU host preparation."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json

DOM_ABI = 'dom_v25_protocol5'
TARGET_REVISION = '9d0446a4ba8a532c8b674fb6ad795af015cd9dcf'
ADAPTER_SHA256 = '36ba945bbbf2d954474de5499f791804ecec3a6cf2b52e92c1661bb9b2f4788c'
CAMERAS = ((1928, 1208), (1344, 760))


@dataclass(frozen=True)
class _CaptureProof:
  capture: object
  raw: object
  prepared: object
  linked: object
  report_json: str


@dataclass(frozen=True)
class _Preparation:
  artifact: dict
  captures: tuple


@dataclass(frozen=True)
class _Admission:
  artifact: dict
  contract_json: str
  migration_json: str


def _json(value):
  return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _contract(expected_contract):
  if not isinstance(expected_contract, dict):
    raise ValueError('Missing frozen Dom role contract')
  source = expected_contract.get('source_contract')
  digest = expected_contract.get('sha256')
  if (not isinstance(source, dict) or not isinstance(digest, str) or len(digest) != 64 or
      any(c not in '0123456789abcdef' for c in digest) or source.get('artifact_sha256') != digest or
      source.get('artifact_format') != DOM_ABI or source.get('source_format') != 'tinygrad_single_v1' or
      source.get('source_compiler_revision', 'undeclared') is not None or
      source.get('target_runtime_revision') != TARGET_REVISION or
      type(source.get('artifact_size')) is not int or source['artifact_size'] <= 0 or
      not isinstance(expected_contract.get('path'), str) or not expected_contract['path'] or
      not isinstance(expected_contract.get('model_id'), str) or not expected_contract['model_id'] or
      not isinstance(expected_contract.get('version'), str)):
    raise ValueError('Dom source and target runtime contract mismatch')
  return source


def _captures(artifact):
  captures = {key: value.captured for key, value in artifact.items() if getattr(value, 'captured', None) is not None}
  if set(captures) != {'run_policy', *CAMERAS}:
    raise ValueError('Dom requires its policy and both original camera captures')
  return captures


def _gpu_payloads(root, ops):
  payloads, devices = Counter(), set()
  for node in root.toposort():
    if node.op is ops.SINK and getattr(node.arg, 'opts_to_apply', None) is not None:
      raise ValueError('Dom artifact requests optimizer replay')
    if node.op is not ops.PROGRAM:
      continue
    if node.arg.target.device == 'CPU':
      raise ValueError('Original Dom source must remain pre-HCQ GPU programs')
    if not node.src or node.src[-1].op is not ops.BINARY or not isinstance(node.src[-1].arg, bytes):
      raise ValueError('Dom GPU program lacks its immutable binary')
    devices.add(node.arg.target.device)
    payloads[hashlib.sha256(node.src[-1].arg).hexdigest()] += 1
  return devices, payloads


def _guarded_capture(prepared, expected_device, ops, hcq_info_type):
  submissions = []
  if prepared.op is not ops.LINEAR:
    raise ValueError('Unexpected prepared Dom capture')
  for entry in prepared.src:
    call = entry.without_after
    if call.op is not ops.CALL:
      raise ValueError('Unexpected Dom top-level schedule entry')
    if call.body.op is ops.COPY:
      continue
    info = call.arg.aux
    if (not isinstance(info, hcq_info_type) or info.device != (expected_device,) or
        call.body.op is not ops.PROGRAM or call.body.arg.target.device != 'CPU' or
        len(call.body.src) != 4 or call.body.src[-1].op is not ops.BINARY):
      raise ValueError('Dom capture contains an unguarded GPU submission')
    names = {node.arg for node in call.body.src[0].toposort() if node.op is ops.CUSTOM_FUNCTION}
    if expected_device == 'AMD':
      if not {'sp_loop_touch', 'sp_loop_check'} <= names or not names.intersection({'sp_control', 'sp_bulk', 'sp_submit', 'sp_events'}):
        raise ValueError('Dom AMD host lacks native USB polling and transfer guards')
    elif 'starpilot_bounded_wait' not in names:
      raise ValueError('Dom QCOM host lacks the bounded native fence')
    submissions.append(len(info.kernels))
  if not submissions:
    raise ValueError('Dom capture has no guarded current HCQ submission')
  return {'host_submissions': len(submissions), 'scheduled_kernels': sum(submissions)}


def flatten_dom_graphs(raw, *, ops):
  """Undo only the exact legacy create_graph_call wrapper, without rebinding.

  That wrapper passes deduplicated original GLOBAL PARAMs, and exec_graph
  forwards the enclosing input_uops unchanged. Its nested LINEAR already uses
  the enclosing slot namespace, so replacing it with its ordered entries
  requires no parameter substitution or new graph interpretation.
  """
  if raw.op is not ops.LINEAR:
    raise ValueError('Expected the original Dom LINEAR schedule')
  entries = []
  for entry in raw.src:
    if entry.op is not ops.CALL:
      raise ValueError('Dom graph wrapper must be a plain ordered CALL')
    body = entry.body
    if body.op is not ops.CUSTOM_FUNCTION:
      entries.append(entry)
      continue
    if body.arg != 'graph' or len(body.src) != 1 or body.src[0].op is not ops.LINEAR:
      raise ValueError('Unknown legacy Dom graph wrapper')
    batch = body.src[0].src
    if (not batch or getattr(entry.arg, 'aux', None) is not None or
        any(call.op is not ops.CALL or call.body.op not in (ops.PROGRAM, ops.COPY) for call in batch)):
      raise ValueError('Unsupported nested Dom graph schedule')
    parameters = []
    for call in batch:
      for argument in call.src[1:]:
        for node in argument.toposort():
          if node.op is ops.PARAM and node not in parameters:
            parameters.append(node)
    if entry.src[1:] != tuple(parameters):
      raise ValueError('Dom graph arguments do not preserve the original outer PARAM identity')
    entries.extend(batch)
  result = raw.replace(src=tuple(entries))
  if _gpu_payloads(raw, ops) != _gpu_payloads(result, ops):
    raise ValueError('Dom graph flattening changed the original GPU binaries')
  return result


def create_dom_storage_bindings(artifact, *, ops, buffer_factory, from_buffer):
  """Allocate only storage declarations needed by HCQ copy staging.

  These buffers are never used to run the model. With cache=False the original
  input PARAMs remain runtime arguments; stage_copy only examines their native
  mappability. The caller retains these UOps through guarded compilation/link.
  """
  bindings = {}
  for key, cap in _captures(artifact).items():
    if 'linear' in cap.__dict__:
      raise ValueError('Dom storage must precede guarded capture linking')
    slots = {}
    for param in cap._linear.toposort(enter_calls=False):
      if param.op is not ops.PARAM:
        continue
      spec = (param.arg.size, param.arg.dtype, param.arg.device)
      slot = param.arg.slot
      if (type(slot) is not int or slot < 0 or type(spec[0]) is not int or spec[0] <= 0 or
          (slot in slots and slots[slot] != spec)):
        raise ValueError('Conflicting Dom outer input storage declaration')
      slots[slot] = spec
    if set(slots) != set(range(len(cap.expected_input_info))):
      raise ValueError('Dom outer storage slots do not exactly cover captured inputs')
    inputs = []
    for slot in range(len(slots)):
      size, dtype, device = slots[slot]
      _view, variables, expected_dtype, expected_device = cap.expected_input_info[slot]
      if variables or dtype != expected_dtype or device != expected_device:
        raise ValueError('Dom input storage differs from captured dtype/device contract')
      buffer = buffer_factory(device, size, dtype).ensure_allocated()
      inputs.append(from_buffer(buffer))
    bindings[key] = inputs
  return bindings


def stage_qcom_uploads(raw, *, ops):
  from tinygrad.device import Buffer
  from tinygrad.uop.ops import UOp
  entries = []
  for call in raw.src:
    if call.body.op is ops.COPY and len(call.src) == 3:
      dst, src = call.src[1:]
      if str(src.device).split(':')[0] == 'QCOM' and str(dst.device).split(':')[0] == 'AMD':
        if (dst.dtype != src.dtype or dst.nbytes() != src.nbytes() or
            type(src.nbytes()) is not int or not 0 < src.nbytes() <= 16 << 20):
          raise ValueError('Invalid original camera upload extent')
        staging = UOp.from_buffer(Buffer('CPU', src.max_numel(), src.dtype).ensure_allocated())
        entries.extend((src.copy_to_device('CPU').call(staging, src),
                        staging.copy_to_device(dst.device).call(dst, staging)))
        continue
    entries.append(call)
  result = raw.replace(src=tuple(entries))
  if _gpu_payloads(raw, ops) != _gpu_payloads(result, ops):
    raise ValueError('Camera staging changed original GPU binaries')
  return result


def prepare_dom_captures(artifact, *, protection, ops, hcq_info_type, input_uops_by_capture):
  """Build guarded CPU submissions from existing compiled GPU programs only.

  Current HCQ relocation builds device program images from the original ELF;
  it does not retain PROGRAM nodes in the encoded CPU graph. Keep the exact
  original graph and its binary inventory rather than compare ELF bytes with
  their relocated device images. Native guard transformations independently
  preserve each encoded host's opaque device payloads.
  """
  from tinygrad.engine import realize
  from tinygrad.runtime.support import hcq2

  if not isinstance(artifact, dict) or hcq2.lower_and_compile is realize.lower_and_compile:
    raise ValueError('Current fresh host protection must precede Dom preparation')
  captures = _captures(artifact)
  if set(input_uops_by_capture) != set(captures):
    raise ValueError('Exact current storage bindings are required for every Dom capture')
  if any('linear' in cap.__dict__ for cap in captures.values()):
    raise ValueError('Dom capture was linked before guarded HCQ preparation')
  original_request = realize._get_call_to_compile

  def no_gpu_compile(call):
    request = original_request(call)
    if request is not None and request[1].target.device != 'CPU':
      raise ValueError('Dom compatibility forbids GPU recompilation')
    return request

  proofs = []
  realize._get_call_to_compile = no_gpu_compile
  try:
    for key, cap in captures.items():
      device = 'AMD' if key == 'run_policy' else 'QCOM'
      raw = cap._linear
      devices, before = _gpu_payloads(raw, ops)
      if devices != {device} or not before:
        raise ValueError('Original Dom capture execution device differs')
      inputs = input_uops_by_capture[key]
      if not isinstance(inputs, list) or len(inputs) != len(cap.expected_input_info):
        raise ValueError('Dom current storage binding count differs from its captured inputs')
      slots = {}
      for param in raw.toposort(enter_calls=False):
        if param.op is not ops.PARAM:
          continue
        spec = (param.arg.size, param.arg.dtype, param.arg.device)
        if (type(param.arg.slot) is not int or not 0 <= param.arg.slot < len(inputs) or
            (param.arg.slot in slots and slots[param.arg.slot] != spec)):
          raise ValueError('Conflicting Dom outer input storage declaration')
        slots[param.arg.slot] = spec
      for slot, (size, dtype, storage_device) in slots.items():
        current = inputs[slot]
        if (current.op is not ops.BUFFER or current.arg.buffer is None or
            (current.arg.size, current.arg.dtype, current.arg.device) != (size, dtype, storage_device)):
          raise ValueError('Dom storage binding differs from its exact captured input')
      start, fresh_start = len(protection.records), len(protection.fresh_records)
      flat = stage_qcom_uploads(flatten_dom_graphs(raw, ops=ops), ops=ops)
      prepared = hcq2.hcq_compile(flat, inputs, False, cache=False)
      report = _guarded_capture(prepared, device, ops, hcq_info_type)
      records = protection.records[start:]
      if (not records or any(record.get('gpu_payload_hashes_preserved') is not True for record in records) or
          not any(protection.fresh_records[fresh_start:])):
        raise ValueError('Missing fresh per-capture guarded host preparation')
      if device == 'AMD' and not any(record.get('usb_checked_calls', 0) > 0 for record in records):
        raise ValueError('Missing current AMD USB submission guard')
      if _gpu_payloads(raw, ops) != (devices, before):
        raise ValueError('Original Dom GPU binaries changed during host preparation')
      cap._linear = prepared
      linked = cap.linear
      if linked.op is not ops.LINEAR:
        raise ValueError('Unexpected linked Dom capture')
      report.update(device=device, gpu_binary_sha256_counts=dict(before), guard_records=records)
      proofs.append(_CaptureProof(cap, raw, prepared, linked, _json(report)))
  finally:
    realize._get_call_to_compile = original_request
  return _Preparation(artifact, tuple(proofs))


def normalize_admitted_dom(artifact, *, expected_contract, verified_source, preparation):
  """Expose the target interface while retaining honest original provenance."""
  source = _contract(expected_contract)
  if (not isinstance(verified_source, dict) or verified_source.get('sha256') != expected_contract['sha256'] or
      verified_source.get('bytes') != source['artifact_size'] or
      verified_source.get('adapter_sha256') != ADAPTER_SHA256 or
      not isinstance(verified_source.get('opcode_sha256'), str) or len(verified_source['opcode_sha256']) != 64 or
      any(c not in '0123456789abcdef' for c in verified_source['opcode_sha256'])):
    raise ValueError('Dom reconstruction source identity was not authenticated')
  if (not isinstance(artifact, dict) or artifact.get('format_version') != 1 or
      artifact.get('behavior_version') != expected_contract['version'] or
      'compiler_revision' in artifact or 'artifact_abi' in artifact or '_dom_admission' in artifact):
    raise ValueError('Unexpected source Dom artifact metadata')
  captures = _captures(artifact)
  if (type(preparation) is not _Preparation or preparation.artifact is not artifact or
      len(preparation.captures) != len(captures)):
    raise ValueError('Dom artifact lacks its current-process guarded preparation')
  reports = {}
  for proof, (key, cap) in zip(preparation.captures, captures.items(), strict=True):
    if (proof.capture is not cap or proof.prepared is not cap._linear or
        proof.linked is not cap.__dict__.get('linear')):
      raise ValueError('Dom capture changed after guarded preparation')
    reports[str(key)] = json.loads(proof.report_json)
  migration = {'schema_version': 1, 'source_format': 'tinygrad_single_v1', 'source_generation': 'v25',
               'source_compiler_revision': None, 'source_sha256': expected_contract['sha256'],
               'source_bytes': source['artifact_size'], 'source_opcode_sha256': verified_source['opcode_sha256'],
               'adapter_sha256': ADAPTER_SHA256, 'target_runtime_revision': TARGET_REVISION,
               'captures': reports}
  result = {**artifact, 'artifact_abi': DOM_ABI, 'execution_device': 'AMD', 'warp_device': 'QCOM', 'migration': migration}
  result['_dom_admission'] = _Admission(result, _json(expected_contract), _json(migration))
  return result


def require_admitted_dom(artifact, *, expected_contract):
  """Metadata copied from an admitted model cannot admit another artifact."""
  _contract(expected_contract)
  token = artifact.get('_dom_admission') if isinstance(artifact, dict) else None
  if (type(token) is not _Admission or token.artifact is not artifact or
      token.contract_json != _json(expected_contract) or token.migration_json != _json(artifact.get('migration')) or
      artifact.get('artifact_abi') != DOM_ABI or 'compiler_revision' in artifact or
      artifact.get('execution_device') != 'AMD' or artifact.get('warp_device') != 'QCOM'):
    raise ValueError('Dom model lacks its current-process guarded admission')
