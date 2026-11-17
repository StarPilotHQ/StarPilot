from unittest import TestCase, mock

from openpilot.system.hardware import hardware_timing as timing


class TestHardwareTiming(TestCase):
  def test_slow_phase_preserves_original_wall_and_thread_cpu_clocks(self):
    for elapsed_ns, reported in ((499_999_999, False), (500_000_000, True), (5_100_019_763, True)):
      with self.subTest(elapsed_ns=elapsed_ns), \
           mock.patch.object(timing.time, 'monotonic_ns', side_effect=[1_000_000_000, 1_000_000_000 + elapsed_ns]), \
           mock.patch.object(timing.time, 'thread_time_ns', side_effect=[80_000_000, 90_000_000]), \
           mock.patch.object(timing.cloudlog, 'event') as report:
        watch = timing.HardwareLoopTiming()
        watch.mark('thermal_read')
        report.assert_not_called()
        watch.mark('system_stats')
        self.assertEqual(bool(report.call_count), reported)
        if reported:
          report.assert_called_once_with('hardwared.slow_phase', phase='thermal_read', elapsedMs=elapsed_ns / 1e6,
            threadCpuMs=10., error=True)
        self.assertEqual(watch.phase, 'system_stats')

  def test_logging_failure_does_not_interrupt_hardware_work(self):
    with mock.patch.object(timing.time, 'monotonic_ns', side_effect=[1, 5_100_000_001]), \
         mock.patch.object(timing.time, 'thread_time_ns', side_effect=[1, 10_000_001]), \
         mock.patch.object(timing.cloudlog, 'event', side_effect=OSError('logging unavailable')):
      watch = timing.HardwareLoopTiming()
      watch.mark('power_read')
      watch.mark('publish')
      self.assertEqual(watch.phase, 'publish')
      self.assertEqual(watch.wall_ns, 5_100_000_001)
