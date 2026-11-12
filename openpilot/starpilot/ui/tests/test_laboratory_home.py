from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

from openpilot.starpilot.models.catalog import model_entries, BUNDLED_CURRENT
from openpilot.starpilot.models.status import ModelStatus, ModelHealth
from openpilot.starpilot.ui.home import HomeView
from openpilot.starpilot.ui.home_state import HomeState
from openpilot.starpilot.ui.models_state import home_model_label, model_display_text


def test_home_pair_is_names_only_before_during_and_after_startup():
  ids = list(model_entries())[:2]
  normal = ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None)
  names = [model_display_text(model_entries()[mid].name) for mid in ids]
  for fields in ({"enabled": True}, {"active": True}, {"active": False, "health": "stale"}):
    for order in ((0, 1), (1, 0)):
      pair = {"lateralModel": ids[order[0]], "longitudinalModel": ids[order[1]], **fields}
      assert home_model_label(normal, "abcdef123", pair) == "/".join(names[i] for i in order)
  assert home_model_label(normal, laboratory={"requested": False}) == home_model_label(normal)


def test_compact_keeps_both_names_inside_the_model_row():
  view = HomeView.__new__(HomeView)
  view._text = Mock()
  view._icon = Mock()
  measure = Mock(side_effect=lambda text, role, size: SimpleNamespace(width=len(text) * size * .6))
  state = SimpleNamespace(version="7", branch="Dom", commit_date="", model_label="A" * 60 + "/" + "B" * 60,
                          network="none", network_strength=0)
  # Stop after rendering the header; later sections need unrelated home state.
  class EndHeader(Exception):
    pass
  view._icon.side_effect = EndHeader
  try:
    with patch.object(view, "_measure", measure):
      view._compact(cast(HomeState, state))
  except EndHeader:
    pass
  drawn = next(call.args for call in view._text.call_args_list if len(call.args) > 4 and call.args[4] == 140)
  text, role, size, x, y, color = drawn
  assert text.startswith("A") and "/B" in text
  assert "Lateral" not in text and "Longitudinal" not in text
  assert measure(text, role, size).width <= 520
