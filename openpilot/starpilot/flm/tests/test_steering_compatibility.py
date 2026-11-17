"""Compatibility follows steering consumers, not unrelated platform discovery."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from openpilot.starpilot.flm.steering_compatibility import source_paths, revision
from openpilot.starpilot.flm.offroad_store import RouteScope, startup_row, NAMESPACE, KEY
from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES
from openpilot.starpilot.flm.learned_lifecycle import encode


@pytest.fixture
def closure(tmp_path):
  control = tmp_path / 'openpilot/selfdrive/controls/lib/latcontrol_torque.py'
  brand = tmp_path / 'opendbc_repo/opendbc/car/hyundai/interface.py'
  seed = [control, brand, brand.with_name('hyundaicanfd.py'),
          tmp_path / 'openpilot/starpilot/lateral/model_turn_assist.py',
          tmp_path / 'opendbc_repo/opendbc/safety/modes/hyundai_canfd.h']
  for path in seed:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('declared baseline\n')
  for path in source_paths(tmp_path, control, brand):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
      path.write_text('declared baseline\n')
  def token(native='native-one'):
    return revision(tmp_path, control, brand, ('LatControlTorque', 'HyundaiInterface'), native)
  return tmp_path, control, brand, token


@pytest.mark.parametrize('state', ['ready_for_next_drive', 'accepted_after_drive', 'retained_after_drive', 'rolled_back'])
def test_unrelated_updates_preserve_queued_accepted_and_rollback_selection(closure, state):
  root, _, _, token = closure
  before = token()
  for name in ('opendbc_repo/opendbc/car/car_helpers.py', 'opendbc_repo/opendbc/car/chrysler/discovery.py',
               'openpilot/starpilot/galaxy/server.py', 'openpilot/starpilot/flm/offroad_worker.py'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('unrelated changed implementation\n')
  assert token() == before
  scope = RouteScope('a' * 64, before, 'b' * 64, 'c' * 64, '0', 'native', 'torque')
  values = dict.fromkeys(ROUTE_NAMES, 0.)
  values['friction_threshold_mid'] = .0036
  row = {'scope': scope.document(), 'values': values, 'previous': dict.fromkeys(ROUTE_NAMES, 0.),
         'route_sha256s': ['d' * 64], 'source_start_ns': 1, 'source_end_ns': 2, 'analysis_sha256': 'e' * 64,
         'completed_wall_ns': 1794777785171179957, 'sequence': 1, 'state': state}
  store = root / KEY
  store.write_bytes(encode({'version': 3, 'profiles': {}, 'fullSurfaces': {}, NAMESPACE: {scope.key(): row}}))
  params = SimpleNamespace(get_param_path=lambda _: str(store))
  assert startup_row(params, scope) == (row, 'route_tune_loaded')
  legacy = replace(scope, controller_revision='actual-controller-registry-v2:' + 'f' * 64)
  assert startup_row(params, legacy) == (None, 'saved_tune_requires_controller_compatibility_review')


@pytest.mark.parametrize('name', [
  'openpilot/selfdrive/controls/lib/latcontrol_torque.py',
  'opendbc_repo/opendbc/car/hyundai/hyundaicanfd.py',
  'openpilot/starpilot/lateral/model_turn_assist.py',
  'opendbc_repo/opendbc/safety/modes/hyundai_canfd.h',
  'openpilot/starpilot/flm/offroad_surface.py',
  'opendbc_repo/opendbc/car/vehicle_model.py',
])
def test_relevant_controller_encoder_model_safety_and_bounds_changes_invalidate(closure, name):
  root, _, _, token = closure
  before = token()
  (root / name).write_text('relevant steering changed\n')
  assert token() != before


def test_missing_semantics_or_changed_native_cannot_reuse_identity(closure):
  root, _, _, token = closure
  assert token('native-one') != token('native-two')
  (root / 'openpilot/starpilot/flm/offroad_surface.py').unlink()
  with pytest.raises(ValueError, match='source unavailable'):
    token()
