"""Production shared event/axis/Controls callers with controlled native peers."""
from contextlib import ExitStack
import os
from unittest import mock

import pytest
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_vehicle_policy_cache import profile

FAMILIES = ('sdgm', 'ascm', 'flashed', 'bolt2017', 'bolt2018', 'bolt2022', 'boltacc',
            'honda', 'hyundai', 'toyota', 'ford', 'mazda', 'tesla')


def params(family):
  from opendbc.car.gm.values import CAR
  if family == 'sdgm':
    from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
    return sdgm_params(alpha=True, sascm=True, radar=True)
  if family == 'ascm':
    from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
    return ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=True, sascm=True, radar=True)
  if family == 'flashed':
    from opendbc.car.gm.tests.test_volt_flashed_obd import params as flashed
    return flashed()
  if family.startswith('bolt'):
    from opendbc.car.gm.tests.test_bolt_pedal import params as bolt
    candidate = {'bolt2017': CAR.CHEVROLET_BOLT_CC_2017, 'bolt2018': CAR.CHEVROLET_BOLT_CC_2018_2021,
                 'bolt2022': CAR.CHEVROLET_BOLT_CC_2022_2023, 'boltacc': CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL}[family]
    return bolt(candidate, pedal=True, alpha_long=True, camera=True)
  return profile(family)


def callers(cleanup, family, full_op=False):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  cleanup.callback(case.doCleanups)
  cleanup.enter_context(OpenpilotPrefix())
  cleanup.enter_context(mock.patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}))
  return case._authority_callers(full_op, cp=params(family), genuine_events=True)


def radar_fault(sd, error, *, other=None):
  original = sd.update_events
  def update(cs):
    radar = messaging.new_message('radarState', valid=True)
    setattr(radar.radarState.radarErrors, error, True)
    sd.sm.data['radarState'] = radar.radarState.as_reader()
    original(cs)
    # Stock-long ports do not consume radarState, but share lateral event policy.
    if not sd.CP.openpilotLongitudinalControl:
      sd.events.add(EventName.radarTempUnavailable if error == 'radarUnavailableTemporary' else EventName.radarFault)
    if other is not None:
      sd.events.add(other)
  return mock.patch.object(sd, 'update_events', side_effect=update)


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('error', ('radarFault', 'radarUnavailableTemporary', 'canError'))
def test_radar_only_fault_keeps_lateral_for_every_port(family, error):
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, family)
    for tick in (0, 1):
      axis, _, command = step(tick, native_request_long=False, panda_controls_allowed=False)
    assert axis.lateralActive and command.latActive
    expected = EventName.radarTempUnavailable if error == 'radarUnavailableTemporary' else EventName.radarFault
    with radar_fault(sd, error):
      for tick in range(2, 22):
        axis, host, command = step(tick, native_request_long=False, panda_controls_allowed=False)
        assert expected in sd.events.names
        assert axis.lateralActive and command.latActive
        assert not host.enabled and not command.longActive and not axis.longitudinalActive
        assert not sd.aol_authority_lost and str(axis.faultReason) == 'none'
        assert EventName.controlsMismatch not in sd.events.names


@pytest.mark.parametrize('family', ('sdgm', 'ascm', 'bolt2018', 'boltacc', 'honda'))
def test_radar_withdraws_long_immediately_without_lateral_loss_or_auto_resume(family):
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, family, full_op=True)
    assert sd.CP.openpilotLongitudinalControl
    cs = fixture.car_state()
    cs.cruiseState.enabled = True
    for tick in (0, 1):
      axis, host, command = step(tick, observed_state=cs)
    assert host.enabled and command.longActive and command.latActive
    with radar_fault(sd, 'canError'):
      for tick in range(2, 322):
        axis, host, command = step(tick, observed_state=cs, native_request_long=False,
                                   long_allowed=False, panda_controls_allowed=False)
        assert EventName.radarFault in sd.events.names and EventName.canError not in sd.events.names
        assert not host.enabled and not command.longActive and not axis.longitudinalActive
        assert axis.lateralActive and command.latActive
        assert EventName.controlsMismatch not in sd.events.names
    for tick in range(322, 330):
      axis, host, command = step(tick, native_request_long=False, long_allowed=False, panda_controls_allowed=False)
      assert command.latActive and not command.longActive and not host.enabled


@pytest.mark.parametrize('fault', ('canError', 'canBusMissing', 'lowMemory', 'steerUnavailable', 'modeldLagging'))
def test_radar_never_hides_other_faults(fault):
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, 'sdgm')
    step(0, native_request_long=False)
    with radar_fault(sd, 'canError', other=getattr(EventName, fault)):
      axis, _, command = step(1, native_request_long=False)
    assert not command.latActive and not axis.lateralActive
    assert EventName.radarFault in sd.events.names and getattr(EventName, fault) in sd.events.names


@pytest.mark.parametrize('negative', ({'allowed': False}, {'native_valid': False}, {'panda_fault': True}, {'calibrated': False}))
def test_radar_cannot_bypass_native_or_model_health(negative):
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, 'sdgm')
    step(0, native_request_long=False)
    with radar_fault(sd, 'canError'):
      for tick in range(1, 31):
        axis, _, command = step(tick, native_request_long=False, **negative)
    assert not axis.lateralActive and not command.latActive


@pytest.mark.parametrize('service', ('radarState', 'modelV2'))
@pytest.mark.parametrize('health', ('valid', 'alive', 'freq_ok'))
def test_only_radar_service_failure_is_longitudinal(service, health):
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, 'sdgm')
    step(0, native_request_long=False)
    original = sd.update_events
    def update(cs):
      getattr(sd.sm, health)[service] = False
      original(cs)
    with mock.patch.object(sd, 'update_events', side_effect=update):
      axis, _, command = step(1, native_request_long=False)
    if service == 'radarState':
      assert EventName.radarFault in sd.events.names
      assert axis.lateralActive and command.latActive
    else:
      assert not axis.lateralActive and not command.latActive


@pytest.mark.parametrize('names,expected', ((['radard'], EventName.radarFault),
                                         (['radard', 'modeld'], EventName.processNotRunning)))
def test_dead_radard_does_not_hide_other_processes(names, expected):
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, 'sdgm')
    step(0, native_request_long=False)
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.driving_process_failures', return_value=names):
      axis, _, command = step(1, native_request_long=False)
    assert expected in sd.events.names
    assert bool(axis.lateralActive and command.latActive) == (names == ['radard'])


@pytest.mark.parametrize('family', ('sdgm', 'honda'))
def test_radar_fault_present_at_start_does_not_require_prior_engagement(family):
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, family)
    with radar_fault(sd, 'radarFault'):
      for tick in range(20):
        axis, host, command = step(tick, native_request_long=False, panda_controls_allowed=False)
        assert axis.lateralActive and command.latActive and not host.enabled and not command.longActive


def test_radar_exception_does_not_arm_unrequested_aol():
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, 'sdgm', full_op=True)
    step(0, latched=False)
    with radar_fault(sd, 'radarFault'):
      axis, host, command = step(1, latched=False, native_request_long=False, panda_controls_allowed=False)
    assert not axis.lateralActive and not command.latActive and not host.enabled and not command.longActive
