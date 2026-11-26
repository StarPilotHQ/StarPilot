from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from openpilot.cereal import log
from openpilot.selfdrive.car.car_events import CarEvents
from openpilot.selfdrive.selfdrived.events import ET

EventName = log.OnroadEvent.EventName
ButtonType = structs.CarState.ButtonEvent.Type


def state(enabled, available, brake=False, cancel=None):
  cs = structs.CarState(vEgo=14., gearShifter=structs.CarState.GearShifter.drive, brakePressed=brake)
  cs.cruiseState.enabled, cs.cruiseState.available = enabled, available
  if cancel is not None:
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=ButtonType.cancel, pressed=cancel)]
  return cs


def drop(events: CarEvents, cs):
  return events.update(cs, state(True, True), None)


def car_events():
  return CarEvents(interfaces['LEAPMOTOR_B10'].get_non_essential_params('LEAPMOTOR_B10'))


def test_car_cancel_is_a_takeover_alert():
  # the passenger unbuckling on route 00000018: cruise off and unavailable, no brake, no cancel push
  events = drop(car_events(), state(False, False))
  assert events.contains(ET.IMMEDIATE_DISABLE)
  assert EventName.cruiseDisabled in events.names
  assert not events.contains(ET.USER_DISABLE)
  alert = events.create_alerts([ET.IMMEDIATE_DISABLE])[0]
  assert (alert.alert_text_1, alert.alert_text_2) == ("TAKE CONTROL IMMEDIATELY", "Car Cancelled Cruise")


def test_driver_cancels_stay_quiet():
  # braking, or cruise still available (the driver's ordinary cancel), is the usual disengage
  for cs in (state(False, False, brake=True), state(False, True)):
    events = drop(car_events(), cs)
    assert EventName.pcmDisable in events.names
    assert not events.contains(ET.IMMEDIATE_DISABLE)
  # the stalk pushed up: cruise goes off up to 0.25 s after the press
  ev = car_events()
  ev.update(state(True, True, cancel=True), state(True, True), None)
  for _ in range(25):
    ev.update(state(True, True), state(True, True), None)
  events = drop(ev, state(False, False))
  assert not events.contains(ET.IMMEDIATE_DISABLE)
  # ...but a car cancel long after a push is still announced
  for _ in range(60):
    ev.update(state(True, True), state(True, True), None)
  assert drop(ev, state(False, False)).contains(ET.IMMEDIATE_DISABLE)
