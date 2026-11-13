"""Fresh camera STOP evidence and bounded confirmation, independent of speed retention."""
from dataclasses import dataclass
import math

from openpilot.common.transformations.camera import DEVICE_CAMERAS
from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID, MAX_PAIR_SKEW_NS

STOP_MAX_AGE_NS = 1_500_000_000
# One maximum source gap plus the next sample's fresh arrival window.
STOP_HISTORY_MAX_AGE_NS = 2 * STOP_MAX_AGE_NS
CONTROL_STOP_MAX_AGE_NS = 750_000_000
RECEIPT_MAX_AGE_NS = 1_000_000_000
STOP_CONFIRM_CONFIDENCE = .7
STOP_SIGN_HEIGHT_M = .762
# Fitted on 17 confirmed sign approaches: where the car stops relative to the box range.
STOP_RANGE_OFFSET_M = 2.0
ROAD_FOCAL_PX = {camera.narrow_road.size: camera.narrow_road.focal_length for camera in DEVICE_CAMERAS.values()}


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


def _read_stop_evidence(sm, *, now_ns: int, now_boot_ns: int, drive_id: int,
                        after_frame: int = -1) -> tuple[StopEvidence | None, bool, int | None]:
  try:
    service = 'slcVisionObservation'
    if (drive_id <= 0 or not sm.seen[service] or not sm.valid[service] or not sm.alive[service] or
        int(sm.recv_frame[service]) <= after_frame):
      return None, False, None
    published = int(sm.logMonoTime[service])
    receipt = int(sm.recv_time[service] * 1e9)
    if not (drive_id < published <= now_ns and now_ns - published <= RECEIPT_MAX_AGE_NS and
            drive_id < receipt <= now_ns and now_ns - receipt <= RECEIPT_MAX_AGE_NS):
      return None, False, None
    event = sm[service].vision
    sample = event.stopSigns
    session, stream = str(event.producerSessionId), str(event.stream)
    observed, eof = int(sample.observedMonoTime), int(sample.cameraFrameEofBootTime)
    expiry = int(sample.validUntilMonoTime)
    width, height, frame = int(sample.imageWidth), int(sample.imageHeight), int(sample.frameId)
    if (str(event.modelId) != MODEL_ID or str(event.status) not in ('valid', 'unknown') or
        not session or len(session) > 64 or stream not in ('road', 'wideRoad') or
        str(sample.status) != 'valid' or not drive_id < observed <= published or
        not 0 <= now_ns - observed < STOP_HISTORY_MAX_AGE_NS or not observed < expiry <= observed + STOP_MAX_AGE_NS or
        eof <= 0 or not 0 <= now_boot_ns - eof < STOP_HISTORY_MAX_AGE_NS or
        abs((now_boot_ns - eof) - (now_ns - observed)) > MAX_PAIR_SKEW_NS or
        not 1 <= width <= 8192 or not 1 <= height <= 8192 or not 0 <= frame <= 0xffffffff or
        len(sample.detections) > 4):
      return None, False, None
    boxes = []
    for item in sample.detections:
      confidence = float(item.confidence)
      box = (int(item.x1Pixels), int(item.y1Pixels), int(item.x2Pixels), int(item.y2Pixels))
      if not math.isfinite(confidence) or not .6 <= confidence <= 1 or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
        return None, False, None
      boxes.append(StopBox(confidence, box))
    expired = (now_ns >= expiry or now_ns - observed >= STOP_MAX_AGE_NS or
               now_boot_ns - eof >= STOP_MAX_AGE_NS)
    return StopEvidence(session, stream, observed, eof, frame, width, height, tuple(boxes)), expired, expiry
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
    return None, False, None


def read_stop_evidence(sm, *, now_ns: int, now_boot_ns: int, drive_id: int,
                       after_frame: int = -1) -> StopEvidence | None:
  sample, expired, _ = _read_stop_evidence(sm, now_ns=now_ns, now_boot_ns=now_boot_ns,
                                         drive_id=drive_id, after_frame=after_frame)
  return None if expired else sample


def stop_distance(sample: StopEvidence, box: StopBox) -> float | None:
  focal = ROAD_FOCAL_PX.get((sample.width, sample.height)) if sample.stream == 'road' else None
  x1, y1, x2, y2 = box.bbox
  if not focal or y2 <= y1:
    return None
  across = ((x1 + x2) / 2 - sample.width / 2) / focal
  up = ((y1 + y2) / 2 - sample.height / 2) / focal
  return focal * STOP_SIGN_HEIGHT_M / (y2 - y1) / math.sqrt(1 + across ** 2 + up ** 2) + STOP_RANGE_OFFSET_M


def _matches(a: StopBox, b: StopBox, *, display_only: bool = False) -> bool:
  ax, ay, ar, ab = a.bbox
  bx, by, br, bb = b.bbox
  aw, ah, bw, bh = ar - ax, ab - ay, br - bx, bb - by
  horizontal = 6.5 if display_only and min(a.confidence, b.confidence) < STOP_CONFIRM_CONFIDENCE else 6
  return (0.5 <= bw * bh / (aw * ah) <= 4 and
          abs(bx + br - ax - ar) <= horizontal * max(aw, bw) and
          abs(by + bb - ay - ab) <= 3 * max(ah, bh))


def _display_pair(old: StopBox, new: StopBox) -> bool:
  if old.confidence >= STOP_CONFIRM_CONFIDENCE and new.confidence >= STOP_CONFIRM_CONFIDENCE:
    return True
  old_width, old_height = old.bbox[2] - old.bbox[0], old.bbox[3] - old.bbox[1]
  new_width, new_height = new.bbox[2] - new.bbox[0], new.bbox[3] - new.bbox[1]
  return (new.confidence >= old.confidence and
          (old.confidence + new.confidence) / 2 >= STOP_CONFIRM_CONFIDENCE and
          new_width >= old_width and new_height >= old_height and
          new_width * new_height >= 2 * old_width * old_height)


class StopConfirmation:
  def __init__(self, *, display_only: bool = False):
    if type(display_only) is not bool:
      raise ValueError('Invalid STOP confirmation role')
    self._display_only = display_only
    self.reset()

  def reset(self) -> None:
    self.previous: StopEvidence | None = None
    self.confirmed_ns = 0
    self.first_seen_ns = 0
    self.distance_m: float | None = None
    self._drive_id: int | None = None
    self._previous_expiry_ns: int | None = None

  def observe_message(self, sm, *, now_ns: int, now_boot_ns: int, drive_id: int,
                      after_frame: int = -1) -> int:
    if self._drive_id != drive_id:
      self.reset()
    sample, expired, expiry = _read_stop_evidence(
      sm, now_ns=now_ns, now_boot_ns=now_boot_ns, drive_id=drive_id, after_frame=after_frame)
    previous = self.previous
    if (sample is not None and previous is not None and
        (sample.session, sample.stream, sample.observed_ns, sample.frame_id) ==
        (previous.session, previous.stream, previous.observed_ns, previous.frame_id) and
        self._previous_expiry_ns is not None and expiry != self._previous_expiry_ns):
      self.reset()
      return 0
    confirmed = self.observe(sample, now_ns=now_ns, expiry_only=expired)
    if self.previous is not None:
      self._drive_id = drive_id
      self._previous_expiry_ns = expiry
    return confirmed

  def observe(self, sample: StopEvidence | None, *, now_ns: int, expiry_only: bool = False) -> int:
    if expiry_only:
      if sample is not None and sample == self.previous and 0 <= now_ns - sample.observed_ns < STOP_HISTORY_MAX_AGE_NS:
        self.confirmed_ns = 0
        self.distance_m = None
        return 0
      self.reset()
      return 0
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
    minimum = .6 if self._display_only else STOP_CONFIRM_CONFIDENCE
    candidates = tuple(box for box in sample.boxes if box.confidence >= minimum)
    matched = tuple(new for new in candidates if previous is not None and
                    100_000_000 <= sample.observed_ns - previous.observed_ns < STOP_MAX_AGE_NS and
                    any(_matches(old, new, display_only=self._display_only) and _display_pair(old, new)
                        for old in previous.boxes if old.confidence >= minimum))
    if not candidates:
      self.first_seen_ns = 0
    elif not matched:
      self.first_seen_ns = sample.observed_ns
    self.previous = sample
    self.confirmed_ns = (sample.observed_ns if matched and
                         sample.observed_ns - self.first_seen_ns >= 500_000_000 else 0)
    self.distance_m = stop_distance(sample, max(matched, key=lambda box: box.confidence)) if self.confirmed_ns else None
    return self.confirmed_ns
