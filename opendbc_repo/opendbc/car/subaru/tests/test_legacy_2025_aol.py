import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.subaru import subarucan
from opendbc.car.subaru.carcontroller import CarController
from opendbc.car.subaru.carstate import CarState
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.tests.test_ascent_angle import setup_controller, step
from opendbc.car.subaru.values import CAR, CanBus, DBC
from opendbc.safety.tests import test_subaru_gen2_angle_pair as pair


def _step_to_hud(controller, command, state):
  while controller.frame % 10 != 0:
    controller.update(command.as_reader(), state, 1_000_000_000 + controller.frame * 10_000_000)
  _, sends = controller.update(command.as_reader(), state, 1_000_000_000 + controller.frame * 10_000_000)
  hud = next(frame for frame in sends if frame[0] == 0x322)
  parser = CANParser(DBC[controller.CP.carFingerprint][Bus.pt], [('ES_LKAS_State', 0)], hud[2])
  parser.update([(1, [hud])])
  return parser.vl['ES_LKAS_State']


class TestLegacy2025Aol(unittest.TestCase):
  def test_lkas_dash_state_toggle_emits_button_event_and_toggles_aol(self):
    cp = CarInterface.get_params(CAR.SUBARU_LEGACY_2025, {0: {}, 1: {}, 2: {}}, [], False, False, False)
    _, frames = pair.TestSubaruGen2AnglePair.sources(CAR.SUBARU_LEGACY_2025)
    packer = CANPacker(DBC[CAR.SUBARU_LEGACY_2025][Bus.pt])
    state = CarState(cp)
    self.assertTrue(state.aol_enabled)
    parsers = state.get_can_parsers(cp)
    state.update(parsers)
    for parser in parsers.values():
      parser.update([(1_000_000_000, frames)])
    ret = state.update(parsers)
    self.assertTrue(ret.cruiseState.available)
    self.assertFalse(state.lateral_off)
    self.assertEqual(len(ret.buttonEvents), 0)

    # Flip LKAS_Dash_State (0 -> 1) on bus 2: toggles AOL off once and holds Type.lkas press/release for 5 frames.
    lkas_on = packer.make_can_msg('ES_LKAS_State', CanBus.camera, {'LKAS_Dash_State': 1})
    parsers[Bus.cam].update([(1_020_000_000, [lkas_on])])
    ret = state.update(parsers)
    self.assertTrue(state.lateral_off)
    lkas_events = [be for be in ret.buttonEvents if be.type == structs.CarState.ButtonEvent.Type.lkas]
    self.assertEqual([be.pressed for be in lkas_events], [True, False])
    for idx in range(4):
      ret = state.update(parsers)
      self.assertTrue(state.lateral_off)
      lkas_events = [be for be in ret.buttonEvents if be.type == structs.CarState.ButtonEvent.Type.lkas]
      self.assertEqual([be.pressed for be in lkas_events], [True, False], f"hold frame {idx + 2}")
    ret = state.update(parsers)
    self.assertTrue(state.lateral_off)
    self.assertEqual([be for be in ret.buttonEvents if be.type == structs.CarState.ButtonEvent.Type.lkas], [])

    # Flip LKAS_Dash_State (1 -> 0): restores AOL active cleanly.
    lkas_off = packer.make_can_msg('ES_LKAS_State', CanBus.camera, {'LKAS_Dash_State': 0})
    parsers[Bus.cam].update([(1_040_000_000, [lkas_off])])
    ret = state.update(parsers)
    self.assertFalse(state.lateral_off)

    # Toggle off, then drop Cruise_On -> lateral_off resets to False when AOL is enabled.
    parsers[Bus.cam].update([(1_060_000_000, [lkas_on])])
    state.update(parsers)
    self.assertTrue(state.lateral_off)
    dash_off = packer.make_can_msg('ES_DashStatus', CanBus.camera, {'Cruise_On': 0})
    parsers[Bus.cam].update([(1_080_000_000, [dash_off])])
    state.update(parsers)
    self.assertFalse(state.lateral_off)

  def test_aol_mode_transitions_and_lkas_button_behavior(self):
    cp = CarInterface.get_params(CAR.SUBARU_LEGACY_2025, {0: {}, 1: {}, 2: {}}, [], False, False, False)
    _, frames = pair.TestSubaruGen2AnglePair.sources(CAR.SUBARU_LEGACY_2025)
    packer = CANPacker(DBC[CAR.SUBARU_LEGACY_2025][Bus.pt])

    def init_state(aol_enabled: bool):
      state = CarState(cp)
      state.aol_enabled = aol_enabled
      parsers = state.get_can_parsers(cp)
      state.update(parsers)
      for parser in parsers.values():
        parser.update([(1_000_000_000, frames)])
      state.update(parsers)
      return state, parsers

    def press_lkas(state, parsers, ts_ns: int, dash_state: int):
      msg = packer.make_can_msg('ES_LKAS_State', CanBus.camera, {'LKAS_Dash_State': dash_state})
      parsers[Bus.cam].update([(ts_ns, [msg])])
      return state.update(parsers)

    # 1. AOL ON: AOL Active <-> AOL Off.
    state, parsers = init_state(True)
    self.assertFalse(state.lateral_off)
    press_lkas(state, parsers, 1_020_000_000, 1)
    self.assertTrue(state.lateral_off)
    press_lkas(state, parsers, 1_040_000_000, 0)
    self.assertFalse(state.lateral_off)

    # 2. AOL OFF: forced Off even after LKAS presses.
    state, parsers = init_state(False)
    self.assertTrue(state.lateral_off)
    press_lkas(state, parsers, 1_020_000_000, 1)
    self.assertTrue(state.lateral_off)

    # 3. Dynamic transitions across AOL settings changes.
    state, parsers = init_state(True)
    self.assertFalse(state.lateral_off)
    state.aol_enabled = False
    state.update(parsers)
    self.assertTrue(state.lateral_off)
    state.aol_enabled = True
    state.update(parsers)
    self.assertFalse(state.lateral_off)

  def test_carcontroller_dash_state_and_steering_across_lateral_modes(self):
    _, controller, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
    state.out.cruiseState.available = True
    state.out.gearShifter = structs.CarState.GearShifter.drive
    state.out.vEgo = 20.0
    state.out.standstill = False

    # AOL active -> LKAS_Dash_State == 2, lines visible, and ES_LKAS_ANGLE LKAS_Request == 1.
    state.aol_enabled = True
    state.lateral_off = False
    command.enabled = True
    command.latActive = True
    command.hudControl.lanesVisible = True
    command.hudControl.leftLaneVisible = True
    command.hudControl.rightLaneVisible = True
    command.actuators.steeringAngleDeg = 10.0
    _, angle = step(controller, command, state)
    self.assertEqual(angle['LKAS_Request'], 1)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 2)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 1)
    self.assertEqual(hud['LKAS_Right_Line_Visible'], 1)

    # lateral_off with AOL toggle ON -> LKAS_Dash_State == 1 (white ready wheel), NO lines, and LKAS_Request == 0.
    state.lateral_off = True
    state.aol_enabled = True
    command.hudControl.lanesVisible = False
    _, angle = step(controller, command, state)
    self.assertEqual(angle['LKAS_Request'], 0)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 1)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 0)
    self.assertEqual(hud['LKAS_Right_Line_Visible'], 0)

    # AOL toggle OFF while not engaged -> LKAS_Dash_State == 0 (wheel off) and NO lines (even before lateral_off syncs).
    state.aol_enabled = False
    state.lateral_off = False
    command.enabled = False
    command.latActive = False
    command.hudControl.lanesVisible = False
    _, angle = step(controller, command, state)
    self.assertEqual(angle['LKAS_Request'], 0)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 0)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 0)
    self.assertEqual(hud['LKAS_Right_Line_Visible'], 0)

    # AOL toggle OFF with ACC engaged (even when state.lateral_off is still True) -> steers + green wheel + lines.
    state.lateral_off = True
    command.enabled = True
    command.latActive = True
    command.hudControl.lanesVisible = True
    _, angle = step(controller, command, state)
    self.assertEqual(angle['LKAS_Request'], 1)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 2)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 1)

    # Main cruise OFF -> LKAS_Dash_State == 0 (wheel icon off) even with AOL toggle ON.
    state.aol_enabled = True
    state.out.cruiseState.available = False
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 0)

  def test_lkas_button_hold_survives_conflation_and_keeps_dash_wheel_in_sync(self):
    cp, controller, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
    _, frames = pair.TestSubaruGen2AnglePair.sources(CAR.SUBARU_LEGACY_2025)
    packer = CANPacker(DBC[CAR.SUBARU_LEGACY_2025][Bus.pt])
    state.aol_enabled = True
    parsers = state.get_can_parsers(cp)
    state.update(parsers)
    for parser in parsers.values():
      parser.update([(1_000_000_000, frames)])
    state.update(parsers)
    self.assertFalse(state.lateral_off)

    controlsd_lateral_off = False
    prev_lkas_pressed = False

    def step_controlsd_from_ret(car_state):
      nonlocal controlsd_lateral_off, prev_lkas_pressed
      raw_pressed = any(
        be.type == structs.CarState.ButtonEvent.Type.lkas and be.pressed
        for be in car_state.buttonEvents
      )
      if not car_state.cruiseState.available:
        prev_lkas_pressed = False
      lkas_pressed = raw_pressed and not prev_lkas_pressed
      prev_lkas_pressed = raw_pressed
      if lkas_pressed:
        controlsd_lateral_off = not controlsd_lateral_off

    # 1. Press LKAS (0 -> 1): simulate SubMaster dropping the first two carState frames.
    parsers[Bus.cam].update([(1_020_000_000, [packer.make_can_msg('ES_LKAS_State', CanBus.camera, {'LKAS_Dash_State': 1})])])
    cs_frames = [state.update(parsers) for _ in range(6)]
    self.assertEqual([any(be.type == structs.CarState.ButtonEvent.Type.lkas for be in f.buttonEvents) for f in cs_frames],
                     [True, True, True, True, True, False])
    self.assertTrue(state.lateral_off)
    for f in cs_frames[2:]:
      step_controlsd_from_ret(f)
    self.assertTrue(controlsd_lateral_off)
    self.assertEqual(state.lateral_off, controlsd_lateral_off)

    # With AOL toggled off, wheel icon must stay white (1) with no lane lines and no steering request.
    state.out.cruiseState.available = True
    state.out.gearShifter = structs.CarState.GearShifter.drive
    state.out.vEgo = 20.0
    state.out.standstill = False
    command.enabled = False
    command.latActive = True
    command.hudControl.lanesVisible = False
    command.hudControl.leftLaneVisible = True
    command.actuators.steeringAngleDeg = -8.0
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 0)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 1)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 0)

    # 2. Press LKAS (1 -> 0) back to AOL: again drop first two carState frames via conflation.
    parsers[Bus.cam].update([(1_100_000_000, [packer.make_can_msg('ES_LKAS_State', CanBus.camera, {'LKAS_Dash_State': 0})])])
    cs_frames_2 = [state.update(parsers) for _ in range(6)]
    self.assertEqual([any(be.type == structs.CarState.ButtonEvent.Type.lkas for be in f.buttonEvents) for f in cs_frames_2],
                     [True, True, True, True, True, False])
    self.assertFalse(state.lateral_off)
    for f in cs_frames_2[2:]:
      step_controlsd_from_ret(f)
    self.assertFalse(controlsd_lateral_off)
    self.assertEqual(state.lateral_off, controlsd_lateral_off)

    # In AOL mode with active steering, wheel icon turns back to green (2) and lines reappear.
    command.enabled = True
    command.latActive = True
    command.hudControl.lanesVisible = True
    command.actuators.steeringAngleDeg = 8.0
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 1)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 2)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 1)

  def test_carcontroller_aol_green_wheel_park_override_and_alerts(self):
    _, controller, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
    state.out.cruiseState.available = True
    state.out.gearShifter = structs.CarState.GearShifter.park
    state.out.vEgo = 0.0
    state.out.standstill = True
    state.aol_enabled = True

    # AOL active in Park / standstill (latActive=False, lanesVisible=True) -> green wheel icon (2).
    state.lateral_off = False
    command.enabled = False
    command.latActive = False
    command.hudControl.lanesVisible = True
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 2)

    # Toggle LKAS off in Park -> immediately drops to white wheel icon (1).
    state.lateral_off = True
    command.hudControl.lanesVisible = False
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 1)

    # steerRequired visual alert maps to Keep Hands On Wheel message (1) cleanly on 0x322.
    command.hudControl.visualAlert = structs.CarControl.HUDControl.VisualAlert.steerRequired
    command.hudControl.audibleAlert = structs.CarControl.HUDControl.AudibleAlert.none
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Alert_Msg'], 1)
    self.assertEqual(hud['LKAS_Alert'], 0)
    command.hudControl.visualAlert = structs.CarControl.HUDControl.VisualAlert.none
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Alert_Msg'], 0)
    self.assertEqual(hud['LKAS_Alert'], 0)

    # Driving in AOL / full OP: active steering -> green (2), manual override -> white (1), resume -> green (2).
    state.lateral_off = False
    state.out.gearShifter = structs.CarState.GearShifter.drive
    state.out.vEgo = 20.0
    state.out.vEgoRaw = 20.0
    state.out.standstill = False
    command.enabled = True
    command.latActive = True
    command.hudControl.lanesVisible = True
    state.out.steeringTorque = 0.0
    state.out.steeringRateDeg = 0.0
    state.out.steeringPressed = False
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 1)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 2)

    # Driver manually overrides steering -> wheel icon turns white (1).
    state.out.steeringTorque = 250.0
    state.out.steeringRateDeg = 30.0
    state.out.steeringPressed = True
    step(controller, command, state)
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 0)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 1)

    # Driver releases wheel -> openpilot resumes control and wheel icon turns back to green (2).
    state.out.steeringTorque = 0.0
    state.out.steeringRateDeg = 0.0
    state.out.steeringPressed = False
    step(controller, command, state)
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 1)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 2)

    # Stock EyeSight LDW / allowed alert takes priority over steerRequired alert.
    state.es_lkas_state_msg["LKAS_Alert"] = 12
    command.hudControl.visualAlert = structs.CarControl.HUDControl.VisualAlert.steerRequired
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Alert'], 12)
    self.assertEqual(hud['LKAS_Alert_Msg'], 0)

    dbc = DBC[CAR.SUBARU_LEGACY_2025][Bus.pt]
    packer = CANPacker(dbc)
    info_parser = CANParser(dbc, [('ES_Infotainment', 0)], CanBus.main)
    info_msg = subarucan.create_es_infotainment(
      packer, 0, {'CHECKSUM': 0, 'LKAS_State_Infotainment': 1, 'LKAS_Blue_Lines': 0, 'Signal1': 0, 'Signal2': 0},
      structs.CarControl.HUDControl.VisualAlert.steerRequired,
      legacy_2025=True,
    )
    info_parser.update([(1, [info_msg])])
    self.assertEqual(info_parser.vl['ES_Infotainment']['LKAS_State_Infotainment'], 1)

    for audible, expected in (
      (structs.CarControl.HUDControl.AudibleAlert.none, 3),
      (structs.CarControl.HUDControl.AudibleAlert.warningSoft, 3),
      (structs.CarControl.HUDControl.AudibleAlert.warningImmediate, 4),
    ):
      info_msg = subarucan.create_es_infotainment(
        packer, 1, {'CHECKSUM': 0, 'LKAS_State_Infotainment': 0, 'LKAS_Blue_Lines': 0, 'Signal1': 0, 'Signal2': 0},
        structs.CarControl.HUDControl.VisualAlert.steerRequired,
        audible_alert=audible,
        legacy_2025=True,
      )
      info_parser.update([(2, [info_msg])])
      self.assertEqual(info_parser.vl['ES_Infotainment']['LKAS_State_Infotainment'], expected)

    # Non-Legacy default (legacy_2025=False) preserves exact upstream 0x323 behavior.
    for stock_state in (0, 1):
      info_msg = subarucan.create_es_infotainment(
        packer, 2, {'CHECKSUM': 0, 'LKAS_State_Infotainment': stock_state, 'LKAS_Blue_Lines': 0, 'Signal1': 0, 'Signal2': 0},
        structs.CarControl.HUDControl.VisualAlert.steerRequired,
        audible_alert=structs.CarControl.HUDControl.AudibleAlert.warningImmediate,
      )
      info_parser.update([(3, [info_msg])])
      self.assertEqual(info_parser.vl['ES_Infotainment']['LKAS_State_Infotainment'], 3)

  def test_carcontroller_aol_manumatic_and_unauthorized_gate(self):
    cp, _, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
    cp.alternativeExperience = 32
    controller = CarController(DBC[CAR.SUBARU_LEGACY_2025], cp)
    state.out.gearShifter = structs.CarState.GearShifter.manumatic
    state.lateral_off = False
    state.out.steeringAngleDeg = 0.0
    state.out.steeringRateDeg = 0.0
    state.out.steeringTorque = 0.0

    # Active AOL in manumatic: angle requested, green wheel icon (2), visible left line (1).
    command.enabled = False
    command.latActive = True
    command.hudControl.lanesVisible = True
    command.hudControl.leftLaneVisible = True
    command.hudControl.rightLaneVisible = False
    command.actuators.steeringAngleDeg = -8.0
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 1)
    self.assertLess(decoded['LKAS_Output'], 0.0)
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 2)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 1)

    # Unauthorized gate: when controlsd keeps latActive=False, CarController must never request 0x124 torque/angle.
    command.latActive = False
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 0)
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 0)

  def test_legacy_2025_manumatic_transition_and_between_detent_gears(self):
    cp = CarInterface.get_params(CAR.SUBARU_LEGACY_2025, {0: {}, 1: {}, 2: {}}, [], False, False, False)
    _, frames = pair.TestSubaruGen2AnglePair.sources(CAR.SUBARU_LEGACY_2025)
    packer = CANPacker(DBC[CAR.SUBARU_LEGACY_2025][Bus.pt])
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    state.update(parsers)
    for parser in parsers.values():
      parser.update([(1_000_000_000, frames)])
    state.update(parsers)

    Gear = structs.CarState.GearShifter
    for can_gear, expected_gear in ((121, Gear.drive), (129, Gear.manumatic), (177, Gear.manumatic), (0, Gear.neutral), (4, Gear.park)):
      msg = packer.make_can_msg('Transmission', 0, {'Gear': can_gear})
      parsers[Bus.pt].update([(1_020_000_000, [msg])])
      ret = state.update(parsers)
      self.assertEqual(ret.gearShifter, expected_gear, f'can_gear={can_gear}')

  def test_legacy_2025_acc_lead_led_preserved_and_aol_line_visibility_passthrough(self):
    _, _, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
    cp = CarInterface.get_params(CAR.SUBARU_LEGACY_2025, {0: {}, 1: {}, 2: {0x3C4: 8}}, [], False, False, False)
    controller = CarController(DBC[CAR.SUBARU_LEGACY_2025], cp)
    state.out.cruiseState.available = True
    state.out.gearShifter = structs.CarState.GearShifter.drive
    state.out.vEgo = 20.0
    state.out.standstill = False

    # Stock ACC active via Cruise_Activated_Dash + Car_Follow (not gasPressed) preserves dat[5]=0x01 when masking 000104000000.
    state.out.cruiseState.enabled = True
    state.out.gasPressed = False
    state.es_dashstatus_msg.update({'Cruise_Activated_Dash': 1, 'Cruise_State': 0, 'Car_Follow': 1})
    raw = bytearray(bytes.fromhex('0002000104000000'))
    raw[0] = (0xC4 + 3 + sum(raw[1:])) & 0xFF
    state.es_lkas_alert_dat = bytes(raw)
    msg = controller._ascent_lkas_alert(command.as_reader(), state)
    self.assertIsNotNone(msg)
    self.assertEqual(msg[1][2:], bytes.fromhex('000000010000'))

    # Gas pedal override clears green lead LED (dat[5] = 0x00) even when cruiseState.enabled and Car_Follow remain set.
    for idx, override_fields in enumerate((
      {'gas': True, 'dash': 1, 'cstate': 0},
      {'gas': False, 'dash': 0, 'cstate': 2},
    )):
      state.out.gasPressed = override_fields['gas']
      state.es_dashstatus_msg.update({
        'Cruise_Activated_Dash': override_fields['dash'],
        'Cruise_State': override_fields['cstate'],
        'Car_Follow': 1,
      })
      raw[1] = 0x03 + idx
      raw[0] = (0xC4 + 3 + sum(raw[1:])) & 0xFF
      state.es_lkas_alert_dat = bytes(raw)
      msg = controller._ascent_lkas_alert(command.as_reader(), state)
      self.assertIsNotNone(msg)
      self.assertEqual(msg[1][2:], bytes.fromhex('000000000000'), override_fields)
    state.out.gasPressed = False
    state.es_dashstatus_msg.update({'Cruise_Activated_Dash': 1, 'Cruise_State': 0})

    # Without Car_Follow, dat[5] stays 0x00 when masking the alert.
    state.es_dashstatus_msg.update({'Cruise_Activated_Dash': 1, 'Cruise_State': 0, 'Car_Follow': 0})
    raw[1] = 0x05
    raw[0] = (0xC4 + 3 + sum(raw[1:])) & 0xFF
    state.es_lkas_alert_dat = bytes(raw)
    msg = controller._ascent_lkas_alert(command.as_reader(), state)
    self.assertIsNotNone(msg)
    self.assertEqual(msg[1][2:], bytes.fromhex('000000000000'))

    # AOL mode passes debounced hudControl lane visibility directly through to cluster CAN.
    state.lateral_off = False
    command.enabled = False
    command.latActive = True
    command.hudControl.lanesVisible = True
    command.hudControl.leftLaneVisible = True
    command.hudControl.rightLaneVisible = True
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 1)
    self.assertEqual(hud['LKAS_Right_Line_Visible'], 1)

    command.hudControl.leftLaneVisible = False
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 0)
    self.assertEqual(hud['LKAS_Right_Line_Visible'], 1)

    command.hudControl.leftLaneVisible = True
    hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Left_Line_Visible'], 1)

  def test_aol_wheel_icon_park_reverse_neutral_and_shift_debounce(self):
    _, controller, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
    state.out.cruiseState.available = True
    state.out.vEgo = 0.0
    state.out.vEgoRaw = 0.0
    state.out.standstill = True
    state.aol_enabled = True
    state.lateral_off = False
    command.enabled = False
    command.latActive = False
    command.hudControl.lanesVisible = True

    # In Park: stays solid green even if steering wheel is moved slightly.
    state.out.gearShifter = structs.CarState.GearShifter.park
    state.out.steeringPressed = True
    self.assertEqual(_step_to_hud(controller, command, state)['LKAS_Dash_State'], 2)

    # Fast Park -> Reverse -> Neutral -> Drive shift (< 0.8s) holds green without flashing white.
    state.out.steeringPressed = False
    for gear, steps in (
      (structs.CarState.GearShifter.reverse, 3),
      (structs.CarState.GearShifter.neutral, 3),
    ):
      state.out.gearShifter = gear
      for _ in range(steps):
        self.assertEqual(_step_to_hud(controller, command, state)['LKAS_Dash_State'], 2)
    state.out.gearShifter = structs.CarState.GearShifter.drive
    self.assertEqual(_step_to_hud(controller, command, state)['LKAS_Dash_State'], 2)

    # Settling in Reverse (>= 0.8s) switches to solid white regardless of steering input.
    state.out.gearShifter = structs.CarState.GearShifter.reverse
    for _ in range(9):
      hud = _step_to_hud(controller, command, state)
    self.assertEqual(hud['LKAS_Dash_State'], 1)
    state.out.steeringPressed = True
    self.assertEqual(_step_to_hud(controller, command, state)['LKAS_Dash_State'], 1)
    state.out.gearShifter = structs.CarState.GearShifter.neutral
    self.assertEqual(_step_to_hud(controller, command, state)['LKAS_Dash_State'], 1)

    # Returning to Park restores solid green cleanly.
    state.out.gearShifter = structs.CarState.GearShifter.park
    self.assertEqual(_step_to_hud(controller, command, state)['LKAS_Dash_State'], 2)


if __name__ == '__main__':
  unittest.main()
