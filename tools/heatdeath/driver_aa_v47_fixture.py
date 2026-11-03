#!/usr/bin/env python3
"""Private current-production joined IPC bench with actual RadarD and bounded RAM capture.

Normal current daemon mains; no private CPU/affinity/candidate overlays.
"""

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time
import uuid

from bounded_capture_v1 import BoundedCapture

HERE = Path(__file__).resolve().parent
MAGIC = 0x3143415254425053
BINARY_OBSERVER_MAGIC = b'SPBIOB01'
BINARY_OBSERVER_HEADER = struct.Struct('<QHI')  # receive MONO ns, service index, raw Event byte count
CAN_RETURNED_BUS_OFFSET = 0x80  # production panda.h / unpack_can_buffer
CAN_REJECTED_BUS_OFFSET = 0xC0
RADAR_OUTPUTS = {'radarState', 'starpilotRadarState'}
PLANNER_OUTPUTS = {'longitudinalPlan', 'driverAssistance', 'starpilotLongitudinalPlan', 'slcState', 'slcCruiseCommand'}
# These are declared inputs, never enabled or rewritten by this driver. Missing
# snapshot keys use production defaults; they do not establish device absence.
PLANNER_PREF_KEYS = (
  tuple(
    '''UseStarPilotLongitudinalPlanner ConditionalModeConfig SafeMode ExperimentalMode
CurveSpeedController CurveSpeedControllerNoLead CurveComfortData CurvatureData
CustomPersonalities LongitudinalPersonalityProfiles LongitudinalPersonality
LeadApproachBuffer FasterLeadTakeoff ForceStops ForceStopDistanceOffset
LaneChangePreferences PulseGlideSpeedDelta
SpeedLimitController ShowSpeedLimits IsMetric SLCPriority1 SLCPriority2 SLCPriority3
SLCOnEngage SLCOverrideMode SLCConfirmation SLCAdvanceHigherSeconds SLCAdvanceLowerSeconds SLCOffsetSchedule
TrafficFollow TrafficJerkAcceleration TrafficJerkDeceleration TrafficJerkSpeed
TrafficJerkSpeedDecrease TrafficJerkDanger'''.split()
  )
  + tuple(
    personality + setting
    for personality in ('Aggressive', 'Standard', 'Relaxed')
    for setting in ('Follow', 'FollowHigh', 'JerkAcceleration', 'JerkDeceleration', 'JerkSpeed', 'JerkSpeedDecrease', 'JerkDanger')
  )
  + tuple('Offset' + str(i) for i in range(1, 8))
)
OWNED = (
  {
    'can',
    'pandaStates',
    'peripheralState',
    'aolSafetyWire',
    'sendcan',
    'carState',
    'carParams',
    'carOutput',
    'radarTracks',
    'starpilotCarState',
    'slcCruiseEvent',
    'slcDashboardObservation',
    'aolIntentWire',
    'selfdriveState',
    'onroadEvents',
    'aolAxisState',
    'starpilotSelfdriveState',
    'carControl',
    'controlsState',
    'starpilotLateralState',
    'autoSteeringLearning',
  }
  | PLANNER_OUTPUTS
  | RADAR_OUTPUTS
)
PEERS = {
  'deviceState',
  'modelV2',
  'vehicleParameters',
  'lateralDelay',
  'lateralTorqueParameters',
  'extrinsicsCalibration',
  'driverMonitoringState',
  'deviceMotion',
  'narrowRoadCameraState',
  'wideRoadCameraState',
  'cabinCameraState',
  'accelerometer',
  'gyroscope',
  'gpsLocation',
  'gpsLocationExternal',
}
BOOT_EVENTS = {'can', 'narrowRoadCameraState', 'wideRoadCameraState', 'cabinCameraState'}
# Explicit reviewed clock paths. Values are shifted by a constant, preserving
# their original age and duration; zero/absent fields remain zero/absent.
MONO_PATHS = {
  'radarState': ('mdMonoTime',),
  'modelV2': ('meta.locationMonoTime',),
  'deviceState': ('startedMonoTime',),
  'accelerometer': ('timestamp',),
  'gyroscope': ('timestamp',),
}
BOOT_PATHS = {
  'modelV2': ('timestampEof',),
  'narrowRoadCameraState': ('timestampSof', 'timestampEof'),
  'wideRoadCameraState': ('timestampSof', 'timestampEof'),
  'cabinCameraState': ('timestampSof', 'timestampEof'),
}
FORBIDDEN = (
  'REPLAY',
  'SIMULATION',
  'CEREAL_FAKE',
  'FAKESEND',
  'STARTED',
  'BOARDD_LOOPBACK',
  'BOARDD_SKIP_FW_CHECK',
  'PANDAD_MAXOUT',
  'ZMQ',
  'FINGERPRINT',
  'SKIP_FW_QUERY',
  'SLC_REPLAY_RUNTIME',
  'CONDITIONAL_MODE_REPLAY_RUNTIME',
  'CURVE_REPLAY_RUNTIME',
  'LONG_PLANNER_REPLAY_RUNTIME',
  'SLC_VISION_DEVELOPMENT',
  'STARPILOT_PANDA_FIRMWARE_MAP',
  'STARPILOT_PANDA_FIRMWARE_NAME',
)


def digest(path):
  return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def can_source_kind(source):
  if 0 <= source < 4:
    return 'incoming'
  if CAN_RETURNED_BUS_OFFSET <= source < CAN_RETURNED_BUS_OFFSET + 4:
    return 'historicalReturned'
  if CAN_REJECTED_BUS_OFFSET <= source < CAN_REJECTED_BUS_OFFSET + 4:
    return 'historicalRejected'
  raise AssertionError(f'Unknown CP1 CAN source {source}; additional Panda buses/remapping forbidden')


def configure_imports(repo):
  repo = Path(repo).resolve()
  assert (repo / 'openpilot/cereal/log.capnp').is_file()
  sys.path[:0] = [str(repo / p) for p in ('', 'opendbc_repo', 'msgq_repo', 'rednose_repo', 'tinygrad_repo')]
  return repo


def prepare(args):
  repo = configure_imports(args.repo)
  from openpilot.tools.lib.logreader import LogReader
  from openpilot.cereal import messaging

  out = args.output.resolve()
  assert not out.exists() and not out.is_relative_to(repo)
  assert 1 <= args.duration <= 120 and args.source_boot_minus_mono_ns >= 0
  out.mkdir(mode=0o700, parents=True)
  begin, end = args.start_mono_ns, args.start_mono_ns + round(args.duration * 1e9)
  selected, can = [], []
  can_sources = {'incoming': 0, 'historicalReturned': 0, 'historicalRejected': 0}
  for path in args.rlog:
    for msg in LogReader(str(path.resolve())):
      service = msg.which()
      if service not in PEERS | {'can'}:
        continue
      mono = int(msg.logMonoTime) - (args.source_boot_minus_mono_ns if service in BOOT_EVENTS else 0)
      if begin <= mono < end:
        if service == 'can':
          frames = [(int(f.address), int(f.src), bytes(f.dat)) for f in msg.can]
          for _, source, _ in frames:
            can_sources[can_source_kind(source)] += 1
          can.append((mono - begin, frames))
        else:
          selected.append((mono - begin, msg.as_builder().to_bytes()))
  can.sort(key=lambda row: row[0])
  selected.sort(key=lambda row: row[0])
  assert can and can[-1][0] - can[0][0] >= round((args.duration - 0.5) * 1e9)
  with (out / 'can.trace').open('xb') as stream:
    stream.write(struct.pack('<QQI', MAGIC, end - begin, len(can)))
    for offset, frames in can:
      stream.write(struct.pack('<QI', offset, len(frames)))
      for address, bus, data in frames:
        stream.write(struct.pack('<IBB', address, bus, len(data)) + data)
  with (out / 'peers.trace').open('xb') as stream:
    for offset, raw in selected:
      stream.write(struct.pack('<QI', offset, len(raw)) + raw)
  metadata = {
    'kind': 'private-realtime-inputs',
    'sourceStartMonoNs': begin,
    'durationNs': end - begin,
    'sourceBootMinusMonoNs': args.source_boot_minus_mono_ns,
    'deviceMotionTimestampClock': args.device_motion_clock,
    'rlogs': [{'path': str(p.resolve()), 'sha256': digest(p)} for p in args.rlog],
    'canBatches': len(can),
    'peerEvents': len(selected),
    'canSourceFrames': can_sources,
    'canSourceContract': {
      'incomingPhysicalBuses': [0, 1, 2, 3],
      'returnedOffset': CAN_RETURNED_BUS_OFFSET,
      'rejectedOffset': CAN_REJECTED_BUS_OFFSET,
      'historicalFlagsPreserved': True,
      'historicalFlagsFeedNativeRx': False,
    },
    'services': sorted({messaging.log_from_bytes(raw).which() for _, raw in selected}),
    'clockPaths': {'monotonic': MONO_PATHS, 'boottime': BOOT_PATHS},
    'limitations': [
      'Recorded peer payloads are workload, not live sensors or present thermal telemetry.',
      'Actual Card startup/ECU handoff must pass without fabricated replies.',
      'This CP1 endpoint does not implement firmware heartbeat timeout-loop equivalence.',
    ],
  }
  metadata['files'] = {p.name: digest(p) for p in (out / 'can.trace', out / 'peers.trace')}
  (out / 'inputs.json').write_text(json.dumps(metadata, indent=2) + '\n')


def shift(body, path, delta):
  try:
    keys = path.split('.')
    for key in keys[:-1]:
      body = getattr(body, key)
    value = int(getattr(body, keys[-1]))
    if value:
      assert value + delta > 0
      setattr(body, keys[-1], value + delta)
  except AttributeError:
    pass  # An absent field is not created by the bench.


def reanchor(msg, offset, meta, mono_start, boot_start):
  service = msg.which()
  msg.logMonoTime = (boot_start if service in BOOT_EVENTS else mono_start) + offset
  body = getattr(msg, service)
  for path in MONO_PATHS.get(service, ()):
    shift(body, path, mono_start - meta['sourceStartMonoNs'])
  for path in BOOT_PATHS.get(service, ()):
    shift(body, path, boot_start - meta['sourceStartMonoNs'] - meta['sourceBootMinusMonoNs'])
  if service == 'deviceMotion':
    delta = (
      mono_start - meta['sourceStartMonoNs']
      if meta['deviceMotionTimestampClock'] == 'monotonic'
      else boot_start - meta['sourceStartMonoNs'] - meta['sourceBootMinusMonoNs']
    )
    shift(body, 'timestamp', delta)
  return msg


def read_peers(path, messaging, suppressed):
  result = []
  with path.open('rb') as stream:
    while header := stream.read(12):
      assert len(header) == 12
      offset, size = struct.unpack('<QI', header)
      assert 0 < size <= 4_000_000
      raw = stream.read(size)
      assert len(raw) == size
      message = messaging.log_from_bytes(raw)
      # A previously prepared v4 packet may contain these old recorded peers.
      # Never publish them alongside the actual selected planner.
      if message.which() in PLANNER_OUTPUTS | RADAR_OUTPUTS:
        name = message.which()
        suppressed[name] = suppressed.get(name, 0) + 1
        continue
      assert message.which() in PEERS and message.which() not in OWNED
      result.append((offset, message))
  return result


def child_record(pid):
  task = Path(f'/proc/{pid}')
  return {
    'pid': pid,
    'cmdline': (task / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace'),
    'exe': os.readlink(task / 'exe'),
    'schedPolicy': os.sched_getscheduler(pid),
    'priority': os.sched_getparam(pid).sched_priority,
    'affinity': sorted(os.sched_getaffinity(pid)),
    'startTicks': (task / 'stat').read_text().rsplit(')', 1)[1].split()[19],
  }


def process_usage(pid):
  task = Path(f'/proc/{pid}')
  fields = (task / 'stat').read_text().rsplit(')', 1)[1].split()
  sched = [int(value) for value in (task / 'schedstat').read_text().split()]
  return {
    'pid': pid,
    'userTicks': int(fields[11]),
    'systemTicks': int(fields[12]),
    'startTicks': int(fields[19]),
    'mainThreadRunNs': sched[0],
    'mainThreadWaitNs': sched[1],
    'mainThreadTimeslices': sched[2],
  }


def private_native_core2(child, endpoint):
  """Observe real production setup, then move only this owned virtual child.

  Main is pinned first, so subsequently created threads inherit core2. Every
  already-existing thread is then pinned, followed by one extra enumeration.
  No policy/priority or other process affinity is changed.
  """
  deadline = time.monotonic_ns() + 3_000_000_000
  expected_exe = str(endpoint.resolve())
  while True:
    assert child.poll() is None, 'Owned virtual native child exited before scheduler setup'
    record = child_record(child.pid)
    assert record['exe'] == expected_exe, 'Owned virtual endpoint executable changed'
    if record['schedPolicy'] == os.SCHED_FIFO and record['priority'] == 54 and record['affinity'] == [3]:
      break
    assert time.monotonic_ns() < deadline, 'No actual production FIFO54/core3 setup within3s'
    time.sleep(0.005)
  pid, start_ticks = child.pid, record['startTicks']

  def tasks():
    result = {}
    for task in sorted((Path(f'/proc/{pid}') / 'task').iterdir(), key=lambda p: int(p.name)):
      tid = int(task.name)
      fields = (task / 'stat').read_text().rsplit(')', 1)[1].split()
      result[tid] = {
        'tid': tid,
        'startTicks': fields[19],
        'policy': os.sched_getscheduler(tid),
        'priority': os.sched_getparam(tid).sched_priority,
        'affinity': sorted(os.sched_getaffinity(tid)),
      }
    return result

  before = tasks()
  assert pid in before and before[pid]['startTicks'] == start_ticks
  assert time.monotonic_ns() < deadline
  os.sched_setaffinity(pid, {2})
  current = tasks()
  for tid, task in current.items():
    assert time.monotonic_ns() < deadline
    assert child.poll() is None and child_record(pid)['startTicks'] == start_ticks
    current_start = (Path(f'/proc/{pid}/task/{tid}') / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    assert current_start == task['startTicks'], 'Owned virtual thread exited/reused before pin'
    if tid in before:
      assert task['startTicks'] == before[tid]['startTicks'], 'Virtual child thread identity changed'
    os.sched_setaffinity(tid, {2})
  after = tasks()
  assert after and all(task['affinity'] == [2] for task in after.values())
  # Extra enumeration after yielding lets a concurrently created send thread
  # appear; it must inherit core2 from main rather than requiring a new pin.
  time.sleep(0.005)
  verified = tasks()
  assert time.monotonic_ns() < deadline
  assert child.poll() is None and child_record(pid)['startTicks'] == start_ticks
  assert verified and all(task['affinity'] == [2] for task in verified.values())
  for tid, task in verified.items():
    previous = after.get(tid, current.get(tid, before.get(tid)))
    if previous:
      assert task['startTicks'] == previous['startTicks'], 'Virtual child TID reused'
      assert task['policy'] == previous['policy'] and task['priority'] == previous['priority']
  assert verified[pid]['policy'] == os.SCHED_FIFO and verified[pid]['priority'] == 54
  return {
    'enabled': True,
    'pid': pid,
    'processStartTicks': start_ticks,
    'observedProductionSetup': record,
    'threadsBefore': list(before.values()),
    'threadsAfterPin': list(after.values()),
    'threadsAfterExtraEnumeration': list(verified.values()),
    'newThreadsAfterPin': sorted(set(verified) - set(after)),
    'policyAndPriorityChanged': False,
    'onlyOwnedVirtualEndpoint': True,
    'limitation': 'Virtual native workload moved to core2; production pandad normally uses core3. Real daemons untouched.',
  }


def decode_observations(args):
  repo = configure_imports(args.repo)
  from openpilot.cereal import messaging

  source, output = args.capture.resolve(), args.output.resolve()
  assert not output.exists() and source != output and not output.is_relative_to(repo)
  metadata = json.loads(args.metadata.read_text())
  assert metadata['format'] == 'SPBIOB01' and digest(source) == metadata['sha256']
  names, counts = metadata['services'], dict.fromkeys(metadata['services'], 0)
  with source.open('rb') as capture, output.open('x') as decoded:
    assert capture.read(len(BINARY_OBSERVER_MAGIC)) == BINARY_OBSERVER_MAGIC
    while header := capture.read(BINARY_OBSERVER_HEADER.size):
      assert len(header) == BINARY_OBSERVER_HEADER.size
      received, service_index, size = BINARY_OBSERVER_HEADER.unpack(header)
      assert service_index < len(names) and 0 < size <= 8_000_000
      raw = capture.read(size)
      assert len(raw) == size
      event = messaging.log_from_bytes(raw)
      name = names[service_index]
      assert event.which() == name
      decoded.write(
        json.dumps(
          {'receivedMonoNs': received, 'service': name, 'event': event.to_dict()},
          separators=(',', ':'),
          default=lambda value: (
            {'base64': base64.b64encode(value).decode()} if isinstance(value, bytes) else (_ for _ in ()).throw(TypeError(type(value).__name__))
          ),
        )
        + '\n'
      )
      counts[name] += 1
  assert counts == metadata['messages'], 'Incomplete raw observation capture'
  print(json.dumps({'capture': str(source), 'decoded': str(output), 'messages': counts, 'sourceSha256': metadata['sha256']}))


def run(args):
  repo = configure_imports(args.repo)
  helpers = args.helpers_dir.resolve() if args.helpers_dir is not None else HERE
  assert helpers.is_dir()
  assert 1 <= args.timing_limit <= 16000

  def interrupted(signum, _frame):
    raise SystemExit('private driver signal ' + str(signum))

  for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(signum, interrupted)
  assert sys.dont_write_bytecode and sys.platform == 'linux'
  assert all(name not in os.environ for name in FORBIDDEN)
  assert os.environ.get('AOL_REPLAY_RUNTIME', '0') == '0'
  assert args.root_grant == 'execute-private-native-ipc-bench'
  source_pins = json.loads((HERE / 'joined-source-pins.json').read_text())
  assert (
    subprocess.check_output(['git', '-c', 'safe.directory=' + str(repo), '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip() == source_pins['sourceHead']
  )
  assert subprocess.check_output(['git', '-c', 'safe.directory=' + str(repo), '-C', str(repo), 'status', '--porcelain']) == b''
  assert all(digest(repo / name) == wanted for name, wanted in source_pins['sources'].items())
  assert all(digest(HERE / name) == wanted for name, wanted in source_pins['helpers'].items())
  assert args.observer_format == 'binary', 'Bounded RAM collector supports raw binary only'
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.starpilot.schema_cache import put_cache
  from opendbc.car.structs import car
  from openpilot.starpilot.aol.runtime import current_native, current_axis

  packet = args.inputs.resolve()
  meta = json.loads((packet / 'inputs.json').read_text())
  for name, expected in meta['files'].items():
    assert digest(packet / name) == expected
  assert digest(args.native_library) == args.native_library_sha256
  assert digest(args.native_endpoint) == args.native_endpoint_sha256
  assert digest(args.car_params) == args.car_params_sha256
  assert (args.cruise_checkpoint is None) == (args.cruise_checkpoint_sha256 is None)
  assert args.steady_state_checkpoint and args.cruise_checkpoint is not None, 'Joined packet requires explicit genuine cruise phase checkpoint'
  cruise_checkpoint = None
  if args.cruise_checkpoint is not None:
    assert args.steady_state_checkpoint, 'Cruise memory requires explicit already-running checkpoint mode'
    assert digest(args.cruise_checkpoint) == args.cruise_checkpoint_sha256
    cruise_checkpoint = json.loads(args.cruise_checkpoint.read_text())
    assert cruise_checkpoint['sourceStartMonoNs'] == meta['sourceStartMonoNs']
    assert cruise_checkpoint['cpSha256'] == args.car_params_sha256
    assert cruise_checkpoint['carSchemaSha256'] == args.car_schema_sha256
    assert any(
      item['path'] == str(Path(cruise_checkpoint['sourceRlog']).resolve()) and item['sha256'] == cruise_checkpoint['sourceRlogSha256'] for item in meta['rlogs']
    )
  if args.steady_state_checkpoint:
    assert args.route_car_params is not None and args.route_car_params_sha256 and args.car_schema_sha256
    assert digest(args.route_car_params) == args.route_car_params_sha256
    assert args.car_params.read_bytes() == args.route_car_params.read_bytes()
    assert digest(repo / 'opendbc_repo/opendbc/car/car.capnp') == args.car_schema_sha256
  with car.CarParams.from_bytes(args.car_params.read_bytes()) as cp:
    expected_cp = cp.to_dict()
    assert cp.carFingerprint == 'HYUNDAI_IONIQ_6' and cp.brand == 'hyundai' and len(cp.safetyConfigs) == 1
    assert not cp.passive and not cp.dashcamOnly and not cp.notCar
    pinned_cp = cp.as_builder()
  output = args.output.resolve()
  assert not output.exists() and not output.is_relative_to(repo)
  output.mkdir(mode=0o700, parents=True)
  prefix = 'replay-bench-' + uuid.uuid4().hex[:12]
  shm = Path('/dev/shm') / ('msgq_' + prefix)
  shm.mkdir(mode=0o700)
  from owned_msgq_cleanup_v2 import journal_created

  journal_created(shm, prefix, output / 'shm-ownership.json')
  params_root = output / 'params'
  params_root.mkdir(mode=0o700)
  env = dict(
    os.environ,
    OPENPILOT_PREFIX=prefix,
    PARAMS_ROOT=str(params_root),
    LOG_ROOT=str(output / 'logs'),
    COMMA_CACHE=str(output / 'cache'),
    SP_PRIVATE_NATIVE_BENCH='1',
    AOL_REPLAY_RUNTIME='0',
    PYTHONDONTWRITEBYTECODE='1',
    PYTHONPATH=os.pathsep.join([str(HERE), *(str(repo / p) for p in ('', 'opendbc_repo', 'msgq_repo', 'rednose_repo', 'tinygrad_repo'))]),
  )
  if env.get('SP_PERF_CAN_VARIANT') in ('baseline', 'candidate', 'feedback', 'pid', 'optimized', 'digest', 'geometry'):
    env['PYTHONPATH'] = str(HERE) + os.pathsep + env['PYTHONPATH']
  if args.steady_state_checkpoint:
    env['SP_STEADY_STATE_CHECKPOINT'] = '1'
  else:
    env.pop('SP_STEADY_STATE_CHECKPOINT', None)
  os.environ.update({key: env[key] for key in ('OPENPILOT_PREFIX', 'PARAMS_ROOT', 'LOG_ROOT', 'COMMA_CACHE')})
  # Source/admission configuration comes from the explicit root-provided live
  # snapshot. It never sets readiness or writes a stale native receipt.
  params = Params()
  forbidden_keys = {
    'CarParams',
    'CarParamsCache',
    'CarParamsPersistent',
    'CarParamsPrevRoute',
    'ControlsReady',
    'FirmwareQueryDone',
    'PandaSignatures',
    'PandaDongleId',
  }
  provided_params = json.loads(args.params_json.read_text())
  for key, value in provided_params.items():
    assert key not in forbidden_keys
    raw = base64.b64decode(value, validate=True)
    target = Path(params.get_param_path(params.check_key(key)))
    assert target.resolve().is_relative_to(params_root)
    temporary = target.with_name(target.name + '.bench-tmp')
    assert not temporary.exists()
    temporary.write_bytes(raw)
    temporary.replace(target)
    assert target.read_bytes() == raw
  assert params.get_bool('OpenpilotEnabledToggle')
  assert not params.get_bool('IsOffroad')
  params.put_bool('ObdMultiplexingEnabled', False, block=True)
  if not args.steady_state_checkpoint:
    put_cache(params, 'CarParamsCache', pinned_cp, block=True)
  assert params.get('CarParams') is None and not params.get_bool('ControlsReady') and not params.get_bool('FirmwareQueryDone')
  planner_configuration = {
    'entrypoint': 'openpilot.selfdrive.controls.plannerd',
    'selectionKey': 'UseStarPilotLongitudinalPlanner',
    'selectionRule': 'Exact raw 0 selects upstream; absent/1 or invalid selects production StarPilot default.',
    'paramsJsonSha256': digest(args.params_json),
    'providedFeatureParamsBase64': {key: provided_params[key] for key in PLANNER_PREF_KEYS if key in provided_params},
    'featureParamsNotProvided': [key for key in PLANNER_PREF_KEYS if key not in provided_params],
    'missingInputMeaning': 'Not supplied in private snapshot; production defaults apply. Does not prove device key absence.',
    'carParams': {
      key: expected_cp[key] for key in ('carFingerprint', 'openpilotLongitudinalControl', 'passive', 'dashcamOnly', 'notCar', 'flags', 'alternativeExperience')
    },
    'nativeInputServices': ['carControl', 'carState', 'controlsState', 'vehicleParameters', 'radarState', 'modelV2', 'selfdriveState'],
    'actualOptionalInputs': ['slcDashboardObservation', 'slcCruiseEvent', 'carState (nonconflated stop/resume)'],
    'unprovidedOptionalInputs': ['slcVisionObservation', 'mapdOut', 'starpilotNavigation', 'slcAction'],
    'optionalInputLimit': 'Missing optional services retain normal production unavailable/refusal behavior; no optional-source qualification.',
    'recordedPlannerPublishersSuppressed': sorted(PLANNER_OUTPUTS),
    'actualRadarFusion': True,
    'recordedRadarPublishersSuppressed': sorted(RADAR_OUTPUTS),
    'publicationLimit': 'Production upstream selection owns only longitudinalPlan/driverAssistance; other reserved output counters may remain zero.',
    'timingEvidence': {
      'onroadEvent81': 'selfdrivedLagging; raw onroadEvents capture',
      'onroadEventsCadence': 'Emits on event-set changes or at 1Hz; presence/removal bounds episodes, not lagged-loop count.',
      'rkMonitorIntervalsExported': True,
      'rkObserverScope': 'Only actual SD/Controls Ratekeeper.monitor_time; no per-phase instrumentation.',
      'plannerFields': ['longitudinalPlan.modelMonoTime', 'longitudinalPlan.processingDelay', 'longitudinalPlan.solverExecutionTime'],
      'limit': 'Service source/receive cadence is not Ratekeeper.monitor_time intervals or avg_dt. Planner has no Ratekeeper.',
    },
  }
  cruise_verification = None
  if cruise_checkpoint is not None:
    spec = importlib.util.spec_from_file_location('private_cruise_checkpoint', helpers / 'checkpoint_card_v2_cruise.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checked = module.verify_cruise_evidence(args, repo, verify_source=True)
    cruise_verification = output / 'cruise-source-verification.json'
    cruise_verification.write_text(
      json.dumps(
        {
          'phase': 'before-owned-actors',
          'sourceMembershipVerified': True,
          'prefix': prefix,
          'paramsRoot': str(params_root),
          'verifiedMonoNs': time.monotonic_ns(),
          'evidence': checked,
        },
        indent=2,
      )
      + '\n'
    )
  messaging.toggle_fake_events(False)
  suppressed = {}
  peers = read_peers(packet / 'peers.trace', messaging, suppressed)
  planner_configuration['recordedPlannerEventsSuppressed'] = suppressed
  peer_services = sorted({message.which() for _, message in peers})
  assert not set(peer_services) & OWNED
  del peers  # Child alone owns peer payloads and publishers after validation.
  peer_pm = messaging.PubMaster(['managerState'])
  proof_names = ['carState', 'selfdriveState', 'onroadEvents', 'aolAxisState', 'aolSafetyWire', 'aolIntentWire', 'carControl', 'pandaStates']
  observed_names = sorted(OWNED | PEERS | {'managerState'})
  observer = messaging.SubMaster(proof_names)
  sockets = {name: messaging.sub_sock(name, conflate=False) for name in observed_names}
  children, logs, records = {}, [], {}
  original_policy = os.sched_getscheduler(0)
  if original_policy != os.SCHED_OTHER:
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
  os.sched_setaffinity(0, {0, 1})
  from placement import apply

  apply('observer')

  def start(name, argv, cwd):
    stream = (output / (name + '.log')).open('xb')
    logs.append(stream)
    if name != 'pandad':
      argv = ['taskset', '-c', '0-7', *argv]
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    children[name] = child
    return child

  failure, cp_verified, ever_ready, active_samples = None, False, False, 0
  counters = dict.fromkeys(observed_names, 0)
  rows = []
  usage_samples = []
  phase_max = {'peerDispatch': 0, 'processSnapshot': 0, 'authorityObserver': 0, 'rawCapture': 0}
  peer_max_feed_late_ns, authority_samples = 0, 0
  capture_path = output / ('observations.bin' if args.observer_format == 'binary' else 'observations.jsonl')
  capture_memory = BoundedCapture(capture_path)  # allocate before owned actor/native timers
  capture_export = None
  capture_cursor = 0
  capture_budget_exhaustions = 0
  placement = {'enabled': False, 'requested': args.private_native_core2}
  checkpoint_hold = None
  try:
    start(
      'peerfeeder',
      [
        sys.executable,
        '-B',
        str(HERE / 'peer_feeder_v7_protected.py'),
        '--repo',
        str(repo),
        '--inputs',
        str(packet),
        '--release',
        str(output / 'release.bin'),
        '--clocks',
        str(output / 'release-clocks.json'),
        '--output',
        str(output / 'peer-feeder.json'),
      ],
      repo,
    )
    peer_ready_deadline = time.monotonic_ns() + 12_000_000_000
    while not (output / 'peer-ready.json').exists():
      assert children['peerfeeder'].poll() is None, 'Peer feeder exited before readiness'
      assert time.monotonic_ns() < peer_ready_deadline, 'Peer feeder not ready within12s'
      time.sleep(0.005)
    peer_ready = json.loads((output / 'peer-ready.json').read_text())
    assert peer_ready['services'] == peer_services
    gated_daemons = cruise_checkpoint is not None
    daemon_gate = output / 'normal-main-ready.json'
    if gated_daemons:
      assert not daemon_gate.exists()
      env.update(SP_DAEMON_MAIN_GATE=str(daemon_gate), SP_DAEMON_MAIN_GATE_CP_SHA256=args.car_params_sha256)
      # Cold imports overlap actual Card startup. The unchanged normal mains
      # remain behind the parent CP-verification token below.
      for name, module, relative in (
        ('selfdrived', 'openpilot.selfdrive.selfdrived.selfdrived', 'openpilot/selfdrive/selfdrived/selfdrived.py'),
        ('controlsd', 'openpilot.selfdrive.controls.controlsd', 'openpilot/selfdrive/controls/controlsd.py'),
      ):
        start(
          name,
          [
            sys.executable,
            '-B',
            str(HERE / 'normal_daemon_entry_v1.py'),
            '--module',
            module,
            '--module-sha256',
            source_pins['sources'][relative],
            '--gate',
            str(daemon_gate),
            '--output',
            str(output / (name + '-timing.json')),
            '--limit',
            str(args.timing_limit),
          ],
          repo,
        )
    native_started_ns = time.monotonic_ns()
    start(
      'pandad',
      [
        str(args.native_endpoint.resolve()),
        str(args.native_library.resolve()),
        str(packet / 'can.trace'),
        str(output / 'release.bin'),
        str(output / 'native-receipt.json'),
      ],
      repo / 'openpilot/selfdrive/pandad',
    )
    if args.steady_state_checkpoint:
      checkpoint_entry = 'checkpoint_card_v2_cruise.py' if cruise_checkpoint is not None else 'checkpoint_card.py'
      checkpoint_launch = helpers / checkpoint_entry
      card_command = [
        sys.executable,
        '-B',
        str(checkpoint_launch),
        '--repo',
        str(repo),
        '--car-params',
        str(args.car_params.resolve()),
        '--car-params-sha256',
        args.car_params_sha256,
        '--route-car-params',
        str(args.route_car_params.resolve()),
        '--route-car-params-sha256',
        args.route_car_params_sha256,
        '--car-schema-sha256',
        args.car_schema_sha256,
        '--receipt',
        str(output / 'checkpoint.json'),
      ]
      if cruise_checkpoint is not None:
        card_command += [
          '--cruise-checkpoint',
          str(args.cruise_checkpoint.resolve()),
          '--cruise-checkpoint-sha256',
          args.cruise_checkpoint_sha256,
          '--cruise-verification',
          str(cruise_verification),
          '--cruise-verification-sha256',
          digest(cruise_verification),
        ]
    else:
      card_command = [sys.executable, '-B', '-m', 'openpilot.selfdrive.car.card']
    start('card', card_command, repo)
    if args.private_native_core2:
      assert not (output / 'release.bin').exists()
      placement = private_native_core2(children['pandad'], args.native_endpoint)
      (output / 'private-native-placement.json').write_text(json.dumps(placement, indent=2) + '\n')
    if cruise_checkpoint is not None:
      # The compiled endpoint's unchanged release wait is 15s. Bound the entire
      # pre-release phase from native spawn to 12s, including scheduler setup.
      hold_deadline = native_started_ns + 12_000_000_000
      while True:
        assert all(children[name].poll() is None for name in ('pandad', 'card')), 'Child exited before checkpoint release'
        assert time.monotonic_ns() < hold_deadline, 'Checkpoint not ready within12s; native15s guard retained'
        if (output / 'checkpoint.json').exists() and params.get('CarParams') == args.car_params.read_bytes():
          break
        time.sleep(0.005)
      checkpoint_hold = {
        'nativeStartedMonoNs': native_started_ns,
        'checkpointReadyMonoNs': time.monotonic_ns(),
        'boundNs': 12_000_000_000,
        'nativeReleaseGuardNs': 15_000_000_000,
        'sourceVerificationSha256': digest(cruise_verification),
      }
    reserve = json.loads(os.environ.get('HEATDEATH_RESERVE', 'null'))
    if reserve is not None:
      start(
        'reserve',
        [
          sys.executable,
          '-B',
          str(HERE / 'reserve.py'),
          '--core',
          str(reserve['core']),
          '--cpu-ms',
          str(reserve['cpuMs']),
          '--period-ms',
          str(reserve['periodMs']),
          '--duration-ns',
          str(meta['durationNs']),
          '--release',
          str(output / 'release.bin'),
          '--output',
          str(output / 'reserve.json'),
        ],
        repo,
      )
    mono_start = time.monotonic_ns() + 300_000_000
    boot_start = time.clock_gettime_ns(time.CLOCK_BOOTTIME) + (mono_start - time.monotonic_ns())
    (output / 'release-clocks.json').write_text(json.dumps({'prefix': prefix, 'monoStartNs': mono_start, 'bootStartNs': boot_start}, sort_keys=True) + '\n')
    release_temporary = output / 'release.tmp'
    release_temporary.write_bytes(struct.pack('<Q', mono_start))
    release_temporary.replace(output / 'release.bin')
    index, manager_due, proof_due = 0, 0, 0
    proof_interval_ns = round(1e9 / args.authority_observer_hz)
    deadline = mono_start + meta['durationNs']
    last_poll = mono_start
    with capture_memory as capture:
      if args.observer_format == 'binary':
        capture.write(BINARY_OBSERVER_MAGIC)
      while time.monotonic_ns() < deadline:
        now = time.monotonic_ns()
        if now < mono_start:
          time.sleep(min((mono_start - now) / 1e9, 0.005))
          continue
        for name, child in children.items():
          if child.poll() is not None:
            if name in ('pandad', 'peerfeeder', 'reserve') and child.returncode == 0 and now >= deadline - 200_000_000:
              break
            raise RuntimeError(f'{name} exited early with {child.returncode}')
        if children['pandad'].poll() is not None:
          break
        checkpoint_complete = not args.steady_state_checkpoint or (output / 'checkpoint.json').exists()
        if not cp_verified and checkpoint_complete and (raw := params.get('CarParams')):
          with car.CarParams.from_bytes(raw) as actual:
            assert actual.to_dict() == expected_cp, 'Actual Card CP differs from live pin; refusing mixed configurations'
          if args.steady_state_checkpoint:
            assert raw == args.car_params.read_bytes()
            checkpoint = json.loads((output / 'checkpoint.json').read_text())
            assert checkpoint['startupExcluded'] is True and checkpoint['cpSha256'] == args.car_params_sha256
            assert checkpoint['routeCpSha256'] == args.route_car_params_sha256
            assert checkpoint['carSchemaSha256'] == args.car_schema_sha256
            assert checkpoint['paramsRoot'] == str(params_root) and checkpoint['prefix'] == prefix
            assert checkpoint['restoredFieldsAfter'] == {
              'ci_initialized': True,
              'ioniq6_long_selected': True,
              'ioniq6_long_prearmed': True,
              'ioniq6_long_pending': False,
            }
            assert checkpoint['restoredPrivateParamsAfter'] == {'FirmwareQueryDone': True, 'ControlsReady': True}
            if cruise_checkpoint is not None:
              restored = checkpoint['cruiseMemory']
              assert restored['checkpointSha256'] == args.cruise_checkpoint_sha256
              assert restored['sourceStartMonoNs'] == meta['sourceStartMonoNs']
              assert restored['priorSetSpeedInitializationExcluded'] is True
              assert restored['restoredFieldsAfter'] == cruise_checkpoint['values']
          cp_verified = True
          if gated_daemons:
            token = {
              'kind': 'private-verified-car-params-main-gate-v1',
              'verifiedMonoNs': time.monotonic_ns(),
              'prefix': prefix,
              'paramsRoot': str(params_root),
              'carParamsSha256': args.car_params_sha256,
            }
            temporary_gate = daemon_gate.with_suffix('.tmp')
            assert not temporary_gate.exists() and not daemon_gate.exists()
            temporary_gate.write_text(json.dumps(token, sort_keys=True) + '\n')
            temporary_gate.replace(daemon_gate)
          start('radard', [sys.executable, '-B', str(HERE / 'actor.py'), 'radard'], repo)
          start('steerlearnerd', [sys.executable, '-B', '-m', 'openpilot.starpilot.flm.steerlearnerd'], repo)
          start('plannerd', [sys.executable, '-B', str(HERE / 'actor.py'), 'plannerd'], repo)
        if now >= manager_due:
          phase_start = time.monotonic_ns()
          manager = messaging.new_message('managerState', valid=True)
          manager.managerState.processes = [
            {'name': name, 'pid': child.pid, 'running': child.poll() is None, 'shouldBeRunning': True} for name, child in children.items()
          ]
          peer_pm.send('managerState', manager)
          manager_due = now + 500_000_000
          for name, child in children.items():
            records[name] = child_record(child.pid)
          usage_samples.append(
            {
              'monoNs': time.monotonic_ns(),
              'processes': {name: process_usage(pid) for name, pid in [('collector', os.getpid()), *((name, child.pid) for name, child in children.items())]},
            }
          )
          phase_max['processSnapshot'] = max(phase_max['processSnapshot'], time.monotonic_ns() - phase_start)
        ready = False
        if time.monotonic_ns() >= proof_due:
          phase_start = time.monotonic_ns()
          observer.update(0)
          now = time.monotonic_ns()
          axis = current_axis(observer, now_ns=now)
          native = current_native(observer, pinned_cp, now_ns=now, axis_session_id=str(axis.sessionId) if axis is not None else None)
          ready = bool(cp_verified and axis is not None and native is not None)
          ever_ready |= ready
          active_samples += bool(
            ready
            and observer.valid['carControl']
            and observer['carControl'].latActive
            and axis.lateralActive
            and axis.nativeAcknowledged
            and native.lateralAllowed
          )
          authority_samples += 1
          proof_due = now + proof_interval_ns
          phase_max['authorityObserver'] = max(phase_max['authorityObserver'], time.monotonic_ns() - phase_start)
        phase_start = time.monotonic_ns()
        socket_items = list(sockets.items())
        no_data = 0
        captured = 0
        while captured < 64 and time.monotonic_ns() - phase_start < 500_000:
          service_index = capture_cursor
          name, socket = socket_items[service_index]
          capture_cursor = (capture_cursor + 1) % len(socket_items)
          raw = socket.receive(non_blocking=True)
          if raw is None:
            no_data += 1
            if no_data >= len(socket_items):
              break
            continue
          no_data = 0
          received = time.monotonic_ns()
          counters[name] += 1
          captured += 1
          assert 0 < len(raw) <= 8_000_000
          capture.write(BINARY_OBSERVER_HEADER.pack(received, service_index, len(raw)))
          capture.write(raw)
        if captured == 64 or time.monotonic_ns() - phase_start >= 500_000:
          capture_budget_exhaustions += 1
        phase_max['rawCapture'] = max(phase_max['rawCapture'], time.monotonic_ns() - phase_start)
        now = time.monotonic_ns()
        assert len(rows) < 180000, 'Bounded collector poll records full'
        rows.append((now, now - last_poll, ready))
        last_poll = now
        time.sleep(0.001)
  except BaseException as error:
    failure = f'{type(error).__name__}: {error}'
  finally:
    pre_cleanup_exit_codes = {name: child.poll() for name, child in children.items()}
    near_source_end = 'deadline' in locals() and time.monotonic_ns() >= deadline - 200_000_000
    unexpected = {
      name: code
      for name, code in pre_cleanup_exit_codes.items()
      if code is not None and not (name in ('pandad', 'peerfeeder', 'reserve') and code == 0 and near_source_end)
    }
    if unexpected:
      failure = failure or 'Production child exited before cleanup: ' + repr(unexpected)
    # Let a normally completed source feeder export its bounded receipt before
    # sending cleanup TERM. A failed/early source run still takes normal cleanup.
    if 'deadline' in locals() and time.monotonic_ns() >= deadline and 'peerfeeder' in children:
      try:
        children['peerfeeder'].wait(timeout=1)
      except subprocess.TimeoutExpired:
        pass
    for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
      signal.signal(number, signal.SIG_IGN)
    for child in children.values():
      if child.poll() is None:
        try:
          os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
          pass
    for child in children.values():
      try:
        child.wait(timeout=3)
      except subprocess.TimeoutExpired:
        try:
          os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
          pass
        child.wait(timeout=3)
    for stream in logs:
      stream.close()
    # Producers are reaped before any raw observation file write.
    if (output / 'peer-feeder.json').exists():
      feeder_receipt = json.loads((output / 'peer-feeder.json').read_text())
      if feeder_receipt['failure'] is not None or feeder_receipt['published'] != feeder_receipt['events']:
        failure = failure or 'Independent peer feeder did not publish exact complete source window'
      peer_max_feed_late_ns = feeder_receipt['maximumLatenessNs']
    else:
      failure = failure or 'Missing independent peer feeder receipt'
    capture_export = capture_memory.export()
    if capture_path.exists() and args.observer_format == 'binary':
      (output / 'observations-format.json').write_text(
        json.dumps(
          {
            'format': 'SPBIOB01',
            'header': '<QHI: receiveMonoNs,serviceIndex,rawEventLength',
            'services': observed_names,
            'messages': counters,
            'sha256': digest(capture_path),
            'sourceTimestampsPreserved': True,
            'liveJsonSerialization': False,
          },
          indent=2,
        )
        + '\n'
      )
    (output / 'process-usage.json').write_text(
      json.dumps(
        {'clockTicksPerSecond': os.sysconf('SC_CLK_TCK'), 'schedstatScope': 'main thread; user/system ticks are process totals', 'samples': usage_samples},
        indent=2,
      )
      + '\n'
    )
    result = {
      'kind': 'private-native-ipc-bench',
      'failure': failure,
      'sourceDurationNs': meta['durationNs'],
      'cpVerified': cp_verified,
      'mode': 'steady-state-checkpoint' if args.steady_state_checkpoint else 'normal-startup',
      'startupExcluded': args.steady_state_checkpoint,
      'checkpointReceipt': str(output / 'checkpoint.json') if args.steady_state_checkpoint else None,
      'everAuthorityReady': ever_ready,
      'activeObserverSamples': active_samples,
      'qualifiedActiveLatencyBench': False,
      'qualificationNote': (
        'Inspect sustained actual active output, native RX/TX proof, service cadence, source ages and scheduler records. No automatic acceptance label.'
      ),
      'prefix': prefix,
      'children': records,
      'exitCodes': {name: child.returncode for name, child in children.items()},
      'preCleanupExitCodes': pre_cleanup_exit_codes,
      'messages': counters,
      'collectorPollCount': len(rows),
      'collectorMaxPollIntervalNs': max((row[1] for row in rows), default=None),
      'harnessVersion': 'current-production-real-radard-v2-prewarmed-peer',
      'observerFormat': args.observer_format,
      'phaseTiming': {
        'rowsPerPhaseMaximum': args.timing_limit,
        'writesOnlyAtShutdown': True,
        'observer': 'Ratekeeper.monitor_time only; normal mains/constructor/worker scheduling retained',
        'sources': {
          name: digest(HERE / name) for name in ('phase_timing.py', 'normal_daemon_entry_v1.py', 'bounded_capture_v1.py', 'peer_feeder_v7_protected.py')
        },
        'qualification': 'Fresh enabled source, collecting evidence, admitted profile and application remain distinct',
      },
      'placement': json.loads(os.environ.get('HEATDEATH_PLACEMENT', '{}')),
      'syntheticReserve': json.loads(os.environ.get('HEATDEATH_RESERVE', 'null')),
      'sourcePins': source_pins,
      'normalProductionDaemons': True,
      'boundedCapture': capture_export,
      'captureBudgetExhaustions': capture_budget_exhaustions,
      'captureBudgetNs': 500000,
      'captureMessagesPerPollMaximum': 64,
      'peerFeedMaximumAdmittedLatenessNs': 20000000,
      'independentPeerFeeder': json.loads((output / 'peer-feeder.json').read_text()) if (output / 'peer-feeder.json').exists() else None,
      'plannerConfiguration': planner_configuration,
      'cruiseCheckpointSha256': args.cruise_checkpoint_sha256,
      'cruiseCheckpointPreRelease': checkpoint_hold,
      'privateNativePlacement': placement,
      'authorityObserverHz': args.authority_observer_hz,
      'authorityObserverSamples': authority_samples,
      'collectorPhaseMaxNs': phase_max,
      'peerMaxFeedLateNs': peer_max_feed_late_ns,
      'capturePath': str(capture_path),
      'captureSha256': digest(capture_path) if capture_path.exists() else None,
      'inputSha256': digest(packet / 'inputs.json'),
      'cpSha256': digest(args.car_params),
      'endpointSha256': digest(args.native_endpoint),
      'nativeLibrarySha256': digest(args.native_library),
      'limits': [
        'No physical CAN I/O; virtual native backend.',
        'Card startup is unmodified and may refuse absent adaptive ECU responses.',
        'Recorded peers and generated managerState are declared input stimuli.',
        'Native safety test library excludes firmware heartbeat timeout-loop equivalence.',
        'Bounded RAM raw capture,25Hz proof polling and private pandad consume hardware CPU; include their PIDs in analysis.',
        'Authority observer sample counts depend on the explicitly reported observer frequency.',
        'Optional private-native-core2 changes only virtual endpoint placement and is a benchmark limitation.',
      ],
    }
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  if failure:
    raise SystemExit(failure)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  prep = commands.add_parser('prepare')
  prep.add_argument('--repo', required=True)
  prep.add_argument('--rlog', action='append', type=Path, required=True)
  prep.add_argument('--start-mono-ns', type=int, required=True)
  prep.add_argument('--duration', type=float, required=True)
  prep.add_argument('--source-boot-minus-mono-ns', type=int, required=True)
  prep.add_argument('--device-motion-clock', choices=('monotonic', 'boottime'), required=True)
  prep.add_argument('--output', type=Path, required=True)
  execute = commands.add_parser('run')
  execute.add_argument('--repo', required=True)
  execute.add_argument('--inputs', type=Path, required=True)
  execute.add_argument('--helpers-dir', type=Path, help='Directory containing the unchanged v6 Card checkpoint helpers')
  execute.add_argument('--timing-limit', type=int, default=12000)
  execute.add_argument('--car-params', type=Path, required=True)
  execute.add_argument('--car-params-sha256', required=True)
  execute.add_argument('--steady-state-checkpoint', action='store_true')
  execute.add_argument('--cruise-checkpoint', type=Path, help='Explicit recorded remembered-speed context; no engagement or authority is restored')
  execute.add_argument('--cruise-checkpoint-sha256')
  execute.add_argument('--route-car-params', type=Path)
  execute.add_argument('--route-car-params-sha256')
  execute.add_argument('--car-schema-sha256')
  execute.add_argument('--params-json', type=Path, required=True)
  execute.add_argument('--native-endpoint', type=Path, required=True)
  execute.add_argument('--native-endpoint-sha256', required=True)
  execute.add_argument('--native-library', type=Path, required=True)
  execute.add_argument('--native-library-sha256', required=True)
  execute.add_argument('--root-grant', required=True)
  execute.add_argument('--output', type=Path, required=True)
  execute.add_argument('--observer-format', choices=('binary', 'json'), default='binary')
  execute.add_argument('--authority-observer-hz', type=int, choices=(25,), default=25)
  execute.add_argument(
    '--private-native-core2',
    action='store_true',
    help='Explicit private bench only: move owned virtual native threads after observing actual FIFO54/core3 setup',
  )
  decode = commands.add_parser('decode-observations')
  decode.add_argument('--repo', required=True)
  decode.add_argument('--capture', type=Path, required=True)
  decode.add_argument('--metadata', type=Path, required=True)
  decode.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  {'prepare': prepare, 'run': run, 'decode-observations': decode_observations}[args.command](args)


if __name__ == '__main__':
  main()
