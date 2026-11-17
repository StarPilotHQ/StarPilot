"""Manual-resume guidance follows the actual automatic-resume capability."""
import pytest
from opendbc.car import structs
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.car.car_events import CarEvents
from openpilot.starpilot.car.toyota.tests.test_rav4h_caller_native import firmware

EventName = log.OnroadEvent.EventName


def state(stopped=True, brake=False):
  cs = structs.CarState(canValid=True, standstill=stopped, brakePressed=brake, vEgo=0. if stopped else 10., gearShifter='drive')
  cs.cruiseState.enabled = cs.cruiseState.available = cs.cruiseState.standstill = True
  return cs


def command():
  cc = structs.CarControl(enabled=True, longActive=True)
  cc.cruiseControl.resume = True
  return cc


@pytest.mark.parametrize('candidate', (CAR.TOYOTA_RAV4H, CAR.TOYOTA_HIGHLANDER, CAR.TOYOTA_SIENNA))
@pytest.mark.parametrize('size', (4, 8))
@pytest.mark.parametrize('stopped', (False, True))
def test_automatic_toyota_does_not_request_manual_resume(candidate, size, stopped):
  cp = CarInterface.get_params(candidate, {0: {0x2FF: size}, 1: {}, 2: {}}, firmware(candidate), False, False, False)
  assert cp.autoResumeSng and cp.openpilotLongitudinalControl
  cs = state(stopped)
  assert EventName.resumeRequired not in CarEvents(cp).update(cs, cs, command()).names, 'AUTO_TOYOTA_FALSE_RESUME_ALERT'


@pytest.mark.parametrize('stopped,brake,expected', ((True, False, True), (False, False, False), (True, True, False)))
def test_manual_resume_profile_only_prompts_while_physically_stopped(stopped, brake, expected):
  cp = CarInterface.get_params(CAR.TOYOTA_PRIUS, {0: {0x2FF: 8}, 1: {}, 2: {}}, firmware(CAR.TOYOTA_PRIUS), False, False, False)
  assert cp.openpilotLongitudinalControl and not cp.autoResumeSng
  cs = state(stopped, brake)
  assert (EventName.resumeRequired in CarEvents(cp).update(cs, cs, command()).names) == expected, 'MOVING_TOYOTA_FALSE_RESUME_ALERT'


def test_automatic_resume_does_not_hide_control_faults():
  cp = CarInterface.get_params(CAR.TOYOTA_RAV4H, {0: {0x2FF: 4}, 1: {}, 2: {}}, firmware(CAR.TOYOTA_RAV4H), False, False, False)
  cs = state()
  cs.accFaulted = True
  cs.doorOpen = True
  automatic = set(CarEvents(cp).update(cs, cs, command()).names)
  cp.autoResumeSng = False
  manual = set(CarEvents(cp).update(cs, cs, command()).names)
  assert automatic - {EventName.resumeRequired} == manual - {EventName.resumeRequired}
  assert EventName.accFaulted in automatic and EventName.doorOpen in automatic
