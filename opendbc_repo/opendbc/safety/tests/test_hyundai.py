#!/usr/bin/env python3
import random
import unittest

from opendbc.car.hyundai.values import HyundaiSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py
import opendbc.safety.tests.common as common
from opendbc.safety.tests.hyundai_common import HyundaiButtonBase, HyundaiLongitudinalBase


# 4 bit checkusm used in some hyundai messages
# lives outside the can packer because we never send this msg
def checksum(msg):
  addr, dat, bus = msg

  chksum = 0
  if addr == 0x386:
    for i, b in enumerate(dat):
      for j in range(8):
        # exclude checksum and counter bits
        if (i != 1 or j < 6) and (i != 3 or j < 6) and (i != 5 or j < 6) and (i != 7 or j < 6):
          bit = (b >> j) & 1
        else:
          bit = 0
        chksum += bit
    chksum = (chksum ^ 9) & 0xF
    ret = bytearray(dat)
    ret[5] |= (chksum & 0x3) << 6
    ret[7] |= (chksum & 0xc) << 4
  else:
    for i, b in enumerate(dat):
      if addr in [0x260, 0x421] and i == 7:
        b &= 0x0F if addr == 0x421 else 0xF0
      elif addr == 0x394 and i == 6:
        b &= 0xF0
      elif addr == 0x394 and i == 7:
        continue
      chksum += sum(divmod(b, 16))
    chksum = (16 - chksum) % 16
    ret = bytearray(dat)
    ret[6 if addr == 0x394 else 7] |= chksum << (4 if addr == 0x421 else 0)

  return addr, ret, bus


class TestHyundaiRefreshSafety(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.packer = CANPackerSafety("hyundai_can_refresh_generated")

  def _set_mode(self, flags):
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, flags)
    self.safety.init_tests()

  def test_stock_camera_scc_refresh_length_and_ownership(self):
    self._set_mode(HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CAN_REFRESH_MSGS)
    lfa = self.packer.make_can_msg_safety("LFAHDA_MFC", 0, {"LFA_Icon_State": 2})
    self.assertTrue(self.safety.safety_tx_hook(lfa))
    self.assertFalse(self.safety.safety_tx_hook(common.make_msg(0, 0x485, 4)))
    self.assertFalse(self.safety.safety_tx_hook(common.make_msg(1, 0x485, 8)))
    self.assertFalse(self.safety.safety_tx_hook(common.make_msg(0, 0x420, 8)))

  def test_long_camera_scc_refresh_allowlist(self):
    self._set_mode(HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CAN_REFRESH_MSGS | HyundaiSafetyFlags.LONG)
    self.assertTrue(self.safety.safety_tx_hook(common.make_msg(0, 0x485, 8)))
    self.assertFalse(self.safety.safety_tx_hook(common.make_msg(0, 0x485, 4)))
    self.assertFalse(self.safety.safety_tx_hook(common.make_msg(1, 0x485, 8)))
    self.assertTrue(self.safety.safety_tx_hook(common.make_msg(0, 0x420, 8)))
    self.assertFalse(self.safety.safety_tx_hook(common.make_msg(2, 0x420, 8)))

  def test_radar_scc_refresh_length_and_longitudinal_ownership(self):
    for longitudinal in (False, True):
      with self.subTest(longitudinal=longitudinal):
        flags = HyundaiSafetyFlags.CAN_REFRESH_MSGS | (HyundaiSafetyFlags.LONG if longitudinal else 0)
        self._set_mode(flags)
        self.assertTrue(self.safety.safety_tx_hook(common.make_msg(0, 0x485, 8)))
        self.assertFalse(self.safety.safety_tx_hook(common.make_msg(0, 0x485, 4)))
        self.assertFalse(self.safety.safety_tx_hook(common.make_msg(1, 0x485, 8)))
        self.assertEqual(self.safety.safety_tx_hook(common.make_msg(0, 0x420, 8)), longitudinal)
        self.assertFalse(self.safety.safety_tx_hook(common.make_msg(2, 0x420, 8)))

  def test_classic_hyundai_length_unchanged(self):
    self._set_mode(HyundaiSafetyFlags.CAMERA_SCC)
    self.assertTrue(self.safety.safety_tx_hook(common.make_msg(0, 0x485, 4)))
    self.assertFalse(self.safety.safety_tx_hook(common.make_msg(0, 0x485, 8)))

  def test_hook_reinitialization_does_not_leak_refresh_length(self):
    modes = (
      (CarParams.SafetyModel.hyundai, HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CAN_REFRESH_MSGS, 8),
      (CarParams.SafetyModel.hyundai, HyundaiSafetyFlags.CAMERA_SCC, 4),
      (CarParams.SafetyModel.hyundaiLegacy, 0, 4),
      (CarParams.SafetyModel.hyundaiCanfd, 0, None),
      (CarParams.SafetyModel.hyundai, HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CAN_REFRESH_MSGS, 8),
    )
    for model, flags, permitted_length in modes:
      with self.subTest(model=model, flags=flags):
        self.safety.set_safety_hooks(model, flags)
        self.safety.init_tests()
        for length in (4, 8):
          self.assertEqual(self.safety.safety_tx_hook(common.make_msg(0, 0x485, length)), length == permitted_length)
        for bus in (1, 2):
          self.assertFalse(self.safety.safety_tx_hook(common.make_msg(bus, 0x485, 8)))

  def test_refresh_flag_fits_unique_safety_param_bit(self):
    values = [int(flag) for flag in HyundaiSafetyFlags]
    self.assertEqual(len(values), len(set(values)))
    self.assertTrue(all(0 < value <= 0xFFFF and value.bit_count() == 1 for value in values))
    self.assertEqual(int(HyundaiSafetyFlags.CAN_REFRESH_MSGS), 32768)


class TestHyundaiNonSccSafety(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.packer = CANPackerSafety("hyundai_can_generated")

  def _set_mode(self, flags):
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, flags | HyundaiSafetyFlags.NON_SCC)
    self.safety.init_tests()

  def _rx(self, name, values, bus=0, fix_checksum=None):
    return self.safety.safety_rx_hook(self.packer.make_can_msg_safety(name, bus, values, fix_checksum=fix_checksum))

  def test_stock_cruise_source_and_no_longitudinal_transmit(self):
    sources = (
      (0, "EMS16", "CRUISE_LAMP_S", "AliveCounter", 4, checksum),
      (HyundaiSafetyFlags.HYBRID_GAS, "E_CRUISE_CONTROL", "CRUISE_LAMP_S", None, 0, None),
      (HyundaiSafetyFlags.EV_GAS, "EMS12", "ACC_ACT", None, 0, None),
    )
    for flags, name, signal, counter, modulus, fix_checksum in sources:
      for lda in (False, True):
        with self.subTest(flags=flags, lda=lda):
          self._set_mode(flags | (HyundaiSafetyFlags.HAS_LDA_BUTTON if lda else 0) | HyundaiSafetyFlags.LONG)
          self.assertTrue(self.safety.safety_tx_hook(common.make_msg(0, 0x485, 4)))
          for addr in (0x420, 0x421, 0x50A, 0x389, 0x38D, 0x483, 0x7D0):
            self.assertFalse(self.safety.safety_tx_hook(common.make_msg(0, addr, 8)), hex(addr))
          self.assertEqual(self.safety.safety_fwd_hook(2, 0x420), 0)
          self.assertEqual(self.safety.safety_fwd_hook(2, 0x340), -1)
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertTrue(self._rx("CLU11", {"CF_Clu_CruiseSwState": 2, "CF_Clu_AliveCnt1": 0}))
          for engaged in (0, 1, 0):
            values = {signal: engaged}
            if counter is not None:
              values[counter] = engaged % modulus
            self.assertTrue(self._rx(name, values, fix_checksum=fix_checksum))
            self.assertEqual(self.safety.get_controls_allowed(), bool(engaged))
          # An SCC12 heartbeat is neither required nor a cruise authority on this family.
          self.assertTrue(self._rx("SCC12", {"ACCMode": 1, "CR_VSM_Alive": 0}, fix_checksum=checksum))
          self.assertFalse(self.safety.get_controls_allowed())

  def test_lda_receive_requirement_is_configuration_specific(self):
    for lda in (False, True):
      with self.subTest(lda=lda):
        self._set_mode(HyundaiSafetyFlags.HAS_LDA_BUTTON if lda else 0)
        self.assertTrue(self._rx("BCM_PO_11", {"LDA_BTN": 1}))
        self.assertFalse(self.safety.get_controls_allowed())

  def test_brake_and_gas_disengage_for_each_fuel_architecture(self):
    for flags, gas_name, gas_signal in (
      (0, "EMS16", "CF_Ems_AclAct"),
      (HyundaiSafetyFlags.HYBRID_GAS, "E_EMS11", "CR_Vcu_AccPedDep_Pos"),
      (HyundaiSafetyFlags.EV_GAS, "E_EMS11", "Accel_Pedal_Pos"),
    ):
      with self.subTest(flags=flags):
        self._set_mode(flags)
        self.safety.set_controls_allowed(True)
        self.assertTrue(self._rx("TCS13", {"DriverOverride": 2, "AliveCounterTCS": 0}, fix_checksum=checksum))
        self.assertFalse(self.safety.get_controls_allowed())
        self.safety.set_controls_allowed(True)
        values = {gas_signal: 10}
        if gas_name == "EMS16":
          values["AliveCounter"] = 0
          values["CRUISE_LAMP_S"] = 1
        self.assertTrue(self._rx(gas_name, values, fix_checksum=checksum))
        self.assertTrue(self.safety.get_gas_pressed_prev())
        self.assertFalse(self.safety.get_longitudinal_allowed())

  def test_receive_health_requires_real_cruise_source_not_scc12(self):
    for flags, cruise_name, cruise_values in (
      (0, "EMS16", {"CRUISE_LAMP_S": 0, "AliveCounter": 0}),
      (HyundaiSafetyFlags.HYBRID_GAS, "E_CRUISE_CONTROL", {"CRUISE_LAMP_S": 0}),
      (HyundaiSafetyFlags.EV_GAS, "EMS12", {"ACC_ACT": 0}),
    ):
      for lda in (False, True):
        with self.subTest(flags=flags, lda=lda):
          self._set_mode(flags | (HyundaiSafetyFlags.HAS_LDA_BUTTON if lda else 0))
          frames = [
            ("WHL_SPD11", {"WHL_SPD_AliveCounter_LSB": 0, "WHL_SPD_AliveCounter_MSB": 0}, checksum),
            ("TCS13", {"AliveCounterTCS": 0}, checksum),
            ("MDPS12", {}, None),
            ("CLU11", {"CF_Clu_AliveCnt1": 0}, None),
          ]
          if flags:
            frames.append(("E_EMS11", {"Accel_Pedal_Pos": 0}, None))
          frames.append((cruise_name, cruise_values, checksum if not flags else None))
          if lda:
            frames.append(("BCM_PO_11", {"LDA_BTN": 0}, None))
          for name, values, fix in frames:
            self.assertTrue(self._rx(name, values, fix_checksum=fix), name)
          self.safety.set_timer(1000)
          self.safety.safety_tick()
          self.assertTrue(self.safety.safety_config_valid())

  def test_missing_or_wrong_bus_cruise_source_invalidates_health(self):
    for flags, cruise_name, cruise_values in (
      (0, "EMS16", {"CRUISE_LAMP_S": 1, "AliveCounter": 0}),
      (HyundaiSafetyFlags.HYBRID_GAS, "E_CRUISE_CONTROL", {"CRUISE_LAMP_S": 1}),
      (HyundaiSafetyFlags.EV_GAS, "EMS12", {"ACC_ACT": 1}),
    ):
      for lda in (False, True):
        with self.subTest(flags=flags, lda=lda):
          self._set_mode(flags | (HyundaiSafetyFlags.HAS_LDA_BUTTON if lda else 0))
          self.safety.set_timer(2_000_000)
          for name, values, fix in (
            ("E_EMS11", {"Accel_Pedal_Pos": 0}, None),
            ("WHL_SPD11", {"WHL_SPD_AliveCounter_LSB": 0, "WHL_SPD_AliveCounter_MSB": 0}, checksum),
            ("TCS13", {"AliveCounterTCS": 0}, checksum),
            ("MDPS12", {}, None),
            ("CLU11", {"CF_Clu_AliveCnt1": 0}, None),
            ("SCC12", {"ACCMode": 1, "CR_VSM_Alive": 0}, checksum),
          ):
            self.assertTrue(self._rx(name, values, fix_checksum=fix), name)
          if lda:
            self.assertTrue(self._rx("BCM_PO_11", {"LDA_BTN": 0}))
          # This address on a different bus is not evidence for the PT cruise source.
          self.assertTrue(self._rx(cruise_name, cruise_values, bus=1,
                                   fix_checksum=checksum if not flags else None))
          self.safety.set_controls_allowed(True)
          self.safety.safety_tick()
          self.assertFalse(self.safety.safety_config_valid())
          self.assertFalse(self.safety.get_controls_allowed())
          steer = self.packer.make_can_msg_safety("LKAS11", 0, {"CR_Lkas_StrToqReq": 1, "CF_Lkas_ActToi": 1})
          self.assertFalse(self.safety.safety_tx_hook(steer))

  def test_expired_hybrid_cruise_source_invalidates_health(self):
    self._set_mode(HyundaiSafetyFlags.HYBRID_GAS)
    self.safety.set_timer(1000)
    self.assertTrue(self._rx("E_CRUISE_CONTROL", {"CRUISE_LAMP_S": 0}))
    self.safety.set_timer(2_000_000)
    for name, values, fix in (
      ("E_EMS11", {"CR_Vcu_AccPedDep_Pos": 0}, None),
      ("WHL_SPD11", {"WHL_SPD_AliveCounter_LSB": 0, "WHL_SPD_AliveCounter_MSB": 0}, checksum),
      ("TCS13", {"AliveCounterTCS": 0}, checksum),
      ("MDPS12", {}, None),
      ("CLU11", {"CF_Clu_AliveCnt1": 0}, None),
      ("SCC12", {"ACCMode": 1, "CR_VSM_Alive": 0}, checksum),
    ):
      self.assertTrue(self._rx(name, values, fix_checksum=fix), name)
    self.safety.set_controls_allowed(True)
    self.safety.safety_tick()
    self.assertFalse(self.safety.safety_config_valid())
    self.assertFalse(self.safety.get_controls_allowed())

  def test_non_scc_steering_request_and_relay_limits(self):
    for flags in (0, HyundaiSafetyFlags.HYBRID_GAS, HyundaiSafetyFlags.EV_GAS,
                  HyundaiSafetyFlags.ALT_LIMITS, HyundaiSafetyFlags.HAS_LDA_BUTTON):
      with self.subTest(flags=flags):
        self._set_mode(flags)
        self.safety.set_controls_allowed(True)

        def steer(torque, request):
          return self.packer.make_can_msg_safety("LKAS11", 0, {"CR_Lkas_StrToqReq": torque, "CF_Lkas_ActToi": request})
        self.assertTrue(self.safety.safety_tx_hook(steer(1, 1)))
        self.assertFalse(self.safety.safety_tx_hook(steer(400, 1)))
        self.assertFalse(self.safety.safety_tx_hook(steer(1, 0)))
        self.safety.init_tests()
        self.assertTrue(self.safety.safety_rx_hook(common.make_msg(0, 0x340, 8)))
        self.assertTrue(self.safety.get_relay_malfunction())
        self.assertFalse(self.safety.safety_tx_hook(steer(0, 1)))
        self.assertEqual(self.safety.safety_fwd_hook(2, 0x420), -1)


class TestHyundaiSafety(HyundaiButtonBase, common.CarSafetyTest, common.DriverTorqueSteeringSafetyTest, common.SteerRequestCutSafetyTest):
  DBC = "hyundai_can_generated"
  SAFETY_MODEL = CarParams.SafetyModel.hyundai

  TX_MSGS = [[0x340, 0], [0x4F1, 0], [0x485, 0]]
  STANDSTILL_THRESHOLD = 12  # 0.375 kph
  RELAY_MALFUNCTION_ADDRS = {0: (0x340, 0x485)}  # LKAS11
  FWD_BLACKLISTED_ADDRS = {2: [0x340, 0x485]}

  MAX_RATE_UP = 3
  MAX_RATE_DOWN = 7
  MAX_TORQUE_LOOKUP = [0], [384]
  MAX_RT_DELTA = 112
  DRIVER_TORQUE_ALLOWANCE = 50
  DRIVER_TORQUE_FACTOR = 2

  # Safety around steering req bit
  MIN_VALID_STEERING_FRAMES = 89
  MAX_INVALID_STEERING_FRAMES = 2

  cnt_gas = 0
  cnt_speed = 0
  cnt_brake = 0
  cnt_cruise = 0
  cnt_button = 0

  def _button_msg(self, buttons, main_button=0, bus=0):
    values = {"CF_Clu_CruiseSwState": buttons, "CF_Clu_CruiseSwMain": main_button, "CF_Clu_AliveCnt1": self.cnt_button}
    self.__class__.cnt_button += 1
    return self.packer.make_can_msg_safety("CLU11", bus, values)

  def _user_gas_msg(self, gas):
    values = {"CF_Ems_AclAct": gas, "AliveCounter": self.cnt_gas % 4}
    self.__class__.cnt_gas += 1
    return self.packer.make_can_msg_safety("EMS16", 0, values, fix_checksum=checksum)

  def _user_brake_msg(self, brake):
    values = {"DriverOverride": 2 if brake else random.choice((0, 1, 3)),
              "AliveCounterTCS": self.cnt_brake % 8}
    self.__class__.cnt_brake += 1
    return self.packer.make_can_msg_safety("TCS13", 0, values, fix_checksum=checksum)

  def _speed_msg(self, speed):
    # safety doesn't scale, so undo the scaling
    values = {"WHL_SPD_%s" % s: speed * 0.03125 for s in ["FL", "FR", "RL", "RR"]}
    values["WHL_SPD_AliveCounter_LSB"] = (self.cnt_speed % 16) & 0x3
    values["WHL_SPD_AliveCounter_MSB"] = (self.cnt_speed % 16) >> 2
    self.__class__.cnt_speed += 1
    return self.packer.make_can_msg_safety("WHL_SPD11", 0, values, fix_checksum=checksum)

  def _pcm_status_msg(self, enable):
    values = {"ACCMode": enable, "CR_VSM_Alive": self.cnt_cruise % 16}
    self.__class__.cnt_cruise += 1
    return self.packer.make_can_msg_safety("SCC12", self.SCC_BUS, values, fix_checksum=checksum)

  def _torque_driver_msg(self, torque):
    values = {"CR_Mdps_StrColTq": torque}
    return self.packer.make_can_msg_safety("MDPS12", 0, values)

  def _torque_cmd_msg(self, torque, steer_req=1):
    values = {"CR_Lkas_StrToqReq": torque, "CF_Lkas_ActToi": steer_req}
    return self.packer.make_can_msg_safety("LKAS11", 0, values)


class TestHyundaiSafetyAltLimits(TestHyundaiSafety):
  SAFETY_PARAM = HyundaiSafetyFlags.ALT_LIMITS

  MAX_RATE_UP = 2
  MAX_RATE_DOWN = 3
  MAX_TORQUE_LOOKUP = [0], [270]

class TestHyundaiSafetyAltLimits2(TestHyundaiSafety):
  SAFETY_PARAM = HyundaiSafetyFlags.ALT_LIMITS_2

  MAX_RATE_UP = 2
  MAX_RATE_DOWN = 3
  MAX_TORQUE_LOOKUP = [0], [170]

class TestHyundaiSafetyCameraSCC(TestHyundaiSafety):
  SAFETY_PARAM = HyundaiSafetyFlags.CAMERA_SCC

  BUTTONS_TX_BUS = 2  # tx on 2, rx on 0
  SCC_BUS = 2  # rx on 2

class TestHyundaiSafetyFCEV(TestHyundaiSafety):
  SAFETY_PARAM = HyundaiSafetyFlags.FCEV_GAS

  def _user_gas_msg(self, gas):
    values = {"ACCELERATOR_PEDAL": gas}
    return self.packer.make_can_msg_safety("FCEV_ACCELERATOR", 0, values)


class TestHyundaiLegacySafety(TestHyundaiSafety):
  SAFETY_MODEL = CarParams.SafetyModel.hyundaiLegacy


class TestHyundaiLegacySafetyEV(TestHyundaiSafety):
  SAFETY_MODEL = CarParams.SafetyModel.hyundaiLegacy
  SAFETY_PARAM = HyundaiSafetyFlags.EV_GAS

  def _user_gas_msg(self, gas):
    values = {"Accel_Pedal_Pos": gas}
    return self.packer.make_can_msg_safety("E_EMS11", 0, values, fix_checksum=checksum)


class TestHyundaiLegacySafetyHEV(TestHyundaiSafety):
  SAFETY_MODEL = CarParams.SafetyModel.hyundaiLegacy
  SAFETY_PARAM = HyundaiSafetyFlags.HYBRID_GAS

  def _user_gas_msg(self, gas):
    values = {"CR_Vcu_AccPedDep_Pos": gas}
    return self.packer.make_can_msg_safety("E_EMS11", 0, values, fix_checksum=checksum)


class TestHyundaiLongitudinalSafety(HyundaiLongitudinalBase, TestHyundaiSafety):
  SAFETY_PARAM = HyundaiSafetyFlags.LONG

  TX_MSGS = [[0x340, 0], [0x4F1, 0], [0x485, 0], [0x420, 0], [0x421, 0], [0x50A, 0], [0x389, 0], [0x4A2, 0], [0x38D, 0], [0x483, 0], [0x7D0, 0]]

  FWD_BLACKLISTED_ADDRS = {2: [0x340, 0x485, 0x421, 0x420, 0x50A, 0x389]}

  RELAY_MALFUNCTION_ADDRS = {0: (0x340, 0x485, 0x421, 0x420, 0x50A, 0x389)}  # LKAS11, LFAHDA_MFC, SCC12, SCC11, SCC13, SCC14

  DISABLED_ECU_UDS_MSG = (0x7D0, 0)
  DISABLED_ECU_ACTUATION_MSG = (0x421, 0)

  def _accel_msg(self, accel, aeb_req=False, aeb_decel=0, aeb_stop_req=False):
    values = {
      "aReqRaw": accel,
      "aReqValue": accel,
      "AEB_CmdAct": int(aeb_req),
      "AEB_StopReq": int(aeb_stop_req),
      "CR_VSM_DecCmd": aeb_decel,
    }
    return self.packer.make_can_msg_safety("SCC12", self.SCC_BUS, values)

  def _fca11_msg(self, idx=0, vsm_aeb_req=False, fca_aeb_req=False, aeb_decel=0):
    values = {
      "CR_FCA_Alive": idx % 0xF,
      "FCA_Status": 2,
      "CR_VSM_DecCmd": aeb_decel,
      "CF_VSM_DecCmdAct": int(vsm_aeb_req),
      "FCA_CmdAct": int(fca_aeb_req),
    }
    return self.packer.make_can_msg_safety("FCA11", 0, values)

  def test_no_aeb_fca11(self):
    self.assertTrue(self._tx(self._fca11_msg()))
    self.assertFalse(self._tx(self._fca11_msg(vsm_aeb_req=True)))
    self.assertFalse(self._tx(self._fca11_msg(fca_aeb_req=True)))
    self.assertFalse(self._tx(self._fca11_msg(aeb_decel=1.0)))

  def test_no_aeb_scc12(self):
    self.assertTrue(self._tx(self._accel_msg(0)))
    self.assertFalse(self._tx(self._accel_msg(0, aeb_req=True)))
    self.assertFalse(self._tx(self._accel_msg(0, aeb_decel=1.0)))
    self.assertFalse(self._tx(self._accel_msg(0, aeb_stop_req=True)))


class TestHyundaiLongitudinalSafetyCameraSCC(HyundaiLongitudinalBase, TestHyundaiSafety):
  SAFETY_PARAM = HyundaiSafetyFlags.LONG | HyundaiSafetyFlags.CAMERA_SCC

  TX_MSGS = [[0x340, 0], [0x4F1, 2], [0x485, 0], [0x420, 0], [0x421, 0], [0x50A, 0], [0x389, 0], [0x4A2, 0]]

  FWD_BLACKLISTED_ADDRS = {2: [0x340, 0x485, 0x420, 0x421, 0x50A, 0x389]}
  RELAY_MALFUNCTION_ADDRS = {0: (0x340, 0x485, 0x421, 0x420, 0x50A, 0x389)}  # LKAS11, LFAHDA_MFC, SCC12, SCC11, SCC13, SCC14

  def _accel_msg(self, accel, aeb_req=False, aeb_decel=0, aeb_stop_req=False):
    values = {
      "aReqRaw": accel,
      "aReqValue": accel,
      "AEB_CmdAct": int(aeb_req),
      "AEB_StopReq": int(aeb_stop_req),
      "CR_VSM_DecCmd": aeb_decel,
    }
    return self.packer.make_can_msg_safety("SCC12", self.SCC_BUS, values)

  def test_no_aeb_scc12(self):
    self.assertTrue(self._tx(self._accel_msg(0)))
    self.assertFalse(self._tx(self._accel_msg(0, aeb_req=True)))
    self.assertFalse(self._tx(self._accel_msg(0, aeb_decel=1.0)))
    self.assertFalse(self._tx(self._accel_msg(0, aeb_stop_req=True)))

  def test_tester_present_allowed(self):
    pass

  def test_disabled_ecu_alive(self):
    pass


class TestHyundaiSafetyFCEVLong(TestHyundaiLongitudinalSafety, TestHyundaiSafetyFCEV):
  SAFETY_PARAM = HyundaiSafetyFlags.FCEV_GAS | HyundaiSafetyFlags.LONG


class TestHyundaiSafetyCameraSCCRefresh(TestHyundaiSafetyCameraSCC):
  def setUp(self):
    self.packer = CANPackerSafety("hyundai_can_refresh_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CAN_REFRESH_MSGS)
    self.safety.init_tests()


class TestHyundaiLongitudinalSafetyCameraSCCRefresh(TestHyundaiLongitudinalSafetyCameraSCC):
  def setUp(self):
    self.packer = CANPackerSafety("hyundai_can_refresh_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai,
                                 HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CAN_REFRESH_MSGS | HyundaiSafetyFlags.LONG)
    self.safety.init_tests()


class HyundaiRefreshHybridGasMixin:
  def _user_gas_msg(self, gas):
    return self.packer.make_can_msg_safety("E_EMS11", 0, {"CR_Vcu_AccPedDep_Pos": gas}, fix_checksum=checksum)


class TestHyundaiSafetyCameraSCCRefreshHybrid(HyundaiRefreshHybridGasMixin, TestHyundaiSafetyCameraSCCRefresh):
  def setUp(self):
    self.packer = CANPackerSafety("hyundai_can_refresh_generated")
    self.safety = libsafety_py.libsafety
    flags = HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CAN_REFRESH_MSGS | HyundaiSafetyFlags.HYBRID_GAS
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, flags)
    self.safety.init_tests()

  def test_hybrid_stock_refresh_length_and_acc_ownership(self):
    self.assertTrue(self._tx(common.make_msg(0, 0x485, 8)))
    self.assertFalse(self._tx(common.make_msg(0, 0x485, 4)))
    self.assertFalse(self._tx(common.make_msg(1, 0x485, 8)))
    self.assertFalse(self._tx(common.make_msg(0, 0x420, 8)))


class TestHyundaiLongitudinalSafetyCameraSCCRefreshHybrid(HyundaiRefreshHybridGasMixin, TestHyundaiLongitudinalSafetyCameraSCCRefresh):
  def setUp(self):
    self.packer = CANPackerSafety("hyundai_can_refresh_generated")
    self.safety = libsafety_py.libsafety
    flags = HyundaiSafetyFlags.CAMERA_SCC | HyundaiSafetyFlags.CAN_REFRESH_MSGS | HyundaiSafetyFlags.HYBRID_GAS | HyundaiSafetyFlags.LONG
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, flags)
    self.safety.init_tests()

  def test_set_resume_buttons(self):
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertTrue(self._rx(self._button_msg(0, main_button=1)))
    self.assertTrue(self._rx(self._button_msg(0, main_button=0)))
    super().test_set_resume_buttons()

  def test_hybrid_long_refresh_length_and_acc_allowlist(self):
    self.assertTrue(self._tx(common.make_msg(0, 0x485, 8)))
    self.assertFalse(self._tx(common.make_msg(0, 0x485, 4)))
    self.assertFalse(self._tx(common.make_msg(1, 0x485, 8)))
    self.assertTrue(self._tx(common.make_msg(0, 0x420, 8)))
    self.assertFalse(self._tx(common.make_msg(2, 0x420, 8)))


class TestHyundaiSoftwareCruiseSafety(unittest.TestCase):
  def test_stock_cruise_buttons_require_moving_cruise_authority(self):
    safety = libsafety_py.libsafety
    packer = CANPackerSafety("hyundai_can_generated")
    self.addCleanup(safety.set_alternative_experience, 0)

    def feed(*, cruise, speed, gas=0, brake=False, count=0):
      messages = (
        ("CLU11", {"CF_Clu_AliveCnt1": count, "CF_Clu_CruiseSwState": 2 if count == 0 else 0}, None),
        ("EMS16", {"CRUISE_LAMP_S": cruise, "CF_Ems_AclAct": gas, "AliveCounter": count}, checksum),
        ("WHL_SPD11", {"WHL_SPD_FL": speed, "WHL_SPD_AliveCounter_LSB": count}, checksum),
        ("TCS13", {"DriverOverride": 2 if brake else 0, "AliveCounterTCS": count}, checksum),
        ("MDPS12", {}, None),
      )
      for name, values, fix_checksum in messages:
        self.assertTrue(safety.safety_rx_hook(packer.make_can_msg_safety(name, 0, values, fix_checksum=fix_checksum)))

    def button(value, main=0):
      return packer.make_can_msg_safety("CLU11", 0, {"CF_Clu_CruiseSwState": value, "CF_Clu_CruiseSwMain": main})

    for word, experience, allowed in ((0x1000, 0, False), (0x1020, 0, True),
                                       (0x1020, 32, True), (0x1020, 33, False)):
      with self.subTest(word=word, experience=experience):
        safety.init_tests()
        safety.set_alternative_experience(experience)
        self.assertEqual(safety.set_safety_hooks(CarParams.SafetyModel.hyundai, word), 0)
        feed(cruise=True, speed=15)
        self.assertTrue(safety.safety_config_valid())
        self.assertTrue(safety.get_controls_allowed())
        self.assertEqual(safety.safety_tx_hook(button(2)), allowed)
        self.assertTrue(safety.safety_tx_hook(button(4)))
        if allowed:
          self.assertTrue(safety.safety_tx_hook(button(1)))
          self.assertFalse(safety.safety_tx_hook(button(2, main=1)))
          feed(cruise=True, speed=0, count=1)
          self.assertFalse(safety.safety_tx_hook(button(1)))
          self.assertFalse(safety.safety_tx_hook(button(2)))
          feed(cruise=True, speed=15, gas=1, count=2)
          self.assertFalse(safety.safety_tx_hook(button(2)))
          feed(cruise=True, speed=15, brake=True, count=3)
          self.assertFalse(safety.safety_tx_hook(button(2)))


if __name__ == "__main__":
  unittest.main()
