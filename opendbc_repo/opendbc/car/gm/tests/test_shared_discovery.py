"""GM-wide normal wiring discovery; recorded Sierra and unchanged control owners."""
import json
from pathlib import Path
from unittest import mock

import pytest
from opendbc.car import car_helpers, gen_empty_fingerprint
from opendbc.car.can_definitions import CanData
from opendbc.car.fingerprints import _FINGERPRINTS
from opendbc.car.ford.discovery import normal_bus_query_brand
from opendbc.car.gm.discovery import normal_bus0_topology, pedal_normal_bus0_topology
from opendbc.car.gm.fingerprints import FINGERPRINTS
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_automatic_discovery import RecordedIsoTpEcu
from opendbc.car.gm.values import CAR
from opendbc.car.hyundai.discovery import normal_bus_topology as hyundai_probe

SIERRA = json.loads((Path(__file__).parent / 'testdata/sierra_startup_discovery.json').read_text())
GM_LAYOUTS = [(car, i, fp) for car, layouts in FINGERPRINTS.items() for i, fp in enumerate(layouts)]
OTHER_LAYOUTS = [(car, i, fp) for car, layouts in _FINGERPRINTS.items() if car not in CAR for i, fp in enumerate(layouts)]


def frames(fp):
  return [CanData(a, bytes(n), b) for b, messages in fp.items() for a, n in messages.items()]


def sierra_fingerprint() -> dict[int, dict[int, int]]:
  fp = gen_empty_fingerprint()
  fp.update({int(b): {int(a): n for a, n in messages.items()} for b, messages in SIERRA['fingerprint'].items()})
  return fp


@pytest.mark.parametrize('car,index,layout', GM_LAYOUTS, ids=[f'{c}-{i}' for c, i, _ in GM_LAYOUTS])
def test_all_registered_gm_can_layouts_use_normal_query_family(car, index, layout):
  assert normal_bus0_topology({0: layout})


@pytest.mark.parametrize('car,index,layout', OTHER_LAYOUTS, ids=[f'{c}-{i}' for c, i, _ in OTHER_LAYOUTS])
def test_other_brand_layouts_do_not_select_gm_queries(car, index, layout):
  assert not normal_bus0_topology({0: layout})
  assert not normal_bus0_topology({0: layout, 2: layout})


def test_recorded_sierra_passive_window_is_covered_without_pedal():
  batches = [[CanData(a, bytes(n), b) for a, n, b in packet] for packet in SIERRA['packets']]
  observed = {}
  for packet in batches:
    for frame in packet:
      observed.setdefault(frame.src, {})[frame.address] = len(frame.dat)
  assert not pedal_normal_bus0_topology(observed)  # This was the uncovered path.
  assert normal_bus0_topology(observed)
  stream = iter(batches)
  reads = []
  clock = [0.]

  def recv(wait_for_one: bool = False) -> list[list[CanData]]:
    reads.append(wait_for_one)
    clock[0] += .01
    return [next(stream, [])]
  assert normal_bus_query_brand(recv, lambda read: hyundai_probe(read, clock=lambda: clock[0]),
                                gm_probe=normal_bus0_topology, clock=lambda: clock[0]) == 'gm'
  assert len(reads) <= 13


@pytest.mark.parametrize('case', ['empty', 'sparse', 'wrong_bus', 'loopback', 'wrong_dlc'])
def test_unsupported_evidence_keeps_generic_discovery(case):
  fp = {0: {0xC9: 8, 0x184: 8, 0x1C4: 8, 0x1E1: 7}}
  if case == 'empty':
    fp = {}
  elif case in ('wrong_bus', 'loopback'):
    fp = {2 if case == 'wrong_bus' else 128: sierra_fingerprint()[0]}
  elif case == 'wrong_dlc':
    fp = {0: dict(sierra_fingerprint()[0])}
    fp[0][0x184] = 7
  assert not normal_bus0_topology(fp)


def test_conflicting_dlc_does_not_fall_back_to_positive_gm_evidence():
  rows = frames(sierra_fingerprint())
  bad = CanData(0x184, bytes(7), 0)

  def recv(wait_for_one: bool = False) -> list[list[CanData]]:
    return [[bad, *rows]]
  assert normal_bus_query_brand(recv, hyundai_probe, gm_probe=normal_bus0_topology, clock=lambda: 0.) is None


class SierraEcu(RecordedIsoTpEcu):
  def recv(self, wait_for_one=False):
    self.clock += .01
    values, self.frames = self.frames, []
    return [values + frames(sierra_fingerprint())]


def discover(peer, *, legacy=False, instantiate=False):
  callbacks = []
  with mock.patch.dict(car_helpers.os.environ, {'FINGERPRINT': '', 'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': ''}), \
       mock.patch.object(car_helpers.time, 'monotonic', side_effect=lambda: peer.clock), \
       mock.patch.object(car_helpers.time, 'sleep', side_effect=lambda seconds: setattr(peer, 'clock', peer.clock + seconds)), \
       mock.patch.object(car_helpers, 'gm_normal_bus0_topology', pedal_normal_bus0_topology if legacy else normal_bus0_topology):
    if instantiate:
      result = car_helpers.get_car(peer.recv, peer.send, callbacks.append, False, False)
    else:
      result = car_helpers.fingerprint(peer.recv, peer.send, callbacks.append, None)
  return result, callbacks


def test_actual_sierra_vin_fw_can_and_control_profile_keep_normal_bus0():
  peer = SierraEcu()
  result, callbacks = discover(peer)
  assert callbacks and set(callbacks) == {False}
  assert peer.sent and all(frame.src == 0 for frame in peer.sent)
  assert any(frame.dat[0] >> 4 == 3 for frame in peer.sent)  # Actual ISO-TP flow control.
  assert result[0] == SIERRA['expected'] == CAR.CHEVROLET_SILVERADO
  assert result[3] and all(fw.brand == 'gm' and fw.bus == 0 for fw in result[3])
  ci, callbacks = discover(SierraEcu(), instantiate=True)
  direct = CarInterface.get_params(CAR.CHEVROLET_SILVERADO, sierra_fingerprint(), [], False, False, False)
  assert set(callbacks) == {False}
  assert ci.CP.safetyConfigs[0].safetyParam == direct.safetyConfigs[0].safetyParam == 0xC171
  assert ci.CP.flags == direct.flags == 16
  assert ci.CP.carFingerprint == direct.carFingerprint
  assert ci.CP.pcmCruise and not ci.CP.openpilotLongitudinalControl
  assert ci.CP.lateralTuning.to_dict() == direct.lateralTuning.to_dict()


def test_previous_pedal_only_route_reproduces_sierra_mux_and_can1_queries():
  # Same inert CAN/ECU peer: the old routing path enables the mux and probes
  # CAN1 even though the GM ECU replies and complete CAN layout are on bus0.
  peer = SierraEcu()
  _, callbacks = discover(peer, legacy=True)
  assert True in callbacks
  assert any(frame.src == 1 for frame in peer.sent)


def test_sierra_routing_fix_keeps_raw_irq_fault_blocking():
  from openpilot.cereal import log
  from openpilot.starpilot.aol.panda_faults import blocking_fault_list, blocking_fault_mask
  cp = CarInterface.get_params(CAR.CHEVROLET_SILVERADO, sierra_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp)
  assert blocking_fault_mask(8, cp)


def test_latest_bypassed_volt_uses_gm_queries_and_retains_automatic_cc_identity():
  data = json.loads((Path(__file__).parent / 'testdata/volt_bypassed_discovery.json').read_text())
  fp = gen_empty_fingerprint()
  fp.update({int(b): {int(a): n for a, n in messages.items()} for b, messages in data['fingerprint'].items()})
  assert not pedal_normal_bus0_topology(fp)
  assert normal_bus0_topology(fp)

  class VoltEcu(RecordedIsoTpEcu):
    def recv(self, wait_for_one=False):
      self.clock += .01
      values, self.frames = self.frames, []
      return [values + frames(fp)]

  peer = VoltEcu()
  result, callbacks = discover(peer)
  assert result[0] == data['expected'] == CAR.CHEVROLET_VOLT_CC
  assert callbacks and set(callbacks) == {False}
  assert peer.sent and all(frame.src == 0 for frame in peer.sent)


def test_early_ford_rejection_still_collects_later_gm_frames_in_same_budget():
  # Actual 2017 Bolt DLCs: 0x204 is four bytes, 0x4f1 is eight. The first
  # rejects Ford and the second rejects HKG before GM core messages arrive.
  layout = FINGERPRINTS[CAR.CHEVROLET_BOLT_CC_2017][0]
  assert layout[0x204] == 4 and layout[0x4F1] == 8
  early = [CanData(a, bytes(layout[a]), 0) for a in (0x204, 0x4F1)]
  remaining = frames({0: layout})
  calls = []
  clock = [0.]

  def recv(wait_for_one: bool = False) -> list[list[CanData]]:
    clock[0] += .01
    calls.append(wait_for_one)
    return [early if len(calls) == 1 else remaining]
  assert normal_bus_query_brand(recv, lambda read: hyundai_probe(read, clock=lambda: clock[0]),
                                gm_probe=normal_bus0_topology, clock=lambda: clock[0]) == 'gm'
  assert 1 < len(calls) <= 13


def test_early_ford_rejection_does_not_add_reads_without_gm_probe():
  calls = []

  def recv(wait_for_one: bool = False) -> list[list[CanData]]:
    calls.append(wait_for_one)
    return [[CanData(0x204, bytes(4), 0), CanData(0x4F1, bytes(8), 0)]]
  assert normal_bus_query_brand(recv, hyundai_probe, clock=lambda: 0.) is None
  assert len(calls) == 1
