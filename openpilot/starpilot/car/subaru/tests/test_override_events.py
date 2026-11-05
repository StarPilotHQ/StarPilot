import pytest
from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.subaru.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.car.car_events import CarEvents

EventName = log.OnroadEvent.EventName


@pytest.mark.parametrize('torque', [-151, -150, -94, -81, 81, 94, 150, 151])
def test_report_driver_torque_even_when_wheel_matches_requested_angle(torque):
  cp = interfaces[CAR.SUBARU_CROSSTREK_2025].get_non_essential_params(CAR.SUBARU_CROSSTREK_2025)
  owner = CarEvents(cp)
  cs = structs.CarState.new_message()
  cs.canValid = True
  cs.gearShifter = 'drive'
  cs.cruiseState.available = True
  cs.steeringTorque = torque
  cs.steeringPressed = True
  cs.steeringAngleDeg = .5
  cc = structs.CarControl.new_message()
  cc.latActive = True
  cc.actuators.steeringAngleDeg = cs.steeringAngleDeg
  events = owner.update(cs, structs.CarState.new_message(), cc)
  assert EventName.steerOverride in events.names
  assert cs.steeringPressed
  assert cs.steeringTorque == torque


@pytest.mark.parametrize('torque', [-80, 0, 80])
def test_no_override_below_original_sensor_threshold(torque):
  cp = interfaces[CAR.SUBARU_CROSSTREK_2025].get_non_essential_params(CAR.SUBARU_CROSSTREK_2025)
  cs = structs.CarState.new_message()
  cs.steeringTorque = torque
  cs.steeringPressed = False
  events = CarEvents(cp).create_common_events(cs, structs.CarState.new_message())
  assert EventName.steerOverride not in events.names


def test_permanent_steering_fault_is_not_masked_by_driver_torque():
  cp = interfaces[CAR.SUBARU_CROSSTREK_2025].get_non_essential_params(CAR.SUBARU_CROSSTREK_2025)
  cs = structs.CarState.new_message()
  cs.steeringTorque = 94
  cs.steeringPressed = True
  cs.steerFaultPermanent = True
  events = CarEvents(cp).create_common_events(cs, structs.CarState.new_message())
  assert EventName.steerOverride in events.names
  assert EventName.steerUnavailable in events.names
