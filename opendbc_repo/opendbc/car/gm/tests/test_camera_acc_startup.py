"""Camera Alpha-long must replace every intercepted ACC stream at startup."""
import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_ordinary_camera import params, feed
from opendbc.car.gm.values import DBC, ORDINARY_CAMERA_ALPHA_CAR
from opendbc.safety.tests.libsafety import libsafety_py as native


class TestCameraAccStartup(unittest.TestCase):
  def test_startup_and_ready_transition_have_continuous_inactive_native_streams(self):
    for identity in ORDINARY_CAMERA_ALPHA_CAR:
      for camera in (False, True):
        with self.subTest(identity=identity, camera=camera):
          cp = params(identity, alpha=True, camera=camera)
          cp.alternativeExperience = 32
          ci = CarInterface(cp)
          packer = CANPacker(DBC[identity][Bus.pt])
          self.assertTrue(ci.CC.ordinary_camera_long)
          safety = native.libsafety
          safety.init_tests()
          safety.set_alternative_experience(cp.alternativeExperience)
          self.assertEqual(safety.set_safety_hooks(4, cp.safetyConfigs[0].safetyParam), 0)
          control = structs.CarControl.new_message()
          # Startup must force every actuator inactive even for an erroneous caller.
          control.enabled = control.latActive = control.longActive = True
          control.actuators.torque = 1.0
          control.actuators.accel = 2.0
          observed = {address: [] for address in (0x180, 0x2CB, 0x315, 0x370)}
          if camera:
            observed[0x2CD] = []
          loopback = []
          for tick in range(690):
            now = 6_180_000_000 + tick * 10_000_000
            safety.set_timer(now // 1000)
            safety.set_aol_test_heartbeat(True)
            safety.aol_set_host_request(0)
            state, packets = feed(ci, packer, now, cruise=False, camera=camera, counter=tick % 4, speed=0)
            for address, data, bus in packets:
              safety.safety_fwd_hook(bus, address)
              self.assertTrue(safety.safety_rx_hook(native.make_CANPacket(address, bus, data)))
            if loopback:
              ci.update([(now, loopback)])
            loopback = []
            if tick < 50:
              continue
            self.assertTrue(state.canValid)
            self.assertFalse(safety.get_controls_allowed())
            self.assertEqual(safety.aol_get_request_mask(), 0)
            startup = tick < 650
            command = control.as_reader() if startup else structs.CarControl.new_message().as_reader()
            output, frames = ci.apply(command, now, startup_keepalive=startup)
            self.assertEqual(output.torque, 0)
            self.assertEqual(output.brake, 0)
            for address, data, bus in frames:
              self.assertTrue(safety.safety_tx_hook(native.make_CANPacket(address, bus, data)), (hex(address), bus))
              if address == 0x180:
                self.assertFalse(data[0] & 8)
                self.assertEqual((data[0] & 7) | data[1], 0)
                loopback.append((address, data, 128))
              elif address == 0x2CB:
                self.assertFalse(data[0] & 1)
                self.assertFalse(data[1] & 0x20)
              elif address == 0x315:
                self.assertEqual(data[0] >> 4, 1)
                self.assertEqual(int.from_bytes(data[:2], 'big') & 0xFFF, 0)
              elif address == 0x370:
                self.assertFalse(data[2] & 0x80)
                self.assertEqual(data[1] & 7, 2)  # Factory ACC standby, with ACCCmdActive clear.
              if address in observed:
                self.assertEqual(bus, 0)
                observed[address].append((now, bytes(data)))
          for address, samples in observed.items():
            self.assertTrue(samples, hex(address))
            self.assertLessEqual(samples[0][0], 6_780_000_000)
            limit = 110_000_000 if address == 0x180 else 40_000_000
            self.assertLessEqual(max(b[0] - a[0] for a, b in zip(samples, samples[1:], strict=False)), limit, hex(address))
          for address in (0x2CB, 0x315):
            counters = [((data[0] >> 6) & 3) if address == 0x2CB else (data[4] & 3)
                        for _, data in observed[address]]
            self.assertTrue(all(b == (a + 1) % 4 for a, b in zip(counters, counters[1:], strict=False)))
