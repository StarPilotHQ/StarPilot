import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opendbc.car import car_helpers, structs
from opendbc.car.can_definitions import CanData
from opendbc.car.gm.values import CAR

VIN = '1G1RD6S50JU000001'
RECORDED = Path(__file__).parent / 'testdata/volt_2018_ascm_fingerprint.json'


@pytest.fixture
def fingerprint():
  return {int(bus): {int(address): size for address, size in messages.items()}
          for bus, messages in json.loads(RECORDED.read_text()).items()}


def receive(fingerprint):
  packets = [CanData(address, bytes(size), bus)
             for bus, messages in fingerprint.items() for address, size in messages.items()]
  return lambda **kwargs: [packets]


def test_recorded_startup_selects_ascm_and_preserves_manual_override(fingerprint):
  fw = structs.CarParams.CarFw.new_message(brand='gm', logging=True)
  cache = SimpleNamespace(carFingerprint='MOCK', carVin=VIN, carFw=[fw], brand='mock')
  with patch.dict(car_helpers.os.environ, {'FINGERPRINT': '', 'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': ''}), \
       patch.object(car_helpers, 'get_vin', return_value=(0x64B, 0, VIN)), \
       patch.object(car_helpers, 'get_fw_versions', return_value=[fw]):
    args = (receive(fingerprint), lambda _: None, lambda _: None, False, True)
    auto = car_helpers.get_car(*args, cached_params=cache).CP
    manual = car_helpers.get_car(*args, forced_candidate=CAR.CHEVROLET_VOLT).CP
  assert auto.carFingerprint == CAR.CHEVROLET_VOLT_ASCM
  assert auto.fingerprintSource == structs.CarParams.FingerprintSource.can
  assert auto.pcmCruise and not auto.openpilotLongitudinalControl and not auto.dashcamOnly
  assert auto.safetyConfigs[0].safetyParam == 0xE05
  assert manual.carFingerprint == CAR.CHEVROLET_VOLT
  assert manual.fingerprintSource == structs.CarParams.FingerprintSource.fixed


@pytest.mark.parametrize('vin', ['0' * 17, '1G1RD6S50KU000001', '1G1FX6S50JU000001'])
def test_layout_alone_does_not_match(fingerprint, vin):
  assert car_helpers.can_fingerprint(receive(fingerprint), vin=vin)[0] is None


@pytest.mark.parametrize('bus,address,size', [(0, 0x2FF, 8), (0, 0x201, 6), (0, 0x320, 6),
                                             (2, 0x180, None), (1, 0x474, None)])
def test_other_harness_or_control_sources_do_not_match(fingerprint, bus, address, size):
  for target in (0, 2) if bus == 0 else (bus,):
    if size is None:
      fingerprint[target].pop(address)
    else:
      fingerprint[target][address] = size
  assert car_helpers.can_fingerprint(receive(fingerprint), vin=VIN)[0] != CAR.CHEVROLET_VOLT_ASCM
