import os
import resource
import signal
import time
import unittest

from opendbc.car import structs
from opendbc.safety.tests.libsafety import libsafety_py


class TestFordAolAdmission(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety

  def tearDown(self):
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def test_no_frames_no_lateral_or_longitudinal_authority(self):
    for word in (0, 1, 2, 3, 8, 9, 10, 11, 12, 13):
      for ae in (0, 32, 33):
        with self.subTest(word=word, ae=ae):
          self.safety.init_tests()
          self.safety.set_alternative_experience(ae)
          self.safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, word)
          self.safety.set_timer(1_000_000)
          self.safety.set_aol_test_heartbeat(True)
          for mask in (0, 1, 2, 3):
            self.safety.aol_set_host_request(mask)
            self.assertEqual(self.safety.aol_get_permission_mask(), 0)


class TestFordAolDriverIntent(unittest.TestCase):
  BASE_PROFILES = (0, 1, 2, 3, 8, 9, 10, 11, 12, 13)
  PROFILES = BASE_PROFILES + tuple(word | 256 for word in BASE_PROFILES)

  def setUp(self):
    from opendbc.can import CANPacker
    self.safety = libsafety_py.libsafety
    self.packer = CANPacker('ford_lincoln_base_pt')

  def tearDown(self):
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def reset(self, word):
    self.word = word
    self.tick = 0
    self.safety.init_tests()
    self.safety.set_alternative_experience(32)
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, word), 0)
    self.safety.set_aol_test_heartbeat(True)

  def rx(self, name, values):
    self.assertLessEqual(set(values), set(self.packer.dbc.name_to_msg[name].sigs))
    address, data, bus = self.packer.make_can_msg(name, 0, values)
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data)))

  def pump(self, count, *, main=3, tja=False, gear=3, eps=0, mask=0, heartbeat=True, speed=10., curvature=0.):
    base = self.word & ~257
    gear_name, gear_signal = ('TransGearData', 'GearLvrPos_D_Actl') if base == 8 else (
      ('Gear_Shift_by_Wire_FD1', 'TrnRng_D_RqGsm') if base == 10 else ('PowertrainData_10', 'TrnRng_D_Rq'))
    if self.word & 256:
      gear_name, gear_signal = 'BCM_Lamp_Stat_FD1', 'RvrseLghtOn_B_Stat'
      gear = int(gear == 1)
    for _ in range(count):
      self.tick += 1
      self.safety.set_timer(1_000_000 + self.tick * 10_000)
      self.safety.set_aol_test_heartbeat(heartbeat)
      values = (
        ('BrakeSysFeatures', 50, {'Veh_V_ActlBrk': speed * 3.6, 'VehVActlBrk_D_Qf': 3, 'VehVActlBrk_No_Cnt': (self.tick // 2) % 16}),
        ('EngVehicleSpThrottle2', 50, {'Veh_V_ActlEng': speed * 3.6, 'VehVActlEng_D_Qf': 3}),
        ('Yaw_Data_FD1', 100, {'VehYaw_W_Actl': curvature * speed, 'VehYawWActl_D_Qf': 3, 'VehRollYaw_No_Cnt': self.tick % 256}),
        ('EngBrakeData', 10, {'BpedDrvAppl_D_Actl': 1, 'CcStat_D_Actl': main}),
        ('EngVehicleSpThrottle', 100, {'ApedPos_Pc_ActlArb': 0}),
        ('DesiredTorqBrk', 50, {'VehStop_D_Stat': 0}),
        ('Steering_Data_FD1', 10, {'TjaButtnOnOffPress': int(tja)}),
        ('EPAS_INFO', 50, {'SteeringColumnTorque': 0, 'EPAS_Failure': eps}),
        (gear_name, 10, {gear_signal: gear}),
      )
      for name, hz, fields in values:
        if self.tick * hz // 100 != (self.tick - 1) * hz // 100:
          self.rx(name, fields)
      if base in (2, 10, 12) and self.tick * 30 // 100 != (self.tick - 1) * 30 // 100:
        self.rx('Lane_Assist_Data3_FD1', {'LatCtlSte_D_Stat': 1, 'LaActAvail_D_Actl': 3, 'LaActDeny_B_Actl': 0})
      self.safety.aol_set_host_request(mask)
      self.safety.aol_get_permission_mask()

  def arm(self):
    self.pump(40, main=0)
    self.pump(10)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertFalse(self.safety.get_controls_allowed())

  def rearm(self):
    self.pump(20)
    self.pump(10, tja=True)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_permanent_eps_then_clean_in_same_batch_revokes(self):
    for word in self.PROFILES:
      with self.subTest(word=word):
        self.reset(word)
        self.arm()
        self.rx('EPAS_INFO', {'SteeringColumnTorque': 0, 'EPAS_Failure': 2})
        self.rx('EPAS_INFO', {'SteeringColumnTorque': 0, 'EPAS_Failure': 0})
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.pump(30, mask=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.rearm()

  def test_reverse_temporary_eps_retain_but_heartbeat_loss_rearms(self):
    for word in self.PROFILES:
      with self.subTest(word=word):
        self.reset(word)
        self.arm()
        self.pump(30, gear=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.pump(20, mask=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.pump(30, eps=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.pump(20, mask=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.pump(30, gear=1, heartbeat=False)
        self.pump(20, mask=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.rearm()

  def test_profile_switches_require_fresh_complete_selected_rx_inventory(self):
    from opendbc.can import CANPacker

    packer = CANPacker("ford_lincoln_base_pt")
    self.safety.init_tests()
    self.safety.set_alternative_experience(32)
    profiles = ((0, "PowertrainData_10", "TrnRng_D_Rq", False),
                (8, "TransGearData", "GearLvrPos_D_Actl", False),
                (10, "Gear_Shift_by_Wire_FD1", "TrnRng_D_RqGsm", True),
                (12, "PowertrainData_10", "TrnRng_D_Rq", True),
                (2, "PowertrainData_10", "TrnRng_D_Rq", True),
                (8, "TransGearData", "GearLvrPos_D_Actl", False),
                (0, "PowertrainData_10", "TrnRng_D_Rq", False))
    for epoch, (word, gear_name, gear_signal, needs_lka) in enumerate(profiles):
      with self.subTest(word=word, epoch=epoch):
        self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, word), 0)
        self.assertFalse(self.safety.safety_config_valid())
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        for count in range(6):
          self.safety.set_timer(1_000_000 + epoch * 100_000 + count * 10_000)
          sources = (
            ("BrakeSysFeatures", {"Veh_V_ActlBrk": 36, "VehVActlBrk_D_Qf": 3, "VehVActlBrk_No_Cnt": count}),
            ("EngVehicleSpThrottle2", {"Veh_V_ActlEng": 36, "VehVActlEng_D_Qf": 3}),
            ("Yaw_Data_FD1", {"VehYaw_W_Actl": 0, "VehYawWActl_D_Qf": 3, "VehRollYaw_No_Cnt": count}),
            ("EngBrakeData", {"BpedDrvAppl_D_Actl": 1, "CcStat_D_Actl": 3}),
            ("EngVehicleSpThrottle", {"ApedPos_Pc_ActlArb": 0}),
            ("DesiredTorqBrk", {"VehStop_D_Stat": 0}),
            ("Steering_Data_FD1", {"TjaButtnOnOffPress": 0}),
            ("EPAS_INFO", {"SteeringColumnTorque": 0, "EPAS_Failure": 0}),
            (gear_name, {gear_signal: 3}),
          )
          if needs_lka:
            sources += (("Lane_Assist_Data3_FD1", {"LaActAvail_D_Actl": 3, "LaActDeny_B_Actl": 0, "LatCtlSte_D_Stat": 1}),)
          for name, values in sources:
            frame = packer.make_can_msg(name, 0, values)
            self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])), name)
        self.assertTrue(self.safety.safety_config_valid())
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_permanent_eps_is_denied_before_any_clean_replacement(self):
    for word in self.PROFILES:
      with self.subTest(word=word):
        self.reset(word)
        self.arm()
        self.rx('EPAS_INFO', {'SteeringColumnTorque': 0, 'EPAS_Failure': 2})
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.pump(30, mask=1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.rearm()

  def test_main_cancel_states_and_physical_cancel_buttons_withdraw(self):
    for state in (1, 2):
      self.reset(0)
      self.arm()
      self.pump(10, main=state, mask=1)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    for bit in (8, 24):
      self.reset(0)
      self.arm()
      data = (1 << bit).to_bytes(8, 'little')
      self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x83, 0, data)))
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.pump(20, mask=1)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.rearm()

  def test_accepted_lateral_owns_only_replacement_until_lease_or_rejection(self):
    from opendbc.car.ford.fordcan import CanBus, create_lat_ctl_msg
    from opendbc.car import gen_empty_fingerprint
    self.reset(0)
    self.arm()
    bus = CanBus(fingerprint=gen_empty_fingerprint())
    frame = create_lat_ctl_msg(self.packer, bus, True, 0., 0., 0., 0.)
    command = libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x3D3), 0)
    self.assertTrue(self.safety.safety_tx_hook(command))
    for address in (0x3D3, 0x3D8, 0x18A):
      self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x321), 0)
    self.pump(31, mask=1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    for address in (0x3D3, 0x3D8, 0x18A):
      self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)

  def host_actuator(self, *, valid=True):
    """This profile's steering actuator, inactive, as the host sends it every frame. Invalid adds a path offset,
    or on the Transit requests steering without lateral permission."""
    from opendbc.car.ford.fordcan import CanBus, create_lat_ctl_msg, create_lat_ctl2_msg, create_lka_msg
    from opendbc.car import gen_empty_fingerprint
    bus = CanBus(fingerprint=gen_empty_fingerprint())
    base = self.word & ~257
    offset = 0. if valid else 1.
    if base == 12:
      frame = create_lka_msg(self.packer, bus, transit=True) if valid else \
              create_lka_msg(self.packer, bus, True, 1., 2, .0002, transit=True)
    elif base in (2, 10):
      frame = create_lat_ctl2_msg(self.packer, bus, 0, offset, 0., 0., 0., 0)
    else:
      frame = create_lat_ctl_msg(self.packer, bus, False, offset, 0., 0., 0.)
    return frame[0], libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])

  def test_one_actuator_sender_while_the_host_sends(self):
    # The camera's actuator is forwarded under AOL so the stock system keeps its message when the host
    # isn't sending; while the host sends, two senders confuse the PSCM, and a camera frame standing in
    # for a rejected host frame mid-steer makes it ramp out and fault
    for word in self.PROFILES:
      with self.subTest(word=word):
        self.reset(word)
        self.pump(10)
        address, inactive = self.host_actuator()
        self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)
        for _ in range(20):
          self.pump(5)
          self.assertTrue(self.safety.safety_tx_hook(inactive))
          self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)
          self.assertEqual(self.safety.safety_fwd_hook(0, address), 2)
          self.assertEqual(self.safety.safety_fwd_hook(2, 0x321), 0)
          # replacing the camera's frame grants nothing
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.assertFalse(self.safety.get_controls_allowed())
        self.pump(30)
        self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)
        self.pump(1)
        self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)
        self.assertTrue(self.safety.safety_tx_hook(inactive))
        self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)
        # a rejected host frame is a missed frame, not the camera's turn
        _, rejected = self.host_actuator(valid=False)
        for _ in range(10):
          self.pump(5)
          self.assertFalse(self.safety.safety_tx_hook(rejected))
          self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)
        self.pump(31)
        self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)
        self.assertFalse(self.safety.safety_tx_hook(rejected))
        self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)
        self.safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, word)
        self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)

  def test_without_aol_the_actuator_stays_statically_blocked(self):
    for word in self.BASE_PROFILES:
      with self.subTest(word=word):
        self.reset(word)
        address, _ = self.host_actuator()
        self.safety.set_alternative_experience(0)
        self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, word), 0)
        self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)

  def test_native_bounded_catalog_copy_denies_invalid_and_restores_registered_table(self):
    for length, null_catalog in ((0, False), (1, False), (7, False), (8, False), (1, True)):
      with self.subTest(length=length, null_catalog=null_catalog):
        self.reset(0)
        self.assertTrue(self.bounded_catalog_contract(length, null_catalog))
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.arm()

  def bounded_catalog_contract(self, length, null_catalog):
    # Coverage uses the direct callback. Mutants use the identical native inputs
    # in a child so a broken pointer/bounds guard becomes a unittest assertion.
    if not hasattr(self.safety, 'mutation_set_active_mutant'):
      return self.safety.safety_test_ford_aol_tx_bounds(length, null_catalog)
    reader, writer = os.pipe()
    child = os.fork()
    if child == 0:
      os.close(reader)
      resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
      try:
        result = self.safety.safety_test_ford_aol_tx_bounds(length, null_catalog)
        os.write(writer, bytes([int(bool(result))]))
        os._exit(0)
      except BaseException:
        os._exit(2)
    os.close(writer)
    reaped = False
    try:
      deadline = time.monotonic() + 5.
      while time.monotonic() < deadline:
        finished, status = os.waitpid(child, os.WNOHANG)
        if finished:
          reaped = True
          break
        time.sleep(.001)
      if not reaped:
        self.fail('native catalog contract exceeded its five-second deadline')
      self.assertTrue(os.WIFEXITED(status), f'native catalog contract failed with wait status {status}')
      self.assertEqual(os.WEXITSTATUS(status), 0)
      self.assertEqual(os.read(reader, 1), bytes([1]))
      return True
    finally:
      if not reaped:
        os.kill(child, signal.SIGKILL)
        os.waitpid(child, 0)
      os.close(reader)
