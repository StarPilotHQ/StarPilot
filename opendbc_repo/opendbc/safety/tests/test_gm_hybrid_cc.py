"""Hybrid-specific physical button credit, independent axes, and pedal envelope."""
import unittest

from opendbc.can import CANPacker
from opendbc.car.gm import gmcan
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py


class TestGmHybridCc(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.packer = CANPacker('gm_global_a_powertrain_generated')
    self.now = 1_000_000
    self.sensor_counter = 0

  def tearDown(self):
    self.safety.set_alternative_experience(0)

  @staticmethod
  def packet(frame):
    return libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])

  def reset(self, word=0xE800, alternative=32):
    self.word = word
    self.safety.init_tests()
    self.safety.set_alternative_experience(alternative)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
    self.now = 1_000_000
    self.sensor_counter = 0
    self.safety.set_timer(self.now)
    self.safety.set_aol_test_heartbeat(True)

  def rx(self, address, data, bus=0):
    return self.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, bytes(data)))

  def tx(self, address, data, bus=0):
    return self.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, bytes(data)))

  @staticmethod
  def button(word, counter=0, prefix=1):
    return bytes((0, 0, 0, prefix, counter, word >> 8, word & 255))

  def slot(self, word=0x15EE, counter=0):
    self.rx(0x1E1, self.button(word, counter))

  def feed(self, *, main=True, stock=False, gear=4, manual=False, brake=False, gas=False, regen=False,
           eps=0, tracks=(604, 304), missing=None):
    values = {
      'PSCMStatus': {'LKATorqueDeliveredStatus': eps},
      'EBCMWheelSpdRear': {'RLWheelSpd': 60, 'RRWheelSpd': 60, 'RLWheelDir': 1, 'RRWheelDir': 1},
      'AcceleratorPedal2': {'AcceleratorPedal2': 30 if gas else 0},
      'ECMEngineStatus': {'CruiseMainOn': main, 'BrakePressed': brake},
      'ECMCruiseControl': {'CruiseActive': stock},
      'ECMPRDNL2': {'PRNDL2': gear, 'ManualMode': manual},
      'EBCMRegenPaddle': {'RegenPaddle': 2 if regen else 0},
    }
    frames = [self.packer.make_can_msg(name, 0, val) for name, val in values.items()]
    if self.word % 2 == 0:
      frames += [self.packer.make_can_msg('ASCMLKASteeringCmd', 2, {}), self.packer.make_can_msg('AEBCmd', 2, {})]
    if self.word in (0xE802, 0xE803):
      a, b = tracks
      sensor = bytearray((a >> 8, a & 255, b >> 8, b & 255, self.sensor_counter & 15, 0))
      sensor[5] = gmcan.pedal_crc(sensor)
      self.sensor_counter += 1
      frames.append((0x201, bytes(sensor), 0))
    for frame in frames:
      if frame[0] != missing:
        self.safety.safety_rx_hook(self.packet(frame))
    if missing != 0x1E1:
      self.slot(0x15EE)
    self.safety.safety_tick()

  def warm(self, **kwargs):
    self.feed(**kwargs)
    self.advance(50_000)
    self.feed(**kwargs)
    self.slot(0x1FCC)
    self.safety.aol_set_host_request(3)

  def advance(self, us):
    self.now += us
    self.safety.set_timer(self.now)
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(3)

  def arm(self):
    self.slot(0x20EF)
    self.assertTrue(self.safety.get_controls_allowed())
    self.slot(0x15EE)

  def pedal(self, fraction=.2, counter=0):
    return self.safety.safety_tx_hook(self.packet(gmcan.create_pedal_command(self.packer, fraction, counter)))

  def test_physical_cancel_main_and_standard_set_have_distinct_rearm_effects(self):
    for physical_word in (0x55AE, 0x5F8C, 0x60AF, 0x6A8D):
      with self.subTest(physical_word=hex(physical_word)):
        self.reset(0xE801)
        self.warm()
        self.arm()
        self.slot(physical_word)
        self.assertFalse(self.safety.get_controls_allowed())
        self.slot(0x15EE)
        self.assertFalse(self.safety.get_controls_allowed())
        for counter in range(4):
          set_word = 0x3000 | (0xDF + counter * 0x4EF)
          self.slot(set_word, counter=counter)
          self.assertFalse(self.safety.get_controls_allowed())
          self.slot(0x15EE, counter=counter)
          self.assertTrue(self.safety.get_controls_allowed())
          self.slot(0x60AF)
          self.assertFalse(self.safety.get_controls_allowed())

  def test_either_rear_wheel_independently_reports_motion(self):
    for left, right, moving in ((0, 0, False), (0, 1, True), (1, 0, True)):
      self.reset(0xE801)
      frame = self.packer.make_can_msg('EBCMWheelSpdRear', 0,
                                      {'RLWheelSpd': left, 'RRWheelSpd': right, 'RLWheelDir': 1, 'RRWheelDir': 1})
      self.assertTrue(self.safety.safety_rx_hook(self.packet(frame)))
      self.assertEqual(self.safety.get_vehicle_moving(), moving)

  def test_phase_qualified_collision_and_one_shot_credit(self):
    for word in (0xE800, 0xE801):
      self.reset(word)
      self.warm()
      self.arm()
      # Unique phase1 means RES5F8C; collided659E cannot be interpreted as arbitrary RES/CANCEL.
      self.assertFalse(self.tx(0x1E1, self.button(0x659E)))
      self.assertTrue(self.tx(0x1E1, self.button(0x5F8C)))
      self.assertFalse(self.tx(0x1E1, self.button(0x5F8C)))
      self.advance(50_000)
      self.feed(stock=True)
      self.slot(0x1FCC)
      self.assertTrue(self.tx(0x1E1, self.button(0x60AF)))
      self.assertFalse(self.tx(0x1E1, self.button(0x60AF)))

  def test_standard_set_counter_and_gas_set_zero_are_distinct(self):
    self.reset()
    self.warm(stock=True)
    self.arm()
    self.slot(0x1FCC, counter=2)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(gmcan.create_buttons(self.packer, 0, 3, 3))))
    self.advance(50_000)
    self.feed(stock=True, gas=True)
    self.slot(0x1FCC, counter=2)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(gmcan.create_buttons(self.packer, 0, 3, 3))))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(gmcan.create_buttons(self.packer, 0, 0, 3))))

  def test_pedal_stock_takeover_cancel_does_not_grant_actuation(self):
    for word in (0xE802, 0xE803):
      self.reset(word)
      self.warm()
      self.arm()
      self.assertTrue(self.pedal())
      self.advance(50_000)
      self.feed(stock=True)
      self.slot(0x1FCC)
      self.assertEqual(self.safety.aol_get_permission_mask(), 1)
      self.assertFalse(self.pedal(counter=1))
      self.assertTrue(self.tx(0x1E1, self.button(0x60AF)))

  def test_stock_reduction_and_exact_profile_admission(self):
    for word in (0xE804, 0xE805):
      self.reset(word)
      self.warm(stock=True)
      self.assertEqual(self.safety.aol_get_request_mask(), 1)
      self.assertEqual(self.safety.aol_get_permission_mask(), 1)
      self.assertFalse(self.pedal())
      self.assertFalse(self.tx(0x1E1, self.button(0x659E)))
      self.assertFalse(self.safety.safety_tx_hook(self.packet(gmcan.create_buttons(self.packer, 0, 1, 3))))
      self.assertTrue(self.tx(0x1E1, self.button(0x60AF)))
      for address in (0x315, 0x2CB, 0x370, 0x409, 0x40A, 0xBD, 0x1F5):
        self.assertFalse(self.tx(address, bytes(7)))
    for word in (0xE806, 0xE80F, 0xE8FF):
      self.reset(word)
      self.assertFalse(self.tx(0x180, bytes(4)))
      self.assertFalse(self.tx(0x1E1, self.button(0x60AF)))

  def test_lateral_survives_driver_pedals_but_fault_and_reverse_withdraw(self):
    for word in range(0xE800, 0xE806):
      for physical in ({'brake': True}, {'gas': True}, {'regen': True}):
        self.reset(word)
        self.warm(**physical)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.assertFalse(self.pedal())
      for physical in ({'main': False}, {'gear': 2}, {'eps': 2}, {'eps': 3}):
        self.reset(word)
        self.warm(**physical)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.reset(word)
      self.warm(manual=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_source_expiry_requalification_and_mode_reset(self):
    self.reset(0xE803)
    self.warm()
    self.arm()
    self.advance(100_001)
    self.assertFalse(self.pedal())
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.feed()
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self.tx(0x1E1, self.button(0x5F8C)))
    self.slot(0x1FCC)
    self.arm()
    self.assertTrue(self.pedal(counter=1))
    self.reset(0xE805)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_independent_adc_crc_state_and_counter(self):
    self.reset(0xE803)
    self.warm()
    self.arm()
    self.assertTrue(self.pedal())
    for tracks in ((0, 4095), (4095, 0), (4096, 304)):
      self.advance(50_000)
      self.feed(tracks=tracks)
      self.assertFalse(self.pedal(counter=1))
    sensor = bytearray((2, 92, 1, 48, 0x10, 0))
    sensor[5] = gmcan.pedal_crc(sensor)
    self.rx(0x201, sensor)
    self.assertFalse(self.pedal(counter=2))
    sensor[4] = 1
    sensor[5] = gmcan.pedal_crc(sensor) ^ 1
    self.rx(0x201, sensor)
    self.assertFalse(self.pedal(counter=2))

  def test_removed_keepalive_and_pscm_exact_copy(self):
    for word in range(0xE800, 0xE806):
      self.reset(word)
      self.warm()
      for address in (0x409, 0x40A):
        expected = word in (0xE801, 0xE803)
        self.assertEqual(self.tx(address, bytes(7)), expected)
        self.assertFalse(self.tx(address, bytes((1, 0, 0, 0, 0, 0, 0))))
      copy = gmcan.create_pscm_status(self.packer, 2, dict.fromkeys(('HandsOffSWDetectionMode', 'HandsOffSWlDetectionStatus', 'LKATorqueDeliveredStatus',
                                                     'LKADriverAppldTrq', 'LKATorqueDelivered', 'LKATotalTorqueDelivered',
                                                     'RollingCounter', 'PSCMStatusChecksum'), 0))
      self.assertTrue(self.safety.safety_tx_hook(self.packet(copy)))
      self.assertFalse(self.safety.safety_tx_hook(self.packet(copy)))
      self.assertFalse(self.tx(0x184, bytes(8), 2))
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x180), -1)
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)

  def test_required_sources_bus_shape_and_individual_expiry(self):
    sources = ((0x184, 8, 0, 300_000), (0x34A, 5, 0, 100_000), (0x1E1, 7, 0, 100_000),
               (0x1C4, 8, 0, 300_000), (0xC9, 8, 0, 300_000), (0x3D1, 8, 0, 300_000),
               (0x1F5, 8, 0, 1_000_000), (0xBD, 7, 0, 100_000), (0x201, 6, 0, 100_000),
               (0x180, 4, 2, 1_000_000), (0x320, 6, 2, 1_000_000))
    for address, length, bus, limit in sources:
      with self.subTest(address=hex(address)):
        self.reset(0xE802)
        self.warm()
        self.rx(address, bytes(length - 1), bus)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.reset(0xE802)
        self.warm()
        saved = self.now
        for _tick in range(1, limit // 50_000 + 2):
          self.advance(50_000)
          self.feed(missing=address)
        self.assertGreater(self.now - saved, limit)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.reset(0xE802)
        self.feed(missing=address)
        self.rx(address, bytes(length), 1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_malformed_buttons_cannot_consume_or_create_credit(self):
    self.reset()
    self.warm()
    self.arm()
    valid = self.button(0x5F8C)
    for index in (0, 1, 2, 3, 4, 5, 6):
      malformed = bytearray(valid)
      malformed[index] ^= 0x80
      self.assertFalse(self.tx(0x1E1, malformed))
    self.assertFalse(self.tx(0x1E1, valid, 2))
    self.assertTrue(self.tx(0x1E1, valid))
    self.advance(50_000)
    self.feed()
    self.slot(0x1FCC, counter=1)
    standard = bytearray(gmcan.create_buttons(self.packer, 0, 2, 3)[1])
    standard[6] ^= 1
    self.assertFalse(self.tx(0x1E1, standard))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(gmcan.create_buttons(self.packer, 0, 2, 3))))

  def test_repeated_sensor_counter_withdraws_positive_output(self):
    self.reset(0xE803)
    self.warm()
    self.arm()
    self.assertTrue(self.pedal())
    sensor = bytearray((2, 92, 1, 48, (self.sensor_counter - 1) & 15, 0))
    sensor[5] = gmcan.pedal_crc(sensor)
    self.rx(0x201, sensor)
    self.assertFalse(self.pedal(counter=1))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def steering(self, torque=1, active=True):
    return self.safety.safety_tx_hook(self.packet(gmcan.create_steering_control(self.packer, 0, torque, 0, active)))

  def stock_frame(self, active):
    self.safety.safety_rx_hook(self.packet(self.packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': active})))

  def main_frame(self, main):
    self.safety.safety_rx_hook(self.packet(self.packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': main})))

  def test_stock_pcm_reengagement_is_independent_of_packet_order(self):
    for word in (0xE804, 0xE805):
      for alternative in (0, 32):
        for cruise_first in (False, True):
          self.reset(word, alternative)
          self.warm(stock=True)
          self.assertTrue(self.steering())
          self.main_frame(False)
          self.stock_frame(False)
          self.assertFalse(self.steering())
          self.advance(10_000)
          if cruise_first:
            self.stock_frame(True)
            self.assertFalse(self.steering())
            self.main_frame(True)
          else:
            self.main_frame(True)
            # Typed lateral is independent of stock cruise; ordinary AE0 is not.
            self.assertEqual(self.steering(), alternative == 32)
            self.stock_frame(True)
          self.assertTrue(self.steering())
        self.reset(word, alternative)
        self.warm(stock=False)
        self.slot(0x20EF)
        if alternative == 0:
          self.assertFalse(self.steering())
        self.assertFalse(self.safety.get_controls_allowed())

  def test_stock_temporary_eps_suppresses_without_permanent_rearm(self):
    for word in (0xE804, 0xE805):
      for alternative in (0, 32):
        self.reset(word, alternative)
        self.warm(stock=True)
        self.assertTrue(self.steering())
        self.advance(10_000)
        self.feed(stock=True, eps=2)
        self.assertFalse(self.steering())
        self.assertTrue(self.steering(0, False))
        self.advance(10_000)
        self.feed(stock=True)
        self.assertTrue(self.steering())
        self.advance(10_000)
        self.feed(stock=True, eps=3)
        self.assertFalse(self.steering())
        self.advance(10_000)
        self.feed(stock=True)
        if alternative == 0:
          self.assertFalse(self.steering())
        self.main_frame(False)
        self.main_frame(True)
        self.assertTrue(self.steering())

  def test_stock_held_cruise_cannot_rearm_after_driver_or_source_withdrawal(self):
    for physical in ({'brake': True}, {'gas': True}, {'regen': True}, {'gear': 2}):
      self.reset(0xE804, 0)
      self.warm(stock=True)
      self.assertTrue(self.steering())
      self.advance(10_000)
      self.feed(stock=True, **physical)
      self.assertFalse(self.steering())
      self.advance(10_000)
      self.feed(stock=True)
      self.assertFalse(self.steering())
      self.stock_frame(False)
      self.stock_frame(True)
      self.assertTrue(self.steering())
    self.reset(0xE804, 0)
    self.warm(stock=True)
    self.advance(100_001)
    self.assertFalse(self.steering())
    self.feed(stock=True)
    self.assertFalse(self.steering())
    self.main_frame(False)
    self.main_frame(True)
    self.assertTrue(self.steering())

  def test_active_temporary_eps_preserves_physical_latch_and_suppresses_outputs(self):
    for word in range(0xE800, 0xE804):
      for alternative in (0, 32):
        self.reset(word, alternative)
        self.warm()
        self.arm()
        self.assertTrue(self.steering())
        self.advance(10_000)
        self.feed(eps=2)
        self.assertFalse(self.steering())
        self.assertTrue(self.steering(0, False))
        self.assertFalse(self.pedal())
        self.assertFalse(self.tx(0x1E1, self.button(0x5F8C)))
        if alternative == 32:
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.advance(10_000)
        self.feed()
        self.assertTrue(self.safety.get_controls_allowed())
        self.assertTrue(self.steering())
        if word in (0xE802, 0xE803):
          self.assertTrue(self.pedal(counter=1))
        self.advance(10_000)
        self.feed(eps=3)
        self.assertFalse(self.steering())
        self.advance(10_000)
        self.feed()
        self.assertFalse(self.safety.get_controls_allowed())
        if alternative == 0:
          self.assertFalse(self.steering())

  def test_temporary_eps_cannot_create_a_new_physical_engagement(self):
    for word in range(0xE800, 0xE806):
      self.reset(word, 0)
      self.warm(stock=False)
      self.advance(10_000)
      self.feed(eps=2)
      if word >= 0xE804:
        self.stock_frame(True)
      else:
        self.slot(0x20EF)
      self.assertFalse(self.safety.get_controls_allowed())
      self.advance(10_000)
      self.feed(stock=word >= 0xE804)
      self.assertFalse(self.safety.get_controls_allowed())
      self.assertFalse(self.steering())

  def test_status_direct_shape_lease_and_source_recovery(self):
    for word in range(0xE800, 0xE806):
      with self.subTest(word=word):
        self.reset(word)
        self.warm(stock=True)
        frame = gmcan.create_ecm_cruise_control_command(self.packer, 0, True, 80.)
        supported = word in (0xE802, 0xE803)
        self.assertEqual(self.safety.safety_tx_hook(self.packet(frame)), supported)
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), -1 if supported else 2)
        if not supported:
          continue
        malformed = bytearray(frame[1])
        malformed[4] = 0x80
        self.assertFalse(self.tx(0x3D1, malformed))
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)  # Rejected candidate revokes the lease.
        self.advance(40_000)
        self.feed(stock=True)
        self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)))
        self.advance(100_001)
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)
        self.feed(stock=True)
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)  # No lease revival on source refresh.
        self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)))
        self.feed(stock=True, brake=True)
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)
        self.feed(stock=True)
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)
        self.advance(40_000)
        self.feed(stock=True)
        self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)))
        self.assertFalse(self.safety.safety_tx_hook(self.packet((frame[0], frame[1], 2))))
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), -1)  # Whitelist rejection never reaches the owner.
        self.assertFalse(self.safety.safety_tx_hook(self.packet(frame)))
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), -1)  # Cadence rejection preserves the bounded prior lease.
