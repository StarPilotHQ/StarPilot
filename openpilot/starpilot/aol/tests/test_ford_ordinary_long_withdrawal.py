from contextlib import ExitStack
import pytest
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.cereal import log
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from opendbc.car import gen_empty_fingerprint
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.values import CAR


@pytest.fixture
def caller():
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as stack:
    stack.callback(case.doCleanups)
    stack.enter_context(OpenpilotPrefix())
    fingerprint = gen_empty_fingerprint()
    fingerprint[0] = {0x5A: 8, 0x3A6: 8, 0x3A7: 8}
    cp = CarInterface.get_params(CAR.FORD_MUSTANG_MACH_E_MK1, fingerprint, [], True, False, False)
    cp.alternativeExperience = 32
    assert cp.safetyConfigs[-1].safetyParam == 19 and cp.flags == 3
    sd, controls, step = case._authority_callers(True, cp=cp, genuine_events=True)
    cs = fixture.car_state()
    cs.cruiseState.enabled = True

    def drive(tick, **kw):
      return step(tick, latched=False, observed_state=cs, **kw)

    for tick in range(1, 10):
      a, s, c = drive(tick)
      assert s.enabled and a.lateralActive and a.longitudinalActive and c.latActive and c.longActive
    yield sd, cs, drive


@pytest.mark.parametrize('pedal', ['gas', 'brake'])
@pytest.mark.parametrize('steering', [False, True])
def test_ordinary_pedal_edge_withdraws_long_without_inventing_aol_latch(caller, pedal, steering):
  sd, cs, drive = caller
  kwargs = {'native_request_lat': True, 'allowed': True, 'native_request_long': True, 'long_allowed': False,
            'steering': steering, 'panda_controls_allowed': pedal == 'gas'}
  a, s, c = drive(10, **kwargs)
  assert not a.longitudinalActive and not c.longActive
  assert a.lateralActive and c.latActive and s.enabled
  assert not sd.aol_last_intent.allowedLatch and not sd.aol_last_intent.lateralArmed
  assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
  if pedal == 'brake':
    cs.brakePressed = True
  a, s, c = drive(11, gas=pedal == 'gas', **kwargs)
  assert not a.longitudinalActive and not c.longActive
  assert not sd.aol_last_intent.allowedLatch and not sd.aol_last_intent.lateralArmed
  assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
  if pedal == 'brake':
    assert not s.enabled and not s.active and not a.lateralActive and not c.latActive
    # Releasing brake alone must not rearm ordinary ACC or independent LAT.
    cs.brakePressed = False
    a, s, c = drive(12, steering=steering)
    assert not s.enabled and not a.lateralActive and not a.longitudinalActive and not c.latActive and not c.longActive
  else:
    assert s.enabled and a.lateralActive and c.latActive


@pytest.mark.parametrize('fault', ['lat_denied', 'native_invalid', 'rx_fault', 'relay_fault', 'prior_expired', 'prior_absent', 'proof_absent'])
@pytest.mark.parametrize('steering', [False, True])
def test_faults_and_missing_original_proof_remain_immediate(caller, fault, steering):
  sd, cs, drive = caller
  kw = {'native_request_lat': True, 'allowed': True, 'native_request_long': True, 'long_allowed': False, 'steering': steering, 'panda_controls_allowed': False}
  if fault == 'lat_denied':
    kw['allowed'] = False
  elif fault == 'native_invalid':
    kw['native_valid'] = False
  elif fault == 'rx_fault':
    kw['panda_fault'] = True
  elif fault == 'relay_fault':
    kw['relay_fault'] = True
  elif fault == 'prior_expired':
    p = list(sd.aol_published_input_proof)
    p[3] = 10_099_999_999
    sd.aol_published_input_proof = tuple(p)
  elif fault == 'prior_absent':
    sd.aol_published_input_native = None
  elif fault == 'proof_absent':
    sd.aol_published_input_proof = None
  a, s, c = drive(10, **kw)
  assert not a.longitudinalActive and not c.longActive
  assert not s.enabled and not a.lateralActive and not c.latActive
  assert log.OnroadEvent.EventName.controlsMismatch in sd.events.names


@pytest.mark.parametrize('steering', [False, True])
def test_persistent_native_long_denial_keeps_original_mismatch_owner(caller, steering):
  sd, cs, drive = caller
  for tick in range(10, 230):
    a, s, c = drive(tick, native_request_lat=True, allowed=True, native_request_long=True, long_allowed=False, panda_controls_allowed=False, steering=steering)
    assert not a.longitudinalActive and not c.longActive
    if log.OnroadEvent.EventName.controlsMismatch in sd.events.names:
      break
  assert sd.mismatch_counter >= 200
  assert not s.enabled and not a.lateralActive and not c.latActive
