"""Recorded HDA1 traffic selects diagnostics on normal harness wiring only."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from opendbc.car.can_definitions import CanData
from opendbc.car.car_helpers import fingerprint
from opendbc.car.hyundai.discovery import CLASSIC_NORMAL_BUS_LAYOUT, NORMAL_BUS_LAYOUT, normal_bus_topology
from opendbc.car.vin import VIN_UNKNOWN

CAPTURES = json.loads((Path(__file__).parent / 'testdata/hda1_discovery.json').read_text())['captures']


def frames(capture):
  return [CanData(f['address'], bytes.fromhex(f['data']), f['bus']) for f in CAPTURES[capture]['frames']]


@pytest.mark.parametrize('capture', [0, 1])
def test_recorded_hda1_layout_selects_normal_wiring(capture):
  assert normal_bus_topology(lambda **kw: [frames(capture)]), 'HDA1_NORMAL_QUERY_LAYOUT_MISSING'


@pytest.mark.parametrize('capture', [0, 1])
@pytest.mark.parametrize('index', range(8))
@pytest.mark.parametrize('condition', ['missing', 'length', 'bus', 'echo', 'contradiction'])
def test_partial_malformed_or_echoed_hda1_cannot_select_query_family(capture, index, condition):
  source = frames(capture)
  original = source[index]
  if condition == 'missing':
    source.pop(index)
  elif condition in ('length', 'contradiction'):
    bad = CanData(original.address, original.dat[:-1], original.src)
    if condition == 'length':
      source[index] = bad
    else:
      source.insert(0, bad)
  else:
    source[index] = CanData(original.address, original.dat, original.src + (128 if condition == 'echo' else 1))
  assert not normal_bus_topology(lambda **kw: [source])


@pytest.mark.parametrize('layout', [NORMAL_BUS_LAYOUT, CLASSIC_NORMAL_BUS_LAYOUT])
def test_unrelated_hda1_length_does_not_veto_an_existing_valid_family(layout):
  source = [CanData(address, bytes(length), bus) for (bus, address), length in layout.items()]
  source.append(CanData(0x130, bytes(8), 0))
  assert normal_bus_topology(lambda **kw: [source])


@pytest.mark.parametrize('capture', [0, 1])
def test_actual_fingerprint_queries_fresh_firmware_without_obd_mux(capture):
  switches = []
  with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)) as vin, \
       patch('opendbc.car.car_helpers.get_fw_versions', return_value=[]) as query, \
       patch('opendbc.car.car_helpers.get_present_ecus', return_value=set()) as present, \
       patch('opendbc.car.car_helpers.get_fw_versions_ordered', return_value=[]) as generic_query, \
       patch('opendbc.car.car_helpers.match_fw_to_car', return_value=(True, set())), \
       patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
    actual = fingerprint(lambda wait_for_one=False: [frames(capture)], lambda packets: None, switches.append, None)
  assert actual[0] is None
  assert switches and not any(switches), 'HDA1_ENTERED_OBD_QUERY_FALLBACK'
  assert vin.call_args.args[2] == (0, 1)
  query.assert_called_once()
  assert query.call_args.kwargs == {'query_brand': 'hyundai', 'normal_bus_only': True}
  present.assert_not_called()
  generic_query.assert_not_called()


@pytest.mark.parametrize('capture', [0, 1])
def test_gv60_interrupt_fault_still_blocks_with_normal_query_support(capture):
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.hyundai.interface import CarInterface
  from opendbc.car.hyundai.values import CAR
  from openpilot.cereal import log
  from openpilot.starpilot.aol.panda_faults import blocking_fault_list

  observed = gen_empty_fingerprint()
  for frame in frames(capture):
    observed[frame.src][frame.address] = len(frame.dat)
  cp = CarInterface.get_params(CAR.GENESIS_GV60_EV_1ST_GEN, observed, [], True, False, False)
  assert cp.openpilotLongitudinalControl and not cp.pcmCruise
  assert cp.safetyConfigs[0].safetyParam == 0xD
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp)
