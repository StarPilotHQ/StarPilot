import json
import pytest
from pathlib import Path
from unittest.mock import patch

from opendbc.car.can_definitions import CanData
from opendbc.car.car_helpers import fingerprint
from opendbc.car.hyundai.discovery import normal_bus_topology
from opendbc.car.vin import VIN_UNKNOWN

CAPTURE = json.loads((Path(__file__).parent / 'testdata/canfd_alternate_discovery.json').read_text())


def batches() -> list[list[CanData]]:
  return [[CanData(f['address'], bytes.fromhex(f['hex']), f['src']) for f in packet['frames']]
          for packet in CAPTURE['packets']]


def test_recorded_alternate_powertrain_and_buttons_select_normal_wiring():
  source = iter(batches())
  assert normal_bus_topology(lambda **kw: [next(source, [])]), 'ALTERNATE_HKG_ENTERED_OBD_MUX_PATH'


def test_actual_fingerprint_keeps_fresh_queries_without_switching_obd_wiring():
  switches = []
  with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)) as vin, \
       patch('opendbc.car.car_helpers.get_fw_versions', return_value=[]) as query, \
       patch('opendbc.car.car_helpers.get_present_ecus', return_value=set()) as present, \
       patch('opendbc.car.car_helpers.get_fw_versions_ordered', return_value=[]) as generic, \
       patch('opendbc.car.car_helpers.match_fw_to_car', return_value=(True, set())), \
       patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
    source = [f for packet in batches() for f in packet]
    actual = fingerprint(lambda wait_for_one=False: [source], lambda packets: None, switches.append, None)
  assert actual[0] is None
  assert switches and not any(switches), 'ALTERNATE_HKG_SWITCHED_OBD_WIRING'
  assert vin.call_args.args[2] == (0, 1)
  query.assert_called_once()
  assert query.call_args.kwargs == {'query_brand': 'hyundai', 'normal_bus_only': True}
  present.assert_not_called()
  generic.assert_not_called()


from opendbc.car.hyundai.discovery import CANFD_VARIANT_LAYOUTS


@pytest.mark.parametrize('layout', CANFD_VARIANT_LAYOUTS)
def test_all_dbc_defined_powertrain_button_and_gear_variants(layout):
  source = [CanData(address, bytes(length), bus) for (bus, address), length in layout.items()]
  assert normal_bus_topology(lambda **kw: [source])


@pytest.mark.parametrize('layout', CANFD_VARIANT_LAYOUTS)
@pytest.mark.parametrize('index', range(8))
@pytest.mark.parametrize('defect', ['missing', 'length', 'bus', 'echo', 'contradiction'])
def test_variants_require_every_consistent_unmodified_source(layout, index, defect):
  source = [CanData(address, bytes(length), bus) for (bus, address), length in layout.items()]
  frame = source.pop(index)
  if defect in ('length', 'contradiction'):
    source.append(CanData(frame.address, frame.dat[:-1], frame.src))
    if defect == 'contradiction':
      source.append(frame)
  elif defect != 'missing':
    source.append(CanData(frame.address, frame.dat, frame.src + (128 if defect == 'echo' else 1)))
  clock = iter([0., 0., .13])
  assert not normal_bus_topology(lambda **kw: [source], clock=lambda: next(clock))


def recorded_firmware():
  from opendbc.car.structs import CarParams
  values = []
  for row in CAPTURE['carFw']:
    data = {k: row[k] for k in ('ecu', 'address', 'subAddress', 'bus', 'brand', 'logging', 'obdMultiplexing', 'responseAddress')}
    data['fwVersion'] = bytes.fromhex(row['fwVersion']['hex'])
    data['request'] = [bytes.fromhex(v['hex']) for v in row['request']]
    values.append(CarParams.CarFw(**data))
  return values


def test_recorded_firmware_identity_and_normal_query_coverage_are_preserved():
  from opendbc.car.fw_versions import FW_QUERY_CONFIGS, match_fw_to_car
  from opendbc.car.hyundai.values import CAR
  fw = recorded_firmware()
  exact, matches = match_fw_to_car(fw, VIN_UNKNOWN)
  assert exact and matches == {CAR.KIA_CARNIVAL_2025}
  normal = [r for r in FW_QUERY_CONFIGS['hyundai'].requests if not (r.bus % 4 == 1 and r.obd_multiplexing)]
  for row in fw:
    assert any(r.bus == row.bus and r.request == list(row.request) and r.obd_multiplexing == row.obdMultiplexing
               for r in normal)


def test_actual_fingerprint_still_requires_recorded_fresh_firmware_for_identity():
  from opendbc.car.hyundai.values import CAR
  switches = []
  with patch('opendbc.car.car_helpers.get_vin', return_value=(-1, -1, VIN_UNKNOWN)), \
       patch('opendbc.car.car_helpers.get_fw_versions', return_value=recorded_firmware()) as query, \
       patch('opendbc.car.car_helpers.get_present_ecus') as present, \
       patch('opendbc.car.car_helpers.get_fw_versions_ordered') as generic, \
       patch('opendbc.car.car_helpers.can_fingerprint', return_value=(None, {})):
    source = [f for packet in batches() for f in packet]
    actual = fingerprint(lambda wait_for_one=False: [source], lambda packets: None, switches.append, None)
  assert actual[0] == CAR.KIA_CARNIVAL_2025
  assert switches and not any(switches)
  assert query.call_args.kwargs == {'query_brand': 'hyundai', 'normal_bus_only': True}
  present.assert_not_called()
  generic.assert_not_called()


def test_exact_recorded_configuration_keeps_irq_fault_blocking():
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.hyundai.interface import CarInterface
  from opendbc.car.hyundai.values import CAR
  from openpilot.cereal import log
  from openpilot.starpilot.aol.panda_faults import blocking_fault_list, blocking_fault_mask
  from openpilot.starpilot.car.hyundai.aol import policy_for
  observed = gen_empty_fingerprint()
  for packet in batches():
    for f in packet:
      observed[f.src][f.address] = len(f.dat)
  cp = CarInterface.get_params(CAR.KIA_CARNIVAL_2025, observed, [], False, False, False)
  cp.safetyConfigs[0].safetyParam |= policy_for(cp).safety_param_addition
  assert cp.safetyConfigs[0].safetyParam == 0x28B0
  assert cp.alternativeExperience == 0 and cp.pcmCruise and not cp.openpilotLongitudinalControl
  assert blocking_fault_mask(8, cp)
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp)
