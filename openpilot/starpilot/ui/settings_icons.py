"""Preserved Settings vector icon geometry; no application or cache ownership."""

import math
from openpilot.starpilot.ui.raylib import rl

def draw_icon_geometry(key: str, x: float, y: float, s: float, color: rl.Color):
  def draw_bezier(p0: rl.Vector2, p1: rl.Vector2, p2: rl.Vector2, thick: float):
    segments = 128
    for i in range(segments):
      t1 = i / segments
      t2 = (i + 1) / segments
      x1_val = (1 - t1)**2 * p0.x + 2 * (1 - t1) * t1 * p1.x + t1**2 * p2.x
      y1_val = (1 - t1)**2 * p0.y + 2 * (1 - t1) * t1 * p1.y + t1**2 * p2.y
      x2_val = (1 - t2)**2 * p0.x + 2 * (1 - t2) * t2 * p1.x + t2**2 * p2.x
      y2_val = (1 - t2)**2 * p0.y + 2 * (1 - t2) * t2 * p1.y + t2**2 * p2.y
      rl.draw_line_ex(rl.Vector2(x1_val, y1_val), rl.Vector2(x2_val, y2_val), thick, color)
  def draw_ellipse_arc(cx: float, cy: float, a: float, b: float, tilt_deg: float, start_deg: float, end_deg: float, thick: float):
    tilt = math.radians(tilt_deg)
    segments = 128
    step = (end_deg - start_deg) / segments
    for i in range(segments):
      p1 = math.radians(start_deg + i * step)
      p2 = math.radians(start_deg + (i + 1) * step)

      x1_val = a * math.cos(p1)
      y1_val = b * math.sin(p1)
      rx1 = x1_val * math.cos(tilt) - y1_val * math.sin(tilt)
      ry1 = x1_val * math.sin(tilt) + y1_val * math.cos(tilt)

      x2_val = a * math.cos(p2)
      y2_val = b * math.sin(p2)
      rx2 = x2_val * math.cos(tilt) - y2_val * math.sin(tilt)
      ry2 = x2_val * math.sin(tilt) + y2_val * math.cos(tilt)

      rl.draw_line_ex(rl.Vector2(cx + rx1, cy + ry1), rl.Vector2(cx + rx2, cy + ry2), thick, color)
  if key == "sound":
    # Sounds & Alerts: Speaker icon on the left, sound waves on the right (proportionate weight & scale)
    x_c = x + 30.0 * s
    y_c = y + 30.0 * s

    thick = 3.0 * s
    r_cap = thick / 2.0

    # Draw speaker body outline (Driver on the left, mouth opening to the right)
    rl.draw_line_ex(rl.Vector2(x_c - 3.0 * s, y_c - 18.0 * s), rl.Vector2(x_c - 3.0 * s, y_c + 18.0 * s), thick, color)
    rl.draw_line_ex(rl.Vector2(x_c - 3.0 * s, y_c - 18.0 * s), rl.Vector2(x_c - 12.0 * s, y_c - 7.0 * s), thick, color)
    rl.draw_line_ex(rl.Vector2(x_c - 3.0 * s, y_c + 18.0 * s), rl.Vector2(x_c - 12.0 * s, y_c + 7.0 * s), thick, color)
    rl.draw_line_ex(rl.Vector2(x_c - 12.0 * s, y_c - 7.0 * s), rl.Vector2(x_c - 22.0 * s, y_c - 7.0 * s), thick, color)
    rl.draw_line_ex(rl.Vector2(x_c - 12.0 * s, y_c + 7.0 * s), rl.Vector2(x_c - 22.0 * s, y_c + 7.0 * s), thick, color)
    rl.draw_line_ex(rl.Vector2(x_c - 22.0 * s, y_c - 7.0 * s), rl.Vector2(x_c - 22.0 * s, y_c + 7.0 * s), thick, color)

    # Draw circles at speaker body vertices to round the corners beautifully
    rl.draw_circle_v(rl.Vector2(x_c - 3.0 * s, y_c - 18.0 * s), r_cap, color)
    rl.draw_circle_v(rl.Vector2(x_c - 3.0 * s, y_c + 18.0 * s), r_cap, color)
    rl.draw_circle_v(rl.Vector2(x_c - 12.0 * s, y_c - 7.0 * s), r_cap, color)
    rl.draw_circle_v(rl.Vector2(x_c - 12.0 * s, y_c + 7.0 * s), r_cap, color)
    rl.draw_circle_v(rl.Vector2(x_c - 22.0 * s, y_c - 7.0 * s), r_cap, color)
    rl.draw_circle_v(rl.Vector2(x_c - 22.0 * s, y_c + 7.0 * s), r_cap, color)

    # Helper to draw rounded concentric sound wave arcs
    def draw_rounded_arc(cx: float, cy: float, r: float, start_deg: float, end_deg: float):
      draw_ellipse_arc(cx, cy, r, r, 0.0, start_deg, end_deg, thick)
      rad_start = math.radians(start_deg)
      rad_end = math.radians(end_deg)
      rl.draw_circle_v(rl.Vector2(cx + r * math.cos(rad_start), cy + r * math.sin(rad_start)), r_cap, color)
      rl.draw_circle_v(rl.Vector2(cx + r * math.cos(rad_end), cy + r * math.sin(rad_end)), r_cap, color)

    # Draw three concentric sound wave arcs (curving on the right side)
    draw_rounded_arc(x_c - 3.0 * s, y_c, 8.0 * s, -25.0, 25.0)
    draw_rounded_arc(x_c - 3.0 * s, y_c, 15.0 * s, -37.5, 37.5)
    draw_rounded_arc(x_c - 3.0 * s, y_c, 22.0 * s, -50.0, 50.0)
  elif key == "steering":
    # Driving Controls: Minimalist 3-spoke steering wheel
    x_c = x + 30.0 * s
    y_c = y + 30.0 * s

    # Outer rim
    rl.draw_ring(rl.Vector2(x_c, y_c), 19.5 * s, 22.5 * s, 0.0, 360.0, 96, color)

    # Center hub
    rl.draw_circle_v(rl.Vector2(x_c, y_c), 4.5 * s, color)

    # Three clean, single-line spokes
    rl.draw_line_ex(rl.Vector2(x_c - 4.5 * s, y_c), rl.Vector2(x_c - 19.5 * s, y_c), 2.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 4.5 * s, y_c), rl.Vector2(x_c + 19.5 * s, y_c), 2.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c, y_c + 4.5 * s), rl.Vector2(x_c, y_c + 19.5 * s), 2.5 * s, color)
  elif key == "system":
    # System Settings: Interlocking minimalist gears (Large & Small)
    cx1, cy1 = x + 24.0 * s, y + 24.0 * s
    cx2, cy2 = x + 38.5 * s, y + 38.5 * s

    # Gear 1 (Large - 6 cogs)
    rl.draw_ring(rl.Vector2(cx1, cy1), 5.0 * s, 9.0 * s, 0.0, 360.0, 48, color)
    for i in range(6):
      angle_rad = math.radians(i * 60.0)
      cos_a = math.cos(angle_rad)
      sin_a = math.sin(angle_rad)
      rl.draw_line_ex(
        rl.Vector2(cx1 + cos_a * 9.0 * s, cy1 + sin_a * 9.0 * s),
        rl.Vector2(cx1 + cos_a * 13.0 * s, cy1 + sin_a * 13.0 * s),
        4.5 * s,
        color
      )

    # Gear 2 (Small - 5 cogs)
    rl.draw_ring(rl.Vector2(cx2, cy2), 3.0 * s, 6.0 * s, 0.0, 360.0, 48, color)
    for j in range(5):
      angle_rad = math.radians(36.0 + j * 72.0)
      cos_a = math.cos(angle_rad)
      sin_a = math.sin(angle_rad)
      rl.draw_line_ex(
        rl.Vector2(cx2 + cos_a * 6.0 * s, cy2 + sin_a * 6.0 * s),
        rl.Vector2(cx2 + cos_a * 9.5 * s, cy2 + sin_a * 9.5 * s),
        3.5 * s,
        color
      )
  elif key == "display":
    # Appearance Settings: A horizontal control panel rectangle with 4 vertical slider tracks and thumbs at various levels
    x_c = x + 30.0 * s
    y_c = y + 30.0 * s

    thick = 2.5 * s
    inner_thick = 1.5 * s
    thumb_r = 2.5 * s

    # 1. Draw outer rounded rectangle outline manually for perfect geometry and scaling
    # Top and bottom horizontal lines
    rl.draw_line_ex(rl.Vector2(x_c - 17.0 * s, y_c - 16.0 * s), rl.Vector2(x_c + 17.0 * s, y_c - 16.0 * s), thick, color)
    rl.draw_line_ex(rl.Vector2(x_c - 17.0 * s, y_c + 16.0 * s), rl.Vector2(x_c + 17.0 * s, y_c + 16.0 * s), thick, color)
    # Left and right vertical lines
    rl.draw_line_ex(rl.Vector2(x_c - 22.0 * s, y_c - 11.0 * s), rl.Vector2(x_c - 22.0 * s, y_c + 11.0 * s), thick, color)
    rl.draw_line_ex(rl.Vector2(x_c + 22.0 * s, y_c - 11.0 * s), rl.Vector2(x_c + 22.0 * s, y_c + 11.0 * s), thick, color)
    # Four corner arcs
    draw_ellipse_arc(x_c - 17.0 * s, y_c - 11.0 * s, 5.0 * s, 5.0 * s, 0.0, 180.0, 270.0, thick)
    draw_ellipse_arc(x_c + 17.0 * s, y_c - 11.0 * s, 5.0 * s, 5.0 * s, 0.0, 270.0, 360.0, thick)
    draw_ellipse_arc(x_c + 17.0 * s, y_c + 11.0 * s, 5.0 * s, 5.0 * s, 0.0, 0.0, 90.0, thick)
    draw_ellipse_arc(x_c - 17.0 * s, y_c + 11.0 * s, 5.0 * s, 5.0 * s, 0.0, 90.0, 180.0, thick)

    # 2. Draw 4 vertical slider tracks
    x_positions = [x_c - 12.0 * s, x_c - 4.0 * s, x_c + 4.0 * s, x_c + 12.0 * s]
    for px in x_positions:
      rl.draw_line_ex(rl.Vector2(px, y_c - 9.0 * s), rl.Vector2(px, y_c + 9.0 * s), inner_thick, color)

    # 3. Draw slider thumbs (solid circles at different vertical levels)
    # Track 1 (left): low level
    rl.draw_circle_v(rl.Vector2(x_positions[0], y_c + 3.0 * s), thumb_r, color)
    # Track 2 (mid-left): center level
    rl.draw_circle_v(rl.Vector2(x_positions[1], y_c - 2.0 * s), thumb_r, color)
    # Track 3 (mid-right): low level
    rl.draw_circle_v(rl.Vector2(x_positions[2], y_c + 3.0 * s), thumb_r, color)
    # Track 4 (right): high level
    rl.draw_circle_v(rl.Vector2(x_positions[3], y_c - 6.0 * s), thumb_r, color)
  elif key == "vehicle":
    # Vehicle Settings: Little Car
    v_front = rl.Vector2(x + 5.0 * s, y + 34.0 * s)
    v_hood_end = rl.Vector2(x + 15.0 * s, y + 27.0 * s)
    v_cabin_end = rl.Vector2(x + 37.0 * s, y + 17.0 * s)
    v_rear = rl.Vector2(x + 55.0 * s, y + 33.0 * s)

    # Car body panels (continuous Bezier curve profile)
    rl.draw_line_ex(rl.Vector2(x + 5.0 * s, y + 38.0 * s), v_front, 2.0 * s, color)
    draw_bezier(v_front, rl.Vector2(x + 9.0 * s, y + 32.0 * s), v_hood_end, 2.0 * s)
    draw_bezier(v_hood_end, rl.Vector2(x + 25.0 * s, y + 17.0 * s), v_cabin_end, 2.0 * s)
    draw_bezier(v_cabin_end, rl.Vector2(x + 48.0 * s, y + 20.0 * s), v_rear, 2.0 * s)
    rl.draw_line_ex(v_rear, rl.Vector2(x + 55.0 * s, y + 38.0 * s), 2.0 * s, color)

    # Underbody frame
    rl.draw_line_ex(rl.Vector2(x + 5.0 * s, y + 38.0 * s), rl.Vector2(x + 11.5 * s, y + 38.0 * s), 2.0 * s, color)
    rl.draw_line_ex(rl.Vector2(x + 21.5 * s, y + 38.0 * s), rl.Vector2(x + 38.5 * s, y + 38.0 * s), 2.0 * s, color)
    rl.draw_line_ex(rl.Vector2(x + 48.5 * s, y + 38.0 * s), rl.Vector2(x + 55.0 * s, y + 38.0 * s), 2.0 * s, color)

    # Wheels (clean hollow rings with high segment counts)
    rl.draw_ring(rl.Vector2(x + 16.5 * s, y + 38.0 * s), 2.5 * s, 5.0 * s, 0.0, 360.0, 48, color)
    rl.draw_ring(rl.Vector2(x + 43.5 * s, y + 38.0 * s), 2.5 * s, 5.0 * s, 0.0, 360.0, 48, color)

    # Window cutout details
    rl.draw_line_ex(rl.Vector2(x + 18.0 * s, y + 25.0 * s), rl.Vector2(x + 36.0 * s, y + 25.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x + 36.0 * s, y + 25.0 * s), rl.Vector2(x + 34.0 * s, y + 20.0 * s), 1.5 * s, color)
  elif key == "aicar":
    # AI Car: Half car silhouette on the left (Sedan), circuit traces with contact pads on the right
    x_c = x + 30.0 * s
    y_c = y + 30.0 * s

    def draw_pad(cx: float, cy: float, size: float):
      half = size / 2.0
      rl.draw_line_ex(rl.Vector2(cx - half, cy - half), rl.Vector2(cx + half, cy - half), 1.5 * s, color)
      rl.draw_line_ex(rl.Vector2(cx + half, cy - half), rl.Vector2(cx + half, cy + half), 1.5 * s, color)
      rl.draw_line_ex(rl.Vector2(cx + half, cy + half), rl.Vector2(cx - half, cy + half), 1.5 * s, color)
      rl.draw_line_ex(rl.Vector2(cx - half, cy + half), rl.Vector2(cx - half, cy - half), 1.5 * s, color)
      dot_half = size / 4.0
      rl.draw_rectangle_rec(rl.Rectangle(cx - dot_half, cy - dot_half, size / 2.0, size / 2.0), color)

    # 1. Soft Dashed Vertical Splitter (Blending/Transition Effect)
    r = color.r if hasattr(color, "r") else color[0]
    g = color.g if hasattr(color, "g") else color[1]
    b = color.b if hasattr(color, "b") else color[2]
    divider_color = rl.Color(r, g, b, 60)  # ~24% opacity
    dash_len = 1.0 * s
    gap_len = 2.0 * s
    curr_y = y_c - 16.0 * s
    end_y = y_c + 12.0 * s
    while curr_y < end_y:
      rl.draw_line_ex(rl.Vector2(x_c, curr_y), rl.Vector2(x_c, min(curr_y + dash_len, end_y)), 1.5 * s, divider_color)
      curr_y += dash_len + gap_len

    # 2. Right Side (AI Circuit Traces & Pads)
    # Trace 1 (Top - Aligned with roofline)
    rl.draw_line_ex(rl.Vector2(x_c, y_c - 16.0 * s), rl.Vector2(x_c + 8.0 * s, y_c - 16.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 8.0 * s, y_c - 16.0 * s), rl.Vector2(x_c + 14.0 * s, y_c - 20.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 14.0 * s, y_c - 20.0 * s), rl.Vector2(x_c + 22.0 * s, y_c - 20.0 * s), 1.5 * s, color)
    draw_pad(x_c + 24.0 * s, y_c - 20.0 * s, 3.5 * s)

    # Trace 2 (Aligned with window bottom / beltline)
    rl.draw_line_ex(rl.Vector2(x_c, y_c - 5.0 * s), rl.Vector2(x_c + 8.0 * s, y_c - 5.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 8.0 * s, y_c - 5.0 * s), rl.Vector2(x_c + 13.0 * s, y_c - 9.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 13.0 * s, y_c - 9.0 * s), rl.Vector2(x_c + 18.0 * s, y_c - 9.0 * s), 1.5 * s, color)
    draw_pad(x_c + 20.0 * s, y_c - 9.0 * s, 3.5 * s)

    # Trace 3 (Forked - Centered on door)
    rl.draw_line_ex(rl.Vector2(x_c, y_c + 1.0 * s), rl.Vector2(x_c + 6.0 * s, y_c + 1.0 * s), 1.5 * s, color)
    # Fork 3A (Up)
    rl.draw_line_ex(rl.Vector2(x_c + 6.0 * s, y_c + 1.0 * s), rl.Vector2(x_c + 10.0 * s, y_c - 2.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 10.0 * s, y_c - 2.0 * s), rl.Vector2(x_c + 16.0 * s, y_c - 2.0 * s), 1.5 * s, color)
    draw_pad(x_c + 18.0 * s, y_c - 2.0 * s, 3.5 * s)
    # Fork 3B (Down)
    rl.draw_line_ex(rl.Vector2(x_c + 6.0 * s, y_c + 1.0 * s), rl.Vector2(x_c + 10.0 * s, y_c + 4.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 10.0 * s, y_c + 4.0 * s), rl.Vector2(x_c + 16.0 * s, y_c + 4.0 * s), 1.5 * s, color)
    draw_pad(x_c + 18.0 * s, y_c + 4.0 * s, 3.5 * s)

    # Trace 4 (Lower door)
    rl.draw_line_ex(rl.Vector2(x_c, y_c + 7.0 * s), rl.Vector2(x_c + 7.0 * s, y_c + 7.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 7.0 * s, y_c + 7.0 * s), rl.Vector2(x_c + 11.0 * s, y_c + 10.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 11.0 * s, y_c + 10.0 * s), rl.Vector2(x_c + 15.0 * s, y_c + 10.0 * s), 1.5 * s, color)
    draw_pad(x_c + 17.0 * s, y_c + 10.0 * s, 3.5 * s)

    # Trace 5 (Bottom - Aligned with rocker panel)
    rl.draw_line_ex(rl.Vector2(x_c, y_c + 12.0 * s), rl.Vector2(x_c + 8.0 * s, y_c + 12.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 8.0 * s, y_c + 12.0 * s), rl.Vector2(x_c + 14.0 * s, y_c + 18.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c + 14.0 * s, y_c + 18.0 * s), rl.Vector2(x_c + 22.0 * s, y_c + 18.0 * s), 1.5 * s, color)
    draw_pad(x_c + 24.0 * s, y_c + 18.0 * s, 3.5 * s)

    # 3. Left Side (Car Front silhouette - Sleek modern profile with long sweeping windshield)
    # Roof & Windshield (continuous Bezier curve profile)
    p0_roof = rl.Vector2(x_c, y_c - 16.0 * s)
    p1_roof = rl.Vector2(x_c - 12.0 * s, y_c - 16.0 * s)
    p2_roof = rl.Vector2(x_c - 22.0 * s, y_c - 5.0 * s)
    draw_bezier(p0_roof, p1_roof, p2_roof, 2.0 * s)

    # Hood (short, streamlined)
    p0_hood = rl.Vector2(x_c - 22.0 * s, y_c - 5.0 * s)
    p1_hood = rl.Vector2(x_c - 25.0 * s, y_c - 4.5 * s)
    p2_hood = rl.Vector2(x_c - 28.0 * s, y_c - 2.5 * s)
    draw_bezier(p0_hood, p1_hood, p2_hood, 2.0 * s)

    # Nose & Bumper Curve
    p0_nose = rl.Vector2(x_c - 28.0 * s, y_c - 2.5 * s)
    p1_nose = rl.Vector2(x_c - 31.0 * s, y_c + 1.0 * s)
    p2_nose = rl.Vector2(x_c - 28.0 * s, y_c + 12.0 * s)
    draw_bezier(p0_nose, p1_nose, p2_nose, 2.0 * s)

    # Wheel Arch
    draw_ellipse_arc(x_c - 21.0 * s, y_c + 12.0 * s, 7.0 * s, 7.0 * s, 0.0, 180.0, 360.0, 2.0 * s)

    # Underbody / Rocker panel (aligned with Trace 5)
    rl.draw_line_ex(rl.Vector2(x_c - 14.0 * s, y_c + 12.0 * s), rl.Vector2(x_c, y_c + 12.0 * s), 2.0 * s, color)

    # Wheel (hollow ring matching the arch)
    rl.draw_ring(rl.Vector2(x_c - 21.0 * s, y_c + 12.0 * s), 3.5 * s, 5.0 * s, 0.0, 360.0, 24, color)

    # Side Window Details (Parallel sloped wedge, open to the dashed divider at the rear to blend perfectly)
    p0_win = rl.Vector2(x_c, y_c - 13.0 * s)
    p1_win = rl.Vector2(x_c - 10.0 * s, y_c - 13.0 * s)
    p2_win = rl.Vector2(x_c - 18.0 * s, y_c - 5.0 * s)
    draw_bezier(p0_win, p1_win, p2_win, 1.5 * s)
    rl.draw_line_ex(rl.Vector2(x_c - 18.0 * s, y_c - 5.0 * s), rl.Vector2(x_c, y_c - 5.0 * s), 1.5 * s, color)
    rl.draw_line_ex(rl.Vector2(x_c - 9.0 * s, y_c - 11.4 * s), rl.Vector2(x_c - 9.0 * s, y_c - 5.0 * s), 1.5 * s, color)
