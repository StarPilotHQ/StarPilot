import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import apply_conditional_stop_hold, conditional_handoff_for_frame, update_curve_frame
from openpilot.starpilot.conditional_mode.planner_host import ConditionalPlannerHost
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
from openpilot.starpilot.curve_speed.host import CurveHost
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages

BOOT = 101_000_000_000
NOW = 100_000_000_000
DRIVE = NOW - 10_000_000_000


class Frame(dict):
  def __init__(self):
    super().__init__(messages()[0])
    device = messaging.new_message('deviceState')
    device.deviceState.started = True
    device.deviceState.startedMonoTime = DRIVE
    self['deviceState'] = device.deviceState
    self.logMonoTime = dict.fromkeys(self, NOW - 5_000_000)
    self.valid = dict.fromkeys(self, True)
    self.alive = dict.fromkeys(self, True)
    self['carControl'].longActive = True
    self['carState'].canValid = True
    self['carControl'].enabled = True
    self['modelV2'].timestampEof = BOOT - 5_000_000
    self.seen = dict.fromkeys(self, True)
    self.recv_time = dict.fromkeys(self, (NOW - 5_000_000) / 1e9)

  def all_checks(self, services):
    return all(self.valid[name] and self.alive[name] for name in services)


class ConditionalHandoffTests(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.params = Params(directory.name)
    self.host = ConditionalPlannerHost(self.params)
    self.addCleanup(self.host.close)
    self.cp = CarInterface.get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    self.cp.openpilotLongitudinalControl = True
    self.sm = Frame()
    self.host.settings.refresh(NOW)

  def key(self, now=NOW, drive=DRIVE):
    return conditional_handoff_for_frame(self.host, self.sm, self.cp, now, drive)

  def save(self, choice):
    self.params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=choice))), block=True)

  def test_current_saved_owner_is_stable_across_bounded_refresh(self):
    key = self.key()
    self.assertEqual(key[2:], (DRIVE, ModeChoice.CEM.value))
    with patch('openpilot.starpilot.conditional_mode.runtime_settings.read_saved') as read:
      self.assertEqual(self.key(NOW + 100_000_000), key)
      read.assert_not_called()
    self.assertEqual(self.key(NOW + 500_000_000), key)
    self.save(ModeChoice.CCM)
    self.sm.logMonoTime['deviceState'] = NOW + 995_000_000
    changed = self.key(NOW + 1_000_000_000)
    self.assertEqual(changed[3], ModeChoice.CEM.value)
    self.assertNotEqual(changed[1], key[1])

  def test_stock_safe_mode_and_invalid_document_cannot_enable_restraint(self):
    for choice in ModeChoice:
      with self.subTest(choice=choice):
        self.save(choice)
        self.host.settings = ConditionalSettingsOwner(self.params)
        self.assertEqual(self.key() is None, choice is ModeChoice.STOCK)
    self.params.put_bool('SafeMode', True, block=True)
    self.host.settings = ConditionalSettingsOwner(self.params)
    self.assertIsNone(self.key())
    self.params.put_bool('SafeMode', False, block=True)
    Path(self.params.get_param_path('ConditionalModeConfig')).write_bytes(b'{')
    self.host.settings = ConditionalSettingsOwner(self.params)
    self.assertIsNone(self.key())

  def test_drive_identity_freshness_and_longitudinal_capability(self):
    self.assertIsNone(conditional_handoff_for_frame(None, self.sm, self.cp, NOW, DRIVE))
    for drive in (0, DRIVE - 1, NOW + 1):
      self.assertIsNone(self.key(drive=drive))
    for field in ('passive', 'dashcamOnly'):
      setattr(self.cp, field, True)
      self.assertIsNone(self.key())
      setattr(self.cp, field, False)
    self.cp.openpilotLongitudinalControl = False
    self.assertIsNone(self.key())
    self.cp.openpilotLongitudinalControl = True
    self.sm['deviceState'].started = False
    self.assertIsNone(self.key())
    self.sm['deviceState'].started = True
    for stamps in (self.sm.valid, self.sm.alive):
      stamps['deviceState'] = False
      self.assertIsNone(self.key())
      stamps['deviceState'] = True
    for stamp in (NOW + 1, NOW - 1_000_000_001):
      self.sm.logMonoTime['deviceState'] = stamp
      self.assertIsNone(self.key())
    self.sm.logMonoTime['deviceState'] = NOW - 750_000_000
    self.assertIsNotNone(self.key(), 'Device freshness must follow its two-Hz publisher, not the model rate')

  def test_actual_planner_receives_same_owner_with_and_without_curve_host(self):
    key = self.key()
    for curve in (None, CurveHost(enabled=False, replay=True)):
      with self.subTest(curve=curve):
        planner = LongitudinalPlanner(self.cp, init_v=20.0)
        with patch.object(planner, 'update', wraps=planner.update) as update:
          update_curve_frame(planner, self.sm, self.cp, NOW, host=curve, conditional_handoff=key)
          self.assertEqual(update.call_args.kwargs['conditional_handoff'], key)
          self.assertEqual(planner.mpc.solution_status, 0)
        self.assertEqual(planner.experimental_release.key, key)

  def test_committed_standstill_survives_chill_planner_publication(self):
    from types import SimpleNamespace
    self.sm['carState'].vEgo = 0.
    self.sm['carState'].standstill = True
    self.sm['selfdriveState'].experimentalMode = False
    self.host.mode.drive_id = DRIVE
    detector = self.host.mode.projector.stop_detector
    detector.standstill_committed = True
    planner = LongitudinalPlanner(self.cp, init_v=0.)
    for _ in range(20):
      update_curve_frame(planner, self.sm, self.cp, NOW)
    self.assertFalse(planner.output_should_stop)
    self.assertTrue(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW, DRIVE, now_boot_ns=BOOT))
    published = []
    planner.publish(self.sm, SimpleNamespace(send=lambda service, value: published.append(value) if service == 'longitudinalPlan' else None))
    self.assertTrue(published[-1].longitudinalPlan.shouldStop)
    self.assertLessEqual(published[-1].longitudinalPlan.aTarget, 0.)
    from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState
    receiver = LongControl(self.cp)
    receiver.long_control_state = LongCtrlState.stopping
    plan = published[-1].longitudinalPlan
    output = receiver.update(True, self.sm['carState'], plan.aTarget, plan.shouldStop, (-3.5, 2.))
    self.assertEqual(receiver.long_control_state, LongCtrlState.stopping)
    self.assertLessEqual(output, 0.)
    detector.standstill_committed = False
    update_curve_frame(planner, self.sm, self.cp, NOW + 10_000_000)
    self.assertFalse(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW + 10_000_000, DRIVE, now_boot_ns=BOOT + 10_000_000))
    planner.publish(self.sm, SimpleNamespace(send=lambda service, value: published.append(value) if service == 'longitudinalPlan' else None))
    self.assertFalse(published[-1].longitudinalPlan.shouldStop)

  def test_committed_hold_requires_current_drive_and_control_evidence(self):
    self.host.mode.drive_id = DRIVE
    self.host.mode.projector.stop_detector.standstill_committed = True
    planner = LongitudinalPlanner(self.cp)
    cases = [('carState', 'gasPressed'), ('carState', 'brakePressed'), ('carState', 'canTimeout'),
             ('carState', 'canValid'), ('carControl', 'enabled'), ('carControl', 'longActive'),
             ('selfdriveState', 'enabled')]
    for service, field in cases:
      self.host.mode.projector.stop_detector.standstill_committed = True
      original = getattr(self.sm[service], field)
      setattr(self.sm[service], field, not original)
      self.assertFalse(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW, DRIVE, now_boot_ns=BOOT), field)
      setattr(self.sm[service], field, original)
    for service in ('carState', 'carControl', 'selfdriveState', 'modelV2'):
      self.host.mode.projector.stop_detector.standstill_committed = True
      stamp = self.sm.logMonoTime[service]
      self.sm.logMonoTime[service] = NOW - 250_000_001
      self.assertFalse(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW, DRIVE, now_boot_ns=BOOT), service)
      self.sm.logMonoTime[service] = stamp
    self.host.mode.projector.stop_detector.standstill_committed = True
    self.sm.logMonoTime['modelV2'] = NOW - 150_000_001
    self.assertFalse(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW, DRIVE, now_boot_ns=BOOT))
    self.sm.logMonoTime['modelV2'] = NOW - 5_000_000
    self.assertFalse(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW, DRIVE + 1, now_boot_ns=BOOT))

    self.assertFalse(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW, DRIVE))
    self.sm.valid['vehicleParameters'] = False
    self.assertFalse(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW, DRIVE, now_boot_ns=BOOT))
    self.sm.valid['vehicleParameters'] = True
    self.sm['modelV2'].timestampEof = BOOT - 150_000_001
    self.assertFalse(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW, DRIVE, now_boot_ns=BOOT))
    self.sm['modelV2'].timestampEof = BOOT - 5_000_000
    self.params.put('SafeMode', True, block=True)
    self.host.settings.refresh(NOW + 1_000_000_000)
    self.assertFalse(apply_conditional_stop_hold(self.host, planner, self.sm, self.cp, NOW + 1_000_000_000,
                                               DRIVE, now_boot_ns=BOOT + 1_000_000_000))
    self.assertFalse(self.host.mode.projector.stop_detector.standstill_committed)


if __name__ == '__main__':
  unittest.main()
