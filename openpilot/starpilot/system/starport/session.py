"""Phone-side Android Auto session over any socket-compatible byte transport.

StarPilot plays the *phone* role: the head unit sends the version request, then
acts as the TLS client while this side is the TLS server presenting the phone
identity. After authentication the session discovers services, opens only the
video channel (and, best effort, the input channel so touches are acknowledged
and discarded) and streams H.264 access units with bounded acknowledgement flow.

Adapted from yummydirtx/openpilot ``tools/starport/{session,video,live_session}.py``
(MIT), pinned at 672a16f6183567c0ada53654f8527d97e1a483fa, whose protocol facts
were cross-checked against AACS. Transport-neutral: the donor used USB and the
desktop head unit over TCP; here the transport is the head unit's Wi-Fi TCP port.
"""

from __future__ import annotations

import math
import re
import select
import ssl
import struct
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from openpilot.starpilot.system.starport.nav_status import NAVIGATION_SERVICE, parse_navigation
from openpilot.starpilot.system.starport.touch import InputConfig, KeyTracker, TouchEvent, TouchMapper, parse_input_config
from openpilot.starpilot.system.starport.wire import describe, describe_fields, field, json_fields, one, parse_fields, signed

MAX_MESSAGE = 2 * 1024 * 1024
PHONE_MAX_VERSION = (6, 1)  # protocol version Android Auto 17.6 reports to newer head units
MAX_FRAGMENT_BYTES = 2 * MAX_MESSAGE
FRAGMENT_SIZE = 16000

FLAG_FIRST = 1
FLAG_LAST = 2
FLAG_CONTROL = 4
FLAG_ENCRYPTED = 8

# Control channel (0) message ids.
MSG_VERSION_REQUEST = 0x01
MSG_VERSION_RESPONSE = 0x02
MSG_SSL_HANDSHAKE = 0x03
MSG_AUTH_COMPLETE = 0x04
MSG_SERVICE_DISCOVERY_REQUEST = 0x05
MSG_SERVICE_DISCOVERY_RESPONSE = 0x06
MSG_CHANNEL_OPEN_REQUEST = 0x07
MSG_CHANNEL_OPEN_RESPONSE = 0x08
MSG_PING_REQUEST = 0x0b
MSG_PING_RESPONSE = 0x0c
MSG_SHUTDOWN_REQUEST = 0x0f
MSG_SHUTDOWN_RESPONSE = 0x10

# Media channel message ids.
AV_MEDIA_WITH_TIMESTAMP = 0x0000
AV_MEDIA_CODEC_CONFIG = 0x0001  # SPS/PPS without a timestamp, sent before the first frame like a phone's MediaCodec
AV_SETUP_REQUEST = 0x8000
AV_START_INDICATION = 0x8001
AV_STOP_INDICATION = 0x8002
AV_SETUP_RESPONSE = 0x8003
AV_MEDIA_ACK = 0x8004
VIDEO_FOCUS_REQUEST = 0x8007
VIDEO_FOCUS_INDICATION = 0x8008

# Input channel message ids.
INPUT_EVENT = 0x8001
INPUT_BINDING_REQUEST = 0x8002
INPUT_BINDING_RESPONSE = 0x8003

# Car data (sensor) and Bluetooth channels, opened the way a phone does.
SENSOR_SERVICE = 2
BLUETOOTH_SERVICE = 6
SENSOR_START_REQUEST = 0x8001
SENSOR_START_RESPONSE = 0x8002
SENSOR_EVENT = 0x8003
BLUETOOTH_PAIRING_REQUEST = 0x8001
BLUETOOTH_PAIRING_RESPONSE = 0x8002
BLUETOOTH_AUTH_DATA = 0x8003
SENSOR_NIGHT, SENSOR_DRIVING_STATUS = 10, 13

CODEC_H264_BP = 3
CODEC_H265 = 7          # MediaCodecType VIDEO_H265, offered by some head units instead of H.264
SETUP_STATUS_READY = 2
FOCUS_PROJECTED = 1
FOCUS_NATIVE = 2
FOCUS_PROJECTED_NO_INPUT = 4
FOCUS_REASON_LAUNCH_NATIVE = 2
FOCUS_REASON_USER_SELECTION = 4
SHUTDOWN_REASON_USER_SELECTION = 1

RESOLUTIONS = {1: (800, 480), 2: (1280, 720), 3: (1920, 1080)}
# Named in errors and reports only: 1440p, 4K and portrait screens also offer 800x480, which is mandatory.
RESOLUTION_NAMES = {**{index: f"{w}x{h}" for index, (w, h) in RESOLUTIONS.items()}, 4: "2560x1440", 5: "3840x2160",
                    6: "720x1280", 7: "1080x1920", 8: "1440x2560", 9: "2160x3840"}
CODEC_NAMES = {3: "H.264", 5: "VP9", 6: "AV1", 7: "H.265"}
# Messages we do not handle are logged with a readable body, but only the first few of each
# kind and then every hundredth, so a head unit repeating one cannot flood the session log.
IGNORED_LOG_FIRST = 5
IGNORED_LOG_EVERY = 100
FRAME_RATES = {1: 60, 2: 30}
# Software H.264 on the comma is the constraint: prefer 720p, then 480p. 1080p
# is accepted only when nothing smaller is offered.
RESOLUTION_PREFERENCE = (2, 1, 3)

# A real phone pings periodically; answering pings is mandatory, sending them
# is harmless and keeps impatient receivers from declaring the link idle.
PING_INTERVAL = 2.0


class AuthenticationRejected(ValueError):
  pass


class PeerRequestedStop(EOFError):
  pass


START_CODE = re.compile(b"\x00\x00(?:\x00)?\x01")


def codec_config(access_unit: bytes) -> bytes:
  """The SPS and PPS NAL units of a keyframe, as Android's codec-config buffer carries them."""
  starts = list(START_CODE.finditer(access_unit))
  units = []
  for index, match in enumerate(starts):
    end = starts[index + 1].start() if index + 1 < len(starts) else len(access_unit)
    if match.end() < end and access_unit[match.end()] & 31 in (7, 8):
      units.append(b"\x00\x00\x00\x01" + access_unit[match.end():end])
  return b"".join(units)


def wire_frame(access_unit: bytes, *, drop_config: bool) -> bytes:
  """The picture NAL units as Android's encoder sends them: no AUD, and no SPS/PPS when the codec config went separately."""
  starts = list(START_CODE.finditer(access_unit))
  drop = {9, 7, 8} if drop_config else {9}
  units = []
  for index, match in enumerate(starts):
    end = starts[index + 1].start() if index + 1 < len(starts) else len(access_unit)
    if match.end() < end and access_unit[match.end()] & 31 not in drop:
      units.append(b"\x00\x00\x00\x01" + access_unit[match.end():end])
  return b"".join(units) or access_unit


# H.265 handling is kept apart from the H.264 functions above so the H.264 path stays byte-for-byte what it was.
# A NAL header is two bytes; the type is bits 1-6 of the first (ITU-T H.265 7.3.1.2).
HEVC_PARAMETER_SETS = (32, 33, 34)   # VPS, SPS, PPS
HEVC_AUD = 35


def hevc_nal_type(first_byte: int) -> int:
  return (first_byte >> 1) & 0x3F


def codec_config_hevc(access_unit: bytes) -> bytes:
  """The VPS, SPS and PPS NAL units of an H.265 keyframe, as the codec-config buffer carries them."""
  starts = list(START_CODE.finditer(access_unit))
  units = []
  for index, match in enumerate(starts):
    end = starts[index + 1].start() if index + 1 < len(starts) else len(access_unit)
    if match.end() < end and hevc_nal_type(access_unit[match.end()]) in HEVC_PARAMETER_SETS:
      units.append(b"\x00\x00\x00\x01" + access_unit[match.end():end])
  return b"".join(units)


def wire_frame_hevc(access_unit: bytes, *, drop_config: bool) -> bytes:
  """The H.265 picture NAL units without the access unit delimiter, and without parameter sets once they went separately."""
  starts = list(START_CODE.finditer(access_unit))
  drop = {HEVC_AUD, *HEVC_PARAMETER_SETS} if drop_config else {HEVC_AUD}
  units = []
  for index, match in enumerate(starts):
    end = starts[index + 1].start() if index + 1 < len(starts) else len(access_unit)
    if match.end() < end and hevc_nal_type(access_unit[match.end()]) not in drop:
      units.append(b"\x00\x00\x00\x01" + access_unit[match.end():end])
  return b"".join(units) or access_unit


@dataclass(frozen=True)
class VideoMode:
  channel: int
  config_index: int
  width: int
  height: int
  fps: int
  margin_width: int
  margin_height: int
  content_left: int | None = None
  content_top: int | None = None
  density: int = 0  # dpi the head unit reports; 0 when it does not say
  codec: int = CODEC_H264_BP

  @property
  def content_width(self) -> int:
    return self.width - self.margin_width

  @property
  def content_height(self) -> int:
    return self.height - self.margin_height

  def as_dict(self) -> dict:
    return {"channel": self.channel, "config_index": self.config_index, "width": self.width, "height": self.height,
            "fps": self.fps, "margin_width": self.margin_width, "margin_height": self.margin_height,
            "density": self.density,
            **({"codec": CODEC_NAMES[self.codec]} if self.codec != CODEC_H264_BP else {}),
            **({"content_left": self.content_left, "content_top": self.content_top} if self.content_left is not None else {})}


def describe_video_config(config: dict, display_type: int | None = None) -> str:
  """One offered video configuration (parsed fields), e.g. "1280x720 H.264 margins 0x240 (display 1)"."""
  resolution, codec = one(config, 1), one(config, 10, CODEC_H264_BP)
  text = f"{RESOLUTION_NAMES.get(resolution, f'resolution {resolution}')} {CODEC_NAMES.get(codec, f'codec {codec}')}"
  margin_width, margin_height = one(config, 3, 0) or 0, one(config, 4, 0) or 0
  if margin_width or margin_height:
    text += f" margins {margin_width}x{margin_height}"
  if display_type:
    text += f" (display {display_type})"
  return text


def describe_video_configs(channels: list[dict]) -> str:
  """What the head unit offered, for an error a person can act on."""
  offered = [describe_video_config(config, channel.get("display_type"))
             for channel in channels for config in channel.get("video_configs", [])]
  return ", ".join(offered) or "no video"


def choose_video_mode(channels: list[dict], allow_hevc: bool = False) -> VideoMode:
  """Pick a negotiated H.264 mode the software encoder can sustain.

  H.265 is considered only when the owner allows it and the car offered no usable H.264 mode, so a car that works on
  H.264 is never changed."""
  candidates = []
  hevc_candidates = []
  for channel in channels:
    for index, config in enumerate(channel.get("video_configs", [])):
      resolution = one(config, 1)
      codec = one(config, 10, CODEC_H264_BP)
      if resolution not in RESOLUTIONS or codec not in (CODEC_H264_BP, CODEC_H265):
        continue
      width, height = RESOLUTIONS[resolution]
      margin_width, margin_height = int(one(config, 3, 0) or 0), int(one(config, 4, 0) or 0)
      if not (0 <= margin_width < width and 0 <= margin_height < height):
        continue
      fps = FRAME_RATES.get(one(config, 2, 2), 30)
      left = top = None
      additional = one(config, 11)
      if isinstance(additional, bytes):
        insets = one(parse_fields(additional), 1)
        if isinstance(insets, bytes):
          edges = parse_fields(insets)
          top_edge, bottom, left_edge, right = (one(edges, field, 0) for field in (1, 2, 3, 4))
          # Use explicit placement only when it describes the same visible area
          # as the legacy totals. Otherwise retain the negotiated centered mode.
          if (all(type(value) is int and value >= 0 for value in (top_edge, bottom, left_edge, right)) and
              top_edge + bottom == margin_height and left_edge + right == margin_width):
            left, top = left_edge, top_edge
      mode = VideoMode(int(channel["id"]), index, width, height, fps, margin_width, margin_height, left, top,
                       int(one(config, 5, 0) or 0), codec)
      (candidates if codec == CODEC_H264_BP else hevc_candidates).append((RESOLUTION_PREFERENCE.index(resolution), fps != 30, index, mode))
  if not candidates and allow_hevc:
    candidates = hevc_candidates
  if not candidates:
    hint = "; this car offers H.265, which can be turned on in Developer > StarPort" if hevc_candidates else ""
    raise ValueError(f"Head unit did not advertise a supported H.264 video mode (offered {describe_video_configs(channels)}){hint}")
  return min(candidates, key=lambda item: item[:3])[3]


class Session:
  """TLS, framing and control-channel handshake; one instance per TCP connection."""

  # Wireless adapters relay to the car, which can take a while (or wait for the driver) before it answers.
  DISCOVERY_TIMEOUT = 60.0

  def __init__(self, peer, cert: str, key: str, log: Callable[..., None] | None = None, ca: str | None = None, *,
               receive_timeout: float = 3.0, send_timeout: float = 3.0, handshake_timeout: float = 15.0):
    self.peer = peer
    self._log = log
    self.receive_timeout = self._timeout(receive_timeout)
    self.send_timeout = self._timeout(send_timeout)
    self.handshake_timeout = self._timeout(handshake_timeout)
    self.authenticated = False
    self.head_unit_subject: str = ""
    self.incoming = ssl.MemoryBIO()
    self.outgoing = ssl.MemoryBIO()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    self.peer_verification_enabled = ca is not None
    if ca is not None:
      context.load_verify_locations(cafile=ca)
      context.verify_mode = ssl.CERT_REQUIRED
    self.tls = context.wrap_bio(self.incoming, self.outgoing, server_side=True)
    self.fragments: dict[int, tuple[int, int, bytearray]] = {}
    self.bytes_sent = 0
    self.bytes_received = 0
    self.ignored_counts: dict[tuple[str, int, int], int] = {}

  @staticmethod
  def _timeout(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
      raise ValueError("Message timeout must be finite and positive")
    return value

  def event(self, name: str, **values) -> None:
    if self._log is not None:
      self._log(name, **values)

  def ignored(self, name: str, channel: int, kind: int, data: bytes, **values) -> None:
    """Log a message this side does not act on, rate-limited per (event, channel, kind)."""
    key = (name, channel, kind)
    count = self.ignored_counts[key] = self.ignored_counts.get(key, 0) + 1
    if count <= IGNORED_LOG_FIRST or count % IGNORED_LOG_EVERY == 0:
      self.event(name, channel=channel, kind=kind, count=count, bytes=len(data), message=describe(data), **values)

  # ----------------------------------------------------------------- framing

  def send(self, channel: int, kind: int, body: bytes = b"", encrypted: bool = True, control: bool = False, *,
           deadline: float | None = None) -> int:
    """Send one whole message within one deadline and return its fragment count; the session is invalid on failure."""
    if self.authenticated and not encrypted:
      raise ValueError("Plaintext is forbidden after authentication")
    data = struct.pack(">H", kind) + body
    if len(data) > MAX_MESSAGE:
      raise ValueError("Message exceeds size limit")
    message_deadline = time.monotonic() + self.send_timeout
    deadline = message_deadline if deadline is None else min(deadline, message_deadline)
    timed_peer = hasattr(self.peer, "gettimeout") and hasattr(self.peer, "settimeout")
    previous_timeout = self.peer.gettimeout() if timed_peer else None
    try:
      for offset in range(0, len(data), FRAGMENT_SIZE):
        chunk = data[offset:offset + FRAGMENT_SIZE]
        flags = (FLAG_FIRST if offset == 0 else 0) | (FLAG_LAST if offset + len(chunk) == len(data) else 0)
        flags |= (FLAG_ENCRYPTED if encrypted else 0) | (FLAG_CONTROL if control else 0)
        if encrypted:
          written = self.tls.write(chunk)
          if written != len(chunk):
            raise ValueError("Incomplete TLS write")
          chunk = self.outgoing.read()
        header = struct.pack(">BBH", channel, flags, len(chunk))
        if flags & 3 == FLAG_FIRST:
          header += struct.pack(">I", len(data))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise TimeoutError("Android Auto message send deadline exceeded")
        if timed_peer:
          self.peer.settimeout(remaining)
        self.peer.sendall(header + chunk)
        self.bytes_sent += len(header) + len(chunk)
        if time.monotonic() >= deadline:
          raise TimeoutError("Android Auto message send deadline exceeded")
    finally:
      if timed_peer:
        self.peer.settimeout(previous_timeout)
    return -(-len(data) // FRAGMENT_SIZE)

  def receive(self, *, deadline: float | None = None, patient: bool = False) -> tuple[int, int, bytes]:
    """Read one complete message within one deadline, tolerating partial reads."""
    message_deadline = time.monotonic() + (self.receive_timeout if self.authenticated else self.handshake_timeout)
    deadline = message_deadline if deadline is None else deadline if patient else min(deadline, message_deadline)
    timed_peer = hasattr(self.peer, "gettimeout") and hasattr(self.peer, "settimeout")
    previous_timeout = self.peer.gettimeout() if timed_peer else None

    def read_exact(size: int) -> bytes:
      data = bytearray()
      while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise TimeoutError("Android Auto message receive deadline exceeded")
        if timed_peer:
          self.peer.settimeout(remaining)
        chunk = self.peer.recv(size - len(data))
        if not chunk:
          raise EOFError(f"Head unit disconnected after {len(data)}/{size} bytes")
        data.extend(chunk)
      self.bytes_received += size
      return bytes(data)

    try:
      return self._receive_message(read_exact)
    finally:
      if timed_peer:
        self.peer.settimeout(previous_timeout)

  def _receive_message(self, read_exact) -> tuple[int, int, bytes]:
    while True:
      channel, flags, size = struct.unpack(">BBH", read_exact(4))
      if flags & ~15 or size == 0:
        raise ValueError("Invalid frame header")
      if self.authenticated and not flags & FLAG_ENCRYPTED:
        raise ValueError("Plaintext is forbidden after authentication")
      segment = flags & 3
      total = struct.unpack(">I", read_exact(4))[0] if segment == FLAG_FIRST else None
      if total is not None and not 2 <= total <= MAX_MESSAGE:
        raise ValueError("Invalid fragmented message size")
      if total is not None and total + sum(item[0] for item in self.fragments.values()) > MAX_FRAGMENT_BYTES:
        raise ValueError("Aggregate fragmented message limit exceeded")
      payload = read_exact(size)
      if flags & FLAG_ENCRYPTED:
        self.incoming.write(payload)
        plain = bytearray()
        while True:
          try:
            part = self.tls.read(65536)
            if not part:
              raise EOFError("TLS closed")
            plain.extend(part)
          except ssl.SSLWantReadError:
            break
        payload = bytes(plain)
      if segment == 3:
        if channel in self.fragments:
          raise ValueError("Full message interrupted fragment sequence")
      elif segment == FLAG_FIRST:
        if channel in self.fragments or total is None or len(payload) >= total:
          raise ValueError("Invalid first fragment")
        self.fragments[channel] = (total, flags & 12, bytearray(payload))
        continue
      else:
        if channel not in self.fragments:
          raise ValueError("Continuation without first fragment")
        expected, mode, assembled = self.fragments[channel]
        if mode != flags & 12 or len(assembled) + len(payload) > expected:
          raise ValueError("Inconsistent fragment sequence")
        assembled.extend(payload)
        if segment == 0:
          continue
        del self.fragments[channel]
        if len(assembled) != expected:
          raise ValueError("Incorrect assembled message size")
        payload = bytes(assembled)
      if len(payload) < 2:
        raise ValueError("Missing message type")
      kind = struct.unpack(">H", payload[:2])[0]
      return channel, kind, payload[2:]

  # --------------------------------------------------------------- handshake

  def authenticate(self) -> None:
    # Some wireless adapters send a control keepalive before their version request.
    # A pong keeps the transport alive; version, TLS and certificate checks remain mandatory.
    deadline = time.monotonic() + self.handshake_timeout
    pings = 0
    while True:
      channel, kind, data = self.receive(deadline=deadline)
      if channel != 0 or kind != MSG_PING_REQUEST:
        break
      pings += 1
      if pings > 8 or len(data) > 64:
        raise ValueError('Excessive pre-version Android Auto keepalive')
      fields = parse_fields(data)
      timestamps = fields.get(1, [])
      if len(timestamps) > 1 or timestamps and not isinstance(timestamps[0], int):
        raise ValueError('Invalid pre-version Android Auto ping timestamp')
      timestamp = timestamps[0] if timestamps else 0
      self.send(0, MSG_PING_RESPONSE, field(1, timestamp), encrypted=False, deadline=deadline)
      self.event('pre_version_ping', count=pings)
      if time.monotonic() >= deadline:
        raise TimeoutError('Android Auto version request deadline exceeded')
    if channel != 0 or kind != MSG_VERSION_REQUEST or len(data) != 4:
      raise ValueError(f"Expected version request; got {channel}/{kind:#x}")
    major, minor = struct.unpack(">HH", data)
    if major < 1:
      raise ValueError(f"Unsupported Android Auto protocol version {major}.{minor}")
    # Android Auto 17.6 answers any request above 1.7 with its own maximum and success;
    # newer head units (e.g. 2025 Honda, 4.1) hang up on anything else.
    reply = (1, min(minor, 5)) if (major, minor) <= (1, 7) else PHONE_MAX_VERSION
    self.event("version", major=major, minor=minor, reply=f"{reply[0]}.{reply[1]}")
    self.send(0, MSG_VERSION_RESPONSE, struct.pack(">HHH", *reply, 0), encrypted=False)
    while True:
      channel, kind, data = self.receive()
      if channel != 0 or kind != MSG_SSL_HANDSHAKE:
        raise ValueError(f"Expected TLS handshake; got {channel}/{kind:#x}")
      self.incoming.write(data)
      done = False
      try:
        self.tls.do_handshake()
        done = True
      except ssl.SSLWantReadError:
        pass
      except ssl.SSLError as error:
        # e.g. NO_SHARED_CIPHER from an old head-unit TLS stack, or a certificate the root does not verify
        self.event("tls_failed", reason=error.reason or "", library=error.library or "", error=str(error)[:200])
        raise
      response = self.outgoing.read()
      if response:
        self.send(0, MSG_SSL_HANDSHAKE, response, encrypted=False)
      if done:
        break
    cipher = self.tls.cipher()
    assert cipher is not None
    self.event("tls_established", version=self.tls.version(), cipher=cipher[0])
    if self.peer_verification_enabled:
      peer_cert = self.tls.getpeercert() or {}
      organizations = [value for rdn in peer_cert.get("subject", ()) for name, value in rdn if name == "organizationName"]
      if any(name in ("CarService", "Google Automotive Link") for name in organizations):
        raise ValueError("Head unit presented a phone or CA identity")
      self.head_unit_subject = ", ".join(f"{name}={value}" for rdn in peer_cert.get("subject", ()) for name, value in rdn)
      self.event("head_unit_verified", subject=self.head_unit_subject, expires=peer_cert.get("notAfter", ""))
    channel, kind, data = self.receive()
    # Some head units send a trailing TLS record before the status; feed it to the engine.
    for _ in range(4):
      if channel != 0 or kind != MSG_SSL_HANDSHAKE:
        break
      self.event("late_tls_record", bytes=len(data), head=data[:24].hex())
      self.incoming.write(data)
      try:
        self.tls.read(1)
      except ssl.SSLWantReadError:
        pass
      channel, kind, data = self.receive()
    if channel != 0 or kind != MSG_AUTH_COMPLETE:
      raise ValueError(f"Expected authentication status; got {channel}/{kind:#x} ({len(data)} bytes {data[:24].hex()})")
    status = signed(one(parse_fields(data), 1))
    if status is None or not isinstance(status, int):
      raise ValueError("Missing or invalid authentication status")
    if status != 0:
      self.event("authentication_rejected", status=status)
      raise AuthenticationRejected(f"Head unit rejected the phone certificate (status {status})")
    self.authenticated = True
    self.event("authenticated", version=self.tls.version(), cipher=cipher[0])

  def discover(self, device_name: str, device_brand: str) -> list[dict]:
    self.send(0, MSG_SERVICE_DISCOVERY_REQUEST, field(4, device_name) + field(5, device_brand))
    raw = self.wait_for(0, MSG_SERVICE_DISCOVERY_RESPONSE, timeout=self.DISCOVERY_TIMEOUT, patient=True)
    response = parse_fields(raw)
    channels = []
    for descriptor in response.get(1, []):
      if not isinstance(descriptor, bytes):
        continue
      fields = parse_fields(descriptor)
      item: dict = {"id": one(fields, 1), "services": sorted(number for number in fields if number != 1),
                    "descriptor": describe_fields(fields)}
      av = one(fields, 3)
      if isinstance(av, bytes):
        media = parse_fields(av)
        item["media_type"] = one(media, 1)
        item["display_id"], item["display_type"] = one(media, 6), one(media, 7)
        item["video_configs"] = [parse_fields(c) for c in media.get(4, []) if isinstance(c, bytes)]
        if one(media, 2) is not None:
          item["audio_type"] = one(media, 2)
          item["audio_configs"] = [parse_fields(c) for c in media.get(3, []) if isinstance(c, bytes)]
      if isinstance(one(fields, NAVIGATION_SERVICE), bytes):
        try:
          item["navigation"] = parse_navigation(one(fields, NAVIGATION_SERVICE))
        except ValueError:
          item["navigation"] = {}
      if isinstance(one(fields, 4), bytes):
        item["input"] = True
        try:
          item["display_id"] = one(parse_fields(one(fields, 4)), 5)  # the display this input belongs to
        except ValueError:
          pass
        try:
          item["input_config"] = parse_input_config(one(fields, 4))
        except ValueError:
          item["input_config"] = InputConfig()
      if isinstance(one(fields, SENSOR_SERVICE), bytes):
        try:
          item["sensors"] = [one(parse_fields(s), 1) for s in parse_fields(one(fields, SENSOR_SERVICE)).get(1, []) if isinstance(s, bytes)]
        except ValueError:
          item["sensors"] = []
      if isinstance(one(fields, BLUETOOTH_SERVICE), bytes):
        try:
          item["bluetooth"] = parse_bluetooth(one(fields, BLUETOOTH_SERVICE))
        except ValueError:
          item["bluetooth"] = {}
      channels.append(item)
    # Everything but the channel list: make, model, year, software and, on newer units, headunit_info.
    head_unit = redact_head_unit(describe_fields({number: values for number, values in response.items() if number != 1}))
    self.event("discovered", channels=[{k: (json_fields_list(v) if k in ("video_configs", "audio_configs") else str(v) if k == "input_config" else v)
                                        for k, v in ch.items()} for ch in channels],
               head_unit=head_unit)
    return channels

  def wait_for(self, channel: int, kind: int, timeout: float = 10.0, patient: bool = False) -> bytes:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      ch, message, data = self.receive(deadline=deadline, patient=patient)
      if ch == channel and message == kind:
        return data
      self.dispatch(ch, message, data, expected=f"{channel}/{kind:#x}")
    raise TimeoutError(f"Head unit did not send {channel}/{kind:#x}")

  def dispatch(self, channel: int, kind: int, data: bytes, expected: str) -> None:
    """Handle a message that arrived while waiting for another one."""
    if channel == 0 and kind == MSG_PING_REQUEST:
      self.send(0, MSG_PING_RESPONSE, field(1, one(parse_fields(data), 1, 0)))
    elif not (channel == 0 and kind == MSG_PING_RESPONSE):
      self.ignored("unexpected_while_waiting", channel, kind, data, expected=expected)


SDR_VEHICLE_ID = 5          # ServiceDiscoveryResponse.vehicle_id
HEAD_UNIT_INFO = 17         # ServiceDiscoveryResponse.headunit_info
HEAD_UNIT_INFO_VEHICLE_ID = 4


def packed_varints(value) -> list[int]:
  """A repeated enum: packed bytes on newer units, one varint per field on older ones."""
  if isinstance(value, int):
    return [value]
  out, pos = [], 0
  while pos < len(value):
    number, shift = 0, 0
    while True:
      if pos >= len(value) or shift > 63:
        raise ValueError("Truncated packed varint")
      byte = value[pos]
      pos += 1
      number |= (byte & 127) << shift
      shift += 7
      if byte < 128:
        break
    out.append(number)
  return out


def parse_bluetooth(data: bytes) -> dict:
  fields = parse_fields(data)
  address = one(fields, 1)
  methods = [m for value in fields.get(2, []) for m in packed_varints(value)]
  return {"car_address": address.decode(errors="replace") if isinstance(address, bytes) else "", "pairing_methods": methods}


def redact_head_unit(described: dict) -> dict:
  """Drop the vehicle identifier from a described discovery response: logs are shared for diagnosis."""
  if SDR_VEHICLE_ID in described:
    described[SDR_VEHICLE_ID] = ["redacted"]
  for info in described.get(HEAD_UNIT_INFO, []):
    if isinstance(info, dict) and HEAD_UNIT_INFO_VEHICLE_ID in info:
      info[HEAD_UNIT_INFO_VEHICLE_ID] = ["redacted"]
  return described


def json_fields_list(configs: list[dict]) -> list[dict]:
  return [json_fields(config) for config in configs]


class ProjectionSession(Session):
  """Video projection with focus epochs, bounded ACK window and mandatory resume keyframes."""

  # Wi-Fi jitter, plus bursts where this loop itself stalls: the dashcam encoders and the model starting at onroad
  # held it ~1 s on 2026-10-01 while the car kept acking in 27-219 ms. Stays under the 3 s after which a Honda takes
  # its screen back without video.
  ACK_TIMEOUT = 2.5
  ACK_DRAIN_SECONDS = 0.1  # before calling an ACK late, read what already arrived, for at most this long
  MAX_WINDOW = 2
  nav = None
  # HeadunitPad acknowledges every fragment (codec config included) rather than every frame.
  fragment_acks = False

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.frame_fragments: deque[int] = deque()
    self.fragment_credit = 0
    self.channels: list[dict] = []
    self.mode: VideoMode | None = None
    self.window = 1
    self.focused = False
    self.allow_projection = True
    self.media_started = False
    self.needs_keyframe = True
    self.session_id = 1
    self.retired_sessions: deque[int] = deque(maxlen=8)
    self.pending: deque[float] = deque()
    self.unacked = 0
    self.acked = 0
    self.frames_sent = 0
    self.focus_epoch = 0
    self.max_ack_seconds = 0.0
    self.config_ack_slack = 0  # codec-config messages a head unit may acknowledge like frames
    self.epoch_acked = 0
    self.video_confirmed = False
    self.video_confirmed_at = 0.0
    self.input_channel: int | None = None
    self.max_window = self.MAX_WINDOW  # raised by the supervisor for adapters that need the car's full window
    self.allow_hevc = False  # the owner's Allow H.265 video setting; used only when the car offers no H.264 mode
    self.phone_services = False  # open the car-data and Bluetooth channels like a phone (adapter profile)
    self.phone_frames = False  # send picture NAL units only (adapter profile)
    self.phone_address = ""  # this comma's Bluetooth address, for the pairing request
    self.sensor_channel: int | None = None
    self.sensor_events: dict[int, int] = {}
    self.bluetooth_channel: int | None = None
    self.nav = None  # NavStatus, attached by the supervisor when the head unit offers turn-by-turn
    self.audio = None  # MediaAudio, attached by the supervisor when Spotify plays through Android Auto
    self.input_events = 0
    self.touch: TouchMapper | None = None
    self.touch_events: deque[TouchEvent] = deque(maxlen=128)
    self.keys = KeyTracker()
    self.media_control: Callable[[str], None] | None = None  # non-blocking; set by the supervisor
    self.last_ping_sent = 0.0
    self.last_rx = time.monotonic()
    self.ping_responses = 0

  # ------------------------------------------------------------------- setup

  def start(self, device_name: str, device_brand: str) -> VideoMode:
    self.channels = self.discover(device_name, device_brand)
    self.mode = choose_video_mode(self.channels, self.allow_hevc)
    self.open_video()
    self.open_input()
    if self.phone_services:
      self.open_phone_services()
    self.request_projection()
    return self.mode

  def open_video(self) -> None:
    mode = self.mode
    assert mode is not None
    self.send(mode.channel, MSG_CHANNEL_OPEN_REQUEST, field(1, 0) + field(2, mode.channel), control=True)
    opened = parse_fields(self.wait_for(mode.channel, MSG_CHANNEL_OPEN_RESPONSE))
    if signed(one(opened, 1)) != 0:
      raise ValueError(f"Head unit rejected opening the video channel: {json_fields(opened)}")
    self.send(mode.channel, AV_SETUP_REQUEST, field(1, mode.codec))
    setup = parse_fields(self.wait_for(mode.channel, AV_SETUP_RESPONSE))
    window = int(one(setup, 2, 0) or 0)
    if one(setup, 1) != SETUP_STATUS_READY or window < 1:  # the window is capped to MAX_WINDOW below; MIB3 offers 100
      raise ValueError(f"Unsupported video setup response: {json_fields(setup)}")
    configs = setup.get(3, [])
    if configs and mode.config_index not in configs:
      raise ValueError(f"Head unit did not accept video configuration {mode.config_index}: {json_fields(setup)}")
    self.window = min(window, self.max_window)
    self.event("video_setup", mode=mode.as_dict(), window=window, used_window=self.window)

  def open_input(self) -> None:
    """Open the touch/key channel so input is acknowledged; failures are not fatal."""
    channel = next((ch for ch in self.channels if ch.get("input")), None)
    if channel is None:
      return
    try:
      self.send(channel["id"], MSG_CHANNEL_OPEN_REQUEST, field(1, 0) + field(2, channel["id"]), control=True)
      opened = parse_fields(self.wait_for(channel["id"], MSG_CHANNEL_OPEN_RESPONSE, timeout=3.0))
      if signed(one(opened, 1)) != 0:
        raise ValueError("channel open rejected")
      self.input_channel = channel["id"]
      config = channel.get("input_config") or InputConfig()
      # Echo the advertised keycodes, as a phone does; touch needs no binding.
      self.send(channel["id"], INPUT_BINDING_REQUEST, b"".join(field(1, code) for code in config.keycodes))
      if self.mode is not None:
        self.touch = TouchMapper(config, self.mode.width, self.mode.height, self.mode.margin_width, self.mode.margin_height,
                                 content_left=self.mode.content_left, content_top=self.mode.content_top)
      self.event("input_opened", channel=channel["id"], keycodes=list(config.keycodes),
                 touch=f"{config.touch_width}x{config.touch_height}")
    except (TimeoutError, ValueError) as error:
      self.event("input_unavailable", error=str(error))

  def _open_optional(self, channel: dict, name: str) -> bool:
    try:
      self.send(channel["id"], MSG_CHANNEL_OPEN_REQUEST, field(1, 0) + field(2, channel["id"]), control=True)
      opened = parse_fields(self.wait_for(channel["id"], MSG_CHANNEL_OPEN_RESPONSE, timeout=3.0))
      if signed(one(opened, 1)) != 0:
        raise ValueError(f"channel open rejected: {signed(one(opened, 1))}")
      return True
    except (TimeoutError, ValueError) as error:
      self.event(f"{name}_unavailable", channel=channel["id"], error=str(error))
      return False

  def open_phone_services(self) -> None:
    """Open the car-data and Bluetooth channels before asking for the screen, as a phone does. Failures are not fatal."""
    sensors = next((ch for ch in self.channels if "sensors" in ch), None)
    if sensors is not None and self._open_optional(sensors, "sensors"):
      self.sensor_channel = sensors["id"]
      # Only what decides whether the screen may project; location would put the car's position in the session log.
      wanted = [kind for kind in (SENSOR_DRIVING_STATUS, SENSOR_NIGHT) if kind in sensors["sensors"]]
      for kind in wanted:
        self.send(sensors["id"], SENSOR_START_REQUEST, field(1, kind) + field(2, 0))
      self.event("sensors_opened", channel=sensors["id"], requested=wanted)
    bluetooth = next((ch for ch in self.channels if "bluetooth" in ch), None)
    if bluetooth is not None and self._open_optional(bluetooth, "bluetooth"):
      self.bluetooth_channel = bluetooth["id"]
      methods = bluetooth["bluetooth"].get("pairing_methods") or []
      if self.phone_address:
        self.send(bluetooth["id"], BLUETOOTH_PAIRING_REQUEST, field(1, self.phone_address) + field(2, methods[0] if methods else 2))
      self.event("bluetooth_opened", channel=bluetooth["id"], phone_address=self.phone_address,
                 car_address=bluetooth["bluetooth"].get("car_address", ""), pairing_methods=methods)

  def request_projection(self) -> None:
    """Ask for display focus; the Mazda donor needed this, DHU grants it unsolicited."""
    assert self.mode is not None
    self.allow_projection = True
    self.send(self.mode.channel, VIDEO_FOCUS_REQUEST, field(2, FOCUS_PROJECTED) + field(3, FOCUS_REASON_USER_SELECTION))

  def request_native(self) -> bool:
    """Return the display to the head unit without ending this AA connection."""
    if self.mode is None or not self.focused:
      return False
    self.send(self.mode.channel, VIDEO_FOCUS_REQUEST, field(2, FOCUS_NATIVE) + field(3, FOCUS_REASON_LAUNCH_NATIVE))
    self.event("native_focus_requested")
    return True

  def dispatch(self, channel: int, kind: int, data: bytes, expected: str) -> None:
    # Once video is set up, an early focus grant or ping must not be lost while
    # waiting for the input channel.
    if self.mode is not None and not (channel == 0 and kind in (MSG_VERSION_REQUEST, MSG_SSL_HANDSHAKE)):
      self.handle(channel, kind, data)
    else:
      super().dispatch(channel, kind, data, expected)

  # --------------------------------------------------------------- streaming

  def pump(self, timeout: float) -> bool:
    """Handle at most one incoming message; returns True when one was handled."""
    now = time.monotonic()
    if self.authenticated and now - self.last_ping_sent >= PING_INTERVAL:
      self.last_ping_sent = now
      self.send(0, MSG_PING_REQUEST, field(1, time.monotonic_ns() // 1000))
    if not select.select([self.peer], [], [], max(0.0, timeout))[0]:
      return False
    self.handle(*self.receive())
    self.last_rx = time.monotonic()
    return True

  def handle(self, channel: int, kind: int, data: bytes) -> None:
    try:
      fields = parse_fields(data) if kind not in (AV_MEDIA_WITH_TIMESTAMP, 1) else {}
    except ValueError:
      fields = {}  # non-protobuf payload on a channel this sender never opened
    mode = self.mode
    if self.audio is not None and self.audio.handle(channel, kind, fields):
      return
    if self.nav is not None and self.nav.handle(channel, kind, fields):
      return
    if channel == 0:
      if kind == MSG_PING_REQUEST:
        self.send(0, MSG_PING_RESPONSE, field(1, one(fields, 1, 0)))
      elif kind == MSG_PING_RESPONSE:
        self.ping_responses += 1
      elif kind == MSG_SHUTDOWN_REQUEST:
        self.send(0, MSG_SHUTDOWN_RESPONSE)
        self.event("peer_requested_shutdown", reason=one(fields, 1))
        raise PeerRequestedStop(f"Head unit ended projection (reason {one(fields, 1)})")
      else:
        self.ignored("control_ignored", channel, kind, data)
    elif mode is not None and channel == mode.channel:
      if kind == VIDEO_FOCUS_INDICATION:
        self._handle_focus(one(fields, 1), one(fields, 2, 0))
      elif kind == AV_MEDIA_ACK:
        self._handle_ack(one(fields, 1), int(one(fields, 2, 0) or 0))
      else:
        self.ignored("video_ignored", channel, kind, data)
    elif channel == self.input_channel:
      if kind == INPUT_EVENT:
        self.input_events += 1
        if self.touch is not None and self.focused:
          try:
            self.touch_events.extend(self.touch.decode(data))
          except ValueError as error:
            self.event("input_invalid", error=str(error))
        # The driver may be on the car's own screen while Android Auto plays the music.
        try:
          for action in self.keys.decode(data):
            self.event("media_key", action=action)
            if self.media_control is not None:
              self.media_control(action)
        except ValueError as error:
          self.event("input_invalid", error=str(error))
      elif kind == INPUT_BINDING_RESPONSE:
        self.event("input_bound", status=signed(one(fields, 1, 0)))
      else:
        self.ignored("input_ignored", channel, kind, data)
    elif channel is not None and channel == self.sensor_channel:
      if kind == SENSOR_START_RESPONSE:
        self.event("sensor_started", status=signed(one(fields, 1, 0)))
      elif kind == SENSOR_EVENT:
        for number, values in fields.items():
          first = number not in self.sensor_events
          self.sensor_events[number] = self.sensor_events.get(number, 0) + len(values)
          if first:
            self.event("sensor_event", field=number, data=describe_fields(fields))
      else:
        self.ignored("sensor_ignored", channel, kind, data)
    elif channel is not None and channel == self.bluetooth_channel:
      if kind == BLUETOOTH_PAIRING_RESPONSE:
        self.event("bluetooth_pairing_response", already_paired=one(fields, 1), status=signed(one(fields, 2, 0)))
      elif kind == BLUETOOTH_AUTH_DATA:
        self.event("bluetooth_auth_data", data=describe_fields(fields))
      else:
        self.ignored("bluetooth_ignored", channel, kind, data)
    else:
      self.ignored("channel_ignored", channel, kind, data)

  def _handle_focus(self, focus, unsolicited) -> None:
    was_focused = self.focused
    granted = focus in (FOCUS_PROJECTED, FOCUS_PROJECTED_NO_INPUT)
    self.focused = granted and self.allow_projection
    self.event("video_focus", focus=focus, unsolicited=unsolicited, focused=self.focused)
    if was_focused and not self.focused and self.touch is not None:
      self.touch_events.extend(self.touch.reset())
    if self.focused and not was_focused:
      assert self.mode is not None
      if self.media_started:
        # A new media epoch: retire in-flight frames from the previous one so
        # late ACKs are harmless, and never continue an old inter-frame chain.
        self.retired_sessions.append(self.session_id)
        self.session_id += 1
        self.unacked = 0
        self.pending.clear()
        if self.fragment_acks:
          self.frame_fragments.clear()
          self.fragment_credit = 0
        self.config_ack_slack = 0
        self.epoch_acked = 0
      self.media_started = True
      self.needs_keyframe = True
      self.focus_epoch += 1
      self.send(self.mode.channel, AV_START_INDICATION, field(1, self.session_id) + field(2, self.mode.config_index))

  def _handle_ack(self, sid, count: int) -> None:
    if sid in self.retired_sessions:
      return
    if self.fragment_acks:
      if sid != self.session_id:
        raise ValueError(f"Invalid video acknowledgement session={sid} count={count} pending={self.unacked}")
      self.fragment_credit += count
      frames = 0
      while self.frame_fragments and self.fragment_credit >= self.frame_fragments[0]:
        self.fragment_credit -= self.frame_fragments.popleft()
        frames += 1
      if self.fragment_credit and not self.frame_fragments:
        raise ValueError(f"Invalid video acknowledgement session={sid} count={count} pending={self.unacked}")
      if not frames:
        return
      count = frames
    # Head units may acknowledge codec config like a frame: under the current
    # session (as an extra count) or, like the DHU, under session 0.
    if sid != self.session_id and 0 < count <= self.config_ack_slack:
      self.config_ack_slack -= count
      self._confirm_video()
      return
    excess = count - self.unacked
    if sid == self.session_id and 0 < excess <= self.config_ack_slack:
      self.config_ack_slack -= excess
      count -= excess
      if count == 0:
        self._confirm_video()
        return
    if sid != self.session_id or not 0 < count <= self.unacked or count > len(self.pending):
      raise ValueError(f"Invalid video acknowledgement session={sid} count={count} pending={self.unacked}")
    now = time.monotonic()
    for _ in range(count):
      self.max_ack_seconds = max(self.max_ack_seconds, now - self.pending.popleft())
    self.unacked -= count
    self.acked += count
    self.epoch_acked += count
    self._confirm_video()

  def _confirm_video(self) -> None:
    # Same-session ACKs do not distinguish configuration from frames. Until the
    # optional configuration ACKs are accounted for, use the conservative lower
    # bound on acknowledged frames without changing flow-control credit.
    if not self.video_confirmed and self.epoch_acked > self.config_ack_slack:
      self.video_confirmed = True
      self.video_confirmed_at = time.monotonic()
      self.event("video_acknowledged", session=self.session_id, ack_ms=round(self.max_ack_seconds * 1000))

  # ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: Receiver ACK flow control bounds in-flight video; extra buffering increases latency.
  # Altering this can degrade video smoothness or introduce lag on Android Auto.
  # Do not change it autonomously; require an explicit user request specifically for this video optimization.
  def can_send(self) -> bool:
    return self.focused and self.unacked < self.window

  def _ack_overdue(self, now: float) -> bool:
    return bool(self.focused and self.pending and now - self.pending[0] > self.ACK_TIMEOUT)

  def check_progress(self, now: float | None = None) -> None:
    measured = now is None
    now = time.monotonic() if measured else now
    if self._ack_overdue(now):
      # ACKs that arrived while this loop was busy (a slow encode, a CPU burst) still sit in the socket; the
      # streaming loop drains only a bounded batch per pass, so read what is there before calling the car silent.
      deadline = time.monotonic() + self.ACK_DRAIN_SECONDS
      while time.monotonic() < deadline and self.pump(0.0):
        pass
      if measured:
        now = time.monotonic()
      if self._ack_overdue(now):
        raise TimeoutError(f"Video acknowledgement older than {self.ACK_TIMEOUT:.1f} s")
    # Only a receiver known to answer pings can be declared dead by silence; for
    # others a lost link surfaces as a socket error or a dropped Wi-Fi lease.
    if self.ping_responses and now - self.last_rx > 15.0:
      raise TimeoutError("Head unit stopped answering for 15 s")

  def send_frame(self, data: bytes, timestamp_us: int, *, keyframe: bool) -> None:
    if not self.can_send():
      raise RuntimeError("Cannot send without video focus and window credit")
    if self.needs_keyframe and not keyframe:
      raise ValueError("A fresh media epoch must start with SPS/PPS and an IDR frame")
    assert self.mode is not None
    fragments = 0
    config = b""
    if self.needs_keyframe:
      config = codec_config_hevc(data) if self.mode.codec == CODEC_H265 else codec_config(data)
      if config:
        fragments += self.send(self.mode.channel, AV_MEDIA_CODEC_CONFIG, config)
        if not self.fragment_acks:
          self.config_ack_slack += 1
    if self.phone_frames:
      data = (wire_frame_hevc if self.mode.codec == CODEC_H265 else wire_frame)(data, drop_config=bool(config))
    fragments += self.send(self.mode.channel, AV_MEDIA_WITH_TIMESTAMP, struct.pack(">Q", timestamp_us) + data)
    if self.fragment_acks:
      self.frame_fragments.append(fragments)
    self.needs_keyframe = False
    self.pending.append(time.monotonic())
    self.unacked += 1
    self.frames_sent += 1

  def shutdown(self, timeout: float = 2.0) -> None:
    self.send(0, MSG_SHUTDOWN_REQUEST, field(1, SHUTDOWN_REASON_USER_SELECTION))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      if not select.select([self.peer], [], [], 0.05)[0]:
        continue
      channel, kind, data = self.receive()
      if channel == 0 and kind == MSG_SHUTDOWN_RESPONSE:
        self.event("shutdown_acknowledged")
        return
      if channel == 0 and kind == MSG_SHUTDOWN_REQUEST:
        self.send(0, MSG_SHUTDOWN_RESPONSE)
        return
      if self.mode is not None and channel == self.mode.channel and kind in (VIDEO_FOCUS_INDICATION, AV_MEDIA_ACK):
        continue
      if channel == 0 and kind == MSG_PING_REQUEST:
        self.send(0, MSG_PING_RESPONSE, field(1, one(parse_fields(data), 1, 0)))
    raise TimeoutError("Head unit did not acknowledge shutdown")

  def stats(self) -> dict:
    return {"frames_sent": self.frames_sent, "frames_acked": self.acked, "pending": self.unacked,
            "focus_epoch": self.focus_epoch, "focused": self.focused, "max_ack_ms": round(self.max_ack_seconds * 1000),
            "input_events": self.input_events, "touch": self.touch is not None,
            "sensor_events": sum(self.sensor_events.values())}
