"""Typed native UI requests joined to the existing planner manual owner."""

from typing import Any

from collections import deque
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import current_ui_manual_event, queue_ui_action
from openpilot.starpilot.conditional_mode import manual_saved
from openpilot.starpilot.conditional_mode.consumer import ConsumerResult, ModeConsumer
from openpilot.starpilot.conditional_mode.effective_status import publish_ack
from openpilot.starpilot.conditional_mode.manual_saved import SavedCodes, read_codes
from openpilot.starpilot.conditional_mode.planner_host import ConditionalPlannerHost
from openpilot.starpilot.conditional_mode.policy import ManualIntent, ModeChoice, next_manual_status
from openpilot.starpilot.conditional_mode.preferences import CCMOptions, CEMOptions, SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.status import CHOICES, ModeObservation, settings_fingerprint
from openpilot.starpilot.conditional_mode.tests.test_projection import BOOT, MONO, FakeSubMaster, serialized_scene
from openpilot.starpilot.conditional_mode.ui_action import ConditionalUiActionOwner, LIFETIME_NS, observation




def changed_arguments(arguments: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
  return {**arguments, **changes}

class Publisher:
  def __init__(self):
    self.events = []

  def send(self, service, event):
    assert service == 'slcAction'
    self.events.append(messaging.log_from_bytes(event.to_bytes()))


class TestUiAction(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.params = Params(directory.name)
    self.params.put('LKASButtonControl', 5, block=True)
    self.cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    self.planner = LongitudinalPlanner(self.cp, init_v=15.0)
    self.sm = FakeSubMaster(serialized_scene(), MONO)
    self.owner = ConditionalPlannerHost(self.params)
    self.addCleanup(self.owner.close)
    self.ui = ConditionalUiActionOwner(self.params)
    self.publisher = Publisher()
    self.ack_sequence = 0
    self.choice = ModeChoice.CEM
    self.configure()
    self.sample(MONO + 10_000_000)

  def configure(self, choice=ModeChoice.CEM, persist=False):
    self.choice = ModeChoice.CEM if choice is ModeChoice.CCM else choice
    document = SavedPreferences(mode=choice, cem=CEMOptions(speed_mps=20.0, persist_manual=persist),
                                ccm=CCMOptions(persist_manual=persist))
    self.params.put('ConditionalModeConfig', json.loads(encode_preferences(document)), block=True)
    self.owner.settings.last_refresh_ns = None
    self.ui.settings.last_refresh_ns = None

  def install(self, name, event, stamp):
    self.sm.payloads[name] = getattr(messaging.log_from_bytes(event.to_bytes()), name)
    event.clear_write_flag()
    for attr in ('seen', 'alive', 'valid'):
      getattr(self.sm, attr)[name] = True
    self.sm.logMonoTime[name] = stamp
    self.sm.recv_time[name] = stamp / 1e9

  def sources(self, stamp, effective=False):
    self.sm.stamp(stamp)
    self.sm.payloads.update(serialized_scene(stamp=BOOT + stamp - MONO, experimental_mode=effective))
    device = messaging.new_message('deviceState', valid=True)
    device.deviceState.started, device.deviceState.startedMonoTime = True, MONO
    self.install('deviceState', device, stamp)
    vehicle = messaging.new_message('vehicleParameters', valid=True)
    self.sm.payloads['vehicleParameters'] = vehicle.vehicleParameters

  def sample(self, stamp, *, ui_event=None, card_event=None, effective=False):
    self.sources(stamp, effective)
    self.planner.update(self.sm)
    return self.owner.sample(self.sm, self.cp, self.planner, now_mono_ns=stamp + 1_000_000,
                             now_boot_ns=BOOT + stamp - MONO + 1_000_000, sample_skew_ns=1000,
                             drive_id=MONO, manual_event=card_event, ui_event=ui_event)

  def ack(self, stamp, *, effective=False, code=None, session='a' * 32, sequence=None):
    self.sources(stamp, effective)
    self.ack_sequence += 1
    code = self.owner.manual.code if code is None else code
    proposal = ModeObservation(self.owner.status.session, 1, stamp, stamp + LIFETIME_NS, MONO, stamp, stamp, 1,
                               settings_fingerprint(self.owner.settings.current) or "", self.choice, effective,
                               'proposed', 'manual' if code else 'automatic', code)
    event = publish_ack(session=session, sequence=self.ack_sequence if sequence is None else sequence,
                        observed_ns=stamp, selfdrive_state_ns=stamp, drive_id=MONO,
                        effective_experimental=effective, result=ConsumerResult(effective, True, 'proposed'),
                        accepted_proposal=proposal)
    self.install('starpilotSelfdriveState', event, stamp)
    return event

  def request(self, stamp, *, effective=False, code=None, ui=None):
    self.ack(stamp, effective=effective, code=code)
    ui = self.ui if ui is None else ui
    context = ui.context(self.sm, self.cp, now_ns=stamp + 1000)
    assert context is not None
    self.assertTrue(ui.dispatch(context, self.sm, self.cp, self.publisher, now_ns=stamp + 2000))
    return self.publisher.events[-1]

  def card(self, stamp, sequence, session='b' * 32):
    event = messaging.new_message('slcCruiseEvent', valid=True)
    event.logMonoTime = stamp
    event.slcCruiseEvent = {
      'kind': 'conditionalMode', 'eventId': sequence, 'producerSessionId': session, 'observedMonoTime': stamp - 1000,
      'manualMode': {'version': 1, 'sessionId': session, 'sequence': sequence, 'observedMonoTime': stamp - 1000,
                     'driveStartMonoTime': MONO, 'settingsFingerprint': settings_fingerprint(self.owner.settings.current),
                     'choice': CHOICES[self.choice], 'button': 'lkas', 'press': 'short',
                     'sourceCarStateMonoTime': stamp, 'validUntilMonoTime': stamp + LIFETIME_NS}}
    return messaging.log_from_bytes(event.to_bytes())

  def settle(self, stamp):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
      self.sample(stamp)
      if self.owner.manual_saved.status == 'saved':
        return
      time.sleep(.005)
    self.fail('manual writer did not settle')

  def test_automatic_and_retired_mode_typed_cycle_and_actual_consumer(self):
    cases = ((choice, effective) for choice in (ModeChoice.CEM, ModeChoice.CCM) for effective in (False, True))
    for index, (choice, effective) in enumerate(cases):
      with self.subTest(choice=choice, effective=effective):
        self.configure(choice)
        stamp = MONO + (index + 1) * 1_000_000_000
        self.sample(stamp)
        event = self.request(stamp + 10_000_000, effective=effective)
        proposal, snapshot = self.sample(stamp + 20_000_000, ui_event=event, effective=effective)
        self.assertEqual(self.owner.manual.code, next_manual_status(self.choice, 0, effective))
        self.assertEqual(self.owner.manual_state.intent, ManualIntent.FORCE_CHILL if effective else ManualIntent.FORCE_EXPERIMENTAL)
        self.assertIs(proposal.override_experimental, not effective)
        consumer = ModeConsumer()
        arguments: dict[str, Any] = {'now_ns': stamp + 21_000_000, 'now_boot_ns': BOOT + stamp - MONO + 21_000_000,
                     'sample_skew_ns': 1000, 'message_ns': stamp + 21_000_000, 'receipt_ns': stamp + 21_000_000,
                     'drive_id': MONO, 'model_ns': stamp + 20_000_000, 'car_state_ns': stamp + 20_000_000,
                     'authority': True, 'stock_experimental': False, 'choice': self.choice,
                     'settings_fingerprint': settings_fingerprint(snapshot)}
        consumer.sample(None, **arguments)  # Establish its real restart barrier.
        later = stamp + 40_000_000
        proposal, snapshot = self.sample(later, effective=not effective)
        status = self.owner.status.attach(None, proposal, snapshot, now_ns=later + 1_000_000, drive_id=MONO,
                                          model_ns=later, car_state_ns=later)
        result = consumer.sample(status.slcState, **changed_arguments(arguments, {'now_ns': later + 2_000_000,
          'now_boot_ns': BOOT + later - MONO + 2_000_000, 'message_ns': status.logMonoTime,
          'receipt_ns': later + 1_000_000, 'model_ns': later, 'car_state_ns': later}))
        self.assertTrue(result.accepted)
        self.assertIs(result.experimental, not effective)
        event = self.request(stamp + 60_000_000, effective=not effective)
        self.sample(stamp + 70_000_000, ui_event=event, effective=not effective)
        self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
        self.assertEqual(self.owner.manual.code, 0)
        self.assertIsNone(self.params.get('ExperimentalMode'))

  def test_card_ui_independent_sequences_and_same_frame_card_priority(self):
    stamp = MONO + 50_000_000
    self.sample(stamp, card_event=self.card(stamp, 900))
    self.assertEqual(self.owner.card_last_sequence, 900)
    ui_event = self.request(stamp + 20_000_000, effective=True)
    self.sample(stamp + 30_000_000, ui_event=ui_event, effective=True)
    self.assertEqual(self.owner.manual.code, 0)
    self.assertEqual(self.owner.ui_last_sequence, 1)
    self.sample(stamp + 40_000_000, card_event=self.card(stamp + 40_000_000, 901))
    self.assertEqual(self.owner.manual.code, 2)
    self.sample(stamp + 50_000_000, ui_event=ui_event, card_event=self.card(stamp + 50_000_000, 902), effective=True)
    self.assertEqual(self.owner.manual.code, 0)  # Duplicate UI cannot reverse physical action.
    ui_event = self.request(stamp + 60_000_000, effective=False)
    self.sample(stamp + 70_000_000, ui_event=ui_event, card_event=self.card(stamp + 70_000_000, 903))
    self.assertEqual(self.owner.manual.code, 2)  # Both requested automatic -> manual; one cycle only.
    self.assertEqual(self.owner.manual.last_sequence, 5)

  def test_ui_restart_retires_old_stream_and_planner_restart_rejects_old_context(self):
    stamp = MONO + 50_000_000
    old = self.request(stamp)
    self.sample(stamp + 10_000_000, ui_event=old)
    restarted = ConditionalUiActionOwner(self.params)
    new = self.request(stamp + 20_000_000, effective=True, ui=restarted)
    self.sample(stamp + 30_000_000, ui_event=new, effective=True)
    self.assertEqual(self.owner.manual.code, 0)
    replay = old.as_builder()
    replay.slcAction.conditionalManual.sequence = 500
    self.sample(stamp + 40_000_000, ui_event=replay)
    self.assertEqual(self.owner.manual.code, 0)
    pending = self.request(stamp + 50_000_000, ui=restarted)
    self.owner.close()
    self.owner = ConditionalPlannerHost(self.params)
    self.addCleanup(self.owner.close)
    self.sample(stamp + 60_000_000, ui_event=pending)
    self.assertEqual(self.owner.manual.code, 0)

  def test_wire_expiry_boundaries_legacy_fields_and_source_binding(self):
    stamp = MONO + 50_000_000
    event = self.request(stamp)
    observed = event.logMonoTime
    assert observation(event, observed) is not None
    assert observation(event, observed + LIFETIME_NS) is not None
    self.assertIsNone(observation(event, observed - 1))
    self.assertIsNone(observation(event, observed + LIFETIME_NS + 1))
    for field, value in (('version', 2), ('sessionId', 'bad'), ('sequence', 0), ('driveStartMonoTime', observed),
                         ('validUntilMonoTime', observed + LIFETIME_NS + 1), ('expectedManualCode', 3),
                         ('sourceCarStateMonoTime', observed + 1), ('sourceSelfdriveStateMonoTime', MONO)):
      changed = event.as_builder()
      setattr(changed.slcAction.conditionalManual, field, value)
      with self.subTest(field=field):
        self.assertIsNone(observation(changed, observed))
    for field, value in (('sessionId', 'legacy'), ('sequenceId', 1), ('decisionId', 1), ('presentationId', 1)):
      changed = event.as_builder()
      setattr(changed.slcAction, field, value)
      self.assertIsNone(observation(changed, observed))

  def test_release_checks_exact_settings_and_changed_context(self):
    stamp = MONO + 50_000_000
    self.ack(stamp)
    context = self.ui.context(self.sm, self.cp, now_ns=stamp + 1000)
    assert context is not None
    self.ack(stamp + 10_000_000)
    current = self.ui.context(self.sm, self.cp, now_ns=stamp + 10_001_000)
    self.assertEqual(context.token, current.token)
    self.params.put_bool('SafeMode', True, block=True)
    self.assertFalse(self.ui.dispatch(context, self.sm, self.cp, self.publisher, now_ns=stamp + 10_002_000))
    self.params.remove('SafeMode')
    original = self.params.get('ConditionalModeConfig')
    self.configure(choice=ModeChoice.STOCK)
    self.assertFalse(self.ui.dispatch(context, self.sm, self.cp, self.publisher, now_ns=stamp + 10_003_000))
    self.params.put('ConditionalModeConfig', original, block=True)
    self.choice = ModeChoice.CEM
    self.ack(stamp + 20_000_000, effective=True)
    self.assertFalse(self.ui.dispatch(context, self.sm, self.cp, self.publisher, now_ns=stamp + 20_001_000))
    self.assertEqual(self.publisher.events, [])

  def test_planner_rejects_changed_settings_axis_drive_and_stale_sources(self):
    stamp = MONO + 50_000_000
    event = self.request(stamp)
    self.sources(stamp + 10_000_000)
    snapshot = self.owner.settings.current
    assert snapshot is not None
    assert snapshot.document_raw is not None
    def deliver():
      self.owner._ui_event(event, snapshot, self.choice, MONO, self.sm, self.cp, stamp + 11_000_000)
      self.assertEqual(self.owner.manual.code, 0)
    self.params.put_bool('SafeMode', True, block=True)
    deliver()
    self.params.remove('SafeMode')
    Path(self.params.get_param_path('ConditionalModeConfig')).write_bytes(snapshot.document_raw + b' ')
    deliver()
    Path(self.params.get_param_path('ConditionalModeConfig')).write_bytes(snapshot.document_raw)
    for service in ('carState', 'carControl', 'selfdriveState', 'deviceState'):
      self.sm.valid[service] = False
      deliver()
      self.sm.valid[service] = True
    original = self.sm.payloads['carControl']
    changed = original.as_builder()
    changed.longActive = False
    self.sm.payloads['carControl'] = changed
    deliver()
    self.sm.payloads['carControl'] = original
    self.sm.recv_time['carState'] -= 1
    deliver()
    self.sm.recv_time['carState'] += 1
    for field, denied in (('openpilotLongitudinalControl', False), ('passive', True), ('dashcamOnly', True), ('notCar', True)):
      original_value = getattr(self.cp, field)
      setattr(self.cp, field, denied)
      deliver()
      setattr(self.cp, field, original_value)
    changed = event.as_builder()
    changed.slcAction.conditionalManual.driveStartMonoTime = MONO - 1
    self.owner._ui_event(changed, snapshot, self.choice, MONO, self.sm, self.cp, stamp + 11_000_000)
    self.assertEqual(self.owner.manual.code, 0)
    self.owner._ui_event(event, snapshot, self.choice, MONO, self.sm, self.cp, stamp + 11_000_000)
    self.assertEqual(self.owner.manual.code, 2)

  def test_ack_replay_restart_and_duplicate_dispatch(self):
    stamp = MONO + 50_000_000
    old = self.ack(stamp, sequence=10)
    context = self.ui.context(self.sm, self.cp, now_ns=stamp + 1000)
    assert context is not None
    assert context is not None
    self.assertTrue(self.ui.dispatch(context, self.sm, self.cp, self.publisher, now_ns=stamp + 2000))
    self.assertFalse(self.ui.dispatch(context, self.sm, self.cp, self.publisher, now_ns=stamp + 3000))
    self.ack(stamp + 10_000_000, sequence=9)
    self.assertIsNone(self.ui.context(self.sm, self.cp, now_ns=stamp + 10_001_000))
    self.ack(stamp + 20_000_000, sequence=1, session='c' * 32)
    self.assertIsNone(self.ui.context(self.sm, self.cp, now_ns=stamp + 20_001_000))
    assert self.ui.context(self.sm, self.cp, now_ns=stamp + 20_002_000) is not None
    self.install('starpilotSelfdriveState', old, stamp)
    self.assertIsNone(self.ui.context(self.sm, self.cp, now_ns=stamp + 20_003_000))

  def test_persistent_ui_waits_for_durable_save_and_write_failure_keeps_live(self):
    for failure in (False, True):
      with self.subTest(failure=failure):
        self.owner.close()
        self.owner = ConditionalPlannerHost(self.params)
        self.addCleanup(self.owner.close)
        self.params.remove('ConditionalManualState')
        self.configure(persist=True)
        stamp = MONO + (2 if failure else 1) * 1_000_000_000
        self.sample(stamp)
        event = self.request(stamp + 10_000_000)
        entered, release = threading.Event(), threading.Event()
        actual = manual_saved.os.fsync
        def blocked(fd, entered=entered, release=release, failure=failure, actual=actual):
          if not entered.is_set():
            entered.set()
            if not release.wait(1):
              raise RuntimeError('test release timeout')
            if failure:
              raise OSError('deliberate pre-rename failure')
          return actual(fd)
        with patch.object(manual_saved.os, 'fsync', side_effect=blocked):
          self.sample(stamp + 20_000_000, ui_event=event)
          self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
          self.sample(stamp + 21_000_000)
          self.assertTrue(entered.wait(1))
          repeated = event.as_builder()
          repeated.slcAction.conditionalManual.sequence += 1
          self.sample(stamp + 22_000_000, ui_event=repeated)
          self.assertEqual(self.owner.manual.code, 2)
          self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
          release.set()
          self.owner.manual_saved.worker.join(timeout=1)
        self.sample(stamp + 30_000_000)
        if failure:
          self.assertEqual(self.owner.manual_state.intent, ManualIntent.NONE)
          self.assertEqual(self.owner.manual_saved.status, 'write_failed')
          self.assertIsNone(read_codes(self.params).raw)
        else:
          self.settle(stamp + 40_000_000)
          self.assertEqual(self.owner.manual_state.intent, ManualIntent.FORCE_EXPERIMENTAL)
          self.assertEqual(read_codes(self.params).codes, SavedCodes(2, 0))

  def test_mixed_slc_queue_does_not_cross_domains(self):
    stamp = MONO + 50_000_000
    conditional = self.request(stamp)
    slc, manual = deque(maxlen=64), deque(maxlen=64)
    for kind in ('accept', 'reject', 'adopt'):
      event = messaging.new_message('slcAction', valid=True)
      event.logMonoTime = stamp
      event.slcAction = {'kind': kind, 'sessionId': 'normal-slc', 'sequenceId': 7, 'decisionId': 8, 'presentationId': 9}
      queue_ui_action(messaging.log_from_bytes(event.to_bytes()), slc, manual)
    queue_ui_action(conditional, slc, manual)
    self.assertEqual(len(slc), 3)
    self.assertEqual([item[1].kind for item in slc], ['accept', 'reject', 'adopt'])
    self.assertEqual(len(manual), 1)
    self.assertIs(current_ui_manual_event(manual, stamp + 3000), conditional)
    manual.extend((conditional, conditional))
    self.assertIsNone(current_ui_manual_event(manual, stamp + LIFETIME_NS + 3000))
    self.assertEqual(len(manual), 0)


if __name__ == '__main__':
  unittest.main()
