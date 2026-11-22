"""Registered torque-actuated PID replay; never reads inactive torque tuning."""
import math
from openpilot.starpilot.flm.physical_response import request_force_accel
from types import SimpleNamespace

PID_SNAPSHOT_REVISION = 'selected-pid-frozen-state-v1'
PID_STAGE_REVISION = 'pid-selected-stage-snapshot-v2'
PID_FIELDS = ('dt', 'sat_limit', 'sat_time', 'sat_check_min_speed', 'steer_max', 'ff_factor', 'automatic_pid_previous_setpoint')


def capture_pid_snapshot(controller, vm):
  from openpilot.starpilot.flm.controller_replay import Snapshot, _tree, _object, _VM_FIELDS
  values = []
  for name in PID_FIELDS:
    value = getattr(controller, name)
    if value is None:
      values.append(('controller.' + name + '.none', 1.))
    else:
      _tree(values, 'controller.' + name, value)
  _object(values, 'pid', controller.pid)
  for name in _VM_FIELDS:
    _tree(values, 'vm.' + name, getattr(vm, name))
  return Snapshot(PID_SNAPSHOT_REVISION, tuple(values))


def pid_snapshot_from_values(values):
  from openpilot.starpilot.flm.controller_replay import Snapshot, snapshot_from_values
  checked = snapshot_from_values(values)
  return Snapshot(PID_SNAPSHOT_REVISION, checked.values)


def restore_pid_snapshot(controller, vm, snapshot):
  from openpilot.starpilot.flm.controller_replay import _decode
  from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values
  if snapshot.revision != PID_SNAPSHOT_REVISION:
    raise ValueError('PID checkpoint revision')
  original = baseline_values(controller)
  def immutable_pid():
    scalars = tuple(float(getattr(controller, name)) for name in
      ('dt', 'sat_limit', 'sat_check_min_speed', 'steer_max', 'ff_factor'))
    gains = tuple(tuple(tuple(float(value) for value in row) for row in getattr(controller.pid, key))
                  for key in ('_k_p', '_k_i', '_k_d'))
    return scalars, gains, float(controller.pid.i_dt), float(controller.pid.pos_limit), float(controller.pid.neg_limit)
  original_immutable = immutable_pid()
  reference = capture_pid_snapshot(controller, vm)
  values = dict(snapshot.values)
  def shape(rows):
    return {key.removesuffix('.none') for key, _ in rows if not any(bit.isdigit() for bit in key.split('.'))}
  if shape(reference.values) != shape(snapshot.values):
    raise ValueError('incomplete or foreign PID checkpoint')
  roots = {'controller': controller, 'pid': controller.pid, 'vm': vm}
  top = set()
  for key in values:
    bits = key.split('.')
    index = next((i for i, bit in enumerate(bits) if bit.isdigit() or bit in ('kind', 'length', 'maxlen', 'none')), len(bits))
    top.add('.'.join(bits[:index]))
  for path in top:
    bits = path.split('.')
    obj = roots[bits[0]]
    for bit in bits[1:-1]:
      obj = getattr(obj, bit)
    name = bits[-1]
    setattr(obj, name, _decode(values, path, getattr(obj, name)))
  if baseline_values(controller) != original or immutable_pid() != original_immutable:
    raise ValueError('PID checkpoint changed immutable actual gains')


class PidControllerFactory:
  def __init__(self, cp, mode, *, turn_assist=False, manual_baseline=(), manual_source_epoch='', manual_kp=None):
    from opendbc.car import structs
    from opendbc.car.car_helpers import interfaces
    if (cp.notCar or cp.passive or cp.dashcamOnly or str(cp.carFingerprint) not in interfaces or cp.lateralTuning.which() != 'pid' or
        str(cp.steerControlType) != 'torque' or mode != 'native' or turn_assist or manual_baseline):
      raise ValueError('registered stock torque PID configuration')
    self.cp = structs.CarParams.new_message(**cp.to_dict()).as_reader()
    self.mode, self.turn_assist = 'native', False
    self.interface_class = interfaces[str(cp.carFingerprint)]
    if bool(manual_source_epoch) != (manual_kp is not None) or manual_source_epoch and (
        type(manual_source_epoch) is not str or len(manual_source_epoch) != 64 or any(c not in '0123456789abcdef' for c in manual_source_epoch)):
      raise ValueError('fixed PID gain source requires its settings epoch')
    self.manual_baseline, self.manual_source_epoch, self.manual_kp = (), manual_source_epoch, manual_kp

  def create(self):
    from opendbc.car.vehicle_model import VehicleModel
    from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
    from openpilot.selfdrive.controls.lib.lateral_controller_factory import create_lateral_controller
    ci = self.interface_class(self.cp)
    controller = create_lateral_controller(self.cp, ci, .01, mode='native', turn_assist=False)
    if type(controller) is not LatControlPID:
      raise ValueError('actual stock PID constructor mismatch')
    if self.manual_kp is not None:
      from openpilot.starpilot.lateral.gain_runtime import install_fixed_kp
      install_fixed_kp(self.cp, controller, self.manual_kp)
    return controller, VehicleModel(self.cp)


def run_pid(controller, vm, inputs, *, effect_trial=None, _prepared_car_state=None):
  from openpilot.starpilot.flm.controller_replay import _VM_FIELDS, _replay_car_state
  from openpilot.starpilot.flm.selected_effects import EffectReplaySource
  values = dict(inputs)
  cs = _replay_car_state(inputs, values, _prepared_car_state)
  for name in _VM_FIELDS:
    setattr(vm, name, values['vm.' + name])
  params = SimpleNamespace(angleOffsetDeg=values['angleOffsetDeg'], roll=values['roll'])
  source = effect_trial
  if source is not None:
    if not isinstance(source, EffectReplaySource):
      source = EffectReplaySource(source)
    source.begin_frame(int(values['control_frame']))
  controller.automatic_flm_effects = source
  from openpilot.starpilot.flm.pid_stages import PidObservation
  observation = PidObservation()
  observation.begin_frame()
  controller.automatic_flm_observation = observation
  output, _, log = controller.update(bool(values['active']), cs, vm, params, bool(values['safety_limited']),
    values['desired_curvature'], bool(values['curvature_limited']), values['lat_delay'])
  if (source is not None and values['active'] and not cs.steeringPressed and
      not values['safety_limited'] and not values['curvature_limited'] and
      (source.stage_calls != 1 or source.pid_error_calls != 1 or source.friction_calls != 0 or not controller.automatic_pid_stage_admitted)):
    raise ValueError('actual PID effect stage absent/duplicated or fabricated friction')
  if not math.isfinite(output):
    raise ValueError('PID replay nonfinite output')
  return float(output), log


def pid_frame(evidence):
  from openpilot.starpilot.flm.controller_replay import ReplayFrame
  stage = dict(evidence.stage_values)
  return ReplayFrame(evidence.control_ns, evidence.replay_inputs, evidence.requested, evidence.applied_coordinates,
    frame_sequence=int(dict(evidence.replay_inputs)['control_frame']), actual_lac_command=stage['selected_command'],
    torque_values=(), actual_stage_residual=(0., 0.), centre_values=(0., 0.),
    desired_physical_accel=request_force_accel(evidence.replay_inputs),
    desired_kinematic_accel=stage['setpoint'],
    healthy=evidence.valid and not evidence.fault and not evidence.override, manual=evidence.manual, limited=evidence.limited,
    applied_parameter_names=tuple(evidence.applied_parameter_names), effect_trace=evidence.effect_trace,
    effect_prepare_ns=evidence.effect_prepare_ns, controller_domain='torque_pid',
    vehicle_model_anchor=evidence.vehicle_model_anchor, live_applied_coordinates=evidence.live_applied_coordinates)
