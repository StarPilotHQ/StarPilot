#!/usr/bin/env python3
"""GWM Always On Lateral policy.

Registering an AolSafetyPolicy moves lateral_controls_allowed() off plain
controls_allowed and onto permission_mask() & 1, so these tests cover both
directions: that lateral survives a brake-only disengage, and that every gate
that is supposed to revoke it actually does.
"""
import unittest

from opendbc.car.gwm.values import GwmFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.test_gwm import GWM_DBC, clip_to_signal, fix_checksum

LATERAL = 0x1
LONGITUDINAL = 0x2
STALK_TIMEOUT_US = 300000


class TestGwmAolPolicy(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.packer = CANPackerSafety(GWM_DBC)
    self._enter(alternative_experience=32, param=GwmFlags.LONG_CONTROL)

  def _enter(self, alternative_experience, param):
    # gwm_init reads alternative_experience to decide whether to register the
    # policy, so it has to be set before set_safety_hooks. init_tests() zeroes
    # it afterwards, which is harmless: registration already happened and is
    # never re-evaluated outside init.
    self.safety.set_alternative_experience(alternative_experience)
    self.safety.set_safety_hooks(CarParams.SafetyModel.gwm, param)
    self.safety.init_tests()
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(LATERAL | LONGITUDINAL)
    self.safety.set_torque_meas(0, 0)
    self.safety.set_desired_torque_last(0)
    self.safety.set_rt_torque_last(0)

  # -- helpers ------------------------------------------------------------
  def _stalk(self, pull=0, cancel=0):
    return self.packer.make_can_msg_safety(
      "STEER_AND_AP_STALK", 0,
      {"AP_ENABLE_COMMAND": pull, "AP_CANCEL_COMMAND": cancel},
      fix_checksum=fix_checksum)

  def _pump_rx(self):
    """aol_rx_healthy() requires every configured rx check to have been seen
    and to be fresh, so the whole bus-0 catalog has to be fed, not just the
    stalk."""
    wheel = {f"{pos}_WHEEL_SPEED": 0.0
             for pos in ("FRONT_LEFT", "FRONT_RIGHT", "REAR_LEFT", "REAR_RIGHT")}
    self.safety.safety_rx_hook(
      self.packer.make_can_msg_safety("WHEEL_SPEEDS", 0, wheel, fix_checksum=fix_checksum))
    self.safety.safety_rx_hook(
      self.packer.make_can_msg_safety("CAR_OVERALL_SIGNALS2", 0, {"GAS_POSITION": 0}))
    self.safety.safety_rx_hook(
      self.packer.make_can_msg_safety("BRAKE2", 0, {"PEDAL_BRAKE_PRESSED": 0}))
    self.safety.safety_rx_hook(
      self.packer.make_can_msg_safety("RX_STEER_RELATED", 0, {"B_RX_EPS_TORQUE": 0}))

  def _latch_stalk(self):
    """Rising edge of the pull latches acc_main_on and controls_allowed."""
    self._pump_rx()
    self.safety.safety_rx_hook(self._stalk(pull=0))
    self.safety.safety_rx_hook(self._stalk(pull=1))

  def _torque_cmd(self, torque, steer_req=1):
    torque = clip_to_signal("STEER_CMD", "TORQUE_CMD", torque)
    return self.packer.make_can_msg_safety(
      "STEER_CMD", 0, {"STEER_REQUEST": steer_req, "TORQUE_CMD": torque})

  def _lateral_granted(self):
    return bool(self.safety.aol_get_permission_mask() & LATERAL)

  def _longitudinal_granted(self):
    return bool(self.safety.aol_get_permission_mask() & LONGITUDINAL)

  # -- the behaviour that is the whole point ------------------------------
  def test_lateral_survives_brake_only_disengage(self):
    self._latch_stalk()
    self.assertTrue(self.safety.get_acc_main_on())
    self.assertTrue(self._lateral_granted())

    # A brake-only disengage clears controls_allowed but must not touch the
    # stalk latch, so lateral authority and real torque both stay.
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.get_acc_main_on(), "brake cleared the stalk latch")
    self.assertTrue(self._lateral_granted(), "lateral revoked by a brake-only disengage")
    self.assertTrue(self.safety.safety_tx_hook(self._torque_cmd(4)),
                    "steering torque blocked while AOL should hold it")

  def test_stalk_cancel_revokes_lateral(self):
    self._latch_stalk()
    self.safety.safety_rx_hook(self._stalk(cancel=1))
    self.assertFalse(self.safety.get_acc_main_on())
    self.assertFalse(self._lateral_granted(), "lateral survived a stalk cancel")
    self.assertFalse(self.safety.safety_tx_hook(self._torque_cmd(4)),
                     "torque allowed after a stalk cancel")

  # -- the policy must not widen anything --------------------------------
  def test_longitudinal_still_needs_ordinary_engagement(self):
    self._latch_stalk()
    self.assertTrue(self._longitudinal_granted())
    # Same brake-only disengage: longitudinal must drop even though lateral holds.
    self.safety.set_controls_allowed(False)
    self.assertFalse(self._longitudinal_granted(),
                     "longitudinal granted without controls_allowed")
    self.assertTrue(self._lateral_granted())

  def test_torque_ceiling_unchanged_under_aol(self):
    self._latch_stalk()
    self.safety.set_controls_allowed(False)
    self.safety.set_torque_meas(300, 300)
    self.safety.set_desired_torque_last(253)
    self.safety.set_rt_torque_last(253)
    self.assertFalse(self.safety.safety_tx_hook(self._torque_cmd(260)),
                     "AOL raised the 253 torque cap")

  # -- revocation gates ---------------------------------------------------
  def test_no_host_request_means_no_authority(self):
    self._latch_stalk()
    self.safety.aol_set_host_request(0)
    self.assertFalse(self._lateral_granted(), "lateral granted with no host request")

  def test_heartbeat_loss_revokes(self):
    self._latch_stalk()
    self.safety.set_aol_test_heartbeat(False)
    self.assertFalse(self._lateral_granted(), "lateral survived heartbeat loss")

  def test_stale_stalk_stream_revokes(self):
    self._latch_stalk()
    # Age the bus past the stalk timeout while re-issuing the host request, so
    # a live host cannot hold authority over a dead bus. This ages the whole rx
    # catalog too, so it does not isolate the stalk gate from aol_rx_healthy();
    # it asserts the outcome, which is that authority is gone either way.
    self.safety.set_timer(STALK_TIMEOUT_US + 100000)
    self.safety.aol_set_host_request(LATERAL | LONGITUDINAL)
    self.assertFalse(self._lateral_granted(), "lateral survived a stale stalk stream")

  def test_bad_stalk_checksum_revokes(self):
    self._latch_stalk()
    bad = self._stalk(pull=1)
    bad[0].data[0] = 0xFF
    self.assertFalse(self.safety.safety_rx_hook(bad))
    self.assertFalse(self._lateral_granted(), "lateral survived an invalid stalk frame")

  # -- registration scope -------------------------------------------------
  def test_unqualified_profile_registers_no_policy(self):
    # Without the AOL alternative experience the policy must not be
    # registered, and lateral falls back to plain controls_allowed.
    self._enter(alternative_experience=0, param=GwmFlags.LONG_CONTROL)
    self._latch_stalk()
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertTrue(self.safety.safety_tx_hook(self._torque_cmd(4)),
                    "fallback lateral broke when no policy is registered")
    self.safety.set_controls_allowed(False)
    self.assertFalse(self.safety.safety_tx_hook(self._torque_cmd(4)),
                     "torque allowed with no policy and no controls_allowed")


if __name__ == "__main__":
  unittest.main()
