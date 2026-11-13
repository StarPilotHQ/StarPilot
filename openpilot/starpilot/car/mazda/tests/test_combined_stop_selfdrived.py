"""Combined-mode stops through selfdrived's own permission check (SelfdriveD.aol_native_permission_lost).

Panda withholds lateral while the car is stopped and keeps longitudinal. selfdrived used to read
that as a lost grant: controlsMismatch, TAKE CONTROL IMMEDIATELY, and openpilot longitudinal
disengaged at the stop (route 00000051--a42c0843d4, 1136.9 s). These run the real selfdrived
method on the real current_native decode and decide_axes, frame by frame, with panda modelled from
a timestamped drive history (test_standstill_release_transport.Drive): a sample taken during a stop
still shows the stop after the host has pulled away, as with a stop shorter than the wire lag.

Needs the host native extensions (messaging): run with ./dev pytest.
"""
from types import SimpleNamespace

import pytest
from openpilot.starpilot.aol.runtime import SAFETY_MAX_AGE_NS, AxisDecision, current_native, decide_axes
from openpilot.starpilot.car.mazda import aol as mazda_aol
from openpilot.starpilot.car.mazda.tests.test_standstill_release_transport import (
  FRAME_NS, PANDA_CATCHUP_NS, SESSION, WIRE_LAG_NS, combined_drive)

INTENT = SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False)


class Host:
  """selfdrived's AOL permission state for one drive, stepped with the drive's own frames."""

  def __init__(self, drive):
    from openpilot.selfdrive.selfdrived.events import Events
    from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
    self.drive = drive
    self.sd = SelfdriveD.__new__(SelfdriveD)
    self.sd.CP = drive.cp
    self.sd.aol_session_id = SESSION
    self.sd.events = Events()
    self.previous = AxisDecision(desired_lateral=True, desired_longitudinal=True, lateral_active=True,
                                 longitudinal_active=True, native_acknowledged=True)
    self.trace = []

  def tick(self, **kwargs):
    self.drive.tick(**kwargs)
    cs, sm, _ = self.drive.last
    now = self.drive.now
    self.sd.sm = sm
    self.sd.aol_car_state_log_ns = now
    native = current_native(sm, self.drive.cp, now_ns=now, axis_session_id=SESSION)
    requested = decide_axes(standard_lateral=False, standard_longitudinal=True, intent=INTENT, native=native,
                            car_state=cs, initialized=True, model_ready=True, no_entry=False,
                            immediate_disable=False, dm_lockout=False, pause_brake_mps=0.0)
    lost = self.sd.aol_native_permission_lost(cs, native, self.previous, requested, source_current=True,
                                              driver_override=False, lost_honda_lateral_grant=False,
                                              ordinary_long_withdrawal=False, now_ns=now)
    self.trace.append((now, bool(cs.standstill), native.lateralAllowed, requested.lateral_active,
                       requested.longitudinal_active, lost))
    self.previous = requested
    return lost


def cruise(host, frames=100):
  for _ in range(frames):
    assert not host.tick(speed=10.0)


def stop_and_pull_away(host, stop_frames, moving_frames=300):
  return [host.tick(standstill=True) for _ in range(stop_frames)] + [host.tick() for _ in range(moving_frames)]


@pytest.mark.parametrize('wire_lag_ns', (WIRE_LAG_NS, 160_000_000, SAFETY_MAX_AGE_NS - PANDA_CATCHUP_NS))
@pytest.mark.parametrize('stop_frames', (3, 10, 15, 200))
def test_combined_stop_is_not_lost_permission(wire_lag_ns, stop_frames):
  host = Host(combined_drive(wire_lag_ns))
  cruise(host)
  lost = stop_and_pull_away(host, stop_frames)
  assert not any(lost)
  stop_start = len(host.trace) - stop_frames - 300
  after = host.trace[stop_start:]
  # Panda's stop denial did reach the host, and lateral went inactive while it held.
  denied = [row for row in after if not row[2]]
  assert denied and not any(row[3] for row in denied)
  # Denials from a short stop arrive after the host has left standstill (the review's case).
  if wire_lag_ns > stop_frames * FRAME_NS:
    assert any(not row[1] for row in denied)
  # Longitudinal stayed engaged throughout, and lateral came back once panda granted it again.
  assert all(row[4] for row in after)
  assert after[-1][2] and after[-1][3]


@pytest.mark.parametrize('stop_frames', (3, 200))
def test_without_the_mazda_exception_selfdrived_faults(monkeypatch, stop_frames):
  # The selfdrived method must route through the Mazda helper: removing it brings back the fault.
  monkeypatch.setattr(mazda_aol, 'expected_stopped_lateral_denial', lambda *a, **k: False)
  host = Host(combined_drive(160_000_000))
  cruise(host)
  assert any(stop_and_pull_away(host, stop_frames))


def test_without_stop_history_a_short_stop_still_faults(monkeypatch):
  # The exact review trace: stop 100 ms, panda feedback 160 ms late. Only the stop history covers it.
  monkeypatch.setattr(mazda_aol, 'note_standstill', lambda history, cp, cs, cs_ns, session: None)
  host = Host(combined_drive(160_000_000))
  cruise(host)
  assert any(stop_and_pull_away(host, 10))


def test_a_denial_after_pulling_away_is_lost_permission():
  host = Host(combined_drive())
  cruise(host)
  assert not any(stop_and_pull_away(host, 50, moving_frames=100))
  host.drive.panda_revoked = True
  lost = [host.tick() for _ in range(30)]
  assert any(lost)


def test_a_denial_while_cruising_is_lost_permission():
  host = Host(combined_drive())
  cruise(host)
  host.drive.panda_revoked = True
  assert any(host.tick(speed=10.0) for _ in range(30))


def test_longitudinal_also_withdrawn_at_a_stop_is_lost_permission():
  host = Host(combined_drive())
  cruise(host)
  host.tick(standstill=True)
  host.drive.long_revoked = True
  assert any(host.tick(standstill=True) for _ in range(30))


def delayed_stop_sample(host):
  # Stop 100 ms, then move until panda's first delayed stop sample arrives (the host is moving by then).
  [host.tick(standstill=True) for _ in range(10)]
  for _ in range(40):
    host.tick()
    cs, sm, native = host.drive.last
    if not native.lateralAllowed:
      return cs, sm, native
  raise AssertionError("no delayed stop sample")


def test_stop_history_is_per_session():
  host = Host(combined_drive(160_000_000))
  cruise(host)
  cs, _, _ = delayed_stop_sample(host)
  last_stop = host.sd.aol_mazda_stop_history[1]
  assert mazda_aol.note_standstill(('other-session', last_stop), host.drive.cp, cs, host.drive.now, SESSION) is None
  assert mazda_aol.note_standstill((SESSION, last_stop), host.drive.cp, cs, host.drive.now, SESSION) == (SESSION, last_stop)


def test_stop_history_bounds():
  from openpilot.starpilot.car.mazda.aol import STOP_HISTORY_NS, STOP_SAMPLE_MARGIN_NS
  host = Host(combined_drive(160_000_000))
  cruise(host)
  cs, sm, native = delayed_stop_sample(host)
  drive = host.drive
  last_stop = host.sd.aol_mazda_stop_history[1]
  check = lambda history, now: mazda_aol.expected_stopped_lateral_denial(  # noqa: E731
    drive.cp, cs, native, sm=sm, axis_session_id=SESSION, now_ns=now, stop_history=history)
  assert not cs.standstill and native.observedMonoTime <= last_stop
  assert check((SESSION, last_stop), drive.now)
  assert not check(('other-session', last_stop), drive.now)
  assert not check(None, drive.now)
  # A sample taken more than the margin after the last standstill frame is not a stop sample.
  assert not check((SESSION, native.observedMonoTime - STOP_SAMPLE_MARGIN_NS - 1), drive.now)
  # The history expires with the release grace.
  assert not check((SESSION, drive.now - STOP_HISTORY_NS - 1), drive.now)
