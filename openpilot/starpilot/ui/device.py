"""Captured large Device panel presentation from explicitly supplied state."""

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import clip

from openpilot.starpilot.ui.device_state import BUTTON_ROWS, DeviceRequest, DeviceState, button_rect, request_enabled
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile

ROW_TITLES = ("Dongle ID", "Serial", "Pair with Galaxy", "Driver Camera", "Reset Driver Monitoring", "Reset Calibration")
BUTTON_TEXT = ("PAIR", "PREVIEW", "RESET", "RESET")
GRAY = rl.GRAY
VALUE_GRAY = rl.Color(170, 170, 170, 255)
BUTTON_GRAY = rl.Color(57, 57, 57, 255)
BUTTON_DISABLED = rl.Color(51, 51, 51, 255)
BUTTON_TEXT_COLOR = rl.Color(228, 228, 228, 255)


class DeviceView:
  """Draw only the right pane; the host owns the shared Settings rail."""

  def __init__(self, fonts: BitmapFonts):
    if fonts.profile != Profile.LARGE:
      raise ValueError("Only the large Device panel has a frozen capture")
    self.fonts = fonts

  def render(self, state: DeviceState) -> None:
    rl.draw_rectangle_rounded(rl.Rectangle(510, 10, 1640, 1060), 0.04, 30, rl.BLACK)
    clip.begin_scissor_mode(550, 50, 1560, 980)
    try:
      for index, title in enumerate(ROW_TITLES):
        if index == 2 and state.galaxy_local_only:
          title = "Galaxy local access"
        top = 50 + index * 171
        if top >= 1030:
          break
        size = self.fonts.measure(title, FontRole.NORMAL, 50)
        self.fonts.draw(title, FontRole.NORMAL, 50, 570, top + (170 - size.height) // 2)
        if index < 2:
          value = state.dongle_id if index == 0 else state.serial
          measured = self.fonts.measure(value, FontRole.NORMAL, 50)
          self.fonts.draw(value, FontRole.NORMAL, 50, 2110 - measured.width, top + (170 - measured.height) / 2, VALUE_GRAY)
        else:
          request = BUTTON_ROWS[index - 2][0]
          x, y, width, height = button_rect(request)
          enabled = request_enabled(request, state)
          rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, height), 1, 10, BUTTON_GRAY if enabled else BUTTON_DISABLED)
          label = "OPEN" if request == DeviceRequest.OPEN_GALAXY and state.galaxy_local_only else (
            "MANAGE" if request == DeviceRequest.OPEN_GALAXY and state.galaxy_paired else BUTTON_TEXT[index - 2])
          label_size = self.fonts.measure(label, FontRole.MEDIUM, 35)
          self.fonts.draw(label, FontRole.MEDIUM, 35, x + (width - label_size.width) // 2,
                          y + (height - label_size.height) // 2, BUTTON_TEXT_COLOR if enabled else VALUE_GRAY)
        if index < len(ROW_TITLES) - 1:
          rl.draw_line(590, top + 170, 2070, top + 170, GRAY)
    finally:
      clip.end_scissor_mode()
