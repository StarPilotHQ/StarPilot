"""Unsupported rack status (PSCM_RELATED) is a temporary steer fault, which withdraws
openpilot's commands so panda forwards the stock frames until the rack recovers."""
import unittest
from opendbc.can.parser import CANParser
from opendbc.car.volvo.helpers import (
  pscm_status_supported,
)
from opendbc.car.volvo.tests.test_cma import _make_state, _pscm_related, _step
from opendbc.car.volvo.tests.test_cma_integrity import PSCM_RELATED_CHECKSUM_1, PSCM_RELATED_CHECKSUM_2


# Counter 5, LCA_ENABLED_ECHO 1, status 8, replica 5 (matches a frame seen on the car).
_PSCM_SUPPORTED = _pscm_related(0x51, 0x85)


def _make_state_with_pscm_registered():
  # The Volvo parsers subscribe lazily on first access, which happens in the first
  # CarState.update(); frames before that aren't parsed.
  cs, parsers, packers = _make_state()
  cs.update(parsers)
  return cs, parsers, packers


def test_pscm_supported_status_has_no_steer_fault():
  cs, parsers, packers = _make_state_with_pscm_registered()
  ret = _step(cs, parsers, packers, 0, False, _PSCM_SUPPORTED)
  assert ret.steerFaultTemporary is False


def test_pscm_unsupported_status_is_a_temporary_steer_fault_until_it_recovers():
  cs, parsers, packers = _make_state_with_pscm_registered()
  quality_bit = _pscm_related(0x51 | 0x8, 0x85)  # valid checksums, quality bit set
  for t, frame, fault in ((0, _PSCM_SUPPORTED, False), (10_000, quality_bit, True), (20_000, _PSCM_SUPPORTED, False)):
    ret = _step(cs, parsers, packers, t, False, frame)
    assert ret.steerFaultTemporary is fault, frame.hex()


def test_pscm_replica_lagging_one_count_is_not_a_steer_fault():
  """The rack sometimes sends RACK_COUNTER_REPLICA one count behind with valid checksums.
  Treating that as unsupported caused false disengages on a real Polestar drive."""
  cs, parsers, packers = _make_state_with_pscm_registered()
  for i in range(45):
    counter = i % 15
    replica = (counter - (i % 2)) % 15  # alternate equal / one behind, across the wrap
    ret = _step(cs, parsers, packers, i * 10_000, False, _pscm_related((counter << 4) | 1, 0x80 | replica))
    assert ret.steerFaultTemporary is False, (counter, replica)


def test_pscm_never_received_is_a_temporary_steer_fault():
  cs, parsers, packers = _make_state()
  ret = cs.update(parsers)
  assert ret.steerFaultTemporary is True


def _decode(frame):
  """(signals, raw frame) for a PSCM_RELATED frame, decoded the way carstate gets it."""
  parser = CANParser("volvo_mid_1", [("PSCM_RELATED", 0)], 0)
  parser.update([(0, [(0x17, frame, 0)])])
  return dict(parser.vl["PSCM_RELATED"]), bytes(frame)


class TestPscmStatusSupported(unittest.TestCase):
  def test_signal_names_match_the_nibbles(self):
    msg, _ = _decode(_pscm_related(0x51, 0x85))
    assert (msg["RACK_COUNTER"], msg["LCA_ENABLED_ECHO"]) == (5, 1)  # byte 1 high / low nibble
    assert (msg["PSCM_STATUS"], msg["RACK_COUNTER_REPLICA"]) == (8, 5)  # byte 2 high / low nibble

  def test_frames_seen_on_the_car_are_supported(self):
    for (b1, b2), cs1 in PSCM_RELATED_CHECKSUM_1.items():
      frame = bytes([cs1, b1, b2, PSCM_RELATED_CHECKSUM_2[b2], 0, 0, 0, 0])
      with self.subTest(frame=frame.hex()):
        assert pscm_status_supported(*_decode(frame))

  def test_each_condition_alone_makes_it_unsupported(self):
    base = _pscm_related(0x51, 0x85)  # RACK_COUNTER 5, LCA_ENABLED_ECHO 1, PSCM_STATUS 8, replica 5
    assert pscm_status_supported(*_decode(base))
    cases = {
      "bad checksum 1": bytes([base[0] ^ 1]) + base[1:],
      "bad checksum 2": base[:3] + bytes([base[3] ^ 1]) + base[4:],
      "PSCM_STATUS 9": _pscm_related(0x51, 0x95),
      "LCA_ENABLED_ECHO top bit set": _pscm_related(0x59, 0x85),
      "replica one ahead of RACK_COUNTER": _pscm_related(0x51, 0x86),
      "replica 15": _pscm_related(0xF1, 0x8F),
      "never received (zeros)": bytes(8),
    }
    for name, frame in cases.items():
      with self.subTest(name):
        assert not pscm_status_supported(*_decode(frame))

  def test_rack_counter_replica_equal_or_one_behind_is_supported(self):
    for counter in range(15):
      for lag in (0, 1):
        replica = (counter - lag) % 15
        with self.subTest(counter=counter, replica=replica):
          assert pscm_status_supported(*_decode(_pscm_related((counter << 4) | 1, 0x80 | replica)))

  def test_other_rack_counter_relationships_are_unsupported(self):
    # replica ahead, two or more behind, or a counter of 15 (never valid)
    for counter, replica in ((15, 14), (15, 0), (0, 1), (0, 13), (1, 14), (5, 3), (14, 15)):
      with self.subTest(counter=counter, replica=replica):
        assert not pscm_status_supported(*_decode(_pscm_related((counter << 4) | 1, 0x80 | replica)))

  def test_short_frame_is_unsupported(self):
    msg, raw = _decode(_pscm_related(0x51, 0x85))
    assert not pscm_status_supported(msg, raw[:7])
