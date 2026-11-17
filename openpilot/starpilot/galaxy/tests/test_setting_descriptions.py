"""Purpose coverage for all source-expanded setting/action keys, without defaults."""
from dataclasses import replace
import json
from pathlib import Path

from openpilot.starpilot.galaxy.setting_descriptions import DESCRIPTIONS, FLM_DESCRIPTIONS, INITIALIZATION, description, purpose
from openpilot.starpilot.ui.feature_settings_state import FeatureRow
from openpilot.starpilot.ui.long_profile_feature import CATEGORY_HELP, VALUE_HELP
from openpilot.starpilot.longitudinal.profile_preferences import SCALARS
from openpilot.starpilot.controllers.wheel_actions import KEYS as WHEEL_KEYS
from openpilot.starpilot.conditional_mode.actions import BOOLEAN_FIELDS, NUMBER_FIELDS


def inventory():
  return set(json.loads((Path(__file__).parent / 'description-key-inventory.json').read_text()))


def test_all_expanded_description_keys_have_a_purpose_before_timing_or_warning():
  keys = inventory()
  assert set(DESCRIPTIONS) <= keys
  for key in keys:
    row = FeatureRow(key, 'Test action', 'On', reason='Saved for the next drive')
    text = purpose(row)
    assert text, key
    first = description(row).split('.')[0].lower()
    assert not first.startswith(('saved', 'warning', 'requires', 'applies')), key
    assert not any(word in first for word in ('next drive', 'migration', 'does not', 'restart')), key
    assert row.reason == 'Saved for the next drive'  # Presentation does not mutate the canonical owner.
  for key in INITIALIZATION:
    assert INITIALIZATION[key] in description(FeatureRow(key, 'Setting', 'On'))


def test_generated_inventory_matches_actual_canonical_field_and_button_sources():
  keys = inventory()
  for scalars in SCALARS.values():
    assert set(scalars) <= keys
    assert {'long_repair:' + key for key in scalars} <= keys
  assert {'wheel:' + key for key in WHEEL_KEYS} <= keys
  for section in ('cem', 'ccm'):
    assert {'conditional:' + section + ':' + field for field in
            BOOLEAN_FIELDS[section] | NUMBER_FIELDS[section] | {'persist_manual'}} <= keys
  for name in ('traffic', 'aggressive', 'standard', 'relaxed'):
    for category in CATEGORY_HELP:
      assert 'profile:' + name + ':' + category in keys
      assert {'profile:' + name + ':' + category + ':' + str(index) for index in range(10)} <= keys
  for suffix in VALUE_HELP:
    assert all(name + suffix in keys for name in ('Aggressive', 'Standard', 'Relaxed'))


def test_real_warning_kept_after_purpose_and_false_blanket_next_drive_removed():
  row = FeatureRow('LongPitch', 'Grade Compensation', 'Invalid', available=False,
                   reason='Invalid saved value; repair required')
  assert description(row).startswith('Compensates acceleration and braking for road grade.')
  assert description(row).endswith(row.reason)
  assert 'next drive' not in description(replace(row, available=True, reason='Saved for the next drive'))
  inactive = FeatureRow('TrafficFollow', 'Following', '1.2', reason='Saved values are inactive while Custom Driving Profiles is Off')
  assert inactive.reason in description(inactive)
  pending = FeatureRow('AlwaysOnLateral', 'Always On Lateral', 'On', reason='Applies after the next startup')
  assert 'pending vehicle configuration' in description(pending)
  assert 'pending vehicle configuration' not in description(replace(pending, reason='Keep steering assistance available'))
  assert 'below' in purpose(FeatureRow('conditional:cem:speed_mps', 'Speed', '20'))
  assert 'at or above' in purpose(FeatureRow('conditional:ccm:speed_mps', 'Speed', '20'))
  camera = FeatureRow('SLCPriority1', 'Primary source', 'Vision',
                      reason='Camera signs are unavailable; choose a connected source')
  assert description(camera).startswith(purpose(camera))
  assert camera.reason in description(camera)
  ownership = FeatureRow('ForceAutoTuneOff', 'Use StarPilot Auto Learn', 'On',
                         reason='Saved for this vehicle. StarPilot Controller uses its vehicle tune; automatic learning is currently unavailable.')
  assert 'StarPilot Controller uses its vehicle tune' in description(ownership)
  assert 'Saved for this vehicle' not in description(ownership)
  maps = FeatureRow('SLCPriority2', 'Secondary source', 'Maps',
                    reason='Maps needs a verified provider and an installed offline map package')
  assert maps.reason in description(maps)


def test_actual_owner_projections_have_no_uncovered_setting(tmp_path):
  from opendbc.car.car_helpers import interfaces
  from opendbc.car.gm.values import CAR as GM
  from opendbc.car.hyundai.values import CAR as HYUNDAI
  from opendbc.car.toyota.values import CAR as TOYOTA
  from opendbc.car.ford.values import CAR as FORD
  from openpilot.common.params import Params
  from openpilot.starpilot.galaxy.settings import PAGES, _projection
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
  from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
  from openpilot.starpilot.ui.display_owner import DisplayOwner
  from openpilot.starpilot.ui.sounds_owner import SoundsOwner
  from openpilot.starpilot.ui.pip_owner import PiPOwner
  from openpilot.starpilot.ui.vasm_owner import VASMOwner
  from openpilot.starpilot.ui.sentry_owner import SentryOwner
  from openpilot.starpilot.galaxy.developer_settings import DeveloperSettingsOwner
  from openpilot.starpilot.ui.presentation import Profile
  params = Params(str(tmp_path))

  def check(rows, page):
    for row in rows:
      if row.key:
        assert purpose(row), (page, row.key)
        assert _projection(row)['reason'].startswith(purpose(row))

  check(DeveloperSettingsOwner(params, lambda: True).snapshot().rows, 'developer')

  for candidate in (GM.CHEVROLET_BOLT_EUV, GM.CHEVROLET_VOLT, HYUNDAI.HYUNDAI_IONIQ_6,
                    TOYOTA.TOYOTA_RAV4_TSS2_2023, FORD.FORD_ESCAPE_MK4):
    cp = interfaces[candidate].get_non_essential_params(candidate)
    owner = FeatureSettingsOwner(params, lambda group: True, vehicle_fingerprint=lambda cp=cp: cp.carFingerprint,
                                 vehicle_params=lambda cp=cp: cp, show_cruise_intervals=True, galaxy=lambda: True)
    for page in PAGES - {'appearance', 'display', 'sounds', 'pip', 'sentry', 'vasm'}:
      state = owner.snapshot(page, parked=True, system_long=True, lateral_context=True, metric=False)
      check(state.rows, page)
  for profile in (Profile.COMPACT, Profile.LARGE):
    check(AppearanceOwner(params, lambda: True).snapshot(profile).rows, 'appearance')
    check(DisplayOwner(params, lambda: True).snapshot(profile).rows, 'display')
  for page, owner in (('sounds', SoundsOwner(params, lambda: True)), ('pip', PiPOwner(params, lambda: True, lambda: False)),
                      ('vasm', VASMOwner(params, lambda: True)), ('sentry', SentryOwner(params, lambda: True))):
    check(owner.snapshot().rows, page)


def test_manual_coordinate_purposes_preserve_startup_and_context_warnings():
  from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
  startup = "Parked only. Saved for the next drive; zero restores this stage's original baseline. Manual overrides pause Auto Learn."
  changed = "Vehicle tune, controller or effect definition changed. Reset this manual tune before editing."
  assert set(FLM_DESCRIPTIONS) == set(NAMES)
  assert len(set(FLM_DESCRIPTIONS.values())) == len(NAMES)
  for name in NAMES:
    row = FeatureRow('torque:surface:' + name, 'Manual correction', '0', reason=startup)
    assert 'higher' in purpose(row).lower() and 'lower' in purpose(row).lower()
    assert 'Zero keeps this stage' in description(row)
    assert 'not a percentage of overall steering strength' in description(row)
    assert description(row).endswith(startup)
    assert description(replace(row, available=False, reason=changed)).endswith(changed)
  reset = FeatureRow('torque:surface:reset', 'Reset manual tune', 'Reset')
  assert purpose(reset) == 'Removes the thirty manual steering overrides while keeping saved Auto Learn history.'


def test_manual_descriptions_explain_direction_dependencies_and_pid_limits():
  for side in ('left', 'right'):
    for name in ('ff_gain_', 'turn_in_boost_', 'unwind_taper_', 'crawl_turn_in_ff_boost_',
                 'turn_in_threshold_reduction_', 'unwind_threshold_increase_', 'curvy_turn_in_trim_',
                 'curvy_unwind_floor_relief_', 'curvy_unwind_extra_reduction_'):
      assert side in FLM_DESCRIPTIONS[name + side]
    for name in ('turn_in_threshold_reduction_', 'unwind_threshold_increase_'):
      assert 'no PID friction effect' in FLM_DESCRIPTIONS[name + side]
    assert 'No effect with FF gain at zero' in FLM_DESCRIPTIONS['turn_in_boost_' + side]
    assert 'can act with FF gain at zero' in FLM_DESCRIPTIONS['crawl_turn_in_ff_boost_' + side]
  for name in ('curvy_speed_min', 'curvy_speed_max', 'curvy_turn_in_trim_speed_min', 'curvy_turn_in_trim_speed_max'):
    assert 'not cruise speed' in FLM_DESCRIPTIONS[name]


def test_alpha_longitudinal_has_one_plain_description(monkeypatch):
  from types import SimpleNamespace
  from typing import cast
  from openpilot.starpilot.ui import feature_settings_owner as owner_module
  monkeypatch.setattr(owner_module, 'read_saved', lambda *args: (b'0', True))
  owner = SimpleNamespace(_alpha_long_capability=lambda: ('qualified',), params=object(),
                          authority=lambda name: True, vehicle_fingerprint=lambda: 'HYUNDAI_IONIQ_6')
  row = owner_module.FeatureSettingsOwner._alpha_long_rows(cast(owner_module.FeatureSettingsOwner, owner), True)[0]
  expected = ('Let StarPilot control acceleration and braking instead of factory ACC. ' +
              'Takes effect next drive. May disable factory Automatic Emergency Braking.')
  assert description(row) == expected
  assert description(replace(row, available=False, reason='Current vehicle status unavailable')) == expected + ' Current vehicle status unavailable'
