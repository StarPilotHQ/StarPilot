from dataclasses import replace

import pytest

from opendbc.car import structs
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import CAR
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.lateral.gain_runtime import gain_supported
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, row_change


def test_golf_parked_gain_setting_reaches_controls_at_restart(monkeypatch):
  monkeypatch.setenv("SIMULATION", "1")
  monkeypatch.setenv("REPLAY", "1")
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, {i: {0xFD: 8} if i == 1 else {} for i in range(8)}, [], True, False, False)
  with OpenpilotPrefix():
    params = Params()
    params.put("CarParams", cp.to_bytes(), block=True)
    parked = True
    owner = FeatureSettingsOwner(params, lambda group: parked or group != "parked_preferences",
                                 vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp)

    def row(key):
      state = owner.snapshot(FeaturePage.DEVELOPER_LATERAL, parked=parked, system_long=True, lateral_context=True, metric=True)
      return next(r for r in state.rows if r.key == key)

    assert gain_supported(cp)
    baseline = Controls()
    assert baseline.lateral_gain_owner is None
    assert tuple(baseline.LaC.pid._k_p[1]) == pytest.approx((0.6,))
    enable = row_change(row("torque:gain:mode"), 1)
    assert enable is not None
    parked = False
    assert not owner.apply(enable)
    parked = True
    assert owner.apply(enable)
    request = row_change(row("torque:gain:value"), -1)
    assert request is not None
    assert not owner.apply(replace(request, value="0.30"))
    assert owner.apply(replace(request, value="0.50"))
    assert tuple(baseline.LaC.pid._k_p[1]) == pytest.approx((0.6,))
    selected = Controls()
    assert tuple(selected.LaC.pid._k_p[1]) == pytest.approx((0.5,))
    assert tuple(selected.LaC.pid._k_i[1]) == tuple(baseline.LaC.pid._k_i[1])
    assert selected.LaC.ff_factor == baseline.LaC.ff_factor
    reset = row_change(row("torque:gain:mode"), -1)
    assert reset is not None and owner.apply(reset)
    assert Controls().lateral_gain_owner is None
    assert tuple(selected.LaC.pid._k_p[1]) == pytest.approx((0.5,))
    cp.transmissionType = structs.CarParams.TransmissionType.automatic
    assert not gain_supported(cp)


def test_standard_torque_gain_preserves_the_speed_schedule(monkeypatch):
  from openpilot.starpilot.lateral.gain_runtime import gain_basis
  from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, replace_gain, serialize_document
  import json

  monkeypatch.setenv("SIMULATION", "1")
  monkeypatch.setenv("REPLAY", "1")
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_SHARAN_MK2, {i: {0x1A0: 8} if i == 1 else {} for i in range(8)}, [], False, False, False)
  with OpenpilotPrefix():
    params = Params()
    params.put("CarParams", cp.to_bytes(), block=True)
    baseline = Controls()
    assert gain_supported(cp)
    basis = gain_basis(cp, baseline.LaC)
    speeds, gains = basis.source_table
    requested = round(0.9 * gains[-1], 8)
    document = replace_gain({}, str(cp.carFingerprint), basis, "custom", requested)
    params.put(DOCUMENT_KEY, json.loads(serialize_document(document)), block=True)
    selected = Controls()
    assert tuple(selected.LaC.pid._k_p[0]) == speeds
    assert tuple(selected.LaC.pid._k_p[1]) == pytest.approx(tuple(gain * requested / gains[-1] for gain in gains))
    assert selected.LaC.pid._k_i == baseline.LaC.pid._k_i
