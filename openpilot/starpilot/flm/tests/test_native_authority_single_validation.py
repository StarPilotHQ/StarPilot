"""Reuse the existing complete verifier path, never a retained live verdict."""
from dataclasses import replace
import pytest
from openpilot.cereal import messaging
from openpilot.starpilot.aol import runtime
from openpilot.starpilot.aol.transport_pause import _healthy_transport_native
from openpilot.starpilot.aol.wire import decode_safety, encode_safety
from openpilot.starpilot.flm.runtime_adapter_registry import current_authority, native_identity_digest, native_identity_key
from openpilot.starpilot.flm.tests.test_native_health import toyota_cp, typed_state

NOW = 5_000_000_000


def original_verdict(sm, cp, now):
  native = runtime.current_native(sm, cp, now_ns=now)
  identity = native_identity_digest(native_identity_key(native))
  allowed = bool(native is not None and native.requestedLateral and native.lateralAllowed and
    _healthy_transport_native(sm, cp, sm['carState'], str(native.axisSessionId), now,
      native=native, require_longitudinal=False) is not None)
  return allowed, native, identity


def test_healthy_call_validates_packet_inventory_once_but_live_permission_each_time(monkeypatch):
  cp = toyota_cp()
  sm = typed_state(cp, NOW-1_000_000)
  calls = []
  actual = runtime.native_inventory_matches_cp
  def observe(*args):
    calls.append(1)
    return actual(*args)
  monkeypatch.setattr(runtime, 'native_inventory_matches_cp', observe)
  from openpilot.starpilot.aol import transport_pause
  monkeypatch.setattr(transport_pause, 'native_inventory_matches_cp', observe)
  sm._aol_native_admission = None
  assert original_verdict(sm, cp, NOW)[0]
  assert len(calls) == 2
  calls.clear()
  sm._aol_native_admission = None
  assert current_authority(sm, cp, now_ns=NOW+1, required=True)[0]
  assert len(calls) == 1
  # The same received body still faces its actual original expiry on each call.
  assert not current_authority(sm, cp, now_ns=NOW+199_000_001, required=True)[0]


@pytest.mark.parametrize('fault', ('healthy','expired','future','denied','not_requested','serial',
  'panda_heartbeat','panda_config','car_fault','invalid','dead','unseen','header','cp'))
def test_full_tuple_matches_original_on_current_inputs_and_refusals(fault):
  cp = toyota_cp()
  sm = typed_state(cp, NOW-1_000_000)
  now = NOW
  if fault in ('denied','not_requested','serial'):
    native = decode_safety(sm['aolSafetyWire'])
    changes = {'denied':{'lateralAllowed':False},'not_requested':{'requestedLateral':False},'serial':{'pandaSerial':'other'}}
    sm.data['aolSafetyWire'] = encode_safety(replace(native, **changes[fault]))
  elif fault.startswith('panda_'):
    message = messaging.new_message('pandaStates', len(cp.safetyConfigs), valid=True)
    message.pandaStates = sm['pandaStates']
    if fault == 'panda_heartbeat':
      message.pandaStates[0].heartbeatLost = True
    else:
      message.pandaStates[0].safetyParam += 1
    sm.data['pandaStates'] = messaging.log_from_bytes(message.to_bytes()).pandaStates
  elif fault == 'car_fault':
    message = messaging.new_message('carState', valid=True)
    message.carState = sm['carState']
    message.carState.canTimeout = True
    sm.data['carState'] = messaging.log_from_bytes(message.to_bytes()).carState
  elif fault in ('invalid','dead','unseen'):
    getattr(sm, {'invalid':'valid','dead':'alive','unseen':'seen'}[fault])['aolSafetyWire'] = False
  elif fault == 'header':
    sm.logMonoTime['aolSafetyWire'] += 1
  elif fault == 'cp':
    cp = cp.as_builder()
    cp.safetyConfigs[0].safetyParam += 1
  elif fault == 'expired':
    now += 199_000_001
  elif fault == 'future':
    now -= 2_000_000
  expected = original_verdict(sm, cp, now)
  sm._aol_native_admission = None
  assert current_authority(sm, cp, now_ns=now, required=True) == expected
  assert expected[0] is (fault == 'healthy')


def test_new_body_and_vehicle_fault_are_checked_after_a_healthy_call():
  cp = toyota_cp()
  sm = typed_state(cp, NOW-1_000_000)
  assert current_authority(sm, cp, now_ns=NOW, required=True)[0]
  native = decode_safety(sm['aolSafetyWire'])
  sm.data['aolSafetyWire'] = encode_safety(replace(native, lateralAllowed=False))
  assert not current_authority(sm, cp, now_ns=NOW+1, required=True)[0]
