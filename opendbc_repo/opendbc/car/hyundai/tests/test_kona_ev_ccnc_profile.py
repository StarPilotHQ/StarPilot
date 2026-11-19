import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_stock_aol import qualified
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests.test_canfd_lfa_status import clock as clock
from opendbc.car.hyundai.tests.test_canfd_lfa_status import physical_frames, template
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags
from opendbc.safety.tests.libsafety import libsafety_py


def kona_params(*, pair='both', alpha=False, alternate=False, offset=0, hda2=False):
  fp = gen_empty_fingerprint()
  ecan = offset + int(hda2)
  fp[ecan][0x1AA if alternate else 0x1CF] = 16 if alternate else 8
  fp[offset + 2][0x1A0] = 32
  bus = ecan if pair == 'ecan' else offset + 2
  if pair not in ('none', 'missing_161'):
    fp[bus][0x161] = 16 if pair == 'short_161' else 32
  if pair not in ('none', 'missing_162'):
    fp[bus][0x162] = 16 if pair == 'short_162' else 32
  if hda2:
    fp[offset + 2][0x50] = 16
  return CarInterface.get_params(CAR.HYUNDAI_KONA_EV_2ND_GEN, fp, [], alpha, False, False)


@pytest.mark.parametrize('pair', ('both', 'none', 'missing_161', 'missing_162', 'short_161', 'short_162', 'ecan'))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
def test_factory_restores_ccnc_profile_for_kona_ev(pair, alpha, alternate, offset):
  cp = kona_params(pair=pair, alpha=alpha, alternate=alternate, offset=offset)
  assert cp.flags & HyundaiFlags.CCNC
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl
  assert not cp.alphaLongitudinalAvailable
  expected = 0x409 | (0x20 if alternate else 0)
  assert cp.safetyConfigs[-1].safetyParam & ~0x800 == expected
  assert qualified(cp)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  assert qualified(cp, marked_only=True)
  assert CanBus(cp).ECAN == offset


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('alternate', (False, True))
def test_hda2_kona_profile_is_unchanged(alpha, alternate):
  original = kona_params(pair='none', alpha=alpha, alternate=alternate, hda2=True)
  observed = kona_params(alpha=alpha, alternate=alternate, hda2=True)
  assert observed.flags & HyundaiFlags.CCNC
  assert observed.flags == original.flags
  assert [c.to_dict() for c in observed.safetyConfigs] == [c.to_dict() for c in original.safetyConfigs]


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('failure', ('none', 'eps_fault', 'host_fault', 'camera_fault', 'higher_grade', 'obstruction', 'missing_tracker'))
def test_actual_kona_ccnc_sender_uses_qualified_ownership_and_preserves_faults(alpha, failure, clock):
  cp = kona_params(alpha=alpha)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_EV_2ND_GEN][Bus.pt])
  bus = ci.CC.CAN
  decoder = CANParser(DBC[CAR.HYUNDAI_KONA_EV_2ND_GEN][Bus.pt], [('CCNC_0x161', 0), ('CCNC_0x162', 0)], bus.ECAN)
  command = structs.CarControl.new_message()
  seen = 0
  for tick in range(100):
    clock[0] = 90_000_000_000 + tick * 10_000_000
    command.latActive = 2 <= tick < 80
    faulted = tick >= 40 and failure != 'none'
    stock = template(packer, bus.CAM, tick, warning=15 if tick >= 14 else 0,
                     fault=int(faulted and failure == 'camera_fault'))
    if faulted and failure == 'higher_grade':
      stock = packer.make_can_msg('LFA', bus.CAM, {'COUNTER': tick, 'LKA_SysIndReq': 5, 'LKA_SysWrn': 15})
    frames = [p for p in physical_frames(ci, packer, tick, stock) if not (p[0] == 0xEA and p[2] == bus.ECAN)]
    frames.append(packer.make_can_msg('MDPS', bus.ECAN, {'COUNTER': tick, 'MDPS_LkaToiActvSta': int(4 <= tick < 82),
                                                     'MDPS_LkaFailSta': int(faulted and failure == 'eps_fault')}))
    if tick % 5 == 0:
      warning = (2 if faulted and failure == 'obstruction' else 1) if tick >= 15 else 0
      frames.extend([packer.make_can_msg('CCNC_0x161', bus.CAM, {'COUNTER': tick // 5}),
                     packer.make_can_msg('CCNC_0x162', bus.CAM, {'COUNTER': tick // 5,
                       'FAULT_LSS': warning, 'FAULT_DAS': warning, 'FAULT_FCA': 2})])
    assert ci.update([(clock[0], frames)]).canValid
    if faulted and failure == 'host_fault':
      ci.CS.out.accFaulted = True
    if faulted and failure == 'missing_tracker':
      ci.CS.stock_lfa_status = None
    _, packets = ci.apply(command.as_reader(), clock[0])
    display = [p for p in packets if p[0] in (0x161, 0x162)]
    if not display:
      continue
    seen += 1
    decoder.update([(clock[0], display)])
    expected = (2 if failure == 'obstruction' else 1) if faulted else 0
    assert decoder.vl['CCNC_0x162']['FAULT_LSS'] == expected
    assert decoder.vl['CCNC_0x162']['FAULT_DAS'] == expected
    assert decoder.vl['CCNC_0x162']['FAULT_FCA'] == 2
    assert decoder.vl['CCNC_0x161']['LFA_ICON'] == (2 if command.latActive else 0)
  assert seen == 19


@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('aol', (False, True))
def test_selected_ccnc_native_profile_owns_only_supported_frames(alternate, aol):
  cp = kona_params(alternate=alternate)
  if aol:
    cp.safetyConfigs[-1].safetyParam |= 0x800
  word = cp.safetyConfigs[-1].safetyParam
  assert word == (0x429 if alternate else 0x409) | (0x800 if aol else 0)
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, word) == 0
  safety.init_tests()
  packer = CANPacker('hyundai_canfd_generated')
  for name in ('CCNC_0x161', 'CCNC_0x162'):
    address, body, bus = packer.make_can_msg(name, 0, {})
    assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, body))
    assert safety.safety_fwd_hook(2, address) == -1
  assert safety.safety_fwd_hook(2, 0x1A0) == 0
  assert safety.safety_fwd_hook(0, 0xEA) == 2
  address, body, bus = packer.make_can_msg('SCC_CONTROL', 0, {'ACCMode': 1, 'aReqValue': 1., 'aReqRaw': 1.})
  assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, body))


@pytest.mark.parametrize('missing', (0x161, 0x162))
def test_kona_ccnc_missing_pair_does_not_emit_legacy_or_empty_display(missing, clock):
  cp = kona_params()
  ci = CarInterface(cp)
  ci.update([])
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_EV_2ND_GEN][Bus.pt])
  command = structs.CarControl.new_message(latActive=True)
  for tick in range(20):
    clock[0] = 95_000_000_000 + tick * 10_000_000
    frames = physical_frames(ci, packer, tick, template(packer, ci.CC.CAN.CAM, tick))
    name = 'CCNC_0x162' if missing == 0x161 else 'CCNC_0x161'
    frames.append(packer.make_can_msg(name, ci.CC.CAN.CAM, {'COUNTER': tick}))
    assert ci.update([(clock[0], frames)]).canValid
    _, packets = ci.apply(command.as_reader(), clock[0])
    assert any(p[0] == 0x12A for p in packets)
    assert not any(p[0] in (0x161, 0x162, 0x1E0) for p in packets)


@pytest.mark.parametrize('alpha', (False, True))
def test_ccnc_selection_keeps_all_steering_packets_identical(alpha, clock):
  cp = kona_params(alpha=alpha)
  cp.safetyConfigs[-1].safetyParam |= 0x800
  legacy = cp.as_reader().as_builder()
  legacy.flags = int(legacy.flags) & ~int(HyundaiFlags.CCNC)
  legacy.safetyConfigs[-1].safetyParam &= ~0x400
  current, previous = CarInterface(cp), CarInterface(legacy)
  current.update([])
  previous.update([])
  packer = CANPacker(DBC[CAR.HYUNDAI_KONA_EV_2ND_GEN][Bus.pt])
  command = structs.CarControl.new_message(latActive=True)
  for tick in range(300):
    clock[0] = 96_000_000_000 + tick * 10_000_000
    command.actuators.torque = .2 if tick < 150 else -.2
    stock = template(packer, current.CC.CAN.CAM, tick % 256, warning=15 if tick >= 14 else 0)
    frames = [p for p in physical_frames(current, packer, tick, stock) if p[0] not in (0xEA, 0x125)]
    frames.extend([packer.make_can_msg('MDPS', 0, {'COUNTER': tick % 256, 'MDPS_LkaToiActvSta': int(tick >= 4)}),
                   packer.make_can_msg('STEERING_SENSORS', 0, {'COUNTER': tick % 256, 'STEERING_ANGLE': 95. if tick < 245 else -95.})])
    if tick % 5 == 0:
      frames.extend([packer.make_can_msg('CCNC_0x161', 2, {'COUNTER': tick // 5}),
                     packer.make_can_msg('CCNC_0x162', 2, {'COUNTER': tick // 5, 'FAULT_DAS': int(tick >= 15)})])
    assert current.update([(clock[0], frames)]).canValid
    assert previous.update([(clock[0], frames)]).canValid
    _, actual = current.apply(command.as_reader(), clock[0])
    _, before = previous.apply(command.as_reader(), clock[0])
    assert [p for p in actual if p[0] == 0x12A] == [p for p in before if p[0] == 0x12A]
