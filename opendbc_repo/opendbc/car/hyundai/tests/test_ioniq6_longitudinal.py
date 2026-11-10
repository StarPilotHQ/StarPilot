import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.hyundaicanfd import CanBus, create_acc_control, create_ioniq6_radar_heartbeat
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.ioniq6_handoff import build_ioniq6_hda2_long_candidate
from opendbc.car.hyundai.ioniq6_longitudinal import Ioniq6LongitudinalPolicy, Ioniq6LongitudinalState, LongState, update_calibration
from opendbc.car.hyundai.values import CAR, DBC
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.starpilot.longitudinal.extension import LongitudinalContext
from openpilot.starpilot.longitudinal.ioniq6_start import StartEvidence

DATA = Path(__file__).parent / 'testdata'


def controller_fixture(alternate=False, *, aol=False):
  fingerprint = gen_empty_fingerprint()
  fingerprint[2].update({0x110: 32, 0x362: 32} if alternate else {0x50: 16, 0x2A4: 24})
  fingerprint[1].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                         0x1BA: 24, 0x1E5: 16, 0x36A: 16})
  fingerprint[0][0x3A5] = 24
  fingerprint[0][0x100] = 24
  stock = CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fingerprint, [], False, False, False)
  cp = build_ioniq6_hda2_long_candidate(stock, fingerprint)
  if aol:
    cp.safetyConfigs[-1].safetyParam |= 0x0800
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  bus = CanBus(cp)
  inputs = [packer.make_can_msg(name, bus.ECAN, values) for name, values in (
    ('ACCELERATOR', {'GEAR': 5}), ('TCS', {'ACCEnable': 0, 'ACC_REQ': 1}),
    ('WHEEL_SPEEDS', {}), ('MDPS', {}), ('CRUISE_BUTTONS', {'COUNTER': 1}),
    ('DOORS_SEATBELTS', {'DRIVER_SEATBELT': 1}), ('STEERING_SENSORS', {}))]
  inputs.append(packer.make_can_msg('CAM_0x362' if alternate else 'CAM_0x2a4', bus.CAM, {}))
  ci.update([(1_000_000_000, inputs)])
  return cp, ci.CS, CarController(DBC[cp.carFingerprint], cp)


class TestIoniq6Longitudinal(unittest.TestCase):
  def test_active_calibration_matches_continuous_corrected_frozen_reference(self):
    reference = json.loads((DATA / 'ioniq6_long_calibration.json').read_text())
    corrected = json.loads((DATA / 'ioniq6_long_calibration_soft_floor.json').read_text())
    self.assertEqual(corrected['originalTraceSha256'], hashlib.sha256((DATA / 'ioniq6_long_calibration.json').read_bytes()).hexdigest())
    # Independently derived from committed prechange calibration by adding
    # only the approved below-floor revocation. Keep every state transition
    # continuous across both historical soft-start windows.
    changed_rows = [index for index, (old, new) in enumerate(zip(reference['outputs'], corrected['outputs'], strict=True)) if old != new]
    self.assertEqual(changed_rows, list(range(12)) + list(range(76, 90)))
    state = Ioniq6LongitudinalState()
    for row, expected in zip(reference['inputs'], corrected['outputs'], strict=True):
      accel, speed, measured_accel, mode = row
      update_calibration(state, accel, speed, measured_accel, getattr(LongState, mode))
      actual = (state.desired_accel, state.actual_accel, state.accel_last, state.jerk_upper,
                state.jerk_lower, state.launch_active, state.stopping, int(state.long_control_state_last))
      for value, previous in zip(actual, expected, strict=True):
        self.assertAlmostEqual(value, previous, places=12)

  def test_saturated_stopping_brake_survives_low_speed_comfort_cap(self):
    for speed in (.1, .5, 1., 2.):
      with self.subTest(speed=speed):
        policy = Ioniq6LongitudinalPolicy()
        last_sent = 0.
        for frame in range(600):
          result = policy.update(frame, active=True, override=False, accel=-3.5, speed=speed,
                                  measured_accel=last_sent, control_state=LongState.stopping,
                                  last_sent_accel=last_sent)
          self.assertTrue(result.stopping)
          self.assertGreaterEqual(result.accel, -3.5)
          if frame % 2 == 0:
            self.assertLessEqual(result.accel, last_sent)
            self.assertGreaterEqual(result.accel + 1e-12, last_sent - .36)
            last_sent = result.accel
        self.assertAlmostEqual(last_sent, -3.5)
        refused = policy.update(601, active=True, override=True, accel=-3.5, speed=speed,
                                 measured_accel=last_sent, control_state=LongState.stopping,
                                 last_sent_accel=last_sent)
        self.assertEqual(refused.accel, 0.)
        self.assertFalse(refused.stopping)
        self.assertEqual(policy.state, Ioniq6LongitudinalState())

  def test_saturated_request_returns_to_soft_hold_at_standstill(self):
    state = Ioniq6LongitudinalState(accel_last=-3.5)
    update_calibration(state, -3.5, 0., 0., LongState.stopping)
    self.assertAlmostEqual(state.desired_accel, -.15)
    self.assertTrue(state.stopping)
    self.assertGreater(state.actual_accel, -3.5)

  def test_soft_target_revokes_launch_on_offcycle_and_scheduled_ticks(self):
    for drop_frame in (31, 35):
      with self.subTest(drop_frame=drop_frame):
        policy = Ioniq6LongitudinalPolicy()
        last_sent = 0.0
        for frame in range(drop_frame):
          request = policy.update(frame, active=True, override=False, accel=1.2, speed=0.0,
                                  measured_accel=0.0, control_state=LongState.starting, last_sent_accel=last_sent)
          if frame % 2 == 0:
            last_sent = request.accel
        self.assertTrue(policy.state.launch_active)
        soft = policy.update(drop_frame, active=True, override=False, accel=.2, speed=0.0,
                             measured_accel=0.0, control_state=LongState.starting, last_sent_accel=last_sent)
        self.assertFalse(policy.state.launch_active)
        self.assertLess(soft.accel, last_sent)
        self.assertGreater(soft.accel, .2)  # Residual demand is rate-limited, not abruptly clipped.
        self.assertLessEqual(soft.accel, last_sent + 1e-12)
        negative = policy.update(drop_frame + 1, active=True, override=False, accel=-1.0, speed=0.0,
                                 measured_accel=0.0, control_state=LongState.starting, last_sent_accel=soft.accel)
        self.assertFalse(policy.state.launch_active)
        self.assertLess(negative.accel, soft.accel)
        self.assertGreater(negative.accel, -1.0)
        policy.update(drop_frame + 2, active=True, override=False, accel=1.2, speed=0.0,
                      measured_accel=0.0, control_state=LongState.starting, last_sent_accel=negative.accel)
        if (drop_frame + 2) % 5 != 0:
          self.assertFalse(policy.state.launch_active)
        for frame in range(drop_frame + 3, drop_frame + 8):
          policy.update(frame, active=True, override=False, accel=1.2, speed=0.0,
                        measured_accel=0.0, control_state=LongState.starting, last_sent_accel=negative.accel)
        self.assertTrue(policy.state.launch_active)
        policy.update(drop_frame + 8, active=True, override=True, accel=1.2, speed=0.0,
                      measured_accel=0.0, control_state=LongState.starting, last_sent_accel=0.0)
        self.assertEqual(policy.state, Ioniq6LongitudinalState())

  def test_disengage_override_and_off_state_discard_old_demand(self):
    for inactive in ({'active': False}, {'override': True}, {'control_state': LongState.off}):
      with self.subTest(inactive=inactive):
        policy = Ioniq6LongitudinalPolicy()
        inputs = dict(active=True, override=False, accel=1.8, speed=0.0, measured_accel=0.0,
                      control_state=LongState.starting, last_sent_accel=0.0)
        for frame in range(50):
          inputs['last_sent_accel'] = policy.update(frame, **inputs).accel
        result = policy.update(51, **(inputs | inactive))
        self.assertEqual(result.accel, 0.0)
        self.assertFalse(result.stopping)
        self.assertEqual(policy.state, Ioniq6LongitudinalState())
        resumed = policy.update(52, **(inputs | {'accel': .1, 'last_sent_accel': 0.0}))
        fresh = Ioniq6LongitudinalPolicy().update(52, **(inputs | {'accel': .1, 'last_sent_accel': 0.0}))
        self.assertEqual(resumed, fresh)

  def test_braking_rate_uses_last_transmitted_frame_and_stays_bounded(self):
    policy = Ioniq6LongitudinalPolicy()
    inputs = dict(active=True, override=False, speed=20., measured_accel=.2, control_state=LongState.pid)
    last_sent = 0.0
    for frame in range(300):
      result = policy.update(frame, **inputs, accel=2.0 if frame < 50 else -3.5, last_sent_accel=last_sent)
      self.assertGreaterEqual(result.accel, -3.5)
      self.assertLessEqual(result.accel, 2.0)
      if frame % 2 == 0:
        if frame >= 50:
          self.assertGreaterEqual(result.accel + 1e-12, last_sent - .36)
        last_sent = result.accel
    self.assertAlmostEqual(last_sent, -3.5)

  def test_real_controller_scc_respects_all_four_axis_states(self):
    for alternate in (False, True):
      for lateral, longitudinal in ((False, False), (True, False), (False, True), (True, True)):
        for override in (False, True):
          with self.subTest(alternate=alternate, lat=lateral, long=longitudinal, override=override):
            cp, cs, controller = controller_fixture(alternate)
            control = structs.CarControl()
            control.enabled = lateral or longitudinal
            control.latActive, control.longActive = lateral, longitudinal
            control.cruiseControl.override = override
            cs.out.gasPressed = lateral and not longitudinal and override
            control.actuators.accel = 1.0
            control.actuators.torque = .02
            control.actuators.longControlState = LongState.starting
            output, frames = controller.update(control.as_reader(), cs, 1_000_000_000)
            scc = next(frame for frame in frames if frame[0] == 0x1A0)
            parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [('SCC_CONTROL', 0)], 1)
            parser.update((1_000_000_000, [scc]))
            values = parser.vl['SCC_CONTROL']
            self.assertEqual(values['ACCMode'], 2 if control.enabled and override else 1 if control.enabled else 0)
            if not longitudinal or override:
              self.assertEqual((values['aReqRaw'], values['aReqValue'], output.accel), (0., 0., 0.))
              self.assertEqual(values['StopReq'], 0)
            else:
              self.assertGreater(values['aReqValue'], 0.)
              self.assertAlmostEqual(values['aReqValue'], output.accel, delta=.0051)
              self.assertAlmostEqual(values['aReqRaw'], output.accel, delta=.0051)
            if not lateral:
              self.assertEqual(output.torque, 0.)

  def test_pedal_override_keeps_scc_engaged_without_acceleration(self):
    cp, cs, controller = controller_fixture()
    control = structs.CarControl()
    control.enabled = True
    control.latActive = True
    control.actuators.accel = 1.0
    control.actuators.longControlState = LongState.pid
    parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [('SCC_CONTROL', 0)], 1)

    for enabled, long_active, override, gas_pressed, brake_pressed, expected_mode in (
      (True, True, False, False, False, 1),
      (True, False, True, True, False, 2),
      (True, False, True, False, False, 2),
      (True, False, True, True, True, 2),
      (True, True, False, False, False, 1),
      (False, False, False, False, False, 0),
    ):
      control.enabled = enabled
      control.longActive = long_active
      control.cruiseControl.override = override
      cs.out.gasPressed = gas_pressed
      cs.out.brakePressed = brake_pressed
      _, frames = controller.update(control.as_reader(), cs, 1_000_000_000)
      scc = next(frame for frame in frames if frame[0] == 0x1A0)
      parser.update((1_000_000_000, [scc]))
      values = parser.vl['SCC_CONTROL']
      self.assertEqual(values['ACCMode'], expected_mode)
      if not long_active:
        self.assertEqual((values['aReqRaw'], values['aReqValue'], values['StopReq']), (0., 0., 0))
      controller.frame += 1

  def test_real_controller_scc_withdraws_launch_floor_after_soft_target(self):
    for alternate in (False, True):
      for drop_frame in (31, 34):  # Between SCC sends and on an SCC send.
        with self.subTest(alternate=alternate, drop_frame=drop_frame):
          cp, cs, controller = controller_fixture(alternate)
          parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [('SCC_CONTROL', 0)], 1)
          control = structs.CarControl()
          control.enabled = True
          control.longActive = True
          control.actuators.longControlState = LongState.starting
          last_scc_accel = 0.0

          def tick(frame: int, target: float, *, override: bool = False,
                   control=control, controller=controller, cs=cs, parser=parser):
            control.actuators.accel = target
            control.cruiseControl.override = override
            output, frames = controller.update(control.as_reader(), cs, 1_000_000_000 + frame * 10_000_000)
            scc = [message for message in frames if message[0] == 0x1A0]
            self.assertEqual(len(scc), int(frame % 2 == 0))
            if scc:
              parser.update((1_000_000_000 + frame * 10_000_000, scc))
              self.assertAlmostEqual(parser.vl['SCC_CONTROL']['aReqValue'], output.accel, delta=.0051)
            return output.accel, parser.vl['SCC_CONTROL']['aReqValue'] if scc else None

          for frame in range(drop_frame):
            _, sent = tick(frame, 1.2)
            if sent is not None:
              last_scc_accel = sent
          self.assertTrue(controller.ioniq6_longitudinal.state.launch_active)
          soft_output, soft_scc = tick(drop_frame, .2)
          self.assertFalse(controller.ioniq6_longitudinal.state.launch_active)
          if soft_scc is not None:
            self.assertLess(soft_scc, last_scc_accel)
            self.assertGreater(soft_scc, .2)
          else:
            self.assertLess(soft_output, last_scc_accel)
            _, next_scc = tick(drop_frame + 1, .2)
            self.assertIsNotNone(next_scc)
            self.assertLess(next_scc, last_scc_accel)
            self.assertGreater(next_scc, .2)

          next_frame = drop_frame + (2 if drop_frame % 2 == 0 else 3)
          for frame in range(drop_frame + (2 if drop_frame % 2 else 1), next_frame):
            tick(frame, .2)
          negative, sent = tick(next_frame, -1.0)
          self.assertFalse(controller.ioniq6_longitudinal.state.launch_active)
          self.assertIsNotNone(sent)
          self.assertLess(negative, soft_output)
          self.assertGreater(negative, -1.0)
          for frame in range(next_frame + 1, next_frame + 7):
            tick(frame, 1.2)
          self.assertTrue(controller.ioniq6_longitudinal.state.launch_active)
          reset, sent = tick(next_frame + 7, 1.2, override=True)
          self.assertEqual(reset, 0.0)
          self.assertEqual(controller.ioniq6_longitudinal.state, Ioniq6LongitudinalState())

  def test_real_host_state_then_controller_scc_on_same_tick(self):
    for alternate in (False, True):
      with self.subTest(alternate=alternate):
        cp, cs, controller = controller_fixture(alternate)
        host = LongControl(cp)
        self.assertIsNotNone(host.extension)
        self.assertIsNotNone(host.extension.ioniq6_start)
        parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [('SCC_CONTROL', 0)], 1)
        cs.out.canValid = True
        cs.out.canTimeout = False
        cs.out.vEgo = 0.0
        cs.out.aEgo = 0.0
        control = structs.CarControl()
        control.enabled = True
        control.longActive = True

        def advance(frame: int, target: float, *, override: bool = False,
                    host=host, cs=cs, control=control, controller=controller, parser=parser):
          control.cruiseControl.override = override
          evidence = StartEvidence(True, True, True, 1, 1_000_000_000 + frame * 10_000_000)
          output = host.update(not override, cs.out, target, False, (-3.5, 2.0), context=LongitudinalContext(start_evidence=evidence))
          # Match Controls' tagged-Ioniq order: publish state after LoC.update.
          control.actuators.longControlState = host.long_control_state
          control.actuators.accel = float(output)
          sent, frames = controller.update(control.as_reader(), cs, 1_000_000_000 + frame * 10_000_000)
          scc = [message for message in frames if message[0] == 0x1A0]
          if scc:
            parser.update((1_000_000_000 + frame * 10_000_000, scc))
          return sent.accel, parser.vl['SCC_CONTROL']['aReqValue'] if scc else None

        for frame in range(34):
          advance(frame, .8)
          self.assertEqual(host.long_control_state, LongState.pid)
        output, scc = advance(34, .8)
        self.assertEqual(host.long_control_state, LongState.starting)
        self.assertIsNotNone(scc)
        self.assertAlmostEqual(output, scc, delta=.0051)
        advance(35, .8)  # Controller calibration runs every fifth tick.
        self.assertTrue(controller.ioniq6_longitudinal.state.launch_active)

        soft_output, soft_scc = advance(36, .2)
        self.assertEqual(host.long_control_state, LongState.pid)
        self.assertFalse(controller.ioniq6_longitudinal.state.launch_active)
        self.assertIsNotNone(soft_scc)
        self.assertLess(soft_scc, scc)
        self.assertAlmostEqual(soft_output, soft_scc, delta=.0051)
        negative, _ = advance(37, -.3)
        self.assertFalse(controller.ioniq6_longitudinal.state.launch_active)
        self.assertLess(negative, soft_output)

  def test_default_scc_packing_matches_committed_parent_bytes(self):
    reference = json.loads((DATA / 'scc_parent_frames.json').read_text())
    for row in reference['cases']:
      packer = CANPacker(DBC[CAR.HYUNDAI_IONIQ_6][Bus.pt])
      frame = create_acc_control(packer, SimpleNamespace(ECAN=1), *row['args'], SimpleNamespace(leadDistanceBars=2))
      self.assertEqual(frame[1].hex(), row['data'])

  def test_radar_heartbeat_matches_frozen_wire_and_runs_while_axes_are_off(self):
    reference = json.loads((DATA / 'ioniq6_heartbeat.json').read_text())
    for row in reference['cases']:
      frame = create_ioniq6_radar_heartbeat(row['counter'], row['brake'], row['gas'])
      self.assertEqual((frame.address, frame.src, len(frame.dat)), (0x100, 0, 24))
      self.assertEqual(frame.dat.hex(), row['payload'])
    for alternate in (False, True):
      _, cs, controller = controller_fixture(alternate)
      control = structs.CarControl()
      counters = []
      for tick in range(1040):
        cs.out.brakePressed = tick % 8 < 4
        cs.out.gasPressed = tick % 12 < 4
        _, frames = controller.update(control.as_reader(), cs, 1_000_000_000 + tick * 10_000_000)
        heartbeats = [frame for frame in frames if frame[0] == 0x100]
        self.assertEqual(len(heartbeats), int(tick % 4 == 0))
        if heartbeats:
          frame = heartbeats[0]
          self.assertEqual(frame[2], 0)
          self.assertEqual(frame[1][4], int(cs.out.brakePressed))
          self.assertEqual(frame[1][22], int(cs.out.gasPressed))
          counters.append(frame[1][2])
      self.assertEqual(counters, [counter & 0xff for counter in range(260)])

  def test_already_gentler_request_uses_bounded_stop_release(self):
    state = Ioniq6LongitudinalState(accel_last=-1.5)
    update_calibration(state, -.5, 3., -1.5, LongState.stopping)
    self.assertGreater(state.actual_accel, -1.47)
    self.assertLessEqual(state.actual_accel, -.5)
    self.assertLessEqual(state.jerk_upper, 1.32)
    self.assertTrue(state.stopping)

  def test_stop_release_does_not_weaken_independent_braking_above_soft_cap(self):
    state = Ioniq6LongitudinalState(accel_last=-3.2)
    update_calibration(state, -3.2, 2.5, -3.2, LongState.stopping)
    self.assertEqual(state.desired_accel, -3.2)
    self.assertEqual(state.actual_accel, -3.2)

  def test_stock_ioniq_and_other_cars_do_not_install_this_policy(self):
    for car in (CAR.HYUNDAI_IONIQ_6, CAR.HYUNDAI_IONIQ_5, CAR.KIA_EV6):
      cp = CarInterface.get_non_essential_params(car)
      self.assertIsNone(CarController(DBC[car], cp).ioniq6_longitudinal)
