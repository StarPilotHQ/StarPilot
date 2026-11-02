"""Lazy map resources for the native large screen; GPU preparation precedes drawing."""

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.system.starport.projection_map_lease import projection_map_active
from openpilot.system.ui.lib.application import gui_app


class NativeMapLayer:
  def __init__(self, fonts, ui_state):
    self.fonts, self.ui = fonts, ui_state
    self.map = self.feed = None
    self.pending = None
    self.prepared_size = None
    gui_app.add_render_prepare(self.prepare, self.close)

  @staticmethod
  def placement(state):
    placed = state.customization["layouts"]["large"].get("nav_map")
    return placed if placed is not None and placed["enabled"] else None

  def prepare(self):
    # The preceding rendered frame requests the next map. Hidden pages stop
    # requesting it, so their worker, GPS subscription and GPU targets go away.
    # Android Auto's own map takes precedence, so only one tile worker runs.
    state, self.pending = self.pending, None
    placed = self.placement(state) if state is not None and self.ui.started and not projection_map_active() else None
    if placed is None:
      self._release()
      return
    if self.map is None:
      from openpilot.starpilot.ui.onroad_map import MapFeed, MapOverlay
      self.map = MapOverlay(fonts=self.fonts)
      self.feed = MapFeed()
    self.map.prepare(self.feed.read(self.ui.sm), placed["width"], placed["height"])
    self.prepared_size = (placed["width"], placed["height"])

  def render(self, _rect, state):
    placed = self.placement(state)
    if placed is None or not self.ui.started or projection_map_active():
      self.pending = None
      return
    self.pending = state
    if self.map is not None and self.prepared_size == (placed["width"], placed["height"]):
      self.map.draw(rl.Rectangle(placed["x"] + state.viewport_width - 1860, placed["y"], placed["width"], placed["height"]),
                    placed["opacity"] / 100)

  def _release(self):
    if self.map is not None:
      self.map.close()
    self.map = self.feed = None
    self.prepared_size = None

  def close(self):
    gui_app.remove_render_prepare(self.prepare)
    self.pending = None
    self._release()
