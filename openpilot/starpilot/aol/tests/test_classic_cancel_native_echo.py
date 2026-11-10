from dataclasses import replace

import pytest

from opendbc.car.hyundai.classic_long_aol import aol_word
from opendbc.car.hyundai.values import CAR
from openpilot.cereal import messaging
from openpilot.starpilot.aol.tests.test_classic_long_profiles import params
from openpilot.starpilot.aol.tests.test_hkg_shared_cancel_guard import captured_context
from openpilot.starpilot.aol.transport_pause import cp_identity
from openpilot.starpilot.aol.wire import encode_safety


@pytest.mark.parametrize('identity', (CAR.HYUNDAI_ELANTRA_2021, CAR.HYUNDAI_SONATA, CAR.HYUNDAI_IONIQ))
@pytest.mark.parametrize('failure', ('retain', 'rx_fault', 'hardware_fault', 'lat_denied', 'expired', 'can_timeout', 'producer'))
def test_classic_cancel_holds_original_intent_across_newer_health_and_old_long_echo(identity, failure):
  _, cs, sm, feedback, _, native, now = captured_context()
  cp = params(identity, lda=True)
  cp.safetyConfigs[0].safetyParam = aol_word(cp)
  cp.alternativeExperience = 32
  config = cp.safetyConfigs[0]
  feedback.cp = cp_identity(cp)
  slot = replace(native.pandaInventory[0], safetyModel=int(config.safetyModel.raw),
    safetyParam=config.safetyParam, controlsAllowed=True)
  native = replace(native, safetyModel=int(config.safetyModel.raw), safetyParam=config.safetyParam,
    longitudinalAllowed=True, validUntilMonoTime=now + 200_000_000, pandaInventory=(slot,))
  def publish(tick, *, withdrawn=False, echo=False):
    stamp = now + tick * 10_000_000
    axis = sm['aolAxisState'].as_builder()
    axis.sequence += 1
    axis.sourceCarStateMonoTime = axis.observedMonoTime = stamp
    axis.validUntilMonoTime = stamp + 30_000_000
    if withdrawn:
      axis.mode = 'lateralOnly'
      axis.desiredLongitudinal = axis.longitudinalActive = False
    if failure == 'producer' and tick:
      axis.intentProducerSessionId = 'foreign'
    sm['aolAxisState'] = axis.as_reader()
    sm.logMonoTime['aolAxisState'] = stamp
    pandas = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=stamp)
    panda = pandas.pandaStates[0]
    panda.safetyModel, panda.safetyParam, panda.alternativeExperience = config.safetyModel, config.safetyParam, 32
    panda.controlsAllowed = not withdrawn
    if failure == 'rx_fault' and tick:
      panda.safetyRxChecksInvalid = True
    if failure == 'hardware_fault' and tick:
      panda.faults = ['interruptRateCan1']
    sm['pandaStates'] = pandas.as_reader().pandaStates
    sm.logMonoTime['pandaStates'] = stamp
    wire = native
    if failure == 'lat_denied' and tick:
      wire = replace(wire, lateralAllowed=False)
    if failure == 'expired' and tick:
      wire = replace(wire, validUntilMonoTime=stamp - 1)
    if echo:
      wire = replace(native, observedMonoTime=stamp, validUntilMonoTime=stamp + 200_000_000,
        sourcePandaStatesMonoTime=stamp, requestedLongitudinal=False, longitudinalAllowed=False,
        pandaInventory=(replace(slot, controlsAllowed=False),))
    sm['aolSafetyWire'] = encode_safety(wire)
    sm.logMonoTime['aolSafetyWire'] = wire.observedMonoTime
    return stamp
  publish(0)
  assert not feedback.observe(sm, cp, cs, now, latched=True, cancel_source_ns=now,
    intent_producer_session='authority-card')
  assert feedback.longitudinal_cancel_ns == now
  cs.buttonEvents = []
  if failure == 'can_timeout':
    cs.canTimeout = True
  for tick in range(1, 8):
    stamp = publish(tick, withdrawn=True)
    rejected = feedback.observe(sm, cp, cs, stamp, latched=True, intent_producer_session='authority-card')
    if failure != 'retain':
      assert rejected
      return
    assert not rejected
    assert feedback.longitudinal_cancel_ns == now
  stamp = publish(8, withdrawn=True, echo=True)
  assert not feedback.observe(sm, cp, cs, stamp, latched=True, intent_producer_session='authority-card')
  assert feedback.longitudinal_cancel_ns == 0
