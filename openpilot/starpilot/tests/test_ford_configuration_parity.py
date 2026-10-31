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

