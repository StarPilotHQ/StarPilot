import unittest

from openpilot.starpilot.speed_limits.vision.tests.test_stop_observation import evidence, message, BASE, OFFSET
from openpilot.starpilot.ui.tests.test_runtime_snapshot import ui_fake, RuntimeSnapshotAdapter
from openpilot.starpilot.ui.shell import ShellMode


class TestStopSignSnapshot(unittest.TestCase):
  def test_actual_snapshot_confirms_and_clears_fresh_negative(self):
    ui = ui_fake()
    ui.sm.messages['deviceState'].startedMonoTime = BASE - 1_000_000_000
    clock = {'now': BASE}
    adapter = RuntimeSnapshotAdapter(ui, mono_clock=lambda: clock['now'], boot_clock=lambda: clock['now'] + OFFSET)
    for i, boxes in enumerate([evidence().boxes, evidence().boxes, ()]):
      stamp = BASE + i * 500_000_000
      sample = evidence(stamp, frame=1 + i * 10, boxes=boxes)
      event = message(sample, now=stamp + 100_000_000)
      ui.sm.put('slcVisionObservation', event.slcVisionObservation)
      ui.sm.logMonoTime['slcVisionObservation'] = stamp + 100_000_000
      ui.sm.recv_time['slcVisionObservation'] = (stamp + 100_000_000) / 1e9
      clock['now'] = stamp + 100_000_000
      result = adapter.build(ShellMode.ONROAD, now_ns=stamp + 100_000_000)
      self.assertEqual(bool(result.onroad.stop_sign_observed_ns), i == 1)
