"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

A kit a fork runs in its own CI, against its real adapter and modelds, and
the fakes jetlink's own tests share.

  check_adapter(module)  the adapter module: every side of Openpilot, a top
                         level of the standard library alone, an owner_config()
                         that imports nothing heavy, an in_control() that
                         answers a bool at once
  check_seam(path)       a modeld's main(): prepare() before it goes realtime,
                         attach() once the small model is built and its
                         result run, handed_over() read
  check_fallback(obj)    the fork's own hooks for no usable jetlink: each
                         answers as Off does, over HOOK_CALLS
  install_guard()        an audit hook a conftest starts and stops around each
                         test, which records a reach for the machine's own
                         params, /dev/shm records, sysfs or sudo
  FakeJoinedModel, FakeSubMaster, RecordingLog, and tinygrad over numpy
  (fake_tinygrad)

The checks return their problems, [] when there are none, so one assertion
shows them all: assertEqual(check_adapter(adapter_module), []).

Not on the owner's path, and no test framework or numpy at module level: a
fork imports this from its tests only.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from jetlink.openpilot.interface import Openpilot, conformance

# jetlink's own heavy half, which the owner must not reach: numpy, and urllib
# with ssl and email behind it
HEAVY_JETLINK = ('jetlink.client', 'jetlink.spec', 'jetlink.queues', 'jetlink.registry')

# -- the adapter module ----------------------------------------------------------

_PROBE = '''
import importlib, json, sys
before = set(sys.modules)
module = importlib.import_module(sys.argv[1])
found = {'import': sorted(set(sys.modules) - before)}
before = set(sys.modules)
try:
  config = module.owner_config()
  found['owner_config'] = sorted(set(sys.modules) - before)
  from jetlink.openpilot.interface import OwnerConfig
  found['config'] = isinstance(config, OwnerConfig)
except Exception as e:
  found['error'] = f'{type(e).__name__}: {e}'
print(json.dumps(found))
'''


def _foreign(names, own: str, heavy, allowed=()) -> list[str]:
  """What is neither the standard library nor jetlink's light half, nor of
  the fork's package `own`, nor `allowed`: by the outermost module of each."""
  found = {n for n in names if n not in allowed and (n.startswith((*heavy, *HEAVY_JETLINK)) or
                                                    n.split('.')[0] not in (*sys.stdlib_module_names, 'jetlink', own))}
  return sorted(n for n in found if not any(n.startswith(f + '.') for f in found))


def check_adapter(module: ModuleType, *, heavy: tuple[str, ...] = ()) -> list[str]:
  """The adapter module's problems. Its top level imports the standard
  library alone, as manager and the resident owner import it. owner_config()
  may import more: anything of the fork's own package that pulls in no third
  party, so 'openpilot.common.params' passes unless it is named in `heavy`. A
  fork should name its heavy modules there (params, swaglog, cereal). Both
  in a fresh interpreter on this one's path, so a module this process has
  loaded already counts."""
  name = module.__name__
  problems = [f"{name}.{member}(): missing" for member in ('adapter', 'owner_config')
              if not callable(getattr(module, member, None))]
  if problems:
    return problems
  # on this interpreter's path, as a fork's CI has set it up
  try:
    run = subprocess.run([sys.executable, '-c', _PROBE, name], capture_output=True, text=True, timeout=120,
                         env={**os.environ, 'PYTHONPATH': os.pathsep.join(p for p in sys.path if p)})
    # the probe's line is the last: the module may print as it imports
    found = json.loads(run.stdout.strip().splitlines()[-1])
  except subprocess.TimeoutExpired:
    return [f"{name} did not import and build its owner_config() in a fresh interpreter within 120 s"]
  except (IndexError, ValueError):
    return [f"{name} does not import in a fresh interpreter: {run.stderr.strip()[-500:]}"]
  # its own packages' __init__s, which run first
  if top := _foreign(found['import'], '', heavy, {name.rsplit('.', i)[0] for i in range(name.count('.') + 1)}):
    problems.append(f"{name}'s top level imports {top}: manager and the resident owner import it")
  if 'error' in found:
    problems.append(f"owner_config() raised {found['error']}")
  elif owner := _foreign(found['owner_config'], name.split('.')[0], heavy):
    problems.append(f"owner_config() imports {owner}: the resident owner calls it")
  if found.get('config') is False:
    problems.append("owner_config(): not an OwnerConfig")
  try:
    op = module.adapter()
  except Exception as e:
    return [*problems, f"adapter() raised {type(e).__name__}: {e}"]
  problems += [f"adapter: {p}" for p in conformance(op, Openpilot)]
  if not callable(getattr(op, 'in_control', None)):
    return problems
  try:
    op.in_control()   # the first may set up what it reads
    start = time.monotonic()
    answer, took = op.in_control(), time.monotonic() - start
  except Exception as e:
    return [*problems, f"in_control() raised {type(e).__name__}: {e}"]
  if not isinstance(answer, bool):
    problems.append(f"in_control() answered {answer!r}, not a bool")
  if took > 0.05:
    problems.append(f"in_control() took {took * 1e3:.0f} ms: modeld asks before every frame")
  return problems


# -- the modeld seam ---------------------------------------------------------------

def check_seam(modeld_path: str | Path) -> list[str]:
  """The problems of a modeld's main() with the hooks, read off its source
  (importing a modeld costs tinygrad and a vision stream). The hooks are
  found as the receiver of attach(model, small, cam_w, cam_h): the fork's
  module (jetlink_adapter.attach) or its Hooks (HOOKS.attach) alike. Calls
  are ordered as the source reads, so one in a branch that never runs (an
  `if False:`) counts as made. state() is the fork's to publish where it has
  a message for it, so it is not asked for."""
  path = Path(modeld_path)
  main = next((n for n in ast.parse(path.read_text(), filename=str(path)).body
               if isinstance(n, ast.FunctionDef) and n.name == 'main'), None)
  if main is None:
    return [f"{path.name}: no main()"]

  def at(n: ast.AST) -> tuple[int, int]:
    return n.lineno, n.col_offset
  nodes = list(ast.walk(main))
  calls = sorted((n for n in nodes if isinstance(n, ast.Call)), key=at)

  def called(name: str, facade: str | None = None) -> list[tuple[int, int]]:
    return [at(c) for c in calls if (c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, 'id', None)) == name
            and (facade is None or isinstance(c.func, ast.Attribute) and ast.unparse(c.func.value) == facade)]

  def stored(target: str) -> list[tuple[int, int]]:
    return [at(n) for n in nodes if isinstance(getattr(n, 'ctx', None), ast.Store) and ast.unparse(n) == target]

  attach = next((c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == 'attach'
                 and len(c.args) + len(c.keywords) == 4), None)
  if attach is None:
    return [f"{path.name}: main() never calls attach(model, small, cam_w, cam_h)"]
  facade = ast.unparse(attach.func.value)
  problems, prepare, realtime = [], called('prepare', facade), called('config_realtime_process')
  if not prepare:
    problems.append(f"main() never calls {facade}.prepare()")
  elif realtime and prepare[0] >= realtime[0]:
    problems.append("prepare() comes after config_realtime_process(): the GPU's thread would inherit the realtime core")
  elif prepare[0] >= at(attach):
    problems.append("attach() comes before prepare(), so the link never joins")
  if not realtime:
    problems.append("main() never calls config_realtime_process()")
  small = attach.args[1] if len(attach.args) > 1 else next((k.value for k in attach.keywords if k.arg == 'small'), None)
  if isinstance(small, ast.Name) and not any(p < at(attach) for p in stored(small.id)):
    problems.append(f"attach() comes before {small.id} is built")
  # the name its result is bound to, which the frame loop must run
  bound = next((t.id for n in nodes if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is attach
                for t in getattr(n, 'targets', [getattr(n, 'target', None)]) if isinstance(t, ast.Name)), None)
  if bound is None:
    problems.append("attach()'s result is dropped: modeld would never run the joined model")
  elif not any(p > at(attach) for p in called('run', bound)):
    problems.append(f"attach()'s result, {bound}, is never run: modeld would keep driving the model it had")
  elif not any(p > at(attach) for p in stored(f'{bound}.frame_drop_ratio')):
    problems.append(f"main() never writes {bound}.frame_drop_ratio: a host that drops camera frames would never hand back")
  if not called('handed_over', facade):
    problems.append(f"main() never calls {facade}.handed_over(model)")
  return [f"{path.name}: {p}" for p in problems]


# -- the fork's fallback ------------------------------------------------------------------

_MODEL = object()
# every hook a fork's lines may call, and the arguments each is checked with:
# the one list of them (jetlink's tests hold Hooks to it)
HOOK_CALLS = {'should_run': [(False, None, None)], 'status': [()], 'reason': [()], 'prepare': [(False,), (True,)],
              'attach': [(_MODEL, object(), 1928, 1208)], 'handed_over': [(_MODEL,)], 'state': [(_MODEL,)],
              'shutdown_step': [(False,), (True, 'test')], 'request_shutdown': [('test',)], 'shutdown_pending': [()],
              'model_state': [('f' * 40,)], 'lists_big_models': [()], 'big_catalog': [(21,)], 'listed_ref': [('f' * 40,)],
              'should_extend_catalog': [()], 'extend_catalog': [({'bundles': []},)],
              'runtime': [()], 'swap_events': [(0.01,)]}


def check_fallback(fallback, hooks: Iterable[str] | None = None, *, link_on=None) -> list[str]:
  """The problems of the fork's own hooks for a jetlink that is missing or
  too old to have Off: each of `hooks`, the ones the fork's lines call (by
  default every hook it has, so pass them to have a missing one named),
  there and answering as Off does, the link off. reason() is the fork's to
  word, a str or None, and None while `link_on()`, the fork's read of the
  setting, says off: nobody who left the link off is nagged. swap_events()
  may be None where the fork's lines check for it. link_on() reads the
  params store, so the test sets PARAMS_ROOT or OPENPILOT_PREFIX first, or
  it reads the machine's own."""
  from jetlink.openpilot.events import Flags
  from jetlink.openpilot.hooks import Off

  def flags(tracker):
    found = tracker.update(True, False, True, state='ready')
    return tuple(bool(getattr(found, f)) for f in Flags._fields), bool(tracker.settling)
  problems = []
  for name in hooks if hooks is not None else [n for n in HOOK_CALLS if hasattr(fallback, n)]:
    if name not in HOOK_CALLS or not callable(getattr(fallback, name, None)):
      problems.append(f"{name}(): missing" if name in HOOK_CALLS else f"{name}(): not a hook")
      continue
    for args in HOOK_CALLS[name]:
      try:
        got, want = getattr(fallback, name)(*args), getattr(Off(), name)(*args)
        if name == 'swap_events':
          got, want = (want, want) if got is None else (flags(got), flags(want))
      except Exception as e:
        problems.append(f"{name}() raised {type(e).__name__}: {e}")
        continue
      if name == 'reason':
        try:
          off = link_on is not None and not link_on()
        except Exception as e:
          problems.append(f"link_on() raised {type(e).__name__}: {e}")
          continue
        same = got is None or (isinstance(got, str) and not off)
      else:
        same = got is want if any(want is a for a in args) else (type(got), got) == (type(want), want)
      if not same:
        problems.append(f"{name}() answered {got!r}, where the link off is {want!r}")
  return problems


# -- the guard ------------------------------------------------------------------------

# the machine's own params store, jetlink's records, sysfs, FunctionFS and a
# PC's openpilot params
FORBIDDEN = ('/data/', '/dev/shm/', '/dev/ffs', '/sys/', str(Path.home() / '.comma') + os.sep)
# read by a library on import, never jetlink's own reach: tinygrad's CPU quota
ALLOWED = ('/sys/fs/cgroup/cpu.max',)
# the audit events Guard reads a path from, and the argument it is
_PATH_ARG = {'open': 0, 'os.listdir': 0, 'os.scandir': 0, 'socket.connect': 1}


class Guard:
  """Records, between start() and stop(), every open, listing, socket connect
  or root-script run that reaches a forbidden path. Recorded, not refused:
  many of the paths are read inside `except OSError`, so a refusal would only
  hide the reach. A test that reaches the live params store can clear a
  real setting such as JetlinkSpec."""

  def __init__(self, forbidden: tuple[str, ...] = FORBIDDEN):
    self.forbidden = tuple(forbidden)
    self.touched: list[str] | None = None

  def start(self) -> None:
    self.touched = []

  def stop(self) -> list[str]:
    """What was reached since start(), once each."""
    touched, self.touched = self.touched or [], None
    return sorted(set(touched))

  def __call__(self, event: str, args) -> None:
    touched = self.touched
    if touched is None:
      return
    if event == 'subprocess.Popen':
      # the root script is sudo on a comma
      argv = args[1] if len(args) > 1 and isinstance(args[1], (list, tuple)) else []
      if (hit := next((str(a) for a in argv if 'jetlink-root.sh' in str(a) or str(a) == 'sudo'), None)) is not None:
        touched.append(f"{event} {hit}")
      return
    index = _PATH_ARG.get(event)
    value = args[index] if index is not None and len(args) > index else None
    if isinstance(value, (str, bytes, os.PathLike)):
      path = os.path.abspath(os.fsdecode(value))
      if (path + os.sep).startswith(self.forbidden) and path not in ALLOWED:
        touched.append(f"{event} {path}")


def install_guard(forbidden: tuple[str, ...] = FORBIDDEN) -> Guard:
  """A Guard, installed as this process's audit hook (for good: Python has no
  removing one). Install once, in a conftest, and start and stop it around
  each test."""
  guard = Guard(forbidden)
  sys.addaudithook(guard)
  return guard


# -- fakes ---------------------------------------------------------------------------

class FakeJoinedModel:
  """What attach() returns, for a fork's modeld tests: drives as `small`;
  hand_over() is a swap or a hand-back."""

  def __init__(self, small=None, *, state: str = 'joining', chestnut: bool = False, constants=None):
    self.small = small
    self.handovers = 0
    self.big_model_state = state
    self.chestnut = chestnut
    self.constants = constants
    self.LAT_SMOOTH_SECONDS = self.LONG_SMOOTH_SECONDS = self.frame_drop_ratio = self.lat_delay = 0.0
    self.runs, self.closed = 0, False

  def hand_over(self, state: str = 'running') -> None:
    self.handovers += 1
    self.big_model_state = state

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    self.runs += 1
    return self.small.run(bufs, transforms, inputs, after_enqueue) if self.small is not None else {}

  def get_action_from_model(self, *args):
    return ('action', args)

  def close(self) -> None:
    self.closed = True


class _Blank:
  """A message never received, as cereal's SubMaster holds one: every field
  reads falsy, a nested one too."""

  def __getattr__(self, name: str):
    if name.startswith('__'):
      raise AttributeError(name)
    return self

  def __bool__(self) -> bool:
    return False


class FakeSubMaster:
  """cereal's SubMaster over fixed messages, for an adapter's engaged() and
  in_control() tests: what Stock and StarPilot's adapter read of it. A
  service given a message is seen, alive and valid; any other is none of
  them until update() receives it, and reads as a blank message whose every
  field is falsy. A test changes `data`, `alive` and `valid` as it goes.
  update() keeps its timeouts: in_control() is on modeld's frame thread and
  must never wait (all 0). In for the real one: mock.patch.object(messaging,
  'SubMaster', lambda services: FakeSubMaster(services, **messages)). Unlike
  cereal's: a service is seen from construction, not from its first receive;
  a blank field is falsy but equals nothing (a capnp default equals 0 or
  False); and there is no updated, frame, freq_ok or logMonoTime."""

  def __init__(self, services: Iterable[str] = (), **messages):
    self.services = list(dict.fromkeys([*services, *messages]))
    self.data = dict(messages)
    self.seen = {s: s in messages for s in self.services}
    self.alive = dict(self.seen)
    self.valid = dict(self.seen)
    self.timeouts: list[float] = []

  def update(self, timeout: float = 100) -> None:
    self.timeouts.append(timeout)
    for s in self.services:
      self.seen[s] = self.seen[s] or (s in self.data and self.alive[s])

  def all_alive(self, service_list=None) -> bool:
    return all(self.alive[s] for s in service_list or self.services)

  def all_valid(self, service_list=None) -> bool:
    return all(self.valid[s] for s in service_list or self.services)

  def __getitem__(self, service: str):
    if service not in self.seen:
      raise KeyError(service)   # not subscribed, as cereal's
    return self.data.get(service, _BLANK)


_BLANK = _Blank()


def _level(name: str):
  def log(self, msg, *args, **kwargs):
    try:
      text = str(msg) % args if args else str(msg)
    except (TypeError, ValueError):
      text = f"{msg} {args}"
    self.records.append((name, text))
  return log


class RecordingLog:
  """cloudlog's face, keeping every line: (level, message with its arguments)."""
  debug, info, warning, error, exception, critical = map(_level, ('debug', 'info', 'warning', 'error', 'exception', 'critical'))

  def __init__(self):
    self.records: list[tuple[str, str]] = []

  def lines(self, level: str | None = None) -> list[str]:
    return [text for lvl, text in self.records if level is None or lvl == level]

  def has(self, fragment: str, level: str | None = None) -> bool:
    return any(fragment in text for text in self.lines(level))


# The fork's tinygrad is not jetlink's to install. What jetlink calls of it is
# small, and these stand-ins do it with numpy; the fork's tests run the real one.

class FakeTensor:
  def __init__(self, data=None, device=None, dtype=None):
    import numpy as np
    self.array = np.asarray(data) if data is not None else np.zeros(0)
    self.device = device

  def realize(self, *others):
    return self

  def assign(self, value):
    self.array[...] = value
    return self

  def numpy(self):
    return self.array


class FakeDevice:
  DEFAULT = 'CPU'


def fake_jit(fn=None, prune=False):
  """TinyJit as a decorator or a wrapper: the function as it is."""
  return (lambda f: f) if fn is None else fn


def fake_tinygrad() -> dict[str, ModuleType]:
  """sys.modules entries for what jetlink imports of tinygrad; patch them in
  with mock.patch.dict(sys.modules, fake_tinygrad())."""
  from types import SimpleNamespace

  def module(name, **attrs):
    m = ModuleType(name)
    m.__dict__.update(attrs)
    return m
  return {
    'tinygrad': module('tinygrad', Tensor=FakeTensor, TinyJit=fake_jit, Device=FakeDevice),
    'tinygrad.tensor': module('tinygrad.tensor', Tensor=FakeTensor),
    'tinygrad.device': module('tinygrad.device', Device=FakeDevice),
    'tinygrad.helpers': module('tinygrad.helpers', PARALLEL=SimpleNamespace(value=8)),
    'tinygrad.engine': module('tinygrad.engine'),
    'tinygrad.engine.jit': module('tinygrad.engine.jit', TinyJit=fake_jit),
  }
