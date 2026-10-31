"""Exercise the shell permission, displayed row, commit and startup receiver together."""
from pathlib import Path
from unittest.mock import patch

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface, toyota_auto_hold_supported
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot.ui import runtime_app
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, row_change
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


class FilesystemParams:
  def __init__(self, path):
    self.path = path

  def get_default_value(self, key):
    return Params().get_default_value(key)

  def get_param_path(self, key):
    return str(self.path / key)


@pytest.mark.parametrize('candidate', [CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023])
@pytest.mark.parametrize('alpha', [False, True])
def test_real_shell_row_commit_startup_receiver(tmp_path, candidate, alpha):
  params = FilesystemParams(tmp_path)
  cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], alpha, False, False)
  assert not toyota_auto_hold_supported(cp)
  baseline = cp.to_dict()
  shell = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  shell._mode = runtime_app.ShellMode.SETTINGS
  owner = FeatureSettingsOwner(params, shell._feature_authority,
                               vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp)
  with patch.object(runtime_app.ui_state, 'CP', cp):
    assert not shell._feature_authority('vehicle')  # Brake hold remains excluded.
    state = owner.snapshot(FeaturePage.VEHICLE, parked=True, system_long=alpha, lateral_context=True, metric=True)
    row = next(row for row in state.rows if row.key == 'ToyotaRadarMode')
    assert row.available, 'Continental radar must not inherit the brake-hold exclusion'
    request = row_change(row, 1)
    assert request is not None
    from dataclasses import replace
    assert owner.apply(replace(request, confirmation=True))
    assert Path(params.get_param_path('ToyotaRadarMode')).read_bytes() == b'1'
    saved = VehicleStartupPreferences.read(params, enabled=True)
    saved.prepare(cp)
    saved.finalize(cp)
    assert RadarInterface(cp).ars510 is not None
    assert cp.safetyConfigs[0].to_dict() == baseline['safetyConfigs'][0]
    assert cp.openpilotLongitudinalControl == baseline['openpilotLongitudinalControl']
    assert cp.lateralTuning.to_dict() == baseline['lateralTuning']
    shell._mode = runtime_app.ShellMode.HOME
    assert not shell._feature_authority('toyota_radar')
    assert not owner.apply(replace(request, confirmation=True, expected=b'1', value='Fused Tracks'))


@pytest.mark.parametrize('field', ['passive', 'dashcamOnly', 'notCar'])
def test_radar_permission_rejects_ineligible_vehicle(field):
  cp = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2_2023, gen_empty_fingerprint(), [], False, False, False)
  setattr(cp, field, True)
  shell = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  shell._mode = runtime_app.ShellMode.SETTINGS
  with patch.object(runtime_app.ui_state, 'CP', cp):
    assert not shell._feature_authority('toyota_radar')
