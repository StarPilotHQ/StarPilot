"""Original ordinary factory-ACC Bolt default law through actual Controls and CAN."""

from unittest.mock import Mock
from openpilot.cereal.messaging import PubMaster
from openpilot.starpilot.lateral.controller_selection import default_selection
from openpilot.starpilot.longitudinal.extension import LongitudinalContext
from openpilot.starpilot.longitudinal.tests.extension_helpers import extension_state
import os
from types import SimpleNamespace as NS
import struct
import unittest
from unittest.mock import patch

from opendbc.car import structs
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.longitudinal import EuvLongitudinalEvidence, GMEuvStopPolicy, euv_policy_for
from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.tests.test_bolt_euv_control import original_demand, original_frames
from opendbc.car.gm.values import CAR, DBC
from opendbc.car.gm.radar_interface import RadarInterface
from openpilot.selfdrive.controls.radard import RadarD
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.tests.test_adjacent_radar_transport import Sources
from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.starpilot.longitudinal.tests.test_gm_volt_long_policy import controls_fixture

# Independent literals from original6cae0ce longcontrol.py, camera-ACC defaults;
# original source SHA384af713ea35696ec021248e3bb93ab8dcd65160c00e2027e08994a8ed53b29e.
def interp(value, xs, ys):
  if value <= xs[0]:
    return ys[0]
  if value >= xs[-1]:
    return ys[-1]
  for low, high, a, b in zip(xs[:-1], xs[1:], ys[:-1], ys[1:], strict=True):
    if low <= value <= high:
      return a + (b - a) * (value - low) / (high - low)
  raise AssertionError(value)


class OriginalDefault:
  """6cae0ce ordinary camera default CP: no startingState, no optional modes."""
  def __init__(self):
    self.state = 'off'
    self.counter = 0
    self.i = self.last = 0.0

  def update(self, active, speed, measured, target, stop, brake, cruise_still, lead):
    # Original _stop_release_ready and long_control_state_trans, CP.startingState=False.
    if self.state != 'stopping':
      self.counter = 0
      ready = True
    elif stop or brake:
      self.counter = 0
      ready = False
    elif speed > .25 or (lead and target > .15) or (target >= .45 and not cruise_still):
      self.counter = 35
      ready = True
    else:
      self.counter = min(self.counter + 1, 35) if target > .15 else 0
      ready = self.counter >= 35
    if not active:
      self.state = 'off'
    elif self.state == 'off':
      self.state = 'pid' if not stop and not brake and not cruise_still else 'stopping'
    elif self.state == 'stopping':
      if not stop and not brake and ready:
        self.state = 'pid'
    elif stop:
      self.state = 'stopping'

    if self.state == 'off':
      self.i = self.last = 0.0
      self.counter = 0
      return self.last
    if self.state == 'stopping':
      output = self.last
      if output > -.25:
        output = min(output, 0.0) - .01
      # Original moving-stop follow: ordinary Bolt has no vehicle-specific stop shaping.
      if stop and not brake and speed > 1.5 and target < output - .25:
        step = interp(speed, [1.5, 3., 6., 10.], [.02, .03, .05, .07])
        output = max(target, output - step)
      self.i = 0.0
    else:
      error = target - measured
      if self.i > 0 and target < -.05 and error < -.25 and not (speed <= .35 and target > -.40):
        self.i *= interp(abs(error), [.25, .75, 1.5], [.55, .25, 0.])
      candidate = self.i + .5 * .01 * error
      test = candidate + target  # kp=0, feedforward=1, no default optional freeze.
      upper = self.i if test > 2. else 2.
      lower = self.i if test < -4. else -4.
      self.i = min(max(candidate, lower), upper)
      output = min(max(self.i + target, -4.), 2.)
      if output > 0 and target < -.10 and error < -.35 and not (speed <= .35 and target > -.40):
        output = min(output, interp(target, [-1.5, -.6, -.1], [0., 0., .05]))
    self.last = min(max(output, -4.), 2.)
    return self.last


# Each input is identical for original and current; measured speed is supplied, not simulated.
# name, frames, speed, measured accel, target, shouldStop, cruiseStandstill, hasLead,
# enabled, active request, driver brake, driver gas.
SCENARIOS = {
 'weak_release': [('hold', 80, .1, 0., 0., True, False, False, True, True, False, False),
                  ('weak_release', 40, .1, 0., .1, False, False, False, True, True, False, False)],
 'latched_standstill': [('hold', 80, 0., 0., 0., True, True, False, True, True, False, False),
                  ('sustained_release', 45, 0., 0., .5, False, True, False, True, True, False, False)],
 'lead_release': [('hold', 80, 0., 0., 0., True, True, True, True, True, False, False),
                  ('lead_release', 12, 0., 0., .2, False, True, True, True, True, False, False)],
 'moving_stop': [('cruise', 12, 5., 0., 0., False, False, True, True, True, False, False),
                  ('moving_stop', 45, 5., 0., -1., True, False, True, True, True, False, False),
                  ('near_stop', 30, .1, 0., -.25, True, False, True, True, True, False, False)],
 'positive_integrator': [('accelerating', 200, 12., 0., .5, False, False, True, True, True, False, False),
                  ('target_decel', 20, 12., .3, -.2, False, False, True, True, True, False, False)],
 'driver_override': [('hold', 80, .1, 0., 0., True, False, False, True, True, False, False),
                  ('gas_override', 8, .1, 0., .5, False, False, False, True, False, False, True),
                  ('gas_release', 8, .3, 0., .5, False, False, False, True, True, False, False),
                  ('brake_disengage', 8, .1, 0., -.5, True, False, True, False, False, True, False)],
}


def euv_controls(alpha=True):
  controls, now, offset = controls_fixture()
  controls.CP = params(CAR.CHEVROLET_BOLT_EUV, alpha=alpha)
  controls.longitudinal_inputs.CP = controls.CP
  controls.CI = CarInterface(controls.CP)
  controls.VM = VehicleModel(controls.CP)
  controls.LoC = LongControl(controls.CP)
  controls.longitudinal_inputs.gm_volt_enabled = False
  controls.longitudinal_inputs.gm_euv_enabled = euv_policy_for(controls.CP) is not None
  controls.longitudinal_inputs.gm_euv_boot_offset_ns = offset
  controls.longitudinal_inputs.gm_euv_source_floor_ns = now - 500_000_000
  return controls, now, offset


def controller_state(car, now):
  return NS(out=car.as_reader(), cam_lka_steering_cmd_counter=0,
    loopback_lka_steering_cmd_updated=False, loopback_lka_steering_cmd_ts_nanos=now,
    pt_lka_steering_cmd_counter=0, buttons_counter=0,
    pscm_status=dict.fromkeys(('HandsOffSWDetectionMode', 'HandsOffSWlDetectionStatus',
      'LKATorqueDeliveredStatus', 'LKADriverAppldTrq', 'LKATorqueDelivered',
      'LKATotalTorqueDelivered', 'RollingCounter', 'PSCMStatusChecksum'), 0))


class TestEuvLongitudinal(unittest.TestCase):
  def test_actual_constructor_subscribes_to_euv_evidence_services(self):
    class Registered(Exception):
      pass

    for alpha in (False, True):
      cp = params(CAR.CHEVROLET_BOLT_EUV, alpha=alpha)
      captured = {}

      def register(services, output=captured, **options):
        output['services'] = services
        output['options'] = options
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
      self.assertEqual(captured['services'].count('radarState'), int(alpha))
      for service in ('deviceState',) + (('radarState',) if alpha else ()):
        for ignored in ('ignore_alive', 'ignore_valid', 'ignore_avg_freq'):
          self.assertIn(service, captured['options'][ignored])
      self.assertIn('carState', captured['services'])
      self.assertIn('longitudinalPlan', captured['services'])

  def test_original_default_actual_controls_to_controller(self):
    for alpha in (False, True):
      for alignment in range(4):
        for scenario, phases in SCENARIOS.items():
          controls, base, offset = euv_controls(alpha)
          old = OriginalDefault()
          controller = CarController(DBC[controls.CP.carFingerprint], controls.CP)
          controller.frame = alignment
          current_clock = [base]
          with patch.dict(os.environ, {'REPLAY': '0'}), \
               patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', side_effect=lambda clock=current_clock, off=offset: (clock[0], clock[0] + off)):
            for phase, count, speed, measured, target, stop, cruise_still, lead, enabled, active, brake, gas in phases:
              for tick in range(count):
                frame = controller.frame
                now = base + frame * 10_000_000
                current_clock[0] = now
                for name in controls.sm.logMonoTime:
                  controls.sm.logMonoTime[name] = now - 20_000_000
                  controls.sm.recv_time[name] = (now - 10_000_000) / 1e9
                car = controls.sm['carState']
                car.vEgo, car.aEgo, car.standstill = speed, measured, speed == 0.
                car.brakePressed, car.gasPressed = brake, gas
                car.cruiseState.available, car.cruiseState.enabled = True, enabled and not alpha
                car.cruiseState.standstill = cruise_still
                car.gearShifter = structs.CarState.GearShifter.drive
                controls.sm['selfdriveState'].enabled = enabled
                controls.sm.data['onroadEvents'] = [] if active else [NS(overrideLongitudinal=True)]
                plan = controls.sm['longitudinalPlan']
                plan.aTarget, plan.shouldStop = target, stop
                plan.hasLead = controls.sm['radarState'].leadOne.present = lead
                controls.sm['radarState'].leadTwo.present = True
                published_old_state = old.state
                expected = old.update(alpha and active and enabled, float(car.vEgo), float(car.aEgo),
                                      float(plan.aTarget), stop, brake, cruise_still, lead)
                cc, _ = controls.state_control()
                self.assertAlmostEqual(cc.actuators.accel, expected, places=6, msg=(alpha, alignment, scenario, phase, tick))
                actual_state = {0:'off', 1:'pid', 2:'stopping', 3:'starting'}[int(controls.LoC.long_control_state)]
                self.assertEqual(actual_state, old.state)
                # Preserve existing prior-state actuator publication in both implementations.
                self.assertEqual(str(cc.actuators.longControlState), published_old_state)
                _, frames = controller.update(cc.as_reader(), controller_state(car, now), now)
                actual = [tuple(msg) for msg in frames if msg[0] in (0x2cb, 0x315, 0x2cd)]
                wanted = []
                if alpha and not frame % 4:
                  wire_accel = struct.unpack('f', struct.pack('f', expected))[0]
                  raw, friction = original_demand(controls.CP, bool(cc.longActive), float(car.vEgo), wire_accel,
                                                 published_old_state, False, 0.)
                  wanted = original_frames(raw, friction, (frame // 4) % 4, enabled,
                                           bool(cc.longActive) and speed == 0. and published_old_state == 'stopping')
                self.assertEqual(actual, wanted, (alpha, alignment, scenario, phase, tick))

  def test_primary_lead_agreement_controls_stop_release(self):
    for primary, secondary, planned in ((False, True, False), (True, False, True),
                                        (False, True, True), (True, False, False)):
      controls, now, offset = euv_controls()
      car, plan, radar = (controls.sm[name] for name in ('carState', 'longitudinalPlan', 'radarState'))
      car.vEgo, car.standstill, car.cruiseState.standstill = 0., True, True
      plan.aTarget, plan.shouldStop, plan.hasLead = .2, False, planned
      radar.leadOne.present, radar.leadTwo.present = primary, secondary
      controls.LoC.long_control_state = structs.CarControl.Actuators.LongControlState.stopping
      controls.LoC.last_output_accel = -.25
      with patch.dict(os.environ, {'REPLAY': '0'}), \
           patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)):
        evidence = controls.longitudinal_inputs._gm_euv_evidence()
        self.assertEqual(evidence is not None, primary == planned)
        if evidence is not None:
          self.assertEqual(evidence.has_lead, primary)
        controls.state_control()
        self.assertEqual(int(controls.LoC.long_control_state), 1 if primary and planned else 2)

  def test_actual_transport_requires_fresh_current_drive_car_plan_and_radar(self):
    for source in ('radarState', 'carState', 'longitudinalPlan', 'deviceState'):
      for failure in ('valid', 'age', 'future'):
        controls, now, offset = euv_controls()
        controls.LoC.long_control_state = structs.CarControl.Actuators.LongControlState.stopping
        controls.LoC.last_output_accel = -.25
        controls.sm['carState'].vEgo = 0.
        controls.sm['carState'].standstill = True
        controls.sm['carState'].cruiseState.standstill = True
        controls.sm['longitudinalPlan'].aTarget = .2
        controls.sm['longitudinalPlan'].hasLead = controls.sm['radarState'].leadOne.present = True
        with patch.dict(os.environ, {'REPLAY': '0'}), \
             patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)):
          assert controls.longitudinal_inputs._gm_euv_evidence() is not None
          if failure == 'valid':
            controls.sm.valid[source] = False
          elif failure == 'age':
            controls.sm.logMonoTime[source] = now - 2_100_000_000
          else:
            controls.sm.logMonoTime[source] = now + 1
          self.assertIsNone(controls.longitudinal_inputs._gm_euv_evidence(), (source, failure))
          cc, _ = controls.state_control()
          self.assertEqual(controls.LoC.long_control_state, structs.CarControl.Actuators.LongControlState.stopping)
          self.assertEqual(cc.actuators.accel, -.25)

  def test_transport_drive_change_rejects_cached_sources(self):
    controls, now, offset = euv_controls()
    with patch.dict(os.environ, {'REPLAY': '0'}), \
         patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)):
      previous = controls.longitudinal_inputs._gm_euv_evidence()
      assert previous is not None
      new_drive = now - 15_000_000
      controls.sm['deviceState'].startedMonoTime = new_drive
      controls.sm.logMonoTime['deviceState'] = now - 10_000_000
      self.assertIsNone(controls.longitudinal_inputs._gm_euv_evidence())
      for name in ('carState', 'longitudinalPlan', 'radarState', 'deviceState'):
        controls.sm.logMonoTime[name] = now - 5_000_000
        controls.sm.recv_time[name] = (now - 1_000_000) / 1e9
      fresh = controls.longitudinal_inputs._gm_euv_evidence()
      assert fresh is not None
      self.assertEqual(fresh.drive_id, new_drive)
      self.assertNotEqual(fresh.drive_id, previous.drive_id)

  def test_stale_gap_drive_change_and_driver_override_reset_release(self):
    cp = params(CAR.CHEVROLET_BOLT_EUV, alpha=True)
    owner = LongControl(cp)
    car = structs.CarState(vEgo=0., standstill=True, canValid=True)
    car.cruiseState.standstill = True
    owner.long_control_state = structs.CarControl.Actuators.LongControlState.stopping
    owner.last_output_accel = -.25
    drive = 1_000_000_000
    now = 2_000_000_000
    for tick in range(34):
      owner.update(True, car.as_reader(), .2, False, (-4., 2.),
                   context=LongitudinalContext(vehicle_stop_evidence=EuvLongitudinalEvidence(drive, now + tick * 10_000_000, False)))
      self.assertEqual(owner.long_control_state, structs.CarControl.Actuators.LongControlState.stopping)
    owner.update(True, car.as_reader(), .2, False, (-4., 2.), context=LongitudinalContext(vehicle_stop_evidence=None))
    self.assertIsNone(extension_state(owner, 'vehicle_stop').release_since_ns)
    for tick in range(34):
      owner.update(True, car.as_reader(), .2, False, (-4., 2.),
                   context=LongitudinalContext(vehicle_stop_evidence=EuvLongitudinalEvidence(drive + 500_000_000, now + (35 + tick) * 10_000_000, False)))
      self.assertEqual(owner.long_control_state, structs.CarControl.Actuators.LongControlState.stopping)
    owner.update(True, car.as_reader(), .2, False, (-4., 2.),
                 context=LongitudinalContext(vehicle_stop_evidence=EuvLongitudinalEvidence(drive + 500_000_000, now + 690_000_000, False)))
    self.assertEqual(owner.long_control_state, structs.CarControl.Actuators.LongControlState.pid)
    owner.update(False, car.as_reader(), .2, False, (-4., 2.))
    self.assertEqual(owner.long_control_state, structs.CarControl.Actuators.LongControlState.off)
    self.assertIsNone(extension_state(owner, 'vehicle_stop').drive_id)
    self.assertEqual(owner.pid.i, 0.)

  def test_original_release_boundaries_continuity_and_output_limits(self):
    states = structs.CarControl.Actuators.LongControlState
    for target, speed, still, wanted in ((.15, .25, True, states.stopping),
                                       (.45, .25, False, states.pid),
                                       (.449999, .25, False, states.stopping),
                                       (0., .25, False, states.stopping),
                                       (0., .250001, False, states.pid)):
      stop = GMEuvStopPolicy()
      car = NS(vEgo=speed, canValid=True, canTimeout=False, brakePressed=False, gasPressed=False,
               cruiseState=NS(standstill=still))
      self.assertEqual(stop.transition(states.pid, states.stopping, True, car, target, False,
                                      EuvLongitudinalEvidence(1_000_000_000, 2_000_000_000, False)), wanted)
    for gap, wanted in ((20_000_000, states.pid), (20_000_001, states.stopping)):
      stop = GMEuvStopPolicy()
      car = NS(vEgo=0., canValid=True, canTimeout=False, brakePressed=False, gasPressed=False,
               cruiseState=NS(standstill=True))
      for tick in range(34):
        state = stop.transition(states.stopping, states.stopping, True, car, .2, False,
                                EuvLongitudinalEvidence(1_000_000_000, 2_000_000_000 + tick * 10_000_000, False))
        self.assertEqual(state, states.stopping)
      self.assertEqual(stop.transition(states.stopping, states.stopping, True, car, .2, False,
                                      EuvLongitudinalEvidence(1_000_000_000, 2_330_000_000 + gap, False)), wanted)
    for target, wanted in ((10., 2.), (-10., -4.)):
      owner = LongControl(params(CAR.CHEVROLET_BOLT_EUV, alpha=True))
      car = structs.CarState(vEgo=12., canValid=True)
      self.assertEqual(owner.update(True, car.as_reader(), target, False, (-4., 2.),
                                   context=LongitudinalContext(vehicle_stop_evidence=EuvLongitudinalEvidence(1_000_000_000, 2_000_000_000, False))), wanted)

  def test_ordinary_radarless_euv_produces_qualified_vision_lead_service(self):
    controls, now, offset = euv_controls()
    self.assertTrue(controls.CP.radarUnavailable)
    interface = RadarInterface(controls.CP)
    self.assertIsNone(interface.rcp)
    for _ in range(4):
      self.assertIsNone(interface.update([]))
    raw = interface.update([])
    assert raw is not None
    self.assertFalse(raw.errors.canError or raw.errors.radarFault or raw.errors.radarUnavailableTemporary)
    for present in (False, True):
      source = Sources(now - 20_000_000)
      lead = source['modelV2'].leadsV3[0]
      lead.prob = .95 if present else 0.
      lead.x, lead.y, lead.v, lead.a = [20.], [0.], [15.], [0.]
      radar = RadarD(controls.CP.radarDelay, radar_available=not controls.CP.radarUnavailable)
      radar.update(source, raw)
      published = {}
      radar.publish(Mock(spec=PubMaster, send=lambda name, event, output=published: output.__setitem__(name, event)))
      event = published['radarState']
      self.assertTrue(event.valid)
      self.assertEqual(event.radarState.leadOne.present, present)
      self.assertFalse(event.radarState.leadOne.radar)
      controls.sm['longitudinalPlan'].hasLead = event.radarState.leadOne.present
      controls.sm.data['radarState'] = event.radarState
      controls.sm.valid['radarState'] = event.valid
      with patch.dict(os.environ, {'REPLAY': '0'}), \
           patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)):
        evidence = controls.longitudinal_inputs._gm_euv_evidence()
        assert evidence is not None
        self.assertEqual(evidence.has_lead, present)
        controls.sm.logMonoTime['radarState'] = now - 200_000_000
        self.assertIsNone(controls.longitudinal_inputs._gm_euv_evidence())
        controls.sm.logMonoTime['radarState'] = now - 20_000_000

  def test_exact_owner_neighbor_and_release_isolation(self):
    for candidate in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023,
                      CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_ASCM):
      for alpha in (False, True):
        for release in (False, True):
          cp = params(candidate, alpha=alpha, release=release, sascm=candidate == CAR.CHEVROLET_VOLT_ASCM, radar=True)
          selected = candidate in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023) and alpha and not release
          owner = LongControl(cp)
          self.assertEqual(euv_policy_for(cp) is not None, selected)
          self.assertEqual(isinstance(extension_state(owner, 'vehicle_stop'), GMEuvStopPolicy), selected)
          if selected:
            for field, value in (('passive', True), ('pcmCruise', True), ('flags', 1), ('radarUnavailable', False)):
              bad = cp.as_reader().as_builder()
              setattr(bad, field, value)
              self.assertIsNone(euv_policy_for(bad))


if __name__ == '__main__':
  unittest.main()
