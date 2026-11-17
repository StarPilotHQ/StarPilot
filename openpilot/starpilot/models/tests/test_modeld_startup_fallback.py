"""CPU-only regressions for modeld startup scheduling and runner-owned fallback."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jetlink.openpilot.testing import FakeJoinedModel
from openpilot.starpilot.models.status import ModelVariant


MODEL_SOURCE = Path(__file__).resolve().parents[3] / "selfdrive/modeld/modeld.py"


def main_body():
  return next(node for node in ast.parse(MODEL_SOURCE.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "main").body


def test_realtime_starts_after_model_load_and_receipt():
  # Inspect startup without importing modeld's GPU runtime or loading artifacts.
  calls = [(node.lineno, ast.unparse(node.func)) for statement in main_body() for node in ast.walk(statement) if isinstance(node, ast.Call)]
  def line(name):
    return [lineno for lineno, function in calls if function == name]
  assert len(line("gc.disable")) == len(line("config_realtime_process")) == 1
  assert line("gc.disable")[0] < min(line("load_small") + line("loader.join"))
  startup_receipt = next(statement for statement in ast.walk(ast.Module(body=main_body(), type_ignores=[])) if isinstance(statement, ast.Expr)
                         and isinstance(statement.value, ast.Call) and ast.unparse(statement.value.func) == "receipt_owner.loaded")
  assert max(line("load_small") + line("loader.join")) < startup_receipt.lineno < line("config_realtime_process")[0] < line("PubMaster")[0]


def test_jetlink_gets_the_frames_delay_before_every_run():
  # a model that joins starts on the current delay; check_seam holds the frame_drop_ratio write
  loop = [node for node in main_body() if isinstance(node, ast.While)][-1]  # the frame loop
  statements = [node for node in ast.walk(loop) if isinstance(node, (ast.Assign, ast.Try))]
  def line(source):
    return next(node.lineno for node in statements if isinstance(node, ast.Assign) and ast.unparse(node) == source)
  run = next(node.lineno for node in statements if isinstance(node, ast.Try) and
             any(isinstance(child, ast.Call) and ast.unparse(child.func) == "model.run" for child in ast.walk(node)))
  assert (line("subaru_tau = subaru_smooth_seconds(v_ego, subaru_smoothing)") <
          line("lat_delay = sm['lateralDelay'].lateralDelay + LAT_SMOOTH_SECONDS + subaru_tau") <
          line("model.lat_delay = lat_delay") < run)
  assignments = {ast.unparse(node.targets[0]): node for node in statements if isinstance(node, ast.Assign)}
  # Reuse the same joined runner across frames: each write must include the current delay and smoothing.
  model = SimpleNamespace(lat_delay=-1.)
  for delay, tau in ((.1, 0.), (.25, .4), (.05, 2.)):
    env = {"sm": {"lateralDelay": SimpleNamespace(lateralDelay=delay)}, "LAT_SMOOTH_SECONDS": .03,
           "subaru_tau": tau, "model": model}
    code = ast.Module(body=[assignments["lat_delay"], assignments["model.lat_delay"]], type_ignores=[])
    exec(compile(ast.fix_missing_locations(code), str(MODEL_SOURCE), "exec"), env)
    assert model.lat_delay == pytest.approx(delay + .03 + tau)


@pytest.mark.parametrize("runner_chestnut,stored_active", [(True, False), (True, True), (False, True), (False, False)])
def test_inference_failure_uses_runner_identity(runner_chestnut, stored_active):
  # Run the production try/except statement with deliberately stale status Params.
  fallback = next(node for statement in main_body() for node in ast.walk(statement)
                  if isinstance(node, ast.Try) and any(isinstance(child, ast.Call) and ast.unparse(child.func) == "model.run"
                                                       for child in ast.walk(node)))
  failure = RuntimeError("inference failed")
  failed_model = SimpleNamespace(chestnut=runner_chestnut, run=Mock(side_effect=failure))
  small_model = SimpleNamespace(chestnut=False, model_id="fallback-small")
  params = Mock()
  params.get_bool.return_value = stored_active
  receipt = Mock()
  chestnut_state = SimpleNamespace(big=True)
  env: dict = {"model": failed_model, "small_model": small_model, "params": params, "receipt_owner": receipt,
             "chestnut_state": chestnut_state, "run_count": 1, "ModelConstants": SimpleNamespace(MODEL_RUN_FREQ=20),
             "SERVICE_LIST": {"chestnutGpuState": SimpleNamespace(frequency=1)}, "bufs": {}, "transforms": {}, "inputs": {},
             "small_prepared": object(), "ModelVariant": ModelVariant, "jetlink_model": None, "drop_chestnut": Mock(), "cloudlog": Mock()}
  code = compile(ast.fix_missing_locations(ast.Module(body=[fallback], type_ignores=[])), str(MODEL_SOURCE), "exec")
  if runner_chestnut:
    exec(code, env)
    assert env["model"] is small_model
    assert env["model_output"] is None
    assert env["run_count"] == 0
    assert not chestnut_state.big
    env["drop_chestnut"].assert_called_once_with()
    params.put_bool.assert_called_once_with("ChestnutActive", False)
    receipt.loaded.assert_called_once_with(env["small_prepared"], ModelVariant.SMALL, "chestnut-load-failed", model_id="fallback-small")
  else:
    with pytest.raises(RuntimeError, match="inference failed"):
      exec(code, env)
    assert env["model"] is failed_model
    params.put_bool.assert_not_called()
    env["drop_chestnut"].assert_not_called()
    receipt.loaded.assert_not_called()
  params.get_bool.assert_not_called()


def test_remote_inference_failure_does_not_change_chestnut_status():
  fallback = next(node for statement in main_body() for node in ast.walk(statement)
                  if isinstance(node, ast.Try) and any(isinstance(child, ast.Call) and ast.unparse(child.func) == "model.run"
                                                       for child in ast.walk(node)))
  failed_model = FakeJoinedModel(chestnut=True)
  failed_model.run = Mock(side_effect=RuntimeError("remote inference failed"))
  failed_model.close = Mock()
  small_model = SimpleNamespace(chestnut=False, model_id="fallback-small")
  params, receipt = Mock(), Mock()
  env = {"model": failed_model, "small_model": small_model, "params": params, "receipt_owner": receipt,
         "chestnut_state": None, "run_count": 1, "bufs": {}, "transforms": {}, "inputs": {},
         "small_prepared": object(), "ModelVariant": ModelVariant, "jetlink_model": failed_model, "drop_chestnut": Mock(), "cloudlog": Mock()}
  execute([fallback], env)
  failed_model.close.assert_called_once_with()
  env["drop_chestnut"].assert_not_called()
  assert env["model"] is small_model
  assert env["model_output"] is None
  assert env["run_count"] == 0
  params.put_bool.assert_not_called()
  assert receipt.loaded.call_args.args == (env["small_prepared"], ModelVariant.SMALL, None)
  assert receipt.loaded.call_args.kwargs["model_id"] == "fallback-small"


def execute(statements, env):
  def exit_process(code):
    raise SystemExit(code)
  env.setdefault("os", SimpleNamespace(getenv=lambda key: None, _exit=exit_process))
  env.setdefault("recovery_small_only", False)
  env.setdefault("coherent_warp", None)
  env.setdefault("laboratory_request", None)
  from openpilot.starpilot.models.laboratory_runner import LaboratoryPair
  env.setdefault("LaboratoryPair", LaboratoryPair)
  env.setdefault("set_core_affinity", Mock())
  from openpilot.starpilot.models.recovery import STARTUP_FALLBACK_EXIT
  env.setdefault("STARTUP_FALLBACK_EXIT", STARTUP_FALLBACK_EXIT)
  exec(compile(ast.fix_missing_locations(ast.Module(body=statements, type_ignores=[])), str(MODEL_SOURCE), "exec"), env)


@pytest.mark.parametrize("present,cable,allow_big,big_path,compiled,expected,link", [
  (True, False, True, "verified-big", False, True, False),
  (False, True, True, "verified-big", False, True, False),
  (False, True, True, None, True, False, False),
  (False, False, False, None, True, False, False),
  (True, False, False, "verified-big", True, False, False),
  (False, True, True, None, False, False, False),
  # with Jetlink on, the cable is its host: no chestnut wait, no Big Model Failed, the link joins
  (False, True, True, "verified-big", False, False, True),
  (True, True, True, "verified-big", False, True, True),
])
def test_observed_availability_and_big_eligibility(monkeypatch, present, cable, allow_big, big_path, compiled, expected, link):
  from types import ModuleType
  import sys

  manager = ModuleType("openpilot.starpilot.models.manager")
  manager.ROOT = Path("/test-models")
  manager.resolve_runtime = Mock(side_effect=lambda chestnut_available, randomize:
                                 SimpleNamespace(allow_big=allow_big and chestnut_available, big_path=big_path))
  manager.requested_runtime_id = Mock(return_value="selected")
  amd = ModuleType("tinygrad.runtime.ops_amd")
  amd.AMDDevice = type("FakeAMDDevice", (), {"wait_timeout_ms": 123})
  monkeypatch.setitem(sys.modules, manager.__name__, manager)
  monkeypatch.setitem(sys.modules, amd.__name__, amd)
  body = main_body()
  start = next(i for i, node in enumerate(body) if isinstance(node, ast.ImportFrom) and node.module == manager.__name__)
  end = next(i for i, node in enumerate(body) if ast.unparse(node) == "gc.disable()")
  params = Mock()
  env: dict = {"chestnut_present": Mock(return_value=present), "cable_connected": Mock(return_value=cable), "usb_host_connected": Mock(return_value=False),
         "chestnut_compiled": Mock(return_value=compiled), "Params": lambda: params,
         "jetlink_adapter": SimpleNamespace(link_on=lambda: link)}
  execute(body[start:end], env)
  available = present or (cable and not link)
  manager.resolve_runtime.assert_called_once_with(chestnut_available=available, randomize=True)
  manager.requested_runtime_id.assert_called_once_with(available)
  env["chestnut_present"].assert_called_once_with()
  assert env["cable_connected"].call_count == (0 if present else 1)
  assert env["CHESTNUT"] is expected
  assert amd.AMDDevice.wait_timeout_ms == (3000 if expected else 123)
  params.put_bool.assert_called_once_with("ChestnutLoading", expected)


@pytest.mark.parametrize("timeout,worker_alive", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("laboratory", [False, True])
def test_big_worker_waits_before_load_and_failure_requests_fresh_small_process(custom, timeout, worker_alive, laboratory, monkeypatch):
  from openpilot.starpilot.models import manager
  monkeypatch.setattr(manager, "shipped_default", lambda: Path("bundled-small"))
  body = main_body()
  start = next(i for i, node in enumerate(body) if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "st")
  end = next(i for i, node in enumerate(body) if ast.unparse(node).startswith("config_realtime_process("))
  events = []
  worker = False

  class Thread:
    def __init__(self, target, daemon):
      assert daemon
      self.target = target

    def start(self):
      nonlocal worker
      worker = True
      self.target()
      worker = False

    def join(self, duration):
      events.append(("join", duration))

    def is_alive(self):
      return worker_alive

  def wait():
    assert worker
    events.append("wait")
    if timeout:
      raise TimeoutError("chestnut did not enumerate")

  def state(width, height, chestnut):
    events.append("big" if chestnut else "small")
    return SimpleNamespace(chestnut=chestnut, model_id="big" if chestnut else "small", warmup=Mock())

  def verified(width, height, path, version, chestnut, model_id, sha, **kwargs):
    assert kwargs == ({} if chestnut else {"coherent_warp": None})
    return state(width, height, chestnut), "prepared-" + model_id

  params, receipt = Mock(), Mock()
  from jetlink.openpilot.hooks import Hooks
  from openpilot.starpilot.models.jetlink_adapter import API
  bind = Mock(side_effect=AssertionError("a chestnut drive must not start jetlink"))
  hooks = Hooks(API, bind)
  selection = SimpleNamespace(big_path="selected-big" if custom else None, small_path="selected-small" if custom else None,
                              small_version=1, small_id="small", small_sha256="small-hash",
                              big_version=1, big_id="big", big_sha256="hash")
  from openpilot.starpilot.models.catalog import BY_ID, DEFAULT_SMALL, DEFAULT_SMALL_SHA256
  env: dict = {"BY_ID": BY_ID, "DEFAULT_SMALL": DEFAULT_SMALL, "DEFAULT_SMALL_SHA256": DEFAULT_SMALL_SHA256,
         "time": SimpleNamespace(monotonic=lambda: 0), "cloudlog": Mock(), "selection": selection,
         "CHESTNUT": True, "vipc_client_main": SimpleNamespace(width=1928, height=1208), "wait_for_chestnut": wait,
         "ModelState": state, "load_verified_model": verified, "modeld_pkl_path": lambda big: "bundled-big" if big else "bundled-small",
         "receipt_owner": receipt, "threading": SimpleNamespace(Thread=Thread), "BIG_MODEL_TIMEOUT": 30,
         "report_load_timeout": Mock(),
         "params": params, "requested_model_id": "big", "ModelVariant": ModelVariant,
         "CP": SimpleNamespace(brand="mock"), "demo": False,
         "wait_for_chestnut_power": lambda CP, timeout: events.append("power"),
         "jetlink_adapter": SimpleNamespace(hooks=lambda: hooks, coherent_warp=lambda: None), "drop_chestnut": Mock(),
         "release_compile_pool": Mock(), "gc": Mock()}
  if laboratory:
    from openpilot.starpilot.models.laboratory_runner import LaboratoryPair
    pair = object.__new__(LaboratoryPair)
    pair.chestnut, pair.model_id = True, 'lateral+longitudinal'
    def load_pair(width, height, config, *, root):
      assert config == {'enabled': True, 'lateralModel': 'lateral', 'longitudinalModel': 'longitudinal'}
      assert root == Path('/test-models')
      events.append('big')
      return pair
    env.update(laboratory_request={'enabled': True, 'lateralModel': 'lateral', 'longitudinalModel': 'longitudinal'},
               load_laboratory_pair=load_pair, MODEL_ROOT=Path('/test-models'), LaboratoryPair=LaboratoryPair)
  wrapper = ast.parse("def startup():\n  pass").body[0]
  wrapper.body = body[start:end] + [ast.Return(value=ast.Call(func=ast.Name(id="locals", ctx=ast.Load()), args=[], keywords=[]))]
  env["cam_w"], env["cam_h"] = env["vipc_client_main"].width, env["vipc_client_main"].height
  execute([wrapper], env)
  if timeout:
    from openpilot.starpilot.models.recovery import STARTUP_FALLBACK_EXIT
    with pytest.raises(SystemExit) as failed:
      env["startup"]()
    assert failed.value.code == STARTUP_FALLBACK_EXIT
    env["drop_chestnut"].assert_not_called()
    receipt.loaded.assert_not_called()
    env["release_compile_pool"].assert_not_called()
    bind.assert_not_called()
    assert events.count("small") == int(custom or laboratory)
    params.put_bool.assert_called_once_with("ChestnutActive", False)
    return
  env.update(env["startup"]())
  bind.assert_not_called()
  assert env["jetlink_model"] is None
  assert events[0] == ("small" if custom or laboratory else "wait")
  assert events.count("small") == 1
  assert ("big" in events) is not timeout
  assert env["model"].chestnut is not timeout
  assert env["small_model"].chestnut is False
  assert events.index("wait") < events.index(("join", 30))
  if not timeout:
    assert events.index("wait") < events.index("power") < events.index("big")
  assert env["drop_chestnut"].call_count == int(timeout)
  env["release_compile_pool"].assert_called_once_with()
  env["gc"].collect.assert_called_once_with()
  params.put_bool.assert_any_call("ChestnutActive", not timeout)
  params.put_bool.assert_any_call("ChestnutLoading", False)
  if laboratory:
    receipt.loaded_pair.assert_called_once()
    assert receipt.loaded_pair.call_args.args[0] is pair
    assert len(receipt.loaded_pair.call_args.args[1]) == 64
    receipt.loaded.assert_not_called()
  else:
    assert receipt.loaded.call_args.args[1] == (ModelVariant.SMALL if timeout else ModelVariant.CHESTNUT)
    assert receipt.loaded.call_args.args[2] == ("chestnut-load-failed" if timeout else None)


def test_wait_for_enumeration_succeeds_without_real_sleep(monkeypatch):
  from openpilot.selfdrive.modeld import helpers
  present = Mock(side_effect=[False, False, True])
  sleep = Mock()
  monkeypatch.setattr(helpers, "chestnut_present", present)
  monkeypatch.setattr(helpers, "time", SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 0.1]), sleep=sleep))
  helpers.wait_for_chestnut(timeout=1)
  assert present.call_count == 3
  assert sleep.call_args_list == [((0.1,),), ((0.1,),)]


def test_wait_for_enumeration_times_out_without_loading_hardware(monkeypatch):
  from openpilot.selfdrive.modeld import helpers
  sleep = Mock()
  monkeypatch.setattr(helpers, "chestnut_present", lambda: False)
  monkeypatch.setattr(helpers, "time", SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 0.2]), sleep=sleep))
  with pytest.raises(TimeoutError, match="chestnut did not enumerate"):
    helpers.wait_for_chestnut(timeout=0.1)
  sleep.assert_called_once_with(0.1)


@pytest.mark.parametrize("previous", ["READY", "FAILED"])
def test_native_ui_keeps_loading_during_enumeration_then_reports_failure(previous):
  from enum import Enum
  source = MODEL_SOURCE.parents[1] / "ui/ui_state.py"
  body = ast.parse(source.read_text()).body
  chestnut_enum = next(node for node in body if isinstance(node, ast.ClassDef) and node.name == "ChestnutState")
  ui_class = next(node for node in body if isinstance(node, ast.ClassDef) and node.name == "UIState")
  update = next(node for node in ui_class.body if isinstance(node, ast.FunctionDef) and node.name == "_update_chestnut_state")
  display_time = next(node for node in ui_class.body if isinstance(node, ast.FunctionDef) and node.name == "display_time_ns")
  env: dict = {"Enum": Enum, "time": SimpleNamespace(monotonic_ns=lambda: 1_000_000_000)}
  execute([chestnut_enum, update, display_time], env)
  env["UIState"] = SimpleNamespace(display_time_ns=env["display_time_ns"])
  states = env["ChestnutState"]

  class Messages(dict):
    recv_frame = {"modelV2": 2}
    alive = {"modelV2": True}
    valid = {"modelV2": True}
    logMonoTime = {"modelV2": 1_000_000_000}

  ui = SimpleNamespace(sm=Messages(deviceState=SimpleNamespace(chestnutPresent=False), modelV2=SimpleNamespace(big=False)),
                       started=True, started_frame=1, chestnut_present=True, chestnut_compiled=True,
                       chestnut_loading=True, chestnut_active=False, chestnut_output_seen=False, chestnut_state=states[previous])
  env["_update_chestnut_state"](ui)
  assert ui.chestnut_state is states.FAILED
  ui.chestnut_loading = False
  env["_update_chestnut_state"](ui)
  assert ui.chestnut_state is states.FAILED
  ui.chestnut_present = False
  env["_update_chestnut_state"](ui)
  assert ui.chestnut_state is states.DISCONNECTED
  ui.chestnut_present, ui.chestnut_compiled = True, False
  env["_update_chestnut_state"](ui)
  assert ui.chestnut_state is states.FAILED
  ui.sm["deviceState"].chestnutPresent = True
  ui.sm["modelV2"].big = True
  ui.chestnut_active = True
  env["_update_chestnut_state"](ui)
  assert ui.chestnut_state is states.ACTIVE


def test_recovery_child_resolves_small_without_randomizer_or_amd(monkeypatch):
  from types import ModuleType
  import sys
  manager = ModuleType("openpilot.starpilot.models.manager")
  manager.ROOT = Path("/test-models")
  manager.resolve_runtime = Mock(return_value=SimpleNamespace(allow_big=False, big_path=None))
  manager.requested_runtime_id = Mock(return_value="requested-big")
  monkeypatch.setitem(sys.modules, manager.__name__, manager)
  body = main_body()
  start = next(i for i, node in enumerate(body) if isinstance(node, ast.ImportFrom) and node.module == manager.__name__)
  end = next(i for i, node in enumerate(body) if ast.unparse(node) == "gc.disable()")
  params = Mock()
  env: dict = {"chestnut_present": lambda: True, "cable_connected": lambda: True,
         "chestnut_compiled": Mock(side_effect=AssertionError("Big compiled probe forbidden")),
         "Params": lambda: params, "os": SimpleNamespace(getenv=lambda key: "1")}
  execute(body[start:end], env)
  manager.resolve_runtime.assert_called_once_with(chestnut_available=False, randomize=False)
  manager.requested_runtime_id.assert_called_once_with(True)
  assert env["CHESTNUT"] is False
  assert "AMDDevice" not in env
  assert params.put_bool.call_args_list == [(("ChestnutLoading", False),)]


def test_recovery_small_receipt_reports_runtime_stall_not_load_failure():
  statement = next(node for node in ast.walk(ast.Module(body=main_body(), type_ignores=[])) if isinstance(node, ast.Expr) and
                   isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == 'receipt_owner.loaded')
  receipt = Mock()
  env: dict = {'model': SimpleNamespace(chestnut=False, model_id='bundled-current'), 'small_prepared': object(),
         'receipt_owner': receipt, 'ModelVariant': ModelVariant, 'initial_chestnut_fallback': False,
         'recovery_small_only': True, 'selected_small_failed': False, 'requested_model_id': 'requested-big'}
  execute([statement], env)
  assert receipt.loaded.call_args.args[1:3] == (ModelVariant.SMALL, 'chestnut-run-stalled')
  assert receipt.loaded.call_args.kwargs['model_id'] == 'bundled-current'


@pytest.mark.parametrize('fallback_failure', [None, 'missing', 'load'])
def test_failed_selected_small_uses_only_verified_rdf_or_refuses_startup(monkeypatch, fallback_failure):
  from openpilot.starpilot.models import manager
  from openpilot.starpilot.models.catalog import BY_ID, DEFAULT_SMALL, DEFAULT_SMALL_SHA256

  function = next(node for node in main_body() if isinstance(node, ast.FunctionDef) and node.name == 'load_small')
  rdf = SimpleNamespace(chestnut=False, model_id=DEFAULT_SMALL)
  shipped = Path('shipped-rdf.pkl')
  monkeypatch.setattr(manager, 'shipped_default', lambda: None if fallback_failure == 'missing' else shipped)
  loader = Mock(side_effect=[RuntimeError('selected failed'), RuntimeError('RDF failed') if fallback_failure == 'load' else (rdf, 'rdf-prepared')])
  stock = Mock(side_effect=AssertionError('stock model must not load'))
  env: dict = {'selection': SimpleNamespace(small_path=Path('custom.pkl'), small_version='v15', small_id='gwm8223', small_sha256='custom'),
         'vipc_client_main': SimpleNamespace(width=1928, height=1208), 'load_verified_model': loader,
         'ModelState': stock, 'modeld_pkl_path': stock, 'cloudlog': Mock(), 'BY_ID': BY_ID,
         'DEFAULT_SMALL': DEFAULT_SMALL, 'DEFAULT_SMALL_SHA256': DEFAULT_SMALL_SHA256}
  env["cam_w"], env["cam_h"] = env["vipc_client_main"].width, env["vipc_client_main"].height
  execute([function], env)
  if fallback_failure is None:
    assert env['load_small']() == (rdf, 'rdf-prepared', True)
  else:
    with pytest.raises(RuntimeError, match='Shipped RDFv4' if fallback_failure == 'missing' else 'RDF failed'):
      env['load_small']()
  stock.assert_not_called()
  if fallback_failure != 'missing':
    assert loader.call_args.args == (1928, 1208, shipped, 'v15', False, DEFAULT_SMALL, DEFAULT_SMALL_SHA256)


@pytest.mark.parametrize("opened", [("QCOM",), ("QCOM", "AMD")])
def test_fallback_releases_only_chestnut_dependencies(opened):
  node = next(node for node in ast.parse(MODEL_SOURCE.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "drop_chestnut")
  class RuntimeDevice:
    def __init__(self):
      self.pending = {}
  class Devices(dict):
    _opened_devices = opened
  devices = Devices(QCOM=RuntimeDevice(), AMD=RuntimeDevice())
  amd, unrelated = devices["AMD"], object()
  for device in devices.values():
    device.pending = {amd: "gpu fence", unrelated: "other fence"}
  env: dict[str, object] = {"Device": devices}
  execute([node], env)
  drop = env["drop_chestnut"]
  assert callable(drop)
  drop()
  for name in opened:
    assert unrelated in devices[name].pending
    assert (amd in devices[name].pending) is ("AMD" not in opened)
