"""Wireless-adapter tolerance in the RFCOMM bootstrap, without changing what a built-in head unit sees."""

import threading
import time
from unittest.mock import Mock

import pytest

from openpilot.starpilot.system.starport import bootstrap as bs
from openpilot.starpilot.system.starport.wire import field

ENDPOINT = field(1, "192.168.50.1") + field(2, 5288)
INFO = field(1, "AAWireless") + field(2, "secret-key") + field(3, "AA:BB:CC:DD:EE:FF") + field(4, 8) + field(5, 1)
SETUP_INFO = field(4, ENDPOINT) + field(5, field(1, "AAWireless") + field(3, "secret-key") + field(4, 8))


def scripted(frames, *, ping_interval=bs.JOIN_PING_INTERVAL, experiment=""):
  """A bootstrap whose car sends `frames` in order, then stays silent; records what the comma sent."""
  boot = bs.WirelessBootstrap(None, lambda *a, **k: None, stage_timeout=2, join_ping_interval=ping_interval,
                          experiment=experiment)
  sent = []
  boot.send = Mock(side_effect=lambda message_id, payload=b"": sent.append(message_id))
  queue = list(frames)

  def receive(timeout):
    if queue:
      item = queue.pop(0)
      if isinstance(item, BaseException):
        raise item
      return item
    time.sleep(min(timeout, 0.02))
    raise bs.BootstrapTimeout(boot.stage, "head unit did not answer in time")

  boot.next_frame = Mock(side_effect=receive)
  return boot, sent


def slow_join(seconds, joined):
  def join(credentials):
    time.sleep(seconds)
    joined.append(credentials)
  return join


def test_network_sent_before_the_endpoint_is_kept():
  boot, sent = scripted([(bs.WIFI_INFO_RESPONSE, INFO), (bs.WIFI_START_REQUEST, ENDPOINT)])
  joined = []
  result = boot.run(slow_join(0, joined))
  assert result.endpoint.port == 5288 and joined[0].ssid == "AAWireless"
  assert bs.WIFI_INFO_REQUEST not in sent
  assert sent[-2:] == [bs.WIFI_START_RESPONSE, bs.WIFI_CONNECT_STATUS]


def test_setup_info_answers_the_network_request():
  boot, sent = scripted([(bs.WIFI_START_REQUEST, ENDPOINT), (bs.WIFI_SETUP_INFO, SETUP_INFO)])
  joined = []
  boot.run(slow_join(0, joined))
  assert bs.WIFI_INFO_REQUEST in sent and joined[0].ssid == "AAWireless" and joined[0].key == "secret-key"


def test_no_join_pings_to_a_receiver_that_never_pings():
  # Built-in head units (2025 Civic) see exactly what they saw before: no unsolicited pings.
  boot, sent = scripted([(bs.WIFI_START_REQUEST, ENDPOINT), (bs.WIFI_INFO_RESPONSE, INFO)], ping_interval=0.05)
  boot.run(slow_join(0.4, []))
  assert bs.WIFI_PING_REQUEST not in sent


def test_join_pings_to_a_receiver_that_pings():
  boot, sent = scripted([(bs.WIFI_PING_REQUEST, field(1, 1)), (bs.WIFI_START_REQUEST, ENDPOINT),
                         (bs.WIFI_INFO_RESPONSE, INFO)], ping_interval=0.05)
  boot.run(slow_join(0.4, []))
  assert sent.count(bs.WIFI_PING_REQUEST) >= 3
  assert sent[-1] == bs.WIFI_CONNECT_STATUS


def test_a_failed_handshake_stops_the_join_before_returning():
  boot, sent = scripted([(bs.WIFI_START_REQUEST, ENDPOINT), (bs.WIFI_INFO_RESPONSE, INFO),
                         bs.BootstrapError("joining_wifi", "head unit closed the RFCOMM connection")])
  join_started, join_stopped = threading.Event(), threading.Event()

  def join(credentials):
    join_started.set()
    while not boot.join_is_cancelled():
      time.sleep(0.01)
    join_stopped.set()
    raise RuntimeError("cancelled")

  with pytest.raises(bs.BootstrapError, match="closed"):
    boot.run(join)
  assert join_started.is_set() and join_stopped.is_set(), "the join worker must not outlive the failed handshake"
  assert bs.WIFI_CONNECT_STATUS not in sent


def test_stop_during_the_join_is_not_reported_as_connected():
  stop = threading.Event()
  boot, sent = scripted([(bs.WIFI_START_REQUEST, ENDPOINT), (bs.WIFI_INFO_RESPONSE, INFO)])

  def join(credentials):
    stop.set()  # the user stops just as the join completes

  with pytest.raises(bs.BootstrapError, match="cancelled"):
    boot.run(join, cancelled=stop.is_set)
  assert bs.WIFI_CONNECT_STATUS not in sent


VERSION_F6 = (field(1, 1) + field(2, 7) + field(4, 5180) + field(5, field(1, "Link") + field(5, "CarLetter")) +
              field(6, field(1, "192.168.43.158") + field(2, 40707)))


def test_version_endpoint_in_field_six_is_read_only_when_asked():
  assert bs.parse_version_request(VERSION_F6)[2] is None
  assert bs.parse_version_request(VERSION_F6, (6,))[2] == bs.Endpoint("192.168.43.158", 40707)


def test_start_request_is_answered_early_only_by_the_experiment():
  for experiment, early in (("", False), ("start_response_first", True), ("all", True)):
    boot, sent = scripted([(bs.WIFI_START_REQUEST, ENDPOINT), (bs.WIFI_INFO_RESPONSE, INFO)], experiment=experiment)
    boot.run(slow_join(0, []))
    assert (sent[:2] == [bs.WIFI_START_RESPONSE, bs.WIFI_INFO_REQUEST]) is early


def test_experiment_logs_frames_but_never_credentials():
  events = []
  boot = bs.WirelessBootstrap(None, lambda name, **v: events.append((name, v)), experiment="all")
  boot.sock = Mock()
  boot.send(bs.WIFI_START_RESPONSE, b"\x18\x00")
  boot.queue = [(bs.WIFI_INFO_RESPONSE, INFO), (bs.WIFI_START_REQUEST, ENDPOINT)]
  boot.next_frame(1)
  boot.next_frame(1)
  dumped = [v for name, v in events if name == "bootstrap_frame_hex"]
  assert [d["message"] for d in dumped] == ["WifiStartResponse", "WifiStartRequest"]
  assert INFO.hex() not in str(events)
  assert bs.WirelessBootstrap(None, lambda *a, **k: events.append(a), experiment="bogus").experiment == ""


def test_a_car_that_keeps_refusing_gets_more_time_but_not_forever():
  refusal = field(3, 1)
  boot, sent = scripted([(bs.WIFI_START_RESPONSE, refusal)] * 3 + [(bs.WIFI_START_REQUEST, ENDPOINT), (bs.WIFI_INFO_RESPONSE, INFO)])
  boot.stage_timeout = 0.2
  boot.start_request_delay = 0.01
  assert boot.run(slow_join(0, [])).endpoint.port == 5288
  silent, _ = scripted([])
  silent.stage_timeout = 0.2
  idle, late = silent.next_frame, [True]

  def refuse_late(timeout):
    if late:
      late.clear()
      time.sleep(0.15)  # just before the original deadline
      return bs.WIFI_START_RESPONSE, refusal
    return idle(timeout)

  silent.next_frame = refuse_late
  started = time.monotonic()
  with pytest.raises(bs.BootstrapTimeout):
    silent.run(slow_join(0, []))
  elapsed = time.monotonic() - started
  assert 0.3 < elapsed < 0.2 * bs.REFUSAL_PATIENCE + 0.5


KIA_V41 = field(1, 4) + field(2, 1) + field(3, 0) + field(4, 5745) + field(5, field(1, "KIA") + field(5, "MOBIS"))
DHU_V42 = field(1, 4) + field(2, 2) + field(3, 2) + field(5, field(1, "Google") + field(6, "Desktop Head Unit"))
FORD_V16 = field(1, 1) + field(2, 6) + field(3, 0) + field(4, 5180) + field(4, 5745) + field(5, field(1, "Ford"))


def version_reply(request, modern=True):
  boot, _ = scripted([])
  boot.modern_reply = modern
  sent = []
  boot.send = Mock(side_effect=lambda message_id, payload=b"": sent.append(payload))
  boot.next_frame = Mock(side_effect=[(bs.WIFI_VERSION_REQUEST, request), bs.BootstrapError("wifi_start", "stop")])
  with pytest.raises(bs.BootstrapError):
    boot.run(slow_join(0, []))
  return bs.parse_fields(sent[0])


def test_version_four_head_units_get_a_phone_shaped_version_reply():
  kia, dhu = version_reply(KIA_V41), version_reply(DHU_V42)
  assert kia[1] == [4] and kia[2] == [1] and kia[4] == [1] and kia[5] == [bs.CHANNEL_5GHZ]
  assert dhu[4] == [1] and dhu[5] == [bs.CHANNEL_24GHZ]
  session = bs.parse_fields(kia[6][0])
  assert bs.text(session, 1) == "starpilot" and len(bs.text(session, 2)) == 32


def test_older_head_units_and_the_first_try_keep_the_reply_that_works_today():
  for reply in (version_reply(FORD_V16), version_reply(KIA_V41, modern=False)):
    assert reply[4] == [0] and 5 not in reply and 6 not in reply


# The version request a Mavlou wireless dongle sent a phone (Bluetooth capture): protocol 4.0, 5 GHz channels, its endpoint.
MAVLOU_V40 = (field(1, 4) + field(2, 0) + field(3, 0) + b"".join(field(4, channel) for channel in (5180, 5200, 5745, 5825, 5720)) +
              field(5, b"") + field(6, field(1, "192.168.1.101") + field(2, 1234)))


def test_the_phone_identity_reply_matches_what_a_phone_sent_the_mavlou_dongle():
  boot, _ = scripted([])
  boot.modern_reply, boot.phone_identity, boot.phone_address = True, True, "AA:BB:CC:DD:EE:FF"
  sent = []
  boot.send = Mock(side_effect=lambda message_id, payload=b"": sent.append(payload))
  boot.next_frame = Mock(side_effect=[(bs.WIFI_VERSION_REQUEST, MAVLOU_V40), bs.BootstrapError("wifi_start", "stop")])
  with pytest.raises(bs.BootstrapError):
    boot.run(slow_join(0, []))
  reply = bs.parse_fields(sent[0])
  assert reply[1] == [4] and reply[2] == [0] and reply[4] == [0] and 5 not in reply
  assert len(bs.text(reply, 3)) == 8
  identity = bs.parse_fields(reply[6][0])
  assert len(bs.text(identity, 1)) == 36 and len(bs.text(identity, 2)) == 36
  assert bs.text(identity, 3) == "starpilot" and bs.text(identity, 4) == "AA:BB:CC:DD:EE:FF"


def test_the_phone_identity_reply_needs_protocol_four_and_leaves_out_an_unknown_address():
  assert version_reply(FORD_V16)[4] == [0] and 6 not in version_reply(FORD_V16)
  boot, _ = scripted([])
  boot.modern_reply, boot.phone_identity = True, True
  reply = bs.parse_fields(boot._version_response(4, 0, MAVLOU_V40))
  assert 4 not in bs.parse_fields(reply[6][0])


def test_phone_identity_is_sent_but_never_hex_logged_even_with_experiments():
  records = []
  address = "AA:BB:CC:DD:EE:FF"
  boot = bs.WirelessBootstrap(Mock(), lambda event, **values: records.append((event, values)),
                             modern_reply=True, phone_identity=True, phone_address=address, experiment="all")
  reply = boot._version_response(4, 0, MAVLOU_V40)
  boot.send(bs.WIFI_VERSION_RESPONSE, reply)
  assert address.encode() in reply
  assert records and not any("hex" in values for _, values in records)
  boot.sock.sendall.assert_called_once()


def test_early_wifi_credentials_show_the_version_reply_was_accepted():
  boot, _ = scripted([(bs.WIFI_VERSION_REQUEST, MAVLOU_V40), (bs.WIFI_INFO_RESPONSE, INFO),
                      bs.BootstrapClosed("wifi_start", "closed after credentials")])
  with pytest.raises(bs.BootstrapClosed) as error:
    boot.run(slow_join(0, []))
  assert boot.stage == "wifi_start" and not boot.hung_up_on_reply(error.value)
