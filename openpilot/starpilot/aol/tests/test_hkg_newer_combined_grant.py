"""Newer native FULL acknowledgement must not erase an existing AOL intent."""
from dataclasses import replace

import pytest

from openpilot.cereal import messaging
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.tests.test_kona_ccnc_long_aol import kona_params
from openpilot.starpilot.aol.tests.test_classic_long_profiles import params as classic_params
from openpilot.starpilot.aol.tests.test_ev6_physical_authorization import ev6_params
from openpilot.starpilot.aol.tests.test_hkg_shared_cancel_guard import captured_context
from openpilot.starpilot.aol.transport_pause import _newer_combined_intent_native
from openpilot.starpilot.aol.wire import encode_safety
from openpilot.starpilot.car.hyundai.aol import policy_for


@pytest.mark.parametrize('profile', ('ev6', 'elantra', 'kona', 'kona_hev'))
@pytest.mark.parametrize('fault', ('none', 'brake', 'gas', 'acc', 'can', 'eps', 'permanent_eps',
                                 'axis_ack', 'axis_lat', 'axis_desired_lat', 'axis_desired_long',
                                 'lat_denied', 'long_denied', 'lat_request', 'long_request',
                                 'native_controls', 'native_rx', 'native_heartbeat', 'native_fault',
                                 'native_identity', 'native_session', 'expired', 'same_health',
                                 'raw_fault', 'raw_rx', 'raw_heartbeat', 'raw_invalid', 'unmarked', 'stock'))
def test_newer_full_native_grant_preserves_only_qualified_intent(profile, fault):
  cp, cs, sm, feedback, _, native, now = captured_context()
  if profile == 'ev6':
    cp = ev6_params()
  elif profile == 'elantra':
    cp = classic_params(CAR.HYUNDAI_ELANTRA_2021, lda=True)
  else:
    cp = kona_params(CAR.HYUNDAI_KONA_HEV_2ND_GEN if profile == 'kona_hev' else CAR.HYUNDAI_KONA_2ND_GEN)
  policy = policy_for(cp)
  cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  config = cp.safetyConfigs[0]
  health = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now - 10_000_000)
  health.pandaStates = [slot.to_dict() for slot in sm['pandaStates']]
  panda = health.pandaStates
  panda[0].safetyModel, panda[0].safetyParam = config.safetyModel, config.safetyParam
  panda[0].alternativeExperience = cp.alternativeExperience
  slot = replace(native.pandaInventory[0], safetyModel=int(config.safetyModel.raw),
                 safetyParam=config.safetyParam, alternativeExperience=cp.alternativeExperience, controlsAllowed=True)
  native = replace(native, safetyModel=int(config.safetyModel.raw), safetyParam=config.safetyParam,
                   longitudinalAllowed=True, pandaInventory=(slot,))
  sm.logMonoTime['pandaStates'] = now - 10_000_000
  axis = sm['aolAxisState'].as_builder()
  cs.cruiseState.enabled = False
  if fault in ('brake', 'gas', 'acc', 'can', 'eps', 'permanent_eps'):
    field, value = {'brake': ('brakePressed', True), 'gas': ('gasPressed', True), 'acc': ('accFaulted', True),
                    'can': ('canValid', False), 'eps': ('steerFaultTemporary', True),
                    'permanent_eps': ('steerFaultPermanent', True)}[fault]
    setattr(cs, field, value)
  elif fault.startswith('axis_'):
    field = {'axis_ack': 'nativeAcknowledged', 'axis_lat': 'lateralActive',
             'axis_desired_lat': 'desiredLateral', 'axis_desired_long': 'desiredLongitudinal'}[fault]
    setattr(axis, field, False)
  elif fault in ('lat_denied', 'long_denied', 'lat_request', 'long_request'):
    field = {'lat_denied': 'lateralAllowed', 'long_denied': 'longitudinalAllowed',
             'lat_request': 'requestedLateral', 'long_request': 'requestedLongitudinal'}[fault]
    native = replace(native, **{field: False})
  elif fault.startswith('native_'):
    if fault == 'native_session':
      native = replace(native, axisSessionId='foreign-session')
    else:
      field, value = {'native_controls': ('controlsAllowed', False), 'native_rx': ('safetyRxChecksInvalid', True),
                      'native_heartbeat': ('heartbeatLost', True), 'native_fault': ('faults', 4),
                      'native_identity': ('safetyParam', 0)}[fault]
      native = replace(native, pandaInventory=(replace(slot, **{field: value}),))
  elif fault == 'expired':
    now += 200_000_001
  elif fault == 'same_health':
    sm.logMonoTime['pandaStates'] = native.sourcePandaStatesMonoTime
  elif fault in ('raw_fault', 'raw_rx', 'raw_heartbeat'):
    field, value = {'raw_fault': ('faults', ['interruptRateCan1']), 'raw_rx': ('safetyRxChecksInvalid', True),
                    'raw_heartbeat': ('heartbeatLost', True)}[fault]
    setattr(panda[0], field, value)
  elif fault == 'raw_invalid':
    sm.valid['pandaStates'] = False
  elif fault == 'unmarked':
    cp.alternativeExperience = 0
  elif fault == 'stock':
    cp.openpilotLongitudinalControl, cp.pcmCruise = False, True
  sm['pandaStates'] = health.as_reader().pandaStates
  sm['aolSafetyWire'] = encode_safety(native)
  result = _newer_combined_intent_native(sm, cp, cs, axis, now)
  assert (result is not None) == (fault == 'none')
  if result is not None:
    assert result == native
    assert not panda[0].controlsAllowed
    assert cs.cruiseState.enabled is False
