import unittest

from openpilot.starpilot.lateral.low_speed_advisory import LowSpeedAdvisory


class TestLowSpeedAdvisory(unittest.TestCase):
  def test_startup_first_crossing_episode_and_later_crossings(self):
    owner = LowSpeedAdvisory()
    minimum = 10 / 3.6
    for speed in (0., 1., minimum - .001):
      self.assertFalse(owner.update(speed, minimum, drive_id=10))
    self.assertFalse(owner.update(minimum, minimum, drive_id=10))
    for speed in (minimum - .001, 0., 1., 0.):
      self.assertTrue(owner.update(speed, minimum, drive_id=10))
    self.assertFalse(owner.update(minimum, minimum, drive_id=10))
    for speed in (0., minimum + 1., 0.):
      self.assertFalse(owner.update(speed, minimum, drive_id=10))

  def test_confirmed_new_drive_and_process_reset(self):
    owner = LowSpeedAdvisory()
    owner.update(4., 3., drive_id=10)
    self.assertTrue(owner.update(2., 3., drive_id=10))
    self.assertTrue(owner.update(2., 3., drive_id=0))
    self.assertFalse(owner.update(2., 3., drive_id=20))
    self.assertFalse(owner.update(3., 3., drive_id=20))
    self.assertTrue(owner.update(2., 3., drive_id=20))
    self.assertFalse(LowSpeedAdvisory().update(2., 3., drive_id=20))

  def test_invalid_sample_cannot_create_or_erase_crossing(self):
    owner = LowSpeedAdvisory()
    for speed in (float('nan'), float('inf'), -float('inf')):
      self.assertFalse(owner.update(speed, 3., drive_id=10))
    self.assertFalse(owner.update(2., 3., drive_id=10))
    owner.update(4., 3., drive_id=10)
    self.assertTrue(owner.update(2., 3., drive_id=10))
    self.assertFalse(owner.update(float('nan'), 3., drive_id=0))
    self.assertTrue(owner.update(2., 3., drive_id=10))
    self.assertFalse(owner.update(2., 0., drive_id=10))

  def test_actual_selfdrived_event_hook_preserves_faults(self):
    from unittest.mock import patch
    from opendbc.car import gen_empty_fingerprint, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import CAR
    from openpilot.cereal import messaging
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.selfdrived.events import ET, EventName
    from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD

    fingerprint = gen_empty_fingerprint()
    fingerprint[0][0x201] = 6
    fingerprint[2].update({0x180: 4, 0x320: 8})
    cp = CarInterface.get_params(CAR.CHEVROLET_BOLT_CC_2018_2021, fingerprint, [], False, False, False)
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x9D)
    self.assertAlmostEqual(cp.minSteerSpeed, 10 / 3.6, delta=1e-6)
    with OpenpilotPrefix(), patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', True):
      drive = SelfdriveD(CP=cp)
      drive.initialized = True
      drive.active = True
      device = messaging.new_message('deviceState', valid=True, logMonoTime=1_000_000_000)
      device.deviceState.started = True
      device.deviceState.startedMonoTime = 1_000_000_000
      device.deviceState.freeSpacePercent = 50.
      drive.sm.update_msgs(1., [device.as_reader()])
      state = structs.CarState()
      state.canValid = True
      state.gearShifter = structs.CarState.GearShifter.drive
      state.cruiseState.available = True
      state.vCruise = 40.

      def sample(speed, *, temporary=False, permanent=False, valid=True, timeout=False):
        state.vEgo = speed
        state.lowSpeedAlert = speed < cp.minSteerSpeed
        state.steerFaultTemporary = temporary
        state.steerFaultPermanent = permanent
        state.canValid, state.canTimeout = valid, timeout
        drive.update_events(state)
        drive.CS_prev = structs.CarState.new_message(**state.to_dict())
        return set(drive.events.names)

      for speed, expected in ((0., False), (cp.minSteerSpeed, False), (1., True),
                              (0., True), (cp.minSteerSpeed, False), (1., False)):
        with self.subTest(speed=speed, expected=expected):
          self.assertEqual(EventName.belowSteerSpeed in sample(speed), expected)
      # Actual CarEvents severity is retained, regardless of advisory suppression.
      temporary = sample(4., temporary=True)
      self.assertIn(EventName.steerTempUnavailableSilent, temporary)
      warning = next(event for event in drive.events.to_msg() if event.name == EventName.steerTempUnavailableSilent)
      self.assertTrue(warning.warning)
      self.assertFalse(warning.immediateDisable)
      permanent = sample(4., permanent=True)
      self.assertIn(EventName.steerUnavailable, permanent)
      self.assertTrue(drive.events.contains(ET.IMMEDIATE_DISABLE))
      self.assertIn(EventName.canError, sample(1., valid=False))
      self.assertIn(EventName.canBusMissing, sample(1., valid=False, timeout=True))
      device.deviceState.startedMonoTime = 2_000_000_000
      device.logMonoTime = 2_000_000_000
      drive.sm.update_msgs(2., [device.as_reader()])
      self.assertNotIn(EventName.belowSteerSpeed, sample(1.))
      self.assertNotIn(EventName.belowSteerSpeed, sample(cp.minSteerSpeed))
      self.assertIn(EventName.belowSteerSpeed, sample(1.))

  def test_disarmed_crossings_do_not_spend_the_advisory(self):
    owner = LowSpeedAdvisory()
    for speed in (4., 2., 4., 0.):
      self.assertFalse(owner.update(speed, 3., drive_id=10, eligible=False))
    self.assertFalse(owner.update(4., 3., drive_id=10, eligible=True))
    self.assertTrue(owner.update(2., 3., drive_id=10, eligible=True))
    self.assertFalse(owner.update(2., 3., drive_id=10, eligible=False))
    self.assertFalse(owner.update(2., 3., drive_id=10, eligible=True))

  def test_advisory_eligibility_keeps_gm_main_and_independent_aol_distinct(self):
    from types import SimpleNamespace
    from openpilot.starpilot.lateral.low_speed_advisory import advisory_eligible
    state = SimpleNamespace(cruiseState=SimpleNamespace(available=False))
    armed = SimpleNamespace(allowedLatch=True, lateralArmed=True, pauseLateral=False)
    for brand in ('gm', 'hyundai', 'toyota', 'ford', 'honda'):
      cp = SimpleNamespace(brand=brand)
      self.assertFalse(advisory_eligible(cp, state, active=False, intent=None))
      self.assertEqual(advisory_eligible(cp, state, active=True, intent=None), brand != 'gm')
      self.assertEqual(advisory_eligible(cp, state, active=False, intent=armed), brand != 'gm')
    state.cruiseState.available = True
    for active, latched, lateral, paused, expected in (
        (False, False, False, False, False), (False, False, True, False, False),
        (False, True, False, False, False), (False, True, True, True, False),
        (False, True, True, False, True), (True, False, False, False, True)):
      intent = SimpleNamespace(allowedLatch=latched, lateralArmed=lateral, pauseLateral=paused)
      self.assertEqual(advisory_eligible(SimpleNamespace(brand='gm'), state, active=active, intent=intent), expected)

  def test_actual_sierra_events_and_alerts_stay_quiet_with_main_off(self):
    from types import SimpleNamespace
    from unittest.mock import patch
    from opendbc.car import gen_empty_fingerprint, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import CAR
    from openpilot.cereal import messaging
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.selfdrived.events import ET, EventName
    from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD

    fingerprint = gen_empty_fingerprint()
    fingerprint[0][0xBE] = 6
    fingerprint[2].update({0x180: 4, 0x320: 6, 0x370: 6})
    cp = CarInterface.get_params(CAR.CHEVROLET_SILVERADO, fingerprint, [], False, False, False)
    self.assertAlmostEqual(cp.minSteerSpeed, 10 / 3.6, delta=1e-6)
    with OpenpilotPrefix(), patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', True):
      drive = SelfdriveD(CP=cp)
      drive.initialized = True
      drive.active = drive.enabled = False
      drive.aol_replay = True
      device = messaging.new_message('deviceState', valid=True, logMonoTime=1_000_000_000)
      device.deviceState.started = True
      device.deviceState.startedMonoTime = 1_000_000_000
      drive.sm.update_msgs(1., [device.as_reader()])
      state = structs.CarState.new_message(canValid=True, gearShifter='drive', vCruise=40.)
      state.cruiseState.available = False
      previous = state.as_reader()

      def sample(speed):
        state.vEgo = speed
        state.lowSpeedAlert = speed < cp.minSteerSpeed
        drive.sm.frame += 1
        drive.events.clear()
        drive._update_car_event_sample(state, previous)
        # An unrelated AOL pause may request warning presentation while off.
        drive.state_machine.current_alert_types = [ET.PERMANENT, ET.WARNING, ET.IMMEDIATE_DISABLE]
        drive.update_alerts(state)

      for speed in (4., 1., 0., 4., 1.):
        sample(speed)
        self.assertNotIn(EventName.belowSteerSpeed, drive.events.names)
        self.assertNotIn('belowSteerSpeed/warning', drive.AM.alerts)
      state.cruiseState.available = True
      drive.aol_last_intent = SimpleNamespace(allowedLatch=True, lateralArmed=True, pauseLateral=False)
      sample(4.)
      sample(1.)
      self.assertIn(EventName.belowSteerSpeed, drive.events.names)
      self.assertEqual(drive.AM.current_alert.alert_type, 'belowSteerSpeed/warning')
      # Main can turn off after the event sample but before alert selection.
      state.cruiseState.available = False
      drive.update_alerts(state)
      self.assertNotIn(EventName.belowSteerSpeed, drive.events.names)
      self.assertNotIn('belowSteerSpeed/warning', drive.AM.alerts)
      for fault in (EventName.steerTempUnavailableSilent, EventName.steerUnavailable, EventName.canError):
        drive.events.clear()
        drive.events.add(fault)
        drive.update_alerts(state)
        self.assertIn(fault, drive.events.names)
        self.assertNotEqual(drive.AM.current_alert.alert_type, 'belowSteerSpeed/warning')
