"""Pinned numeric atlas and the larger stack's saved/touch geometry."""
from dataclasses import replace
from itertools import product
import io
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import numpy as np
from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile, default_font_directory, font_filename, font_path, validate_bitmap_font
from openpilot.starpilot.ui.onroad_customization import default_document, validate_document
from openpilot.starpilot.ui.large_speed_geometry import LARGE_SIGN_SCALE, SIGN_WIDTH, SIGN_HEIGHT, confirmation_bounds
from openpilot.starpilot.ui.onroad_state import ObservationKind, slc_controls, speed_limit_bounds
from openpilot.starpilot.ui.tests.test_speed_stack import road
from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, validate_layout_for_viewport


def test_numeric_subset_is_pinned_and_has_real_tabular_glyphs():
  filename = font_filename(Profile.LARGE, FontRole.SPEED)
  path = font_path(default_font_directory(), filename)
  assert filename == 'DINish-Speed.fnt'
  assert font_filename(Profile.COMPACT, FontRole.SPEED) == 'Inter-Bold.fnt'
  assert font_path(default_font_directory() / 'missing', filename) == path
  validate_bitmap_font(path)
  glyphs = {int(row['id']): row for line in path.read_text().splitlines() if line.startswith('char id=')
            for row in [dict(re.findall(r'(\w+)=(-?\d+)', line))]}
  assert set(map(ord, '0123456789–-?')) <= glyphs.keys()
  assert len({glyphs[ord(char)]['xadvance'] for char in '0123456789'}) == 1
  one = glyphs[ord('1')]
  x, y, w, h = (int(one[key]) for key in ('x', 'y', 'width', 'height'))
  with Image.open(path.with_suffix('.png')) as atlas:
    bounds = atlas.crop((x, y, x + w, y + h)).getchannel('A').getbbox()
    assert bounds is not None and bounds[0] > 0


def test_approved_limit_offset_and_action_sizes_fit_actual_fonts():
  from openpilot.starpilot.ui.large_speed_geometry import ACTION_TEXT_SIZE, LARGE_SIGN_SCALE, LIMIT_RECTS
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, "Native speed-group font fixture")
  try:
    for profile in Profile:
      with BitmapFonts(profile, default_font_directory()) as fonts:
        large = profile == Profile.LARGE
        scale = LARGE_SIGN_SCALE if large else 1
        text_scale = scale * Profile.COMPACT.font_scale / profile.font_scale
        limit_size, offset_size = 58 * text_scale, 36 * text_scale
        for value, offset in (('65', '+5'), ('120', '+10'), ('130', '-10'), ('145', '-137')):
          a, b = fonts.horizontal_ink(value, FontRole.BOLD, limit_size)
          value_width = b - a
          a, b = fonts.horizontal_ink(offset, FontRole.MEDIUM, offset_size)
          offset_width = b - a
          width = max(value_width, offset_width) + 20 * scale
          assert width <= LIMIT_RECTS[profile][2]
        action_width = 308 if large else 184
        assert all(fonts.measure(label, FontRole.MEDIUM, ACTION_TEXT_SIZE[profile]).width + 4 <= action_width
                   for label in ('Accept', 'Decline'))
        ppi = (2160**2 + 1080**2)**.5 / 6 if large else 300
        for text, role, size, minimum in (('65', FontRole.BOLD, limit_size, 2.5 if large else 2.8),
                                         ('+5', FontRole.MEDIUM, offset_size, 1.5 if large else 2.0),
                                         ('H', FontRole.MEDIUM, ACTION_TEXT_SIZE[profile], 2.8)):
          top, bottom = fonts.vertical_ink(text, role, size)
          assert (bottom - top) * 25.4 / ppi >= minimum
  finally:
    if owns_context:
      rl.close_window()


def test_large_set_and_limit_share_typography_at_the_saved_position():
  from unittest.mock import patch
  from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
  from openpilot.starpilot.ui.layout_preview_renderer import sample_state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, "Native speed-group placement fixture")
  try:
    with BitmapFonts(Profile.LARGE, default_font_directory()) as fonts:
      for metric, value, show_set in product((False, True), (60, 99, 100, 199, None), (False, True)):
        document = default_document()
        document['showMaxSpeed'] = show_set
        document['layouts']['large']['cruise_limits'].update(x=188, y=95)
        current = sample_state('engaged', document)
        factor = 3.6 if metric else 2.2369362921
        observation = replace(current.speed_limit, speed_limit_mps=120 / factor,
                              presentation_adjusted_limit_mps=130 / factor, effective_cluster_target_mps=130 / factor)
        current = replace(current, metric=metric, cruise_kph=value / factor * 3.6 if value else None,
                          speed_limit=observation)
        calls = []
        with patch.object(fonts, 'draw', lambda *args, calls=calls: calls.append(args)), patch.object(rl, 'draw_line_ex') as lines:
          UnifiedSpeedWidget(fonts).render(rl.Rectangle(30, 30, 1800, 1020), current)
        calls = [call for call in calls if call[-1].a == 255]
        posted = next(call for call in calls if call[0] == '130' and call[1] == FontRole.SET_SPEED and call[3] > 388)
        left, right = fonts.horizontal_ink(posted[0], posted[1], posted[2])
        assert right-left <= 108*LARGE_SIGN_SCALE
        assert abs(posted[3] + (left + right) / 2 - (418 + SIGN_WIDTH/2*LARGE_SIGN_SCALE)) <= .5
        a, b = fonts.vertical_ink('130', FontRole.SET_SPEED, posted[2])
        assert 60 <= b - a <= 70
        assert abs(posted[4]+(a+b)/2-(95+61*LARGE_SIGN_SCALE)) <= .5
        assert {'LIMIT', '120 +10'} <= {call[0] for call in calls}
        set_calls = [call for call in calls if call[1] == FontRole.SET_SPEED and call[3] < 388]
        assert bool(set_calls) == (show_set and value is not None)
        if set_calls:
          text, role, size, x, y, _ = set_calls[0]
          assert fonts.measure(text, role, size).width <= 200
          left, right = fonts.horizontal_ink(text, role, size)
          assert x + (left + right) / 2 == pytest.approx(280)
          assert x + right + 20 <= 418
          top, bottom = fonts.vertical_ink(text, role, size)
          assert (role, size, y) == (posted[1], posted[2], posted[4])
          heading = next(call for call in calls if call[0] == 'SET')
          limit = next(call for call in calls if call[0] == 'LIMIT')
          assert heading[1:3] == limit[1:3]
          assert heading[4] + sum(fonts.vertical_ink(*heading[:3])) / 2 == pytest.approx(
            limit[4] + sum(fonts.vertical_ink(*limit[:3])) / 2)
        assert not any('mph' in call[0] or 'km/h' in call[0] for call in calls)
        if show_set and value is None:
          start, end, stroke, color = lines.call_args.args
          assert (start.x, start.y, end.x, end.y, stroke) == pytest.approx(
            (280 - 20 * LARGE_SIGN_SCALE, 95 + 61 * LARGE_SIGN_SCALE,
             280 + 20 * LARGE_SIGN_SCALE, 95 + 61 * LARGE_SIGN_SCALE, 5 * LARGE_SIGN_SCALE))
          assert (color.r, color.g, color.b, color.a) == (255, 255, 255, 255)
          assert all(call[0] not in ('–', '?') for call in calls)
        else:
          lines.assert_not_called()
  finally:
    if owns_context:
      rl.close_window()


@pytest.mark.parametrize('pending', [False, True])
def test_set_and_slc_match_font_and_baseline_on_both_displays(pending):
  from unittest.mock import patch
  from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
  from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
  from openpilot.starpilot.ui.tests.test_unified_speed_native import state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'Shared SET and SLC typography')
  try:
    for profile in Profile:
      with BitmapFonts(profile, default_font_directory()) as fonts:
        widget = UnifiedSpeedWidget(fonts) if profile == Profile.LARGE else CompactHudRenderer(fonts, Path('/unused'))
        try:
          for value in (9, 65, 128):
            current = state(cruise_kph=140)
            current = replace(current, speed_limit=replace(current.speed_limit, speed_limit_mps=value / 3.6,
              pending_speed_limit_mps=value / 3.6 if pending else None,
              pending_adjusted_limit_mps=value / 3.6 if pending else None,
              presentation_adjusted_limit_mps=value / 3.6,
              limiting_max_set=True, effective_cluster_target_mps=value / 3.6))
            calls = []
            with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)):
              if isinstance(widget, UnifiedSpeedWidget):
                widget.render(rl.Rectangle(0, 0, 2160, 1080), current)
              else:
                with patch.object(widget, '_set_speed_opacity', return_value=1.):
                  widget.render_max_speed(current)
                widget._speed_limit_sign(current)
              if pending:
                sign = widget.sign if isinstance(widget, UnifiedSpeedWidget) else widget._speed_limit_widget
                sign.render_confirmation(current)
            rows = {row[0]: row for row in calls if row[-1].a == 255}
            for a, b in (('SET', 'OSM' if pending else 'LIMIT'), ('140', str(value))):
              assert rows[a][1] == rows[b][1]
              assert rows[a][2] == pytest.approx(rows[b][2])
              if rows[a][1] == FontRole.SET_SPEED:
                assert rows[a][4] == pytest.approx(rows[b][4])
              else:
                assert rows[a][4] + sum(fonts.vertical_ink(*rows[a][:3])) / 2 == pytest.approx(
                  rows[b][4] + sum(fonts.vertical_ink(*rows[b][:3])) / 2)
            assert not any('mph' in text or 'km/h' in text for text in rows)
        finally:
          widget.close()
  finally:
    if owns_context:
      rl.close_window()


def test_pending_confirmation_keeps_three_digit_set_speed_clear():
  from unittest.mock import patch
  from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
  from openpilot.starpilot.ui.tests.test_unified_speed_native import state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'Pending confirmation spacing fixture')
  try:
    with BitmapFonts(Profile.LARGE, default_font_directory()) as fonts:
      for metric in (False, True):
        factor = 3.6 if metric else 2.2369362921
        current = state(metric=metric, cruise_kph=100 / factor * 3.6)
        bounds = speed_limit_bounds(Profile.LARGE, current)
        assert bounds is not None
        calls = []
        with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)):
          UnifiedSpeedWidget(fonts).render(rl.Rectangle(30, 30, 1800, 1020), current)
        number = next(call for call in calls if call[0] == '100' and call[1] == FontRole.SET_SPEED and call[-1].a == 255)
        assert number[3] + fonts.horizontal_ink(number[0], number[1], number[2])[1] < bounds[0]
  finally:
    if owns_context:
      rl.close_window()


def test_status_below_set_has_real_ink_clearance_and_fits_its_widget():
  from unittest.mock import patch
  from openpilot.starpilot.ui.onroad_customization import placement, widget_size
  from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
  from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
  from openpilot.starpilot.ui.tests.test_unified_speed_native import state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'SET status clearance')
  try:
    for profile, status, set_speed in product(Profile, ('PEDAL', 'LIMIT 45', 'OVERRIDE 100'), (70, 100, 199)):
      with BitmapFonts(profile, default_font_directory()) as fonts:
        current = state(cruise_kph=set_speed, longitudinal_overridden=status == 'PEDAL', slc_prompt_open=True)
        current = replace(current, speed_limit=replace(current.speed_limit,
          pending_speed_limit_mps=None if status == 'PEDAL' else 50/3.6,
          limiting_max_set=status == 'LIMIT 45', driver_override_active=status == 'OVERRIDE 100',
          override_basis='persistent', effective_cluster_target_mps=(100 if status == 'OVERRIDE 100' else 45)/3.6))
        widget = UnifiedSpeedWidget(fonts) if profile == Profile.LARGE else CompactHudRenderer(fonts, Path('/unused'))
        if isinstance(widget, CompactHudRenderer):
          widget._set_speed_alpha.x = 1
        calls = []
        with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)), \
             patch('openpilot.starpilot.ui.speed_limit_widget.speed_limit_bounds', return_value=None):
          if isinstance(widget, UnifiedSpeedWidget):
            widget.render(rl.Rectangle(30,30,1800,1020), current)
          else:
            widget.render_max_speed(current)
        rows = [row for row in calls if row[-1].a == 255]
        number = next(row for row in rows if row[0] == str(set_speed) and row[1] == FontRole.SET_SPEED)
        caption = next(row for row in rows if row[0] == status)
        bottom = number[4] + fonts.vertical_ink(*number[:3])[1]
        top = caption[4] + fonts.vertical_ink(*caption[:3])[0]
        assert top - bottom >= 6, (profile, status, set_speed)
        key = 'cruise_limits' if profile == Profile.LARGE else 'max_speed'
        saved = placement(current.customization, profile, key)
        width, height = widget_size(current.customization, profile, key)
        assert caption[4] + fonts.vertical_ink(*caption[:3])[1] <= saved['y'] + height - 12
        scale = LARGE_SIGN_SCALE if profile == Profile.LARGE else 1
        assert 16 <= caption[2] * profile.font_scale / Profile.COMPACT.font_scale / scale <= 22 + 1e-9
        left, right = fonts.horizontal_ink(*caption[:3])
        number_left, number_right = fonts.horizontal_ink(*number[:3])
        if profile == Profile.COMPACT:
          assert saved['x'] + 8 <= caption[3] + left < caption[3] + right <= saved['x'] + width - 8
          assert caption[3] + (left + right) / 2 == pytest.approx(saved['x'] + width / 2)
          assert number[3] + (number_left + number_right) / 2 == pytest.approx(saved['x'] + width / 2)
        else:
          assert caption[3] + (left + right) / 2 == pytest.approx(saved['x'] + 92)
          assert number[3] + (number_left + number_right) / 2 == pytest.approx(saved['x'] + 92)
        widget.close()
  finally:
    if owns_context:
      rl.close_window()


def test_sign_status_rows_keep_balanced_insets_with_real_fonts():
  from unittest.mock import patch
  from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget
  from openpilot.starpilot.ui.tests.test_unified_speed_native import state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'Sign status margins')
  try:
    for profile in Profile:
      with BitmapFonts(profile, default_font_directory()) as fonts:
        widget = SpeedLimitWidget(fonts, profile)
        for variant in ('in_use', 'offset', 'override', 'pedal', 'retained'):
          current = state(cruise_kph=140, longitudinal_overridden=variant == 'pedal')
          target = 128 if variant == 'override' else 110 if variant == 'offset' else 100
          current = replace(current, speed_limit=replace(current.speed_limit,
            source='vision', speed_limit_mps=100/3.6, pending_speed_limit_mps=None,
            accepted_source='vision', accepted_speed_limit_mps=100/3.6, accepted_adjusted_limit_mps=target/3.6,
            presentation_adjusted_limit_mps=target/3.6, effective_cluster_target_mps=target/3.6,
            limiting_max_set=True, driver_override_active=variant == 'override', override_basis='persistent'))
          if variant == 'pedal':
            current.customization['showMaxSpeed'] = False
            current.customization['layouts']['compact']['max_speed']['enabled'] = False
          if variant == 'retained':
            current = replace(current, speed_limit=replace(current.speed_limit, kind=ObservationKind.STALE,
              speed_limit_mps=None, last_received_speed_limit_mps=100/3.6, last_received_source='vision'))
          calls = []
          with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)):
            widget.render(current)
          if variant == 'retained':
            assert [call[0] for call in calls if call[-1].a == 255] == ['LIMIT', '100', 'LAST VISION']
          bounds = speed_limit_bounds(profile, current)
          assert bounds is not None
          x, y = bounds[0] + 12, bounds[1] + 12
          scale = widget.scale
          boxes = []
          for text, role, size, left, top, color in calls:
            if color.a != 255:
              continue
            a, b = fonts.horizontal_ink(text, role, size)
            c, d = fonts.vertical_ink(text, role, size)
            box = left+a, top+c, left+b, top+d
            # Keep real glyph ink clear of the portrait outline, including both footer rows.
            assert x+9.5*scale <= box[0] < box[2] <= x+118.5*scale, (profile, variant, text)
            assert y+3.5*scale <= box[1] < box[3] <= y+126.5*scale, (profile, variant, text)
            assert (box[0]+box[2])/2 == pytest.approx(x+SIGN_WIDTH/2*scale, abs=.5)
            boxes.append(box)
          assert len(boxes) >= 2
          assert all(second[1] - first[3] >= 4*scale for first, second in zip(boxes, boxes[1:], strict=False))
        widget.close()
  finally:
    if owns_context:
      rl.close_window()


def test_set_limited_sign_matches_approved_spacing_and_existing_touch_area():
  from unittest.mock import patch
  from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget, draw_set_speed, IN_USE
  from openpilot.starpilot.ui.tests.test_unified_speed_native import state
  from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'SET-limited sign margins')
  try:
    for profile in Profile:
      with BitmapFonts(profile, default_font_directory()) as fonts:
        widget = SpeedLimitWidget(fonts, profile)
        widget.shapes = Mock()
        scale = widget.scale
        for metric, limited, vienna in product((False, True), (False, True), (False, True)):
          if vienna and not limited:
            continue  # Regulatory Vienna geometry has its own existing checks.
          factor = 3.6 if metric else 2.2369362921
          target, selected = (120, 100 if limited else 130) if metric else (55, 45 if limited else 70)
          current = state(metric=metric, cruise_kph=selected / factor * 3.6)
          current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna),
            speed_limit=replace(current.speed_limit, source='dashboard', speed_limit_mps=target / factor,
              accepted_source='dashboard', accepted_speed_limit_mps=target / factor,
              accepted_adjusted_limit_mps=target / factor, presentation_adjusted_limit_mps=target / factor,
              effective_cluster_target_mps=target / factor, effective_cap_mps=target / factor,
              pending_speed_limit_mps=None, limiting_max_set=not limited))
          shown = resolve_unified_speed(current)
          bounds = speed_limit_bounds(profile, current)
          assert bounds is not None
          x, y = bounds[0] + 12, bounds[1] + 12
          cx = x + SIGN_WIDTH / 2 * scale
          outline_scale, cy = (13 / 12, 70) if limited else (1, 65)
          calls = []
          with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)):
            draw_set_speed(fonts, shown, cx - 200 * scale, y, rl.WHITE)
            widget.render(current)
          rows = [row for row in calls if row[-1].a == 255]
          assert [row[0] for row in rows] == ['SET', str(selected), 'LIMIT', str(target), *(
            ['SET IS LOW', 'RAISE SET'] if limited else ['DASH'])]
          boxes = []
          for text, role, size, left, top, _ in rows:
            a, b = fonts.horizontal_ink(text, role, size)
            c, d = fonts.vertical_ink(text, role, size)
            boxes.append((left+a, top+c, left+b, top+d))
          assert rows[1][1:3] == rows[3][1:3]
          for first, second in ((0, 2), (1, 3)):
            assert sum(boxes[first][1::2]) == pytest.approx(sum(boxes[second][1::2]), abs=.01)
          half_width, half_height = (SIGN_WIDTH / 2 - 5) * outline_scale * scale, (SIGN_HEIGHT / 2 - 5) * outline_scale * scale
          radius = 1.15 if profile == Profile.LARGE else 1.
          for left, top, right, bottom in boxes[2:]:
            assert (left+right)/2 == pytest.approx(cx, abs=.5)
            assert min(left-(cx-half_width), cx+half_width-right,
                       top-(y+cy*scale-half_height), y+cy*scale+half_height-bottom)-radius >= (8 if limited else 7.5)*scale
          assert boxes[-2][3] + 5*scale <= boxes[-1][1]
          assert abs((boxes[2][1]-(y+cy*scale-half_height)) - (y+cy*scale+half_height-boxes[-1][3])) <= scale
          # The painted stroke plus its contour remains inside the existing tap target.
          outer_width, outer_height = (SIGN_WIDTH/2-1)*outline_scale*scale, (SIGN_HEIGHT/2-1)*outline_scale*scale
          assert bounds[0] <= cx-outer_width < cx+outer_width <= bounds[2]
          assert bounds[1] <= y+cy*scale-outer_height < y+cy*scale+outer_height <= bounds[3]
          accent = widget.shapes.outline.call_args.args[2]
          assert (accent.r == IN_USE.r) == (not limited)
        widget.close()
  finally:
    if owns_context:
      rl.close_window()


def test_galaxy_card_pixels_align_without_reading_font_textures():
  from unittest.mock import patch
  from openpilot.starpilot.ui.layout_preview_renderer import _Canvas, sample_state
  from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
  from openpilot.starpilot.system.starport.projection_layout import (
    default_layout_for_viewport, projection_customization, validate_layout_for_viewport)

  original_readback = rl.load_image_from_texture

  def mobile_readback(texture):
    image = original_readback(texture)
    # Raylib's GLES texture readback unbinds the framebuffer instead of restoring
    # the active preview target. Reproduce that side effect on desktop too.
    rl.rl_disable_framebuffer()
    return image

  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'Galaxy card alignment fixture')
  target = rl.load_render_texture(*Profile.LARGE.size)
  try:
    for align, projection in product(('center', 'right'), (False, True)):
      # Start with cold font metrics, as when Galaxy creates its preview canvas.
      with BitmapFonts(Profile.LARGE, default_font_directory()) as fonts:
        document = default_document()
        document['speedAlign'] = align
        viewport = (1920, 1080) if projection else None
        if projection:
          layout = default_layout_for_viewport(viewport)
          layout['speedAlign'] = align
          layout['widgets']['cruise_limits']['opacity'] = 50
          document = projection_customization(validate_layout_for_viewport(layout, viewport), document)
        else:
          document['widgetColors']['large']['cruise_limits'] = {'cardFill': '#10191F80'}
          document = validate_document(document)
        state = sample_state('engaged', document)  # MAX 55 / Your ceiling, SLC 49.
        widget = UnifiedSpeedWidget(fonts)
        canvas = _Canvas.__new__(_Canvas)
        canvas.profile, canvas.viewport, canvas.fonts, canvas.target = Profile.LARGE, viewport, fonts, target
        with patch.object(canvas, 'view',
                          SimpleNamespace(_ordered_widgets=lambda content, state, widget=widget, **kw: widget.render(content, state)), create=True), \
             patch.object(canvas, '_settle', lambda state: None), \
             patch.dict('openpilot.starpilot.ui.layout_preview_renderer.RUNTIME_PREVIEW_WIDGETS', large=('cruise_limits',)), \
             patch('openpilot.starpilot.system.starport.projection_layout.SPRITE_WIDGETS', ()), \
             patch.object(rl, 'load_image_from_texture', side_effect=mobile_readback) as readback:
          png = canvas.render_widget_art(state, document['layouts']['large'])
          # A saved 50% background must show on the first frame, without a slider
          # edit triggering another render after the font metrics are warm.
          assert png == canvas.render_widget_art(state, document['layouts']['large'])
          # Only the completed preview is read; measuring glyphs must never change its framebuffer.
          assert readback.call_count == 2
          assert readback.call_args.args[0].id == target.texture.id
        with Image.open(io.BytesIO(png)) as image:
          pixels = np.asarray(image.convert('RGBA'))
        assert pixels[100, 10, 3] == 0  # Unboxed widget art keeps the camera visible.
        ink = pixels[..., 3] > 200
        assert ink.any() and ink[:, 24:160].any() and ink[:, 184:].any()

  finally:
    rl.unload_render_texture(target)
    if owns_context:
      rl.close_window()


def test_native_signs_offsets_and_captions_fit_their_touch_targets():
  from unittest.mock import Mock, patch
  from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
  from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
  from openpilot.starpilot.ui.onroad_state import speed_limit_bounds
  from openpilot.starpilot.ui.tests.test_unified_speed_native import state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'SLC caption and touch fixture')
  try:
    for profile in Profile:
      with BitmapFonts(profile, default_font_directory()) as fonts:
        for metric, moved, elapsed in product((False, True), (False, True), (0, 750_000_000, 1_500_000_000)):
          current = state(metric=metric, observed_ns=10_000_000_000+elapsed)
          current = replace(current, speed_limit=replace(current.speed_limit, action_enabled=False))
          factor = 3.6 if metric else 2.2369362921
          current = replace(current, speed_limit=replace(current.speed_limit, speed_limit_mps=120/factor,
            pending_speed_limit_mps=120/factor, pending_adjusted_limit_mps=130/factor, presentation_adjusted_limit_mps=130/factor))
          if moved:
            for key in (('cruise_limits',) if profile == Profile.LARGE else ('max_speed', 'speed_limit')):
              saved = current.customization['layouts'][profile][key]
              saved['x'] += 100 if profile == Profile.LARGE else 16
              saved['y'] += 20 if profile == Profile.LARGE else 4
          calls = []
          with patch.object(fonts, 'draw', lambda *args, calls=calls: calls.append(args)), patch.object(rl, 'draw_line_ex'):
            if profile == Profile.LARGE:
              UnifiedSpeedWidget(fonts).render(rl.Rectangle(30, 30, 1800, 1020), current)
            else:
              hud = CompactHudRenderer(fonts, Mock())
              hud._set_speed_opacity(current, current.observed_ns)
              hud._set_speed_alpha.x = 1.
              hud.render_max_speed(current)
              hud._speed_limit_sign(current)
          ink = {call[0]: (call[3]+fonts.horizontal_ink(call[0], call[1], call[2])[0], call[4]+fonts.vertical_ink(call[0], call[1], call[2])[0],
                          call[3]+fonts.horizontal_ink(call[0], call[1], call[2])[1],
                          call[4]+fonts.vertical_ink(call[0], call[1], call[2])[1])
                 for call in calls if call[-1].a == 255}
          if profile == Profile.LARGE:
            assert ink['SET'][2] < ink['OSM'][0]
          else:
            assert 'SET' not in ink and 'OSM' in ink
          assert ink['120'][3] < ink['+10'][1]
          bounds = speed_limit_bounds(profile, current)
          assert bounds is not None
          left, top, right, bottom = bounds
          for label in ('OSM', '120', '+10'):
            x1, y1, x2, y2 = ink[label]
            assert left < x1 < x2 < right and top <= y1 < y2 < bottom
          assert not slc_controls(profile, replace(current, slc_prompt_open=True))
  finally:
    if owns_context:
      rl.close_window()


@pytest.mark.parametrize('profile', list(Profile))
def test_preview_sprite_keeps_the_full_confirmation_override_and_set_limited_sign(profile):
  import json
  from unittest.mock import patch
  from openpilot.starpilot.ui.layout_preview_renderer import _Canvas, _straight_rgba, sample_state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'SLC preview crop fixture')
  canvas = _Canvas(profile)
  owner = 'speed_limit' if profile == Profile.COMPACT else 'cruise_limits'
  positions = (12, 16) if profile == Profile.COMPACT else (75, 95)
  width, height = profile.size
  try:
    for y, scene, vienna in product(positions, ('slc_notice', 'slc_pending', 'slc_retained', 'slc_display', 'slc_max'), (False, True)):
      document = default_document()
      document['layouts'][profile][owner]['y'] = y
      assert validate_document(document)['layouts'][profile][owner]['y'] == y
      state = sample_state(scene, document)
      state = replace(state, appearance=replace(state.appearance, use_vienna_sign=vienna))
      with patch.dict('openpilot.starpilot.ui.layout_preview_renderer.RUNTIME_PREVIEW_WIDGETS', {str(profile): (owner,)}):
        png = canvas.render_widget_art(state, document['layouts'][profile])
      with Image.open(io.BytesIO(png)) as sheet:
        slot = json.loads(sheet.info['starpilot-widget-art'])['sprites'][owner]
        sprite = sheet.crop((slot['x'], slot['y'], slot['x']+slot['width'], slot['y']+slot['height']))
        rebuilt = Image.new('RGBA', profile.size)
        rebuilt.paste(sprite, (round(document['layouts'][profile][owner]['x']+slot['offsetX']), round(y+slot['offsetY'])))
      image = rl.load_image_from_texture(canvas.target.texture)
      try:
        rl.image_flip_vertical(image)
        direct = np.frombuffer(_straight_rgba(bytes(image.data[:width*height*4])), np.uint8).reshape(height, width, 4)
      finally:
        rl.unload_image(image)
      assert np.array_equal(direct[..., 3], np.asarray(rebuilt)[..., 3])
      if profile == Profile.COMPACT:
        if scene in ('slc_notice', 'slc_pending'):
          assert (slot['offsetX'], slot['offsetY'], slot['width'], slot['height']) == (-172, -8, 328, 159)
        else:
          assert (slot['offsetX'], slot['offsetY'], slot['width'], slot['height']) == (-12, -12, SIGN_WIDTH+24, SIGN_HEIGHT+20)
      assert np.any(direct[..., 3]), (profile, y, scene)
  finally:
    canvas.close()
    if owns_context:
      rl.close_window()


def test_large_sources_and_confirmation_text_have_clearance_below_both_signs():
  from unittest.mock import patch
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
  from openpilot.starpilot.ui.onroad_state import SourceReading
  from openpilot.starpilot.ui.tests.test_unified_speed_native import state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'Large sign sources and confirmation spacing')
  try:
    with BitmapFonts(Profile.LARGE, default_font_directory()) as fonts:
      widget = UnifiedSpeedWidget(fonts)
      view = OnroadView.__new__(OnroadView)
      view.personality = Mock()
      view.fonts = fonts
      view.unified_speed = widget
      for vienna, separate in product((False, True), repeat=2):
        current = state(slc_prompt_open=True)
        current.customization.update(speedSources=True, speedSourcesCard=separate)
        current = replace(current, customization=validate_document(current.customization),
          appearance=replace(current.appearance, use_vienna_sign=vienna),
          speed_limit=replace(current.speed_limit, source_readings=tuple(
            SourceReading(source, True, 'valid', 50/3.6) for source in ('dashboard', 'map', 'vision'))))
        calls = []
        with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)):
          widget.render(rl.Rectangle(30, 30, 1800, 1020), current)
          view._slc_actions(current)
        foreground = [call for call in calls if call[-1].a == 255]
        action_bottom = max(y+fonts.vertical_ink(text, role, size)[1]
                            for text, role, size, _, y, _ in foreground if text in ('ACCEPT', 'REJECT'))
        source_top = min(y+fonts.vertical_ink(text, role, size)[0]
                         for text, role, size, _, y, _ in foreground if text in ('Dash', 'Map', 'Vision'))
        assert action_bottom+8 <= source_top
      widget.close()
  finally:
    if owns_context:
      rl.close_window()


def test_source_card_action_sprite_contains_both_actual_button_rows():
  import json
  from unittest.mock import patch
  from openpilot.starpilot.ui.layout_preview_renderer import _Canvas, sample_state
  from openpilot.starpilot.ui.onroad import OnroadView

  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'SLC source card widget art')
  target = rl.load_render_texture(*Profile.LARGE.size)
  try:
    with BitmapFonts(Profile.LARGE, default_font_directory()) as fonts:
      document = default_document()
      document.update(speedSources=True, speedSourcesCard=True)
      document = validate_document(document)
      state = sample_state('slc_pending', document)
      view = OnroadView.__new__(OnroadView)
      view.personality = Mock()
      view.fonts = fonts
      from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
      view.unified_speed = UnifiedSpeedWidget(fonts)
      canvas = _Canvas.__new__(_Canvas)
      canvas.profile, canvas.viewport, canvas.fonts, canvas.target = Profile.LARGE, None, fonts, target
      with patch.object(canvas, 'view', SimpleNamespace(_ordered_widgets=lambda content, state, **kw: view._slc_actions(state)), create=True), \
           patch.object(canvas, '_settle', lambda state: None), \
           patch.dict('openpilot.starpilot.ui.layout_preview_renderer.RUNTIME_PREVIEW_WIDGETS', large=('cruise_limits',)):
        png = canvas.render_widget_art(state, document['layouts']['large'])
      with Image.open(io.BytesIO(png)) as image:
        slot = json.loads(image.info['starpilot-widget-art'])['sprites']['cruise_limits']
        assert slot['width'] >= 591 and slot['offsetY'] == -12
        alpha = np.asarray(image.convert('RGBA'))[..., 3]
      origin = document['layouts']['large']['cruise_limits']
      for control in slc_controls(Profile.LARGE, state):
        left, top, right, bottom = control.bounds
        x1, x2 = [round(value - origin['x'] - slot['offsetX'] + slot['x']) for value in (left, right)]
        y1, y2 = [round(value - origin['y'] - slot['offsetY'] + slot['y']) for value in (top, bottom)]
        assert np.count_nonzero(alpha[y1:y2, x1:x2] > 200) > 50, control.label
        assert np.mean(alpha[y1:y2, x1:x2] == 0) > .65, 'Button border and label leave the camera visible'
      assert alpha[100, 382] == 0, 'The space between choices keeps the camera visible'
  finally:
    rl.unload_render_texture(target)
    if owns_context:
      rl.close_window()


def test_both_sign_styles_contain_text_and_arrows_at_each_profile_scale():
  from unittest.mock import patch
  from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget
  from openpilot.starpilot.ui.tests.test_unified_speed_native import state
  from openpilot.starpilot.ui.speed_limit_shapes import ATLAS_PATH, SPRITES, SPRITE_DENSITIES
  glyphs = {}
  painted_pixels = {}
  with Image.open(ATLAS_PATH) as atlas:
    for size in ('large', 'compact'):
      sx, sy, sw, sh = SPRITES[f'arrow_{size}']
      bbox = atlas.crop((sx, sy, sx+sw, sy+sh)).getchannel('A').point(lambda alpha: 255 if alpha > 40 else 0).getbbox()
      assert bbox is not None
      glyphs[size] = tuple(value / SPRITE_DENSITIES.get(f'arrow_{size}', 1)
                           for value in (bbox[0]-sw/2, bbox[1]-sh/2, bbox[2]-sw/2, bbox[3]-sh/2))
      rows, columns = np.nonzero(np.asarray(atlas.crop((sx, sy, sx+sw, sy+sh)).getchannel('A')) > 40)
      painted_pixels[size] = np.column_stack((columns+.5-sw/2, rows+.5-sh/2)) / SPRITE_DENSITIES.get(f'arrow_{size}', 1)
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'Contained signs on both device profiles')
  try:
    for profile in Profile:
      with BitmapFonts(profile, default_font_directory()) as fonts:
        widget = SpeedLimitWidget(fonts, profile)
        scale = widget.scale
        positions = ((88, 75), (188, 95), (1642, 856)) if profile == Profile.LARGE else ((320, 16), (100, 0), (348, 96))
        for vienna, (source, heading), (posted, offset), elapsed, (saved_x, y), direction, retained in product(
            (False, True), (('map', 'OSM'), ('vision', 'VISION')),
            ((65, 5), (120, 10), (130, -10), (145, -137), (199, -191), (200, -192), (252, -244), (406, -261), (55, 0)),
            (0, 350_000_000, 2_100_000_000), positions, (-1, 1), (False, True)):
          case = (profile, vienna, source, posted, offset, elapsed, saved_x, y, direction, retained)
          current = state(observed_ns=10_000_000_000+elapsed)
          key = 'cruise_limits' if profile == Profile.LARGE else 'speed_limit'
          current.customization['layouts'][profile][key].update(x=saved_x, y=y)
          x = saved_x+230 if profile == Profile.LARGE else saved_x
          current = replace(current, speed_mps=(posted+offset+direction*2)/3.6,
            appearance=replace(current.appearance, use_vienna_sign=vienna),
            speed_limit=replace(current.speed_limit, source=source, speed_limit_mps=posted/3.6,
              pending_speed_limit_mps=posted/3.6, action_enabled=False, presentation_adjusted_limit_mps=(posted+offset)/3.6))
          if retained:
            current = replace(current, speed_limit=replace(current.speed_limit, kind=ObservationKind.STALE,
              speed_limit_mps=None, pending_speed_limit_mps=None, last_received_speed_limit_mps=posted/3.6,
              last_received_source=source, last_received_adjusted_limit_mps=(posted+offset)/3.6))
          calls = []
          with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)), \
               patch.object(widget.shapes, 'arrow') as arrow_draw:
            widget.render(current)
          calls = [call for call in calls if call[-1].a == 255]
          assert sum(call[0] == (f'LAST {heading}' if retained else heading) for call in calls) == 1, case
          assert all(call[0] != 'LIMIT' for call in calls), case
          ink = []
          for text, role, size, left, top, color in calls:
            a, b = fonts.horizontal_ink(text, role, size)
            c, d = fonts.vertical_ink(text, role, size)
            box = left+a, top+c, left+b, top+d
            if vienna:
              cx, cy = x+64*scale, y+72*scale
              assert all((px-cx)**2+(py-cy)**2 <= (47*scale)**2
                         for px, py in product((box[0], box[2]), (box[1], box[3]))), case
              expected = widget.pulse.color(rl.Color(244, 247, 245, 255), current.observed_ns / 1e9)
              assert (color.r, color.g, color.b, color.a) == (expected.r, expected.g, expected.b, expected.a), case
            else:
              # The inner stroke ends at x+4/x+124; keep one clear pixel beside the glyphs.
              assert x+5*scale <= box[0] < box[2] <= x+123*scale, case
              assert y+7*scale <= box[1] < box[3] <= y+137*scale, case
            ink.append((text, box))
            nominal_size = size/widget.text_scale
            if role == FontRole.BOLD:
              assert 20 <= nominal_size <= (58 if vienna else 64)+.01, case
            elif role == FontRole.MEDIUM:
              assert 10 <= nominal_size <= (30 if vienna else 27)+.01, case
          if retained:
            arrow_draw.assert_not_called()
          else:
            arrow_draw.assert_called_once()
            size, ax, ay, arrow_direction, _ = arrow_draw.call_args.args
            assert size == 'compact', case
            assert arrow_direction == direction, case
            arrow_scale = arrow_draw.call_args.kwargs['scale']
            glyph = glyphs[size]
            left, top, right, bottom = glyph if direction>0 else (-glyph[2], -glyph[3], -glyph[0], -glyph[1])
            arrow = ax+left*arrow_scale, ay+top*arrow_scale, ax+right*arrow_scale, ay+bottom*arrow_scale
            label = next(box for text, box in ink if text == heading)
            assert abs(arrow[0] - label[2] - 6*scale) <= 1, case
            assert abs((label[0] + arrow[2]) / 2 - (x+64*scale)) <= .5, case
            assert abs((label[1] + label[3]) / 2 - (arrow[1] + arrow[3]) / 2) <= 1, case
            if vienna:
              # The arrow's transparent bounding-box corners are not painted ink.
              pixels = painted_pixels[size] * (arrow_scale if direction > 0 else -arrow_scale) + (ax, ay)
              assert np.all(np.linalg.norm(pixels-(cx, cy), axis=1) <= 47*scale), case
              assert .95*(label[3]-label[1]) <= arrow[3]-arrow[1] <= 1.25*(label[3]-label[1]), case
            else:
              assert x+7*scale <= arrow[0] < arrow[2] <= x+121*scale, case
              assert y+7*scale <= arrow[1] < arrow[3] <= y+137*scale, case
              assert all(box[2]+4*scale <= arrow[0] for text, box in ink if text == heading), (case, ink, arrow)
              assert arrow[3]+6*scale <= next(box[1] for text, box in ink if text==str(posted))
            ink.append(('arrow', arrow))
          for index, (_, a) in enumerate(ink):
            for _, b in ink[index+1:]:
              assert not (a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]), (case, a, b)
        widget.close()
  finally:
    if owns_context:
      rl.close_window()


def test_sign_profiles_share_layout_and_keep_aligned_numbers():
  from unittest.mock import Mock, patch
  from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget
  from openpilot.starpilot.ui.tests.test_unified_speed_native import state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'Shared sign scale and sharpness fixture')
  try:
    with BitmapFonts(Profile.COMPACT, default_font_directory()) as small, BitmapFonts(Profile.LARGE, default_font_directory()) as large:
      for vienna, source, (value, offset), pending in product(
          (False, True), ('map', 'vision'), ((65, 5), (120, 10), (145, -137), (70, 0)), (False, True)):
        current = state()
        current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna),
          speed_limit=replace(current.speed_limit, source=source, speed_limit_mps=value/3.6,
            pending_speed_limit_mps=value/3.6 if pending else None, action_enabled=False, presentation_adjusted_limit_mps=(value+offset)/3.6))
        drawn = []
        for fonts in (small, large):
          widget = SpeedLimitWidget(fonts, fonts.profile)
          widget.shapes = Mock()
          calls = []
          with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)):
            widget.render(current)
          calls = [call for call in calls if call[-1].a == 255]
          assert all(float(call[3]).is_integer() and float(call[4]).is_integer()
                     for call in calls if call[1] != FontRole.SET_SPEED)
          if not vienna:
            assert calls[0][2]/widget.text_scale <= 28
          elif fonts.profile == Profile.LARGE and not pending:
            assert 18 <= calls[0][2] / widget.text_scale <= 21
          elif vienna and pending and source == 'vision':
            assert 14 <= round(calls[0][2] / widget.text_scale) <= 18
          else:
            assert abs(calls[0][2]/widget.text_scale-18) < 1e-9
          drawn.append((widget, calls))
        small_widget, small_calls = drawn[0]
        large_widget, large_calls = drawn[1]
        assert [call[0] for call in small_calls] == [call[0] for call in large_calls]
        if not vienna:
          assert small_calls[1][1] == large_calls[1][1] == FontRole.SET_SPEED
        start = int(vienna and not pending)
        for a, b in zip(small_calls[start:], large_calls[start:], strict=True):
          assert abs(a[2]/small_widget.text_scale-b[2]/large_widget.text_scale) < 1e-9
          assert abs((a[3]-320)-(b[3]-318)/LARGE_SIGN_SCALE) <= 1
          assert abs((a[4]-16)-(b[4]-75)/LARGE_SIGN_SCALE) <= 1
          assert (a[-1].r, a[-1].g, a[-1].b, a[-1].a) == (b[-1].r, b[-1].g, b[-1].b, b[-1].a)
        if pending:
          for widget in (small_widget, large_widget):
            asset, x, y, _, _ = widget.shapes.arrow.call_args.args
            assert asset == 'compact' and float(x).is_integer() and float(y).is_integer()
        else:
          small_widget.shapes.arrow.assert_not_called()
          large_widget.shapes.arrow.assert_not_called()
  finally:
    if owns_context:
      rl.close_window()


def test_v5_geometry_migrates_and_keeps_custom_actions_independent():
  doc = default_document()
  doc['version'] = 5
  doc['speedSources'] = True
  doc['layouts']['large']['cruise_limits'].update(x=1550, y=638)
  doc['layouts']['large']['speed_limit_actions'].update(x=1550, y=954)
  migrated = validate_document(doc)
  assert migrated['version'] == 14
  assert migrated['layouts']['large']['cruise_limits'] == default_document()['layouts']['large']['cruise_limits']
  assert migrated['layouts']['large']['speed_limit_actions'] == {'x': 1550, 'y': 882, 'enabled': True}
  assert migrated['speedSources'] is True
  assert doc['layouts']['large']['cruise_limits']['x'] == 1550
  assert validate_document(migrated) == migrated
  doc['layouts']['large']['cruise_limits'].update(x=88, y=75)
  doc['layouts']['large']['speed_limit_actions'].update(x=88, y=447)
  assert validate_document(doc)['layouts']['large']['speed_limit_actions']['y'] == 381


def test_pending_choices_use_the_sign_with_separated_horizontal_targets():
  state = road()
  state = replace(state, slc_prompt_open=True, speed_limit=replace(state.speed_limit, pending_speed_limit_mps=25))
  accept, decline = slc_controls(Profile.LARGE, state)
  assert accept.bounds[2] < decline.bounds[0]
  assert accept.bounds[1] == decline.bounds[1] and accept.bounds[3] == decline.bounds[3]
  assert (accept.label, decline.label) == ('Accept', 'Reject')


def test_projection_v1_layout_migrates_without_losing_other_widgets():
  viewport = (2880, 1080)
  doc = default_layout_for_viewport(viewport)
  doc['version'] = 1
  doc['widgets']['speed_limit_actions'].update(x=88, y=447)
  doc['widgets']['current_speed'].update(x=1400, y=100)
  migrated = validate_layout_for_viewport(doc, viewport)
  assert migrated['version'] == 6
  assert migrated['widgets']['speed_limit_actions']['y'] == 267
  assert migrated['widgets']['current_speed'] == doc['widgets']['current_speed']
  assert validate_layout_for_viewport(migrated, viewport) == migrated


def test_committed_projection_v1_right_edge_is_preserved_and_clamped():
  viewport = (1860, 1080)
  doc = default_layout_for_viewport(viewport)
  doc['version'] = 1
  doc['widgets']['cruise_limits'].update(x=1654, y=75)
  doc['widgets']['speed_limit_actions'].update(x=1654, y=500)
  migrated = validate_layout_for_viewport(doc, viewport)
  card = migrated['widgets']['cruise_limits']
  assert confirmation_bounds('large', card['x'], card['y'], opened=True)[2] <= 1830
  assert confirmation_bounds('large', card['x']+1, card['y'], opened=True)[2] > 1830
  assert migrated['widgets']['speed_limit_actions'] == {'x': 1510, 'y': 500, 'enabled': True}



@pytest.mark.parametrize('wheel_confirmation', [False, True])
def test_chooser_binds_the_target_to_accept_and_keeps_all_native_ink_inside_its_control(wheel_confirmation):
  from unittest.mock import patch
  from openpilot.starpilot.ui.large_speed_geometry import CONFIRMATION_SCALE
  from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget
  from openpilot.starpilot.ui.speed_limit_shapes import ATLAS_PATH, SPRITES, SPRITE_DENSITIES
  from openpilot.starpilot.ui.tests.test_slc_ui_actions import state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'Confirmation typography fixture')
  try:
    for profile in Profile:
      scale = CONFIRMATION_SCALE[profile]
      with BitmapFonts(profile, default_font_directory()) as fonts:
        widget = SpeedLimitWidget(fonts, profile)
        try:
          for metric, posted, offset, show_offset in ((False, 65, 5, True), (True, 120, 10, True),
                                                       (True, 50, -5, True), (False, 65, 5, False),
                                                       (True, 120, 10, False), (True, 50, -5, False),
                                                       (True, 110, 10, True), (True, 110, 10, False),
                                                       (False, 5, 0, False), (True, 5, 0, False)):
            current = state()
            factor = 3.6 if metric else 2.2369362921
            obs = replace(current.speed_limit, source='vision', speed_limit_mps=posted / factor,
                          pending_speed_limit_mps=posted / factor,
                          pending_adjusted_limit_mps=(posted + offset) / factor,
                          presentation_adjusted_limit_mps=(posted + offset) / factor)
            current = replace(current, speed_limit=obs, metric=metric, show_slc_offset=show_offset,
                              slc_wheel_confirmation_available=wheel_confirmation)
            notice_calls = []
            with patch.object(fonts, 'draw', lambda *args, sink=notice_calls: sink.append(args)):
              widget.render_confirmation(current)
            notice_calls = [call for call in notice_calls if call[-1].a == 255]
            notice_bounds = speed_limit_bounds(profile, current)
            assert notice_bounds is not None
            boxes = {}
            for text, role, size, x, y, _ in notice_calls:
              left, right = fonts.horizontal_ink(text, role, size)
              top, bottom = fonts.vertical_ink(text, role, size)
              boxes[text] = (x + left, y + top, x + right, y + bottom)
              assert notice_bounds[0] + 8 * scale <= x + left < x + right <= notice_bounds[2] - 8 * scale
              assert notice_bounds[1] + 8 * scale <= y + top < y + bottom <= notice_bounds[3] - 4 * scale
            number = boxes[str(posted)]
            right = boxes[f'{offset:+d}'][2] if show_offset else number[2]
            assert (number[0] + right) / 2 == pytest.approx((notice_bounds[0] + notice_bounds[2]) / 2)
            assert boxes['VISION'][3] + 6 * scale <= number[1]
            assert number[3] + 6 * scale <= boxes['NEW LIMIT'][1]
            assert boxes['NEW LIMIT'][3] + 6 * scale <= boxes['ACCEPT' if wheel_confirmation else 'Tap to review'][1]
            if show_offset:
              assert boxes[f'{offset:+d}'][3] == pytest.approx(number[3])
            else:
              assert f'{offset:+d}' not in boxes
            current = replace(current, slc_prompt_open=True)
            if posted == 110:
              document = default_document()
              document['showMaxSpeed'] = False
              document['layouts']['compact']['max_speed']['enabled'] = False
              current = replace(current, customization=document, speed_limit=replace(current.speed_limit,
                driver_override_active=True, override_basis='persistent', effective_cluster_target_mps=128 / 3.6))
            calls = []
            with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)), patch.object(rl, 'draw_rectangle_rounded'):
              widget.render_confirmation(current)
            calls = [call for call in calls if call[-1].a == 255]
            context = 'Now: OVERRIDE 128' if posted == 110 else 'NEW LIMIT'
            assert [call[0] for call in calls] == [context, 'ACCEPT', str(posted + offset), 'REJECT', 'NEW LIMIT']
            bounds = speed_limit_bounds(profile, current)
            assert bounds is not None
            for text, role, size, x, y, _ in calls[:1]:
              left, right = fonts.horizontal_ink(text, role, size)
              top, bottom = fonts.vertical_ink(text, role, size)
              assert x + left >= bounds[0] + 10 * scale
              assert x + right <= bounds[2] - 10 * scale
              assert bounds[1] + 8 * scale <= y + top < y + bottom <= bounds[1] + 40 * scale
            # Measure from the painted outline, which is smaller than the touch area.
            name = f'button_{profile}'
            sx, sy, sw, sh = SPRITES[name]
            with Image.open(ATLAS_PATH) as atlas:
              alpha = np.asarray(atlas.crop((sx, sy, sx + sw, sy + sh)).getchannel('A'))
            horizontal = np.flatnonzero(alpha[sh // 2])
            vertical = np.flatnonzero(alpha[:, sw // 2])
            inner_left, inner_right = horizontal[horizontal < sw / 2][-1] + 1, horizontal[horizontal > sw / 2][0]
            inner_top, inner_bottom = vertical[vertical < sh / 2][-1] + 1, vertical[vertical > sh / 2][0]
            density = SPRITE_DENSITIES[name]
            row_centers = []
            for control, content in zip(slc_controls(profile, current), (calls[1:3], calls[3:]), strict=True):
              label_bottom = 0
              boxes = []
              cx, cy = (control.bounds[0] + control.bounds[2]) / 2, (control.bounds[1] + control.bounds[3]) / 2
              interior = (cx + (inner_left - sw / 2) * scale / density, cy + (inner_top - sh / 2) * scale / density,
                          cx + (inner_right - sw / 2) * scale / density, cy + (inner_bottom - sh / 2) * scale / density)
              contour = 1.15 if profile == Profile.LARGE else 1.
              padding = (14 if profile == Profile.LARGE else 9) * scale
              for text, role, size, x, y, _ in content:
                left, right = fonts.horizontal_ink(text, role, size)
                top, bottom = fonts.vertical_ink(text, role, size)
                boxes.append((x + left, y + top, x + right, y + bottom))
                assert min(x + left - interior[0], interior[2] - x - right,
                           y + top - interior[1], interior[3] - y - bottom) - contour >= padding
                if text == control.label.upper():
                  label_bottom = y + bottom
                  assert (bottom - top) / scale * 25.4 / 300 >= (1.7 if profile == Profile.LARGE else 1.5)
                else:
                  assert y + top >= label_bottom + 6 * scale
              assert (boxes[0][0] + boxes[0][2]) / 2 == pytest.approx(cx)
              assert (min(box[0] for box in boxes[1:]) + max(box[2] for box in boxes[1:])) / 2 == pytest.approx(cx)
              row_centers.append(tuple((box[1] + box[3]) / 2 for box in boxes))
              if control.label == 'Accept':
                assert abs((boxes[0][1] + boxes[1][3]) / 2 - cy) < 3 * scale
                assert (boxes[1][3] - boxes[1][1]) / scale * 25.4 / 300 >= (3.3 if profile == Profile.LARGE else 2.7)
            assert row_centers[0] == pytest.approx(row_centers[1])
        finally:
          widget.close()
  finally:
    if owns_context:
      rl.close_window()
