"""Camera sign selection remains separate from vehicle speed authority."""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.speed_limits import planner_host as slc_host
from openpilot.starpilot.feature_runtime import enabled, slc_runtime_settings, vision_control_enabled
from openpilot.starpilot.speed_limits.runtime import Action, Runtime
from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM
from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID
from openpilot.starpilot.speed_limits.vision_gate import diagnostic_choice_enabled
from openpilot.starpilot.tests.test_gm_feature_runtime import LONG_CASES, STOCK_CASES, configured
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change
from opendbc.car.gm.tests.test_ascm_intercept import params as ordinary_params
from opendbc.car.gm.values import CAR, ORDINARY_ASCM_CAR, ORDINARY_SDGM_CAR


class TestGmVisionSource(unittest.TestCase):
  @staticmethod
  def requests(params, control):
    params.put_bool('SpeedLimitController', control, block=True)
    params.put_bool('ShowSpeedLimits', True, block=True)
    params.put('SLCPriority1', 'Vision', block=True)
    params.put('SLCPriority2', 'Dashboard', block=True)

  @staticmethod
  def bus():
    data, _ = messages()
    data['carState'].vCruise = data['carState'].vCruiseCluster = 100.0
    data['carState'].vEgoCluster = data['carState'].vEgo
    data['carState'].canValid = True
    data['carState'].canTimeout = False
    data['carControl'].longActive = True
    data['slcDashboardObservation'] = messaging.new_message('slcDashboardObservation').slcDashboardObservation
    msg = messaging.new_message('slcVisionObservation')
    evidence = msg.slcVisionObservation.init('vision')
    evidence.status = 'valid'
    evidence.producerSessionId = 'camera-a'
    evidence.modelId = MODEL_ID
    evidence.stream = 'road'
    evidence.speedMps = 55 * .44704
    evidence.confidence = .9
    evidence.supportCount = 2
    evidence.episode = 1
    evidence.frameId = 20
    evidence.observedMonoTime = 1_980_000_000
    evidence.validUntilMonoTime = 2_200_000_000
    evidence.cameraFrameEofBootTime = 5_950_000_000
    data['slcVisionObservation'] = msg.slcVisionObservation
    return ReplaySM(data, 2_000_000_000)

  def test_finalized_gm_source_startup_and_control_are_separate(self):
    with OpenpilotPrefix():
      params = Params()
      self.requests(params, True)
      cases = [(configured(params, identity, **kwargs), True) for identity, kwargs in LONG_CASES]
      cases += [(configured(params, identity, **kwargs), False) for identity, kwargs in STOCK_CASES]
      for identity in ORDINARY_ASCM_CAR | ORDINARY_SDGM_CAR:
        cases.extend((ordinary_params(identity, sascm=True, alpha=alpha), alpha) for alpha in (False, True))
      for cp, control in cases:
        with self.subTest(identity=cp.carFingerprint, word=cp.safetyConfigs[0].safetyParam):
          self.assertTrue(enabled(params, cp, 'vision', {}))
          self.assertTrue(diagnostic_choice_enabled({}, cp))
          self.assertEqual(vision_control_enabled(params, cp), control)
          for field in ('passive', 'dashcamOnly', 'notCar'):
            bad = cp.as_reader().as_builder()
            setattr(bad, field, True)
            self.assertFalse(enabled(params, bad, 'vision', {}))
            self.assertFalse(vision_control_enabled(params, bad))
      self.requests(params, False)
      self.assertTrue(enabled(params, cases[0][0], 'vision', {}))
      self.assertFalse(vision_control_enabled(params, cases[0][0]))
      params.put_bool('ShowSpeedLimits', False, block=True)
      self.assertFalse(enabled(params, cases[0][0], 'vision', {}))
      Path(params.get_param_path('SLCPriority1')).write_bytes(b'invalid')
      self.assertFalse(vision_control_enabled(params, cases[0][0]))

  def test_visible_camera_sign_cannot_control_stock_or_display_only_sessions(self):
    with OpenpilotPrefix():
      params = Params()
      for disable, control in ((True, True), (False, False)):
        self.requests(params, control)
        cp = configured(params, CAR.CHEVROLET_VOLT_CC, disable=disable)
        sm = self.bus()
        runtime = Runtime(slc_runtime_settings(params, cp, {}), vision_enabled=True,
                          vision_control_qualified=vision_control_enabled(params, cp),
                          vision_display_qualified=enabled(params, cp, 'vision', {}))
        output = runtime.step(sm, cp, now_ns=2_000_000_000, now_boot_ns=6_000_000_000)
        state = output.message.slcState
        self.assertEqual(state.source, 'vision')
        self.assertAlmostEqual(state.speedLimit, 55 * .44704, places=4)
        self.assertTrue(state.displayOnly)
        self.assertFalse(state.hasPending)
        self.assertFalse(state.hasCeiling)
        self.assertIsNone(output.result.ceiling)
        self.assertEqual(state.sourceProducerSessionId, 'camera-a')
        sm.advance(2_300_000_000)
        stale = runtime.step(sm, cp, now_ns=2_300_000_000, now_boot_ns=6_300_000_000)
        self.assertNotEqual(stale.message.slcState.observationKind, 'valid')
        self.assertFalse(stale.message.slcState.hasCeiling)

  def test_gm_control_uses_saved_confirmation_and_axis_authority(self):
    def assert_cap(output, mph, sm, driver_set):
      cap = mph * .44704
      state = output.message.slcState
      self.assertTrue(state.hasCeiling)
      self.assertAlmostEqual(output.result.ceiling.speed_mps, cap, places=4)
      self.assertEqual(state.isLimitingMaxSet, cap < driver_set / 3.6)
      self.assertEqual(sm['carState'].vCruise, driver_set)
      self.assertEqual(sm['carState'].vCruiseCluster, driver_set)

    with OpenpilotPrefix():
      params = Params()
      self.requests(params, True)
      cp = configured(params, CAR.CHEVROLET_VOLT_CC)
      for confirmation in (False, True):
        for next_mph in (45, 65):
          with self.subTest(confirmation=confirmation, next_mph=next_mph):
            params.put('SLCConfirmation', int(confirmation), block=True)
            sm = self.bus()
            driver_set = sm['carState'].vCruise
            runtime = Runtime(slc_runtime_settings(params, cp, {}), session_id='drive', vision_enabled=True,
                              vision_control_qualified=vision_control_enabled(params, cp))
            first = runtime.step(sm, cp, now_ns=2_000_000_000, now_boot_ns=6_000_000_000)
            self.assertEqual(first.message.slcState.hasPending, confirmation)
            self.assertEqual(first.message.slcState.hasAccepted, not confirmation)
            self.assertFalse(first.message.slcState.automaticAdoptionPending)
            if confirmation:
              self.assertFalse(first.message.slcState.hasCeiling)
              self.assertIsNone(first.result.ceiling)
            else:
              self.assertAlmostEqual(first.message.slcState.acceptedSpeedLimit, 55 * .44704, places=4)
              assert_cap(first, 55, sm, driver_set)
            evidence = sm.data['slcVisionObservation'].vision
            evidence.observedMonoTime = 4_980_000_000
            evidence.validUntilMonoTime = 5_200_000_000
            evidence.cameraFrameEofBootTime = 8_950_000_000
            evidence.frameId += 1
            sm.advance(5_000_000_000)
            request = (Action('drive', 1, first.message.slcState.decisionId, first.message.slcState.presentationId, 'accept')
                       if confirmation else None)
            accepted = runtime.step(sm, cp, now_ns=5_000_000_000, now_boot_ns=9_000_000_000, request=request)
            self.assertTrue(accepted.message.slcState.hasAccepted)
            assert_cap(accepted, 55, sm, driver_set)
            approved = accepted.result.state.acceptance.accepted
            evidence = sm.data['slcVisionObservation'].vision
            evidence.episode += 1
            evidence.frameId += 1
            sm.advance(5_040_000_000)
            repeated = runtime.step(sm, cp, now_ns=5_040_000_000, now_boot_ns=9_040_000_000)
            self.assertFalse(repeated.message.slcState.hasPending)
            assert_cap(repeated, 55, sm, driver_set)
            if confirmation:
              self.assertEqual(repeated.result.state.acceptance.accepted, approved)
              self.assertIsNone(repeated.result.acceptance.history_write)
            evidence.speedMps = next_mph * .44704
            evidence.episode += 1
            evidence.frameId += 1
            sm.advance(5_060_000_000)
            changed = runtime.step(sm, cp, now_ns=5_060_000_000, now_boot_ns=9_060_000_000)
            self.assertEqual(changed.message.slcState.hasPending, confirmation)
            self.assertFalse(changed.message.slcState.automaticAdoptionPending)
            self.assertTrue(changed.message.slcState.hasAccepted)
            accepted_mph = 55 if confirmation else next_mph
            self.assertAlmostEqual(changed.message.slcState.acceptedSpeedLimit, accepted_mph * .44704, places=4)
            assert_cap(changed, accepted_mph, sm, driver_set)
            self.assertEqual(changed.result.acceptance.policy, runtime.settings.acceptance)
            sm.data['carControl'].longActive = False
            sm.advance(5_080_000_000)
            inactive = runtime.step(sm, cp, now_ns=5_080_000_000, now_boot_ns=9_080_000_000)
            self.assertFalse(inactive.message.slcState.hasCeiling)
            self.assertIsNone(inactive.result.ceiling)

  def test_actual_planner_and_manager_start_display_producer(self):
    from openpilot.selfdrive.controls import plannerd
    from openpilot.system.manager.process_config import vision_slc_development
    class EndLoop(Exception):
      pass
    with OpenpilotPrefix():
      params = Params()
      self.requests(params, False)
      cp = configured(params, CAR.CHEVROLET_VOLT_CC, disable=True)
      params.put('CarParams', cp.to_bytes(), block=True)
      with patch.dict('os.environ', {}, clear=True):
        self.assertTrue(vision_slc_development(True, params, cp))
        self.assertFalse(vision_slc_development(False, params, cp))
        sm = Mock()
        sm.update.side_effect = EndLoop
        with (patch.object(plannerd, 'Params', return_value=params),
              patch.object(plannerd, 'config_realtime_process'),
              patch.object(slc_host, 'SlcRuntime', wraps=slc_host.SlcRuntime) as runtime,
              patch.object(plannerd.messaging, 'sub_sock'),
              patch.object(plannerd.messaging, 'SubMaster', return_value=sm),
              patch.object(plannerd.messaging, 'PubMaster')):
          with self.assertRaises(EndLoop):
            plannerd.main()
          self.assertTrue(runtime.call_args.kwargs['vision_enabled'])
          self.assertTrue(runtime.call_args.kwargs['vision_display_qualified'])
          self.assertFalse(runtime.call_args.kwargs['vision_control_qualified'])

  def test_stock_ui_can_select_display_source_with_stale_request_guard(self):
    with OpenpilotPrefix():
      params = Params()
      params.put('SLCPriority1', 'Dashboard', block=True)
      cp = configured(params, CAR.CHEVROLET_VOLT_CC, disable=True)
      owner = FeatureSettingsOwner(params, authority=lambda group: group == 'preferences',
                                   vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp,
                                   vision_development=lambda: diagnostic_choice_enabled({}, cp))
      state = owner.snapshot('slc', parked=True, system_long=False, lateral_context=True, metric=False)
      row = next(row for row in state.rows if row.key == 'SLCPriority1')
      self.assertTrue(row.available)
      self.assertEqual(row.reason, 'Use this source first; try the secondary source when it is unavailable')
      request = row_change(row)
      assert request is not None
      self.assertTrue(owner.apply(request))
      self.assertEqual(params.get('SLCPriority1'), 'Vision')
      self.assertFalse(owner.apply(request))
