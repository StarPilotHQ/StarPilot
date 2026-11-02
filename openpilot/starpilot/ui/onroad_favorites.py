"""Native Quick Select gestures and presentation over supplied owner snapshots."""

from typing import Any

import copy
from dataclasses import dataclass, replace
import math
from collections.abc import Callable

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.onroad_customization import CEM_GAUGE_FRAME, CEM_GAUGE_INSET, PROFILES, widget_size
from openpilot.starpilot.ui.onroad_favorites_geometry import COLUMN_BODY, ROW_BODY, RADIUS, contains, intersects, rounded_contains, shelf_geometry
from openpilot.starpilot.ui.onroad_state import AlertSize, manual_slc_confirmation
from openpilot.starpilot.ui.onroad_widget_style import roundness_for
from openpilot.starpilot.ui.presentation import FontRole, Profile

SHELF_COLORS = {name: rl.Color(*rgb, 255) for name, rgb in {
  'surface': (17, 22, 29), 'outline': (69, 82, 97), 'divider': (46, 58, 71),
  'ink': (241, 244, 248), 'secondary': (177, 190, 205), 'muted': (147, 162, 180),
  'positive': (111, 207, 155), 'caution': (226, 184, 134), 'remove': (245, 176, 178),
  'pressed': (36, 46, 59), 'editing': (47, 29, 35),
}.items()}


@dataclass(frozen=True)
class FavoriteRequest:
  kind: str
  index: int
  key: str | None
  revision: str


class OnroadFavorites:
  def __init__(self, fonts, emit: Callable[[FavoriteRequest], bool]):
    self.fonts = fonts
    self.profile = fonts.profile
    self.emit = emit
    self.data: dict[str, Any] = {"revision": "", "slots": [], "options": []}
    self.mode = "collapsed"
    self.selected = None
    self.editing = None
    self.page = 0
    self.press_target = None
    self.press_pos = None
    self.press_time = 0.0
    self.max_travel = 0.0
    self.last_interaction = 0.0
    self.last_activation = {}
    self.feedback = None
    self.feedback_text = ("", "")
    self.feedback_at = 0.0
    self.enabled = False
    self.reduce_motion = False
    self.progress = 0.0
    self.vertical = False
    self._motion_from = self._motion_to = self._motion_at = 0.0
    self._context = None
    self._document = None
    self._protected = ()
    self._fit_cache = {}
    self._label_cache = {}
    self._failed = None
    self._notice_until = 0.0
    self._corner_obstacles = ()
    self.rect = (30, 30, 1800, 1020) if self.profile == Profile.LARGE else (0, 0, 476, 240)

  @property
  def is_open(self):
    return self.mode != "collapsed"

  @property
  def scale(self):
    return max(0.35, min(self.rect[2] / 2160, self.rect[3] / 1080))

  def prepare(self):
    if self.profile == Profile.LARGE:
      self.fonts.horizontal_ink('Quick Select', FontRole.SEMI_BOLD, 28)
      self.fonts.horizontal_ink('×', FontRole.MEDIUM, 44)

  def update(self, state, data, now):
    if self.profile == Profile.LARGE:
      self.rect = (30, 30, state.viewport_width - 60, 1020)
      self._update_layout(state)
      self.reduce_motion = state.customization.get('reduceMotion', False)
    changed = data.get("revision") != self.data.get("revision")
    self.data = data
    camera = state.appearance.camera_view
    self.enabled = state.camera_available and state.alert.size == AlertSize.NONE
    if self.profile == Profile.LARGE:
      self.enabled = self.enabled and not manual_slc_confirmation(self.profile, state)
    if self.profile == Profile.COMPACT:
      self.enabled = self.enabled and (camera in (CameraViewChoice.NONE, CameraViewChoice.DRIVER) or
                                      not getattr(state, "reversing", False))
    if not self.enabled:
      self.cancel()
    elif changed:
      self.press_target, self.press_pos = None, None
      if self.profile == Profile.LARGE:
        self.editing = self.selected = None
        self.feedback = self._failed = None
        if self.mode == 'picker':
          self.mode = 'shelf'
    if self.is_open and self.mode != 'closing' and self.press_pos is None and now - self.last_interaction >= (30 if self.mode == "picker" else 6):
      self.dismiss(now)
    self._advance(now)
    if (self.press_target and self.press_target[0] == "slot" and self.profile == Profile.LARGE and
        self.mode == "shelf" and now - self.press_time >= 0.60 and self.configured(self.press_target[1])):
      self.editing = self.press_target[1]

  def _update_layout(self, state):
    context = state.viewport_width, state.drive_frame
    document = state.customization
    if context == self._context and document == self._document:
      return
    self.cancel()
    self._context, self._document = context, copy.deepcopy(document)
    bounds = {}
    for key, spec in PROFILES['large']['widgets'].items():
      saved = document['layouts']['large'].get(key)
      if saved is None or not saved['enabled'] or key in ('pip_left', 'pip_right', 'nav_map', 'torque_bar', 'speed_limit_actions'):
        continue
      x = saved['x'] + spec.get('anchorShift', 0) * (state.viewport_width - 1860)
      y = saved['y']
      width, height = widget_size(document, 'large', key)
      if key == 'cem_gauge':
        x, y = x - CEM_GAUGE_INSET[0], y - CEM_GAUGE_INSET[1]
        width, height = CEM_GAUGE_FRAME
      bounds[key] = x, y, width, height
    self._protected = tuple(bounds[key] for key in ('current_speed', 'cruise_limits') if key in bounds)
    self._corner_obstacles = tuple(bounds.values())

  def _advance(self, now):
    if self.mode not in ('shelf', 'closing'):
      return
    duration = (.20 if self._motion_to else .15) * abs(self._motion_to - self._motion_from)
    t = 1.0 if self.reduce_motion or duration == 0 else max(0., min(1., (now - self._motion_at) / duration))
    self.progress = self._motion_from + (self._motion_to - self._motion_from) * (1 - (1 - t) ** 4)
    if t == 1 and self.mode == 'closing':
      self.cancel()

  def _transition(self, target, now):
    self._advance(now)
    self._motion_from, self._motion_to, self._motion_at = self.progress, target, now
    self.mode = 'shelf' if target else 'closing'
    self._advance(now)

  def open(self, now):
    if self.profile != Profile.LARGE or not self.enabled:
      return False
    self._advance(now)
    if self.mode != 'closing':
      choices = [vertical for vertical, body in enumerate((ROW_BODY, COLUMN_BODY))
                 if not any(intersects(body, area) for area in self._protected)]
      if not choices:
        self._notice_until = now + 2
        return False
      self.vertical = bool(choices[0])
    self.selected = self.editing = None
    self.last_interaction = now
    self._transition(1., now)
    return True

  def dismiss(self, now):
    if self.profile == Profile.COMPACT:
      self.cancel()
    elif self.is_open and self.mode != 'closing':
      self.press_target = self.press_pos = None
      self.editing = self.selected = None
      self._transition(0., now)

  def present(self, state):
    if self.profile != Profile.LARGE or not self.is_open:
      return state
    return replace(state, quick_select_open=True)

  def claims_corner(self, x, y):
    if self.profile != Profile.LARGE or not self.enabled or self.mode != 'collapsed':
      return False
    cx, cy = self.rect[0] + 48 * self.scale, self.rect[1] + self.rect[3] - 48 * self.scale
    return contains((cx - 22 * self.scale, cy - 22 * self.scale, 44 * self.scale, 44 * self.scale), x, y)

  def cancel(self):
    self.mode = "collapsed"
    self.selected = None
    self.editing = None
    self.press_target = None
    self.press_pos = None
    self.max_travel = 0.0
    self.feedback = None
    self._failed = None
    self.progress = self._motion_from = self._motion_to = 0.0
    self._notice_until = 0.0
    self.vertical = False

  def slots(self):
    return self.data.get("slots", [])

  def configured(self, index):
    slots = self.slots()
    return index < len(slots) and bool(slots[index].get("enabled") and slots[index].get("show_onroad") and slots[index].get("key"))

  def corner(self):
    x, y, _, height = self.rect
    size = 180 * self.scale
    return x, y + height - size, 340, size

  def menu_buttons(self):
    return [(rect, (rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)) for rect in shelf_geometry(self.vertical)[1]]

  def picker_geometry(self):
    x, y, width, height = self.rect
    s = self.scale
    panel = (x + 76 * s, y + 66 * s, width - 152 * s, height - 132 * s)
    px, py, pw, ph = panel
    close = (px + pw - 186 * s, py, 150 * s, 152 * s)
    grid_w, grid_h = pw - 72 * s, ph - (152 + 168 + 36) * s
    cw, ch = (grid_w - 72 * s) / 4, (grid_h - 24 * s) / 2
    cells = [(px + 36 * s + col * (cw + 24 * s), py + 170 * s + row * (ch + 24 * s), cw, ch)
             for row in range(2) for col in range(4)]
    cy = py + ph - 168 * s + 36 * s
    nxt = (px + pw - 236 * s, cy, 200 * s, 96 * s)
    prev = (nxt[0] - 224 * s, cy, 200 * s, 96 * s)
    return panel, close, cells, prev, nxt

  @staticmethod
  def contains(rect, x, y):
    return rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]

  def target_at(self, x, y):
    if self.profile == Profile.COMPACT:
      if not self.contains(self.rect, x, y):
        return None
      index = min(2, int((x - self.rect[0]) / (self.rect[2] / 3)))
      return ("slot", index) if self.configured(index) else None
    if self.mode == "collapsed":
      return (("corner", None) if self.claims_corner(x, y) or self.contains(self.corner(), x, y) and
              not any(self.contains(area, x, y) for area in self._corner_obstacles) else None)
    if self.mode == "picker":
      _, close, cells, prev, nxt = self.picker_geometry()
      if self.contains(close, x, y):
        return "close", None
      for kind, rect in (("previous", prev), ("next", nxt)):
        if self.contains((rect[0] - 10 * self.scale, rect[1] - 28 * self.scale,
                          rect[2] + 20 * self.scale, rect[3] + 56 * self.scale), x, y):
          return kind, None
      for index, cell in enumerate(cells):
        option = self.page * 8 + index
        if option < len(self.data.get("options", [])) and self.contains(cell, x, y):
          return "option", option
      return None
    if self.mode == 'closing' or self.progress < 1:
      return None
    body, buttons, close = shelf_geometry(self.vertical)
    if not rounded_contains(body, x, y):
      return None
    for index, button in enumerate(buttons):
      if contains(button, x, y):
        return ('unassign' if self.editing == index else 'slot'), index
    if contains(close, x, y):
      return 'dismiss', None
    return None

  def press(self, x, y, now):
    if not self.enabled:
      return False
    if self.mode == 'closing' and self.contains(self.corner(), x, y):
      self.open(now)
      self.press_target, self.press_pos, self.press_time = ('reopen', None), (x, y), now
      return True
    self._advance(now)
    self.press_target = self.target_at(x, y)
    if self.press_target is None and not self.is_open:
      return False
    self.press_pos, self.press_time = (x, y), now
    self.max_travel = 0.0
    self.last_interaction = now
    if self.profile == Profile.COMPACT:
      self.feedback = None
    return True

  def move(self, x, y):
    if self.press_pos is not None:
      self.max_travel = max(self.max_travel, math.hypot(x - self.press_pos[0], y - self.press_pos[1]))

  def release(self, x, y, now):
    if self.press_pos is None:
      return False
    self.move(x, y)
    target, start = self.press_target, self.press_pos
    self.press_target, self.press_pos = None, None
    if not self.enabled:
      return True
    if self.mode == 'closing' or target == ('reopen', None):
      return True
    if target == ("corner", None):
      if self.target_at(*start) != target:
        return True
      dx, dy = x - start[0], y - start[1]
      if (math.hypot(dx, dy) <= 36 * self.scale or
          (dx >= 55 * self.scale and dy <= -55 * self.scale and 0.35 <= dx / max(1, abs(dy)) <= 2.8)):
        self.open(now)
      return True
    limit = 24 if self.profile == Profile.COMPACT else 36 * self.scale
    if self.max_travel > limit:
      self.feedback = None
      self.editing = None
      return True
    if self.profile == Profile.LARGE and target and target[0] == "slot" and now - self.press_time >= 0.60 and self.configured(target[1]):
      self.editing = target[1]
      self.last_interaction = now
      return True
    if target is None or target != self.target_at(x, y):
      if self.is_open:
        self.dismiss(now)
      self.feedback = None
      return True
    kind, index = target
    if kind == "slot":
      if self.editing is not None:
        self.editing = None
      elif self.configured(index):
        if self.profile == Profile.LARGE and not self.slots()[index].get('available', True):
          self.last_interaction = now
          return True
        if self.profile == Profile.COMPACT or now - self.last_activation.get(index, -math.inf) >= 0.28:
          self.last_activation[index] = now
          success = self._emit("activate", index, self.slots()[index]["key"])
          self.feedback, self.feedback_at = (index if success else None), now
          self._failed = None if success else index
          if success:
            slot = self.slots()[index]
            self.feedback_text = (slot.get("label") or slot["key"], slot.get("state_label", "Done"))
      else:
        if self.data.get('configurable', False):
          self.mode, self.selected, self.page = "picker", index, 0
    elif kind == "unassign":
      self._emit("unassign", index, None)
      self.editing = None
    elif kind == "close":
      self.mode, self.selected, self.page = "shelf", None, 0
    elif kind == 'dismiss':
      self.dismiss(now)
    elif kind == "previous":
      self.page = max(0, self.page - 1)
    elif kind == "next":
      if (self.page + 1) * 8 < len(self.data.get("options", [])):
        self.page += 1
    elif kind == "option":
      if self._emit("assign", self.selected, self.data["options"][index]["key"]) and self.enabled and self.is_open:
        self.mode, self.selected, self.page = "shelf", None, 0
    self.last_interaction = now
    return True

  def _emit(self, kind, index, key):
    if kind in ("assign", "unassign") and not self.data.get("configurable", False):
      return False
    return self.emit(FavoriteRequest(kind, index, key, self.data.get("revision", "")))

  def _text(self, text, size, x, y, color, *, role=FontRole.SEMI_BOLD):
    self.fonts.draw(text, role, max(10, int(size)), x, y, color)

  def _fit(self, text, size, width, role=FontRole.SEMI_BOLD):
    key = text, size, width, role
    if key in self._fit_cache:
      return self._fit_cache[key]
    original = text
    while text and self.fonts.measure(text, role, max(10, int(size))).width > width:
      text = text[:-1]
    if text != original:
      while text and self.fonts.measure(text + "...", role, max(10, int(size))).width > width:
        text = text[:-1]
      text += "..."
    if len(self._fit_cache) >= 64:
      self._fit_cache.clear()
    self._fit_cache[key] = text
    return text

  def _wrapped_label(self, text, size, width):
    lines = []
    current = ""
    for word in text.split():
      candidate = f"{current} {word}".strip()
      if current and self.fonts.measure(candidate, FontRole.SEMI_BOLD, size).width > width:
        lines.append(current)
        current = word
      else:
        current = candidate
    if current:
      lines.append(current)
    if len(lines) > 3:
      lines = lines[:2] + [" ".join(lines[2:])]
    return [self._fit(line, size, width) for line in lines]

  def render(self, now):
    if not self.enabled:
      return
    if self.profile == Profile.COMPACT:
      self._compact_feedback(now)
    elif self.mode in ('shelf', 'closing'):
      self._shelf(now)
    elif self.mode == "picker":
      self._picker()
    elif now < self._notice_until:
      self._blocked_notice()

  def _compact_feedback(self, now):
    if self.feedback is None or not self.configured(self.feedback):
      return
    held = self.press_target == ("slot", self.feedback)
    elapsed = max(0, now - self.feedback_at)
    alpha = 1 if held or elapsed < 2 else max(0, 3 - elapsed)
    if alpha == 0:
      self.feedback = None
      return
    width = self.rect[2] / 3
    panel = rl.Rectangle(self.rect[0] + self.feedback * width + 12, self.rect[1] + self.rect[3] / 4,
                         max(96, width - 24), min(132, self.rect[3] / 2))
    progress = 1 - (1 - min(1, elapsed / 0.65)) ** 3
    accent = rl.Color(round(188 + 67 * progress), round(132 + 123 * progress), 255, round(255 * alpha))
    for expansion, opacity in ((8, 22), (4, 44)):
      glow = rl.Rectangle(panel.x - expansion, panel.y - expansion,
                          panel.width + 2 * expansion, panel.height + 2 * expansion)
      rl.draw_rectangle_rounded_lines_ex(glow, 0.16, 12, 2,
                                         rl.Color(188, 132, 255, round(opacity * (1 - progress) * alpha)))
    rl.draw_rectangle_rounded(panel, 0.16, 12, rl.Color(0, 0, 0, round(178 * alpha)))
    rl.draw_rectangle_rounded_lines_ex(panel, 0.16, 12, 3, accent)
    label, value = self.feedback_text
    size = 18
    for candidate in range(30, 17, -1):
      if any(self.fonts.measure(word, FontRole.SEMI_BOLD, candidate).width > panel.width - 20 for word in label.split()):
        continue
      lines = self._wrapped_label(label, candidate, panel.width - 20)
      if len(lines) * candidate * 1.12 <= panel.height - 46:
        size = candidate
        break
    lines = self._wrapped_label(label, size, panel.width - 20)
    text_y = panel.y + 12 + (panel.height - 42 - len(lines) * size * 1.08) / 2
    for index, label in enumerate(lines):
      measured = self.fonts.measure(label, FontRole.SEMI_BOLD, size)
      self._text(label, size, panel.x + (panel.width - measured.width) / 2, text_y + index * size * 1.08, accent)
    value = value.upper()
    measured = self.fonts.measure(value, FontRole.SEMI_BOLD, 20)
    self._text(value, 20, panel.x + (panel.width - measured.width) / 2, panel.y + panel.height - 30,
               rl.Color(255, 255, 255, round(210 * alpha)))

  def _ink(self, text, size, x, top, color, role=FontRole.SEMI_BOLD):
    ink_top, _ = self.fonts.vertical_ink(text, role, size)
    ink_left, _ = self.fonts.horizontal_ink(text, role, size)
    self.fonts.draw(text, role, size, x - ink_left, top - ink_top, color)

  def _label(self, text):
    if text not in self._label_cache:
      lines = self._wrapped_label(text, 28, 144)
      if len(lines) > 2:
        lines = [lines[0], self._fit(' '.join(lines[1:]), 28, 144)]
      if len(self._label_cache) >= 32:
        self._label_cache.clear()
      self._label_cache[text] = tuple(lines)
    return self._label_cache[text]

  def _color(self, name):
    color = SHELF_COLORS[name]
    return color if self.progress == 1 else rl.color_alpha(color, self.progress)

  def _shelf(self, now):
    if self.progress <= 0:
      return
    (x, y, width, height), buttons, _ = shelf_geometry(self.vertical)
    lift = round(12 * (1 - self.progress))
    body = rl.Rectangle(x, y + lift, width, height)
    rl.draw_rectangle_rounded(body, roundness_for(body, RADIUS), 12, self._color('surface'))
    rl.draw_rectangle_rounded_lines_ex(body, roundness_for(body, RADIUS), 12, 2, self._color('outline'))
    for index, (bx, by, bw, bh) in enumerate(buttons):
      by += lift
      if index:
        a, b = (((bx + 16, by - 4), (bx + bw - 16, by - 4)) if self.vertical else
                ((bx - 4, by + 20), (bx - 4, by + bh - 20)))
        rl.draw_line_ex(rl.Vector2(*a), rl.Vector2(*b), 1, self._color('divider'))
      configured = self.configured(index)
      slot = self.slots()[index] if index < len(self.slots()) else {}
      available = slot.get('available', True) if configured else self.data.get('configurable', False)
      editing = self.editing == index
      if editing or self.press_target == ('slot', index):
        fill = rl.Rectangle(bx + 4, by + 4, bw - 8, bh - 8)
        rl.draw_rectangle_rounded(fill, roundness_for(fill, 18), 10, self._color('editing' if editing else 'pressed'))
      label = ('Remove shortcut' if editing else (slot.get('label') or slot.get('key') or '') if configured else 'Add shortcut')
      value = 'Tap again' if editing else slot.get('state_label', 'Unavailable') if configured else 'Configure'
      if not available:
        value = 'Unavailable'
      elif self._failed == index and now - self.feedback_at < 2:
        value = 'Try again'
      elif self.feedback == index and slot.get('kind') == 'action' and now - self.feedback_at < 1.5:
        value = 'Done'
      title_color = self._color('remove' if editing else 'ink' if available else 'muted')
      for line, text in enumerate(self._label(label)):
        self._ink(text, 28, bx + 16, by + 36 + line * 36, title_color)
      on = configured and slot.get('kind') == 'toggle' and value == 'On'
      if on:
        rl.draw_circle(round(bx + 4), round(by + 144), 4, self._color('positive'))
      color = self._color('remove' if editing else 'caution' if value in ('Unavailable', 'Try again') else 'secondary')
      self._ink(self._fit(value, 22, 144, FontRole.MEDIUM), 22, bx + 16, by + 136, color, FontRole.MEDIUM)
    cx, cy = self.rect[0] + 48 * self.scale, self.rect[1] + self.rect[3] - 48 * self.scale
    left, right = self.fonts.horizontal_ink('×', FontRole.MEDIUM, 44)
    top, bottom = self.fonts.vertical_ink('×', FontRole.MEDIUM, 44)
    self.fonts.draw('×', FontRole.MEDIUM, 44, cx - (left + right) / 2, cy - (top + bottom) / 2, self._color('secondary'))

  def _blocked_notice(self):
    width = self.rect[2] + 60
    for box in ((126, 958, 400, 72), (width / 2 - 200, 344, 400, 72)):
      if any(intersects(box, area) for area in self._protected):
        continue
      rect = rl.Rectangle(*box)
      rl.draw_rectangle_rounded(rect, roundness_for(rect, 16), 10, SHELF_COLORS['surface'])
      self._ink('Quick Select needs space', 24, box[0] + 16, box[1] + 12, SHELF_COLORS['caution'])
      self._ink('Adjust speed widgets in Layout', 22, box[0] + 16, box[1] + 42, SHELF_COLORS['secondary'], FontRole.MEDIUM)
      break

  def _picker(self):
    assert self.selected is not None
    s = self.scale
    panel, close, cells, prev, nxt = self.picker_geometry()
    rl.draw_rectangle_rec(rl.Rectangle(*self.rect), rl.Color(4, 4, 10, 188))
    rect = rl.Rectangle(*panel)
    rl.draw_rectangle_rounded(rect, min(1, 68 * s / min(rect.width, rect.height)), 16, rl.Color(13, 11, 23, 236))
    rl.draw_rectangle_rounded_lines_ex(rect, 0.08, 16, 2 * s, rl.Color(214, 192, 255, 166))
    self._text(f"Assign Favorite {self.selected + 1}", 50 * s, panel[0] + 36 * s, panel[1] + 26 * s, rl.WHITE)
    self._text("×", 50 * s, close[0] + 50 * s, close[1] + 38 * s, rl.WHITE)
    options = self.data.get("options", [])
    for index, cell in enumerate(cells):
      option = self.page * 8 + index
      if option >= len(options):
        break
      r = rl.Rectangle(*cell)
      rl.draw_rectangle_rounded(r, 0.1, 16, rl.Color(24, 18, 40, 255))
      rl.draw_rectangle_rounded_lines_ex(r, 0.1, 16, 2 * s, rl.Color(161, 112, 255, 100))
      self._text(self._fit(options[option]["label"], 32 * s, r.width - 32 * s), 32 * s, r.x + 16 * s, r.y + 24 * s, rl.WHITE)
    for label, rect in (("Previous", prev), ("Next", nxt)):
      rl.draw_rectangle_rounded(rl.Rectangle(*rect), 0.18, 16, rl.Color(42, 28, 64, 255))
      self._text(label, 28 * s, rect[0] + 20 * s, rect[1] + 28 * s, rl.WHITE)
