import gc
import time

import pytest

from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.can.dbc import DBC as CANDBC
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR, DBC


CARS = (CAR.MAZDA_CX5, CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021)


def exercise_startup(identity, alpha, release, aol, monkeypatch, mapping=(0, 0), probe=None, cancel_action=3):
  from opendbc.car import car_helpers
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car import card as card_module
  from openpilot.selfdrive.car.card import Car
  from openpilot.selfdrive.controls.controlsd import Controls
  from openpilot.starpilot.aol.runtime import current_native
  from openpilot.starpilot.aol.wire import SafetyState, encode_safety, decode_intent
  from openpilot.starpilot.lateral.tests.test_lane_runtime import feed

  monkeypatch.setenv("SIMULATION", "1")
  monkeypatch.setenv("REPLAY", "1")
  monkeypatch.setenv("AOL_REPLAY_RUNTIME", "1")
  fingerprint = gen_empty_fingerprint()
  with OpenpilotPrefix():
    saved = Params()
    for key, value in (("OpenpilotEnabledToggle", True), ("AlwaysOnLateral", aol),
                       ("AlphaLongitudinalEnabled", alpha), ("IsReleaseBranch", release)):
      saved.put_bool(key, value, block=True)
    saved.put("MainCruiseButtonControl", mapping[0], block=True)
    saved.put("LKASButtonControl", mapping[1], block=True)
    if probe is not None:
      saved.put("CancelButtonControl", cancel_action, block=True)

    initial = CarInterface.get_params(identity, fingerprint, [], card_module.alpha_long_requested(saved, is_release=release), release, False)
    initial.carVin = "0" * 17
    initial.carFw = []
    assert initial.safetyConfigs[0].safetyParam == 0
    expected = initial.to_dict()
    explicit_owner = True
    assert not initial.openpilotLongitudinalControl and initial.pcmCruise and not initial.dashcamOnly
    expected["alternativeExperience"] = 32 if aol and explicit_owner else 0
    subscriber = messaging.sub_sock("carParams", timeout=100, conflate=True)
    intent_subscriber = messaging.sub_sock("aolIntentWire", timeout=100, conflate=True)
    card = ci = controls = None
    try:
      observed = (identity, fingerprint, initial.carVin, [], initial.fingerprintSource, True)
      with monkeypatch.context() as discovery:
        discovery.setattr(car_helpers, "fingerprint", lambda *args: observed)
        discovery.setattr(card_module, "can_comm_callbacks", lambda *args: (lambda *args: [], lambda frames: None))
        initial_can = messaging.new_message("can", 1)
        discovery.setattr(card_module.messaging, "recv_one_retry", lambda socket: initial_can)
        card = Car()
      ci = card.CI
      assert card.CP.to_dict() == expected
      assert ci.CP is card.CP and ci.CC is not None
      assert card.aol_qualified is aol
      if aol:
        assert card.aol_card_intent.explicit_latch is explicit_owner
        assert not card.aol_card_intent.allowed_latch
        assert card.aol_card_intent.settings.main_action == mapping[0]
        assert card.aol_card_intent.settings.lkas_action == mapping[1]
      with structs.CarParams.from_bytes(saved.get("CarParams")) as published:
        assert published.to_dict() == expected
      assert card.aol_replay
      event = intent_event = None
      for _ in range(10):
        card.car_params_published = False
        card.state_publish(ci.update([]), None)
        event = messaging.recv_one(subscriber) or event
        intent_event = messaging.recv_one(intent_subscriber) or intent_event
        if event is not None and intent_event is not None:
          break
        time.sleep(0.01)
      assert event is not None and event.valid and event.carParams.to_dict() == expected
      assert intent_event is not None
      intent = decode_intent(intent_event.aolIntentWire)
      assert intent is not None and not intent.allowedLatch and not intent.lateralArmed
      assert intent.producerSessionId == card.slc_producer_session
      controls = Controls()
      assert controls.CP.to_dict() == expected
      assert controls.ordinary_axis_ack_required is (aol and explicit_owner)
      now = 1_000_000_000
      feed(controls, now, 0, active=False, enabled=False, can_valid=False, can_timeout=True)
      if aol:
        receipt = messaging.new_message("aolSafetyWire", 0, valid=True, logMonoTime=now)
        receipt.aolSafetyWire = encode_safety(SafetyState(
          1, True, now, now + 200_000_000, int(card.CP.safetyConfigs[0].safetyModel.raw),
          card.CP.safetyConfigs[0].safetyParam, False, False, False, False, "fixture-panda", card.slc_producer_session))
        controls.sm.update_msgs(now / 1e9, [receipt.as_reader()])
        native = current_native(controls.sm, controls.CP, now_ns=now, axis_session_id=card.slc_producer_session)
        assert native is not None and not native.lateralAllowed and not native.requestedLateral
        assert current_native(controls.sm, controls.CP, now_ns=now, axis_session_id="different-session") is None
      command, lateral_log = controls.state_control()
      assert not command.enabled and not command.latActive and not command.longActive
      controls.publish(command, lateral_log)
      ci.apply(command.as_reader(), now)
      if probe is not None:
        probe(card, ci, controls, saved)
    finally:
      del controls, card, ci, subscriber, intent_subscriber
      gc.collect()


@pytest.mark.parametrize("identity", CARS)
@pytest.mark.parametrize("alpha", (False, True))
@pytest.mark.parametrize("release", (False, True))
@pytest.mark.parametrize("aol", (False, True))
def test_actual_card_publication_and_disabled_controls(identity, alpha, release, aol, monkeypatch):
  exercise_startup(identity, alpha, release, aol, monkeypatch)


@pytest.mark.parametrize("identity", CARS)
@pytest.mark.parametrize("mutation", ("manual", "multi_panda", "fixed", "dashcam"))
def test_independent_owner_rejects_unadmitted_configuration(identity, mutation):
  from opendbc.car.mazda.stock_aol import qualified
  from openpilot.starpilot.car.mazda.aol import policy_for

  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  if mutation == "manual":
    cp.transmissionType = structs.CarParams.TransmissionType.manual
  elif mutation == "multi_panda":
    cp.safetyConfigs = [cp.safetyConfigs[0], cp.safetyConfigs[0]]
  elif mutation == "fixed":
    cp.fingerprintSource = structs.CarParams.FingerprintSource.fixed
  else:
    cp.dashcamOnly = True
  assert not qualified(cp)
  policy = policy_for(cp)
  assert not policy.intent_supported and not policy.runtime_supported


@pytest.mark.parametrize("identity", CARS)
@pytest.mark.parametrize("mapping", ((9, 9), (3, 4)))
def test_saved_button_mappings_reach_actual_card_owner(identity, mapping, monkeypatch):
  exercise_startup(identity, False, False, True, monkeypatch, mapping=mapping)


@pytest.mark.parametrize("identity", (CAR.MAZDA_CX9, CAR.MAZDA_3, CAR.MAZDA_6))
def test_unchanged_dashcam_configuration_denies_independent_owner(identity):
  from opendbc.car.mazda.stock_aol import qualified
  from openpilot.starpilot.car.mazda.aol import policy_for

  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  assert cp.dashcamOnly and cp.pcmCruise and not cp.openpilotLongitudinalControl
  cp.alternativeExperience = 32
  assert not qualified(cp)
  policy = policy_for(cp)
  assert not policy.intent_supported and not policy.runtime_supported


def complete_frames(packer, cp, tick, *, main, engaged=False, reverse=False, speed_kph=80.0,
           temporary=False, permanent=False, gas=False, brake=False, cancel=False, omit=None, wrong_bus=None):
  dbc = CANDBC(DBC[cp.carFingerprint][Bus.pt])
  pt = {
    "ENGINE_DATA": {"SPEED": speed_kph, "PEDAL_GAS": int(gas)},
    "WHEEL_SPEEDS": {"FL": speed_kph, "FR": speed_kph, "RL": speed_kph, "RR": speed_kph},
    "GEAR": {"GEAR": 2 if reverse else 4},
    "STEER": {"STEER_ANGLE": 0},
    "STEER_TORQUE": {"STEER_TORQUE_SENSOR": 0, "STEER_TORQUE_MOTOR": 0},
    "STEER_RATE": {"STEER_ANGLE_RATE": 0, "LKAS_BLOCK": int(temporary), "CTR": tick % 16},
    "CRZ_CTRL": {"CRZ_AVAILABLE": int(main), "CRZ_ACTIVE": int(engaged)},
    "CRZ_BTNS": {"CTR": tick % 16, "CAN_OFF": int(cancel)},
    "CRZ_EVENTS": {"CRZ_SPEED": speed_kph},
    "PEDALS": {"BRAKE_ON": int(brake), "STANDSTILL": int(speed_kph == 0)},
    "SEATBELT": {"DRIVER_SEATBELT": 1},
    "DOORS": {},
    "BLINK_INFO": {},
    "BSM": {},
  }
  camera = {
    "CAM_LKAS": {"CTR": tick % 16, "ERR_BIT_1": int(permanent), "LKAS_REQUEST": 0},
    "CAM_LANEINFO": {"LANE_LINES": 1},
  }
  result = []
  for bus, sources in ((0, pt), (2, camera)):
    for name, fields in sources.items():
      assert name in dbc.name_to_msg and fields.keys() <= dbc.name_to_msg[name].sigs.keys(), (name, fields)
      if name != omit:
        result.append(packer.make_can_msg(name, (bus + 1) % 3 if name == wrong_bus else bus, fields))
  return result


@pytest.mark.parametrize("loss", ("wire", "camera"))
def test_actual_card_physical_pause_resume_and_source_loss(loss, monkeypatch):
  from opendbc.can import CANPacker
  from openpilot.cereal import messaging
  from openpilot.selfdrive.car import card as card_module
  from openpilot.starpilot.aol.runtime import current_native
  from openpilot.starpilot.aol.wire import SafetyState, encode_safety

  def probe(card, ci, controls, saved):
    now = [1_000_000_000]
    queued = []
    monkeypatch.setattr(card_module.time, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(card_module.time, "clock_gettime_ns", lambda clock: now[0])
    monkeypatch.setattr(card_module.messaging, "drain_sock_raw", lambda *args, **kwargs: list(queued))
    monkeypatch.setattr(card.sm, "update", lambda *args: None)
    packer = CANPacker(DBC[card.CP.carFingerprint][Bus.pt])
    tick = 0

    def step(main=True, cancel=False, missing=False):
      nonlocal tick
      tick += 1
      now[0] += 10_000_000
      rx = complete_frames(packer, card.CP, tick, main=main, cancel=cancel,
                           omit="CAM_LKAS" if missing and loss == "camera" else None)
      packet = messaging.log_from_bytes(card_module.can_list_to_can_capnp(rx)).as_builder()
      packet.logMonoTime = now[0]
      queued[:] = [packet.to_bytes()]
      events = []
      for service, size in (("deviceState", None), ("carControl", None), ("onroadEvents", 0), ("pandaStates", 1)):
        events.append(messaging.new_message(service, size, valid=True, logMonoTime=now[0]))
      events[0].deviceState.started = True
      events[0].deviceState.startedMonoTime = 1_000_000_000
      panda = events[-1].pandaStates[0]
      panda.safetyModel = card.CP.safetyConfigs[0].safetyModel
      panda.safetyParam = card.CP.safetyConfigs[0].safetyParam
      panda.alternativeExperience = card.CP.alternativeExperience
      wire = messaging.new_message("aolSafetyWire", 0, valid=not (missing and loss == "wire"), logMonoTime=now[0])
      active = card.aol_card_intent.allowed_latch and not card.aol_card_intent.pause_lateral
      wire.aolSafetyWire = encode_safety(SafetyState(
        1, True, now[0], now[0] + 30_000_000, int(card.CP.safetyConfigs[0].safetyModel.raw),
        card.CP.safetyConfigs[0].safetyParam, active, False, active, False, "fixture-panda", card.slc_producer_session))
      events.append(wire)
      card.sm.update_msgs(now[0] / 1e9, [event.as_reader() for event in events])
      state, _ = card.state_update()
      assert card.CI is ci and card.CP.to_dict() == controls.CP.to_dict()
      if missing and loss == "wire":
        assert current_native(card.sm, card.CP, now_ns=now[0], axis_session_id=card.slc_producer_session) is None
      return state

    for _ in range(120):
      step(main=False)
    for _ in range(30):
      step()
    assert card.aol_card_intent.allowed_latch
    for _ in range(2):
      step(cancel=True)
    for _ in range(60):
      state = step()
      assert state.canValid and card.aol_card_intent.allowed_latch and card.aol_card_intent.pause_lateral
    assert card._angle_aol_pending_since_ns == 0
    for _ in range(2):
      step(cancel=True)
    for _ in range(30):
      step()
    assert card.aol_card_intent.allowed_latch and not card.aol_card_intent.pause_lateral
    for _ in range(2):
      step(cancel=True)
    for _ in range(30):
      step()
    assert card.aol_card_intent.pause_lateral
    for _ in range(120):
      step(missing=True)
    assert not card.aol_card_intent.allowed_latch
    for _ in range(2):
      step(cancel=True)
    for _ in range(120):
      step()
    assert not card.aol_card_intent.allowed_latch
    for _ in range(20):
      step(main=False)
    for _ in range(30):
      step()
    assert card.aol_card_intent.allowed_latch and not card.aol_card_intent.pause_lateral

  exercise_startup(CAR.MAZDA_CX5_2022, False, False, True, monkeypatch, probe=probe)
