from unittest.mock import patch

import pytest
from opendbc.car.can_definitions import CanData
from opendbc.car.hyundai.discovery import normal_bus_topology
from opendbc.car.car_helpers import fingerprint
from opendbc.car.vin import VIN_UNKNOWN

# Source: d90ba2e1ecdbb770/0000000e--ed1fea4874, clean 7da9cea0f6.
LAYOUT = {(0, 0xEA): 24, (0, 0xA0): 24, (0, 0x1A0): 32, (0, 0x100): 32,
          (0, 0x1AA): 16, (0, 0x40): 32, (0, 0x60): 32,
          **{(1, address): 32 for address in range(0x210, 0x220)}}


def frames():
  return [CanData(address, bytes(length), bus) for (bus, address), length in LAYOUT.items()]


def test_recorded_tucson_with_quiet_camera_keeps_normal_query_wiring():
  assert normal_bus_topology(lambda **kw: [frames()])


@pytest.mark.parametrize('index', range(len(LAYOUT)))
@pytest.mark.parametrize('defect', ['missing', 'length', 'bus', 'echo', 'contradiction'])
def test_quiet_camera_requires_complete_live_chassis_and_radar(index, defect):
  source = frames()
  frame = source.pop(index)
  if defect in ('length', 'contradiction'):
    source.append(CanData(frame.address, frame.dat[:-1], frame.src))
    if defect == 'contradiction':
      source.append(frame)
  elif defect != 'missing':
    source.append(CanData(frame.address, frame.dat, frame.src + (128 if defect == 'echo' else 1)))
  clock = iter([0., 0., .13])
  assert not normal_bus_topology(lambda **kw: [source], clock=lambda: next(clock))


def test_actual_query_keeps_normal_bus_but_never_assigns_vehicle_identity():
  switches = []
  with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)), \
       patch('opendbc.car.car_helpers.get_fw_versions', return_value=[]) as query, \
       patch('opendbc.car.car_helpers.get_present_ecus', return_value=set()) as generic, \
       patch('opendbc.car.car_helpers.match_fw_to_car', return_value=(True, set())), \
       patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
    actual = fingerprint(lambda wait_for_one=False: [frames()], lambda packets: None, switches.append, None)
  assert actual[0] is None
  assert switches and not any(switches)
  assert query.call_args.kwargs == {'query_brand': 'hyundai', 'normal_bus_only': True}
  generic.assert_not_called()
