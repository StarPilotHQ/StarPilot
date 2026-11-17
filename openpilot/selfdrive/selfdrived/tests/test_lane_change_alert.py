"""Optional status only changes the native pre-change alert copy."""

from types import SimpleNamespace
from copy import copy
from unittest.mock import patch
import unittest

from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.starpilot.controllers.mode_actions import SwitchbackCooldown
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Alert, ET, EVENTS, Events, Priority
from openpilot.selfdrive.selfdrived.selfdrived import EventName, SelfdriveD
from openpilot.starpilot.lateral.lane_change_status_wire import Direction, LaneChangeStatus, Phase, encode


class FakeMaster:
  def __init__(self, model, raw=None):
    self.values = {"modelV2": model, "laneChangeAssistWire": raw,
                   "deviceState": SimpleNamespace(started=False, startedMonoTime=0)}
    self.seen = {"laneChangeAssistWire": raw is not None}
    self.alive = {"laneChangeAssistWire": raw is not None}
    self.valid = {"laneChangeAssistWire": raw is not None, "modelV2": True}
    self.recv_time = {"laneChangeAssistWire": 1.02}
    self.logMonoTime = {"laneChangeAssistWire": 1_020_000_000}
    self.frame = 1

  def __getitem__(self, key):
    return self.values[key]


class TestLaneChangeAlert(unittest.TestCase):
  def _run_alert(self, raw=None, *, frame_id=77):
    model = SimpleNamespace(frameId=frame_id, timestampEof=10_000_000_000,
                            meta=SimpleNamespace(laneChangeDirection="left"))
    drive = SelfdriveD.__new__(SelfdriveD)
    drive.events = Events()
    drive.events.add(EventName.preLaneChangeLeft)
    self.enterContext(patch.object(drive, "state_machine", SimpleNamespace(current_alert_types=[ET.WARNING], soft_disable_timer=0), create=True))
    drive.CP = car.CarParams.new_message()
    drive.personality = next(iter(log.LongitudinalPersonality.schema.enumerants.values()))
    drive.is_metric = False
    drive.enabled = False
    drive.switchback_capable = False
    drive.switchback_setting_ns = 1_050_000_000
    drive.switchback_cooldown_ns = 300_000_000_000
    drive.switchback_cooldown = SwitchbackCooldown()
    self.enterContext(patch.object(drive, "sm", FakeMaster(model, raw), create=True))
    alerts = AlertManager()
    self.enterContext(patch.object(drive, "AM", alerts, create=True))
    drive.lane_status_session = ""
    drive.lane_status_sequence = -1
    with patch("openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns", return_value=1_050_000_000):
      drive.update_alerts(object())
    return alerts.current_alert

  def test_missing_or_mismatched_status_is_neutral(self):
    self.assertEqual((self._run_alert().alert_text_1, self._run_alert().alert_text_2),
                     ("Lane Change Pending", "Check surroundings"))
    status = LaneChangeStatus("session", 1, 77, 10_000_000_000, 1_000_000_000, 1_100_000_000,
                              Phase.MANUAL_REQUIRED, Direction.LEFT, False, True)
    mismatched = self._run_alert(encode(status), frame_id=78)
    self.assertEqual((mismatched.alert_text_1, mismatched.alert_text_2),
                     ("Lane Change Pending", "Check surroundings"))

    compact = copy(EVENTS[EventName.preLaneChangeLeft][ET.WARNING])
    assert isinstance(compact, Alert)
    compact.alert_text_1, compact.alert_text_2 = "Steer Left", "Confirm Lane Change"
    with patch.dict(EVENTS, {EventName.preLaneChangeLeft: {ET.WARNING: compact}}):
      neutral = self._run_alert()
      self.assertEqual((neutral.alert_text_1, neutral.alert_text_2),
                       ("Lane Change Pending", "Check surroundings"))
      manual = self._run_alert(encode(status))
      self.assertEqual((manual.alert_text_1, manual.alert_text_2),
                       ("Steer Left", "Confirm Lane Change"))

  def test_fresh_manual_keeps_stock_and_waiting_copy_does_not_mutate_template(self):
    stock = EVENTS[EventName.preLaneChangeLeft][ET.WARNING]
    original = stock.alert_text_1
    manual = LaneChangeStatus("session", 1, 77, 10_000_000_000, 1_000_000_000, 1_100_000_000,
                              Phase.MANUAL_REQUIRED, Direction.LEFT, False, True)
    self.assertEqual(self._run_alert(encode(manual)).alert_text_1, original)
    aol_manual = LaneChangeStatus("session", 1, 77, 10_000_000_000, 1_000_000_000, 1_100_000_000,
                                   Phase.MANUAL_REQUIRED, Direction.LEFT, True, False)
    self.assertEqual(self._run_alert(encode(aol_manual)).alert_text_1, original)
    waiting = LaneChangeStatus("session", 2, 77, 10_000_000_000, 1_000_000_000, 1_100_000_000,
                               Phase.WAITING_FOR_DELAY, Direction.LEFT, True, True)
    changed = self._run_alert(encode(waiting))
    self.assertEqual((changed.alert_text_1, changed.alert_text_2),
                     ("Automatic Lane Change Pending", "Check surroundings; steer to confirm now"))
    self.assertEqual(stock.alert_text_1, original)
    self.assertEqual((changed.alert_type, changed.event_type), ("preLaneChangeLeft/warning", ET.WARNING))
    self.assertEqual((changed.priority, changed.alert_size, changed.duration, changed.audible_alert),
                     (stock.priority, stock.alert_size, stock.duration, stock.audible_alert))
    critical = copy(changed)
    critical.alert_type = "critical/warning"
    critical.priority = Priority.HIGH
    manager = AlertManager()
    manager.add_many(1, [changed, critical])
    manager.process_alerts(1, set())
    self.assertIs(manager.current_alert, critical)


if __name__ == "__main__":
  unittest.main()
