import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from opendbc.car import gen_empty_fingerprint
from opendbc.car.can_definitions import CanData
from opendbc.car.gm.interface import CarInterface as GMInterface
from opendbc.car.gm.values import CAR as GMCar, GMFlags
from openpilot.cereal import messaging
from openpilot.selfdrive.car.card import Car, EventName
from openpilot.selfdrive.pandad import can_capnp_to_list
from openpilot.starpilot.vehicle_startup import VehicleStartupOwner


class CardInitLifecycleTest(unittest.TestCase):
  def assert_inactive_gm_stream(self, sent, *, alpha, brake_bus, inactive_regen):
    expected = {(0x180, 0)}
    if alpha:
      expected |= {(0x184, 2), (0x2CB, 0), (0x315, brake_bus), (0x370, 0)}
    self.assertEqual({(address, bus) for address, _, bus in sent}, expected)
    for address, data, _bus in sent:
      if address == 0x180:
        self.assertEqual(data[0] & 15, 0)
        self.assertEqual(data[1], 0)
      elif address == 0x184:
        self.assertEqual(data, bytes.fromhex('0000200000200000'))
      elif address == 0x2CB:
        self.assertFalse(data[0] & 1)  # Gas/regen command inactive.
        self.assertFalse(data[1] & 0x20)  # No full-stop request.
        # DBC: 19-bit Motorola command, scale .125 and offset -22534.
        raw = (inactive_regen + 22534) * 8
        self.assertEqual(int.from_bytes(data[1:4], 'big') & 0x7FFFF, raw)
        counter = data[0] >> 6
        command = bytes((counter << 6, 0x40 | (raw >> 16), (raw >> 8) & 0xFF, raw & 0xFF))
        # Inactive checksum bit, complemented bytes 1/2, then byte 3 + counter complement.
        checksum = bytes((1, 0xFF - command[1], 0xFF - command[2], (-command[3] - counter) & 0xFF))
        self.assertEqual(data, command + checksum)
      elif address == 0x315:
        self.assertEqual(data[0] >> 4, 1)  # Released friction-brake mode.
        self.assertEqual(int.from_bytes(data[:2], 'big') & 0xFFF, 0)
      elif address == 0x370:
        self.assertEqual(data, bytes.fromhex('010200000100'))  # Inactive ACC, no speed or warning.

  def defer_radar_fixture(self, card):
    eager = card.state_update
    batch = []

    def receive(*, defer_radar=False):
      self.assertTrue(defer_radar)
      card._deferred_radar_can = batch
      return eager()

    def radar_update(value):
      self.assertIs(value, batch)
      return None

    card.state_update = Mock(side_effect=receive)
    card.RI = SimpleNamespace(update=Mock(side_effect=radar_update))
    self.addCleanup(lambda: self.assertEqual(card.RI.update.call_count, card.state_update.call_count))

  def test_bolt_pedal_waits_for_control_then_initializes_once_before_output(self):
    fingerprint = gen_empty_fingerprint()
    fingerprint[0][0x201] = 6
    cp = GMInterface.get_params(GMCar.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
                                fingerprint, [], False, False, False)
    self.assertTrue(cp.flags & GMFlags.PEDAL_LONG.value)

    order = []
    card = Car.__new__(Car)
    card.CP = cp
    card.vehicle_startup = VehicleStartupOwner()
    card.ci_initialized = False
    card.initialized_prev = False
    card.aol_replay = False
    card.ioniq6_long_prearmed = False
    card.ioniq6_long_selected = False
    card.can_callbacks = (lambda wait_for_one=False: [], lambda frames: None)

    def apply(*_):
      order.append('apply')
      return object(), [CanData(0x200, b'\x00' * 8, 0)]

    fake_ci = SimpleNamespace(
      CS=GMInterface(cp).CS,
      CC=SimpleNamespace(),
      init=Mock(side_effect=lambda *_: order.append('init')),
      apply=Mock(side_effect=apply),
    )
    self.enterContext(patch.object(card, 'CI', fake_ci, create=True))
    self.enterContext(patch.object(card, 'params', SimpleNamespace(put_bool=lambda key, value: order.append((key, value))), create=True))
    sent = []

    def publish(service, packet):
      assert service == 'sendcan'
      sent.append(packet)
      order.append(('sendcan', tuple(can_capnp_to_list([packet], msgtype='sendcan')[0][1]),
                    bool(messaging.log_from_bytes(packet).valid)))

    self.enterContext(patch.object(card, 'pm', SimpleNamespace(send=publish), create=True))
    card.state_publish = Mock(side_effect=lambda *_: order.append('carState'))
    cs = SimpleNamespace(canValid=True, canTimeout=False)
    card.state_update = Mock(return_value=(cs, None))
    self.defer_radar_fixture(card)
    control = object()

    class SubMaster:
      valid = {'carControl': False}
      alive = {'carControl': False}
      seen = {'onroadEvents': False, 'carControl': True}
      logMonoTime = {'carControl': 0}
      recv_time = {'carControl': 0.0}
      events = []

      def __getitem__(self, key):
        return self.events if key == 'onroadEvents' else control

      def all_alive(self, services):
        return all(self.alive[name] for name in services)

    sm = SubMaster()
    self.enterContext(patch.object(card, 'sm', sm, create=True))
    for _ in range(30):
      card.step()
    self.assertNotIn('init', order)
    sm.seen['onroadEvents'] = True
    sm.events = [SimpleNamespace(name=EventName.selfdriveInitializing)]
    card.step()
    self.assertNotIn('init', order)

    sm.events = []
    card.step()  # Controls still have no live producer.
    self.assertNotIn('init', order)
    sm.valid['carControl'] = True
    sm.alive['carControl'] = True
    import time
    sm.logMonoTime['carControl'] = time.monotonic_ns()
    sm.recv_time['carControl'] = time.monotonic()
    cs.canValid = False
    card.step()  # A live controller cannot initialize against invalid CAN.
    self.assertNotIn('init', order)

    cs.canValid = True
    card.step()
    self.assertEqual(order[-5:-1], ['carState', 'init', ('ControlsReady', True), 'apply'])
    self.assertEqual(order[-1][0], 'sendcan')
    self.assertEqual([(frame[0], frame[2]) for frame in order[-1][1]], [(0x200, 0)])
    self.assertTrue(order[-1][2])
    self.assertTrue(all(not can_capnp_to_list([packet], msgtype='sendcan')[0][1] for packet in sent[:-1]))
    self.assertTrue(all(not messaging.log_from_bytes(packet).valid for packet in sent[:-1]))
    card.step()
    fake_ci.init.assert_called_once_with(cp, *card.can_callbacks)
    self.assertEqual(order.count(('ControlsReady', True)), 1)

    applied = fake_ci.apply.call_count
    sent_before = len(sent)
    sm.valid['carControl'] = False
    card.step()  # A live but invalid native command cannot renew CAN output.
    self.assertEqual(fake_ci.apply.call_count, applied)
    self.assertEqual(len(sent), sent_before)
    sm.valid['carControl'] = True
    card.step()
    self.assertEqual(fake_ci.apply.call_count, applied + 1)
    self.assertEqual(len(sent), sent_before + 1)
    self.assertTrue(messaging.log_from_bytes(sent[-1]).valid)
    fake_ci.init.assert_called_once_with(cp, *card.can_callbacks)

  def test_volt_sdgm_inactive_startup_continues_until_initialized(self):
    from opendbc.car import Bus, structs
    from opendbc.can import CANPacker
    from opendbc.car.gm.values import DBC
    from opendbc.car.gm.tests.test_volt_camera_control import feed_camera
    fingerprint = gen_empty_fingerprint()
    fingerprint[2][0x180] = 4
    fingerprint[0][0x2FF] = 8
    fingerprint[0][0xBE] = 6
    cp = GMInterface.get_params(GMCar.CHEVROLET_VOLT_2019, fingerprint, [], True, False, False)
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x5007)
    cp.alternativeExperience = 32
    card = Car.__new__(Car)
    card.CP = cp
    card.CI = GMInterface(cp)
    card.vehicle_startup = VehicleStartupOwner()
    card.ci_initialized = False
    card.initialized_prev = False
    card.aol_replay = True
    card.ioniq6_long_prearmed = False
    card.ioniq6_long_selected = False
    card.can_callbacks = (lambda wait_for_one=False: [], lambda _: None)
    params = SimpleNamespace(put_bool=Mock())
    self.enterContext(patch.object(card, 'params', params, create=True))
    card.state_publish = Mock()
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    cs, _ = feed_camera(card.CI, packer, 22_000_000_000, active=False)
    self.assertTrue(cs.canValid)
    card.state_update = Mock(return_value=(cs, None))
    self.defer_radar_fixture(card)
    control = structs.CarControl.new_message().as_reader()
    panda = SimpleNamespace(safetyModel=cp.safetyConfigs[0].safetyModel, safetyParam=0x5007,
                            alternativeExperience=cp.alternativeExperience, safetyRxChecksInvalid=True)
    mono, boot = 20_000_000_000, 22_000_000_000

    class SubMaster:
      seen = dict.fromkeys(('carControl', 'pandaStates', 'onroadEvents'), True)
      valid = dict.fromkeys(('carControl', 'pandaStates'), True)
      alive = dict(valid)
      logMonoTime = {'carControl': mono, 'pandaStates': boot}
      recv_time = dict.fromkeys(('carControl', 'pandaStates'), mono / 1e9)
      events = [SimpleNamespace(name=EventName.selfdriveInitializing)]

      def __getitem__(self, key):
        return {'carControl': control, 'pandaStates': [panda], 'onroadEvents': self.events}[key]

      def all_alive(self, services):
        return all(self.alive[name] for name in services)

    sm = SubMaster()
    self.enterContext(patch.object(card, 'sm', sm, create=True))
    sent = []
    self.enterContext(patch.object(card, 'publish_sendcan', lambda frames, valid=True: sent.extend(frames)))
    card.learning_send_committed_mono_ns = 0
    # These saved requests cannot acquire startup brake ownership.
    card.CI.CC.gm_auto_hold = True
    card.CI.CC.volt_one_pedal = True
    with patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=mono), \
         patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', return_value=boot), \
         patch.object(card.CI, 'apply', wraps=card.CI.apply) as apply, \
         patch.object(card.CI, 'init', wraps=card.CI.init) as init:
      self.assertFalse(card.startup_panda_configured())
      self.assertTrue(card.startup_panda_configured(inactive_keepalive=True))
      for _ in range(30):
        card.step()
      self.assertEqual(apply.call_count, 30)
      init.assert_called_once()
      params.put_bool.assert_called_once_with('ControlsReady', True)
      self.assertEqual(card.CI.CC.gm_auto_hold_state.drive_ns, 0)
      self.assertEqual(card.CI.CC.apply_brake, 0)
      for call in apply.call_args_list:
        cc, timestamp = call.args
        self.assertEqual(timestamp, boot)
        self.assertTrue(call.kwargs['startup_keepalive'])
        self.assertFalse(cc.enabled or cc.latActive or cc.longActive)
        self.assertEqual((cc.actuators.torque, cc.actuators.accel), (0., 0.))
      self.assertTrue(sent)
      self.assert_inactive_gm_stream(sent, alpha=True, brake_bus=2, inactive_regen=-500)
      panda.safetyParam = 0x5087
      card.step()
      self.assertEqual(apply.call_count, 30)
      panda.safetyParam = 0x5007
      sm.logMonoTime['pandaStates'] = boot - 300_000_001
      card.step()
      self.assertEqual(apply.call_count, 30)
      sm.logMonoTime['pandaStates'] = boot
      active = structs.CarControl.new_message(latActive=True)
      control = active.as_reader()
      card.step()
      self.assertEqual(apply.call_count, 30)
      control = structs.CarControl.new_message().as_reader()
      sm.events = []
      card.step()
      self.assertEqual(apply.call_count, 31)
      self.assertEqual(apply.call_args.args[1], boot)
      self.assertFalse(apply.call_args.kwargs)


  def test_volt_ascm_inactive_startup_has_no_six_second_handoff_gap(self):
    self._assert_volt_ascm_inactive_startup(alpha=True)

  def test_volt_stock_ascm_inactive_startup_has_no_six_second_steering_gap(self):
    self._assert_volt_ascm_inactive_startup(alpha=False)

  def _assert_volt_ascm_inactive_startup(self, *, alpha):
    from opendbc.car import Bus, structs
    from opendbc.can import CANPacker
    from opendbc.car.gm.values import DBC
    from opendbc.car.gm.tests.test_ascm_intercept import params as ascm_params
    from opendbc.car.gm.tests.test_volt_camera_control import feed_camera
    cp = ascm_params(GMCar.CHEVROLET_VOLT_ASCM, sascm=True, alpha=alpha, accelerator=True, radar=True)
    word = 0xD114 if alpha else 0xA05
    if alpha:
      cp.safetyConfigs[0].safetyParam = word
    self.assertEqual(cp.safetyConfigs[0].safetyParam, word)
    cadence = ((0x180, 110_000_000),)
    if alpha:
      cadence += ((0x2CB, 50_000_000), (0x315, 50_000_000), (0x370, 50_000_000))
    ready_cadence = cadence
    cp.alternativeExperience = 32
    card = Car.__new__(Car)
    card.CP = cp
    card.CI = GMInterface(cp)
    card.vehicle_startup = VehicleStartupOwner()
    card.ci_initialized = False
    card.initialized_prev = False
    card.aol_replay = True
    card.ioniq6_long_prearmed = False
    card.ioniq6_long_selected = False
    card.can_callbacks = (lambda wait_for_one=False: [], lambda _: None)
    params = SimpleNamespace(put_bool=Mock())
    self.enterContext(patch.object(card, 'params', params, create=True))
    card.state_publish = Mock()
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    cs, _ = feed_camera(card.CI, packer, 22_000_000_000, active=False)
    self.assertTrue(cs.canValid)
    counter = [1]

    def state_update():
      state, _ = feed_camera(card.CI, packer, boot, counter=counter[0] % 4, speed=0., active=False)
      counter[0] += 1
      self.assertTrue(state.canValid)
      return state, None

    self.enterContext(patch.object(card, 'state_update', state_update))
    self.defer_radar_fixture(card)
    control = structs.CarControl.new_message().as_reader()
    panda = SimpleNamespace(safetyModel=cp.safetyConfigs[0].safetyModel, safetyParam=word,
                            alternativeExperience=cp.alternativeExperience, safetyRxChecksInvalid=True)
    mono, boot = 20_000_000_000, 22_000_000_000

    class SubMaster:
      seen = dict.fromkeys(('carControl', 'pandaStates', 'onroadEvents'), True)
      valid = dict.fromkeys(('carControl', 'pandaStates'), True)
      alive = dict(valid)
      logMonoTime = {'carControl': mono, 'pandaStates': boot}
      recv_time = dict.fromkeys(('carControl', 'pandaStates'), mono / 1e9)
      events = [SimpleNamespace(name=EventName.selfdriveInitializing)]

      def __getitem__(self, key):
        return {'carControl': control, 'pandaStates': [panda], 'onroadEvents': self.events}[key]

      def all_alive(self, services):
        return all(self.alive[name] for name in services)

    sm = SubMaster()
    self.enterContext(patch.object(card, 'sm', sm, create=True))
    sent = []
    timestamps = {}
    envelopes = []

    def capture_packet(service, packet):
      self.assertEqual(service, 'sendcan')
      event = messaging.log_from_bytes(packet)
      self.assertTrue(event.valid)
      self.assertEqual(event.logMonoTime, boot)
      # Match pandad's unsigned BOOTTIME subtraction and strict one-second bound.
      self.assertLess((boot - int(event.logMonoTime)) & ((1 << 64) - 1), 1_000_000_000)
      envelopes.append(event.logMonoTime)

    self.enterContext(patch.object(card, 'pm', SimpleNamespace(send=capture_packet), create=True))

    def publish(frames, valid=True):
      Car.publish_sendcan(card, frames, valid=valid)
      self.assertTrue(valid)
      sent.extend(frames)
      for address, _, _ in frames:
        timestamps.setdefault(address, []).append(boot)

    self.enterContext(patch.object(card, 'publish_sendcan', publish))
    # These saved requests cannot acquire startup brake ownership.
    card.CI.CC.gm_auto_hold = True
    card.CI.CC.volt_one_pedal = True
    with patch('openpilot.selfdrive.car.card.time.monotonic', side_effect=lambda: mono / 1e9), \
         patch('openpilot.selfdrive.car.card.time.monotonic_ns', side_effect=lambda: mono), \
         patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', side_effect=lambda _clock: boot), \
         patch.object(card.CI, 'apply', wraps=card.CI.apply) as apply, \
         patch.object(card.CI, 'init', wraps=card.CI.init) as init:
      self.assertFalse(card.startup_panda_configured())
      self.assertTrue(card.startup_panda_configured(inactive_keepalive=True))
      for _ in range(601):
        sm.logMonoTime.update(carControl=mono, pandaStates=boot)
        sm.recv_time.update(carControl=mono / 1e9, pandaStates=mono / 1e9)
        card.step()
        mono += 10_000_000
        boot += 10_000_000
      sm.logMonoTime.update(carControl=mono, pandaStates=boot)
      sm.recv_time.update(carControl=mono / 1e9, pandaStates=mono / 1e9)
      self.assertEqual(apply.call_count, 601)
      init.assert_called_once()
      params.put_bool.assert_called_once_with('ControlsReady', True)
      self.assertEqual(card.CI.CC.gm_auto_hold_state.drive_ns, 0)
      self.assertEqual(card.CI.CC.apply_brake, 0)
      for call in apply.call_args_list:
        cc, timestamp = call.args
        self.assertGreaterEqual(timestamp, 22_000_000_000)
        self.assertLessEqual(timestamp, 28_000_000_000)
        self.assertTrue(call.kwargs['startup_keepalive'])
        self.assertFalse(cc.enabled or cc.latActive or cc.longActive)
        self.assertEqual((cc.actuators.torque, cc.actuators.accel), (0., 0.))
      for address, max_gap in cadence:
        times = timestamps[address]
        self.assertLessEqual(times[0], 22_100_000_000)
        self.assertGreaterEqual(times[-1], 27_900_000_000)
        self.assertLessEqual(max(b - a for a, b in zip(times, times[1:], strict=False)), max_gap)
      self.assertTrue(sent)
      if not alpha:
        self.assertFalse(any(address in (0x2CB, 0x315) for address, _, _ in sent))
        self.assertTrue(card.CP.pcmCruise)
        self.assertFalse(card.CP.openpilotLongitudinalControl)
      self.assert_inactive_gm_stream(sent, alpha=alpha, brake_bus=0, inactive_regen=-650)
      panda.safetyParam = word ^ 1
      card.step()
      self.assertEqual(apply.call_count, 601)
      panda.safetyParam = word
      sm.logMonoTime['pandaStates'] = boot - 300_000_001
      card.step()
      self.assertEqual(apply.call_count, 601)
      sm.logMonoTime['pandaStates'] = boot
      sm.valid['carControl'] = False
      card.step()
      self.assertEqual(apply.call_count, 601)
      sm.valid['carControl'] = True
      sm.logMonoTime['carControl'] = mono - 150_000_001
      card.step()
      self.assertEqual(apply.call_count, 601)
      sm.logMonoTime['carControl'] = mono
      active = structs.CarControl.new_message(latActive=True)
      control = active.as_reader()
      card.step()
      self.assertEqual(apply.call_count, 601)
      control = structs.CarControl.new_message().as_reader()
      sm.events = []
      # The physical CAN clock remains continuous after inactive startup.
      for _ in range(10):
        sm.logMonoTime.update(carControl=mono, pandaStates=boot)
        sm.recv_time.update(carControl=mono / 1e9, pandaStates=mono / 1e9)
        card.step()
        self.assertEqual(apply.call_args.args[1], boot)
        self.assertFalse(apply.call_args.kwargs)
        mono += 10_000_000
        boot += 10_000_000
      self.assertEqual(apply.call_count, 611)
      self.assertEqual(len(envelopes), 611)
      self.assertEqual(envelopes, [call.args[1] for call in apply.call_args_list])
      self.assertGreaterEqual((boot - mono) & ((1 << 64) - 1), 1_000_000_000)
      for before, after in zip(apply.call_args_list, apply.call_args_list[1:], strict=False):
        self.assertEqual(after.args[1] - before.args[1], 10_000_000)
      for address, max_gap in ready_cadence:
        times = timestamps[address]
        self.assertGreaterEqual(times[-1], 28_060_000_000)
        self.assertLessEqual(max(b - a for a, b in zip(times, times[1:], strict=False)), max_gap)


  def test_volt_startup_selector_denies_other_profiles_and_permissions(self):
    from opendbc.car.gm.tests.test_ascm_intercept import params as ascm_params
    from opendbc.car.gm.tests.test_volt_camera_control import camera_params
    card = Car.__new__(Car)
    self.assertFalse(card.volt_startup_keepalive())
    self.assertFalse(card.volt_sdgm_startup_keepalive())
    for cp in (None, SimpleNamespace(safetyConfigs=[]), SimpleNamespace(brand='hyundai'),
               SimpleNamespace(brand='toyota', alternativeExperience=32, flags=0,
                               carFingerprint=GMCar.CHEVROLET_VOLT_ASCM)):
      with self.subTest(cp=cp):
        card.CP = cp
        self.assertFalse(card.volt_startup_keepalive())
        self.assertFalse(card.volt_sdgm_startup_keepalive())
    for radar in (False, True):
      for accelerator in (False, True):
        cp = ascm_params(GMCar.CHEVROLET_VOLT_ASCM, sascm=True, alpha=True,
                         accelerator=accelerator, radar=radar)
        cp.alternativeExperience = 32
        card.CP = cp
        self.assertTrue(card.volt_startup_keepalive())
        for field, value in (('alternativeExperience', 0), ('openpilotLongitudinalControl', False),
                             ('pcmCruise', True), ('passive', True), ('dashcamOnly', True),
                             ('flags', int(cp.flags) | int(GMFlags.PEDAL_LONG))):
          saved = getattr(cp, field)
          setattr(cp, field, value)
          self.assertFalse(card.volt_startup_keepalive())
          setattr(cp, field, saved)
    for radar in (False, True):
      for accelerator in (False, True):
        cp = ascm_params(GMCar.CHEVROLET_VOLT_ASCM, sascm=True, alpha=False,
                         accelerator=accelerator, radar=radar)
        cp.alternativeExperience = 32
        card.CP = cp
        self.assertTrue(card.volt_startup_keepalive())
        for field, value in (('alternativeExperience', 0), ('openpilotLongitudinalControl', True),
                             ('pcmCruise', False), ('passive', True), ('dashcamOnly', True),
                             ('radarUnavailable', not cp.radarUnavailable),
                             ('flags', int(cp.flags) | int(GMFlags.PEDAL_LONG))):
          saved = getattr(cp, field)
          setattr(cp, field, value)
          self.assertFalse(card.volt_startup_keepalive())
          setattr(cp, field, saved)
        word = cp.safetyConfigs[0].safetyParam
        cp.safetyConfigs[0].safetyParam = word | 0x4000
        self.assertFalse(card.volt_startup_keepalive())
        cp.safetyConfigs[0].safetyParam = word
    cc = GMInterface.get_params(GMCar.CHEVROLET_VOLT_CC, gen_empty_fingerprint(), [], True, False, False)
    for cp in (cc, camera_params(), GMInterface.get_params(GMCar.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
                                                       gen_empty_fingerprint(), [], True, False, False)):
      cp.alternativeExperience = 32
      card.CP = cp
      self.assertFalse(card.volt_startup_keepalive())


if __name__ == '__main__':
  unittest.main()
