#!/usr/bin/env python3
"""Run explicitly scoped vehicle suites with collection and native-build evidence."""
import argparse
import ast
from collections import Counter
import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import test_runner
from tools.ci import lifecycle_shards, vehicle_pytest

# Representative production callers plus their source/native refusal cases.
# The emitted selected_test_ids record exact parametrized configurations.
AOL_LIFECYCLE_TARGETS = (
  "opendbc_repo/opendbc/car/hyundai/tests/test_torque_ev_deferred_takeover.py",
  'openpilot/starpilot/aol/tests/test_hkg_early_long_withdrawal.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_stock.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_stock_callers.py',
  'opendbc_repo/opendbc/car/toyota/tests/test_matrix_filter.py',
  'openpilot/starpilot/car/toyota/tests/test_matrix_filter_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_factory.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_fw.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_fw_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_refusals.py',
  'openpilot/starpilot/car/toyota/tests/test_radar_lta_pedal.py',
  'openpilot/starpilot/car/toyota/tests/test_radar_lta_pedal_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_rav4h_pedal_composite.py',
  'openpilot/starpilot/car/toyota/tests/test_rav4h_pedal_composite_callers.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_rav4h_pedal_composite.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_rav4h_pedal_order.py',
  'openpilot/starpilot/car/toyota/tests/test_dsu_bypass.py',
  'openpilot/starpilot/car/toyota/tests/test_dsu_bypass_callers.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_dsu_bypass.py',
  'openpilot/starpilot/car/toyota/tests/test_pedal_parent_configurations.py',
  'openpilot/starpilot/car/toyota/tests/test_pedal_parent_callers.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_pedal_parent.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_radar_lta_pedal.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_prius_retrofit_advert.py',
  'openpilot/starpilot/aol/tests/test_crossmake_native_admission.py',
  'openpilot/starpilot/aol/tests/test_hkg_source_policy_cache.py',
  'openpilot/starpilot/flm/tests/test_controls_capture_startup.py',
  'openpilot/starpilot/aol/tests/test_hkg_deadline_admission_cache.py',
  'openpilot/starpilot/aol/tests/test_hkg_native_admission.py',
  'openpilot/starpilot/aol/tests/test_native_observation_admission.py',
  'openpilot/starpilot/aol/tests/test_forte_physical_arm_join.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_canfd_display.py',
  'openpilot/starpilot/aol/tests/test_hkg_cancel_joined.py',
  'openpilot/starpilot/aol/tests/test_gv70_two_panda_long.py',
  'openpilot/starpilot/longitudinal/tests/test_gv70_stopping_parity.py',

  'opendbc_repo/opendbc/safety/tests/test_hyundai_angle_tx_recovery.py',
  'openpilot/starpilot/aol/tests/test_stock_angle_physical_recovery.py',
  'openpilot/starpilot/tests/test_ford_configuration_parity.py',
  'openpilot/starpilot/tests/test_ford_configuration_admission.py',
  'openpilot/starpilot/tests/test_ford_release_long_preferences.py',
  'openpilot/selfdrive/car/tests/test_registered_ford_discovery.py',
  'openpilot/selfdrive/car/tests/test_automatic_ford_discovery.py',
  'openpilot/starpilot/tests/test_ford_ci_registration.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_configuration_parity.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_alpha_aol.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_full_engagement.py',
  'opendbc_repo/opendbc/safety/tests/test_hkg_mode_parity.py',
  'opendbc_repo/opendbc/safety/tests/test_pe_stopped_callers.py',
  'opendbc_repo/opendbc/safety/tests/test_pe_combined_callers.py',
  'opendbc_repo/opendbc/safety/tests/test_pe_set_release_callers.py',
  'openpilot/starpilot/aol/tests/test_pe_combined_profile_contract.py',
  'opendbc_repo/opendbc/safety/tests/test_palisade_stock_aol_status.py',
  'opendbc_repo/opendbc/safety/tests/test_ev9_set_release_callers.py',
  'openpilot/starpilot/aol/tests/test_ev6_source_deadline.py',
  "openpilot/starpilot/car/rivian/tests/test_aol_policy.py",
  "openpilot/starpilot/car/rivian/tests/test_aol_native.py",
  "openpilot/starpilot/car/rivian/tests/test_aol_lifecycle.py",
  "opendbc_repo/opendbc/safety/tests/test_ev9_combined_callers.py",
  "openpilot/starpilot/aol/tests/test_ev9_main_withdrawal_guards.py",
  "openpilot/starpilot/aol/tests/test_native_inventory_control_cadence.py",
  "openpilot/starpilot/aol/tests/test_gm.py",
  "openpilot/starpilot/aol/tests/test_gm_steering_receive_order.py",
  "openpilot/starpilot/aol/tests/test_gm_card_source_gap.py",
  "openpilot/starpilot/aol/tests/test_gm_sdgm_cold_main.py",
  "openpilot/starpilot/aol/tests/test_gm_main_level_contract.py",
  "openpilot/starpilot/aol/tests/test_gm_sdgm_source_wait.py",
  "openpilot/starpilot/aol/tests/test_gm_stock_ascm_refusal.py",
  "openpilot/starpilot/aol/tests/test_transition_diagnostic.py",
  "openpilot/starpilot/aol/tests/test_gm_ascm_receive_order.py",
  "openpilot/starpilot/aol/tests/test_gm_main_response.py",
  "openpilot/starpilot/aol/tests/test_gm_held_native.py",
  'openpilot/starpilot/longitudinal/tests/test_volt_mode_transition.py',
  'openpilot/starpilot/aol/tests/test_gm_route1c_receive_order.py',

  "openpilot/starpilot/aol/tests/test_gm_ordinary_warning.py",
  "openpilot/starpilot/aol/tests/test_gm_startup_main.py",
  "openpilot/starpilot/aol/tests/test_gm_pedal_order.py",
  "openpilot/starpilot/aol/tests/test_gm_cancel_remap.py",
  "openpilot/starpilot/aol/tests/test_gm_held_brake_successor.py",
  "openpilot/starpilot/aol/tests/test_gm_lag_episode_recovery.py",
  "openpilot/starpilot/aol/tests/test_independent_lateral_soft_takeover.py",
  "openpilot/starpilot/aol/tests/test_volt_lag_pedal_intent.py",
  "openpilot/starpilot/aol/tests/test_gm_route18e_recovery.py",
  "openpilot/starpilot/aol/tests/test_stock_lateral_honda.py",
  "openpilot/starpilot/aol/tests/test_honda_reverse_join.py",
  "openpilot/starpilot/aol/tests/test_honda_recovery_lifecycle.py",
  "openpilot/starpilot/aol/tests/test_honda_classic_alpha_lifecycle.py",
  "opendbc_repo/opendbc/car/honda/tests/test_classic_alpha_aol_parity.py",
  "opendbc_repo/opendbc/car/honda/tests/test_manual_honda_port_aol.py",
  "opendbc_repo/opendbc/car/honda/tests/test_selected_honda_topology.py",
  "openpilot/starpilot/aol/tests/test_honda_pedal_lifecycle.py",
  "openpilot/starpilot/aol/tests/test_honda_canfd_lifecycle.py",
  "openpilot/starpilot/aol/tests/test_honda_alt_radar_lifecycle.py",
  "openpilot/starpilot/aol/tests/test_fresh_inactive_transport.py",
  "opendbc_repo/opendbc/car/mazda/tests/test_native_engagement_callers.py",
  "openpilot/starpilot/car/mazda/tests/test_main_startup.py",
  "opendbc_repo/opendbc/car/mazda/tests/test_dashboard_stop_sign.py",
  "opendbc_repo/opendbc/safety/tests/test_mazda_stock_aol.py",
  "openpilot/starpilot/aol/tests/test_temporary_lag_intent.py",
  "openpilot/starpilot/aol/tests/test_stock_location_recovery.py",
  "openpilot/starpilot/aol/tests/test_stock_eps_recovery.py",
  "openpilot/starpilot/aol/tests/test_stock_lateral_lag_gap_sequence.py",
  "openpilot/starpilot/aol/tests/test_chrysler.py",
  "openpilot/starpilot/aol/tests/test_toyota.py",
  "openpilot/starpilot/car/toyota/tests/test_lexus_is_caller_native.py",
  "openpilot/starpilot/car/toyota/tests/test_legacy_stop_timer.py",
  "openpilot/starpilot/car/toyota/tests/test_legacy_stop_timer_callers.py",
  "openpilot/starpilot/car/toyota/tests/test_camera_2aa_filter.py",
  "openpilot/starpilot/car/toyota/tests/test_camera_2aa_callers.py",
  "openpilot/starpilot/car/toyota/tests/test_camera_2aa_native.py",
  "openpilot/starpilot/car/toyota/tests/test_prius_sdsu_filter.py",
  "openpilot/starpilot/car/toyota/tests/test_prius_sdsu_callers.py",
  "openpilot/starpilot/car/toyota/tests/test_sng.py",
  "openpilot/starpilot/car/toyota/tests/test_sng_callers.py",
  "openpilot/starpilot/car/toyota/tests/test_brake_stop_cancel.py",
  "openpilot/starpilot/car/toyota/tests/test_prius_stop_engagement_callers.py",
  "opendbc_repo/opendbc/safety/tests/test_toyota_prius_sdsu.py",
  "openpilot/starpilot/car/toyota/tests/test_prius_advert_filter.py",
  "openpilot/starpilot/car/toyota/tests/test_prius_advert_callers.py",
  "opendbc_repo/opendbc/safety/tests/test_toyota_prius_advert.py",
  "openpilot/starpilot/car/toyota/tests/test_prius_advert_ci.py",
  "openpilot/starpilot/car/toyota/tests/test_legacy_advert_filter.py",
  "openpilot/starpilot/car/toyota/tests/test_legacy_advert_callers.py",
  "openpilot/starpilot/car/toyota/tests/test_legacy_advert_native.py",
  "openpilot/starpilot/car/toyota/tests/test_legacy_advert_lateral.py",
  "openpilot/starpilot/car/nissan/tests/test_aol.py",
  "openpilot/starpilot/aol/tests/test_volkswagen.py",
  "openpilot/starpilot/aol/tests/test_volkswagen_saved_intent.py",
  "openpilot/starpilot/aol/tests/test_mqb_actual_callers.py",
  "openpilot/starpilot/aol/tests/test_meb_actual_callers.py",
  "opendbc_repo/opendbc/car/volkswagen/tests/test_meb_preferences.py",
  "opendbc_repo/opendbc/car/volkswagen/tests/test_meb_discovery.py",
  "opendbc_repo/opendbc/car/volkswagen/tests/test_meb_stock_modes.py",
  "openpilot/starpilot/ui/tests/test_meb_preferences.py",
  "openpilot/starpilot/aol/tests/test_mqb_direct_callers.py",
  "openpilot/starpilot/aol/tests/test_mqb_topology_callers.py",
  "openpilot/starpilot/aol/tests/test_mqb_topology_contract.py",
  "opendbc_repo/opendbc/car/volkswagen/tests/test_mqb_topology_discovery.py",
  "opendbc_repo/opendbc/car/volkswagen/tests/test_mqb_aol_discovery.py",
  "opendbc_repo/opendbc/safety/tests/test_volkswagen_stock_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_volkswagen_engagement_arm.py",
  "opendbc_repo/opendbc/safety/tests/test_volkswagen_long_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_meb_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_volkswagen_direct_aol.py",
  "openpilot/starpilot/aol/tests/test_volkswagen_main_boot.py",
  "openpilot/starpilot/car/subaru/tests/test_aol.py",
  "openpilot/starpilot/car/subaru/tests/test_reverse_join.py",
  "openpilot/starpilot/car/subaru/tests/test_stock_aol_callers.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_stock_aol.py",
  "openpilot/starpilot/car/subaru/tests/test_software_cruise.py",
  "openpilot/starpilot/car/subaru/tests/test_software_cruise_callers.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_software_cruise.py",
  "openpilot/starpilot/car/subaru/tests/test_sng.py",
  "openpilot/starpilot/car/subaru/tests/test_sng_callers.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_sng.py",
  "openpilot/starpilot/car/subaru/tests/test_stop_start.py",
  "openpilot/starpilot/car/subaru/tests/test_stop_start_callers.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_stop_start.py",
  "openpilot/starpilot/car/subaru/tests/test_avh.py",
  "openpilot/starpilot/car/subaru/tests/test_avh_callers.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_avh_startup.py",
  "openpilot/starpilot/car/subaru/tests/test_angle_tune.py",
  "openpilot/starpilot/car/subaru/tests/test_forester_angle_callers.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_forester_angle.py",
  "openpilot/starpilot/car/subaru/tests/test_outback_angle_callers.py",
  "openpilot/starpilot/car/subaru/tests/test_outback_startup_callers.py",
  "openpilot/starpilot/car/subaru/tests/test_smoothing.py",
  "openpilot/starpilot/car/subaru/tests/test_smoothing_factory.py",
  "openpilot/starpilot/car/subaru/tests/test_smoothing_settings.py",
  "openpilot/starpilot/car/subaru/tests/test_smoothing_model_join.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_outback_angle.py",
  'openpilot/starpilot/car/toyota/tests/test_matrix_stock_caller_native.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_matrix_stock_aol.py',
)

AOL_LIFECYCLE_SCOPE = {
  "ford": "14 stock/Alpha identities, RELEASE CAN-FD Alpha, auto/manual, one/two Pandas; actual acquisition and caller/native lifecycle guards",
  "rivian": ("GEN1 base stock/DEBUG Alpha: actual factory/Card/SD/Controls/native engagement, brake LAT-only, gas, standstill zero output, " +
             "Reverse/Drive, temporary ToI recovery and physical UP_1 cancel; GEN2/unknown profiles refused"),
  "mazda": ("CX5 2022/CX9 2021 GEN1 stock AE32: engagement/brake/Cancel pause/toggle/Reverse/standstill/EPS recovery; " +
            "native permanent-fault rearm vetoes"),
  "subaru": ("eleven stock global identities; automatic/manual and AOL on/off factory-to-native lifecycles, " +
               "startup requests, frozen smoothing, brake, Reverse/Drive, EPS recovery, MAIN-off and refusal guards"),
  "volkswagen": "stock MQB auto/manual, gateway/camera factory acquisition, MAIN/engagement/brake/Reverse joins, permanent EPS native rearm",
  "gm": "Volt/Bolt factory profiles, MAIN boot, stock/Alpha, BE/C9, pedal/regen, Cancel mappings, RES, lag withdrawal/recovery",
  "honda": "stock Civic source/native vetoes, native-first Reverse joins, and EPS-recovery/Reverse/Drive native TX lifecycle",
  "hyundai": "Ioniq stock/OP host lag plus classic/CAN-FD stock source-gap and EPS/location recovery",
  "ford_chrysler": "stock PCM-off EPS/location joins; Chrysler factory/parser/controller OFF and Cancel behavior",
  "toyota": "stock torque/angle native-ack vetoes, source-gap recovery, Lexus IS DSU actual DEBUG/RELEASE native callers",
  "nissan": "five stock identities, real acquisition/parser/controller/native optional paths",
}

DEBUG_SAFETY_CLASS_TARGETS = {
  'opendbc_repo/opendbc/safety/tests/test_hyundai_gv60_long.py': ('opendbc_repo/opendbc/safety/tests/test_hyundai_gv60_long.py::TestGV60LongDebug',),
  'opendbc_repo/opendbc/safety/tests/test_pe_combined_aol.py': ('opendbc_repo/opendbc/safety/tests/test_pe_combined_aol.py::TestPECombinedDebug',),
  "opendbc_repo/opendbc/safety/tests/test_ev9_combined_aol.py": ("opendbc_repo/opendbc/safety/tests/test_ev9_combined_aol.py::TestEV9CombinedDebug",),
  "opendbc_repo/opendbc/safety/tests/test_hyundai_ev9_long.py": (
    "opendbc_repo/opendbc/safety/tests/test_hyundai_ev9_long.py::TestEv9LongDebugContract",
  ),
  "opendbc_repo/opendbc/safety/tests/test_hyundai_main_rearm.py": (
    "opendbc_repo/opendbc/safety/tests/test_hyundai_main_rearm.py::TestHkgMainDebug",
  ),
  "opendbc_repo/opendbc/safety/tests/test_hyundai_classic_cancel_joined.py": (
    "opendbc_repo/opendbc/safety/tests/test_hyundai_classic_cancel_joined.py::TestClassicCancelDebug",
  ),
  "opendbc_repo/opendbc/safety/tests/test_ioniq5pe_paired_native.py": ("opendbc_repo/opendbc/safety/tests/test_ioniq5pe_paired_native.py::TestPELongDebug",),
}


RELEASE_TARGETS = (
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_stock.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_stock_callers.py',
  'opendbc_repo/opendbc/car/toyota/tests/test_matrix_filter.py',
  'openpilot/starpilot/car/toyota/tests/test_matrix_filter_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_factory.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_fw.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_fw_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_retrofit_advert_refusals.py',
  'openpilot/starpilot/car/toyota/tests/test_radar_lta_pedal.py',
  'openpilot/starpilot/car/toyota/tests/test_radar_lta_pedal_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_rav4h_pedal_composite.py',
  'openpilot/starpilot/car/toyota/tests/test_rav4h_pedal_composite_callers.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_rav4h_pedal_composite.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_rav4h_pedal_order.py',
  'openpilot/starpilot/car/toyota/tests/test_dsu_bypass.py',
  'openpilot/starpilot/car/toyota/tests/test_dsu_bypass_callers.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_dsu_bypass.py',
  'openpilot/starpilot/car/toyota/tests/test_pedal_parent_configurations.py',
  'openpilot/starpilot/car/toyota/tests/test_pedal_parent_callers.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_pedal_parent.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_radar_lta_pedal.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_prius_retrofit_advert.py',
  'opendbc_repo/opendbc/car/hyundai/tests/test_long_cp_copy.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_canfd_display.py',
  "opendbc_repo/opendbc/safety/tests/test_toyota_prius_advert.py",
  'opendbc_repo/opendbc/safety/tests/test_hyundai_gv60_long.py::TestGV60LongReleaseDenied',
  'openpilot/starpilot/tests/test_ford_configuration_parity.py',
  'openpilot/starpilot/tests/test_ford_configuration_admission.py',
  'openpilot/starpilot/tests/test_ford_release_long_preferences.py',
  'openpilot/selfdrive/car/tests/test_registered_ford_discovery.py',
  'openpilot/selfdrive/car/tests/test_automatic_ford_discovery.py',
  'openpilot/starpilot/tests/test_ford_ci_registration.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_configuration_parity.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_alpha_aol.py',
  'opendbc_repo/opendbc/safety/tests/test_ford_full_engagement.py',
  'opendbc_repo/opendbc/safety/tests/test_pe_combined_aol.py::TestPECombinedReleaseDenied',
  'openpilot/starpilot/aol/tests/test_pe_combined_profile_contract.py',
  'opendbc_repo/opendbc/safety/tests/test_palisade_stock_aol_status.py',
  'openpilot/starpilot/aol/tests/test_ev6_source_deadline.py',
  "openpilot/starpilot/aol/tests/test_ev9_main_withdrawal_guards.py",
  "opendbc_repo/opendbc/safety/tests/test_ev9_combined_aol.py::TestEV9CombinedReleaseDenied",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ev9_combined_aol.py::TestEV9CombinedProfile",
  "opendbc_repo/opendbc/car/hyundai/tests/test_gv60_paired_startup.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ev9_pending_protocol.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ev9_pending_startup.py::TestEV9NormalSelection",
  "opendbc_repo/opendbc/car/hyundai/tests/test_batched_ready_observations.py",
  "openpilot/starpilot/aol/tests/test_canfd_location_native_recovery.py",
  "openpilot/starpilot/aol/tests/test_classic_location_stock_intent.py",
  "openpilot/starpilot/aol/tests/test_stock_location_family_intent.py",
  'opendbc_repo/opendbc/car/hyundai/tests/test_generic_hda2_startup.py',
  'opendbc_repo/opendbc/car/hyundai/tests/test_generic_hda2_startup_guards.py',
  'opendbc_repo/opendbc/car/hyundai/tests/test_generic_hda2_startup_lifecycle.py::test_exact_factory_stock_copy_and_owner_preserve_all_params',
  'opendbc_repo/opendbc/car/hyundai/tests/test_generic_hda2_startup_lifecycle.py::test_actual_card_rejected_generic_hda2_restores_stock',
  "opendbc_repo/opendbc/car/hyundai/tests/test_ioniq5pe_ready_stock_contract.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_radar_hda2_startup.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_radar_hda2_startup_guards.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_radar_hda2_startup_lifecycle.py::test_exact_factory_stock_copy_and_owner_preserve_all_params",
  "openpilot/starpilot/aol/tests/test_location_input_intent.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_startup_owner_current_admission.py::test_actual_card_only_publishes_prepared_takeover_with_current_owner_evidence[panda-g90]",
  "opendbc_repo/opendbc/car/hyundai/tests/test_startup_owner_current_admission.py::test_actual_card_only_publishes_prepared_takeover_with_current_owner_evidence[panda-ev6-1p]",
  "opendbc_repo/opendbc/car/hyundai/tests/test_startup_owner_current_admission.py::test_actual_card_only_publishes_prepared_takeover_with_current_owner_evidence[panda-gv70-1p]",
  "opendbc_repo/opendbc/car/hyundai/tests/test_startup_owner_current_admission.py::test_actual_card_only_publishes_prepared_takeover_with_current_owner_evidence[panda-ev6-init-1p]",
  "opendbc_repo/opendbc/car/hyundai/tests/test_startup_owner_current_admission.py::test_actual_card_only_publishes_prepared_takeover_with_current_owner_evidence[panda-ev6-2p]",
  "opendbc_repo/opendbc/car/hyundai/tests/test_startup_owner_current_admission.py::test_actual_card_only_publishes_prepared_takeover_with_current_owner_evidence[panda-gv70-2p]",
  "opendbc_repo/opendbc/car/hyundai/tests/test_startup_owner_current_admission.py::test_actual_card_only_publishes_prepared_takeover_with_current_owner_evidence[panda-ev6-init-2p]",
  "opendbc_repo/opendbc/car/hyundai/tests/test_radar_hda1_startup.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_radar_hda1_startup_guards.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_radar_hda1_startup_lifecycle.py::test_exact_factory_stock_copy_and_owner_preserve_all_params",
  "opendbc_repo/opendbc/safety/tests/test_toyota_pedal.py",
  "opendbc_repo/opendbc/safety/tests/test_toyota_lexus_is.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ordinary_classic_startup_contract.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ordinary_classic_startup_protocol.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ordinary_classic_stock_fallback.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ordinary_classic_guards.py",
  "openpilot/starpilot/aol/tests/test_native_inventory_control_cadence.py",
  "openpilot/starpilot/aol/tests/test_classic_family_button_scope.py",
  "openpilot/starpilot/aol/tests/test_hkg_independent_cancel_contract.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_restore_session_settle.py",
  "openpilot/starpilot/aol/tests/test_sonata_hybrid_cancel_scope.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_sibling_continuity.py",
  "openpilot/starpilot/aol/tests/test_sonata_stock_transition.py",
  "openpilot/starpilot/aol/tests/test_classic_stock_full_engagement.py",
  "openpilot/starpilot/aol/tests/test_sonata_cruise_only_withdrawal.py",
  "openpilot/starpilot/aol/tests/test_classic_scc_mapped_buttons.py",
  "openpilot/starpilot/aol/tests/test_sonata_mapped_button_callers.py",
  "openpilot/starpilot/aol/tests/test_sonata_temporary_withdrawals.py",
  "openpilot/starpilot/aol/tests/test_expired_axis_zero_request.py",
  "openpilot/starpilot/aol/tests/test_independent_lateral_soft_takeover.py",
  "tools/ci/test_classic_startup_release.py",
  "openpilot/starpilot/aol/tests/test_classic_startup_neutral_replacement.py::test_classic_initializing_never_bypasses_current_health",
  "openpilot/starpilot/tests/test_ev6_startup.py",
  "openpilot/starpilot/aol/tests/test_ev6_stock_parity.py",
  "openpilot/starpilot/aol/tests/test_ev6_physical_authorization.py",
  "opendbc_repo/opendbc/safety/tests/test_classic_scc_main_ingress.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_classic_dash_late_configuration.py",
  "opendbc_repo/opendbc/car/toyota/tests/test_rav4h_filter.py",
  "opendbc_repo/opendbc/safety/tests/test_toyota_rav4h_filter.py",
  "openpilot/starpilot/aol/tests/test_sonata_irq_callers.py",
  "openpilot/starpilot/aol/tests/test_sonata_irq_policy.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_classic_aol_dash.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_g90_continuity.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_classic_deferred_takeover.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_torque_ev_deferred_takeover.py::test_release_native_refuses_alpha_torque_long",
  "opendbc_repo/opendbc/car/hyundai/tests/test_disable_response.py",
  "openpilot/starpilot/aol/tests/test_angle_stop_native_feedback.py",
  "openpilot/starpilot/aol/tests/test_angle_stop_fault_guards.py",
  "openpilot/starpilot/aol/tests/test_torque_stock_cruise_transition.py",
  "openpilot/starpilot/aol/tests/test_torque_stock_brake_guards.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_torque_stock_transition.py",
  "openpilot/starpilot/aol/tests/test_angle_stock_cruise_transition.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_stock_cruise_transition.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ioniq5pe_replacement_duplicates.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ioniq5pe_pending_startup.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_angle_cancel.py",
  "openpilot/starpilot/aol/tests/test_angle_cancel.py",
  "openpilot/starpilot/aol/tests/test_angle_cancel_batches.py",
  "openpilot/starpilot/aol/tests/test_angle_brake_deadline.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_main_rearm.py::TestHkgMainRelease",
  "opendbc_repo/opendbc/car/hyundai/tests/test_long_main_buttons.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_classic_cancel_joined.py::TestClassicCancelReleaseDenied",
  "opendbc_repo/opendbc/car/hyundai/tests/test_classic_cancel_construction.py",
  "openpilot/starpilot/aol/tests/test_classic_cancel_guards.py",
  "opendbc_repo/opendbc/safety/tests/test_ioniq5pe_paired_native.py::TestPELongReleaseDenied",
  "opendbc_repo/opendbc/safety/tests/test_tesla_preap_pedal.py",
  "openpilot/starpilot/car/tesla/tests/test_pedal_callers.py",
  "openpilot/starpilot/car/tesla/tests/test_pedal_planner.py",
  "openpilot/starpilot/car/tesla/tests/test_preap_adaptive.py",
  "openpilot/starpilot/car/tesla/tests/test_preap_pause_callers.py",
  "openpilot/starpilot/car/tesla/tests/test_pedal_schema_evolution.py",
  "openpilot/starpilot/car/tesla/tests/test_radar_schema_evolution.py",
  "opendbc_repo/opendbc/safety/tests/test_tesla_preap_stock.py",
  "opendbc_repo/opendbc/safety/tests/test_tesla_preap_radar.py",
  "opendbc_repo/opendbc/car/tesla/tests/test_preap_radar.py",
  "openpilot/starpilot/car/tesla/tests/test_radar_settings.py",
  "openpilot/starpilot/car/tesla/tests/test_radar_context.py",
  "openpilot/starpilot/car/tesla/tests/test_radar_startup.py",
  "openpilot/starpilot/car/tesla/tests/test_radar_pipeline.py",
  "openpilot/starpilot/ui/tests/test_preap_radar_feature.py",
  "opendbc_repo/opendbc/car/tesla/tests/test_preap_startup.py",
  "opendbc_repo/opendbc/car/tesla/tests/test_preap_stock.py",
  "openpilot/starpilot/car/tesla/tests/test_preap_intent.py",
  "openpilot/starpilot/car/tesla/tests/test_stock_events.py",
  "opendbc_repo/opendbc/car/ford/tests/test_aol_startup.py",
  "openpilot/starpilot/tests/test_ford_aol_intent.py",
  "opendbc_repo/opendbc/safety/tests/test_ford_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_ford.py::TestFordCANFDLongitudinalSafety",
  "opendbc_repo/opendbc/car/ford/tests/test_added_firmware_identity.py",
  "opendbc_repo/opendbc/car/ford/tests/test_required_source_health.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_blended_stock_aol.py",
  "openpilot/starpilot/aol/tests/test_nexo_stock_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_nexo_stock_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_stock_angle_aol.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_stock_angle_aol.py",
  "openpilot/starpilot/aol/tests/test_stock_angle_pedals.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_g90_startup.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_shared_session_recovery_contract.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_ev9_ready.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_dom_radar_vehicle_bindings.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_dom_radar_complete_paths.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_dom_firmware_catalog.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_classic_long_aol.py",
  "openpilot/starpilot/aol/tests/test_classic_long_profiles.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_shared_canfd_stock_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_hda2_long_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_prepared_torque_ev_native.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_shared_canfd_stock_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_classic_scc_aol.py",
  "openpilot/starpilot/aol/tests/test_classic_scc_profiles.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_blended.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_blended_alpha.py::TestHyundaiBlendedAlphaAol",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_blended_alpha.py::TestHyundaiBlendedHda2Integrity",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_ev9_long.py::TestEv9LongReleaseDenied",
  "opendbc_repo/opendbc/car/hyundai/tests/test_palisade_2023.py",
  "openpilot/starpilot/longitudinal/tests/test_output_max.py",
  "openpilot/starpilot/ui/tests/test_output_max_feature.py",
  "openpilot/starpilot/tests/test_output_max_namespace.py",
  "opendbc_repo/opendbc/car/gm/tests/test_conventional_pedal_transitions.py",
  "opendbc_repo/opendbc/car/gm/tests/test_conventional_pedal_disabled.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_cc_pedal_disabled.py",
  "opendbc_repo/opendbc/car/gm/tests/test_silverado_cc_pedal.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_silverado_pedal.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_cc_pedal.py",
  "opendbc_repo/opendbc/car/gm/tests/test_conventional_pedal.py",
  "openpilot/starpilot/tests/test_gm_conventional_pedal_startup.py",
  "tools/ci/test_release_safety.py",
  "opendbc_repo/opendbc/safety/tests/test_defaults.py::TestNoOutput",
  "opendbc_repo/opendbc/safety/tests/test_defaults.py::TestSilent",
  "opendbc_repo/opendbc/safety/tests/test_tesla.py::TestTeslaStockSafety",
  "opendbc_repo/opendbc/safety/tests/test_tesla_hw1.py",
  "opendbc_repo/opendbc/safety/tests/test_volvo_c1.py",
  "opendbc_repo/opendbc/safety/tests/test_volvo_cma.py",
  "opendbc_repo/opendbc/safety/tests/test_volkswagen_stock_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_volkswagen_engagement_arm.py",
  "opendbc_repo/opendbc/safety/tests/test_volkswagen_long_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_meb_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_volkswagen_direct_aol.py",
  "openpilot/starpilot/aol/tests/test_volkswagen_main_boot.py",
  "opendbc_repo/opendbc/safety/tests/test_mazda.py::TestMazdaSafety",
  "opendbc_repo/opendbc/safety/tests/test_toyota.py::TestToyotaStockLongitudinalTorque",
  "opendbc_repo/opendbc/safety/tests/test_toyota_auto_hold.py",
  "opendbc_repo/opendbc/safety/tests/test_gm.py::TestGmCameraSafety",
  "opendbc_repo/opendbc/safety/tests/test_gm.py::TestGmCameraEVSafety",
  "opendbc_repo/opendbc/safety/tests/test_gm_bolt_pedal.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_bolt_cc.py",
  "opendbc_repo/opendbc/car/gm/tests/test_bolt_cc_low.py",
  "openpilot/starpilot/tests/test_bolt_cc_controls_low.py",
  "openpilot/starpilot/tests/test_bolt_cc_controls_manual.py",
  "openpilot/starpilot/car/gm/tests/test_gear_events.py",
  "openpilot/starpilot/tests/test_bolt_disable_active_pedal.py::TestBoltDisableActivePedal",
  "opendbc_repo/opendbc/car/gm/tests/test_bolt_acc_cc.py",
  "opendbc_repo/opendbc/car/gm/tests/test_bolt_factory_acc.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_bolt_factory_stock.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_bolt_euv.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_volt_cc.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_volt_cc_gas.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_ascm_intercept.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_sdgm_stock.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_cc_gateway_stock.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_stock_cancel_delay.py",
  "openpilot/starpilot/tests/test_gm_stock_caller.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_camera_stock_four.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_volt_gateway_mapping.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_volt_alternate_brake.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_volt_ascm.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_ordinary_ascm.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_ordinary_sdgm.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_ordinary_cc.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_hybrid_cc.py",
  "opendbc_repo/opendbc/car/gm/tests/test_ordinary_cc.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_ordinary_camera.py",
  "opendbc_repo/opendbc/car/gm/tests/test_ordinary_camera.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_ordinary_camera_removed.py",
  "opendbc_repo/opendbc/car/gm/tests/test_ordinary_camera_removed.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_ascm_aol.py",
  "opendbc_repo/opendbc/car/gm/tests/test_ordinary_ascm.py",
  "openpilot/starpilot/longitudinal/tests/test_gm_suburban_outer.py",
  "opendbc_repo/opendbc/car/gm/tests/test_ordinary_sdgm.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_volt_camera.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_volt_camera_removed.py",
  "opendbc_repo/opendbc/car/gm/tests/test_volt_camera_removed.py",
  "opendbc_repo/opendbc/safety/tests/test_gm_volt_sdgm.py",
  "opendbc_repo/opendbc/car/gm/tests/test_volt_sdgm_control.py",
  "opendbc_repo/opendbc/car/gm/tests/test_stock_ascm_steering_authority.py",
  "opendbc_repo/opendbc/car/gm/tests/test_sdgm_brake_reacquire.py",
  "opendbc_repo/opendbc/car/gm/tests/test_volt_camera_control.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_mrr35_angle_four.py",
  "opendbc_repo/opendbc/safety/tests/test_kia_ev6_2025_angle.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_angle_hybrids_three.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_ccnc_angle_two.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_ioniq6_long.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_ray_pedal.py",
  "opendbc_repo/opendbc/safety/tests/test_honda_mvl_stock.py",
  "opendbc_repo/opendbc/safety/tests/test_honda_stock_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_mazda_stock_aol.py",
  "opendbc_repo/opendbc/car/mazda/tests/test_stock_aol_pause.py",
  "opendbc_repo/opendbc/safety/tests/test_honda_rdx_gas_cap.py",
  "opendbc_repo/opendbc/safety/tests/test_honda_aol_source.py",
  "opendbc_repo/opendbc/safety/tests/test_honda_aol_family.py",
  "opendbc_repo/opendbc/safety/tests/test_aol_defensive_config.py",
  "opendbc_repo/opendbc/safety/tests/test_honda_nidec_six.py",
  "opendbc_repo/opendbc/safety/tests/test_honda_interceptor.py",
  "opendbc_repo/opendbc/safety/tests/test_honda_bosch_radarless_three.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_gen2_angle_pair.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_ascent_angle.py",
  "opendbc_repo/opendbc/safety/tests/test_ford_three.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_canfd_carnival.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_stock_aol.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_software_cruise.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_sng.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_stop_start.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_avh_startup.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_forester_angle.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_receive_guards.py",
  "opendbc_repo/opendbc/safety/tests/test_subaru_outback_angle.py",
  'openpilot/starpilot/car/toyota/tests/test_matrix_stock_caller_native.py',
  'opendbc_repo/opendbc/safety/tests/test_toyota_matrix_stock_aol.py',
)

# Vehicle tests import both the vendored car code and these host control owners.
# Presentation work can proceed independently; native binaries have separate
# provenance below. Keep result directories outside these source areas.
SOURCE_INPUT_PATHS = (
  "opendbc_repo", "openpilot/cereal", "openpilot/common", "openpilot/selfdrive/car",
  "openpilot/selfdrive/controls", "openpilot/selfdrive/locationd", "openpilot/selfdrive/selfdrived",
  "openpilot/starpilot/aol", "openpilot/starpilot/car", "openpilot/starpilot/lateral", "openpilot/starpilot/longitudinal",
  "openpilot/starpilot/speed_limits", "openpilot/starpilot/schema_cache.py", "msgq_repo", "rednose_repo",
  "openpilot/starpilot/vehicle_preferences.py",
  "openpilot/starpilot/vehicle_startup.py", "openpilot/starpilot/controller_extensions.py",
  "tools/ci", "tools/test_runner.py", "pyproject.toml", "uv.lock", "upstream-sync.json",
)


def source_input_snapshot():
  def git(*args):
    return subprocess.check_output(["git", "--no-optional-locks", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args], cwd=ROOT)

  untracked = {}
  for raw_path in git("ls-files", "--others", "--exclude-standard", "-z", "--", *SOURCE_INPUT_PATHS).split(b"\0"):
    if raw_path:
      name = os.fsdecode(raw_path)
      path = ROOT / name
      data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
      untracked[name] = hashlib.sha256(data).hexdigest()
  patch_bytes = git("diff", "--no-renames", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", "--", *SOURCE_INPUT_PATHS)
  return {"revision": git("rev-parse", "HEAD").decode().strip(), "paths": list(SOURCE_INPUT_PATHS),
          "tracked_patch_sha256": hashlib.sha256(patch_bytes).hexdigest(), "untracked_sha256": untracked}


def sha256(path):
  return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def targets_for(suite):
  if suite == "aol-lifecycle":
    return list(AOL_LIFECYCLE_TARGETS)
  if suite == "interfaces":
    car_root = ROOT / "opendbc_repo/opendbc/car"
    return [str(path.relative_to(ROOT)) for path in sorted(car_root.rglob("test_*.py"))
            if path != car_root / "tests/test_models.py"]
  if suite == "safety-debug":
    paths = [str(path.relative_to(ROOT)) for path in sorted((ROOT / "opendbc_repo/opendbc/safety/tests").glob("test_*.py"))]
    return [selected for path in paths for selected in DEBUG_SAFETY_CLASS_TARGETS.get(path, (path,))] + [
      "openpilot/starpilot/aol/tests/test_classic_startup_neutral_replacement.py",
    ]
  if suite == "safety-release":
    return list(RELEASE_TARGETS)
  raise ValueError(f"Unknown vehicle test suite: {suite}")


def interface_class_names(path, class_name):
  """Resolve concrete parameterized classes without accepting unrelated name prefixes."""
  concrete = set()
  path = path.resolve()
  for module in tuple(sys.modules.values()):
    source = getattr(module, "__file__", None)
    if not source or Path(source).resolve() != path:
      continue
    template = getattr(module, class_name, None)
    if not isinstance(template, type) or template.__dict__.get("__test__") is not False:
      continue
    for name, candidate in vars(module).items():
      if (isinstance(candidate, type) and candidate.__bases__ == (template,) and
          candidate.__module__ == module.__name__ and candidate.__name__ == name and
          name.startswith(class_name + "_") and len(name) > len(class_name) + 1 and
          candidate.__dict__.get("__test__") is True):
        concrete.add(name)
  return sorted(concrete) or [class_name]


def audit_interface_modules(targets, test_ids):
  """Expose empty modules and test declarations unsupported by unittest discovery."""
  modules, errors = [], []
  for target in targets:
    path = ROOT / target
    module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
    selected = [test_id for test_id in test_ids if test_id.startswith(module + ".")]
    declarations = ast.parse(path.read_text(), filename=str(path))
    functions, missing_methods = [], []
    for node in declarations.body:
      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
        functions.append(node.name)
      elif isinstance(node, ast.ClassDef):
        class_names = interface_class_names(path, node.name)
        for method in node.body:
          if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test_"):
            for class_name in class_names:
              expected = f"{module}.{class_name}.{method.name}"
              if not any(test_id == expected or test_id.startswith((expected + "_", expected + "[")) for test_id in selected):
                missing_methods.append(f"{class_name}.{method.name}")
    if not selected:
      errors.append(f"Interface test module collected zero tests: {target}")
    for function in functions:
      expected = f"{module}.{function}"
      if not any(test_id == expected or test_id.startswith(expected + "[") for test_id in selected):
        errors.append(f"Declared module-level test was not collected: {target}: {function}")
    if missing_methods:
      errors.append(f"Declared interface tests were not collected: {target}: {missing_methods}")
    modules.append({"path": target, "source_sha256": sha256(path), "collected_count": len(selected),
                    "module_level_test_functions": functions, "uncollected_test_methods": missing_methods})
  return modules, errors


def interface_coverage(test_ids, platforms):
  prefix = ".TestCarInterfaces.test_car_interfaces_"
  matches = [(test_id.split(prefix, 1)[1], test_id) for test_id in test_ids if prefix in test_id]
  counts = Counter(platform for platform, _ in matches)
  if set(counts) != set(platforms) or any(count != 1 for count in counts.values()):
    missing, extra = sorted(set(platforms) - counts.keys()), sorted(counts.keys() - set(platforms))
    duplicates = sorted(platform for platform, count in counts.items() if count != 1)
    raise ValueError(f"Concrete interface collection mismatch: missing={missing}, extra={extra}, duplicates={duplicates}")
  return dict(matches)


def interface_execution_errors(platform_tests, records):
  by_id = {record["id"]: record for record in records}
  return [f"Concrete interface test for {platform} must pass: {by_id.get(test_id, {}).get('status', 'missing')} ({test_id})"
          for platform, test_id in platform_tests.items() if by_id.get(test_id, {}).get("status") != "passed"]


def vehicle_inventory():
  from opendbc.car.values import PLATFORMS
  from opendbc.car.tests.routes import non_tested_cars, routes
  return {"platform_ids": sorted(PLATFORMS), "platform_count": len(PLATFORMS),
          "platforms_without_route": sorted(set(PLATFORMS) - {str(route.car_model) for route in routes}),
          "declared_route_exemptions": sorted(str(platform) for platform in non_tested_cars)}


def source_provenance():
  def git(*args):
    return subprocess.check_output(["git", "--no-optional-locks", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args], cwd=ROOT).decode().strip()
  manifest = ROOT / "upstream-sync.json"
  return {"revision": git("rev-parse", "HEAD"), "status": git("status", "--porcelain"),
          "inputs": source_input_snapshot(),
          "manifest_sha256": sha256(manifest), "dependencies": json.loads(manifest.read_text())["dependencies"],
          "runner_sha256": sha256(ROOT / "tools/test_runner.py"), "suite_runner_sha256": sha256(__file__),
          "pytest_runner_sha256": sha256(ROOT / "tools/ci/vehicle_pytest.py"),
          "shard_runner_sha256": sha256(ROOT / "tools/ci/lifecycle_shards.py")}


def native_provenance():
  import capnp.lib.capnp as native_capnp
  from opendbc.can import packer, parser
  path = Path(native_capnp.__file__).resolve()
  if not any(str(path).endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES):
    raise RuntimeError("Vehicle tests require the native pycapnp extension")
  expected = ROOT / "opendbc_repo/opendbc/can"
  if Path(packer.__file__).resolve().parent != expected or Path(parser.__file__).resolve().parent != expected:
    raise RuntimeError("Vehicle tests imported CAN code from outside the tracked dependency")
  return {"pycapnp": {"path": str(path), "sha256": sha256(path)},
          "can_implementation": "tracked upstream Python parser/packer", "can_sources": {
            str(Path(module.__file__).relative_to(ROOT)): sha256(module.__file__) for module in (packer, parser)}}


def build_safety_library(release, output):
  from opendbc.car.structs import CarParams
  from opendbc.safety.tests.libsafety import libsafety_py
  commands = []
  check_call = subprocess.check_call

  def record_command(command, *args, **kwargs):
    commands.append(list(command))
    return check_call(command, *args, **kwargs)

  with patch.object(libsafety_py.subprocess, "check_call", side_effect=record_command):
    built = Path(libsafety_py._build_libsafety(release=release))
  path = output / ("libsafety-release.so" if release else "libsafety-debug.so")
  shutil.copyfile(built, path)
  built.unlink()
  defines_debug = any("-DALLOW_DEBUG" in command for command in commands)
  if defines_debug == release or len(commands) != 2:
    raise RuntimeError("Safety build did not use the requested compile variant")
  libsafety_py.load(path)
  status = libsafety_py.libsafety.set_safety_hooks(CarParams.SafetyModel.allOutput, 0)
  if (status == 0) != (not release):
    raise RuntimeError("Loaded safety library does not match the requested debug/release registry")
  return {"variant": "release" if release else "ALLOW_DEBUG", "path": str(path), "sha256": sha256(path),
          "commands": commands, "compiler": shutil.which(commands[0][0]),
          "compiler_version": subprocess.check_output([commands[0][0], "--version"], text=True).strip(),
          "all_output_hook_available": status == 0,
          "loader_sha256": sha256(libsafety_py.__file__)}


def build_aol_protocol(output):
  """The existing C++ protocol contract is part of the same lifecycle gate."""
  executable = output / "test_aol_protocol"
  command = ["c++", "-std=c++17", "-I.", "-Iopenpilot", "-Iopendbc_repo",
             "openpilot/selfdrive/pandad/tests/test_aol_protocol.cc", "-o", str(executable)]
  subprocess.check_call(command, cwd=ROOT)
  subprocess.check_call([str(executable)], cwd=ROOT)
  return {"command": command, "path": str(executable), "sha256": sha256(executable)}


def run(suite, output, collect_only=False, shard_index=None, shard_count=4):
  if shard_index is not None and (suite not in ("aol-lifecycle", "safety-release") or not 0 <= shard_index < shard_count):
    raise ValueError("Lifecycle shard index is outside its catalog")
  output = Path(output).resolve()
  output.mkdir(parents=True, exist_ok=True)
  started = time.monotonic()
  tests, errors = [], []
  pytest_nodeids = []
  records, platform_tests, gate_errors = [], {}, []
  phase = "initialization"
  plan = {"schema_version": 1, "suite": suite, "status": "initializing", "workers": 1,
          "scope": "host synthetic interfaces" if suite == "interfaces" else "host safety hooks",
          "uncovered": ["historical route replay", "hardware and bus integration", "additional downstream vehicles",
                        "independent lateral/longitudinal feature transitions", "device firmware execution"],
          "targets": [], "selected_test_ids": [], "collection_errors": [],
          "fuzz_seed": os.environ.get("FUZZ_SEED"), "max_examples_override": os.environ.get("MAX_EXAMPLES")}
  if suite in ("aol-lifecycle", "safety-release"):
    plan["scope"] = "selected actual vehicle callers with synthetic receive/readiness timelines; native hooks where exercised by the selected suites"
    plan["lifecycle_rows"] = AOL_LIFECYCLE_SCOPE
    plan["uncovered"] = ["complete recorded routes and real-time device deadlines", "physical firmware/harness acceptance",
                         "unselected exact variants, including new G90/GV70 report configurations",
                         "unselected Mazda/Volkswagen profiles; Rivian, Subaru and Tesla complete joined lifecycles"]
  if suite == "safety-release":
    plan["uncovered"].append("full release-mode behavioral safety suite; only explicit selected classes run")
  (output / "plan.json").write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
  try:
    plan["targets"] = targets_for(suite)
    plan["source"] = source_provenance()
    if suite in ("aol-lifecycle", "safety-release"):
      plan["source"]["build_identity"] = lifecycle_shards.build_identity(ROOT)
    plan.update(vehicle_inventory())
    plan["native"] = native_provenance()
    if suite != "interfaces" and not collect_only:
      plan["native"]["safety"] = build_safety_library(suite == "safety-release", output)
    if suite == "aol-lifecycle" and not collect_only:
      plan["native"]["aol_protocol"] = build_aol_protocol(output)
    if suite in ("aol-lifecycle", "safety-release"):
      build_input_digest = hashlib.sha256(json.dumps(plan["source"]["build_identity"]["inputs"], sort_keys=True).encode()).hexdigest()
      for name in (('safety',) if suite == 'safety-release' else ('safety', 'aol_protocol')):
        if name in plan["native"]:
          plan["native"][name]["build_inputs_sha256"] = build_input_digest
    phase = "collection"
    unittest_targets, pytest_targets = vehicle_pytest.split_targets(ROOT, plan["targets"])
    plan["runners"] = {"unittest": unittest_targets, "pytest": pytest_targets}
    tests, errors = test_runner.collect(unittest_targets, None) if unittest_targets else ([], [])
    pytest_nodeids, pytest_errors = vehicle_pytest.collect(pytest_targets)
    errors.extend(pytest_errors)
    ids = [test.id() for test in tests] + [vehicle_pytest.test_id(nodeid) for nodeid in pytest_nodeids]
    if shard_index is not None and not errors:
      full_targets = list(plan["targets"])
      partitioner = lifecycle_shards.release_partition if suite == 'safety-release' else lifecycle_shards.partition
      matcher = lifecycle_shards.release_matches if suite == 'safety-release' else lifecycle_shards.matches
      shards = partitioner(full_targets, ids, shard_count)
      plan.update(full_catalog_targets=full_targets, full_catalog_test_ids=list(ids),
                  shard={"index": shard_index, "count": shard_count})
      assigned_targets = shards[shard_index]
      plan["targets"] = assigned_targets
      def assigned(identity):
        return any(matcher(identity, target) for target in assigned_targets)
      tests = [test for test in tests if assigned(test.id())]
      pytest_nodeids = [nodeid for nodeid in pytest_nodeids if assigned(vehicle_pytest.test_id(nodeid))]
      ids = [test.id() for test in tests] + [vehicle_pytest.test_id(nodeid) for nodeid in pytest_nodeids]
    if len(set(ids)) != len(ids):
      errors.append("Vehicle runners selected duplicate test IDs")
    if suite == "interfaces":
      plan["module_collection"], module_errors = audit_interface_modules(plan["targets"], ids)
      errors.extend(module_errors)
      platform_tests = interface_coverage(ids, plan["platform_ids"])
    if not ids:
      errors.append("Vehicle suite collected no tests")
    plan.update(selected_test_ids=ids, interface_test_ids=platform_tests, collection_errors=errors,
                status="error" if errors else "not_run" if collect_only else "ready")
    (output / "plan.json").write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
    print(f"{suite}: {len(ids)} tests; {len(platform_tests)} concrete platform interface tests; one process", flush=True)
    if collect_only and not errors:
      return 0
    if not errors:
      phase = "execution"
      records = test_runner.run_batch([test.id() for test in tests], capture_output=True) if tests else []
      pytest_records, pytest_errors = vehicle_pytest.run(pytest_nodeids)
      records.extend(pytest_records)
      errors.extend(pytest_errors)
      phase = "source verification"
      plan["source_after"] = source_provenance()
      if suite in ("aol-lifecycle", "safety-release"):
        plan["source_after"]["build_identity"] = lifecycle_shards.build_identity(ROOT)
        if plan["source"]["build_identity"] != plan["source_after"]["build_identity"]:
          gate_errors.append("Lifecycle source/build identity changed during the suite")
      if plan["source"].get("inputs") != plan["source_after"].get("inputs"):
        gate_errors.append("Vehicle/control source inputs changed during the suite; rerun against stable source")
  except Exception:
    errors.append(f"{phase} error:\n{traceback.format_exc()}")
    plan["error_phase"] = phase
  ids = [test.id() for test in tests] + [vehicle_pytest.test_id(nodeid) for nodeid in pytest_nodeids]
  records = test_runner.account_for_tests(ids, records, tests)
  if not errors and suite == "interfaces":
    gate_errors.extend(interface_execution_errors(platform_tests, records))
  if not collect_only and suite in ("aol-lifecycle", "safety-release"):
    gate_errors.extend(f"Required lifecycle case did not pass: {record['id']} ({record['status']})"
                       for record in records
                       if not (lifecycle_shards.release_result_allowed(record) if suite == "safety-release" else record["status"] == "passed"))
    for target in plan["targets"]:
      if not any((lifecycle_shards.release_matches if suite == 'safety-release' else lifecycle_shards.matches)(identity, target) for identity in ids):
        gate_errors.append(f"Required lifecycle module collected no cases: {target}")
  elapsed = time.monotonic() - started
  exit_code = test_runner.report(records, errors + gate_errors, 10, elapsed)
  status = "error" if errors else "failed" if exit_code else "completed"
  test_runner.write_json_report(output / "results.json", ids, records, errors, elapsed, exit_code)
  result = json.loads((output / "results.json").read_text())
  result.update(status=status, gate_errors=gate_errors)
  if "error_phase" in plan:
    result["error_phase"] = plan["error_phase"]
  (output / "results.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
  by_id = {record["id"]: record for record in records}
  plan.update(status=status, exit_code=exit_code, collection_errors=errors, gate_errors=gate_errors, duration_seconds=elapsed,
              selected_test_ids=ids, interface_test_ids=platform_tests,
              result_counts=dict(Counter(record["status"] for record in records)),
              interface_results={platform: by_id[test_id] for platform, test_id in platform_tests.items()},
              result_sha256=sha256(output / "results.json"))
  if errors:
    (output / "plan.json").write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
  (output / "coverage.json").write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
  return exit_code


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--suite", choices=("interfaces", "safety-debug", "safety-release", "aol-lifecycle"), required=True)
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--collect-only", action="store_true")
  parser.add_argument("--shard-index", type=int)
  parser.add_argument("--shard-count", type=int, default=4)
  args = parser.parse_args()
  os.chdir(ROOT)
  # Keep the same generated examples across collection, execution, and reruns.
  os.environ.setdefault("FUZZ_SEED", "0")
  return run(args.suite, args.output, args.collect_only, args.shard_index, args.shard_count)


if __name__ == "__main__":
  raise SystemExit(main())
