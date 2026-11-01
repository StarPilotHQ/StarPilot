"""Offline service and Params fakes; no IPC, device, or settings writes."""
from unittest.mock import Mock
from openpilot.starpilot.ui.presentation import BitmapFonts


from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import patch

import pyray as rl
import pytest

from openpilot.cereal import log, messaging
from openpilot.starpilot.ui.onroad_state import AlertSize, ObservationKind
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter as NativeRuntimeSnapshotAdapter, current_alert, current_message, display_message
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.shell import ShellMode


NOW = 10_000_000_000
BOOT_OFFSET_NS = time.clock_gettime_ns(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)) - time.monotonic_ns()


class RuntimeSnapshotAdapter(NativeRuntimeSnapshotAdapter):
  def __init__(self, *args, **kwargs):
    self._fixture_now_ns = NOW
    if "mono_clock" not in kwargs:
      first = True

      def mono_clock():
        nonlocal first
        if first:
          first = False
          return NOW - 1
        return self._fixture_now_ns

      kwargs["mono_clock"] = mono_clock
      kwargs["boot_clock"] = lambda: self._fixture_now_ns + BOOT_OFFSET_NS
    super().__init__(*args, **kwargs)

  def build(self, *args, now_ns=None, **kwargs):
    self._fixture_now_ns = NOW if now_ns is None else now_ns
    return super().build(*args, now_ns=now_ns, **kwargs)


class ParamsFake:
  def __init__(self):
    self.values = {"Version": "1.2.3", "GitCommit": "abc1234", "GitCommitDate": "1710000000 2024-03-09",
                   "LongitudinalPersonality": "1", "DongleId": "device", "DisableUpdates": "0"}

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key):
    return self.values.get(key) == "1"


class SubMasterFake:
  def __init__(self):
    self.frame = 3
    self.messages = {}
    self.valid = {}
    self.alive = {}
    self.seen = {}
    self.updated = {}
    self.logMonoTime = {}
    self.recv_frame = {}
    self.recv_time = {}

  def put(self, service, message, *, age_ns=0, frame=3):
    self.messages[service] = message
    self.valid[service] = True
    self.alive[service] = True
    self.seen[service] = True
    self.updated[service] = True
    self.logMonoTime[service] = NOW - age_ns + (BOOT_OFFSET_NS if service == "pandaStates" else 0)
    self.recv_frame[service] = frame
    self.recv_time[service] = (NOW - age_ns) / 1e9

  def __getitem__(self, service):
    return self.messages[service]


def ui_fake():
  sm = SubMasterFake()
  sm.put("deviceState", NS(started=True, networkType=NS(raw=1), networkStrength=NS(raw=2), lastAthenaPingTime=NOW,
                           maxTempC=65.6, thermalStatus=log.DeviceState.ThermalStatus.ok))
  sm.put("pandaStates", [NS(pandaType=log.PandaState.PandaType.tres, ignitionLine=True, ignitionCan=False)])
  sm.put("carState", NS(vEgoCluster=15.0, vEgo=16.0, vCruiseCluster=80.0))
  sm.put("carControl", NS(latActive=True, longActive=False, actuators=NS(torque=0.3)))
  sm.put("selfdriveState", NS(experimentalMode=False, alertSize=NS(raw=0), alertStatus=NS(raw=0),
                               alertText1="", alertText2=""))
  sm.put("slcState", NS(enabled=True, displayOnly=False, observationKind="valid", source="dashboard", speedLimit=20.0, offset=1.0,
                         pendingSpeedLimit=0.0, effectiveCap=21.0, acceptedSpeedLimit=0.0,
                         hasPending=False, hasCeiling=True, hasAccepted=False, sessionId="uuid-text",
                         decisionId=7, presentationId=9, status="active"))
  return NS(sm=sm, params=ParamsFake(), prime_state=NS(is_paired=lambda: True), started=True, started_frame=1, is_metric=False,
            has_longitudinal_control=True, CP=None, recording_audio=False)


class TestRuntimeSnapshot(unittest.TestCase):
  def test_switchback_display_uses_payload_reader_with_original_source_metadata(self):
    from opendbc.car.structs import CarParams
    ui = ui_fake()
    cp = CarParams()
    cp.carFingerprint, cp.brand = 'HYUNDAI_IONIQ_6', 'hyundai'
    cp.steerControlType = CarParams.SteerControlType.torque
    cp.safetyConfigs = [CarParams.SafetyConfig(safetyModel=CarParams.SafetyModel.hyundaiCanfd)]
    ui.CP = cp
    ui.sm['deviceState'].startedMonoTime = NOW - 1_000_000_000
    adapter = RuntimeSnapshotAdapter(ui)
    owner = Mock()
    owner.sample.side_effect = AssertionError('A SubMaster payload is not an Event envelope')
    owner.sample_state.return_value = True
    adapter._switchback = owner
    snapshot = adapter.build(ShellMode.ONROAD, now_ns=NOW, onroad_view_only=True)
    self.assertTrue(snapshot.onroad.switchback_mode)
    owner.sample.assert_not_called()
    owner.sample_state.assert_called_once_with(ui.sm['slcState'], drive_id=NOW - 1_000_000_000,
                                              now_ns=NOW, event_ns=ui.sm.logMonoTime['slcState'])

  def test_personality_feedback_uses_current_drive_plan_and_override_events(self):
    ui = ui_fake()
    ui.sm.put('longitudinalPlan', NS(hasLead=True))
    ui.sm.put('onroadEvents', [NS(name=log.OnroadEvent.EventName.gasPressedOverride)])
    ui.sm.put('driverMonitoringState', NS(isRHD=True))
    adapter = RuntimeSnapshotAdapter(ui)
    state = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
    self.assertTrue(state.lead_detected and state.gas_pressed_override and state.right_hand_drive)
    ui.sm.recv_frame['longitudinalPlan'] = ui.sm.recv_frame['onroadEvents'] = 0
    state = adapter.build(ShellMode.ONROAD, now_ns=NOW + 1).onroad
    self.assertFalse(state.lead_detected or state.gas_pressed_override)

  def test_home_temperature_uses_live_units_and_warning_status(self):
    from openpilot.starpilot.ui.home import HomeView
    from openpilot.starpilot.ui.presentation import Profile

    ui = ui_fake()
    ui.started = False
    adapter = RuntimeSnapshotAdapter(ui)
    view = HomeView.__new__(HomeView)
    view.fonts = Mock(spec=BitmapFonts, profile=Profile.LARGE)
    with patch.object(view, '_icon'), patch.object(view, '_text') as text, \
         patch.object(view, '_measure', return_value=NS(width=80, height=35)), \
         patch('openpilot.starpilot.ui.home.rl.draw_circle'), \
         patch('openpilot.starpilot.ui.home.clip.begin_scissor_mode'), patch('openpilot.starpilot.ui.home.clip.end_scissor_mode'), \
         patch('openpilot.starpilot.ui.home.outside_rounded_border'), \
         patch('openpilot.starpilot.ui.home.rl.draw_rectangle_rounded') as edge:
      for temperature, high, celsius, fahrenheit in ((0., False, '0°C', '32°F'), (65.6, False, '66°C', '150°F'),
                                                    (107., True, '107°C', '225°F'), (None, False, '--', '--')):
        ui.sm['deviceState'].maxTempC = temperature
        ui.sm['deviceState'].thermalStatus = log.DeviceState.ThermalStatus.critical if high else log.DeviceState.ThermalStatus.ok
        ui.sm.valid['deviceState'] = temperature is not None
        for metric, expected in ((True, celsius), (False, fahrenheit), (True, celsius)):
          with self.subTest(temperature=temperature, metric=metric):
            ui.is_metric = metric
            home = adapter.build(ShellMode.HOME, now_ns=NOW).home
            self.assertEqual(home.temperature_c, temperature)
            self.assertEqual(home.metric, metric)
            text.reset_mock()
            edge.reset_mock()
            view._sidebar(home)
            self.assertEqual(text.call_args_list[2].args[0], expected)
            expected_color = (201, 34, 49, 255) if high else (255, 255, 255, 255)
            self.assertEqual(rl.color_to_int(edge.call_args_list[0].args[-1]), rl.color_to_int(expected_color))

  # Temporary old-route replay compatibility: remove this entire test when support is removed.
  # Cleanup instructions: openpilot/starpilot/ui/old_starpilot_route_shim.py.
  def test_legacy_slc_is_replay_only_display_fallback(self):
    from openpilot.tools.replay.display_clock import ClockSample
    from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed
    ui = ui_fake()
    legacy = messaging.new_message('customReserved6', valid=True).customReserved6
    legacy.slcSpeedLimit, legacy.slcSpeedLimitOffset, legacy.slcSpeedLimitSource = 13.4112, 2.2352, 'None'
    ui.sm.put('customReserved6', legacy)
    ui.sm.valid['slcState'] = False
    adapter = RuntimeSnapshotAdapter(ui)
    self.assertNotEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.speed_limit.kind, ObservationKind.VALID)
    ui.replay_sample = ui.sm.replay_sample = ClockSample(NOW, None, NOW, epoch=1, valid=True)
    state = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
    observation = state.speed_limit
    self.assertAlmostEqual(observation.speed_limit_mps, 13.4112, places=4)
    self.assertAlmostEqual(observation.offset_mps, 2.2352, places=4)
    self.assertAlmostEqual(observation.effective_cap_mps, 15.6464, places=4)
    self.assertEqual(observation.source, 'unknown')
    self.assertFalse(observation.action_enabled)
    self.assertIsNone(observation.decision_id)
    presentation = resolve_unified_speed(state)
    self.assertEqual(presentation.posted_text, '30')
    self.assertEqual(presentation.limit_text, '35')
    self.assertEqual(presentation.offset_text, '+5')
    self.assertEqual(presentation.active_side, 'none')
    ui.params.values['SpeedLimitController'] = '1'
    ui.sm['carState'].canValid, ui.sm['carState'].canTimeout = True, False
    ui.sm['carControl'].longActive = True
    state = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
    presentation = resolve_unified_speed(state)
    self.assertEqual((presentation.active_side, presentation.status), ('max', 'Using set speed'))
    from openpilot.starpilot.ui.onroad_state import slc_controls
    from openpilot.starpilot.ui.presentation import Profile
    self.assertEqual(slc_controls(Profile.LARGE, state), ())
    ui.sm['carState'].vCruiseCluster = 40.0
    presentation = resolve_unified_speed(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad)
    self.assertEqual((presentation.active_side, presentation.status), ('max', 'Using set speed'))
    legacy.slcOverriddenSpeed = 20.0
    presentation = resolve_unified_speed(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad)
    self.assertEqual((presentation.active_side, presentation.status), ('max', 'Set-speed override'))
    ui.sm['carState'].gasPressed = True
    presentation = resolve_unified_speed(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad)
    self.assertEqual((presentation.active_side, presentation.status), ('max', 'Set-speed override'))
    ui.sm['carState'].gasPressed = False
    legacy.slcOverriddenSpeed = 0.0
    ui.sm['carControl'].longActive = False
    presentation = resolve_unified_speed(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad)
    self.assertEqual((presentation.active_side, presentation.status), ('none', 'Cruise off'))
    ui.sm.valid['slcState'] = True
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.speed_limit.source, 'dashboard')
    ui.sm.valid['slcState'] = False
    ui.sm.logMonoTime['customReserved6'] = NOW - 1_000_000_001
    self.assertNotEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.speed_limit.kind, ObservationKind.VALID)

  def test_replay_display_survives_qlog_intervals_and_expires(self):
    from openpilot.tools.replay.display_clock import ClockSample
    for service, interval_ns in (('vehicleParameters', 250_000_000),
                                 ('driverStateV2', 500_000_000), ('driverMonitoringState', 500_000_000)):
      with self.subTest(service=service):
        sm = SubMasterFake()
        message = NS()
        sm.put(service, message, age_ns=interval_ns + 20_000_000)
        self.assertIsNone(display_message(sm, service, NOW, after_frame=1))
        sm.replay_sample = ClockSample(NOW, None, NOW, epoch=1, valid=True)
        self.assertIs(display_message(sm, service, NOW, after_frame=1), message)
        self.assertIsNone(current_message(sm, service, NOW, after_frame=1))
        self.assertIsNone(display_message(sm, service, NOW + 2 * interval_ns, after_frame=1))
        self.assertIsNone(display_message(sm, service, NOW, after_frame=3))
        self.assertIsNone(display_message(sm, service, NOW - 2 * interval_ns, after_frame=1))
        sm.valid[service] = False
        self.assertIsNone(display_message(sm, service, NOW, after_frame=1))
        sm.valid[service] = True
        sm.replay_sample = ClockSample(None, None, NOW, epoch=2)
        self.assertIsNone(display_message(sm, service, NOW, after_frame=1))

  def test_replay_uses_recorded_time_without_rewriting_messages(self):
    from openpilot.tools.replay.display_clock import ClockSample
    ui = ui_fake()
    ui.replay_clock = object()
    ui.replay_sample = ClockSample(NOW, None, NOW * 1000, epoch=1, valid=True)
    ui.sm.replay_sample = ui.replay_sample
    ui.sm['selfdriveState'].personality = 'relaxed'
    before = dict(ui.sm.logMonoTime)
    state = NativeRuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD).onroad
    self.assertTrue(state.engaged)
    self.assertEqual(state.personality, 2)
    self.assertEqual(state.observed_ns, NOW)
    self.assertEqual(ui.sm.logMonoTime, before)
    ui.replay_sample = ClockSample(None, None, NOW * 1000, epoch=1)
    ui.sm.replay_sample = ui.replay_sample
    self.assertFalse(NativeRuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD).onroad.engaged)

  def test_replay_pause_holds_visuals_but_publisher_death_does_not(self):
    from openpilot.tools.replay.display_clock import ClockSample
    ui = ui_fake()
    ui.replay_clock = object()
    ui.replay_sample = ClockSample(NOW, None, NOW * 1000, epoch=1, paused=True, valid=True)
    ui.sm.replay_sample = ui.replay_sample
    ui.sm['selfdriveState'].alertText1 = 'Recorded alert'
    ui.sm['selfdriveState'].alertSize = NS(raw=1)
    state = NativeRuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD).onroad
    self.assertTrue(state.engaged)
    self.assertEqual(state.alert.text1, 'Recorded alert')
    ui.sm.replay_sample = ClockSample(None, None, NOW * 1000, epoch=1, paused=True)
    self.assertEqual(current_alert(ui.sm, NOW, after_frame=1).size, AlertSize.NONE)

  def test_replay_has_no_parked_control_authority(self):
    from openpilot.tools.replay.display_clock import ClockSample
    ui = ui_fake()
    ui.replay_clock = object()
    ui.started = False
    ui.replay_sample = ClockSample(NOW, NOW, NOW * 1000, epoch=1, valid=True)
    ui.sm.replay_sample = ui.replay_sample
    adapter = NativeRuntimeSnapshotAdapter(ui)
    self.assertFalse(adapter.confirmed_offroad())
    self.assertFalse(adapter.build(ShellMode.HOME).device.offroad)

  def test_replay_epoch_resets_projectors_and_saved_layout_cache(self):
    from openpilot.tools.replay.display_clock import ClockSample
    ui = ui_fake()
    ui.replay_clock = object()
    ui.replay_sample = ClockSample(NOW, None, NOW * 1000, epoch=1, valid=True)
    ui.sm.replay_sample = ui.replay_sample
    adapter = NativeRuntimeSnapshotAdapter(ui)
    adapter.build(ShellMode.ONROAD)
    previous = adapter._conditional
    ui.replay_sample = ClockSample(NOW - 1_000_000_000, None, NOW * 1000, epoch=2, valid=True)
    ui.sm.replay_sample = ui.replay_sample
    state = adapter.build(ShellMode.ONROAD).onroad
    self.assertIsNot(adapter._conditional, previous)
    self.assertFalse(state.engaged)

  def test_home_panda_presence_is_independent_of_ignition(self):
    for panda_type in (log.PandaState.PandaType.dos, log.PandaState.PandaType.tres, log.PandaState.PandaType.cuatro):
      for ignition in (False, True):
        with self.subTest(panda_type=panda_type, ignition=ignition):
          ui = ui_fake()
          ui.started = False
          ui.sm['deviceState'].started = False
          panda = messaging.new_message('pandaStates', 1).pandaStates[0]
          panda.pandaType = panda_type
          panda.ignitionLine = ignition
          ui.sm.put('pandaStates', [panda])
          self.assertTrue(RuntimeSnapshotAdapter(ui).build(ShellMode.HOME, now_ns=NOW).home.vehicle_online)

  def test_home_does_not_report_missing_or_unhealthy_panda_online(self):
    for failure in ('empty', 'unknown', 'invalid', 'dead', 'old', 'future', 'old_receipt'):
      with self.subTest(failure=failure):
        ui = ui_fake()
        if failure == 'empty':
          ui.sm.messages['pandaStates'] = []
        elif failure == 'unknown':
          ui.sm['pandaStates'][0].pandaType = log.PandaState.PandaType.unknown
        elif failure == 'invalid':
          ui.sm.valid['pandaStates'] = False
        elif failure == 'dead':
          ui.sm.alive['pandaStates'] = False
        elif failure == 'old':
          ui.sm.logMonoTime['pandaStates'] -= 1_000_000_000
        elif failure == 'future':
          ui.sm.logMonoTime['pandaStates'] += 1_000_000_000
        else:
          ui.sm.recv_time['pandaStates'] -= 1
        self.assertFalse(RuntimeSnapshotAdapter(ui).build(ShellMode.HOME, now_ns=NOW).home.vehicle_online)

  def test_every_registered_settings_destination_stays_selected(self):
    adapter = RuntimeSnapshotAdapter(ui_fake())
    state = adapter.build(ShellMode.SETTINGS)
    for item in state.settings.availability:
      if item.available:
        with self.subTest(destination=item.destination):
          self.assertEqual(adapter.build(ShellMode.SETTINGS, item.destination).selected, item.destination)
    self.assertEqual(adapter.build(ShellMode.SETTINGS, 'unknown').selected, Destination.STAR)
    self.assertEqual(adapter.build(ShellMode.HOME, Destination.BLUETOOTH).selected, Destination.STAR)

  def test_home_keeps_live_fields_without_querying_leaf_panels(self):
    ui = ui_fake()
    ui.started = False
    forbidden = {'DongleId', 'HardwareSerial', 'UpdaterState', 'UpdaterFetchAvailable',
                 'DisableUpdates', 'UpdaterTargetBranch', 'OpenpilotEnabledToggle',
                 'DisengageOnAccelerator', 'IsLdwEnabled', 'AlwaysOnDM', 'RecordFront', 'RecordAudio'}
    original_get, original_bool = ui.params.get, ui.params.get_bool

    def get(key):
      self.assertNotIn(key, forbidden)
      return original_get(key)

    def get_bool(key):
      self.assertNotIn(key, forbidden)
      return original_bool(key)

    ui.params.get, ui.params.get_bool = get, get_bool
    access = NS(status=lambda: self.fail('Home queried Galaxy leaf credentials'))
    adapter = RuntimeSnapshotAdapter(ui, galaxy_access=access, bluetooth_powered=lambda: True)
    for frame in range(3):
      ui.params.values.update(GitCommit=f'commit{frame}', GitBranch=f'branch{frame}',
                              UpdaterCurrentDescription=f'upstream / detail{frame}')
      ui.prime_state.is_paired = lambda paired=frame % 2 == 0: paired
      snapshot = adapter.build(ShellMode.HOME, now_ns=NOW + frame * 500_000_000)
      self.assertEqual(snapshot.home.commit, f'commit{frame}')
      self.assertEqual(snapshot.home.branch, f'branch{frame}')
      self.assertEqual(snapshot.home.description, f'7.0 / detail{frame}')
      self.assertEqual(snapshot.home.paired, frame % 2 == 0)
      self.assertTrue(snapshot.home.bluetooth)
      self.assertFalse(snapshot.onroad.camera_available)
    ui.params.get, ui.params.get_bool = original_get, original_bool
    adapter.galaxy_access = None
    ui.params.values['DongleId'] = 'changed'
    self.assertEqual(adapter.build(ShellMode.SETTINGS, Destination.DEVICE).device.dongle_id, 'changed')
    ui.started = True
    self.assertTrue(adapter.build(ShellMode.ONROAD).onroad.camera_available)

  def test_compact_menu_preserves_consumed_state_and_temporal_transitions(self):
    full_ui, menu_ui = ui_fake(), ui_fake()
    full, menu = RuntimeSnapshotAdapter(full_ui), RuntimeSnapshotAdapter(menu_ui)
    modes = (ShellMode.ONROAD, ShellMode.SETTINGS, ShellMode.SETTINGS, ShellMode.ONROAD, ShellMode.HOME)
    for frame, mode in enumerate(modes):
      now = NOW + frame * 500_000_000
      for ui in (full_ui, menu_ui):
        ui.sm.frame += 1
        ui.params.values['LongitudinalPersonality'] = str(frame % 3)
        ui.params.values['ShowCSCStatus'] = str(frame % 2)
        ui.prime_state.is_paired = lambda paired=frame % 2 == 0: paired
      for destination in Destination:
        expected = full.build(mode, destination, compact_scroll_x=frame * -25, now_ns=now)
        actual = menu.build(mode, destination, compact_scroll_x=frame * -25, now_ns=now, menu_only=True)
        self.assertEqual((actual.home, actual.onroad, actual.settings, actual.selected, actual.device.offroad),
                         (expected.home, expected.onroad, expected.settings, expected.selected, expected.device.offroad))
        if mode != ShellMode.SETTINGS:
          self.assertEqual(actual, expected)

  def test_compact_menu_does_not_read_native_leaf_preferences(self):
    ui = ui_fake()
    reads = []
    original_get, original_bool = ui.params.get, ui.params.get_bool
    ui.params.get = lambda key: (reads.append(key), original_get(key))[1]
    ui.params.get_bool = lambda key: (reads.append(key), original_bool(key))[1]
    adapter = RuntimeSnapshotAdapter(ui)
    adapter.build(ShellMode.SETTINGS, now_ns=NOW, menu_only=True)
    leaf_keys = {'DongleId', 'HardwareSerial', 'UpdaterState', 'UpdaterFetchAvailable', 'UpdaterTargetBranch',
                 'DisableUpdates', 'OpenpilotEnabledToggle', 'DisengageOnAccelerator', 'RecordFront', 'RecordAudio'}
    self.assertFalse(leaf_keys.intersection(reads))
    reads.clear()
    adapter.build(ShellMode.SETTINGS, now_ns=NOW + 1)
    self.assertTrue(leaf_keys.issubset(reads))

  def test_home_branch_and_native_chestnut_state(self):
    ui = ui_fake()
    ui.params.values["GitBranch"] = "Domathon"
    ui.chestnut_state = NS(value="loading")
    home = RuntimeSnapshotAdapter(ui).build(ShellMode.HOME, now_ns=NOW).home
    self.assertEqual(home.branch, "Domathon")
    self.assertEqual(home.gpu_state, "loading")

  def test_temporary_eps_alert_keeps_upstream_status_and_visual(self):
    ui = ui_fake()
    ui.sm.put("selfdriveState", NS(experimentalMode=False, alertSize=NS(raw=1), alertStatus=NS(raw=1),
                                    alertText1="Steering Temporarily Unavailable", alertText2="",
                                    alertType="steerTempUnavailableSilent/warning", alertHudVisual=NS(raw=1)))
    alert = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.alert
    self.assertEqual(alert.size, AlertSize.SMALL)
    self.assertTrue(alert.user_prompt)
    self.assertFalse(alert.critical)
    self.assertEqual(alert.visual_alert, 1)

  def test_onroad_ancillary_reads_are_bounded_and_settings_refreshes_immediately(self):
    ui = ui_fake()
    ui.params.values['UpdaterState'] = 'idle'
    reads = []
    original_get = ui.params.get
    original_bool = ui.params.get_bool
    ui.params.get = lambda key: (reads.append(key), original_get(key))[1]
    ui.params.get_bool = lambda key: (reads.append(key), original_bool(key))[1]
    adapter = RuntimeSnapshotAdapter(ui)
    first = adapter.build(ShellMode.ONROAD, now_ns=NOW)
    self.assertGreaterEqual(len(reads), 15)
    reads.clear()
    ui.params.values['UpdaterState'] = 'downloading'
    same_second = adapter.build(ShellMode.ONROAD, now_ns=NOW + 10_000_000)
    self.assertEqual(len(reads), 3)
    self.assertEqual(same_second.software, first.software)
    refreshed = adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_000_000_000)
    self.assertNotEqual(refreshed.software, first.software)
    ui.params.values['UpdaterState'] = 'idle'
    settings = adapter.build(ShellMode.SETTINGS, now_ns=NOW + 1_000_000_001)
    self.assertEqual(settings.software, first.software)
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_000_000_002).software, first.software)

  def test_background_finalization_allows_metadata_check(self):
    from openpilot.starpilot.ui.software_state import SoftwareRequest
    ui = ui_fake()
    ui.params.values['UpdaterState'] = 'finalizing update...'
    ui.params.values['UpdaterFastState'] = {'background': True}
    with patch.object(RuntimeSnapshotAdapter, '_parked', return_value=True):
      state = RuntimeSnapshotAdapter(ui).build(ShellMode.SETTINGS, now_ns=NOW).software
      self.assertIn(SoftwareRequest.CHECK_FOR_UPDATES, state.available_actions)
      ui.params.values['UpdaterFastState']['check'] = {'state': 'checking'}
      state = RuntimeSnapshotAdapter(ui).build(ShellMode.SETTINGS, now_ns=NOW).software
      self.assertNotIn(SoftwareRequest.CHECK_FOR_UPDATES, state.available_actions)

  def test_customization_uses_existing_refresh_and_invalidation(self):
    adapter = RuntimeSnapshotAdapter(ui_fake())
    first = default_document()
    second = default_document()
    second["layouts"]["compact"]["max_speed"]["x"] = 200
    with patch("openpilot.starpilot.ui.runtime_snapshot.read_customization", side_effect=[first, second, first]) as read:
      self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.customization, first)
      self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW + 1).onroad.customization, first)
      self.assertEqual(read.call_count, 1)
      self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_000_000_000).onroad.customization, second)
      adapter.invalidate_appearance()
      self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_000_000_001).onroad.customization, first)
      self.assertEqual(read.call_count, 3)

  def test_lead_indicator_requires_fresh_model_and_radar_in_same_drive(self):
    ui = ui_fake()
    ui.sm.put("modelV2", NS())
    ui.sm.put("radarState", NS())
    adapter = RuntimeSnapshotAdapter(ui)
    self.assertTrue(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.lead_indicator_source_fresh)
    ui.sm.alive["radarState"] = False
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.lead_indicator_source_fresh)
    ui.sm.alive["radarState"] = True
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 201_000_000).onroad.lead_indicator_source_fresh)
    ui.sm.logMonoTime["radarState"] = NOW + 202_000_000
    ui.sm.logMonoTime["modelV2"] = NOW + 202_000_000
    ui.sm.logMonoTime["deviceState"] = NOW + 202_000_000
    ui.sm.logMonoTime["pandaStates"] = NOW + BOOT_OFFSET_NS + 202_000_000
    ui.sm.recv_time["pandaStates"] = (NOW + 202_000_000) / 1e9
    ui.started_frame = 3
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 202_000_000).onroad.lead_indicator_source_fresh)
    ui.sm.recv_frame["modelV2"] = 4
    ui.sm.recv_frame["radarState"] = 4
    self.assertTrue(adapter.build(ShellMode.ONROAD, now_ns=NOW + 202_000_000).onroad.lead_indicator_source_fresh)
    ui.sm.valid["radarState"] = False
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 202_000_000).onroad.lead_indicator_source_fresh)

  def test_stock_confidence_source_requires_current_post_start_model(self):
    ui = ui_fake()
    ui.sm.put("modelV2", NS(meta=NS(disengagePredictions=NS(brakeDisengageProbs=[0.1], steerOverrideProbs=[0.1]))))
    adapter = RuntimeSnapshotAdapter(ui)
    initial = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
    self.assertTrue(initial.stock_confidence_source_fresh)
    self.assertEqual(initial.stock_confidence_source_stamp_ns, NOW)
    self.assertEqual(initial.stock_confidence_drive_frame, 1)
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 201_000_000).onroad.stock_confidence_source_fresh)
    ui.sm.logMonoTime["modelV2"] = NOW + 202_000_000
    ui.sm.logMonoTime["deviceState"] = NOW + 202_000_000
    ui.sm.logMonoTime["pandaStates"] = NOW + BOOT_OFFSET_NS + 202_000_000
    ui.sm.recv_time["pandaStates"] = (NOW + 202_000_000) / 1e9
    ui.sm.recv_frame["modelV2"] = 4
    ui.started_frame = 4
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 202_000_000).onroad.stock_confidence_source_fresh)
    ui.sm.recv_frame["modelV2"] = 5
    self.assertTrue(adapter.build(ShellMode.ONROAD, now_ns=NOW + 202_000_000).onroad.stock_confidence_source_fresh)
    ui.sm.alive["modelV2"] = False
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 202_000_000).onroad.stock_confidence_source_fresh)

  def test_personality_notice_uses_observed_change_once_and_native_alert_wins(self):
    ui = ui_fake()
    ui.sm.messages['selfdriveState'].personality = NS(raw=1)
    adapter = RuntimeSnapshotAdapter(ui)
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.size, AlertSize.NONE)

    ui.sm.messages['selfdriveState'].personality = NS(raw=0)
    ui.sm.logMonoTime['selfdriveState'] = NOW + 100_000_000
    changed = adapter.build(ShellMode.ONROAD, now_ns=NOW + 100_000_000).onroad.alert
    self.assertEqual((changed.size, changed.text1, changed.text2),
                     (AlertSize.MID, 'Aggressive', 'Driving Personality'))

    ui.sm.logMonoTime['selfdriveState'] = NOW + 1_000_000_000
    ui.sm.logMonoTime['deviceState'] = NOW + 1_000_000_000
    ui.sm.logMonoTime['pandaStates'] = NOW + BOOT_OFFSET_NS + 1_000_000_000
    ui.sm.recv_time['pandaStates'] = (NOW + 1_000_000_000) / 1e9
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_000_000_000).onroad.alert.text1, 'Aggressive')
    ui.sm.logMonoTime['selfdriveState'] = NOW + 1_700_000_000
    ui.sm.logMonoTime['deviceState'] = NOW + 1_700_000_000
    ui.sm.logMonoTime['pandaStates'] = NOW + BOOT_OFFSET_NS + 1_700_000_000
    ui.sm.recv_time['pandaStates'] = (NOW + 1_700_000_000) / 1e9
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_700_000_000).onroad.alert.size, AlertSize.NONE)

    ui.sm.messages['selfdriveState'].personality = NS(raw=2)
    ui.sm.messages['selfdriveState'].alertSize = NS(raw=3)
    ui.sm.messages['selfdriveState'].alertText1 = 'Take control'
    ui.sm.messages['selfdriveState'].alertText2 = 'Now'
    ui.sm.logMonoTime['selfdriveState'] = NOW + 1_800_000_000
    ui.sm.recv_time['selfdriveState'] = (NOW + 1_800_000_000) / 1e9
    native = adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_800_000_000).onroad.alert
    self.assertEqual((native.size, native.text1), (AlertSize.FULL, 'Take control'))

  def test_hiding_personality_notices_keeps_native_safety_alerts_and_clears_pending_notice(self):
    from dataclasses import replace
    from openpilot.starpilot.ui.appearance_preferences import OnroadAppearance
    from openpilot.starpilot.ui.runtime_snapshot import PersonalityNotice
    from openpilot.starpilot.ui.onroad_state import OnroadAlert
    ui = ui_fake()
    ui.sm.messages['selfdriveState'].personality = NS(raw=1)
    adapter = RuntimeSnapshotAdapter(ui)
    adapter.build(ShellMode.ONROAD, now_ns=NOW)
    adapter._appearance_value = OnroadAppearance(hide_personality_alerts=True)
    ui.sm.messages['selfdriveState'].personality = NS(raw=0)
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.size, AlertSize.NONE)
    source = ui.sm.messages['selfdriveState']
    source.alertSize, source.alertType = NS(raw=1), 'personalityChanged/warning'
    source.alertText1 = 'Driving Personality: Aggressive'
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.size, AlertSize.NONE)
    source.alertType, source.alertText1, source.alertSize = 'controlsMismatch/immediateDisable', 'Take control', NS(raw=3)
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.text1, 'Take control')
    source.alertSize = NS(raw=0)
    adapter._appearance_value = replace(adapter._appearance_value, hide_personality_alerts=False)
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.size, AlertSize.NONE)
    source.personality = NS(raw=2)
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.text1, 'Relaxed')
    notice = PersonalityNotice()
    notice.update(NS(personality=1), drive_frame=1, event_ns=NOW, now_ns=NOW, native_alert=OnroadAlert())
    for safety in (OnroadAlert(AlertSize.FULL, 'Take control', critical=True, alert_type='personalityChanged/warning'),
                   OnroadAlert(AlertSize.SMALL, 'Warning', user_prompt=True, alert_type='personalityChanged/warning')):
      self.assertEqual(notice.update(NS(personality=2), drive_frame=1, event_ns=NOW, now_ns=NOW,
                                     native_alert=safety, hide=True), safety)

  def test_personality_notice_clears_on_invalid_source_and_new_drive(self):
    ui = ui_fake()
    ui.sm.messages['selfdriveState'].personality = NS(raw=1)
    adapter = RuntimeSnapshotAdapter(ui)
    adapter.build(ShellMode.ONROAD, now_ns=NOW)
    ui.sm.messages['selfdriveState'].personality = NS(raw=2)
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.text1, 'Relaxed')
    ui.sm.valid['selfdriveState'] = False
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.size, AlertSize.NONE)
    ui.sm.valid['selfdriveState'] = True
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.size, AlertSize.NONE)
    ui.sm.messages['selfdriveState'].personality = NS(raw=0)
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.text1, 'Aggressive')
    ui.started = False
    self.assertEqual(adapter.build(ShellMode.HOME, now_ns=NOW).onroad.alert.size, AlertSize.NONE)
    ui.started = True
    ui.started_frame = 2
    self.assertEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.size, AlertSize.NONE)

  def test_display_survives_ui_frame_gap_without_extending_control_authority(self):
    ui = ui_fake()
    ui.CP = NS(carFingerprint="", openpilotLongitudinalControl=True, pcmCruise=False)
    ui.sm.messages['carState'].canValid = True
    ui.sm.messages['carState'].canTimeout = False
    ui.sm.messages['carState'].vCruiseCluster = 86.0
    ui.sm.messages['carControl'].longActive = True
    ui.sm.messages['selfdriveState'].experimentalMode = True
    ui.sm.messages['selfdriveState'].enabled = True
    ui.sm.put('controlsState', NS(longControlState='pid'))
    for service in ('carState', 'carControl', 'controlsState', 'selfdriveState'):
      ui.sm.logMonoTime[service] = NOW - 50_000_000
      self.assertIsNone(current_message(ui.sm, service, NOW, after_frame=ui.started_frame))
    shown = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad
    self.assertTrue(shown.lateral_active and shown.longitudinal_active)
    self.assertTrue(shown.experimental_enabled)
    self.assertEqual(shown.cruise_kph, 86.0)
    self.assertFalse(shown.slc_system_long_available)
    ui.sm.logMonoTime['carControl'] = NOW - 201_000_000
    expired = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad
    self.assertFalse(expired.lateral_active or expired.longitudinal_active)
    ui.sm.logMonoTime['carControl'] = NOW
    ui.sm.valid['carControl'] = False
    invalid = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad
    self.assertFalse(invalid.lateral_active or invalid.longitudinal_active)
    ui.sm.valid['carControl'] = True
    ui.params.values['ExperimentalMode'] = '1'
    ui.sm.logMonoTime['selfdriveState'] = NOW - 201_000_000
    self.assertFalse(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.experimental_enabled)

  def test_set_speed_uses_upstream_control_fallback_only_for_zero_cluster_value(self):
    ui = ui_fake()
    ui.sm.messages['carState'].canValid = True
    ui.sm.messages['carState'].canTimeout = False
    ui.sm.messages['carState'].vCruiseCluster = 0.0
    ui.sm.put('controlsState', NS(vCruiseDEPRECATED=93.0))
    self.assertEqual(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.cruise_kph, 93.0)
    ui.sm.logMonoTime['controlsState'] = NOW - 201_000_000
    self.assertIsNone(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.cruise_kph)

  def test_alert_uses_upstream_display_lifetime_without_relaxing_control_data(self):
    ui = ui_fake()
    ui.sm.put('selfdriveState', NS(experimentalMode=False, enabled=False, alertSize=NS(raw=2),
                                   alertStatus=NS(raw=1), alertText1='Communication issue', alertText2='Check system'),
              age_ns=50_000_000)
    self.assertIsNone(current_message(ui.sm, 'selfdriveState', NOW, after_frame=ui.started_frame))
    self.assertEqual(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.text1,
                     'Communication issue')
    ui.sm.put('selfdriveState', NS(experimentalMode=False, enabled=False, alertSize=NS(raw=0),
                                   alertStatus=NS(raw=0), alertText1='', alertText2=''))
    self.assertEqual(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.size, AlertSize.NONE)

  def test_alert_rejects_invalid_and_old_drive_then_uses_upstream_lost_source_warning(self):
    ui = ui_fake()
    ui.sm.put('selfdriveState', NS(experimentalMode=False, enabled=True, alertSize=NS(raw=3),
                                   alertStatus=NS(raw=2), alertText1='Warning', alertText2=''), age_ns=5_100_000_000)
    self.assertEqual(current_alert(ui.sm, NOW, after_frame=ui.started_frame).text2, 'System Unresponsive')
    ui.sm.valid['selfdriveState'] = False
    self.assertEqual(current_alert(ui.sm, NOW, after_frame=ui.started_frame).size, AlertSize.NONE)
    ui.sm.valid['selfdriveState'] = True
    ui.started_frame = ui.sm.recv_frame['selfdriveState']
    self.assertEqual(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.alert.size, AlertSize.NONE)

  def test_home_indicators_use_live_power_and_fresh_chestnut_source(self):
    ui = ui_fake()
    ui.sm.messages["deviceState"].chestnutPresent = True
    ui.chestnut_state = NS(value="active")
    state = RuntimeSnapshotAdapter(ui, bluetooth_powered=lambda: True).build(ShellMode.HOME, now_ns=NOW).home
    self.assertTrue(state.bluetooth)
    self.assertTrue(state.gpu_present)
    self.assertTrue(state.gpu_active)
    self.assertFalse(RuntimeSnapshotAdapter(ui, bluetooth_powered=lambda: True).build(ShellMode.ONROAD, now_ns=NOW).home.bluetooth)
    ui.sm.valid["deviceState"] = False
    state = RuntimeSnapshotAdapter(ui, bluetooth_powered=lambda: False).build(ShellMode.HOME, now_ns=NOW).home
    self.assertEqual(state.bluetooth, "off")
    for value in ("off", "on", "connected"):
      self.assertEqual(RuntimeSnapshotAdapter(ui, bluetooth_powered=lambda value=value: value).build(ShellMode.HOME, now_ns=NOW).home.bluetooth, value)
    self.assertFalse(state.gpu_present)
    self.assertFalse(state.gpu_active)
    ui.sm.valid["deviceState"] = True
    ui.usb_unknown = True
    self.assertFalse(RuntimeSnapshotAdapter(ui).build(ShellMode.HOME, now_ns=NOW).home.gpu_present)

  def test_home_shows_starpilot_version_without_changing_upstream_params(self):
    ui = ui_fake()
    ui.params.values["Version"] = "0.11.2"
    ui.params.values["UpdaterCurrentDescription"] = "0.11.2 / Domathon"
    snapshot = RuntimeSnapshotAdapter(ui).build(ShellMode.HOME, now_ns=NOW)
    self.assertEqual(snapshot.home.version, "7.0")
    self.assertEqual(snapshot.home.description, "7.0 / Domathon")
    self.assertEqual(ui.params.values["Version"], "0.11.2")
    self.assertEqual(ui.params.values["UpdaterCurrentDescription"], "0.11.2 / Domathon")
    ui.params.values.pop("UpdaterCurrentDescription")
    self.assertEqual(RuntimeSnapshotAdapter(ui).build(ShellMode.HOME, now_ns=NOW).home.description, "7.0")

  def test_nested_curve_independent_of_outer_slc_validity(self):
    ui = ui_fake()
    ui.params.values["ShowCSCStatus"] = "1"
    ui.CP = NS(carFingerprint="", openpilotLongitudinalControl=True, pcmCruise=False)
    ui.sm.messages["carState"].canValid = True
    ui.sm.messages["carState"].canTimeout = False
    ui.sm.messages["carControl"].longActive = True
    ui.sm.messages["selfdriveState"].enabled = True
    ui.sm.put("controlsState", NS(longControlState="pid"))
    event = messaging.new_message("slcState")
    event.valid = False  # SLC-specific envelope validity cannot veto independent Curve evidence.
    event.logMonoTime = NOW
    event.slcState.curve = {
      "version": 1, "sessionId": "curve-session", "sequence": 1,
      "observedMonoTime": NOW, "validUntilMonoTime": NOW + 100_000_000,
      "modelMonoTime": NOW - 20_000_000, "configured": True, "documentValid": True,
      "hasCandidate": True, "candidateMps": 15.0, "hasCeiling": True, "ceilingMps": 14.0,
      "applied": True, "controlling": True, "training": False,
      "calibrationProgress": 20.0, "comfortAccel": 2.0, "bindingDistance": 60.0,
      "reason": "available", "plannerStatus": "selected", "persistenceStatus": "saved",
      "glow": True, "curveOnly": True, "hasRoadCurvature": True, "roadCurvature": 0.02,
    }
    decoded = messaging.log_from_bytes(event.to_bytes())
    ui.sm.put("slcState", decoded.slcState)
    ui.sm.valid["slcState"] = decoded.valid
    snapshot = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW)
    self.assertEqual(snapshot.onroad.speed_limit.kind, ObservationKind.STALE)
    self.assertIsNotNone(snapshot.onroad.curve)
    self.assertTrue(snapshot.onroad.curve.curve_only)
    curve = snapshot.onroad.curve
    self.assertIsNotNone(curve)
    if curve is None:
      self.fail("the fresh curve is missing")
    curvature = curve.road_curvature
    self.assertIsNotNone(curvature)
    if curvature is None:
      self.fail("the fresh curve has no curvature")
    self.assertAlmostEqual(curvature, 0.02, places=5)
    self.assertTrue(snapshot.onroad.slc_system_long_available)
    ui.sm.logMonoTime["slcState"] = NOW - 300_000_000
    self.assertIsNone(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.curve)
    ui.sm.logMonoTime["slcState"] = NOW
    ui.sm.recv_frame["slcState"] = ui.started_frame
    self.assertIsNone(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.curve)
    ui.sm.recv_frame["slcState"] = ui.started_frame + 1
    ui.sm.valid["slcState"] = True
    invalid = messaging.new_message("slcState")
    invalid.slcState.curve.version = 0
    ui.sm.put("slcState", messaging.log_from_bytes(invalid.to_bytes()).slcState)
    self.assertIsNone(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.curve)

  def test_fresh_state_tracks_each_control_axis_and_text_session(self):
    ui = ui_fake()
    ui.CP = NS(carFingerprint="vehicle-platform-id")
    snapshot = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW)
    self.assertTrue(snapshot.onroad.lateral_active)
    self.assertFalse(snapshot.onroad.longitudinal_active)
    self.assertTrue(snapshot.onroad.engaged)
    self.assertEqual(snapshot.onroad.speed_limit.session_id, "uuid-text")
    self.assertEqual(snapshot.onroad.speed_limit.kind, ObservationKind.VALID)
    self.assertEqual(snapshot.onroad.speed_limit.effective_cap_mps, 21.0)
    self.assertIsNone(snapshot.home.stats)
    self.assertEqual(snapshot.home.model_label, "")

  def test_zero_speed_remains_visible_across_signed_filtered_estimates(self):
    import pyray as rl
    from openpilot.starpilot.ui.onroad_large_widgets import CurrentSpeedHud

    for metric in (False, True):
      ui = ui_fake()
      ui.is_metric = metric
      adapter = RuntimeSnapshotAdapter(ui)
      ui.sm.messages["carState"].vEgoCluster = 0.0
      for speed in (-0.238205, -0.001, 0.0, 0.001, 0.44704):
        with self.subTest(metric=metric, speed=speed):
          ui.sm.messages["carState"].vEgo = speed
          state = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
          self.assertEqual(state.speed_mps, max(0.0, speed))
          fonts = Mock(spec=BitmapFonts)
          fonts.measure.return_value = NS(width=10, height=20)
          fonts.vertical_ink.return_value = (0., 20.)
          CurrentSpeedHud(fonts).render(rl.Rectangle(0, 0, 1800, 1020), state)
          self.assertEqual(fonts.draw.call_args_list[0].args[0], "1" if speed == 0.44704 and not metric else
                           str(round(max(0., speed) * (3.6 if metric else 2.2369362921))))
          self.assertEqual(fonts.draw.call_args_list[1].args[0], "km/h" if metric else "mph")
      ui.sm.logMonoTime["carState"] = NOW - 2_000_000_000
      self.assertIsNone(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.speed_mps)

  def test_stale_and_malformed_values_never_become_displayed_speed(self):
    ui = ui_fake()
    ui.sm.logMonoTime["slcState"] = NOW - 500_000_000
    ui.sm.messages["carState"].vEgoCluster = float("nan")
    ui.sm.messages["carState"].vEgo = float("inf")
    snapshot = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW)
    self.assertEqual(snapshot.onroad.speed_limit.kind, ObservationKind.STALE)
    self.assertIsNone(snapshot.onroad.speed_mps)
    ui.sm.put("slcState", NS(**{**vars(ui.sm.messages["slcState"]), "speedLimit": float("nan")}))
    snapshot = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW)
    self.assertEqual(snapshot.onroad.speed_limit.kind, ObservationKind.STALE)

  def test_pretransition_and_invalid_transport_are_rejected(self):
    ui = ui_fake()
    ui.sm.recv_frame["carControl"] = ui.started_frame
    ui.sm.valid["slcState"] = False
    self.assertIsNone(current_message(ui.sm, "carControl", NOW, after_frame=ui.started_frame))
    snapshot = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW)
    self.assertFalse(snapshot.onroad.engaged)
    self.assertEqual(snapshot.onroad.speed_limit.kind, ObservationKind.STALE)

  def test_signed_torque_and_ambiguous_started_never_enable_offroad_actions(self):
    ui = ui_fake()
    ui.sm.messages["carState"].canValid = True
    ui.sm.messages["carState"].canTimeout = False
    ui.sm.messages["carControl"].actuators.torque = -0.9
    ui.sm.put("carOutput", NS(actuatorsOutput=NS(torque=0.4)))
    ui.sm.put("controlsState", NS(lateralControlState=NS(which=lambda: "torqueState")))
    snapshot = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW)
    self.assertAlmostEqual(snapshot.onroad.torque_utilization, -0.4)
    self.assertTrue(snapshot.onroad.torque_source_available)
    ui.sm.logMonoTime["deviceState"] = NOW - 2_000_000_000
    snapshot = RuntimeSnapshotAdapter(ui).build(ShellMode.SETTINGS, now_ns=NOW)
    self.assertFalse(snapshot.device.offroad)
    self.assertTrue(snapshot.settings.destination(Destination.DEVICE).available)
    self.assertFalse(snapshot.device.available_actions)
    self.assertTrue(snapshot.onroad.engaged)
    self.assertEqual(snapshot.onroad.speed_mps, 15.0)
    self.assertTrue(snapshot.onroad.camera_available)

  def test_health_gaps_do_not_restart_presentation_or_set_speed_timer(self):
    from pathlib import Path
    from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer

    ui = ui_fake()
    ui.sm['deviceState'].startedMonoTime = NOW - 1_000_000_000
    ui.sm['carControl'].longActive = True
    ui.sm['carState'].canValid = True
    ui.sm['carState'].canTimeout = False
    adapter = RuntimeSnapshotAdapter(ui)
    hud = CompactHudRenderer(Mock(spec=BitmapFonts, **vars(NS())), Path('/unused'))
    began = None
    for frame in range(240):
      now = NOW + frame * 50_000_000
      for service in ui.sm.messages:
        ui.sm.logMonoTime[service] = now + (BOOT_OFFSET_NS if service == 'pandaStates' else 0)
        ui.sm.recv_time[service] = now / 1e9
      ui.sm.alive['pandaStates'] = frame % 3 != 0
      ui.sm.alive['deviceState'] = frame % 5 != 0
      if frame % 7 == 0:
        ui.sm.logMonoTime['deviceState'] -= 2_000_000_000
      view = adapter.build(ShellMode.SETTINGS if frame % 13 == 0 else ShellMode.ONROAD, now_ns=now)
      self.assertEqual((view.onroad.drive_frame, view.onroad.torque_drive_frame), (1, 1))
      self.assertTrue(view.onroad.camera_available and view.onroad.engaged)
      self.assertFalse(view.device.offroad)
      opacity = hud._set_speed_opacity(view.onroad, now)
      if began is None:
        began = hud._set_speed_changed_ns
      self.assertEqual(hud._set_speed_changed_ns, began)
      if frame > 100:
        self.assertLess(opacity, .01)
    ui.started = False
    ended = adapter.build(ShellMode.HOME, now_ns=now).onroad
    self.assertFalse(ended.camera_available or ended.engaged)
    self.assertIsNone(ended.drive_frame)
    hud._set_speed_opacity(ended, now)
    self.assertIsNone(hud._set_speed_changed_ns)

  def test_torque_feedback_requires_actual_current_drive_output(self):
    ui = ui_fake()
    ui.sm.messages["carState"].canValid = True
    ui.sm.messages["carState"].canTimeout = False
    ui.sm.put("controlsState", NS(lateralControlState=NS(which=lambda: "torqueState")))
    adapter = RuntimeSnapshotAdapter(ui)
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.torque_source_available)
    ui.sm.put("carOutput", NS(actuatorsOutput=NS(torque=0.4)))
    self.assertAlmostEqual(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.torque_utilization, -0.4)
    for service in ("carOutput", "controlsState", "carState"):
      with self.subTest(service=service):
        ui.sm.valid[service] = False
        self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.torque_source_available)
        ui.sm.valid[service] = True
        ui.sm.recv_frame[service] = ui.started_frame
        self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.torque_source_available)
        ui.sm.recv_frame[service] = 3
        ui.sm.logMonoTime[service] = NOW - 201_000_000
        self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.torque_source_available)
        ui.sm.logMonoTime[service] = NOW
    ui.started = False
    self.assertFalse(adapter.build(ShellMode.HOME, now_ns=NOW).onroad.torque_source_available)

  def test_angle_torque_feedback_uses_current_vehicle_parameters(self):
    ui = ui_fake()
    ui.CP = NS(carFingerprint="", maxLateralAccel=3.0, openpilotLongitudinalControl=False, pcmCruise=True)
    ui.sm.messages["carState"].canValid = True
    ui.sm.messages["carState"].canTimeout = False
    ui.sm.put("controlsState", NS(lateralControlState=NS(which=lambda: "angleState"),
                                   curvature=0.001, desiredCurvature=0.002))
    ui.sm.put("vehicleParameters", NS(valid=True, roll=0.01))
    adapter = RuntimeSnapshotAdapter(ui)
    state = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
    self.assertTrue(state.torque_source_available)
    self.assertAlmostEqual(state.torque_utilization, (0.002 * 16 ** 2 - 0.01 * 9.81) / 3)
    ui.sm.logMonoTime["vehicleParameters"] = NOW - 1_000_000_000
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.torque_source_available)

  def test_slc_controls_require_fresh_system_long_independent_of_pcm_cruise(self):
    ui = ui_fake()
    ui.CP = NS(carFingerprint="", openpilotLongitudinalControl=True, pcmCruise=False)
    ui.sm.messages["carState"].canTimeout = False
    ui.sm.messages["carState"].canValid = True
    ui.sm.messages["carControl"].longActive = True
    ui.sm.messages["selfdriveState"].enabled = True
    ui.sm.put("controlsState", NS(longControlState="pid"))
    self.assertTrue(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.slc_system_long_available)
    ui.CP.pcmCruise = True
    self.assertTrue(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.slc_system_long_available)
    ui.CP.openpilotLongitudinalControl = False
    self.assertFalse(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.slc_system_long_available)
    ui.CP.openpilotLongitudinalControl = True
    ui.sm.messages["carState"].canValid = False
    self.assertFalse(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.slc_system_long_available)
    ui.sm.messages["carState"].canValid = True
    ui.sm.logMonoTime["controlsState"] = NOW - 200_000_000
    self.assertFalse(RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad.slc_system_long_available)


@pytest.mark.parametrize('brand,standstill,cruise_standstill,cruise_enabled,expected', [
  ('honda', False, False, True, True),
  ('toyota', False, False, True, True),
  ('toyota', True, False, True, False),
  ('toyota', False, True, True, False),
  ('toyota', False, False, False, False),
])
def test_wheel_confirmation_hint_preserves_toyota_resume(brand, standstill, cruise_standstill, cruise_enabled, expected):
  ui = ui_fake()
  ui.CP = NS(carFingerprint='', brand=brand, openpilotLongitudinalControl=True, pcmCruise=brand == 'toyota')
  car = ui.sm['carState']
  car.canTimeout, car.canValid, car.standstill = False, True, standstill
  car.cruiseState = NS(available=True, enabled=cruise_enabled, standstill=cruise_standstill)
  ui.sm['carControl'].longActive = ui.sm['carControl'].enabled = True
  ui.sm['selfdriveState'].enabled = True
  ui.sm.put('controlsState', NS(longControlState='pid'))
  adapter = RuntimeSnapshotAdapter(ui)
  assert adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.slc_wheel_confirmation_available == expected
  ui.sm.logMonoTime['carControl'] = NOW - 200_000_000
  assert not adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.slc_wheel_confirmation_available


class TestParkedClockDomains(unittest.TestCase):
  def setUp(self):
    self.ui = ui_fake()
    self.ui.started = False
    self.ui.sm["deviceState"].started = False
    self.ui.sm["pandaStates"][0].ignitionLine = False
    self.mono = 100_000_000_000
    self.boot = 110_000_000_000
    self.adapter = RuntimeSnapshotAdapter(self.ui, mono_clock=lambda: self.mono, boot_clock=lambda: self.boot)
    self.mono += 150_000_000
    self.boot += 150_000_000
    self.refresh_both()

  def refresh_both(self):
    sm = self.ui.sm
    sm.logMonoTime["deviceState"] = self.mono - 50_000_000
    sm.logMonoTime["pandaStates"] = self.boot - 50_000_000
    sm.recv_time["deviceState"] = (self.mono - 30_000_000) / 1e9
    sm.recv_time["pandaStates"] = (self.mono - 30_000_000) / 1e9
    sm.updated["deviceState"] = True
    sm.updated["pandaStates"] = True

  def parked(self):
    current = self.adapter.confirmed_offroad()
    self.assertEqual(current, self.adapter.build(ShellMode.SETTINGS, now_ns=self.mono).device.offroad)
    return current

  def test_authority_check_does_not_read_preferences_or_change_display_mode(self):
    self.adapter._last_mode = ShellMode.ONROAD
    with patch.object(self.ui.params, 'get', side_effect=AssertionError('unexpected preference read')), \
         patch.object(self.ui.params, 'get_bool', side_effect=AssertionError('unexpected preference read')), \
         patch.object(self.adapter._conditional, 'project') as conditional:
      self.assertTrue(self.adapter.confirmed_offroad())
      self.assertEqual(self.adapter._last_mode, ShellMode.ONROAD)
      conditional.assert_not_called()
      self.ui.sm['pandaStates'][0].ignitionCan = True
      self.assertTrue(self.adapter.confirmed_offroad())

  def test_forced_offroad_unlocks_settings_and_connectivity_with_ignition(self):
    self.ui.sm['pandaStates'][0].ignitionCan = True
    self.assertTrue(self.adapter.connectivity_allowed())
    self.assertTrue(self.adapter.confirmed_offroad())
    snapshot = self.adapter.build(ShellMode.SETTINGS, Destination.NETWORK, now_ns=self.mono)
    self.assertTrue(snapshot.settings.destination(Destination.NETWORK).available)
    self.assertTrue(snapshot.device.offroad)
    self.ui.started = True
    self.assertFalse(self.adapter.connectivity_allowed())
    self.ui.started = False
    self.ui.sm['deviceState'].started = True
    self.assertFalse(self.adapter.connectivity_allowed())

  def test_connectivity_rejects_invalid_stale_empty_and_future_sources(self):
    self.ui.sm['pandaStates'][0].ignitionLine = True
    self.assertTrue(self.adapter.connectivity_allowed())
    sm = self.ui.sm
    for service in ('deviceState', 'pandaStates'):
      for field in ('seen', 'alive', 'valid'):
        with self.subTest(service=service, field=field):
          getattr(sm, field)[service] = False
          self.assertFalse(self.adapter.connectivity_allowed())
          getattr(sm, field)[service] = True
      old = sm.logMonoTime[service]
      sm.logMonoTime[service] -= 2_000_000_000
      self.assertFalse(self.adapter.connectivity_allowed())
      sm.logMonoTime[service] = old + 2_000_000_000
      self.assertFalse(self.adapter.connectivity_allowed())
      sm.logMonoTime[service] = old
    pandas = sm.messages['pandaStates']
    sm.messages['pandaStates'] = []
    self.assertFalse(self.adapter.connectivity_allowed())
    sm.messages['pandaStates'] = pandas
    self.assertTrue(self.adapter.connectivity_allowed())

  def test_connectivity_suspend_requires_sources_published_after_barrier(self):
    self.ui.sm['pandaStates'][0].ignitionLine = True
    self.assertTrue(self.adapter.connectivity_allowed())
    self.boot += 10_000_000_000
    self.ui.sm.updated['deviceState'] = False
    self.ui.sm.updated['pandaStates'] = False
    self.assertFalse(self.adapter.connectivity_allowed())
    self.ui.sm.logMonoTime['pandaStates'] = self.boot - 20_000_000
    self.ui.sm.recv_time['pandaStates'] = (self.mono - 10_000_000) / 1e9
    self.assertFalse(self.adapter.connectivity_allowed())
    self.mono += 100_000_000
    self.boot += 100_000_000
    self.refresh_both()
    self.assertTrue(self.adapter.connectivity_allowed())
    self.assertTrue(self.adapter.confirmed_offroad())

  def test_fresh_publishers_use_their_own_clock_domains(self):
    self.assertIsNotNone(current_message(self.ui.sm, "pandaStates", self.mono, boot_now_ns=self.boot))
    self.assertTrue(self.parked())
    self.assertIsNone(current_message(self.ui.sm, "pandaStates", self.mono, boot_now_ns=self.mono))

  def test_suspend_denies_cached_evidence_until_both_sources_refresh(self):
    self.assertTrue(self.parked())
    self.boot += 10_000_000_000
    self.ui.sm.updated["deviceState"] = False
    self.ui.sm.updated["pandaStates"] = False
    self.assertFalse(self.parked())
    self.assertIsNone(current_message(self.ui.sm, "pandaStates", self.mono, boot_now_ns=self.boot))
    self.ui.sm.logMonoTime["pandaStates"] = self.boot - 20_000_000
    self.ui.sm.recv_time["pandaStates"] = (self.mono - 10_000_000) / 1e9
    self.ui.sm.updated["pandaStates"] = True
    self.assertFalse(self.parked())
    self.mono += 100_000_000
    self.boot += 100_000_000
    self.ui.sm.logMonoTime["deviceState"] = self.mono - 20_000_000
    self.ui.sm.recv_time["deviceState"] = (self.mono - 10_000_000) / 1e9
    self.ui.sm.updated["deviceState"] = True
    self.assertTrue(self.parked())

  def test_missing_invalid_onroad_and_future_sources_deny(self):
    self.assertTrue(self.parked())
    cases = ((lambda: self.ui.sm.seen.__setitem__("pandaStates", False),
              lambda: self.ui.sm.seen.__setitem__("pandaStates", True)),
             (lambda: self.ui.sm.valid.__setitem__("deviceState", False),
              lambda: self.ui.sm.valid.__setitem__("deviceState", True)),
             (lambda: setattr(self.ui, "started", True), lambda: setattr(self.ui, "started", False)),
             (lambda: self.ui.sm.logMonoTime.__setitem__("deviceState", self.mono + 1_000_000),
              lambda: self.refresh_both()),
             (lambda: self.ui.sm.logMonoTime.__setitem__("pandaStates", self.boot + 1_000_000),
              lambda: self.refresh_both()))
    for corrupt, restore in cases:
      corrupt()
      self.assertFalse(self.parked())
      restore()
      self.assertTrue(self.parked())

  def test_failed_clock_pair_denies_and_requires_a_new_device_sample(self):
    self.assertTrue(self.parked())
    original = self.adapter._mono_clock
    self.adapter._mono_clock = lambda: (_ for _ in ()).throw(OSError("clock unavailable"))
    self.assertFalse(self.parked())
    self.adapter._mono_clock = original
    boot_clock = self.adapter._boot_clock
    self.adapter._boot_clock = lambda: 0
    self.assertFalse(self.parked())
    self.adapter._boot_clock = boot_clock
    self.assertFalse(self.parked())
    self.mono += 100_000_000
    self.boot += 100_000_000
    self.refresh_both()
    self.assertTrue(self.parked())


class TestHostOffroad(unittest.TestCase):
  def test_only_private_parked_host_ui_has_authority_without_publishers(self):
    env = {'SP_HOST_PARKED': '1', 'SP_HOST_RUNTIME': '1', 'OPENPILOT_PREFIX': 'starpilot-dev-test', 'SP_HOST_PREFIX': 'starpilot-dev-test'}
    for overrides, expected in (({}, True), ({'SP_HOST_PARKED': '0'}, False), ({'SP_HOST_RUNTIME': '0'}, False),
                                ({'SP_HOST_PREFIX': 'other'}, False), ({'OPENPILOT_PREFIX': ''}, False)):
      with self.subTest(overrides=overrides), patch.dict('os.environ', {**env, **overrides}, clear=True):
        ui = ui_fake()
        ui.started = False
        ui.sm = SubMasterFake()
        adapter = RuntimeSnapshotAdapter(ui)
        self.assertEqual(adapter.confirmed_offroad(), expected)
        self.assertEqual(adapter.build(ShellMode.HOME).device.offroad, expected)
        ui.started = True
        self.assertFalse(adapter.confirmed_offroad())
        ui.started = False
        ui.replay_clock = object()
        self.assertFalse(adapter.confirmed_offroad())
        self.assertFalse(adapter.build(ShellMode.HOME).device.offroad)


if __name__ == "__main__":
  unittest.main()


@pytest.mark.parametrize('phase,label,present', [('active', 'Jetlink Model', True),
                                               ('ready', 'Jetlink Model', True), ('connecting', '', True),
                                               ('disconnected', '', False), ('unavailable', '', False),
                                               ('off', '', False)])
def test_home_jetlink_model_name_and_gpu_share_existing_feed(phase, label, present):
  from openpilot.starpilot.models.jetlink_status import JetlinkStatus
  ui = ui_fake()
  feed = Mock()
  feed.snapshot.return_value = JetlinkStatus('Computer', phase, port='connected' if present else None)
  ui.jetlink_status_feed = feed
  ui.chestnut_state = NS(value='active' if phase == 'active' else 'loading')
  adapter = RuntimeSnapshotAdapter(ui)
  with patch.object(adapter._jetlink, 'set_enabled') as enable:
    home = adapter.build(ShellMode.HOME, now_ns=NOW).home
  enable.assert_called_once_with(False)
  assert home.model_label == label
  assert home.gpu_present is present
  assert home.gpu_active is (phase == 'active')
