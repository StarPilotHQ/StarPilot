"""Saved preferences reach actual Controls only with fresh lateral authority."""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.lateral.lane_centering import ControlMode
from openpilot.starpilot.lateral.lane_runtime import LaneCenteringHost, read_settings, runtime_supported
from openpilot.starpilot.lateral.tests.test_lane_centering import model
from openpilot.starpilot.aol.wire import SafetyState, encode_safety
from opendbc.car import gen_empty_fingerprint
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.interface import CarInterface as HondaCarInterface
from opendbc.car.honda.values import CAR as HondaCAR
from opendbc.car.hyundai.interface import CarInterface as HyundaiCarInterface
from opendbc.car.hyundai.ioniq6_handoff import build_ioniq6_hda2_long_candidate
from opendbc.car.hyundai.values import CAR as HyundaiCAR
from opendbc.car.structs import car
from opendbc.car.toyota.values import CAR


def ioniq_candidate(*, alternate=False):
  fingerprint = gen_empty_fingerprint()
  fingerprint[2].update({0x110: 32, 0x362: 32} if alternate else {0x50: 16, 0x2A4: 24})
  fingerprint[1].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                         0x1BA: 24, 0x1E5: 16, 0x36A: 16})
  fingerprint[0][0x3A5] = 24
  fingerprint[0][0x100] = 24
  stock = HyundaiCarInterface.get_params(HyundaiCAR.HYUNDAI_IONIQ_6, fingerprint, [], False, False, False)
  return stock, build_ioniq6_hda2_long_candidate(stock, fingerprint)


def known_body_fixture():
  # Documented pre-facelift body bounds for control-path tests. This injects a
  # known body; it does not certify a production VIN or firmware/body mapping.
  return mock.patch.object(HyundaiCarInterface, 'get_lateral_body_dimensions', create=True, return_value=(1.88, 4.855))


class Frame:
  def __init__(self, now_ns):
    self.logMonoTime = dict.fromkeys(LaneCenteringHost.REQUIRED, now_ns)
    self.checked = True
    self.state = SimpleNamespace(canValid=True, canTimeout=False)

  def all_checks(self, _services):
    return self.checked

  def all_alive(self, _services):
    return True

  def all_valid(self, _services):
    return True

  def __getitem__(self, _service):
    return self.state


def publish_corolla_params(params):
  """Use Card's real startup admission and serialized full-axis configuration."""
  from openpilot.selfdrive.car.card import Car
  params.put_bool('OpenpilotEnabledToggle', True, block=True)
  params.put_bool('SafeMode', False, block=True)
  cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)

  def discover(*args, pre_create_hook, **kwargs):
    return interfaces[cp.carFingerprint](pre_create_hook(cp.as_reader().as_builder(), cp.carFingerprint, {}, []))

  with mock.patch.dict(os.environ, {'SIMULATION': '1'}), \
       mock.patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=SimpleNamespace(can=[1])), \
       mock.patch('openpilot.selfdrive.car.card.get_car', side_effect=discover):
    selected = Car()
  final = selected.CP.as_reader().as_builder()
  assert final.alternativeExperience == 32
  return final


def feed(controls, timestamp, tick, *, active=True, enabled=True, fault=False, override=False, signal=False, model_age=0,
         can_valid=True, can_timeout=False, aol_lat_only=False, native_session='lane-aol-session', speed=20.,
         drive_id=500_000_000, native_allowed=True, independent=False, delay_fast=False):
  events = []

  def msg(service, size=None):
    event = messaging.new_message(service, size, valid=True, logMonoTime=timestamp)
    events.append(event)
    return getattr(event, service)

  device = msg('deviceState')
  device.started = True
  device.startedMonoTime = drive_id
  cs = msg('carState')
  cs.vEgo = cs.vEgoRaw = speed
  cs.vCruise = cs.vCruiseCluster = 100.0
  cs.canValid = can_valid
  cs.canTimeout = can_timeout
  cs.steerFaultTemporary = fault
  cs.steeringPressed = override
  cs.leftBlinker = signal
  state = msg('selfdriveState')
  state.active, state.enabled = active, enabled
  state.state = 'enabled' if enabled else 'disabled'
  if tick % 5 == 0:
    lp = msg('vehicleParameters')
    lp.stiffnessFactor, lp.steerRatio = 1.0, controls.CP.steerRatio
  msg('longitudinalPlan').aTarget = 0.5
  if tick % 25 == 0 or delay_fast:
    msg('lateralDelay').lateralDelay = 0.1
  msg('onroadEvents', 0)
  if tick % 5 == 0:
    event = messaging.new_message('modelV2', valid=True, logMonoTime=timestamp - model_age)
    event.modelV2 = model()
    event.modelV2.action.desiredCurvature = 0.0002
    events.append(event)
  if aol_lat_only:
    axis = msg('aolAxisState')
    axis.qualified = axis.nativeAcknowledged = axis.desiredLateral = axis.lateralActive = True
    axis.desiredLongitudinal = axis.longitudinalActive = False
    axis.sessionId = 'lane-aol-session'
    axis.observedMonoTime = timestamp
    axis.validUntilMonoTime = timestamp + 30_000_000
    msg('aolSafetyWire', 0)
    events[-1].aolSafetyWire = encode_safety(SafetyState(
      1, True, timestamp, timestamp + 200_000_000, int(car.CarParams.SafetyModel.hondaBosch),
      controls.CP.safetyConfigs[-1].safetyParam, True, False, True, False,
      'lane-test-panda', native_session))
  elif controls.aol_replay and controls.CP.brand == 'toyota':
    from openpilot.starpilot.car.toyota.aol import qualified
    assert qualified(controls.CP, marked_only=True)
    axis = msg('aolAxisState')
    axis.qualified = axis.nativeAcknowledged = True
    axis.desiredLateral = axis.lateralActive = independent or active and enabled
    axis.desiredLongitudinal = axis.longitudinalActive = enabled
    axis.sessionId = native_session
    axis.observedMonoTime = timestamp
    axis.validUntilMonoTime = timestamp + 30_000_000
    msg('aolSafetyWire', 0)
    events[-1].aolSafetyWire = encode_safety(SafetyState(
      1, True, timestamp, timestamp + 200_000_000, int(controls.CP.safetyConfigs[0].safetyModel.raw),
      controls.CP.safetyConfigs[0].safetyParam, native_allowed and can_valid and not can_timeout and not fault, can_valid and not can_timeout,
      independent or active and enabled, enabled, 'lane-test-panda', native_session))
  controls.sm.update_msgs(timestamp / 1e9, [event.as_reader() for event in events])


class LaneRuntimeTests(unittest.TestCase):
  def test_two_lines_do_not_add_curvature_that_opposes_current_spacing_goal(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'REPLAY': '1'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      publish_corolla_params(params)
      params.put_bool('LaneCentering', True, block=True)
      params.put('LaneCenteringE2EAuthority', 0., block=True)
      controls = Controls()
      m = model(left=-1.8, right=1.8, path=-.5)
      with mock.patch(f'{__name__}.model', return_value=m):
        for tick in range(100):
          feed(controls, 1_000_000_000 + tick * 10_000_000, tick)
          controls.state_control()
      self.assertIsNotNone(controls.last_lane_centering_result)
      assert controls.last_lane_centering_result is not None
      self.assertEqual(controls.last_lane_centering_result.correction, 0.)
      self.assertEqual(controls.lane_centering_applied, 0.)
      self.assertAlmostEqual(controls.desired_curvature, .0002)

  def test_receive_queued_state_before_control_without_extending_old_input(self):
    from openpilot.starpilot.lateral.tests.test_lane_centering import single
    for queued in ('fresh', 'empty', 'override', 'fault', 'disabled'):
      with self.subTest(queued=queued), OpenpilotPrefix(), \
           mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'REPLAY': '1'}), \
           mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
        params = Params()
        publish_corolla_params(params)
        params.put_bool('LaneCentering', True, block=True)
        params.put('LaneCenteringE2EAuthority', 0., block=True)
        controls = Controls()
        with mock.patch(f'{__name__}.model', return_value=single(line=-1.4, edge=10.)):
          for tick in range(100):
            feed(controls, 1_000_000_000 + tick * 10_000_000, tick)
            controls.state_control()
          now = 2_030_000_000
          held = dict(controls.sm.logMonoTime)

          def receive(timeout, queued=queued, controls=controls, now=now):
            self.assertEqual(timeout, 0)
            if queued != 'empty':
              feed(controls, now, 100, override=queued == 'override', fault=queued == 'fault',
                   active=queued != 'disabled', enabled=queued != 'disabled')

          with mock.patch.dict(os.environ, {'REPLAY': '0'}), \
               mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now), \
               mock.patch.object(controls, 'update', side_effect=receive) as refresh:
            command, _ = controls.state_control()
          refresh.assert_called_once_with(0)
          if queued == 'fresh':
            self.assertIsNotNone(controls.last_lane_centering_result)
            self.assertGreater(controls.lane_centering_applied, 0.)
          else:
            self.assertEqual(controls.lane_centering_applied, 0.)
          if queued == 'empty':
            self.assertEqual(controls.sm.logMonoTime, held)
            self.assertEqual(controls.lane_centering_inactive_reason, 'host_carState_stale')
          if queued in ('fault', 'disabled'):
            self.assertFalse(command.latActive)

  def test_input_refresh_stays_off_for_disabled_or_current_preferences(self):
    from dataclasses import replace
    with OpenpilotPrefix():
      host = LaneCenteringHost(Params())
      now = 1_000_000_000
      frame = Frame(now - 30_000_000)
      self.assertFalse(host.inputs_need_refresh(frame, now_ns=now))
      host.settings = replace(host.settings, enabled=True)
      self.assertTrue(host.inputs_need_refresh(frame, now_ns=now))
      frame.logMonoTime = dict.fromkeys(host.REQUIRED, now)
      self.assertFalse(host.inputs_need_refresh(frame, now_ns=now))
      frame.logMonoTime['carState'] = now + 1
      self.assertFalse(host.inputs_need_refresh(frame, now_ns=now))

  def test_single_line_recovers_between_brief_refusals_without_retaining_output(self):
    from openpilot.starpilot.lateral.tests.test_lane_centering import single
    for missing_right, sign in ((True, 1), (False, -1)):
      with self.subTest(missing_right=missing_right), OpenpilotPrefix(), \
           mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'REPLAY': '1'}), \
           mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
        params = Params()
        publish_corolla_params(params)
        params.put_bool('LaneCentering', True, block=True)
        params.put('LaneCenteringE2EAuthority', 0., block=True)
        controls = Controls()
        m = single(missing_right=missing_right, line=-1.5, edge=10.)
        with mock.patch(f'{__name__}.model', return_value=m):
          for tick in range(220):
            phase = tick - 100
            now = 1_000_000_000 + tick * 10_000_000
            feed(controls, now, tick)
            if phase >= 0 and phase % 12 == 0:
              controls.sm.logMonoTime['selfdriveState'] = now - 25_000_001
            controls.state_control()
            if phase >= 0 and phase % 12 == 0:
              self.assertIsNone(controls.last_lane_centering_result)
              self.assertEqual(controls.lane_centering_applied, 0.)
              self.assertEqual(controls.lane_centering_controller._correction, 0.)
              self.assertIsNone(controls.lane_centering_controller._tracker.path)
            elif phase >= 0 and phase % 12 == 11:
              self.assertGreater(sign * controls.lane_centering_applied, 0.)

  def test_precise_single_line_corrects_small_spacing_error_in_actual_controls(self):
    from openpilot.starpilot.lateral.tests.test_lane_centering import single
    for missing_right, sign in ((True, 1), (False, -1)):
      with self.subTest(missing_right=missing_right), OpenpilotPrefix(), \
           mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'REPLAY': '1'}), \
           mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
        params = Params()
        publish_corolla_params(params)
        params.put_bool('LaneCentering', True, block=True)
        params.put('LaneCenteringE2EAuthority', 0., block=True)
        controls = Controls()
        m = single(missing_right=missing_right, line=-1.64, edge=10.)
        m.laneLineStds[1 if missing_right else 2] = .02
        with mock.patch(f'{__name__}.model', return_value=m):
          for tick in range(100):
            feed(controls, 1_000_000_000 + tick * 10_000_000, tick)
            controls.state_control()
        self.assertGreater(sign * controls.lane_centering_applied, 0.)

  def test_body_profile_reaches_actual_controls_and_clearance_withdraws_only_added_curvature(self):
    from opendbc.car.hyundai.tests.test_lane_body import identity
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '0', 'REPLAY': '1',
                                                        'AOL_REPLAY_RUNTIME': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      _, cp = ioniq_candidate()
      metadata = identity()
      cp.carVin = metadata.carVin
      cp.carFw = [vars(fw) for fw in metadata.carFw]
      before = cp.to_dict()
      params.put('CarParams', cp.to_bytes(), block=True)
      params.put_bool('LaneCentering', True, block=True)
      params.put('LaneCenteringE2EAuthority', 0., block=True)
      baseline, candidate = Controls(), Controls(predict_lane_clearance=True)
      baseline.lane_centering_host = None
      body = candidate.lane_centering_controller._body
      self.assertIsNotNone(body)
      self.assertEqual((body.width, body.length, body.height), (1.88, 4.855, 1.495))
      self.assertEqual(candidate.CP.to_dict(), before)
      original_model = model
      phase = 0

      def perceived_model():
        m = original_model(left=-1.2, right=2.4)
        m.init('roadEdges', 2)
        for edge, side in zip(m.roadEdges, (-1., 1.), strict=True):
          edge.x, edge.y = list(range(51)), [10. * side] * 51
        m.roadEdgeStds = [.1, .1]
        if phase >= 30:
          m.laneLineProbs = [0., .9, .1, 0.]
        if 60 <= phase < 90:
          m.roadEdges[1].y = [1.8] * 51
        return m

      added = recovered = 0
      with mock.patch(f'{__name__}.model', side_effect=perceived_model):
        for phase in range(150):
          now = 2_000_000_000 + phase * 10_000_000
          override = phase >= 120
          feed(baseline, now, phase, override=override)
          feed(candidate, now, phase, override=override)
          reference, _ = baseline.state_control()
          command, _ = candidate.state_control()
          self.assertEqual((command.latActive, command.longActive), (reference.latActive, reference.longActive))
          self.assertEqual(command.actuators.accel, reference.actuators.accel)
          self.assertEqual(command.actuators.longControlState, reference.actuators.longControlState)
          if 60 <= phase < 90 or override:
            self.assertEqual(candidate.lane_centering_applied, 0.)
          elif 30 <= phase < 60:
            added += abs(candidate.lane_centering_applied) > 1e-6
          elif 90 <= phase < 120:
            recovered += abs(candidate.lane_centering_applied) > 1e-6
      self.assertGreater(added, 0)
      self.assertGreater(recovered, 0)
      self.assertEqual(candidate.last_lane_centering_result.reason, 'driver_override')

  def test_unqualified_body_through_actual_controls_preserves_nominal_native_output(self):
    for identity in ('absent', 'manual', 'unknown_firmware', 'mismatched_firmware'):
      with self.subTest(identity=identity), OpenpilotPrefix(), \
           mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '0', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}), \
           mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
        params = Params()
        _, cp = ioniq_candidate()
        if identity == 'manual':
          cp.fingerprintSource = 'fixed'
        elif identity in ('unknown_firmware', 'mismatched_firmware'):
          cp.fingerprintSource = 'fw'
          firmware = [{'ecu': 'fwdCamera', 'address': 0x7c4, 'brand': 'hyundai', 'fwVersion': b'UNQUALIFIED'}]
          if identity == 'mismatched_firmware':
            firmware.append({'ecu': 'fwdRadar', 'address': 0x7d0, 'brand': 'hyundai',
                             'fwVersion': b'\xf1\x00CE__ RDR -----      1.00 1.01 99110-KL000         '})
          cp.carFw = firmware
        params.put('CarParams', cp.to_bytes(), block=True)
        params.put_bool('LaneCentering', True, block=True)
        params.put('LaneCenteringE2EAuthority', 0., block=True)
        baseline, candidate = Controls(), Controls(predict_lane_clearance=True)
        baseline.lane_centering_host = None
        for tick in range(40):
          now = 2_000_000_000 + tick * 10_000_000
          feed(baseline, now, tick)
          feed(candidate, now, tick)
          reference, _ = baseline.state_control()
          command, _ = candidate.state_control()
          self.assertEqual((command.latActive, command.longActive), (reference.latActive, reference.longActive))
          self.assertEqual(command.actuators.to_dict(), reference.actuators.to_dict())
          self.assertEqual(candidate.desired_curvature, baseline.desired_curvature)
          self.assertEqual(candidate.lane_centering_applied, 0.)
        self.assertTrue(command.latActive)
        self.assertNotEqual(candidate.desired_curvature, 0.)
        self.assertTrue(candidate.lane_centering_controller._body_required)
        self.assertIsNone(candidate.lane_centering_controller._body)
        self.assertEqual(candidate.last_lane_centering_result.reason, 'unqualified_body')
        self.assertEqual(candidate.last_lane_centering_result.correction, 0.)
        self.assertIsNone(candidate.lane_centering_controller._tracker.path)


  def test_shared_platform_requires_verified_body_without_retuning_geometry(self):
    from openpilot.starpilot.lateral.lane_clearance import body_dimensions_required, interface_dimensions
    stock, tagged = ioniq_candidate()
    for cp in (stock, tagged):
      before = cp.to_dict()
      self.assertTrue(body_dimensions_required(HyundaiCarInterface, cp))
      self.assertIsNone(interface_dimensions(HyundaiCarInterface, cp))
      self.assertEqual(cp.to_dict(), before)
      other = cp.as_reader().as_builder()
      other.carFingerprint = HyundaiCAR.HYUNDAI_IONIQ_5
      self.assertFalse(body_dimensions_required(HyundaiCarInterface, other))
      self.assertIsNone(interface_dimensions(HyundaiCarInterface, other))
    self.assertFalse(body_dimensions_required(HondaCarInterface, stock))
    self.assertIsNone(interface_dimensions(HondaCarInterface, stock))


  def test_shared_feed_is_one_current_batch_and_delay_qualifies_at_real_rate(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'REPLAY': '1', 'SIMULATION': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      _, tagged = ioniq_candidate()
      params.put('CarParams', tagged.to_bytes(), block=True)
      controls = Controls()
      now = 2_000_000_000
      batches = []
      with mock.patch.object(controls.sm, 'update_msgs', side_effect=lambda stamp, rows: batches.append((stamp, rows))):
        feed(controls, now, 0)
      self.assertEqual(len(batches), 1)
      self.assertEqual(batches[0][0], now / 1e9)
      delays = [row for row in batches[0][1] if row.which() == 'lateralDelay']
      self.assertEqual([row.logMonoTime for row in delays], [now])
      before = controls.sm.frame
      controls.sm.update_msgs(*batches[0])
      self.assertEqual(controls.sm.frame, before + 1)
      self.assertEqual(controls.sm.freq_tracker['lateralDelay'].avg_dt.count, 0)
      self.assertFalse(controls.sm.all_checks(['lateralDelay']))
      for tick in range(1, 26):
        before = controls.sm.frame
        feed(controls, now + tick * 10_000_000, tick)
        self.assertEqual(controls.sm.frame, before + 1)
        if tick < 25:
          self.assertFalse(controls.sm.all_checks(['lateralDelay']))
      self.assertEqual(controls.sm.freq_tracker['lateralDelay'].avg_dt.get_average(), .25)
      self.assertTrue(controls.sm.all_checks(['lateralDelay']))
      for tick in range(26, 31):
        feed(controls, now + tick * 10_000_000, tick, delay_fast=True)
      self.assertFalse(controls.sm.all_checks(['lateralDelay']))


  def test_body_guard_rejects_incorrect_delay_rate_with_lateral_axis_still_active(self):
    with OpenpilotPrefix(), known_body_fixture(), mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '0', 'REPLAY': '1',
                                                         'AOL_REPLAY_RUNTIME': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      _, tagged = ioniq_candidate()
      params.put('CarParams', tagged.to_bytes(), block=True)
      params.put_bool('LaneCentering', True, block=True)
      params.put('LaneCenteringE2EAuthority', 0., block=True)
      controls = Controls(predict_lane_clearance=True)
      for tick in range(35):
        feed(controls, 2_000_000_000 + tick * 10_000_000, tick)
        command, _ = controls.state_control()
      self.assertTrue(command.latActive)
      self.assertGreater(controls.last_lane_centering_result.correction, 0.)
      for tick in range(35, 45):
        feed(controls, 2_000_000_000 + tick * 10_000_000, tick, delay_fast=True)
        command, _ = controls.state_control()
      self.assertTrue(command.latActive)
      self.assertFalse(controls.sm.all_checks(['lateralDelay']))
      self.assertEqual(controls.last_lane_centering_result.correction, 0.)
      self.assertEqual(controls.lane_centering_applied, 0.)

  def test_clearance_delay_uses_original_header_and_rejects_stale_or_future_source(self):
    from openpilot.starpilot.lateral.lane_clearance import current_delay
    frame = SimpleNamespace(logMonoTime={'lateralDelay':1_000_000_000}, all_checks=lambda services:True)
    class Source:
      logMonoTime = frame.logMonoTime
      all_checks = staticmethod(frame.all_checks)
      def __getitem__(self, service):
        return SimpleNamespace(lateralDelay=.1)
    source = Source()
    self.assertEqual(current_delay(source, 1_250_000_000), .1)
    self.assertIsNone(current_delay(source, 1_626_000_000))
    self.assertIsNone(current_delay(source, 999_999_999))
    self.assertEqual(source.logMonoTime['lateralDelay'], 1_000_000_000)

  def test_steering_pause_original_timing_source_loss_and_units(self):
    from openpilot.starpilot.lateral.pause import LateralPause, PauseSettings, read_settings as read_pause
    state = SimpleNamespace(vEgo=0., leftBlinker=True, rightBlinker=False, canValid=True, canTimeout=False)
    pause = LateralPause(PauseSettings(10., True, .2))
    now = 1_000_000_000
    self.assertFalse(pause.allowed(state, now_ns=now, source_ns=now))
    state.leftBlinker = False
    self.assertFalse(pause.allowed(state, now_ns=now + 10_000_000, source_ns=now + 10_000_000))
    self.assertFalse(pause.allowed(state, now_ns=now + 209_000_000, source_ns=now + 209_000_000))
    self.assertTrue(pause.allowed(state, now_ns=now + 210_000_000, source_ns=now + 210_000_000))
    # A short source/drive-observation gap cannot erase a committed delay.
    pause = LateralPause(PauseSettings(10., True, .2))
    state.leftBlinker = True
    self.assertFalse(pause.allowed(state, now_ns=now, source_ns=now, drive_id=1))
    state.leftBlinker = False
    self.assertFalse(pause.allowed(state, now_ns=now + 10_000_000, source_ns=now + 10_000_000, drive_id=1))
    self.assertFalse(pause.allowed(state, now_ns=now + 50_000_000, source_ns=0, drive_id=1))
    self.assertFalse(pause.allowed(state, now_ns=now + 60_000_000, source_ns=now + 60_000_000, drive_id=0))
    self.assertFalse(pause.allowed(state, now_ns=now + 100_000_000, source_ns=now + 100_000_000, drive_id=1))
    self.assertTrue(pause.allowed(state, now_ns=now + 210_000_000, source_ns=now + 210_000_000, drive_id=1))
    # If signal-off was hidden by the gap, begin the delay on its first fresh observation.
    pause = LateralPause(PauseSettings(10., True, .2))
    state.leftBlinker = True
    pause.allowed(state, now_ns=now, source_ns=now, drive_id=1)
    state.leftBlinker = False
    self.assertFalse(pause.allowed(state, now_ns=now + 50_000_000, source_ns=0, drive_id=1))
    self.assertFalse(pause.allowed(state, now_ns=now + 100_000_000, source_ns=now + 100_000_000, drive_id=1))
    self.assertFalse(pause.allowed(state, now_ns=now + 299_000_000, source_ns=now + 299_000_000, drive_id=1))
    self.assertTrue(pause.allowed(state, now_ns=now + 300_000_000, source_ns=now + 300_000_000, drive_id=1))
    # The half-threshold condition is strict; equality never starts a delay.
    state.leftBlinker, state.vEgo = True, 5.
    pause.allowed(state, now_ns=now + 300_000_000, source_ns=now + 300_000_000)
    state.leftBlinker = False
    self.assertTrue(pause.allowed(state, now_ns=now + 310_000_000, source_ns=now + 310_000_000))
    self.assertFalse(pause.allowed(state, now_ns=now + 700_000_000, source_ns=now + 310_000_000))
    self.assertTrue(pause.allowed(state, now_ns=now + 710_000_000, source_ns=now + 710_000_000))
    self.assertFalse(pause.allowed(state, now_ns=now, source_ns=now))
    with OpenpilotPrefix():
      params = Params()
      params.put('PauseLateralSpeed', 20., block=True)
      params.put_bool('IsMetric', False, block=True)
      settings = read_pause(params)
      assert settings is not None
      self.assertAlmostEqual(settings.speed_mps, 20. * .44704)
      params.put_bool('IsMetric', True, block=True)
      settings = read_pause(params)
      assert settings is not None
      self.assertAlmostEqual(settings.speed_mps, 20. / 3.6)
      Path(params.get_param_path('PauseLateralSpeed')).write_bytes(b'nan')
      self.assertIsNone(read_pause(params))

  def test_invalid_saved_pause_settings_preserve_committed_delay(self):
    from openpilot.starpilot.lateral.pause import LateralPause, PauseSettings, read_settings as read_pause
    with OpenpilotPrefix():
      params = Params()
      self.assertEqual(read_pause(params), PauseSettings())
      params.put('PauseLateralSpeed', 20., block=True)
      params.put_bool('PauseLateralOnSignal', True, block=True)
      params.put('LateralResumeDelay', 5., block=True)
      pause = LateralPause(read_pause(params), params)
      expected = pause.settings
      state = SimpleNamespace(vEgo=0., leftBlinker=True, rightBlinker=False, canValid=True, canTimeout=False)
      now = 1_000_000_000
      self.assertFalse(pause.allowed(state, now_ns=now, source_ns=now))
      state.leftBlinker = False
      self.assertFalse(pause.allowed(state, now_ns=now + 10_000_000, source_ns=now + 10_000_000))
      path = Path(params.get_param_path('PauseLateralSpeed'))
      for tick, raw in ((2, b'nan'), (3, b'')):
        path.write_bytes(raw)
        self.assertIsNone(read_pause(params))
        self.assertFalse(pause.allowed(state, now_ns=tick * now, source_ns=tick * now))
        self.assertEqual(pause.settings, expected)
      path.unlink()
      path.mkdir()  # A nonregular Params object is unreadable, not an explicit zero.
      self.assertIsNone(read_pause(params))
      self.assertFalse(pause.allowed(state, now_ns=4 * now, source_ns=4 * now))
      self.assertEqual(pause.settings, expected)
      path.rmdir()
      params.put('PauseLateralSpeed', 20., block=True)
      self.assertFalse(pause.allowed(state, now_ns=5 * now, source_ns=5 * now))
      self.assertTrue(pause.allowed(state, now_ns=6_010_000_000, source_ns=6_010_000_000))
      # Only a valid explicit opt-out (or canonical absence) disables the policy.
      state.leftBlinker = True
      self.assertFalse(pause.allowed(state, now_ns=7 * now, source_ns=7 * now))
      params.put('PauseLateralSpeed', 0., block=True)
      self.assertTrue(pause.allowed(state, now_ns=8 * now, source_ns=8 * now))
      self.assertEqual(pause.settings.speed_mps, 0.)
      path.write_bytes(b'nan')
      self.assertEqual(LateralPause(read_pause(params)).settings, PauseSettings())

  def test_actual_controls_steering_pause_angle_standard_and_torque_aol(self):
    from opendbc.car.ford.values import CAR as FORD
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'REPLAY': '1', 'LANE_CENTERING_REPLAY_RUNTIME': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      params.put('PauseLateralSpeed', 10., block=True)
      params.put_bool('PauseLateralOnSignal', True, block=True)
      params.put('LateralResumeDelay', .1, block=True)
      params.put_bool('IsMetric', False, block=True)
      for marked in (False, True):
        with self.subTest(marked=marked):
          if marked:
            cp = publish_corolla_params(params)
          else:
            cp = interfaces[FORD.FORD_ESCAPE_MK4].get_non_essential_params(FORD.FORD_ESCAPE_MK4)
          params.put('CarParams', cp.to_bytes(), block=True)
          controls = Controls()
          self.assertEqual(controls.CP.lateralTuning.which(), 'torque' if marked else 'pid')
          for tick in range(25):
            now = 2_000_000_000 + tick * 10_000_000
            feed(controls, now, tick, signal=1 <= tick <= 5, speed=2., fault=tick == 20,
                 native_allowed=tick != 21, independent=marked, active=not marked, enabled=not marked)
            if tick == 22:
              controls.sm.valid['carState'] = False
            command, _ = controls.state_control()
            # The steering pause applies to AOL steering only (marked); normally engaged steering never pauses.
            # (Tick 22's stale carState was only caught by the pause's freshness check, which now guards AOL alone.)
            expected = (tick != 20 if not marked else
                        (tick == 0 or tick >= 16) and tick not in (20, 21, 22))
            self.assertEqual(command.latActive, expected, (marked, tick))
            self.assertEqual(command.enabled, not marked)
          mode = dict(active=not marked, enabled=not marked, independent=marked)
          # A new drive discards the prior low-speed turn's delay.
          feed(controls, 2_250_000_000, 25, signal=True, speed=0., **mode)
          self.assertEqual(controls.state_control()[0].latActive, not marked)
          feed(controls, 2_260_000_000, 26, speed=2., drive_id=2_255_000_000, **mode)
          self.assertTrue(controls.state_control()[0].latActive)
          # Live explicit opt-out refreshes without changing intent or the saved choice.
          params.put('PauseLateralSpeed', 0., block=True)
          feed(controls, 3_300_000_000, 30, signal=True, speed=2., **mode)
          self.assertTrue(controls.state_control()[0].latActive)
          self.assertEqual(params.get('PauseLateralSpeed'), 0.)
          params.put('PauseLateralSpeed', 10., block=True)

  def test_toyota_aol_remains_active_at_standstill_with_current_native_permission(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'REPLAY': '1'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      cp = publish_corolla_params(params)
      params.put('CarParams', cp.to_bytes(), block=True)
      controls = Controls()
      self.assertEqual(cp.minSteerSpeed, 0.)
      for tick, (speed, allowed, fault) in enumerate(((2., True, False), (0., True, False),
                                                     (0., False, False), (0., True, True),
                                                     (0., True, False), (2., True, False))):
        feed(controls, 2_000_000_000 + tick * 10_000_000, tick, speed=speed,
             active=False, enabled=False, independent=True, native_allowed=allowed, fault=fault)
        command, _ = controls.state_control()
        self.assertEqual(command.latActive, allowed and not fault, (tick, speed))
        self.assertFalse(command.longActive)
        self.assertFalse(command.enabled)

  def test_strength_saved_values_and_live_refresh_keep_existing_admission(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      params = SimpleNamespace(get_param_path=lambda key: str(root / key))
      (root / 'LaneCentering').write_bytes(b'1')
      self.assertEqual(read_settings(params).strength, 1.5)
      (root / 'LaneCenteringStrength').write_bytes(b'1.0')
      self.assertEqual(read_settings(params).strength, 1.0)
      self.assertEqual((root / 'LaneCenteringStrength').read_bytes(), b'1.0')
      (root / 'LaneCenteringStrength').unlink()
      host = LaneCenteringHost(params)
      now = 10_000_000_000
      first = host.sample(Frame(now), now_ns=now, lateral_active=True, longitudinal_active=False)
      self.assertEqual(first.settings.strength, 1.5)
      (root / 'LaneCenteringStrength').write_bytes(b'3.0')
      now += host.REFRESH_NS
      updated = host.sample(Frame(now), now_ns=now, lateral_active=True, longitudinal_active=False)
      self.assertEqual(updated.settings.strength, 3.0)
      restarted = LaneCenteringHost(params).sample(Frame(now), now_ns=now, lateral_active=True, longitudinal_active=False)
      self.assertEqual(restarted.settings.strength, 3.0)
      self.assertEqual((root / 'LaneCenteringStrength').read_bytes(), b'3.0')
      self.assertTrue(updated.time_discontinuity)
      for invalid in (b'nan', b'0.49', b'3.001', b'true'):
        (root / 'LaneCenteringStrength').write_bytes(invalid)
        self.assertFalse(read_settings(params).enabled)
        now += host.REFRESH_NS
        self.assertIsNone(host.sample(Frame(now), now_ns=now, lateral_active=True, longitudinal_active=False))


  def test_normal_reader_admission_uses_steering_and_saved_preference_gates(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '0'}):
      params = Params()
      for alternate in (False, True):
        with self.subTest(alternate=alternate):
          stock, tagged = ioniq_candidate(alternate=alternate)
          self.assertTrue(runtime_supported(stock))
          self.assertTrue(runtime_supported(tagged))
          self.assertFalse(read_settings(params).enabled)
          params.put_bool('LaneCentering', True, block=True)
          self.assertTrue(read_settings(params).enabled)
          self.assertTrue(runtime_supported(interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)))
          for safety_model in ('noOutput', 'silent', 'allOutput'):
            tagged.safetyConfigs[0].safetyModel = safety_model
            self.assertFalse(runtime_supported(tagged))
          params.put_bool('LaneCentering', False, block=True)
      _, tagged = ioniq_candidate()
      params.put_bool('LaneCentering', True, block=True)
      Path(params.get_param_path('LaneCenterOffset')).write_bytes(b'nan')
      self.assertFalse(read_settings(params).enabled)
      with mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '1'}):
        self.assertTrue(runtime_supported(tagged))

  def test_actual_tagged_ioniq_saved_host_uses_fresh_lateral_authority(self):
    with OpenpilotPrefix(), known_body_fixture(), mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '0', 'REPLAY': '1',
                                                         'AOL_REPLAY_RUNTIME': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      stock, tagged = ioniq_candidate()
      params.put('LaneCenteringE2EAuthority', 0.0, block=True)
      params.put('CarParams', tagged.to_bytes(), block=True)
      disabled = Controls(predict_lane_clearance=True)
      self.assertIsNotNone(disabled.lane_centering_host)
      frame = Frame(1_000_000_000)
      self.assertIsNone(disabled.lane_centering_host.sample(frame, now_ns=1_000_000_000,
                                                         lateral_active=True, longitudinal_active=False))
      params.put_bool('LaneCentering', True, block=True)
      controls = disabled
      self.assertEqual(controls.lane_centering_controller._body.width, 1.88)
      self.assertEqual(controls.lane_centering_controller._body.length, 4.855)
      self.assertIsNotNone(controls.lane_centering_host)
      for tick in range(35):
        now = 2_000_000_000 + tick * 10_000_000
        feed(controls, now, tick)
        command, _ = controls.state_control()
        self.assertTrue(command.latActive)
      self.assertIsNotNone(controls.last_lane_centering_result)
      self.assertGreater(controls.last_lane_centering_result.correction, 0.0)
      feed(controls, now + 10_000_000, 35, model_age=200_000_000)
      controls.state_control()
      self.assertIsNone(controls.last_lane_centering_result)
      params.put_bool('LaneCentering', False, block=True)
      feed(controls, 3_000_000_000, 36)
      controls.state_control()
      self.assertIsNone(controls.last_lane_centering_result)
      self.assertEqual(controls.lane_centering_applied, 0.0)
      params.put('CarParams', stock.to_bytes(), block=True)
      self.assertIsNotNone(Controls().lane_centering_host)

  def test_tagged_ioniq_four_axes_and_stale_reset(self):
    with OpenpilotPrefix(), known_body_fixture(), mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '0', 'REPLAY': '1',
                                                         'AOL_REPLAY_RUNTIME': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      _, tagged = ioniq_candidate()
      params.put('CarParams', tagged.to_bytes(), block=True)
      params.put_bool('LaneCentering', True, block=True)
      params.put('LaneCenteringE2EAuthority', 0.0, block=True)
      controls = Controls()
      # This qualified-axis test also explicitly primes the 4 Hz delay source.
      initial = messaging.new_message('lateralDelay', valid=True, logMonoTime=750_000_000)
      initial.lateralDelay.lateralDelay = .1
      controls.sm.update_msgs(.75, [initial.as_reader()])
      self.assertIsNotNone(controls.lane_centering_host)
      # Inject already-qualified independent-axis results at the consumer seam;
      # AOL's native/session checks are covered by its own wire tests.
      controls.aol_replay = True
      tick = 0
      for lat, lon in ((True, True), (True, False), (False, True), (False, False)):
        with self.subTest(lat=lat, lon=lon), \
             mock.patch('openpilot.selfdrive.controls.controlsd.current_axis', return_value=SimpleNamespace(
               sessionId='test-session', nativeAcknowledged=True, lateralActive=lat, desiredLateral=lat,
               longitudinalActive=lon, desiredLongitudinal=lon)), \
             mock.patch('openpilot.selfdrive.controls.controlsd.current_native', return_value=SimpleNamespace(
               requestedLateral=lat, lateralAllowed=lat, requestedLongitudinal=lon, longitudinalAllowed=lon)):
          for _ in range(10):
            now = 1_000_000_000 + tick * 10_000_000
            feed(controls, now, tick, active=lat, enabled=lon)
            command, _ = controls.state_control()
            tick += 1
          self.assertEqual((command.latActive, command.longActive), (lat, lon))
          self.assertIsNotNone(controls.last_lane_centering_result)
          self.assertEqual(controls.last_lane_centering_result.correction > 0.0, lat)
      with mock.patch('openpilot.selfdrive.controls.controlsd.current_axis', return_value=SimpleNamespace(
             sessionId='test-session', nativeAcknowledged=True, lateralActive=True, desiredLateral=True,
             longitudinalActive=False, desiredLongitudinal=False)), \
           mock.patch('openpilot.selfdrive.controls.controlsd.current_native', return_value=SimpleNamespace(
             requestedLateral=True, lateralAllowed=True, requestedLongitudinal=False, longitudinalAllowed=False)):
        now = 1_000_000_000 + tick * 10_000_000
        feed(controls, now, tick, active=False, enabled=False, model_age=200_000_000)
        command, _ = controls.state_control()
        self.assertTrue(command.latActive)
        self.assertFalse(command.longActive)
        self.assertIsNone(controls.last_lane_centering_result)
        for recovery_tick in range(tick + 1, tick + 6):
          now = 1_000_000_000 + recovery_tick * 10_000_000
          feed(controls, now, recovery_tick, active=False, enabled=False)
          controls.state_control()
        self.assertGreater(controls.last_lane_centering_result.correction, 0.0)

  def test_host_refusal_classification_preserves_admission_and_expires(self):
    with OpenpilotPrefix():
      params = Params()
      params.put_bool('LaneCentering', True, block=True)
      host = LaneCenteringHost(params)
      now = 1_000_000_000

      def sample(frame, at=now):
        return host.sample(frame, now_ns=at, lateral_active=True, longitudinal_active=True)

      def recovered():
        self.assertIsNotNone(sample(Frame(now)))
        self.assertEqual(host.last_refusal, 'inactive')

      recovered()
      # Use the actual subscriber methods, including their ignore policy.
      sm = messaging.SubMaster(list(host.REQUIRED), frequency=100)
      for service in host.REQUIRED:
        sm.alive[service] = sm.freq_ok[service] = sm.valid[service] = True
        sm.logMonoTime[service] = now
      car_state = messaging.new_message('carState', valid=True)
      car_state.carState.canValid = True
      sm.data['carState'] = car_state.as_reader().carState
      for flag, reason in (('alive', 'not_alive'), ('freq_ok', 'frequency'), ('valid', 'invalid')):
        for service in host.REQUIRED:
          if flag == 'freq_ok' and service == 'selfdriveState':
            continue
          with self.subTest(flag=flag, service=service):
            getattr(sm, flag)[service] = False
            self.assertIsNone(sample(sm))
            self.assertEqual(host.last_refusal, f'host_{service}_{reason}')
            getattr(sm, flag)[service] = True
            self.assertIsNotNone(sample(sm))
            self.assertEqual(host.last_refusal, 'inactive')
      for ignore, flag in (('ignore_alive', 'alive'), ('ignore_average_freq', 'freq_ok'), ('ignore_valid', 'valid')):
        getattr(sm, ignore).append('modelV2')
        getattr(sm, flag)['modelV2'] = False
        self.assertIsNotNone(sample(sm))
        self.assertEqual(host.last_refusal, 'inactive')
        getattr(sm, flag)['modelV2'] = True
        getattr(sm, ignore).remove('modelV2')
      for service in host.REQUIRED:
        for stamp, reason in ((0, 'missing_time'), (now + 1, 'future'), (now - 200_000_000, 'stale')):
          frame = Frame(now)
          frame.logMonoTime[service] = stamp
          self.assertIsNone(sample(frame))
          self.assertEqual(host.last_refusal, f'host_{service}_{reason}')
          recovered()
      frame = Frame(now)
      frame.checked = False
      self.assertIsNone(sample(frame))
      self.assertEqual(host.last_refusal, 'host_checks')
      recovered()
      for valid, timeout, reason in ((False, False, 'host_can_invalid'), (True, True, 'host_can_timeout')):
        frame = Frame(now)
        frame.state.canValid, frame.state.canTimeout = valid, timeout
        self.assertIsNone(sample(frame))
        self.assertEqual(host.last_refusal, reason)
        recovered()
      self.assertIsNone(sample(Frame(0), 0))
      self.assertEqual(host.last_refusal, 'host_clock')
      recovered()
      params.put_bool('LaneCentering', False, block=True)
      self.assertIsNone(sample(Frame(now * 2), now * 2))
      self.assertEqual(host.last_refusal, 'host_disabled')

  def test_actual_controls_publishes_host_refusal_and_clears_it_on_recovery(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'REPLAY': '1'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      publish_corolla_params(params)
      params.put_bool('LaneCentering', True, block=True)
      params.put('LaneCenteringE2EAuthority', 0., block=True)
      controls = Controls()
      tick = 0
      for tick in range(60):
        feed(controls, 1_000_000_000 + tick * 10_000_000, tick)
        controls.state_control()
      for flag, reason in (('alive', 'not_alive'), ('freq_ok', 'frequency'), ('valid', 'invalid')):
        for service in LaneCenteringHost.REQUIRED:
          if flag == 'freq_ok' and service == 'selfdriveState':
            continue
          tick += 1
          feed(controls, 1_000_000_000 + tick * 10_000_000, tick)
          getattr(controls.sm, flag)[service] = False
          command, lateral_log = controls.state_control()
          self.assertIsNone(controls.last_lane_centering_result)
          self.assertEqual(controls.lane_centering_applied, 0.)
          controls.pm.send.reset_mock()
          controls.publish(command, lateral_log)
          feedback = [call.args[1] for call in controls.pm.send.call_args_list if call.args[0] == 'starpilotLateralState'][-1]
          state = feedback.starpilotLateralState.laneCentering
          self.assertEqual(state.reason, f'host_{service}_{reason}')
          self.assertEqual((state.requestedCorrection, state.appliedCorrection), (0., 0.))
          getattr(controls.sm, flag)[service] = True
          tick += 1
          feed(controls, 1_000_000_000 + tick * 10_000_000, tick)
          command, lateral_log = controls.state_control()
          self.assertIsNotNone(controls.last_lane_centering_result)
          self.assertEqual(controls.lane_centering_inactive_reason, 'inactive')
          controls.pm.send.reset_mock()
          controls.publish(command, lateral_log)
          feedback = [call.args[1] for call in controls.pm.send.call_args_list if call.args[0] == 'starpilotLateralState'][-1]
          self.assertEqual(feedback.starpilotLateralState.laneCentering.reason, controls.last_lane_centering_result.reason)

  def test_actual_controls_lane_change_has_zero_added_and_retained_contribution(self):
    from openpilot.cereal import log
    for state in (log.LaneChangeState.preLaneChange, log.LaneChangeState.laneChangeStarting,
                  log.LaneChangeState.laneChangeFinishing):
      for signal in (False, True):
        for pause in (False, True):
          with self.subTest(state=state, signal=signal, pause=pause), OpenpilotPrefix(), \
               mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'REPLAY': '1'}), \
               mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
            params = Params()
            publish_corolla_params(params)
            params.put_bool('LaneCentering', True, block=True)
            params.put_bool('LaneCenteringPauseOnSignal', pause, block=True)
            params.put('LaneCenteringE2EAuthority', 0., block=True)
            reference, candidate = Controls(), Controls()
            for tick in range(60):
              now = 1_000_000_000 + tick * 10_000_000
              for controls in (reference, candidate):
                feed(controls, now, tick)
                controls.state_control()
            self.assertGreater(candidate.last_lane_centering_result.correction, 0.)
            self.assertEqual(candidate.desired_curvature, reference.desired_curvature)
            # Both native controllers have the same complete centering history.
            reference.lane_centering_host = None
            changed_model = model()
            changed_model.meta.laneChangeState = state
            with mock.patch(f'{__name__}.model', return_value=changed_model):
              for tick in range(60, 70):
                now = 1_000_000_000 + tick * 10_000_000
                feed(reference, now, tick, signal=signal)
                feed(candidate, now, tick, signal=signal)
                nominal, _ = reference.state_control()
                command, _ = candidate.state_control()
                self.assertEqual(command.to_dict(), nominal.to_dict())
                self.assertEqual(candidate.desired_curvature, reference.desired_curvature)
                self.assertEqual(candidate.last_lane_centering_result.reason, 'lane_change')
                self.assertEqual(candidate.last_lane_centering_result.correction, 0.)
                self.assertEqual(candidate.lane_centering_applied, 0.)
                self.assertEqual(candidate.lane_centering_controller._correction, 0.)
                self.assertIsNone(candidate.lane_centering_controller._tracker.path)


  def test_fresh_polled_state_with_slow_consumer_keeps_actual_centering(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'REPLAY': '1'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      publish_corolla_params(params)
      params.put_bool('LaneCentering', True, block=True)
      params.put('LaneCenteringE2EAuthority', 0., block=True)
      reference, candidate = Controls(), Controls()
      for tick in range(100):
        now = 1_000_000_000 + tick * 10_000_000
        feed(reference, now, tick)
        feed(candidate, now, tick)
        if tick >= 30:
          candidate.sm.freq_ok['selfdriveState'] = False
        nominal, _ = reference.state_control()
        command, _ = candidate.state_control()
        self.assertEqual(command.to_dict(), nominal.to_dict())
        if tick >= 30:
          self.assertIsNotNone(candidate.last_lane_centering_result)
          self.assertGreater(candidate.lane_centering_applied, 0.)
      for age in (25_000_001, -1):
        tick += 1
        now = 1_000_000_000 + tick * 10_000_000
        feed(candidate, now, tick)
        candidate.sm.freq_ok['selfdriveState'] = False
        candidate.sm.logMonoTime['selfdriveState'] = now - age
        candidate.state_control()
        self.assertIsNone(candidate.last_lane_centering_result)
        self.assertEqual(candidate.lane_centering_applied, 0.)
        self.assertEqual(candidate.lane_centering_controller._correction, 0.)
      for flag in ('alive', 'valid'):
        tick += 1
        now = 1_000_000_000 + tick * 10_000_000
        feed(candidate, now, tick)
        candidate.sm.freq_ok['selfdriveState'] = False
        getattr(candidate.sm, flag)['selfdriveState'] = False
        candidate.state_control()
        self.assertIsNone(candidate.last_lane_centering_result)
        self.assertEqual(candidate.lane_centering_applied, 0.)
        getattr(candidate.sm, flag)['selfdriveState'] = True

  def test_actual_single_line_cold_start_moves_from_biased_path_and_clears_for_lane_change(self):
    from openpilot.starpilot.lateral.tests.test_lane_centering import single
    for missing_right, sign in ((True, 1), (False, -1)):
      with self.subTest(missing_right=missing_right), OpenpilotPrefix(), \
           mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'REPLAY': '1'}), \
           mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
        params = Params()
        publish_corolla_params(params)
        params.put_bool('LaneCentering', True, block=True)
        params.put('LaneCenteringE2EAuthority', 0., block=True)
        candidate = Controls()
        m = single(missing_right=missing_right, line=-1.5, edge=10.)
        with mock.patch(f'{__name__}.model', return_value=m):
          for tick in range(100):
            feed(candidate, 1_000_000_000 + tick * 10_000_000, tick)
            candidate.state_control()
          self.assertGreater(sign * candidate.last_lane_centering_result.correction, 0.)
          self.assertGreater(sign * candidate.lane_centering_applied, 0.)
          self.assertIsNone(candidate.lane_centering_controller._tracker.width)
          assert candidate.lane_centering_controller._tracker.spacing is not None
          self.assertAlmostEqual(candidate.lane_centering_controller._tracker.spacing, 1.7)
          m.meta.laneChangeState = 'laneChangeStarting'
          feed(candidate, 2_000_000_000, 100)
          candidate.state_control()
          self.assertEqual(candidate.last_lane_centering_result.reason, 'lane_change')
          self.assertEqual(candidate.lane_centering_applied, 0.)
          self.assertEqual(candidate.lane_centering_controller._correction, 0.)
          self.assertIsNone(candidate.lane_centering_controller._tracker.path)


  def test_ordinary_centering_and_explicit_prediction_on_close_observed_edge(self):
    from opendbc.car.hyundai.tests.test_lane_body import identity
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '0', 'REPLAY': '1',
                                                        'AOL_REPLAY_RUNTIME': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      _, cp = ioniq_candidate()
      metadata = identity()
      cp.carVin, cp.carFw = metadata.carVin, [vars(fw) for fw in metadata.carFw]
      params.put('CarParams', cp.to_bytes(), block=True)
      params.put_bool('LaneCentering', True, block=True)
      params.put('LaneCenteringE2EAuthority', 0., block=True)
      ordinary = Controls()
      m = model(left=-1.2, right=1.8)
      m.init('roadEdges', 2)
      for edge, side in zip(m.roadEdges, (-1., 1.), strict=True):
        edge.x, edge.y = list(range(51)), [2.5 * side] * 51
      m.roadEdgeStds = [.17, 1.]
      with mock.patch(f'{__name__}.model', return_value=m):
        for tick in range(80):
          feed(ordinary, 1_000_000_000 + tick * 10_000_000, tick)
          ordinary.state_control()
      self.assertGreater(ordinary.lane_centering_applied, 0.)
      self.assertIsNone(ordinary.lane_centering_controller._body)
      predicted = Controls(predict_lane_clearance=True)
      self.assertIsNotNone(predicted.lane_centering_controller._body)
      with mock.patch(f'{__name__}.model', return_value=m):
        for tick in range(80):
          feed(predicted, 1_000_000_000 + tick * 10_000_000, tick)
          predicted.state_control()
      self.assertEqual(predicted.last_lane_centering_result.reason, 'clearance_limited')
      self.assertEqual(predicted.lane_centering_applied, 0.)
      self.assertEqual(predicted.CP.to_dict(), ordinary.CP.to_dict())

  def test_half_breakaway_defaults_reset_and_preserve_saved_preferences(self):
    from openpilot.starpilot.lateral.lane_centering import LaneCenteringSettings
    from openpilot.starpilot.ui.controller_feature import SETUP_DEFAULTS
    with OpenpilotPrefix():
      params = Params()
      key = 'LaneCenteringE2EAuthority'
      self.assertIsNone(params.get(key))
      self.assertEqual(params.get_default_value(key), .5)
      self.assertEqual(LaneCenteringSettings().e2e_authority, .5)
      self.assertEqual(read_settings(params).e2e_authority, .5)
      self.assertEqual(SETUP_DEFAULTS[key], b'0.5')
      self.assertIsNone(params.get(key))
      for value in (0., .3, 1.):
        params.put(key, value, block=True)
        before = Path(params.get_param_path(key)).read_bytes()
        self.assertEqual(read_settings(params).e2e_authority, value)
        self.assertEqual(Path(params.get_param_path(key)).read_bytes(), before)
        self.assertEqual(params.get_default_value(key), .5)
      params.remove(key)
      self.assertEqual(read_settings(params).e2e_authority, .5)
      self.assertIsNone(params.get(key))

  def test_saved_settings_bounds_and_corruption(self):
    with OpenpilotPrefix():
      params = Params()
      self.assertFalse(read_settings(params).enabled)
      self.assertTrue(read_settings(params).pause_on_signal)
      params.put_bool('LaneCentering', True, block=True)
      params.put('LaneCenterOffset', -0.2, block=True)
      params.put('LaneCenteringE2EAuthority', 0.5, block=True)
      settings = read_settings(params)
      self.assertEqual((settings.enabled, settings.offset_m, settings.e2e_authority), (True, -0.2, 0.5))
      for key, values in (('LaneCenterOffset', (b'bad', b'nan', b'inf', b'0.31')),
                          ('LaneCenteringE2EAuthority', (b'', b'1.01', b'-0.1')),
                          ('LaneCenteringPauseOnSignal', (b'false', b'2'))):
        path = Path(params.get_param_path(key))
        original = path.read_bytes() if path.exists() else None
        for value in values:
          with self.subTest(key=key, value=value):
            path.write_bytes(value)
            self.assertFalse(read_settings(params).enabled)
            self.assertTrue(params.get_bool('LaneCentering'))
        if original is None:
          path.unlink()
        else:
          path.write_bytes(original)

  def test_authority_is_derived_from_actual_axes(self):
    with OpenpilotPrefix():
      params = Params()
      params.put_bool('LaneCentering', True, block=True)
      host = LaneCenteringHost(params)
      for tick, (lat, lon, mode) in enumerate(((False, False, ControlMode.OFF), (False, True, ControlMode.LONGITUDINAL_ONLY),
                                             (True, False, ControlMode.LATERAL_ONLY), (True, True, ControlMode.COMBINED))):
        now = 1_000_000_000 + tick * 10_000_000
        request = host.sample(Frame(now), now_ns=now, lateral_active=lat, longitudinal_active=lon)
        self.assertIsNotNone(request)
        self.assertEqual(request.mode, mode)

  def test_stale_future_invalid_input_and_clock_gap(self):
    with OpenpilotPrefix():
      params = Params()
      params.put_bool('LaneCentering', True, block=True)
      host = LaneCenteringHost(params)
      now = 1_000_000_000
      def sample(frame, at=now):
        return host.sample(frame, now_ns=at, lateral_active=True, longitudinal_active=True)
      self.assertIsNotNone(sample(Frame(now)))
      for service in host.REQUIRED:
        for age in (200_000_000, -1):
          frame = Frame(now)
          frame.logMonoTime[service] = now - age
          self.assertIsNone(sample(frame))
      frame = Frame(now)
      frame.checked = False
      self.assertIsNone(sample(frame))
      frame.checked, frame.state.canValid = True, False
      self.assertIsNone(sample(frame))
      frame.state.canValid, frame.state.canTimeout = True, True
      self.assertIsNone(sample(frame))
      frame.state.canTimeout = False
      frame.logMonoTime['modelV2'] = 0
      self.assertIsNone(sample(frame))
      self.assertIsNone(sample(Frame(0), 0))
      gap = sample(Frame(now + 100_000_000), now + 100_000_000)
      self.assertTrue(gap.time_discontinuity)
      self.assertFalse(sample(Frame(now + 110_000_000), now + 110_000_000).time_discontinuity)
      self.assertTrue(sample(Frame(now), now).time_discontinuity)

  def test_live_settings_refresh_and_corrupt_setting_disables(self):
    with OpenpilotPrefix():
      params = Params()
      params.put_bool('LaneCentering', True, block=True)
      host = LaneCenteringHost(params)
      for now in (1_000_000_000, 1_010_000_000):
        self.assertIsNotNone(host.sample(Frame(now), now_ns=now, lateral_active=True, longitudinal_active=False))
      Path(params.get_param_path('LaneCenterOffset')).write_bytes(b'bad')
      now = 2_000_000_000
      self.assertIsNone(host.sample(Frame(now), now_ns=now, lateral_active=True, longitudinal_active=False))
      self.assertTrue(params.get_bool('LaneCentering'))

  def test_actual_controls_saved_settings_and_permission_gates(self):
    scenarios = ('normal', 'disabled_setting', 'off', 'long_only', 'fault', 'override', 'signal', 'stale', 'invalid_can', 'can_timeout')
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'REPLAY': '1'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      publish_corolla_params(params)
      for scenario in scenarios:
        with self.subTest(scenario=scenario):
          params.put_bool('LaneCentering', scenario != 'disabled_setting', block=True)
          params.put('LaneCenteringE2EAuthority', 0.0, block=True)
          with mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '0'}):
            baseline = Controls()
          with mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '1'}):
            candidate = Controls()
          self.assertIsNotNone(baseline.lane_centering_host)
          baseline.lane_centering_host = None  # Explicit no-feature reference; Toyota now uses the normal host.
          previous = 0.0
          for tick in range(120):
            now = 1_000_000_000 + tick * 10_000_000
            phase = scenario if tick >= 80 else 'normal'
            options = {'active': phase not in ('off', 'long_only'), 'enabled': phase != 'off',
                       'fault': phase == 'fault', 'override': phase == 'override', 'signal': phase == 'signal',
                       'model_age': 200_000_000 if phase == 'stale' else tick % 5 * 10_000_000,
                       'can_valid': phase != 'invalid_can', 'can_timeout': phase == 'can_timeout'}
            for controls in (baseline, candidate):
              feed(controls, now, tick, **options)
            reference, _ = baseline.state_control()
            command, _ = candidate.state_control()
            result = candidate.last_lane_centering_result
            self.assertEqual(command.latActive, reference.latActive)
            self.assertEqual(command.longActive, reference.longActive)
            self.assertEqual(command.actuators.accel, reference.actuators.accel)
            if scenario == 'disabled_setting':
              self.assertEqual(command.to_dict(), reference.to_dict())
            elif phase in ('off', 'long_only', 'fault', 'override'):
              self.assertEqual(result.correction, 0.0)
            elif phase in ('stale', 'invalid_can', 'can_timeout'):
              self.assertIsNone(result)
            elif phase == 'signal':
              self.assertLess(result.correction, previous)
            elif tick >= 30:
              # Wait for two delay packets; guarded correction may then settle.
              self.assertGreater(result.correction, 0.)
              self.assertGreater(command.actuators.curvature, reference.actuators.curvature)
            previous = result.correction if result is not None else 0.0

  def test_actual_aol_lateral_only_controls_without_longitudinal(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '1', 'LANE_CENTERING_REPLAY_RUNTIME': '1',
                                                         'REPLAY': '1'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      params.put_bool('LaneCentering', True, block=True)
      cp = HondaCarInterface.get_params(HondaCAR.HONDA_CIVIC_BOSCH, gen_empty_fingerprint(), [], True, False, False)
      cp.safetyConfigs[-1].safetyParam |= 0x20  # Card's qualified AOL bit in serialized CarParams.
      params.put('CarParams', cp.to_bytes(), block=True)
      controls = Controls()
      self.assertTrue(controls.aol_replay)
      self.assertIsNotNone(controls.lane_centering_host)
      for tick in range(40):
        now = 1_000_000_000 + tick * 10_000_000
        feed(controls, now, tick, active=False, enabled=False, aol_lat_only=True)
        command, _ = controls.state_control()
        self.assertFalse(command.enabled)
        self.assertTrue(command.latActive)
        self.assertFalse(command.longActive)
        self.assertEqual(command.actuators.accel, 0.0)
      assert controls.last_lane_centering_result is not None
      self.assertGreater(controls.last_lane_centering_result.correction, 0.0)
      previous = controls.last_lane_centering_result.correction
      now += 10_000_000
      feed(controls, now, 40, active=False, enabled=False, aol_lat_only=True, native_session='old-drive')
      command, _ = controls.state_control()
      self.assertFalse(command.latActive or command.longActive)
      self.assertEqual(controls.last_lane_centering_result.correction, 0.0)
      now += 10_000_000
      feed(controls, now, 41, active=False, enabled=False, aol_lat_only=True)
      command, _ = controls.state_control()
      self.assertTrue(command.latActive)
      self.assertFalse(command.longActive)
      self.assertLess(controls.last_lane_centering_result.correction, previous)
      for tick in range(42, 48):
        now += 10_000_000
        feed(controls, now, tick, active=False, enabled=False)
        command, _ = controls.state_control()
      self.assertFalse(command.latActive or command.longActive)
      self.assertEqual(controls.last_lane_centering_result.correction, 0.0)

  def test_stock_acc_lateral_stays_separate_from_aol(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'AOL_REPLAY_RUNTIME': '0', 'LANE_CENTERING_REPLAY_RUNTIME': '1',
                                                         'REPLAY': '1'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      params.put_bool('LaneCentering', True, block=True)
      cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
      cp.openpilotLongitudinalControl = False
      cp.pcmCruise = True
      params.put('CarParams', cp.to_bytes(), block=True)
      controls = Controls()
      self.assertFalse(controls.aol_replay)
      for tick in range(40):
        now = 1_000_000_000 + tick * 10_000_000
        feed(controls, now, tick)
        command, _ = controls.state_control()
        self.assertTrue(command.latActive)
        self.assertFalse(command.longActive)
      assert controls.last_lane_centering_result is not None
      self.assertGreater(controls.last_lane_centering_result.correction, 0.0)
