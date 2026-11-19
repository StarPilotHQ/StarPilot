"""Keeps the big model card fed on the native large screen: Jetlink polled only while it is showing, and the GPU state."""

from openpilot.starpilot.models.jetlink_status import JetlinkStatusFeed
from openpilot.starpilot.ui.onroad_customization import JETLINK_WIDGET
from openpilot.starpilot.ui.onroad_jetlink import gpu_inputs
from openpilot.system.ui.lib.application import gui_app


class NativeJetlinkLayer:
  def __init__(self, widget, ui_state):
    self.widget, self.ui = widget, ui_state
    self.feed = JetlinkStatusFeed()
    self.pending = None
    gui_app.add_render_prepare(self.prepare, self.close)

  @staticmethod
  def placement(state):
    placed = state.customization["layouts"]["large"].get(JETLINK_WIDGET)
    return placed if placed is not None and placed["enabled"] else None

  def prepare(self):
    # The preceding rendered frame asks for the next poll; hidden pages stop asking.
    state, self.pending = self.pending, None
    self.feed.set_enabled(state is not None and bool(self.ui.started) and self.placement(state) is not None)

  def render(self, state):
    if self.placement(state) is None:
      self.pending = None
      return
    self.pending = state
    self.widget.status = self.feed.snapshot()
    self.widget.gpu, self.widget.chestnut = gpu_inputs(self.ui)
    self.widget.render(state)

  def close(self):
    gui_app.remove_render_prepare(self.prepare)
    self.pending = None
    self.feed.set_enabled(False)
    self.feed.close()
