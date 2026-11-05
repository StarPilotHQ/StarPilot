from pathlib import Path
from unittest.mock import patch

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.starpilot.car.toyota.tests.test_radar_preferences import FilesystemParams
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


def factory(alpha, fp=None, candidate=CAR.TOYOTA_RAV4_TSS2_2023):
  if fp is None:
    fp = gen_empty_fingerprint()
    fp[0][0x2AA] = 8
  return CarInterface.get_params(candidate, fp, [], alpha, False, False)


@pytest.mark.parametrize('alpha', [False, True])
def test_filter_preserves_radar_and_existing_tune(alpha):
  plain = factory(alpha, gen_empty_fingerprint())
  cp = factory(alpha)
  assert not cp.flags & ToyotaFlags.DISABLE_RADAR
  assert cp.flags & (1 << 18)
  assert cp.safetyConfigs[0].safetyParam == (0x1449 if alpha else 0x1649)
  expected = plain.to_dict()
  expected['flags'] = (expected['flags'] & ~int(ToyotaFlags.DISABLE_RADAR)) | (1 << 18) | int(ToyotaFlags.ARS510_RADAR)
  expected['radarUnavailable'] = False
  expected['safetyConfigs'][0]['safetyParam'] = cp.safetyConfigs[0].safetyParam
  assert cp.to_dict() == expected
  with patch('opendbc.car.toyota.interface.disable_ecu') as disable:
    CarInterface.init(cp, list, lambda _: None)
    CarInterface.deinit(cp, list, lambda _: None)
    disable.assert_not_called()


@pytest.mark.parametrize('fault', ['length', 'camera', 'radar_bus', 'mismatched_copy', 'pedal', 'sdsu', 'sibling'])
def test_filter_detection_rejects_wrong_topology(fault):
  fp = gen_empty_fingerprint()
  fp[0][0x2AA] = 8
  candidate = CAR.TOYOTA_RAV4_TSS2_2023
  if fault == 'length':
    fp[0][0x2AA] = 7
  elif fault in ('camera', 'radar_bus'):
    fp[2 if fault == 'camera' else 1][0x2AA] = fp[0].pop(0x2AA)
  elif fault == 'mismatched_copy':
    fp[2][0x2AA] = 7
  elif fault in ('pedal', 'sdsu'):
    fp[0][0x201 if fault == 'pedal' else 0x2FF] = 8
  else:
    candidate = CAR.TOYOTA_RAV4_TSS2
  cp = factory(True, fp, candidate)
  assert not cp.flags & (1 << 18)
  baseline = factory(True, gen_empty_fingerprint(), candidate)
  assert cp.to_dict() == baseline.to_dict()


@pytest.mark.parametrize('key,value', [('DisableOpenpilotLongitudinal', b'1'), ('SafeMode', b'1')])
def test_startup_off_selects_native_stock_owner(tmp_path, key, value):
  params = FilesystemParams(tmp_path)
  Path(params.get_param_path(key)).write_bytes(value)
  cp = factory(True)
  owner = VehicleStartupPreferences.read(params, enabled=True)
  owner.prepare(cp)
  owner.finalize(cp)
  assert not cp.openpilotLongitudinalControl
  assert cp.safetyConfigs[0].safetyParam == 0x1649
  assert not cp.flags & ToyotaFlags.DISABLE_RADAR


def test_intercepted_acc_is_not_a_required_parser_message():
  cp = factory(True)
  ci = CarInterface(cp)
  parsers = CarState.get_can_parsers(cp)
  ci.CS.update(parsers)
  # Signal accesses register CANParser requirements. PCS and PRE_COLLISION
  # remain observed, while the intercepted ACC message must stay absent.
  from opendbc.car import Bus
  assert 0x343 not in parsers[Bus.pt].vl
