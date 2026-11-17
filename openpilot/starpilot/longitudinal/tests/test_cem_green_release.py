"""CEM stationary model-clear release through the planner and mode consumer."""
from types import SimpleNamespace
import json
import tempfile
import unittest

from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.cereal import log, messaging
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import apply_conditional_stop_hold
from openpilot.starpilot.conditional_mode.consumer import ModeConsumer
from openpilot.starpilot.conditional_mode.planner_host import ConditionalPlannerHost
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.runtime_settings import DOCUMENT_KEY
from openpilot.starpilot.conditional_mode.status import settings_fingerprint
from openpilot.starpilot.conditional_mode.tests.test_projection import BOOT, MONO, FakeSubMaster, serialized_scene
from openpilot.starpilot.conditional_mode.tests.test_stop_commit_transport import RED_97_X, RED_97_V

# Exact recorded segment64 model at4044.285490106; no color inference.
CLEAR_64_X = (0.0,
 0.0,
 0.00042629241943359375,
 -7.086992263793945e-05,
 0.0029163360595703125,
 0.007503509521484375,
 0.0150146484375,
 0.038543701171875,
 0.07806396484375,
 0.1441650390625,
 0.25537109375,
 0.45263671875,
 0.74267578125,
 1.1796875,
 1.7470703125,
 2.541015625,
 3.560546875,
 4.78515625,
 6.24609375,
 7.93359375,
 9.828125,
 11.9140625,
 13.96875,
 16.25,
 18.40625,
 20.546875,
 22.59375,
 24.3125,
 25.9375,
 27.5,
 28.984375,
 30.328125,
 31.59375)
CLEAR_64_V = (-0.001239776611328125,
 0.004302978515625,
 0.01226806640625,
 0.012725830078125,
 0.033294677734375,
 0.07177734375,
 0.125732421875,
 0.205322265625,
 0.34033203125,
 0.5322265625,
 0.78125,
 1.103515625,
 1.5,
 1.9453125,
 2.453125,
 2.96484375,
 3.525390625,
 4.05859375,
 4.55859375,
 5.04296875,
 5.51171875,
 5.94140625,
 6.35546875,
 6.765625,
 7.1953125,
 7.65625,
 8.2421875,
 8.78125,
 9.3671875,
 10.03125,
 10.6953125,
 11.3671875,
 12.015625)
CLEAR_64_ACCEL = 0.5698226690292358


class CallerFrame(FakeSubMaster):
  def __init__(self, payloads, stamp):
    super().__init__(payloads, stamp)
    for name in payloads:
      self.logMonoTime[name] = stamp
      self.recv_time[name] = stamp / 1e9
      self.seen[name] = self.valid[name] = self.alive[name] = True

  def all_checks(self, services):
    return all(self.valid[name] and self.alive[name] for name in services)


class CemGreenCallerTests(unittest.TestCase):
  def setUp(self):
    directory = self.enterContext(tempfile.TemporaryDirectory())
    self.params = Params(directory)
    self.params.put(DOCUMENT_KEY, json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CEM))), block=True)
    self.cp = CarInterface.get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    self.cp.openpilotLongitudinalControl = True
    self.owner = ConditionalPlannerHost(self.params)
    self.addCleanup(self.owner.close)
    self.consumer = ModeConsumer()
    self.planner = LongitudinalPlanner(self.cp, init_v=4.27)
    self.tick = 0
    self.experimental = False
    self.drive = MONO - 1_000_000_000

  def cycle(self, *, stopped=False, position=None, velocity=None, should_stop=True,
            personality=log.LongitudinalPersonality.aggressive, unavailable=None, stale=None,
            repeat_model=False, lead=False, desired_accel=None, horizon=20., force_stop_owner=None, resume=False, aol=False, starting=False):
    now = MONO + self.tick * 50_000_000
    self.tick += 1
    stamp = now - 2_000_000
    boot = BOOT + now - MONO
    payloads = serialized_scene(stamp=boot - 2_000_000, speed=0. if stopped else 4.27,
                               horizon=horizon, standstill=stopped, lead_present=lead,
                               experimental_mode=self.experimental)
    for name in ('modelV2', 'controlsState', 'selfdriveState', 'carControl', 'carState'):
      event = messaging.new_message(name, valid=True)
      setattr(event, name, payloads[name])
      value = getattr(event, name)
      if name == 'modelV2':
        value.action.shouldStop = should_stop
        value.action.desiredAcceleration = (desired_accel if desired_accel is not None else
                                           -.2 if should_stop else CLEAR_64_ACCEL)
        value.orientationRate.z = [0.] * 33
        value.meta.disengagePredictions.gasPressProbs = [1.] * 6
        if position is not None:
          value.position.x = position
        if velocity is not None:
          value.velocity.x = velocity
      elif name == 'controlsState':
        value.curvature = 0.
        value.longControlState = LongCtrlState.starting if starting else LongCtrlState.stopping if stopped else LongCtrlState.pid
      elif name == 'selfdriveState':
        value.personality = personality
        if aol:
          value.enabled = False
      elif name == 'carControl':
        value.orientationNED = [0.] * 3
        if aol:
          value.enabled = value.longActive = False
          value.latActive = True
      elif name == 'carState':
        value.vEgoRaw = 0. if stopped else 4.27
      payloads[name] = getattr(messaging.log_from_bytes(event.to_bytes()), name)
    vehicle = messaging.new_message('vehicleParameters', valid=True)
    payloads['vehicleParameters'] = messaging.log_from_bytes(vehicle.to_bytes()).vehicleParameters
    sm = CallerFrame(payloads, stamp)
    if unavailable is not None:
      sm.alive[unavailable] = False
    if stale is not None:
      sm.logMonoTime[stale] = now - 120_000_000
      sm.recv_time[stale] = (now - 120_000_000) / 1e9
    if repeat_model:
      sm.logMonoTime['modelV2'] = stamp - 50_000_000
      sm.recv_time['modelV2'] = (stamp - 50_000_000) / 1e9
    provider = None
    if force_stop_owner is not None:
      event = messaging.new_message('carState', valid=True)
      event.logMonoTime = stamp
      event.carState = sm['carState']
      if resume:
        event.carState.buttonEvents = [{'type': 'accelCruise', 'pressed': True}]
      force_stop_owner.resume.observe(event, now_ns=now, drive_id=self.drive)
      def provider(follow):
        return force_stop_owner.sample(sm, self.cp, now_ns=now, now_boot_ns=boot,
                                       drive_id=self.drive, follow_seconds=follow, traffic_mode=False)
    self.planner.update(sm, now_ns=now, drive_id=self.drive, force_stop_provider=provider)
    self.assertEqual(self.planner.mpc.solution_status, 0)
    proposal, snapshot = self.owner.sample(sm, self.cp, self.planner, now_mono_ns=now,
      now_boot_ns=boot, sample_skew_ns=1000, drive_id=self.drive, native_plan_valid=sm.all_checks(payloads))
    held = apply_conditional_stop_hold(self.owner, self.planner, sm, self.cp, now, self.drive, now_boot_ns=boot)
    event = self.owner.status.attach(None, proposal, snapshot, now_ns=now, drive_id=self.drive,
                                    model_ns=stamp, car_state_ns=stamp)
    status = messaging.log_from_bytes(event.to_bytes()).slcState
    result = self.consumer.sample(status, now_ns=now, now_boot_ns=boot, sample_skew_ns=1000,
      message_ns=now, receipt_ns=now, drive_id=self.drive, model_ns=stamp, car_state_ns=stamp,
      authority=unavailable is None, stock_experimental=False, choice=proposal.choice,
      settings_fingerprint=settings_fingerprint(snapshot))
    self.experimental = result.experimental
    published = []
    self.planner.publish(sm, SimpleNamespace(send=lambda service, value: published.append(value)
                                             if service == 'longitudinalPlan' else None))
    return proposal, result, held, published[-1].longitudinalPlan

  def prime(self):
    for _ in range(45):
      self.cycle()
    self.assertTrue(self.owner.mode.projector.stop_detector.committed)
    for _ in range(12):
      _, result, held, plan = self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V)
      self.assertTrue(result.experimental and held and plan.shouldStop)
      self.assertLessEqual(plan.aTarget, 0.)
    self.assertTrue(self.owner.mode.projector.stop_detector.standstill_committed)

  def test_red_hold_and_clear_release_reach_actual_planner_publication(self):
    self.prime()
    for tick in range(18):
      _, result, held, plan = self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False)
      if tick < 10:
        self.assertTrue(result.experimental and held and plan.shouldStop)
      elif tick >= 12:
        self.assertFalse(result.experimental or held or plan.shouldStop)
    self.assertFalse(self.owner.mode.projector.stop_detector.committed)
    self.assertFalse(held or result.experimental or plan.shouldStop)
    self.assertGreater(plan.aTarget, 0.)

  def test_missing_source_and_transient_clear_cannot_release_stop(self):
    self.prime()
    for _ in range(6):
      self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False)
    self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False, unavailable='modelV2')
    for _ in range(6):
      _, result, held, plan = self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False)
      self.assertTrue(result.experimental and held and plan.shouldStop)
    self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V)
    self.assertTrue(self.owner.mode.projector.stop_detector.standstill_committed)

  def test_alive_transport_gap_and_repeated_model_restart_clear_dwell(self):
    self.prime()
    for changes in ({'stale': 'radarState'}, {'repeat_model': True}):
      for _ in range(6):
        self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False)
      self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False, **changes)
      for _ in range(6):
        _, result, held, plan = self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False)
        self.assertTrue(result.experimental and held and plan.shouldStop)
      self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V)

  def test_should_stop_veto_and_short_geometry_cannot_be_false_clear(self):
    self.prime()
    for _ in range(18):
      _, result, held, plan = self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V)
      self.assertTrue(result.experimental and held and plan.shouldStop)
    for _ in range(18):
      _, result, held, plan = self.cycle(stopped=True, horizon=52., should_stop=False)
      self.assertTrue(result.experimental and held and plan.shouldStop)

  def test_personality_changes_preserve_red_hold_and_allow_clear_release(self):
    self.prime()
    personalities = (log.LongitudinalPersonality.aggressive, log.LongitudinalPersonality.standard,
                     log.LongitudinalPersonality.relaxed)
    for personality in personalities:
      _, result, held, plan = self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V,
                                        personality=personality)
      self.assertTrue(result.experimental and held and plan.shouldStop)
    for tick in range(18):
      _, result, held, plan = self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V,
                                        should_stop=False, personality=personalities[tick % 3])
    self.assertFalse(held or result.experimental or plan.shouldStop)

  def test_stationary_launch_requires_complete_positive_tail_and_no_lead(self):
    self.prime()
    damaged_position = list(CLEAR_64_X)
    damaged_position[12] = damaged_position[11] - .1
    cases = ({'velocity': [0.] * 33}, {'velocity': [0.] * 32},
             {'velocity': list(CLEAR_64_V[:-1]) + [0.]}, {'velocity': list(CLEAR_64_V[:-1]) + [float('nan')]},
             {'desired_accel': 0.}, {'lead': True}, {'position': damaged_position})
    for changes in cases:
      for _ in range(14):
        arguments = {'stopped': True, 'position': CLEAR_64_X, 'velocity': CLEAR_64_V, 'should_stop': False}
        arguments.update(changes)
        _, result, held, plan = self.cycle(**arguments)
        self.assertTrue(result.experimental and held and plan.shouldStop)
    for _ in range(18):
      _, result, held, plan = self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False)
    self.assertFalse(held or result.experimental or plan.shouldStop)

  def test_launch_proof_does_not_clear_moving_stop_or_remaining_distance(self):
    self.prime()
    for _ in range(18):
      _, result, _, _ = self.cycle(position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False)
      self.assertTrue(result.experimental)
      self.assertTrue(self.owner.mode.projector.stop_detector.committed)
    for _ in range(18):
      _, result, held, plan = self.cycle(stopped=True, horizon=4., velocity=CLEAR_64_V, should_stop=False)
      self.assertTrue(result.experimental and held and plan.shouldStop)

  def test_cold_clear_trajectory_cannot_acquire_committed_stop(self):
    for _ in range(18):
      self.cycle(stopped=True, position=CLEAR_64_X, velocity=CLEAR_64_V, should_stop=False)
      self.assertFalse(self.owner.mode.projector.stop_detector.committed)
      self.assertFalse(self.owner.mode.projector.stop_detector.standstill_committed)


  def test_res_releases_existing_force_and_cem_holds_together(self):
    from openpilot.starpilot.longitudinal.force_stop_runtime import ForceStopRuntime
    self.params.put_bool('ForceStops', True, block=True)
    force = ForceStopRuntime(self.params)
    for _ in range(45):
      self.cycle(force_stop_owner=force)
    for _ in range(12):
      _, _, held, plan = self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V, force_stop_owner=force)
    self.assertTrue(held and plan.shouldStop and self.planner.force_stop_plan.manual_hold)
    self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V, force_stop_owner=force, resume=True)
    for _ in range(20):
      _, _, held, plan = self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V, force_stop_owner=force)
      self.assertFalse(held)
    self.assertTrue(self.planner.force_stop_plan.resume_override)
    self.assertFalse(plan.shouldStop)
    self.assertGreaterEqual(plan.aTarget, .1)

  def test_res_engagement_from_aol_preserves_red_light_hold(self):
    from openpilot.starpilot.longitudinal.force_stop_runtime import ForceStopRuntime
    self.params.put_bool('ForceStops', True, block=True)
    force = ForceStopRuntime(self.params)
    for _ in range(45):
      self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V, force_stop_owner=force, aol=True)
    self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V, force_stop_owner=force, resume=True)
    for _ in range(20):
      _, _, _, plan = self.cycle(stopped=True, position=RED_97_X, velocity=RED_97_V, force_stop_owner=force)
      self.assertFalse(self.planner.force_stop_plan.resume_override)
      self.assertTrue(plan.shouldStop)
      self.assertLessEqual(plan.aTarget, 0.)
