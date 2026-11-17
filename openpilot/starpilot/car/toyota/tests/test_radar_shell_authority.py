"""Both settings surfaces omit the retired Continental receiver choice."""
from unittest.mock import patch

import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.tests.test_radar_preferences import FilesystemParams
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage
from openpilot.starpilot.ui import runtime_app
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway


@pytest.mark.parametrize('candidate', [CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023])
@pytest.mark.parametrize('alpha', [False, True])
def test_no_manual_continental_choice_on_either_surface(tmp_path, candidate, alpha):
  params = FilesystemParams(tmp_path)
  cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], alpha, False, False)
  shell = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  shell._mode = runtime_app.ShellMode.SETTINGS
  with patch.object(runtime_app.ui_state, 'CP', cp):
    native = FeatureSettingsOwner(params, shell._feature_authority,
      vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp)
    ctx = AuthorityContext(True, cp, cp.to_bytes())
    galaxy = SettingsGateway(params)._owner(ctx)
    for owner in (native, galaxy):
      state = owner.snapshot(FeaturePage.VEHICLE, parked=True, system_long=alpha, lateral_context=True, metric=True)
      assert all(row.key != 'ToyotaRadarMode' for row in state.rows), 'RETIRED_CONTINENTAL_TOGGLE'
