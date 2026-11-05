"""Exercise the producer loop against the current camera API and typed wire."""

from collections import deque
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from openpilot.cereal import messaging
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.starpilot.speed_limits.vision import producer
from openpilot.starpilot.speed_limits.vision.model import Detection, VisionModelCore
from openpilot.starpilot.speed_limits.vision.observation import vision_observation
from openpilot.starpilot.speed_limits.acceptance import ObservationKind

place_inference = producer._place_inference


class EndProducer(BaseException):
  pass


class TestVisionProducer(unittest.TestCase):
  def setUp(self):
    event = messaging.new_message('drivingModelData', valid=True)
    self.model = event.drivingModelData
    self.model.frameDropPerc = 0
    self.sm = MagicMock()
    self.sm.seen = {'drivingModelData': True}
    self.sm.valid = {'drivingModelData': True}
    self.sm.logMonoTime = {'drivingModelData': 0}
    self.sm.__getitem__ = Mock(return_value=self.model)
    for patcher in (patch.object(messaging, 'SubMaster', return_value=self.sm),
                    patch.object(producer, '_configure_process'), patch.object(producer, '_place_inference')):
      patcher.start()
      self.addCleanup(patcher.stop)

  def test_vision_avoids_vasm_core_when_both_enabled(self):
    with patch.object(producer, 'vasm_enabled', side_effect=[True, False]), \
         patch('openpilot.common.realtime.set_core_affinity') as affinity:
      place_inference(object())
      place_inference(object())
    self.assertEqual([call.args[0] for call in affinity.call_args_list], [[0, 1], [0, 1, 2]])

  def clock_pairs(self, clocks, states=None):
    pairs = iter(clocks)
    model_states = iter(states) if states is not None else None

    def next_pair():
      pair = next(pairs)
      self.sm.logMonoTime['drivingModelData'] = pair[0]
      if model_states is not None:
        seen, valid, age_ns, drop = next(model_states)
        self.sm.seen['drivingModelData'] = seen
        self.sm.valid['drivingModelData'] = valid
        self.sm.logMonoTime['drivingModelData'] = pair[0] - age_ns
        self.model.frameDropPerc = drop
      return pair

    return next_pair

  def run_frame(self, streams, *, completion_age_ns=10_000_000, model_states=None, metric=False, number=55):
    core = Mock(last_published_support_at=1., last_detection_at=0., stop_detections=())
    core.observe.return_value = (number, 0.9, 2, 1)
    client = Mock()
    client.is_connected.return_value = True
    camera = Mock(return_value=client)
    camera.available_streams.side_effect = [streams, EndProducer()]
    messages = []

    def send(service, event):
      self.assertEqual(service, 'slcVisionObservation')
      messages.append(messaging.log_from_bytes(event.to_bytes()).slcVisionObservation.vision)

    clocks = [(1_000_000_000, 5_000_000_000), (1_000_000_000, 5_000_000_000),
              (1_000_000_000 + completion_age_ns, 5_000_000_000 + completion_age_ns)]
    with patch('msgq.visionipc.VisionIpcClient', camera), \
         patch('openpilot.common.params.Params', return_value=SimpleNamespace(get_bool=lambda _: metric)), \
         patch.object(messaging, 'PubMaster', return_value=SimpleNamespace(send=send)), \
         patch.object(producer, 'VisionModelCore', return_value=core), \
         patch.object(producer, '_camera_buffer', return_value=(object(), 5_000_000_000, 12)), \
         patch.object(producer, '_convert_camera_buffer', return_value=(np.zeros((2, 2, 3)), 5_000_000_000, 12)) as convert, \
         patch.object(producer, 'clock_pair_ns', side_effect=self.clock_pairs(clocks, model_states)), \
         patch.object(producer.time, 'sleep'), \
         self.assertRaises(EndProducer):
      producer.main()
    if model_states is not None and len(model_states) == 1:
      convert.assert_not_called()
    return camera, core, messages

  def test_recognized_sign_uses_metric_setting_at_the_source(self):
    for metric in (False, True):
      for number in (30, 50, 60, 80, 100):
        with self.subTest(metric=metric, number=number):
          _, _, messages = self.run_frame({VisionStreamType.VISION_STREAM_NARROW_ROAD}, metric=metric, number=number)
          self.assertAlmostEqual(messages[0].speedMps, number * (CV.KPH_TO_MS if metric else CV.MPH_TO_MS), places=4)

  def test_actual_narrow_camera_api_and_compatible_road_wire_tag(self):
    for streams in ({VisionStreamType.VISION_STREAM_NARROW_ROAD},
                    {VisionStreamType.VISION_STREAM_NARROW_ROAD, VisionStreamType.VISION_STREAM_WIDE_ROAD}):
      with self.subTest(streams=streams):
        camera, core, messages = self.run_frame(streams)
        camera.assert_called_once_with('camerad', VisionStreamType.VISION_STREAM_NARROW_ROAD, True)
        core.observe.assert_called_once()
        self.assertEqual(len(messages), 1)
        self.assertEqual(str(messages[0].status), 'valid')
        self.assertEqual(str(messages[0].stream), 'road')
        self.assertEqual(messages[0].frameId, 12)
        self.assertEqual(messages[0].cameraFrameEofBootTime, 5_000_000_000)

  def test_actual_wide_camera_fallback_and_no_camera_unavailability(self):
    camera, _, messages = self.run_frame({VisionStreamType.VISION_STREAM_WIDE_ROAD})
    camera.assert_called_once_with('camerad', VisionStreamType.VISION_STREAM_WIDE_ROAD, True)
    self.assertEqual(str(messages[0].status), 'valid')
    self.assertEqual(str(messages[0].stream), 'wideRoad')
    camera, core, messages = self.run_frame({VisionStreamType.VISION_STREAM_CABIN})
    camera.assert_not_called()
    core.observe.assert_not_called()
    self.assertEqual(str(messages[0].status), 'unavailable')
    self.assertEqual(str(messages[0].stream), 'unknown')

  def test_inference_beyond_result_budget_remains_stale(self):
    _, core, messages = self.run_frame({VisionStreamType.VISION_STREAM_NARROW_ROAD},
                                     completion_age_ns=2_000_000_001)
    core.observe.assert_called_once()
    self.assertEqual(len(messages), 1)
    self.assertEqual(str(messages[0].status), 'stale')
    self.assertEqual(messages[0].validUntilMonoTime, 0)
    self.assertEqual(messages[0].speedMps, 0)

  def test_bounded_slow_inference_preserves_original_frame_time(self):
    _, core, messages = self.run_frame({VisionStreamType.VISION_STREAM_NARROW_ROAD},
                                      completion_age_ns=500_000_000)
    core.observe.assert_called_once()
    self.assertEqual(str(messages[0].status), 'valid')
    self.assertEqual(messages[0].observedMonoTime, 1_000_000_000)
    self.assertEqual(messages[0].validUntilMonoTime, 3_000_000_000)
    self.assertEqual(messages[0].cameraFrameEofBootTime, 5_000_000_000)
    self.assertEqual(core.observe.call_args.kwargs['now'], 1.)

  def test_unhealthy_driving_model_skips_inference_and_does_not_publish_speed(self):
    road = {VisionStreamType.VISION_STREAM_NARROW_ROAD}
    for state in ((False, True, 0, 0), (True, False, 0, 0),
                  (True, True, 250_000_001, 0), (True, True, -1, 0),
                  (True, True, 0, 1.01), (True, True, 0, float('nan'))):
      with self.subTest(state=state):
        _, core, messages = self.run_frame(road, model_states=[state])
        core.observe.assert_not_called()
        self.assertEqual([str(message.status) for message in messages], ['unavailable'])
        self.assertEqual(messages[0].speedMps, 0)
        self.assertEqual(messages[0].validUntilMonoTime, 0)

  def test_model_loss_during_inference_cannot_renew_result(self):
    healthy = (True, True, 0, 0)
    _, core, messages = self.run_frame({VisionStreamType.VISION_STREAM_NARROW_ROAD},
                                      model_states=[healthy, healthy, (True, True, 0, 1.01)])
    core.observe.assert_called_once()
    self.assertEqual([str(message.status) for message in messages], ['unavailable'])
    self.assertEqual(messages[0].validUntilMonoTime, 0)
    self.assertEqual(messages[0].speedMps, 0)

  def test_borrowed_camera_buffers_do_not_survive_conversion_or_skip(self):
    received, released = [], []
    sources = iter(((12, 5_000_000_000), (13, 5_050_000_000), (14, 5_000_000_000),
                    (15, 5_400_000_000), (16, 5_500_000_000), (17, 5_700_000_000)))

    class Buffer:
      def __init__(self, frame_id):
        self.frame_id = frame_id
        self.data = bytes(6)

      def __del__(self):
        released.append(self.frame_id)

    client = Mock()
    client.is_connected.return_value = True
    client.width = client.height = client.stride = 2
    client.uv_offset = 4

    def receive(timeout):
      self.assertEqual(timeout, 100)
      self.assertEqual(released, received)
      frame_id, stamp = next(sources)
      client.frame_id, client.timestamp_eof = frame_id, stamp
      received.append(frame_id)
      return Buffer(frame_id)

    client.recv.side_effect = receive
    core = Mock(last_published_support_at=1., last_detection_at=0., stop_detections=())

    def observe(_frame, *, now):
      self.assertEqual(released, [12] if core.observe.call_count == 1 else [12, 13, 14, 15, 16, 17])
      self.assertEqual(now, 1. if core.observe.call_count == 1 else 1.7)
      return None

    core.observe.side_effect = observe
    road = {VisionStreamType.VISION_STREAM_NARROW_ROAD}
    camera = Mock(return_value=client)
    camera.available_streams.side_effect = [road] * 6 + [EndProducer()]
    clocks = [(1_000_000_000, 5_000_000_000), (1_000_000_000, 5_000_000_000),
              (1_010_000_000, 5_010_000_000), (1_050_000_000, 5_050_000_000),
              (1_300_000_000, 5_300_000_000), (1_400_000_000, 5_400_000_000),
              (1_500_000_000, 5_500_000_000), (1_700_000_000, 5_700_000_000),
              (1_700_000_000, 5_700_000_000), (1_710_000_000, 5_710_000_000)]
    healthy = (True, True, 0, 0)
    states = [healthy] * 5 + [(True, True, 0, 1.1)] * 2 + [healthy] * 3
    messages = []

    def send(_service, event):
      messages.append(messaging.log_from_bytes(event.to_bytes()).slcVisionObservation.vision)

    opencv = Mock()
    opencv.cvtColor.side_effect = lambda array, _format: array.copy()
    with patch('msgq.visionipc.VisionIpcClient', camera), \
         patch('openpilot.common.params.Params', return_value=SimpleNamespace(get_bool=lambda _: False)), \
         patch.object(messaging, 'PubMaster', return_value=SimpleNamespace(send=send)), \
         patch.object(producer, 'VisionModelCore', return_value=core), \
         patch.object(producer, 'cv2', opencv), \
         patch.object(producer, 'clock_pair_ns', side_effect=self.clock_pairs(clocks, states)), \
         patch.object(producer.time, 'sleep'), \
         self.assertRaises(EndProducer):
      producer.main()
    self.assertEqual(released, [12, 13, 14, 15, 16, 17])
    self.assertEqual(opencv.cvtColor.call_count, 2)
    self.assertEqual(core.observe.call_count, 2)
    self.assertEqual([str(message.status) for message in messages], ['unknown', 'stale', 'unavailable', 'unknown'])
    self.assertEqual(messages[2].validUntilMonoTime, 0)
    self.assertEqual(messages[2].producerSessionId, messages[3].producerSessionId)

  def test_throttled_and_already_stale_frames_do_not_convert(self):
    core = Mock(last_published_support_at=1., last_detection_at=0., stop_detections=())
    core.observe.return_value = None
    client = Mock()
    client.is_connected.return_value = True
    camera = Mock(return_value=client)
    road = {VisionStreamType.VISION_STREAM_NARROW_ROAD}
    camera.available_streams.side_effect = [road, road, road, EndProducer()]
    buffers = [(object(), 5_000_000_000, 12), (object(), 5_050_000_000, 13),
               (object(), 5_000_000_000, 14)]
    clocks = [(1_000_000_000, 5_000_000_000), (1_000_000_000, 5_000_000_000),
              (1_010_000_000, 5_010_000_000), (1_050_000_000, 5_050_000_000),
              (1_300_000_000, 5_300_000_000)]
    messages = []

    def send(service, event):
      self.assertEqual(service, 'slcVisionObservation')
      messages.append(messaging.log_from_bytes(event.to_bytes()).slcVisionObservation.vision)

    with patch('msgq.visionipc.VisionIpcClient', camera), \
         patch('openpilot.common.params.Params', return_value=SimpleNamespace(get_bool=lambda _: False)), \
         patch.object(messaging, 'PubMaster', return_value=SimpleNamespace(send=send)), \
         patch.object(producer, 'VisionModelCore', return_value=core), \
         patch.object(producer, '_camera_buffer', side_effect=buffers), \
         patch.object(producer, '_convert_camera_buffer', return_value=(np.zeros((2, 2, 3)), 5_000_000_000, 12)) as convert, \
         patch.object(producer, 'clock_pair_ns', side_effect=self.clock_pairs(clocks)), \
         patch.object(producer.time, 'sleep'), \
         self.assertRaises(EndProducer):
      producer.main()

    convert.assert_called_once_with(buffers[0])
    core.observe.assert_called_once()
    self.assertEqual([str(message.status) for message in messages], ['unknown', 'stale'])
    self.assertEqual([message.frameId for message in messages], [12, 14])

  def test_expired_inference_cools_down_without_accepting_old_result(self):
    core = Mock(last_published_support_at=1., last_detection_at=0., stop_detections=())
    core.observe.return_value = None
    client = Mock()
    client.is_connected.return_value = True
    camera = Mock(return_value=client)
    road = {VisionStreamType.VISION_STREAM_NARROW_ROAD}
    camera.available_streams.side_effect = [road, road, road, EndProducer()]
    buffers = [(object(), 5_000_000_000, 12), (object(), 7_100_000_000, 13),
               (object(), 8_600_000_000, 14)]
    frames = [(np.zeros((2, 2, 3)), item[1], item[2]) for item in (buffers[0], buffers[2])]
    clocks = [(1_000_000_000, 5_000_000_000), (1_000_000_000, 5_000_000_000),
              (3_010_000_000, 7_010_000_000), (3_100_000_000, 7_100_000_000),
              (4_600_000_000, 8_600_000_000), (4_600_000_000, 8_600_000_000),
              (4_610_000_000, 8_610_000_000)]
    messages = []

    def send(service, event):
      self.assertEqual(service, 'slcVisionObservation')
      messages.append(messaging.log_from_bytes(event.to_bytes()).slcVisionObservation.vision)

    with patch('msgq.visionipc.VisionIpcClient', camera), \
         patch('openpilot.common.params.Params', return_value=SimpleNamespace(get_bool=lambda _: False)), \
         patch.object(messaging, 'PubMaster', return_value=SimpleNamespace(send=send)), \
         patch.object(producer, 'VisionModelCore', return_value=core), \
         patch.object(producer, '_camera_buffer', side_effect=buffers), \
         patch.object(producer, '_convert_camera_buffer', side_effect=frames) as convert, \
         patch.object(producer, 'clock_pair_ns', side_effect=self.clock_pairs(clocks)), \
         patch.object(producer.time, 'sleep'), \
         self.assertRaises(EndProducer):
      producer.main()

    self.assertEqual([call.args[0] for call in convert.call_args_list], [buffers[0], buffers[2]])
    self.assertEqual(core.observe.call_count, 2)
    self.assertEqual([str(message.status) for message in messages], ['stale', 'unknown'])
    self.assertEqual([message.frameId for message in messages], [12, 14])
    self.assertEqual(messages[0].validUntilMonoTime, 0)
    self.assertEqual(messages[0].speedMps, 0)

  def test_followup_confirms_brief_candidate_without_changing_thresholds(self):
    for conversion_ns, sources, expected in (
      (0, (1_000_000_000, 1_100_000_000, 1_300_000_000), ['unknown', 'valid', 'valid']),
      (80_000_000, (1_000_000_000, 1_200_000_000, 1_400_000_000), ['unknown', 'unknown']),
    ):
      with self.subTest(conversion_ns=conversion_ns):
        core = VisionModelCore.__new__(VisionModelCore)
        core.stop_detections = ()
        core.is_metric = False
        core.history = deque()
        core.reset()
        client = Mock()
        client.is_connected.return_value = True
        camera = Mock(return_value=client)
        road = {VisionStreamType.VISION_STREAM_NARROW_ROAD}
        camera.available_streams.side_effect = [road] * 3 + [EndProducer()]
        buffers = [(object(), t + 4_000_000_000, i) for i, t in enumerate(sources)]
        frames = [(np.full((2, 2, 3), i), b[1], b[2]) for i, b in enumerate(buffers)]
        current = [0, 0]
        pending = iter(buffers)
        messages = []

        def receive(_client, pending=pending, current=current):
          buffer = next(pending)
          current[:] = [buffer[2], 0]
          return buffer

        def clock_pair(sources=sources, current=current, conversion_ns=conversion_ns):
          now = sources[current[0]] + (0, conversion_ns, conversion_ns + 10_000_000)[min(current[1], 2)]
          current[1] += 1
          self.sm.logMonoTime['drivingModelData'] = now
          return now, now + 4_000_000_000

        def send(_service, event, messages=messages):
          messages.append(messaging.log_from_bytes(event.to_bytes()).slcVisionObservation.vision)

        with patch('msgq.visionipc.VisionIpcClient', camera), \
             patch('openpilot.common.params.Params', return_value=SimpleNamespace(get_bool=lambda _: False)), \
             patch.object(messaging, 'PubMaster', return_value=SimpleNamespace(send=send)), \
             patch.object(producer, 'VisionModelCore', return_value=core), \
             patch.object(producer, '_camera_buffer', side_effect=receive), \
             patch.object(producer, '_convert_camera_buffer', side_effect=lambda buffer, frames=frames: frames[buffer[2]]), \
             patch.object(producer, 'clock_pair_ns', side_effect=clock_pair), \
             patch.object(producer.time, 'sleep'), \
             patch.object(core, 'infer', side_effect=lambda frame: Detection(55, 0.60) if frame[0, 0, 0] < 2 else None), \
             self.assertRaises(EndProducer):
          producer.main()

        self.assertEqual([str(message.status) for message in messages], expected)
        if conversion_ns:
          self.assertEqual([message.frameId for message in messages], [0, 2])
          self.assertEqual(core.last_detection_at, 1.)
          continue
        self.assertEqual([message.frameId for message in messages], [0, 1, 2])
        self.assertEqual(messages[1].supportCount, 2)
        self.assertEqual(messages[1].observedMonoTime, 1_100_000_000)
        self.assertEqual(messages[2].observedMonoTime, 1_100_000_000)
        self.assertEqual(messages[1].validUntilMonoTime, 3_100_000_000)
        self.assertEqual(messages[2].validUntilMonoTime, 3_300_000_000)
        self.assertEqual(core.last_detection_at, 1.1)
        self.assertEqual(messages[1].confidence, messages[2].confidence)

  def test_followup_preserves_processing_cost_and_expires(self):
    interval = producer._inference_interval_ns
    # Fast candidate reads: 100ms; no candidate or expired window: 166.7ms.
    self.assertEqual(interval(25_000_000, 1_000_000_000, 1_100_000_000), 100_000_000)
    for last, now in ((0, 1_100_000_000), (1_000_000_000, 3_000_000_000),
                      (2_000_000_000, 1_100_000_000)):
      self.assertEqual(interval(25_000_000, last, now), producer.INFERENCE_INTERVAL_NS)
    # A 120ms pass retains 300ms cadence, and expensive/stale passes retain the busy cap.
    for processing in (300_000_000, 1_250_000_000, producer.BUSY_INFERENCE_INTERVAL_NS):
      self.assertEqual(interval(processing, 1_000_000_000, 1_100_000_000), processing)

  def test_only_usable_detector_reads_renew_followup_and_reset_clears_it(self):
    core = VisionModelCore.__new__(VisionModelCore)
    core.stop_detections = ()
    core.history = deque()
    core.is_metric = False
    core.reset()
    frame = np.zeros((2, 2, 3))
    with patch.object(core, 'infer', side_effect=[Detection(55, 0.60), None, Detection(55, float('nan'))]):
      self.assertIsNone(core.observe(frame, now=1.))
      self.assertEqual(core.last_detection_at, 1.)
      self.assertIsNone(core.observe(frame, now=1.1))
      self.assertIsNone(core.observe(frame, now=1.2))
      self.assertEqual(core.last_detection_at, 1.)
    core.reset()
    self.assertEqual(core.last_detection_at, 0.)

  def cooldown_messages(self, *, processing_ns=500_000_000, heartbeat_failure=None, metric=False, numeric=True, stops=()):
    core = VisionModelCore.__new__(VisionModelCore)
    core.stop_detections = stops
    core.is_metric = metric
    core.history = deque()
    core.reset()
    client = Mock()
    client.is_connected.return_value = True
    camera = Mock(return_value=client)
    road = {VisionStreamType.VISION_STREAM_NARROW_ROAD}
    sources = (1_000_000_000, 1_550_000_000, 1_800_000_000, 2_000_000_000, 2_200_000_000, 2_250_000_000)
    if heartbeat_failure == 'camera':
      sources = (1_000_000_000, 1_550_000_000, 1_800_000_000, 2_150_000_000, 2_200_000_000, 2_250_000_000)
    if processing_ns == 1_200_000_000:
      sources = (1_000_000_000, 2_400_000_000, 2_800_000_000, 3_050_000_000, 4_000_000_000)
    streams = [road] * len(sources)
    if heartbeat_failure == 'session':
      streams[2:] = [{VisionStreamType.VISION_STREAM_WIDE_ROAD}] * (len(sources)-2)
    camera.available_streams.side_effect = streams + [EndProducer()]
    buffers = [(object(), t + 4_000_000_000, i) for i, t in enumerate(sources)]
    frames = [(np.full((2, 2, 3), i), b[1], b[2]) for i, b in enumerate(buffers)]
    pending = iter(buffers)
    current = [0, 0, 0]
    messages = []

    def receive(_client):
      buffer = next(pending)
      current[:] = [buffer[2], 0, sources[buffer[2]]]
      return buffer

    def clock_pair():
      index, sample = current[:2]
      now = sources[index]
      if index in (0, len(sources)-1) and sample >= 2:
        now += processing_ns
      if heartbeat_failure == 'camera' and index == 2 and sample == 1:
        now += 260_000_000
      self.sm.valid['drivingModelData'] = not (heartbeat_failure == 'model' and index == 2 and sample == 1)
      self.sm.logMonoTime['drivingModelData'] = now
      current[1] += 1
      current[2] = now
      skew = 10_000_000 if heartbeat_failure == 'clock' and index == 2 and sample == 1 else 0
      return now, now + 4_000_000_000 + skew

    def send(_service, event):
      wire = messaging.log_from_bytes(event.to_bytes()).slcVisionObservation.vision
      messages.append((wire, current[2]))

    with patch('msgq.visionipc.VisionIpcClient', camera), \
         patch('openpilot.common.params.Params', return_value=SimpleNamespace(get_bool=lambda _: metric)), \
         patch.object(messaging, 'PubMaster', return_value=SimpleNamespace(send=send)), \
         patch.object(producer, 'VisionModelCore', return_value=core), \
         patch.object(producer, '_camera_buffer', side_effect=receive), \
         patch.object(producer, '_convert_camera_buffer', side_effect=lambda buffer: frames[buffer[2]]) as convert, \
         patch.object(producer, 'clock_pair_ns', side_effect=clock_pair), \
         patch.object(producer.time, 'sleep'), \
         patch.object(core, 'infer', side_effect=[Detection(55, 0.90) if numeric else None, None]) as infer, \
         self.assertRaises(EndProducer):
      producer.main()
    self.assertEqual(infer.call_count, 2)
    self.assertEqual(convert.call_count, 2)
    return messages

  def test_stop_heartbeat_does_not_require_a_numeric_speed_read(self):
    stop = SimpleNamespace(confidence=.8, bbox=(0, 0, 2, 2))
    messages = self.cooldown_messages(numeric=False, stops=(stop,))
    heartbeats = [(event, stamp) for event, stamp in messages if 1_500_000_000 < stamp < 2_250_000_000]
    self.assertTrue(heartbeats)
    for event, stamp in heartbeats:
      self.assertEqual(str(event.status), 'unknown')
      self.assertEqual(event.speedMps, 0)
      self.assertEqual(event.validUntilMonoTime, 0)
      self.assertEqual(str(event.stopSigns.status), 'valid')
      self.assertEqual(event.stopSigns.observedMonoTime, 1_000_000_000)
      self.assertEqual(event.stopSigns.cameraFrameEofBootTime, 5_000_000_000)
      self.assertEqual(event.stopSigns.frameId, 0)
      self.assertEqual(event.stopSigns.validUntilMonoTime, 2_500_000_000)
      self.assertLess(stamp, event.stopSigns.validUntilMonoTime)
      self.assertEqual(len(event.stopSigns.detections), 1)
    self.assertEqual(len({event.stopSigns.frameId for event, _ in heartbeats}), 1)

  def test_metric_heartbeat_keeps_the_original_conversion(self):
    messages = self.cooldown_messages(metric=True)
    self.assertGreater(len(messages), 2)
    for event, _ in messages:
      self.assertEqual(str(event.status), 'valid')
      self.assertAlmostEqual(event.speedMps, 55 * CV.KPH_TO_MS, places=4)

  def unit_change_messages(self, params, change_at):
    core = VisionModelCore.__new__(VisionModelCore)
    core.stop_detections = ()
    core.is_metric = params.get_bool('IsMetric')
    core.history = deque()
    core.reset()
    initial_metric = core.is_metric
    client = Mock()
    client.is_connected.return_value = True
    camera = Mock(return_value=client)
    road = {VisionStreamType.VISION_STREAM_NARROW_ROAD}
    sources = (1_000_000_000, 1_550_000_000, 1_800_000_000, 2_250_000_000,
               3_500_000_000, 4_750_000_000, 5_550_000_000)
    camera.available_streams.side_effect = [road] * len(sources) + [EndProducer()]
    buffers = iter((object(), t + 4_000_000_000, i) for i, t in enumerate(sources))
    current = [0, 0, 0]
    messages = []

    def change_units():
      params.put_bool('IsMetric', not initial_metric, block=True)

    def receive(_client):
      buffer = next(buffers)
      current[:] = [buffer[2], 0, sources[buffer[2]]]
      return buffer

    def clock_pair():
      index, sample = current[:2]
      now = sources[index] + (500_000_000 if sample >= 2 else 0)
      if change_at == 'heartbeat' and index == 2 and sample == 1:
        change_units()
      self.sm.logMonoTime['drivingModelData'] = now
      current[1] += 1
      current[2] = now
      return now, now + 4_000_000_000

    def infer(_frame):
      if current[0] == 3:
        if change_at == 'inference':
          change_units()
        return None
      return Detection(50, .9 if current[0] == 0 else .6)

    def send(_service, event):
      wire = messaging.log_from_bytes(event.to_bytes()).slcVisionObservation.vision
      messages.append((wire, current[0], params.get_bool('IsMetric')))
      if change_at == 'between_frames' and current[0] == 2:
        change_units()

    with patch('msgq.visionipc.VisionIpcClient', camera), \
         patch('openpilot.common.params.Params', return_value=params), \
         patch.object(messaging, 'PubMaster', return_value=SimpleNamespace(send=send)), \
         patch.object(producer, 'VisionModelCore', return_value=core), \
         patch.object(producer, '_camera_buffer', side_effect=receive), \
         patch.object(producer, '_convert_camera_buffer', side_effect=lambda buffer: (np.zeros((2, 2, 3)), *buffer[1:])), \
         patch.object(producer, 'clock_pair_ns', side_effect=clock_pair), \
         patch.object(producer.time, 'sleep'), \
         patch.object(core, 'infer', side_effect=infer), \
         self.assertRaises(EndProducer):
      producer.main()
    self.assertEqual(core.is_metric, not initial_metric)
    return messages

  def test_live_unit_changes_discard_held_sign_and_require_fresh_consensus(self):
    for initial_metric in (False, True):
      for change_at in ('between_frames', 'inference', 'heartbeat'):
        with self.subTest(initial_metric=initial_metric, change_at=change_at), TemporaryDirectory() as directory:
          params = Params(directory)
          params.put_bool('IsMetric', initial_metric, block=True)
          messages = self.unit_change_messages(params, change_at)
          valid = [(event, frame, metric) for event, frame, metric in messages if str(event.status) == 'valid']
          for event, frame, metric in valid:
            self.assertAlmostEqual(event.speedMps, 50 * (CV.KPH_TO_MS if metric else CV.MPH_TO_MS), places=4,
                                   msg=(initial_metric, change_at, frame, metric))
          after = [(event, frame) for event, frame, metric in valid if metric != initial_metric]
          self.assertEqual([frame for _, frame in after], [5, 6])
          self.assertNotEqual(valid[0][0].producerSessionId, after[0][0].producerSessionId)
          self.assertEqual(after[0][0].supportCount, 2)
          self.assertEqual(after[0][0].observedMonoTime, 4_750_000_000)
          self.assertEqual(after[1][0].observedMonoTime, 4_750_000_000)

  def test_confirmed_camera_heartbeat_preserves_inference_expiry_and_health(self):
    messages = self.cooldown_messages()
    probe = 2_510_000_000
    wire, receipt = next(row for row in reversed(messages) if row[1] <= probe)
    observation = vision_observation(wire, receipt_mono_ns=receipt, now_mono_ns=probe,
                                     now_boot_ns=probe+4_000_000_000, car_fingerprint='IONIQ6', drive_session='drive')
    self.assertIs(observation.kind, ObservationKind.VALID)
    first, _ = messages[0]
    heartbeats = [(event, stamp) for event, stamp in messages if 1_500_000_000 < stamp < 2_250_000_000]
    self.assertTrue(heartbeats)
    for event, _ in heartbeats:
      self.assertEqual(event.observedMonoTime, 1_000_000_000)
      self.assertEqual(event.validUntilMonoTime, 3_000_000_000)
      self.assertEqual((event.speedMps, event.confidence, event.supportCount, event.episode),
                       (first.speedMps, first.confidence, first.supportCount, first.episode))
      self.assertEqual(event.producerSessionId, first.producerSessionId)
      self.assertGreater(event.cameraFrameEofBootTime, first.cameraFrameEofBootTime)
    self.assertTrue(all(b[1]-a[1] >= producer.INFERENCE_INTERVAL_NS for a, b in zip(heartbeats, heartbeats[1:], strict=False)))
    # No background heartbeat can hide an actually stopped or blocked producer.
    final, receipt = messages[-1]
    stopped = receipt + 1_000_000_001
    self.assertIs(vision_observation(final, receipt_mono_ns=receipt, now_mono_ns=stopped,
                                    now_boot_ns=stopped+4_000_000_000,
                                    car_fingerprint='IONIQ6', drive_session='drive').kind, ObservationKind.STALE)
    for reason in ('model', 'clock', 'camera', 'session'):
      with self.subTest(reason=reason):
        invalid = self.cooldown_messages(heartbeat_failure=reason)
        if reason != 'session':
          self.assertTrue(any(event.status in ('stale', 'unavailable') for event, _ in invalid[1:]))
        self.assertNotEqual(invalid[-1][0].producerSessionId, invalid[0][0].producerSessionId)
        self.assertTrue(all(event.status != 'valid' for event, _ in invalid[1:]))
    # A new camera receipt cannot extend the last completed inference's deadline.
    expired = self.cooldown_messages(processing_ns=1_200_000_000)
    self.assertEqual([stamp for _, stamp in expired], [2_200_000_000, 2_400_000_000, 2_800_000_000, 5_200_000_000])
    old, receipt = expired[-2]
    self.assertEqual(old.validUntilMonoTime, 3_000_000_000)
    self.assertIs(vision_observation(old, receipt_mono_ns=receipt, now_mono_ns=3_200_000_000,
                                    now_boot_ns=7_200_000_000, car_fingerprint='IONIQ6',
                                    drive_session='drive').kind, ObservationKind.STALE)

  def test_same_client_reconnect_starts_new_consensus_and_session(self):
    # Use the real temporal state machine without loading ONNX assets. A sign
    # seen once before a reconnect cannot confirm a single post-reconnect read.
    core = VisionModelCore.__new__(VisionModelCore)
    core.stop_detections = ()
    core.history = deque()
    core.reset()
    core.is_metric = False
    client = Mock()
    client.is_connected.side_effect = [True, False, True, True]
    camera = Mock(return_value=client)
    road = {VisionStreamType.VISION_STREAM_NARROW_ROAD}
    camera.available_streams.side_effect = [road, road, road, EndProducer()]
    frames = [(np.zeros((2, 2, 3)), 5_000_000_000, 12),
              (np.zeros((2, 2, 3)), 5_300_000_000, 1),
              (np.zeros((2, 2, 3)), 5_600_000_000, 2)]
    clocks = [(1_000_000_000, 5_000_000_000), (1_000_000_000, 5_000_000_000), (1_010_000_000, 5_010_000_000),
              (1_300_000_000, 5_300_000_000), (1_300_000_000, 5_300_000_000), (1_310_000_000, 5_310_000_000),
              (1_600_000_000, 5_600_000_000), (1_600_000_000, 5_600_000_000), (1_610_000_000, 5_610_000_000)]
    messages = []

    def send(service, event):
      self.assertEqual(service, 'slcVisionObservation')
      messages.append(messaging.log_from_bytes(event.to_bytes()).slcVisionObservation.vision)

    with patch('msgq.visionipc.VisionIpcClient', camera), \
         patch('openpilot.common.params.Params', return_value=SimpleNamespace(get_bool=lambda _: False)), \
         patch.object(messaging, 'PubMaster', return_value=SimpleNamespace(send=send)), \
         patch.object(producer, 'VisionModelCore', return_value=core), \
         patch.object(producer, '_camera_buffer', side_effect=[(object(), item[1], item[2]) for item in frames]), \
         patch.object(producer, '_convert_camera_buffer', side_effect=frames), \
         patch.object(producer, 'clock_pair_ns', side_effect=self.clock_pairs(clocks)), \
         patch.object(producer.time, 'sleep'), \
         patch.object(core, 'infer', side_effect=[Detection(55, 0.60)] * 3), \
         self.assertRaises(EndProducer):
      producer.main()

    camera.assert_called_once_with('camerad', VisionStreamType.VISION_STREAM_NARROW_ROAD, True)
    client.connect.assert_called_once_with(False)
    self.assertEqual([str(message.status) for message in messages], ['unknown', 'unknown', 'valid'])
    self.assertEqual([message.frameId for message in messages], [12, 1, 2])
    self.assertNotEqual(messages[0].producerSessionId, messages[1].producerSessionId)
    self.assertEqual(messages[1].producerSessionId, messages[2].producerSessionId)
    self.assertEqual(messages[2].supportCount, 2)


if __name__ == '__main__':
  unittest.main()
