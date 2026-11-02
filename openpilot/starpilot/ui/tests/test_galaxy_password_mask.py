"""Compact password entry masks committed text and the hover candidate."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.mici.widgets.dialog import BigInputDialog


class TestCompactPasswordMask(unittest.TestCase):
  def test_optional_mask_hides_text_and_candidate_without_changing_default(self):
    dialog = BigInputDialog.__new__(BigInputDialog)
    dialog._rect = rl.Rectangle(0, 0, 476, 240)
    self.enterContext(patch.object(dialog, "_keyboard", NS(text=lambda: "secret", get_candidate_character=lambda: "x",
                                                       get_keyboard_height=lambda: 80, render=Mock()), create=True))
    self.enterContext(patch.object(dialog, "_hint_label", NS(text="hint", render=Mock()), create=True))
    dialog._enter_img = NS(width=76)
    dialog._enter_disabled_img = NS()
    dialog._backspace_img = NS(width=42)
    self.enterContext(patch.object(dialog, "_backspace_img_alpha", NS(x=0, update=Mock()), create=True))
    self.enterContext(patch.object(dialog, "_enter_img_alpha", NS(x=0, update=Mock()), create=True))
    dialog._text_valid = lambda text: len(text) >= 6
    with patch("openpilot.selfdrive.ui.mici.widgets.dialog.measure_text_cached", return_value=rl.Vector2(110, 35)), \
         patch("openpilot.selfdrive.ui.mici.widgets.dialog.gui_app.font"), \
         patch.object(rl, "begin_scissor_mode"), patch.object(rl, "end_scissor_mode"), \
         patch.object(rl, "draw_text_ex") as draw, patch.object(rl, "draw_rectangle_rounded"), \
         patch.object(rl, "draw_texture_ex"), patch.object(rl, "get_time", return_value=0):
      dialog._password_mode = True
      dialog._render(None)
      self.assertEqual([call.args[1] for call in draw.call_args_list], ["••••••", "•"])
      draw.reset_mock()
      dialog._password_mode = False
      dialog._render(None)
      self.assertEqual([call.args[1] for call in draw.call_args_list], ["secret", "x"])
