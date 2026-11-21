"""Mixed publisher-clock settings authority tests."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import (
  LiveContextSource,
)


class FakeMessages:
  def __init__(self, mono_now, boot_now):
    self.mono_now = mono_now
    self.boot_now = boot_now
    self.updated = {"deviceState": True, "pandaStates": True}
    self.seen = {"deviceState": True, "pandaStates": True}
    self.alive = {"deviceState": True, "pandaStates": True}
    self.valid = {"deviceState": True, "pandaStates": True}
    self.logMonoTime = {"deviceState": mono_now - 50_000_000, "pandaStates": boot_now - 50_000_000}
    self.recv_time = {"deviceState": (mono_now - 40_000_000) / 1e9,
                      "pandaStates": (mono_now - 40_000_000) / 1e9}
    self.device = SimpleNamespace(started=False)
    self.pandas = [SimpleNamespace(ignitionLine=False, ignitionCan=False)]
    self.calls = []

  def update(self, timeout):
    self.calls.append(timeout)

  def __getitem__(self, name):
    return self.device if name == "deviceState" else self.pandas


class LiveContextTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.params.put_bool("IsOffroad", True, block=True)
    self.mono = 10_000_000_000
    self.boot = 20_000_000_000
    self.wait_now = 0.0
    self.messages = FakeMessages(self.mono, self.boot)
    self.source = LiveContextSource(self.params, self.messages, mono_clock=lambda: self.mono,
                                    boot_clock=lambda: self.boot, evidence_wait_ms=0)
    self.mono += 100_000_000
    self.boot += 100_000_000
    self.messages.logMonoTime["deviceState"] = self.mono - 20_000_000
    self.messages.logMonoTime["pandaStates"] = self.boot - 20_000_000
    self.messages.recv_time = {"deviceState": (self.mono - 10_000_000) / 1e9,
                               "pandaStates": (self.mono - 10_000_000) / 1e9}

  def queued_messages(self, on_panda=None):
    messages = FakeMessages(self.mono, self.boot)
    messages.seen = {"deviceState": False, "pandaStates": False}
    messages.updated = {"deviceState": False, "pandaStates": False}
    events = iter((None, "deviceState", "pandaStates"))

    def update(timeout):
      messages.calls.append(timeout)
      self.wait_now += timeout / 1000
      event = next(events)
      messages.updated = {"deviceState": False, "pandaStates": False}
      if event is None:
        return
      self.mono += 100_000_000
      self.boot += 100_000_000
      messages.seen[event] = messages.updated[event] = True
      messages.valid[event] = True
      messages.logMonoTime[event] = (self.mono if event == "deviceState" else self.boot) - 1_000_000
      messages.recv_time[event] = self.mono / 1e9
      if event == "pandaStates" and on_panda is not None:
        on_panda(messages)

    messages.update = mock.Mock(side_effect=update)
    return messages

  def test_actual_clock_domains_and_suspend_barrier(self):
    self.assertTrue(self.source.sample().parked)
    self.messages.updated["deviceState"] = False
    self.boot += 1_600_000_000
    self.assertFalse(self.source.sample().parked)  # Mono stamp ages against boot on resume.
    # Pre-resume frames stay blocked.
    self.messages.updated["deviceState"] = True
    self.assertFalse(self.source.sample().parked)
    self.mono += 30_000_000
    self.boot += 30_000_000
    self.messages.logMonoTime["deviceState"] = self.mono - 1_000_000
    self.messages.logMonoTime["pandaStates"] = self.boot - 1_000_000
    self.messages.recv_time = {"deviceState": self.mono / 1e9, "pandaStates": self.mono / 1e9}
    self.assertTrue(self.source.sample().parked)
    self.boot -= 1_600_000_000
    self.messages.logMonoTime["pandaStates"] = self.mono - 50_000_000  # Wrong publisher domain.
    self.assertFalse(self.source.sample().parked)

  def test_startup_queued_frame_and_unbounded_clock_pair(self):
    # Queued frames predate this source.
    source = LiveContextSource(self.params, self.messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, evidence_wait_ms=0)
    self.assertFalse(source.sample().parked)
    self.mono += 20_000_000
    self.boot += 20_000_000
    self.messages.logMonoTime["deviceState"] = self.mono - 1_000_000
    self.messages.logMonoTime["pandaStates"] = self.boot - 1_000_000
    self.messages.recv_time = {"deviceState": self.mono / 1e9, "pandaStates": self.mono / 1e9}
    self.assertTrue(source.sample().parked)
    from itertools import chain, repeat
    readings = chain((self.mono, self.mono, self.mono + 6_000_000), repeat(self.mono + 6_000_000))
    jittered = LiveContextSource(self.params, self.messages, mono_clock=lambda: next(readings),
                                 boot_clock=lambda: self.boot, evidence_wait_ms=0)
    self.assertFalse(jittered.sample().parked)

  def test_manager_device_and_panda_authority(self):
    self.assertTrue(self.source.sample().parked)
    self.params.put_bool("IsOffroad", False, block=True)
    self.assertFalse(self.source.sample().parked)
    self.params.put_bool("IsOffroad", True, block=True)
    self.messages.pandas[0].ignitionCan = True
    self.assertTrue(self.source.sample().parked)  # Offroad permits config with ignition.
    self.assertTrue(self.source.parked())       # Operations use effective offroad.
    self.messages.pandas[0].ignitionCan = False
    self.messages.device.started = True
    self.assertFalse(self.source.sample().parked)

  def test_first_parked_sample_waits_for_both_live_publishers(self):
    messages = self.queued_messages()
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, wait_clock=lambda: self.wait_now)
    self.assertTrue(source.parked())
    self.assertEqual(messages.calls[0], 0)
    self.assertEqual(len(messages.calls), 3)
    self.assertTrue(all(0 < timeout <= 50 for timeout in messages.calls[1:]))

  def test_wait_never_admits_missing_or_known_unsafe_evidence(self):
    messages = FakeMessages(self.mono, self.boot)
    messages.device.started = True
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot)
    self.assertFalse(source.parked())
    self.assertEqual(messages.calls, [0])
    messages.pandas[0].ignitionCan = False
    messages.device.started = True
    self.assertFalse(source.parked())
    self.assertEqual(messages.calls, [0, 0])

    messages.device.started = False
    messages.seen["pandaStates"] = False
    def wait_update(timeout):
      messages.calls.append(timeout)
      self.wait_now += timeout / 1000
    messages.update = mock.Mock(side_effect=wait_update)
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
      boot_clock=lambda: self.boot, evidence_wait_ms=20,
      wait_clock=lambda: self.wait_now)
    self.assertFalse(source.parked())
    self.assertEqual(messages.calls[2], 0)
    self.assertTrue(any(timeout > 0 for timeout in messages.calls[3:]))

  def test_wait_rechecks_manager_offroad_before_admission(self):
    messages = self.queued_messages(lambda _: self.params.put_bool("IsOffroad", False, block=True))
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, wait_clock=lambda: self.wait_now)
    self.assertFalse(source.parked())
    self.assertEqual(len(messages.calls), 3)

  def test_wait_allows_ignition_when_effective_state_is_offroad(self):
    messages = self.queued_messages(lambda m: setattr(m.pandas[0], "ignitionLine", True))
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, wait_clock=lambda: self.wait_now)
    self.assertTrue(source.parked())
    self.assertEqual(len(messages.calls), 3)

  def test_wait_rejects_stale_invalid_then_accepts_new_valid_frames(self):
    messages = self.queued_messages()
    messages.seen = {"deviceState": True, "pandaStates": True}
    messages.valid["deviceState"] = False
    messages.logMonoTime = {"deviceState": self.mono - 2_000_000_000,
                            "pandaStates": self.boot - 2_000_000_000}
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, wait_clock=lambda: self.wait_now)
    self.assertTrue(source.parked())
    self.assertEqual(len(messages.calls), 3)

  def test_wait_requires_post_resume_publisher_frames(self):
    messages = self.messages
    self.assertTrue(self.source.parked())
    self.boot += 1_600_000_000
    events = iter((None, "deviceState", "pandaStates"))

    def update(timeout):
      messages.calls.append(timeout)
      self.wait_now += timeout / 1000
      event = next(events)
      messages.updated = {"deviceState": False, "pandaStates": False}
      if event is None:
        return
      self.mono += 100_000_000
      self.boot += 100_000_000
      messages.updated[event] = True
      messages.logMonoTime[event] = (self.mono if event == "deviceState" else self.boot) - 1_000_000
      messages.recv_time[event] = self.mono / 1e9

    messages.update = mock.Mock(side_effect=update)
    self.source.evidence_wait_ms = 750
    self.source.wait_clock = lambda: self.wait_now
    self.assertTrue(self.source.parked())
    self.assertEqual(messages.calls[-3:], [0, 50, 50])

  def test_actual_verified_cp_envelope(self):
    from opendbc.car.structs import car
    from openpilot.starpilot.schema_cache import put_cache
    cp = car.CarParams.new_message()
    cp.carFingerprint = "TOYOTA COROLLA TSS2"
    cp.brand = "toyota"
    cp.openpilotLongitudinalControl = True
    put_cache(self.params, "CarParamsPersistent", cp, block=True)
    snapshot = self.source.sample()
    self.assertTrue(snapshot.parked)
    self.assertEqual(snapshot.cp.carFingerprint, "TOYOTA COROLLA TSS2")
    path = Path(self.params.get_param_path("CarParamsPersistent"))
    self.assertTrue(snapshot.cp_raw.endswith(path.read_bytes()))
    self.assertTrue(snapshot.cp_raw.startswith(b"settings-configuration:"))
    path.write_bytes(b"not a versioned cache")
    self.assertIsNone(self.source.sample().cp)
    self.assertEqual(path.read_bytes(), b"not a versioned cache")
