"""The driver's own scroll and speed sync. Wheel input that cannot be read as one clean scroll pauses sync; it never
stops it until disengaging.

A right-wheel nudge that springs back with one scroll click is taken by the Tesla and not by the comma, so sync
scrolls the Tesla back. A clean scroll waits for the comma: card's receipt for the step it applied releases it at
once, and without one it is dropped after its window and corrected the same way.
"""
from dataclasses import replace

import pytest

from opendbc.car.tesla.speed_sync import MANUAL_COMPLETION_NS, MANUAL_SETTLE_NS, ManualReceipt, SpeedSynchronizer, SyncAuthority

MPH = 1.609344
DOCUMENT, VEHICLE, SESSION, DRIVE = 'd' * 64, 'e' * 64, '0123456789abcdef' * 2, 100_000_000

NEUTRAL = bytes.fromhex('2955000000000040')
TICK = 50_000_000


def wheel(right_left=1, right_scroll=0):
  bits = int.from_bytes(NEUTRAL, 'little')
  bits = (bits & ~(3 << 8)) | (right_left << 8)
  bits = (bits & ~(63 << 24)) | ((right_scroll & 63) << 24)
  return bits.to_bytes(8, 'little')


class Rig:
  """The Tesla in mph at 77, the comma's set speed 77; nothing answers requests unless a test moves `speed`."""
  def __init__(self):
    self.sync = SpeedSynchronizer()
    self.now = 1_000_000_000
    self.counter = 0
    self.speed = self.target = 77
    self.sent = []

  def tick(self, data=NEUTRAL):
    self.now += TICK
    self.counter = (self.counter + 1) % 16
    bits = (self.counter << 8) | (2 << 12) | (round(self.speed * 2) << 15)
    di = bytearray(bits.to_bytes(8, 'little'))
    di[0] = (0x88 + sum(di[1:])) & 255
    self.sync.observe([(self.now, [(0x286, bytes(di), 0), (0x3c2, data, 1)])])
    msg = self.sync.update(self.now, target_kph=self.target * 1.609344, enabled=True, safe=True, longitudinal_active=True)
    if msg:
      self.sent.append((self.now, msg))
    return msg

  def settle(self, seconds):
    return [m for m in (self.tick() for _ in range(round(seconds * 1e9 / TICK))) if m]


def clicks(msg):
  ticks = msg[1][3] & 63
  return ticks - 64 if ticks >= 32 else ticks


def test_nudge_release_click_is_corrected_not_a_stop():
  r = Rig()
  assert not r.settle(1)
  r.tick(wheel(right_left=2))
  r.tick(wheel(right_left=2))
  r.tick(wheel(right_scroll=1))   # springs back with one click
  r.speed = 78                    # the Tesla took it; the comma stays at 77
  sent = r.settle(1.5)
  assert r.sync.blocked is None
  assert 'ambiguous_manual_input' not in r.sync.noticed
  assert sent and clicks(sent[0]) == -1, 'sync puts the Tesla back on the comma speed'
  assert any(h['event'] == 'nudge_release_click' for h in r.sync.history)


def test_unclear_scroll_pauses_and_sync_carries_on():
  r = Rig()
  r.settle(1)
  r.tick(wheel(right_left=2))
  r.tick(wheel(right_left=2, right_scroll=1))   # a scroll while the wheel is still tilted
  assert r.sync.blocked is None and 'ambiguous_manual_input' in r.sync.noticed
  r.tick()
  r.speed = 78
  sent = r.settle(1.5)
  assert r.sync.blocked is None and sent and clicks(sent[0]) == -1


def test_a_driver_scroll_waits_its_window_and_is_then_dropped_with_a_notice():
  r = Rig()
  r.settle(1)
  r.tick(wheel(right_scroll=1))   # a clean scroll; the comma's own speed does not follow it here
  r.tick()
  r.speed = 78
  assert not r.settle(MANUAL_COMPLETION_NS / 1e9 - 0.2), "nothing is sent while the comma may still act on the driver's scroll"
  assert r.sync.status == 'waiting_for_manual_processing'
  sent = r.settle(1)
  assert r.sync.blocked is None and 'manual_action_unconfirmed' in r.sync.noticed
  assert any(h['event'] == 'manual_action_unconfirmed' and h['dropped'] == 1 for h in r.sync.history)
  assert not r.sync.manual_queue
  assert sent and clicks(sent[0]) == -1, "then the Tesla is put back on the comma's speed"


def test_a_driver_scroll_the_comma_followed_needs_no_correction():
  r = Rig()
  r.settle(1)
  r.tick(wheel(right_scroll=1))
  r.tick()
  r.speed = r.target = 78         # the Tesla and the comma both took the scroll
  assert not r.settle(MANUAL_COMPLETION_NS / 1e9 + 1)
  assert r.sync.status == 'matched' and r.sync.blocked is None
  assert 'manual_action_unconfirmed' in r.sync.noticed, 'the wait always ends with the same notice'


def test_a_fast_driver_scroll_is_one_gesture_and_one_notice_per_engagement():
  r = Rig()
  r.settle(1)
  for _ in range(2):
    r.tick(wheel(right_scroll=3))
    r.tick(wheel(right_scroll=2))   # the same flick, still turning
    r.tick()
    assert len(r.sync.manual_queue) == 1 and r.sync.manual_queue[0][2:] == ('right_wheel_up', True)
    r.settle(MANUAL_COMPLETION_NS / 1e9 + 0.2)
    assert not r.sync.manual_queue
  assert len([h for h in r.sync.history if h['event'] == 'manual_action_unconfirmed']) == 2
  assert [n['kind'] for n in r.sync.notices].count('manual_action_unconfirmed') == 1


def test_nothing_is_sent_right_after_a_driver_scroll_that_was_never_queued():
  r = Rig()
  r.settle(1)
  r.tick(wheel(right_left=2, right_scroll=1))   # unclear input: paused, not queued
  r.tick()
  r.speed = 78
  assert not r.settle(MANUAL_SETTLE_NS / 1e9 - 0.15), "the Tesla gets time to apply the driver's own scroll first"
  assert r.sync.status == 'waiting_for_driver_scroll'
  assert r.settle(0.5)


def test_clock_faults_still_stop():
  r = Rig()
  r.settle(0.5)
  r.sync.observe([(r.now - TICK, [(0x3c2, NEUTRAL, 1)])])
  assert r.sync.blocked == 'wheel_clock_reversal'


# Card's receipts for the driver's own scrolls.

def scrolled(r, ticks=1):
  """The driver scrolls once; returns the engine's record of it: (stamp, frame index, name, fast)."""
  r.settle(1)
  r.tick(wheel(right_scroll=ticks))
  r.tick()
  entry, = r.sync.manual_queue
  return entry


def receipt_for(entry, **changes):
  """What card records when it applies the comma step for that scroll, in the frame the scroll was seen."""
  stamp, index, name, fast = entry
  values = dict(boot_ns=stamp, frame_index=index, name=name, fast=fast, worker_session=SESSION, sequence=1,
                document_hash=DOCUMENT, vehicle_hash=VEHICLE, drive_start_ns=DRIVE, processed_boot_ns=stamp,
                target_kph=78 * MPH)
  values.update(changes)
  return ManualReceipt(**values)


def authority(r, *receipts, **changes):
  values = dict(observed_boot_ns=r.now, target_kph=r.target * MPH, is_metric=False, configuration_id='a' * 64, software_owned=True,
                document_hash=DOCUMENT, vehicle_hash=VEHICLE, drive_start_ns=DRIVE, manual_receipts=tuple(receipts))
  values.update(changes)
  return SyncAuthority(**values)


def test_a_receipt_releases_the_drivers_scroll_at_once():
  r = Rig()
  entry = scrolled(r)
  assert entry[1:] == (1, 'right_wheel_up', False) and r.sync.status == 'waiting_for_manual_processing'
  receipt = receipt_for(entry)
  r.speed = r.target = 78   # the Tesla and the comma both took it
  r.sync.target = ('stale',)
  r.sync.accept_manual_receipts(authority(r, receipt), r.now)
  assert not r.sync.manual_queue and r.sync.target is None, 'the comma target is read again'
  assert r.sync.take_manual_receipt_acks() == (receipt,) and r.sync.take_manual_receipt_acks() == ()
  r.tick()
  assert r.sync.status == 'matched' and r.sync.blocked is None
  assert not r.settle(MANUAL_COMPLETION_NS / 1e9 + .5)
  assert 'manual_action_unconfirmed' not in r.sync.noticed


def test_with_a_receipt_a_difference_is_corrected_without_waiting_out_the_window():
  r = Rig()
  receipt = receipt_for(scrolled(r))
  r.speed, r.target = 79, 78   # the Tesla moved further than the comma did
  r.sync.accept_manual_receipts(authority(r, receipt), r.now)
  sent = r.settle(MANUAL_SETTLE_NS / 1e9 + .2)
  assert sent and clicks(sent[0]) == -1, "corrected as soon as the Tesla has applied the driver's own scroll"
  assert r.sent[0][0] - receipt.boot_ns < MANUAL_COMPLETION_NS // 2
  waiting = Rig()
  scrolled(waiting)
  waiting.speed, waiting.target = 79, 78
  assert not waiting.settle(MANUAL_SETTLE_NS / 1e9 + .2), 'without a receipt the scroll still waits'


def test_a_receipt_may_arrive_before_the_scroll_is_seen_on_the_bus():
  r = Rig()
  r.settle(1)
  r.tick(wheel(right_scroll=3))
  early = receipt_for((r.now + TICK, 1, 'right_wheel_up', True), processed_boot_ns=r.now + TICK)
  r.now += TICK   # card applied the step in the frame the release arrives in; speed sync sees that frame next
  r.sync.accept_manual_receipts(authority(r, early), r.now)
  assert r.sync.manual_receipts == {(early.boot_ns, 1): early} and r.sync.take_manual_receipt_acks() == (early,)
  r.now -= TICK
  r.tick()
  assert r.sync.manual_queue == [(early.boot_ns, 1, 'right_wheel_up', True)]
  r.sync.accept_manual_receipts(authority(r), r.now)
  assert not r.sync.manual_queue and not r.sync.manual_receipts and r.sync.blocked is None


@pytest.mark.parametrize('changes', [dict(name='right_wheel_down'), dict(fast=True)])
def test_a_receipt_for_a_different_scroll_at_the_same_frame_stops_sync(changes):
  r = Rig()
  receipt = receipt_for(scrolled(r), **changes)
  r.sync.accept_manual_receipts(authority(r, receipt), r.now)
  assert r.sync.blocked == 'manual_receipt_mismatch'
  assert not r.settle(3), 'stopped until disengaging'


@pytest.mark.parametrize('changes', [
  dict(document_hash='f' * 64), dict(vehicle_hash='f' * 64), dict(drive_start_ns=DRIVE + 1), dict(drive_start_ns=0),
  dict(worker_session='x' * 32), dict(worker_session='0' * 31), dict(sequence=0), dict(sequence=1.0),
  dict(name='right_wheel_left'), dict(name='left_wheel_up'), dict(fast=1),
  dict(frame_index=-1), dict(frame_index=4096), dict(frame_index=1.0),
  dict(target_kph=0), dict(target_kph=255), dict(target_kph=float('nan')), dict(target_kph='125'),
  dict(processed_boot_ns=0), dict(boot_ns=0), dict(boot_ns=1.5),
])
def test_a_receipt_that_fails_any_check_is_ignored_and_the_scroll_waits_out_its_window(changes):
  r = Rig()
  entry = scrolled(r)
  receipt = receipt_for(entry, **changes)
  r.sync.accept_manual_receipts(authority(r, receipt), r.now)
  assert r.sync.manual_queue == [entry] and r.sync.blocked is None
  assert r.sync.take_manual_receipt_acks() == (), 'never taken into custody'
  r.settle(MANUAL_COMPLETION_NS / 1e9 + .2)
  assert 'manual_action_unconfirmed' in r.sync.noticed and not r.sync.manual_queue


def test_a_receipt_processed_in_the_future_or_before_its_scroll_is_ignored():
  r = Rig()
  entry = scrolled(r)
  for changes in (dict(processed_boot_ns=r.now + 1), dict(processed_boot_ns=entry[0] - 1)):
    r.sync.accept_manual_receipts(authority(r, receipt_for(entry, **changes)), r.now)
    assert r.sync.manual_queue == [entry] and r.sync.take_manual_receipt_acks() == ()


@pytest.mark.parametrize('changes', [dict(document_hash=''), dict(vehicle_hash=''), dict(document_hash='d' * 63)])
def test_receipts_need_an_admission_that_names_the_assignments_and_the_car(changes):
  r = Rig()
  entry = scrolled(r)
  receipt = receipt_for(entry, **changes)
  r.sync.accept_manual_receipts(authority(r, receipt, **changes), r.now)
  assert r.sync.manual_queue == [entry] and r.sync.take_manual_receipt_acks() == ()


def test_an_expired_receipt_is_disposed_of_and_completes_nothing():
  r = Rig()
  entry = scrolled(r)
  receipt = receipt_for(entry)
  r.now = entry[0] + MANUAL_COMPLETION_NS + 1
  r.sync.accept_manual_receipts(authority(r, receipt), r.now)
  assert r.sync.manual_queue == [entry] and not r.sync.manual_receipts
  assert r.sync.take_manual_receipt_acks() == (receipt,), 'handed back so card drops it'
  r.now = entry[0] + MANUAL_COMPLETION_NS
  r.sync.accept_manual_receipts(authority(r, receipt), r.now)
  assert not r.sync.manual_queue, 'exactly at the end of the window still counts'


def test_a_receipt_for_a_scroll_already_dealt_with_is_handed_back():
  r = Rig()
  entry = scrolled(r)
  receipt = receipt_for(entry)
  r.sync.accept_manual_receipts(authority(r, receipt), r.now)
  r.sync.take_manual_receipt_acks()
  r.sync.accept_manual_receipts(authority(r, receipt), r.now)   # card had not yet seen the acknowledgment
  assert r.sync.take_manual_receipt_acks() == (receipt,) and not r.sync.manual_receipts and r.sync.blocked is None


def test_a_cached_receipt_expires_with_its_window():
  r = Rig()
  r.settle(1)
  early = receipt_for((r.now, 1, 'right_wheel_up', False), processed_boot_ns=r.now)
  r.sync.accept_manual_receipts(authority(r, early), r.now)
  assert r.sync.manual_receipts
  r.settle(MANUAL_COMPLETION_NS / 1e9 + .1)
  r.sync.accept_manual_receipts(authority(r), r.now)
  assert not r.sync.manual_receipts and r.sync.blocked is None


def test_more_receipts_than_card_ever_holds_stop_sync():
  r = Rig()
  entry = scrolled(r)
  many = tuple(receipt_for(entry, sequence=index + 1) for index in range(17))
  r.sync.accept_manual_receipts(authority(r, *many), r.now)
  assert r.sync.blocked == 'invalid_manual_receipts'
  r = Rig()
  scrolled(r)
  r.sync.accept_manual_receipts(replace(authority(r), manual_receipts=[]), r.now)
  assert r.sync.blocked == 'invalid_manual_receipts', 'only a tuple, as card builds it'


def test_sixteen_receipts_are_accepted():
  r = Rig()
  r.settle(1)
  receipts = tuple(receipt_for((r.now - index, index, 'right_wheel_up', False), processed_boot_ns=r.now, sequence=index + 1)
                   for index in range(16))
  r.sync.accept_manual_receipts(authority(r, *receipts), r.now)
  assert r.sync.blocked is None and len(r.sync.manual_receipts) == 16
  assert r.sync.take_manual_receipt_acks() == receipts


def test_two_different_receipts_for_one_scroll_stop_sync():
  r = Rig()
  r.settle(1)
  first = receipt_for((r.now, 1, 'right_wheel_up', False), processed_boot_ns=r.now)
  r.sync.accept_manual_receipts(authority(r, first), r.now)
  r.sync.accept_manual_receipts(authority(r, replace(first, sequence=2)), r.now)
  assert r.sync.blocked == 'conflicting_manual_receipt'


def test_anything_that_is_not_a_receipt_is_skipped():
  r = Rig()
  entry = scrolled(r)
  receipt = receipt_for(entry)
  r.sync.accept_manual_receipts(authority(r, None, 'receipt', (1, 2), receipt), r.now)
  assert not r.sync.manual_queue and r.sync.take_manual_receipt_acks() == (receipt,)


def test_disengaging_forgets_receipts_and_acknowledgments():
  r = Rig()
  r.settle(1)
  early = receipt_for((r.now, 1, 'right_wheel_up', False), processed_boot_ns=r.now)
  r.sync.accept_manual_receipts(authority(r, early), r.now)
  assert r.sync.manual_receipts and r.sync.manual_receipt_acks
  r.now += TICK
  r.sync.update(r.now, target_kph=77 * MPH, enabled=False, safe=True)
  assert not r.sync.manual_receipts and not r.sync.manual_receipt_acks and r.sync.take_manual_receipt_acks() == ()
