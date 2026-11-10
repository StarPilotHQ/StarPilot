from openpilot.starpilot.navigation.wire import navigation_envelope
from types import SimpleNamespace as NS

import pytest

from openpilot.cereal import messaging, log
from openpilot.common.constants import CV
from openpilot.starpilot.navigation.intent import current_instruction, turn_desire, cruise_ceiling, turn_speed


NOW = 20_000_000_000


@pytest.fixture
def sm():
  class SM(dict):
    valid = dict.fromkeys(('starpilotNavigation', 'deviceState', 'carState', 'carControl'), True)
    alive = dict(valid)
    logMonoTime = dict.fromkeys(valid, NOW)
  instruction = NS(maneuverType='turn', maneuverModifier='right', distanceMeters=30.)
  nav = NS(enabled=True, controlValid=True, status='guiding', sessionId='producer', revision='one', frameMonoTime=NOW,
           startedMonoTime=10_000_000_000, locationMonoTime=NOW, instruction=instruction, nextManeuver=instruction)
  nav.version = 2
  return SM(starpilotNavigation=NS(navigation=nav), deviceState=NS(started=True, startedMonoTime=10_000_000_000),
            carState=NS(canValid=True, canTimeout=False, gearShifter='drive', vEgo=5., standstill=False, leftBlinker=False,
                        rightBlinker=True, leftBlindspot=False, rightBlindspot=False, gasPressed=False, brakePressed=False),
            carControl=NS(latActive=True, longActive=True))


def test_signal_turn_and_existing_desire_preserved(sm):
  assert turn_desire(sm, NOW, log.Desire.none, supported=True) == log.Desire.turnRight
  assert turn_desire(sm, NOW, log.Desire.laneChangeLeft, supported=True) == log.Desire.laneChangeLeft
  assert turn_desire(sm, NOW, log.Desire.none, supported=False) == log.Desire.none
  sm['carState'].rightBlinker = False
  assert turn_desire(sm, NOW, log.Desire.none, supported=True) == log.Desire.none


@pytest.mark.parametrize('field,value', [('standstill', True), ('rightBlindspot', True), ('leftBlinker', True),
                                        ('vEgo', 14.), ('gearShifter', 'reverse'), ('canValid', False), ('canTimeout', True)])
def test_no_turn_without_vehicle_admission(sm, field, value):
  setattr(sm['carState'], field, value)
  assert turn_desire(sm, NOW, log.Desire.none, supported=True) == log.Desire.none


@pytest.mark.parametrize('field,value', [('controlValid', False), ('enabled', False), ('frameMonoTime', NOW-3_000_000_000),
                                        ('locationMonoTime', NOW+1), ('startedMonoTime', 1), ('sessionId', '')])
def test_stale_previous_drive_and_disabled_nav_are_inert(sm, field, value):
  setattr(sm['starpilotNavigation'].navigation, field, value)
  assert current_instruction(sm, NOW) is None


def test_original_turn_ceiling_and_override(sm):
  cp = NS(openpilotLongitudinalControl=True, passive=False, dashcamOnly=False, minSteerSpeed=0.)
  expected = ((14 * CV.MPH_TO_MS) ** 2 + 2 * .45 * (30 - 8)) ** .5
  assert cruise_ceiling(sm, cp, NOW, 20.) == pytest.approx(expected)
  sm['carState'].gasPressed = True
  assert cruise_ceiling(sm, cp, NOW, 20.) is None
  sm['carState'].gasPressed = False
  cp.openpilotLongitudinalControl = False
  assert cruise_ceiling(sm, cp, NOW, 20.) is None


def civic_cp(fingerprint='HONDA_CIVIC_2022'):
  return NS(openpilotLongitudinalControl=True, passive=False, dashcamOnly=False, minSteerSpeed=0., brand='honda',
            carFingerprint=fingerprint)


def test_civic_turn_ceiling_advances_between_route_fixes(sm):
  nav = sm['starpilotNavigation'].navigation
  nav.instruction.distanceMeters = 300.
  nav.locationMonoTime = NOW - 1_000_000_000
  sm['carState'].vEgo = 15.
  expected = ((14 * CV.MPH_TO_MS) ** 2 + 2 * .45 * (300 - 15 - 8)) ** .5
  assert cruise_ceiling(sm, civic_cp(), NOW, 30.) == pytest.approx(expected)
  stale = ((14 * CV.MPH_TO_MS) ** 2 + 2 * .45 * (300 - 8)) ** .5
  assert cruise_ceiling(sm, civic_cp('HONDA_CIVIC_BOSCH'), NOW, 30.) == pytest.approx(stale)


def test_civic_turn_ceiling_brakes_only_as_hard_as_needed_above_profile(sm):
  nav = sm['starpilotNavigation'].navigation
  nav.instruction.distanceMeters = 174.
  sm['carState'].vEgo = 19.
  target = 14 * CV.MPH_TO_MS
  needed = (19. ** 2 - target ** 2) / (2 * (174 - 8))
  assert cruise_ceiling(sm, civic_cp(), NOW, 30.) == pytest.approx(19. - needed)
  profile = (target ** 2 + 2 * .45 * (174 - 8)) ** .5
  assert cruise_ceiling(sm, civic_cp('HONDA_CIVIC_BOSCH'), NOW, 30.) == pytest.approx(profile)
  sm['carState'].vEgo = profile - 1.
  assert cruise_ceiling(sm, civic_cp(), NOW, 30.) == pytest.approx(profile)


@pytest.mark.parametrize('kind,modifier,mph', [('turn', 'left', 14), ('turn', 'sharpRight', 10), ('turn', 'uturn', 5),
                                             ('roundabout', 'right', 12), ('fork', 'right', None)])
def test_turn_target_matches_dom_table(kind, modifier, mph):
  result = turn_speed(NS(maneuverType=kind, maneuverModifier=modifier, distanceMeters=8.), 0.)
  assert result == pytest.approx(mph * CV.MPH_TO_MS) if mph is not None else result is None


def test_new_event_roundtrip_keeps_route_and_control_evidence():
  message = messaging.new_message('starpilotNavigation')
  message.starpilotNavigation = navigation_envelope({'sessionId': 'owner', 'frameMonoTime': NOW, 'startedMonoTime': 10_000_000_000,
                                    'enabled': True, 'revision': 'one', 'controlValid': True, 'status': 'guiding',
                                    'instruction': {'text': 'Turn right', 'maneuverType': 'turn', 'maneuverModifier': 'right', 'distanceMeters': 30.},
                                    'route': [{'latitude': 1., 'longitude': 2.}], 'locationMonoTime': NOW})
  with log.Event.from_bytes(message.to_bytes()) as received:
    assert received.which() == 'starpilotNavigation'
    assert received.starpilotNavigation.navigation.route[0].latitude == 1.
    assert received.starpilotNavigation.navigation.controlValid


def test_native_planner_navigation_cap_and_default_equivalence():
  import numpy as np
  from unittest.mock import patch
  from opendbc.car.hyundai.interface import CarInterface
  from opendbc.car.hyundai.values import CAR
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.starpilot.longitudinal.tests.test_conditional_handoff import Frame, NOW as FRAME_NOW, DRIVE

  cp = CarInterface.get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
  cp.openpilotLongitudinalControl = True
  baseline, absent, guided = (LongitudinalPlanner(cp, init_v=20.) for _ in range(3))
  changed = False
  for tick in range(40):
    frame = Frame()
    now = FRAME_NOW + tick * 50_000_000
    frame['carState'].vEgo = 20.
    frame['carState'].gearShifter = 'drive'
    frame.logMonoTime = dict.fromkeys(frame, now)
    with patch('openpilot.selfdrive.controls.lib.longitudinal_planner.navigation_ceiling', return_value=None):
      baseline.update(frame, now_ns=now)
    absent.update(frame, now_ns=now)
    assert baseline.output_a_target == absent.output_a_target
    np.testing.assert_array_equal(baseline.mpc.params, absent.mpc.params)
    nav = messaging.new_message('starpilotNavigation')
    nav.starpilotNavigation = navigation_envelope({'enabled': True, 'controlValid': True, 'status': 'guiding', 'sessionId': 'test',
                              'revision': 'one', 'frameMonoTime': now, 'startedMonoTime': DRIVE, 'locationMonoTime': now,
                              'instruction': {'maneuverType': 'turn', 'maneuverModifier': 'right', 'distanceMeters': 20.}})
    frame['starpilotNavigation'] = nav.starpilotNavigation
    frame.logMonoTime['starpilotNavigation'] = now
    frame.valid['starpilotNavigation'] = frame.alive['starpilotNavigation'] = True
    guided.update(frame, now_ns=now)
    assert guided.output_a_target <= baseline.output_a_target
    changed |= guided.output_a_target < baseline.output_a_target
  assert changed


def test_turn_stop_hold_reuses_planner_stop_until_standstill(sm):
  from openpilot.starpilot.navigation.intent import TurnIntent
  owner = TurnIntent()
  sm['longitudinalPlan'] = NS(modelMonoTime=NOW, shouldStop=False)
  sm.logMonoTime['longitudinalPlan'] = NOW
  sm.valid['longitudinalPlan'] = sm.alive['longitudinalPlan'] = True
  def sample(model_stop=False):
    return owner.select(sm, NOW, log.Desire.none, supported=True, model_stop=model_stop)
  assert sample() == log.Desire.turnRight
  sm['longitudinalPlan'].shouldStop = True
  assert sample() == log.Desire.none
  sm['longitudinalPlan'].shouldStop = False
  sm['starpilotNavigation'].navigation.revision = 'favorite-added'
  assert sample() == log.Desire.none
  sm['carState'].standstill = True
  assert sample() == log.Desire.none
  sm['carState'].standstill = False
  assert sample() == log.Desire.turnRight
  assert sample(model_stop=True) == log.Desire.none
  sm.valid['longitudinalPlan'] = False
  assert sample() == log.Desire.none


def test_approaching_turn_does_not_start_or_replace_lane_change(sm):
  from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
  from openpilot.starpilot.navigation.intent import matching_turn_signal
  from openpilot.starpilot.lateral.lane_change_preferences import LaneChangePolicy
  cs = sm['carState']
  cs.steeringPressed, cs.steeringTorque = True, -1.
  cs.vEgo = 21. * CV.MPH_TO_MS  # Above the current turn-guidance lane-change floor.
  helper = DesireHelper(LaneChangePolicy(minimum_speed_mps=0.))
  turn = matching_turn_signal(sm, NOW, supported=True)
  assert turn
  helper.update(cs, True, 1., navigation_turn=turn)
  assert helper.lane_change_state == log.LaneChangeState.off
  helper.lane_change_state = log.LaneChangeState.preLaneChange
  helper.update(cs, True, 1., navigation_turn=turn)
  assert helper.lane_change_state == log.LaneChangeState.off
  helper.lane_change_state = log.LaneChangeState.laneChangeStarting
  helper.lane_change_direction = log.LaneChangeDirection.right
  helper.update(cs, True, 1., navigation_turn=turn)
  assert helper.lane_change_state == log.LaneChangeState.laneChangeStarting
  assert helper.desire == log.Desire.laneChangeRight


@pytest.mark.parametrize('source', ['phone:googleMaps', 'phone:waze'])
def test_notification_guidance_never_grants_vehicle_control_even_if_flag_is_forged(sm, source):
  sm['starpilotNavigation'].navigation.source = source
  sm['starpilotNavigation'].navigation.controlValid = True
  assert current_instruction(sm, NOW) is None
  assert turn_desire(sm, NOW, log.Desire.none, supported=True) == log.Desire.none
  cp = NS(openpilotLongitudinalControl=True, passive=False, dashcamOnly=False, minSteerSpeed=0.)
  assert cruise_ceiling(sm, cp, NOW, 20.) is None
