"""Physical Ioniq Traffic intent, source loss, and same-drive authority tests."""

import json
import tempfile
import unittest
from collections import deque
from types import SimpleNamespace

from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR as HONDA_CAR
from opendbc.car.hyundai.values import CAR as HYUNDAI_CAR, HyundaiFlags
from openpilot.starpilot.longitudinal.profile_document import default_personality_profiles, profile_document
from openpilot.cereal import log, messaging
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import (current_traffic_event, profile_host_needed,
                                                   queue_cruise_event, traffic_profile_status, update_curve_frame)
from openpilot.starpilot.longitudinal.profile_runtime import ProfileHost
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
from openpilot.starpilot.conditional_mode.manual import read_button_map
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
from openpilot.starpilot.conditional_mode.status import settings_fingerprint
from openpilot.starpilot.conditional_mode.traffic import TrafficOwner


NOW = 100_000_000_000
DRIVE = NOW - 1_000_000_000
BOOT = NOW + 2_000_000_000


class FakeSM:
  def __init__(self):
    self.logMonoTime = dict.fromkeys(('deviceState', 'carState', 'modelV2', 'carControl', 'selfdriveState'), NOW - 5_000_000)
    self.recv_time = dict.fromkeys(self.logMonoTime, (NOW - 5_000_000) / 1e9)
    self.seen = dict.fromkeys(self.logMonoTime, True)
    self.alive = dict.fromkeys(self.logMonoTime, True)
    self.valid = dict.fromkeys(self.logMonoTime, True)
    self.payloads = {
      'deviceState': SimpleNamespace(started=True),
      'carState': SimpleNamespace(canValid=True, canTimeout=False),
      'carControl': SimpleNamespace(enabled=True, longActive=True),
      'selfdriveState': SimpleNamespace(enabled=True),
    }

  def __getitem__(self, key):
    return self.payloads[key]

  def advance(self, now):
    for key in self.logMonoTime:
      self.logMonoTime[key] = now - 5_000_000
      self.recv_time[key] = (now - 5_000_000) / 1e9


class TrafficOwnerTests(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.params = Params(directory.name)
    self.params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.STOCK))), block=True)
    self.settings = ConditionalSettingsOwner(self.params)
    self.owner = TrafficOwner()
    self.sm = FakeSM()
    self.cp = interfaces[HYUNDAI_CAR.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI_CAR.HYUNDAI_IONIQ_6)
    self.cp.openpilotLongitudinalControl = True
    self.cp.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)

  def event(self, sequence, now, *, toggle=False, epoch=0, session='a' * 32):
    snapshot = self.settings.refresh(now)
    fingerprint = settings_fingerprint(snapshot)
    buttons = read_button_map(self.params, include_ioniq_media=True)
    assert fingerprint is not None and buttons is not None
    message = messaging.new_message('slcCruiseEvent', valid=True)
    message.logMonoTime = now - 4_000_000
    wire = message.slcCruiseEvent
    wire.kind = 'trafficMode'
    wire.eventId = sequence
    wire.producerSessionId = session
    wire.observedMonoTime = now - 7_000_000
    wire.trafficMode = {
      'version': 1, 'sessionId': session, 'sequence': sequence,
      'observedMonoTime': now - 7_000_000, 'driveStartMonoTime': DRIVE,
      'settingsFingerprint': fingerprint, 'buttonMapFingerprint': buttons.fingerprint(),
      'sourceEpoch': epoch, 'sourceBootTime': BOOT + now - NOW - 7_000_000,
      'sourceCarStateMonoTime': now - 5_000_000, 'validUntilMonoTime': now + 95_000_000,
      'toggle': toggle, 'button': 'mode' if toggle else 'unknown',
      'press': 'short' if toggle else 'unknown',
    }
    return messaging.log_from_bytes(message.to_bytes())

  def sample(self, now=NOW, event=None):
    self.sm.advance(now)
    return self.owner.sample(event, params=self.params, settings=self.settings,
                             sm=self.sm, cp=self.cp, drive_id=DRIVE,
                             now_mono_ns=now, now_boot_ns=BOOT + now - NOW)

  def test_valid_conditional_preference_change_preserves_controller_intent(self):
    self.sm.advance(NOW)
    on = self.owner.sample(None, params=self.params, settings=self.settings, sm=self.sm, cp=self.cp,
                           drive_id=DRIVE, now_mono_ns=NOW, now_boot_ns=BOOT,
                           controller_toggle=True, controller_requested=True)
    self.assertTrue(on.requested)
    self.params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CCM))), block=True)
    later = NOW + 1_000_000_000
    self.sm.advance(later)
    continued = self.owner.sample(None, params=self.params, settings=self.settings, sm=self.sm, cp=self.cp,
                                  drive_id=DRIVE, now_mono_ns=later, now_boot_ns=BOOT+later-NOW)
    self.assertTrue(continued.requested and continued.effective)
    self.assertTrue(continued.controller_source)
    off = self.owner.sample(None, params=self.params, settings=self.settings, sm=self.sm, cp=self.cp,
                            drive_id=DRIVE, now_mono_ns=later+50_000_000, now_boot_ns=BOOT+later-NOW+50_000_000,
                            controller_toggle=True, controller_requested=False)
    self.assertFalse(off.requested)

  def test_explicit_controller_target_does_not_invert_after_owner_source_loss(self):
    self.sm.advance(NOW)
    self.owner.requested = False
    off = self.owner.sample(None, params=self.params, settings=self.settings, sm=self.sm, cp=self.cp,
                            drive_id=DRIVE, now_mono_ns=NOW, now_boot_ns=BOOT,
                            controller_toggle=True, controller_requested=False)
    self.assertFalse(off.requested)
    on = self.owner.sample(None, params=self.params, settings=self.settings, sm=self.sm, cp=self.cp,
                           drive_id=DRIVE, now_mono_ns=NOW+50_000_000, now_boot_ns=BOOT+50_000_000,
                           controller_toggle=True, controller_requested=True)
    self.assertTrue(on.requested)

  def test_unassigned_preserves_ordinary_profile_without_media(self):
    verdict = self.sample()
    self.assertEqual((verdict.effective, verdict.reason), (False, 'unassigned'))
    self.assertEqual(verdict.source_mono_ns, self.sm.logMonoTime['modelV2'])
    self.assertFalse(verdict.requested)

  def test_conditional_startup_uses_longitudinal_capability_without_media_buttons(self):
    honda = interfaces[HONDA_CAR.HONDA_CIVIC].get_non_essential_params(HONDA_CAR.HONDA_CIVIC)
    self.assertTrue(honda.openpilotLongitudinalControl)
    self.assertTrue(profile_host_needed(honda, profile_enabled=False, conditional_enabled=True))
    self.assertFalse(profile_host_needed(self.cp, profile_enabled=False, conditional_enabled=False))
    self.assertTrue(profile_host_needed(self.cp, profile_enabled=False, conditional_enabled=True))
    self.assertTrue(profile_host_needed(honda, profile_enabled=True, conditional_enabled=False))
    honda.openpilotLongitudinalControl = False
    self.assertFalse(profile_host_needed(honda, profile_enabled=False, conditional_enabled=True))

  def test_profile_status_requires_post_update_application(self):
    target = object()
    self.assertEqual(traffic_profile_status(True, target, None, None), (False, 'target_unapplied'))
    self.assertEqual(traffic_profile_status(True, target, object(), None), (True, 'qualified'))
    self.assertEqual(traffic_profile_status(True, None, None, SimpleNamespace(reason='valid')),
                     (False, 'source_unavailable'))
    self.assertEqual(traffic_profile_status(False, target, object(), None), (False, 'inactive'))

  def test_card_event_has_own_queue_and_100ms_expiry(self):
    event = self.event(1, NOW)
    slc, curve, manual, traffic = (deque() for _ in range(4))
    queue_cruise_event(event, slc, curve, manual, traffic)
    self.assertEqual((len(slc), len(curve), len(manual), len(traffic)), (0, 0, 0, 1))
    self.assertIs(current_traffic_event(traffic, NOW, NOW - 5_000_000), event)
    queue_cruise_event(event, slc, curve, manual, traffic)
    self.assertIsNone(current_traffic_event(traffic, NOW + 100_000_000, NOW + 100_000_000))

  def test_toggle_retains_intent_only_through_fresh_same_drive_authority(self):
    self.params.put('ModeButtonControl', 6, block=True)
    self.assertFalse(self.sample(NOW, self.event(1, NOW)).requested)
    on = self.sample(NOW + 50_000_000, self.event(2, NOW + 50_000_000, toggle=True))
    self.assertEqual((on.requested, on.effective), (True, True))
    self.sm.payloads['carControl'].longActive = False
    paused = self.sample(NOW + 100_000_000)
    self.assertEqual((paused.requested, paused.effective), (True, None))
    self.sm.payloads['carControl'].longActive = True
    resumed = self.sample(NOW + 150_000_000)
    self.assertEqual((resumed.requested, resumed.effective), (True, True))
    lost = self.sample(NOW + 400_000_000)
    self.assertEqual((lost.requested, lost.effective, lost.reason), (True, True, 'active'))
    self.assertEqual(lost.source_boot_ns, 0)

  def test_new_source_epoch_preserves_intent_and_unassigning_is_explicit_exit(self):
    self.params.put('ModeButtonControl', 6, block=True)
    self.sample(NOW, self.event(1, NOW))
    self.assertTrue(self.sample(NOW + 50_000_000, self.event(2, NOW + 50_000_000, toggle=True)).requested)
    epoch = self.sample(NOW + 100_000_000, self.event(3, NOW + 100_000_000, epoch=1))
    self.assertEqual((epoch.requested, epoch.effective), (True, True))
    self.assertFalse(self.sample(NOW + 150_000_000, self.event(4, NOW + 150_000_000, toggle=True, epoch=1)).requested)
    self.params.put('ModeButtonControl', 0, block=True)
    changed = self.sample(NOW + 1_200_000_000)
    self.assertEqual((changed.requested, changed.effective, changed.reason), (False, False, 'unassigned'))

  def test_physical_intent_survives_packet_silence_and_cem_change_until_new_toggle(self):
    self.params.put('ModeButtonControl', 6, block=True)
    self.sample(NOW, self.event(1, NOW))
    original = self.event(2, NOW + 50_000_000, toggle=True)
    self.assertTrue(self.sample(NOW + 50_000_000, original).requested)
    self.params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CCM))), block=True)
    later = NOW + 1_200_000_000
    held = self.sample(later)
    self.assertTrue(held.requested and held.effective)
    stale = self.sample(later + 50_000_000, original)
    self.assertTrue(stale.requested)
    off = self.sample(later + 100_000_000, self.event(3, later + 100_000_000, toggle=True, epoch=1))
    self.assertFalse(off.requested)

  def test_expired_physical_receipt_keeps_fresh_serialized_active_display(self):
    from openpilot.starpilot.conditional_mode.traffic_status import TrafficDisplayProjector
    self.params.put('ModeButtonControl', 6, block=True)
    self.sample(NOW, self.event(1, NOW))
    self.sample(NOW + 50_000_000, self.event(2, NOW + 50_000_000, toggle=True))
    later = NOW + 1_000_000_000
    verdict = self.sample(later)
    event = self.owner.attach(None, verdict, now_ns=later, drive_id=DRIVE,
                              profile_target_ready=True, profile_reason='qualified')
    state = messaging.log_from_bytes(event.to_bytes()).slcState
    self.assertEqual(state.trafficMode.sourceBootTime, 0)
    reader = TrafficDisplayProjector()
    args = {'now_mono_ns': later, 'now_boot_ns': BOOT+later-NOW, 'drive_id': DRIVE,
                'settings_fingerprint': verdict.settings_fingerprint, 'map_fingerprint': verdict.map_fingerprint,
                'map_assigned': True, 'profile_valid': True, 'long_active': True, 'selfdrive_enabled': True,
                'car_valid': True, 'system_long': True}
    self.assertEqual(reader.project(state, **args).state, 'active')
    args['now_mono_ns'] += 101_000_000
    self.assertIsNone(reader.project(state, **args))

  def test_card_session_restart_retires_old_session_and_requires_new_toggle(self):
    self.params.put('ModeButtonControl', 6, block=True)
    self.sample(NOW, self.event(1, NOW))
    self.assertTrue(self.sample(NOW + 50_000_000, self.event(2, NOW + 50_000_000, toggle=True)).requested)
    restarted = self.sample(NOW + 100_000_000, self.event(1, NOW + 100_000_000, session='b' * 32))
    self.assertEqual((restarted.requested, restarted.effective), (False, False))
    replay = self.sample(NOW + 150_000_000, self.event(3, NOW + 150_000_000, toggle=True))
    self.assertFalse(replay.requested)
    fresh = self.sample(NOW + 200_000_000, self.event(2, NOW + 200_000_000, toggle=True, session='b' * 32))
    self.assertTrue(fresh.effective)

  def test_source_reversal_or_onroad_end_clears_without_renewal(self):
    self.params.put('ModeButtonControl', 6, block=True)
    self.sample(NOW, self.event(1, NOW))
    self.assertTrue(self.sample(NOW + 50_000_000, self.event(2, NOW + 50_000_000, toggle=True)).requested)
    reversed_clock = self.owner.sample(None, params=self.params, settings=self.settings, sm=self.sm, cp=self.cp,
                                       drive_id=DRIVE, now_mono_ns=NOW + 60_000_000,
                                       now_boot_ns=BOOT - 1)
    self.assertEqual((reversed_clock.requested, reversed_clock.reason), (False, 'media_unavailable'))
    self.sm.payloads['deviceState'].started = False
    offroad = self.sample(NOW + 100_000_000)
    self.assertEqual((offroad.requested, offroad.reason), (False, 'can_unavailable'))

  def test_same_cycle_traffic_profile_mpc_and_fresh_off_convergence(self):
    self.params.put('ModeButtonControl', 6, block=True)
    host = ProfileHost(self.params)  # CustomPersonalities remains off.
    planner = LongitudinalPlanner(self.cp, init_v=20.0)

    def solve(mode, now, *, long_active=True):
      target = host.sample(now, log.LongitudinalPersonality.standard, 20.0, self.cp, traffic_mode=mode)
      sm, _ = messages(lead=True)
      sm['carState'].vEgo = 20.0
      sm['carControl'].longActive = long_active
      update_curve_frame(planner, sm, self.cp, now, profile_tuning=target, traffic_mode=mode)
      self.assertEqual(planner.mpc.solution_status, 0)
      return float(planner.mpc.params[0, 4])

    # Without action 6 or a physical receipt, default native follows unchanged.
    ordinary = solve(False, NOW)
    self.assertAlmostEqual(ordinary, 1.45)
    self.sample(NOW, self.event(1, NOW))
    accepted = self.sample(NOW + 50_000_000, self.event(2, NOW + 50_000_000, toggle=True))
    self.assertTrue(accepted.effective)
    status = self.owner.attach(None, accepted, now_ns=NOW + 50_000_000, drive_id=DRIVE)
    status = messaging.log_from_bytes(status.to_bytes()).slcState.trafficMode
    self.assertTrue(status.accepted)
    self.assertTrue(status.effective)
    self.assertEqual(status.reason, 'active')
    on = solve(accepted.effective, NOW + 50_000_000)
    self.assertLess(on, ordinary)
    self.assertIsNotNone(planner.last_profile)

    # The real long axis going inactive (Nostalgia paddle/AOL lateral-only)
    # keeps the same-drive intent but immediately removes Traffic MPC tuning.
    self.sm.payloads['carControl'].longActive = False
    paused = self.sample(NOW + 100_000_000)
    self.assertTrue(paused.requested)
    self.assertIsNone(paused.effective)
    self.assertAlmostEqual(solve(paused.effective, NOW + 100_000_000, long_active=False), 1.45)
    self.assertIsNone(planner.last_profile)
    self.sm.payloads['carControl'].longActive = True
    resumed = self.sample(NOW + 150_000_000)
    self.assertTrue(resumed.effective)
    self.assertLess(solve(resumed.effective, NOW + 150_000_000), ordinary)

    off = self.sample(NOW + 200_000_000, self.event(3, NOW + 200_000_000, toggle=True))
    self.assertEqual((off.requested, off.effective), (False, False))
    previous = float(planner.mpc.params[0, 4])
    for index in range(45):
      current = solve(off.effective, NOW + 200_000_000 + index * 50_000_000)
      self.assertGreaterEqual(current + 1e-8, previous)
      self.assertLessEqual(current - previous, planner.dt + 1e-8)
      previous = current
    self.assertAlmostEqual(previous, 1.45, places=5)

  def test_assigned_never_pressed_missing_media_keeps_ordinary_custom_profile(self):
    self.params.put('ModeButtonControl', 6, block=True)
    profiles = default_personality_profiles(False)
    profiles['standard']['following'] = {'preset': 'custom_simple', 'curve': []}  # Following = Custom (Simple) uses the saved follow values
    self.params.put('LongitudinalPersonalityProfiles', profile_document(profiles, enabled=True), block=True)
    self.params.put('StandardFollow', 2.1, block=True)
    self.params.put('StandardFollowHigh', 2.1, block=True)
    host = ProfileHost(self.params)
    planner = LongitudinalPlanner(self.cp, init_v=20.0)
    absent = self.sample()
    self.assertIsNone(absent.effective)  # Physical traffic observation is still unknown.
    self.assertFalse(absent.profile_mode)  # An unused assignment cannot suppress ordinary tuning.
    ordinary = host.sample(NOW, log.LongitudinalPersonality.standard, 20.0, self.cp,
                           traffic_mode=absent.profile_mode)
    self.assertIsNotNone(ordinary)
    self.assertAlmostEqual(ordinary.follow_seconds, 2.1)
    sm, _ = messages(lead=True)
    sm['carControl'].longActive = True
    update_curve_frame(planner, sm, self.cp, NOW, profile_tuning=ordinary,
                       traffic_mode=absent.profile_mode)
    self.assertGreater(float(planner.mpc.params[0, 4]), 1.45)

    self.sample(NOW + 50_000_000, self.event(1, NOW + 50_000_000))
    on = self.sample(NOW + 100_000_000, self.event(2, NOW + 100_000_000, toggle=True))
    self.assertTrue(on.profile_mode)
    traffic = host.sample(NOW + 100_000_000, log.LongitudinalPersonality.standard, 20.0, self.cp,
                          traffic_mode=on.profile_mode)
    self.assertIsNotNone(traffic)
    update_curve_frame(planner, sm, self.cp, NOW + 100_000_000,
                       profile_tuning=traffic, traffic_mode=on.profile_mode)
    held = self.sample(NOW + 450_000_000)
    self.assertTrue(held.profile_mode and held.requested)
    retained = host.sample(NOW + 450_000_000, log.LongitudinalPersonality.standard,
                           20.0, self.cp, traffic_mode=held.profile_mode)
    self.assertIsNotNone(retained)
    self.assertEqual(retained.follow_seconds, traffic.follow_seconds)
    update_curve_frame(planner, sm, self.cp, NOW + 450_000_000,
                       profile_tuning=retained, traffic_mode=held.profile_mode)
    self.assertIsNotNone(planner.last_profile)
    self.sm.payloads['carState'].canValid = False
    lost = self.sample(NOW + 500_000_000)
    self.assertIsNone(lost.profile_mode)
    self.assertFalse(lost.requested)
    self.assertIsNone(host.sample(NOW + 500_000_000, log.LongitudinalPersonality.standard,
                                  20.0, self.cp, traffic_mode=lost.profile_mode))
    sm['carState'].canValid = False
    update_curve_frame(planner, sm, self.cp, NOW + 500_000_000,
                       profile_tuning=None, traffic_mode=lost.profile_mode)
    self.assertIsNone(planner.last_profile)
    self.assertAlmostEqual(float(planner.mpc.params[0, 4]), 1.45)



if __name__ == '__main__':
  unittest.main()
