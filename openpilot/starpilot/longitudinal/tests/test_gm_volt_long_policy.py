"""Original default Volt acceleration law through the retained current CAN path.

Equation fixtures were transcribed from Dom 8d01d881cb4f, longcontrol.py and
longcontrol_vehicle_tunes.py. Wire fixtures describe the current torque-domain
DBC; baseline gateway Volt actuator mapping is compared with original raw commands.
"""

from unittest.mock import Mock, MagicMock
from openpilot.cereal.messaging import SubMaster
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.selfdrive.controls.lib.highway_correction_gain import HighwayCorrectionGain, read_gain
from openpilot.starpilot.lateral.controller_selection import turn_assist_supported
from openpilot.starpilot.lateral.lane_centering import LaneCenteringController

from openpilot.starpilot.longitudinal.extension import LongitudinalContext
from openpilot.starpilot.longitudinal.tests.extension_helpers import extension_state
from openpilot.starpilot.longitudinal.tests.extension_helpers import attach_inputs
import os
from tempfile import TemporaryDirectory
from weakref import finalize
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from opendbc.car import structs
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.longitudinal import GMVoltLongitudinalPolicy, volt_policy_for
from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
from opendbc.car.gm.tests.test_bolt_volt_configurations import controller_messages, ordinary_params
from opendbc.car.gm.values import CAR, GMFlags, GMSafetyFlags, PEDAL_BOLT_CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.cereal import messaging
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.common.params import Params
from openpilot.starpilot.lateral.lane_change_preferences import LiveLaneChangePolicy
from openpilot.starpilot.lateral.lane_change_smoothing import LaneChangeSmoother


def observed_lead(controls):
  observation = controls.longitudinal_inputs._gm_volt_observation()
  return observation[0] if observation is not None else None


def state(speed=12.0, accel=0.0):
  # Preserve literal threshold inputs here; actual Float32 transport is covered
  # by Controls and the CAN fixtures below.
  return NS(vEgo=speed, aEgo=accel, brakePressed=False, gasPressed=False,
            canValid=True, canTimeout=False, cruiseState=NS(standstill=False))


class SubMasterFixture:
  def __init__(self, now):
    self.frame = 0
    names = ('radarState', 'deviceState', 'carState', 'longitudinalPlan', 'vehicleParameters',
             'modelV2', 'selfdriveState', 'lateralManeuverPlan', 'lateralDelay')
    self.data = {name: getattr(messaging.new_message(name), name) for name in names}
    self.data['onroadEvents'] = []
    self.seen = dict.fromkeys(names, True)
    self.alive = dict.fromkeys(names, True)
    self.valid = dict.fromkeys(names, True)
    self.logMonoTime = dict.fromkeys(names, now - 20_000_000)
    self.recv_time = dict.fromkeys(names, (now - 10_000_000) / 1e9)
    self['deviceState'].started = True
    self['deviceState'].startedMonoTime = now - 500_000_000
    self['carState'].vEgo = 12.0
    self['carState'].canValid = True
    self['selfdriveState'].enabled = True
    self['vehicleParameters'].stiffnessFactor = 1.0
    self['vehicleParameters'].steerRatio = 16.7
    self.valid['lateralManeuverPlan'] = False

  def __getitem__(self, name):
    return self.data[name]

  def all_checks(self, names):
    return all(self.seen[name] and self.alive[name] and self.valid[name] for name in names)


def controls_fixture(alpha=False):
  now, offset = 5_000_000_000, 500_000_000
  controls = Controls.__new__(Controls)
  from openpilot.starpilot.lateral.pause import LateralPause, PauseSettings
  controls.lateral_pause = LateralPause(PauseSettings())
  attach_inputs(controls)
  controls.CP = ordinary_params(CAR.CHEVROLET_VOLT, alpha=alpha, radar=True)
  controls.longitudinal_inputs.CP = controls.CP
  controls.CI = CarInterface(controls.CP)
  controls.LoC = LongControl(controls.CP)
  controls.VM = VehicleModel(controls.CP)
  controls.LaC = Mock(spec=LatControl, reset=lambda: None, update=lambda *args: (0.0, 0.0, None))
  controls.lateral_gain_owner = None
  controls.aol_replay = False
  controls.longitudinal_inputs.toyota_sienna_replay = False
  controls.longitudinal_inputs.ioniq6_start_enabled = controls.longitudinal_inputs.gm_start_enabled = False
  controls.longitudinal_inputs.gm_volt_enabled = volt_policy_for(controls.CP) is not None
  controls.longitudinal_inputs.gm_volt_boot_offset_ns = offset
  controls.longitudinal_inputs.gm_volt_source_floor_ns = now - 500_000_000
  controls.torque_host = controls.lane_centering_host = None
  controls.torque_learning_allowed = False
  controls.lane_centering_controller = Mock(spec=LaneCenteringController, reset=lambda: None)
  saved = TemporaryDirectory()
  finalize(controls, saved.cleanup)
  controls.params = Params(saved.name)
  controls.highway_correction_gain = HighwayCorrectionGain()
  controls.highway_smoothing_supported = turn_assist_supported(controls.CP)
  controls.highway_gain = read_gain(controls.params) if controls.highway_smoothing_supported else 1.0
  controls.lane_change_preferences = LiveLaneChangePolicy(controls.params, now_ns=now)
  controls.lane_change_policy = controls.lane_change_preferences.applied
  controls.lane_change_smoother = LaneChangeSmoother()
  controls.curvature = controls.desired_curvature = 0.0
  controls.steer_limited_by_safety = False
  fixture = SubMasterFixture(now)
  controls.sm = MagicMock(spec=SubMaster, **vars(fixture))
  controls.sm.__getitem__.side_effect = fixture.__getitem__
  controls.sm.all_checks.side_effect = fixture.all_checks
  return controls, now, offset


class GMVoltLongPolicyTests(unittest.TestCase):
  def test_final_params_and_unity_feedforward_recurrence(self):
    for alpha in (False, True):
      cp = ordinary_params(CAR.CHEVROLET_VOLT, alpha=alpha, radar=True)
      self.assertEqual(list(cp.longitudinalTuning.kiBP), [5.0, 35.0])
      self.assertEqual(list(cp.longitudinalTuning.kiV), [0.5, 0.5])
      self.assertEqual((cp.stopAccel, cp.longitudinalActuatorDelay), (-1.5, 0.5))
      for speed in (5.0, 8.0, 12.0, 35.0):
        with self.subTest(alpha=alpha, speed=speed):
          long = LongControl(cp)
          self.assertIsInstance(extension_state(long, 'vehicle_policy'), GMVoltLongitudinalPolicy)
          self.assertIsNone(extension_state(long, 'gm_start'))
          limits = CarInterface.get_pid_accel_limits(cp, speed, 0.0)
          self.assertEqual(limits, (-4.0, 2.0))
          for tick in range(1, 21):
            # Original P=0, FF=target, I += .5 * .01 * (target-aEgo).
            output = long.update(True, state(speed), 1.0, False, limits)
            self.assertAlmostEqual(output, 1.0 + 0.005 * tick)
            self.assertEqual((long.pid.p, long.pid.f), (0.0, 1.0))
          self.assertAlmostEqual(output, 1.100)

  def test_original_leak_and_overshoot_boundary_literals(self):
    # speed, target, measured, initial I, lead status, expected I, final output.
    # Unknown lead status must not release negative I. Bleed/cap do not need radar.
    fixtures = (
      (8.0, 0.0, 0.0, -1.0, False, -0.995, -0.995),
      (7.999, 0.0, 0.0, -1.0, False, -1.0, -1.0),
      (8.0, 0.0, 0.0, -1.0, True, -1.0, -1.0),
      (8.0, 0.0, 0.0, -1.0, None, -1.0, -1.0),
      (8.0, 0.12, 0.0, -1.0, False, -0.9944, -0.8744),
      (8.0, -0.12, 0.0, -1.0, False, -0.9956, -1.1156),
      (8.0, 0.120001, 0.120001, -1.0, False, -1.0, -0.879999),
      (8.0, 0.0, -0.120001, -1.0, False, -0.999399995, -0.999399995),
      (12.0, -0.05, 0.45, 1.0, None, 0.9975, 0.9475),
      (12.0, -0.050001, 0.449999, 1.0, None, 0.3975, 0.347499),
      (12.0, -0.2, 0.05, 1.0, None, 0.99875, 0.79875),
      (12.0, -0.1, 0.4, 1.0, None, 0.3975, 0.2975),
      (12.0, -0.2, 0.15, 1.0, None, 0.48825, 0.28825),
      (12.0, -0.2, 0.3, 1.0, None, 0.3975, 0.04),
      (12.0, -0.2, 0.3, 1.0, True, 0.3975, 0.04),
      (12.0, -0.2, 0.3, 1.0, False, 0.3975, 0.04),
      (0.35, -0.2, 0.3, 1.0, None, 0.9975, 0.7975),
      (0.350001, -0.2, 0.3, 1.0, None, 0.3975, 0.04),
      (0.35, -0.4, 0.1, 1.0, None, 0.3975, -0.0025),
      (12.0, -0.6, -0.1, 4.0, None, 1.5975, 0.0),
      (12.0, -1.5, -1.0, 4.0, None, 1.5975, 0.0),
      (12.0, -0.2, 0.55, 1.0, None, 0.24625, 0.04),
      (12.0, -0.2, 1.3, 1.0, None, -0.0075, -0.2075),
    )
    for alpha in (False, True):
      cp = ordinary_params(CAR.CHEVROLET_VOLT, alpha=alpha, radar=True)
      for speed, target, measured, initial_i, lead, expected_i, expected in fixtures:
        with self.subTest(alpha=alpha, fixture=(speed, target, measured, initial_i, lead)):
          long = LongControl(cp)
          long.pid.i = initial_i
          assert lead is None or isinstance(lead, bool)
          output = long.update(True, state(speed, measured), target, False, (-4.0, 2.0), context=LongitudinalContext(has_lead=lead))
          self.assertAlmostEqual(long.pid.i, expected_i)
          self.assertAlmostEqual(output, expected)

  def test_saturation_unwind_stop_and_disengagement(self):
    for alpha in (False, True):
      long = LongControl(ordinary_params(CAR.CHEVROLET_VOLT, alpha=alpha, radar=True))
      for target in (2.0, -4.0):
        for _ in range(5):
          self.assertEqual(long.update(True, state(), target, False, (-4.0, 2.0)), target)
          self.assertEqual(long.pid.i, 0.0)
      long.pid.i = -1.0
      self.assertAlmostEqual(long.update(True, state(), 0.5, False, (-4.0, 2.0)), -0.4975)
      self.assertAlmostEqual(long.pid.i, -0.9975)
      self.assertEqual(long.update(False, state(), 1.0, False, (-4.0, 2.0)), 0.0)
      self.assertEqual(long.pid.i, 0.0)
      self.assertAlmostEqual(long.update(True, state(), 1.0, False, (-4.0, 2.0)), 1.005)
      # Gateway default uses the final vehicle-owned stopping rate.
      self.assertEqual(long.update(True, state(), 0.0, True, (-4.0, 2.0), context=LongitudinalContext(has_lead=False)), -0.03)
      self.assertEqual(long.pid.i, 0.0)

  def test_current_wire_consumes_original_derived_acceleration(self):
    # At frame 4 counter=1. Gas and friction literals come from the original
    # raw-domain Volt equation and old DBC, normalized to the current wire layout.
    fixtures = ((1.0, 0.0, 0.0, None, 1.005, 1070, 0, '4142e1a000bd1e5f', '1000efff01'),
                (-0.2, 0.3, 1.0, None, 0.04, 86, 0, '4142c2e000bd3d1f', '1000efff01'),
                (0.0, 0.0, -1.0, False, -0.995, -650, 1, '4142abe000bd541f', 'afff500001'),
                (0.0, 0.0, -1.0, None, -1.0, -650, 1, '4142abe000bd541f', 'afff500001'),
                (-3.0, 0.0, 0.0, None, -3.015, -650, 265, '4142abe000bd541f', 'aef7510801'))
    for alpha in (False, True):
      cp = ordinary_params(CAR.CHEVROLET_VOLT, alpha=alpha, radar=True)
      for target, measured, initial_i, lead, expected, gas, brake, gas_hex, brake_hex in fixtures:
        with self.subTest(alpha=alpha, target=target, lead=lead):
          long = LongControl(cp)
          long.pid.i = initial_i
          demand = long.update(True, state(12.0, measured), target, False,
                               CarInterface.get_pid_accel_limits(cp, 12.0, 0.0), context=LongitudinalContext(has_lead=lead))
          self.assertAlmostEqual(demand, expected)
          controller, messages = controller_messages(cp, 4, accel=float(demand))
          self.assertEqual(controller.apply_gas, gas)
          self.assertEqual(controller.apply_brake, brake)
          self.assertEqual([m for m in messages if m[0] == 0x2CB], [(0x2CB, bytes.fromhex(gas_hex), 0)])
          self.assertEqual([m for m in messages if m[0] == 0x315], [(0x315, bytes.fromhex(brake_hex), 2)])
          self.assertFalse(any(m[0] == 0x200 for m in messages))

      long, controller = LongControl(cp), None
      wire = {4: '4142e1a000bd1e5f', 8: '8142e24800bd1db6', 12: 'c142e2e800bd1d15',
              16: '0142e39000bd1c70', 20: '4142e43000bd1bcf'}
      for frame in range(4, 24):
        demand = long.update(True, state(), 1.0, False, (-4.0, 2.0))
        self.assertAlmostEqual(demand, 1.0 + 0.005 * (frame - 3))
        controller, messages = controller_messages(cp, frame, controller, accel=float(demand))
        gas_frames = [m for m in messages if m[0] == 0x2CB]
        self.assertEqual(gas_frames, [(0x2CB, bytes.fromhex(wire[frame]), 0)] if frame in wire else [])

  def test_exact_admission_and_other_vehicle_outputs(self):
    for alpha in (False, True):
      for candidate in (CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_2019, CAR.GMC_ACADIA):
        cp = ordinary_params(candidate, alpha=alpha, sascm=True, radar=True)
        if candidate == CAR.CHEVROLET_VOLT_ASCM:
          cp.safetyConfigs[0].safetyParam &= ~int(GMSafetyFlags.VOLT_LONG)
        if candidate == CAR.CHEVROLET_VOLT_2019 and alpha:
          self.assertIsInstance(volt_policy_for(cp), GMVoltLongitudinalPolicy)
          self.assertIsInstance(extension_state(LongControl(cp), 'vehicle_policy'), GMVoltLongitudinalPolicy)
        else:
          self.assertIsNone(volt_policy_for(cp))
          self.assertIsNone(extension_state(LongControl(cp), 'vehicle_policy'))
      for field, value in (('brand', 'toyota'), ('networkLocation', structs.CarParams.NetworkLocation.fwdCamera),
                           ('openpilotLongitudinalControl', False), ('pcmCruise', True), ('passive', True),
                           ('dashcamOnly', True), ('notCar', True), ('radarUnavailable', True), ('flags', int(GMFlags.PEDAL_LONG))):
        cp = ordinary_params(CAR.CHEVROLET_VOLT, alpha=alpha, radar=True)
        setattr(cp, field, value)
        self.assertIsNone(volt_policy_for(cp), (field, value))
      cp = ordinary_params(CAR.CHEVROLET_VOLT, alpha=alpha, radar=True)
      cp.safetyConfigs[0].safetyParam |= int(GMSafetyFlags.HW_CAM)
      self.assertIsNone(volt_policy_for(cp))
      cp.safetyConfigs = []
      self.assertIsNone(volt_policy_for(cp))
      self.assertIsNone(volt_policy_for(ordinary_params(CAR.CHEVROLET_VOLT, alpha=alpha)))
      for candidate in PEDAL_BOLT_CAR:
        cp = pedal_params(candidate, setting=True, pedal=True, alpha_long=alpha)
        self.assertIsNone(volt_policy_for(cp))
        long = LongControl(cp)
        # Same pre-existing pedal P=.071 and FF=.20; no Volt I leak.
        self.assertAlmostEqual(long.update(True, state(), 1.0, False, (-4.0, 2.0), context=LongitudinalContext(has_lead=False)), 0.271)
        long.reset()
        long.pid.i = -1.0
        self.assertEqual(long.update(True, state(), 0.0, False, (-4.0, 2.0), context=LongitudinalContext(has_lead=False)), -1.0)

  def test_controlsd_qualified_leads_reach_actual_longcontrol(self):
    for alpha in (False, True):
      controls, now, offset = controls_fixture(alpha)
      with patch.dict(os.environ, {'REPLAY': '0'}), \
           patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)):
        for present in (False, True):
          controls.sm['radarState'].leadTwo.present = present
          self.assertIs(observed_lead(controls), present)
          controls.LoC.pid.i = -1.0
          cc, _ = controls.state_control()
          self.assertAlmostEqual(cc.actuators.accel, -1.0 if present else -0.995, places=6)
        controls.sm.valid['radarState'] = False
        controls.sm['longitudinalPlan'].aTarget = -0.2
        controls.sm['carState'].aEgo = 0.3
        controls.LoC.pid.i = 1.0
        cc, _ = controls.state_control()
        self.assertAlmostEqual(cc.actuators.accel, 0.04, places=6)  # Unknown radar does not disable bleed/cap.
        controls.sm['selfdriveState'].enabled = False
        with patch.object(controls.longitudinal_inputs, '_gm_volt_observation', side_effect=AssertionError('inactive evidence requested')):
          cc, _ = controls.state_control()
        self.assertEqual(cc.actuators.accel, 0.0)

  def test_stale_invalid_missing_and_replay_evidence_never_means_clear(self):
    changes = [(field, service) for field in ('seen', 'alive', 'valid')
               for service in ('radarState', 'deviceState', 'carState', 'longitudinalPlan')]
    changes += [(field, service) for field in ('old_source', 'old_receipt', 'future_source', 'future_receipt')
                for service in ('radarState', 'deviceState', 'carState', 'longitudinalPlan')]
    changes += [('radar_error', name) for name in ('canError', 'radarFault', 'wrongConfig', 'radarUnavailableTemporary')]
    changes += [(name, '') for name in ('drive_changed', 'offroad', 'can_invalid', 'can_timeout', 'bad_lead', 'clock_missing', 'replay')]
    for field, service in changes:
      with self.subTest(change=(field, service)):
        controls, now, offset = controls_fixture()
        sm = controls.sm
        if field in ('seen', 'alive', 'valid'):
          getattr(sm, field)[service] = False
        elif field == 'old_source':
          sm.logMonoTime[service] = now - (2_000_000_001 if service == 'deviceState' else 150_000_001)
        elif field == 'old_receipt':
          sm.recv_time[service] = (now - (2_001_000_000 if service == 'deviceState' else 151_000_000)) / 1e9
        elif field == 'future_source':
          sm.logMonoTime[service] = now + 1
        elif field == 'future_receipt':
          sm.recv_time[service] = (now + 1_000_000) / 1e9
        elif field == 'radar_error':
          setattr(sm['radarState'].radarErrors, service, True)
        elif field == 'drive_changed':
          sm['deviceState'].startedMonoTime = now - 5_000_000
        elif field == 'offroad':
          sm['deviceState'].started = False
        elif field == 'can_invalid':
          sm['carState'].canValid = False
        elif field == 'can_timeout':
          sm['carState'].canTimeout = True
        elif field == 'bad_lead':
          sm['radarState'].leadOne.dRel = float('nan')
        with patch.dict(os.environ, {'REPLAY': '1' if field == 'replay' else '0'}), \
             patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns',
                   return_value=None if field == 'clock_missing' else (now, now + offset)):
          self.assertIsNone(observed_lead(controls))
          controls.LoC.pid.i = -1.0
          cc, _ = controls.state_control()
          self.assertEqual(cc.actuators.accel, -1.0)

  def test_startup_and_suspend_require_all_new_sources(self):
    controls, now, offset = controls_fixture()
    controls.longitudinal_inputs.gm_volt_boot_offset_ns = None
    clock = [now, offset]
    with patch.dict(os.environ, {'REPLAY': '0'}), \
         patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', side_effect=lambda: (clock[0], clock[0] + clock[1])):
      for suspend in (False, True):
        if suspend:
          clock[1] += 9_000_000_000
        self.assertIsNone(observed_lead(controls))
        self.assertIsNone(observed_lead(controls))
        clock[0] += 20_000_000
        for service in ('radarState', 'deviceState', 'carState', 'longitudinalPlan'):
          controls.sm.logMonoTime[service] = clock[0] - 5_000_000
          controls.sm.recv_time[service] = (clock[0] - 2_000_000) / 1e9
          self.assertIs(observed_lead(controls), False if service == 'longitudinalPlan' else None)


if __name__ == '__main__':
  unittest.main()


class VoltSdgmAcceptedLongControlTests(unittest.TestCase):
  def test_driver_override_release_saturation_and_unwind_use_factory_envelope(self):
    from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
    for c9 in (False, True):
      cp = sdgm_params(brake_c9=c9)
      owner = LongControl(cp)
      limits = CarInterface.get_pid_accel_limits(cp, 10., 40. / 3.6)
      aero = .5 * .30 * (1.05 * cp.wheelbase + .0679) * 1.225 * 10. ** 2 / cp.mass
      self.assertAlmostEqual(limits[1], 2. * 2041 / 2698 - aero)
      # Driver override withdraws actual LongControl and clears accumulated I.
      owner.pid.i = .6
      self.assertEqual(owner.update(False, state(10., 0.), 1.11575, False, limits), 0.)
      self.assertEqual(owner.pid.i, 0.)
      outputs = [owner.update(True, state(10., 0.), 1.11575, False, limits) for _ in range(400)]
      self.assertLessEqual(max(outputs), limits[1])
      saturated_i = owner.pid.i
      for _ in range(100):
        owner.update(True, state(10., 0.), 1.11575, False, limits)
      self.assertAlmostEqual(owner.pid.i, saturated_i)
      output = owner.update(True, state(10., 1.7), -.72, False, limits)
      self.assertLess(output, 0.)
      self.assertLessEqual(owner.pid.i, saturated_i)
      owner.update(False, state(10., 0.), 0., False, limits)
      self.assertEqual(owner.pid.i, 0.)
