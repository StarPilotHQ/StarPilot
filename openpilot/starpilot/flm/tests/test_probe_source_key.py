"""Exact C source keys against the unchanged Python fallback and refusals."""
import importlib
import math
import struct
import pytest
from openpilot.starpilot.flm import native_primitives
from openpilot.starpilot.flm.live_tracking_probe import _primitive


@pytest.fixture
def native(monkeypatch):
  monkeypatch.setattr(native_primitives, '_enabled', False)
  return importlib.import_module('openpilot.starpilot.flm._primitive_walk')


def test_complete_typed_values_and_float_bits(native):
  values = [None, False, True, 0, 1, -(2**511), 2**511, 0., -0., math.ldexp(1., -1074),
            'é😀\ud800', b'\0\xff', (), [], {}, (True, 1, 1.), {'z': [-0., b'x'], 'a': (0.,)}]
  for value in values:
    assert native.probe_source_key(value) == _primitive(value)
    assert native.probe_source_valid(value) is True
  assert native.probe_source_key(True) != native.probe_source_key(1)
  assert native.probe_source_key((1,)) != native.probe_source_key([1])
  assert native.probe_source_key(0.) == ('float64', struct.pack('!d', 0.))
  assert native.probe_source_key(-0.) != native.probe_source_key(0.)


def test_mutable_aliases_are_fresh_complete_keys(native):
  mutable = [False, 0., 'before']
  shared: dict[str, object] = {'values': mutable}
  value = (shared, shared)
  before = native.probe_source_key(value)
  mutable[1] = -0.
  mutable[2] = 'after'
  after = native.probe_source_key(value)
  assert before != after and after == _primitive(value)
  value[0]['new'] = {'complete': [1, 2, 3]}
  assert native.probe_source_key(value) == _primitive(value)


@pytest.mark.parametrize('value', [2**512, 'x'*131073, b'x'*131073, [0]*8193, float('inf'), float('nan'), {1:'x'}])
def test_original_bounds_refuse_without_substituting_a_key(native, value):
  assert native.probe_source_key(value) is native.FALLBACK
  assert native.probe_source_valid(value) is native.FALLBACK
  with pytest.raises(ValueError):
    _primitive(value)


def test_depth_cycles_and_subclasses_keep_python_refusals(native):
  class Trap(list):
    def __iter__(self):
      raise AssertionError('subclass callback')
  cycle = []
  cycle.append(cycle)
  boundary = 0
  for _ in range(40):
    boundary = [boundary]
  assert native.probe_source_key(boundary) == _primitive(boundary)
  for value in (cycle, [boundary], Trap()):
    assert native.probe_source_key(value) is native.FALLBACK
    assert native.probe_source_valid(value) is native.FALLBACK
    with pytest.raises(ValueError):
      _primitive(value)
