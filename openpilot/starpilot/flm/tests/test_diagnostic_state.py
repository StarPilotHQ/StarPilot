from dataclasses import replace
"""Support downloads explain coarse learner outcomes without changing tuning."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.flm import offroad_daemon as daemon
from openpilot.starpilot.flm.diagnostic_state import DiagnosticState, MAX_BYTES, MAX_EVENTS, path_for_params, read_state
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
from openpilot.starpilot.galaxy.flm_diagnostics import download


class TestDiagnosticState(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.params = Params(str(self.root / 'params'))
    self.params.put('GitCommit', 'a' * 40, block=True)

  def exported(self, report=None):
    return json.loads(download({} if report is None else report, self.params)[1])

  def test_last_analysis_counts_and_exact_refusals_survive_driving_and_restart(self):
    journal = DiagnosticState(self.params)
    for reason in ('loading_parked_analysis', 'analyzing_last_completed_drive', 'checking_recorded_steering_recommendations'):
      self.assertTrue(journal.observe({'status': 'fitting', 'reason': reason}))
    self.assertTrue(journal.observe({'status': 'collecting', 'reason': 'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application',
      'completed_route': '1234567890abcdef/00000049--1b13f9d9d3', 'analysis_counts': {'recorded_tracking': 1234},
      'recommendation_count': 3, 'saved_adjustments': 0,
      'qualification_summary': {'passed': 2, 'required': 8, 'refusals': [('independent_native_validation_not_run', 6)]}}))
    journal.observe({'status': 'collecting', 'reason': 'recording_for_offroad_analysis', 'enabled': True})
    restarted = DiagnosticState(self.params)
    self.assertEqual(restarted.state, journal.state)
    exported = self.exported({'current': {'reason': 'recording_for_offroad_analysis'}})
    history = exported['learning_diagnostics']
    self.assertEqual(history['current']['stage'], 'recording')
    self.assertEqual(history['last_attempt']['stage'], 'checking_recommendations')
    self.assertEqual(history['last_result']['analysis_counts'], {'recorded_tracking': 1234})
    self.assertEqual(history['last_result']['qualification'], {'passed': 2, 'required': 8, 'refusals': [['independent_native_validation_not_run', 6]]})
    self.assertEqual(history['last_result']['saved_adjustments'], 0)
    self.assertIn('automatic saving', history['last_result']['explanation'])
    self.assertNotIn('1234567890abcdef', json.dumps(exported))
    self.assertEqual(exported['storage']['status'], 'not_created')

  def test_four_hz_control_status_does_not_write_or_retain_control_objects(self):
    journal = DiagnosticState(self.params)
    value = {'status': 'collecting', 'reason': 'recording_for_offroad_analysis', 'enabled': True}
    journal.observe(value)
    path = path_for_params(self.params)
    before = path.read_bytes(), path.stat().st_mtime_ns
    with patch('openpilot.starpilot.flm.diagnostic_state.os.replace', side_effect=AssertionError('Repeated tick wrote to disk')):
      for frame in range(1000):
        self.assertTrue(journal.observe(dict(value, actual_control=object(), actual_control_ns=frame)))
    self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
    self.assertEqual(len(journal.state['events']), 1)
    self.assertFalse(directory_for_params(self.params).exists())

  def test_restart_records_interrupted_stage_and_never_invents_a_result(self):
    journal = DiagnosticState(self.params)
    journal.observe({'status': 'fitting', 'reason': 'analyzing_last_completed_drive'})
    DiagnosticState(self.params)
    result = self.exported()['learning_diagnostics']
    self.assertEqual(result['last_result']['reason'], 'previous_analysis_interrupted_before_completion')
    self.assertEqual(result['last_result']['interrupted_stage'], 'reading_recording')
    self.assertNotIn('saved_adjustments', result['last_result'])
    self.assertEqual(result['last_attempt']['stage'], 'reading_recording')

  def test_new_analysis_diagnostics_use_new_counts_while_display_retains_prior_findings(self):
    report = daemon.StatusReport(path=self.root / 'report.json')
    report.diagnostics = DiagnosticState(self.params)
    report.previous = {'completed_route': 'old', 'analysis_counts': {'recorded_tracking': 99}}
    report.analyzing({'status': 'fitting', 'reason': 'checking_recorded_steering_recommendations',
                      'completed_route': 'new', 'analysis_counts': {'recorded_tracking': 1234}})
    self.assertEqual(report.current['analysis_counts']['recorded_tracking'], 99)
    self.assertEqual(read_state(self.params)['last_attempt']['analysis_counts']['recorded_tracking'], 1234)

  def test_current_verification_and_its_interruption_are_distinct_from_previous_drive(self):
    from openpilot.starpilot.flm.offroad_route import RouteInputCancelled

    report = daemon.StatusReport(path=self.root / 'report.json')
    report.diagnostics = DiagnosticState(self.params)
    report.previous = {'completed_route': 'earlier-drive', 'vehicle': 'KIA_NIRO_PHEV_2022',
                       'analysis_counts': {'original_segments_reviewed': 3, 'original_segments_available': 3}}
    report.analyzing({'status': 'fitting', 'reason': 'verifying_recorded_drive',
                      'analysis_progress': {'phase': 'verifying', 'completed': 2, 'total': 2}})
    report.publish()
    active = self.exported(json.loads(report.path.read_bytes()))
    self.assertEqual(active['current']['analysis']['Parked analysis progress'], ['verifying', 2, 2])
    self.assertTrue(active['current']['analysis']['Earlier analysis shown while updating'])
    self.assertEqual(active['current']['analysis']['Recorded drive coverage'], '3 of 3 original recorded segments reviewed')

    def cancelled():
      raise RouteInputCancelled('Parked route input collection cancelled')

    worker = SimpleNamespace(run_once=cancelled)
    reason = 'waiting_for_fresh_deviceState'
    self.assertFalse(daemon.run_parked_analysis(worker, report, permission_loss=lambda: reason))
    self.assertEqual(report.current['reason'], reason)
    outcome = self.exported()['learning_diagnostics']['last_result']
    self.assertEqual(outcome['reason'], reason)
    self.assertEqual(outcome['interrupted_stage'], 'verifying_recording')
    self.assertEqual(report.previous['completed_route'], 'earlier-drive')
    self.assertFalse(directory_for_params(self.params).exists())

  def test_worker_startup_failure_is_captured_before_assessment_cache_exists(self):
    self.params.put_bool('IsOffroad', True, block=True)
    self.params.put('AutomaticSteeringProfiles', b'preserved-original', block=True)
    report = daemon.StatusReport(path=self.root / 'report.json')
    worker = SimpleNamespace(OffroadWorker=lambda *args, **kwargs: (_ for _ in ()).throw(OSError('worker startup denied')))
    with patch.object(daemon, 'Params', return_value=self.params), patch.object(daemon, 'StatusReport', return_value=report), \
         patch.object(daemon.messaging, 'SubMaster', return_value=SimpleNamespace(sock={})), \
         patch.object(daemon, 'wait_for_parked', return_value=True), \
         patch.dict(sys.modules, {'openpilot.starpilot.flm.offroad_worker': worker}):
      with self.assertRaisesRegex(OSError, 'worker startup denied'):
        daemon._run()
    self.assertEqual(self.params.get('AutomaticSteeringProfiles'), b'preserved-original')
    self.assertFalse(directory_for_params(self.params).exists())
    result = self.exported()['learning_diagnostics']['last_result']
    self.assertEqual(result['reason'], 'OSError: worker startup denied')
    self.assertEqual(result['interrupted_stage'], 'starting_analysis')

  def test_parked_blocker_names_preserve_actual_admission_rules(self):
    self.params.put_bool('IsOffroad', True, block=True)
    services = ('deviceState', 'pandaStates', 'peripheralState')
    panda = SimpleNamespace(ignitionLine=False, ignitionCan=False, controlsAllowed=False, faults=[])
    messages = {'deviceState': SimpleNamespace(started=False), 'pandaStates': [panda]}
    class Status:
      seen = dict.fromkeys(services, True)
      alive = dict.fromkeys(services, True)
      valid = dict.fromkeys(services, True)
      recv_time = dict.fromkeys(services, 1.0)
      def __getitem__(self, key):
        return messages[key]
    sm = Status()
    self.assertIsNone(daemon.parked_blocker(sm, self.params, now=2.0))
    self.assertTrue(daemon.parked_status(sm, self.params, now=2.0))
    cases: list[tuple[dict[str, Any], str, Any, str]] = [(sm.valid, 'pandaStates', False, 'waiting_for_valid_pandaStates'),
             (sm.recv_time, 'deviceState', -2.0, 'waiting_for_fresh_deviceState'),
             (panda.__dict__, 'ignitionCan', True, 'waiting_for_ignition_off'),
             (panda.__dict__, 'controlsAllowed', True, 'waiting_for_panda_controls_to_disarm'),
             (panda.__dict__, 'faults', [1], 'waiting_for_panda_fault_to_clear')]
    journal = DiagnosticState(self.params)
    for values, key, bad, reason in cases:
      original, values[key] = values[key], bad
      self.assertFalse(daemon.parked_status(sm, self.params, now=2.0))
      self.assertEqual(daemon.parked_blocker(sm, self.params, now=2.0), reason)
      journal.observe({'status': 'collecting', 'reason': reason})
      self.assertEqual(self.exported()['learning_diagnostics']['current']['reason'], reason)
      values[key] = original

  def test_failed_write_is_visible_preserves_old_record_and_does_not_stop_reporting(self):
    report = daemon.StatusReport(path=self.root / 'report.json')
    report.diagnostics = DiagnosticState(self.params)
    report.set({'status': 'collecting', 'reason': 'recording_for_offroad_analysis'})
    path = path_for_params(self.params)
    original = path.read_bytes()
    with patch('openpilot.starpilot.flm.diagnostic_state.os.replace', side_effect=OSError('disk full')) as replace:
      for _ in range(5):
        report.set({'status': 'unavailable', 'reason': 'local recordings unavailable'})
      self.assertEqual(replace.call_count, 1)
    self.assertEqual(path.read_bytes(), original)
    self.assertEqual(list(path.parent.glob('.flm-diagnostics-*')), [])
    report.publish()
    wire = json.loads(report.path.read_text())
    self.assertEqual(wire['current']['reason'], 'local recordings unavailable')
    self.assertEqual(self.exported(wire)['learning_diagnostics']['write_error'], 'disk full')

  def test_invalid_existing_history_is_preserved_and_download_explains_it(self):
    path = path_for_params(self.params)
    for raw in (b'{broken', b'{}', b'x' * (MAX_BYTES + 1)):
      path.write_bytes(raw)
      journal = DiagnosticState(self.params)
      self.assertFalse(journal.observe({'reason': 'recording_for_offroad_analysis'}))
      self.assertEqual(path.read_bytes(), raw)
      self.assertEqual(self.exported()['learning_diagnostics']['status'], 'unavailable')
    path.unlink()
    private = self.root / 'private'
    private.write_text('preserve')
    path.symlink_to(private)
    self.assertEqual(read_state(self.params)['status'], 'unavailable')
    self.assertFalse(DiagnosticState(self.params).observe({'reason': 'recording_for_offroad_analysis'}))
    self.assertTrue(path.is_symlink())
    self.assertEqual(private.read_text(), 'preserve')

  def test_bounded_history_and_privacy_even_with_large_reasons_and_refusals(self):
    journal = DiagnosticState(self.params)
    for index in range(30):
      self.assertTrue(journal.observe({'status': 'unavailable', 'reason': str(index) + 'x' * 1000,
        'analysis_counts': {'count_' + chr(97 + i // 26) + chr(97 + i % 26): 2**62 for i in range(64)},
        'qualification_summary': {'refusals': [('x' * 1000, 2**62)] * 16}}))
    self.assertLessEqual(path_for_params(self.params).stat().st_size, MAX_BYTES)
    self.assertLessEqual(len(read_state(self.params)['events']), MAX_EVENTS)
    reason = 'password=secret https://example.invalid/token /data/private/route 1234567890abcdef/00000049--1b13f9d9d3 JTDAC3EH0B3000000'
    journal.observe({'status': 'unavailable', 'reason': reason, 'skipped_routes': [['private', 'secret=hidden']]})
    body = download({'history': [{'reason': ('password=tuple-secret',)}]}, self.params)[1]
    for secret in (b'password=', b'example.invalid', b'/data/private', b'1234567890abcdef', b'JTDAC3EH0B3000000', b'secret=hidden'):
      self.assertNotIn(secret, body)

  def test_old_export_without_persistent_record_does_not_assert_learning_never_ran(self):
    before = list(path_for_params(self.params).parent.iterdir())
    result = self.exported()
    self.assertEqual(result['learning_diagnostics']['status'], 'not_recorded')
    self.assertEqual(list(path_for_params(self.params).parent.iterdir()), before)

  def test_volt_learning_respects_active_manual_tune_and_the_visible_preference(self):
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import CAR
    from openpilot.starpilot.flm.manual_coordinates import ManualCoordinates, context, selected_manual
    from openpilot.starpilot.flm.offroad_runtime import configuration_data
    from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES
    from openpilot.starpilot.flm.runtime_adapter_registry import ControllerDescriptor, car_params_digest, read_source
    from openpilot.starpilot.flm.torque_surface import GmFlmBinding, GmSurface
    from openpilot.starpilot.lateral.controller_selection import read_selection
    from openpilot.starpilot.lateral.learning_preferences import KEY, replace_preference
    from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, FieldChoice, GeometryProfile, PlatformProfile, parse_document, serialize_document

    cp = CarInterface.get_non_essential_params(CAR.CHEVROLET_VOLT_ASCM)
    cp.carVin = '1G1RA6E42DU000001'
    tune = cp.lateralTuning.torque
    basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
    self.params.put_bool('OpenpilotEnabledToggle', True, block=True)
    mode = str(read_selection(self.params, cp).mode)
    fingerprint = str(cp.carFingerprint)
    descriptor = ControllerDescriptor('torque', mode, 'volt', 'test-revision', 'test-stage', car_params_digest(cp), '', False)
    surface = GmSurface('torque_universal', {'ff_gain_left': .01}, None)
    legacy = GmFlmBinding(mode, 'volt', basis, (('saved', 'Saved tune', surface),))
    geometry = GeometryProfile((float(cp.steerRatio), float(cp.steerActuatorDelay) + .2), learning='force_off')
    profile = PlatformProfile(basis, FieldChoice(), FieldChoice(), geometry=geometry, flm=legacy)
    values = dict.fromkeys(ROUTE_NAMES, 0.) | {'ff_gain_left': .01, 'unwind_taper_right': .005}
    manual = ManualCoordinates(context(cp, mode, recovered=True), tuple(values.items()), automatic=('unwind_taper_right',))

    def store(current):
      self.params.put(DOCUMENT_KEY, json.loads(serialize_document({fingerprint: current})), block=True)
      return self.params.get(DOCUMENT_KEY)

    def source():
      return read_source(cp, self.params, now_ns=1_000_000_000, calibration=(0., 0., 0.), calibration_generation='a' * 32)

    def configure():
      with patch('openpilot.starpilot.flm.runtime_adapter_registry.selected_descriptor', return_value=descriptor):
        return configuration_data(cp, None, SimpleNamespace(steer_max=1.), self.params, manual_status='configured')[0]

    self.params.put(KEY, replace_preference(None, cp, True), block=True)
    for current in (replace(profile, flm=None), replace(profile, geometry=None), profile, replace(profile, manual_coordinates=manual)):
      with self.subTest(legacy=current.flm is not None, geometry=current.geometry is not None, manual=current.manual_coordinates is not None):
        before = store(current)
        assert before is not None
        self.assertEqual(parse_document(json.dumps(before).encode()), {fingerprint: current})
        received = source()
        self.assertTrue(received.enabled)
        self.assertFalse(received.manual)
        self.assertEqual(configure().persistent_source_epoch, received.persistent_source_epoch)
        if current.manual_coordinates is not None:
          selected = selected_manual(cp, self.params)
          assert selected is not None
          self.assertEqual(selected.locked, ('ff_gain_left',))
        self.assertEqual(self.params.get(DOCUMENT_KEY), before)

    before = store(profile)
    self.params.put(KEY, replace_preference(None, cp, False), block=True)
    self.assertFalse(source().enabled)
    with self.assertRaisesRegex(ValueError, 'Automatic learning is off'):
      configure()
    self.assertEqual(self.params.get(DOCUMENT_KEY), before)

    self.params.put(KEY, replace_preference(None, cp, True), block=True)
    active = replace(profile, flm=replace(legacy, active='saved', applied=True))
    before = store(active)
    self.assertTrue(source().manual)
    self.assertFalse(source().enabled)
    reason = 'saved_gm_flm_profile_blocks_learning'
    with self.assertRaisesRegex(ValueError, '^' + reason + '$'):
      configure()
    with self.assertRaisesRegex(ValueError, 'Only one manual surface'):
      serialize_document({fingerprint: replace(active, manual_coordinates=manual)})
    self.assertIn('manual GM FLM tune is active', self.exported({'current': {'reason': reason}})['current']['explanation'])
    self.assertEqual(self.params.get(DOCUMENT_KEY), before)

  def test_malformed_nested_diagnostic_data_does_not_enter_the_download(self):
    journal = DiagnosticState(self.params)
    journal.observe({'status': 'collecting', 'reason': 'recording_for_offroad_analysis'})
    value = json.loads(path_for_params(self.params).read_bytes())
    value['current']['analysis_counts'] = {'unapproved_field': {'private_raw_data': 'not a count'}}
    raw = json.dumps(value).encode()
    path_for_params(self.params).write_bytes(raw)
    result = self.exported()['learning_diagnostics']
    self.assertEqual(result['status'], 'unavailable')
    self.assertNotIn('private_raw_data', json.dumps(result))
    self.assertEqual(path_for_params(self.params).read_bytes(), raw)

  def test_bad_native_cache_does_not_hide_an_independent_analysis_error(self):
    self.params.put('LiveDelay', b'bad schema bytes', block=True)
    journal = DiagnosticState(self.params)
    journal.observe({'status': 'unavailable', 'reason': 'Local drive inventory is incomplete'})
    result = self.exported()
    self.assertEqual(result['native_learning_caches']['LiveDelay']['status'], 'incompatible')
    self.assertEqual(result['learning_diagnostics']['last_result']['reason'], 'Local drive inventory is incomplete')
    self.assertIn('complete list', result['learning_diagnostics']['last_result']['explanation'])

  def test_qualification_reasons_are_exported_from_existing_checkpoints_without_new_history(self):
    cache = AssessmentCache(directory_for_params(self.params))
    cache.checkpoint({'input_fingerprint': 'b' * 64, 'result': {'status': 'collecting', 'reason': 'more_independent_eligible_tracking_required',
      'qualification_summary': {'passed': 0, 'required': 6, 'refusals': [['missing_original_controller_metadata', 6]]}}})
    result = self.exported()
    self.assertEqual(result['checkpoints'][0]['qualification']['refusals'], [['missing_original_controller_metadata', 6]])
    self.assertIn('independent', result['checkpoints'][0]['explanation'])
    self.assertEqual(result['learning_diagnostics']['status'], 'not_recorded')

  def test_download_distinguishes_old_configuration_from_changed_finding_source(self):
    from opendbc.car.toyota.interface import CarInterface
    from opendbc.car.toyota.values import CAR
    from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
    from openpilot.starpilot.schema_cache import put_cache

    cp = CarInterface.get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    cp.carVin = 'JTDAC3EH0B3000000'
    self.params.put_bool('IsOffroad', True, block=True)
    put_cache(self.params, 'CarParamsPersistent', cp.as_reader(), block=True)
    cache = AssessmentCache(directory_for_params(self.params))
    parked = car_params_digest(cp)
    cp.mass += 50
    old, source = car_params_digest(cp), 'a' * 64
    def assessment(digest, vehicle):
      return cache.put({'kind': 'partial_assessment', 'assessment': {
        'vehicle': vehicle, 'route': {'cp_sha256': digest, 'source_sha256': source}}})
    older, matching = assessment(old, 'other-platform'), assessment(parked, str(cp.carFingerprint))
    report = {'preference': {'valid': True, 'parked': True, 'vehicle': str(cp.carFingerprint)}, 'current': {'details': {
      'Recorded suggestion assessments': [
        ['turn_in_boost_left', older, source], ['turn_in_boost_right', matching, 'b' * 64],
        ['unwind_taper_right', matching, source], ['unwind_taper_left', 'f' * 64, source],
      ],
    }}}
    before = {p.name: p.read_bytes() for p in cache.directory.iterdir()}
    review = self.exported(report)['finding_review']
    self.assertEqual(review['manual_coordinates'], 'not_configured')
    self.assertEqual(review['findings'], {
      'turn_in_boost_left': 'vehicle_mismatch', 'turn_in_boost_right': 'recording_mismatch',
      'unwind_taper_right': 'matches_parked_fingerprint', 'unwind_taper_left': 'assessment_unavailable',
    })
    self.assertEqual({p.name: p.read_bytes() for p in cache.directory.iterdir()}, before)
    for private in (parked, old, cp.carVin):
      self.assertNotIn(private, json.dumps(self.exported(report)))
    self.params.put_bool('IsOffroad', False, block=True)
    self.assertEqual(self.exported(report)['finding_review']['status'], 'parked_configuration_unavailable')

  def test_native_cache_is_reported_separately_from_parked_route_analysis(self):
    from openpilot.cereal import messaging
    from openpilot.starpilot.schema_cache import put_cache
    message = messaging.new_message('lateralDelay')
    message.valid = True
    message.lateralDelay.version = 1
    message.lateralDelay.status = 'estimated'
    put_cache(self.params, 'LiveDelay', message, block=True)
    result = self.exported()
    self.assertEqual(result['storage']['status'], 'not_created')
    self.assertEqual(result['stored_profile_counts']['parked_route_profiles'], 0)
    self.assertEqual(result['native_learning_caches']['LiveDelay']['status'], 'valid')
    self.assertTrue(result['native_learning_caches']['LiveDelay']['estimate_valid'])
    self.assertIn('not proof', result['native_learning_caches']['LiveDelay']['scope'])
