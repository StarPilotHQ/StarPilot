import io

from openpilot.starpilot.system.starport import bt_trace
from openpilot.starpilot.system.starport.bt_trace import BluetoothTrace, TraceFilter

SAMPLE = """\
= Note: Linux version 4.9 (aarch64)                                      0.000000
> HCI Event: Connect Request (0x04) plen 10                #12 [hci0] 2026-11-09 10:17:29.123456
        Address: 58:B8:58:11:22:E4 (Honda Motor)
        Class: 0x340408
        Link type: ACL (0x01)
< HCI Command: Accept Connection Request (0x01|0x0009) plen 7  #13 [hci0] 2026-11-09 10:17:29.124000
        Address: 58:B8:58:11:22:E4 (Honda Motor)
        Role: Central (0x00)
> HCI Event: Link Key Request (0x17) plen 6                #14 [hci0] 2026-11-09 10:17:29.300000
        Address: 58:B8:58:11:22:E4 (Honda Motor)
< HCI Command: Link Key Request Reply (0x01|0x000b) plen 22  #15 [hci0] 2026-11-09 10:17:29.301000
        Address: 58:B8:58:11:22:E4 (Honda Motor)
        Link key: 00112233445566778899aabbccddeeff
> ACL Data RX: Handle 3804 flags 0x02 dlen 12              #16 [hci0] 2026-11-09 10:17:29.400000
      L2CAP: Connection Request (0x02) ident 1 len 4
        Address: 11:22:33:44:55:66
> HCI Event: Remote Name Req Complete (0x07) plen 255      #17 [hci0] 2026-11-09 10:17:29.500000
        Status: Success (0x00)
        Name: Some Phone
> HCI Event: Disconnect Complete (0x05) plen 4             #18 [hci0] 2026-11-09 10:17:40.000000
        Status: Success (0x00)
        Handle: 3804
        Reason: Remote User Terminated Connection (0x13)
"""


def run(text):
  trace, kept, events = TraceFilter(), [], []
  for line in text.splitlines():
    line_kept, event = trace.feed(line)
    kept += [line_kept] if line_kept is not None else []
    events += [event] if event is not None else []
  last = trace.flush()
  return "\n".join(kept), events + ([last] if last else [])


def test_the_trace_keeps_headers_and_link_details_and_no_secret_or_payload():
  kept, _ = run(SAMPLE)
  assert "Connect Request" in kept and "Disconnect Complete" in kept and "Reason: Remote User Terminated" in kept
  assert "xx:xx:xx:xx:xx:E4" in kept and "58:B8" not in kept, "addresses are masked to the last byte"
  assert "Link key" not in kept and "00112233" not in kept, "key material never reaches the file"
  assert "Some Phone" not in kept and "Class" not in kept, "no device names"
  assert "L2CAP: Connection Request" in kept and ":66" not in kept, "signalling is named, the lines under it are not kept"


# What btmon really writes on the comma (2026-11-13, session 56): headers shortened to 80 columns, and the data packets
# of one silent link and one working link.
SHORTENED = """\
> HCI Event: Connect Re.. (0x04) plen 10  #18 [hci0] 2026-11-13 16:49:40.869849
        Address: 74:D7:CA:00:00:E4 (Panasonic Automotive Systems Co.,Ltd)
        Link type: ACL (0x01)
< HCI Command: Ac.. (0x01|0x0009) plen 7  #20 [hci0] 2026-11-13 16:49:40.869980
        Address: 74:D7:CA:00:00:E4 (Panasonic Automotive Systems Co.,Ltd)
        Role: Central (0x00)
< HCI Command: C.. (0x01|0x0005) plen 13  #22 [hci0] 2026-11-13 16:49:41.067066
        Address: 68:8F:C9:00:00:2A (Zhuolian (Shenzhen) Communication Co., Ltd)
> HCI Event: Connect Co.. (0x03) plen 11  #32 [hci0] 2026-11-13 16:49:41.133098
        Status: Success (0x00)
        Handle: 22
        Address: 74:D7:CA:00:00:E4 (Panasonic Automotive Systems Co.,Ltd)
@ MGMT Event: De.. (0x000b) plen 33  {0x0001} [hci0] 2026-11-13 16:49:41.206296
> HCI Event: Command Sta.. (0x0f) plen 4  #21 [hci0] 2026-11-13 16:49:41.300000
        Status: Success (0x00)
> ACL Data RX: Han.. flags 0x02 dlen 12   #40 [hci0] 2026-11-13 16:49:41.400000
      L2CAP: Connection Request (0x02) ident 2 len 4
        PSM: 1 (0x0001)
        Source CID: 64
< ACL Data TX: Han.. flags 0x00 dlen 16   #41 [hci0] 2026-11-13 16:49:41.401000
      L2CAP: Connection Response (0x03) ident 2 len 8
        Destination CID: 64
        Source CID: 64
        Result: Connection successful (0x0000)
        Status: No further information available (0x0000)
> ACL Data RX: Han.. flags 0x02 dlen 24   #42 [hci0] 2026-11-13 16:49:41.450000
      Channel: 64 len 20 [PSM 1 mode Basic (0x00)] {chan 0}
      SDP: Service Search Attribute Request (0x06) tid 0 len 15
        Search pattern: [len 5]
              Handsfree Audio Gateway (0x111f)
> ACL Data RX: Han.. flags 0x02 dlen 8    #43 [hci0] 2026-11-13 16:49:41.500000
      Channel: 65 len 4 [PSM 3 mode Basic (0x00)] {chan 1}
      RFCOMM: Set Async Balance Mode (SABM) (0x2f)
         Address: 0x03 cr 1 dlci 0x00
         FCS: 0x1c
> ACL Data RX: Han.. flags 0x02 dlen 21   #44 [hci0] 2026-11-13 16:49:41.600000
      Channel: 65 len 17 [PSM 3 mode Basic (0x00)] {chan 1}
      RFCOMM: Unnumbered Info with Header Check (UIH) (0xef)
         Address: 0x0b cr 1 dlci 0x02
        41 54 2b 42 52 53 46 3d 31 39 31 0d              AT+BRSF=191.
> ACL Data RX: Han.. flags 0x02 dlen 600  #45 [hci0] 2026-11-13 16:49:41.700000
      Channel: 66 len 596 [PSM 25 mode Basic (0x00)] {chan 3}
        80 60 00 01 00 00 03 c0 00 00 00 00 9c bd 35 21  .`............5!
> HCI Event: Mode Change (0x14) plen 6    #46 [hci0] 2026-11-13 16:49:57.230000
        Status: Success (0x00)
        Handle: 4 Address: 98:69:8A:00:00:03 (Apple, Inc.)
        Mode: Sniff (0x02)
        Interval: 500.000 msec (0x0320)
> HCI Event: Disconnec.. (0x05) plen 4    #47 [hci0] 2026-11-13 16:50:49.280000
        Status: Success (0x00)
        Handle: 22 Address: 74:D7:CA:00:00:E4 (Panasonic Automotive Systems Co.,Ltd)
        Reason: Remote User Terminated Connection (0x13)
> HCI Event: Connect.. (0x04) plen 10 #10470 [hci0] 2026-11-13 16:50:51.250000
        Address: 74:D7:CA:00:00:E4 (Panasonic Automotive Systems Co.,Ltd)
< HCI Command:.. (0x01|0x0009) plen 7 #10471 [hci0] 2026-11-13 16:50:51.251000
        Address: 74:D7:CA:00:00:E4 (Panasonic Automotive Systems Co.,Ltd)
"""


def test_link_events_are_recognised_when_btmon_shortens_their_names():
  # Sessions 55 and 56 logged "bt_trace_stopped events=0": every header was shortened and none matched by name.
  _, events = run(SHORTENED)
  assert [event["what"] for event in events] == [
    "HCI Event: Connect Request (0x04)", "HCI Command: Accept Connection Request (0x01|0x0009)",
    "HCI Command: Create Connection (0x01|0x0005)", "HCI Event: Connect Complete (0x03)",
    "MGMT Event: Device Connected (0x000b)", "HCI Event: Disconnect Complete (0x05)",
    # later in a session the packet number is longer and btmon drops the whole name: "HCI Command:.. (0x01|0x0009)"
    "HCI Event: Connect Request (0x04)", "HCI Command: Accept Connection Request (0x01|0x0009)"]
  assert events[0]["address"].startswith("xx:xx:xx:xx:xx:E4") and events[2]["address"].startswith("xx:xx:xx:xx:xx:2A")
  assert events[3]["handle"] == "22" and events[5]["reason"].startswith("Remote User Terminated")


def test_connection_signalling_is_kept_and_streams_and_commands_are_not():
  kept, _ = run(SHORTENED)
  lines = kept.splitlines()
  assert "      L2CAP: Connection Request (0x02) ident 2 len 4" in lines and "        PSM: 1 (0x0001)" in lines
  assert "        Result: Connection successful (0x0000)" in lines, "how the request was answered"
  assert "      SDP: Service Search Attribute Request (0x06) tid 0 len 15" in lines
  assert "      Channel: 64 len 20 [PSM 1 mode Basic (0x00)] {chan 0}" in lines, "which service the signalling ran on"
  assert "      RFCOMM: Set Async Balance Mode (SABM) (0x2f)" in lines
  assert sum("ACL Data" in line for line in lines) == 4, "one header per signalling packet, none for data"
  assert "Handsfree Audio Gateway" not in kept and "Search pattern" not in kept, "no service contents"
  assert "UIH" not in kept and "AT+BRSF" not in kept and "PSM 3 mode" in kept and "len 17" not in kept, "no AT commands"
  assert "PSM 25" not in kept and "9c bd 35" not in kept, "no audio stream"
  assert "        Mode: Sniff (0x02)" in lines and "        Interval: 500.000 msec (0x0320)" in lines


def test_link_events_reach_the_session_log_with_their_own_timestamp():
  _, events = run(SAMPLE)
  assert [event["what"] for event in events] == ["HCI Event: Connect Request (0x04)",
                                                 "HCI Command: Accept Connection Request (0x01|0x0009)",
                                                 "HCI Event: Disconnect Complete (0x05)"]
  request, _, gone = events
  assert request == {"what": "HCI Event: Connect Request (0x04)", "direction": "received",
                     "at": "2026-11-09 10:17:29.123456", "address": "xx:xx:xx:xx:xx:E4 (Honda Motor)",
                     "link_type": "ACL (0x01)"}
  assert gone["handle"] == "3804" and gone["reason"].startswith("Remote User Terminated") and gone["at"].endswith("40.000000")


class FakeProcess:
  def __init__(self, text):
    self.stdout = io.StringIO(text)

  def poll(self):
    return 0


def test_a_session_trace_rotates_the_last_one_and_logs_start_events_and_stop(tmp_path):
  (tmp_path / bt_trace.TRACE_NAME).write_text("previous session\n")
  logged, commands = [], []

  def popen(command, **_):
    commands.append(command)
    return FakeProcess(SAMPLE)
  trace = BluetoothTrace(tmp_path, lambda name, **values: logged.append((name, values)), seconds=7, popen=popen,
                         run=lambda *a, **k: commands.append(a[0])).start()
  trace.thread.join(timeout=5)
  trace.stop()
  assert commands == [["sudo", "-n", "stdbuf", "-oL", "timeout", "--signal=INT", "7", "btmon", "-T"]], "it ended alone: no kill"
  assert (tmp_path / "bt_trace.1.txt").read_text() == "previous session\n"
  assert "Disconnect Complete" in (tmp_path / bt_trace.TRACE_NAME).read_text()
  names = [name for name, _ in logged]
  assert names[0] == "bt_trace_started" and names.count("bt_hci") == 3 and names[-1] == "bt_trace_stopped"
  assert logged[-1][1]["events"] == 3


def test_a_comma_without_btmon_only_logs_that_and_carries_on(tmp_path):
  logged = []

  def popen(*_, **__):
    raise FileNotFoundError("sudo")
  trace = BluetoothTrace(tmp_path, lambda name, **values: logged.append(name), popen=popen).start()
  trace.stop()
  assert logged == ["bt_trace_unavailable"]
