"""Recorded 2024 Outback Wilderness identity, with discovery guards intact."""
import pytest

from opendbc.car import structs
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.subaru.values import CAR


def observed_firmware():
  # Real non-logging replies; no transmission/EPS reply was available.
  return [structs.CarParams.CarFw(ecu=ecu, address=address, fwVersion=bytes.fromhex(version), brand='subaru')
          for ecu, address, version in (
            (structs.CarParams.Ecu.abs, 0x7b0, 'a120241700'),
            (structs.CarParams.Ecu.engine, 0x7a2, 'fb2ca27007'),
            (structs.CarParams.Ecu.fwdCamera, 0x787, '09210804360000000000'),
          )]


def test_recorded_wilderness_automatically_matches_without_a_manual_override():
  exact, matches = match_fw_to_car(observed_firmware(), '0' * 17, log=False)
  assert exact
  assert matches == {CAR.SUBARU_OUTBACK_2023}


@pytest.mark.parametrize('ecu', (structs.CarParams.Ecu.abs, structs.CarParams.Ecu.engine, structs.CarParams.Ecu.fwdCamera))
@pytest.mark.parametrize('failure', ('missing', 'unknown', 'mixed'))
def test_recorded_wilderness_rejects_incomplete_or_contradictory_platform_ecus(ecu, failure):
  records = observed_firmware()
  target = next(record for record in records if record.ecu == ecu)
  if failure != 'mixed':
    records.remove(target)
  if failure != 'missing':
    records.append(structs.CarParams.CarFw(ecu=ecu, address=target.address, fwVersion=b'unknown firmware', brand='subaru'))
  _, matches = match_fw_to_car(records, '0' * 17, log=False)
  assert not matches


def test_recorded_wilderness_does_not_accept_the_diagnostic_camera_response():
  records = observed_firmware()
  records[-1].fwVersion = bytes.fromhex('20020e')
  _, matches = match_fw_to_car(records, '0' * 17, log=False)
  assert not matches
