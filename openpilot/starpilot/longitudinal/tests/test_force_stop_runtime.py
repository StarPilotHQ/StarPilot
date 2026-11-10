from pathlib import Path
import tempfile
import unittest

import numpy as np
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import update_curve_frame
from openpilot.starpilot.curve_speed.host import CurveHost
from openpilot.starpilot.longitudinal.force_stop import StopPlan
from openpilot.starpilot.longitudinal.force_stop_runtime import ForceStopRuntime, dashboard_stop_sign
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages, snapshot
from openpilot.starpilot.longitudinal.tests.test_stop_resume import event as button_event

BASE = 100_000_000_000
DRIVE = BASE - 1_000_000_000


class Frame(dict):
  def __init__(self, tick, *, horizon=40., stopped=False):
    super().__init__(messages()[0])
    self.stamp = BASE + tick * 50_000_000
    self.logMonoTime = dict.fromkeys(self, self.stamp)
    self.recv_time = dict.fromkeys(self, self.stamp / 1e9)
    self.valid = dict.fromkeys(self, True)
    self.alive = dict.fromkeys(self, True)
    self['carState'].vEgo = 0. if stopped else 10.
    self['carState'].standstill = stopped
    self['carState'].canValid = True
    self['carControl'].longActive = True
    model = self['modelV2']
    model.timestampEof = self.stamp + 2_000_000_000
    model.position.x = [horizon * index / 32 for index in range(33)]
    model.position.y = [0.] * 33
    model.orientationRate.z = [0.] * 33
    model.orientationRate.t = [float(index) / 10 for index in range(33)]
    model.velocity.x = [10.] * 33


class ForceStopRuntimeTests(unittest.TestCase):
  def setUp(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    self.params = Params(temp.name)
    self.params.put_bool('QOLLongitudinal', True, block=True)
    self.params.put_bool('ForceStops', True, block=True)
    self.cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    self.owner = ForceStopRuntime(self.params)

  def sample(self, sm, **changes):
    args: dict = {'now_ns': sm.stamp + 1_000_000, 'now_boot_ns': sm.stamp + 2_001_000_000,
            'drive_id': DRIVE, 'follow_seconds': 1.45, 'traffic_mode': False}
    args.update(changes)
    return self.owner.sample(sm, self.cp, **args)

  def prime(self):
    for tick in range(40):
      output = self.sample(Frame(tick))
    self.assertTrue(output.forcing)
    return output

  def test_distance_braking_requires_exact_ioniq_control_profile(self):
    from unittest.mock import patch
    from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import candidate

    stock, admitted = candidate()
    stock.openpilotLongitudinalControl = True  # An incomplete profile must still refuse the new tune.
    for cp, expected in [(self.cp, False), (stock, False), (admitted, True)]:
      self.cp = cp
      self.owner = ForceStopRuntime(self.params)
      with patch.object(self.owner.policy, 'step', wraps=self.owner.policy.step) as step:
        for tick in range(12):
          self.sample(Frame(tick))
        self.assertTrue(step.called)
        self.assertEqual(step.call_args.args[1].distance_control, expected)

  def test_committed_stop_survives_valid_mode_settings_and_traffic_changes(self):
    import json
    from openpilot.starpilot.conditional_mode.policy import ModeChoice
    from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences
    self.prime()
    self.params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CCM))), block=True)
    for tick in range(40, 80):
      plan = self.sample(Frame(tick), traffic_mode=True)
      self.assertTrue(plan.forcing)
      assert plan.obstacle_m is not None
    self.owner = ForceStopRuntime(self.params)
    for tick in range(80, 120):
      self.assertFalse(self.sample(Frame(tick), traffic_mode=True).forcing)

  def test_default_off_and_saved_choices_are_not_overwritten(self):
    path = Path(self.params.get_param_path('ForceStops'))
    for raw, expected in ((None, False), (b'0', False), (b'1', True)):
      if raw is None:
        path.unlink(missing_ok=True)
      else:
        path.write_bytes(raw)
      self.owner = ForceStopRuntime(self.params)
      for tick in range(40):
        output = self.sample(Frame(tick))
      self.assertEqual(output.forcing, expected)
      self.assertEqual(path.read_bytes() if path.exists() else None, raw)

  def test_resume_releases_current_stop_once_without_changing_longitudinal_authority(self):
    self.prime()
    self.owner.resume.observe(button_event(197), now_ns=BASE + 2_000_000_001, drive_id=DRIVE)
    self.owner.resume.observe(button_event(198, [('resumeCruise', True)]), now_ns=BASE + 2_000_000_001, drive_id=DRIVE)
    self.owner.resume.observe(button_event(199, [('resumeCruise', False)]), now_ns=BASE + 2_000_000_001, drive_id=DRIVE)
    for tick in range(40, 240):
      output = self.sample(Frame(tick))
      self.assertFalse(output.forcing)
      self.assertIsNone(output.obstacle_m)
      self.assertIsNone(output.speed_ceiling_mps)
    assert self.sample(Frame(240)).speed_ceiling_mps is not None

  def test_resume_during_manual_brake_or_aol_does_not_carry_into_long_engagement(self):
    for manual in ('brake', 'aol'):
      with self.subTest(manual=manual):
        self.owner = ForceStopRuntime(self.params)
        self.prime()
        self.owner.resume.observe(button_event(197), now_ns=BASE + 2_000_000_001, drive_id=DRIVE)
        self.owner.resume.observe(button_event(198, [('accelCruise', True)]), now_ns=BASE + 2_000_000_001, drive_id=DRIVE)
        frame = Frame(40, stopped=True)
        if manual == 'brake':
          frame['carState'].brakePressed = True
        else:
          frame['carControl'].longActive = False
          frame['carControl'].latActive = True
        self.assertFalse(self.sample(frame).forcing)
        self.assertTrue(self.sample(Frame(41, stopped=True)).should_stop)

  def test_recorded_stop_tail_invalidity_preserves_manual_hold_until_resume(self):
    self.prime()
    previous_distance = self.owner.plan.tracked_distance_m
    for tick in range(40, 70):
      sm = Frame(tick, horizon=26.18937873840332)
      # Actual predicted-stop tail: a 12 mm backwards position step, then
      # a following model event with -0.0208 m/s terminal velocity. Both
      # stay inadmissible geometry; neither is evidence that a light cleared.
      if tick % 2 == 0:
        sm['modelV2'].position.x = list(sm['modelV2'].position.x)[:-2] + [26.201595306396484, 26.18937873840332]
      else:
        sm['modelV2'].velocity.x = [10.] * 32 + [-0.02081957273185253]
      plan = self.sample(sm)
      self.assertTrue(plan.forcing)
      self.assertLessEqual(plan.tracked_distance_m, previous_distance)
      assert plan.speed_ceiling_mps is not None
      previous_distance = plan.tracked_distance_m
    for tick in range(70, 100):
      sm = Frame(tick, stopped=True)
      sm['modelV2'].position.x = [float('nan')] * 33
      plan = self.sample(sm)
      self.assertTrue(plan.should_stop)
      self.assertEqual(plan.speed_ceiling_mps, 0.)
    self.assertTrue(plan.forcing)
    self.assertTrue(plan.manual_hold)
    now = BASE + 5_000_000_000 + 1_000_000
    for item in (button_event(497), button_event(498, [('resumeCruise', True)]),
                 button_event(499, [('resumeCruise', False)])):
      self.owner.resume.observe(item, now_ns=now, drive_id=DRIVE)
    released = self.sample(Frame(100, horizon=190., stopped=True))
    self.assertFalse(released.manual_hold)
    self.assertFalse(released.forcing)
    self.assertIsNone(released.speed_ceiling_mps)

  def test_cold_invalid_geometry_cannot_acquire_stop(self):
    for tick in range(40):
      sm = Frame(tick)
      sm['modelV2'].velocity.x = [10.] * 32 + [-.02]
      self.assertFalse(self.sample(sm).forcing)

  def test_actual_model_and_saved_gate_reach_active_force_stop(self):
    output = self.prime()
    assert output.obstacle_m is not None
    self.assertEqual(output.jerk_scale, .32)
    self.params.put_bool('QOLLongitudinal', False, block=True)
    self.assertFalse(self.sample(Frame(60)).forcing)
    self.params.put_bool('QOLLongitudinal', True, block=True)
    self.params.put_bool('ForceStops', False, block=True)
    self.assertFalse(self.sample(Frame(80)).forcing)

  def test_bad_sources_and_driver_brake_never_hold_a_stop(self):
    for defect in ('gas', 'brake', 'can', 'off', 'stock', 'old_drive', 'stale', 'dead', 'eof', 'suspend'):
      with self.subTest(defect=defect):
        self.owner = ForceStopRuntime(self.params)
        self.prime()
        sm = Frame(40)
        args: dict = {}
        if defect in ('gas', 'brake'):
          setattr(sm['carState'], defect + 'Pressed', True)
        elif defect == 'can':
          sm['carState'].canValid = False
        elif defect == 'off':
          sm['carControl'].longActive = False
        elif defect == 'stock':
          self.cp.openpilotLongitudinalControl = False
        elif defect == 'old_drive':
          args['drive_id'] = sm.stamp + 1
        elif defect == 'stale':
          sm.logMonoTime['carState'] -= 150_000_001
        elif defect == 'dead':
          sm.alive['modelV2'] = False
        elif defect == 'eof':
          sm['modelV2'].timestampEof = 0
        elif defect == 'traffic':
          args['traffic_mode'] = True
        else:
          args['now_boot_ns'] = sm.stamp + 3_001_000_000
        plan = self.sample(sm, **args)
        self.assertFalse(plan.forcing)
        self.assertIsNone(plan.obstacle_m)
        self.cp.openpilotLongitudinalControl = True

  def test_aol_brake_approach_retains_perception_without_requesting_control(self):
    for tick in range(40):
      sm = Frame(tick)
      sm['carControl'].longActive = False
      sm['carControl'].latActive = True
      sm['selfdriveState'].enabled = False
      sm['carState'].brakePressed = True
      output = self.sample(sm)
      self.assertFalse(output.forcing)
      self.assertIsNone(output.speed_ceiling_mps)
    self.assertTrue(self.owner.light.light_detected)
    # At zero speed, the stopping-time threshold alone cannot newly detect a
    # light; the same current-drive detector carries the approach observation.
    sm = Frame(40, stopped=True, horizon=30.)
    sm['carControl'].latActive = True
    output = self.sample(sm)
    self.assertTrue(output.forcing)
    self.assertTrue(output.should_stop)
    self.assertEqual(output.speed_ceiling_mps, 0.)
    for tick in range(41, 80):
      output = self.sample(Frame(tick, stopped=True, horizon=30.))
      self.assertTrue(output.should_stop)
      self.assertTrue(output.manual_hold)
    for tick in range(80, 200):
      output = self.sample(Frame(tick, stopped=True, horizon=30.))
      self.assertFalse(output.forcing)
      self.assertFalse(output.manual_hold)

  def test_manual_hold_publishes_dedicated_bit_and_release_withdraws_same_cycle(self):
    from types import SimpleNamespace
    self.prime()
    planner = LongitudinalPlanner(self.cp)
    published = {}
    for tick, gas in ((40, False), (41, False), (42, True)):
      sm = Frame(tick, stopped=True, horizon=30. if tick == 40 else 192.)
      sm['carState'].gasPressed = gas
      sm.all_checks = lambda _: True
      planner.update(sm, now_ns=sm.stamp + 1_000_000, drive_id=DRIVE,
                     force_stop_provider=lambda follow, source=sm: self.sample(source, follow_seconds=follow))
      from unittest.mock import patch
      with patch('openpilot.cereal.messaging.time.monotonic', return_value=(sm.stamp + 1_000_000) / 1e9):
        planner.publish(sm, SimpleNamespace(send=lambda name, event: published.__setitem__(name, event)))
      event = published['longitudinalPlan']
      companion = published['starpilotLongitudinalPlan']
      hold = companion.starpilotLongitudinalPlan
      self.assertTrue(companion.valid)
      self.assertEqual(hold.version, 1)
      self.assertEqual(hold.sourcePlanMonoTime, event.logMonoTime)
      self.assertEqual(companion.logMonoTime, event.logMonoTime)
      self.assertEqual(hold.modelMonoTime, event.longitudinalPlan.modelMonoTime)
      self.assertEqual(hold.driveStartMonoTime, DRIVE)
      self.assertEqual(hold.forceStopHolding, not gas)
      self.assertEqual(planner.force_stop_plan.manual_hold, not gas)
      self.assertEqual(event.longitudinalPlan.modelMonoTime, sm.stamp)
      if not gas:
        self.assertTrue(event.longitudinalPlan.shouldStop)
        # The actual committed stop remains authoritative even when a legacy speed tail is positive.
        from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
        from opendbc.car.gm.values import CAR as GM_CAR
        from openpilot.selfdrive.controls.lib.longcontrol import LongControl
        from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
        from openpilot.starpilot.longitudinal.tests.test_gm_volt_cc_policy import fixture
        controls, _, _, _, _, _, offset = fixture()
        controls.CP = ordinary_params(GM_CAR.CHEVROLET_VOLT, radar=True)
        controls.LoC = LongControl(controls.CP)
        controls.vehicle_startup_preferences = VehicleStartupPreferences(volt_sng=True)
        event.longitudinalPlan.speeds = [2.] * 17
        controls.sm.data['longitudinalPlan'] = event.longitudinalPlan
        controls.sm['carState'].cruiseState.enabled = controls.sm['carState'].cruiseState.standstill = True
        controls.sm['deviceState'].startedMonoTime = sm.stamp - 1_000_000_000
        for name in ('carState', 'longitudinalPlan', 'deviceState'):
          controls.sm.logMonoTime[name] = sm.stamp - 10_000_000
          controls.sm.recv_time[name] = (sm.stamp - 1_000_000) / 1e9
        from openpilot.starpilot.longitudinal.inputs import ResumeFreshness
        controls.longitudinal_inputs.resume_freshness = ResumeFreshness()
        controls.longitudinal_inputs.resume_freshness.offset_ns = offset
        controls.longitudinal_inputs.resume_freshness.floor_ns = sm.stamp - 500_000_000
        from unittest.mock import patch
        from openpilot.cereal import messaging
        cc = messaging.new_message('carControl').carControl
        cc.enabled = cc.longActive = True
        lateral = messaging.new_message('controlsState').controlsState.lateralControlState.init('torqueState')
        with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(sm.stamp, sm.stamp + offset)):
          self.assertTrue(controls.longitudinal_inputs.resume_sources_current())
          controls.publish(cc, lateral)
        self.assertFalse(cc.cruiseControl.resume)
        self.assertTrue(event.longitudinalPlan.shouldStop)

  def test_saved_malformed_and_safe_mode_disable_instead_of_defaulting_on(self):
    for key, raw in (('ForceStops', b'true'), ('ForceStopDistanceOffset', b'21'),
                     ('ForceStopDistanceOffset', b'nan'), ('SafeMode', b'1'),
                     ('ConditionalModeConfig', b'{}')):
      with self.subTest(key=key, raw=raw):
        Path(self.params.get_param_path(key)).write_bytes(raw)
        self.owner = ForceStopRuntime(self.params)
        for tick in range(40):
          self.assertFalse(self.sample(Frame(tick)).forcing)
        Path(self.params.get_param_path(key)).unlink()

  def test_native_solver_gets_same_cycle_stop_and_defaults_remain_identical(self):
    for use_curve in (False, True):
      with self.subTest(use_curve=use_curve):
        self.owner = ForceStopRuntime(self.params)
        self.prime()
        selected = LongitudinalPlanner(self.cp, init_v=10)
        original = LongitudinalPlanner(self.cp, init_v=10)
        inactive = LongitudinalPlanner(self.cp, init_v=10)
        for tick in range(40, 65):
          sm = Frame(tick)
          def provider(follow, frame=sm):
            return self.sample(frame, follow_seconds=follow)
          host = CurveHost(enabled=False, replay=True) if use_curve else None
          update_curve_frame(selected, sm, self.cp, sm.stamp + 1_000_000, host=host, force_stop_provider=provider)
          original.update(sm, now_ns=sm.stamp + 1_000_000)
          inactive.update(sm, now_ns=sm.stamp + 1_000_000, force_stop_provider=lambda _: StopPlan())
          self.assertEqual(snapshot(original), snapshot(inactive))
          np.testing.assert_array_equal(original.mpc.params, inactive.mpc.params)
          np.testing.assert_array_equal(original.mpc.x_sol, inactive.mpc.x_sol)
          self.assertEqual(selected.mpc.solution_status, 0)
          assert selected.force_stop_plan.obstacle_m is not None
          np.testing.assert_array_equal(selected.mpc.params[:, 2], selected.force_stop_plan.obstacle_m + 6.)
          self.assertLessEqual(selected.output_a_target, original.output_a_target)
        # Native stop handoff is explicit even before acceleration alone reaches its threshold.
        sm = Frame(65)
        selected.update(sm, now_ns=sm.stamp + 1_000_000,
                        force_stop_provider=lambda _, stamp=sm.stamp: StopPlan(stamp, 0., None, True, True))
        self.assertTrue(selected.output_should_stop)
        self.assertEqual(selected.force_stop_plan.speed_ceiling_mps, 0.)

  def test_invalid_stop_tail_reaches_native_mpc_as_committed_obstacle(self):
    self.prime()
    selected = LongitudinalPlanner(self.cp, init_v=10)
    for tick in range(40, 55):
      sm = Frame(tick, horizon=26.18937873840332)
      sm['modelV2'].position.x = list(sm['modelV2'].position.x)[:-2] + [26.201595306396484, 26.18937873840332]
      selected.update(sm, now_ns=sm.stamp + 1_000_000,
                      force_stop_provider=lambda follow, frame=sm: self.sample(frame, follow_seconds=follow))
      plan = selected.force_stop_plan
      self.assertTrue(plan.forcing)
      assert plan.obstacle_m is not None
      np.testing.assert_array_equal(selected.mpc.params[:, 2], plan.obstacle_m + 6.)
      self.assertEqual(selected.mpc.solution_status, 0)
      self.assertLess(selected.output_a_target, 0.)

  def test_native_boundary_rejects_old_model_and_driver_override(self):
    for defect in ('old_model', 'gas', 'brake', 'off', 'force'):
      original, proposed = LongitudinalPlanner(self.cp, init_v=10), LongitudinalPlanner(self.cp, init_v=10)
      for tick in range(8):
        sm = Frame(tick)
        if defect in ('gas', 'brake'):
          setattr(sm['carState'], defect + 'Pressed', True)
        elif defect == 'off':
          sm['carControl'].longActive = False
        elif defect == 'force':
          sm['controlsState'].forceDecel = True
        stamp = sm.stamp - 1 if defect == 'old_model' else sm.stamp
        original.update(sm, now_ns=sm.stamp + 1_000_000)
        proposed.update(sm, now_ns=sm.stamp + 1_000_000,
                        force_stop_provider=lambda _, stamp=stamp: StopPlan(stamp, 0., 5., True, True, jerk_scale=.32))
        self.assertEqual(snapshot(original), snapshot(proposed))
        np.testing.assert_array_equal(original.mpc.params, proposed.mpc.params)

  def test_dashboard_stop_sign_requires_fresh_valid_companion(self):
    from openpilot.cereal import messaging
    message = messaging.new_message('starpilotCarState')
    message.starpilotCarState.dashboardStopSign = 1
    for defect, expected in ((None, True), ('stale', False), ('invalid', False), ('unseen', False), ('missing', False)):
      with self.subTest(defect=defect):
        sm = Frame(0)
        if defect != 'missing':
          sm['starpilotCarState'] = message.starpilotCarState
          sm.logMonoTime['starpilotCarState'] = sm.stamp
          sm.valid['starpilotCarState'] = defect != 'invalid'
        sm.seen = {'starpilotCarState': defect not in ('unseen', 'missing')}
        now = sm.stamp + (150_000_001 if defect == 'stale' else 1_000_000)
        self.assertEqual(dashboard_stop_sign(sm, now_ns=now, drive_id=DRIVE), expected)


if __name__ == '__main__':
  unittest.main()


  def test_resume_from_aol_engages_but_does_not_override_red_light_model(self):
    for same_tick in (False, True):
      with self.subTest(same_tick=same_tick):
        self.owner = ForceStopRuntime(self.params)
        planner = LongitudinalPlanner(self.cp)
        for tick in range(45):
          sm = Frame(tick, stopped=True, horizon=30.)
          sm['selfdriveState'].experimentalMode = True
          sm['modelV2'].action.shouldStop = True
          sm['modelV2'].action.desiredAcceleration = 0.
          if tick < 40:
            sm['carControl'].longActive = False
            sm['carControl'].latActive = True
            sm['selfdriveState'].enabled = False
          if tick == 39:
            self.owner.resume.observe(button_event(194), now_ns=BASE + 1_950_000_001, drive_id=DRIVE)
            self.owner.resume.observe(button_event(195, [('accelCruise', True)]), now_ns=BASE + 1_950_000_001, drive_id=DRIVE)
          if same_tick and tick == 40:
            self.owner.resume.observe(button_event(199), now_ns=BASE + 2_000_000_001, drive_id=DRIVE)
            self.owner.resume.observe(button_event(200, [('accelCruise', True)]), now_ns=BASE + 2_000_000_001, drive_id=DRIVE)
          planner.update(sm, now_ns=sm.stamp + 1_000_000, drive_id=DRIVE,
                         force_stop_provider=lambda follow, source=sm: self.sample(source, follow_seconds=follow))
          self.assertFalse(getattr(planner.force_stop_plan, 'resume_override', False))
          if tick >= 40:
            self.assertTrue(planner.output_should_stop)
            self.assertLessEqual(planner.output_a_target, 0.)

  def test_res_releases_preexisting_force_hold_without_overriding_other_stop_candidates(self):
    from dataclasses import replace
    planner = LongitudinalPlanner(self.cp)
    for tick in range(90):
      sm = Frame(tick, stopped=True, horizon=30.)
      sm['selfdriveState'].experimentalMode = True
      sm['modelV2'].action.shouldStop = True
      sm['modelV2'].action.desiredAcceleration = 0.
      planner.update(sm, now_ns=sm.stamp + 1_000_000, drive_id=DRIVE,
                     force_stop_provider=lambda follow, source=sm: self.sample(source, follow_seconds=follow))
    self.assertTrue(planner.output_should_stop)
    self.assertTrue(planner.force_stop_plan.manual_hold)
    self.owner.resume.observe(button_event(448), now_ns=BASE + 4_490_000_001, drive_id=DRIVE)
    self.owner.resume.observe(button_event(449, [('accelCruise', True)]), now_ns=BASE + 4_490_000_001, drive_id=DRIVE)
    for tick in range(90, 110):
      sm = Frame(tick, stopped=True, horizon=30.)
      sm['carState'].vEgo = -1e-8 if tick % 2 else 0.
      sm['selfdriveState'].experimentalMode = True
      sm['modelV2'].action.shouldStop = True
      sm['modelV2'].action.desiredAcceleration = 0.
      planner.update(sm, now_ns=sm.stamp + 1_000_000, drive_id=DRIVE,
                     force_stop_provider=lambda follow, source=sm: self.sample(source, follow_seconds=follow))
    self.assertTrue(planner.force_stop_plan.resume_override)
    self.assertFalse(planner.output_should_stop)
    self.assertGreaterEqual(planner.output_a_target, .1)
    resumed = planner.force_stop_plan
    for defect in ('lead1', 'lead2', 'brake', 'gas', 'force', 'inactive', 'stale', 'zero_target'):
      with self.subTest(defect=defect):
        sm = Frame(110, stopped=True)
        sm['selfdriveState'].experimentalMode = True
        sm['modelV2'].action.shouldStop = True
        sm['modelV2'].action.desiredAcceleration = 0.
        if defect.startswith('lead'):
          lead = sm['radarState'].leadOne if defect == 'lead1' else sm['radarState'].leadTwo
          lead.present = True
        elif defect in ('brake', 'gas'):
          setattr(sm['carState'], defect + 'Pressed', True)
        elif defect == 'force':
          sm['controlsState'].forceDecel = True
        elif defect == 'inactive':
          sm['carControl'].longActive = False
        elif defect == 'zero_target':
          sm['carState'].vCruise = 0.
        stamp = sm.stamp - 1 if defect == 'stale' else sm.stamp
        planner.update(sm, now_ns=sm.stamp + 1_000_000, drive_id=DRIVE,
                       force_stop_provider=lambda _, source=stamp: replace(resumed, model_ns=source))
        self.assertTrue(planner.output_should_stop)
