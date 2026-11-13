import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.onroad_layout import LayoutChanged, OnroadLayoutOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.ui.onroad_customization import MAX_BYTES, PARAM_KEY, default_document


class TestOnroadLayoutOwner(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.parked = True
    self.owner = OnroadLayoutOwner(self.params, lambda: self.parked)

  def changed(self, profile='large'):
    snapshot = self.owner.snapshot()
    document = copy.deepcopy(snapshot['document'])
    first = next(iter(document['layouts'][profile].values()))
    first['enabled'] = not first['enabled']
    return {'revision': snapshot['revision'], 'document': document}

  def test_defaults_and_profile_edits_survive_independent_saves(self):
    initial = self.owner.snapshot()
    self.assertTrue(initial['editable'])
    self.assertTrue(initial['valid'])
    self.assertEqual(initial['document'], default_document())
    self.assertIsNone(self.params.get(PARAM_KEY))
    large = self.changed()
    saved = self.owner.save(large, session_valid=lambda: True)
    self.assertEqual(saved['document']['layouts']['compact'], initial['document']['layouts']['compact'])
    compact = self.changed('compact')
    result = self.owner.save(compact, session_valid=lambda: True)
    self.assertEqual(result['document']['layouts']['large'], large['document']['layouts']['large'])
    self.assertEqual(self.params.get(PARAM_KEY), compact['document'])
    self.assertNotEqual(initial['revision'], result['revision'])

  def test_disable_map_turns_only_the_big_screen_map_off_and_only_when_it_was_on(self):
    self.assertFalse(self.owner.disable_map(lambda: True), 'nothing saved yet')
    payload = self.changed()
    payload['document']['layouts']['large']['nav_map']['enabled'] = True
    payload['document']['layouts']['large']['nav_card']['x'] -= 5
    self.owner.save(payload, session_valid=lambda: True)
    self.assertFalse(self.owner.disable_map(lambda: False))
    self.parked = False
    self.assertFalse(self.owner.disable_map(lambda: True))
    self.parked = True
    self.assertTrue(self.owner.disable_map(lambda: True))
    expected = copy.deepcopy(payload['document'])
    expected['layouts']['large']['nav_map']['enabled'] = False
    self.assertEqual(self.owner.snapshot()['document'], expected)
    self.assertFalse(self.owner.disable_map(lambda: True))

  def test_v1_read_is_non_mutating_and_widget_colors_save_with_source_revision(self):
    old = default_document()
    old['version'] = 1
    del old['layouts']['large']['cem_gauge']  # Version 1 predates the CEM gauge.
    del old['widgetColors']
    del old['roadColors']
    for profile in old['layouts'].values():
      del profile['steering_wheel']['size']
    old['palette']['text'] = '#ABCDEF80'
    old['layouts']['compact']['driver_monitor'].update(x=80, y=40, enabled=False)
    raw = json.dumps(old).encode()
    path = Path(self.params.get_param_path(PARAM_KEY))
    path.write_bytes(raw)
    snapshot = self.owner.snapshot()
    self.assertTrue(snapshot['valid'])
    self.assertEqual(path.read_bytes(), raw)
    expected_layouts = copy.deepcopy(old['layouts'])
    expected_layouts['large']['cem_gauge'] = default_document()['layouts']['large']['cem_gauge']
    for profile, placements in expected_layouts.items():
      placements['steering_wheel']['size'] = default_document()['layouts'][profile]['steering_wheel']['size']
    self.assertEqual(snapshot['document']['layouts'], expected_layouts)
    document = copy.deepcopy(snapshot['document'])
    document['widgetColors']['compact']['conditional_mode'] = {'cardFill': '#12345680'}
    saved = self.owner.save({'revision': snapshot['revision'], 'document': document}, session_valid=lambda: True)
    self.assertEqual(saved['document'], document)
    self.assertEqual(saved['document']['palette'], old['palette'])
    self.assertNotEqual(saved['revision'], snapshot['revision'])
    with self.assertRaises(LayoutChanged):
      self.owner.save({'revision': snapshot['revision'], 'document': document}, session_valid=lambda: True)

  def test_stale_browser_cannot_overwrite_another_profile_edit(self):
    stale = self.changed('compact')
    fresh = self.changed('large')
    self.owner.save(fresh, session_valid=lambda: True)
    with self.assertRaises(LayoutChanged):
      self.owner.save(stale, session_valid=lambda: True)
    self.assertEqual(self.params.get(PARAM_KEY), fresh['document'])

  def test_parked_and_session_authority_rechecked_during_commit(self):
    request = self.changed()
    self.parked = False
    self.assertFalse(self.owner.snapshot()['editable'])
    with self.assertRaises(LayoutChanged):
      self.owner.save(request, session_valid=lambda: True)
    self.parked = True
    calls = 0
    def session():
      nonlocal calls
      calls += 1
      return calls == 1
    with self.assertRaises(LayoutChanged):
      self.owner.save(request, session_valid=session)
    self.assertGreaterEqual(calls, 2)
    self.assertIsNone(self.params.get(PARAM_KEY))

  def test_corrupt_saved_document_can_be_repaired_without_silent_write(self):
    path = Path(self.params.get_param_path(PARAM_KEY))
    path.write_bytes(b'{"version":1,"version":2}')
    snapshot = self.owner.snapshot()
    self.assertFalse(snapshot['valid'])
    self.assertTrue(snapshot['editable'])
    self.assertEqual(snapshot['document'], default_document())
    self.assertEqual(path.read_bytes(), b'{"version":1,"version":2}')
    saved = self.owner.save({'revision': snapshot['revision'], 'document': default_document()}, session_valid=lambda: True)
    self.assertTrue(saved['valid'])
    path.write_bytes(b'x' * (MAX_BYTES + 1))
    self.assertFalse(self.owner.snapshot()['editable'])
    with self.assertRaises(OSError):
      self.owner.save({'revision': saved['revision'], 'document': default_document()}, session_valid=lambda: True)

  def test_unknown_and_invalid_requests_do_not_write(self):
    request = self.changed()
    for invalid in ({}, {**request, 'action': 'execute'}, {'revision': True, 'document': request['document']},
                    {'revision': request['revision'], 'document': {'version': 99}}):
      with self.subTest(request=invalid), self.assertRaises(ValueError):
        self.owner.save(invalid, session_valid=lambda: True)
    self.assertIsNone(self.params.get(PARAM_KEY))


class TestOnroadLayoutDisplay(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.parked = True
    self.owner = OnroadLayoutOwner(self.params, lambda: self.parked)

  def save(self, snapshot=None, **values):
    snapshot = snapshot or self.owner.snapshot()
    document = {**snapshot['document'], 'display': {**snapshot['display']['values'], **values}}
    return self.owner.save({'revision': snapshot['revision'], 'document': document}, session_valid=lambda: True)

  def test_snapshot_keeps_layout_pure_and_lists_display_rows(self):
    snapshot = self.owner.snapshot()
    self.assertEqual(snapshot['document'], default_document())
    rows = {row['key']: row for row in snapshot['display']['rows']}
    self.assertEqual(rows['LeadInfo']['requires'], 'HideLeadMarker')
    self.assertTrue(rows['LeadInfo']['available'], 'editable once the draft shows the Lead indicator')
    self.assertEqual({rows[key]['group'] for key in ('ShowBrakeStatus', 'StockConfidenceBallWidget')}, {'widget'})
    self.assertFalse({'HideSpeed', 'RainbowPath', 'StoppedTimer', 'EnableTorqueBarWidget'} & rows.keys())
    self.assertEqual(snapshot['display']['values']['HideLeadMarker'], 'Off')
    self.assertEqual(rows['DeveloperSidebar']['group'], 'Big layout')
    self.assertEqual(rows['DeveloperSidebar']['choices'], ['Off', 'On'])
    self.assertEqual(snapshot['display']['values']['DeveloperSidebar'], 'Off')
    self.assertEqual(snapshot['display']['defaults']['DeveloperSidebar'], 'Off')
    self.assertEqual(rows['UseVienna']['group'], 'Speed limits')
    self.assertEqual(rows['UseVienna']['choices'], ['Off', 'On'])
    self.assertEqual(snapshot['display']['defaults']['UseVienna'], 'Off')

  def test_use_vienna_saves_through_onroad_layout(self):
    saved = self.save(UseVienna='On')
    self.assertEqual(saved['failed'], [])
    self.assertTrue(self.params.get_bool('UseVienna'))
    self.assertEqual(saved['display']['values']['UseVienna'], 'On')
    saved = self.save(saved, UseVienna='Off')
    self.assertEqual(saved['failed'], [])
    self.assertFalse(self.params.get_bool('UseVienna'))

  def test_developer_sidebar_saves_to_its_own_param_and_rejects_stale_drafts(self):
    self.params.put_bool('DeveloperSidebar', True, block=True)
    saved = self.save(DeveloperSidebar='Off')
    self.assertEqual(saved['failed'], [])
    self.assertFalse(self.params.get_bool('DeveloperSidebar'))
    self.assertNotIn('DeveloperSidebar', self.params.get(PARAM_KEY))
    self.params.put_bool('DeveloperSidebar', True, block=True)
    with self.assertRaises(LayoutChanged):
      self.save(saved, DeveloperSidebar='Off')
    saved = self.save(DeveloperSidebar='Off')
    self.assertEqual(self.save(saved, DeveloperSidebar='On')['failed'], [])
    self.assertTrue(self.params.get_bool('DeveloperSidebar'))

  def test_layout_and_dependent_display_choices_save_together(self):
    saved = self.save(HideLeadMarker='On', LeadInfo='Distance', SignalMetrics='On')
    self.assertEqual(saved['failed'], [])
    self.assertEqual((self.params.get('HideLeadMarker'), self.params.get('LeadInfo'), self.params.get('SignalMetrics')),
                     (False, True, True))
    self.assertEqual(saved['display']['values']['LeadInfo'], 'Distance')
    # Turning both off orders Lead info first, while the indicator still allows it.
    saved = self.save(saved, HideLeadMarker='Off', LeadInfo='Off')
    self.assertEqual(saved['failed'], [])
    self.assertEqual((self.params.get('HideLeadMarker'), self.params.get('LeadInfo')), (True, False))

  def test_display_change_elsewhere_makes_the_draft_stale(self):
    snapshot = self.owner.snapshot()
    self.params.put_bool('SignalMetrics', True, block=True)
    with self.assertRaises(LayoutChanged):
      self.save(snapshot, BlindSpotMetrics='Off')
    self.assertIsNone(self.params.get(PARAM_KEY))

  def test_invalid_display_requests_do_not_write(self):
    for values in ({'HideSpeed': 'On'}, {'CameraView': 'Sideways'}, {'SignalMetrics': True}):
      with self.subTest(values=values), self.assertRaises(ValueError):
        self.save(**values)
    self.assertIsNone(self.params.get(PARAM_KEY))
    self.assertIsNone(self.params.get('SignalMetrics'))

  def test_unchanged_invalid_choice_is_kept_and_failed_choices_are_reported(self):
    Path(self.params.get_param_path('BlindSpotMetrics')).write_bytes(b'bad')
    snapshot = self.owner.snapshot()
    self.assertEqual(snapshot['display']['values']['BlindSpotMetrics'], 'Invalid saved choice')
    from unittest import mock
    with mock.patch('openpilot.starpilot.galaxy.onroad_layout.AppearanceOwner.apply', return_value=False):
      saved = self.save(snapshot, SignalMetrics='On')
    self.assertEqual(saved['failed'], ['C4 amber signal border'])
    self.assertEqual(Path(self.params.get_param_path('BlindSpotMetrics')).read_bytes(), b'bad')
    self.assertIsNotNone(self.params.get(PARAM_KEY), 'the layout itself still saved')


class TestOnroadLayoutHttp(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(str(Path(temporary.name) / 'params'))
    self.parked = True
    self.owner = OnroadLayoutOwner(self.params, lambda: self.parked)
    access = GalaxyAccessOwner(Path(temporary.name) / 'access')
    self.server = make_server(port=0, owner=access, layouts=self.owner)
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    self.worker.start()
    self.addCleanup(self.stop)

  def stop(self):
    self.server.shutdown()
    self.worker.join(timeout=2)
    self.server.server_close()

  def request(self, path, payload=None, cookie='', *, origin=None, raw=None):
    connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
    headers = {'Cookie': cookie}
    if payload is not None or raw is not None:
      headers.update({'Content-Type': 'application/json', 'Origin': origin or f'http://127.0.0.1:{self.server.server_port}'})
    try:
      connection.request('POST' if payload is not None or raw is not None else 'GET', path,
                         body=raw if raw is not None else json.dumps(payload) if payload is not None else None, headers=headers)
      response = connection.getresponse()
      return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
      connection.close()

  def login(self):
    code, _, headers = self.request('/api/auth/session', None)
    self.assertEqual(code, 200)
    return headers['Set-Cookie'].split(';', 1)[0]

  def test_local_access_layout_save_and_stale_write(self):
    self.assertEqual(self.request('/api/ui/layout')[0], 401)
    cookie = self.login()
    code, snapshot, _ = self.request('/api/ui/layout', cookie=cookie)
    self.assertEqual(code, 200)
    self.assertEqual(set(snapshot['metadata']['profiles']), {'large', 'compact'})
    request = {'revision': snapshot['revision'], 'document': snapshot['document']}
    covered_actions = copy.deepcopy(request)
    covered_actions['document']['layouts']['compact']['speed_limit']['y'] = -1
    self.assertEqual(self.request('/api/ui/layout', covered_actions, cookie)[0], 400)
    self.assertIsNone(self.params.get(PARAM_KEY))
    code, saved, _ = self.request('/api/ui/layout', request, cookie)
    self.assertEqual(code, 200)
    self.assertEqual(saved['document'], default_document())
    self.assertEqual(self.request('/api/ui/layout', request, cookie)[0], 409)
    self.parked = False
    self.assertEqual(self.request('/api/ui/layout', {'revision': saved['revision'], 'document': saved['document']}, cookie)[0], 409)
    self.assertEqual(self.params.get(PARAM_KEY), default_document())

  def test_mutation_requires_origin_session_and_unique_fields(self):
    cookie = self.login()
    _, snapshot, _ = self.request('/api/ui/layout', cookie=cookie)
    request = {'revision': snapshot['revision'], 'document': snapshot['document']}
    self.assertEqual(self.request('/api/ui/layout', request)[0], 401)
    self.assertEqual(self.request('/api/ui/layout', request, cookie, origin='http://example.com')[0], 403)
    self.assertEqual(self.request('/api/ui/layout', cookie=cookie, raw='{"revision":"a","revision":"b"}')[0], 400)
    self.assertEqual(self.request('/api/ui/layout', cookie=cookie, raw=' ' * 17409)[0], 413)
    self.assertIsNone(self.params.get(PARAM_KEY))
