import pytest
from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.subaru.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.car.car_events import CarEvents

EventName = log.OnroadEvent.EventName


def event_owner(candidate=CAR.SUBARU_CROSSTREK_2025, angle=True):
  cp = interfaces[candidate].get_non_essential_params(candidate)
  if not angle:
    cp.steerControlType = structs.CarParams.SteerControlType.torque
  return CarEvents(cp)


def sample(owner, torque, **fields):
  cs = structs.CarState.new_message()
  cs.gearShifter = structs.CarState.GearShifter.drive
  cs.cruiseState.available = True
  cs.canValid = True
  cs.steeringTorque = torque
  cs.steeringPressed = abs(torque) > 80
  for key, value in fields.items():
    setattr(cs, key, value)
  events = owner.create_common_events(cs, structs.CarState.new_message())
  return cs, events


@pytest.mark.parametrize('direction', [-1, 1])
def test_light_wheel_interaction_is_not_an_override(direction):
  owner = event_owner()
  for torque in (81, 94, 100, 149, 150):
    cs, events = sample(owner, direction * torque)
    assert cs.steeringPressed
    assert cs.steeringTorque == direction * torque
    assert EventName.steerOverride not in events.names


@pytest.mark.parametrize('direction', [-1, 1])
def test_meaningful_override_is_immediate_and_retained_until_release(direction):
  owner = event_owner()
  for torque, expected in ((150, False), (151, True), (140, True), (101, True), (100, True),
                           (99, False), (140, False), (180, True), (0, False)):
    cs, events = sample(owner, direction * torque)
    assert (EventName.steerOverride in events.names) is expected
    assert cs.steeringPressed == (torque > 80)


@pytest.mark.parametrize('candidate', [CAR.SUBARU_FORESTER_2022, CAR.SUBARU_OUTBACK_2023,
                                      CAR.SUBARU_ASCENT_2023, CAR.SUBARU_IMPREZA])
def test_neighboring_subaru_profiles_retain_existing_detection(candidate):
  owner = event_owner(candidate)
  for torque, expected in ((0, False), (80, False), (81, True), (150, True), (-81, True)):
    _, events = sample(owner, torque)
    assert (EventName.steerOverride in events.names) is expected


def test_nonangle_configuration_keeps_existing_detection():
  _, events = sample(event_owner(angle=False), 94)
  assert EventName.steerOverride in events.names


def test_other_make_keeps_existing_detection():
  from opendbc.car.hyundai.values import CAR as HKG
  owner = event_owner(HKG.HYUNDAI_IONIQ_6)
  _, events = sample(owner, 94)
  assert EventName.steerOverride in events.names


@pytest.mark.parametrize('field,event', [('steerFaultPermanent', EventName.steerUnavailable),
                                        ('doorOpen', EventName.doorOpen),
                                        ('seatbeltUnlatched', EventName.seatbeltNotLatched)])
def test_fault_and_disengagement_events_are_preserved(field, event):
  cs, events = sample(event_owner(), 94, **{field: True})
  assert cs.steeringPressed
  assert event in events.names
  assert EventName.steerOverride not in events.names


@pytest.mark.parametrize('direction', [-1, 1])
def test_existing_controller_yield_and_settled_handoff_are_unchanged(direction):
  from types import SimpleNamespace
  from opendbc.car.subaru.carcontroller import CarController
  owner = event_owner()
  controller = CarController.__new__(CarController)
  controller.CP = owner.CP
  controller.angle_driver_override = False
  controller.angle_override_frames = 0
  controller.angle_handoff_active = False
  controller.angle_was_active = True
  for torque, event_override, active in ((94, False, True), (151, True, True), (151, True, False),
                                        (140, True, False), (100, True, False), (99, False, False),
                                        (99, False, True)):
    cs, events = sample(owner, direction * torque)
    assert (EventName.steerOverride in events.names) is event_override
    assert controller.angle_active(SimpleNamespace(out=cs), True) is active


@pytest.mark.parametrize('direction', [-1, 1])
def test_light_wheel_load_does_not_hide_temporary_eps_warning(direction):
  owner = event_owner()
  cs, events = sample(owner, direction * 94, steerFaultTemporary=True)
  assert cs.steeringPressed
  assert EventName.steerOverride not in events.names
  assert any(name in events.names for name in (EventName.steerTempUnavailable, EventName.steerTempUnavailableSilent))
