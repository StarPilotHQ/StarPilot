"""Screen-space clipping for native shell views drawn at a widget offset."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

from openpilot.starpilot.ui.raylib import rl


_placement: ContextVar[tuple[float, float, rl.Rectangle, rl.Rectangle | None] | None] = ContextVar("shell_placement", default=None)


def _intersection(a: rl.Rectangle, b: rl.Rectangle) -> rl.Rectangle:
  x = max(a.x, b.x)
  y = max(a.y, b.y)
  return rl.Rectangle(x, y, max(0, min(a.x + a.width, b.x + b.width) - x),
                      max(0, min(a.y + a.height, b.y + b.height) - y))


@contextmanager
def placed_at(rect: rl.Rectangle, parent_clip: rl.Rectangle | None = None) -> Iterator[None]:
  """Translate view geometry and its explicit scissors together.

  The compact scroller already has a screen-space scissor. Child scissors replace
  it in raylib, so each child clip is intersected with that parent and restored.
  """
  viewport = _intersection(rect, parent_clip) if parent_clip is not None else rect
  token = _placement.set((rect.x, rect.y, viewport, parent_clip))
  rl.rl_push_matrix()
  rl.rl_translatef(rect.x, rect.y, 0)
  try:
    yield
  finally:
    rl.rl_pop_matrix()
    if parent_clip is not None:
      rl.begin_scissor_mode(int(parent_clip.x), int(parent_clip.y), int(parent_clip.width), int(parent_clip.height))
    else:
      rl.end_scissor_mode()
    _placement.reset(token)


@contextmanager
def clipped(rect: rl.Rectangle, parent: rl.Rectangle) -> Iterator[None]:
  bounds = _intersection(rect, parent)
  rl.rl_draw_render_batch_active()
  begin_scissor_mode(bounds.x, bounds.y, bounds.width, bounds.height)
  try:
    yield
  finally:
    rl.rl_draw_render_batch_active()
    begin_scissor_mode(parent.x, parent.y, parent.width, parent.height)


def begin_scissor_mode(x: float, y: float, width: float, height: float) -> None:
  placement = _placement.get()
  if placement is None:
    rl.begin_scissor_mode(int(x), int(y), int(width), int(height))
    return
  ox, oy, viewport, _ = placement
  clipped = _intersection(rl.Rectangle(x + ox, y + oy, width, height), viewport)
  rl.begin_scissor_mode(int(clipped.x), int(clipped.y), int(clipped.width), int(clipped.height))


def end_scissor_mode() -> None:
  placement = _placement.get()
  if placement is None:
    rl.end_scissor_mode()
    return
  _, _, viewport, _ = placement
  rl.begin_scissor_mode(int(viewport.x), int(viewport.y), int(viewport.width), int(viewport.height))
