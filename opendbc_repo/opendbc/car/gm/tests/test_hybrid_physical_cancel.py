"""Physical Cancel drops factory cruise without canceling the Hybrid pedal owner."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.gm import gmcan
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
from opendbc.car.gm.tests.test_ordinary_cc import malibu_hybrid_params
from opendbc.car.gm.values import DBC
from opendbc.safety.tests import test_gm_hybrid_cc as native_fixture

CANCEL = (0x60AF, 0x659E, 0x6A8D, 0x6F7C)


def button(word, prefix=1):
  return bytes((0, 0, 0, prefix, 0, word >> 8, word & 255))


@pytest.mark.parametrize('profile', range(0xE800, 0xE806))
@pytest.mark.parametrize('alternative', (0, 32))
@pytest.mark.parametrize('prefix', (1, 0x41))
def test_native_physical_cancel_preserves_only_pedal_takeover(profile, alternative, prefix):
  f = native_fixture.TestGmHybridCc(methodName='runTest')
  f.setUp()
  try:
    for cancel in CANCEL:
      f.reset(profile, alternative)
      f.warm()
      if profile < 0xE804:
        f.arm()
      f.feed(stock=True)
      if profile in (0xE802, 0xE803):
        assert not f.pedal()  # Stock cruise owns propulsion until its real falling edge.
      f.rx(0x1E1, button(cancel, prefix))
      if profile in (0xE802, 0xE803):
        assert f.safety.get_controls_allowed()
        assert not f.pedal()
      elif profile < 0xE804:
        assert not f.safety.get_controls_allowed()
      f.advance(20_000)
      f.feed(stock=False)
      if profile in (0xE802, 0xE803):
        assert f.safety.get_controls_allowed()
        assert f.pedal(counter=1)  # No second SET or RES was sent.
        f.slot(0x55AE)
        assert not f.safety.get_controls_allowed()
        assert not f.pedal(counter=2)
  finally:
    f.tearDown()


@pytest.mark.parametrize('alternative', (0, 32))
@pytest.mark.parametrize('removed', (False, True))
def test_cancel_cannot_engage_or_rearm_disabled_pedal(removed, alternative):
  f = native_fixture.TestGmHybridCc(methodName='runTest')
  f.setUp()
  try:
    f.reset(0xE802 + int(removed), alternative)
    f.warm(stock=True)
    for word in CANCEL * 3 + (0x1FCC,):
      f.slot(word)
      assert not f.safety.get_controls_allowed()
      assert not f.pedal()
    f.feed(stock=False)
    assert not f.safety.get_controls_allowed()
    f.arm()
    f.slot(0x55AE)
    for word in CANCEL + (0x1FCC,):
      f.slot(word)
      assert not f.safety.get_controls_allowed()
      assert not f.pedal()
  finally:
    f.tearDown()


@pytest.mark.parametrize('guard', ('brake', 'gas', 'regen', 'main', 'gear', 'eps', 'sensor', 'stale'))
def test_physical_cancel_never_bypasses_native_pedal_guards(guard):
  f = native_fixture.TestGmHybridCc(methodName='runTest')
  f.setUp()
  try:
    f.reset(0xE802)
    f.warm()
    f.arm()
    kwargs = {'brake': {'brake': True}, 'gas': {'tracks': (1000, 500)}, 'regen': {'regen': True},
              'main': {'main': False}, 'gear': {'gear': 2}, 'eps': {'eps': 2}, 'sensor': {'missing': 0x201}}.get(guard, {})
    f.advance(110_000 if guard in ('sensor', 'stale') else 20_000)
    if guard != 'stale':
      f.feed(**kwargs)
    f.slot(CANCEL[0])
    assert not f.pedal()
  finally:
    f.tearDown()


def frames(packer, tick, *, pedal, removed, stock, words, prefix):
  rows = [f for f in pt_frames(packer) if f[0] not in (0x1E1, 0x3D1, 0x17D)]
  rows += [packer.make_can_msg('ESPStatus', 0, {'TractionControlOn': 1}),
           packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': stock, 'CruiseSetSpeed': 60}),
           packer.make_can_msg('EBCMBrakePedalPosition', 0, {}), packer.make_can_msg('EBCMRegenPaddle', 0, {})]
  if not removed:
    rows += [packer.make_can_msg('ASCMLKASteeringCmd', 2, {}), packer.make_can_msg('AEBCmd', 2, {})]
  if pedal:
    sensor = bytearray((2, 92, 1, 48, tick % 16, 0))
    sensor[-1] = gmcan.pedal_crc(sensor)
    rows.append((0x201, bytes(sensor), 0))
  rows += [(0x1E1, button(word, prefix), 0) for word in words]
  return rows


@pytest.mark.parametrize('pedal,stock_owner', ((False, False), (True, False), (False, True)))
@pytest.mark.parametrize('removed', (False, True))
@pytest.mark.parametrize('batch', (False, True))
def test_actual_parser_cancel_policy_and_batched_edges(pedal, stock_owner, removed, batch):
  from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
  cp = malibu_hybrid_params(pedal=pedal, removed=removed)
  if stock_owner:
    prepare_disable_longitudinal(cp, True)
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for tick in range(80):
    words = [0x15EE if tick % 2 else 0x1FCC]
    if tick == 70:
      words = [CANCEL[0], 0x15EE] if batch else [CANCEL[0]]
    elif 70 < tick < 74 and not batch:
      words = [CANCEL[tick - 70]]
    if tick == 77:
      words = [0x55AE]
    now = 1_000_000_000 + tick * 10_000_000
    rows = frames(packer, tick, pedal=pedal, removed=removed, stock=tick < 72,
                  words=[0x15EE] if batch and tick == 70 else words, prefix=0x41)
    packets = [(now - 1, [(0x1E1, button(CANCEL[0], 0x41), 0)])] if batch and tick == 70 else []
    state = ci.update([*packets, (now, rows)])
    if tick >= 60:
      assert state.canValid
    if tick == 70:
      assert any(str(b.type) == 'cancel' and b.pressed for b in state.buttonEvents) == (not pedal)
    if tick == 77:
      assert any(str(b.type) == 'mainCruise' and b.pressed for b in state.buttonEvents)
    if tick >= 70 and pedal:
      assert not any(str(b.type) in ('cancel', 'gapAdjustCruise') for b in state.buttonEvents)
      assert not state.buttonEnable


@pytest.mark.parametrize('removed', (False, True))
def test_parser_selfdrived_controls_native_pedal_continue_after_physical_cancel(removed):
  from openpilot.cereal import log, messaging
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture

  cp = malibu_hybrid_params(pedal=True, removed=removed)
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  f = native_fixture.TestGmHybridCc(methodName='runTest')
  f.setUp()
  f.reset(0xE802 + int(removed))
  # Real SET/release establishes native intent before exercising the running callers.
  for tick in range(90):
    now = 9_100_000_000 + tick * 10_000_000
    words = [0x30DF] if 70 <= tick < 74 else [0x15EE if tick % 2 else 0x1FCC]
    rows = frames(packer, tick, pedal=True, removed=removed, stock=True, words=words, prefix=1)
    f.safety.set_timer(now // 1000)
    f.safety.set_aol_test_heartbeat(True)
    f.safety.aol_set_host_request(3)
    for address, raw, bus in rows:
      f.rx(address, raw, bus)
    f.safety.safety_tick()
    ci.update([(now, rows)])
  assert f.safety.get_controls_allowed()
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  accepted_pedal = 0
  observations = []
  try:
    with OpenpilotPrefix(), ExitStack() as cleanup:
      cleanup.callback(case.doCleanups)
      sd, controls, step = case._authority_callers(True, cp=cp, genuine_events=True)
      receive = sd.sm.update_msgs
      last_control_stamp = 0
      intent_queue = []
      receive_optional = messaging.recv_one_or_none

      def deliver(sock):
        if sd.aol_intent_companion is not None and sock is sd.aol_intent_companion.sock:
          return intent_queue.pop(0) if intent_queue else None
        return receive_optional(sock)

      cleanup.enter_context(patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one_or_none', side_effect=deliver))

      def receive_sd(stamp, rows):
        nonlocal last_control_stamp
        intent_queue.extend(row for row in rows if row.which() == 'aolIntentWire')
        published = controls.pm.messages.get('carControl')
        rows = [row for row in rows if row.which() != 'carControl']
        if published is not None and published.logMonoTime != last_control_stamp:
          rows.append(published)
          last_control_stamp = int(published.logMonoTime)
        return receive(stamp, rows)

      cleanup.enter_context(patch.object(sd.sm, 'update_msgs', side_effect=receive_sd))
      for tick in range(20):
        now = 10_000_000_000 + tick * 10_000_000
        word = CANCEL[tick - 3] if 3 <= tick < 7 else (0x15EE if tick % 2 else 0x1FCC)
        stock = tick < 5
        rows = frames(packer, tick + 90, pedal=True, removed=removed, stock=stock, words=[word], prefix=1)
        f.safety.set_timer(now // 1000)
        f.safety.set_aol_test_heartbeat(True)
        f.safety.aol_set_host_request(3)
        for address, raw, bus in rows:
          f.rx(address, raw, bus)
        f.safety.safety_tick()
        state = ci.update([(now, rows)])
        mask = f.safety.aol_get_permission_mask()
        assert f.safety.get_controls_allowed()
        axis, sd_state, command = step(tick, observed_state=state, allowed=bool(mask & 1), long_allowed=bool(mask & 2),
                                    native_request_long=True, panda_controls_allowed=bool(f.safety.get_controls_allowed()))
        assert log.OnroadEvent.EventName.buttonCancel not in sd.events.names
        assert log.OnroadEvent.EventName.pcmDisable not in sd.events.names
        assert sd_state.enabled and command.enabled
        _, sent = ci.CC.update(command.as_reader(), ci.CS, now)
        observations.append((tick, command.enabled, command.longActive, str(command.actuators.longControlState),
                             command.actuators.accel, state.cruiseState.enabled, ci.CS.pedal_sensor_healthy,
                             mask, axis.qualified, axis.nativeAcknowledged, axis.desiredLongitudinal, axis.longitudinalActive,
                             sd.aol_fault_reason, [(address, raw.hex()) for address, raw, _ in sent if address == 0x200]))
        for address, raw, bus in sent:
          if address == 0x200:
            assert f.tx(address, raw, bus)
            if any(raw[:4]):
              assert not stock
              accepted_pedal += 1
      assert accepted_pedal > 0, repr((observations, sd.events.names, sd.aol_last_intent, sd.aol_car_state_log_ns))
  finally:
    f.tearDown()
