import gc
import time

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR, HondaFlags


STARTUP_CASES = (
  CAR.HONDA_CRV_SA,
  CAR.HONDA_CLARITY,
  CAR.HONDA_ACCORD_9G,
  CAR.ACURA_MDX_3G,
  CAR.ACURA_MDX_3G_MMR,
  CAR.ACURA_TLX_1G,
  CAR.HONDA_FIT_4G,
  CAR.ACURA_INTEGRA,
  CAR.ACURA_ADX,
  CAR.HONDA_NBOX_2G,
  CAR.HONDA_ACCORD,
  CAR.HONDA_CIVIC_BOSCH,
  CAR.HONDA_CIVIC_BOSCH_DIESEL,
  CAR.HONDA_CRV_5G,
  CAR.HONDA_CRV_HYBRID,
  CAR.ACURA_RDX_3G,
  CAR.HONDA_INSIGHT,
  CAR.HONDA_E,
  CAR.HONDA_E_ADVANCE,
  CAR.HONDA_CIVIC,
  CAR.HONDA_CITY_7G,
  CAR.HONDA_ACCORD_11G,
)


def exercise_final_cp_publication_and_disabled_startup(identity, monkeypatch, firmware=(), *, automatic_firmware=False):
  from opendbc.car import car_helpers
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car import card as card_module
  from openpilot.selfdrive.car.card import Car
  from openpilot.selfdrive.controls.controlsd import Controls
  from openpilot.starpilot.lateral.tests.test_lane_runtime import feed

  monkeypatch.setenv("SIMULATION", "1")
  monkeypatch.setenv("REPLAY", "1")
  flags = identity.config.flags
  classic_bosch = flags & HondaFlags.BOSCH and not flags & (HondaFlags.BOSCH_RADARLESS | HondaFlags.BOSCH_CANFD)
  pt_bus = 1 if classic_bosch else 0
  fingerprint = gen_empty_fingerprint()
  from opendbc.can.dbc import DBC as CANDBC
  from opendbc.car import Bus
  from opendbc.car.honda.values import DBC
  messages = CANDBC(DBC[identity][Bus.pt]).name_to_msg
  selected_gear = messages.get("GEARBOX_AUTO") or messages.get("GEARBOX_CVT")
  assert selected_gear is not None
  fingerprint[pt_bus][selected_gear.address] = selected_gear.size
  initial = CarInterface.get_params(identity, fingerprint, list(firmware), False, False, False)
  initial.carVin = "0" * 17
  initial.carFw = list(firmware)
  if automatic_firmware:
    initial.fingerprintSource = structs.CarParams.FingerprintSource.fw
  if firmware and not automatic_firmware:
    assert initial.flags & HondaFlags.EPS_MODIFIED
    assert not initial.dashcamOnly
  expected = initial.to_dict()
  with OpenpilotPrefix():
    saved = Params()
    for key, value in (("OpenpilotEnabledToggle", True), ("AlwaysOnLateral", False), ("AlphaLongitudinalEnabled", False), ("IsReleaseBranch", False)):
      saved.put_bool(key, value, block=True)
    subscriber = messaging.sub_sock("carParams", timeout=100, conflate=True)
    card = ci = controls = None
    try:
      observed = (identity, fingerprint, initial.carVin, list(firmware), initial.fingerprintSource, True)
      with monkeypatch.context() as discovery:
        if automatic_firmware:
          discovery.setenv("FINGERPRINT", "")
          discovery.setenv("SKIP_FW_QUERY", "")
          discovery.setenv("DISABLE_FW_CACHE", "1")
          discovery.setattr(car_helpers, "get_vin", lambda *args: (-1, -1, initial.carVin))
          discovery.setattr(car_helpers, "get_present_ecus", lambda *args: set())

          def can_fingerprint(can_recv, *, vin, camera_cruise_messages):
            assert callable(can_recv) and vin == initial.carVin
            assert camera_cruise_messages == set()
            return None, fingerprint

          discovery.setattr(car_helpers, "can_fingerprint", can_fingerprint)
          discovery.setattr(car_helpers, "get_fw_versions_ordered", lambda *args: list(firmware))
          discovery.setattr(card_module, "obd_callback", lambda *args: lambda enabled: None)
        else:
          discovery.setattr(car_helpers, "fingerprint", lambda *args: observed)
        discovery.setattr(card_module, "can_comm_callbacks", lambda *args: (lambda *args, **kwargs: [], lambda frames: None))
        initial_can = messaging.new_message("can", 1)
        discovery.setattr(card_module.messaging, "recv_one_retry", lambda socket: initial_can)
        card = Car()
      ci = card.CI
      assert card.CP.to_dict() == expected
      assert ci.CP is card.CP and ci.CC is not None
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
      feed(controls, 1_000_000_000, 0, active=False, enabled=False, can_valid=False, can_timeout=True)
      command, lateral_log = controls.state_control()
      assert not command.enabled and not command.latActive and not command.longActive
      controls.publish(command, lateral_log)
      _, outgoing = ci.apply(command.as_reader(), 1_000_000_000)
      if automatic_firmware:
        from opendbc.safety.tests.libsafety import libsafety_py
        safety = libsafety_py.libsafety
        safety.init_tests()
        safety.set_alternative_experience(card.CP.alternativeExperience)
        try:
          assert safety.set_safety_hooks(card.CP.safetyConfigs[0].safetyModel.raw,
                                         card.CP.safetyConfigs[0].safetyParam) == 0
          assert not safety.get_controls_allowed()
          steering = ci.CC.packer.dbc.name_to_msg["STEERING_CONTROL"].address
          assert any(address == steering and bus == ci.CC.CAN.lkas for address, _, bus in outgoing)
          for address, data, bus in outgoing:
            assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data))
        finally:
          safety.set_alternative_experience(0)
    finally:
      del controls, card, ci, subscriber
      gc.collect()


@pytest.mark.parametrize("identity", STARTUP_CASES)
def test_final_cp_publication_and_disabled_startup(identity, monkeypatch):
  exercise_final_cp_publication_and_disabled_startup(identity, monkeypatch)


@pytest.mark.parametrize("identity,version", (
  (CAR.HONDA_CLARITY, b"39990-TRW,A020\x00\x00"),
  (CAR.HONDA_CIVIC, b"39990-TBA,A030\x00\x00"),
  (CAR.HONDA_ACCORD, b"39990-TVA,A150\x00\x00"),
  (CAR.HONDA_CIVIC_BOSCH, b"39990-TGG,A020\x00\x00"),
  (CAR.HONDA_CIVIC_BOSCH, b"39990-TGG,A120\x00\x00"),
  (CAR.HONDA_CRV_5G, b"39990-TLA,A040\x00\x00"),
))
def test_known_modified_eps_final_cp_publication_and_disabled_startup(identity, version, monkeypatch):
  firmware = (structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps, address=0x18DA30F1,
                                     subAddress=0, fwVersion=version, brand="honda"),)
  exercise_final_cp_publication_and_disabled_startup(identity, monkeypatch, firmware)


@pytest.mark.parametrize("identity", (CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CIVIC_2022, CAR.HONDA_CIVIC))
def test_automatic_fw_final_cp_and_disabled_native_handoff(identity, monkeypatch):
  """Acquisition is controlled; real FW matching, Card factory and native TX run."""
  from opendbc.car.fw_versions import match_fw_to_car
  from opendbc.car.honda.fingerprints import FW_VERSIONS

  firmware = [structs.CarParams.CarFw(ecu=ecu, address=address, subAddress=sub or 0,
                                         fwVersion=versions[0], brand="honda")
              for (ecu, address, sub), versions in FW_VERSIONS[identity].items()]
  exact, matches = match_fw_to_car(firmware, "0" * 17, log=False)
  assert exact and matches == {identity}
  exercise_final_cp_publication_and_disabled_startup(identity, monkeypatch, firmware,
                                                    automatic_firmware=True)
