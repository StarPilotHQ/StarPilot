import pytest

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.longitudinal.profile_document import default_personality_profiles, profile_document, serialize_personality_profiles
from openpilot.starpilot.longitudinal.profile_runtime import read_settings, read_traffic_settings, resolve
from openpilot.starpilot.longitudinal.upstream.planner import LongitudinalPlanner as StockPlanner
from openpilot.starpilot.longitudinal.tests.test_profile_runtime import FrameSM
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
from openpilot.starpilot.conditional_mode.planner_host import selected_follow_time
from openpilot.starpilot.longitudinal.lead_approach import LeadApproach, LeadApproachKey, ApproachFrame


def test_registered_half_second_follow_reaches_mpc_and_resets_to_stock_owner(tmp_path):
  params = Params(str(tmp_path))
  # Traffic's Following = Custom (Simple) is what puts the saved follow and smoothness values in use.
  profiles = default_personality_profiles(False)
  profiles['traffic']['following'] = {'preset': 'custom_simple', 'curve': []}
  params.put('LongitudinalPersonalityProfiles', profile_document(profiles, enabled=True), block=True)
  params.put('TrafficFollow', .5, block=True)
  params.put('RelaxedFollow', .5, block=True)
  settings = read_traffic_settings(params)
  assert settings.valid
  cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
  cp.openpilotLongitudinalControl = True
  tuning = resolve(settings, log.LongitudinalPersonality.standard, 2.5, cp, traffic_mode=True)
  assert tuning is not None and tuning.follow_seconds == .5
  planner = LongitudinalPlanner(cp, init_v=2.5)
  stock, reference = StockPlanner(cp, init_v=2.5), StockPlanner(cp, init_v=2.5)
  data, _ = messages(lead=True)
  data['carState'].vEgo = 2.5
  data['radarState'].leadOne.vLead = 2.5
  data['radarState'].leadOne.dRel = 12.
  data['carControl'].longActive = True
  sm = FrameSM(data)
  for _ in range(30):
    planner.update(sm, profile_tuning=tuning, traffic_mode=True)
    stock.update(sm)
    reference.update(sm)
  assert float(planner.mpc.params[0, 4]) == pytest.approx(.5)
  assert selected_follow_time(planner) == pytest.approx(.5)
  assert float(stock.mpc.params[0, 4]) == pytest.approx(1.45)
  assert stock.output_a_target == reference.output_a_target
  assert stock.output_should_stop == reference.output_should_stop
  for _ in range(30):
    planner.update(sm, traffic_mode=False)
  assert float(planner.mpc.params[0, 4]) == pytest.approx(1.45)
  for _ in range(30):
    planner.update(sm, profile_tuning=tuning, traffic_mode=True)
  planner.update(sm, traffic_mode=None)
  assert float(planner.mpc.params[0, 4]) == pytest.approx(1.45)
  assert params.get('TrafficFollow') == .5


def test_sub_half_second_saved_value_remains_invalid_and_unmodified(tmp_path):
  params = Params(str(tmp_path))
  params.put('TrafficFollow', .499, block=True)
  # Not in use while Traffic's Following is StarPilot Default: ignored and left as saved.
  assert read_traffic_settings(params).valid
  profiles = default_personality_profiles(False)
  profiles['traffic']['following'] = {'preset': 'custom_simple', 'curve': []}
  params.put('LongitudinalPersonalityProfiles', profile_document(profiles, enabled=True), block=True)
  assert not read_traffic_settings(params).valid
  assert params.get('TrafficFollow') == .499


def test_half_second_headway_is_valid_approach_input_without_extra_steady_gap():
  owner = LeadApproach(.05, .3)
  key = LeadApproachKey('configured', 900_000_000)
  frame = ApproachFrame(1_000_000_001, 1_000_000_000, 2.5, None)
  assert owner.step(key, frame, .5) is None
  assert owner.key == key and owner.effective == .5


@pytest.mark.parametrize("speed", [0.0, 5.0, 15.0, 30.0])
@pytest.mark.parametrize("personality", [log.LongitudinalPersonality.aggressive,
                                        log.LongitudinalPersonality.standard,
                                        log.LongitudinalPersonality.relaxed])
@pytest.mark.parametrize("follow", [0.5, 1.0, 3.0])
def test_following_bounds_across_speed_personality_and_profiles(tmp_path, speed, personality, follow):
  params = Params(str(tmp_path))
  params.put_bool('CustomPersonalities', True, block=True)
  profiles = default_personality_profiles(False)
  for profile in profiles.values():
    profile['following'] = {'preset': 'custom', 'curve': [follow] * 10}
  from pathlib import Path
  Path(params.get_param_path('LongitudinalPersonalityProfiles')).write_text(
    serialize_personality_profiles(profiles, False, False, enabled=True))
  cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
  data, _ = messages(lead=True)
  data['carState'].vEgo = speed
  data['carControl'].longActive = True
  data['selfdriveState'].personality = personality
  for lead in (data['radarState'].leadOne, data['radarState'].leadTwo):
    lead.vLead = speed
    lead.vLeadK = speed
    lead.vRel = 0.0
    lead.dRel = 6.0 + follow * speed
  sm = FrameSM(data)
  planner = LongitudinalPlanner(cp, init_v=speed)
  stock = StockPlanner(cp, init_v=speed)
  expected_stock = {log.LongitudinalPersonality.aggressive: 1.25,
                    log.LongitudinalPersonality.standard: 1.45,
                    log.LongitudinalPersonality.relaxed: 1.75}[personality]
  for traffic in (False, True, False):
    settings = read_traffic_settings(params) if traffic else read_settings(params)
    tuning = resolve(settings, personality, speed, cp, traffic_mode=traffic)
    assert tuning is not None and tuning.follow_seconds == pytest.approx(follow)
    for _ in range(60):
      planner.update(sm, profile_tuning=tuning, traffic_mode=traffic)
      stock.update(sm)
    assert float(planner.mpc.params[0, 4]) == pytest.approx(follow)
    assert float(stock.mpc.params[0, 4]) == pytest.approx(expected_stock)
    assert planner.mpc.solution_status == stock.mpc.solution_status == 0
  planner.update(sm, traffic_mode=None)
  assert float(planner.mpc.params[0, 4]) == pytest.approx(expected_stock)
