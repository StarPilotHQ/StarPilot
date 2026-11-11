from types import SimpleNamespace
from unittest.mock import Mock

from openpilot.starpilot.models.catalog import model_entries, BUNDLED_CURRENT
from openpilot.starpilot.models.status import ModelStatus, ModelHealth
from openpilot.starpilot.ui.home import HomeView
from openpilot.starpilot.ui.models_state import home_model_label, model_display_text


def test_home_pair_uses_verified_roles_and_never_saved_choices():
  ids = list(model_entries())[:2]
  normal = ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None)
  pair = {"active": True, "lateralModel": ids[0], "longitudinalModel": ids[1], "requested": False, "pendingNextStart": True}
  names = [model_display_text(model_entries()[mid].name) for mid in ids]
  assert home_model_label(normal, "abcdef123", pair) == " / ".join(names)
  assert home_model_label(normal, laboratory=pair | {"active": False}) == "Laboratory output unavailable"
  assert home_model_label(normal, laboratory={"requested": True, "health": "unavailable"}) == "Model Laboratory · next start"
  assert home_model_label(normal, laboratory={"requested": True, "health": "fallback"}) == home_model_label(normal)


def test_compact_keeps_both_names_inside_the_model_row():
  view = HomeView.__new__(HomeView)
  view._text = Mock()
  view._icon = Mock()
  view._measure = lambda text, role, size: SimpleNamespace(width=len(text) * size * .6)
  state = SimpleNamespace(version="7", branch="Dom", commit_date="", model_label="A" * 60 + " / " + "B" * 60,
                          network="none", network_strength=0)
  # Stop after rendering the header; later sections need unrelated home state.
  class EndHeader(Exception):
    pass
  view._icon.side_effect = EndHeader
  try:
    view._compact(state)
  except EndHeader:
    pass
  drawn = next(call.args for call in view._text.call_args_list if len(call.args) > 4 and call.args[4] == 140)
  text, role, size, x, y, color = drawn
  assert text.startswith("A") and " / B" in text
  assert "Lateral" not in text and "Longitudinal" not in text
  assert view._measure(text, role, size).width <= 520
