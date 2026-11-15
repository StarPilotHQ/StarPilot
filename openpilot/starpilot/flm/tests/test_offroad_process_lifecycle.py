"""Process-mode regressions; declared lifecycle/heap, no vehicle or actuator."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpilot.common.params import Params
from openpilot.starpilot.flm import offroad_daemon as daemon
from openpilot.starpilot.flm.offroad_store import KEY


class TestParkedProcessLifetime(unittest.TestCase):
  def test_driving_imports_do_not_load_the_parked_optimizer(self):
    code = '''
import sys
from openpilot.starpilot.flm import offroad_daemon, offroad_quality, offroad_source
from openpilot.starpilot.flm.calibration_lifecycle import configuration_calibration
from openpilot.starpilot.flm.runtime_adapter_registry import read_source
for name in ('offroad_worker', 'offroad_processor', 'offroad_stable_bridge', 'steerlearnerd', 'controller_replay', 'pid_controller_replay'):
  assert 'openpilot.starpilot.flm.' + name not in sys.modules, name
assert 'opendbc.car.car_helpers' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=15)
    self.assertEqual(result.returncode, 0, result.stderr)

  def test_no_saved_tune_does_not_construct_controllers_for_a_guard(self):
    from openpilot.starpilot.flm.offroad_quality import from_configuration
    with tempfile.TemporaryDirectory() as root:
      params = Params(root)
      with patch('openpilot.starpilot.flm.offroad_runtime.current_scope', side_effect=AssertionError('No saved map')):
        self.assertIsNone(from_configuration(None, params, Path(root) / 'cache', vehicle_session='a' * 32))

  def test_a_quality_and_status_tick_decode_the_same_original_packet_once(self):
    from openpilot.starpilot.flm.offroad_source import record_drive

    for malformed in (False, True):
      with self.subTest(malformed=malformed):
        now, finished, reports = 1_000_000_000, [], []
        packet = object()
        control = SimpleNamespace(valid=True, active=True, manual=False, override=False, fault=False, limited=False,
                                  control_ns=now, route_applied_coordinates=(('declared', .01),))
        guard = SimpleNamespace(stopped=None, marker=None, observe=Mock())

        class Receiver:
          def __init__(self, services, packet=packet, now=now):
            self.original = {'starpilotLateralState': packet, 'controlsState': object()}
            self.logMonoTime = {'controlsState': now}
            self.updated = {'controlsState': True}
            self.sock = {}

          def update(self, timeout):
            pass

        pm = SimpleNamespace(sock={}, send=lambda *args, finished=finished: finished.append(True))
        message = SimpleNamespace(autoSteeringLearning=SimpleNamespace(learning=SimpleNamespace(reason='')))
        params = SimpleNamespace(get=lambda key: b'declared-cp', get_bool=lambda key: False)
        source = SimpleNamespace(enabled=True, observed_ns=now, expires_ns=now + 500_000_000)
        with patch('openpilot.starpilot.flm.offroad_source.messaging.SubMaster', Receiver), \
             patch('openpilot.starpilot.flm.offroad_source.messaging.PubMaster', return_value=pm), \
             patch('openpilot.starpilot.flm.offroad_source.messaging.log_from_bytes', return_value=object()), \
             patch('openpilot.starpilot.flm.offroad_source.time.monotonic_ns', return_value=now), \
             patch('openpilot.starpilot.flm.runtime_adapter_registry.car_params_digest', return_value='a' * 64), \
             patch('openpilot.starpilot.flm.runtime_adapter_registry.read_source', return_value=source), \
             patch('openpilot.starpilot.flm.calibration_lifecycle.configuration_calibration', return_value=None), \
             patch('openpilot.starpilot.flm.offroad_cache.directory_for_params', return_value=Path('/declared-cache')), \
             patch('openpilot.starpilot.flm.offroad_quality.from_configuration', return_value=guard), \
             patch('openpilot.starpilot.flm.learning_wire.result_message', return_value=message), \
             patch('openpilot.starpilot.flm.learning_wire.write_source_receipt'), \
             patch('openpilot.starpilot.flm.learning_wire.read_control', side_effect=ValueError('malformed') if malformed else None,
                   return_value=control) as decode:
          record_drive(params, stopped=lambda finished=finished: bool(finished), on_status=lambda *args, reports=reports: reports.append(args))
        decode.assert_called_once_with(packet)
        self.assertIs(guard.observe.call_args.args[2], None if malformed else control)
        self.assertIs(reports[0][1], None if malformed else control)

  def exercise_transition(self, *, starts_parked, interrupted=None, cancel_on_cleanup=False):
    events = []
    prior_signals = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    with tempfile.TemporaryDirectory() as root:
      params = Params(root)
      params.put_bool('IsOffroad', starts_parked, block=True)
      params.put(KEY, b'declared-existing-saved-tune')
      def close_socket(i):
        events.append(('closed', i))
        if cancel_on_cleanup and i == 0:
          handler = signal.getsignal(signal.SIGTERM)
          if not callable(handler):
            raise AssertionError('Cleanup cancellation handler missing')
          handler(signal.SIGTERM, None)

      sockets = {str(i): SimpleNamespace(close=lambda i=i: close_socket(i)) for i in range(3)}
      sm = SimpleNamespace(sock=sockets)

      class Worker:
        def __init__(self, *args, permitted, **kwargs):
          events.append('parked-worker')
          self.permitted = permitted
          self.cache = object()

        def run_once(self):
          child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
          try:
            if interrupted is None:
              params.put_bool('IsOffroad', False, block=True)
            else:
              handler = signal.getsignal(interrupted)
              if not callable(handler):
                raise AssertionError("Cancellation handler missing")
              handler(interrupted, None)
            self_cancelled = not self.permitted()
            events.append(('cancelled', self_cancelled))
            if not self_cancelled:
              raise AssertionError('Cancellation must reach the original worker')
            raise ValueError('Declared interrupted analysis')
          finally:
            child.terminate()
            child.wait(timeout=3)
            events.append('child-reaped')

        def record_failure(self, error):
          return {'status': 'collecting', 'reason': 'declared-cancelled'}

      def drive(*args, **kwargs):
        events.append('driving-source')
        self.assertNotIn('parked-worker', events, 'A cold driving start must not construct the optimizer')
        params.put_bool('IsOffroad', True, block=True)

      report = SimpleNamespace(restore=lambda cache: events.append('history-restored'), set=lambda value: None,
                               publish=lambda: None, analyzing=lambda value: None)
      with patch.object(daemon, 'Params', return_value=params), patch.object(daemon.messaging, 'SubMaster', return_value=sm), \
           patch.object(daemon, 'StatusReport', return_value=report), \
           patch.object(daemon, 'wait_for_parked', side_effect=lambda *a, stopped, **k: not stopped() and params.get_bool('IsOffroad')), \
           patch.dict(sys.modules, {'openpilot.starpilot.flm.offroad_worker': SimpleNamespace(OffroadWorker=Worker)}), \
           patch('openpilot.starpilot.flm.offroad_source.record_drive', side_effect=drive):
        restart = daemon._run()
      self.assertEqual(params.get(KEY), b'declared-existing-saved-tune')
      self.assertIn(('cancelled', True), events)
      self.assertIn('child-reaped', events)
      self.assertEqual(sum(isinstance(e, tuple) and e[0] == 'closed' for e in events), 3)
    self.assertEqual({s: signal.getsignal(s) for s in prior_signals}, prior_signals)
    return restart, events

  def test_ignition_cancels_reaps_and_requests_a_clean_driving_image(self):
    restart, events = self.exercise_transition(starts_parked=True)
    self.assertTrue(restart)
    self.assertNotIn('driving-source', events, 'The analysis image must never enter driving collection')

  def test_driving_to_parked_starts_a_new_worker_and_restores_history(self):
    restart, events = self.exercise_transition(starts_parked=False)
    self.assertTrue(restart)
    self.assertLess(events.index('driving-source'), events.index('parked-worker'))
    self.assertEqual(events.count('history-restored'), 1)

  def test_term_int_and_hup_cancel_without_reexec_or_losing_saved_tunes(self):
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
      with self.subTest(signal=sig):
        restart, _ = self.exercise_transition(starts_parked=True, interrupted=sig)
        self.assertFalse(restart)

  def test_shutdown_during_cleanup_cancels_an_already_requested_restart(self):
    restart, events = self.exercise_transition(starts_parked=True, cancel_on_cleanup=True)
    self.assertFalse(restart)
    self.assertIn('child-reaped', events)

  def test_reexec_uses_the_existing_manager_launcher(self):
    with patch.object(sys, 'argv', ['-c', daemon._CLEAN_IMAGE]), \
         patch.object(daemon, '_run', return_value=True), patch.object(daemon.os, 'execv') as execute:
      daemon.main()
      executable, argv = execute.call_args.args
      self.assertEqual(executable, sys.executable)
      self.assertEqual(argv[:2], [sys.executable, '-c'])
      self.assertEqual(argv[-1], daemon._CLEAN_IMAGE)
      self.assertIn("launcher('openpilot.starpilot.flm.offroad_daemon', 'steerlearnerd')", argv[2])
    with patch.object(sys, 'argv', ['-c', daemon._CLEAN_IMAGE]), \
         patch.object(daemon, '_run', return_value=None), patch.object(daemon.os, 'execv') as execute:
      daemon.main()
      execute.assert_not_called()

  def test_cold_manager_fork_is_replaced_before_loading_either_mode(self):
    with patch.object(sys, 'argv', ['manager.py']), patch.object(daemon, '_run') as run, patch.object(daemon.os, 'execv') as execute:
      daemon.main()
      run.assert_not_called()
      self.assertEqual(execute.call_count, 1)
      self.assertEqual(execute.call_args.args[1][-1], daemon._CLEAN_IMAGE)

  def test_exec_failure_does_not_fall_back_to_collecting_in_the_parked_image(self):
    with patch.object(sys, 'argv', ['-c', daemon._CLEAN_IMAGE]), patch.object(daemon, '_run', return_value=True) as run, \
         patch.object(daemon.os, 'execv', side_effect=OSError('declared exec failure')):
      with self.assertRaises(OSError):
        daemon.main()
      run.assert_called_once()

  @unittest.skipUnless(sys.platform == 'linux', 'Actual manager/process/IPC lifecycle')
  def test_manager_tracks_the_same_pid_and_can_stop_the_exec_replaced_child(self):
    from openpilot.system.manager.process import PythonProcess

    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory(prefix='msgq_flm-manager-', dir='/dev/shm') as ipc:
      prefix = Path(ipc).name.removeprefix('msgq_')
      report = Path('/dev/shm') / ('starpilot-flm-training-' + prefix + '.json')
      with patch.dict(os.environ, {'OPENPILOT_PREFIX': prefix, 'PARAMS_ROOT': root}):
        params = Params(root)
        params.put_bool('IsOffroad', False, block=True)
        params.put(KEY, b'declared-preserved-manager-store')
        process = PythonProcess('steerlearnerd', 'openpilot.starpilot.flm.offroad_daemon', lambda *args: True, restart_on_exit=True)
        process.start()
        child = process.proc
        self.assertIsNotNone(child)
        assert child is not None
        pid = child.pid
        try:
          deadline = time.monotonic() + 20
          while time.monotonic() < deadline:
            self.assertTrue(child.is_alive(), 'Manager lost its child during exec')
            try:
              state = json.loads(report.read_text())
            except (OSError, ValueError):
              state = {}
            if state.get('current', {}).get('reason') == 'recording_for_offroad_analysis':
              break
            time.sleep(.02)
          else:
            self.fail('Manager-launched driving image did not become ready')
          self.assertTrue(process.recover())
          self.assertIs(process.proc, child)
          self.assertEqual(process.proc.pid, pid)
          self.assertIsNone(child.exitcode)
          self.assertEqual(params.get(KEY), b'declared-preserved-manager-store')
        finally:
          process.stop(sig=signal.SIGTERM)
          report.unlink(missing_ok=True)
        self.assertEqual(child.exitcode, 0)
        self.assertIsNone(process.proc)

  @unittest.skipUnless(sys.platform == 'linux', 'Real Linux exec/RSS/IPC lifecycle')
  def test_actual_exec_releases_a_declared_parked_heap_and_preserves_pid_and_store(self):
    # A deliberate heap demonstrates allocator/data release, independently of
    # any claim about an EV6's actual analysis size or driving CPU workload.
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory(prefix='msgq_flm-exec-', dir='/dev/shm') as ipc:
      prefix = Path(ipc).name.removeprefix('msgq_')
      with patch.dict(os.environ, {'OPENPILOT_PREFIX': prefix}):
        params = Params(root)
      params.put_bool('IsOffroad', True, block=True)
      params.put(KEY, b'declared-retained-store')
      report = Path('/dev/shm') / ('starpilot-flm-training-' + prefix + '.json')
      environment = dict(os.environ, PARAMS_ROOT=root, OPENPILOT_PREFIX=prefix)
      code = '''
import json,sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
from openpilot.common.params import Params
from openpilot.starpilot.flm import offroad_daemon as daemon
params=Params()
sys.argv.append(daemon._CLEAN_IMAGE)  # The declared heap belongs to an already-clean parked image.
class Worker:
 def __init__(self,*args,**kwargs):
  self.cache=object(); self.heap=bytearray(160*1024*1024)
 def run_once(self):
  rss=int(next(s for s in open('/proc/self/status') if s.startswith('VmRSS:')).split()[1])
  print(json.dumps({'before_kib':rss}),flush=True)
  params.put_bool('IsOffroad',False,block=True)
  return {'status':'collecting','reason':'declared-transition'}
sm=SimpleNamespace(sock={})
with patch.object(daemon.messaging,'SubMaster',return_value=sm), patch.object(daemon,'wait_for_parked',return_value=True), \\
 patch.object(daemon.StatusReport,'restore',return_value=None), \\
 patch.dict(sys.modules,{'openpilot.starpilot.flm.offroad_worker':SimpleNamespace(OffroadWorker=Worker)}):
 daemon.main()
'''
      child = subprocess.Popen([sys.executable, '-B', '-c', code], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
      try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
          if child.poll() is not None:
            self.fail('Lifecycle child exited early: ' + child.stderr.read()[-2000:])
          try:
            state = json.loads(report.read_text())
          except (OSError, ValueError):
            state = {}
          if state.get('current', {}).get('reason') == 'recording_for_offroad_analysis':
            break
          time.sleep(.02)
        else:
          self.fail('Fresh driving image did not publish its status')
        after = int(next(s for s in Path(f'/proc/{child.pid}/status').read_text().splitlines() if s.startswith('VmRSS:')).split()[1])
        self.assertEqual(params.get(KEY), b'declared-retained-store')
      finally:
        child.terminate()
        try:
          output, error = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
          child.kill()
          output, error = child.communicate(timeout=3)
        report.unlink(missing_ok=True)
      self.assertEqual(child.returncode, 0, error[-2000:])
      before = next(json.loads(line)['before_kib'] for line in output.splitlines() if line.startswith('{"before_kib"'))
      self.assertGreater(before - after, 96 * 1024, f'Parked heap retained: before={before}KiB after={after}KiB')
