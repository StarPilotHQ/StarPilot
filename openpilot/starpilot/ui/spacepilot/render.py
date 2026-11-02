"""Procedural native artwork. No downloads, offscreen allocations or external assets."""

import math
import random

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.spacepilot.state import Flight, Phase
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.multilang import tr_display

INK = rl.Color(8, 13, 30, 255)
WHITE = rl.Color(235, 248, 255, 255)
MUTED = rl.Color(145, 170, 199, 255)
CYAN = rl.Color(89, 229, 246, 255)
VIOLET = rl.Color(158, 134, 252, 255)
GOLD = rl.Color(255, 207, 121, 255)
EXIT = (938., 10., 118., 48.)


def canvas_rect(rect: rl.Rectangle) -> rl.Rectangle:
  scale = min(rect.width / 1072, rect.height / 480)
  return rl.Rectangle(rect.x + (rect.width - 1072 * scale) / 2, rect.y + (rect.height - 480 * scale) / 2,
                      1072 * scale, 480 * scale)


class SpaceRenderer:
  def __init__(self):
    rng = random.Random(5683)
    self.stars = tuple((rng.uniform(0, 1072), rng.uniform(65, 435), rng.uniform(.8, 2.1), rng.uniform(4, 18)) for _ in range(64))
    self.vector_gradient = True

  def gradient(self, x: int, y: int, radius: float, inner, outer) -> None:
    # Desktop and comma raylib expose different native gradient signatures.
    if self.vector_gradient:
      rl.draw_circle_gradient(rl.Vector2(x, y), radius, inner, outer)
    else:
      rl.draw_circle_gradient(x, y, radius, inner, outer)

  @staticmethod
  def text(text: str, x: float, y: float, size: int, color=WHITE, *, centered: bool = False, bold: bool = False) -> None:
    text = tr_display(text)
    font = gui_app.font(FontWeight.BOLD if bold else FontWeight.NORMAL)
    if centered:
      x -= measure_text_cached(font, text, size).x / 2
    rl.draw_text_ex(font, text, (x, y), size, 0, color)

  @staticmethod
  def panel(x: float, y: float, w: float, h: float, color) -> None:
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, w, h), .22, 8, color)

  def background(self, game: Flight) -> None:
    rl.draw_rectangle_gradient_v(0, 0, 1072, 480, rl.Color(16, 20, 48, 255), rl.Color(7, 15, 30, 255))
    # Orbital glow and shadowed gas giant: a restrained, coherent cyan/violet palette.
    self.gradient(805, 230, 280, rl.Color(60, 46, 112, 42), rl.Color(20, 19, 52, 0))
    for x, y, size, speed in self.stars:
      px = (x - game.age * speed) % 1072
      rl.draw_circle_v((px, y), size, rl.Color(130, 179, 216, 145 if size < 1.5 else 225))
    rl.draw_ellipse_lines(827, 207, 210, 88, rl.Color(92, 75, 135, 100))
    self.gradient(836, 187, 104, rl.Color(94, 77, 146, 255), rl.Color(31, 34, 66, 255))
    self.gradient(869, 168, 92, rl.Color(15, 24, 46, 255), rl.Color(19, 27, 51, 100))
    rl.draw_circle_lines(836, 187, 104, rl.Color(128, 109, 190, 160))
    rl.draw_line_ex((623, 224), (1012, 168), 2, rl.Color(121, 113, 170, 180))
    rl.draw_line_ex((633, 230), (1006, 176), 1, rl.Color(166, 146, 210, 110))
    rl.draw_rectangle_gradient_v(0, 375, 1072, 105, rl.Color(7, 15, 30, 0), INK)

  @staticmethod
  def ship(x: float, y: float, tilt: float, t: float, *, scale: float = 1.) -> None:
    rl.rl_push_matrix()
    rl.rl_translatef(x, y, 0)
    rl.rl_rotatef(tilt, 0, 0, 1)
    rl.rl_scalef(scale, scale, 1)
    flame = 34 + 9 * math.sin(t * 38)
    rl.draw_triangle((-20, -9), (-20 - flame, 0), (-20, 9), rl.Color(69, 202, 246, 110))
    rl.draw_triangle((-19, -5), (-40, 0), (-19, 5), CYAN)
    rl.draw_triangle((-18, -5), (-30, 0), (-18, 5), WHITE)
    rl.draw_triangle((-20, -25), (-16, 0), (12, -5), VIOLET)
    rl.draw_triangle((-20, 25), (12, 5), (-16, 0), VIOLET)
    rl.draw_triangle((-23, -13), (-23, 13), (32, 0), WHITE)
    rl.draw_triangle((-21, 10), (25, 1), (-9, 2), rl.Color(126, 160, 185, 255))
    rl.draw_triangle((-3, -7), (-7, 3), (17, -1), rl.Color(32, 111, 145, 255))
    rl.draw_line_ex((-4, -7), (17, -1), 2, CYAN)
    rl.rl_pop_matrix()

  def preparing(self, rect: rl.Rectangle, *, stopping: bool) -> None:
    rl.draw_rectangle_rec(rect, INK)
    rect = canvas_rect(rect)
    rl.rl_push_matrix()
    rl.rl_translatef(rect.x, rect.y, 0)
    rl.rl_scalef(rect.width / 1072, rect.height / 480, 1)
    try:
      rl.draw_rectangle_gradient_v(0, 0, 1072, 480, rl.Color(16, 20, 48, 255), INK)
      self.text('SpacePilot', 536, 109, 62, centered=True, bold=True)
      self.text('Entering parked game mode' if stopping else 'Checking physical Park', 536, 211, 27, CYAN, centered=True)
      self.text('Waiting for driving services to stop' if stopping else 'Keep the car in Park and release the wheel buttons',
                536, 265, 21, MUTED, centered=True)
      self.text('Driving services stay off until you select Onroad or Auto again.', 536, 362, 19, MUTED, centered=True)
      self.panel(*EXIT, rl.Color(30, 43, 62, 255))
      self.text('EXIT', 997, 19, 22, centered=True, bold=True)
    finally:
      rl.rl_pop_matrix()

  def render(self, game: Flight, rect: rl.Rectangle, *, wheel_ready: bool, wheel_available: bool = True) -> None:
    rl.draw_rectangle_rec(rect, INK)
    rect = canvas_rect(rect)
    rl.rl_push_matrix()
    rl.rl_translatef(rect.x, rect.y, 0)
    rl.rl_scalef(rect.width / 1072, rect.height / 480, 1)
    try:
      self.background(game)
      if game.phase != Phase.READY:
        for gate in game.gates:
          high, low = gate.center - game.GAP / 2, gate.center + game.GAP / 2
          for top, bottom, edge in ((game.TOP, high, high - 6), (low, game.BOTTOM, low)):
            rl.draw_rectangle_gradient_h(int(gate.x), int(top), 72, int(bottom - top), rl.Color(32, 51, 74, 255), rl.Color(17, 30, 49, 255))
            rl.draw_line_ex((gate.x + 5, top), (gate.x + 5, bottom), 2, rl.Color(66, 116, 140, 255))
            rl.draw_rectangle_rec(rl.Rectangle(gate.x - 6, edge, 84, 6), CYAN)
            rl.draw_rectangle_rec(rl.Rectangle(gate.x - 6, edge - 4, 84, 14), rl.Color(89, 229, 246, 28))
            for y in range(int(top + 24), int(bottom - 16), 36):
              rl.draw_line_ex((gate.x + 24, y), (gate.x + 51, y), 2, rl.Color(59, 82, 108, 255))
        if game.phase == Phase.FLIGHT:
          self.ship(game.SHIP_X, game.y, max(-24., min(42., game.vy * .1)), game.age)
        else:
          for i in range(16):
            angle, distance = i * math.tau / 16, min(70., game.over_age * 130)
            alpha = int(255 * max(0., 1 - game.over_age / .7))
            rl.draw_circle_v((game.SHIP_X + math.cos(angle) * distance, game.y + math.sin(angle) * distance),
                             3, rl.Color(89, 229, 246, alpha))
      rl.draw_rectangle(0, 0, 1072, 64, INK)
      rl.draw_line(0, 63, 1072, 63, rl.Color(46, 66, 88, 255))
      self.text('SpacePilot', 22, 13, 27, bold=True)
      if game.phase == Phase.FLIGHT:
        self.text(f'{game.score:02d}', 536, 3, 42, centered=True, bold=True)
      else:
        self.text('PARKED FLIGHT CLUB', 536, 20, 18, MUTED, centered=True)
      self.panel(*EXIT, rl.Color(30, 43, 62, 255))
      self.text('EXIT', 997, 19, 22, centered=True, bold=True)
      rl.draw_rectangle(0, 439, 1072, 41, INK)
      rl.draw_line(0, 438, 1072, 438, rl.Color(46, 66, 88, 255))
      self.text('P  /  DRIVING SERVICES OFF', 22, 450, 16, CYAN)
      hint = 'TAP OR + / – TO BOOST' if wheel_available else 'TOUCH OR PAIRED CONTROLLER'
      self.text(hint, 1050 - measure_text_cached(gui_app.font(), hint, 16).x, 450, 16, MUTED)
      if game.phase == Phase.READY:
        self.ship(247, 234 + math.sin(game.age * 2.2) * 8, -8 + math.sin(game.age * 1.4) * 4, game.age, scale=2.7)
        self.text('A LITTLE ESCAPE', 675, 100, 18, CYAN, centered=True)
        self.text('SpacePilot', 675, 139, 62, centered=True, bold=True)
        self.text('Thread the gates. Chase the stars.', 675, 224, 22, MUTED, centered=True)
        self.panel(395, 286, 560, 60, rl.Color(38, 80, 103, 255))
        self.text('Press +/– or tap to start' if wheel_available else 'Tap to start', 675, 300, 27, centered=True, bold=True)
        self.text(('Release each wheel button once to arm it' if not wheel_ready else 'One press. One boost. Find your rhythm.')
                  if wheel_available else 'Controller: use your mapped +/– buttons.',
                  675, 369, 18, MUTED, centered=True)
      elif game.phase == Phase.OVER:
        self.panel(326, 99, 450, 306, rl.Color(10, 19, 37, 245))
        self.text('FLIGHT COMPLETE', 551, 119, 18, CYAN, centered=True)
        self.text(f'{game.score:02d}', 551, 147, 73, centered=True, bold=True)
        self.text(f'GATES CLEARED   /   BEST {game.best:02d}', 551, 244, 19, MUTED, centered=True)
        self.panel(356, 301, 390, 67, rl.Color(38, 80, 103, 255))
        self.text('Press +/– or tap to retry' if wheel_available else 'Tap to retry', 551, 321, 23, centered=True, bold=True)
    finally:
      rl.rl_pop_matrix()
