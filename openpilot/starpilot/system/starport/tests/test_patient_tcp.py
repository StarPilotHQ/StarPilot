from types import SimpleNamespace

from openpilot.starpilot.system.starport import supervisor as sup


class Clock:
  def __init__(self):
    self.now = 1000.0


def fake_supervisor(clock, patient):
  waits, logs = [], []
  def wait(seconds):
    waits.append(seconds)
    clock.now += seconds
  return SimpleNamespace(config={"patient_tcp": patient}, _stage=lambda *a: None, _check_cancel=lambda: None, _wait=wait,
                         _track=lambda sock: sock, log=lambda name, **v: logs.append((name, v))), waits, logs


def run(monkeypatch, patient, error, succeed_after=None):
  clock = Clock()
  monkeypatch.setattr(sup.time, "monotonic", lambda: clock.now)
  calls = []
  class Sock:
    def setsockopt(self, *a):
      pass
  def connect(*a, **k):
    calls.append(clock.now)
    if succeed_after is not None and len(calls) > succeed_after:
      return Sock()
    raise error
  monkeypatch.setattr(sup.socket, "create_connection", connect)
  self, waits, logs = fake_supervisor(clock, patient)
  result = SimpleNamespace(endpoint=SimpleNamespace(ip="192.168.1.101", port=1234))
  lease = SimpleNamespace(local_ip="")
  try:
    return sup.Supervisor._connect_tcp(self, result, lease), calls
  except RuntimeError:
    return None, calls


REFUSED = ConnectionRefusedError(111, "Connection refused")


def test_default_gives_up_after_the_usual_attempts(monkeypatch):
  sock, calls = run(monkeypatch, False, REFUSED)
  assert sock is None and len(calls) == sup.TCP_ATTEMPTS


def test_patient_keeps_trying_a_refused_connection_for_a_minute(monkeypatch):
  sock, calls = run(monkeypatch, True, REFUSED)
  assert sock is None and len(calls) > sup.TCP_ATTEMPTS
  assert sup.PATIENT_TCP_SECONDS <= calls[-1] - calls[0] + 3.0 <= sup.PATIENT_TCP_SECONDS + 6.0
  sock, calls = run(monkeypatch, True, REFUSED, succeed_after=15)
  assert sock is not None and len(calls) == 16


def test_patient_does_not_stretch_other_errors(monkeypatch):
  sock, calls = run(monkeypatch, True, TimeoutError("timed out"))
  assert sock is None and len(calls) == sup.TCP_ATTEMPTS
