"""Fresh Traffic receipts affect only the read-only C3/C4 status display."""

from dataclasses import replace
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from openpilot.starpilot.ui.raylib import rl

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.conditional_mode.manual import read_button_map
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
from openpilot.starpilot.conditional_mode.status import settings_fingerprint
from openpilot.starpilot.ui.onroad_compact_widgets import MiciSidebarWidgets
from openpilot.starpilot.ui.onroad import OnroadView, axis_status_color
from openpilot.starpilot.ui.presentation import FontRole, Profile
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import ui_fake, BOOT_OFFSET_NS
from openpilot.starpilot.ui.traffic_status import TrafficDisplayProjector


NOW = 10_000_000_000
BOOT = NOW + 2_000_000_000
DRIVE = NOW - 1_000_000_000
SESSION = 'a' * 32


def receipt(settings_hash, map_hash, *, sequence=1, session=SESSION, reason='active',
            accepted=True, effective=True, ready=True, observed=NOW, drive=DRIVE,
            source_boot=BOOT - 5_000_000):
  event = messaging.new_message('slcState', valid=False)
  event.logMonoTime = observed - 1_000_000  # Outer SLC clock/validity belongs to another feature.
  event.slcState.trafficMode = {
    'version': 1, 'sessionId': session, 'sequence': sequence,
    'observedMonoTime': observed, 'validUntilMonoTime': observed + 100_000_000,
    'driveStartMonoTime': drive, 'sourceEpoch': 1, 'sourceBootTime': source_boot,
    'accepted': accepted, 'effective': effective, 'reason': reason,
    'settingsFingerprint': settings_hash, 'buttonMapFingerprint': map_hash,
    'profileTargetReady': ready, 'profileReason': 'qualified' if ready else 'unsupported_traffic_follow_below_effective_floor',
  }
  return messaging.log_from_bytes(event.to_bytes())


class TestTrafficStatus(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.params.put('ModeButtonControl', 6, block=True)
    self.settings_hash = settings_fingerprint(ConditionalSettingsOwner(self.params).refresh(NOW))
    self.map_hash = read_button_map(self.params, include_ioniq_media=True).fingerprint()
    self.reader = TrafficDisplayProjector()

  def project(self, event, **changes):
    args = {'now_mono_ns': NOW, 'now_boot_ns': BOOT, 'drive_id': DRIVE,
            'settings_fingerprint': self.settings_hash, 'map_fingerprint': self.map_hash,
            'map_assigned': True, 'profile_valid': True, 'long_active': True, 'selfdrive_enabled': True,
            'car_valid': True, 'system_long': True}
    args.update(changes)
    return self.reader.project(event.slcState if event is not None else None, **args)

  def test_no_traffic_wire_remains_absent_with_independent_vehicle_checks(self):
    ui = ui_fake()
    ui.CP = NS(carFingerprint="", openpilotLongitudinalControl=True, pcmCruise=False)
    state = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad
    self.assertIsNone(state.traffic_display)
    self.assertFalse(state.traffic_mode)

  def test_actual_wire_states_and_clearing(self):
    active = receipt(self.settings_hash, self.map_hash)
    self.assertEqual(self.project(active).state, 'active')
    self.assertTrue(self.reader.requested)
    self.assertEqual(self.project(active).state, 'active')  # Same frame remains fresh, not a new source.
    self.assertIsNone(self.project(active, now_mono_ns=NOW + 101_000_000))
    self.assertTrue(self.reader.requested)
    self.assertIsNone(self.project(active, now_boot_ns=BOOT + 400_000_000))
    paused = receipt(self.settings_hash, self.map_hash, sequence=2, observed=NOW + 10_000_000,
                     reason='authority_unavailable', effective=False)
    self.assertEqual(self.project(paused, now_mono_ns=NOW + 10_000_000, long_active=False).state, 'paused')
    self.assertTrue(self.reader.requested)
    off = receipt(self.settings_hash, self.map_hash, sequence=3, observed=NOW + 20_000_000,
                  reason='off', accepted=False, effective=False)
    self.assertEqual(self.project(off, now_mono_ns=NOW + 20_000_000).state, 'off')
    self.assertFalse(self.reader.requested)
    unavailable = receipt(self.settings_hash, self.map_hash, sequence=4, observed=NOW + 30_000_000, ready=False)
    self.assertEqual(self.project(unavailable, now_mono_ns=NOW + 30_000_000).state, 'unavailable_profile')
    self.assertIsNone(self.project(active))  # Old sequence cannot resurrect active status.
    source = receipt(self.settings_hash, self.map_hash, sequence=5, observed=NOW + 40_000_000,
                     reason='media_unavailable', accepted=False, effective=False, source_boot=0)
    self.assertEqual(self.project(source, now_mono_ns=NOW + 40_000_000).state, 'unavailable_source')

  def test_wrong_drive_context_and_restarted_session_reject_old_source(self):
    first = receipt(self.settings_hash, self.map_hash)
    self.assertIsNone(self.project(first, drive_id=DRIVE + 1))
    self.assertIsNone(self.project(first, settings_fingerprint='b' * 64))
    self.assertIsNone(self.project(first, map_fingerprint='b' * 64))
    self.assertIsNone(self.project(first, map_assigned=False))
    self.assertIsNone(self.project(first, profile_valid=False, long_active=False))
    self.assertEqual(self.project(first).state, 'active')
    second = receipt(self.settings_hash, self.map_hash, session='c' * 32, observed=NOW + 10_000_000)
    self.assertIsNone(self.project(second, now_mono_ns=NOW + 10_000_000))
    next_frame = receipt(self.settings_hash, self.map_hash, session='c' * 32,
                         sequence=2, observed=NOW + 20_000_000)
    self.assertEqual(self.project(next_frame, now_mono_ns=NOW + 20_000_000).state, 'active')
    self.assertIsNone(self.project(first))
    self.assertIsNone(self.project(next_frame, drive_id=DRIVE + 1))

  def test_snapshot_work_does_not_blink_fresh_traffic_border(self):
    ui = ui_fake()
    ui.params = self.params
    ui.CP = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    ui.CP.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    ui.CP.openpilotLongitudinalControl = True
    ui.sm['deviceState'].startedMonoTime = DRIVE
    ui.sm['carState'].canValid = True
    ui.sm['carState'].canTimeout = False
    ui.sm['carControl'].longActive = True
    ui.sm['selfdriveState'].enabled = True
    ui.sm.put('controlsState', NS(longControlState='pid'))
    adapter = RuntimeSnapshotAdapter(ui, mono_clock=lambda: NOW, boot_clock=lambda: NOW + BOOT_OFFSET_NS)
    clock = [NOW]
    # A delayed settings read must not move clock acquisition away from the snapshot.
    from openpilot.starpilot.ui import runtime_snapshot
    read_settings = runtime_snapshot.read_traffic_settings

    def delayed_settings(params):
      clock[0] += 30_000_000
      return read_settings(params)

    with mock.patch.object(runtime_snapshot, 'paired_clocks_ns', side_effect=lambda: (clock[0], clock[0] + BOOT - NOW, 0)), \
         mock.patch.object(runtime_snapshot, 'read_traffic_settings', side_effect=delayed_settings):
      for frame, delay_read in enumerate((True, False, True, False, True)):
        now = NOW + frame * 10_000_000
        clock[0] = now
        if delay_read:
          adapter._traffic_context_ns = None
        event = receipt(self.settings_hash, self.map_hash, observed=now, sequence=frame + 1)
        ui.sm.put('slcState', event.slcState)
        for _ in range(2):
          clock[0] = now
          shown = adapter.build(ShellMode.ONROAD, now_ns=now).onroad
          self.assertTrue(shown.traffic_mode)
          color = axis_status_color(shown)
          self.assertEqual((color.r, color.g, color.b, color.a), (201, 34, 49, 255))
      # Actual expiry still clears status; no cached red border or grace period.
      clock[0] = now + 100_000_001
      shown = adapter.build(ShellMode.ONROAD, now_ns=clock[0]).onroad
      self.assertFalse(shown.traffic_mode)
      self.assertIsNone(shown.traffic_display)
      # A mismatched or unavailable clock pair must still fail closed.
      clock[0] = now
      self.assertTrue(adapter.build(ShellMode.ONROAD, now_ns=now).onroad.traffic_mode)
      for pair in (None, (now + 10_000_001, BOOT, 0)):
        with mock.patch.object(runtime_snapshot, 'paired_clocks_ns', return_value=pair):
          self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=now).onroad.traffic_mode)

  def test_snapshot_and_compact_rail_use_only_live_effective_status(self):
    ui = ui_fake()
    ui.params = self.params
    ui.CP = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    ui.CP.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    ui.CP.openpilotLongitudinalControl = True
    ui.sm['deviceState'].startedMonoTime = DRIVE
    ui.sm['carState'].canValid = True
    ui.sm['carState'].canTimeout = False
    ui.sm['carControl'].longActive = True
    ui.sm['selfdriveState'].enabled = True
    ui.sm.put('controlsState', NS(longControlState='pid'))
    event = receipt(self.settings_hash, self.map_hash)
    ui.sm.put('slcState', event.slcState)
    ui.sm.valid['slcState'] = False
    adapter = RuntimeSnapshotAdapter(ui, mono_clock=lambda: NOW, boot_clock=lambda: NOW + BOOT_OFFSET_NS)
    with mock.patch('openpilot.starpilot.ui.runtime_snapshot.paired_clocks_ns', return_value=(NOW, BOOT, 0)):
      shown = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
      self.assertTrue(shown.traffic_mode)
      self.assertEqual(shown.traffic_display.label, 'TRAFFIC')
      self.assertEqual(shown.speed_limit.kind.value, 'stale')  # Invalid SLC does not veto independent Traffic status.
      fonts = mock.Mock()
      fonts.profile = Profile.COMPACT
      fonts.measure.return_value = NS(width=24, height=11)
      rail = MiciSidebarWidgets(fonts)
      with mock.patch.object(rail.personality, 'draw'):
        rail._personality(rl.Rectangle(476, 160, 60, 80), shown)
      fonts.draw.assert_not_called()
      large = OnroadView.__new__(OnroadView)
      large.personality = mock.Mock()
      large.fonts = fonts
      large.projection_viewport = None
      fonts.reset_mock()
      for width, expected_x in ((1860, 1390), (2160, 1690)):
        large._traffic_badge(replace(shown, viewport_width=width), 101)
        self.assertEqual(fonts.draw.call_args.args[:5], ('TRAFFIC', FontRole.SEMI_BOLD, 25, expected_x, 101))
      self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 101_000_000).onroad.traffic_mode)
      self.assertTrue(adapter.build(ShellMode.ONROAD, now_ns=NOW + 101_000_000).onroad.traffic_selected)
      self.assertIsNone(adapter.build(ShellMode.ONROAD, now_ns=NOW + 101_000_000).onroad.traffic_display)


if __name__ == '__main__':
  unittest.main()
