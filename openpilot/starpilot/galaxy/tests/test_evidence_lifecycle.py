"""Galaxy authority stays sampled independently of HTTP traffic."""
from collections import deque
import threading
import time
from types import SimpleNamespace as NS
from unittest import mock

import pytest

from openpilot.starpilot.galaxy.evidence import EvidenceSource
from openpilot.starpilot.galaxy.settings import LiveContextSource
from openpilot.starpilot.drive_state.evidence import PhysicalSource
from openpilot.starpilot.parked_evidence import RESUME_SKEW_NS


class Messages:
  def __init__(self, clock):
    self.clock = clock
    self.calls = 0
    self.fail = False
    self.sock = {'owned': NS(close=lambda: setattr(self, 'socket_closed', True))}
    self.socket_closed = False
    self.publish()

  def publish(self, *, ignition=False, started=False):
    mono, boot = self.clock['mono'], self.clock['boot']
    self.data = {'deviceState': NS(started=started),
                 'pandaStates': [NS(pandaType='uno', ignitionLine=ignition, ignitionCan=False, controlsAllowed=False, safetyModel='noOutput')],
                 'carState': NS(canValid=True, canTimeout=False, standstill=True, gearShifter='park'),
                 'selfdriveState': NS(enabled=False, active=False), 'carParams': NS(notCar=True), 'peripheralState': NS(pandaType=1, voltage=12640)}
    self.seen = self.alive = self.valid = dict.fromkeys(EvidenceSource.SERVICES, True)
    self.updated = dict(self.seen)
    self.logMonoTime = {name: boot if name == 'pandaStates' else mono for name in EvidenceSource.SERVICES}
    self.recv_time = dict.fromkeys(EvidenceSource.SERVICES, mono / 1000000000.0)

  def update(self, timeout):
    assert timeout == 0
    self.calls += 1
    if self.fail:
      raise OSError('reader failed')

  def __getitem__(self, name):
    return self.data[name]


def readers(tmp_path):
  clock = {'mono': 10_000_000_000, 'boot': 20_000_000_000}
  messages = Messages(clock)
  source = EvidenceSource(messages, mono=lambda: clock['mono'], boot=lambda: clock['boot'])
  (tmp_path / 'IsOffroad').write_bytes(b'1')
  params = NS(get_param_path=lambda key: str(tmp_path / key))
  def clients():
    return (LiveContextSource(params, source, mono_clock=lambda: clock['mono'], boot_clock=lambda: clock['boot'], evidence_wait_ms=0),
            PhysicalSource(source, mono=lambda: clock['mono'], boot=lambda: clock['boot']))
  return clock, messages, source, clients


def tick(clock, messages, source, **state):
  clock['mono'] += 100_000_000
  clock['boot'] += 100_000_000
  messages.publish(**state)
  source.poll()


def test_cold_start_and_idle_first_reads_use_sampled_source_not_http_polling(tmp_path):
  clock, messages, source, clients = readers(tmp_path)
  source.poll()
  parked, physical = clients()
  assert not parked.parked() and not physical.allowed() and physical.effective() is None
  for _ in range(30):
    tick(clock, messages, source)
  # Lazy owners inherit the already-qualified collector startup boundary.
  parked, physical = clients()
  calls = messages.calls
  assert parked.parked() and physical.allowed() and physical.effective() is False
  assert messages.calls == calls


def test_current_stale_future_and_ignition_transition_authority(tmp_path):
  clock, messages, source, clients = readers(tmp_path)
  tick(clock, messages, source)
  parked, physical = clients()
  assert parked.parked() and physical.allowed()
  clock['mono'] += 400_000_000
  clock['boot'] += 400_000_000
  assert not parked.parked() and not physical.allowed()
  tick(clock, messages, source, ignition=True, started=True)
  assert not parked.parked() and physical.effective() is True
  messages.data['selfdriveState'] = NS(enabled=True, active=True)
  source.poll()
  assert not physical.allowed()
  tick(clock, messages, source)
  assert parked.parked() and physical.allowed()
  messages.logMonoTime['pandaStates'] = clock['boot'] + 1
  source.poll()
  assert not parked.parked() and not physical.allowed()


def test_short_resume_before_next_poll_rejects_new_lazy_consumers(tmp_path):
  clock, messages, source, clients = readers(tmp_path)
  tick(clock, messages, source)
  assert clients()[0].parked()
  clock['boot'] += 100_000_000  # Still inside Panda TTL; suspend must invalidate anyway.
  parked, physical = clients()
  assert not parked.parked() and not physical.allowed() and physical.effective() is None
  source.poll()  # Cached pre-resume publisher cannot clear the shared barrier.
  assert not parked.parked()
  tick(clock, messages, source)
  assert parked.parked() and physical.allowed()


@pytest.mark.parametrize('operation', ('poll', 'snapshot'))
def test_preempted_clock_sample_retries_without_discarding_fresh_evidence(tmp_path, operation):
  clock, messages, source, clients = readers(tmp_path)
  tick(clock, messages, source)
  barrier = source.after_mono_ns
  now = clock['mono']
  source.mono = mock.Mock(side_effect=(now - 20_000_000, now, now, now))
  getattr(source, operation)()
  assert source.mono.call_count == 4
  source.mono = lambda: clock['mono']
  assert source.after_mono_ns == barrier
  assert source.device_offset_ns == clock['boot'] - now
  assert clients()[0].parked() and clients()[1].allowed()


def test_repeatedly_preempted_clock_samples_still_fail_closed(tmp_path):
  import itertools
  clock, messages, source, clients = readers(tmp_path)
  tick(clock, messages, source)
  source.mono = mock.Mock(side_effect=itertools.count(clock['mono'], RESUME_SKEW_NS + 1))
  source.poll()
  assert source.mono.call_count == 6
  assert source.device_offset_ns is None
  source.mono = lambda: clock['mono']
  assert not clients()[0].parked() and not clients()[1].allowed()


def test_snapshot_metadata_stays_coherent_and_immutable(tmp_path):
  clock, messages, source, _ = readers(tmp_path)
  tick(clock, messages, source)
  captured = source.snapshot()
  tick(clock, messages, source, started=True)
  assert not captured['deviceState'].started and source.snapshot()['deviceState'].started
  with pytest.raises(TypeError):
    captured.seen['deviceState'] = False
  with pytest.raises(AttributeError):
    captured.after_mono_ns = 0


def test_failure_and_terminal_close_never_resurrect_subscription(tmp_path):
  clock, messages, source, clients = readers(tmp_path)
  tick(clock, messages, source)
  parked, physical = clients()
  messages.fail = True
  source.poll()
  assert not parked.parked() and not physical.allowed() and physical.effective() is None
  messages.fail = False
  tick(clock, messages, source)
  assert parked.parked()
  parked.close()
  calls = messages.calls
  assert not parked.parked() and messages.calls == calls
  source.close()
  assert messages.socket_closed and source.snapshot() is None and not physical.allowed()


def test_background_reader_closes_and_drains_owned_thread(tmp_path):
  _, messages, source, _ = readers(tmp_path)
  source.start()
  assert source.thread.is_alive()
  source.close()
  assert not source.thread.is_alive() and messages.socket_closed and source.snapshot() is None


class RefreshMessages(Messages):
  """Queue each publisher observation once, as the real SubMaster does."""

  def __init__(self):
    self.pending = deque()
    self.pending_lock = threading.Lock()
    self.update_threads = []
    super().__init__({'mono': time.monotonic_ns(), 'boot': time.monotonic_ns() + 1_000_000_000})
    self.seen, self.alive, self.valid = dict(self.seen), dict(self.alive), dict(self.valid)

  def queue(self, **frame):
    with self.pending_lock:
      self.pending.append(frame)

  def update(self, timeout):
    super().update(timeout)
    self.update_threads.append(threading.get_ident())
    self.updated = dict.fromkeys(EvidenceSource.SERVICES, False)
    with self.pending_lock:
      frame = self.pending.popleft() if self.pending else None
    if frame is None:
      return
    if frame.get('reader_fail', False):
      raise OSError('reader failed')
    services = frame.get('services', ('deviceState', 'pandaStates'))
    self.clock['mono'] = time.monotonic_ns()
    self.clock['boot'] = self.clock['mono'] + 1_000_000_000
    for name in services:
      self.updated[name] = True
      self.seen[name] = self.alive[name] = True
      self.valid[name] = frame.get('valid', True)
      if not frame.get('replay', False):
        self.logMonoTime[name] = self.clock['boot' if name == 'pandaStates' else 'mono']
      self.recv_time[name] = self.clock['mono'] / 1e9
    if 'deviceState' in services:
      self.data['deviceState'] = NS(started=frame.get('started', False))
    if 'pandaStates' in services:
      self.data['pandaStates'] = [] if frame.get('empty', False) else [
        NS(pandaType='uno', ignitionLine=frame.get('started', False), ignitionCan=False,
           controlsAllowed=False, safetyModel='noOutput')]


def refresh_readers(tmp_path):
  messages = RefreshMessages()
  source = EvidenceSource(messages, mono=time.monotonic_ns, boot=lambda: time.monotonic_ns() + 1_000_000_000)
  (tmp_path / 'IsOffroad').write_bytes(b'1')
  params = NS(get_param_path=lambda key: str(tmp_path / key))
  authority = LiveContextSource(params, source, mono_clock=time.monotonic_ns,
                                boot_clock=lambda: time.monotonic_ns() + 1_000_000_000, evidence_wait_ms=0)
  source.start()
  messages.update_threads.clear()  # start() owns one initial synchronous poll.
  return messages, source, authority


def refresh_with_frames(source, messages, *frames, timeout_ms=500):
  requested = threading.Event()
  results, errors = [], []
  wake = source.wake.set

  def signal_request():
    requested.set()
    wake()

  def request():
    try:
      results.append(source.refresh(timeout_ms=timeout_ms))
    except Exception as error:
      errors.append(error)

  with mock.patch.object(source.wake, 'set', side_effect=signal_request):
    waiter = threading.Thread(target=request)
    waiter.start()
    try:
      assert requested.wait(1)
      for frame in frames:
        messages.queue(**frame)
      wake()
      waiter.join(timeout=1)
      assert not waiter.is_alive()
      assert not errors
      assert len(results) == 1
      return results[0]
    finally:
      if waiter.is_alive():
        source.close()
        waiter.join(timeout=1)


def test_refresh_updates_cached_onroad_and_reverse_transition(tmp_path):
  messages, source, authority = refresh_readers(tmp_path)
  try:
    assert refresh_with_frames(source, messages, {'started': True})
    assert not authority.parked()
    before = source.snapshot().logMonoTime
    assert refresh_with_frames(source, messages, {'started': False})
    assert authority.parked()
    after = source.snapshot().logMonoTime
    assert all(after[name] > before[name] for name in ('deviceState', 'pandaStates'))
    assert refresh_with_frames(source, messages, {'started': True})
    assert not authority.parked()
    assert set(messages.update_threads) == {source.thread.ident}
  finally:
    authority.close()
    source.close()
  assert not source.thread.is_alive() and messages.socket_closed


def test_stale_sample_waits_for_next_publishers_instead_of_reading_onroad(tmp_path):
  messages, source, _ = refresh_readers(tmp_path)
  params = NS(get_param_path=lambda key: str(tmp_path / key))
  authority = LiveContextSource(params, source, mono_clock=time.monotonic_ns,
                                boot_clock=lambda: time.monotonic_ns() + 1_000_000_000, evidence_wait_ms=750)
  try:
    assert refresh_with_frames(source, messages, {})
    time.sleep(0.4)  # Past the Panda TTL: a parked editor's 5 s poll must not flip on one late frame.
    timer = threading.Timer(0.1, messages.queue)
    timer.start()
    assert authority.parked()
    timer.join()
    time.sleep(0.4)
    started = time.monotonic()
    assert not authority.parked()  # Silent publishers still fail closed once the wait expires.
    assert time.monotonic() - started < 1.5
    # Concurrent requests share one authority; their waits overlap instead of queueing on its lock.
    results = []
    waiters = [threading.Thread(target=lambda: results.append(authority.parked())) for _ in range(3)]
    started = time.monotonic()
    for waiter in waiters:
      waiter.start()
    for waiter in waiters:
      waiter.join(timeout=3)
    assert results == [False, False, False]
    assert time.monotonic() - started < 1.5
  finally:
    authority.close()
    source.close()


@pytest.mark.parametrize('frame', [None, {'services': ('deviceState',)}, {'services': ('pandaStates',)}, {'replay': True}])
def test_refresh_requires_both_post_request_publishers(tmp_path, frame):
  messages, source, authority = refresh_readers(tmp_path)
  try:
    assert refresh_with_frames(source, messages, {})
    assert authority.parked()
    assert not refresh_with_frames(source, messages, *(() if frame is None else (frame,)), timeout_ms=50)
  finally:
    authority.close()
    source.close()
  assert source.refresh(timeout_ms=0) is False


@pytest.mark.parametrize('frame', [{'valid': False}, {'empty': True}, {'started': True}, {'reader_fail': True}])
def test_observed_refresh_never_replaces_live_safety_checks(tmp_path, frame):
  messages, source, authority = refresh_readers(tmp_path)
  try:
    assert refresh_with_frames(source, messages, {})
    assert authority.parked()
    assert refresh_with_frames(source, messages, frame) is (not frame.get('reader_fail', False))
    assert not authority.parked()
  finally:
    authority.close()
    source.close()


@pytest.mark.parametrize('closing', [False, True])
def test_concurrent_refresh_shares_collector_and_close_wakes_waiters(tmp_path, closing):
  messages, source, authority = refresh_readers(tmp_path)
  both_requested = threading.Event()
  count_lock = threading.Lock()
  requests = 0
  results, errors = [], []
  wake = source.wake.set

  def signal_request():
    nonlocal requests
    with count_lock:
      requests += 1
      if requests >= 2:
        both_requested.set()
    wake()

  def request():
    try:
      results.append(source.refresh(timeout_ms=500))
    except Exception as error:
      errors.append(error)

  waiters = [threading.Thread(target=request) for _ in range(2)]
  try:
    with mock.patch.object(source.wake, 'set', side_effect=signal_request):
      for waiter in waiters:
        waiter.start()
      assert both_requested.wait(1)
      if closing:
        source.close()
      else:
        messages.queue()
        wake()
      for waiter in waiters:
        waiter.join(timeout=1)
      assert all(not waiter.is_alive() for waiter in waiters)
      assert not errors and results == [not closing, not closing]
      if not closing:
        assert authority.parked()
        assert set(messages.update_threads) == {source.thread.ident}
  finally:
    authority.close()
    source.close()
    for waiter in waiters:
      waiter.join(timeout=1)
  assert not source.thread.is_alive() and messages.socket_closed
  assert source.refresh(timeout_ms=0) is False
