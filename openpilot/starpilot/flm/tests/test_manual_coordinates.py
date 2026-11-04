"""Manual edits are explicit bounded configuration, never accepted learning."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from opendbc.car import structs
from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.manual_coordinates import ManualCoordinates, ManualEffects, context, load
from openpilot.starpilot.flm.selected_effects import apply_selected_stage
from openpilot.starpilot.flm.torque_stages import StageFrame
from openpilot.starpilot.lateral.controller_selection import default_selection
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, FieldChoice, PlatformProfile, parse_document, serialize_document
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.torque_feature import TorqueFeature


@pytest.fixture
def cp():
  value = structs.CarParams.new_message()
  value.carFingerprint = str(CAR.HYUNDAI_IONIQ_6)
  value.brand = 'hyundai'
  value.steerControlType = 'torque'
  tune = value.lateralTuning.init('torque')
  tune.latAccelFactor, tune.latAccelOffset, tune.friction = 2.5, 0., .1
  return value


class PrivateParams:
  def __init__(self, root):
    self.root = root
    (root / 'd').mkdir()
  def get_param_path(self, key):
    return str(self.root / 'd' / key)
  def put(self, *args, **kwargs):
    raise AssertionError('Manual editing must use the exact-source document writer')


def basis(cp):
  t = cp.lateralTuning.torque
  return float(t.latAccelFactor), float(t.latAccelOffset), float(t.friction)


def manual(cp, **changes):
  values = dict.fromkeys(NAMES, 0.)
  values.update(changes)
  return ManualCoordinates(context(cp, default_selection(cp).mode), tuple(values.items())).validate()


def save(params, cp, value):
  profile = PlatformProfile(basis(cp), FieldChoice(), FieldChoice(), manual_coordinates=value)
  raw = serialize_document({str(cp.carFingerprint): profile})
  from pathlib import Path
  Path(params.get_param_path(DOCUMENT_KEY)).write_bytes(raw)
  return raw


def saved_profile(params, cp):
  raw, readable = read_saved(params, DOCUMENT_KEY, 16384)
  assert readable and raw is not None
  return parse_document(raw)[str(cp.carFingerprint)]


@pytest.mark.parametrize('name', NAMES)
def test_each_of_thirty_values_roundtrips_without_learning_history(cp, name):
  value = manual(cp, **{name: .01})
  restored = ManualCoordinates.from_document(value.document())
  assert restored == value
  assert dict(restored.values)[name] == .01
  assert sum(v != 0 for _, v in restored.values) == 1


@pytest.mark.parametrize('bad', [True, float('nan'), float('inf'), -.001, .251])
def test_deadband_refuses_wrong_types_nonfinite_and_bounds(cp, bad):
  with pytest.raises((ValueError, OverflowError)):
    manual(cp, center_deadband_low_deg=bad)


def test_full_profile_is_required_and_ordered(cp):
  value = manual(cp)
  with pytest.raises(ValueError):
    replace(value, values=value.values[:-1]).validate()
  with pytest.raises(ValueError):
    replace(value, values=tuple(reversed(value.values))).validate()


def test_opposite_speed_window_edges_stay_ordered_inside_original_limits(cp):
  value = manual(cp, curvy_turn_in_trim_speed_min=.25, curvy_turn_in_trim_speed_max=-.25)
  assert dict(value.values)['curvy_turn_in_trim_speed_min'] == .25
  assert dict(value.values)['curvy_turn_in_trim_speed_max'] == -.25


def test_same_actual_context_loads_once_and_does_not_write_history(tmp_path, cp):
  params = PrivateParams(tmp_path)
  raw = save(params, cp, manual(cp, center_taper_max=.01))
  owner, status = load(cp, default_selection(cp).mode, params)
  assert type(owner) is ManualEffects and status == 'configured'
  assert dict(owner.profile.values)['center_taper_max'] == .01
  assert read_saved(params, DOCUMENT_KEY, 16384) == (raw, True)
  assert read_saved(params, 'AutomaticSteeringLiveTrials', 65536) == (None, True)


def test_changed_vehicle_context_refuses_instead_of_retagging(tmp_path, cp):
  params = PrivateParams(tmp_path)
  raw = save(params, cp, manual(cp, center_taper_max=.01))
  cp.mass = 1800.
  assert load(cp, default_selection(cp).mode, params) == (None, 'context_changed')
  assert read_saved(params, DOCUMENT_KEY, 16384) == (raw, True)


def test_unreadable_is_not_absence(tmp_path, cp):
  params = PrivateParams(tmp_path)
  (tmp_path / 'd' / DOCUMENT_KEY).mkdir()
  assert load(cp, default_selection(cp).mode, params) == (None, 'unreadable')


def test_neutral_profile_does_not_activate_manual_override(tmp_path, cp):
  params = PrivateParams(tmp_path)
  save(params, cp, manual(cp))
  assert load(cp, default_selection(cp).mode, params) == (None, 'not_configured')


@pytest.mark.parametrize('active,override,limited,curvature_limited', [
  (False, False, False, False), (True, True, False, False),
  (True, False, True, False), (True, False, True, True),
])
def test_manual_does_not_bypass_inactive_override_or_limiter(cp, active, override, limited, curvature_limited):
  source = ManualEffects(manual(cp, center_taper_max=.01))
  source.begin_frame()
  parent = SimpleNamespace(manual_flm_effects=source, flm_surface=None, starpilot_extension=None, automatic_flm_effects=None)
  frame = StageFrame(15., 0., 0., .5, .3, active, override, limited, curvature_limited)
  assert apply_selected_stage(parent, frame) == (.5, .3)


def test_manual_uses_existing_selected_stage_with_no_auto_owner(cp):
  source = ManualEffects(manual(cp, center_taper_max=.01))
  source.begin_frame()
  parent = SimpleNamespace(manual_flm_effects=source, flm_surface=None, starpilot_extension=None, automatic_flm_effects=None)
  frame = StageFrame(15., 0., 0., .5, .3, True, False, False)
  ff, threshold = apply_selected_stage(parent, frame)
  assert 0 < ff < .5
  assert .3 <= threshold <= .375
  assert parent.automatic_flm_effects is None


def feature(cp, params, state):
  capability = (str(cp.carFingerprint), 'hyundai', 'torque', 'torque', False, *basis(cp), None)
  def raw(key):
    return read_saved(params, key, 16384)[0]
  owner = SimpleNamespace(params=params, authority=lambda group: state['parked'] if group == 'parked_preferences' else True,
    vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp,
    _capability=lambda group: capability, _raw=raw, _readable=lambda key: read_saved(params, key, 16384)[1],
    _dependents=lambda *keys: tuple((key, raw(key)) for key in keys))
  return TorqueFeature(owner), capability


def request(row, value):
  return FeatureSettingsRequest(row.key, row.source, str(value), capability=row.capability,
    related_source=row.related_source, dependencies=row.dependencies, vehicle_fingerprint=row.vehicle_fingerprint)


def test_parked_edit_exact_readback_and_single_value_reset(tmp_path, cp):
  params = PrivateParams(tmp_path)
  state = {'parked': True}
  editor, capability = feature(cp, params, state)
  rows = editor.manual_rows(capability, True)
  assert len(rows) == 30 and all(row.available for row in rows)
  row = next(r for r in rows if r.key.endswith(':center_taper_max'))
  row = replace(row, vehicle_fingerprint=str(cp.carFingerprint))
  assert editor.apply(request(row, 1.))
  profile = saved_profile(params, cp)
  assert profile.manual_coordinates is not None
  assert dict(profile.manual_coordinates.values)['center_taper_max'] == .01
  row = next(r for r in editor.manual_rows(capability, True) if r.key.endswith(':center_taper_max'))
  assert editor.apply(request(replace(row, vehicle_fingerprint=str(cp.carFingerprint)), 0.))
  assert saved_profile(params, cp).manual_coordinates is None


def test_stale_source_and_onroad_writes_are_refused(tmp_path, cp):
  params = PrivateParams(tmp_path)
  state = {'parked': True}
  editor, capability = feature(cp, params, state)
  row = replace(editor.manual_rows(capability, True)[0], vehicle_fingerprint=str(cp.carFingerprint))
  assert editor.apply(request(row, 1.))
  assert not editor.apply(request(row, 2.))
  current = replace(editor.manual_rows(capability, True)[0], vehicle_fingerprint=str(cp.carFingerprint))
  state['parked'] = False
  assert not editor.apply(request(current, 2.))
  profile = saved_profile(params, cp)
  assert profile.manual_coordinates is not None
  assert dict(profile.manual_coordinates.values)[NAMES[0]] == .01


def test_invalid_saved_document_is_not_replaced_by_a_new_manual_tune(tmp_path, cp):
  params = PrivateParams(tmp_path)
  (tmp_path / 'd' / DOCUMENT_KEY).write_bytes(b'{broken')
  editor, capability = feature(cp, params, {'parked': True})
  assert not any(row.available for row in editor.manual_rows(capability, True))
  assert (tmp_path / 'd' / DOCUMENT_KEY).read_bytes() == b'{broken'


def test_authority_loss_under_native_lock_refuses_and_removes_stage(tmp_path, cp, monkeypatch):
  from openpilot.starpilot import saved_document
  params = PrivateParams(tmp_path)
  state = {'parked': True}
  editor, capability = feature(cp, params, state)
  row = replace(editor.manual_rows(capability, True)[0], vehicle_fingerprint=str(cp.carFingerprint))
  original = saved_document.acquire_native_lock
  def acquire(fd):
    original(fd)
    state['parked'] = False
  monkeypatch.setattr(saved_document, 'acquire_native_lock', acquire)
  assert not editor.apply(request(row, 1.))
  assert read_saved(params, DOCUMENT_KEY, 16384) == (None, True)
  assert not list(tmp_path.glob('.manual-steering-*'))


def test_source_change_under_native_lock_is_not_overwritten(tmp_path, cp, monkeypatch):
  from openpilot.starpilot import saved_document
  from pathlib import Path
  params = PrivateParams(tmp_path)
  editor, capability = feature(cp, params, {'parked': True})
  row = replace(editor.manual_rows(capability, True)[0], vehicle_fingerprint=str(cp.carFingerprint))
  other = serialize_document({str(cp.carFingerprint): PlatformProfile(basis(cp), FieldChoice(), FieldChoice('custom', .12))})
  original = saved_document.acquire_native_lock
  def acquire(fd):
    original(fd)
    Path(params.get_param_path(DOCUMENT_KEY)).write_bytes(other)
  monkeypatch.setattr(saved_document, 'acquire_native_lock', acquire)
  assert not editor.apply(request(row, 1.))
  assert read_saved(params, DOCUMENT_KEY, 16384) == (other, True)
  assert not list(tmp_path.glob('.manual-steering-*'))


def test_reset_all_uses_original_row_request_and_requires_confirmation(tmp_path, cp):
  from openpilot.starpilot.ui.feature_settings_state import row_change
  params = PrivateParams(tmp_path)
  save(params, cp, manual(cp, ff_gain_left=.01, center_taper_max=.01))
  editor, capability = feature(cp, params, {'parked': True})
  reset = replace(editor.manual_rows(capability, True)[-1], vehicle_fingerprint=str(cp.carFingerprint))
  unconfirmed = row_change(reset)
  assert unconfirmed is not None and unconfirmed.value == 'Reset'
  assert not editor.apply(unconfirmed)
  assert editor.apply(replace(unconfirmed, confirmation=True))
  assert saved_profile(params, cp).manual_coordinates is None


def test_replay_controller_construction_does_not_read_manual_preferences(cp, monkeypatch):
  from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
  from openpilot.starpilot.flm import manual_coordinates
  def forbidden(*args, **kwargs):
    raise AssertionError('Only the actual Controls owner may load manual configuration')
  monkeypatch.setattr(manual_coordinates, 'load', forbidden)
  ci = SimpleNamespace(
    torque_from_lateral_accel=lambda: lambda value, tune: value / tune.latAccelFactor,
    lateral_accel_from_torque=lambda: lambda value, tune: value * tune.latAccelFactor)
  controller = LatControlTorque(cp.as_reader(), ci, .01, controller_mode=default_selection(cp).mode)
  assert controller.manual_flm_effects is None
  assert controller.manual_flm_status == 'not_configured'


def test_next_startup_loads_new_saved_values_without_mutating_running_profile(tmp_path, cp):
  params = PrivateParams(tmp_path)
  save(params, cp, manual(cp, center_taper_max=.01))
  original, status = load(cp, default_selection(cp).mode, params)
  assert status == 'configured'
  save(params, cp, manual(cp, center_taper_max=.02))
  following, status = load(cp, default_selection(cp).mode, params)
  assert status == 'configured'
  assert dict(original.profile.values)['center_taper_max'] == .01
  assert dict(following.profile.values)['center_taper_max'] == .02


@pytest.mark.parametrize('saved,expected', [(False, True), (True, False)])
def test_actual_controls_startup_holds_native_learning_only_for_manual_tune(tmp_path, cp, monkeypatch, saved, expected):
  from openpilot.selfdrive.controls import controlsd
  params = PrivateParams(tmp_path)
  if saved:
    save(params, cp, manual(cp, center_taper_max=.01))
  (tmp_path / 'd' / 'ForceAutoTuneOff').write_bytes(b'0')
  monkeypatch.setattr(controlsd, 'learning_allowed', lambda *args, **kwargs: True)
  owner = object.__new__(controlsd.Controls)
  owner.CP = cp
  monkeypatch.setattr(owner, 'params', params, raising=False)
  owner.lateral_controller_selection = default_selection(cp)
  effects, status = owner._configure_manual_tune()
  assert owner.torque_learning_allowed is expected
  assert (effects is not None) is saved
  assert status == ('configured' if saved else 'not_configured')
  assert (tmp_path / 'd' / 'ForceAutoTuneOff').read_bytes() == b'0'


def test_actual_controls_startup_does_not_treat_unreadable_as_automatic_permission(tmp_path, cp, monkeypatch):
  from openpilot.selfdrive.controls import controlsd
  params = PrivateParams(tmp_path)
  (tmp_path / 'd' / DOCUMENT_KEY).mkdir()
  monkeypatch.setattr(controlsd, 'learning_allowed', lambda *args, **kwargs: True)
  owner = object.__new__(controlsd.Controls)
  owner.CP = cp
  monkeypatch.setattr(owner, 'params', params, raising=False)
  owner.lateral_controller_selection = default_selection(cp)
  assert owner._configure_manual_tune() == (None, 'unreadable')
  assert owner.torque_learning_allowed is False
