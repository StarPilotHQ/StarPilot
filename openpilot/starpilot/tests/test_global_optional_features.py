"""Generic admission reaches real Toyota hosts and native planner application."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.common.params import Params
from openpilot.selfdrive.controls import plannerd
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.tests.test_curve_runtime_loop import bus, NOW
from openpilot.starpilot.curve_speed.host import CurveHost
from openpilot.starpilot.feature_runtime import enabled, slc_runtime_settings, vision_control_enabled
from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling
from openpilot.starpilot.longitudinal.profile_document import default_personality_profiles, profile_document
from openpilot.starpilot.longitudinal.profile_runtime import ProfileHost
from openpilot.starpilot.speed_limits.acceptance import Authority, LongitudinalOwner, Mode
from openpilot.starpilot.speed_limits.selection import Source
from openpilot.starpilot.speed_limits.vision_gate import diagnostic_choice_enabled
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner


def toyota():
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
  assert cp.openpilotLongitudinalControl and cp.pcmCruise
  return cp


@pytest.mark.parametrize('brand', ['toyota', 'honda', 'hyundai', 'subaru', 'ford', 'volkswagen', 'mazda', 'nissan', 'tesla', 'rivian', 'future'])
def test_generic_admission_is_brand_independent_and_stock_is_display_only(tmp_path, brand):
  params = Params(str(tmp_path))
  params.put_bool('CurveSpeedController', True, block=True)
  params.put_bool('SpeedLimitController', True, block=True)
  cp = NS(brand=brand, carFingerprint=brand + '_native', openpilotLongitudinalControl=True,
          pcmCruise=True, passive=False, dashcamOnly=False, notCar=False)
  for feature in ('conditional', 'curve', 'profile', 'slc', 'vision'):
    assert enabled(params, cp, feature, {})
  assert diagnostic_choice_enabled({}, cp)
  assert vision_control_enabled(params, cp)
  cp.openpilotLongitudinalControl = False
  for feature in ('conditional', 'curve', 'profile'):
    assert not enabled(params, cp, feature, {})
  settings = slc_runtime_settings(params, cp, {})
  assert settings.display and not settings.enabled and settings.acceptance.display_only
  assert enabled(params, cp, 'slc', {}) and enabled(params, cp, 'vision', {})
  assert not vision_control_enabled(params, cp)
  for field in ('passive', 'dashcamOnly', 'notCar'):
    setattr(cp, field, True)
    for feature in ('conditional', 'curve', 'profile', 'slc', 'vision'):
      assert not enabled(params, cp, feature, {})
    setattr(cp, field, False)


def test_factory_defaults_and_hidden_vision_control_producer(tmp_path):
  params, cp = Params(str(tmp_path)), toyota()
  settings = slc_runtime_settings(params, cp, {})
  assert settings.selection.slots == (Source.VISION, Source.MAP, None)
  assert not settings.enabled and settings.display
  assert enabled(params, cp, 'conditional', {})
  assert enabled(params, cp, 'slc', {}) and enabled(params, cp, 'vision', {})
  assert not enabled(params, cp, 'curve', {})
  params.put_bool('ShowSpeedLimits', False, block=True)
  params.put_bool('SpeedLimitController', True, block=True)
  assert enabled(params, cp, 'vision', {}) and vision_control_enabled(params, cp)


def test_toyota_actual_default_plannerd_constructs_requested_hosts(tmp_path):
  params, cp = Params(str(tmp_path)), toyota()
  params.put('CarParams', cp.to_bytes(), block=True)
  params.put_bool('CurveSpeedController', True, block=True)
  class EndLoop(Exception):
    pass
  sm = Mock()
  sm.update.side_effect = EndLoop
  with (patch.object(plannerd, 'Params', return_value=params),
        patch('openpilot.common.params.Params', return_value=params),
        patch.object(plannerd, 'config_realtime_process'), patch.object(plannerd, 'set_core_affinity'),
        patch.object(plannerd.messaging, 'sub_sock'),
        patch.object(plannerd.messaging, 'SubMaster', return_value=sm) as subscriber,
        patch.object(plannerd.messaging, 'PubMaster') as publisher,
        patch.object(plannerd, 'ConditionalPlannerHost', wraps=plannerd.ConditionalPlannerHost) as conditional,
        patch.object(plannerd, 'ProfileHost', wraps=ProfileHost) as profiles,
        patch.object(plannerd, 'CurveHost', wraps=CurveHost),
        patch.dict('os.environ', {}, clear=True)):
    with pytest.raises(EndLoop):
      plannerd.main()
    conditional.assert_called_once()
    profiles.assert_called_once()
    assert 'slcState' in publisher.call_args.args[0]
    assert 'starpilotLongitudinalPlan' in publisher.call_args.args[0]
    for source in ('slcDashboardObservation', 'slcVisionObservation', 'mapdOut'):
      assert source in subscriber.call_args.args[0]
  assert plannerd.SavedSlcLifecycle(params, cp, {}).refresh(NOW)[0] is not None


def test_toyota_curve_host_cap_reaches_actual_native_mpc_and_inactive_axis_denies_it():
  cp, host = toyota(), CurveHost(enabled=True, replay=True)
  planner = LongitudinalPlanner(cp, init_v=25.0)
  applied = []
  for tick in range(1, 31):
    stamp = NOW + tick * 50_000_000
    sm = bus(stamp)
    result = plannerd.update_curve_frame(planner, sm, cp, stamp, host=host)
    assert result.ceiling_mps is not None
    applied.append(planner.last_curve_ceiling_applied)
  assert any(applied)
  sm.messages['carControl'] = sm.messages['carControl'].as_builder()
  sm.messages['carControl'].longActive = False
  result = plannerd.update_curve_frame(planner, sm, cp, stamp, host=host)
  assert result.ceiling_mps is None and not planner.last_curve_ceiling_applied


def test_toyota_profile_host_and_slc_ceiling_reach_actual_native_planner(tmp_path):
  params, cp = Params(str(tmp_path)), toyota()
  params.put_bool('CustomPersonalities', True, block=True)
  params.put('LongitudinalPersonalityProfiles', profile_document(default_personality_profiles(True), enabled=True), block=True)
  host = ProfileHost(params)
  planner = LongitudinalPlanner(cp, init_v=25.0)
  ceiling = CruiseCeiling(10.0, Authority(Mode.LONGITUDINAL_ONLY, LongitudinalOwner.SYSTEM, False, True, False, False))
  sm = bus(NOW)
  target = plannerd.profile_for_frame(host, sm, cp, NOW)
  assert target is not None
  planner.update(sm, profile_tuning=target, cruise_ceiling=ceiling, now_ns=NOW)
  assert planner.last_profile is not None
  assert planner.last_cruise_ceiling_status == 'applied'
  sm.messages['carControl'] = sm.messages['carControl'].as_builder()
  sm.messages['carControl'].longActive = False
  planner.update(sm, profile_tuning=target, now_ns=NOW)
  assert planner.last_profile is None
  owner = FeatureSettingsOwner(params, lambda group: True, vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
  assert owner.long_profiles.capability() is not None


@pytest.fixture
def mode_transport():
  from openpilot.starpilot.conditional_mode.tests import test_status_consumer as fixture
  with patch.object(fixture, 'CP', toyota()):
    case = fixture.TestModeTransport('test_actual_selfdrived_owner_preserves_stock_and_separates_axes')
    case.setUp()
    try:
      yield case
    finally:
      case.doCleanups()


def test_toyota_actual_selfdrived_acceptance_keeps_current_axis_and_freshness(mode_transport):
  from openpilot.cereal import messaging
  from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
  from openpilot.starpilot.conditional_mode.policy import ManualIntent
  from openpilot.starpilot.conditional_mode.tests.test_projection import MONO, BOOT
  case = mode_transport
  drive = SelfdriveD.__new__(SelfdriveD)
  drive.conditional_replay = False
  drive.requested_experimental_mode = True
  drive.update_conditional_mode(None)
  case.assertTrue(drive.experimental_mode)
  case.params.put_bool('ExperimentalMode', True, block=True)
  case.owner.refresh(MONO, force=True)
  drive.conditional_replay = True
  drive.conditional_settings = case.owner
  drive.conditional_consumer = case.consumer
  drive.conditional_car_state_valid = True
  drive.initialized = True
  drive.enabled = True
  drive.aol_replay = True
  axes = NS(longitudinal_active=True, lateral_active=False)
  case.enterContext(patch.object(drive, "aol_axis_decision", axes, create=True))
  drive.CP = toyota()
  for index, intent in enumerate((ManualIntent.FORCE_CHILL, ManualIntent.FORCE_EXPERIMENTAL), start=1):
    now = MONO + index * 50_000_000
    event, _, sm = case.produce(now, intent=intent)
    device = messaging.new_message('deviceState', valid=True)
    device.deviceState.started = True
    device.deviceState.startedMonoTime = case.drive_id
    sm.payloads['deviceState'] = messaging.log_from_bytes(device.to_bytes()).deviceState
    sm.payloads['slcState'] = messaging.log_from_bytes(event.as_reader().as_builder().to_bytes()).slcState
    for service in ('deviceState', 'slcState'):
      sm.logMonoTime[service] = now
      sm.recv_time[service] = now / 1e9
      sm.seen[service] = sm.alive[service] = sm.valid[service] = True
    sm.valid['slcState'] = False  # Unavailable SLC does not invalidate this nested proposal.
    drive.sm = sm
    drive.aol_car_state_log_ns = now - 2_000_000
    with patch('openpilot.selfdrive.selfdrived.selfdrived.paired_clocks_ns', return_value=(now, BOOT + now - MONO, 1000)):
      drive.update_conditional_mode(sm['carState'])
      case.assertEqual(drive.experimental_mode, intent is ManualIntent.FORCE_EXPERIMENTAL)
      axes.longitudinal_active = False
      drive.update_conditional_mode(sm['carState'])
      case.assertTrue(drive.experimental_mode)  # Exact existing stock request; no conditional override.
      axes.longitudinal_active = True


def test_toyota_actual_conditional_planner_host_uses_native_mpc_and_adjacent_radar():
  # Reuse the serialized fresh-source integration trace with Toyota's unchanged
  # native CarParams, including pcmCruise=True, rather than a modified CP stub.
  from openpilot.selfdrive.controls.tests import test_conditional_runtime_loop as fixture
  case = fixture.TestConditionalRuntimeLoop('test_automatic_chill_ignores_retired_slc_modes_but_keeps_native_radar_veto')
  with patch.object(fixture, 'CarInterface', NS(get_non_essential_params=lambda candidate: toyota())):
    case.test_automatic_chill_ignores_retired_slc_modes_but_keeps_native_radar_veto()


def test_toyota_galaxy_long_authority_and_native_curve_rows(tmp_path):
  from openpilot.starpilot.galaxy.settings import AuthorityContext, _qualified
  cp = toyota()
  assert _qualified(AuthorityContext(parked=True, cp=cp, cp_raw=cp.to_bytes()), 'long')
  params = Params(str(tmp_path))
  owner = FeatureSettingsOwner(params, lambda group: True, vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
  snapshot = owner.snapshot('curve', parked=True, system_long=True, lateral_context=True, metric=True)
  master = next(row for row in snapshot.rows if row.key == 'CurveSpeedController')
  assert master.available and 'not supported' not in master.reason


def test_invalid_saved_sources_and_explicit_replay_optins(tmp_path):
  from pathlib import Path
  params, cp = Params(str(tmp_path)), toyota()
  for key, value, feature in (('ConditionalModeConfig', b'{bad', 'conditional'),
                              ('CurveSpeedController', b'true', 'curve'),
                              ('LongitudinalPersonalityProfiles', b'{bad', 'profile')):
    Path(params.get_param_path(key)).write_bytes(value)
    assert not enabled(params, cp, feature, {})
  for feature, flag in (('conditional', 'CONDITIONAL_MODE_REPLAY_RUNTIME'),
                        ('curve', 'CURVE_REPLAY_RUNTIME'), ('profile', 'LONG_PLANNER_REPLAY_RUNTIME'),
                        ('slc', 'SLC_REPLAY_RUNTIME')):
    assert enabled(params, None, feature, {flag: '1'})
  assert enabled(params, None, 'vision', {'SLC_REPLAY_RUNTIME': '1', 'SLC_VISION_DEVELOPMENT': '1'})
  assert not enabled(params, None, 'vision', {'SLC_VISION_DEVELOPMENT': '1'})
