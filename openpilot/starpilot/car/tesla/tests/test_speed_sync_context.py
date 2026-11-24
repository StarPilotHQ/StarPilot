"""The comma's set speed as the Tesla's scroll target: card admits it each frame, and the car controller only scrolls
with a fresh admission that matches the set speed it is given, on a car that is safely engaged."""
import time
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest
from opendbc.car.structs import car
from opendbc.car.tesla.carcontroller import CarController
from opendbc.car.tesla.carstate import CarState
from opendbc.car.tesla.speed_sync import ENGAGE_DELAY_NS, ManualReceipt, SpeedSynchronizer, SyncAuthority, sync_controller
from opendbc.car.tesla.values import DBC
from openpilot.common.params import Params
from openpilot.starpilot.car.tesla_speed_sync_context import SETTINGS_PERIOD_NS, publish_context
from openpilot.starpilot.common.car_control_config import PARAM
from openpilot.starpilot.controllers.cruise_action import TESLA_RECEIPT_NS, CruiseActionConsumer
from openpilot.starpilot.controllers.tesla_input_protocol import document_hash
from openpilot.starpilot.controllers.wheel_actions import cp_fingerprint
from openpilot.starpilot.tests.tesla_set_speed_fixture import tesla

NEUTRAL = bytes.fromhex('2955000000000040')
MPH = 1.609344
START = 1_000_000_000
TICK = 10_000_000
IDENTITY = 'a' * 64


def di_frame(counter, speed, units):
  bits = (counter << 8) | (2 << 12) | (round(speed * 2) << 15) | (units << 24)
  data = bytearray(bits.to_bytes(8, 'little'))
  data[0] = (0x88 + sum(data[1:])) & 255
  return bytes(data)


class Rig:
  """A synchronizer watching a Tesla at 50 mph while the comma's set speed is 52 mph; ticks are 10 ms."""
  def __init__(self, *, units=0):
    self.controller = NS(speed_sync=SpeedSynchronizer(), speed_sync_authority=None)
    self.now, self.counter, self.units = START, 0, units
    self.native = 50
    self.out = car.CarState.new_message(canValid=True, gearShifter='drive', cruiseState={'enabled': True, 'available': True})
    self.out.vCruise = 52 * MPH if units == 0 else 52.
    self.cs = NS(out=self.out, autopark=False)
    self.cc = car.CarControl.new_message(enabled=True, longActive=True)
    self.sent = []

  def authority(self, **changes):
    values: dict[str, Any] = {'observed_boot_ns': self.now, 'target_kph': float(self.out.vCruise), 'is_metric': bool(self.units),
                              'configuration_id': IDENTITY, 'software_owned': True}
    values.update(changes)
    return SyncAuthority(**values)

  def tick(self, admit=True, **changes):
    self.now += TICK
    self.counter = (self.counter + 1) % 16
    self.controller.speed_sync.observe([(self.now, [(0x286, di_frame(self.counter, self.native, self.units), 0), (0x3C2, NEUTRAL, 1)])])
    self.controller.speed_sync_authority = self.authority(**changes) if admit else None
    messages = sync_controller(self.controller, self.cc.as_reader(), self.cs, self.now)
    self.sent += messages
    return messages

  def run(self, ticks=40, **kwargs):
    for _ in range(ticks):
      self.tick(**kwargs)
    return self.sent

  @property
  def status(self):
    return self.controller.speed_sync.status


def test_a_fresh_matching_admission_lets_the_tesla_be_scrolled_to_the_comma_speed():
  r = Rig()
  for _ in range(ENGAGE_DELAY_NS // TICK):
    assert r.tick() == []
  sent = r.run(5)
  assert len(sent) == 1
  address, data, bus = sent[0]
  assert (address, bus, data[3] & 63) == (0x3C2, 1, 1), 'one click up, on the Vehicle CAN'
  assert r.status == 'waiting_for_response' and r.controller.speed_sync.blocked is None


def test_matched_speeds_send_nothing():
  r = Rig()
  r.native = 52
  assert r.run() == [] and r.status == 'matched'


@pytest.mark.parametrize('changes', [
  {'software_owned': False}, {'software_owned': 1},
  {'observed_boot_ns': 0}, {'observed_boot_ns': None}, {'observed_boot_ns': 1.5e9},
  {'target_kph': float('nan')}, {'target_kph': None}, {'target_kph': '84'},
  {'is_metric': True}, {'is_metric': 0}, {'is_metric': None},
  {'configuration_id': 'a' * 63}, {'configuration_id': 'A' * 64}, {'configuration_id': 'g' * 64}, {'configuration_id': None},
])
def test_a_malformed_or_foreign_admission_sends_nothing(changes):
  r = Rig()
  assert r.run(**changes) == []
  assert r.status == 'owner_or_config_ineligible'


def test_no_admission_sends_nothing():
  r = Rig()
  assert r.run(admit=False) == [] and r.status == 'owner_or_config_ineligible'
  r.controller.speed_sync_authority = object()
  assert sync_controller(r.controller, r.cc.as_reader(), r.cs, r.now + TICK) == []
  del r.controller.speed_sync_authority
  assert sync_controller(r.controller, r.cc.as_reader(), r.cs, r.now + 2 * TICK) == []


def test_an_admission_for_another_set_speed_sends_nothing():
  r = Rig()
  assert r.run(target_kph=float(r.out.vCruise) + .1) == []
  assert r.run(target_kph=51 * MPH) == [] and r.status == 'owner_or_config_ineligible'


def test_an_old_or_future_admission_sends_nothing():
  r = Rig()
  for _ in range(40):
    r.tick(observed_boot_ns=r.now + TICK - 100_000_001)
  assert r.sent == []
  for _ in range(40):
    r.tick(observed_boot_ns=r.now + TICK + 1)
  assert r.sent == []
  for _ in range(40):
    r.tick(observed_boot_ns=r.now + TICK - 100_000_000)
  assert len(r.sent) >= 1, 'exactly at the age limit still counts'


def test_tesla_units_must_match_the_comma_units():
  r = Rig(units=1)
  assert r.run(is_metric=False) == []
  r = Rig(units=1)
  sent = r.run()
  assert len(sent) == 1 and sent[0][1][3] & 63 == 1, 'km/h: 50 to 52 starts with one click'


UNSAFE = {
  'can invalid': lambda r: setattr(r.out, 'canValid', False),
  'can timeout': lambda r: setattr(r.out, 'canTimeout', True),
  'tesla cruise off': lambda r: setattr(r.out.cruiseState, 'enabled', False),
  'not in drive': lambda r: setattr(r.out, 'gearShifter', 'reverse'),
  'standstill': lambda r: setattr(r.out, 'standstill', True),
  'brake': lambda r: setattr(r.out, 'brakePressed', True),
  'cruise fault': lambda r: setattr(r.out, 'accFaulted', True),
  'emergency braking': lambda r: setattr(r.out, 'stockAeb', True),
  'collision warning': lambda r: setattr(r.out, 'stockFcw', True),
  'stability control off': lambda r: setattr(r.out, 'espDisabled', True),
  'stability control active': lambda r: setattr(r.out, 'espActive', True),
  'sensors invalid': lambda r: setattr(r.out, 'vehicleSensorsInvalid', True),
  'car not ready': lambda r: setattr(r.out, 'carNotReady', True),
  'door open': lambda r: setattr(r.out, 'doorOpen', True),
  'seatbelt': lambda r: setattr(r.out, 'seatbeltUnlatched', True),
  'steering fault': lambda r: setattr(r.out, 'steerFaultPermanent', True),
  'steering fault for now': lambda r: setattr(r.out, 'steerFaultTemporary', True),
  'tesla lane keeping': lambda r: setattr(r.out, 'stockLkas', True),
  'lane keeping setting': lambda r: setattr(r.out, 'invalidLkasSetting', True),
  'autopark': lambda r: setattr(r.cs, 'autopark', True),
}


@pytest.mark.parametrize('reason', UNSAFE)
def test_nothing_is_sent_while_the_car_is_not_safely_engaged(reason):
  r = Rig()
  UNSAFE[reason](r)
  assert r.run() == []
  assert r.status == 'inhibited' and r.controller.speed_sync.blocked is None


def test_not_engaged_or_canceling_is_inactive():
  r = Rig()
  r.cc.enabled = False
  assert r.run() == [] and r.status == 'inactive'
  r = Rig()
  r.cc.cruiseControl.cancel = True
  assert r.run() == [] and r.status == 'inactive'


def test_without_longitudinal_control_or_the_pedal_nothing_is_sent():
  r = Rig()
  r.cc.longActive = False
  assert r.run() == [] and r.status == 'inhibited'
  r.out.gasPressed = True
  assert len(r.run()) == 1, 'the driver on the pedal is still the comma driving'


def test_a_changed_configuration_stops_sync_until_disengaged():
  r = Rig()
  assert len(r.run()) == 1
  r.native = 51
  r.tick(configuration_id='b' * 64)
  assert r.controller.speed_sync.blocked == 'configuration_changed'
  sent = len(r.sent)
  r.run()
  assert len(r.sent) == sent
  r.cc.enabled = False
  r.tick()
  r.cc.enabled = True
  assert r.controller.speed_sync.blocked is None
  r.run(configuration_id='b' * 64)
  assert len(r.sent) == sent + 1


# card's side: openpilot/starpilot/car/tesla_speed_sync_context.py

class Card:
  def __init__(self, tmp_path, cp=None, *, metric=False, v_cruise=52 * MPH):
    self.CP = tesla(selected=True) if cp is None else cp
    self.params = Params(str(tmp_path))
    self.is_metric = metric
    self.v_cruise_helper = NS(tesla_set_speed=True, v_cruise_kph=v_cruise)
    self.CI = NS(CC=NS(speed_sync=SpeedSynchronizer()))
    self.state = car.CarState.new_message(vCruise=v_cruise)

  def publish(self, now=START):
    publish_context(self, self.state, now_boot_ns=now)
    return self.CI.CC.speed_sync_authority


def test_card_admits_its_own_set_speed(tmp_path):
  card = Card(tmp_path)
  authority = card.publish()
  assert isinstance(authority, SyncAuthority)
  assert authority.observed_boot_ns == START and authority.software_owned is True and authority.is_metric is False
  assert authority.target_kph == float(card.state.vCruise) and authority.target_kph == pytest.approx(52 * MPH, abs=1e-4)
  assert len(authority.configuration_id) == 64 and set(authority.configuration_id) <= set('0123456789abcdef')


def test_the_configuration_is_the_car_and_its_units(tmp_path):
  ids = set()
  for index, (model, metric) in enumerate([('TESLA_MODEL_3', False), ('TESLA_MODEL_3', True), ('TESLA_MODEL_Y', False)]):
    path = tmp_path / str(index)
    path.mkdir()
    card = Card(path, tesla(model, selected=True), metric=metric)
    if metric:
      card.params.put_bool('IsMetric', True, block=True)
    ids.add(card.publish().configuration_id)
    assert card.publish(START + TICK).configuration_id in ids, 'the same every frame'
  assert len(ids) == 3


def test_card_stamps_with_the_boot_clock_by_default(tmp_path):
  card = Card(tmp_path)
  before = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
  publish_context(card, card.state)
  after = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
  assert before <= card.CI.CC.speed_sync_authority.observed_boot_ns <= after


@pytest.mark.parametrize('reason', ['helper does not own it', 'unset speed', 'published speed differs', 'safe mode', 'units differ',
                                    'units unreadable', 'bad clock'])
def test_card_admits_nothing_when_it_does_not_own_a_valid_set_speed(tmp_path, reason):
  card = Card(tmp_path)
  now = START
  if reason == 'helper does not own it':
    card.v_cruise_helper.tesla_set_speed = False
  if reason == 'unset speed':
    card.v_cruise_helper.v_cruise_kph = card.state.vCruise = 255
  if reason == 'published speed differs':
    card.state.vCruise = 51 * MPH
  if reason == 'safe mode':
    card.params.put_bool('SafeMode', True, block=True)
  if reason == 'units differ':
    card.params.put_bool('IsMetric', True, block=True)
  if reason == 'units unreadable':
    Path(card.params.get_param_path('IsMetric')).write_bytes(b'maybe')
  if reason == 'bad clock':
    now = 0
  assert card.publish(now) is None


def test_a_refusal_withdraws_the_previous_admission_at_once(tmp_path):
  card = Card(tmp_path)
  assert card.publish() is not None
  card.state.vCruise = 60 * MPH
  assert card.publish(START + TICK) is None
  card.v_cruise_helper.v_cruise_kph = 60 * MPH
  assert card.publish(START + 2 * TICK).target_kph == pytest.approx(60 * MPH, abs=1e-4)
  del card.v_cruise_helper
  assert card.publish(START + 3 * TICK) is None, 'an error while reading is a refusal too'


def test_saved_settings_are_read_once_a_second_not_every_frame(tmp_path):
  card = Card(tmp_path)
  assert card.publish(START) is not None
  card.params.put_bool('SafeMode', True, block=True)
  assert card.publish(START + SETTINGS_PERIOD_NS - 1) is not None, 'within the second: the earlier read'
  assert card.publish(START + SETTINGS_PERIOD_NS) is None
  card.params.put_bool('SafeMode', False, block=True)
  assert card.publish(START + 2 * SETTINGS_PERIOD_NS) is not None


# Card hands speed sync the receipts for the driver's own scrolls, with what they must match.

DRIVE = 100_000_000


def receipt(card, now, index=0):
  return ManualReceipt(boot_ns=now, frame_index=index, name='right_wheel_up', fast=False, worker_session='0123456789abcdef' * 2,
                       sequence=index + 1, document_hash=document_hash(b''), vehicle_hash=cp_fingerprint(card.CP),
                       drive_start_ns=DRIVE, processed_boot_ns=now, target_kph=float(card.state.vCruise))


def driving(tmp_path):
  card = Card(tmp_path)
  card.sm = {'deviceState': NS(startedMonoTime=DRIVE)}
  card.controller_cruise_consumer = CruiseActionConsumer()
  return card


def test_card_names_the_assignments_the_car_and_the_drive(tmp_path):
  card = driving(tmp_path)
  authority = card.publish()
  assert authority.document_hash == document_hash(b''), 'nothing saved: the hash the input worker stamps for that'
  assert authority.vehicle_hash == cp_fingerprint(card.CP) and authority.drive_start_ns == DRIVE
  assert authority.manual_receipts == ()
  saved = b'{"version":1,"vehicle":"TESLA_MODEL_3","bindings":{}}'
  Path(card.params.get_param_path(PARAM)).write_bytes(saved)
  assert card.publish(START + SETTINGS_PERIOD_NS - 1).document_hash == document_hash(b''), 'read with the settings, once a second'
  assert card.publish(START + SETTINGS_PERIOD_NS).document_hash == document_hash(saved)
  card.CP = tesla('TESLA_MODEL_Y', selected=True)
  assert card.publish(START + 2 * SETTINGS_PERIOD_NS).vehicle_hash == authority.vehicle_hash, "the car's identity is worked out once"


def test_card_offers_its_receipts_and_drops_the_ones_speed_sync_took(tmp_path):
  card = driving(tmp_path)
  first, second = receipt(card, START), receipt(card, START, 1)
  card.controller_cruise_consumer.tesla_receipts += [first, second]
  assert card.publish(START).manual_receipts == (first, second)
  card.CI.CC.speed_sync.manual_receipt_acks.append(first)   # speed sync took custody of the first
  assert card.publish(START + TICK).manual_receipts == (second,)
  assert card.controller_cruise_consumer.tesla_receipts == [second] and card.CI.CC.speed_sync.manual_receipt_acks == []
  assert card.publish(START + TESLA_RECEIPT_NS).manual_receipts == (second,)
  assert card.publish(START + TESLA_RECEIPT_NS + 1).manual_receipts == (), 'not offered past its two seconds'
  assert card.controller_cruise_consumer.tesla_receipts == []


def test_the_engine_takes_cards_receipt_through_the_admission(tmp_path):
  card = driving(tmp_path)
  card.CI.CC.speed_sync_authority = None
  item = receipt(card, START)
  card.controller_cruise_consumer.tesla_receipts.append(item)
  card.CI.CC.speed_sync.observe([(START, [(0x286, di_frame(1, 50, 0), 0)])])   # the Tesla shows mph, as card does
  card.publish(START)
  cc = car.CarControl.new_message(enabled=True, longActive=True).as_reader()
  out = car.CarState.new_message(canValid=True, gearShifter='drive', cruiseState={'enabled': True, 'available': True},
                                 vCruise=float(card.state.vCruise))
  sync_controller(card.CI.CC, cc, NS(out=out, autopark=False), START)
  assert card.CI.CC.speed_sync.manual_receipts == {(START, 0): item}, 'cached until its scroll is seen on the bus'
  card.publish(START + TICK)
  assert card.controller_cruise_consumer.tesla_receipts == [], 'and handed back to card the next frame'


def test_receipts_wait_with_card_while_it_admits_nothing(tmp_path):
  card = driving(tmp_path)
  item = receipt(card, START)
  card.controller_cruise_consumer.tesla_receipts.append(item)
  card.v_cruise_helper.tesla_set_speed = False
  assert card.publish(START) is None and card.controller_cruise_consumer.tesla_receipts == [item]
  card.v_cruise_helper.tesla_set_speed = True
  assert card.publish(START + TICK).manual_receipts == (item,)


def test_card_without_a_step_consumer_or_drive_offers_no_receipts(tmp_path):
  card = Card(tmp_path)
  authority = card.publish()
  assert authority.manual_receipts == () and authority.drive_start_ns == 0 and len(authority.document_hash) == 64


@pytest.mark.parametrize('controller', [None, NS(), NS(speed_sync=None)])
def test_card_does_nothing_for_a_controller_without_a_synchronizer(tmp_path, controller):
  card = Card(tmp_path)
  card.CI.CC = controller
  publish_context(card, card.state, now_boot_ns=START)
  assert not hasattr(controller, 'speed_sync_authority')


@pytest.mark.parametrize('model', ['TESLA_MODEL_3', 'TESLA_MODEL_Y'])
def test_the_real_controller_scrolls_only_with_cards_admission(tmp_path, model):
  cp = tesla(model, selected=True)
  controller = CarController(DBC[model], cp)
  cs = CarState(cp)
  cs.hands_on_level = 0
  cs.das_control = {'DAS_controlCounter': 0}
  cs.out = car.CarState.new_message(canValid=True, gearShifter='drive', vEgo=22., vEgoRaw=22., vCruise=52 * MPH,
                                    cruiseState={'enabled': True, 'available': True, 'speed': 50 * .44704})
  card = Card(tmp_path, cp)
  card.CI.CC = controller
  card.state = cs.out
  cc = car.CarControl.new_message(enabled=True, longActive=True).as_reader()

  def drive(frames, admit):
    scrolls, counter = [], 0
    for _ in range(frames):
      now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
      counter = (counter + 1) % 16
      controller.speed_sync.observe([(now, [(0x286, di_frame(counter, 50, 0), 0), (0x3C2, NEUTRAL, 1)])])
      if admit:
        publish_context(card, cs.out)
      else:
        controller.speed_sync_authority = None
      _, sends = controller.update(cc, cs, now)
      scrolls += [message for message in sends if message[0] == 0x3C2]
      time.sleep(.01)
    return scrolls

  assert drive(25, admit=False) == [], 'the controller alone never scrolls'
  scrolls = drive(40, admit=True)
  assert len(scrolls) >= 1 and all(bus == 1 and data[3] & 63 == 1 for _, data, bus in scrolls)
