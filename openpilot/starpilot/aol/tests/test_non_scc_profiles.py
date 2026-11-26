"""Typed factory, physical intent and gas-family source contracts."""
import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.carstate import get_non_scc_cruise_signals
from opendbc.car.hyundai.non_scc_aol import NON_SCC_IDS, qualified, aol_word
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags
from opendbc.car import Bus
from openpilot.starpilot.aol.intent import AolSettings, AOL_TOGGLE
from openpilot.starpilot.car.hyundai.aol import policy_for, create_intent, native_accepts_cp


MATCH_ANY_LAYOUT_IDS = NON_SCC_IDS - {CAR.HYUNDAI_KONA_EV_2023_NON_SCC}


def params(identity, source=None):
  fingerprint = gen_empty_fingerprint()
  if source is not None:
    fingerprint[0][source] = 8
  return CarInterface.get_params(identity, fingerprint, [], False, False, False)


class TestNonSccProfiles(unittest.TestCase):
  def test_actual_factory_profiles_and_mismatched_gas_denied(self):
    for identity in MATCH_ANY_LAYOUT_IDS:
      for source in (None, 0x391, 0x50c):
        with self.subTest(identity=identity, source=source):
          cp = params(identity, source)
          self.assertTrue(qualified(cp))
          self.assertEqual(bool(cp.flags & HyundaiFlags.HAS_LDA_BUTTON), source is not None)
          self.assertFalse(cp.openpilotLongitudinalControl)
          self.assertTrue(cp.pcmCruise)
          before = (cp.mass, cp.wheelbase, cp.steerRatio, cp.lateralTuning.which())
          policy = policy_for(cp)
          cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
          cp.alternativeExperience |= policy.alternative_experience_addition
          self.assertEqual(cp.safetyConfigs[0].safetyParam, aol_word(cp))
          self.assertTrue(native_accepts_cp(cp, int(cp.safetyConfigs[0].safetyModel.raw), aol_word(cp)))
          self.assertEqual(before, (cp.mass, cp.wheelbase, cp.steerRatio, cp.lateralTuning.which()))
          for flag in (HyundaiFlags.EV, HyundaiFlags.HYBRID, HyundaiFlags.CANFD, HyundaiFlags.ALT_LIMITS):
            bad = cp.as_reader().as_builder()
            bad.flags = int(bad.flags) ^ int(flag)
            self.assertFalse(qualified(bad))
    self.assertFalse(qualified(params(CAR.KIA_RAY_EV)))
    self.assertFalse(qualified(params(CAR.HYUNDAI_KONA)))

  def test_physical_lkas_intent_and_native_word_binding(self):
    settings = AolSettings(True, 0., AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0))
    for identity in MATCH_ANY_LAYOUT_IDS:
      with self.subTest(identity=identity):
        cp = params(identity, 0x391)
        owner = create_intent(cp, settings)
        cs = structs.CarState(canValid=True, gearShifter='drive')
        cs.cruiseState.available = True
        owner.update(cs, now_ns=1, fault_active=False)
        self.assertFalse(owner.allowed_latch)
        cs.buttonEvents = [{'type':'lkas','pressed':False}]
        owner.update(cs, now_ns=2, fault_active=False)
        cs.buttonEvents = [{'type':'lkas','pressed':True}]
        owner.update(cs, now_ns=3, fault_active=False)
        self.assertTrue(owner.allowed_latch)
        owner.update(cs, now_ns=4, fault_active=True)
        self.assertFalse(owner.allowed_latch)
        # A repeated held input cannot rearm a faulted session.
        owner.update(cs, now_ns=5, fault_active=False)
        self.assertFalse(owner.allowed_latch)

  def test_actual_dbc_main_and_enabled_sources_are_distinct_for_ev(self):
    for identity in MATCH_ANY_LAYOUT_IDS:
      with self.subTest(identity=identity):
        cp = params(identity)
        available_msg, available_sig, enabled_msg, enabled_sig, _, _ = get_non_scc_cruise_signals(cp.flags, identity)
        dbc = DBC[identity][Bus.pt]
        packer = CANPacker(dbc)
        parser = CANParser(dbc, [(name,0) for name in dict.fromkeys((available_msg,enabled_msg))], 0)
        messages: dict[str, dict[str, float]] = {available_msg:{available_sig:1}}
        messages.setdefault(enabled_msg,{})[enabled_sig] = 1
        frames = [packer.make_can_msg(name,0,values) for name,values in messages.items()]
        parser.update([(1_000_000_000,frames)])
        self.assertEqual(parser.vl[available_msg][available_sig], 1)
        self.assertEqual(parser.vl[enabled_msg][enabled_sig], 1)
        if cp.flags & HyundaiFlags.EV:
          self.assertEqual((frames[0][0],frames[1][0]), (0x592,0x329))
        elif cp.flags & HyundaiFlags.HYBRID:
          self.assertEqual(frames[0][0],0x595)
        else:
          self.assertEqual(frames[0][0],0x260)
