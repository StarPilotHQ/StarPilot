from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.radar_interface import RadarInterface
from opendbc.car.gm.values import CAR, DBC
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Alert, AlertSize, AlertStatus, AudibleAlert, Priority, VisualAlert
from openpilot.starpilot.car.gm.radar_recovery import (
  ALERT_TYPE, EVENT_TYPE, RecoveryMonitor, RecoveryNotification, capability,
)


def vehicle(candidate=CAR.CHEVROLET_VOLT, *, radar=True, pedal=False):
  fp = gen_empty_fingerprint()
  fp[0][0xBE] = 6
  fp[2][0x320] = 6
  if radar:
    fp[1][0x460] = 8
  if pedal:
    fp[0][0x201] = 6
  if candidate == CAR.CHEVROLET_VOLT_CC:
    fp[0].update({0x184: 8, 0x34A: 5, 0x1E1: 7, 0x1C4: 8, 0xC9: 8, 0x3D1: 8, 0xBD: 7, 0x1F5: 8})
    fp[2][0x180] = 4
  return CarInterface.get_params(candidate, fp, [], False, False, False)


class Feed:
  def __init__(self, cp=None):
    self.cp = vehicle() if cp is None else cp
    self.monitor = RecoveryMonitor()
    self.now = 10_000_000_000
    self.enabled, self.drive = True, 1
    self.sample()

  def sample(self, *, fault=False, temporary=False, can_error=False, valid=True, **overrides):
    self.now += 100_000_000
    args = {'enabled': self.enabled, 'drive_id': self.drive, 'now_ns': self.now, 'stamp_ns': self.now,
                'updated': True, 'alive': True, 'valid': valid,
                'errors': {'canError': can_error, 'radarFault': fault, 'radarUnavailableTemporary': temporary}}
    args.update(overrides)
    return self.monitor.update(self.cp, **args)

  def healthy(self, count=21):
    return [self.sample() for _ in range(count)]


@pytest.mark.parametrize('candidate,pedal', [
  (CAR.CHEVROLET_VOLT, False), (CAR.CHEVROLET_VOLT_ASCM, False),
  (CAR.CHEVROLET_VOLT_CAMERA, False), (CAR.CHEVROLET_VOLT_2019, False), (CAR.CHEVROLET_VOLT_CC, True),
])
def test_exact_supported_factories_and_radarless_exclusion(candidate, pedal):
  assert capability(vehicle(candidate, pedal=pedal)) is not None
  assert capability(vehicle(candidate, radar=False, pedal=pedal)) is None


@pytest.mark.parametrize('cp', [None, SimpleNamespace(brand='gm', carFingerprint='Chevrolet Volt'),
                              vehicle(CAR.CHEVROLET_BOLT_EUV), vehicle(CAR.GMC_ACADIA),
                              vehicle(CAR.CHEVROLET_VOLT_CC)])
def test_other_unknown_and_unsupported_vehicles(cp):
  assert capability(cp) is None
  feed = Feed(cp=cp) if cp is not None else Feed()
  feed.cp = cp
  assert not feed.sample(fault=True)
  assert not any(feed.healthy())


@pytest.mark.parametrize('field', ['radarUnavailable', 'notCar', 'passive', 'dashcamOnly'])
def test_inactive_contract_is_ineligible(field):
  cp = vehicle()
  setattr(cp, field, True)
  assert capability(cp) is None


def test_two_seconds_of_fresh_health_once_per_fault_episode():
  feed = Feed()
  assert not feed.sample(fault=True, valid=False)
  assert feed.healthy(20) == [False] * 20
  assert feed.sample()
  assert not any(feed.healthy(50))
  assert not feed.sample(temporary=True, valid=False)
  assert feed.healthy() == [False] * 20 + [True]


def test_startup_persistent_fault_and_short_flicker_do_not_chime():
  feed = Feed()
  assert not any(feed.healthy(40))
  for _ in range(5):
    assert not feed.sample(fault=True, valid=False)
    assert not any(feed.healthy(15))
  for _ in range(30):
    assert not feed.sample(fault=True, valid=False)
  assert feed.healthy() == [False] * 20 + [True]


@pytest.mark.parametrize('interruption', [
  {'valid': False}, {'alive': False}, {'can_error': True}, {'errors': None},
  {'errors': {'canError': False}},
  {'errors': {'canError': False, 'radarFault': False, 'radarUnavailableTemporary': 0}},
  {'errors': {'canError': False, 'radarFault': False, 'radarUnavailableTemporary': False, 'wrongConfig': True}},
])
def test_bad_data_resets_continuous_recovery(interruption):
  feed = Feed()
  feed.sample(fault=True, valid=False)
  assert not any(feed.healthy(15))
  assert not feed.sample(**interruption)
  assert feed.healthy() == [False] * 20 + [True]


def test_cached_stale_missing_duplicate_and_future_updates_cannot_advance_timer():
  feed = Feed()
  feed.sample(fault=True, valid=False)
  assert not any(feed.healthy(15))
  last = feed.now
  for _ in range(4):
    assert not feed.sample(updated=False, stamp_ns=last)
  assert feed.healthy() == [False] * 20 + [True]
  for stamp in (feed.now, feed.now - 500_000_000, feed.now + 1_000_000_000):
    feed.sample(fault=True, valid=False)
    assert not any(feed.healthy(15))
    assert not feed.sample(stamp_ns=stamp)
    assert feed.healthy() == [False] * 20 + [True]


def test_communication_outage_alone_never_arms_recovery():
  feed = Feed()
  feed.sample(can_error=True, fault=True, valid=False)
  assert not any(feed.healthy(40))
  feed.sample(valid=False)
  assert not any(feed.healthy(40))


@pytest.mark.parametrize('reset', ['disable', 'offroad', 'new_drive', 'new_vehicle', 'restart'])
def test_pending_fault_history_resets(reset):
  feed = Feed()
  feed.sample(fault=True, valid=False)
  feed.healthy(15)
  if reset == 'disable':
    feed.enabled = False
    feed.sample()
    feed.enabled = True
  elif reset == 'offroad':
    feed.drive = 0
    feed.sample()
    feed.drive = 1
  elif reset == 'new_drive':
    feed.drive = 2
  elif reset == 'new_vehicle':
    feed.cp.carVin = 'another-volt'
  else:
    feed.monitor = RecoveryMonitor()
  assert not any(feed.healthy(40))


def test_enabling_does_not_consume_old_fault_or_notify_healthy_radar():
  feed = Feed()
  feed.enabled = False
  feed.sample(fault=True, valid=False)
  cached = feed.now
  feed.enabled = True
  feed.sample(fault=True, valid=False, stamp_ns=cached)
  assert not any(feed.healthy(40))


def test_actual_radar_blockage_reports_without_any_leads():
  cp = vehicle()
  radar = RadarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.radar])
  feed = Feed(cp)
  results = []
  for tick in range(45):
    fault = 2 <= tick <= 4
    messages = [packer.make_can_msg('F_LRR_Obj_Header', 1, {'FLRRSnsrBlckd': fault, 'FLRRNumValidTargets': 0})]
    messages += [packer.make_can_msg(address, 1, {}) for address in range(1121, 1141)]
    report = radar.update([(feed.now + 100_000_000, messages)])
    assert report is not None and not report.points
    errors = report.errors.to_dict()
    assert errors['radarFault'] == fault
    results.append(feed.sample(valid=not any(errors.values()), errors=errors))
  assert results.count(True) == 1


class Messages:
  def __init__(self):
    self.updated = self.alive = self.seen = self.valid = {'radarTracks': True}
    self.logMonoTime = {'radarTracks': 0}
    self.device = SimpleNamespace(started=True, startedMonoTime=1)
    self.errors = {'canError': False, 'radarFault': False, 'radarUnavailableTemporary': False}

  def all_checks(self, services):
    return True

  def __getitem__(self, service):
    return self.device if service == 'deviceState' else SimpleNamespace(errors=SimpleNamespace(to_dict=lambda: self.errors))


def test_disengaged_notification_priority_duration_and_no_replayed_chime():
  cp, sm, notification = vehicle(), Messages(), RecoveryNotification()
  manager = AlertManager()
  now = 10_000_000_000
  notification.update(cp, sm, enabled=True, now_ns=now)
  emitted = []
  for tick in range(23):
    now += 100_000_000
    sm.logMonoTime['radarTracks'] = now
    sm.errors['radarFault'] = tick == 0
    sm.valid = {'radarTracks': tick != 0}
    alert = notification.update(cp, sm, enabled=True, now_ns=now)
    if alert is not None:
      emitted.append(alert)
      manager.add_many(100, [alert])
  assert len(emitted) == 1
  alert = emitted[0]
  assert alert is not None and alert.alert_type == ALERT_TYPE
  assert alert.alert_text_1 == 'Radar available again' and alert.alert_text_2 == ''
  assert alert.audible_alert == AudibleAlert.prompt and alert.duration == 300
  urgent = Alert('Take control', '', AlertStatus.critical, AlertSize.full, Priority.HIGHEST,
                 VisualAlert.steerRequired, AudibleAlert.warningImmediate, 0.)
  urgent.alert_type, urgent.event_type = 'urgent', 'urgent'
  manager.add_many(100, [urgent])
  manager.process_alerts(100, set())
  assert manager.current_alert == urgent
  notification.selected(manager.current_alert)
  manager.process_alerts(102, set())
  assert manager.current_alert == alert
  notification.selected(manager.current_alert)
  manager.add_many(103, [urgent])
  manager.process_alerts(103, set())
  notification.selected(manager.current_alert)
  manager.process_alerts(105, set())
  assert manager.current_alert == alert and alert.audible_alert == AudibleAlert.none
  notification.update(cp, sm, enabled=False, now_ns=now)
  manager.process_alerts(106, {EVENT_TYPE})
  assert manager.current_alert.alert_type != ALERT_TYPE


def test_actual_selfdrived_alert_path_works_disengaged_and_resets_offroad(monkeypatch):
  from openpilot.cereal import messaging
  from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
  from openpilot.selfdrive.selfdrived.events import Events
  from openpilot.selfdrive.selfdrived.state import StateMachine
  from openpilot.starpilot.controllers.mode_actions import SwitchbackCooldown
  from openpilot.starpilot.longitudinal.force_stop_alert import HoldAlertState
  daemon = SelfdriveD.__new__(SelfdriveD)
  daemon.CP, daemon.AM = vehicle(), AlertManager()
  daemon.sm = messaging.SubMaster(['deviceState', 'radarTracks'])
  daemon.events, daemon.enabled, daemon.personality, daemon.is_metric = Events(), False, 1, True
  daemon.state_machine = StateMachine()
  daemon.switchback_capable = False
  daemon.switchback_cooldown = SwitchbackCooldown()
  daemon.force_stop_hold_alert = HoldAlertState()
  daemon.radar_recovery_enabled = True
  now = [10_000_000_000]
  daemon.switchback_setting_ns = now[0] + 100_000_000_000
  daemon.switchback_cooldown_ns = 0
  monkeypatch.setattr('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', lambda: now[0])

  def publish(*, fault=False, started=True, update_device=False):
    radar = messaging.new_message('radarTracks', valid=not fault, logMonoTime=now[0])
    radar.radarTracks.errors.radarFault = fault
    messages = [radar.as_reader()]
    if update_device:
      device = messaging.new_message('deviceState', valid=True, logMonoTime=now[0])
      device.deviceState.started, device.deviceState.startedMonoTime = started, 1
      messages.append(device.as_reader())
    daemon.sm.update_msgs(now[0] / 1e9, messages)

  device = messaging.new_message('deviceState', valid=True, logMonoTime=now[0] - 450_000_000)
  device.deviceState.started, device.deviceState.startedMonoTime = True, 1
  daemon.sm.update_msgs((now[0] - 450_000_000) / 1e9, [device.as_reader()])
  car_state = structs.CarState(gearShifter="drive", canValid=True)
  for tick in range(48):
    now[0] += 50_000_000
    publish(fault=tick == 1, update_device=tick % 10 == 0)
    daemon.sm.frame = tick * 5
    daemon.update_alerts(car_state)
  assert not daemon.enabled and daemon.AM.current_alert.alert_type == ALERT_TYPE
  publish(started=False, update_device=True)
  daemon.sm.frame += 1
  daemon.update_alerts(car_state)
  assert not daemon.enabled and daemon.AM.current_alert.alert_type != ALERT_TYPE
