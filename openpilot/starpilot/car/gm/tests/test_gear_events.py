import unittest

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_bolt_cc import feed
from opendbc.car.gm.values import BOLT_CC_WORDS, CAR, DBC, NO_ACC_BOLT_CAR, is_bolt_cc_profile
from openpilot.cereal import log, messaging
from openpilot.selfdrive.car.car_events import CarEvents
from openpilot.selfdrive.selfdrived.events import EVENTS, ET, Events
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.starpilot.aol.intent import disarming_fault
from openpilot.starpilot.car.events import event_rules
from openpilot.starpilot.car.gm.events import regen_gear_alert_text

Gear = structs.CarState.GearShifter
Name = log.OnroadEvent.EventName
PROFILES = [(identity, removed, word) for identity, words in BOLT_CC_WORDS.items()
            for removed, word in enumerate(words)]


def params(identity, removed=False):
  fingerprint = gen_empty_fingerprint()
  if not removed:
    fingerprint[2][0x180] = 4
  return CarInterface.get_params(identity, fingerprint, [], False, False, False)


def state(gear, *, enable=False):
  cs = structs.CarState(gearShifter=gear, vEgo=20.0, canValid=True, buttonEnable=enable)
  cs.cruiseState.available = True
  return cs


def events_for(cp, cs, previous):
  produced = CarEvents(cp).update(cs, previous, structs.CarControl())
  received = Events(event_rules(cp))
  received.add_from_msg(produced.to_msg())
  return produced, received


@pytest.mark.parametrize('identity,removed,word', PROFILES)
@pytest.mark.parametrize('gear', (Gear.manumatic, Gear.unknown, Gear.park, Gear.neutral, Gear.reverse, Gear.sport, Gear.brake, Gear.eco))
def test_checked_bolt_unsupported_gear_blocks_entry_and_disables_immediately(identity, removed, word, gear):
  cp = params(identity, removed)
  assert is_bolt_cc_profile(cp)
  assert cp.safetyConfigs[0].safetyParam == word
  previous = state(Gear.low)
  current = state(gear, enable=True)
  produced, received = events_for(cp, current, previous)
  for events in (produced, received):
    assert Name.wrongGear in events.names
    assert events.contains(ET.NO_ENTRY)
    assert events.contains(ET.USER_DISABLE)
    assert events.contains(ET.SOFT_DISABLE)
    message = next(event for event in events.to_msg() if event.name == Name.wrongGear)
    assert message.noEntry and message.userDisable and message.softDisable and not message.immediateDisable
    alerts = events.create_alerts([ET.NO_ENTRY, ET.USER_DISABLE])
    assert any('Gear not D' in (alert.alert_text_1, alert.alert_text_2) for alert in alerts)
    assert disarming_fault(events.to_msg(), current) == (gear == Gear.unknown)
  machine = StateMachine()
  assert not machine.update(received)[0]
  _, allowed = events_for(cp, previous, state(Gear.drive))
  allowed.add(Name.buttonEnable)
  assert machine.update(allowed)[0]
  assert not machine.update(received)[0]
  assert machine.state == log.SelfdriveState.OpenpilotState.disabled
  assert ET.USER_DISABLE in machine.current_alert_types
  assert ET.SOFT_DISABLE not in machine.current_alert_types
  _, recovered = events_for(cp, previous, current)
  assert not machine.update(recovered)[0]
  recovered.add(Name.buttonEnable)
  assert machine.update(recovered)[0]


@pytest.mark.parametrize('identity', sorted(NO_ACC_BOLT_CAR))
@pytest.mark.parametrize('removed', (False, True))
def test_conventional_bolt_raw_regen_gear_changes_alert_text_only(identity, removed):
  cp = params(identity, removed)
  ci = CarInterface(cp)
  packer = CANPacker(DBC[identity][Bus.pt])
  now = 1_000_000_000
  previous, frames = feed(ci, packer, now, camera=not removed)
  frames = [frame for frame in frames if frame[0] not in (0x1F5, 0xBD)]
  for prndl2 in (0, 1, 2, 3, 5, 7):
    for manual, paddle in ((False, False), (False, True), (True, False), (True, True)):
      now += 25_000_000
      gear = packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': prndl2, 'ManualMode': int(manual)})
      regen = packer.make_can_msg('EBCMRegenPaddle', 0, {'RegenPaddle': 2 if paddle else 0})
      current = ci.update([(now, frames + [gear, regen])])
      assert current.canValid and current.regenBraking == paddle
      _, events = events_for(cp, current, previous)
      recognized_regen = prndl2 in (5, 7)
      assert ci.CS.gm_regen_gear == recognized_regen
      alerts = [alert for alert in events.create_alerts([ET.NO_ENTRY, ET.USER_DISABLE])
                if alert.alert_type.startswith('wrongGear/')]
      assert len(alerts) == 2
      for alert in alerts:
        display = regen_gear_alert_text(alert) if ci.CS.gm_regen_gear else alert
        expected = 'Regen Gear Selected' if recognized_regen else 'Gear not D'
        assert expected in (display.alert_text_1, display.alert_text_2)
        assert display.alert_status == alert.alert_status and display.audible_alert == alert.audible_alert
        assert 'Gear not D' in (alert.alert_text_1, alert.alert_text_2)
      machine = StateMachine()
      _, allowed = events_for(cp, state(Gear.drive, enable=True), previous)
      assert machine.update(allowed)[0]
      assert not machine.update(events)[0]


def test_card_publishes_regen_gear_on_the_exact_fork_car_state_sample():
  from openpilot.selfdrive.car.tests.test_carparams_first_publish import Publisher, card_for

  identity = CAR.CHEVROLET_BOLT_CC_2022_2023
  cp = params(identity)
  ci = CarInterface(cp)
  packer = CANPacker(DBC[identity][Bus.pt])
  now = 1_000_000_000
  _, frames = feed(ci, packer, now, camera=True)
  frames = [frame for frame in frames if frame[0] != 0x1F5]
  publisher = Publisher()
  case = unittest.TestCase()
  try:
    card = card_for(case, cp, publisher, 1)
    card.CI = ci
    for prndl2 in (5, 7, 3):
      now += 25_000_000
      gear = packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': prndl2})
      observed = ci.update([(now, frames + [gear])])
      assert observed.canValid
      publisher.messages.clear()
      card.state_publish(observed, None)
      messages = {service: messaging.log_from_bytes(raw) for service, raw in publisher.messages}
      if prndl2 == 3:
        assert 'starpilotCarState' not in messages or not messages['starpilotCarState'].starpilotCarState.gmRegenGear
      else:
        companion = messages['starpilotCarState']
        assert companion.valid and companion.starpilotCarState.gmRegenGear
        assert companion.logMonoTime == companion.starpilotCarState.sourceCarStateMonoTime == messages['carState'].logMonoTime
  finally:
    case.doCleanups()


def test_regen_gear_rewords_existing_alert_when_published():
  from contextlib import ExitStack
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.starpilot.aol.intent import read_settings
  from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture

  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, _, step = case._authority_callers(True, cp=params(CAR.CHEVROLET_BOLT_CC_2022_2023), genuine_events=True)
    sd.aol_settings = read_settings(sd.params)
    unsupported = state(Gear.unknown)
    _, host, control = step(0, observed_state=unsupported)
    assert 'Gear not D' in (host.alertText1, host.alertText2)
    assert not host.enabled and not control.longActive
    assert host.alertSound == log.SelfdriveState.AudibleAlert.warningImmediate

    regen = state(Gear.manumatic)
    source_ns = sd.aol_car_state_log_ns + 10_000_000
    companion = messaging.new_message('starpilotCarState', valid=True, logMonoTime=source_ns)
    companion.starpilotCarState.sourceCarStateMonoTime = source_ns
    companion.starpilotCarState.gmRegenGear = True
    _, host, control = step(1, observed_state=regen, authority_message=companion)
    assert 'Regen Gear Selected' in (host.alertText1, host.alertText2)
    assert not host.enabled and not control.longActive
    assert host.alertSound == log.SelfdriveState.AudibleAlert.warningImmediate

    _, host, control = step(2, observed_state=regen, authority_message=companion)
    assert 'Gear not D' in (host.alertText1, host.alertText2)
    assert not host.enabled and not control.longActive
    assert host.alertSound == log.SelfdriveState.AudibleAlert.warningImmediate


@pytest.mark.parametrize('identity,removed,word', PROFILES)
@pytest.mark.parametrize('gear', (Gear.drive, Gear.low))
def test_checked_bolt_supported_gears_are_unchanged(identity, removed, word, gear):
  cp = params(identity, removed)
  produced, received = events_for(cp, state(gear), state(Gear.drive))
  assert Name.wrongGear not in produced.names
  assert Name.wrongGear not in received.names


@pytest.mark.parametrize('identity', (CAR.CHEVROLET_VOLT_CC, CAR.CHEVROLET_VOLT, CAR.CHEVROLET_BOLT_EUV))
def test_other_actual_gm_factories_keep_manumatic_admission(identity):
  cp = params(identity)
  assert not is_bolt_cc_profile(cp)
  assert event_rules(cp) == {}
  produced, received = events_for(cp, state(Gear.manumatic), state(Gear.drive))
  assert Name.wrongGear not in produced.names
  assert Name.wrongGear not in received.names


def test_rules_are_instance_local_and_wrong_profile_cannot_change_global_classification():
  cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021)
  rules = event_rules(cp)
  scoped, ordinary = Events(rules), Events()
  scoped.add(Name.wrongGear)
  ordinary.add(Name.wrongGear)
  assert scoped.contains(ET.USER_DISABLE)
  assert ordinary.contains(ET.SOFT_DISABLE) and not ordinary.contains(ET.USER_DISABLE)
  assert ET.USER_DISABLE not in EVENTS[Name.wrongGear]
  altered = structs.CarParams.new_message(**cp.to_dict())
  altered.safetyConfigs[0].safetyParam = 4
  assert event_rules(altered) == {}
  scoped.clear()
  assert not scoped.contains(ET.USER_DISABLE)


def test_pedal_factory_has_no_scoped_gear_rules():
  fingerprint = gen_empty_fingerprint()
  fingerprint[0][0x201] = 6
  fingerprint[2][0x180] = 4
  cp = CarInterface.get_params(CAR.CHEVROLET_BOLT_CC_2018_2021, fingerprint, [], False, False, False)
  assert not is_bolt_cc_profile(cp)
  assert event_rules(cp) == {}


def test_unaffected_volt_reverse_pauses_legacy_aol_output_without_clearing_intent():
  from openpilot.starpilot.aol.intent import AolCardIntent, AolSettings
  cp = params(CAR.CHEVROLET_VOLT_CC)
  owner = AolCardIntent(AolSettings(True, 0.0, 0, 0, (0, 0, 0), (0, 0, 0)))
  drive = state(Gear.drive)
  owner.update(drive)
  assert owner.allowed_latch
  reverse = state(Gear.reverse)
  _, events = events_for(cp, reverse, drive)
  assert Name.reverseGear in events.names
  assert not disarming_fault(events.to_msg(), reverse)
  owner.update(reverse, fault_active=disarming_fault(events.to_msg(), reverse))
  assert owner.allowed_latch
  assert not owner.output(reverse)[0]
  owner.update(drive)
  assert owner.allowed_latch
  assert owner.output(drive)[0]
