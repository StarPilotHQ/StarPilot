"""Real caller ordering and CEM transport after route68-sized configuration delays."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from unittest.mock import Mock, call, patch
import unittest

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot import feature_runtime
from openpilot.starpilot.speed_limits import planner_host as slc_host
from openpilot.starpilot.speed_limits.runtime_settings import read_params as read_slc_settings
from openpilot.selfdrive.controls import plannerd
from openpilot.starpilot.conditional_mode.tests import test_status_consumer as transport
from openpilot.starpilot.conditional_mode.tests.test_projection import BOOT, CP, MONO, FakeSubMaster, owner_context, serialized_scene
from openpilot.starpilot.conditional_mode.policy import ManualIntent
from openpilot.starpilot.conditional_mode.preferences import manual_for_drive
from openpilot.starpilot.conditional_mode.status import observation
from openpilot.starpilot.conditional_mode.effective_status import publish_ack
from openpilot.starpilot.ui.conditional_status import ConditionalDisplayProjector


class TestCemInputAcquisitionOrder(unittest.TestCase):
  def test_actual_live_caller_verifies_due_settings_before_collecting_batch(self):
    from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import candidate
    _, cp = candidate(alternate=True)
    class EndLoop(Exception):
      pass
    for delay_ns in (58_805_000, 87_000_000):
      with self.subTest(delay_ns=delay_ns), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        params = Params(directory)
        params.put('CarParams', cp.as_reader().as_builder().to_bytes(), block=True)
        params.put_bool('ShowSpeedLimits', True, block=True)
        params.put_bool('SpeedLimitController', False, block=True)
        lifecycle = plannerd.SavedSlcLifecycle
        clock = [MONO]
        order, captured = [], []
        class DelayedLifecycle(lifecycle):
          def refresh(inner, now_ns, *, order=order, params=params, clock=clock, delay_ns=delay_ns, **kwargs):
            if now_ns > 0:
              order.append('verify')
              # Genuine admission change also retires the old runtime and both
              # pending queues before the incoming input batch can be used.
              kwargs['pending_actions'].append(object())
              kwargs['pending_cruise'].append(object())
              params.put_bool('ShowSpeedLimits', False, block=True)
              clock[0] += delay_ns
            result = super().refresh(now_ns, **kwargs)
            if now_ns > 0:
              self.assertIsNone(result[0])
              self.assertTrue(result[1])
              self.assertFalse(kwargs['pending_actions'])
              self.assertFalse(kwargs['pending_cruise'])
            return result
        sm = Mock()
        def acquire(order=order, captured=captured, clock=clock):
          order.append('input')
          # Source timestamps represent a newly acquired model/CS/CC batch.
          # They are not rewritten from the old pre-verification batch.
          captured.append(FakeSubMaster(serialized_scene(stamp=BOOT + clock[0] - MONO), clock[0]))
          raise EndLoop
        sm.update.side_effect = acquire
        stack.enter_context(patch.dict(os.environ, {'CONDITIONAL_MODE_REPLAY_RUNTIME': '0',
          'SLC_REPLAY_RUNTIME': '0', 'CURVE_REPLAY_RUNTIME': '0', 'LONG_PLANNER_REPLAY_RUNTIME': '0'}))
        os.environ.pop('REPLAY', None)
        stack.enter_context(patch.object(plannerd, 'Params', return_value=params))
        stack.enter_context(patch.object(plannerd, 'SavedSlcLifecycle', DelayedLifecycle))
        stack.enter_context(patch.object(plannerd.time, 'monotonic_ns', side_effect=lambda clock=clock: clock[0]))
        stack.enter_context(patch.object(plannerd, 'config_realtime_process'))
        stack.enter_context(patch.object(plannerd, 'set_core_affinity'))
        stack.enter_context(patch.object(messaging, 'PubMaster'))
        stack.enter_context(patch.object(messaging, 'SubMaster', return_value=sm))
        stack.enter_context(patch.object(messaging, 'sub_sock'))
        with self.assertRaises(EndLoop):
          plannerd.starpilot_main()
        self.assertEqual(order, ['verify', 'input'])
        self.assertEqual(captured[0].logMonoTime['modelV2'], MONO + delay_ns)
        self.assertEqual(captured[0].logMonoTime['carState'], MONO + delay_ns)
        self.assertEqual(captured[0].logMonoTime['carControl'], MONO + delay_ns)
        self.assertEqual(captured[0]['modelV2'].timestampEof, BOOT + delay_ns)

  def test_due_configuration_uses_one_canonical_snapshot_and_unchanged_qualification(self):
    from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import candidate
    _, cp = candidate(alternate=True)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('ShowSpeedLimits', True, block=True)
      params.put_bool('SpeedLimitController', True, block=True)
      params.put('SLCPriority1', 'Vision', block=True)
      params.put('SLCPriority2', 'Dashboard', block=True)
      owner = plannerd.SavedSlcLifecycle(params, cp, {})
      expected = (feature_runtime.enabled(params, cp, 'slc', {}),
        feature_runtime.slc_runtime_settings(params, cp, {}),
        feature_runtime.enabled(params, cp, 'vision', {}),
        feature_runtime.vision_control_enabled(params, cp),
        feature_runtime.enabled(params, cp, 'vision', {}), params.get_bool('IsMetric'))
      reads = []
      def read(params):
        reads.append(True)
        return read_slc_settings(params)
      with patch.object(slc_host, 'read_slc_settings', side_effect=read), \
           patch.object(feature_runtime, 'read_slc_settings', side_effect=read):
        runtime, changed = owner.refresh(MONO)
        self.assertTrue(changed)
        self.assertIsNotNone(runtime)
        self.assertEqual(owner.configuration, expected)
        self.assertEqual(len(reads), 1)
        self.assertEqual(owner.refresh(MONO+50_000_000), (runtime, False))
        self.assertEqual(len(reads), 1)
        # Errors and disabled requests still retire the runtime; the single
        # read is actual Params parsing, not a fabricated feature capability.
        Path(params.get_param_path('SLCPriority1')).write_bytes(b'invalid')
        retired, changed = owner.refresh(MONO+1_000_000_000)
        self.assertIsNone(retired)
        self.assertTrue(changed)
        self.assertEqual(len(reads), 2)
        assert owner.configuration is not None
        self.assertTrue(owner.configuration[1].errors)

  def test_explicit_replay_does_not_perform_live_clock_settings_verification(self):
    from opendbc.car.honda.interface import CarInterface
    from opendbc.car.honda.values import CAR
    class EndLoop(Exception):
      pass
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
      params = Params(directory)
      params.put('CarParams', cp.as_reader().as_builder().to_bytes(), block=True)
      sm = Mock()
      sm.update.side_effect = EndLoop
      stack.enter_context(patch.dict(os.environ, {'REPLAY': '1', 'CONDITIONAL_MODE_REPLAY_RUNTIME': '0',
        'SLC_REPLAY_RUNTIME': '0', 'CURVE_REPLAY_RUNTIME': '0', 'LONG_PLANNER_REPLAY_RUNTIME': '0'}))
      stack.enter_context(patch.object(plannerd, 'Params', return_value=params))
      refresh = stack.enter_context(patch.object(plannerd.SavedSlcLifecycle, 'refresh', return_value=(None, False)))
      stack.enter_context(patch.object(plannerd, 'config_realtime_process'))
      stack.enter_context(patch.object(plannerd, 'set_core_affinity'))
      stack.enter_context(patch.object(messaging, 'PubMaster'))
      stack.enter_context(patch.object(messaging, 'SubMaster', return_value=sm))
      stack.enter_context(patch.object(messaging, 'sub_sock'))
      with self.assertRaises(EndLoop):
        plannerd.starpilot_main()
      self.assertEqual(refresh.call_args_list, [call(0)])


class TestCemDelayedBatchTransport(unittest.TestCase):
  def setUp(self):
    self.fixture = transport.TestModeTransport(methodName='runTest')
    self.fixture.setUp()
    self.addCleanup(self.fixture.doCleanups)
    self.scene = {'speed': 10.0, 'horizon': 40.0, 'lead_present': False}
    for tick in range(1, 31):
      now = MONO + tick * 50_000_000
      event, _, _ = self.fixture.produce(now, intent=ManualIntent.NONE, scene=self.scene,
                                         context=owner_context(now-2_000_000))
      self.fixture.consume(event, now)
    self.assertTrue(self.fixture.host.projector.stop_detector.committed)

  def test_old_batch_after_configuration_delay_rejects_and_does_not_clear_stop_commit(self):
    source = MONO + 31*50_000_000
    for delay in (58_805_000, 87_000_000):
      with self.subTest(delay=delay):
        now = source + delay
        sm = FakeSubMaster(serialized_scene(stamp=BOOT+source-MONO, **self.scene), source)
        result = self.fixture.host.sample(sm, CP, now_mono_ns=now, now_boot_ns=BOOT+now-MONO,
          sample_skew_ns=1000, drive_id=self.fixture.drive_id, settings_owner=self.fixture.owner,
          manual=manual_for_drive(ManualIntent.NONE, self.fixture.drive_id, self.fixture.drive_id+1),
          owner_context=owner_context(source), selected_t_follow_s=1.45,
          selected_t_follow_observed_mono_ns=source)
        self.assertIsNone(result.projected.authority)
        self.assertIsNone(result.override_experimental)
        self.assertEqual(result.status, 'authority_unavailable')
        self.assertTrue(self.fixture.host.projector.stop_detector.committed)

  def test_configuration_then_fresh_batch_keeps_actual_stop_ack_and_display(self):
    for delay in (58_805_000, 87_000_000):
      with self.subTest(delay=delay):
        now = MONO + 32*50_000_000 + delay
        event, proposal, sm = self.fixture.produce(now, intent=ManualIntent.NONE, scene=self.scene,
                                                  context=owner_context(now-2_000_000))
        model = sm['modelV2'].as_builder().to_bytes()
        model_stamp = sm.logMonoTime['modelV2']
        self.assertEqual(proposal.decision.reason.value, 'cem_stop')
        self.assertTrue(proposal.override_experimental)
        self.assertTrue(self.fixture.host.projector.stop_detector.committed)
        result = self.fixture.consume(event, now)
        self.assertTrue(result.accepted)
        self.assertTrue(result.experimental)
        # The actual publisher/consumer read the acquired model, without a
        # second SubMaster update after the native plan replacing that source.
        self.assertEqual(sm['modelV2'].as_builder().to_bytes(), model)
        self.assertEqual(sm.logMonoTime['modelV2'], model_stamp)
        wire = observation(event.slcState, now)
        self.assertIsNotNone(wire)
        assert wire is not None
        self.assertEqual(wire.model_ns, model_stamp)
        ack = publish_ack(session='a'*32, sequence=1, observed_ns=now,
          selfdrive_state_ns=now-1_000_000, drive_id=self.fixture.drive_id,
          effective_experimental=result.experimental, result=result, accepted_proposal=wire)
        displayed = ConditionalDisplayProjector().project(ack.starpilotSelfdriveState, now_ns=now,
          event_ns=now, selfdrive_ns=now-1_000_000, drive_id=self.fixture.drive_id,
          selfdrive_experimental=True, selfdrive_enabled=True, long_active=True, car_valid=True, system_long=True)
        self.assertIsNotNone(displayed)
        self.assertEqual(displayed.reason, 'cem_stop')
        self.assertTrue(displayed.effective_experimental)

  def test_expired_model_and_explicit_authority_loss_still_reject(self):
    now = MONO + 35*50_000_000
    event, _, _ = self.fixture.produce(now, intent=ManualIntent.NONE, scene=self.scene,
                                     context=owner_context(now-2_000_000))
    self.assertFalse(self.fixture.consume(event, now+151_000_000,
      model_ns=now+150_000_000, car_state_ns=now+150_000_000,
      message_ns=now, receipt_ns=now).accepted)
    event, _, _ = self.fixture.produce(now+200_000_000, intent=ManualIntent.NONE, scene=self.scene,
                                     context=owner_context(now+198_000_000))
    self.assertFalse(self.fixture.consume(event, now+200_000_000, authority=False).accepted)


class TestCemSlcSnapshotCompatibility(unittest.TestCase):
  def test_single_snapshot_preserves_display_stock_replay_and_parse_errors(self):
    from opendbc.car.gm.tests.test_bolt_cc import params as bolt_params
    from opendbc.car.gm.values import CAR as GM_CAR
    from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import candidate
    _, ioniq = candidate(alternate=True)
    stock = bolt_params(GM_CAR.CHEVROLET_BOLT_CC_2018_2021, alpha=True, present=True)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      for raw in (b'Vision', b'Dashboard', b'Map Data', b'invalid'):
        params.put_bool('SpeedLimitController', True, block=True)
        params.put_bool('ShowSpeedLimits', True, block=True)
        Path(params.get_param_path('SLCPriority1')).write_bytes(raw)
        for cp in (ioniq, stock):
          for environment in ({}, {'SLC_REPLAY_RUNTIME': '1', 'SLC_VISION_DEVELOPMENT': '1'}):
            with self.subTest(raw=raw, fingerprint=cp.carFingerprint, environment=environment):
              saved = read_slc_settings(params)
              self.assertEqual(feature_runtime.requested(params, 'slc', saved_slc=saved),
                               feature_runtime.requested(params, 'slc'))
              self.assertEqual(feature_runtime.enabled(params, cp, 'slc', environment, saved_slc=saved),
                               feature_runtime.enabled(params, cp, 'slc', environment))
              self.assertEqual(feature_runtime.enabled(params, cp, 'vision', environment, saved_slc=saved),
                               feature_runtime.enabled(params, cp, 'vision', environment))
              self.assertEqual(feature_runtime.slc_runtime_settings(params, cp, environment, saved_slc=saved),
                               feature_runtime.slc_runtime_settings(params, cp, environment))
              self.assertEqual(feature_runtime.vision_control_enabled(params, cp, saved_slc=saved),
                               feature_runtime.vision_control_enabled(params, cp))
