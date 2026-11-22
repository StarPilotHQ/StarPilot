from unittest.mock import patch

import pytest

from opendbc.car.can_definitions import CanData
from opendbc.car.car_helpers import fingerprint
from opendbc.car.hyundai.discovery import normal_bus_topology
from opendbc.car.vin import VIN_UNKNOWN


def mixed_can_frames():
  return [CanData(addr, bytes(length), bus) for bus, addr, length in (
    (0, 0x251, 16), (2, 0x50, 16), (2, 0x2A4, 24),
    (1, 0x251, 8), (1, 0x386, 8), (1, 0x394, 8), (1, 0x4F1, 4), (1, 0x420, 8), (1, 0x421, 8),
  )]


@pytest.mark.parametrize('index', range(9))
@pytest.mark.parametrize('defect', ['missing', 'length', 'bus', 'echo', 'contradiction'])
def test_mixed_can_probe_requires_consistent_physical_sources(index, defect):
  frames = mixed_can_frames()
  original = frames.pop(index)
  if defect in ('length', 'contradiction'):
    frames.append(CanData(original.address, original.dat[:-1], original.src))
    if defect == 'contradiction':
      frames.append(original)
  elif defect in ('bus', 'echo'):
    frames.append(CanData(original.address, original.dat, original.src + (128 if defect == 'echo' else 1)))
  assert not normal_bus_topology(lambda **kwargs: [frames])


def test_mixed_can_probe_spans_can_reads():
  frames = iter([frame] for frame in mixed_can_frames())
  assert normal_bus_topology(lambda **kwargs: [next(frames, [])], clock=lambda: 0.)


def test_mixed_can_fingerprint_keeps_fresh_queries_on_normal_wiring():
  switches = []
  with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)) as vin, \
       patch('opendbc.car.car_helpers.get_fw_versions', return_value=[]) as query, \
       patch('opendbc.car.car_helpers.get_present_ecus', return_value=set()) as present, \
       patch('opendbc.car.car_helpers.get_fw_versions_ordered', return_value=[]) as generic, \
       patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
    result = fingerprint(lambda **kwargs: [mixed_can_frames()], lambda packets: None, switches.append, None)
  assert result[0] is None
  assert switches and not any(switches)
  vin.assert_called_once()
  assert vin.call_args.args[2] == (0, 1)
  query.assert_called_once()
  assert query.call_args.kwargs == {'query_brand': 'hyundai', 'normal_bus_only': True}
  present.assert_not_called()
  generic.assert_not_called()
