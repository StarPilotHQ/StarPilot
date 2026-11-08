"""An aged conflating cache is metadata, never a new raw integrity violation."""
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import pytest
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.starpilot.aol.intent_companion import IntentCompanion
from openpilot.starpilot.aol.wire import decode_intent, encode_intent
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import intent

BASE = 10_000_000_000
SERVICE = 'aolIntentWire'


def drain(companion, message):
  with patch('openpilot.starpilot.aol.intent_companion.messaging.recv_one_or_none', side_effect=[message.as_reader(), None]):
    assert companion.drain()


@pytest.mark.parametrize('bad', ['none', 'cached_invalid', 'cached_tampered', 'raw_invalid', 'expired', 'short_lease'])
def test_aged_known_sm_does_not_poison_valid_raw_successor_or_grant_output(bad):
  with OpenpilotPrefix():
    sm = messaging.SubMaster([SERVICE])
    companion = IntentCompanion(sock=object())
    original = intent(BASE, 1)
    if bad == 'short_lease':
      original.aolIntentWire = encode_intent(replace(decode_intent(bytes(original.aolIntentWire)),
        validUntilMonoTime=BASE + 50_000_000))
    sm.update_msgs(BASE / 1e9, [original.as_reader()])
    drain(companion, original)
    assert companion.current(sm, source_ns=BASE, now_ns=BASE + 1_000_000, drain=False) is not None
    # A normal source wait prunes the output cache, retaining only its original metadata proof.
    assert companion.current(sm, source_ns=0, now_ns=BASE + 60_000_000, drain=False) is None
    assert BASE not in companion.samples
    epoch = companion.integrity_epoch
    now = BASE + (210_000_000 if bad == 'expired' else 110_000_000)
    successor = intent(now - 1_000_000, 2)
    if bad == 'raw_invalid':
      successor.valid = False
    drain(companion, successor)
    sm.alive[SERVICE] = False
    if bad == 'cached_invalid':
      sm.valid[SERVICE] = False
    elif bad == 'cached_tampered':
      changed = intent(BASE, 1, latched=False)
      sm.update_msgs(now / 1e9, [changed.as_reader()])
      sm.alive[SERVICE] = False
    assert companion.current(sm, source_ns=0, now_ns=now, drain=False) is None
    assert companion.current(sm, source_ns=successor.logMonoTime, now_ns=now, drain=False) is None
    if bad == 'none':
      assert companion.integrity_epoch == epoch and companion.latest[1]
      sm.update_msgs(now / 1e9, [successor.as_reader()])
      assert companion.current(sm, source_ns=successor.logMonoTime, now_ns=now, drain=False) is not None
    else:
      assert companion.integrity_epoch > epoch


def test_actual_bolt_source_wait_survives_raw_successor_before_conflated_cache_recovers():
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
    sd, controls, step = case._authority_callers(False, cp=cp, genuine_events=True)
    for tick in (0, 1):
      axis, _, command = step(tick, native_request_long=False, panda_controls_allowed=False)
      assert axis.lateralActive and command.latActive and not command.longActive
    old = intent(BASE + 10_000_000, 2)
    for tick in (4, 7):
      axis, _, command = step(tick, offset_ns=1_000_000 if tick == 4 else 0, timeout=True, state_stamp=BASE + 10_000_000,
        intent_message=old, native_request_long=False, panda_controls_allowed=False)
      assert str(axis.faultReason) == 'availabilityPause'
      assert not axis.lateralActive and not command.latActive and not command.longActive
    epoch = sd.aol_intent_companion.integrity_epoch
    original_update = sd.sm.update_msgs

    def cached_poll(now, messages):
      original_update(now, [m for m in messages if m.which() != SERVICE])
      sd.sm.alive[SERVICE] = False

    successor = intent(BASE + 110_000_000, 12)
    with patch.object(sd.sm, 'update_msgs', side_effect=cached_poll):
      axis, state, command = step(11, timeout=True, state_stamp=BASE + 10_000_000,
        intent_message=successor, native_request_long=False, panda_controls_allowed=False)
    assert sd.aol_intent_companion.integrity_epoch == epoch
    assert str(axis.faultReason) == 'availabilityPause' and axis.desiredLateral and axis.nativeAcknowledged
    assert not axis.lateralActive and not axis.longitudinalActive and axis.sourceCarStateMonoTime == 0
    assert not command.latActive and not command.longActive and not state.enabled
    assert EventName.controlsMismatch not in sd.events.names and not sd.aol_authority_lost
    axis, _, command = step(12, native_request_long=False, panda_controls_allowed=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    assert EventName.controlsMismatch not in sd.events.names
