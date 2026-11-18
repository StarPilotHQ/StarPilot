import math
from dataclasses import dataclass

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.layout_preview_sidebar import metric_rects
from openpilot.starpilot.ui.presentation import FontRole


@dataclass(frozen=True)
class DeveloperMetric:
  label: str
  value: str


def finite(value):
  try:
    number = float(value)
    return number if math.isfinite(number) else None
  except (TypeError, ValueError, OverflowError):
    return None


class DeveloperMetrics:
  def __init__(self):
    self.drive = None
    self.maximum = None

  def observe(self, *, drive, car, delay, torque, parameters, metric, cp=None, applied=None, delay_selection=None):
    if drive != self.drive:
      self.drive, self.maximum = drive, None
    acceleration = finite(getattr(car, 'aEgo', None)) if car is not None else None
    if acceleration is not None and drive is not None and not bool(getattr(car, 'gasPressed', False)):
      self.maximum = max(0., acceleration) if self.maximum is None else max(self.maximum, acceleration)
    unit, conversion = ('m/s²', 1.) if metric else ('ft/s²', 3.280839895)

    def shown(value, decimals=5, suffix=''):
      number = finite(value)
      return '—' if number is None else f'{number:.{decimals}f}{suffix}'

    actual = applied if applied is not None and getattr(applied, 'version', 0) == 1 else None
    def torque_metric(field, label):
      manual = actual is not None and bool(getattr(actual, 'manual' + field.capitalize(), False))
      source = 'LOCKED ' if manual else 'LEARNED ' if actual is not None and actual.learned else 'CURRENT '
      return DeveloperMetric(source + label, shown(getattr(actual, field, None)))

    manual_delay = delay_selection is not None and delay_selection.value is not None
    delay_value = getattr(delay, 'lateralDelay', None) if delay is not None else None
    delay_label = 'ACTUATOR DELAY'
    if delay is not None:
      if manual_delay and finite(delay_value) is not None and abs(delay_value - delay_selection.value) < 1e-5:
        delay_label = 'LOCKED DELAY'
      elif not manual_delay and str(getattr(delay, 'status', '')) == 'estimated':
        delay_label = 'LEARNED DELAY'
    elif not manual_delay:
      base_delay = finite(getattr(cp, 'steerActuatorDelay', None))
      delay_value = None if base_delay is None else base_delay + .2
      delay_label = 'BASE DELAY'
    return (
      DeveloperMetric('ACCEL', shown(None if acceleration is None else acceleration * conversion, 2, f' {unit}')),
      DeveloperMetric('MAX ACCEL', shown(None if car is None or self.maximum is None else self.maximum * conversion, 2, f' {unit}')),
      DeveloperMetric(delay_label, shown(delay_value, suffix=' s')),
      torque_metric('friction', 'FRICTION'),
      torque_metric('factor', 'LAT ACCEL'),
      DeveloperMetric('STEER RATIO', shown(getattr(parameters, 'steerRatio', None))),
      DeveloperMetric('STEER STIFF', shown(getattr(parameters, 'stiffnessFactor', None))),
    )


def render_sidebar(fonts, frame, metrics):
  rl.draw_rectangle_rec(frame, rl.BLACK)
  for rect, metric in zip(metric_rects(frame, len(metrics)), metrics, strict=True):
    rl.draw_rectangle_rounded_lines_ex(rect, .3, 10, 2, rl.Color(255, 255, 255, 85))
    for line, y in ((metric.label, rect.y + 24), (metric.value, rect.y + 68)):
      size = 30
      measured = fonts.measure(line, FontRole.SEMI_BOLD, size)
      while measured.width > rect.width - 22 and size > 16:
        size -= 1
        measured = fonts.measure(line, FontRole.SEMI_BOLD, size)
      fonts.draw(line, FontRole.SEMI_BOLD, size, rect.x + (rect.width - measured.width) / 2, y, rl.WHITE)
