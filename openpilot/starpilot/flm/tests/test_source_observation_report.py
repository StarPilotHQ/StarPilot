"""Actual receiver observations under the existing declared synthetic fixture.

No receiver flags/all_checks or native predicate is replaced. These unit inputs
are not CAN-earned permission, original receiver scheduling or road evidence.
"""
from openpilot.starpilot.flm.source_observation_report import record_refusal, refusal_report
from openpilot.starpilot.flm.tests.test_original_pose_work import BASE, fixture, observe, packet

SERVICES = ('deviceMotion', 'extrinsicsCalibration', 'carState', 'driverMonitoringState')


def receiver_state(sm):
  return tuple(tuple(getattr(sm, name)[service] for service in SERVICES)
               for name in ('seen', 'alive', 'valid', 'freq_ok', 'logMonoTime', 'recv_time'))


def test_actual_observer_refusal_reports_real_invalid_envelope_without_changing_receiver():
  with fixture() as (collector, cp, sm):
    invalid = packet('extrinsicsCalibration', BASE)
    invalid.valid = False
    sm.update_msgs(BASE/1e9, [invalid.as_reader()])
    before = receiver_state(sm)
    assert not sm.all_checks(list(SERVICES))
    assert observe(collector, cp, sm, BASE+1_000_000) is None
    assert receiver_state(sm) == before
    report = refusal_report(collector)
    assert report['counts'] == {'receiver_health': 1}
    assert report['publisherRefusal'] == 'unknown_not_transmitted'
    first = report['firstRefusal']
    assert first['reason'] == 'receiver_health'
    assert first['readyPairs'] == 1 and first['currentNative']
    assert not first['receiver']['extrinsicsCalibration']['valid']
    assert first['controlMonoTime'] == BASE-9_000_000
    assert first['applyMonoTime'] == BASE-8_000_000
    assert first['originalNativeObservedMonoTime'] == BASE-100_000_000
    assert first['originalNativeValidUntilMonoTime'] == BASE+100_000_000
    for service in SERVICES:
      assert first['receiver'][service] == {
        'seen': sm.seen[service], 'alive': sm.alive[service], 'valid': sm.valid[service],
        'freqOk': sm.freq_ok[service], 'sourceEventMonoTime': sm.logMonoTime[service],
        'receivedMonoTime': int(sm.recv_time[service]*1e9),
      }


def test_two_lifetime_snapshots_retain_original_times_across_repeated_refusals():
  with fixture() as (collector, cp, sm):
    pair = collector.ready.pop()
    before = receiver_state(sm)
    record_refusal(collector, sm, BASE, 'current_native_unavailable')
    first = refusal_report(collector)['firstRefusal']
    assert first['readyPairs'] == 0
    assert refusal_report(collector)['firstReadyPairRefusal'] is None
    collector.ready.append(pair)
    record_refusal(collector, sm, BASE+1, 'receiver_health')
    ready = refusal_report(collector)['firstReadyPairRefusal']
    for tick in range(1000):
      record_refusal(collector, sm, BASE+tick+2, 'receiver_health')
    report = refusal_report(collector)
    assert report['firstRefusal'] is first and first['observedMonoTime'] == BASE
    assert report['firstReadyPairRefusal'] is ready and ready['observedMonoTime'] == BASE+1
    assert report['counts'] == {'current_native_unavailable': 1, 'receiver_health': 1001}
    assert report['lastRefusal'] == {'reason': 'receiver_health', 'observedMonoTime': BASE+1001}
    assert receiver_state(sm) == before
    assert collector.ready[-1] is pair


def test_counter_cardinality_is_bounded_and_snapshot_failure_does_not_change_collection():
  with fixture() as (collector, cp, sm):
    before = (receiver_state(sm), tuple(collector.ready), collector.reason)
    for index in range(30):
      record_refusal(collector, None, BASE+index, 'declared_unit_reason_'+str(index))
    report = refusal_report(collector)
    assert len(report['counts']) == 24
    assert report['firstRefusal'] is None and report['firstReadyPairRefusal'] is None
    assert (receiver_state(sm), tuple(collector.ready), collector.reason) == before
    assert collector._source_observation_first_attempted
    assert collector._source_observation_ready_attempted


def test_source_plant_native_refusal_retains_existing_revocation():
  from openpilot.starpilot.flm.source_plant_runtime import observe as plant_observe
  with fixture() as (collector, cp, sm):
    before = receiver_state(sm)
    collector.adapter.current_physical_valid = True
    collector.adapter.last_physical_ns = collector.last_qualified_response = BASE-1
    plant_observe(sm, cp, collector, BASE, False)
    assert not collector.adapter.current_physical_valid
    assert collector.adapter.last_physical_ns == collector.last_qualified_response == 0
    assert receiver_state(sm) == before
    assert refusal_report(collector)['counts'] == {'current_native_unavailable': 1}


def test_existing_report_writer_exposes_bounded_observations_without_application(tmp_path):
  from types import SimpleNamespace
  from openpilot.cereal import messaging
  from openpilot.starpilot.flm.training_report import TrainingReporter, read_report, READ_MAX_BYTES
  with fixture() as (collector, cp, sm):
    collector.adapter.event_collection = True
    collector.adapter.source_plant_protocol = 'declared-unit-report-only'
    collector.adapter.replay = SimpleNamespace(records=(), checkpoints=(), emissions=SimpleNamespace(events=()))
    record_refusal(collector, sm, BASE, 'receiver_health')
    before = (receiver_state(sm), tuple(collector.ready), collector.reason)
    message = messaging.new_message('autoSteeringLearning', valid=True, logMonoTime=BASE)
    message.autoSteeringLearning.learning.status = 'collecting'
    path = tmp_path/'report.json'
    TrainingReporter('declared-unit-report-only', path=path).publish(
      now_ns=BASE, message=message, collector=collector, source=None,
      objective='declared-unit-report-only', pending=False, native_current=False)
    actual = read_report(path, now_ns=BASE)['current']
    assert actual['details']['GLOBAL source observation refusals'] == refusal_report(collector)
    assert not actual['application']['applied']
    assert path.stat().st_size <= READ_MAX_BYTES
    assert (receiver_state(sm), tuple(collector.ready), collector.reason) == before


def test_stall_log_exports_original_receiver_refusal_without_granting_application():
  import json
  from openpilot.starpilot.flm.source_stall_log import SourceStallLog, WAIT_NS
  with fixture() as (collector, cp, sm):
    record_refusal(collector, sm, BASE, 'receiver_health')
    before = (receiver_state(sm), tuple(collector.ready), collector.reason)
    emitted = []
    logger = SourceStallLog(emitted.append)
    logger.observe(collector, now_ns=BASE, bootstrap_allowed=True)
    assert not emitted
    logger.observe(collector, now_ns=BASE+WAIT_NS, bootstrap_allowed=True)
    assert len(emitted) == 1
    value = emitted[0]
    assert value['phase'] == 'original_pose_unavailable'
    assert value['controlMonoTime'] == collector.ready[-1][0].control_ns
    assert value['applyMonoTime'] == collector.ready[-1][1].apply_ns
    assert value['sourceObservation'] == refusal_report(collector)
    assert len(json.dumps(value)) < 8192
    assert (receiver_state(sm), tuple(collector.ready), collector.reason) == before
    assert not collector.original_source_capture.rows


def test_stall_logging_is_rate_limited_across_collector_replacement_and_sink_failure():
  from openpilot.starpilot.flm.source_stall_log import SourceStallLog, WAIT_NS, INTERVAL_NS
  attempted = []

  def broken_sink(value):
    attempted.append(value)
    raise OSError('declared unavailable log transport')

  logger = SourceStallLog(broken_sink)
  logger.observe(None, now_ns=BASE, bootstrap_allowed=True)
  for tick in range(1000):
    logger.observe(None, now_ns=BASE+WAIT_NS+tick, bootstrap_allowed=True)
  assert len(attempted) == 1
  logger.observe(None, now_ns=BASE+WAIT_NS+INTERVAL_NS, bootstrap_allowed=False)
  assert len(attempted) == 2
  assert attempted[-1]['phase'] == 'bootstrap_deferred'


def test_context_failure_log_keeps_old_pose_clock_and_bounds_error():
  from types import SimpleNamespace
  from openpilot.starpilot.flm.source_stall_log import SourceStallLog, WAIT_NS
  with fixture() as (collector, cp, sm):
    original = SimpleNamespace(mono_ns=BASE-100_000_001)
    collector.original_source_capture.rows.append(original)
    emitted = []
    logger = SourceStallLog(emitted.append)
    logger.observe(collector, now_ns=BASE, bootstrap_allowed=True)
    logger.observe(collector, now_ns=BASE+WAIT_NS, bootstrap_allowed=True, context_error=ValueError('old original clock '+200*'x'))
    assert emitted[0]['phase'] == 'source_context_rejected'
    assert len(emitted[0]['contextError']) == 192
    assert collector.original_source_capture.rows == [original]
    assert original.mono_ns == BASE-100_000_001


def test_actual_observer_exception_is_reported_with_existing_refusal_intact():
  from unittest import mock
  from openpilot.selfdrive.locationd.helpers import Pose
  with fixture() as (collector, cp, sm):
    before = (receiver_state(sm), tuple(collector.ready))
    with mock.patch.object(Pose, 'from_device_motion', side_effect=ValueError('declared original pose decode failure')):
      assert observe(collector, cp, sm, BASE) is None
    report = refusal_report(collector)
    assert report['lastRefusal'] == {'reason': 'source_observation_exception', 'observedMonoTime': BASE}
    assert report['lastException'] == {'observedMonoTime': BASE, 'type': 'ValueError', 'message': 'declared original pose decode failure'}
    assert not collector.original_source_capture.rows
    assert collector.reason == 'initializing'
    assert (receiver_state(sm), tuple(collector.ready)) == before


def test_stall_checkpoint_reports_current_health_without_rewriting_first_refusal():
  from openpilot.starpilot.flm.source_stall_log import SourceStallLog, WAIT_NS
  with fixture() as (collector, cp, sm):
    record_refusal(collector, sm, BASE, 'receiver_health')
    original = refusal_report(collector)['firstRefusal']
    sm.freq_ok['driverMonitoringState'] = False
    before = (receiver_state(sm), tuple(collector.ready), collector.reason)
    emitted = []
    logger = SourceStallLog(emitted.append)
    logger.observe(collector, now_ns=BASE, bootstrap_allowed=True, sm=sm)
    logger.observe(collector, now_ns=BASE+WAIT_NS, bootstrap_allowed=True, sm=sm)
    logger.observe(collector, now_ns=BASE+WAIT_NS+1, bootstrap_allowed=True, sm=sm)
    assert len(emitted) == 1
    current = emitted[0]['receiverCurrent']
    assert current['observedMonoTime'] == BASE+WAIT_NS
    assert not current['receiver']['driverMonitoringState']['freqOk']
    assert original['receiver']['driverMonitoringState']['freqOk']
    assert emitted[0]['sourceObservation']['firstRefusal'] == original
    assert (receiver_state(sm), tuple(collector.ready), collector.reason) == before
