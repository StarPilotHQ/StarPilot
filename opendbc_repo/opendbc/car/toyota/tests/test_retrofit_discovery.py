from unittest.mock import patch

import pytest

from opendbc.car import car_helpers, structs
from opendbc.car.toyota.values import CAR


def rack(**changes):
  values = {'ecu': structs.CarParams.Ecu.eps, 'fwVersion': b'8965B47070' + bytes(6),
                'address': 0x7A1, 'bus': 0, 'brand': 'toyota', 'subAddress': 0, 'logging': False}
  values.update(changes)
  return structs.CarParams.CarFw.new_message(**values)


def identify(fw, *, release=False, forced=None, adapter=None, source=None):
  # Native DSU commands are present on bus0 and mirrored on the camera bus.
  fp = {0: {0x343: 8, 0x4CB: 8, 0x1D2: 8, 0x1D3: 8, 0x262: 8}, 1: {},
        2: {0x343: 8, 0x4CB: 8}}
  if adapter is not None:
    fp[0][0x2FF] = adapter
  detected = (CAR.TOYOTA_PRIUS, fp, '00000000000000000', fw,
              structs.CarParams.FingerprintSource.fw if source is None else source, True)
  with patch.object(car_helpers, 'fingerprint', return_value=detected):
    return car_helpers.get_car(lambda wait_for_one=False: [], lambda _: None, lambda _: None,
                               False, release, forced_candidate=forced).CP


@pytest.mark.parametrize('release', [False, True])
@pytest.mark.parametrize('source', [structs.CarParams.FingerprintSource.fw, structs.CarParams.FingerprintSource.can])
def test_automatic_stock_retrofit_matches_explicit_profile(release, source):
  automatic = identify([rack()], release=release, source=source)
  explicit = identify([rack()], release=release, forced=CAR.TOYOTA_PRIUS_RETROFIT)
  assert automatic.carFingerprint == CAR.TOYOTA_PRIUS_RETROFIT, 'PRIUS_RETROFIT_AUTO_IDENTITY'
  assert not automatic.dashcamOnly
  assert automatic.safetyConfigs[0].safetyParam == 585
  assert not automatic.openpilotLongitudinalControl
  assert automatic.pcmCruise
  assert not automatic.alphaLongitudinalAvailable
  assert automatic.fingerprintSource == source
  a, b = automatic.to_dict(), explicit.to_dict()
  a.pop('fingerprintSource')
  b.pop('fingerprintSource')
  assert a == b


@pytest.mark.parametrize('release', [False, True])
@pytest.mark.parametrize('length', [4, 8, 7])
def test_automatic_retrofit_retains_existing_adapter_admission(release, length):
  automatic = identify([rack()], release=release, adapter=length)
  explicit = identify([rack()], release=release, adapter=length, forced=CAR.TOYOTA_PRIUS_RETROFIT)
  assert automatic.carFingerprint == CAR.TOYOTA_PRIUS_RETROFIT, 'PRIUS_RETROFIT_AUTO_IDENTITY'
  a, b = automatic.to_dict(), explicit.to_dict()
  a.pop('fingerprintSource')
  b.pop('fingerprintSource')
  assert a == b
  assert automatic.dashcamOnly == (length == 7)


@pytest.mark.parametrize('changes', [{'fwVersion': b'8965B47060' + bytes(6)}, {'bus': 1}, {'brand': 'honda'},
                                      {'address': 0x7A0}, {'subAddress': 1}, {'logging': True},
                                      {'fwVersion': b'8965B47070'}])
def test_unproven_rack_does_not_change_identity(changes):
  cp = identify([rack(**changes)])
  assert cp.carFingerprint == CAR.TOYOTA_PRIUS


def test_missing_or_conflicting_eps_does_not_change_identity():
  for fw in ([], [rack(), rack(fwVersion=b'8965B47060' + bytes(6))]):
    assert identify(fw).carFingerprint == CAR.TOYOTA_PRIUS


def test_manual_original_choice_is_preserved_and_stays_blocked():
  cp = identify([rack()], forced=CAR.TOYOTA_PRIUS)
  assert cp.carFingerprint == CAR.TOYOTA_PRIUS
  assert cp.dashcamOnly
  assert cp.fingerprintSource == structs.CarParams.FingerprintSource.fixed


def test_developer_fixed_fingerprint_is_preserved():
  cp = identify([rack()], source=structs.CarParams.FingerprintSource.fixed)
  assert cp.carFingerprint == CAR.TOYOTA_PRIUS
  assert cp.dashcamOnly
