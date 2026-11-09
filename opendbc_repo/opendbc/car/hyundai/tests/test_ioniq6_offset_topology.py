"""Ioniq 6 offset transport preserves the selected owner and fail-closed startup."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.can_definitions import CanData
from opendbc.car.hyundai import ioniq6_handoff as handoff
from opendbc.car.hyundai.hyundaicanfd import CanBus, create_ioniq6_radar_heartbeat, create_ioniq6_blindspot_status
from opendbc.car.hyundai.ioniq6_bsm import BlindspotStatus
from opendbc.car.hyundai.ioniq6_media import Ioniq6MediaButtons
from opendbc.car.hyundai.radar_interface import RadarInterface, radar_bus
from opendbc.car.hyundai.tests import test_ioniq6_handoff as fixtures
from opendbc.car.hyundai.tests.test_ioniq6_handoff import FakeIoniq6Bus, ioniq6_profiles
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.car.hyundai.aol import ioniq6_settings_capable, qualified_ioniq6, native_admission_key
from openpilot.starpilot.conditional_mode.manual import ioniq6_media_eligible
from openpilot.starpilot.longitudinal.ioniq6_start import eligible
from openpilot.starpilot.aol.runtime import native_inventory_matches_cp
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState


@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('alternate', (False, True))
def test_factory_and_preparation_retain_exact_transport_owner(offset, alternate):
  fingerprint, stock, cp = ioniq6_profiles(alternate=alternate, bus_offset=offset)
  assert not stock.radarUnavailable, 'OFFSET_MRR35_RADAR_NOT_DISCOVERED'
  assert not stock.openpilotLongitudinalControl and stock.pcmCruise
  assert ioniq6_settings_capable(stock)
  assert cp is not None, 'OFFSET_IONIQ6_LONG_CANDIDATE_NOT_PREPARED'
  assert cp.openpilotLongitudinalControl and cp.alphaLongitudinalAvailable and not cp.pcmCruise
  assert eligible(cp)
  assert len(cp.safetyConfigs) == 1 + offset // 4
  assert cp.safetyConfigs[-1].safetyParam == (0x8095 if alternate else 0x8015)
  if offset:
    assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput
    assert cp.safetyConfigs[0].safetyParam == 0
  assert (CanBus(cp).ACAN, CanBus(cp).ECAN, CanBus(cp).CAM) == (offset, offset + 1, offset + 2)
  assert radar_bus(cp) == offset
  assert RadarInterface(cp).rcp.bus == offset
  assert ioniq6_media_eligible(cp)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  assert qualified_ioniq6(cp) and eligible(cp)
  assert native_admission_key(cp) is not None
  wrong = gen_empty_fingerprint()
  for bus, frames in fingerprint.items():
    if frames:
      wrong[(bus + 4) % 8].update(frames)
  assert handoff.build_ioniq6_hda2_long_candidate(stock, wrong) is None


@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('option,expected', (({}, handoff.HandoffOutcome.CONFIRMED),
  ({'disable_works': False}, handoff.HandoffOutcome.STOCK),
  ({'disable_ack': False}, handoff.HandoffOutcome.STOCK),
  ({'heartbeat_stays_after_disable': True}, handoff.HandoffOutcome.STOCK),
  ({'bsm_stays_after_disable': True}, handoff.HandoffOutcome.STOCK),
  ({'post_missing_required': 0xEA, 'restore_works': False}, handoff.HandoffOutcome.UNAVAILABLE)))
def test_owner_bus_handoff_and_restore_keep_original_evidence(offset, option, expected):
  bus = FakeIoniq6Bus(bus_offset=offset, **option)
  result = bus.run()
  assert result.outcome is expected
  if expected is handoff.HandoffOutcome.CONFIRMED:
    assert result.disable_acknowledged and result.source_period == pytest.approx(.02, abs=.001)
  else:
    assert bus.queries[-2:] == [(b'\x28\x00\x01', b'\x68\x00'), (b'\x10\x01', b'\x50\x01')]


def test_foreign_bus_and_echo_cannot_authorize_external_takeover():
  bus = FakeIoniq6Bus()
  with patch('opendbc.car.hyundai.ioniq6_handoff._ioniq6_boot_ns', side_effect=lambda: int(bus.now * 1e9)):
    result = handoff.inspect_ioniq6_long_sources(bus.recv, lambda: bus.now, ecan_bus=5)
  assert result.source_period is None
  assert bus.queries == []
  observed = handoff.ObservingCanRecv(bus.recv, lambda: bus.now, ecan_bus=5)
  observed()
  assert observed.source_seen_count == observed.heartbeat_seen_count == 0


@pytest.mark.parametrize('offset', (0, 4))
def test_real_isotp_routes_ack_restore_and_tester_to_the_same_owner(offset):
  pending = []
  sent = []
  def recv(wait_for_one=False):
    frames = pending[:]
    pending.clear()
    return [frames] if frames else []
  def send(frames):
    sent.extend(frames)
    for frame in frames:
      assert frame.src == offset + 1
      request = frame.dat[1:1 + frame.dat[0]]
      response = {b'\x10\x03': b'\x50\x03\x00\x32\x01\xf4', b'\x28\x01\x01': b'\x68\x01',
                  b'\x28\x00\x01': b'\x68\x00', b'\x10\x01': b'\x50\x01\x00\x32\x01\xf4'}[request]
      pending.append(CanData(0x738, bytes([len(response)]) + response + bytes(7 - len(response)), offset + 1))
  for request, response in ((b'\x10\x03', b'\x50\x03'), (b'\x28\x01\x01', b'\x68\x01'),
                            (b'\x28\x00\x01', b'\x68\x00'), (b'\x10\x01', b'\x50\x01')):
    assert handoff.query_ioniq6_adas(recv, send, request, response, ecan_bus=offset + 1)
  keepalive = handoff.Ioniq6StartupKeepalive(sent.extend, lambda: True, Mock(), Mock(),
                                             clock=lambda: 0., ecan_bus=offset + 1)
  keepalive.tick()
  keepalive.stop()
  assert sent[-1].address == 0x730 and sent[-1].src == offset + 1
  assert bytes(sent[-1].dat[:3]) == b'\x02\x3e\x80'


@pytest.mark.parametrize('offset', (0, 4))
def test_media_and_replacement_payloads_keep_bytes_and_only_shift_transport(offset):
  source = Ioniq6MediaButtons(offset + 1)
  payload = bytes((0, 0, 0x40, 0, 0, 0x10, 0, 0))
  stamp = 10_000_000_000
  assert not source.update([(stamp, [(0x448, payload, 129 + offset)])]).valid
  sample = source.update([(stamp + 1, [(0x448, payload, offset + 1)])])
  assert sample.valid and sample.samples[0].mode_held and sample.samples[0].custom_held
  original = create_ioniq6_radar_heartbeat(19, True, False)
  shifted = create_ioniq6_radar_heartbeat(19, True, False, bus=offset)
  assert shifted.src == offset and shifted.dat == original.dat
  original = create_ioniq6_blindspot_status(19, BlindspotStatus(1, 2))
  shifted = create_ioniq6_blindspot_status(19, BlindspotStatus(1, 2), bus=offset + 1)
  assert [(m.address, m.dat) for m in original] == [(m.address, m.dat) for m in shifted]
  assert all(m.src == offset + 1 for m in shifted)


@pytest.mark.parametrize('mutation', ('aux_word', 'aux_model', 'swapped', 'third', 'owner_model', 'angle'))
def test_invalid_owner_topologies_never_borrow_long_settings_or_admission(mutation):
  _, stock, cp = ioniq6_profiles(bus_offset=4, alternate=True)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  if mutation == 'aux_word':
    cp.safetyConfigs[0].safetyParam = 1
  elif mutation == 'aux_model':
    cp.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.elm327
  elif mutation == 'owner_model':
    cp.safetyConfigs[-1].safetyModel = structs.CarParams.SafetyModel.noOutput
  elif mutation == 'swapped':
    cp.safetyConfigs = list(reversed(cp.safetyConfigs))
  elif mutation == 'third':
    cp.safetyConfigs = [*cp.safetyConfigs, cp.safetyConfigs[0]]
  else:
    cp.steerControlType = structs.CarParams.SteerControlType.angle
  assert not eligible(cp) and not qualified_ioniq6(cp)
  assert not ioniq6_settings_capable(cp)
  if mutation != 'angle':
    assert native_admission_key(cp) is None
  assert handoff.prepare_ioniq6_long_candidate(stock, cp, enabled=False, is_release=False) == (stock, False)


def test_diagnostic_owner_and_configured_passive_slot_remain_separate():
  case = fixtures.TestIoniq6PrepublicationHandoff(methodName='runTest')
  card = case.ready_card(ioniq_selected=True, bus_offset=4)
  assert card.ioniq6_panda_diagnostic_ready()
  card.sm.panda[0].safetyParam = 0
  assert card.ioniq6_panda_diagnostic_ready()  # internal OBD multiplexing, external remains unchanged
  card.sm.panda[1].safetyParam = 0
  assert not card.ioniq6_panda_diagnostic_ready()
  card.sm.panda[1].safetyParam = 1
  card.sm.panda[0].controlsAllowed = True
  assert not card.ioniq6_panda_diagnostic_ready()
  card.sm.panda[0].controlsAllowed = False
  card.sm.panda.append(SimpleNamespace(safetyModel=3, safetyParam=1, controlsAllowed=False))
  assert not card.ioniq6_panda_diagnostic_ready()
  card.sm.panda.pop()
  for state, cfg in zip(card.sm.panda, card.CP.safetyConfigs, strict=True):
    state.safetyModel, state.safetyParam = cfg.safetyModel, cfg.safetyParam
    state.alternativeExperience = card.CP.alternativeExperience
    state.safetyRxChecksInvalid = False
  assert not card.ioniq6_panda_diagnostic_ready()
  assert Car.ioniq6_panda_matches(card)
  card.sm.panda.append(card.sm.panda[0])
  assert not Car.ioniq6_panda_matches(card)


@pytest.mark.parametrize('fault', ('none', 'aux_controls', 'aux_fault', 'owner_fault', 'aux_rx', 'owner_heartbeat',
                                  'duplicate', 'missing', 'extra', 'swapped', 'wrong_owner', 'unbound'))
def test_complete_native_inventory_rejects_every_fault_or_identity_mismatch(fault):
  _, _, cp = ioniq6_profiles(bus_offset=4, alternate=True)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  aux = PandaSlot(0, 'internal', int(cp.safetyConfigs[0].safetyModel.raw), 0, 32, False, False, False, 0)
  owner = PandaSlot(1, 'external', int(cp.safetyConfigs[1].safetyModel.raw), 0x8895, 32, True, False, False, 0)
  slots = [aux, owner]
  serial = 'external'
  source = 10_000_000_000
  if fault == 'aux_controls':
    slots[0] = replace(aux, controlsAllowed=True)
  elif fault == 'aux_fault':
    slots[0] = replace(aux, faults=4)
  elif fault == 'owner_fault':
    slots[1] = replace(owner, faults=4)
  elif fault == 'aux_rx':
    slots[0] = replace(aux, safetyRxChecksInvalid=True)
  elif fault == 'owner_heartbeat':
    slots[1] = replace(owner, heartbeatLost=True)
  elif fault == 'duplicate':
    slots[0] = replace(aux, hardwareSerial='external')
  elif fault == 'missing':
    slots.pop(0)
  elif fault == 'extra':
    slots.append(aux)
  elif fault == 'swapped':
    slots.reverse()
  elif fault == 'wrong_owner':
    serial = 'internal'
  elif fault == 'unbound':
    source = 0
  native = SafetyState(protocolVersion=1, compatible=True, observedMonoTime=source,
                       validUntilMonoTime=source + 30_000_000, safetyModel=28, safetyParam=0x8895,
                       lateralAllowed=True, longitudinalAllowed=True, requestedLateral=True, requestedLongitudinal=True,
                       pandaSerial=serial, axisSessionId='axis-session', sourcePandaStatesMonoTime=source,
                       pandaInventory=tuple(slots))
  assert native_inventory_matches_cp(native, cp) == (fault == 'none')


@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('ready,unknown', ((False, False), (True, False), (False, True)))
def test_actual_card_cold_ready_unknown_selection_on_external_transport(alternate, ready, unknown):
  case = fixtures.TestIoniq6PrepublicationHandoff(methodName='runTest')
  try:
    case.assert_card_prepublication_fallback({}, alternate=alternate, vehicle_ready=ready, unknown=unknown, bus_offset=4)
  finally:
    case.doCleanups()
