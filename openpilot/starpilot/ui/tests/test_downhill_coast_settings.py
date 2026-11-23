from openpilot.common.params import Params
from openpilot.starpilot.longitudinal.downhill_coast import KEY, SURPLUS_KEY, read_surplus
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest, row_change


def test_downhill_toggle_and_slider_share_the_planner_settings(tmp_path):
  params = Params(str(tmp_path))
  owner = FeatureSettingsOwner(params, lambda group: group in ('long', 'parked_preferences', 'preferences'),
                               vehicle_fingerprint=lambda: 'TEST_CAR')

  def rows():
    state = owner.snapshot('profiles', parked=True, system_long=True, lateral_context=False, metric=False)
    return {row.key: row for row in state.rows}

  initial = rows()
  assert initial[KEY].value == 'Off'
  assert SURPLUS_KEY not in initial
  enable = row_change(initial[KEY])
  assert enable is not None and owner.apply(enable)
  slider = rows()[SURPLUS_KEY]
  assert (float(slider.value), slider.minimum, slider.maximum, slider.step, slider.unit) == (0., 0., 25., 1., 'mph')
  assert read_surplus(params) == 0.
  assert not owner.apply(FeatureSettingsRequest(SURPLUS_KEY, slider.source, '26', vehicle_fingerprint='TEST_CAR'))
  assert owner.apply(FeatureSettingsRequest(SURPLUS_KEY, slider.source, '5', vehicle_fingerprint='TEST_CAR'))
  assert read_surplus(params) > 0.
  active = rows()
  disable = row_change(active[KEY])
  assert disable is not None and owner.apply(disable)
  assert SURPLUS_KEY not in rows()
  assert read_surplus(params) == 0.
  assert params.get(SURPLUS_KEY) == 5.
