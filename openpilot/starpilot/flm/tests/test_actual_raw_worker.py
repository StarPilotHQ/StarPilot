"""Root-only actual raw factory->child actor->typed result->Controls proof.

Known independent synthetic plant: static LA gain 1.98 and response time 50 ms.
Both constants are fixed here, never derived from CP/CI inverse or candidate
goals. CS steering angle and independent response samples share the actual
plant state; EPS torque follows the previous decoded motor command. Every
controller/CAN state and candidate validation is recomputed by production.
This bounded synthetic dynamics case does not qualify a physical device, real
vehicle dynamics or fleet.
"""
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
import time

from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.steering_projection import install_observer, inspect_projection, normalized_applied_command
from opendbc.car.toyota.values import CAR
from openpilot.cereal import messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.starpilot.flm.controller_replay import SelectedControllerFactory, capture_snapshot, frame_input, final_command
from openpilot.starpilot.flm.fit_process import FitProcess
from openpilot.starpilot.flm.fullrate_collector import ControlEvidence, AppliedEvidence, RawPoint, RawBlock
from openpilot.starpilot.flm.learner_factory import TorqueCollectionAdapter, decode_fit_result, fit_snapshot
from openpilot.starpilot.flm.learning_wire import write_control, read_control, write_applied, read_applied
from openpilot.starpilot.flm.torque_adapter import Objective, gates
from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values, current_authority, read_source
from openpilot.starpilot.flm.torque_observation import TorqueObservation, REVISION
from openpilot.starpilot.flm.tests.test_registered_controls_global import controls_for, frame
from openpilot.starpilot.flm.tests.test_registered_accepted_profile import accepted_frame

PLANT_SLOPE = 1.98
PLANT_TAU_SECONDS = .05
BASE = 400_000_000_000
CONTEXT = ('a' * 64, 'b' * 64, 'c' * 64, 0)


def raw_job(cp, runtime, params_store, *, native_refinements=False):
  factory = SelectedControllerFactory(cp, 'standard', turn_assist=runtime.descriptor.turn_assist)
  ci = interfaces[cp.carFingerprint](cp)
  ci.update([])
  assert install_observer(ci)
  health = messaging.SubMaster(['pandaStates'])
  native_health = messaging.SubMaster(['lateralTorqueParameters']) if native_refinements else None
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

  blocks, adapter = [], None
  sequence = 0
  last_control = last_output = None
  physical_accel = 0.0
  measured_eps_torque = 0.0
  plant_command = 0.0
  plant_ns = None

  def advance_physics(stamp):
    nonlocal physical_accel, plant_ns
    if plant_ns is not None:
      assert stamp > plant_ns
      alpha = 1 - math.exp(-(stamp - plant_ns) / 1e9 / PLANT_TAU_SECONDS)
      physical_accel += alpha * (PLANT_SLOPE * -plant_command - physical_accel)
    plant_ns = stamp

  for block in range(16):
    controller, vm = factory.create()
    observer = TorqueObservation()
    controller.automatic_flm_observation = observer
    params = SimpleNamespace(angleOffsetDeg=0.0, roll=0.0)
    direction = 1 if block % 2 == 0 else -1
    # Broader independent model training, narrower model heldout and stage
    # requests. These fixed maneuvers never enlarge a fitted inverse envelope.
    amplitude = .40 if block < 4 else .34 if block < 8 else .14
    cs = structs.CarState(vEgo=30.0, vEgoRaw=30.0, canValid=True, gearShifter=structs.CarState.GearShifter.drive)
    cs.cruiseState.available = cs.cruiseState.enabled = True
    ci.CS.out = cs.as_reader()
    start = BASE - 64_000_000_000 + block * 4_000_000_000
    points, source = [], None
    for tick in range(400):
      stamp = start + tick * 10_000_000
      goal = amplitude + (.03 * max(0, tick - 200) * .01 if block >= 8 else 0.0)
      desired = direction * goal / 900.0
      advance_physics(stamp)
      # State is measured BEFORE this tick’s CAN application: previous plant
      # output, never the desired trajectory or a native inverse-torque table.
      cs.steeringAngleDeg = math.degrees(vm.get_steer_from_curvature(-physical_accel / 900.0, 30.0, 0.0))
      # Toyota CarState exposes STEER_TORQUE_EPS in native motor counts
      # (after eps_torque_scale), as consumed by the real measured limiter.
      cs.steeringTorqueEps = measured_eps_torque
      update_health(stamp)
      if native_refinements and tick % 25 == 0:
        from openpilot.starpilot.flm.native_baseline import apply_native_refinement
        packet = messaging.new_message('lateralTorqueParameters', valid=True, logMonoTime=stamp)
        row = packet.lateralTorqueParameters
        row.version, row.valid, row.useParams = 1, True, True
        # Small, deterministic changing native baseline, independent of the
        # synthetic physical plant or fitted objective. No fabricated fit.
        row.latAccelFactorFiltered = float(cp.lateralTuning.torque.latAccelFactor) * (1 + .002 * math.sin((block * 400 + tick) / 100))
        row.latAccelOffsetFiltered = 0.
        row.frictionCoefficientFiltered = float(cp.lateralTuning.torque.friction) * (1 + .002 * math.cos((block * 400 + tick) / 100))
        native_health.update_msgs(stamp / 1e9, [packet.as_reader()])
        apply_native_refinement(controller, cp, native_health, now_ns=stamp,
          apply=lambda controller=controller, row=row: controller.update_torque_parameters(row.latAccelFactorFiltered, row.latAccelOffsetFiltered,
                                                           row.frictionCoefficientFiltered))
      checkpoint = ()
      if tick >= 200 and tick % 100 == 0:
        checkpoint = capture_snapshot(controller, vm).values
        sequence += 1
      inputs = frame_input(cs, vm, params, active=True, safety_limited=False,
                           desired_curvature=desired, curvature_limited=False, lat_delay=.1) + (
                             ('control_frame', float(block * 400 + tick + 1)), ('raw_model_curvature', desired),
                             ('model_source_ns.high', float(stamp >> 32)), ('model_source_ns.low', float(stamp & 0xffffffff)))
      observer.begin_frame()
      raw, _, log = controller.update(True, cs, vm, params, False, desired, False, .1)
      cc = structs.CarControl(enabled=True, latActive=True)
      cc.actuators.torque = float(raw)
      output, packets = ci.apply(cc.as_reader(), stamp + 1_000_000)
      witness = ci.CC.learning_projection
      assert witness is not None
      delivered = normalized_applied_command(witness)
      assert delivered is not None
      # Hold the previous CAN command through the original application time,
      # then integrate the new one only through the original response time.
      # No future command or PID/controller state enters the physical sample.
      advance_physics(stamp + 1_000_000)
      plant_command = delivered
      advance_physics(stamp + 2_000_000)
      assert output.torqueOutputCan == witness.final_raw
      measured_eps_torque = float(witness.final_raw)
      if tick < 200:
        continue
      witness = ci.CC.learning_projection
      assert witness is not None and inspect_projection(ci, cc.as_reader(), output, witness) is True
      assert (witness.can_address, witness.can_data, witness.can_bus) in packets
      assert health.freq_ok['pandaStates']
      allowed, _, _ = current_authority(health, cp, now_ns=stamp + 1_000_000, required=False)
      assert allowed
      trace = observer.finish_frame(controller, log, selected_command=raw)
      assert trace is not None and trace[0] == REVISION and not log.saturated
      values = trace[1] + (('actual_delta_ff', 0.0), ('actual_delta_threshold', 0.0))
      if source is None or tick % 20 == 0:
        source = read_source(cp, params_store, now_ns=stamp, calibration=(0.0, 0.0, 0.0), vehicle_session='fixture-drive')
      assert source.enabled and not source.manual and source.source_epoch == runtime.identity_source_epoch
      assert source.observed_ns <= stamp <= source.expires_ns
      control = ControlEvidence(
        identity=runtime.identity, generation=runtime.generation, control_ns=stamp, state_ns=stamp,
        domain='normalized_torque', stage_revision=runtime.descriptor.stage_revision,
        response_revision='raw-known-plant-v1', requested=final_command(raw),
        features=tuple(observer.stage.baseline_ff * value for value in
                       gates(Objective('feedforward', 'high', 'steady'), observer.stage.speed, observer.stage.setpoint, observer.stage.jerk)),
        baseline_prediction=final_command(raw),
        enabled=True, manual=False, active=True, valid=True, override=False, fault=False,
        saturated=False, limited=False, model_wobble=False, controller_chatter=False,
        stage_schema_revision=REVISION, stage_values=values, applied_coordinates=(0.0, 0.0),
        source_epoch=source.source_epoch, source_receipt_ns=source.observed_ns, controller_mode='standard',
        policy=runtime.descriptor.policy, baseline_revision=runtime.baseline_revision,
        baseline_values=(baseline_values(controller) if native_refinements else runtime.initial_values),
        controller_revision=runtime.descriptor.controller_revision, turn_assist=runtime.descriptor.turn_assist,
        replay_snapshot=checkpoint, replay_inputs=inputs, snapshot_sequence=sequence)
      applied = AppliedEvidence(stamp, stamp, stamp + 1_000_000, 'normalized_torque', float(output.torque),
                                True, True, False, False, False, allowed, witness, ci.CC.frame, stamp + 1_000_000)
      # Actual producer receives decoded cereal evidence, never live objects.
      control_event = messaging.new_message('starpilotLateralState', valid=True, logMonoTime=control.control_ns)
      applied_event = messaging.new_message('starpilotCarState', valid=True, logMonoTime=applied.apply_ns)
      write_control(control_event.starpilotLateralState, control)
      write_applied(applied_event.starpilotCarState, applied)
      decoded_control = read_control(messaging.log_from_bytes(control_event.to_bytes()))
      decoded_applied = read_applied(messaging.log_from_bytes(applied_event.to_bytes()))
      assert decoded_control == control and decoded_applied == applied
      control, applied = decoded_control, decoded_applied
      if adapter is None:
        adapter = TorqueCollectionAdapter(cp, control, ('feedforward', 'high', 'steady'))
      assert adapter.prepare_control(control) is control
      assert adapter.record_actual_carrier(applied) and adapter.record_actual_emission(control, applied)
      adapter.replay.emissions.verify(control, applied)
      if tick % 5 == 0:
        delivered = normalized_applied_command(witness)
        assert delivered is not None
        observed = applied.apply_ns + 1_000_000
        measured = {'lateral_accel': physical_accel, 'applied_command': delivered,
                    'response_ns': observed, 'speed_mps': 30.0, 'yaw_rate': physical_accel / 30.0,
                    'roll_rad': 0.0, 'curvature': physical_accel / 900.0,
                    'steering_angle_deg': float(cs.steeringAngleDeg)}
        points.append(RawPoint(stamp, applied.apply_ns, observed, control, applied, measured, CONTEXT))
        last_control, last_output = control, applied
    assert len(points) == 40
    blocks.append(RawBlock(runtime.identity, 'raw-' + str(block),
                           'high.' + ('left' if desired > 0 else 'right') + '.steady',
                           runtime.descriptor.stage_revision, 'raw-known-plant-v1', tuple(points)))
  calibration_commands = [-point.physical['applied_command'] for block in blocks[:8] for point in block.samples]
  calibration_responses = [point.physical['lateral_accel'] for block in blocks[:8] for point in block.samples]
  stage_targets = [dict(point.control.stage_values)['setpoint'] for block in blocks[8:] for point in block.samples]
  support = {'calibration_command': (min(calibration_commands), max(calibration_commands)),
             'calibration_response': (min(calibration_responses), max(calibration_responses)),
             'stage_physical_target': (min(stage_targets), max(stage_targets)), 'plant_slope': PLANT_SLOPE,
             'plant_tau_seconds': PLANT_TAU_SECONDS}
  assert all(min(calibration_responses) <= value <= max(calibration_responses) for value in stage_targets), support
  cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
  job_now_ns = applied.apply_ns + 2_000_000 if native_refinements else BASE + 300_000_000
  snapshot = adapter.fit_job_snapshot(tuple(blocks), job_now_ns, 1, None,
                                      control=last_control, output=last_output, physical_context=CONTEXT)
  export_cost = {'cpu_ns': time.thread_time_ns() - cpu, 'wall_ns': time.perf_counter_ns() - wall,
                 'json_bytes': len(json.dumps(snapshot).encode()), 'recorded_frames': len(adapter.replay.records), 'blocks': 16, 'support': support}
  assert snapshot is not None and export_cost['recorded_frames'] == 3200
  if native_refinements:
    export_cost['last_physical'] = dict(points[-1].physical)
    # Fit uses its genuine maneuver endpoint. Fresh revalidation separately
    # uses later actual frames already observed after that physical response.
    last_control, last_output = control, applied
  return snapshot, adapter, last_control, last_output, export_cost


def test_actual_raw_registered_worker_report_reaches_controls():
  cp = interfaces[CAR.TOYOTA_RAV4_TSS2].get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
  with controls_for(cp, 'standard', True) as baseline, controls_for(cp, 'standard', True) as candidate:
    for tick in range(30):
      now = BASE + tick * 10_000_000
      frame(baseline, now, tick)
      frame(candidate, now, tick)
    runtime = candidate.global_learning
    assert runtime.identity and runtime.eligible
    original_cp = candidate.CP.as_builder().to_bytes()
    original_tune = candidate.LaC.torque_params.to_dict()
    snapshot, adapter, control, output, export_cost = raw_job(candidate.CP, runtime, candidate.params)
    # Private causal diagnostic: expose the real child-factory exception in
    # pytest without changing the production actor's stderr/failure boundary.
    # The exact same payload must still pass the actual actor below.
    direct_report = fit_snapshot(snapshot)
    direct_decoded = decode_fit_result(direct_report)
    assert direct_decoded is not None and direct_decoded[0].profile is not None, direct_report
    fitter = FitProcess()
    started = time.monotonic_ns()
    token = (runtime.identity, runtime.baseline_revision)
    report = None
    try:
      assert fitter.submit(snapshot, token=token, now_ns=started)
      while fitter.child is not None and time.monotonic_ns() - started <= 31_000_000_000:
        report = fitter.poll(token=token, now_ns=time.monotonic_ns())
        if report is not None:
          break
        time.sleep(.01)
      assert report is not None, fitter.reason
    finally:
      fitter.close()
    decoded = decode_fit_result(report)
    assert decoded is not None, report
    result, validation = decoded
    assert result.profile is not None and validation is not None, report
    assert report['model_cutoff_ns'] < control.control_ns
    assert adapter.install_fit(report, result, validation, physical_context=CONTEXT)
    # Dynamic samples have finite settling/quantization error: the independently
    # identified static gain must remain within 1%, with tight heldout response
    # error. Production envelope/lag/replay limits are unchanged.
    assert abs(adapter.model.slope - PLANT_SLOPE) < .01 * PLANT_SLOPE
    assert adapter.model.heldout_rmse < .005
    effect = False
    for tick in range(30, 81):
      now = BASE + tick * 10_000_000
      reference, _ = frame(baseline, now, tick)
      actual, _ = accepted_frame(candidate, now, tick, result, validation)
      assert runtime.reason == 'qualified' and runtime.used
      assert runtime.outcome is not None and runtime.outcome.status == 'applied'
      assert actual.latActive == reference.latActive and actual.longActive == reference.longActive
      assert candidate.LaC.torque_params.to_dict() == original_tune
      assert candidate.CP.as_builder().to_bytes() == original_cp
      effect |= actual.actuators.torque != reference.actuators.torque
    assert effect
    accepted_frame(candidate, result.profile.expires_ns + 1, 81, result, validation)
    assert runtime.applied_coordinates == (0.0, 0.0)
    assert runtime.outcome is None or runtime.outcome.status != 'applied'
    destination = os.environ.get('FLM_RAW_WORKER_REPORT')
    if destination:
      Path(destination).write_text(json.dumps({'export': export_cost, 'worker_wall_ns': time.monotonic_ns() - started,
                                              'report': report, 'physical_device_qualification': False}, indent=2) + '\n')
