#!/usr/bin/env python3
# Adapted from Alexandre Sato's GWM Haval H6 port; see the repository root
# CREDITS.md and THIRD_PARTY_NOTICES.md for provenance.
import unittest

from opendbc.can.dbc import DBC
from opendbc.car.gwm.gwmcan import checksum as _checksum
from opendbc.car.gwm.values import GwmFlags
from opendbc.car.structs import CarParams
import opendbc.safety.tests.common as common

GWM_DBC = "gwm_haval_h6_mk3"
dbc = DBC(GWM_DBC)


def clip_to_signal(msg_name, sig_name, value):
  """Keep a test value inside the signal's representable range, so the packer
  does not raise before the safety hook gets a chance to judge the value."""
  sig = dbc.name_to_msg[msg_name].sigs[sig_name]
  if sig.is_signed:
    raw_min, raw_max = -(2 ** (sig.size - 1)), (2 ** (sig.size - 1)) - 1
  else:
    raw_min, raw_max = 0, (2 ** sig.size) - 1
  lo = raw_min * sig.factor + sig.offset
  hi = raw_max * sig.factor + sig.offset
  return max(min(value, hi), lo)


def fix_checksum(msg):
  addr, dat, bus = msg
  ret = bytearray(dat)
  if addr == 0xA1:    # STEER_AND_AP_STALK
    ret[0] = _checksum(ret[1:8], 0x2D)
  elif addr == 0x13B:  # WHEEL_SPEEDS
    ret[0] = _checksum(ret[1:8], 0x7F)
  return addr, ret, bus


class TestGwmSafety(common.CarSafetyTest, common.MotorTorqueSteeringSafetyTest,
                    common.LongitudinalGasBrakeSafetyTest):
  DBC = GWM_DBC
  SAFETY_MODEL = CarParams.SafetyModel.gwm
  SAFETY_PARAM = GwmFlags.LONG_CONTROL

  # Steer command, longitudinal command, HUD on bus 0; EPS feedback and the
  # stalk stream are re-emitted toward the camera on bus 2.
  TX_MSGS = [[0x12B, 0], [0x143, 0], [0x23D, 0], [0x147, 2], [0xA1, 2]]
  RELAY_MALFUNCTION_ADDRS = {0: (0x12B, 0x143, 0x23D), 2: (0x147, 0xA1)}
  FWD_BLACKLISTED_ADDRS = {0: [0x147, 0xA1], 2: [0x12B, 0x143, 0x23D]}

  MAX_RATE_UP = 4
  MAX_RATE_DOWN = 6
  MAX_TORQUE_LOOKUP = [0], [253]
  MAX_RT_DELTA = 100
  MAX_TORQUE_ERROR = 80
  TORQUE_MEAS_TOLERANCE = 1

  MIN_GAS = -10
  MAX_GAS = 4577
  MIN_POSSIBLE_GAS = -11
  MAX_POSSIBLE_GAS = 4600  # reasonably excessive limits, not signal max
  INACTIVE_GAS = 0

  MAX_BRAKE = 107
  MAX_POSSIBLE_BRAKE = 180

  def _user_gas_msg(self, gas):
    return self.packer.make_can_msg_safety("CAR_OVERALL_SIGNALS2", 0, {"GAS_POSITION": gas})

  def _user_brake_msg(self, brake):
    return self.packer.make_can_msg_safety("BRAKE2", 0, {"PEDAL_BRAKE_PRESSED": brake})

  def _speed_msg(self, speed):
    values = {f"{pos}_WHEEL_SPEED": speed * 1.0 for pos in ("FRONT_LEFT", "FRONT_RIGHT", "REAR_LEFT", "REAR_RIGHT")}
    return self.packer.make_can_msg_safety("WHEEL_SPEEDS", 0, values, fix_checksum=fix_checksum)

  def _pcm_status_msg(self, enable):
    # Engagement is button-style on this car: the stalk pull latches controls,
    # the stalk push (cancel) clears them.
    values = {"AP_ENABLE_COMMAND": enable, "AP_CANCEL_COMMAND": not enable}
    return self.packer.make_can_msg_safety("STEER_AND_AP_STALK", 0, values, fix_checksum=fix_checksum)

  def _torque_meas_msg(self, torque):
    torque = clip_to_signal("RX_STEER_RELATED", "B_RX_EPS_TORQUE", torque)
    return self.packer.make_can_msg_safety("RX_STEER_RELATED", 0, {"B_RX_EPS_TORQUE": torque})

  def _torque_cmd_msg(self, torque, steer_req=1):
    torque = clip_to_signal("STEER_CMD", "TORQUE_CMD", torque)
    values = {"STEER_REQUEST": steer_req, "TORQUE_CMD": torque}
    return self.packer.make_can_msg_safety("STEER_CMD", 0, values)

  def _send_brake_msg(self, brake):
    return self.packer.make_can_msg_safety("ACC_CMD", 0, {"BRAKE_CMD": -brake, "GAS_CMD": 0})

  def _send_gas_msg(self, gas):
    return self.packer.make_can_msg_safety("ACC_CMD", 0, {"GAS_CMD": gas, "BRAKE_CMD": 0})

  def test_cruise_engaged_prev(self):
    # Engagement here is button-style, like gm and hyundai: the stalk rising edge
    # latches controls_allowed directly instead of going through
    # pcm_cruise_check, so cruise_engaged_prev is deliberately never updated.
    # Routing it through pcm_cruise_check instead left controls_allowed latched
    # false after a brake-only disengage, because the brake clears
    # controls_allowed without clearing acc_main_on, so the next stalk pull
    # produced no rising edge - a permanent "Controls Mismatch". The behaviour
    # that matters is covered by test_stalk_engage_and_cancel below.
    pass

  def test_stalk_engage_and_cancel(self):
    # Pull latches controls on its rising edge, and only on the edge.
    self.safety.set_controls_allowed(False)
    self._rx(self._pcm_status_msg(False))
    self._rx(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())

    # Cancel clears them, and a repeated cancel keeps them clear.
    for _ in range(3):
      self._rx(self.packer.make_can_msg_safety("STEER_AND_AP_STALK", 0, {"AP_CANCEL_COMMAND": 1},
                                               fix_checksum=fix_checksum))
      self.assertFalse(self.safety.get_controls_allowed())

    # A held pull must not re-latch without a fresh edge.
    self._rx(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())
    self.safety.set_controls_allowed(False)
    for _ in range(5):
      self._rx(self._pcm_status_msg(True))
    self.assertFalse(self.safety.get_controls_allowed(), "a held stalk re-latched controls without an edge")

  def test_main_cancel_button(self):
    # The stalk push must drop controls even with no other state change.
    self.safety.set_controls_allowed(True)
    self._rx(self.packer.make_can_msg_safety("STEER_AND_AP_STALK", 0, {"AP_CANCEL_COMMAND": 1},
                                             fix_checksum=fix_checksum))
    self.assertFalse(self.safety.get_controls_allowed())

  def test_rx_checksum(self):
    # The two checksummed streams must be rejected when the checksum is wrong,
    # otherwise a dead bus would read as a valid standstill.
    for msg_fn in (lambda: self._speed_msg(0), lambda: self._pcm_status_msg(0)):
      with self.subTest(msg=msg_fn().__class__.__name__):
        self.assertTrue(self._rx(msg_fn()))
        bad = msg_fn()
        bad[0].data[0] = 0xFF
        self.assertFalse(self._rx(bad))

  def test_counter_wraps_at_15(self):
    # Every stock stream on this car walks 0..14 and never emits 15, so the
    # expected-counter modulus is 15. A 14 -> 0 rollover must be accepted.
    for counter in (13, 14, 0, 1):
      msg = self.packer.make_can_msg_safety("STEER_AND_AP_STALK", 0, {"COUNTER": counter},
                                            fix_checksum=fix_checksum)
      self.assertTrue(self._rx(msg), f"rejected a valid counter value {counter}")


if __name__ == "__main__":
  unittest.main()
