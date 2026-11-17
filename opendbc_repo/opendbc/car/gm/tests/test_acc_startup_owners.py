"""All finalized GM ACC owners service the same disabled producer during startup."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from opendbc.car import structs
from opendbc.car.gm import gmcan
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm.values import DBC, GMFlags
from opendbc.safety.tests.libsafety import libsafety_py as native
from openpilot.starpilot.aol.tests.test_gm import _configurations


def configurations():
  unique = {}
  for cp in _configurations():
    key = (str(cp.carFingerprint), cp.safetyConfigs[0].safetyParam, cp.flags, cp.openpilotLongitudinalControl)
    if not cp.passive and not cp.dashcamOnly:
      unique[key] = cp
  return unique.values()


def state(cp):
  cs = CarState(cp)
  out = structs.CarState.new_message()
  out.canValid = True
  out.gearShifter = structs.CarState.GearShifter.reverse
  cs.out = out.as_reader()
  cs.pscm_status = dict.fromkeys(('HandsOffSWDetectionMode', 'HandsOffSWlDetectionStatus', 'LKATorqueDeliveredStatus',
                                'LKADriverAppldTrq', 'LKATorqueDelivered', 'LKATotalTorqueDelivered',
                                'RollingCounter', 'PSCMStatusChecksum'), 0)
  return cs


class TestAccStartupOwners(unittest.TestCase):
  def test_every_full_acc_owner_emits_inactive_native_streams_and_ready_handoff(self):
    counts = {'full': 0, 'excluded': 0, 'pedal_acc': 0, 'gateway': 0}
    for cp in configurations():
      with self.subTest(identity=cp.carFingerprint, word=hex(cp.safetyConfigs[0].safetyParam)):
        cp.alternativeExperience = 32
        controller = CarController(DBC[cp.carFingerprint], cp)
        cs = state(cp)
        # Mirror the reference producer independently of the new startup flag.
        primary_pedal = bool(cp.flags & GMFlags.PEDAL_LONG and controller.camera_pedal_profile is None)
        full = cp.openpilotLongitudinalControl and not cp.flags & GMFlags.CC_LONG and not primary_pedal
        self.assertEqual(bool(controller.startup_acc_keepalive), bool(full))
        if not full:
          counts['excluded'] += 1
          for tick in range(12):
            _, frames = controller.update(structs.CarControl.new_message().as_reader(), cs,
                                          1_000_000_000 + tick * 10_000_000, startup_keepalive=True)
            self.assertTrue(all(address == 0x180 for address, _, _ in frames))
          continue
        counts['full'] += 1
        counts['pedal_acc'] += controller.camera_pedal_profile is not None
        counts['gateway'] += cp.networkLocation == structs.CarParams.NetworkLocation.gateway
        safety = native.libsafety
        safety.init_tests()
        safety.set_alternative_experience(32)
        self.assertEqual(safety.set_safety_hooks(4, cp.safetyConfigs[0].safetyParam), 0)
        control = structs.CarControl.new_message()
        control.enabled = control.latActive = control.longActive = True
        control.actuators.accel, control.actuators.torque = 2., 1.
        observed = {address: [] for address in (0x2CB, 0x315, 0x370)}
        pedal_input = Mock()
        pedal_input.update.side_effect = AssertionError('Startup must not consult the pedal launch permission')
        if controller.camera_pedal_profile is not None:
          controller.camera_pedal_input = pedal_input
        for tick in range(140):
          now = 6_180_000_000 + tick * 10_000_000
          safety.set_timer(now // 1000)
          safety.set_aol_test_heartbeat(True)
          safety.aol_set_host_request(0)
          # After initialization, keep normal controls disabled. ACC-pedal has an
          # ordinary runtime permission reader, which must not be sampled at startup.
          startup = tick < 100
          if not startup and controller.camera_pedal_profile is not None:
            controller.camera_pedal_input = SimpleNamespace(update=lambda now: True)
          command = control.as_reader() if startup else structs.CarControl.new_message().as_reader()
          output, frames = controller.update(command, cs, now, startup_keepalive=startup)
          self.assertEqual(output.torque, 0)
          self.assertEqual(output.brake, 0)
          for address, data, bus in frames:
            self.assertTrue(safety.safety_tx_hook(native.make_CANPacket(address, bus, data)), (hex(address), bus, data.hex()))
            if address in observed:
              observed[address].append((now, bus, data))
            if address == 0x180:
              self.assertFalse(data[0] & 8)
              self.assertEqual((data[0] & 7) | data[1], 0)
            elif address == 0x2CB:
              self.assertEqual(bus, 0)
              self.assertFalse(data[0] & 1)
              self.assertFalse(data[1] & 0x20)
            elif address == 0x315:
              self.assertEqual(data[0] >> 4, 1)
              self.assertEqual(int.from_bytes(data[:2], 'big') & 0xFFF, 0)
            elif address == 0x370:
              self.assertEqual(bus, 0)
              self.assertFalse(data[2] & 0x80)
            elif address == 0x200:
              self.assertEqual(bus, 0)
              self.assertEqual(data[:4], bytes(4))
              self.assertFalse(data[4] & 0x80)
              self.assertEqual(data[5], gmcan.pedal_crc(data))
          self.assertFalse(safety.get_controls_allowed())
          self.assertEqual(safety.aol_get_request_mask(), 0)
        if controller.camera_pedal_profile is not None:
          pedal_input.update.assert_not_called()
        for address, samples in observed.items():
          self.assertTrue(samples, hex(address))
          self.assertLessEqual(samples[0][0], 6_220_000_000)
          self.assertLessEqual(max(b[0] - a[0] for a, b in zip(samples, samples[1:], strict=False)), 40_000_000)
        for address in (0x2CB, 0x315):
          counters = [(data[0] >> 6) & 3 if address == 0x2CB else data[4] & 3 for _, _, data in observed[address]]
          self.assertTrue(all(b == (a + 1) % 4 for a, b in zip(counters, counters[1:], strict=False)))
    self.assertGreater(counts['full'], 90)
    self.assertGreater(counts['excluded'], 100)
    self.assertGreater(counts['pedal_acc'], 30)
    self.assertGreater(counts['gateway'], 5)

  def test_invalid_final_config_does_not_enter_acc_startup(self):
    source = next(cp for cp in configurations() if cp.openpilotLongitudinalControl and not cp.flags)
    for field in ('passive', 'dashcamOnly', 'notCar'):
      cp = source.copy()
      setattr(cp, field, True)
      self.assertFalse(CarController(DBC[cp.carFingerprint], cp).startup_acc_keepalive)
