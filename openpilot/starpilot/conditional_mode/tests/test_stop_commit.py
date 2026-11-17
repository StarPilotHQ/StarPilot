from dataclasses import replace

from openpilot.starpilot.conditional_mode.policy import Authority, ConditionalModePolicy, ManualIntent, ModeChoice, SceneEvidence
from openpilot.starpilot.conditional_mode.stop import StopLightDetector
from openpilot.starpilot.conditional_mode.tests.test_stop import frame


def prime():
  detector = StopLightDetector()
  for tick in range(30):
    result = detector.step(frame(tick))
  assert result.light_detected and detector.committed
  return detector


def test_acquired_stop_survives_fresh_invalid_geometry_until_valid_clear():
  detector = prime()
  for tick in range(30, 140):
    result = detector.step(frame(tick, horizon=None, speed=4.27))
    assert result.light_detected and detector.committed
  for tick in range(140, 160):
    result = detector.step(frame(tick, horizon=192., speed=4.27))
  assert not result.light_detected and not detector.committed


def test_traffic_and_turn_cannot_abandon_short_committed_stop():
  for changes in ({'traffic': True}, {'left': True, 'steering': 45.}):
    detector = prime()
    for tick in range(30, 140):
      result = detector.step(frame(tick, horizon=6.684, speed=4.27, **changes))
      assert result.light_detected and detector.committed


def test_unknown_source_and_cold_invalid_geometry_cannot_authorize_commit():
  detector = StopLightDetector()
  assert detector.step(frame(0, horizon=None)).light_detected is None
  assert not detector.committed
  detector = prime()
  assert detector.step(replace(frame(30), traffic_mode=None)).light_detected is None
  assert detector.committed
  assert not detector.standstill_committed


def test_manual_chill_and_personality_mode_change_do_not_override_committed_stop():
  policy = ConditionalModePolicy()
  authority = Authority(True, True, False, True, True, True)
  for tick, choice in enumerate((ModeChoice.CEM, ModeChoice.CCM, ModeChoice.CEM)):
    now = 100. + tick * .05
    scene = SceneEvidence(observed_mono_s=now, speed_mps=4.27, committed_stop=True)
    result = policy.step(now, choice, ManualIntent.FORCE_CHILL, authority, scene)
    assert result.requested_experimental and result.reason.value == 'cem_stop'
  scene = replace(scene, observed_mono_s=now+.05, committed_stop=False)
  result = policy.step(now+.05, ModeChoice.CEM, ManualIntent.FORCE_CHILL, authority, scene)
  assert not result.requested_experimental and result.reason.value == 'manual_chill'


def test_pedal_override_releases_acquired_stop_without_authority_recreation():
  detector = prime()
  result = detector.step(frame(30, horizon=6.684, speed=4.27, pedal=True))
  assert not result.light_detected and not detector.committed


def test_standstill_commit_survives_unknowns_and_requires_continuous_clear_evidence():
  detector = prime()
  detector.step(frame(30, speed=0., standstill=True, horizon=6., model_should_stop=True))
  assert detector.standstill_committed
  for tick in range(31, 45):
    observation = detector.step(replace(frame(tick), traffic_mode=None))
    assert observation.light_detected is None
    assert detector.standstill_committed
  for tick in range(45, 65):
    detector.step(frame(tick, speed=0., standstill=True, horizon=192., model_should_stop=True))
    assert detector.standstill_committed
  for tick in range(65, 71):
    detector.step(frame(tick, speed=0., standstill=True, horizon=192., model_should_stop=False))
  detector.step(replace(frame(71), traffic_mode=None))
  for tick in range(72, 79):
    detector.step(frame(tick, speed=0., standstill=True, horizon=192., model_should_stop=False))
    assert detector.standstill_committed  # Unknown time contributes no clear dwell.
  for tick in range(79, 86):
    detector.step(frame(tick, speed=0., standstill=True, horizon=192., model_should_stop=False))
  assert not detector.committed and not detector.standstill_committed


def test_ford_stopped_commit_needs_prior_bounded_geometry_and_current_stop_bit():
  from opendbc.car.ford.values import CAR
  for name in CAR:
    detector = StopLightDetector()
    held = replace(frame(0, speed=0., standstill=True, horizon=None, model_should_stop=True),
                   car_fingerprint=str(name), standstill_path_extent_m=.1, longitudinal_stop_active=True)
    detector.step(held)
    assert not detector.committed
    detector.step(replace(held, observed_mono_s=100.05, now_mono_s=100.055))
    assert detector.committed
    missing = replace(held, observed_mono_s=100.1, now_mono_s=100.105, standstill_path_extent_m=None)
    assert detector.step(missing).standstill_hold
    assert not detector.step(replace(missing, observed_mono_s=100.15, now_mono_s=100.155,
                                     pedal_override=True)).standstill_hold
    assert not detector.committed
  for defect in ('unknown', 'invalid', 'no_stop', 'moving', 'stale', 'long_off'):
    detector = StopLightDetector()
    current = replace(frame(0, speed=0., standstill=True, horizon=None, model_should_stop=True),
                      car_fingerprint=str(CAR.FORD_MUSTANG_MACH_E_MK1), standstill_path_extent_m=.1, longitudinal_stop_active=True)
    if defect == 'unknown':
      current = replace(current, car_fingerprint='FORD_UNKNOWN')
    elif defect == 'invalid':
      current = replace(current, standstill_path_extent_m=None)
    elif defect == 'no_stop':
      current = replace(current, model_should_stop=False)
    elif defect == 'moving':
      current = replace(current, standstill=False)
    elif defect == 'stale':
      current = replace(current, observed_mono_s=99.)
    elif defect == 'long_off':
      current = replace(current, longitudinal_stop_active=False)
    detector.step(current)
    detector.step(replace(current, now_mono_s=current.now_mono_s+.05,
                          observed_mono_s=current.observed_mono_s+.05))
    assert not detector.committed, defect


def test_clear_stopped_launch_cannot_reacquire_from_lingering_lead_handoff():
  from openpilot.starpilot.conditional_mode.stop import StopLead
  detector = prime()
  detector.step(frame(30, speed=0., standstill=True, horizon=6., model_should_stop=True))
  assert detector.standstill_committed
  lead = StopLead(True, 4.8, 1.4, False, .999, False)
  cleared = False
  for tick in range(31, 70):
    current = replace(frame(tick, speed=0., standstill=True, horizon=62.4, model_should_stop=False), lead=lead, longitudinal_launch_active=True)
    detector.step(current)
    if not detector.committed:
      cleared = True
    if cleared:
      assert not detector.committed and not detector.standstill_committed
  assert cleared


def test_launch_acquisition_guard_keeps_current_stop_and_forcing_evidence():
  from openpilot.starpilot.conditional_mode.stop import StopLead
  lead = StopLead(True, 4.8, 1.4, False, .999, False)
  for change in ({'model_should_stop': True}, {'forcing_stop': True}, {'model_horizon_m': 10.},
                 {'longitudinal_stop_active': True}, {'longitudinal_launch_active': False}):
    detector = StopLightDetector()
    detector.light_detected = True
    current = replace(frame(0, speed=0., standstill=True, horizon=62.4, model_should_stop=False), lead=lead, longitudinal_launch_active=True)
    current = replace(current, **change)
    detector.step(current)
    assert detector.committed, change


def test_actual_mache_clear_launch_keeps_hold_released(monkeypatch):
  from opendbc.car.car_helpers import interfaces
  from opendbc.car.ford.values import CAR
  from openpilot.cereal import messaging
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.starpilot.longitudinal.tests import test_cem_green_release as green
  case = green.CemGreenCallerTests(methodName='runTest')
  case.setUp()
  case.cp = interfaces[CAR.FORD_MUSTANG_MACH_E_MK1].get_non_essential_params(CAR.FORD_MUSTANG_MACH_E_MK1)
  case.cp.openpilotLongitudinalControl = True
  case.cp.pcmCruise = False
  case.planner = LongitudinalPlanner(case.cp, init_v=4.27)
  try:
    case.prime()
    original = green.serialized_scene
    def departing(**kwargs):
      kwargs.update(lead_present=True, lead_distance=4.8, lead_speed=1.4, lead_accel=1.3)
      payloads = original(**kwargs)
      event = messaging.new_message('radarState')
      event.radarState = payloads['radarState']
      event.radarState.leadOne.radar = False
      event.radarState.leadOne.modelProb = .999
      payloads['radarState'] = event.radarState
      return payloads
    monkeypatch.setattr(green, 'serialized_scene', departing)
    cleared = moving = False
    for _ in range(40):
      _, _, held, plan = case.cycle(stopped=True, starting=True, horizon=62.4,
        velocity=[float(i) / 4 for i in range(33)], should_stop=False, desired_accel=.8, lead=True)
      if not case.owner.mode.projector.stop_detector.committed:
        cleared = True
      if cleared:
        assert not held and not case.owner.mode.projector.stop_detector.committed
        moving |= not plan.shouldStop and plan.aTarget > .1
    assert cleared and moving
  finally:
    case.doCleanups()
