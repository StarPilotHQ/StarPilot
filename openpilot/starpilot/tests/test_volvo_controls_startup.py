"""Real Card/Controls constructors, CAN ingress, selected factory and native TX.
Only discovery and external CAN ingress are supplied by the fixture.
"""
import os
import time
import unittest
from unittest.mock import patch

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.volvo.tests.test_cma import CMA, EXPECTED, sources, packet
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.card import Car
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle
from openpilot.selfdrive.pandad import can_list_to_can_capnp


def incoming(identity, *, frame=0):
  if identity != 'VOLVO_V40':
    return sources(identity, frame=frame)
  packer = CANPacker('volvo_v40_2017_pt')
  messages: list[tuple[str, int, dict[str, float]]] = [
    ('VehicleSpeed1', 0, {'VehicleSpeed': 72}), ('CCButtons', 0, {}),
    ('PSCM1', 0, {'SteeringAngleServo': 12.5, 'LKATorque': 10}),
    ('PedalandBrake', 0, {}), ('TCM0', 0, {'GearShifter': 3}), ('ACC', 0, {'SpeedTargetACC': 100}),
    ('MiscCarInfo', 0, {}), ('FSM0', 2, {'ACCStatusOnOff': 1, 'ACCStatusActive': 1}), ('FSM1', 2, {}),
  ]
  return [packer.make_can_msg(name, bus, values) for name, bus, values in messages]


class TestVolvoActualStartup(unittest.TestCase):
  def start(self, identity, *, enabled=True, aol=False, alpha=False):
    params = Params()
    for key, value in [('OpenpilotEnabledToggle', enabled), ('SafeMode', False), ('IsReleaseBranch', False),
                       ('AlphaLongitudinalEnabled', alpha), ('AlwaysOnLateral', aol), ('ForceAutoTuneOff', True)]:
      params.put_bool(key, value, block=True)
    result = (identity, gen_empty_fingerprint(), '0' * 17, [], structs.CarParams.FingerprintSource.can, True)
    with patch('opendbc.car.car_helpers.fingerprint', return_value=result), \
         patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=messaging.new_message('can', 1)):
      card = Car()
    controls = Controls()
    self.assertEqual(controls.CP.to_dict(), card.CP.to_dict())
    self.assertIsInstance(controls.LaC, LatControlAngle)
    self.assertIsNone(controls.torque_host)
    self.assertFalse(card.CP.openpilotLongitudinalControl)
    self.assertEqual(card.CP.passive, not enabled)
    self.assertEqual(card.CP.safetyConfigs[0].safetyModel,
                     structs.CarParams.SafetyModel.volvo if enabled else structs.CarParams.SafetyModel.noOutput)
    self.assertEqual(card.aol_qualified, False)
    self.assertIsNone(card.aol_card_intent)
    self.assertTrue(params.get_bool('ForceAutoTuneOff'))
    self.assertFalse(params.get_bool('ControlsReady'))
    # Ordinary family path delays init/ControlsReady until valid CAN and controls.
    invalid = structs.CarState.new_message()
    card.controls_update(invalid, card.sm['carControl'])
    self.assertFalse(params.get_bool('ControlsReady'))
    command, log = controls.state_control()
    self.assertFalse(command.enabled or command.latActive or command.longActive)
    deadline = time.monotonic() + 1
    while not card.sm.seen['carControl'] and time.monotonic() < deadline:
      controls.publish(command, log)
      card.sm.update(10)
    self.assertTrue(card.sm.seen['carControl'])
    return card, controls

  def assert_ready(self, card):
    self.assertTrue(card.ci_initialized)
    # Card intentionally queues Params writes; await durable publication only.
    deadline = time.monotonic() + 1.
    while not card.params.get_bool('ControlsReady') and time.monotonic() < deadline:
      time.sleep(.001)
    self.assertTrue(card.params.get_bool('ControlsReady'))

  def publish_controls(self, card, controls, state, *, enabled):
    now = time.monotonic_ns()
    messages = []
    for name, payload in [('carState', state.to_dict()),
                          ('selfdriveState', {'enabled': enabled, 'active': enabled,
                           'state': 'enabled' if enabled else 'disabled'}),
                          ('vehicleParameters', {'stiffnessFactor': 1., 'steerRatio': card.CP.steerRatio})]:
      message = messaging.new_message(name, valid=True, logMonoTime=now)
      setattr(message, name, payload)
      messages.append(message)
    controls.sm.update_msgs(now / 1e9, messages)
    command, log = controls.state_control()
    self.assertEqual(command.latActive, enabled)
    self.assertFalse(command.longActive)
    controls.publish(command, log)
    deadline = time.monotonic() + .25
    while card.sm.logMonoTime['carControl'] < now and time.monotonic() < deadline:
      card.sm.update(10)
    self.assertTrue(card.sm.valid['carControl'])
    self.assertGreaterEqual(card.sm.logMonoTime['carControl'], now)
    return command

  def test_ordinary_startup_waits_then_real_can_and_control_emit_to_native(self):
    for identity in EXPECTED:
      for aol, alpha in ((False, False), (True, True)):
        with self.subTest(identity=identity, aol=aol, alpha=alpha), OpenpilotPrefix(), \
             patch.dict(os.environ, {'SIMULATION': '1'}):
          card, controls = self.start(identity, aol=aol, alpha=alpha)
          data = incoming(identity)
          with patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw',
                     return_value=[can_list_to_can_capnp(data)]):
            state, _ = card.state_update()
          self.assertTrue(state.canValid)
          self.publish_controls(card, controls, state, enabled=False)
          cp = card.CP
          lib = libsafety_py.libsafety
          self.assertEqual(lib.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam), 0)
          lib.init_tests()
          lib.set_timer(10000)
          for msg in data:
            self.assertTrue(lib.safety_rx_hook(packet(msg)), msg)
          lib.safety_tick()
          with patch.object(card, 'publish_sendcan', wraps=card.publish_sendcan) as published:
            card.controls_update(state, card.sm['carControl'])
          self.assert_ready(card)
          self.assertTrue(published.called)
          frames = published.call_args.args[0]
          if identity in CMA:
            # Disengaged CMA sends nothing; panda forwards the stock frames instead.
            self.assertEqual(frames, [])
          else:
            self.assertTrue(frames)
          for msg in frames:
            self.assertTrue(lib.safety_tx_hook(packet(msg)), msg)

  def test_explicit_disabled_openpilot_remains_passive_and_off_preserved(self):
    for identity in EXPECTED:
      with self.subTest(identity=identity), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
        card, _ = self.start(identity, enabled=False, aol=True, alpha=True)
        self.assertTrue(card.CP.passive)
        self.assertEqual(card.CP.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)

  def test_actual_controls_card_native_cycle_with_upstream_selfdrive_inputs(self):
    # Inputs are typed selfdriveState publications. This exercises Controls and
    # Card, not selfdrived's event/state machine or a physical engagement claim.
    for identity in EXPECTED:
      with self.subTest(identity=identity), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
        card, controls = self.start(identity)
        lib = libsafety_py.libsafety
        cp = card.CP
        self.assertEqual(lib.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam), 0)
        lib.init_tests()
        for frame in range(20):
          data = incoming(identity, frame=frame)
          lib.set_timer(10000 + frame * 10000)
          for msg in data:
            self.assertTrue(lib.safety_rx_hook(packet(msg)), msg)
          lib.safety_tick()
          with patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw',
                     return_value=[can_list_to_can_capnp(data)]):
            state, _ = card.state_update()
          enabled = 4 <= frame < 10 or frame >= 14
          self.publish_controls(card, controls, state, enabled=enabled)
          with patch.object(card, 'publish_sendcan', wraps=card.publish_sendcan) as published:
            card.controls_update(state, card.sm['carControl'])
          self.assertTrue(published.called)
          for msg in published.call_args.args[0]:
            self.assertTrue(lib.safety_tx_hook(packet(msg)), (identity, frame, msg))
        self.assert_ready(card)
