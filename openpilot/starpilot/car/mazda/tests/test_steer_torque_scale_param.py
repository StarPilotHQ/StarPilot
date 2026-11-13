"""MazdaSteerTorqueScale is a registered Param, read through the real params library.

Runs in a temporary OpenpilotPrefix, so it never touches a saved calibration (host or device).
Needs the host native extensions (libparams_c): run with ./dev pytest.
"""
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from opendbc.car.mazda.interface import STEER_TORQUE_SCALE_PARAM, steer_torque_scale


def test_unset_saved_and_out_of_range_values():
  with OpenpilotPrefix():
    params = Params()
    assert params.get(STEER_TORQUE_SCALE_PARAM) is None
    assert params.get(STEER_TORQUE_SCALE_PARAM, return_default=True) == 1.0
    assert steer_torque_scale() == 1.0
    params.put(STEER_TORQUE_SCALE_PARAM, 1.2, block=True)
    assert steer_torque_scale() == 1.2
    params.put(STEER_TORQUE_SCALE_PARAM, 2.0, block=True)
    assert steer_torque_scale() == 1.0


def test_a_saved_calibration_outside_the_test_is_untouched():
  # Review finding: the test must not delete a real calibration. Seed one in an outer prefix
  # (standing in for the user's store) and check the test leaves it as it was.
  with OpenpilotPrefix():
    Params().put(STEER_TORQUE_SCALE_PARAM, 1.3, block=True)
    test_unset_saved_and_out_of_range_values()
    assert Params().get(STEER_TORQUE_SCALE_PARAM) == 1.3
    assert steer_torque_scale() == 1.3
