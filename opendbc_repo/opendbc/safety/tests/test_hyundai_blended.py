"""Stock mixed-bus native envelopes; stock SCC12 RX CRC remains unverified."""
import unittest
from opendbc.car.structs import CarParams
from opendbc.safety.tests import common
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.test_hyundai import checksum


class TestHyundaiBlendedStock(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.safety.set_alternative_experience(0)
    self.packer = common.CANPackerSafety('hyundai_can_generated')

  def select(self, hda2):
    self.bus = int(hda2)
    self.steer_counter = 0
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, 0x2010 if hda2 else 0x2000), 0)
    self.safety.init_tests()

  def scc(self, counter, active=False, crc=0, bus=None, length=8):
    data = bytearray(length)
    data[0], data[1], data[3] = crc, counter << 4, int(active) << 4
    return common.make_msg(self.bus if bus is None else bus, 0x421, dat=bytes(data))

  def steer(self, torque, request=True):
    if self.bus == 0:
      return self.packer.make_can_msg_safety('LKAS11', 0, {'CR_Lkas_StrToqReq': torque, 'CF_Lkas_ActToi': request})
    data = bytearray(16)
    raw = torque + 1024
    data[5], data[6] = (raw & 0x7F) << 1, ((raw >> 7) & 0xF) | (int(request) << 4)
    from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
    data[2] = self.steer_counter
    self.steer_counter = (self.steer_counter + 1) & 255
    data[:2] = hkg_can_fd_checksum(0x50, None, data).to_bytes(2, 'little')
    return common.make_msg(0, 0x50, dat=bytes(data))

  def test_scc_source_counter_and_explicit_crc_omission(self):
    for hda2 in (False, True):
      self.select(hda2)
      for counter in range(32):
        self.assertTrue(self.safety.safety_rx_hook(self.scc(counter % 16, crc=counter * 7 % 256)))
      for _ in range(6):
        valid = self.safety.safety_rx_hook(self.scc(15))
      self.assertFalse(valid)
      self.assertFalse(self.safety.get_controls_allowed())
      self.select(hda2)
      self.safety.safety_rx_hook(common.make_msg(self.bus, 0x4F1, dat=bytes([1, 0, 0, 0])))
      for message in (self.scc(0, True, bus=2), self.scc(0, True, length=7)):
        self.safety.safety_rx_hook(message)
        self.assertFalse(self.safety.get_controls_allowed())
      self.assertTrue(self.safety.safety_rx_hook(self.scc(0, True)))
      self.assertTrue(self.safety.get_controls_allowed())
      self.safety.safety_rx_hook(self.scc(1, False))
      self.assertFalse(self.safety.get_controls_allowed())

  def test_set_resume_cancel_and_no_longitudinal_ownership(self):
    for hda2 in (False, True):
      self.select(hda2)
      for controls in (False, True):
        for cruise in (False, True):
          self.safety.set_controls_allowed(controls)
          self.safety.set_cruise_engaged_prev(cruise)
          for button in (0, 1, 2, 3, 4, 5):
            expected = (button in (1, 2) and controls) or (button == 4 and cruise)
            self.assertEqual(self.safety.safety_tx_hook(common.make_msg(self.bus, 0x4F1,
              dat=bytes([button, 0, 0, 0]))), expected)
          # Stock SCC remains receiver/forwarding owned even while enabled.
          for address in (0x420, 0x421):
            for bus in (0, 1, 2):
              self.assertFalse(self.safety.safety_tx_hook(common.make_msg(bus, address)))

  def test_hda2_status_actual_payloads_and_reserved_bits(self):
    self.select(True)
    # Lossless unique coverage of the retained 92 stateless original packets.
    payloads = [bytes.fromhex(value) for value in (
      'bf00000400000000',
      'd850000400000000',
      '71a0000400000000',
      '16f0000400000000',
      '6140000400000000',
      'a790000400000000',
      'afe0000400000000',
      '6930000400000000',
      '1e80000400000000',
      '79d0000400000000',
      'd020000400000000',
      '6370000600000000',
      'c0c0000400000000',
      '0610000400000000',
      '0e60000400000000',
      'c8b0000400000000',
      'b770000400000000',
      '7be0000600000000',
    )]
    for controls in (False, True):
      self.safety.set_controls_allowed(controls)
      for payload in payloads:
        self.assertTrue(self.safety.safety_tx_hook(common.make_msg(1, 0x485, dat=payload)))
    status_packer = common.CANPackerSafety('hyundai_palisade_2023_generated')
    for icon in range(4):
      self.assertTrue(self.safety.safety_tx_hook(status_packer.make_can_msg_safety(
        'LFAHDA_MFC', 1, {'LFA_Icon_State': icon, 'COUNTER': 7, 'CHECKSUM': 0xA5})))
    for bus in (0, 2, 3):
      self.assertFalse(self.safety.safety_tx_hook(common.make_msg(bus, 0x485, dat=payloads[0])))
    for length in (0, 1, 2, 3, 4, 5, 6, 7, 12, 16, 20, 24, 32, 48, 64):
      self.assertFalse(self.safety.safety_tx_hook(common.make_msg(1, 0x485, length)))
    mask = (0xff, 0xf0, 0x00, 0x06, 0x00, 0x00, 0x00, 0x00)
    for index, allowed in enumerate(mask):
      for bit in range(8):
        if not allowed & (1 << bit):
          data = bytearray(payloads[0])
          data[index] |= 1 << bit
          self.assertFalse(self.safety.safety_tx_hook(common.make_msg(1, 0x485, dat=bytes(data))))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x485), 0)
    self.assertEqual(self.safety.safety_fwd_hook(0, 0x485), 2)
    self.safety.set_timer(2_000_000)
    self.safety.safety_rx_hook(common.make_msg(1, 0x485, dat=payloads[0]))
    self.assertFalse(self.safety.get_relay_malfunction(), 'Stock E-CAN status is not exclusive steering ownership')
    self.safety.safety_rx_hook(common.make_msg(0, 0x50, 16))
    self.assertTrue(self.safety.get_relay_malfunction())
    self.assertFalse(self.safety.safety_tx_hook(common.make_msg(1, 0x485, dat=payloads[0])))

  def test_required_sources_and_timeout(self):
    for hda2 in (False, True):
      sources = (None, 'EMS16', 'WHL_SPD11', 'TCS13', 'MDPS12', 'CLU11', 'SCC11', 'SCC12')
      for omitted in sources + (('CAM_0x2a4',) if hda2 else ()):
        self.select(hda2)
        for name, values, fix in (
          ('EMS16', {'AliveCounter': 0}, checksum), ('WHL_SPD11', {}, checksum),
          ('TCS13', {'AliveCounterTCS': 0}, checksum), ('MDPS12', {}, None),
          ('CLU11', {'CF_Clu_AliveCnt1': 0}, None),
        ):
          if name != omitted:
            self.assertTrue(self.safety.safety_rx_hook(self.packer.make_can_msg_safety(name, self.bus, values, fix_checksum=fix)))
        if omitted != 'SCC11':
          self.assertTrue(self.safety.safety_rx_hook(common.make_msg(self.bus, 0x420)))
        if omitted != 'SCC12':
          self.assertTrue(self.safety.safety_rx_hook(self.scc(0)))
        if hda2 and omitted != 'CAM_0x2a4':
          camera = common.CANPackerSafety('hyundai_palisade_2023_generated')
          self.assertTrue(self.safety.safety_rx_hook(camera.make_can_msg_safety('CAM_0x2a4', 2, {})))
        self.safety.set_timer(1000)
        self.safety.safety_tick()
        self.assertEqual(self.safety.safety_config_valid(), omitted is None)
        self.safety.set_controls_allowed(True)
        self.safety.set_timer(2_000_000)
        self.safety.safety_tick()
        self.assertFalse(self.safety.safety_config_valid())
        self.assertFalse(self.safety.get_controls_allowed())

  def test_torque_driver_rate_off_and_cancel(self):
    for hda2 in (False, True):
      self.select(hda2)
      maximum, rate = (384, 3) if hda2 else (404, 2)
      self.safety.set_torque_driver(0, 0)
      for allowed, torque, last, expected in (
        (False, 0, 0, True), (False, 1, 0, False), (True, rate, 0, True),
        (True, rate + 1, 0, False), (True, maximum, maximum, True), (True, maximum + 1, maximum, False),
      ):
        self.safety.set_controls_allowed(allowed)
        self.safety.set_desired_torque_last(last)
        self.safety.set_rt_torque_last(last)
        self.assertEqual(self.safety.safety_tx_hook(self.steer(torque, torque != 0)), expected)
      self.safety.set_controls_allowed(True)
      self.safety.set_desired_torque_last(100)
      self.safety.set_rt_torque_last(100)
      self.safety.set_torque_driver(-1000, -1000)
      self.assertFalse(self.safety.safety_tx_hook(self.steer(100)))
      self.safety.set_controls_allowed(False)
      self.safety.set_cruise_engaged_prev(False)
      for button in (1, 2, 4):
        self.assertFalse(self.safety.safety_tx_hook(common.make_msg(self.bus, 0x4F1, dat=bytes([button, 0, 0, 0]))))
      self.safety.set_cruise_engaged_prev(True)
      self.assertTrue(self.safety.safety_tx_hook(common.make_msg(self.bus, 0x4F1, dat=bytes([4, 0, 0, 0]))))

  def test_relay_exact_tx_and_stock_forwarding(self):
    for hda2 in (False, True):
      self.select(hda2)
      address, length = (0x50, 16) if hda2 else (0x340, 8)
      self.assertFalse(self.safety.safety_tx_hook(common.make_msg(0, address, 12 if hda2 else 7)))
      self.assertFalse(self.safety.safety_tx_hook(common.make_msg(1, address, length)))
      for denied in (0x110, 0x12A, 0x420, 0x421):
        self.assertFalse(self.safety.safety_tx_hook(common.make_msg(0, denied)))
      for stock in (0x420, 0x421):
        self.assertEqual(self.safety.safety_fwd_hook(2, stock), 0)
        self.assertEqual(self.safety.safety_fwd_hook(0, stock), 2)
      self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)
      self.safety.set_timer(2_000_000)
      self.safety.safety_rx_hook(common.make_msg(0, address, length))
      self.assertTrue(self.safety.get_relay_malfunction())
      self.assertFalse(self.safety.safety_tx_hook(self.steer(0, False)))

  def test_palisade_captured_status_preserves_steering_relay_shutdown(self):
    for steering_address, length in ((0x50, 16), (0x2A4, 24)):
      self.select(True)
      self.safety.set_timer(2_000_000)
      camera = bytes.fromhex('2dc35b01000008006401010200000000')
      suppression = bytes.fromhex('daed12000022000022000000000000000000000000000000')
      status = bytes.fromhex('5030020000000000')
      self.safety.safety_rx_hook(common.make_msg(2, 0x50, dat=camera))
      self.safety.safety_rx_hook(common.make_msg(2, 0x2A4, dat=suppression))
      self.assertFalse(self.safety.get_relay_malfunction())
      self.safety.safety_rx_hook(common.make_msg(1, 0x485, dat=status))
      self.assertFalse(self.safety.get_relay_malfunction())
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x50), -1)
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x2A4), -1)
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x485), 0)
      self.safety.safety_rx_hook(common.make_msg(0, steering_address, length))
      self.assertTrue(self.safety.get_relay_malfunction())
      self.assertFalse(self.safety.safety_tx_hook(common.make_msg(0, 0x50, 16)))

  def test_stock_status_ownership_preserves_both_stock_aol_experiences(self):
    for experience in (0, 32):
      self.safety.set_alternative_experience(experience)
      self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, 0x2010), 0)
      self.safety.init_tests()
      self.safety.set_alternative_experience(experience)
      self.safety.safety_rx_hook(common.make_msg(1, 0x485, dat=bytes.fromhex('5030020000000000')))
      self.assertFalse(self.safety.get_relay_malfunction())
      self.safety.safety_rx_hook(common.make_msg(0, 0x50, 16))
      self.assertTrue(self.safety.get_relay_malfunction())
    self.safety.set_alternative_experience(0)

  def _start_hda2_aol(self):
    self.safety.init_tests()
    self.safety.set_alternative_experience(32)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, 0x2010), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.steer_counter = 0
    self.bus = 1
    self._refresh_hda2_aol_rx(1_000_000)
    self.safety.aol_set_host_request(1)

  def _refresh_hda2_aol_rx(self, now):
    self.safety.set_timer(now)
    for status in self.safety.rx_status:
      status.msg_seen = status.valid_checksum = status.valid_quality_flag = True
      status.last_timestamp = now

  def test_hda2_brake_retains_aol_only_after_accepted_steering(self):
    for steering_before_brake in (False, True):
      self._start_hda2_aol()
      self.assertTrue(self.safety.safety_rx_hook(common.make_msg(1, 0x4F1, dat=bytes([1, 0, 0, 0]))))
      self.assertTrue(self.safety.safety_rx_hook(self.scc(0, True)))
      self.assertTrue(self.safety.get_controls_allowed())
      if steering_before_brake:
        self.assertTrue(self.safety.safety_tx_hook(self.steer(3)))
      self._refresh_hda2_aol_rx(1_010_000)
      brake = common.make_msg(1, 0x394, dat=bytes.fromhex('036c000003449798'))
      self.assertTrue(self.safety.safety_rx_hook(brake))
      self.assertFalse(self.safety.get_controls_allowed())
      self.assertEqual(bool(self.safety.safety_tx_hook(self.steer(3))), steering_before_brake)
      self.safety.aol_set_host_request(0)
      self.safety.aol_set_host_request(1)
      self.assertFalse(self.safety.safety_tx_hook(self.steer(3)))

  def test_hda2_idle_blocks_duplicate_only_while_host_steering_is_fresh(self):
    self._start_hda2_aol()
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x50), 0)
    self.assertTrue(self.safety.safety_tx_hook(self.steer(0, False)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x50), -1)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x421), 0)
    self._refresh_hda2_aol_rx(1_500_000)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x50), 0)
    self.safety.set_relay_malfunction(True)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x50), -1)

  def test_hda2_stock_engagement_retains_lateral_only_through_cancel_and_brake(self):
    for disarm in ('main', 'main_and_cancel', 'lease', 'relay', 'native_rx'):
      with self.subTest(disarm=disarm):
        self._start_hda2_aol()
        self.safety.aol_set_host_request(3)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        def button(value):
          return common.make_msg(1, 0x4F1, dat=bytes([value, 0, 0, 0]))

        def scc(counter, main, active):
          dat = bytearray(8)
          dat[1], dat[3] = counter << 4, (int(main) << 3) | (int(active) << 4)
          return common.make_msg(1, 0x421, dat=bytes(dat))

        self.assertTrue(self.safety.safety_rx_hook(button(4)))
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.assertTrue(self.safety.safety_rx_hook(button(2)))
        self.assertTrue(self.safety.safety_rx_hook(scc(0, True, True)))
        self.safety.aol_set_host_request(3)
        self.assertTrue(self.safety.get_controls_allowed())
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.assertTrue(self.safety.safety_tx_hook(self.steer(3)))

        self._refresh_hda2_aol_rx(1_010_000)
        self.assertTrue(self.safety.safety_rx_hook(button(4)))
        self.assertTrue(self.safety.safety_rx_hook(scc(1, True, False)))
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.assertTrue(self.safety.safety_tx_hook(self.steer(3)))
        self.assertFalse(self.safety.safety_tx_hook(common.make_msg(1, 0x421)))
        brake = common.make_msg(1, 0x394, dat=bytes.fromhex('036c000003449798'))
        self.assertTrue(self.safety.safety_rx_hook(brake))
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)

        if disarm == 'main':
          self.assertTrue(self.safety.safety_rx_hook(scc(2, False, False)))
        elif disarm == 'main_and_cancel':
          self.assertTrue(self.safety.safety_rx_hook(button(12)))
        elif disarm == 'lease':
          self._refresh_hda2_aol_rx(1_400_000)
        elif disarm == 'relay':
          self.safety.set_relay_malfunction(True)
        else:
          self.safety.set_timer(2_000_000)
          self.safety.safety_tick()
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.assertFalse(self.safety.safety_tx_hook(self.steer(3)))
