"""A real reverse withdrawal may precede the 10Hz native request echo; it never permits output."""
from dataclasses import replace

import pytest

from opendbc.car import structs
from opendbc.car.ford.values import CAR, FordFlags
from openpilot.starpilot.aol.tests.test_reverse_source_gap import NOW, PRODUCER, SESSION, sample, change
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.wire import encode_safety
from openpilot.starpilot.car.ford.command_pause import retain_gear_withdrawal_intent


@pytest.mark.parametrize('gap', [False, True])
@pytest.mark.parametrize('fault', ['none', 'expired_baseline', 'drive', 'can', 'eps', 'unlatched', 'producer',
                                  'axis_active', 'axis_source', 'events', 'native_allow', 'native_long', 'native_fault',
                                  'native_expired', 'inventory'])
def test_ford_reverse_native_withdrawal_preserves_only_bounded_existing_intent(fault, gap):
  cp = structs.CarParams(brand='ford', carFingerprint=CAR.FORD_MUSTANG_MACH_E_MK1,
    flags=int(FordFlags.CANFD), alternativeExperience=32, steerControlType='angle', transmissionType='automatic',
    pcmCruise=True, safetyConfigs=[structs.CarParams.SafetyConfig(safetyModel='ford', safetyParam=0x4002)])
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  feedback = TransportPauseFeedback(cp)
  active, _ = sample(cp, NOW - 10_000_000, active=True)
  assert not feedback.observe(active, cp, cs, NOW - 10_000_000, latched=True, intent_producer_session=PRODUCER)
  cs.gearShifter = 'reverse'
  now = NOW + (190_000_001 if fault == 'expired_baseline' else 10_000_000)
  sm, native = sample(cp, now, 2, gap=gap)
  native = replace(native, requestedLateral=True)
  producer, latched = PRODUCER, fault != 'unlatched'
  if fault == 'drive':
    cs.gearShifter = 'drive'
  elif fault == 'can':
    cs.canValid = False
  elif fault == 'eps':
    cs.steerFaultTemporary = True
  elif fault == 'producer':
    producer = 'different'
  elif fault in ('axis_active', 'axis_source'):
    change(sm, 'aolAxisState', lambda a: setattr(a, 'lateralActive' if fault == 'axis_active' else 'sourceCarStateMonoTime',
                                               True if fault == 'axis_active' else now + 1))
  elif fault == 'events':
    sm.valid['onroadEvents'] = False
  elif fault == 'native_allow':
    native = replace(native, lateralAllowed=True)
  elif fault == 'native_long':
    native = replace(native, requestedLongitudinal=True, longitudinalAllowed=True)
  elif fault == 'native_fault':
    native = replace(native, pandaInventory=(replace(native.pandaInventory[0], heartbeatLost=True),))
  elif fault == 'native_expired':
    native = replace(native, validUntilMonoTime=now - 1)
  elif fault == 'inventory':
    native = replace(native, pandaSerial='changed', pandaInventory=(replace(native.pandaInventory[0], hardwareSerial='changed'),))
  wire = sm.messages['aolSafetyWire'].as_builder()
  wire.aolSafetyWire = encode_safety(native)
  sm.messages['aolSafetyWire'] = wire.as_reader()
  before = dict(vars(feedback))
  retained = retain_gear_withdrawal_intent(feedback, sm, cp, cs, sm['aolAxisState'], now,
                                               latched=latched, producer=producer)
  assert retained == (fault == 'none')
  assert vars(feedback) == before
  if retained:
    assert not sm['aolAxisState'].lateralActive and not sm['aolAxisState'].longitudinalActive
  assert native.axisSessionId == SESSION
