"""Default Volt STOP ownership, qualified release, and parsed Controls-to-CAN recurrence."""

from openpilot.starpilot.lateral.controller_selection import default_selection
from openpilot.starpilot.longitudinal.extension import LongitudinalContext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from opendbc.car import Bus
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.longitudinal import VoltStopEvidence, volt_policy_for
from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
from opendbc.car.gm.values import CAR, DBC
from opendbc.car.vehicle_model import VehicleModel
from opendbc.can import CANPacker
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.starpilot.longitudinal.tests.test_gm_euv_long_policy import interp
from openpilot.starpilot.longitudinal.tests.test_gm_volt_cc_policy import fixture, physical_frames
from openpilot.starpilot.longitudinal.tests.test_gm_volt_long_policy import controls_fixture, observed_lead, state


def profiles():
  for candidate, accelerator, sascm in ((CAR.CHEVROLET_VOLT, True, False),
                                       (CAR.CHEVROLET_VOLT, False, False),
                                       (CAR.CHEVROLET_VOLT_ASCM, True, True)):
    for alpha in (False, True):
      yield ordinary_params(candidate, alpha=alpha, accelerator=accelerator, sascm=sascm, radar=True), accelerator, sascm, alpha


def original_stop(output, speed, target, gateway, brake=False):
  floor, rate, minimum = (-1.5, 3., 1.75) if gateway else (-.25, 1., 1.5)
  if output > floor:
    output = min(output, 0.) - rate * .01
  if not brake and speed > minimum and target < output - .25:
    output = max(target, output - interp(speed, [minimum, 3., 6., 10.], [.02, .03, .05, .07]))
  return min(max(output, -4.), 2.)


def original_brake(accel, speed, cp):
  drag = .5 * .30 * (1.05 * cp.wheelbase + .0679) * 1.225 * speed ** 2 / cp.mass
  threshold = interp(speed, [1.29, 1.52, 1.55, 1.6, 1.7, 1.8, 2., 2.2, 2.5, 5.52, 9.6, 20.5, 23.5, 35.],
                     [0., -.14, -.16, -.18, -.215, -.255, -.32, -.41, -.5, -.72, -.895, -1.125, -1.145, -1.16])
  return round(interp(min(max(accel + drag, -4.), 2.), [-4., threshold], [400., 0.]))


def brake_wire(brake, counter, full_stop=False):
  mode = (13 if full_stop else 10) if brake else 1
  raw = (4096 - brake) & 4095
  checksum = (65536 - (mode << 12) - raw - counter) & 65535
  return bytes([(mode << 4) | (raw >> 8), raw & 255, checksum >> 8, checksum & 255, counter])


class TestVoltStop(unittest.TestCase):
  def test_retired_tune_uses_vehicle_default_for_every_volt_owner(self):
    from opendbc.car.gm.tests.test_volt_transitions import (volt_ascm_pedal_params, volt_camera_pedal_params,
                                                          volt_sdgm_pedal_params, volt_cc_pedal_params)
    candidates = [cp for cp, *_ in profiles()]
    for helper in (volt_ascm_pedal_params, volt_camera_pedal_params, volt_sdgm_pedal_params, volt_cc_pedal_params):
      for pedal in (False, True):
        kwargs = {"pedal": pedal, "alpha": True}
        if helper is not volt_camera_pedal_params:
          kwargs.update(radar=True, sascm=not pedal and helper is not volt_cc_pedal_params)
        candidates.append(helper(**kwargs))
    for cp in candidates:
      with self.subTest(owner=cp.carFingerprint, safety=cp.safetyConfigs[0].safetyParam):
        before = cp.to_dict()
        normal = LongControl(cp)
        retired = LongControl(cp, startup_preferences=NS(gm_longitudinal_tune=2))
        if normal.extension is None:
          self.assertIsNone(retired.extension)
          self.assertEqual(cp.to_dict(), before)
          continue
        self.assertEqual(type(normal.extension.vehicle_policy), type(retired.extension.vehicle_policy))
        self.assertEqual(type(normal.extension.vehicle_stop), type(retired.extension.vehicle_stop))
        self.assertEqual((normal.extension.kp, normal.extension.ki), (retired.extension.kp, retired.extension.ki))
        for stopped in (True, False):
          for _tick in range(20):
            cs = state(0.)
            expected = normal.update(True, cs, .5, stopped, (-4., 2.), context=LongitudinalContext())
            actual = retired.update(True, cs, .5, stopped, (-4., 2.), context=LongitudinalContext())
            self.assertEqual(actual, expected)
            self.assertEqual(retired.long_control_state, normal.long_control_state)
        self.assertEqual(cp.to_dict(), before)


  def test_actual_constructor_services_and_exact_final_owner(self):
    class Registered(Exception):
      pass
    for cp, _, sascm, alpha in profiles():
      selected = not sascm or alpha
      owner = volt_policy_for(cp)
      self.assertEqual(owner is not None, selected)
      if selected:
        self.assertEqual((owner.stopping_decel_rate, owner.starting_speed, cp.stopAccel),
                         (1., .25, -.25) if sascm else (3., .75, -1.5))
      captured = {}
      def register(services, output=captured, **options):
        output.update(services=services, options=options)
        raise Registered
      with patch('openpilot.selfdrive.controls.controlsd.Params'), \
           patch('openpilot.selfdrive.controls.controlsd.messaging.log_from_bytes', return_value=cp), \
           patch('openpilot.selfdrive.controls.controlsd.read_selection', return_value=NS(policy=None, mode=default_selection(cp).mode)), \
           patch('openpilot.selfdrive.controls.controlsd.learning_allowed', return_value=False), \
           patch('openpilot.selfdrive.controls.controlsd.feature_enabled', return_value=False), \
           patch('openpilot.starpilot.longitudinal.inputs.toyota_development_enabled', return_value=False), \
           patch('openpilot.selfdrive.controls.controlsd.messaging.SubMaster', side_effect=register):
        with self.assertRaises(Registered):
          Controls()
      self.assertEqual(captured['services'].count('deviceState'), 1)
      self.assertEqual(captured['services'].count('radarState'), int(selected))
      for name in ('deviceState',) + (('radarState',) if selected else ()):
        for ignored in ('ignore_alive', 'ignore_valid', 'ignore_avg_freq'):
          self.assertIn(name, captured['options'][ignored])

  def test_stop_recurrence_and_release_boundaries(self):
    for cp, _, sascm, alpha in profiles():
      if sascm and not alpha:
        continue
      for speed in (0., .25, .75, 1.5, 1.75, 6., 10.):
        long = LongControl(cp)
        wanted = 0.
        for tick in range(100):
          evidence = VoltStopEvidence(1, 1_000_000_000 + tick * 10_000_000, False)
          wanted = original_stop(wanted, speed, -2., not sascm)
          output = long.update(True, state(speed), -2., True, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=evidence))
          self.assertAlmostEqual(output, wanted, places=7)
      for target, lead, still, count in ((.15, False, False, 40), (.2, False, True, 35),
                                       (.2, True, True, 1), (.45, False, False, 1)):
        long = LongControl(cp)
        cs = state(0.)
        cs.cruiseState.standstill = still
        long.update(True, cs, 0., True, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(1, 1_000_000_000, lead)))
        for tick in range(1, count + 1):
          long.update(True, cs, target, False, (-4., 2.),
            context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(1, 1_000_000_000 + tick * 10_000_000, lead)))
          expected = 'pid' if target > .15 and tick >= count else 'stopping'
          self.assertEqual({0: 'off', 1: 'pid', 2: 'stopping', 3: 'starting'}[int(long.long_control_state)], expected)

  def test_exact_start_speed_and_twenty_ms_continuity_boundary(self):
    for cp, _, sascm, alpha in profiles():
      if sascm and not alpha:
        continue
      threshold = .25 if sascm else .75
      for speed in (threshold, threshold + .000001):
        long = LongControl(cp)
        cs = state(speed)
        cs.cruiseState.standstill = True
        long.update(True, cs, 0., True, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(1, 1_000_000_000, False)))
        long.update(True, cs, .1, False, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(1, 1_010_000_000, False)))
        self.assertEqual(int(long.long_control_state), 1 if speed > threshold else 2)
      for gap, expected in ((20_000_000, 1), (20_000_001, 2)):
        long = LongControl(cp)
        cs = state(0.)
        cs.cruiseState.standstill = True
        long.update(True, cs, 0., True, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(1, 1_000_000_000, False)))
        for tick in range(1, 34):
          long.update(True, cs, .2, False, (-4., 2.),
            context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(1, 1_000_000_000 + tick * 10_000_000, False)))
        long.update(True, cs, .2, False, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(1, 1_330_000_000 + gap, False)))
        self.assertEqual(int(long.long_control_state), expected)

  def test_missing_evidence_overrides_and_continuity_reset(self):
    for cp, _, sascm, alpha in profiles():
      if sascm and not alpha:
        continue
      for interruption in ('missing', 'drive', 'gap', 'brake', 'gas'):
        long = LongControl(cp)
        cs = state(0.)
        cs.cruiseState.standstill = True
        now = 1_000_000_000
        long.update(True, cs, 0., True, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(1, now, False)))
        for tick in range(1, 35):
          long.update(True, cs, .2, False, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(1, now + tick * 10_000_000, False)))
        now += 350_000_000
        drive = 2 if interruption == 'drive' else 1
        if interruption == 'gap':
          now += 10_000_001
        cs.brakePressed, cs.gasPressed = interruption == 'brake', interruption == 'gas'
        evidence = None if interruption == 'missing' else VoltStopEvidence(drive, now, False)
        long.update(True, cs, .2, False, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=evidence))
        self.assertEqual(int(long.long_control_state), 2)
        cs.brakePressed = cs.gasPressed = False
        for tick in range(1, 35):
          long.update(True, cs, .2, False, (-4., 2.),
            context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(drive, now + tick * 10_000_000, False)))
          if tick < 34 or interruption in ('missing', 'brake', 'gas'):
            self.assertEqual(int(long.long_control_state), 2)
        long.update(False, cs, .5, False, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=VoltStopEvidence(drive, now + 350_000_000, False)))
        self.assertEqual((int(long.long_control_state), long.last_output_accel), (0, 0.))

  def test_controls_transport_admission_does_not_guess_lead(self):
    for defect in ('stale_radar', 'stale_plan', 'invalid_car', 'previous_drive', 'lead_disagreement'):
      controls, now, offset = controls_fixture()
      with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)):
        self.assertFalse(controls.longitudinal_inputs._gm_volt_stop_from_observation(controls.longitudinal_inputs._gm_volt_observation()).has_lead)
        if defect == 'stale_radar':
          controls.sm.logMonoTime['radarState'] = now - 200_000_000
        elif defect == 'stale_plan':
          controls.sm.logMonoTime['longitudinalPlan'] = now - 200_000_000
        elif defect == 'invalid_car':
          controls.sm['carState'].canValid = False
        elif defect == 'previous_drive':
          controls.sm['deviceState'].startedMonoTime = now - 1_000_000
        else:
          controls.sm['longitudinalPlan'].hasLead = True
          # Existing PID no-lead transport contract is unchanged by stop agreement.
          self.assertFalse(observed_lead(controls))
        self.assertIsNone(controls.longitudinal_inputs._gm_volt_stop_from_observation(controls.longitudinal_inputs._gm_volt_observation()))

  def test_primary_plan_agreement_does_not_reject_secondary_lead(self):
    for primary in (False, True):
      for secondary in (False, True):
        for plan_lead in (False, True):
          controls, now, offset = controls_fixture()
          controls.sm['radarState'].leadOne.present = primary
          controls.sm['radarState'].leadTwo.present = secondary
          controls.sm['longitudinalPlan'].hasLead = plan_lead
          with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)):
            observation = controls.longitudinal_inputs._gm_volt_observation()
            self.assertEqual(observation[0], primary or secondary)
            evidence = controls.longitudinal_inputs._gm_volt_stop_from_observation(observation)
          if plan_lead == primary:
            self.assertIs(evidence.has_lead, primary)
          else:
            self.assertIsNone(evidence)

  def test_card_resume_provider_remains_fresh_at_actual_gas_sender_cadence(self):
    import time
    from openpilot.starpilot.controller_extensions import ResumePlanInputs
    def clock_pair():
      now = time.monotonic_ns()
      return now, now
    self.enterContext(OpenpilotPrefix())
    self.enterContext(patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', side_effect=clock_pair))
    provider = ResumePlanInputs()
    pm = messaging.PubMaster(['deviceState', 'carState', 'longitudinalPlan'])
    drive = time.monotonic_ns() - 1_000_000_000
    healthy = []
    started = time.monotonic()
    #100HzcarState/20Hzplan producers coalesce into the actual25Hzgas sender consumer.
    for tick in range(204):
      for name, cadence in (('carState', 1), ('longitudinalPlan', 5), ('deviceState', 50)):
        if tick % cadence == 0:
          event = messaging.new_message(name)
          event.valid = True
          if name == 'deviceState':
            event.deviceState.started = True
            event.deviceState.startedMonoTime = drive
          elif name == 'carState':
            event.carState.canValid = True
          else:
            event.longitudinalPlan.shouldStop = False
          pm.send(name, event)
      if tick % 4 == 0:
        current = provider.update(time.monotonic_ns())
        if tick >= 120:
          healthy.append(current)
      time.sleep(max(0., started + (tick + 1) * .01 - time.monotonic()))
    self.assertTrue(all(healthy))
    tracker = provider.sm.freq_tracker['carState'].recent_avg_dt
    self.assertEqual(tracker.count, tracker.window_size)
    self.assertTrue(provider.sm.freq_ok['carState'])
    event = messaging.new_message('longitudinalPlan')
    event.valid = True
    event.longitudinalPlan.shouldStop = True
    pm.send('longitudinalPlan', event)
    self.assertFalse(provider.update(time.monotonic_ns()))
    time.sleep(.16)
    self.assertFalse(provider.update(time.monotonic_ns()))

  def test_ascm_card_resume_uses_physical_clock_and_monotonic_plan_with_two_second_offset(self):
    from opendbc.car.gm.tests.test_ascm_intercept import params as ascm_params
    from openpilot.starpilot.controller_extensions import configure_controller
    from openpilot.starpilot.longitudinal.tests.test_gm_volt_long_policy import SubMasterFixture
    mono, boot = 20_000_000_000, 22_000_000_000
    cp = ascm_params(CAR.CHEVROLET_VOLT_ASCM, sascm=True, alpha=True, accelerator=True, radar=True)
    cp.safetyConfigs[0].safetyParam = 0xD114
    self.assertEqual(cp.alternativeExperience, 0)
    from openpilot.common.params import Params
    from openpilot.selfdrive.car.card import Car
    self.enterContext(OpenpilotPrefix())
    self.enterContext(patch.dict('os.environ', {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}))
    saved = Params()
    for key in ('OpenpilotEnabledToggle', 'AlwaysOnLateral', 'VoltSNG', 'GMAutoHold', 'VoltOnePedalMode'):
      saved.put_bool(key, True, block=True)
    saved.put_bool('SafeMode', False, block=True)
    ci = CarInterface(cp)
    card = Car(CI=ci, RI=CarInterface.RadarInterface(cp))
    self.assertEqual(card.CP.alternativeExperience, 32)
    self.assertEqual(card.CP.safetyConfigs[0].safetyParam, 0xD114)
    self.assertTrue(card.volt_startup_keepalive())
    provider = ci.CC.volt_sng_plan_input
    sm = SubMasterFixture(mono)
    self.enterContext(patch.object(sm, 'update', lambda timeout: None, create=True))
    self.enterContext(patch.object(provider, 'sm', sm))
    self.assertTrue(provider.boottime)
    provider.freshness.offset_ns = boot - mono
    provider.freshness.floor_ns = mono - 1_000_000_000
    with patch('openpilot.starpilot.controller_extensions.time.monotonic_ns', return_value=mono), \
         patch('openpilot.starpilot.controller_extensions.time.clock_gettime_ns', return_value=boot), \
         patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(mono, boot)):
      self.assertTrue(provider.update(boot))
      self.assertFalse(provider.update(mono))
      sm['longitudinalPlan'].shouldStop = True
      self.assertFalse(provider.update(boot))
      sm['longitudinalPlan'].shouldStop = False
      sm.logMonoTime['longitudinalPlan'] = mono - 150_000_001
      self.assertFalse(provider.update(boot))
      sm.logMonoTime['longitudinalPlan'] = boot - 20_000_000
      self.assertFalse(provider.update(boot))
      sm.logMonoTime['longitudinalPlan'] = mono - 20_000_000
      sm.valid['longitudinalPlan'] = False
      self.assertFalse(provider.update(boot))
      sm.valid['longitudinalPlan'] = True
      self.assertTrue(provider.update(boot))
    # Outside the exact ASCM independent-axis profile, retain the prior clock.
    cp.alternativeExperience = 0
    with patch('openpilot.starpilot.controller_extensions.messaging.SubMaster', return_value=sm):
      configure_controller(ci, None)
    self.assertFalse(ci.CC.volt_sng_plan_input.boottime)

  def test_frozen_card_opt_in_qualifies_plan_when_controls_snapshot_is_off(self):
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
    from openpilot.starpilot.controller_extensions import configure_controller
    controls, _, _, _, _, now, offset = fixture()
    cp = ordinary_params(CAR.CHEVROLET_VOLT, radar=True)
    ci = CarInterface(cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    rx = physical_frames(packer, speed=0., gear=4, counter=0)
    rx += [packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': 4})]
    ci.update([(now - 1_000_000, rx)])
    ci.update([(now, rx)])
    self.assertTrue(ci.CS.out.canValid)
    VehicleStartupPreferences(volt_sng=True).configure_controller(ci)
    with patch('openpilot.starpilot.controller_extensions.messaging.SubMaster', return_value=controls.sm):
      configure_controller(ci, None)
    ci.CC.volt_sng_plan_input.freshness.offset_ns = offset
    ci.CC.volt_sng_plan_input.freshness.floor_ns = now - 500_000_000
    controls.CP, controls.LoC = cp, LongControl(cp)
    controls.vehicle_startup_preferences = VehicleStartupPreferences(volt_sng=False)
    controls.sm.data['carState'] = ci.CS.out
    controls.sm['longitudinalPlan'].shouldStop = False
    lateral = messaging.new_message('controlsState').controlsState.lateralControlState.init('torqueState')
    for stale in (True, False):
      controls.sm.logMonoTime['longitudinalPlan'] = now - (200_000_000 if stale else 10_000_000)
      cc = messaging.new_message('carControl').carControl
      cc.enabled = cc.longActive = True
      cc.actuators.longControlState = "starting"
      cc.actuators.accel = .5
      controls.publish(cc, lateral)
      self.assertTrue(cc.cruiseControl.resume)  # Default/off publication is unchanged.
      ci.CC.frame = 4
      with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)), \
           patch('openpilot.starpilot.controller_extensions.time.monotonic_ns', return_value=now):
        _, tx = ci.apply(cc.as_reader(), now)
      gas = next(data for address, data, _ in tx if address == 0x2CB)
      self.assertEqual(bool(gas[0] & 1), stale)

  def test_saved_sng_on_non_volt_does_not_read_optional_resume_sources(self):
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
    controls, _, _, _, _, _, _ = fixture()
    controls.CP = ordinary_params(CAR.CHEVROLET_SUBURBAN, radar=True)
    controls.LoC = LongControl(controls.CP)
    self.assertIsNone(controls.LoC.extension.resume_policy)
    controls.vehicle_startup_preferences = VehicleStartupPreferences(volt_sng=True)
    controls.sm.data.pop('deviceState', None)
    controls.sm['carState'].cruiseState.standstill = True
    controls.sm['longitudinalPlan'].shouldStop = False
    cc = messaging.new_message('carControl').carControl
    cc.enabled = cc.longActive = True
    lateral = messaging.new_message('controlsState').controlsState.lateralControlState.init('torqueState')
    with patch.object(controls.longitudinal_inputs, 'resume_sources_current', side_effect=AssertionError('unsubscribed source')):
      controls.publish(cc, lateral)
    self.assertTrue(cc.cruiseControl.resume)

  def test_sng_resume_honors_stop_owner_and_fresh_plan_in_actual_publication(self):
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
    controls, _, _, _, _, now, offset = fixture()
    controls.CP = ordinary_params(CAR.CHEVROLET_VOLT, radar=True)
    controls.LoC = LongControl(controls.CP)
    controls.vehicle_startup_preferences = VehicleStartupPreferences(volt_sng=True)
    from openpilot.starpilot.longitudinal.inputs import ResumeFreshness
    controls.longitudinal_inputs.resume_freshness = ResumeFreshness()
    controls.longitudinal_inputs.resume_freshness.offset_ns = offset
    controls.longitudinal_inputs.resume_freshness.floor_ns = now - 500_000_000
    controls.sm['carState'].cruiseState.enabled = True
    controls.sm['carState'].cruiseState.standstill = True
    controls.sm['longitudinalPlan'].speeds = [2.] * 17
    cc = messaging.new_message('carControl').carControl
    cc.enabled = cc.longActive = True
    lateral = messaging.new_message('controlsState').controlsState.lateralControlState.init('torqueState')
    for should_stop, stale, requested, expected in ((True, False, True, False), (False, False, True, True),
                                                   (False, True, True, False), (False, True, False, True)):
      controls.vehicle_startup_preferences = VehicleStartupPreferences(volt_sng=requested)
      controls.sm['longitudinalPlan'].shouldStop = should_stop
      controls.sm.logMonoTime['longitudinalPlan'] = now - (200_000_000 if stale else 10_000_000)
      with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)):
        controls.publish(cc, lateral)
      self.assertTrue(cc.cruiseControl.cancel)  # Existing non-pcm stock cancel is not a driver cancel.
      self.assertEqual(cc.cruiseControl.resume, expected)
      self.assertEqual(controls.sm['longitudinalPlan'].shouldStop, should_stop)

  def test_parsed_controls_card_moving_stop_wire(self):
    for cp, accelerator, sascm, alpha in profiles():
      if sascm and not alpha:
        continue
      controls, card, _, _, sent, base, _ = fixture(alpha)
      ci = CarInterface(cp)
      controls.CP, controls.CI, controls.LoC, controls.VM = cp, ci, LongControl(cp), VehicleModel(cp)
      controls.longitudinal_inputs.gm_cc_enabled = controls.longitudinal_inputs.gm_start_enabled = controls.longitudinal_inputs.gm_euv_enabled = False
      controls.longitudinal_inputs.gm_volt_enabled = True
      controls.longitudinal_inputs.gm_volt_boot_offset_ns, controls.longitudinal_inputs.gm_volt_source_floor_ns = 0, base - 500_000_000
      log = messaging.new_message('controlsState').controlsState.lateralControlState.init(cp.lateralTuning.which() + 'State')
      controls.LaC = NS(reset=lambda: None, update=lambda *args, log=log: (0., 0., log))
      card.CP, card.CI, card.volt_cc_selected = cp, ci, False
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      wanted = 0.
      for tick in range(-2, 152):
        now = base + tick * 10_000_000
        speed = 6. if tick < 64 else 0.
        frames = physical_frames(packer, speed=speed, gear=4, counter=tick % 4)
        if not accelerator:
          frames.append(packer.make_can_msg('EBCMBrakePedalPosition', 0, {}))
        frames += [packer.make_can_msg(name, 2, {}) for name in ('ASCMLKASteeringCmd', 'AEBCmd', 'ASCMActiveCruiseControlStatus')]
        out = ci.update([(now - 2_000_000, frames)])
        if tick < 0:
          continue
        self.assertTrue(out.canValid)
        controls.sm.data['carState'] = out
        plan = controls.sm['longitudinalPlan']
        target = -2. if tick < 104 else .1 if tick < 149 else .5
        plan.aTarget, plan.shouldStop, plan.hasLead = target, tick < 104, False
        for name in controls.sm.logMonoTime:
          controls.sm.logMonoTime[name] = now - 2_000_000
          controls.sm.recv_time[name] = (now - 1_000_000) / 1e9
        prior = {0: 'off', 1: 'pid', 2: 'stopping', 3: 'starting'}[int(controls.LoC.long_control_state)]
        with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now)), \
             patch('openpilot.selfdrive.car.card.time.monotonic', return_value=now / 1e9), \
             patch('openpilot.selfdrive.car.card.REPLAY', False):
          self.assertFalse(controls.longitudinal_inputs._gm_volt_stop_from_observation(controls.longitudinal_inputs._gm_volt_observation()).has_lead)
          with patch.object(controls.longitudinal_inputs, '_gm_volt_observation', wraps=controls.longitudinal_inputs._gm_volt_observation) as observation:
            cc, lateral = controls.state_control()
          self.assertEqual(observation.call_count, 1)
          controls.publish(cc, lateral)
          controls.sm.data['carControl'] = cc
          for mapping in (controls.sm.seen, controls.sm.alive, controls.sm.valid):
            mapping['carControl'] = True
          controls.sm.logMonoTime['carControl'] = now - 2_000_000
          controls.sm.recv_time['carControl'] = (now - 1_000_000) / 1e9
          card.controls_update(out, cc.as_reader())
        if tick < 149:
          wanted = original_stop(wanted, out.vEgo, target, not sascm) if tick < 104 else min(wanted, cp.stopAccel)
          self.assertAlmostEqual(cc.actuators.accel, wanted, places=6)
          self.assertEqual(int(controls.LoC.long_control_state), 2)
        else:
          self.assertEqual(int(controls.LoC.long_control_state), 1)
        self.assertEqual(str(cc.actuators.longControlState), prior)
        if 0 < tick < 149 and tick % 4 == 0:
          counter = (tick // 4) % 4
          full_stop = bool(out.standstill)
          gas = bytes([1 | (counter << 6), 0x62 if full_stop else 0x42, 0xab, 0xe0, 0,
                       0x9d if full_stop else 0xbd, 0x54, 0x20 - counter])
          near_stop = abs(out.vEgo) < (.25 if sascm else .5)
          brake = round(-100 * cp.stopAccel) if near_stop else original_brake(float(cc.actuators.accel), out.vEgo, cp)
          bus = 0 if sascm or not accelerator else 2
          wire = [(msg[0], bytes(msg[1]), msg[2]) for msg in sent[-1][0] if msg[0] in (0x2cb, 0x315)]
          self.assertEqual(wire, [(0x2cb, gas, 0), (0x315, brake_wire(brake, counter, full_stop), bus)])
