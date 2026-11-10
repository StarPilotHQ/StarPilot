"""Exercise the actual nested GPU loader without constructing a GPU context."""
import ast
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest


SOURCE = Path(__file__).resolve().parents[3] / 'selfdrive/modeld/modeld.py'


def loader(source, *, demo=False, bundled=False, failure=None):
  tree = ast.parse(source.read_text())
  main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
  function = next(n for n in ast.walk(main) if isinstance(n, ast.FunctionDef) and n.name == 'load_big')
  calls = []

  def record(name, result=None):
    def run(*args, **kwargs):
      calls.append((name, args, threading.get_ident()))
      if name == failure:
        raise OSError(name)
      return result
    return run

  model = SimpleNamespace(warmup=record('warmup'))
  namespace = {'laboratory_request': None, 'demo': demo, 'CP': object(), 'BIG_MODEL_TIMEOUT': 120, 'wait_for_chestnut': record('usb'),
                   'wait_for_chestnut_power': record('power'), 'set_core_affinity': record('affinity'),
                   'selection': SimpleNamespace(big_path=None if bundled else Path('/verified.pkl'), big_version='v16',
                                             big_id='mountaindewv3', big_sha256='a' * 64),
                   'cam_w': 1928, 'cam_h': 1208, 'big_path': Path('/verified.pkl'),
                   'load_verified_model': record('verified', (model, 'prepared')), 'ModelState': record('bundled', model),
                   'receipt_owner': SimpleNamespace(prepare=record('receipt', 'prepared')),
                   'big_artifact_identity': object(), 'cloudlog': SimpleNamespace(exception=record('error'))}
  outer = ast.parse('def outer():\n big_model = None\n big_prepared = None\n return load_big, lambda: (big_model, big_prepared)\n').body[0]
  outer.body.insert(2, function)
  module = ast.fix_missing_locations(ast.Module(body=[outer], type_ignores=[]))
  exec(compile(module, str(source), 'exec'), namespace)
  outer_function = namespace['outer']
  assert callable(outer_function)
  loaded = outer_function()
  assert isinstance(loaded, tuple) and len(loaded) == 2
  callback, state = loaded
  assert callable(callback) and callable(state)
  return callback, state, calls


@pytest.mark.parametrize('demo,expected', [(False, ['usb', 'power', 'affinity', 'verified']),
                                          (True, ['usb', 'affinity', 'verified'])])
def test_actual_gpu_loader_pins_after_readiness_before_verified_load(demo, expected):
  callback, state, calls = loader(SOURCE, demo=demo)
  callback()
  assert [row[0] for row in calls] == expected
  assert next(row[1] for row in calls if row[0] == 'affinity') == ([7],)
  assert state()[1] == 'prepared'


@pytest.mark.parametrize('failure,expected', [('power', ['usb', 'power', 'error']),
                                             ('affinity', ['usb', 'power', 'affinity', 'error'])])
def test_failed_readiness_or_affinity_never_loads_a_model(failure, expected):
  callback, state, calls = loader(SOURCE, failure=failure)
  callback()
  assert [row[0] for row in calls] == expected
  assert state() == (None, None)


def test_bundled_gpu_loader_keeps_warmup_and_receipt():
  callback, state, calls = loader(SOURCE, bundled=True)
  callback()
  assert [row[0] for row in calls] == ['usb', 'power', 'affinity', 'bundled', 'warmup', 'receipt']
  assert state()[1] == 'prepared'


def test_affinity_is_applied_to_the_loader_thread_only():
  callback, state, calls = loader(SOURCE)
  main_thread = threading.get_ident()
  worker = threading.Thread(target=callback)
  worker.start()
  worker.join(timeout=2)
  assert not worker.is_alive()
  assert state()[1] == 'prepared'
  assert all(row[2] == worker.ident and row[2] != main_thread for row in calls)


def test_no_fifo_priority_change_inside_gpu_loader():
  tree = ast.parse(SOURCE.read_text())
  main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
  callback = next(n for n in ast.walk(main) if isinstance(n, ast.FunctionDef) and n.name == 'load_big')
  assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'config_realtime_process'
                 for n in ast.walk(callback))
  assert sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'config_realtime_process'
             for n in ast.walk(main)) == 1
