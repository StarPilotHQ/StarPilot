from contextlib import ExitStack
from unittest.mock import patch

import pytest
from opendbc.car.hyundai.values import CAR
from opendbc.car.gm.values import CAR as GM
from opendbc.car.gm.tests.test_bolt_cc import params as bolt_params
from opendbc.car.gm.tests.test_volt_flashed_obd import params as flashed_params
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests.test_canfd_stock_profiles import params as hkg_params
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


def profiles():
  return [
    bolt_params(GM.CHEVROLET_BOLT_CC_2018_2021, present=True, pedal=True),
    flashed_params(),
    hkg_params(CAR.KIA_EV6),
    hkg_params(CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN),
  ]


@pytest.mark.parametrize('index', range(4))
@pytest.mark.parametrize('initially_open', [False, True])
def test_door_open_keeps_independent_aol_active_without_critical_alert(index, initially_open):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, _, step = case._authority_callers(False, cp=profiles()[index], genuine_events=True)
    initial = fixture.car_state()
    initial.doorOpen = initially_open
    for tick in range(20):
      axis, host, command = step(tick, observed_state=initial)
    assert axis.lateralActive and command.latActive and not host.enabled
    door = fixture.car_state()
    door.doorOpen = True
    for tick in range(20, 40):
      axis, host, command = step(tick, observed_state=door)
      assert log.OnroadEvent.EventName.doorOpen in sd.events.names
      assert axis.lateralActive and command.latActive and not command.longActive
      assert not sd.aol_soft_takeover_alert and not host.enabled
      assert host.alertStatus != log.SelfdriveState.AlertStatus.critical
      assert 'doorOpen/softDisable' not in sd.AM.alerts
    for tick in range(40, 60):
      axis, host, command = step(tick)
    assert axis.lateralActive and command.latActive and not command.longActive and not host.enabled


@pytest.mark.parametrize('index', [0, 2])
def test_other_soft_fault_with_open_door_still_warns(index):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, _, step = case._authority_callers(False, cp=profiles()[index], genuine_events=True)
    for tick in range(20):
      axis, host, command = step(tick)
    assert command.latActive
    original = sd.update_events

    def with_memory_fault(cs):
      original(cs)
      sd.events.add(log.OnroadEvent.EventName.lowMemory)

    door = fixture.car_state()
    door.doorOpen = True
    with patch.object(sd, 'update_events', side_effect=with_memory_fault):
      axis, host, command = step(20, observed_state=door)
    assert not command.latActive and not command.longActive
    assert host.alertType == 'lowMemory/softDisable'
    assert host.alertStatus == log.SelfdriveState.AlertStatus.critical


@pytest.mark.parametrize('index', [0, 2])
def test_door_open_during_ordinary_engagement_keeps_takeover(index):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, _, step = case._authority_callers(True, cp=profiles()[index], genuine_events=True)
    current = fixture.car_state()
    current.cruiseState.enabled = True
    for tick in range(20):
      axis, host, command = step(tick, observed_state=current)
    assert host.enabled
    door = current.as_reader().as_builder()
    door.doorOpen = True
    saw_ordinary_alert = False
    for tick in range(20, 340):
      axis, host, command = step(tick, observed_state=door, panda_controls_allowed=False, native_request_long=False, long_allowed=False)
      assert command.latActive and not command.longActive and not host.enabled
      assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      saw_ordinary_alert |= host.alertType == 'doorOpen/softDisable'
    assert saw_ordinary_alert
    assert command.latActive and not command.longActive and not host.enabled
    for tick in range(340, 360):
      axis, host, command = step(tick, native_request_long=False, long_allowed=False, panda_controls_allowed=False)
    assert command.latActive and not command.longActive and not host.enabled


def test_physical_door_alone_vetoes_only_longitudinal():
  from types import SimpleNamespace
  from openpilot.starpilot.aol.runtime import decide_axes

  door = fixture.car_state()
  door.doorOpen = True
  decision = decide_axes(
    standard_lateral=True,
    standard_longitudinal=True,
    intent=SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False),
    native=SimpleNamespace(requestedLateral=True, lateralAllowed=True, requestedLongitudinal=True, longitudinalAllowed=True),
    car_state=door,
    initialized=True,
    model_ready=True,
    no_entry=False,
    immediate_disable=False,
    dm_lockout=False,
    pause_brake_mps=0.0,
  )
  assert decision.desired_lateral and decision.lateral_active
  assert not decision.desired_longitudinal and not decision.longitudinal_active
