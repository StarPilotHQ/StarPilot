"""Comma can set speed on Tesla dashboard: which cars can have it, and how the startup choice lands in CarParams."""
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, get_safety_config, structs
from opendbc.car.tesla.carcontroller import CarController
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.speed_sync import MODELS, SYNC_FLAG, SpeedSynchronizer, eligible, prepare, selected
from opendbc.car.tesla.values import CAR, CANBUS, DBC, TeslaFlags, TeslaSafetyFlags

MODERN = (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y)


def tesla(candidate=CAR.TESLA_MODEL_3, *, cable=True, long=True):
  fingerprint = gen_empty_fingerprint()
  fingerprint[CANBUS.autopilot_party][0x489] = 8
  if cable:
    fingerprint[CANBUS.vehicle][0x3DF] = 8
  return CarInterface.get_params(candidate, fingerprint, [], long, False, False)


def word(cp):
  return int(cp.safetyConfigs[0].safetyParam)


def raw(cp):
  """The whole CarParams as bytes, from a copy so the builder can be written again."""
  return cp.as_reader().as_builder().to_bytes()


def two_configs(cp):
  cp.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.tesla, 1 | SYNC_FLAG),
                      get_safety_config(structs.CarParams.SafetyModel.tesla, 1 | SYNC_FLAG)]


def no_configs(cp):
  cp.safetyConfigs = []


# Each one alone takes the feature away from an otherwise eligible Model 3.
MISSING = {
  'other brand': lambda cp: setattr(cp, 'brand', 'hyundai'),
  'model x': lambda cp: setattr(cp, 'carFingerprint', str(CAR.TESLA_MODEL_X)),
  'model s hw1': lambda cp: setattr(cp, 'carFingerprint', str(CAR.TESLA_MODEL_S_HW1)),
  'model s pre-ap': lambda cp: setattr(cp, 'carFingerprint', str(CAR.TESLA_MODEL_S_PREAP)),
  'unknown fingerprint': lambda cp: setattr(cp, 'carFingerprint', 'MOCK'),
  'stock longitudinal': lambda cp: setattr(cp, 'openpilotLongitudinalControl', False),
  'no car cruise': lambda cp: setattr(cp, 'pcmCruise', False),
  'passive': lambda cp: setattr(cp, 'passive', True),
  'dashcam': lambda cp: setattr(cp, 'dashcamOnly', True),
  'not a car': lambda cp: setattr(cp, 'notCar', True),
  'no cable': lambda cp: setattr(cp, 'flags', int(cp.flags) & ~int(TeslaFlags.HAS_VEHICLE_BUS)),
  'no safety config': no_configs,
  'two safety configs': two_configs,
  'safety word without longitudinal': lambda cp: setattr(cp.safetyConfigs[0], 'safetyParam', word(cp) & ~1),
}


def test_the_flag_is_its_own_bit():
  assert SYNC_FLAG == 4096 == int(TeslaSafetyFlags.SCROLL_SPEED_SYNC)
  others = [flag for flag in TeslaSafetyFlags if flag is not TeslaSafetyFlags.SCROLL_SPEED_SYNC]
  assert not any(SYNC_FLAG & int(flag) for flag in others)
  assert not SYNC_FLAG & 2048, 'ordinary Always On Lateral keeps 2048'
  assert MODELS == tuple(str(model) for model in MODERN)


@pytest.mark.parametrize('candidate', MODERN)
def test_model_3_and_y_with_cable_and_longitudinal_are_eligible_and_start_unselected(candidate):
  cp = tesla(candidate)
  assert eligible(cp)
  assert word(cp) == 1 and not selected(cp), 'the car interface never selects it; startup preferences do'


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('cable,long', [(False, True), (True, False), (False, False)])
def test_cable_and_longitudinal_are_both_needed(candidate, cable, long):
  cp = tesla(candidate, cable=cable, long=long)
  before = raw(cp)
  assert not eligible(cp) and not selected(cp)
  assert prepare(cp, True) is cp and raw(cp) == before


@pytest.mark.parametrize('reason', MISSING)
def test_every_condition_is_required(reason):
  cp = tesla()
  prepare(cp, True)
  assert selected(cp)
  MISSING[reason](cp)
  assert not eligible(cp), reason
  assert not selected(cp), 'a set bit alone selects nothing'


@pytest.mark.parametrize('reason', MISSING)
@pytest.mark.parametrize('requested', (False, True))
def test_prepare_never_touches_a_car_that_is_not_eligible(reason, requested):
  cp = tesla()
  if reason not in ('no safety config', 'two safety configs'):
    cp.safetyConfigs[0].safetyParam = word(cp) | SYNC_FLAG  # even a stale bit is left for the car's own code
  MISSING[reason](cp)
  before = raw(cp)
  assert prepare(cp, requested) is cp
  assert raw(cp) == before


@pytest.mark.parametrize('candidate', MODERN)
@pytest.mark.parametrize('base', (1, 257, 513, 769, 1537, 1793, 2049, 2305, 3073, 3329))
def test_prepare_sets_and_clears_only_its_own_bit(candidate, base):
  cp = tesla(candidate)
  cp.safetyConfigs[0].safetyParam = base
  rest = raw(cp)
  for _ in range(2):
    assert prepare(cp, True) is cp
    assert word(cp) == base | SYNC_FLAG and selected(cp)
  for _ in range(2):
    assert prepare(cp, False) is cp
    assert word(cp) == base and not selected(cp) and eligible(cp)
  assert raw(cp) == rest, 'nothing else in CarParams moved'


def test_prepare_clears_a_stale_selection():
  cp = tesla()
  cp.safetyConfigs[0].safetyParam = 4097
  prepare(cp, False)
  assert word(cp) == 1 and not selected(cp)


@pytest.mark.parametrize('cp', [None, object(), SimpleNamespace(), SimpleNamespace(brand='tesla'),
                                SimpleNamespace(brand='tesla', carFingerprint='TESLA_MODEL_3', openpilotLongitudinalControl=True,
                                                pcmCruise=True, passive=False, dashcamOnly=False, notCar=False, flags=None,
                                                safetyConfigs=[])])
def test_missing_or_malformed_car_parameters_are_not_eligible(cp):
  assert eligible(cp) is False and selected(cp) is False
  assert prepare(cp, True) is cp


def test_a_reader_is_judged_like_its_builder():
  cp = tesla()
  assert eligible(cp.as_reader()) and not selected(cp.as_reader())
  prepare(cp, True)
  assert selected(cp.as_reader())


@pytest.mark.parametrize('candidate', MODERN)
def test_the_controller_builds_a_synchronizer_only_when_selected(candidate):
  cp = tesla(candidate)
  assert CarController(DBC[candidate], cp).speed_sync is None
  prepare(cp, True)
  assert isinstance(CarController(DBC[candidate], cp).speed_sync, SpeedSynchronizer)
  prepare(cp, False)
  assert CarController(DBC[candidate], cp).speed_sync is None


@pytest.mark.parametrize('candidate', (CAR.TESLA_MODEL_X, CAR.TESLA_MODEL_S_HW1, CAR.TESLA_MODEL_S_PREAP))
def test_other_teslas_never_get_a_synchronizer(candidate):
  cp = tesla(candidate)
  before = raw(cp)
  assert not eligible(cp) and not selected(cp)
  prepare(cp, True)
  assert raw(cp) == before
  assert CarController(DBC[candidate], cp).speed_sync is None
