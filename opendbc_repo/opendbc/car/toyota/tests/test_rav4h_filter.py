"""Production factory/parser contracts for the bounded RAV4H smartDSU port."""
import unittest
from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.toyota.fingerprints import FW_VERSIONS
from opendbc.car.toyota.interface import CarInterface, apply_toyota_auto_hold
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.rav4h_filter import configured, enabled, prepare_stock, LONG_WORD, STOCK_WORD
from opendbc.car.toyota.values import CAR, DBC, ToyotaFlags
from openpilot.starpilot.car.toyota.aol import qualified


def firmware(car=CAR.TOYOTA_RAV4H):
  return [structs.CarParams.CarFw(ecu=ecu, address=address, subAddress=subaddress or 0,
                                 fwVersion=versions[0], brand="toyota", bus=0)
          for (ecu, address, subaddress), versions in FW_VERSIONS[car].items()]


def factory(fp=None, fw=None, alpha=False, car=CAR.TOYOTA_RAV4H):
  return CarInterface.get_params(car, {0: {0x2FF: 8}, 1: {}, 2: {}} if fp is None else fp,
                                 firmware(car) if fw is None else fw, alpha, False, False)


class TestRav4hFilter(unittest.TestCase):
  def test_exact_factory_fw_identity_and_current_tune(self):
    from opendbc.car.fw_versions import match_fw_to_car
    exact, candidates = match_fw_to_car(firmware(), "", log=False)
    self.assertTrue(exact)
    self.assertIn(CAR.TOYOTA_RAV4H, candidates)
    self.assertNotIn(CAR.TOYOTA_PRIUS, candidates)
    for alpha in (False, True):
      cp = factory(alpha=alpha)
      self.assertTrue(enabled(cp))
      self.assertEqual(cp.safetyConfigs[0].safetyParam, LONG_WORD)
      self.assertEqual(cp.flags, int(ToyotaFlags.HYBRID | ToyotaFlags.LONG_FILTER))
      self.assertEqual(cp.carFingerprint, CAR.TOYOTA_RAV4H)
      self.assertTrue(cp.autoResumeSng)
      self.assertEqual(cp.minEnableSpeed, -1)
      self.assertAlmostEqual(cp.longitudinalActuatorDelay, .05)
      self.assertEqual(CarState(cp).eps_torque_scale, .73)
      self.assertTrue(qualified(cp))
      from openpilot.selfdrive.controls.lib.longcontrol import LongControl
      self.assertAlmostEqual(LongControl(cp).stopping_decel_rate, .3)
      self.assertEqual(DBC[cp.carFingerprint][Bus.pt], "toyota_tnga_k_pt_generated")

  def test_highlander_sdsu_low_speed_engagement_and_stock_fallback(self):
    from opendbc.car.toyota import legacy_advert_filter
    from opendbc.car.toyota.values import MIN_ACC_SPEED

    car = CAR.TOYOTA_HIGHLANDER
    stock = factory(fp={0: {}, 1: {}, 2: {}}, car=car)
    self.assertFalse(stock.openpilotLongitudinalControl)
    self.assertAlmostEqual(stock.minEnableSpeed, MIN_ACC_SPEED, places=6)
    for size, restore_stock in ((4, legacy_advert_filter.prepare_stock), (8, prepare_stock)):
      fp = {0: {0x2FF: size}, 1: {}, 2: {}}
      for alpha in (False, True):
        with self.subTest(size=size, alpha=alpha):
          cp = factory(fp=fp, car=car, alpha=alpha)
          self.assertTrue(cp.openpilotLongitudinalControl)
          self.assertFalse(cp.alphaLongitudinalAvailable)
          self.assertEqual(cp.minEnableSpeed, -1)
          self.assertTrue(restore_stock(cp))
          self.assertFalse(cp.openpilotLongitudinalControl)
          self.assertEqual(cp.minEnableSpeed, stock.minEnableSpeed)
      unknown = factory(fp=fp, car=car, fw=[])
      self.assertFalse(unknown.openpilotLongitudinalControl)
      self.assertEqual(unknown.minEnableSpeed, stock.minEnableSpeed)

  def test_missing_or_mismatched_hardware_never_grants_long_or_aol(self):
    fps = ({0: {}, 1: {}, 2: {}}, {0: {0x2FF: 7}, 1: {}, 2: {}},
           {0: {}, 1: {0x2FF: 8}, 2: {}}, {0: {0x2FF: 8}, 1: {0x2FF: 8}, 2: {}},
           {0: {0x2FF: 8, 0x201: 5}, 1: {}, 2: {}}, {0: {0x2FF: 8, 0x2AA: 8}, 1: {}, 2: {}},
           {0: {0x2FF: 8}, 1: {}, 2: {0x343: 7}})
    for fp in fps:
      cp = factory(fp=fp)
      self.assertFalse(enabled(cp), fp)
      self.assertFalse(cp.openpilotLongitudinalControl, fp)
      from opendbc.car.toyota import stock_aol
      self.assertEqual(qualified(cp), stock_aol.configured(cp), fp)
    for ecu in (structs.CarParams.Ecu.eps, structs.CarParams.Ecu.fwdCamera, structs.CarParams.Ecu.fwdRadar):
      missing = [fw for fw in firmware() if fw.ecu != ecu]
      self.assertFalse(enabled(factory(fw=missing)))
      for field, value in (("fwVersion", b"unknown"), ("address", 0x999), ("subAddress", 99),
                           ("bus", 1), ("brand", "ford"), ("brand", ""), ("logging", True)):
        fw = firmware()
        selected = next(item for item in fw if item.ecu == ecu)
        setattr(selected, field, value)
        cp = factory(fw=fw)
        self.assertFalse(enabled(cp), (ecu, field))
        self.assertTrue(cp.dashcamOnly)

  def test_stock_owner_hold_and_wrong_profile_denial(self):
    cp = factory()
    self.assertTrue(apply_toyota_auto_hold(cp, True))
    self.assertEqual(cp.alternativeExperience, 128)
    cp.alternativeExperience = 160
    self.assertTrue(qualified(cp, marked_only=True))
    self.assertTrue(prepare_stock(cp))
    self.assertFalse(cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
    self.assertEqual(cp.alternativeExperience, 32)
    self.assertEqual(cp.safetyConfigs[0].safetyParam, STOCK_WORD)
    self.assertTrue(configured(cp))
    self.assertTrue(qualified(cp, marked_only=True))
    self.assertFalse(enabled(cp))
    self.assertFalse(apply_toyota_auto_hold(cp, True))
    for field, value in (("carFingerprint", CAR.TOYOTA_RAV4), ("flags", int(ToyotaFlags.HYBRID)),
                         ("dashcamOnly", True), ("passive", True), ("notCar", True),
                         ("openpilotLongitudinalControl", True), ("alternativeExperience", 288)):
      bad = cp.as_reader().as_builder()
      setattr(bad, field, value)
      self.assertFalse(qualified(bad, marked_only=True), field)

  def test_actual_sdsu_parser_gap_edges_and_faults(self):
    cp = factory()
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    for tick, pressed in enumerate((0, 1, 1, 0)):
      now = 1_000_000_000 + tick * 20_000_000
      parsers[Bus.pt].update([(now, [packer.make_can_msg("SDSU", 0, {"FD_BUTTON": pressed})])])
      result = state.update(parsers)
      events = [event for event in result.buttonEvents if event.type == structs.CarState.ButtonEvent.Type.gapAdjustCruise]
      self.assertEqual([event.pressed for event in events], ([], [True], [], [False])[tick])
    self.assertEqual(parsers[Bus.pt].vl["SDSU"]["FD_BUTTON"], 0)
    # Complete production warmup and expiry are asserted in the paired Card/native join packet.

    for tick, value in enumerate((0, 1, 2, 6, 7, 0), start=10):
      parsers[Bus.pt].update([(1_000_000_000 + tick * 20_000_000, [packer.make_can_msg("SDSU", 0, {"STATE": value})])])
      result = state.update(parsers)
      self.assertEqual(result.accFaulted, value not in (0, 7))
      self.assertEqual(result.stockAeb, value == 7)

  def test_sdsu_expiry_withdraws_before_shared_can_valid_debounce(self):
    cp = factory()
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    parser = parsers[Bus.pt]
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    start = 1_000_000_000
    parser.update([(start, [packer.make_can_msg("SDSU", 0, {"STATE": 0})])])
    for age in (199_999_999, 200_000_000, 200_000_001):
      parser.update([(start + age, [])])
      self.assertEqual(state.update(parsers).accFaulted, age > 200_000_000)
    parser.update([(start + 220_000_000, [packer.make_can_msg("SDSU", 0, {"STATE": 0})])])
    self.assertFalse(state.update(parsers).accFaulted)

  def test_short_sdsu_does_not_refresh_source_or_button_edges(self):
    cp = factory()
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    parser = parsers[Bus.pt]
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    start = 1_000_000_000
    valid = packer.make_can_msg("SDSU", 0, {"STATE": 0})
    parser.update([(start, [valid])])
    self.assertFalse(state.update(parsers).accFaulted)
    malformed = packer.make_can_msg("SDSU", 0, {"STATE": 0, "FD_BUTTON": 1})
    for length in (0, 2, 7, 9):
      bad = (malformed[0], (malformed[1] + bytes(8))[:length], malformed[2])
      parser.update([(start + 210_000_000, [bad])])
      result = state.update(parsers)
      self.assertTrue(result.accFaulted, length)
      self.assertEqual(parser.ts_nanos["SDSU"]["STATE"], start)
      self.assertFalse(any(e.type == structs.CarState.ButtonEvent.Type.gapAdjustCruise for e in result.buttonEvents))
    parser.update([(start + 230_000_000, [valid])])
    self.assertFalse(state.update(parsers).accFaulted)
