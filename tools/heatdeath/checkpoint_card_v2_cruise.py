#!/usr/bin/env python3
"""Explicit private Ioniq6 checkpoint including pinned remembered cruise speed.

No daemon method, clock, socket, native admission, or grant is replaced. This
restores only the declared post-handoff phase. Fresh parser/panda/session/lease
checks run normally and any subsequent authority loss remains sticky.
"""

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


def sha(path):
  return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_cruise_evidence(args, repo, *, verify_source):
  from openpilot.cereal import messaging
  from openpilot.selfdrive.car.cruise import V_CRUISE_MAX

  assert sha(args.cruise_checkpoint) == args.cruise_checkpoint_sha256
  evidence = json.loads(args.cruise_checkpoint.read_text())
  assert evidence['kind'] == 'private-recorded-cruise-speed-checkpoint' and evidence['startupExcluded'] is True
  assert evidence['cpSha256'] == args.car_params_sha256
  assert evidence['carSchemaSha256'] == args.car_schema_sha256
  head = subprocess.check_output(['git', '-c', 'safe.directory=' + str(repo), '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
  assert evidence['sourceHead'] == head
  source = Path(evidence['sourceRlog'])
  assert source.is_absolute() and source.is_file() and sha(source) == evidence['sourceRlogSha256']
  start = evidence['sourceStartMonoNs']
  assert type(start) is int and start > 0 and len(evidence['samples']) == 2
  values = evidence['values']
  fields = {'v_cruise_kph', 'v_cruise_cluster_kph', 'v_cruise_kph_last'}
  assert set(values) == fields
  assert all(type(value) in (int, float) and math.isfinite(value) and 0 < value <= V_CRUISE_MAX for value in values.values())
  raw_samples, times = [], []
  for sample in evidence['samples']:
    raw = base64.b64decode(sample['rawEventBase64'], validate=True)
    event = messaging.log_from_bytes(raw)
    assert event.which() == 'carState' and event.valid
    cs, stamp = event.carState, int(event.logMonoTime)
    assert stamp == sample['monoNs'] and 0 < start - stamp <= 30_000_000
    assert cs.canValid and not cs.canTimeout and cs.cruiseState.available and not cs.cruiseState.enabled
    assert not len(cs.buttonEvents) and not cs.buttonEnable
    assert sample['canValid'] is True and sample['canTimeout'] is False
    assert sample['cruiseAvailable'] is True and sample['cruiseEnabled'] is False and sample['buttonCount'] == 0
    assert float(cs.vCruise) == sample['vCruiseKph'] == values['v_cruise_kph'] == values['v_cruise_kph_last']
    assert float(cs.vCruiseCluster) == sample['vCruiseClusterKph'] == values['v_cruise_cluster_kph']
    raw_samples.append(raw)
    times.append(stamp)
  assert 0 < times[1] - times[0] <= 30_000_000
  # Independently prove these are the last two actual source samples before
  # this suffix, rather than trusting scalar claims beside an unrelated rlog.
  if verify_source:
    from local_rlog import local_events

    recent, boundary_seen = [], False
    with local_events(source) as events:
      for event in events:
        if event.which() == 'carState':
          if int(event.logMonoTime) >= start:
            boundary_seen = True
            break
          recent = (recent + [event.as_builder().to_bytes()])[-2:]
    assert boundary_seen, 'Source ended before the cruise checkpoint'
    assert recent == raw_samples, 'Cruise memory does not match the last two source carState events'
  return {
    'checkpointSha256': args.cruise_checkpoint_sha256,
    'sourceStartMonoNs': start,
    'sourceRlog': str(source),
    'sourceRlogSha256': evidence['sourceRlogSha256'],
    'cpSha256': evidence['cpSha256'],
    'carSchemaSha256': evidence['carSchemaSha256'],
    'sourceHead': head,
    'sampleMonoNs': times,
    'rawSampleSha256': [hashlib.sha256(raw).hexdigest() for raw in raw_samples],
    'values': values,
  }


def cruise_memory(args, repo, helper):
  from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET

  checked = verify_cruise_evidence(args, repo, verify_source=False)
  verification = args.cruise_verification.resolve()
  assert verification.parent == args.receipt.resolve().parent
  assert sha(verification) == args.cruise_verification_sha256
  proof = json.loads(verification.read_text())
  assert proof['phase'] == 'before-owned-actors' and proof['sourceMembershipVerified'] is True
  assert proof['prefix'] == os.environ['OPENPILOT_PREFIX'] and proof['paramsRoot'] == os.environ['PARAMS_ROOT']
  assert proof['evidence'] == checked and 0 < proof['verifiedMonoNs'] <= time.monotonic_ns()
  values = checked['values']
  fields = set(values)
  before = {name: getattr(helper, name) for name in fields}
  assert before == {'v_cruise_kph': V_CRUISE_UNSET, 'v_cruise_cluster_kph': V_CRUISE_UNSET, 'v_cruise_kph_last': 0}
  untouched = {name: value for name, value in vars(helper).items() if name not in fields}
  for name, value in values.items():
    setattr(helper, name, float(value))
  assert untouched == {name: value for name, value in vars(helper).items() if name not in fields}
  return {
    **checked,
    'priorSetSpeedInitializationExcluded': True,
    'sourceVerificationSha256': args.cruise_verification_sha256,
    'restoredFieldsBefore': before,
    'restoredFieldsAfter': {name: getattr(helper, name) for name in fields},
    'otherCruiseFieldsUnchanged': True,
    'restoredAuthority': False,
  }


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--repo', type=Path, required=True)
  parser.add_argument('--car-params', type=Path, required=True)
  parser.add_argument('--car-params-sha256', required=True)
  parser.add_argument('--route-car-params', type=Path, required=True)
  parser.add_argument('--route-car-params-sha256', required=True)
  parser.add_argument('--car-schema-sha256', required=True)
  parser.add_argument('--cruise-checkpoint', type=Path, required=True)
  parser.add_argument('--cruise-checkpoint-sha256', required=True)
  parser.add_argument('--cruise-verification', type=Path, required=True)
  parser.add_argument('--cruise-verification-sha256', required=True)
  parser.add_argument('--receipt', type=Path, required=True)
  args = parser.parse_args()
  repo = args.repo.resolve()
  assert sys.dont_write_bytecode and sys.platform == 'linux'
  assert os.environ.get('SP_PRIVATE_NATIVE_BENCH') == '1'
  assert os.environ.get('SP_STEADY_STATE_CHECKPOINT') == '1'
  assert os.environ.get('OPENPILOT_PREFIX', '').startswith('replay-bench-')
  assert Path(os.environ['PARAMS_ROOT']).resolve() == args.receipt.resolve().parent / 'params'
  assert not args.receipt.exists() and not args.receipt.resolve().is_relative_to(repo)
  assert all(name not in os.environ for name in ('REPLAY', 'SIMULATION', 'CEREAL_FAKE', 'FAKESEND', 'STARTED'))
  assert sha(args.car_params) == args.car_params_sha256
  assert sha(args.route_car_params) == args.route_car_params_sha256
  assert sha(repo / 'opendbc_repo/opendbc/car/car.capnp') == args.car_schema_sha256
  pinned = args.car_params.read_bytes()
  assert pinned == args.route_car_params.read_bytes(), 'Live and recorded final CP must be byte-identical under current schema'
  sys.path[:0] = [str(repo / path) for path in ('', 'opendbc_repo', 'msgq_repo', 'rednose_repo', 'tinygrad_repo')]
  from opendbc.car.structs import car
  from opendbc.car.car_helpers import interfaces
  from openpilot.common.params import Params
  from openpilot.common.realtime import Priority, config_realtime_process
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.longitudinal.ioniq6_start import eligible

  config_realtime_process(4, Priority.CTRL_HIGH)
  from placement import apply

  apply('card')
  with car.CarParams.from_bytes(pinned) as source:
    cp = source.as_builder()
  assert eligible(cp), 'Only exact current Ioniq6 active-LONG CP checkpoint is supported'
  interface = interfaces[cp.carFingerprint]
  selected = Car(CI=interface(cp), RI=interface.RadarInterface(cp))
  try:
    assert selected.CP.to_bytes() == pinned, 'Card finalized CP bytes differ from verified live/route checkpoint'
    params = Params()
    assert params.get('CarParams') == pinned
    assert selected.vehicle_startup.owner is None
    assert not selected.ioniq6_host_warmed and not selected.ioniq6_panda_armed
    assert not selected.ioniq6_long_lost
    assert selected.ioniq6_keepalive is None and selected.ioniq6_restore_callbacks is None
    restored_fields = {'ci_initialized': True, 'ioniq6_long_selected': True, 'ioniq6_long_prearmed': True, 'ioniq6_long_pending': False}
    before = {name: getattr(selected, name) for name in restored_fields}
    # Explicit root-authorized phase checkpoint only. No accepted command,
    # driver latch, parser sample, native status, proof, clock or session is copied.
    for name, value in restored_fields.items():
      setattr(selected, name, value)
    ready_before = {name: params.get_bool(name) for name in ('FirmwareQueryDone', 'ControlsReady')}
    for name in ready_before:
      params.put_bool(name, True, block=True)
    memory = cruise_memory(args, repo, selected.v_cruise_helper)
    receipt = {
      'kind': 'private-already-running-card-checkpoint',
      'startupExcluded': True,
      'excluded': [
        'vehicle discovery',
        'ECU handoff/ownership transaction',
        'CI.init',
        'physical native firmware startup',
        'prior set-speed initialization',
        'road qualification',
      ],
      'restoredFieldsBefore': before,
      'restoredFieldsAfter': restored_fields,
      'restoredPrivateParamsBefore': ready_before,
      'restoredPrivateParamsAfter': {'FirmwareQueryDone': True, 'ControlsReady': True},
      'cruiseMemory': memory,
      'freshGatesRetained': {
        'ioniq6_host_warmed': selected.ioniq6_host_warmed,
        'ioniq6_panda_armed': selected.ioniq6_panda_armed,
        'ioniq6_long_lost': selected.ioniq6_long_lost,
      },
      'noRestoredAuthority': [
        'intent latch',
        'native grant',
        'panda inventory',
        'RX health',
        'session',
        'lease',
        'source timestamp',
        'axis proof',
        'active CarControl',
      ],
      'cpSha256': sha(args.car_params),
      'routeCpSha256': sha(args.route_car_params),
      'carSchemaSha256': args.car_schema_sha256,
      'cardSourceSha256': sha(repo / 'openpilot/selfdrive/car/card.py'),
      'pid': os.getpid(),
      'startedMonoNs': time.monotonic_ns(),
      'paramsRoot': str(Path(os.environ['PARAMS_ROOT']).resolve()),
      'prefix': os.environ['OPENPILOT_PREFIX'],
      'normalScheduler': {'policy': os.sched_getscheduler(0), 'priority': os.sched_getparam(0).sched_priority, 'affinity': sorted(os.sched_getaffinity(0))},
    }
    temporary_receipt = args.receipt.with_suffix('.tmp')
    assert not temporary_receipt.exists()
    temporary_receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    temporary_receipt.replace(args.receipt)
    from phase_timing import PhaseCapture

    capture = PhaseCapture(args.receipt.parent / 'card-timing.json', 16000, 'private-current-card-phases')
    for name in ('step', 'state_update', 'controls_update', 'state_publish'):
      capture.add(Car, name, 'Car.' + name, cycle=name == 'step')
    if os.environ.get('SP_PRIVATE_CARD_MACRO') in ('1', '2'):
      from opendbc.can.parser import CANParser
      from opendbc.car import steering_projection
      from openpilot.starpilot.flm import feedback_observation, physical_input_observation

      capture.add(CANParser, 'update', 'CANParser.update')
      capture.add(feedback_observation.CardFeedbackCapture, 'publication', 'Feedback.publication')
      capture.add(feedback_observation, 'applied_digest', 'Feedback.applied_digest')
      capture.add(physical_input_observation, 'body_digest', 'Physical.body_digest')
      capture.add(steering_projection, 'projection_to_dict', 'Projection.to_dict')
      if os.environ.get('SP_PRIVATE_CARD_MACRO') == '2':
        from openpilot.selfdrive.car import card as card_module
        from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback

        capture.add(type(selected.CI), 'update', 'CI.update')
        capture.add(card_module, 'can_capnp_to_list', 'CAN.deserialize')
        capture.add(TransportPauseFeedback, 'observe', 'Transport.observe')
        for name in (
          'radar_update',
          '_feedback_capture',
          'observe_ioniq6_long_authority',
          'update_vehicle_state_context',
          'refresh_slc_configuration',
          'update_cruise_speed',
        ):
          capture.add(Car, name, 'Car.' + name)
    with capture:
      if os.environ.get('SP_PRIVATE_CARD_PROFILE') == '1':
        import cProfile

        profile = cProfile.Profile()
        profile.enable()
        try:
          selected.card_thread()
        finally:
          profile.disable()
          profile.dump_stats(str(args.receipt.parent / 'card.pstats'))
      else:
        selected.card_thread()
  finally:
    selected.vehicle_startup.close()
    # There is no ECU/keepalive owner to restore in this virtual checkpoint.
    assert selected.ioniq6_keepalive is None and selected.ioniq6_restore_callbacks is None


if __name__ == '__main__':
  main()
