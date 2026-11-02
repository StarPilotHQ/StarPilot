"""Home statistics drawing with supplied data and native presentation resources."""
from __future__ import annotations

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.home_state import DriveStatsData, DriveSummary
from openpilot.starpilot.ui.home_geometry import outside_rounded_border
from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole

CARD_COLOR = rl.Color(18, 20, 29, 255)
CARD_BORDER = rl.Color(67, 57, 86, 255)
TEXT_COLOR = rl.Color(250, 248, 255, 255)
MUTED_COLOR = rl.Color(190, 186, 207, 255)
TRACK_COLOR = rl.Color(49, 51, 65, 255)
PURPLE = rl.Color(139, 108, 197, 255)
TEAL = rl.Color(94, 200, 200, 255)


def _format_count(value: int) -> str:
  return f"{value / 1000:.1f}k" if value >= 10000 else f"{value:,}"


def _format_decimal(value: float) -> str:
  if value >= 10000:
    return f"{value / 1000:.1f}k"
  return f"{value:,.0f}" if value >= 100 else f"{value:.1f}"


class DriveStatsDashboard:
  def __init__(self, fonts: BitmapFonts, data: DriveStatsData):
    self._fonts = fonts
    self._data = data
    self._font_bold = fonts.font(FontRole.BOLD)
    self._font_semi_bold = fonts.font(FontRole.SEMI_BOLD)
    self._font_medium = fonts.font(FontRole.MEDIUM)

  def _role(self, font):
    return FontRole.BOLD if font is self._font_bold else FontRole.SEMI_BOLD if font is self._font_semi_bold else FontRole.MEDIUM

  def _measure(self, font, text, size):
    measured = self._fonts.measure(text, self._role(font), size)
    return rl.Vector2(measured.width, measured.height)

  def _draw_text(self, font, text, position, size, spacing, color):
    self._fonts.draw(text, self._role(font), size, position.x, position.y, color, spacing=spacing)

  @staticmethod
  def _draw_card(rect: rl.Rectangle, accent: rl.Color | None = None) -> None:
    rl.draw_rectangle_rounded(rect, 0.04, 12, CARD_COLOR)
    outside_rounded_border(rect, 0.04, 12, 2, CARD_BORDER)
    if accent is not None:
      accent_rect = rl.Rectangle(rect.x + 18, rect.y + 12, rect.width - 36, 5)
      rl.draw_rectangle_rounded(accent_rect, 1.0, 8, accent)

  @staticmethod
  def _draw_week_bar(rect: rl.Rectangle) -> None:
    x = int(round(rect.x))
    y = int(round(rect.y))
    width = max(1, int(round(rect.width)))
    height = max(1, int(round(rect.height)))
    radius = max(1, min(6, width // 2, height // 2))

    blend = radius / height
    body_top = rl.Color(
      round(PURPLE.r + (TEAL.r - PURPLE.r) * blend),
      round(PURPLE.g + (TEAL.g - PURPLE.g) * blend),
      round(PURPLE.b + (TEAL.b - PURPLE.b) * blend),
      255,
    )

    rl.draw_circle(x + radius, y + radius, radius, PURPLE)
    rl.draw_circle(x + width - radius, y + radius, radius, PURPLE)
    rl.draw_rectangle(x + radius, y, max(1, width - 2 * radius), radius + 1, PURPLE)
    rl.draw_rectangle_gradient_v(x, y + radius, width, max(1, height - radius), body_top, TEAL)

  @staticmethod
  def _draw_record_icon(index: int, rect: rl.Rectangle) -> None:
    rl.draw_rectangle_rounded(rect, 0.18, 8, rl.Color(40, 33, 68, 255))
    cx = rect.x + rect.width / 2
    cy = rect.y + rect.height / 2
    color = PURPLE
    scale = min(rect.width, rect.height) / 64.0
    thickness = 2.5 * scale

    def line(x1: float, y1: float, x2: float, y2: float) -> None:
      rl.draw_line_ex(rl.Vector2(x1, y1), rl.Vector2(x2, y2), thickness, color)

    if index == 0:
      line(cx - 13 * scale, cy, cx + 12 * scale, cy)
      line(cx + 12 * scale, cy, cx + 5 * scale, cy - 7 * scale)
      line(cx + 12 * scale, cy, cx + 5 * scale, cy + 7 * scale)
    elif index == 1:
      rl.draw_circle_lines(int(cx), int(cy), 13 * scale, color)
      rl.draw_circle_lines(int(cx), int(cy), 12 * scale, color)
      line(cx - 7 * scale, cy, cx - 2 * scale, cy + 5 * scale)
      line(cx - 2 * scale, cy + 5 * scale, cx + 8 * scale, cy - 7 * scale)
    elif index == 2:
      line(cx - 13 * scale, cy - 12 * scale, cx - 13 * scale, cy + 12 * scale)
      line(cx - 13 * scale, cy + 12 * scale, cx + 13 * scale, cy + 12 * scale)
      line(cx - 9 * scale, cy + 6 * scale, cx - 2 * scale, cy - 2 * scale)
      line(cx - 2 * scale, cy - 2 * scale, cx + 4 * scale, cy + 3 * scale)
      line(cx + 4 * scale, cy + 3 * scale, cx + 13 * scale, cy - 8 * scale)
    elif index == 3:
      points = (
        (cx + 2 * scale, cy - 15 * scale), (cx - 10 * scale, cy + 2 * scale), (cx - 2 * scale, cy + 2 * scale),
        (cx - 5 * scale, cy + 15 * scale), (cx + 11 * scale, cy - 5 * scale), (cx + 3 * scale, cy - 5 * scale),
      )
      for point_index, point in enumerate(points):
        next_point = points[(point_index + 1) % len(points)]
        line(point[0], point[1], next_point[0], next_point[1])
    elif index == 4:
      points = (
        (cx, cy - 14 * scale), (cx + 12 * scale, cy - 9 * scale), (cx + 10 * scale, cy + 3 * scale),
        (cx, cy + 14 * scale), (cx - 10 * scale, cy + 3 * scale), (cx - 12 * scale, cy - 9 * scale),
      )
      for point_index, point in enumerate(points):
        next_point = points[(point_index + 1) % len(points)]
        line(point[0], point[1], next_point[0], next_point[1])
      line(cx - 5 * scale, cy, cx - scale, cy + 4 * scale)
      line(cx - scale, cy + 4 * scale, cx + 6 * scale, cy - 4 * scale)
    else:
      def sparkle(x: float, y: float, radius: float) -> None:
        inner = radius * 0.22
        points = (
          (x, y - radius), (x + inner, y - inner),
          (x + radius, y), (x + inner, y + inner),
          (x, y + radius), (x - inner, y + inner),
          (x - radius, y), (x - inner, y - inner),
        )
        for point_index, point in enumerate(points):
          next_point = points[(point_index + 1) % len(points)]
          line(point[0], point[1], next_point[0], next_point[1])

      sparkle(cx + 3 * scale, cy + 2 * scale, 10 * scale)
      sparkle(cx - 9 * scale, cy - 9 * scale, 5 * scale)
      sparkle(cx + 12 * scale, cy - 10 * scale, 4 * scale)

  def _draw_fitted_centered(self, text: str, rect: rl.Rectangle, font_size: int, minimum_size: int, color: rl.Color) -> None:
    size = font_size
    while size > minimum_size and self._measure(self._font_bold, text, size).x > rect.width:
      size -= 2
    text_size = self._measure(self._font_bold, text, size)
    position = rl.Vector2(
      rect.x + (rect.width - text_size.x) / 2,
      rect.y + (rect.height - text_size.y) / 2,
    )
    self._draw_text(self._font_bold, text, position, size, 0, color)

  def _draw_summary_card(self, rect: rl.Rectangle, title: str, summary: DriveSummary, accent: rl.Color) -> None:
    self._draw_card(rect, accent)
    title_pos = rl.Vector2(rect.x + 24, rect.y + 28)
    self._draw_text(self._font_semi_bold, title, title_pos, 30, 0, MUTED_COLOR)

    values = (
      (_format_count(summary.drives), ("drives")),
      (_format_decimal(summary.distance), ("km") if summary.unit == "kilometers" else ("miles")),
      (_format_decimal(summary.hours), ("hours")),
    )
    column_width = (rect.width - 32) / len(values)
    for index, (value, label) in enumerate(values):
      if index > 0:
        divider_x = rect.x + 16 + index * column_width
        rl.draw_line_ex(
          rl.Vector2(divider_x, rect.y + 76),
          rl.Vector2(divider_x, rect.y + rect.height - 23),
          2,
          TRACK_COLOR,
        )

      column_rect = rl.Rectangle(rect.x + 16 + index * column_width, rect.y + 64, column_width, 72)
      self._draw_fitted_centered(value, column_rect, 48, 30, TEXT_COLOR)
      label_size = self._measure(self._font_medium, label, 23)
      label_pos = rl.Vector2(
        column_rect.x + (column_rect.width - label_size.x) / 2,
        rect.y + rect.height - 38,
      )
      self._draw_text(self._font_medium, label, label_pos, 23, 0, MUTED_COLOR)

  def render_overview(self, rect: rl.Rectangle) -> None:
    gap = 18
    summary_height = 184
    card_width = (rect.width - gap) / 2
    summaries = (
      (("ALL TIME"), self._data.all_time, PURPLE),
      (("PAST WEEK"), self._data.past_week, TEAL),
    )
    for index, (title, summary, accent) in enumerate(summaries):
      card_rect = rl.Rectangle(rect.x + index * (card_width + gap), rect.y, card_width, summary_height)
      self._draw_summary_card(card_rect, title, summary, accent)

    graph_rect = rl.Rectangle(rect.x, rect.y + summary_height + gap, rect.width, rect.height - summary_height - gap)
    self._draw_distance_graph(graph_rect)

  def _draw_distance_graph(self, rect: rl.Rectangle) -> None:
    self._draw_card(rect)
    title = ("DISTANCE THIS WEEK")
    self._draw_text(self._font_semi_bold, title, rl.Vector2(rect.x + 30, rect.y + 26), 32, 0, TEXT_COLOR)

    unit = ("km") if self._data.this_week.unit == "kilometers" else ("miles")
    total_text = f"{_format_decimal(self._data.this_week.distance)} {unit}"
    total_size = self._measure(self._font_bold, total_text, 34)
    self._draw_text(
      self._font_bold,
      total_text,
      rl.Vector2(rect.x + rect.width - total_size.x - 30, rect.y + 24),
      34,
      0,
      TEAL,
    )

    plot = rl.Rectangle(rect.x + 42, rect.y + 92, rect.width - 84, rect.height - 142)
    for line_index in range(4):
      y = plot.y + line_index * plot.height / 3
      rl.draw_line(int(plot.x), int(y), int(plot.x + plot.width), int(y), TRACK_COLOR)

    max_distance = max((day.distance for day in self._data.daily_distance), default=0.0)
    max_distance = max(max_distance, 1.0)
    slot_width = plot.width / max(len(self._data.daily_distance), 1)
    bar_width = min(78.0, slot_width * 0.52)
    value_headroom = 38.0
    bar_area_height = max(1.0, plot.height - value_headroom)
    for index, day in enumerate(self._data.daily_distance):
      center_x = plot.x + slot_width * (index + 0.5)
      bar_height = max(5.0, bar_area_height * day.distance / max_distance) if day.distance > 0.0 else 5.0
      bar_rect = rl.Rectangle(center_x - bar_width / 2, plot.y + plot.height - bar_height, bar_width, bar_height)
      self._draw_week_bar(bar_rect)

      if day.distance > 0.0:
        value_text = _format_decimal(day.distance)
        value_size = self._measure(self._font_medium, value_text, 21)
        value_y = max(plot.y + 4, bar_rect.y - value_size.y - 8)
        self._draw_text(
          self._font_medium,
          value_text,
          rl.Vector2(center_x - value_size.x / 2, value_y),
          21,
          0,
          MUTED_COLOR,
        )

      label_color = TEXT_COLOR if day.is_today else MUTED_COLOR
      label_size = self._measure(self._font_semi_bold, day.label, 28)
      label_pos = rl.Vector2(center_x - label_size.x / 2, plot.y + plot.height + 16)
      self._draw_text(self._font_semi_bold, day.label, label_pos, 28, 0, label_color)

  def render_records(self, rect: rl.Rectangle) -> None:
    self._draw_card(rect)
    self._draw_text(
      self._font_semi_bold,
      ("PERSONAL RECORDS"),
      rl.Vector2(rect.x + 30, rect.y + 26),
      32,
      0,
      TEXT_COLOR,
    )

    displayed_records = [
      (record_index, self._data.records[record_index])
      for record_index in (0, 3, 5)
      if record_index < len(self._data.records)
    ]
    header_height = 82
    row_height = (rect.height - header_height - 12) / max(len(displayed_records), 1)
    for row_index, (record_index, record) in enumerate(displayed_records):
      row_y = rect.y + header_height + row_index * row_height
      if row_index > 0:
        rl.draw_line(int(rect.x + 24), int(row_y), int(rect.x + rect.width - 24), int(row_y), TRACK_COLOR)

      icon_size = min(120.0, row_height - 34)
      icon_rect = rl.Rectangle(rect.x + 28, row_y + (row_height - icon_size) / 2, icon_size, icon_size)
      self._draw_record_icon(record_index, icon_rect)

      text_x = rect.x + 170
      content_center_y = row_y + row_height / 2
      title_pos = rl.Vector2(text_x, content_center_y - 66)
      self._draw_text(self._font_medium, record.title, title_pos, 38, 0, MUTED_COLOR)

      value_pos = rl.Vector2(text_x, content_center_y - 10)
      self._draw_text(self._font_bold, record.value, value_pos, 56, 0, TEXT_COLOR)

      detail_font_size = 34
      detail_min_x = text_x + 250
      detail_width = rect.x + rect.width - detail_min_x - 30
      while detail_font_size > 29 and self._measure(self._font_medium, record.detail, detail_font_size).x > detail_width:
        detail_font_size -= 1
      detail_size = self._measure(self._font_medium, record.detail, detail_font_size)
      detail_x = rect.x + rect.width - detail_size.x - 30
      detail_pos = rl.Vector2(detail_x, content_center_y + 52)
      self._draw_text(self._font_medium, record.detail, detail_pos, detail_font_size, 0, MUTED_COLOR)
