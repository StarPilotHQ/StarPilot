from dataclasses import fields
import pytest
from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
from openpilot.starpilot.flm.live_tracking_probe_process import _literal_control


def control():
  # Literal transport only; this grants no live/native/controller authority.
  return ControlEvidence(identity='transport-only',generation=1,control_ns=100,state_ns=99,
    domain='normalized_torque',stage_revision='transport-only',response_revision='transport-only',
    requested=-0.,features=(0.,),baseline_prediction=0.,enabled=False,manual=False,active=False,
    valid=False,override=False,fault=False,saturated=False,limited=False,model_wobble=False,controller_chatter=False,
    replay_inputs=(('complete',{'value':[1.,True,1]}),),live_applied_coordinates=(0.,)*30,
    vehicle_model_anchor=(1.,2.,3.))


def test_complete_literal_preserves_every_actual_field():
  row=control()
  literal=_literal_control(row)
  assert list(literal)==[f.name for f in fields(ControlEvidence)]
  assert all(literal[f.name]==getattr(row,f.name) for f in fields(ControlEvidence))


@pytest.mark.parametrize('field',['requested','features','replay_snapshot','replay_inputs','stage_values','vehicle_model_anchor','live_applied_coordinates'])
def test_unknown_leaf_never_executes_conversion_or_reducer(field):
  calls=[]
  class Trap:
    def __reduce__(self):
      calls.append('reduce')
      raise AssertionError('foreign reducer')
    def __float__(self):
      calls.append('float')
      raise AssertionError('foreign conversion')
    def __iter__(self):
      calls.append('iter')
      raise AssertionError('foreign iteration')
  row=control()
  object.__setattr__(row,field,Trap())
  with pytest.raises(ValueError):
    _literal_control(row)
  assert calls==[]


@pytest.mark.parametrize('value',[float('nan'),float('inf'),1<<513,'x'*131073,tuple(range(8193))])
def test_original_closed_graph_limits_still_refuse(value):
  row=control()
  object.__setattr__(row,'replay_inputs',(('invalid',value),))
  with pytest.raises(ValueError):
    _literal_control(row)


def test_nested_mutation_is_rechecked_on_next_transport():
  row=control()
  nested: list[object]=[1.,True,1]
  object.__setattr__(row,'replay_inputs',(('complete',nested),))
  _literal_control(row)
  nested.append(object())
  with pytest.raises(ValueError):
    _literal_control(row)
