"""Explicit V2 live calibrated-frame lifecycle; V1 exact-basis remains supported."""
import math
from types import SimpleNamespace

import pytest
from openpilot.cereal import messaging
from openpilot.starpilot.flm.calibration_lifecycle import ProducerCalibrationLifecycle, ConsumerCalibrationLifecycle
from openpilot.starpilot.flm.learning_wire import write_source_receipt, read_source_receipt
from openpilot.starpilot.flm.runtime_adapter_registry import SourceState, read_source
from openpilot.starpilot.flm.tests.test_fixed_manual_baseline import fixed_controls, BASE


def calibration(*, basis=(0., 0., 0.), blocks=50, status='calibrated', spread=(0., 0., 0.)):
  return SimpleNamespace(rpyCalib=basis, validBlocks=blocks, calStatus=status, rpyCalibSpread=spread)


def admitted(consumer, message, generation):
  consumer.observe(message, current=True)
  return consumer.admit(generation, BASE)


def producer():
  values = iter(('1' * 32, '2' * 32, '3' * 32))
  return ProducerCalibrationLifecycle(lambda: next(values))


def test_live_estimate_drift_does_not_change_frame_generation():
  owner = producer()
  first = owner.observe(calibration(blocks=5), current=True)[0]
  for tick in range(1600):
    result = owner.observe(calibration(basis=(0., tick * 1e-6, -tick * 1e-6),
                                      blocks=min(50, 5 + tick // 100)), current=True)
    assert result[0] == first
    assert result[1][1] == tick * 1e-6  # Actual live estimate still carried.


@pytest.mark.parametrize('message,current', [
  (calibration(status='recalibrating', blocks=1), True),
  (calibration(status='invalid'), True), (calibration(blocks=4), True),
  (calibration(basis=(float('nan'), 0., 0.)), True),
  (calibration(spread=(0., math.radians(4) + 1e-9, 0.)), True),
  (calibration(spread=(0., 0., math.radians(2) + 1e-9)), True),
])
def test_lifecycle_interruption_rotates_and_consumer_cannot_resume_old_generation(message, current):
  owner = producer()
  first = owner.observe(calibration(), current=True)[0]
  consumer = ConsumerCalibrationLifecycle()
  assert admitted(consumer, calibration(), first)
  assert owner.observe(message, current=current) is None
  if current:
    assert not admitted(consumer, message, first)
  else:
    consumer.reject()  # Core original-source stale guard performs this.
  second = owner.observe(calibration(), current=True)[0]
  assert second != first
  assert not admitted(consumer, calibration(), first)
  assert admitted(consumer, calibration(), second)


def test_monotonic_blocks_regression_is_material_reset_even_when_status_claims_calibrated():
  owner = producer()
  first = owner.observe(calibration(), current=True)[0]
  consumer = ConsumerCalibrationLifecycle()
  assert admitted(consumer, calibration(), first)
  next_frame = owner.observe(calibration(blocks=5), current=True)[0]
  assert next_frame != first
  assert not admitted(consumer, calibration(blocks=5), first)
  assert admitted(consumer, calibration(blocks=5), next_frame)


def test_exact_calibrationd_spread_boundary_and_malformed_vectors():
  owner = producer()
  assert owner.observe(calibration(spread=(0., math.radians(4), math.radians(2))), current=True)
  assert owner.observe(calibration(spread=(0., -1., 0.)), current=True) is None
  assert owner.observe(calibration(blocks=51), current=True) is None


def test_v1_exact_basis_and_v2_generation_identity_and_real_wire_roundtrip():
  with fixed_controls() as (controls, _):
    args = {'now_ns': BASE, 'vehicle_session': 'lifecycle-test'}
    first = read_source(controls.CP, controls.params, calibration=(0., 0., 0.), **args)
    drift = read_source(controls.CP, controls.params, calibration=(0., .001, 0.), **args)
    assert first.enabled and first.source_epoch != drift.source_epoch
    v2 = read_source(controls.CP, controls.params, calibration=(0., 0., 0.),
                     calibration_generation='a' * 32, **args)
    updated = read_source(controls.CP, controls.params, calibration=(0., .001, 0.),
                          calibration_generation='a' * 32, **args)
    new_drive = read_source(controls.CP, controls.params, calibration=(0., .001, 0.),
                            calibration_generation='b' * 32, **args)
    assert v2.enabled and v2.source_epoch == updated.source_epoch
    assert v2.calibration != updated.calibration
    assert new_drive.source_epoch != v2.source_epoch
    for source, version in ((first, 1), (v2, 2)):
      event = messaging.new_message('autoSteeringLearning', valid=True)
      write_source_receipt(event, source)
      assert event.autoSteeringLearning.learning.sourceReceipt.version == version
      # Native serialized reader and original generic builder paths.
      with _reader(event) as reader:
        for value in (event, reader):
          assert read_source_receipt(value, now_ns=BASE, cp_sha256=source.cp_sha256) == source
    # Old lifecycle never attaches via current_sources(identity, old_epoch).
    assert v2.source_epoch != new_drive.source_epoch


def _reader(event):
  from contextlib import nullcontext
  return nullcontext(messaging.log_from_bytes(event.to_bytes()))


@pytest.mark.parametrize('generation', ['', 'x' * 32, 'A' * 32, 'a' * 31, 'a' * 33])
def test_malformed_v2_generation_never_admits(generation):
  source = SourceState(True, False, '0', 'c' * 64, BASE, BASE + 500_000_000,
                       'd' * 64, (0., 0., 0.), 'a' * 32)
  event = messaging.new_message('autoSteeringLearning', valid=True)
  write_source_receipt(event, source)
  event.autoSteeringLearning.learning.sourceReceipt.calibrationGeneration = generation
  for value in (event, messaging.log_from_bytes(event.to_bytes())):
    assert read_source_receipt(value, now_ns=BASE, cp_sha256=source.cp_sha256) is None


def test_retired_generation_cannot_return_after_later_generation_also_resets():
  consumer = ConsumerCalibrationLifecycle()
  assert admitted(consumer, calibration(), 'a' * 32)
  consumer.reject()
  assert admitted(consumer, calibration(), 'b' * 32)
  consumer.reject()
  assert not admitted(consumer, calibration(), 'a' * 32)
  assert not admitted(consumer, calibration(), 'b' * 32)
  assert admitted(consumer, calibration(), 'c' * 32)


@pytest.mark.parametrize('token', ['', 'a', 'A' * 32, 'x' * 32, None])
def test_producer_cannot_publish_malformed_token(token):
  owner = ProducerCalibrationLifecycle(lambda: token)
  assert owner.observe(calibration(), current=True) is None


def test_producer_cannot_recycle_retired_token():
  owner = ProducerCalibrationLifecycle(lambda: 'a' * 32)
  assert owner.observe(calibration(), current=True)
  owner.observe(calibration(status='invalid'), current=True)
  assert owner.observe(calibration(), current=True) is None


@pytest.mark.parametrize('blocks', [True, 5.1, '5', None])
def test_malformed_block_count_cannot_be_silently_coerced(blocks):
  assert producer().observe(calibration(blocks=blocks), current=True) is None


def test_token_factory_error_is_fail_closed():
  def failed():
    raise RuntimeError('unavailable token')
  assert ProducerCalibrationLifecycle(failed).observe(calibration(), current=True) is None


@pytest.mark.parametrize('generation', [None, 0, False, [], 'invalid'])
def test_read_source_does_not_fall_back_to_v1_for_malformed_generation(generation):
  with fixed_controls() as (controls, _):
    source = read_source(controls.CP, controls.params, now_ns=BASE, calibration=(0., 0., 0.),
                         vehicle_session='lifecycle-test', calibration_generation=generation)
    assert not source.enabled and not source.source_epoch


def test_first_invalid_calibration_captures_fresh_unadmitted_generation():
  consumer = ConsumerCalibrationLifecycle()
  assert consumer.capture('a' * 32)
  consumer.observe(calibration(status='invalid'), current=True, now_ns=BASE)
  consumer.observe(calibration(), current=True, now_ns=BASE + 10_000_000)
  assert not consumer.admit('a' * 32, BASE + 1)
  assert consumer.admit('b' * 32, BASE + 1)


def test_first_invalid_without_receipt_rejects_old_receipt_observation():
  consumer = ConsumerCalibrationLifecycle()
  consumer.observe(calibration(), current=False, now_ns=BASE)
  consumer.observe(calibration(), current=True, now_ns=BASE + 10_000_000)
  assert not consumer.admit('a' * 32, BASE)
  assert not consumer.admit('a' * 32, BASE - 1)
  assert consumer.admit('b' * 32, BASE + 1)


def test_generation_switch_alone_retires_previous_token():
  consumer = ConsumerCalibrationLifecycle()
  consumer.observe(calibration(), current=True)
  assert consumer.admit('a' * 32, BASE)
  assert consumer.admit('b' * 32, BASE + 1)
  assert not consumer.admit('a' * 32, BASE + 2)


def test_actual_core_first_invalid_receipt_then_same_generation_cannot_resume():
  from openpilot.starpilot.flm.tests.test_live_calibration_paths import live_frame

  with fixed_controls() as (controls, _):
    runtime = controls.global_learning
    live_frame(controls, 0, 'a' * 32, status='recalibrating', blocks=1, healthy=False)
    assert 'a' * 32 in runtime._calibration_lifecycle.rejected
    for tick in range(1, 31):
      live_frame(controls, tick, 'a' * 32)
      assert not runtime.eligible
    assert not runtime._calibration_lifecycle.admit('a' * 32, 1_000_000_000_000)
    for tick in range(31, 91):
      live_frame(controls, tick, 'b' * 32)
    assert runtime._calibration_lifecycle.generation == 'b' * 32
    assert 'b' * 32 not in runtime._calibration_lifecycle.rejected
    assert runtime.eligible


def test_actual_main_source_reader_reuses_lifecycle_for_cache_and_persistence():
  from unittest import mock
  from openpilot.starpilot.flm import steerlearnerd

  with fixed_controls() as (controls, _):
    cp = controls.CP.as_builder()
    cp.carVin = 'KMHM54AA6PA000001'
    controls.params.put('CarParams', cp.to_bytes(), block=True)
    cal = calibration()
    sm = type('Sources', (), {
      '__getitem__': lambda self, key: cal,
      'all_checks': lambda self, keys: True,
    })()
    sm.seen = {'extrinsicsCalibration': True}
    sm.valid = {'extrinsicsCalibration': True}
    sm.logMonoTime = {'extrinsicsCalibration': BASE}
    sm.recv_time = {'extrinsicsCalibration': BASE / 1e9}
    with mock.patch.object(steerlearnerd, 'Params', return_value=controls.params), \
         mock.patch.object(steerlearnerd, 'source_receiver', return_value=sm), \
         mock.patch.object(steerlearnerd.messaging, 'sub_sock'), \
         mock.patch.object(steerlearnerd.messaging, 'PubMaster'), \
         mock.patch.object(steerlearnerd, 'run') as run, \
         mock.patch.object(steerlearnerd.os, 'sched_setscheduler', create=True), \
         mock.patch.object(steerlearnerd.os, 'nice'):
      steerlearnerd.main()
    source_reader, persistence = run.call_args.args[7], run.call_args.args[11]
    first = source_reader(BASE)
    assert first.enabled and first.calibration_generation
    cal.rpyCalib = (0., .001, -.001)
    sm.logMonoTime['extrinsicsCalibration'] = BASE + 250_000_000
    sm.recv_time['extrinsicsCalibration'] = (BASE + 250_000_000) / 1e9
    refined = source_reader(BASE + 250_000_000)
    assert refined.source_epoch == first.source_epoch
    assert refined.calibration != first.calibration
    with mock.patch.object(steerlearnerd.time, 'monotonic_ns', return_value=BASE + 250_000_000):
      assert persistence.current_sources('current-identity', first.source_epoch) == ('current-identity', True, False, first.reset)
    cal.calStatus, cal.validBlocks = 'recalibrating', 1
    assert not source_reader(BASE + 260_000_000).enabled
    cal.calStatus, cal.validBlocks = 'calibrated', 50
    fresh = source_reader(BASE + 270_000_000)
    assert fresh.enabled and fresh.calibration_generation != first.calibration_generation
    with mock.patch.object(steerlearnerd.time, 'monotonic_ns', return_value=BASE + 270_000_000):
      assert persistence.current_sources('old-identity', first.source_epoch)[0] == ''
      assert persistence.current_sources('fresh-identity', fresh.source_epoch)[0] == 'fresh-identity'
    for tick in range(100):
      gap = BASE + (tick + 1) * 1_000_000_000
      retained = source_reader(gap)
      assert retained.enabled and retained.source_epoch == fresh.source_epoch
      sm.logMonoTime['extrinsicsCalibration'] = gap + 1
      sm.recv_time['extrinsicsCalibration'] = (gap + 1) / 1e9
      resumed = source_reader(gap + 1)
      assert resumed.enabled and resumed.source_epoch == fresh.source_epoch
    sm.valid['extrinsicsCalibration'] = False
    assert not source_reader(gap + 2).enabled
    sm.valid['extrinsicsCalibration'] = True
    reset = source_reader(gap + 3)
    assert reset.enabled and reset.source_epoch != fresh.source_epoch



def test_oversized_calibration_vectors_are_rejected_before_iteration():
  class Oversized:
    def __len__(self):
      return 1_000_000

    def __iter__(self):
      raise AssertionError('unbounded calibration work entered the control loop')

  owner = producer()
  assert owner.observe(calibration(basis=Oversized()), current=True) is None
  assert owner.observe(calibration(spread=Oversized()), current=True) is None


def test_delivery_gaps_pause_without_exhausting_calibration_identity():
  owner = producer()
  consumer = ConsumerCalibrationLifecycle()
  token = owner.observe(calibration(), current=True)[0]
  assert admitted(consumer, calibration(), token)
  for tick in range(100):
    now = BASE + (tick + 1) * 1_000_000_000
    assert owner.observe(calibration(), current=False) is None
    assert not consumer.observe(calibration(), current=False, now_ns=now)
    assert not consumer.admit(token, now + 1)
    assert owner.observe(calibration(), current=True)[0] == token
    assert consumer.observe(calibration(), current=True, now_ns=now + 1)
    assert not consumer.admit(token, now)
    assert consumer.admit(token, now + 1)
  assert len(owner.issued) == 1 and not consumer.rejected and not consumer.exhausted
  owner.observe(calibration(status='recalibrating'), current=False)
  consumer.observe(calibration(status='recalibrating'), current=False, now_ns=now + 2)
  assert owner.observe(calibration(), current=True)[0] != token
  assert not admitted(consumer, calibration(), token)


def test_noncurrent_block_regression_retires_without_admitting():
  owner = producer()
  consumer = ConsumerCalibrationLifecycle()
  token = owner.observe(calibration(), current=True)[0]
  assert admitted(consumer, calibration(), token)
  assert owner.observe(calibration(blocks=5), current=False) is None
  assert not consumer.observe(calibration(blocks=5), current=False, now_ns=BASE+1)
  assert not consumer.admit(token, BASE+2)
  assert owner.observe(calibration(blocks=5), current=True)[0] != token
  assert not admitted(consumer, calibration(blocks=5), token)
