"""Source-derived G90 lead hysteresis and existing CAN encoder fields."""
import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.hyundai.g90_lead import G90LeadState, LeadObservation, eligible
from opendbc.car.hyundai.hyundaican import create_acc_commands
from opendbc.car.hyundai.tests.test_g90_longitudinal import params
from opendbc.car.hyundai.values import CAR, DBC


def observation(distance=26., relative=-.5, present=True, drive=1):
  return LeadObservation(drive, 100, 101, 102, present, distance, relative)


class TestG90Lead(unittest.TestCase):
  def test_visibility_gap_and_relative_state_counts_controller_updates(self):
    state = G90LeadState()
    for _ in range(49):
      lead = state.update(observation(), False)
    self.assertFalse(lead.lead_visible)
    self.assertEqual(lead.object_gap, 0)
    lead = state.update(observation(), False)
    self.assertTrue(lead.lead_visible)
    self.assertEqual((lead.object_gap, lead.object_rel_gap), (4, 2))
    for _ in range(49):
      lead = state.update(observation(0, 0, False), False)
    self.assertTrue(lead.lead_visible)
    self.assertFalse(state.update(observation(0, 0, False), False).lead_visible)
    state = G90LeadState()
    for frame in range(50):
      lead = state.update(observation(15 if frame % 2 else 26), False)
    self.assertEqual(lead.object_gap, 2)  # Original non-current gaps share one counter.
    self.assertEqual(state.update(observation(.05, -1), False).lead_distance, 20.)
    self.assertEqual(state.update(observation(999, -999), False).lead_rel_speed, -170.)
    self.assertEqual(state.update(observation(26, -.2), False).object_rel_gap, 1)

  def test_optional_payload_preserves_sibling_and_accel_aeb_bytes(self):
    for car, alpha in ((CAR.GENESIS_G90, True), (CAR.GENESIS_G90, False), (CAR.GENESIS_G80, True),
                       (CAR.HYUNDAI_KONA_EV, True), (CAR.HYUNDAI_SONATA, True), (CAR.KIA_STINGER_2022, True)):
      cp = params(car, alpha)
      hud = structs.CarControl.HUDControl(leadVisible=True, leadDistanceBars=2)
      state = G90LeadState()
      for _ in range(50):
        lead = state.update(observation(), False)
      def packed(payload, cp=cp, hud=hud):
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        return create_acc_commands(packer, True, .2, 1., 7, hud, 70., False, False, True, cp, lead_data=payload)
      baseline, changed = packed(None), packed(lead)
      if not eligible(cp):
        self.assertEqual(baseline, changed)
      else:
        for old, new in zip(baseline, changed, strict=True):
          if old[0] in (0x421, 0x38d):
            self.assertEqual(old, new)
        parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [('FCA11', 0), ('SCC12', 0)], 0)
        parser.update([(1_000_000_000, changed)])
        self.assertEqual(parser.vl['FCA11']['FCA_Status'], 1)
        non_fca = create_acc_commands(CANPacker(DBC[cp.carFingerprint][Bus.pt]), True, .2, 1., 7,
                                      hud, 70., False, False, False, cp, lead_data=lead)
        parser.update([(1_010_000_000, non_fca)])
        self.assertEqual(parser.vl['SCC12']['AEB_Status'], 1)


  def test_classic_scope_and_missing_source_never_invent_a_close_lead(self):
    from opendbc.car.hyundai.values import HyundaiFlags

    for car in (CAR.GENESIS_G90, CAR.HYUNDAI_KONA_EV, CAR.HYUNDAI_SONATA, CAR.KIA_STINGER_2022):
      cp = params(car)
      self.assertTrue(eligible(cp))
      hud = structs.CarControl.HUDControl(leadVisible=True, leadDistanceBars=2)
      packets = create_acc_commands(CANPacker(DBC[car][Bus.pt]), True, -.5, 3., 7,
                                    hud, 50., False, False, True, cp)
      parser = CANParser(DBC[car][Bus.pt], [('SCC11', 0), ('SCC12', 0), ('SCC14', 0)], 0)
      parser.update([(1_000_000_000, packets)])
      self.assertEqual(parser.vl['SCC11']['ACC_ObjDist'], 0)
      self.assertEqual(parser.vl['SCC11']['ObjValid'], 0)
      self.assertEqual(parser.vl['SCC14']['ObjGap'], 0)
      self.assertEqual(parser.vl['SCC14']['ObjDistStat'], 0)
      self.assertAlmostEqual(parser.vl['SCC12']['aReqValue'], -.5)
      cp.openpilotLongitudinalControl = False
      self.assertFalse(eligible(cp))
    cp = params(CAR.HYUNDAI_KONA_EV)
    for mutation in ('passive', 'dashcamOnly', 'notCar'):
      setattr(cp, mutation, True)
      self.assertFalse(eligible(cp))
      setattr(cp, mutation, False)
    cp.flags |= int(HyundaiFlags.CANFD)
    self.assertFalse(eligible(cp))
    cp.flags &= ~int(HyundaiFlags.CANFD)
    cp.carFingerprint = CAR.KIA_RAY_EV
    self.assertFalse(eligible(cp))

  def test_classic_lead_native_admission_preserves_acceleration_checks(self):
    from opendbc.car.hyundai.values import HyundaiFlags
    from opendbc.safety.tests.libsafety import libsafety_py
    from opendbc.safety.tests.libsafety.libsafety_py import make_CANPacket

    native = libsafety_py.libsafety
    for car in (CAR.GENESIS_G90, CAR.HYUNDAI_KONA_EV, CAR.HYUNDAI_SONATA, CAR.KIA_STINGER_2022):
      cp = params(car)
      config = cp.safetyConfigs[-1]
      packer = CANPacker(DBC[car][Bus.pt])
      hud = structs.CarControl.HUDControl(leadVisible=True, leadDistanceBars=2)
      for controls in (False, True):
        for accel in (-.5, 0., 1.):
          results = []
          for distance in (0., 15., 26., 100.):
            state = G90LeadState()
            for _ in range(50):
              lead = state.update(observation(distance, -.5, distance > 0), False)
            self.assertEqual(native.set_safety_hooks(config.safetyModel.raw, config.safetyParam), 0)
            native.init_tests()
            native.set_controls_allowed(controls)
            packets = create_acc_commands(packer, True, accel, 3., 7, hud, 50., False, False,
                                          bool(cp.flags & HyundaiFlags.USE_FCA), cp, lead_data=lead)
            results.append({address: bool(native.safety_tx_hook(make_CANPacket(address, bus, data)))
                            for address, data, bus in packets})
          self.assertTrue(all(result == results[0] for result in results))
          if not controls and accel != 0.:
            self.assertFalse(results[0][0x421])
