"""Joined planner receipt, effective manual state, and asynchronous saved bytes."""

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.car.hyundai.values import CAR as HYUNDAI_CAR, HyundaiFlags
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.conditional_mode import manual_saved
from openpilot.starpilot.conditional_mode.manual_saved import KEY, SavedCodes, encode, read_codes
from openpilot.starpilot.conditional_mode.planner_host import ConditionalPlannerHost
from openpilot.starpilot.conditional_mode.policy import ManualIntent, ModeChoice
from openpilot.starpilot.conditional_mode.preferences import CEMOptions, SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.tests.test_projection import BOOT, MONO, FakeSubMaster, serialized_scene
from openpilot.starpilot.conditional_mode.status import settings_fingerprint


class ManualSavedJoinTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.params = Params(self.directory.name)
    self.params.put('ConditionalModeConfig',
                    json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CEM,
                                                                   cem=CEMOptions(speed_mps=20.0, persist_manual=True)))), block=True)
    self.params.put('LKASButtonControl', 5, block=True)
    self.cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    self.planner = LongitudinalPlanner(self.cp, init_v=15.0)
    payloads = serialized_scene()
    vehicle = messaging.new_message('vehicleParameters', valid=True)
    payloads['vehicleParameters'] = messaging.log_from_bytes(vehicle.to_bytes()).vehicleParameters
    self.sm = FakeSubMaster(payloads, MONO - 5_000_000)
    self.owner = ConditionalPlannerHost(self.params)
    self.addCleanup(self.owner.close)

  def sample(self, stamp: int, event=None):
    self.sm.stamp(stamp)
    self.sm.payloads.update(serialized_scene(stamp=BOOT + stamp - MONO))
    self.planner.update(self.sm)
    return self.owner.sample(self.sm, self.cp, self.planner, now_mono_ns=stamp + 1_000_000,
                             now_boot_ns=BOOT + stamp - MONO + 1_000_000,
                             sample_skew_ns=1000, drive_id=MONO, manual_event=event)

  def receipt(self, stamp: int, sequence: int, *, session: str = 'b' * 32, button: str = 'lkas'):
    fingerprint = settings_fingerprint(self.owner.settings.current)
    assert fingerprint is not None
    event = messaging.new_message('slcCruiseEvent', valid=True)
    event.logMonoTime = stamp
    outer = event.slcCruiseEvent
    outer.kind = 'conditionalMode'
    outer.eventId = sequence
    outer.producerSessionId = session
    outer.observedMonoTime = stamp - 1000
    outer.manualMode = {'version': 1, 'sessionId': session, 'sequence': sequence,
                        'observedMonoTime': stamp - 1000, 'driveStartMonoTime': MONO,
                        'settingsFingerprint': fingerprint, 'choice': 'conditionalExperimental',
                        'button': button, 'press': 'short', 'sourceCarStateMonoTime': stamp,
                        'validUntilMonoTime': stamp + 100_000_000}
    return messaging.log_from_bytes(event.to_bytes())

  def settle(self, stamp: int):
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
      self.sample(stamp)
      if self.owner.manual_saved.status == 'saved':
        return
      time.sleep(0.005)
    self.fail('planner saved writer did not settle')

  def test_media_receipt_requires_exact_ioniq_and_current_assignment(self):
    self.params.put('ModeButtonControl', 5, block=True)
    self.sample(MONO)
    stamp = MONO + 50_000_000
    self.sample(stamp, self.receipt(stamp, 1, button='mode'))
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)  # Honda cannot consume Ioniq media.
    self.assertEqual(self.owner.card_last_sequence, 0)
    self.cp.carFingerprint = HYUNDAI_CAR.HYUNDAI_IONIQ_6
    self.cp.safetyConfigs = [{'safetyModel': 'hyundaiCanfd'}]
    self.cp.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    self.sample(stamp + 50_000_000, self.receipt(stamp + 50_000_000, 2, button='mode'))
    self.assertEqual(self.owner.card_last_sequence, 2)
    self.settle(stamp + 100_000_000)
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.FORCE_EXPERIMENTAL)

  def test_restored_override_then_physical_toggle_saves_automatic_zero(self):
    self.params.put(KEY, json.loads(encode(SavedCodes(2, 1))), block=True)
    self.sample(MONO)
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.FORCE_EXPERIMENTAL)
    self.assertEqual(self.owner.manual.code, 2)
    stamp = MONO + 50_000_000
    self.sample(stamp, self.receipt(stamp, 1))
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.FORCE_EXPERIMENTAL)  # Pending durable zero.
    self.assertEqual(self.owner.manual.code, 0)
    self.settle(stamp + 50_000_000)
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
    self.assertEqual(read_codes(self.params).codes, SavedCodes(0, 1))

  def test_invalid_saved_bytes_with_persist_off_keep_automatic_and_manual(self):
    self.params.put('ConditionalModeConfig',
                    json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CEM,
                                                                   cem=CEMOptions(speed_mps=20.0, persist_manual=False)))), block=True)
    raw = b'{"version":1,"cem":true,"ccm":0}'
    Path(self.params.get_param_path(KEY)).write_bytes(raw)
    self.sample(MONO)
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
    stamp = MONO + 50_000_000
    self.sample(stamp, self.receipt(stamp, 1))
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.FORCE_EXPERIMENTAL)
    self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), raw)

  def test_invalid_saved_bytes_with_persist_on_never_restore_or_write(self):
    raw = b'{"version":1,"cem":true,"ccm":0}'
    Path(self.params.get_param_path(KEY)).write_bytes(raw)
    self.sample(MONO)
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
    stamp = MONO + 50_000_000
    self.sample(stamp, self.receipt(stamp, 1))
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
    self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), raw)

  def test_external_edit_after_manual_request_withdraws_runtime_override(self):
    self.sample(MONO)
    stamp = MONO + 50_000_000
    entered = threading.Event()
    release = threading.Event()
    actual_fsync = manual_saved.os.fsync

    def blocked_fsync(fd):
      if not entered.is_set():
        entered.set()
        self.assertTrue(release.wait(1.0))
      return actual_fsync(fd)

    with patch.object(manual_saved.os, 'fsync', side_effect=blocked_fsync):
      self.sample(stamp, self.receipt(stamp, 1))
      self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)  # Unsaved gesture is not applied.
      self.sample(stamp + 1_000_000)
      self.assertTrue(entered.wait(1.0))
      self.params.put(KEY, json.loads(encode(SavedCodes(2, 2))), block=True)
      release.set()
      assert self.owner.manual_saved.worker is not None
      self.owner.manual_saved.worker.join(timeout=1.0)
    later = stamp + 50_000_000
    self.sample(later)
    self.assertTrue(self.owner.manual_saved.conflicted)
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
    self.sample(later + 50_000_000, self.receipt(later + 50_000_000, 2))
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
    self.assertEqual(read_codes(self.params).codes, SavedCodes(2, 2))

  def test_first_pre_rename_write_failure_keeps_prior_live_mode(self):
    self.sample(MONO)
    stamp = MONO + 50_000_000
    self.sample(stamp, self.receipt(stamp, 1))
    with patch.object(manual_saved.os, 'fsync', side_effect=OSError('stage failed')):
      self.sample(stamp + 1_000_000)
      assert self.owner.manual_saved.worker is not None
      self.owner.manual_saved.worker.join(timeout=1.0)
    self.sample(stamp + 2_000_000)
    self.assertEqual(self.owner.manual_saved.status, 'write_failed')
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
    self.assertIsNone(read_codes(self.params).raw)

  def test_two_rapid_gestures_second_failure_uses_first_durable_state(self):
    self.sample(MONO)
    stamp = MONO + 50_000_000
    entered = threading.Event()
    release = threading.Event()
    actual_fsync = manual_saved.os.fsync
    calls = 0

    def staged_fsync(fd):
      nonlocal calls
      calls += 1
      if calls == 1:
        entered.set()
        self.assertTrue(release.wait(1.0))
      if calls == 3:
        raise OSError('second stage failed')
      return actual_fsync(fd)

    with patch.object(manual_saved.os, 'fsync', side_effect=staged_fsync):
      self.sample(stamp, self.receipt(stamp, 1))  # Requested code 2; no immediate mode change.
      self.sample(stamp + 1_000_000)
      self.assertTrue(entered.wait(1.0))
      second = stamp + 2_000_000
      self.sample(second, self.receipt(second, 2))  # Requested automatic code 0 while first is staged.
      self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
      release.set()
      assert self.owner.manual_saved.worker is not None
      self.owner.manual_saved.worker.join(timeout=1.0)
      self.sample(stamp + 3_000_000)  # First save acknowledged; second starts.
      self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
      assert self.owner.manual_saved.worker is not None
      self.owner.manual_saved.worker.join(timeout=1.0)
    self.sample(stamp + 4_000_000)
    self.assertEqual(self.owner.manual_saved.status, 'write_failed')
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.FORCE_EXPERIMENTAL)
    self.assertEqual(read_codes(self.params).codes, SavedCodes(2, 0))

  def test_card_session_restart_cannot_apply_pending_unsaved_code(self):
    self.sample(MONO)
    stamp = MONO + 50_000_000
    entered = threading.Event()
    release = threading.Event()
    actual_fsync = manual_saved.os.fsync

    def blocked_fsync(fd):
      if not entered.is_set():
        entered.set()
        self.assertTrue(release.wait(1.0))
      return actual_fsync(fd)

    with patch.object(manual_saved.os, 'fsync', side_effect=blocked_fsync):
      self.sample(stamp, self.receipt(stamp, 100))
      self.sample(stamp + 1_000_000)
      self.assertTrue(entered.wait(1.0))
      restart = stamp + 2_000_000
      self.sample(restart, self.receipt(restart, 1, session='c' * 32))
      self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
      self.assertEqual(self.owner.manual_live_code, 0)
      release.set()
      assert self.owner.manual_saved.worker is not None
      self.owner.manual_saved.worker.join(timeout=1.0)
    self.sample(stamp + 3_000_000)
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.FORCE_EXPERIMENTAL)
    self.assertEqual(read_codes(self.params).codes, SavedCodes(2, 0))
    resumed = stamp + 4_000_000
    self.sample(resumed, self.receipt(resumed, 2, session='c' * 32))
    self.assertEqual(self.owner.manual.last_sequence, 2)
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.FORCE_EXPERIMENTAL)  # Code-zero write pending.
    self.settle(resumed + 50_000_000)
    self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)


if __name__ == '__main__':
  unittest.main()
