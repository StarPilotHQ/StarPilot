"""Hardware H.264 for Android Auto: ctypes wrapper around hw/libstarport_encoder.so, and encoder selection.

The library owns a dedicated Qualcomm V4L2 encoder session (separate from
loggerd's), converts RGBA to NV12 with NEON straight into its ION buffer (or
copies NV12 the car renderer already converted on the GPU), and returns one
access unit per call. ABI 3 adds NV12 input and constant-bitrate rate control;
an ABI 2 library still works, RGBA and VBR only. ``create_encoder`` validates it with a forced
keyframe before a session starts. Desktop auto mode may fall back to libx264;
comma sessions require hardware and report initialization failure. The
codec is never swapped mid-session; a driver session that times out is reopened
in place (``reopen``) so the projection survives a stall of the shared VPU.

Adapted from yummydirtx/openpilot ``tools/starport/{hardware_encode,live_encode}.py``
(MIT), pinned at 672a16f6183567c0ada53654f8527d97e1a483fa.
"""

from __future__ import annotations

import ctypes
import time
from pathlib import Path

from openpilot.starpilot.system.starport.encoder import MAX_ACCESS_UNIT, START_CODE, H264Encoder, nal_types

LIBRARY = Path(__file__).with_name("hw") / "libstarport_encoder.so"
ABI = 4
SUPPORTED_ABIS = (2, 3, 4)
OPTION_CBR = 1
OPTION_HEVC = 2          # ABI 4: H.265 Main profile instead of H.264 baseline
MAX_WIDTH, MAX_HEIGHT, HW_FPS = 1280, 720, 30
HEVC_MAX_WIDTH, HEVC_MAX_HEIGHT = 1920, 1080
AUD = b"\x00\x00\x00\x01\x09\xf0"
HEVC_AUD = b"\x00\x00\x00\x01\x46\x01\x50"


def normalize_access_unit(data: bytes, *, keyframe: bool) -> bytes:
  """Match the libx264 contract: exactly one leading AUD, one picture, SPS/PPS on keyframes."""
  if not data.startswith((b"\x00\x00\x01", b"\x00\x00\x00\x01")) or len(data) > MAX_ACCESS_UNIT:
    raise RuntimeError("Expected a bounded Annex B hardware frame")
  starts = list(START_CODE.finditer(data))
  units = []
  for index, match in enumerate(starts):
    end = starts[index + 1].start() if index + 1 < len(starts) else len(data)
    if match.end() >= end:
      raise RuntimeError("Empty hardware NAL unit")
    units.append((data[match.end()] & 31, data[match.start():end]))
  kinds = [kind for kind, _ in units]
  if kinds.count(1) + kinds.count(5) != 1:
    raise RuntimeError("Hardware encoder did not produce one picture")
  if keyframe and not {5, 7, 8}.issubset(kinds):
    raise RuntimeError("Hardware encoder did not produce an independently decodable keyframe")
  result = AUD + b"".join(unit for kind, unit in units if kind != 9)
  if len(result) > MAX_ACCESS_UNIT:
    raise RuntimeError("Hardware access unit too large")
  return result


def hevc_nal_kinds(data: bytes) -> list[int]:
  return [(data[match.end()] >> 1) & 0x3F for match in START_CODE.finditer(data) if match.end() < len(data)]


def normalize_access_unit_hevc(data: bytes, *, keyframe: bool) -> bytes:
  """H.265 counterpart of normalize_access_unit: exactly one leading AUD, one picture, VPS/SPS/PPS on keyframes.

  A picture NAL is a VCL unit (types 0-9 and 16-21); an IDR is type 19 or 20. The comma's encoder splits every H.265
  picture into two slices (measured on the device: [19, 19] and [1, 1]), so one or more VCL units make a picture.
  Separate from the H.264 function so that path is unchanged."""
  if not data.startswith((b"\x00\x00\x01", b"\x00\x00\x00\x01")) or len(data) > MAX_ACCESS_UNIT:
    raise RuntimeError("Expected a bounded Annex B hardware frame")
  starts = list(START_CODE.finditer(data))
  units = []
  for index, match in enumerate(starts):
    end = starts[index + 1].start() if index + 1 < len(starts) else len(data)
    if match.end() + 1 >= end:
      raise RuntimeError("Empty hardware NAL unit")
    units.append(((data[match.end()] >> 1) & 0x3F, data[match.start():end]))
  kinds = [kind for kind, _ in units]
  pictures = [kind for kind in kinds if kind <= 9 or 16 <= kind <= 21]
  if not pictures or len({kind in (19, 20) for kind in pictures}) != 1:  # slices of one picture are all IDR or none are
    raise RuntimeError("Hardware encoder did not produce one picture")
  if keyframe and not ({19, 20} & set(kinds) and {32, 33, 34}.issubset(kinds)):
    raise RuntimeError("Hardware encoder did not produce an independently decodable keyframe")
  result = HEVC_AUD + b"".join(unit for kind, unit in units if kind != 35)
  if len(result) > MAX_ACCESS_UNIT:
    raise RuntimeError("Hardware access unit too large")
  return result


# ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: Hardware encoding and bounded frame submission avoid CPU stalls and encoder backlog.
# Altering this can degrade video smoothness or introduce lag on Android Auto.
# Do not change it autonomously; require an explicit user request specifically for this video optimization.
class HardwareH264Encoder:
  backend = "qcom-v4l2"
  codec = "h264"
  max_width, max_height = MAX_WIDTH, MAX_HEIGHT   # level 3.1 baseline is a 720p30 session
  codec_options = 0
  required_abi = 2

  def __init__(self, width: int, height: int, fps: int = HW_FPS, bitrate_kbps: int = 6000, margin_height: int = 0,
               library: Path = LIBRARY, rate_control: str = "cbr"):
    if not (0 < width <= self.max_width and 0 < height <= self.max_height) or width % 2 or height % 2 or fps != HW_FPS:
      raise ValueError(f"Hardware encoder supports even sizes up to {self.max_width}x{self.max_height} at 30 fps")
    if margin_height % 4:
      margin_height = 0  # only chroma-aligned black margins may be skipped
    self.width, self.height, self.fps = width, height, fps
    self.frame_index = 0
    self.last_encode_ms = 0.0
    self.handle = None
    self.lib = ctypes.CDLL(str(library))
    self.lib.starport_encoder_abi.restype = ctypes.c_int
    self.abi = self.lib.starport_encoder_abi()
    if self.abi not in SUPPORTED_ABIS or self.abi < self.required_abi:
      raise RuntimeError("libstarport_encoder.so ABI mismatch; rebuild")
    pointer = ctypes.c_void_p
    encode_args = [pointer, pointer, ctypes.c_size_t, ctypes.c_int, pointer, ctypes.c_size_t, pointer, ctypes.c_size_t]
    self.lib.starport_encoder_create.argtypes = [ctypes.c_int] * (6 if self.abi >= 3 else 5) + [pointer, ctypes.c_size_t]
    self.lib.starport_encoder_create.restype = pointer
    self.lib.starport_encoder_encode.argtypes = encode_args
    self.lib.starport_encoder_encode.restype = ctypes.c_int
    self.supports_nv12 = self.abi >= 3
    if self.supports_nv12:
      self.lib.starport_encoder_encode_nv12.argtypes = encode_args
      self.lib.starport_encoder_encode_nv12.restype = ctypes.c_int
    self.rate_control = "cbr" if rate_control == "cbr" and self.abi >= 3 else "vbr"
    self.lib.starport_encoder_destroy.argtypes = [pointer]
    self.lib.starport_encoder_destroy.restype = None
    self.error = ctypes.create_string_buffer(512)
    self.output = ctypes.create_string_buffer(MAX_ACCESS_UNIT)
    options = ((OPTION_CBR if self.rate_control == "cbr" else 0) | self.codec_options,) if self.abi >= 3 else ()
    self._create_args = (width, height, fps, int(bitrate_kbps) * 1000, margin_height, *options)
    self._open()

  def _open(self) -> None:
    self.handle = self.lib.starport_encoder_create(*self._create_args, self.error, len(self.error))
    if not self.handle:
      raise RuntimeError(self.error.value.decode("utf-8", "replace") or "hardware encoder unavailable")
    self.frame_index = 0  # the first picture of a fresh driver session is an IDR

  def reopen(self) -> None:
    """Replace a failed driver session (a timeout leaves buffers queued) with a fresh one, same settings."""
    self.close()
    self._open()

  def encode_rgba(self, rgba, *, keyframe: bool = False) -> tuple[bytes, bool]:
    if len(rgba) != self.width * self.height * 4:
      raise ValueError("Expected tightly packed RGBA at the negotiated size")
    return self._encode(self.lib.starport_encoder_encode, rgba, keyframe)

  def encode_nv12(self, nv12, *, keyframe: bool = False) -> tuple[bytes, bool]:
    if not self.supports_nv12:
      raise RuntimeError("libstarport_encoder.so predates NV12 input; rebuild")
    if len(nv12) != self.width * self.height * 3 // 2:
      raise ValueError("Expected tightly packed NV12 at the negotiated size")
    return self._encode(self.lib.starport_encoder_encode_nv12, nv12, keyframe)

  def _encode(self, function, pixels, keyframe: bool) -> tuple[bytes, bool]:
    if not self.handle:
      raise RuntimeError("Encoder has been closed")
    started = time.monotonic()
    source = ctypes.c_char_p(bytes(pixels)) if not isinstance(pixels, bytes) else ctypes.c_char_p(pixels)
    force = keyframe or self.frame_index == 0
    size = function(self.handle, source, len(pixels), int(force), self.output, len(self.output), self.error, len(self.error))
    if size < 0:
      raise RuntimeError(self.error.value.decode("utf-8", "replace"))
    if size > len(self.output):
      raise RuntimeError("Hardware access unit too large")
    # .raw copies the entire 2 MiB capacity, even for a small inter frame.
    data = self.normalize(ctypes.string_at(self.output, size), keyframe=force)
    self.frame_index += 1
    self.last_encode_ms = (time.monotonic() - started) * 1000
    return data, self.is_keyframe(data)

  def normalize(self, data: bytes, *, keyframe: bool) -> bytes:
    return normalize_access_unit(data, keyframe=keyframe)

  def is_keyframe(self, data: bytes) -> bool:
    return {5, 7, 8}.issubset(nal_types(data))

  def close(self) -> None:
    handle, self.handle = self.handle, None
    if handle:
      self.lib.starport_encoder_destroy(handle)


class HardwareHevcEncoder(HardwareH264Encoder):
  """H.265 for a head unit that offers nothing else (Allow H.265 video). Hardware only: there is no software fallback."""
  backend = "qcom-v4l2-hevc"
  codec = "h265"
  max_width, max_height = HEVC_MAX_WIDTH, HEVC_MAX_HEIGHT
  codec_options = OPTION_HEVC
  required_abi = 4

  def normalize(self, data: bytes, *, keyframe: bool) -> bytes:
    return normalize_access_unit_hevc(data, keyframe=keyframe)

  def is_keyframe(self, data: bytes) -> bool:
    kinds = set(hevc_nal_kinds(data))
    return bool({19, 20} & kinds) and {32, 33, 34}.issubset(kinds)


def create_hevc_encoder(width: int, height: int, *, bitrate_kbps: int, margin_height: int, log,
                        rate_control: str = "cbr") -> tuple[object, int]:
  """Return ``(encoder, fps)`` for an H.265 session. The car was told H.265, so a stand-in codec would send it a stream it
  cannot decode: if the hardware encoder cannot open, the session fails with the reason."""
  encoder = None
  try:
    if not LIBRARY.is_file():
      raise RuntimeError(f"{LIBRARY.name} not built")
    encoder = HardwareHevcEncoder(width, height, bitrate_kbps=max(bitrate_kbps, 4000), margin_height=margin_height,
                                  rate_control=rate_control)
    encoder.encode_rgba(bytes(width * height * 4), keyframe=True)  # exercise the driver and the keyframe contract
  except Exception as error:
    if encoder is not None:
      encoder.close()
    raise RuntimeError(f"The car needs H.265 and the hardware encoder could not start it: {error}") from error
  log("encoder", backend=encoder.backend, codec=encoder.codec, fps=HW_FPS, abi=encoder.abi, rate_control=encoder.rate_control,
      nv12=encoder.supports_nv12)
  return encoder, HW_FPS


# ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: Backend selection and frame-rate budgets are part of the low-latency video pipeline.
# Altering this can degrade video smoothness or introduce lag on Android Auto.
# Do not change it autonomously; require an explicit user request specifically for this video optimization.
def create_encoder(width: int, height: int, *, preference: str, bitrate_kbps: int, margin_height: int,
                   software_fps: int, log, rate_control: str = "cbr") -> tuple[object, int]:
  """Return ``(encoder, fps)``: hardware at 30 fps when it works, else libx264."""
  if preference not in ("auto", "hardware", "software"):
    preference = "auto"
  if preference != "software":
    encoder = None
    try:
      if not LIBRARY.is_file():
        raise RuntimeError(f"{LIBRARY.name} not built")
      encoder = HardwareH264Encoder(width, height, bitrate_kbps=max(bitrate_kbps, 4000), margin_height=margin_height,
                                    rate_control=rate_control)
      # Exercise the driver and the keyframe contract before promising a session.
      encoder.encode_rgba(bytes(width * height * 4), keyframe=True)
      log("encoder", backend=encoder.backend, fps=HW_FPS, abi=encoder.abi, rate_control=encoder.rate_control,
          nv12=encoder.supports_nv12)
      return encoder, HW_FPS
    except Exception as error:
      if encoder is not None:
        encoder.close()
      if preference == "hardware":
        raise
      log("encoder_fallback", reason=f"{type(error).__name__}: {str(error)[:160]}")
  encoder = H264Encoder(width, height, fps=software_fps, bitrate_kbps=bitrate_kbps)
  log("encoder", backend=encoder.backend, fps=software_fps)
  return encoder, software_fps
