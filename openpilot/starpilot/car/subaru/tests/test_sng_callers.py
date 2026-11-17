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
  throttle: float
  speed: float
  driver: list[tuple[str, bool]]
  fault: object
  state: str
  events: list[str]
  axis: dict[str, object]
  ordinary_warning: bool


@pytest.mark.parametrize("aol", [False, True])
@pytest.mark.parametrize('candidate,software', [(CAR.SUBARU_ASCENT, False), (CAR.SUBARU_IMPREZA, False),
                                                   (CAR.SUBARU_IMPREZA_2020, False), (CAR.SUBARU_FORESTER, False),
                                                   (CAR.SUBARU_IMPREZA_2020, True)])
def test_sng_actual_stock_callers(candidate, software, aol, subaru_native_pair, tmp_path):
  fw = firmware_records(candidate, FW_VERSIONS, "subaru")
  rows: list[CallerRow] = []
  with actual_factory(
    candidate,
    gen_empty_fingerprint(),
    fw,
    "0" * 17,
    manual=False,
    aol=aol,
    preferences={"SubaruSNG": True, "SubaruRedneckCruise": software},
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
    expected_word = (520 if software else 8) | (IMPREZA_MAIN_AOL if aol and candidate == CAR.SUBARU_IMPREZA_2020 else 0)
    assert cp.safetyConfigs[0].safetyParam == expected_word and cp.autoResumeSng
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
    receiver = CANParser(DBC[candidate][Bus.pt], [('Throttle', 100), ('Brake_Pedal', 50)], 2)
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
          "Wheel_Speeds": dict.fromkeys(("FL", "FR", "RL", "RR"), 54 if tick < 50 else 0),
          "Throttle": {"Signal1": 9, "Engine_RPM": 1234, "Neutral": 1, "Signal3": 7, "Off_Accel": 3},
          "Brake_Pedal": {"Brake_Pedal": 512 if brake else 0, "Signal1": 7, "Signal2": 31, "Signal3": 1, "Signal4": 0xABCD},
          "ES_Distance": {"Car_Follow": int(not 125 <= tick < 135), "Close_Distance": 4.0 if tick < 80 else 4.02},
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
        incoming = stream.frames(values, tick=tick, omit=((Bus.pt, 'Brake_Pedal'),) if tick % 2 else ())
        # These physical throttle bits have no current DBC signal. Preserve them through the actual parser/caller/native path.
        for index, (address, data, bus) in enumerate(incoming):
          if address == 0x40 and bus == 0:
            raw = bytearray(data)
            raw[3] |= 0x60
            raw[0] = (0x40 + sum(raw[1:])) & 255
            incoming[index] = (address, bytes(raw), bus)
        safety.set_timer((now // 1000) & 0xFFFFFFFF)
        for addr, data, bus in incoming:
          safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, data))
        safety.safety_tick()
        for address, data, bus in incoming:
          if address in (0x40, 0x139) and bus == 0 and safety.safety_fwd_hook(bus, address) == 2:
            receiver.update([(now, [(address, data, 2)])])
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
        card_plan.longitudinalPlan.hasLead = not 125 <= tick < 135
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
        plan.longitudinalPlan.hasLead = not 125 <= tick < 135
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
        emitted = [(address, data, bus, accepted) for address, data, bus, accepted in tx if address in (0x40, 0x139)]
        for address, data, bus, accepted in emitted:
          assert accepted and bus == 2, (tick, emitted)
          physical = next(raw for addr, raw, incoming_bus in incoming if addr == address and incoming_bus == 0)
          raw = bytes.fromhex(data)
          assert raw[1] & 15 == ((physical[1] + 1) & 15)
          if address == 0x40:
            assert raw[2:4] == physical[2:4] and raw[5:] == physical[5:]
          else:
            assert raw[4:] == physical[4:]
          receiver.update([(now + 1000, [(address, raw, bus)])])
        if tick >= 15:
          assert receiver.can_valid
          assert all(receiver.message_states[addr].counter_fail <= 1 for addr in (0x40, 0x139))
        rows.append(
          {
            "tick": tick,
            "buttons": emitted,
            "enabled": command.enabled,
            "native": bool(safety.get_controls_allowed()),
            "long": command.longActive,
            "target": cs.vCruise,
            "throttle": receiver.vl["Throttle"]["Throttle_Pedal"],
            "speed": receiver.vl["Brake_Pedal"]["Speed"],
            "driver": [(str(event.type), bool(event.pressed)) for event in cs.buttonEvents],
            "fault": getattr(sd, 'aol_fault_reason', None),
            "state": str(sd.state_machine.state),
            "events": [str(event.name) for event in sd.events.to_msg()],
            "axis": driven['aolAxisState'].aolAxisState.to_dict() if 'aolAxisState' in driven else {},
            "ordinary_warning": bool(getattr(sd, 'aol_availability_ordinary_warning', False)),
          }
        )
        assert not command.longActive
        if tick >= 50:
          assert not command.latActive
          assert not safety.aol_get_permission_mask() if aol else True
        if tick < 50 or 110 <= tick < 140 or brake or reverse or temporary or not main:
          assert not any(address == 0x40 and bytes.fromhex(data)[4] == 5 for address, data, _bus, _accepted in emitted), (tick, emitted)
      assert any(row['throttle'] == 5 for row in rows[80:100]), rows[75:100]
      assert any(row['speed'] > 0 for row in rows[100:110]), rows[98:110]
      assert all(not row['long'] for row in rows)
    finally:
      pandad.close()
      (tmp_path / (str(candidate) + "-" + str(aol) + "-actual.json")).write_text(json.dumps(rows, indent=2) + "\n")
