import gzip

from dataclasses import replace
import json
import tempfile
import unittest

from openpilot.cereal import messaging
from openpilot.selfdrive.controls.plannerd import traffic_observation
from openpilot.common.params import Params
from openpilot.starpilot.conditional_mode.consumer import ModeConsumer
from openpilot.starpilot.conditional_mode.host import ConditionalModeHost
from openpilot.starpilot.conditional_mode.policy import ManualIntent, ModeChoice
from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences, manual_for_drive
from openpilot.starpilot.conditional_mode.projection import ConditionalOwnerContext, ObservedBool
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner, DOCUMENT_KEY
from openpilot.starpilot.conditional_mode.status import StatusPublisher, settings_fingerprint
from openpilot.starpilot.conditional_mode.tests.test_projection import BOOT, CP, MONO, FakeSubMaster, serialized_scene

# Exact recorded model arrays; green-like model clearance is not a light-color observation.
# Segment97 model timestamp 5885.753172288.
CLEAR_97_X = (
  0.0, -0.00011479854583740234, -0.00015497207641601562,
  -0.0016298294067382812, -0.002017974853515625, -0.001964569091796875,
  -0.0055084228515625, -0.0010929107666015625, 0.007328033447265625,
  0.018890380859375, 0.04547119140625, 0.131591796875,
  0.268798828125, 0.50439453125, 0.84716796875,
  1.3984375, 2.15625, 3.17578125,
  4.5234375, 6.21875, 8.328125,
  10.8828125, 13.8359375, 17.3125,
  21.265625, 25.734375, 30.8125,
  36.28125, 42.40625, 49.125,
  56.4375, 64.3125, 72.375,
)
CLEAR_97_V = (
  -0.0175018310546875, -0.014984130859375, -0.01061248779296875,
  -0.019439697265625, -0.019287109375, -0.01033782958984375,
  -0.002498626708984375, 0.0158233642578125, 0.061737060546875,
  0.14013671875, 0.260009765625, 0.450439453125,
  0.7265625, 1.0732421875, 1.52734375,
  2.076171875, 2.72265625, 3.4375,
  4.1953125, 4.97265625, 5.7734375,
  6.58203125, 7.37109375, 8.1484375,
  8.9140625, 9.6640625, 10.4609375,
  11.171875, 11.8828125, 12.578125,
  13.25, 13.890625, 14.4765625,
)
# Segment98 model timestamp 5955.949773908.
CLEAR_98_X = (
  0.0, -3.349781036376953e-05, 3.8743019104003906e-05,
  -0.0006380081176757812, 4.482269287109375e-05, -0.00015115737915039062,
  -0.0018358230590820312, -0.0010929107666015625, 0.006130218505859375,
  0.0115203857421875, 0.0239715576171875, 0.08087158203125,
  0.1771240234375, 0.352783203125, 0.61767578125,
  1.060546875, 1.7021484375, 2.587890625,
  3.759765625, 5.29296875, 7.21875,
  9.5625, 12.328125, 15.59375,
  19.265625, 23.4375, 28.21875,
  33.34375, 39.09375, 45.375,
  52.1875, 59.53125, 67.125,
)
CLEAR_98_V = (
  -0.00688934326171875, -0.004718780517578125, -0.001102447509765625,
  -0.0083465576171875, -0.00527191162109375, -0.001102447509765625,
  0.001110076904296875, 0.0038852691650390625, 0.0279693603515625,
  0.07244873046875, 0.1448974609375, 0.28271484375,
  0.49560546875, 0.779296875, 1.1708984375,
  1.66015625, 2.2578125, 2.935546875,
  3.671875, 4.421875, 5.19921875,
  5.98828125, 6.75, 7.49609375,
  8.21875, 8.921875, 9.65625,
  10.3203125, 10.9765625, 11.6484375,
  12.3125, 12.953125, 13.5546875,
)
# Segment97 model timestamp 5868.005344185.
RED_97_X = (
  0.0, 0.0055389404296875, 0.022308349609375,
  0.048583984375, 0.08428955078125, 0.126708984375,
  0.1688232421875, 0.21923828125, 0.264404296875,
  0.2998046875, 0.321044921875, 0.360107421875,
  0.367919921875, 0.39111328125, 0.361083984375,
  0.37841796875, 0.380126953125, 0.381103515625,
  0.364501953125, 0.37548828125, 0.37060546875,
  0.3779296875, 0.412109375, 0.410888671875,
  0.328369140625, 0.384765625, 0.3583984375,
  0.252197265625, 0.283935546875, 0.196044921875,
  0.23974609375, 0.2008056640625, 0.12548828125,
)
RED_97_V = (
  0.568359375, 0.56787109375, 0.5615234375,
  0.525390625, 0.492431640625, 0.446044921875,
  0.396484375, 0.3291015625, 0.269775390625,
  0.194580078125, 0.126220703125, 0.08367919921875,
  0.048095703125, 0.014434814453125, 0.003330230712890625,
  -0.01116180419921875, -0.008270263671875, -0.006103515625,
  -0.00666046142578125, -0.01047515869140625, -0.010406494140625,
  -0.005828857421875, -0.0233154296875, -0.0280303955078125,
  -0.0137786865234375, -0.0204010009765625, -0.0196990966796875,
  -0.0274810791015625, -0.035552978515625, -0.046844482421875,
  -0.0482177734375, -0.069091796875, -0.0882568359375,
)


class TestStopCommitTransport(unittest.TestCase):
  def setUp(self):
    directory = self.enterContext(tempfile.TemporaryDirectory())
    self.params = Params(directory)
    self.save(ModeChoice.CEM)
    self.owner = ConditionalSettingsOwner(self.params)
    self.host = ConditionalModeHost()
    self.publisher = StatusPublisher()
    self.consumer = ModeConsumer()
    self.drive = MONO - 1_000_000_000
    self.tick = 0

  def save(self, choice):
    self.params.put(DOCUMENT_KEY, json.loads(encode_preferences(SavedPreferences(mode=choice))), block=True)

  def sample(self, *, invalid=False, lead=False, horizon=20., manual=ManualIntent.NONE, alive=True, gas=False,
             unavailable=None, traffic_verdict=None, traffic_owner_present=None, cp=CP, standstill=False,
             model_should_stop=True, enabled=True, ego_speed=None, raw_speed=0.,
             model_position=None, model_velocity=None, forcing=False, stop_sign=False):
    now = MONO + self.tick * 50_000_000
    self.tick += 1
    payloads = serialized_scene(stamp=BOOT+now-MONO-2_000_000, horizon=horizon, speed=4.27,
                                lead_present=lead, lead_distance=50., lead_speed=4.27, gas_pressed=gas,
                                standstill=standstill)
    event = messaging.new_message('modelV2', valid=True)
    event.modelV2 = payloads['modelV2']
    event.modelV2.orientationRate.z = [0.] * 33
    event.modelV2.action.shouldStop = model_should_stop
    if model_position is not None:
      event.modelV2.position.x = model_position
    if model_velocity is not None:
      event.modelV2.velocity.x = model_velocity
    if invalid:
      values = list(event.modelV2.position.x)
      values[-2] = values[-1] + .015308380126953125
      event.modelV2.position.x = values
      event.modelV2.velocity.x = [4.27] * 32 + [-.0027507354971021414]
    payloads['modelV2'] = messaging.log_from_bytes(event.to_bytes()).modelV2
    if ego_speed is not None:
      car = messaging.new_message('carState', valid=True)
      car.carState = payloads['carState']
      car.carState.vEgo = ego_speed
      car.carState.vEgoRaw = raw_speed
      payloads['carState'] = messaging.log_from_bytes(car.to_bytes()).carState
    controls = messaging.new_message('controlsState', valid=True)
    controls.controlsState = payloads['controlsState']
    controls.controlsState.curvature = 0.
    payloads['controlsState'] = messaging.log_from_bytes(controls.to_bytes()).controlsState
    state = messaging.new_message('selfdriveState', valid=True)
    state.selfdriveState = payloads['selfdriveState']
    state.selfdriveState.enabled = enabled
    payloads['selfdriveState'] = messaging.log_from_bytes(state.to_bytes()).selfdriveState
    sm = FakeSubMaster(payloads, now-2_000_000)
    sm.alive['modelV2'] = alive
    if unavailable is not None:
      sm.alive[unavailable] = False
    context = ConditionalOwnerContext(
      traffic_mode=ObservedBool(False, now-2_000_000),
      stop_sign_confirmed=ObservedBool(stop_sign, now-2_000_000),
      forcing_stop=ObservedBool(forcing, now-2_000_000),
      dashboard_stop_sign=ObservedBool(False, now-2_000_000),
      previous_experimental=ObservedBool(False, now-2_000_000),
      red_light=ObservedBool(False, now-2_000_000),
      plan_forcing_stop=ObservedBool(forcing, now-2_000_000),
      plan_should_stop=ObservedBool(False, now-2_000_000),
      pedal_override=ObservedBool(gas, now-2_000_000), plan_allow_throttle=ObservedBool(True, now-2_000_000))
    if traffic_owner_present is not None:
      context = replace(context, traffic_mode=traffic_observation(traffic_verdict,
                        model_ns=now-2_000_000, owner_present=traffic_owner_present))
    proposal = self.host.sample(sm, cp, now_mono_ns=now, now_boot_ns=BOOT+now-MONO,
      sample_skew_ns=1000, drive_id=self.drive, settings_owner=self.owner,
      manual=manual_for_drive(manual, self.drive, self.drive+1), owner_context=context,
      selected_t_follow_s=.5, selected_t_follow_observed_mono_ns=now-2_000_000)
    event = self.publisher.attach(None, proposal, self.owner.current, now_ns=now, drive_id=self.drive,
                                  model_ns=now-2_000_000, car_state_ns=now-2_000_000)
    state = messaging.log_from_bytes(event.to_bytes()).slcState
    result = self.consumer.sample(state, now_ns=now, now_boot_ns=BOOT+now-MONO, sample_skew_ns=1000,
      message_ns=now, receipt_ns=now, drive_id=self.drive, model_ns=now-2_000_000,
      car_state_ns=now-2_000_000, authority=alive, stock_experimental=False,
      choice=proposal.choice, settings_fingerprint=settings_fingerprint(self.owner.current))
    return proposal, result

  def prime(self):
    for _ in range(40):
      proposal, result = self.sample()
    self.assertTrue(self.host.projector.stop_detector.committed)
    self.assertTrue(result.accepted and result.experimental)

  def test_recorded_stationary_filter_noise_keeps_stop_and_releases_on_model_clear(self):
    self.prime()
    # Recorded Ioniq6 estimates alternate around zero while raw wheel speed is
    # exactly zero. A model path clearing is not a traffic-light color assertion.
    speeds = (0., -0.0008391447481699288, 0., -0.001509889611043036,
              0., -1.401298464324817e-45)
    for tick in range(24):
      proposal, result = self.sample(standstill=True, ego_speed=speeds[tick % len(speeds)],
                                    horizon=0.15, model_should_stop=True)
      self.assertEqual(proposal.status, 'proposed')
      self.assertEqual(proposal.projected.scene.speed_mps, 0.)
      self.assertTrue(self.host.projector.stop_detector.standstill_committed)
      self.assertTrue(result.accepted and result.experimental)
      self.assertEqual(proposal.decision.reason.value, 'cem_stop')
    for tick in range(16):
      proposal, result = self.sample(standstill=True, ego_speed=speeds[tick % len(speeds)],
                                    horizon=70., model_should_stop=False)
      self.assertEqual(proposal.status, 'proposed')
      self.assertTrue(result.accepted)
      if tick < 10:
        self.assertTrue(self.host.projector.stop_detector.standstill_committed)
        self.assertTrue(result.experimental)
    self.assertFalse(self.host.projector.stop_detector.committed)
    self.assertFalse(self.host.projector.stop_detector.standstill_committed)
    self.assertFalse(result.experimental)
    self.assertEqual(proposal.decision.reason.value, 'no_trigger')

  def test_recorded_stationary_prefix_releases_only_existing_stop_with_fresh_clear_model(self):
    # The same raw geometry cannot acquire a new stop at cold start.
    for _ in range(30):
      _, result = self.sample(standstill=True, ego_speed=0., model_should_stop=False,
                              model_position=CLEAR_97_X, model_velocity=CLEAR_97_V)
      self.assertFalse(result.experimental)
      self.assertFalse(self.host.projector.stop_detector.committed)
    self.prime()
    material_tail = list(CLEAR_97_X)
    material_tail[-2] = material_tail[-1] + .015308380126953125
    # Short red-like horizon, explicit shouldStop and material later backtracking
    # each retain the stop instead of manufacturing release evidence.
    cases = ((RED_97_X, RED_97_V, False, 0., 0.),
             (CLEAR_97_X, CLEAR_97_V, True, 0., 0.),
             (material_tail, CLEAR_97_V, False, 0., 0.),
             (CLEAR_97_X, CLEAR_97_V, False, .06, 0.),
             (CLEAR_97_X, CLEAR_97_V, False, 0., .06))
    for position, velocity, should_stop, speed, raw in cases:
      for _ in range(16):
        _, result = self.sample(standstill=True, ego_speed=speed, raw_speed=raw,
                                model_should_stop=should_stop, model_position=position, model_velocity=velocity)
        self.assertTrue(result.accepted and result.experimental)
        self.assertTrue(self.host.projector.stop_detector.standstill_committed)
    speeds = (0., -0.0008391447481699288, -0.001509889611043036, -1.401298464324817e-45)
    released = False
    for tick in range(24):
      position, velocity = (CLEAR_97_X, CLEAR_97_V) if tick % 2 else (CLEAR_98_X, CLEAR_98_V)
      proposal, result = self.sample(standstill=True, ego_speed=speeds[tick % len(speeds)],
                                     model_should_stop=False, model_position=position, model_velocity=velocity)
      self.assertTrue(result.accepted)
      self.assertIsNone(proposal.projected.model_horizon_m)  # Full spatial geometry stays unavailable.
      if tick < 10:
        self.assertTrue(result.experimental)
      if not self.host.projector.stop_detector.committed:
        released = True
        self.assertFalse(self.host.projector.stop_detector.standstill_committed)
        self.assertFalse(result.experimental)
    self.assertTrue(released)
    self.assertFalse(self.host.projector.stop_detector.committed)

  def test_departing_lead_stationary_prefix_retains_model_clear_release_guards(self):
    self.prime()
    # Establish the actual moving lead detector before reaching the light.
    for _ in range(20):
      self.sample(lead=True, horizon=70., model_should_stop=True)
    self.assertTrue(self.host.projector.lead_detector.tracked)
    short = tuple(value * 53.9 / CLEAR_97_X[-1] for value in CLEAR_97_X)
    for position, stopping, unavailable, forcing in ((CLEAR_97_X, True, None, False), (short, False, None, False),
                                                    (CLEAR_97_X, False, 'modelV2', False)):
      for _ in range(14):
        self.sample(lead=True, standstill=True, ego_speed=0., model_should_stop=stopping,
                    model_position=position, model_velocity=CLEAR_97_V, unavailable=unavailable, forcing=forcing)
        with self.subTest(stopping=stopping, unavailable=unavailable, forcing=forcing, endpoint=position[-1]):
          self.assertTrue(self.host.projector.stop_detector.committed)
    for tick in range(16):
      proposal, _ = self.sample(lead=True, standstill=True, ego_speed=0., model_should_stop=False,
                                model_position=CLEAR_97_X, model_velocity=CLEAR_97_V)
      self.assertIsNone(proposal.projected.model_horizon_m)
      if tick < 10:
        self.assertTrue(self.host.projector.stop_detector.committed)
    self.assertFalse(self.host.projector.stop_detector.committed)

    for _ in range(16):
      proposal, _ = self.sample(lead=True, standstill=True, ego_speed=0., model_should_stop=False,
                                model_position=CLEAR_97_X, model_velocity=CLEAR_97_V, forcing=True)
      self.assertTrue(proposal.projected.scene.standstill_stop_hold)

  def test_fresh_invalid_geometry_and_unknown_lead_tracking_preserve_actual_transport(self):
    self.prime()
    for _ in range(30):
      proposal, result = self.sample(invalid=True, lead=True, horizon=6.683574199676514)
      self.assertTrue(proposal.projected.scene.committed_stop)
      self.assertTrue(result.accepted and result.experimental)

  def test_mode_change_manual_chill_stock_then_valid_clear(self):
    self.prime()
    for choice in (ModeChoice.CCM, ModeChoice.STOCK):
      self.save(choice)
      for _ in range(25):
        proposal, result = self.sample(manual=ManualIntent.FORCE_CHILL)
        self.assertTrue(result.accepted and result.experimental)
      self.assertEqual(proposal.choice, choice)
    for _ in range(30):
      proposal, result = self.sample(horizon=192., model_should_stop=False)
    self.assertFalse(result.experimental)
    self.assertFalse(self.host.projector.stop_detector.committed)

  def test_cold_stock_and_lost_authority_or_driver_override_cannot_hold_commit(self):
    self.save(ModeChoice.STOCK)
    for _ in range(30):
      _, result = self.sample()
      self.assertFalse(result.experimental)
    self.assertFalse(self.host.projector.stop_detector.committed)
    self.save(ModeChoice.CEM)
    self.prime()
    _, result = self.sample(alive=False)
    self.assertFalse(result.experimental)
    self.assertTrue(self.host.projector.stop_detector.committed)
    self.prime()
    _, result = self.sample(gas=True)
    self.assertFalse(self.host.projector.stop_detector.committed)

  def test_unknown_authority_preserves_memory_but_cannot_publish_permission(self):
    for service in ('carControl', 'selfdriveState'):
      self.prime()
      _, result = self.sample(unavailable=service)
      self.assertFalse(result.experimental)
      self.assertTrue(self.host.projector.stop_detector.committed)
      _, result = self.sample(invalid=True)
      self.assertTrue(result.experimental)
      self.assertTrue(self.host.projector.stop_detector.committed)
      _, result = self.sample(enabled=False)
      self.assertFalse(result.experimental)
      self.assertFalse(self.host.projector.stop_detector.committed)

  def test_current_traffic_owner_decision_survives_raw_packet_silence(self):
    from openpilot.starpilot.conditional_mode.tests.test_traffic import TrafficOwnerTests, DRIVE
    physical = TrafficOwnerTests('test_unassigned_preserves_ordinary_profile_without_media')
    physical.setUp()
    self.addCleanup(physical.doCleanups)
    physical.params, physical.settings = self.params, self.owner
    physical.params.put('ModeButtonControl', 6, block=True)
    self.drive = DRIVE
    for _ in range(40):
      now = MONO + self.tick * 50_000_000
      verdict = physical.sample(now)
      self.assertFalse(verdict.requested)
      proposal, result = self.sample(traffic_verdict=verdict, traffic_owner_present=True)
    self.assertTrue(self.host.projector.stop_detector.committed)
    self.assertTrue(result.accepted and result.experimental)
    now = MONO + self.tick * 50_000_000
    on = physical.sample(now, physical.event(1, now, toggle=True))
    self.assertTrue(on.effective)
    for _ in range(30):
      now = MONO + self.tick * 50_000_000
      held = physical.sample(now)
      self.assertEqual(held.source_mono_ns, on.source_mono_ns)
      proposal, result = self.sample(traffic_verdict=held, traffic_owner_present=True)
      self.assertTrue(self.host.projector.stop_detector.committed)
      self.assertTrue(result.accepted and result.experimental)
    self.assertEqual(held.source_boot_ns, 0)
    _, result = self.sample(traffic_verdict=held, traffic_owner_present=True, unavailable='carControl')
    self.assertFalse(result.experimental)
    self.assertTrue(self.host.projector.stop_detector.committed)

  def test_optional_absent_traffic_owner_does_not_prevent_cold_cem_stop(self):
    for _ in range(40):
      _, result = self.sample(traffic_owner_present=False)
    self.assertTrue(self.host.projector.stop_detector.committed)
    self.assertTrue(result.accepted and result.experimental)
    self.sample(traffic_owner_present=False, alive=False)
    self.assertTrue(self.host.projector.stop_detector.committed)

  def test_unsupported_optional_traffic_owner_does_not_block_actual_other_factory_cem(self):
    from opendbc.car.honda.interface import CarInterface
    from opendbc.car.honda.values import CAR
    from openpilot.starpilot.conditional_mode.tests.test_traffic import TrafficOwnerTests, DRIVE
    physical = TrafficOwnerTests('test_unassigned_preserves_ordinary_profile_without_media')
    physical.setUp()
    self.addCleanup(physical.doCleanups)
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    self.assertTrue(cp.openpilotLongitudinalControl)
    physical.cp = cp
    self.drive = DRIVE
    for _ in range(40):
      now = MONO + self.tick * 50_000_000
      verdict = physical.sample(now)
      self.assertEqual(verdict.reason, 'unsupported_car')
      _, result = self.sample(traffic_verdict=verdict, traffic_owner_present=True, cp=cp)
    self.assertTrue(self.host.projector.stop_detector.committed)
    self.assertTrue(result.accepted and result.experimental)
    self.sample(traffic_verdict=verdict, traffic_owner_present=True, cp=cp, unavailable='carControl')
    self.assertTrue(self.host.projector.stop_detector.committed)


def test_recorded_ford_stopped_geometry_does_not_release_native_stop(request):
  from pathlib import Path
  from opendbc.car import gen_empty_fingerprint, structs
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.selfdrive.controls.lib.longcontrol import LongControl
  from openpilot.starpilot.conditional_mode.policy import ConditionalModePolicy
  from openpilot.starpilot.conditional_mode.projection import SceneProjector
  from openpilot.starpilot.conditional_mode.tests.test_projection import owner_context
  from openpilot.starpilot.longitudinal.tests.test_lead_approach_planner import Frame, DRIVE_NS

  recorded = json.loads(gzip.decompress((Path(__file__).parent / 'testdata/ford_stationary_stop.json.gz').read_bytes()))['frames']
  fingerprint = gen_empty_fingerprint()
  fingerprint[0][0x5A] = 8
  cp = CarInterface.get_params(CAR.FORD_MUSTANG_MACH_E_MK1, fingerprint, [], True, False, False)
  assert cp.openpilotLongitudinalControl
  from opendbc.car.ford import fordcan
  from opendbc.safety.tests.test_ford_aol import TestFordAolDriverIntent
  from opendbc.safety.tests.libsafety import libsafety_py
  endpoint = TestFordAolDriverIntent(methodName='runTest')
  endpoint.setUp()
  request.addfinalizer(endpoint.tearDown)
  endpoint.reset(3)
  endpoint.pump(40, main=0)
  endpoint.pump(10, main=3)
  endpoint.pump(10, main=5, mask=3)
  assert endpoint.safety.aol_get_permission_mask() == 3
  from opendbc.can import CANParser
  packet_parser = CANParser('ford_lincoln_base_pt', [('ACCDATA', 50)], 0)
  bus = fordcan.CanBus(fingerprint=fingerprint)
  projector, policy = SceneProjector(), ConditionalModePolicy()
  planner, controller = LongitudinalPlanner(cp), LongControl(cp)
  checked = 0
  for index, sample in enumerate(recorded):
    stamp = MONO + index * 50_000_000
    boot = BOOT + index * 50_000_000
    payloads = serialized_scene(stamp=boot, speed=sample['vEgo'], standstill=sample['standstill'],
                                lead_present=False, experimental_mode=True)
    car = messaging.new_message('carState')
    car.carState = payloads['carState']
    car.carState.vEgoRaw = sample['vEgoRaw']
    car.carState.cruiseState.available = car.carState.cruiseState.enabled = True
    payloads['carState'] = messaging.log_from_bytes(car.to_bytes()).carState
    command = messaging.new_message('carControl')
    command.carControl = payloads['carControl']
    command.carControl.longActive = sample['longActive']
    command.carControl.enabled = sample['enabled']
    payloads['carControl'] = messaging.log_from_bytes(command.to_bytes()).carControl
    controls = messaging.new_message('controlsState')
    controls.controlsState = payloads['controlsState']
    controls.controlsState.longControlState = sample['longControlState']
    payloads['controlsState'] = messaging.log_from_bytes(controls.to_bytes()).controlsState
    model = messaging.new_message('modelV2')
    model.modelV2 = payloads['modelV2']
    model.modelV2.position.x = sample['x']
    model.modelV2.action.shouldStop = sample['shouldStop']
    model.modelV2.action.desiredAcceleration = sample['aTarget']
    payloads['modelV2'] = messaging.log_from_bytes(model.to_bytes()).modelV2
    sm = FakeSubMaster(payloads, stamp)
    projected = projector.project(sm, cp, now_mono_ns=stamp+1_000_000, now_boot_ns=boot+1_000_000,
      sample_skew_ns=1000, safe_mode=False, owner_context=owner_context(stamp))
    if projected.authority is None:
      continue
    decision = policy.step((stamp+1_000_000)/1e9, ModeChoice.CEM, ManualIntent.NONE,
                           projected.authority, projected.scene)
    native = Frame(index, approach=False)
    native['carState'].vEgo = max(sample['vEgo'], 0.)
    native['carState'].standstill = sample['standstill']
    native['carState'].cruiseState.available = native['carState'].cruiseState.enabled = True
    native['carControl'].longActive = True
    native['selfdriveState'].enabled = True
    native['selfdriveState'].experimentalMode = decision.requested_experimental
    native['modelV2'].action.shouldStop = sample['shouldStop']
    native['modelV2'].action.desiredAcceleration = sample['aTarget']
    for lead in (native['radarState'].leadOne, native['radarState'].leadTwo):
      lead.present = False
    if index == 1:
      for _ in range(100):
        controller.update(True, native['carState'], -.1, True, (-3.5, 2.))
    native['controlsState'].longControlState = controller.long_control_state
    planner.update(native, now_ns=native.now_ns, drive_id=DRIVE_NS)
    assert planner.mpc.solution_status == 0
    for _ in range(5):
      output = controller.update(True, native['carState'], float(planner.output_a_target),
                                 planner.output_should_stop, (-3.5, 2.))
      assert -3.5 <= output <= 2.
      endpoint.pump(1, main=5, mask=3, speed=max(sample['vEgoRaw'], 0.))
      stopping = controller.long_control_state == structs.CarControl.Actuators.LongControlState.stopping
      # Pack the actual LongControl result and keep the original native TX guard.
      address, data, source = fordcan.create_acc_msg(endpoint.packer, bus, True, -5. if output < 0. else output,
        float(output), stopping, output < 0., v_ego_kph=80.)
      assert endpoint.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, source, data))
      packet_parser.update([(native.now_ns, [(address, data, source)])])
      if sample['routeSeconds'] >= 596.:
        assert decision.requested_experimental, ('Lost accepted Ford stop', sample['routeSeconds'])
        assert planner.output_should_stop
        assert packet_parser.vl['ACCDATA']['AccStopStat_B_Rq'] == 1
        assert output <= 0., ('Released brake during model stop', sample['routeSeconds'])
        checked += 1
  assert checked > 200
