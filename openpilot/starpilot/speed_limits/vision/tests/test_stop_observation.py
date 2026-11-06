from dataclasses import replace
from types import SimpleNamespace as NS
import unittest

from openpilot.cereal import messaging
from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID
from openpilot.starpilot.speed_limits.vision.producer import _send
from openpilot.starpilot.speed_limits.vision.stop_observation import (
  StopBox, StopConfirmation, StopEvidence, STOP_MAX_AGE_NS, read_stop_evidence,
)

BASE = 100_000_000_000
OFFSET = 2_000_000_000
DRIVE = BASE - 1_000_000_000
BOX = StopBox(.875, (780, 200, 840, 260))


def evidence(t=BASE, *, frame=1, boxes=(BOX,), **changes):
  return replace(StopEvidence('camera', 'road', t, t + OFFSET, frame, 1344, 760, boxes), **changes)


def message(sample, *, now=None):
  now = sample.observed_ns + 400_000_000 if now is None else now
  captured = []
  def send(service, event):
    assert service == 'slcVisionObservation'
    event.logMonoTime = now
    captured.append(messaging.log_from_bytes(event.to_bytes()))
  _send(NS(send=send), session=sample.session, stream=sample.stream, status='unknown', stop_sample=sample)
  return captured[0]


class Input(dict):
  def __init__(self, event, *, now=None):
    super().__init__(slcVisionObservation=event.slcVisionObservation)
    now = int(event.logMonoTime) if now is None else now
    self.seen = {'slcVisionObservation': True}
    self.valid = self.seen.copy()
    self.alive = self.seen.copy()
    self.logMonoTime = {'slcVisionObservation': int(event.logMonoTime)}
    self.recv_time = {'slcVisionObservation': now / 1e9}
    self.recv_frame = {'slcVisionObservation': 2}


class TestStopObservation(unittest.TestCase):
  def read(self, sm, now=BASE + 400_000_000):
    return read_stop_evidence(sm, now_ns=now, now_boot_ns=now + OFFSET, drive_id=DRIVE)

  def test_real_wire_roundtrip_independent_of_speed_and_heartbeat_clock(self):
    sample = evidence()
    event = message(sample)
    self.assertEqual(str(event.slcVisionObservation.vision.status), 'unknown')
    decoded = self.read(Input(event))
    self.assertEqual(decoded, sample)
    heartbeat = message(sample, now=BASE + 800_000_000)
    self.assertEqual(self.read(Input(heartbeat), BASE + 800_000_000), sample)
    self.assertIsNone(self.read(Input(heartbeat), BASE + STOP_MAX_AGE_NS))

  def test_old_payload_has_no_stop_evidence(self):
    event = messaging.new_message('slcVisionObservation', valid=True, logMonoTime=BASE)
    event.slcVisionObservation.init('vision').modelId = MODEL_ID
    self.assertIsNone(self.read(Input(event)))

  def test_rejects_invalid_transport_identity_clocks_and_geometry(self):
    mutations = [lambda sm: sm.valid.update(slcVisionObservation=False),
                 lambda sm: sm.alive.update(slcVisionObservation=False),
                 lambda sm: sm.recv_time.update(slcVisionObservation=(BASE - 2_000_000_000) / 1e9),
                 lambda sm: sm.logMonoTime.update(slcVisionObservation=BASE + 900_000_000)]
    for mutate in mutations:
      sm = Input(message(evidence()))
      mutate(sm)
      self.assertIsNone(self.read(sm))
    for field, value in [('modelId', 'wrong'), ('stream', 'unknown'), ('producerSessionId', '')]:
      event = message(evidence()).as_builder()
      setattr(event.slcVisionObservation.vision, field, value)
      self.assertIsNone(self.read(Input(event)))
    for field, value in [('observedMonoTime', BASE + 900_000_000), ('validUntilMonoTime', BASE + 9_000_000_000),
                         ('cameraFrameEofBootTime', BASE + OFFSET - 10_000_000), ('imageWidth', 0), ('status', 'unavailable')]:
      event = message(evidence()).as_builder()
      setattr(event.slcVisionObservation.vision.stopSigns, field, value)
      self.assertIsNone(self.read(Input(event)))
    for field, value in [('confidence', float('nan')), ('x2Pixels', 1400), ('x1Pixels', 840)]:
      event = message(evidence()).as_builder()
      setattr(event.slcVisionObservation.vision.stopSigns.detections[0], field, value)
      self.assertIsNone(self.read(Input(event)))

  def test_confirmation_requires_distinct_coherent_frames_and_expires(self):
    owner = StopConfirmation()
    first = evidence()
    self.assertEqual(owner.observe(first, now_ns=BASE), 0)
    self.assertEqual(owner.observe(first, now_ns=BASE + 400_000_000), 0)
    second = evidence(BASE + 700_000_000, frame=15)
    self.assertEqual(owner.observe(second, now_ns=BASE + 800_000_000), second.observed_ns)
    self.assertEqual(owner.observe(second, now_ns=second.observed_ns + STOP_MAX_AGE_NS), 0)
    self.assertIsNone(owner.previous)

  def test_negative_unavailable_session_change_and_reordered_frames_clear(self):
    for change in ('negative', 'unavailable', 'session', 'stream', 'reorder', 'altered_duplicate'):
      owner = StopConfirmation()
      owner.observe(evidence(), now_ns=BASE)
      second = evidence(BASE + 700_000_000, frame=15)
      self.assertGreater(owner.observe(second, now_ns=second.observed_ns), 0)
      sample = evidence(BASE + 800_000_000, frame=16)
      if change == 'negative':
        sample = replace(sample, boxes=())
      elif change == 'unavailable':
        sample = None
      elif change == 'session':
        sample = replace(sample, session='new')
      elif change == 'stream':
        sample = replace(sample, stream='wideRoad')
      elif change == 'reorder':
        sample = evidence()
      else:
        sample = replace(second, boxes=())
      self.assertEqual(owner.observe(sample, now_ns=BASE + 900_000_000), 0, change)

  def test_unrelated_and_low_confidence_boxes_do_not_confirm(self):
    for box in (StopBox(.69, BOX.bbox), StopBox(.9, (1150, 450, 1162, 462))):
      owner = StopConfirmation()
      owner.observe(evidence(), now_ns=BASE)
      self.assertEqual(owner.observe(evidence(BASE + 700_000_000, frame=15, boxes=(box,)), now_ns=BASE + 800_000_000), 0)

  def test_confirmed_road_box_carries_forward_distance(self):
    box = StopBox(.875, (1214, 400, 1294, 480))
    for stream, expected in (('road', 27.025), ('wideRoad', None)):
      owner = StopConfirmation()
      owner.observe(evidence(boxes=(box,), stream=stream, width=1928, height=1208), now_ns=BASE)
      self.assertIsNone(owner.distance_m)
      second = evidence(BASE + 700_000_000, frame=15, boxes=(box,), stream=stream, width=1928, height=1208)
      self.assertEqual(owner.observe(second, now_ns=BASE + 800_000_000), second.observed_ns)
      if expected is None:
        self.assertIsNone(owner.distance_m)
      else:
        self.assertAlmostEqual(owner.distance_m, expected, places=2)
      owner.observe(None, now_ns=BASE + 900_000_000)
      self.assertIsNone(owner.distance_m)

  def test_brief_warning_burst_cannot_confirm_but_sustained_sign_can(self):
    owner = StopConfirmation()
    for i in range(3):
      sample = evidence(BASE + i * 200_000_000, frame=1 + i * 4)
      self.assertEqual(owner.observe(sample, now_ns=sample.observed_ns), 0)
    owner.observe(None, now_ns=BASE + 500_000_000)
    self.assertEqual(owner.confirmed_ns, 0)
    for i in range(4):
      sample = evidence(BASE + 1_000_000_000 + i * 200_000_000, frame=21 + i * 4)
      output = owner.observe(sample, now_ns=sample.observed_ns)
    self.assertEqual(output, sample.observed_ns)
