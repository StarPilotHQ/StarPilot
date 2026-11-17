from types import SimpleNamespace
from unittest.mock import Mock

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.system.ui.widgets.mici_keyboard import MiciKeyboard, SELECTED_CHAR_FONT_SIZE


@pytest.fixture
def layout():
  keyboard = object.__new__(MiciKeyboard)
  keyboard._rect = rl.Rectangle(0, 0, 520, 240)
  keyboard._txt_bg = SimpleNamespace(width=520, height=170)
  keys = [[Mock(rect=rl.Rectangle(0, 0, 30, 40), original_position=rl.Vector2(x * 40, y * 40)) for x in range(2)] for y in range(2)]
  keyboard._closest_key = (keys[0][0], 0)
  vars(keyboard)['_selected_key_filter'] = SimpleNamespace(x=0.45)
  return keyboard, keys


@pytest.mark.parametrize("error", [None, TypeError, RuntimeError])
def test_selected_key_layout_supports_both_gradient_signatures(layout, monkeypatch, error):
  keyboard, keys = layout
  def draw(*args):
    if error is not None and len(args) == 4:
      raise error("unsupported gradient signature")
  draw_circle = Mock(side_effect=draw)
  monkeypatch.setattr(rl, "draw_circle_gradient", draw_circle)
  keyboard._lay_out_keys(0, 70, keys)
  assert draw_circle.call_count == (1 if error is None else 2)
  vector, radius, inner, outer = draw_circle.call_args_list[0].args
  assert radius == SELECTED_CHAR_FONT_SIZE
  assert (inner.r, inner.g, inner.b, inner.a) == (0, 0, 0, 101)
  assert outer == rl.BLANK
  if error is not None:
    x, y, fallback_radius, fallback_inner, fallback_outer = draw_circle.call_args.args
    assert (x, y) == (int(vector.x), int(vector.y))
    assert type(x) is type(y) is int
    assert fallback_radius == radius
    assert fallback_inner.a == inner.a and fallback_outer == outer
  keys[0][0].set_font_size.assert_called_once_with(SELECTED_CHAR_FONT_SIZE)
  for row in keys:
    for key in row:
      key.set_position.assert_called_once()


def test_other_gradient_errors_are_not_hidden(layout, monkeypatch):
  keyboard, keys = layout
  draw = Mock(side_effect=ValueError("unrelated drawing failure"))
  monkeypatch.setattr(rl, "draw_circle_gradient", draw)
  with pytest.raises(ValueError, match="unrelated drawing failure"):
    keyboard._lay_out_keys(0, 70, keys)
  draw.assert_called_once()
