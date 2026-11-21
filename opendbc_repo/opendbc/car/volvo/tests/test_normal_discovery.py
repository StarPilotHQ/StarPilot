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


def v60_rows():
  # Live V60 CAN as in route 00000004: its fingerprint and EngineData mirrored on buses 0 and 2, the ESR on bus 1
  from opendbc.car.volvo.fingerprints import FINGERPRINTS
  addrs = set(FINGERPRINTS[CAR.VOLVO_V60][0]) | {0x12A}
  rows = [CanData(a, bytes(8), bus) for bus in (0, 2) for a in sorted(addrs)]
  return rows + [CanData(a, bytes(8), 1) for a in range(0x500, 0x540)]


def test_v60_layout_is_its_own():
  from opendbc.car.volvo.discovery import V60_BUS_LAYOUT, v60_bus_topology
  assert v60_bus_topology(fingerprint(v60_rows()))
  assert not normal_bus_topology(fingerprint(v60_rows()))
  assert not v60_bus_topology(fingerprint(frames()))  # Polestar
  for key in V60_BUS_LAYOUT:
    assert not v60_bus_topology(fingerprint([f for f in v60_rows() if (f.src, f.address) != key]))
  # GM shares FSM1/EngineData addresses, not FSM0/FSM3
  gm = [CanData(a, bytes(8), bus) for bus in (0, 2) for a in (0x12A, 0x260, 0x262)]
  assert not v60_bus_topology(fingerprint(gm))


def test_v60_queries_only_its_engine_on_bus0_without_the_obd_scan():
  from opendbc.car import structs
  rows = v60_rows()
  peer = CapturedCan()
  peer.recv = lambda wait_for_one=False: (setattr(peer, 'clock', peer.clock + .01), [rows])[1]
  engine = structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.engine, address=0x7e0, bus=0, brand='volvo',
                                   fwVersion=b'31361041 AJ\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
                                   request=[bytes.fromhex('22f1a2')], responseAddress=0x7e8)
  with patch.dict(car_helpers.os.environ, {'FINGERPRINT': '', 'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': ''}), \
       patch.object(car_helpers.time, 'monotonic', side_effect=lambda: peer.clock), \
       patch.object(car_helpers.time, 'sleep', side_effect=lambda seconds: setattr(peer, 'clock', peer.clock + seconds)), \
       patch.object(car_helpers, 'get_present_ecus', return_value=set()) as present, \
       patch.object(car_helpers, 'get_fw_versions_ordered', return_value=[]) as generic, \
       patch.object(car_helpers, 'get_fw_versions', return_value=[engine]) as normal:
    ci = car_helpers.get_car(peer.recv, peer.send, peer.switches.append, False, False)
  assert peer.switches and set(peer.switches) == {False}
  assert all(f.src == 0 for f in peer.sent)
  normal.assert_called_once()
  assert normal.call_args.kwargs == {'query_brand': 'volvo', 'normal_bus_only': True}
  present.assert_not_called()
  generic.assert_not_called()
  assert ci.CP.carFingerprint == CAR.VOLVO_V60
  assert str(ci.CP.fingerprintSource) == 'fw'
