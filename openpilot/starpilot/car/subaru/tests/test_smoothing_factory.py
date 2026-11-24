"""Actual Card snapshot publication and Controls consumption for every admitted Subaru."""
import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.subaru.fingerprints import FW_VERSIONS
from opendbc.car.subaru.stock_aol import TORQUE_CARS, ANGLE_CARS
from opendbc.car.subaru.values import CAR
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.car.subaru.smoothing import MARKER, read_frozen
from openpilot.starpilot.car.subaru.tests.caller_helpers import actual_factory, firmware_records


@pytest.mark.parametrize('candidate', sorted(TORQUE_CARS | ANGLE_CARS))
@pytest.mark.parametrize('manual', [False, True])
@pytest.mark.parametrize('aol', [False, True])
def test_actual_factory_frozen_default(candidate, manual, aol):
  shared_early_fw = candidate == CAR.SUBARU_CROSSTREK_2018
  fw_candidate = CAR.SUBARU_IMPREZA if shared_early_fw else candidate
  vin = 'JF2GTAAC0JH000001' if shared_early_fw else '0' * 17
  fw = firmware_records(fw_candidate, FW_VERSIONS, 'subaru')
  with actual_factory(candidate, gen_empty_fingerprint(), fw, vin, manual=manual, aol=aol, firmware_candidate=fw_candidate) as (card, params, _):
    assert card.CP.flags & MARKER
    expected = .4 if candidate == CAR.SUBARU_OUTBACK_2023 else 0.
    assert read_frozen(card.CP, params) == expected
    assert Controls().subaru_smoothing == expected
    assert not card.CP.openpilotLongitudinalControl and not card.CP.alphaLongitudinalAvailable


@pytest.mark.parametrize('value', [0., .4, 2.])
def test_actual_saved_manual_choice_is_frozen(value):
  candidate = CAR.SUBARU_OUTBACK_2023
  preferences = {'DeveloperUI': True, 'LatSmoothSeconds': value}
  fw = firmware_records(candidate, FW_VERSIONS, 'subaru')
  with actual_factory(candidate, gen_empty_fingerprint(), fw, '0' * 17, manual=False, aol=True,
                      preferences=preferences) as (card, params, _):
    params.put('LatSmoothSeconds', 1.5, block=True)
    assert Controls().subaru_smoothing == read_frozen(card.CP, params) == float(value)



def test_smoothing_snapshot_is_keyword_only():
  import inspect
  from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
  assert inspect.signature(VehicleStartupPreferences).parameters['subaru_smoothing'].kind == inspect.Parameter.KEYWORD_ONLY
