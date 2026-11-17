from types import SimpleNamespace as NS
from unittest.mock import patch

from openpilot.cereal import messaging
from openpilot.starpilot.controllers.mode_actions import ModeActionOwner, SwitchbackCooldown

NOW = 10_000_000_000
DRIVE = 1_000_000_000


def fixture():
  class SM(dict):
    pass
  sm = SM(deviceState=NS(started=True, startedMonoTime=DRIVE),
          carState=NS(canValid=True, canTimeout=False),
          carControl=NS(enabled=True, longActive=True, latActive=True),
          selfdriveState=NS(enabled=True))
  names = tuple(sm)
  sm.seen = dict.fromkeys(names, True)
  sm.valid = dict.fromkeys(names, True)
  sm.alive = dict.fromkeys(names, True)
  sm.logMonoTime = dict.fromkeys(names, NOW - 10_000_000)
  sm.recv_time = dict.fromkeys(names, (NOW - 5_000_000) / 1e9)
  cp = NS(carFingerprint='qualified-fixture', openpilotLongitudinalControl=True,
          passive=False, dashcamOnly=False, notCar=False)
  return sm, cp


def request(sm, cp, kind='trafficModeToggle', sequence=1, now=NOW):
  msg = messaging.new_message('slcAction', valid=True)
  msg.logMonoTime = now
  msg.slcAction.kind = kind
  msg.slcAction.controllerMode = {'version': 1, 'sessionId': 'a' * 32, 'sequence': sequence,
    'observedMonoTime': now, 'validUntilMonoTime': now + 250_000_000,
    'driveStartMonoTime': sm['deviceState'].startedMonoTime,
    'carFingerprint': cp.carFingerprint, 'sourceCarControlMonoTime': sm.logMonoTime['carControl']}
  return msg


def test_request_replay_expiry_wrong_vehicle_and_drive_never_toggle():
  sm, cp = fixture()
  owner = ModeActionOwner()
  msg = request(sm, cp)
  with patch('openpilot.starpilot.controllers.mode_actions.lateral_supported', return_value=True):
    assert owner.update(msg, sm, cp, now_ns=NOW)
    assert not owner.update(msg, sm, cp, now_ns=NOW + 100_000_000)
    assert owner.requested['traffic']
    assert not owner.update(request(sm, cp, sequence=2), sm, cp, now_ns=NOW + 251_000_000)
    changed = request(sm, cp, sequence=3)
    changed.slcAction.controllerMode.carFingerprint = 'other'
    assert not owner.update(changed, sm, cp, now_ns=NOW + 100_000_000)
    sm['deviceState'].startedMonoTime = DRIVE + 1
    assert not owner.update(msg, sm, cp, now_ns=NOW + 100_000_000)
    assert not owner.requested['traffic']


def test_temporary_authority_loss_pauses_effective_intent_without_stale_display():
  sm, cp = fixture()
  owner = ModeActionOwner()
  with patch('openpilot.starpilot.controllers.mode_actions.lateral_supported', return_value=True):
    assert owner.update(request(sm, cp), sm, cp, now_ns=NOW)
    assert owner.sample('traffic', sm, cp, now_ns=NOW).effective
    sm['carControl'].longActive = False
    assert owner.sample('traffic', sm, cp, now_ns=NOW).requested
    assert not owner.sample('traffic', sm, cp, now_ns=NOW).effective
    sm['carControl'].longActive = True
    assert owner.sample('traffic', sm, cp, now_ns=NOW).effective
    sm['deviceState'].started = False
    assert not owner.sample('traffic', sm, cp, now_ns=NOW).requested


def test_switchback_allows_qualified_lateral_only_but_unknown_vehicle_denied():
  sm, cp = fixture()
  owner = ModeActionOwner()
  sm['carControl'].longActive = False
  sm['selfdriveState'].enabled = False
  cp.openpilotLongitudinalControl = False
  with patch('openpilot.starpilot.controllers.mode_actions.lateral_supported', return_value=True):
    assert owner.update(request(sm, cp, 'switchbackModeToggle'), sm, cp, now_ns=NOW)
    assert owner.sample('switchback', sm, cp, now_ns=NOW).effective
    assert not owner.update(request(sm, cp, sequence=2), sm, cp, now_ns=NOW + 100_000_000)
  with patch('openpilot.starpilot.controllers.mode_actions.lateral_supported', return_value=False):
    assert not ModeActionOwner().update(request(sm, cp, 'switchbackModeToggle'), sm, cp, now_ns=NOW)


def test_switchback_only_rate_limits_two_advisories_and_resets_per_drive():
  owner = SwitchbackCooldown()
  def allow(event, now, drive=DRIVE, active=True):
    return owner.allow(event, active=active, drive_id=drive, now_ns=now, cooldown_ns=300_000_000_000)
  assert allow('steerSaturated', NOW)
  assert not allow('steerSaturated', NOW + 1)
  assert allow('belowSteerSpeed', NOW + 1)
  assert allow('controlsMismatch', NOW + 1)
  assert allow('steerUnavailable', NOW + 1)
  assert allow('steerSaturated', NOW + 300_000_000_000)
  assert allow('steerSaturated', NOW + 300_000_000_001, drive=DRIVE + 1)
  assert allow('steerSaturated', NOW + 300_000_000_002, active=False)


def test_actual_alpha_off_wheel_switchback_mapping_and_replay_denial():
  from openpilot.starpilot.conditional_mode.tests import test_traffic as fixtures
  from openpilot.starpilot.conditional_mode.manual import read_button_map
  from openpilot.starpilot.conditional_mode.status import settings_fingerprint
  from openpilot.starpilot.controllers.mode_actions import apply_switchback_gesture
  f = fixtures.TrafficOwnerTests('runTest')
  f.setUp()
  try:
    f.params.put('ModeButtonControl', 7, block=True)
    f.cp.openpilotLongitudinalControl = False
    f.sm['deviceState'].startedMonoTime = fixtures.DRIVE
    f.sm['carControl'].latActive = True
    f.sm['carControl'].enabled = False
    f.sm['carControl'].longActive = False
    f.sm['selfdriveState'].enabled = False
    msg = messaging.new_message('slcCruiseEvent', valid=True)
    stamp = fixtures.NOW - 5_000_000
    msg.logMonoTime = stamp
    record = msg.slcCruiseEvent
    record.kind, record.eventId = 'switchbackMode', 1
    record.producerSessionId, record.observedMonoTime = 'b' * 32, stamp
    record.trafficMode = {'version': 1, 'sessionId': 'b' * 32, 'sequence': 1,
      'observedMonoTime': stamp, 'validUntilMonoTime': stamp + 100_000_000,
      'driveStartMonoTime': fixtures.DRIVE, 'sourceCarStateMonoTime': stamp,
      'settingsFingerprint': settings_fingerprint(f.settings.refresh(fixtures.NOW)),
      'buttonMapFingerprint': read_button_map(f.params, include_ioniq_media=True).fingerprint(),
      'sourceEpoch': 0, 'sourceBootTime': fixtures.BOOT - 5_000_000,
      'toggle': True, 'button': 'mode', 'press': 'short'}
    owner = ModeActionOwner()
    assert apply_switchback_gesture(owner, msg, params=f.params, settings=f.settings, sm=f.sm,
      cp=f.cp, now_ns=fixtures.NOW, now_boot_ns=fixtures.BOOT)
    assert owner.sample('switchback', f.sm, f.cp, now_ns=fixtures.NOW).effective
    assert not apply_switchback_gesture(owner, msg, params=f.params, settings=f.settings, sm=f.sm,
      cp=f.cp, now_ns=fixtures.NOW, now_boot_ns=fixtures.BOOT)
    # Generic Switchback authority must not grant an Ioniq physical-media source.
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR
    from openpilot.starpilot.controllers.mode_actions import authority
    other_cp = interfaces[CAR.TOYOTA_RAV4_TSS2].get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
    other_cp.openpilotLongitudinalControl = False
    assert authority(f.sm, other_cp, fixtures.NOW, 'switchback')
    assert not apply_switchback_gesture(ModeActionOwner(), msg, params=f.params, settings=f.settings, sm=f.sm,
      cp=other_cp, now_ns=fixtures.NOW, now_boot_ns=fixtures.BOOT)
    f.params.put('ModeButtonControl', 0, block=True)
    assert not apply_switchback_gesture(ModeActionOwner(), msg, params=f.params, settings=f.settings, sm=f.sm,
      cp=f.cp, now_ns=fixtures.NOW, now_boot_ns=fixtures.BOOT)
  finally:
    f.doCleanups()


def test_controller_traffic_without_media_assignment_uses_existing_qualified_profile_owner():
  from openpilot.starpilot.conditional_mode.tests import test_traffic as fixtures
  f = fixtures.TrafficOwnerTests('runTest')
  f.setUp()
  try:
    verdict = f.owner.sample(None, params=f.params, settings=f.settings, sm=f.sm, cp=f.cp,
      drive_id=fixtures.DRIVE, now_mono_ns=fixtures.NOW, now_boot_ns=fixtures.BOOT, controller_toggle=True)
    assert verdict.controller_source and verdict.requested and verdict.effective and verdict.profile_mode
    f.sm['carControl'].longActive = False
    paused = f.owner.sample(None, params=f.params, settings=f.settings, sm=f.sm, cp=f.cp,
      drive_id=fixtures.DRIVE, now_mono_ns=fixtures.NOW, now_boot_ns=fixtures.BOOT)
    assert paused.requested and paused.effective is None and paused.profile_mode is None
    f.sm['carControl'].longActive = True
    assert f.owner.sample(None, params=f.params, settings=f.settings, sm=f.sm, cp=f.cp,
      drive_id=fixtures.DRIVE, now_mono_ns=fixtures.NOW, now_boot_ns=fixtures.BOOT).effective
    f.cp.openpilotLongitudinalControl = False
    assert not f.owner.sample(None, params=f.params, settings=f.settings, sm=f.sm, cp=f.cp,
      drive_id=fixtures.DRIVE, now_mono_ns=fixtures.NOW, now_boot_ns=fixtures.BOOT).requested
  finally:
    f.doCleanups()


def test_leased_switchback_status_rejects_expiry_reorder_and_retired_session():
  from openpilot.starpilot.controllers.mode_actions import ModeIntent, publish_switchback, SwitchbackStatusOwner
  status = SwitchbackStatusOwner()
  first = publish_switchback(None, ModeIntent(DRIVE, True, True), session='a' * 32, sequence=2,
    now_ns=NOW, source_car_control_ns=NOW - 1)
  assert status.sample(first, drive_id=DRIVE, now_ns=NOW)
  assert not status.sample(first, drive_id=DRIVE, now_ns=NOW + 100_000_001)
  older = publish_switchback(None, ModeIntent(DRIVE, True, True), session='a' * 32, sequence=1,
    now_ns=NOW, source_car_control_ns=NOW - 1)
  assert not status.sample(older, drive_id=DRIVE, now_ns=NOW)
  restart = publish_switchback(None, ModeIntent(DRIVE, True, True), session='b' * 32, sequence=1,
    now_ns=NOW + 1, source_car_control_ns=NOW)
  assert not status.sample(restart, drive_id=DRIVE, now_ns=NOW + 1)
  assert status.sample(restart, drive_id=DRIVE, now_ns=NOW + 1)
  assert not status.sample(first, drive_id=DRIVE, now_ns=NOW + 1)


def test_switchback_payload_reader_matches_generic_envelope_metadata():
  from openpilot.cereal import log
  from openpilot.starpilot.controllers.mode_actions import ModeIntent, publish_switchback, SwitchbackStatusOwner

  # Actual SubMaster payload shape; compare to the existing public envelope API.
  for valid, age, stamp_delta, drive, expected in (
      (True, 0, 0, DRIVE, True), (False, 0, 0, DRIVE, False),
      (True, 100_000_000, 0, DRIVE, True), (True, 100_000_001, 0, DRIVE, False),
      (True, 0, -1, DRIVE, False), (True, 0, 0, DRIVE + 1, False)):
    message = publish_switchback(None, ModeIntent(DRIVE, True, True), session='a' * 32,
      sequence=2, now_ns=NOW, source_car_control_ns=NOW - 1)
    message.valid, message.logMonoTime = valid, NOW + stamp_delta
    with log.Event.from_bytes(message.to_bytes()) as event:
      generic, payload = SwitchbackStatusOwner(), SwitchbackStatusOwner()
      assert generic.sample(event, drive_id=drive, now_ns=NOW + age) is expected
      assert payload.sample_state(event.slcState, drive_id=drive, now_ns=NOW + age,
        event_ns=event.logMonoTime, valid=event.valid) is expected
      assert (payload.drive, payload.last, payload.retired) == (generic.drive, generic.last, generic.retired)
      if valid:
        bare = SwitchbackStatusOwner()
        assert bare.sample(event.slcState, drive_id=drive, now_ns=NOW + age, event_ns=event.logMonoTime) is expected
        assert (bare.drive, bare.last, bare.retired) == (payload.drive, payload.last, payload.retired)


def test_switchback_payload_reader_preserves_duplicates_retirement_and_drive_reset():
  from openpilot.cereal import log
  from openpilot.starpilot.controllers.mode_actions import ModeIntent, publish_switchback, SwitchbackStatusOwner

  generic, payload = SwitchbackStatusOwner(), SwitchbackStatusOwner()
  for session, sequence, observed, drive, expected in (
      ('a', 2, NOW, DRIVE, True), ('a', 2, NOW, DRIVE, True),
      ('a', 1, NOW, DRIVE, False), ('b', 1, NOW + 1, DRIVE, False),
      ('b', 1, NOW + 1, DRIVE, True), ('a', 2, NOW, DRIVE, False),
      ('a', 2, NOW, 0, False), ('a', 2, NOW, DRIVE, True)):
    message = publish_switchback(None, ModeIntent(DRIVE, True, True), session=session * 32,
      sequence=sequence, now_ns=observed, source_car_control_ns=observed - 1)
    with log.Event.from_bytes(message.to_bytes()) as event:
      assert generic.sample(event, drive_id=drive, now_ns=NOW + 2) is expected
      assert payload.sample_state(event.slcState, drive_id=drive, now_ns=NOW + 2,
        event_ns=event.logMonoTime) is expected
      assert (payload.drive, payload.last, payload.retired) == (generic.drive, generic.last, generic.retired)


def test_controller_dispatch_requires_live_producer_and_binds_current_control_stamp():
  from unittest.mock import Mock
  from openpilot.starpilot.controllers.mode_actions import ModeActionPublisher, ModeIntent, publish_switchback
  sm, cp = fixture()
  publisher, sender = ModeActionPublisher(), Mock()
  with patch('openpilot.starpilot.controllers.mode_actions.lateral_supported', return_value=True):
    assert not publisher.dispatch('switchback', sm, cp, sender, now_ns=NOW)
    sender.send.assert_not_called()
    state = publish_switchback(None, ModeIntent(DRIVE, False, False), session='b' * 32, sequence=1,
                               now_ns=NOW, source_car_control_ns=sm.logMonoTime['carControl'])
    sm['slcState'] = state.slcState
    for name in ('seen', 'valid', 'alive'):
      getattr(sm, name)['slcState'] = True
    sm.logMonoTime['slcState'] = NOW
    sm.recv_time['slcState'] = NOW / 1e9
    assert publisher.dispatch('switchback', sm, cp, sender, now_ns=NOW)
    event = sender.send.call_args.args[1]
    assert str(event.slcAction.kind) == 'switchbackModeToggle'
    assert event.slcAction.controllerMode.sourceCarControlMonoTime == sm.logMonoTime['carControl']
    assert not publisher.dispatch('traffic', sm, cp, sender, now_ns=NOW + 101_000_000)
    assert sender.send.call_count == 1


def test_actual_card_alpha_off_short_wheel_gesture_has_lateral_only_authority():
  from openpilot.selfdrive.car.tests import test_conditional_manual_event as fixtures
  from opendbc.car.hyundai.values import CAR, HyundaiFlags
  from openpilot.starpilot.conditional_mode.card_input import conditional_traffic_candidate
  from openpilot.starpilot.conditional_mode.manual import ButtonTracker, Button, Gesture, Press
  f = fixtures.CardManualEventTests('runTest')
  f.setUp()
  try:
    f.cp.carFingerprint = CAR.HYUNDAI_IONIQ_6
    f.cp.safetyConfigs = [{"safetyModel": "hyundaiCanfd"}]
    f.cp.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    f.cp.openpilotLongitudinalControl = False
    f.sm['carControl'].enabled = f.sm['carControl'].longActive = False
    f.sm['carControl'].latActive = True
    f.params.put('ModeButtonControl', 7, block=True)
    tracker = ButtonTracker()
    def pulse(mode, stamp, action=7):
      return conditional_traffic_candidate(fixtures.state(), tracker, f.params, f.owner, f.cp, f.sm,
        now_ns=f.now, media=f.media((mode, False, stamp)), action=action)
    assert not pulse(False, 1_000_000_000)[0]
    assert not pulse(True, 1_200_000_000)[0]
    result = pulse(False, 1_400_000_000)
    assert result[0] and result[1] == Gesture(Button.MODE, Press.SHORT)
    assert pulse(False, 1_600_000_000, action=6) is None
    f.sm['carControl'].latActive = False
    assert not pulse(True, 1_800_000_000)[0]
    assert not pulse(False, 2_000_000_000)[0]
    f.cp.carFingerprint = 'unknown'
    assert pulse(False, 2_200_000_000) is None
  finally:
    f.doCleanups()
