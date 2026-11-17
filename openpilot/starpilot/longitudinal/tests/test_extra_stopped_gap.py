import copy
from dataclasses import replace
from types import SimpleNamespace as NS

import numpy as np
import pytest
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc
from openpilot.starpilot.longitudinal.profile_document import (
  default_personality_profiles, profile_document, migrate_profile_document, strict_profile_document,
)
from openpilot.starpilot.longitudinal.profile_runtime import ProfileHost, selected_profiles_requested
from openpilot.starpilot.ui.feature_settings_state import row_change
from openpilot.starpilot.ui.tests import test_feature_settings_owner as owner_tests


@pytest.fixture
def owner():
  fixture = owner_tests.FeatureSettingsOwnerTests()
  fixture.setUp()
  try:
    yield fixture
  finally:
    fixture.doCleanups()


def document(gap=0.):
  return profile_document(default_personality_profiles(False), enabled=False, selected_acceleration_profile='dom_default',
                          selected_deceleration_profile='dom_default', extra_stopped_gap_m=gap)


def test_migration_keeps_all_existing_settings_and_default_output():
  old = document()
  old['schemaVersion'] = 5
  del old['extraStoppedGapM']
  before = copy.deepcopy(old)
  migrated = migrate_profile_document(old)
  assert old == before
  assert migrated == {**old, 'schemaVersion': 6, 'extraStoppedGapM': 0.}
  assert not selected_profiles_requested(migrated)
  assert selected_profiles_requested(document(1.))


@pytest.mark.parametrize('bad', [-1, 3.049, float('inf'), float('nan'), True, '1', None])
def test_invalid_gap_cannot_activate(bad):
  raw = document()
  raw['extraStoppedGapM'] = bad
  assert strict_profile_document(raw) is None


def request(row, value):
  gesture = row_change(row, 1)
  assert gesture is not None
  return replace(gesture, value=value)


def test_metric_display_and_edits_preserve_physical_gap_and_other_profiles(owner):
  row = owner._row('profiles', 'profile:stopped_gap')
  assert row.available and row.value == '0.0' and row.unit == 'ft' and row.maximum == 10.
  assert owner.owner.apply(request(row, '5'))
  stored = owner.params.get('LongitudinalPersonalityProfiles')
  assert stored['extraStoppedGapM'] == pytest.approx(1.524)
  owner.params.put_bool('IsMetric', True, block=True)
  metric = owner._row('profiles', 'profile:stopped_gap')
  assert metric.unit == 'm' and float(metric.value) == pytest.approx(1.524)
  assert owner.params.get('LongitudinalPersonalityProfiles') == stored
  assert owner.owner.apply(request(metric, '2'))
  changed = owner.params.get('LongitudinalPersonalityProfiles')
  assert changed == {**stored, 'extraStoppedGapM': 2.}
  owner.params.put_bool('IsMetric', False, block=True)
  imperial = owner._row('profiles', 'profile:stopped_gap')
  assert float(imperial.value) == pytest.approx(2. / .3048, abs=.001)
  assert not owner.owner.apply(request(imperial, '-1'))
  assert not owner.owner.apply(request(imperial, '10.1'))
  assert owner.params.get('LongitudinalPersonalityProfiles') == changed
  braking = owner._row('profiles', 'profile:global_braking')
  assert owner.owner.apply(request(braking, 'Sport'))
  assert owner.params.get('LongitudinalPersonalityProfiles')['extraStoppedGapM'] == 2.


def test_changed_units_or_vehicle_or_permission_rejects_old_press(owner):
  row = owner._row('profiles', 'profile:stopped_gap')
  press = request(row, '5')
  owner.params.put_bool('IsMetric', True, block=True)
  assert not owner.owner.apply(press)
  row = owner._row('profiles', 'profile:stopped_gap')
  owner.fingerprint = 'OTHER'
  assert not owner.owner.apply(request(row, '2'))
  owner.allowed = False
  assert not owner.owner.apply(request(owner._row('profiles', 'profile:stopped_gap'), '2'))


def test_saved_refresh_uses_existing_profile_snapshot_and_expires_bad_data(owner):
  from openpilot.cereal import log
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.hyundai.interface import CarInterface
  from opendbc.car.hyundai.values import CAR
  cp = CarInterface.get_params(CAR.KIA_STINGER_2022, gen_empty_fingerprint(), [], True, False, False)
  owner.params.put('LongitudinalPersonalityProfiles', document(2.), block=True)
  host = ProfileHost(owner.params)
  tuning = host.sample_selected(10_000_000_000, log.LongitudinalPersonality.standard, 1., cp)
  assert tuning is not None and tuning.extra_stopped_gap_m == 2.
  owner.params.put('LongitudinalPersonalityProfiles', {'invalid': True}, block=True)
  assert host.sample_selected(11_000_000_000, log.LongitudinalPersonality.standard, 1., cp) is None


def solve(gap=0., *, speed=1., lead_speed=0., present=True, stop_line=None):
  mpc = LongitudinalMpc()
  mpc.set_cur_state(speed, 0.)
  lead = NS(present=present, radar=True, dRel=10., vLead=lead_speed, aLeadK=0., aLeadTau=1.5, modelProb=.99)
  radar = NS(leadOne=lead, leadTwo=NS(present=False))
  mpc.update(radar, extra_stopped_gap_m=gap, stop_line_m=stop_line)
  assert mpc.solution_status == 0
  return mpc


def test_actual_native_mpc_adds_only_bounded_low_speed_lead_space():
  baseline, extra = solve(), solve(3.048)
  np.testing.assert_allclose(extra.params[:, 2], baseline.params[:, 2] - 3.048, rtol=0, atol=1e-12)
  assert extra.a_solution[1] < baseline.a_solution[1]
  np.testing.assert_array_equal(extra.params[:, [0, 1, 3, 4, 5]], baseline.params[:, [0, 1, 3, 4, 5]])
  for kwargs in ({'present': False}, {'speed': 20., 'lead_speed': 20.}, {'stop_line': 0.}):
    before, after = solve(**kwargs), solve(3.048, **kwargs)
    np.testing.assert_array_equal(before.params, after.params)
    np.testing.assert_array_equal(before.a_solution, after.a_solution)


@pytest.mark.parametrize('bad', [-1., 3.049, float('nan'), float('inf'), True, None])
def test_invalid_runtime_input_keeps_original_mpc(bad):
  before, after = solve(), solve(bad)
  np.testing.assert_array_equal(before.params, after.params)
  np.testing.assert_array_equal(before.a_solution, after.a_solution)


def test_unconfigured_document_and_zero_reset_are_editable(owner):
  from openpilot.starpilot.ui.feature_settings_state import row_default
  owner.params.put('LongitudinalPersonalityProfiles', {}, block=True)
  assert owner.owner.apply(request(owner._row('profiles', 'profile:stopped_gap'), '5'))
  row = owner._row('profiles', 'profile:stopped_gap')
  reset = row_default(row)
  assert reset is not None and owner.owner.apply(reset)
  assert owner.params.get('LongitudinalPersonalityProfiles')['extraStoppedGapM'] == 0.


def test_actual_stinger_planner_receives_preference_without_changing_limits():
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.hyundai.interface import CarInterface
  from opendbc.car.hyundai.values import CAR
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.starpilot.longitudinal.profile_runtime import SelectedProfileTuning
  from openpilot.starpilot.longitudinal.tests.test_lead_approach_planner import Frame, DRIVE_NS
  cp = CarInterface.get_params(CAR.KIA_STINGER_2022, gen_empty_fingerprint(), [], True, False, False)
  original_cp = cp.to_dict()
  assert cp.openpilotLongitudinalControl
  baseline, extra = LongitudinalPlanner(cp, init_v=1.), LongitudinalPlanner(cp, init_v=1.)
  for index in range(10):
    sm = Frame(index)
    sm['carState'].vEgo = 1.
    sm['carState'].aEgo = 0.
    sm['radarState'].leadTwo.present = False
    lead = sm['radarState'].leadOne
    lead.radar = True
    lead.dRel, lead.vLead, lead.vLeadK, lead.aLeadK = 10., 0., 0., 0.
    baseline.update(sm, now_ns=sm.now_ns, drive_id=DRIVE_NS)
    extra.update(sm, now_ns=sm.now_ns, drive_id=DRIVE_NS, selected_profiles=SelectedProfileTuning(extra_stopped_gap_m=1.524))
  np.testing.assert_allclose(extra.mpc.params[:, 2], baseline.mpc.params[:, 2] - 1.524, rtol=0, atol=1e-12)
  np.testing.assert_array_equal(extra.mpc.params[:, [0, 1, 4, 5]], baseline.mpc.params[:, [0, 1, 4, 5]])
  assert extra.output_a_target <= baseline.output_a_target and cp.to_dict() == original_cp


def test_galaxy_editor_converts_units_and_rejects_old_unit_press():
  from openpilot.starpilot.galaxy.tests import test_settings as gateway_tests
  fixture = gateway_tests.SettingsGatewayTest()
  fixture.setUp()
  try:
    page = fixture.page('profiles')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Extra stopped-lead distance')
    assert page['rows'][index]['unit'] == 'ft'
    intent = fixture.gateway.preview(page['view'], index, 0, fixture.session, fixture.generation, value=5.)
    assert fixture.gateway.confirm(intent['intent'], fixture.session, fixture.generation)
    saved = fixture.params.get('LongitudinalPersonalityProfiles')
    assert saved['extraStoppedGapM'] == 1.524
    fixture.params.put_bool('IsMetric', True, block=True)
    page = fixture.page('profiles')
    row = page['rows'][index]
    assert row['unit'] == 'm' and float(row['value']) == 1.524
    assert fixture.params.get('LongitudinalPersonalityProfiles') == saved
    intent = fixture.gateway.preview(page['view'], index, 0, fixture.session, fixture.generation, value=2.)
    fixture.params.put_bool('IsMetric', False, block=True)
    assert not fixture.gateway.confirm(intent['intent'], fixture.session, fixture.generation)
    assert fixture.params.get('LongitudinalPersonalityProfiles') == saved
  finally:
    fixture.doCleanups()
