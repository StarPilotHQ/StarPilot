import base64
import struct
from unittest import TestCase
from unittest.mock import patch

from openpilot.starpilot.flm import native_refinement_wire as wire


def packet():
  raw = struct.pack(wire.FORMAT, 1, 10000, 10001, 1, .01, *([0.] * 9))
  return (('value', 1.), (wire.FIELD, wire.PREFIX + base64.b64encode(raw).decode()))


class NativeMetadataProofTest(TestCase):
  def setUp(self):
    replacement = patch.object(wire, '_INPUT_PROOFS', ())
    replacement.start()
    self.addCleanup(replacement.stop)

  def test_reuses_only_the_same_validated_immutable_tuple(self):
    calls = []
    original = wire.decode

    def counted(value):
      calls.append(value)
      return original(value)

    replacement = patch.object(wire, 'decode', counted)
    replacement.start()
    self.addCleanup(replacement.stop)
    value = packet()
    for _ in range(3):
      assert wire.valid_inputs(value)
    assert len(calls) == 1
    equal = (value[0], value[1])
    assert equal == value and equal is not value
    assert wire.valid_inputs(equal)
    assert len(calls) == 2
    assert not wire.valid_inputs(value[:-1] + ((wire.FIELD, 'changed'),))


  def test_proof_retention_is_bounded_and_evicted_inputs_are_checked(self):
    first = packet()
    assert wire.valid_inputs(first)
    for i in range(12):
      assert wire.valid_inputs((('value', i),))
    assert len(wire._INPUT_PROOFS) == 4
    assert all(value is not first for value in wire._INPUT_PROOFS)
    assert wire.valid_inputs(first)


  def test_mutable_string_subclass_cannot_reuse_an_immutable_proof(self):

    class Name(str):
      matches = True

      def __eq__(self, other):
        return self.matches and super().__eq__(other)

      def __ne__(self, other):
        return not self.__eq__(other)

      __hash__ = str.__hash__

    name = Name(wire.FIELD)
    value = ((name, packet()[-1][1]),)
    assert wire.valid_inputs(value)
    assert not wire._INPUT_PROOFS
    name.matches = False
    assert not wire.valid_inputs(value)
