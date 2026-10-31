"""GEN2 AOL native profile tests.

Exercises the GEN2 half of the safety AOL state machine, which shares the GEN1
protocol but takes its sources from GEN2 messages: CRZ_STATE for the main switch,
GEAR, interceptor liveness on 0x24B and camera-bus speed on 0x217.

The practical question is whether a lateral-only grant can exist and authorise
torque while openpilot longitudinal is inactive, without ever granting the
longitudinal axis.
"""
import unittest

from opendbc.car import structs
from opendbc.car.mazda.values import MazdaSafetyFlags
from opendbc.can.dbc import DBC
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py

GEN2 = int(MazdaSafetyFlags.GEN2)


class TestMazdaGen2Aol(unittest.TestCase):
  def tearDown(self):
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def configure(self, experience=32, param=GEN2):
    self.safety = libsafety_py.libsafety
    self.safety.init_tests()
    self.safety.set_alternative_experience(experience)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.mazda, param)
    self.time = 1_000_000
    self.safety.set_timer(self.time)
    self.safety.set_aol_test_heartbeat(True)
    self.packer = CANPackerSafety('mazda_2019')
    self.dbc = DBC('mazda_2019')

  def packet(self, name, fields, bus=0):
    self.assertFalse(fields.keys() - self.dbc.name_to_msg[name].sigs.keys())
    return self.packer.make_can_msg_safety(name, bus, fields)

  def feed(self, main=True, cruise=False, reverse=False, violation=False, speed=80, brake=False,
           buttons=0, omit=None, request=1):
    self.time += 10_000
    self.safety.set_timer(self.time)
    sources = (
      ('CRUZE_STATE', 0, {'CRZ_STATE': 2 if cruise else int(main)}),
      ('CRZ_BTNS', 0, {'RES': int(buttons == 1), 'SET_M': int(buttons == 2), 'CAN': int(buttons == 3)}),
      ('TI_FEEDBACK', 1, {'STEER_TORQUE_SENSOR': 0,
                          'CPU_0_VIOL': int(violation), 'CPU_0_STATE': 3, 'CPU_1_STATE': 3,
                          'CPU_2_STATE': 3, 'CPU_3_STATE': 3}),
      # Captures from the target car show GEAR originating on camera bus 2.
      ('GEAR', 2, {'GEAR': 12 if reverse else 4}),  # GEN2: 1..6 = D, 12 = R
      ('SPEED', 2, {'SPEED': speed}),
      ('ENGINE_DATA', 2, {'PEDAL_GAS': 0}),   # required GEN2 rx check (0x202, 100 Hz)
      ('BRAKE_PEDAL_SLOW', 0, {'BRAKE_PEDAL_PRESSED': int(brake)}),
    )
    for name, bus, fields in sources:
      if name != omit:
        self.safety.safety_rx_hook(self.packet(name, fields, bus))
    self.safety.safety_tick()
    self.safety.aol_set_host_request(request)

  def arm(self):
    for _ in range(10):
      self.feed(main=True)
    self.feed(main=False)
    self.feed(main=True)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_profile_is_enabled_for_gen2_and_tx_is_not_withdrawn(self):
    self.configure()
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    # The alt-exp path used to null the TX whitelist for every non-stock GEN1
    # configuration; a GEN2 profile must keep its TX.
    self.assertTrue(self.safety.safety_tx_hook(self.packet('EPS_LKAS', {'LKAS_REQUEST': 0}, bus=1)))

  def test_arm_grants_lateral_only_never_longitudinal(self):
    self.configure()
    self.arm()
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertTrue(self.safety.safety_tx_hook(self.packet('EPS_LKAS', {'LKAS_REQUEST': 45}, bus=1)))

  def test_optional_cancel_button_rearms_after_session_loss(self):
    self.configure()
    self.arm()
    self.safety.set_aol_test_heartbeat(False)
    self.feed(buttons=3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(True)
    # A button held through the lost session is not a new arming edge. CRZ_BTNS
    # is optional for GEN2, so this also proves its optional ingress is connected.
    self.feed(buttons=3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.feed(buttons=0)
    self.feed(buttons=3)
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_ordinary_gen2_engagement_survives_cruise_release(self):
    self.configure()
    for _ in range(12):
      self.feed(request=0)
    self.feed(cruise=True, request=0)
    self.assertTrue(self.safety.get_controls_allowed())
    # Releasing ordinary cruise must leave the lateral token created by the
    # accepted GEN2 engagement edge. Without mazda_aol_engagement this is zero.
    self.feed(brake=True)
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_ti_fault_revokes_token_without_an_intervening_tx_query(self):
    self.configure()
    self.arm()
    self.feed(violation=True)
    # Do not query permission or transmit during fault/recovery. Eager RX-side
    # revocation is required; a lazy TX-side check would let this token survive.
    for _ in range(12):
      self.feed()
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_standstill_longitudinal_does_not_inherit_lateral_speed_gate(self):
    self.configure()
    for _ in range(12):
      self.feed(speed=0, request=0)
    self.feed(cruise=True, speed=0, request=2)
    self.assertTrue(self.safety.get_controls_allowed())
    # Normal openpilot longitudinal needs HOLD/RESUME at standstill. Only the
    # lateral axis is speed-gated; a lateral-only request remains denied here.
    self.assertEqual(self.safety.aol_get_permission_mask(), 2)
    stock = {'ACCEL_CMD': 2000, 'ACC_ENABLED': 1, 'ACC_ENABLED_2': 1, 'HOLD': 0, 'RESUME': 0}
    self.safety.safety_rx_hook(self.packet('ACC', stock, bus=0))
    for button in ('HOLD', 'RESUME'):
      command = dict(stock, **{button: 1})
      self.assertTrue(self.safety.safety_tx_hook(self.packet('ACC', command, bus=2)), button)
    self.safety.set_controls_allowed(False)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.safety.safety_tx_hook(self.packet('ACC', dict(stock, HOLD=1), bus=2)))

  def test_only_the_exact_gen2_parameter_enables_the_profile(self):
    for param in (10, 18, 34, 66, 130, 258):
      with self.subTest(param=param):
        self.configure(param=param)
        for _ in range(10):
          self.feed()
        self.feed(main=False)
        self.feed(main=True)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.assertFalse(self.safety.safety_tx_hook(self.packet('EPS_LKAS', {'LKAS_REQUEST': 0}, bus=1)))

  def test_permission_requires_every_source(self):
    self.configure()
    self.arm()
    for field in ({'main': False}, {'reverse': True}, {'violation': True}, {'speed': 0}):
      self.configure()
      for _ in range(10):
        self.feed()
      self.feed(main=False)
      self.feed(main=True)
      self.feed(**field)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0, field)

  def test_missing_source_removes_permission(self):
    for omit in ('GEAR', 'SPEED', 'TI_FEEDBACK', 'CRUZE_STATE'):
      with self.subTest(source=omit):
        self.configure()
        self.arm()
        # Keep every other input and the host request fresh. At 310 ms the AOL
        # source timestamps must reject GEAR/CRUZE_STATE/SPEED before the shared
        # one-second RX-lag check can be the reason for denial.
        for _ in range(31):
          self.feed(omit=omit)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_without_the_alternative_experience_marker_nothing_is_granted(self):
    self.configure(experience=0)
    for _ in range(10):
      self.feed()
    self.feed(main=False)
    self.feed(main=True)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_stock_gen1_scope_is_unchanged(self):
    # Alt-exp 32 with a GEN1 safety parameter but a GEN2-only source set must not
    # grant anything: this profile is the GEN1 one, and its sources are absent.
    self.configure(param=int(MazdaSafetyFlags.GEN1) | GEN2)
    for _ in range(10):
      self.feed()
    self.feed(main=False)
    self.feed(main=True)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)


if __name__ == "__main__":
  unittest.main()
