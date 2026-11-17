"""A zero cruise engagement floor must retain stop-and-go authority."""
from contextlib import ExitStack

import pytest
from opendbc.car import structs
from opendbc.car.gm.tests.test_ordinary_camera import params
from opendbc.car.gm.values import CAR, GMFlags
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.car_events import CarEvents, EventName
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


def truck_params():
  cp = params(CAR.CHEVROLET_SILVERADO, alpha=True)
  cp.flags |= int(GMFlags.HAS_BSM)
  assert cp.minEnableSpeed == 0 and cp.openpilotLongitudinalControl and not cp.pcmCruise
  return cp


def stopped(speed):
  cs = fixture.car_state()
  cs.vEgo, cs.vEgoRaw, cs.standstill = speed, 0., True
  cs.cruiseState.available = cs.cruiseState.enabled = cs.cruiseState.standstill = True
  return cs


@pytest.mark.parametrize('speed', [-.04, -.00876117, -.0000004, 0., .02])
def test_zero_minimum_accepts_filtered_standstill_undershoot(speed):
  cp = truck_params()
  cs = stopped(speed)
  events = CarEvents(cp).update(cs, cs, structs.CarControl(enabled=True))
  assert EventName.belowEngageSpeed not in events.names
  assert EventName.resumeRequired in events.names


@pytest.mark.parametrize('minimum', [1.3888889, 10.72896])
@pytest.mark.parametrize('speed', [-.04, 0., .5, 1.])
def test_positive_cruise_minimum_remains_enforced(minimum, speed):
  cp = truck_params()
  cp.minEnableSpeed = minimum
  cs = stopped(speed)
  cs.standstill = speed <= 0.
  events = CarEvents(cp).update(cs, cs, structs.CarControl(enabled=True))
  assert EventName.belowEngageSpeed in events.names


@pytest.mark.parametrize('guard,expected', [('reverse', EventName.reverseGear), ('park', EventName.wrongGear),
                                          ('acc', EventName.accFaulted), ('brake', EventName.preEnableStandstill)])
def test_zero_minimum_preserves_physical_guards(guard, expected):
  cp = truck_params()
  cs, previous = stopped(-.008), stopped(0.)
  if guard in ('reverse', 'park'):
    cs.gearShifter = guard
  elif guard == 'acc':
    cs.accFaulted = True
  else:
    cs.brakePressed = True
  events = CarEvents(cp).update(cs, previous, structs.CarControl(enabled=True))
  assert expected in events.names


def test_combined_authority_survives_repeated_standstill_undershoot():
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, _, step = case._authority_callers(True, cp=truck_params(), genuine_events=True)
    sd.aol_settings = read_settings(sd.params)
    for tick, speed in enumerate((.02, 0., -.00876117, -.0001, 0., .002) * 4):
      axis, host, control = step(tick, observed_state=stopped(speed))
      assert host.enabled and host.active
      assert EventName.belowEngageSpeed not in sd.events.names
      assert axis.desiredLongitudinal and axis.longitudinalActive and axis.nativeAcknowledged
      assert control.longActive
    # The eligibility correction cannot bypass a native denial or driver brake.
    _, _, control = step(24, observed_state=stopped(0.), long_allowed=False)
    assert not control.longActive
    cs = stopped(0.)
    cs.brakePressed = True
    _, _, control = step(25, observed_state=cs)
    assert not control.longActive
