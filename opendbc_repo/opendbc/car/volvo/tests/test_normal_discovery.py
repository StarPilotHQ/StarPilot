"""Recorded Polestar discovery must not switch its normal harness into OBD."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from opendbc.car import car_helpers
from opendbc.car.can_definitions import CanData
from opendbc.car.ford.discovery import normal_bus_query_brand
from opendbc.car.hyundai.discovery import normal_bus_topology as hyundai_probe
from opendbc.car.volvo.discovery import NORMAL_BUS_LAYOUT, normal_bus_topology
from opendbc.car.volvo.values import CAR
from opendbc.car.vin import VIN_UNKNOWN

CAPTURE = json.loads((Path(__file__).parent / 'testdata/polestar_startup_discovery.json').read_text())


def frames(key='frames'):
  return [CanData(f['address'], bytes.fromhex(f['hex']), f['bus']) for f in CAPTURE[key]]


def fingerprint(rows):
  result = {}
  for f in rows:
    result.setdefault(f.src, {})[f.address] = len(f.dat)
  return result


def test_recorded_pre_query_packet_selects_only_normal_query_wiring():
  rows = frames()
  assert normal_bus_topology(fingerprint(rows))
  assert normal_bus_query_brand(lambda **kw: [rows], hyundai_probe,
                                volvo_probe=normal_bus_topology, clock=lambda: 0.) == 'volvo'


@pytest.mark.parametrize('key', NORMAL_BUS_LAYOUT)
@pytest.mark.parametrize('defect', ('missing', 'length', 'bus', 'echo', 'contradiction'))
def test_every_recorded_source_must_be_consistent(key, defect):
  rows = [CanData(a, bytes(n), b) for (b, a), n in NORMAL_BUS_LAYOUT.items()]
  original = next(f for f in rows if (f.src, f.address) == key)
  rows.remove(original)
  if defect in ('length', 'contradiction'):
    rows.append(CanData(original.address, original.dat[:-1], original.src))
    if defect == 'contradiction':
      rows.append(original)
  elif defect != 'missing':
    rows.append(CanData(original.address, original.dat, original.src + (128 if defect == 'echo' else 1)))
  assert normal_bus_query_brand(lambda **kw: [rows], hyundai_probe,
                                volvo_probe=normal_bus_topology, clock=lambda: 0.) is None


class CapturedCan:
  def __init__(self):
    self.clock = 10.
    self.sent = []
    self.switches = []

  def recv(self, wait_for_one=False):
    self.clock += .01
    return [frames('fingerprintFrames')]

  def send(self, rows):
    self.sent.extend(rows)


def discover(*, old=False):
  peer = CapturedCan()
  with patch.dict(car_helpers.os.environ, {'FINGERPRINT': '', 'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': ''}), \
       patch.object(car_helpers.time, 'monotonic', side_effect=lambda: peer.clock), \
       patch.object(car_helpers.time, 'sleep', side_effect=lambda seconds: setattr(peer, 'clock', peer.clock + seconds)), \
       patch.object(car_helpers, 'get_present_ecus', return_value=set()) as present, \
       patch.object(car_helpers, 'get_fw_versions_ordered', return_value=[]) as generic, \
       patch.object(car_helpers, 'volvo_normal_bus_topology', side_effect=(lambda fp: False) if old else normal_bus_topology):
    ci = car_helpers.get_car(peer.recv, peer.send, peer.switches.append, False, False)
  return ci, peer, present, generic


def test_actual_vin_and_live_can_discovery_keep_normal_wiring_and_profile():
  ci, peer, present, generic = discover()
  assert peer.switches and set(peer.switches) == {False}
  assert peer.sent and all(f.src == 0 for f in peer.sent)
  assert ci.CP.carFingerprint == CAR.POLESTAR_2
  assert ci.CP.safetyConfigs[0].safetyParam == 5
  assert ci.CP.pcmCruise and not ci.CP.openpilotLongitudinalControl
  assert ci.CP.carVin == VIN_UNKNOWN and not ci.CP.carFw
  assert str(ci.CP.steerControlType) == 'angle'
  present.assert_not_called()
  generic.assert_not_called()


def test_same_recorded_can_reproduces_old_mux_switch_without_probe():
  ci, peer, present, generic = discover(old=True)
  assert True in peer.switches
  assert any(f.src == 1 for f in peer.sent)
  assert ci.CP.carFingerprint == CAR.POLESTAR_2
  present.assert_called_once()
  generic.assert_called_once()


def test_discovery_fix_keeps_reported_irq_fault_blocking():
  from openpilot.cereal import log
  from openpilot.starpilot.aol.panda_faults import blocking_fault_list, blocking_fault_mask
  ci, _, _, _ = discover()
  assert blocking_fault_mask(8, ci.CP)
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], ci.CP)
