"""A zero cruise engagement floor must retain stop-and-go authority."""
from contextlib import ExitStack

import pytest
from opendbc.car import structs
from opendbc.car.gm.tests.test_ascm_intercept import params as radar_params
from opendbc.car.gm.tests.test_ordinary_camera import params
from opendbc.car.gm.values import CAR, GMFlags, ORDINARY_ASCM_CAR
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


@pytest.mark.parametrize(('candidate', 'layout'), [
  *((candidate, 'camera') for candidate in (CAR.CHEVROLET_TRAX, CAR.CHEVROLET_EQUINOX, CAR.CHEVROLET_TRAILBLAZER,
                                           CAR.CHEVROLET_BOLT_ACC_2022_2023, CAR.CHEVROLET_BOLT_EUV)),
  *((candidate, 'bypass') for candidate in (CAR.CHEVROLET_TRAX, CAR.CHEVROLET_EQUINOX, CAR.CHEVROLET_TRAILBLAZER)),
  *((candidate, 'radar') for candidate in sorted(ORDINARY_ASCM_CAR - {CAR.CADILLAC_ESCALADE_ESV_2019_ASCM})),
  (CAR.CHEVROLET_BLAZER, 'radar'),
  (CAR.BUICK_LACROSSE, 'radar'),
  (CAR.CHEVROLET_SUBURBAN, 'radar'),
])
@pytest.mark.parametrize('engaged', [False, True])
def test_gm_full_range_acc_uses_minimum_speed_only_for_entry(candidate, layout, engaged):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = (radar_params(candidate, sascm=True, radar=True, alpha=True) if layout == 'radar' else
          params(candidate, alpha=True, camera=layout != 'bypass'))
    assert cp.openpilotLongitudinalControl and cp.minEnableSpeed > 0.
    sd, _, step = case._authority_callers(engaged, cp=cp, genuine_events=True)
    sd.aol_settings = read_settings(sd.params)
    floor = cp.minEnableSpeed
    speeds = (floor + .06, floor - .03, floor + .04, floor - .05, .7, .2, 0.) if engaged else (floor - .05, .7)
    for tick, speed in enumerate(speeds):
      cs = stopped(speed)
      cs.vEgoRaw, cs.standstill, cs.cruiseState.standstill = speed, speed == 0., speed == 0.
      cs.buttonEnable = not engaged
      axis, host, control = step(tick, observed_state=cs)
      assert (EventName.belowEngageSpeed in sd.events.names) == (speed < cp.minEnableSpeed)
      assert host.enabled == axis.longitudinalActive == control.longActive == engaged
    if engaged:
      _, _, control = step(len(speeds), observed_state=stopped(0.), long_allowed=False)
      assert not control.longActive
      cs = stopped(0.)
      cs.brakePressed = True
      _, _, control = step(len(speeds) + 1, observed_state=cs)
      assert not control.longActive


@pytest.mark.parametrize(('candidate', 'pedal', 'show_alert'), [
  (CAR.CHEVROLET_BOLT_CC_2017, True, True),
  (CAR.CHEVROLET_BOLT_CC_2018_2021, True, True),
  (CAR.CHEVROLET_BOLT_CC_2017, False, False),
  (CAR.CHEVROLET_BOLT_CC_2022_2023, True, False),
  (CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, True, False),
])
def test_bolt_shift_to_low_is_a_display_only_button_alert(candidate, pedal, show_alert):
  from opendbc.car.gm.carstate import CarState
  from opendbc.car.gm.tests.test_bolt_pedal import params as bolt_params

  with OpenpilotPrefix(), ExitStack() as cleanup:
    cp = bolt_params(candidate, pedal=pedal)
    car_state = CarState(cp)
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, _, step = case._authority_callers(True, cp=cp, genuine_events=True)
    sd.aol_settings = read_settings(sd.params)
    message = 'Shift to L for speed control'
    for tick, (gear, button, pressed) in enumerate((
      ('drive', 'decelCruise', True),
      ('drive', 'decelCruise', False),
      ('low', 'decelCruise', False),
      ('drive', 'accelCruise', True),
      ('low', 'accelCruise', True),
    )):
      cs = fixture.car_state()
      cs.gearShifter = gear
      cs.vCruise = 65.
      cs.cruiseState.enabled = True
      cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=pressed)]
      cs.buttonEnable = car_state.update_button_enable(cs.buttonEvents)
      axis, host, control = step(tick, observed_state=cs)
      assert (host.alertText1 == message) == (show_alert and tick in (1, 3))
      if show_alert:
        assert axis.longitudinalActive and control.longActive
        assert axis.lateralActive and control.latActive
