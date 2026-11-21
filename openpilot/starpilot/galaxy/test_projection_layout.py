"""Actual file-owner tests, CPU-only, no IPC/Params runtime required."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from openpilot.starpilot.galaxy.onroad_layout import LayoutChanged
from openpilot.starpilot.galaxy.projection_layout import ProjectionLayoutOwner, host_owner
from openpilot.starpilot.system.starport.display_profile import record_screen
from openpilot.starpilot.system.starport.projection_layout import ProjectionLayoutSource
from openpilot.starpilot.ui.onroad_customization import default_document

SCREEN = {'version': 1, 'width': 1280, 'height': 720, 'margin_width': 0,
          'margin_height': 240, 'fps': 60, 'config_index': 0}


class Params:
  def __init__(self, root):
    self.root = root
    self.enabled = True
    self.jetlink_mode = 0
  def get(self, key):
    assert key == 'JetlinkMode'
    return self.jetlink_mode
  def get_param_path(self, key):
    return str(self.root / key)
  def get_bool(self, key):
    assert key == 'StarPortEnabled'
    return self.enabled


class TestProjectionLayoutOwner(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.params = Params(self.root)
    self.parked = True
    self.source = ProjectionLayoutSource(self.root / 'layouts/document.json')
    self.screen_path = self.root / 'screen.json'
    self.owner = ProjectionLayoutOwner(self.params, lambda: self.parked, self.source, self.screen_path)

  def screen(self, **changes):
    record_screen(SCREEN | changes, self.screen_path)

  def payload(self):
    snapshot = self.owner.snapshot()
    return {'revision': snapshot['revision'], 'document': copy.deepcopy(snapshot['document'])}

  def test_no_screen_no_fake_geometry(self):
    value = self.owner.snapshot()
    self.assertFalse(value['available'])
    self.assertFalse(value['editable'])
    self.assertFalse(value['valid'])
    self.assertIsNone(value['document'])
    self.assertIsNone(value['metadata'])
    self.assertIsNone(value['defaults'])
    self.assertIn('actual screen', value['reason'])

  def test_big_model_widget_available_without_jetlink_or_chestnut(self):
    self.screen()
    for mode in (0, 1, 2):
      self.params.jetlink_mode = mode
      self.assertNotIn('available', self.owner.snapshot()['metadata']['widgets']['jetlink_status'])

  def test_defaults_valid_false_then_exact_saved_document(self):
    self.screen()
    value = self.owner.snapshot()
    self.assertTrue(value['editable'])
    self.assertFalse(value['valid'])
    self.assertEqual(value['document']['canvas'], {'width': 2880, 'height': 1080})
    payload = self.payload()
    payload['document']['widgets']['current_speed']['x'] += 10
    payload['document']['blindSpotCamera'] = True
    saved = self.owner.save(payload, session_valid=lambda: True)
    self.assertTrue(saved['valid'])
    self.assertEqual(saved['document'], payload['document'])
    self.assertNotEqual(saved['revision'], payload['revision'])
    self.assertEqual(json.loads(self.source.path.read_bytes()), payload['document'])
    self.assertFalse((self.root / 'OnroadCustomizations').exists())

  def test_first_layout_starts_from_comma_display_choices(self):
    self.screen()
    for key, raw in {'BlindSpotMetrics': b'0', 'SignalMetrics': b'1', 'HidePersonalityAlerts': b'1', 'ShowBrakeStatus': b'1',
                     'HideLeadMarker': b'0', 'LeadInfo': b'1', 'LeadInfoMode': b'1'}.items():
      (self.root / key).write_bytes(raw)
    value = self.owner.snapshot()
    seeded = {key: value['document'][key] for key in ('blindSpotBorder', 'signalBorder', 'hidePersonalityAlerts',
                                                       'wheelPedalFeedback', 'leadIndicator', 'leadInfo', 'blindSpotCamera')}
    self.assertEqual(seeded, {'blindSpotBorder': False, 'signalBorder': True, 'hidePersonalityAlerts': True,
                              'wheelPedalFeedback': True, 'leadIndicator': True, 'leadInfo': 'Distance', 'blindSpotCamera': False})
    self.assertFalse(value['defaults']['signalBorder'], 'reset still returns to the plain defaults')
    saved = self.owner.save(self.payload(), session_valid=lambda: True)
    # Once saved, the AA screen keeps its own choices whatever the comma changes.
    (self.root / 'SignalMetrics').write_bytes(b'0')
    self.assertTrue(self.owner.snapshot()['document']['signalBorder'])
    self.assertEqual(self.owner.snapshot()['document'], saved['document'])

  def test_disable_map_turns_only_the_map_off_and_only_when_it_was_on(self):
    self.screen()
    self.assertFalse(self.owner.disable_map(session_valid=lambda: True), 'nothing saved yet')
    payload = self.payload()
    payload['document']['widgets']['nav_map']['enabled'] = True
    payload['document']['widgets']['nav_card']['x'] -= 5
    self.owner.save(payload, session_valid=lambda: True)
    self.assertFalse(self.owner.disable_map(session_valid=lambda: False), 'a stale session cannot change it')
    self.assertTrue(self.owner.snapshot()['document']['widgets']['nav_map']['enabled'])
    self.parked = False
    self.assertFalse(self.owner.disable_map(session_valid=lambda: True), 'only while parked')
    self.parked = True
    self.assertTrue(self.owner.disable_map(session_valid=lambda: True))
    saved = self.owner.snapshot()
    self.assertTrue(saved['valid'])
    expected = copy.deepcopy(payload['document'])
    expected['widgets']['nav_map']['enabled'] = False
    self.assertEqual(saved['document'], expected)
    self.assertFalse(self.owner.disable_map(session_valid=lambda: True))

  def test_disabled_retains_layout_but_unavailable(self):
    self.screen()
    self.owner.save(self.payload(), session_valid=lambda: True)
    before = self.source.path.read_bytes()
    self.params.enabled = False
    snapshot = self.owner.snapshot()
    self.assertFalse(snapshot['available'])
    self.assertFalse(snapshot['editable'])
    self.assertTrue(snapshot['valid'])
    self.assertIn('Enable StarPort', snapshot['reason'])
    with self.assertRaises(LayoutChanged):
      self.owner.save(self.payload(), session_valid=lambda: True)
    self.assertEqual(self.source.path.read_bytes(), before)

  def test_corrupt_and_other_canvas_default_without_overwrite(self):
    self.screen()
    self.source.prepare()
    self.source.path.write_bytes(b'bad')
    self.assertFalse(self.owner.snapshot()['valid'])
    self.assertEqual(self.source.path.read_bytes(), b'bad')
    self.owner.save(self.payload(), session_valid=lambda: True)
    before = self.source.path.read_bytes()
    self.screen(margin_height=0)
    value = self.owner.snapshot()
    self.assertFalse(value['valid'])
    self.assertEqual(value['document'], value['defaults'])
    self.assertEqual(self.source.path.read_bytes(), before)

  def test_revision_binds_exact_screen_bytes_and_document(self):
    self.screen()
    payload = self.payload()
    # Same geometry with a changed selected mode is still a changed receiver receipt.
    self.screen(config_index=1)
    with self.assertRaises(LayoutChanged):
      self.owner.save(payload, session_valid=lambda: True)
    self.assertFalse(self.source.path.exists())
    payload = self.payload()
    self.source.prepare()
    self.source.path.write_bytes(b'changed')
    with self.assertRaises(LayoutChanged):
      self.owner.save(payload, session_valid=lambda: True)
    self.assertEqual(self.source.path.read_bytes(), b'changed')

  def test_screen_change_during_commit_authorization_rejects(self):
    self.screen()
    payload = self.payload()
    calls = 0
    def session():
      nonlocal calls
      calls += 1
      if calls == 3:
        self.screen(config_index=2)
      return True
    with self.assertRaises(LayoutChanged):
      self.owner.save(payload, session_valid=session)
    self.assertGreaterEqual(calls, 3)
    self.assertFalse(self.source.path.exists())

  def test_session_revoke_or_unpark_inside_commit_rejects(self):
    self.screen()
    for mode in ('session', 'parked', 'disabled'):
      self.parked = True
      self.params.enabled = True
      calls = 0
      def session(mode=mode):
        nonlocal calls
        calls += 1
        if calls >= 3:
          if mode == 'session':
            return False
          if mode == 'parked':
            self.parked = False
          if mode == 'disabled':
            self.params.enabled = False
        return True
      with self.assertRaises(LayoutChanged):
        self.owner.save(self.payload(), session_valid=session)
      self.assertFalse(self.source.path.exists())

  def test_invalid_payload_or_bounds_never_written(self):
    self.screen()
    payload = self.payload()
    for broken in (payload | {'colors': {}}, payload | {'revision': None}):
      with self.assertRaises(ValueError):
        self.owner.save(broken, session_valid=lambda: True)
    payload['document']['widgets']['current_speed']['x'] = float('nan')
    with self.assertRaises(ValueError):
      self.owner.save(payload, session_valid=lambda: True)
    self.assertFalse(self.source.path.exists())

  def test_shared_colors_are_readonly_and_outside_layout(self):
    self.screen()
    colors = default_document()
    colors['palette']['text'] = '#123456FF'
    path = self.root / 'OnroadCustomizations'
    raw = json.dumps(colors).encode()
    path.write_bytes(raw)
    self.assertEqual(self.owner.snapshot()['colors']['palette']['text'], '#123456FF')
    self.owner.save(self.payload(), session_valid=lambda: True)
    self.assertEqual(path.read_bytes(), raw)
    self.assertNotIn('colors', json.loads(self.source.path.read_bytes()))

  def test_unreadable_or_invalid_screen_cannot_edit(self):
    for raw in (b'not json', b'x' * 2049, b'{"version":1,"version":1}'):
      self.screen_path.write_bytes(raw)
      self.assertFalse(self.owner.snapshot()['available'])
      with self.assertRaises(LayoutChanged):
        self.owner.save({'revision': 'any', 'document': {}}, session_valid=lambda: True)


  def test_host_runtime_seeds_private_screen_and_ignores_disabled(self):
    root = self.root / 'host'
    self.assertIsNone(host_owner(self.params, lambda: True, root, environ={}))
    self.assertFalse(root.exists())
    owner = host_owner(self.params, lambda: True, root, environ={'SP_HOST_RUNTIME': '1'})
    self.params.enabled = False
    snapshot = owner.snapshot()
    self.assertTrue(snapshot['editable'])
    self.assertIsNone(snapshot['reason'])
    self.assertEqual(snapshot['screen']['width'], 1920)
    payload = {'revision': snapshot['revision'], 'document': copy.deepcopy(snapshot['document'])}
    payload['document']['widgets']['current_speed']['x'] += 10
    owner.save(payload, session_valid=lambda: True)
    screen = (root / 'screen.json').read_bytes()
    again = host_owner(self.params, lambda: True, root, environ={'SP_HOST_RUNTIME': '1'})
    self.assertEqual((root / 'screen.json').read_bytes(), screen)
    self.assertEqual(again.snapshot()['document'], payload['document'])

  def test_host_editor_and_renderer_share_default_paths(self):
    # Constants are initialized at import time, as in the actual launcher processes.
    code = """
import copy
import os
from pathlib import Path
from types import SimpleNamespace
from openpilot.starpilot.galaxy.projection_layout import host_owner
from openpilot.starpilot.system.starport.projection_layout_runtime import load_projection_layout
root = Path(os.environ['STARPORT_DIR'])
params = SimpleNamespace(get_param_path=lambda key: str(root / 'params' / key), get_bool=lambda key: False, get=lambda key: None)
owner = host_owner(params, lambda: True)
snapshot = owner.snapshot()
payload = {'revision': snapshot['revision'], 'document': copy.deepcopy(snapshot['document'])}
payload['document']['widgets']['current_speed']['x'] += 10
owner.save(payload, session_valid=lambda: True)
assert load_projection_layout((1920, 1080)) == payload['document']
"""
    env = os.environ | {'SP_HOST_RUNTIME': '1', 'STARPORT_DIR': str(self.root / 'host')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, capture_output=True)


if __name__ == '__main__':
  unittest.main()
