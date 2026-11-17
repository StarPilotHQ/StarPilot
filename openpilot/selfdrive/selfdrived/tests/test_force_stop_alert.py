"""Force Stop hold uses fresh dedicated transport and clears on physical release."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from openpilot.cereal import messaging, log
from openpilot.common.params import Params
from opendbc.car import structs
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events, ET, Alert, Priority
from openpilot.starpilot.controllers.mode_actions import SwitchbackCooldown
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.longitudinal.force_stop_alert import HoldAlertState

NOW = 10_000_000_000


class Sources:
  def __init__(self):
    names = ('deviceState', 'modelV2', 'longitudinalPlan', 'starpilotLongitudinalPlan', 'carControl')
    self.data = {name: getattr(messaging.new_message(name), name) for name in names}
    self.seen = self.valid = self.alive = dict.fromkeys(names, True)
    self.logMonoTime = dict.fromkeys(names, NOW - 10_000_000)
    self.recv_time = dict.fromkeys(names, (NOW - 5_000_000) / 1e9)
    self.frame = 1
    self.data['deviceState'].started = True
    self.data['deviceState'].startedMonoTime = NOW - 1_000_000_000
    plan = self.data['longitudinalPlan']
    plan.modelMonoTime = self.logMonoTime['modelV2']
    plan.shouldStop = True
    hold = self.data['starpilotLongitudinalPlan']
    hold.version = 1
    hold.sourcePlanMonoTime = self.logMonoTime['longitudinalPlan']
    hold.modelMonoTime = plan.modelMonoTime
    hold.driveStartMonoTime = self.data['deviceState'].startedMonoTime
    hold.forceStopHolding = True
    self.data['carControl'].longActive = True

  def __getitem__(self, name):
    return self.data[name]


def fixture():
  drive = SelfdriveD.__new__(SelfdriveD)
  drive.CP = structs.CarParams(openpilotLongitudinalControl=True)
  drive.enabled, drive.personality, drive.is_metric = True, log.LongitudinalPersonality.standard, False
  vars(drive)['state_machine'] = NS(current_alert_types=[ET.WARNING], soft_disable_timer=0)
  drive.events, drive.AM = Events(), AlertManager()
  vars(drive)['sm'] = Sources()
  drive.params = Mock(spec=Params, get=lambda _key: None)  # Missing advisory cooldown uses its real default.
  drive.switchback_capable = False
  drive.switchback_setting_ns = NOW
  drive.switchback_cooldown_ns = 300_000_000_000
  drive.switchback_cooldown = SwitchbackCooldown()
  drive.force_stop_hold_alert = HoldAlertState()
  drive.aol_car_state_log_ns, drive.conditional_car_state_valid = NOW - 10_000_000, True
  cs = structs.CarState(canValid=True, standstill=True)
  return drive, cs


class TestForceStopAlert(unittest.TestCase):
  def update(self, drive, cs, now=NOW):
    with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now):
      drive.update_alerts(cs)
    return drive.AM.current_alert

  def test_hold_transport_defaults_false(self):
    self.assertFalse(messaging.new_message('starpilotLongitudinalPlan').starpilotLongitudinalPlan.forceStopHolding)

  def test_actual_alert_clears_immediately_and_does_not_reappear_before_planner_catches_release(self):
    for action in ('gas', 'resume', 'accel'):
      drive, cs = fixture()
      alert = self.update(drive, cs)
      self.assertEqual((alert.alert_text_1, alert.alert_text_2), ('Enhanced Stop', 'Press RES or accelerator to proceed'))
      self.assertEqual(alert.audible_alert, log.SelfdriveState.AudibleAlert.none)
      if action == 'gas':
        cs.gasPressed = True
      else:
        cs.buttonEvents = [{'type': 'resumeCruise' if action == 'resume' else 'accelCruise', 'pressed': True}]
      drive.sm.frame += 1
      drive.aol_car_state_log_ns += 1_000_000
      self.assertEqual(self.update(drive, cs).alert_text_1, '')
      cs.gasPressed, cs.buttonEvents = False, []
      drive.sm.frame += 1
      self.assertEqual(self.update(drive, cs).alert_text_1, '')
      drive.sm['starpilotLongitudinalPlan'].forceStopHolding = False
      self.update(drive, cs)
      drive.sm['starpilotLongitudinalPlan'].forceStopHolding = True
      self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')

  def test_press_already_present_at_subscription_does_not_clear_hold(self):
    drive, cs = fixture()
    cs.buttonEvents = [{'type': 'resumeCruise', 'pressed': True}]
    self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')
    drive.aol_car_state_log_ns += 1_000_000
    cs.buttonEvents = [{'type': 'resumeCruise', 'pressed': False}]
    self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')
    drive.aol_car_state_log_ns += 1_000_000
    cs.buttonEvents = [{'type': 'resumeCruise', 'pressed': True}]
    self.assertEqual(self.update(drive, cs).alert_text_1, '')

  def test_hold_stays_visible_across_staggered_publisher_cadences(self):
    drive, cs = fixture()
    for tick in range(151):
      now = NOW + tick * 10_000_000
      for name, period, phase in (('deviceState', 50, 0), ('modelV2', 5, 0),
                                  ('longitudinalPlan', 5, 1), ('starpilotLongitudinalPlan', 5, 2), ('carControl', 1, 0)):
        if tick % period == phase:
          drive.sm.logMonoTime[name] = now
          drive.sm.recv_time[name] = now / 1e9
          if name == 'longitudinalPlan':
            drive.sm[name].modelMonoTime = drive.sm.logMonoTime['modelV2']
          elif name == 'starpilotLongitudinalPlan':
            drive.sm[name].sourcePlanMonoTime = drive.sm.logMonoTime['longitudinalPlan']
            drive.sm[name].modelMonoTime = drive.sm['longitudinalPlan'].modelMonoTime
            drive.sm.logMonoTime[name] = drive.sm.logMonoTime['longitudinalPlan']
      drive.aol_car_state_log_ns = now
      drive.sm.frame += 1
      self.assertEqual(self.update(drive, cs, now).alert_text_1, 'Enhanced Stop', tick)

  def test_companion_binding_and_expiration_do_not_latch_unmatched_hold(self):
    for defect in ('version', 'drive', 'future', 'wrong_pair', 'withdrawn'):
      drive, cs = fixture()
      hold = drive.sm['starpilotLongitudinalPlan']
      if defect == 'version':
        hold.version = 2
      elif defect == 'drive':
        hold.driveStartMonoTime -= 1
      elif defect == 'future':
        hold.sourcePlanMonoTime = NOW + 1
      elif defect == 'wrong_pair':
        hold.sourcePlanMonoTime -= 1
      else:
        drive.sm.valid['starpilotLongitudinalPlan'] = False
      self.assertEqual(self.update(drive, cs).alert_text_1, '', defect)
    drive, cs = fixture()
    self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')
    # Core arrives first: the prior matched pair bridges the bounded handoff.
    drive.sm.logMonoTime['longitudinalPlan'] += 1_000_000
    self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')
    self.assertEqual(self.update(drive, cs, NOW + 151_000_000).alert_text_1, '')
    drive, cs = fixture()
    self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')
    # Companion arrives first; its source binding cannot promote an unpaired hold.
    hold = drive.sm['starpilotLongitudinalPlan']
    hold.sourcePlanMonoTime += 1_000_000
    drive.sm.logMonoTime['starpilotLongitudinalPlan'] += 1_000_000
    self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')
    drive.sm.logMonoTime['longitudinalPlan'] = hold.sourcePlanMonoTime
    self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')

  def test_physical_release_during_companion_withdrawal_stays_suppressed(self):
    drive, cs = fixture()
    self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')
    drive.sm.valid['starpilotLongitudinalPlan'] = False
    cs.gasPressed = True
    self.assertEqual(self.update(drive, cs).alert_text_1, '')
    cs.gasPressed = False
    drive.sm.valid['starpilotLongitudinalPlan'] = True
    self.assertEqual(self.update(drive, cs).alert_text_1, '')

  def test_invalid_evidence_and_disengage_withdraw_without_overriding_other_alerts(self):
    for defect in ('stale', 'device_stale', 'previous_drive', 'model_mismatch', 'model_reference_stale',
                   'inactive', 'off', 'stock', 'generic_stop', 'car_invalid'):
      drive, cs = fixture()
      self.assertEqual(self.update(drive, cs).alert_text_1, 'Enhanced Stop')
      if defect == 'stale':
        drive.sm.logMonoTime['longitudinalPlan'] = NOW - 200_000_000
      elif defect == 'device_stale':
        drive.sm['deviceState'].startedMonoTime = NOW - 3_000_000_000
        drive.sm.logMonoTime['deviceState'] = NOW - 2_000_000_000
      elif defect == 'previous_drive':
        drive.sm['deviceState'].startedMonoTime = NOW - 1_000_000
      elif defect == 'model_mismatch':
        drive.sm['longitudinalPlan'].modelMonoTime = drive.sm.logMonoTime['modelV2'] + 1
      elif defect == 'model_reference_stale':
        drive.sm['longitudinalPlan'].modelMonoTime = NOW - 200_000_000
      elif defect == 'inactive':
        drive.sm['carControl'].longActive = False
      elif defect == 'off':
        drive.enabled = False
      elif defect == 'stock':
        drive.CP.openpilotLongitudinalControl = False
      elif defect == 'generic_stop':
        drive.sm['starpilotLongitudinalPlan'].forceStopHolding = False
      else:
        cs.canValid = False
      drive.sm.frame += 1
      self.assertEqual(self.update(drive, cs).alert_text_1, '')
    drive, cs = fixture()
    warning = Alert('Existing warning', '', log.SelfdriveState.AlertStatus.userPrompt,
                    log.SelfdriveState.AlertSize.mid, Priority.MID, structs.CarControl.HUDControl.VisualAlert.none,
                    log.SelfdriveState.AudibleAlert.none, .5)
    warning.alert_type, warning.event_type = 'existing/warning', ET.WARNING
    drive.AM.add_many(drive.sm.frame, [warning])
    self.assertEqual(self.update(drive, cs).alert_text_1, 'Existing warning')


class TestJetlinkLinkLostAlert(unittest.TestCase):
  def test_a_jetlink_hand_back_shows_big_model_lost(self):
    drive, cs = fixture()
    drive.jetlink_link_lost = True
    with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=NOW):
      drive.update_alerts(cs)
    self.assertEqual(drive.AM.current_alert.alert_type, 'jetlinkLinkLost/warning')
    self.assertEqual(drive.AM.current_alert.alert_text_1, 'Big Model Lost')
