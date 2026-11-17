"""GM-wide MAIN readiness and Bolt physical withdrawal through current callers.

Native RX earns controls; the extracted current pandad C++ writer requests the
actual host decision. No controls_allowed or native permission flag is forced.
Board heartbeat telemetry is an explicit transport fixture, not an execution of
the firmware heartbeat timer; its reported-loss case must veto retention.
This measures a current caller contract, not an unlogged original blocked call.
"""
from dataclasses import replace
from collections import deque
from contextlib import ExitStack
import os
import json
from pathlib import Path
from unittest import mock

import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm import gmcan
from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
from opendbc.car.gm.values import CruiseButtons, DBC
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.starpilot.aol.tests import test_gm as gm_fixture
from openpilot.starpilot.aol.tests import test_ioniq_availability as caller_fixture
from openpilot.starpilot.aol.tests.test_gm_startup_main import parsed_packets
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, decode_intent, encode_safety
from openpilot.starpilot.car.gm.steering_companion import SteeringCompanion
from openpilot.starpilot.aol.tests.gm_gap_cpp_bridge import gm_host_heartbeat_bridge as gm_host_heartbeat_bridge

BASE = 10_000_000_000
LAST_SOURCE = BASE + 250_000_000
GAP_AGE = 36_415_981
SOURCE_ADVANCE = 73_592_025
NATIVE_BEFORE_SOURCE = 12_576_709


class Capture:
  def __init__(self):
    self.messages = {}

  def send(self, name, event):
    raw = event if isinstance(event, bytes) else event.to_bytes()
    self.messages[name] = messaging.log_from_bytes(raw)


def joined_case(cleanup, bridge, *, c9=False, pedal='none', engage=False, cp=None, cold_boot=False):
  saved = Params()
  cleanup.callback(saved._finalizer)
  for key in ('OpenpilotEnabledToggle', 'AlwaysOnLateral', 'AlphaLongitudinalEnabled'):
    saved.put_bool(key, True, block=True)
  cp = sdgm_params(alpha=True, brake_c9=c9) if cp is None else cp
  card = gm_fixture.TestGmAol.card(cp, saved)
  cleanup.callback(card.vehicle_startup.close)
  assert card.CP.alternativeExperience == 32
  card.ci_initialized = True  # No ECU/device I/O; native permission is earned below.
  card.pm = Capture()
  case = caller_fixture.IoniqAvailabilityTests(methodName='runTest')
  cleanup.callback(case.doCleanups)
  producer_message = messaging.new_message  # Preserve Card's own publication clock before the SD fixture patches it.
  sd, controls, step = case._authority_callers(False, cp=card.CP.as_reader().as_builder(), genuine_events=True)
  # Restore the real constructor's settings snapshot removed by this generic fixture.
  from openpilot.starpilot.aol.intent import read_settings
  sd.aol_settings = read_settings(sd.params)
  assert sd.aol_settings.enabled and sd.aol_settings.pause_brake_mps == 0.
  cleanup.callback(sd.params._finalizer)
  cleanup.callback(controls.params._finalizer)
  native = libsafety_py.libsafety
  # GM snapshots AE during mode initialization; configure it before selecting hooks.
  native.set_alternative_experience(card.CP.alternativeExperience)
  assert native.set_safety_hooks(card.CP.safetyConfigs[0].safetyModel.raw,
                                 card.CP.safetyConfigs[0].safetyParam) == 0
  # Match the shared native harness: hook selection resets the relay transition
  # counter; initialize its established-mode fixture afterwards, then set AE.
  native.init_tests()
  native.set_alternative_experience(card.CP.alternativeExperience)
  assert not native.get_controls_allowed()
  bridge.reset_host()
  packer = CANPacker(DBC[card.CP.carFingerprint][Bus.pt])
  receipt_queue = deque()
  steering_receipts = []
  sd.steering_companion = SteeringCompanion(sock=object())
  original_receive = messaging.recv_one_or_none
  original_update = sd.sm.update_msgs
  original_controls_update = controls.sm.update_msgs
  card_delivered = {}
  controls_delivered = {}
  delivered = {}
  current = {'gap': False, 'raw_ahead': False, 'pedal': 'none', 'warmup_trace': [], 'last_cs': None, 'wire': None, 'pandas': None, 'last_command': None}

  def receive(sock):
    if sock is sd.steering_companion.sock:
      return receipt_queue.popleft() if receipt_queue else None
    return original_receive(sock)

  def update(now, rows):
    if cold_boot and now < (BASE + 180_000_000) / 1e9:
      changed = []
      for row in rows:
        if row.which() == 'deviceMotion':
          row = row.as_builder()
          row.deviceMotion.inputsOK = False
          row = row.as_reader()
        changed.append(row)
      rows = changed
    if current['gap'] and not current['raw_ahead']:
      rows = [row for row in rows if row.which() not in ('aolIntentWire', 'carOutput', 'starpilotCarState')]
    actual = []
    for row in rows:
      if row.which() in ('aolSafetyWire', 'pandaStates'):
        signature = row.as_builder().to_bytes()
        if delivered.get(row.which()) == signature:
          continue
        delivered[row.which()] = signature
      actual.append(row)
    return original_update(now, actual)

  def controls_update(now, rows):
    actual = []
    for row in rows:
      if current['gap'] and row.which() in ('carState', 'carOutput', 'aolIntentWire', 'starpilotCarState'):
        continue
      if row.which() in ('aolSafetyWire', 'pandaStates'):
        signature = row.as_builder().to_bytes()
        if controls_delivered.get(row.which()) == signature:
          continue
        controls_delivered[row.which()] = signature
      actual.append(row)
    return original_controls_update(now, actual)

  cleanup.enter_context(mock.patch.object(messaging, 'recv_one_or_none', side_effect=receive))
  cleanup.enter_context(mock.patch.object(controls.sm, 'update_msgs', side_effect=controls_update))
  cleanup.enter_context(mock.patch.object(sd.sm, 'update_msgs', side_effect=update))

  def physical(now, tick, *, main=True, button=CruiseButtons.UNPRESS, eps=1):
    rows = parsed_packets(card.CP, packer, now, tick, 20., main)
    if current['pedal'] != 'none':
      from opendbc.car.gm.tests.test_volt_camera_control import feed_camera
      from types import SimpleNamespace
      _, extra = feed_camera(SimpleNamespace(update=lambda _: None), packer, now,
        counter=tick % 4, speed=20., brake=current['pedal'] == 'brake',
        regen=current['pedal'] == 'regen', active=False, eps_status=eps)
      addresses = {0xBE, 0xC9, 0xF1, 0xBD}
      rows = [row for row in rows if row[0] not in addresses]
      rows += [row for row in extra if row[0] in addresses and row[0] != 0xBE]
      rows.append(packer.make_can_msg('ECMAcceleratorPos', 0,
        {'BrakePedalPos': 10 if current['pedal'] == 'brake' else 0}))
    if cold_boot and tick < 20:
      gear_address = packer.make_can_msg('ECMPRDNL2', 0, {})[0]
      wheel_addresses = {packer.make_can_msg(name, 0, {})[0] for name in ('EBCMWheelSpdFront', 'EBCMWheelSpdRear')}
      rows = [row for row in rows if not (row[2] == 0 and row[0] in wheel_addresses | {gear_address})]
      rows += [packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 2 if tick < 8 else 3 if tick < 14 else 4}),
               packer.make_can_msg('EBCMWheelSpdFront', 0, {}),
               packer.make_can_msg('EBCMWheelSpdRear', 0, {})]
    if card.CP.flags & GMFlags.PEDAL_LONG:
      sensor = bytearray(packer.make_can_msg('GAS_SENSOR', 0,
        {'INTERCEPTOR_GAS': 0., 'INTERCEPTOR_GAS2': 0., 'STATE': 0, 'COUNTER_PEDAL': tick % 4})[1])
      sensor[5] = gmcan.pedal_crc(sensor)
      rows.append((0x201, bytes(sensor), 0))
    if card.CP.flags & GMFlags.HAS_BSM:
      rows.append(packer.make_can_msg('BCMBlindSpotMonitor', 0, {}))
    if current['pedal'] != 'none':
      # The recorded paddle transition also reports physical manual/regen mode.
      # Preserve that actual forward gear; never fabricate a drive-mode CS flag.
      gear_address = packer.make_can_msg('ECMPRDNL2', 0, {})[0]
      rows = [row for row in rows if not (row[0] == gear_address and row[2] == 0)]
      rows.append(packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 6, 'ManualMode': 1}))
    rows = [row for row in rows if row[0] not in (0x1E1, 0x184)]
    rows += [gmcan.create_buttons(packer, 0, tick % 4, button),
             packer.make_can_msg('PSCMStatus', 0, {'LKATorqueDeliveredStatus': eps})]
    native.set_timer(now // 1000)
    for address, data, bus in rows:
      assert native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data)), (hex(address), bus)
    native.safety_tick()
    return rows

  def sample_native(now, *, heartbeat_lost=False):
    axis = sd.pm.messages.get('aolAxisState')
    intent = card.pm.messages.get('aolIntentWire')
    axis_bytes = axis.as_builder().to_bytes() if axis is not None else b''
    intent_bytes = intent.as_builder().to_bytes() if intent is not None else b''
    native.set_timer(now // 1000)
    plan = bridge.plan_host(4, card.CP.safetyConfigs[0].safetyParam,
      native.aol_get_request_mask(), native.aol_get_permission_mask(), now,
      axis_bytes, len(axis_bytes), intent_bytes, len(intent_bytes), sd.enabled)
    native.aol_set_host_request(plan & 0xFF)
    native.set_aol_test_heartbeat(bool(plan & 0x100))
    bridge.complete_host(native.aol_get_request_mask(), native.aol_get_permission_mask())
    mask, request = native.aol_get_permission_mask(), native.aol_get_request_mask()
    bad_rx = not native.safety_config_valid()
    relay = bool(native.get_relay_malfunction())
    panda = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
    row = panda.pandaStates[0]
    row.safetyModel, row.safetyParam = card.CP.safetyConfigs[0].safetyModel, card.CP.safetyConfigs[0].safetyParam
    row.alternativeExperience, row.controlsAllowed = 32, native.get_controls_allowed()
    row.safetyRxChecksInvalid = bad_rx
    row.heartbeatLost = heartbeat_lost  # Received board-health fixture; safety DLL has no firmware timer.
    if relay:
      row.faults = ['relayMalfunction']
    slot = PandaSlot(0, 'gm-gap-native', 4, row.safetyParam, 32, row.controlsAllowed, bad_rx, heartbeat_lost, int(relay))
    wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
    wire.aolSafetyWire = encode_safety(SafetyState(1, bool(plan & 0x400), now, now + 200_000_000,
      4, row.safetyParam, bool(mask & 1), bool(mask & 2), bool(request & 1), bool(request & 2),
      'gm-gap-native', sd.aol_session_id, now, (slot,)))
    current['wire'], current['pandas'] = wire, panda
    return mask

  def card_receive(now, rows, *, publish=True):
    peers = [current['wire'].as_reader(), current['pandas'].as_reader()]
    peers += [sd.pm.messages[name] for name in ('aolAxisState', 'onroadEvents', 'selfdriveState') if name in sd.pm.messages]
    if current['last_command'] is not None:
      peers.append(current['last_command'])
    device = messaging.new_message('deviceState', valid=sd.sm.valid['deviceState'],
                                   logMonoTime=sd.sm.logMonoTime['deviceState'])
    device.deviceState = sd.sm['deviceState']
    peers.append(device.as_reader())
    fresh = []
    for row in peers:
      signature = row.as_builder().to_bytes()
      if card_delivered.get(row.which()) != signature:
        card_delivered[row.which()] = signature
        fresh.append(row)
    card.sm.update_msgs(now / 1e9, fresh)
    def card_message(*args, **kwargs):
      kwargs.setdefault('logMonoTime', now)
      return producer_message(*args, **kwargs)

    with mock.patch.object(card_module.time, 'monotonic_ns', return_value=now), \
         mock.patch.object(card_module.time, 'monotonic', return_value=now / 1e9), \
         mock.patch.object(card_module.time, 'clock_gettime_ns', return_value=now), \
         mock.patch.object(card_module.messaging, 'new_message', side_effect=card_message), \
         mock.patch.object(card_module.messaging, 'drain_sock_raw', return_value=[b'physical-can']), \
         mock.patch.object(card_module, 'can_capnp_to_list', return_value=[(now - 1, list(steering_receipts)), (now, rows)]), \
         mock.patch.object(card_module, 'REPLAY', False), mock.patch.object(card.sm, 'update'), \
         mock.patch.object(card.RI, 'update', return_value=None):
      cs, rd = card.state_update()
      steering_receipts.clear()
      if publish:
        card.state_publish(cs, rd)
    current['last_cs'] = cs
    return cs

  def drive(now, tick, *, gap=False, missing_companion=False):
    current['gap'] = gap
    assert current['wire'] is not None and current['pandas'] is not None
    if (not gap or current['raw_ahead']) and not missing_companion and 'starpilotCarState' in card.pm.messages:
      receipt_queue.append(card.pm.messages['starpilotCarState'])
    source = card.pm.messages['carState'].as_builder()
    intent = card.pm.messages['aolIntentWire'].as_builder()
    authority = (card.pm.messages['starpilotCarState'].as_builder() if 'starpilotCarState' in card.pm.messages and
      not (gap and not current['raw_ahead']) and not missing_companion else None)
    axis, state, command = step(tick, offset_ns=now - (BASE + tick * 10_000_000), timeout=gap,
      car_state_message=None if gap else source, intent_message=intent, companion=False,
      authority_message=authority, car_output_message=card.pm.messages['carOutput'].as_builder(),
      safety_message=current['wire'], panda_state_message=current['pandas'])
    current['last_command'] = controls.pm.messages['carControl']
    return axis, state, command

  def apply_command(now, command):
    native.set_timer(now // 1000)
    card.pm.messages.pop('sendcan', None)
    # Card.step passes the decoded SubMaster reader, never Controls' builder.
    # Match the same explicit BOOTTIME/monotonic clock used in card_receive.
    received = current['last_command'].carControl
    assert received.to_dict() == command.to_dict()
    with mock.patch.object(card_module.time, 'monotonic_ns', return_value=now), \
         mock.patch.object(card_module.time, 'monotonic', return_value=now / 1e9), \
         mock.patch.object(card_module.time, 'clock_gettime_ns', return_value=now):
      card.controls_update(current['last_cs'], received)
    if 'sendcan' in card.pm.messages:
      for frame in card.pm.messages['sendcan'].sendcan:
        accepted = native.safety_tx_hook(libsafety_py.make_CANPacket(frame.address, frame.src, bytes(frame.dat)))
        if frame.address == 0x3D1 and card.CP.safetyConfigs[0].safetyParam == 157:
          # Bolt dashboard bytes are queued for the native physical RX owner.
          # Direct USB transmission must be denied, even for a valid replacement.
          assert not accepted
        else:
          assert accepted, (hex(frame.address), frame.src)
          if frame.address == 0x180:
            steering_receipts.append((frame.address, bytes(frame.dat), 128))

  native_due = BASE
  for tick in range(26):
    now = BASE + tick * 10_000_000
    if tick == 24:
      sample_native(LAST_SOURCE - NATIVE_BEFORE_SOURCE)
      native_due = LAST_SOURCE - NATIVE_BEFORE_SOURCE + 100_000_000
    rows = physical(now, tick, main=not engage or tick >= 5,
                    button=CruiseButtons.DECEL_SET if engage and tick == 12 else CruiseButtons.UNPRESS)
    if tick == 0:
      # Real parser subscriptions need the preceding physical batch, as in
      # the existing cold-start parser tests; no validity flag is forced.
      card.CI.update([(now - 1_000_000, rows)])
    if current['wire'] is None or now >= native_due:
      sample_native(now)
      native_due = now + 100_000_000
    card_receive(now, rows)
    axis, state, command = drive(now + 5_467_396, tick)
    apply_command(now + 5_467_397, command)
    current['warmup_trace'].append((tick, current['last_cs'].cruiseState.available,
      card.aol_card_intent.allowed_latch, card.aol_card_intent._main_cycle_required,
      card.aol_transport_feedback.failed, str(axis.faultReason), axis.lateralActive,
      axis.longitudinalActive, native.aol_get_request_mask(), native.aol_get_permission_mask(),
      current['last_cs'].canValid, str(current['last_cs'].gearShifter),
      current['last_cs'].steeringDisengage, current['last_cs'].brakePressed,
      current['last_cs'].gasPressed, card.pm.messages['aolIntentWire'].aolIntentWire.hex(),
      current['wire'].aolSafetyWire.hex()))
  if os.environ.get('GM_SDGM_TRACE'):
    with Path(os.environ['GM_SDGM_TRACE']).open('a') as out:
      out.write(json.dumps({'c9': c9, 'engage': engage, 'warmup': current['warmup_trace']}) + '\n')
  assert native.get_controls_allowed() == engage and native.aol_get_permission_mask() == (3 if engage else 1)
  assert axis.lateralActive and command.latActive
  assert axis.longitudinalActive == engage and command.longActive == engage
  assert state.enabled == engage and card.aol_card_intent.allowed_latch, current['warmup_trace']
  assert not card.CI.CS.steering_authority.latched
  assert decode_intent(card.pm.messages['aolIntentWire'].aolIntentWire).carStateLogMonoTime == LAST_SOURCE
  return card, sd, controls, native, current, physical, sample_native, card_receive, drive, apply_command

from opendbc.car.gm.values import CAR, GMFlags


def route_bolt_params():
  fingerprint = gen_empty_fingerprint()
  fingerprint[0].update({0x201: 6, 0x142: 8})
  fingerprint[2].update({0x320: 8, 0x180: 4})
  return CarInterface.get_params(CAR.CHEVROLET_BOLT_CC_2018_2021, fingerprint, [], True, False, False)


@pytest.mark.parametrize('pedal', ('brake', 'regen'))
@pytest.mark.parametrize('main_cycle', (False, True))
@pytest.mark.parametrize('initial_engaged', (False, True))
def test_bolt_main_startup_engagement_pedal_and_rearm(gm_host_heartbeat_bridge, pedal, main_cycle, initial_engaged):
  with OpenpilotPrefix(), ExitStack() as cleanup, mock.patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    cp = route_bolt_params()
    joined = joined_case(cleanup, gm_host_heartbeat_bridge, cp=cp, engage=initial_engaged)
    card, sd, controls, native, current, physical, sample_native, card_receive, drive, apply_command = joined
    assert card.CP.flags == 17 and card.CP.safetyConfigs[0].safetyParam == 157
    # The cold path starts with MAIN already ON and no SET. The second
    # path earns ordinary Long first, isolating its pedal return behavior.
    # Exercise physical MAIN OFF/ON as a distinct recovery path.
    for tick in range(26, 34):
      now = BASE + tick * 10_000_000
      rows = physical(now, tick, main=not main_cycle or tick >= 30)
      sample_native(now)
      card_receive(now, rows)
      axis, state, command = drive(now + 1, tick)
      apply_command(now + 2, command)
      if os.environ.get('GM_SDGM_TRACE'):
        with Path(os.environ['GM_SDGM_TRACE']).open('a') as out:
          out.write(json.dumps({'phase': 'main-cycle', 'tick': tick,
            'axis': axis.to_dict(), 'intent': card.pm.messages['aolIntentWire'].aolIntentWire.hex(),
            'native': current['wire'].aolSafetyWire.hex(),
            'allowed': card.aol_card_intent.allowed_latch,
            'mainArm': card.aol_card_intent.main_arm_ns,
            'feedbackFailed': card.aol_transport_feedback.failed,
            'feedbackSignature': card.aol_transport_feedback.signature,
            'mainRequired': card.aol_card_intent._main_cycle_required}) + '\n')
      if main_cycle and tick < 30:
        assert not axis.lateralActive and not command.latActive
        assert not card.aol_card_intent.allowed_latch
    assert axis.lateralActive and command.latActive and card.aol_card_intent.allowed_latch
    engaged = False
    for tick in range(34, 66):
      now = BASE + tick * 10_000_000
      current['pedal'] = pedal if 46 <= tick < 60 else 'none'
      rows = physical(now, tick, button=CruiseButtons.DECEL_SET if tick == 35 else CruiseButtons.UNPRESS)
      # Receive native LONG withdrawal before the corresponding host decision,
      # as in the route at408.396/408.398s; retain the genuine LAT-only grant.
      if tick in (34, 40, 45, 46, 49, 54, 59, 62):
        sample_native(now)
      card_receive(now, rows)
      axis, state, command = drive(now + 1, tick)
      if 40 <= tick < 46:
        assert state.enabled and axis.longitudinalActive and command.longActive
        engaged = True
      if tick >= 46:
        assert str(current['last_cs'].gearShifter) == ('manumatic' if tick < 60 else 'drive')
        assert current['last_cs'].brakePressed == (pedal == 'brake' and tick < 60)
        assert current['last_cs'].regenBraking == (pedal == 'regen' and tick < 60)
        assert not state.enabled and not command.longActive and not axis.longitudinalActive
        assert axis.lateralActive and command.latActive and card.aol_card_intent.allowed_latch
        assert not sd.aol_authority_lost and EventName.controlsMismatch not in sd.events.names
      apply_command(now + 2, command)
    assert engaged


from unittest.mock import patch
from openpilot.selfdrive.car import card as module
from openpilot.starpilot.aol.tests import test_gm as fixtures
from openpilot.starpilot.aol.tests.test_gm_held_native import peers
from openpilot.starpilot.aol.tests.test_gm_startup_main import cases as standard_cold_cases
NOW = BASE


def cold_cases():
  return list(standard_cold_cases()) + [route_bolt_params()]


@pytest.mark.parametrize('index', range(9))
@pytest.mark.parametrize('braking', (False, True))
@pytest.mark.parametrize('variation', ('none', 'event_expired', 'source_expired', 'native_expired',
  'critical', 'producer', 'epoch', 'session', 'native_request', 'main_off', 'settings_off', 'gas'))
def test_gm_initial_main_waits_on_original_soft_occurrence(index, braking, variation):
  with OpenpilotPrefix(), ExitStack() as cleanup, patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
    saved = Params()
    saved.put_bool('AlwaysOnLateral', True, block=True)
    card = fixtures.TestGmAol.card(cold_cases()[index], saved)
    cleanup.callback(card.vehicle_startup.close)
    packer = CANPacker(DBC[card.CP.carFingerprint][Bus.pt])
    card.ci_initialized = True
    steering_receipts = []

    def publish_sendcan(frames, valid=True):
      # This idle transport fixture confirms only the commands actually sent.
      if valid:
        steering_receipts.extend((address, bytes(data), 128)
                                 for address, data, bus in frames if address == 0x180 and bus == 0)

    card.publish_sendcan = publish_sendcan

    def packets(now, tick, main=True):
      rows = parsed_packets(card.CP, packer, now, tick, 8., main)
      if braking:
        from opendbc.car.gm.tests.test_volt_camera_control import feed_camera
        from types import SimpleNamespace
        _, extra = feed_camera(SimpleNamespace(update=lambda _: None), packer, now,
          counter=tick % 4, speed=8., brake=True, active=False, eps_status=1)
        addresses = {0xBE, 0xC9, 0xF1}
        rows = [row for row in rows if row[0] not in addresses]
        rows += [row for row in extra if row[0] in addresses and row[0] not in (0xBE, 0xC9)]
        rows.append(packer.make_can_msg('ECMAcceleratorPos', 0, {'BrakePedalPos': 10}))
        rows.append(packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': int(main), 'BrakePressed': 1}))
      if card.CP.flags & GMFlags.HAS_BSM:
        rows.append(packer.make_can_msg('BCMBlindSpotMonitor', 0, {}))
      if card.CP.flags & GMFlags.PEDAL_LONG:
        sensor = bytearray(packer.make_can_msg('GAS_SENSOR', 0,
          {'INTERCEPTOR_GAS': 700. if variation == 'gas' else 0.,
           'INTERCEPTOR_GAS2': 700. if variation == 'gas' else 0., 'STATE': 0, 'COUNTER_PEDAL': tick % 4})[1])
        sensor[5] = gmcan.pedal_crc(sensor)
        rows.append((0x201, bytes(sensor), 0))
      if variation == 'gas':
        address = packer.make_can_msg('AcceleratorPedal2', 0, {})[0]
        rows = [row for row in rows if not (row[0] == address and row[2] == 0)]
        rows.append(packer.make_can_msg('AcceleratorPedal2', 0, {'AcceleratorPedal2': 30}))
      return rows

    for tick in range(20):
      stamp = NOW - (20 - tick) * 10_000_000
      card.CI.update([(stamp, packets(stamp, tick))])

    def cycle(tick, age=None, mutate=False, recovering=False):
      now = NOW + tick * 10_000_000
      source = now - 1
      config = card.CP.safetyConfigs[0]
      slot = PandaSlot(0, 'sdgm-idle', config.safetyModel.raw, config.safetyParam, 32, False)
      native = SafetyState(1, True, now, now + 200_000_000, config.safetyModel.raw, config.safetyParam,
        False, False, False, False, 'sdgm-idle', 'gm-session',
        sourcePandaStatesMonoTime=now, pandaInventory=(slot,))
      if mutate and variation == 'native_expired':
        native = replace(native, observedMonoTime=now - 201_000_000,
                         validUntilMonoTime=now - 1, sourcePandaStatesMonoTime=now - 201_000_000)
      if mutate and variation == 'native_request':
        native = replace(native, requestedLateral=True)
      incoming, _ = peers(card.CP, card.slc_producer_session, now, controls=False,
                           native=native, sequence=tick + 1, long_active=False)
      axis = incoming.messages['aolAxisState'].as_builder()
      a = axis.aolAxisState
      a.sourceCarStateMonoTime = now - 31_000_000 if mutate and variation == 'source_expired' else source
      a.nativeAcknowledged = age is None
      a.desiredLateral = recovering
      a.lateralActive = a.desiredLongitudinal = a.longitudinalActive = False
      a.faultReason = 'none' if age is None else 'availabilityPause'
      event_ns = now if age is None else now - age
      if mutate and variation == 'event_expired':
        event_ns = now - 1_500_000_001
      a.faultEventMonoTime = event_ns
      if mutate and variation == 'producer':
        a.intentProducerSessionId = 'foreign'
      if mutate and variation == 'epoch':
        a.intentIntegrityEpoch += 1
      if mutate and variation == 'session':
        a.sessionId = a.faultSessionId = 'foreign'
      events = messaging.new_message('onroadEvents', (1 + int(braking)) if age is not None else 0,
                                     valid=True, logMonoTime=event_ns)
      if age is not None:
        events.onroadEvents[0].name = 'commIssueAvgFreq'
        events.onroadEvents[0].softDisable = True
        if braking:
          events.onroadEvents[1].name = 'pedalPressed'
          events.onroadEvents[1].userDisable = True
        if mutate and variation == 'critical':
          events.onroadEvents[0].name = 'controlsMismatch'
          events.onroadEvents[0].softDisable = False
          events.onroadEvents[0].immediateDisable = True
          a.faultReason = 'critical'
      if mutate and variation == 'settings_off':
        saved.put_bool('AlwaysOnLateral', False, block=True)
        card.aol_card_intent.settings = module.read_settings(saved)
      control = messaging.new_message('carControl', valid=True, logMonoTime=now)
      card.sm.update_msgs(now / 1e9, [incoming.messages[k] for k in ('aolSafetyWire', 'pandaStates')] +
                           [axis.as_reader(), events.as_reader(), control.as_reader()])
      rows = packets(now, tick, main=not (mutate and variation == 'main_off'))
      rows = steering_receipts + rows
      steering_receipts.clear()
      batch = messaging.log_from_bytes(module.can_list_to_can_capnp(rows)).as_builder()
      batch.logMonoTime = now
      with patch.object(module, 'REPLAY', False), \
           patch.object(module.messaging, 'drain_sock_raw', return_value=[batch.to_bytes()]), \
           patch.object(module.time, 'monotonic_ns', return_value=now), \
           patch.object(module.time, 'monotonic', return_value=now / 1e9), \
           patch.object(module.time, 'clock_gettime_ns', return_value=now), patch.object(card.sm, 'update'):
        card.step()
      assert card.CS_prev.canValid and not card.CS_prev.canTimeout
      assert card.CS_prev.brakePressed == braking
      assert card.CS_prev.gasPressed == (variation == 'gas')
      assert not card.last_actuators_output.torqueOutputCan

    cycle(0)  # MAIN already true; no button event or Long engagement.
    assert card.aol_card_intent.allowed_latch
    healthy = card.aol_transport_feedback.healthy_feedback_ns
    # Hold ONE original event occurrence while real parser/Card samples advance
    # at100Hz; independently fresh native/source messages never restamp it.
    for tick in range(1, 38):
      age = (tick + 3) * 10_000_000
      cycle(tick, age, mutate=variation not in ('none', 'gas'))
      if variation not in ('none', 'gas'):
        assert not card.aol_card_intent.allowed_latch
        return
      assert card.aol_card_intent.allowed_latch, 'cold MAIN intent was revoked by a current SOFT occurrence'
      assert card.aol_transport_feedback.healthy_feedback_ns == healthy
      assert not card.aol_transport_feedback.failed
    cycle(38)  # Healthy idle reply after a cold wait longer than native200ms.
    assert card.aol_card_intent.allowed_latch and not card.aol_transport_feedback.failed
    cycle(39, recovering=True)  # Native OFF while the fresh LAT request awaits acknowledgment.
    assert card.aol_card_intent.allowed_latch and not card.aol_transport_feedback.failed


@pytest.mark.parametrize('cp_kind', ('sdgm', 'bolt'))
def test_main_already_on_reverse_neutral_drive_autoarms_without_button_or_long(gm_host_heartbeat_bridge, cp_kind):
  with OpenpilotPrefix(), ExitStack() as cleanup, mock.patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    cp = sdgm_params(alpha=True) if cp_kind == 'sdgm' else route_bolt_params()
    card, sd, controls, native, current, *_ = joined_case(cleanup, gm_host_heartbeat_bridge, cp=cp, cold_boot=True)
    assert not native.get_controls_allowed()
    assert card.aol_card_intent.allowed_latch
    assert not card.aol_card_intent._main_cycle_required
    assert all(row[1] for row in current['warmup_trace'])
    assert all(not row[7] for row in current['warmup_trace'])
    assert not any(row[6] for row in current['warmup_trace'][:20])
    assert any(row[6] for row in current['warmup_trace'][20:])
