"""Ford FULL->LAT Cancel receipt ordering; controlled host/native wire regression."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from opendbc.car import structs
from openpilot.cereal import log
from openpilot.starpilot.aol.runtime import decide_axes
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety

NOW = 10_000_000_000


class Peers:
  def __init__(self, messages):
    self.messages = {m.which(): m for m in messages}
    self.logMonoTime = {k: m.logMonoTime for k, m in self.messages.items()}
    self.seen = dict.fromkeys(self.messages, True)
    self.valid = dict(self.seen)
    self.alive = dict(self.seen)

  def __getitem__(self, name):
    return getattr(self.messages[name], name)


def message(name, stamp, size=None):
  m = log.Event.new_message(logMonoTime=stamp, valid=True)
  m.init(name) if size is None else m.init(name, size)
  return m


def sample(cp, stamp, sequence, native, *, combined, event=True, raw_controls=False):
  w = message('aolSafetyWire', native.observedMonoTime, 0)
  w.aolSafetyWire = encode_safety(native)
  a = message('aolAxisState', stamp)
  a.aolAxisState = {'sessionId': 'axis', 'faultSessionId': 'axis', 'sequence': sequence, 'qualified': True,
    'faultReason': 'none', 'observedMonoTime': stamp, 'sourceCarStateMonoTime': stamp, 'validUntilMonoTime': stamp+30_000_000,
    'intentProducerSessionId': 'producer', 'intentIntegrityEpoch': 1, 'faultEventMonoTime': stamp,
    'desiredLateral': True, 'lateralActive': True, 'desiredLongitudinal': combined, 'longitudinalActive': combined,
    'nativeAcknowledged': True, 'mode': 'combined' if combined else 'lateralOnly'}
  p = message('pandaStates', stamp, 1)
  p.pandaStates[0] = {'safetyModel': 'ford', 'safetyParam': 16387, 'alternativeExperience': 32, 'controlsAllowed': raw_controls}
  e = message('onroadEvents', stamp, int(event))
  if event:
    e.onroadEvents[0] = {'name': 'pcmDisable', 'userDisable': True}
  return Peers([w.as_reader(), a.as_reader(), p.as_reader(), e.as_reader()])


def fixture():
  cp = structs.CarParams(brand='ford', carFingerprint='FORD_MUSTANG_MACH_E_MK1', flags=3,
    alternativeExperience=32, steerControlType='angle', transmissionType='automatic',
    pcmCruise=True, openpilotLongitudinalControl=True, safetyConfigs=[{'safetyModel': 'ford', 'safetyParam': 16387}])
  cs = structs.CarState(canValid=True, vEgo=11., vEgoRaw=11., gearShifter='drive')
  cs.cruiseState.available = cs.cruiseState.enabled = True
  slot = PandaSlot(0, 'panda', 6, 16387, 32, True)
  native = SafetyState(1, True, NOW, NOW+200_000_000, 6, 16387, True, True, True, True,
    'panda', 'axis', sourcePandaStatesMonoTime=NOW, pandaInventory=(slot,))
  f = TransportPauseFeedback(cp)
  sm = sample(cp, NOW, 1, native, combined=True, event=False, raw_controls=True)
  assert not f.observe(sm, cp, cs, NOW, latched=True, intent_producer_session='producer')
  cs.cruiseState.enabled = False
  stamp = NOW + 20_000_000
  sm = sample(cp, stamp, 2, native, combined=False)
  return cp, cs, f, sm, native, stamp + 2_000_000


def change(sm, name, fn):
  m = sm.messages[name].as_builder()
  fn(getattr(m, name))
  sm.messages[name] = m.as_reader()


def wire(sm, native):
  m = sm.messages['aolSafetyWire'].as_builder()
  m.logMonoTime = native.observedMonoTime
  m.aolSafetyWire = encode_safety(native)
  sm.messages['aolSafetyWire'] = m.as_reader()
  sm.logMonoTime['aolSafetyWire'] = native.observedMonoTime


def invoke(cp, cs, f, sm, now, latched=True):
  return f.observe(sm, cp, cs, now, latched=latched, intent_producer_session='producer')


def test_raw_cancel_before_native_echo_retains_lat_not_long_without_renewing_receipts():
  cp, cs, f, sm, native, now = fixture()
  before = dict(sm.logMonoTime)
  assert not invoke(cp, cs, f, sm, now)
  assert sm.logMonoTime == before
  assert sm['aolAxisState'].validUntilMonoTime == NOW+50_000_000
  intent = SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False)
  axes = decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent, native=native,
    car_state=cs, initialized=True, model_ready=True, no_entry=False, immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
  assert axes.lateral_active and not axes.longitudinal_active
  intent.allowedLatch = False
  axes = decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent, native=native,
    car_state=cs, initialized=True, model_ready=True, no_entry=False, immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
  assert not axes.lateral_active and not axes.longitudinal_active


def test_native_echo_first_is_unchanged():
  cp, cs, f, sm, native, now = fixture()
  wire(sm, replace(native, observedMonoTime=NOW+20_000_000, validUntilMonoTime=NOW+220_000_000,
    requestedLongitudinal=False, longitudinalAllowed=False,
    pandaInventory=(replace(native.pandaInventory[0], controlsAllowed=False),)))
  assert not invoke(cp, cs, f, sm, now)


@pytest.mark.parametrize('fault', ['lat_denied', 'missing_native', 'expired_native', 'source_stale', 'source_missing',
  'axis_stale', 'raw_missing', 'raw_fault', 'native_fault', 'event_stale', 'event_missing', 'event_wrong',
  'explicit_off', 'epoch', 'gas', 'can_invalid'])
def test_cancel_cannot_retain_unproven_lateral(fault):
  cp, cs, f, sm, native, now = fixture()
  latched = True
  if fault == 'lat_denied':
    wire(sm, replace(native, lateralAllowed=False))
  elif fault == 'missing_native':
    sm.seen['aolSafetyWire'] = False
  elif fault == 'expired_native':
    wire(sm, replace(native, observedMonoTime=now-200_000_001, validUntilMonoTime=now-1))
  elif fault in ('source_stale', 'source_missing'):
    change(sm, 'aolAxisState', lambda a: setattr(a, 'sourceCarStateMonoTime', now-30_000_001 if fault=='source_stale' else 0))
  elif fault == 'axis_stale':
    now = NOW+50_000_001
  elif fault == 'raw_missing':
    sm.seen['pandaStates'] = False
  elif fault == 'raw_fault':
    change(sm, 'pandaStates', lambda p: setattr(p[0], 'safetyRxChecksInvalid', True))
  elif fault == 'native_fault':
    wire(sm, replace(native, pandaInventory=(replace(native.pandaInventory[0], faults=1),)))
  elif fault == 'event_stale':
    sm.logMonoTime['onroadEvents'] = now-30_000_001
  elif fault == 'event_missing':
    sm.seen['onroadEvents'] = False
  elif fault == 'event_wrong':
    change(sm, 'onroadEvents', lambda e: setattr(e[0], 'name', 'pedalPressed'))
  elif fault == 'explicit_off':
    latched = False
  elif fault == 'epoch':
    change(sm, 'aolAxisState', lambda a: setattr(a, 'intentIntegrityEpoch', 2))
  elif fault == 'gas':
    cs.gasPressed = True
  elif fault == 'can_invalid':
    cs.canValid = False
  retained = f._native_long_withdrawal_lateral_current(sm, cp, cs, sm['aolAxisState'], now, latched=latched, producer='producer')
  assert retained is None


def test_repeated_cancel_does_not_refresh_original_source_or_native_deadlines():
  cp, cs, f, sm, native, now = fixture()
  for stamp in [now, now+5_000_000, now+10_000_000]:
    assert not invoke(cp, cs, f, sm, stamp)
  # A second raw Cancel event cannot revive the original expired axis proof.
  sm.logMonoTime['onroadEvents'] = NOW+50_000_001
  change(sm, 'onroadEvents', lambda e: setattr(e[0], 'name', 'buttonCancel'))
  assert invoke(cp, cs, f, sm, NOW+50_000_001)
  assert sm['aolAxisState'].validUntilMonoTime == NOW+50_000_000
  assert native.validUntilMonoTime == NOW+200_000_000


def test_fresh_native_receipts_allow_repeated_cancel_and_new_full_engagement():
  cp, cs, f, sm, native, now = fixture()
  assert not invoke(cp, cs, f, sm, now)
  sequence = 3
  for cycle in range(3):
    stamp = NOW + (40 + cycle * 80) * 1_000_000
    # New native LAT-only receipt completes the previous withdrawal.
    native = replace(native, observedMonoTime=stamp, validUntilMonoTime=stamp+200_000_000,
      sourcePandaStatesMonoTime=stamp, requestedLongitudinal=False, longitudinalAllowed=False,
      pandaInventory=(replace(native.pandaInventory[0], controlsAllowed=False),))
    sm = sample(cp, stamp, sequence, native, combined=False)
    assert not invoke(cp, cs, f, sm, stamp+1_000_000)
    assert f.lateral_only_baseline
    # A fresh driver engagement and matching native FULL receipt, not Cancel,
    # reestablish the combined axis baseline.
    stamp += 20_000_000
    cs.cruiseState.enabled = True
    native = replace(native, observedMonoTime=stamp, validUntilMonoTime=stamp+200_000_000,
      sourcePandaStatesMonoTime=stamp, requestedLongitudinal=True, longitudinalAllowed=True,
      pandaInventory=(replace(native.pandaInventory[0], controlsAllowed=True),))
    sm = sample(cp, stamp, sequence+1, native, combined=True, event=False, raw_controls=True)
    assert not invoke(cp, cs, f, sm, stamp+1_000_000)
    assert not f.lateral_only_baseline
    # Second physical Cancel: newer raw LONG-off, previous FULL native receipt.
    stamp += 20_000_000
    cs.cruiseState.enabled = False
    sm = sample(cp, stamp, sequence+2, native, combined=False)
    original_deadline = native.validUntilMonoTime
    assert not invoke(cp, cs, f, sm, stamp+1_000_000)
    assert native.validUntilMonoTime == original_deadline
    sequence += 3


def test_real_ford_intent_owner_cancel_does_not_rearm_explicit_lateral_off():
  from openpilot.starpilot.aol.intent import AOL_TOGGLE, AolSettings
  from openpilot.starpilot.car.ford.aol import FordCardIntent

  cp, cs, _, _, _, now = fixture()
  settings = AolSettings(True, 0., AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0))
  owner = FordCardIntent(cp, settings)
  owner.update(cs, now_ns=now, fault_active=False)
  owner.observe_full_engagement(cs, enabled=False, active=False, source_ns=now,
    observation_valid=True, now_ns=now, fault_active=False)
  owner.observe_full_engagement(cs, enabled=True, active=True, source_ns=now+1,
    observation_valid=True, now_ns=now+1, fault_active=False)
  assert owner.allowed_latch
  cs.buttonEvents = [{'type': 'cancel', 'pressed': True}]
  owner.update(cs, now_ns=now+2, fault_active=False)
  assert owner.allowed_latch
  cs.buttonEvents = []
  owner.update(cs, now_ns=now+3, fault_active=False)
  cs.buttonEvents = [{'type': 'lkas', 'pressed': True}]
  owner.update(cs, now_ns=now+4, fault_active=False)
  assert not owner.allowed_latch and owner.pause_lateral
  cs.buttonEvents = [{'type': 'lkas', 'pressed': False}]
  owner.update(cs, now_ns=now+5, fault_active=False)
  cs.buttonEvents = [{'type': 'cancel', 'pressed': True}]
  owner.update(cs, now_ns=now+6, fault_active=False)
  assert not owner.allowed_latch and owner.pause_lateral
  cs.buttonEvents = []
  owner.update(cs, now_ns=now+7, fault_active=False)
  cs.buttonEvents = [{'type': 'lkas', 'pressed': True}]
  owner.update(cs, now_ns=now+8, fault_active=False)
  assert owner.allowed_latch and not owner.pause_lateral
