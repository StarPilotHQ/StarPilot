import threading
import time
import unittest

from openpilot.system.hardware.power_telemetry import PowerTelemetry


class TestPowerTelemetry(unittest.TestCase):
  def test_fresh_expired_and_backwards_clock(self):
    now = [1.0]
    end = threading.Event()
    def read():
      end.set()
      return 3.25
    telemetry = PowerTelemetry(read, 0.5, lambda: now[0])
    self.assertEqual(telemetry.value(), 0)
    telemetry.run(end)
    self.assertEqual(telemetry.value(), 3.25)
    now[0] = 2.01
    self.assertEqual(telemetry.value(), 0)
    now[0] = 0.9
    self.assertEqual(telemetry.value(), 0)

  def test_slow_result_is_not_fresh_on_return(self):
    now = [1.0]
    end = threading.Event()
    def read():
      now[0] += 4.557
      end.set()
      return 7.0
    telemetry = PowerTelemetry(read, 0.5, lambda: now[0])
    telemetry.run(end)
    self.assertEqual(telemetry.value(), 0)

  def test_blocked_reader_does_not_block_publisher_or_spawn_reads(self):
    entered, release, end = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def read():
      calls.append(1)
      entered.set()
      release.wait()
      return 2.0
    telemetry = PowerTelemetry(read, 0.01)
    worker = threading.Thread(target=telemetry.run, args=(end,), daemon=True)
    worker.start()
    try:
      self.assertTrue(entered.wait(1))
      # Stand-in health publisher ticks progress while the hardware read remains
      # blocked. No wait/future/result operation belongs to the publisher.
      start = time.monotonic()
      for _ in range(1000):
        self.assertEqual(telemetry.value(), 0)
      self.assertLess(time.monotonic() - start, 0.5)
      self.assertEqual(len(calls), 1)
      end.set()
      self.assertTrue(worker.is_alive())
    finally:
      end.set()
      release.set()
      worker.join(1)
    self.assertFalse(worker.is_alive())
    self.assertEqual(len(calls), 1)

  def test_exception_recovers(self):
    end = threading.Event()
    calls = []
    def read():
      calls.append(1)
      if len(calls) == 1:
        raise OSError('unavailable')
      end.set()
      return 4.0
    telemetry = PowerTelemetry(read, 0.01)
    telemetry.run(end)
    self.assertEqual(len(calls), 2)
    self.assertEqual(telemetry.value(), 4.0)

  def test_nonfinite_unavailable(self):
    for value in (float('nan'), float('inf'), float('-inf')):
      end = threading.Event()
      def read(end=end, value=value):
        end.set()
        return value
      telemetry = PowerTelemetry(read, 0.5)
      telemetry.run(end)
      self.assertEqual(telemetry.value(), 0)

  def test_stopped_worker_does_not_read(self):
    end = threading.Event()
    end.set()
    def read():
      self.fail('read after shutdown')
    PowerTelemetry(read, 0.5).run(end)

  def test_health_publication_and_shutdown_continue_during_stale_read(self):
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock

    now = [1.0]
    entered, release, end = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def read():
      calls.append(1)
      if len(calls) == 1:
        return 5.0
      entered.set()
      release.wait()
      return 6.0
    telemetry = PowerTelemetry(read, 0.01, lambda: now[0])
    worker = threading.Thread(target=telemetry.run, args=(end,), daemon=True)
    worker.start()
    try:
      self.assertTrue(entered.wait(1))
      self.assertEqual(telemetry.value(), 5.0)
      now[0] = 6.0
      self.assertEqual(telemetry.value(), 0.0)
      source = Path(__file__).parents[1] / 'hardwared.py'
      tree = ast.parse(source.read_text())
      function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'hardware_thread')
      loop = next(node for node in function.body if isinstance(node, ast.While))
      first = next(i for i, node in enumerate(loop.body) if ast.unparse(node) == "timing.mark('som_power')")
      last = next(i for i, node in enumerate(loop.body) if ast.unparse(node) == "pm.send('deviceState', msg)")
      code = compile(ast.Module(body=loop.body[first:last + 1], type_ignores=[]), str(source), 'exec')
      for shutdown in (False, True):
        msg = SimpleNamespace(deviceState=SimpleNamespace())
        power_monitor, params, publisher = Mock(), Mock(), Mock()
        power_monitor.should_shutdown.return_value = shutdown
        params.get.return_value = None
        namespace = {'power_telemetry': telemetry, 'timing': Mock(), 'msg': msg, 'power_monitor': power_monitor,
                         'params': params, 'pm': publisher, 'cloudlog': Mock(), 'onroad_conditions': {'ignition': True},
                         'in_car': True, 'physical_off_ts': None, 'forced_power_episode': False, 'off_ts': None,
                         'started_seen': True, 'started_ts': 10.0, 'thermal_status': 0}
        exec(code, namespace)
        self.assertEqual(msg.deviceState.somPowerDrawW, 0.0)
        self.assertTrue(msg.deviceState.started)
        power_monitor.should_shutdown.assert_called_once_with(True, True, None, True)
        publisher.send.assert_called_once_with('deviceState', msg)
        if shutdown:
          params.put_bool.assert_called_once_with('DoShutdown', True, block=True)
        else:
          params.put_bool.assert_not_called()
    finally:
      end.set()
      release.set()
      worker.join(1)
    self.assertFalse(worker.is_alive())
