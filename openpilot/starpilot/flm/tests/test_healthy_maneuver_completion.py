"""Real selected controller and CAN evidence retain a healthy finished maneuver."""
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest
from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.steering_projection import install_observer, inspect_projection, normalized_applied_command
from opendbc.car.toyota.values import CAR
from openpilot.cereal import messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.starpilot.flm.controller_replay import SelectedControllerFactory, capture_snapshot, frame_input, final_command
from openpilot.starpilot.flm.fullrate_collector import ControlEvidence, AppliedEvidence, FullrateCollector, PhysicalEvidence
from openpilot.starpilot.flm.learner_factory import TorqueCollectionAdapter, objective_key
from openpilot.starpilot.flm.learning_wire import write_control, read_control, write_applied, read_applied
from openpilot.starpilot.flm.torque_adapter import Objective, gates
from openpilot.starpilot.flm.runtime_adapter_registry import current_authority, read_source
from openpilot.starpilot.flm.torque_observation import TorqueObservation, REVISION
from openpilot.starpilot.flm.tests.test_registered_controls_global import controls_for, frame

PLANT_SLOPE = 1.98
PLANT_TAU_SECONDS = .05
BASE = 400_000_000_000
CONTEXT = ('a' * 64, 'b' * 64, 'c' * 64, 0)


def actual_completion_trace(cp, runtime, params_store, failure):
  factory = SelectedControllerFactory(cp, 'standard', turn_assist=runtime.descriptor.turn_assist)
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

  adapter = collector = None
  boundary = None
  sequence = 0
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

  for block in range(1):
    controller, vm = factory.create()
    observer = TorqueObservation()
    controller.automatic_flm_observation = observer
    params = SimpleNamespace(angleOffsetDeg=0.0, roll=0.0)
    direction = 1 if block % 2 == 0 else -1
    # Broader independent model training, narrower model heldout and stage
    # requests. These fixed maneuvers never enlarge a fitted inverse envelope.
    cs = structs.CarState(vEgo=30.0, vEgoRaw=30.0, canValid=True, gearShifter=structs.CarState.GearShifter.drive)
    cs.cruiseState.available = cs.cruiseState.enabled = True
    ci.CS.out = cs.as_reader()
    start = BASE - 64_000_000_000 + block * 4_000_000_000
    source = None
    for tick in range(780):
      stamp = start + tick * 10_000_000
      goal = max(0., .40 - .0006 * max(0, tick - 500))
      desired = direction * goal / 900.0
      advance_physics(stamp)
      # State is measured BEFORE this tick’s CAN application: previous plant
      # output, never the desired trajectory or a native inverse-torque table.
      cs.steeringAngleDeg = math.degrees(vm.get_steer_from_curvature(-physical_accel / 900.0, 30.0, 0.0))
      # Toyota CarState exposes STEER_TORQUE_EPS in native motor counts
      # (after eps_torque_scale), as consumed by the real measured limiter.
      cs.steeringTorqueEps = measured_eps_torque
      update_health(stamp)
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
        baseline_values=runtime.initial_values, controller_revision=runtime.descriptor.controller_revision, turn_assist=runtime.descriptor.turn_assist,
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
      if collector is None:
        collector = FullrateCollector(adapter)
      adapter.select_objective(objective_key(control))
      assert adapter.prepare_control(control) is control
      assert collector.control(control, applied.apply_ns)
      assert collector.output(applied, applied.apply_ns)
      adapter.replay.emissions.verify(control, applied)
      if tick % 5 == 0:
        observed = applied.apply_ns + 1_000_000
        physical = PhysicalEvidence(observed, observed, CONTEXT[0], CONTEXT[1], CONTEXT[2], 0,
                                    30., physical_accel / 30., 0., float(cs.steeringAngleDeg),
                                    True, False, False, allowed)
        low = abs(dict(control.stage_values)['setpoint']) < .25
        before = tuple(collector.raw_points)
        if low and before and boundary is None:
          if failure == 'native':
            physical = replace(physical, native_allowed=False)
          elif failure == 'fault':
            physical = replace(physical, fault=True)
          elif failure == 'generation':
            physical = replace(physical, calibration='changed-calibration')
          accepted = collector.physical(physical, observed)
          boundary = (before, tuple(collector.blocks), accepted, control, applied, physical)
          break
        collector.physical(physical, observed)
    if boundary is not None:
      break
  assert boundary is not None, 'actual controller never exited the excited maneuver'
  return collector, adapter, boundary


@pytest.mark.parametrize('failure', [None, 'native', 'fault', 'generation'])
def test_actual_factory_normalized_native_projected_healthy_maneuver_completion(failure):
  cp = interfaces[CAR.TOYOTA_RAV4_TSS2].get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
  with controls_for(cp, 'standard', True) as controls:
    for tick in range(30):
      frame(controls, BASE + tick * 10_000_000, tick)
    runtime = controls.global_learning
    assert runtime.identity and runtime.eligible
    collector, adapter, boundary = actual_completion_trace(controls.CP, runtime, controls.params, failure)
    before, blocks, accepted, control, applied, physical = boundary
    assert 40 <= len(before) < 80
    assert isinstance(adapter, TorqueCollectionAdapter)
    assert control.domain == applied.domain == 'normalized_torque'
    assert applied.projection is not None and applied.native_allowed
    if failure is None:
      assert adapter.raw_maneuver_complete(control, {})
      for field, value in (
        ('identity', 'different'), ('generation', 'different'), ('source_epoch', 'different'),
        ('baseline_revision', 'different'), ('domain', 'angle'), ('stage_schema_revision', 'different'),
      ):
        assert not adapter.raw_maneuver_complete(replace(control, **{field: value}), {})
      assert not adapter.raw_maneuver_complete(replace(control, stage_values=tuple(
        (key, 20.0 if key == 'speed' else value) for key, value in control.stage_values)), {})
    assert not accepted and not collector.raw_points
    if failure is None:
      # The healthy low-excitation edge ends estimation, not the actual source
      # and encoder trace. Preserve original chronology without adding a low-
      # excitation physical point or manufacturing another two-second warmup.
      assert collector.ready and collector.ready[-1] == (control, applied)
      assert 0 < collector.active_since <= before[0].apply_ns <= applied.apply_ns
      assert collector.last_apply == applied.apply_ns
      assert collector.cohort is None and not collector.sealed
      assert collector.reason == 'waiting_for_excited_maneuver'
      adapter.replay.emissions.verify(control, applied)
      assert len(blocks) == 1
      assert blocks[0].samples == before
      assert all(abs(dict(point.control.stage_values)['setpoint']) >= .25 for point in blocks[0].samples)
      assert control.control_ns > blocks[0].samples[-1].control_ns
    else:
      assert not collector.ready and collector.active_since == 0
      assert not blocks
