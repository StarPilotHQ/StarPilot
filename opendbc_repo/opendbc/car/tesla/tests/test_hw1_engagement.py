import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.car.car_events import CarEvents
from openpilot.selfdrive.selfdrived.state import StateMachine

EventName = log.OnroadEvent.EventName


def params(alpha):
  return CarInterface.get_params(CAR.TESLA_MODEL_S_HW1, gen_empty_fingerprint(), [], alpha, False, False)


def state(enabled):
  cs = structs.CarState(canValid=True, vEgo=15., gearShifter=structs.CarState.GearShifter.drive)
  cs.cruiseState.available = True
  cs.cruiseState.enabled = enabled
  cs.cruiseState.speed = 20.
  return cs


@pytest.mark.parametrize('alpha', (False, True))
def test_cruise_stalk_enables_and_factory_cancel_disables(alpha):
  cp = params(alpha)
  owner = CarEvents(cp)
  machine = StateMachine()
  off, on = state(False), state(True)
  cc = structs.CarControl()
  events = owner.update(on, off, cc)
  assert EventName.pcmEnable in events.names
  machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.enabled
  assert EventName.pcmEnable not in owner.update(on, on, cc).names
  events = owner.update(off, on, cc)
  assert EventName.pcmDisable in events.names
  machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.disabled
  assert cp.openpilotLongitudinalControl is alpha
  assert cp.safetyConfigs[0].safetyParam == 16 + int(alpha)


@pytest.mark.parametrize('fault', ('seatbeltUnlatched','doorOpen','accFaulted','steerFaultPermanent','blockPcmEnable'))
def test_alpha_cruise_edge_cannot_bypass_vehicle_interlocks(fault):
  cp = params(True)
  owner = CarEvents(cp)
  machine = StateMachine()
  on = state(True)
  setattr(on, fault, True)
  events = owner.update(on, state(False), structs.CarControl())
  machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.disabled


def test_preap_and_modern_tesla_engagement_selection_is_unchanged():
  for candidate in (CAR.TESLA_MODEL_S_PREAP, CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y):
    for alpha in (False, True):
      cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], alpha, False, True)
      assert cp.pcmCruise
