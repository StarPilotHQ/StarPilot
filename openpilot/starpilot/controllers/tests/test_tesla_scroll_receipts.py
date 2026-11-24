"""The driver's own scroll, followed from the wheel to speed sync.

The input worker reports where it saw the scroll on the car's bus, the screen attaches that to the comma step it asks
for, card records a receipt when it applies the step, and speed sync matches the receipt to the scroll it saw itself.
With the receipt it reads the comma's set speed again at once; without one it waits out its two-second window.
None of this metadata ever creates, repeats or alters a speed action.
"""
from dataclasses import replace
import json
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from opendbc.car.structs import car
from opendbc.car.tesla.speed_sync import MANUAL_COMPLETION_NS, ManualReceipt, SpeedSynchronizer, sync_controller
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.starpilot.car.tesla_speed_sync_context import publish_context
from openpilot.starpilot.common.car_control_config import SPEED_DOWN, SPEED_UP
from openpilot.starpilot.controllers import cruise_action
from openpilot.starpilot.controllers.cruise_action import TESLA_RECEIPT_NS, CruiseActionConsumer, CruiseActionPublisher
from openpilot.starpilot.controllers.tesla_gesture_context import GestureSource, current_source, parse_source, source_from_event, source_scope
from openpilot.starpilot.controllers.tesla_input_protocol import decode_event, document_hash, encode_event
from openpilot.starpilot.controllers.tests.tesla_worker_fixture import build
from openpilot.starpilot.controllers.tests.test_cruise_action import Publisher
from openpilot.starpilot.controllers.tests.test_tesla_decoder import wheel
from openpilot.starpilot.controllers.wheel_actions import cp_fingerprint
from openpilot.starpilot.tests.tesla_set_speed_fixture import tesla

MPH = 1.609344
SESSION = '0123456789abcdef' * 2
DOCUMENT, VEHICLE, DRIVE = 'a' * 64, 'b' * 64, 100_000_000


def gesture(**changes):
  values = {'worker_session': SESSION, 'sequence': 7, 'boot_ns': 5_000_000_000, 'frame_index': 3, 'name': 'right_wheel_up',
            'fast': False, 'document': DOCUMENT, 'vehicle': VEHICLE, 'drive': DRIVE}
  values.update(changes)
  return GestureSource(**values)


def event(source=None, **changes):
  source = source or gesture()
  value = {'version': 2, 'sequence': source.sequence, 'name': source.name, 'fast': source.fast, 'stamp': 5., 'drive': source.drive,
           'document': source.document, 'vehicle': source.vehicle, 'provenance': source.to_wire()}
  value.update(changes)
  return value


# The gesture's identity.

@pytest.mark.parametrize('name,fast,step', [('right_wheel_up', False, 1), ('right_wheel_up', True, 5),
                                           ('right_wheel_down', False, -1), ('right_wheel_down', True, -5)])
def test_a_scroll_names_the_comma_step_it_produces(name, fast, step):
  assert gesture(name=name, fast=fast).step == step
  assert step in cruise_action.TESLA_STEPS


def test_the_wire_form_round_trips():
  source = gesture()
  wire = source.to_wire()
  assert wire == {'version': 1, 'workerSession': SESSION, 'sequence': 7, 'sourceCanBootTime': 5_000_000_000,
                  'sourceCanFrameIndex': 3, 'name': 'right_wheel_up', 'fast': False, 'assignmentsHash': DOCUMENT,
                  'vehicleHash': VEHICLE, 'driveStartMonoTime': DRIVE}
  assert parse_source(wire) == source
  assert not hasattr(source, 'matches'), 'no saved step sizes to compare with any more'


@pytest.mark.parametrize('change', [
  {'version': 2}, {'version': True}, {'workerSession': 'x' * 32}, {'workerSession': SESSION[:31]}, {'workerSession': None},
  {'sequence': 0}, {'sequence': 2**64}, {'sequence': 1.0}, {'sourceCanBootTime': 0}, {'sourceCanBootTime': -1},
  {'sourceCanFrameIndex': -1}, {'sourceCanFrameIndex': 4096}, {'sourceCanFrameIndex': True},
  {'name': 'right_wheel_left'}, {'name': 'left_wheel_up'}, {'name': 3}, {'fast': 1}, {'fast': None},
  {'assignmentsHash': 'A' * 64}, {'assignmentsHash': 'a' * 63}, {'vehicleHash': ''}, {'driveStartMonoTime': 0},
  {'extra': 1},
])
def test_a_malformed_wire_form_is_no_gesture(change):
  wire = gesture().to_wire()
  wire.update(change)
  assert parse_source(wire) is None


@pytest.mark.parametrize('value', [None, [], 'gesture', 5, {}, {'version': 1}])
def test_anything_else_is_no_gesture(value):
  assert parse_source(value) is None


def test_an_event_carries_its_gesture_only_when_both_say_the_same():
  assert source_from_event(event()) == gesture()
  for change in ({'sequence': 8}, {'name': 'right_wheel_down'}, {'fast': True}, {'document': 'c' * 64}, {'vehicle': 'c' * 64},
                 {'drive': DRIVE + 1}, {'provenance': None}, {'provenance': {}}):
    assert source_from_event(event(**change)) is None, change
  plain = event()
  del plain['provenance']
  assert source_from_event(plain) is None


def test_the_gesture_is_only_current_inside_its_scope():
  assert current_source() is None
  with source_scope(event()):
    assert current_source() == gesture()
    with source_scope(event(name='right_wheel_down')):
      assert current_source() is None, 'an event that does not match its own gesture carries none'
    assert current_source() == gesture()
  assert current_source() is None
  with pytest.raises(RuntimeError):
    with source_scope(event()):
      raise RuntimeError('the action failed')
  assert current_source() is None


# The worker's message.

def test_the_protocol_carries_the_gesture_as_version_two():
  source = gesture()
  packet = encode_event(7, 'right_wheel_up', False, 5., DRIVE, DOCUMENT, VEHICLE, provenance=source.to_wire())
  decoded = decode_event(packet, 5.1)
  assert decoded['version'] == 2 and source_from_event(decoded) == source
  plain = decode_event(encode_event(7, 'right_wheel_up', False, 5., DRIVE, DOCUMENT, VEHICLE), 5.1)
  assert plain['version'] == 1 and 'provenance' not in plain and source_from_event(plain) is None


@pytest.mark.parametrize('change', [{'sequence': 8}, {'name': 'right_wheel_down'}, {'fast': True}, {'document': 'c' * 64},
                                    {'vehicle': 'c' * 64}, {'drive': DRIVE + 1}])
def test_the_worker_cannot_encode_a_gesture_that_is_not_the_events_own(change):
  arguments = {'sequence': 7, 'name': 'right_wheel_up', 'fast': False, 'drive': DRIVE, 'document': DOCUMENT, 'vehicle': VEHICLE}
  arguments.update(change)
  with pytest.raises(ValueError):
    encode_event(arguments['sequence'], arguments['name'], arguments['fast'], 5., arguments['drive'], arguments['document'],
                 arguments['vehicle'], provenance=gesture().to_wire())


@pytest.mark.parametrize('change', [{'version': 3}, {'version': 1}, {'provenance': None}, {'provenance': {}}, {'sequence': 8},
                                    {'name': 'right_wheel_down'}])
def test_the_screen_refuses_a_message_whose_gesture_does_not_match(change):
  value = event(**change)
  assert decode_event(json.dumps(value).encode(), 5.1) is None
  missing = event()
  del missing['provenance']
  assert decode_event(json.dumps(missing).encode(), 5.1) is None, 'version two without its gesture'


@pytest.fixture
def rig(tmp_path):
  """The worker and the screen's receiver for a Model 3 with the cable and the switch on."""
  return build(tmp_path, tesla(selected=True), comma=True)


def scroll(s, ticks=1):
  """One scroll: neutral, the scroll, neutral. Returns the worker's packets, undelivered."""
  s.feed(wheel(), dispatch=False)
  s.feed(wheel(right_scroll=ticks), dispatch=False)
  s.feed(wheel(), dispatch=False)
  packets = [json.loads(packet) for packet in s.channel.queue]
  return packets


def test_the_worker_stamps_a_scroll_with_where_it_saw_it_on_the_bus(rig):
  s = rig
  assert s.pump.sync_provenance, 'speed sync is admitted on this car'
  packet, = scroll(s, 3)
  assert packet['version'] == 2 and packet['name'] == 'right_wheel_up' and packet['fast'] is True
  source = source_from_event(packet)
  assert source.boot_ns == round(s.clock[0] * 1e9), 'the CAN batch of the release frame, on the boot clock, to the nanosecond'
  assert source.frame_index == 0 and source.worker_session == s.pump.worker_session and source.sequence == packet['sequence'] == 1
  assert source.document == document_hash((s.params.path / 'CarControlAssignments').read_bytes())
  assert source.vehicle == cp_fingerprint(s.cp) and source.drive == 100_000_000 and source.step == 5
  s.channel.queue.clear()
  later, = scroll(s, -1)
  assert source_from_event(later).sequence == 2 and source_from_event(later).step == -1


def test_the_frame_index_is_the_position_inside_the_can_batch(rig):
  from openpilot.cereal import messaging
  s = rig
  s.feed(wheel(), dispatch=False)
  s.feed(wheel(right_scroll=1), dispatch=False)
  s.clock[0] += .1
  s.sm.refresh(s.clock[0])
  batch = messaging.new_message('can', 4, valid=True, logMonoTime=round(s.clock[0] * 1e9))
  batch.can[0] = {'address': 0x286, 'dat': bytes(8), 'src': 0}
  batch.can[1] = {'address': 0x3c2, 'dat': bytes(8), 'src': 1}       # the wheel message's other half
  batch.can[2] = {'address': 0x118, 'dat': bytes(8), 'src': 0}
  batch.can[3] = {'address': 0x3c2, 'dat': wheel(), 'src': 1}         # the release
  s.frames.append(batch)
  s.pump.poll(s.sm)
  packet, = [json.loads(raw) for raw in s.channel.queue]
  assert source_from_event(packet).frame_index == 3


def test_only_scrolls_carry_a_gesture(rig):
  s = rig
  s.feed(wheel(), dispatch=False)
  s.feed(wheel(right_wheel_left=2), dispatch=False)
  s.feed(wheel(), dispatch=False)
  packet, = [json.loads(raw) for raw in s.channel.queue]
  assert packet['name'] == 'right_wheel_left' and packet['version'] == 1 and 'provenance' not in packet


def test_the_worker_sessions_differ_and_a_changed_car_resets_the_worker(rig, tmp_path):
  s = rig
  (tmp_path / 'other').mkdir()
  other = build(tmp_path / 'other', tesla(selected=True), comma=True)
  assert s.pump.worker_session != other.pump.worker_session and len(s.pump.worker_session) == 32
  s.feed(wheel(), dispatch=False)
  assert s.pump.decoder is not None
  unselected = s.cp.as_reader().as_builder()
  unselected.safetyConfigs[0].safetyParam = 1
  s.pump.set_vehicle(unselected.as_reader())
  assert s.pump.decoder is None and not s.pump.sync_provenance, 'a car without the scroll frame reports no gestures'
  s.pump.set_vehicle(None)
  assert not s.pump.sync_provenance and s.pump.cp_hash is None


def test_can_stamped_ahead_of_the_boot_clock_or_too_old_is_not_decoded(rig):
  from openpilot.cereal import messaging
  s = rig
  s.feed(wheel(), dispatch=False)
  s.feed(wheel(right_scroll=1), dispatch=False)
  for offset in (.01, -.3):
    s.clock[0] += .1
    s.sm.refresh(s.clock[0])
    batch = messaging.new_message('can', 1, valid=True, logMonoTime=round((s.clock[0] + offset) * 1e9))
    batch.can[0] = {'address': 0x3c2, 'dat': wheel(), 'src': 1}
    s.frames.append(batch)
    s.pump.poll(s.sm)
    assert not s.channel.queue, offset
  s.feed(wheel(), dispatch=False)
  assert not s.channel.queue, 'the decoder was reset: the held scroll is gone'


# The screen's request and card's receipt.

class Boot:
  """The boot clock card and the screen read, set by the test."""
  CLOCK_BOOTTIME = time.CLOCK_BOOTTIME

  def __init__(self, now):
    self.now = now

  def clock_gettime_ns(self, clock):
    assert clock == time.CLOCK_BOOTTIME
    return self.now


class Card:
  """Card's side for one selected Model 3 with the switch on: the cruise helper and the consumer of comma steps."""
  def __init__(self, cp, sm):
    self.cp, self.sm = cp, sm
    self.helper = VCruiseHelper(cp)
    self.helper.tesla_set_speed = True
    self.helper.v_cruise_kph = self.helper.v_cruise_cluster_kph = round(77 * MPH, 1)
    self.consumer = CruiseActionConsumer()

  def apply(self, message, now_ns):
    cs = car.CarState.new_message(canValid=True, cruiseState={'available': True, 'enabled': True})
    return self.consumer.apply(message, self.cp, cs, self.sm, self.helper, now_ns=now_ns, is_metric=False, enabled=True)

  @property
  def shown(self):
    return round(self.helper.v_cruise_kph / MPH)


class Drive:
  """Everything between the wheel and speed sync, on one clock: the worker, the screen, card and the car controller."""
  def __init__(self, tmp_path):
    self.s = build(tmp_path, tesla(selected=True), comma=True)
    self.s.sm['carControl'] = NS(enabled=True, longActive=True)
    self.s.sm['selfdriveState'] = NS(enabled=True)
    self.boot = Boot(self.now_ns)
    self.sender, self.publisher = CruiseActionPublisher(), Publisher()
    self.card = Card(self.s.cp, self.s.sm)
    self.sync = SpeedSynchronizer()
    self.controller = NS(speed_sync=self.sync, speed_sync_authority=None)
    self.host = NS(CI=NS(CC=self.controller), CP=self.s.cp, params=self.s.params, is_metric=False, sm=self.s.sm,
                   v_cruise_helper=self.card.helper, controller_cruise_consumer=self.card.consumer)
    self.native, self.counter, self.sent, self.steps, self.attach = 77, 0, [], [], True
    for key, sign in ((SPEED_UP, 1), (SPEED_DOWN, -1)):
      self.s.actions[key] = NS(available=True, invoke=None, invoke_value=lambda fast, sign=sign: self.step(sign * (5 if fast else 1)))

  @property
  def now_ns(self):
    return round(self.s.clock[0] * 1e9)

  def step(self, units):
    """The screen's comma-speed action: ask card for the step, then card applies it."""
    self.steps.append(units)
    if not self.attach:
      with source_scope({}):  # as if the worker had reported no gesture
        sent = self.sender.dispatch(units > 0, self.s.sm, self.s.cp, self.publisher, now_ns=self.now_ns, step_units=units)
    else:
      sent = self.sender.dispatch(units > 0, self.s.sm, self.s.cp, self.publisher, now_ns=self.now_ns, step_units=units)
    return sent and self.card.apply(self.publisher.message, self.now_ns)

  def frame(self, data=None, *, publish=True):
    """One 100 ms frame: the wheel frame reaches the worker and the car interface, then card and the controller run."""
    data = wheel() if data is None else data
    with patch.object(cruise_action, 'time', self.boot):
      self.boot.now = self.now_ns + 100_000_000
      self.s.feed(data)
      now = self.now_ns
      self.counter = (self.counter + 1) % 16
      bits = (self.counter << 8) | (2 << 12) | (round(self.native * 2) << 15)
      di = bytearray(bits.to_bytes(8, 'little'))
      di[0] = (0x88 + sum(di[1:])) & 255
      self.sync.observe([(now, [(0x3C2, data, 1)]), (now, [(0x286, bytes(di), 0)])])
      out = car.CarState.new_message(canValid=True, gearShifter='drive', cruiseState={'enabled': True, 'available': True},
                                     vCruise=self.card.helper.v_cruise_kph)
      if publish:
        publish_context(self.host, out, now_boot_ns=now)
      else:
        self.controller.speed_sync_authority = None
      cc = car.CarControl.new_message(enabled=True, longActive=True).as_reader()
      messages = sync_controller(self.controller, cc, NS(out=out, autopark=False), now)
    self.sent += [(now, message) for message in messages]
    return messages

  def frames(self, count, **kwargs):
    return [message for _ in range(count) for message in self.frame(**kwargs)]

  def scroll(self, ticks=1):
    self.frame(wheel(right_scroll=ticks))
    return self.frame()


@pytest.fixture
def drive(tmp_path):
  d = Drive(tmp_path)
  assert d.frames(12) == [] and d.sync.status == 'matched', 'settled: the Tesla and the comma both at 77 mph'
  return d


def test_the_screen_attaches_the_gesture_to_the_step_it_asks_for(drive):
  drive.card.apply = lambda message, now_ns: True   # look at the request only
  drive.frame(wheel(right_scroll=1))
  drive.frame()
  request = drive.publisher.message.slcAction.controllerCruise
  assert drive.steps == [1] and request.stepUnits == 1
  source = parse_source(request.sourceTeslaGesture.to_dict())
  assert source is not None and source.step == 1 and source.boot_ns == drive.now_ns and source.frame_index == 0
  assert source.vehicle == cp_fingerprint(drive.s.cp) and source.drive == 100_000_000


@pytest.mark.parametrize('case', ['no gesture', 'another step', 'another drive', 'too old', 'from the future'])
def test_the_screen_attaches_nothing_that_is_not_this_steps_fresh_gesture(drive, case):
  d = drive
  source = gesture(boot_ns=d.now_ns, vehicle=cp_fingerprint(d.s.cp), drive=100_000_000)
  if case == 'another step':
    source = replace(source, fast=True)
  if case == 'another drive':
    source = replace(source, drive=100_000_001)
  d.boot.now = d.now_ns + {'too old': 500_000_001, 'from the future': -1}.get(case, 0)
  with patch.object(cruise_action, 'time', d.boot):
    if case == 'no gesture':
      assert d.sender.dispatch(True, d.s.sm, d.s.cp, d.publisher, now_ns=d.now_ns, step_units=1)
    else:
      with source_scope(event(source)):
        assert d.sender.dispatch(True, d.s.sm, d.s.cp, d.publisher, now_ns=d.now_ns, step_units=1)
    request = d.publisher.message.slcAction.controllerCruise
    assert request.stepUnits == 1 and parse_source(request.sourceTeslaGesture.to_dict()) is None
    assert d.card.apply(d.publisher.message, d.now_ns) and d.card.shown == 78, 'the step itself is applied all the same'
  assert d.card.consumer.tesla_receipts == []


def test_the_gesture_never_rides_on_an_ordinary_speed_action(drive):
  d = drive
  with patch.object(cruise_action, 'time', d.boot), source_scope(event(gesture(boot_ns=d.now_ns, drive=100_000_000))):
    assert d.sender.dispatch(True, d.s.sm, d.s.cp, d.publisher, now_ns=d.now_ns)
    assert d.publisher.message.slcAction.kind == 'cruiseIncrease'
    assert parse_source(d.publisher.message.slcAction.controllerCruise.sourceTeslaGesture.to_dict()) is None
    assert d.card.apply(d.publisher.message, d.now_ns) and d.card.consumer.tesla_receipts == []


def test_card_records_a_receipt_for_the_step_it_applied(drive):
  d = drive
  d.frame(wheel(right_scroll=1))
  d.frame(publish=False)   # the controller has not run yet: the receipt is still with card
  assert d.card.shown == 78
  receipt, = d.card.consumer.tesla_receipts
  assert isinstance(receipt, ManualReceipt)
  assert (receipt.boot_ns, receipt.frame_index, receipt.name, receipt.fast) == (d.now_ns, 0, 'right_wheel_up', False)
  assert receipt.worker_session == d.s.pump.worker_session and receipt.sequence == 1 and receipt.drive_start_ns == 100_000_000
  assert receipt.vehicle_hash == cp_fingerprint(d.s.cp) and len(receipt.document_hash) == 64
  assert receipt.processed_boot_ns == d.boot.now and receipt.target_kph == d.card.helper.v_cruise_kph


@pytest.mark.parametrize('case', ['another car', 'another drive', 'another step', 'too old', 'from the future'])
def test_card_records_no_receipt_for_metadata_that_does_not_fit_and_still_applies_the_step(drive, case):
  d = drive
  source = gesture(boot_ns=d.now_ns, vehicle=cp_fingerprint(d.s.cp), drive=100_000_000)
  if case == 'another car':
    source = replace(source, vehicle='c' * 64)
  with patch.object(cruise_action, 'time', d.boot):
    d.boot.now = d.now_ns
    with source_scope(event(source)):
      assert d.sender.dispatch(True, d.s.sm, d.s.cp, d.publisher, now_ns=d.now_ns, step_units=1)
    message = d.publisher.message.as_builder()
    if case == 'another drive':
      message.slcAction.controllerCruise.sourceTeslaGesture.driveStartMonoTime = 100_000_001
    if case == 'another step':
      message.slcAction.controllerCruise.sourceTeslaGesture.fast = True
    d.boot.now = d.now_ns + {'too old': TESLA_RECEIPT_NS + 1, 'from the future': -1}.get(case, 0)
    assert d.card.apply(message.as_reader(), d.now_ns) and d.card.shown == 78
  assert d.card.consumer.tesla_receipts == []


def test_a_refused_step_leaves_no_receipt(drive):
  d = drive
  d.card.helper.tesla_set_speed = False   # the switch went off
  d.frame(wheel(right_scroll=1))
  d.frame(publish=False)
  assert d.steps == [1] and d.card.shown == 77 and d.card.consumer.tesla_receipts == []


def manual(now, index=0, **changes):
  values = {'boot_ns': now, 'frame_index': index, 'name': 'right_wheel_up', 'fast': False, 'worker_session': SESSION, 'sequence': index + 1,
            'document_hash': DOCUMENT, 'vehicle_hash': VEHICLE, 'drive_start_ns': DRIVE, 'processed_boot_ns': now, 'target_kph': 125.}
  values.update(changes)
  return ManualReceipt(**values)


def test_receipts_are_offered_for_two_seconds_then_dropped():
  consumer = CruiseActionConsumer()
  assert TESLA_RECEIPT_NS == MANUAL_COMPLETION_NS == 2_000_000_000, "the window in which speed sync can still match one"
  first, second = manual(1_000_000_000), manual(2_000_000_000, 1)
  consumer.tesla_receipts += [first, second]
  assert consumer.peek_tesla_receipts(2_500_000_000) == (first, second)
  assert consumer.peek_tesla_receipts(3_000_000_000) == (first, second), 'exactly two seconds old is still offered'
  assert consumer.peek_tesla_receipts(3_000_000_001) == (second,) and consumer.tesla_receipts == [second]
  assert consumer.peek_tesla_receipts(1_999_999_999) == (), 'a receipt from the future is dropped too'
  assert consumer.tesla_receipts == []


def test_only_the_receipts_speed_sync_took_are_discarded():
  consumer = CruiseActionConsumer()
  receipts = [manual(1_000_000_000, index) for index in range(3)]
  consumer.tesla_receipts += receipts
  consumer.acknowledge_tesla_receipts((receipts[1], manual(9_000_000_000, 9)))
  assert consumer.tesla_receipts == [receipts[0], receipts[2]]
  for bad in ([receipts[0]], None, tuple(manual(1_000_000_000, index) for index in range(17))):
    consumer.acknowledge_tesla_receipts(bad)
    assert consumer.tesla_receipts == [receipts[0], receipts[2]], 'only a tuple of at most sixteen, as speed sync hands back'
  consumer.acknowledge_tesla_receipts(())
  assert len(consumer.tesla_receipts) == 2


def test_a_full_list_drops_the_newest_receipt_and_the_step_is_still_applied(drive):
  d = drive
  d.card.consumer.drive = 100_000_000   # this drive's receipts, not yet taken by speed sync
  d.card.consumer.tesla_receipts += [manual(d.now_ns, index, vehicle_hash=cp_fingerprint(d.s.cp)) for index in range(16)]
  before = list(d.card.consumer.tesla_receipts)
  d.frame(wheel(right_scroll=1))
  d.frame(publish=False)
  assert d.card.shown == 78, 'the step is applied'
  assert d.card.consumer.tesla_receipts == before, 'no room: the new receipt is the one left out'
  assert not hasattr(d.card.consumer, 'tesla_receipt_overflow')


def test_a_new_drive_forgets_the_receipts(drive):
  d = drive
  d.frame(wheel(right_scroll=1))
  d.frame(publish=False)
  assert d.card.consumer.tesla_receipts
  d.s.sm['deviceState'].startedMonoTime += 1
  assert not d.card.apply(d.publisher.message, d.now_ns)
  assert d.card.consumer.tesla_receipts == []


# End to end.

def test_with_the_receipt_speed_sync_reads_the_comma_speed_again_at_once(drive):
  d = drive
  d.frame(wheel(right_scroll=1))
  d.native = 78   # the Tesla took the driver's scroll
  d.frame()
  assert d.steps == [1] and d.card.shown == 78, 'the comma took it too'
  assert d.sync.manual_queue == [] and d.sync.blocked is None, 'matched to the scroll speed sync saw on the bus, the same frame'
  assert d.controller.speed_sync_authority.manual_receipts == tuple(d.card.consumer.tesla_receipts) != ()
  d.frame()
  assert d.card.consumer.tesla_receipts == [], 'handed back and dropped the frame after'
  assert d.sync.status == 'matched'
  assert d.frames(25) == [] and 'manual_action_unconfirmed' not in d.sync.noticed
  assert not d.sync.manual_receipts and not d.sync.manual_receipt_acks


def test_with_the_receipt_a_tesla_that_moved_further_is_corrected_within_a_second(drive):
  d = drive
  d.frame(wheel(right_scroll=1))
  d.native = 80   # the Tesla jumped to its next multiple of five; the comma moved one
  d.frame()
  assert d.card.shown == 78 and d.sync.manual_queue == []
  sent = d.frames(9)
  assert sent and sent[0][1][3] & 63 == 63, 'one click down, without waiting out the two-second window'
  assert d.sent[0][0] - d.sync.driver_ns < 1_000_000_000


def test_without_the_gesture_speed_sync_waits_out_its_window(drive):
  d = drive
  d.attach = False
  d.frame(wheel(right_scroll=1))
  d.native = 80
  d.frame()
  assert d.card.shown == 78 and d.card.consumer.tesla_receipts == []
  assert len(d.sync.manual_queue) == 1
  assert d.frames(19) == [] and d.sync.status == 'waiting_for_manual_processing'
  sent = d.frames(8)
  assert 'manual_action_unconfirmed' in d.sync.noticed and sent and sent[0][1][3] & 63 == 63, 'corrected after the window'


def test_a_fast_scroll_down_is_matched_too(drive):
  d = drive
  d.frame(wheel(right_scroll=-3))
  d.native = 75
  d.frame()
  assert d.steps == [-5] and d.card.shown == 75 and d.sync.manual_queue == []
  assert d.frames(25) == [] and d.sync.status == 'matched' and 'manual_action_unconfirmed' not in d.sync.noticed


def test_a_receipt_is_still_matched_at_the_end_of_its_two_seconds(drive):
  d = drive
  d.frame(wheel(right_scroll=1))
  d.native = 78
  d.frame(publish=False)   # card's admission does not reach the controller
  assert len(d.card.consumer.tesla_receipts) == 1
  for _ in range(19):
    d.frame(publish=False)
  assert len(d.card.consumer.tesla_receipts) == 1, 'kept while nobody asks'
  d.frame()   # 2.0 s after the scroll: still offered, and still inside speed sync's window
  assert d.sync.manual_queue == [] and 'manual_action_unconfirmed' not in d.sync.noticed


def test_a_receipt_speed_sync_never_takes_expires_with_card(drive):
  d = drive
  d.frame(wheel(right_scroll=1))
  d.native = 78
  for _ in range(22):
    d.frame(publish=False)
  assert len(d.card.consumer.tesla_receipts) == 1
  d.frame()   # 2.2 s after: card drops it instead of offering it
  assert d.card.consumer.tesla_receipts == [] and d.controller.speed_sync_authority.manual_receipts == ()
  assert 'manual_action_unconfirmed' in d.sync.noticed and d.sync.blocked is None, 'the scroll was dropped after its window'
  assert d.frames(10) == [] and d.sync.status == 'matched'


def test_seventeen_scrolls_that_are_never_taken_keep_sixteen_receipts(drive):
  d = drive
  for count in range(1, 18):
    d.frame(wheel(right_scroll=-1), publish=False)
    d.native -= 1
    d.frame(publish=False)
    assert len(d.card.consumer.tesla_receipts) == min(count, 16)
  assert d.card.shown == 77 - 17, 'every step was applied'
  assert [receipt.sequence for receipt in d.card.consumer.tesla_receipts] == list(range(1, 17)), 'the seventeenth was left out'
