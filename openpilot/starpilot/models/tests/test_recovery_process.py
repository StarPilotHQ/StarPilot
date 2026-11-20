from types import SimpleNamespace as NS
from itertools import product
from unittest.mock import Mock, patch
import unittest

from openpilot.starpilot.models.recovery_process import ModeldProcess, modeld_launcher, SMALL_ONLY_ENV
from openpilot.starpilot.models.tests.test_recovery import BASE
from openpilot.starpilot.models.status import ModelVariant


class Feed(dict):
  def __init__(self, t, output=True):
    now = BASE + int(t * 1e9)
    super().__init__(deviceState=NS(startedMonoTime=BASE), modelV2=NS(big=True),
                     narrowRoadCameraState=NS(frameId=int(t * 20)), wideRoadCameraState=NS(frameId=int(t * 20)))
    self.seen = dict.fromkeys(('deviceState', 'modelV2', 'drivingModelData', 'narrowRoadCameraState', 'wideRoadCameraState'), True)
    self.valid = self.seen.copy()
    self.valid.update(modelV2=output, drivingModelData=output)
    self.alive = self.seen.copy()
    self.logMonoTime = dict.fromkeys(self.seen, now)
    if not output:
      self.logMonoTime.update(modelV2=BASE + 2_000_000_000, drivingModelData=BASE + 2_000_000_000)


class TestRecoveryProcess(unittest.TestCase):
  def test_resident_preload_watchdog_kills_before_isolated_restart(self):
    from openpilot.starpilot.models.conditional_protocol import PRELOAD_TIMEOUT_NS
    owner = ModeldProcess('modeld', 'module', lambda *args: True)
    child = Mock(pid=123)
    child.is_alive.return_value = True
    owner.proc, owner.launch_conditional, owner.launch_ns = child, True, BASE
    elapsed = PRELOAD_TIMEOUT_NS / 1e9 + .1
    with patch('openpilot.starpilot.models.recovery_process.process_start_ticks', return_value=456), \
         patch('openpilot.starpilot.models.recovery_process.read_receipt', return_value=None), \
         patch('openpilot.starpilot.models.recovery_process.read_pair_receipt', return_value=None), \
         patch('openpilot.starpilot.models.recovery_process.time.monotonic_ns', return_value=BASE + int(elapsed * 1e9)), \
         patch.object(owner, 'signal') as send, patch('openpilot.starpilot.models.recovery_process.Process') as spawn:
      owner.observe(Feed(elapsed), True)
      assert owner.recovery.small_only and owner.recovery.phase == 'sigint'
      owner.start()
      spawn.assert_not_called()
      send.assert_called_once()

  def test_native_handoff_stall_requires_held_ack_and_fresh_cameras(self):
    for held, camera_count, progressing, source_valid in product((False, True), (1, 2), (False, True), (False, True)):
      owner = ModeldProcess('modeld', 'module', lambda *args: True)
      child = Mock(pid=123)
      child.is_alive.return_value = True
      owner.proc = child
      previous = (BASE + 4_000_000_000, 80) if progressing else (BASE + 5_000_000_000, 100)
      owner.last_conditional_cameras = (previous,) * camera_count
      feed = Feed(5., output=False)
      if camera_count == 1:
        feed.seen['wideRoadCameraState'] = False
      feed['conditionalModelState'] = NS(processPid=123, processStartTicks=456, session='session', requestId='request',
                                 phase='switching', sessionStartMonoTime=BASE, frameMonoTime=0, fallbackModel='cheddar')
      feed['conditionalModelAck'] = NS(session='session', requestId='request', holding=held,
                               stateMonoTime=BASE + 4_900_000_000 if source_valid else BASE - 1)
      feed.seen.update(conditionalModelState=True, conditionalModelAck=True)
      feed.valid.update(conditionalModelState=True, conditionalModelAck=True)
      # Status may still be publishing even when no valid model frames emerge.
      feed.logMonoTime.update(conditionalModelState=BASE + 5_000_000_000, conditionalModelAck=BASE + 5_000_000_000)
      load = NS(pid=123, process_start_ticks=456, loaded_mono_ns=BASE, variant=ModelVariant.SMALL)
      with patch('openpilot.starpilot.models.recovery_process.process_start_ticks', return_value=456), \
           patch('openpilot.starpilot.models.recovery_process.read_receipt', return_value=load), \
           patch('openpilot.starpilot.models.recovery_process.read_pair_receipt', return_value=None), \
           patch('openpilot.starpilot.models.recovery_process.time.monotonic_ns', return_value=BASE + 5_000_000_000), \
           patch.object(owner, 'signal') as send:
        owner.observe(feed, True)
      assert send.called == (held and progressing and source_valid)
      if held and source_valid:
        assert owner.conditional_recovery_model == 'cheddar'

  def test_crash_after_ready_before_hold_release_recovers_previous_model_after_confirmed_exit(self):
    owner = ModeldProcess('modeld', 'module', lambda *args: True)
    child = Mock(pid=123, exitcode=-9)
    child.is_alive.return_value = False
    owner.proc, owner.launch_conditional, owner.launch_ticks, owner.launch_drive_id = child, True, 456, BASE
    feed = Feed(5.)
    feed['conditionalModelState'] = NS(processPid=123, processStartTicks=456, session='session', requestId='request',
                               phase='ready', sessionStartMonoTime=BASE, frameMonoTime=0, fallbackModel='cheddar')
    feed['conditionalModelAck'] = NS(session='session', requestId='request', holding=True, stateMonoTime=BASE + 4_900_000_000)
    feed.seen.update(conditionalModelState=True, conditionalModelAck=True)
    feed.valid.update(conditionalModelState=True, conditionalModelAck=True)
    feed.logMonoTime.update(conditionalModelState=BASE + 4_900_000_000, conditionalModelAck=BASE + 5_000_000_000)
    load = NS(pid=123, process_start_ticks=456, loaded_mono_ns=BASE, variant=ModelVariant.SMALL)
    with patch('openpilot.starpilot.models.recovery_process.read_receipt', return_value=load), \
         patch('openpilot.starpilot.models.recovery_process.read_pair_receipt', return_value=None), \
         patch('openpilot.starpilot.models.recovery_process.time.monotonic_ns', return_value=BASE + 5_000_000_000), \
         patch('openpilot.starpilot.models.recovery_process.process_start_ticks', return_value=789), \
         patch('openpilot.starpilot.models.recovery_process.Process') as spawn:
      owner.observe(feed, True)
      child.join.assert_called_once_with(timeout=0)
      assert owner.proc is None and owner.recovery.small_only
      owner.start()
      assert spawn.call_args.kwargs['kwargs'] == {'recovery_model': 'cheddar'}

  def test_established_runner_never_becomes_a_preload_timeout_when_receipt_is_lost(self):
    owner = ModeldProcess('modeld', 'module', lambda *args: True)
    child = Mock(pid=123)
    child.is_alive.return_value = True
    owner.proc, owner.launch_conditional, owner.launch_ns = child, True, BASE
    feed = Feed(121.)
    now = BASE + 121_000_000_000
    feed['conditionalModelState'] = NS(processPid=123, processStartTicks=456, sessionStartMonoTime=BASE, frameMonoTime=now)
    feed.seen['conditionalModelState'] = feed.valid['conditionalModelState'] = True
    feed.logMonoTime['conditionalModelState'] = now
    with patch('openpilot.starpilot.models.recovery_process.process_start_ticks', return_value=456), \
         patch('openpilot.starpilot.models.recovery_process.read_receipt', return_value=None), \
         patch('openpilot.starpilot.models.recovery_process.read_pair_receipt', return_value=None), \
         patch('openpilot.starpilot.models.recovery_process.time.monotonic_ns', return_value=now), \
         patch.object(owner, 'signal') as send:
      owner.observe(feed, True)
      assert owner.launch_established
      del feed['conditionalModelState']
      owner.observe(feed, True)
      send.assert_not_called()
    assert not owner.recovery.small_only

  def test_established_native_crash_does_not_restart_a_different_model_while_engaged(self):
    owner = ModeldProcess('modeld', 'module', lambda *args: True)
    child = Mock(pid=123, exitcode=-9)
    child.is_alive.return_value = False
    owner.proc, owner.launch_conditional, owner.launch_ticks, owner.launch_drive_id = child, True, 456, BASE
    load = NS(pid=123, process_start_ticks=456, loaded_mono_ns=BASE, variant=ModelVariant.SMALL)
    with patch('openpilot.starpilot.models.recovery_process.read_receipt', return_value=load), \
         patch('openpilot.starpilot.models.recovery_process.read_pair_receipt', return_value=None), \
         patch('openpilot.starpilot.models.recovery_process.time.monotonic_ns', return_value=BASE + 2_000_000_000):
      owner.observe(Feed(2.), True)
    assert owner.proc is child and not owner.recovery.small_only

  def test_adapter_never_spawns_before_confirmed_exit(self):
    owner = ModeldProcess('modeld', 'openpilot.selfdrive.modeld.modeld', lambda *args: True)
    child = Mock(pid=123)
    child.is_alive.return_value = True
    owner.proc = child
    load = NS(pid=123, process_start_ticks=456, loaded_mono_ns=BASE, variant=ModelVariant.CHESTNUT)
    with patch('openpilot.starpilot.models.recovery_process.process_start_ticks', return_value=456), \
         patch('openpilot.starpilot.models.recovery_process.read_receipt', return_value=load), \
         patch('openpilot.starpilot.models.recovery_process.time.monotonic_ns') as clock, \
         patch.object(owner, 'signal') as send, \
         patch('openpilot.starpilot.models.recovery_process.Process') as spawn:
      for t in (.1, .6, 1.1, 1.6, 2., 3., 4., 4.5):
        clock.return_value = BASE + int(t * 1e9)
        owner.observe(Feed(t, output=t <= 2.), True)
        owner.start()
      self.assertEqual(send.call_count, 2)
      spawn.assert_not_called()
      child.is_alive.return_value = False
      clock.return_value = BASE + 4_600_000_000
      owner.observe(Feed(4.6, output=False), True)
      child.join.assert_called_once_with(timeout=0)
      owner.start()
      spawn.assert_called_once()
      self.assertEqual(spawn.call_args.kwargs['args'][-1], True)
      spawn.return_value.start.assert_called_once()
      owner.start()
      spawn.assert_called_once()

  def test_offroad_teardown_never_uses_generic_stop_or_waits(self):
    owner = ModeldProcess('modeld', 'module', lambda *args: True)
    child = Mock(pid=123)
    child.is_alive.return_value = True
    owner.proc = child
    owner.recovery.phase = 'sigint'
    with patch('openpilot.starpilot.models.recovery_process.PythonProcess.stop') as generic:
      self.assertIsNone(owner.stop(block=False))
      generic.assert_not_called()
      child.join.assert_not_called()

  def test_final_cleanup_kills_with_bounded_wait_and_reports_uncertain_exit(self):
    for exits in (False, True):
      owner = ModeldProcess('modeld', 'module', lambda *args: True)
      child = Mock(pid=123, exitcode=-9)
      child.is_alive.side_effect = [True, not exits]
      owner.proc = child
      owner.recovery.phase = 'sigint'
      with patch.object(owner, 'signal') as send, patch('openpilot.starpilot.models.recovery_process.PythonProcess.stop') as generic, \
           patch('openpilot.starpilot.models.recovery_process.join_process') as wait:
        result = owner.stop(block=True)
        generic.assert_not_called()
        send.assert_called_once()
        wait.assert_called_once_with(child, 1.0)
        self.assertTrue(all(call.kwargs['timeout'] == 0 for call in child.join.call_args_list))
        self.assertEqual(result, -9 if exits else None)
        self.assertEqual(owner.recovery.phase, 'sigint' if exits else 'failed')
        self.assertEqual(owner.proc is None, exits)

  def test_existing_exit_wait_polls_without_multiprocessing_join(self):
    from openpilot.system.manager.process import join_process
    child = Mock(exitcode=None)
    clock = NS(monotonic=Mock(side_effect=[0., 0., .5, 1.]), sleep=Mock())
    with patch('openpilot.system.manager.process.time', clock):
      join_process(child, 1.)
    self.assertEqual(clock.sleep.call_count, 2)
    child.join.assert_not_called()
    child.exitcode = -9
    clock = NS(monotonic=Mock(side_effect=[0., 0.]), sleep=Mock())
    with patch('openpilot.system.manager.process.time', clock):
      join_process(child, 1.)
    clock.sleep.assert_not_called()
    child.join.assert_not_called()

  def test_invalid_device_context_cannot_arm_or_start(self):
    owner = ModeldProcess('modeld', 'module', lambda *args: True)
    feed = Feed(2.)
    feed.valid['deviceState'] = False
    with patch('openpilot.starpilot.models.recovery_process.Process') as spawn:
      owner.observe(feed, True)
      owner.start()
      spawn.assert_not_called()
      self.assertFalse(owner.recovery.armed)

  def test_child_environment_only_and_before_import(self):
    with patch('openpilot.starpilot.models.recovery_process.os.environ', {}) as environment, \
         patch('openpilot.starpilot.models.recovery_process.launcher') as launch:
      modeld_launcher('module', 'modeld', True)
      self.assertEqual(environment[SMALL_ONLY_ENV], '1')
      launch.assert_called_once_with('module', 'modeld')
      modeld_launcher('module', 'modeld', False)
      self.assertNotIn(SMALL_ONLY_ENV, environment)
