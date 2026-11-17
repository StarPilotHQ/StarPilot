"""Actual automatic Subaru factory/parser/Card/SD/Controls/native/CPP negotiation."""

import json
import time
from typing import TypedDict
from unittest.mock import patch
import pytest
from opendbc.can import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.subaru.values import CAR, DBC
from opendbc.car.subaru.stock_aol import IMPREZA_MAIN_AOL
from opendbc.car.subaru.fingerprints import FW_VERSIONS
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.pandad import can_capnp_to_list
from openpilot.starpilot.car.subaru.tests.caller_helpers import (
  actual_factory,
  firmware_records,
  CompleteCan,
  feed_perception_only,
)
from openpilot.starpilot.car.subaru.tests.selfdrive_step import selfdrive_step
from openpilot.starpilot.car.subaru.tests.pandad_order import PandadOrder

from openpilot.starpilot.car.subaru.tests.native_fixture import (
  subaru_native_pair as subaru_native_pair,
)



class CallerRow(TypedDict):
  tick: int
  buttons: list[tuple[int, str, int, bool]]
  enabled: bool
  native: bool
  long: bool
  target: float
  software: int
  driver: list[tuple[str, bool]]


@pytest.mark.parametrize("aol", [False, True])
def test_software_cruise_actual_stock_callers(aol, subaru_native_pair, tmp_path):
  candidate = CAR.SUBARU_IMPREZA_2020
  fw = firmware_records(candidate, FW_VERSIONS, "subaru")
  rows: list[CallerRow] = []
  with actual_factory(
    candidate,
    gen_empty_fingerprint(),
    fw,
    "0" * 17,
    manual=False,
    aol=aol,
    preferences={"SubaruRedneckCruise": True},
  ) as (
    card,
    params,
    matches,
  ):
    cp = card.CP
    ci = card.CI
    safety = libsafety_py.libsafety
    assert cp.pcmCruise and not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
    assert cp.alternativeExperience == (32 if aol else 0) and not cp.dashcamOnly
    assert cp.safetyConfigs[0].safetyParam == (512 | (IMPREZA_MAIN_AOL if aol else 0))
    sd, controls = SelfdriveD(CP=cp), Controls()
    stream = CompleteCan(ci)
    safety.init_tests()
    safety.set_alternative_experience(cp.alternativeExperience)
    assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
    safety.set_aol_test_heartbeat(False)
    library, bridge = subaru_native_pair
    pandad = PandadOrder(safety, library, bridge)
    previous_controls = {}
    latest_controls = {}
    driven = {}
    driven_new = {}
    receipt = None
    button_receiver = CANParser(DBC[candidate][Bus.pt], [('Cruise_Buttons', 100)], 0)
    base = time.monotonic_ns() + 100_000_000
    try:
      for tick in range(310):
        now = base + tick * 10_000_000 + (tick % 3) * 1_000_000
        cruise = 30 <= tick < 110 or 140 <= tick < 200 or tick >= 220
        brake = 200 <= tick < 220
        reverse = 235 <= tick < 250
        temporary = 265 <= tick < 280
        main = not 285 <= tick < 300
        values = {
          "Steering_Torque": {"Steer_Warning": int(temporary)},
          "Steering_2": {"Steering_Angle": 0},
          "Wheel_Speeds": dict.fromkeys(("FL", "FR", "RL", "RR"), 54),
          "Brake_Status": {"Brake": int(brake)},
          "CruiseControl": {
            "Cruise_On": int(main),
            "Cruise_Activated": int(cruise),
          },
          "ES_Status": {"Cruise_Activated": int(cruise)},
          "ES_DashStatus": {"Cruise_On": int(main), "Cruise_Set_Speed": 60},
          "Transmission": {"Gear": 3 if reverse else 121},
          "Cruise_Buttons": {
            "Resume": int(tick == 50),
            "Signal1": 0x12345,
            "Signal2": 0x12345,
          },
        }
        incoming = stream.frames(values, tick=tick)
        safety.set_timer((now // 1000) & 0xFFFFFFFF)
        for addr, data, bus in incoming:
          safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, data))
        safety.safety_tick()
        button_receiver.update([(now, incoming)])
        if tick >= 10:
          assert button_receiver.can_valid
          assert button_receiver.message_states[0x146].counter_fail <= 1
        panda = messaging.new_message("pandaStates", 1, valid=True, logMonoTime=now)
        panda.pandaStates[0].safetyModel = cp.safetyConfigs[0].safetyModel
        panda.pandaStates[0].safetyParam = cp.safetyConfigs[0].safetyParam
        panda.pandaStates[0].alternativeExperience = cp.alternativeExperience
        panda.pandaStates[0].ignitionLine = True
        panda.pandaStates[0].controlsAllowed = bool(safety.get_controls_allowed())
        panda.pandaStates[0].safetyRxChecksInvalid = not bool(safety.safety_config_valid())
        device = messaging.new_message("deviceState", valid=True, logMonoTime=now)
        device.deviceState.started = True
        device.deviceState.startedMonoTime = 500_000_000
        card_plan = messaging.new_message("longitudinalPlan", valid=True, logMonoTime=now)
        card_plan.longitudinalPlan.aTarget = 1.0 if tick < 150 else -1.0
        peers = [device.as_reader(), card_plan.as_reader()]
        if tick % 10 == 0:
          peers.append(panda.as_reader())
        if "carControl" in latest_controls:
          peers.append(latest_controls["carControl"])
        peers.extend(driven_new[s] for s in ("onroadEvents", "selfdriveState", "aolAxisState") if s in driven_new)
        if receipt is not None and tick % 10 == 1:
          peers.append(receipt.as_reader())
        card.sm.update_msgs((now + 1000) / 1e9, peers)
        published = {}
        real_new = messaging.new_message

        def stamped(service=None, size=None, *, _real_new=real_new, _now=now, **kwargs):
          return _real_new(service, size, **({"logMonoTime": _now} | kwargs))

        def capture(service, msg, _published=published):
          wire = msg if isinstance(msg, bytes) else msg.to_bytes()
          _published[service] = messaging.log_from_bytes(wire)

        with (
          patch.object(card.sm, "update"),
          patch(
            "openpilot.selfdrive.car.card.messaging.drain_sock_raw",
            return_value=[b"can"],
          ),
          patch(
            "openpilot.selfdrive.car.card.can_capnp_to_list",
            return_value=[(now, incoming)],
          ),
          patch("openpilot.selfdrive.car.card.REPLAY", False),
          patch(
            "openpilot.selfdrive.car.card.time.monotonic_ns",
            return_value=now + 2000,
          ),
          patch(
            "openpilot.selfdrive.car.card.time.monotonic",
            return_value=(now + 2000) / 1e9,
          ),
          patch(
            "openpilot.selfdrive.car.card.time.clock_gettime_ns",
            return_value=now + 2000,
          ),
          patch(
            "openpilot.selfdrive.car.card.messaging.new_message",
            side_effect=stamped,
          ),
          patch.object(card.pm, "send", side_effect=capture),
        ):
          card.step()
        if tick % 10 == 0:
          published["pandaStates"] = panda.as_reader()
        cs = published["carState"].carState
        assert cs.brakePressed == brake and cs.gearShifter == (structs.CarState.GearShifter.reverse if reverse else structs.CarState.GearShifter.drive)
        if tick >= 10:
          assert cs.canValid and safety.safety_config_valid()
        # Deliver each genuine10Hz native receipt once; do not refresh receive time/frequency from a cached snapshot.
        new_native = receipt if receipt is not None and tick % 10 == 1 else None
        driven_new = selfdrive_step(
          sd,
          published,
          new_native,
          previous_controls,
          cp,
          safety,
          now,
          calibration="calibrated",
        )
        driven.update(driven_new)
        order = None
        if aol and tick % 10 == 0:
          receipt, order = pandad.step(driven, published, now)
        feed_perception_only(controls, now, tick, enabled=sd.enabled, active=sd.active)
        plan = messaging.new_message("longitudinalPlan", valid=True, logMonoTime=now)
        plan.longitudinalPlan.aTarget = 1.0 if tick < 150 else -1.0
        control_peers = [
          published["carState"],
          published["carOutput"],
          plan.as_reader(),
        ]
        control_peers.extend(driven_new[s] for s in ("selfdriveState", "onroadEvents", "aolAxisState") if s in driven_new)
        if receipt is not None and tick % 10 == 0:
          control_peers.append(receipt.as_reader())
        controls.sm.update_msgs((now + 1000) / 1e9, control_peers)
        previous_controls = {}

        def capture_control(service, msg, _controls=previous_controls):
          wire = msg if isinstance(msg, bytes) else msg.to_bytes()
          _controls[service] = messaging.log_from_bytes(wire)

        with (
          patch(
            "openpilot.selfdrive.controls.controlsd.time.monotonic_ns",
            return_value=now + 2000,
          ),
          patch(
            "openpilot.cereal.messaging.time.monotonic",
            return_value=(now + 2000) / 1e9,
          ),
          patch.object(controls.pm, "send", side_effect=capture_control),
        ):
          command, lateral_log = controls.state_control()
          controls.publish(command, lateral_log)
        latest_controls.update(previous_controls)
        command = previous_controls["carControl"].carControl
        outgoing = (
          can_capnp_to_list(
            [published["sendcan"].as_builder().to_bytes()],
            msgtype="sendcan",
          )[0][1]
          if "sendcan" in published
          else []
        )
        tx = [
          (
            a,
            d.hex(),
            b,
            bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(a, b, d))),
          )
          for a, d, b in outgoing
        ]
        button_receiver.update([(now, [(a, bytes.fromhex(data), bus) for a, data, bus, accepted in tx if accepted])])
        assert button_receiver.message_states[0x146].counter_fail <= 1
        emitted = [(address, data, bus, accepted) for address, data, bus, accepted in tx if address == 0x146]
        for _address, data, bus, accepted in emitted:
          assert accepted and bus == 0, (tick, emitted)
          raw = bytes.fromhex(data)
          assert raw[5] & 0x1C in (8, 16), (tick, emitted)
          assert button_receiver.vl['Cruise_Buttons']['Resume' if raw[5] & 16 else 'Set'] == 1
        rows.append(
          {
            "tick": tick,
            "buttons": emitted,
            "enabled": command.enabled,
            "native": bool(safety.get_controls_allowed()),
            "long": command.longActive,
            "target": cs.vCruise,
            "software": card.CI.CC.software_cruise_command,
            "driver": [(str(event.type), bool(event.pressed)) for event in cs.buttonEvents],
          }
        )
        assert not command.longActive
        if tick < 30 or 110 <= tick < 140 or brake or reverse or temporary or not main:
          assert not emitted, (tick, emitted)
      assert rows[52]["target"] > 60.5, rows[49:54]
      assert any(row["buttons"] and any(bytes.fromhex(message[1])[5] & 16 for message in row["buttons"]) for row in rows[65:105]), rows[45:105]
      assert any(row["buttons"] and any(bytes.fromhex(message[1])[5] & 8 for message in row["buttons"]) for row in rows[165:195]), rows[140:200]
      for row in rows:
        if row["buttons"]:
          assert len(row["buttons"]) == 2
          counters = [bytes.fromhex(message[1])[1] & 15 for message in row["buttons"]]
          assert counters[1] == (counters[0] + 1) % 16
    finally:
      pandad.close()
      (tmp_path / (str(candidate) + "-" + str(aol) + "-actual.json")).write_text(json.dumps(rows, indent=2) + "\n")
