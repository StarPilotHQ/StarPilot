from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.ui import stop_sign_widget
from openpilot.starpilot.ui.presentation import Profile


@pytest.mark.parametrize('profile', (Profile.COMPACT, Profile.LARGE))
def test_stop_sign_has_opaque_red_fill_white_octagon_and_text(profile):
  fonts = SimpleNamespace(profile=profile, horizontal_ink=lambda *args: (0., 40.),
                          vertical_ink=lambda *args: (0., 20.), draw=Mock())
  with patch.object(stop_sign_widget.rl, 'draw_poly') as fill, \
       patch.object(stop_sign_widget.rl, 'draw_line_ex') as border:
    stop_sign_widget.draw_stop_sign(fonts, 40., 50., scale=.7 if profile == Profile.COMPACT else 1.25)
  fill.assert_called_once()
  assert fill.call_args.args[1] == 8
  red = fill.call_args.args[-1]
  assert (red.r, red.g, red.b, red.a) == (200, 0, 0, 255)
  assert border.call_count == 8
  assert all((color.r, color.g, color.b, color.a) == (244, 247, 245, 255)
             for color in (call.args[-1] for call in border.call_args_list))
  fonts.draw.assert_called_once()
  assert fonts.draw.call_args.args[0] == 'STOP'
  white = fonts.draw.call_args.args[-1]
  assert (white.r, white.g, white.b, white.a) == (244, 247, 245, 255)
