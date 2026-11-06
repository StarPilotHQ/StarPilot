"""Evaluate the actual SCons graph with fake nodes; never execute compilers."""

import ctypes.util
import shlex
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest


SOURCE = Path(__file__).resolve().parents[3] / "selfdrive/modeld/SConscript"
EXPECTED_TARGETS = {
  "dmonitoring_model_tinygrad.pkl",
  "driving_warp_1344x760_tinygrad.pkl", "driving_warp_1928x1208_tinygrad.pkl",
  "dm_warp_1344x760_tinygrad.pkl", "dm_warp_1928x1208_tinygrad.pkl",
}


class Returned(Exception):
  pass


@pytest.fixture
def graph(monkeypatch, tmp_path):
  script = ModuleType("SCons.Script")
  script.Action = lambda command, description: command
  script.Value = lambda value: value
  monkeypatch.setitem(sys.modules, script.__name__, script)
  helpers = ModuleType("openpilot.selfdrive.modeld.helpers")
  helpers.chestnut_present = Mock(return_value=False)
  monkeypatch.setitem(sys.modules, helpers.__name__, helpers)
  monkeypatch.delenv("COMMA_LAPTOP_CROSS_BUILD", raising=False)
  env = MagicMock()
  build_env = {"ENV": {}}
  env.__getitem__.side_effect = build_env.__getitem__
  env.Clone.return_value = env

  def node(path):
    relative = path.removeprefix("#")
    return SimpleNamespace(abspath=str(tmp_path / relative), relpath=relative)

  env.Dir.side_effect = node
  for compiler in ("tinygrad_repo", "tinygrad_model_runtime/legacy9d/tinygrad_repo"):
    marker = tmp_path / compiler / "compiler_dependency.py"
    marker.parent.mkdir(parents=True)
    marker.write_text("# compiler dependency witness\n")

  def stop():
    raise Returned()

  def evaluate(arch):
    scope = {"env": env, "arch": arch, "Import": Mock(), "Return": stop, "Dir": node, "File": node}
    try:
      exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), scope)
    except Returned:
      pass
    return scope

  return evaluate, env, helpers.chestnut_present


def test_missing_llvm_pc_skips_graph_before_probe_or_compilation(graph, monkeypatch, capsys):
  evaluate, env, present = graph
  llvm = Mock(return_value=None)
  monkeypatch.setattr(ctypes.util, "find_library", llvm)
  evaluate("aarch64")
  llvm.assert_called_once_with("LLVM")
  env.Clone.assert_not_called()
  env.Command.assert_not_called()
  present.assert_not_called()
  assert "Install LLVM to compile models" in capsys.readouterr().out


@pytest.mark.parametrize("arch,llvm,backend", [
  ("aarch64", "libLLVM.so", "DEV=CPU:LLVM"),
  ("Darwin", None, "DEV=METAL HCQ_RUNTIME_DEV=CPU:CLANG JIT=2"),
  ("comma_arm64", None, "DEV=QCOM IMAGE=1 FLOAT16=1 NOLOCALS=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1"),
])
def test_supported_backend_retains_models_and_camera_warp_graph(graph, monkeypatch, arch, llvm, backend):
  evaluate, env, present = graph
  library = Mock(return_value=llvm)
  monkeypatch.setattr(ctypes.util, "find_library", library)
  evaluate(arch)
  assert library.call_count == (1 if arch == "aarch64" else 0)
  present.assert_not_called()
  calls = env.Command.call_args_list
  assert len(calls) == 5
  targets = [Path(call.args[0]).name for call in calls]
  assert set(targets) == EXPECTED_TARGETS
  assert targets[0] == "dmonitoring_model_tinygrad.pkl"
  assert "driving_tinygrad.pkl" not in targets
  assert sum(name.startswith("driving_warp_") for name in targets) == 2
  assert sum(name.startswith("dm_warp_") for name in targets) == 2
  compiler = "tinygrad_model_runtime/legacy9d/tinygrad_repo" if arch == "comma_arm64" else "tinygrad_repo"
  compiler_root = Path(calls[0].args[0]).parent.parent / compiler
  # Commands and their dependency Values must describe the same selected compiler.
  # Token checks retain every required flag without requiring adjacent assignments.
  for call in calls:
    target, dependencies, command = call.args
    if callable(command):
      assert arch == "comma_arm64"
      assert Path(target).name in {"dmonitoring_model_tinygrad.pkl", "dm_warp_1344x760_tinygrad.pkl", "dm_warp_1928x1208_tinygrad.pkl"}
      owner = Mock()
      monkeypatch.setitem(command.__globals__, "preserve_stock_artifact", owner)
      command([target], [], env)
      owner.assert_called_once_with(Path(target))
      assert dependencies[-1].startswith(backend)
      continue
    tokens = shlex.split(command)
    assert set(shlex.split(backend)) <= set(tokens)
    assert str(compiler_root / "examples/openpilot" / ("compile_onnx.py" if target.endswith("dmonitoring_model_tinygrad.pkl")
                                                     else "compile_warp.py")) in tokens
    assert "#" + str(Path(compiler) / "compiler_dependency.py") in dependencies
    assert command in dependencies
    if "_warp_" in target:
      warp_dependency = "openpilot/system/camerad/cameras/nv12_info.py"
      assert any(getattr(source, "relpath", None) == warp_dependency and
                 getattr(source, "abspath", None) == str(Path(target).parent.parent / warp_dependency)
                 for source in dependencies)
      assert "--frame" in tokens and "--warp-to" in tokens
      dimensions = Path(target).name.split("_warp_", 1)[1].split("_tinygrad.pkl", 1)[0]
      assert tokens[tokens.index("--frame") + 1].split(",")[:2] == dimensions.split("x")
      assert tokens[tokens.index("--output") + 1] == target
      assert tokens[tokens.index("--layout") + 1] == ("luma" if Path(target).name.startswith("dm_") else "yuv420")
    else:
      assert str(Path(target).parent / "dmonitoring_model.onnx") in dependencies
  env.PrependENVPath.assert_called_once_with("PYTHONPATH", str(compiler_root))
  if arch == "comma_arm64":
    assert env["ENV"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["ENV"]["CACHEDB"].endswith("/9d0446a4ba8a532c8b674fb6ad795af015cd9dcf/tinygrad.db")
    assert env["ENV"]["XDG_CACHE_HOME"].endswith("/9d0446a4ba8a532c8b674fb6ad795af015cd9dcf/xdg")
  else:
    assert env["ENV"] == {}
  env.Execute.assert_not_called()


def test_llvm_probe_error_does_not_schedule_compilation(graph, monkeypatch):
  evaluate, env, present = graph
  monkeypatch.setattr(ctypes.util, "find_library", Mock(side_effect=OSError("probe failed")))
  with pytest.raises(OSError, match="probe failed"):
    evaluate("aarch64")
  env.Command.assert_not_called()
  present.assert_not_called()


def test_build_does_not_probe_chestnut_or_regenerate_shipped_big_artifacts(graph, monkeypatch):
  evaluate, env, present = graph
  present.return_value = True
  library = Mock()
  monkeypatch.setattr(ctypes.util, "find_library", library)
  evaluate("Darwin")
  library.assert_not_called()
  present.assert_not_called()
  assert env.Command.call_count == 5
  assert {Path(call.args[0]).name for call in env.Command.call_args_list} == EXPECTED_TARGETS
  assert not any(Path(call.args[0]).name.startswith("big_driving_") for call in env.Command.call_args_list)
  env.SideEffect.assert_not_called()
  env.Execute.assert_not_called()


def test_device_monitoring_nodes_never_recompile_pinned_artifacts(graph):
  evaluate, env, _ = graph
  evaluate("comma_arm64")
  stock = [call for call in env.Command.call_args_list if Path(call.args[0]).name.startswith(("dm_warp_", "dmonitoring_model_"))]
  assert len(stock) == 3
  assert env.Precious.call_count == 3
  assert env.NoClean.call_count == 3
  assert env.AlwaysBuild.call_count == 3
  assert all(callable(call.args[2]) for call in stock)
  assert all(isinstance(call.args[2], str) for call in env.Command.call_args_list if Path(call.args[0]).name.startswith("driving_warp_"))
