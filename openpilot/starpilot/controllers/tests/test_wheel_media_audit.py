from pathlib import Path
from unittest import mock

import pytest

from opendbc.car.hyundai.ioniq6_media import Ioniq6MediaButtons
from opendbc.car.hyundai.tests.test_ioniq6_media import packet
from openpilot.starpilot.conditional_mode.manual import MEDIA_LONG_NS
from openpilot.starpilot.controllers import wheel_actions
from openpilot.starpilot.controllers.tests.test_wheel_actions import DRIVE, NOW, context as context, state


def observe(owner, source, params, cp, stamp, *, mode=False, custom=False, media_packet=True):
  media = source.update([packet(stamp, mode=mode, custom=custom) if media_packet else (stamp, [])])
  return owner.observe(params, cp, state(), now_ns=stamp, drive_id=DRIVE, media=media)


def test_neutral_media_packets_do_not_scan_fourteen_params_each_time(context):
  params, cp = context
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  with mock.patch.object(wheel_actions, 'read_saved', wraps=wheel_actions.read_saved) as reads:
    for frame in range(200):
      commands = observe(owner, source, params, cp, NOW + frame * 10_000_000, media_packet=frame % 20 == 0)
      assert not any(action for _, action in commands)
  # Idle heartbeats and neutral media packets must not add blocking file reads.
  assert reads.call_count == len(wheel_actions.KEYS)


@pytest.mark.parametrize('custom,key', [(False, 'ModeButtonControl'), (True, 'StarButtonControl')])
def test_real_media_edges_refresh_mapping_and_emit_release_once(context, custom, key):
  params, cp = context
  params.put(key, 8, block=True)
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  observe(owner, source, params, cp, NOW)
  params.put(key, 11, block=True)
  with mock.patch.object(wheel_actions, 'capture', wraps=wheel_actions.capture) as capture:
    assert not any(action for _, action in observe(owner, source, params, cp, NOW + 100_000_000,
                                                   mode=not custom, custom=custom))
    assert capture.call_count == 1
    # A changed mapping cancels the current gesture and requires new neutral.
    assert not any(action for _, action in observe(owner, source, params, cp, NOW + 200_000_000))
    assert capture.call_count == 2
    assert not any(action for _, action in observe(owner, source, params, cp, NOW + 300_000_000))
    assert capture.call_count == 2
    observe(owner, source, params, cp, NOW + 400_000_000, mode=not custom, custom=custom)
    assert observe(owner, source, params, cp, NOW + 500_000_000) == ((key, 11),)
    assert capture.call_count == 4


def test_transient_mapping_change_during_hold_cancels_the_gesture(context):
  params, cp = context
  params.put('ModeButtonControl', 8, block=True)
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  observe(owner, source, params, cp, NOW)
  observe(owner, source, params, cp, NOW + 10_000_000, mode=True)
  params.put('ModeButtonControl', 11, block=True)
  observe(owner, source, params, cp, NOW + 20_000_000, mode=True)
  params.put('ModeButtonControl', 8, block=True)
  observe(owner, source, params, cp, NOW + 30_000_000, mode=True)
  assert not any(action for _, action in observe(owner, source, params, cp, NOW + 40_000_000))


def test_invalid_media_and_new_epoch_cannot_release_an_old_gesture(context):
  params, cp = context
  params.put('ModeButtonControl', 8, block=True)
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  observe(owner, source, params, cp, NOW)
  observe(owner, source, params, cp, NOW + 100_000_000, mode=True)
  invalid = source.update([])
  assert not invalid.valid
  assert not any(action for _, action in owner.observe(params, cp, state(), now_ns=NOW + 110_000_000,
                                                        drive_id=DRIVE, media=invalid))
  # A held-at-restart input cannot finish the pre-fault gesture.
  assert not any(action for _, action in observe(owner, source, params, cp, NOW + 200_000_000, mode=True))
  assert not any(action for _, action in observe(owner, source, params, cp, NOW + 300_000_000))
  observe(owner, source, params, cp, NOW + 400_000_000, mode=True)
  assert observe(owner, source, params, cp, NOW + 500_000_000) == (('ModeButtonControl', 8),)



def test_press_release_in_one_media_batch_is_not_lost(context):
  params, cp = context
  params.put('ModeButtonControl', 8, block=True)
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  observe(owner, source, params, cp, NOW)
  batch = source.update([packet(NOW + 100_000_000, mode=True), packet(NOW + 200_000_000)])
  assert owner.observe(params, cp, state(), now_ns=NOW + 200_000_000, drive_id=DRIVE, media=batch) == (('ModeButtonControl', 8),)


@pytest.mark.parametrize('long_press', [False, True])
def test_invalid_saved_mapping_cannot_emit_media_command(context, long_press):
  params, cp = context
  key = 'LongModeButtonControl' if long_press else 'ModeButtonControl'
  params.put(key, 8, block=True)
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  observe(owner, source, params, cp, NOW)
  pressed = NOW + 10_000_000
  observe(owner, source, params, cp, pressed, mode=True)
  Path(params.get_param_path(key)).write_bytes(b'invalid')
  stop = MEDIA_LONG_NS if long_press else 100_000_000
  for elapsed in range(10_000_000, stop + 1, 10_000_000):
    commands = observe(owner, source, params, cp, pressed + elapsed, mode=long_press or elapsed < stop)
    assert not any(action for _, action in commands)


def test_off_to_on_mapping_during_hold_cannot_authorize_that_gesture(context):
  params, cp = context
  params.put('LongModeButtonControl', 0, block=True)
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  observe(owner, source, params, cp, NOW)
  pressed = NOW + 10_000_000
  observe(owner, source, params, cp, pressed, mode=True)
  params.put('LongModeButtonControl', 8, block=True)
  for elapsed in range(10_000_000, MEDIA_LONG_NS + 20_000_000, 10_000_000):
    assert not any(action for _, action in observe(owner, source, params, cp, pressed + elapsed, mode=True))
  assert not any(action for _, action in observe(owner, source, params, cp, pressed + MEDIA_LONG_NS + 20_000_000))


def test_idle_mapping_change_is_checked_before_a_new_media_gesture(context):
  params, cp = context
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  observe(owner, source, params, cp, NOW)
  params.put('ModeButtonControl', 8, block=True)
  for frame in range(1, 101):
    assert not any(action for _, action in observe(owner, source, params, cp, NOW + frame * 10_000_000,
                                                  media_packet=frame % 20 == 0))
  assert owner.mapping is not None
  assert owner.mapping['ModeButtonControl'] == 0
  assert owner.checked_ns == NOW
  # A changed assignment cannot authorize the gesture that first observes it.
  assert not any(action for _, action in observe(owner, source, params, cp, NOW + 1_100_000_000, mode=True))
  assert owner.mapping['ModeButtonControl'] == 8
  assert owner.checked_ns == NOW + 1_100_000_000
  assert not any(action for _, action in observe(owner, source, params, cp, NOW + 1_200_000_000))
  observe(owner, source, params, cp, NOW + 1_300_000_000, mode=True)
  assert observe(owner, source, params, cp, NOW + 1_400_000_000) == (('ModeButtonControl', 8),)


def test_new_drive_while_held_cannot_release_previous_gesture(context):
  params, cp = context
  params.put('ModeButtonControl', 8, block=True)
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  observe(owner, source, params, cp, NOW)
  observe(owner, source, params, cp, NOW + 100_000_000, mode=True)
  for offset, held in ((200_000_000, True), (300_000_000, False)):
    media = source.update([packet(NOW + offset, mode=held)])
    assert not any(action for _, action in owner.observe(params, cp, state(), now_ns=NOW + offset,
                                                          drive_id=DRIVE + 1, media=media))


def test_no_media_still_audits_on_car_button_edges(context):
  params, cp = context
  owner = wheel_actions.WheelPublisher(params)
  owner.observe(params, cp, state(), now_ns=NOW, drive_id=DRIVE)
  with mock.patch.object(wheel_actions, 'capture', wraps=wheel_actions.capture) as capture:
    owner.observe(params, cp, state(True), now_ns=NOW + 10_000_000, drive_id=DRIVE)
    assert owner.observe(params, cp, state(False), now_ns=NOW + 20_000_000, drive_id=DRIVE) == (('DistanceButtonControl', 1),)
    assert capture.call_count == 2


@pytest.mark.parametrize('key,button', [('DistanceButtonControl', 'gapAdjustCruise'),
                                       ('CancelButtonControl', 'cancel'), ('MainCruiseButtonControl', 'mainCruise')])
def test_neutral_media_retains_audits_during_conventional_button_hold(context, key, button):
  from opendbc.car.structs import car
  params, cp = context
  # Main's sole supported command is adopt-speed-limit.
  original, changed = (10, 0) if key == 'MainCruiseButtonControl' else (8, 11)
  params.put(key, original, block=True)
  owner, source = wheel_actions.WheelPublisher(params), Ioniq6MediaButtons(1)
  observe(owner, source, params, cp, NOW)
  button_type = getattr(car.CarState.ButtonEvent.Type, button)
  owner.observe(params, cp, state(True, button=button_type), now_ns=NOW + 10_000_000, drive_id=DRIVE)
  params.put(key, changed, block=True)
  observe(owner, source, params, cp, NOW + 20_000_000)
  params.put(key, original, block=True)
  observe(owner, source, params, cp, NOW + 30_000_000)
  commands = owner.observe(params, cp, state(False, button=button_type), now_ns=NOW + 40_000_000, drive_id=DRIVE)
  assert not any(action for _, action in commands)


def test_valid_empty_media_never_turns_into_a_per_frame_audit(context):
  from opendbc.car.hyundai.ioniq6_media import MediaObservation
  params, cp = context
  owner = wheel_actions.WheelPublisher(params)
  with mock.patch.object(wheel_actions, 'capture', wraps=wheel_actions.capture) as capture:
    for frame in range(200):
      now = NOW + frame * 10_000_000
      media = MediaObservation((), now, frame // 20, True)
      assert not any(action for _, action in owner.observe(params, cp, state(), now_ns=now, drive_id=DRIVE, media=media))
    assert capture.call_count == 1
  assert owner.media_sample_state is None
