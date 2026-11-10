"""Card's physical conditional-mode receipt is distinct from cruise actions."""

import tempfile
import time
import unittest
import json
from pathlib import Path
from types import MethodType, SimpleNamespace as NS
from typing import cast
from unittest.mock import patch

from opendbc.car.structs import car
from opendbc.car.honda.interface import CarInterface as HondaInterface
from opendbc.car.honda.values import CAR as HONDA_CAR
from opendbc.car.hyundai.ioniq6_media import MediaObservation, MediaSample
from opendbc.car.hyundai.values import CAR as HYUNDAI_CAR, HyundaiFlags
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.car.card import Car, conditional_manual_candidate, conditional_traffic_candidate
from openpilot.selfdrive.car.tests.publication_fixture import initialize_publication_sources
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.starpilot.conditional_mode.manual import Button, ButtonTracker, Gesture, Press
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import CEMOptions, SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner


ButtonType = car.CarState.ButtonEvent.Type


def state(*events):
  return car.CarState(canValid=True, buttonEvents=[car.CarState.ButtonEvent(type=button, pressed=pressed)
                                                  for button, pressed in events])


class Publisher:
  def __init__(self):
    self.events = []

  def send(self, service, event):
    self.events.append((service, event.to_bytes()))


class FakeSM:
  def __init__(self, now, drive_id):
    self.logMonoTime = {'carControl': now, 'deviceState': now}
    self.valid = {'carControl': True, 'deviceState': True}
    self.alive = {'carControl': True, 'deviceState': True}
    self.data = {'carControl': NS(enabled=True, longActive=True),
                 'deviceState': NS(started=True, startedMonoTime=drive_id)}
    self.frame = 1

  def __getitem__(self, key):
    return self.data[key]

  def all_checks(self, _services):
    return True


class CardManualEventTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.params = Params(self.directory.name)
    self.params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CEM))), block=True)
    self.owner = ConditionalSettingsOwner(self.params)
    self.tracker = ButtonTracker()
    self.cp = car.CarParams(openpilotLongitudinalControl=True, pcmCruise=False)
    self.cp.init('safetyConfigs', 1)  # One Panda maps the physical buses to 0–3.
    self.now = time.monotonic_ns()
    self.drive_id = self.now - 1_000_000_000
    self.sm = FakeSM(self.now, self.drive_id)
    self.tracker.observe(state())

  def candidate(self, *events, explicit_aol=False, media=None):
    return conditional_manual_candidate(state(*events), self.tracker, self.params, self.owner,
                                        self.cp, self.sm, now_ns=self.now,
                                        explicit_aol_latch=explicit_aol, media=media)

  @staticmethod
  def media(*samples):
    return MediaObservation(tuple(MediaSample(mode, custom, stamp) for mode, custom, stamp in samples),
                            samples[-1][2], 0, True)

  def test_traffic_media_uses_only_new_packet_durations_across_empty_card_polls(self):
    self.cp.carFingerprint = HYUNDAI_CAR.HYUNDAI_IONIQ_6
    self.cp.safetyConfigs = [{'safetyModel': 'hyundaiCanfd'}]
    self.cp.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    self.params.put('ModeButtonControl', 6, block=True)
    self.params.put('LongModeButtonControl', 6, block=True)
    self.params.put('VeryLongModeButtonControl', 6, block=True)
    tracker = ButtonTracker()

    def pulse(*samples):
      observation = self.media(*samples) if samples else MediaObservation((), 1_300_000_000, 0, True)
      return conditional_traffic_candidate(state(), tracker, self.params, self.owner, self.cp,
                                           self.sm, now_ns=self.now, media=observation)

    self.assertFalse(pulse((False, False, 1_000_000_000))[0])
    for _ in range(15):
      self.assertIsNone(pulse())
    self.assertFalse(pulse((True, False, 1_200_000_000))[0])
    for _ in range(15):
      self.assertIsNone(pulse())
    short = pulse((False, False, 1_400_000_000))
    self.assertTrue(short[0])
    self.assertEqual(short[1], Gesture(Button.MODE, Press.SHORT))
    pulse((True, False, 1_600_000_000))
    for _ in range(50):
      self.assertIsNone(pulse())
    self.assertFalse(pulse((True, False, 1_800_000_000))[0])
    self.assertFalse(pulse((True, False, 2_000_000_000))[0])
    self.assertEqual((tracker.media_press_ns, tracker.media_long_emitted), (1_600_000_000, False))
    long = pulse((True, False, 2_200_000_000))
    self.assertTrue(long[0], long)
    self.assertEqual(long[1], Gesture(Button.MODE, Press.LONG))
    self.assertFalse(pulse((False, False, 2_400_000_000))[0])
    pulse((True, False, 2_600_000_000))
    for stamp in range(2_800_000_000, 5_200_000_000, 200_000_000):
      pulse((True, False, stamp))
    very_long = pulse((True, False, 5_200_000_000))
    self.assertTrue(very_long[0])
    self.assertEqual(very_long[1], Gesture(Button.MODE, Press.VERY_LONG))

  def test_exact_ioniq_media_assignment_requires_source_and_host_authority(self):
    self.params.put('ModeButtonControl', 5, block=True)
    self.cp.carFingerprint = HYUNDAI_CAR.HYUNDAI_IONIQ_6
    self.cp.safetyConfigs = [{'safetyModel': 'hyundaiCanfd'}]
    self.assertIsNone(self.candidate(media=self.media((False, False, 700_000_000),
                                                   (True, False, 900_000_000),
                                                   (False, False, 1_000_000_000))))  # ECAN is 0 in this CP.
    self.cp.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    self.assertIsNone(self.candidate(media=self.media((False, False, 1_000_000_000))))
    self.assertIsNone(self.candidate(media=self.media((True, False, 1_200_000_000))))
    self.assertIsNone(self.candidate(media=MediaObservation((), 1_300_000_000, 0, True)))
    receipt = self.candidate(media=self.media((False, False, 1_400_000_000)))
    self.assertEqual(receipt[0], Gesture(Button.MODE, Press.SHORT))
    publisher = Publisher()
    fake = NS(sm=self.sm, pm=publisher, CP=self.cp, car_params_published=False, slc_replay=False, curve_replay=False,
              conditional_replay=True, aol_replay=False, manual_receipt=receipt,
              manual_event_sequence=0, slc_cruise_event_id=0, slc_producer_session='a' * 32,
              last_actuators_output=car.CarControl.Actuators(), can_rcv_cum_timeout_counter=0,
              rk=NS(remaining=0.0), v_cruise_helper=VCruiseHelper(self.cp), slc_receipts=[])
    initialize_publication_sources(fake)
    fake.radar_publish = MethodType(Car.radar_publish, fake)
    fake._feedback_capture = MethodType(Car._feedback_capture, fake)
    fake.publish_traffic_receipt = lambda event: Car.publish_traffic_receipt(cast(Car, fake), event)
    Car.state_publish(cast(Car, fake), state(), None)
    records = [messaging.log_from_bytes(payload).slcCruiseEvent for service, payload in publisher.events
               if service == 'slcCruiseEvent']
    self.assertEqual((str(records[0].manualMode.button), str(records[0].manualMode.press)), ('mode', 'short'))
    self.sm.data['carControl'].longActive = False
    self.assertIsNone(self.candidate(media=self.media((True, False, 1_600_000_000),
                                                   (False, False, 1_800_000_000))))
    self.sm.data['carControl'].longActive = True
    self.cp.carFingerprint = HONDA_CAR.HONDA_CIVIC
    self.assertIsNone(self.candidate(media=self.media((True, False, 2_000_000_000),
                                                   (False, False, 2_200_000_000))))

  def test_ordinary_buttons_do_not_depend_on_optional_media_cache(self):
    self.cp.carFingerprint = HYUNDAI_CAR.HYUNDAI_IONIQ_6
    self.cp.safetyConfigs = [{'safetyModel': 'hyundaiCanfd'}]
    self.cp.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    self.params.put('LKASButtonControl', 5, block=True)
    self.params.put('DistanceButtonControl', 5, block=True)

    def physical(*events, media=None):
      return conditional_manual_candidate(state(*events), self.tracker, self.params, self.owner,
                                          self.cp, self.sm, now_ns=self.now, media=media,
                                          media_buttons=None, media_map_captured=True)

    self.assertEqual(physical((ButtonType.lkas, True))[0], Gesture(Button.LKAS, Press.SHORT))
    self.params.put('LKASButtonControl', 0, block=True)
    self.assertIsNone(physical((ButtonType.lkas, True), media=MediaObservation((), 1, 0, False)))
    self.assertIsNone(physical((ButtonType.gapAdjustCruise, True)))
    self.assertEqual(physical((ButtonType.gapAdjustCruise, False))[0], Gesture(Button.DISTANCE, Press.SHORT))
    self.params.put('DistanceButtonControl', 0, block=True)
    self.assertIsNone(physical((ButtonType.gapAdjustCruise, True)))
    self.assertIsNone(physical((ButtonType.gapAdjustCruise, False)))

  def test_ioniq_media_hold_cannot_cross_map_or_drive_change(self):
    self.cp.carFingerprint = HYUNDAI_CAR.HYUNDAI_IONIQ_6
    self.cp.safetyConfigs = [{'safetyModel': 'hyundaiCanfd'}]
    self.cp.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    self.params.put('ModeButtonControl', 5, block=True)
    self.candidate(media=self.media((False, False, 1_000_000_000)))
    self.candidate(media=self.media((True, False, 1_200_000_000)))
    self.params.put('ModeButtonControl', 0, block=True)
    self.assertIsNone(self.candidate(media=self.media((False, False, 1_400_000_000))))
    self.params.put('ModeButtonControl', 5, block=True)
    self.candidate(media=self.media((True, False, 1_600_000_000)))
    self.sm.data['deviceState'].startedMonoTime += 1
    self.assertIsNone(self.candidate(media=self.media((False, False, 1_800_000_000))))
    self.assertIsNone(self.candidate(media=self.media((True, False, 2_000_000_000))))
    self.assertIsNotNone(self.candidate(media=self.media((False, False, 2_200_000_000))))

  def test_default_none_and_explicit_lkas_only(self):
    self.assertIsNone(self.candidate((ButtonType.lkas, True)))
    self.params.put('LKASButtonControl', 5, block=True)
    self.candidate((ButtonType.lkas, False))
    candidate = self.candidate((ButtonType.lkas, True))
    self.assertEqual((candidate[0], candidate[1], candidate[3]),
                     (Gesture(Button.LKAS, Press.SHORT), self.drive_id, ModeChoice.CEM))
    self.assertEqual(len(candidate[2]), 64)
    self.assertIsNone(self.candidate((ButtonType.lkas, True), explicit_aol=True))

  def test_missing_authority_safe_mode_and_corruption_deny_but_persistence_receipt_allowed(self):
    self.params.put('LKASButtonControl', 5, block=True)
    self.sm.data['carControl'].longActive = False
    self.assertIsNone(self.candidate((ButtonType.lkas, True)))
    self.sm.data['carControl'].longActive = True
    self.params.put_bool('SafeMode', True, block=True)
    self.now += 1_000_000_000
    self.sm.logMonoTime = {'carControl': self.now, 'deviceState': self.now}
    self.assertIsNone(self.candidate((ButtonType.lkas, True)))
    self.params.put_bool('SafeMode', False, block=True)
    self.params.put('ConditionalModeConfig',
                    json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CEM,
                                                                   cem=CEMOptions(persist_manual=True)))), block=True)
    self.now += 1_000_000_000
    self.sm.logMonoTime = {'carControl': self.now, 'deviceState': self.now}
    self.assertIsNotNone(self.candidate((ButtonType.lkas, True)))
    self.params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CEM))), block=True)
    Path(self.params.get_param_path('LKASButtonControl')).write_bytes(b'broken')
    self.now += 1_000_000_000
    self.sm.logMonoTime = {'carControl': self.now, 'deviceState': self.now}
    self.assertIsNone(self.candidate((ButtonType.lkas, True)))

  def test_actual_nidec_system_long_with_pcm_cruise_is_eligible(self):
    self.cp = HondaInterface.get_params(HONDA_CAR.HONDA_CIVIC, {0: {}, 1: {}, 2: {}}, [], False, False, False)
    self.assertTrue(self.cp.openpilotLongitudinalControl)
    self.assertTrue(self.cp.pcmCruise)
    self.params.put('LKASButtonControl', 5, block=True)
    self.assertIsNotNone(self.candidate((ButtonType.lkas, True)))

    self.cp.openpilotLongitudinalControl = False
    self.candidate((ButtonType.lkas, False))
    self.assertIsNone(self.candidate((ButtonType.lkas, True)))

  def test_card_publishes_nested_receipt_and_neutral_cruise_fields(self):
    self.params.put('LKASButtonControl', 5, block=True)
    receipt = self.candidate((ButtonType.lkas, True))
    publisher = Publisher()
    fake = NS(sm=self.sm, pm=publisher, CP=self.cp, car_params_published=False, slc_replay=False, curve_replay=False,
              conditional_replay=True, aol_replay=False, manual_receipt=receipt,
              manual_event_sequence=0, slc_cruise_event_id=0, slc_producer_session='a' * 32,
              last_actuators_output=car.CarControl.Actuators(), can_rcv_cum_timeout_counter=0,
              rk=NS(remaining=0.0), v_cruise_helper=VCruiseHelper(self.cp), slc_receipts=[])
    initialize_publication_sources(fake)
    fake.radar_publish = MethodType(Car.radar_publish, fake)
    fake._feedback_capture = MethodType(Car._feedback_capture, fake)
    fake.publish_traffic_receipt = lambda event: Car.publish_traffic_receipt(cast(Car, fake), event)
    Car.state_publish(cast(Car, fake), state(), None)
    published = [messaging.log_from_bytes(payload) for service, payload in publisher.events
                 if service == 'slcCruiseEvent']
    self.assertEqual(len(published), 1)
    record = published[0].slcCruiseEvent
    self.assertEqual(str(record.kind), 'conditionalMode')
    self.assertEqual((record.eventId, record.previousMps, record.selectedMps), (1, 0.0, 0.0))
    nested = record.manualMode
    self.assertEqual((nested.version, nested.sequence, nested.driveStartMonoTime), (1, 1, self.drive_id))
    self.assertEqual((str(nested.choice), str(nested.button), str(nested.press)),
                     ('conditionalExperimental', 'lkas', 'short'))
    self.assertEqual(str(nested.settingsFingerprint), receipt[2])
    self.assertEqual(nested.sourceCarStateMonoTime, published[0].logMonoTime)
    self.assertLessEqual(nested.observedMonoTime, nested.sourceCarStateMonoTime)

  def test_recorded_clock_and_car_state_validity_follow_receipt(self):
    self.params.put('LKASButtonControl', 5, block=True)
    receipt = self.candidate((ButtonType.lkas, True))
    publisher = Publisher()
    fake = NS(sm=self.sm, pm=publisher, CP=self.cp, car_params_published=False, slc_replay=False, curve_replay=False,
              conditional_replay=True, aol_replay=False, manual_receipt=receipt,
              manual_event_sequence=0, slc_cruise_event_id=0, slc_producer_session='a' * 32,
              last_actuators_output=car.CarControl.Actuators(), can_rcv_cum_timeout_counter=0,
              rk=NS(remaining=0.0), v_cruise_helper=VCruiseHelper(self.cp), slc_receipts=[],
              can_log_mono_time=self.now + 2_000_000)
    initialize_publication_sources(fake)
    fake.radar_publish = MethodType(Car.radar_publish, fake)
    fake._feedback_capture = MethodType(Car._feedback_capture, fake)
    invalid = car.CarState(canValid=False)
    with patch('openpilot.selfdrive.car.card.REPLAY', True):
      fake.publish_traffic_receipt = lambda event: Car.publish_traffic_receipt(cast(Car, fake), event)
      Car.state_publish(cast(Car, fake), invalid, None)
    published = [messaging.log_from_bytes(payload) for service, payload in publisher.events
                 if service == 'slcCruiseEvent']
    self.assertEqual(len(published), 1)
    self.assertFalse(published[0].valid)
    self.assertEqual(published[0].logMonoTime, fake.can_log_mono_time)
    self.assertEqual(published[0].slcCruiseEvent.manualMode.sourceCarStateMonoTime, fake.can_log_mono_time)

  def test_claimed_distance_release_does_not_change_published_personality_input(self):
    self.params.put('DistanceButtonControl', 5, block=True)
    self.assertIsNone(self.candidate((ButtonType.gapAdjustCruise, True)))
    release = state((ButtonType.gapAdjustCruise, False))
    receipt = conditional_manual_candidate(release, self.tracker, self.params, self.owner,
                                           self.cp, self.sm, now_ns=self.now)
    self.assertEqual(receipt[0], Gesture(Button.DISTANCE, Press.SHORT))
    self.tracker.claim(receipt[0], receipt[1], receipt[2])
    self.assertTrue(self.tracker.suppress_distance_release)
    publisher = Publisher()
    fake = NS(sm=self.sm, pm=publisher, CP=self.cp, car_params_published=False, slc_replay=False, curve_replay=False,
              conditional_replay=True, aol_replay=False, manual_receipt=receipt,
              manual_button_tracker=self.tracker, manual_event_sequence=0, slc_cruise_event_id=0,
              slc_producer_session='a' * 32, last_actuators_output=car.CarControl.Actuators(),
              can_rcv_cum_timeout_counter=0, rk=NS(remaining=0.0),
              v_cruise_helper=VCruiseHelper(self.cp), slc_receipts=[])
    initialize_publication_sources(fake)
    fake.radar_publish = MethodType(Car.radar_publish, fake)
    fake._feedback_capture = MethodType(Car._feedback_capture, fake)
    fake.publish_traffic_receipt = lambda event: Car.publish_traffic_receipt(cast(Car, fake), event)
    Car.state_publish(cast(Car, fake), release, None)
    car_events = [messaging.log_from_bytes(payload).carState for service, payload in publisher.events if service == 'carState']
    self.assertEqual(len(car_events), 1)
    self.assertEqual(len(car_events[0].buttonEvents), 0)
    self.assertEqual(len(release.buttonEvents), 1)  # Original state remains physical.
    self.assertTrue(any(service == 'slcCruiseEvent' for service, _ in publisher.events))

  def test_long_and_very_long_distance_claims_own_later_release(self):
    for key, frames, expected in (('LongDistanceButtonControl', 50, Press.LONG),
                                  ('VeryLongDistanceButtonControl', 250, Press.VERY_LONG)):
      with self.subTest(key=key):
        self.tracker = ButtonTracker()
        self.tracker.observe(state())
        self.params.put(key, 5, block=True)
        self.assertIsNone(self.candidate((ButtonType.gapAdjustCruise, True)))
        for _ in range(frames - 2):
          self.candidate()
        receipt = self.candidate()
        self.assertEqual(receipt[0], Gesture(Button.DISTANCE, expected))
        self.tracker.claim(receipt[0], receipt[1], receipt[2])
        self.assertIsNone(self.candidate((ButtonType.gapAdjustCruise, False)))
        self.assertTrue(self.tracker.suppress_distance_release)
        self.assertIsNone(self.tracker.distance_claim)
        self.params.remove(key)

  def test_unclaimed_and_invalid_distance_release_remain_physical(self):
    self.candidate((ButtonType.gapAdjustCruise, True))
    self.assertIsNone(self.candidate((ButtonType.gapAdjustCruise, False)))
    self.assertFalse(self.tracker.suppress_distance_release)
    self.params.put('DistanceButtonControl', 5, block=True)
    self.candidate((ButtonType.gapAdjustCruise, True))
    self.sm.data['carControl'].longActive = False
    self.assertIsNone(self.candidate((ButtonType.gapAdjustCruise, False)))
    self.assertFalse(self.tracker.suppress_distance_release)
    self.sm.data['carControl'].longActive = True
    self.candidate((ButtonType.gapAdjustCruise, True))
    self.tracker.observe(car.CarState(canValid=False))
    self.assertIsNone(self.candidate((ButtonType.gapAdjustCruise, False)))
    self.assertFalse(self.tracker.suppress_distance_release)


if __name__ == '__main__':
  unittest.main()
