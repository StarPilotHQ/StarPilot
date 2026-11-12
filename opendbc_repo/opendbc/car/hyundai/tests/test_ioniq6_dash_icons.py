import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_dash_icons import CanFDDashIcons
from opendbc.car.hyundai.tests.test_ioniq6_longitudinal import controller_fixture
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC


class TestCanFDDashIcons(unittest.TestCase):
  def test_lateral_and_cruise_activity_disengage_blink_and_reengage(self):
    icons = CanFDDashIcons()
    for frame, enabled, lateral, expected in ((0, False, False, 0), (1, False, True, 2),
                                             (2, False, False, 3), (100, False, False, 3),
                                             (101, False, False, 0), (102, True, False, 0),
                                             (103, False, True, 2), (104, False, False, 3)):
      self.assertEqual(icons.update(frame, enabled, lateral), expected)

  def test_actual_controller_preserves_cluster_cadence_and_axis_lifecycle(self):
    for alternate in (False, True):
      cp, cs, controller = controller_fixture(alternate, aol=True)
      parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFAHDA_CLUSTER', 0)], controller.CAN.ECAN)
      command = structs.CarControl()
      for frame in range(120):
        command.latActive = 5 <= frame < 10 or frame >= 115
        command.enabled = 105 <= frame < 110
        _, messages = controller.update(command.as_reader(), cs, 1_000_000_000 + frame * 10_000_000)
        cluster = [message for message in messages if message[0] == 0x1E0]
        self.assertEqual(len(cluster), int(frame % 5 == 0))
        if cluster:
          parser.update([(1_000_000_000 + frame * 10_000_000, cluster)])
          value = parser.vl['LFAHDA_CLUSTER']
          expected = 2 if command.latActive else 3 if 10 <= frame < 109 else 0
          self.assertEqual(value['LFA_ICON'], expected)
          self.assertEqual(value['HDA_ICON'], int(command.enabled))

  def test_actual_alpha_off_interface_does_not_install_icons_or_emit_cluster(self):
    for alternate in (False, True):
      with self.subTest(alternate=alternate):
        fingerprint = gen_empty_fingerprint()
        fingerprint[2].update({0x110: 32, 0x362: 32} if alternate else {0x50: 16, 0x2A4: 24})
        fingerprint[1].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                               0x1BA: 24, 0x1E5: 16, 0x36A: 16})
        fingerprint[0].update({0x3A5: 24, 0x100: 24})
        cp = CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fingerprint, [], False, False, False)
        self.assertFalse(cp.openpilotLongitudinalControl)
        self.assertTrue(cp.pcmCruise)
        ci = CarInterface(cp)
        self.assertIsNone(ci.CC.canfd_dash_icons)
        ci.update([])  # Register the actual lazy parser reads.
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        control = structs.CarControl()
        for tick in range(120):
          stamp = 1_000_000_000 + tick * 10_000_000
          inputs = [packer.make_can_msg(name, ci.CC.CAN.ECAN, values) for name, values in (
            ('ACCELERATOR', {'GEAR': 5}), ('TCS', {'ACCEnable': 0, 'ACC_REQ': 1}),
            ('WHEEL_SPEEDS', {}), ('MDPS', {'MDPS_StrTqSnsrVal': 0, 'MDPS_OutTqVal': 0}),
            ('CRUISE_BUTTONS', {'COUNTER': tick % 16}),
            ('DOORS_SEATBELTS', {'DRIVER_SEATBELT': 1}), ('STEERING_SENSORS', {}), ('BLINKERS', {}),
            ('SCC_CONTROL', {'MainMode_ACC': 1, 'ACCMode': 1}))]
          inputs.append(packer.make_can_msg('CAM_0x362' if alternate else 'CAM_0x2a4', ci.CC.CAN.CAM, {}))
          state = ci.update([(stamp, inputs)])
          self.assertTrue(state.canValid)
          control.enabled = 10 <= tick < 20
          control.latActive = 5 <= tick < 15
          _, messages = ci.apply(control.as_reader(), stamp)
          self.assertFalse(any(message[0] == 0x1E0 for message in messages), tick)


  def test_longitudinal_engagement_cannot_hold_or_relight_lateral_icon(self):
    for alternate in (False, True):
      for aol in (False, True):
        with self.subTest(alternate=alternate, aol=aol):
          cp, cs, controller = controller_fixture(alternate, aol=aol)
          original = cp.to_dict()
          parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFAHDA_CLUSTER', 0)], controller.CAN.ECAN)
          command = structs.CarControl(enabled=True, longActive=True)
          for frame in range(135):
            command.latActive = frame < 5 or frame >= 125
            _, messages = controller.update(command.as_reader(), cs, 1_000_000_000 + frame * 10_000_000)
            cluster = [message for message in messages if message[0] == 0x1E0]
            self.assertEqual(len(cluster), int(frame % 5 == 0))
            if cluster:
              parser.update([(1_000_000_000 + frame * 10_000_000, cluster)])
              value = parser.vl['LFAHDA_CLUSTER']
              expected = 2 if command.latActive else 3 if frame < 104 else 0
              self.assertEqual(value['LFA_ICON'], expected)
              self.assertEqual(value['HDA_ICON'], 1)
          self.assertEqual(cp.to_dict(), original)


  def test_cruise_transitions_cannot_invert_lateral_icon_at_standstill(self):
    for alternate in (False, True):
      cp, cs, controller = controller_fixture(alternate, aol=True)
      self.assertEqual(cs.out.vEgo, 0.0)
      parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFAHDA_CLUSTER', 0)], controller.CAN.ECAN)
      command = structs.CarControl(latActive=True)
      for frame in range(40):
        command.enabled = 10 <= frame < 30
        command.longActive = command.enabled
        _, messages = controller.update(command.as_reader(), cs, 1_000_000_000 + frame * 10_000_000)
        cluster = [message for message in messages if message[0] == 0x1E0]
        if cluster:
          parser.update([(1_000_000_000 + frame * 10_000_000, cluster)])
          self.assertEqual(parser.vl['LFAHDA_CLUSTER']['LFA_ICON'], 2)
          self.assertEqual(parser.vl['LFAHDA_CLUSTER']['HDA_ICON'], int(command.enabled))
