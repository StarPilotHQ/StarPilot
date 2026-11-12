"""Preserve limiter bounds and report the lateral request actually sent."""

import pytest

from opendbc.can import CANParser
from opendbc.car import Bus, structs
from opendbc.car.subaru import subarucan
from opendbc.car.subaru.stock_aol import ANGLE_CARS, TORQUE_CARS
from opendbc.car.subaru.tests.test_ascent_angle import setup_controller, step
from opendbc.car.subaru.values import CAR, DBC, SubaruSafetyFlags
from opendbc.safety.tests import test_subaru_gen2_angle_pair as pair
from opendbc.safety.tests.libsafety import libsafety_py


@pytest.mark.parametrize('car', sorted(ANGLE_CARS))
@pytest.mark.parametrize('sign', (-1, 1))
@pytest.mark.parametrize('position', ('within', 'limit', 'beyond'))
def test_inactive_sensor_angle_is_bounded_and_native_accepts(car, sign, position):
  cp, controller, command, state = setup_controller(car)
  limit = 545.0 if cp.safetyConfigs[0].safetyParam & SubaruSafetyFlags.FIXED_ANGLE_LIMITS else 650.0
  if car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025):
    limit = 190.0
  measured = sign * {'within': 100.0, 'limit': limit, 'beyond': limit + 5.0}[position]
  packer, frames = pair.TestSubaruGen2AnglePair.sources(car, cruise=False)
  measurement = packer.make_can_msg('Steering_2', 0, {'Steering_Angle': measured})
  frames = [measurement if frame[0] == 0x11A else frame for frame in frames]
  parsers = state.get_can_parsers(cp)
  state.update(parsers)
  for parser in parsers.values():
    parser.update([(1_000_000_000, frames)])
  state.out = state.update(parsers)
  assert state.out.steeringAngleDeg == pytest.approx(measured, abs=0.01)
  command.enabled = command.latActive = False
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(0)
  safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam)
  safety.init_tests()
  safety.set_timer(1_000_000)
  try:
    for frame in frames:
      safety.safety_rx_hook(pair.TestSubaruGen2AnglePair.packet(frame))
    safety.safety_tick()
    assert not safety.get_controls_allowed()
    steer, decoded = step(controller, command, state)
    assert decoded['LKAS_Request'] == 0
    assert decoded['LKAS_Output'] == sign * min(abs(measured), limit)
    assert safety.safety_tx_hook(pair.TestSubaruGen2AnglePair.packet(steer))
    if position == 'beyond':
      unbounded = subarucan.create_steering_control_angle(packer, measured, False, steer[2])
      assert not safety.safety_tx_hook(pair.TestSubaruGen2AnglePair.packet(unbounded))
  finally:
    safety.set_alternative_experience(0)
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
    safety.init_tests()


@pytest.mark.parametrize('car', sorted(ANGLE_CARS | TORQUE_CARS))
@pytest.mark.parametrize('enabled,lat_active,marked', ((True, False, False), (False, True, False), (False, True, True)))
def test_dash_uses_current_request_without_mutating_physical_template(car, enabled, lat_active, marked):
  cp, controller, command, state = setup_controller(car)
  cp.alternativeExperience = 32 if marked else 0
  command.enabled, command.latActive = enabled, lat_active
  state.es_lkas_state_msg['LKAS_ACTIVE'] = 1
  original = dict(state.es_lkas_state_msg)
  decoder = CANParser(DBC[car][Bus.pt], [('ES_LKAS_State', 0)], 0)
  checked = 0
  for tick in range(41):
    now = 1_000_000_000 + tick * 10_000_000
    _, sends = controller.update(command.as_reader(), state, now)
    decoder.update([(now, sends)])
    if any(frame[0] == 0x322 for frame in sends):
      expected = controller.angle_was_active if car in ANGLE_CARS else (lat_active if marked else enabled)
      dash = decoder.vl['ES_LKAS_State']
      if car in ANGLE_CARS or marked or enabled:
        assert bool(dash['LKAS_ACTIVE']) == expected
      assert dash['LKAS_Dash_State'] == (2 if expected else 0)
      assert state.es_lkas_state_msg == original
      checked += 1
  assert checked == 5
