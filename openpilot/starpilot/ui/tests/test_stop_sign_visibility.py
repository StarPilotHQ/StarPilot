from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.ui import stop_sign_widget
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.tests.test_unified_speed_native import foreground_calls


@pytest.mark.parametrize('profile', (Profile.COMPACT, Profile.LARGE))
def test_original_stop_sign_fill_outline_and_text(profile):
  fonts = SimpleNamespace(profile=profile, horizontal_ink=lambda *args: (0., 40.),
                          vertical_ink=lambda *args: (0., 20.), draw=Mock())
  with patch.object(stop_sign_widget.rl, 'draw_poly') as fill, \
       patch.object(stop_sign_widget.rl, 'draw_line_ex') as border:
    stop_sign_widget.draw_stop_sign(fonts, 40., 50., scale=.7 if profile == Profile.COMPACT else 1.25)
  if profile == Profile.LARGE:
    fill.assert_called_once()
    assert fill.call_args.args[1] == 8
    color = fill.call_args.args[-1]
    assert (color.r, color.g, color.b, color.a) == (10, 17, 19, 230)
  else:
    fill.assert_not_called()
  edges = foreground_calls(border)
  assert len(edges) == 8
  assert all((color.r, color.g, color.b, color.a) == (244, 247, 245, 255)
             for color in (call.args[-1] for call in edges))
  text = foreground_calls(fonts.draw)
  assert len(text) == 1
  assert text[0].args[0] == 'STOP'
  white = text[0].args[-1]
  assert (white.r, white.g, white.b, white.a) == (244, 247, 245, 255)
