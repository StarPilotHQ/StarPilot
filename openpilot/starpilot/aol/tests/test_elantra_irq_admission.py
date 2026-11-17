"""Elantra CAN interrupt faults block steering regardless of otherwise valid authority."""
from contextlib import ExitStack
from dataclasses import replace
from unittest import mock

import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.classic_scc_aol import aol_word, qualified
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.panda_faults import blocking_fault_list, blocking_fault_mask
from openpilot.starpilot.aol.runtime import current_native
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_ordinary_source_availability import settings


def recorded_cp():
  fingerprint = gen_empty_fingerprint()
  fingerprint[0].update({0x391: 8, 0x38d: 8})
  fingerprint[2][0x485] = 4
  cp = CarInterface.get_params(CAR.HYUNDAI_ELANTRA_2021, fingerprint, [], False, False, False)
  cp.fingerprintSource = 'fw'
  cp.safetyConfigs[0].safetyParam = aol_word(cp)
  cp.alternativeExperience = 32
  assert int(cp.flags) == 16843136 and cp.safetyConfigs[0].safetyParam == 0xC00
  assert qualified(cp, marked_only=True)
  return cp


@pytest.mark.parametrize('fault_source', ['none', 'panda', 'native', 'both'])
@pytest.mark.parametrize('full', [False, True])
@pytest.mark.parametrize('fault', ['none', 'rx', 'relay', 'denied', 'request', 'expired-wire'])
def test_recorded_irq_through_selfdrived_and_controls_blocks_faulted_steering(full, fault, fault_source):
  irq = fault_source != 'none'
  cp = recorded_cp()
  before = cp.to_dict()
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, controls, drive = case._authority_callers(full, cp=cp, genuine_events=True)
    new_message = messaging.new_message
    def with_irq(service=None, size=None, **kwargs):
      message = new_message(service, size, **kwargs)
      if fault_source in ('panda', 'both') and service == 'pandaStates':
        for panda in message.pandaStates:
          panda.faults = ['interruptRateCan1']
      return message
    encode = fixture.encode_safety
    def wire_irq(state):
      if fault_source in ('native', 'both'):
        state = replace(state, pandaInventory=tuple(replace(slot, faults=slot.faults | 4) for slot in state.pandaInventory))
      if fault == 'expired-wire':
        state = replace(state, observedMonoTime=state.observedMonoTime - 300_000_000,
                        validUntilMonoTime=state.validUntilMonoTime - 300_000_000)
      return encode(state)
    cleanup.enter_context(mock.patch.object(messaging, 'new_message', with_irq))
    cleanup.enter_context(mock.patch.object(fixture, 'encode_safety', wire_irq))
    for tick in range(3):
      stamp = 10_000_000_000 + tick * 10_000_000
      options = settings('combined' if full else 'lateral_only', stamp, tick + 1)
      observed = fixture.car_state()
      observed.cruiseState.enabled = full
      options['observed_state'] = observed
      options.update(panda_fault=fault == 'rx', relay_fault=fault == 'relay', long_allowed=full)
      if fault == 'denied':
        options.update(allowed=False, long_allowed=False)
      if fault == 'request':
        options.update(native_request_lat=False, native_request_long=False)
      axis, state, command = drive(tick, **options)
    if fault == 'none' and not irq:
      assert axis.lateralActive and command.latActive, 'HEALTHY_ELANTRA_AUTHORITY_REJECTED'
      assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      native = current_native(sd.sm, sd.CP, now_ns=stamp, axis_session_id=sd.aol_session_id)
      assert native is not None and native.pandaInventory[0].faults == 0
      assert list(sd.sm['pandaStates'][0].faults) == []
      assert not axis.longitudinalActive and not command.longActive
      assert state.enabled == full
    else:
      assert not axis.lateralActive and not command.latActive, 'ELANTRA_HARDWARE_FAULT_MUST_BLOCK'
      if irq and fault == 'none':
        assert log.OnroadEvent.EventName.controlsMismatch in sd.events.names
        expected_faults = [log.PandaState.FaultType.interruptRateCan1] if fault_source in ('panda', 'both') else []
        assert list(sd.sm['pandaStates'][0].faults) == expected_faults
      assert not axis.longitudinalActive and not command.longActive
    assert cp.to_dict() == before


@pytest.mark.parametrize('name,value', tuple(log.PandaState.FaultType.schema.enumerants.items()))
def test_other_and_mixed_faults_still_block(name, value):
  cp = recorded_cp()
  expected = True
  assert blocking_fault_mask(1 << value, cp) == expected
  assert blocking_fault_mask((1 << value) | 4, cp) == expected
  assert blocking_fault_list([value], cp) == expected
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan1, value], cp) == expected


@pytest.mark.parametrize('mutation', ['alpha', 'fixed', 'can', 'word', 'unmarked', 'flags', 'identity', 'extra', 'passive'])
def test_unrecorded_configurations_cannot_borrow_diagnostic(mutation):
  cp = recorded_cp()
  if mutation == 'alpha':
    cp.openpilotLongitudinalControl = True
    cp.pcmCruise = False
  elif mutation in ('fixed', 'can'):
    cp.fingerprintSource = mutation
  elif mutation == 'word':
    cp.safetyConfigs[0].safetyParam |= 4
  elif mutation == 'unmarked':
    cp.alternativeExperience = 0
  elif mutation == 'flags':
    cp.flags |= 1 << 31
  elif mutation == 'identity':
    cp.carFingerprint = CAR.HYUNDAI_SONATA
  elif mutation == 'extra':
    cp.safetyConfigs = [cp.safetyConfigs[0], cp.safetyConfigs[0]]
  else:
    cp.passive = True
  assert blocking_fault_mask(4, cp)
  assert blocking_fault_list([log.PandaState.FaultType.interruptRateCan1], cp)
