"""Actual Controls -> GlobalRuntime -> LaC at100Hz with real cereal deliveries.

Response certificates below are explicit unit fixtures, not physical model
qualification. Admission, native permission, source leases, controller hooks,
application receipts and steering encoding execute their production paths.
"""

from contextlib import contextmanager
from dataclasses import replace
import math
from unittest import mock

import pytest

from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.values import CAR
from openpilot.cereal import messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.starpilot.flm.canonical_surface_adapter import full_profile_sha256
from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding
from openpilot.starpilot.flm.live_trial_owner import LiveTrialApplication
from openpilot.starpilot.flm.live_trial_wire import write_trial, write_tracking_source
from openpilot.starpilot.flm.runtime_adapter_registry import read_source
from openpilot.starpilot.flm.tests.test_live_trial_owner import offer, response
from openpilot.starpilot.flm.tests.test_registered_controls_global import controls_for, feed, source_packet


LIVE = 'starpilotSteeringTracking'
LEGACY = 'autoSteeringLearning'
BASE = 800_000_000_000
DT = 10_000_000
GENERATION = 'a' * 32
FED = frozenset(('carState', 'selfdriveState', 'deviceState', 'vehicleParameters', 'longitudinalPlan',
                 'lateralDelay', 'onroadEvents', 'modelV2', 'driverMonitoringState', 'extrinsicsCalibration',
                 LEGACY, LIVE, 'pandaStates', 'aolAxisState', 'aolSafetyWire'))


class LiveControls:
  def __init__(self, controls):
    self.controls = controls
    self.tick = -1
    self.publications = 0
    self.latest = None
    self.last_command = None
    self.extra_last = {}

  @property
  def runtime(self):
    return self.controls.global_learning

  def source(self, now):
    return read_source(self.controls.CP, self.controls.params, now_ns=now,
                       calibration=(0.0, 0.0, 0.0), calibration_generation=GENERATION,
                       vehicle_session='live-consumer-integration-drive')

  def initial_offer(self):
    now = BASE + (self.tick + 1) * DT
    runtime = self.runtime
    source = self.source(now)
    assert runtime.identity and runtime.live_vehicle_model_generation
    assert runtime.identity_source_epoch == source.source_epoch
    binding = LiveTrialBinding(runtime.identity, runtime.descriptor.cp_sha256,
      runtime.descriptor.controller_revision, runtime.baseline_revision,
      source.source_epoch, source.reset, source.calibration_generation,
      runtime.live_vehicle_model_generation, runtime.native_identity, '0' * 64, source.persistent_source_epoch, runtime.native_required)
    binding, witness = response(binding, now)
    original = offer(now)
    return replace(original, binding=binding, response_witness=witness,
      baseline_sha256=full_profile_sha256(binding.baseline_revision, original.baseline),
      target_sha256=full_profile_sha256(binding.baseline_revision, original.target),
      deadline_ns=now + 120_000_000_000)

  def renew(self, proposal, now):
    from openpilot.starpilot.flm.live_tracking_response import ResponseWitness, prepare_response_witness, validate_response_witness

    raw = validate_response_witness(proposal.response_witness, proposal.binding, proposal.observed_ns)
    witness = prepare_response_witness(ResponseWitness(raw.certificate, proposal.binding,
      now, now, now, proposal.started_ns - 500_000_000, 8, 0.001, now + 100_000_000), proposal.binding, now)
    return replace(proposal, sequence=self.publications + 1, observed_ns=now,
                   expires_ns=now + 100_000_000, response_witness=witness)

  def frame(self, proposal=None, *, publish=True, source_valid=True, native_allowed=True,
            driver=False, active=True, model_age=0, lease_ns=100_000_000):
    self.tick += 1
    now = BASE + self.tick * DT
    controls = self.controls
    original_message = messaging.new_message

    def packet(service, *args, **kwargs):
      event = original_message(service, *args, **kwargs)
      if service == 'carState':
        event.carState.steeringAngleDeg = math.degrees(controls.VM.get_steer_from_curvature(-0.0002, 30.0, 0.0))
      elif service == 'extrinsicsCalibration':
        event.extrinsicsCalibration.calStatus = 'calibrated'
        event.extrinsicsCalibration.validBlocks = 50
        event.extrinsicsCalibration.rpyCalibSpread = [0.0, 0.001, 0.001]
      return event

    from openpilot.starpilot.aol.wire import encode_safety

    def native_packet(value):
      # Change only the typed Panda input on denial. The positive path uses
      # the existing real wire encoder and Controls' current_native parser.
      if not native_allowed:
        value = replace(value, lateralAllowed=False, longitudinalAllowed=False,
          pandaInventory=tuple(replace(slot, controlsAllowed=False) for slot in value.pandaInventory))
      return encode_safety(value)

    with (mock.patch.object(messaging, 'new_message', side_effect=packet),
          mock.patch('openpilot.starpilot.aol.wire.encode_safety', side_effect=native_packet)):
      feed(controls, now, self.tick, speed=30.0, calibration=(0.0, 0.0, 0.0),
           override=driver, active=active, model_age=model_age)
    events = []
    # The existing feed fixture supplies the actual GlobalRuntime inputs.
    # Populate other ordinary Controls subscriptions at their declared rates
    # too, so all_checks() is a real aggregation assertion, not a flag patch.
    for service in controls.sm.services:
      frequency = SERVICE_LIST[service].frequency
      if service in FED or service in controls.sm.ignore_alive or frequency <= 0:
        continue
      period = round(1e9 / frequency)
      if service not in self.extra_last:
        for stamp in (now - 2 * period, now - period):
          event = messaging.new_message(service, valid=True, logMonoTime=stamp)
          controls.sm.update_msgs(stamp / 1e9, [messaging.log_from_bytes(event.to_bytes())])
        self.extra_last[service] = now - period
      if now - self.extra_last[service] >= period:
        event = messaging.new_message(service, valid=True, logMonoTime=now)
        events.append(messaging.log_from_bytes(event.to_bytes()))
        self.extra_last[service] = now
    if self.tick % 25 == 0:
      events.append(source_packet(controls, now, self.tick, (0.0, 0.0, 0.0)))
    panda_period = round(100 / SERVICE_LIST['pandaStates'].frequency)
    if self.tick % panda_period == 0:
      event = messaging.new_message('pandaStates', len(controls.CP.safetyConfigs), valid=True, logMonoTime=now)
      for panda, config in zip(event.pandaStates, controls.CP.safetyConfigs, strict=True):
        panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
        panda.alternativeExperience = controls.CP.alternativeExperience
        panda.controlsAllowed = native_allowed and str(config.safetyModel) not in ('silent', 'noOutput')
      events.append(messaging.log_from_bytes(event.to_bytes()))
    if publish and self.tick % 5 == 0:
      event = messaging.new_message(LIVE, valid=source_valid, logMonoTime=now)
      write_tracking_source(event, self.source(now))
      if proposal is not None:
        self.latest = replace(self.renew(proposal, now), expires_ns=now + lease_ns)
        write_trial(event, self.latest)
      self.publications += 1
      events.append(messaging.log_from_bytes(event.to_bytes()))
    controls.sm.update_msgs(now / 1e9, events)
    with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
      command, _ = controls.state_control()
    assert controls.global_learning is not None  # Optional exceptions must not silently satisfy denials.
    assert not controls.sm.simulation
    assert math.isfinite(command.actuators.torque) and abs(command.actuators.torque) <= 1.0
    self.last_command = command
    return now, command

  def assert_applied(self):
    runtime = self.runtime
    owner = runtime.live_trial_owner
    assert runtime.reason == 'qualified', (self.tick, runtime.reason)
    assert owner is not None and self.controls.LaC.automatic_flm_effects is owner
    assert owner.used and owner.frame_complete(runtime.descriptor.domain)
    assert type(runtime.outcome) is LiveTrialApplication
    assert runtime.outcome.status == 'applied'
    assert runtime.outcome.control_ns == BASE + self.tick * DT
    assert runtime.outcome.sequence == self.latest.sequence
    assert runtime.profile is None and runtime.validation is None
    assert owner.confirmed_full == self.latest.baseline or owner.offer.phase == 'confirmed'
    return owner.applied_full['ff_gain_left']

  def assert_neutral(self):
    assert self.controls.LaC.automatic_flm_effects is None
    owner = self.runtime.live_trial_owner
    if owner is not None:
      assert owner.source is None and not any(owner.applied_full.values())
      assert owner.application is None
    outcome = self.runtime.outcome
    if outcome is not None:
      from openpilot.starpilot.flm.adaptive_profile import ApplicationOutcome
      # Active original control still emits fresh legacy neutral bookkeeping.
      # This must never be a stale live-trial acknowledgement or correction.
      assert type(outcome) is ApplicationOutcome
      assert outcome.observed_ns == BASE + self.tick * DT
      assert outcome.identity == self.runtime.identity and outcome.baseline_revision == self.runtime.baseline_revision
      assert outcome.status in ('collecting', 'paused') and outcome.reason == self.runtime.reason
      assert outcome.profile_sequence == 0 and outcome.actual_coordinates == (0.0, 0.0)
      assert outcome.physical_model_revision == ''
      assert outcome.effect_manifest_sha256 == outcome.effect_original_anchor == outcome.effect_applied_profile_sha256 == ''
      assert self.runtime.profile is None and self.runtime.validation is None


@contextmanager
def live_controls():
  cp = interfaces[CAR.TOYOTA_RAV4_TSS2].get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
  with controls_for(cp, 'standard', True) as controls:
    fixture = LiveControls(controls)
    assert LIVE in controls.sm.services and 'pandaStates' in controls.sm.services
    from openpilot.starpilot.aol.vehicle import policy_for
    assert controls.aol_replay and controls.ordinary_axis_ack_required
    assert policy_for(controls.CP).ordinary_axis_ack_required
    assert {'aolAxisState', 'aolSafetyWire'} <= set(controls.sm.services)
    for _ in range(60):
      fixture.frame()
    assert controls.sm.freq_ok[LIVE] and controls.sm.freq_ok[LEGACY]
    assert controls.sm.freq_ok['pandaStates']
    assert controls.sm.freq_ok['aolAxisState'] and controls.sm.freq_ok['aolSafetyWire']
    assert fixture.runtime.native_required and fixture.runtime.native_identity
    assert controls.flm_native_witness is not None
    yield fixture


def test_production100hz_consumer_across4hz_metadata_and20hz_trials_reaches_actual_can():
  from openpilot.starpilot.flm.tests.test_steering_projection import make_interface, install_observer, settle, control, apply

  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    original_tune = fixture.controls.LaC.torque_params.to_dict()
    native = make_interface(CAR.TOYOTA_RAV4_TSS2)
    assert install_observer(native)
    settle(native, control(0.0))
    previous = 0.0
    actual_packets = 0
    for _ in range(120):
      _, command = fixture.frame(proposal)
      value = fixture.assert_applied()
      assert 0 < value <= 0.005
      assert 0 <= value - previous <= 0.00005 + 1e-12
      previous = value
      assert fixture.controls.sm.freq_ok[LIVE] and fixture.controls.sm.freq_ok[LEGACY]
      assert fixture.controls.sm.all_checks()  # Ordinary aggregation sees no artificial rate failure.
      # Card receives the serialized SubMaster reader, not the Controls builder.
      reader = command.as_reader()
      assert reader.actuators.torque == command.actuators.torque and reader.latActive == command.latActive
      _, packets, witness = apply(native, reader)
      if witness is not None:
        actual_packets += 1
        assert (witness.can_address, witness.can_data, witness.can_bus) in packets
    assert actual_packets > 0
    assert previous == pytest.approx(0.005)
    assert fixture.controls.LaC.torque_params.to_dict() == original_tune
    assert fixture.controls.params.get('AutomaticSteeringLiveTrials') is None


def test_missed20hz_publications_expire_at100ms_and_old_pending_cannot_resume_or_confirm():
  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    for _ in range(21):
      fixture.frame(proposal)
      fixture.assert_applied()
    last = fixture.controls.sm.recv_time[LIVE]
    for _ in range(10):
      now, _ = fixture.frame(proposal, publish=False)
      assert now <= round(last * 1e9) + 100_000_000
      fixture.assert_applied()
    fixture.frame(proposal, publish=False)
    fixture.assert_neutral()
    assert fixture.controls.sm.alive[LIVE]  # Generic20Hz liveness is500ms, not the100ms permission lease.
    for _ in range(9):
      fixture.frame(proposal)
      fixture.assert_neutral()
    confirmed = replace(proposal, phase='confirmed')
    for _ in range(5):
      fixture.frame(confirmed)
      fixture.assert_neutral()
    assert fixture.runtime.live_trial_owner.confirmed_full == proposal.baseline
    assert fixture.controls.params.get('AutomaticSteeringLiveTrials') is None
    replacement = replace(proposal, trial_id='c' * 64, target_sequence=2)
    for _ in range(10):
      fixture.frame(replacement)
      if fixture.tick % 5 == 0 or fixture.runtime.reason == 'qualified':
        fixture.assert_applied()


@pytest.mark.parametrize('fault', ['driver', 'native', 'source', 'model', 'inactive'])
def test_current_fault_neutralizes_then_rollback_requires_fresh_permission(fault):
  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    for _ in range(20):
      fixture.frame(proposal)
      fixture.assert_applied()
    assert fixture.tick == 79
    before = fixture.runtime.live_trial_owner.applied_full['ff_gain_left']
    assert before > 0
    kwargs = {'driver': fault == 'driver', 'native_allowed': fault != 'native',
              'source_valid': fault != 'source', 'model_age': 1_000_000_000 if fault == 'model' else 0,
              'active': fault != 'inactive'}
    fixture.frame(proposal, **kwargs)
    fixture.assert_neutral()
    rollback = replace(proposal, phase='rollback', target=proposal.baseline, target_sha256=proposal.baseline_sha256)
    # The rollback offer is configuration; fresh native/current source admission
    # remains necessary before it can submit a controller command.
    for _ in range(10):
      fixture.frame(rollback, **{**kwargs, 'native_allowed': False})
      fixture.assert_neutral()
    assert fixture.runtime.live_trial_owner.confirmed_full == proposal.baseline
    for _ in range(15):
      fixture.frame(rollback)
      if fixture.tick >= 100:
        fixture.assert_applied()
      else:
        fixture.assert_neutral()
    assert fixture.runtime.live_trial_owner.confirmed_full == proposal.baseline
    assert not any(fixture.runtime.live_trial_owner.applied_full.values())
    assert fixture.runtime.outcome.confirmed_target_sha256 == ''
    from openpilot.starpilot.flm.learning_wire import write_control, read_control

    now = BASE + fixture.tick * DT
    evidence = fixture.runtime.capture_evidence(control_ns=now,
      state_ns=int(fixture.controls.sm.logMonoTime['carState']), active=bool(fixture.last_command.latActive), limited=False)
    assert evidence is not None and evidence.live_applied_coordinates == (0.0,) * 30
    event = messaging.new_message('starpilotLateralState', valid=True, logMonoTime=now)
    write_control(event.starpilotLateralState, evidence)
    assert read_control(messaging.log_from_bytes(event.to_bytes())).live_applied_coordinates == (0.0,) * 30


def test_confirmed_requires_actual_controller_ack_and_retains_exact_older_publication_sequence():
  from openpilot.starpilot.flm.live_trial_wire import write_application, read_application

  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    for _ in range(110):
      fixture.frame(proposal)
      fixture.assert_applied()
    assert fixture.runtime.live_trial_owner.confirmed_full == proposal.baseline
    confirmed = replace(proposal, phase='confirmed')
    for _ in range(5):
      now, _ = fixture.frame(confirmed)
    fixture.assert_applied()
    application = fixture.runtime.outcome
    assert application.confirmed_target_sha256 == proposal.target_sha256
    assert fixture.runtime.live_trial_owner.confirmed_full == proposal.target
    event = messaging.new_message('starpilotLateralState', valid=True, logMonoTime=now)
    write_application(event.starpilotLateralState, application)
    later = replace(fixture.latest, sequence=fixture.latest.sequence + 1)
    parsed = read_application(messaging.log_from_bytes(event.to_bytes()).starpilotLateralState, now_ns=now, offer=later)
    assert parsed is not None and parsed.sequence == application.sequence
    assert parsed.sequence < later.sequence
    assert parsed.applied == proposal.target
    assert parsed.confirmed_target_sha256 == proposal.target_sha256
    assert read_application(event.starpilotLateralState, now_ns=now + 100_000_001, offer=later) is None


def test_actual_control_evidence_keeps_owner_vm_anchor_and_typed_wire_bounds():
  from openpilot.starpilot.flm.learning_wire import write_control, read_control

  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    now, command = fixture.frame(proposal)
    fixture.assert_applied()
    evidence = fixture.runtime.capture_evidence(control_ns=now,
      state_ns=int(fixture.controls.sm.logMonoTime['carState']), active=bool(command.latActive), limited=False)
    assert evidence is not None
    assert evidence.vehicle_model_anchor == tuple(fixture.runtime.vm_anchor)
    assert len(evidence.vehicle_model_anchor) == 3
    actual = tuple(value for _, value in fixture.runtime.outcome.applied)
    assert len(evidence.live_applied_coordinates) == 30
    assert evidence.live_applied_coordinates == actual
    assert evidence.live_applied_coordinates != tuple(value for _, value in proposal.target)
    assert evidence.effect_trace is None  # A live stage acknowledgement is not a replay trace.
    assert evidence.applied_coordinates == (0.0, 0.0)
    event = messaging.new_message('starpilotLateralState', valid=True, logMonoTime=now)
    write_control(event.starpilotLateralState, evidence)
    decoded = read_control(messaging.log_from_bytes(event.to_bytes()))
    assert decoded.vehicle_model_anchor == evidence.vehicle_model_anchor
    assert decoded.live_applied_coordinates == actual
    assert decoded.effect_trace is None
    # Historical recordings did not carry an anchor; no reconstruction mints one.
    write_control(event.starpilotLateralState, replace(evidence, vehicle_model_anchor=(), live_applied_coordinates=()))
    historical = read_control(messaging.log_from_bytes(event.to_bytes()))
    assert historical.vehicle_model_anchor == ()
    assert historical.live_applied_coordinates == ()
    for invalid in ((1.0,), (1.0, 2.0), (1.0, 2.0, 3.0, 4.0),
                    (1.0, 0.0, 3.0), (1.0, math.nan, 3.0), (1.0, math.inf, 3.0)):
      with pytest.raises(ValueError, match='control evidence'):
        write_control(event.starpilotLateralState, replace(evidence, vehicle_model_anchor=invalid))
      write_control(event.starpilotLateralState, evidence)
      event.starpilotLateralState.learningEvidence.vehicleModelAnchor = invalid
      with pytest.raises(ValueError, match='control version or bound'):
        read_control(messaging.log_from_bytes(event.to_bytes()))
    for invalid in ((0.0,), (0.0,) * 29, (0.0,) * 31, (math.nan,) * 30, (math.inf,) * 30):
      with pytest.raises(ValueError, match='control evidence'):
        write_control(event.starpilotLateralState, replace(evidence, live_applied_coordinates=invalid))
      write_control(event.starpilotLateralState, evidence)
      event.starpilotLateralState.learningEvidence.liveAppliedCoordinates = invalid
      with pytest.raises(ValueError, match='control version or bound'):
        read_control(messaging.log_from_bytes(event.to_bytes()))
    now, command = fixture.frame(proposal, driver=True)
    fixture.assert_neutral()
    interrupted = fixture.runtime.capture_evidence(control_ns=now,
      state_ns=int(fixture.controls.sm.logMonoTime['carState']), active=bool(command.latActive), limited=False)
    assert interrupted is None  # No previous live map or acknowledgement survives as current evidence.
    assert fixture.runtime.outcome is None
    assert not any(fixture.runtime.live_trial_owner.applied_full.values())


def test_consumed_live_map_without_same_frame_publication_is_absent_not_neutral():
  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    now, command = fixture.frame(proposal, lease_ns=100_000_000)
    fixture.assert_applied()
    actual = fixture.runtime.outcome.applied
    assert any(value != 0.0 for _, value in actual)
    assert fixture.runtime.live_trial_owner.used
    assert fixture.runtime.live_trial_owner.frame_complete(fixture.runtime.descriptor.domain)
    publication_ns = now + 30_000_001
    assert publication_ns <= fixture.latest.expires_ns
    evidence = fixture.runtime.capture_evidence(control_ns=publication_ns,
      state_ns=int(fixture.controls.sm.logMonoTime['carState']), active=bool(command.latActive), limited=False)
    assert evidence is None
    assert fixture.runtime.outcome.applied == actual  # Historical consumption is not relabelled as neutral.


def test_interrupted_pending_consumer_reports_rejection_after_actual_current_frame():
  from openpilot.starpilot.flm.application_wire import write_application
  from openpilot.starpilot.flm.live_trial_wire import read_application

  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    for _ in range(21):
      fixture.frame(proposal)
    for _ in range(11):
      fixture.frame(proposal, publish=False)
    for _ in range(9):
      now, _ = fixture.frame(proposal)
    fixture.assert_neutral()
    owner = fixture.runtime.live_trial_owner
    assert owner.interrupted and owner.offer.phase == 'trial'
    evidence = fixture.runtime.capture_evidence(control_ns=now,
      state_ns=int(fixture.controls.sm.logMonoTime['carState']), active=bool(fixture.last_command.latActive), limited=False)
    assert evidence is not None and evidence.valid and evidence.native_allowed
    refusal = fixture.runtime.outcome
    assert type(refusal) is LiveTrialApplication and refusal.status == 'rejected'
    assert refusal.reason == 'pending_trial_interrupted' and refusal.confirmed_target_sha256 == ''
    assert refusal.trial_id == fixture.latest.trial_id and refusal.target_sequence == fixture.latest.target_sequence
    assert not any(value for _, value in refusal.applied) and owner.confirmed_full == proposal.baseline
    message = messaging.new_message('starpilotLateralState', valid=True, logMonoTime=now)
    write_application(message.starpilotLateralState, refusal)
    decoded = read_application(messaging.log_from_bytes(message.to_bytes()).starpilotLateralState,
      now_ns=now, offer=fixture.latest)
    assert decoded == refusal
    assert fixture.controls.params.get('AutomaticSteeringLiveTrials') is None


@pytest.mark.parametrize('fault', ['driver', 'native', 'source', 'model', 'inactive'])
def test_pending_rejection_never_bypasses_current_consumer_faults(fault):
  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    for _ in range(20):
      fixture.frame(proposal)
    for _ in range(11):
      fixture.frame(proposal, publish=False)
    for _ in range(9):
      fixture.frame(proposal)
    assert fixture.tick == 99  # Next frame delivers real10Hz Panda and20Hz model/source denials.
    now, _ = fixture.frame(proposal, driver=fault == 'driver', native_allowed=fault != 'native',
      source_valid=fault != 'source', model_age=1_000_000_000 if fault == 'model' else 0, active=fault != 'inactive')
    fixture.runtime.capture_evidence(control_ns=now, state_ns=int(fixture.controls.sm.logMonoTime['carState']),
      active=bool(fixture.last_command.latActive), limited=False)
    outcome = fixture.runtime.outcome
    assert type(outcome) is not LiveTrialApplication
    assert fixture.controls.params.get('AutomaticSteeringLiveTrials') is None


@pytest.mark.parametrize('publication_delay_ns', (0, 2_000_000, 3_036_210, 30_000_000))
def test_consumed_live_map_evidence_uses_consumption_lease_with_bounded_publication(publication_delay_ns):
  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    consumed, command = fixture.frame(proposal, lease_ns=2_000_000)
    fixture.assert_applied()
    assert fixture.latest.observed_ns == consumed and fixture.latest.expires_ns == consumed + 2_000_000
    evidence = fixture.runtime.capture_evidence(control_ns=consumed + publication_delay_ns,
      state_ns=int(fixture.controls.sm.logMonoTime['carState']), active=bool(command.latActive), limited=False)
    assert evidence is not None
    assert evidence.effect_prepare_ns == consumed and evidence.control_ns == consumed + publication_delay_ns
    assert evidence.live_applied_coordinates == tuple(value for _, value in fixture.runtime.outcome.applied)
    assert any(evidence.live_applied_coordinates)
    assert fixture.runtime.live_trial_owner.offer.expires_ns == consumed + 2_000_000  # Never renewed or restamped.


@pytest.mark.parametrize('refusal', ('late_publication', 'source_expired', 'native_expired', 'unconsumed_stage'))
def test_consumed_evidence_still_requires_current_source_native_and_an_actual_stage(refusal):
  from types import SimpleNamespace
  with live_controls() as fixture:
    consumed, command = fixture.frame(fixture.initial_offer(), lease_ns=2_000_000)
    fixture.assert_applied()
    runtime = fixture.runtime
    delay = 30_000_001 if refusal == 'late_publication' else 3_000_000
    if refusal == 'source_expired':
      runtime.source = replace(runtime.source, expires_ns=consumed + 2_000_000)
    elif refusal == 'native_expired':
      original = runtime.native_witness
      # Denial-only input: shorten the actual witness; never manufacture a grant.
      runtime.native_witness = SimpleNamespace(observedMonoTime=original.observedMonoTime,
        validUntilMonoTime=consumed + 2_000_000, requestedLateral=original.requestedLateral,
        lateralAllowed=original.lateralAllowed)
    elif refusal == 'unconsumed_stage':
      runtime.outcome = replace(runtime.outcome, status='neutral', reason='live_stage_not_used')
    evidence = runtime.capture_evidence(control_ns=consumed + delay,
      state_ns=int(fixture.controls.sm.logMonoTime['carState']), active=bool(command.latActive), limited=False)
    if refusal == 'native_expired':
      assert evidence is not None and not evidence.native_allowed and not evidence.valid
      assert not evidence.live_applied_coordinates
      assert type(runtime.outcome) is not LiveTrialApplication
      assert runtime.reason == 'native_lease_unavailable'
    else:
      assert evidence is None


def test_expired_offer_cannot_be_consumed_again_after_an_evidence_capture():
  with live_controls() as fixture:
    proposal = fixture.initial_offer()
    consumed, command = fixture.frame(proposal, lease_ns=2_000_000)
    fixture.assert_applied()
    assert fixture.runtime.capture_evidence(control_ns=consumed + 3_000_000,
      state_ns=int(fixture.controls.sm.logMonoTime['carState']), active=bool(command.latActive), limited=False) is not None
    fixture.frame(proposal, publish=False)
    fixture.assert_neutral()
