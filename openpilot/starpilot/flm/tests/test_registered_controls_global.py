"""Production factory -> Controls.state_control -> actual selected update.

Root-owned native execution only. Configuration rows are generated from the
real registry, recorded legacy fingerprints and registered firmware. The
matrix distinguishes path coverage from physical learning qualification.
"""

from contextlib import contextmanager
import copy
import hashlib
import json
import os
import struct
from pathlib import Path
import time
from unittest import mock

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.fingerprints import FW_VERSIONS
from opendbc.car.interfaces import get_interface_attr
from openpilot.cereal import messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.starpilot.flm.learning_wire import result_message, write_source_receipt
from openpilot.starpilot.flm.runtime_adapter_registry import read_source
from openpilot.starpilot.lateral.controller_selection import DOCUMENT_KEY, policy_for, replace_mode
from openpilot.starpilot.lateral.learning_preferences import KEY, replace_preference
from openpilot.starpilot.lateral.tests.test_lane_centering import model


def configurations():
  fingerprints = get_interface_attr('FINGERPRINTS', combine_brands=True)
  for vehicle, factory in sorted(interfaces.items()):
    fingerprints_for_vehicle = [('absent', gen_empty_fingerprint())]
    for index, recorded in enumerate(fingerprints.get(vehicle, ())):
      fingerprint = gen_empty_fingerprint()
      fingerprint[0].update(recorded)
      fingerprints_for_vehicle.append(('legacy-' + str(index), fingerprint))
    firmware = []
    for (ecu, address, subaddress), versions in FW_VERSIONS.get(vehicle, {}).items():
      if versions:
        firmware.append(structs.CarParams.CarFw(ecu=ecu, address=address, subAddress=subaddress or 0, fwVersion=versions[0]))
    seen = set()
    for alpha in (False, True):
      for release in (False, True):
        for fingerprint_name, fingerprint in fingerprints_for_vehicle:
          for firmware_name, observations in (('absent', []), ('registered', firmware)):
            cp = factory.get_params(vehicle, copy.deepcopy(fingerprint), observations, alpha, release, False)
            token = hashlib.sha256(cp.as_reader().as_builder().to_bytes()).hexdigest()
            if token in seen:
              continue
            seen.add(token)
            yield (
              cp,
              {
                'vehicle': vehicle,
                'brand': cp.brand,
                'alpha': alpha,
                'release': release,
                'fingerprint': fingerprint_name,
                'firmware': firmware_name,
                'cp_sha256': token,
              },
            )


def published_cp(cp, params):
  """Apply only the vehicle-declared AOL publication additions used by Card."""
  from openpilot.starpilot.aol.vehicle import policy_for as axis_policy
  from openpilot.starpilot.aol.intent import read_settings, independent_axis_requested
  from openpilot.starpilot.car.hyundai.aol import aol_safety_config
  result = cp.as_reader().as_builder()
  policy = axis_policy(result)
  settings = read_settings(params) if policy.settings_supported else None
  if (settings is not None and policy.intent_supported and not result.passive and
      (policy.full_axis_runtime_required or policy.physical_stalk_owner or
       independent_axis_requested(settings, include_auxiliary=policy.intent_supported and not policy.explicit_latch))):
    config = aol_safety_config(result) if result.brand == 'hyundai' else result.safetyConfigs[0]
    assert config is not None
    config.safetyParam |= policy.safety_param_addition
    result.alternativeExperience |= policy.alternative_experience_addition
  expected = cp.to_dict()
  expected['alternativeExperience'] = result.alternativeExperience
  if 'safetyConfigs' in expected or len(result.safetyConfigs):
    expected['safetyConfigs'] = [entry.to_dict() for entry in result.safetyConfigs]
  assert result.to_dict() == expected  # All tune, vehicle and controller fields preserved.
  return result


def feed(controls, timestamp, tick, *, active=True, enabled=True, fault=False, override=False, signal=False,
         model_age=0, can_valid=True, can_timeout=False, speed=20., calibration=(0.0, 0.0, 0.0)):
  """Real SubMaster deliveries at declared service rates, one synthetic epoch."""
  from openpilot.starpilot.aol.vehicle import native_matches_cp
  from openpilot.starpilot.aol.wire import SafetyState, PandaSlot, encode_safety
  services = ('carState', 'selfdriveState', 'deviceState', 'vehicleParameters', 'longitudinalPlan',
              'lateralDelay', 'onroadEvents', 'modelV2', 'driverMonitoringState', 'extrinsicsCalibration')
  last = getattr(controls, '_global_fixture_last', None)
  first = last is None
  if first:
    last = controls._global_fixture_last = {}

  def packet(service, stamp):
    event = messaging.new_message(service, 0 if service == 'onroadEvents' else None, valid=True, logMonoTime=stamp)
    value = getattr(event, service)
    if service == 'carState':
      value.vEgo = value.vEgoRaw = speed
      value.vCruise = value.vCruiseCluster = 100.0
      value.canValid, value.canTimeout = can_valid, can_timeout
      value.steerFaultTemporary, value.steeringPressed, value.leftBlinker = fault, override, signal
      value.gearShifter = 'drive'
    elif service == 'selfdriveState':
      value.active, value.enabled, value.state = active, enabled, 'enabled' if enabled else 'disabled'
    elif service == 'deviceState':
      value.started, value.startedMonoTime = True, 500_000_000
    elif service == 'vehicleParameters':
      value.stiffnessFactor, value.steerRatio = 1.0, controls.CP.steerRatio
    elif service == 'longitudinalPlan':
      value.aTarget = .5
    elif service == 'lateralDelay':
      value.lateralDelay = .1
    elif service == 'modelV2':
      event.logMonoTime = stamp - model_age
      event.modelV2 = model()
      event.modelV2.action.desiredCurvature = .0002
    elif service == 'extrinsicsCalibration':
      value.calStatus, value.rpyCalib = 'calibrated', calibration
    return event.as_reader()

  if first:
    history = []
    for service in services:
      period = round(1e9 / SERVICE_LIST[service].frequency)
      # 4Hz sources align to tick5/30, including first accepted profile tick30.
      end = timestamp - (200_000_000 if period == 250_000_000 else period)
      for stamp in (end - period, end):
        history.append((stamp, packet(service, stamp)))
      last[service] = end
    for stamp in (timestamp - 450_000_000, timestamp - 200_000_000):
      history.append((stamp, source_packet(controls, stamp, tick, calibration)))
    controls._global_fixture_source_ns = timestamp - 200_000_000
    for stamp, event in sorted(history, key=lambda row: row[0]):
      controls.sm.update_msgs(stamp / 1e9, [event])
  events = []
  for service in services:
    period = round(1e9 / SERVICE_LIST[service].frequency)
    if timestamp - last[service] >= period:
      events.append(packet(service, timestamp))
      last[service] = timestamp
  if controls.aol_replay or controls.ordinary_axis_ack_required:
    configs = tuple(controls.CP.safetyConfigs)
    owners = [(i, c) for i, c in enumerate(configs) if str(c.safetyModel) not in ('silent', 'noOutput')]
    admitted = len(owners) == 1 and native_matches_cp(controls.CP, int(owners[0][1].safetyModel.raw), int(owners[0][1].safetyParam))
    requested_lat = bool(active and enabled)
    requested_long = bool(enabled and controls.aol_replay)
    allowed = bool(admitted and can_valid and not can_timeout and not fault)
    axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=timestamp)
    axis.aolAxisState.qualified = admitted
    axis.aolAxisState.nativeAcknowledged = allowed
    axis.aolAxisState.desiredLateral = requested_lat
    axis.aolAxisState.desiredLongitudinal = requested_long
    axis.aolAxisState.lateralActive = requested_lat and allowed
    axis.aolAxisState.longitudinalActive = requested_long and allowed
    axis.aolAxisState.sessionId = 'global-fixture-axis'
    axis.aolAxisState.observedMonoTime = timestamp
    axis.aolAxisState.validUntilMonoTime = timestamp + 30_000_000
    events.append(axis.as_reader())
    if owners and timestamp - last.get('aolSafetyWire', 0) >= round(1e9 / SERVICE_LIST['aolSafetyWire'].frequency):
      last['aolSafetyWire'] = timestamp
      index, config = owners[0]
      slots = tuple(PandaSlot(i, 'global-panda-' + str(i), int(c.safetyModel.raw), int(c.safetyParam),
                              int(controls.CP.alternativeExperience), allowed and str(c.safetyModel) not in ('silent', 'noOutput'))
                    for i, c in enumerate(configs))
      native = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=timestamp)
      native.aolSafetyWire = encode_safety(SafetyState(1, bool(admitted), timestamp, timestamp + 200_000_000,
        int(config.safetyModel.raw), int(config.safetyParam), allowed and requested_lat, allowed and requested_long,
        requested_lat, requested_long, 'global-panda-' + str(index), 'global-fixture-axis', timestamp, slots))
      events.append(native.as_reader())
  controls.sm.update_msgs(timestamp / 1e9, events)


def source_packet(controls, now, tick, calibration, result=None, validation=None):
  source = read_source(controls.CP, controls.params, now_ns=now, calibration=calibration, vehicle_session='fixture-drive')
  event = result_message(result, 'configuration-regression', tick + 1, now, replay_validation=validation)
  write_source_receipt(event, source)
  return messaging.log_from_bytes(event.to_bytes())


@contextmanager
def controls_for(cp, mode=None, enabled=False):
  with (
    OpenpilotPrefix(),
    mock.patch.dict(
      os.environ, {'REPLAY': '1', 'SIMULATION': '0', 'TORQUE_REPLAY_RUNTIME': '0', 'AOL_REPLAY_RUNTIME': '0', 'LANE_CENTERING_REPLAY_RUNTIME': '0'}
    ),
    mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'),
  ):
    params = Params()
    params.put_bool('OpenpilotEnabledToggle', True, block=True)
    selected = published_cp(cp, params)
    params.put('CarParams', selected.to_bytes(), block=True)
    params.put(KEY, replace_preference(None, cp, enabled), block=True)
    if mode is not None:
      params.put(DOCUMENT_KEY, json.loads(replace_mode(None, cp, mode)), block=True)
    yield Controls()


def frame(controls, now, tick, *, collecting=True, calibration=(0.0, 0.0, 0.0), result=None, validation=None, **health):
  # Bind source identity to the actual Float32 calibration carried by cereal.
  calibration = tuple(struct.unpack('<f', struct.pack('<f', value))[0] for value in calibration)
  feed(controls, now, tick, speed=30.0, calibration=calibration, **health)
  if collecting and now - controls._global_fixture_source_ns >= 250_000_000:
    controls.sm.update_msgs(now / 1e9, [source_packet(controls, now, tick, calibration, result, validation)])
    controls._global_fixture_source_ns = now
  with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
    return controls.state_control()


def test_registered_configuration_paths_disabled_and_insufficient_confidence():
  matrix = []
  for cp, row in configurations():
    modes = (None,) if policy_for(cp) is None else ('standard', 'starpilot')
    for mode in modes:
      with controls_for(cp, mode, False) as baseline, controls_for(cp, mode, True) as candidate:
        before = baseline.CP.as_builder().to_bytes()
        assert type(baseline.LaC) is type(candidate.LaC)
        actual_torque = isinstance(candidate.LaC, LatControlTorque)
        actual_pid_torque = type(candidate.LaC) is LatControlPID and str(cp.steerControlType) == 'torque' and cp.lateralTuning.which() == 'pid'
        eligible = (actual_torque or actual_pid_torque) and not (cp.notCar or cp.passive or cp.dashcamOnly)
        assert (candidate.global_learning.descriptor is not None) is eligible
        original = candidate.LaC.torque_params.to_dict() if actual_torque else None
        updates = 0
        real_update = candidate.LaC.update

        def selected_update(*args, _update=real_update, **kwargs):
          nonlocal updates
          updates += 1
          return _update(*args, **kwargs)

        with mock.patch.object(candidate.LaC, 'update', side_effect=selected_update):
          for tick in range(30):
            now = 400_000_000_000 + tick * 10_000_000
            reference, _ = frame(baseline, now, tick)
            command, _ = frame(candidate, now, tick)
            assert reference.to_dict() == command.to_dict(), (row, mode, tick)
            if actual_torque:
              assert candidate.LaC.torque_params.to_dict() == original
        assert updates == 30  # The production Controls caller actually dispatched.
        assert candidate.CP.as_builder().to_bytes() == before
        extension = getattr(candidate.LaC, 'starpilot_extension', None)
        matrix.append(
          {
            **row,
            'mode': mode or 'default',
            'controller': type(candidate.LaC).__name__,
            'selected_policy': type(extension.policy).__name__ if extension is not None else None,
            'disposition': 'implemented; factory/Controls disabled+collecting parity tested'
            if eligible
            else 'excluded: actual selected non-torque or ineligible CarParams; parity tested',
            'physical_fit': 'missing full worker/emission/plant qualification',
          }
        )
  destination = os.environ.get('FLM_CONFIGURATION_REPORT')
  if destination:
    Path(destination).write_text(json.dumps(matrix, indent=2) + '\n')
  assert matrix and any(row['controller'] == 'LatControlTorque' for row in matrix)


def test_actual_controls_source_freshness_calibration_and_override_rejection():
  from opendbc.car.toyota.values import CAR

  cp = interfaces[CAR.TOYOTA_RAV4_TSS2].get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
  with controls_for(cp, 'standard', True) as controls:
    for tick in range(30):
      frame(controls, 400_000_000_000 + tick * 10_000_000, tick)
    runtime = controls.global_learning
    assert runtime.identity and runtime.reason != 'qualified'
    original_identity = runtime.identity
    frame(controls, 400_300_000_000, 30, calibration=(0.01, 0.0, 0.0))
    assert runtime.identity != original_identity
    assert runtime.applied_coordinates == (0.0, 0.0)
    for tick, health in enumerate(({'override': True}, {'fault': True}, {'can_timeout': True}, {'can_valid': False}, {'model_age': 500_000_000}), 31):
      frame(controls, 400_000_000_000 + tick * 10_000_000, tick, **health)
      assert not runtime.eligible and runtime.applied_coordinates == (0.0, 0.0)


def test_actual_controls_added_cost_collecting_path():
  from opendbc.car.toyota.values import CAR

  cp = interfaces[CAR.TOYOTA_RAV4_TSS2].get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
  measured = []
  with controls_for(cp, 'standard', True) as controls:
    for tick in range(1000):
      now = 500_000_000_000 + tick * 10_000_000
      start_cpu, start_wall = time.thread_time_ns(), time.perf_counter_ns()
      frame(controls, now, tick)
      measured.append((time.thread_time_ns() - start_cpu, time.perf_counter_ns() - start_wall))
  # The harness includes message production; report it explicitly. A separate
  # source-matched preimage comparison is mandatory for added production cost.
  destination = os.environ.get('FLM_CONTROLS_PERFORMANCE_REPORT')
  if destination:
    Path(destination).write_text(
      json.dumps(
        {
          'scope': 'actual Controls plus fixture message production',
          'frames': len(measured),
          'thread_cpu_ns': sum(cpu for cpu, _ in measured),
          'wall_ns': sum(wall for _, wall in measured),
          'max_frame_wall_ns': max(wall for _, wall in measured),
          'added_production_cost': 'requires exclusive preimage/candidate paired run',
        },
        indent=2,
      )
      + '\n'
    )


def test_actual_nonfinite_factory_car_params_identity_is_lossless():
  import math

  from opendbc.car.body.values import CAR as BODY_CAR
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest, digest

  cp = interfaces[BODY_CAR.COMMA_BODY].get_non_essential_params(BODY_CAR.COMMA_BODY)
  assert cp.minSteerSpeed == -math.inf and cp.maxLateralAccel == math.inf
  original = cp.to_dict()
  expected = car_params_digest(cp)
  assert car_params_digest(cp.as_reader()) == car_params_digest(cp.as_reader().as_builder()) == expected
  source = read_source(cp.as_reader(), Params(), now_ns=1_000_000_000)
  assert source.cp_sha256 == expected
  assert cp.to_dict() == original
  for field, first, second in (('maxLateralAccel', math.inf, -math.inf), ('minSteerSpeed', 0.0, -0.0)):
    changed = cp.as_reader().as_builder()
    setattr(changed, field, first)
    first_digest = car_params_digest(changed)
    setattr(changed, field, second)
    assert car_params_digest(changed) != first_digest
  changed = cp.as_reader().as_builder()
  changed.mass += 1.0
  assert car_params_digest(changed) != expected
  import pytest

  with pytest.raises(ValueError):
    digest({'physical_observation': math.inf})
