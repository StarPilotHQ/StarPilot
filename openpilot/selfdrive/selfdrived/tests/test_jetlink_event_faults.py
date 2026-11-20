"""Jetlink bookkeeping cannot waive selfdrived's native fault decisions."""
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.car.structs import car
from openpilot.cereal import log, messaging
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.selfdrive.selfdrived.tests.test_big_model_status import jetlink_drive


class Sources:
  def __init__(self):
    names = ('modelV2', 'deviceState', 'carControl', 'controlsState', 'driverMonitoringState',
             'peripheralState', 'extrinsicsCalibration', 'driverAssistance', 'vehicleParameters',
             'managerState', 'radarState', 'deviceMotion', 'longitudinalPlan', 'userBookmark',
             'lateralManeuverPlan', 'alertDebug', 'gpsLocationExternal')
    self.data = {name: getattr(messaging.new_message(name), name) for name in names}
    self.data['pandaStates'] = messaging.new_message('pandaStates', 0).pandaStates
    self.frame = 2000
    self.seen = self.alive = dict.fromkeys(self.data, True)
    self.valid = dict.fromkeys(self.data, True)
    self.freq_ok = dict.fromkeys(self.data, True)
    self.updated = dict.fromkeys(self.data, False)
    self.recv_frame = dict.fromkeys(self.data, self.frame)
    self.recv_frame['alertDebug'] = self.recv_frame['lateralManeuverPlan'] = 0
    self.logMonoTime = dict.fromkeys(self.data, 100_000_000_000)
    self.services = list(self.data)
    self['controlsState'].lateralControlState.init('pidState')
    self['extrinsicsCalibration'].calStatus = log.ExtrinsicsCalibration.Status.calibrated
    self['deviceState'].freeSpacePercent = 100
    self['deviceMotion'].posenetOK = self['deviceMotion'].inputsOK = True
    self['vehicleParameters'].valid = True

  def __getitem__(self, name):
    return self.data[name]

  def all_alive(self, services=None):
    return all(self.alive[name] for name in self.data if services is None or name in services)

  def all_freq_ok(self, services=None):
    return all(self.freq_ok[name] for name in self.data if services is None or name in services)

  def all_checks(self, services=None):
    return self.all_alive(services) and self.all_freq_ok(services) and all(
      self.valid[name] for name in self.data if services is None or name in services)


def events_drive():
  drive = jetlink_drive()
  drive.CP = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
  drive.sm = Sources()
  drive.enabled = drive.active = drive.initialized = True
  drive.aol_replay = drive.axis_transport_required = False
  drive.big_model_loading = False
  drive.startup_event = drive.calibrated_pose = None
  drive.dm_lockout_set = drive.dm_uncertain_alerted = drive.recalibrating_seen = False
  drive.is_ldw_enabled = drive.excessive_actuation = False
  drive.mismatch_counter = drive.cruise_mismatch_counter = drive.last_steering_pressed_frame = 0
  drive.logged_comm_issue = drive.not_running_prev = None
  drive.rk = NS(lagging=False)
  drive.camera_packets = drive.sensor_packets = []
  drive.gps_location_service = 'gpsLocationExternal'
  drive.distance_traveled = 0.
  drive.tesla_stock_consumer = NS(binding=None)
  drive.params = NS(get=lambda key: None, get_bool=lambda key: False)
  # This test isolates the real generic fault stage from vehicle button owners.
  drive._update_car_event_sample = lambda sample, previous: None
  drive._update_steering_authority = lambda sample: None
  cs = car.CarState.new_message(canValid=True)
  drive.CS_prev = cs
  drive.jetlink_hooks.runtime = lambda: NS(state='running', active=True)
  drive.sm['modelV2'].big = True
  return drive, cs


@pytest.mark.parametrize('fault,event', [('comm', EventName.commIssue),
  ('posenet', EventName.posenetInvalid), ('localizer', EventName.locationdTemporaryError),
  ('can', EventName.canError), ('radar', EventName.radarFault), ('process', EventName.processNotRunning)])
def test_real_selfdrived_faults_survive_jetlink_swap(fault, event):
  drive, cs = events_drive()
  with patch('openpilot.selfdrive.selfdrived.helpers.time.monotonic', return_value=100.):
    drive.update_events(cs)
    assert drive.jetlink_settling
    assert drive.big_model_ready_t == 0.
    if fault == 'comm':
      drive.sm.valid['controlsState'] = False
    elif fault == 'posenet':
      drive.sm['deviceMotion'].posenetOK = False
    elif fault == 'localizer':
      drive.sm['deviceMotion'].inputsOK = False
    elif fault == 'can':
      cs.canValid = False
    elif fault == 'radar':
      drive.sm['radarState'].radarErrors.canError = True
    else:
      drive.sm['managerState'].processes = [{'name': 'modeld', 'running': False, 'shouldBeRunning': True}]
    drive.update_events(cs)
  assert event in drive.events.names
  state = StateMachine()
  state.state = log.SelfdriveState.OpenpilotState.enabled
  state.update(drive.events)
  assert state.state != log.SelfdriveState.OpenpilotState.enabled


def test_known_healthy_jetlink_fallback_keeps_native_state_enabled():
  drive, cs = events_drive()
  with patch('openpilot.selfdrive.selfdrived.helpers.time.monotonic', return_value=100.):
    drive.update_events(cs)
    drive.sm['modelV2'].big = False
    drive.jetlink_hooks.runtime = lambda: NS(state='retrying', active=False)
    drive._jetlink_runtime_ticks = 0
    drive.update_events(cs)
  assert not drive.big_model_failed
  assert EventName.bigModelFailed not in drive.events.names
  assert drive.big_model_ready_t == 0.
  state = StateMachine()
  state.state = log.SelfdriveState.OpenpilotState.enabled
  state.update(drive.events)
  assert state.state == log.SelfdriveState.OpenpilotState.enabled
