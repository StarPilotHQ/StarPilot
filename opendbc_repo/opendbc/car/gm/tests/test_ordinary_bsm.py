import pytest

from opendbc.car import structs
from opendbc.car.gm.aol import qualified_gm
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.tests.test_bolt_cc import native
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.car.gm.values import (DBC, GMFlags, ORDINARY_ASCM_CAR, ORDINARY_SDGM_CAR,
                                  is_ordinary_ascm_profile, is_ordinary_sdgm_profile)
from openpilot.starpilot.aol.tests import test_gm_startup_continuity as startup


CARS = sorted(ORDINARY_ASCM_CAR | ORDINARY_SDGM_CAR)


@pytest.mark.parametrize('candidate', CARS)
def test_bsm_preserves_exact_control_owners(candidate):
  profile = is_ordinary_sdgm_profile if candidate in ORDINARY_SDGM_CAR else is_ordinary_ascm_profile
  for alpha in (False, True):
    for release in (False, True):
      for accelerator in (False, True):
        for radar in (False, True):
          cp = params(candidate, sascm=True, accelerator=accelerator, radar=radar, alpha=alpha, release=release)
          word = cp.safetyConfigs[0].safetyParam
          cp.flags |= int(GMFlags.HAS_BSM)
          longitudinal = alpha and not release
          assert profile(cp, longitudinal=longitudinal)
          assert qualified_gm(cp)
          assert cp.safetyConfigs[0].safetyParam == word
          assert not profile(cp, longitudinal=not longitudinal)
          for bit in (1, 2, 4, 8, 32, 1 << 30):
            denied = cp.as_reader().as_builder()
            denied.flags |= bit
            assert not profile(denied, longitudinal=longitudinal)
          cp.safetyConfigs[0].safetyParam |= 4
          assert not profile(cp, longitudinal=longitudinal)


@pytest.mark.parametrize('candidate', CARS)
def test_bsm_startup_replaces_all_acc_messages_on_correct_buses(candidate):
  cp = params(candidate, sascm=True, alpha=True)
  cp.flags |= int(GMFlags.HAS_BSM)
  controller = CarController(DBC[candidate], cp)
  assert controller.startup_acc_keepalive
  assert controller.ordinary_sdgm_long == (candidate in ORDINARY_SDGM_CAR)
  state = CarState(cp)
  state.pscm_status = dict.fromkeys(('HandsOffSWDetectionMode', 'HandsOffSWlDetectionStatus',
                                    'LKATorqueDeliveredStatus', 'LKADriverAppldTrq', 'LKATorqueDelivered',
                                    'LKATotalTorqueDelivered', 'RollingCounter', 'PSCMStatusChecksum'), 0)
  state.out = structs.CarState.new_message(canValid=True).as_reader()
  control = structs.CarControl.new_message(enabled=True, latActive=True, longActive=True)
  control.actuators.accel = 1.
  control.actuators.torque = 1.
  _, frames = controller.update(control.as_reader(), state, 1_000_000_000, startup_keepalive=True)
  safety = libsafety_py.libsafety
  safety.init_tests()
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  for message in frames:
    if message[0] in (0x2CB, 0x315, 0x370):
      assert native('tx', message, 1_000_000), message
  expected_brake_bus = 2 if candidate in ORDINARY_SDGM_CAR else 0
  assert {(0x2CB, 0), (0x315, expected_brake_bus), (0x370, 0)} <= {(a, b) for a, _, b in frames}
  assert not any(a == 0x315 and b != expected_brake_bus for a, _, b in frames)
  startup.test_initializing_gm_output_is_inactive_and_profile_complete(cp)
