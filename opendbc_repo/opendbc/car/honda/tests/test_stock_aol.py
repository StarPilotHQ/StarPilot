from dataclasses import replace
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.stock_aol import CLASSIC_BOSCH, NIDEC, RADARLESS, qualified, temporary_restriction, native_observation
from opendbc.car.honda.values import CAR, HondaFlags
from openpilot.starpilot.aol.intent import AolSettings, AOL_TOGGLE
from openpilot.starpilot.car.honda.aol import policy_for, create_intent, native_accepts_cp


AUTO_188 = frozenset((CAR.ACURA_RDX, CAR.HONDA_CRV_SA, CAR.HONDA_ACCORD_9G))
IDENTITIES = tuple(sorted(CLASSIC_BOSCH | NIDEC | RADARLESS, key=str))


def final_cp(identity, alpha=False, release=False, *, hybrid=False):
  fingerprint = gen_empty_fingerprint()
  pt = 1 if identity in CLASSIC_BOSCH else 0
  address, length = (0x188, 6) if identity in AUTO_188 else (0x1A3, 8)
  fingerprint[pt][address] = length
  if hybrid:
    fingerprint[pt][0x184] = 8
  return CarInterface.get_params(identity, fingerprint, [], alpha, release, False)


@pytest.mark.parametrize("identity", IDENTITIES)
@pytest.mark.parametrize("alpha,release", ((False, False), (True, False), (True, True)))
@pytest.mark.parametrize("hybrid", (False, True))
def test_actual_factory_admission_and_marker(identity, alpha, release, hybrid):
  cp = final_cp(identity, alpha, release, hybrid=hybrid)
  assert bool(cp.flags & HondaFlags.HYBRID) == hybrid
  expected = True
  assert qualified(cp) == expected
  if not expected:
    return
  before = cp.to_dict()
  policy = policy_for(cp)
  assert not policy.ordinary_axis_ack_required
  settings = AolSettings(True, 5.0, AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0))
  intent = create_intent(cp, settings)
  assert intent.explicit_latch
  cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  assert qualified(cp, marked_only=True)
  assert policy_for(cp).ordinary_axis_ack_required
  assert cp.alternativeExperience == 32
  expected_word = before["safetyConfigs"][0]["safetyParam"]
  if identity == CAR.HONDA_ODYSSEY_TWN:
    expected_word |= 256
  if identity in CLASSIC_BOSCH and cp.openpilotLongitudinalControl:
    expected_word |= 2080
  assert cp.safetyConfigs[0].safetyParam == expected_word
  assert cp.openpilotLongitudinalControl == before["openpilotLongitudinalControl"]
  assert cp.pcmCruise == before["pcmCruise"]
  assert native_accepts_cp(cp, cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam)
  cp.alternativeExperience = 0
  assert not qualified(cp, marked_only=True)
  assert not native_accepts_cp(cp, cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam)


@pytest.mark.parametrize("identity", IDENTITIES)
@pytest.mark.parametrize("hybrid", (False, True))
def test_manual_unknown_namespace_and_extra_panda_denied(identity, hybrid):
  cp = final_cp(identity, hybrid=hybrid)
  cp.fingerprintSource = structs.CarParams.FingerprintSource.fixed
  assert qualified(cp)  # Explicit registered selection retains truthful metadata.
  cp = final_cp(identity, hybrid=hybrid)
  cp.transmissionType = structs.CarParams.TransmissionType.manual
  assert not qualified(cp)
  cp = final_cp(identity, hybrid=hybrid)
  cp.flags |= 1 << 31
  assert not qualified(cp)
  cp = final_cp(identity, hybrid=hybrid)
  cp.safetyConfigs[0].safetyParam |= 0x4000
  assert not qualified(cp)
  cp = final_cp(identity, hybrid=hybrid)
  cp.safetyConfigs = [structs.CarParams.SafetyConfig(safetyModel="noOutput"), cp.safetyConfigs[0]]
  assert not qualified(cp)


def state(pressed=None, *, available=True, gear="drive", temporary=False):
  cs = structs.CarState(canValid=True, gearShifter=gear, steerFaultTemporary=temporary)
  cs.cruiseState.available = available
  if pressed is not None:
    cs.buttonEvents = [structs.CarState.ButtonEvent(type="lkas", pressed=pressed)]
  return cs.as_reader()


def test_real_schema_loss_then_lkas_arms_once_without_toggle_off():
  cp = final_cp(CAR.HONDA_CIVIC_BOSCH)
  cp.alternativeExperience = 32
  settings = AolSettings(True, 5.0, AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0))
  intent = create_intent(cp, settings)
  intent.update(state(), now_ns=10)
  intent.update(state(True), now_ns=20)
  intent.update(state(False), now_ns=30)
  assert intent.allowed_latch
  intent.update(state(), now_ns=40, native_rejection_ns=40)
  assert not intent.allowed_latch
  intent.update(state(True), now_ns=50, native_rejection_ns=40)
  assert intent.allowed_latch
  assert not intent.pause_lateral
  intent.update(state(False), now_ns=60)
  intent.update(state(gear="reverse"), now_ns=70)
  assert intent.allowed_latch
  assert temporary_restriction(cp, state(gear="reverse"))
  intent.update(state(temporary=True), now_ns=80)
  assert intent.allowed_latch


def test_cancel_uses_saved_pause_instead_of_erasing_latch():
  cp = final_cp(CAR.HONDA_CIVIC_BOSCH)
  cp.alternativeExperience = 32
  for action in (0, 3):
    settings = AolSettings(True, 5.0, AOL_TOGGLE, 0, (0, 0, 0), (action, 0, 0))
    intent = create_intent(cp, settings)
    intent.update(state(), now_ns=10)
    intent.update(state(True), now_ns=20)
    intent.update(state(False), now_ns=30)
    cs = structs.CarState(**state().to_dict())
    cs.buttonEvents = [structs.CarState.ButtonEvent(type="cancel", pressed=True)]
    intent.update(cs.as_reader(), now_ns=40)
    cs.buttonEvents = [structs.CarState.ButtonEvent(type="cancel", pressed=False)]
    intent.update(cs.as_reader(), now_ns=50)
    assert intent.allowed_latch
    assert intent.pause_lateral == (action == 3)


@pytest.mark.parametrize("lkas_action,main_action", ((0, 0), (0, AOL_TOGGLE), (AOL_TOGGLE, 0), (3, 4)))
def test_configured_button_ownership_and_default_main_pulse(lkas_action, main_action):
  cp = final_cp(CAR.HONDA_CIVIC_BOSCH)
  cp.alternativeExperience = 32
  settings = AolSettings(True, 5.0, lkas_action, main_action, (0, 0, 0), (0, 0, 0))
  intent = create_intent(cp, settings)
  intent.update(state(available=False), now_ns=10)
  intent.update(state(), now_ns=20)
  intent.update(state(), now_ns=30)
  automatic = lkas_action != AOL_TOGGLE and main_action != AOL_TOGGLE
  assert intent.allowed_latch == automatic
  assert not intent._main_held
  if automatic:
    intent.allowed_latch = False
  for button, action in (("mainCruise", main_action), ("lkas", lkas_action)):
    cs = structs.CarState(**state().to_dict())
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=True)]
    intent.update(cs.as_reader(), now_ns=40)
    assert intent.allowed_latch == (action == AOL_TOGGLE)
    if action == 3:
      assert intent.pause_lateral
    if action == 4:
      assert intent.pause_longitudinal
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=False)]
    intent.update(cs.as_reader(), now_ns=50)
    intent.allowed_latch = False
    intent.pause_lateral = False


def civic_engagement_owner(*, enabled=True, lkas_action=AOL_TOGGLE, main_action=0):
  cp = final_cp(CAR.HONDA_CIVIC_2022, alpha=True, hybrid=True)
  cp.alternativeExperience = 32
  assert cp.flags == 2060 and cp.safetyConfigs[0].safetyParam == 10
  settings = AolSettings(enabled, 5., lkas_action, main_action, (0, 0, 0), (0, 0, 0))
  intent = create_intent(cp, settings)
  intent.observe_full_engagement(state(), enabled=False, active=False, source_ns=1,
                                  observation_valid=True, now_ns=1, fault_active=False)
  return cp, intent


def update_engagement(intent, cs, *, now_ns, standard_enabled=False, standard_active=False,
                      fault_active=False, native_rejection_ns=0):
  intent.update(cs, now_ns=now_ns, standard_enabled=standard_enabled,
                fault_active=fault_active, native_rejection_ns=native_rejection_ns)
  intent.observe_full_engagement(cs, enabled=standard_enabled or standard_active, active=standard_active,
                                source_ns=now_ns, observation_valid=True, now_ns=now_ns,
                                fault_active=fault_active, native_rejection_ns=native_rejection_ns)


@pytest.mark.parametrize("main_action", (0, AOL_TOGGLE))
@pytest.mark.parametrize("paused,temporary", ((False, False), (True, False), (False, True)))
def test_active_engagement_arms_without_raw_cruise_resumes_lateral_and_preserves_long_pause(main_action, paused, temporary):
  from openpilot.starpilot.aol.runtime import decide_axes

  _, intent = civic_engagement_owner(main_action=main_action)
  cs = structs.CarState(**state(temporary=temporary).to_dict())
  cs.vEgo = 20.
  cs.gasPressed = True
  assert not cs.cruiseState.enabled  # Non-PCM host engagement can precede ACC_STATUS.
  intent.pause_lateral = intent.pause_longitudinal = paused
  update_engagement(intent, cs.as_reader(), now_ns=10, fault_active=False, standard_enabled=True)
  assert not intent.allowed_latch  # Enabled/preEnabled alone is not the active edge.
  update_engagement(intent, cs.as_reader(), now_ns=20, fault_active=False, standard_enabled=True, standard_active=True)
  assert intent.allowed_latch
  assert not intent.pause_lateral and intent.pause_longitudinal is paused
  cs.gasPressed = False
  cs.brakePressed = True
  update_engagement(intent, cs.as_reader(), now_ns=30, fault_active=False, standard_active=False)
  assert intent.allowed_latch  # Braking drops normal cruise, retaining AOL intent.
  allowed, pause_lat, pause_long = intent.output(cs.as_reader())
  snapshot = SimpleNamespace(allowedLatch=allowed, pauseLateral=pause_lat, pauseLongitudinal=pause_long)
  native = SimpleNamespace(requestedLateral=True, requestedLongitudinal=False,
                           lateralAllowed=True, longitudinalAllowed=False)
  kwargs = {"standard_lateral": False, "standard_longitudinal": False, "intent": snapshot,
                "car_state": cs.as_reader(), "initialized": True, "model_ready": True,
                "no_entry": False, "immediate_disable": False, "dm_lockout": False, "pause_brake_mps": 5.}
  decision = decide_axes(native=native, **kwargs)
  assert decision.lateral_active == (not temporary)
  assert not decision.longitudinal_active
  assert not decide_axes(native=None, **kwargs).lateral_active
  cs.vEgo = 4.9
  assert not decide_axes(native=native, **kwargs).lateral_active
  cs.vEgo = 20.
  kwargs["no_entry"] = True
  assert not decide_axes(native=native, **kwargs).lateral_active


@pytest.mark.parametrize("blocked", ("disabled", "main_off", "can_invalid", "timeout",
                                     "fatal", "permanent", "acc_fault", "native_rejection", "park", "unknown", "reverse", "neutral"))
def test_blocked_engagement_edge_is_consumed_without_rearm_on_held_active(blocked):
  _, intent = civic_engagement_owner(enabled=blocked != "disabled")
  cs = structs.CarState(**state().to_dict())
  update_engagement(intent, cs.as_reader(), now_ns=10, fault_active=False)
  if blocked == "main_off":
    cs.cruiseState.available = False
  for case, field in (("can_invalid", "canValid"), ("timeout", "canTimeout"),
                       ("permanent", "steerFaultPermanent"), ("acc_fault", "accFaulted")):
    if blocked == case:
      setattr(cs, field, case != "can_invalid")
  if blocked in ("park", "unknown", "reverse", "neutral"):
    cs.gearShifter = blocked
  update_engagement(intent, cs.as_reader(), now_ns=20, standard_enabled=True, standard_active=True,
                fault_active=blocked == "fatal", native_rejection_ns=20 if blocked == "native_rejection" else 0)
  assert not intent.allowed_latch
  intent.settings = replace(intent.settings, enabled=True, lkas_action=AOL_TOGGLE)
  update_engagement(intent, state(), now_ns=30, standard_enabled=True, standard_active=True, fault_active=False)
  assert not intent.allowed_latch
  update_engagement(intent, state(), now_ns=40, fault_active=False)
  update_engagement(intent, state(), now_ns=50, standard_enabled=True, standard_active=True, fault_active=False)
  assert intent.allowed_latch


@pytest.mark.parametrize("button", ("lkas", "mainCruise", "cancel"))
def test_manual_gesture_takes_priority_over_coincident_active_edge(button):
  _, intent = civic_engagement_owner(main_action=AOL_TOGGLE)
  cs = structs.CarState(**state().to_dict())
  update_engagement(intent, cs.as_reader(), now_ns=10, fault_active=False)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=True)]
  update_engagement(intent, cs.as_reader(), now_ns=20, standard_enabled=True, standard_active=True, fault_active=False)
  assert not intent.allowed_latch
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=False)]
  update_engagement(intent, cs.as_reader(), now_ns=30, standard_enabled=True, standard_active=True, fault_active=False)
  assert not intent.allowed_latch


def test_manual_off_and_native_withdrawal_do_not_rearm_while_active_stays_high():
  _, intent = civic_engagement_owner()
  update_engagement(intent, state(), now_ns=10, fault_active=False)
  update_engagement(intent, state(), now_ns=20, standard_enabled=True, standard_active=True, fault_active=False)
  assert intent.allowed_latch
  update_engagement(intent, state(True), now_ns=30, standard_enabled=True, standard_active=True, fault_active=False)
  assert not intent.allowed_latch and intent.pause_lateral
  update_engagement(intent, state(False), now_ns=40, standard_enabled=True, standard_active=True, fault_active=False)
  assert not intent.allowed_latch and intent.pause_lateral
  update_engagement(intent, state(), now_ns=50, fault_active=False)
  update_engagement(intent, state(), now_ns=60, standard_enabled=True, standard_active=True, fault_active=False)
  assert intent.allowed_latch and not intent.pause_lateral  # A new full SET resumes lateral output.
  update_engagement(intent, state(), now_ns=70, standard_enabled=True, standard_active=True, native_rejection_ns=70, fault_active=False)
  assert not intent.allowed_latch
  update_engagement(intent, state(), now_ns=80, standard_enabled=True, standard_active=True, fault_active=False)
  assert not intent.allowed_latch


@pytest.mark.parametrize("gear", ("drive", "sport", "low", "brake"))
def test_supported_driving_gears_keep_active_engagement_intent(gear):
  _, intent = civic_engagement_owner()
  update_engagement(intent, state(gear=gear), now_ns=10, fault_active=False)
  update_engagement(intent, state(gear=gear), now_ns=20, standard_enabled=True, standard_active=True, fault_active=False)
  assert intent.allowed_latch


@pytest.mark.parametrize("lkas_action", (0, AOL_TOGGLE))
def test_full_engagement_does_not_require_a_configured_lkas_toggle(lkas_action):
  _, intent = civic_engagement_owner(lkas_action=lkas_action)
  update_engagement(intent, state(), now_ns=10, standard_enabled=True, standard_active=False)
  assert not intent.allowed_latch
  update_engagement(intent, state(), now_ns=20, standard_enabled=True, standard_active=True)
  assert intent.allowed_latch
  update_engagement(intent, state(), now_ns=30, standard_enabled=False, standard_active=False)
  assert intent.allowed_latch


def test_starting_during_active_engagement_requires_a_fresh_disabled_epoch():
  cp, seeded = civic_engagement_owner()
  intent = create_intent(cp, seeded.settings)
  update_engagement(intent, state(), now_ns=10, standard_enabled=True, standard_active=True)
  update_engagement(intent, state(), now_ns=20, standard_enabled=True, standard_active=True)
  assert not intent.allowed_latch
  update_engagement(intent, state(), now_ns=30)
  update_engagement(intent, state(), now_ns=40, standard_enabled=True, standard_active=True)
  assert intent.allowed_latch


def test_actual_card_active_observation_requires_fresh_selfdrive_state(monkeypatch):
  from openpilot.cereal import log
  from openpilot.selfdrive.car import card as card_module
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.aol.intent import AolCardIntent
  from openpilot.starpilot.aol.wire import SafetyState

  class BoundaryReached(Exception):
    pass

  class Samples(dict):
    def update(self, _timeout):
      pass

  cp, intent = civic_engagement_owner()
  cs = state()
  now = 1_000_000_000
  control = structs.CarControl(enabled=False, latActive=False, longActive=False)
  engagement = log.SelfdriveState.new_message(enabled=False, active=False)
  samples = Samples(carControl=control.as_reader(), onroadEvents=[], selfdriveState=engagement.as_reader())
  samples.valid = {"carControl": True, "onroadEvents": True, "selfdriveState": True}
  samples.alive = {"carControl": True, "selfdriveState": True}
  samples.seen = {"selfdriveState": True}
  samples.updated = {"onroadEvents": True}
  samples.logMonoTime = {"carControl": now, "onroadEvents": now, "selfdriveState": now}
  host = Car.__new__(Car)
  host.CP, host.aol_card_intent = cp, intent
  host.aol_settings = intent.settings
  host.CI = SimpleNamespace(update=lambda _: cs, CS=SimpleNamespace(),
                            can_parsers=CarInterface.CarState(cp).get_can_parsers(cp))
  host.RI = SimpleNamespace(update=lambda _: None)
  host.car_gps_publisher = SimpleNamespace(update=lambda *args: None)
  host.observe_ioniq6_long_authority = lambda *args: None
  host.update_vehicle_state_context = lambda *args: None

  def update_cruise_speed(_state, _pending, *, wheel_context=None):
    assert wheel_context is None
  host.update_cruise_speed = update_cruise_speed
  host.v_cruise_helper = SimpleNamespace(slc_consumed_button=None, toyota=None)
  host.startup_panda_configured = lambda: True
  host.can_sock = object()
  host.pm = None
  host.sm = samples
  host.CC_prev = structs.CarControl(enabled=False).as_reader()
  host.slc_replay = False
  host.controller_cruise_sock = None
  host.slc_producer_session = "honda-engagement"
  host.can_rcv_cum_timeout_counter = 0
  monkeypatch.setattr(card_module, "REPLAY", False)
  monkeypatch.setattr(card_module.time, "monotonic_ns", lambda: now)
  monkeypatch.setattr(card_module.messaging, "drain_sock_raw", lambda *args, **kwargs: [b"fixture-can"])
  monkeypatch.setattr(card_module, "can_capnp_to_list", lambda _: [])
  native = SafetyState(1, True, now, now + 200_000_000, int(cp.safetyConfigs[0].safetyModel.raw),
                       cp.safetyConfigs[0].safetyParam, False, False, False, False,
                       "fixture-panda", host.slc_producer_session)
  monkeypatch.setattr(card_module, "current_native", lambda *args, **kwargs: native)
  observed = []
  observe = intent.observe_full_engagement

  def at_boundary(state, **kwargs):
    observed.append(kwargs)
    observe(state, **kwargs)
    raise BoundaryReached

  monkeypatch.setattr(intent, "observe_full_engagement", at_boundary)

  def tick(*, enabled=True, active=False, valid=True, alive=True, seen=True, age=0, control_active=True):
    nonlocal now
    now += 10_000_000
    previous_enabled = bool(control.enabled)
    control.enabled, control.latActive, control.longActive = enabled, control_active, False
    engagement.enabled, engagement.active = enabled, active
    samples["carControl"] = control.as_reader()
    samples["selfdriveState"] = engagement.as_reader()
    samples.valid["selfdriveState"], samples.alive["selfdriveState"] = valid, alive
    samples.seen["selfdriveState"] = seen
    samples.logMonoTime.update(carControl=now, onroadEvents=now, selfdriveState=now - age)
    host.CC_prev = structs.CarControl(enabled=previous_enabled).as_reader()
    with pytest.raises(BoundaryReached):
      host.state_update()
    return observed[-1]

  assert not tick(enabled=False)["active"]
  assert not tick()["active"]  # PreEnabled: fresh actuator activity cannot invent ordinary active state.
  assert not intent.allowed_latch
  for kwargs in ({"valid": False}, {"alive": False}, {"seen": False}, {"age": 200_000_000}, {"age": -1}):
    assert not tick(active=True, **kwargs)["observation_valid"]
    assert not intent.allowed_latch
  assert tick(active=True)["observation_valid"]
  assert intent.allowed_latch and not cs.cruiseState.enabled
  intent.allowed_latch = False
  tick(active=True)
  assert not intent.allowed_latch  # Repeating the same enabled epoch is not a new SET.
  tick(enabled=False)
  assert tick(active=True, control_active=False)["active"]
  assert intent.allowed_latch  # Actual ordinary state proves engagement independently of actuator samples.

  # The actual Card caller consumes the acknowledged native permission. Its
  # immediate rejection boundary remains strict; no host grace is extended.
  tick(enabled=False)
  cs = state(True)
  native = replace(native, requestedLateral=True, lateralAllowed=True,
                   observedMonoTime=now + 10_000_000, validUntilMonoTime=now + 210_000_000)
  tick(enabled=False)
  assert not intent.allowed_latch  # The previously active latch toggles OFF.
  cs = state(False)
  native = replace(native, observedMonoTime=now + 10_000_000)
  tick(enabled=False)
  cs = state(True)
  native = replace(native, observedMonoTime=now + 10_000_000)
  tick(enabled=False)
  assert intent.allowed_latch  # Release then a new physical press toggles ON.
  cs = state(False)
  native = replace(native, observedMonoTime=now + 10_000_000)
  assert tick(enabled=False)["native_rejection_ns"] == 0
  assert intent.allowed_latch
  native = replace(native, lateralAllowed=False, observedMonoTime=now + 10_000_000)
  observed_native = native.observedMonoTime
  assert tick(enabled=False)["native_rejection_ns"] == observed_native
  assert not intent.allowed_latch  # Native denial immediately revokes independent intent.
  native = replace(native, lateralAllowed=True, observedMonoTime=now + 10_000_000)
  assert tick(enabled=False)["native_rejection_ns"] == 0
  assert not intent.allowed_latch  # Permission recovery alone cannot recreate a gesture.
  cs = state(True)
  native = replace(native, observedMonoTime=now + 10_000_000)
  tick(enabled=False)
  assert intent.allowed_latch  # A genuinely new LKAS edge can arm again.

  generic = AolCardIntent(intent.settings)
  host.aol_card_intent = generic

  def generic_boundary(state, **kwargs):
    assert "standard_active" not in kwargs
    raise BoundaryReached

  monkeypatch.setattr(generic, "update", generic_boundary)
  with pytest.raises(BoundaryReached):
    host.state_update()


@pytest.mark.parametrize("acknowledged,requested,reset", ((False, True, False), (True, True, True),
                                                          (True, False, False), (False, False, False)))
def test_native_cold_pending_vs_requested_ack_loss(acknowledged, requested, reset):
  native = SimpleNamespace(requestedLateral=False, lateralAllowed=False)
  result = native_observation(native, latched=True, panda_ready=True, restricted=False,
                              now_ns=1_000_000_000, pending_since_ns=0,
                              previously_acknowledged=acknowledged, host_requested_lateral=requested)
  assert result == ((0, False, True) if reset else (1_000_000_000, False, False))
  if not reset:
    assert native_observation(native, latched=True, panda_ready=True, restricted=False,
                              now_ns=1_200_000_000, pending_since_ns=result[0],
                              previously_acknowledged=acknowledged, host_requested_lateral=requested) == (result[0], False, True)


@pytest.mark.parametrize("changed", ("cold", "session", "panda", "cp", "manual_off", "restriction",
                                     "software_pause", "host_withdrawal", "invalid_axis", "stale_axis",
                                     "replaced_producer", "older_native", "resuming_pause", "pending_pause", "requested_denial", "unpaired_event",
                                     "fault_event", "invalid_event", "stale_event", "none"))
def test_actual_card_ack_loss_requires_current_requested_same_identity(changed):
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  from openpilot.starpilot.aol.wire import SafetyState

  cp = final_cp(CAR.HONDA_CIVIC_2022, alpha=True, hybrid=True)
  cp.alternativeExperience |= 32
  now = 2_000_000_000
  native = SafetyState(1, True, now, now + 200_000_000, int(cp.safetyConfigs[0].safetyModel.raw),
                       cp.safetyConfigs[0].safetyParam, True, False, True, False, "panda", "axis")
  axis = SimpleNamespace(qualified=True, sessionId="axis", observedMonoTime=now, validUntilMonoTime=now+30_000_000,
                         faultSessionId="axis", faultReason="none", faultEventMonoTime=now, sequence=1,
                         nativeAcknowledged=True, desiredLateral=True, lateralActive=True, sourceCarStateMonoTime=now,
                         intentProducerSessionId="producer")

  class Samples(dict):
    pass
  sm = Samples(aolAxisState=axis, onroadEvents=[])
  sm.valid = sm.alive = sm.seen = {"aolAxisState": True, "onroadEvents": True}
  sm.logMonoTime = {"aolAxisState": now, "onroadEvents": now}
  host = Car.__new__(Car)
  host.CP, host.sm, host.slc_producer_session = cp, sm, "producer"
  host.aol_transport_feedback = TransportPauseFeedback(cp)
  kwargs = {"latched": True, "panda_ready": True, "restricted": False, "now_ns": now, "pending_since_ns": 0}
  assert host.aol_honda_native_observation(native, **kwargs) == (0, False, False)
  if changed == "cold":
    host._honda_aol_acknowledgment = None
  elif changed == "session":
    native = replace(native, axisSessionId="replacement")
  elif changed == "panda":
    native = replace(native, pandaSerial="replacement")
  elif changed == "cp":
    host.CP = final_cp(CAR.HONDA_CIVIC_2022, alpha=False, hybrid=True)
  elif changed == "manual_off":
    kwargs['latched'] = False
  elif changed == "restriction":
    kwargs['restricted'] = True
  elif changed == "software_pause":
    axis.faultReason = "availabilityPause"
    host.aol_transport_feedback.retention_qualified = True
  elif changed == "host_withdrawal":
    axis.desiredLateral = False
  elif changed == "invalid_axis":
    sm.valid = {"aolAxisState": False}
  elif changed == "stale_axis":
    kwargs['now_ns'] += 40_000_000
  elif changed == "replaced_producer":
    axis.intentProducerSessionId = "replacement"
  elif changed == "resuming_pause":
    axis.lateralActive = False
  elif changed == "pending_pause":
    host.aol_transport_feedback.awaiting = True
  elif changed == "unpaired_event":
    axis.faultEventMonoTime -= 1
  elif changed == "fault_event":
    sm["onroadEvents"] = [SimpleNamespace(immediateDisable=True, softDisable=False)]
  elif changed == "invalid_event":
    sm.valid = {"aolAxisState": True, "onroadEvents": False}
  elif changed == "stale_event":
    axis.faultEventMonoTime = sm.logMonoTime["onroadEvents"] = now-1_500_000_001
  elif changed == "older_native":
    native = replace(native, observedMonoTime=now-1)
  withdrawn = replace(native, requestedLateral=False, lateralAllowed=False)
  if changed == "requested_denial":
    withdrawn = replace(withdrawn, requestedLateral=True)
  result = host.aol_honda_native_observation(withdrawn, **kwargs)
  if changed == "none":
    assert result == (0, False, True)
  else:
    assert not result[2]  # No unexpected-loss cause is inferred from ambiguous zero.
  if changed in ("manual_off", "restriction"):
    assert result == (0, False, False)
  if changed in ("cold", "session", "panda", "cp", "manual_off"):
    assert host._honda_aol_acknowledgment is None
