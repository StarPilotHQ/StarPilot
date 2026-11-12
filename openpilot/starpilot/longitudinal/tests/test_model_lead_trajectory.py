import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import numpy as np
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib import long_mpc as m
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages

NOW = 100_000_000_000
BOOT_OFFSET = 12_000_000_000

class Inputs(dict):
  def __init__(self, data):
    super().__init__(data)
    self.valid = dict.fromkeys(data, True)
    self.alive = dict(self.valid)
    self.freq_ok = dict(self.valid)
    self.logMonoTime = dict.fromkeys(data, NOW)
    self.recv_time = dict.fromkeys(data, NOW / 1e9)


def inputs(radar=True):
  data, envelopes = messages(lead=True)
  sm = Inputs(data)
  sm['carState'].canValid = True
  sm['carControl'].longActive = True
  sm['radarState'].radarErrors = {}
  sm['radarState'].mdMonoTime = NOW
  sm['modelV2'].timestampEof = NOW + BOOT_OFFSET - 20_000_000
  sm['modelV2'].velocity.x = [20.]
  model = sm['modelV2'].init('leadsV3', 2)
  for i, lead in enumerate(model):
    lead.prob = .99
    lead.t = m.LEAD_T_IDXS_MODEL.tolist()
    lead.x = [float(36. + 8. * i + m.RADAR_TO_CAMERA + 20. * t + .15 * t * t) for t in m.LEAD_T_IDXS_MODEL]
    lead.y = [0.] * len(m.LEAD_T_IDXS_MODEL)
    lead.v = [float(20. + .3 * t) for t in m.LEAD_T_IDXS_MODEL]
    sm['radarState'].leadOne.radar = radar
    sm['radarState'].leadTwo.radar = radar
    measured = sm['radarState'].leadOne if i == 0 else sm['radarState'].leadTwo
    measured.radarTrackId = i + 1 if radar else -1
    measured.modelProb = .99
    measured.vLead = 20.
    measured.vLeadK = 20.
    measured.vRel = 0.
    measured.dRel = 36. + 8. * i
  return sm, envelopes


class TestModelLeadTrajectory(unittest.TestCase):
  def setUp(self):
    clock = patch.object(m, '_model_lead_clock_pair_ns', return_value=(NOW, NOW + BOOT_OFFSET))
    clock.start()
    self.addCleanup(clock.stop)

  def planner(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    cp.openpilotLongitudinalControl = True
    return LongitudinalPlanner(cp, init_v=20.)

  def stopped_departure_inputs(self):
    sm, _ = inputs()
    sm['carState'].vEgo = 0.
    sm['carState'].aEgo = 0.
    sm['carState'].standstill = True
    sm['modelV2'].velocity.x = [0.]
    sm['controlsState'].longControlState = 'stopping'
    for i, model in enumerate(sm['modelV2'].leadsV3):
      model.x = [8.116, 9.58, 15.07, 24.75, 38.74, 56.94]
      model.v = [0., 1.46, 3.1, 5.2, 7.0, 8.5]
      lead = sm['radarState'].leadOne if i == 0 else sm['radarState'].leadTwo
      lead.dRel = 6.596
      lead.vLead = lead.vLeadK = lead.vRel = lead.aLeadK = 0.
    return sm

  def test_stopped_radar_does_not_borrow_predicted_departure(self):
    sm = self.stopped_departure_inputs()
    lead, model = sm['radarState'].leadOne, sm['modelV2'].leadsV3[0]
    mpc = self.planner().mpc
    mpc.set_cur_state(0., 0.)
    for speed in (0., .1, .49):
      lead.vLead = speed
      self.assertIsNone(m.build_model_lead_trajectory(model, lead, 0., 0.))
      np.testing.assert_array_equal(mpc.process_lead(lead, model, model_v_ego=0.), mpc.process_lead(lead))
    for speed in (.5, .8, 1.5):
      lead.vLead = speed
      self.assertIsNotNone(m.build_model_lead_trajectory(model, lead, 0., 0.))

  def test_actual_planner_and_longcontrol_keep_hold_for_predicted_departure(self):
    from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    actual, fallback = LongitudinalPlanner(cp, init_v=0.), LongitudinalPlanner(cp, init_v=0.)
    sm = self.stopped_departure_inputs()
    actual.update(sm, now_ns=NOW)
    with patch('openpilot.selfdrive.controls.lib.longitudinal_planner.qualified_model_leads', return_value=None):
      fallback.update(sm, now_ns=NOW)
    self.assertEqual(actual.mpc.solution_status, 0)
    np.testing.assert_array_equal(actual.mpc.params, fallback.mpc.params)
    self.assertTrue(actual.output_should_stop)
    control = LongControl(cp)
    control.long_control_state = LongCtrlState.stopping
    for _ in range(10):
      output = control.update(True, sm['carState'], actual.output_a_target, actual.output_should_stop, (-3.5, 2.))
      self.assertLess(output, 0.)
      self.assertEqual(control.long_control_state, LongCtrlState.stopping)

  def test_actual_planner_mpc_uses_both_model_horizons_without_changing_lead_owner(self):
    for radar in (True, False):
      with self.subTest(radar=radar):
        actual, legacy = self.planner(), self.planner()
        sm, _ = inputs(radar)
        seen = []
        original = actual.mpc.process_lead
        def capture(lead, model_lead=None, *, model_v_ego=None, original=original, seen=seen):
          result = original(lead, model_lead, model_v_ego=model_v_ego)
          seen.append((lead.radar, model_lead is not None, result.copy()))
          return result
        with patch.object(actual.mpc, 'process_lead', side_effect=capture):
          actual.update(sm, now_ns=NOW)
        with patch.object(m, 'qualified_model_leads', return_value=None), patch(
            'openpilot.selfdrive.controls.lib.longitudinal_planner.qualified_model_leads', return_value=None):
          legacy.update(sm, now_ns=NOW)
        self.assertEqual(actual.mpc.solution_status, 0)
        self.assertEqual([x[:2] for x in seen], [(radar, True), (radar, True)])
        for i, row in enumerate(seen):
          self.assertEqual(row[2][0, 0], 36. + 8. * i)
          self.assertEqual(row[2][0, 1], 20.)
        self.assertFalse(np.array_equal(actual.mpc.params[:, 2], legacy.mpc.params[:, 2]))
        np.testing.assert_array_equal(actual.mpc.params[:, [0, 1, 3, 4, 5]], legacy.mpc.params[:, [0, 1, 3, 4, 5]])

  def test_planner_fault_stale_future_and_missing_health_fall_back_exactly(self):
    variants = [lambda s: s.logMonoTime.update(modelV2=NOW-150_000_001),
                lambda s: s.logMonoTime.update(radarState=NOW+1),
                lambda s: s.valid.update(modelV2=False), lambda s: s.alive.update(radarState=False),
                lambda s: s.freq_ok.update(modelV2=False), lambda s: setattr(s['carState'], 'canValid', False),
                lambda s: setattr(s['radarState'].radarErrors, 'canError', True)]
    for mutate in variants:
      with self.subTest(mutate=mutate):
        a, b = self.planner(), self.planner()
        sm, _ = inputs()
        mutate(sm)
        a.update(sm, now_ns=NOW)
        with patch('openpilot.selfdrive.controls.lib.longitudinal_planner.qualified_model_leads', return_value=None):
          b.update(sm, now_ns=NOW)
        np.testing.assert_array_equal(a.mpc.params, b.mpc.params)
        np.testing.assert_array_equal(a.mpc.x_sol, b.mpc.x_sol)
        np.testing.assert_array_equal(a.a_desired_trajectory, b.a_desired_trajectory)
    plain, _ = messages(lead=True)
    self.assertIsNone(m.qualified_model_leads(plain, NOW))

  def test_malformed_or_hazard_model_fallback_is_legacy_exact(self):
    sm, _ = inputs()
    lead = sm['radarState'].leadOne
    good = sm['modelV2'].leadsV3[0]
    mpc = self.planner().mpc
    mpc.set_cur_state(20., 0.)
    def model(**changes):
      return NS(**{**good.to_dict(), **changes})
    variants = [model(prob=.5), model(x=[1.]), model(x=[float('nan')]*len(good.x)),
                model(v=[float('inf')]*len(good.v)), None]
    for model_variant in variants:
      np.testing.assert_array_equal(mpc.process_lead(lead, model_variant, model_v_ego=20.), mpc.process_lead(lead))
    lead.aLeadK = -.51
    np.testing.assert_array_equal(mpc.process_lead(lead, good, model_v_ego=20.), mpc.process_lead(lead))
    lead.aLeadK = 0.
    lead.vLead = 10.
    np.testing.assert_array_equal(mpc.process_lead(lead, good, model_v_ego=20.), mpc.process_lead(lead))
    lead.present = False
    np.testing.assert_array_equal(mpc.process_lead(lead, good, model_v_ego=20.), mpc.process_lead(lead))


  def test_unmatched_low_speed_override_cannot_adopt_other_models_departure(self):
    from openpilot.selfdrive.controls.radard import Track, KalmanParams, get_lead
    from openpilot.cereal import log
    sm, _ = inputs()
    model = sm['modelV2'].leadsV3[0]
    model.x = [float(36. + m.RADAR_TO_CAMERA + 20. * t) for t in m.LEAD_T_IDXS_MODEL]
    model.v = [20.] * len(m.LEAD_T_IDXS_MODEL)
    model.xStd = [1.] * len(m.LEAD_T_IDXS_MODEL)
    model.yStd = [1.] * len(m.LEAD_T_IDXS_MODEL)
    model.vStd = [1.] * len(m.LEAD_T_IDXS_MODEL)
    matched, stopped = Track(1, 20., KalmanParams(.05)), Track(2, 0., KalmanParams(.05))
    matched.update(36., 0., 19.5, 20.)
    stopped.update(5., 0., -.5, 0.)
    result = get_lead(.5, True, {1: matched, 2: stopped}, model, .5, .99, low_speed_override=True)
    self.assertEqual(result['radarTrackId'], 2)
    self.assertEqual(result['modelProb'], 0.)
    lead = log.RadarState.LeadData.new_message(**result)
    mpc = self.planner().mpc
    mpc.set_cur_state(.5, 0.)
    self.assertIsNone(m.build_model_lead_trajectory(model, lead, .5, .5))
    np.testing.assert_array_equal(mpc.process_lead(lead, model, model_v_ego=.5), mpc.process_lead(lead))

  def test_association_missing_geometry_and_same_frame_restrictions(self):
    changes = [lambda s: setattr(s['radarState'].leadOne, 'modelProb', 0.),
      lambda s: setattr(s['radarState'].leadOne, 'radarTrackId', -1),
      lambda s: setattr(s['radarState'].leadOne, 'dRel', 100.),
      lambda s: setattr(s['radarState'].leadOne, 'vLead', 24.),
      lambda s: setattr(s['radarState'].leadOne, 'yRel', 2.),
      lambda s: setattr(s['modelV2'].leadsV3[0], 't', [0.]),
      lambda s: setattr(s['modelV2'].leadsV3[0], 'y', [float('nan')]*6)]
    for change in changes:
      sm, _ = inputs()
      change(sm)
      mpc = self.planner().mpc
      mpc.set_cur_state(20., 0.)
      lead, model = sm['radarState'].leadOne, sm['modelV2'].leadsV3[0]
      np.testing.assert_array_equal(mpc.process_lead(lead, model, model_v_ego=20.), mpc.process_lead(lead))
    sm, _ = inputs(False)
    sm['radarState'].leadOne.dRel += .1
    self.assertIsNone(m.build_model_lead_trajectory(sm['modelV2'].leadsV3[0], sm['radarState'].leadOne, 20., 20.))

  def test_eof_receipt_matched_model_and_dynamic_clock_fallback(self):
    changes = [lambda s: setattr(s['radarState'], 'mdMonoTime', NOW-50_000_000),
      lambda s: setattr(s['modelV2'], 'timestampEof', NOW),
      lambda s: setattr(s['modelV2'], 'timestampEof', NOW+BOOT_OFFSET+1),
      lambda s: setattr(s['modelV2'], 'timestampEof', NOW+BOOT_OFFSET-150_000_001),
      lambda s: s.recv_time.update(modelV2=(NOW+1)/1e9),
      lambda s: s.recv_time.update(modelV2=(NOW-150_000_001)/1e9),
      lambda s: setattr(s['carState'], 'canTimeout', True),
      lambda s: setattr(s['modelV2'].velocity, 'x', [])]
    for change in changes:
      sm, _ = inputs()
      change(sm)
      self.assertIsNone(m.qualified_model_leads(sm, NOW))
    sm, _ = inputs()
    for pair in (None, (NOW-1,NOW+BOOT_OFFSET), (NOW+150_000_001,NOW+BOOT_OFFSET), (True,NOW+BOOT_OFFSET)):
      with patch.object(m, '_model_lead_clock_pair_ns', return_value=pair):
        self.assertIsNone(m.qualified_model_leads(sm, NOW))

  def test_actual_radard_matched_and_voacc_lead_owners_are_preserved(self):
    from openpilot.selfdrive.controls.radard import Track, KalmanParams, get_lead
    from openpilot.cereal import log
    sm, _ = inputs()
    model = sm['modelV2'].leadsV3[0]
    model.xStd = model.yStd = model.vStd = [1.] * len(m.LEAD_T_IDXS_MODEL)
    model.a = [0.] * len(m.LEAD_T_IDXS_MODEL)
    track = Track(7, 20., KalmanParams(.05))
    track.update(36., 0., 0., 20.)
    for tracks, expected_radar, expected_id in (({7: track}, True, 7), ({}, False, -1)):
      state = get_lead(20., True, tracks, model, 20., .99, low_speed_override=False)
      lead = log.RadarState.LeadData.new_message(**state)
      original = lead.to_bytes()
      trajectory = m.build_model_lead_trajectory(model, lead, 20., 20.)
      self.assertIsNotNone(trajectory)
      self.assertEqual(lead.radar, expected_radar)
      self.assertEqual(lead.radarTrackId, expected_id)
      lead.clear_write_flag()
      self.assertEqual(lead.to_bytes(), original)
      self.assertEqual(trajectory[0, 0], 36.)
      self.assertEqual(trajectory[0, 1], 20.)

  def test_actual_planner_pairs_prior_exact_model_without_waiting_or_borrowing_latest_horizon(self):
    for radar in (True, False):
      with self.subTest(radar=radar):
        planner = self.planner()
        sm, _ = inputs(radar)
        original_wire_x = float(sm['modelV2'].leadsV3[0].x[0])
        sm['radarState'].mdMonoTime = NOW - 50_000_000
        planner.update(sm, now_ns=NOW, drive_id=1)
        original = planner.model_lead_history.frames[NOW]
        self.assertEqual(original.leadsV3[0].x[0], original_wire_x)
        later = NOW + 50_000_000
        sm.logMonoTime = dict.fromkeys(sm, later)
        sm.recv_time = dict.fromkeys(sm, later / 1e9)
        sm['radarState'].mdMonoTime = NOW
        sm['modelV2'].timestampEof = later + BOOT_OFFSET - 20_000_000
        for lead in sm['modelV2'].leadsV3:
          lead.x = [float(value + 100.) for value in lead.x]
        selected = []
        real_process = planner.mpc.process_lead
        def record(lead, model_lead=None, *, model_v_ego=None, selected=selected, real_process=real_process):
          selected.append(model_lead)
          return real_process(lead, model_lead, model_v_ego=model_v_ego)
        with patch.object(m, '_model_lead_clock_pair_ns', return_value=(later, later + BOOT_OFFSET)), \
             patch.object(planner.mpc, 'process_lead', side_effect=record):
          planner.update(sm, now_ns=later, drive_id=1)
        self.assertEqual(planner.mpc.solution_status, 0)
        self.assertEqual(tuple(selected), original.leadsV3)
        self.assertIs(planner.model_lead_history.frames[NOW], original)
        self.assertEqual(original.leadsV3[0].x[0], original_wire_x)

  def test_cached_original_receipt_and_eof_cannot_be_renewed_and_current_faults_still_block(self):
    sm, _ = inputs()
    history = m.ModelLeadHistory()
    selected = m.qualified_model_leads(sm, NOW, history)
    self.assertIsNotNone(selected)
    original = selected
    sm.recv_time['modelV2'] = (NOW+1_000_000)/1e9
    with patch.object(m, '_model_lead_clock_pair_ns', return_value=(NOW+1_000_000, NOW+1_000_000+BOOT_OFFSET)):
      selected = m.qualified_model_leads(sm, NOW+1_000_000, history)
    self.assertIs(selected, original)
    self.assertEqual(selected.receipt, NOW)
    self.assertEqual(selected.timestampEof, NOW+BOOT_OFFSET-20_000_000)
    sm.valid['modelV2'] = False
    self.assertIsNone(m.qualified_model_leads(sm, NOW, history))
    sm.valid['modelV2'] = True
    later = NOW+140_000_000
    sm.logMonoTime = dict.fromkeys(sm, later)
    sm.recv_time = dict.fromkeys(sm, later/1e9)
    sm['modelV2'].timestampEof = later+BOOT_OFFSET-20_000_000
    sm['radarState'].mdMonoTime = NOW
    with patch.object(m, '_model_lead_clock_pair_ns', return_value=(later, later+BOOT_OFFSET)):
      self.assertIsNone(m.qualified_model_leads(sm, later, history))

  def test_history_capacity_missing_pair_and_drive_reset_are_bounded(self):
    planner = self.planner()
    sm, _ = inputs()
    for index in range(6):
      stamp = NOW + index*40_000_000
      sm.logMonoTime = dict.fromkeys(sm, stamp)
      sm.recv_time = dict.fromkeys(sm, stamp/1e9)
      sm['modelV2'].timestampEof = stamp+BOOT_OFFSET-20_000_000
      sm['radarState'].mdMonoTime = stamp-40_000_000 if index else stamp
      with patch.object(m, '_model_lead_clock_pair_ns', return_value=(stamp, stamp+BOOT_OFFSET)):
        planner.update(sm, now_ns=stamp, drive_id=1)
      self.assertLessEqual(len(planner.model_lead_history.frames), 4)
    self.assertNotIn(NOW, planner.model_lead_history.frames)
    sm['radarState'].mdMonoTime = NOW
    with patch.object(m, '_model_lead_clock_pair_ns', return_value=(stamp, stamp+BOOT_OFFSET)):
      self.assertIsNone(m.qualified_model_leads(sm, stamp, planner.model_lead_history))
      planner.update(sm, now_ns=stamp, drive_id=2)
    self.assertEqual(set(planner.model_lead_history.frames), {stamp})

  def test_vision_association_uses_original_relative_velocity_not_new_filtered_ego(self):
    sm, _ = inputs(False)
    model, lead = sm['modelV2'].leadsV3[0], sm['radarState'].leadOne
    self.assertIsNotNone(m.build_model_lead_trajectory(model, lead, 20.1, 20.))
    lead.vRel = .01
    self.assertIsNone(m.build_model_lead_trajectory(model, lead, 20.1, 20.))
