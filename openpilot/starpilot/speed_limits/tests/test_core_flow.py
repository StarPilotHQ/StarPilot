"""Live SLC caps the planner while the driver and native cruise retain SET."""

from collections import deque
from dataclasses import replace

import pytest

from openpilot.common.constants import CV
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits.runtime import Action, CruiseEvent, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import PedalOverride, parse
from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
from openpilot.starpilot.speed_limits.tests import test_runtime_replay as replay

START = 2_000_000_000
# Observed stop jitter, plus a larger normal filter undershoot.
STOPPED_SPEEDS = (0., -0.0005728941, 0.0000374307, -0.0000785884, -.08, 0.)


@pytest.fixture
def drive():
  fixture = replay.RuntimeReplayTests()
  fixture.setUp()
  return fixture.sm, fixture.cp


def tick(runtime, sm, cp, step, **kwargs):
  now = START + step * 50_000_000
  sm.advance(now)
  sm['slcDashboardObservation'].validUntilMonoTime = now + 1_000_000_000
  output = runtime.step(sm, cp, now_ns=now, **kwargs)
  assert not output.result.errors
  return output


def controller(offset=0., *, confirm=False):
  settings = parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCPriority2': 'Off', 'SLCConfirmation': int(confirm)})
  return Runtime(replace(settings, offsets=OffsetSchedule((OffsetBand(0., None, offset),))))


@pytest.mark.parametrize('pcm', [False, True])
@pytest.mark.parametrize('metric', [False, True])
def test_limits_follow_below_driver_ceiling_without_rewriting_set(drive, pcm, metric):
  sm, cp = drive
  cp.pcmCruise = pcm
  unit = CV.KPH_TO_MS if metric else CV.MPH_TO_MS
  selected = 65 * unit * 3.6
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected
  selected = float(sm['carState'].vCruise)
  runtime = controller(5 * unit)
  planner = LongitudinalPlanner(cp)
  for step, limit, status in ((0, 55, 'applied'), (1, 70, 'above_driver'), (2, 40, 'applied')):
    sm['slcDashboardObservation'].speedMps = limit * unit
    output = tick(runtime, sm, cp, step)
    assert output.result.ceiling.speed_mps == pytest.approx((limit + 5) * unit)
    planner.update(sm, cruise_ceiling=output.result.ceiling)
    assert planner.last_cruise_ceiling_status == status
    assert sm['carState'].vCruise == sm['carState'].vCruiseCluster == selected


@pytest.mark.parametrize('selected', [50., 110.])
def test_cruise_adjustments_change_only_the_driver_ceiling(drive, selected):
  sm, cp = drive
  runtime = controller()
  tick(runtime, sm, cp, 0)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected
  event = CruiseEvent(1, START+50_000_000, 100/3.6, selected/3.6,
                      'accel' if selected > 100 else 'decel', 'card-producer', START+50_000_000)
  changed = tick(runtime, sm, cp, 1, cruise_event=event)
  assert changed.result.ceiling.speed_mps == 25.
  assert not changed.message.slcState.driverOverrideActive
  assert sm['carState'].vCruise == selected


def wheel_crossing(drive, *, metric=True, enabled=True, previous_kph=90., selected_kph=91., offset=0.):
  sm, cp = drive
  cp.pcmCruise = False
  car_state = sm['carState']
  car_state.canValid = True
  car_state.vCruise = car_state.vCruiseCluster = previous_kph
  settings = replace(controller(offset).settings, wheel_override=enabled)
  runtime = Runtime(settings, is_metric=metric)
  first = tick(runtime, sm, cp, 0).message.slcState
  car_state.vCruise = car_state.vCruiseCluster = selected_kph
  event = CruiseEvent(1, START + 50_000_000, previous_kph / 3.6, selected_kph / 3.6, 'accel',
                      'card-producer', START + 50_000_000, session_id=runtime.session_id,
                      wheel_override_since_ns=first.wheelOverrideSince)
  return runtime, event


@pytest.mark.parametrize('metric,previous_kph,selected_kph,limit,offset', [
  (True, 90., 91., 25., 0.),
  (True, 54.5, 55.5, 54. / 3.6, 0.),
  (False, 89., 90.6, 55. * CV.MPH_TO_MS, 0.),
  (False, 86.9, 88.5, 55. * CV.MPH_TO_MS, -.5 * CV.MPH_TO_MS),
])
def test_wheel_tap_crosses_displayed_limit_and_uses_native_set(drive, metric, previous_kph, selected_kph, limit, offset):
  from struct import pack, unpack
  sm, cp = drive
  sm['slcDashboardObservation'].speedMps = limit
  sm['carState'].vEgoCluster += 1.0
  runtime, event = wheel_crossing(drive, metric=metric, previous_kph=previous_kph, selected_kph=selected_kph, offset=offset)
  event = replace(event, previous_mps=unpack('f', pack('f', event.previous_mps))[0])
  assert event.wheel_override_since_ns == START
  # Card publishes carState before the event. The new SET can be sampled first.
  assert tick(runtime, sm, cp, 1).result.ceiling is not None
  crossed = tick(runtime, sm, cp, 2, cruise_event=event)
  status = crossed.message.slcState
  assert crossed.result.ceiling is None and status.driverOverrideActive and status.overrideBasis == 'wheel'
  assert status.effectiveClusterTarget == pytest.approx(selected_kph / 3.6)
  assert not status.isLimitingMaxSet
  planner = LongitudinalPlanner(cp)
  planner.update(sm, cruise_ceiling=crossed.result.ceiling)
  assert planner.last_cruise_ceiling_status == 'absent'

  sm['carState'].vCruise = sm['carState'].vCruiseCluster = previous_kph
  lowered = tick(runtime, sm, cp, 3)
  assert lowered.result.ceiling is not None and not lowered.message.slcState.driverOverrideActive
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected_kph
  assert not tick(runtime, sm, cp, 4, cruise_event=event).message.slcState.driverOverrideActive


@pytest.mark.parametrize('scenario', ['off', 'already above', 'controller', 'hold', 'foreign producer', 'foreign session',
                                      'different SET', 'stop or curve', 'confirmation', 'new limit', 'disengaged', 'pcm',
                                      'standstill', 'units changed'])
def test_wheel_override_requires_current_unconsumed_physical_crossing(drive, scenario):
  sm, cp = drive
  prior, selected = (100., 101.) if scenario == 'already above' else (90., 91.)
  runtime, event = wheel_crossing(drive, enabled=scenario != 'off', previous_kph=prior, selected_kph=selected)
  changes = {
    'controller': {'wheel_override_since_ns': 0},
    'hold': {'long_press': True},
    'foreign producer': {'producer_session': 'old-card'},
    'foreign session': {'session_id': 'old-slc'},
    'different SET': {'selected_mps': event.selected_mps - .05},
  }
  event = replace(event, **changes.get(scenario, {}))
  if scenario in ('confirmation', 'new limit'):
    sm['slcDashboardObservation'].speedMps = 23.
    if scenario == 'confirmation':
      runtime.settings = replace(runtime.settings, acceptance=acc.Policy(confirm_lower=True))
  if scenario == 'disengaged':
    sm['carControl'].enabled = sm['carControl'].longActive = sm['selfdriveState'].enabled = False
  if scenario == 'pcm':
    cp.pcmCruise = True
  if scenario == 'standstill':
    sm['carState'].standstill = True
  if scenario == 'units changed':
    runtime.update_offsets(runtime.settings.offsets, is_metric=False)
  output = tick(runtime, sm, cp, 1, cruise_event=event, wheel_override_blocked=scenario == 'stop or curve')
  assert not output.message.slcState.driverOverrideActive
  if scenario in ('stop or curve', 'standstill'):
    assert output.message.slcState.wheelOverrideSince == 0
    sm['carState'].standstill = False
    assert not tick(runtime, sm, cp, 2, cruise_event=replace(event, event_id=2)).message.slcState.driverOverrideActive


@pytest.mark.parametrize('limit_kph', [51., 90.])
def test_wheel_override_survives_units_change_until_set_returns_to_limit(drive, limit_kph):
  sm, cp = drive
  sm['slcDashboardObservation'].speedMps = limit_kph / 3.6
  runtime, event = wheel_crossing(drive, previous_kph=limit_kph, selected_kph=limit_kph + 1.)
  assert tick(runtime, sm, cp, 1, cruise_event=event).message.slcState.overrideBasis == 'wheel'

  # At 51 km/h both speeds round to 32 mph, even though SET remains higher.
  runtime.update_offsets(runtime.settings.offsets, is_metric=False)
  changed = tick(runtime, sm, cp, 2)
  assert changed.result.ceiling is None
  assert changed.message.slcState.overrideBasis == 'wheel'
  assert changed.message.slcState.wheelOverrideSince != event.wheel_override_since_ns

  sm['carState'].vCruise = sm['carState'].vCruiseCluster = limit_kph
  returned = tick(runtime, sm, cp, 3)
  assert returned.result.ceiling.speed_mps == pytest.approx(limit_kph / 3.6)
  assert not returned.message.slcState.driverOverrideActive


@pytest.mark.parametrize('cruise_standstill', [False, True])
def test_wheel_override_survives_engaged_stop(drive, cruise_standstill):
  sm, cp = drive
  runtime, event = wheel_crossing(drive)
  assert tick(runtime, sm, cp, 1, cruise_event=event).message.slcState.overrideBasis == 'wheel'

  stopped = sm['carState'].cruiseState if cruise_standstill else sm['carState']
  stopped.standstill = True
  sm['controlsState'].longControlState = 'stopping'
  for step, speed in enumerate((0.,) if cruise_standstill else STOPPED_SPEEDS, 2):
    sm['carState'].vEgo = sm['carState'].vEgoCluster = speed
    held = tick(runtime, sm, cp, step)
    assert held.message.slcState.overrideBasis == 'wheel'
    assert held.message.slcState.effectiveClusterTarget == pytest.approx(91. / 3.6)
    assert held.message.slcState.wheelOverrideSince == 0
    assert held.result.ceiling is None

  stopped.standstill = False
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 1.
  sm['controlsState'].longControlState = 'pid'
  assert tick(runtime, sm, cp, step + 1).message.slcState.overrideBasis == 'wheel'


def test_wheel_override_survives_gas_handoff_without_latching_pedal_speed(drive):
  sm, cp = drive
  runtime, event = wheel_crossing(drive)
  assert tick(runtime, sm, cp, 1, cruise_event=event).message.slcState.overrideBasis == 'wheel'
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 91. / 3.6
  sm['carState'].gasPressed = True
  sm['carControl'].longActive = False
  sm['controlsState'].longControlState = 'off'
  assert tick(runtime, sm, cp, 2).result.state.pedal_target_mps == 0.
  sm['carState'].gasPressed = False
  assert tick(runtime, sm, cp, 3).result.state.wheel_override_metric is True
  sm['carControl'].longActive = True
  sm['controlsState'].longControlState = 'pid'
  assert tick(runtime, sm, cp, 4).message.slcState.overrideBasis == 'wheel'
  sm['slcDashboardObservation'].speedMps = 23.
  accepted = tick(runtime, sm, cp, 5)
  assert accepted.result.ceiling.speed_mps == 23. and not accepted.message.slcState.driverOverrideActive


@pytest.mark.parametrize('missing', ['absent', 'unknown', 'stale'])
@pytest.mark.parametrize('stopped', [False, True])
def test_source_gap_uses_the_previous_limit_and_current_offset(drive, missing, stopped):
  sm, cp = drive
  runtime = controller(2.)
  first = tick(runtime, sm, cp, 0)
  assert first.result.ceiling.speed_mps == 27.
  if stopped:
    sm['carState'].standstill = True
    sm['carState'].vEgo = sm['carState'].vEgoCluster = -.08
  sm['slcDashboardObservation'].status = missing
  runtime.update_offsets(OffsetSchedule((OffsetBand(0., None, 4.),)))
  gap = tick(runtime, sm, cp, 1)
  assert gap.result.ceiling.speed_mps == 29.
  assert gap.message.slcState.hasCeiling and gap.message.slcState.observationKind == missing
  sm['slcDashboardObservation'].status = 'valid'
  recovered = tick(runtime, sm, cp, 2)
  assert recovered.result.ceiling.speed_mps == 29.
  assert sm['carState'].vCruise == 100.


def test_previous_limit_resumes_after_disengagement_and_session_reset(drive):
  sm, cp = drive
  runtime = controller()
  tick(runtime, sm, cp, 0)
  sm['slcDashboardObservation'].status = 'unknown'
  sm['selfdriveState'].enabled = sm['carControl'].enabled = sm['carControl'].longActive = False
  sm['controlsState'].longControlState = 'off'
  assert tick(runtime, sm, cp, 1).result.ceiling is None
  sm['selfdriveState'].enabled = sm['carControl'].enabled = sm['carControl'].longActive = True
  sm['controlsState'].longControlState = 'pid'
  assert tick(runtime, sm, cp, 2).result.ceiling.speed_mps == 25.
  runtime.reset()
  assert tick(runtime, sm, cp, 3).result.ceiling.speed_mps == 25.


def test_force_deceleration_suppresses_a_cap_without_forgetting_source_gap_continuity(drive):
  sm, cp = drive
  runtime = controller()
  tick(runtime, sm, cp, 0)
  sm['slcDashboardObservation'].status = 'unknown'
  sm['controlsState'].forceDecel = True
  assert tick(runtime, sm, cp, 1).result.ceiling is None
  sm['controlsState'].forceDecel = False
  assert tick(runtime, sm, cp, 2).result.ceiling.speed_mps == 25.
  assert sm['carState'].vCruise == 100.


@pytest.mark.parametrize('choice', ['accept', 'reject'])
def test_confirmation_changes_the_slc_target_and_preserves_set(drive, choice):
  sm, cp = drive
  runtime = controller()
  tick(runtime, sm, cp, 0)
  runtime.settings = replace(runtime.settings, acceptance=acc.Policy(confirm_lower=True, confirm_higher=True))
  sm['slcDashboardObservation'].speedMps = 15.
  pending = tick(runtime, sm, cp, 1)
  assert pending.result.ceiling.speed_mps == 25.
  state = pending.message.slcState
  action = Action(runtime.session_id, 1, state.decisionId, state.presentationId, choice)
  chosen = tick(runtime, sm, cp, 2, request=action)
  assert chosen.result.ceiling.speed_mps == (15. if choice == 'accept' else 25.)
  assert not chosen.message.slcState.hasPending
  assert sm['carState'].vCruise == 100.


def test_pedal_release_reapplies_limit_without_a_sticky_override(drive):
  sm, cp = drive
  runtime = controller()
  runtime.settings = replace(runtime.settings, pedal_override=PedalOverride.OFF)
  tick(runtime, sm, cp, 0)
  sm['carState'].gasPressed = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 30.
  sm['carControl'].longActive = False
  sm['controlsState'].longControlState = 'off'
  pressed = tick(runtime, sm, cp, 1)
  assert not pressed.message.slcState.driverOverrideActive
  sm['carState'].gasPressed = False
  sm['carControl'].longActive = True
  sm['controlsState'].longControlState = 'pid'
  released = tick(runtime, sm, cp, 2)
  assert released.result.ceiling.speed_mps == 25.
  assert not released.message.slcState.driverOverrideActive
  assert sm['carState'].vCruise == 100.


@pytest.mark.parametrize('mode,target', [(PedalOverride.SPEED_REACHED, 26.), (PedalOverride.SET_SPEED, 100. / 3.6)])
def test_gas_above_limit_holds_the_selected_override_after_release(drive, mode, target):
  sm, cp = drive
  runtime = controller()
  runtime.settings = replace(runtime.settings, pedal_override=mode)
  assert tick(runtime, sm, cp, 0).result.ceiling.speed_mps == 25.

  sm['carState'].gasPressed = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 24.
  assert not tick(runtime, sm, cp, 1).message.slcState.driverOverrideActive
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 26.
  sm['carControl'].longActive = False
  sm['controlsState'].longControlState = 'off'
  pressed = tick(runtime, sm, cp, 2).message.slcState
  assert pressed.overrideBasis == 'pedal'
  assert pressed.effectiveClusterTarget == pytest.approx(target)

  sm['carState'].gasPressed = False
  assert tick(runtime, sm, cp, 3).result.ceiling is None
  sm['carControl'].longActive = True
  sm['controlsState'].longControlState = 'pid'
  released = tick(runtime, sm, cp, 4)
  assert released.message.slcState.overrideBasis == 'persistent'
  assert released.result.ceiling.speed_mps == pytest.approx(target)
  assert released.message.slcState.acceptedSpeedLimit == 25.
  assert released.message.slcState.speedLimit == 25.
  assert sm['carState'].vCruise == 100.
  planner = LongitudinalPlanner(cp)
  planner.update(sm, cruise_ceiling=released.result.ceiling)
  assert planner.last_cruise_ceiling_status == ('applied' if mode is PedalOverride.SPEED_REACHED else 'above_driver')

  sm['carState'].standstill = True
  sm['controlsState'].longControlState = 'stopping'
  for step, speed in enumerate(STOPPED_SPEEDS, 5):
    sm['carState'].vEgo = sm['carState'].vEgoCluster = speed
    held = tick(runtime, sm, cp, step)
    assert held.message.slcState.overrideBasis == 'persistent'
    assert held.result.ceiling == released.result.ceiling
    assert held.message.slcState.effectiveClusterTarget == pytest.approx(target)
    assert sm['carState'].vCruise == sm['carState'].vCruiseCluster == 100.

  sm['carState'].standstill = False
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 1.
  sm['controlsState'].longControlState = 'pid'
  assert tick(runtime, sm, cp, step + 1).result.ceiling == released.result.ceiling
  sm['carControl'].enabled = sm['carControl'].longActive = sm['selfdriveState'].enabled = False
  assert tick(runtime, sm, cp, step + 2).result.ceiling is None
  sm['carControl'].enabled = sm['carControl'].longActive = sm['selfdriveState'].enabled = True
  resumed = tick(runtime, sm, cp, step + 3)
  assert resumed.result.ceiling.speed_mps == 25.
  assert not resumed.message.slcState.driverOverrideActive


@pytest.mark.parametrize('change', ['lower SET', 'higher limit', 'accept', 'reject', 'adopt'])
@pytest.mark.parametrize('stopped', [False, True])
def test_driver_and_limit_changes_preserve_override_rules(drive, change, stopped):
  sm, cp = drive
  runtime = controller()
  tick(runtime, sm, cp, 0)
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 29.
  sm['carState'].gasPressed = True
  assert tick(runtime, sm, cp, 1).result.ceiling.speed_mps == pytest.approx(100. / 3.6)
  sm['carState'].gasPressed = False
  if stopped:
    sm['carState'].standstill = True
    sm['carState'].vEgo = sm['carState'].vEgoCluster = -.08
  action = None
  expected = 25.
  if change == 'lower SET':
    sm['carState'].vCruise = sm['carState'].vCruiseCluster = 90.
  elif change == 'higher limit':
    sm['slcDashboardObservation'].speedMps = expected = 28.
  else:
    runtime.settings = replace(runtime.settings, acceptance=acc.Policy())
    sm['slcDashboardObservation'].speedMps = expected = 20.
    pending = tick(runtime, sm, cp, 2).message.slcState
    assert pending.hasPending and pending.driverOverrideActive
    action = Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, change)
    if change == 'reject':
      expected = 100. / 3.6
  changed = tick(runtime, sm, cp, 3, request=action)
  assert changed.result.ceiling.speed_mps == expected
  assert changed.message.slcState.driverOverrideActive == (change == 'reject')
  assert not changed.message.slcState.hasPending
  if change == 'reject':
    assert changed.message.slcState.acceptedSpeedLimit == 25.
    assert changed.message.slcState.speedLimit == 20.
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 100.
  assert tick(runtime, sm, cp, 4).result.ceiling.speed_mps == expected


@pytest.mark.parametrize('mode', [PedalOverride.SPEED_REACHED, PedalOverride.SET_SPEED])
def test_explicit_accept_blocks_override_until_the_held_pedal_is_released(drive, mode):
  sm, cp = drive
  runtime = controller()
  tick(runtime, sm, cp, 0)
  runtime.settings = replace(runtime.settings, acceptance=acc.Policy(), pedal_override=mode)
  sm['slcDashboardObservation'].speedMps = 15.
  sm['carState'].gasPressed = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 26.
  prompt = tick(runtime, sm, cp, 1).message.slcState
  action = Action(runtime.session_id, 1, prompt.decisionId, prompt.presentationId, 'accept')
  for step, request in ((2, action), (3, None)):
    accepted = tick(runtime, sm, cp, step, request=request).message.slcState
    assert accepted.acceptedSpeedLimit == accepted.effectiveCap == 15.
    assert not accepted.driverOverrideActive
  sm['carState'].gasPressed = False
  assert tick(runtime, sm, cp, 4).result.ceiling.speed_mps == 15.
  sm['carState'].gasPressed = True
  assert tick(runtime, sm, cp, 5).message.slcState.driverOverrideActive


@pytest.mark.parametrize('wheel', [False, True])
@pytest.mark.parametrize('in_time', [False, True])
def test_host_keeps_in_flight_confirmation_bound_to_its_capture_time(drive, tmp_path, wheel, in_time):
  from openpilot.common.params import Params
  from openpilot.starpilot.speed_limits.planner_host import SavedSlcLifecycle

  sm, cp = drive
  host = SavedSlcLifecycle(Params(str(tmp_path)), cp, {})
  host.runtime = runtime = controller()
  tick(runtime, sm, cp, 0)
  runtime.settings = replace(runtime.settings, acceptance=acc.Policy())
  sm['slcDashboardObservation'].speedMps = 15.
  prompt = tick(runtime, sm, cp, 1).message.slcState
  deadline = prompt.pendingSinceMonoTime + acc.CONFIRMATION_NS
  observed = deadline + (-5_000_000 if in_time else 1_000_000)
  actions, cruise = deque(), deque()
  if wheel:
    cruise.append(CruiseEvent(1, observed, 0., 0., 'accel', 'card-producer', deadline + 2_000_000,
                             'confirmationAccept', runtime.session_id, prompt.decisionId, prompt.presentationId))

  def host_tick(now, *, card_ns=None):
    sm.advance(card_ns if card_ns is not None else now)
    sm['slcDashboardObservation'].validUntilMonoTime = now + 1_000_000_000
    return host.step(sm, now, actions, cruise, drive_changed=False).message.slcState

  expired = host_tick(deadline + 3_000_000, card_ns=deadline)
  assert not expired.hasPending and expired.acceptedSpeedLimit == 25.
  if wheel:
    assert len(cruise) == 1
  else:
    actions.append((observed, Action(runtime.session_id, 1, prompt.decisionId, prompt.presentationId, 'accept', observed)))
  joined = host_tick(deadline + 50_000_000)
  assert not cruise and not actions
  assert joined.acceptedSpeedLimit == (15. if in_time else 25.)
  if not in_time:
    assert host_tick(deadline + 100_000_000).status == 'timeout_reject'


def test_offset_edit_applies_to_the_previous_limit_without_accepting_the_proposal(drive):
  sm, cp = drive
  runtime = controller()
  tick(runtime, sm, cp, 0)
  runtime.settings = replace(runtime.settings, acceptance=acc.Policy(confirm_lower=True, confirm_higher=True))
  sm['slcDashboardObservation'].speedMps = 30.
  assert tick(runtime, sm, cp, 1).message.slcState.hasPending
  runtime.update_offsets(OffsetSchedule((OffsetBand(0., None, 2.),)))
  assert tick(runtime, sm, cp, 2).result.ceiling.speed_mps == 27.
  sm['slcDashboardObservation'].speedMps = 25.
  assert tick(runtime, sm, cp, 3).result.ceiling.speed_mps == 27.


def test_raw_cluster_conversion_is_applied_once_including_during_source_loss(drive):
  sm, cp = drive
  runtime = controller(2.)
  sm['carState'].vEgoCluster = sm['carState'].vEgo + 1.
  assert tick(runtime, sm, cp, 0).result.ceiling.speed_mps == 26.
  sm['slcDashboardObservation'].status = 'unknown'
  sm['carState'].vEgoCluster = sm['carState'].vEgo + .5
  assert tick(runtime, sm, cp, 1).result.ceiling.speed_mps == 26.5


@pytest.mark.parametrize('stopped,raw,cluster,cap', [
  (True, 0., 0., 25.),
  (True, -.08, -.08, 25.),
  (True, -.08, .1, 24.9),
  (True, .1, -.08, 25.),
  (True, .1, .2, 24.9),
  (False, -.08, 0., None),
  (False, 0., -.08, None),
  (True, float('nan'), 0., None),
  (True, 0., float('nan'), None),
  (True, float('inf'), 0., None),
  (True, 0., float('inf'), None),
  (True, float('-inf'), 0., None),
  (True, 0., float('-inf'), None),
])
def test_ego_coordinates_are_normalized_only_at_standstill(drive, stopped, raw, cluster, cap):
  sm, cp = drive
  runtime = controller()
  tick(runtime, sm, cp, 0)
  sm['carState'].standstill = stopped
  sm['carState'].vEgo, sm['carState'].vEgoCluster = raw, cluster
  output = tick(runtime, sm, cp, 1)
  assert not output.message.slcState.driverOverrideActive
  if cap is None:
    assert output.result.ceiling is None
  else:
    assert output.result.ceiling.speed_mps == pytest.approx(cap)
  assert (sm['carState'].vEgo, sm['carState'].vEgoCluster) == pytest.approx((raw, cluster), nan_ok=True)


@pytest.mark.parametrize('unavailable', ['stale', 'invalid', 'not alive', 'CAN timeout', 'cruise standstill only'])
def test_standstill_normalization_requires_fresh_vehicle_evidence(drive, unavailable):
  sm, cp = drive
  runtime = controller()
  tick(runtime, sm, cp, 0)
  sm['carState'].standstill = unavailable != 'cruise standstill only'
  sm['carState'].cruiseState.standstill = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = -.08
  now = START + 50_000_000
  sm.advance(now)
  if unavailable == 'stale':
    sm.logMonoTime['carState'] = now - 1_000_000_000
  elif unavailable == 'invalid':
    sm.valid['carState'] = False
  elif unavailable == 'not alive':
    sm.alive['carState'] = False
  elif unavailable == 'CAN timeout':
    sm['carState'].canTimeout = True
  output = runtime.step(sm, cp, now_ns=now)
  assert not output.result.errors
  assert output.result.ceiling is None
  assert not output.message.slcState.driverOverrideActive


@pytest.mark.parametrize('pcm', [False, True])
def test_factory_longitudinal_never_gets_an_slc_cap(drive, pcm):
  sm, cp = drive
  cp.pcmCruise, cp.openpilotLongitudinalControl = pcm, False
  sm['carControl'].longActive = False
  sm['controlsState'].longControlState = 'off'
  output = tick(controller(), sm, cp, 0)
  assert output.result.ceiling is None
  assert not output.message.slcState.hasCeiling


@pytest.mark.parametrize('pcm', [False, True])
def test_curve_press_remains_available_and_cannot_reuse_an_slc_confirmation(drive, pcm):
  from opendbc.car.structs import car
  from openpilot.selfdrive.car.card import curve_press_receipt
  from openpilot.selfdrive.car.cruise import VCruiseHelper, SlcPendingConfirmation
  sm, cp = drive
  cp.pcmCruise = pcm
  helper = VCruiseHelper(cp)
  cs = car.CarState(canValid=True, cruiseState={'available': True, 'enabled': True, 'speed': 25.},
    buttonEvents=[car.CarState.ButtonEvent(type='accelCruise', pressed=True)])
  helper.update_v_cruise(cs, True, True, slc_enabled=True)
  def receipt():
    return curve_press_receipt(cs, helper, sm, curve_replay=True, slc_replay=False,
                               host_long_active=True, observed_ns=START)
  assert receipt()[0] == 'curveAccelPress'
  helper.update_v_cruise(cs, True, True, SlcPendingConfirmation('drive', 1, 1), slc_enabled=True)
  assert receipt() is None
  cs.buttonEvents = [car.CarState.ButtonEvent(type='accelCruise', pressed=False)]
  helper.update_v_cruise(cs, True, True, slc_enabled=False)
  assert receipt() is None
  cs.standstill = True
  cs.buttonEvents = [car.CarState.ButtonEvent(type='accelCruise', pressed=True)]
  assert receipt() is None
