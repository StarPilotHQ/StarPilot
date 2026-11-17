"""Protect limit decisions against legacy Maps conversion noise on the Float32 wire."""
from dataclasses import replace
import math
import struct

import pytest

from openpilot.starpilot.speed_limits import acceptance as acc, history
from openpilot.starpilot.speed_limits.map_observation import repair_legacy_map_speed
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
from openpilot.starpilot.speed_limits.tests.test_advance import Drive


def numeric_drive(metric=True):
  drive = Drive(higher=0, lower=0)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 140.
  drive.runtime.is_metric = metric
  drive.runtime.settings = replace(drive.runtime.settings,
    selection=parse({'SLCPriority1': 'Dashboard', 'SLCPriority2': 'Maps'}).selection,
    offsets=OffsetSchedule((OffsetBand(0., None, 0.),)))
  return drive


@pytest.mark.parametrize('metric', [False, True])
@pytest.mark.parametrize('posted', [30, 50, 80, 100, 130, 250])
def test_legacy_map_conversion_does_not_reprompt_for_the_same_limit(metric, posted):
  drive = numeric_drive(metric)
  exact = posted / 3.6
  # Frozen maps.ParseMaxSpeed conversion; source correction must also account for existing tiles.
  mapped = posted * .277778
  first = drive.tick(current=mapped, dashboard=exact, target=0.)
  accepted = first.result.acceptance.state.accepted.candidate.speed_mps
  drive.runtime.settings = replace(drive.runtime.settings, acceptance=acc.Policy())
  switched = drive.tick(current=mapped, dashboard=None, target=0.)
  state = switched.message.slcState
  assert state.source == 'map' and not state.hasPending
  assert state.speedLimit == accepted
  assert switched.result.ceiling == first.result.ceiling


@pytest.mark.parametrize('exact,mapped', [
  (50 / 3.6, 50 * (1 / 3.6)),
  (45 * .44704, 45 * 1.609344 / 3.6),
  (45 * .44704, math.nextafter(45 * .44704, math.inf)),
])
def test_equivalent_arithmetic_collapses_to_the_same_live_wire_value(exact, mapped):
  drive = numeric_drive()
  first = drive.tick(current=mapped, dashboard=exact, target=0.)
  accepted = first.result.acceptance.state.accepted.candidate.speed_mps
  drive.runtime.settings = replace(drive.runtime.settings, acceptance=acc.Policy())
  switched = drive.tick(current=mapped, dashboard=None, target=0.)
  assert switched.message.slcState.speedLimit == accepted
  assert not switched.message.slcState.hasPending


@pytest.mark.parametrize('abbreviated', [False, True])
def test_alternating_same_posted_sources_keeps_the_original_deadline(abbreviated):
  drive = numeric_drive()
  drive.runtime.settings = replace(drive.runtime.settings, acceptance=acc.Policy())
  exact = 50 / 3.6
  mapped = 50 * .277778 if abbreviated else exact
  decision_ids, starts, timeout_frames = set(), set(), []
  for index in range(801):
    output = drive.tick(current=mapped, dashboard=exact if (index // 10) % 2 == 0 else None,
      target=0., at=drive.now+50_000_000)
    state = output.message.slcState
    assert not output.result.errors
    if state.hasPending:
      decision_ids.add(state.decisionId)
      starts.add(state.pendingSinceMonoTime)
    if output.result.acceptance.basis == 'timeout_reject':
      timeout_frames.append(index)
  assert not state.hasPending and len(decision_ids) == len(starts) == 1
  assert timeout_frames == [600]


def test_legacy_signature_sweep():
  def f32(value):
    return struct.unpack('f', struct.pack('f', value))[0]
  for posted in range(4, 251):
    assert repair_legacy_map_speed(f32(posted * .277778)) == f32(posted / 3.6)
    assert repair_legacy_map_speed(f32(posted / 3.6)) == f32(posted / 3.6)
  for posted in range(3, 251):
    assert repair_legacy_map_speed(f32(posted * .44704)) == f32(posted * .44704)
  for speed in (f32(50 / 3.6 + .00004), 1e300):
    assert repair_legacy_map_speed(speed) == speed


def test_upcoming_legacy_limit_matches_at_crossing():
  drive = numeric_drive()
  drive.runtime.update_advance(5, 5)
  drive.tick(current=50 * .277778, target=0.)
  drive.runtime.settings = replace(drive.runtime.settings, acceptance=acc.Policy())
  preview = drive.tick(current=50 * .277778, target=55 * .277778).message.slcState
  assert preview.hasPending and preview.pendingSpeedLimit == preview.upcomingSpeedLimit
  crossed = drive.tick(current=55 / 3.6, target=0., way=2).message.slcState
  assert crossed.speedLimit == preview.upcomingSpeedLimit
  assert crossed.decisionId == preview.decisionId
  assert crossed.pendingSinceMonoTime == preview.pendingSinceMonoTime


@pytest.mark.parametrize('source', ['map', 'dashboard', 'vision'])
def test_restore_repairs_only_saved_maps_limits(source):
  drive = numeric_drive()
  road = drive.tick(current=50 * .277778, target=0.)
  saved = road.result.acceptance.state.accepted
  # The saved record predates the repair and carries the original wire value.
  legacy = float(drive.sm['mapdOut'].speedLimit)
  saved = replace(saved, candidate=replace(saved.candidate, source=source, speed_mps=legacy))
  restored = history.decode(history.encode(saved, 1), session_id=drive.runtime.session_id)
  assert restored is not None
  if source != 'map':
    assert restored.candidate.speed_mps == legacy
    return
  drive.runtime.state = replace(drive.runtime.state, acceptance=acc.new_session(drive.runtime.session_id, restored))
  drive.runtime.settings = replace(drive.runtime.settings, acceptance=acc.Policy())
  state = drive.tick(current=50 / 3.6, dashboard=50 / 3.6, target=0.).message.slcState
  assert state.speedLimit == restored.candidate.speed_mps and not state.hasPending


def test_same_numeric_producer_restart_keeps_decision_but_retires_the_old_press():
  drive = numeric_drive()
  drive.runtime.settings = replace(drive.runtime.settings, acceptance=acc.Policy())
  first = drive.tick(current=50 / 3.6, target=0.).message.slcState
  replacement = drive.tick(current=50 / 3.6, target=0., producer=42).message.slcState
  assert replacement.decisionId == first.decisionId
  assert replacement.pendingSinceMonoTime == first.pendingSinceMonoTime
  assert replacement.presentationId != first.presentationId
  acted = drive.tick(current=50 / 3.6, target=0., producer=42, action=drive.action(first, 'accept'))
  assert acted.message.slcState.hasPending
  assert acted.message.slcState.acceptedSpeedLimit == 20.


def test_plain_source_gap_keeps_numeric_decision_and_original_expiry():
  drive = numeric_drive()
  drive.runtime.settings = replace(drive.runtime.settings, acceptance=acc.Policy())
  first = drive.tick(current=50 / 3.6, target=0.).message.slcState
  lost = drive.tick(current=50 / 3.6, target=0., alive=False).message.slcState
  assert lost.decisionId == first.decisionId and lost.presentationId == first.presentationId
  expired = drive.tick(current=50 / 3.6, target=0., at=first.pendingSinceMonoTime+acc.CONFIRMATION_NS)
  assert not expired.message.slcState.hasPending
  assert expired.result.acceptance.basis == 'timeout_reject'


@pytest.mark.parametrize('before,after,metric', [(40 / 3.6, 41 / 3.6, False), (45 * .44704, 46 * .44704, True)])
def test_real_one_unit_changes_still_ask_even_when_a_display_can_hide_the_difference(before, after, metric):
  drive = numeric_drive(metric)
  first = drive.tick(current=before, dashboard=before, target=0.)
  drive.runtime.settings = replace(drive.runtime.settings, acceptance=acc.Policy())
  changed = drive.tick(current=after, dashboard=after, target=0.)
  assert changed.message.slcState.hasPending
  assert changed.message.slcState.pendingSpeedLimit != first.message.slcState.acceptedSpeedLimit
  if not metric:
    assert round(before / .44704) == round(after / .44704) == 25
