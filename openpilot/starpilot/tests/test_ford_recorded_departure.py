import gzip
import json
from pathlib import Path

import pytest
from types import SimpleNamespace
from openpilot.cereal import messaging
from opendbc.car.ford import fordcan
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.ford.stop_release import StoppedLeadRelease
from openpilot.starpilot.longitudinal.tests import test_cem_green_release as green
from opendbc.car import structs
from openpilot.selfdrive.controls.lib.drive_helpers import get_accel_from_plan
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner, CONTROL_N_T_IDX
from openpilot.starpilot.longitudinal.tests.test_lead_approach_planner import Frame, DRIVE_NS
from openpilot.starpilot.tests.ford_lifecycle_helpers import FORD_FACTORIES, ford_joined as ford_joined


@pytest.fixture(autouse=True)
def topology_settings(monkeypatch, request):
  monkeypatch.ford_topology = request.node.callspec.params.get('topology', 'automatic')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [True])
def test_recorded_departure_native_plan_is_ready(ford_joined):
  samples = json.loads(gzip.decompress((Path(__file__).parent / 'testdata/ford_mache_lead_departure.json.gz').read_bytes()))['frames']
  planner = LongitudinalPlanner(ford_joined.host.CP, init_v=samples[0]['car']['vEgo'])
  controller = LongControl(ford_joined.host.CP)
  report = []
  for index, sample in enumerate(samples):
    sm = Frame(index)
    for service, age in sample['sourceAgeNs'].items():
      sm.logMonoTime[service] = sm.now_ns - 10_000_000 - age
      sm.valid[service] = sample['sourceValid'][service]
    car = sm['carState']
    for key, value in sample['car'].items():
      setattr(car, key, value)
    car.gearShifter = 'drive'
    car.cruiseState.available = car.cruiseState.enabled = True
    sm['carControl'].longActive = sample['longActive']
    sm['carControl'].orientationNED = sample['orientationNED']
    sm['selfdriveState'].enabled = sample['enabled']
    sm['selfdriveState'].experimentalMode = sample['experimental']
    sm['controlsState'].longControlState = controller.long_control_state
    sm['controlsState'].forceDecel = sample['forceDecel']
    for key, values in sample['model'].items():
      if key == 'action':
        for name, value in values.items():
          setattr(sm['modelV2'].action, name, value)
      elif key == 'leadsV3':
        leads = sm['modelV2'].init('leadsV3', len(values))
        for lead, value in zip(leads, values, strict=True):
          lead.from_dict(value)
      else:
        getattr(sm['modelV2'], key).x = values['x']
    for lead, values in zip((sm['radarState'].leadOne, sm['radarState'].leadTwo), sample['leads'], strict=True):
      for key in ('dRel', 'yRel', 'vRel', 'vLead', 'vLeadK', 'aLeadK', 'present', 'aLeadTau', 'modelProb', 'radar', 'radarTrackId'):
        setattr(lead, key, values[key])
    planner.update(sm, now_ns=sm.now_ns, drive_id=DRIVE_NS)
    assert planner.mpc.solution_status == 0
    output = 0.
    for _ in range(5):
      output = controller.update(sample['longActive'], car, float(planner.output_a_target), planner.output_should_stop, (-3.5, 2.))
    if 333. <= sample['routeSeconds'] <= 335.:
      report.append({'t': sample['routeSeconds'], 'target': float(planner.output_a_target), 'stop': bool(planner.output_should_stop),
        'cruise': float(planner.a_cruise), 'mpc': float(get_accel_from_plan(planner.v_desired_trajectory, planner.a_desired_trajectory,
          CONTROL_N_T_IDX, action_t=float(ford_joined.host.CP.longitudinalActuatorDelay) + .05)),
        'model': sample['model']['action']['desiredAcceleration'], 'source': str(planner.mpc.source),
        'output': output, 'state': str(controller.long_control_state), 'forceStop': planner.force_stop_plan.should_stop})
  assert all(not r['stop'] and float(r['target']) > 0 for r in report if 334.4 < float(r['t']) < 334.9)


def _samples():
  return json.loads(gzip.decompress((Path(__file__).parent / 'testdata/ford_mache_lead_departure.json.gz').read_bytes()))['frames']


def _motion_payloads(payloads, sample):
  for service in ('carState', 'radarState', 'modelV2'):
    event = messaging.new_message(service)
    setattr(event, service, payloads[service])
    value = getattr(event, service)
    if service == 'carState':
      value.vEgo = sample['car']['vEgo']
      value.vEgoRaw = 0.
      value.gearShifter = 'drive'
    elif service == 'radarState':
      for lead, values in zip((value.leadOne, value.leadTwo), sample['leads'], strict=True):
        for key in ('dRel', 'yRel', 'vRel', 'vLead', 'vLeadK', 'aLeadK', 'present', 'aLeadTau', 'modelProb', 'radar', 'radarTrackId'):
          setattr(lead, key, values[key])
    else:
      value.position.x, value.position.y = sample['model']['position']['x'], sample['model']['position']['y']
      value.velocity.x = sample['model']['velocity']['x']
      value.action.shouldStop = sample['model']['action']['shouldStop']
      value.action.desiredAcceleration = sample['model']['action']['desiredAcceleration']
      leads = value.init('leadsV3', len(sample['model']['leadsV3']))
      for lead, values in zip(leads, sample['model']['leadsV3'], strict=True):
        lead.from_dict(values)
    payloads[service] = value
  return payloads


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_recorded_departure_with_real_cem_stop_hold(ford_joined, monkeypatch, topology):
  # Stop acquisition and clocks use the existing caller fixture. Departure
  # geometry, filtered speed and radar values are recorded; MCU timing is synthetic.
  samples = [sample for sample in _samples() if 334.29 <= sample['routeSeconds'] <= 334.95]
  case = green.CemGreenCallerTests(methodName='runTest')
  case.setUp()
  case.cp = ford_joined.host.CP
  case.planner = LongitudinalPlanner(case.cp, init_v=4.27)
  endpoint, safety = ford_joined.native, ford_joined.safety
  endpoint.arm()
  endpoint.pump(10, main=5, mask=3)
  controller = LongControl(case.cp)
  try:
    case.prime()
    original = green.serialized_scene
    current = [None]
    monkeypatch.setattr(green, 'serialized_scene', lambda **kwargs: _motion_payloads(original(**kwargs), current[0]))
    observations = []
    actual_sample = StoppedLeadRelease.sample
    def observed(owner, sm, cp, planner, detector, **kwargs):
      target = float(planner.output_a_target)
      released = actual_sample(owner, sm, cp, planner, detector, **kwargs)
      observations.append((released, target))
      return released
    monkeypatch.setattr(StoppedLeadRelease, 'sample', observed)
    report, accepted = [], 0
    for sample in samples:
      current[0] = sample
      observation_count = len(observations)
      _, _, held, plan = case.cycle(stopped=True, position=sample['model']['position']['x'],
        velocity=sample['model']['velocity']['x'], should_stop=sample['model']['action']['shouldStop'],
        desired_accel=sample['model']['action']['desiredAcceleration'], lead=True)
      report.append((bool(held), bool(plan.shouldStop), float(plan.aTarget)))
      if len(observations) > observation_count and observations[-1][0]:
        assert float(plan.aTarget) == pytest.approx(observations[-1][1], abs=1e-6), 'Release changed native acceleration'
      state = structs.CarState(canValid=True, standstill=True, vEgo=sample['car']['vEgo'], gearShifter='drive')
      for _ in range(5):
        output = controller.update(True, state, float(plan.aTarget), bool(plan.shouldStop), (-3.5, 2.))
        endpoint.pump(1, main=5, mask=3, speed=0.)
        stopping = controller.long_control_state == structs.CarControl.Actuators.LongControlState.stopping
        address, data, bus = fordcan.create_acc_msg(endpoint.packer, ford_joined.host.CI.CC.CAN, True,
          -5. if output < 0. else float(output), float(output), stopping, bool(output < 0.), v_ego_kph=80.)
        assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus - ford_joined.offset, data))
        accepted += 1
    assert any(not held and not stopping and target > 0 for held, stopping, target in report[-3:]), report
    assert accepted == len(samples) * 5
  finally:
    case.doCleanups()


DEFECTS = ('none', 'door', 'belt', 'regen', 'acc-fault', 'brake', 'gas', 'moving-wheel', 'reverse', 'can', 'sensor', 'off', 'force-decel', 'model-stop',
           'model-velocity', 'model-path', 'near-lead', 'stationary-lead', 'second-stopped', 'vision-only',
           'uncertain-lead', 'sign', 'unknown-hold', 'native-braking', 'native-stop', 'solver', 'stale', 'invalid',
           'track-changes', 'drive-changes')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [True])
@pytest.mark.parametrize('defect', DEFECTS)
def test_stationary_release_requires_corroborated_continuous_evidence(ford_joined, defect):
  motion = next(sample for sample in _samples() if 334.44 < sample['routeSeconds'] < 334.48)
  owner = StoppedLeadRelease()
  detector = SimpleNamespace(committed=True, standstill_committed=True, standstill_reason='light', commit_distance_m=3.)
  planner = SimpleNamespace(mpc=SimpleNamespace(solution_status=0), output_should_stop=False, output_a_target=.5,
                            force_stop_plan=SimpleNamespace(forcing=False, should_stop=False))
  results = []
  for tick in range(13):
    now = green.MONO + 1_000_000_000 + tick * 50_000_000
    payloads = _motion_payloads(green.serialized_scene(stamp=green.BOOT + now - green.MONO - 2_000_000,
      speed=0., standstill=True), motion)
    for service in ('carControl', 'controlsState'):
      event = messaging.new_message(service)
      setattr(event, service, payloads[service])
      payloads[service] = getattr(event, service)
    sm = green.CallerFrame(payloads, now - 2_000_000)
    car, lead = sm['carState'], sm['radarState'].leadOne
    drive = green.MONO - 1_000_000_000
    if defect in ('door', 'belt', 'regen', 'acc-fault', 'brake', 'gas', 'moving-wheel', 'reverse', 'can', 'sensor'):
      field, value = {'door': ('doorOpen', True), 'belt': ('seatbeltUnlatched', True),
        'regen': ('regenBraking', True), 'acc-fault': ('accFaulted', True), 'brake': ('brakePressed', True),
        'gas': ('gasPressed', True), 'moving-wheel': ('vEgoRaw', .01),
        'reverse': ('gearShifter', 'reverse'), 'can': ('canValid', False), 'sensor': ('vehicleSensorsInvalid', True)}[defect]
      setattr(car, field, value)
    elif defect == 'off':
      sm['carControl'].longActive = False
    elif defect == 'force-decel':
      sm['controlsState'].forceDecel = True
    elif defect == 'model-stop':
      sm['modelV2'].action.shouldStop = True
    elif defect == 'model-velocity':
      sm['modelV2'].velocity.x = [0.] * 33
    elif defect == 'model-path':
      sm['modelV2'].position.x = [600.] * 33
    elif defect in ('near-lead', 'stationary-lead', 'vision-only', 'uncertain-lead', 'track-changes'):
      field, value = {'near-lead': ('dRel', 2.), 'stationary-lead': ('vLead', 0.), 'vision-only': ('radar', False),
        'uncertain-lead': ('modelProb', .5), 'track-changes': ('radarTrackId', tick)}[defect]
      setattr(lead, field, value)
    elif defect == 'second-stopped':
      sm['radarState'].leadTwo.vLead = 0.
    elif defect in ('sign', 'unknown-hold'):
      detector.standstill_reason = 'sign' if defect == 'sign' else None
    elif defect == 'native-braking':
      planner.output_a_target = -.1
    elif defect == 'native-stop':
      planner.output_should_stop = True
    elif defect == 'solver':
      planner.mpc.solution_status = 1
    elif defect == 'stale':
      sm.logMonoTime['radarState'] = now - 150_000_001
    elif defect == 'invalid':
      sm.valid['modelV2'] = False
    elif defect == 'drive-changes':
      drive -= tick
    results.append(owner.sample(sm, ford_joined.host.CP, planner, detector, now_ns=now, drive_id=drive))
    assert planner.output_a_target == (-.1 if defect == 'native-braking' else .5)
  assert any(results) == (defect == 'none'), (defect, results)
