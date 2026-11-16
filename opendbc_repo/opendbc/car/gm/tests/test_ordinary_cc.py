import unittest
import numpy as np

from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.values import CAR, ORDINARY_CC_CAR, is_ordinary_cc_profile, CruiseButtons, GMFlags
from opendbc.car.gm.ordinary_cc import button_request, ButtonCadence, policy_for
from opendbc.car.gm.feature_capabilities import longitudinal_supported
from opendbc.car.gm.lateral import lane_centering_supported
from opendbc.car.gm.aol import qualified_gm
from openpilot.starpilot.lateral.controller_selection import policy_for as lateral_policy_for


def qualified_frames(packer, counter):
  from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
  from opendbc.car.gm.gmcan import create_buttons
  frames = [frame for frame in pt_frames(packer, counter=counter) if frame[0] not in (0x1E1, 0x34A)]
  frames.append(create_buttons(packer, 0, counter, CruiseButtons.UNPRESS))
  frames.append(packer.make_can_msg('EBCMWheelSpdRear', 0, {'RLWheelSpd': 60, 'RRWheelSpd': 60, 'RLWheelDir': 1, 'RRWheelDir': 1}))
  return frames


def warm_ordinary_native(test, ci, packer, safety):
  from opendbc.car.gm.gmcan import create_buttons
  from opendbc.car.gm.tests.test_bolt_cc import native
  # Authentic physical SET release before the sender loop, with two current RX batches.
  # No host transmission or controls/clock-history grant is fabricated.
  for now, counter, button in ((980_000_000, 3, CruiseButtons.DECEL_SET),
                               (990_000_000, 0, CruiseButtons.UNPRESS)):
    frames = [f for f in qualified_frames(packer, counter) if f[0] not in (0x1E1, 0x3D1)]
    frames.append(packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': 1, 'CruiseSetSpeed': 50}))
    frames.append(create_buttons(packer, 0, counter, button))
    safety.set_timer(now // 1000)
    for frame in frames:
      test.assertTrue(native('rx', frame, now // 1000))
    ci.update([(now, frames)])
    safety.safety_tick()
  test.assertTrue(safety.safety_config_valid())
  test.assertTrue(safety.get_controls_allowed(), 'real physical SET release grants ordinary cruise')


def malibu_f1_params(*, release=False):
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.gm.interface import CarInterface
  from opendbc.car.gm.values import MALIBU_CC_F1_SOURCES
  fingerprint = gen_empty_fingerprint()
  fingerprint[0].update(MALIBU_CC_F1_SOURCES)
  return CarInterface.get_params(CAR.CHEVROLET_MALIBU_CC, fingerprint, [], False, release, False)

class TestOrdinaryCc(unittest.TestCase):
  def test_alternate_malibu_factory_and_parser_brake_sources(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
    from opendbc.car.gm.values import DBC, is_malibu_cc_f1_profile
    for release in (False, True):
      for disabled in (False, True):
        cp = malibu_f1_params(release=release)
        prepare_disable_longitudinal(cp, disabled)
        self.assertTrue(is_malibu_cc_f1_profile(cp))
        self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xC162 if disabled else 0xC161)
        self.assertEqual(cp.pcmCruise, disabled)
        self.assertEqual(cp.openpilotLongitudinalControl, not disabled)
        ci = CarInterface(cp)
        packer = CANPacker(DBC[CAR.CHEVROLET_MALIBU_CC][Bus.pt])
        for tick in range(80):
          raw, f1, c9 = ((20, 0, 0), (21, 0, 0), (0, 1, 0), (0, 0, 1), (0, 1, 1), (0, 0, 1), (0, 0, 0))[tick % 7]
          frames = [frame for frame in qualified_frames(packer, tick % 4) if frame[0] not in (0xBE, 0xF1, 0xC9)]
          frames += [packer.make_can_msg('EBCMBrakePedalPosition', 0, {'BrakePedalPosition': raw, 'BrakePressed': f1}),
                     packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': 1, 'BrakePressed': c9})]
          out = ci.update([(1_000_000_000 + tick * 10_000_000, frames)])
          self.assertEqual(out.brakePressed, bool(raw >= 21 or f1 or c9))
          self.assertFalse(out.cruiseState.nonAdaptive)
        self.assertTrue(out.canValid)
        self.assertNotIn('ECMAcceleratorPos', ci.can_parsers[Bus.pt].vl)

  def test_alternate_malibu_missing_source_is_final_no_output(self):
    from opendbc.car import gen_empty_fingerprint, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import MALIBU_CC_F1_SOURCES, is_malibu_cc_f1_profile
    for address in MALIBU_CC_F1_SOURCES:
      fingerprint = gen_empty_fingerprint()
      fingerprint[0].update(MALIBU_CC_F1_SOURCES)
      fingerprint[0].pop(address)
      cp = CarInterface.get_params(CAR.CHEVROLET_MALIBU_CC, fingerprint, [], False, False, False)
      self.assertTrue(cp.dashcamOnly)
      self.assertFalse(cp.openpilotLongitudinalControl)
      self.assertFalse(is_malibu_cc_f1_profile(cp))
      self.assertEqual(cp.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)

  def test_actual_final_configuration_and_shared_owners(self):
    self.assertEqual(len(ORDINARY_CC_CAR), 8)
    for identity in ORDINARY_CC_CAR:
      for release in (False, True):
        for alpha in (False, True):
          cp = params(identity, alpha=alpha, release=release)
          self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xC160)
          self.assertTrue(cp.openpilotLongitudinalControl)
          self.assertFalse(cp.pcmCruise)
          self.assertFalse(cp.alphaLongitudinalAvailable)
          self.assertFalse(cp.dashcamOnly)
          self.assertTrue(is_ordinary_cc_profile(cp))
          self.assertTrue(longitudinal_supported(cp))
          self.assertTrue(lane_centering_supported(cp))
          self.assertTrue(qualified_gm(cp))
          self.assertEqual(lateral_policy_for(cp), 'ordinary_cc')
          self.assertEqual(policy_for(cp).stopping_decel_rate, 11.18)
          self.assertEqual(policy_for(cp).kp[1], (0, 20, 20) if identity == CAR.CHEVROLET_MALIBU_CC else (0, 5, 2))
          if identity in (CAR.CADILLAC_CT6_CC, CAR.CADILLAC_XT5_CC, CAR.CHEVROLET_SUBURBAN_CC, CAR.GMC_YUKON_CC):
            self.assertEqual(identity.config.specs.tireStiffnessFactor, 1.0)

  def test_bsm_metadata_preserves_exact_control_profile_and_other_flags_deny(self):
    for identity in ORDINARY_CC_CAR:
      cp = params(identity)
      word = cp.safetyConfigs[0].safetyParam
      cp.flags |= int(GMFlags.HAS_BSM)
      self.assertTrue(is_ordinary_cc_profile(cp), identity)
      self.assertTrue(longitudinal_supported(cp), identity)
      self.assertTrue(lane_centering_supported(cp), identity)
      self.assertEqual(cp.safetyConfigs[0].safetyParam, word)
      for flag in (GMFlags.PEDAL_LONG, GMFlags.NO_CAMERA, 1 << 30):
        rejected = cp.as_reader().as_builder()
        rejected.flags |= int(flag)
        self.assertFalse(is_ordinary_cc_profile(rejected), (identity, flag))

  def test_bsm_metadata_preserves_actual_interface_controller_can(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import DBC
    for identity in ORDINARY_CC_CAR:
      plain = params(identity)
      bsm = plain.as_reader().as_builder()
      bsm.flags |= int(GMFlags.HAS_BSM)
      interfaces = (CarInterface(plain), CarInterface(bsm))
      for ci in interfaces:
        ci.update([])
      packer = CANPacker(DBC[identity][Bus.pt])
      control = structs.CarControl(enabled=True, latActive=True, longActive=True)
      control.actuators.torque = .1
      control.actuators.accel = .5
      control.hudControl.setSpeed = 35
      for tick in range(40):
        now = 1_000_000_000 + tick * 10_000_000
        frames = qualified_frames(packer, tick % 4)
        with_bsm = [*frames, packer.make_can_msg('BCMBlindSpotMonitor', 0, {})]
        states = [ci.update([(now, inputs)]) for ci, inputs in zip(interfaces, (frames, with_bsm), strict=True)]
        self.assertEqual(states[0].canValid, states[1].canValid, identity)
        outputs = [ci.apply(control.as_reader(), now) for ci in interfaces]
        self.assertEqual(outputs[0][0].to_dict(), outputs[1][0].to_dict(), identity)
        self.assertEqual(outputs[0][1], outputs[1][1], (identity, tick))
      self.assertTrue(states[0].canValid, identity)
      self.assertTrue(states[1].canValid, identity)

  def test_button_request_and_observed_counter_burst(self):
    self.assertEqual(button_request(20, 19, .4, 10.72896, True)[0], CruiseButtons.RES_ACCEL)
    self.assertEqual(button_request(20, 22, -.4, 10.72896, True)[0], CruiseButtons.DECEL_SET)
    cadence = ButtonCadence(True)
    self.assertFalse(cadence.ready(30, 0, CruiseButtons.RES_ACCEL, .2))
    self.assertTrue(cadence.ready(31, 0, CruiseButtons.RES_ACCEL, .2))
    self.assertFalse(cadence.ready(32, 0, CruiseButtons.RES_ACCEL, .2))
    self.assertFalse(cadence.ready(33, 1, CruiseButtons.RES_ACCEL, .2))
    self.assertTrue(cadence.ready(34, 1, CruiseButtons.RES_ACCEL, .2))

  def test_xt4_burst_strict_native_interval_and_nominal_cadence(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import DBC
    from opendbc.car.gm.tests.test_bolt_cc import native
    from opendbc.safety.tests.libsafety import libsafety_py

    cadence = ButtonCadence(True)
    self.assertFalse(cadence.ready(64, 0, CruiseButtons.RES_ACCEL, .2))
    self.assertTrue(cadence.ready(65, 0, CruiseButtons.RES_ACCEL, .2))
    self.assertFalse(cadence.ready(66, 1, CruiseButtons.RES_ACCEL, .2))
    self.assertFalse(cadence.ready(67, 1, CruiseButtons.RES_ACCEL, .2), 'exact20ms cannot pass native')
    self.assertEqual((cadence.remaining, cadence.last_counter), (5, 0), 'early attempt cannot consume settled credit')
    self.assertTrue(cadence.ready(68, 1, CruiseButtons.RES_ACCEL, .2), 'same fresh counter remains eligible at30ms')
    for identity in (CAR.CADILLAC_CT6_CC, CAR.CADILLAC_XT4_CC):
      for physical_step in (2, 3):
        with self.subTest(identity=identity, physical_step=physical_step):
          cp = params(identity)
          ci = CarInterface(cp)
          packer = CANPacker(DBC[identity][Bus.pt])
          safety = libsafety_py.libsafety
          safety.init_tests()
          safety.set_alternative_experience(cp.alternativeExperience)
          self.assertEqual(safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam), 0)
          warm_ordinary_native(self, ci, packer, safety)
          control = structs.CarControl(enabled=True, longActive=True)
          control.actuators.accel = 1.
          control.hudControl.setSpeed = 35.
          sent = []
          last_credit = None
          for tick in range(200):
            now = 1_000_000_000 + tick*10_000_000
            counter = (tick // physical_step) % 4
            frames = [frame for frame in qualified_frames(packer, counter)
                      if frame[0] != 0x3D1 and (frame[0] != 0x1E1 or tick % physical_step == 0)]
            frames.append(packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': 1, 'CruiseSetSpeed': 50}))
            safety.set_timer(now//1000)
            for frame in frames:
              self.assertTrue(native('rx', frame, now//1000))
            safety.safety_tick()
            state = ci.update([(now, frames)])
            if tick >= 60:
              self.assertTrue(state.canValid)
              self.assertTrue(safety.safety_config_valid())
            _, outgoing = ci.apply(control.as_reader(), now)
            for frame in outgoing:
              self.assertTrue(native('tx', frame, now//1000), (identity, physical_step, tick, frame))
              if frame[0] == 0x1E1:
                self.assertEqual(frame[2], 0)
                self.assertEqual(frame[1][4], (counter + 1) % 4)
                self.assertEqual((frame[1][5] >> 4) & 7, CruiseButtons.RES_ACCEL)
                credit = ci.CS.volt_cc_physical.button_credit_ns
                self.assertGreater(credit, 0)
                self.assertNotEqual(credit, last_credit, 'one physical credit is consumed once; counter wrap is allowed')
                last_credit = credit
                sent.append(tick)
          self.assertGreaterEqual(len(sent), 6, 'actual parser/controller/native burst must be nonempty')
          self.assertTrue(all((b-a)*10_000 > 20_000 for a,b in zip(sent, sent[1:], strict=False)), sent)
          if identity == CAR.CADILLAC_CT6_CC:
            self.assertTrue(all(tick % 4 == 0 for tick in sent), 'CT6 retains25Hz processing')
          elif physical_step == 3:
            self.assertTrue(any(b-a == 3 for a,b in zip(sent, sent[1:], strict=False)), 'nominal33Hz settled burst remains reachable')

  def test_shared_writer_clocks_preserve_direct_set_cancel_and_burst_credit(self):
    from opendbc.car.gm.gmcan import create_buttons
    from opendbc.can import CANPacker
    from opendbc.car import Bus, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import DBC
    from opendbc.car.gm.tests.test_bolt_cc import native
    from opendbc.safety.tests.libsafety import libsafety_py

    for burst in (False, True):
      cadence = ButtonCadence(burst)
      cadence.note_sent(51, CruiseButtons.RES_ACCEL)
      self.assertFalse(cadence.can_send(52, CruiseButtons.DECEL_SET))
      self.assertFalse(cadence.can_send(53, CruiseButtons.DECEL_SET))
      self.assertTrue(cadence.can_send(54, CruiseButtons.DECEL_SET))
      cadence.note_sent(54, CruiseButtons.DECEL_SET)
      self.assertFalse(cadence.ready(55, 0, CruiseButtons.RES_ACCEL, .2))
      self.assertFalse(cadence.ready(56, 0, CruiseButtons.RES_ACCEL, .2))
      self.assertTrue(cadence.ready(57, 0, CruiseButtons.RES_ACCEL, .2))
      cadence.note_sent(80, CruiseButtons.CANCEL)
      self.assertFalse(cadence.can_send(82, CruiseButtons.CANCEL))
      self.assertTrue(cadence.can_send(83, CruiseButtons.CANCEL))
      self.assertTrue(cadence.can_send(82, CruiseButtons.DECEL_SET), 'native clock classes remain independent')
      cadence.reset_burst()
      self.assertFalse(cadence.can_send(82, CruiseButtons.CANCEL), 'reset cannot erase successful-send history')
    for identity in (CAR.CADILLAC_CT6_CC, CAR.CADILLAC_XT4_CC):
      cp = params(identity)
      ci = CarInterface(cp)
      packer = CANPacker(DBC[identity][Bus.pt])
      safety = libsafety_py.libsafety
      safety.set_alternative_experience(0)
      self.assertEqual(safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam), 0)
      safety.init_tests()
      warm_ordinary_native(self, ci, packer, safety)
      sent = {True: [], False: []}
      buttons = []
      for tick in range(320):
        now = 1_000_000_000 + tick * 10_000_000
        gas = tick == 52 or 250 <= tick < 270
        counter = (tick // 2) % 4
        if identity == CAR.CADILLAC_XT4_CC:
          if 44 <= tick < 50:
            counter = 2  # Physical duplicate neutral packets at46/48 cannot renew consumed credit.
          elif tick >= 50:
            counter = (tick // 2 - 2) % 4  # Fresh sequential3@50,0@52 and continued physical wrap.
        frames = [f for f in qualified_frames(packer, counter) if f[0] not in (0x3D1, 0x1C4, 0x1E1)]
        if tick % 2 == 0:
          frames.append(create_buttons(packer, 0, counter, CruiseButtons.UNPRESS))
        frames += [packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': 1, 'CruiseSetSpeed': 50}),
                   packer.make_can_msg('AcceleratorPedal2', 0, {'AcceleratorPedal2': 30 if gas else 0})]
        safety.set_timer(now // 1000)
        for f in frames:
          self.assertTrue(native('rx', f, now // 1000))
        safety.safety_tick()
        state = ci.update([(now, frames)])
        if tick >= 60:
          self.assertTrue(state.canValid)
          self.assertTrue(safety.safety_config_valid())
        control = structs.CarControl(enabled=not 180 <= tick < 196, longActive=not 180 <= tick < 196)
        control.actuators.accel = -20. if 170 <= tick < 180 else 1.
        control.hudControl.setSpeed = 35.
        _, outgoing = ci.apply(control.as_reader(), now)
        if identity == CAR.CADILLAC_XT4_CC and tick == 52:
          self.assertEqual(sent[False][-1], 51, 'actual burst RES immediately precedes single-frame gas SET opportunity')
          self.assertTrue(state.canValid and safety.safety_config_valid())
          self.assertTrue(ci.CS.volt_cc_physical.current(now))
          self.assertTrue(state.gasPressed)
          self.assertEqual(ci.CS.volt_cc_physical.button_credit_ns, now)
          self.assertNotEqual(ci.CC.volt_cc_consumed_source_ns, now, '20ms guard cannot consume fresh raw credit')
          self.assertEqual(ci.CC.ordinary_cc_cadence.last_direction_frame, 51)
          self.assertFalse(any(f[0] == 0x1E1 for f in outgoing), 'native must reject SET52 after RES51')
        for f in outgoing:
          self.assertTrue(native('tx', f, now // 1000), (identity, tick, f))
          if f[0] == 0x1E1:
            button = (f[1][5] >> 4) & 7
            self.assertEqual(f[2], 0)
            self.assertEqual(f[1][4], (counter + 1) % 4)
            sent[button == CruiseButtons.CANCEL].append(tick)
            buttons.append(button)
      for times in sent.values():
        self.assertTrue(times)
        self.assertTrue(all(b - a > 2 for a, b in zip(times, times[1:], strict=False)), (identity, times))
      self.assertIn(CruiseButtons.RES_ACCEL, buttons)
      self.assertIn(CruiseButtons.DECEL_SET, buttons)
      self.assertIn(CruiseButtons.CANCEL, buttons)

  def test_actual_packed_state_and_missing_source_steering_backstop(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import DBC
    for identity in ORDINARY_CC_CAR:
      cp = params(identity)
      ci = CarInterface(cp)
      packer = CANPacker(DBC[identity][Bus.pt])
      control = structs.CarControl(enabled=True, latActive=True, longActive=True)
      control.actuators.torque = .1
      control.actuators.accel = .5
      control.hudControl.setSpeed = 35
      for tick in range(30):
        now = 1_000_000_000 + tick * 10_000_000
        out = ci.update([(now, qualified_frames(packer, tick % 4))])
        ci.apply(control.as_reader(), now)
      self.assertTrue(out.canValid)
      self.assertFalse(out.cruiseState.nonAdaptive)
      self.assertTrue(out.cruiseState.enabled)
      self.assertTrue(ci.CS.volt_cc_physical.current(now))
      for tick in range(30, 50):
        now = 1_000_000_000 + tick * 10_000_000
        frames = [frame for frame in qualified_frames(packer, tick % 4) if frame[0] != 0xC9]
        ci.update([(now, frames)])
        actuators, _ = ci.apply(control.as_reader(), now)
      self.assertFalse(ci.CS.volt_cc_physical.sources_current(now))
      self.assertEqual(actuators.torqueOutputCan, 0)

  def test_saved_disabled_owner_preserves_lateral_without_long_takeover(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
    from opendbc.car.gm.values import DBC
    for identity in ORDINARY_CC_CAR:
      cp = params(identity)
      prepare_disable_longitudinal(cp, True)
      self.assertFalse(cp.openpilotLongitudinalControl)
      self.assertTrue(cp.pcmCruise)
      self.assertEqual(cp.safetyConfigs[0].safetyParam, 16)
      ci = CarInterface(cp)
      packer = CANPacker(DBC[identity][Bus.pt])
      control = structs.CarControl(enabled=True, latActive=True, longActive=True)
      control.actuators.accel = 2
      control.actuators.torque = .1
      control.hudControl.setSpeed = 35
      for tick in range(60):
        now = 1_000_000_000 + tick * 10_000_000
        ci.update([(now, qualified_frames(packer, tick % 4))])
        if tick == 59:
          control.enabled = False
        _, frames = ci.apply(control.as_reader(), now)
        self.assertEqual({frame[0] for frame in frames} - {0x180}, set())

  def test_actual_longcontrol_stopping_release_and_source_unknown(self):
    from opendbc.car.structs import CarState, CarControl
    from opendbc.car.gm.cc_longitudinal import VoltCcEvidence
    from openpilot.selfdrive.controls.lib.longcontrol import LongControl
    from openpilot.starpilot.longitudinal.extension import LongitudinalContext
    for identity in (CAR.CADILLAC_CT6_CC, CAR.CHEVROLET_MALIBU_CC):
      cp = params(identity).as_reader()
      cs = CarState.new_message(vEgo=0., canValid=True, canTimeout=False)
      cs.cruiseState.standstill = True
      controller = LongControl(cp)
      states = CarControl.Actuators.LongControlState
      output = controller.update(True, cs.as_reader(), -.5, True, (-4., 2.))
      self.assertEqual(controller.long_control_state, states.stopping)
      self.assertAlmostEqual(output, -.1118, places=6)
      for _ in range(40):
        controller.update(True, cs.as_reader(), .2, False, (-4., 2.))
        self.assertEqual(controller.long_control_state, states.stopping)
      for tick in range(35):
        evidence = VoltCcEvidence(1, 1_000_000_000 + tick * 10_000_000, False)
        context = LongitudinalContext(vehicle_stop_evidence=evidence)
        controller.update(True, cs.as_reader(), .2, False, (-4., 2.), context=context)
        self.assertEqual(controller.long_control_state, states.pid if tick == 34 else states.stopping)

  def test_held_gas_set_uses_fresh_33hz_neutral_credit_once(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import DBC
    for identity in (CAR.CADILLAC_CT6_CC, CAR.CADILLAC_XT4_CC):
      cp = params(identity)
      ci = CarInterface(cp)
      packer = CANPacker(DBC[identity][Bus.pt])
      control = structs.CarControl(enabled=True, latActive=True, longActive=False)
      control.hudControl.setSpeed = 35
      emitted = []
      for tick in range(55):
        now = 1_000_000_000 + tick * 10_000_000
        frames = [frame for frame in qualified_frames(packer, (tick // 3) % 4)
                  if frame[0] not in (0x3D1, 0x1C4) and (tick % 3 == 0 or frame[0] != 0x1E1)]
        frames += [packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': 1, 'CruiseSetSpeed': 50}),
                   packer.make_can_msg('AcceleratorPedal2', 0, {'AcceleratorPedal2': 30})]
        ci.update([(now, frames)])
        _, commands = ci.apply(control.as_reader(), now)
        emitted += [(tick, frame) for frame in commands if frame[0] == 0x1E1]
      self.assertEqual([tick for tick, _ in emitted], [0, 52])

  def test_default_lateral_and_explicit_standard_remain_separate(self):
    from opendbc.car.gm.interface import CarInterface
    from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque, KP_INTERP, INTERP_SPEEDS
    from openpilot.starpilot.lateral.controller_selection import ControllerMode
    for identity in ORDINARY_CC_CAR:
      cp = params(identity).as_reader()
      ci = CarInterface(cp)
      owner = LatControlTorque(cp, ci, .01)
      self.assertEqual(owner.controller_policy, 'ordinary_cc')
      self.assertEqual(owner.pid._k_p, [[0.0], [.6]])
      self.assertEqual(owner.pid._k_i, ([0], [.35]))
      standard = LatControlTorque(cp, ci, .01, controller_mode=ControllerMode.STANDARD)
      self.assertIsNone(standard.starpilot_extension)
      self.assertEqual(standard.pid._k_p, [INTERP_SPEEDS, KP_INTERP])

  def test_actual_card_finalizes_registered_saved_choice_and_metric(self):
    import os
    from unittest.mock import patch
    from openpilot.common.params import Params
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.car.card import Car
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.radar_interface import RadarInterface
    for identity in (CAR.CADILLAC_CT6_CC, CAR.CADILLAC_XT4_CC):
      with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
        saved = Params()
        saved.put_bool('OpenpilotEnabledToggle', True, block=True)
        saved.put_bool('DisableOpenpilotLongitudinal', False, block=True)
        saved.put_bool('IsMetric', True, block=True)
        cp = params(identity)
        ci = CarInterface(cp)
        card = Car(CI=ci, RI=RadarInterface(cp))
        self.assertTrue(card.CP.openpilotLongitudinalControl)
        self.assertFalse(card.CP.pcmCruise)
        self.assertEqual(card.CP.safetyConfigs[0].safetyParam, 0xC160)
        self.assertTrue(card.volt_cc_selected)
        self.assertTrue(card.is_metric)
        from opendbc.can import CANPacker
        from opendbc.car import Bus, structs
        from opendbc.car.gm.values import DBC
        packer = CANPacker(DBC[identity][Bus.pt])
        ci.update([(1_000_000_000, qualified_frames(packer, 0))])
        # Exercise metric transport before initialization; the separately qualified
        # envelope-clock tests own source admission, not this units regression.
        card.volt_cc_boot_offset_ns = 0
        card.volt_cc_drive_id = 1
        card.volt_cc_source_floor_ns = 0
        with patch.object(card, 'volt_cc_control_current', return_value=True):
          card.controls_update(structs.CarState(canValid=False), structs.CarControl())
        self.assertTrue(ci.CC.volt_cc_metric)


def malibu_hybrid_params(*, pedal=False, removed=False, alternate=False, radar=False, release=False):
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.gm.interface import CarInterface
  from opendbc.car.gm.values import MALIBU_HYBRID_SOURCES
  from opendbc.car.gm.radar_interface import RADAR_HEADER_MSG
  fingerprint = gen_empty_fingerprint()
  fingerprint[0].update(MALIBU_HYBRID_SOURCES)
  fingerprint[0][0xF1 if alternate else 0xBE] = 6
  if pedal:
    fingerprint[0][0x201] = 6
  if not removed:
    fingerprint[2].update({0x320: 6, 0x180: 4})
  if radar:
    fingerprint[1][RADAR_HEADER_MSG] = 8
  return CarInterface.get_params(CAR.CHEVROLET_MALIBU_HYBRID_CC, fingerprint, [], False, release, False)


class TestMalibuHybridCc(unittest.TestCase):
  def test_active_speed_buttons_ignore_generic_upstream_cancel(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import DBC
    from opendbc.car.gm.hybrid_cc import CANCEL, RESUME, standard_set_bytes
    from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
    cp = malibu_hybrid_params(removed=True)
    ci = CarInterface(cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    control = structs.CarControl.new_message()
    control.enabled = control.latActive = control.longActive = True
    control.cruiseControl.cancel = True  # Controls requests this for a non-PCM cruise owner.
    control.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
    observed = set()
    echo = []
    for tick in range(280):
      now = 1_000_000_000 + tick * 10_000_000
      control.enabled = control.latActive = control.longActive = tick < 240
      control.actuators.accel = .5 if tick < 150 else -3.
      frames = [frame for frame in pt_frames(packer) if frame[0] not in (0x1E1, 0x3D1)]
      frames += [packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': 1, 'CruiseSetSpeed': 50}),
                 packer.make_can_msg('EBCMBrakePedalPosition', 0, {}),
                 packer.make_can_msg('EBCMRegenPaddle', 0, {}),
                 (0x1E1, bytes.fromhex('000000010015ee' if tick % 2 else '00000001001fcc'), 0), *echo]
      state = ci.update([(now, frames)])
      _, sent = ci.CC.update(control.as_reader(), ci.CS, now)
      echo = [(address, raw, 128) for address, raw, bus in sent if address == 0x180 and bus == 0]
      if tick >= 60:
        self.assertTrue(state.canValid)
        for address, raw, bus in sent:
          if address != 0x1E1:
            continue
          self.assertEqual(bus, 0)
          if tick < 150:
            self.assertIn(int.from_bytes(raw[5:7], 'big'), RESUME)
            observed.add('resume')
          elif tick < 240:
            self.assertEqual(raw, standard_set_bytes(raw[4]))
            observed.add('set')
          else:
            self.assertIn(int.from_bytes(raw[5:7], 'big'), CANCEL)
            observed.add('cancel')
    self.assertEqual(observed, {'resume', 'set', 'cancel'})

  def test_reached_factory_and_stock_reduction(self):
    from opendbc.car.gm.values import malibu_hybrid_profile
    from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
    from opendbc.car.gm.hybrid_cc import policy_for as hybrid_policy
    for release in (False, True):
      for pedal in (False, True):
        for removed in (False, True):
          for alternate in (False, True):
            for radar in ((False, True) if pedal else (False,)):
              cp = malibu_hybrid_params(pedal=pedal, removed=removed, alternate=alternate, radar=radar, release=release)
              profile = malibu_hybrid_profile(cp)
              self.assertIsNotNone(profile)
              self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xE800 + 2 * int(pedal) + int(removed))
              self.assertTrue(cp.openpilotLongitudinalControl)
              self.assertFalse(cp.pcmCruise)
              self.assertEqual(cp.radarUnavailable, not radar)
              self.assertAlmostEqual(cp.steerActuatorDelay, .2)
              self.assertEqual(cp.steerRatio, float(np.float32(15.8)))
              self.assertEqual(lateral_policy_for(cp), 'ordinary_cc')
              self.assertTrue(qualified_gm(cp))
              self.assertTrue(longitudinal_supported(cp))
              self.assertTrue(lane_centering_supported(cp))
              policy = hybrid_policy(cp)
              self.assertIsNotNone(policy)
              self.assertEqual(policy.kp, ((0., 5., 15., 35.), tuple(float(np.float32(v))
                                for v in (.095, .085, .065, .05))) if pedal else ((0.,), (0.,)))
              from openpilot.selfdrive.controls.lib.longcontrol import LongControl
              control = LongControl(cp)
              self.assertEqual(control.extension.kp, policy.kp)
              self.assertEqual(control.stopping_decel_rate, float(np.float32(.8)) if pedal else 1.)
              self.assertEqual(bool(policy.ignore_cruise_standstill), pedal)
              self.assertAlmostEqual(policy.feedforward(.5, 10., 0.), .1 if pedal else .5)
              prepare_disable_longitudinal(cp, True)
              self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xE804 + int(removed))
              self.assertTrue(cp.pcmCruise)
              self.assertFalse(cp.openpilotLongitudinalControl)
              self.assertFalse(cp.autoResumeSng)
              self.assertIsNotNone(malibu_hybrid_profile(cp))
              self.assertTrue(qualified_gm(cp))
              self.assertFalse(longitudinal_supported(cp))
              self.assertIsNone(hybrid_policy(cp))
              prepare_disable_longitudinal(cp, False)
              self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xE804 + int(removed))

  def test_semantic_unknown_packets_do_not_release_a_physical_press(self):
    from opendbc.car.gm.hybrid_cc import HybridButtons, custom_bytes, standard_set_bytes
    buttons = HybridButtons()

    def receive(stamp, raw):
      buttons.observe_packets([(stamp, [(0x1E1, raw, 0)])])
      return buttons.update(True)[2]
    self.assertEqual(receive(1, bytes.fromhex('000000410015ee')), [])
    self.assertEqual(receive(2, custom_bytes('resume', 0, 0x41)), [(1, 2)])
    for stamp, word in enumerate(('10ff', '1add', '659e', '6f7c'), 3):
      self.assertEqual(receive(stamp, bytes.fromhex('0000004100' + word)), [])
    self.assertEqual(receive(8, bytes.fromhex('00000041001fcc')), [(2, 1)])
    self.assertEqual(receive(9, standard_set_bytes(2)), [(1, 3)])
    self.assertEqual(receive(10, bytes.fromhex('000000410015ee')), [(3, 1)])
    self.assertEqual(receive(200_000_000, bytes.fromhex('00000041001fcc')), [])
    self.assertEqual(receive(200_000_001, bytes.fromhex('000000410015ee')), [])

  def test_phase_credit_replay_expiry_and_requalification(self):
    from opendbc.car.gm.hybrid_cc import PhysicalSlot, custom_bytes
    slot = PhysicalSlot()
    neutral = bytes.fromhex('000000410015ee')
    slot.observe(1, neutral, bus=0)
    self.assertFalse(slot.requalify(physical_ready=True))
    packet = custom_bytes('resume', 1, 0x41)
    self.assertTrue(slot.accept('resume', packet, 2, interval_ns=200_000_001, authorized=True))
    slot.observe(3, bytes.fromhex('000000410315ee'), bus=0)
    self.assertIsNone(slot.expected('resume', 3, interval_ns=0, authorized=True))
    slot.observe(4, bytes.fromhex('000000410010ff'), bus=0)
    slot.observe(5, neutral, bus=0)
    self.assertIsNone(slot.expected('resume', 5, interval_ns=0, authorized=True))
    slot.observe(100_000_006, bytes.fromhex('00000041001fcc'), bus=0)
    self.assertFalse(slot.requalify(physical_ready=False))
    self.assertTrue(slot.requalify(physical_ready=True))
    slot.observe(100_000_007, neutral, bus=0)
    self.assertEqual(slot.last_tx_ns, 2)
    self.assertIsNone(slot.expected('resume', 100_000_007, interval_ns=200_000_001, authorized=True))

  def test_fixed_near_stop_preserves_pedal_memory_and_release_slew(self):
    from types import SimpleNamespace
    from opendbc.car.gm.hybrid_cc import HybridPedalCommand
    cp = malibu_hybrid_params(pedal=True)
    command = HybridPedalCommand(cp)
    cs = SimpleNamespace(vEgo=2., standstill=False, cruiseState=SimpleNamespace(standstill=False))
    first = command.update(.5, True, cs, stopping=False, resume=False, orientation=None)
    self.assertGreater(first, 0.)
    saved = command.steady, command.active_last
    cs.vEgo = .1
    self.assertEqual(command.update(-.5, True, cs, stopping=True, resume=False, orientation=None), 0.)
    self.assertEqual((command.steady, command.active_last), saved)
    resumed = command.update(.5, True, cs, stopping=False, resume=True, orientation=None)
    from opendbc.car.gm.silverado_cc import pedal_fraction, pedal_slew
    self.assertEqual(resumed, pedal_slew(pedal_fraction(.5, .1), saved[0], .5, .1))

  def test_healthy_inactive_resets_only_calc_memory(self):
    from types import SimpleNamespace
    from opendbc.car.gm.hybrid_cc import HybridPedalCommand
    command = HybridPedalCommand(malibu_hybrid_params(pedal=True))
    cs = SimpleNamespace(vEgo=2., standstill=False, cruiseState=SimpleNamespace(standstill=False))
    self.assertGreater(command.update(.5, True, cs, stopping=False, resume=False, orientation=None), 0.)
    self.assertEqual(command.update(.5, False, cs, stopping=False, resume=False, orientation=None), 0.)
    self.assertEqual(command.steady, 0.)
    self.assertFalse(command.active_last)

  def test_final_factory_denies_malformed_required_camera(self):
    from opendbc.car import gen_empty_fingerprint, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import MALIBU_HYBRID_SOURCES, malibu_hybrid_profile
    for address, wrong_length in ((0x180, 8), (0x320, 8)):
      fingerprint = gen_empty_fingerprint()
      fingerprint[0].update(MALIBU_HYBRID_SOURCES)
      fingerprint[0][0xBE] = 6
      fingerprint[2].update({0x180: 4, 0x320: 6})
      fingerprint[2][address] = wrong_length
      cp = CarInterface.get_params(CAR.CHEVROLET_MALIBU_HYBRID_CC, fingerprint, [], False, False, False)
      self.assertTrue(cp.dashcamOnly)
      self.assertIsNone(malibu_hybrid_profile(cp))
      self.assertEqual(cp.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)

  def test_actual_stock_parser_uses_c9_brake_and_pt_cruise_without_lazy_camera(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import DBC
    from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
    from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
    for alternate, removed in ((False, False), (True, False), (False, True), (True, True)):
      cp = malibu_hybrid_params(pedal=True, removed=removed, alternate=alternate)
      prepare_disable_longitudinal(cp, True)
      ci = CarInterface(cp)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      for tick in range(80):
        pressed = tick % 4 >= 2
        frames = [frame for frame in pt_frames(packer, acc_cruise=4, cruise=True)
                  if frame[0] not in (0xBE, 0xC9, 0x1E1)]
        frames += [packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': 1, 'BrakePressed': int(pressed)}),
                   packer.make_can_msg('EBCMRegenPaddle', 0, {}),
                   packer.make_can_msg('EBCMBrakePedalPosition', 0, {'BrakePedalPosition': 200, 'BrakePressed': 1})
                   if alternate else packer.make_can_msg('ECMAcceleratorPos', 0, {'BrakePedalPos': 100}),
                   (0x1E1, bytes.fromhex('000000010015ee' if tick % 2 else '00000001001fcc'), 0)]
        if not removed:
          frames += [packer.make_can_msg('ASCMLKASteeringCmd', 2, {}), packer.make_can_msg('AEBCmd', 2, {})]
        out = ci.update([(1_000_000_000 + tick * 10_000_000, frames)])
        self.assertEqual(out.brakePressed, pressed)
        self.assertTrue(out.cruiseState.enabled)
        self.assertTrue(out.cruiseState.standstill)
        self.assertFalse(out.cruiseState.nonAdaptive)
      self.assertTrue(out.canValid)
      self.assertNotIn('GAS_SENSOR', ci.can_parsers[Bus.pt].vl)
      if removed:
        self.assertNotIn('ASCMLKASteeringCmd', ci.can_parsers[Bus.pt].vl)
        self.assertNotIn('ASCMLKASteeringCmd', ci.can_parsers[Bus.cam].vl)
      self.assertNotIn('ASCMActiveCruiseControlStatus', ci.can_parsers[Bus.cam].vl)

  def test_driver_override_preserves_credit_without_semantic_enable(self):
    from opendbc.car.gm.hybrid_cc import HybridButtons, standard_set_bytes
    buttons = HybridButtons()
    buttons.observe_packets([(1_000_000_000, [(0x1E1, bytes.fromhex('000000010015ee'), 0)])])
    _, _, edges = buttons.update(True, enable_ready=False)
    self.assertEqual(edges, [])
    self.assertIsNone(buttons.semantic)
    self.assertEqual(buttons.slot.expected('gas_set', 1_000_000_000,
                                         interval_ns=520_000_000, authorized=True), standard_set_bytes(0))
    buttons.observe_packets([(1_030_000_000, [(0x1E1, bytes.fromhex('00000001001fcc'), 0)])])
    self.assertEqual(buttons.update(True, enable_ready=True)[2], [])
    self.assertEqual(buttons.semantic, 1)
    self.assertFalse(buttons.reset_pending)

  def test_real_sensor_invalidity_withdraws_hybrid_commands_and_recovers_without_edge(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import DBC
    from opendbc.car.gm.gmcan import pedal_crc
    from opendbc.car.gm.hybrid_cc import sources_current
    from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
    cp = malibu_hybrid_params(pedal=True, removed=True)
    ci = CarInterface(cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    control = structs.CarControl.new_message()
    control.enabled = control.latActive = control.longActive = True
    control.actuators.torque, control.actuators.accel = .1, .5
    control.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
    positive_pedal = positive_steer = cancelled = recovered = status_recovered = False
    echo = []
    for tick in range(160):
      now = 1_000_000_000 + tick * 10_000_000
      stock_active = tick >= 80
      frames = [frame for frame in pt_frames(packer, cruise=stock_active) if frame[0] != 0x1E1]
      frames += [packer.make_can_msg('EBCMRegenPaddle', 0, {}),
                 (0x1E1, bytes.fromhex('000000010015ee' if tick % 2 else '00000001001fcc'), 0), *echo]
      if not 108 <= tick < 132:
        first = 4096 if 96 <= tick < 104 else 612
        sensor = bytearray(first.to_bytes(2, 'big') + (285).to_bytes(2, 'big') + bytes((tick % 16, 0)))
        sensor[-1] = pedal_crc(sensor)
        if 104 <= tick < 108:
          sensor[-1] ^= 1
        frames.append((0x201, bytes(sensor), 0))
      state = ci.update([(now, frames)])
      _, sent = ci.CC.update(control.as_reader(), ci.CS, now)
      statuses = [packet for packet in sent if packet[0] == 0x3D1]
      if 60 <= tick < 96 or tick >= 132:
        self.assertEqual(bool(statuses), tick % 4 == 0)
      if 96 <= tick < 132:
        self.assertFalse(statuses)
      if tick >= 132:
        status_recovered |= bool(statuses)
      self.assertTrue(all(packet[2] == 0 and packet[1][4] == 0 for packet in statuses))
      echo = [(address, data, 128) for address, data, bus in sent if address == 0x180 and bus == 0]
      pedal = any(address == 0x200 and data[4] & 0x80 for address, data, _ in sent)
      steering = any(address == 0x180 and ((data[0] & 7) or data[1]) for address, data, _ in sent)
      cancel = any(address == 0x1E1 for address, _, _ in sent)
      if 60 <= tick < 80:
        positive_pedal |= pedal
      if 80 <= tick < 96:
        positive_steer |= steering
        cancelled |= cancel
      if 96 <= tick < 108 or 119 <= tick < 132:
        self.assertFalse(sources_current(ci.CS, now))
        self.assertFalse(pedal)
        self.assertFalse(steering)
        self.assertFalse(cancel)
      if tick >= 132:
        self.assertFalse(state.buttonEvents)
        recovered |= sources_current(ci.CS, now) and cancel
    self.assertTrue(positive_pedal)
    self.assertTrue(positive_steer)
    self.assertTrue(cancelled)
    self.assertTrue(recovered)
    self.assertTrue(status_recovered)

  def test_real_status_requires_raw_driver_and_camera_sources(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus, structs
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import DBC
    from opendbc.car.gm.gmcan import pedal_crc
    from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
    for fault in ('brake', 'accelerator', 'main_expiry', 'camera_steer', 'camera_aeb'):
      with self.subTest(fault=fault):
        removed = not fault.startswith('camera_')
        cp = malibu_hybrid_params(pedal=True, removed=removed)
        self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xE803 if removed else 0xE802)
        ci = CarInterface(cp)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        control = structs.CarControl.new_message()
        # Status production is independent of the requested control axes.
        healthy = recovered = False
        for tick in range(240):
          now = 1_000_000_000 + tick * 10_000_000
          blocked = 80 <= tick < (191 if not removed else 121 if fault == 'main_expiry' else 100)
          frames = [frame for frame in pt_frames(packer) if frame[0] not in (0x1E1, 0xC9, 0x1C4, 0x1F5)]
          frames += [packer.make_can_msg('ECMEngineStatus', 0, {
                       'CruiseMainOn': 1, 'BrakePressed': int(blocked and fault == 'brake')}),
                     packer.make_can_msg('AcceleratorPedal2', 0, {
                       'AcceleratorPedal2': int(blocked and fault == 'accelerator')}),
                     packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 7, 'ManualMode': 1}),
                     packer.make_can_msg('EBCMRegenPaddle', 0, {}),
                     (0x1E1, bytes.fromhex('000000010015ee' if tick % 2 else '00000001001fcc'), 0)]
          sensor = bytearray((612).to_bytes(2, 'big') + (285).to_bytes(2, 'big') + bytes((tick % 16, 0)))
          sensor[-1] = pedal_crc(sensor)
          frames.append((0x201, bytes(sensor), 0))
          if not removed:
            if not (blocked and fault == 'camera_steer'):
              frames.append(packer.make_can_msg('ASCMLKASteeringCmd', 2, {'RollingCounter': tick % 4}))
            if not (blocked and fault == 'camera_aeb'):
              frames.append(packer.make_can_msg('AEBCmd', 2, {}))
          if blocked and fault == 'main_expiry':
            frames = [frame for frame in frames if frame[0] != 0xC9]
          state = ci.update([(now, frames)])
          _, sent = ci.CC.update(control.as_reader(), ci.CS, now)
          statuses = [packet for packet in sent if packet[0] == 0x3D1]
          self.assertTrue(all(bus == 0 and raw == bytes((1, 0, 0, 0, 0, 0, 0, 0))
                              for _, raw, bus in statuses))
          if tick >= 60:
            self.assertEqual(state.gearShifter, structs.CarState.GearShifter.manumatic)
            self.assertTrue(ci.CS.pedal_sensor_healthy)
            main_ns = 1_790_000_000 if blocked and fault == 'main_expiry' else now
            self.assertEqual(ci.CS.hybrid_status.main_ns, main_ns)
            self.assertTrue(ci.CS.hybrid_status.main_on)
            self.assertTrue(all(stamp == (main_ns if index == 4 else now)
                                for index, (stamp, _) in enumerate(ci.CS.hybrid_sources)))
          if 60 <= tick < 80 or tick >= 210:
            self.assertTrue(state.canValid)
            self.assertEqual(bool(statuses), tick % 4 == 0)
            healthy |= tick < 80 and bool(statuses)
            recovered |= tick >= 210 and bool(statuses)
          if removed and blocked and (fault != 'main_expiry' or tick >= 110):
            if fault == 'accelerator':
              self.assertFalse(state.gasPressed)  # Healthy pedal data replaces the host gas state.
              self.assertTrue(ci.CS.hybrid_status.accelerator_pressed)
            if fault == 'main_expiry':
              self.assertGreater(now - ci.CS.hybrid_status.main_ns, 300_000_000)
            self.assertFalse(statuses)
          if not removed and 180 <= tick < 191:
            camera_ns = ci.CS.hybrid_status.camera_ns[0 if fault == 'camera_steer' else 1]
            self.assertGreater(now - camera_ns, 1_000_000_000)
            self.assertFalse(statuses)
        self.assertTrue(healthy)
        self.assertTrue(recovered)
