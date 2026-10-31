import gc
import time

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.values import CAR


PROFILES = (
  (CAR.FORD_BRONCO_SPORT_MK1, 0), (CAR.FORD_ESCAPE_MK4, 0), (CAR.FORD_FOCUS_MK4, 0),
  (CAR.FORD_MAVERICK_MK1, 0), (CAR.FORD_EXPLORER_MK6, 0),
  (CAR.FORD_MUSTANG_MACH_E_MK1, 2), (CAR.FORD_ESCAPE_MK4_5, 2),
  (CAR.FORD_EXPEDITION_MK4, 2), (CAR.FORD_F_150_MK14, 2),
  (CAR.FORD_F_150_LIGHTNING_MK1, 2), (CAR.FORD_RANGER_MK2, 2),
  (CAR.FORD_EDGE_MK2, 8), (CAR.FORD_MONDEO_MK5, 10), (CAR.FORD_TRANSIT_MK5, 12),
)


@pytest.mark.parametrize('identity,base_word', PROFILES)
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('topology', ('automatic', 'manual', 'multi-panda', 'manual-multi'))
def test_unmarked_profiles_keep_actual_ordinary_controls(identity, base_word, alpha, topology, monkeypatch):
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.controls.controlsd import Controls
  from openpilot.starpilot.lateral.tests.test_lane_runtime import feed

  monkeypatch.setenv('REPLAY', '1')
  monkeypatch.setenv('AOL_REPLAY_RUNTIME', '0')
  offset = 4 if topology in ('multi-panda', 'manual-multi') else 0
  manual = topology in ('manual', 'manual-multi')
  fp = gen_empty_fingerprint()
  if not manual:
    fp[offset][0x5A] = 8
  fp[offset + 2].update({0x3D6: 8, 0x186: 8})
  cp = CarInterface.get_params(identity, fp, [], alpha, True, False)
  assert cp.alternativeExperience == 0
  assert cp.safetyConfigs[-1].safetyParam == base_word | int(alpha)
  with OpenpilotPrefix():
    saved = Params()
    saved.put_bool('AlwaysOnLateral', False, block=True)
    saved.put('CarParams', cp.to_bytes(), block=True)
    controls = Controls()
    for tick, (enabled, fault) in enumerate(((True, False), (True, True), (False, False))):
      feed(controls, 1_000_000_000 + tick * 10_000_000, tick, active=enabled, enabled=enabled, fault=fault)
      command, _ = controls.state_control()
      assert command.enabled == enabled
      assert command.latActive == (enabled and not fault)
    assert not controls.aol_replay and not controls.ordinary_axis_ack_required
    assert 'aolAxisState' not in controls.sm.services


def run_startup(identity, base_word, alpha, release, topology, monkeypatch):
  from opendbc.car import car_helpers
  from opendbc.car.ford.aol import qualified
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car import card as card_module
  from openpilot.selfdrive.car.card import Car
  from openpilot.selfdrive.controls.controlsd import Controls
  from openpilot.starpilot.aol.vehicle import policy_for
  from openpilot.starpilot.lateral.tests.test_lane_runtime import feed

  monkeypatch.setenv("SIMULATION", "1")
  monkeypatch.setenv("REPLAY", "1")
  monkeypatch.setenv("AOL_REPLAY_RUNTIME", "1")
  offset = 4 if topology == "multi-panda" else 0
  fingerprint = gen_empty_fingerprint()
  if topology != "manual":
    fingerprint[offset][0x5A] = 8
  fingerprint[offset + 2].update({0x3D6: 8, 0x186: 8})
  expected_long = alpha
  initial_word = base_word | int(expected_long)
  expected_word = initial_word | (256 if topology == "manual" else 0)
  initial = CarInterface.get_params(identity, fingerprint, [], expected_long, release, False)
  initial.carVin = "0" * 17
  initial.carFw = []
  admitted = True
  expected = initial.to_dict()
  expected["alternativeExperience"] = 32 if admitted else 0
  expected["safetyConfigs"][-1]["safetyParam"] = expected_word
  assert initial.safetyConfigs[-1].safetyParam == initial_word
  assert initial.openpilotLongitudinalControl == expected_long
  assert not initial.passive and not initial.dashcamOnly
  with OpenpilotPrefix():
    saved = Params()
    for key, value in (("OpenpilotEnabledToggle", True), ("AlwaysOnLateral", True),
                       ("AlphaLongitudinalEnabled", alpha), ("IsReleaseBranch", release),
                       ("FordHumanTurnDetection", False)):
      saved.put_bool(key, value, block=True)
    observed = (identity, fingerprint, initial.carVin, [], initial.fingerprintSource, True)
    subscriber = messaging.sub_sock("carParams", timeout=100, conflate=True)
    card = ci = controls = None
    try:
      with monkeypatch.context() as discovery:
        discovery.setattr(car_helpers, "fingerprint", lambda *args: observed)
        discovery.setattr(card_module, "can_comm_callbacks", lambda *args: (lambda *args: [], lambda frames: None))
        initial_can = messaging.new_message("can", 1)
        discovery.setattr(card_module.messaging, "recv_one_retry", lambda socket: initial_can)
        card = Car()
      ci = card.CI
      assert card.CP.to_dict() == expected
      assert qualified(card.CP, marked_only=True) == admitted
      assert card.aol_qualified == admitted
      policy = policy_for(card.CP)
      assert policy.intent_supported == admitted
      assert policy.normal_runtime_supported == admitted
      assert ci.CP is card.CP
      assert ci.CC is not None
      if admitted:
        assert card.aol_card_intent is not None
        healthy = structs.CarState(canValid=True, canTimeout=False, gearShifter="drive", buttonEvents=[])
        healthy.cruiseState.available = True
        healthy.cruiseState.enabled = False
        card.aol_card_intent.update(healthy.as_reader(), now_ns=1_000_000_000)
        assert not card.aol_card_intent.allowed_latch
      else:
        assert card.aol_card_intent is None
        assert card.CP.alternativeExperience == 0
        assert card.CP.safetyConfigs[-1].safetyParam == expected_word
        if topology == "manual":
          assert card.CP.transmissionType == structs.CarParams.TransmissionType.manual
        else:
          assert len(card.CP.safetyConfigs) == 2
          assert card.CP.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput
      with structs.CarParams.from_bytes(saved.get("CarParams")) as published:
        assert published.to_dict() == expected
      event = None
      for _ in range(10):
        card.car_params_published = False
        card.state_publish(ci.update([]), None)
        event = messaging.recv_one(subscriber)
        if event is not None:
          break
        time.sleep(0.01)
      assert event is not None and event.valid and event.carParams.to_dict() == expected
      controls = Controls()
      assert controls.CP.to_dict() == expected
      assert controls.ordinary_axis_ack_required == admitted
      feed(controls, 1_000_000_000, 0, active=False, enabled=False, can_valid=True, can_timeout=False)
      command, lateral_log = controls.state_control()
      assert not command.enabled and not command.latActive and not command.longActive
      controls.publish(command, lateral_log)
      ci.apply(command.as_reader(), 1_000_000_000)
      if admitted:
        from openpilot.starpilot.aol.wire import SAFETY_SERVICE, PandaSlot, SafetyState, encode_safety

        for tick, (acknowledged, stale) in enumerate(((False, False), (True, False), (True, True)), 1):
          now = 1_000_000_000 + tick * 10_000_000
          feed(controls, now, tick, active=True, enabled=True, can_valid=True, can_timeout=False)
          axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
          axis.aolAxisState.qualified = True
          axis.aolAxisState.sessionId = 'ford-current'
          axis.aolAxisState.observedMonoTime = now
          axis.aolAxisState.validUntilMonoTime = now - 1 if stale else now + 30_000_000
          axis.aolAxisState.desiredLateral = True
          axis.aolAxisState.lateralActive = acknowledged
          axis.aolAxisState.nativeAcknowledged = acknowledged
          native = messaging.new_message(SAFETY_SERVICE, 0, valid=True, logMonoTime=now)
          native.aolSafetyWire = encode_safety(SafetyState(
            1, True, now, now + 200_000_000, int(card.CP.safetyConfigs[-1].safetyModel.raw),
            card.CP.safetyConfigs[-1].safetyParam, acknowledged, False, True, False, 'fixture-panda', 'ford-current', sourcePandaStatesMonoTime=now,
            pandaInventory=tuple(PandaSlot(index, 'fixture-panda' if index == len(card.CP.safetyConfigs) - 1 else 'passive-panda',
              int(config.safetyModel.raw), config.safetyParam, 32, False, False)
              for index, config in enumerate(card.CP.safetyConfigs))))
          controls.sm.update_msgs(now / 1e9, [axis.as_reader(), native.as_reader()])
          active_command, _ = controls.state_control()
          assert active_command.enabled
          assert active_command.latActive == (acknowledged and not stale)
          assert not active_command.longActive
    finally:
      del controls, card, ci, subscriber
      gc.collect()


@pytest.mark.parametrize("identity,word", PROFILES)
@pytest.mark.parametrize("alpha,release", ((False, False), (True, False), (True, True)))
def test_actual_card_aol_publication_and_disabled_controls(identity, word, alpha, release, monkeypatch):
  run_startup(identity, word, alpha, release, "automatic", monkeypatch)


@pytest.mark.parametrize("identity,word", PROFILES)
@pytest.mark.parametrize("topology", ("manual", "multi-panda"))
def test_aol_manual_and_multi_panda_keep_actual_ordinary_startup(identity, word, topology, monkeypatch):
  run_startup(identity, word, False, False, topology, monkeypatch)


@pytest.mark.parametrize("identity,word", PROFILES)
@pytest.mark.parametrize("alpha", (False, True))
def test_aol_qualifier_requires_actual_pcm_cruise_contract(identity, word, alpha):
  from opendbc.car.ford.aol import qualified
  fingerprint = gen_empty_fingerprint()
  fingerprint[0][0x5A] = 8
  fingerprint[2].update({0x3D6: 8, 0x186: 8})
  cp = CarInterface.get_params(identity, fingerprint, [], alpha, False, False)
  assert cp.pcmCruise
  assert cp.safetyConfigs[-1].safetyParam == word | int(alpha)
  assert qualified(cp)
  cp.alternativeExperience = 32
  assert qualified(cp, marked_only=True)
  cp.pcmCruise = False
  assert not qualified(cp)
  assert not qualified(cp, marked_only=True)
