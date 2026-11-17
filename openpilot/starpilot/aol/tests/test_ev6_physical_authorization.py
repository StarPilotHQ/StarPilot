"""EV6 physical authorization and button intent."""
import unittest
from types import SimpleNamespace
from typing import cast

from opendbc.car import Bus, structs
from opendbc.car.hyundai.ev6_aol import Ev6PhysicalAuthorization, qualified
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.values import Buttons, CAR, DBC, HyundaiFlags
from opendbc.can.packer import CANPacker
from openpilot.starpilot.aol.intent import AolSettings, AOL_TOGGLE, PAUSE_LATERAL, PAUSE_LONGITUDINAL
from openpilot.starpilot.car.hyundai.ev6_intent import Ev6CardIntent


def ev6_params():
  # Factory remains stock. The actual captured/UDS prepublication owner alone promotes alpha.
  from openpilot.starpilot.tests.test_ev6_startup import TestEV6Startup
  ci, holder, _ = TestEV6Startup().exercise(mode='sent_restore')
  try:
    assert ci.CP.openpilotLongitudinalControl and ci.CP.safetyConfigs[-1].safetyParam == 0x15
    return ci.CP.as_reader().as_builder()
  finally:
    holder.close()


class TestEv6PhysicalAuthorization(unittest.TestCase):
  def test_exact_final_word_and_identity(self):
    cp = ev6_params()
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x15)
    self.assertTrue(qualified(cp))
    cp.safetyConfigs[0].safetyParam = 0x815
    cp.alternativeExperience = 32
    self.assertTrue(qualified(cp, marked_only=True))
    for identity in (CAR.KIA_EV6_2025, CAR.KIA_EV9, CAR.HYUNDAI_IONIQ_5):
      bad = cp.as_reader().as_builder()
      bad.carFingerprint = identity
      self.assertFalse(qualified(bad))
    for flag in (HyundaiFlags.CANFD_ALT_BUTTONS, HyundaiFlags.CANFD_LKA_STEER_MSG_ALT,
                 HyundaiFlags.CANFD_ANGLE_STEERING, HyundaiFlags.CCNC):
      bad = cp.as_reader().as_builder()
      bad.flags |= int(flag)
      self.assertFalse(qualified(bad))
    bad = cp.as_reader().as_builder()
    bad.openpilotLongitudinalControl, bad.pcmCruise = False, True
    self.assertFalse(qualified(bad))

  def test_actual_parser_preserves_each_physical_sample(self):
    cp = ev6_params()
    interface = CarInterface(cp)
    state, parsers = interface.CS, interface.can_parsers
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    warm_ns = 1_000_000
    warm = [CANPacker(parser.dbc.name).make_can_msg(parser.dbc.addr_to_msg[address].name,
            parser.bus, {'COUNTER': 0}) for parser in parsers.values() for address in sorted(parser.addresses)]
    interface.update([(warm_ns, warm)])
    self.assertEqual(parsers[Bus.pt].can_invalid_cnt, 0)
    # Let required non-button bodies expire while the ordered button source remains current.
    target_ns = warm_ns + int(max(message.timeout_threshold for message in parsers[Bus.pt].message_states.values()
                                if not message.ignore_alive)) + 1
    samples = [(0, 0, Buttons.NONE), (1, 0, Buttons.NONE), (0, 0, Buttons.NONE),
               (0, 1, Buttons.SET_DECEL), (0, 0, Buttons.NONE)]
    messages = [packer.make_can_msg(state.cruise_btns_msg_canfd, CanBus(cp).ECAN,
                {'ADAPTIVE_CRUISE_MAIN_BTN': main, 'LDA_BTN': lkas, 'CRUISE_BUTTONS': cruise, 'COUNTER': counter})
                for counter, (main, lkas, cruise) in enumerate(samples, start=1)]
    self.assertTrue(cp.flags & HyundaiFlags.CANFD)
    interface.update([(target_ns, messages)])
    self.assertEqual(parsers[Bus.pt].can_invalid_cnt, 1)
    self.assertTrue(state.ev6_aol_source_healthy)
    self.assertEqual(list(state.ev6_aol_samples), samples)
    interface.update([(target_ns + 10_000_000, [])])
    self.assertEqual(parsers[Bus.pt].can_invalid_cnt, 2)
    self.assertEqual(state.ev6_aol_samples, ())
    interface.update([(target_ns + state.ev6_aol_timeout_ns + 1_000_000_000, [])])
    self.assertFalse(state.ev6_aol_source_healthy)

  def test_authorization_is_union_and_optional_release_not_desired_intent(self):
    for preference in (False, True):
      for button in (Buttons.SET_DECEL, Buttons.RES_ACCEL):
        owner = Ev6PhysicalAuthorization(lkas_on_engage=preference)
        owner.observe(0, 0, button, main_available=True)
        self.assertFalse(owner.authorized)
        owner.observe(0, 0, Buttons.NONE, main_available=True)
        self.assertEqual(owner.authorized, preference)
        owner.observe(1, 0, Buttons.NONE, main_available=True)
        self.assertTrue(owner.authorized)
        owner.observe(1, 0, Buttons.NONE, main_available=True)
        self.assertTrue(owner.authorized)  # Held MAIN is not a second toggle.
        owner.observe(0, 0, Buttons.NONE, main_available=False)
        self.assertEqual(owner.authorized, preference)  # MAIN loss preserves LKAS latch.
        owner.revoke()
        self.assertFalse(owner.authorized)


class TestEv6ConfiguredDesire(unittest.TestCase):
  def owner(self, *, main=0, lkas=0, enabled=True, cancel=(0, 0, 0)):
    return Ev6CardIntent(AolSettings(enabled, 0., lkas, main, (0, 0, 0), cancel))

  def sample(self, owner, samples, *, available=True, active=False, events=(), valid=True, fault=False, acc_fault=False, cruise_enabled=False, brake=False):
    state = structs.CarState(canValid=valid, canTimeout=False, gearShifter='drive', buttonEvents=list(events), brakePressed=brake)
    state.cruiseState.available = available
    state.cruiseState.enabled = cruise_enabled
    state.accFaulted = acc_fault
    now = getattr(owner, '_test_clock_ns', 90) + 10
    owner._test_clock_ns = now
    owner.observe_physical_samples(SimpleNamespace(ev6_aol_sample_stamp_ns=now,
      ev6_aol_source_healthy=True, ev6_aol_timeout_ns=1_000_000_000, ev6_aol_samples=samples), now_ns=now)
    owner.update(state, now_ns=now, fault_active=fault)
    owner.observe_full_engagement(state, enabled=active, active=active, source_ns=now,
                                 observation_valid=True, now_ns=now, fault_active=bool(fault))
    return state

  def test_exact_main_and_lkas_action_ownership(self):
    for button in ('main', 'lkas'):
      for action in (0, AOL_TOGGLE, PAUSE_LATERAL, PAUSE_LONGITUDINAL):
        owner = self.owner(**{button: action})
        self.sample(owner, [(0, 0, Buttons.NONE)])
        pressed = (1, 0, Buttons.NONE) if button == 'main' else (0, 1, Buttons.NONE)
        self.sample(owner, [pressed, (0, 0, Buttons.NONE)])
        self.assertTrue(owner.physical.authorized)
        self.assertTrue(owner.allowed_latch)
        self.assertEqual(owner.pause_lateral, action == PAUSE_LATERAL)
        self.assertEqual(owner.pause_longitudinal, action == PAUSE_LONGITUDINAL)
        # The unassigned other input cannot toggle a managed desired owner.
        if action == AOL_TOGGLE:
          other = self.owner(**{button: action})
          self.sample(other, [(0, 0, Buttons.NONE)])
          opposite = (0, 1, Buttons.NONE) if button == 'main' else (1, 0, Buttons.NONE)
          self.sample(other, [opposite, (0, 0, Buttons.NONE)])
          self.assertTrue(other.physical.authorized)
          self.assertFalse(other.allowed_latch)

  def test_default_desire_depends_on_actual_main_not_only_physical_arm(self):
    owner = self.owner()
    self.sample(owner, [(0, 0, Buttons.NONE)], available=False)
    cs = self.sample(owner, [(0, 1, Buttons.NONE), (0, 0, Buttons.NONE)], available=False)
    self.assertTrue(owner.physical.authorized)
    self.assertFalse(owner.output(cs)[0])
    cs = self.sample(owner, [], available=True)
    self.assertTrue(owner.output(cs)[0])
    cs = self.sample(owner, [], available=False)
    self.assertFalse(owner.output(cs)[0])

  def test_set_release_authorization_and_actual_active_engagement_are_distinct(self):
    for button in (Buttons.SET_DECEL, Buttons.RES_ACCEL):
      for lkas_action in (0, AOL_TOGGLE):
        owner = self.owner(lkas=lkas_action)
        self.sample(owner, [(0, 0, Buttons.NONE)])
        self.sample(owner, [(0, 0, button), (0, 0, Buttons.NONE)])
        self.assertEqual(owner.physical.authorized, lkas_action == AOL_TOGGLE)
        self.assertFalse(owner.allowed_latch)  # Physical release alone never forces desire.
        self.sample(owner, [], active=True)
        self.assertTrue(owner.allowed_latch)  # A successful fresh full engagement arms either configured mode.

  def test_raw_manual_off_wins_over_full_engagement_without_net_button_event(self):
    owner = self.owner(main=AOL_TOGGLE)
    self.sample(owner, [(0, 0, Buttons.NONE)])
    self.sample(owner, [(0, 1, Buttons.NONE), (0, 0, Buttons.NONE)])
    self.sample(owner, [(1, 0, Buttons.NONE), (0, 0, Buttons.NONE)])
    self.assertTrue(owner.allowed_latch and owner.physical.authorized)
    cs = self.sample(owner, [(1, 0, Buttons.NONE), (0, 0, Buttons.NONE)], active=True)
    self.assertTrue(owner.physical.authorized)
    self.assertFalse(cs.buttonEvents)
    self.assertFalse(owner.allowed_latch or owner.output(cs)[0])
    self.sample(owner, [], active=True)
    self.assertFalse(owner.allowed_latch)

  def test_auxiliary_off_after_pending_activation_cannot_rearm_same_engagement(self):
    owner = self.owner(lkas=AOL_TOGGLE)
    self.sample(owner, [(0, 0, Buttons.NONE)])
    cs = self.sample(owner, [(0, 1, Buttons.NONE), (0, 0, Buttons.NONE)])
    self.assertTrue(owner.allowed_latch and owner.physical.authorized)
    now = owner._test_clock_ns + 1
    owner.observe_full_engagement(cs, enabled=True, active=False, source_ns=now,
                                 observation_valid=True, now_ns=now, fault_active=False)
    owner._perform(AOL_TOGGLE)
    self.assertFalse(owner.allowed_latch)
    self.sample(owner, [], active=True)
    self.assertFalse(owner.allowed_latch)

  def test_cold_held_no_source_and_unphysical_auxiliary_cannot_create_output(self):
    for main_action, lkas_action in ((0, 0), (AOL_TOGGLE, 0), (0, AOL_TOGGLE),
                                     (PAUSE_LATERAL, PAUSE_LONGITUDINAL)):
      owner = self.owner(main=main_action, lkas=lkas_action, cancel=(AOL_TOGGLE, 0, 0))
      cs = self.sample(owner, [(1, 0, Buttons.NONE)])
      self.assertFalse(owner.output(cs)[0])
      owner._perform(AOL_TOGGLE)
      self.assertFalse(owner.output(cs)[0])
      cs = self.sample(owner, [(0, 0, Buttons.NONE)], valid=False)
      self.assertFalse(owner.output(cs)[0])
      owner.update_auxiliary(cs, media=None, media_eligible=False, fault_active=False)
      self.assertFalse(owner.output(cs)[0])

  def test_cancel_and_custom_proposals_remain_behind_physical_gate(self):
    for main, lkas in ((0, 0), (AOL_TOGGLE, 0), (0, AOL_TOGGLE)):
      with self.subTest(main=main, lkas=lkas):
        owner = self.owner(main=main, lkas=lkas)
        self.sample(owner, [(0, 0, Buttons.NONE)])
        cs = self.sample(owner, [(1, int(lkas == AOL_TOGGLE), Buttons.NONE), (0, 0, Buttons.NONE)])
        self.assertTrue(owner.output(cs)[0])
        for pressed, raw in ((True, Buttons.CANCEL), (False, Buttons.NONE)):
          cancel = structs.CarState.ButtonEvent(type='cancel', pressed=pressed)
          cs = self.sample(owner, [(0, 0, raw)], events=[cancel])
          owner.update_auxiliary(cs, fault_active=False)
          self.assertTrue(owner.output(cs)[0])
        owner.settings = AolSettings(True, 0., lkas, main, (0, 0, 0), (AOL_TOGGLE, 0, 0))
        for pressed, raw in ((True, Buttons.CANCEL), (False, Buttons.NONE)):
          cancel = structs.CarState.ButtonEvent(type='cancel', pressed=pressed)
          cs = self.sample(owner, [(0, 0, raw)], events=[cancel])
          owner.update_auxiliary(cs, fault_active=False)
        self.assertFalse(owner.output(cs)[0])
        owner.physical.revoke()
        owner._perform(AOL_TOGGLE)
        self.assertFalse(owner.output(cs)[0])


  def test_retained_fault_cannot_be_bypassed_by_default_or_fresh_mapped_press(self):
    for main in (0, AOL_TOGGLE):
      owner = self.owner(main=main)
      self.sample(owner, [(0, 0, Buttons.NONE)])
      cs = self.sample(owner, [(1, 0, Buttons.NONE), (0, 0, Buttons.NONE)])
      self.assertTrue(owner.output(cs)[0])
      cs = self.sample(owner, [], fault=True)
      self.assertFalse(owner.output(cs)[0])
      cs = self.sample(owner, [(0, 0, Buttons.NONE), (1, 0, Buttons.NONE), (0, 0, Buttons.NONE)], fault=None)
      self.assertTrue(owner._fault_inhibit)
      self.assertFalse(owner.allowed_latch)
      self.assertFalse(owner.output(cs)[0])
      owner._perform(AOL_TOGGLE)
      self.assertFalse(owner.allowed_latch)
      cs = self.sample(owner, [(0, 0, Buttons.NONE), (1, 0, Buttons.NONE), (0, 0, Buttons.NONE)], fault=False)
      self.assertFalse(owner._fault_inhibit)
      self.assertTrue(owner.output(cs)[0])
      cs = self.sample(owner, [(0, 0, Buttons.NONE), (1, 0, Buttons.NONE)], acc_fault=True)
      self.assertFalse(owner.allowed_latch)
      self.assertFalse(owner.output(cs)[0])

  def test_raw_neutral_is_required_after_cold_held_and_restart(self):
    for main in (0, AOL_TOGGLE):
      owner = self.owner(main=main)
      cs = self.sample(owner, [(1, 0, Buttons.NONE)])
      self.assertFalse(owner._raw_neutral_seen)
      self.assertFalse(owner.output(cs)[0])
      cs = self.sample(owner, [(1, 0, Buttons.NONE)])
      self.assertFalse(owner.output(cs)[0])
      cs = self.sample(owner, [(0, 0, Buttons.NONE), (1, 0, Buttons.NONE), (0, 0, Buttons.NONE)])
      self.assertTrue(owner.output(cs)[0])
      cs = self.sample(owner, [], valid=False)
      self.assertFalse(owner.output(cs)[0])
      cs = self.sample(owner, [(1, 0, Buttons.NONE)])
      self.assertFalse(owner.output(cs)[0])
      cs = self.sample(owner, [(0, 0, Buttons.NONE), (1, 0, Buttons.NONE), (0, 0, Buttons.NONE)])
      self.assertTrue(owner.output(cs)[0])

    for cruise in (Buttons.SET_DECEL, Buttons.RES_ACCEL):
      with self.subTest(cruise=cruise):
        owner = self.owner(lkas=AOL_TOGGLE)
        for restart in (False, True):
          if restart:
            self.sample(owner, [], valid=False)
          cs = self.sample(owner, [(0, 0, cruise)], active=False)
          self.assertFalse(owner._raw_neutral_seen)
          self.assertFalse(owner.physical.authorized)
          cs = self.sample(owner, [(0, 0, Buttons.NONE)], active=True)
          self.assertTrue(owner._raw_neutral_seen)
          self.assertFalse(owner.physical.authorized)
          self.assertFalse(owner.output(cs)[0])
          self.sample(owner, [(0, 0, cruise)], active=False)
          cs = self.sample(owner, [(0, 0, Buttons.NONE)], active=True)
          self.assertTrue(owner.physical.authorized)
          self.assertTrue(owner.output(cs)[0])

  def test_main_derived_auxiliary_off_persists_until_explicit_on(self):
    owner = self.owner()
    self.sample(owner, [(0, 0, Buttons.NONE)])
    cs = self.sample(owner, [(1, 0, Buttons.NONE), (0, 0, Buttons.NONE)])
    self.assertTrue(owner.output(cs)[0])
    owner._perform(AOL_TOGGLE)
    self.assertFalse(owner._controller_override)
    cs = self.sample(owner, [])
    self.assertFalse(owner.allowed_latch)
    self.assertFalse(owner.output(cs)[0])
    owner._perform(AOL_TOGGLE)
    self.assertTrue(owner._controller_override)
    cs = self.sample(owner, [])
    self.assertTrue(owner.output(cs)[0])


  def test_second_lkas_toggle_with_ordinary_engagement_pauses_steering(self):
    owner = self.owner(lkas=AOL_TOGGLE)
    self.sample(owner, [(0, 0, Buttons.NONE)])
    cs = self.sample(owner, [(0, 1, Buttons.NONE), (0, 0, Buttons.NONE)])
    self.assertTrue(owner.output(cs)[0])
    self.sample(owner, [], active=True, cruise_enabled=True)
    cs = self.sample(owner, [(0, 1, Buttons.NONE), (0, 0, Buttons.NONE)], active=True, cruise_enabled=True)
    self.assertFalse(owner.physical.authorized)
    self.assertFalse(owner.allowed_latch)
    self.assertTrue(owner.pause_lateral)
    self.assertFalse(owner.output(cs)[0])
    owner._perform(AOL_TOGGLE)
    self.assertFalse(owner.allowed_latch)  # ON remains denied without physical authorization.
    self.assertTrue(owner.pause_lateral)

  def test_current_params_thread_refreshes_optional_release_without_granting_authority(self):
    import tempfile
    import threading
    from unittest.mock import patch
    from openpilot.common.params import Params
    from openpilot.selfdrive.car.card import Car
    from openpilot.selfdrive.car.cruise import VCruiseHelper
    from openpilot.starpilot.aol.intent import read_settings

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('AlwaysOnLateral', True, block=True)
      params.put('LKASButtonControl', 0, block=True)
      params.put('MainCruiseButtonControl', 0, block=True)
      card = Car.__new__(Car)
      card.params, card.CP = params, ev6_params()
      card.CI = cast(CarInterfaceBase, SimpleNamespace(CP=card.CP, CC=None))
      card.slc_transport_available = False
      card.v_cruise_helper = VCruiseHelper(card.CP)
      card.aol_card_intent = Ev6CardIntent(read_settings(params))
      owner = card.aol_card_intent
      cp_before = card.CP.as_reader().as_builder().to_bytes()
      def refresh():
        stop = threading.Event()
        with patch('openpilot.selfdrive.car.card.time.sleep', side_effect=lambda _: stop.set()):
          card.params_thread(stop)
      self.sample(owner, [(0, 0, Buttons.NONE)])
      params.put('LKASButtonControl', AOL_TOGGLE, block=True)
      refresh()
      cs = self.sample(owner, [])
      self.assertTrue(owner.physical.lkas_on_engage)
      self.assertFalse(owner.physical.authorized)
      self.assertFalse(owner.output(cs)[0])
      cs = self.sample(owner, [(0, 0, Buttons.SET_DECEL), (0, 0, Buttons.NONE)])
      self.assertTrue(owner.physical.authorized)
      self.assertFalse(owner.output(cs)[0])
      cs = self.sample(owner, [], active=True)
      self.assertTrue(owner.output(cs)[0])
      for action, enabled in ((0, True), (PAUSE_LONGITUDINAL, True), (AOL_TOGGLE, False)):
        self.sample(owner, [], valid=False)
        params.put('LKASButtonControl', action, block=True)
        params.put_bool('AlwaysOnLateral', enabled, block=True)
        refresh()
        cs = self.sample(owner, [(0, 0, Buttons.NONE), (0, 0, Buttons.RES_ACCEL), (0, 0, Buttons.NONE)])
        self.assertFalse(owner.physical.lkas_on_engage)
        self.assertFalse(owner.physical.authorized)
        self.assertFalse(owner.output(cs)[0])
      self.assertEqual(card.CP.as_reader().as_builder().to_bytes(), cp_before)
