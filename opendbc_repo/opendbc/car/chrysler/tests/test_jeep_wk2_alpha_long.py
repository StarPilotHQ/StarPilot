"""JEEP_GRAND_CHEROKEE_2019 alpha-long + radar admission."""

import math
import unittest
from types import SimpleNamespace

from opendbc.can import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.chrysler.aol_contract import qualified as aol_qualified
from opendbc.car.chrysler.brake_hold import brake_hold_supported
from opendbc.car.chrysler.carcontroller import CarController
from opendbc.car.chrysler.carstate import CarState
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR, DBC, ChryslerFlags, ChryslerSafetyFlags

LongCtrlState = structs.CarControl.Actuators.LongControlState
NOW_NS = 100_000_000_000


def _fp_with_radar():
  fp = gen_empty_fingerprint()
  # OEM Chrysler track IDs observed on WK2 bus 1.
  fp[1] = {addr: 8 for addr in list(range(0x2a2, 0x2b4 + 2, 2)) + list(range(0x2c2, 0x2d4 + 2, 2))}
  return fp


class TestJeepWk2AlphaLong(unittest.TestCase):
  def test_alpha_long_off_keeps_stock_acc_and_hold(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, _fp_with_radar(), [], False, False, False)
    self.assertTrue(cp.flags & ChryslerFlags.ALPHA_LONG_AVAILABLE)
    self.assertTrue(cp.alphaLongitudinalAvailable)
    self.assertFalse(cp.openpilotLongitudinalControl)
    self.assertTrue(cp.pcmCruise)
    # Still unavailable until #26842-class parse/FCW validation on WK2 (addrs alone are not enough).
    self.assertTrue(cp.radarUnavailable)
    self.assertEqual(cp.safetyConfigs[0].safetyParam & ChryslerSafetyFlags.LONG_CONTROL, 0)
    self.assertTrue(brake_hold_supported(cp))
    self.assertTrue(aol_qualified(cp))

  def test_alpha_long_on_enables_op_long_and_disables_hold(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, _fp_with_radar(), [], True, False, False)
    self.assertTrue(cp.openpilotLongitudinalControl)
    self.assertFalse(cp.pcmCruise)
    self.assertTrue(cp.autoResumeSng)
    self.assertEqual(cp.minEnableSpeed, -1.)
    self.assertTrue(cp.safetyConfigs[0].safetyParam & ChryslerSafetyFlags.LONG_CONTROL)
    self.assertFalse(brake_hold_supported(cp))
    self.assertFalse(aol_qualified(cp))

  def test_alpha_long_hidden_on_release(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, _fp_with_radar(), [], True, True, False)
    self.assertFalse(cp.alphaLongitudinalAvailable)
    self.assertFalse(cp.openpilotLongitudinalControl)
    self.assertTrue(cp.pcmCruise)

  def test_radar_stays_unavailable_even_when_tracks_present(self):
    # Presence proves hardware; #26842 was parse/FCW quality — do not auto-enable.
    for fp in (gen_empty_fingerprint(), _fp_with_radar()):
      cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, fp, [], False, False, False)
      self.assertTrue(cp.radarUnavailable)

  def test_older_wk2_has_no_alpha_long(self):
    cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE, gen_empty_fingerprint(), [], True, False, False)
    self.assertFalse(cp.flags & ChryslerFlags.ALPHA_LONG_AVAILABLE)
    self.assertFalse(getattr(cp, 'alphaLongitudinalAvailable', False))
    self.assertFalse(cp.openpilotLongitudinalControl)

  def test_gap_stalk_distance_plus_is_wk2_2019_only(self):
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'carstate.py').read_text()
    self.assertIn('JEEP_GRAND_CHEROKEE_2019', src)
    self.assertIn('ACC_Distance_Inc', src)
    # Older Chrysler keeps Distance_Dec-only path.
    self.assertIn('self.distance_button = buttons["ACC_Distance_Dec"]', src)

  def test_older_jeep_keeps_stock_pid(self):
    older = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE, gen_empty_fingerprint(), [], True, False, False)
    newer = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, gen_empty_fingerprint(), [], False, False, False)
    self.assertAlmostEqual(older.lateralTuning.pid.kpV[0], 0.15)
    self.assertAlmostEqual(older.lateralTuning.pid.kpV[1], 0.30)
    self.assertAlmostEqual(newer.lateralTuning.pid.kpV[0], 0.12)
    self.assertAlmostEqual(newer.lateralTuning.pid.kpV[1], 0.22)
    self.assertFalse(older.flags & ChryslerFlags.ALPHA_LONG_AVAILABLE)

  def test_oem_fcw_frame_is_optional_and_alpha_long_only(self):
    long_cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, gen_empty_fingerprint(), [], True, False, False)
    parser = CarState.get_can_parsers(long_cp)[Bus.pt]
    self.assertTrue(parser.message_states[0x346].ignore_alive)
    stock_cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, gen_empty_fingerprint(), [], False, False, False)
    self.assertNotIn(0x346, CarState.get_can_parsers(stock_cp)[Bus.pt].addresses)


class TestJeepWk2LongController(unittest.TestCase):
  """Decoded DAS_3 output of the WK2 alpha-long controller against stock-measured semantics."""

  def setUp(self):
    self.CP = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, gen_empty_fingerprint(), [], True, False, False)
    self.controller = CarController(DBC[self.CP.carFingerprint], self.CP)
    self.controller.jeep_hold_replay = True  # use the passed clock; CLOCK_BOOTTIME is Linux-only
    self.parser = CANParser(DBC[self.CP.carFingerprint][Bus.pt], [('DAS_3', math.nan), ('CRUISE_BUTTONS', math.nan)], 0)
    self.stock = {
      'ACC_AVAILABLE': 1, 'ACC_ACTIVE': 1, 'ACC_DECEL_REQ': 0, 'ACC_DECEL': 4.0,
      'ENGINE_TORQUE_REQUEST': 50.0, 'ENGINE_TORQUE_REQUEST_MAX': 1, 'ACC_STANDSTILL': 0, 'ACC_GO': 0,
      'COLLISION_BRK_PREP': 0, 'ACC_BRK_PREP': 0, 'ACC_FAULTED': 0, 'GR_MAX_REQ': 0, 'COUNTER': 5,
    }

  def step(self, accel, *, accel_last=None, v_ego=5.0, state=LongCtrlState.pid, stock_fcw=False):
    if accel_last is not None:
      self.controller.accel_last = accel_last
    CS = SimpleNamespace(
      das_3=dict(self.stock), das_3_timestamp=NOW_NS, button_counter=0,
      out=structs.CarState(canValid=True, vEgo=v_ego, standstill=v_ego < 1e-3, stockFcw=stock_fcw),
    )
    CC = structs.CarControl(enabled=True, longActive=True)
    CC.actuators.accel = accel
    CC.actuators.longControlState = state
    sends = []
    self.controller.update_jeep_wk2_2019_long(CC, CS, NOW_NS, sends)
    self.controller.frame += 1
    das_3 = [s for s in sends if s[0] == 0x1F4]
    if not das_3:
      return None, sends
    self.parser.update([(NOW_NS, das_3)])
    return dict(self.parser.vl['DAS_3']), sends

  def test_torque_mode_uses_stock_idle_decel_and_fitted_torque(self):
    out, _ = self.step(1.0, accel_last=1.0, v_ego=5.0)
    self.assertEqual(out['ACC_DECEL_REQ'], 0)
    self.assertAlmostEqual(out['ACC_DECEL'], 4.0, delta=0.01)
    self.assertEqual(out['ENGINE_TORQUE_REQUEST_MAX'], 1)
    # base 55..71 Nm and gain 100..130 Nm/(m/s^2) interpolated at 5 m/s
    self.assertAlmostEqual(out['ENGINE_TORQUE_REQUEST'], 62.27 + 113.64, delta=0.5)
    self.assertEqual(out['COUNTER'], (self.stock['COUNTER'] + 2) % 16)

  def test_torque_is_capped_at_stock_peak(self):
    out, _ = self.step(2.0, accel_last=2.0, v_ego=20.0)
    self.assertAlmostEqual(out['ENGINE_TORQUE_REQUEST'], 380.0, delta=0.3)

  def test_braking_mode_and_hysteresis(self):
    out, _ = self.step(-1.5, accel_last=-1.5)
    self.assertEqual((out['ACC_DECEL_REQ'], out['ENGINE_TORQUE_REQUEST_MAX']), (1, 0))
    self.assertAlmostEqual(out['ACC_DECEL'], -1.5, delta=0.01)
    self.assertAlmostEqual(out['ENGINE_TORQUE_REQUEST'], -52.0, delta=0.3)
    out, _ = self.step(0.05, accel_last=0.0)
    self.assertEqual(out['ACC_DECEL_REQ'], 1)  # stays braking until accel exceeds +0.1
    out, _ = self.step(0.3, accel_last=0.2)
    self.assertEqual(out['ACC_DECEL_REQ'], 0)

  def test_accel_clamped_to_envelope(self):
    out, _ = self.step(-5.0, accel_last=-3.5)
    self.assertAlmostEqual(out['ACC_DECEL'], -3.5, delta=0.01)

  def test_standstill_hold_and_start(self):
    out, _ = self.step(-0.5, accel_last=-2.0, v_ego=0.0, state=LongCtrlState.stopping)
    self.assertEqual((out['ACC_STANDSTILL'], out['ACC_DECEL_REQ']), (1, 1))
    self.assertAlmostEqual(out['ACC_DECEL'], -2.0, delta=0.01)
    self.controller.frame = 10
    out, sends = self.step(0.5, accel_last=0.5, v_ego=0.0, state=LongCtrlState.starting)
    self.assertEqual((out['ACC_GO'], out['ACC_STANDSTILL']), (1, 0))
    self.assertTrue(any(s[0] == 0x23B for s in sends), "resume pulse while starting")

  def test_runs_at_100hz(self):
    for _ in range(3):
      out, _ = self.step(0.5, accel_last=0.5)
      self.assertIsNotNone(out)

  def test_yields_to_oem_collision_handling(self):
    self.assertIsNone(self.step(0.5, accel_last=0.5, stock_fcw=True)[0])
    self.stock['COLLISION_BRK_PREP'] = 1
    self.assertIsNone(self.step(0.5, accel_last=0.5)[0])
    self.stock.update(COLLISION_BRK_PREP=0, ACC_FAULTED=1)
    self.assertIsNone(self.step(0.5, accel_last=0.5)[0])
    self.stock.update(ACC_FAULTED=0, ACC_DECEL_REQ=1, ACC_DECEL=-4.0)
    self.assertIsNone(self.step(0.5, accel_last=0.5)[0])
    self.assertAlmostEqual(self.controller.accel_last, -3.5)
    self.stock.update(ACC_ACTIVE=0)  # AEB-style braking need not set ACC_ACTIVE
    self.assertIsNone(self.step(0.5, accel_last=0.5)[0])

  def test_firm_stock_braking_is_a_floor_while_moving(self):
    self.stock.update(ACC_DECEL_REQ=1, ACC_DECEL=-2.5)
    out, _ = self.step(0.5, accel_last=0.5, v_ego=10.0)
    self.assertEqual(out['ACC_DECEL_REQ'], 1)
    self.assertAlmostEqual(out['ACC_DECEL'], -2.5, delta=0.01)
    self.stock.update(ACC_DECEL=-0.5)
    out, _ = self.step(0.5, accel_last=0.5, v_ego=10.0)
    self.assertEqual(out['ACC_DECEL_REQ'], 0)  # gentle stock trims do not override OP


if __name__ == '__main__':
  unittest.main()
