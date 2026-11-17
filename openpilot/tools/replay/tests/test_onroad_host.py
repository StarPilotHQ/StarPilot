"""Host replay parsing, private Params seeding and child ownership."""

from pathlib import Path
from types import SimpleNamespace
import os
import signal
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.tools.replay.onroad import _block_alert_service, _ipc_root, owned_ipc_namespace, parse_onroad_args, run, seed_replay_params, supervise
from openpilot.tools.replay.onroad_config import first_log_identifier, parse_replay_args, seed_preview, select_ui_target, replay_device_type


class TestOnroadHost(unittest.TestCase):
  def test_supervisor_preserves_fast_clean_exit_and_maps_child_signal(self):
    class FakeProcess:
      def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.returncode = None

      def poll(self):
        self.returncode = next(self.statuses, self.returncode)
        return self.returncode

      def terminate(self):
        self.returncode = 0

    clean = FakeProcess([0])
    self.assertEqual(supervise(["replay"], [], {}, spawn=lambda *_args, **_kwargs: clean), 0)
    replay = FakeProcess([None, None])
    aborted_ui = FakeProcess([-6])
    queue = iter((replay, aborted_ui))
    self.assertEqual(supervise(["replay"], [["ui"]], {}, spawn=lambda *_args, **_kwargs: next(queue)), 134)

  def test_native_pubmaster_uses_owned_ipc_parent_and_cleanup(self):
    prefix = f"replay-test-{os.getpid()}-{time.time_ns()}"
    root = _ipc_root(prefix)
    self.assertFalse(root.exists())
    with owned_ipc_namespace(prefix):
      self.assertTrue(root.is_dir())
      env = dict(os.environ, OPENPILOT_PREFIX=prefix)
      env.pop("CEREAL_FAKE", None)
      result = subprocess.run([sys.executable, "-c", "from openpilot.cereal.messaging import PubMaster; PubMaster(['accelerometer'])"],
                              env=env, capture_output=True, text=True, check=False, timeout=5)
      self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
      self.assertTrue((root / "accelerometer").exists())
    self.assertFalse(root.exists())

  def test_existing_ipc_namespace_is_never_removed(self):
    prefix = f"replay-test-{os.getpid()}-{time.time_ns()}"
    root = _ipc_root(prefix)
    root.mkdir()
    try:
      sentinel = root / "external"
      sentinel.write_text("keep")
      with self.assertRaisesRegex(RuntimeError, "already has an IPC namespace"):
        with owned_ipc_namespace(prefix):
          pass
      self.assertEqual(sentinel.read_text(), "keep")
    finally:
      sentinel.unlink()
      root.rmdir()

  def test_help_requires_no_runtime_or_route(self):
    result = subprocess.run([sys.executable, "-m", "openpilot.tools.replay.onroad", "--help"],
                            capture_output=True, text=True, check=False)
    self.assertEqual(result.returncode, 0)
    self.assertIn("--replay-only", result.stdout)

  def test_route_ui_and_option_preservation(self):
    plan = parse_onroad_args(["--c4", "--prefix", "replay-fixture", "--start", "30", "--data_dir=/private/routes",
                              "route/abc", "--no-loop"])
    self.assertEqual(plan.targets, ("c4",))
    self.assertEqual(plan.prefix, "replay-fixture")
    self.assertEqual(plan.replay_args, ("--start", "30", "--data_dir=/private/routes", "route/abc", "--no-loop"))
    self.assertEqual(select_ui_target(SimpleNamespace(deviceType="mici")), "c4")
    self.assertEqual(select_ui_target(None), "c3")
    self.assertEqual(parse_onroad_args(["--all", "--demo"]).targets, ("c3", "c4"))
    self.assertEqual(parse_onroad_args(["--replay-only", "--demo"]).targets, ())

  def test_unsupported_or_unsafe_options_fail_before_spawn(self):
    for args in (["--nav", "--demo"], ["--replay-only", "--alert", "--demo"],
                 ["--ui=none", "-alert", "--demo"],
                 ["--prefix", "d", "--demo"], ["--demo", "--", "--prefix", "replay-other"],
                 ["--headless", "--demo"], ["--auto"], ["--ui=bogus", "--demo"]):
      with self.subTest(args=args), self.assertRaises(ValueError):
        parse_onroad_args(args)

  def test_alert_preview_preserves_route_and_combines_native_blocklist(self):
    plan = parse_onroad_args(["--c4", "-alert", "-b", "carControl,selfdriveState", "--start", "30", "--demo"])
    self.assertTrue(plan.alert)
    self.assertEqual(plan.targets, ("c4",))
    self.assertEqual(_block_alert_service(plan.replay_args),
                     ["-b", "carControl,selfdriveState", "--start", "30", "--demo"])
    self.assertEqual(_block_alert_service(("--block=carState", "--demo")),
                     ["-b", "carState,selfdriveState", "--demo"])
    self.assertEqual(_block_alert_service(("-b", "carState", "--block=carControl,selfdriveState", "--demo")),
                     ["-b", "carState,carControl,selfdriveState", "--demo"])
    self.assertFalse(parse_onroad_args(["--c3", "--demo"]).alert)
    self.assertFalse(parse_onroad_args(["--demo"]).visual_preview)

  def test_cem_csc_aliases_are_ui_only_and_combine_with_alert(self):
    plan = parse_onroad_args(["--c4", "--mici-widget-demo", "--csc-demo", "-alert", "--demo"])
    self.assertEqual(plan.visual_preview, frozenset(("cem", "csc")))
    self.assertTrue(plan.alert)
    self.assertEqual(plan.replay_args, ("--demo",))
    self.assertEqual(parse_onroad_args(["--c3", "--widget-demo", "--demo"]).visual_preview,
                     frozenset(("cem",)))
    self.assertEqual(parse_onroad_args(["--c3", "--csc", "--demo"]).visual_preview,
                     frozenset(("csc",)))
    for args in (["--replay-only", "--cem", "--demo"], ["--ui=none", "--csc", "--demo"]):
      with self.subTest(args=args), self.assertRaisesRegex(ValueError, "require a native UI"):
        parse_onroad_args(args)

  def test_alert_publisher_failure_reaps_replay_and_ui(self):
    class FakeProcess:
      def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.returncode = None
        self.terminated = False

      def poll(self):
        self.returncode = next(self.statuses, self.returncode)
        return self.returncode

      def terminate(self):
        self.terminated = True
        self.returncode = 0

    replay = FakeProcess([None, None])
    ui = FakeProcess([None])
    alert = FakeProcess([2])
    queue = iter((replay, ui, alert))
    self.assertEqual(supervise(["replay"], [["ui"]], {}, demo_commands=[["alert"]],
                               spawn=lambda *_args, **_kwargs: next(queue)), 2)
    self.assertTrue(replay.terminated)
    self.assertTrue(ui.terminated)
    replay = FakeProcess([None, None])
    ui = FakeProcess([None])
    alert = FakeProcess([0])
    queue = iter((replay, ui, alert))
    self.assertEqual(supervise(["replay"], [["ui"]], {}, demo_commands=[["alert"]],
                               spawn=lambda *_args, **_kwargs: next(queue)), 1)

  def test_alert_run_passes_one_owned_publisher_and_replay_exclusion(self):
    with tempfile.TemporaryDirectory() as directory:
      private = Path(directory) / "params"
      env = {"SP_HOST_RUNTIME": "1", "SP_HOST_PARAMS_ROOT": str(private), "PARAMS_ROOT": str(private),
             "SP_HOST_PREFIX": "starpilot-dev-host", "OPENPILOT_PREFIX": "starpilot-dev-host"}
      with patch.dict(os.environ, env), patch.object(Path, "is_file", return_value=True), \
           patch("openpilot.tools.replay.onroad.os.access", return_value=True), \
           patch("openpilot.tools.replay.onroad_config.route_init_data", return_value=None), \
           patch("openpilot.starpilot.ui.host_launch.launch_environment", return_value={}), \
           patch("openpilot.tools.replay.onroad.supervise", return_value=0) as owned:
        self.assertEqual(run(parse_onroad_args(["--c4", "--cem", "--csc", "-alert", "--prefix", "replay-alertfixture",
                                               "-b", "carControl", "--demo"])), 0)
      replay, ui, child_env, kwargs = owned.call_args.args[0], owned.call_args.args[1], owned.call_args.args[2], owned.call_args.kwargs
      self.assertEqual(replay[1:3], ["-b", "carControl,selfdriveState"])
      self.assertEqual(len(ui), 1)
      self.assertEqual(child_env["SP_ONROAD_VISUAL_PREVIEW"], "cem,csc")
      self.assertEqual(kwargs["demo_commands"], [[sys.executable, "-m", "openpilot.tools.replay.alert_demo"]])

  def test_actual_private_alert_pubsub_shows_then_clears(self):
    import importlib.machinery
    import capnp.lib.capnp as native_capnp
    import msgq._ipc as native_ipc
    from openpilot.cereal import messaging
    from openpilot.starpilot.ui.runtime_snapshot import current_message, _alert

    root = Path(__file__).resolve().parents[4]
    self.assertTrue(Path(native_ipc.__file__).resolve().is_relative_to(root / 'msgq_repo/msgq'))
    for native in (native_ipc, native_capnp):
      self.assertTrue(any(str(native.__file__).endswith(suffix)
                          for suffix in importlib.machinery.EXTENSION_SUFFIXES))
    self.assertIs(messaging.SubSocket, native_ipc.SubSocket)
    self.assertEqual(messaging.SubMaster.__module__, 'openpilot.cereal.messaging')
    prefix = f"replay-alert-{os.getpid()}-{time.time_ns()}"
    env = dict(os.environ, OPENPILOT_PREFIX=prefix, USE_MSGQ_PREFIX="true", SP_HOST_RUNTIME="1")
    env.pop("CEREAL_FAKE", None)
    with owned_ipc_namespace(prefix), patch.dict(os.environ, env, clear=True), tempfile.TemporaryFile(mode='w+b') as diagnostics:
      subscriber = messaging.SubMaster(['selfdriveState'])
      publisher = None
      seen = []
      overall_deadline = time.monotonic() + 7

      def publisher_diagnostics():
        diagnostics.flush()
        diagnostics.seek(0, os.SEEK_END)
        diagnostics.seek(max(0, diagnostics.tell() - 4096))
        return diagnostics.read().decode(errors='replace')

      try:
        publisher = subprocess.Popen([sys.executable, "-m", "openpilot.tools.replay.alert_demo",
                                      "--delay", ".2", "--hold", ".5"], env=env,
                                     stdout=diagnostics, stderr=subprocess.STDOUT, text=True)
        deadline = min(time.monotonic() + 5, overall_deadline)
        while time.monotonic() < deadline:
          subscriber.update(100)
          if not subscriber.updated['selfdriveState']:
            continue
          msg = current_message(subscriber, 'selfdriveState', time.monotonic_ns())
          if msg is None:
            continue
          visual = _alert(msg)
          if visual.size.value == 'full':
            self.assertTrue(visual.critical)
            self.assertEqual(visual.text1, 'TAKE CONTROL IMMEDIATELY')
            seen.append('full')
          elif visual.size.value == 'none' and 'full' in seen:
            seen.append('clear')
            break
        self.assertEqual(seen[-2:], ['full', 'clear'], publisher_diagnostics())
        self.assertIsNone(publisher.poll(), "Preview publisher must remain owned after clearing")
      except BaseException as error:
        error.add_note('Alert publisher diagnostics: ' + publisher_diagnostics())
        raise
      finally:
        if publisher is not None:
          if publisher.poll() is None:
            publisher.terminate()
          try:
            publisher.communicate(timeout=max(.001, min(3, overall_deadline - time.monotonic())))
          except subprocess.TimeoutExpired:
            publisher.kill()
            publisher.communicate(timeout=2)
        del subscriber

  def test_alert_module_refuses_unowned_namespace_before_publishing(self):
    env = dict(os.environ, SP_HOST_RUNTIME="1", OPENPILOT_PREFIX="replay-missing-namespace")
    result = subprocess.run([sys.executable, "-m", "openpilot.tools.replay.alert_demo", "--delay", "0"],
                            env=env, capture_output=True, text=True, check=False, timeout=5)
    self.assertEqual(result.returncode, 2)
    self.assertIn("isolated host replay session", result.stderr)

  def test_local_first_log_choice_does_not_fetch(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "private.rlog.zst"
      path.write_bytes(b"fixture")
      self.assertEqual(first_log_identifier(parse_replay_args([str(path)])), str(path))
      self.assertIsNotNone(first_log_identifier(parse_replay_args(["--demo"])))

  def test_data_dir_uses_native_timestamp_segment_layout_without_remote_fallback(self):
    route = "0123456789abcdef/2024-01-02--03-04-05/0"
    with tempfile.TemporaryDirectory() as directory:
      segment = Path(directory) / "2024-01-02--03-04-05--0"
      segment.mkdir()
      local = segment / "rlog.zst"
      local.write_bytes(b"fixture")
      args = parse_replay_args(["--data_dir", directory, route])
      self.assertEqual(first_log_identifier(args), str(local))
      local.unlink()
      self.assertIsNone(first_log_identifier(args))

  def test_seed_recorded_typed_settings_and_preserve_explicit_choices(self):
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OPENPILOT_PREFIX="replay-test-seed"):
      params = Params(directory)
      entries = [SimpleNamespace(key="IsMetric", value=b"1"), SimpleNamespace(key="HideSpeed", value=b"0"),
                 SimpleNamespace(key="HideMaxSpeed", value=b"not-a-bool"),
                 SimpleNamespace(key="CameraView", value=b"3"),
                 SimpleNamespace(key="OpenpilotEnabledToggle", value=b"0"),
                 SimpleNamespace(key="ConditionalModeConfig", value=b'{"version":1,"mode":"cem"}'),
                 SimpleNamespace(key="LaneCenteringStrength", value=b"nan"),
                 SimpleNamespace(key="CarParamsPersistent", value=b"unversioned"),
                 SimpleNamespace(key="AccessToken", value=b"secret")]
      init = SimpleNamespace(params=SimpleNamespace(entries=entries))
      self.assertEqual(seed_preview(init, params), 5)
      self.assertIs(params.get("IsMetric"), True)
      self.assertIs(params.get("HideSpeed"), False)
      self.assertIsNone(params.get("HideMaxSpeed"))
      self.assertIsNone(params.get("AccessToken"))
      self.assertIsNone(params.get("CarParamsPersistent"))
      self.assertIsNone(params.get("LaneCenteringStrength"))
      self.assertEqual(params.get("CameraView"), 3)
      self.assertEqual(params.get("ConditionalModeConfig"), {"version": 1, "mode": "cem"})
      self.assertIs(params.get("OpenpilotEnabledToggle"), False)

  def test_saved_snapshot_supplements_unlogged_preferences_without_modifying_source(self):
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OPENPILOT_PREFIX="replay-test-snapshot"):
      root = Path(directory)
      saved = root / "saved"
      saved.mkdir()
      raw = b'{"version":1,"mode":"stock"}'
      (saved / "ConditionalModeConfig").write_bytes(raw)
      (saved / "HideSpeed").write_bytes(b"1")
      (saved / "AccessToken").write_bytes(b"secret")
      params = Params(str(root / "target"))
      init = SimpleNamespace(params=SimpleNamespace(entries=[SimpleNamespace(key="HideSpeed", value=b"0")]))
      self.assertEqual(seed_preview(init, params, str(saved)), 3)
      self.assertIs(params.get("HideSpeed"), True)
      self.assertEqual(params.get("ConditionalModeConfig"), {"version": 1, "mode": "stock"})
      self.assertIsNone(params.get("AccessToken"))
      self.assertEqual((saved / "ConditionalModeConfig").read_bytes(), raw)
      plan = parse_onroad_args(["--params", str(saved), "--demo"])
      self.assertEqual(plan.params_snapshot, str(saved))
      self.assertNotIn("--params", plan.replay_args)

  def test_saved_snapshot_rejects_symlinks_and_replay_device_keeps_tizi_identity(self):
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OPENPILOT_PREFIX="replay-test-snapshot"):
      root = Path(directory)
      saved = root / "saved"
      saved.mkdir()
      (saved / "IsMetric").symlink_to(root / "missing")
      with self.assertRaises(OSError):
        seed_preview(None, Params(str(root / "target")), str(saved))
    self.assertEqual(replay_device_type(SimpleNamespace(deviceType="tizi")), "tizi")
    self.assertEqual(replay_device_type(SimpleNamespace(deviceType="mici")), "mici")
    self.assertEqual(replay_device_type(None), "pc")

  def test_seed_valid_cache_retains_its_existing_schema_provenance(self):
    from opendbc.car import structs as car
    from openpilot.starpilot.schema_cache import get_cache, put_cache
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OPENPILOT_PREFIX="replay-test-cache"):
      source = Params(str(Path(directory) / "source"))
      cp = car.CarParams.new_message(carFingerprint="HYUNDAI IONIQ 6 2023", openpilotLongitudinalControl=True)
      put_cache(source, "CarParamsPersistent", cp, block=True)
      raw = Path(source.get_param_path("CarParamsPersistent")).read_bytes()
      target = Params(str(Path(directory) / "target"))
      init = SimpleNamespace(params=SimpleNamespace(entries=[SimpleNamespace(key="CarParamsPersistent", value=raw)]))
      self.assertEqual(seed_preview(init, target), 1)
      with car.CarParams.from_bytes(get_cache(target, "CarParamsPersistent")) as restored:
        self.assertEqual(restored.carFingerprint, cp.carFingerprint)
        self.assertTrue(restored.openpilotLongitudinalControl)
      self.assertEqual(Path(target.get_param_path("CarParamsPersistent")).read_bytes(), raw)

  def test_seed_uses_replay_namespace_not_runner_namespace(self):
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OPENPILOT_PREFIX="starpilot-dev-original"):
      init = SimpleNamespace(params=SimpleNamespace(entries=[SimpleNamespace(key="IsMetric", value=b"1")]))
      self.assertEqual(seed_replay_params(init, directory, "replay-target"), 1)
      self.assertEqual(os.environ["OPENPILOT_PREFIX"], "starpilot-dev-original")
      self.assertEqual((Path(directory) / "replay-target" / "IsMetric").read_bytes(), b"1")
      self.assertFalse((Path(directory) / "starpilot-dev-original").exists())

  def test_replay_only_does_not_fetch_route_metadata(self):
    for ui_args in (["--replay-only"], ["--ui=none"]):
      with self.subTest(ui_args=ui_args), tempfile.TemporaryDirectory() as directory:
        private = Path(directory) / "params"
        env = {"SP_HOST_RUNTIME": "1", "SP_HOST_PARAMS_ROOT": str(private), "PARAMS_ROOT": str(private),
               "SP_HOST_PREFIX": "starpilot-dev-host", "OPENPILOT_PREFIX": "starpilot-dev-host"}
        with patch.dict(os.environ, env), \
             patch.object(Path, "is_file", return_value=True), \
             patch("openpilot.tools.replay.onroad.os.access", return_value=True), \
             patch("openpilot.tools.replay.onroad_config.route_init_data", side_effect=AssertionError("fetched")), \
             patch("openpilot.tools.replay.onroad.supervise", return_value=0) as owned:
          self.assertEqual(run(parse_onroad_args([*ui_args, "--prefix", "replay-onlyfixture", "--demo"])), 0)
        self.assertEqual(owned.call_count, 1)
        self.assertFalse((Path(directory) / "replay-session-replay-onlyfixture").exists())

  def test_replay_exit_reaps_other_child(self):
    with tempfile.TemporaryDirectory() as directory:
      marker = Path(directory) / "terminated"
      waiting = "; ".join(("import signal,time,sys",
                            "signal.signal(signal.SIGTERM, lambda *_: (open(sys.argv[1], 'w').write('yes'), sys.exit(0)))",
                            "time.sleep(10)"))
      exiting = "import time; time.sleep(.2)"
      result = supervise([sys.executable, "-c", exiting], [[sys.executable, "-c", waiting, str(marker)]], dict(os.environ))
      self.assertEqual(result, 0)
      self.assertEqual(marker.read_text(), "yes")

  def test_termination_reaps_replay_and_ui(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      ready = root / "ready"
      replay_stopped = root / "replay-stopped"
      ui_stopped = root / "ui-stopped"
      worker = "; ".join(("import signal,time,sys", "from pathlib import Path",
                          "signal.signal(signal.SIGTERM, lambda *_: (Path(sys.argv[2]).write_text('stopped'), sys.exit(0)))",
                          "Path(sys.argv[1]).write_text('ready')",
                          "time.sleep(20)"))
      call = "raise SystemExit(supervise([sys.executable,'-c',worker,paths[0],paths[1]]," + \
             "[[sys.executable,'-c',worker,paths[2],paths[3]]],dict(os.environ)))"
      supervisor = "; ".join(("import os,sys", "from openpilot.tools.replay.onroad import supervise",
                              "worker=sys.argv[1]", "paths=sys.argv[2:]", call))
      proc = subprocess.Popen([sys.executable, "-c", supervisor, worker, str(ready), str(replay_stopped),
                               str(root / "ui-ready"), str(ui_stopped)], env=dict(os.environ))
      try:
        deadline = time.monotonic() + 5
        while not (ready.exists() and (root / "ui-ready").exists()) and time.monotonic() < deadline:
          time.sleep(.02)
        self.assertTrue(ready.exists() and (root / "ui-ready").exists())
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=5), 143)
        self.assertEqual(replay_stopped.read_text(), "stopped")
        self.assertEqual(ui_stopped.read_text(), "stopped")
      finally:
        if proc.poll() is None:
          proc.kill()
          proc.wait(timeout=2)


class TestReplayDisplayClock(unittest.TestCase):
  def test_epoch_reset_clears_real_submaster_structs_and_lists(self):
    from openpilot.cereal import messaging
    from openpilot.selfdrive.ui.ui_state import UIState
    state = object.__new__(UIState)
    with patch.dict(os.environ, OPENPILOT_PREFIX=self.prefix):
      state.sm = messaging.SubMaster(['carControl', 'modelV2', 'pandaStates', 'onroadEvents'])
      state.sm.update_msgs(time.monotonic(), [messaging.new_message('carControl', valid=True),
                                             messaging.new_message('modelV2', valid=True),
                                             messaging.new_message('pandaStates', 1, valid=True),
                                             messaging.new_message('onroadEvents', 1, valid=True)])
      state.started = True
      state.chestnut_output_seen = True
      state._reset_replay_state()
    self.assertFalse(state.started or state.chestnut_output_seen)
    self.assertEqual(len(state.sm['pandaStates']), 0)
    self.assertEqual(len(state.sm['onroadEvents']), 0)
    self.assertFalse(any(state.sm.valid.values()))
    self.assertFalse(any(state.sm.seen.values()))
    self.assertFalse(state.sm['carControl'].latActive)

  def test_native_writer_and_python_reader_share_host_clock_and_lifecycle(self):
    source = '''#include <iostream>
#include "tools/replay/display_clock.h"
int main() {
  ReplayDisplayClock clock;
  if (!clock.active()) return 2;
  clock.published(30000000000ULL, 1.0);
  std::cout << "ready" << std::endl;
  char command;
  while (std::cin >> command) {
    if (command == 'p') clock.pause(true);
    if (command == 's') clock.seek(2000000000ULL);
    if (command == 'n') clock.published(2000000000ULL, 1.0);
    if (command == 'o') {
      clock.pause(false);
      clock.published(18446744073709551615ULL, 1.0);
      clock.pause(true);
    }
    if (command == 'b') {
      clock.pause(false);
      clock.published(18446462598732840960ULL, 1.0);
      clock.pause(true);
    }
    if (command == 'q') { clock.stop(); break; }
    std::cout << "done" << std::endl;
  }
}'''
    with tempfile.TemporaryDirectory() as directory:
      cpp, binary = Path(directory) / "clock.cc", Path(directory) / "clock"
      cpp.write_text(source)
      include = Path(__file__).resolve().parents[3]
      compiler = shlex.split(os.environ.get('CXX', 'c++'))
      warnings = ['-Werror', '-Wimplicit-const-int-float-conversion'] if sys.platform == 'darwin' else ['-Werror']
      subprocess.run([*compiler, *warnings, '-std=c++17', '-pthread', '-I', str(include), str(cpp), '-o', str(binary)],
                     check=True, capture_output=True, timeout=30)
      process = subprocess.Popen([str(binary)], env={**os.environ, **self.env}, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
      try:
        self.assertEqual(process.stdout.readline().strip(), 'ready')
        sample = self.reader.sample()
        self.assertTrue(sample.valid)
        assert sample.now_ns is not None
        assert sample.epoch is not None
        self.assertLess(abs(sample.now_ns - 30_000_000_000), 250_000_000)
        process.stdin.write('p\n')
        process.stdin.flush()
        self.assertEqual(process.stdout.readline().strip(), 'done')
        paused = self.reader.sample()
        time.sleep(.3)
        self.assertTrue(self.reader.sample().valid)
        self.assertEqual(self.reader.sample().now_ns, paused.now_ns)
        process.stdin.write('s\n')
        process.stdin.flush()
        self.assertEqual(process.stdout.readline().strip(), 'done')
        seek = self.reader.sample()
        self.assertFalse(seek.valid)
        self.assertEqual(seek.epoch, sample.epoch + 1)
        process.stdin.write('n\n')
        process.stdin.flush()
        self.assertEqual(process.stdout.readline().strip(), 'done')
        self.assertEqual(self.reader.sample().now_ns, 2_000_000_000)
        process.stdin.write('o\n')
        process.stdin.flush()
        self.assertEqual(process.stdout.readline().strip(), 'done')
        self.assertFalse(self.reader.sample().valid)
        process.stdin.write('b\n')
        process.stdin.flush()
        self.assertEqual(process.stdout.readline().strip(), 'done')
        below_overflow = self.reader.sample()
        self.assertTrue(below_overflow.valid)
        assert below_overflow.now_ns is not None
        self.assertGreaterEqual(below_overflow.now_ns, 18_446_462_598_732_840_960)
        self.assertLess(below_overflow.now_ns, 2**64)
        process.stdin.write('q\n')
        process.stdin.flush()
        self.assertEqual(process.wait(timeout=3), 0)
        self.assertFalse(self.reader.sample().valid)
      finally:
        if process.poll() is None:
          process.kill()
          process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
          if stream is not None:
            stream.close()

  def setUp(self):
    from openpilot.tools.replay.display_clock import DisplayClockReader, RECORD
    self.reader_type = DisplayClockReader
    self.record = RECORD
    self.prefix = f"replay-clocktest-{os.getpid()}-{time.time_ns()}"
    self.root = self.enterContext(owned_ipc_namespace(self.prefix))
    self.path = self.root / "display-clock"
    self.path.write_bytes(bytes(RECORD.size))
    self.path.chmod(0o600)
    self.env = {"SP_HOST_RUNTIME": "1", "OPENPILOT_PREFIX": self.prefix, "SP_REPLAY_CLOCK_PATH": str(self.path)}
    self.reader = DisplayClockReader(self.env)
    self.addCleanup(self.reader.close)

  def write(self, **changes):
    from openpilot.tools.replay.display_clock import MAGIC, VALID
    fields = {"sequence": 2, "magic": MAGIC, "version": 1, "flags": VALID, "epoch": 1,
              "route_ns": 30_000_000_000, "host_ns": 10_000_000_000, "speed": 1., "boot_offset": 0,
              "heartbeat": 10_000_000_000, "reserved": 0}
    fields.update(changes)
    self.path.write_bytes(self.record.pack(*fields.values()))

  def test_recorded_time_speed_and_explicit_pause_keep_clock_domains_separate(self):
    from openpilot.tools.replay.display_clock import PAUSED, VALID
    self.write(speed=2.)
    sample = self.reader.sample(10_100_000_000)
    self.assertTrue(sample.valid)
    self.assertEqual(sample.now_ns, 30_200_000_000)
    self.assertEqual(sample.host_ns, 10_100_000_000)
    self.assertIsNone(sample.boot_ns)
    self.write(flags=VALID | PAUSED, route_ns=30_200_000_000, host_ns=10_100_000_000,
               heartbeat=10_400_000_000)
    sample = self.reader.sample(10_500_000_000)
    self.assertTrue(sample.valid and sample.paused)
    self.assertEqual(sample.now_ns, 30_200_000_000)
    self.write(route_ns=30_200_000_000, host_ns=10_500_000_000, heartbeat=10_500_000_000, speed=.5)
    self.assertEqual(self.reader.sample(10_600_000_000).now_ns, 30_250_000_000)

  def test_implicit_host_clock_follows_heartbeat_published_during_snapshot(self):
    from openpilot.tools.replay import display_clock
    self.write()
    original_unpack = display_clock.struct.unpack_from
    reads = 0

    def heartbeat_then_read(*args, **kwargs):
      nonlocal reads
      if reads == 0:
        self.write(heartbeat=10_000_000_001)
      reads += 1
      return original_unpack(*args, **kwargs)

    ticks = iter((10_000_000_000, 10_000_000_001))
    with patch.object(display_clock, 'time', SimpleNamespace(monotonic_ns=lambda: next(ticks))), \
         patch.object(display_clock.struct, 'unpack_from', side_effect=heartbeat_then_read):
      self.assertIsNot(display_clock.time, time)
      sample = self.reader.sample()
    self.assertTrue(sample.valid)
    self.assertEqual(sample.host_ns, 10_000_000_001)
    self.assertEqual(sample.now_ns, 30_000_000_001)
    # Explicit historical observations still reject genuinely future writer timestamps.
    self.assertFalse(self.reader.sample(10_000_000_000).valid)

  def test_unknown_boot_offset_differs_from_valid_zero_and_recorded_suspend_offset(self):
    from openpilot.tools.replay.display_clock import BOOT_KNOWN, VALID
    self.write()
    self.assertIsNone(self.reader.sample(10_000_000_000).boot_ns)
    self.write(flags=VALID | BOOT_KNOWN)
    self.assertEqual(self.reader.sample(10_000_000_000).boot_ns, 30_000_000_000)
    self.write(flags=VALID | BOOT_KNOWN, boot_offset=9_000_000_000)
    self.assertEqual(self.reader.sample(10_000_000_000).boot_ns, 39_000_000_000)

  def test_seek_epoch_is_visible_but_invalid_until_new_publication(self):
    from openpilot.tools.replay.display_clock import SEEKING, VALID
    self.write()
    self.assertEqual(self.reader.sample(10_000_000_000).epoch, 1)
    self.write(flags=SEEKING, epoch=2, route_ns=2_000_000_000)
    sample = self.reader.sample(10_000_000_000)
    self.assertFalse(sample.valid)
    self.assertEqual(sample.epoch, 2)
    self.assertIsNone(sample.now_ns)
    self.write(flags=VALID, epoch=2, route_ns=2_000_000_000)
    sample = self.reader.sample(10_100_000_000)
    self.assertTrue(sample.valid)
    self.assertEqual((sample.epoch, sample.now_ns), (2, 2_100_000_000))

  def test_dead_publisher_torn_record_and_invalid_metadata_fail_closed(self):
    from openpilot.tools.replay.display_clock import BOOT_KNOWN, HEARTBEAT_MAX_AGE_NS, VALID
    self.write()
    self.assertTrue(self.reader.sample(10_000_000_000 + HEARTBEAT_MAX_AGE_NS).valid)
    self.assertFalse(self.reader.sample(10_000_000_001 + HEARTBEAT_MAX_AGE_NS).valid)
    for changes in ({"sequence": 3}, {"sequence": 0}, {"magic": 0}, {"version": 2}, {"flags": 16},
                    {"epoch": 0}, {"speed": float("nan")}, {"speed": 0}, {"reserved": 1},
                    {"heartbeat": 10_000_000_001}, {"host_ns": 10_000_000_001},
                    {"flags": VALID | BOOT_KNOWN, "boot_offset": -1}):
      with self.subTest(changes=changes):
        self.write(**changes)
        self.assertFalse(self.reader.sample(10_000_000_000).valid)

  def test_only_exact_owned_context_path_regular_file_size_and_modes_are_admitted(self):
    self.reader.close()
    self.write()
    for changes in ({"SP_HOST_RUNTIME": "0"}, {"OPENPILOT_PREFIX": "replay-other"},
                    {"SP_REPLAY_CLOCK_PATH": str(self.root / "other")}, {"OPENPILOT_PREFIX": "../replay-other"}):
      with self.subTest(changes=changes):
        self.assertFalse(self.reader_type.enabled({**self.env, **changes}))
        reader = self.reader_type({**self.env, **changes})
        self.assertFalse(reader.sample(10_000_000_000).valid)
        reader.close()
    self.path.chmod(0o644)
    reader = self.reader_type(self.env)
    self.assertFalse(reader.sample(10_000_000_000).valid)
    reader.close()

    self.path.chmod(0o600)
    for size in (self.record.size - 1, self.record.size + 1):
      self.path.write_bytes(bytes(size))
      reader = self.reader_type(self.env)
      self.assertFalse(reader.sample(10_000_000_000).valid)
      reader.close()
    self.write()
    self.root.chmod(0o750)
    reader = self.reader_type(self.env)
    self.assertFalse(reader.sample(10_000_000_000).valid)
    reader.close()
    self.root.chmod(0o700)
    target = self.root / "other"
    self.path.rename(target)
    self.path.symlink_to(target)
    reader = self.reader_type(self.env)
    self.assertFalse(reader.sample(10_000_000_000).valid)
    reader.close()
    self.path.unlink()
    target.rename(self.path)
    os.link(self.path, target)
    reader = self.reader_type(self.env)
    self.assertFalse(reader.sample(10_000_000_000).valid)
    reader.close()

  def test_enabled_context_stays_enabled_when_clock_file_is_missing(self):
    self.reader.close()
    self.path.unlink()
    self.assertTrue(self.reader_type.enabled(self.env))
    reader = self.reader_type(self.env)
    self.assertFalse(reader.sample(10_000_000_000).valid)
    reader.close()
