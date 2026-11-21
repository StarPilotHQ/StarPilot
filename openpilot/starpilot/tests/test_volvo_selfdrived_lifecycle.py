"""Genuine event/state-machine producer -> Controls -> Card -> native TX.

CAN, external service transport and fault flags are synthetic. Typed EPS flags
exercise generic fault handling, not undocumented physical EPS decoding.
"""
import os
import time
from collections.abc import Callable
from contextlib import ExitStack
from typing import NoReturn, cast
from unittest.mock import patch

import pytest

from opendbc.can import CANPacker
from opendbc.car.volvo.tests.test_cma import CMA, EXPECTED, packet, sources
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import log, messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.pandad import can_list_to_can_capnp
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.tests import test_volvo_controls_startup as startup


def frames_for(identity, frame, cruise):
  if identity in CMA:
    return sources(identity, cruise=cruise, frame=frame)
  frames = startup.incoming(identity, frame=frame)
  fsm = CANPacker('volvo_v40_2017_pt').make_can_msg(
    'FSM0', 2, {'ACCStatusOnOff': 1, 'ACCStatusActive': int(cruise)})
  return [msg for msg in frames if (msg[0], msg[2]) != (fsm[0], fsm[2])] + [fsm]


@pytest.mark.parametrize('identity', EXPECTED)
def test_actual_selfdrived_fault_clear_does_not_reengage_without_pcm_edge(identity):
  case = startup.TestVolvoActualStartup(methodName='runTest')
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}), ExitStack() as cleanup:
    card, controls = case.start(identity)
    sd = SelfdriveD(CP=card.CP)
    assert not sd.aol_replay and not sd.ordinary_axis_ack_required
    lib = libsafety_py.libsafety
    cfg = card.CP.safetyConfigs[0]
    assert lib.set_safety_hooks(cfg.safetyModel.raw, cfg.safetyParam) == 0
    lib.init_tests()
    base = time.monotonic_ns()
    clock = [base]
    original_message = messaging.new_message

    def clocked_message(service, size=None, **kwargs):
      kwargs.setdefault('logMonoTime', clock[0])
      return original_message(service, size, **kwargs)

    cleanup.enter_context(patch.object(messaging, 'new_message', side_effect=clocked_message))
    due, sent = {}, {}
    previous_command = [None]
    # These on-demand test modes trigger from receipt alone; ordinary driving
    # must leave them unseen, just as production does without a test producer.
    names = [name for name in sd.sm.data if SERVICE_LIST[name].frequency > 0 and
             name not in ('lateralManeuverPlan', 'alertDebug')]

    def feed_services(stamp, *, force_panda=False):
      messages = []
      for name in names:
        if stamp < due.get(name, 0) and not (name == 'pandaStates' and force_panda):
          continue
        frequency = SERVICE_LIST[name].frequency
        due[name] = stamp + round(1e9 / min(frequency, 100))
        # The production subscriber includes an optional Data wire as well as
        # structs and the Panda list. Preserve cadence/health checks while
        # giving each actual cereal field its required initialization shape.
        size = len(card.CP.safetyConfigs) if name == 'pandaStates' else 0 if name == 'laneChangeAssistWire' else None
        message = messaging.new_message(name, size, valid=True, logMonoTime=stamp)
        value = getattr(message, name)
        if name == 'deviceState':
          value.started, value.startedMonoTime, value.freeSpacePercent = True, base - 2_000_000_000, 50.
        elif name == 'pandaStates':
          for panda, config in zip(value, card.CP.safetyConfigs, strict=True):
            panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
            panda.alternativeExperience = card.CP.alternativeExperience
            panda.controlsAllowed = bool(lib.get_controls_allowed())
            panda.safetyRxChecksInvalid = not lib.safety_config_valid()
        elif name == 'extrinsicsCalibration':
          value.calStatus, value.rpyCalib = 'calibrated', [0., 0., 0.]
        elif name == 'deviceMotion':
          value.inputsOK = value.posenetOK = True
        elif name == 'vehicleParameters':
          value.valid, value.stiffnessFactor, value.steerRatio = True, 1., card.CP.steerRatio
        elif name == 'controlsState':
          value.lateralControlState.init('angleState')
        elif name == 'carControl' and previous_command[0] is not None:
          message.carControl = previous_command[0]
        messages.append(message.as_reader())
      sd.sm.update_msgs(stamp / 1e9, messages)

    # Prime real cadence trackers; do not replace validity/frequency checks.
    for tick in range(-120, 0):
      feed_services(base + tick * 10_000_000)

    real_send = sd.pm.send

    def record_send(name, message):
      sent[name] = message.as_reader()
      real_send(name, message)

    frame = 0

    def cycle(cruise=True, *, fault=None, corrupt_native=False):
      nonlocal frame
      stamp = base + frame * 10_000_000
      clock[0] = stamp
      data = frames_for(identity, frame, cruise)
      lib.set_timer(10000 + frame * 10000)
      for msg in data:
        assert lib.safety_rx_hook(packet(msg)), (identity, frame, msg)
      if corrupt_native:
        msg = next(msg for msg in data if msg[0] == 0x69 and msg[2] == 0)
        corrupt = (msg[0], bytes([msg[1][0] ^ 1]) + msg[1][1:], msg[2])
        assert not lib.safety_rx_hook(packet(corrupt))
      lib.safety_tick()
      with patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw',
                 return_value=[can_list_to_can_capnp(data)]):
        state, _ = card.state_update()
      assert state.canValid
      if fault:
        setattr(state, fault, fault != 'canValid')
      envelope = messaging.new_message('carState', valid=state.canValid, logMonoTime=stamp)
      envelope.carState = state
      with patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=envelope.as_reader()), \
           patch.object(sd.sm, 'update', side_effect=lambda *_: feed_services(stamp, force_panda=corrupt_native)), \
           patch.object(sd.pm, 'send', side_effect=record_send):
        sd.step()
      assert sd.sm.all_checks()
      assert 'selfdriveState' in sent
      publications = [envelope.as_reader(), sent['selfdriveState']]
      params = messaging.new_message('vehicleParameters', valid=True, logMonoTime=stamp)
      params.vehicleParameters.stiffnessFactor, params.vehicleParameters.steerRatio = 1., card.CP.steerRatio
      publications.append(params.as_reader())
      if 'onroadEvents' in sent:
        publications.append(sent['onroadEvents'])
      controls.sm.update_msgs(stamp / 1e9, publications)
      command, lateral_log = controls.state_control()
      assert command.enabled == sd.enabled
      assert not command.longActive
      if fault or not sd.active:
        assert not command.latActive
      controls.publish(command, lateral_log)
      deadline = time.monotonic() + .25
      while card.sm.logMonoTime['carControl'] < stamp and time.monotonic() < deadline:
        card.sm.update(10)
      assert card.sm.logMonoTime['carControl'] >= stamp
      previous_command[0] = card.sm['carControl']
      with patch.object(card, 'publish_sendcan', wraps=card.publish_sendcan) as publish, \
           patch.object(card.CI, 'apply', wraps=card.CI.apply) as apply:
        card.controls_update(state, card.sm['carControl'])
      if not state.canValid:
        # Controls publishes invalid carControl on this real CAN-invalid input;
        # Card must keep its original validity gate and emit no command at all.
        assert fault == 'canValid'
        assert not card.sm.valid['carControl']
        assert not command.enabled and not command.latActive and not command.longActive
        assert not apply.called and not publish.called
      else:
        assert apply.called and publish.called
        for msg in publish.call_args.args[0]:
          assert lib.safety_tx_hook(packet(msg)), (identity, frame, fault, msg)
      frame += 1
      return state, command

    def assert_event(event):
      assert event in sd.events.names
      assert event in [item.name for item in sent['onroadEvents'].onroadEvents]

    def reengage():
      # Preserve the actual Panda service cadence. A healthy native receiver
      # cannot replace a still-cached fault report; wait for the next real
      # healthy receipt while ordinary control remains explicitly disabled.
      after_source = int(sd.sm.logMonoTime['pandaStates'])
      period_ns = round(1e9 / SERVICE_LIST['pandaStates'].frequency)
      for _ in range((period_ns + 9_999_999) // 10_000_000 + 2):
        cycle(False)
        assert not sd.enabled
        fresh_panda = (int(sd.sm.logMonoTime['pandaStates']) > after_source and
                       sd.sm.all_checks(['pandaStates']) and
                       all(not panda.safetyRxChecksInvalid and not panda.faults for panda in sd.sm['pandaStates']))
        if lib.safety_config_valid() and fresh_panda:
          break
      else:
        cast(Callable[[str], NoReturn], pytest.fail)('Native RX and a fresh healthy Panda receipt did not recover within one source period')
      assert not sd.enabled
      _, command = cycle(True)
      assert sd.enabled and sd.active and command.latActive

    cycle(False)
    reengage()
    cycle(fault='steerFaultTemporary')
    assert any(event in sd.events.names for event in
               (log.OnroadEvent.EventName.steerTempUnavailable, log.OnroadEvent.EventName.steerTempUnavailableSilent))
    _, recovered = cycle()
    assert recovered.latActive
    for field, event in [('steerFaultPermanent', log.OnroadEvent.EventName.steerUnavailable),
                         ('canValid', log.OnroadEvent.EventName.canError),
                         ('canTimeout', log.OnroadEvent.EventName.canBusMissing)]:
      cycle(fault=field)
      assert_event(event)
      assert not sd.enabled
      cycle()
      assert not sd.enabled  # Clearing a hard fault does not synthesize PCM enable.
      reengage()
    if identity in CMA:
      # Select an even source frame: LCA_2 is a real 50 Hz input.
      if frame % 2:
        cycle()
      cycle(corrupt_native=True)
      assert_event(log.OnroadEvent.EventName.controlsMismatch)
      assert not sd.enabled
      cycle()
      assert not sd.enabled
      reengage()
    case.assert_ready(card)
