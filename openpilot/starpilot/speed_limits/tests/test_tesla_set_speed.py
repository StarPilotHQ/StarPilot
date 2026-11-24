"""Tesla set-speed rule: on a Model 3/Y whose software SET is mirrored to the car, SLC writes SET through card.

The rule needs both a car that started with the scroll frame admitted (opendbc.car.tesla.speed_sync.selected(CP)) and
the owner's switch, which the planner host passes in as Settings.set_speed_rule and card keeps on its cruise helper.
With the switch off, on a Tesla that started without the frame admitted, and on every other car, SLC keeps the planner
cap beneath SET exactly as before.
"""
from collections import deque
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

from opendbc.car.dashboard_speed_limit import Status
from opendbc.car.structs import car
from opendbc.car.tesla.interface import CarInterface as TeslaInterface
from opendbc.car.tesla.speed_sync import prepare, selected
from opendbc.car.tesla.values import CAR as TESLA, TeslaFlags, TeslaSafetyFlags
from openpilot.common.constants import CV
from openpilot.selfdrive.car import card
from openpilot.selfdrive.car.cruise import VCruiseHelper, slc_target_kph
from openpilot.starpilot.speed_limits import acceptance as acc, composition as comp, physical_actions, planner_host
from openpilot.starpilot.speed_limits import tesla_set_speed_card
from openpilot.starpilot.speed_limits.planner_host import SLC_CRUISE_EVENTS, SavedSlcLifecycle
from openpilot.starpilot.speed_limits.runtime import Action, CruiseEvent, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import PedalOverride, parse as saved_settings
from openpilot.starpilot.speed_limits.selection import Source
from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
from openpilot.starpilot.speed_limits.tests import test_engagement as engagement
from openpilot.starpilot.speed_limits.tests import test_runtime_replay as replay_fixture
from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM

START = 2_000_000_000
AUTOMATIC = {'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}
CONFIRM = {**AUTOMATIC, 'SLCConfirmation': 1}


def parse(values, *, switch=True):
  """SLC settings as the planner host hands them to the runtime; switch is the owner's Comma can set speed on Tesla
  dashboard, which SavedSlcLifecycle.refresh turns into set_speed_rule."""
  return replace(saved_settings(values), set_speed_rule=switch)


def rule_cp(*, admitted=True):
  """A Model 3 with StarPilot longitudinal, its own cruise and the CAN add-on cable; admitted is whether it started
  with the scroll frame admitted (it does unless Safe Mode or a release build kept the bit off)."""
  cp = TeslaInterface.get_non_essential_params(TESLA.TESLA_MODEL_3)
  cp.dashcamOnly = False
  cp.openpilotLongitudinalControl = True
  cp.flags |= int(TeslaFlags.HAS_VEHICLE_BUS)
  cp.safetyConfigs[0].safetyParam |= int(TeslaSafetyFlags.LONG_CONTROL)
  prepare(cp, admitted)
  assert cp.pcmCruise and selected(cp) == admitted
  return cp


def owned_helper(cp, *, switch=True):
  """Card's cruise helper as tesla_set_speed_card.refresh leaves it while the owner's switch is on."""
  helper = VCruiseHelper(cp)
  helper.tesla_set_speed = bool(switch and selected(cp))
  return helper


class SavedSwitch:
  """Saved settings holding only the owner's switch, read the way card reads it."""
  def __init__(self, on=True):
    self.directory = tempfile.TemporaryDirectory()
    if on is not None:
      Path(self.directory.name, 'TeslaSpeedSync').write_bytes(b'1' if on else b'0')

  def get_param_path(self, key=''):
    return str(Path(self.directory.name) / key)


# The three ways a car is without the rule: the switch off, a Tesla that started without the frame admitted, both.
WITHOUT_RULE = [(True, False), (False, True), (False, False)]


def scene(cp):
  fixture = replay_fixture.RuntimeReplayTests()
  fixture.setUp()
  fixture.sm['carState'].vCruise = fixture.sm['carState'].vCruiseCluster = 60.
  return fixture.sm, cp


@pytest.fixture
def drive():
  return scene(rule_cp())


def tick(runtime, sm, cp, seconds, **kwargs):
  now = START + round(seconds * 1e9)
  sm.advance(now)
  sm['slcDashboardObservation'].validUntilMonoTime = now + 1_000_000_000
  return runtime.step(sm, cp, now_ns=now, **kwargs)


def command_receipt(runtime, command, seconds, previous, selected_kph, *, applied=True, event_id=1):
  stamp = START + round(seconds * 1e9)
  return CruiseEvent(event_id, stamp, previous / 3.6, selected_kph / 3.6, '', 'card-producer', stamp,
                     'commandApplied' if applied else 'commandRejected', runtime.session_id,
                     command.decisionId, command.presentationId, command.commandId)


def set_change(event_id, seconds, previous, selected_kph):
  stamp = START + round(seconds * 1e9)
  return CruiseEvent(event_id, stamp, previous / 3.6, selected_kph / 3.6, 'accel' if selected_kph > previous else 'decel',
                     'card-producer', stamp)


def dashboard_observation(sm):
  evidence = sm['slcDashboardObservation']
  return SimpleNamespace(status=Status.VALID, speed_mps=evidence.speedMps, episode=evidence.episode,
                         observed_ns=evidence.observedMonoTime, valid_until_ns=evidence.validUntilMonoTime)


def sign(output, set_kph):
  """What the onroad speed widget makes of this frame while cruise is engaged."""
  from openpilot.starpilot.ui.onroad_state import OnroadState, speed_limit_from_message
  from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed
  return resolve_unified_speed(OnroadState(True, False, 20., set_kph, speed_limit_from_message(output.message.slcState),
                                           metric=True, longitudinal_active=True))


def adopt_automatically(runtime, sm, cp):
  """The fixture's 25 m/s limit is adopted after the rule's wait, written to SET (90 km/h) by card and acknowledged."""
  assert runtime.is_metric
  tick(runtime, sm, cp, 0)
  adopted = tick(runtime, sm, cp, 3)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 90.
  tick(runtime, sm, cp, 3.05, cruise_event=command_receipt(runtime, adopted.command.slcCruiseCommand, 3.05, 60., 90.))
  return adopted


# --- the gate ---

@pytest.mark.parametrize('admitted,switch', [(True, True), *WITHOUT_RULE])
def test_only_the_selected_tesla_swaps_the_planner_cap_for_a_written_set_speed(admitted, switch):
  rule = admitted and switch
  sm, cp = scene(rule_cp(admitted=admitted))
  runtime = Runtime(parse(AUTOMATIC, switch=switch))
  first = tick(runtime, sm, cp, 0)
  assert runtime.set_speed_rule == rule
  if not rule:
    # Switch off: the limit is adopted at once and caps the planner beneath SET; SET is never written.
    assert first.message.slcState.hasAccepted and not first.message.slcState.hasPending
    assert first.result.ceiling is not None and first.result.ceiling.speed_mps == pytest.approx(25.)
    assert first.message.slcState.hasCeiling and first.message.slcState.effectiveCap == pytest.approx(25.)
    assert first.result.override is None and first.result.state.override is None
  else:
    assert first.message.slcState.automaticAdoptionPending and not first.message.slcState.hasAccepted
    assert first.result.ceiling is None and first.result.state.override is not None
  later = tick(runtime, sm, cp, 3)
  assert (later.command is not None) == rule
  assert later.message.slcState.hasCeiling == (not rule)
  assert later.message.slcState.commandId == (1 if rule else 0)
  assert later.message.slcState.commandStatus == ('issued' if rule else '')
  assert later.message.slcState.commandActionSequenceId == 0


@pytest.mark.parametrize('admitted,switch', WITHOUT_RULE)
def test_other_cars_and_the_switched_off_tesla_publish_the_same_state(admitted, switch):
  """The Tesla without the rule runs the very path every other car runs: no rule field reaches the wire or the state."""
  def run(cp, settings):
    sm, cp = scene(cp)
    runtime = Runtime(settings, session_id='same-drive')
    outputs = [tick(runtime, sm, cp, 0)]
    sm['carState'].vCruise = sm['carState'].vCruiseCluster = 100.
    outputs.append(tick(runtime, sm, cp, .05, cruise_event=set_change(1, .05, 60., 100.)))
    sm['slcDashboardObservation'].speedMps = 20.
    outputs.append(tick(runtime, sm, cp, .1))
    outputs.append(tick(runtime, sm, cp, .15, request=Action(runtime.session_id, 1, 0, outputs[-1].message.slcState.presentationId, 'adopt')))
    sm['slcDashboardObservation'].status = 'unknown'
    outputs.append(tick(runtime, sm, cp, .2))
    assert all(output.command is None and output.result.override is None for output in outputs)
    assert runtime.state.override is None and runtime.state.acceptance.received_source is None
    assert not runtime.set_speed_rule and runtime.last_command_id == 0
    return [(output.message.to_bytes(), output.result.ceiling, output.result.status) for output in outputs]

  honda = replay_fixture.RuntimeReplayTests()
  honda.setUp()
  honda.cp.pcmCruise = True
  upstream = run(honda.cp, saved_settings(AUTOMATIC))
  assert run(rule_cp(admitted=admitted), parse(AUTOMATIC, switch=switch)) == upstream
  assert run(honda.cp, parse(AUTOMATIC)) == upstream, 'the switch means nothing on another car'


def test_composition_without_the_rule_ignores_the_rule_fields_and_keeps_the_cap(drive):
  sm, cp = drive
  frames = []
  runtime = Runtime(parse(AUTOMATIC))
  # Capture the frame the runtime builds, then replay it through the reducer with and without the rule.
  original = comp.step
  with patch.object(comp, 'step', side_effect=lambda state, frame: frames.append((state, frame)) or original(state, frame)):
    tick(runtime, sm, cp, 0)
  state, frame = frames[0]
  assert frame.set_speed_rule and frame.acceptance_policy.automatic_adoption_ns == acc.AUTOMATIC_ADOPTION_NS
  plain = replace(frame, set_speed_rule=False, set_speed_ready=True, acceptance_policy=saved_settings(AUTOMATIC).acceptance)
  capped = comp.step(state, plain)
  assert capped.ceiling is not None and capped.ceiling.speed_mps == pytest.approx(25.)
  assert capped.override is None and capped.state.override is None and capped.status == 'accepted'
  ruled = comp.step(state, replace(plain, set_speed_rule=True))
  assert ruled.ceiling is None and ruled.state.override is not None and ruled.status == 'accepted'
  assert ruled.coordinate.cluster_target_mps == capped.coordinate.cluster_target_mps


# --- automatic adoption and the waits ---

@pytest.mark.parametrize('limit', [12., 25.])
def test_automatic_adoption_writes_set_speed_up_or_down_after_three_seconds(drive, limit):
  sm, cp = drive
  sm['slcDashboardObservation'].speedMps = limit
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  first = tick(runtime, sm, cp, 0)
  assert first.message.slcState.automaticAdoptionPending
  assert first.message.slcState.pendingSinceMonoTime == START
  assert not first.message.slcState.hasAccepted
  assert first.command is None and first.result.ceiling is None
  assert tick(runtime, sm, cp, 2.999).command is None
  adopted = tick(runtime, sm, cp, 3)
  assert adopted.command is not None
  assert adopted.command.slcCruiseCommand.targetMps == pytest.approx(limit)
  assert adopted.result.ceiling is None and not adopted.message.slcState.hasCeiling
  assert not adopted.message.slcState.hasPending
  state = adopted.message.slcState
  observation = dashboard_observation(sm)
  current = physical_actions.state_current(ReplaySM({'slcState': state}, START + 3_000_000_000), observation,
                                           'card-producer', START + 3_000_000_000)
  assert physical_actions.command_applicable(adopted.command.slcCruiseCommand, current, observation, 'card-producer',
    START + 3_000_000_000, 60 / 3.6, long_active=True, button_event=False)
  helper = owned_helper(cp)
  helper.v_cruise_kph = 60.
  helper.apply_slc_target(adopted.command.slcCruiseCommand.targetMps, True)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = helper.v_cruise_kph
  assert helper.v_cruise_kph == round(limit * 3.6)
  settled = tick(runtime, sm, cp, 3.05)
  assert settled.command is None and settled.message.slcState.isLimitingMaxSet


def test_a_limit_from_another_source_must_hold_two_seconds_before_the_adoption_wait_starts():
  dashboard = acc.Candidate('dashboard', acc.ObservationIdentity(acc.IdentityKind.SESSION_VALUE, session_id='drive'), 20.)
  mapped = acc.Candidate('map', acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value='road'), 15.)
  authority = acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.SYSTEM, False, True, False, False)
  policy = acc.Policy(False, False, automatic_adoption_ns=acc.AUTOMATIC_ADOPTION_NS, source_switch_ns=acc.SOURCE_SWITCH_NS)

  def step(state, candidate, now, **kwargs):
    return acc.step(state, acc.Observation(acc.ObservationKind.VALID, candidate), authority, policy, now_ns=now, **kwargs)

  decision = step(acc.new_session('drive'), dashboard, 0)
  decision = step(decision.state, dashboard, acc.AUTOMATIC_ADOPTION_NS)
  assert decision.basis == 'accepted' and decision.state.received_source == 'dashboard'
  now = acc.AUTOMATIC_ADOPTION_NS + 1
  switching = step(decision.state, mapped, now)
  assert switching.basis == 'source_switch_pending' and switching.state.pending is None
  assert switching.control_target_mps == 20. and switching.state.accepted.candidate == dashboard
  # The old source returning restarts the settle; the same source's own new limit never waits for it.
  assert step(switching.state, dashboard, now + 1).basis == 'accepted'
  assert step(decision.state, replace(dashboard, speed_mps=15.), now).basis == 'automatic_pending'
  held = step(switching.state, mapped, now + acc.SOURCE_SWITCH_NS - 1)
  assert held.basis == 'source_switch_pending'
  settled = step(held.state, mapped, now + acc.SOURCE_SWITCH_NS)
  assert settled.basis == 'automatic_pending' and settled.state.received_source == 'map'
  waiting = step(settled.state, mapped, now + acc.SOURCE_SWITCH_NS + acc.AUTOMATIC_ADOPTION_NS - 1)
  assert waiting.basis == 'automatic_pending'
  adopted = step(waiting.state, mapped, now + acc.SOURCE_SWITCH_NS + acc.AUTOMATIC_ADOPTION_NS)
  assert adopted.basis == 'accepted' and adopted.state.accepted.candidate == mapped
  # An explicit adopt does not wait for the settle.
  shown = switching.state.presentation
  explicit = step(switching.state, mapped, now + 1, adopt=acc.AdoptRequest('drive', 1, shown.presentation_id, mapped))
  assert explicit.basis == 'driver_adopt' and explicit.state.accepted.candidate == mapped


def test_default_policy_adopts_at_once_and_never_tracks_a_source():
  candidate = acc.Candidate('dashboard', acc.ObservationIdentity(acc.IdentityKind.SESSION_VALUE, session_id='drive'), 20.)
  other = acc.Candidate('map', acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value='road'), 15.)
  authority = acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.SYSTEM, False, True, False, False)
  policy = acc.Policy(False, False)
  assert (policy.automatic_adoption_ns, policy.source_switch_ns) == (0, 0)
  first = acc.step(acc.new_session('drive'), acc.Observation(acc.ObservationKind.VALID, candidate), authority, policy, now_ns=0)
  second = acc.step(first.state, acc.Observation(acc.ObservationKind.VALID, other), authority, policy, now_ns=1)
  assert first.basis == second.basis == 'accepted' and second.state.accepted.candidate == other
  assert second.state.received_source is None and second.state.switch_candidate is None
  for invalid in (replace(policy, automatic_adoption_ns=-1), replace(policy, source_switch_ns=True),
                  replace(policy, source_switch_ns=acc.CONFIRMATION_NS + 1)):
    assert acc.step(first.state, acc.Observation(acc.ObservationKind.VALID, other), authority, invalid, now_ns=1).errors


def test_automatic_countdown_never_consumes_plus_or_minus_and_late_accept_is_declined():
  candidate = acc.Candidate('dashboard', acc.ObservationIdentity(acc.IdentityKind.SESSION_VALUE, session_id='drive'), 20.)
  observation = acc.Observation(acc.ObservationKind.VALID, candidate)
  authority = acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.SYSTEM, False, True, False, False)
  for automatic in (False, True):
    policy = acc.Policy(not automatic, not automatic, automatic_adoption_ns=acc.AUTOMATIC_ADOPTION_NS)
    pending = acc.step(acc.new_session('drive'), observation, authority, policy, now_ns=0)
    action = acc.DriverAction('drive', 1, pending.state.pending.decision_id, acc.ActionKind.ACCEPT)
    output = acc.step(pending.state, observation, authority, policy, now_ns=1 if automatic else acc.CONFIRMATION_NS + 1, action=action)
    assert not output.action_receipt.consumed
    assert output.state.accepted is None


@pytest.mark.parametrize('confirmation', [False, True])
def test_new_limit_can_be_adopted_while_accelerator_temporarily_suspends_longitudinal_output(drive, confirmation):
  sm, cp = drive
  sm['carState'].gasPressed = True
  sm['carControl'].longActive = False
  sm['controlsState'].longControlState = 'off'
  runtime = Runtime(parse(CONFIRM if confirmation else AUTOMATIC))
  first = tick(runtime, sm, cp, 0).message.slcState
  action = Action(runtime.session_id, 1, first.decisionId, first.presentationId, 'accept') if confirmation else None
  output = tick(runtime, sm, cp, 1 if confirmation else 3, request=action)
  assert output.command is not None
  assert output.command.slcCruiseCommand.targetMps == 25.
  assert not output.message.slcState.driverOverrideActive
  assert not output.result.acceptance.authority.longitudinal_active
  assert output.result.acceptance.authority.accelerator_override
  assert output.result.ceiling is None


# --- confirmation, accept and explicit adopt ---

@pytest.mark.parametrize('response', ['accept', 'reject', 'timeout'])
def test_confirmation_outcomes_preserve_set_speed_until_acceptance(drive, response):
  sm, cp = drive
  runtime = Runtime(parse(CONFIRM))
  pending = tick(runtime, sm, cp, 0).message.slcState
  assert pending.hasPending and not pending.automaticAdoptionPending
  action = Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, response) if response != 'timeout' else None
  output = tick(runtime, sm, cp, 30 if action is None else 1, request=action)
  assert not output.message.slcState.hasPending
  assert (output.command is not None) == (response == 'accept')
  assert sm['carState'].vCruise == 60.
  if response == 'accept':
    sm['carState'].vCruise = sm['carState'].vCruiseCluster = 90.
    tick(runtime, sm, cp, 1.05, cruise_event=command_receipt(runtime, output.command.slcCruiseCommand, 1.05, 60., 90.))
  assert tick(runtime, sm, cp, 31).command is None
  assert not tick(runtime, sm, cp, 32).message.slcState.hasPending


@pytest.mark.parametrize('selected_kph', [70., 110.])
def test_explicit_adopt_puts_a_changed_set_speed_back_to_the_limit_plus_offset(drive, selected_kph):
  sm, cp = drive
  settings = replace(parse(AUTOMATIC), offsets=OffsetSchedule((OffsetBand(0., None, 2 / 3.6),)))
  runtime = Runtime(settings, is_metric=True)
  tick(runtime, sm, cp, 0)
  adopted = tick(runtime, sm, cp, 3)
  assert adopted.command.slcCruiseCommand.targetMps == pytest.approx(92 / 3.6)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 92.
  tick(runtime, sm, cp, 3.05, cruise_event=command_receipt(runtime, adopted.command.slcCruiseCommand, 3.05, 60., 92.))
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected_kph
  held = tick(runtime, sm, cp, 3.1, cruise_event=set_change(2, 3.1, 92., selected_kph))
  assert held.message.slcState.driverOverrideActive and held.command is None
  assert not held.message.slcState.isLimitingMaxSet and not held.message.slcState.hasCeiling
  assert held.message.slcState.effectiveClusterTarget == pytest.approx(selected_kph / 3.6)  # cruise targets the held SET
  shown = sign(held, selected_kph)
  assert (shown.status, shown.limit_in_use, shown.posted_text) == ('Set-speed override', False, '90')
  assert shown.override_text == shown.max_text == str(round(selected_kph))  # a plain OVERRIDE under SET, above or below
  assert tick(runtime, sm, cp, 10).command is None  # the driver's SET holds while the limit stays
  state = tick(runtime, sm, cp, 10.05).message.slcState
  resumed = tick(runtime, sm, cp, 10.1, request=Action(runtime.session_id, 1, 0, state.presentationId, 'adopt'))
  assert resumed.message.slcState.actionStatus == 'adopt' and not resumed.message.slcState.driverOverrideActive
  command = resumed.command.slcCruiseCommand
  assert command.targetMps == pytest.approx(92 / 3.6) and command.expectedSelectedMps == pytest.approx(selected_kph / 3.6)
  assert command.actionId > 0 and command.commandId == 2
  current = physical_actions.state_current(ReplaySM({'slcState': resumed.message.slcState}, START + 10_100_000_000),
                                           dashboard_observation(sm), 'card-producer', START + 10_100_000_000)
  assert physical_actions.command_applicable(command, current, dashboard_observation(sm), 'card-producer',
    START + 10_100_000_000, selected_kph / 3.6, long_active=True, button_event=False)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 92.
  done = tick(runtime, sm, cp, 10.15, cruise_event=command_receipt(runtime, command, 10.15, selected_kph, 92., event_id=3))
  assert done.message.slcState.commandStatus == 'applied' and done.message.slcState.isLimitingMaxSet
  shown = sign(done, 92.)
  assert (shown.status, shown.limit_in_use, shown.override_text, shown.offset_text) == ('Using speed limit', True, None, '+2')
  assert done.command is None and tick(runtime, sm, cp, 12).command is None


@pytest.mark.parametrize('metric,limit,offset', [(False, 45., 0.), (False, 45., 5.), (False, 45., -.4),
                                              (True, 72., 0.), (True, 72., 5.), (True, 72., -.4)])
def test_matching_set_needs_neither_confirmation_nor_command(drive, metric, limit, offset):
  sm, cp = drive
  conversion = CV.KPH_TO_MS if metric else CV.MPH_TO_MS
  settings = replace(parse(CONFIRM), offsets=OffsetSchedule((OffsetBand(0., None, offset * conversion),)))
  runtime = Runtime(settings, is_metric=metric)
  sm['slcDashboardObservation'].speedMps = limit * conversion
  set_kph = slc_target_kph((limit + offset) * conversion, metric)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = set_kph
  set_kph = float(sm['carState'].vCruise)
  for seconds in (0, .05, 11):
    output = tick(runtime, sm, cp, seconds)
    assert not output.result.errors and not output.message.slcState.hasPending
    assert output.message.slcState.hasAccepted and output.command is None
    assert output.message.slcState.commandStatus == 'applied'
    assert sm['carState'].vCruise == set_kph


def test_half_unit_offset_uses_the_rounding_card_gets_after_float32_transport(drive):
  sm, cp = drive
  settings = replace(parse(CONFIRM), offsets=OffsetSchedule((OffsetBand(0., None, .5 / 3.6),)))
  runtime = Runtime(settings, is_metric=True)
  sm['slcDashboardObservation'].speedMps = 72 / 3.6
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 72.
  pending = tick(runtime, sm, cp, 0).message.slcState
  assert pending.hasPending and not pending.hasAccepted
  accepted = tick(runtime, sm, cp, .05,
    request=Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, 'accept'))
  assert accepted.command is not None
  helper = owned_helper(cp)
  helper.apply_slc_target(accepted.command.slcCruiseCommand.targetMps, True)
  assert helper.v_cruise_kph == 73.


def test_commute_changes_only_set_speed_and_keeps_confirmation_policy(drive):
  sm, cp = drive
  runtime = Runtime(parse(CONFIRM))
  helper = owned_helper(cp)
  set_kph = slc_target_kph(35 * CV.MPH_TO_MS, False)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = set_kph
  for index, mph in enumerate((35, 45, 65, 55, 65, 35, 30)):
    source = sm['slcDashboardObservation']
    source.speedMps = mph * CV.MPH_TO_MS
    source.episode = index + 1
    source.observedMonoTime = START + index * 1_000_000_000
    output = tick(runtime, sm, cp, index)
    if index == 0:
      assert not output.message.slcState.hasPending and output.command is None
      continue
    pending = output.message.slcState
    assert pending.hasPending and output.command is None
    assert sm['carState'].vCruise == pytest.approx(set_kph)
    output = tick(runtime, sm, cp, index + .01,
      request=Action(runtime.session_id, index, pending.decisionId, pending.presentationId, 'accept'))
    command = output.command.slcCruiseCommand
    helper.apply_slc_target(command.targetMps, False)
    previous, set_kph = set_kph, helper.v_cruise_kph
    assert round(set_kph * CV.KPH_TO_MPH) == mph
    sm['carState'].vCruise = sm['carState'].vCruiseCluster = set_kph
    applied = tick(runtime, sm, cp, index + .02,
      cruise_event=command_receipt(runtime, command, index + .02, previous, set_kph, event_id=index))
    assert not applied.message.slcState.hasPending and applied.command is None


@pytest.mark.parametrize('source_name', ['Maps', 'Vision'])
def test_secondary_source_matching_set_skips_confirmation_and_command(drive, source_name):
  from openpilot.cereal import messaging
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': source_name, 'SLCConfirmation': 1}),
                    vision_enabled=True, vision_control_qualified=True)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(65 * CV.MPH_TO_MS, False)
  source = Source.MAP if source_name == 'Maps' else Source.VISION
  observations = dict.fromkeys(Source, acc.Observation(acc.ObservationKind.UNKNOWN))
  observations[source] = acc.Observation(acc.ObservationKind.VALID, acc.Candidate(source.value,
    acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value='current-road'), 65 * CV.MPH_TO_MS))
  sm.data['slcVisionObservation'] = messaging.new_message('slcVisionObservation').slcVisionObservation
  with patch.object(runtime, '_observations', return_value=observations):
    aligned = tick(runtime, sm, cp, 0)
  assert aligned.message.slcState.acceptedSource == source.value
  assert not aligned.message.slcState.hasPending and aligned.command is None


@pytest.mark.parametrize('action_kind', ['accept', 'reject'])
@pytest.mark.parametrize('seconds', [.05, 30.])
def test_presented_driver_choice_wins_when_set_reaches_the_proposal(drive, action_kind, seconds):
  sm, cp = drive
  runtime = Runtime(parse(CONFIRM))
  sm['slcDashboardObservation'].speedMps = 55 * CV.MPH_TO_MS
  pending = tick(runtime, sm, cp, 0).message.slcState
  set_kph = slc_target_kph(55 * CV.MPH_TO_MS, False)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = set_kph
  output = tick(runtime, sm, cp, seconds,
    request=Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, action_kind))
  assert output.result.acceptance.action_receipt.consumed
  assert output.result.acceptance.basis == ('driver_accept' if action_kind == 'accept' else 'driver_reject')
  assert output.command is None and sm['carState'].vCruise == set_kph
  held = tick(runtime, sm, cp, seconds + .05)
  assert not held.message.slcState.hasPending and held.command is None
  if action_kind == 'reject':
    assert held.result.acceptance.basis == 'rejected' and not held.message.slcState.hasAccepted


@pytest.mark.parametrize('seconds', [30., 30.001, 31.])
def test_set_alignment_cannot_accept_an_expired_confirmation(drive, seconds):
  sm, cp = drive
  runtime = Runtime(parse(CONFIRM))
  sm['slcDashboardObservation'].speedMps = 55 * CV.MPH_TO_MS
  pending = tick(runtime, sm, cp, 0)
  assert pending.message.slcState.hasPending
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(55 * CV.MPH_TO_MS, False)
  expired = tick(runtime, sm, cp, seconds)
  assert expired.result.acceptance.basis == 'timeout_reject'
  assert not expired.message.slcState.hasAccepted and not expired.message.slcState.hasPending
  assert expired.command is None


@pytest.mark.parametrize('unavailable', ['off', 'lateral', 'stale_set', 'stale_ego', 'unset', 'force_decel'])
def test_set_alignment_requires_current_system_speed_authority(drive, unavailable):
  sm, cp = drive
  runtime = Runtime(parse(CONFIRM))
  sm['slcDashboardObservation'].speedMps = 65 * CV.MPH_TO_MS
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(65 * CV.MPH_TO_MS, False)
  sm.advance(START)
  if unavailable in ('off', 'lateral'):
    sm['carControl'].longActive = False
    sm['controlsState'].longControlState = 'off'
    sm['carControl'].enabled = sm['selfdriveState'].enabled = False
    sm['carControl'].latActive = unavailable == 'lateral'
  elif unavailable == 'stale_set':
    sm.valid['carState'] = False
  elif unavailable == 'stale_ego':
    sm['carState'].vEgoCluster = float('nan')
  elif unavailable == 'unset':
    sm['carState'].vCruise = sm['carState'].vCruiseCluster = 255.
  else:
    sm['controlsState'].forceDecel = True
  output = runtime.step(sm, cp, now_ns=START)
  assert not output.message.slcState.hasAccepted and output.command is None


def test_reaching_actual_speed_does_not_accept_a_different_set_target(drive):
  sm, cp = drive
  runtime = Runtime(parse(CONFIRM))
  sm['slcDashboardObservation'].speedMps = 45 * CV.MPH_TO_MS
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 45 * CV.MPH_TO_MS
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(65 * CV.MPH_TO_MS, False)
  output = tick(runtime, sm, cp, 0)
  assert output.message.slcState.hasPending and not output.message.slcState.hasAccepted and output.command is None


# --- the command and its receipt ---

@pytest.mark.parametrize('resolution', ['applied', 'rejected', 'expired'])
def test_alignment_waits_for_the_previous_command_and_newer_set_evidence(drive, resolution):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Maps', 'SLCConfirmation': 1}))
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(65 * CV.MPH_TO_MS, False)
  previous = sm['carState'].vCruise
  observations = dict.fromkeys(Source, acc.Observation(acc.ObservationKind.UNKNOWN))

  def limit(mph):
    observations[Source.MAP] = acc.Observation(acc.ObservationKind.VALID, acc.Candidate('map',
      acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value=f'road-{mph}'), mph * CV.MPH_TO_MS))

  with patch.object(runtime, '_observations', return_value=observations):
    limit(55)
    pending = tick(runtime, sm, cp, 0).message.slcState
    issued = tick(runtime, sm, cp, .05,
      request=Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, 'accept'))
    command = issued.command.slcCruiseCommand
    limit(65)
    waiting = tick(runtime, sm, cp, .06)
    assert waiting.command is None and waiting.message.slcState.commandStatus == 'issued'
    assert runtime.commanded_acceptance.candidate.speed_mps == pytest.approx(55 * CV.MPH_TO_MS)
    if resolution != 'expired':
      set_kph = slc_target_kph(command.targetMps, False) if resolution == 'applied' else previous
      sm['carState'].vCruise = sm['carState'].vCruiseCluster = set_kph
      received = tick(runtime, sm, cp, .07, cruise_event=command_receipt(
        runtime, command, .07, previous, set_kph, applied=resolution == 'applied'))
      assert received.command is None
      settled = tick(runtime, sm, cp, .08)
    else:
      # An expired command could have applied after this older (still fresh) SET sample.
      now = START + 151_000_000
      sm.advance(now)
      sm.logMonoTime['carState'] = command.expiresMonoTime
      sm.logMonoTime['slcDashboardObservation'] = command.expiresMonoTime
      sm['slcDashboardObservation'].carStateLogMonoTime = command.expiresMonoTime
      stale_set = runtime.step(sm, cp, now_ns=now)
      assert stale_set.command is None and stale_set.message.slcState.commandStatus == 'expired'
      settled = tick(runtime, sm, cp, .16)
    if resolution == 'applied':
      assert settled.message.slcState.hasPending and settled.message.slcState.commandStatus == 'applied'
      assert settled.message.slcState.acceptedSpeedLimit == pytest.approx(55 * CV.MPH_TO_MS)
    else:
      assert not settled.message.slcState.hasPending and settled.command is None
      assert settled.message.slcState.acceptedSpeedLimit == pytest.approx(65 * CV.MPH_TO_MS)


def test_session_reset_does_not_forget_an_in_flight_speed_change(drive):
  sm, cp = drive
  runtime = Runtime(parse(CONFIRM))
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(65 * CV.MPH_TO_MS, False)
  sm['slcDashboardObservation'].speedMps = 55 * CV.MPH_TO_MS
  pending = tick(runtime, sm, cp, 0).message.slcState
  issued = tick(runtime, sm, cp, .05,
    request=Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, 'accept'))
  assert issued.command is not None
  runtime.reset()
  assert runtime.last_command_id == 0 and runtime.command_status == '' and runtime.state.override is None
  sm['slcDashboardObservation'].speedMps = 65 * CV.MPH_TO_MS
  # SET reads 65 mph, but the old command may still land: the matching SET is not trusted yet.
  waiting = tick(runtime, sm, cp, .06)
  assert waiting.message.slcState.hasPending and waiting.command is None
  assert waiting.message.slcState.acceptedSpeedLimit == pytest.approx(55 * CV.MPH_TO_MS)  # the carried last limit
  settled = tick(runtime, sm, cp, .16)
  assert settled.message.slcState.acceptedSpeedLimit == pytest.approx(65 * CV.MPH_TO_MS)
  assert not settled.message.slcState.hasPending and settled.command is None


@pytest.mark.parametrize('failure', ['source_refresh', 'expiry'])
def test_failed_adoption_retries_with_current_evidence_and_stops_after_application(drive, failure):
  sm, cp = drive
  runtime = Runtime(parse(CONFIRM))
  pending = tick(runtime, sm, cp, 0).message.slcState
  first = tick(runtime, sm, cp, 1, request=Action(runtime.session_id, 17, pending.decisionId, pending.presentationId, 'accept'))
  command = first.command.slcCruiseCommand
  sm['slcDashboardObservation'].observedMonoTime = START + 1_010_000_000
  if failure == 'source_refresh':
    retry = tick(runtime, sm, cp, 1.05, cruise_event=command_receipt(runtime, command, 1.05, 60., 60., applied=False))
  else:
    retry = tick(runtime, sm, cp, 1.15)
  assert retry.command is not None
  current = retry.message.slcState
  retried = retry.command.slcCruiseCommand
  assert retried.commandId > command.commandId
  assert physical_actions.command_applicable(retried, current, dashboard_observation(sm), 'card-producer',
    int(retry.message.logMonoTime), 60 / 3.6, long_active=True, button_event=False)
  applied_at = 1.2 if failure == 'expiry' else 1.1
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 90.
  applied = tick(runtime, sm, cp, applied_at,
    cruise_event=command_receipt(runtime, retried, applied_at, 60., 90., event_id=2))
  assert applied.message.slcState.commandStatus == 'applied' and applied.command is None
  assert tick(runtime, sm, cp, 2).command is None


@pytest.mark.parametrize('change', ['manual', 'unclassified', 'source', 'returned_source', 'disengaged'])
def test_expired_adoption_does_not_retry_after_driver_or_authority_changes(drive, change):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC))
  tick(runtime, sm, cp, 0)
  assert tick(runtime, sm, cp, 3).command is not None
  kwargs = {}
  if change in ('manual', 'unclassified'):
    sm['carState'].vCruise = sm['carState'].vCruiseCluster = 61.
    if change == 'manual':
      kwargs['cruise_event'] = set_change(1, 3.2, 60., 61.)
  elif change == 'source':
    sm['slcDashboardObservation'].speedMps = 20.
  elif change == 'returned_source':
    sm['slcDashboardObservation'].speedMps = 20.
    tick(runtime, sm, cp, 3.05)
    sm['slcDashboardObservation'].speedMps = 25.
  else:
    sm['selfdriveState'].enabled = sm['carControl'].enabled = sm['carControl'].longActive = False
    sm['controlsState'].longControlState = 'off'
  assert tick(runtime, sm, cp, 3.2, **kwargs).command is None


@pytest.mark.parametrize('limit,offset,bounded', [(140., 10., 145.), (10., -5., 8.)])
@pytest.mark.parametrize('metric', [False, True])
def test_adoption_and_display_use_existing_cruise_bounds(drive, limit, offset, bounded, metric):
  sm, cp = drive
  sm['slcDashboardObservation'].speedMps = limit / 3.6
  settings = replace(parse(AUTOMATIC), offsets=OffsetSchedule((OffsetBand(0., None, offset / 3.6),)))
  runtime = Runtime(settings)
  tick(runtime, sm, cp, 0)
  output = tick(runtime, sm, cp, 3)
  assert not output.result.errors and output.command is not None
  command, state = output.command.slcCruiseCommand, output.message.slcState
  assert command.targetMps == pytest.approx(bounded / 3.6)
  assert state.acceptedAdjustedLimit == pytest.approx(bounded / 3.6)
  assert state.offset == pytest.approx((bounded - limit) / 3.6)
  assert physical_actions.command_applicable(command, state, dashboard_observation(sm), 'card-producer', START + 3_000_000_000,
                                             60 / 3.6, long_active=True, button_event=False)
  helper = owned_helper(cp)
  helper.apply_slc_target(command.targetMps, metric)
  assert 8 <= helper.v_cruise_kph <= 145


@pytest.mark.parametrize('source_name', ['map', 'vision'])
def test_map_and_vision_adoption_commands_bind_the_current_source_without_dashboard_limit(drive, source_name):
  from openpilot.cereal import messaging
  sm, cp = drive
  sm['slcDashboardObservation'].status = 'unknown'
  evidence = messaging.new_message('slcVisionObservation').slcVisionObservation
  evidence.vision.producerSessionId = 'vision-producer'
  evidence.vision.episode = 1
  evidence.vision.observedMonoTime = START
  evidence.vision.validUntilMonoTime = START + 10_000_000_000
  sm.data['slcVisionObservation'] = evidence
  source = Source(source_name)
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Maps' if source == Source.MAP else 'Vision'}),
                    vision_enabled=True, vision_control_qualified=True)
  candidate = acc.Candidate(source_name, acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value='qualified-source'), 20.)
  observations = dict.fromkeys(Source, acc.Observation(acc.ObservationKind.ABSENT))
  observations[source] = acc.Observation(acc.ObservationKind.VALID, candidate)
  with patch.object(runtime, '_observations', return_value=observations):
    tick(runtime, sm, cp, 0)
    output = tick(runtime, sm, cp, 3)
  assert output.command is not None, (output.result.errors, output.result.status, output.result.acceptance, runtime.settings.errors)
  command = output.command.slcCruiseCommand
  assert command.source == source_name
  current = physical_actions.state_current(ReplaySM({'slcState': output.message.slcState}, START + 3_000_000_000),
                                           None, 'card-producer', START + 3_000_000_000)
  assert physical_actions.command_applicable(command, current, None, 'card-producer', START + 3_000_000_000,
                                             60 / 3.6, long_active=True, button_event=False)
  command.source = 'dashboard'
  assert not physical_actions.command_applicable(command, current, None, 'card-producer', START + 3_000_000_000,
                                                 60 / 3.6, long_active=True, button_event=False)


def test_card_refuses_a_command_that_no_longer_matches_what_it_sees(drive):
  from openpilot.cereal import messaging
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC))
  tick(runtime, sm, cp, 0)
  output = tick(runtime, sm, cp, 3)
  now = START + 3_000_000_000
  command, state, observation = output.command.slcCruiseCommand, output.message.slcState, dashboard_observation(sm)

  def applicable(*, command=command, state=state, observation=observation, now=now, selected_mps=60 / 3.6, **kwargs):
    kwargs = {'long_active': True, 'button_event': False, **kwargs}
    return physical_actions.command_applicable(command, state, observation, 'card-producer', now, selected_mps, **kwargs)

  assert applicable()
  assert not applicable(state=None) and not applicable(observation=None)
  assert not applicable(long_active=False) and not applicable(button_event=True)
  assert not applicable(selected_mps=61 / 3.6)  # the driver moved SET after the planner looked
  assert not applicable(now=int(command.expiresMonoTime) + 1) and not applicable(now=int(command.issuedMonoTime) - 1)
  command_wire, state_wire = output.command.to_bytes(), output.message.to_bytes()
  for field, value in (('kind', 'adoptAcceptedHigherLimit'), ('sessionId', 'other'), ('commandId', 2), ('presentationId', 99),
                       ('targetMps', 26.), ('sourceEpisode', 9), ('expiresMonoTime', now + 1_000_000_000)):
    forged = messaging.log_from_bytes(command_wire).as_builder().slcCruiseCommand
    setattr(forged, field, value)
    assert not applicable(command=forged), field
  stale = messaging.log_from_bytes(state_wire).as_builder().slcState
  stale.speedLimit = 20.
  assert not applicable(state=stale)


def early_drive():
  """Maps first, 20 m/s accepted with a 2 m/s offset, limits adopted 5 s early, on a car under the rule. card is a
  function that qualifies and applies a tick's command as card would, moving SET."""
  from openpilot.starpilot.speed_limits.tests.test_advance import Drive
  drive = Drive()
  drive.cp = rule_cp()
  drive.runtime.settings = replace(drive.runtime.settings, set_speed_rule=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 79.  # the whole-unit SET card writes for 22 m/s
  assert drive.tick(target=0.).command is None and drive.runtime.command_status == 'applied'

  def card(output):
    command, now = output.command.slcCruiseCommand, drive.now
    state = physical_actions.state_current(ReplaySM({'slcState': output.message.slcState}, now), None, 'card-producer', now)
    applied = physical_actions.command_applicable(command, state, None, 'card-producer', now, drive.sm['carState'].vCruise / 3.6,
                                                  long_active=True, button_event=False)
    if applied:
      drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = slc_target_kph(command.targetMps, True)
    return applied
  return drive, card


def test_an_early_maps_preview_is_written_when_it_is_offered():
  drive, card = early_drive()
  far = drive.tick(target=15., distance=120.)  # 6 s from the boundary at 20 m/s: not offered yet
  assert far.command is None and not far.message.slcState.acceptedIsPreview
  offered = drive.tick(target=15., distance=100.)  # 5 s: offered, and adopted without the rule's 3 s wait
  preview = offered.message.slcState
  assert preview.acceptedSpeedLimit == 15. and preview.speedLimit == 20. and preview.acceptedIsPreview
  assert preview.upcomingBoundaryId and preview.upcomingSpeedLimit == 15. and not preview.hasCeiling
  command = offered.command.slcCruiseCommand
  assert command.targetMps == pytest.approx(17.) and command.source == 'map'
  assert card(offered) and drive.sm['carState'].vCruise == 61.
  approach = [drive.tick(target=15., distance=99. - step) for step in range(60)]
  assert all(output.command is None for output in approach), 'written once'
  arrived = [drive.tick(current=15., target=0., way=2) for _ in range(3)]
  assert all(output.command is None for output in arrived), 'SET is already the new limit when the car reaches it'
  assert arrived[-1].message.slcState.speedLimit == arrived[-1].message.slcState.acceptedSpeedLimit == 15.
  assert not arrived[-1].message.slcState.acceptedIsPreview


def test_a_withdrawn_early_preview_is_written_back_to_the_roads_limit():
  drive, card = early_drive()
  assert card(drive.tick(target=15., distance=100.)) and drive.sm['carState'].vCruise == 61.
  for step in range(20):
    assert drive.tick(target=15., distance=99. - step).command is None
  turned = drive.tick(current=20., target=0., way=3)  # left the road before the boundary
  state = turned.message.slcState
  assert state.acceptedSpeedLimit == state.speedLimit == 20. and not state.acceptedIsPreview
  assert turned.command.slcCruiseCommand.targetMps == pytest.approx(22.)
  assert card(turned) and drive.sm['carState'].vCruise == 79.
  assert all(drive.tick(current=20., target=0., way=3).command is None for _ in range(40))


def test_early_adoption_turned_off_writes_the_limit_only_when_the_car_reaches_it():
  drive, card = early_drive()
  drive.runtime.update_advance(0, 0)
  approach = [drive.tick(target=15., distance=100. - step) for step in range(70)]
  assert all(output.command is None and not output.message.slcState.acceptedIsPreview for output in approach)
  waiting = [drive.tick(current=15., target=0., way=2) for _ in range(59)]  # the rule's 3 s wait
  assert all(output.command is None for output in waiting)
  arrived = next(output for output in (drive.tick(current=15., target=0., way=2) for _ in range(11)) if output.command is not None)
  assert arrived.command.slcCruiseCommand.targetMps == pytest.approx(17.)
  assert card(arrived) and drive.sm['carState'].vCruise == 61.


def test_card_binds_an_early_preview_to_maps_and_its_boundary():
  from openpilot.cereal import messaging
  drive, _ = early_drive()
  offered = drive.tick(target=15., distance=100.)
  now, selected_mps = drive.now, drive.sm['carState'].vCruise / 3.6
  command_wire, state_wire = offered.command.to_bytes(), offered.message.to_bytes()

  def applicable(command=None, state=None):
    command = command if command is not None else messaging.log_from_bytes(command_wire).slcCruiseCommand
    state = state if state is not None else messaging.log_from_bytes(state_wire).slcState
    return physical_actions.command_applicable(command, state, None, 'card-producer', now, selected_mps,
                                               long_active=True, button_event=False)

  assert applicable()
  for field, value in (('acceptedSource', 'dashboard'), ('upcomingBoundaryId', ''), ('upcomingBoundaryId', 'not-a-boundary'),
                       ('upcomingSpeedLimit', 14.), ('acceptedSpeedLimit', 14.), ('offset', 3.)):
    forged = messaging.log_from_bytes(state_wire).as_builder().slcState
    setattr(forged, field, value)
    assert not applicable(state=forged), field
  for field, value in (('source', 'dashboard'), ('sourceEpisode', 1), ('sourceProducerSessionId', 'card-producer'),
                       ('sourceObservedMonoTime', 1), ('sourceValidUntilMonoTime', now + 1), ('targetMps', 22.)):
    forged = messaging.log_from_bytes(command_wire).as_builder().slcCruiseCommand
    setattr(forged, field, value)
    assert not applicable(command=forged), field
  # Without the preview mark, an accepted limit that is not the road's is refused as before.
  plain = messaging.log_from_bytes(state_wire).as_builder().slcState
  plain.acceptedIsPreview = False
  assert not applicable(state=plain)


# --- the driver's own SET change ---

@pytest.mark.parametrize('metric,display', [(True, 72), (False, 55), (False, 60), (False, 65), (False, 70)])
@pytest.mark.parametrize('direction', [-1, 1])
def test_manual_override_compares_against_the_applied_rounded_set_speed(drive, metric, display, direction):
  sm, cp = drive
  sm['slcDashboardObservation'].speedMps = 45 * CV.MPH_TO_MS if metric else display * CV.MPH_TO_MS
  runtime = Runtime(parse(AUTOMATIC), is_metric=metric)
  tick(runtime, sm, cp, 0)
  adopted = tick(runtime, sm, cp, 3)
  helper = owned_helper(cp)
  helper.apply_slc_target(adopted.command.slcCruiseCommand.targetMps, metric)
  baseline = helper.v_cruise_kph
  conversion = 1 if metric else CV.KPH_TO_MPH
  assert round(baseline * conversion) == display
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = baseline
  tick(runtime, sm, cp, 3.05, cruise_event=command_receipt(runtime, adopted.command.slcCruiseCommand, 3.05, 60., baseline))
  for event_id, (step, distance) in enumerate(zip((direction, direction, -direction, -direction), (1, 2, 1, 0), strict=True), 2):
    previous = float(helper.v_cruise_kph)
    helper.adjust_v_cruise(car.CarState.ButtonEvent.Type.accelCruise if step > 0 else
                          car.CarState.ButtonEvent.Type.decelCruise, sm['carState'], metric)
    chosen = float(helper.v_cruise_kph)
    assert round(chosen * conversion) == display + direction * distance
    sm['carState'].vCruise = sm['carState'].vCruiseCluster = chosen
    seconds = 3.05 + event_id * .05
    output = tick(runtime, sm, cp, seconds, cruise_event=set_change(event_id, seconds, previous, chosen))
    assert output.result.override.event_receipt.consumed
    assert output.message.slcState.driverOverrideActive == (distance != 0)
    assert output.message.slcState.overrideBasis == ('persistent' if distance else 'none')
    assert output.message.slcState.isLimitingMaxSet == (distance == 0)
    assert output.command is None
  assert helper.v_cruise_kph == pytest.approx(baseline, abs=1e-6)


@pytest.mark.parametrize('selected_kph', [50., 100.])
def test_a_new_lower_limit_is_adopted_over_a_held_set_speed_in_either_direction(drive, selected_kph):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  adopt_automatically(runtime, sm, cp)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected_kph
  changed = tick(runtime, sm, cp, 3.1, cruise_event=set_change(2, 3.1, 90., selected_kph))
  assert changed.message.slcState.driverOverrideActive
  sm['slcDashboardObservation'].speedMps = 18.
  new = tick(runtime, sm, cp, 3.2)
  assert new.message.slcState.automaticAdoptionPending and new.message.slcState.driverOverrideActive
  adopted = tick(runtime, sm, cp, 6.2)
  assert adopted.command.slcCruiseCommand.targetMps == pytest.approx(18.)
  assert not adopted.message.slcState.driverOverrideActive


def test_a_higher_limit_still_below_the_held_set_speed_leaves_it_alone(drive):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  adopt_automatically(runtime, sm, cp)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 110.
  tick(runtime, sm, cp, 3.1, cruise_event=set_change(2, 3.1, 90., 110.))
  sm['slcDashboardObservation'].speedMps = 28.  # 100.8 km/h, under the driver's 110
  tick(runtime, sm, cp, 3.2)
  higher = tick(runtime, sm, cp, 6.2)
  assert higher.message.slcState.acceptedSpeedLimit == pytest.approx(28.)
  assert higher.command is None and higher.message.slcState.driverOverrideActive
  sm['slcDashboardObservation'].speedMps = 33.  # 118.8 km/h, above it
  tick(runtime, sm, cp, 6.3)
  above = tick(runtime, sm, cp, 9.3)
  assert above.command.slcCruiseCommand.targetMps == pytest.approx(33.)
  assert not above.message.slcState.driverOverrideActive


def test_matching_new_limit_reconciles_an_old_manual_override_without_changing_set(drive):
  sm, cp = drive
  runtime = Runtime(parse(CONFIRM))
  sm['slcDashboardObservation'].speedMps = 65 * CV.MPH_TO_MS
  baseline = slc_target_kph(65 * CV.MPH_TO_MS, False)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = baseline
  first = tick(runtime, sm, cp, 0)
  assert not first.message.slcState.hasPending
  chosen = slc_target_kph(70 * CV.MPH_TO_MS, False)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = chosen
  chosen = float(sm['carState'].vCruise)
  changed = tick(runtime, sm, cp, .05, cruise_event=set_change(1, .05, baseline, chosen))
  assert changed.message.slcState.driverOverrideActive and changed.command is None
  sm['slcDashboardObservation'].speedMps = 70 * CV.MPH_TO_MS
  aligned = tick(runtime, sm, cp, .1)
  assert not aligned.message.slcState.hasPending and not aligned.message.slcState.driverOverrideActive
  assert aligned.command is None and sm['carState'].vCruise == chosen


def test_accelerator_never_makes_an_override_or_changes_set_speed(drive):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  adopt_automatically(runtime, sm, cp)
  sm['carState'].gasPressed = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 30.
  pressed = tick(runtime, sm, cp, 4)
  assert not pressed.message.slcState.driverOverrideActive
  assert sm['carState'].vCruise == 90. and pressed.command is None and pressed.result.ceiling is None
  sm['carState'].gasPressed = False
  released = tick(runtime, sm, cp, 5)
  assert not released.message.slcState.driverOverrideActive and released.result.ceiling is None
  assert released.command is None and released.message.slcState.isLimitingMaxSet


def test_offset_edit_does_not_cancel_manual_override(drive):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  adopt_automatically(runtime, sm, cp)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 100.
  tick(runtime, sm, cp, 3.1, cruise_event=set_change(2, 3.1, 90., 100.))
  runtime.update_offsets(OffsetSchedule((OffsetBand(0., None, 1.),)))
  output = tick(runtime, sm, cp, 3.2)
  assert output.command is None and output.message.slcState.driverOverrideActive
  assert sm['carState'].vCruise == 100.


def test_offset_edit_writes_the_new_target_but_a_unit_change_does_not(drive):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  adopt_automatically(runtime, sm, cp)
  assert tick(runtime, sm, cp, 4).command is None
  runtime.update_offsets(runtime.settings.offsets, is_metric=False)
  assert tick(runtime, sm, cp, 5).command is None  # 90 km/h is not a whole mph: only rounding differs
  runtime.update_offsets(OffsetSchedule((OffsetBand(0., None, 2 / 3.6),)), is_metric=True)
  edited = tick(runtime, sm, cp, 6)
  assert edited.command.slcCruiseCommand.targetMps == pytest.approx(92 / 3.6)


def test_manual_changes_during_source_loss_remain_overrides_after_reconnection(drive):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  adopt_automatically(runtime, sm, cp)
  sm['slcDashboardObservation'].status = 'unknown'
  lost = tick(runtime, sm, cp, 3.1)
  assert lost.result.ceiling is None and lost.command is None
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 100.
  tick(runtime, sm, cp, 3.2, cruise_event=set_change(2, 3.2, 90., 100.))
  sm['slcDashboardObservation'].status = 'valid'
  output = tick(runtime, sm, cp, 3.3)
  assert output.message.slcState.driverOverrideActive and output.command is None
  assert sm['carState'].vCruise == 100.


# --- card ---

def card_owner(sm, cp, state, *, metric=True, switch=True):
  owner = card.Car.__new__(card.Car)
  owner.CP, owner.is_metric = cp, metric
  vars(owner)['params'] = SavedSwitch(switch)
  owner.slc_replay = owner.slc_control_enabled = owner.slc_transport_available = True
  owner.slc_producer_session = 'card-producer'
  owner.slc_receipts = []
  owner.v_cruise_helper = owned_helper(cp)
  owner.v_cruise_helper.v_cruise_kph = owner.v_cruise_helper.v_cruise_cluster_kph = float(sm['carState'].vCruise)
  with patch.object(tesla_set_speed_card.messaging, 'sub_sock', return_value=object()) as subscribe:
    owner.slc_set_speed = tesla_set_speed_card.attach(owner)
  assert subscribe.call_args_list == ([(('slcCruiseCommand',), {'conflate': False})] if selected(cp) else [])
  assert owner.v_cruise_helper.tesla_set_speed == bool(switch and selected(cp)), 'attach reads the switch'
  owner.sm = cast(card.messaging.SubMaster, ReplaySM({'slcState': state}, int(state.frameMonoTime)))
  return owner


def deliver(owner, commands, now, *, observation, buttons=(), long_active=True, after_ns=0):
  queue = deque(commands)
  cs = car.CarState(canValid=True, cruiseState={'available': True}, buttonEvents=list(buttons))
  with patch.object(tesla_set_speed_card.messaging, 'recv_one_or_none', side_effect=lambda sock: queue.popleft() if queue else None):
    tesla_set_speed_card.apply_commands(owner, cs, observation, now, long_active=long_active, after_ns=after_ns)
  return owner.slc_receipts


def receipt_event(owner, receipt, event_id=1):
  """The slcCruiseEvent card publishes for a command receipt, as the planner reads it."""
  from openpilot.cereal import messaging
  from openpilot.starpilot.speed_limits.planner_host import cruise_event_from_message
  kind, detail, previous, written, at = receipt
  event = messaging.new_message('slcCruiseEvent', valid=True, logMonoTime=at)
  record = event.slcCruiseEvent
  record.eventId, record.producerSessionId, record.observedMonoTime = event_id, owner.slc_producer_session, at
  record.kind, record.previousMps, record.selectedMps = kind, previous, written
  tesla_set_speed_card.write_receipt(record, kind, detail)
  return cruise_event_from_message(event)


def test_card_writes_the_command_once_and_its_receipt_completes_the_runtime(drive):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  tick(runtime, sm, cp, 0)
  output = tick(runtime, sm, cp, 3)
  now = START + 3_010_000_000
  owner = card_owner(sm, cp, output.message.slcState)
  owner.sm.logMonoTime['slcState'] = START + 3_000_000_000
  receipts = deliver(owner, [output.command], now, observation=dashboard_observation(sm))
  assert owner.v_cruise_helper.v_cruise_kph == owner.v_cruise_helper.v_cruise_cluster_kph == 90.
  assert [(kind, round(previous * 3.6), round(written * 3.6), at) for kind, _, previous, written, at in receipts] == [
    ('commandApplied', 60, 90, now)]
  # The same command again is ignored, as is an older one.
  assert len(deliver(owner, [output.command], now + 10_000_000, observation=dashboard_observation(sm))) == 1
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = owner.v_cruise_helper.v_cruise_kph
  done = tick(runtime, sm, cp, 3.05, cruise_event=receipt_event(owner, receipts[0]))
  assert done.message.slcState.commandStatus == 'applied' and done.command is None
  assert done.message.slcState.isLimitingMaxSet and not done.message.slcState.driverOverrideActive
  assert runtime.state.override.applied_selected_mps == pytest.approx(25.)


@pytest.mark.parametrize('reason', ['step', 'button', 'inactive', 'moved'])
def test_card_refuses_the_command_when_the_driver_or_authority_changed_and_the_runtime_decides_again(drive, reason):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC))
  tick(runtime, sm, cp, 0)
  output = tick(runtime, sm, cp, 3)
  now = START + 3_010_000_000
  owner = card_owner(sm, cp, output.message.slcState)
  if reason == 'step':
    owner.v_cruise_helper.slc_cruise_change = (60 / 3.6, 60 / 3.6, 'accel', False)
  elif reason == 'moved':
    owner.v_cruise_helper.v_cruise_kph = owner.v_cruise_helper.v_cruise_cluster_kph = 61.
  buttons = [car.CarState.ButtonEvent(type='lkas', pressed=True)] if reason == 'button' else []
  receipts = deliver(owner, [output.command], now, observation=dashboard_observation(sm), buttons=buttons,
                     long_active=reason != 'inactive')
  assert [receipt[0] for receipt in receipts] == ['commandRejected']
  assert owner.v_cruise_helper.v_cruise_kph == (61. if reason == 'moved' else 60.)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = owner.v_cruise_helper.v_cruise_kph
  again = tick(runtime, sm, cp, 3.05, cruise_event=receipt_event(owner, receipts[0]))
  assert again.message.slcState.commandStatus == ('rejected' if reason == 'moved' else 'issued')
  # SET still where the planner saw it: ask again. SET moved by the driver: leave it alone.
  assert (again.command is not None) == (reason != 'moved')


def test_card_waits_for_the_state_published_with_the_command_and_drops_stale_ones(drive):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC))
  early = tick(runtime, sm, cp, 0)
  output = tick(runtime, sm, cp, 3)
  issued = START + 3_000_000_000
  owner = card_owner(sm, cp, early.message.slcState)
  owner.sm.logMonoTime['slcState'] = issued - 50_000_000
  early.message.slcState.frameMonoTime = issued - 50_000_000
  assert not deliver(owner, [output.command], issued + 5_000_000, observation=dashboard_observation(sm))
  assert len(owner.slc_set_speed.commands) == 1 and owner.v_cruise_helper.v_cruise_kph == 60.
  owner.sm.data['slcState'] = output.message.slcState
  owner.sm.logMonoTime['slcState'] = issued
  assert [receipt[0] for receipt in deliver(owner, [], issued + 10_000_000, observation=dashboard_observation(sm))] == ['commandApplied']
  # A command issued before the SLC settings last changed is dropped without an answer.
  fresh = card_owner(sm, cp, output.message.slcState)
  assert not deliver(fresh, [output.command], issued + 10_000_000, observation=dashboard_observation(sm), after_ns=issued)
  assert not fresh.slc_set_speed.commands and fresh.v_cruise_helper.v_cruise_kph == 60.


def test_card_loop_applies_the_command_and_publishes_its_receipt(drive):
  """The real card update and publish steps, with only the car interface and the sockets faked."""
  from openpilot.cereal import messaging
  from openpilot.selfdrive.controls.plannerd import queue_cruise_event
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  tick(runtime, sm, cp, 0)
  output = tick(runtime, sm, cp, 3)
  now = START + 3_010_000_000
  fixture = replay_fixture.RuntimeReplayTests()
  try:
    fake, current_cs = fixture._card_with_slc_state(output.message.slcState, dashboard_observation(sm), now)
    fake.sm.logMonoTime['slcState'] = START + 3_000_000_000
    fake.is_metric, fake.car_params_published, fake.aol_replay, fake.slc_cruise_event_id = True, True, False, 0
    fake.sm.frame, fake.sm.all_checks = 1, lambda services: True
    fake.last_actuators_output, fake.rk, fake.radar_publish = car.CarControl.Actuators(), SimpleNamespace(remaining=0.0), Mock()
    receiver = fake.slc_set_speed = tesla_set_speed_card.Receiver(object())
    fake.v_cruise_helper.tesla_set_speed = True  # the owner's switch, as card's settings thread keeps it
    current_cs[0] = car.CarState(canValid=True, cruiseState={'available': True})
    queue = deque([output.command])
    with (patch.object(card, 'REPLAY', False), patch.object(card.time, 'monotonic_ns', return_value=now),
          patch.object(card.time, 'monotonic', return_value=now / 1e9),
          patch.object(card.messaging, 'drain_sock_raw', return_value=[]),
          patch.object(card.messaging, 'recv_one_or_none',
                       side_effect=lambda sock: queue.popleft() if sock is receiver.sock and queue else None)):
      cs, radar = fake.state_update()
      fake.state_publish(cs, radar)
    assert cs.vCruise == cs.vCruiseCluster == 90.
    events = [call.args[1] for call in fake.pm.send.call_args_list if call.args[0] == 'slcCruiseEvent']
    assert [str(event.slcCruiseEvent.kind) for event in events] == ['commandApplied']
    record = events[0].slcCruiseEvent
    assert (record.sessionId, record.commandId, record.producerSessionId) == (runtime.session_id, 1, 'card-producer')
    assert record.previousMps == pytest.approx(60 / 3.6) and record.selectedMps == pytest.approx(25.)
    slc_events, curve_events = deque(), deque()
    queue_cruise_event(messaging.log_from_bytes(events[0].to_bytes()), slc_events, curve_events)
    assert len(slc_events) == 1 and not curve_events
    sm['carState'].vCruise = sm['carState'].vCruiseCluster = 90.
    done = tick(runtime, sm, cp, 3.05, cruise_event=replace(slc_events[0], car_state_log_ns=START + 3_050_000_000))
    assert done.message.slcState.commandStatus == 'applied' and done.message.slcState.isLimitingMaxSet
  finally:
    fixture.doCleanups()


def test_card_drops_commands_while_the_switch_is_off_and_never_replays_them(drive):
  sm, cp = drive
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  tick(runtime, sm, cp, 0)
  output = tick(runtime, sm, cp, 3)
  now = START + 3_010_000_000
  owner = card_owner(sm, cp, output.message.slcState, switch=False)
  owner.sm.logMonoTime['slcState'] = START + 3_000_000_000
  assert owner.slc_set_speed is not None and not tesla_set_speed_card.rule(owner), 'the receiver is there; the car owns SET'
  assert deliver(owner, [output.command], now, observation=dashboard_observation(sm)) == []
  assert owner.v_cruise_helper.v_cruise_kph == 60. and not owner.slc_set_speed.commands
  # The owner turns the switch on: card follows within a second, and the command it dropped is not written late.
  Path(owner.params.get_param_path('TeslaSpeedSync')).write_bytes(b'1')
  tesla_set_speed_card.refresh(owner)
  assert owner.v_cruise_helper.tesla_set_speed and tesla_set_speed_card.rule(owner)
  assert deliver(owner, [], now + 10_000_000, observation=dashboard_observation(sm)) == []
  assert owner.v_cruise_helper.v_cruise_kph == 60.
  receipts = deliver(owner, [output.command], now + 20_000_000, observation=dashboard_observation(sm))
  assert [kind for kind, *_ in receipts] == ['commandApplied'] and owner.v_cruise_helper.v_cruise_kph == 90.
  # And off again: the car's own set speed is SET from the next frame, and commands are dropped as before.
  Path(owner.params.get_param_path('TeslaSpeedSync')).write_bytes(b'0')
  tesla_set_speed_card.refresh(owner)
  assert not owner.v_cruise_helper.tesla_set_speed and not tesla_set_speed_card.rule(owner)


def test_card_without_the_rule_has_no_receiver_and_reads_nothing():
  sm, cp = scene(rule_cp(admitted=False))
  output = tick(Runtime(parse(AUTOMATIC)), sm, cp, 0)
  owner = card_owner(sm, cp, output.message.slcState)
  assert owner.slc_set_speed is None and not tesla_set_speed_card.rule(owner)
  with patch.object(tesla_set_speed_card.messaging, 'recv_one_or_none', side_effect=AssertionError('no socket to read')):
    tesla_set_speed_card.apply_commands(owner, car.CarState(), None, START, long_active=True)
  assert not owner.slc_receipts and owner.v_cruise_helper.v_cruise_kph == 60.


@pytest.mark.parametrize('admitted,switch', [(True, True), *WITHOUT_RULE])
@pytest.mark.parametrize('resume', [False, True])
def test_engaging_starts_set_at_the_current_limit_only_on_the_rule(admitted, switch, resume):
  runtime, sm, _ = engagement.drive(metric=True, limit=72., offset=5., confirmation=False)
  cp = rule_cp(admitted=admitted)
  state = runtime.step(sm, cp, now_ns=START).message.slcState
  sm.data['slcState'] = state
  owner = engagement.owner(sm, cp, metric=True, resume=resume, match=False)  # Match Speed Limit on Engage is off
  owner.slc_set_speed = tesla_set_speed_card.Receiver(object()) if admitted else None
  owner.v_cruise_helper.tesla_set_speed = switch = admitted and switch
  owner.v_cruise_helper.v_cruise_kph = owner.v_cruise_helper.v_cruise_kph_last = 60.
  engagement.engage(owner)
  assert owner.v_cruise_helper.slc_pcm == switch
  if switch:
    assert owner.v_cruise_helper.v_cruise_kph == owner.v_cruise_helper.v_cruise_cluster_kph == 77.
    assert [receipt[0] for receipt in owner.slc_receipts] == ['engagementAdopt']
  else:
    assert owner.v_cruise_helper.v_cruise_kph == pytest.approx(60.) and not owner.slc_receipts


def test_engaging_without_a_current_limit_keeps_the_cars_own_set_speed():
  _, sm, _ = engagement.drive(metric=True, limit=72., offset=5., confirmation=False)
  cp = rule_cp()
  sm['slcDashboardObservation'].status = 'unknown'
  sm.data['slcState'] = Runtime(parse(AUTOMATIC), is_metric=True).step(sm, cp, now_ns=START).message.slcState
  owner = engagement.owner(sm, cp, metric=True, match=True)
  owner.slc_set_speed = tesla_set_speed_card.Receiver(object())
  owner.v_cruise_helper.tesla_set_speed = True
  engagement.engage(owner)
  assert owner.v_cruise_helper.v_cruise_kph == pytest.approx(60.) and not owner.slc_receipts


def test_helper_keeps_the_written_set_speed_while_the_car_reports_its_own(drive):
  _, cp = drive
  helper = owned_helper(cp)
  cs = car.CarState(cruiseState={'available': True, 'enabled': True, 'speed': 60 / 3.6})
  helper.update_v_cruise(cs, False, True, slc_enabled=True)
  assert helper.slc_pcm and helper.v_cruise_kph == pytest.approx(60.)
  assert helper.initialize_v_cruise(cs, False, resume=True, slc_target_mps=25., is_metric=True)
  assert not helper.initialize_v_cruise(cs, False, resume=False, slc_target_mps=None, is_metric=True)
  cs.cruiseState.speed = 59 / 3.6
  helper.update_v_cruise(cs, True, True, slc_enabled=True)
  assert helper.v_cruise_kph == helper.v_cruise_cluster_kph == 90.


# --- planner host ---

def test_host_publishes_commands_and_takes_receipts_only_for_the_rule(drive, tmp_path):
  from openpilot.common.params import Params
  sm, cp = drive
  assert {'commandApplied', 'commandRejected'} <= set(SLC_CRUISE_EVENTS)
  honda = replay_fixture.RuntimeReplayTests()
  honda.setUp()
  with patch.object(planner_host.messaging, 'pub_sock', return_value=Mock()) as publisher:
    assert SavedSlcLifecycle(Params(str(tmp_path)), honda.cp, {}).command_sock is None
    assert SavedSlcLifecycle(Params(str(tmp_path)), rule_cp(admitted=False), {}).command_sock is None
    assert not publisher.called
    host = SavedSlcLifecycle(Params(str(tmp_path)), cp, {})
  publisher.assert_called_once_with('slcCruiseCommand')
  assert host.command_sock is publisher.return_value
  host.runtime = runtime = Runtime(parse(AUTOMATIC))
  actions, cruise = deque(), deque()

  def host_tick(seconds):
    now = START + round(seconds * 1e9)
    sm.advance(now)
    sm['slcDashboardObservation'].validUntilMonoTime = now + 1_000_000_000
    return host.step(sm, now, actions, cruise, drive_changed=False)

  assert host_tick(0).command is None and not host.command_sock.send.called
  issued = host_tick(3)
  from openpilot.cereal import messaging
  sent = messaging.log_from_bytes(host.command_sock.send.call_args.args[0])
  assert sent.which() == 'slcCruiseCommand' and sent.valid and sent.logMonoTime == START + 3_000_000_000
  assert sent.slcCruiseCommand.commandId == issued.message.slcState.commandId == 1
  assert sent.slcCruiseCommand.targetMps == pytest.approx(25.)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 90.
  cruise.append(command_receipt(runtime, sent.slcCruiseCommand, 3.05, 60., 90.))
  assert host_tick(3.05).message.slcState.commandStatus == 'applied'
  assert host.command_sock.send.call_count == 1


# --- the switch, followed live by the planner host ---

SECOND = 1_000_000_000


def saved_params(tmp_path, *, switch, safe_mode=False):
  from openpilot.common.params import Params
  params = Params(str(tmp_path))
  for name, value in {'SpeedLimitController': True, 'IsMetric': True, 'TeslaSpeedSync': switch, 'SafeMode': safe_mode}.items():
    params.put_bool(name, value, block=True)
  params.put('SLCPriority1', 'Dashboard', block=True)
  params.put('SLCAdvanceHigherSeconds', 5, block=True)
  params.put('SLCAdvanceLowerSeconds', 3, block=True)
  params.put('SLCOverrideMode', int(PedalOverride.SET_SPEED), block=True)
  return params


def lifecycle(params, cp):
  with patch.object(planner_host.messaging, 'pub_sock', return_value=Mock()):
    return SavedSlcLifecycle(params, cp, {'SLC_REPLAY_RUNTIME': '1'})


def test_lifecycle_refresh_marks_the_rule_from_the_saved_switch_and_keeps_every_saved_choice(tmp_path):
  params = saved_params(tmp_path, switch=True)
  host = lifecycle(params, rule_cp())
  runtime, changed = host.refresh(START)
  assert changed and runtime.settings.set_speed_rule
  assert runtime.settings == replace(planner_host.slc_runtime_settings(params, rule_cp(), {'SLC_REPLAY_RUNTIME': '1'}), set_speed_rule=True)
  assert (runtime.settings.advance_higher_seconds, runtime.settings.advance_lower_seconds) == (5, 3), 'Adopt limits early'
  assert runtime.settings.pedal_override is PedalOverride.SET_SPEED, 'After an accelerator override'
  sm, cp = scene(rule_cp())
  first = tick(runtime, sm, cp, 0)
  assert runtime.set_speed_rule and first.result.ceiling is None and first.message.slcState.automaticAdoptionPending


@pytest.mark.parametrize('case', ['switch off', 'safe mode', 'started without the frame admitted', 'another car'])
def test_lifecycle_refresh_without_the_rule_is_the_saved_settings_untouched(tmp_path, case):
  params = saved_params(tmp_path, switch=case != 'switch off', safe_mode=case == 'safe mode')
  honda = replay_fixture.RuntimeReplayTests()
  honda.setUp()
  cp = {'started without the frame admitted': rule_cp(admitted=False), 'another car': honda.cp}.get(case) or rule_cp()
  runtime, changed = lifecycle(params, cp).refresh(START)
  assert changed and not runtime.settings.set_speed_rule
  assert runtime.settings == planner_host.slc_runtime_settings(params, cp, {'SLC_REPLAY_RUNTIME': '1'})
  assert (runtime.settings.advance_higher_seconds, runtime.settings.advance_lower_seconds) == (5, 3)
  assert runtime.settings.pedal_override is PedalOverride.SET_SPEED
  sm, cp = scene(cp)
  tick(runtime, sm, cp, 0)
  assert not runtime.set_speed_rule


@pytest.mark.parametrize('start', [False, True])
def test_lifecycle_refresh_follows_the_switch_within_a_second_and_starts_a_new_session(tmp_path, start):
  params = saved_params(tmp_path, switch=start)
  host = lifecycle(params, rule_cp())
  first, _ = host.refresh(START)
  assert first.settings.set_speed_rule == start
  queued_actions, queued_cruise = deque(['action']), deque(['event'])
  params.put_bool('TeslaSpeedSync', not start, block=True)
  assert host.refresh(START + SECOND - 1, pending_actions=queued_actions, pending_cruise=queued_cruise) == (first, False)
  assert first.settings.set_speed_rule == start and queued_actions and queued_cruise, 'not read again within the second'
  second, changed = host.refresh(START + SECOND, pending_actions=queued_actions, pending_cruise=queued_cruise)
  assert changed and second is not first and second.session_id != first.session_id
  assert second.settings.set_speed_rule == (not start)
  assert not queued_actions and not queued_cruise, 'nothing queued for the old owner of SET crosses over'
  assert (second.settings.advance_higher_seconds, second.settings.advance_lower_seconds, second.settings.pedal_override) == (
    5, 3, PedalOverride.SET_SPEED), 'the saved choices are the same either way'
  assert host.refresh(START + 2 * SECOND) == (second, False), 'unchanged settings keep the session'


def test_lifecycle_refresh_keeps_the_session_when_only_another_setting_changes_under_the_rule(tmp_path):
  params = saved_params(tmp_path, switch=True)
  host = lifecycle(params, rule_cp())
  first, _ = host.refresh(START)
  # Early adoption and the accelerator-override choice are SLC choices like any other under the rule.
  params.put('SLCAdvanceLowerSeconds', 10, block=True)
  params.put('SLCOverrideMode', int(PedalOverride.SPEED_REACHED), block=True)
  assert host.refresh(START + SECOND) == (first, True)
  assert first.settings.advance_lower_seconds == 10 and first.settings.pedal_override is PedalOverride.SPEED_REACHED
  assert first.settings.set_speed_rule
  params.put('SLCConfirmation', 1, block=True)
  second, changed = host.refresh(START + 2 * SECOND)
  assert changed and second is first and second.settings.set_speed_rule, 'an SLC choice of its own renews choices, not the session'
