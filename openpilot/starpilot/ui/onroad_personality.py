"""Shared compact/large personality artwork and longitudinal feedback."""

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadState, is_lane_change_notice, personality_bounds
from openpilot.starpilot.ui.onroad_widget_style import draw_widget_frame
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.system.ui.lib.application import gui_app

HIGHLIGHT_TIME = 2.5


class PersonalityWidget:
  def __init__(self, fonts: BitmapFonts | None = None):
    self.fonts = fonts
    self._lead_car = None
    self._distance = []
    self._alpha_filter = FirstOrderFilter(0.0, 0.05, 1 / gui_app.target_fps)
    self._lead_car_filters = tuple(FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps) for _ in range(2))
    self._distance_filters = [(FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps),
                               FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)) for _ in range(3)]
    self.reset()

  def reset(self) -> None:
    self._personality = None
    self._personality_changed_time = -HIGHLIGHT_TIME
    self._alpha_filter.x = 0.0

  def _prepare(self) -> None:
    if self._lead_car is not None:
      return
    def art(name, width, height):
      # Keep the full source resolution so the large profile never enlarges compact-sized textures.
      return gui_app.texture(f'icons_mici/longitudinal/{name}.png'), width, height
    self._lead_car = (art('car', 35, 27), art('car_green', 50, 42))
    self._distance = [(art(f'distance_{i}', w, h), art(f'distance_{i}_green', w + 28, h + 28))
                      for i, w, h in ((1, 32, 7), (2, 40, 9), (3, 48, 11))]

  def render(self, state: OnroadState) -> None:
    profile = self.fonts.profile if self.fonts is not None else Profile.COMPACT
    bounds = personality_bounds(profile, state)
    routine = state.alert.size == AlertSize.NONE or is_lane_change_notice(state.alert)
    if bounds is None or profile == Profile.COMPACT and bounds[0] < 476 and not routine:
      return
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    rect = rl.Rectangle(left, top, width, height)
    draw_widget_frame(rect, state.customization, profile, 'following_distance')
    self.draw(rect, personality=state.personality, has_lead=state.lead_detected, longitudinal_active=state.cruise_active,
              traffic_mode=state.traffic_selected or state.traffic_mode, gas_override=state.gas_pressed_override, visible=routine)

  def draw(self, rect: rl.Rectangle, *, personality: int, has_lead: bool, longitudinal_active: bool,
           traffic_mode: bool = False, gas_override: bool = False, visible: bool = True, fit: bool = True) -> None:
    self._prepare()
    assert self._lead_car is not None
    alpha = self._alpha_filter.update(visible)
    engaged_lead = has_lead and longitudinal_active
    white_target = 0.0 if engaged_lead else (1.0 if has_lead else 0.35)
    white_f, green_f = self._lead_car_filters
    if not longitudinal_active:
      white_f.x, green_f.x = white_target, 0.0
    self._draw_centered(self._lead_car[0], rect, 100, white_f.update(white_target) * alpha, fit=fit)
    self._draw_centered(self._lead_car[1], rect, 100, green_f.update(float(engaged_lead)) * alpha, fit=fit)

    now = rl.get_time()
    if self._personality is not None and personality != self._personality:
      self._personality_changed_time = now
    self._personality = personality
    # The personality notice hides the bars; retain its highlight until they return.
    if not visible and now - self._personality_changed_time < HIGHLIGHT_TIME:
      self._personality_changed_time = now
    highlight = now - self._personality_changed_time < HIGHLIGHT_TIME
    blink = 0.35 / 0.9 if gas_override and longitudinal_active and not traffic_mode and now % 0.375 > 0.1875 else 1.0
    count = 1 if traffic_mode else personality + 1
    for i, ((white, green), y, (active_f, green_f)) in enumerate(zip(self._distance, (122, 136, 152), self._distance_filters, strict=True)):
      active = active_f.update(float(i < count))
      green_alpha = green_f.update(float(highlight and i == count - 1 and not traffic_mode))
      color = (200, 32, 48) if traffic_mode and i < count else (255, 255, 255)
      self._draw_centered(white, rect, y, (0.35 * (1 - active) + 0.9 * (active - green_alpha) * blink) * alpha, color, fit=fit)
      self._draw_centered(green, rect, y, green_alpha * blink * alpha, fit=fit)

  @staticmethod
  def _draw_centered(art, rect: rl.Rectangle, y: float, alpha: float,
                     color: tuple[int, int, int] = (255, 255, 255), *, fit: bool = True) -> None:
    texture, width, height = art
    opacity = round(255 * alpha)
    if opacity == 0:
      return
    padding = rect.width / 15
    scale = min((rect.width - padding) / 76, (rect.height - padding) / 94) if fit else 1.0
    cx = rect.x + rect.width / 2 if fit else rect.x + 46
    cy = rect.y + rect.height / 2 + (y - 126) * scale if fit else rect.y + y
    rl.draw_texture_pro(texture, rl.Rectangle(0, 0, texture.width, texture.height),
                        rl.Rectangle(cx - width * scale / 2, cy - height * scale / 2, width * scale, height * scale),
                        rl.Vector2(0, 0), 0, rl.Color(*color, opacity))
