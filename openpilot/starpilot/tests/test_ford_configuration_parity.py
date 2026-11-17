"""Actual Ford factory, parser, Card, SelfdriveD, Controls and native matrix.

CAN, perception, USB and clocks are synthetic; no physical acceptance claim.
"""
import pytest
from openpilot.starpilot.tests.ford_lifecycle_helpers import FORD_FACTORIES, ford_joined as ford_joined, run_actual_producer


@pytest.fixture(autouse=True)
def configuration_topology(monkeypatch, topology, request):
  monkeypatch.ford_topology = topology
  if request.node.name.startswith(('test_brake_retains_lateral_with_native_first_feedback',
      'test_cancel_retains_lateral_with_native_first_feedback', 'test_gas_first_engagement_native_cadence',
      'test_first_main_gas_native_cadence', 'test_native_rejected_ui_cannot_replace_stock_cluster')):
    monkeypatch.ford_native_10hz = True
  if request.node.name.startswith('test_route49_first_main_engagement'):
    monkeypatch.ford_first_main = True


@pytest.mark.parametrize('identity', FORD_FACTORIES)
@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('hands_free', [False])
@pytest.mark.parametrize('topology', ['automatic', 'manual', 'multi-panda', 'manual-multi'])
def test_actual_configuration_lifecycle(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='configuration-parity')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [False])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_route49_first_main_engagement_arms_before_cruise_cancel(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='route49')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_brake_retains_lateral_with_native_first_feedback(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='brake-native-cadence')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_cancel_retains_lateral_with_native_first_feedback(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='cancel-native-cadence')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_recorded_radar_creep_enters_native_stopping(ford_joined):
  import json
  import gzip
  from pathlib import Path
  from opendbc.car import structs
  from opendbc.car.ford import fordcan
  from opendbc.safety.tests.libsafety import libsafety_py
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.selfdrive.controls.lib.longcontrol import LongControl
  from openpilot.starpilot.longitudinal.tests.test_lead_approach_planner import Frame, DRIVE_NS

  recorded = json.loads(gzip.decompress((Path(__file__).parent / 'testdata/ford_mache_radar_creep.json.gz').read_bytes()))['frames']
  joined = ford_joined
  endpoint, safety = joined.native, joined.safety
  endpoint.arm()
  endpoint.pump(10, main=5, mask=3)
  assert safety.aol_get_permission_mask() == 3
  planner, controller = LongitudinalPlanner(joined.host.CP, init_v=recorded[0]['vEgo']), LongControl(joined.host.CP)
  checked = 0
  for index, sample in enumerate(recorded):
    sm = Frame(index)
    # Retain the route's actual cross-service age and validity, including radar cadence.
    for service, age in sample['sourceAgeNs'].items():
      sm.logMonoTime[service] = sm.now_ns - 10_000_000 - age
      sm.valid[service] = sample['sourceValid'][service]
    car = sm['carState']
    car.vEgo, car.vEgoRaw, car.aEgo = sample['vEgo'], sample['vEgoRaw'], sample['aEgo']
    car.standstill, car.gasPressed, car.brakePressed = sample['standstill'], sample['gas'], sample['brake']
    car.gearShifter = 'drive'
    car.cruiseState.available = car.cruiseState.enabled = True
    sm['carControl'].longActive = sample['longActive']
    sm['selfdriveState'].enabled = sample['enabled']
    sm['selfdriveState'].experimentalMode = sample['experimental']
    sm['controlsState'].longControlState = controller.long_control_state
    sm['modelV2'].action.shouldStop = sample['shouldStop']
    sm['modelV2'].action.desiredAcceleration = sample['aTarget']
    for key, values in sample['model'].items():
      getattr(sm['modelV2'], key).x = values
    for lead, values in zip((sm['radarState'].leadOne, sm['radarState'].leadTwo), sample['leads'], strict=True):
      for key, value in values.items():
        setattr(lead, key, value)
    planner.update(sm, now_ns=sm.now_ns, drive_id=DRIVE_NS)
    assert planner.mpc.solution_status == 0
    for _ in range(5):
      output = controller.update(sample['longActive'], car, float(planner.output_a_target), planner.output_should_stop, (-3.5, 2.))
      endpoint.pump(1, main=5, mask=3, speed=sample['vEgoRaw'])
      stopping = controller.long_control_state == structs.CarControl.Actuators.LongControlState.stopping
      active = sample['longActive']
      gas = -5. if output < 0. or not active else float(output)
      address, data, bus = fordcan.create_acc_msg(endpoint.packer, joined.host.CI.CC.CAN, active, gas,
        float(output) if active else 0., stopping if active else False, bool(output < 0. and active), v_ego_kph=80.)
      assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus - joined.offset, data))
      if 821. <= sample['routeSeconds'] <= 828.:
        assert planner.output_should_stop, ('Creeping toward stopped radar lead', sample['routeSeconds'])
        assert stopping and output < 0.
        checked += 1
  assert checked > 500


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_gas_first_engagement_native_cadence(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='route22')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_first_main_gas_native_cadence(ford_joined, monkeypatch):
  monkeypatch.ford_main_off_warmup = True
  run_actual_producer(ford_joined, monkeypatch, scenario='route22')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_cancel_retains_lateral_with_native_first_feedback_while_gas_held(ford_joined, monkeypatch, topology):
  monkeypatch.ford_native_10hz = True
  monkeypatch.ford_cancel_gas = True
  run_actual_producer(ford_joined, monkeypatch, scenario='cancel-native-cadence')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[6], FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [False])
@pytest.mark.parametrize('topology', ['automatic'])
def test_neutral_display_waits_for_same_batch_inactive_steering(ford_joined, monkeypatch):
  controller = ford_joined.host.CI.CC
  update = controller.update
  checked = [0]

  def check_batch(control, state, now_nanos):
    result = update(control, state, now_nanos)
    addresses = {address for address, _, _ in result[1]}
    if not control.latActive and addresses & {0x3D8, 0x18A}:
      assert 0x3D6 in addresses, 'Standby display preceded inactive steering proof'
      steering = next(data for address, data, _ in result[1] if address == 0x3D6)
      assert ((steering[0] >> 4) & 7) == 0
      checked[0] += 1
    return result

  monkeypatch.setattr(controller, 'update', check_batch)
  run_actual_producer(ford_joined, monkeypatch, scenario='configuration-parity')
  assert checked[0] > 0


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [False])
@pytest.mark.parametrize('topology', ['automatic'])
@pytest.mark.parametrize('fault', ['heartbeat', 'source-loss'])
def test_native_rejected_ui_cannot_replace_stock_cluster(ford_joined, monkeypatch, fault):
  monkeypatch.ford_native_10hz = True
  monkeypatch.ford_route22_final_fault = fault
  run_actual_producer(ford_joined, monkeypatch, scenario='route22')
  assert ford_joined.rejected_ui_packets
  assert all(address in (0x18A, 0x3D8) for _, address in ford_joined.rejected_ui_packets)



@pytest.fixture
def conventional_ford_joined(request, monkeypatch, mode, identity, alpha, hands_free):
  monkeypatch.ford_non_adaptive = True
  monkeypatch.ford_bluepilot = True
  monkeypatch.ford_angle = mode == 'angle'
  monkeypatch.ford_starpilot = mode == 'starpilot'
  monkeypatch.ford_pinion = True
  monkeypatch.ford_nonzero = True
  monkeypatch.ford_native_first_pedal = True
  monkeypatch.ford_native_10hz = True
  return request.getfixturevalue('ford_joined')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [False])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
@pytest.mark.parametrize('mode', ['curvature', 'angle', 'starpilot'])
@pytest.mark.parametrize('scenario', ['configuration-parity', 'cancel-native-cadence'])
def test_conventional_cruise_keeps_actual_aol_without_longitudinal(conventional_ford_joined, monkeypatch, scenario):
  joined = conventional_ford_joined
  assert not joined.host.CP.openpilotLongitudinalControl
  run_actual_producer(joined, monkeypatch, scenario=scenario)
  assert joined.stock_cruise_active_samples > 0
  if scenario == 'configuration-parity':
    assert joined.nonzero_after_brake > 0


@pytest.mark.parametrize('identity', FORD_FACTORIES)
@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('marked', [False, True])
@pytest.mark.parametrize('topology', ['automatic'])
def test_conventional_cruise_event_requires_stock_marked_ford(identity, alpha, marked):
  from opendbc.car import gen_empty_fingerprint, structs
  from opendbc.car.ford.interface import CarInterface
  from openpilot.cereal import log
  from openpilot.selfdrive.car.car_events import CarEvents
  fp = gen_empty_fingerprint()
  fp[0].update({0x5A: 8, 0x3A6: 8, 0x3A7: 8})
  fp[2].update({0x3D6: 8, 0x186: 8})
  cp = CarInterface.get_params(identity[0], fp, [], alpha, False, False)
  cp.alternativeExperience = 32 if marked else 0
  state = structs.CarState.new_message(canValid=True, gearShifter='drive',
      cruiseState={'available': True, 'enabled': True, 'nonAdaptive': True})
  events = CarEvents(cp).create_common_events(state, structs.CarState.new_message())
  assert (log.OnroadEvent.EventName.wrongCruiseMode in events.names) == (alpha or not marked)
  state.steerFaultPermanent = state.accFaulted = True
  faults = CarEvents(cp).create_common_events(state, state)
  assert log.OnroadEvent.EventName.steerUnavailable in faults.names
  assert log.OnroadEvent.EventName.accFaulted in faults.names


@pytest.mark.parametrize('topology', ['automatic'])
@pytest.mark.parametrize('invalid', ['passive', 'dashcam', 'not-car', 'missing-panda', 'extra-owner', 'wrong-word', 'wrong-brand'])
def test_conventional_cruise_never_admits_an_unsupported_profile(invalid):
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.ford.interface import CarInterface
  from openpilot.starpilot.car.ford.aol import stock_conventional_cruise_supported
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  fp[2].update({0x3D6: 8, 0x186: 8})
  cp = CarInterface.get_params(FORD_FACTORIES[11][0], fp, [], False, False, False)
  cp.alternativeExperience = 32
  if invalid == 'passive':
    cp.passive = True
  elif invalid == 'dashcam':
    cp.dashcamOnly = True
  elif invalid == 'not-car':
    cp.notCar = True
  elif invalid == 'missing-panda':
    cp.safetyConfigs = []
  elif invalid == 'extra-owner':
    cp.safetyConfigs = [cp.safetyConfigs[0].to_dict()] * 2
  elif invalid == 'wrong-word':
    cp.safetyConfigs[0].safetyParam = 3
  else:
    cp.brand = 'toyota'
  assert not stock_conventional_cruise_supported(cp)


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [True])
@pytest.mark.parametrize('topology', ['automatic'])
@pytest.mark.parametrize('mode', ['curvature', 'angle', 'starpilot'])
def test_conventional_cruise_keeps_alpha_long_guard(conventional_ford_joined, monkeypatch):
  run_actual_producer(conventional_ford_joined, monkeypatch, scenario='conventional-long-refusal')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [False])
@pytest.mark.parametrize('hands_free', [True])
@pytest.mark.parametrize('topology', ['automatic'])
@pytest.mark.parametrize('mode', ['starpilot'])
def test_conventional_cruise_stops_for_lost_model(conventional_ford_joined, monkeypatch):
  run_actual_producer(conventional_ford_joined, monkeypatch, scenario='conventional-model-loss')
