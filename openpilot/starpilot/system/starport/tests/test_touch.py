"""Receiver touch packets must preserve protobuf defaults and pointer state."""

import pytest

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
