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
  state.out.vEgoRaw = state.out.vEgo = speed
  command.actuators.steeringAngleDeg = sign * 2.0
  for _ in range(200):
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


@pytest.mark.parametrize('speed,tau', ((0., .3), (5., .3), (7.5, .2), (10., .1), (15., .05), (20., 0.), (25., 0.)))
def test_hackathon_filter_updates_at_control_rate(speed, tau):
  _, controller, command, state = setup_controller(CAR.SUBARU_CROSSTREK_2025)
  state.out.vEgo = state.out.vEgoRaw = speed
  # Keep the production motion gate satisfied even for the zero-speed filter case.
  command.enabled = True
  command.actuators.steeringAngleDeg = .1
  controller.update(command.as_reader(), state, 1_000_000_000)
  alpha = .01 / (tau + .01)
  assert controller.angle_filter.x == pytest.approx(.1 * alpha)
  _, odd_sends = controller.update(command.as_reader(), state, 1_010_000_000)
  assert not any(frame[0] == 0x124 for frame in odd_sends)
  assert controller.angle_filter.x == pytest.approx(.1 * (1. - (1. - alpha) ** 2))
  command.latActive = False
  command.actuators.steeringAngleDeg = -.2
  controller.update(command.as_reader(), state, 1_020_000_000)
  assert controller.angle_filter.x == command.actuators.steeringAngleDeg


def test_hackathon_geometry_is_scoped_and_native_bound_remains_strict():
  cp, controller, _, _ = setup_controller(CAR.SUBARU_CROSSTREK_2025)
  assert cp.steerRatio == 13.
  assert controller.VM.sR == 13.
  assert controller.p.ANGLE_LIMITS.MAX_ANGLE_RATE == 1.
  assert not cp.alphaLongitudinalAvailable and not cp.openpilotLongitudinalControl
  forester, other, _, _ = setup_controller(CAR.SUBARU_FORESTER_2022)
  assert forester.steerRatio == 17.
  assert other.angle_filter is None


@pytest.mark.parametrize('sign', (-1, 1))
def test_hackathon_acceleration_sweep(sign):
  import math

  from opendbc.car.lateral import MAX_LATERAL_ACCEL

  # Jacob's forced-demand sweep, through the production controller and wire codec.
  for half_speed in range(4, 81):
    speed = half_speed / 2.
    _, controller, command, state = setup_controller(CAR.SUBARU_CROSSTREK_2025)
    state.out.vEgo = state.out.vEgoRaw = speed
    command.actuators.steeringAngleDeg = sign * math.degrees(controller.VM.get_steer_from_curvature(6. / speed ** 2, speed, 0.))
    peak = 0.
    for _ in range(300):
      _, wire = step(controller, command, state)
      angle = wire['LKAS_Output']
      peak = max(peak, abs(controller.VM.calc_curvature(math.radians(angle), speed, 0.)) * speed ** 2)
      assert abs(angle) <= 190.
    # The wire quantizes to .01 degree; allow only that quantization in acceleration.
    quantization = abs(controller.VM.calc_curvature(math.radians(.01), speed, 0.)) * speed ** 2
    assert peak <= MAX_LATERAL_ACCEL + quantization + 1e-3
