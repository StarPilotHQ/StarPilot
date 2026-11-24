"""The 3D drive render on the comma 3 / 3X's own onroad screen.

Galaxy's "3D drive render" set to Comma display replaces the camera composition with the same rendered road view
and HUD StarPort's car screen draws. Nothing is imported or built until the first frame that needs it, so a device
that never chooses it pays nothing.
"""

from openpilot.starpilot.ui.raylib import rl


def build(fonts, ui_state):
  """The view and its HUD, reading the live UIState; imported here so only a device that draws them loads them."""
  from openpilot.selfdrive.ui.onroad.birds_eye_renderer import BirdsEyeRenderer, BitmapText
  from openpilot.starpilot.system.starport.birds_eye_hud import BirdsEyeHud
  text = BitmapText(fonts)
  view = BirdsEyeRenderer(text=text, source=lambda: (ui_state.sm, ui_state.started_frame))
  hud = BirdsEyeHud(rl, text, lambda: (ui_state.sm, getattr(ui_state, 'CP', None)))
  return view, hud


class DriveRenderLayer:
  def __init__(self, fonts, ui_state, *, factory=build):
    self.fonts = fonts
    self.ui_state = ui_state
    self._factory = factory
    self.view = None
    self.hud = None
    self._failed = False

  def ready(self) -> bool:
    """Build on first use. A failed build is remembered, so the caller keeps drawing the camera."""
    if self.view is None and not self._failed:
      try:
        self.view, self.hud = self._factory(self.fonts, self.ui_state)
      except Exception:
        self._failed = True
        from openpilot.common.swaglog import cloudlog
        cloudlog.exception("3D drive render unavailable on the comma display; showing the camera")
    return self.view is not None

  def render(self, content: rl.Rectangle, state) -> None:
    """Draw the view and the HUD inside ``content``; the caller owns the scissor, the alerts and the border."""
    view, hud = self.view, self.hud
    view.panel = False
    view.opacity = 1.0
    # The view restores this scissor when it is done, so the HUD and the alerts stay inside the content.
    view.parent_clip = content
    view.metric = bool(state.metric)
    view.road_style = state.customization.get('roadColors', {}).get('large', {})
    view.long_control = bool(getattr(self.ui_state, 'has_longitudinal_control', True))
    x, y, width, height = hud.render_rect(content.width, content.height)
    view.render(rl.Rectangle(content.x + x, content.y + y, width, height))
    hud.metric = view.metric
    # The HUD draws from (0, 0) and sets no scissors of its own, so a translation places it.
    rl.rl_push_matrix()
    try:
      rl.rl_translatef(content.x, content.y, 0)
      hud.render(content.width, content.height)
    finally:
      rl.rl_pop_matrix()

  def close(self) -> None:
    if self.hud is not None:
      self.hud.close()
    self.view = self.hud = None
