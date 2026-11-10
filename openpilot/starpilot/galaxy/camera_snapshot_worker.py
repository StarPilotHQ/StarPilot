"""CPU-only frame reader. Does not start or stop camerad or change Params."""

from io import BytesIO
import sys
import time

from PIL import Image

from msgq.visionipc import VisionIpcClient
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.starpilot.galaxy.camera_snapshot import CAPTURE_TIMEOUT_SECONDS, MAX_JPEG_BYTES
from openpilot.system.camerad.snapshot import extract_image


STREAMS = {"cabin": VisionStreamType.VISION_STREAM_CABIN, "wide": VisionStreamType.VISION_STREAM_WIDE_ROAD,
           "narrow": VisionStreamType.VISION_STREAM_NARROW_ROAD}
CABIN_SETTLE_NS = 10_000_000_000
WIDE_SETTLE_NS = 5_000_000_000
SETTLE_MIN_FRAME = 3  # the first reports carry no measurement yet
STATE_SERVICES = {"cabin": "cabinCameraState", "wide": "wideRoadCameraState", "narrow": "narrowRoadCameraState"}


def boot_ns():
  return time.clock_gettime_ns(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))


def settled(state, recent_ev, *, extremes=True):
  # Reported grey is from the previous frame; exposure percent is this frame's.
  grey, target, percent = state.measuredGreyFraction, state.targetGreyFraction, state.exposureValPercent
  if state.frameId < SETTLE_MIN_FRAME:
    return False
  # Cabin IR keeps brightening after exposure maxes out.
  if extremes and ((percent >= 99 and grey < target) or (percent <= 1 and grey > target)):
    return True
  # A black frame also reads near target, so exposure must be steady too.
  return abs(grey - target) <= 0.25 * target and len(recent_ev) == 3 and max(recent_ev) <= 1.05 * min(recent_ev)


def camera_states(camera):
  from openpilot.cereal import messaging
  service = STATE_SERVICES[camera]
  sock = messaging.sub_sock(service)
  return lambda: [getattr(event, service) for event in messaging.drain_sock(sock)]


def capture(camera, *, parked=False, client_factory=VisionIpcClient, clock=boot_ns, states_factory=camera_states):
  started = clock()
  states = states_factory(camera) if parked else None
  settled_from = None
  recent_ev = []
  client = client_factory("camerad", STREAMS[camera], True)
  while not client.connect(False):
    if clock() - started > 9_000_000_000:
      return None
    time.sleep(.1)
  connected = clock()
  deadline = started + (CAPTURE_TIMEOUT_SECONDS[camera] - 2) * 1_000_000_000
  wide_ready_after = None
  while clock() < deadline:
    frame = client.recv(timeout_ms=100)
    now = clock()
    if states is not None and settled_from is None:
      for state in states():
        recent_ev = (recent_ev + [state.integLines * state.gain])[-3:]
        if settled_from is None and settled(state, recent_ev, extremes=camera != "cabin"):
          settled_from = state.frameId - 1
    early = settled_from is not None and client.frame_id >= settled_from
    if camera == "cabin" and not early and now - connected < CABIN_SETTLE_NS:
      continue
    # Native camerad does not populate VIPC extra.valid; use capture freshness.
    if frame is None or not started < client.timestamp_sof <= client.timestamp_eof <= now:
      continue
    if now - client.timestamp_sof > 500_000_000:
      continue
    if camera == "wide" and not early:
      # A live IPC connection can precede camera frames. Count warm-up from the
      # first fresh frame, then require an exposure captured after that interval.
      if wide_ready_after is None:
        wide_ready_after = client.timestamp_sof + WIDE_SETTLE_NS
      if client.timestamp_sof < wide_ready_after:
        continue
    width, height, stride, uv_offset = frame.width, frame.height, frame.stride, frame.uv_offset
    uv_height = ((height // 2 + 15) // 16) * 16
    if (not 0 < width <= 4096 or not 0 < height <= 4096 or width % 2 or height % 2 or
        not width <= stride <= 8192 or stride % 2 or not stride * height <= uv_offset <= stride * 4096 or
        uv_offset % stride or len(frame.data) < uv_offset + stride * uv_height):
      return None
    image = Image.fromarray(extract_image(frame))
    image.thumbnail((1280, 720))
    output = BytesIO()
    image.save(output, format="JPEG", quality=85)
    body = output.getvalue()
    return body if len(body) <= MAX_JPEG_BYTES else None
  return None


if __name__ == '__main__':
  try:
    valid = len(sys.argv) in (2, 3) and sys.argv[1] in STREAMS and sys.argv[2:] in ([], ['parked'])
    result = capture(sys.argv[1], parked=len(sys.argv) == 3) if valid else None
    if result is not None:
      sys.stdout.buffer.write(result)
    else:
      sys.exit(1)
  except Exception:
    sys.exit(1)
