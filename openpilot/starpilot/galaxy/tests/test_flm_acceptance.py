"""Accepting a finding saves explicit user intent without freezing its learner."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from openpilot.starpilot.flm.manual_coordinates import selected_manual
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES
from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
from openpilot.starpilot.galaxy.flm_acceptance import accept_finding
from openpilot.starpilot.galaxy.tests import test_flm_backup as fixtures
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY
from openpilot.starpilot.saved_source import read_saved


class TestFindingAcceptance(unittest.TestCase):
  def setUp(self):
    self.fixture = fixtures.FlmBackupTest()
    self.fixture.setUp()
    self.addCleanup(self.fixture.doCleanups)
    self.settings, self.params = self.fixture.gateway, self.fixture.params
    self.cp = self.fixture.fixture.context.value.cp
    self.cache = AssessmentCache(directory_for_params(self.params))
    self.source = 'c' * 64
    self.assessment = self.cache.put({'kind': 'completed_assessment', 'assessment': {
      'route': {'cp_sha256': car_params_digest(self.cp), 'source_sha256': self.source}}})
    self.report: dict[str, Any] = {'vehicle': str(self.cp.carFingerprint), 'current': {'fresh': True, 'details': {
      'Original completed route hash': self.source, 'Offroad assessment hash': self.assessment,
      'Recorded coordinate recommendations': [['ff_gain_left', 0., .005, 6, 'recorded_provisional_suggestion']]}}}
    self.reports = SimpleNamespace(snapshot=lambda: deepcopy(self.report))

  def accept(self, name='ff_gain_left', target=.005, **kwargs):
    return accept_finding(self.settings, self.reports, {'parameter': name, 'target': target, 'assessment': self.assessment},
                          authorized=kwargs.get('authorized', lambda: True))

  def raw(self):
    return read_saved(self.params, DOCUMENT_KEY, 16384)[0]

  def test_every_supported_coordinate_can_be_accepted_without_locking_it(self):
    for name in ROUTE_NAMES:
      with self.subTest(name=name):
        self.report['current']['details']['Recorded coordinate recommendations'] = [[name, 0., .001, 6, 'recorded_provisional_suggestion']]
        result = self.accept(name, .001)
        self.assertEqual(result['learning'], 'automatic')
        selected = selected_manual(self.cp, self.params)
        self.assertIn(name, selected.automatic)
        self.assertNotIn(name, selected.locked)
        self.assertEqual(dict(selected.values)[name], .001)

  def test_acceptance_preserves_other_values_learning_history_and_repeated_readback(self):
    before = self.fixture.stored()
    first = self.accept()
    raw = self.raw()
    self.assertEqual(self.accept(), first)
    self.assertEqual(self.raw(), raw)
    self.assertEqual(self.fixture.stored(), before)
    self.assertTrue(first['for_next_drive'])
    self.assertNotIn('qualified', first)
    self.assertEqual(selected_manual(self.cp, self.params).locked, ())

  def test_canonical_report_without_envelope_identity_uses_exact_original_car_params(self):
    self.report.pop('vehicle')
    self.assertEqual(self.accept()['learning'], 'automatic')

  def test_refuses_changed_target_conflicts_missing_review_and_nonfinite_values_without_writes(self):
    for change in ('target', 'conflict', 'stale', 'missing_cache', 'wrong_vehicle', 'wrong_cp', 'disabled', 'nonfinite', 'onroad'):
      with self.subTest(change=change):
        original = deepcopy(self.report)
        context = self.fixture.fixture.context.value
        preference = self.params.get('SteeringLearningPreferences')
        raw = self.raw()
        target = .005
        if change == 'target':
          target = .006
        elif change == 'conflict':
          self.report['current']['details']['Recorded coordinate recommendations'][0][4] = 'conflicting_recorded_candidates'
        elif change == 'stale':
          self.report['current']['fresh'] = False
        elif change == 'missing_cache':
          (self.cache.directory / (self.assessment + '.json')).rename(self.cache.directory / 'held.json')
        elif change == 'wrong_vehicle':
          self.report['vehicle'] = 'other'
        elif change == 'wrong_cp':
          cp = self.cp.as_reader().as_builder()
          cp.mass += 1
          self.fixture.fixture.context.value = replace(context, cp=cp, cp_raw=cp.to_bytes())
        elif change == 'disabled':
          from openpilot.starpilot.lateral.learning_preferences import replace_preference
          self.params.put('SteeringLearningPreferences', replace_preference(None, self.cp, False), block=True)
        elif change == 'nonfinite':
          target = float('nan')
        else:
          self.fixture.fixture.context.value = replace(context, parked=False)
        try:
          with self.assertRaises((ValueError, OSError)):
            self.accept(target=target)
          self.assertEqual(self.raw(), raw)
        finally:
          self.report = original
          self.fixture.fixture.context.value = context
          if change == 'missing_cache':
            (self.cache.directory / 'held.json').rename(self.cache.directory / (self.assessment + '.json'))
          if preference is None:
            self.params.remove('SteeringLearningPreferences')
          else:
            self.params.put('SteeringLearningPreferences', preference, block=True)

  def test_session_or_parked_authority_lost_during_commit_preserves_original(self):
    before = self.raw()
    calls = []
    def authorized():
      calls.append(True)
      return len(calls) < 4
    with self.assertRaises(ValueError):
      self.accept(authorized=authorized)
    self.assertEqual(self.raw(), before)

  def test_interrupted_replace_preserves_original_and_reports_unconfirmed(self):
    before = self.raw()
    with patch('openpilot.starpilot.saved_document.os.replace', side_effect=OSError('interrupted')):
      with self.assertRaisesRegex(ValueError, 'confirm'):
        self.accept()
    self.assertEqual(self.raw(), before)
    root = Path(self.params.get_param_path(DOCUMENT_KEY)).parent.parent
    self.assertEqual(list(root.glob('.accepted-steering-*')), [])

  def test_backup_roundtrip_preserves_acceptance_and_does_not_turn_it_into_a_manual_lock(self):
    self.accept()
    backup = self.fixture.owner.export()
    selected = selected_manual(self.cp, self.params)
    self.assertEqual(selected.automatic, ('ff_gain_left',))
    self.params.remove(DOCUMENT_KEY)
    preview = self.fixture.preview(backup, mode='manual')
    result = self.fixture.confirm(preview)
    self.assertTrue(result['manual'])
    restored = selected_manual(self.cp, self.params)
    self.assertEqual(restored, selected)
    self.assertEqual(restored.locked, ())

  def test_http_requires_session_same_origin_and_parked_state_before_saving(self):
    from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
    from openpilot.starpilot.galaxy.server import make_server
    access = GalaxyAccessOwner(Path(self.params.get_param_path(DOCUMENT_KEY)).parent.parent / 'accept-access')
    self.assertTrue(access.configure('password123', lambda: True))
    server = make_server(port=0, owner=access, settings=self.settings, training_results=self.reports)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    worker.start()
    def close():
      server.shutdown()
      worker.join(timeout=2)
      server.server_close()
    self.addCleanup(close)
    def post(path, body, cookie='', origin=None):
      connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
      try:
        connection.request('POST', path, body=json.dumps(body), headers={'Cookie': cookie, 'Content-Type': 'application/json',
                           'Origin': origin or f'http://127.0.0.1:{server.server_port}'})
        response = connection.getresponse()
        return response.status, dict(response.headers), json.loads(response.read())
      finally:
        connection.close()
    endpoint = '/api/settings/flm-finding/accept'
    body = {'parameter': 'ff_gain_left', 'target': .005, 'assessment': self.assessment}
    before = self.raw()
    self.assertEqual(post(endpoint, body)[0], 401)
    _, headers, _ = post('/api/auth/login', {'password': 'password123'})
    cookie = headers['Set-Cookie'].split(';')[0]
    self.assertEqual(post(endpoint, body, cookie, 'https://wrong.invalid')[0], 403)
    self.assertEqual(self.raw(), before)
    status, _, saved = post(endpoint, body, cookie)
    self.assertEqual(status, 200)
    self.assertEqual(saved['learning'], 'automatic')
    self.assertNotIn('ff_gain_left', selected_manual(self.cp, self.params).locked)
