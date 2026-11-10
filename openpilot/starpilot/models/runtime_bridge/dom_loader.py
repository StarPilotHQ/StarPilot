"""Authenticate original Dom bytes and admit only current guarded HCQ hosts."""
import hashlib
import os
from pathlib import Path
import pickle
import stat
import struct
from unittest.mock import patch


_SOURCES = {'adapter.py': '36ba945bbbf2d954474de5499f791804ecec3a6cf2b52e92c1661bb9b2f4788c',
 'ops-mapping.json': '8acf8d759ffd597684fce9e0879b488cf2b32c3f05a5ca355a77ba0026c6bb95',
 'dom_admission.py': '45ca0ad26271b6606af258af040ded846b19b74f693f837dd802fa71b9e9a284',
 'dom_usb.py': 'f57983bac65f61873d67fa644e9eedde6eecec9d026130c31d8e55efe2185c0d',
 'qcom.py': 'da93aa4b1929fbb881056669a78e765d113a2ace2d766e1cf7cce40ddba8ab95'}


def verify_dom_sources():
  root = Path(__file__).parent
  for name, expected in _SOURCES.items():
    path = root / name
    if path.is_symlink() or not path.is_file():
      raise ValueError('Missing reviewed Dom source: ' + name)
    with path.open('rb') as stream:
      if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
        raise ValueError('Reviewed Dom source changed: ' + name)


def load_admitted_dom(path: Path, *, expected_contract: dict, protection):
  verify_dom_sources()
  from openpilot.starpilot.models.runtime_bridge import runtime
  from openpilot.starpilot.models.catalog import COMPILER_REVISION as LEGACY_REVISION
  request = runtime.frozen_laboratory_request()
  if not runtime.selected_legacy() or request is None:
    raise ValueError('Original Dom source requires the frozen legacy runtime')
  if (not isinstance(expected_contract, dict) or str(path) != expected_contract.get('path') or
      any(parent.is_symlink() for parent in (path, *path.parents))):
    raise ValueError('Original Dom source path differs from frozen role')
  roles = request['roles']
  if sum(role == expected_contract for role in roles.values()) != 1 or protection is not runtime._protection:
    raise ValueError('Original Dom source/protection was not selected by the runtime')
  source = expected_contract['source_contract']
  if source.get('target_runtime_revision') != LEGACY_REVISION or source.get('source_compiler_revision', 'missing') is not None:
    raise ValueError('Original Dom source/runtime contract changed')
  fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
  with os.fdopen(fd, 'rb') as stream:
    before = os.fstat(stream.fileno())
    def identity(value):
      return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    def unchanged():
      named = path.lstat()
      if (not stat.S_ISREG(named.st_mode) or identity(before) != identity(named) or
          identity(before) != identity(os.fstat(stream.fileno()))):
        raise ValueError('Original Dom source changed during reconstruction')
    if (not stat.S_ISREG(before.st_mode) or type(source.get('artifact_size')) is not int or
        not 0 < before.st_size == source['artifact_size'] <= 2 * 1024**3 or
        hashlib.file_digest(stream, 'sha256').hexdigest() != expected_contract['sha256']):
      raise ValueError('Original Dom complete artifact identity mismatch')
    unchanged()
    stream.seek(0)
    header = stream.read(8)
    if len(header) != 8:
      raise ValueError('Missing original Dom envelope')
    length = struct.unpack('<q', header)[0]
    if not 0 < length <= min(64 << 20, before.st_size - 8):
      raise ValueError('Invalid original Dom opcode length')
    opcodes = stream.read(length)
    if len(opcodes) != length:
      raise ValueError('Truncated original Dom opcodes')
    opcode_sha256 = hashlib.sha256(opcodes).hexdigest()
    from openpilot.starpilot.models.runtime_bridge import adapter, dom_admission
    from tinygrad import Context
    from tinygrad.device import Buffer
    from tinygrad.uop.ops import UOp
    from tinygrad.engine import realize
    from tinygrad.runtime.support.hcq2 import HCQInfo
    from tinygrad.uop import Ops
    original_request = realize._get_call_to_compile
    def deny_gpu_compile(call):
      value = original_request(call)
      if value is not None and value[1].target.device != 'CPU':
        raise ValueError('Dom reconstruction cannot compile a GPU program')
      return value
    def buffers():
      previous = None
      while header := stream.read(8):
        if len(header) != 8:
          raise ValueError('Truncated original Dom buffer header')
        size = struct.unpack('<q', header)[0]
        if not 0 <= size <= before.st_size - stream.tell():
          raise ValueError('Invalid original Dom buffer length')
        if previous is not None:
          previous.release()
        data = bytearray(size)
        if stream.readinto(data) != size:
          raise ValueError('Truncated original Dom buffer')
        unchanged()
        previous = pickle.PickleBuffer(data)
        yield previous
    with Context(DEV='USB+AMD:LLVM'), patch.object(realize, '_get_call_to_compile', deny_gpu_compile):
      reader = adapter.MigrationUnpickler(opcodes, buffers=buffers(), target=adapter.selected9d_target(),
                                         expected_opcode_sha256=opcode_sha256)
      artifact = reader.load()
      if stream.read(1):
        raise ValueError('Unconsumed original Dom source payload')
      unchanged()
      bindings = dom_admission.create_dom_storage_bindings(artifact, ops=Ops, buffer_factory=Buffer, from_buffer=UOp.from_buffer)
      preparation = dom_admission.prepare_dom_captures(artifact, protection=protection, ops=Ops, hcq_info_type=HCQInfo,
                                                       input_uops_by_capture=bindings)
      unchanged()
      admitted = dom_admission.normalize_admitted_dom(artifact, expected_contract=expected_contract,
        verified_source={'sha256': expected_contract['sha256'], 'bytes': before.st_size,
                         'opcode_sha256': opcode_sha256, 'adapter_sha256': dom_admission.ADAPTER_SHA256},
        preparation=preparation)
      unchanged()
      return admitted
