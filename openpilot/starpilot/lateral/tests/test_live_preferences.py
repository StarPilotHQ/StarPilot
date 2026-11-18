import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest

from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.lateral import lane_change_preferences, lane_runtime
from openpilot.starpilot.lateral.live_preferences import LiveSteeringPreferences
from openpilot.starpilot.lateral.tests.test_lane_runtime import Frame, feed


@pytest.mark.parametrize('reader', ['highway', 'centering', 'lane_change'])
def test_actual_control_cycle_does_not_wait_for_live_settings_disk(reader):
  with OpenpilotPrefix(), mock.patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '0', 'AOL_REPLAY_RUNTIME': '0'}), \
       mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
    params = Params()
    cp = interfaces[CAR.HONDA_CIVIC].get_non_essential_params(CAR.HONDA_CIVIC)
    params.put('CarParams', cp.to_bytes(), block=True)
    actual, reference = Controls(), Controls()
    origin = time.monotonic_ns()
    expected = []
    for tick in range(110):
      now = origin + tick * 10_000_000
      with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
        feed(reference, now, tick)
        command, _ = reference.state_control()
        expected.append(command.to_dict())
    targets = {
      'highway': 'openpilot.selfdrive.controls.controlsd.read_gain',
      'centering': 'openpilot.starpilot.lateral.lane_runtime.read_settings',
      'lane_change': 'openpilot.starpilot.lateral.lane_change_preferences.read_saved',
    }
    actual.lane_change_preferences.last_read_ns = 0
    with mock.patch(targets[reader], side_effect=AssertionError('control thread waited for disk')):
      for tick in range(110):
        now = origin + tick * 10_000_000
        with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
          feed(actual, now, tick)
          command, _ = actual.state_control()
          assert command.to_dict() == expected[tick]
    now = origin + 110 * 10_000_000
    with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
      feed(actual, now, 110, fault=True)
      command, _ = actual.state_control()
    assert not command.latActive


def test_blocked_reader_keeps_complete_snapshot_and_does_not_delay_stop():
  with OpenpilotPrefix():
    params = Params()
    params.put('HighwaySmoothingLevel', 5., block=True)
    reader = LiveSteeringPreferences(params)
    before = reader.current
    assert before.highway_gain == .5
    entered, release = threading.Event(), threading.Event()
    next_snapshot = replace(before, highway_gain=.8)

    def blocked_read():
      entered.set()
      assert release.wait(2.)
      return next_snapshot

    with mock.patch.object(reader, 'INTERVAL', .001), mock.patch.object(reader, '_read', side_effect=blocked_read):
      reader.start()
      try:
        assert entered.wait(2.)
        thread = reader._thread
        reader.start()
        assert reader._thread is thread
        for _ in range(1000):
          assert reader.current is before
        reader.close()
        assert not release.is_set()
      finally:
        release.set()
        reader.close()
        assert reader._thread is not None
        reader._thread.join(2.)
        assert not reader._thread.is_alive()
    assert reader.current is before


def test_cached_preferences_reach_existing_owners_without_revalidation_each_tick():
  with OpenpilotPrefix():
    params = Params()
    reader = LiveSteeringPreferences(params)
    centering = lane_runtime.LaneCenteringHost(params, settings_reader=lambda: reader.current.lane_centering)
    policy = lane_change_preferences.LiveLaneChangePolicy(params, now_ns=1_000_000_000,
                                                        settings_reader=lambda: reader.current.lane_change)
    with mock.patch.object(lane_change_preferences, 'effective', wraps=lane_change_preferences.effective) as validate:
      for tick in range(200):
        now = 1_000_000_001 + tick * 10_000_000
        centering.sample(Frame(now), now_ns=now, lateral_active=True, longitudinal_active=False)
        policy.sample(now)
      assert validate.call_count == 0
      changed = replace(reader.current.lane_change, policy=replace(policy.applied, auto_lane_change=True))
      reader.current = replace(reader.current, lane_change=changed)
      assert policy.sample(4_000_000_000).auto_lane_change
      assert validate.call_count == 1
      reader.current = replace(reader.current, lane_change=replace(changed, readable=False))
      assert not policy.sample(4_000_000_001, signaling=True, maneuver_active=True).auto_lane_change
      assert validate.call_count == 1
    assert not policy.sample(0).enabled
    reader.current = replace(reader.current, lane_change=changed)
    assert policy.sample(5_000_000_000).auto_lane_change


def test_live_reader_uses_existing_validation_for_each_completed_refresh():
  with OpenpilotPrefix():
    params = Params()
    reader = LiveSteeringPreferences(params)
    params.put('HighwaySmoothingLevel', 3., block=True)
    params.put_bool('LaneCentering', True, block=True)
    params.put('LaneCenterOffset', .1, block=True)
    Path(params.get_param_path('LaneChangePreferences')).write_bytes(b'not-json')
    refreshed = reader._read()
    assert refreshed.highway_gain == .3
    assert refreshed.lane_centering.enabled
    assert refreshed.lane_centering.offset_m == .1
    assert not refreshed.lane_change.valid
    Path(params.get_param_path('HighwaySmoothingLevel')).write_bytes(b'nan')
    Path(params.get_param_path('LaneCentering')).write_bytes(b'bad')
    invalid = reader._read()
    assert invalid.highway_gain == 1.
    assert not invalid.lane_centering.enabled


def test_control_main_starts_reader_after_scheduling_and_closes_on_exit():
  from openpilot.selfdrive.controls import controlsd

  events = []
  preferences = mock.Mock()
  preferences.start.side_effect = lambda: events.append('start')
  preferences.close.side_effect = lambda: events.append('close')
  controls = mock.Mock(live_steering_preferences=preferences)
  controls.run.side_effect = KeyboardInterrupt
  with mock.patch.object(controlsd, 'config_realtime_process'), mock.patch.object(controlsd, 'Controls', return_value=controls), \
       mock.patch.object(controlsd.HARDWARE, 'get_device_type', return_value='pc'), \
       mock.patch.object(controlsd, 'set_core_affinity', side_effect=lambda cpus: events.append('affinity')):
    with pytest.raises(KeyboardInterrupt):
      controlsd.main()
  assert events == ['affinity', 'start', 'close']


def test_completed_background_refresh_reaches_the_control_snapshot():
  with OpenpilotPrefix():
    reader = LiveSteeringPreferences(Params())
    refreshed = replace(reader.current, highway_gain=.7)
    pause = threading.Event()
    with mock.patch.object(reader, 'INTERVAL', .001), mock.patch.object(reader, '_read', return_value=refreshed):
      reader.start()
      try:
        deadline = time.monotonic() + 2.
        while reader.current is not refreshed and time.monotonic() < deadline:
          pause.wait(.001)
        assert reader.current is refreshed
      finally:
        reader.close()
        assert reader._thread is not None
        reader._thread.join(2.)
        assert not reader._thread.is_alive()
