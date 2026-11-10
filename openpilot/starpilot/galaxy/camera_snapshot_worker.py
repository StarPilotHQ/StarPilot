"""CPU-only frame reader. Does not start or stop camerad or change Params."""

from io import BytesIO
import sys
import time

from PIL import Image

from msgq.visionipc import VisionIpcClient
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.starpilot.galaxy.camera_exposure import CameraStates, ExposureReadiness
from openpilot.starpilot.galaxy.camera_snapshot import CAPTURE_TIMEOUT_SECONDS, MAX_JPEG_BYTES
from openpilot.system.camerad.snapshot import extract_image


STREAMS = {"cabin": VisionStreamType.VISION_STREAM_CABIN, "wide": VisionStreamType.VISION_STREAM_WIDE_ROAD,
           "narrow": VisionStreamType.VISION_STREAM_NARROW_ROAD}
CABIN_SETTLE_NS = 10_000_000_000
WIDE_SETTLE_NS = 5_000_000_000


def boot_ns():
  return time.clock_gettime_ns(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))


def capture(camera, *, parked=False, client_factory=VisionIpcClient, clock=boot_ns, states_factory=CameraStates):
  started = clock()
  states = None
  try:
    if parked and camera in ("wide", "cabin"):
      try:
        states = states_factory(camera)
      except Exception:
        pass  # Exposure telemetry is optional; keep the bounded timed fallback.
    return _capture(camera, started, states=states, client_factory=client_factory, clock=clock)
  finally:
    close = getattr(states, "close", None)
    if close is not None:
      try:
        close()
      except Exception:
        pass


def _capture(camera, started, *, states, client_factory, clock):
  readiness = ExposureReadiness(camera, started)
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
    if now >= deadline:
      return None
    # Native camerad does not populate VIPC extra.valid; use capture freshness.
    if frame is None or not started < client.timestamp_sof <= client.timestamp_eof <= now:
      continue
    if now - client.timestamp_sof > 500_000_000:
      continue
    early = False
    if states is not None:
      frame_id = client.frame_id
      readiness.observe_frame(frame_id, client.timestamp_sof, client.timestamp_eof)
      try:
        events = states()
        # camerad publishes VIPC before constructing/publishing cameraState.
        # Metadata received here can legitimately be newer than the recv clock.
        now = clock()
        readiness.observe(events, now)
        early = readiness.ready(frame_id, client.timestamp_sof, client.timestamp_eof, now)
      except Exception:
        states = None
      now = clock()
      if now >= deadline:
        return None
      if now - client.timestamp_sof > 500_000_000:
        continue
    if camera == "cabin" and not early and now - connected < CABIN_SETTLE_NS:
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
