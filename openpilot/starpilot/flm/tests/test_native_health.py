"""Real public native parsers; declared unit wires, no hardware/RX claim.

The timing tuple is from the actual learner refusal. Fresh current wire identity
below is explicit unit data, not a fabricated reconstruction of the lost wire.
"""
from types import SimpleNamespace
import time

import pytest
from opendbc.car.car_helpers import interfaces
from opendbc.car.ford.values import CAR as FORD
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.wire import SafetyState, PandaSlot, encode_safety
from openpilot.starpilot.flm.native_health import current_native_health, current_ford_native_health
from openpilot.starpilot.flm.runtime_adapter_registry import current_authority, current_ford_authority
from openpilot.starpilot.flm.tests.test_registered_controls_global import published_cp

OBSERVED = 52650499071971
CONTROL = 52650608574930
EXPIRES = 52650699071971
RECEIVED = 52650741010722


def panda_event(cp, stamp, *, heartbeat=False):
  event = messaging.new_message('pandaStates', len(cp.safetyConfigs), valid=True, logMonoTime=stamp)
  for index, (panda, config) in enumerate(zip(event.pandaStates, cp.safetyConfigs, strict=True)):
    panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
    panda.alternativeExperience = cp.alternativeExperience
    panda.controlsAllowed = index == len(cp.safetyConfigs)-1
    panda.heartbeatLost = heartbeat
  return event


def typed_state(cp, observed, *, session='declared-native-health-session', heartbeat=False, denied=False):
  sm = messaging.SubMaster(['carState', 'pandaStates', 'aolSafetyWire'])
  config = cp.safetyConfigs[0]
  slots = tuple(PandaSlot(i, 'declared-panda-'+str(i), int(c.safetyModel.raw), int(c.safetyParam),
    int(cp.alternativeExperience), str(c.safetyModel) not in ('silent', 'noOutput'))
    for i, c in enumerate(cp.safetyConfigs))
  for offset in (-300_000_000, -200_000_000, -100_000_000, 0):
    stamp = observed+offset
    cs = messaging.new_message('carState', valid=True, logMonoTime=stamp)
    cs.carState.canValid = True
    cs.carState.gearShifter = 'drive'
    native = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=stamp)
    native.aolSafetyWire = encode_safety(SafetyState(1, True, stamp, stamp+200_000_000,
      int(config.safetyModel.raw), int(config.safetyParam), not denied, not denied, True, True,
      'declared-panda-0', session, stamp, slots))
    events = [cs, panda_event(cp, stamp, heartbeat=heartbeat), native]
    sm.update_msgs(stamp/1e9, [messaging.log_from_bytes(e.to_bytes()) for e in events])
  return sm


def toyota_cp():
  cp = interfaces[TOYOTA.TOYOTA_RAV4_TSS2].get_non_essential_params(TOYOTA.TOYOTA_RAV4_TSS2)
  with OpenpilotPrefix():
    return published_cp(cp, Params()).as_reader()


def old_record(identity):
  return SimpleNamespace(native_required=True, native_allowed=True, native_identity=identity,
    native_observed_ns=OBSERVED, control_ns=CONTROL, native_expires_ns=EXPIRES)


def test_actual_refusal_times_keep_old_issuance_proof_and_fresh_current_authority():
  cp = toyota_cp()
  original_sm = typed_state(cp, OBSERVED)
  old_allowed, old_native, identity = current_authority(original_sm, cp, now_ns=CONTROL, required=True)
  assert old_allowed and old_native is not None
  sm = typed_state(cp, RECEIVED-1_000_000)
  allowed, native, current_identity = current_authority(sm, cp, now_ns=RECEIVED, required=True)
  assert allowed and native is not None and current_identity == identity
  control = old_record(identity)
  assert OBSERVED <= CONTROL <= EXPIRES < RECEIVED
  assert current_native_health(sm, cp, control, RECEIVED) is True


@pytest.mark.parametrize('fault', ('expired_current', 'session', 'cp', 'heartbeat', 'denied',
  'record_denied', 'record_before_observed', 'record_after_expiry', 'malformed_original_lease', 'future_control'))
def test_typed_current_and_original_issuance_failures_remain_denied(fault):
  cp = toyota_cp()
  healthy = typed_state(cp, RECEIVED-1_000_000)
  allowed, native, identity = current_authority(healthy, cp, now_ns=RECEIVED, required=True)
  assert allowed and native is not None
  control = old_record(identity)
  now = RECEIVED
  sm = healthy
  if fault == 'expired_current':
    now += 300_000_000
  elif fault == 'session':
    sm = typed_state(cp, RECEIVED-1_000_000, session='rotated-declared-session')
  elif fault == 'cp':
    cp = cp.as_builder()
    cp.safetyConfigs[0].safetyParam += 1
  elif fault == 'heartbeat':
    sm = typed_state(cp, RECEIVED-1_000_000, heartbeat=True)
  elif fault == 'denied':
    sm = typed_state(cp, RECEIVED-1_000_000, denied=True)
  elif fault == 'record_denied':
    control.native_allowed = False
  elif fault == 'record_before_observed':
    control.control_ns = OBSERVED-1
  elif fault == 'record_after_expiry':
    control.control_ns = EXPIRES+1
  elif fault == 'malformed_original_lease':
    control.native_observed_ns = EXPIRES+1
  else:
    control.native_observed_ns = RECEIVED
    control.control_ns = RECEIVED+1
    control.native_expires_ns = RECEIVED+200_000_000
  assert current_native_health(sm, cp, control, now) is False


def ford_state(cp, observed, *, heartbeat=False):
  sm = messaging.SubMaster(['pandaStates'])
  for offset in (-300_000_000, -200_000_000, -100_000_000, 0):
    stamp = observed+offset
    e = panda_event(cp, stamp, heartbeat=heartbeat)
    sm.update_msgs(stamp/1e9, [messaging.log_from_bytes(e.to_bytes())])
  return sm


def ford_setup():
  from opendbc.car.ford.aol import BP_LATERAL
  cp = interfaces[FORD.FORD_ESCAPE_MK4].get_non_essential_params(FORD.FORD_ESCAPE_MK4)
  cp.safetyConfigs[-1].safetyParam |= BP_LATERAL
  now = time.monotonic_ns()
  sm = ford_state(cp, now-1_000_000)
  allowed, identity = current_ford_authority(sm, cp, now_ns=now)
  assert allowed
  age = RECEIVED-CONTROL
  row = SimpleNamespace(native_allowed=True, native_identity=identity, native_observed_ns=now-(RECEIVED-OBSERVED),
    native_validated_ns=now-age-1_000_000, event_ns=now-age, native_expires_ns=now-(RECEIVED-EXPIRES),
    native_command_ns=now-age-2_000_000, announced_control_ns=now-age-2_000_000)
  return sm, cp.as_reader(), row, now


def test_ford_original_validation_and_event_lease_are_separate_from_fresh_current_health():
  sm, cp, row, now = ford_setup()
  assert current_ford_native_health(sm, cp, row, now) is True


@pytest.mark.parametrize('fault', ('expired_current', 'identity', 'cp', 'heartbeat', 'record_denied',
  'validation_after_expiry', 'event_after_expiry', 'future_event', 'command_mismatch'))
def test_ford_current_health_original_validation_and_command_failures_remain_denied(fault):
  sm, cp, row, now = ford_setup()
  if fault == 'expired_current':
    now += 2_000_000_000
  elif fault == 'identity':
    row.native_identity = '0'*64
  elif fault == 'cp':
    cp = cp.as_builder()
    cp.safetyConfigs[-1].safetyParam ^= 1
  elif fault == 'heartbeat':
    sm = ford_state(cp, now-1_000_000, heartbeat=True)
  elif fault == 'record_denied':
    row.native_allowed = False
  elif fault == 'validation_after_expiry':
    row.native_validated_ns = row.native_expires_ns+1
  elif fault == 'event_after_expiry':
    row.event_ns = row.native_expires_ns+1
  elif fault == 'future_event':
    row.native_observed_ns = now
    row.native_validated_ns = now+1
    row.event_ns = now+2
    row.native_expires_ns = now+200_000_000
  else:
    row.native_command_ns += 1
  assert current_ford_native_health(sm, cp, row, now) is False
