"""What the Bluetooth controller did in the first minutes of a session, without its secrets.

2026-11-09: after a comma reboot the Honda took 60 s to bring up the hands-free link that grants the screen, and
nothing on the comma recorded what the controller was doing or when the phone connected. This runs btmon for the
start of each session, keeps only the command and event headers with their address, handle, status and reason lines
(no payloads, no keys, no names), masks addresses to their last byte, and writes them to bt_trace.txt beside the
session logs. Link-level connects and disconnects also go into the session log, on the same timeline as
hfp_connected and video_focus.

2026-11-13: btmon shortens a header to 80 columns when its output is not a terminal ("HCI Event: Connect Re.. (0x04)"),
so no link event ever matched by name and none reached the session log; they are recognised by their code now. The
traces of sessions 55 and 56 showed the Honda's link up for 68 s with no authentication and no hands-free, and could
not show whether the Honda asked for anything on it, because every data packet was dropped. Connection signalling is
kept now: the L2CAP, SDP, AVDTP and RFCOMM control headers with their channel, result and status lines (which service
was asked for and how it was answered), still without any payload; streams and AT commands stay out.
"""
import re
import subprocess
import threading
from pathlib import Path

from openpilot.starpilot.system.starport.system_snapshot import redact

TRACE_NAME = "bt_trace.txt"
TRACE_SECONDS = 300
TRACE_MAX_BYTES = 2_000_000
COMMAND = ("sudo", "-n", "stdbuf", "-oL", "timeout", "--signal=INT", "{seconds}", "btmon", "-T")
STOP_COMMAND = ("sudo", "-n", "pkill", "-INT", "-x", "btmon")
HEADER = re.compile(r"^[<>@=] ")
PAYLOAD = ("ACL Data", "SCO Data", "ISO Data")
DETAIL = re.compile(r"^\s+(Address|Peer address|BD_ADDR|LE Address|Handle|Status|Reason|Link type|Role|Error|Mode|Interval)\b")
LINK_EVENTS = ("Connect Request", "Connect Complete", "Connection Complete", "Disconnect Complete",
               "Disconnection Complete", "Create Connection", "Accept Connection", "Reject Connection",
               "HCI Command: Disconnect", "Device Connected", "Device Disconnected", "Connect Failed")
# The same events by code: btmon shortens the name to fit 80 columns ("Connect Re..", or all of it: "HCI Command:..."),
# never the code.
CODED = re.compile(r"^[<>@] (HCI Event|HCI Command|MGMT Event):.*?\((0x[0-9a-fx|]+)\) plen")
LINK_CODES = {
  "HCI Event": {"0x03": "Connect Complete", "0x04": "Connect Request", "0x05": "Disconnect Complete"},
  "HCI Command": {"0x01|0x0005": "Create Connection", "0x01|0x0006": "Disconnect",
                  "0x01|0x0009": "Accept Connection Request", "0x01|0x000a": "Reject Connection Request"},
  "MGMT Event": {"0x000b": "Device Connected", "0x000c": "Device Disconnected", "0x000d": "Connect Failed"},
}
# Under an ACL data header: connection signalling is kept, everything else on the link (streams, AT commands) is not.
SIGNALLING = re.compile(r"^\s+(L2CAP: |SDP: |AVDTP: |RFCOMM: (?!Unnumbered Info))")
SIGNALLING_CHANNEL = re.compile(r"^\s+Channel: \d+ len \d+ \[PSM \d+")
SIGNALLING_DETAIL = re.compile(r"^\s+(PSM|Source CID|Destination CID|Result|Status|Type|Reason)\b")
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s*$")
TRAILER = re.compile(r"\s+(plen \d+|\{0x[0-9a-fA-F]+\}|#\d+|\[hci\d+\]).*$")
FIELD = re.compile(r"^\s+([A-Za-z_ ]+?):\s*(.*)$")


class TraceFilter:
  """Line by line: the kept, masked text, and one summary per link-level event once its details are in."""

  def __init__(self):
    self.keeping = False
    self.event: dict | None = None
    self.held: str | None = None   # an ACL data header (and its channel line) not yet known to be signalling
    self.signalling = False        # the ACL data block being read is connection signalling

  @staticmethod
  def link_event(line: str) -> str | None:
    """The name of a link-level event, whether btmon wrote its name out or shortened it."""
    coded = CODED.match(line)
    if coded:
      name = LINK_CODES[coded.group(1)].get(coded.group(2))
      if name:
        return f"{coded.group(1)}: {name} ({coded.group(2)})"
    if any(word in line for word in LINK_EVENTS):
      return TRAILER.sub("", line[2:]).strip()[:80]
    return None

  def feed(self, line: str) -> tuple[str | None, dict | None]:
    line = line.rstrip("\r\n")
    if HEADER.match(line):
      finished, self.event = self.event, None
      self.held, self.signalling = None, False
      self.keeping = not any(word in line for word in PAYLOAD)
      if not self.keeping:
        if "ACL Data" in line:
          self.held = redact(line.encode()).decode()
        return None, finished
      what = self.link_event(line)
      if what:
        stamp = STAMP.search(line)
        self.event = {"what": what, "direction": {"<": "sent", ">": "received"}.get(line[0], "host"),
                      "at": stamp.group(1) if stamp else ""}
      return redact(line.encode()).decode(), finished
    if self.held is not None:
      if SIGNALLING.match(line):
        kept, self.held, self.signalling = f"{self.held}\n{redact(line.encode()).decode()}", None, True
        return kept, None
      if SIGNALLING_CHANNEL.match(line):
        self.held = f"{self.held}\n{line}"   # which service's channel; written only if signalling follows on it
      return None, None
    if self.signalling:
      if SIGNALLING.match(line) or SIGNALLING_DETAIL.match(line):
        return redact(line.encode()).decode(), None
      return None, None
    if self.keeping and DETAIL.match(line):
      kept = redact(line.encode()).decode()
      field = FIELD.match(kept)
      if self.event is not None and field:
        self.event.setdefault(field.group(1).strip().lower().replace(" ", "_"), field.group(2).strip()[:60])
      return kept, None
    return None, None

  def flush(self) -> dict | None:
    finished, self.event = self.event, None
    return finished


class BluetoothTrace:
  def __init__(self, directory: Path, log, *, seconds: int = TRACE_SECONDS, popen=subprocess.Popen, run=subprocess.run):
    self.path = Path(directory) / TRACE_NAME
    self.log, self.seconds, self.popen, self.run = log, seconds, popen, run
    self.process = None
    self.thread: threading.Thread | None = None

  def start(self) -> "BluetoothTrace":
    self.stop()
    try:
      if self.path.exists():
        self.path.replace(self.path.with_name(self.path.stem + ".1" + self.path.suffix))
      command = [part.format(seconds=self.seconds) for part in COMMAND]
      self.process = self.popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                text=True, errors="replace")
    except (OSError, ValueError) as error:
      self.process = None
      self.log("bt_trace_unavailable", error=str(error)[:120])
      return self
    self.thread = threading.Thread(target=self._pump, args=(self.process,), name="starport-bt-trace", daemon=True)
    self.thread.start()
    self.log("bt_trace_started", seconds=self.seconds)
    return self

  def _pump(self, process) -> None:
    trace, written, events = TraceFilter(), 0, 0
    try:
      with open(self.path, "w", encoding="utf-8") as out:
        for line in process.stdout:
          kept, event = trace.feed(line)
          if event is not None:
            events += 1
            self.log("bt_hci", **event)
          if kept is not None and written < TRACE_MAX_BYTES:
            out.write(kept + "\n")
            out.flush()
            written += len(kept) + 1
        event = trace.flush()
        if event is not None:
          events += 1
          self.log("bt_hci", **event)
    except (OSError, ValueError) as error:
      self.log("bt_trace_failed", error=str(error)[:120])
    self.log("bt_trace_stopped", bytes=written, events=events)

  def stop(self) -> None:
    process, self.process = self.process, None
    thread, self.thread = self.thread, None
    if process is None:
      return
    try:
      if process.poll() is None:
        self.run(list(STOP_COMMAND), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
      pass
    if thread is not None:
      thread.join(timeout=3)
