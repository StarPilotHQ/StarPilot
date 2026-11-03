"""Keep factory SCC until an inactive producer is current; never bypass native RX."""
import os
from unittest.mock import patch

import pytest

from opendbc.car import Bus, structs
from opendbc.car.hyundai.ecu_startup import Outcome
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.radar_interface import RadarInterface
from opendbc.car.hyundai.tests.test_torque_ev_startup import Keeper
from opendbc.car.hyundai.tests.test_generic_hda2_startup_lifecycle import physical_frames as source_frames
from opendbc.car.hyundai.tests.test_ccnc_six import params
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.canfd_stock_aol import qualified_long
from opendbc.car.hyundai.torque_ev_scc import eligible as ev_scc_eligible
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.card import Car
from openpilot.starpilot.vehicle_startup import VehicleStartupOwner


@pytest.mark.parametrize("scenario", (
  "healthy_suppressed", "healthy_positive", "stale_control", "active_control", "missing_source",
  "native_mode", "cruise_fault", "stock_engaged", "disable_negative", "disable_malformed", "restore_failure", "publisher_failure"))
def test_actual_card_defers_carnival_until_initialized_and_then_native_rx(scenario):
  stock = params(CAR.KIA_CARNIVAL_2025, hda2=True, alt_buttons=True, alpha=True, adas=True, alt_lka=True)
  assert stock.safetyConfigs[0].safetyParam == 0x20B4 and qualified_long(stock) and not ev_scc_eligible(stock)
  native = libsafety_py.libsafety
  assert native.set_safety_hooks(int(structs.CarParams.SafetyModel.elm327), 1) == 0
  native.init_tests()
  now = [100_000_000_000]
  requests, emitted = [], []
  muted = [False]

  def send(frames):
    for address, data, bus in frames:
      assert native.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data))
    emitted.extend(frames)
    if scenario == "publisher_failure" and any(data[:4] == b"\x03\x28\x83\x01" for _, data, _ in frames):
      muted[0] = True
      raise OSError("diagnostic publisher failed after possible ECU delivery")

  def query(sender, receiver, bus, addresses, commands, responses):
    command = commands[0]
    requests.append((now[0], command))

    class Query:
      def get_data(self, timeout):
        sender([(0x730, bytes([len(command)]) + command + bytes(7 - len(command)), bus)])
        if command == b"\x28\x83\x01":
          muted[0] = True
          if scenario in ("disable_negative", "restore_failure"):
            return {(0x730, None): b"\x7f\x28\x22"}
          if scenario == "disable_malformed":
            return {(0x730, None): b"\x68"}
          return {(0x730, None): b"\x68\x03"} if scenario == "healthy_positive" else {}
        if command == b"\x28\x00\x01":
          if scenario == "restore_failure":
            return {}
          muted[0] = False
        return {(0x730, None): b""}
    return Query()

  def sleep(seconds):
    now[0] += round(seconds * 1e9)

  def update_sm(host, command, stale=False):
    message = messaging.new_message("carControl", valid=True, logMonoTime=now[0] - (151_000_000 if stale else 0))
    message.carControl = command
    panda = messaging.new_message("pandaStates", 1, valid=True, logMonoTime=now[0])
    state = panda.pandaStates[0]
    state.safetyModel, state.safetyParam = native.get_current_safety_mode(), native.get_current_safety_param()
    state.alternativeExperience = host.CP.alternativeExperience
    state.ignitionLine = True
    state.safetyRxChecksInvalid = native.safety_test_toyota_rx_summary_invalid()
    host.sm.update_msgs(now[0] / 1e9, [message.as_reader(), panda.as_reader()])

  with OpenpilotPrefix(), patch.dict(os.environ, {"SIMULATION": "0", "AOL_REPLAY_RUNTIME": "1"}), \
       patch("time.clock_gettime_ns", side_effect=lambda clock: now[0]), \
       patch("time.monotonic_ns", side_effect=lambda: now[0]), \
       patch("time.monotonic", side_effect=lambda: now[0] / 1e9), \
       patch("time.sleep", side_effect=sleep):
    saved = Params()
    for key in ("OpenpilotEnabledToggle", "AlwaysOnLateral", "AlphaLongitudinalEnabled"):
      saved.put_bool(key, True, block=True)
    holder = VehicleStartupOwner()
    prepared = holder.prepare(stock, CarInterface, (list, send), requested=True,
                              admission=lambda: True,
                              defer_until_controls=True)
    startup = holder.owner
    assert startup is not None
    startup.clock, startup.keeper_factory, startup._query = lambda: now[0] / 1e9, Keeper, query
    try:
      assert startup.outcome is Outcome.PENDING and not requests and not emitted
      assert not startup.before_control(configured=True, sources_current=True, control_current=True)
      card = Car(CI=CarInterface(prepared), RI=RadarInterface(prepared), startup_owner=startup)
      startup.admission = card.startup_diagnostic_admission
      card.params.put_bool("IsOffroad", False, block=True)
      original_cp = card.CP.to_dict()
      assert card.CP.safetyConfigs[0].safetyParam == 0x28B4 and card.CP.alternativeExperience == 32
      assert not ev_scc_eligible(card.CP)
      # Explicit synthetic delayed producer: stock has not been told to stop.
      for tick in range(1, 701):
        now[0] += 10_000_000
        state = card.CI.update([(now[0], [f for f in source_frames(card.CI, tick) if f[0] not in (0x1A0, 0x1CF, 0x160)])])
      assert state.canValid and not state.canTimeout and not state.accFaulted
      assert not requests and not muted[0] and startup.keeper is None
      command = structs.CarControl(enabled=scenario == "active_control", latActive=scenario == "active_control").as_reader()
      if scenario == "missing_source":
        parser = card.CI.can_parsers[Bus.pt]
        parser.message_states[parser.dbc.name_to_msg["TCS"].address].timestamps.clear()
      if scenario == "cruise_fault":
        state.accFaulted = True
      if scenario == "stock_engaged":
        state.cruiseState.enabled = True
      config = card.CP.safetyConfigs[-1]
      if scenario == "native_mode":
        native.set_alternative_experience(card.CP.alternativeExperience)
        assert native.set_safety_hooks(int(config.safetyModel.raw), int(config.safetyParam)) == 0
      update_sm(card, command, stale=scenario == "stale_control")
      card.controls_update(state, command, initialize_only=True)
      assert not requests and not muted[0] and not card.ci_initialized
      if scenario in ("disable_negative", "disable_malformed", "restore_failure", "publisher_failure"):
        with pytest.raises(RuntimeError, match="pending"):
          card.controls_update(state, command)
        assert startup.outcome is Outcome.ABORT_UNCERTAIN and not card.ci_initialized
        assert card.CP.to_dict() == original_cp and not startup.handed_off
        assert muted[0] == (scenario == "restore_failure")
        assert startup.restored == (scenario != "restore_failure")
        return
      card.controls_update(state, command)
      if not scenario.startswith("healthy"):
        assert not requests and not muted[0] and not card.ci_initialized
        assert startup.outcome is Outcome.PENDING and card.CP.to_dict() == original_cp
        return
      assert [command for _, command in requests] == [b"\x10\x03", b"\x28\x83\x01"]
      assert muted[0] and card.ci_initialized and startup.outcome is Outcome.SENT_UNCONFIRMED
      assert startup.keeper.started and not startup.handed_off
      # No SCC while native is diagnostic, mismatched, or awaiting RX health.
      emitted.clear()
      card.controls_update(state, command)
      assert not emitted and not startup.handed_off
      native.set_alternative_experience(card.CP.alternativeExperience)
      assert native.set_safety_hooks(int(config.safetyModel.raw), int(config.safetyParam)) == 0
      native.init_tests()
      update_sm(card, command)
      card.controls_update(state, command)
      assert not emitted and not startup.handed_off
      for tick in range(701, 811):
        now[0] += 10_000_000
        frames = [f for f in source_frames(card.CI, tick) if f[0] not in (0x1A0, 0x1CF, 0x160)]
        state = card.CI.update([(now[0], frames)])
        native.set_timer(tick * 10_000)
        for address, data, bus in frames:
          native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
        if tick % 100 == 0:
          native.safety_tick()
      assert native.safety_config_valid()
      update_sm(card, command)
      captured = []

      def publish(frames, valid=True):
        if valid:
          send(frames)
          card.vehicle_startup.sent(frames, valid=valid)
          captured.extend(frames)

      with patch.object(card, "publish_sendcan", side_effect=publish):
        card.controls_update(state, command)
      assert any(address == 0x1A0 and bus == 1 for address, _, bus in captured)
      decoder = card.CI.can_parsers[Bus.pt]
      decoder.update((now[0], captured))
      scc = decoder.vl['SCC_CONTROL']
      assert scc['ACCMode'] == 0 and scc['aReqRaw'] == 0 and scc['aReqValue'] == 0
      assert startup.handed_off and not startup.keeper.started
      assert not native.get_controls_allowed() and native.aol_get_permission_mask() == 0
      assert card.CP.to_dict() == original_cp
    finally:
      holder.close()
