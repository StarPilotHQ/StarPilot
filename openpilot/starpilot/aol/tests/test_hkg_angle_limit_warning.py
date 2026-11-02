from contextlib import ExitStack
from unittest.mock import patch

import pytest

from opendbc.car.structs import car
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.tests.test_stock_angle_aol import source_profile
from opendbc.car.hyundai.canfd_angle_aol import MODELS
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import Events, ET
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture

BASE = 10_000_000_000


class Inputs(dict):
  def __init__(self):
    super().__init__(carControl=car.CarControl(latActive=True))
    self.logMonoTime = {'carControl': BASE}
    self.seen = {'carControl': True}
    self.valid = {'carControl': True}
    self.alive = {'carControl': True}


def warning_state(monkeypatch):
  sd = SelfdriveD.__new__(SelfdriveD)
  sd.hkg_angle_steering = True
  sd.hkg_angle_limit_since_ns = sd.hkg_angle_limit_last_ns = 0
  sd.conditional_car_state_valid = True
  monkeypatch.setattr(sd, 'sm', Inputs(), raising=False)
  sd.events = Events()
  cs = car.CarState(canValid=True, gearShifter='drive')
  return sd, cs


def sample(sd, cs, elapsed, *, angle=200., control_age=0, state_age=0):
  now = BASE + elapsed
  sd.sm.logMonoTime['carControl'] = now - control_age
  sd.aol_car_state_log_ns = now - state_age
  sd.sm['carControl'].actuators.steeringAngleDeg = angle
  sd.events.clear()
  with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now):
    sd._update_hkg_angle_limit_warning(cs)
  return log.OnroadEvent.EventName.steerSaturated in sd.events.names


@pytest.mark.parametrize('speed', (0., 1., 4., 10.))
@pytest.mark.parametrize('sign', (-1, 1))
def test_sustained_request_warns_at_any_speed_and_in_both_directions(speed, sign, monkeypatch):
  sd, cs = warning_state(monkeypatch)
  cs.vEgo = cs.vEgoRaw = speed
  for elapsed in range(0, 200_000_000, 10_000_000):
    assert not sample(sd, cs, elapsed, angle=sign * 200.)
  assert not sample(sd, cs, 199_000_000, angle=sign * 200.)
  assert sample(sd, cs, 200_000_000, angle=sign * 200.)
  assert set(sd.events.event_rules[log.OnroadEvent.EventName.steerSaturated]) == {ET.WARNING}


@pytest.mark.parametrize('fault', ('threshold', 'inactive', 'driver', 'temporary_eps', 'permanent_eps', 'can', 'timeout',
  'invalid_source', 'invalid_control', 'unseen_control', 'dead_control', 'old_control', 'future_control', 'old_state', 'future_state', 'nan', 'inf'))
def test_invalid_or_withdrawn_input_resets_confirmation(fault, monkeypatch):
  sd, cs = warning_state(monkeypatch)
  for elapsed in range(0, 210_000_000, 10_000_000):
    sample(sd, cs, elapsed)
  assert log.OnroadEvent.EventName.steerSaturated in sd.events.names
  fields = {'driver': ('steeringPressed', True), 'temporary_eps': ('steerFaultTemporary', True),
            'permanent_eps': ('steerFaultPermanent', True), 'can': ('canValid', False), 'timeout': ('canTimeout', True)}
  if fault in fields:
    setattr(cs, *fields[fault])
  sd.sm['carControl'].latActive = fault != 'inactive'
  sd.conditional_car_state_valid = fault != 'invalid_source'
  sd.sm.valid['carControl'] = fault != 'invalid_control'
  sd.sm.seen['carControl'] = fault != 'unseen_control'
  sd.sm.alive['carControl'] = fault != 'dead_control'
  angles = {'threshold': 176., 'nan': float('nan'), 'inf': float('inf')}
  assert not sample(sd, cs, 210_000_000, angle=angles.get(fault, 200.),
    control_age=30_000_001 if fault == 'old_control' else -1 if fault == 'future_control' else 0,
    state_age=30_000_001 if fault == 'old_state' else -1 if fault == 'future_state' else 0)
  assert sd.hkg_angle_limit_since_ns == 0
  if fault in fields:
    setattr(cs, fields[fault][0], fault == 'can')
  sd.sm['carControl'].latActive = sd.conditional_car_state_valid = sd.sm.valid['carControl'] = True
  sd.sm.seen['carControl'] = sd.sm.alive['carControl'] = True
  assert not sample(sd, cs, 220_000_000)


@pytest.mark.parametrize('elapsed', (500_000_000, -10_000_000))
def test_gap_or_clock_reversal_cannot_complete_a_previous_interval(elapsed, monkeypatch):
  sd, cs = warning_state(monkeypatch)
  sample(sd, cs, 0)
  sample(sd, cs, 100_000_000)
  assert not sample(sd, cs, elapsed)


@pytest.mark.parametrize('identity', sorted(MODELS))
def test_actual_angle_profiles_enable_warning_without_changing_parameters(identity):
  with OpenpilotPrefix():
    cp = source_profile(CAR(identity))
    before = cp.to_dict()
    sd = SelfdriveD(CP=cp.as_reader())
    assert sd.hkg_angle_steering
    assert cp.to_dict() == before


@pytest.mark.parametrize('change', ('brand', 'torque', 'flags', 'not_car'))
def test_warning_does_not_enable_for_other_steering_configurations(change):
  with OpenpilotPrefix():
    cp = source_profile(CAR.HYUNDAI_IONIQ_5_PE)
    if change == 'brand':
      cp.brand = 'mock'
    elif change == 'torque':
      cp.steerControlType = 'torque'
    elif change == 'flags':
      cp.flags = 0
    else:
      cp.notCar = True
    assert not SelfdriveD(CP=cp.as_reader()).hkg_angle_steering


@pytest.mark.parametrize('full', (False, True))
def test_actual_low_speed_warning_preserves_engagement_and_clears_for_override(full, monkeypatch):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    cp = source_profile(CAR.HYUNDAI_IONIQ_5_PE)
    if full:
      from opendbc.car.hyundai.ioniq5pe_long_profile import candidate
      cp = candidate(cp, enabled=True, is_release=False)
      assert cp is not None
    sd, _, drive = case._authority_callers(full, cp=cp, genuine_events=True)
    original = sd.sm.update_msgs
    def requests(now, messages):
      updated = []
      for message in messages:
        if message.which() == 'carControl':
          message = message.as_builder()
          message.carControl.actuators.steeringAngleDeg = 200.
        updated.append(message)
      original(now, updated)
    monkeypatch.setattr(sd.sm, 'update_msgs', requests)
    for tick in range(53):
      cs = fixture.car_state()
      cs.vEgo = cs.vEgoRaw = 4.
      axis, state, command = drive(tick, observed_state=cs, steering=tick == 30, native_request_long=full)
      assert axis.lateralActive and command.latActive
      assert bool(axis.longitudinalActive) == bool(full) and bool(command.longActive) == bool(full)
      assert bool(state.enabled) == bool(full)
      if tick in (29, 52):
        assert log.OnroadEvent.EventName.steerSaturated in sd.events.names
        assert state.alertType == 'steerSaturated/warning'
      if tick == 30:
        assert log.OnroadEvent.EventName.steerSaturated not in sd.events.names
        assert state.alertType != 'steerSaturated/warning'


def test_existing_saturation_warning_is_not_duplicated(monkeypatch):
  sd, cs = warning_state(monkeypatch)
  for elapsed in range(0, 200_000_000, 10_000_000):
    sample(sd, cs, elapsed)
  sd.events.add(log.OnroadEvent.EventName.steerSaturated)
  with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=BASE + 200_000_000):
    sd._update_hkg_angle_limit_warning(cs)
  assert sd.events.names.count(log.OnroadEvent.EventName.steerSaturated) == 1


def test_angle_limit_warning_regressions_are_registered():
  from tools.ci.run_vehicle_tests import AOL_LIFECYCLE_TARGETS
  assert AOL_LIFECYCLE_TARGETS.count('openpilot/starpilot/aol/tests/test_hkg_angle_limit_warning.py') == 1
