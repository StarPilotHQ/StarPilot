"""A GM proof expiring during Controls preparation must consume queued evidence."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.tests.test_bolt_pedal import params as bolt_params
from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
from opendbc.car.gm.values import CAR
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


def configuration(profile):
  if profile == 'ascm-long-one-pedal':
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
    cp = params(CAR.CHEVROLET_VOLT_ASCM, sascm=True, alpha=True, radar=True)
    VehicleStartupPreferences(volt_one_pedal=True, gm_auto_hold=True).prepare(cp)
    assert cp.safetyConfigs[0].safetyParam == 0xD114
    return cp
  if profile.startswith('ascm'):
    return params(CAR.CHEVROLET_VOLT_ASCM, sascm=True, alpha=profile.endswith('long'))
  if profile.startswith('sdgm'):
    return sdgm_params(alpha=profile.endswith('long'))
  candidate = CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL if profile == 'bolt-acc-pedal' else CAR.CHEVROLET_BOLT_CC_2018_2021
  return bolt_params(candidate, pedal=True, camera=True)


@pytest.mark.parametrize('profile', ['ascm-stock', 'ascm-long', 'sdgm-stock', 'sdgm-long', 'bolt-cc-pedal', 'bolt-acc-pedal'])
@pytest.mark.parametrize('arrival', ['healthy', 'empty', 'temporary_fault', 'withdrawn', 'native_invalid',
                                    'native_expired', 'can_invalid', 'longitudinal'])
def test_gm_axis_expiry_during_qualification_uses_only_fresh_queued_permission(profile, arrival):
  cp = configuration(profile)
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    _, controls, step = case._authority_callers(False, cp=cp)
    controls.lane_centering_host = None
    _, _, initial = step(0, native_request_long=False, long_allowed=False)
    assert initial.latActive and not initial.longActive
    base = int(controls.sm.logMonoTime['aolAxisState'])
    deadline = int(controls.sm['aolAxisState'].validUntilMonoTime)
    clock = [deadline - 10_000_000]
    fresh = deadline + (210_000_000 if arrival == 'native_expired' else 10_000_000)
    queued = []
    for service in ('carState', 'aolAxisState', 'selfdriveState'):
      event = messaging.new_message(service, valid=True, logMonoTime=fresh)
      setattr(event, service, controls.sm[service])
      if service == 'carState':
        event.carState.steerFaultTemporary = arrival == 'temporary_fault'
        event.carState.canValid = arrival != 'can_invalid'
      elif service == 'aolAxisState':
        axis = event.aolAxisState
        axis.sequence += 1
        axis.sourceCarStateMonoTime = axis.observedMonoTime = fresh
        axis.validUntilMonoTime = fresh + 30_000_000
        axis.lateralActive = axis.desiredLateral = arrival != 'withdrawn'
        axis.longitudinalActive = axis.desiredLongitudinal = arrival == 'longitudinal'
      queued.append(event.as_reader())

    def qualify(active):
      # Route6a: proof is fresh on entry, expired by the axis-selection gate.
      clock[0] = fresh
      return active

    def receive(_timeout):
      if arrival != 'empty':
        controls.sm.update_msgs(fresh / 1e9, queued)
      if arrival == 'native_invalid':
        controls.sm.valid['aolSafetyWire'] = False

    cleanup.enter_context(patch.dict('os.environ', {'REPLAY': '0'}))
    cleanup.enter_context(patch('time.monotonic_ns', side_effect=lambda: clock[0]))
    cleanup.enter_context(patch.object(controls.longitudinal_inputs, 'qualify_active', side_effect=qualify))
    receive_mock = cleanup.enter_context(patch.object(controls.sm, 'update', side_effect=receive))
    command, _ = controls.state_control()
    assert receive_mock.call_args_list == [((0,),)] * (2 if arrival == 'empty' else 1)
    assert command.latActive == (arrival in ('healthy', 'longitudinal'))
    assert not command.longActive
    # Neither the original nor the received proof acquires a longer lease.
    assert deadline == base + 30_000_000
    expected_deadline = deadline if arrival == 'empty' else fresh + 30_000_000
    assert controls.sm['aolAxisState'].validUntilMonoTime == expected_deadline


@pytest.mark.parametrize('profile', ['ascm-long', 'sdgm-long', 'bolt-cc-pedal', 'bolt-acc-pedal'])
def test_expired_combined_gm_permission_withdraws_both_axes_without_fresh_receipt(profile):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    _, controls, step = case._authority_callers(True, cp=configuration(profile))
    controls.lane_centering_host = None
    _, _, initial = step(0)
    assert initial.latActive and initial.longActive
    deadline = controls.sm['aolAxisState'].validUntilMonoTime
    clock = [deadline - 10_000_000]

    def qualify(active):
      clock[0] = deadline + 10_000_000
      return active

    cleanup.enter_context(patch.dict('os.environ', {'REPLAY': '0'}))
    cleanup.enter_context(patch('time.monotonic_ns', side_effect=lambda: clock[0]))
    cleanup.enter_context(patch.object(controls.longitudinal_inputs, 'qualify_active', side_effect=qualify))
    receive = cleanup.enter_context(patch.object(controls.sm, 'update'))
    command, _ = controls.state_control()
    assert receive.call_args_list == [((0,),), ((0,),)]
    assert not command.latActive and not command.longActive


@pytest.mark.parametrize('profile', ['ascm-long', 'ascm-long-one-pedal', 'sdgm-long', 'bolt-cc-pedal', 'bolt-acc-pedal'])
@pytest.mark.parametrize('delivery', ['queued', 'publishing'])
@pytest.mark.parametrize('arrival', ['healthy', 'empty', 'temporary_fault', 'withdrawn', 'native_invalid',
                                    'native_expired', 'can_invalid', 'stale_axis', 'foreign_session',
                                    'longitudinal_veto', 'ordinary_disabled'])
def test_combined_gm_expiry_receives_queued_evidence_without_extending_permission(profile, arrival, delivery):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    _, controls, step = case._authority_callers(True, cp=configuration(profile))
    controls.lane_centering_host = None
    _, _, initial = step(0)
    assert initial.latActive and initial.longActive
    axis = controls.sm['aolAxisState']
    deadline = int(axis.validUntilMonoTime)
    # Route bc: fresh at entry, expired by 145868 ns at the gate; a newer proof
    # was queued 243373 ns before the previous proof's deadline.
    clock = [deadline - 23_380_738]
    gate = deadline + (210_000_000 if arrival == 'native_expired' else 145_868)
    receipt = gate if arrival == 'native_expired' else deadline - 243_373
    if delivery == 'publishing' and arrival != 'native_expired':
      # Route 2a: the immediate receive raced the publishing process.
      clock[0] = deadline + 1_371_273
      gate = deadline + 1_731_582
      receipt = deadline + 1_468_560
    queued = []
    for service in ('carState', 'aolAxisState', 'selfdriveState'):
      event = messaging.new_message(service, valid=True, logMonoTime=receipt)
      setattr(event, service, controls.sm[service])
      if service == 'carState':
        event.carState.steerFaultTemporary = arrival == 'temporary_fault'
        event.carState.canValid = arrival != 'can_invalid'
      elif service == 'aolAxisState':
        fresh = event.aolAxisState
        fresh.sequence += 1
        fresh.sourceCarStateMonoTime = fresh.observedMonoTime = receipt
        fresh.validUntilMonoTime = deadline if arrival == 'stale_axis' else receipt + 30_000_000
        fresh.lateralActive = fresh.desiredLateral = arrival != 'withdrawn'
        fresh.longitudinalActive = fresh.desiredLongitudinal = arrival != 'withdrawn'
        fresh.qualified = arrival != 'can_invalid'
        if arrival == 'foreign_session':
          fresh.sessionId = 'unacknowledged-new-session'
      elif service == 'selfdriveState' and arrival == 'ordinary_disabled':
        event.selfdriveState.enabled = False
      queued.append(event.as_reader())

    def qualify(active):
      clock[0] = gate
      return active and arrival != 'longitudinal_veto'

    receives = []

    def receive(_timeout):
      receives.append(_timeout)
      if delivery == 'publishing' and len(receives) == 1:
        return
      if arrival != 'empty':
        controls.sm.update_msgs(receipt / 1e9, queued)
      if arrival == 'native_invalid':
        controls.sm.valid['aolSafetyWire'] = False

    cleanup.enter_context(patch.dict('os.environ', {'REPLAY': '0'}))
    cleanup.enter_context(patch('time.monotonic_ns', side_effect=lambda: clock[0]))
    cleanup.enter_context(patch.object(controls.longitudinal_inputs, 'qualify_active', side_effect=qualify))
    receiver = cleanup.enter_context(patch.object(controls.sm, 'update', side_effect=receive))
    poller = cleanup.enter_context(patch('openpilot.cereal.messaging.Poller'))
    poller.return_value.poll.side_effect = lambda timeout: clock.__setitem__(0, gate + 4_000_000)
    command, _ = controls.state_control()
    assert command.latActive == (arrival in ('healthy', 'longitudinal_veto', 'ordinary_disabled'))
    assert command.longActive == (arrival in ('healthy', 'temporary_fault'))
    if delivery == 'publishing' or arrival == 'empty':
      poller.return_value.registerSocket.assert_called_once_with(controls.sm.sock['aolAxisState'])
      poller.return_value.poll.assert_called_once_with(10)
    else:
      poller.assert_not_called()
    assert receiver.call_args_list == [((0,),)] * (2 if arrival == 'empty' or delivery == 'publishing' else 1)
    assert axis.validUntilMonoTime == deadline
    expected = deadline if arrival in ('empty', 'stale_axis') else receipt + 30_000_000
    assert controls.sm['aolAxisState'].validUntilMonoTime == expected


def test_combined_snapshot_refresh_requires_explicit_opt_in():
  from types import SimpleNamespace
  from unittest.mock import Mock
  from openpilot.starpilot.aol.runtime import refresh_expired_lateral_snapshot

  axis = SimpleNamespace(qualified=True, nativeAcknowledged=True, sessionId='session',
                         lateralActive=True, desiredLateral=True, longitudinalActive=True,
                         desiredLongitudinal=True, observedMonoTime=100, validUntilMonoTime=200)

  class SM:
    seen = valid = alive = {'aolAxisState': True}
    logMonoTime = {'aolAxisState': 100}
    update = Mock()

    def __getitem__(self, key):
      return axis

  sm = SM()
  assert not refresh_expired_lateral_snapshot(sm, now_ns=201)
  sm.update.assert_not_called()
