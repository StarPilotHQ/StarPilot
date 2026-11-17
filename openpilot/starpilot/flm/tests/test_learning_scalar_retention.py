"""Retained learning evidence shares content without changing any source value."""
from dataclasses import replace
import struct

import pytest
from openpilot.cereal import custom, messaging
from openpilot.starpilot.flm import learning_wire as wire
from openpilot.starpilot.flm.tests.test_fullrate_pipeline import snapshots, BASE


@pytest.fixture(autouse=True)
def clear_scalar_cache():
  wire._scalar_pair.cache_clear()
  yield
  wire._scalar_pair.cache_clear()


def read(values):
  row = custom.LearningControlEvidence.new_message(replayInputs=values)
  return wire._read_named_values(row.as_reader().replayInputs, 32)


def test_independent_cereal_decodes_share_exact_immutable_pairs():
  values = [{'name': 'speed', 'number': 20.5}, {'name': 'active', 'boolean': True},
            {'name': 'mode', 'text': 'stock'}]
  first, second = read(values), read(values)
  assert first == second == (('speed', 20.5), ('active', True), ('mode', 'stock'))
  assert all(a is b for a, b in zip(first, second, strict=True))


@pytest.mark.parametrize('bits', ('0000000000000000', '8000000000000000', '3ff0000000000000',
                                 '7ff0000000000000', 'fff0000000000000', '7ff8000000001234'))
def test_numeric_cache_preserves_ieee_bits(bits):
  value = struct.unpack('!d', bytes.fromhex(bits))[0]
  decoded = read([{'name': 'coordinate', 'number': value}])[0][1]
  assert type(decoded) is float and struct.pack('!d', decoded).hex() == bits


def test_boolean_number_text_and_signed_zero_do_not_alias():
  values = [wire._shared_scalar_pair('same', v) for v in (False, 0., -0., '0', True, 1.)]
  assert len({id(v) for v in values}) == 6
  assert [type(v[1]) for v in values] == [bool, float, float, str, bool, float]


def test_original_wire_rejections_and_finite_input_checks_remain():
  for name in ('', 'x' * 97):
    with pytest.raises(ValueError, match='bounded string'):
      read([{'name': name, 'number': 1.}])
  assert wire._scalar_pair.cache_info().currsize == 0
  with pytest.raises(ValueError, match='replay scalar bounds'):
    read([{'name': 'speed', 'number': 1.}] * 33)
  row = custom.LearningControlEvidence.new_message(replayInputs=[{'name': 'speed', 'number': float('nan')}])
  with pytest.raises(ValueError, match='original replay input bounds'):
    wire._read_original_inputs(row.as_reader().replayInputs)


def test_cache_is_bounded_and_eviction_does_not_change_retained_values():
  original = wire._shared_scalar_pair('sample', -0.)
  for index in range(5000):
    wire._shared_scalar_pair('sample', float(index))
  assert wire._scalar_pair.cache_info().currsize == 4096
  assert struct.pack('!d', original[1]) == struct.pack('!d', -0.)
  assert wire._shared_scalar_pair('sample', -0.) == original
  for name, value in (('x' * 97, 0.), ('sample', 'x' * 97)):
    before = wire._scalar_pair.cache_info()
    assert wire._shared_scalar_pair(name, value) == (name, value)
    assert wire._scalar_pair.cache_info() == before


def test_control_metadata_and_tuning_values_share_content_but_not_live_verdicts():
  original, _, _ = snapshots(BASE)
  original = replace(original, stage_values=(('factor', -0.), ('limit', 1.)))
  def decode(value):
    event = messaging.new_message('starpilotLateralState', valid=True)
    wire.write_control(event.starpilotLateralState, value)
    return wire.read_control(messaging.log_from_bytes(event.to_bytes()))
  first, second = decode(original), decode(original)
  assert first == second == original
  assert first.identity is second.identity and first.source_epoch is second.source_epoch
  assert all(a is b for a, b in zip(first.stage_values, second.stage_values, strict=True))
  changed = replace(original, control_ns=BASE + 10_000_000, state_ns=BASE + 9_000_000,
                    native_allowed=False, valid=False, fault=True, stage_values=(('factor', 0.), ('limit', 2.)))
  result = decode(changed)
  assert result == changed and result != first
  assert not result.native_allowed and not result.valid and result.fault
  assert struct.pack('!d', result.stage_values[0][1]) != struct.pack('!d', first.stage_values[0][1])
