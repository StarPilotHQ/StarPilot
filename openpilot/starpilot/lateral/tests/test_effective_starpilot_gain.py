from types import SimpleNamespace

import numpy as np
import pytest

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from opendbc.car.gm.values import CAR as GM_CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode, policy_for
from openpilot.starpilot.lateral.gain_runtime import create_gain_owner, gain_basis
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, replace_gain, serialize_document


def controller(car=CAR.KIA_EV6, mode=ControllerMode.STARPILOT):
  cp = interfaces[car].get_non_essential_params(car)
  return cp, LatControlTorque(cp.as_reader(), interfaces[car](cp), .01, controller_mode=mode)


def test_every_starpilot_torque_profile_uses_effective_stable_gain():
  checked = []
  wrong = []
  for car, interface in interfaces.items():
    cp = interface.get_non_essential_params(car)
    if cp.lateralTuning.which() != 'torque' or policy_for(cp) is None:
      continue
    before = cp.to_dict()
    selected = LatControlTorque(cp.as_reader(), interface(cp), .01, controller_mode=ControllerMode.STARPILOT)
    assert cp.to_dict() == before
    checked.append(str(car))
    for speed in (.3, 1., 2., 5., 10., 15., 30.):
      actual = float(np.interp(speed, selected.pid._k_p[0], selected.pid._k_p[1]))
      if actual != pytest.approx(.6):
        wrong.append((str(car), speed, actual))
  assert {'KIA_EV6', 'HYUNDAI_IONIQ_5', 'KIA_NIRO_EV_2ND_GEN'} <= set(checked)
  assert not wrong, wrong


def test_ev6_low_speed_proportional_term_matches_stable_effective_loop():
  cp, selected = controller()
  params = log.VehicleParameters.new_message(angleOffsetDeg=-.53, roll=.015)
  state = SimpleNamespace(vEgo=2.3547, steeringAngleDeg=-38.1, steeringPressed=False)
  vm = VehicleModel(cp)
  for frame in range(100):
    _, _, result = selected.update(frame > 5, state, vm, params, False, -.001, False, .3)
    if result.active:
      raw_error = result.desiredLateralAccel - result.actualLateralAccel
      low_speed_factor = (np.interp(state.vEgo, [0., 10., 20., 30.], [12., 10.5, 8., 5.]) / state.vEgo) ** 2
      assert result.p == pytest.approx(raw_error * (.6 + low_speed_factor), abs=1e-6)


def test_preexisting_manual_gain_keeps_ownership_and_withdraws_to_stable(tmp_path):
  cp, selected = controller(GM_CAR.CHEVROLET_BOLT_ACC_2022_2023)
  basis = gain_basis(cp, selected)
  raw = serialize_document(replace_gain({}, str(cp.carFingerprint), basis, 'custom', .7))
  file = tmp_path / DOCUMENT_KEY
  file.write_bytes(raw)
  (tmp_path / "AdvancedLateralTune").write_bytes(b"1")
  params = SimpleNamespace(get_param_path=lambda key: str(tmp_path / key))
  owner = create_gain_owner(params, cp, selected)
  assert owner is not None
  assert selected.pid._k_p == [[0.], [.7]]
  assert file.read_bytes() == raw
  selected.pid.i = .123
  file.unlink()
  owner.refresh(now_ns=1_000_000_000)
  assert selected.pid._k_p == [[0.], [.6]]
  assert selected.pid.i == .123
