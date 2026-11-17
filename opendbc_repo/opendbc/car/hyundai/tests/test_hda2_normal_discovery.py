"""Standard HDA2 wiring selects fresh normal-bus queries, never vehicle identity."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.can_definitions import CanData
from opendbc.car.car_helpers import fingerprint
from opendbc.car.hyundai.discovery import CLASSIC_NORMAL_BUS_LAYOUT, HDA1_NORMAL_BUS_LAYOUT, NORMAL_BUS_LAYOUT, normal_bus_topology
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from opendbc.car.vin import VIN_UNKNOWN
from openpilot.cereal import log
from openpilot.starpilot.aol.panda_faults import blocking_fault_list
from openpilot.starpilot.car.hyundai.aol import policy_for

CAPTURE = json.loads((Path(__file__).parent / 'testdata/hda2_discovery.json').read_text())


def frames(collision=True):
  values = ([CAPTURE['classicIdCollision']] if collision else []) + CAPTURE['frames']
  return [CanData(f['address'], bytes.fromhex(f['data']), f['bus']) for f in values]


@pytest.mark.parametrize('collision', [False, True])
def test_recorded_hda2_layout_selects_normal_wiring(collision):
  assert normal_bus_topology(lambda **kw: [frames(collision)]), 'HDA2_NORMAL_QUERY_LAYOUT_MISSING'


@pytest.mark.parametrize('index', range(8))
@pytest.mark.parametrize('defect', ['missing', 'length', 'bus', 'echo', 'contradiction'])
def test_incomplete_malformed_or_echoed_hda2_still_cannot_select_query_family(index, defect):
  packets = frames()
  original = packets.pop(index + 1)
  if defect in ('length', 'contradiction'):
    packets.append(CanData(original.address, original.dat[:-1], original.src))
    if defect == 'contradiction':
      packets.append(original)
  elif defect != 'missing':
    packets.append(CanData(original.address, original.dat, original.src + (128 if defect == 'echo' else 1)))
  assert not normal_bus_topology(lambda **kw: [packets])


@pytest.mark.parametrize('layout', [NORMAL_BUS_LAYOUT, CLASSIC_NORMAL_BUS_LAYOUT, HDA1_NORMAL_BUS_LAYOUT])
def test_original_family_contradictions_still_fail_without_complete_hda2_proof(layout):
  packets = [CanData(address, bytes(length), bus) for (bus, address), length in layout.items()]
  packets.insert(0, CanData(0x251, bytes(16), 0))
  assert not normal_bus_topology(lambda **kw: [packets])


@pytest.mark.parametrize('collision', [False, True])
def test_complete_hda2_proof_can_span_multiple_can_drains(collision):
  packets = frames(collision)
  batches = iter([[f] for f in packets])
  assert normal_bus_topology(lambda **kw: [next(batches, [])])


@pytest.mark.parametrize('collision', [False, True])
def test_actual_fingerprint_keeps_hda2_on_normal_wiring_and_requires_fresh_identity(collision):
  switches = []
  with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)) as vin, \
       patch('opendbc.car.car_helpers.get_fw_versions', return_value=[]) as query, \
       patch('opendbc.car.car_helpers.get_present_ecus', return_value=set()) as present, \
       patch('opendbc.car.car_helpers.get_fw_versions_ordered', return_value=[]) as generic, \
       patch('opendbc.car.car_helpers.match_fw_to_car', return_value=(True, set())), \
       patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
    actual = fingerprint(lambda wait_for_one=False: [frames(collision)], lambda packets: None, switches.append, None)
  assert actual[0] is None
  assert switches and not any(switches), 'HDA2_ENTERED_OBD_QUERY_FALLBACK'
  assert vin.call_args.args[2] == (0, 1)
  query.assert_called_once()
  assert query.call_args.kwargs == {'query_brand': 'hyundai', 'normal_bus_only': True}
  present.assert_not_called()
  generic.assert_not_called()


@pytest.mark.parametrize('identity', [CAR.KIA_EV6, CAR.HYUNDAI_IONIQ_5])
@pytest.mark.parametrize('release', [False, True])
def test_exact_ev6_stock_aol_word_and_neighbor_keep_interrupt_fault_blocking(identity, release):
  observed = gen_empty_fingerprint()
  for frame in frames():
    observed[frame.src][frame.address] = len(frame.dat)
  cp = CarInterface.get_params(identity, observed, [], False, release, False)
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl
  assert cp.safetyConfigs[0].safetyParam == 0x11
  cp.safetyConfigs[0].safetyParam |= policy_for(cp).safety_param_addition
  assert cp.safetyConfigs[0].safetyParam == 0x811
  if identity == CAR.KIA_EV6:
    assert cp.flags == 10241
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp)
