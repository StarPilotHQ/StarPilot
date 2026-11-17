import hashlib
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from typing import TypedDict

import numpy as np

from openpilot.cereal import messaging
from openpilot.starpilot.speed_limits.acceptance import ObservationKind
from openpilot.starpilot.speed_limits.vision import model
from openpilot.starpilot.speed_limits.vision import producer
from openpilot.starpilot.speed_limits.vision.model import Detection, VisionModelCore
from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID, vision_observation
from openpilot.starpilot.speed_limits.runtime import Runtime
from openpilot.starpilot.speed_limits.runtime import Action
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.speed_limits.selection import Source, select_limit
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change


class ObservationContext(TypedDict):
  receipt_mono_ns: int
  now_mono_ns: int
  now_boot_ns: int
  car_fingerprint: str
  drive_session: str


class TestVision(unittest.TestCase):
  def test_bundled_models_have_frozen_identity_and_real_synthetic_inference(self):
    if model.cv2 is None:
      self.skipTest('OpenCV DNN is not available on this host')
    self.assertTrue(model.cv2.__version__.startswith(('4.11.', '4.13.')))
    for name, digest in (('speed_limit_us_detector.onnx', VisionModelCore.DETECTOR_SHA256),
                         ('speed_limit_us_value_classifier.onnx', VisionModelCore.CLASSIFIER_SHA256)):
      self.assertEqual(hashlib.sha256((VisionModelCore.ASSET_DIR / name).read_bytes()).hexdigest(), digest)
    core = VisionModelCore(is_metric=False)
    black = np.zeros((512, 1024, 3), dtype=np.uint8)
    self.assertIsNone(core.infer(black))
    self.assertEqual(core.last_detector_forward_count, 1)

  def test_confirmation_reuses_frozen_thresholds_and_retains_confirmed_limit(self):
    core = VisionModelCore.__new__(VisionModelCore)
    core.is_metric = False
    core.history = model.deque()
    core.published_speed_limit_mph = 0
    core.published_confidence = 0.0
    core.previous_published_speed_limit_mph = 0
    core.last_publish_change_at = 0.0
    core.last_published_support_at = 0.0
    core.episode = 0
    core.published_support_count = 0
    reads = iter((Detection(55, 0.60), Detection(55, 0.76), None))
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    with patch.object(core, 'infer', side_effect=lambda _frame: next(reads)):
      self.assertIsNone(core.observe(frame, now=1.0))
      self.assertEqual(core.observe(frame, now=1.2), (55, 0.76, 2, 1))
      self.assertEqual(core.observe(frame, now=3.21), (55, 0.76, 2, 1))
      self.assertEqual(core.last_published_support_at, 1.2)

  def test_confirmed_sign_survives_leaving_view_and_changes_only_on_confirmation(self):
    core = VisionModelCore.__new__(VisionModelCore)
    core.history = model.deque()
    core.reset()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    reads = [Detection(55, 0.60), Detection(55, 0.76), None, Detection(65, 0.99), None]
    with patch.object(core, 'infer', side_effect=reads):
      self.assertIsNone(core.observe(frame, now=1.0))
      observed = core.observe(frame, now=1.2)
      assert observed is not None
      self.assertEqual(observed[0], 55)
      observed = core.observe(frame, now=60.0)
      assert observed is not None
      self.assertEqual(observed[0], 55)
      self.assertEqual(core.last_published_support_at, 1.2)
      observed = core.observe(frame, now=61.0)
      assert observed is not None
      self.assertEqual(observed[0], 65)
      self.assertEqual(core.last_published_support_at, 61.0)
      core.reset()
      self.assertIsNone(core.observe(frame, now=62.0))

  def test_camera_reset_discards_pre_reconnect_consensus_and_episode(self):
    core = VisionModelCore.__new__(VisionModelCore)
    core.is_metric = False
    core.history = model.deque()
    core.latest_detector_proposal = None
    core.published_speed_limit_mph = 0
    core.published_confidence = 0.0
    core.previous_published_speed_limit_mph = 0
    core.last_publish_change_at = 0.0
    core.last_published_support_at = 0.0
    core.episode = 0
    core.published_support_count = 0
    reads = iter((Detection(55, 0.60), Detection(55, 0.60), Detection(55, 0.65), None))
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    with patch.object(core, 'infer', side_effect=lambda _frame: next(reads)):
      self.assertIsNone(core.observe(frame, now=1.0))
      core.reset()  # A camera disconnect or stream transition starts a new producer session.
      self.assertIsNone(core.observe(frame, now=1.1))
      self.assertEqual(core.observe(frame, now=1.2), (55, 0.65, 2, 1))
      core.reset()
      self.assertIsNone(core.observe(frame, now=1.3))
    self.assertEqual(core.episode, 0)
    self.assertEqual(core.published_speed_limit_mph, 0)

  @staticmethod
  def _event():
    msg = messaging.new_message('slcVisionObservation')
    e = msg.slcVisionObservation.init('vision')
    e.producerSessionId = 'camera-a'
    e.status = 'valid'
    e.speedMps = 24.5872
    e.confidence = 0.9
    e.supportCount = 2
    e.observedMonoTime = 1_000_000_000
    e.validUntilMonoTime = 1_250_000_000
    e.cameraFrameEofBootTime = 5_000_000_000
    e.episode = 4
    e.modelId = MODEL_ID
    e.frameId = 22
    e.stream = 'road'
    return msg

  def test_actual_typed_wire_and_separate_camera_clock_freshness(self):
    msg = self._event()
    decoded = messaging.log_from_bytes(msg.to_bytes()).slcVisionObservation.vision
    kw: ObservationContext = {'receipt_mono_ns': 1_020_000_000, 'now_mono_ns': 1_030_000_000,
          'now_boot_ns': 5_050_000_000, 'car_fingerprint': 'IONIQ6', 'drive_session': 'drive-a'}
    valid = vision_observation(decoded, **kw)
    self.assertIs(valid.kind, ObservationKind.VALID)
    self.assertAlmostEqual(valid.candidate.speed_mps, 24.5872, places=4)
    self.assertIs(vision_observation(decoded, receipt_mono_ns=1_020_000_000, now_mono_ns=1_030_000_000,
                                     now_boot_ns=7_000_000_001, car_fingerprint='IONIQ6',
                                     drive_session='drive-a').kind, ObservationKind.STALE)
    self.assertIs(vision_observation(decoded, receipt_mono_ns=1_020_000_000, now_mono_ns=1_300_000_000,
                                     now_boot_ns=5_050_000_000, car_fingerprint='IONIQ6',
                                     drive_session='drive-a').kind, ObservationKind.STALE)
    event = msg.slcVisionObservation.vision
    event.status = 'unavailable'
    self.assertIs(vision_observation(event, **kw).kind, ObservationKind.UNKNOWN)
    event.status = 'valid'
    event.modelId = 'unrecognized'
    self.assertIs(vision_observation(event, **kw).kind, ObservationKind.UNKNOWN)

  def test_runtime_adapter_requires_explicit_development_mode_and_fresh_wire(self):
    wire = self._event().slcVisionObservation

    class SM:
      valid = {'slcVisionObservation': True, 'carState': False}
      alive = {'slcVisionObservation': True, 'carState': False}
      logMonoTime = {'slcVisionObservation': 1_020_000_000, 'carState': 0}

      def __getitem__(self, name):
        return wire

    cp = SimpleNamespace(carFingerprint='IONIQ6')
    settings = parse({'SpeedLimitController': False, 'ShowSpeedLimits': True,
                      'SLCPriority1': 'Vision', 'SLCPriority2': 'Dashboard'})
    sm = SM()
    stock = Runtime(settings, session_id='drive-a')
    self.assertIs(stock._observations(sm, cp, 1_030_000_000, 5_050_000_000)[Source.VISION].kind,
                  ObservationKind.UNKNOWN)
    runtime = Runtime(settings, session_id='drive-a', vision_enabled=True)
    self.assertIs(runtime._observations(sm, cp, 1_030_000_000, 5_050_000_000)[Source.VISION].kind,
                  ObservationKind.UNKNOWN)
    self.assertIs(runtime.vision_diagnostic.kind, ObservationKind.VALID)
    selected = select_limit(runtime._observations(sm, cp, 1_030_000_000, 5_050_000_000), settings.selection)
    self.assertIsNone(selected.selected_source)
    wire.vision.frameId = 21
    wire.vision.episode = 3
    runtime._observations(sm, cp, 1_030_000_000, 5_050_000_000)
    self.assertIs(runtime.vision_diagnostic.kind, ObservationKind.STALE)
    self.assertIs(runtime._observations(sm, cp, 1_030_000_000, 5_260_000_000)[Source.VISION].kind,
                  ObservationKind.UNKNOWN)
    self.assertIs(runtime.vision_diagnostic.kind, ObservationKind.STALE)

  def test_slow_vision_result_uses_original_frame_deadline(self):
    message = self._event()
    event = message.slcVisionObservation.vision
    event.validUntilMonoTime = 3_000_000_000
    decoded = messaging.log_from_bytes(message.to_bytes()).slcVisionObservation.vision
    kw: ObservationContext = {'receipt_mono_ns': 1_500_000_000, 'now_mono_ns': 1_500_000_000,
          'now_boot_ns': 5_500_000_000, 'car_fingerprint': 'IONIQ6', 'drive_session': 'drive-a'}
    self.assertIs(vision_observation(decoded, **kw).kind, ObservationKind.VALID)
    self.assertIs(vision_observation(decoded, receipt_mono_ns=kw['receipt_mono_ns'], now_mono_ns=3_000_000_001,
                                     now_boot_ns=7_000_000_001, car_fingerprint=kw['car_fingerprint'],
                                     drive_session=kw['drive_session']).kind, ObservationKind.STALE)

  def test_runtime_slow_vision_supports_display_and_control(self):
    message = self._event()
    message.slcVisionObservation.vision.validUntilMonoTime = 3_000_000_000
    wire = messaging.log_from_bytes(message.to_bytes()).slcVisionObservation

    class SM:
      valid = {'slcVisionObservation': True, 'carState': False}
      alive = {'slcVisionObservation': True, 'carState': False}
      logMonoTime = {'slcVisionObservation': 1_500_000_000, 'carState': 0}

      def __getitem__(self, _name):
        return wire

    for control in (False, True):
      with self.subTest(control=control):
        settings = parse({'SpeedLimitController': control, 'ShowSpeedLimits': True,
                          'SLCPriority1': 'Vision', 'SLCPriority2': 'Dashboard'})
        runtime = Runtime(settings, session_id='drive-a', vision_enabled=True,
                          vision_control_qualified=control, vision_display_qualified=True)
        observations = runtime._observations(SM(), SimpleNamespace(carFingerprint='IONIQ6'),
                                             1_500_000_000, 5_500_000_000)
        self.assertIs(observations[Source.VISION].kind,
                      ObservationKind.VALID)
        selected = select_limit(observations, settings.selection)
        self.assertEqual(selected.selected_source, Source.VISION)

  def test_retained_sign_needs_current_camera_and_producer_health(self):
    message = self._event()
    event = message.slcVisionObservation.vision
    # The sign was last seen at monotonic1s. This camera heartbeat is at60s.
    event.cameraFrameEofBootTime = 64_000_000_000
    event.validUntilMonoTime = 62_000_000_000
    kw: ObservationContext = {'receipt_mono_ns': 60_100_000_000, 'now_mono_ns': 60_100_000_000,
          'now_boot_ns': 64_100_000_000, 'car_fingerprint': 'IONIQ6', 'drive_session': 'drive-a'}
    self.assertIs(vision_observation(event, **kw).kind, ObservationKind.VALID)
    self.assertEqual(event.observedMonoTime, 1_000_000_000)
    self.assertIs(vision_observation(event, receipt_mono_ns=59_000_000_000, now_mono_ns=kw['now_mono_ns'],
                                     now_boot_ns=kw['now_boot_ns'], car_fingerprint=kw['car_fingerprint'],
                                     drive_session=kw['drive_session']).kind, ObservationKind.STALE)
    event.cameraFrameEofBootTime = 61_000_000_000
    self.assertIs(vision_observation(event, **kw).kind, ObservationKind.STALE)
    event.status = 'unavailable'
    self.assertIs(vision_observation(event, **kw).kind, ObservationKind.UNKNOWN)

  def test_native_shared_source_choice_is_dev_and_parked_guarded(self):
    with tempfile.TemporaryDirectory() as root:
      params = Params(root)
      active = True
      parked = True
      owner = FeatureSettingsOwner(params, lambda group: parked,
                                   vehicle_fingerprint=lambda: 'IONIQ6',
                                   vision_development=lambda: active)

      def row(key):
        state = owner.snapshot('slc', parked=parked, system_long=True, lateral_context=False, metric=False)
        return next(item for item in state.rows if item.key == key)

      primary = row('SLCPriority1')
      self.assertEqual(primary.value, 'Vision')
      self.assertEqual(primary.default_value, 'Vision')
      params.put('SLCPriority1', 'Dashboard', block=True)
      primary = row('SLCPriority1')
      self.assertEqual(primary.value, 'Dashboard')
      self.assertIn('Vision', primary.choices)
      request = row_change(primary)
      assert request is not None
      self.assertEqual(request.value, 'Vision')
      active = False
      self.assertFalse(owner.apply(request))
      active = True
      fresh = row_change(row('SLCPriority1'))
      assert fresh is not None
      self.assertTrue(owner.apply(fresh))
      self.assertEqual(params.get('SLCPriority1'), 'Vision')
      secondary = row('SLCPriority2')
      self.assertEqual(secondary.value, 'Maps')
      self.assertIsNone(secondary.default_value)
      params.put('SLCPriority2', 'Map Data', block=True)
      secondary = row('SLCPriority2')
      self.assertEqual(secondary.value, 'Maps')
      request = row_change(secondary, -1)
      assert request is not None
      self.assertEqual(request.value, 'Vision')
      parked = False
      self.assertFalse(owner.apply(request))
      parked = True
      oversized = b'V' * 129
      Path(params.get_param_path('SLCPriority2')).write_bytes(oversized)
      self.assertFalse(row('SLCPriority2').available)
      self.assertIn('unreadable', row('SLCPriority2').reason)
      self.assertEqual(Path(params.get_param_path('SLCPriority2')).read_bytes(), oversized)

  def test_real_ipc_vision_event_reaches_runtime_after_model_poll(self):
    with OpenpilotPrefix():
      messaging.reset_context()
      pm = messaging.PubMaster(['slcVisionObservation', 'modelV2'])
      sm = messaging.SubMaster(['slcVisionObservation', 'modelV2'], poll='modelV2')
      pair = model.time.monotonic_ns(), model.time.clock_gettime_ns(getattr(model.time, 'CLOCK_BOOTTIME', model.time.CLOCK_MONOTONIC))
      source = self._event()
      source.valid = True
      source.logMonoTime = pair[0] - 10_000_000
      event = source.slcVisionObservation.vision
      event.observedMonoTime = pair[0] - 15_000_000
      event.validUntilMonoTime = pair[0] + 200_000_000
      event.cameraFrameEofBootTime = pair[1] - 40_000_000
      pm.send('slcVisionObservation', source)
      model_event = messaging.new_message('modelV2')
      model_event.valid = True
      pm.send('modelV2', model_event)
      sm.update(100)
      self.assertTrue(sm.updated['slcVisionObservation'])
      runtime = Runtime(parse({'ShowSpeedLimits': True, 'SLCPriority1': 'Vision',
                               'SLCPriority2': 'Dashboard'}), session_id='ipc-drive', vision_enabled=True)
      cp = SimpleNamespace(carFingerprint='IONIQ6')
      now = time.monotonic_ns()
      boot_now = time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))
      self.assertIs(runtime._observations(sm, cp, now, boot_now)[Source.VISION].kind, ObservationKind.UNKNOWN)
      self.assertIs(runtime.vision_diagnostic.kind, ObservationKind.VALID)

  def test_producer_envelope_statuses_survive_real_ipc_and_runtime(self):
    expected = {'unknown': ObservationKind.UNKNOWN, 'valid': ObservationKind.VALID,
                'stale': ObservationKind.STALE, 'unavailable': ObservationKind.UNKNOWN}
    with OpenpilotPrefix():
      messaging.reset_context()
      pm = messaging.PubMaster(['slcVisionObservation'])
      sm = messaging.SubMaster(['slcVisionObservation'])
      runtime = Runtime(parse({'ShowSpeedLimits': True, 'SLCPriority1': 'Vision',
                               'SLCPriority2': 'Dashboard'}), session_id='producer-ipc', vision_enabled=True)
      cp = SimpleNamespace(carFingerprint='IONIQ6')
      for frame_id, (status, kind) in enumerate(expected.items(), 1):
        with self.subTest(status=status):
          now = time.monotonic_ns()
          boot = time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))
          producer._send(pm, session='real-producer', status=status, stream='road',
                         observed_ns=now - 10_000_000, eof_boot_ns=boot - 40_000_000,
                         frame_id=frame_id, result=(55, 0.9, 2, 1) if status == 'valid' else None)
          sm.update(100)
          self.assertTrue(sm.updated['slcVisionObservation'])
          self.assertTrue(sm.valid['slcVisionObservation'])
          received = sm['slcVisionObservation'].vision
          self.assertEqual(str(received.status), status)
          now = time.monotonic_ns()
          boot = time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))
          self.assertIs(runtime._observations(sm, cp, now, boot)[Source.VISION].kind, ObservationKind.UNKNOWN)
          self.assertIs(runtime.vision_diagnostic.kind, kind)

  def test_producer_copies_frame_and_emits_nested_typed_status(self):
    if model.cv2 is None:
      self.skipTest('OpenCV is not available on this host')

    released = []
    y = np.arange(16, dtype=np.uint8).reshape((4, 4)) + 16
    uv = np.tile(np.array([128, 128, 90, 200], dtype=np.uint8), (2, 1))
    packed = np.concatenate((y, uv), axis=0)
    padded = np.full(64, 255, dtype=np.uint8)
    for index in range(4):
      padded[index * 8:index * 8 + 4] = y[index]
    for index in range(2):
      padded[48 + index * 8:48 + index * 8 + 4] = uv[index]

    class Buffer:
      data = padded.tobytes()

      def __del__(self):
        released.append(True)

    class Client:
      width = 4
      height = 4
      stride = 8
      uv_offset = 48
      timestamp_eof = 5_000_000_000
      frame_id = 7

      @staticmethod
      def recv(_timeout):
        return Buffer()

    captured = producer._camera_frame(Client())
    assert captured is not None
    frame, stamp, frame_id = captured
    self.assertEqual(released, [True])
    self.assertEqual(frame.shape, (4, 4, 3))
    np.testing.assert_array_equal(frame, model.cv2.cvtColor(packed, model.cv2.COLOR_YUV2BGR_NV12))
    self.assertEqual((stamp, frame_id), (5_000_000_000, 7))

    class PM:
      sent = None

      def send(self, service, message):
        self.sent = (service, messaging.log_from_bytes(message.to_bytes()))

    pm = PM()
    producer._send(pm, session='s', status='valid', stream='road', observed_ns=1_000_000_000,
                   eof_boot_ns=stamp, frame_id=frame_id, result=(55, 0.9, 2, 1))
    assert pm.sent is not None
    service, envelope = pm.sent
    self.assertEqual(service, 'slcVisionObservation')
    self.assertEqual(envelope.slcVisionObservation.vision.status, 'valid')
    self.assertAlmostEqual(envelope.slcVisionObservation.vision.speedMps, 55 * 0.44704, places=4)
    self.assertEqual(envelope.slcVisionObservation.vision.modelId, MODEL_ID)

  def test_injected_qualified_vision_reuses_selection_confirmation_and_expiry(self):
    data, _ = messages()
    data['carState'].vCruise = 100.0
    data['carState'].vCruiseCluster = 100.0
    data['carState'].vEgoCluster = data['carState'].vEgo
    data['carControl'].longActive = True
    dashboard = messaging.new_message('slcDashboardObservation').slcDashboardObservation
    dashboard.status = 'unknown'
    data['slcDashboardObservation'] = dashboard
    vision = self._event().slcVisionObservation
    evidence = vision.vision
    evidence.observedMonoTime = 1_980_000_000
    evidence.validUntilMonoTime = 2_200_000_000
    evidence.cameraFrameEofBootTime = 5_950_000_000
    data['slcVisionObservation'] = vision

    class SM:
      def __init__(self, values):
        self.data = values
        self.valid = dict.fromkeys(values, True)
        self.alive = dict.fromkeys(values, True)
        self.logMonoTime = dict.fromkeys(values, 2_000_000_000)

      def __getitem__(self, name):
        return self.data[name]

      def advance(self, stamp):
        self.logMonoTime = dict.fromkeys(self.data, stamp)
        self.data['slcDashboardObservation'].carStateLogMonoTime = stamp

    cp = SimpleNamespace(brand='hyundai', carFingerprint='IONIQ6', openpilotLongitudinalControl=True)
    sm = SM(data)
    confirmed = Runtime(parse({'SpeedLimitController': True, 'SLCConfirmation': 1,
                               'SLCPriority1': 'Vision', 'SLCPriority2': 'Dashboard'}),
                        session_id='trial', vision_enabled=True, vision_control_qualified=True)
    first = confirmed.step(sm, cp, now_ns=2_000_000_000, now_boot_ns=6_000_000_000)
    self.assertEqual(first.message.slcState.source, 'vision')
    self.assertTrue(first.message.slcState.hasPending)
    self.assertFalse(first.message.slcState.hasCeiling)
    sm.advance(2_020_000_000)
    accepted = confirmed.step(sm, cp, now_ns=2_020_000_000, now_boot_ns=6_020_000_000,
                              request=Action('trial', 1, first.message.slcState.decisionId,
                                             first.message.slcState.presentationId, 'accept'))
    self.assertTrue(accepted.message.slcState.hasAccepted)
    self.assertTrue(accepted.message.slcState.hasCeiling)
    self.assertEqual(accepted.message.slcState.sourceProducerSessionId, 'camera-a')
    self.assertAlmostEqual(accepted.result.ceiling.speed_mps, evidence.speedMps)
    sm.advance(2_300_000_000)
    expired = confirmed.step(sm, cp, now_ns=2_300_000_000, now_boot_ns=6_300_000_000)
    self.assertTrue(expired.message.slcState.hasCeiling)
    self.assertEqual(expired.result.acceptance.basis, 'unknown')
    self.assertAlmostEqual(expired.result.acceptance.control_target_mps, evidence.speedMps)
    self.assertEqual(expired.result.acceptance.state.accepted, accepted.result.acceptance.state.accepted)
    self.assertIs(confirmed.vision_diagnostic.kind, ObservationKind.STALE)

    dashboard.producerSessionId = 'card-a'
    dashboard.status = 'valid'
    dashboard.speedMps = 20.0
    dashboard.observedMonoTime = 2_290_000_000
    dashboard.validUntilMonoTime = 2_800_000_000
    dashboard.episode = 1
    dashboard.carStateLogMonoTime = 2_300_000_000
    fallback = Runtime(parse({'SpeedLimitController': True, 'SLCConfirmation': 1, 'SLCPriority1': 'Vision',
                              'SLCPriority2': 'Dashboard'}), session_id='fallback',
                       vision_enabled=True, vision_control_qualified=True)
    chosen = fallback.step(sm, cp, now_ns=2_300_000_000, now_boot_ns=6_300_000_000)
    self.assertEqual(chosen.message.slcState.source, 'dashboard')
    self.assertFalse(chosen.message.slcState.hasCeiling)
    self.assertTrue(chosen.message.slcState.hasPending)
    sm.advance(2_310_000_000)
    chosen_accepted = fallback.step(sm, cp, now_ns=2_310_000_000, now_boot_ns=6_310_000_000,
                                    request=Action('fallback', 1, chosen.message.slcState.decisionId,
                                                   chosen.message.slcState.presentationId, 'accept'))
    self.assertTrue(chosen_accepted.message.slcState.hasAccepted)
    self.assertTrue(chosen_accepted.message.slcState.hasCeiling)
    self.assertAlmostEqual(chosen_accepted.result.ceiling.speed_mps, dashboard.speedMps)
    dashboard.status = 'unknown'
    sm.advance(2_350_000_000)
    missing = fallback.step(sm, cp, now_ns=2_350_000_000, now_boot_ns=6_350_000_000)
    self.assertTrue(missing.message.slcState.hasCeiling)
    self.assertEqual(missing.result.acceptance.basis, 'unknown')
    assert missing.result.acceptance.control_target_mps is not None
    self.assertAlmostEqual(missing.result.acceptance.control_target_mps, dashboard.speedMps)
    self.assertEqual(missing.result.acceptance.state.accepted, chosen_accepted.result.acceptance.state.accepted)
