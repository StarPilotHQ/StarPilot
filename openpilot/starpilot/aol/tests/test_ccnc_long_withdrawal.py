"""CCNC LONG withdrawal keeps only existing independent lateral intent."""
from contextlib import ExitStack
from dataclasses import replace
import unittest

import pytest

from opendbc.car.hyundai.values import CAR
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.common.params import Params
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.wire import decode_safety, encode_safety
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_hkg_cancel_joined import parser_frames
from openpilot.starpilot.aol.tests.test_hkg_early_long_withdrawal import BASE, wire


def callers(case, identity, full_op):
  if identity == CAR.HYUNDAI_IONIQ_5_PE:
    from opendbc.car.hyundai.tests.test_ioniq5pe_pending_startup import PEStartupFixture
    startup = type('StartupCase', (PEStartupFixture, unittest.TestCase), {})()
  else:
    from opendbc.car.hyundai.tests.test_ev9_pending_startup import EV9StartupFixture
    startup = EV9StartupFixture()
  case.addCleanup(startup.doCleanups)
  startup.setUp()
  owner, ci, _ = startup.instance(seal=False)
  case.addCleanup(owner.close)
  result = case._ioniq_availability_callers(full_op=full_op, stock_angle=True, cp=ci.CP, startup_owner=owner)
  result[0].aol_settings = read_settings(Params())
  assert result[0].aol_settings == result[3].aol_card_intent.settings
  return result


@pytest.mark.parametrize('identity', [CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9])
def test_native_cancel_precedes_parsed_button_without_clearing_aol(identity):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, _, step, card, receive = callers(case, identity, True)
    for tick in range(2, 10):
      axis, _, command = step(tick)
      assert axis.lateralActive and command.latActive and command.longActive
      assert receive(BASE + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    now = BASE + 100_000_000
    axis, state, command = step(10, native_request_lat=True, native_request_long=True,
      long_allowed=False, panda_controls_allowed=True, safety_message=wire(sd, now))
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'CCNC_EARLY_LONG_WITHDRAWAL_IS_NOT_CRITICAL'
    assert state.enabled and axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert receive(now, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    for tick in range(1, 123):
      stamp = BASE + (tick - 111) * 10_000_000
      physical = parser_frames(card.CI, tick, button=4 if tick == 122 else 0, engaged=True)
      cancel = card.CI.update([(stamp, physical)])
    assert cancel.canValid and card.CI.CS.ev6_aol_source_healthy
    assert any(str(event.type) == 'cancel' and event.pressed for event in cancel.buttonEvents)
    axis, state, command = step(11, observed_state=cancel, native_request_long=True,
      long_allowed=False, panda_controls_allowed=False)
    assert not state.enabled and not command.longActive
    assert axis.lateralActive and command.latActive
    assert receive(BASE + 110_000_000, state=cancel, axis=sd.pm.messages['aolAxisState'],
                   event=sd.pm.messages['onroadEvents'], can_packets=[(stamp, physical)])
    assert card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('identity', [CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9])
def test_native_eps_withdrawal_then_axis_before_warning_keeps_latch(identity):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, _, step, card, receive = callers(case, identity, False)
    for tick in range(2, 10):
      step(tick, native_request_long=False)
      assert receive(BASE + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    axis, _, command = step(10, gas=True, steering=True, allowed=False, native_request_long=False)
    assert axis.desiredLateral and not axis.lateralActive and not command.latActive
    assert receive(BASE + 100_000_000, state=sd.CS_prev.as_builder(), axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    fault = fixture.car_state()
    fault.vEgoRaw = fault.vEgo
    fault.steerFaultTemporary = True
    axis, _, command = step(11, gas=True, steering=True, observed_state=fault, allowed=False, native_request_long=False)
    assert not axis.lateralActive and not command.latActive and not command.longActive
    assert str(axis.faultReason) == 'none'
    assert receive(BASE + 110_000_000, state=sd.CS_prev.as_builder(), axis=sd.pm.messages['aolAxisState']), 'CCNC_EPS_AXIS_FIRST_CLEARED_INTENT'
    assert receive(BASE + 111_000_000, state=sd.CS_prev.as_builder(), event=sd.pm.messages['onroadEvents'])
    axis, _, command = step(12, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    assert receive(BASE + 120_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('identity', [CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9])
@pytest.mark.parametrize('fault', ['invalid', 'rx', 'heartbeat', 'hardware_fault', 'inventory', 'session', 'expired', 'lateral_denied'])
def test_early_long_drop_never_bypasses_native_faults_or_lateral_denial(identity, fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, _, step, _, _ = callers(case, identity, True)
    now = BASE + 20_000_000
    event = wire(sd, now, lateral=fault != 'lateral_denied')
    native = decode_safety(event.aolSafetyWire)
    assert native is not None
    if fault == 'invalid':
      event.valid = False
    elif fault in ('rx', 'heartbeat', 'hardware_fault', 'inventory'):
      field, value = {'rx': ('safetyRxChecksInvalid', True), 'heartbeat': ('heartbeatLost', True),
                      'hardware_fault': ('faults', 1), 'inventory': ('safetyParam', 0)}[fault]
      native = replace(native, pandaInventory=(replace(native.pandaInventory[0], **{field: value}),))
    elif fault == 'session':
      native = replace(native, axisSessionId='foreign')
    elif fault == 'expired':
      native = replace(native, observedMonoTime=now - 200_000_001, validUntilMonoTime=now - 1)
      event.logMonoTime = native.observedMonoTime
    event.aolSafetyWire = encode_safety(native)
    axis, host, command = step(2, long_allowed=False, panda_controls_allowed=False, safety_message=event)
    assert not axis.lateralActive and not command.latActive
    assert not axis.longitudinalActive and not command.longActive and not host.enabled
    assert log.OnroadEvent.EventName.controlsMismatch in sd.events.names
