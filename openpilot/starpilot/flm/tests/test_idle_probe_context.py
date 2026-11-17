import os
import resource
import sys
import pytest
from openpilot.starpilot.flm import native_primitives as native


pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux optional worker scheduler policy')


@pytest.mark.parametrize('policy,allowed',[(0,True),(5,True),(1,False),(2,False),(3,False),(6,False)])
def test_native_worker_keeps_non_realtime_bounded_context(monkeypatch,policy,allowed):
  monkeypatch.setattr(native,'_enabled',False)
  monkeypatch.setattr(native.sys,'platform','linux')
  monkeypatch.setattr(os,'getpriority',lambda kind,pid:19)
  monkeypatch.setattr(os,'sched_getscheduler',lambda pid:policy)
  monkeypatch.setattr(resource,'getrlimit',lambda kind:(20,20)if kind==resource.RLIMIT_CPU else(2*1024**3,2*1024**3))
  if allowed:
    native.enable_fit_worker()
    assert native._enabled
  else:
    with pytest.raises(ValueError,match='worker context'):
      native.enable_fit_worker()
    assert not native._enabled


@pytest.mark.parametrize('cpu,memory,nice',[(-1,2*1024**3,19),(21,2*1024**3,19),(20,-1,19),(20,2*1024**3+1,19),(20,2*1024**3,9)])
def test_idle_class_does_not_relax_cpu_memory_or_nice_bounds(monkeypatch,cpu,memory,nice):
  monkeypatch.setattr(native,'_enabled',False)
  monkeypatch.setattr(native.sys,'platform','linux')
  monkeypatch.setattr(os,'getpriority',lambda kind,pid:nice)
  monkeypatch.setattr(os,'sched_getscheduler',lambda pid:os.SCHED_IDLE)
  monkeypatch.setattr(resource,'getrlimit',lambda kind:(cpu,cpu)if kind==resource.RLIMIT_CPU else(memory,memory))
  with pytest.raises(ValueError,match='worker context'):
    native.enable_fit_worker()
  assert not native._enabled
