"""Sample-only Big developer sidebar artwork for the parked layout preview."""

from dataclasses import dataclass

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole


SIDEBAR_WIDTH = 300
METRIC_WIDTH = 275
METRIC_HEIGHT = 126
METRIC_MARGIN = 12


@dataclass(frozen=True)
class SidebarMetric:
  label: str
  value: str
  color: tuple[int, int, int]


SAMPLE_METRICS = (
  SidebarMetric("ACCEL", "1.08 ft/s²", (255, 255, 255)),
  SidebarMetric("STEER DELAY", "0.15000", (59, 130, 246)),
  SidebarMetric("FRICTION", "0.12000", (34, 197, 94)),
  SidebarMetric("LAT ACCEL", "2.50000", (59, 130, 246)),
  SidebarMetric("LATERAL %", "78.00%", (255, 255, 255)),
  SidebarMetric("TORQUE %", "42%", (255, 255, 255)),
  SidebarMetric("CHESTNUT", "", (255, 255, 255)),
)


def metric_rects(frame: rl.Rectangle, count: int) -> tuple[rl.Rectangle, ...]:
  if not 0 <= count <= 7:
    raise ValueError("Big developer sidebar supports up to seven metrics")
  spacing = max(1, (int(frame.height) - count * METRIC_HEIGHT) // max(1, count + 1))
  x = int(frame.x + frame.width) - METRIC_MARGIN - METRIC_WIDTH
  return tuple(rl.Rectangle(x, frame.y + spacing + index * (METRIC_HEIGHT + spacing),
                            METRIC_WIDTH, METRIC_HEIGHT) for index in range(count))


def render_sidebar(fonts: BitmapFonts, frame: rl.Rectangle,
                   metrics: tuple[SidebarMetric, ...] = SAMPLE_METRICS) -> None:
  if len(metrics) > 7:
    raise ValueError("Big developer sidebar supports up to seven metrics")
  rl.draw_rectangle_rec(frame, rl.BLACK)
  for rect, metric in zip(metric_rects(frame, len(metrics)), metrics, strict=True):
    color = rl.Color(*metric.color, 255)
    edge = rl.Rectangle(rect.x + METRIC_WIDTH - 104, rect.y + 4, 100, 118)
    rl.draw_rectangle_rounded(edge, 0.3, 10, color)
    rl.draw_rectangle_rec(rl.Rectangle(edge.x, rect.y, 82, rect.height), rl.BLACK)
    rl.draw_rectangle_rounded_lines_ex(rect, 0.3, 10, 2, rl.Color(255, 255, 255, 85))
    lines = (metric.label, metric.value) if metric.value else (metric.label,)
    fitted = []
    for line in lines:
      font_size = 35
      measured = fonts.measure(line, FontRole.SEMI_BOLD, font_size)
      while measured.width > rect.width - 22 and font_size > 20:
        font_size -= 1
        measured = fonts.measure(line, FontRole.SEMI_BOLD, font_size)
      fitted.append((line, font_size, measured))
    y = rect.y + (rect.height - sum(measured.height for _, _, measured in fitted)) / 2
    for line, font_size, measured in fitted:
      fonts.draw(line, FontRole.SEMI_BOLD, font_size,
                 rect.x + (rect.width - measured.width) / 2, y, rl.WHITE)
      y += measured.height
