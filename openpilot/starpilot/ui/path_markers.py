"""Lead and stop lines drawn on the driving path, shared by the large and compact model renderers."""
from dataclasses import dataclass
import math
from types import SimpleNamespace

import numpy as np

from openpilot.cereal import log
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.controls.radard import RADAR_TO_CAMERA
from openpilot.selfdrive.ui.ui_state import UIStatus, ui_state
from openpilot.starpilot.ui.appearance_preferences import format_lead_info
from openpilot.starpilot.ui.onroad_cem_gauge import COLOR_FORCE_STOP
from openpilot.starpilot.ui.onroad_conditional import stop_active
from openpilot.starpilot.ui.raylib import rl
from openpilot.starpilot.ui.road_colors import lead_marker_color
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.shader_polygon import draw_polygon
from openpilot.system.ui.lib.text_measure import measure_text_cached

E2E = log.LongitudinalPlan.LongitudinalPlanSource.e2e
MAX_DRAW_DISTANCE = 100.0  # m
STRIP_DEPTH = 0.6  # m of road a line covers
LEAD_HALF_WIDTH = 0.9  # m, half a car's width, which is also the drawn path's
WIDTH_CAP = 0.3  # a lead line is never wider than this share of the screen
LEAD_JUMP = (4.0, 0.15)  # m or share of distance, whichever is larger: a bigger change is another car or a flicker
LEAD_HOLD = 0.25  # s, a farther reading must last this long before the line glides to it; closer ones go at once
LEAD_SNAP = 0.5  # m, a glide locks onto the car once this close instead of creeping the last bit
URGENCY_DISTANCE, URGENCY_SPEED = 40.0, 10.0  # m, m/s: the old chevron's closeness and closing-speed scale
LEAD_OPACITY = {False: (0.7, 1.0), True: (0.35, 0.5)}  # (calm, urgent), keyed by disengaged
STOP_OPACITY = 0.9  # white while planned; the committed red is drawn at full strength
UNIT_SCALE = 0.6  # the unit sits under the number at this size, like the HUD's speed unit
TEXT_MIN = 16  # px; lead text fades out below this size rather than shrink unreadably
STOP_FADE = (6.0, 10.0)  # m, the stop line fades out over this range as the car closes in

# Planned stop, ported from openpilot master's mici model renderer.
STOP_SPEED = 1.0  # m/s, planned speed that counts as stopping
STOP_APPEAR_DISTANCE = (8.0, 50.0)  # m
STOP_LEAD_MARGIN = 15.0  # m, a lead this close past the stop is what it's stopping for
LANE_EDGES = (-1.8, 1.8)  # m, a 3.6 m lane for a stop line without lane lines
LANE_LINE_PROB = 0.5


@dataclass(frozen=True)
class PathMarkerInput:
  indicator: bool = False
  info_mode: int = 0
  metric: bool | None = None
  lateral_active: bool = False
  stop_committed: bool = False


def path_markers(state) -> PathMarkerInput:
  """The one place onroad state becomes marker choices; without saved appearance the markers stay off."""
  appearance = getattr(state, 'appearance', None)
  if appearance is None:
    return PathMarkerInput()
  indicator = bool(appearance.show_lead_indicator and state.lead_indicator_source_fresh)
  return PathMarkerInput(indicator=indicator, info_mode=int(appearance.lead_info_mode), metric=appearance.lead_info_metric,
                         lateral_active=bool(state.lateral_active),
                         stop_committed=indicator and (stop_active(state) or bool(state.force_stop)))


def valid_lead_numbers(lead) -> bool:
  try:
    distance, lateral = float(lead.dRel), float(lead.yRel)
    relative_speed = float(lead.vRel) if lead.vRel is not None else 0.0
    return distance > 0 and all(math.isfinite(value) for value in (distance, lateral, relative_speed))
  except (AttributeError, TypeError, ValueError, OverflowError):
    return False


def urgency(d_rel: float, v_rel: float | None) -> float:
  if d_rel >= URGENCY_DISTANCE:
    return 0.0
  return min(1.0, 1.0 - d_rel / URGENCY_DISTANCE + max(-(v_rel or 0.0), 0.0) / URGENCY_SPEED)


def bounds(strip: np.ndarray) -> rl.Rectangle:
  (left, top), (right, bottom) = strip.min(axis=0), strip.max(axis=0)
  return rl.Rectangle(float(left), float(top), float(right - left), float(bottom - top))


def hysteresis(x: float, state: bool, on: float, off: float) -> bool:
  return x > on or (state and x > off)


class LeadMarker:
  def __init__(self):
    self.info = None
    self.strip: np.ndarray | None = None  # screen corners, drawn like the path
    self.box: rl.Rectangle | None = None  # the strip's bounds, for placing text
    self.d_raw = 0.0  # last accepted camera distance
    self.held_frames = 0  # frames a farther reading has lasted
    self.gliding = False
    self.d_filter = FirstOrderFilter(0.0, 0.2, 1 / gui_app.target_fps, initialized=False)
    self.fade_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)

  def follow(self, d: float, info) -> None:
    """Glide along the path to a new car, closer ones at once and farther ones once they last; flickers are ignored."""
    if self.d_filter.initialized:
      jump = abs(d - self.d_raw) > max(LEAD_JUMP[0], LEAD_JUMP[1] * self.d_raw)
      self.held_frames = self.held_frames + 1 if jump and d > self.d_raw else 0
      if jump and d > self.d_raw and self.held_frames < LEAD_HOLD * gui_app.target_fps:
        return
      self.gliding = self.gliding or jump
    self.d_raw, self.info = d, info
    if self.gliding and abs(self.d_filter.update(d) - d) < LEAD_SNAP:
      self.d_filter.x, self.gliding = d, False
    elif not self.gliding:
      self.d_filter.update(d)

  def lose(self) -> None:
    self.info, self.held_frames, self.gliding = None, 0, False
    self.d_filter.initialized = False


class StopPoint:
  """Where the model plans to stop with no lead to stop behind, e.g. at a stop sign or red light"""
  def __init__(self):
    self.strip: np.ndarray | None = None  # screen corners, drawn like the path
    self.box: rl.Rectangle | None = None  # the strip's bounds, for placing text
    self.committed = False
    self.d = 0.0
    self.planned_d: float | None = None
    self.visible = False
    # the planned stop comes and goes frame to frame, so it needs to persist before showing and before hiding
    self._planning, self._planning_filter = False, FirstOrderFilter(0.0, 0.3, 1 / gui_app.target_fps)
    self._behind_lead, self._behind_lead_filter = False, FirstOrderFilter(0.0, 0.3, 1 / gui_app.target_fps)
    # lateral offsets of the line's ends from the path, at the lane lines
    self.edges = [FirstOrderFilter(y, 0.2, 1 / gui_app.target_fps, initialized=False) for y in LANE_EDGES]
    self.fade_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)

  def update(self, model, model_updated: bool, v_ego: float, lead_distances: list[float]) -> bool:
    # the stop point is fixed on the road, so move it with the car between model frames
    self.d = max(self.d - v_ego / gui_app.target_fps, 0.0)
    if model_updated:
      self.planned_d = self._get_planned_stop(model)
      if self.planned_d is not None:
        self.d = self.d + 0.3 * (self.planned_d - self.d) if self.visible else self.planned_d

    self._planning = hysteresis(self._planning_filter.update(self.planned_d is not None), self._planning, 0.6, 0.2)
    # the lead line already shows stopping behind a lead
    behind_lead = any(d < self.d + RADAR_TO_CAMERA + STOP_LEAD_MARGIN for d in lead_distances)
    self._behind_lead = hysteresis(self._behind_lead_filter.update(behind_lead), self._behind_lead, 0.6, 0.1)
    # appear out ahead, not right in front of the car while creeping or stopped,
    # and not so far out that a lead to stop behind may not be detected yet
    appear = STOP_APPEAR_DISTANCE[0] < self.d < STOP_APPEAR_DISTANCE[1]
    self.visible = self._planning and not self._behind_lead and (self.visible or appear)
    return self.visible

  @staticmethod
  def _get_planned_stop(model) -> float | None:
    vx, px = np.array(model.velocity.x), np.array(model.position.x)
    stopped = np.flatnonzero(vx < STOP_SPEED)
    if not stopped.size or len(vx) != len(px):
      return None
    i = stopped[0]
    return 0.0 if i == 0 else float(np.interp(STOP_SPEED, vx[[i, i - 1]], px[[i, i - 1]]))


class PathMarkers:
  """Mixed into both model renderers, which supply the path, lane lines, transform and screen rect."""
  LINE_PX = (4, 16)  # on-screen depth range of a painted strip; the max is also the text gap and the screen margin
  LEAD_INFO_FONT = 40  # the HUD's secondary label size
  _markers: PathMarkerInput | None = None
  _paint = True

  def _init_markers(self):
    self._lead = LeadMarker()
    self._stop_point = StopPoint()

  def render_with_markers(self, rect: rl.Rectangle, markers: PathMarkerInput | None, paint: bool = True):
    self._markers, self._paint = markers, paint
    try:
      return self.render(rect)
    finally:
      self._markers, self._paint = None, True

  def _marker_input(self) -> PathMarkerInput:
    # without a StarPilot screen's choices, follow upstream: lines whenever openpilot controls speed, no text
    return self._markers or PathMarkerInput(indicator=self._longitudinal_control)

  def _visual_status(self) -> UIStatus:
    lateral = self._markers is not None and self._markers.lateral_active
    return UIStatus.ENGAGED if ui_state.status == UIStatus.DISENGAGED and lateral else ui_state.status

  def _draw_offset(self) -> tuple[float, float]:
    return 0.0, 0.0

  def _lead_source(self, sm) -> tuple[float, object] | None:
    """(camera distance, display values) of the car being followed, from the model in e2e and radar otherwise.

    Only the first lead: the second is the model's guess at the lead two seconds from now, not a second car."""
    plan, model = sm['longitudinalPlan'], sm['modelV2']
    info = None
    if sm.valid['longitudinalPlan'] and sm.valid['modelV2'] and plan.longitudinalPlanSource == E2E and len(model.leadsV3):
      ego_speed = sm['carState'].vEgo if sm.all_checks(['carState']) else None
      if ego_speed is not None and not math.isfinite(ego_speed):
        ego_speed = None
      candidate = model.leadsV3[0]
      if candidate.x and candidate.y and candidate.v:
        info = SimpleNamespace(present=candidate.prob > 0.5, dRel=candidate.x[0] - RADAR_TO_CAMERA,
                               yRel=-candidate.y[0], vRel=candidate.v[0] - ego_speed if ego_speed is not None else None,
                               vLead=candidate.v[0])
    elif sm.valid['radarState']:
      info = sm['radarState'].leadOne
    if info is None or not info.present or not valid_lead_numbers(info):
      return None
    return float(info.dRel) + RADAR_TO_CAMERA, info

  def _update_markers(self, sm):
    """Runs every frame so the filters and the stop point's dead reckoning keep time."""
    markers, path, cp = self._marker_input(), self._path.raw_points, getattr(ui_state, 'CP', None)
    available = bool(markers.indicator and len(path) > 1 and np.isfinite(path).all() and not (cp is not None and cp.passive) and
                     (sm['selfdriveState'].enabled or sm['selfdriveState'].engageable or sm['carState'].brakePressed or
                      markers.lateral_active))
    source = self._lead_source(sm) if available else None
    disengaged = self._visual_status() == UIStatus.DISENGAGED
    calm, urgent = LEAD_OPACITY[disengaged]

    lead, box = self._lead, None
    if source is not None and source[0] < MAX_DRAW_DISTANCE:
      lead.follow(*source)
      # centered on the path, so the line never follows the car's drift within its lane
      d = lead.d_filter.x
      y = float(np.interp(d, path[:, 0], path[:, 1]))
      box = self._road_strip(d, y - LEAD_HALF_WIDTH, y + LEAD_HALF_WIDTH, cap=True, hold=True)
    if box is None:
      lead.lose()
    else:
      lead.strip, lead.box = box, bounds(box)
    lead.fade_filter.update(calm + (urgent - calm) * urgency(float(lead.info.dRel), lead.info.vRel) if box is not None else 0.0)
    if lead.fade_filter.x < 0.01:
      lead.strip = lead.box = None

    self._update_stop(sm, markers, available, [] if source is None else [source[0]], disengaged)

  def _update_stop(self, sm, markers: PathMarkerInput, available: bool, lead_distances: list[float], disengaged: bool):
    stop, visible, fade = self._stop_point, False, 0.0
    if available and self._longitudinal_control:
      planning = stop.update(sm['modelV2'], sm.updated['modelV2'], sm['carState'].vEgo, lead_distances)
      e2e = sm.valid['longitudinalPlan'] and sm['longitudinalPlan'].longitudinalPlanSource == E2E
      d = stop.d + RADAR_TO_CAMERA
      fade = min(max((d - STOP_FADE[0]) / (STOP_FADE[1] - STOP_FADE[0]), 0.0), 1.0)
      visible = planning and (e2e or markers.stop_committed) and fade > 0 and d < MAX_DRAW_DISTANCE
    if visible:
      lane = self._path.raw_points
      path_y = float(np.interp(d, lane[:, 0], lane[:, 1]))
      edges = []
      for f, ll, prob, default in zip(stop.edges, self._lane_lines[1:3], self._lane_line_probs[1:3], LANE_EDGES, strict=True):
        line = ll.raw_points
        edges.append(f.update(np.interp(d, line[:, 0], line[:, 1]) - path_y if prob > LANE_LINE_PROB and len(line) else default))
      # centered on the path at the stop; upstream's straight-ahead center drifts off the lane on curves
      strip = self._road_strip(d, path_y + edges[0], path_y + edges[1], cap=False, hold=False)
      visible = strip is not None
      if visible:
        stop.strip, stop.box, stop.committed = strip, bounds(strip), markers.stop_committed
    stop.fade_filter.update(fade * (0.5 if disengaged else 1.0) if visible else 0.0)
    if stop.fade_filter.x < 0.01:
      stop.strip = stop.box = None

  def _road_strip(self, d: float, y_a: float, y_b: float, *, cap: bool, hold: bool) -> np.ndarray | None:
    """Screen corners of a STRIP_DEPTH-deep strip lying on the road from distance d back toward the car, painted
    like the path; its on-screen depth stays within LINE_PX so it never thins out or grows into a block."""
    path = self._path.raw_points
    near = max(d - STRIP_DEPTH, 0.5)
    z = np.interp([d, near], path[:, 0], path[:, 2]) + self._path_offset_z
    # same corner order as the path ribbon (near a, far a, far b, near b): raylib culls a strip wound the other way
    corners = np.array([[near, y_a, z[1]], [d, y_a, z[0]], [d, y_b, z[0]], [near, y_b, z[1]]], dtype=np.float32)
    pts = self._car_space_transform @ corners.T
    if not np.isfinite(pts).all() or np.any(np.abs(pts[2]) < 1e-6):
      return None
    strip = (pts[:2] / pts[2]).T + np.array(self._draw_offset(), dtype=np.float32)
    far, near_edge = [1, 2], [0, 3]
    depth = float(np.linalg.norm(strip[near_edge].mean(axis=0) - strip[far].mean(axis=0)))
    if not np.isfinite(strip).all() or depth < 1e-6 or abs(strip[2, 0] - strip[1, 0]) < 1:
      return None
    strip[near_edge] = strip[far] + (strip[near_edge] - strip[far]) * (np.clip(depth, *self.LINE_PX) / depth)
    rect, width = self._rect, float(strip[:, 0].max() - strip[:, 0].min())
    if cap and width > WIDTH_CAP * rect.width:
      mid = float(strip[:, 0].mean())
      strip[:, 0] = mid + (strip[:, 0] - mid) * (WIDTH_CAP * rect.width / width)
    if hold:
      strip[:, 1] -= max(0.0, float(strip[:, 1].max()) - (rect.y + rect.height - self.LINE_PX[1]))
    return strip.astype(np.float32)

  def _draw_markers(self):
    if not self._paint:
      return
    stop = self._stop_point
    if stop.box is not None:
      alpha = stop.fade_filter.x if stop.committed else stop.fade_filter.x * STOP_OPACITY
      base = COLOR_FORCE_STOP if stop.committed else rl.WHITE
      draw_polygon(self._rect, stop.strip, rl.Color(base.r, base.g, base.b, int(255 * alpha)))
    lead = self._lead
    if lead.box is None:
      return
    alpha = lead.fade_filter.x
    color = lead_marker_color(self.road_style, alpha)
    draw_polygon(self._rect, lead.strip, color if color is not None else rl.Color(255, 255, 255, int(255 * alpha)))
    markers = self._marker_input()
    text = format_lead_info(lead.info, markers.info_mode, markers.metric) if lead.info is not None else ""
    if text:
      self._draw_lead_info(lead.box, text, min(1.0, alpha / LEAD_OPACITY[True][0]), stop.box)

  def _draw_lead_info(self, line: rl.Rectangle, text: str, alpha: float, avoid: rl.Rectangle | None):
    """The number over a smaller unit, as large as fits the path under the lead line (up to LEAD_INFO_FONT); centered
    below the line, or above it when there's no room or it would cover the stop line. Fades out below TEXT_MIN."""
    font, gap, rect = gui_app.font(FontWeight.SEMI_BOLD), self.LINE_PX[1], self._rect
    number, _, unit = text.partition(" ")
    cap = self.LEAD_INFO_FONT
    widest = max(measure_text_cached(font, number, cap).x, measure_text_cached(font, unit, round(cap * UNIT_SCALE)).x)
    size = cap * min(1.0, line.width / widest)
    alpha *= min(max((size - TEXT_MIN + 2) / 2, 0.0), 1.0)
    if alpha <= 0:
      return
    unit_size = size * UNIT_SCALE
    lines = [(number, size, measure_text_cached(font, number, round(size)).x)]
    if unit:
      lines.append((unit, unit_size, measure_text_cached(font, unit, round(unit_size)).x))
    width, height = max(w for _, _, w in lines), sum(s for _, s, _ in lines)
    center = min(max(line.x + line.width / 2, rect.x + gap + width / 2), rect.x + rect.width - gap - width / 2)
    y = line.y + line.height + gap
    if y + height > rect.y + rect.height - gap or (avoid is not None and rl.check_collision_recs(
        rl.Rectangle(center - width / 2, y, width, height), avoid)):
      y = line.y - gap - height
    a = int(255 * alpha)
    for label, label_size, label_width in lines:
      x = center - label_width / 2
      for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        rl.draw_text_ex(font, label, rl.Vector2(x + dx, y + dy), label_size, 0, rl.Color(0, 0, 0, a))
      rl.draw_text_ex(font, label, rl.Vector2(x, y), label_size, 0, rl.Color(255, 255, 255, a))
      y += label_size
