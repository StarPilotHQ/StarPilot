"""Fresh camera STOP evidence and bounded confirmation, independent of speed retention."""
from dataclasses import dataclass
import math

from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID, MAX_PAIR_SKEW_NS

STOP_MAX_AGE_NS = 1_500_000_000
CONTROL_STOP_MAX_AGE_NS = 750_000_000
RECEIPT_MAX_AGE_NS = 1_000_000_000
STOP_CONFIRM_CONFIDENCE = .7


@dataclass(frozen=True)
class StopBox:
  confidence: float
  bbox: tuple[int, int, int, int]


@dataclass(frozen=True)
class StopEvidence:
  session: str
  stream: str
  observed_ns: int
  eof_boot_ns: int
  frame_id: int
  width: int
  height: int
  boxes: tuple[StopBox, ...]


def read_stop_evidence(sm, *, now_ns: int, now_boot_ns: int, drive_id: int,
                       after_frame: int = -1) -> StopEvidence | None:
  try:
    service = 'slcVisionObservation'
    if (drive_id <= 0 or not sm.seen[service] or not sm.valid[service] or not sm.alive[service] or
        int(sm.recv_frame[service]) <= after_frame):
      return None
    published = int(sm.logMonoTime[service])
    receipt = int(sm.recv_time[service] * 1e9)
    if not (drive_id < published <= now_ns and now_ns - published <= RECEIPT_MAX_AGE_NS and
            drive_id < receipt <= now_ns and now_ns - receipt <= RECEIPT_MAX_AGE_NS):
      return None
    event = sm[service].vision
    sample = event.stopSigns
    session, stream = str(event.producerSessionId), str(event.stream)
    observed, eof = int(sample.observedMonoTime), int(sample.cameraFrameEofBootTime)
    expiry = int(sample.validUntilMonoTime)
    width, height, frame = int(sample.imageWidth), int(sample.imageHeight), int(sample.frameId)
    if (str(event.modelId) != MODEL_ID or not session or len(session) > 64 or stream not in ('road', 'wideRoad') or
        str(sample.status) != 'valid' or not drive_id < observed <= published or
        not 0 <= now_ns - observed < STOP_MAX_AGE_NS or not observed < expiry <= observed + STOP_MAX_AGE_NS or
        now_ns >= expiry or eof <= 0 or not 0 <= now_boot_ns - eof < STOP_MAX_AGE_NS or
        abs((now_boot_ns - eof) - (now_ns - observed)) > MAX_PAIR_SKEW_NS or
        not 1 <= width <= 8192 or not 1 <= height <= 8192 or not 0 <= frame <= 0xffffffff or
        len(sample.detections) > 4):
      return None
    boxes = []
    for item in sample.detections:
      confidence = float(item.confidence)
      box = (int(item.x1Pixels), int(item.y1Pixels), int(item.x2Pixels), int(item.y2Pixels))
      if not math.isfinite(confidence) or not .6 <= confidence <= 1 or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
        return None
      boxes.append(StopBox(confidence, box))
    return StopEvidence(session, stream, observed, eof, frame, width, height, tuple(boxes))
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
    return None


def _matches(a: StopBox, b: StopBox) -> bool:
  ax, ay, ar, ab = a.bbox
  bx, by, br, bb = b.bbox
  aw, ah, bw, bh = ar - ax, ab - ay, br - bx, bb - by
  return (0.5 <= bw * bh / (aw * ah) <= 4 and
          abs(bx + br - ax - ar) <= 6 * max(aw, bw) and
          abs(by + bb - ay - ab) <= 3 * max(ah, bh))


class StopConfirmation:
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.previous: StopEvidence | None = None
    self.confirmed_ns = 0
    self.first_seen_ns = 0

  def observe(self, sample: StopEvidence | None, *, now_ns: int) -> int:
    if sample is None or not 0 <= now_ns - sample.observed_ns < STOP_MAX_AGE_NS:
      self.reset()
      return 0
    previous = self.previous
    if previous is not None and (sample.session, sample.stream, sample.width, sample.height) != (
        previous.session, previous.stream, previous.width, previous.height):
      self.reset()
      previous = None
    if previous is not None and sample.observed_ns == previous.observed_ns and sample.frame_id == previous.frame_id:
      if sample != previous:
        self.reset()
        return 0
      return self.confirmed_ns
    if previous is not None and (sample.observed_ns <= previous.observed_ns or sample.frame_id <= previous.frame_id):
      self.reset()
      return 0
    candidates = tuple(box for box in sample.boxes if box.confidence >= STOP_CONFIRM_CONFIDENCE)
    coherent = (previous is not None and 100_000_000 <= sample.observed_ns - previous.observed_ns < STOP_MAX_AGE_NS and
                any(_matches(old, new) for old in previous.boxes if old.confidence >= STOP_CONFIRM_CONFIDENCE for new in candidates))
    if not candidates:
      self.first_seen_ns = 0
    elif not coherent:
      self.first_seen_ns = sample.observed_ns
    self.previous = sample
    self.confirmed_ns = (sample.observed_ns if candidates and coherent and
                         sample.observed_ns - self.first_seen_ns >= 500_000_000 else 0)
    return self.confirmed_ns
