"""Generic lane preferences reach each native controller without granting an axis."""

import os
from unittest import mock

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.car_helpers import interfaces
from opendbc.car.ford.values import CAR as FORD
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.mazda.values import CAR as MAZDA
from opendbc.car.nissan.values import CAR as NISSAN
from opendbc.car.subaru.values import CAR as SUBARU
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.volkswagen.values import CAR as VOLKSWAGEN
from opendbc.car.volkswagen.values import NetworkLocation
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.feature_capabilities import lateral_supported
from openpilot.starpilot.lateral.lane_runtime import runtime_supported
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed, publish_corolla_params


GENERIC_STEERING = (
  HONDA.HONDA_CIVIC,
  MAZDA.MAZDA_CX5,
  MAZDA.MAZDA_CX5_2022,
  SUBARU.SUBARU_IMPREZA,
  FORD.FORD_ESCAPE_MK4,
  NISSAN.NISSAN_XTRAIL,
  VOLKSWAGEN.VOLKSWAGEN_GOLF_MK7,
  VOLKSWAGEN.VOLKSWAGEN_ID4_MK1,
)


def native_steering_params(identity):
  if identity == VOLKSWAGEN.VOLKSWAGEN_ID4_MK1:
    fingerprint = gen_empty_fingerprint()
    fingerprint[1][0x13D] = 32  # QFK_01 on the gateway side, as in the native MEB interface tests.
    cp = interfaces[identity].get_params(identity, fingerprint, [], False, False, False)
    assert cp.networkLocation == NetworkLocation.gateway
    assert not cp.dashcamOnly
    return cp
  return interfaces[identity].get_non_essential_params(identity)


@pytest.mark.parametrize('identity', GENERIC_STEERING)
def test_actual_controller_paths_admit_generic_lane_host_and_keep_original_output_when_off(identity):
  with OpenpilotPrefix(), mock.patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1',
       'LANE_CENTERING_REPLAY_RUNTIME': '0', 'AOL_REPLAY_RUNTIME': '0'}), \
       mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
    params = Params()
    cp = native_steering_params(identity)
    assert lateral_supported(cp)
    params.put('CarParams', cp.to_bytes(), block=True)
    candidate, reference = Controls(), Controls()
    assert candidate.lane_centering_host is not None
    reference.lane_centering_host = None
    for tick in range(20):
      now = 1_000_000_000 + tick * 10_000_000
      feed(candidate, now, tick)
      feed(reference, now, tick)
      actual, _ = candidate.state_control()
      expected, _ = reference.state_control()
      assert actual.to_dict() == expected.to_dict()


@pytest.mark.parametrize('identity', GENERIC_STEERING)
def test_actual_controller_paths_apply_generic_lane_curvature_then_withdraw_for_fault(identity):
  with OpenpilotPrefix(), mock.patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1',
       'LANE_CENTERING_REPLAY_RUNTIME': '0', 'AOL_REPLAY_RUNTIME': '0'}), \
       mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
    params = Params()
    cp = native_steering_params(identity)
    params.put('CarParams', cp.to_bytes(), block=True)
    params.put_bool('LaneCentering', True, block=True)
    params.put('LaneCenteringE2EAuthority', 0., block=True)
    params.put('LaneCenterOffset', .2, block=True)
    controls = Controls()
    assert controls.lane_centering_host is not None
    for tick in range(60):
      now = 2_000_000_000 + tick * 10_000_000
      feed(controls, now, tick)
      command, _ = controls.state_control()
    assert command.latActive
    assert controls.last_lane_centering_result.correction > 0.
    assert controls.lane_centering_applied > 0.
    for tick in range(60, 65):
      now = 2_000_000_000 + tick * 10_000_000
      feed(controls, now, tick, fault=True)
      command, _ = controls.state_control()
      assert not command.latActive
      assert controls.lane_centering_applied == 0.


def test_toyota_full_card_startup_admits_normal_lane_host_without_replay_opt_in():
  with OpenpilotPrefix(), mock.patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1',
       'LANE_CENTERING_REPLAY_RUNTIME': '0', 'AOL_REPLAY_RUNTIME': '0'}), \
       mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
    params = Params()
    cp = publish_corolla_params(params)
    assert lateral_supported(cp)
    params.put('CarParams', cp.to_bytes(), block=True)
    controls = Controls()
    assert controls.lane_centering_host is not None


@pytest.mark.parametrize('identity', GENERIC_STEERING)
def test_actual_controller_paths_track_one_current_line_and_withdraw_for_fault(identity):
  from openpilot.starpilot.lateral.tests.test_lane_centering import single
  with OpenpilotPrefix(), mock.patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1',
       'LANE_CENTERING_REPLAY_RUNTIME': '0', 'AOL_REPLAY_RUNTIME': '0'}), \
       mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
    params = Params()
    cp = native_steering_params(identity)
    params.put('CarParams', cp.to_bytes(), block=True)
    params.put_bool('LaneCentering', True, block=True)
    params.put('LaneCenteringE2EAuthority', 0., block=True)
    controls = Controls()
    for tick in range(20):
      feed(controls, 2_000_000_000 + tick * 10_000_000, tick)
      controls.state_control()
    with mock.patch('openpilot.starpilot.lateral.tests.test_lane_runtime.model',
                    side_effect=lambda:single(line=-1.2, edge=10.)):
      for tick in range(20, 90):
        feed(controls, 2_000_000_000 + tick * 10_000_000, tick)
        command, _ = controls.state_control()
      assert command.latActive
      assert controls.last_lane_centering_result.correction > 0.
      assert controls.lane_centering_applied > 0.
      feed(controls, 2_900_000_000, 90, fault=True)
      command, _ = controls.state_control()
      assert not command.latActive
      assert controls.lane_centering_applied == 0.


def test_actual_secoc_configuration_requires_its_key_without_affecting_nonsecoc():
  cp = interfaces[TOYOTA.TOYOTA_SIENNA_4TH_GEN].get_non_essential_params(TOYOTA.TOYOTA_SIENNA_4TH_GEN)
  assert cp.secOcRequired and not cp.secOcKeyAvailable
  assert not lateral_supported(cp)
  cp.secOcKeyAvailable = True
  assert lateral_supported(cp)
  cp.dashcamOnly = True
  assert not lateral_supported(cp)


def test_generic_native_configuration_and_replay_opt_in_are_distinct():
  with mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '0'}):
    assert not runtime_supported(None)
    cp = interfaces[TOYOTA.TOYOTA_RAV4_TSS2].get_non_essential_params(TOYOTA.TOYOTA_RAV4_TSS2)
    assert runtime_supported(cp)
    for flag in ('notCar', 'dashcamOnly', 'passive'):
      bad = cp.as_reader().as_builder()
      setattr(bad, flag, True)
      assert not runtime_supported(bad)
    for safety_model in ('noOutput', 'silent', 'allOutput'):
      bad = cp.as_reader().as_builder()
      bad.safetyConfigs[0].safetyModel = safety_model
      assert not runtime_supported(bad)
    bad = cp.as_reader().as_builder()
    bad.safetyConfigs = []
    assert not runtime_supported(bad)
  with mock.patch.dict(os.environ, {'LANE_CENTERING_REPLAY_RUNTIME': '1'}):
    assert runtime_supported(None)


@pytest.mark.parametrize('identity', (MAZDA.MAZDA_CX9, VOLKSWAGEN.VOLKSWAGEN_ID4_MK1))
def test_actual_dashcam_configurations_never_create_lane_host(identity):
  with OpenpilotPrefix(), mock.patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1',
       'LANE_CENTERING_REPLAY_RUNTIME': '0', 'AOL_REPLAY_RUNTIME': '0'}), \
       mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
    params = Params()
    # The original empty-fingerprint configurations are real dashcam paths.
    cp = interfaces[identity].get_non_essential_params(identity)
    assert cp.dashcamOnly
    assert not lateral_supported(cp)
    assert not runtime_supported(cp)
    params.put('CarParams', cp.to_bytes(), block=True)
    params.put_bool('LaneCentering', True, block=True)
    controls = Controls()
    assert controls.CP.dashcamOnly
    assert controls.lane_centering_host is None
