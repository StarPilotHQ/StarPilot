import unittest

from openpilot.starpilot.speed_limits.vision.stop_observation import StopBox, StopConfirmation
from openpilot.starpilot.speed_limits.vision.tests.test_stop_observation import BASE, DRIVE, OFFSET, Input, evidence, message
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import RuntimeSnapshotAdapter, ui_fake


class TestStopDisplaySnapshot(unittest.TestCase):
  def test_actual_snapshot_accepts_approaching_pair_without_control_authority(self):
    for pair in (((.6765813, (874, 338, 914, 379)), (.7417721, (1018, 289, 1089, 358))),
                 ((.6999748, (794, 338, 830, 380)), (.7953088, (924, 302, 988, 367)))):
      with self.subTest(pair=pair):
        ui = ui_fake()
        ui.sm.messages['deviceState'].startedMonoTime = DRIVE
        clock = {'now': BASE}
        adapter = RuntimeSnapshotAdapter(ui, mono_clock=lambda clock=clock: clock['now'], boot_clock=lambda clock=clock: clock['now'] + OFFSET)
        control = StopConfirmation()
        for i, (confidence, box) in enumerate(pair):
          stamp = BASE + i * 1_100_000_000
          sample = evidence(stamp, frame=1 + i * 22, boxes=(StopBox(confidence, box),))
          now = stamp + 500_000_000
          event = message(sample, now=now)
          ui.sm.put('slcVisionObservation', event.slcVisionObservation, frame=3 + i)
          ui.sm.logMonoTime['slcVisionObservation'] = now
          ui.sm.recv_time['slcVisionObservation'] = now / 1e9
          clock['now'] = now
          result = adapter.build(ShellMode.ONROAD, now_ns=now)
          self.assertEqual(bool(result.onroad.stop_sign_observed_ns), i == 1)
          self.assertEqual(control.observe_message(Input(event), now_ns=now, now_boot_ns=now + OFFSET, drive_id=DRIVE), 0)
        ui.sm.alive['slcVisionObservation'] = False
        self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=clock['now']).onroad.stop_sign_observed_ns, 0)
