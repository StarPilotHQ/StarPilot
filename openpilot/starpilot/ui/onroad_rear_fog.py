"""Amber rear-fog status; no fog-light control or onroad input action."""
import math
from dataclasses import replace

from openpilot.starpilot.car.ford.rear_fog import boot_time_ns, fresh
from openpilot.starpilot.ui.onroad_customization import placement
from openpilot.starpilot.ui.raylib import rl


def render_rear_fog(state, profile):
  if not state.ford_rear_fog_on or not fresh(state.ford_rear_fog_source_ns, boot_time_ns()):
    return
  position = placement(state.customization, profile, "rear_fog")
  if not position["enabled"]:
    return
  x, y = position["x"], position["y"]
  scale = 1.0 if str(profile) == "large" else .4
  amber = rl.Color(255, 184, 0, 255)
  def point(px, py):
    return rl.Vector2(x + px * scale, y + py * scale)
  def line(a, b):
    rl.draw_line_ex(point(*a), point(*b), 4.5 * scale, amber)
    rl.draw_circle_v(point(*a), 2.25 * scale, amber)
    rl.draw_circle_v(point(*b), 2.25 * scale, amber)
  curve = [(42 + 29 * math.cos(math.radians(a)), 34 + 27 * math.sin(math.radians(a))) for a in range(90, 271, 5)]
  for a, b in zip(curve, curve[1:], strict=False):
    line(a, b)
  line((42, 7), (42, 61))
  for height in (14, 34, 54):
    line((55, height), (105, height))
  fog = [(77 + 4 * math.sin(n * math.pi / 16), n) for n in range(2, 67)]
  for a, b in zip(fog, fog[1:], strict=False):
    line(a, b)


def update_rear_fog(session, snapshot, *, onroad):
  from openpilot.starpilot.car.ford.rear_fog import RearFogReceiver
  if getattr(session, "rear_fog", None) is None:
    session.rear_fog = RearFogReceiver()
  ui = session.adapter.ui_state
  cp = getattr(ui, "CP", None)
  enabled = snapshot.onroad.customization.get("layouts", {}).get(str(session.profile), {}).get("rear_fog", {}).get("enabled", False)
  fog_on, fog_stamp = session.rear_fog.sample(cp, active=onroad and bool(getattr(ui, "started", False)) and cp is not None and enabled)
  if (fog_on, fog_stamp) != (snapshot.onroad.ford_rear_fog_on, snapshot.onroad.ford_rear_fog_source_ns):
    snapshot = replace(snapshot, onroad=replace(snapshot.onroad, ford_rear_fog_on=fog_on, ford_rear_fog_source_ns=fog_stamp))
  return snapshot
