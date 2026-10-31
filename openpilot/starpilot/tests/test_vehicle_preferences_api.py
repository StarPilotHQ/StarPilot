"""Existing positional vehicle startup preferences remain source-compatible."""

from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


def test_original_positional_preferences_do_not_enable_door_features():
  values = (True, False, True, False, True, False, True, False, True, False, True, False, True, 1)
  preferences = VehicleStartupPreferences(*values)
  names = (
    'toyota_auto_hold',
    'turn_assist',
    'gm_long_pitch',
    'disable_bolt_long',
    'honda_bosch_a_radar',
    'tesla_preap_stock',
    'volt_sng',
    'gm_auto_hold',
    'volt_one_pedal',
    'tesla_screen',
    'tesla_screen_brake',
    'gm_camera_pedal',
    'toyota_filter',
    'gm_longitudinal_tune',
  )
  assert tuple(getattr(preferences, name) for name in names) == values
  assert not preferences.toyota_lock_doors and not preferences.toyota_unlock_doors
  assert not preferences.gm_cancel_remap
  assert VehicleStartupPreferences(True, True).turn_assist


def test_explicit_door_keywords_do_not_shift_turn_assist():
  preferences = VehicleStartupPreferences(True, True, toyota_lock_doors=True, toyota_unlock_doors=True)
  assert preferences.toyota_auto_hold and preferences.turn_assist
  assert preferences.toyota_lock_doors and preferences.toyota_unlock_doors


def test_new_radar_keyword_preserves_all_legacy_positional_fields():
  preferences = VehicleStartupPreferences(True, True, toyota_radar_mode=2)
  assert preferences.toyota_auto_hold and preferences.turn_assist
  assert preferences.toyota_radar_mode == 2
  assert VehicleStartupPreferences(True, True).toyota_radar_mode == 0
