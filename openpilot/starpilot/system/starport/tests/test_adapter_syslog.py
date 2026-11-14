import gzip
import io
import zipfile

from openpilot.starpilot.system.starport import adapter_syslog, compat_report
from openpilot.starpilot.system.starport.adapter_syslog import AdapterSyslog


class Clock:
  def __init__(self):
    self.now = 100.0

  def __call__(self):
    return self.now


def make(tmp_path, download):
  events = []
  clock = Clock()
  syslog = AdapterSyslog(lambda name, **fields: events.append((name, fields)), log_dir=tmp_path, download=download, clock=clock)
  return syslog, events, clock


def test_gzip_syslog_is_saved_as_text(tmp_path):
  calls = []

  def download(host, local_ip):
    calls.append((host, local_ip))
    return 200, gzip.compress(b"aawireless: video started\n")

  syslog, events, _ = make(tmp_path, download)
  assert syslog.fetch("10.42.1.1", "10.42.1.77")
  text = (tmp_path / adapter_syslog.FILE_NAME).read_text()
  assert text.startswith("# AAWireless syslog from http://10.42.1.1:8000/syslog")
  assert text.endswith("aawireless: video started\n")
  assert calls == [("10.42.1.1", "10.42.1.77")]
  assert events[0][0] == "adapter_syslog" and events[0][1]["ok"]


def test_plain_text_syslog_is_saved(tmp_path):
  syslog, _, _ = make(tmp_path, lambda host, local_ip: (200, b"plain line\n"))
  assert syslog.fetch("10.42.1.1")
  assert (tmp_path / adapter_syslog.FILE_NAME).read_text().endswith("plain line\n")


def test_large_syslog_keeps_the_newest_part(tmp_path, monkeypatch):
  monkeypatch.setattr(adapter_syslog, "MAX_TEXT_BYTES", 64)
  body = gzip.compress(b"old\n" * 1000 + b"newest line\n")
  syslog, _, _ = make(tmp_path, lambda host, local_ip: (200, body))
  assert syslog.fetch("10.42.1.1")
  text = (tmp_path / adapter_syslog.FILE_NAME).read_text()
  assert text.endswith("newest line\n")
  assert len(text.split("\n", 1)[1]) <= 64


def test_failure_is_logged_and_keeps_the_previous_file(tmp_path):
  (tmp_path / adapter_syslog.FILE_NAME).write_text("previous\n")

  def download(host, local_ip):
    raise ConnectionRefusedError("refused")

  syslog, events, _ = make(tmp_path, download)
  assert not syslog.fetch("10.42.1.1")
  assert (tmp_path / adapter_syslog.FILE_NAME).read_text() == "previous\n"
  assert events == [("adapter_syslog", {"ok": False, "error": "refused", "kind": "ConnectionRefusedError"})]


def test_http_error_is_a_failure(tmp_path):
  syslog, events, _ = make(tmp_path, lambda host, local_ip: (404, b"not found"))
  assert not syslog.fetch("10.42.1.1")
  assert not (tmp_path / adapter_syslog.FILE_NAME).exists()
  assert events[0][1]["error"] == "HTTP 404"


def test_fetches_are_throttled(tmp_path):
  calls = []

  def download(host, local_ip):
    calls.append(host)
    return 200, b"x\n"

  syslog, _, clock = make(tmp_path, download)
  assert syslog.fetch("10.42.1.1")
  clock.now += 10
  assert not syslog.fetch("10.42.1.1")
  clock.now += adapter_syslog.MIN_INTERVAL_S
  assert syslog.fetch("10.42.1.1")
  assert len(calls) == 2


def test_bundle_includes_the_adapter_syslog(tmp_path):
  (tmp_path / adapter_syslog.FILE_NAME).write_text("dongle line aa:bb:cc:dd:ee:ff\n")
  data = compat_report.bundle(tmp_path, tmp_path / "config.json", snapshot=None)
  with zipfile.ZipFile(io.BytesIO(data)) as archive:
    text = archive.read(f"logs/{adapter_syslog.FILE_NAME}").decode()
  assert text.startswith("dongle line")
  assert "aa:bb:cc:dd:ee" not in text


class FakeSupervisor:
  def __init__(self, profile="aawireless", car_type="adapter", stopped=False):
    import threading
    self.config = {"car_profile_override": profile}
    self._car_type = car_type
    self._stop = threading.Event()
    if stopped:
      self._stop.set()
    self.fetched = []
    self.watched = []
    self._adapter_syslog = type("Syslog", (), {"fetch": lambda _, host, local_ip, force=False: self.fetched.append((host, local_ip, force)),
                                               "watch": lambda _, host, local_ip, done: self.watched.append((host, local_ip))})()

  def car_type(self):
    if self._car_type is None:
      raise ValueError("bad car")
    return self._car_type

  def log(self, *args, **kwargs):
    pass


def save(supervisor, endpoint=True):
  from types import SimpleNamespace
  from openpilot.starpilot.system.starport.supervisor import Supervisor
  result = SimpleNamespace(endpoint=SimpleNamespace(ip="10.42.1.1", port=5000)) if endpoint else SimpleNamespace()
  Supervisor._save_adapter_syslog(supervisor, result, SimpleNamespace(local_ip="10.42.1.77"))
  return supervisor.fetched


def test_supervisor_fetches_only_for_the_aawireless_adapter():
  assert save(FakeSupervisor()) == [("10.42.1.1", "10.42.1.77", True)]
  assert save(FakeSupervisor(profile="unofficial")) == []
  assert save(FakeSupervisor(profile="")) == []
  assert save(FakeSupervisor(car_type="builtin")) == []
  assert save(FakeSupervisor(stopped=True)) == []
  assert save(FakeSupervisor(), endpoint=False) == []


def test_a_failing_gate_never_breaks_session_cleanup():
  assert save(FakeSupervisor(car_type=None)) == []


class TricklingResponse:
  status = 200

  def __init__(self, clock):
    self.clock = clock

  def read1(self, size):
    self.clock.now += 1.0
    return b"x" * 10


def test_a_trickling_download_stops_at_the_deadline(monkeypatch):
  clock = Clock()
  connection = type("Connection", (), {"request": lambda *a: None, "close": lambda self: None,
                                       "getresponse": lambda self: TricklingResponse(clock)})
  monkeypatch.setattr(adapter_syslog.http.client, "HTTPConnection", lambda *a, **k: connection())
  monkeypatch.setattr(adapter_syslog.time, "monotonic", clock)
  try:
    adapter_syslog._download("10.42.1.1", "")
  except TimeoutError:
    assert clock.now - 100.0 <= adapter_syslog.DEADLINE_S + 1
  else:
    raise AssertionError("trickling download was not cut off")


def test_while_connected_the_supervisor_starts_the_copier():
  import threading
  from types import SimpleNamespace
  from openpilot.starpilot.system.starport.supervisor import Supervisor
  supervisor = FakeSupervisor()
  result = SimpleNamespace(endpoint=SimpleNamespace(ip="10.42.1.1", port=5000))
  Supervisor._save_adapter_syslog(supervisor, result, SimpleNamespace(local_ip="10.42.1.77"), threading.Event())
  assert supervisor.watched == [("10.42.1.1", "10.42.1.77")] and supervisor.fetched == []
  other = FakeSupervisor(profile="unofficial")
  Supervisor._save_adapter_syslog(other, result, SimpleNamespace(local_ip=""), threading.Event())
  assert other.watched == []


def test_copies_are_taken_while_connected_and_stop_with_the_session(tmp_path, monkeypatch):
  import threading
  monkeypatch.setattr(adapter_syslog, "FIRST_COPY_S", 0.01)
  monkeypatch.setattr(adapter_syslog, "MIN_INTERVAL_S", 0.01)
  copied = threading.Event()

  def download(host, local_ip):
    copied.set()
    return 200, b"live line\n"

  syslog = AdapterSyslog(lambda *a, **k: None, log_dir=tmp_path, download=download)
  done = threading.Event()
  thread = syslog.watch("10.42.1.1", "", done)
  assert copied.wait(2)
  done.set()
  thread.join(2)
  assert not thread.is_alive()
  assert (tmp_path / adapter_syslog.FILE_NAME).read_text().endswith("live line\n")


def test_the_end_of_session_copy_ignores_the_throttle(tmp_path):
  calls = []
  syslog, _, clock = make(tmp_path, lambda host, local_ip: (calls.append(host), (200, b"x\n"))[1])
  assert syslog.fetch("10.42.1.1")
  clock.now += 1
  assert not syslog.fetch("10.42.1.1")
  assert syslog.fetch("10.42.1.1", force=True)
  assert len(calls) == 2


def test_overlapping_copies_are_skipped(tmp_path):
  syslog, _, _ = make(tmp_path, lambda host, local_ip: (200, b"x\n"))
  with syslog.busy:
    assert not syslog.fetch("10.42.1.1", force=True)
