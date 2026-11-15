import unittest
from types import SimpleNamespace

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm.gmcan import create_bolt_regen_gear, create_bolt_regen_paddle, create_pedal_command, create_steering_control, pedal_crc
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR, DBC, GMSafetyFlags, PEDAL_BOLT_CAR
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.libsafety.libsafety_py import new_CANPacket


class TestGmBoltPedalSafety(unittest.TestCase):
  def setUp(self):
    self.packer = CANPacker(DBC[CAR.CHEVROLET_BOLT_CC_2017][Bus.pt])
    self.safety = libsafety_py.libsafety
    self.init_mode(GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_2017)

  def init_mode(self, variant, pedal=True):
    param = int(GMSafetyFlags.HW_CAM | GMSafetyFlags.EV | variant)
    if pedal:
      param |= int(GMSafetyFlags.PEDAL_LONG | GMSafetyFlags.PADDLE_SCHED)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, param), 0)
    self.safety.init_tests()
    self.safety.reset_recorded_can()

  @staticmethod
  def packet(msg):
    return libsafety_py.make_CANPacket(msg[0], msg[2], msg[1])

  def sensor(self, counter, state=0, gas=0., bad_adc=False, bad_crc=False):
    msg = self.packer.make_can_msg("GAS_SENSOR", 0, {
      "INTERCEPTOR_GAS": gas, "INTERCEPTOR_GAS2": gas,
      "STATE": state, "COUNTER_PEDAL": counter,
    })
    data = bytearray(msg[1])
    if bad_adc:
      data[0], data[1] = 0x10, 0
    data[5] = pedal_crc(data) ^ int(bad_crc)
    return self.packet((msg[0], bytes(data), 0))

  def stock(self, name, values):
    return self.packet(self.packer.make_can_msg(name, 0, values))

  def low_gear(self):
    return self.stock("ECMPRDNL2", {"PRNDL2": 6, "ManualMode": 0})

  def recorded(self):
    result = []
    for index in range(self.safety.get_recorded_can_count()):
      packet = new_CANPacket()
      self.assertTrue(self.safety.get_recorded_can(index, packet))
      result.append((packet.addr, packet.bus, bytes(packet.data[0:7 if packet.addr == 0xBD else 8])))
    return result

  def test_actual_parser_drive_admission_by_bolt_generation(self):
    from opendbc.car.gm.interface import CarInterface
    for candidate in PEDAL_BOLT_CAR:
      cp = params(candidate, True, True)
      packer = CANPacker(DBC[candidate][Bus.pt])
      ci = CarInterface(cp)
      cs = ci.CS
      controller = CarController(DBC[candidate], cp)
      cc = structs.CarControl(longActive=True)
      for tick, (gear, manual) in enumerate(((4, 0), (6, 0), (4, 1), (0, 0), (1, 0), (2, 0), (3, 0)), start=1):
        with self.subTest(candidate=candidate, gear=gear, manual=manual):
          raw = bytearray.fromhex("0264011d0100")
          raw[5] = pedal_crc(raw)
          frames = [(0x201, bytes(raw), 0),
                    packer.make_can_msg("ECMPRDNL2", 0, {"PRNDL2": gear, "ManualMode": manual}),
                    packer.make_can_msg("AcceleratorPedal2", 0, {"CruiseState": 0}),
                    packer.make_can_msg("ECMEngineStatus", 0, {"CruiseMainOn": 1})]
          now = 1_000_000_000 + tick * 600_000_000
          raw[4] = tick % 16
          raw[5] = pedal_crc(raw)
          frames[0] = (0x201, bytes(raw), 0)
          cs.out = ci.update([(now, frames)])
          active, _, low = controller.bolt_pedal_admission(cc, cs, now + 1)
          expected = not manual and (gear == 6 or (candidate in (CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL) and gear == 4))
          self.assertEqual(active, expected)
          self.assertEqual(low, not manual and (gear == 6 or (candidate == CAR.CHEVROLET_BOLT_CC_2022_2023 and gear == 4)))
          self.assertFalse(controller.bolt_pedal_admission(cc, cs, now + 100_000_001)[0])
          raw[4] = (tick + 8) % 16
          raw[5] = pedal_crc(raw)
          cs.out = ci.update([(now + 110_000_000, [(0x201, bytes(raw), 0), frames[2], frames[3]])])
          self.assertTrue(cs.pedal_sensor_healthy)
          # A fresh pedal sample cannot renew the independent physical gear receipt.
          self.assertFalse(controller.bolt_pedal_admission(cc, cs, now + 110_000_001)[0])
          raw[4] = (tick + 9) % 16
          raw[5] = pedal_crc(raw)
          cs.out = ci.update([(now + 120_000_000, [(0x201, bytes(raw), 0), frames[1], frames[2], frames[3]])])
          self.assertEqual(controller.bolt_pedal_admission(cc, cs, now + 120_000_001)[0], expected)
          if candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and expected:
            raw[4] = (tick + 10) % 16
            raw[5] = pedal_crc(raw)
            cs.out = ci.update([(now + 420_000_000, [(0x201, bytes(raw), 0), frames[1], frames[2]])])
            self.assertTrue(cs.pedal_sensor_healthy)
            self.assertTrue(cs.out.cruiseState.available)
            self.assertFalse(controller.bolt_pedal_admission(cc, cs, now + 420_000_001)[0])

  def test_pedal_forward_gear_and_spoofing_by_bolt_generation(self):
    variants = (GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_2017,
                GMSafetyFlags.NO_ACC,
                GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_GEN2,
                GMSafetyFlags.BOLT_ACC_PEDAL | GMSafetyFlags.BOLT_GEN2)
    for variant in variants:
      acc = bool(variant & GMSafetyFlags.BOLT_ACC_PEDAL)
      gen2 = bool(variant & GMSafetyFlags.BOLT_GEN2)
      for gear, manual in ((4, 0), (6, 0), (4, 1), (6, 1), (0, 0), (1, 0), (2, 0), (3, 0)):
        with self.subTest(variant=variant, gear=gear, manual=manual):
          self.init_mode(variant)
          self.safety.set_timer(1_000_000)
          self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": gear, "ManualMode": manual}))
          self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 0}))
          self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 1}))
          raw = bytearray.fromhex("0264011d0000")  # Recorded independent ADC tracks 612/285.
          raw[5] = pedal_crc(raw)
          self.safety.safety_rx_hook(self.packet((0x201, bytes(raw), 0)))
          self.safety.set_controls_allowed(True)
          allowed = not manual and (gear == 6 or ((acc or gen2) and gear == 4))
          self.assertEqual(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 1))), allowed)
          self.assertFalse(self.safety.safety_tx_hook(self.packet(create_bolt_regen_paddle(self.packer, True))))
          self.assertFalse(self.safety.safety_tx_hook(self.packet(create_bolt_regen_gear(self.packer, True, gen2))))
          self.safety.safety_rx_hook(self.stock("EBCMRegenPaddle", {"RegenPaddle": 0}))
          self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": gear, "ManualMode": manual}))
          self.assertEqual(bool(self.recorded()), not manual and (gear == 6 or (gen2 and not acc and gear == 4)))
          self.safety.safety_rx_hook(self.stock("EBCMRegenPaddle", {"RegenPaddle": 1}))
          self.safety.set_controls_allowed(True)
          self.assertFalse(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 2))))
          self.safety.set_timer(1_100_001)
          self.assertFalse(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 2))))
          self.assertTrue(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, 0., 2))))

  def test_adc_sensor_fault_checksum_counter_and_timeout(self):
    self.safety.safety_rx_hook(self.low_gear())
    self.safety.set_controls_allowed(True)
    self.assertTrue(self.safety.safety_rx_hook(self.sensor(1)))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 1))))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 1))))

    for counter, bad in enumerate((self.sensor(2, state=1), self.sensor(3, bad_adc=True),
                                   self.sensor(4, bad_crc=True), self.sensor(4)), start=2):
      self.safety.set_controls_allowed(True)
      self.safety.safety_rx_hook(bad)
      self.assertFalse(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, counter))))
      self.assertTrue(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, 0., counter))))

    self.safety.safety_rx_hook(self.sensor(5))
    self.safety.set_controls_allowed(True)
    self.safety.set_timer(100001)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 6))))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, 0., 6))))

  def test_sensor_physical_curve_and_driver_override(self):
    self.safety.safety_rx_hook(self.low_gear())
    for counter, gas in enumerate((0., 4., 10., 23., 24., 30., 128., 255.), start=1):
      with self.subTest(gas=gas):
        self.assertTrue(self.safety.safety_rx_hook(self.sensor(counter, gas=gas)))
        self.assertEqual(self.safety.get_gas_pressed_prev(), gas > 23.)
        self.safety.set_controls_allowed(True)
        self.assertEqual(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, counter))),
                         gas <= 23.)

  def test_paddle_waits_for_stock_phase_and_expires_without_feed(self):
    self.safety.safety_rx_hook(self.low_gear())
    self.safety.safety_rx_hook(self.sensor(1))
    self.safety.set_controls_allowed(True)
    paddle = create_bolt_regen_paddle(self.packer, True)
    gear = create_bolt_regen_gear(self.packer, True, False)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(paddle)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(gear)))
    self.assertEqual(self.recorded(), [])

    stock_paddle = self.stock("EBCMRegenPaddle", {"RegenPaddle": 0})
    stock_gear = self.stock("ECMPRDNL2", {"PRNDL2": 6})
    self.safety.safety_rx_hook(stock_paddle)
    self.safety.safety_rx_hook(stock_gear)
    self.assertEqual([(addr, bus) for addr, bus, _ in self.recorded()], [(0xBD, 0), (0x1F5, 0)])
    self.assertEqual(self.recorded()[0][2], paddle[1])
    self.assertEqual(self.recorded()[1][2], gear[1])

    self.safety.reset_recorded_can()
    self.safety.set_timer(150000)
    self.safety.safety_rx_hook(stock_paddle)
    self.safety.safety_rx_hook(stock_gear)
    self.assertEqual(self.recorded(), [])
    self.safety.safety_rx_hook(self.sensor(2))
    self.safety.set_controls_allowed(True)
    self.safety.safety_rx_hook(self.low_gear())
    self.assertFalse(self.safety.safety_tx_hook(self.packet(paddle)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(gear)))
    self.safety.safety_rx_hook(stock_paddle)
    self.safety.safety_rx_hook(stock_gear)
    self.assertEqual([(addr, bus) for addr, bus, _ in self.recorded()], [(0xBD, 0), (0x1F5, 0)])

  def test_host_25hz_feed_covers_each_stock_40hz_phase(self):
    for gen2, applied in ((False, 7), (True, 5)):
      for phase in (0, 5000, 10000, 15000, 20000):
        with self.subTest(gen2=gen2, phase=phase):
          self.init_mode(GMSafetyFlags.NO_ACC | (GMSafetyFlags.BOLT_GEN2 if gen2 else GMSafetyFlags.BOLT_2017))
          self.safety.safety_rx_hook(self.low_gear())
          self.assertTrue(self.safety.safety_rx_hook(self.sensor(0)))
          self.safety.set_controls_allowed(True)
          paddle = create_bolt_regen_paddle(self.packer, True)
          gear = create_bolt_regen_gear(self.packer, True, gen2)
          self.assertEqual(gear[1][3], applied)
          feeds = 0
          events = sorted([(t, 0) for t in range(0, 120001, 40000)] +
                          [(t, 1) for t in range(phase, 120001, 25000)])
          for timestamp, kind in events:
            self.safety.set_timer(timestamp)
            if kind == 0:
              feeds += 1
              self.assertTrue(self.safety.safety_rx_hook(self.sensor(feeds % 16)))
              self.assertTrue(self.safety.get_controls_allowed())
              self.safety.safety_rx_hook(self.low_gear())
              self.safety.reset_recorded_can()
              self.assertFalse(self.safety.safety_tx_hook(self.packet(paddle)))
              self.assertFalse(self.safety.safety_tx_hook(self.packet(gear)))
            else:
              self.safety.reset_recorded_can()
              self.safety.safety_rx_hook(self.stock("EBCMRegenPaddle", {"RegenPaddle": 0}))
              self.safety.safety_rx_hook(self.low_gear())
              self.assertEqual(self.recorded(), [(0xBD, 0, paddle[1]), (0x1F5, 0, gear[1])], timestamp)
          # Refresh physical sources independently, without renewing either host feed.
          for index, (age, allowed) in enumerate(((100000, True), (100001, False))):
            self.safety.set_timer(120000 + age)
            self.assertTrue(self.safety.safety_rx_hook(self.sensor((feeds + index + 1) % 16)))
            self.assertTrue(self.safety.get_controls_allowed())
            self.safety.safety_rx_hook(self.low_gear())
            self.safety.reset_recorded_can()
            self.safety.safety_rx_hook(self.stock("EBCMRegenPaddle", {"RegenPaddle": 0}))
            self.safety.safety_rx_hook(self.low_gear())
            self.assertEqual(self.recorded(), [(0xBD, 0, paddle[1]), (0x1F5, 0, gear[1])] if allowed else [])

  def test_fault_and_driver_source_changes_consume_pending_spoofs(self):
    self.safety.safety_rx_hook(self.sensor(1))
    self.safety.safety_rx_hook(self.low_gear())
    self.safety.set_controls_allowed(True)
    applied_gear = self.packet(create_bolt_regen_gear(self.packer, True, False))
    applied_paddle = self.packet(create_bolt_regen_paddle(self.packer, True))
    stock_paddle = self.stock("EBCMRegenPaddle", {"RegenPaddle": 0})
    self.assertFalse(self.safety.safety_tx_hook(applied_gear))
    self.assertFalse(self.safety.safety_tx_hook(applied_paddle))
    # A real brake edge clears the pending active request; regaining controls alone cannot replay it.
    self.safety.safety_rx_hook(self.packet((0xC9, b"\x00" * 5 + b"\x01\x00\x00", 0)))
    self.safety.safety_rx_hook(self.packet((0xC9, b"\x00" * 8, 0)))
    self.safety.set_controls_allowed(True)
    self.safety.safety_rx_hook(self.low_gear())
    self.safety.safety_rx_hook(stock_paddle)
    self.assertEqual(self.recorded(), [])
    self.assertFalse(self.safety.safety_tx_hook(applied_gear))
    self.assertFalse(self.safety.safety_tx_hook(applied_paddle))
    self.safety.safety_rx_hook(self.low_gear())
    self.safety.safety_rx_hook(stock_paddle)
    self.assertEqual([addr for addr, _, _ in self.recorded()], [0x1F5, 0xBD])

    for index, (prndl, manual) in enumerate(((1, 0), (2, 0), (4, 0), (6, 1))):
      with self.subTest(prndl=prndl, manual=manual):
        self.safety.reset_recorded_can()
        self.safety.safety_rx_hook(self.low_gear())
        self.assertEqual(self.recorded(), [(0x1F5, 0, bytes(applied_gear.data[0:8]))] if index == 0 else [])
        self.safety.set_controls_allowed(True)
        self.assertFalse(self.safety.safety_tx_hook(applied_gear))
        self.safety.reset_recorded_can()
        self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": prndl, "ManualMode": manual}))
        self.assertEqual(self.recorded(), [])
        self.assertFalse(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, prndl))))
        self.safety.safety_rx_hook(self.low_gear())
        self.assertEqual(self.recorded(), [])

    self.safety.reset_recorded_can()
    self.safety.safety_rx_hook(self.low_gear())
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(applied_paddle))
    self.safety.safety_rx_hook(self.stock("EBCMRegenPaddle", {"RegenPaddle": 2}))
    self.assertEqual(self.recorded(), [])
    self.safety.safety_rx_hook(stock_paddle)
    self.assertEqual(self.recorded(), [])
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(applied_paddle))
    self.safety.safety_rx_hook(stock_paddle)
    self.assertEqual([addr for addr, _, _ in self.recorded()], [0xBD])

  def test_owned_tx_only_and_stock_acc_sibling(self):
    self.safety.safety_rx_hook(self.sensor(1))
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.stock("ASCMGasRegenCmd", {"GasRegenCmd": 0})))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(self.packer.make_can_msg("ASCMSteeringButton", 2, {"ACCButtons": 6}))))

    self.init_mode(GMSafetyFlags.BOLT_ACC_PEDAL)
    self.safety.safety_rx_hook(self.sensor(1))
    self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 1}))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(self.packer.make_can_msg("ASCMSteeringButton", 2, {"ACCButtons": 6}))))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(self.packer.make_can_msg("ASCMSteeringButton", 2, {"ACCButtons": 2}))))
    self.assertFalse(self.safety.safety_tx_hook(self.stock("ASCMGasRegenCmd", {"GasRegenCmd": 0})))

    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, int(GMSafetyFlags.HW_CAM | GMSafetyFlags.EV)), 0)
    self.safety.init_tests()
    self.assertFalse(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 1))))

  def test_acc_pedal_dashboard_shape_and_accepted_forwarding_lease(self):
    from opendbc.car.gm.gmcan import create_acc_dashboard_command

    hud = structs.CarControl.HUDControl(leadDistanceBars=3, leadVisible=True)

    def dashboard(active=True, bus=0):
      return create_acc_dashboard_command(self.packer, bus, active, 72., hud, False, cruise_state=2)

    self.init_mode(GMSafetyFlags.BOLT_ACC_PEDAL | GMSafetyFlags.BOLT_GEN2)
    self.safety.set_timer(1)
    self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": 4, "ManualMode": 0}))
    self.safety.safety_rx_hook(self.sensor(1))
    self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 0}))
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 1}))
    for name in ("PSCMStatus", "EBCMWheelSpdRear", "EBCMRegenPaddle", "ASCMSteeringButton"):
      self.assertTrue(self.safety.safety_rx_hook(self.stock(name, {})))
    self.safety.safety_tick()
    self.assertTrue(self.safety.safety_config_valid())
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), 0)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(dashboard())))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), 0)
    self.safety.set_controls_allowed(True)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(dashboard())))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), -1)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(dashboard(bus=2))))
    original = dashboard()[1]
    for byte, mask in ((0, 2), (1, 4), (2, 0x40), (4, 2), (5, 4)):
      malformed = bytearray(original)
      malformed[byte] ^= mask
      self.assertFalse(self.safety.safety_tx_hook(self.packet((0x370, bytes(malformed), 0))))
    self.safety.set_timer(90000)
    self.safety.safety_rx_hook(self.sensor(2))
    self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": 4, "ManualMode": 0}))
    malformed = bytearray(original)
    malformed[0] |= 2
    self.assertFalse(self.safety.safety_tx_hook(self.packet((0x370, bytes(malformed), 0))))
    self.assertFalse(self.safety.safety_tx_hook(self.packet((0x370, original[:5], 0))))
    self.safety.set_timer(100002)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), 0)
    self.safety.safety_rx_hook(self.sensor(3))
    self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": 4, "ManualMode": 0}))
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(dashboard(False))))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), -1)
    invalid = bytearray(dashboard(False)[1])
    invalid[2] |= 0x10
    self.assertFalse(self.safety.safety_tx_hook(self.packet((0x370, bytes(invalid), 0))))
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 0}))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(dashboard(False))))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), 0)
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 1}))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), 0)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(dashboard(False))))
    self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": 2, "ManualMode": 0}))
    self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": 4, "ManualMode": 0}))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), 0)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(dashboard(False))))
    self.safety.safety_rx_hook(self.sensor(4, state=1))
    self.safety.safety_rx_hook(self.sensor(5))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), 0)
    for variant in (GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_2017, GMSafetyFlags.NO_ACC,
                    GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_GEN2):
      self.init_mode(variant)
      self.assertFalse(self.safety.safety_tx_hook(self.packet(dashboard(False))))
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), 0)

  def test_acc_pedal_paddle_display_is_fresh_and_never_grants_actuation(self):
    from opendbc.car.gm.gmcan import create_acc_dashboard_command

    hud = structs.CarControl.HUDControl(leadDistanceBars=3)
    for condition in ('fresh', 'released', 'paddle_stale', 'gear_stale', 'main_off'):
      with self.subTest(condition=condition):
        self.init_mode(GMSafetyFlags.BOLT_ACC_PEDAL | GMSafetyFlags.BOLT_GEN2)
        self.safety.set_timer(1)
        self.safety.safety_rx_hook(self.sensor(1))
        self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 0}))
        self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 1}))
        self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": 5, "ManualMode": 1}))
        self.safety.safety_rx_hook(self.stock("EBCMRegenPaddle", {"RegenPaddle": 2}))
        for name in ("PSCMStatus", "EBCMWheelSpdRear", "ASCMSteeringButton"):
          self.assertTrue(self.safety.safety_rx_hook(self.stock(name, {})))
        self.safety.safety_tick()
        self.assertTrue(self.safety.safety_config_valid())
        if condition in ('paddle_stale', 'gear_stale'):
          self.safety.set_timer(100002)
          self.safety.safety_rx_hook(self.sensor(2))
          if condition == 'paddle_stale':
            self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": 5, "ManualMode": 1}))
          else:
            self.safety.safety_rx_hook(self.stock("EBCMRegenPaddle", {"RegenPaddle": 2}))
        elif condition == 'released':
          self.safety.safety_rx_hook(self.stock("EBCMRegenPaddle", {"RegenPaddle": 0}))
        elif condition == 'main_off':
          self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 0}))
        self.assertFalse(self.safety.get_controls_allowed())
        dashboard = create_acc_dashboard_command(self.packer, 0, False, 72., hud, False, cruise_state=2)
        self.assertEqual(self.safety.safety_tx_hook(self.packet(dashboard)), condition == 'fresh')
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.safety.safety_fwd_hook(2, 0x370), -1 if condition == 'fresh' else 0)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 1))))

  def test_acc_pedal_friction_requires_exclusive_fresh_owner(self):
    chassis = CANPacker("gm_global_a_chassis")

    def brake(value, counter, mode=None, bus=0):
      mode = (1 if value == 0 else 0xA) if mode is None else mode
      raw = (0x1000 - value) & 0xFFF
      checksum = (0x10000 - (mode << 12) - raw - counter) & 0xFFFF
      return self.packet(chassis.make_can_msg("EBCMFrictionBrakeCmd", bus, {
        "FrictionBrakeCmd": -value, "FrictionBrakeMode": mode,
        "RollingCounter": counter, "FrictionBrakeChecksum": checksum,
      }))

    self.init_mode(GMSafetyFlags.BOLT_ACC_PEDAL | GMSafetyFlags.BOLT_GEN2)
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(brake(100, 0)))
    self.assertFalse(self.safety.safety_tx_hook(brake(0, 0)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x2CB), 0)

    self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": 4, "ManualMode": 0}))
    self.safety.safety_rx_hook(self.sensor(1))
    self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 0}))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(brake(100, 0)))
    self.assertFalse(self.safety.safety_tx_hook(brake(0, 0)))
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 1}))
    self.assertTrue(self.safety.safety_tx_hook(brake(100, 1)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), -1)
    self.assertFalse(self.safety.safety_tx_hook(brake(100, 1)))
    self.safety.set_timer(100001)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.assertFalse(self.safety.safety_tx_hook(brake(100, 2, mode=1)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.assertTrue(self.safety.safety_tx_hook(brake(0, 2)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), -1)
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 0}))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 1}))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.assertTrue(self.safety.safety_tx_hook(brake(0, 3)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), -1)
    self.safety.safety_rx_hook(self.sensor(2))
    self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": 4, "ManualMode": 0}))
    self.safety.set_controls_allowed(True)

    self.assertFalse(self.safety.safety_tx_hook(brake(401, 2)))
    self.assertTrue(self.safety.safety_tx_hook(brake(0, 2, mode=9)))
    self.assertFalse(self.safety.safety_tx_hook(brake(100, 3, mode=1)))
    self.assertFalse(self.safety.safety_tx_hook(brake(100, 3, bus=2)))
    invalid = bytearray(chassis.make_can_msg("EBCMFrictionBrakeCmd", 0, {
      "FrictionBrakeCmd": -100, "FrictionBrakeMode": 0xA,
      "RollingCounter": 3, "FrictionBrakeChecksum": (0x10000 - (0xA << 12) - (0x1000 - 100) - 3) & 0xFFFF,
    })[1])
    invalid[3] ^= 1
    self.assertFalse(self.safety.safety_tx_hook(self.packet((0x315, bytes(invalid), 0))))

    self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 1}))
    self.assertFalse(self.safety.safety_tx_hook(brake(100, 3)))
    self.assertFalse(self.safety.safety_tx_hook(brake(0, 3, mode=9)))
    self.assertFalse(self.safety.safety_tx_hook(brake(0, 3)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 0}))
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 0, "BrakePressed": 1}))
    self.assertFalse(self.safety.safety_tx_hook(brake(100, 3)))
    self.assertFalse(self.safety.safety_tx_hook(brake(0, 3)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.safety.set_timer(300001)
    self.assertFalse(self.safety.safety_tx_hook(brake(100, 0)))
    self.assertFalse(self.safety.safety_tx_hook(brake(0, 0)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)

    self.init_mode(GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_GEN2)
    self.assertFalse(self.safety.safety_tx_hook(brake(0, 0)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)

  def test_2017_steering_limit_is_conditional(self):
    steer = self.packet(create_steering_control(self.packer, 0, 400, 0, True))
    for variant, pedal, allowed in ((GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_2017, True, True),
                                    (GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_2017, False, True),
                                    (GMSafetyFlags.NO_ACC, True, False)):
      with self.subTest(variant=variant, pedal=pedal):
        self.init_mode(variant, pedal=pedal)
        self.safety.set_controls_allowed(True)
        self.safety.set_torque_driver(0, 0)
        self.safety.set_desired_torque_last(400)
        self.safety.set_rt_torque_last(400)
        self.assertEqual(self.safety.safety_tx_hook(steer), allowed)

  def test_stock_acc_ownership_requires_fresh_confirmed_disengagement(self):
    self.init_mode(GMSafetyFlags.BOLT_ACC_PEDAL | GMSafetyFlags.BOLT_GEN2)
    self.safety.safety_rx_hook(self.low_gear())

    def active(counter):
      return self.packet(create_pedal_command(self.packer, .2, counter))

    def disabled(counter):
      return self.packet(create_pedal_command(self.packer, 0., counter))
    self.safety.safety_rx_hook(self.sensor(1))
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(active(1)))  # startup status unknown
    self.assertTrue(self.safety.safety_tx_hook(disabled(1)))

    self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 0}))
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(active(2)))  # main state unknown
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 1}))
    self.assertTrue(self.safety.safety_tx_hook(active(2)))
    self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 1}))
    self.assertFalse(self.safety.safety_tx_hook(active(3)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(create_bolt_regen_paddle(self.packer, True))))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(create_bolt_regen_gear(self.packer, True, True))))
    self.assertTrue(self.safety.safety_tx_hook(disabled(3)))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(self.packer.make_can_msg("ASCMSteeringButton", 2, {"ACCButtons": 6}))))

    self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 0}))
    self.safety.safety_rx_hook(self.sensor(2))
    self.safety.set_controls_allowed(True)
    self.assertTrue(self.safety.safety_tx_hook(active(4)))
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 0}))
    self.assertFalse(self.safety.get_controls_allowed())
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(active(5)))
    self.assertTrue(self.safety.safety_tx_hook(disabled(5)))
    self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 1}))
    self.safety.set_timer(300001)
    self.safety.safety_rx_hook(self.sensor(3))
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(active(6)))
    self.assertTrue(self.safety.safety_tx_hook(disabled(6)))

  def test_scheduler_rejects_all_noncanonical_payloads(self):
    for variant in (GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_2017,
                    GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_GEN2):
      with self.subTest(variant=variant):
        self.init_mode(variant)
        self.safety.safety_rx_hook(self.low_gear())
        self.safety.safety_rx_hook(self.sensor(1))
        self.safety.set_controls_allowed(True)
        paddle = create_bolt_regen_paddle(self.packer, True)
        gear = create_bolt_regen_gear(self.packer, True, bool(variant & GMSafetyFlags.BOLT_GEN2))
        bad = bytearray(paddle[1])
        bad[1] = 1
        self.assertFalse(self.safety.safety_tx_hook(self.packet((paddle[0], bytes(bad), paddle[2]))))
        bad = bytearray(paddle[1])
        bad[0] = 0x30
        self.assertFalse(self.safety.safety_tx_hook(self.packet((paddle[0], bytes(bad), paddle[2]))))
        for index, replacement in ((0, 0x0D), (3, 0x0F), (5, 3), (6, 2)):
          bad = bytearray(gear[1])
          bad[index] = replacement
          self.assertFalse(self.safety.safety_tx_hook(self.packet((gear[0], bytes(bad), gear[2]))))
        wrong_generation = create_bolt_regen_gear(self.packer, True, not bool(variant & GMSafetyFlags.BOLT_GEN2))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(wrong_generation)))
        self.assertEqual(self.recorded(), [])

  def test_real_parser_controller_and_native_safety_four_variants(self):
    for candidate in PEDAL_BOLT_CAR:
      with self.subTest(candidate=candidate):
        cp = params(candidate, True, True)
        self.assertTrue(cp.openpilotLongitudinalControl)
        self.assertEqual(cp.safetyConfigs[0].safetyModel, CarParams.SafetyModel.gm)
        self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm,
                                                      cp.safetyConfigs[0].safetyParam), 0)
        self.safety.init_tests()
        self.safety.reset_recorded_can()
        self.safety.set_timer(950000)
        packer = CANPacker(DBC[candidate][Bus.pt])
        car_state = CarState(cp)
        parsers = car_state.get_can_parsers(cp)
        car_state.update(parsers)
        sensor = packer.make_can_msg("GAS_SENSOR", 0, {"INTERCEPTOR_GAS": 0,
                                                      "INTERCEPTOR_GAS2": 0,
                                                      "COUNTER_PEDAL": 1, "STATE": 0})
        sensor_data = bytearray(sensor[1])
        sensor_data[5] = pedal_crc(sensor_data)
        source = [(sensor[0], bytes(sensor_data), sensor[2]),
                  packer.make_can_msg("ECMPRDNL2", 0, {"PRNDL2": 6, "ManualMode": 0})]
        if candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL:
          source.append(packer.make_can_msg("AcceleratorPedal2", 0, {"CruiseState": 0}))
          source.append(packer.make_can_msg("ECMEngineStatus", 0, {"CruiseMainOn": 1}))
        parsers[Bus.pt].update([(950_000_000, source)])
        out = car_state.update(parsers)
        self.assertTrue(car_state.pedal_sensor_healthy)
        self.assertTrue(self.safety.safety_rx_hook(self.packet(source[0])))
        self.assertTrue(self.safety.safety_rx_hook(self.packet(source[1])))
        if len(source) > 2:
          self.assertTrue(self.safety.safety_rx_hook(self.packet(source[2])))
          self.assertTrue(self.safety.safety_rx_hook(self.packet(source[3])))
          self.assertFalse(out.cruiseState.enabled)
          self.assertEqual(car_state.stock_acc_status_ts_nanos, 950_000_000)
        self.safety.set_controls_allowed(True)
        self.assertEqual(out.gearShifter, structs.CarState.GearShifter.low)

        controller = CarController(DBC[candidate], cp)
        controller.frame = 4
        controller.last_steer_frame = 4
        control = structs.CarControl()
        control.enabled = True
        control.longActive = True
        control.actuators.accel = 1.
        out.vEgo = 12.
        cs = SimpleNamespace(out=out.as_reader(), pedal_sensor_healthy=car_state.pedal_sensor_healthy,
                             pedal_sensor_ts_nanos=car_state.pedal_sensor_ts_nanos,
                             stock_acc_status_ts_nanos=car_state.stock_acc_status_ts_nanos,
                             bolt_pedal_gear_ts_nanos=car_state.bolt_pedal_gear_ts_nanos,
                             bolt_pedal_main_ts_nanos=car_state.bolt_pedal_main_ts_nanos,
                             cam_lka_steering_cmd_counter=0, loopback_lka_steering_cmd_updated=False,
                             loopback_lka_steering_cmd_ts_nanos=1_000_000_000, pt_lka_steering_cmd_counter=0,
                             buttons_counter=0, steering_authority=car_state.steering_authority)
        _, commands = controller.update(control.as_reader(), cs, 1_000_000_000)
        acc_pedal = candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL
        self.assertEqual([msg[0] for msg in commands], ([0x3D1] if not acc_pedal else []) +
                         [0x200, 0x1F5, 0xBD])
        # Sparse input has no valid factory brake clock; it must not emit friction braking.
        self.assertNotIn(0x315, [msg[0] for msg in commands])
        self.safety.set_timer(1_000_000)
        if not acc_pedal:
          self.assertFalse(self.safety.safety_tx_hook(self.packet(commands[0])))  # Required physical status sources are absent.
          commands = commands[1:]
        self.assertTrue(self.safety.safety_tx_hook(self.packet(commands[0])))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(commands[1])))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(commands[2])))
        self.assertEqual(self.recorded(), [])
        self.safety.safety_rx_hook(self.packet(packer.make_can_msg("ECMPRDNL2", 0, {"PRNDL2": 6})))
        self.safety.safety_rx_hook(self.packet(packer.make_can_msg("EBCMRegenPaddle", 0, {"RegenPaddle": 0})))
        self.assertEqual([item[0] for item in self.recorded()], [0x1F5, 0xBD])

        controller.frame = 8
        self.safety.reset_recorded_can()
        _, released = controller.update(control.as_reader(), cs, 1_200_000_000)
        released = [msg for msg in released if msg[0] in (0x200, 0x1F5, 0xBD)]
        self.assertEqual([msg[0] for msg in released], [0x200, 0x1F5, 0xBD])
        self.assertEqual(released[0][1][0:4], b"\x00" * 4)
        self.assertEqual(released[1][1], b"\x0c\x0c\x00\x06\x00\x00\x01\x00")
        self.assertEqual(released[2][1], b"\x00" * 7)
        self.safety.set_timer(1_200_000)
        self.assertTrue(self.safety.safety_tx_hook(self.packet(released[0])))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(released[1])))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(released[2])))
        self.safety.safety_rx_hook(self.packet(packer.make_can_msg("ECMPRDNL2", 0, {"PRNDL2": 6})))
        self.safety.safety_rx_hook(self.packet(packer.make_can_msg("EBCMRegenPaddle", 0, {"RegenPaddle": 0})))
        self.assertEqual(self.recorded(), [(0x1F5, 0, released[1][1]), (0xBD, 0, released[2][1])])

  def test_equipped_acc_friction_uses_real_parser_clock_and_native_acceptance(self):
    from opendbc.car.gm.tests.test_bolt_pedal import TestBoltPedalStartupParser
    from opendbc.car.gm.tests.test_bolt_acc_pedal_friction import brake_fields
    candidate = CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL
    cp = params(candidate, True, True)
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x1CD)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, cp.safetyConfigs[0].safetyParam), 0)
    self.safety.init_tests()
    ci, out = TestBoltPedalStartupParser().stream(cp, native=self.safety)
    packer = CANPacker(DBC[candidate][Bus.pt])
    wheels = [packer.make_can_msg("EBCMWheelSpdFront", 0, {"FLWheelSpd": 43.2, "FRWheelSpd": 43.2}),
              packer.make_can_msg("EBCMWheelSpdRear", 0, {"RLWheelSpd": 43.2, "RRWheelSpd": 43.2,
                                                        "RLWheelDir": 1, "RRWheelDir": 1})]
    for message in wheels:
      self.assertTrue(self.safety.safety_rx_hook(self.packet(message)))
    out = ci.update([(3_490_000_000, wheels)])
    self.assertTrue(out.canValid)
    self.assertFalse(out.canTimeout)
    self.assertGreater(out.vEgo, 10.)
    self.assertTrue(self.safety.safety_config_valid())
    self.assertEqual(ci.CS.bolt_acc_brake_clock_ns, 3_480_000_000)
    self.assertEqual(ci.CS.bolt_acc_brake_clock_counter, 0)
    self.assertTrue(out.cruiseState.available)
    self.assertFalse(out.cruiseState.enabled)
    self.safety.set_controls_allowed(True)
    controller = CarController(DBC[candidate], cp)
    controller.frame = controller.last_steer_frame = 4
    control = structs.CarControl(enabled=True, longActive=True)
    control.actuators.accel = -3.
    control.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
    self.assertTrue(controller.bolt_pedal_admission(control.as_reader(), ci.CS, 3_490_000_000)[0])
    _, commands = controller.update(control.as_reader(), ci.CS, 3_490_000_000)
    friction = [msg for msg in commands if msg[0] == 0x315]
    self.assertEqual(len(friction), 1)
    mode, brake, counter = brake_fields(friction[0])
    self.assertGreater(brake, 0)
    self.assertNotEqual(mode, 1)
    self.assertEqual(counter, ci.CS.bolt_acc_brake_clock_counter)
    self.assertEqual(friction[0][2], 0)
    self.assertEqual(len(friction[0][1]), 5)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(friction[0])))
    controller.frame = 5
    _, duplicate = controller.update(control.as_reader(), ci.CS, 3_500_000_000)
    self.assertFalse(any(msg[0] == 0x315 for msg in duplicate))
    ci.update([(3_580_000_000, [])])
    controller.frame = 6
    _, expired = controller.update(control.as_reader(), ci.CS, 3_580_000_000)
    release = [msg for msg in expired if msg[0] == 0x315]
    self.assertEqual(len(release), 1)
    self.assertEqual(brake_fields(release[0]), (1, 0, 1))
    self.safety.set_timer(3_580_000)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(release[0])))
    controller.frame = 7
    _, still_expired = controller.update(control.as_reader(), ci.CS, 3_590_000_000)
    self.assertFalse(any(msg[0] == 0x315 for msg in still_expired))

  def test_recorded_independent_adc_keeps_set_lateral_and_driver_override(self):
    for sample in ('053502ba0164', '04e30286048f', '046c024108e3', '0279012a06f6'):
      with self.subTest(sample=sample):
        self.init_mode(GMSafetyFlags.NO_ACC)
        self.safety.set_timer(10_000)
        for name, values in (('PSCMStatus', {}), ('EBCMWheelSpdRear', {}),
                             ('ECMEngineStatus', {}), ('AcceleratorPedal2', {}),
                             ('ECMCruiseControl', {}),
                             ('EBCMRegenPaddle', {}), ('ECMPRDNL2', {'PRNDL2': 6})):
          self.assertTrue(self.safety.safety_rx_hook(self.stock(name, values)))
        self.assertTrue(self.safety.safety_rx_hook(self.sensor(0)))
        for button in (3, 1):
          self.safety.safety_rx_hook(self.stock('ASCMSteeringButton', {'ACCButtons': button}))
        self.safety.safety_tick()
        self.assertTrue(self.safety.get_controls_allowed())
        raw = bytes.fromhex(sample)
        self.assertEqual(pedal_crc(raw), raw[5])
        self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x201, 0, raw)))
        self.assertTrue(self.safety.safety_config_valid())
        self.assertTrue(self.safety.get_controls_allowed())
        steer = create_steering_control(self.packer, 0, 1, 0, True)
        self.assertTrue(self.safety.safety_tx_hook(self.packet(steer)))
        self.assertEqual(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 1))),
                         sample == '0279012a06f6')

  def test_adc_overflow_fault_does_not_weaken_synthetic_tx_pair(self):
    self.safety.safety_rx_hook(self.low_gear())
    self.safety.safety_rx_hook(self.sensor(0))
    for button in (3, 1):
      self.safety.safety_rx_hook(self.stock('ASCMSteeringButton', {'ACCButtons': button}))
    command = create_pedal_command(self.packer, .2, 1)
    bad_pair = bytearray(command[1])
    bad_pair[2:4] = (int.from_bytes(bad_pair[2:4], "big") + 12).to_bytes(2, "big")
    bad_pair[5] = pedal_crc(bad_pair)
    self.assertFalse(self.safety.safety_tx_hook(self.packet((command[0], bytes(bad_pair), command[2]))))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(command)))
    for track in (0, 2):
      for button in (3, 1):
        self.safety.safety_rx_hook(self.stock('ASCMSteeringButton', {'ACCButtons': button}))
      self.assertTrue(self.safety.get_controls_allowed())
      data = bytearray(self.sensor(track + 2).data[0:6])
      data[track], data[track + 1] = 0x10, 0
      data[5] = pedal_crc(data)
      self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x201, 0, data))
      self.assertFalse(self.safety.get_controls_allowed())

  def test_removed_bolt_profiles_keep_distinct_limits_and_reset_topology(self):
    canonical = (0xBD, 0x9D, 0x19D, 0x1CD)
    for index, word in enumerate(canonical):
      for selected in (0xE700 + index, word, 0xE700 + index):
        with self.subTest(index=index, word=selected):
          self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, selected), 0)
          self.safety.init_tests()
          for address in (0x409, 0x40A):
            self.assertEqual(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, 0, bytes(7))), selected != word)
            self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, 2, bytes(7))))
            self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, 0, bytes(8))))
            for byte in range(7):
              payload = bytearray(7)
              payload[byte] = 1
              self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, 0, payload)))
          self.safety.set_controls_allowed(True)
          self.safety.set_torque_driver(0, 0)
          self.safety.set_desired_torque_last(400)
          self.safety.set_rt_torque_last(400)
          self.assertEqual(self.safety.safety_tx_hook(self.packet(create_steering_control(self.packer, 0, 400, 0, True))), index == 0)
          self.assertEqual(self.safety.safety_fwd_hook(0, 0x184), -1)
          for address in (0x2CB, 0x2CD, 0x370, 0x3D1):
            self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, 0, bytes(8))))

  def test_removed_acc_brake_retains_accepted_producer_lease(self):
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, 0xE703), 0)
    self.safety.init_tests()
    self.safety.set_timer(1000)
    for frame in (self.low_gear(), self.sensor(1),
                  libsafety_py.make_CANPacket(0x3D1, 0, bytes(8)),
                  self.stock("AcceleratorPedal2", {"CruiseState": 0}),
                  self.stock("ECMEngineStatus", {"CruiseMainOn": 1})):
      self.assertTrue(self.safety.safety_rx_hook(frame))
    self.safety.set_controls_allowed(True)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    chassis = CANPacker("gm_global_a_chassis")
    brake = self.packet(chassis.make_can_msg("EBCMFrictionBrakeCmd", 0, {
      "FrictionBrakeCmd": 0, "FrictionBrakeMode": 1,
      "RollingCounter": 0, "FrictionBrakeChecksum": 0xF000,
    }))
    self.assertTrue(self.safety.safety_tx_hook(brake))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), -1)
    self.safety.set_timer(101001)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.assertTrue(self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 0})))
    self.assertFalse(self.safety.safety_tx_hook(brake))
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, 0xE703), 0)
    self.safety.init_tests()
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)

  def test_removed_acc_stock_cruise_withdraws_brake_lease_without_ecm_change(self):
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, 0xE703), 0)
    self.safety.init_tests()
    self.safety.set_timer(1000)
    for frame in (self.low_gear(), self.sensor(1),
                  self.stock("AcceleratorPedal2", {"CruiseState": 0}),
                  self.stock("ECMEngineStatus", {"CruiseMainOn": 1})):
      self.assertTrue(self.safety.safety_rx_hook(frame))
    chassis = CANPacker("gm_global_a_chassis")
    brake = self.packet(chassis.make_can_msg("EBCMFrictionBrakeCmd", 0, {
      "FrictionBrakeCmd": 0, "FrictionBrakeMode": 1,
      "RollingCounter": 0, "FrictionBrakeChecksum": 0xF000,
    }))
    self.assertFalse(self.safety.safety_tx_hook(brake))
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x3D1, 0, bytes(8))))
    self.assertTrue(self.safety.safety_tx_hook(brake))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), -1)
    active = bytes((0, 0, 0, 0, 128, 0, 0, 0))
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x3D1, 0, active)))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    self.assertFalse(self.safety.safety_tx_hook(brake))
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x3D1, 0, bytes(8))))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
    brake = self.packet(chassis.make_can_msg("EBCMFrictionBrakeCmd", 0, {
      "FrictionBrakeCmd": 0, "FrictionBrakeMode": 1,
      "RollingCounter": 1, "FrictionBrakeChecksum": 0xEFFF,
    }))
    self.assertTrue(self.safety.safety_tx_hook(brake))
    self.safety.set_timer(301001)
    self.assertTrue(self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 0})))
    self.assertTrue(self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 1})))
    self.assertFalse(self.safety.safety_tx_hook(brake))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)

  def test_removed_acc_cancel_observes_pt_cruise_without_camera_state(self):
    for word, expected in ((0xE703, True), (0x1CD, False)):
      self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
      self.safety.init_tests()
      self.safety.set_timer(1000)
      for frame in (self.stock("AcceleratorPedal2", {"CruiseState": 0}),
                    self.stock("ECMEngineStatus", {"CruiseMainOn": 1}),
                    libsafety_py.make_CANPacket(0x3D1, 0, bytes((0, 0, 0, 0, 128, 0, 0, 0)))):
        self.safety.safety_rx_hook(frame)
      for button in (2, 3, 6):
        command = self.packet(self.packer.make_can_msg("ASCMSteeringButton", 2, {"ACCButtons": button}))
        self.assertEqual(self.safety.safety_tx_hook(command), expected and button == 6)
      self.safety.set_timer(301001)
      command = self.packet(self.packer.make_can_msg("ASCMSteeringButton", 2, {"ACCButtons": 6}))
      self.assertFalse(self.safety.safety_tx_hook(command))

  def test_present_no_acc_cancel_admits_exact_three_profiles(self):
    from opendbc.car.gm.bolt_cc import button_bytes
    for word in (0xBD, 0x9D, 0x19D, 0x1CD):
      with self.subTest(word=word):
        self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
        self.safety.init_tests()
        self.safety.set_timer(1000)
        for frame in (self.low_gear(), self.sensor(1),
                      self.stock("ECMEngineStatus", {"CruiseMainOn": 1}),
                      libsafety_py.make_CANPacket(0x3D1, 0, bytes((0, 0, 0, 0, 128, 0, 0, 0))),
                      libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(1, 0))):
          self.safety.safety_rx_hook(frame)
        self.assertEqual(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, 1))), word != 0x1CD)
        self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, 1))))

  def test_no_acc_mode_zero_credit_all_counters_preserves_cancel_signature(self):
    from opendbc.car.gm.bolt_cc import button_bytes
    for word, counter in ((word, counter) for word in (0xBD, 0x9D, 0x19D, 0xE700, 0xE701, 0xE702, 0x1CD) for counter in range(4)):
      with self.subTest(word=word, counter=counter):
        self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
        self.safety.init_tests()
        self.safety.set_timer(1000)
        for frame in (self.low_gear(), self.sensor(1),
                      self.stock("ECMEngineStatus", {"CruiseMainOn": 1}),
                      libsafety_py.make_CANPacket(0x3D1, 0, bytes((0, 0, 0, 0, 128, 0, 0, 0))),
                      libsafety_py.make_CANPacket(0x1E1, 0, bytes((0, 0, 0, 0, counter,
                                                                 0x10 | ((0xF0 + counter * 0x3F0) >> 8), (0xF0 + counter * 0x3F0) & 0xFF)))):
          self.safety.safety_rx_hook(frame)
        self.assertEqual(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, (counter + 1) % 4))), word != 0x1CD)
        self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, (counter + 1) % 4))))

  def test_no_acc_mode_zero_corruption_cannot_grant_cancel_credit(self):
    from opendbc.car.gm.bolt_cc import button_bytes
    for word in (0xBD, 0x9D, 0x19D, 0xE700, 0xE701, 0xE702):
      for counter in range(4):
        checksum = 0xF0 + counter * 0x3F0
        neutral = bytes((0, 0, 0, 0, counter, 0x10 | (checksum >> 8), checksum & 0xFF))
        corruptions = []
        for index in range(7):
          damaged = bytearray(neutral)
          damaged[index] ^= 0x80
          corruptions.append(bytes(damaged))
        for mode in (2, 3):
          damaged = bytearray(neutral)
          damaged[3] = mode
          corruptions.append(bytes(damaged))
        corruptions.extend((neutral[:-1], neutral + b'\x00'))
        for raw in corruptions:
          with self.subTest(word=word, counter=counter, raw=raw.hex()):
            self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
            self.safety.init_tests()
            self.safety.set_timer(1000)
            for frame in (self.low_gear(), self.sensor(1),
                          self.stock('ECMEngineStatus', {'CruiseMainOn': 1}),
                          libsafety_py.make_CANPacket(0x3D1, 0, bytes((0, 0, 0, 0, 128, 0, 0, 0))),
                          libsafety_py.make_CANPacket(0x1E1, 0, raw)):
              self.safety.safety_rx_hook(frame)
            self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(
              0x1E1, 0, button_bytes(6, (counter + 1) % 4))))

  def test_removed_no_acc_gap_recovers_without_reusing_cancel_slot(self):
    from opendbc.car.gm.bolt_cc import button_bytes
    for word in (0x9D, 0xE700, 0xE701, 0xE702):
      with self.subTest(word=word):
        self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
        self.safety.init_tests()
        for time, counter in ((1000, 0), (151000, 1), (181000, 2)):
          self.safety.set_timer(time)
          for frame in (self.low_gear(), self.sensor(counter + 1),
                        self.stock("ECMEngineStatus", {"CruiseMainOn": 1}),
                        libsafety_py.make_CANPacket(0x3D1, 0, bytes((0, 0, 0, 0, 128, 0, 0, 0))),
                        libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(1, counter))):
            self.assertTrue(self.safety.safety_rx_hook(frame))
          accepted = self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, (counter + 1) % 4)))
          self.assertEqual(accepted, counter in (0, 2))
          self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, (counter + 1) % 4))))
        self.safety.set_timer(186000)
        self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(1, 2))))
        self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, 3))))

  def test_removed_e701_keeps_forward_gear_cancel_guard(self):
    from opendbc.car.gm.bolt_cc import button_bytes
    for gear, manual in ((0, 0), (1, 0), (4, 1), (4, 0), (6, 0)):
      with self.subTest(gear=gear, manual=manual):
        self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, 0xE701), 0)
        self.safety.init_tests()
        self.safety.set_timer(1000)
        for frame in (self.stock("ECMPRDNL2", {"PRNDL2": gear, "ManualMode": manual}), self.sensor(1),
                      self.stock("ECMEngineStatus", {"CruiseMainOn": 1}),
                      libsafety_py.make_CANPacket(0x3D1, 0, bytes((0, 0, 0, 0, 128, 0, 0, 0))),
                      libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(1, 0))):
          self.assertTrue(self.safety.safety_rx_hook(frame))
        self.assertEqual(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, 1))),
                         gear in (4, 6) and not manual)

  def test_removed_no_acc_cancel_requires_physical_one_shot_neutral_slot(self):
    from opendbc.car.gm.bolt_cc import button_bytes
    for word, gear in ((0xBD, None), (0xBD, 4), (0xBD, 6), (0x19D, None), (0x19D, 4), (0x19D, 6),
                       (0x9D, None), (0x9D, 4), (0x9D, 6), (0xE700, None), (0xE700, 4), (0xE700, 6), (0xE701, None), (0xE701, 4), (0xE701, 6),
                       (0xE702, None), (0xE702, 4), (0xE702, 6)):
      for button, bus, damaged in ((6, 0, False), (2, 0, False), (3, 0, False), (6, 2, False), (6, 0, True)):
        with self.subTest(word=word, gear=gear, button=button, bus=bus, damaged=damaged):
          self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
          self.safety.init_tests()
          self.safety.set_timer(1000)
          if gear is not None:
            self.assertTrue(self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": gear})))
          for frame in (self.sensor(1), self.stock("ECMEngineStatus", {"CruiseMainOn": 1}),
                        libsafety_py.make_CANPacket(0x3D1, 0, bytes((0, 0, 0, 0, 128, 0, 0, 0))),
                        libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(1, 0))):
            self.assertTrue(self.safety.safety_rx_hook(frame))
          data = bytearray(button_bytes(button, 1))
          if damaged:
            data[6] ^= 1
          self.safety.set_timer(2000)
          self.assertEqual(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, bus, data)),
                           gear is not None and button == 6 and bus == 0 and not damaged)
          self.assertEqual(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, 1))),
                           gear is not None and bus == 2)
          self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E1, 0, button_bytes(6, 1))))

  def test_acc_brake_counter_epoch_tracks_actual_producer_lease(self):
    chassis = CANPacker("gm_global_a_chassis")
    brake = self.packet(chassis.make_can_msg("EBCMFrictionBrakeCmd", 0, {
      "FrictionBrakeCmd": 0, "FrictionBrakeMode": 1,
      "RollingCounter": 0, "FrictionBrakeChecksum": 0xF000,
    }))
    bad_crc = self.packet(chassis.make_can_msg("EBCMFrictionBrakeCmd", 0, {
      "FrictionBrakeCmd": 0, "FrictionBrakeMode": 1,
      "RollingCounter": 1, "FrictionBrakeChecksum": 0xEFFE,
    }))
    for word in (0x1CD, 0xE703):
      for withdrawal in ("main", "ecm", "expiry", "pt_cruise"):
        if word == 0x1CD and withdrawal == "pt_cruise":
          continue
        with self.subTest(word=word, withdrawal=withdrawal):
          self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
          self.safety.init_tests()
          self.safety.set_timer(1000)
          for frame in (self.low_gear(), self.sensor(1),
                        self.stock("AcceleratorPedal2", {"CruiseState": 0}),
                        self.stock("ECMEngineStatus", {"CruiseMainOn": 1}),
                        libsafety_py.make_CANPacket(0x3D1, 0, bytes(8))):
            self.safety.safety_rx_hook(frame)
          self.assertTrue(self.safety.safety_tx_hook(brake))
          self.safety.set_timer(1500)
          self.assertFalse(self.safety.safety_tx_hook(bad_crc))
          self.assertFalse(self.safety.safety_tx_hook(brake))
          self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), -1)
          self.safety.set_timer(2000)
          if withdrawal == "main":
            self.safety.safety_rx_hook(self.stock("ECMEngineStatus", {"CruiseMainOn": 0}))
          elif withdrawal == "ecm":
            self.safety.safety_rx_hook(self.stock("AcceleratorPedal2", {"CruiseState": 2}))
          elif withdrawal == "pt_cruise":
            self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x3D1, 0, bytes((0, 0, 0, 0, 128, 0, 0, 0))))
          else:
            self.safety.set_timer(101001)
          self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
          for frame in (self.stock("AcceleratorPedal2", {"CruiseState": 0}),
                        self.stock("ECMEngineStatus", {"CruiseMainOn": 1}),
                        libsafety_py.make_CANPacket(0x3D1, 0, bytes(8))):
            self.safety.safety_rx_hook(frame)
          self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
          self.assertFalse(self.safety.safety_tx_hook(bad_crc))
          self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), 0)
          self.assertTrue(self.safety.safety_tx_hook(brake))
          self.assertFalse(self.safety.safety_tx_hook(brake))
          self.assertEqual(self.safety.safety_fwd_hook(2, 0x315), -1)

  def status_sources(self, now, counter=0, *, main=True, gear=6, brake=False, missing=None):
    from opendbc.car.gm.bolt_cc import button_bytes
    self.safety.set_timer(now)
    frames = (self.stock("PSCMStatus", {}), self.stock("EBCMWheelSpdRear", {}),
              self.packet((0x1E1, button_bytes(1, counter % 4), 0)),
              self.stock("AcceleratorPedal2", {}), self.stock("ECMEngineStatus", {"CruiseMainOn": main, "BrakePressed": brake}),
              self.sensor(counter % 16), self.stock("EBCMRegenPaddle", {}),
              self.stock("ECMPRDNL2", {"PRNDL2": gear}))
    for frame in frames:
      if frame.addr != missing:
        self.assertTrue(self.safety.safety_rx_hook(frame))

  def test_status_feed_forwarding_precedes_physical_rx(self):
    from opendbc.car.gm import gmcan
    from opendbc.car.gm.bolt_cc import button_bytes
    for word in (0xBD, 0x9D, 0x19D, 0xE700, 0xE701, 0xE702):
      with self.subTest(word=word):
        self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
        self.safety.init_tests()
        self.safety.reset_recorded_can()
        now = 1_000_000
        self.status_sources(now)
        status = self.packet(gmcan.create_ecm_cruise_control_command(self.packer, 0, True, 80.))
        raw = self.stock("ECMCruiseControl", {"CruiseActive": 1})
        self.assertFalse(self.safety.safety_tx_hook(status))
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)
        self.assertTrue(self.safety.safety_rx_hook(raw))
        self.assertFalse(self.recorded())
        self.assertFalse(self.safety.safety_tx_hook(status))
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), -1)
        self.assertTrue(self.safety.safety_rx_hook(raw))
        self.assertEqual(self.recorded(), [(0x3D1, 0, bytes(status.data[0:8]))])
        returned = self.stock("ECMCruiseControl", {"CruiseActive": 0})
        returned.returned = True
        self.safety.safety_rx_hook(returned)
        self.safety.safety_rx_hook(self.packet((0x3D1, bytes(status.data[0:8]), 2)))
        self.assertTrue(self.safety.safety_tx_hook(self.packet((0x1E1, button_bytes(6, 1), 0))))
        # Raw physical CruiseActive remains authoritative after emitted and returned status zeros.
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)
        self.safety.safety_rx_hook(raw)
        self.assertEqual(len(self.recorded()), 1)
        self.assertFalse(self.safety.safety_tx_hook(status))
        self.safety.set_timer(now + 100_001)
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)
        self.status_sources(now + 100_001, 1)
        self.safety.safety_rx_hook(raw)
        self.assertEqual(len(self.recorded()), 1)  # Fresh source RX cannot revive the expired feed.
        self.assertFalse(self.safety.safety_tx_hook(status))
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), -1)
        self.safety.safety_rx_hook(raw)
        self.assertEqual(len(self.recorded()), 2)

  def test_status_shape_and_intervening_loss_cannot_widen_authority(self):
    from opendbc.car.gm import gmcan
    for word in (0xBD, 0x9D, 0x19D, 0xE700, 0xE701, 0xE702):
      for loss in ("main", "brake", "gear", "sensor"):
        with self.subTest(word=word, loss=loss):
          self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word)
          self.safety.init_tests()
          self.safety.reset_recorded_can()
          self.status_sources(1_000_000)
          raw = self.stock("ECMCruiseControl", {"CruiseActive": 1})
          self.safety.safety_rx_hook(raw)
          status = gmcan.create_ecm_cruise_control_command(self.packer, 0, True, 80.)
          self.safety.safety_tx_hook(self.packet(status))
          if loss == "sensor":
            self.safety.safety_rx_hook(self.sensor(1, bad_crc=True))
          else:
            self.status_sources(1_010_000, 1, main=loss != "main", gear=2 if loss == "gear" else 6,
                                brake=loss == "brake")
          self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)
          self.status_sources(1_020_000, 2)
          self.safety.safety_rx_hook(raw)
          self.assertFalse(self.recorded())
          for index in (0, 1, 2, 4, 5, 6, 7):
            mutated = bytearray(status[1])
            mutated[index] ^= 0x80 if index == 2 else 1
            self.assertFalse(self.safety.safety_tx_hook(self.packet((0x3D1, bytes(mutated), 0))))
            self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)
          self.assertFalse(self.safety.safety_tx_hook(self.packet((0x3D1, status[1], 2))))

  def test_accelerator_override_keeps_only_dashboard_replacement(self):
    from opendbc.car.gm import gmcan
    for word in (0xBD, 0x9D, 0x19D, 0xE700, 0xE701, 0xE702):
      with self.subTest(word=word):
        self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word)
        self.safety.init_tests()
        self.safety.reset_recorded_can()
        self.status_sources(1_000_000)
        raw = self.stock('ECMCruiseControl', {})
        self.safety.safety_rx_hook(raw)
        self.assertTrue(self.safety.safety_rx_hook(self.sensor(1, gas=30.)))
        self.assertTrue(self.safety.get_gas_pressed_prev())
        status = gmcan.create_ecm_cruise_control_command(self.packer, 0, True, 80.)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(status)))
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), -1)
        self.safety.safety_rx_hook(raw)
        self.assertEqual(self.recorded(), [(0x3D1, 0, status[1])])
        self.safety.set_controls_allowed(True)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(create_pedal_command(self.packer, .2, 0))))
        self.safety.safety_tx_hook(self.packet(create_bolt_regen_paddle(self.packer, True)))
        self.safety.safety_rx_hook(self.stock("EBCMRegenPaddle", {}))
        self.assertEqual(self.recorded(), [(0x3D1, 0, status[1])])

  def test_status_physical_jitter_does_not_halve_output_cadence(self):
    from opendbc.car.gm import gmcan
    for word in (0xBD, 0x9D, 0x19D, 0xE700, 0xE701, 0xE702):
      with self.subTest(word=word):
        self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word)
        self.safety.init_tests()
        self.safety.reset_recorded_can()
        now = 1_000_000
        self.status_sources(now)
        raw = self.stock("ECMCruiseControl", {"CruiseActive": 1})
        self.safety.safety_rx_hook(raw)
        status = gmcan.create_ecm_cruise_control_command(self.packer, 0, True, 80.)
        source_counter = 0
        for counter, interval in enumerate((99_000, 99_000, 80_000, 120_000, 100_000, 99_000), start=1):
          # Keep independent physical sources and the 25 Hz host feed fresh between 10 Hz stock samples.
          remaining = interval
          while remaining:
            step = min(40_000, remaining)
            now += step
            remaining -= step
            source_counter += 1
            self.status_sources(now, source_counter)
            self.assertFalse(self.safety.safety_tx_hook(self.packet(status)))
          self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), -1)
          self.assertTrue(self.safety.safety_rx_hook(raw))
          self.assertEqual(self.recorded(), [(0x3D1, 0, status[1])] * counter)
          self.assertEqual(self.safety.safety_fwd_hook(0, 0x3D1), 2)
          self.assertTrue(self.safety.safety_rx_hook(raw))
          self.assertEqual(len(self.recorded()), counter)

  def test_gen2_drive_release_never_replays_over_changed_physical_gear(self):
    self.init_mode(GMSafetyFlags.NO_ACC | GMSafetyFlags.BOLT_GEN2)
    self.safety.set_timer(1_000_000)
    for current, following in ((4, 6), (6, 4)):
      self.safety.reset_recorded_can()
      self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": current, "ManualMode": 0}))
      release = create_bolt_regen_gear(self.packer, False, True, drive=current == 4)
      self.assertFalse(self.safety.safety_tx_hook(self.packet(release)))
      self.safety.safety_rx_hook(self.stock("ECMPRDNL2", {"PRNDL2": following, "ManualMode": 0}))
      self.assertEqual(self.recorded(), [])
