"""The Leapmotor B10's CAN2 interrupt-rate diagnostic is exempt; every other fault still blocks."""
import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.leapmotor.interface import CarInterface
from opendbc.car.leapmotor.values import CAR
from openpilot.cereal import log
from openpilot.starpilot.aol.panda_faults import blocking_fault_mask, blocking_fault_list


def leapmotor(alpha=True):
  return CarInterface.get_params(CAR.LEAPMOTOR_B10, gen_empty_fingerprint(), [], alpha, False, False)


@pytest.mark.parametrize('alpha', (False, True))
def test_can2_interrupt_rate_is_diagnostic(alpha):
  cp = leapmotor(alpha)
  assert not blocking_fault_mask(1 << 3, cp)
  assert not blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp)


@pytest.mark.parametrize('fault,value', tuple(log.PandaState.FaultType.schema.enumerants.items()))
def test_all_other_and_mixed_faults_still_block(fault, value):
  cp = leapmotor()
  blocks = fault != 'interruptRateCan2'
  assert blocking_fault_mask(1 << value, cp) == blocks
  assert blocking_fault_mask((1 << value) | (1 << 3), cp) == blocks
  assert blocking_fault_list([value], cp) == blocks
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2, value], cp) == blocks


@pytest.mark.parametrize('mutation', ('passive', 'dashcam', 'notcar', 'extra_panda', 'wrong_model'))
def test_other_configurations_cannot_borrow_exception(mutation):
  cp = leapmotor()
  if mutation == 'passive':
    cp.passive = True
  elif mutation == 'dashcam':
    cp.dashcamOnly = True
  elif mutation == 'notcar':
    cp.notCar = True
  elif mutation == 'extra_panda':
    cp.safetyConfigs = [cp.safetyConfigs[0], structs.CarParams.SafetyConfig(safetyModel=structs.CarParams.SafetyModel.noOutput)]
  elif mutation == 'wrong_model':
    cp.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.noOutput
  assert blocking_fault_mask(1 << 3, cp)
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp)
