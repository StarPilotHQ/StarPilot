"""Polestar primary-angle activation through the production parser and controller."""
import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.volvo.cma_can import create_lca_5_message
from opendbc.car.volvo.interface import CarInterface
from opendbc.car.volvo.values import CAR, DBC


def _sources(identity, cruise=True, *, frame=0):
  modulus = 15 if identity == 'POLESTAR_2' else 16
  mid = CANPacker('volvo_mid_1')
  pt = CANPacker(DBC[CAR(identity)][Bus.pt])
  result = [mid.make_can_msg(name, bus, values) for name, bus, values in [
    ('SPEED', 0, {'SPEED': 3000}), ('SPEED_2', 0, {}), ('GEAR_POSITION', 0, {'GEAR_POSITION': 3}),
    ('LCA', 0, {}), ('LCA_2', 0, {'COUNTER_1': ((frame // 2) * 2) % modulus, 'COUNTER_2': ((frame // 2) * 4) % modulus}), ('LCA_3', 0, {}), ('LCA_4', 0, {}),
    ('LCA_5', 0, {}), ('LCA_6', 0, {}), ('LCA_7', 0, {}),
    ('PSCM', 2, {}), ('DRIVER_INPUT', 2, {}), ('EGSM', 2, {}), ('PSCM_RELATED', 2, {'NEW_SIGNAL_2': 8}), ('SAS', 2, {}),
  ]]
  spa = identity != 'VOLVO_XC40_RECHARGE'
  result.append(pt.make_can_msg('ECM_1', 1, {'ACCELERATOR_PEDAL_POS': 0 if spa else 20}))
  result.append(pt.make_can_msg('BUS1_CRUISE_CONTROL', 1,
                              {'CRUISE_CONTROL_SPA_ENABLED' if spa else 'CRUISE_CONTROL_ENABLED': int(cruise)}))
  from opendbc.car.volvo.helpers import (checksum_lca_2_message, checksum_2_0x69_message,
    checksum_1_pscm_related_message, checksum_2_pscm_related_message, checksum_lca_5_message)
  checked = []
  for address, payload, bus in result:
    if address == 0x69 and frame % 2:
      continue  # This source is 50Hz in the actual receiver contract.
    d = bytearray(payload)
    if address == 0x69:
      d[6] = checksum_lca_2_message(d[0], d[5])
      d[2] = checksum_2_0x69_message(d[0], d[1], d[3], d[4])
    elif address == 0x17:
      d[0] = checksum_1_pscm_related_message(d[1], d[2])
      d[3] = checksum_2_pscm_related_message(d[2])
    elif address == 0x67:
      d[2] = checksum_lca_5_message(d[0], d[1], d[3], d[4], d[5])
    checked.append((address, bytes(d), bus))
  return checked


@pytest.mark.parametrize('withdrawal', ['disabled', 'invalid', 'timeout', 'park', 'temporary', 'permanent'])
def test_polestar_primary_primes_on_cold_start_and_after_withdrawal(withdrawal):
  cp = CarInterface.get_params(CAR.POLESTAR_2, gen_empty_fingerprint(), [], False, False, False)
  ci = CarInterface(cp)
  for frame in range(40):
    ci.update([(1_000_000_000 + frame * 10_000_000, _sources('POLESTAR_2', frame=frame))])
  assert ci.CS.out.canValid
  assert ci.CS.out.gearShifter == structs.CarState.GearShifter.drive
  cc = structs.CarControl.new_message(latActive=True)
  cc.actuators.steeringAngleDeg = 12.0
  controller = ci.CC
  assert controller.lca_2_modulus == 15
  stock = create_lca_5_message(controller.packer, False, 0., ci.CS.msg_lca_5, ci.CS.msg_lca_5['COUNTER'])
  _, output = ci.apply(cc.as_reader(), 2_000_000_000)
  assert next(message for message in output if message[0] == 0x67) == stock
  assert controller.lca_5_primed
  for frame in range(1, 7):
    actuators, output = ci.apply(cc.as_reader(), 2_000_000_000 + frame * 10_000_000)
    assert actuators.steeringAngleDeg == pytest.approx(controller.lca_5_angle_last)
  assert controller.lca_5_angle_last > 0
  healthy = ci.CS.out
  blocked = structs.CarState.new_message(**healthy.to_dict())
  if withdrawal == 'disabled':
    cc.latActive = False
  elif withdrawal == 'invalid':
    blocked.canValid = False
  elif withdrawal == 'timeout':
    blocked.canTimeout = True
  elif withdrawal == 'park':
    blocked.gearShifter = structs.CarState.GearShifter.park
  elif withdrawal == 'temporary':
    blocked.steerFaultTemporary = True
  else:
    blocked.steerFaultPermanent = True
  ci.CS.out = blocked.as_reader()
  for frame in (7, 8):
    _, output = ci.apply(cc.as_reader(), 2_000_000_000 + frame * 10_000_000)
    assert not controller.lca_5_primed
    for message in output:
      if message[0] == 0x67:
        assert message == stock
  ci.CS.out = healthy
  cc.latActive = True
  for frame in (9, 10):
    _, output = ci.apply(cc.as_reader(), 2_000_000_000 + frame * 10_000_000)
    for message in output:
      if message[0] == 0x67:
        assert message == stock
