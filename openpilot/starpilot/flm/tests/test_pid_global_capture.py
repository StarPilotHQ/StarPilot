"""Actual registered Controls PID collection; no learned-profile admission claim."""
import pytest
from opendbc.car import structs, gen_empty_fingerprint
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.gm.values import CAR as GM, CanBus
from opendbc.car.gm.radar_interface import RADAR_HEADER_MSG
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.starpilot.flm.pid_stages import PidObservation, REVISION, SUPPORTED, UNSUPPORTED
from openpilot.starpilot.flm.tests.test_registered_controls_global import controls_for, frame


@pytest.mark.parametrize('platform', (HONDA.HONDA_CIVIC, HONDA.HONDA_CIVIC_BOSCH,
  HONDA.HONDA_PILOT_4G, HONDA.ACURA_TLX_2G_MMR, GM.CADILLAC_ESCALADE_ESV))
def test_actual_controls_pid_collects_own_trace_without_changing_disabled_baseline(platform):
  if platform==GM.CADILLAC_ESCALADE_ESV:
    fingerprint=gen_empty_fingerprint()
    fingerprint[CanBus.OBSTACLE][RADAR_HEADER_MSG]=8
    cp=interfaces[platform].get_params(platform,fingerprint,[],False,False,False)
    assert not cp.radarUnavailable and not cp.dashcamOnly
  else:
    cp=interfaces[platform].get_non_essential_params(platform)
  assert cp.steerControlType==structs.CarParams.SteerControlType.torque
  assert cp.lateralTuning.which()=='pid'
  with controls_for(cp,enabled=False) as baseline, controls_for(cp,enabled=True) as candidate:
    assert type(candidate.LaC) is LatControlPID
    runtime=candidate.global_learning
    assert runtime.descriptor is not None and runtime.descriptor.domain=='torque_pid'
    assert type(runtime.observation) is PidObservation
    assert len(SUPPORTED)==26 and len(UNSUPPORTED)==4
    original=candidate.CP.as_builder().to_bytes()
    emitted_snapshots=[]
    for tick in range(35):
      now=400_000_000_000+tick*10_000_000
      expected,_=frame(baseline,now,tick)
      actual,_=frame(candidate,now,tick)
      if runtime.snapshot:
        emitted_snapshots.append(runtime.snapshot)
      assert actual.to_dict()==expected.to_dict()
      assert candidate.LaC.pid.i==baseline.LaC.pid.i
      assert candidate.LaC.ff_factor==baseline.LaC.ff_factor
      assert candidate.CP.as_builder().to_bytes()==original
    evidence=runtime.capture_evidence(control_ns=now,state_ns=now,active=True,limited=False)
    assert evidence is not None
    assert evidence.stage_schema_revision==REVISION
    stage=dict(evidence.stage_values)
    assert 'friction' not in stage and 'effective_factor' not in stage
    assert 'angle_error_deg' in stage and 'baseline_ff' in stage
    assert all(name in stage for name in ('learned_error_deg','deadband_deg','deadband_normalizer_deg'))
    assert evidence.domain=='normalized_torque'
    assert evidence.effect_trace is None
    # Check the actual emitted checkpoint, not an arbitrary inter-checkpoint
    # frame: the production wire publishes frozen state periodically.
    assert emitted_snapshots and runtime.snapshot_sequence>0
    assert all(any(name=='controller.ff_factor' for name,_ in snapshot) for snapshot in emitted_snapshots)
    assert all(not any(name.startswith('tune.') for name,_ in snapshot) for snapshot in emitted_snapshots)
    assert runtime.replay_inputs
