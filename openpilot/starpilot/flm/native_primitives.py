"""Source-bound optional primitive acceleration in the bounded fitter only."""
from functools import cache
import hashlib
import importlib.util
from importlib.machinery import ExtensionFileLoader
import json
from pathlib import Path
import platform
import struct
import sys
import sysconfig
from typing import cast

HERE = Path(__file__).resolve().parent
REVISION = 'flm-exact-primitives-v1'
FALLBACK = object()
_enabled = False


def source_paths():
  return {'_primitive_walk.pyx': HERE / '_primitive_walk.pyx', 'native_primitives.py': HERE / 'native_primitives.py',
          'SConscript': HERE / 'SConscript', 'SConstruct': HERE.parents[2] / 'SConstruct'}


def runtime_abi():
  return {'implementation': sys.implementation.name, 'cache_tag': sys.implementation.cache_tag,
          'python': list(sys.version_info[:2]), 'SOABI': sysconfig.get_config_var('SOABI'),
          'machine': platform.machine(), 'pointer_bits': struct.calcsize('P') * 8, 'platform': sys.platform}


def _digest(path, maximum):
  try:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
      return None
    return hashlib.sha256(path.read_bytes()).hexdigest()
  except OSError:
    return None


@cache
def native_identity():
  sources = {name: _digest(path, 2 * 1024 * 1024) for name, path in source_paths().items()}
  artifact, receipt = HERE / '_primitive_walk.so', HERE / '_primitive_walk.binding.json'
  binary_hash = _digest(artifact, 32 * 1024 * 1024)
  receipt_hash = _digest(receipt, 65536)
  result = {'revision': REVISION, 'sources': sources, 'artifact_sha256': binary_hash,
            'receipt_sha256': receipt_hash, 'abi': runtime_abi(), 'status': 'python-missing'}
  if artifact.exists() or receipt.exists():
    result['status'] = 'python-invalid-binding'
    if binary_hash is not None and receipt_hash is not None and all(sources.values()):
      try:
        binding = json.loads(receipt.read_text())
        if (type(binding) is dict and binding.get('schema') == 1 and binding.get('revision') == REVISION and
            binding.get('sources') == sources and binding.get('artifact_sha256') == binary_hash):
          result['status'] = 'python-unsupported-abi'
          if binding.get('abi') == result['abi'] and sys.platform.startswith('linux'):
            result['status'] = 'native-bound'
      except (OSError, ValueError, TypeError):
        pass
  return json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False)


def enable_fit_worker():
  # The idle class is below CFS; both retain the same CPU/AS/nice bounds.
  import os
  import resource
  if not sys.platform.startswith('linux'):
    return
  cpu = resource.getrlimit(resource.RLIMIT_CPU)[0]
  memory = resource.getrlimit(resource.RLIMIT_AS)[0]
  if (not 0 < cpu <= 20 or not 0 < memory <= 2 * 1024 * 1024 * 1024 or
      os.getpriority(os.PRIO_PROCESS, 0) < 10 or os.sched_getscheduler(0) not in (os.SCHED_OTHER, os.SCHED_IDLE)):
    raise ValueError('FLM native primitive worker context')
  global _enabled
  _enabled = True


@cache
def _extension():
  identity = json.loads(native_identity())
  if identity['status'] != 'native-bound':
    return None
  path = HERE / '_primitive_walk.so'
  expected = identity['artifact_sha256']
  if _digest(path, 32 * 1024 * 1024) != expected:
    raise ValueError('FLM native primitive artifact changed')
  name = cast(str, __package__) + '._primitive_walk'
  if name in sys.modules:
    raise ValueError('FLM native primitive unowned preloaded module')
  spec = importlib.util.spec_from_file_location(name, path)
  if spec is None or spec.loader is None:
    raise ValueError('FLM native primitive module missing')
  try:
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
  except Exception as error:
    sys.modules.pop(name, None)
    raise ValueError('FLM native primitive import failed') from error
  spec = getattr(module, '__spec__', None)
  if (spec is None or not isinstance(spec.loader, ExtensionFileLoader) or
      Path(spec.origin).resolve() != path.resolve() or Path(cast(str, module.__file__)).resolve() != path.resolve() or path.is_symlink() or
      _digest(path, 32 * 1024 * 1024) != expected or
      module.compiled_abi() != (sys.version_info.major, sys.version_info.minor, struct.calcsize('P') * 8, REVISION)):
    raise ValueError('FLM native primitive executable binding')
  return module


def try_native(operation, value):
  if not _enabled:
    return FALLBACK
  module = _extension()
  if module is None:
    return FALLBACK
  result = getattr(module, operation)(value)
  return FALLBACK if result is module.FALLBACK else result


def enable_response_worker(purpose: str) -> bool:
  """Enable exact primitives only in the explicitly bounded production child.

  This verifies execution context, not native availability or model authority.
  The unchanged extension loader still requires the exact fresh source/ABI/SO
  receipt; a qualified context with no valid receipt keeps Python fallback.
  """
  import ctypes
  from importlib.machinery import ModuleSpec, SourceFileLoader
  import os
  import resource
  import signal
  from types import ModuleType

  module_name = 'openpilot.starpilot.flm.live_tracking_response_worker'
  worker_path = HERE / 'live_tracking_response_worker.py'
  if (type(purpose) is not str or purpose != 'live-response-bootstrap-v1' or
      not sys.platform.startswith('linux')):
    raise ValueError('FLM native response purpose/platform')
  main = sys.modules.get('__main__')
  if type(main) is not ModuleType:
    raise ValueError('FLM native response main module')
  namespace = vars(main)
  spec, main_file = namespace.get('__spec__'), namespace.get('__file__')
  original = getattr(sys, 'orig_argv', None)
  if (type(original) is not list or any(type(value) is not str for value in original) or
      tuple(original[1:]) != ('-B', '-m', module_name) or
      type(namespace.get('__package__')) is not str or namespace['__package__'] != 'openpilot.starpilot.flm' or
      type(spec) is not ModuleSpec or type(spec.name) is not str or spec.name != module_name or
      type(spec.loader) is not SourceFileLoader or
      type(spec.origin) is not str or type(main_file) is not str or
      worker_path.is_symlink() or not worker_path.is_file() or
      Path(spec.origin).resolve() != worker_path.resolve() or Path(main_file).resolve() != worker_path.resolve()):
    raise ValueError('FLM native response executable context')
  expected = {'PURPOSE': purpose, 'MAX_CPU': 180, 'MAX_AS': 2*1024*1024*1024, 'MAX_WALL_NS': 240_000_000_000}
  if any(type(namespace.get(name)) is not type(value) or namespace[name] != value for name, value in expected.items()):
    raise ValueError('FLM native response fixed policy')
  cpu = resource.getrlimit(resource.RLIMIT_CPU)[0]
  memory = resource.getrlimit(resource.RLIMIT_AS)[0]
  parent = os.environ.get('STARPILOT_FLM_PARENT_PID', '')
  if (not 0 < cpu <= 180 or not 0 < memory <= 2*1024*1024*1024 or
      os.getpriority(os.PRIO_PROCESS, 0) < 10 or os.sched_getscheduler(0) != os.SCHED_OTHER or
      any(os.environ.get(name) != '1' for name in
        ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')) or
      not parent.isascii() or not parent.isdecimal() or not 1 < int(parent) < 2**31 or os.getppid() != int(parent)):
    raise ValueError('FLM native response bounded worker context')
  death_signal = ctypes.c_int()
  if ctypes.CDLL(None).prctl(2, ctypes.byref(death_signal), 0, 0, 0) != 0 or death_signal.value != signal.SIGKILL:
    raise ValueError('FLM native response parent death signal')
  global _enabled
  _enabled = True
  return True
