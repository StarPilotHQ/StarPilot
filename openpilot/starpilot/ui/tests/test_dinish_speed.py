"""Pinned numeric atlas and the larger stack's saved/touch geometry."""
from dataclasses import replace
from itertools import product
import io
import re
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import numpy as np
from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile, default_font_directory, font_filename, font_path, validate_bitmap_font
from openpilot.starpilot.ui.onroad_customization import default_document, validate_document
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


def test_toyota_guidance_preserves_primary_legibility_and_fits_the_action_area():
  from unittest.mock import patch
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.onroad_state import slc_action_bounds
  from openpilot.starpilot.ui.unified_speed_presentation import toyota_cruise_notice
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'Toyota guidance font fixture')
  try:
    for profile in Profile:
      with BitmapFonts(profile, default_font_directory()) as fonts:
        view = OnroadView.__new__(OnroadView)
        view.personality = Mock()
        view.fonts = fonts
        ppi = (2160**2 + 1080**2)**.5 / 6 if profile == Profile.LARGE else 300
        for metric, status in product((False, True), ('raise_dash', 'release_plus', 'off_hold', 'off_resume')):
          current = replace(road(), metric=metric, toyota_cruise_status=status, toyota_cruise_target_kph=145.)
          first, last = slc_action_bounds(profile, current)
          notice = toyota_cruise_notice(current)
          assert notice is not None
          calls = []
          with patch.object(fonts, 'draw', lambda *args, calls=calls: calls.append(args)):
            view._slc_actions(current)
          foreground = [call for call in calls if call[-1].a == 255]
          for text, role, size, x, y, _ in foreground:
            a, b = fonts.horizontal_ink(text, role, size)
            top, bottom = fonts.vertical_ink(text, role, size)
            assert first[0] + 4 <= x + a < x + b <= last[2] - 4
            assert first[1] <= y + top < y + bottom <= last[3]
            if text in (notice[0], notice[2]):
              assert (bottom - top) * 25.4 / ppi >= 2.8
          for _left, top, _right, bottom in (first, last):
            assert (bottom - top) * 25.4 / ppi >= 4.8
  finally:
    if owns_context:
      rl.close_window()


def test_large_sign_is_secondary_to_set_speed_at_the_saved_position():
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
                              presentation_adjusted_limit_mps=130 / factor)
        current = replace(current, metric=metric, cruise_kph=value / factor * 3.6 if value else None,
                          speed_limit=observation)
        calls = []
        with patch.object(fonts, 'draw', lambda *args, calls=calls: calls.append(args)), patch.object(rl, 'draw_line_ex') as lines:
          UnifiedSpeedWidget(fonts).render(rl.Rectangle(30, 30, 1800, 1020), current)
        calls = [call for call in calls if call[-1].a == 255]
        posted = next(call for call in calls if call[0] == '120' and call[1] == FontRole.SET_SPEED)
        left, right = fonts.horizontal_ink(posted[0], posted[1], posted[2])
        assert right-left <= 96*1.25
        assert abs(posted[3] + (left + right) / 2 - 491.75) <= .5
        a, b = fonts.vertical_ink('120', FontRole.SET_SPEED, posted[2])
        assert b - a == pytest.approx(60, abs=.1)
        assert abs(posted[4]+(a+b)/2-(95+67*1.25)) <= .5
        set_calls = [call for call in calls if call[1] == FontRole.SET_SPEED and call[3] < 388]
        assert bool(set_calls) == (show_set and value is not None)
        if set_calls:
          text, role, size, x, y, _ = set_calls[0]
          assert fonts.measure(text, role, size).width <= 200
          left, right = fonts.horizontal_ink(text, role, size)
          assert x + (left + right) / 2 == pytest.approx(308)
          assert x + right + 20 <= 418
          top, bottom = fonts.vertical_ink(text, role, size)
          assert bottom - top == pytest.approx(88, abs=1)
          assert y + (top + bottom) / 2 == pytest.approx(196)
          unit = next(call for call in calls if call[0] == ('km/h' if metric else 'mph'))
          assert unit[2] == 28
          assert unit[4] + fonts.vertical_ink(unit[0], unit[1], unit[2])[1] + 12 <= y + top
        if show_set and value is None:
          start, end, stroke, color = lines.call_args.args
          assert (start.x, start.y, end.x, end.y, stroke) == (280, 196, 336, 196, 8)
          assert (color.r, color.g, color.b, color.a) == (190, 208, 202, 255)
          assert all(call[0] not in ('–', '?') for call in calls)
        else:
          lines.assert_not_called()
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


def test_compact_preview_sprite_keeps_the_sign_offset_arrow_and_override_cross():
  import json
  from unittest.mock import patch
  from openpilot.starpilot.ui.layout_preview_renderer import _Canvas, _straight_rgba, sample_state
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'SLC preview crop fixture')
  canvas = _Canvas(Profile.COMPACT)
  try:
    for y, scene in product((20, 24), ('slc_pending', 'slc_retained')):
      document = default_document()
      document['layouts']['compact']['speed_limit']['y'] = y
      assert validate_document(document)['layouts']['compact']['speed_limit']['y'] == y
      with patch.dict('openpilot.starpilot.ui.layout_preview_renderer.RUNTIME_PREVIEW_WIDGETS', compact=('speed_limit',)):
        png = canvas.render_widget_art(sample_state(scene, document), document['layouts']['compact'])
      with Image.open(io.BytesIO(png)) as sheet:
        slot = json.loads(sheet.info['starpilot-widget-art'])['sprites']['speed_limit']
        sprite = sheet.crop((slot['x'], slot['y'], slot['x']+slot['width'], slot['y']+slot['height']))
        rebuilt = Image.new('RGBA', Profile.COMPACT.size)
        rebuilt.paste(sprite, (round(330+slot['offsetX']), round(y+slot['offsetY'])))
      image = rl.load_image_from_texture(canvas.target.texture)
      try:
        rl.image_flip_vertical(image)
        direct = np.frombuffer(_straight_rgba(bytes(image.data[:536*240*4])), np.uint8).reshape(240, 536, 4)
      finally:
        rl.unload_image(image)
      assert np.array_equal(direct[..., 3], np.asarray(rebuilt)[..., 3])
      if scene == 'slc_pending':
        assert (slot['offsetX'], slot['offsetY'], slot['width'], slot['height']) == (-182, -12, 312, 152)
      else:
        assert slot['offsetX'] == slot['offsetY'] == 0
        assert (slot['width'], slot['height']) == (118, min(132, 240-y))
      if scene == 'slc_retained':
        assert np.any(direct[..., 3]), (y, scene)
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
                            for text, role, size, _, y, _ in foreground if text in ('Accept', 'Reject'))
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
      assert alpha[100, 220] > 200, 'Accept must remain in its sprite'
      assert alpha[100, 450] > 200, 'Reject must remain in its sprite'
      assert alpha[100, 382] > 200, 'The shared surface must remain in its sprite'
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
        positions = ((88, 75), (188, 95), (1642, 882)) if profile == Profile.LARGE else ((330, 20), (100, 0), (358, 108))
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
          assert sum(call[0] == heading for call in calls) == 1, case
          assert all(call[0] != 'LAST' for call in calls), case
          assert all(call[0] != 'LIMIT' for call in calls), case
          ink = []
          for text, role, size, left, top, color in calls:
            a, b = fonts.horizontal_ink(text, role, size)
            c, d = fonts.vertical_ink(text, role, size)
            box = left+a, top+c, left+b, top+d
            if vienna:
              cx, cy = x+59*scale, y+67*scale
              assert all((px-cx)**2+(py-cy)**2 <= (47*scale)**2
                         for px, py in product((box[0], box[2]), (box[1], box[3]))), case
              assert (color.r, color.g, color.b, color.a) == (0, 0, 0, 255), case
            else:
              # The inner stroke ends at x+4/x+114; keep one clear pixel beside the glyphs.
              assert x+5*scale <= box[0] < box[2] <= x+113*scale, case
              assert y+7*scale <= box[1] < box[3] <= y+125*scale, case
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
            glyph = glyphs[size]
            left, top, right, bottom = glyph if direction>0 else (-glyph[2], -glyph[3], -glyph[0], -glyph[1])
            arrow = ax+left*scale, ay+top*scale, ax+right*scale, ay+bottom*scale
            label = next(box for text, box in ink if text == heading)
            assert abs(arrow[0] - label[2] - 6*scale) <= 1, case
            assert abs((label[0] + arrow[2]) / 2 - (x+59*scale)) <= .5, case
            assert abs((label[1] + label[3]) / 2 - (arrow[1] + arrow[3]) / 2) <= 1, case
            if vienna:
              # The arrow's transparent bounding-box corners are not painted ink.
              pixels = painted_pixels[size] * (scale if direction > 0 else -scale) + (ax, ay)
              assert np.all(np.linalg.norm(pixels-(cx, cy), axis=1) <= 47*scale), case
              assert .95*(label[3]-label[1]) <= arrow[3]-arrow[1] <= 1.25*(label[3]-label[1]), case
            else:
              assert x+7*scale <= arrow[0] < arrow[2] <= x+111*scale, case
              assert y+7*scale <= arrow[1] < arrow[3] <= y+125*scale, case
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


def test_sign_profiles_share_layout_and_snap_only_final_pixels():
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
          assert all(float(call[3]).is_integer() and float(call[4]).is_integer() for call in calls)
          if fonts.profile == Profile.LARGE and not vienna and not pending:
            assert calls[0][2] <= 28
          else:
            assert abs(calls[0][2]/widget.text_scale-18) < 1e-9
          drawn.append((widget, calls))
        small_widget, small_calls = drawn[0]
        large_widget, large_calls = drawn[1]
        assert [call[0] for call in small_calls] == [call[0] for call in large_calls]
        if not vienna and not pending:
          assert small_calls[1][1] == FontRole.BOLD
          assert large_calls[1][1] == FontRole.SET_SPEED
          continue
        for a, b in zip(small_calls, large_calls, strict=True):
          assert abs(a[2]/small_widget.text_scale-b[2]/large_widget.text_scale) < 1e-9
          assert abs((a[3]-330)-(b[3]-318)/1.25) <= 1
          assert abs((a[4]-20)-(b[4]-75)/1.25) <= 1
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
  assert migrated['version'] == 12
  assert migrated['layouts']['large']['cruise_limits'] == default_document()['layouts']['large']['cruise_limits']
  assert migrated['layouts']['large']['speed_limit_actions'] == {'x': 1550, 'y': 882, 'enabled': True}
  assert migrated['speedSources'] is True
  assert doc['layouts']['large']['cruise_limits']['x'] == 1550
  assert validate_document(migrated) == migrated
  doc['layouts']['large']['cruise_limits'].update(x=88, y=75)
  doc['layouts']['large']['speed_limit_actions'].update(x=88, y=447)
  assert validate_document(doc)['layouts']['large']['speed_limit_actions']['y'] == 359


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
  assert migrated['version'] == 5
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
  assert migrated['widgets']['cruise_limits']['x'] == 1239
  assert migrated['widgets']['speed_limit_actions'] == {'x': 1510, 'y': 500, 'enabled': True}



def test_chooser_target_header_and_padded_labels_fit_the_native_ink():
  from unittest.mock import patch
  from openpilot.starpilot.ui.large_speed_geometry import CONFIRMATION_SCALE
  from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget
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
                                                       (True, 120, 10, False), (True, 50, -5, False)):
            current = state()
            factor = 3.6 if metric else 2.2369362921
            obs = replace(current.speed_limit, source='vision', speed_limit_mps=posted / factor,
                          pending_speed_limit_mps=posted / factor,
                          pending_adjusted_limit_mps=(posted + offset) / factor,
                          presentation_adjusted_limit_mps=(posted + offset) / factor)
            current = replace(current, speed_limit=obs, metric=metric, show_slc_offset=show_offset,
                              slc_wheel_confirmation_available=True)
            notice_calls = []
            with patch.object(fonts, 'draw', lambda *args, sink=notice_calls: sink.append(args)):
              widget.render_confirmation(current)
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
            assert number[3] + 6 * scale <= boxes['New Limit'][1]
            assert boxes['New Limit'][3] + 6 * scale <= boxes['Accept'][1]
            if show_offset:
              assert boxes[f'{offset:+d}'][3] == pytest.approx(number[3])
            else:
              assert f'{offset:+d}' not in boxes
            current = replace(current, slc_prompt_open=True)
            calls = []
            with patch.object(fonts, 'draw', lambda *args, sink=calls: sink.append(args)), patch.object(rl, 'draw_rectangle_rounded'):
              widget.render_confirmation(current)
            assert calls[0][0] == str(posted + offset)
            assert calls[1][0] == ('km/h' if metric else 'mph')
            assert calls[2][0] == (f'VISION {posted} {offset:+d}' if show_offset else 'VISION')
            bounds = speed_limit_bounds(profile, current)
            assert bounds is not None
            previous_right = bounds[0]
            for text, role, size, x, y, _ in calls[:3]:
              left, right = fonts.horizontal_ink(text, role, size)
              top, bottom = fonts.vertical_ink(text, role, size)
              assert x + left >= previous_right
              assert x + right <= bounds[2] - 10 * scale
              assert bounds[1] + 8 * scale <= y + top < y + bottom <= bounds[1] + 40 * scale
              previous_right = x + right
            for control, (text, role, size, x, y, _) in zip(slc_controls(profile, current), calls[-2:], strict=True):
              left, right = fonts.horizontal_ink(text, role, size)
              top, bottom = fonts.vertical_ink(text, role, size)
              assert x + left - control.bounds[0] >= 12 * scale
              assert control.bounds[2] - x - right >= 12 * scale
              assert control.bounds[1] + 10 * scale <= y + top < y + bottom <= control.bounds[3] - 10 * scale
              assert (bottom - top) / scale * 25.4 / 300 >= 2.8
        finally:
          widget.close()
  finally:
    if owns_context:
      rl.close_window()
