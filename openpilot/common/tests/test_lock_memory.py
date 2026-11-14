"""lock_memory locks only under an unlimited RLIMIT_MEMLOCK and never raises."""
import resource

import pytest

from openpilot.common import realtime

INF = resource.RLIM_INFINITY


class FakeLibc:
  def __init__(self, result=0):
    self.result = result
    self.calls = []

  def mlockall(self, flags):
    self.calls.append(flags)
    return self.result


@pytest.fixture
def device(monkeypatch):
  monkeypatch.setattr(realtime, 'PC', False)
  monkeypatch.setattr(realtime.sys, 'platform', 'linux')
  state = {'limit': (INF, INF), 'set': [], 'libc': FakeLibc()}
  monkeypatch.setattr(realtime.resource, 'getrlimit', lambda which: state['limit'])
  monkeypatch.setattr(realtime.resource, 'setrlimit', lambda which, value: state['set'].append(value))
  monkeypatch.setattr(realtime.ctypes, 'CDLL', lambda *a, **k: state['libc'])
  monkeypatch.setattr(realtime.ctypes, 'get_errno', lambda: 12)
  return state


def test_locks_on_fault_current_and_future(device):
  assert realtime.lock_memory() == 'locked'
  assert device['libc'].calls == [realtime.MCL_CURRENT | realtime.MCL_FUTURE | realtime.MCL_ONFAULT]
  assert device['set'] == []


def test_raises_soft_limit_when_hard_is_unlimited(device):
  device['limit'] = (8 << 20, INF)
  assert realtime.lock_memory() == 'locked'
  assert device['set'] == [(INF, INF)]


@pytest.mark.parametrize('limit', [(8 << 20, 8 << 20), (INF - 1, INF - 1), (8 << 20, 1 << 40)])
def test_finite_hard_limit_never_locks(device, limit):
  device['limit'] = limit
  assert realtime.lock_memory().startswith('skipped')
  assert device['libc'].calls == [] and device['set'] == []


def test_mlockall_failure_is_reported_not_raised(device):
  device['libc'] = FakeLibc(result=-1)
  assert realtime.lock_memory() == 'failed: errno 12'


def test_setrlimit_error_is_reported_not_raised(device, monkeypatch):
  device['limit'] = (8 << 20, INF)

  def deny(which, value):
    raise ValueError('not allowed')
  monkeypatch.setattr(realtime.resource, 'setrlimit', deny)
  assert realtime.lock_memory().startswith('failed')
  assert device['libc'].calls == []


def test_pc_does_nothing(device, monkeypatch):
  monkeypatch.setattr(realtime, 'PC', True)
  assert realtime.lock_memory() == 'skipped: not device'
  assert device['libc'].calls == []
