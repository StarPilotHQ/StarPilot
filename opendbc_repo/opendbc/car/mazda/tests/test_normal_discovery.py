from unittest.mock import patch

import pytest

from opendbc.car.can_definitions import CanData
from opendbc.car.car_helpers import fingerprint
from opendbc.car.mazda.values import CAR
from opendbc.car.structs import CarParams
from opendbc.car.vin import VIN_UNKNOWN


def legacy_frames():
  return [CanData(address, bytes(8), bus) for bus, address in (
    (0, 0x09D), (0, 0x165), (0, 0x202), (0, 0x21C), (0, 0x240), (2, 0x243),
  )]


@pytest.mark.parametrize('layout', ['normal', 'missing_camera', 'camera_echo', 'wrong_dlc'])
@pytest.mark.parametrize('observed_engine', [True, False])
def test_legacy_mazda_discovery_selects_normal_wiring_and_requires_firmware(layout, observed_engine):
  frames = legacy_frames()
  if layout != 'normal':
    frame = frames.pop()
    if layout == 'camera_echo':
      frames.append(CanData(frame.address, frame.dat, frame.src + 128))
    if layout == 'wrong_dlc':
      frames.extend((frame, CanData(frame.address, frame.dat[:-1], frame.src)))

  firmware = [CarParams.CarFw(address=address, brand='mazda', fwVersion=version.ljust(24, b'\0')) for address, version in (
    (0x7E1, b'PYB2-21PS1-G'), (0x730, b'KSD5-3210X-C-00'),
  )]
  if observed_engine:
    firmware.append(CarParams.CarFw(address=0x7E0, brand='mazda', fwVersion=b'PX2H-188K2-E'.ljust(24, b'\0')))

  mux = []
  with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)) as vin, \
       patch('opendbc.car.car_helpers.get_fw_versions', return_value=firmware) as fw, \
       patch('opendbc.car.car_helpers.get_present_ecus', return_value=set()), \
       patch('opendbc.car.car_helpers.get_fw_versions_ordered', return_value=[]), \
       patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})), \
       patch('opendbc.car.car_helpers.time.sleep'):
    result = fingerprint(lambda **kwargs: [frames], lambda _: None, mux.append, None)

  if layout == 'normal':
    assert mux and not any(mux)
    assert vin.call_args.args[2] == (0,)
    assert fw.call_args.kwargs == {'query_brand': 'mazda', 'normal_bus_only': True}
    assert result[0] == (CAR.MAZDA_CX5 if observed_engine else None)
  else:
    fw.assert_not_called()
    assert result[0] is None
