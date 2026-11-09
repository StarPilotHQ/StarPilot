from unittest.mock import Mock
import json
import socket
import ssl
import struct
import threading
import time
from pathlib import Path

import pytest

from openpilot.starpilot.system.starport import bootstrap as bs
from openpilot.starpilot.system.starport import hfp, sdp
from openpilot.starpilot.system.starport.frame_source import FrameConsumer, FrameProducer, FrameRequest, fit_content
from openpilot.starpilot.system.starport.session import (AuthenticationRejected, ProjectionSession, START_CODE, VideoMode, choose_video_mode,
                                                              wire_frame)
from openpilot.starpilot.system.starport.tests.fake_head_unit import (BLUETOOTH_CHANNEL, SENSOR_CHANNEL, FakeHeadUnit, make_identity,
                                                                     rfcomm_head_unit)
from openpilot.starpilot.system.starport.wire import field, one, parse_fields, signed


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("identity"))


# ---------------------------------------------------------------------- wire

def test_wire_roundtrip_and_negative_ints():
  data = field(1, 5) + field(2, "abc") + field(3, -1)
  fields = parse_fields(data)
  assert one(fields, 1) == 5 and one(fields, 2) == b"abc" and signed(one(fields, 3)) == -1


def test_wire_rejects_truncation():
  with pytest.raises(ValueError):
    parse_fields(field(2, "abcdef")[:-2])


# ----------------------------------------------------------------------- sdp

def sdp_response(attributes: bytes, continuation: bytes = b"") -> bytes:
  params = struct.pack(">H", len(attributes)) + attributes + bytes([len(continuation)]) + continuation
  return struct.pack(">BHH", sdp.PDU_SEARCH_ATTRIBUTE_RESPONSE, 0, len(params)) + params


PROTOCOLS = b"\x35\x20\x09\x00\x04\x35\x0c\x35\x03\x19\x01\x00\x35\x05\x19\x00\x03\x08\x08"  # L2CAP + RFCOMM ch 8
AA_RECORD = PROTOCOLS + b"\x09\x00\x01\x35\x11\x1c" + sdp.AA_WIRELESS_UUID.bytes


class FakeSdpSocket:
  def __init__(self, responses):
    self.responses = list(responses)
    self.requests = []

  def settimeout(self, _):
    pass

  def send(self, data):
    self.requests.append(data)

  def recv(self, _):
    return self.responses.pop(0)


class ClosableSdpSocket(FakeSdpSocket):
  def __enter__(self):
    return self

  def __exit__(self, *_):
    pass

  def shutdown(self, *_):
    pass

  def close(self):
    pass


def test_sdp_request_matches_phone_shape():
  request = sdp.build_request(b"")
  assert request[0] == sdp.PDU_SEARCH_ATTRIBUTE_REQUEST and request[1:3] == b"\x00\x00"
  assert sdp.AA_WIRELESS_UUID.bytes in request and b"\x03\xf0" in request and request.endswith(b"\x00")


def test_sdp_follows_continuation_and_finds_channel():
  first, second = AA_RECORD[:10], AA_RECORD[10:]
  sock = FakeSdpSocket([sdp_response(first, b"\x01\x02"), sdp_response(second)])
  assert sdp.query_channel(sock) == 8
  assert sock.requests[1].endswith(b"\x02\x01\x02")


def test_sdp_missing_service_and_error_response():
  with pytest.raises(sdp.SdpError, match="does not advertise"):
    sdp.query_channel(FakeSdpSocket([sdp_response(b"\x35\x00")]))
  error = struct.pack(">BHHH", sdp.PDU_ERROR_RESPONSE, 0, 2, 3)
  with pytest.raises(sdp.SdpError, match="invalid request syntax"):
    sdp.query_channel(FakeSdpSocket([error]))


def test_sdp_channel_encodings():
  assert sdp.rfcomm_channel(b"\x19\x00\x03\x09\x00\x0c") == 12
  assert sdp.rfcomm_channel(b"\x19\x00\x03\x08\x00" + b"\x19\x00\x03\x08\x05") == 5
  assert sdp.rfcomm_channel(b"\x19\x01\x00") is None


# ----------------------------------------------------------------- bootstrap

def unexpected_join(_):
  raise AssertionError("Unexpected join")


def run_bootstrap(start_request_delay=5.0, initial_kick_delay=bs.INITIAL_KICK_SECONDS, experiment="", **hu_options):
  phone, car = socket.socketpair()
  joined = []
  result_holder = {}

  def car_side():
    try:
      result_holder["seen"] = rfcomm_head_unit(car, ("192.168.50.1", 5288), **hu_options)
    except BaseException as error:
      result_holder["error"] = error

  thread = threading.Thread(target=car_side, daemon=True)
  thread.start()
  events = []
  boot = bs.WirelessBootstrap(phone, lambda name, **values: events.append((name, values)), stage_timeout=5.0,
                              start_request_delay=start_request_delay, initial_kick_delay=initial_kick_delay, experiment=experiment)

  def join(credentials):
    time.sleep(0.3)  # the car pings while we join
    joined.append(credentials)

  result = boot.run(join)
  thread.join(5)
  phone.close()
  car.close()
  assert "error" not in result_holder, result_holder.get("error")
  return result, joined, result_holder["seen"], events


def test_bootstrap_standard_flow():
  result, joined, seen, events = run_bootstrap()
  assert (result.endpoint.ip, result.endpoint.port) == ("192.168.50.1", 5288)
  creds = joined[0]
  assert (creds.ssid, creds.key, creds.bssid, creds.security) == ("HondaAA", "secret-key", "AA:BB:CC:DD:EE:FF", 8)
  assert signed(one(seen["start_response"], 3)) == 0 and 1 not in seen["start_response"]
  assert signed(one(seen["connect_status"], 1)) == 0
  assert all("secret-key" not in repr(values) for _, values in events)


def test_bootstrap_version_first_and_fragmented():
  result, joined, seen, _ = run_bootstrap(version_first=True, byte_by_byte=True)
  assert result.version == (1, 3) and result.head_unit.get("car_make") == "Honda"
  assert one(seen["version_response"], 1) == 1 and signed(one(seen["version_response"], 4)) == 0
  assert joined[0].ssid == "HondaAA"


def test_bootstrap_asks_car_to_start_projection():
  result, joined, seen, events = run_bootstrap(start_request_delay=0.2, version_first=True, wait_for_phone_start=True)
  assert seen["phone_start_request"] == b"" and joined[0].ssid == "HondaAA"
  assert ("bootstrap_tx", {"message": "WifiStartRequest", "bytes": 0}) in events


def test_bootstrap_takes_endpoint_from_start_response():
  # 2025 Honda (session 25): version exchange, phone asks, car answers with a StartResponse.
  result, joined, seen, events = run_bootstrap(start_request_delay=0.1, version_first=True, wait_for_phone_start=True,
                                               answer_start=True)
  assert (result.endpoint.ip, result.endpoint.port) == ("192.168.50.1", 5288) and joined[0].ssid == "HondaAA"
  assert seen["messages"].count(bs.WIFI_START_REQUEST) == 1
  assert not any(name == "bootstrap_ignored" for name, _ in events)


def test_bootstrap_waits_out_a_busy_start_response():
  # The car says "not ready" (status alone), then sends the endpoint on its own.
  result, joined, _, events = run_bootstrap(start_request_delay=0.1, version_first=True, wait_for_phone_start=True,
                                            answer_start=True, busy_replies=1, busy_follow_up=True)
  assert result.endpoint.port == 5288 and joined[0].ssid == "HondaAA"
  assert ("bootstrap_start_refused", {"status": -1, "endpoint": False, "hex": "18ffffffffffffffffff01"}) in events


def test_bootstrap_asks_again_after_a_busy_start_response():
  result, joined, seen, _ = run_bootstrap(start_request_delay=0.1, version_first=True, wait_for_phone_start=True,
                                          answer_start=True, busy_replies=2)
  assert result.endpoint.port == 5288 and joined[0].ssid == "HondaAA"
  assert seen["messages"].count(bs.WIFI_START_REQUEST) == 3


def test_bootstrap_start_response_without_endpoint_is_not_an_endpoint(monkeypatch):
  now = [0.0]
  monkeypatch.setattr(bs.time, "monotonic", lambda: now[0])
  boot = bs.WirelessBootstrap(None, lambda *a, **k: None, stage_timeout=1, start_request_delay=0.3)
  sent = []
  monkeypatch.setattr(boot, "send", lambda message, payload=b"": sent.append(message))
  replies = [(bs.WIFI_START_RESPONSE, field(3, 0)), (bs.WIFI_START_RESPONSE, field(1, "10.0.0.1") + field(2, 5288) + field(3, -3))]

  def receive(timeout):
    now[0] += 0.2
    if replies:
      return replies.pop(0)
    raise bs.BootstrapTimeout("wifi_start", "head unit did not answer in time")

  monkeypatch.setattr(boot, "next_frame", receive)
  with pytest.raises(bs.BootstrapTimeout):
    boot.run(unexpected_join)
  assert bs.WIFI_START_REQUEST in sent, "a refused start is asked again"


def test_bootstrap_silent_peer_receives_one_prompt():
  _, joined, seen, events = run_bootstrap(initial_kick_delay=0.05, wait_for_phone_start=True, pings=False)
  assert joined[0].ssid == "HondaAA"
  assert seen["messages"].count(bs.WIFI_START_REQUEST) == 1
  assert sum(name == "bootstrap_initial_kick" for name, _ in events) == 1


def test_bootstrap_version_prompt_does_not_get_an_extra_initial_prompt():
  _, _, seen, events = run_bootstrap(initial_kick_delay=0.05, start_request_delay=0.1,
                                     version_first=True, wait_for_phone_start=True)
  assert seen["messages"].count(bs.WIFI_START_REQUEST) == 1
  assert not any(name == "bootstrap_initial_kick" for name, _ in events)


@pytest.mark.parametrize("stage", ["wifi_start", "wifi_info"])
def test_bootstrap_stage_deadline_survives_continuous_pings(monkeypatch, stage):
  now = [0.0]
  monkeypatch.setattr(bs.time, "monotonic", lambda: now[0])
  boot = bs.WirelessBootstrap(None, lambda *a, **k: None, stage_timeout=1)
  sent = []
  monkeypatch.setattr(boot, "send", lambda message, payload=b"": sent.append(message))
  first = [True]

  def receive(timeout):
    now[0] += 0.2
    if first[0] and stage == "wifi_info":
      first[0] = False
      return bs.WIFI_START_REQUEST, field(1, "10.0.0.1") + field(2, 5288)
    return bs.WIFI_PING_REQUEST, b""

  monkeypatch.setattr(boot, "next_frame", receive)
  with pytest.raises(bs.BootstrapTimeout) as error:
    boot.run(unexpected_join)
  assert error.value.stage == stage and now[0] < 1.5
  assert bs.WIFI_PING_RESPONSE in sent and bs.WIFI_START_REQUEST not in sent


def test_bootstrap_hint_cannot_hide_disconnect():
  phone, car = socket.socketpair()
  try:
    car.sendall(bs.encode_frame(bs.WIFI_SETUP_INFO, field(4, field(1, "10.0.0.1") + field(2, 5288))))
    car.close()
    with pytest.raises(bs.BootstrapError, match="closed"):
      bs.WirelessBootstrap(phone, lambda *a, **k: None).run(unexpected_join)
  finally:
    phone.close()
    car.close()


def test_bootstrap_hint_grace_is_not_extended_by_pings(monkeypatch):
  now = [0.0]
  monkeypatch.setattr(bs.time, "monotonic", lambda: now[0])
  boot = bs.WirelessBootstrap(None, lambda *a, **k: None, stage_timeout=10)
  monkeypatch.setattr(boot, "send", lambda *a: None)
  payload = field(4, field(1, "10.0.0.1") + field(2, 5288)) + field(5, field(1, "test") + field(3, "secret-key") + field(4, 8))

  def receive(timeout):
    now[0] += 0.25
    return (bs.WIFI_SETUP_INFO, payload) if now[0] == 0.25 else (bs.WIFI_PING_REQUEST, b"")

  monkeypatch.setattr(boot, "next_frame", receive)
  joined = []
  result = boot.run(joined.append)
  assert result.endpoint.ip == "10.0.0.1" and joined[0].ssid == "test"
  assert 3 <= now[0] < 5


def test_bootstrap_detects_alternate_info_layout():
  _, joined, _, _ = run_bootstrap(oaa_layout=True)
  assert joined[0].key == "secret-key" and joined[0].bssid == "AA:BB:CC:DD:EE:FF"


def test_bootstrap_setup_info_without_start_request():
  result, joined, seen, _ = run_bootstrap(setup_info_only=True, pings=False)
  assert result.endpoint.port == 5288 and joined[0].key == "secret-key"
  assert bs.WIFI_INFO_REQUEST not in seen["messages"]


def test_bootstrap_join_failure_reports_status():
  phone, car = socket.socketpair()
  seen = {}

  def car_side():
    try:
      seen.update(rfcomm_head_unit(car, ("10.0.0.1", 5000), pings=False))
    except BaseException as error:
      seen["error"] = error

  thread = threading.Thread(target=car_side, daemon=True)
  thread.start()

  def join(_):
    raise RuntimeError("wrong key")

  with pytest.raises(bs.BootstrapError) as error:
    bs.WirelessBootstrap(phone, lambda *a, **k: None, stage_timeout=5.0).run(join)
  assert error.value.stage == "joining_wifi"
  thread.join(5)
  phone.close()
  car.close()
  assert signed(one(seen["connect_status"], 1)) == bs.STATUS_NETWORK_UNAVAILABLE


def test_bootstrap_timeout_names_stage():
  phone, car = socket.socketpair()
  with pytest.raises(bs.BootstrapError) as error:
    bs.WirelessBootstrap(phone, lambda *a, **k: None, stage_timeout=0.3).run(lambda _: None)
  assert error.value.stage == "wifi_start"
  car.close()
  phone.close()


def test_frame_reader_bounds():
  reader = bs.FrameReader()
  assert reader.feed(bs.encode_frame(1, b"ab")[:3]) == []
  assert reader.feed(bs.encode_frame(1, b"ab")[3:] + bs.encode_frame(8)) == [(1, b"ab"), (8, b"")]
  with pytest.raises(ValueError):
    reader.feed(struct.pack(">HH", 60000, 1))


# ----------------------------------------------------------------------- hfp

def test_hfp_service_level_connection():
  assert hfp.respond("AT+BRSF=767") == ["+BRSF: 995", "OK"]  # a phone's feature set, not "nothing"
  assert hfp.respond("AT+BAC=1,2") == ["OK"]
  assert hfp.respond("AT+BCC") == ["ERROR"]  # never start call audio we cannot carry
  assert hfp.respond("AT+BIA=1,1,1,1,1,1,1,0") == ["OK"]
  assert hfp.respond("AT+CIND=?")[0].startswith("+CIND: (\"call\"")
  assert hfp.respond("AT+CIND?") == ["+CIND: 0,0,1,5,0,5,0", "OK"]
  assert hfp.respond("AT+CMER=3,0,0,1") == ["OK"]
  assert hfp.respond("ATD5551234;") == ["ERROR"]
  assert hfp.respond("AT+UNKNOWN") == ["ERROR"]


def test_hfp_serve_over_socket():
  a, b = socket.socketpair()
  stop = threading.Event()
  thread = threading.Thread(target=hfp.serve, args=(a, stop, lambda *x, **y: None), daemon=True)
  thread.start()
  b.sendall(b"AT+BRSF=0\rAT+CIND=?\r")
  time.sleep(0.2)
  data = b.recv(4096)
  assert b"+BRSF: 995" in data and data.count(b"OK") == 2
  b.close()
  thread.join(2)
  stop.set()


# -------------------------------------------------------------- frame source

def test_fit_content_letterboxes_compact_ui():
  x, y, w, h = fit_content(536, 240, 1280, 720, 0, 240)
  assert (w, h) == (1072, 480) and x == 104 and y == 120
  assert fit_content(536, 240, 800, 480, 0, 0) == (0, 60, 800, 358)


def test_frame_handoff(tmp_path):
  path = str(tmp_path / "frames")
  consumer = FrameConsumer(path)
  producer = FrameProducer(path)
  request = FrameRequest(64, 32, 0, 0, 100_000)
  consumer.configure(request)
  assert producer.pending_request() is None  # no demand yet
  consumer.demand(1.0)
  producer._next_open_check = 0
  pending = producer.pending_request()
  assert pending == request
  now_ns = time.monotonic_ns()
  assert producer.due(pending, now_ns)
  producer.publish(pending, bytes([7]) * (64 * 32 * 4), now_ns)
  assert not producer.due(pending, now_ns + 10_000_000)  # paced to 10 fps
  frame = consumer.latest()
  assert frame is not None and frame.data[:4] == b"\x07" * 4 and frame.captured_ns == now_ns
  assert consumer.latest() is None  # nothing newer
  consumer.release_demand()
  assert producer.pending_request() is None
  consumer.close()


def test_frame_consumer_rejects_torn_write(tmp_path):
  path = str(tmp_path / "frames")
  consumer = FrameConsumer(path)
  consumer.configure(FrameRequest(16, 16, 0, 0, 50_000))
  consumer.demand()
  producer = FrameProducer(path)
  request = producer.pending_request()
  assert request is not None
  producer.publish(request, bytes(16 * 16 * 4), time.monotonic_ns())
  struct.pack_into("<Q", consumer.mm, 8, 5)  # writer mid-update
  assert consumer.latest() is None
  consumer.close()


# ------------------------------------------------------------------- session

def test_choose_video_mode_prefers_720p():
  channels = [{"id": 3, "video_configs": [parse_fields(field(1, 1)), parse_fields(field(1, 2) + field(4, 240) + field(5, 160)),
                                          parse_fields(field(1, 3))]}]
  assert choose_video_mode(channels) == VideoMode(3, 1, 1280, 720, 30, 0, 240, density=160)
  with pytest.raises(ValueError):
    choose_video_mode([{"id": 3, "video_configs": [parse_fields(field(1, 2) + field(10, 7))]}])  # H.265 only


def keyframe_au(tag: int) -> bytes:
  return b"\x00\x00\x00\x01\x09\xf0" + b"\x00\x00\x00\x01\x67\x42" + b"\x00\x00\x00\x01\x68\xce" + b"\x00\x00\x00\x01\x65" + bytes([tag])


def delta_au(tag: int) -> bytes:
  return b"\x00\x00\x00\x01\x09\xf0" + b"\x00\x00\x00\x01\x41" + bytes([tag])


def connect(hu: FakeHeadUnit, identity, verify=True) -> ProjectionSession:
  sock = socket.create_connection(("127.0.0.1", hu.port), timeout=5)
  return ProjectionSession(sock, str(identity["phone_cert"]), str(identity["phone_key"]), None,
                           str(identity["root"]) if verify else None)


def pump_until(session, predicate, timeout=5.0):
  deadline = time.monotonic() + timeout
  while not predicate():
    assert time.monotonic() < deadline, "condition not reached"
    session.pump(0.05)


def test_head_unit_tls_encode_drain_and_frame_share_send_lock():
  hu = FakeHeadUnit.__new__(FakeHeadUnit)
  hu._send_lock = threading.Lock()
  pending = bytearray()
  packets = []

  def write(data):
    assert hu._send_lock.locked()
    assert not pending
    pending.extend(data)
    return len(data)

  def read():
    assert hu._send_lock.locked()
    data = bytes(pending)
    pending.clear()
    return data

  def sendall(data):
    assert hu._send_lock.locked()
    packets.append(data)

  hu.tls = Mock(write=Mock(side_effect=write))
  hu.outgoing = Mock(read=Mock(side_effect=read))
  hu.sock = Mock(sendall=Mock(side_effect=sendall))
  start = threading.Barrier(3)
  failures = []

  def send(kind, body):
    try:
      start.wait(timeout=5)
      hu._send(3, kind, body)
    except BaseException as error:
      failures.append(error)

  messages = ((0x8004, field(1, 1) + field(2, 1)), (0x8008, field(1, 2) + field(2, 1)))
  threads = [threading.Thread(target=send, args=message) for message in messages]
  for thread in threads:
    thread.start()
  start.wait(timeout=5)
  for thread in threads:
    thread.join(timeout=5)
    assert not thread.is_alive()
  assert not failures
  decoded = []
  for packet in packets:
    channel, flags, size = struct.unpack(">BBH", packet[:4])
    assert channel == 3 and flags == 11 and size == len(packet) - 4 and size > 2
    decoded.append((struct.unpack(">H", packet[4:6])[0], packet[6:]))
  assert sorted(decoded) == sorted(messages)
  assert not pending


def test_session_end_to_end_with_focus_epochs(identity):
  hu = FakeHeadUnit(identity)
  session = connect(hu, identity)
  session.authenticate()
  assert "Honda" in session.head_unit_subject
  mode = session.start("StarPilot", "comma.ai")
  assert (mode.width, mode.height, mode.margin_height) == (1280, 720, 240)
  pump_until(session, lambda: session.focused)
  with pytest.raises(ValueError):
    session.send_frame(delta_au(1), 1, keyframe=False)  # a new epoch must start with a keyframe
  session.send_frame(keyframe_au(1), 1, keyframe=True)
  for i in range(2, 6):
    pump_until(session, session.can_send)
    session.send_frame(delta_au(i), i, keyframe=False)
  pump_until(session, lambda: session.acked == 5)

  hu.set_focus(False)  # driver switched to the car's own screen
  pump_until(session, lambda: not session.focused)
  hu.set_focus(True)
  pump_until(session, lambda: session.focused)
  assert session.needs_keyframe and session.session_id == 2
  session.send_frame(keyframe_au(9), 9, keyframe=True)
  pump_until(session, lambda: session.acked == 6)
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None, hu.error
  assert hu.device_name == "StarPilot" and hu.start_indications == [1, 2]
  assert [sid for sid, _ in hu.frames] == [1, 1, 1, 1, 1, 2] and hu.shutdown_received.is_set()


def test_session_can_return_to_native_screen_without_disconnect(identity):
  hu = FakeHeadUnit(identity)
  session = connect(hu, identity)
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  assert session.request_native()
  pump_until(session, lambda: not session.focused)
  assert hu.focus_requests[-1] == (2, 2)
  assert not hu.shutdown_received.is_set()
  hu.set_focus(True)
  pump_until(session, lambda: session.focused)
  assert session.needs_keyframe and session.session_id == 2
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None, hu.error


def test_session_accepts_newer_head_unit_protocol(identity):
  hu = FakeHeadUnit(identity, version=(4, 1))  # 2025 Honda Civic head unit
  session = connect(hu, identity)
  session.authenticate()
  mode = session.start("StarPilot", "comma.ai")
  assert hu.version_reply == (6, 1, 0) and mode.width == 1280
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None, hu.error


@pytest.mark.parametrize("ack_codec_config", [True, False, 0])
def test_session_sends_codec_config_before_each_epoch(identity, ack_codec_config):
  hu = FakeHeadUnit(identity, ack_codec_config=ack_codec_config)
  session = connect(hu, identity)
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  session.send_frame(keyframe_au(1), 1, keyframe=True)
  for i in range(2, 5):
    pump_until(session, session.can_send)
    session.send_frame(delta_au(i), i, keyframe=False)
  pump_until(session, lambda: session.acked == 4)
  hu.set_focus(False)
  pump_until(session, lambda: not session.focused)
  hu.set_focus(True)
  pump_until(session, lambda: session.focused)
  session.send_frame(keyframe_au(9), 9, keyframe=True)
  pump_until(session, lambda: session.acked == 5)
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None, hu.error
  sps_pps = b"\x00\x00\x00\x01\x67\x42\x00\x00\x00\x01\x68\xce"
  assert hu.codec_configs == [(1, sps_pps, 0), (2, sps_pps, 4)]  # one per epoch, before its keyframe


def test_wire_frame_drops_aud_and_optionally_config():
  aud, sps, pps, idr = b"\x00\x00\x00\x01\x09\xf0", b"\x00\x00\x00\x01\x67\x42", b"\x00\x00\x00\x01\x68\xce", b"\x00\x00\x00\x01\x65\x88"
  assert wire_frame(aud + sps + pps + idr, drop_config=True) == idr
  assert wire_frame(aud + sps + pps + idr, drop_config=False) == sps + pps + idr
  assert wire_frame(b"junk", drop_config=True) == b"junk"


def test_session_sends_picture_units_only_when_asked(identity):
  hu = FakeHeadUnit(identity)
  session = connect(hu, identity)
  session.phone_frames = True
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  session.send_frame(b"\x00\x00\x00\x01\x09\xf0\x00\x00\x00\x01\x67\x42\x00\x00\x00\x01\x68\xce\x00\x00\x00\x01\x65\x88", 1, keyframe=True)
  pump_until(session, lambda: session.unacked == 0)
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None, hu.error
  for _, frame in hu.frames:
    assert frame and all(frame[m.end()] & 31 not in (7, 8, 9) for m in START_CODE.finditer(frame))


@pytest.mark.parametrize("max_window, offered, used", [(2, 30, 2), (30, 30, 30), (30, 100, 30), (30, 4, 4)])
def test_session_streams_at_the_window_the_profile_allows(identity, max_window, offered, used):
  hu = FakeHeadUnit(identity, window=offered)
  session = connect(hu, identity)
  session.max_window = max_window
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  assert session.window == used
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)


def test_session_opens_car_data_and_bluetooth_like_a_phone(identity):
  hu = FakeHeadUnit(identity, phone_services=True)
  session = connect(hu, identity)
  session.phone_services = True
  session.phone_address = "39:90:21:64:07:00"
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused and session.sensor_events)
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None
  assert {SENSOR_CHANNEL, BLUETOOTH_CHANNEL} <= set(hu.opened)
  assert hu.sensor_requests == [13, 10]  # driving status and night only; never location
  assert hu.pairing_requests == [("39:90:21:64:07:00", 2)]
  assert session.stats()["sensor_events"] == 1


def test_session_leaves_car_data_alone_by_default(identity):
  hu = FakeHeadUnit(identity, phone_services=True)
  session = connect(hu, identity)
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None and SENSOR_CHANNEL not in hu.opened and BLUETOOTH_CHANNEL not in hu.opened and hu.pairing_requests == []


def test_session_projects_without_a_bluetooth_address(identity):
  hu = FakeHeadUnit(identity, phone_services=True)
  session = connect(hu, identity)
  session.phone_services = True
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None and BLUETOOTH_CHANNEL in hu.opened and hu.pairing_requests == []


def test_session_keeps_unsolicited_focus_grant(identity):
  hu = FakeHeadUnit(identity, unsolicited_focus=True)
  session = connect(hu, identity)
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused, timeout=2.0)
  session.send_frame(keyframe_au(1), 1, keyframe=True)
  pump_until(session, lambda: session.acked == 1)
  session.shutdown()
  session.peer.close()
  hu.thread.join(5)
  assert hu.error is None and hu.start_indications == [1]


HONDA_SCREEN_TAKEBACK_S = 3.0  # the Civic gives its screen back after this long without video


def streaming_session(hu: FakeHeadUnit, identity) -> ProjectionSession:
  session = connect(hu, identity)
  session.authenticate()
  session.start("StarPilot", "comma.ai")
  pump_until(session, lambda: session.focused)
  session.send_frame(keyframe_au(1), 1, keyframe=True)
  pump_until(session, lambda: session.acked == 1 and not session.pending)
  # ACKs are counts, not frame ids: read everything the car sent (the codec config's ACK too) so a late one is not
  # credited to the next frame.
  deadline = time.monotonic() + 5
  while session.pump(0.2):
    assert time.monotonic() < deadline, "the car kept talking"
  return session


def sent_frame(hu: FakeHeadUnit, session: ProjectionSession) -> float:
  """Send one frame and wait until the car has it; its ACK, if any, is left unread in the socket."""
  received = len(hu.frames)
  session.send_frame(delta_au(2), 2, keyframe=False)
  deadline = time.monotonic() + 5
  while len(hu.frames) == received:
    assert time.monotonic() < deadline, "the car never received the frame"
    time.sleep(0.01)
  time.sleep(0.1)
  return session.pending[0]


def end_session(session: ProjectionSession, hu: FakeHeadUnit) -> None:
  hu.close()
  session.peer.close()


def test_ack_timeout_leaves_room_for_a_stall_but_beats_the_car_taking_its_screen_back():
  assert 1.5 < ProjectionSession.ACK_TIMEOUT < HONDA_SCREEN_TAKEBACK_S


def test_ack_already_received_during_a_loop_stall_keeps_the_session(identity):
  # 2026-10-01: the dashcam and model starting at onroad stalled the comma's loop while the car kept acking.
  hu = FakeHeadUnit(identity)
  session = streaming_session(hu, identity)
  sent = sent_frame(hu, session)
  session.check_progress(now=sent + ProjectionSession.ACK_TIMEOUT + 0.5)  # the waiting ACK is read, not timed out
  assert session.acked == 2 and not session.pending
  end_session(session, hu)


def test_car_that_stops_acking_times_out_at_the_new_limit(identity):
  hu = FakeHeadUnit(identity)
  session = streaming_session(hu, identity)
  hu.hold_acks.set()
  sent = sent_frame(hu, session)
  session.check_progress(now=sent + 1.6)  # past the old 1.5 s limit: no longer fatal
  with pytest.raises(TimeoutError, match="2.5 s"):
    session.check_progress(now=sent + ProjectionSession.ACK_TIMEOUT + 0.05)
  end_session(session, hu)


def test_session_accepts_a_large_video_window_and_caps_it(identity):
  hu = FakeHeadUnit(identity, window=100)  # VW MIB3 offers 100
  session = streaming_session(hu, identity)
  assert session.window == ProjectionSession.MAX_WINDOW
  end_session(session, hu)


def test_session_authentication_rejected(identity):
  hu = FakeHeadUnit(identity, reject_auth=True)
  session = connect(hu, identity)
  with pytest.raises(AuthenticationRejected):
    session.authenticate()
  session.peer.close()
  hu.close()


def test_session_refuses_untrusted_head_unit(identity, tmp_path):
  other = make_identity(tmp_path / "other")
  hu = FakeHeadUnit({**identity, "hu_cert": other["hu_cert"], "hu_key": other["hu_key"]}, require_client_cert=False)
  session = connect(hu, identity)
  with pytest.raises(ssl.SSLError):
    session.authenticate()
  session.peer.close()
  hu.close()


# ---------------------------------------------------------------- supervisor

class FakeLease:
  def __init__(self, *_):
    self.local_ip = ""
    self.acquired = []
    self.releases = []

  def acquire(self, credentials, cancelled=lambda: False, timeout=40.0):
    self.acquired.append(credentials.ssid)
    self.local_ip = "127.0.0.1"
    return self.local_ip

  def still_connected(self):
    return True

  def release(self, restore=True):
    self.releases.append(restore)


class FakeBluez:
  def __init__(self, log):
    self.acquired = 0
    self.released = 0
    self.class_restored = 0
    self.hfp_registrations = 0
    self.trusted = []
    self.adapter_ready = True
    self.connected = True
    self.paired_devices = [("AA:BB:CC:DD:EE:01", "Civic", True)]

  def acquire(self):
    self.acquired += 1

  def release(self):
    self.released += 1

  def restore_class(self):
    self.class_restored += 1

  def register_hfp(self):
    self.hfp_registrations += 1

  def set_trusted(self, address):
    self.trusted.append(address)

  def close(self):
    pass

  def device(self, address):
    return {"address": address, "paired": True, "connected": self.connected, "name": "Civic", "starport": True,
            "uuids": ["0000111e-0000-1000-8000-00805f9b34fb"]}

  def devices(self):
    return [{**self.device(address), "name": name, "starport": aa} for address, name, aa in self.paired_devices]

  def snapshot(self, address):
    return self.adapter_ready, self.device(address)

  def connect_device(self, address):
    pass

  def disconnect_stray_link(self):
    self.stray_disconnects = getattr(self, "stray_disconnects", 0) + 1
    return 7


def test_supervisor_full_wireless_session(identity, tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import bt_sockets, hw_encoder, identity as identity_store, supervisor as supervisor_module

  class TransportEncoder:
    backend = 'fixture'
    supports_nv12 = False
    last_encode_ms = 0.0

    def __init__(self, width, height):
      self.width, self.height, self.frames = width, height, 0

    def encode_rgba(self, pixels, *, keyframe=False):
      assert len(pixels) == self.width * self.height * 4
      self.frames += 1
      return (keyframe_au(self.frames), True) if keyframe or self.frames == 1 else (delta_au(self.frames), False)

    def close(self): pass

  monkeypatch.setattr(hw_encoder, 'create_encoder',
                      lambda width, height, **_kwargs: (TransportEncoder(width, height), 15))

  data = tmp_path / "aa"
  (data / "identity").mkdir(parents=True)
  for src, name in ((identity["phone_cert"], "phone-cert.pem"), (identity["phone_key"], "phone-key.pem"), (identity["root"], "root-cert.pem")):
    (data / "identity" / name).write_bytes(Path(src).read_bytes())
  (data / "identity" / "phone-key.pem").chmod(0o600)
  monkeypatch.setattr(identity_store, "IDENTITY_DIR", data / "identity")
  monkeypatch.setattr(identity_store, "CONFIG_PATH", data / "config.json")
  monkeypatch.setattr(identity_store, "LOG_DIR", data / "logs")
  monkeypatch.setattr(supervisor_module, "SDP_SETTLE", (0.01,))

  hu = FakeHeadUnit(identity)
  rfcomm_seen = {}

  def fake_l2cap(address, psm, timeout=10.0):
    return ClosableSdpSocket([sdp_response(AA_RECORD)])

  def fake_rfcomm(address, channel, timeout=15.0):
    assert channel == 8
    phone, car = socket.socketpair()
    def car_side():
      with car:
        rfcomm_seen.update(rfcomm_head_unit(car, ("127.0.0.1", hu.port), pings=False))
        time.sleep(1.0)
    threading.Thread(target=car_side, daemon=True).start()
    return phone

  monkeypatch.setattr(bt_sockets, "connect_l2cap", fake_l2cap)
  monkeypatch.setattr(bt_sockets, "connect_rfcomm", fake_rfcomm)

  frame_path = str(tmp_path / "frames")
  sup = supervisor_module.Supervisor(bluez_factory=FakeBluez, lease_factory=FakeLease,
                                     bluetooth_client=type("C", (), {"status": lambda s: type("St", (), {"selected_audio": ""})()})(),
                                     frame_path=frame_path)
  sup.config["dedicated_wifi_interface"] = False  # this session uses the fake wlan0 lease
  sup.select_receiver("AA:BB:CC:DD:EE:01", "Civic")
  lease_holder = {}
  original_lease = sup._lease
  sup._lease = Mock(side_effect=lambda: lease_holder.setdefault("lease", original_lease()))

  stop_producer = threading.Event()

  def producer_loop():
    producer = FrameProducer(frame_path)
    while not stop_producer.is_set():
      producer._next_open_check = 0
      request = producer.pending_request()
      now_ns = time.monotonic_ns()
      if request is not None and producer.due(request, now_ns):
        producer.publish(request, bytes([now_ns % 251]) * (request.width * request.height * 4), now_ns)
      time.sleep(0.01)

  threading.Thread(target=producer_loop, daemon=True).start()
  try:
    sup.start()
    deadline = time.monotonic() + 20
    while len(hu.frames) < 5:
      status = sup.status()
      assert time.monotonic() < deadline, (repr(status), repr(hu.error))
      time.sleep(0.05)
    status = sup.status()
    assert status["state"] == "streaming" and status["mode"]["width"] == 1280
    sup.stop()
  finally:
    stop_producer.set()
    hu.close()
  hu.thread.join(5)
  assert hu.error is None, hu.error
  assert hu.shutdown_received.is_set()
  lease = lease_holder["lease"]
  assert lease.acquired == ["HondaAA"] and lease.releases[-1] is True
  assert sup.status()["state"] == "idle" and sup.status()["error"] == ""
  assert signed(one(rfcomm_seen["connect_status"], 1)) == 0
  logs = list((data / "logs").glob("session-*.jsonl"))
  assert logs and "secret-key" not in logs[0].read_text()


def make_supervisor(identity, tmp_path, monkeypatch, rfcomm):
  from openpilot.starpilot.system.starport import bt_sockets, identity as identity_store, supervisor as supervisor_module
  data = tmp_path / "aa"
  (data / "identity").mkdir(parents=True)
  for src, name in ((identity["phone_cert"], "phone-cert.pem"), (identity["phone_key"], "phone-key.pem"), (identity["root"], "root-cert.pem")):
    (data / "identity" / name).write_bytes(Path(src).read_bytes())
  (data / "identity" / "phone-key.pem").chmod(0o600)
  monkeypatch.setattr(identity_store, "IDENTITY_DIR", data / "identity")
  monkeypatch.setattr(identity_store, "CONFIG_PATH", data / "config.json")
  monkeypatch.setattr(identity_store, "LOG_DIR", data / "logs")
  monkeypatch.setattr(supervisor_module, "SDP_SETTLE", (0.01,))
  monkeypatch.setattr(supervisor_module, "BACKOFF_SECONDS", (0.2,))
  monkeypatch.setattr(bt_sockets, "connect_l2cap", lambda *a, **k: ClosableSdpSocket([sdp_response(AA_RECORD)] * 4))
  monkeypatch.setattr(bt_sockets, "connect_rfcomm", rfcomm)
  leases = []

  def lease_factory(*args):
    leases.append(FakeLease())
    return leases[-1]

  client = type("C", (), {"status": lambda s: type("St", (), {"selected_audio": ""})()})()
  sup = supervisor_module.Supervisor(bluez_factory=FakeBluez, lease_factory=lease_factory, bluetooth_client=client,
                                     frame_path=str(tmp_path / "frames"))
  sup.config["dedicated_wifi_interface"] = False  # these sessions use the fake wlan0 lease
  sup.select_receiver("AA:BB:CC:DD:EE:01", "Civic")
  return sup, leases


def test_supervisor_retries_after_rejection(identity, tmp_path, monkeypatch):
  cars = []
  threads = []
  failures = []
  stopping = threading.Event()

  def rfcomm(address, channel, timeout=15.0):
    hu = FakeHeadUnit(identity, reject_auth=True)
    cars.append(hu)
    phone, car = socket.socketpair()
    def car_side():
      try:
        with car:
          rfcomm_head_unit(car, ("127.0.0.1", hu.port), pings=False)
          stopping.wait(1.0)
      except EOFError as error:
        if not stopping.is_set():
          failures.append(error)
      except Exception as error:
        failures.append(error)
    thread = threading.Thread(target=car_side, daemon=True)
    threads.append(thread)
    thread.start()
    return phone

  sup, leases = make_supervisor(identity, tmp_path, monkeypatch, rfcomm)
  sup.start()
  deadline = time.monotonic() + 15
  while len(cars) < 2:
    assert time.monotonic() < deadline, sup.status()
    time.sleep(0.05)
  assert "rejected" in sup.status()["error"]
  stopping.set()
  sup.stop()
  for hu in cars:
    hu.close()
  for thread in threads:
    thread.join(timeout=5)
    assert not thread.is_alive(), "RFCOMM fixture did not finish after stop"
  assert not failures, failures
  assert False in leases[0].releases and leases[0].releases[-1] is True  # retries keep the old Wi-Fi parked
  assert sup.status()["state"] == "idle"


def test_supervisor_stop_while_waiting_for_car(identity, tmp_path, monkeypatch):
  held = []

  def rfcomm(address, channel, timeout=15.0):
    phone, car = socket.socketpair()
    held.append(car)  # the car never answers
    return phone

  sup, leases = make_supervisor(identity, tmp_path, monkeypatch, rfcomm)
  sup.start()
  deadline = time.monotonic() + 5
  while sup.status()["state"] != "wifi_start":
    assert time.monotonic() < deadline, sup.status()
    time.sleep(0.02)
  started = time.monotonic()
  sup.stop()
  assert time.monotonic() - started < 5
  assert sup.status()["state"] == "idle" and leases[0].releases[-1] is True
  for car in held:
    car.close()


# ------------------------------------------------------------------- network

class FakeNetworkManager:
  def __init__(self, fail_with_bssid=False, stalled_dhcp=0):
    self.fail_with_bssid = fail_with_bssid
    self.stalled_dhcp = stalled_dhcp
    self.active_device_connection = "/active/home"
    self.states = {}
    self.added = []
    self.calls = []
    self.autoconnect = True

  def call(self, path, interface, member, signature=None, body: tuple=(), timeout=10.0):
    from openpilot.starpilot.system.starport import network
    self.calls.append(member)
    if member == "GetDevices":
      return [["/dev/wlan0"]]
    if member == "AddAndActivateConnection2":
      settings = body[0]
      self.added.append(settings)
      active = f"/active/aa{len(self.added)}"
      locked = "bssid" in settings["802-11-wireless"]
      self.states[active] = network.ACTIVE_STATE_DEACTIVATED if (locked and self.fail_with_bssid) else network.ACTIVE_STATE_ACTIVATED
      if len(self.added) <= self.stalled_dhcp:
        self.states[active] = 1  # activating: the access point never answers DHCP
      self.active_device_connection = active
      return [f"/settings/aa{len(self.added)}", active]
    if member == "GetSettings":
      return [{"connection": {"id": ("s", "home" if path == "/settings/home" else "starpilot-starport")}}]
    if member == "ListConnections":
      return [[]]
    return []

  def get(self, path, interface, name):
    values = {"WirelessEnabled": True, "DeviceType": 2, "Interface": "wlan0", "ActiveConnection": self.active_device_connection,
              "Connection": "/settings/home", "Ip4Config": "/ip4/1", "Autoconnect": self.autoconnect,
              "ActiveAccessPoint": "/ap/1", "Frequency": 5745}
    if name == "State" and interface.endswith("Device"):
      return 70
    if name == "State":
      return self.states.get(path, 4)
    if name == "AddressData":
      return [{"address": ("s", "192.168.50.23")}]
    return values[name]


def make_lease(nm):
  from openpilot.starpilot.system.starport.network import NetworkLease
  lease = NetworkLease(lambda *a, **k: None)
  router = Mock(wraps=type("R", (), {"close": lambda self: None})())

  def reopen(*args, **kwargs):  # the real lease reopens D-Bus lazily on its next call
    lease.router = router

  def call(*args, **kwargs):
    reopen()
    return nm.call(*args, **kwargs)

  def get(*args, **kwargs):
    reopen()
    return nm.get(*args, **kwargs)

  def set_property(path, interface, name, signature, value):
    reopen()
    nm.calls.append(f"Set{name}={value}")
    nm.autoconnect = value

  lease._call, lease._get, lease._set = Mock(side_effect=call), Mock(side_effect=get), Mock(side_effect=set_property)
  lease._delete_stale_profiles = Mock(side_effect=lambda: None)
  return lease


def credentials():
  return bs.WifiCredentials(ssid="HondaAA", key="secret-key", bssid="AA:BB:CC:DD:EE:FF", security=8, ap_type=1)


def test_network_lease_settings_keep_internet_route():
  from openpilot.starpilot.system.starport.network import connection_settings
  settings = connection_settings(credentials(), "wlan0")
  assert settings["ipv4"]["never-default"] == ("b", True) and settings["ipv4"]["ignore-auto-dns"] == ("b", True)
  assert settings["connection"]["autoconnect"] == ("b", False) and settings["802-11-wireless-security"]["psk"] == ("s", "secret-key")
  assert settings["802-11-wireless"]["bssid"] == ("ay", bytes.fromhex("AABBCCDDEEFF"))


def test_network_lease_retries_without_bssid_and_restores_previous():
  nm = FakeNetworkManager(fail_with_bssid=True)
  lease = make_lease(nm)
  assert lease.acquire(credentials(), timeout=2.0) == "192.168.50.23"
  assert "bssid" in nm.added[0]["802-11-wireless"] and "bssid" not in nm.added[1]["802-11-wireless"]
  lease.release(restore=True)
  assert nm.calls[-1] == "ActivateConnection"  # home Wi-Fi comes back


def test_network_lease_waits_for_a_slow_honda_access_point(monkeypatch):
  from openpilot.starpilot.system.starport import network
  lease = network.NetworkLease(lambda *a, **k: None)
  budgets = []

  def attempt(credentials, timeout, cancelled):
    budgets.append((credentials.bssid, timeout))
    raise network.NetworkError("not up yet")

  monkeypatch.setattr(lease, "_acquire_once", attempt)
  monkeypatch.setattr(lease, "_drop_attempt", lambda: None)
  with pytest.raises(network.NetworkError):
    lease.acquire(credentials())
  assert budgets == [("AA:BB:CC:DD:EE:FF", 90.0), ("", 30.0)]


def test_network_lease_joins_aawireless_on_a_static_address_first():
  nm = FakeNetworkManager()
  lease = make_lease(nm)
  lease.acquire(credentials(), timeout=10.0, dhcp_patience=0.3, endpoint_ip=lambda: "10.42.1.1", static_first=True)
  assert len(nm.added) == 1
  assert nm.added[0]["ipv4"]["method"] == ("s", "manual")


def test_network_lease_falls_back_to_a_static_address_when_dhcp_never_answers():
  nm = FakeNetworkManager(stalled_dhcp=1)
  lease = make_lease(nm)
  lease.acquire(credentials(), timeout=10.0, dhcp_patience=0.3, endpoint_ip=lambda: "10.42.1.1")
  assert [settings["ipv4"]["method"][1] for settings in nm.added] == ["auto", "manual"]


def test_network_lease_restarts_a_join_whose_dhcp_goes_unanswered():
  nm = FakeNetworkManager(stalled_dhcp=2)
  lease = make_lease(nm)
  assert lease.acquire(credentials(), timeout=10.0, dhcp_patience=0.3) == "192.168.50.23"
  assert len(nm.added) == 3


def test_network_lease_waits_for_dhcp_without_patience():
  from openpilot.starpilot.system.starport import network
  nm = FakeNetworkManager(stalled_dhcp=2)
  lease = make_lease(nm)
  with pytest.raises(network.JoinTimeout):
    lease.acquire(credentials(), timeout=1.0)
  assert len(nm.added) == 2  # only the SSID-only retry, never a DHCP restart


def test_network_lease_does_not_undo_user_network_change():
  nm = FakeNetworkManager()
  lease = make_lease(nm)
  lease.acquire(credentials(), timeout=2.0)
  nm.active_device_connection = "/active/user-picked"  # user chose another network meanwhile
  lease.release(restore=True)
  assert "ActivateConnection" not in nm.calls


def test_network_lease_session_stop_after_a_failed_attempt_restores_home_wifi(tmp_path):
  nm = FakeNetworkManager()
  lease = make_lease(nm)
  lease.marker = tmp_path / "paused.json"
  lease.acquire(credentials(), timeout=2.0)
  assert "SetAutoconnect=False" in nm.calls  # home Wi-Fi does not rejoin between attempts
  lease.release(restore=False)
  nm.active_device_connection = "/"  # NetworkManager left wlan0 disconnected
  assert lease.router is None and not nm.autoconnect
  assert lease.marker.exists()
  lease.release(restore=True)  # the session's last attempt failed; D-Bus is already closed
  assert nm.calls[-2:] == ["SetAutoconnect=True", "ActivateConnection"] and nm.autoconnect
  assert not lease.marker.exists()
  calls = len(nm.calls)
  lease.release(restore=True)
  assert len(nm.calls) == calls


def test_network_lease_retry_keeps_previous_for_final_stop():
  nm = FakeNetworkManager()
  lease = make_lease(nm)
  lease.acquire(credentials(), timeout=2.0)
  lease.release(restore=False)
  assert "ActivateConnection" not in nm.calls and lease.previous_connection == "/settings/home"
  lease.acquire(credentials(), timeout=2.0)
  lease.release(restore=True)
  assert nm.calls.count("ActivateConnection") == 1


def test_paused_autoconnect_leaves_a_marker_until_restored(tmp_path):
  nm = FakeNetworkManager()
  lease = make_lease(nm)
  lease.marker = tmp_path / "paused.json"
  lease.acquire(credentials(), timeout=2.0)
  assert lease.marker.exists() and json.loads(lease.marker.read_text())["interface"] == "wlan0"
  lease.release(restore=True)
  assert not lease.marker.exists() and nm.autoconnect


def test_next_session_takes_over_a_pause_a_crash_left_behind(tmp_path):
  nm = FakeNetworkManager()
  crashed = make_lease(nm)
  crashed.marker = tmp_path / "paused.json"
  crashed.acquire(credentials(), timeout=2.0)  # the daemon dies here: no release
  assert not nm.autoconnect
  lease = make_lease(nm)
  lease.marker = crashed.marker
  lease.acquire(credentials(), timeout=2.0)
  assert lease.autoconnect_paused
  lease.release(restore=True)
  assert nm.autoconnect and not lease.marker.exists()


def test_recover_autoconnect_after_a_crash(tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import network
  marker = tmp_path / "paused.json"
  log, sets = [], []
  monkeypatch.setattr(network.NetworkLease, "_wifi_devices", lambda self: [("/dev/wlan0", "wlan0")])
  monkeypatch.setattr(network.NetworkLease, "_set", lambda self, *args: sets.append(args))
  assert network.recover_autoconnect(lambda event, **fields: log.append(event), marker)  # nothing to do
  marker.write_text(json.dumps({"interface": "wlan0", "boot": network._boot_id()}))
  assert network.recover_autoconnect(lambda event, **fields: log.append(event), marker)
  assert sets == [("/dev/wlan0", network.NM_DEVICE_IFACE, "Autoconnect", "b", True)] and not marker.exists()
  assert log == ["wifi_autoconnect_recovered"]
  marker.write_text(json.dumps({"interface": "wlan0", "boot": "an earlier boot"}))
  assert network.recover_autoconnect(lambda event, **fields: None, marker)  # a reboot already reset it
  assert len(sets) == 1 and not marker.exists()


def test_recover_autoconnect_keeps_the_marker_when_networkmanager_fails(tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import network
  marker = tmp_path / "paused.json"
  marker.write_text(json.dumps({"interface": "wlan0", "boot": network._boot_id()}))

  def unavailable(self):
    raise network.NetworkError("NetworkManager is not running")
  monkeypatch.setattr(network.NetworkLease, "_wifi_devices", unavailable)
  assert not network.recover_autoconnect(lambda event, **fields: None, marker)
  assert marker.exists()


def test_recover_autoconnect_waits_for_missing_wifi_device(tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import network
  marker = tmp_path / "paused.json"
  marker.write_text(json.dumps({"interface": "wlan0", "boot": network._boot_id()}))
  monkeypatch.setattr(network.NetworkLease, "_wifi_devices", lambda self: [])
  assert not network.recover_autoconnect(lambda event, **fields: None, marker)
  assert marker.exists()


def test_autoconnect_stays_on_when_recovery_marker_cannot_be_written(tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import network
  lease = network.NetworkLease(lambda *args, **kwargs: None)
  lease.device_path = "/dev/wlan0"
  lease.marker = tmp_path / "missing" / "paused.json"
  monkeypatch.setattr(lease, "_get", lambda *args: True)
  monkeypatch.setattr(lease, "_write_marker", lambda: False)
  changes = []
  monkeypatch.setattr(lease, "_set", lambda *args: changes.append(args))
  lease._pause_autoconnect()
  assert not lease.autoconnect_paused and changes == []


def test_supervisor_retries_wifi_recovery_while_idle(monkeypatch):
  from openpilot.starpilot.system.starport import network, supervisor
  results = [False, True]
  calls = []
  monkeypatch.setattr(network, "recover_autoconnect", lambda log: calls.append(1) or results.pop(0))
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup.log, sup._wifi_recover_at = (lambda *a, **k: None), 0.0  # ty: ignore[invalid-assignment]  # test double
  sup._session_alive = lambda: False  # ty: ignore[invalid-assignment]  # test double
  sup.recover_wifi(100)
  sup.recover_wifi(110)  # waits before retrying
  sup.recover_wifi(100 + supervisor.WIFI_RECOVER_RETRY_S)
  sup.recover_wifi(1000)  # done
  assert len(calls) == 2
  sup._wifi_recover_at = 0.0
  sup._session_alive = lambda: True  # ty: ignore[invalid-assignment]  # test double
  sup.recover_wifi(2000)  # never while a session owns wlan0
  assert len(calls) == 2


def test_jetlink_delays_wired_but_does_not_stop_choosing_it(identity, tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import supervisor as supervisor_module
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: None)
  monkeypatch.setattr(supervisor_module, "jetlink_blocker", lambda: "Turn off Jetlink to use wired Android Auto")
  monkeypatch.setattr(supervisor_module, "chestnut_blocker", lambda: "")
  sup.set_connection("wired")
  status = sup.status()
  assert sup.config["connection"] == "wired" and status["wired_blocked"] == "" and "Jetlink" in status["wired_busy"]
  with pytest.raises(RuntimeError, match="Turn off Jetlink"):
    sup.start()


def test_chestnut_still_stops_choosing_wired(identity, tmp_path, monkeypatch):
  from openpilot.starpilot.system.starport import supervisor as supervisor_module
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: None)
  monkeypatch.setattr(supervisor_module, "jetlink_blocker", lambda: "")
  monkeypatch.setattr(supervisor_module, "chestnut_blocker", lambda: "Wired Android Auto is unavailable while Chestnut is connected")
  with pytest.raises(RuntimeError, match="Chestnut"):
    sup.set_connection("wired")


def test_discovery_waits_past_the_message_timeout_for_a_slow_adapter():
  from openpilot.starpilot.system.starport.session import Session
  a, b = socket.socketpair()
  session = Session.__new__(Session)
  session.peer = a
  session.authenticated = False
  session.handshake_timeout = 0.1
  session.fragments = {}
  session.bytes_received = 0
  def send_slowly():
    time.sleep(0.2)
    b.sendall(b"\x00\x03\x00\x02")
    time.sleep(0.2)
    b.sendall(b"\x00\x06")
  sender = threading.Thread(target=send_slowly)
  sender.start()
  try:
    assert session.wait_for(0, 6, timeout=1.0, patient=True) == b""
  finally:
    sender.join()
    a.close()
    b.close()
