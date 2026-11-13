import unittest
from opendbc.car import structs
from opendbc.can.dbc import DBC
from opendbc.safety.tests.common import CANPackerSafety, make_msg
from opendbc.safety.tests.libsafety import libsafety_py


class TestVolkswagenStockAol(unittest.TestCase):
  def tearDown(self):
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def configure(self, word=8, experience=32):
    self.safety = libsafety_py.libsafety
    self.safety.init_tests()
    self.safety.set_alternative_experience(experience)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.volkswagen, word)
    self.safety.set_timer(1_000_000)
    self.safety.set_aol_test_heartbeat(True)
    self.packer = CANPackerSafety("vw_mqb")
    self.dbc = DBC("vw_mqb")
    self.time = 1_000_000
    self.manual = word == 24

  def packet(self, name, fields, bus=0):
    assert not fields.keys() - self.dbc.name_to_msg[name].sigs.keys()
    return self.packer.make_can_msg_safety(name, bus, fields)

  def feed(self, main=True, engaged=False, gear=True, temporary=False, permanent=False,
           cancel=False, brake=False, moving=True, omit=None, wrong_bus=None):
    self.time += 10_000
    self.safety.set_timer(self.time)
    gear_source = ("Gateway_72", {"BCM1_Rueckfahrlicht_Schalter": int(not gear)}) if self.manual else ("Gateway_73", {"GE_Fahrstufe": 8 if gear else 6})
    sources = (
      (gear_source[0], gear_source[1]),
      ("ESP_19", {"ESP_VL_Radgeschw_02": 36 if moving else 0, "ESP_VR_Radgeschw_02": 36 if moving else 0,
                  "ESP_HL_Radgeschw_02": 36 if moving else 0, "ESP_HR_Radgeschw_02": 36 if moving else 0}),
      ("LH_EPS_03", {"EPS_HCA_Status": 0 if permanent else 4 if temporary else 3, "EPS_Lenkmoment": 0}),
      ("ESP_05", {"ESP_Fahrer_bremst": int(brake)}),
      ("Motor_20", {"MO_Fahrpedalrohwert_01": 0}),
      ("Motor_14", {"MO_Fahrer_bremst": int(brake)}),
      ("GRA_ACC_01", {"GRA_Abbrechen": int(cancel)}),
      ("TSK_06", {"TSK_Status": 3 if engaged else 2 if main else 0}),
    )
    for name, fields in sources:
      if name != omit:
        self.safety.safety_rx_hook(self.packet(name, fields, 1 if name == wrong_bus else 0))
    self.safety.safety_tick()
    self.safety.aol_set_host_request(1)

  def arm(self):
    for _ in range(12):
      self.feed()
    self.feed(main=False)
    self.feed()
    assert self.safety.aol_get_permission_mask() == 1

  def test_main_cycle_native_ownership_and_long_denial(self):
    for word in (8, 24):
      self.configure(word)
      self.safety.aol_set_host_request(3)
      assert self.safety.aol_get_permission_mask() == 0  # No complete physical source snapshot yet.
      self.feed()
      assert not self.safety.get_controls_allowed()
      assert self.safety.aol_get_permission_mask() == 1  # Complete fresh MAIN snapshot grants LAT only.
      self.arm()
      assert self.safety.safety_fwd_hook(2, 0x126) == 0
      torque = self.packet("HCA_01", {"HCA_01_LM_Offset": 4, "HCA_01_Sendestatus": 1})
      assert self.safety.safety_tx_hook(torque)
      assert self.safety.safety_fwd_hook(2, 0x126) == -1
      self.safety.aol_set_host_request(3)
      assert self.safety.aol_get_permission_mask() == 1
      assert not self.safety.safety_tx_hook(self.packet("ACC_06", {"ACC_Sollbeschleunigung_02": 0.5}))
      assert not self.safety.safety_tx_hook(self.packet("GRA_ACC_01", {"GRA_Tip_Wiederaufnahme": 1}))
      self.safety.aol_set_host_request(0)
      assert self.safety.aol_get_permission_mask() == 0
      assert self.safety.safety_fwd_hook(2, 0x126) == 0
      self.feed(cancel=True)
      assert not self.safety.get_controls_allowed()
      assert self.safety.aol_get_permission_mask() == 1
      self.feed()
      assert self.safety.aol_get_permission_mask() == 1

  def test_temporary_brake_and_fault_recovery_preserves_only_arm(self):
    for word in (8, 24):
      self.configure(word)
      self.arm()
      self.feed(brake=True)
      assert self.safety.aol_get_permission_mask() == 1
      for kwargs in ({"temporary": True}, {"moving": False}, {"gear": False}):
        self.feed(**kwargs)
        assert self.safety.aol_get_permission_mask() == 0
        self.feed()
        assert self.safety.aol_get_permission_mask() == 1
      self.feed(permanent=True)
      assert self.safety.aol_get_permission_mask() == 0
      self.feed()
      assert self.safety.aol_get_permission_mask() == 0

  def test_each_source_missing_wrong_bus_or_length_expires(self):
    for word in (8, 24):
      for source in ("ESP_19", "LH_EPS_03", "ESP_05", "Motor_20", "Motor_14", "GRA_ACC_01", "TSK_06",
                     "Gateway_72" if word == 24 else "Gateway_73"):
        for fault in ("missing", "bus", "length"):
          with self.subTest(word=word, source=source, fault=fault):
            self.configure(word)
            self.arm()
            for _ in range(120):
              self.feed(omit=source if fault != "bus" else None, wrong_bus=source if fault == "bus" else None)
              if fault == "length":
                self.safety.safety_rx_hook(make_msg(0, self.dbc.name_to_msg[source].address, dat=bytes(7)))
            assert self.safety.aol_get_permission_mask() == 0
            assert self.safety.safety_fwd_hook(2, 0x126) == 0

  def test_heartbeat_expiry_and_illegal_profile_never_rearms(self):
    self.configure()
    self.arm()
    self.safety.set_aol_test_heartbeat(False)
    assert self.safety.aol_get_permission_mask() == 0
    self.safety.set_aol_test_heartbeat(True)
    self.feed()
    assert self.safety.aol_get_permission_mask() == 0
    self.arm()
    self.safety.set_timer(self.time + 300_001)
    assert self.safety.aol_get_permission_mask() == 0
    for word, exp in ((8, 0), (24, 0), (9, 32), (25, 32), (0, 32), (56, 32)):
      self.configure(word, exp)
      assert self.safety.aol_get_permission_mask() == 0
      assert not self.safety.safety_tx_hook(self.packet("HCA_01", {"HCA_01_LM_Offset": 4, "HCA_01_Sendestatus": 1}))

  def test_crc_relay_and_rejected_torque_withdraw_forwarding(self):
    self.configure()
    self.arm()
    assert not self.safety.safety_tx_hook(self.packet("HCA_01", {"HCA_01_LM_Offset": 301, "HCA_01_Sendestatus": 1}))
    assert self.safety.safety_fwd_hook(2, 0x126) == 0
    assert self.safety.safety_tx_hook(self.packet("HCA_01", {"HCA_01_LM_Offset": 0, "HCA_01_Sendestatus": 0}))
    assert self.safety.safety_fwd_hook(2, 0x126) == 0
    packet = self.packet("TSK_06", {"TSK_Status": 2})
    packet[0].data[0] ^= 0xFF
    assert not self.safety.safety_rx_hook(packet)
    assert self.safety.aol_get_permission_mask() == 0
    self.arm()
    self.safety.set_relay_malfunction(True)
    assert self.safety.aol_get_permission_mask() == 0
    assert not self.safety.safety_tx_hook(self.packet("HCA_01", {"HCA_01_LM_Offset": 0, "HCA_01_Sendestatus": 0}))

  def test_fresh_accepted_hud_eps_forwarding_and_ownership_expiry(self):
    self.configure()
    self.arm()
    assert self.safety.safety_tx_hook(self.packet('HCA_01', {'HCA_01_LM_Offset': 4, 'HCA_01_Sendestatus': 1}))
    assert self.safety.safety_tx_hook(self.packet('LDW_02', {}))
    assert self.safety.safety_tx_hook(self.packet('LH_EPS_03', {'EPS_HCA_Status': 3}, 2))
    assert self.safety.safety_fwd_hook(2, 0x397) == -1
    assert self.safety.safety_fwd_hook(0, 0x9F) == -1
    assert self.safety.safety_fwd_hook(2, 0x777) == 0
    self.safety.set_timer(self.time + 300_001)
    assert self.safety.aol_get_permission_mask() == 0
    assert self.safety.safety_fwd_hook(2, 0x397) == 0
    assert self.safety.safety_fwd_hook(0, 0x9F) == 2
