"""Angle-primary candidate: bounded conditioning and unchanged native contracts."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from opendbc.bluepilot_lateral.core.carcontroller_ext import InputTracker, SharedLateralState
from opendbc.bluepilot_lateral.core.lateral_angle_ext import LateralAngleExt
from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import bp_safety_param, init_ford_safety_param_starpilot
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.tests.test_bluepilot_lateral import StraightRoadInputs, STARTUP, settings
from opendbc.car.ford.values import CAR
from openpilot.starpilot.car.ford.starpilot_controller import CurvatureConditioner, StarPilotFordController


def controller(kind=StarPilotFordController, speed=20.0, pinion=False, car=CAR.FORD_MUSTANG_MACH_E_MK1):
  cp = CarInterface.get_params(car, gen_empty_fingerprint(), [], False, False, False)
  init_ford_safety_param_starpilot(cp, STARTUP['on+pinion' if pinion else 'on'])
  inputs = InputTracker(cp, bp_safety_param(cp))
  inputs.update(StraightRoadInputs(settings(STARTUP['on'], True)).update().lateralInputs)
  angle = kind(cp, inputs, SharedLateralState())
  angle.update_angle_params(None)
  state = structs.CarState(vEgoRaw=speed, vEgo=speed, canValid=True)
  cs = SimpleNamespace(out=state, lat_ctl_lim_stat=0)
  cc = structs.CarControl(latActive=True)
  return angle, cc, cs


def set_curve(angle, cc, cs, curvature, model=None):
  cc.actuators.curvature = curvature
  cs.out.yawRate = -curvature * cs.out.vEgoRaw
  angle.inputs.model.orientationRate.z = [(curvature if model is None else model) * cs.out.vEgoRaw] * 33


def test_small_signal_smoothing_has_no_dc_bias_or_unbounded_lag():
  filt = CurvatureConditioner()
  targets = np.array([.0003 + (-1) ** i * .0001 for i in range(200)])
  outputs = np.array([filt.update(float(x), 25.) for x in targets])
  assert np.mean(np.abs(np.diff(outputs[20:]))) < .4 * np.mean(np.abs(np.diff(targets[20:])))
  assert np.max(np.abs(outputs - targets)) * 25. ** 2 <= .04 + 1e-12
  for _ in range(100):
    output = filt.update(.0003, 25.)
  assert output == pytest.approx(.0003, abs=1e-12)


@pytest.mark.parametrize('sign', [-1., 1.])
def test_unwind_is_bounded_and_reversal_or_zero_clears_filter_history(sign):
  filt = CurvatureConditioner()
  filt.update(sign * .002, 20.)
  assert abs(filt.update(sign * .001, 20.) - sign * .001) * 20. ** 2 <= .04 + 1e-12
  assert filt.update(-sign * .001, 20.) == -sign * .001
  assert filt.update(0., 20.) == 0.


def test_large_curve_has_bounded_lag_and_driver_lane_change_bypass_filter():
  filt = CurvatureConditioner()
  filt.update(0., 20.)
  assert abs(filt.update(.004, 20.) - .004) * 20. ** 2 <= .04 + 1e-12
  assert filt.update(.0001, 20., bypass=True) == .0001
  filt.reset()
  assert filt.update(-.0002, 20.) == -.0002


@pytest.mark.parametrize('speed', [3., 10., 20., 30.])
def test_passthrough_conditioner_matches_planner_primary_pinned_limit_chain(speed):
  candidate, cc, cs = controller(speed=speed)
  baseline, baseline_cc, baseline_cs = controller(LateralAngleExt, speed=speed)
  candidate.conditioner.update = lambda target, speed, bypass=False: target
  baseline.path_angle_blend_ratio = 0.0
  for i in range(300):
    curve = .004 * math.sin(i / 30.)
    set_curve(candidate, cc, cs, curve)
    set_curve(baseline, baseline_cc, baseline_cs, curve)
    cc.latActive = baseline_cc.latActive = i >= 10 and not 130 <= i < 140
    cs.out.steeringPressed = baseline_cs.out.steeringPressed = 150 <= i < 220
    cs.out.steeringAngleDeg = baseline_cs.out.steeringAngleDeg = 60. if 155 <= i < 220 else 0.
    actual = candidate.update_angle_strategy(cc, cs, cc.actuators)
    expected = baseline.update_angle_strategy(baseline_cc, baseline_cs, baseline_cc.actuators)
    assert actual == expected, i
    assert candidate.bp_kappa_cmd == baseline.bp_kappa_cmd
    assert candidate.angle_human_turn_active == baseline.angle_human_turn_active
    assert candidate.angle_stall_blip_active == baseline.angle_stall_blip_active


def test_conditioned_shadow_stays_in_measured_envelope():
  angle, cc, cs = controller()
  for i in range(200):
    set_curve(angle, cc, cs, .0003 + .0002 * math.sin(i))
    cs.out.yawRate = 0.
    angle.update_angle_strategy(cc, cs, cc.actuators)
    assert abs(angle.bp_kappa_cmd) <= .002 + 1e-12
    assert angle.conditioner.curvature == angle.bp_kappa_cmd


def test_override_inactive_and_pulse_clear_conditioner():
  angle, cc, cs = controller()
  set_curve(angle, cc, cs, .0003)
  angle.update_angle_strategy(cc, cs, cc.actuators)
  cs.out.steeringPressed = True
  angle.update_angle_strategy(cc, cs, cc.actuators)
  assert angle.conditioner.curvature is None
  cs.out.steeringPressed = False
  angle.stall_blip_frames_left = 6
  for _ in range(6):
    result = angle.update_angle_strategy(cc, cs, cc.actuators)
    assert result.path_angle == 0. and angle.conditioner.curvature is None
  cc.latActive = False
  angle.update_angle_strategy(cc, cs, cc.actuators)
  assert angle.conditioner.curvature is None and angle.shared.path_angle_last == 0.


@pytest.mark.parametrize('field,value', [
  ('vEgoRaw', float('nan')), ('yawRate', float('nan')), ('steeringAngleDeg', float('nan')),
  ('steeringTorque', float('nan')), ('canValid', False), ('canTimeout', True),
  ('vehicleSensorsInvalid', True), ('steerFaultTemporary', True), ('steerFaultPermanent', True),
])
def test_invalid_vehicle_inputs_zero_demand_and_shadow_remains_finite(field, value):
  angle, cc, cs = controller()
  setattr(cs.out, field, value)
  assert not angle.inputs_valid(cs, cc.actuators)
  result = angle.update_angle_strategy(cc, cs, cc.actuators)
  assert result.path_angle == result.apply_curvature == 0.
  assert math.isfinite(angle.bp_kappa_cmd)


def test_missing_model_or_nonfinite_demand_cannot_activate():
  angle, cc, cs = controller()
  angle.inputs.model = None
  assert not angle.inputs_valid(cs, cc.actuators)
  assert angle.update_angle_strategy(cc, cs, cc.actuators).path_angle == 0.
  angle, cc, cs = controller()
  cc.actuators.curvature = float('nan')
  assert not angle.inputs_valid(cs, cc.actuators)
  assert angle.update_angle_strategy(cc, cs, cc.actuators).path_angle == 0.


@pytest.mark.parametrize('car', [CAR.FORD_BRONCO_SPORT_MK1, CAR.FORD_F_150_MK14])
@pytest.mark.parametrize('startup', ['on', 'on+pinion'])
def test_candidate_packets_pass_existing_end_to_end_native_check(car, startup, monkeypatch):
  from opendbc.car.ford.tests import test_bluepilot_lateral as original
  from openpilot.starpilot.car.ford.bluepilot import FordBluePilotController
  from openpilot.starpilot.car.ford.controller_selection import FordController
  original_factory = original.CarController

  def candidate_factory(dbc_names, cp):
    output = original_factory(dbc_names, cp)
    source = SimpleNamespace(controller_choice=FordController.STARPILOT, current_inputs_valid=True)
    output.bp = FordBluePilotController(cp, output.packer, output.CAN, source)
    return output

  monkeypatch.setattr(original, 'CarController', candidate_factory)
  original.test_every_steering_frame_passes_panda_safety(car, startup, True)


def test_candidate_host_packs_inactive_on_invalid_inputs():
  from opendbc.car.ford.carcontroller import CarController
  from opendbc.car.ford.values import DBC
  from openpilot.starpilot.car.ford.bluepilot import FordBluePilotController
  from openpilot.starpilot.car.ford.controller_selection import FordController
  angle, cc, cs = controller()
  cp = angle.CP
  car = CarController(DBC[cp.carFingerprint], cp)
  source = SimpleNamespace(controller_choice=FordController.STARPILOT, current_inputs_valid=True)
  host = FordBluePilotController(cp, car.packer, car.CAN, source)
  host.update(StraightRoadInputs(settings(STARTUP['on'], True)).update(), 0)
  cc.actuators.curvature = .002
  cs.out.canTimeout = True
  packets, applied = host.step_lateral(cc, cs, cc.actuators, 0.)
  assert applied == 0. and host.angle.bp_path_angle_final == 0.
  for address, data, _ in packets:
    if address == 0x3D6:
      assert (data[0] >> 4) & 7 == 0
    elif address == 0x3D3:
      assert data[4] & 4 == 0


def test_small_signal_filter_is_left_right_symmetric():
  left, right = CurvatureConditioner(), CurvatureConditioner()
  for i in range(300):
    target = .0004 * math.sin(i / 3.)
    assert left.update(target, 25.) == -right.update(-target, 25.)


def test_candidate_uses_platform_defaults_not_hidden_bluepilot_tuning():
  from opendbc.bluepilot_lateral.params.param_store import ParamStore
  angle, _, _ = controller()
  angle.update_angle_params(ParamStore([('FordHighSpeedFactor_ang', b'1.5'), ('enable_lane_positioning_ang', b'1')]))
  assert angle.high_speed_curv_factor == 1.
  assert not angle.enable_lane_positioning_ang


@pytest.mark.parametrize('phase', range(5))
@pytest.mark.parametrize('origin,target', [(0, 2), (1, 2), (2, 0), (2, 1)])
def test_controller_changes_wait_for_inactive_frame_and_preserve_native_shadow(origin, target, phase):
  from opendbc.can import CANPacker
  from opendbc.car.ford.fordcan import CanBus
  from opendbc.safety.tests.libsafety import libsafety_py
  from openpilot.starpilot.car.ford.bluepilot import FordBluePilotController
  from openpilot.starpilot.car.ford.controller_selection import FordController
  angle, cc, cs = controller()
  cp, speed, curvature = angle.CP, 20., .004
  packer = CANPacker('ford_lincoln_base_pt')
  source = SimpleNamespace(controller_choice=FordController(origin), current_inputs_valid=True)
  host = FordBluePilotController(cp, packer, CanBus(cp), source)
  road = StraightRoadInputs(settings(STARTUP['on'], origin != 0))
  road.curvature = curvature
  cs.out.yawRate = -curvature * speed
  cc.actuators.curvature = curvature
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, int(cp.safetyConfigs[-1].safetyParam)) == 0
  safety.set_controls_allowed(True)
  applied = 0.
  try:
    for frame in range(100):
      safety.set_timer(1_000_000 + frame * 10_000)
      messages: list[tuple[str, dict[str, float]]] = [
        ('BrakeSysFeatures', {'Veh_V_ActlBrk': speed * 3.6, 'VehVActlBrk_D_Qf': 3, 'VehVActlBrk_No_Cnt': (frame // 2) % 16}),
        ('EngVehicleSpThrottle2', {'Veh_V_ActlEng': speed * 3.6, 'VehVActlEng_D_Qf': 3}),
        ('Yaw_Data_FD1', {'VehYaw_W_Actl': -curvature * speed, 'VehYawWActl_D_Qf': 3, 'VehRollYaw_No_Cnt': frame % 256}),
        ('DesiredTorqBrk', {'VehStop_D_Stat': 0}),
        ('EngVehicleSpThrottle', {'ApedPos_Pc_ActlArb': 0}),
        ('EngBrakeData', {'BpedDrvAppl_D_Actl': 1, 'CcStat_D_Actl': 5}),
      ]
      for name, values in messages:
        if name in ('BrakeSysFeatures', 'EngVehicleSpThrottle2') and frame % 2:
          continue
        address, data, bus = packer.make_can_msg(name, 0, values)
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
      if frame == 40 + phase:
        source.controller_choice = FordController(target)
        road.settings['FordPrefLateralControl'] = b'0' if target == 0 else b'1'
      host.update(road.update(), frame)
      if 40 + phase <= frame <= 60:
        assert host.starpilot_selected == (origin == 2)
      cc.latActive = 15 <= frame < 60 or frame >= 75
      packets = []
      if frame % 5 == 0:
        sends, applied = host.step_lateral(cc, cs, cc.actuators, applied)
        packets.extend(sends)
      if frame % 3 == 0:
        packets.append(host.step_lka(cc, cc.hudControl))
      for address, data, bus in packets:
        assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)), (frame, hex(address), data.hex())
    assert host.starpilot_selected == (target == 2)
  finally:
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)


@pytest.mark.parametrize('invalid', ['shape', 'nonfinite', 'flag', 'freshness'])
def test_new_invalid_frame_cannot_reuse_cached_turn_intent(invalid):
  from opendbc.can import CANPacker
  from opendbc.car.ford.fordcan import CanBus
  from openpilot.starpilot.car.ford.bluepilot import FordBluePilotController
  from openpilot.starpilot.car.ford.controller_selection import FordController
  angle, cc, cs = controller()
  source = SimpleNamespace(controller_choice=FordController.STARPILOT, current_inputs_valid=True)
  host = FordBluePilotController(angle.CP, CANPacker('ford_lincoln_base_pt'), CanBus(angle.CP), source)
  road = StraightRoadInputs(settings(STARTUP['on'], True))
  road.curvature = cc.actuators.curvature = .002
  host.update(road.update(), 0)
  host.step_lateral(cc, cs, cc.actuators, 0.)
  frame = road.update()
  if invalid == 'shape':
    frame.lateralInputs.pathYawRate = []
  elif invalid == 'nonfinite':
    frame.lateralInputs.pathYawRate[0] = float('nan')
  elif invalid == 'flag':
    frame.lateralInputs.valid = False
  else:
    source.current_inputs_valid = False
  host.update(frame, 5)
  assert host.inputs.model is not None  # Upstream correctly retains its previous complete snapshot.
  packets, _ = host.step_lateral(cc, cs, cc.actuators, 0.)
  assert host.angle.conditioner.curvature is None
  assert host.angle.bp_path_angle_final == 0.
  assert all((data[0] >> 4) & 7 == 0 for address, data, _ in packets if address == 0x3D6)


def test_unselected_bluepilot_angle_packets_are_unchanged():
  from opendbc.can import CANPacker
  from opendbc.car.ford.fordcan import CanBus
  from opendbc.bluepilot_lateral.hosts.starpilot.lateral import BluePilotLateral
  from openpilot.starpilot.car.ford.bluepilot import FordBluePilotController
  from openpilot.starpilot.car.ford.controller_selection import FordController
  angle, cc, cs = controller()
  packer, can = CANPacker('ford_lincoln_base_pt'), CanBus(angle.CP)
  source = SimpleNamespace(controller_choice=FordController.BLUEPILOT_ANGLE, current_inputs_valid=True)
  baseline = BluePilotLateral(angle.CP, packer, can)
  host = FordBluePilotController(angle.CP, packer, can, source)
  road = StraightRoadInputs(settings(STARTUP['on'], True))
  for frame in range(100):
    road.curvature = cc.actuators.curvature = .003 * math.sin(frame / 20.)
    cs.out.yawRate = -road.curvature * cs.out.vEgoRaw
    inputs = road.update()
    baseline.update(inputs, frame)
    host.update(inputs, frame)
    if frame % 5 == 0:
      assert baseline.step_lateral(cc, cs, cc.actuators, 0.) == host.step_lateral(cc, cs, cc.actuators, 0.)
    if frame % 3 == 0:
      assert baseline.step_lka(cc, cc.hudControl) == host.step_lka(cc, cc.hudControl)


@pytest.mark.parametrize('speed', [3., 12., 30.])
@pytest.mark.parametrize('pinion', [False, True])
@pytest.mark.parametrize('curvature', [0., .0003])
def test_ordinary_override_release_does_not_reset_steering(speed, pinion, curvature):
  angle, cc, cs = controller(speed=speed, pinion=pinion)
  set_curve(angle, cc, cs, curvature)
  for tick in range(50):
    cs.out.steeringPressed = 10 <= tick < 30
    result = angle.update_angle_strategy(cc, cs, cc.actuators)
    assert not angle.angle_human_turn_active
    assert not angle.angle_stall_blip_active
    assert angle.stall_blip_frames_left == angle.stall_blip_count == 0
    assert math.isfinite(result.path_angle)


def test_natural_deviation_recovery_remains_bounded_and_native_admitted():
  from opendbc.can import CANPacker
  from opendbc.car.ford.fordcan import CanBus
  from opendbc.safety.tests.libsafety import libsafety_py
  from openpilot.starpilot.car.ford.bluepilot import FordBluePilotController
  from openpilot.starpilot.car.ford.controller_selection import FordController
  angle, cc, cs = controller(speed=12.)
  cp, speed = angle.CP, 12.
  packer = CANPacker('ford_lincoln_base_pt')
  source = SimpleNamespace(controller_choice=FordController.STARPILOT, current_inputs_valid=True)
  host = FordBluePilotController(cp, packer, CanBus(cp), source)
  road = StraightRoadInputs(settings(STARTUP['on'], True))
  road.curvature = cc.actuators.curvature = .01
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, int(cp.safetyConfigs[-1].safetyParam)) == 0
  safety.set_controls_allowed(True)
  applied, modes, deviation_seen = 0., [], False
  try:
    for frame in range(200):
      safety.set_timer(1_000_000 + frame * 10_000)
      messages: list[tuple[str, dict[str, float]]] = [
        ('BrakeSysFeatures', {'Veh_V_ActlBrk': speed * 3.6, 'VehVActlBrk_D_Qf': 3, 'VehVActlBrk_No_Cnt': (frame // 2) % 16}),
        ('EngVehicleSpThrottle2', {'Veh_V_ActlEng': speed * 3.6, 'VehVActlEng_D_Qf': 3}),
        ('Yaw_Data_FD1', {'VehYaw_W_Actl': 0, 'VehYawWActl_D_Qf': 3, 'VehRollYaw_No_Cnt': frame % 256}),
        ('DesiredTorqBrk', {'VehStop_D_Stat': 0}),
        ('EngVehicleSpThrottle', {'ApedPos_Pc_ActlArb': 0}),
        ('EngBrakeData', {'BpedDrvAppl_D_Actl': 1, 'CcStat_D_Actl': 5}),
      ]
      for name, values in messages:
        if name in ('BrakeSysFeatures', 'EngVehicleSpThrottle2') and frame % 2:
          continue
        address, data, bus = packer.make_can_msg(name, 0, values)
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
      host.update(road.update(), frame)
      cc.latActive = frame >= 15
      packets = []
      if frame % 5 == 0:
        sends, applied = host.step_lateral(cc, cs, cc.actuators, applied)
        packets.extend(sends)
        deviation_seen |= host.shared.bp_curvature_deviation_limited
        for address, data, _ in sends:
          if address == 0x3D6 and cc.latActive:
            mode = (data[0] >> 4) & 7
            modes.append(mode)
            if mode == 0:
              assert deviation_seen and host.angle.angle_stall_blip_active
              assert host.angle.bp_path_angle_final == 0.
      if frame % 3 == 0:
        packets.append(host.step_lka(cc, cc.hudControl))
      for address, data, bus in packets:
        assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)), (frame, hex(address), data.hex())
    zero_indices = [i for i, mode in enumerate(modes) if mode == 0]
    assert len(zero_indices) == 6
    assert zero_indices == list(range(zero_indices[0], zero_indices[0] + 6))
    assert modes[-1] == 1 and host.angle.bp_path_angle_final > 0.
    assert host.angle.stall_blip_count == 1 and host.angle.stall_blip_cooldown_s > 0.
  finally:
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)


@pytest.mark.parametrize('sign', [-1., 1.])
@pytest.mark.parametrize('speed', [12., 15., 25.])
def test_responding_steering_cannot_trigger_a_stall_release(sign, speed):
  angle, cc, cs = controller(speed=speed)
  set_curve(angle, cc, cs, sign * .0065)
  for tick in range(40):
    cs.out.yawRate = -sign * tick * .00005 * speed
    result = angle.update_angle_strategy(cc, cs, cc.actuators)
    assert not angle.angle_stall_blip_active
    assert angle.stall_blip_frames_left == angle.stall_blip_count == 0
    assert sign * result.path_angle > 0.


@pytest.mark.parametrize('sign', [-1., 1.])
def test_response_cancels_an_armed_release_before_steering_drops(sign):
  angle, cc, cs = controller(speed=12.)
  set_curve(angle, cc, cs, sign * .0065)
  cs.out.yawRate = 0.
  for _ in range(40):
    angle.update_angle_strategy(cc, cs, cc.actuators)
    assert not angle.angle_stall_blip_active
    if angle.stall_blip_frames_left:
      break
  assert angle.stall_blip_frames_left > 0
  cs.out.yawRate = -sign * .0001 * cs.out.vEgoRaw
  result = angle.update_angle_strategy(cc, cs, cc.actuators)
  assert not angle.angle_stall_blip_active
  assert angle.stall_blip_frames_left == 0
  assert angle.stall_blip_count == 0
  assert sign * result.path_angle > 0.
