"""Receiver touch packets must preserve protobuf defaults and pointer state."""

import pytest
from unittest.mock import Mock

from openpilot.starpilot.system.starport.session import INPUT_BINDING_RESPONSE, INPUT_EVENT, VideoMode
from openpilot.starpilot.system.starport.tests.test_media_controls import session_with_input
from openpilot.starpilot.system.starport.touch import InputConfig, TouchEvent, TouchMapper
from openpilot.starpilot.system.starport.wire import field


def packet(action, x=640, y=360):
  # A zero pointer ID and action index may also be omitted by the receiver.
  location = field(1, x) + field(2, y)
  touch = field(1, location)
  if action is not None:
    touch += field(3, action)
  return field(3, touch)


@pytest.mark.parametrize('press', [None, 0])
def test_default_and_explicit_press_both_start_a_complete_gesture(press):
  mapper = TouchMapper(InputConfig(touch_width=1280, touch_height=720), 1280, 720, 0, 0)
  assert mapper.decode(packet(press)) == [TouchEvent('down', .5, .5)]
  assert mapper.decode(packet(2, 960)) == [TouchEvent('move', .75, .5)]
  assert mapper.decode(packet(1, 960)) == [TouchEvent('up', .75, .5)]
  assert mapper.pointer is None
  assert mapper.decode(packet(press)) == [TouchEvent('down', .5, .5)]


def test_move_without_a_press_does_not_start_a_gesture():
  mapper = TouchMapper(InputConfig(), 1280, 720, 0, 0)
  assert mapper.decode(packet(2)) == []
  assert mapper.decode(packet(1)) == []
  assert mapper.pointer is None


@pytest.fixture
def session():
  value = session_with_input(True)
  value.touch = TouchMapper(InputConfig(), 1280, 720, 0, 0)
  return value


def test_logs_wire_packet_and_mapped_gesture(session):
  for action in (None, 2, 1):
    session.handle(3, INPUT_EVENT, packet(action))
  records = [values for name, values in session.events if name == 'input_event']
  assert [row['mapped'][0]['kind'] for row in records] == ['down', 'move', 'up']
  assert all(row['channel'] == 3 and row['disposition'] == 'queued' for row in records)
  assert records[0]['message'][3][0][1][0] == {1: [640], 2: [360]}
  assert records[0]['mapped'] == [{'kind': 'down', 'x': .5, 'y': .5}]
  assert len(session.touch_events) == 3


@pytest.mark.parametrize('reason', ['unfocused', 'no_mapper', 'no_touch_event', 'invalid'])
def test_logs_why_touch_did_not_queue(session, reason):
  data = packet(None)
  if reason == 'unfocused':
    session.focused = False
  elif reason == 'no_mapper':
    session.touch = None
  elif reason == 'no_touch_event':
    data = packet(2)
  else:
    data = field(3, b'\x80')
  session.handle(3, INPUT_EVENT, data)
  record = next(values for name, values in session.events if name == 'input_event')
  assert record['disposition'] == reason
  assert record['mapped'] == []
  assert session.touch_events == []


def test_input_log_sampling_does_not_drop_gestures(session):
  for _ in range(100):
    session.handle(3, INPUT_EVENT, packet(None))
    session.handle(3, INPUT_EVENT, packet(1))
  records = [values for name, values in session.events if name == 'input_event']
  assert [row['count'] for row in records] == [*range(1, 21), 100, 200]
  assert len(session.touch_events) == 200


def test_channel_selection_and_mapping_are_logged(session):
  session.channels = [{'id': 4, 'input': True, 'display_id': 1},
                      {'id': 3, 'input': True, 'display_id': 0, 'input_config': InputConfig((), 1280, 720)}]
  session.mode = VideoMode(5, 0, 1280, 720, 30, 0, 240)
  session.send = Mock()
  session.wait_for = Mock(return_value=field(1, 0))
  session.open_input()
  selection = next(values for name, values in session.events if name == 'input_channels')
  assert selection['selected'] == 3
  assert [ch['channel'] for ch in selection['channels']] == [4, 3]
  opened = next(values for name, values in session.events if name == 'input_opened')
  assert opened['channel'] == 3
  assert opened['mapping']['content_height'] == 480
  assert opened['mapping']['content_top'] == 120


def test_no_advertised_input_channel_is_logged(session):
  session.channels = []
  session.open_input()
  assert session.events == [('input_channels', {'channels': [], 'selected': None})]


def test_failed_input_channel_is_identified(session):
  session.channels = [{'id': 7, 'input': True}]
  session.send = Mock()
  session.wait_for = Mock(side_effect=TimeoutError('no response'))
  session.open_input()
  assert ('input_unavailable', {'channel': 7, 'error': 'no response'}) in session.events


def test_binding_response_includes_channel(session):
  session.handle(3, INPUT_BINDING_RESPONSE, field(1, 0))
  assert session.events == [('input_bound', {'channel': 3, 'status': 0})]
