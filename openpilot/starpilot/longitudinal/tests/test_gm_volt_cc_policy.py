"""Exact manual Volt CC, actual parsed producer/Controls/Card/controller path."""

from unittest.mock import Mock
from unittest.mock import patch

from openpilot.starpilot.lateral.controller_selection import default_selection
from openpilot.starpilot.longitudinal.extension import LongitudinalContext
from openpilot.starpilot.longitudinal.tests.extension_helpers import extension_state
import os
from types import SimpleNamespace as NS
import unittest

from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.can import CANPacker
from opendbc.car.gm import gmcan
from opendbc.car.gm.cc_longitudinal import VoltCcEvidence, VoltCcStopPolicy
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.radar_interface import RadarInterface
from opendbc.car.gm.fingerprints import FINGERPRINTS, FW_VERSIONS
from opendbc.car.gm.values import CAR, DBC, is_volt_cc_longitudinal
from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames
from openpilot.cereal import messaging
from openpilot.selfdrive.car.card import Car
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.starpilot.longitudinal.tests.test_gm_volt_long_policy import controls_fixture
from opendbc.car.vehicle_model import VehicleModel

REQUIRED = {0xbe: 6, 0x3d1: 8, 0xc9: 8, 0x1e1: 7, 0x1f5: 8, 0x34a: 5, 0x1c4: 8, 0xbd: 7}


def params(alpha=False, release=False, missing=None):
  fp = gen_empty_fingerprint()
  fp[0].update(REQUIRED)
  if missing is not None:
    del fp[0][missing]
  return CarInterface.get_params(CAR.CHEVROLET_VOLT_CC, fp, [], alpha, release, False)


def physical_frames(packer, speed=20., target_speed=20., counter=0, brake=False, gas=False, gear=4, button=1):
  frames = pt_frames(packer, counter=counter, brake=brake, gas=gas)
  replacements = {
    'EBCMWheelSpdFront': {'FLWheelSpd': speed * 3.6, 'FRWheelSpd': speed * 3.6},
    'EBCMWheelSpdRear': {'RLWheelSpd': speed * 3.6, 'RRWheelSpd': speed * 3.6, 'RLWheelDir': 1, 'RRWheelDir': 1},
    'ECMCruiseControl': {'CruiseActive': 1, 'CruiseSetSpeed': target_speed * 3.6},
    'ECMPRDNL2': {'PRNDL2': gear},
    'EBCMRegenPaddle': {},
  }
  for name, values in replacements.items():
    msg = packer.make_can_msg(name, 0, values)
    frames = [frame for frame in frames if frame[0] != msg[0]] + [msg]
  message = gmcan.create_buttons(packer, 0, counter, button)
  return [frame for frame in frames if frame[0] != 0x1e1] + [message]


def original_request(speed, stock_speed, accel, cruise_kph, lead, metric):
  # Independent request-law literals.
  convert = 3.6 if metric else 1 / .44704
  stock, ego = round(stock_speed * convert), speed * convert
  projected = (speed * 1.01 + 3 * accel) * convert
  cap = round(cruise_kph if metric else cruise_kph / 1.609344) if 0 < cruise_kph < 255 else None
  toward = cap is not None and ((accel > 0 and stock < cap) or (accel < 0 and stock > cap))
  if cap is not None and ((accel > 0 and stock >= cap) or (accel < 0 and stock <= cap and not lead)):
    return 0, float('inf')
  if accel == 0 or (not toward and abs(projected - stock) <= (2 if lead else 5) * (1.609344 if metric else 1)):
    return 0, float('inf')
  if accel < 0:
    return 3, .2 if stock > ego + 3 else max(1 / (-accel * convert), .2)
  return 2, .2 if stock < ego - 3 else max(1 / (accel * convert), .2)


def original_bytes(counter, button):
  checksum = 255 + counter * 1263 - (button - 1) * 16
  return bytes((0, 0, 0, 1, counter, button * 16 + (checksum >> 8), checksum & 255))


def fixture(alpha=False):
  controls, base, offset = controls_fixture()
  cp = params(alpha)
  controls.CP = cp
  controls.longitudinal_inputs.CP = controls.CP
  controls.CI = CarInterface(cp)
  controls.VM = VehicleModel(cp)
  controls.LoC = LongControl(cp)
  controls.longitudinal_inputs.gm_volt_enabled = controls.longitudinal_inputs.gm_euv_enabled = False
  controls.longitudinal_inputs.gm_cc_enabled = True
  controls.longitudinal_inputs.gm_cc_boot_offset_ns, controls.longitudinal_inputs.gm_cc_source_floor_ns = offset, base - 500_000_000
  controls.longitudinal_inputs.gm_cc_evidence = None
  for name in ('driverAssistance', 'carOutput', 'driverMonitoringState'):
    controls.sm.data[name] = getattr(messaging.new_message(name), name)
    controls.sm.valid[name] = False
  controls.calibrated_pose = controls.last_lane_centering_result = None
  controls.lane_centering_applied = 0.
  controls.lane_centering_inactive_reason = 'inactive'
  controls.pm = NS(send=lambda *args: None)
  lateral_log = messaging.new_message('controlsState').controlsState.lateralControlState.init(
    'torqueState' if controls.CP.lateralTuning.which() == 'torque' else 'pidState')
  controls.LaC = NS(reset=lambda: None, update=lambda *args: (0., 0., lateral_log))
  ci = controls.CI
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  card = Car.__new__(Car)
  from openpilot.starpilot.vehicle_startup import VehicleStartupOwner
  card.vehicle_startup = VehicleStartupOwner()
  card.CP, card.CI, card.sm = cp, ci, controls.sm
  card.ci_initialized = card.volt_cc_selected = True
  card.volt_cc_boot_offset_ns, card.volt_cc_source_floor_ns = offset, base - 500_000_000
  card.ioniq6_long_prearmed = False
  card.ioniq6_keepalive = None
  card.is_metric = False
  sent = []
  card.publish_sendcan = Mock(side_effect=lambda frames, valid: sent.append((frames, valid)))
  card.learning_send_committed_mono_ns = 0
  controls.sm.all_alive = lambda names: all(controls.sm.alive[name] for name in names)
  return controls, card, ci, packer, sent, base, offset


class TestVoltCc(unittest.TestCase):
  def test_exact_manual_cp_and_release_missing_source_denial(self):
    for alpha in (False, True):
      cp = params(alpha)
      self.assertTrue(is_volt_cc_longitudinal(cp))
      self.assertFalse(cp.pcmCruise or cp.alphaLongitudinalAvailable)
      self.assertEqual(cp.safetyConfigs[0].safetyParam, 20)
      self.assertAlmostEqual(cp.minEnableSpeed, 24 * .44704, places=5)
      self.assertAlmostEqual(cp.stopAccel, -1.5)
      self.assertEqual(extension_state(LongControl(cp), 'vehicle_policy').kp, ((10.7, 10.8, 28.), (0., 5., 2.)))
      self.assertAlmostEqual(cp.longitudinalTuning.kiV[0], .1)
      self.assertFalse(is_volt_cc_longitudinal(params(alpha, release=True)))
      for addr in REQUIRED:
        self.assertFalse(is_volt_cc_longitudinal(params(alpha, missing=addr)), hex(addr))
      for field, value in (('passive', True), ('pcmCruise', True), ('flags', 0), ('radarUnavailable', False)):
        invalid = cp.as_reader().as_builder()
        setattr(invalid, field, value)
        self.assertFalse(is_volt_cc_longitudinal(invalid))
    cc_source_signatures = [fingerprint for fingerprint in FINGERPRINTS[CAR.CHEVROLET_VOLT_CC]
                            if all(fingerprint.get(addr) == size for addr, size in REQUIRED.items())]
    self.assertEqual(len(cc_source_signatures), 1)
    self.assertNotIn(CAR.CHEVROLET_VOLT_CC, FW_VERSIONS)
    self.assertEqual(CAR.CHEVROLET_VOLT_CC.config.car_docs[0].package,
                     "Manual development configuration; installation must be verified")

  def test_actual_controls_constructor_registers_required_services(self):
    class Registered(Exception):
      pass

    captured = {}
    def register(services, **options):
      captured['services'] = services
      raise Registered

    for alpha in (False, True):
      with patch('openpilot.selfdrive.controls.controlsd.Params'), \
           patch('openpilot.selfdrive.controls.controlsd.messaging.log_from_bytes', return_value=params(alpha)), \
           patch('openpilot.selfdrive.controls.controlsd.read_selection', return_value=NS(policy=None, mode=default_selection(params(alpha)).mode)), \
           patch('openpilot.selfdrive.controls.controlsd.learning_allowed', return_value=False), \
           patch('openpilot.selfdrive.controls.controlsd.feature_enabled', return_value=False), \
           patch('openpilot.starpilot.longitudinal.inputs.toyota_development_enabled', return_value=False), \
           patch('openpilot.selfdrive.controls.controlsd.messaging.SubMaster', side_effect=register):
        with self.assertRaises(Registered):
          Controls()
      self.assertTrue({'radarState', 'deviceState', 'carState', 'longitudinalPlan'}.issubset(captured['services']))

  def test_primary_lead_agreement_preserves_secondary_only_authority(self):
    for alpha in (False, True):
      for primary, secondary, planned in ((False, True, False), (True, False, True),
                                          (False, True, True), (True, False, False)):
        controls, _, _, _, _, now, offset = fixture(alpha)
        car, plan, radar = (controls.sm[name] for name in ('carState', 'longitudinalPlan', 'radarState'))
        car.vEgo, car.standstill, car.cruiseState.standstill = 0., True, True
        plan.aTarget, plan.shouldStop, plan.hasLead = .2, False, planned
        radar.leadOne.present, radar.leadTwo.present = primary, secondary
        controls.LoC.long_control_state = structs.CarControl.Actuators.LongControlState.stopping
        controls.LoC.last_output_accel = -.25
        with patch.dict(os.environ, {'REPLAY': '0'}), \
             patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)):
          evidence = controls.longitudinal_inputs._gm_cc_evidence()
          self.assertEqual(evidence is not None, primary == planned)
          if evidence is not None:
            self.assertEqual(evidence.has_lead, primary)
          cc, _ = controls.state_control()
          self.assertEqual(bool(cc.longActive), primary == planned)
          self.assertEqual(int(controls.LoC.long_control_state), 1 if primary and planned else 2 if primary == planned else 0)

  def test_actual_parsed_controls_publish_card_and_controller(self):
    for alpha in (False, True):
      for metric in (False, True):
        for alignment in range(4):
          controls, card, ci, packer, sent, base, offset = fixture(alpha)
          card.is_metric = metric
          ci.CC.frame = alignment
          original_i = 0.
          original_last_frame = 0
          prior_state = 'off'
          for tick in range(160):
            now = base + tick * 10_000_000
            counter = (tick // 3) % 4
            out = ci.update([(now + offset - 2_000_000, physical_frames(packer, counter=counter))])
            self.assertTrue(out.canValid)
            out.vCruise = out.vCruiseCluster = 100.
            out.aEgo = 0.
            controls.sm.data['carState'] = out
            for name in controls.sm.logMonoTime:
              controls.sm.logMonoTime[name] = now - 2_000_000
              controls.sm.recv_time[name] = (now - 1_000_000) / 1e9
            target = .5 if tick < 80 else -.4
            plan = controls.sm['longitudinalPlan']
            plan.aTarget, plan.shouldStop = target, False
            plan.hasLead = controls.sm['radarState'].leadOne.present = tick < 80
            controls.sm['radarState'].leadTwo.present = True
            # Original current/default PID is acceleration error, no deprecated speed deadzone.
            error = float(plan.aTarget) - out.aEgo
            if original_i > 0 and target < -.05 and error < -.25:
              factor = .55 + (.25 - .55) * (abs(error) - .25) / .5
              original_i *= factor
            candidate = original_i + float(controls.CP.longitudinalTuning.kiV[0]) * .01 * error
            kp = 5 + (2 - 5) * (out.vEgo - 10.8) / (28 - 10.8)
            test = kp * error + candidate + float(plan.aTarget)
            original_i = min(max(candidate, original_i if test < -4 else -4), original_i if test > 2 else 2)
            expected = min(max(kp * error + original_i + float(plan.aTarget), -4), 2)
            with patch.dict(os.environ, {'REPLAY': '0'}), \
                 patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)), \
                 patch('openpilot.selfdrive.car.card.clock_pair_ns', return_value=(now, now + offset)), \
                 patch('openpilot.selfdrive.car.card.time.monotonic', return_value=now / 1e9), \
                 patch('openpilot.selfdrive.car.card.REPLAY', False):
              cc, log = controls.state_control()
              self.assertTrue(cc.longActive)
              self.assertAlmostEqual(cc.actuators.accel, expected, places=5)
              self.assertEqual(str(cc.actuators.longControlState), prior_state)
              prior_state = 'pid'
              controls.publish(cc, log)
              controls.sm.data['carControl'] = cc
              for mapping in (controls.sm.seen, controls.sm.alive, controls.sm.valid):
                mapping['carControl'] = True
              controls.sm.logMonoTime['carControl'] = now - 2_000_000
              controls.sm.recv_time['carControl'] = (now - 1_000_000) / 1e9
              frame = ci.CC.frame
              card.controls_update(out, cc.as_reader())
            button, rate = original_request(out.vEgo, out.cruiseState.speed, float(cc.actuators.accel), out.vCruise, bool(plan.hasLead), metric)
            wanted = []
            if frame % 4 == 0 and (frame - original_last_frame) * .01 > rate:
              wanted = [(0x1e1, original_bytes((counter + 1) % 4, button), 0)]
              original_last_frame = frame
            actual = [tuple(msg) for msg in sent[-1][0] if msg[0] == 0x1e1]
            self.assertEqual(actual, wanted, (alpha, metric, alignment, tick))
            self.assertFalse(any(msg[0] in (0x2cb, 0x315, 0x200, 0x3d1) for msg in sent[-1][0]))


  def test_slc_cap_reaches_cruise_down_without_changing_driver_set(self):
    for metric in (False, True):
      with self.subTest(metric=metric):
        controls, card, ci, packer, sent, base, offset = fixture()
        card.is_metric, card.slc_control_enabled, card.slc_source_floor_ns = metric, True, 0
        state = messaging.new_message('slcState').slcState
        state.enabled, state.sessionId, state.hasCeiling, state.effectiveCap = True, 'drive', True, 45 * .44704
        controls.sm.data['slcState'] = state
        for flags in (controls.sm.seen, controls.sm.valid, controls.sm.alive):
          flags['slcState'] = True
          flags['carControl'] = True
        command = structs.CarControl(enabled=True, latActive=True, longActive=True)
        command.actuators.accel = -1.
        command.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
        command.hudControl.setSpeed = 70 * .44704
        controls.sm.data['carControl'] = command
        controls.sm.logMonoTime['carControl'] = base
        by_phase = []
        for phase in ('active', 'stale'):
          directions = []
          for tick in range(100):
            now = base + (len(by_phase) * 100 + tick) * 10_000_000
            out = ci.update([(now + offset - 2_000_000,
                              physical_frames(packer, speed=55 * .44704, target_speed=70 * .44704, counter=(tick // 3) % 4))])
            self.assertTrue(out.canValid)
            out.vCruise = out.vCruiseCluster = 70 * .44704 * 3.6
            for name in controls.sm.logMonoTime:
              controls.sm.logMonoTime[name] = now - 2_000_000
              controls.sm.recv_time[name] = (now - 1_000_000) / 1e9
            controls.sm.logMonoTime['slcState'] = state.frameMonoTime = now - (2_000_000 if phase == 'active' else 200_000_000)
            with patch('openpilot.selfdrive.car.card.clock_pair_ns', return_value=(now, now + offset)), \
                 patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now), \
                 patch('openpilot.selfdrive.car.card.REPLAY', False):
              card.controls_update(out, command.as_reader())
            directions.extend((data[5] >> 4) & 7 for address, data, _ in sent[-1][0] if address == 0x1e1)
            self.assertAlmostEqual(out.vCruise, 70 * .44704 * 3.6, places=4)
            self.assertAlmostEqual(command.hudControl.setSpeed, 70 * .44704, places=4)
          by_phase.append(directions)
        self.assertIn(3, by_phase[0])
        self.assertEqual(by_phase[1], [])
        self.assertIsNone(ci.CC.volt_cc_speed_ceiling_mps)

  def test_actual_card_constructor_subscribes_selected_consumer(self):
    class Registered(Exception):
      pass

    captured = []
    def register(services, **options):
      captured.append(list(services))
      raise Registered

    ci = CarInterface(params())
    with patch('openpilot.selfdrive.car.card.prewarm_cache_contracts'), \
         patch('openpilot.selfdrive.car.card.Params') as saved, \
         patch('openpilot.selfdrive.car.card.feature_requested', return_value=False), \
         patch('openpilot.selfdrive.car.card.messaging.sub_sock'), \
         patch('openpilot.selfdrive.car.card.messaging.SubMaster', side_effect=register):
      saved.return_value.get_bool.return_value = False
      saved.return_value.get.return_value = None
      with self.assertRaises(Registered):
        Car(CI=ci, RI=RadarInterface(ci.CP))
    self.assertEqual(len(captured), 1)
    self.assertEqual(set(captured[0]), {'pandaStates', 'carControl', 'onroadEvents', 'deviceState', 'autoSteeringLearning'})

  def test_actual_card_transport_epoch_and_individual_source_loss(self):
    controls, card, ci, packer, sent, base, offset = fixture()
    now = base
    out = ci.update([(now + offset - 2_000_000, physical_frames(packer))])
    cc = structs.CarControl(enabled=True, longActive=True)
    cc.actuators.accel = 1.
    for mapping in (card.sm.seen, card.sm.alive, card.sm.valid):
      mapping['carControl'] = True
    card.sm.data['carControl'] = cc
    card.sm.logMonoTime['carControl'] = now - 2_000_000
    card.sm.recv_time['carControl'] = (now - 1_000_000) / 1e9
    with patch('openpilot.selfdrive.car.card.clock_pair_ns', return_value=(now, now + offset)), \
         patch('openpilot.selfdrive.car.card.time.monotonic', return_value=now / 1e9), \
         patch('openpilot.selfdrive.car.card.REPLAY', False):
      card.controls_update(out, cc.as_reader())
      frame = ci.CC.frame
      card.sm.logMonoTime['carControl'] = now - 150_000_001
      card.controls_update(out, cc.as_reader())
      self.assertEqual(ci.CC.frame, frame)
      self.assertEqual(sent[-1], ([], False))
      card.sm.logMonoTime['carControl'] = now - 2_000_000
      card.sm['deviceState'].startedMonoTime = now - 1_000_000
      card.sm.logMonoTime['deviceState'] = now - 500_000
      card.sm.recv_time['deviceState'] = (now - 200_000) / 1e9
      card.controls_update(out, cc.as_reader())
      self.assertEqual(ci.CC.frame, frame)
      card.sm.logMonoTime['carControl'] = now - 300_000
      card.sm.recv_time['carControl'] = (now - 200_000) / 1e9
      # Fresh command cannot authorize physical observations retained from the old drive.
      card.controls_update(out, cc.as_reader())
      self.assertEqual(ci.CC.frame, frame)
      out = ci.update([(now + offset - 400_000, physical_frames(packer))])
      ci.CC.frame = 100
      card.controls_update(out, cc.as_reader())
      self.assertEqual(ci.CC.frame, 101)
      self.assertFalse(any(msg[0] == 0x1e1 for msg in sent[-1][0]))
      # A duplicate counter cannot carry pre-drive credit into the new drive.
      out = ci.update([(now + offset - 300_000, physical_frames(packer, counter=1))])
      ci.CC.frame = 104
      card.controls_update(out, cc.as_reader())
      self.assertTrue(any(msg[0] == 0x1e1 for msg in sent[-1][0]))
    # Receiving unrelated PT traffic never advances the physical button source timestamp.
    previous = ci.CS.volt_cc_physical.source_ns[1]
    later = now + 110_000_000
    frames = [msg for msg in physical_frames(packer) if msg[0] != 0x1e1]
    ci.update([(later + offset - 1_000_000, frames)])
    self.assertEqual(ci.CS.volt_cc_physical.source_ns[1], previous)
    self.assertFalse(ci.CS.volt_cc_physical.current(later + offset))

  def test_actual_longcontrol_stopping_recurrence_and_release(self):
    states = structs.CarControl.Actuators.LongControlState
    for alpha in (False, True):
      for speed in (0., .75, 1.75, 3., 6., 10.):
        loop = LongControl(params(alpha))
        cs = structs.CarState.new_message(canValid=True, vEgo=speed)
        cs.cruiseState.standstill = speed == 0.
        expected = 0.
        for tick in range(20):
          if expected > -1.5:
            expected = min(expected, 0.) - .1118
          if speed > 1.75 and -2. < expected - .25:
            step = .03 if speed == 3. else .05 if speed == 6. else .07
            expected = max(-2., expected - step)
          actual = loop.update(True, cs, -2., True, (-4., 2.),
                               context=LongitudinalContext(vehicle_stop_evidence=VoltCcEvidence(1, 1_000_000_000 + tick * 10_000_000, False)))
          self.assertAlmostEqual(actual, expected, places=6)
          self.assertEqual(loop.long_control_state, states.stopping)
        cs.vEgo = .75
        cs.cruiseState.standstill = False
        loop.update(True, cs, .15, False, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltCcEvidence(1, 1_200_000_000, False)))
        self.assertEqual(loop.long_control_state, states.stopping)
        loop.update(True, cs, .45, False, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltCcEvidence(1, 1_210_000_000, False)))
        self.assertEqual(loop.long_control_state, states.pid)
        self.assertEqual(loop.update(False, cs, 0., False, (-4., 2.)), 0.)
        self.assertEqual(loop.long_control_state, states.off)

  def test_actual_controls_card_controller_stopping_and_release(self):
    states = structs.CarControl.Actuators.LongControlState
    for alpha in (False, True):
      for speed in (0., 6.):
        controls, card, ci, packer, sent, base, offset = fixture(alpha)
        expected = 0.
        prior = states.off
        for tick in range(22):
          now = base + tick * 10_000_000
          out = ci.update([(now + offset - 2_000_000, physical_frames(packer, speed=speed, counter=(tick // 3) % 4))])
          controls.sm.data['carState'] = out
          plan = controls.sm['longitudinalPlan']
          plan.aTarget, plan.shouldStop = (-2., True) if tick < 20 else (.5, False)
          for name in controls.sm.logMonoTime:
            controls.sm.logMonoTime[name] = now - 2_000_000
            controls.sm.recv_time[name] = (now - 1_000_000) / 1e9
          with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)), \
               patch('openpilot.selfdrive.car.card.clock_pair_ns', return_value=(now, now + offset)), \
               patch('openpilot.selfdrive.car.card.REPLAY', False):
            cc, lateral = controls.state_control()
            controls.publish(cc, lateral)
            controls.sm.data['carControl'] = cc
            for mapping in (controls.sm.seen, controls.sm.alive, controls.sm.valid):
              mapping['carControl'] = True
            controls.sm.logMonoTime['carControl'] = now - 2_000_000
            controls.sm.recv_time['carControl'] = (now - 1_000_000) / 1e9
            card.controls_update(out, cc.as_reader())
          self.assertEqual(cc.actuators.longControlState, prior)
          prior = controls.LoC.long_control_state
          if tick < 20:
            if expected > -1.5:
              expected = min(expected, 0.) - .1118
            if out.vEgo > 1.75 and -2. < expected - .25:
              # Compare the same parsed physical speed, including DBC quantization.
              step = (.05 + (out.vEgo - 6.) * .02 / 4. if out.vEgo >= 6. else
                      .03 + (out.vEgo - 3.) * .02 / 3.)
              expected = max(-2., expected - step)
            self.assertAlmostEqual(cc.actuators.accel, expected, places=5)
            self.assertEqual(prior, states.stopping)
          else:
            self.assertEqual(prior, states.pid)
            self.assertAlmostEqual(cc.actuators.accel, .5 + (tick - 19) * .0005, places=5)
          self.assertFalse(any(msg[0] in (0x1e1, 0x200, 0x315, 0x2cb, 0x3d1) for msg in sent[-1][0]))

  def test_actual_parser_counter_credit_rejects_duplicates_and_rebases(self):
    _, _, ci, packer, _, base, offset = fixture()
    command = structs.CarControl.new_message(enabled=True, longActive=True)
    command.actuators.accel = 2.
    command.hudControl.leadVisible = True
    now = base + offset
    ci.update([(now - 1_000_000, physical_frames(packer, counter=3))])
    ci.CC.frame = 100
    _, frames = ci.apply(command.as_reader(), now)
    self.assertTrue(any(frame[0] == 0x1e1 for frame in frames))
    consumed = ci.CS.volt_cc_physical.button_credit_ns
    for _tick in range(1, 29):
      now += 10_000_000
      ci.update([(now - 1_000_000, physical_frames(packer, counter=3))])
      _, frames = ci.apply(command.as_reader(), now)
      self.assertFalse(any(frame[0] == 0x1e1 for frame in frames))
      self.assertEqual(ci.CS.volt_cc_physical.button_credit_ns, consumed)
    now += 10_000_000
    ci.update([(now - 1_000_000, physical_frames(packer, counter=0))])
    self.assertGreater(ci.CS.volt_cc_physical.button_credit_ns, consumed)
    ci.CC.frame = 132
    _, frames = ci.apply(command.as_reader(), now)
    self.assertTrue(any(frame[0] == 0x1e1 for frame in frames))
    # A skipped counter and a stale gap both rebase without issuing fresh credit.
    for elapsed, counter in ((10_000_000, 2), (110_000_000, 3)):
      now += elapsed
      ci.update([(now - 1_000_000, physical_frames(packer, counter=counter))])
      self.assertEqual(ci.CS.volt_cc_physical.button_credit_ns, 0)
    now += 10_000_000
    ci.update([(now - 1_000_000, physical_frames(packer, counter=0))])
    self.assertGreater(ci.CS.volt_cc_physical.button_credit_ns, 0)

  def test_stop_release_boundaries_and_continuity(self):
    states = structs.CarControl.Actuators.LongControlState
    cs = structs.CarState.new_message(canValid=True, vEgo=.75)
    cs.cruiseState.standstill = False
    for target, lead, speed, immediate in ((.15, True, .75, False), (.150001, True, .75, True),
                                          (.449999, False, .75, False), (.45, False, .75, True),
                                          (.15, False, .750001, True)):
      policy = VoltCcStopPolicy()
      cs.vEgo = speed
      evidence = VoltCcEvidence(1, 1_000_000_000, lead)
      result = policy.transition(states.pid, states.stopping, True, cs, target, False, evidence)
      self.assertEqual(result, states.pid if immediate else states.stopping)
    cs.vEgo = .75
    policy = VoltCcStopPolicy()
    for sample in range(35):
      result = policy.transition(states.pid, states.stopping, True, cs, .2, False,
                                 VoltCcEvidence(1, 1_000_000_000 + sample * 10_000_000, False))
      self.assertEqual(result, states.pid if sample == 34 else states.stopping)
    self.assertEqual(policy.transition(states.pid, states.stopping, True, cs, .2, False, None), states.stopping)
    self.assertIsNone(policy.release_since_ns)
    self.assertEqual(policy.transition(states.pid, states.stopping, False, cs, .2, False, None), states.off)

  def test_actual_controller_low_speed_and_gas_preserve_lateral_request(self):
    for speed, gas in ((4., False), (20., True)):
      _, _, ci, packer, _, base, offset = fixture()
      out = ci.update([(base + offset - 2_000_000, physical_frames(packer, speed=speed, gas=gas))])
      self.assertTrue(out.canValid)
      command = structs.CarControl.new_message(enabled=True, latActive=True, longActive=True)
      command.actuators.torque = .1
      command.actuators.accel = 2.
      ci.CC.frame = 100
      _, frames = ci.apply(command.as_reader(), base + offset)
      steering = [frame for frame in frames if frame[0] == 0x180]
      self.assertEqual(len(steering), 1)
      self.assertTrue(steering[0][1][0] & 8)
      self.assertFalse(any(frame[0] == 0x1e1 for frame in frames))

  def test_actual_sendcan_uses_parser_boot_clock_only_for_selected_profile(self):
    card = Car.__new__(Car)
    from openpilot.starpilot.vehicle_startup import VehicleStartupOwner
    card.vehicle_startup = VehicleStartupOwner()
    card.ioniq6_long_prearmed = False
    card.ioniq6_keepalive = None
    card.volt_cc_now_boot_ns = 9_000_000_000
    packets = []
    self.enterContext(patch.object(card, 'pm', NS(send=lambda service, packet: packets.append(packet)), create=True))
    frame = (0x1e1, original_bytes(1, 6), 0)
    for selected, expected in ((True, 9_000_000_000), (False, 5_000_000_000)):
      card.volt_cc_selected = selected
      with patch('openpilot.selfdrive.pandad.pandad_api_impl.time.monotonic', return_value=5.):
        card.publish_sendcan([frame])
      message = messaging.log_from_bytes(packets[-1])
      self.assertEqual(message.logMonoTime, expected)
      self.assertEqual(bytes(message.sendcan[0].dat), frame[1])
      self.assertTrue(message.valid)

  def test_actual_card_suspend_epoch_requires_new_command_and_physical_sources(self):
    controls, card, ci, packer, sent, base, offset = fixture()
    cc = structs.CarControl.new_message(enabled=True, longActive=True)
    cc.actuators.accel = 1.
    for mapping in (card.sm.seen, card.sm.alive, card.sm.valid):
      mapping['carControl'] = True
    card.sm.data['carControl'] = cc
    now = base + 1_000_000_000
    new_offset = offset + 4_000_000_000
    out = ci.update([(now + new_offset - 2_000_000, physical_frames(packer))])
    with patch('openpilot.selfdrive.car.card.clock_pair_ns', side_effect=lambda: (now, now + new_offset)), \
         patch('openpilot.selfdrive.car.card.REPLAY', False):
      frame = ci.CC.frame
      card.controls_update(out, cc.as_reader())
      self.assertEqual(ci.CC.frame, frame)
      # A suspend clock discontinuity establishes a new source floor.
      now += 10_000_000
      for name in ('carControl', 'deviceState'):
        card.sm.logMonoTime[name] = now - 2_000_000
        card.sm.recv_time[name] = (now - 1_000_000) / 1e9
      card.controls_update(out, cc.as_reader())
      self.assertEqual(ci.CC.frame, frame)
      out = ci.update([(now + new_offset - 2_000_000, physical_frames(packer))])
      card.controls_update(out, cc.as_reader())
      self.assertEqual(ci.CC.frame, frame + 1)
      self.assertEqual(card.volt_cc_now_boot_ns, now + new_offset)

  def test_actual_enable_disable_cancel_and_driver_override(self):
    for override in ('none', 'gas', 'brake'):
      controls, card, ci, packer, sent, base, offset = fixture()
      lateral_log = messaging.new_message('controlsState').controlsState.lateralControlState.init(
        'torqueState' if controls.CP.lateralTuning.which() == 'torque' else 'pidState')
      controls.LaC.update = lambda *args, log=lateral_log: (.1, 0., log)
      gas_steering = []
      for tick in range(44):
        now = base + tick * 10_000_000
        disabled = tick >= 40 and override != 'gas'
        brake = tick >= 40 and override == 'brake'
        gas = tick >= 40 and override == 'gas'
        out = ci.update([(now + offset - 2_000_000, physical_frames(packer, counter=(tick // 3) % 4, brake=brake, gas=gas))])
        controls.sm.data['carState'] = out
        controls.sm['selfdriveState'].enabled = not disabled
        controls.sm['selfdriveState'].active = not disabled
        controls.sm.data['onroadEvents'] = [NS(overrideLongitudinal=True)] if gas else []
        controls.sm['longitudinalPlan'].aTarget = .5
        for name in controls.sm.logMonoTime:
          controls.sm.logMonoTime[name] = now - 2_000_000
          controls.sm.recv_time[name] = (now - 1_000_000) / 1e9
        with patch.dict(os.environ, {'REPLAY': '0'}), \
             patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)), \
             patch('openpilot.selfdrive.car.card.clock_pair_ns', return_value=(now, now + offset)), \
             patch('openpilot.selfdrive.car.card.time.monotonic', return_value=now / 1e9), \
             patch('openpilot.selfdrive.car.card.REPLAY', False):
          cc, lateral = controls.state_control()
          controls.publish(cc, lateral)
          controls.sm.data['carControl'] = cc
          for mapping in (controls.sm.seen, controls.sm.alive, controls.sm.valid):
            mapping['carControl'] = True
          controls.sm.logMonoTime['carControl'] = now - 2_000_000
          controls.sm.recv_time['carControl'] = (now - 1_000_000) / 1e9
          card.controls_update(out, cc.as_reader())
        buttons = [msg for msg in sent[-1][0] if msg[0] == 0x1e1]
        if tick == 40:
          if override == 'gas':
            self.assertFalse(buttons)  # Original gas-override adaptive SET remains explicitly outside this bounded port.
            self.assertFalse(cc.longActive)
            self.assertTrue(cc.enabled and cc.latActive)
          else:
            self.assertEqual([tuple(msg) for msg in buttons], [(0x1e1, original_bytes(((tick // 3) % 4 + 1) % 4, 6), 0)])
            self.assertFalse(cc.enabled or cc.longActive)
        elif tick > 40:
          self.assertFalse(buttons)
        if gas:
          steering = [msg for msg in sent[-1][0] if msg[0] == 0x180]
          self.assertTrue(all(msg[1][0] & 8 for msg in steering))
          gas_steering.extend(steering)
      if override == 'gas':
        self.assertTrue(gas_steering)


if __name__ == '__main__':
  unittest.main()
