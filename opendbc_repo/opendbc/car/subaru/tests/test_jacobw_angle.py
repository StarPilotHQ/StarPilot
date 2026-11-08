"""Fork-specific jacobw behavior through physical parsers and real native TX."""
import pytest

from opendbc.can import CANParser
from opendbc.car import Bus
from opendbc.car.lateral import get_max_angle_delta_vm, get_max_angle_vm
from opendbc.car.subaru.jacobw_angle import limit_angle
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.tests.test_ascent_angle import setup_controller, step
from opendbc.car.subaru.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.subaru.tests.caller_helpers import CompleteCan


@pytest.mark.parametrize('speed', (1.0, 2.0, 6.0, 9.0))
@pytest.mark.parametrize('sign', (-1, 1))
def test_real_controller_tracks_small_low_speed_changes(speed, sign):
  _, controller, command, state = setup_controller(CAR.SUBARU_CROSSTREK_2025)
  state.out.vEgoRaw = speed
  command.actuators.steeringAngleDeg = sign * 2.0
  for _ in range(20):
    _, decoded = step(controller, command, state)
    assert decoded['LKAS_Request'] == 1
    assert sign * decoded['LKAS_Output'] > 0
  assert decoded['LKAS_Output'] == pytest.approx(sign * 2.0, abs=.01)


def test_target_first_limits_recover_to_moving_acceleration_bound():
  _, controller, _, _ = setup_controller(CAR.SUBARU_CROSSTREK_2025)
  speed = 30.0
  maximum = get_max_angle_vm(speed, controller.VM, controller.p)
  delta = min(get_max_angle_delta_vm(speed, controller.VM, controller.p), 1.0)
  previous = maximum + .5 * delta
  angle, active = limit_angle(400., previous, speed, 0., True, controller.p, controller.VM)
  assert active
  assert angle == pytest.approx(maximum)
  assert abs(angle - previous) <= delta


def test_impossible_accel_jerk_intersection_withdraws():
  _, controller, _, _ = setup_controller(CAR.SUBARU_CROSSTREK_2025)
  angle, active = limit_angle(400., 180., 35., 220., True, controller.p, controller.VM)
  assert not active and angle == 190.


@pytest.mark.parametrize('sign', (-1, 1))
def test_current_parser_controller_and_native_accept_speed_sweep_and_bound(sign):
  cp, controller, command, state = setup_controller(CAR.SUBARU_CROSSTREK_2025)
  interface = CarInterface(cp)
  controller, state = interface.CC, interface.CS
  stream = CompleteCan(interface)
  parsers = interface.can_parsers
  state.update(parsers)
  decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [('ES_LKAS_ANGLE', 0)], 0)
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  safety.init_tests()
  accepted = 0
  measured = 0.
  peak = 0.
  try:
    for tick in range(2000):
      # Stay low long enough to hit the absolute bound, then accelerate and
      # decelerate with small physical steps while reversing the target.
      speed = 2.0 if tick < 500 else max(2.0, 2.0 + 0.045 * min(tick - 500, 2000 - tick))
      now = 1_000_000_000 + tick * 10_000_000
      frames = stream.frames({
        'Steering_2': {'Steering_Angle': measured},
        'Wheel_Speeds': dict.fromkeys(('FL', 'FR', 'RL', 'RR'), speed * 3.6),
        'CruiseControl': {'Cruise_On': 1, 'Cruise_Activated': 1},
        'ES_Status': {'Cruise_Activated': 1}, 'ES_DashStatus': {'Cruise_On': 1},
        'Transmission': {'Gear': 121},
      }, tick=tick)
      safety.set_timer(now // 1000)
      for frame in frames:
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], frame[1]))
      safety.safety_tick()
      for parser in parsers.values():
        parser.update([(now, frames)])
      state.out = state.update(parsers)
      command.actuators.steeringAngleDeg = sign * (400. if tick < 900 else -400.)
      _, sends = controller.update(command.as_reader(), state, now)
      for frame in sends:
        if frame[0] != 0x124:
          continue
        decoder.update([(now, [frame])])
        values = decoder.vl['ES_LKAS_ANGLE']
        assert abs(values['LKAS_Output']) <= 190.0
        assert safety.safety_tx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])), (tick, speed, values)
        accepted += 1
        measured = values['LKAS_Output']
        peak = max(peak, abs(measured))
    assert accepted == 1000 and peak == 190.
    # An independently packed out-of-envelope command is rejected natively.
    invalid = controller.packer.make_can_msg('ES_LKAS_ANGLE', 0, {'LKAS_Output': sign * 190.01, 'LKAS_Request': 1, 'SET_3': 3})
    assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(invalid[0], invalid[2], invalid[1]))
  finally:
    safety.set_alternative_experience(0)
    safety.set_safety_hooks(0, 0)
    safety.init_tests()


@pytest.mark.parametrize('sign', (-1, 1))
def test_native_absolute_bound_independent_of_rate_and_accel(sign):
  from opendbc.car.subaru import subarucan
  from opendbc.safety.tests.test_subaru_gen2_angle_pair import TestSubaruGen2AnglePair as Pair

  cp = Pair.params(CAR.SUBARU_CROSSTREK_2025)
  packer, frames = Pair.sources(CAR.SUBARU_CROSSTREK_2025, speed=7.2)
  frames = [packer.make_can_msg('Steering_2', 0, {'Steering_Angle': sign * 190.}) if frame[0] == 0x11a else frame
            for frame in frames]
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(0)
  safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam)
  safety.init_tests()
  safety.set_timer(1_000_000)
  try:
    for frame in frames:
      assert safety.safety_rx_hook(Pair.packet(frame))
    safety.safety_tick()
    assert safety.safety_config_valid() and safety.get_controls_allowed()
    # Establish history from a genuine measured-angle inactive frame. At 2m/s,
    # accel permits this angle; the final .01deg step is below the jerk bound.
    assert safety.safety_tx_hook(Pair.packet(subarucan.create_steering_control_angle(packer, sign * 190., False, 0)))
    assert safety.safety_tx_hook(Pair.packet(subarucan.create_steering_control_angle(packer, sign * 190., True, 0)))
    assert not safety.safety_tx_hook(Pair.packet(subarucan.create_steering_control_angle(packer, sign * 190.01, True, 0)))
  finally:
    safety.set_alternative_experience(0)
    safety.set_safety_hooks(0, 0)
    safety.init_tests()
