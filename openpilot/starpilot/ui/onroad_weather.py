"""Small vector weather art in the onroad widget palette."""

import math
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.presentation import FontRole
from openpilot.starpilot.ui.onroad_customization import placement, rgba, widget_size
from openpilot.starpilot.ui.onroad_widget_style import draw_widget_frame
from openpilot.starpilot.ui.presentation import Profile


def draw_weather_icon(x, y, size, code, is_day):
  def point(a, b):
    return rl.Vector2(x + a * size, y + b * size)
  def line(a, b, c, d, color, thickness=.045):
    rl.draw_line_ex(point(a, b), point(c, d), max(1, size * thickness), color)
  lavender, blue, white = rl.Color(199, 174, 247, 255), rl.Color(125, 202, 255, 255), rl.Color(237, 240, 255, 255)
  if code is None:
    white = rl.Color(145, 151, 166, 200)
  if code in (0, 1, 2):
    if is_day:
      rl.draw_circle_v(point(.38, .37), size * .17, lavender)
      for angle in range(0, 360, 45):
        a = math.radians(angle)
        line(.38 + .23 * math.cos(a), .37 + .23 * math.sin(a),
             .38 + .30 * math.cos(a), .37 + .30 * math.sin(a), lavender)
    else:
      for step in range(16):
        a, b = (math.radians(55 + index * 250 / 16) for index in (step, step + 1))
        thickness = .03 + .14 * math.sin(math.pi * (step + .5) / 16)
        line(.42 + .25 * math.cos(a), .35 + .25 * math.sin(a),
             .42 + .25 * math.cos(b), .35 + .25 * math.sin(b), lavender, thickness)
        rl.draw_circle_v(point(.42 + .25 * math.cos(b), .35 + .25 * math.sin(b)), size * thickness / 2, lavender)
      rl.draw_circle_v(point(.77, .19), size * .035, blue)
    if code == 0:
      return
  rl.draw_circle_v(point(.32, .55), size * .18, white)
  rl.draw_circle_v(point(.52, .44), size * .23, white)
  rl.draw_circle_v(point(.74, .57), size * .16, white)
  rl.draw_rectangle_rounded(rl.Rectangle(x + size * .23, y + size * .54, size * .58, size * .19), .8, 8, white)
  if code in (45, 48):
    for row in (.82, .94):
      line(.22, row, .8, row, blue, .035)
  elif code in (71, 73, 75, 77, 85, 86):
    for column in (.32, .56, .78):
      rl.draw_circle_v(point(column, .9), size * .04, blue)
  elif code in (95, 96, 99):
    line(.57, .7, .43, .87, lavender, .07)
    line(.43, .87, .62, .84, lavender, .07)
    line(.62, .84, .48, 1., lavender, .07)
  elif code is not None and code >= 51:
    for column in (.32, .56, .78):
      line(column, .81, column - .06, .98, blue)


def render_weather(fonts, state, client, center_shift=0):
  profile = fonts.profile
  position = placement(state.customization, profile, 'weather')
  width, height = widget_size(state.customization, profile, 'weather')
  x, y = position['x'] + center_shift, position['y']
  reading = state.weather or client.observe(state.weather_location)
  draw_widget_frame(rl.Rectangle(x, y, width, height), state.customization, profile, 'weather')
  large = profile == Profile.LARGE
  size = 44 if large else 24
  icon_size = height * .76
  draw_weather_icon(x + height * .09, y + height * .1, icon_size,
                    reading.code if reading else None, reading.is_day if reading else True)
  text = '--°' if reading is None else f'{reading.temperature_c if state.metric else reading.temperature_c * 1.8 + 32:.0f}°'
  text += 'C' if state.metric else 'F'
  color = rl.Color(*rgba(state.customization, 'text', profile, 'weather'))
  fonts.draw(text, FontRole.SEMI_BOLD, size, x + height, y + height * .09, color)
  fonts.draw('Open-Meteo' if reading else 'Outside', FontRole.NORMAL, 16 if large else 8,
             x + height, y + height * .69, rl.Color(199, 174, 247, min(color.a, 220)))
