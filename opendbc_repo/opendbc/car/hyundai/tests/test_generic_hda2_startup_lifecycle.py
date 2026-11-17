from types import SimpleNamespace
from unittest.mock import patch
import os
import time
import threading

import pytest

from opendbc.can import CANPacker
from opendbc.car import car_helpers, gen_empty_fingerprint, make_tester_present_msg, structs
from opendbc.car.hyundai.ecu_startup import Outcome, stock_copy
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.ioniq6_handoff import TimestampedCanPacket
from opendbc.car.hyundai.generic_hda2_startup import GenericHDA2Startup, qualified
from opendbc.car.hyundai.tests.test_generic_hda2_startup import FUEL_CASES
from opendbc.car.hyundai.values import CAR
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module


def firmware():
  # Structural ADAS discovery only; no invented on-vehicle firmware/version.
  return [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.adas, address=0x730, bus=1)]


def fingerprint(hybrid=False, alternate=False, gear=0x130):
  fp = gen_empty_fingerprint()
  fp[1].update({0x1CF: 8, gear: 32, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1A0: 32})
  fp[2].update({0x110: 32, 0x362: 32} if alternate else {0x50: 16, 0x2A4: 24})
  if hybrid:
    fp[1][0xFA] = 8
  return fp


@pytest.mark.parametrize("identity,hybrid", FUEL_CASES)
@pytest.mark.parametrize("alternate", [False, True])
@pytest.mark.parametrize("gear", [0x130, 0x40, 0x70])
def test_exact_factory_stock_copy_and_owner_preserve_all_params(identity, hybrid, alternate, gear):
  fp = fingerprint(hybrid, alternate, gear)
  cp = CarInterface.get_params(identity, fp, firmware(), True, False, False)
  stock = CarInterface.get_params(identity, fp, firmware(), False, False, False)
  assert qualified(cp) and stock_copy(cp).to_dict() == stock.to_dict()
  before = cp.to_dict()
  owner = CarInterface.startup_owner(cp, (list, list), requested=True)
  assert isinstance(owner, GenericHDA2Startup) and owner.cp.to_dict() == before
  assert owner.stock_cp.to_dict() == stock.to_dict() and cp.to_dict() == before
  assert CarInterface.startup_owner(cp, (list, list), requested=False) is None


class Keeper:
  def __init__(self, send, **kwargs):
    self.send, self.abort_reason, self.running = send, None, False

  def start(self):
    self.running = True

  def stop(self):
    self.running = False


def physical_frames(ci, tick):
  frames = []
  for parser in ci.can_parsers.values():
    packer = CANPacker(parser.dbc.name)
    for address in parser.addresses:
      message = parser.dbc.addr_to_msg[address]
      frequency = parser.message_states[address].frequency
      period = max(1, round(100 / frequency)) if frequency > 0 else 1
      if tick % period:
        continue
      name = message.name
      values = {}
      if "COUNTER" in message.sigs:
        values["COUNTER"] = tick // period % (1 << message.sigs["COUNTER"].size)
      if name == ci.CS.gear_msg_canfd:
        values["GEAR"] = next(code for code, label in ci.CS.shifter_values.items() if label == "D")
      if name == "DOORS_SEATBELTS":
        values["DRIVER_SEATBELT"] = 1
      if name == "TCS":
        values.update(ACCEnable=0, ACC_REQ=0, DriverBraking=0)
      if name == "WHEEL_SPEEDS":
        values.update(dict.fromkeys(("WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal"), 36))
      frames.append(packer.make_can_msg(name, parser.bus, values))
  return frames


def actual_card_lifecycle(identity, hybrid, alternate, gear, mode, aol):
  native = libsafety_py.libsafety
  now = [10_000_000_000]
  owners, commands, receive_ticks = [], [], [0]
  card = None
  with OpenpilotPrefix(), patch.dict(os.environ, {"SIMULATION": "0", "AOL_REPLAY_RUNTIME": "1"}):
    saved = Params()
    for key, value in [
      ("OpenpilotEnabledToggle", True),
      ("AlphaLongitudinalEnabled", True),
      ("IsReleaseBranch", False),
      ("IsOffroad", False),
      ("AlwaysOnLateral", aol),
    ]:
      saved.put_bool(key, value, block=True)
    saved.put("LKASButtonControl", 9, block=True)
    saved.put("MainCruiseButtonControl", 0, block=True)
    keys = ["AlphaLongitudinalEnabled", "AlwaysOnLateral", "LKASButtonControl", "MainCruiseButtonControl"]
    settings = {key: saved.get(key) for key in keys}
    fp = fingerprint(hybrid, alternate, gear)
    desired = CarInterface.get_params(identity, fp, firmware(), True, False, False)
    expected = CarInterface.get_params(identity, fp, firmware(), mode == "sent", False, False)
    expected.carVin, expected.carFw, expected.fuzzyFingerprint = "0" * 17, firmware(), False
    assert desired.openpilotLongitudinalControl
    native.set_alternative_experience(0)
    assert native.set_safety_hooks(structs.CarParams.SafetyModel.elm327, 1) == 0
    native.init_tests()
    factory = CarInterface.startup_owner

    def send(frames):
      assert saved.get("CarParams") is None and not saved.get_bool("ControlsReady")
      assert native.get_current_safety_mode() == int(structs.CarParams.SafetyModel.elm327)
      for address, data, bus in frames:
        assert native.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data))

    def receive(*args, **kwargs):
      if not owners or owners[0].ci is None:
        return []
      owner = owners[0]
      assert owner.restored and not owner.published and saved.get("CarParams") is None
      receive_ticks[0] += 1
      now[0] += 10_000_000
      return [TimestampedCanPacket(physical_frames(owner.ci, receive_ticks[0]), now[0])]

    def query(sender, receiver, bus, addresses, requests, responses):
      command = requests[0]
      commands.append(command)
      assert bus == 1 and addresses == [(0x730, None)]

      def data(timeout):
        sender([(0x730, bytes([len(command)]) + command + bytes(7 - len(command)), 1)])
        if command == b"\x28\x83\x01":
          return {} if mode == "sent" else {(0x730, None): b"\x7f\x28\x22"}
        return {(0x730, None): b""}

      return SimpleNamespace(get_data=data)

    def owner_factory(cp, callbacks, *, requested):
      owner = factory(cp, callbacks, requested=requested)
      assert isinstance(owner, GenericHDA2Startup)
      owner._query, owner.keeper_factory = query, Keeper
      owners.append(owner)
      return owner

    def panda_update(sm, *args, **kwargs):
      if "pandaStates" in sm.services:
        message = messaging.new_message("pandaStates", 1, valid=True, logMonoTime=now[0])
        panda = message.pandaStates[0]
        panda.safetyModel, panda.safetyParam = native.get_current_safety_mode(), native.get_current_safety_param()
        panda.alternativeExperience, panda.controlsAllowed, panda.safetyRxChecksInvalid = 0, False, False
        panda.ignitionLine = True
        sm.update_msgs(now[0] / 1e9, [message.as_reader()])

    def sleep(seconds):
      now[0] += round(seconds * 1e9)

    initial = messaging.new_message("can", 1, valid=True, logMonoTime=now[0])
    initial.can[0].address, initial.can[0].src, initial.can[0].dat = 0x1CF, 1, bytes(8)
    try:
      with (
        patch.object(car_helpers, "fingerprint", return_value=(identity, fp, "0" * 17, firmware(), desired.fingerprintSource, True)),
        patch.object(CarInterface, "startup_owner", side_effect=owner_factory),
        patch.object(card_module, "can_comm_callbacks", return_value=(receive, send)),
        patch.object(card_module.messaging, "recv_one_retry", return_value=initial.as_reader()),
        patch.object(messaging.SubMaster, "update", panda_update),
        patch("time.clock_gettime_ns", side_effect=lambda clock: now[0]),
        patch("time.monotonic_ns", side_effect=lambda: now[0]),
        patch("time.monotonic", side_effect=lambda: now[0] / 1e9),
        patch("time.sleep", side_effect=sleep),
      ):
        card = card_module.Car()
      owner = owners[0]
      assert owner.published and owner.ready and owner.prepared_for(card.CP)
      assert {key: saved.get(key) for key in keys} == settings and not saved.get_bool("ControlsReady")
      if mode == "rejected" and aol:
        expected.safetyConfigs[0].safetyParam |= 0x800
      elif mode == "sent" and aol and identity == CAR.KIA_SPORTAGE_5TH_GEN:
        expected.safetyConfigs[0].safetyParam |= 0x800
        expected.alternativeExperience = 32
      assert card.CP.to_dict() == expected.to_dict()
      if mode == "rejected":
        assert owner.restored and owner.outcome is Outcome.STOCK_RESTORED and owner.keeper is None
        assert commands == [b"\x10\x03", b"\x28\x83\x01", b"\x10\x03", b"\x28\x00\x01", b"\x10\x01"]
        assert receive_ticks[0] > 0
      else:
        assert commands == [b"\x10\x03", b"\x28\x83\x01"] and owner.keeper.running
        assert owner.outcome is Outcome.SENT_UNCONFIRMED and not owner.restored
      cp = card.CP
      config = cp.safetyConfigs[0]
      native.set_alternative_experience(cp.alternativeExperience)
      assert native.set_safety_hooks(int(config.safetyModel.raw), int(config.safetyParam)) == 0
      native.init_tests()
      native.set_alternative_experience(cp.alternativeExperience)
      for tick in range(1, 121):
        now[0] += 10_000_000
        frames = physical_frames(card.CI, tick)
        state = card.CI.update([(now[0], frames)])
        native.set_timer(tick * 10_000)
        for address, data, bus in frames:
          assert native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
        if tick % 100 == 0:
          native.safety_tick()
      assert state.canValid and not state.canTimeout and native.safety_config_valid()
      assert owner.sources_current(card.CI, now[0])
      if mode == "sent":
        assert not owner.before_control(configured=False, sources_current=True, control_current=True)
        assert not owner.before_control(configured=True, sources_current=False, control_current=True)
        assert not owner.before_control(configured=True, sources_current=True, control_current=False)
        assert owner.before_control(configured=True, sources_current=True, control_current=True)
        assert owner.keeper.running and not owner.handed_off
        card.CI.CC.frame = 0
        _, frames = card.CI.apply(structs.CarControl().as_reader(), now[0])
        tester = make_tester_present_msg(0x730, 1, suppress_response=True)
        assert tester in frames
        for address, data, bus in frames:
          assert native.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)), hex(address)
        card.publish_sendcan(frames, valid=False)
        assert owner.keeper.running and not owner.handed_off
        command = messaging.new_message("carControl", valid=True, logMonoTime=now[0])
        card.sm.update_msgs(now[0] / 1e9, [command.as_reader()])
        card.CI.CC.frame = 100
        published = []
        original_send = card.pm.send

        def native_publication(service, encoded):
          assert service == "sendcan"
          event = messaging.log_from_bytes(encoded)
          if event.valid:
            for frame in event.sendcan:
              assert native.safety_tx_hook(libsafety_py.make_CANPacket(frame.address, frame.src, bytes(frame.dat)))
          published.append(event.valid)
          original_send(service, encoded)

        with (
          patch.object(card.pm, "send", side_effect=native_publication),
          patch("time.clock_gettime_ns", side_effect=lambda clock: now[0]),
          patch("time.monotonic_ns", side_effect=lambda: now[0]),
          patch("time.monotonic", side_effect=lambda: now[0] / 1e9),
        ):
          card.controls_update(state, command.carControl.as_reader())
          ready_deadline = time.perf_counter() + 1.0
          while not saved.get_bool("ControlsReady") and time.perf_counter() < ready_deadline:
            threading.Event().wait(0.001)
          assert card.ci_initialized and saved.get_bool("ControlsReady")
          assert owner.keeper.running and not owner.handed_off
          assert card.CI.CC.frame == 100
          now[0] += 10_000_000
          panda_event = messaging.new_message("pandaStates", 1, valid=True, logMonoTime=now[0])
          panda = panda_event.pandaStates[0]
          panda.safetyModel, panda.safetyParam = native.get_current_safety_mode(), native.get_current_safety_param()
          panda.alternativeExperience = cp.alternativeExperience
          panda.controlsAllowed = native.get_controls_allowed()
          panda.safetyRxChecksInvalid = not native.safety_config_valid()
          panda.ignitionLine = True
          card.sm.update_msgs(now[0] / 1e9, [command.as_reader(), panda_event.as_reader()])
          card.controls_update(state, command.carControl.as_reader())
        assert owner.handed_off and not owner.keeper.running
        assert card.CI.CC.frame == 101 and published == [False, True]
      assert not native.get_controls_allowed() and native.aol_get_permission_mask() == 0
    finally:
      if card is not None:
        card.vehicle_startup.close()


CALLER_CASES = tuple((identity, hybrid, alternate, 0x130) for identity, hybrid in FUEL_CASES for alternate in (False, True))
GEAR_SOURCE_FAMILIES = (
  (CAR.KIA_SPORTAGE_5TH_GEN, False),
  (CAR.KIA_SPORTAGE_5TH_GEN, True),
  (CAR.HYUNDAI_STARIA_4TH_GEN, False),
  (CAR.KIA_CARNIVAL_2025, False),
  (CAR.KIA_CARNIVAL_HEV_4TH_GEN, True),
  (CAR.KIA_NIRO_EV_2ND_GEN, False),
  (CAR.HYUNDAI_IONIQ_5_N, False),
)
CALLER_CASES += tuple((identity, hybrid, alternate, gear) for identity, hybrid in GEAR_SOURCE_FAMILIES
                      for alternate in (False, True) for gear in (0x40, 0x70))


@pytest.mark.parametrize("identity,hybrid,alternate,gear", CALLER_CASES)
@pytest.mark.parametrize("aol", [False, True])
def test_actual_card_rejected_generic_hda2_restores_stock(identity, hybrid, alternate, gear, aol):
  actual_card_lifecycle(identity, hybrid, alternate, gear, 'rejected', aol)


@pytest.mark.parametrize("identity,hybrid,alternate,gear", CALLER_CASES)
@pytest.mark.parametrize("aol", [False, True])
def test_actual_card_sent_generic_hda2_commits_controller_publisher(identity, hybrid, alternate, gear, aol):
  actual_card_lifecycle(identity, hybrid, alternate, gear, 'sent', aol)
