"""The plannerd curve seam passes a current model cap, then records the winner."""

import unittest
from collections import deque
from types import SimpleNamespace as NS

from openpilot.cereal import messaging
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.plannerd import (
  confirm_curve_frame, curve_for_frame, current_cruise_event, queue_cruise_event, update_curve_frame,
)
from openpilot.starpilot.curve_speed.host import CurveHost
from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling, CurveCeiling
from openpilot.starpilot.longitudinal.profile_runtime import ProfileTuning
from openpilot.starpilot.speed_limits.acceptance import Authority, LongitudinalOwner, Mode
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR

NOW = 10_000_000_000


class Bus:
  def __init__(self, messages, stamp):
    self.messages = messages
    self.logMonoTime = dict.fromkeys(messages, stamp)
    self.valid = dict.fromkeys(messages, True)
    self.alive = dict.fromkeys(messages, True)

  def __getitem__(self, name):
    return self.messages[name]


def bus(stamp, *, lead_present=False, lead_distance=30.0):
  names = ('modelV2', 'carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState', 'vehicleParameters')
  events = {name: messaging.new_message(name) for name in names}
  events['modelV2'].modelV2.orientationRate.z = [0.25] * 33
  events['modelV2'].modelV2.velocity.x = [25.0] * 33
  events['modelV2'].modelV2.position.x = [float(i * 5) for i in range(33)]
  events['modelV2'].modelV2.position.y = [0.0] * 33
  events['carState'].carState.vCruise = 108.0
  events['carState'].carState.vEgo = 25.0
  events['carState'].carState.canValid = True
  events['carControl'].carControl.longActive = True
  events['carControl'].carControl.orientationNED = [0.0, 0.0, 0.0]
  events['selfdriveState'].selfdriveState.enabled = True
  events['controlsState'].controlsState.curvature = 0.01
  events['controlsState'].controlsState.longControlState = LongCtrlState.pid
  events['radarState'].radarState.leadOne.present = lead_present
  events['radarState'].radarState.leadOne.radar = True
  events['radarState'].radarState.leadOne.dRel = lead_distance
  events['radarState'].radarState.leadOne.vLead = 25.0
  events['radarState'].radarState.leadOne.modelProb = 1.0
  return Bus({name: getattr(messaging.log_from_bytes(msg.to_bytes()), name) for name, msg in events.items()}, stamp)


class CurveLoopTests(unittest.TestCase):
  def cruise_message(self, kind, stamp, event_id=1, **fields):
    event = messaging.new_message('slcCruiseEvent')
    event.valid = True
    event.logMonoTime = stamp
    event.slcCruiseEvent = dict(eventId=event_id, observedMonoTime=stamp, kind=kind, button='accel',
                               producerSessionId='card-session', previousMps=30.0, selectedMps=30.0, **fields)
    return messaging.log_from_bytes(event.to_bytes())

  def test_one_ordered_stream_keeps_curve_press_and_slc_speed_change(self):
    slc, curve = deque(maxlen=64), deque(maxlen=64)
    queue_cruise_event(self.cruise_message('curveAccelPress', NOW, 10), slc, curve)
    queue_cruise_event(self.cruise_message('driverChange', NOW, 11), slc, curve)
    self.assertEqual(current_cruise_event(curve, NOW).event_id, 10)
    self.assertEqual(current_cruise_event(slc, NOW).event_id, 11)
    self.assertIsNone(current_cruise_event(curve, NOW))
    self.assertIsNone(current_cruise_event(slc, NOW))

  def test_confirmations_and_ambiguous_or_expired_presses_cannot_reach_curve(self):
    slc, curve = deque(maxlen=64), deque(maxlen=64)
    queue_cruise_event(self.cruise_message('confirmationAccept', NOW), slc, curve)
    self.assertEqual(len(slc), 1)
    self.assertFalse(curve)
    for fields in ({'longPress': True}, {'sessionId': 'slc-session'}, {'decisionId': 1}, {'commandId': 1}, {'presentationId': 1}):
      queue_cruise_event(self.cruise_message('curveAccelPress', NOW, **fields), slc, curve)
      self.assertFalse(curve)
    queue_cruise_event(self.cruise_message('curveAccelPress', NOW), slc, curve)
    self.assertIsNone(current_cruise_event(curve, NOW - 1))
    self.assertEqual(len(curve), 1)
    self.assertIsNone(current_cruise_event(curve, NOW + 150_000_000))
    self.assertFalse(curve)

  def test_serialized_physical_press_releases_only_confirmed_curve_owner(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    cp.pcmCruise = False
    host = CurveHost(enabled=True, replay=True)
    planner = LongitudinalPlanner(cp, init_v=25.0)
    for n in range(1, 31):
      stamp = NOW + n * 50_000_000
      sm = bus(stamp)
      update_curve_frame(planner, sm, cp, stamp, host=host)
    self.assertTrue(host.runtime.was_controlling)
    slc, curve = deque(maxlen=64), deque(maxlen=64)
    stamp += 50_000_000
    queue_cruise_event(self.cruise_message('curveAccelPress', stamp), slc, curve)
    sm = bus(stamp)
    result = update_curve_frame(planner, sm, cp, stamp, host=host, event=current_cruise_event(curve, stamp))
    self.assertIsNone(result.ceiling_mps)
    self.assertTrue(host.runtime.override)
    self.assertFalse(planner.last_curve_ceiling_applied)

  def test_exact_model_cap_and_post_composition_winner(self):
    cp = NS(openpilotLongitudinalControl=True, pcmCruise=False)
    host = CurveHost(enabled=True, replay=True)
    planner = LongitudinalPlanner.__new__(LongitudinalPlanner)
    planner.last_curve_ceiling_applied = False
    for n in range(1, 27):
      stamp = NOW + n * 50_000_000
      ceiling, result = curve_for_frame(host, bus(stamp), cp, stamp)
      self.assertIsNotNone(ceiling)
      self.assertIsNotNone(result)
      self.assertEqual(ceiling.model_ns, stamp)
      # SLC/lead can win despite a curve candidate. Feedback remains clear.
      confirm_curve_frame(host, planner, stamp)
      self.assertFalse(host.runtime.was_controlling)
    planner.last_curve_ceiling_applied = True
    stamp += 50_000_000
    self.assertIsNotNone(curve_for_frame(host, bus(stamp), cp, stamp)[0])
    confirm_curve_frame(host, planner, stamp)
    self.assertTrue(host.runtime.was_controlling)

  def test_no_host_has_no_cap(self):
    self.assertEqual(curve_for_frame(None, None, None, NOW), (None, None))

  def test_actual_native_planner_update_provides_feedback(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    cp.pcmCruise = False
    host = CurveHost(enabled=True, replay=True)
    planner = LongitudinalPlanner(cp, init_v=25.0)
    applied = []
    states = []
    for n in range(1, 31):
      stamp = NOW + n * 50_000_000
      sm = bus(stamp)
      result = update_curve_frame(planner, sm, cp, stamp, host=host)
      applied.append(planner.last_curve_ceiling_applied)
      states.append((planner.last_curve_ceiling_status, float(planner.a_cruise), float(planner.output_a_target),
                     str(planner.mpc.source), result.ceiling_mps if result else None, result.reason if result else None))
      self.assertEqual(host.runtime.confirmed_ns, stamp)
    self.assertIn(True, applied, states[-5:])

  def test_actual_native_slc_lower_cap_denies_curve_feedback(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    cp.pcmCruise = False
    host = CurveHost(enabled=True, replay=True)
    planner = LongitudinalPlanner(cp, init_v=25.0)
    slc = CruiseCeiling(10.0, Authority(Mode.LONGITUDINAL_ONLY, LongitudinalOwner.SYSTEM,
                                       False, True, False, False))
    for n in range(1, 27):
      stamp = NOW + n * 50_000_000
      sm = bus(stamp)
      result = update_curve_frame(planner, sm, cp, stamp, host=host, cruise_ceiling=slc)
      self.assertIsNone(result.ceiling_mps)
      self.assertFalse(planner.last_curve_ceiling_applied)
      self.assertFalse(host.runtime.was_controlling)
    self.assertEqual(host.runtime.curve.revision, 0)

  def test_same_cycle_mpc_headway_filters_following_lead(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    cp.pcmCruise = False
    host = CurveHost(enabled=True, no_lead=True, replay=True)
    planner = LongitudinalPlanner(cp, init_v=25.0)
    allowed = []
    selected_headways = []
    for n in range(1, 65):
      stamp = NOW + n * 50_000_000
      # Current radar lead enters then leaves the filtered tracking/following
      # window. A single raw absence cannot immediately authorize NoLead.
      sm = bus(stamp, lead_present=16 <= n <= 42)
      output = []
      def provider(follow_time_s, *, _sm=sm, _stamp=stamp, _output=output):
        selected_headways.append(follow_time_s)
        ceiling, result = curve_for_frame(host, _sm, cp, _stamp, follow_time_s=follow_time_s)
        _output.append((ceiling, result))
        return ceiling
      planner.update(sm, curve_provider=provider)
      self.assertEqual(len(output), 1)
      self.assertAlmostEqual(selected_headways[-1], float(planner.mpc.params[0, 4]))
      confirm_curve_frame(host, planner, stamp)
      allowed.append(output[0][0] is not None)
    self.assertTrue(all(allowed[:10]))
    self.assertFalse(any(allowed[30:42]))
    self.assertTrue(any(allowed[-10:]))
    with self.assertRaises(ValueError):
      planner.update(bus(NOW + 65 * 50_000_000), curve_ceiling=CurveCeiling(20.0, NOW), curve_provider=lambda _headway: None)

  def test_selected_profile_headway_changes_following_on_same_cycle(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    cp.pcmCruise = False
    stock_host = CurveHost(enabled=True, no_lead=True, replay=True)
    tuned_host = CurveHost(enabled=True, no_lead=True, replay=True)
    stock = LongitudinalPlanner(cp, init_v=25.0)
    tuned = LongitudinalPlanner(cp, init_v=25.0)
    profile = ProfileTuning('aggressive', 2.5, 1.0, 1.0, 1.0, 1.0, 1.0)
    stock_caps, tuned_caps, headways = [], [], []
    for n in range(1, 32):
      stamp = NOW + n * 50_000_000
      sm = bus(stamp, lead_present=True, lead_distance=80.0)
      def stock_provider(headway, *, _sm=sm, _stamp=stamp):
        ceiling, _ = curve_for_frame(stock_host, _sm, cp, _stamp, follow_time_s=headway)
        stock_caps.append(ceiling)
        return ceiling
      def tuned_provider(headway, *, _sm=sm, _stamp=stamp):
        headways.append(headway)
        ceiling, _ = curve_for_frame(tuned_host, _sm, cp, _stamp, follow_time_s=headway)
        tuned_caps.append(ceiling)
        return ceiling
      stock.update(sm, curve_provider=stock_provider)
      tuned.update(sm, profile_tuning=profile, curve_provider=tuned_provider)
      confirm_curve_frame(stock_host, stock, stamp)
      confirm_curve_frame(tuned_host, tuned, stamp)
    self.assertAlmostEqual(float(stock.mpc.params[0, 4]), 1.25)
    self.assertGreater(headways[-1], 1.6)
    self.assertAlmostEqual(headways[-1], float(tuned.mpc.params[0, 4]))
    self.assertIsNotNone(stock_caps[-1])
    self.assertIsNone(tuned_caps[-1])


if __name__ == '__main__':
  unittest.main()
