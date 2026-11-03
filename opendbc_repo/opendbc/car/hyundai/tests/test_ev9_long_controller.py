"""EV9 optional LONG actual parser/controller and reached-cadence boundaries."""
import unittest
from types import SimpleNamespace
from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.hyundai.ev9_longitudinal import candidate, qualified, EV9LongitudinalPolicy, LongCtrlState
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params
from opendbc.car.hyundai.values import CAR, DBC


def packets(packer, tick, *, fault=0, moving=True, gear=5, cruise=True, advance_counter=False, cruise_button=0,
            adaptive_main=False):
  values = {
    'ACCELERATOR': (100, {'GEAR': gear, 'ACCELERATOR_PEDAL': 0}),
    'TCS': (50, {'ACCEnable': 0, 'ACC_REQ': int(cruise), 'DriverBraking': 0}),
    'WHEEL_SPEEDS': (100, dict.fromkeys(('WHL_SpdFLVal','WHL_SpdFRVal','WHL_SpdRLVal','WHL_SpdRRVal'), 36. if moving else 0.)),
    'MDPS': (100, {'MDPS_ADAS_AciFltSig_Lv2': fault}),
    'STEERING_SENSORS': (100, {}),
    'DOORS_SEATBELTS': (10, {'DRIVER_SEATBELT': 1}),
    'BLINKERS': (10, {}),
    'CRUISE_BUTTONS': (50, {'CRUISE_BUTTONS': cruise_button, 'ADAPTIVE_CRUISE_MAIN_BTN': int(adaptive_main)}),
  }
  frames = [packer.make_can_msg(name, 1, {**fields, **(
              {"COUNTER": tick // (100 // hz)} if advance_counter and name not in ("DOORS_SEATBELTS", "BLINKERS") else {})})
            for name, (hz, fields) in values.items()
            if tick % (100 // hz) == 0]
  # Optional stock camera receive frames remain available on CAM2; no active
  # steering source is inserted on ACAN0 where relay detection checks ownership.
  frames.append(packer.make_can_msg('LKAS_ALT', 2, {}))
  if tick % 5 == 0:
    frames.append(packer.make_can_msg('CAM_0x362', 2, {}))
  assert all(address != 0x1a0 and not 0x3a5 <= address <= 0x3c4 for address, _, _ in frames)
  return frames


def update(ci, packer, tick, **kwargs):
  return ci.update([(tick * 10_000_000, packets(packer, tick, **kwargs))])

class TestEV9LongController(unittest.TestCase):
  def instance(self):
    cp = candidate(params(candidate=CAR.KIA_EV9), enabled=True, is_release=False)
    self.assertTrue(qualified(cp))
    ci = CarInterface(cp)
    # This fixture isolates controller calibration after a controlled owner
    # acknowledgment. Pending-owner/first-native-output tests use the real owner.
    ci.CC.pe_startup = SimpleNamespace(handed_off=True, can_emit=lambda controller: True)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    ci.update([])
    for tick in range(1, 13):
      update(ci, packer, tick)
    self.assertTrue(ci.CS.out.canValid)
    return ci, packer

  def test_original_calibration_20hz_and_stop_latch_50hz(self):
    policy = EV9LongitudinalPolicy()
    first = policy.update(0, 1.0, 0.0, 0.0, LongCtrlState.starting, True, False)
    self.assertGreater(first, 0)
    for frame in range(1, 5):
      self.assertEqual(policy.update(frame, 1.0, 0.0, 0.0, LongCtrlState.starting, True, False), first)
    self.assertGreater(policy.update(5, 1.0, 0.0, 0.0, LongCtrlState.starting, True, False), first)
    policy.update_stop(True, False, True, 0.47)
    for sample in range(1, 179):
      policy.update_stop(True, False, True, 0.47)
      self.assertEqual(policy.stop.cruise_standstill, sample >= 178)
    for sample in range(1, 8):
      policy.update_stop(True, False, False, 0.2)
      self.assertEqual(policy.stop.stop_request, sample <= 6)
      self.assertFalse(policy.stop.cruise_standstill)

  def test_actual_mdps_byte16_and_original_mask2(self):
    ci, packer = self.instance()
    for tick, fault in enumerate((0, 1, 2, 4), 13):
      update(ci, packer, tick, fault=fault)
      ci.update([((tick * 10_000_000) + 1, [packer.make_can_msg("MDPS", 1,
                {"MDPS_EstStrAnglVal": 12.3, "MDPS_PaStrAnglVal": -45.6, "MDPS_ADAS_AciFltSig_Lv2": fault})])])
      self.assertAlmostEqual(ci.CS.angle_steering_angle, 12.3)
      self.assertEqual(ci.CS.angle_steering_fault, bool(fault & 2))
      self.assertEqual(ci.CS.out.steerFaultTemporary, bool(fault & 2))

  def test_actual_controller_cb_only_and_adrv_cadence(self):
    ci, _ = self.instance()
    cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
    cc.actuators.steeringAngleDeg = 10.0
    cc.actuators.accel = 1.0
    cc.actuators.longControlState = LongCtrlState.pid
    cc.hudControl.setSpeed = 20.0
    for frame in range(101):
      _, packets = ci.apply(cc.as_reader(), 1_000_000_000 + frame * 10_000_000)
      ids = [p[0] for p in packets]
      self.assertNotIn(0x110, ids)
      self.assertNotIn(0x51, ids)
      self.assertEqual(ids.count(0xcb), 1)
      self.assertEqual(ids.count(0x100), 1)
      for address, period in ((0x160, 2), (0x1da, 100), (0x1ea, 5), (0x200, 5), (0x345, 20),
                              (0x1e0, 5), (0x38c, 20), (0x161, 5), (0x162, 5), (0x1ba, 5), (0x1e5, 5),
                              (0x1a0, 2), (0x362, 5)):
        self.assertEqual(ids.count(address), int(frame % period == 0), (frame, hex(address)))
      cb = next(p for p in packets if p[0] == 0xcb)
      self.assertEqual(cb[2], 1)
      self.assertEqual((cb[1][3] >> 4) & 0xf, 2)

  def test_nondrive_neutral_lfa_cb_and_emitted_acceleration_bound(self):
    ci, _ = self.instance()
    cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
    cc.actuators.longControlState = LongCtrlState.off
    cc.actuators.accel = 3.5
    ci.CS.out.gearShifter = structs.CarState.GearShifter.park
    ci.CS.angle_steering_angle = 10.0
    _, packets = ci.apply(cc.as_reader(), 1_000_000_000)
    ids = [p[0] for p in packets]
    self.assertEqual(ids[:2], [0x730, 0x12a])
    self.assertNotIn(0x110, ids)
    self.assertNotIn(0x362, ids)
    cb = next(p for p in packets if p[0] == 0xcb)
    self.assertEqual((cb[1][3] >> 4) & 0xf, 1)
    self.assertEqual(cb[1][6], 0)
    self.assertEqual(ci.CC.accel_last, 2.2)

  def test_exact_ev9_pid_ceiling_and_sibling_limits_unchanged(self):
    stock = params(candidate=CAR.KIA_EV9)
    long_cp = candidate(stock, enabled=True, is_release=False)
    for speed in (0., 10., 30.):
      self.assertEqual(CarInterface.get_pid_accel_limits(long_cp, speed, 20.), (-3.5, 2.2))
      self.assertEqual(CarInterface.get_pid_accel_limits(stock, speed, 20.), (-3.5, 2.0))
      for sibling in (CAR.HYUNDAI_IONIQ_5_PE, CAR.HYUNDAI_IONIQ_6):
        self.assertEqual(CarInterface.get_pid_accel_limits(params(candidate=sibling), speed, 20.), (-3.5, 2.0))
    changed = long_cp.as_reader().as_builder()
    changed.safetyConfigs[0].safetyParam = 0x5c91
    self.assertEqual(CarInterface.get_pid_accel_limits(changed, 0., 20.), (-3.5, 2.0))

  def test_stale_lateral_command_cannot_bypass_native_state_gates(self):
    ci, _ = self.instance()
    cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
    cc.actuators.steeringAngleDeg = 10.
    cc.actuators.longControlState = LongCtrlState.pid
    cc.actuators.accel = 0.
    for index, (field, value) in enumerate((('standstill', True), ('gasPressed', True), ('brakePressed', True),
                                           ('steerFaultTemporary', True), ('steerFaultPermanent', True),
                                           ('canTimeout', True), ('canValid', False),
                                           ('gearShifter', structs.CarState.GearShifter.park))):
      with self.subTest(field=field):
        previous = getattr(ci.CS.out, field)
        setattr(ci.CS.out, field, value)
        ci.CC.frame = 5 * index
        _, packets = ci.apply(cc.as_reader(), 1_000_000_000 + index * 50_000_000)
        ids = [packet[0] for packet in packets]
        if field in ('canTimeout', 'canValid'):
          self.assertNotIn(0xcb, ids)
          self.assertNotIn(0x12a, ids)
        else:
          cb = next(packet for packet in packets if packet[0] == 0xcb)
          self.assertEqual((cb[1][3] >> 4) & 0xf, 1)
          self.assertEqual(cb[1][6], 0)
        self.assertNotIn(0x362, ids)
        setattr(ci.CS.out, field, previous)
    cc.enabled = False
    ci.CC.frame = 50
    _, packets = ci.apply(cc.as_reader(), 2_000_000_000)
    cb = next(packet for packet in packets if packet[0] == 0xcb)
    self.assertEqual((cb[1][3] >> 4) & 0xf, 1)
    self.assertEqual(cb[1][6], 0)
    self.assertNotIn(0x362, [packet[0] for packet in packets])

  def test_advertised_stock_availability_still_requires_startup_choice(self):
    for alpha in (False, True):
      stock = params(candidate=CAR.KIA_EV9, alpha=alpha)
      self.assertTrue(stock.alphaLongitudinalAvailable)
      self.assertFalse(stock.openpilotLongitudinalControl)
      self.assertEqual(stock.safetyConfigs[0].safetyParam, 0x5c91)
      not_requested = candidate(stock, enabled=False, is_release=False)
      self.assertIs(not_requested, stock)
      selected = candidate(stock, enabled=True, is_release=False)
      self.assertTrue(qualified(selected))
      self.assertEqual(selected.safetyConfigs[0].safetyParam, 0x5c95)
      self.assertEqual(stock.safetyConfigs[0].safetyParam, 0x5c91)
      self.assertFalse(stock.openpilotLongitudinalControl)
      release_stock = params(candidate=CAR.KIA_EV9, alpha=alpha, release=True)
      self.assertFalse(release_stock.alphaLongitudinalAvailable)
      self.assertIs(candidate(release_stock, enabled=True, is_release=True), release_stock)

  def test_long_healthy_without_stock_scc_but_stock_still_requires_scc(self):
    for selected in (False, True):
      stock = params(candidate=CAR.KIA_EV9)
      cp = candidate(stock, enabled=selected, is_release=False)
      ci = CarInterface(cp)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      ci.update([])
      subscribed = ci.can_parsers[Bus.pt].addresses
      self.assertEqual(0x1a0 in subscribed, not selected)
      for tick in range(1, 101):
        frames = packets(packer, tick, adaptive_main=tick == 8)
        self.assertNotIn(0x1a0, [address for address, _, _ in frames])
        ci.update([(tick * 10_000_000, frames)])
      self.assertEqual(ci.CS.out.canValid, selected)
      if selected:
        self.assertFalse(ci.CS.out.canTimeout)
        self.assertTrue(ci.CS.out.cruiseState.available)
        self.assertTrue(ci.CS.out.cruiseState.enabled)


class TestEV9FreshMetadataQualification(unittest.TestCase):
  @staticmethod
  def original(cp):
    from opendbc.car.hyundai.ev9_longitudinal import LONG_PARAM, stock_copy
    from opendbc.car.hyundai.ccnc_ev_stock import qualified as stock_qualified
    if (cp.carFingerprint != CAR.KIA_EV9 or not cp.openpilotLongitudinalControl or cp.pcmCruise or
        cp.alternativeExperience not in (0, 32) or not cp.alphaLongitudinalAvailable or
        len(cp.safetyConfigs) != 1 or cp.safetyConfigs[0].safetyParam != LONG_PARAM):
      return False
    stock = stock_copy(cp)
    stock.alternativeExperience = 0
    return stock_qualified(stock)

  def base(self):
    return candidate(params(candidate=CAR.KIA_EV9), enabled=True, is_release=False)

  def check(self, cp):
    def result(fn):
      try:
        return ('value', fn(cp))
      except Exception as error:
        return ('error', type(error), str(error))
    self.assertEqual(result(qualified), result(self.original))

  def test_current_builder_fields_and_every_safety_word(self):
    cp = self.base()
    for experience in (0, 32):
      cp.alternativeExperience = experience
      for word in range(65536):
        cp.safetyConfigs[0].safetyParam = word
        self.assertEqual(qualified(cp), self.original(cp), (experience, word))

  def test_each_live_metadata_field_and_neighbor_profile(self):
    cp = self.base()
    changes = {
      'brand': ('', 'gm', 'hyundai'),
      'carFingerprint': ('', CAR.HYUNDAI_IONIQ_5_PE, CAR.HYUNDAI_IONIQ_6, CAR.KIA_EV9),
      'flags': (0, *(int(cp.flags) ^ (1 << bit) for bit in range(32))),
      'alternativeExperience': (0, 32, 64, 96, 128),
      'openpilotLongitudinalControl': (False, True), 'pcmCruise': (False, True),
      'alphaLongitudinalAvailable': (False, True), 'notCar': (False, True),
      'passive': (False, True), 'dashcamOnly': (False, True),
    }
    for field, values in changes.items():
      for value in values:
        changed = cp.copy()
        setattr(changed, field, value)
        with self.subTest(field=field, value=value):
          self.check(changed)
    for count in (0, 2):
      changed = cp.copy()
      changed.init('safetyConfigs', count)
      self.check(changed)
    for model in ('silent', 'noOutput', 'toyota', 'gm', 'hyundai', 'hyundaiCanfd'):
      changed = cp.copy()
      changed.safetyConfigs[0].safetyModel = model
      self.check(changed)

  def test_ignored_gear_metadata_and_live_mutations(self):
    from opendbc.car.hyundai.values import HyundaiFlags
    cp = self.base()
    for bits in (0, int(HyundaiFlags.CANFD_ALT_GEARS), int(HyundaiFlags.CANFD_ALT_GEARS_2),
                 int(HyundaiFlags.CANFD_ALT_GEARS | HyundaiFlags.CANFD_ALT_GEARS_2)):
      cp.flags = int(self.base().flags) | bits
      self.assertTrue(qualified(cp))
      self.check(cp)
      cp.safetyConfigs[0].safetyParam ^= 1
      self.assertFalse(qualified(cp))
      self.check(cp)
      cp.safetyConfigs[0].safetyParam ^= 1
      self.assertTrue(qualified(cp))

  def test_typed_builder_and_reader_avoid_copy_with_original_semantics(self):
    from unittest.mock import patch
    from opendbc.car.hyundai import ev9_longitudinal
    cp = self.base()
    with patch.object(ev9_longitudinal, 'stock_copy', side_effect=AssertionError('unexpected whole CP copy')):
      self.assertTrue(qualified(cp))
    with structs.CarParams.from_bytes(cp.to_bytes()) as reader:
      expected = self.original(reader)
      with patch.object(ev9_longitudinal, 'stock_copy', side_effect=AssertionError('unexpected whole CP copy')):
        self.assertEqual(qualified(reader), expected)

  def test_custom_owner_keeps_original_access_and_copy_behavior(self):
    cp = self.base()

    class Proxy:
      def __init__(self):
        self.calls = []

      def __getattr__(self, field):
        self.calls.append(field)
        return getattr(cp, field)

      def to_dict(self):
        self.calls.append('to_dict')
        return cp.to_dict()
    current, original = Proxy(), Proxy()
    self.assertEqual(qualified(current), self.original(original))
    self.assertEqual(current.calls, original.calls)
