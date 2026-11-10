"""Optional, frame-bound exposure evidence. Missing evidence keeps the timed fallback."""

from collections import deque
import math


STATE_SERVICES = {"cabin": "cabinCameraState", "wide": "wideRoadCameraState", "narrow": "narrowRoadCameraState"}
# Use the native camera test's sustained target-grey window, with an additional
# command-stability check. This is an early-capture gate, not a sensor calibration.
EXPOSURE_STABLE_FRAMES = 20
EXPOSURE_TOLERANCE = 0.2
EXPOSURE_COMMAND_LATENCY_FRAMES = 3
STATE_MAX_AGE_NS = 500_000_000


class CameraStates:
  def __init__(self, camera):
    from openpilot.cereal import messaging
    self.messaging = messaging
    self.sock = messaging.sub_sock(STATE_SERVICES[camera])

  def __call__(self):
    # Bound each read even if a publisher produces an unexpected backlog.
    events = []
    for _ in range(32):
      event = self.messaging.recv_one_or_none(self.sock)
      if event is None:
        break
      events.append(event)
    return events

  def close(self):
    self.sock.close()


class ExposureReadiness:
  def __init__(self, camera, started):
    self.service, self.started = STATE_SERVICES[camera], started
    self.frames = {}
    self.last_frame = None
    self.last_state = None
    self.ev = deque(maxlen=EXPOSURE_STABLE_FRAMES)
    self.ready_from = None

  def clear(self):
    self.ev.clear()
    self.ready_from = None

  def observe_frame(self, frame_id, sof, eof):
    if type(frame_id) is not int or frame_id < 0:
      self.clear()
      return
    if self.last_frame is not None and (frame_id <= self.last_frame[0] or sof <= self.last_frame[1]):
      self.frames.clear()
      self.last_state = None
      self.clear()
    self.last_frame = (frame_id, sof, eof)
    self.frames[frame_id] = (sof, eof)
    while len(self.frames) > 32:
      self.frames.pop(next(iter(self.frames)))

  def observe(self, events, now):
    for event in events:
      try:
        if not event.valid or event.which() != self.service:
          raise ValueError("Invalid camera state")
        state = getattr(event, self.service)
        frame_id, sof, eof = state.frameId, state.timestampSof, state.timestampEof
        if (type(frame_id) is not int or frame_id < 3 or
            not self.started < sof <= eof <= now or now - sof > STATE_MAX_AGE_NS or
            not sof <= event.logMonoTime <= now or now - event.logMonoTime > STATE_MAX_AGE_NS or
            self.frames.get(frame_id) != (sof, eof)):
          raise ValueError("Camera state does not describe an observed fresh frame")
        if self.last_state is not None:
          previous_id, previous_sof, _ = self.last_state
          if frame_id <= previous_id or sof <= previous_sof:
            raise ValueError("Repeated or reversed camera state")
          if frame_id != previous_id + 1 or sof - previous_sof > STATE_MAX_AGE_NS:
            self.clear()
        self.last_state = (frame_id, sof, eof)
        grey, target, gain = state.measuredGreyFraction, state.targetGreyFraction, state.gain
        lines, percent = state.integLines, state.exposureValPercent
        if (not all(math.isfinite(value) for value in (grey, target, gain, percent)) or
            not 0 <= grey <= 1 or not 0 < target <= 1 or gain <= 0 or
            type(lines) is not int or lines <= 0 or not 0 <= percent <= 100):
          raise ValueError("Invalid exposure measurement")
        ev = lines * gain
        if not math.isfinite(ev) or abs(grey - target) > EXPOSURE_TOLERANCE * target:
          raise ValueError("Exposure has not converged")
        self.ev.append(ev)
        if len(self.ev) == EXPOSURE_STABLE_FRAMES and max(self.ev) <= 1.05 * min(self.ev):
          if self.ready_from is None:
            # Grey describes the previous processed frame, and commands need
            # three frames to reach the sensor. Do not accept that earlier image.
            self.ready_from = frame_id + EXPOSURE_COMMAND_LATENCY_FRAMES
        else:
          self.ready_from = None
      except (AttributeError, TypeError, ValueError, OverflowError):
        # Process the whole batch: newer contrary evidence revokes an older
        # ready report. Saturation alone never authorizes an early snapshot.
        self.clear()

  def ready(self, frame_id, sof, eof, now):
    if self.ready_from is None or self.last_state is None:
      return False
    state_id, state_sof, _ = self.last_state
    return (self.frames.get(frame_id) == (sof, eof) and frame_id >= self.ready_from and
            0 <= frame_id - state_id <= 1 and 0 <= now - state_sof <= STATE_MAX_AGE_NS)
