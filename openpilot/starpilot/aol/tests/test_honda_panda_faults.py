"""Only reported CAN2 load diagnostics on current qualified Bosch AOL profiles."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.stock_aol import qualified
from opendbc.car.honda.values import CAR, HondaFlags
from openpilot.cereal import log
from openpilot.starpilot.aol.panda_faults import blocking_fault_mask, blocking_fault_list
from openpilot.starpilot.aol.runtime import current_native, decide_axes
from openpilot.starpilot.aol.tests.test_panda_inventory import bound_state, sm_for, NOW
from openpilot.starpilot.aol.tests.test_runtime import car_state


PROFILES = ((CAR.HONDA_CIVIC_BOSCH, False), (CAR.HONDA_ACCORD, False),
            (CAR.HONDA_CIVIC_2022, False), (CAR.HONDA_CIVIC_2022, True),
            (CAR.HONDA_CRV_6G, False), (CAR.HONDA_PILOT_4G, False), (CAR.HONDA_PASSPORT_4G, False),
            (CAR.ACURA_MDX_4G_MMR, False), (CAR.ACURA_TLX_2G_MMR, False))


def honda(identity=CAR.HONDA_CIVIC_BOSCH, alpha=False):
  fingerprint = gen_empty_fingerprint()
  fingerprint[0][0x1A3] = 8
  fingerprint[1][0x1A3] = 8
  cp = CarInterface.get_params(identity, fingerprint, [], alpha, False, False)
  cp.fingerprintSource = structs.CarParams.FingerprintSource.fw
  cp.alternativeExperience = 32
  assert qualified(cp, marked_only=True)
  return cp


@pytest.mark.parametrize('identity,alpha', PROFILES)
def test_reported_can2_fault_on_qualified_bosch_is_diagnostic(identity, alpha):
  cp = honda(identity, alpha)
  assert not blocking_fault_mask(1 << 3, cp)
  assert not blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp)


@pytest.mark.parametrize('identity,alpha', PROFILES)
@pytest.mark.parametrize('fault,value', tuple(log.PandaState.FaultType.schema.enumerants.items()))
def test_all_other_known_and_mixed_faults_still_block(identity, alpha, fault, value):
  cp = honda(identity, alpha)
  blocks = fault != 'interruptRateCan2'
  assert blocking_fault_mask(1 << value, cp) == blocks
  assert blocking_fault_mask((1 << value) | (1 << 3), cp) == blocks
  assert blocking_fault_list([value], cp) == blocks
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2, value], cp) == blocks


@pytest.mark.parametrize('mutation', ('unmarked', 'fixed', 'passive', 'dashcam', 'notcar', 'extra_panda',
                                    'wrong_model', 'wrong_word', 'unknown_flags', 'manual', 'nidec', 'unknown_identity'))
def test_other_honda_configurations_cannot_borrow_exception(mutation):
  cp = honda()
  if mutation == 'unmarked':
    cp.alternativeExperience = 0
  elif mutation == 'fixed':
    cp.fingerprintSource = 'fixed'
  elif mutation == 'passive':
    cp.passive = True
  elif mutation == 'dashcam':
    cp.dashcamOnly = True
  elif mutation == 'notcar':
    cp.notCar = True
  elif mutation == 'extra_panda':
    cp.safetyConfigs = [cp.safetyConfigs[0], cp.safetyConfigs[0]]
  elif mutation == 'wrong_model':
    cp.safetyConfigs[0].safetyModel = 'hondaNidec'
  elif mutation == 'wrong_word':
    cp.safetyConfigs[0].safetyParam = 16
  elif mutation == 'unknown_flags':
    cp.flags |= 1 << 31
  elif mutation == 'manual':
    cp.transmissionType = 'manual'
  elif mutation == 'nidec':
    cp = honda(CAR.HONDA_CIVIC)
  else:
    cp.carFingerprint = 'UNKNOWN HONDA'
  assert blocking_fault_mask(1 << 3, cp)
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp)


@pytest.mark.parametrize('mask', (-1, 1 << 27, 1 << 31, 1 << 32))
def test_unknown_or_malformed_faults_are_not_diagnostic(mask):
  cp = honda()
  assert blocking_fault_mask(mask, cp)
  assert blocking_fault_list([31], cp)


@pytest.mark.parametrize('identity', (CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CRV_6G))
@pytest.mark.parametrize('mutation', ('none', 'rx', 'heartbeat', 'relay', 'expired', 'wrong_session', 'denied'))
def test_real_inventory_exception_does_not_create_authority_or_bypass_vetoes(mutation, identity):
  cp = honda(identity)
  state = bound_state(cp)
  slot = replace(state.pandaInventory[0], faults=1 << 3)
  if mutation == 'rx':
    slot = replace(slot, safetyRxChecksInvalid=True)
  elif mutation == 'heartbeat':
    slot = replace(slot, heartbeatLost=True)
  elif mutation == 'relay':
    slot = replace(slot, faults=(1 << 3) | 1)
  state = replace(state, pandaInventory=(slot,))
  if mutation == 'denied':
    state = replace(state, lateralAllowed=False, longitudinalAllowed=False)
  native = current_native(sm_for(cp, state), cp,
                          now_ns=NOW + (200_000_001 if mutation == 'expired' else 0),
                          axis_session_id='wrong' if mutation == 'wrong_session' else state.axisSessionId)
  if mutation in ('rx', 'heartbeat', 'relay', 'expired', 'wrong_session'):
    assert native is None
    return
  assert native == state
  decision = decide_axes(standard_lateral=True, standard_longitudinal=False,
                         intent=SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False),
                         native=native, car_state=car_state(), initialized=True, model_ready=True, no_entry=False,
                         immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
  assert decision.lateral_active == (mutation == 'none')
  assert not decision.longitudinal_active


@pytest.mark.parametrize('mutation', ('none', 'hybrid', 'alternate_brake', 'two_pandas', 'fixed',
                                    'can_fingerprint', 'unmarked', 'alpha', 'wrong_word'))
def test_canfd_diagnostic_requires_the_qualified_single_panda_stock_owner(mutation):
  cp = honda(CAR.HONDA_CRV_6G)
  if mutation == 'hybrid':
    cp.flags |= int(HondaFlags.HYBRID)
  elif mutation == 'alternate_brake':
    cp.flags |= int(HondaFlags.BOSCH_ALT_BRAKE)
    cp.safetyConfigs[0].safetyParam = 17
  elif mutation == 'two_pandas':
    cp.safetyConfigs = [{'safetyModel': 'noOutput', 'safetyParam': 0}, cp.safetyConfigs[0].to_dict()]
    assert qualified(cp, marked_only=True)
  elif mutation == 'fixed':
    cp.fingerprintSource = 'fixed'
  elif mutation == 'can_fingerprint':
    cp.fingerprintSource = 'can'
  elif mutation == 'unmarked':
    cp.alternativeExperience = 0
  elif mutation == 'alpha':
    cp.openpilotLongitudinalControl = True
    cp.pcmCruise = False
  elif mutation == 'wrong_word':
    cp.safetyConfigs[0].safetyParam = 8
  blocks = mutation not in ('none', 'hybrid', 'alternate_brake')
  assert blocking_fault_mask(1 << 3, cp) == blocks
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan2], cp) == blocks
