"""Immutable saved-feature settings presentation and guarded user intent."""

from dataclasses import dataclass, replace
from enum import StrEnum
import math
from collections import deque
from collections.abc import Callable

FEATURE_HEADER_HEIGHT = 88
FEATURE_BACK_WIDTH = 208
FEATURE_ROW_TOP = 112
FEATURE_ROW_HEIGHT = 154
FEATURE_VISIBLE_ROWS = 5
FEATURE_CONTROL_LEFT = 1748
FEATURE_CONTROL_RIGHT = 2118
FEATURE_BUTTON_TOP = 64
FEATURE_BUTTON_HEIGHT = 80
FEATURE_ACTION_MARGIN = 36
FEATURE_PAGE_COUNTER_WIDTH = 160
FEATURE_FOOTER_CENTER = 1015
FEATURE_FOOTER_BOTTOM = 1080
FEATURE_FOOTER_BUTTON_WIDTH = 540
FEATURE_FOOTER_BUTTON_HEIGHT = 84
FEATURE_TAP_SLOP = 36
FEATURE_SWIPE_DISTANCE = 120
FEATURE_FLICK_DISTANCE = 60
FEATURE_FLICK_VELOCITY = 800
FEATURE_FLICK_WINDOW = 0.1
FEATURE_FLICK_PAUSE = 0.06
TOOLBAR_CHIP_HEIGHT = FEATURE_ROW_HEIGHT - 2 * 36
TOOLBAR_LABEL_WIDTH = 110
TOOLBAR_SLOT_GAP = 40

SOUND_PRESETS = ("0", "10", "25", "50", "75", "100", "Auto")
SYSTEM_PRESETS = {
  "ScreenBrightness": tuple((str(value), f"{value}%") for value in (10, 25, 50, 75, 100)) + (("Auto", "Auto"),),
  "ScreenTimeout": tuple((str(value), f"{value} s") for value in (5, 10, 15, 30, 60)),
  "power:hours": tuple((f"{value} h", f"{value} h") for value in (1, 3, 6, 12, 24, 30)),
  "power:volts": tuple((f"{value} V", f"{value} V") for value in ("11.8", "12.0", "12.2", "12.5")),
}
SYSTEM_PRESETS["ScreenBrightnessOnroad"] = SYSTEM_PRESETS["ScreenBrightness"]
SYSTEM_PRESETS["ScreenTimeoutOnroad"] = SYSTEM_PRESETS["ScreenTimeout"]


def is_long_confirm_action(key: str) -> bool:
  return key.startswith(("long_repair:", "long_reset:"))


class FeaturePage(StrEnum):
  HUB = "hub"
  VEHICLE = "vehicle"
  SLC = "slc"
  LANE = "lane"
  LANE_CHANGE = "lane_change"
  PROFILES = "profiles"
  PERSONALITIES = "personalities"
  AGGRESSIVE = "aggressive"
  STANDARD = "standard"
  RELAXED = "relaxed"
  TRAFFIC = "traffic"
  CURVE = "curve"
  TORQUE = "torque"
  TUNING = "tuning"
  BLUEPILOT = "torque/bluepilot"
  AOL = "aol"
  WHEEL = "wheel"
  CONDITIONAL = "conditional"
  CONDITIONAL_CEM = "conditional/cem"
  CONDITIONAL_CCM = "conditional/ccm"


TORQUE_CONFIRM_ACTIONS = frozenset(("torque_adopt", "torque_reset", "torque_reset_profile", "torque_rebase",
                                  "torque_prepare_firestar", "torque_gain_rebase", "torque:surface:reset"))
SLC_CONFIRM_ACTIONS = frozenset(("slc_reset",))
SLC_OFFSET_KEYS = tuple(f"slc:offset:{index}" for index in range(1, 8))
LANE_CHANGE_CONFIRM_ACTIONS = frozenset(("lane_change:reset",))
CURVE_CONFIRM_ACTIONS = frozenset(("curve_reset",))
CONDITIONAL_CONFIRM_ACTIONS = frozenset(("conditional:reset", "conditional:manual_reset"))
FEATURE_CONFIRM_ACTIONS = (TORQUE_CONFIRM_ACTIONS | SLC_CONFIRM_ACTIONS | LANE_CHANGE_CONFIRM_ACTIONS |
                           CURVE_CONFIRM_ACTIONS | CONDITIONAL_CONFIRM_ACTIONS | {"reset_profiles", "pip:reset", "sentry:reset"})


HEADING_PREFIX = "heading:"


def is_heading(key: str) -> bool:
  """A section title inside a page: shown like a row but never a control, and never a change request."""
  return key.startswith(HEADING_PREFIX)


@dataclass(frozen=True)
class FeatureRow:
  key: str
  label: str
  value: str
  source: bytes | None = None
  choices: tuple[str, ...] = ()
  step: float = 0.0
  minimum: float = 0.0
  maximum: float = 0.0
  unit: str = ""
  available: bool = False
  reason: str = ""
  page: str = ""
  related_source: bytes | None = None
  vehicle_fingerprint: str | None = None
  capability: tuple | None = None
  dependencies: tuple[tuple[str, bytes | None], ...] = ()
  display_unit: str = ""
  repair_value: str = ""
  default_value: str | None = None
  default_key: str = ""
  actions: tuple[tuple[str, bool], ...] = ()
  presets: tuple[tuple[str, str], ...] = ()
  default_reset_available: bool = False
  collapsed: bool = False  # a heading whose rows are hidden until it is opened


@dataclass(frozen=True)
class FeatureSettingsState:
  page: str = FeaturePage.HUB
  title: str = "Driving Controls"
  subtitle: str = ""
  rows: tuple[FeatureRow, ...] = ()
  parked: bool = False
  scroll: int = 0
  sidebar_expanded: bool = True
  parent_title: str = "StarPilot"
  editor: bool = False
  save_hint: str = "Changes save automatically"
  subtitle_accent: str = ""
  subtitle_tone: str = ""
  # Pinned (title, current value) chips; a tap emits FeatureUiAction("toolbar", direction=index).
  toolbar: tuple[tuple[str, str], ...] = ()


def feature_body_top(state: FeatureSettingsState) -> int:
  return FEATURE_ROW_TOP + (52 if state.subtitle else 0)


def feature_row_top(state: FeatureSettingsState) -> int:
  return feature_body_top(state) + (FEATURE_ROW_HEIGHT if state.toolbar else 0)


def feature_page_rows(state: FeatureSettingsState) -> int:
  return FEATURE_VISIBLE_ROWS - (1 if state.toolbar else 0)


def toolbar_rects(state: FeatureSettingsState) -> tuple[tuple[str, str, tuple[float, float, float, float]], ...]:
  if not state.toolbar:
    return ()
  left = (520 if state.sidebar_expanded else 20) + 55
  slot = (FEATURE_CONTROL_RIGHT - left + TOOLBAR_SLOT_GAP) / len(state.toolbar)
  y = feature_body_top(state) + (FEATURE_ROW_HEIGHT - TOOLBAR_CHIP_HEIGHT) / 2
  return tuple((title, value, (left + index * slot + TOOLBAR_LABEL_WIDTH, y, slot - TOOLBAR_SLOT_GAP - TOOLBAR_LABEL_WIDTH,
                               TOOLBAR_CHIP_HEIGHT)) for index, (title, value) in enumerate(state.toolbar))


def value_editor_rect(state: FeatureSettingsState) -> tuple[float, float, float, float]:
  left = (520 if state.sidebar_expanded else 20) + 55
  top = feature_row_top(state) + 24
  return left, top, 2094 - left, 1020 - top


def value_done_rect(state: FeatureSettingsState) -> tuple[float, float, float, float]:
  x, y, width, height = value_editor_rect(state)
  return x, y + height - 140, width, 140


def value_buttons(state: FeatureSettingsState, row: FeatureRow, y: float = 0) -> tuple[tuple[str, tuple[float, float, float, float], bool], ...]:
  """Shared value-editor geometry; limits and choices come from the saved row."""
  if not state.editor or len(state.rows) != 1 or not row.presets or row.repair_value:
    return ()
  left, _, editor_width, _ = value_editor_rect(state)
  step_width, gap, height = 200, 24, 200
  presets = tuple(value for value, _ in row.presets)
  width = (editor_width - (len(presets) - 1) * gap) / len(presets)
  if row.step:
    try:
      current = float(row.value)
    except ValueError:
      current = None
    minus = current is not None and current > row.minimum
    plus = current is not None and current < row.maximum
  else:
    index = row.choices.index(row.value) if row.value in row.choices else -1
    minus, plus = index > 0, 0 <= index < len(row.choices) - 1
  buttons = [("-", (left, y + 110, step_width, height),
              row.available and minus)]
  buttons.extend((value, (left + slot * (width + gap), y + 356, width, height),
                  row.available and row.value != value)
                 for slot, value in enumerate(presets))
  buttons.append(("+", (left + editor_width - step_width, y + 110, step_width, height),
                  row.available and plus))
  return tuple(buttons)


def lane_strength_level(row: FeatureRow) -> float | None:
  if row.key != "LaneCenteringStrength" or row.unit != "%" or not row.minimum < 100 < row.maximum:
    return None
  try:
    raw = float(row.value)
  except ValueError:
    return None
  if not math.isfinite(raw) or not row.minimum <= raw <= row.maximum:
    return None
  return round(1 + (raw - row.minimum) * 4 / (100 - row.minimum) if raw <= 100 else
               5 + (raw - 100) * 5 / (row.maximum - 100), 2)


def value_reason(row: FeatureRow) -> str:
  if row.available and lane_strength_level(row) is not None:
    return "1–10; 5 is normal. Lower values reduce centering and higher values strengthen it"
  return row.reason


def value_text(row: FeatureRow) -> str:
  level = lane_strength_level(row)
  if level is not None:
    return f"{level:g}"
  label = dict(row.presets).get(row.value)
  if label is not None:
    return "Muted" if label == "Mute" else label
  return row.value + (("" if row.unit == "%" else " ") + row.unit if row.unit and row.value != "Auto" else "")


def value_editor_state(state: FeatureSettingsState, key: str | None) -> FeatureSettingsState:
  """Reuse the large value editor without changing saved-setting owners or compact rows."""
  rows = []
  for row in state.rows:
    if row.key and not (row.page or row.presets or row.actions or row.repair_value or boolean_value(row) is not None):
      presets = ()
      if row.step:
        try:
          current = float(row.value)
          numbers = {row.minimum, row.maximum}
          if row.default_value is not None and not row.default_key:
            numbers.add(float(row.default_value))
          if not all(math.isfinite(value) for value in (*numbers, current)) or row.minimum >= row.maximum:
            rows.append(row)
            continue
          if row.key == "lane_change:speed":
            numbers = set((10., 30., 50., 70., 100.) if row.unit == "km/h" else (5., 15., 30., 45., 60.))
          presets = tuple((row.value if number == current else str(number),
                           value_text(replace(row, value=f"{number:g}", presets=())))
                          for number in sorted(numbers) if row.minimum <= number <= row.maximum)
        except ValueError:
          pass
      elif len(row.choices) > 1 and row.value in row.choices:
        choices = row.choices if len(row.choices) <= 3 else tuple(dict.fromkeys((
          row.choices[0], row.default_value if row.default_value in row.choices else row.choices[len(row.choices) // 2], row.choices[-1])))
        presets = tuple((value, value) for value in choices)
      if presets:
        row = replace(row, presets=presets, page=row.key)
    rows.append(row)
  selected = next((row for row in rows if row.key == key and row.presets), None)
  if selected is not None:
    return replace(state, title=selected.label, subtitle=value_reason(selected), rows=(replace(selected, page=""),),
                   parent_title=state.title, scroll=0, editor=True)
  return replace(state, rows=tuple(rows))


def sound_editor_state(state: FeatureSettingsState, key: str | None) -> FeatureSettingsState:
  rows = tuple(replace(row, presets=tuple(
    (value, "Mute" if value == "0" else value if value == "Auto" else value + "%")
    for value in SOUND_PRESETS if value == "Auto" or row.minimum <= int(value) <= row.maximum))
    if row.unit == "%" else row for row in state.rows)
  rows = value_editor_state(replace(state, rows=rows), None).rows
  if key is not None:
    row = next(row for row in rows if row.key == key)
    return replace(state, title=row.label, subtitle=row.reason, rows=(row,), parent_title="Sounds & Alerts", editor=True)
  return replace(state, page="sounds:overview", rows=tuple(replace(row, page=row.key) for row in rows), subtitle="")


def feature_page_counter_left(state: FeatureSettingsState) -> float:
  left = 520 if state.sidebar_expanded else 20
  return (left + 25 + 2120 - FEATURE_PAGE_COUNTER_WIDTH) / 2


def feature_footer_top(state: FeatureSettingsState) -> float:
  return feature_body_top(state) + FEATURE_VISIBLE_ROWS * FEATURE_ROW_HEIGHT


def feature_footer_buttons(state: FeatureSettingsState) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
  left = 520 if state.sidebar_expanded else 20
  y = FEATURE_FOOTER_CENTER - FEATURE_FOOTER_BUTTON_HEIGHT / 2
  buttons = tuple((center - FEATURE_FOOTER_BUTTON_WIDTH / 2, y, FEATURE_FOOTER_BUTTON_WIDTH, FEATURE_FOOTER_BUTTON_HEIGHT)
                  for center in ((left + 25 + 1320) / 2, (1320 + 2120) / 2))
  return buttons[0], buttons[1]


def feature_footer_target(x: float, y: float, state: FeatureSettingsState) -> int | None:
  left = 520 if state.sidebar_expanded else 20
  if state.editor or not feature_footer_top(state) <= y <= FEATURE_FOOTER_BOTTOM:
    return None
  counter_left = feature_page_counter_left(state)
  if left <= x < counter_left:
    return -1
  if counter_left + FEATURE_PAGE_COUNTER_WIDTH < x <= 2140:
    return 1
  return None


def feature_parent_page(page: str) -> str | None:
  if page == FeaturePage.HUB:
    return None
  if "/" in page:
    return page.split("/", maxsplit=1)[0]
  if page in (FeaturePage.AGGRESSIVE, FeaturePage.STANDARD, FeaturePage.RELAXED, FeaturePage.TRAFFIC):
    return FeaturePage.PROFILES
  return FeaturePage.HUB


def feature_parent_title(page: str) -> str:
  parent = feature_parent_page(page)
  return {None: "StarPilot", FeaturePage.HUB: "Driving Controls", FeaturePage.PROFILES: "Long Planner",
          FeaturePage.CONDITIONAL: "Conditional Driving Modes", FeaturePage.SLC: "Speed Limit Controller",
          FeaturePage.TORQUE: "Steering and Torque"}.get(parent, (parent or "").replace("_", " ").title())


def boolean_value(row: FeatureRow) -> bool | None:
  if (not row.key or row.actions or row.repair_value or row.step or row.key in FEATURE_CONFIRM_ACTIONS or
      row.key.startswith("pip:format:") or is_long_confirm_action(row.key) or row.choices != ("Off", "On")):
    return None
  if row.value in ("Off", "On"):
    return row.value == "On"
  return None


def feature_action_left(row: FeatureRow) -> float:
  if row.page and boolean_value(row) is not None:
    return FEATURE_CONTROL_LEFT - 280
  return feature_action_rects(row, 0)[0][0] if row.actions else FEATURE_CONTROL_LEFT


def feature_action_rects(row: FeatureRow, y: float) -> tuple[tuple[float, float, float, float], ...]:
  width = (300 if row.key == "drive_state" else 240 if row.key.startswith("model:") or row.key == "models:active" else
           FEATURE_CONTROL_RIGHT - FEATURE_CONTROL_LEFT)
  margin = 14 if row.key == "drive_state" else FEATURE_ACTION_MARGIN
  left = FEATURE_CONTROL_RIGHT - len(row.actions) * width - max(0, len(row.actions) - 1) * 16
  return tuple((left + index * (width + 16), y + margin, width, FEATURE_ROW_HEIGHT - 2 * margin)
               for index in range(len(row.actions)))


def feature_scroll(scroll: int, direction: int, row_count: int, page: int = FEATURE_VISIBLE_ROWS) -> int:
  last_page = max(0, row_count - 1) // page * page
  return max(0, min(last_page, scroll + direction * page))


@dataclass(frozen=True)
class FeatureSettingsRequest:
  key: str
  expected: bytes | None
  value: str
  confirmation: bool = False
  related_source: bytes | None = None
  vehicle_fingerprint: str | None = None
  capability: tuple | None = None
  dependencies: tuple[tuple[str, bytes | None], ...] = ()
  display_unit: str = ""
  direction: int = 0


@dataclass(frozen=True)
class FeatureUiAction:
  kind: str
  row: FeatureRow | None = None
  direction: int = 1


class FeatureInput:
  """One touch: movement cancels its tap; a horizontal swipe pages on release."""

  def __init__(self, emit: Callable[[FeatureUiAction], None]):
    self.emit = emit
    self.held: tuple[float, float, FeatureUiAction | None, int, str, bool] | None = None
    self._released: tuple[float, float, FeatureUiAction | None, int, str, bool] | None = None
    self._samples: deque[tuple[float, float]] = deque(maxlen=16)
    self._drag_x = 0.0

  def pressed_action(self, state: FeatureSettingsState) -> FeatureUiAction | None:
    """Keep taps completed between renders visible for one frame."""
    held = self.held or self._released
    self._released = None
    if held is not None:
      x, y, action, scroll, page, sidebar = held
      if ((scroll, page, sidebar) == (state.scroll, state.page, state.sidebar_expanded) and
          self.target(x, y, state) == action):
        return action
    return None

  @property
  def drag_x(self) -> float:
    return self._drag_x

  @staticmethod
  def _in_body(x: float, y: float, state: FeatureSettingsState, *, include_sidebar: bool = False) -> bool:
    left = (520 if state.sidebar_expanded else 20) + 25
    if include_sidebar and state.sidebar_expanded:
      left = 0
    return left <= x <= 2125 and feature_row_top(state) <= y < feature_row_top(state) + feature_page_rows(state) * FEATURE_ROW_HEIGHT

  @staticmethod
  def target(x: float, y: float, state: FeatureSettingsState) -> FeatureUiAction | None:
    left = 520 if state.sidebar_expanded else 20
    row_top = feature_row_top(state)
    if not left <= x <= 2140:
      return None
    if 12 <= y <= 12 + FEATURE_HEADER_HEIGHT:
      return FeatureUiAction("back") if not state.sidebar_expanded and x < left + FEATURE_BACK_WIDTH else None
    if state.toolbar and not state.editor and feature_body_top(state) <= y < row_top:
      for index, (_, _, (bx, by, width, height)) in enumerate(toolbar_rects(state)):
        if bx <= x <= bx + width and by <= y <= by + height:
          return FeatureUiAction("toolbar", direction=index)
      return None
    if 12 + FEATURE_HEADER_HEIGHT < y < feature_body_top(state):
      return FeatureUiAction("details") if state.subtitle else None
    if state.editor:
      bx, by, width, height = value_done_rect(state)
      if bx <= x <= bx + width and by <= y <= by + height:
        return FeatureUiAction("back")
    if not state.editor and y >= feature_footer_top(state):
      direction = feature_footer_target(x, y, state)
      return FeatureUiAction("scroll", direction=direction) if direction is not None else None
    if not row_top <= y < row_top + feature_page_rows(state) * FEATURE_ROW_HEIGHT:
      return None
    if len(state.rows) == 1 and (controls := value_buttons(state, state.rows[0], row_top)):
      row = state.rows[0]
      for button, (value, (bx, by, width, height), enabled) in enumerate(controls):
        if bx <= x <= bx + width and by <= y <= by + height:
          if not enabled:
            return None
          return FeatureUiAction("change", row, -1 if value == "-" else 1) if value in ("-", "+") else FeatureUiAction("action", row, button)
      return FeatureUiAction("details", row) if controls[0][1][1] <= y <= controls[0][1][1] + controls[0][1][3] else None
    visible = int((y - row_top) // FEATURE_ROW_HEIGHT)
    index = state.scroll + visible
    if 0 <= index < len(state.rows):
      row = state.rows[index]
      if row.actions:
        for action, (bx, by, width, height) in enumerate(feature_action_rects(row, row_top + visible * FEATURE_ROW_HEIGHT)):
          if bx <= x <= bx + width and by <= y <= by + height:
            return FeatureUiAction("action", row, action) if row.available and row.actions[action][1] else None
        return FeatureUiAction("details", row) if x < feature_action_left(row) else None
      if row.page and row.choices == ("Off", "On"):
        if boolean_value(row) is None or x < FEATURE_CONTROL_LEFT - 24:
          return FeatureUiAction("open", row)
        local_y = y - row_top - visible * FEATURE_ROW_HEIGHT
        if row.available and FEATURE_CONTROL_LEFT <= x <= FEATURE_CONTROL_RIGHT and 18 <= local_y <= FEATURE_ROW_HEIGHT - 18:
          return FeatureUiAction("change", row, -1 if x < 1930 else 1)
        return None
      if row.page and row.available:
        return FeatureUiAction("open", row)
      if FEATURE_CONTROL_LEFT <= x <= FEATURE_CONTROL_RIGHT:
        if not row.available:
          return None
        local_y = y - row_top - visible * FEATURE_ROW_HEIGHT
        if row.key in FEATURE_CONFIRM_ACTIONS or row.key.startswith("pip:format:") or is_long_confirm_action(row.key):
          return FeatureUiAction("reset", row) if FEATURE_ACTION_MARGIN <= local_y <= FEATURE_ROW_HEIGHT - FEATURE_ACTION_MARGIN else None
        if row.repair_value:
          return FeatureUiAction("change", row) if FEATURE_ACTION_MARGIN <= local_y <= FEATURE_ROW_HEIGHT - FEATURE_ACTION_MARGIN else None
        if boolean_value(row) is not None:
          return FeatureUiAction("change", row, -1 if x < 1930 else 1) if 18 <= local_y <= FEATURE_ROW_HEIGHT - 18 else None
        if row.key and FEATURE_BUTTON_TOP <= local_y <= FEATURE_BUTTON_TOP + FEATURE_BUTTON_HEIGHT:
          if 1760 <= x <= 1915 or 1940 <= x <= 2095:
            return FeatureUiAction("change", row, -1 if x < 1930 else 1)
      if row.key or row.value or row.reason:
        return FeatureUiAction("details", row)
    return None

  def press(self, x: float, y: float, state: FeatureSettingsState) -> None:
    self.cancel()
    target = self.target(x, y, state)
    self.held = ((x, y, target, state.scroll, state.page, state.sidebar_expanded)
                 if target is not None or self._in_body(x, y, state, include_sidebar=True) else None)

  def cancel_tap(self) -> None:
    """Suppress the held control action while preserving the pagination gesture."""
    if self.held is not None:
      x, y, _, scroll, page, sidebar = self.held
      self.held = (x, y, None, scroll, page, sidebar)

  def move(self, x: float, y: float, state: FeatureSettingsState, now: float | None = None) -> None:
    if self.held is not None:
      px, py, action, scroll, page, sidebar = self.held
      if state.scroll != scroll or state.page != page or state.sidebar_expanded != sidebar:
        self.cancel()
        return
      dx, dy = abs(x - px), abs(y - py)
      moved = max(dx, dy) > FEATURE_TAP_SLOP
      body = self._in_body(px, py, state, include_sidebar=True)
      # Keep horizontal tracking across the sidebar boundary.
      if body and (not self._in_body(px, y, state, include_sidebar=True) or (moved and dy > dx)):
        self.cancel()
        return
      if action is not None and (moved or self.target(x, y, state) != action):
        self.held = (px, py, None, scroll, page, sidebar) if body else None
      signed_dx = x - px
      can_drag = body and (self._in_body(px, py, state) or self._in_body(x, y, state))
      self._drag_x = signed_dx if can_drag and dx > FEATURE_TAP_SLOP and dx >= 2 * dy else 0.0
      if body and now is not None:
        if self._samples and now <= self._samples[-1][0]:
          self._samples.clear()
        self._samples.append((now, x))
        while now - self._samples[0][0] > FEATURE_FLICK_WINDOW:
          self._samples.popleft()

  def _is_flick(self, px: float, dx: float, now: float | None) -> bool:
    samples = self._samples
    if (now is None or len(samples) < 3 or not 0 <= now - samples[-1][0] <= FEATURE_FLICK_PAUSE or
        samples[-1][0] - samples[0][0] < 0.01):
      return False
    travel = samples[-1][1] - px
    reversal = samples[-1][1] - min(x for _, x in samples) if dx < 0 else max(x for _, x in samples) - samples[-1][1]
    if abs(travel) < FEATURE_FLICK_DISTANCE or travel * dx <= 0 or reversal > FEATURE_TAP_SLOP:
      return False
    # Fit recent movement to reduce polling jitter; release positions are never sampled.
    mean_t = sum(t for t, _ in samples) / len(samples)
    mean_x = sum(x for _, x in samples) / len(samples)
    velocity = sum((t - mean_t) * (x - mean_x) for t, x in samples) / sum((t - mean_t) ** 2 for t, _ in samples)
    return abs(velocity) >= FEATURE_FLICK_VELOCITY and velocity * dx > 0

  def release(self, x: float, y: float, state: FeatureSettingsState, now: float | None = None) -> None:
    self.move(x, y, state)
    held = self.held
    action = None
    if self.held is not None:
      px, py, action, *_ = self.held
      dx, dy = x - px, abs(y - py)
      direction = 1 if dx < 0 else -1
      swipe = abs(dx) >= FEATURE_SWIPE_DISTANCE or (abs(dx) >= FEATURE_FLICK_DISTANCE and self._is_flick(px, dx, now))
      if (self._in_body(px, py, state, include_sidebar=True) and
          (self._in_body(px, py, state) or self._in_body(x, y, state)) and swipe and abs(dx) >= 2 * dy and
          feature_scroll(state.scroll, direction, len(state.rows), feature_page_rows(state)) != state.scroll):
        action = FeatureUiAction("scroll", direction=direction)
    self.cancel()
    if action is not None:
      self._released = held
      self.emit(action)

  def cancel(self) -> None:
    self.held = None
    self._released = None
    self._samples.clear()
    self._drag_x = 0.0


def row_request(row: FeatureRow, value: str) -> FeatureSettingsRequest:
  return FeatureSettingsRequest(row.key, row.source, value, related_source=row.related_source,
                                vehicle_fingerprint=row.vehicle_fingerprint, capability=row.capability,
                                dependencies=row.dependencies, display_unit=row.display_unit)


def value_request(state: FeatureSettingsState, action: FeatureUiAction) -> FeatureSettingsRequest | None:
  row = action.row
  if row is None or row not in state.rows or not row.available or (row.presets and not state.editor):
    return None
  buttons = value_buttons(state, row)
  if action.kind == "action":
    if not 0 <= action.direction < len(buttons):
      return None
    value, _, enabled = buttons[action.direction]
    return row_request(row, value) if enabled and value not in ("-", "+") else None
  if action.kind == "change":
    if buttons and (action.direction not in (-1, 1) or not any(
        value == ("-" if action.direction == -1 else "+") and enabled for value, _, enabled in buttons)):
      return None
    return row_change(row, action.direction)
  return None


def row_change(row: FeatureRow, direction: int = 1) -> FeatureSettingsRequest | None:
  """Bind a press to the displayed source, never a later refreshed value."""
  if not row.available:
    return None
  if row.key in SLC_OFFSET_KEYS:
    if direction not in (-1, 1):
      return None
    return FeatureSettingsRequest(row.key, row.source, "", related_source=row.related_source,
                                  vehicle_fingerprint=row.vehicle_fingerprint, capability=row.capability,
                                  dependencies=row.dependencies, display_unit=row.display_unit, direction=direction)
  if row.repair_value:
    value = row.repair_value
  elif row.choices and not (row.choices == ("Auto",) and row.step and row.value != "Auto"):
    if row.value not in row.choices:
      return None
    value = row.choices[(row.choices.index(row.value) + direction) % len(row.choices)]
  elif row.step:
    try:
      current = float(row.value)
    except ValueError:
      return None
    level = lane_strength_level(row)
    if level is not None:
      if direction not in (-1, 1):
        return None
      next_level = max(1, min(10, level + direction))
      raw = (row.minimum + (next_level - 1) * (100 - row.minimum) / 4 if next_level <= 5 else
             100 + (next_level - 5) * (row.maximum - 100) / 5)
      value = str(math.floor(raw + 0.5))
    else:
      precision = 8 if row.key.startswith("torque:") else 4
      value = str(round(max(row.minimum, min(row.maximum, current + row.step * direction)), precision))
    if float(value) == current:
      return None
  else:
    return None
  return row_request(row, value)


def row_default(row: FeatureRow, *, allow_inactive: bool = False) -> FeatureSettingsRequest | None:
  if (not row.available and not (allow_inactive and row.default_reset_available)) or not row.key or row.page or row.default_value is None:
    return None
  if not row.default_key and row.default_value not in row.choices:
    if row.step:
      try:
        number = float(row.default_value)
      except ValueError:
        return None
      if not math.isfinite(number) or not row.minimum <= number <= row.maximum:
        return None
    elif row.choices:
      return None
  return FeatureSettingsRequest(row.default_key or row.key, row.source, row.default_value, confirmation=True,
                                related_source=row.related_source, vehicle_fingerprint=row.vehicle_fingerprint,
                                capability=row.capability, dependencies=row.dependencies, display_unit=row.display_unit)
