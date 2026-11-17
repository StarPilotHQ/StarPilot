from types import SimpleNamespace
from dataclasses import dataclass, field
import pytest
from openpilot.starpilot.flm import learner_cpu as cpu


@dataclass
class State:
  device: str = 'mici'
  online: str | OSError = '1'
  policy: int = 0
  allowed: set[int] = field(default_factory=lambda: set(range(8)))
  calls: list[tuple[object, ...]] = field(default_factory=list)


@pytest.fixture
def placement(monkeypatch):
  state = State()
  monkeypatch.setattr(cpu, 'HARDWARE', SimpleNamespace(get_device_type=lambda:state.device))
  def read(path):
    state.calls.append(('read',path))
    if isinstance(state.online, OSError):
      raise state.online
    return state.online
  monkeypatch.setattr(cpu, 'Path', lambda path:SimpleNamespace(read_text=lambda:read(path)))
  monkeypatch.setattr(cpu, 'os', SimpleNamespace(SCHED_OTHER=0,SCHED_IDLE=5,PRIO_PROCESS=0,
    sched_param=lambda priority:priority,
    sched_setscheduler=lambda pid,policy,parameter:state.calls.append(('policy',pid,policy,parameter)),
    sched_getaffinity=lambda pid:state.allowed,sched_getscheduler=lambda pid:state.policy,
    sched_setaffinity=lambda pid,mask:state.calls.append(('affinity',pid,mask)),
    setpriority=lambda kind,pid,nice:state.calls.append(('nice',kind,pid,nice))))
  return state


def test_receiver_shares_model_core_as_background_work(placement: State):
  assert cpu.configure_cpu()
  assert placement.calls[-2:]==[('affinity',0,{7}),('nice',0,0,10)]


def test_numerical_child_uses_background_cluster(placement: State):
  placement.allowed={7}
  assert cpu.configure_cpu(numerical=True)
  assert placement.calls[-3:]==[('affinity',0,{0,1,2,3}),('nice',0,0,19),('policy',0,5,0)]


@pytest.mark.parametrize('device',['pc','tici','tizi','mici'])
def test_supported_hardware_only(placement: State,device):
  placement.device=device
  assert cpu.configure_cpu()==(device!='pc')
  if device=='pc':
    assert placement.calls==[]


@pytest.mark.parametrize('online',['0',OSError('unavailable')])
def test_never_enables_an_offline_cpu(placement: State,online):
  placement.online=online
  assert not cpu.configure_cpu()
  assert not any(call[0]in('affinity','nice')for call in placement.calls)


def test_receiver_respects_restricted_parent_affinity(placement: State):
  placement.allowed={0,1}
  assert not cpu.configure_cpu()
  assert placement.calls==[]


def test_never_inherits_or_grants_realtime(placement: State):
  placement.policy=1
  with pytest.raises(ValueError,match='ordinary scheduling'):
    cpu.configure_cpu()
  assert not any(call[0]in('affinity','nice')for call in placement.calls)
