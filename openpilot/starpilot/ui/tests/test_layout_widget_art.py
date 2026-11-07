import numpy as np
from typing import cast
import json
import struct
import zlib

import pytest

from openpilot.starpilot.ui.layout_preview_renderer import _png_rgba, _straight_rgba
from openpilot.starpilot.ui.layout_preview_transport import _payload, validate_png, PreviewInvalid
from openpilot.starpilot.ui.onroad_customization import RUNTIME_PREVIEW_WIDGETS, customization_metadata, default_document
from openpilot.starpilot.ui.onroad_personality import PersonalityWidget


def test_widget_art_request_uses_same_validated_document_and_scene():
  request = {'document': default_document(), 'profile': 'compact', 'scene': 'cem_curve', 'art': True}
  assert _payload(request) == request
  assert _payload({**request, 'art': 'backdrop'}) == {**request, 'art': 'backdrop'}
  for art in (False, None, 1, 'yes', 'Backdrop'):
    with pytest.raises(PreviewInvalid):
      _payload({**request, 'art': art})
  with pytest.raises(PreviewInvalid):
    _payload({**request, 'scene': 'live'})
  without = {key: value for key, value in request.items() if key != 'art'}
  assert _payload(without) == without


@pytest.mark.parametrize('sidebar', (True, False))
def test_native_preview_sidebar_choice_is_strict_and_does_not_change_saved_layout(sidebar):
  document = default_document()
  payload = {'document': {**document, 'developerSidebar': sidebar}, 'profile': 'large', 'scene': 'engaged'}
  assert _payload(payload) == payload
  assert 'developerSidebar' not in document
  for invalid in (0, 1, 'Off', None, [], {}):
    with pytest.raises(PreviewInvalid):
      _payload({**payload, 'document': {**document, 'developerSidebar': invalid}})
  with pytest.raises(PreviewInvalid):
    _payload({**payload, 'document': list(document.items())})


def test_widget_art_registry_is_shared_with_native_and_projection_metadata():
  from openpilot.starpilot.system.starport.projection_layout import layout_metadata_for_viewport
  metadata = customization_metadata()['profiles']
  for profile, ids in RUNTIME_PREVIEW_WIDGETS.items():
    assert metadata[profile]['runtimePreviewWidgets'] == list(ids)
    assert all(key in metadata[profile]['widgets'] for key in ids)
  assert layout_metadata_for_viewport((2880, 1080))['runtimePreviewWidgets'] == list(RUNTIME_PREVIEW_WIDGETS['large'])


def test_transparent_render_coverage_exports_straight_alpha_without_darkening():
  straight = _straight_rgba(bytes((128, 64, 0, 128, 9, 8, 7, 0, 255, 255, 255, 255)))
  assert straight == bytes((255, 128, 0, 128, 0, 0, 0, 0, 255, 255, 255, 255))
  # Browser source-over reconstructs the native scratch RGB to within one byte.
  assert [round(value * 128 / 255) for value in straight[:3]] == [128, 64, 0]


def test_sprite_sheet_metadata_and_pixels_share_actual_png_dimensions():
  metadata = {'version': 1, 'width': 2, 'height': 1, 'sprites': {'conditional_mode': {'x': 0, 'y': 0, 'width': 2, 'height': 1}}}
  pixels = bytes((255, 255, 255, 255, 0, 0, 0, 0))
  image = _png_rgba(2, 1, pixels, widget_art=metadata)
  assert validate_png(image) == image
  offset = 8
  chunks = {}
  while offset < len(image):
    size = struct.unpack('!I', image[offset:offset + 4])[0]
    tag, data = image[offset + 4:offset + 8], image[offset + 8:offset + 8 + size]
    assert zlib.crc32(tag + data) & 0xffffffff == struct.unpack('!I', image[offset + 8 + size:offset + 12 + size])[0]
    chunks[tag] = data
    offset += size + 12
  assert json.loads(chunks[b'tEXt'].split(b'\0', 1)[1]) == metadata
  assert zlib.decompress(chunks[b'IDAT']) == b'\0' + pixels


def test_static_compact_sample_settles_actual_hud_without_changing_live_blink_policy():
  from pathlib import Path
  from types import SimpleNamespace as NS
  from unittest.mock import Mock
  import time
  from openpilot.starpilot.ui.layout_preview_renderer import _Canvas, sample_state
  from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
  from openpilot.starpilot.ui.presentation import Profile
  from openpilot.starpilot.ui.onroad import OnroadView

  hud = CompactHudRenderer(Mock(), Path('/unused-assets'))
  canvas = _Canvas.__new__(_Canvas)
  canvas.profile = Profile.COMPACT
  canvas.view = cast(OnroadView, NS(personality=PersonalityWidget(), compact_hud=hud, torque_bar=NS(_torque_filter=NS(x=0), _alpha_filter=NS(x=0))))
  sample = sample_state('engaged', default_document())
  for _ in range(2):
    canvas._settle(sample)
    assert hud._set_speed_opacity(sample, time.monotonic_ns()) == pytest.approx(1.)
  lateral_only = sample_state('aol', default_document())
  canvas._settle(lateral_only)
  assert hud._set_speed_opacity(lateral_only, time.monotonic_ns()) == 0.


def test_scene_switches_settle_cem_geometry_independently_of_previous_scene():
  from types import SimpleNamespace as NS
  from unittest.mock import Mock
  from openpilot.starpilot.ui.layout_preview_renderer import _Canvas, sample_state
  from openpilot.starpilot.ui.onroad_cem_gauge import AetherGauge, _get_road_height, gauge_data
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.presentation import Profile

  gauge = AetherGauge(Mock())
  canvas = _Canvas.__new__(_Canvas)
  canvas.profile = Profile.LARGE
  canvas.view = cast(OnroadView, NS(personality=PersonalityWidget(), cem_gauge=gauge, torque_bar=NS(_torque_filter=NS(x=0), _alpha_filter=NS(x=0))))
  for scene in ('cem_stop_light', 'cem_curve', 'cem_lead', 'engaged', 'cem_curve'):
    state = sample_state(scene, default_document())
    gauge._road_h_filter.x = -1  # A previous animation must never leak into a static scene.
    canvas._settle(state)
    assert gauge._road_h_filter.x == _get_road_height(gauge_data(state))
    assert gauge._drive_frame == state.drive_frame


@pytest.mark.parametrize('viewport', (None, (2880, 1080)))
@pytest.mark.parametrize('sidebar', (True, False))
def test_large_wheel_art_is_drawn_inside_its_crop_with_or_without_sidebar(monkeypatch, viewport, sidebar):
  from dataclasses import replace
  from pathlib import Path
  from types import SimpleNamespace as NS
  from unittest.mock import Mock
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.starpilot.ui import layout_preview_renderer as renderer
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.onroad_large_widgets import SteeringWheelWidget
  from openpilot.starpilot.ui.presentation import Profile
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, projection_customization

  document = default_document()
  positions = document['layouts']['large']
  if viewport:
    projection = default_layout_for_viewport(viewport)
    positions = projection['widgets']
    document = projection_customization(projection, document)
  canvas = renderer._Canvas.__new__(renderer._Canvas)
  canvas.profile, canvas.viewport = Profile.LARGE, viewport
  monkeypatch.setattr(canvas, 'fonts', NS(profile=Profile.LARGE), raising=False)
  canvas.target = NS(texture=object())
  view = OnroadView.__new__(OnroadView)
  view.personality = Mock()
  monkeypatch.setattr(view, 'fonts', canvas.fonts, raising=False)
  view.projection_viewport = viewport
  view.steering_wheel = SteeringWheelWidget(Path('/unused'))
  view.steering_wheel._texture = object()  # Real widget geometry, without loading a GL texture.
  canvas.view = view
  monkeypatch.setattr(canvas, '_settle', lambda state: None)
  monkeypatch.setitem(renderer.RUNTIME_PREVIEW_WIDGETS, 'large', ('steering_wheel',))
  monkeypatch.setattr('openpilot.starpilot.system.starport.projection_layout.SPRITE_WIDGETS', ())
  monkeypatch.setattr(rl, 'rl_set_blend_factors_separate', Mock())
  for method in ('begin_texture_mode', 'clear_background', 'begin_blend_mode', 'end_blend_mode', 'end_texture_mode',
                 'image_format', 'image_flip_vertical', 'image_crop', 'unload_image', 'draw_circle', 'draw_ring', 'draw_texture_pro'):
    monkeypatch.setattr(rl, method, Mock())
  pixels = np.zeros(192 * 192 * 4, dtype=np.uint8)
  monkeypatch.setattr(rl, 'load_image_from_texture', Mock(return_value=NS(data=pixels)))
  monkeypatch.setattr(renderer, '_png_rgba', Mock(return_value=b'png'))
  state = renderer.sample_state('braking', document)
  canvas.render_widget_art(replace(state, appearance=replace(state.appearance, show_developer_sidebar=sidebar)), positions)
  drawn = rl.draw_texture_pro.call_args.args[2]
  crop = rl.image_crop.call_args.args[1]
  assert crop.x <= drawn.x < drawn.x + drawn.width <= crop.x + crop.width
  assert crop.y <= drawn.y < drawn.y + drawn.height <= crop.y + crop.height
  color = rl.draw_texture_pro.call_args.args[-1]
  assert (color.r, color.g, color.b) == (255, 0, 0), 'The braking wheel must remain visible and red'


def test_starport_widgets_get_sprites_and_empty_ones_keep_the_sketch(monkeypatch):
  from types import SimpleNamespace as NS
  from unittest.mock import Mock
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.starpilot.ui import layout_preview_renderer as renderer
  from openpilot.starpilot.ui.presentation import Profile
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, projection_customization

  viewport = (1920, 1080)
  layout = default_layout_for_viewport(viewport)
  layout['widgets']['nav_home']['enabled'] = True
  document = projection_customization(layout, default_document())
  canvas = renderer._Canvas.__new__(renderer._Canvas)
  canvas.profile, canvas.viewport = Profile.LARGE, viewport
  monkeypatch.setattr(canvas, 'fonts', NS(profile=Profile.LARGE), raising=False)
  canvas.target = NS(texture=object())
  drawn = []
  canvas.view = NS(_ordered_widgets=lambda content, state, only_widget=None: drawn.append(only_widget))
  monkeypatch.setattr(canvas, '_settle', lambda state: None)
  monkeypatch.setitem(renderer.RUNTIME_PREVIEW_WIDGETS, 'large', ())
  monkeypatch.setattr(rl, 'rl_set_blend_factors_separate', Mock())
  for method in ('begin_texture_mode', 'clear_background', 'begin_blend_mode', 'end_blend_mode', 'end_texture_mode',
                 'image_format', 'image_flip_vertical', 'image_crop', 'unload_image'):
    monkeypatch.setattr(rl, method, Mock())
  # Only the nav card draws anything; every other StarPort widget comes back transparent.
  monkeypatch.setattr(rl, 'load_image_from_texture', lambda texture: NS(data=np.full(1920 * 1080 * 4, 255 if drawn[-1] == 'nav_card' else 0, np.uint8)))
  png = Mock(return_value=b'png')
  monkeypatch.setattr(renderer, '_png_rgba', png)
  canvas.render_widget_art(renderer.sample_state('general', document), layout['widgets'], general=True)
  assert 'nav_home' in drawn and 'nav_map' not in drawn
  assert set(png.call_args.kwargs['widget_art']['sprites']) == {'nav_card'}
