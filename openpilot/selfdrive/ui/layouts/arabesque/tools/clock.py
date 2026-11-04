"""Touch date and time wheels for the Arabesque clock."""

import calendar
from datetime import datetime
import math
from zoneinfo import ZoneInfo, available_timezones

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.time_helpers import MIN_DATE, MAX_DATE
from openpilot.selfdrive.ui.layouts.arabesque.theme import DEFAULT_THEME
from openpilot.system.ui.lib.application import MousePos, TextAlignment
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import gui_label


_COMMON_ZONES = {
  'Pacific/Honolulu': 'Hawaii',
  'America/Anchorage': 'Alaska',
  'America/Los_Angeles': 'Pacific',
  'America/Phoenix': 'Arizona',
  'America/Denver': 'Mountain',
  'America/Chicago': 'Central',
  'America/New_York': 'Eastern',
  'America/Halifax': 'Atlantic',
  'UTC': 'UTC',
}


class ClockEditor(Widget):
  def __init__(self, submit, cancel, timezone):
    super().__init__()
    self._submit, self._cancel = submit, cancel
    zones = available_timezones() | {timezone.key}
    self._zones = tuple(zone for zone in _COMMON_ZONES if zone in zones) + tuple(sorted(zones - _COMMON_ZONES.keys()))
    now = datetime.now(timezone)
    year = min(MAX_DATE.year, max(MIN_DATE.year, now.year))
    day = min(now.day, calendar.monthrange(year, now.month)[1])
    self._values = [now.month, day, year, now.hour % 12 or 12, now.minute, now.hour // 12, self._zones.index(timezone.key)]
    self.saving = False
    self.time_edited = False
    self.fold = now.fold
    self.error = ''
    self._pressed = None
    self._focus = 0
    self._keyboard_focus = False
    self._dragged = False
    self._offset = self._velocity = 0.0
    self._moving = None
    self._start_y = self._last_y = self._last_t = self._last_motion = 0.0

  def _update_layout_rects(self):
    rect = self._rect
    self._scale = s = min(rect.width / 2160, rect.height / 976)
    self._cancel_button = rl.Rectangle(rect.x + rect.width - 520 * s, rect.y + 40 * s, 220 * s, 88 * s)
    self._save_button = rl.Rectangle(rect.x + rect.width - 260 * s, rect.y + 40 * s, 220 * s, 88 * s)
    self._panel = panel = rl.Rectangle(rect.x + (rect.width - 1936 * s) / 2, rect.y + 220 * s, 1936 * s, 640 * s)
    self._fold_button = rl.Rectangle(panel.x + 36 * s, panel.y + 556 * s, 880 * s, 76 * s)
    self._row_height = 84 * s
    width = (panel.width - 436 * s) / 6
    wheels = [rl.Rectangle(panel.x + 36 * s + index * width + (40 * s if index == 1 else 0) + (32 * s if index >= 3 else 0),
                           panel.y + 132 * s, width + (40 * s if index == 0 else -40 * s if index == 1 else 0),
                           5 * self._row_height) for index in range(6)]
    wheels.append(rl.Rectangle(panel.x + 100 * s + 6 * width, panel.y + 132 * s, 300 * s, 5 * self._row_height))
    self._wheels = tuple(wheels)

  @property
  def timezone(self):
    return self._zones[self._values[6]]

  def _limits(self, index):
    month, _day, year, _hour, _minute, _ampm, _zone = self._values
    return ((1, 12), (1, calendar.monthrange(year, month)[1]), (MIN_DATE.year, MAX_DATE.year),
            (1, 12), (0, 59), (0, 1), (0, len(self._zones) - 1))[index]

  def _change(self, index, amount):
    old_time = self._selected().replace(tzinfo=ZoneInfo(self.timezone)) if index == 6 and not self.time_edited else None
    previous = self._values[index]
    low, high = self._limits(index)
    value = self._values[index] + amount
    self._values[index] = min(high, max(low, value)) if index in (2, 5) else low + (value - low) % (high - low + 1)
    if index < 6 and self._values[index] != previous:
      self.time_edited = True
    if index in (0, 2):
      self._values[1] = min(self._values[1], self._limits(1)[1])
    if old_time is not None and self._values[index] != previous:
      local = old_time.astimezone(ZoneInfo(self.timezone))
      if MIN_DATE.year <= local.year <= MAX_DATE.year:
        self._values[:6] = [local.month, local.day, local.year, local.hour % 12 or 12, local.minute, local.hour // 12]
        self.fold = local.fold
    if self._values[index] != previous and not self._ambiguous():
      self.fold = 0
    self.error = ''

  def _ambiguous(self):
    local = self._selected()
    zone = ZoneInfo(self.timezone)
    return local.replace(tzinfo=zone, fold=0).utcoffset() > local.replace(tzinfo=zone, fold=1).utcoffset()

  def _toggle_fold(self):
    self.fold = 1 - self.fold
    self.time_edited = True
    self.error = ''

  def _label_value(self, index, offset):
    low, high = self._limits(index)
    value = self._values[index] + offset
    if index in (2, 5):
      if not low <= value <= high:
        return ''
    else:
      value = low + (value - low) % (high - low + 1)
    if index == 0:
      return calendar.month_name[value]
    if index == 5:
      return ('AM', 'PM')[value]
    if index == 6:
      zone = self._zones[value]
      return _COMMON_ZONES.get(zone, zone.rsplit('/', 1)[-1].replace('_', ' '))
    return f'{value:02d}' if index == 4 else str(value)

  def _scroll(self, index, delta):
    self._offset += delta
    steps = round(self._offset / self._row_height)
    if steps:
      self._change(index, -steps)
      self._offset -= steps * self._row_height

  def _update_state(self):
    if not self.enabled or self.saving:
      self._pressed = self._moving = None
      self._offset = self._velocity = 0.0
      while rl.get_key_pressed() or rl.get_char_pressed():
        pass
      return
    if self._moving is not None:
      dt = max(0.0, min(rl.get_frame_time(), 0.05))
      self._scroll(self._moving, self._velocity * dt)
      self._velocity *= math.exp(-8 * dt)
      if abs(self._velocity) < 25 * self._scale:
        self._moving = None
    elif self._pressed is None and self._offset:
      self._offset *= math.exp(-16 * max(0.0, min(rl.get_frame_time(), 0.05)))
      if abs(self._offset) < 0.5 * self._scale:
        self._offset = 0.0

    key = rl.get_key_pressed()
    while key:
      if self._focus == len(self._wheels) and not self._ambiguous():
        self._focus -= 1
      if key in (rl.KEY_LEFT, rl.KEY_RIGHT, rl.KEY_TAB, rl.KEY_UP, rl.KEY_DOWN):
        self._moving = None
        self._offset = self._velocity = 0.0
      if key in (rl.KEY_LEFT, rl.KEY_RIGHT, rl.KEY_TAB):
        self._keyboard_focus = True
        backwards = key == rl.KEY_LEFT or key == rl.KEY_TAB and (rl.is_key_down(rl.KEY_LEFT_SHIFT) or rl.is_key_down(rl.KEY_RIGHT_SHIFT))
        self._focus = (self._focus + (-1 if backwards else 1)) % (len(self._wheels) + self._ambiguous())
      elif key in (rl.KEY_UP, rl.KEY_DOWN):
        self._keyboard_focus = True
        if self._focus == len(self._wheels):
          self._toggle_fold()
        else:
          self._change(self._focus, -1 if key == rl.KEY_UP else 1)
      elif key in (rl.KEY_ENTER, rl.KEY_KP_ENTER):
        if self._keyboard_focus and self._focus == len(self._wheels):
          self._toggle_fold()
        else:
          self._save()
      elif key == rl.KEY_ESCAPE:
        self._cancel()
        return
      key = rl.get_key_pressed()
    while rl.get_char_pressed():
      pass
    wheel = rl.get_mouse_wheel_move()
    if wheel:
      pos = rl.get_mouse_position()
      for index, bounds in enumerate(self._wheels):
        if rl.check_collision_point_rec(pos, bounds):
          self._moving = None
          self._offset = self._velocity = 0.0
          self._change(index, -round(wheel))
          break

  def _selected(self):
    month, day, year, hour, minute, ampm, _zone = self._values
    return datetime(year, month, day, hour % 12 + ampm * 12, minute, fold=self.fold)

  def _save(self):
    self._submit(self._selected().strftime('%Y-%m-%d %H:%M'))

  def _handle_mouse_press(self, pos: MousePos):
    if self.saving:
      return
    self._moving = None
    self._offset = self._velocity = 0.0
    self._pressed = next((index for index, rect in enumerate(self._wheels) if rl.check_collision_point_rec(pos, rect)), None)
    if self._pressed is None:
      self._pressed = next((action for rect, action in ((self._cancel_button, 'cancel'), (self._save_button, 'save'))
                            if rl.check_collision_point_rec(pos, rect)), None)
    if self._pressed is None and self._ambiguous() and rl.check_collision_point_rec(pos, self._fold_button):
      self._pressed = 'fold'
    self._start_y = self._last_y = pos.y
    self._last_t = self._last_motion = 0.0
    self._dragged = False
    self._keyboard_focus = False

  def _handle_mouse_event(self, mouse_event):
    if not isinstance(self._pressed, int):
      return
    if mouse_event.left_pressed:
      self._last_t = self._last_motion = mouse_event.t
      return
    delta = mouse_event.pos.y - self._last_y
    self._dragged |= abs(mouse_event.pos.y - self._start_y) > 12 * self._scale
    if delta:
      self._scroll(self._pressed, delta)
      elapsed = mouse_event.t - self._last_t
      if 0 < elapsed < 0.15:
        self._velocity = max(-1600 * self._scale, min(1600 * self._scale, delta / elapsed))
      self._last_motion = mouse_event.t
    self._last_y, self._last_t = mouse_event.pos.y, mouse_event.t
    if mouse_event.left_released and mouse_event.t - self._last_motion > 0.12:
      self._velocity = 0.0
    if mouse_event.left_released and not rl.check_collision_point_rec(mouse_event.pos, self._rect):
      self._pressed = None
      self._offset = self._velocity = 0.0

  def _handle_mouse_release(self, pos: MousePos):
    pressed, self._pressed = self._pressed, None
    if isinstance(pressed, int):
      if self._dragged:
        self._moving = pressed if abs(self._velocity) >= 25 * self._scale else None
      else:
        wheel = self._wheels[pressed]
        self._change(pressed, round((pos.y - wheel.y - wheel.height / 2) / self._row_height))
        self._offset = 0.0
      self._focus = pressed
    elif pressed == 'cancel' and rl.check_collision_point_rec(pos, self._cancel_button):
      self._cancel()
    elif pressed == 'save' and rl.check_collision_point_rec(pos, self._save_button):
      self._save()
    elif pressed == 'fold' and self._ambiguous() and rl.check_collision_point_rec(pos, self._fold_button):
      self._toggle_fold()

  def _render(self, rect):
    s, theme = self._scale, DEFAULT_THEME
    rl.draw_rectangle_rec(rect, theme.background)
    gui_label(rl.Rectangle(rect.x + 48 * s, rect.y + 40 * s, rect.width - 608 * s, 88 * s), tr('Set Date & Time'),
              round(48 * s), theme.text, theme.font_weight)
    hint = self.error or tr('Swipe each wheel to select the local date and time.')
    gui_label(rl.Rectangle(rect.x + 48 * s, rect.y + 140 * s, rect.width - 96 * s, 52 * s),
              tr('Setting time…') if self.saving else hint, round(28 * s),
              theme.text if self.error else theme.secondary_text, theme.font_weight)

    for bounds, action, label in ((self._cancel_button, 'cancel', tr('Cancel')), (self._save_button, 'save', tr('Set Time'))):
      primary = action == 'save'
      pressed = self.enabled and not self.saving and self._pressed == action and self.is_pressed
      face = theme.surface if self.saving else theme.pressed if pressed else theme.control if primary else theme.surface
      color = theme.secondary_text if self.saving else theme.background if primary else theme.text
      rl.draw_rectangle_rounded(bounds, 0.2, 8, face)
      gui_label(bounds, label, round(30 * s), color, theme.font_weight, alignment=TextAlignment.CENTER)

    panel = self._panel
    rl.draw_rectangle_rounded(panel, 0.06, 12, theme.surface)
    center_y = self._wheels[0].y + self._wheels[0].height / 2
    tint = rl.Color(theme.control.r, theme.control.g, theme.control.b, 38)
    rl.draw_rectangle_rounded(rl.Rectangle(panel.x + 24 * s, center_y - 46 * s, panel.width - 48 * s, 92 * s), 0.16, 8, tint)
    for x, width, title in ((self._wheels[0].x, 3 * self._wheels[0].width, tr('DATE')),
                            (self._wheels[3].x, 3 * self._wheels[3].width, tr('TIME')),
                            (self._wheels[6].x, self._wheels[6].width, tr('TIME ZONE'))):
      gui_label(rl.Rectangle(x, panel.y + 20 * s, width, 48 * s), title,
                round(26 * s), theme.secondary_text, theme.font_weight)
    for index, (bounds, title) in enumerate(zip(self._wheels, ('MONTH', 'DAY', 'YEAR', 'HOUR', 'MIN', 'AM/PM', 'REGION'), strict=True)):
      gui_label(rl.Rectangle(bounds.x, panel.y + 76 * s, bounds.width, 44 * s), tr(title),
                round(24 * s), theme.secondary_text, theme.font_weight, alignment=TextAlignment.CENTER)
      if self._keyboard_focus and self._focus == index:
        rl.draw_line_ex(rl.Vector2(bounds.x + 48 * s, panel.y + 122 * s),
                        rl.Vector2(bounds.x + bounds.width - 48 * s, panel.y + 122 * s), 3 * s, theme.control)
      rl.begin_scissor_mode(int(bounds.x), int(bounds.y), int(bounds.width), int(bounds.height))
      offset = self._offset if self._pressed == index or self._moving == index or self._pressed is None and self._focus == index else 0.0
      for row in range(-3, 4):
        distance = abs(row + offset / self._row_height)
        color = rl.Color(theme.text.r, theme.text.g, theme.text.b, round(255 * max(0.18, 1 - distance * 0.33)))
        size = 52 if distance < 0.5 else 42 if distance < 1.5 else 34
        if index == 6:
          size = min(size, 38)
        line = rl.Rectangle(bounds.x, center_y + row * self._row_height + offset - self._row_height / 2, bounds.width, self._row_height)
        gui_label(line, self._label_value(index, row), round(size * s), color, theme.font_weight, alignment=TextAlignment.CENTER)
      rl.end_scissor_mode()
    zone_time = self._selected().replace(tzinfo=ZoneInfo(self.timezone))
    if self._ambiguous():
      pressed = self.enabled and not self.saving and self._pressed == 'fold' and self.is_pressed
      rl.draw_rectangle_rounded(self._fold_button, 0.2, 8, theme.pressed if pressed else theme.control)
      if self._keyboard_focus and self._focus == len(self._wheels):
        rl.draw_rectangle_rounded_lines_ex(self._fold_button, 0.2, 8, 3 * s, theme.text)
      occurrence = tr('First occurrence') if self.fold == 0 else tr('Second occurrence')
      label = tr('Repeated hour:') + f' {occurrence} ({zone_time:%Z})  ·  ' + tr('Tap to change')
      gui_label(self._fold_button, label, round(25 * s), theme.background, theme.font_weight, alignment=TextAlignment.CENTER)
    offset = zone_time.strftime('%z')
    zone_label = f'{self.timezone.replace("_", " ")}  ·  UTC{offset[:3]}:{offset[3:]}'
    gui_label(rl.Rectangle(panel.x + panel.width - 720 * s, panel.y + 566 * s, 684 * s, 42 * s), zone_label,
              round(24 * s), theme.secondary_text, theme.font_weight, alignment=TextAlignment.RIGHT)
