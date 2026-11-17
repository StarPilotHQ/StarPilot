"""Original-history selection units, without native or scoring authority."""
from collections import deque
from dataclasses import replace
from types import SimpleNamespace

import pytest
from openpilot.starpilot.flm.original_context_receipts import OriginalContextReceipts
from openpilot.starpilot.flm.live_tracking_actor import _original_probe_history
from openpilot.starpilot.flm.tests.test_live_tracking_episodes import owner, pair


def fixture(count=120, *, source_tracking=True):
  builder = owner()
  controls = [pair(builder, index)[0] for index in range(count)]
  context = OriginalContextReceipts()
  for control in controls:
    context.record_control(control)
  ingress = SimpleNamespace(episodes=SimpleNamespace(source_tracking=source_tracking), control_history=deque(controls[-30:]))
  collector = SimpleNamespace(adapter=SimpleNamespace(original_context_receipts=context))
  return collector, ingress, controls


def test_physical_warmup_does_not_discard_original_numerical_prestate():
  collector, ingress, controls = fixture()
  before = tuple(ingress.control_history)
  result = _original_probe_history(collector, ingress, controls[-1])
  assert result == tuple(controls)
  assert all(left is right for left, right in zip(result, controls, strict=True))
  assert tuple(ingress.control_history) == before
  assert not hasattr(ingress, 'ready')  # No endpoint or scoring admission is created.


def test_original_probe_context_is_bounded_and_never_includes_future_controls():
  collector, ingress, controls = fixture(700)
  result = _original_probe_history(collector, ingress, controls[600])
  assert result == tuple(controls[300:601])
  assert len(result) == 301
  assert result[-1] is controls[600]


def test_changed_duplicate_original_context_still_fails_closed():
  collector, ingress, controls = fixture()
  collector.adapter.original_context_receipts.record_control(replace(controls[0], requested=0.5))
  assert collector.adapter.original_context_receipts.failed
  with pytest.raises(ValueError, match='original probe context unavailable'):
    _original_probe_history(collector, ingress, controls[-1])


def test_identified_plant_keeps_its_existing_admitted_control_history():
  collector, ingress, controls = fixture(source_tracking=False)
  assert _original_probe_history(collector, ingress, controls[-1]) == tuple(ingress.control_history)
