"""Physical distance gestures reserve only their mapped custom action."""

import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.car.card import Car
from openpilot.selfdrive.car.tests.publication_fixture import initialize_publication_sources
from openpilot.selfdrive.car.cruise import VCruiseHelper, CRUISE_LONG_PRESS
from openpilot.selfdrive.selfdrived.events import EventName, Events
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.aol.intent import AolCardIntent, read_settings as read_aol_settings
from openpilot.selfdrive.car.tests.test_hyundai_aol import candidate as ioniq6_candidate
from openpilot.starpilot.conditional_mode.manual import ButtonTracker
from openpilot.starpilot.lateral.low_speed_advisory import LowSpeedAdvisory


ButtonType = car.CarState.ButtonEvent.Type


def state(*events):
  return car.CarState(canValid=True, buttonEvents=[car.CarState.ButtonEvent(type=ButtonType.gapAdjustCruise, pressed=pressed)
                                                  for pressed in events])


class Publisher:
  def __init__(self):
    self.messages = []

  def send(self, service, message):
    self.messages.append((service, message.to_bytes()))


class TestDistancePersonalityClaim(unittest.TestCase):
  def setUp(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    self.params = Params(temp.name)
    _, long_cp = ioniq6_candidate(False)
    self.cp = long_cp
    self.publisher = Publisher()
    self.card = Car.__new__(Car)
    self.enterContext(patch.multiple(self.card, create=True, params=self.params, CP=self.cp,
                                    distance_personality_tracker=ButtonTracker(), aol_card_intent=None,
                                    sm=NS(frame=1, all_checks=lambda _: True), pm=self.publisher,
                                    slc_replay=False, curve_replay=False, conditional_replay=False, aol_replay=False,
                                    last_actuators_output=car.CarControl.Actuators(), can_rcv_cum_timeout_counter=0,
                                    rk=NS(remaining=0.0), v_cruise_helper=VCruiseHelper(self.cp), slc_receipts=[]))
    initialize_publication_sources(self.card)
    Car.observe_distance_personality(self.card, state(), 1)

  def observe(self, physical, tick):
    Car.observe_distance_personality(self.card, physical, tick)
    return physical

  def published(self, physical):
    self.publisher.messages.clear()
    Car.state_publish(self.card, physical, None)
    return next(messaging.log_from_bytes(payload).carState for service, payload in self.publisher.messages
                if service == 'carState')

  def test_custom_short_and_conditional_code_reserve_release(self):
    for code in (3, 4, 5):
      with self.subTest(code=code):
        self.params.put('DistanceButtonControl', code, block=True)
        self.observe(state(True), 10)
        release = self.observe(state(False), 11)
        self.assertEqual(len(self.published(release).buttonEvents), 0)
        self.assertEqual(len(release.buttonEvents), 1)
        self.params.put('DistanceButtonControl', 0, block=True)
        self.observe(state(True), 12)
        release = self.observe(state(False), 13)
        self.assertEqual(len(self.published(release).buttonEvents), 1)

  def test_long_mapping_preserves_short_fallback_and_claims_later_release(self):
    self.params.put('LongDistanceButtonControl', 3, block=True)
    self.observe(state(True), 10)
    self.assertEqual(len(self.published(self.observe(state(False), 11)).buttonEvents), 1)
    self.observe(state(True), 20)
    for tick in range(CRUISE_LONG_PRESS - 1):
      self.observe(state(), 21 + tick)
    self.params.put('LongDistanceButtonControl', 0, block=True)
    release = self.observe(state(False), 100)
    self.assertEqual(len(self.published(release).buttonEvents), 0)
    self.observe(state(True), 101)
    self.assertEqual(len(self.published(self.observe(state(False), 102)).buttonEvents), 1)

  def test_very_long_and_unqualified_keep_unmapped_fallback(self):
    self.params.put('VeryLongDistanceButtonControl', 5, block=True)
    self.observe(state(True), 10)
    for tick in range(CRUISE_LONG_PRESS * 5 - 1):
      self.observe(state(), 11 + tick)
    self.assertEqual(len(self.published(self.observe(state(False), 300)).buttonEvents), 0)
    stock, _ = ioniq6_candidate(False)
    self.card.CP = stock
    self.card.distance_personality_tracker = None
    self.assertEqual(len(self.published(state(False)).buttonEvents), 1)

  def test_base_ioniq_long_without_aol_profile_reserves_conditional_gesture(self):
    self.card.CP.safetyConfigs[0].safetyParam &= ~0x800
    self.params.put('DistanceButtonControl', 5, block=True)
    self.observe(state(True), 10)
    self.assertEqual(len(self.published(self.observe(state(False), 11)).buttonEvents), 0)

  def test_startup_held_and_invalid_can_do_not_inherit_a_claim(self):
    self.params.put('DistanceButtonControl', 3, block=True)
    self.params.put_bool('AlwaysOnLateral', True, block=True)
    self.card.aol_card_intent = AolCardIntent(read_aol_settings(self.params), explicit_latch=True)
    self.card.distance_personality_tracker = ButtonTracker()
    pressed = state(True)
    self.card.aol_card_intent.update(pressed)
    self.observe(pressed, 10)
    release = state(False)
    self.card.aol_card_intent.update(release)
    self.observe(release, 11)
    self.assertFalse(self.card.aol_card_intent.pause_lateral)
    self.assertEqual(len(self.published(release).buttonEvents), 1)

    self.observe(state(), 20)
    self.card.aol_card_intent.update(state())
    self.params.put('LongDistanceButtonControl', 3, block=True)
    self.observe(state(True), 21)
    for tick in range(CRUISE_LONG_PRESS - 1):
      self.observe(state(), 22 + tick)
    self.assertIsNotNone(self.card.distance_personality_tracker.distance_claim)
    self.observe(car.CarState(canValid=False), 80)
    self.assertIsNone(self.card.distance_personality_tracker.distance_claim)
    self.assertEqual(len(self.published(self.observe(state(False), 81)).buttonEvents), 1)
    self.observe(state(), 82)
    self.card.aol_card_intent.update(state())
    self.observe(state(True), 83)
    self.card.aol_card_intent.update(state(True))
    self.observe(state(False), 84)
    self.card.aol_card_intent.update(state(False))
    self.assertTrue(self.card.aol_card_intent.pause_lateral)

    self.observe(state(True), 90)
    self.card.aol_card_intent.update(state(True))
    timeout = car.CarState(canValid=True, canTimeout=True)
    self.observe(timeout, 91)
    self.card.aol_card_intent.update(timeout)
    self.assertIsNone(self.card.distance_personality_tracker.distance_claim)
    self.observe(state(False), 92)
    self.card.aol_card_intent.update(state(False))
    self.assertTrue(self.card.aol_card_intent.pause_lateral)

  def test_mid_press_saved_change_keeps_sampled_aol_action_reserved(self):
    self.params.put('DistanceButtonControl', 3, block=True)
    self.params.put_bool('AlwaysOnLateral', True, block=True)
    self.card.aol_card_intent = AolCardIntent(read_aol_settings(self.params), explicit_latch=True)
    self.card.aol_card_intent.update(state())
    pressed = state(True)
    self.card.aol_card_intent.update(pressed)
    self.observe(pressed, 10)
    self.params.put('DistanceButtonControl', 0, block=True)
    release = state(False)
    self.card.aol_card_intent.update(release)
    self.observe(release, 11)
    self.assertTrue(self.card.aol_card_intent.pause_lateral)
    self.assertEqual(len(self.published(release).buttonEvents), 0)

  def test_lkas_first_startup_interleaving_does_not_diverge_from_tracker(self):
    self.params.put('DistanceButtonControl', 3, block=True)
    self.params.put_bool('AlwaysOnLateral', True, block=True)
    self.card.aol_card_intent = AolCardIntent(read_aol_settings(self.params), explicit_latch=True)
    self.card.distance_personality_tracker = ButtonTracker()
    lkas = car.CarState(canValid=True, buttonEvents=[car.CarState.ButtonEvent(type=ButtonType.lkas, pressed=True)])
    self.card.aol_card_intent.update(lkas)
    self.observe(lkas, 10)
    pressed = state(True)
    self.card.aol_card_intent.update(pressed)
    self.observe(pressed, 11)
    release = state(False)
    self.card.aol_card_intent.update(release)
    self.observe(release, 12)
    self.assertFalse(self.card.aol_card_intent.pause_lateral)
    self.assertEqual(len(self.published(release).buttonEvents), 1)

  def test_selfdrived_only_cycles_for_unclaimed_serialized_release(self):
    self.params.put('LongDistanceButtonControl', 3, block=True)
    self.observe(state(True), 10)
    short = self.published(self.observe(state(False), 11))
    self.observe(state(True), 20)
    for tick in range(CRUISE_LONG_PRESS - 1):
      self.observe(state(), 21 + tick)
    claimed = self.published(self.observe(state(False), 100))
    self.assertEqual((len(short.buttonEvents), len(claimed.buttonEvents)), (1, 0))
    drive = SelfdriveD.__new__(SelfdriveD)
    drive.CP = car.CarParams(openpilotLongitudinalControl=True, notCar=True)
    from openpilot.starpilot.car.tesla.stock_events import StockCruiseConsumer
    drive.tesla_stock_consumer = StockCruiseConsumer(drive.CP)
    drive.low_speed_advisory = LowSpeedAdvisory()
    drive.params = MagicMock()
    drive.events = Events()
    drive.personality = 1
    drive.initialized = True
    drive.startup_event = None
    drive.big_model_loading = False
    drive.big_model_active = False
    drive.big_model_failed = False
    drive.big_model_ready_t = 0.0
    drive.jetlink_swaps = None
    drive.enabled = False
    drive.active = False
    drive.aol_replay = False
    drive.nostalgia_enabled = False
    drive.CS_prev = car.CarState.new_message()
    drive.dm_lockout_set = False
    drive.dm_uncertain_alerted = False
    drive.distance_traveled = 0.0
    drive.last_functional_fan_frame = 0
    drive.recalibrating_seen = False
    drive.is_ldw_enabled = False
    drive.calibrated_pose = None
    drive.excessive_actuation = False
    drive.not_running_prev = None
    drive.logged_comm_issue = None
    drive.sensor_packets = []
    drive.camera_packets = []
    drive.cruise_mismatch_counter = 0
    drive.last_steering_pressed_frame = 0
    drive.gps_location_service = 'gpsLocationExternal'
    services = ('controlsState', 'deviceState', 'driverMonitoringState', 'peripheralState',
                'extrinsicsCalibration', 'modelV2', 'managerState', 'radarState', 'longitudinalPlan',
                'deviceMotion', 'vehicleParameters', 'carControl')
    messages = {service: getattr(messaging.new_message(service), service) for service in services}
    messages['pandaStates'] = []
    class FakeMaster:
      frame = 1

      def __init__(self):
        self.updated = {}
        self.seen = {}
        self.valid = {}
        self.alive = {}
        self.freq_ok = {}
        self.recv_frame = {}

      def __getitem__(self, key):
        return messages[key]

      def all_checks(self, *_):
        return True

      def all_alive(self, *_):
        return True

      def all_freq_ok(self, *_):
        return True

    self.enterContext(patch.multiple(drive, create=True, sm=FakeMaster(), rk=NS(lagging=False),
                                    car_events=NS(update=lambda *_: NS(to_msg=list))))
    for service in (*services, 'userBookmark', 'alertDebug', 'lateralManeuverPlan', 'managerState',
                    'gpsLocationExternal', 'deviceMotion', 'vehicleParameters'):
      drive.sm.updated[service] = False
      drive.sm.seen[service] = False
      drive.sm.valid[service] = True
      drive.sm.alive[service] = True
      drive.sm.freq_ok[service] = True
      drive.sm.recv_frame[service] = 0
    drive.update_events(short)
    self.assertEqual(drive.personality, 0)
    self.assertIn(EventName.personalityChanged, drive.events.names)
    drive.update_events(claimed)
    self.assertEqual(drive.personality, 0)
    self.assertNotIn(EventName.personalityChanged, drive.events.names)
