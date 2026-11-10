"""One-frame-at-a-time software H.264 for Android Auto (baseline, zero latency).

Each call encodes exactly one RGBA frame into one AUD-delimited Annex B access
unit, so there is never an encoder-side queue. Keyframes carry SPS/PPS
(``repeat-headers``) and are forced on request, which the session requires at
the start of every media epoch (new connection or regained display focus).

PyAV is taken from the system Python when its bundled FFmpeg has libx264;
otherwise from an isolated ``/data/starport/deps`` target (see
docs/how-to/wireless-android-auto.md). Adapted from yummydirtx/openpilot
``tools/starport/live_encode.py`` (MIT), pinned at 672a16f6183567c0ada53654f8527d97e1a483fa.
"""

from __future__ import annotations

import os
import re
import sys
import time
from fractions import Fraction

ISOLATED_DEPS = os.environ.get("STARPORT_DEPS", os.environ.get("ANDROID_AUTO_DEPS", "/data/starport/deps"))
MAX_ACCESS_UNIT = 2 * 1024 * 1024 - 16
START_CODE = re.compile(b"\x00\x00(?:\x00)?\x01")


def nal_types(data: bytes) -> list[int]:
  types = []
  for match in START_CODE.finditer(data):
    if match.end() < len(data):
      types.append(data[match.end()] & 31)
  return types


def import_av():
  """Import a PyAV whose FFmpeg includes libx264, preferring the system install."""
  errors = []
  try:
    import av
    if "libx264" in av.codecs_available:
      return av
    errors.append(f"system PyAV {av.__version__} lacks libx264")
  except ImportError as error:
    errors.append(f"system PyAV missing ({error})")
  if os.path.isdir(ISOLATED_DEPS):
    for name in [key for key in sys.modules if key == "av" or key.startswith("av.")]:
      del sys.modules[name]
    sys.path.insert(0, ISOLATED_DEPS)
    try:
      import av
      if "libx264" in av.codecs_available:
        return av
      errors.append(f"isolated PyAV {av.__version__} lacks libx264")
    except ImportError as error:
      errors.append(f"isolated PyAV missing ({error})")
    finally:
      if sys.path and sys.path[0] == ISOLATED_DEPS:
        sys.path.pop(0)
  raise RuntimeError("No H.264 encoder available: " + "; ".join(errors))


# ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: The software fallback uses zero-latency H.264 with no encoder-side frame queue.
# Altering this can degrade video smoothness or introduce lag on Android Auto.
# Do not change it autonomously; require an explicit user request specifically for this video optimization.
class H264Encoder:
  backend = "libx264"

  def __init__(self, width: int, height: int, fps: int = 30, bitrate_kbps: int = 4000):
    if not (0 < min(width, height) <= 1080 and max(width, height) <= 1920) or width % 2 or height % 2:
      raise ValueError("H.264 requires even dimensions no larger than 1920x1080")
    av = import_av()
    self.av = av
    self.width, self.height, self.fps = width, height, fps
    self.frame_index = 0
    self.last_encode_ms = 0.0
    level = "4.0" if width * height > 1280 * 720 else "3.1"
    codec = av.CodecContext.create("libx264", "w")
    codec.width = width
    codec.height = height
    codec.pix_fmt = "yuv420p"
    codec.time_base = Fraction(1, fps)
    codec.framerate = Fraction(fps)
    codec.thread_count = 1
    codec.gop_size = fps * 2
    codec.max_b_frames = 0
    codec.options = {
      "preset": "ultrafast", "tune": "zerolatency", "profile": "baseline", "level": level, "forced-idr": "1",
      # Bounded bitrate keeps Wi-Fi bursts small; periodic IDR bounds recovery from a lost frame.
      "x264-params": ":".join((
        "aud=1", "repeat-headers=1", "annexb=1", f"keyint={fps * 2}", f"min-keyint={fps}", "scenecut=0",
        f"vbv-maxrate={bitrate_kbps}", f"vbv-bufsize={bitrate_kbps // 2}", "crf=23")),
    }
    codec.open()
    self.codec = codec
    self._frame = av.VideoFrame(width, height, "rgba")
    if self._frame.planes[0].line_size != width * 4:
      raise RuntimeError("PyAV RGBA frame is not tightly packed")

  def encode_rgba(self, rgba, *, keyframe: bool = False) -> tuple[bytes, bool]:
    """Encode one frame; returns ``(access_unit, is_keyframe)``."""
    if self.codec is None:
      raise RuntimeError("Encoder has been closed")
    started = time.monotonic()
    self._frame.planes[0].update(rgba)
    frame = self._frame.reformat(format="yuv420p")
    frame.pts = self.frame_index
    frame.time_base = self.codec.time_base
    force = keyframe or self.frame_index == 0
    if force:
      frame.pict_type = self.av.video.frame.PictureType.I
    packets = self.codec.encode(frame)
    if len(packets) != 1:
      raise RuntimeError(f"Low-latency encoder buffered or split a frame ({len(packets)} packets)")
    data = bytes(packets[0])
    types = nal_types(data)
    if len(data) > MAX_ACCESS_UNIT or types.count(9) != 1:
      raise RuntimeError("Encoder did not produce one bounded AUD-delimited access unit")
    is_keyframe = {5, 7, 8}.issubset(types)
    if force and not is_keyframe:
      raise RuntimeError("Encoder did not produce an independently decodable keyframe")
    self.frame_index += 1
    self.last_encode_ms = (time.monotonic() - started) * 1000
    return data, is_keyframe

  def close(self) -> None:
    codec, self.codec = self.codec, None
    if codec is not None:
      try:
        codec.encode(None)
      except Exception:
        pass
