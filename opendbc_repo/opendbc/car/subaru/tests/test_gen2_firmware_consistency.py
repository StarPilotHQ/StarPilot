"""Automatic Gen2 discovery requires complete, mutually consistent ECU evidence."""
import pytest

from opendbc.car import structs
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.subaru.fingerprints import FW_VERSIONS
from opendbc.car.subaru.values import CAR

CARS = (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025, CAR.SUBARU_LEGACY_2025)
REQUIRED = (structs.CarParams.Ecu.abs, structs.CarParams.Ecu.fwdCamera, structs.CarParams.Ecu.engine)
VIN = '00000000000000000'


def records(car, *, all_versions=False):
  return [structs.CarParams.CarFw(ecu=ecu, address=address, subAddress=sub or 0,
                                 fwVersion=version, brand='subaru')
          for (ecu, address, sub), versions in FW_VERSIONS[car].items()
          for version in (versions if all_versions else versions[:1])]


@pytest.mark.parametrize('car', CARS)
def test_complete_current_firmware_remains_exact_automatic(car):
  exact, matches = match_fw_to_car(records(car), VIN, log=False)
  assert exact and matches == {car}


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('missing_ecu', REQUIRED)
@pytest.mark.parametrize('allow_exact', (True, False), ids=('exact-enabled', 'fuzzy-only'))
def test_missing_platform_ecu_cannot_fall_back_to_partial_fuzzy(car, missing_ecu, allow_exact):
  live = [record for record in records(car) if record.ecu != missing_ecu]
  _, matches = match_fw_to_car(live, VIN, allow_exact=allow_exact, log=False)
  assert not (set(CARS) & matches)


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('ecu', REQUIRED)
@pytest.mark.parametrize('allow_exact', (True, False), ids=('exact-enabled', 'fuzzy-only'))
def test_foreign_version_appended_to_valid_ecu_rejects_mixed_observation(car, ecu, allow_exact):
  peer = CARS[1] if car == CARS[0] else CARS[0]
  live = records(car)
  target = next(record for record in live if record.ecu == ecu)
  foreign = next(record for record in records(peer) if record.ecu == ecu)
  foreign.fwVersion = next(version for version in FW_VERSIONS[peer][ecu, foreign.address, None]
                           if version not in FW_VERSIONS[car][ecu, target.address, None])
  assert foreign.address == target.address and foreign.fwVersion != target.fwVersion
  assert foreign.fwVersion not in FW_VERSIONS[car][ecu, target.address, None]
  live.append(foreign)
  _, matches = match_fw_to_car(live, VIN, allow_exact=allow_exact, log=False)
  assert not (set(CARS) & matches)


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('ecu', REQUIRED)
@pytest.mark.parametrize('allow_exact', (True, False), ids=('exact-enabled', 'fuzzy-only'))
def test_unknown_version_appended_to_valid_ecu_rejects_contradiction(car, ecu, allow_exact):
  live = records(car)
  target = next(record for record in live if record.ecu == ecu)
  live.append(structs.CarParams.CarFw(ecu=ecu, address=target.address,
                                    fwVersion=b'unknown firmware', brand='subaru'))
  _, matches = match_fw_to_car(live, VIN, allow_exact=allow_exact, log=False)
  assert not (set(CARS) & matches)


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('allow_exact', (True, False), ids=('exact-enabled', 'fuzzy-only'))
def test_multiple_known_versions_for_same_platform_are_not_globally_banned(car, allow_exact):
  exact, matches = match_fw_to_car(records(car, all_versions=True), VIN, allow_exact=allow_exact, log=False)
  assert exact == allow_exact and matches == {car}


@pytest.mark.parametrize('allow_exact', (True, False), ids=('exact-enabled', 'fuzzy-only'))
def test_gen2_missing_eps_query_rule_is_preserved(allow_exact):
  live = [record for record in records(CAR.SUBARU_LEGACY_2025)
          if record.ecu != structs.CarParams.Ecu.eps]
  exact, matches = match_fw_to_car(live, VIN, allow_exact=allow_exact, log=False)
  assert exact == allow_exact and matches == {CAR.SUBARU_LEGACY_2025}


def test_original_cross_abs_counterexample_rejected_at_production_selection_boundary():
  live = [record for record in records(CARS[0]) if record.ecu == structs.CarParams.Ecu.abs]
  live.extend(records(CARS[1]))
  _, matches = match_fw_to_car(live, VIN, log=False)
  assert not (set(CARS) & matches)


def test_subaru_validator_has_no_new_veto_for_existing_ascent():
  from opendbc.car.subaru.firmware_validation import validate_fw_match
  assert validate_fw_match(CAR.SUBARU_ASCENT_2023, {(0x7b0, None): {b'unknown firmware'}}, FW_VERSIONS)


@pytest.mark.parametrize('car', CARS)
def test_complete_current_firmware_fuzzy_only_stays_supported(car):
  exact, matches = match_fw_to_car(records(car), VIN, allow_exact=False, log=False)
  assert not exact and matches == {car}


@pytest.mark.parametrize('append', (False, True), ids=('unknown-eps-only', 'known-and-unknown-eps'))
@pytest.mark.parametrize('allow_exact', (True, False), ids=('exact-enabled', 'fuzzy-only'))
def test_optional_eps_when_present_must_not_contradict_candidate(append, allow_exact):
  live = records(CAR.SUBARU_LEGACY_2025)
  eps = next(record for record in live if record.ecu == structs.CarParams.Ecu.eps)
  if not append:
    live = [record for record in live if record.ecu != structs.CarParams.Ecu.eps]
  live.append(structs.CarParams.CarFw(ecu=eps.ecu, address=eps.address,
                                    fwVersion=b'unknown EPS firmware', brand='subaru'))
  _, matches = match_fw_to_car(live, VIN, allow_exact=allow_exact, log=False)
  assert not (set(CARS) & matches)


def test_other_subaru_firmware_variants_keep_existing_public_exact_matches():
  # All current non-scoped identities: the original404 and six restored Ascent
  # responses. Firmware data is unchanged by this validator-only repair.
  checked = 0
  for car, versions in FW_VERSIONS.items():
    if car in CARS:
      continue
    for (ecu, address, subaddress), alternatives in versions.items():
      for version in alternatives:
        live = records(car)
        target = next(record for record in live if record.ecu == ecu and record.address == address
                      and (record.subAddress or None) == subaddress)
        target.fwVersion = version
        exact, matches = match_fw_to_car(live, VIN, log=False)
        assert exact and car in matches, (car, ecu, address, version.hex(), matches)
        checked += 1
  assert checked >= 404
