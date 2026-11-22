import time

import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_lfa_status import CANFDClusterStatus, CANFDLfaStatus, preserves_stock_lfa
from opendbc.car.hyundai.hyundaicanfd import CLUSTER_CONTROL_MASK, CanBus, create_lfahda_cluster, hkg_can_fd_checksum
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags


@pytest.fixture
def clock(monkeypatch):
  value = [1_000_000_000]
  monkeypatch.setattr(time, 'CLOCK_BOOTTIME', getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC), raising=False)
  monkeypatch.setattr(time, 'clock_gettime_ns', lambda _: value[0])
  return value


def params(car=CAR.HYUNDAI_KONA_EV_2ND_GEN, *, hda2=False, alpha=False, alternate=False):
  fp = gen_empty_fingerprint()
  fp[1 if hda2 else 0][0x1AA if alternate else 0x1CF] = 16 if alternate else 8
  if hda2:
    fp[2].update({0x50: 16, 0x2A4: 24})
  cp = CarInterface.get_params(car, fp, [], alpha, False, False)
  if not cp.openpilotLongitudinalControl:
    cp.safetyConfigs[-1].safetyParam |= 0x800
  return cp


def template(packer, bus, counter, *, warning=0, fault=0, mode=0):
  address, body, _ = packer.make_can_msg('LFA', bus, {'COUNTER': counter, 'LKA_SysIndReq': 4 if warning else 1,
                                                     'LKA_SysWrn': warning, 'ToiFltSta': fault, 'LKA_UsmMod': mode})
  data = bytearray(body)
  data[9] = 0x0A
  data[14] = 0xA5
  data[:2] = hkg_can_fd_checksum(address, None, data).to_bytes(2, 'little')
  return address, bytes(data), bus


def physical_frames(ci, packer, tick, stock, *, buttons=True):
  bus = ci.CC.CAN
  drive = next(code for code, label in ci.CS.shifter_values.items() if label == 'D')
  inventory = {ci.CS.accelerator_msg_canfd: {}, ci.CS.gear_msg_canfd: {'GEAR': drive},
               'TCS': {}, 'WHEEL_SPEEDS': dict.fromkeys(('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), 36.),
               'MDPS': {}, 'DOORS_SEATBELTS': {'DRIVER_SEATBELT': 1},
               'STEERING_SENSORS': {}, 'BLINKERS': {}, 'MANUAL_SPEED_LIMIT_ASSIST': {}}
  if buttons:
    name = ci.CS.cruise_btns_msg_canfd
    inventory[name] = {'COUNTER': tick % (16 if name == 'CRUISE_BUTTONS' else 256)}
  frames = [packer.make_can_msg(name, bus.ECAN, values) for name, values in inventory.items()]
  return frames + [packer.make_can_msg('SCC_CONTROL', bus.CAM, {}), stock]


@pytest.mark.parametrize('car', (CAR.HYUNDAI_KONA_EV_2ND_GEN, CAR.HYUNDAI_IONIQ_5, CAR.KIA_EV6,
                                CAR.KIA_NIRO_EV_2ND_GEN, CAR.GENESIS_GV60_EV_1ST_GEN,
                                CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, CAR.HYUNDAI_SONATA_2024,
                                CAR.KIA_SPORTAGE_5TH_GEN))
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('active', (False, True))
@pytest.mark.parametrize('warning,fault', ((0, 0), (15, 2)))
def test_actual_interface_preserves_stock_body_and_faults_without_borrowing_actuation(car, alternate, alpha, active, warning, fault, clock):
  cp = params(car, alpha=alpha, alternate=alternate)
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[car][Bus.pt])
  bus = ci.CC.CAN
  decoder = CANParser(DBC[car][Bus.pt], [('LFA', 0)], bus.ECAN)
  command = structs.CarControl.new_message()
  command.latActive = active
  command.enabled = bool(active and cp.openpilotLongitudinalControl)
  command.actuators.torque = .4
  for tick in range(4):
    clock[0] = 1_000_000_000 + tick * 10_000_000
    stock = template(packer, bus.CAM, (tick + 173) % 256, warning=warning, fault=fault)
    frames = physical_frames(ci, packer, tick, stock)
    state = ci.update([(clock[0], frames)])
    missing = [(str(bus_key), source.name, source.frequency, source.ignore_alive) for bus_key, parser in ci.can_parsers.items()
               for source in parser.message_states.values() if not source.timestamps and not source.ignore_alive]
    assert state.canValid and not state.canTimeout, missing
    _, packets = ci.apply(command.as_reader(), clock[0])
    own = [packet for packet in packets if packet[0] == 0x12A and packet[2] == bus.ECAN]
    assert len(own) == 1
    body = own[0][1]
    assert body[9] == stock[1][9] == 0x0A
    assert body[14] == stock[1][14] == 0xA5
    assert body[2] == tick
    assert int.from_bytes(body[:2], 'little') == hkg_can_fd_checksum(0x12A, None, bytearray(body))
    decoder.update([(clock[0], own)])
    assert decoder.vl['LFA']['LKA_SysWrn'] == warning
    assert decoder.vl['LFA']['ToiFltSta'] == fault
    assert decoder.vl['LFA']['ActToiSta'] == int(active)
    assert decoder.vl['LFA']['LKA_SysIndReq'] == (4 if warning else 2 if active else 1)
    assert abs(decoder.vl['LFA']['StrTqReqVal']) <= ci.CC.params.STEER_MAX
    if not active:
      assert decoder.vl['LFA']['StrTqReqVal'] == 0


@pytest.mark.parametrize('mutation', ('crc', 'bus', 'length', 'future', 'held', 'backward', 'counter'))
def test_bad_or_held_sources_do_not_renew_template(mutation, clock):
  source = CANFDLfaStatus(2)
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_EV_2ND_GEN][Bus.pt])
  first = template(packer, 2, 254)
  source.update([(clock[0], [first])])
  assert source.current(clock[0]) == first[1]
  clock[0] += 10_000_000
  later = template(packer, 2, 255)
  stamp = clock[0]
  if mutation == 'crc':
    data = bytearray(later[1])
    data[9] ^= 1
    later = later[0], bytes(data), later[2]
  elif mutation == 'bus':
    later = later[0], later[1], 0
  elif mutation == 'length':
    later = later[0], later[1][:-1], later[2]
  elif mutation == 'future':
    stamp += 1
  elif mutation == 'held':
    later = first
  elif mutation == 'backward':
    stamp = 999_999_999
  elif mutation == 'counter':
    later = template(packer, 2, 10)
  source.update([(stamp, [later])])
  assert source.current(clock[0]) == first[1]
  assert source.current(1_050_000_000) == first[1]
  assert source.current(1_050_000_001) is None
  assert source.current(999_999_999) is None


def test_multiple_frames_per_batch_and_counter_wrap_are_preserved(clock):
  source = CANFDLfaStatus(2)
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_EV_2ND_GEN][Bus.pt])
  frames = [template(packer, 2, value) for value in (254, 255, 0)]
  source.update([(clock[0], frames)])
  assert source.current(clock[0]) == frames[-1][1]


def test_layout_and_passive_profiles_keep_existing_sender():
  for hda2, passive, car in ((True, False, CAR.HYUNDAI_KONA_EV_2ND_GEN),
                             (False, True, CAR.HYUNDAI_KONA_EV_2ND_GEN),
                             (False, False, CAR.KIA_CARNIVAL_2025),
                             (False, False, CAR.HYUNDAI_IONIQ_5_PE)):
    cp = params(car, hda2=hda2)
    cp.passive = passive
    assert not preserves_stock_lfa(cp)


def test_mask_owns_only_current_dbc_actuation_and_normal_status_fields():
  from opendbc.car.hyundai.hyundaicanfd import LFA_CONTROL_MASK
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_EV_2ND_GEN][Bus.pt])
  signals = packer.dbc.name_to_msg['LFA'].sigs
  expected = bytearray(16)
  owned = ('CHECKSUM', 'COUNTER', 'LKA_OptUsmSta', 'LKA_RcgSta', 'LKA_SysIndReq',
           'StrTqReqVal', 'ActToiSta')
  for name in owned:
    signal = signals[name]
    assert signal.is_little_endian
    for bit in range(signal.lsb, signal.lsb + signal.size):
      expected[bit // 8] |= 1 << (bit % 8)
  assert bytes(expected) == LFA_CONTROL_MASK
  for name in ('Damping_Gain', 'LKA_UsmMod', 'ToiFltSta', 'LKA_SysWrn', 'LKA_OnOffEquip2Sta', 'ELK_SysFlrSta', 'FCA_ESA_WrnSta'):
    signal = signals[name]
    assert all(not (LFA_CONTROL_MASK[bit // 8] >> (bit % 8)) & 1 for bit in range(signal.lsb, signal.lsb + signal.size))


@pytest.mark.parametrize('indication', (4, 5, 6, 7))
def test_critical_and_unknown_stock_indications_are_not_replaced_with_green(indication):
  from opendbc.car.hyundai.hyundaicanfd import CanBus, create_steering_messages
  cp = params()
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  _, body, _ = packer.make_can_msg('LFA', 2, {'LKA_SysIndReq': indication})
  message = create_steering_messages(packer, cp, CanBus(cp), True, True, 0, stock_lfa=body)
  decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFA', 0)], CanBus(cp).ECAN)
  decoder.update([(1_000_000_000, message)])
  assert decoder.vl['LFA']['LKA_SysIndReq'] == indication


def test_alpha_choice_does_not_change_the_template_protocol_selector():
  for car in (CAR.HYUNDAI_KONA_EV_2ND_GEN, CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_IONIQ_6, CAR.KIA_EV6):
    stock, requested = params(car), params(car, alpha=True)
    assert preserves_stock_lfa(stock)
    assert preserves_stock_lfa(requested)
    for experience in (0, 32):
      requested.alternativeExperience = experience
      assert preserves_stock_lfa(requested)


def test_real_native_lateral_grant_and_source_expiry_still_control_transmission():
  from opendbc.car.hyundai.hyundaicanfd import CanBus, create_steering_messages
  from opendbc.safety.tests.test_hyundai_shared_canfd_stock_aol import TestHyundaiSharedCanfdStockAol
  case = TestHyundaiSharedCanfdStockAol()
  case.setUp()
  case.mode(0x809)
  case.healthy(0x809)
  case.gesture(0x809)
  assert case.safety.aol_get_permission_mask() == 1
  cp = params()
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  source = template(packer, 2, 1, warning=15, fault=2)[1]
  message = create_steering_messages(packer, cp, CanBus(cp), True, True, 2, stock_lfa=source)[0]
  assert case.safety.safety_tx_hook(case.packet(message))
  case.safety.set_timer(2_000_000)
  assert case.safety.aol_get_permission_mask() == 0
  assert not case.safety.safety_tx_hook(case.packet(message))


def test_stale_source_does_not_clear_faults_or_keep_equipment_body(clock):
  from opendbc.car.hyundai.hyundaicanfd import CanBus, create_steering_messages
  cp = params()
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  source = CANFDLfaStatus(2)
  fault = template(packer, 2, 0, warning=15, fault=2, mode=3)
  source.update([(clock[0], [fault])])
  clock[0] += 50_000_001
  assert source.current(clock[0]) is None
  assert source.last_fault() == fault[1]
  packets = create_steering_messages(packer, cp, CanBus(cp), True, True, 2, stock_lfa_faults=source.last_fault())
  body = packets[0][1]
  decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFA', 0)], CanBus(cp).ECAN)
  decoder.update([(clock[0], packets)])
  assert decoder.vl['LFA']['LKA_SysIndReq'] == 4
  assert decoder.vl['LFA']['LKA_SysWrn'] == 15
  assert decoder.vl['LFA']['ToiFltSta'] == 2
  assert decoder.vl['LFA']['ActToiSta'] == 1
  assert decoder.vl['LFA']['StrTqReqVal'] == 2
  assert decoder.vl['LFA']['LKA_UsmMod'] == 0
  assert body[9] == body[14] == 0
  assert int.from_bytes(body[:2], 'little') == hkg_can_fd_checksum(0x12A, None, bytearray(body))
  healthy = template(packer, 2, 1)
  source.update([(clock[0], [healthy])])
  assert source.current(clock[0]) == healthy[1]
  assert source.last_fault() is None


def test_fault_only_mask_is_derived_from_dbc_and_cannot_copy_actuation():
  from opendbc.car.hyundai.hyundaicanfd import LFA_FAULT_MASK
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_EV_2ND_GEN][Bus.pt])
  signals = packer.dbc.name_to_msg['LFA'].sigs
  expected = bytearray(16)
  for name in ('LKA_SysIndReq', 'ToiFltSta', 'LKA_SysWrn'):
    signal = signals[name]
    assert signal.is_little_endian
    for bit in range(signal.lsb, signal.lsb + signal.size):
      expected[bit // 8] |= 1 << (bit % 8)
  assert bytes(expected) == LFA_FAULT_MASK
  for name in ('CHECKSUM', 'COUNTER', 'StrTqReqVal', 'ActToiSta', 'LKA_OnOffEquip2Sta', 'Damping_Gain'):
    signal = signals[name]
    assert all(not (LFA_FAULT_MASK[bit // 8] >> (bit % 8)) & 1 for bit in range(signal.lsb, signal.lsb + signal.size))


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('alternate', (False, True))
def test_actual_primary_buttons_stay_required_and_optional_units_do_not_gate_health(alpha, alternate, clock):
  cp = params(CAR.HYUNDAI_IONIQ_5, alpha=alpha, alternate=alternate)
  for present in (False, True):
    ci = CarInterface(cp)
    ci.update([])
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    for tick in range(12):
      clock[0] = 2_000_000_000 + tick * 10_000_000
      stock = template(packer, ci.CC.CAN.CAM, tick)
      state = ci.update([(clock[0], physical_frames(ci, packer, tick, stock, buttons=present))])
    assert state.canValid == present
    primary = ci.can_parsers[Bus.pt].message_states[0x1AA if alternate else 0x1CF]
    assert not primary.ignore_alive
    if not alternate:
      assert ci.can_parsers[Bus.pt].message_states[0x1AA].ignore_alive


@pytest.mark.parametrize('car', (CAR.HYUNDAI_IONIQ_5, CAR.KIA_EV6, CAR.KIA_NIRO_EV_2ND_GEN,
                                CAR.GENESIS_GV60_EV_1ST_GEN, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, CAR.HYUNDAI_SONATA_2024))
@pytest.mark.parametrize('alternate', (False, True))
def test_alpha_on_and_off_emit_identical_steering_for_identical_commands(car, alternate, clock):
  outputs = []
  for alpha in (False, True):
    cp = params(car, alpha=alpha, alternate=alternate)
    assert cp.openpilotLongitudinalControl == alpha
    ci = CarInterface(cp)
    ci.update([])
    packer = CANPacker(DBC[car][Bus.pt])
    command = structs.CarControl.new_message(enabled=True, latActive=True)
    command.actuators.torque = .4
    branch = []
    for tick in range(6):
      clock[0] = 3_000_000_000 + tick * 10_000_000
      stock = template(packer, ci.CC.CAN.CAM, tick)
      state = ci.update([(clock[0], physical_frames(ci, packer, tick, stock))])
      assert state.canValid and not state.canTimeout
      _, packets = ci.apply(command.as_reader(), clock[0])
      branch.append(tuple(packet for packet in packets if packet[0] == 0x12A))
    outputs.append(branch)
  assert outputs[0] == outputs[1]


def test_actual_standard_hda1_alpha_health_does_not_require_unseen_units(clock):
  cp = params(CAR.HYUNDAI_IONIQ_5, alpha=True)
  assert cp.openpilotLongitudinalControl
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for tick in range(12):
    clock[0] = 4_000_000_000 + tick * 10_000_000
    stock = template(packer, ci.CC.CAN.CAM, tick)
    frames = physical_frames(ci, packer, tick, stock)
    assert not any(packet[0] == 0x1AA for packet in frames)
    state = ci.update([(clock[0], frames)])
  assert state.canValid and not state.canTimeout


def test_stale_fault_fallback_never_copies_other_stock_bits():
  from opendbc.car.hyundai.hyundaicanfd import CanBus, LFA_FAULT_MASK, create_steering_messages
  cp = params()
  receiver = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  _, original, _ = template(receiver, 2, 193, warning=15, fault=2)
  source = bytearray(original)
  source[4] |= 0x3F
  source[8] = 0x9C
  source[9] = 0xAF
  source[10] = 0xFC
  source[:2] = hkg_can_fd_checksum(0x12A, None, source).to_bytes(2, 'little')
  args = (cp, CanBus(cp), True, True, 2)
  generated = create_steering_messages(CANPacker(DBC[cp.carFingerprint][Bus.pt]), *args)[0][1]
  actual = create_steering_messages(CANPacker(DBC[cp.carFingerprint][Bus.pt]), *args, stock_lfa_faults=bytes(source))[0][1]
  for index in range(2, 16):
    nonfault = LFA_FAULT_MASK[index] ^ 0xFF
    assert actual[index] & nonfault == generated[index] & nonfault
  assert actual[2] == 0 and source[2] == 193
  assert actual[9] == actual[14] == 0
  assert int.from_bytes(actual[:2], 'little') == hkg_can_fd_checksum(0x12A, None, bytearray(actual))


@pytest.mark.parametrize('indication', (0, 1, 2, 3))
def test_stale_warning_cannot_keep_old_green_or_blink_when_host_is_off(indication):
  from opendbc.car.hyundai.hyundaicanfd import CanBus, create_steering_messages
  cp = params()
  receiver = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  _, source, _ = receiver.make_can_msg('LFA', 2, {'LKA_SysIndReq': indication, 'LKA_SysWrn': 15, 'ToiFltSta': 2})
  packets = create_steering_messages(CANPacker(DBC[cp.carFingerprint][Bus.pt]), cp, CanBus(cp), False, False, 0,
                                      stock_lfa_faults=source)
  decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFA', 0)], CanBus(cp).ECAN)
  decoder.update([(1_000_000_000, packets)])
  assert decoder.vl['LFA']['LKA_SysIndReq'] == 1
  assert decoder.vl['LFA']['LKA_SysWrn'] == 15
  assert decoder.vl['LFA']['ToiFltSta'] == 2
  assert decoder.vl['LFA']['ActToiSta'] == 0
  assert decoder.vl['LFA']['StrTqReqVal'] == 0


def cluster(packer, bus, counter):
  address, body, _ = packer.make_can_msg('LFAHDA_CLUSTER', bus, {'COUNTER': counter, 'HDA_ICON': 1, 'LFA_ICON': 3})
  data = bytearray(body)
  data[3] |= 7
  data[4], data[7], data[14] = 3, 0xE5, 0xA5
  data[:2] = hkg_can_fd_checksum(address, None, data).to_bytes(2, 'little')
  return address, bytes(data), bus


@pytest.mark.parametrize('car', (CAR.KIA_NIRO_EV_2ND_GEN, CAR.HYUNDAI_KONA_EV_2ND_GEN, CAR.HYUNDAI_IONIQ_5))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('enabled,active', ((False, False), (False, True), (True, False), (True, True)))
def test_actual_interface_preserves_body_icons_cadence_and_identical_steering(car, alpha, alternate, enabled, active, clock):
  cp = params(car, alpha=alpha, alternate=alternate)
  expected_alpha = alpha and car in (CAR.KIA_NIRO_EV_2ND_GEN, CAR.HYUNDAI_IONIQ_5)
  assert cp.openpilotLongitudinalControl == expected_alpha
  assert cp.pcmCruise != expected_alpha
  ci, reference = CarInterface(cp), CarInterface(cp)
  ci.update([])
  reference.update([])
  packer = CANPacker(DBC[car][Bus.pt])
  bus = ci.CC.CAN
  command = structs.CarControl.new_message()
  command.enabled, command.latActive = enabled, active
  command.actuators.torque = .2
  for tick in range(10):
    clock[0] = 1_000_000_000 + tick * 10_000_000
    packets = physical_frames(ci, packer, tick, template(packer, bus.CAM, (tick + 173) % 256, warning=15, fault=2))
    assert reference.update([(clock[0], packets)]).canValid
    stock = cluster(packer, bus.CAM, 61 + tick // 5)
    if tick % 5 == 0:
      packets = packets + [stock]
    state = ci.update([(clock[0], packets)])
    assert state.canValid and not state.canTimeout
    _, own = ci.apply(command.as_reader(), clock[0])
    _, original = reference.apply(command.as_reader(), clock[0])
    assert [p for p in own if p[0] == 0x12A] == [p for p in original if p[0] == 0x12A]
    messages = [p for p in own if p[0] == 0x1E0]
    if cp.flags & HyundaiFlags.CCNC:
      assert not messages
      assert not any(p[0] in (0x161, 0x162) for p in own)
      continue
    assert len(messages) == int(tick % 5 == 0)
    if messages:
      original_cluster = next(p for p in original if p[0] == 0x1E0)
      body = messages[0][1]
      for i, mask in enumerate(CLUSTER_CONTROL_MASK):
        if i > 1:
          assert body[i] & mask == original_cluster[1][i] & mask
        assert body[i] & ~mask == stock[1][i] & ~mask
      assert body[2] == tick // 5
      assert int.from_bytes(body[:2], 'little') == hkg_can_fd_checksum(0x1E0, None, bytearray(body))


def test_mask_matches_exact_dbc_counter_checksum_and_icons():
  packer = CANPacker(DBC[CAR.KIA_NIRO_EV_2ND_GEN][Bus.pt])
  expected = bytearray(16)
  for name in ('CHECKSUM', 'COUNTER', 'HDA_ICON', 'LFA_ICON'):
    signal = packer.dbc.name_to_msg['LFAHDA_CLUSTER'].sigs[name]
    assert signal.is_little_endian
    for bit in range(signal.lsb, signal.lsb + signal.size):
      expected[bit // 8] |= 1 << (bit % 8)
  assert bytes(expected) == CLUSTER_CONTROL_MASK


@pytest.mark.parametrize('enabled', (False, True))
@pytest.mark.parametrize('icon', (0, 1, 2, 3))
def test_generated_icons_never_come_from_stock(enabled, icon):
  cp = params(CAR.KIA_NIRO_EV_2ND_GEN)
  bus = CanBus(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  stock = cluster(CANPacker(DBC[cp.carFingerprint][Bus.pt]), bus.CAM, 173)
  message = create_lfahda_cluster(packer, bus, enabled, lfa_icon=icon, stock_cluster=stock[1])
  decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFAHDA_CLUSTER', 0)], bus.ECAN)
  decoder.update([(1_000_000_000, [message])])
  assert decoder.vl['LFAHDA_CLUSTER']['HDA_ICON'] == int(enabled)
  assert decoder.vl['LFAHDA_CLUSTER']['LFA_ICON'] == icon
  assert message[1][3] & 7 == 7
  assert message[1][4] == 3
  assert message[1][7] == 0xE5
  assert message[1][14] == 0xA5
  assert message[1][2] == 0


@pytest.mark.parametrize('mutation', ('crc', 'bus', 'length', 'future', 'held', 'backward', 'counter', 'address'))
def test_bad_source_never_renews_freshness(mutation, clock):
  packer = CANPacker(DBC[CAR.KIA_NIRO_EV_2ND_GEN][Bus.pt])
  source = CANFDClusterStatus(2)
  first = cluster(packer, 2, 254)
  source.update([(clock[0], [first])])
  assert source.current(clock[0]) == first[1]
  clock[0] += 50_000_000
  later, stamp = cluster(packer, 2, 255), clock[0]
  if mutation == 'crc':
    body = bytearray(later[1])
    body[3] ^= 1
    later = later[0], bytes(body), later[2]
  elif mutation == 'bus':
    later = later[0], later[1], 0
  elif mutation == 'length':
    later = later[0], later[1][:-1], later[2]
  elif mutation == 'future':
    stamp += 1
  elif mutation == 'held':
    later = first
  elif mutation == 'backward':
    stamp -= 50_000_001
  elif mutation == 'counter':
    later = cluster(packer, 2, 0)
  elif mutation == 'address':
    later = template(packer, 2, 255)
  source.update([(stamp, [later])])
  clock[0] += 50_000_000
  assert source.current(clock[0]) == first[1]
  clock[0] += 1
  assert source.current(clock[0]) is None
  assert source.last_fault() is None


def test_multiple_frames_wrap_and_no_stale_template(clock):
  packer = CANPacker(DBC[CAR.KIA_NIRO_EV_2ND_GEN][Bus.pt])
  source = CANFDClusterStatus(2)
  first, second = cluster(packer, 2, 255), cluster(packer, 2, 0)
  source.update([(clock[0], [first, second])])
  assert source.current(clock[0]) == second[1]
  clock[0] += 100_000_001
  assert source.current(clock[0]) is None


@pytest.mark.parametrize('car,hda2', ((CAR.KIA_NIRO_EV_2ND_GEN, True), (CAR.HYUNDAI_IONIQ_5_PE, False),
                                    (CAR.KIA_CARNIVAL_2025, False)))
def test_other_protocols_do_not_acquire_source(car, hda2):
  ci = CarInterface(params(car, hda2=hda2))
  assert ci.CS.stock_cluster_status is None


@pytest.mark.parametrize('car', (CAR.KIA_EV6, CAR.KIA_NIRO_EV_2ND_GEN, CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_KONA_EV_2ND_GEN))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('mode', (0, 1, 2, 3))
@pytest.mark.parametrize('active', (False, True))
def test_actual_interface_preserves_camera_lane_safety_user_mode(car, alpha, mode, active, clock):
  cp = params(car, alpha=alpha)
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[car][Bus.pt])
  bus = ci.CC.CAN
  decoder = CANParser(DBC[car][Bus.pt], [('LFA', 0)], bus.ECAN)
  command = structs.CarControl.new_message()
  command.latActive = active
  command.enabled = bool(active and cp.openpilotLongitudinalControl)
  command.actuators.torque = .4
  for tick in range(4):
    clock[0] = 1_000_000_000 + tick * 10_000_000
    address, body, _ = template(packer, bus.CAM, (tick + 173) % 256)
    body = bytearray(body)
    body[10] = (body[10] & 0xFC) | mode
    body[:2] = hkg_can_fd_checksum(address, None, body).to_bytes(2, 'little')
    stock = address, bytes(body), bus.CAM
    state = ci.update([(clock[0], physical_frames(ci, packer, tick, stock))])
    assert state.canValid and not state.canTimeout
    _, packets = ci.apply(command.as_reader(), clock[0])
    own = [packet for packet in packets if packet[0] == 0x12A and packet[2] == bus.ECAN]
    assert len(own) == 1
    decoder.update([(clock[0], own)])
    assert decoder.vl['LFA']['LKA_UsmMod'] == mode
    assert decoder.vl['LFA']['ActToiSta'] == int(active)
    assert abs(decoder.vl['LFA']['StrTqReqVal']) <= ci.CC.params.STEER_MAX
    assert decoder.vl['LFA']['LKA_SysWrn'] == 0
    assert int.from_bytes(own[0][1][:2], 'little') == hkg_can_fd_checksum(0x12A, None, bytearray(own[0][1]))
    if not active:
      assert decoder.vl['LFA']['StrTqReqVal'] == 0


@pytest.mark.parametrize('car', (CAR.KIA_EV6, CAR.KIA_NIRO_EV_2ND_GEN, CAR.GENESIS_GV60_EV_1ST_GEN))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('preexisting', (False, True))
def test_unclassified_camera_system_fail_remains_visible_before_and_during_aol(car, alpha, preexisting, clock):
  cp = params(car, alpha=alpha)
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[car][Bus.pt])
  decoder = CANParser(DBC[car][Bus.pt], [('LFA', 0)], ci.CC.CAN.ECAN)
  command = structs.CarControl.new_message(enabled=True)
  for tick in range(6):
    clock[0] = 9_000_000_000 + tick * 10_000_000
    command.latActive = tick >= 2
    warning = 15 if preexisting or tick >= 3 else 0
    stock = template(packer, ci.CC.CAN.CAM, tick, warning=warning, fault=0)
    assert ci.update([(clock[0], physical_frames(ci, packer, tick, stock))]).canValid
    _, packets = ci.apply(command.as_reader(), clock[0])
    own = [p for p in packets if p[0] == 0x12A and p[2] == ci.CC.CAN.ECAN]
    decoder.update([(clock[0], own)])
    assert decoder.vl['LFA']['LKA_SysWrn'] == warning
    if warning:
      assert decoder.vl['LFA']['LKA_SysIndReq'] == 4
    assert decoder.vl['LFA']['ToiFltSta'] == 0
    assert decoder.vl['LFA']['ActToiSta'] == int(command.latActive)


@pytest.mark.parametrize('fresh', (False, True))
@pytest.mark.parametrize('active', (False, True))
@pytest.mark.parametrize('indication', (4, 5, 6, 7))
@pytest.mark.parametrize('warning', range(16))
@pytest.mark.parametrize('fault', range(4))
def test_fault_indication_is_never_cleared_by_lateral_request(fresh, active, indication, warning, fault):
  from opendbc.car.hyundai.hyundaicanfd import create_steering_messages
  cp = params(CAR.GENESIS_GV60_EV_1ST_GEN)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  _, body, _ = packer.make_can_msg('LFA', 2, {'LKA_SysIndReq': indication, 'LKA_SysWrn': warning,
                                          'ToiFltSta': fault, 'ActToiSta': 0, 'StrTqReqVal': -123})
  kwargs = {'stock_lfa' if fresh else 'stock_lfa_faults': body}
  own = create_steering_messages(packer, cp, CanBus(cp), active, active, 2 if active else 0, **kwargs)
  decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [('LFA', 0)], CanBus(cp).ECAN)
  decoder.update([(1_000_000_000, own)])
  values = decoder.vl['LFA']
  assert values['LKA_SysIndReq'] == indication
  assert values['LKA_SysWrn'] == warning
  assert values['ToiFltSta'] == fault
  assert values['ActToiSta'] == int(active)
  assert values['StrTqReqVal'] == (2 if active else 0)
  assert int.from_bytes(own[0][1][:2], 'little') == hkg_can_fd_checksum(0x12A, None, bytearray(own[0][1]))


@pytest.mark.parametrize('car', (CAR.KIA_EV6, CAR.KIA_NIRO_EV_2ND_GEN, CAR.GENESIS_GV60_EV_1ST_GEN))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('clear_camera', (False, True))
def test_actual_verified_owner_keeps_display_clean_through_release(car, alpha, offset, clear_camera, clock):
  fp = gen_empty_fingerprint()
  fp[offset][0x1CF] = 8
  fp[offset + 2][0x1A0] = 32
  cp = CarInterface.get_params(car, fp, [], alpha, False, False)
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[car][Bus.pt])
  decoder = CANParser(DBC[car][Bus.pt], [('LFA', 0)], ci.CC.CAN.ECAN)
  command = structs.CarControl.new_message()
  command.actuators.torque = 0.2
  for tick in range(230):
    clock[0] = 10_000_000_000 + tick * 10_000_000
    command.enabled = command.latActive = 2 <= tick < 30
    warning = 15 if tick >= 14 and not (clear_camera and tick >= 214) else 0
    stock = template(packer, ci.CC.CAN.CAM, tick // 2 % 256, warning=warning)
    frames = physical_frames(ci, packer, tick, stock)
    frames = [p for p in frames if not (p[0] == 0xEA and p[2] == ci.CC.CAN.ECAN)]
    frames.append(packer.make_can_msg('MDPS', ci.CC.CAN.ECAN, {'COUNTER': tick % 256, 'MDPS_LkaToiActvSta': int(4 <= tick < 32)}))
    if tick % 2:
      frames = [p for p in frames if not (p[0] == 0x12A and p[2] == ci.CC.CAN.CAM)]
    assert ci.update([(clock[0], frames)]).canValid
    _, packets = ci.apply(command.as_reader(), clock[0])
    own = [p for p in packets if p[0] == 0x12A and p[2] == ci.CC.CAN.ECAN]
    decoder.update([(clock[0], own)])
    expired = not clear_camera and tick > 224
    assert decoder.vl['LFA']['LKA_SysWrn'] == (15 if expired else 0)
    assert decoder.vl['LFA']['ToiFltSta'] == 0
    assert decoder.vl['LFA']['ActToiSta'] == int(command.latActive)
    if not command.latActive:
      assert decoder.vl['LFA']['StrTqReqVal'] == 0
    if warning and not expired and not command.latActive:
      assert decoder.vl['LFA']['LKA_SysIndReq'] == 1


@pytest.mark.parametrize(
  'failure', ('preexisting', 'late', 'no_eps', 'eps_fault', 'eps_unavailable', 'eps_crc', 'eps_counter', 'wrong_bus', 'camera_active', 'host_fault')
)
def test_owner_classifier_preserves_unproven_and_genuine_failures(failure, clock):
  packer = CANPacker(DBC[CAR.KIA_NIRO_EV_2ND_GEN][Bus.pt])
  source = CANFDLfaStatus(2)
  qualified = []
  for tick in range(40):
    clock[0] = 20_000_000_000 + tick * 10_000_000
    request = tick >= 2
    fault_tick = 30 if failure == 'late' else 14
    warning = failure == 'preexisting' or tick >= fault_tick
    _, camera, _ = template(packer, 2, tick % 256, warning=15 if warning else 0)
    if failure == 'camera_active':
      body = bytearray(camera)
      body[6] |= 0x10
      body[:2] = hkg_can_fd_checksum(0x12A, None, body).to_bytes(2, 'little')
      camera = bytes(body)
    values = {'COUNTER': tick % 256, 'MDPS_LkaToiActvSta': int(tick >= 4 and failure != 'no_eps')}
    if failure == 'eps_fault':
      values['MDPS_LkaFailSta'] = 1
    if failure == 'eps_unavailable':
      values['MDPS_LkaToiUnblSta'] = 1
    if failure == 'eps_counter':
      values['COUNTER'] = 0
    eps = packer.make_can_msg('MDPS', 1 if failure == 'wrong_bus' else 0, values)
    if failure == 'eps_crc':
      b = bytearray(eps[1])
      b[0] ^= 1
      eps = eps[0], bytes(b), eps[2]
    source.update([(clock[0], [eps, (0x12A, camera, 2)])])
    qualified.append(source.owns_display_status(request, clock[0], healthy=failure != 'host_fault'))
  assert not any(qualified)


@pytest.mark.parametrize('failure', ('host_fault', 'eps_fault', 'stale_eps', 'stale_camera', 'camera_fault', 'higher_grade'))
def test_established_owner_immediately_yields_to_faults_and_stale_evidence(failure, clock):
  packer = CANPacker(DBC[CAR.KIA_NIRO_EV_2ND_GEN][Bus.pt])
  source = CANFDLfaStatus(2)
  for tick in range(16):
    clock[0] = 30_000_000_000 + tick * 10_000_000
    camera = template(packer, 2, tick, warning=15 if tick >= 14 else 0)
    eps = packer.make_can_msg('MDPS', 0, {'COUNTER': tick, 'MDPS_LkaToiActvSta': int(tick >= 4)})
    source.update([(clock[0], [eps, camera])])
    owned = source.owns_display_status(tick >= 2, clock[0], healthy=True)
    popup_owned = source.owns_disengage_popup(tick >= 2, clock[0], healthy=True, qualified_warning=owned)
  assert owned and popup_owned
  for tick in range(16, 23):
    clock[0] += 10_000_000
    camera = template(packer, 2, tick, warning=15, fault=int(failure == 'camera_fault'))
    if failure == 'higher_grade':
      b = bytearray(camera[1])
      b[4] = (b[4] & 0x3F) | 0x40
      b[5] |= 1
      b[:2] = hkg_can_fd_checksum(0x12A, None, b).to_bytes(2, 'little')
      camera = camera[0], bytes(b), camera[2]
    eps = packer.make_can_msg('MDPS', 0, {'COUNTER': tick, 'MDPS_LkaToiActvSta': 1, 'MDPS_LkaFailSta': int(failure == 'eps_fault')})
    frames = [] if failure == 'stale_eps' else [eps]
    if failure != 'stale_camera':
      frames.append(camera)
    source.update([(clock[0], frames)])
    owned = source.owns_display_status(True, clock[0], healthy=failure != 'host_fault')
    popup_owned = source.owns_disengage_popup(True, clock[0], healthy=failure != 'host_fault', qualified_warning=owned)
    if failure not in ('stale_eps', 'stale_camera') or tick >= 21:
      assert not owned
      assert not popup_owned
  assert not source.owns_display_status(False, clock[0], healthy=True)


@pytest.mark.parametrize('popup', range(8))
@pytest.mark.parametrize('owned', (False, True))
def test_cluster_clears_only_automatic_disengagement_for_verified_owner(popup, owned):
  cp = params()
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  _, body, _ = cluster(packer, 2, 10)
  data = bytearray(body)
  data[4] = (data[4] & 0xF8) | popup
  data[:2] = hkg_can_fd_checksum(0x1E0, None, data).to_bytes(2, 'little')
  original = create_lfahda_cluster(CANPacker(DBC[cp.carFingerprint][Bus.pt]), CanBus(cp), True, stock_cluster=bytes(data))[1]
  result = create_lfahda_cluster(CANPacker(DBC[cp.carFingerprint][Bus.pt]), CanBus(cp), True, stock_cluster=bytes(data), owned_lfa_status=owned)[1]
  assert result[4] & 7 == (0 if owned and popup == 3 else popup)
  allowed = (7 << 32) | 65535
  assert not (int.from_bytes(original, 'little') ^ int.from_bytes(result, 'little')) & ~allowed
  assert int.from_bytes(result[:2], 'little') == hkg_can_fd_checksum(0x1E0, None, bytearray(result))


@pytest.mark.parametrize('car', (CAR.KIA_NIRO_EV_2ND_GEN, CAR.KIA_EV6, CAR.GENESIS_GV60_EV_1ST_GEN))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('popup', (3, 4, 5, 6, 7))
def test_actual_startup_popup_ownership_preserves_failures_and_hands_off(car, alpha, popup, clock):
  cp = params(car, alpha=alpha)
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[car][Bus.pt])
  observed = []
  for tick in range(45):
    clock[0] = 40_000_000_000 + tick * 10_000_000
    command = structs.CarControl.new_message(enabled=tick >= 2, latActive=tick >= 2)
    stock = template(packer, ci.CC.CAN.CAM, tick % 256, warning=15 if tick >= 14 else 0)
    frames = physical_frames(ci, packer, tick, stock)
    frames = [p for p in frames if not (p[0] == 0xEA and p[2] == ci.CC.CAN.ECAN)]
    frames.append(packer.make_can_msg('MDPS', ci.CC.CAN.ECAN,
                                    {'COUNTER': tick, 'MDPS_LkaToiActvSta': int(tick >= 4)}))
    if tick % 5 == 0:
      _, body, _ = cluster(packer, ci.CC.CAN.CAM, tick // 5)
      b = bytearray(body)
      b[4] = (b[4] & 0xF8) | (popup if tick >= 15 else 0)
      b[:2] = hkg_can_fd_checksum(0x1E0, None, b).to_bytes(2, 'little')
      frames.append((0x1E0, bytes(b), ci.CC.CAN.CAM))
    assert ci.update([(clock[0], frames)]).canValid
    _, packets = ci.apply(command.as_reader(), clock[0])
    for address, body, bus in packets:
      if address == 0x1E0 and bus == ci.CC.CAN.ECAN and tick >= 15:
        observed.append(body[4] & 7)
  assert observed and all(v == (0 if popup == 3 else popup) for v in observed)


@pytest.mark.parametrize('car', (CAR.HYUNDAI_KONA_2ND_GEN, CAR.HYUNDAI_KONA_HEV_2ND_GEN, CAR.HYUNDAI_KONA_EV_2ND_GEN,
                                CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_IONIQ_6, CAR.KIA_EV6,
                                CAR.KIA_NIRO_EV_2ND_GEN, CAR.GENESIS_GV60_EV_1ST_GEN))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('damping', (0, 37, 100, 255))
def test_actual_hda1_sender_preserves_fresh_camera_damping(car, alpha, damping, clock):
  cp = params(car, alpha=alpha)
  assert preserves_stock_lfa(cp)
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[car][Bus.pt])
  command = structs.CarControl.new_message(enabled=True, latActive=True)
  command.actuators.torque = .4
  for tick in range(6):
    clock[0] = 5_000_000_000 + tick * 10_000_000
    address, original, bus = template(packer, ci.CC.CAN.CAM, tick)
    body = bytearray(original)
    body[13] = damping
    body[:2] = hkg_can_fd_checksum(address, None, body).to_bytes(2, 'little')
    stock = address, bytes(body), bus
    state = ci.update([(clock[0], physical_frames(ci, packer, tick, stock))])
    assert state.canValid and not state.canTimeout
    _, packets = ci.apply(command.as_reader(), clock[0])
    outgoing = [raw for address, raw, bus in packets if address == 0x12A and bus == ci.CC.CAN.ECAN]
    assert len(outgoing) == 1
    assert outgoing[0][13] == damping
    assert int.from_bytes(outgoing[0][:2], 'little') == hkg_can_fd_checksum(0x12A, None, bytearray(outgoing[0]))


def test_stale_camera_damping_does_not_replace_sender_fallback():
  from opendbc.car.hyundai.hyundaicanfd import create_steering_messages
  cp = params()
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  _, body, _ = packer.make_can_msg('LFA', 2, {'Damping_Gain': 37, 'ToiFltSta': 2})
  for active in (False, True):
    args = (packer, cp, CanBus(cp), active, active, 0)
    missing = create_steering_messages(*args)[0][1]
    stale = create_steering_messages(*args, stock_lfa_faults=body)[0][1]
    assert missing[13] == stale[13] == 100
    assert stale[6] & 0xC0 == body[6] & 0xC0


@pytest.mark.parametrize('car', (CAR.HYUNDAI_KONA_EV_2ND_GEN, CAR.HYUNDAI_KONA_2ND_GEN,
                                CAR.HYUNDAI_KONA_HEV_2ND_GEN, CAR.HYUNDAI_IONIQ_5,
                                CAR.KIA_EV6, CAR.KIA_NIRO_EV_2ND_GEN,
                                CAR.GENESIS_GV60_EV_1ST_GEN, CAR.HYUNDAI_SONATA_2024))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
def test_actual_handoff_preserves_order_of_healthy_and_fault_frames_in_one_batch(car, alpha, offset, clock):
  fp = gen_empty_fingerprint()
  fp[offset][0x1CF] = 8
  fp[offset + 2][0x1A0] = 32
  cp = CarInterface.get_params(car, fp, [], alpha, False, False)
  ci = CarInterface(cp)
  ci.update([])
  assert ci.CS.stock_lfa_status is not None
  packer = CANPacker(DBC[car][Bus.pt])
  decoder = CANParser(DBC[car][Bus.pt], [('LFA', 0)], ci.CC.CAN.ECAN)
  command = structs.CarControl.new_message()
  command.actuators.torque = .2
  for tick in range(20):
    clock[0] = 70_000_000_000 + tick * 10_000_000
    command.latActive = tick >= 2
    command.enabled = bool(command.latActive and cp.openpilotLongitudinalControl)
    warning = tick >= 14
    camera = packer.make_can_msg('LFA', ci.CC.CAN.CAM,
                                {'COUNTER': tick + int(tick > 14), 'LKA_SysIndReq': 4 if warning and tick != 14 else 1,
                                 'LKA_SysWrn': 15 if warning and tick != 14 else 0,
                                 'ActToiSta': 0, 'StrTqReqVal': -123})
    frames = [p for p in physical_frames(ci, packer, tick, camera)
              if not (p[0] == 0xEA and p[2] == ci.CC.CAN.ECAN)]
    if tick == 14:
      frames.append(packer.make_can_msg('LFA', ci.CC.CAN.CAM,
                                       {'COUNTER': 15, 'LKA_SysIndReq': 4, 'LKA_SysWrn': 15}))
    frames.append(packer.make_can_msg('MDPS', ci.CC.CAN.ECAN,
                                     {'COUNTER': tick, 'MDPS_LkaToiActvSta': int(tick >= 4)}))
    state = ci.update([(clock[0], frames)])
    assert state.canValid and not state.canTimeout
    _, packets = ci.apply(command.as_reader(), clock[0])
    own = [p for p in packets if p[0] == 0x12A and p[2] == ci.CC.CAN.ECAN]
    assert len(own) == 1
    decoder.update([(clock[0], own)])
    values = decoder.vl['LFA']
    assert values['ActToiSta'] == int(command.latActive)
    assert values['StrTqReqVal'] != -123
    assert values['ToiFltSta'] == 0
    assert values['LKA_SysWrn'] == 0
    if not command.latActive:
      assert values['StrTqReqVal'] == 0
    if tick >= 4:
      source = ci.CS.stock_lfa_status
      owned = source.owns_display_status(True, clock[0], healthy=True)
      assert source.owns_disengage_popup(True, clock[0], healthy=True, qualified_warning=owned)
    if warning:
      assert values['LKA_SysIndReq'] == 2
    body = own[0][1]
    assert int.from_bytes(body[:2], 'little') == hkg_can_fd_checksum(0x12A, None, bytearray(body))


@pytest.mark.parametrize('failure', ('none', 'bad_crc', 'counter', 'camera_fault', 'higher_grade', 'warning',
                                   'no_predecessor', 'fault_first', 'wrong_bus', 'eps_fault', 'eps_lamp',
                                   'eps_unavailable', 'host_fault', 'reserved_request'))
@pytest.mark.parametrize('wrap', (False, True))
def test_same_batch_ownership_requires_valid_ordered_healthy_predecessor(failure, wrap, clock):
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_EV_2ND_GEN][Bus.pt])
  source = CANFDLfaStatus(2)
  counter_start = 241 if wrap else 0
  for tick in range(15):
    clock[0] = 80_000_000_000 + tick * 10_000_000
    counter = (counter_start + tick) % 256
    camera = packer.make_can_msg('LFA', 2, {'COUNTER': counter, 'LKA_SysIndReq': 1,
                                         'ActToiSta': int(tick >= 9)})
    eps_values = {'COUNTER': tick, 'MDPS_LkaToiActvSta': int(tick >= 4),
                  'MDPS_LkaFailSta': int(failure == 'eps_fault'),
                  'MDPS_WrngLmpSta': 3 if failure == 'eps_lamp' else 0,
                  'MDPS_LkaToiUnblSta': int(failure == 'eps_unavailable')}
    eps = packer.make_can_msg('MDPS', 0, eps_values)
    if tick == 14:
      predecessor = packer.make_can_msg('LFA', 1 if failure == 'wrong_bus' else 2,
                                       {'COUNTER': counter, 'LKA_SysIndReq': 5 if failure == 'higher_grade' else 1,
                                        'LKA_SysWrn': 15 if failure == 'warning' else 0,
                                        'ToiFltSta': int(failure == 'camera_fault'),
                                        'ActToiSta': 2 if failure == 'reserved_request' else 0})
      if failure == 'bad_crc':
        body = bytearray(predecessor[1])
        body[0] ^= 1
        predecessor = predecessor[0], bytes(body), predecessor[2]
      fault = packer.make_can_msg('LFA', 2, {'COUNTER': (counter + (failure != 'counter')) % 256,
                                           'LKA_SysIndReq': 4, 'LKA_SysWrn': 15})
      frames = [eps, fault] if failure == 'no_predecessor' else [eps, fault, predecessor] if failure == 'fault_first' else [eps, predecessor, fault]
    else:
      frames = [eps, camera]
    source.update([(clock[0], frames)])
    owned = source.owns_display_status(tick >= 2, clock[0], healthy=failure != 'host_fault')
  assert owned == (failure == 'none')
  assert not source.owns_display_status(False, clock[0] + 3_000_000_000, healthy=True)


@pytest.mark.parametrize('car', (CAR.HYUNDAI_KONA_EV_2ND_GEN, CAR.HYUNDAI_KONA_2ND_GEN, CAR.HYUNDAI_KONA_HEV_2ND_GEN,
                                CAR.HYUNDAI_SANTA_CRUZ_2025, CAR.HYUNDAI_SONATA_2024,
                                CAR.HYUNDAI_SONATA_HEV_2024, CAR.HYUNDAI_TUCSON_2025,
                                CAR.HYUNDAI_TUCSON_HEV_2025, CAR.KIA_K5_2025))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('failure', ('none', 'host_fault', 'eps_fault', 'camera_fault', 'higher_grade'))
def test_actual_ccnc_handover_keeps_owned_warning_quiet_and_real_faults_visible(car, alpha, failure, clock):
  cp = params(car, alpha=alpha)
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[car][Bus.pt])
  bus = ci.CC.CAN
  decoder = CANParser(DBC[car][Bus.pt], [('CCNC_0x161', 0), ('CCNC_0x162', 0)], bus.ECAN)
  command = structs.CarControl.new_message()
  fields = ('FAULT_LSS', 'FAULT_HDA', 'FAULT_DAS', 'FAULT_LFA', 'FAULT_DAW')
  seen = 0
  for tick in range(100):
    clock[0] = 40_000_000_000 + tick * 10_000_000
    command.enabled = command.latActive = 2 <= tick < 30
    faulted = failure != 'none' and tick >= 40
    stock = template(packer, bus.CAM, tick % 256, warning=15 if 14 <= tick < 75 else 0,
                     fault=int(faulted and failure == 'camera_fault'))
    if faulted and failure == 'higher_grade':
      data = bytearray(stock[1])
      data[4], data[5] = (data[4] & 0x3F) | 0x40, data[5] | 1
      data[:2] = hkg_can_fd_checksum(0x12A, None, data).to_bytes(2, 'little')
      stock = stock[0], bytes(data), stock[2]
    frames = [p for p in physical_frames(ci, packer, tick, stock) if not (p[0] == 0xEA and p[2] == bus.ECAN)]
    frames.append(packer.make_can_msg('MDPS', bus.ECAN, {'COUNTER': tick % 256,
                  'MDPS_LkaToiActvSta': int(4 <= tick < 32), 'MDPS_LkaFailSta': int(faulted and failure == 'eps_fault')}))
    if tick % 5 == 0:
      faults = dict.fromkeys(fields, int(14 <= tick < 80))
      frames.extend([packer.make_can_msg('CCNC_0x161', bus.CAM, {'COUNTER': tick // 5 % 256}),
                     packer.make_can_msg('CCNC_0x162', bus.CAM, {'COUNTER': tick // 5 % 256, 'FAULT_FSS': 2, **faults})])
    state = ci.update([(clock[0], frames)])
    assert state.canValid
    if faulted and failure == 'host_fault':
      ci.CS.out.accFaulted = True
    _, packets = ci.apply(command.as_reader(), clock[0])
    display = [p for p in packets if p[0] in (0x161, 0x162)]
    if not display:
      continue
    seen += 1
    decoder.update([(clock[0], display)])
    for field in fields:
      assert decoder.vl['CCNC_0x162'][field] == int(faulted and 14 <= tick < 80), (tick, failure, field)
    assert decoder.vl['CCNC_0x162']['FAULT_FSS'] == 2
    assert decoder.vl['CCNC_0x161']['LFA_ICON'] == (2 if command.latActive else 0)
    assert decoder.vl['CCNC_0x161']['CENTERLINE'] == int(command.latActive)
  assert seen == 19


@pytest.mark.parametrize('failure', ('expired', 'backward', 'negative', 'host_fault', 'eps_fault', 'stale_eps', 'stale_camera', 'stale_camera_qualified',
                                    'camera_crc', 'camera_fault', 'higher_grade', 'unqualified_warning'))
def test_ccnc_normal_camera_handover_requires_current_health_and_expires(failure, clock):
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_2ND_GEN][Bus.pt])
  source = CANFDLfaStatus(2)
  for tick in range(16):
    clock[0] = 50_000_000_000 + tick * 10_000_000
    camera = template(packer, 2, tick, warning=15 if tick >= 14 else 0)
    eps = packer.make_can_msg('MDPS', 0, {'COUNTER': tick, 'MDPS_LkaToiActvSta': int(tick >= 4)})
    source.update([(clock[0], [eps, camera])])
    owned = source.owns_display_status(tick >= 2, clock[0], healthy=True)
    assert source.owns_ccnc_fault_status(clock[0], display_sample_ns=clock[0], healthy=True, qualified_warning=owned) == owned
  assert owned
  final_owned_ns = clock[0]
  for tick in range(16, 23):
    clock[0] += 10_000_000
    warning = 15 if failure == 'unqualified_warning' else 0
    camera = template(packer, 2, tick, warning=warning, fault=int(failure == 'camera_fault'))
    if failure in ('camera_crc', 'higher_grade'):
      data = bytearray(camera[1])
      if failure == 'camera_crc':
        data[0] ^= 1
      else:
        data[4], data[5] = (data[4] & 0x3F) | 0x40, data[5] | 1
        data[:2] = hkg_can_fd_checksum(0x12A, None, data).to_bytes(2, 'little')
      camera = camera[0], bytes(data), camera[2]
    eps = packer.make_can_msg('MDPS', 0, {'COUNTER': tick, 'MDPS_LkaFailSta': int(failure == 'eps_fault')})
    frames = [] if failure == 'stale_eps' else [eps]
    if failure not in ('stale_camera', 'stale_camera_qualified'):
      frames.append(camera)
    source.update([(clock[0], frames)])
    now = final_owned_ns - 1 if failure == 'backward' else -1 if failure == 'negative' else clock[0]
    held = source.owns_ccnc_fault_status(now, display_sample_ns=clock[0], healthy=failure != 'host_fault',
                                         qualified_warning=failure == 'stale_camera_qualified')
    if failure == 'expired':
      assert held == (clock[0] - final_owned_ns <= 50_000_000)
    elif failure in ('stale_eps', 'stale_camera', 'stale_camera_qualified') and tick < 21:
      continue
    else:
      assert not held
  for tick in range(23, 27):
    clock[0] += 10_000_000
    camera = template(packer, 2, tick)
    eps = packer.make_can_msg('MDPS', 0, {'COUNTER': tick})
    source.update([(clock[0], [eps, camera])])
    assert not source.owns_ccnc_fault_status(clock[0], display_sample_ns=clock[0], healthy=True, qualified_warning=False)


def test_ccnc_handover_uses_fresh_display_sample_time(clock):
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_2ND_GEN][Bus.pt])
  source = CANFDLfaStatus(2)
  for tick in range(21):
    clock[0] = 51_000_000_000 + tick * 10_000_000
    camera = template(packer, 2, tick, warning=15 if 14 <= tick < 16 else 0)
    eps = packer.make_can_msg('MDPS', 0, {'COUNTER': tick, 'MDPS_LkaToiActvSta': int(tick >= 4)})
    source.update([(clock[0], [eps, camera])])
    owned = source.owns_display_status(tick >= 2, clock[0], healthy=True)
    if owned:
      assert source.owns_ccnc_fault_status(clock[0], display_sample_ns=clock[0], healthy=True, qualified_warning=True)

  assert not owned
  now = clock[0] + 1_000_000
  assert source.owns_ccnc_fault_status(now, display_sample_ns=clock[0] - 40_000_000, healthy=True)
  assert not source.owns_ccnc_fault_status(now, display_sample_ns=clock[0] - 50_000_000, healthy=True)
  assert not source.owns_ccnc_fault_status(now, display_sample_ns=source.ccnc_last_owned_ns - 1, healthy=True)
  assert not source.owns_ccnc_fault_status(now, display_sample_ns=now, healthy=True)
  assert not source.owns_ccnc_fault_status(now, display_sample_ns=now + 1, healthy=True)
  assert not source.owns_ccnc_fault_status(now, display_sample_ns=clock[0] - 40_000_000, healthy=False)


@pytest.mark.parametrize('fault', range(1, 8))
@pytest.mark.parametrize('owned', (False, True))
def test_ccnc_fault_handover_preserves_obstruction_and_unknown_codes(fault, owned):
  from opendbc.car.hyundai.hyundaicanfd import create_ccnc
  cp = params(CAR.HYUNDAI_KONA_2ND_GEN)
  bus = CanBus(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  fields = ('FAULT_LSS', 'FAULT_HDA', 'FAULT_DAS', 'FAULT_LFA', 'FAULT_DAW')
  original = dict.fromkeys(fields, fault)
  original.update({'COUNTER': 173, 'FAULT_FCA': 1, 'FAULT_FSS': 2, 'FAULT_ESS': 1, 'FAULT_SCC': 3})
  messages = create_ccnc(packer, bus, False, False, None, False, False, {'COUNTER': 172}, original.copy(),
                         None, True, None, False, False, owned_lfa_status=owned)
  decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [('CCNC_0x161', 0), ('CCNC_0x162', 0)], bus.ECAN)
  decoder.update([(1_000_000_000, messages)])
  for field in fields:
    assert decoder.vl['CCNC_0x162'][field] == (0 if owned and fault == 1 else fault)
  for field in ('COUNTER', 'FAULT_FCA', 'FAULT_FSS', 'FAULT_ESS', 'FAULT_SCC'):
    assert decoder.vl['CCNC_0x162'][field] == original[field]
  assert decoder.vl['CCNC_0x161']['LFA_ICON'] == decoder.vl['CCNC_0x161']['CENTERLINE'] == 0
