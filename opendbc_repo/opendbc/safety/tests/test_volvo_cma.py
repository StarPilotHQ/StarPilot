#!/usr/bin/env python3
"""
Safety tests for the Volvo CMA/SPA receiver layout (safetyParam 0): XC40 Recharge,
S60 Recharge and Polestar 2. The mode lives at ``opendbc/safety/modes/volvo_cma.h``
and is selected by ``volvo_dispatch.h``; C1 (V40) is covered by test_volvo_c1.py.

Exercised through the generic ``CarSafetyTest`` harness so divergence between
``carstate.py`` and ``volvo_cma.h`` is caught before running in a car.

Companion to: ``opendbc/car/volvo/carstate.py`` (must agree on thresholds).
"""

import pathlib
import re
import unittest

from opendbc.car.volvo.interface import SAFETY_VOLVO
from opendbc.car.volvo.values import VolvoSafetyFlags
from opendbc.safety.tests.libsafety import libsafety_py
import opendbc.safety.tests.common as common
from opendbc.car.volvo.helpers import pscm_status_supported
from opendbc.car.volvo.tests.test_cma import _pscm_related, checksummed_lca_2
from opendbc.car.volvo.tests.test_cma_integrity import LCA_2_FRAMES
from opendbc.safety.tests.common import CANPackerSafety

# Must match VOLVO_FLAG_C1 in opendbc/safety/modes/volvo_dispatch.h
VOLVO_FLAG_C1 = VolvoSafetyFlags.C1.value

# Must match VOLVO_SPEED_TO_MS in volvo_cma.h and SPEED_TO_MS in carstate.py
VOLVO_SPEED_TO_MS = 0.003977

# Bus layout (must match volvo_cma.h)
VOLVO_MAIN_BUS = 0
VOLVO_PT_BUS = 1
VOLVO_PARTY_BUS = 2

# Shared platform-independent addresses
VOLVO_SPEED         = 0x60
VOLVO_LCA_STEER     = 0x58
VOLVO_LCA_2         = 0x69
VOLVO_LCA_3         = 0x57
VOLVO_LCA_4         = 0x90
VOLVO_LCA_5         = 0x67
VOLVO_LCA_6         = 0x97
VOLVO_LCA_7         = 0x92
VOLVO_PSCM          = 0x16
VOLVO_PSCM_RELATED  = 0x17
VOLVO_SAS           = 0x55
VOLVO_DRIVER_INPUT  = 0x15

# Addresses panda TX'd with .check_relay = true, grouped by bus
_TX_PARTY_BUS_ADDRS = (
  VOLVO_LCA_STEER, VOLVO_LCA_2, VOLVO_LCA_3, VOLVO_LCA_4, VOLVO_LCA_5,
)
_TX_MAIN_BUS_ADDRS = (VOLVO_PSCM, VOLVO_PSCM_RELATED)

# Every TX'd address replaces a stock frame arriving on the opposite bus.
FWD_BUS = {VOLVO_MAIN_BUS: VOLVO_PARTY_BUS, VOLVO_PARTY_BUS: VOLVO_MAIN_BUS}
_REPLACED_STOCK_BUS = {
  **{a: VOLVO_MAIN_BUS for a in _TX_PARTY_BUS_ADDRS},
  **{a: VOLVO_PARTY_BUS for a in _TX_MAIN_BUS_ADDRS},
}
# Must match VOLVO_REPLACED_MSGS timeout_us in volvo_cma.h
_REPLACED_TIMEOUT_US = {
  VOLVO_LCA_STEER: 25000, VOLVO_PSCM: 25000, VOLVO_PSCM_RELATED: 25000,
  VOLVO_LCA_3: 50000, VOLVO_LCA_2: 50000, VOLVO_LCA_5: 50000,
  VOLVO_LCA_4: 100000,
}


class TestVolvoCMA(common.CarSafetyTest):
  """
  Volvo SPA: XC40 Recharge, S60 Recharge, Polestar 2.

  ECM_1.ACCELERATOR_PEDAL_POS is DBC-scaled to percent (factor 0.00390625,
  range 0-100%). Idle is ~0; panda fires gas_pressed above 1.0%.
  carstate.py MUST use the same percent-based threshold — see
  opendbc/car/volvo/carstate.py. A mismatch here manifests in test_models.py
  as persistent ``panda safety doesn't agree with openpilot: {'gasPressed': N}``.
  """

  TX_MSGS = (
    [[a, VOLVO_PARTY_BUS] for a in _TX_PARTY_BUS_ADDRS]
    + [[a, VOLVO_MAIN_BUS] for a in _TX_MAIN_BUS_ADDRS]
  )
  RELAY_MALFUNCTION_ADDRS = {
    VOLVO_PARTY_BUS: _TX_PARTY_BUS_ADDRS,
    VOLVO_MAIN_BUS: _TX_MAIN_BUS_ADDRS,
  }
  # volvo_cma.h installs volvo_cma_fwd_hook, which only blocks the stock version of a TX
  # addr while openpilot has recently sent its replacement. There's no static
  # (always-blocked) blacklist for SPA, so test_fwd_hook (nothing sent yet) sees
  # every address forward normally. See the test_fwd_hook_* tests below.
  STANDSTILL_THRESHOLD = 0.1  # m/s, matches volvo_cma.h
  GAS_PRESSED_THRESHOLD = 1.0  # percent

  SAFETY_PARAM = 0
  PT_DBC = "volvo_front_1_spa"
  MID_DBC = "volvo_mid_1"

  def setUp(self):
    self.pt_packer = CANPackerSafety(self.PT_DBC)
    self.mid_packer = CANPackerSafety(self.MID_DBC)
    # CarSafetyTest.packer is referenced by a few generic tests; point it at
    # the mid-bus DBC since most shared messages live there.
    self.packer = self.mid_packer
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(SAFETY_VOLVO, self.SAFETY_PARAM)
    self.safety.init_tests()

  # ---- Abstract methods from CarSafetyTest ----

  def _user_gas_msg(self, gas):
    values = {"ACCELERATOR_PEDAL_POS": gas}
    return self.pt_packer.make_can_msg_safety("ECM_1", VOLVO_PT_BUS, values)

  def _pcm_status_msg(self, enable):
    # SPA cruise signal has DBC factor -1 offset 1, so the "enabled" DBC value
    # is 1 (which encodes as raw bit 0 — panda inverts it).
    values = {"CRUISE_CONTROL_SPA_ENABLED": 1 if enable else 0}
    return self.pt_packer.make_can_msg_safety(
      "BUS1_CRUISE_CONTROL", VOLVO_PT_BUS, values)

  def _user_brake_msg(self, brake):
    # volvo_cma.h reads BRAKE_PEDAL_PRESSED_B (bit 46, factor 1, active high)
    # LCA_2 checksums are validated (volvo_cma_integrity.h), so build it as the car does.
    address, data, bus = checksummed_lca_2({"BRAKE_PEDAL_PRESSED_B": 1 if brake else 0})
    return libsafety_py.make_CANPacket(address, bus, data)

  def _speed_msg(self, speed):
    # Main-bus SPEED, the same message on both platforms. The DBC carries it as
    # raw counts (factor 1) and volvo_cma.h applies VOLVO_SPEED_TO_MS, so convert here.
    values = {"SPEED": round(speed / VOLVO_SPEED_TO_MS)}
    return self.mid_packer.make_can_msg_safety("SPEED", VOLVO_MAIN_BUS, values)

  def _speed_msg_2(self, speed):
    # Volvo's safety mode only consumes a single vehicle-speed source, so the
    # generic rx_hook_speed_mismatch test doesn't apply. Returning None asks
    # the harness to skip it (see common.CarSafetyTest.test_rx_hook_speed_mismatch).
    return None

  def _angle_meas_msg(self, angle):
    return self.mid_packer.make_can_msg_safety(
      "PSCM", VOLVO_PARTY_BUS, {"PSCM_ANGLE_SENSOR": angle})

  def _angle_cmd_msg(self, angle):
    return self.mid_packer.make_can_msg_safety(
      "LCA_5", VOLVO_PARTY_BUS, {"LCA_5_STEER": angle})

  def _reset_angle_measurement(self, angle):
    for _ in range(common.MAX_SAMPLE_VALS):
      self._rx(self._angle_meas_msg(angle))

  def _reset_speed_measurement(self, speed):
    for _ in range(common.MAX_SAMPLE_VALS):
      self._rx(self._speed_msg(speed))

  def test_angle_tx_is_bounded(self):
    """The LCA_5 angle command must obey the measured-angle safety envelope."""
    self._reset_angle_measurement(0)
    self._reset_speed_measurement(10)

    self.safety.set_controls_allowed(True)
    self.safety.set_desired_angle_last(0)
    self.assertTrue(self._tx(self._angle_cmd_msg(0)))

    # The software controller's ±540 degree envelope is also enforced by the
    # panda. Set the previous command explicitly so this test isolates the
    # absolute bound from the rate limiter.
    angle_can = round(540 / 0.05596)
    self.safety.set_desired_angle_last(angle_can)
    self.assertTrue(self._tx(self._angle_cmd_msg(540)))
    self.safety.set_desired_angle_last(0)
    self.assertFalse(self._tx(self._angle_cmd_msg(541)))

  def test_angle_tx_follows_measurement_when_controls_disabled(self):
    self._reset_angle_measurement(10)
    self.safety.set_controls_allowed(False)

    self.assertTrue(self._tx(self._angle_cmd_msg(10)))
    self.assertFalse(self._tx(self._angle_cmd_msg(20)))

  def test_angle_tx_rate_matches_controller_cadence(self):
    """LCA_5 is 50 Hz, so each frame may contain two 100 Hz controller steps."""
    self._reset_speed_measurement(50)
    self.safety.set_controls_allowed(True)

    self.safety.set_desired_angle_last(0)
    self.assertTrue(self._tx(self._angle_cmd_msg(0.4)))

    self.safety.set_desired_angle_last(round(0.4 / 0.05596))
    self.assertFalse(self._tx(self._angle_cmd_msg(0.9)))

  def test_lca_authority_is_bounded(self):
    self.safety.set_controls_allowed(True)
    valid = {
      "RIGHT_TORQUE_AUTHORITY": 614,
      "LEFT_TORQUE_AUTHORITY": -614,
      "LCA_STEER": 0,
    }
    self.assertTrue(self._tx(self.mid_packer.make_can_msg_safety(
      "LCA", VOLVO_PARTY_BUS, valid)))

    for key, value in (("RIGHT_TORQUE_AUTHORITY", 615),
                       ("LEFT_TORQUE_AUTHORITY", -615)):
      invalid = valid | {key: value}
      self.assertFalse(self._tx(self.mid_packer.make_can_msg_safety(
        "LCA", VOLVO_PARTY_BUS, invalid)))

  def test_pscm_relay_cannot_invent_angle(self):
    self._reset_angle_measurement(10)
    valid = self.mid_packer.make_can_msg_safety(
      "PSCM", VOLVO_MAIN_BUS, {"PSCM_ANGLE_SENSOR": 10})
    invalid = self.mid_packer.make_can_msg_safety(
      "PSCM", VOLVO_MAIN_BUS, {"PSCM_ANGLE_SENSOR": 20})
    self.assertTrue(self._tx(valid))
    self.assertFalse(self._tx(invalid))

  def test_driver_input_is_a_normal_override(self):
    def driver_input_msg(value):
      return self.mid_packer.make_can_msg_safety(
        "DRIVER_INPUT", VOLVO_PARTY_BUS, {"STEERING_DRIVER_INPUT": value})

    for value in (2, 3, 5, 6, 20, -20):
      self._rx(driver_input_msg(0))
      self.safety.set_controls_allowed(True)
      self._rx(driver_input_msg(value))
      self.assertTrue(self.safety.get_controls_allowed(), f"unexpected safety disengage at {value=}")
      self.assertFalse(self.safety.get_steering_disengage_prev())

  # ---- Volvo-specific consistency tests ----

  def _tx_replacement(self, addr):
    bus = VOLVO_PARTY_BUS if addr in _TX_PARTY_BUS_ADDRS else VOLVO_MAIN_BUS
    return self._tx(common.make_msg(bus, addr))

  def test_fwd_hook_forwards_stock_until_openpilot_sends(self):
    """Being allowed to steer isn't enough to block stock traffic: openpilot has to actually send."""
    self.safety.set_controls_allowed(True)
    for addr, stock_bus in _REPLACED_STOCK_BUS.items():
      self.assertEqual(FWD_BUS[stock_bus], self.safety.safety_fwd_hook(stock_bus, addr), hex(addr))

  def test_fwd_hook_blocks_stock_while_openpilot_sends(self):
    self.safety.set_controls_allowed(True)
    for addr, stock_bus in _REPLACED_STOCK_BUS.items():
      with self.subTest(addr=hex(addr)):
        self.safety.set_timer(1_000_000)
        self.assertTrue(self._tx_replacement(addr))
        self.assertEqual(-1, self.safety.safety_fwd_hook(stock_bus, addr))

        # still blocked right up to the timeout, then stock takes over again
        self.safety.set_timer(1_000_000 + _REPLACED_TIMEOUT_US[addr])
        self.assertEqual(-1, self.safety.safety_fwd_hook(stock_bus, addr))
        self.safety.set_timer(1_000_000 + _REPLACED_TIMEOUT_US[addr] + 1)
        self.assertEqual(FWD_BUS[stock_bus], self.safety.safety_fwd_hook(stock_bus, addr))

  def test_fwd_hook_stall_hands_back_to_stock(self):
    """An openpilot stall (no sends for 150 ms) must not leave any replaced address silent."""
    self.safety.set_controls_allowed(True)
    self.safety.set_timer(1_000_000)
    for addr in _REPLACED_STOCK_BUS:
      self.assertTrue(self._tx_replacement(addr))
    self.safety.set_timer(1_000_000 + 150_000)
    self.assertTrue(self.safety.get_controls_allowed())
    for addr, stock_bus in _REPLACED_STOCK_BUS.items():
      self.assertEqual(FWD_BUS[stock_bus], self.safety.safety_fwd_hook(stock_bus, addr), hex(addr))

  def test_fwd_hook_only_blocks_the_stock_side(self):
    """Replacing an address only blocks it arriving from the stock side, not the other way."""
    self.safety.set_controls_allowed(True)
    self.safety.set_timer(1_000_000)
    for addr, stock_bus in _REPLACED_STOCK_BUS.items():
      self.assertTrue(self._tx_replacement(addr))
      other_bus = FWD_BUS[stock_bus]
      self.assertEqual(stock_bus, self.safety.safety_fwd_hook(other_bus, addr), hex(addr))

  def test_fwd_hook_ignores_rejected_tx(self):
    """A replacement the safety rejects never reaches the bus, so the stock frame keeps forwarding."""
    self._reset_angle_measurement(0)
    self.safety.set_controls_allowed(False)
    self.safety.set_desired_angle_last(0)
    self.assertFalse(self._tx(self._angle_cmd_msg(20)))
    self.assertEqual(VOLVO_PARTY_BUS, self.safety.safety_fwd_hook(VOLVO_MAIN_BUS, VOLVO_LCA_5))

  def test_fwd_hook_timeout_across_timer_wrap(self):
    self.safety.set_controls_allowed(True)
    start = 0xFFFFF000
    self.safety.set_timer(start)
    self.assertTrue(self._tx_replacement(VOLVO_LCA_STEER))
    self.safety.set_timer((start + _REPLACED_TIMEOUT_US[VOLVO_LCA_STEER]) & 0xFFFFFFFF)
    self.assertEqual(-1, self.safety.safety_fwd_hook(VOLVO_MAIN_BUS, VOLVO_LCA_STEER))
    self.safety.set_timer((start + _REPLACED_TIMEOUT_US[VOLVO_LCA_STEER] + 1) & 0xFFFFFFFF)
    self.assertEqual(VOLVO_PARTY_BUS, self.safety.safety_fwd_hook(VOLVO_MAIN_BUS, VOLVO_LCA_STEER))

  def test_fwd_hook_cleared_on_safety_init(self):
    self.safety.set_controls_allowed(True)
    self.safety.set_timer(1_000_000)
    self.assertTrue(self._tx_replacement(VOLVO_LCA_STEER))
    self.safety.set_safety_hooks(SAFETY_VOLVO, self.SAFETY_PARAM)
    self.assertEqual(VOLVO_PARTY_BUS, self.safety.safety_fwd_hook(VOLVO_MAIN_BUS, VOLVO_LCA_STEER))

  def test_unmodified_lca_messages_are_forwarded_not_sent(self):
    """LCA_6/LCA_7 aren't changed by openpilot, so they're forwarded and never TX'able."""
    self.safety.set_controls_allowed(True)
    for addr in (VOLVO_LCA_6, VOLVO_LCA_7):
      self.assertFalse(self._tx(common.make_msg(VOLVO_PARTY_BUS, addr)))
      self.assertEqual(VOLVO_PARTY_BUS, self.safety.safety_fwd_hook(VOLVO_MAIN_BUS, addr))

  def test_tx_hook_wrong_bus_blocked(self):
    """
    Every TX address is pinned to exactly one bus by the VOLVO_TX_MSGS
    allowlist, which safety_tx_hook() enforces before volvo_tx_hook() runs.
    TX attempts anywhere off the allowlisted bus must be rejected, even with
    controls allowed. Also covers the relayed-RX addresses (SAS, DRIVER_INPUT)
    that volvo_tx_hook() used to re-check: they are not TX'able on any bus.
    """
    tx_msgs = {tuple(m) for m in self.TX_MSGS}
    addrs = {addr for addr, _ in tx_msgs} | {VOLVO_SAS, VOLVO_DRIVER_INPUT}
    self.safety.set_controls_allowed(True)
    for addr in sorted(addrs):
      for bus in range(4):
        if (addr, bus) in tx_msgs:
          self.assertTrue(self._tx(common.make_msg(bus, addr)),
                          f"blocked TX addr={hex(addr)} on allowlisted bus={bus}")
        else:
          self.assertFalse(self._tx(common.make_msg(bus, addr)),
                           f"allowed TX addr={hex(addr)} on wrong bus={bus}")

  def test_gas_threshold_self_consistent(self):
    """
    Sanity: the panda's gas_pressed edge is exactly where GAS_PRESSED_THRESHOLD
    is defined, and carstate.py must use the same percent threshold.
    If you change the threshold in volvo_cma.h, update carstate.py AND this test.
    """
    # Just below threshold → not pressed. Use a value strictly less than threshold.
    just_below = max(self.GAS_PRESSED_THRESHOLD - 1, 0)
    self._rx(self._user_gas_msg(just_below))
    self.assertFalse(self.safety.get_gas_pressed_prev(),
                     f"gas flagged at {just_below} (threshold={self.GAS_PRESSED_THRESHOLD})")

    # Just above threshold → pressed.
    self._rx(self._user_gas_msg(self.GAS_PRESSED_THRESHOLD + 1))
    self.assertTrue(self.safety.get_gas_pressed_prev(),
                    f"gas not flagged above {self.GAS_PRESSED_THRESHOLD}")

  def test_speed_scale_self_consistent(self):
    """
    The main-bus SPEED LSB is written out in three places: volvo_cma.h, carstate.py
    and this file. They must agree, or the panda and openpilot disagree about
    vehicle speed. Parsed as text so this holds without importing either.
    """
    root = pathlib.Path(__file__).parents[3]
    sources = {
      "volvo_cma.h": (root / "opendbc/safety/modes/volvo_cma.h",
                  r"#define\s+VOLVO_SPEED_TO_MS\s+([0-9.]+)f?"),
      "carstate.py": (root / "opendbc/car/volvo/carstate.py",
                      r"^SPEED_TO_MS\s*=\s*([0-9.]+)"),
    }
    for name, (path, pattern) in sources.items():
      m = re.search(pattern, path.read_text(), re.MULTILINE)
      self.assertIsNotNone(m, f"could not find the speed LSB in {name}")
      self.assertEqual(float(m.group(1)), VOLVO_SPEED_TO_MS,
                       f"{name} speed LSB disagrees with test_volvo_cma.py")


class TestVolvoCMAIntegrity(unittest.TestCase):
  """Receive checksums (LCA_2, PSCM_RELATED) and the rack-status rule in volvo_cma_integrity.h."""

  def setUp(self):
    self.pt_packer = CANPackerSafety("volvo_front_1_spa")
    self.safety = libsafety_py.libsafety
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.assertEqual(self.safety.set_safety_hooks(SAFETY_VOLVO, 0), 0)

  def _rx(self, address, data, bus):
    return self.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, bytes(data)))

  def _cruise(self, enabled):
    return self.safety.safety_rx_hook(self.pt_packer.make_can_msg_safety(
      "BUS1_CRUISE_CONTROL", VOLVO_PT_BUS, {"CRUISE_CONTROL_SPA_ENABLED": int(enabled)}))

  def test_lca_2_frames_captured_on_the_car_are_accepted(self):
    for frame in LCA_2_FRAMES:
      with self.subTest(frame=frame):
        self.assertTrue(self._rx(VOLVO_LCA_2, bytes.fromhex(frame), VOLVO_MAIN_BUS))

  def test_corrupt_lca_2_is_rejected_and_its_brake_is_never_parsed(self):
    address, data, bus = checksummed_lca_2({"BRAKE_PEDAL_PRESSED_B": 1})
    for byte in (6, 2, 5):  # either checksum, or the brake byte under checksum 1
      with self.subTest(byte=byte):
        self.setUp()
        corrupt = bytearray(data)
        corrupt[byte] ^= 1 if byte != 5 else 0x40  # byte 5: clear the brake bit itself
        self.safety.set_controls_allowed(True)
        self.assertFalse(self._rx(address, corrupt, bus))
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertFalse(self.safety.get_brake_pressed_prev())

  def test_normal_engagement_drops_on_an_invalid_frame_and_needs_a_new_engagement(self):
    self.assertTrue(self._cruise(False))
    self.assertTrue(self._cruise(True))
    self.assertTrue(self.safety.get_controls_allowed())
    address, data, bus = checksummed_lca_2()
    corrupt = bytearray(data)
    corrupt[2] ^= 1
    self.assertFalse(self._rx(address, corrupt, bus))
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertTrue(self._rx(address, data, bus))
    self.assertTrue(self._cruise(True))
    self.assertFalse(self.safety.get_controls_allowed(), "stock cruise still on: no new rising edge")
    self.assertTrue(self._cruise(False))
    self.assertTrue(self._cruise(True))
    self.assertTrue(self.safety.get_controls_allowed())

  def test_pscm_related_panda_and_host_agree_on_every_status_byte(self):
    """Exhaustive over bytes 1/2 with valid checksums: panda accepts exactly what
    carstate.py's pscm_status_supported treats as supported (else the two would disagree
    and openpilot would disengage on a controls mismatch)."""
    for b1 in range(256):
      for b2 in range(256):
        frame = _pscm_related(b1, b2)
        msg = {"RACK_COUNTER": b1 >> 4, "LCA_ENABLED_ECHO": b1 & 0xF, "PSCM_STATUS": b2 >> 4,
               "RACK_COUNTER_REPLICA": b2 & 0xF, "CHECKSUM_1": frame[0], "CHECKSUM_2": frame[3]}
        host = pscm_status_supported(msg, frame)
        panda = self._rx(VOLVO_PSCM_RELATED, frame, VOLVO_PARTY_BUS)
        if host != panda:
          self.fail(f"b1={b1:#x} b2={b2:#x}: host={host} panda={panda}")
    self.assertTrue(self._rx(VOLVO_PSCM_RELATED, _pscm_related(0x51, 0x84), VOLVO_PARTY_BUS), "replica one behind")

  def test_corrupt_pscm_related_checksum_is_rejected(self):
    frame = _pscm_related(0x51, 0x85)
    self.assertTrue(self._rx(VOLVO_PSCM_RELATED, frame, VOLVO_PARTY_BUS))
    for byte in (0, 3):
      with self.subTest(byte=byte):
        corrupt = bytearray(frame)
        corrupt[byte] ^= 1
        self.assertFalse(self._rx(VOLVO_PSCM_RELATED, corrupt, VOLVO_PARTY_BUS))

  def test_c1_ignores_cma_checksum_domains(self):
    self.assertEqual(self.safety.set_safety_hooks(SAFETY_VOLVO, VOLVO_FLAG_C1), 0)
    corrupt = bytearray(_pscm_related(0x51, 0x85))
    corrupt[0] ^= 1
    # 0x17 isn't a C1 receiver at all; the CMA checks must not leak into the C1 hooks.
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(VOLVO_PSCM_RELATED, VOLVO_PARTY_BUS, bytes(corrupt))))


if __name__ == "__main__":
  unittest.main()
