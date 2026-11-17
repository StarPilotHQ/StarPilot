"""Fresh stock-HKG sources earn new LAT proofs after an older command expires."""
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import pytest

from opendbc.car import structs
from opendbc.car.hyundai.canfd_stock_aol import qualified
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.runtime import current_native
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_canfd_location_native_recovery import FAMILIES, params
from openpilot.starpilot.aol.tests.test_ordinary_source_availability import settings
from openpilot.starpilot.aol.wire import encode_safety

BASE = 10_000_000_000
CURRENT = BASE + 50_000_000
OFFSET = 5_319_697


def warm(case, family, pedal):
  sd, controls, drive = case._authority_callers(False, cp=params(family), genuine_events=True)
  sd.params.put('LKASButtonControl', 9, block=True)
  sd.params.put('MainCruiseButtonControl', 0, block=True)
  sd.params.put('AolBrakePauseSpeedMps', 0.0, block=True)
  sd.aol_settings = read_settings(sd.params)
  assert qualified(sd.CP, marked_only=True)
  if family == 'camera':
    assert sd.CP.flags == 10248 and sd.CP.safetyConfigs[0].safetyParam == 0x809
  cs = fixture.car_state(brake=pedal == 'brake')
  cs.vEgo = cs.vEgoRaw = 1.6
  cs.cruiseState.available = True
  for tick in (0, 1, 2):
    source = BASE + tick * 10_000_000
    previous, _, command = drive(tick, observed_state=cs, gas=pedal == 'gas', state_stamp=source,
      offset_ns=5_011_449 if tick == 2 else 0,
      **settings('lateral_only', source, tick + 1))
    assert previous.lateralActive and command.latActive and not command.longActive
  return sd, controls, drive, cs, previous


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('pedal', ['none', 'gas', 'brake'])
def test_stock_source_survives_expired_previous_axis(family, pedal):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, controls, drive, cs, previous = warm(case, family, pedal)
    old = previous.to_dict()
    previous_deadline = previous.validUntilMonoTime
    with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=previous_deadline + 1):
      expired, _ = controls.state_control()
    assert not expired.latActive and not expired.longActive
    axis, state, command = drive(5, observed_state=cs, gas=pedal == 'gas', state_stamp=CURRENT, offset_ns=OFFSET,
      **settings('lateral_only', CURRENT, 4))
    assert axis.observedMonoTime == previous_deadline + 308_248
    assert axis.sourceCarStateMonoTime == CURRENT
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'HKG_STOCK_FRESH_SUCCESSOR_DISABLED'
    assert axis.lateralActive and command.latActive and not command.longActive
    assert not state.enabled and not state.active
    assert previous.to_dict() == old
    assert axis.validUntilMonoTime == axis.observedMonoTime + 30_000_000


@pytest.mark.parametrize('fault', ['lateral_denied', 'rx', 'relay', 'invalid_native', 'native_session', 'expired_native',
  'expired_source', 'invalid_source', 'producer', 'expired_intent', 'eps', 'cancel', 'settings', 'pause'])
def test_current_native_source_settings_and_fault_guards_remain_required(fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive, cs, _ = warm(case, 'camera', 'gas')
    options = settings('lateral_only', CURRENT, 4)
    changes = {'lateral_denied': {'allowed': False}, 'rx': {'panda_fault': True},
               'relay': {'relay_fault': True}, 'invalid_native': {'native_valid': False}}
    if fault in changes:
      options.update(changes[fault])
    if fault in ('native_session', 'expired_native'):
      native = current_native(sd.sm, sd.CP, now_ns=BASE + 25_011_449, axis_session_id=sd.aol_session_id)
      assert native is not None
      now = CURRENT + OFFSET
      native = replace(native, observedMonoTime=now, validUntilMonoTime=now + 200_000_000)
      if fault == 'native_session':
        native = replace(native, axisSessionId='foreign')
      else:
        native = replace(native, observedMonoTime=now - 200_000_001, validUntilMonoTime=now - 1)
      event = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=native.observedMonoTime)
      event.aolSafetyWire = encode_safety(native)
      options['safety_message'] = event
    source = CURRENT
    if fault == 'expired_source':
      source = BASE + 20_000_000
      options.update(settings('lateral_only', source, 4))
    if fault == 'invalid_source':
      event = messaging.new_message('carState', valid=False, logMonoTime=CURRENT)
      event.carState = cs
      options['car_state_message'] = event
    if fault in ('producer', 'expired_intent', 'pause'):
      from openpilot.starpilot.aol.wire import decode_intent, encode_intent

      intent_event = options['intent_message'].as_reader().as_builder()
      intent = decode_intent(intent_event.aolIntentWire)
      assert intent is not None
      change = {'producer': {'producerSessionId': 'foreign'}, 'expired_intent': {'validUntilMonoTime': CURRENT - 1},
                'pause': {'pauseLateral': True}}[fault]
      intent_event.aolIntentWire = encode_intent(replace(intent, **change))
      options['intent_message'] = intent_event
    if fault == 'eps':
      cs.steerFaultPermanent = True
    if fault == 'cancel':
      cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
    if fault == 'settings':
      sd.aol_settings = replace(sd.aol_settings, enabled=False)
    axis, _, command = drive(5, observed_state=cs, gas=True, state_stamp=source, offset_ns=OFFSET, **options)
    assert not axis.lateralActive and not command.latActive
    assert not axis.longitudinalActive and not command.longActive
