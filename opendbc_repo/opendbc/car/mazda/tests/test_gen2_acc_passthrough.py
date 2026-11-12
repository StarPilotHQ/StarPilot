"""GEN2 ACC passthrough tests (W4: inactive longitudinal must not synthesize commands).

The panda now rejects any 0x220 frame that is not a byte-exact copy of the stock
frame while openpilot longitudinal is inactive (see
reports/mazda-gen2-aol-gate-report.md section 10). These tests pin the controller
side of that contract: the relay path must be byte-identical, including when the
stock frame itself carries HOLD or RESUME, and the synthesis path must only be
reachable while openpilot actually commands longitudinal.
"""
import random

from opendbc.can import CANPacker, CANParser
from opendbc.car import structs
from opendbc.car.mazda import mazdacan
from opendbc.car.mazda.values import DBC, CAR, MazdaSafetyFlags

DBC_NAME = DBC[CAR.MAZDA_3_2019]["pt"]
PACKER = CANPacker(DBC_NAME)
STOCK = {"ACCEL_CMD": 2000, "ACC_ENABLED": 1, "ACC_ENABLED_2": 1, "HOLD": 0, "RESUME": 0}


def stock_payload(values):
  _, dat, _ = PACKER.make_can_msg("ACC", 2, values)
  return bytes(dat)


def test_passthrough_is_byte_identical_to_the_stock_frame():
  addr, dat, bus = mazdacan.create_acc_passthrough(PACKER, dict(STOCK))
  assert (addr, bus) == (0x220, 2)
  assert bytes(dat) == stock_payload(dict(STOCK))


def test_passthrough_preserves_a_stock_hold_and_resume():
  for stock_values in (dict(STOCK, HOLD=1), dict(STOCK, RESUME=1)):
    _, dat, _ = mazdacan.create_acc_passthrough(PACKER, dict(stock_values))
    assert bytes(dat) == stock_payload(dict(stock_values)), stock_values


def test_synthesis_changes_the_frame_so_the_two_paths_are_distinguishable():
  # Guards the tests above: if create_acc_cmd produced identical bytes, proving
  # passthrough identity would say nothing about the inactive path.
  _, synthesized, _ = mazdacan.create_acc_cmd(PACKER, dict(STOCK), True, False)
  assert bytes(synthesized) != stock_payload(dict(STOCK))


def test_synthesis_is_only_reachable_with_active_openpilot_longitudinal():
  cp_long = structs.CarParams(openpilotLongitudinalControl=True)
  cp_stock = structs.CarParams(openpilotLongitudinalControl=False)

  cc_active = structs.CarControl()
  cc_active.longActive = True
  cc_inactive = structs.CarControl()
  cc_inactive.longActive = False

  assert mazdacan.synthesizes_longitudinal(cp_long, cc_active)
  assert not mazdacan.synthesizes_longitudinal(cp_long, cc_inactive)
  assert not mazdacan.synthesizes_longitudinal(cp_stock, cc_active)
  assert not mazdacan.synthesizes_longitudinal(cp_stock, cc_inactive)


def test_the_isolation_rule_is_a_gen2_profile_rule():
  assert MazdaSafetyFlags.GEN2 == 2


def stock_frame(dat: bytes) -> bytes:
  """Eight-byte stock ACC frame with a valid mazda2019 checksum."""
  frame = bytearray(dat[:7])
  frame.append(mazdacan.mazda2019_checksum(0x220, None, frame))
  return bytes(frame)


def parsed_passthrough(frame: bytes) -> bytes:
  parser = CANParser(DBC_NAME, [("ACC", 0)], 2)
  parser.update([(0, [(0x220, frame, 2)])])
  _, dat, _ = mazdacan.create_acc_passthrough(PACKER, dict(parser.vl["ACC"]))
  return bytes(dat)


def test_passthrough_roundtrip_is_lossless_for_every_payload_bit():
  # The panda only accepts a byte-exact copy of the stock frame while
  # longitudinal is inactive, and the controller must rebuild that frame from
  # the parsed signal dict. Every payload bit (bytes 0..6) therefore has to be
  # covered by the DBC, or the relay silently zeroes it and the panda blocks
  # the stock ACC frame.
  for bit in range(56):
    frame = stock_frame(bytes(1 << (7 - bit % 8) if bit // 8 == i else 0 for i in range(7)))
    assert parsed_passthrough(frame) == frame, f"bit {bit} does not round-trip through the DBC"


def test_passthrough_roundtrip_is_lossless_for_random_stock_frames():
  rng = random.Random(0x220)
  for _ in range(500):
    frame = stock_frame(bytes(rng.getrandbits(8) for _ in range(7)))
    assert parsed_passthrough(frame) == frame, frame.hex()
