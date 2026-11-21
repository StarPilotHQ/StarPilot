from opendbc.car.hyundai import software_cruise
from opendbc.car.hyundai.non_scc_aol import aol_word, NON_SCC_IDS
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.starpilot.car.hyundai.aol import native_accepts_cp, native_profile_supported, policy_for
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


def test_default_off_is_byte_identical_and_selected_is_only_a_button_profile():
  assert not VehicleStartupPreferences().hyundai_software_cruise
  for candidate in NON_SCC_IDS:
    cp = CarInterface.get_non_essential_params(candidate)
    before = cp.to_dict()
    assert software_cruise.eligible(cp), candidate
    assert not software_cruise.selected(cp)
    assert software_cruise.prepare(cp, False).to_dict() == before
    assert not VCruiseHelper(cp).hyundai_software_cruise
    software_cruise.prepare(cp, True)
    assert software_cruise.selected(cp)
    assert VCruiseHelper(cp).hyundai_software_cruise
    assert cp.pcmCruise and not cp.openpilotLongitudinalControl
    assert cp.safetyConfigs[0].safetyParam == before["safetyConfigs"][0]["safetyParam"] | 0x20
    policy = policy_for(cp)
    cp.alternativeExperience |= policy.alternative_experience_addition
    cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
    assert cp.safetyConfigs[0].safetyParam == aol_word(cp)
    config = cp.safetyConfigs[0]
    assert native_profile_supported(config.safetyModel.raw, config.safetyParam)
    assert native_accepts_cp(cp, config.safetyModel.raw, config.safetyParam)
    assert cp.safetyConfigs[0].safetyParam & 0x420 == 0x420
    assert software_cruise.selected(cp)


def test_scc_ray_and_passive_profiles_are_excluded_even_with_explicit_opt_in():
  for candidate in (CAR.KIA_RAY_EV, CAR.HYUNDAI_KONA_2ND_GEN, CAR.HYUNDAI_IONIQ_6):
    cp = CarInterface.get_non_essential_params(candidate)
    before = cp.to_dict()
    assert not software_cruise.eligible(cp)
    assert software_cruise.prepare(cp, True).to_dict() == before
  cp = CarInterface.get_non_essential_params(CAR.KIA_FORTE_2019_NON_SCC)
  cp.passive = True
  assert not software_cruise.eligible(cp)


def test_actual_forte_controller_keeps_legacy_button_burst_and_off_is_silent():
  from opendbc.car.structs import CarControl

  cp = CarInterface.get_non_essential_params(CAR.KIA_FORTE_2019_NON_SCC)
  off = CarInterface(cp)
  on = CarInterface(software_cruise.prepare(cp.as_reader().as_builder(), True))
  control = CarControl(enabled=True).as_reader()
  for interface in (off, on):
    interface.update([])
  for frame in range(1, 31):
    stamp = 1_000_000_000 + frame * 10_000_000
    for interface in (off, on):
      interface.CC.software_cruise_command = 2
      interface.CC.software_cruise_command_ns = stamp
    _, off_frames = off.apply(control, stamp)
    _, on_frames = on.apply(control, stamp)
    assert [message for message in on_frames if message[0] != 0x4F1] == off_frames
    button_frames = [message for message in on_frames if message[0] == 0x4F1]
    assert bool(button_frames) == (11 <= frame - 1 <= 15 or 26 <= frame - 1 <= 30)
    assert all(bus == 0 and data[0] & 7 == 2 for _, data, bus in button_frames)
  on.CC.software_cruise_command_ns = stamp
  _, frames = on.apply(control, stamp + 10_000_000)
  assert all(address != 0x4F1 for address, _, _ in frames)


def test_button_only_limit_reaches_wire_but_cannot_enter_acceleration_planner():
  from openpilot.starpilot.longitudinal.cruise_ceiling import select_cruise_ceiling
  from openpilot.starpilot.speed_limits.acceptance import LongitudinalOwner
  from openpilot.starpilot.speed_limits.tests.test_core_flow import controller, tick
  from openpilot.starpilot.speed_limits.tests.test_runtime_replay import RuntimeReplayTests

  replay = RuntimeReplayTests()
  replay.setUp()
  sm = replay.sm
  cp = CarInterface.get_non_essential_params(CAR.KIA_FORTE_2019_NON_SCC)
  sm['selfdriveState'].enabled = sm['selfdriveState'].active = True
  sm['carControl'].longActive = False
  cs = sm['carState']
  cs.canValid = cs.cruiseState.available = cs.cruiseState.enabled = True
  cs.gearShifter = 'drive'
  sm['slcDashboardObservation'].speedMps = 18.
  assert tick(controller(), sm, cp, 0).result.ceiling is None
  software_cruise.prepare(cp, True)
  owner = controller()
  result = tick(owner, sm, cp, 1).result
  assert result.ceiling is not None
  assert result.ceiling.authority.owner is LongitudinalOwner.CRUISE_BUTTONS
  assert not result.ceiling.authority.longitudinal_active
  assert result.coordinate.cluster_target_mps == 18.
  ceiling = select_cruise_ceiling(result.ceiling, driver_v_cruise_kph=100., driver_cruise_mps=100. / 3.6,
                                 system_long_available=True, long_control_active=True, selfdrive_enabled=True, force_decel=False)
  assert ceiling.effective_mps is None
  for field in ('brakePressed', 'gasPressed', 'canTimeout'):
    setattr(cs, field, True)
    assert tick(owner, sm, cp, 2).result.ceiling is None
    setattr(cs, field, False)


def test_vehicle_setting_saves_only_with_confirmed_parked_authority(tmp_path):
  from dataclasses import replace

  from openpilot.common.params import Params
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
  from openpilot.starpilot.ui.feature_settings_state import row_change

  params = Params(str(tmp_path))
  cp = CarInterface.get_non_essential_params(CAR.KIA_FORTE_2019_NON_SCC)
  parked = [True]
  owner = FeatureSettingsOwner(params, lambda _: parked[0], vehicle_fingerprint=lambda: cp.carFingerprint,
                               vehicle_params=lambda: cp, configuration_vehicle=lambda: True)
  rows = owner.snapshot('vehicle', parked=True, system_long=False, lateral_context=True, metric=True).rows
  row = next(row for row in rows if row.key == 'RedneckCruise')
  assert row.label == 'Redneck Cruise' and row.default_value == row.value == 'Off'
  request = row_change(row)
  assert request is not None and not owner.apply(request)
  parked[0] = False
  assert not owner.apply(replace(request, confirmation=True))
  parked[0] = True
  assert owner.apply(replace(request, confirmation=True))
  assert params.get_bool('RedneckCruise')
  assert VehicleStartupPreferences.read(params, enabled=True).hyundai_software_cruise
  assert not software_cruise.selected(cp)
  cp = CarInterface.get_non_essential_params(CAR.HYUNDAI_KONA_2ND_GEN)
  assert not any(row.key == 'RedneckCruise' for row in owner.snapshot('vehicle', parked=True, system_long=True,
                                                                      lateral_context=True, metric=True).rows)


def test_fresh_limit_drives_only_set_button_and_renewal_requires_all_inputs():
  from types import SimpleNamespace
  from opendbc.car.structs import CarControl, CarState
  from openpilot.cereal import messaging
  from openpilot.starpilot.car.hyundai.redneck_cruise import RedneckCruise, update
  from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM, RuntimeReplayTests
  from openpilot.starpilot.speed_limits.tests.test_core_flow import controller, tick

  cp = CarInterface.get_non_essential_params(CAR.KIA_FORTE_2019_NON_SCC)
  software_cruise.prepare(cp, True)
  ci = CarInterface(cp)
  ci.update([])
  cs = CarState(canValid=True, vEgo=24., vEgoCluster=24., vCruise=100., vCruiseCluster=100., gearShifter='drive')
  cs.cruiseState.available = cs.cruiseState.enabled = True
  cs.cruiseState.speedCluster = 25.
  command = CarControl(enabled=True)
  command.actuators.accel = 1.
  source = RuntimeReplayTests()
  source.setUp()
  sm = source.sm
  sm.data['carState'] = cs
  sm.data['carControl'] = command
  sm['selfdriveState'].active = True
  sm['slcDashboardObservation'].speedMps = 18.
  runtime = controller()
  output = tick(runtime, sm, cp, 0)
  assert output.message.slcState.hasCeiling
  plan = messaging.new_message('longitudinalPlan').longitudinalPlan
  plan.speeds = [22.] * 17
  services = {'carControl': command, 'selfdriveState': sm['selfdriveState'], 'longitudinalPlan': plan,
              'pandaStates': [SimpleNamespace(controlsAllowed=True, faults=[])], 'slcState': output.message.slcState}
  link = ReplaySM(services, 2_000_000_000)
  link.seen = dict(link.valid)
  link.recv_time = dict.fromkeys(services, 2.)
  card = SimpleNamespace(CI=ci, sm=link, startup_panda_configured=lambda: True, is_metric=True, slc_control_enabled=True,
    redneck_cruise=RedneckCruise(cp, SimpleNamespace(redneckCruiseAvailable=True, pcmCruiseSpeed=False)))
  buttons = []
  for cycle in range(15):
    now = 2_000_000_000 + cycle * 10_000_000
    if cycle % 5 == 0:
      output = tick(runtime, sm, cp, cycle // 5)
      services['slcState'] = output.message.slcState
      link.logMonoTime['slcState'] = now
      link.recv_time['slcState'] = now / 1e9
    for name in services.keys() - {'slcState'}:
      link.logMonoTime[name] = now
      link.recv_time[name] = now / 1e9
    update(card, cs, command, now)
    _, sends = ci.apply(command.as_reader(), now)
    buttons.extend(data[0] & 7 for address, data, _ in sends if address == 0x4F1)
  assert buttons and set(buttons) == {2}
  assert not command.longActive and cp.pcmCruise and not cp.openpilotLongitudinalControl
  cs.brakePressed = True
  update(card, cs, command, now)
  assert ci.CC.software_cruise_command == 0
  cs.brakePressed = False
  link.valid['longitudinalPlan'] = False
  update(card, cs, command, now)
  assert ci.CC.software_cruise_command == 0
