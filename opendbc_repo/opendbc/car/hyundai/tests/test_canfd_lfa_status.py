import time

import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_lfa_status import CANFDClusterStatus, CANFDLfaStatus, preserves_stock_lfa
from opendbc.car.hyundai.hyundaicanfd import CLUSTER_CONTROL_MASK, CanBus, create_lfahda_cluster, hkg_can_fd_checksum
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC


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
           'StrTqReqVal', 'ActToiSta', 'Damping_Gain')
  for name in owned:
    signal = signals[name]
    assert signal.is_little_endian
    for bit in range(signal.lsb, signal.lsb + signal.size):
      expected[bit // 8] |= 1 << (bit % 8)
  assert bytes(expected) == LFA_CONTROL_MASK
  for name in ('LKA_UsmMod', 'ToiFltSta', 'LKA_SysWrn', 'LKA_OnOffEquip2Sta', 'ELK_SysFlrSta', 'FCA_ESA_WrnSta'):
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
