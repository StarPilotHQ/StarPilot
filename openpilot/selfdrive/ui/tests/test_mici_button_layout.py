"""Card and glyph bounds with deterministic metrics; native renders cover real fonts."""

from collections import defaultdict
from types import SimpleNamespace

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.selfdrive.ui.mici.widgets import button
from openpilot.system.ui.lib import wrap_text
from openpilot.system.ui.lib.application import FONT_SCALE, gui_app
from openpilot.system.ui.widgets import label


@pytest.fixture
def drawn(monkeypatch):
  def measure(_font, text, size, _spacing=0):
    return rl.Vector2(len(text) * size * .5, size * FONT_SCALE)

  monkeypatch.setattr(gui_app, "font", lambda _: rl.Font())
  monkeypatch.setattr(gui_app, "texture", lambda _path, width, height: SimpleNamespace(width=width, height=height))
  monkeypatch.setattr(gui_app, "_mouse_events", [])
  monkeypatch.setattr(gui_app, "_show_touches", False)
  monkeypatch.setattr(label, "measure_text_cached", measure)
  monkeypatch.setattr(wrap_text, "measure_text_cached", measure)
  monkeypatch.setattr(wrap_text, "_cache", {})
  for name in ("draw_texture_ex", "draw_texture_pro", "begin_scissor_mode", "end_scissor_mode", "draw_rectangle_gradient_h"):
    monkeypatch.setattr(rl, name, lambda *_args: None)
  lines = defaultdict(list)
  monkeypatch.setattr(label.UnifiedLabel, "_render_line",
                      lambda self, text, size, y, *_args: lines[self].append((text, y, y + size.y)))
  return lines


@pytest.mark.parametrize("factory", [
  lambda: button.BigButton("use starpilot longitudinal planner", "On · Off uses the upstream planner"),
  lambda: button.BigButton("always on lateral", "saved options"),
  lambda: button.BigButton("driving interaction timeout", "5 s"),
  lambda: button.BigButton("driving interaction timeout -", "55 s"),
  lambda: button.BigButton("driving interaction timeout +", "55 s"),
  lambda: button.BigButton("brightness", "50 %"),
  lambda: button.BigButton("long branch name that needs to scroll", "installed", scroll=True),
  lambda: button.BigButton("network", "connected", SimpleNamespace(width=76, height=56)),
  lambda: button.BigToggle("enable controls", "Off"),
  lambda: button.BigToggle("disengage on accelerator"),
  lambda: button.BigMultiToggle("controller", ["Default", "Custom"]),
  lambda: button.GreyBigButton("saved settings", "unavailable while driving"),
  lambda: button.GreyBigButton("", "A full explanation that must retain enough room for several lines of readable text."),
  lambda: button.BigButton("driving\ncontrols"),
])
def test_card_title_and_value_do_not_overlap(factory, drawn):
  card = factory()
  card.set_position(20, 30)
  card._draw_content(card.rect.y)
  title, value = drawn[card._label], drawn[card._sub_label]
  if card.text:
    assert title
  if card.value:
    assert value
  if title and value:
    assert max(bottom for _, _, bottom in title) < min(top for _, top, _ in value)
  if card.text.endswith(("-", "+")):
    assert " ".join(text for text, _, _ in title) == card.text
  for _, top, bottom in title + value:
    assert top >= card.rect.y + card.LABEL_VERTICAL_PADDING
    assert bottom <= card.rect.y + card.rect.height - card.LABEL_VERTICAL_PADDING + .001
  if not card.text:
    assert len(value) > 1


def test_label_fits_the_last_glyph_as_well_as_line_spacing(drawn):
  text = label.UnifiedLabel("first\nsecond", font_size=48, line_height=.9)
  height = 48 * FONT_SCALE * 1.7
  text.render(rl.Rectangle(0, 0, 400, height))
  assert len(drawn[text]) == 1
  assert drawn[text][0][0].endswith("...")
  assert drawn[text][0][2] <= height
