import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.lateral import get_friction
from opendbc.car.toyota.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import (
  ControllerMode, DOCUMENT_KEY, LEARNING_OFF_KEY, default_selection, learning_allowed, policy_for, replace_mode,
)
from openpilot.starpilot.lateral.learning_preferences import KEY as LEARNING_KEY, read_preference, replace_preference
from openpilot.starpilot.lateral.rav4_tss2_policy import (
  supported_cp, friction_threshold, center_output_scale,
)
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.lateral.tests.test_torque_learning import learned_event
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change


def raw_saved(params, key):
  try:
    return Path(params.get_param_path(key)).read_bytes()
  except FileNotFoundError:
    return None


def factory(car=CAR.TOYOTA_RAV4_TSS2):
  return interfaces[car].get_non_essential_params(car)


def publish_rav4_params(params):
  """Publish RAV4 CP through Card's real startup configuration and finalization."""
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.car.toyota.aol import qualified
  params.put_bool("OpenpilotEnabledToggle", True, block=True)
  params.put_bool("SafeMode", False, block=True)
  cp = factory()

  def discover(*args, pre_create_hook, **kwargs):
    prepared = pre_create_hook(cp.as_reader().as_builder(), cp.carFingerprint, {}, [])
    return interfaces[cp.carFingerprint](prepared)

  with patch.dict(os.environ, {"SIMULATION": "1"}), \
       patch("openpilot.selfdrive.car.card.messaging.recv_one_retry", return_value=SimpleNamespace(can=[1])), \
       patch("openpilot.selfdrive.car.card.get_car", side_effect=discover):
    selected = Car()
  try:
    final = selected.CP.as_reader().as_builder()
    assert qualified(final, marked_only=True)
    assert final.lateralTuning.torque.to_dict() == cp.lateralTuning.torque.to_dict()
    for field in ("carFingerprint", "steerControlType", "mass", "wheelbase", "centerToFront", "steerRatio",
                  "tireStiffnessFront", "tireStiffnessRear", "steerActuatorDelay"):
      assert getattr(final, field) == getattr(cp, field)
    assert raw_saved(params, "CarParams") == final.to_bytes()
    return final
  finally:
    selected.vehicle_startup.close()


def controller(cp, mode=None):
  return LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), .01, controller_mode=mode)


@pytest.mark.parametrize("speed,increment", [(0., 0.), (2.5, 0.), (5., .04), (10., .065), (15., .05), (25., .025)])
def test_latest_cleanup_knots_taper_and_left_right_symmetry(speed, increment):
  # Frozen source-default center envelope, with both taper plateaus independently exercised.
  envelope = (1 / (1 + math.exp((speed - 13) / 3))) * (1 / (1 + math.exp(-.30 / .08)))
  assert friction_threshold(speed, 0, 0) == pytest.approx(.30 * (1 + .14 * envelope) + increment)
  for accel, jerk in ((.10, .10), (.40, .40), (.85, .75)):
    assert friction_threshold(speed, accel, jerk) == friction_threshold(speed, -accel, -jerk)
  for accel, jerk in ((.85, 0), (0, .75)):
    env = (1 / (1 + math.exp((speed - 13) / 3))) * (1 / (1 + math.exp((abs(accel) - .30) / .08)))
    assert friction_threshold(speed, accel, jerk) == pytest.approx(.30 * (1 + .14 * env))
  assert .80 <= center_output_scale(0, speed) <= 1


def test_exact_scope_excludes_related_racks_and_bad_tunes():
  cp = factory()
  assert supported_cp(cp)
  assert policy_for(cp) == "rav4_tss2"
  assert default_selection(cp).mode == ControllerMode.STARPILOT
  for car in (CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023, CAR.TOYOTA_RAV4_PRIME, CAR.TOYOTA_RAV4H):
    assert not supported_cp(factory(car))
  for field, value in (("brand", "hyundai"), ("notCar", True), ("passive", True), ("dashcamOnly", True),
                       ("steerControlType", structs.CarParams.SteerControlType.angle)):
    bad = cp.as_reader().as_builder()
    setattr(bad, field, value)
    assert not supported_cp(bad)
  for field, value in (("latAccelFactor", 0), ("latAccelFactor", float("nan")), ("friction", -.1),
                       ("latAccelOffset", float("inf"))):
    bad = cp.as_reader().as_builder()
    setattr(bad.lateralTuning.torque, field, value)
    assert not supported_cp(bad)


def test_actual_modern_owner_reaches_friction_and_final_taper_preserves_tune():
  cp = factory()
  shaped = controller(cp)
  assert type(selected_policy(shaped)).__name__ == "Rav4TSS2TorquePolicy"
  assert shaped.pid._k_i[1] == [.15]  # Modern control gains stay at the existing value.
  shaped.update_torque_parameters(3.2, .025, .08)
  assert shaped.torque_params.latAccelFactor == pytest.approx(3.2)
  assert shaped.torque_params.latAccelOffset == pytest.approx(.025)
  assert shaped.torque_params.friction == pytest.approx(.08)
  cs = SimpleNamespace(vEgo=10., steeringAngleDeg=0., steeringPressed=False)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0., roll=0.)
  vm = VehicleModel(cp)
  with patch("openpilot.selfdrive.controls.lib.latcontrol_torque.get_friction", wraps=get_friction) as friction, \
       patch.object(shaped.pid, "update", return_value=.4):
    for _ in range(300):
      result, _, state = shaped.update(True, cs, vm, params, False, .001, False, .14)
    assert state.desiredLateralAccel == pytest.approx(.1)
    assert state.desiredLateralJerk == pytest.approx(0., abs=1e-8)
    assert friction.call_args.args[2] == pytest.approx(friction_threshold(10., .1, 0.), abs=1e-8)
    raw = shaped.torque_from_lateral_accel(.4, shaped.torque_params)
    assert result == pytest.approx(-raw * center_output_scale(.1, 10.))
  standard = controller(cp, ControllerMode.STANDARD)
  assert selected_policy(standard) is None
  with patch("openpilot.selfdrive.controls.lib.latcontrol_torque.get_friction", wraps=get_friction) as friction, \
       patch.object(standard.pid, "update", return_value=.4):
    standard.update(True, cs, vm, params, False, .001, False, .14)
    assert friction.call_args.args[2] == .20


def test_actual_controls_saved_optout_and_native_learning_consumer():
  with OpenpilotPrefix(), patch.dict(os.environ, {"SIMULATION": "0", "REPLAY": "1", "TORQUE_REPLAY_RUNTIME": "0",
                                                "AOL_REPLAY_RUNTIME": "0", "LANE_CENTERING_REPLAY_RUNTIME": "0"}), \
       patch("openpilot.selfdrive.controls.controlsd.messaging.PubMaster"):
    saved = Params()
    cp = publish_rav4_params(saved)
    saved.put_bool("OpenpilotEnabledToggle", True, block=True)
    saved.put_bool("SafeMode", False, block=True)
    shaped = Controls()
    assert shaped.LaC.controller_mode == ControllerMode.STARPILOT
    assert shaped.torque_learning_allowed
    for tick in range(2):
      now = 1_000_000_000 + tick * 250_000_000
      feed(shaped, now, 0)
      shaped.sm.update_msgs(now / 1e9, [learned_event(cp, now)])
    assert shaped.sm.all_checks(["lateralTorqueParameters"])
    with patch.object(shaped.LaC, "update_torque_parameters") as update:
      command, _ = shaped.state_control()
      assert command.latActive
      update.assert_called_once()
    raw = replace_mode(None, cp, ControllerMode.STANDARD)
    saved.put(DOCUMENT_KEY, json.loads(raw), block=True)
    stored_raw = raw_saved(saved, DOCUMENT_KEY)
    assert json.loads(stored_raw) == json.loads(raw)
    assert shaped.LaC.controller_mode == ControllerMode.STARPILOT
    standard = Controls()
    assert standard.LaC.controller_mode == ControllerMode.STANDARD
    assert selected_policy(standard.LaC) is None
    assert raw_saved(saved, DOCUMENT_KEY) == stored_raw


@pytest.mark.parametrize("mode", [ControllerMode.STANDARD, ControllerMode.STARPILOT])
@pytest.mark.parametrize("saved_raw,allowed,label", [(None, True, "On"), (b"0", True, "On"),
                                                      (b"1", False, "Off"), (b"bad", False, "Invalid saved preference")])
def test_actual_controls_consumer_and_ui_row_honor_learning_choice(mode, saved_raw, allowed, label):
  with OpenpilotPrefix(), patch.dict(os.environ, {"SIMULATION": "0", "REPLAY": "1", "TORQUE_REPLAY_RUNTIME": "0",
                                                "AOL_REPLAY_RUNTIME": "0", "LANE_CENTERING_REPLAY_RUNTIME": "0"}), \
       patch("openpilot.selfdrive.controls.controlsd.messaging.PubMaster"):
    saved = Params()
    cp = publish_rav4_params(saved)
    saved.put(DOCUMENT_KEY, json.loads(replace_mode(None, cp, mode)), block=True)
    saved.put_bool("OpenpilotEnabledToggle", True, block=True)
    saved.put_bool("SafeMode", False, block=True)
    if saved_raw is not None:
      Path(saved.get_param_path(LEARNING_OFF_KEY)).write_bytes(saved_raw)
    controls = Controls()
    assert controls.LaC.controller_mode == mode
    assert controls.torque_learning_allowed == allowed
    owner = FeatureSettingsOwner(saved, lambda group: group == "preferences",
                                 vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp)
    snapshot = owner.snapshot("torque", parked=False, system_long=False, lateral_context=False, metric=False)
    row = next(row for row in snapshot.rows if row.key == LEARNING_OFF_KEY)
    assert row.value == label
    assert row.available == (saved_raw != b"bad")
    assert row.choices == (() if saved_raw == b"bad" else ("Off", "On"))
    for tick in range(2):
      now = 1_000_000_000 + tick * 250_000_000
      feed(controls, now, 0)
      controls.sm.update_msgs(now / 1e9, [learned_event(cp, now)])
    assert controls.sm.all_checks(["lateralTorqueParameters"])
    with patch.object(controls.LaC, "update_torque_parameters") as update:
      command, _ = controls.state_control()
      assert command.latActive
      assert update.call_count == int(allowed)
    assert raw_saved(saved, LEARNING_OFF_KEY) == saved_raw


@pytest.mark.parametrize("initial_mode", [ControllerMode.STANDARD, ControllerMode.STARPILOT])
def test_actual_ui_controller_switch_preserves_learning_and_explicit_toggle(initial_mode):
  with OpenpilotPrefix():
    cp = factory()
    saved = Params()
    unrelated_cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    unrelated_raw = replace_preference(None, unrelated_cp, False)
    saved.put(LEARNING_KEY, unrelated_raw, block=True)
    saved.put(DOCUMENT_KEY, json.loads(replace_mode(None, cp, initial_mode)), block=True)
    owner = FeatureSettingsOwner(saved, lambda group: group == "preferences",
                                 vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp)

    def row(key):
      state = owner.snapshot("torque", parked=False, system_long=False, lateral_context=False, metric=False)
      return next(row for row in state.rows if row.key == key)

    controller_row = row(DOCUMENT_KEY)
    request = row_change(controller_row)
    assert request is not None
    assert owner.apply(request)
    # RAV4's native-learning default remains enabled across both controller choices.
    assert raw_saved(saved, LEARNING_OFF_KEY) is None
    learning_row = row(LEARNING_OFF_KEY)
    assert learning_row.available and learning_row.value == "On"
    request = row_change(learning_row)
    assert request is not None
    assert owner.apply(request)
    assert raw_saved(saved, LEARNING_OFF_KEY) is None
    assert raw_saved(saved, LEARNING_KEY) == replace_preference(unrelated_raw, cp, False)
    preference = read_preference(saved, cp)
    assert preference.valid and not preference.enabled and preference.source == "scope"
    assert not learning_allowed(saved, cp)
    unrelated = read_preference(saved, unrelated_cp)
    assert unrelated.valid and not unrelated.enabled and unrelated.source == "scope"
    learning_row = row(LEARNING_OFF_KEY)
    assert learning_row.value == "Off" and learning_row.available
    request = row_change(learning_row)
    assert request is not None
    assert owner.apply(request)
    assert raw_saved(saved, LEARNING_OFF_KEY) is None
    assert raw_saved(saved, LEARNING_KEY) == replace_preference(unrelated_raw, cp, True)
    preference = read_preference(saved, cp)
    assert preference.valid and preference.enabled and preference.source == "scope"
    assert learning_allowed(saved, cp)
    unrelated = read_preference(saved, unrelated_cp)
    assert unrelated.valid and not unrelated.enabled and unrelated.source == "scope"


@pytest.mark.parametrize("mph", [0., 10., 15., 50., 60., 80.])
@pytest.mark.parametrize("accel", [0., .15, -.15, .7, -.7])
def test_low_speed_taper_preserves_other_speed_ranges(mph, accel):
  from openpilot.common.constants import CV
  from openpilot.starpilot.lateral.rav4_tss2_policy import center_envelope
  speed = mph * CV.MPH_TO_MS
  assert center_output_scale(accel, speed) == 1.0 - .12 * center_envelope(accel, speed)


@pytest.mark.parametrize("mph", [20., 27., 35., 40.])
def test_low_speed_center_taper_is_small_symmetric_and_fades_for_curves(mph):
  from openpilot.common.constants import CV
  from openpilot.starpilot.lateral.rav4_tss2_policy import center_envelope
  speed = mph * CV.MPH_TO_MS
  for accel in (0., .15, .30):
    previous = 1.0 - .12 * center_envelope(accel, speed)
    adjusted = center_output_scale(accel, speed)
    assert 0 < previous - adjusted <= .08
    assert .8 <= adjusted <= 1.
    assert adjusted == center_output_scale(-accel, speed)
  for accel in (.65, -.65, 1., -1.):
    assert center_output_scale(accel, speed) == 1.0 - .12 * center_envelope(accel, speed)
