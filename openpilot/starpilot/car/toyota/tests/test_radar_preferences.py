"""Saved opt-in selection joins the current factory and existing publication owners."""

from pathlib import Path
from contextvars import ContextVar
from unittest.mock import Mock

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.common.params import Params
from openpilot.starpilot.car.toyota.radar_preferences import KEY, CHOICES, capability, selected
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


class FilesystemParams:
  def __init__(self, path):
    self.path = path

  def get_default_value(self, key):
    return Params().get_default_value(key)

  def get_param_path(self, key):
    return str(self.path / key)


def factory(candidate=CAR.TOYOTA_RAV4_TSS2_2022):
  return CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, False)


@pytest.mark.parametrize('raw,expected', [(None, 0), (b'0', 0), (b'1', 1), (b'2', 2), (b'3', 0), (b'-1', 0), (b'', 0), (b'garbage', 0)])
def test_saved_choice_is_literal_default_off(tmp_path, raw, expected):
  params = FilesystemParams(tmp_path)
  if raw is not None:
    Path(params.get_param_path(KEY)).write_bytes(raw)
  assert selected(params, enabled=True) == expected
  assert selected(params, enabled=False) == 0
  Path(params.get_param_path('SafeMode')).write_bytes(b'1')
  assert selected(params, enabled=True) == 0


@pytest.mark.parametrize('candidate', [CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023])
@pytest.mark.parametrize('mode', [0, 1, 2])
def test_actual_startup_changes_only_radar_fields(tmp_path, candidate, mode):
  params = FilesystemParams(tmp_path)
  Path(params.get_param_path(KEY)).write_bytes(str(mode).encode())
  cp = factory(candidate)
  baseline = cp.to_dict()
  owner = VehicleStartupPreferences.read(params, enabled=True)
  owner.prepare(cp)
  owner.finalize(cp)
  expected = baseline.copy()
  if mode:
    expected['flags'] |= int(ToyotaFlags.ARS510_RADAR)
    if mode == 2:
      expected['flags'] |= int(ToyotaFlags.ARS510_FUSED)
    expected['radarUnavailable'] = False
  assert cp.to_dict() == expected
  assert cp.safetyConfigs[0].to_dict() == baseline['safetyConfigs'][0]
  receiver = RadarInterface(cp)
  assert (receiver.ars510 is not None) == bool(mode)
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl  # Stock native authority unchanged.


@pytest.mark.parametrize('mutation', ['vehicle', 'capability', 'expected', 'authorization', 'unconfirmed'])
def test_setting_commit_rechecks_vehicle_saved_bytes_and_existing_authority(tmp_path, mutation):
  params = FilesystemParams(tmp_path)
  cp = factory()
  owner = FeatureSettingsOwner.__new__(FeatureSettingsOwner)
  owner.params = params
  owner._snapshot_vehicle = ContextVar("test_radar_snapshot_vehicle", default=None)
  owner.vehicle_params = Mock(return_value=cp)
  owner.vehicle_fingerprint = Mock(return_value=str(cp.carFingerprint))
  owner.authority = Mock(return_value=mutation != 'authorization')
  request = FeatureSettingsRequest(
    KEY,
    b'0' if mutation == 'expected' else None,
    CHOICES[1],
    confirmation=mutation != 'unconfirmed',
    vehicle_fingerprint='OTHER' if mutation == 'vehicle' else str(cp.carFingerprint),
    capability=('wrong',) if mutation == 'capability' else capability(cp),
  )
  assert not owner._apply_toyota_radar(request)
  assert not Path(params.get_param_path(KEY)).exists()


def test_confirmed_existing_vehicle_owner_persists_exact_opt_in(tmp_path):
  params = FilesystemParams(tmp_path)
  cp = factory()
  owner = FeatureSettingsOwner.__new__(FeatureSettingsOwner)
  owner.params = params
  owner._snapshot_vehicle = ContextVar("test_radar_snapshot_vehicle", default=None)
  owner.vehicle_params = Mock(return_value=cp)
  owner.vehicle_fingerprint = Mock(return_value=str(cp.carFingerprint))
  owner.authority = Mock(return_value=True)
  request = FeatureSettingsRequest(KEY, None, CHOICES[1], True, vehicle_fingerprint=str(cp.carFingerprint), capability=capability(cp))
  assert owner._apply_toyota_radar(request)
  assert Path(params.get_param_path(KEY)).read_bytes() == b'1'
  assert selected(params, enabled=True) == 1
