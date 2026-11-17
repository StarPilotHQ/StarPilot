"""Driver gas override is neutral output, not a source-failure cruise cancel."""

from types import SimpleNamespace

import pytest
from opendbc.car import structs
from opendbc.car.toyota import dsu_bypass, pedal_composite, prius_longitudinal
from opendbc.car.toyota.carcontroller import CarController
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.tests.test_auto_hold import decode
from opendbc.car.toyota.values import CAR, DBC
from openpilot.starpilot.car.toyota.tests.test_dsu_bypass import factory as bypass_factory
from openpilot.starpilot.car.toyota.tests.test_pedal_parent_configurations import sources
from openpilot.starpilot.car.toyota.tests.test_prius_retrofit_advert_factory import factory as retrofit_factory
from openpilot.starpilot.car.toyota.tests.test_prius_sdsu_filter import factory as original_factory

PROFILES = ('prius-status', 'prius-advert', 'prius-retrofit-advert', 'bypass', 'composite')


def setup(profile, *, stock=False):
  if profile == 'composite':
    fp, fw = sources(CAR.TOYOTA_PRIUS, '2FF4', 'catalog')
    fp[0][0x201] = 6
    cp = CarInterface.get_params(CAR.TOYOTA_PRIUS, fp, fw, True, False, False)
    assert pedal_composite.configured(cp)
  elif profile == 'bypass':
    cp = bypass_factory(CAR.TOYOTA_PRIUS)
    assert dsu_bypass.configured(cp)
  elif profile == 'prius-retrofit-advert':
    cp = retrofit_factory(CAR.TOYOTA_PRIUS)
  else:
    cp = original_factory(hardware='2FF8' if profile == 'prius-status' else '2FF4')
  if stock:
    owner = pedal_composite if profile == 'composite' else dsu_bypass if profile == 'bypass' else prius_longitudinal
    assert owner.prepare_stock(cp)
  assert cp.openpilotLongitudinalControl == (not stock)
  controller = CarController(DBC[cp.carFingerprint], cp)
  ready = SimpleNamespace(update=lambda now: True)
  controller.prius_filter_input = controller.bypass_input = ready
  controller.pedal_input = controller.rav4h_filter_input = controller.advert_filter_input = ready
  state = CarState(cp)
  state.pedal_ready = True
  state.out = structs.CarState(canValid=True, gasPressed=True, vEgo=15., vEgoRaw=15.,
                              gearShifter=structs.CarState.GearShifter.drive)
  state.out.cruiseState.available = state.out.cruiseState.enabled = True
  state.out.cruiseState.speed = 20.
  command = structs.CarControl(enabled=True, latActive=True, longActive=False)
  command.actuators.accel = 1.
  return cp, controller, command, state


def acc_frames(cp, controller, command, state):
  frames = []
  for _ in range(6):
    frames.extend(controller.update(command.as_reader(), state, 2_000_000_000 + controller.frame * 10_000_000)[1])
  return [decode(cp, frame, 'ACC_CONTROL') for frame in frames if frame[0] == 0x343]


@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('long_requested', (False, True))
def test_gas_override_neutral_does_not_invent_cancel(profile, long_requested):
  cp, controller, command, state = setup(profile)
  command.longActive = long_requested
  frames = acc_frames(cp, controller, command, state)
  assert frames
  assert all(frame['ACCEL_CMD'] == 0 for frame in frames)
  assert all(not frame['CANCEL_REQ'] for frame in frames), 'GAS_OVERRIDE_INVENTED_CRUISE_CANCEL'
  assert not command.cruiseControl.cancel


@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('fault', ('cancel', 'brake', 'can-invalid', 'timeout', 'main-off', 'acc-fault',
                                   'aeb', 'eps-temporary', 'eps-permanent', 'door', 'belt', 'gear', 'source-expired'))
def test_gas_never_masks_source_or_physical_cancel(profile, fault):
  cp, controller, command, state = setup(profile)
  if fault == 'cancel':
    command.cruiseControl.cancel = True
  elif fault == 'source-expired':
    stale = SimpleNamespace(update=lambda now: False)
    controller.prius_filter_input = controller.bypass_input = stale
    controller.pedal_input = controller.rav4h_filter_input = controller.advert_filter_input = stale
  elif fault == 'main-off':
    state.out.cruiseState.available = False
  elif fault == 'gear':
    state.out.gearShifter = structs.CarState.GearShifter.park
  else:
    field = {'brake': 'brakePressed', 'can-invalid': 'canValid', 'timeout': 'canTimeout', 'acc-fault': 'accFaulted',
             'aeb': 'stockAeb', 'eps-temporary': 'steerFaultTemporary', 'eps-permanent': 'steerFaultPermanent',
             'door': 'doorOpen', 'belt': 'seatbeltUnlatched'}[fault]
    setattr(state.out, field, fault != 'can-invalid')
  frames = acc_frames(cp, controller, command, state)
  assert frames and all(frame['CANCEL_REQ'] for frame in frames)
  assert all(frame['ACCEL_CMD'] == 0 for frame in frames)


@pytest.mark.parametrize('profile', PROFILES)
def test_stock_gas_does_not_send_unrequested_cancel(profile):
  cp, controller, command, state = setup(profile, stock=True)
  assert not acc_frames(cp, controller, command, state)
  command.cruiseControl.cancel = True
  frames = acc_frames(cp, controller, command, state)
  assert frames and all(frame['CANCEL_REQ'] for frame in frames)
