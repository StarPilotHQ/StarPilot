"""Root-only registered Controls acceptance, using computed controller/CAN replay.

Independent synthetic plant y=3*(-delivered) is explicit. This is one registered
Toyota path, not physical fleet qualification or full raw worker qualification.
No caller supplies ReplayValidation booleans or mocks a production admission.
Matrix covers seven shared stage implementations and one standard controller;
registered representatives are source coverage, not firmware/fleet equivalence.
"""
from dataclasses import replace
from types import SimpleNamespace
import math
from pathlib import Path
import sys

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.values import CAR
from opendbc.car.gm.values import CAR as GM
from opendbc.car.hyundai.values import CAR as HKG
from opendbc.car.steering_projection import install_observer, inspect_projection
from openpilot.cereal import messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.params import ParamKeyType
from openpilot.starpilot.flm.adaptive_profile import Block, Sample, evaluate, replay_accepts
from openpilot.starpilot.flm.physical_response import PhysicalRow, fit_physical
from openpilot.starpilot.flm.controller_replay import (
  ControllerReplay, SelectedControllerFactory, ReplayFrame, capture_snapshot, frame_input, final_command,
)
from openpilot.starpilot.flm.fullrate_collector import AppliedEvidence
from openpilot.starpilot.flm.learning_wire import read_runtime_admission
from openpilot.starpilot.flm.runtime_adapter_registry import canonical_physical_context, current_authority
from openpilot.starpilot.flm.torque_adapter import Objective
from openpilot.starpilot.flm.tests.test_registered_controls_global import controls_for, frame


def refresh_registered_sources(ci, cs, stamp, tick):
  """Supply stock GM source packets through its actual registered parsers."""
  if ci.CP.carFingerprint in (GM.CHEVROLET_VOLT, GM.CHEVROLET_BOLT_ACC_2022_2023):
    packets = []
    for role, bus in ((Bus.pt, 0), (Bus.cam, 2)):
      parser = ci.can_parsers.get(role)
      if parser is None:
        continue
      packer = CANPacker(parser.dbc_name)
      for name, signals in parser.vl.items():
        if not isinstance(name, str):
          continue
        # Independent healthy stock EPS/cruise/speed response, encoded by
        # the registered DBC and consumed by the actual CarState readiness.
        stock = {'LKATorqueDeliveredStatus': 1, 'CruiseMainOn': 1, 'CruiseState': 2,
                 'CruiseActive': 1, 'PRNDL2': 4,
                 'FLWheelSpd': cs.vEgo * 3.6, 'FRWheelSpd': cs.vEgo * 3.6,
                 'RLWheelSpd': cs.vEgo * 3.6, 'RRWheelSpd': cs.vEgo * 3.6}
        values = {field: value for field, value in stock.items() if field in signals}
        if 'RollingCounter' in signals:
          values['RollingCounter'] = (tick // 2) % 4
        if name == 'ASCMActiveCruiseControlStatus':
          values['ACCCruiseState'] = 2
        packets.append(packer.make_can_msg(name, bus, values))
    ci.update([(stamp, packets)])
  # Geometry and maneuver are the explicit independent synthetic plant inputs.
  ci.CS.out = cs.as_reader()


def computed_registered_fit(cp, runtime, observed_ns, *, mode="standard", baseline_snapshot=None):
  """Fit independent plant, actual stage sensitivities, then exact heldout replay."""
  objective = Objective('feedforward', 'high', 'steady')
  # Predeclared independent plant goal: a bounded 2.5% FF reduction.
  # The larger signal resolves the real integer CAN encoder's one-count step.
  known_coordinates = (-0.025, -0.025)
  context = ('a' * 64, 'b' * 64, 'c' * 64, 0)
  context_id = canonical_physical_context(context)
  # Calibration and stage cohorts have disjoint IDs AND original clock support.
  # The known measured response rule is independent of the CI conversion table.
  rows = []
  for block in range(8):
    sign = 1 if block % 2 == 0 else -1
    for index in range(40):
      stamp = observed_ns - 60_000_000_000 + (block * 40 + index) * 50_000_000
      command = sign * (0.2 + index * 0.02)
      rows.append(PhysicalRow('plant-' + str(block), stamp, stamp, command, 3.0 * command,
                              context_id, 'left' if sign > 0 else 'right', True, False, False))
  model = fit_physical(runtime.identity, 'normalized_torque', context_id, tuple(rows), lag_ns=0,
                       min_excitation=0.05, max_rmse=1e-9, now_ns=observed_ns)
  assert model is not None
  factory = SelectedControllerFactory(cp, mode, turn_assist=runtime.descriptor.turn_assist)
  engine = ControllerReplay(factory, objective, identity=runtime.identity,
                            baseline_revision=runtime.baseline_revision, model_revision='known-plant-v1')
  ci = interfaces[cp.carFingerprint](cp)
  ci.update([])
  assert install_observer(ci)
  health = messaging.SubMaster(['pandaStates'])
  panda_period_ns = round(1e9 / SERVICE_LIST['pandaStates'].frequency)
  last_panda_ns = 0

  def update_health(stamp):
    nonlocal last_panda_ns
    if last_panda_ns and stamp - last_panda_ns < panda_period_ns:
      return
    pandas = messaging.new_message('pandaStates', len(cp.safetyConfigs), valid=True, logMonoTime=stamp)
    for panda, config in zip(pandas.pandaStates, cp.safetyConfigs, strict=True):
      panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
      panda.alternativeExperience = cp.alternativeExperience
      panda.controlsAllowed = str(config.safetyModel) not in ('silent', 'noOutput')
    health.update_msgs((stamp + 1_000_000) / 1e9, [pandas.as_reader()])
    last_panda_ns = stamp

  pending_blocks, windows = [], []
  current_control = current_output = None
  for block in range(8):
    sign = 1 if block % 2 == 0 else -1
    controller, vm = factory.create()
    if baseline_snapshot is not None:
      from openpilot.starpilot.flm.controller_replay import restore_snapshot
      restore_snapshot(controller, vm, baseline_snapshot)
    params = SimpleNamespace(angleOffsetDeg=0.0, roll=0.0)
    # Parameters are independent left/right coordinates. Repeat the same
    # per-sign maneuver in each independent cohort so heldout improvement
    # variance measures noise rather than a deliberately changed trajectory.
    # Shared high-speed 1 m/s² maneuver spans multiple real encoder counts
    # while retaining each production policy's full output stabilization.
    speed = 30.0
    desired = sign * 1.0 / speed ** 2
    cs = structs.CarState(vEgo=speed, vEgoRaw=speed, canValid=True,
                         gearShifter=structs.CarState.GearShifter.drive)
    cs.cruiseState.available = cs.cruiseState.enabled = True
    cs.steeringAngleDeg = math.degrees(vm.get_steer_from_curvature(-desired, speed, 0.0))
    ci.CS.out = cs.as_reader()
    start = observed_ns - 31_000_000_000 + block * 3_600_000_000
    # Real controller and CAN encoder warmup; score only an independently frozen
    # steady maneuver. Every scored controller/input/emission remains unthinned.
    for tick in range(240):
      refresh_registered_sources(ci, cs, start + tick * 10_000_000, tick)
      update_health(start + tick * 10_000_000)
      raw, _, _ = controller.update(True, cs, vm, params, False, desired, False, 0.1)
      cc = structs.CarControl(enabled=True, latActive=True)
      cc.actuators.torque = float(raw)
      warm_stamp = start + tick * 10_000_000
      warm_output, warm_packets = ci.apply(cc.as_reader(), warm_stamp)
      # Independent ideal EPS plant reports the preceding actually delivered
      # motor command; the native measured-torque guard remains authoritative.
      cs.steeringTorqueEps = float(warm_output.torqueOutputCan)
      warm_witness = ci.CC.learning_projection
      if warm_witness is not None and inspect_projection(ci, cc.as_reader(), warm_output, warm_witness) is True:
        assert (warm_witness.can_address, warm_witness.can_data, warm_witness.can_bus) in warm_packets
        warm_allowed, _, _ = current_authority(health, cp, now_ns=warm_stamp, required=False)
        current_output = AppliedEvidence(warm_stamp, warm_stamp, warm_stamp, 'normalized_torque', float(warm_output.torque),
                                        True, True, False, False, False, warm_allowed, warm_witness, ci.CC.frame, warm_stamp)
    first = start + 2_400_000_000
    engine.checkpoint(first, capture_snapshot(controller, vm))
    samples = []
    initial_desired = desired
    for tick in range(120):
      stamp = first + tick * 10_000_000
      if tick and tick % 100 == 0:
        # Actual chronological 1 Hz snapshot, before this controller update.
        engine.checkpoint(stamp, capture_snapshot(controller, vm))
      # A real gradual unwind supplies chronological command slew and stage
      # excitation. Repeat the same actual trajectory for every port and
      # independent train/heldout block; retain the original safe peak.
      # Smooth onset avoids an artificial measured-acceleration derivative
      # jump at the scored boundary; peak slope stays below the linear ramp.
      desired = initial_desired * (1.0 - 0.2 * (1.0 - math.cos(math.pi * tick / 119)))
      cs.steeringAngleDeg = math.degrees(vm.get_steer_from_curvature(-desired, speed, 0.0))
      refresh_registered_sources(ci, cs, stamp, 240 + tick)
      inputs = frame_input(cs, vm, params, active=True, safety_limited=False,
                           desired_curvature=desired, curvature_limited=False, lat_delay=0.1)
      raw, _, _ = controller.update(True, cs, vm, params, False, desired, False, 0.1)
      replay = ReplayFrame(stamp, inputs, final_command(raw), (0.0, 0.0),
                           frame_sequence=block * 360 + 241 + tick, actual_lac_command=raw)
      trace = engine.normalize(replay, epsilon=0.01)
      assert trace is not None and trace.verified, (block, tick)
      cc = structs.CarControl(enabled=True, latActive=True)
      cc.actuators.torque = float(raw)
      output, packets = ci.apply(cc.as_reader(), stamp + 1_000_000)
      cs.steeringTorqueEps = float(output.torqueOutputCan)
      witness = ci.CC.learning_projection
      if witness is None:
        # GM uses its native steering cadence. Retain every actual 100 Hz LaC frame
        # above and every Card carrier; a hold retains its original proof.
        assert current_output is not None
        held = replace(current_output, carrier_frame=ci.CC.frame, carrier_observed_ns=stamp + 1_000_000)
        assert engine.emissions.record_carrier(held)
        continue
      assert inspect_projection(ci, cc.as_reader(), output, witness) is True
      assert (witness.can_address, witness.can_data, witness.can_bus) in packets
      update_health(stamp)
      assert health.freq_ok['pandaStates']
      allowed, _, _ = current_authority(health, cp, now_ns=stamp + 1_000_000, required=False)
      assert allowed
      control = SimpleNamespace(identity=runtime.identity, source_epoch=runtime.identity_source_epoch,
                                baseline_revision=runtime.baseline_revision, requested=float(cc.actuators.torque),
                                control_ns=stamp, native_required=False, native_allowed=False,
                                native_observed_ns=0, native_expires_ns=0, native_identity='')
      applied = AppliedEvidence(stamp, stamp, stamp + 1_000_000, 'normalized_torque', float(output.torque),
                                True, True, False, False, False, allowed, witness, ci.CC.frame, stamp + 1_000_000)
      assert engine.emissions.record_carrier(applied) and engine.emissions.record(control, applied)
      engine.emissions.verify(control, applied)  # Actual CAN parser decode, no supplied verification flag.
      # Preserve original evidence clocks; chronological stage sensitivity is
      # rebuilt only after the complete actual controller/CAN trajectory exists.
      samples.append((stamp + 2_000_000, stamp, applied.apply_ns))
      current_control, current_output = control, applied
    assert len(samples) >= 40
    windows.append((first, first + 1_190_000_000))
    pending_blocks.append(('stage-' + str(block), objective.required_cohorts[0 if sign > 0 else 1], tuple(samples)))
  engine.replay_windows = tuple(windows)
  engine.rebuild_causal_features(epsilon=0.01)
  blocks = []
  for block_id, cohort, clocks in pending_blocks:
    samples = []
    for mono_ns, control_ns, apply_ns in clocks:
      projected = engine.projected[apply_ns]
      # The independent known plant and its inverse are unchanged. Sensitivity
      # now carries the selected policy's actual chronological filter state.
      goal = model.slope * (-(projected[0] + sum(a * b for a, b in zip(known_coordinates, projected[1], strict=True)))) + model.intercept
      target = model.inverse(goal)
      assert target is not None
      samples.append(Sample(mono_ns, control_ns, apply_ns, projected[1], -target - projected[0], 1.0))
    blocks.append(Block(runtime.identity, block_id, cohort, objective.parameters[0].stage_revision,
                        'known-plant-stage-v1', tuple(samples)))
  result = evaluate(runtime.identity, objective.parameters, tuple(blocks), now_ns=observed_ns,
                    required_cohorts=objective.required_cohorts, response_revision='known-plant-stage-v1', ridge=1e-6)
  assert result.profile is not None and all(math.isclose(actual, known, abs_tol=0.0001) for actual, known in
                                            zip(result.profile.coefficients, known_coordinates, strict=True)), result
  # Report the exact production refusal and its computed metrics without
  # changing any replay gate. Only this validate frame is line-traced.
  refusal = {}
  def validation_trace(call, event, arg):
    if call.f_code is not ControllerReplay.validate.__code__:
      return None
    if event == 'return' and arg is None:
      refusal['line'] = call.f_lineno
      for key in ('losses', 'cohort', 'improvements', 'lower', 'saturated_regression',
                  'no_slew_regression', 'max_base_slew', 'max_candidate_slew',
                  'max_emitted_base_slew', 'max_emitted_candidate_slew'):
        if key in call.f_locals:
          refusal[key] = call.f_locals[key]
    return validation_trace
  previous_trace = sys.gettrace()
  try:
    sys.settrace(validation_trace)
    validation = engine.validate(result.profile, current_control, current_output, context, tuple(blocks), model)
  finally:
    sys.settrace(previous_trace)
  if validation is None:
    print('ACTUAL_REGISTERED_REPLAY_REFUSAL', cp.carFingerprint, mode, repr(refusal), flush=True)
  assert validation is not None and replay_accepts(validation, result.profile), repr(refusal)
  return result, validation


def accepted_frame(controls, now, tick, result, validation):
  command = frame(controls, now, tick, result=result, validation=validation)
  current = SimpleNamespace(valid=True, autoSteeringLearning=controls.sm['autoSteeringLearning'])
  assert read_runtime_admission(current) == (result.profile, validation)
  return command


@pytest.mark.parametrize('vehicle,mode,policy_module', (
  (CAR.TOYOTA_RAV4_TSS2, 'standard', None),
  (CAR.TOYOTA_RAV4_TSS2, 'starpilot', 'rav4_tss2_policy'),
  (CAR.TOYOTA_COROLLA_TSS2, 'starpilot', 'corolla_tss2_policy'),
  (GM.CHEVROLET_VOLT, 'starpilot', 'volt_policy'),
  (GM.CHEVROLET_BOLT_ACC_2022_2023, 'starpilot', 'bolt_policy'),
  (HKG.HYUNDAI_SONATA, 'starpilot', 'hkg_vehicle_policy'),
  (HKG.GENESIS_G70_2020, 'starpilot', 'genesis_g70_policy'),
  (HKG.GENESIS_GV70_ELECTRIFIED_1ST_GEN, 'starpilot', 'genesis_gv70_policy'),
  (HKG.HYUNDAI_IONIQ_6, 'starpilot', 'ioniq6_policy'),
))
def test_registered_controls_computed_profile_is_used_and_revoked(vehicle, mode, policy_module):
  if vehicle == GM.CHEVROLET_VOLT:
    # Gateway Volt needs its actual radar bus before the factory can admit it.
    # Same normal-radar layout as test_volt_lane_runtime.configurations().
    fingerprint = gen_empty_fingerprint()
    fingerprint[0][0xBE] = 6
    fingerprint[1][0x460] = 8
    cp = interfaces[vehicle].get_params(vehicle, fingerprint, [], False, False, False)
  else:
    cp = interfaces[vehicle].get_non_essential_params(vehicle)
  with controls_for(cp, mode, True) as baseline, controls_for(cp, mode, True) as candidate:
    assert str(candidate.lateral_controller_selection.mode) == mode
    extension = candidate.LaC.starpilot_extension
    if policy_module is None:
      assert extension is None
    else:
      assert type(extension.policy).__module__ == 'openpilot.starpilot.lateral.' + policy_module
    for tick in range(30):
      now = 400_000_000_000 + tick * 10_000_000
      frame(baseline, now, tick)
      frame(candidate, now, tick)
    assert candidate.params.check_key('AutomaticSteeringProfiles')
    assert candidate.params.get_type('AutomaticSteeringProfiles') == ParamKeyType.BYTES
    assert candidate.params.check_key('UseVOACC')
    assert candidate.params.check_key('DeveloperSidebar')
    from tools.laptop_device_build.validate_params_registry import expected_registry
    root = Path(__file__).resolve().parents[4]
    registry = expected_registry((root / 'openpilot/common/params_keys.h').read_text(),
                                 (root / 'openpilot/common/params.h').read_text(),
                                 (root / 'openpilot/cereal/log.capnp').read_text())
    assert set(candidate.params.all_keys()) == {key.encode() for key in registry}
    runtime = candidate.global_learning
    assert runtime.identity and runtime.eligible
    assert candidate.sm['selfdriveState'].active and candidate.sm['selfdriveState'].enabled
    original_cp, original_tune = candidate.CP.as_builder().to_bytes(), candidate.LaC.torque_params.to_dict()
    result, validation = computed_registered_fit(candidate.CP, runtime, 400_300_000_000, mode=mode)
    observed_effect = False
    for tick in range(30, 81):
      now = 400_000_000_000 + tick * 10_000_000
      reference, _ = frame(baseline, now, tick)
      actual, _ = accepted_frame(candidate, now, tick, result, validation)
      assert runtime.reason == 'qualified' and runtime.used
      assert runtime.outcome is not None and runtime.outcome.status == 'applied'
      assert runtime.outcome.baseline_revision == validation.baseline_revision
      assert max(abs(x) for x in runtime.applied_coordinates) <= (tick - 29) * 0.00005 + 1e-12
      assert actual.latActive == reference.latActive and actual.longActive == reference.longActive
      observed_effect |= actual.actuators.torque != reference.actuators.torque
      assert candidate.CP.as_builder().to_bytes() == original_cp
      assert candidate.LaC.torque_params.to_dict() == original_tune
    assert observed_effect  # Real selected LaC and final Controls command consumed the profile.
    # No new result is minted and original profile expiry is not extended.
    assert now == result.profile.expires_ns  # Inclusive original lease boundary was admitted.
    accepted_frame(candidate, result.profile.expires_ns + 1, 81, result, validation)
    assert runtime.applied_coordinates == (0.0, 0.0)
    assert runtime.outcome is None or runtime.outcome.status != 'applied'
