"""Real native predicate versus unchanged primitive IPC encoding/refusals."""
import importlib
import math
import pytest
from openpilot.starpilot.flm import native_primitives as native
from openpilot.starpilot.flm.controller_replay import pack_primitive_snapshot, unpack_primitive_snapshot


@pytest.fixture
def select(monkeypatch):
  module=importlib.import_module('openpilot.starpilot.flm._primitive_walk')
  def choose(enabled):
    def predicate(operation,value):
      assert operation=='probe_source_valid'
      return module.probe_source_valid(value) if enabled else native.FALLBACK
    monkeypatch.setattr(native,'try_native',predicate)
  return choose


def test_native_validation_preserves_exact_codec_float_bits_and_aliases(select):
  shared=[True,1,1.,-0.,'é😀\ud800',b'\x00\xff']
  source={'first':shared,'second':shared,'tuple':tuple(shared)}
  select(False)
  expected=pack_primitive_snapshot(source)
  select(True)
  assert pack_primitive_snapshot(source)==expected
  decoded=unpack_primitive_snapshot(expected)
  assert decoded['first'] is decoded['second']
  assert math.copysign(1.,decoded['first'][3])==-1.
  shared[3]=0.
  changed=pack_primitive_snapshot(source)
  assert changed!=expected
  select(False)
  assert pack_primitive_snapshot(source)==changed


@pytest.mark.parametrize('value',[2**512,'x'*131073,b'x'*131073,[0]*8193])
def test_native_unsupported_shape_keeps_original_valid_python_codec(select,value):
  select(False)
  expected=pack_primitive_snapshot(value)
  select(True)
  assert pack_primitive_snapshot(value)==expected


@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('kind',['foreign','cycle','nan','key','subclass'])
def test_both_paths_reject_without_invoking_foreign_callbacks(select,enabled,kind):
  calls=[]
  class Trap:
    def __reduce__(self):
      calls.append('reduce')
      raise AssertionError('foreign reducer')
  class ForeignList(list):
    def __iter__(self):
      calls.append('iter')
      raise AssertionError('foreign iterator')
  cycle=[]
  cycle.append(cycle)
  value={'foreign':Trap(),'cycle':cycle,'nan':float('nan'),'key':{1:'x'},'subclass':ForeignList()}[kind]
  select(enabled)
  with pytest.raises(ValueError):
    pack_primitive_snapshot(value)
  assert calls==[]
