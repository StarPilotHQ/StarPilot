"""A scripted Android Auto head unit for tests: the car's side of RFCOMM, TLS and video.

Certificates are generated per test run (a fake "Google Automotive Link" root,
a "CarService" phone leaf and a head-unit leaf), so no real identity is needed.
"""

from __future__ import annotations

import datetime
import socket
import ssl
import struct
import threading
import time
from pathlib import Path

from openpilot.starpilot.system.starport import bootstrap as bs
from openpilot.starpilot.system.starport.wire import field, one, parse_fields


def make_identity(directory: Path) -> dict[str, Path]:
  from cryptography import x509
  from cryptography.hazmat.primitives import hashes, serialization
  from cryptography.hazmat.primitives.asymmetric import rsa
  from cryptography.x509.oid import NameOID

  now = datetime.datetime.now(datetime.UTC)

  def name(org):
    return x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org)])

  def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)

  root_key = key()
  root = (x509.CertificateBuilder().subject_name(name("Google Automotive Link")).issuer_name(name("Google Automotive Link"))
          .public_key(root_key.public_key()).serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=30)).add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
          .sign(root_key, hashes.SHA256()))

  def leaf(org, serial):
    leaf_key = key()
    cert = (x509.CertificateBuilder().subject_name(name(org)).issuer_name(root.subject).public_key(leaf_key.public_key())
            .serial_number(serial).not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
            .sign(root_key, hashes.SHA256()))
    return cert, leaf_key

  paths = {}
  directory.mkdir(parents=True, exist_ok=True)
  for label, (cert, cert_key) in {"phone": leaf("CarService", 2), "hu": leaf("Honda Motor Co", 3)}.items():
    paths[f"{label}_cert"] = directory / f"{label}-cert.pem"
    paths[f"{label}_key"] = directory / f"{label}-key.pem"
    paths[f"{label}_cert"].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    paths[f"{label}_key"].write_bytes(cert_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                              serialization.NoEncryption()))
    paths[f"{label}_key"].chmod(0o600)
  paths["root"] = directory / "root-cert.pem"
  paths["root"].write_bytes(root.public_bytes(serialization.Encoding.PEM))
  return paths


def video_config(resolution: int, margin_w: int = 0, margin_h: int = 0) -> bytes:
  return field(1, resolution) + field(2, 2) + field(3, margin_w) + field(4, margin_h) + field(5, 160)


SENSOR_CHANNEL, BLUETOOTH_CHANNEL = 7, 8


PHONE_STATUS_CHANNEL, VENDOR_CHANNEL = 9, 10
BOLT_IDENTITY = {2: "Chevrolet", 3: "myChevrolet", 4: "2021", 7: "Chevrolet", 8: "myChevrolet", 10: "W28E-V155.3.8-MIHS21A-107"}


def discovery_response(video_channel: int = 3, input_channel: int = 1, *, phone_services: bool = False, bolt: bool = False) -> bytes:
  """``phone_services`` adds the car-data and Bluetooth channels (sensor types one per entry, pairing method packed)."""
  av = field(1, 3) + field(4, video_config(1)) + field(4, video_config(2, 0, 240))
  video = field(1, video_channel) + field(3, av)
  audio = field(1, 4) + field(3, field(1, 1))
  touch = field(1, input_channel) + field(4, field(1, 84) + field(1, 4) + field(2, field(1, 1280) + field(2, 720)))
  extra = b""
  if phone_services:
    sensors = b"".join(field(1, field(1, kind)) for kind in (1, 3, 7, 8, 10, 13, 21))
    extra = (field(1, field(1, SENSOR_CHANNEL) + field(2, sensors)) +
             field(1, field(1, BLUETOOTH_CHANNEL) + field(6, field(1, "F8:36:9B:0A:7D:C8") + field(2, bytes([2])))))
  if bolt:
    sensors = b"".join(field(1, field(1, kind)) for kind in (1, 3, 7, 8, 10, 13, 21))
    bluetooth = field(1, BLUETOOTH_CHANNEL) + field(6, field(1, "F8:36:9B:0A:7D:C8") + field(2, bytes([2])))
    extra += (field(1, field(1, SENSOR_CHANNEL) + field(2, sensors)) + field(1, bluetooth) +
              field(1, field(1, PHONE_STATUS_CHANNEL) + field(10, field(1, 1))) + field(1, field(1, VENDOR_CHANNEL) + field(12, field(1, 1))))
    return field(1, video) + field(1, audio) + field(1, touch) + extra + b"".join(field(n, v) for n, v in BOLT_IDENTITY.items())
  return field(1, video) + field(1, audio) + field(1, touch) + extra + field(2, "Honda") + field(3, "Civic")


class FakeHeadUnit:
  """Car side of one AA TCP session. Runs in a thread; records what the phone sent."""

  def __init__(self, identity: dict[str, Path], *, window: int = 4, reject_auth: bool = False, require_client_cert: bool = True,
               unsolicited_focus: bool = False, version: tuple[int, int] = (1, 7), ack_codec_config: bool | int = True,
               connect_to: tuple[str, int] | None = None, ack_fragments: bool = False,
               phone_services: bool = False, bolt: bool = False, silent_discovery: bool = False, withhold_focus: bool = False,
               grant_focus_after_s: float | None = None, hang_up_after_s: float | None = None,
               hang_up_unless: frozenset[int] = frozenset()):
    self.phone_services = phone_services
    self.bolt = bolt
    self.silent_discovery = silent_discovery  # never answer service discovery
    self.withhold_focus = withhold_focus  # never grant focus, whatever the phone asks
    self.grant_focus_after_s = grant_focus_after_s  # grant it this long after the phone asks (a driver-selected grant)
    self.hang_up_after_s = hang_up_after_s  # send a shutdown request this long after the first video frame ...
    self.hang_up_unless = hang_up_unless  # ... unless the phone has opened all of these channels
    self.discovery_requests: list[bytes] = []
    self.focus_request_bodies: list[bytes] = []
    self.video_stops: list[bytes] = []
    self.messages_seen: dict[tuple[int, int], int] = {}
    self._grant_at: float | None = None
    self._first_frame_at: float | None = None
    self.hung_up = False
    self.sensor_requests: list[int] = []
    self.pairing_requests: list[tuple[str, int]] = []
    self.unsolicited_focus = unsolicited_focus
    self.opened: list[int] = []
    self.connect_to = connect_to
    self.ack_fragments = ack_fragments
    self.fragments = 0
    self.ack_codec_config = ack_codec_config
    self.codec_configs: list[tuple[int | None, bytes, int]] = []  # (session, SPS/PPS, frames received before it)
    self.version = version
    self.version_reply: tuple[int, int, int] | None = None
    self.listener = socket.socket()
    self.listener.bind(("127.0.0.1", 0))
    self.listener.listen(1)
    self.port = self.listener.getsockname()[1]
    self.identity = identity
    self.window = window
    self.reject_auth = reject_auth
    self.require_client_cert = require_client_cert
    self.frames: list[tuple[int, bytes]] = []
    self.start_indications: list[int] = []
    self.focus_requests: list[tuple[int, int]] = []
    self.device_name = ""
    self.shutdown_received = threading.Event()
    self.hold_acks = threading.Event()  # set: receive video frames but stop acknowledging them
    self.streaming = threading.Event()
    self.stop = threading.Event()
    self.error: BaseException | None = None
    self.sock: socket.socket | None = None
    self._send_lock = threading.Lock()
    self.thread = threading.Thread(target=self._run, daemon=True)
    self.thread.start()

  # framing, HU side (TLS client)
  def _send(self, channel: int, kind: int, body: bytes = b"", encrypted: bool = True, control: bool = False) -> None:
    data = struct.pack(">H", kind) + body
    flags = 3 | (8 if encrypted else 0) | (4 if control else 0)
    with self._send_lock:
      if encrypted:
        self.tls.write(data)
        data = self.outgoing.read()
      self.sock.sendall(struct.pack(">BBH", channel, flags, len(data)) + data)

  def _read_exact(self, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
      chunk = self.sock.recv(size - len(data))
      if not chunk:
        raise EOFError("phone closed")
      data.extend(chunk)
    return bytes(data)

  def _receive(self) -> tuple[int, int, bytes]:
    assembled = bytearray()
    self.fragments = 0
    while True:
      self.fragments += 1
      channel, flags, size = struct.unpack(">BBH", self._read_exact(4))
      if flags & 3 == 1:
        self._read_exact(4)
      payload = self._read_exact(size)
      if flags & 8:
        self.incoming.write(payload)
        plain = bytearray()
        while True:
          try:
            plain.extend(self.tls.read(65536))
          except ssl.SSLWantReadError:
            break
        payload = bytes(plain)
      assembled.extend(payload)
      if flags & 2:
        return channel, struct.unpack(">H", assembled[:2])[0], bytes(assembled[2:])

  def _run(self) -> None:
    try:
      if self.connect_to is not None:
        self.sock = self._dial(self.connect_to)
      else:
        self.listener.settimeout(20)
        self.sock, _ = self.listener.accept()
      self.sock.settimeout(20)
      self._session()
    except BaseException as error:  # surfaced to the test
      if not self.stop.is_set():
        self.error = error
    finally:
      if self.sock is not None:
        self.sock.close()
      self.listener.close()

  def _dial(self, address: tuple[str, int]) -> socket.socket:
    deadline = time.monotonic() + 20
    while True:
      try:
        return socket.create_connection(address, timeout=1)
      except OSError:
        if self.stop.is_set() or time.monotonic() > deadline:
          raise
        time.sleep(0.05)

  def _ack(self, session_id: int) -> None:
    for _ in range(self.fragments if self.ack_fragments else 1):
      self._send(3, 0x8004, field(1, session_id) + field(2, 1))

  def _session(self) -> None:
    self._send(0, 1, struct.pack(">HH", *self.version), encrypted=False)
    channel, kind, data = self._receive()
    assert (channel, kind) == (0, 2)
    self.version_reply = struct.unpack(">HHH", data)
    assert self.version_reply[2] == 0
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.load_cert_chain(str(self.identity["hu_cert"]), str(self.identity["hu_key"]))
    context.load_verify_locations(cafile=str(self.identity["root"]))
    context.verify_mode = ssl.CERT_REQUIRED if self.require_client_cert else ssl.CERT_NONE
    self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    self.tls = context.wrap_bio(self.incoming, self.outgoing, server_side=False)
    while True:
      try:
        self.tls.do_handshake()
        done = True
      except ssl.SSLWantReadError:
        done = False
      out = self.outgoing.read()
      if out:
        self._send(0, 3, out, encrypted=False)
      if done:
        break
      channel, kind, data = self._receive()
      assert (channel, kind) == (0, 3)
      self.incoming.write(data)
    self._send(0, 4, field(1, 1 if self.reject_auth else 0), encrypted=False)
    if self.reject_auth:
      return

    session_id = None
    unacked = 0
    focused = False
    while not self.stop.is_set():
      channel, kind, data = self._receive()
      self.messages_seen[(channel, kind)] = self.messages_seen.get((channel, kind), 0) + 1
      now = time.monotonic()
      if self._grant_at is not None and now >= self._grant_at and not focused:
        self._grant_at, focused = None, True
        self._send(3, 0x8008, field(1, 1) + field(2, 1))
      if (self.hang_up_after_s is not None and self._first_frame_at is not None and now - self._first_frame_at >= self.hang_up_after_s
          and not self.hang_up_unless <= set(self.opened)):
        self.hung_up = True
        self._send(0, 15, field(1, 1))  # ByeByeRequest, reason 1 (user selection)
        return
      fields = parse_fields(data) if kind not in (0, 1) else {}
      if channel == 0 and kind == 5:
        self.discovery_requests.append(data)
        self.device_name = one(fields, 4, b"").decode()
        if not self.silent_discovery:
          self._send(0, 6, discovery_response(phone_services=self.phone_services, bolt=self.bolt))
      elif channel == 0 and kind == 11:
        self._send(0, 12, data)
      elif channel == 0 and kind == 15:
        self._send(0, 16)
        self.shutdown_received.set()
        return
      elif kind == 7:
        self.opened.append(channel)
        self._send(channel, 8, field(1, 0), control=True)
      elif (self.phone_services or self.bolt) and channel == SENSOR_CHANNEL and kind == 0x8001:
        self.sensor_requests.append(int(one(fields, 1, 0) or 0))
        self._send(SENSOR_CHANNEL, 0x8002, field(1, 0))
        if one(fields, 1) == 13:
          self._send(SENSOR_CHANNEL, 0x8003, field(13, field(1, 0)))  # driving status: unrestricted
      elif (self.phone_services or self.bolt) and channel == BLUETOOTH_CHANNEL and kind == 0x8001:
        address = one(fields, 1, b"")
        self.pairing_requests.append((address.decode() if isinstance(address, bytes) else "", int(one(fields, 2, 0) or 0)))
        self._send(BLUETOOTH_CHANNEL, 0x8002, field(1, 1) + field(2, 0))  # not paired
      elif channel == 3 and kind == 0x8000:
        self._send(3, 0x8003, field(1, 2) + field(2, self.window) + field(3, 1))
        if self.unsolicited_focus:  # like the DHU: grant focus before the phone asks
          focused = True
          self._send(3, 0x8008, field(1, 1) + field(2, 1))
      elif channel == 3 and kind == 0x8007:
        requested = int(one(fields, 2, 0) or 0)
        reason = int(one(fields, 3, 0) or 0)
        self.focus_requests.append((requested, reason))
        self.focus_request_bodies.append(data)
        if requested == 1 and not focused and self.withhold_focus:
          pass
        elif requested == 1 and not focused and self.grant_focus_after_s is not None:
          self._grant_at = time.monotonic() + self.grant_focus_after_s
        elif requested == 1 and not focused:
          focused = True
          self._send(3, 0x8008, field(1, 1) + field(2, 0))
        elif requested == 2 and focused:
          focused = False
          self._send(3, 0x8008, field(1, 2) + field(2, 0))
      elif channel == 3 and kind == 0x8001:
        session_id = one(fields, 1)
        assert isinstance(session_id, int)
        self.start_indications.append(session_id)
      elif channel == 3 and kind == 1:
        assert isinstance(session_id, int)
        self.codec_configs.append((session_id, data, len(self.frames)))
        if self.ack_codec_config is not False:  # True: current session; an int: that session id (the DHU uses 0)
          ack_session = session_id if self.ack_codec_config is True else self.ack_codec_config
          assert isinstance(ack_session, int)
          self._ack(ack_session)
      elif channel == 3 and kind == 0:
        assert isinstance(session_id, int)
        self.frames.append((session_id, data[8:]))
        if self._first_frame_at is None:
          self._first_frame_at = time.monotonic()
        unacked += 1
        self.streaming.set()
        if not self.hold_acks.is_set():
          self._ack(session_id)
          unacked -= 1
      elif channel == 3 and kind == 0x8002:
        self.video_stops.append(data)
      elif channel == 1 and kind == 0x8002:
        self._send(1, 0x8003, field(1, 0))

  def send_touch(self, action: int, x: int, y: int, pointer: int = 0) -> None:
    location = field(1, x) + field(2, y) + field(3, pointer)
    self._send(1, 0x8001, field(1, 123) + field(3, field(1, location) + field(2, 0) + field(3, action)))

  def set_focus(self, projected: bool) -> None:
    self._send(3, 0x8008, field(1, 1 if projected else 2) + field(2, 1))

  def close(self) -> None:
    self.stop.set()
    for sock in (self.sock, self.listener):
      try:
        if sock is not None:
          sock.close()
      except OSError:
        pass


def rfcomm_head_unit(sock: socket.socket, endpoint: tuple[str, int], *, version_first: bool = False, oaa_layout: bool = False,
                     setup_info_only: bool = False, pings: bool = True, byte_by_byte: bool = False,
                     wait_for_phone_start: bool = False, answer_start: bool = False, busy_replies: int = 0,
                     busy_follow_up: bool = False, require_phone_ping: bool = False) -> dict:
  """Car side of the RFCOMM bootstrap; returns what the phone sent.

  ``answer_start`` models a 2025 Honda: it answers the phone's WifiStartRequest with
  WifiStartResponse(ip, port, status) instead of sending its own request, after
  ``busy_replies`` "not ready" answers (status alone). With ``busy_follow_up`` it sends
  the endpoint unprompted after the busy answer; otherwise it waits to be asked again.
  """
  seen: dict = {"messages": []}
  reader = bs.FrameReader()
  queue: list[tuple[int, bytes]] = []

  def send(message_id, payload=b""):
    frame = bs.encode_frame(message_id, payload)
    if byte_by_byte:
      for byte in frame:
        sock.sendall(bytes([byte]))
    else:
      sock.sendall(frame)

  def receive():
    while not queue:
      data = sock.recv(1024)
      if not data:
        raise EOFError
      queue.extend(reader.feed(data))
    message = queue.pop(0)
    seen["messages"].append(message[0])
    return message

  ip, port = endpoint
  network = field(1, "HondaAA") + field(2, "AA:BB:CC:DD:EE:FF") + field(3, "secret-key") + field(4, 8)
  if setup_info_only:
    send(bs.WIFI_SETUP_INFO, field(1, 1) + field(2, 1) + field(4, field(1, ip) + field(2, port)) + field(5, network))
    send(bs.WIFI_VERSION_REQUEST, field(1, 1) + field(2, 1))
    message_id, payload = receive()
    assert message_id == bs.WIFI_VERSION_RESPONSE
  else:
    if version_first:
      head_unit = field(1, "Honda") + field(2, "Civic") + field(3, "2026")
      send(bs.WIFI_VERSION_REQUEST, field(1, 1) + field(2, 3) + field(5, head_unit))
      message_id, payload = receive()
      assert message_id == bs.WIFI_VERSION_RESPONSE
      seen["version_response"] = parse_fields(payload)
    if wait_for_phone_start:  # 2025 Honda: projection starts only when the phone asks; some peers skip the version
      message_id, payload = receive()
      assert message_id == bs.WIFI_START_REQUEST, message_id
      seen["phone_start_request"] = payload
    if require_phone_ping:  # a dongle that starts only after the phone pings it
      message_id, payload = receive()
      while message_id != bs.WIFI_PING_REQUEST:
        message_id, payload = receive()
      seen["phone_ping_first"] = True
    if pings:
      send(bs.WIFI_PING_REQUEST, field(1, 123))
    if answer_start:
      for _ in range(busy_replies):
        send(bs.WIFI_START_RESPONSE, field(3, -1))
        if not busy_follow_up:
          message_id, _ = receive()
          while message_id == bs.WIFI_PING_RESPONSE:
            message_id, _ = receive()
          assert message_id == bs.WIFI_START_REQUEST, message_id
      send(bs.WIFI_START_RESPONSE, field(1, ip) + field(2, port) + field(3, 0))
    else:
      send(bs.WIFI_START_REQUEST, field(1, ip) + field(2, port))
    message_id, payload = receive()
    while message_id == bs.WIFI_PING_RESPONSE:
      message_id, payload = receive()
    assert message_id == bs.WIFI_INFO_REQUEST, message_id
    if oaa_layout:
      send(bs.WIFI_INFO_RESPONSE, field(1, "HondaAA") + field(2, "AA:BB:CC:DD:EE:FF") + field(3, "secret-key") + field(4, 8) + field(5, 0))
    else:
      send(bs.WIFI_INFO_RESPONSE, field(1, "HondaAA") + field(2, "secret-key") + field(3, "AA:BB:CC:DD:EE:FF") + field(4, 8) + field(5, 1))
  sent_ping = False
  while True:
    message_id, payload = receive()
    if message_id == bs.WIFI_START_RESPONSE:
      seen["start_response"] = parse_fields(payload)
      if pings and not sent_ping:
        sent_ping = True
        send(bs.WIFI_PING_REQUEST, field(1, 456))
    elif message_id == bs.WIFI_CONNECT_STATUS:
      seen["connect_status"] = parse_fields(payload)
      return seen
    elif message_id != bs.WIFI_PING_RESPONSE:
      raise AssertionError(f"unexpected {message_id}")
