import os
import signal
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import Mock, patch

from openpilot.cereal import log, messaging
from openpilot.common.params import Params
from openpilot.starpilot.power.offroad_preferences import read_saved as read_power, effective as effective_power
from openpilot.starpilot.sentry_mode.policy import RESET_TIME_SECONDS, Settings
from openpilot.starpilot.sentry_mode.preferences import KEY, Preferences, encode
from openpilot.starpilot.sentry_mode.runtime import SentryRuntime
from openpilot.starpilot.sentry_mode import runtime as runtime_module


class Clock:
  def __init__(self):
    self.mono = 1_000_000_000_000
    self.offset = 50_000_000_000

  def boot(self):
    return self.mono + self.offset


class Messages:
  def __init__(self, clock):
    self.clock = clock
    self.messages = {}
    names = ("deviceState", "pandaStates", "peripheralState", "accelerometer")
    self.seen = dict.fromkeys(names, True)
    self.alive = dict.fromkeys(names, True)
    self.valid = dict.fromkeys(names, True)
    self.updated = dict.fromkeys(names, True)
    self.logMonoTime = dict.fromkeys(names, 0)
    self.recv_time = dict.fromkeys(names, 0.0)
    self.sock = {}
    self.voltage = 12_500

  def __getitem__(self, name):
    return self.messages[name]

  def update(self, _timeout):
    pass

  def set_sources(self, *, sample_stamp=None, acceleration=(0.0, 0.0, 9.81)):
    now = self.clock.mono
    for name in self.logMonoTime:
      self.recv_time[name] = (now - 10_000_000) / 1e9
      self.logMonoTime[name] = now - 10_000_000 if name in ("deviceState", "accelerometer") else self.clock.boot() - 10_000_000
      self.updated[name] = True
    self.messages["deviceState"] = NS(started=False, carBatteryCapacityUwh=1_000_000)
    self.messages["pandaStates"] = [NS(ignitionLine=False, ignitionCan=False, pandaType="uno")]
    self.messages["peripheralState"] = NS(pandaType="uno", voltage=self.voltage)
    # A real Cap'n Proto event is serialized and decoded before projection.
    event = messaging.new_message("accelerometer", valid=True)
    event.accelerometer.timestamp = sample_stamp if sample_stamp is not None else now - 20_000_000
    event.accelerometer.acceleration.v = list(acceleration)
    return event.to_bytes()


class TestRuntime(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.environment = patch.dict(os.environ, PARAMS_ROOT=self.directory.name, OPENPILOT_PREFIX="sentryruntime",
                                  STARPILOT_SENTRY_DEVELOPMENT="1")
    self.environment.start()
    self.addCleanup(self.environment.stop)
    self.params = Params()
    self.params.put_bool("IsOffroad", True, block=True)
    self.cutoff = effective_power(read_power(self.params)).cutoff_tenths
    Path(self.params.get_param_path(KEY)).write_bytes(encode(Preferences(True)))
    self.clock = Clock()
    self.messages = Messages(self.clock)
    self.events = []
    self.runtime = SentryRuntime(self.params, self.messages, mono_clock=lambda: self.clock.mono,
                                 boot_clock=self.clock.boot,
                                 record=lambda kind, stamp, permitted: self.events.append((kind, stamp, permitted())))

  def tick(self, *, stamp=None, acceleration=(0.0, 0.0, 9.81)):
    self.clock.mono += 100_000_000
    raw = self.messages.set_sources(sample_stamp=stamp, acceleration=acceleration)
    with log.Event.from_bytes(raw) as parsed:
      self.messages.messages["accelerometer"] = parsed.accelerometer
      return self.runtime.tick()

  def test_status_reports_actual_policy_and_immediate_sensor_revocation(self):
    status = Mock()
    self.runtime.status = status
    decision = self.tick()
    status.publish.assert_called_once_with(decision, encode(Preferences(True)), "idle", power=(125, self.cutoff))
    self.assertEqual(decision.state, "arming")
    self.messages.updated["accelerometer"] = False
    self.messages.valid["accelerometer"] = False
    self.clock.mono += 100_000_000
    decision = self.runtime.tick()
    self.assertEqual(decision.state, "sensor_unavailable")
    self.assertEqual(status.publish.call_args.args[0], decision)

  def test_serialized_sensor_and_fresh_parked_evidence(self):
    first = self.tick()
    self.assertEqual(first.state, "arming")
    self.assertIsNotNone(self.runtime.policy.last_sample_time)
    self.messages.updated["accelerometer"] = False
    self.clock.mono += 100_000_000
    self.assertEqual(self.runtime.tick().state, "arming")
    self.clock.mono += 400_000_000
    self.messages.set_sources()
    self.messages.updated["accelerometer"] = False
    self.assertEqual(self.runtime.tick().state, "sensor_unavailable")
    self.assertIsNone(self.runtime.policy.arm_started)
    self.assertEqual(self.events, [])

  def test_low_battery_warns_once_after_holding_near_cutoff_and_rearms_after_recovery(self):
    warnings = []
    self.runtime.battery = lambda voltage, cutoff: warnings.append((voltage, cutoff))
    cutoff = self.cutoff
    self.messages.voltage = (cutoff + 2) * 100
    for _ in range(599):
      self.tick()
    self.assertEqual(warnings, [])
    for _ in range(10):
      self.tick()
    self.assertEqual(warnings, [(cutoff + 2, cutoff)])
    for _ in range(700):
      self.tick()
    self.assertEqual(len(warnings), 1)
    self.messages.voltage = (cutoff + 4) * 100
    self.tick()
    self.messages.voltage = (cutoff - 1) * 100
    for _ in range(105):
      self.tick()
    self.assertEqual(warnings[-1], (cutoff - 1, cutoff))
    self.assertEqual(len(warnings), 2)

  def test_brief_voltage_dip_does_not_warn(self):
    warnings = []
    self.runtime.battery = lambda voltage, cutoff: warnings.append((voltage, cutoff))
    cutoff = self.cutoff
    for _ in range(20):
      self.messages.voltage = (cutoff + 1) * 100
      for _ in range(300):
        self.tick()
      self.messages.voltage = (cutoff + 3) * 100
      self.tick()
    self.assertEqual(warnings, [])

  def test_battery_warning_storage_failure_is_logged_but_bugs_propagate(self):
    self.messages.voltage = (self.cutoff - 1) * 100
    self.runtime.battery = Mock(side_effect=runtime_module.StorageUnavailable("Event storage is full"))
    for _ in range(101):
      self.tick()
    self.runtime.battery.assert_called_once()
    self.runtime.battery_warned = False
    self.runtime.battery = Mock(side_effect=ValueError("Invalid Sentry event"))
    with self.assertRaises(ValueError):
      for _ in range(101):
        self.tick()

  def test_recent_battery_warning_ignores_old_other_and_future_events(self):
    hour = runtime_module.BATTERY_REPEAT_NS
    now = 10 * hour
    self.assertTrue(runtime_module.recent_battery_warning([{"kind": "battery", "wallTimeNs": now - hour}], now))
    self.assertFalse(runtime_module.recent_battery_warning([{"kind": "battery", "wallTimeNs": now - hour - 1}], now))
    self.assertFalse(runtime_module.recent_battery_warning([{"kind": "warning", "wallTimeNs": now}], now))
    self.assertFalse(runtime_module.recent_battery_warning([{"kind": "battery", "wallTimeNs": now + 1}], now))

  def test_arming_completes_with_brief_gaps_between_sensor_messages(self):
    for _ in range(451):
      self.tick()
      self.messages.updated["accelerometer"] = False
      self.clock.mono += 100_000_000
      decision = self.runtime.tick()
      self.assertIn(decision.state, ("arming", "armed"))
    self.assertEqual(decision.state, "armed")
    self.assertEqual(self.events, [])

  def test_serialized_device_panda_peripheral_use_actual_clock_domains(self):
    self.clock.mono += 100_000_000
    sensor_raw = self.messages.set_sources()
    device = messaging.new_message("deviceState", valid=True)
    device.deviceState.started = False
    device.deviceState.carBatteryCapacityUwh = 1_000_000
    panda = messaging.new_message("pandaStates", 1, valid=True)
    panda.pandaStates[0].pandaType = "uno"
    peripheral = messaging.new_message("peripheralState", valid=True)
    peripheral.peripheralState.pandaType = "uno"
    peripheral.peripheralState.voltage = 12_500
    with ExitStack() as stack:
      readers = [stack.enter_context(log.Event.from_bytes(raw)) for raw in
                 (sensor_raw, device.to_bytes(), panda.to_bytes(), peripheral.to_bytes())]
      self.messages.messages.update(accelerometer=readers[0].accelerometer, deviceState=readers[1].deviceState,
                                    pandaStates=readers[2].pandaStates, peripheralState=readers[3].peripheralState)
      self.assertEqual(self.runtime.tick().state, "arming")
      # A C++ pandad BOOTTIME stamp incorrectly supplied in MONOTONIC units is stale.
      self.messages.logMonoTime["pandaStates"] = self.clock.mono - 10_000_000
      self.assertNotEqual(self.runtime.tick().state, "armed")

  def test_unknown_ignition_power_and_shutdown_disarm(self):
    self.tick()
    self.messages.messages["pandaStates"][0].ignitionCan = True
    self.assertEqual(self.runtime.tick().state, "disabled_ignition")
    self.messages.messages["pandaStates"][0].ignitionCan = False
    self.messages.messages["peripheralState"].voltage = 11_700
    self.assertEqual(self.runtime.tick().state, "low_voltage")
    self.messages.messages["peripheralState"].voltage = 12_500
    self.params.put_bool("DoShutdown", True, block=True)
    self.assertNotEqual(self.runtime.tick().state, "armed")
    self.assertEqual(self.events, [])

  def test_resume_barrier_rejects_queued_pre_resume_sensor(self):
    self.tick()
    old_stamp = self.messages.messages["accelerometer"].timestamp
    self.clock.offset += 100_000_000
    self.clock.mono += 100_000_000
    raw = self.messages.set_sources(sample_stamp=old_stamp)
    with log.Event.from_bytes(raw) as parsed:
      self.messages.messages["accelerometer"] = parsed.accelerometer
      self.assertNotEqual(self.runtime.tick().state, "armed")
    self.assertIsNone(self.runtime.policy.arm_started)
    self.assertEqual(self.events, [])
    self.assertEqual(self.tick().state, "arming")

  def test_revoked_settings_and_source_preserved(self):
    self.assertEqual(self.tick().state, "arming")
    raw = b'{"version":1,"enabled":true,"sensitivity":NaN,"warningTimeSeconds":1}'
    Path(self.params.get_param_path(KEY)).write_bytes(raw)
    self.assertEqual(self.tick().state, "disabled")
    self.assertEqual(Path(self.params.get_param_path(KEY)).read_bytes(), raw)
    self.assertEqual(self.events, [])

  def test_full_arming_to_warning_records_once_with_fresh_final_authority(self):
    for _ in range(901):
      self.tick()
    self.assertIsNotNone(self.runtime.policy.arm_started)
    for index in range(11):
      value = 10.0 if index % 2 else 9.81
      self.tick(acceleration=(0.0, 0.0, value))
    self.assertEqual(len(self.events), 1)
    self.assertEqual(self.events[0][0], "warning")
    self.assertTrue(self.events[0][2])
    self.assertEqual(self.runtime.recording_state, "durability_unknown")  # Stub supplies no durability receipt.
    self.params.put_bool("DoShutdown", True, block=True)
    self.assertNotEqual(self.tick().state, "armed")
    self.assertEqual(len(self.events), 1)

  def test_first_jolt_prewarms_cameras_before_the_warning(self):
    prewarms = []
    self.runtime.prewarm = lambda: prewarms.append(len(self.events))
    for _ in range(901):
      self.tick()
    self.tick()  # first armed sample only sets the baseline
    self.assertEqual(prewarms, [])
    self.tick(acceleration=(0.0, 0.0, 10.0))
    self.assertEqual(prewarms, [0])  # before any event is recorded
    for index in range(40):
      self.tick(acceleration=(0.0, 0.0, 9.81 if index % 2 == 0 else 10.0))
    self.assertEqual(prewarms, [0])
    self.assertEqual([event[0] for event in self.events], ["warning"])
    self.params.put_bool("DoShutdown", True, block=True)
    self.tick(acceleration=(0.0, 0.0, 9.81))
    self.assertEqual(prewarms, [0])

  def test_new_motion_window_prewarms_even_when_trigger_count_decreases(self):
    self.runtime.prewarm = Mock()
    for _ in range(902):
      self.tick()
    for index in range(5):
      self.tick(acceleration=(0.0, 0.0, 10.0 if index % 2 == 0 else 9.81))
    started = self.runtime.policy.trigger_started
    assert started is not None
    self.assertEqual(self.runtime.policy.trigger_count, 5)
    self.runtime.prewarm.assert_called_once()
    # Keep the sensors fresh and the policy armed up to the exact window boundary.
    while (self.clock.mono + 100_000_000) / 1e9 < started + RESET_TIME_SECONDS:
      self.tick(acceleration=(0.0, 0.0, 10.0))
    self.tick(acceleration=(0.0, 0.0, 9.81))
    self.assertEqual(self.runtime.policy.trigger_count, 1)
    self.assertNotEqual(self.runtime.policy.trigger_started, started)
    self.assertEqual(self.runtime.prewarm.call_count, 2)
    self.tick(acceleration=(0.0, 0.0, 10.0))
    self.assertEqual(self.runtime.prewarm.call_count, 2)

  def test_prewarm_rechecks_authority_after_first_jolt(self):
    self.runtime.prewarm = Mock()
    def revoke():
      Path(self.params.get_param_path(KEY)).write_bytes(encode(Preferences(False)))
    def ignition():
      self.messages.messages["pandaStates"][0].ignitionCan = True
    def low_voltage():
      self.messages.messages["peripheralState"].voltage = self.cutoff * 100
    def stale():
      self.messages.valid["deviceState"] = False
    for change in (revoke, ignition, low_voltage, stale):
      with self.subTest(change=change.__name__):
        Path(self.params.get_param_path(KEY)).write_bytes(encode(Preferences(True)))
        self.messages.valid["deviceState"] = True
        # Disarm between cases so each first jolt starts its own episode.
        self.params.put_bool("DoShutdown", True, block=True)
        self.tick()
        self.params.put_bool("DoShutdown", False, block=True)
        for _ in range(902):
          self.tick()
        # The first update supplies policy evidence, the final check sees revocation.
        updates = iter((lambda: None, change))
        with patch.object(self.messages, "update", side_effect=lambda _timeout, updates=updates: next(updates)()):
          self.tick(acceleration=(0.0, 0.0, 10.0))
        self.runtime.prewarm.assert_not_called()

  def test_prewarm_restarts_after_settings_change_disarm_and_resume(self):
    self.runtime.prewarm = Mock()
    for _ in range(902):
      self.tick()
    self.tick(acceleration=(0.0, 0.0, 10.0))
    self.runtime.prewarm.assert_called_once()
    for index, reset in enumerate(("settings", "disarm", "resume"), start=2):
      with self.subTest(reset=reset):
        if reset == "settings":
          Path(self.params.get_param_path(KEY)).write_bytes(encode(Preferences(True, Settings(0.05, 1.0))))
        elif reset == "disarm":
          self.params.put_bool("DoShutdown", True, block=True)
          self.tick()
          self.params.put_bool("DoShutdown", False, block=True)
        else:
          self.clock.offset += 100_000_000
        for _ in range(903):
          self.tick()
        self.assertIsNone(self.runtime.policy.trigger_started)
        self.tick(acceleration=(0.0, 0.0, 10.0))
        self.assertEqual(self.runtime.prewarm.call_count, index)

  def test_camera_prewarm_failure_shows_unavailable_and_keeps_watching(self):
    def prewarm():
      raise OSError("lease unavailable")
    self.runtime.prewarm = Mock(side_effect=prewarm)
    for _ in range(902):
      self.tick()
    with patch.object(runtime_module.cloudlog, "warning") as warning:
      self.assertEqual(self.tick(acceleration=(0.0, 0.0, 10.0)).state, "armed")
      self.assertEqual(self.tick(acceleration=(0.0, 0.0, 9.81)).state, "armed")
    self.assertEqual(self.runtime.recording_state, "unavailable")
    warning.assert_called_once()
    self.runtime.prewarm.assert_called_once()

  def test_store_callback_rechecks_saved_revision_and_sample_age(self):
    self.tick()
    self.runtime.policy.arm_started = self.clock.mono / 1e9 - 90
    self.runtime.policy.last_sample_time = self.clock.mono / 1e9 - 0.02
    original = Path(self.params.get_param_path(KEY)).read_bytes()
    self.assertTrue(self.runtime._permitted(original))
    Path(self.params.get_param_path(KEY)).write_bytes(encode(Preferences(True, self.runtime.policy.settings.__class__(0.05, 1.0))))
    self.assertFalse(self.runtime._permitted(original))
    Path(self.params.get_param_path(KEY)).write_bytes(original)
    self.clock.mono += 600_000_000
    self.assertFalse(self.runtime._permitted(original))


class TestMainShutdown(unittest.TestCase):
  def test_both_signals_close_owned_subscription_and_restore_handlers(self):
    from openpilot.starpilot.sentry_mode import storage
    for requested in (signal.SIGINT, signal.SIGTERM):
      with self.subTest(requested=requested):
        installed = {}
        prior = object()
        close = Mock()
        def install(number, handler, target=installed):
          target[number] = handler
        def tick(which=requested, target=installed):
          target[which](which, None)
        with patch.object(signal, "getsignal", return_value=prior), patch.object(signal, "signal", side_effect=install), \
             patch.object(messaging, "SubMaster"), patch.object(runtime_module, "Params"), \
             patch.object(storage, "EventStore"), patch.object(runtime_module, "SentryRuntime") as owner:
          owner.return_value.tick.side_effect = tick
          owner.return_value.close = close
          runtime_module.main()
        close.assert_called_once()
        self.assertIs(installed[signal.SIGINT], prior)
        self.assertIs(installed[signal.SIGTERM], prior)


if __name__ == "__main__":
  unittest.main()
