"""Scene sketches and eye controls must agree with the native driving presentation."""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
from typing import NoReturn, cast

import pytest

from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, projection_customization
from openpilot.starpilot.ui.layout_preview_renderer import sample_state
from openpilot.starpilot.ui.layout_preview_transport import SCENES
from openpilot.starpilot.ui.onroad import axis_status_color, driving_mode_description
from openpilot.starpilot.ui.onroad_cem_gauge import gauge_data
from openpilot.starpilot.ui.onroad_customization import PROFILES, default_document
from openpilot.starpilot.ui.onroad_dm import monitor_visible
from openpilot.starpilot.ui.onroad_state import slc_controls
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed


@pytest.mark.parametrize('target', ('large', 'compact', 'projection'))
def test_every_scene_matches_native_border_mode_and_widget_visibility(target):
  node = shutil.which('node')
  if node is None:
    cast('Callable[[str], NoReturn]', pytest.skip)('Node is required for the browser/native scene contract')
  profile = Profile.COMPACT if target == 'compact' else Profile.LARGE
  document = default_document()
  if target == 'projection':
    document = projection_customization(default_layout_for_viewport((2880, 1080)), document)
  layout = document['layouts'][str(profile)]
  for placed in layout.values():
    placed['enabled'] = True
  module = Path(__file__).parents[2] / 'galaxy/web/js/layout-preview.js'
  script = f"""
    import {{PREVIEW_SCENES, previewScene, sceneHidesWidget}} from {json.dumps(module.as_uri())};
    const input = JSON.parse(await new Promise(resolve => {{
      let data = ''; process.stdin.on('data', chunk => data += chunk); process.stdin.on('end', () => resolve(data));
    }}));
    process.stdout.write(JSON.stringify(PREVIEW_SCENES.map(({{id}}) => ({{id, ...previewScene(id), hidden:
      Object.fromEntries(input.ids.map(key => [key, sceneHidesWidget(id, input.profile, key, input.layout, input.widgets)]))}}))));
  """
  ids = set(layout) | {'stopped_timer'}
  for move in (False, True):
    if move and profile == Profile.COMPACT:
      layout['driver_monitor'].update(x=200, y=100)
      layout['speed_limit'].update(x=200, y=100)
      layout['speed_limit_actions'].update(x=16, y=0)
    result = subprocess.run([node, '--input-type=module', '-e', script], input=json.dumps({
      'profile': str(profile), 'layout': layout, 'widgets': PROFILES[str(profile)]['widgets'], 'ids': sorted(ids),
    }), text=True, capture_output=True, check=True)
    scenes = json.loads(result.stdout)
    assert {scene['id'] for scene in scenes} == SCENES
    for scene in scenes:
      state = sample_state(scene['id'], document)
      color = axis_status_color(state)
      assert scene['border'] == f'#{color.r:02x}{color.g:02x}{color.b:02x}', (target, scene['id'])
      assert scene['mode'] == driving_mode_description(state)
      assert (scene['lateral'], scene['longitudinal']) == (state.lateral_active, state.longitudinal_active)
      expected = dict.fromkeys(ids, False)
      if scene['id'] != 'general':
        expected['stopped_timer'] = state.stopped_duration_s is None
        expected['torque_bar'] = not state.lateral_active
        expected['speed_limit_actions'] = scene['id'] in ('slc_pending', 'slc_notice') or not slc_controls(profile, state)
        if profile == Profile.LARGE:
          expected['cem_gauge'] = gauge_data(state) is None
        else:
          expected['steering_wheel'] = not state.lateral_active
          expected['max_speed'] = not state.cruise_active
          expected['speed_limit'] = str(state.speed_limit.kind) != 'valid'
          # Before native art arrives, scene rules cover eligibility; native pixels supply prompt occlusion.
          eligibility = replace(state, speed_limit=replace(state.speed_limit, pending_speed_limit_mps=None))
          expected['driver_monitor'] = not monitor_visible(profile, eligibility, fresh=True, onroad=True,
                                                         top_icons=state.cruise_active and layout['max_speed']['enabled'])
      assert scene['hidden'] == expected, (target, scene['id'], move)


def test_speed_limit_scenes_change_the_presentation_without_disengaging_driving():
  expected = {'slc_max': 'Using set speed', 'slc_equal': 'Using set speed', 'slc_pedal': 'Using accelerator',
              'slc_retained': 'Set-speed override', 'slc_disengaged': 'Cruise off',
              'slc_unavailable': 'Unavailable', 'slc_display': 'Display only'}
  for scene, status in expected.items():
    state = sample_state(scene, default_document())
    assert state.engaged == (scene != 'slc_disengaged')
    assert resolve_unified_speed(state).status == status
