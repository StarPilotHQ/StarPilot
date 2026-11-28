"""Portable backend contracts. All observations/history here are declared unit fixtures."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
from openpilot.starpilot.flm.offroad_daemon import StatusReport
from openpilot.starpilot.flm.tests.test_offroad_backend import values
from openpilot.starpilot.flm.offroad_surface import REVISION


class TestOffroadReport(unittest.TestCase):
  def test_lapped_parked_subscriber_waits_for_real_freshness_and_preserves_safety_refusals(self):
    from types import SimpleNamespace
    from openpilot.starpilot.flm.offroad_daemon import parked_status, wait_for_parked

    def fixture(refresh=True):
      now = [100.0]
      flags = {'IsOffroad': True, 'IsEngaged': False}
      params = SimpleNamespace(get_bool=lambda key: flags[key])

      class Messages:
        def __init__(self):
          self.data = {
            'deviceState': SimpleNamespace(started=False),
            'pandaStates': [SimpleNamespace(ignitionLine=False, ignitionCan=False, controlsAllowed=False, faults=[])],
            'peripheralState': SimpleNamespace(),
          }
          self.seen = dict.fromkeys(self.data, True)
          self.alive = dict.fromkeys(self.data, True)
          self.valid = dict.fromkeys(self.data, True)
          self.recv_time = dict.fromkeys(self.data, now[0])
          self.alive['pandaStates'] = False
          self.recv_time['pandaStates'] = 97.0
          self.calls = []

        def __getitem__(self, service):
          return self.data[service]

        def update(self, timeout):
          self.calls.append(timeout)
          now[0] += timeout / 1000
          if timeout and refresh:
            self.alive['pandaStates'] = True
            self.recv_time['pandaStates'] = now[0]

      return now, flags, params, Messages()

    now, _, params, sm = fixture()
    self.assertFalse(parked_status(sm, params, now=now[0]))
    self.assertTrue(wait_for_parked(sm, params, clock=lambda now=now: now[0]))
    self.assertEqual(sm.calls, [0, 20])
    self.assertTrue(parked_status(sm, params, now=now[0]))

    for unavailable in ('no_new_message', 'invalid', 'unseen'):
      with self.subTest(unavailable=unavailable):
        now, _, params, sm = fixture(refresh=unavailable != 'no_new_message')
        if unavailable == 'invalid':
          sm.valid['pandaStates'] = False
        elif unavailable == 'unseen':
          sm.seen['pandaStates'] = False
        self.assertFalse(wait_for_parked(sm, params, clock=lambda now=now: now[0]))
        self.assertLessEqual(now[0], 100.8)
        self.assertLessEqual(len(sm.calls), 40)

    for unsafe in ('onroad', 'engaged', 'started', 'ignition', 'controls', 'fault', 'stopped'):
      with self.subTest(unsafe=unsafe):
        now, flags, params, sm = fixture()
        if unsafe == 'onroad':
          flags['IsOffroad'] = False
        elif unsafe == 'engaged':
          flags['IsEngaged'] = True
        elif unsafe == 'started':
          sm['deviceState'].started = True
        elif unsafe == 'ignition':
          sm['pandaStates'][0].ignitionCan = True
        elif unsafe == 'controls':
          sm['pandaStates'][0].controlsAllowed = True
        elif unsafe == 'fault':
          sm['pandaStates'][0].faults.append('declared-fault')
        self.assertFalse(wait_for_parked(sm, params, stopped=lambda unsafe=unsafe: unsafe == 'stopped', clock=lambda now=now: now[0]))
        self.assertEqual(sm.calls, [0], 'Actual unsafe state refuses immediately instead of waiting')

  def test_completed_drive_reports_every_candidate_without_claiming_application(self):
    from openpilot.starpilot.flm.training_report import read_report
    from openpilot.starpilot.flm.offroad_worker import qualification_summary

    checks = {
      'windows': [{'qualified': True}] * 22
      + [
        {'qualified': False, 'numerical': {'qualified': False, 'reason': 'original_scored_frame_not_eligible'}},
        {
          'qualified': False,
          'numerical': {'qualified': True},
          'registered_encoder': {'qualified': False, 'detail': 'candidate changed an actual limiter/support'},
        },
      ]
    }
    summary = qualification_summary(checks)
    self.assertEqual((summary['passed'], summary['required']), (22, 24))

    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'report.json'
      report = StatusReport(path=path, clock_ns=lambda: 10_000_000_000)
      names = ['highway_center_taper_max', 'turn_in_boost_left', 'unwind_taper_left', 'turn_in_boost_right', 'unwind_taper_right']
      suggestions = [{'parameter': name, 'delta': 0.005, 'whole_windows': 9, 'qualification': 'controller_native_checks_not_passed'} for name in names]
      recorded = {
        'version': 1,
        'domain': 'lateral_acceleration',
        'samples': 2000,
        'windows': 10,
        'error_rms': 0.12,
        'route': 'declared-completed-drive',
        'segment': 0,
        'source_start_ns': 1_000_000_000,
        'source_end_ns': 2_900_000_000,
        'points': [[1_000_000_000 + i * 100_000_000, 0.2, 0.08] for i in range(20)],
        'sampling': 'input_ordered_original_endpoints_no_interpolation',
      }
      report.set(
        {
          'status': 'collecting',
          'reason': 'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application',
          'completed_route': 'declared-completed-drive',
          'analysis_counts': {'recorded_tracking': 241573},
          'suggestions': suggestions,
          'suggestion_count': len(suggestions),
          'configuration_changed': False,
          'recorded_tracking': recorded,
          'saved_adjustments': 0,
          'qualification_summary': summary,
        }
      )
      report.publish()
      current = read_report(path, now_ns=10_000_000_000)['current']
      assert current is not None
      self.assertEqual([r[0] for r in current['details']['Recorded route suggestions']], names)
      self.assertEqual(current['details']['Recorded suggestion count'], 5)
      self.assertEqual(current['progress']['samples'], 241573)
      self.assertFalse(current['details']['Automatic configuration changed'])
      self.assertFalse(current['application']['applied'])
      self.assertFalse(current['details']['Physical road improvement proven'])
      self.assertEqual(current['details']['Recorded steering response'], recorded)
      self.assertEqual(current['details']['Saved route adjustment count'], 0)
      self.assertEqual(current['details']['Controller and encoder check windows'], [22, 24])
      self.assertEqual(
        current['details']['Automatic tune refusal reasons'], [['candidate changed an actual limiter/support', 1], ['original_scored_frame_not_eligible', 1]]
      )

  def test_typed_current_application_and_completed_analysis_remain_distinct(self):
    with tempfile.TemporaryDirectory() as directory:
      now = 10_000_000_000
      control = ControlEvidence(
        identity='unit',
        generation=1,
        control_ns=now,
        state_ns=now,
        domain='torque',
        stage_revision='',
        response_revision='',
        requested=0.0,
        features=(),
        baseline_prediction=0.0,
        enabled=True,
        manual=False,
        active=True,
        valid=True,
        override=False,
        fault=False,
        saturated=False,
        limited=False,
        model_wobble=False,
        controller_chatter=False,
        route_applied_coordinates=tuple(values(0.01).items()),
        route_effect_revision=REVISION,
        route_configuration_scope='a' * 64,
      )
      path = Path(directory) / 'report.json'
      report = StatusReport(path=path, clock_ns=lambda: now)
      current = {
        'status': 'collecting',
        'reason': 'declared-unit',
        'enabled': True,
        'actual_control': control,
        'actual_control_ns': now,
        'actual_coordinates': control.route_applied_coordinates,
        'completed_route': 'declared-unit-route',
        'analysis_counts': {'recorded_tracking': 1000, 'qualified_whole_windows': 6},
      }
      report.set(current)
      report.publish()
      observed = json.loads(path.read_bytes())['current']
      self.assertTrue(observed['application']['applied'])
      self.assertEqual(observed['progress']['samples'], 1000)
      self.assertFalse(observed['details']['Physical road improvement proven'])
      for replacement in (None, replace(control, manual=True), replace(control, limited=True), replace(control, control_ns=now - 100_000_001)):
        with self.subTest(control=replacement):
          report.next_ns = 0
          report.set(dict(current, actual_control=replacement))
          report.publish()
          self.assertFalse(json.loads(path.read_bytes())['current']['application']['applied'])
