import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.onroad_customization import PROFILES, default_document, validate_document, widget_size
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.onroad_torque import TorqueBarWidget
from openpilot.starpilot.ui.tests.test_onroad_torque_draw import record_gpu


def scene(document=None):
  return replace(OnroadState(True, True, 20, 80, SpeedLimitObservation(), lateral_active=True),
                 customization=document or default_document())


def geometry(profile, state, torque=.3, alpha=1.):
  data = PROFILES[profile]
  bounds = data['bounds']
  rect = rl.Rectangle(*(bounds[key] for key in ('x', 'y', 'width', 'height')))
  widget = TorqueBarWidget()
  with patch.object(widget._torque_filter, 'update', return_value=torque), \
       patch.object(widget._alpha_filter, 'update', return_value=alpha), \
       record_gpu() as gpu:
    widget.render(rect, state, data['width'])
  return gpu.frames


class TestTorqueLayout(unittest.TestCase):
  def test_square_projection_uses_saved_large_placement_and_visibility(self):
    from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, projection_customization, validate_layout_for_viewport
    document = default_layout_for_viewport((1860, 1860))
    validate_layout_for_viewport(document, (1860, 1860))

    def projected_frames(layout):
      state = scene(projection_customization(layout, default_document()))
      widget = TorqueBarWidget()
      with patch.object(widget._torque_filter, 'update', return_value=.3), \
           patch.object(widget._alpha_filter, 'update', return_value=1.), record_gpu() as gpu:
        widget.render(rl.Rectangle(30, 30, 1800, 1800), state, 1860)
      return gpu.frames

    original, = projected_frames(document)
    moved = copy.deepcopy(document)
    moved['widgets']['torque_bar']['x'] -= 10
    moved['widgets']['torque_bar']['y'] += 10
    validate_layout_for_viewport(moved, (1860, 1860))
    translated, = projected_frames(moved)
    for field in ('geometry', 'dotGeometry'):
      x, y, *rest = original.uniforms[field]
      self.assertEqual(translated.uniforms[field], (x - 10, y + 10, *rest))
    moved['widgets']['torque_bar']['enabled'] = False
    self.assertEqual(projected_frames(moved), [])

  def test_moved_arc_dot_and_sampling_quad_translate_without_resizing(self):
    for profile in PROFILES:
      with self.subTest(profile=profile):
        document = default_document()
        base = document['layouts'][profile]['torque_bar']
        base['x'] += 10
        base['y'] += 10
        document = validate_document(document)
        original, = geometry(profile, scene())
        moved, = geometry(profile, scene(document))
        np.testing.assert_allclose(moved.vertices, np.array(original.vertices) + [10, 10], atol=1e-10)
        for field in ('geometry', 'dotGeometry'):
          x, y, *rest = original.uniforms[field]
          self.assertEqual(moved.uniforms[field], (x + 10, y + 10, *rest))
        for field in ('backgroundNormals', 'indicatorNormals', 'style'):
          self.assertEqual(moved.uniforms[field], original.uniforms[field])

  def test_metadata_bounds_enclose_all_legal_arc_and_dot_states(self):
    for profile, data in PROFILES.items():
      widget = data['widgets']['torque_bar']
      x, y = widget['default']['x'], widget['default']['y']
      right, bottom = x + widget['width'], y + widget['height']
      for torque in np.linspace(-1, 1, 41):
        for alpha in (0, .01, .2, .5, 1):
          for frame in geometry(profile, scene(), float(torque), alpha):
            cx, cy, radius, half = frame.uniforms['geometry']
            cap = frame.uniforms['style'][0]
            normals = np.array(frame.uniforms['backgroundNormals']).reshape(2, 2)
            # The arc and its rounded ends must fit the saved layout, independent of the padded sampling quad.
            ends = np.array([cx, cy]) + radius * normals
            extents = abs(normals) * (half - cap) + cap
            self.assertGreaterEqual(float((ends - extents)[:, 0].min()), x)
            self.assertGreaterEqual(cy - radius - half, y)
            self.assertLessEqual(float((ends + extents)[:, 0].max()), right)
            self.assertLessEqual(float((ends + extents)[:, 1].max()), bottom)
            if frame.uniforms['style'][3] > 0:
              dx, dy, dot_radius, _ = frame.uniforms['dotGeometry']
              self.assertGreaterEqual(dx - dot_radius, x)
              self.assertGreaterEqual(dy - dot_radius, y)
              self.assertLessEqual(dx + dot_radius, right)
              self.assertLessEqual(dy + dot_radius, bottom)

  def test_only_torque_underlay_can_overlap_reserved_actions(self):
    document = default_document()
    self.assertEqual(validate_document(document), document)
    document['layouts']['compact']['torque_bar'].update(x=174, y=179)
    validate_document(document)
    for key in ('max_speed', 'driver_monitor', 'steering_wheel'):
      with self.subTest(key=key):
        invalid = default_document()
        actions = invalid['layouts']['compact']['speed_limit_actions']
        _, height = widget_size(invalid, 'compact', key)
        invalid['layouts']['compact'][key].update(x=actions['x'] - widget_size(invalid, 'compact', key)[0], y=min(actions['y'], 240 - height))
        self.assertEqual(validate_document(invalid), invalid)  # Touching edges do not overlap.
        invalid['layouts']['compact'][key]['x'] += 1
        with self.assertRaises(ValueError):
          validate_document(invalid)

  def test_complete_old_three_and_four_widget_shapes_migrate(self):
    for count in (3, 4):
      document = default_document()
      for key in ('model_confidence', 'conditional_mode', 'following_distance'):
        del document['layouts']['compact'][key]
      for layout in document['layouts'].values():
        del layout['torque_bar']
        del layout['speed_limit_actions']
        if count == 3:
          del layout['driver_monitor']
      document['palette']['text'] = '#12345678'
      document['layouts']['large']['current_speed'].update(x=700, y=200, enabled=False)
      old = copy.deepcopy(document)
      result = validate_document(document)
      self.assertEqual(document, old)
      self.assertEqual(result['palette'], old['palette'])
      for profile, layout in old['layouts'].items():
        for key, placement in layout.items():
          self.assertEqual(result['layouts'][profile][key], placement)
        self.assertEqual(result['layouts'][profile]['torque_bar'], default_document()['layouts'][profile]['torque_bar'])
        self.assertEqual(result['layouts'][profile]['speed_limit_actions'], default_document()['layouts'][profile]['speed_limit_actions'])

  def test_mixed_legacy_shape_is_rejected(self):
    document = default_document()
    for layout in document['layouts'].values():
      del layout['torque_bar']
      del layout['speed_limit_actions']
    del document['layouts']['large']['driver_monitor']
    with self.assertRaises(ValueError):
      validate_document(document)

  def test_disabled_widget_keeps_filter_updates_without_drawing(self):
    widget = TorqueBarWidget()
    data = PROFILES['compact']['bounds']
    rect = rl.Rectangle(*(data[key] for key in ('x', 'y', 'width', 'height')))
    document = default_document()
    document['layouts']['compact']['torque_bar']['enabled'] = False
    with record_gpu() as gpu:
      widget.render(rect, replace(scene(document), torque_utilization=.8), 536)
      self.assertFalse(gpu.frames or gpu.loads)
    self.assertGreater(widget._torque_filter.x, 0)
    self.assertGreater(widget._alpha_filter.x, 0)

  def test_unknown_source_or_inactive_lateral_resets_without_drawing(self):
    widget = TorqueBarWidget()
    for lateral, source in ((True, False), (False, True)):
      widget._torque_filter.x = widget._alpha_filter.x = .8
      state = replace(scene(), lateral_active=lateral, torque_source_available=source)
      with record_gpu() as gpu:
        widget.render(rl.Rectangle(0, 0, 476, 240), state, 536)
        self.assertFalse(gpu.frames or gpu.loads)
      self.assertEqual((widget._torque_filter.x, widget._alpha_filter.x), (0, 0))

  def test_new_drive_resets_existing_filters_before_first_fresh_render(self):
    widget = TorqueBarWidget()
    state = replace(scene(), lateral_active=True, torque_source_available=True, torque_drive_frame=10, torque_utilization=.8)
    rect = rl.Rectangle(0, 0, 476, 240)
    with record_gpu():
      for _ in range(30):
        widget.render(rect, state, 536)
      self.assertGreater(widget._torque_filter.x, .7)
      self.assertGreater(widget._alpha_filter.x, .9)
      state = replace(state, torque_drive_frame=100)
      widget.render(rect, state, 536)
      self.assertLess(widget._torque_filter.x, .2)
      self.assertLess(widget._alpha_filter.x, .2)
