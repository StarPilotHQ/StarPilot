"""Optional friction loss must not impersonate loss of the exact Bolt pedal owner."""
import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.tests import test_bolt_acc_pedal_friction as fixtures
from opendbc.car.gm.values import CAR, DBC


class TestBoltSecondaryFriction(unittest.TestCase):

  def test_actual_parser_preserves_stock_fault_and_ownership(self):
    for unavailable in [False, True]:
      for acc_state in [0, 1, 2, 3, 4]:
        cp = params(CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, pedal=True, camera=True)
        assert cp.safetyConfigs[0].safetyParam == 0x1CD
        cs = CarState(cp)
        parsers = cs.get_can_parsers(cp)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        frames = [packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': acc_state}),
                  packer.make_can_msg('EBCMFrictionBrakeStatus', 0, {'FrictionBrakeUnavailable': unavailable})]
        parsers[Bus.pt].update([(1_000_000_000, frames)])
        out = cs.update(parsers)
        assert cs.bolt_acc_pedal_friction_unavailable == unavailable
        assert out.accFaulted == (acc_state == 3 or (unavailable and acc_state != 0))
        assert out.cruiseState.enabled == (acc_state != 0)

  def test_other_profiles_keep_friction_fault(self):
    profiles = ((CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, True), (CAR.CHEVROLET_BOLT_ACC_2022_2023, False),
                (CAR.CHEVROLET_VOLT_CAMERA, False), (CAR.CHEVROLET_VOLT_2019, False))
    for candidate, removed in profiles:
      cp = params(candidate, pedal=removed, camera=True, removed=removed)
      cs = CarState(cp)
      parsers = cs.get_can_parsers(cp)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      parsers[Bus.pt].update([(1_000_000_000, [packer.make_can_msg('EBCMFrictionBrakeStatus', 0, {'FrictionBrakeUnavailable': 1})])])
      assert not cs.bolt_acc_pedal_secondary_friction
      assert not cs.bolt_acc_pedal_friction_unavailable
      assert cs.update(parsers).accFaulted

  def test_controller_withdraws_secondary_brake_without_changing_primary_commands(self):
    helper = fixtures.TestBoltAccPedalFriction(methodName='runTest')
    failed, reference = (helper.fixture(), helper.fixture())
    for tick in range(1, 51):
      frame = tick * 4
      failed[3].bolt_acc_pedal_friction_unavailable = tick == 4
      reference[3].bolt_acc_pedal_friction_unavailable = False
      actual, expected = (helper.step(failed, frame), helper.step(reference, frame))
      assert [m for m in actual if m[0] != 0x315] == [m for m in expected if m[0] != 0x315]
      assert failed[0].apply_gas == reference[0].apply_gas
      assert fixtures.brake_frames(actual)
      if tick < 4:
        assert actual == expected
      else:
        assert failed[0].bolt_acc_pedal_friction_unavailable
        assert failed[0].apply_brake == 0
        assert not failed[0].bolt_acc_pedal_friction_low_speed_active
        assert all(fixtures.brake_fields(m)[:2] == (1, 0) for m in fixtures.brake_frames(actual))
    failed[2].cruiseState.available = False
    helper.step(failed, 204)
    failed[2].cruiseState.available = True
    actual = helper.step(failed, 208)
    assert failed[0].bolt_acc_pedal_friction_unavailable
    assert all(fixtures.brake_fields(m)[:2] == (1, 0) for m in fixtures.brake_frames(actual))
    assert not helper.fixture()[0].bolt_acc_pedal_friction_unavailable
