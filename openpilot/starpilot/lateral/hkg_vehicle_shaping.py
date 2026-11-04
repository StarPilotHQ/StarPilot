import math

import numpy as np

from openpilot.common.constants import CV

STANDARD_FRICTION_THRESHOLD = 0.30

HKG_CANFD_BASE_FRICTION_THRESHOLD = 0.39

SONATA_FF_REDUCTION_LEFT = 0.04

SONATA_FF_REDUCTION_RIGHT = 0.26

SONATA_FF_ONSET = 0.18

SONATA_FF_ONSET_WIDTH = 0.08

SONATA_FF_CUTOFF = 1.40

SONATA_FF_CUTOFF_WIDTH = 0.42

SONATA_TRANSITION_SPEED = 8.5

SONATA_PHASE_SCALE = 0.12

SONATA_TURN_IN_BOOST_LEFT = 0.18

SONATA_TURN_IN_BOOST_RIGHT = 0.00

SONATA_UNWIND_TAPER_LEFT = 0.28

SONATA_UNWIND_TAPER_RIGHT = 0.00

SONATA_CENTER_TAPER_MAX = 0.04

SONATA_CENTER_TAPER_LAT = 0.15

SONATA_CENTER_TAPER_LAT_WIDTH = 0.025

SONATA_CENTER_TAPER_SPEED = 22.0

SONATA_CENTER_TAPER_SPEED_WIDTH = 2.5

SONATA_LOW_SPEED_CENTER_TAPER_MAX = 0.08

SONATA_LOW_SPEED_CENTER_TAPER_LAT = 0.10

SONATA_LOW_SPEED_CENTER_TAPER_LAT_WIDTH = 0.02

SONATA_LOW_SPEED_CENTER_TAPER_SPEED_MAX = 7.0

SONATA_LOW_SPEED_CENTER_TAPER_SPEED_WIDTH = 1.0

ELANTRA_NON_SCC_FF_ADJUST_LEFT = 0.02

ELANTRA_NON_SCC_FF_ADJUST_RIGHT = -0.02

ELANTRA_NON_SCC_FF_ONSET = 0.14

ELANTRA_NON_SCC_FF_ONSET_WIDTH = 0.06

ELANTRA_NON_SCC_FF_CUTOFF = 1.10

ELANTRA_NON_SCC_FF_CUTOFF_WIDTH = 0.34

ELANTRA_NON_SCC_TRANSITION_SPEED = 8.0

ELANTRA_NON_SCC_PHASE_SCALE = 0.10

ELANTRA_NON_SCC_TURN_IN_BOOST_LEFT = 0.10

ELANTRA_NON_SCC_TURN_IN_BOOST_RIGHT = 0.12

ELANTRA_NON_SCC_UNWIND_TAPER_LEFT = 0.22

ELANTRA_NON_SCC_UNWIND_TAPER_RIGHT = 0.12

KIA_XCEED_FF_REDUCTION_LEFT = 0.04

KIA_XCEED_FF_REDUCTION_RIGHT = 0.10

KIA_XCEED_FF_ONSET = 0.16

KIA_XCEED_FF_ONSET_WIDTH = 0.06

KIA_XCEED_FF_CUTOFF = 1.30

KIA_XCEED_FF_CUTOFF_WIDTH = 0.36

KIA_XCEED_TRANSITION_SPEED = 11.5

KIA_XCEED_PHASE_SCALE = 0.10

KIA_XCEED_TURN_IN_BOOST_LEFT = 0.26

KIA_XCEED_TURN_IN_BOOST_RIGHT = 0.06

KIA_XCEED_UNWIND_TAPER_LEFT = 0.20

KIA_XCEED_UNWIND_TAPER_RIGHT = 0.14

KIA_XCEED_CENTER_TAPER_MAX = 0.06

KIA_XCEED_CENTER_TAPER_LAT = 0.12

KIA_XCEED_CENTER_TAPER_LAT_WIDTH = 0.03

KIA_XCEED_CENTER_TAPER_SPEED = 17.5

KIA_XCEED_CENTER_TAPER_SPEED_WIDTH = 2.5

KIA_NIRO_PHEV_2022_CENTER_TAPER_MAX = 0.06

KIA_NIRO_PHEV_2022_CENTER_TAPER_LAT = 0.12

KIA_NIRO_PHEV_2022_CENTER_TAPER_LAT_WIDTH = 0.03

KIA_NIRO_PHEV_2022_CENTER_TAPER_SPEED = 23.0

KIA_NIRO_PHEV_2022_CENTER_TAPER_SPEED_WIDTH = 2.5

KIA_NIRO_PHEV_2022_FRICTION_CENTER_LAT = 0.12

KIA_NIRO_PHEV_2022_FRICTION_CENTER_LAT_WIDTH = 0.03

KIA_NIRO_PHEV_2022_FRICTION_SPEED = 23.0

KIA_NIRO_PHEV_2022_FRICTION_SPEED_WIDTH = 2.5

KIA_NIRO_PHEV_2022_FRICTION_CALM_JERK = 0.22

KIA_NIRO_PHEV_2022_FRICTION_CALM_JERK_WIDTH = 0.06

KIA_NIRO_PHEV_2022_FRICTION_THRESHOLD_GAIN = 0.12

KIA_STINGER_2022_CENTER_TAPER_MAX = 0.12

KIA_STINGER_2022_CENTER_TAPER_LAT = 0.30

KIA_STINGER_2022_CENTER_TAPER_LAT_WIDTH = 0.05

KIA_STINGER_2022_CENTER_TAPER_SPEED = 10.0

KIA_STINGER_2022_CENTER_TAPER_SPEED_WIDTH = 2.5

KIA_STINGER_2022_FRICTION_THRESHOLD_GAIN = 0.10

KIA_CARNIVAL_CENTER_TAPER_MAX = 0.20

KIA_CARNIVAL_CENTER_TAPER_LAT = 0.20

KIA_CARNIVAL_CENTER_TAPER_LAT_WIDTH = 0.055

KIA_CARNIVAL_CENTER_TAPER_SPEED = 3.5

KIA_CARNIVAL_CENTER_TAPER_SPEED_WIDTH = 1.8

KIA_CARNIVAL_CENTER_TAPER_SPEED_MAX = 14.5

KIA_CARNIVAL_CENTER_TAPER_SPEED_MAX_WIDTH = 2.0

KIA_CARNIVAL_FRICTION_THRESHOLD_GAIN = 0.24

KIA_CARNIVAL_FRICTION_CENTER_FADE_MAX = 0.34

KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_MAX = 0.14

KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_LAT = 0.24

KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_LAT_WIDTH = 0.06

KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_SPEED = 28.0

KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_SPEED_WIDTH = 2.0

KIA_CARNIVAL_HIGHWAY_FRICTION_THRESHOLD_GAIN = 0.14

KIA_CARNIVAL_HIGHWAY_FRICTION_CENTER_FADE_MAX = 0.20

KIA_CARNIVAL_HIGHWAY_TRANSITION_TAPER_MAX = 0.28

KIA_CARNIVAL_HIGHWAY_TRANSITION_JERK = 0.45

KIA_CARNIVAL_HIGHWAY_TRANSITION_JERK_WIDTH = 0.15

KIA_CARNIVAL_HIGHWAY_TRANSITION_LAT_CUTOFF = 1.20

KIA_CARNIVAL_HIGHWAY_TRANSITION_LAT_WIDTH = 0.20

KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_MAX = 0.34

KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_SPEED = 15.0

KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_SPEED_WIDTH = 2.0

KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_SPEED_CUTOFF = 23.0

KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_SPEED_CUTOFF_WIDTH = 2.0

KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_LAT = 0.35

KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_LAT_WIDTH = 0.18

KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_JERK = 0.65

KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_JERK_WIDTH = 0.25

KIA_CARNIVAL_UNWIND_FF_REDUCTION_MAX = 0.45

KIA_CARNIVAL_UNWIND_FF_SPEED = 9.0

KIA_CARNIVAL_UNWIND_FF_SPEED_WIDTH = 2.0

KIA_CARNIVAL_UNWIND_FF_SPEED_CUTOFF = 23.0

KIA_CARNIVAL_UNWIND_FF_SPEED_CUTOFF_WIDTH = 2.0

KIA_CARNIVAL_UNWIND_FF_OVERSHOOT = 0.08

KIA_CARNIVAL_UNWIND_FF_OVERSHOOT_WIDTH = 0.06

KIA_CARNIVAL_UNWIND_FF_JERK = 0.45

KIA_CARNIVAL_UNWIND_FF_JERK_WIDTH = 0.20

KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_MAX = 0.35

KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_SPEED = 8.0

KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_SPEED_WIDTH = 2.0

KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_SPEED_CUTOFF = 16.0

KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_SPEED_CUTOFF_WIDTH = 2.5

KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_OVERSHOOT = 0.25

KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_OVERSHOOT_WIDTH = 0.15

KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_JERK = 0.45

KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_JERK_WIDTH = 0.20

TUCSON_4TH_GEN_CENTER_TAPER_MAX = 0.44

TUCSON_4TH_GEN_CENTER_TAPER_LAT = 0.28

TUCSON_4TH_GEN_CENTER_TAPER_LAT_WIDTH = 0.055

TUCSON_4TH_GEN_CENTER_TAPER_SPEED_MAX = 14.0

TUCSON_4TH_GEN_CENTER_TAPER_SPEED_WIDTH = 1.5

TUCSON_4TH_GEN_FRICTION_THRESHOLD_GAIN = 0.28

KIA_FORTE_BASE_LAT_ACCEL_FACTOR_MULT = 1.05

KIA_FORTE_FF_REDUCTION_LEFT = 0.05

KIA_FORTE_FF_REDUCTION_RIGHT = 0.10

KIA_FORTE_FF_ONSET = 0.16

KIA_FORTE_FF_ONSET_WIDTH = 0.06

KIA_FORTE_FF_CUTOFF = 1.20

KIA_FORTE_FF_CUTOFF_WIDTH = 0.36

KIA_FORTE_TRANSITION_SPEED = 9.0

KIA_FORTE_PHASE_SCALE = 0.10

KIA_FORTE_TURN_IN_BOOST_LEFT = 0.10

KIA_FORTE_TURN_IN_BOOST_RIGHT = 0.05

KIA_FORTE_UNWIND_TAPER_LEFT = 0.18

KIA_FORTE_UNWIND_TAPER_RIGHT = 0.02

KIA_FORTE_CRAWL_TURN_IN_FF_BOOST_LEFT = 0.10

KIA_FORTE_CRAWL_TURN_IN_FF_BOOST_RIGHT = 0.14

KIA_FORTE_CRAWL_TURN_IN_FF_SPEED = 4.5

KIA_FORTE_CRAWL_TURN_IN_FF_SPEED_WIDTH = 0.8

KIA_FORTE_CRAWL_TURN_IN_FF_LAT = 0.10

KIA_FORTE_CRAWL_TURN_IN_FF_LAT_WIDTH = 0.05

KIA_FORTE_CENTER_TAPER_MAX = 0.12

KIA_FORTE_CENTER_TAPER_LAT = 0.18

KIA_FORTE_CENTER_TAPER_LAT_WIDTH = 0.04

KIA_FORTE_CENTER_TAPER_SPEED = 22.5

KIA_FORTE_CENTER_TAPER_SPEED_WIDTH = 3.0

KIA_FORTE_FRICTION_CENTER_LAT = 0.18

KIA_FORTE_FRICTION_CENTER_LAT_WIDTH = 0.04

KIA_FORTE_FRICTION_SPEED = 24.0

KIA_FORTE_FRICTION_SPEED_WIDTH = 3.0

KIA_FORTE_FRICTION_CALM_JERK = 0.24

KIA_FORTE_FRICTION_CALM_JERK_WIDTH = 0.08

KIA_FORTE_FRICTION_THRESHOLD_GAIN = 0.10

IONIQ_5_BASE_LAT_ACCEL_FACTOR_MULT = 1.22

IONIQ_5_FF_ONSET = 0.10

IONIQ_5_FF_ONSET_WIDTH = 0.05

IONIQ_5_FF_CUTOFF = 1.20

IONIQ_5_FF_CUTOFF_WIDTH = 0.30

IONIQ_5_TRANSITION_SPEED = 12.5

IONIQ_5_PHASE_SCALE = 0.10

IONIQ_5_FF_REDUCTION_LEFT = 0.15

IONIQ_5_FF_REDUCTION_RIGHT = 0.25

IONIQ_5_TURN_IN_BOOST_LEFT = 0.10

IONIQ_5_TURN_IN_BOOST_RIGHT = 0.04

IONIQ_5_UNWIND_TAPER_LEFT = 0.92

IONIQ_5_UNWIND_TAPER_RIGHT = 1.04

IONIQ_5_TURN_IN_THRESHOLD_REDUCTION_LEFT = 0.05

IONIQ_5_TURN_IN_THRESHOLD_REDUCTION_RIGHT = 0.03

IONIQ_5_UNWIND_THRESHOLD_INCREASE_LEFT = 0.46

IONIQ_5_UNWIND_THRESHOLD_INCREASE_RIGHT = 0.50

IONIQ_5_TURN_IN_FRICTION_BOOST_LEFT = 0.02

IONIQ_5_TURN_IN_FRICTION_BOOST_RIGHT = 0.01

IONIQ_5_UNWIND_FRICTION_REDUCTION_LEFT = 0.42

IONIQ_5_UNWIND_FRICTION_REDUCTION_RIGHT = 0.44

IONIQ_5_CENTER_TAPER_MAX = 0.18

IONIQ_5_CENTER_TAPER_LAT = 0.16

IONIQ_5_CENTER_TAPER_LAT_WIDTH = 0.04

IONIQ_5_CENTER_TAPER_SPEED = 15.0

IONIQ_5_CENTER_TAPER_SPEED_WIDTH = 2.2

IONIQ_5_SUSTAINED_TURN_IN_FF_BOOST_LEFT = 0.10

IONIQ_5_SUSTAINED_TURN_IN_FF_BOOST_RIGHT = 0.16

IONIQ_5_SUSTAINED_TURN_IN_FF_SPEED = 13.5

IONIQ_5_SUSTAINED_TURN_IN_FF_SPEED_WIDTH = 1.8

IONIQ_5_SUSTAINED_TURN_IN_FF_LAT_START = 1.10

IONIQ_5_SUSTAINED_TURN_IN_FF_LAT_END = 3.60

IONIQ_5_SUSTAINED_TURN_IN_FF_LAT_WIDTH = 0.30

IONIQ_5_LOW_SPEED_OUTPUT_LIMIT_BASE = 0.05

IONIQ_5_LOW_SPEED_OUTPUT_LIMIT_TURN_RELIEF = 0.95

IONIQ_5_LOW_SPEED_OUTPUT_LIMIT_SPEED = 8.0

IONIQ_5_LOW_SPEED_OUTPUT_LIMIT_SPEED_WIDTH = 0.8

IONIQ_5_LOW_SPEED_CENTER_LAT = 0.40

IONIQ_5_LOW_SPEED_CENTER_LAT_WIDTH = 0.10

IONIQ_5_LOW_SPEED_CENTER_JERK = 0.40

IONIQ_5_LOW_SPEED_CENTER_JERK_WIDTH = 0.12

IONIQ_5_FRICTION_JERK_DEADZONE_MAX = 0.36

IONIQ_5_FRICTION_JERK_DEADZONE_LAT = 1.25

IONIQ_5_FRICTION_JERK_DEADZONE_LAT_WIDTH = 0.35

IONIQ_5_FRICTION_JERK_DEADZONE_SPEED = 18.0

IONIQ_5_FRICTION_JERK_DEADZONE_SPEED_WIDTH = 3.0

IONIQ_EV_OLD_BASE_LAT_ACCEL_FACTOR_MULT = 1.16

IONIQ_EV_OLD_FF_REDUCTION_LEFT = 0.16

IONIQ_EV_OLD_FF_REDUCTION_RIGHT = 0.30

IONIQ_EV_OLD_FF_ONSET = 0.14

IONIQ_EV_OLD_FF_ONSET_WIDTH = 0.05

IONIQ_EV_OLD_FF_CUTOFF = 1.10

IONIQ_EV_OLD_FF_CUTOFF_WIDTH = 0.30

IONIQ_EV_OLD_TRANSITION_SPEED = 10.0

IONIQ_EV_OLD_PHASE_SCALE = 0.10

IONIQ_EV_OLD_TURN_IN_BOOST_LEFT = 0.01

IONIQ_EV_OLD_TURN_IN_BOOST_RIGHT = 0.00

IONIQ_EV_OLD_UNWIND_TAPER_LEFT = 0.26

IONIQ_EV_OLD_UNWIND_TAPER_RIGHT = 0.06

IONIQ_EV_OLD_CENTER_TAPER_MAX = 0.14

IONIQ_EV_OLD_CENTER_TAPER_LAT = 0.12

IONIQ_EV_OLD_CENTER_TAPER_LAT_WIDTH = 0.03

IONIQ_EV_OLD_CENTER_TAPER_SPEED = 22.0

IONIQ_EV_OLD_CENTER_TAPER_SPEED_WIDTH = 2.2

KIA_EV6_FF_GAIN_LEFT = 0.12

KIA_EV6_FF_GAIN_RIGHT = 0.17

KIA_EV6_FF_ONSET = 0.08

KIA_EV6_FF_ONSET_WIDTH = 0.04

KIA_EV6_FF_CUTOFF = 1.90

KIA_EV6_FF_CUTOFF_WIDTH = 0.40

KIA_EV6_TRANSITION_SPEED = 14.5

KIA_EV6_PHASE_SCALE = 0.09

KIA_EV6_TURN_IN_BOOST_LEFT = 0.54

KIA_EV6_TURN_IN_BOOST_RIGHT = 0.60

KIA_EV6_UNWIND_TAPER_LEFT = 0.56

KIA_EV6_UNWIND_TAPER_RIGHT = 0.54

KIA_EV6_BASE_UNWIND_TAPER_LEFT = 0.10

KIA_EV6_BASE_UNWIND_TAPER_RIGHT = 0.13

KIA_EV6_JWARM_BASE_TURN_IN_BOOST_LEFT = 0.12

KIA_EV6_JWARM_BASE_TURN_IN_BOOST_RIGHT = 0.14

KIA_EV6_JWARM_BASE_UNWIND_TAPER_LEFT = 0.15

KIA_EV6_JWARM_BASE_UNWIND_TAPER_RIGHT = 0.16

KIA_EV6_JWARM_PHASE_STABILITY_MAX_REDUCTION = 0.25

KIA_EV6_JWARM_PHASE_STABILITY_SPEED = 10.0

KIA_EV6_JWARM_PHASE_STABILITY_SPEED_WIDTH = 1.8

KIA_EV6_JWARM_PHASE_STABILITY_JERK = 0.70

KIA_EV6_JWARM_PHASE_STABILITY_JERK_WIDTH = 0.18

KIA_EV6_FRICTION_MULT = 1.01

KIA_EV6_FRICTION_LAT_RISE = 0.18

KIA_EV6_FRICTION_JERK_RISE = 0.22

KIA_EV6_TURN_IN_THRESHOLD_REDUCTION_LEFT = 0.34

KIA_EV6_TURN_IN_THRESHOLD_REDUCTION_RIGHT = 0.40

KIA_EV6_UNWIND_THRESHOLD_INCREASE_LEFT = 0.28

KIA_EV6_UNWIND_THRESHOLD_INCREASE_RIGHT = 0.24

KIA_EV6_TURN_IN_FRICTION_BOOST_LEFT = 0.18

KIA_EV6_TURN_IN_FRICTION_BOOST_RIGHT = 0.24

KIA_EV6_UNWIND_FRICTION_REDUCTION_LEFT = 0.28

KIA_EV6_UNWIND_FRICTION_REDUCTION_RIGHT = 0.22

KIA_EV6_CENTER_TAPER_MAX = 0.12

KIA_EV6_CENTER_TAPER_LAT = 0.16

KIA_EV6_CENTER_TAPER_LAT_WIDTH = 0.04

KIA_EV6_CENTER_TAPER_SPEED = 17.0

KIA_EV6_CENTER_TAPER_SPEED_WIDTH = 2.8

KIA_EV6_CENTER_FRICTION_THRESHOLD_GAIN = 0.14

KIA_EV6_CENTER_FRICTION_THRESHOLD_LAT = 0.30

KIA_EV6_CENTER_FRICTION_THRESHOLD_LAT_WIDTH = 0.07

KIA_EV6_CENTER_FRICTION_THRESHOLD_SPEED = 18.0

KIA_EV6_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH = 2.5

KIA_EV6_LOW_SPEED_CENTER_TAPER_MAX = 0.19

KIA_EV6_LOW_SPEED_CENTER_TAPER_LAT = 0.08

KIA_EV6_LOW_SPEED_CENTER_TAPER_LAT_WIDTH = 0.02

KIA_EV6_LOW_SPEED_CENTER_TAPER_SPEED_MAX = 8.5

KIA_EV6_LOW_SPEED_CENTER_TAPER_SPEED_WIDTH = 1.4

KIA_EV6_CENTER_OUTPUT_TAPER_MAX = 0.14

KIA_EV6_HIGHWAY_CENTER_DAMPING = 0.08

KIA_EV6_CENTER_OUTPUT_TAPER_LAT = 0.30

KIA_EV6_CENTER_OUTPUT_TAPER_LAT_WIDTH = 0.08

KIA_EV6_CENTER_OUTPUT_TAPER_SPEED = 12.0

KIA_EV6_CENTER_OUTPUT_TAPER_SPEED_WIDTH = 2.4

KONA_NON_SCC_TRANSITION_TURN_IN_TAPER_MAX = 0.24

KONA_NON_SCC_TRANSITION_UNWIND_TAPER_MAX = 0.42

KONA_NON_SCC_TRANSITION_SPEED_ONSET = 22.0

KONA_NON_SCC_TRANSITION_SPEED_FULL = 29.0

KONA_NON_SCC_TRANSITION_JERK_ONSET = 0.35

KONA_NON_SCC_TRANSITION_JERK_FULL = 1.20

KONA_NON_SCC_TRANSITION_LAT_FADE_START = 0.45

KONA_NON_SCC_TRANSITION_LAT_FADE_END = 1.60

KONA_NON_SCC_CENTER_TAPER_MAX = 0.14

KONA_NON_SCC_CENTER_TAPER_LAT = 0.28

KONA_NON_SCC_CENTER_TAPER_SPEED_ONSET = 12.0

KONA_NON_SCC_CENTER_TAPER_SPEED_FULL = 24.0

KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_GAIN = 0.14

KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_LAT = 0.28

KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_LAT_WIDTH = 0.07

KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_SPEED_ONSET = 11.0

KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH = 2.5

KONA_EV_2022_CENTER_FRICTION_THRESHOLD_GAIN = 0.08

KONA_EV_2022_CENTER_FRICTION_THRESHOLD_LAT = 0.20

KONA_EV_2022_CENTER_FRICTION_THRESHOLD_LAT_WIDTH = 0.05

KONA_EV_2022_CENTER_FRICTION_THRESHOLD_SPEED = 18.0

KONA_EV_2022_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH = 2.5

KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_GAIN = 0.30
KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_SPEED_ONSET = 100.0 / 3.6
KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_SPEED_FULL = 110.0 / 3.6
KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_LAT_FADE_START = 1.20
KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_LAT_FADE_END = 2.00

KONA_EV_2022_CENTER_OUTPUT_TAPER_MAX = 0.08

KONA_EV_2022_CENTER_OUTPUT_TAPER_LAT = 0.20

KONA_EV_2022_CENTER_OUTPUT_TAPER_LAT_WIDTH = 0.05

KONA_EV_2022_CENTER_OUTPUT_TAPER_SPEED = 18.0

KONA_EV_2022_CENTER_OUTPUT_TAPER_SPEED_WIDTH = 2.5

def _sigmoid(x: float) -> float:
  if x >= 0.0:
    z = math.exp(-x)
    return 1.0 / (1.0 + z)

  z = math.exp(x)
  return z / (1.0 + z)

def _gm_base_friction_threshold_default(v_ego: float) -> float:
  return float(np.interp(v_ego, [1 * CV.MPH_TO_MS, 20 * CV.MPH_TO_MS, 75 * CV.MPH_TO_MS], [0.16, 0.19, 0.27]))

def _standard_friction_threshold_default(v_ego: float) -> float:
  return max(_gm_base_friction_threshold_default(v_ego), STANDARD_FRICTION_THRESHOLD)

def _hkg_canfd_base_friction_threshold_default(v_ego: float) -> float:
  return max(_gm_base_friction_threshold_default(v_ego), HKG_CANFD_BASE_FRICTION_THRESHOLD)

def _flm_vehicle_knob(name, default_value):
  return float(default_value)

def get_gm_base_friction_threshold(v_ego):
  return _gm_base_friction_threshold_default(v_ego)

def get_standard_friction_threshold(v_ego):
  return _standard_friction_threshold_default(v_ego)

def get_hkg_canfd_base_friction_threshold(v_ego):
  return _hkg_canfd_base_friction_threshold_default(v_ego)

def get_kona_non_scc_highway_transition_output_scale(desired_lateral_accel: float, desired_lateral_jerk: float,
                                                       v_ego: float) -> float:
  speed_weight = float(np.interp(v_ego, [KONA_NON_SCC_TRANSITION_SPEED_ONSET, KONA_NON_SCC_TRANSITION_SPEED_FULL], [0.0, 1.0]))
  jerk_weight = float(np.interp(abs(desired_lateral_jerk),
                                [KONA_NON_SCC_TRANSITION_JERK_ONSET, KONA_NON_SCC_TRANSITION_JERK_FULL], [0.0, 1.0]))
  lat_weight = 1.0 - float(np.interp(abs(desired_lateral_accel),
                                     [KONA_NON_SCC_TRANSITION_LAT_FADE_START, KONA_NON_SCC_TRANSITION_LAT_FADE_END], [0.0, 1.0]))
  taper_max = (KONA_NON_SCC_TRANSITION_UNWIND_TAPER_MAX
               if desired_lateral_accel * desired_lateral_jerk < 0.0
               else KONA_NON_SCC_TRANSITION_TURN_IN_TAPER_MAX)
  return 1.0 - (taper_max * speed_weight * jerk_weight * lat_weight)

def get_kona_non_scc_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0,
                                        desired_lateral_jerk: float = 0.0) -> float:
  del desired_lateral_jerk
  speed_weight = _sigmoid((v_ego - KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_SPEED_ONSET) /
                          KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH)
  center_weight = _sigmoid((KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_LAT - abs(desired_lateral_accel)) /
                           KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_LAT_WIDTH)
  return get_standard_friction_threshold(v_ego) * (
    1.0 + KONA_NON_SCC_CENTER_FRICTION_THRESHOLD_GAIN * speed_weight * center_weight
  )

def get_kona_non_scc_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = float(np.interp(v_ego, [KONA_NON_SCC_CENTER_TAPER_SPEED_ONSET, KONA_NON_SCC_CENTER_TAPER_SPEED_FULL], [0.0, 1.0]))
  center_weight = float(np.interp(abs(desired_lateral_accel), [0.0, KONA_NON_SCC_CENTER_TAPER_LAT], [1.0, 0.0]))
  return 1.0 - (KONA_NON_SCC_CENTER_TAPER_MAX * speed_weight * center_weight)

def _kona_ev_2022_center_weights(desired_lateral_accel: float, v_ego: float) -> tuple[float, float]:
  speed_weight = _sigmoid((v_ego - KONA_EV_2022_CENTER_FRICTION_THRESHOLD_SPEED) /
                          KONA_EV_2022_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH)
  center_weight = _sigmoid((KONA_EV_2022_CENTER_FRICTION_THRESHOLD_LAT - abs(desired_lateral_accel)) /
                           KONA_EV_2022_CENTER_FRICTION_THRESHOLD_LAT_WIDTH)
  return speed_weight, center_weight

def get_kona_ev_2022_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0) -> float:
  speed_weight, center_weight = _kona_ev_2022_center_weights(desired_lateral_accel, v_ego)
  base_threshold = get_standard_friction_threshold(v_ego) * (
    1.0 + KONA_EV_2022_CENTER_FRICTION_THRESHOLD_GAIN * speed_weight * center_weight
  )
  high_speed_weight = float(np.interp(
    v_ego,
    [KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_SPEED_ONSET, KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_SPEED_FULL],
    [0.0, 1.0],
  ))
  curve_weight = float(np.interp(
    abs(desired_lateral_accel),
    [KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_LAT_FADE_START, KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_LAT_FADE_END],
    [1.0, 0.0],
  ))
  return base_threshold + KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_GAIN * high_speed_weight * curve_weight

def get_kona_ev_2022_center_output_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _sigmoid((v_ego - KONA_EV_2022_CENTER_OUTPUT_TAPER_SPEED) /
                          KONA_EV_2022_CENTER_OUTPUT_TAPER_SPEED_WIDTH)
  center_weight = _sigmoid((KONA_EV_2022_CENTER_OUTPUT_TAPER_LAT - abs(desired_lateral_accel)) /
                           KONA_EV_2022_CENTER_OUTPUT_TAPER_LAT_WIDTH)
  return 1.0 - (KONA_EV_2022_CENTER_OUTPUT_TAPER_MAX * speed_weight * center_weight)

def _sonata_sigmoid(x: float) -> float:
  return _sigmoid(x)

def _sonata_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / SONATA_TRANSITION_SPEED) ** 2)

def _sonata_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / SONATA_PHASE_SCALE)

def _sonata_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value

def get_sonata_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _sonata_sigmoid((abs_lateral_accel - SONATA_FF_ONSET) / SONATA_FF_ONSET_WIDTH)
  cutoff = _sonata_sigmoid((SONATA_FF_CUTOFF - abs_lateral_accel) / SONATA_FF_CUTOFF_WIDTH)
  base_reduction = _sonata_side_value(desired_lateral_accel, SONATA_FF_REDUCTION_LEFT, SONATA_FF_REDUCTION_RIGHT) * onset * cutoff
  phase = _sonata_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  low_speed_factor = _sonata_low_speed_factor(v_ego)
  turn_in_boost = 1.0 + (_sonata_side_value(desired_lateral_accel, SONATA_TURN_IN_BOOST_LEFT, SONATA_TURN_IN_BOOST_RIGHT) *
                         turn_in_weight * low_speed_factor)
  unwind_taper = 1.0 - (_sonata_side_value(desired_lateral_accel, SONATA_UNWIND_TAPER_LEFT, SONATA_UNWIND_TAPER_RIGHT) *
                        unwind_weight * (0.35 + 0.65 * low_speed_factor))
  return (1.0 - base_reduction) * turn_in_boost * max(unwind_taper, 0.0)

def get_sonata_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _sonata_sigmoid((v_ego - SONATA_CENTER_TAPER_SPEED) / SONATA_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _sonata_sigmoid((SONATA_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / SONATA_CENTER_TAPER_LAT_WIDTH)
  reduction = SONATA_CENTER_TAPER_MAX * speed_weight * center_weight
  low_speed_weight = _sonata_sigmoid((SONATA_LOW_SPEED_CENTER_TAPER_SPEED_MAX - v_ego) /
                                     SONATA_LOW_SPEED_CENTER_TAPER_SPEED_WIDTH)
  low_speed_center_weight = _sonata_sigmoid((SONATA_LOW_SPEED_CENTER_TAPER_LAT - abs(desired_lateral_accel)) /
                                            SONATA_LOW_SPEED_CENTER_TAPER_LAT_WIDTH)
  reduction += SONATA_LOW_SPEED_CENTER_TAPER_MAX * low_speed_weight * low_speed_center_weight
  return 1.0 - reduction

def _elantra_non_scc_sigmoid(x: float) -> float:
  return _sigmoid(x)

def _elantra_non_scc_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / ELANTRA_NON_SCC_TRANSITION_SPEED) ** 2)

def _elantra_non_scc_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / ELANTRA_NON_SCC_PHASE_SCALE)

def _elantra_non_scc_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value

def get_elantra_non_scc_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _elantra_non_scc_sigmoid((abs_lateral_accel - ELANTRA_NON_SCC_FF_ONSET) / ELANTRA_NON_SCC_FF_ONSET_WIDTH)
  cutoff = _elantra_non_scc_sigmoid((ELANTRA_NON_SCC_FF_CUTOFF - abs_lateral_accel) / ELANTRA_NON_SCC_FF_CUTOFF_WIDTH)
  low_speed_factor = _elantra_non_scc_low_speed_factor(v_ego)
  envelope = onset * cutoff * low_speed_factor
  base_scale = 1.0 - (_elantra_non_scc_side_value(desired_lateral_accel,
                                                   ELANTRA_NON_SCC_FF_ADJUST_LEFT,
                                                   ELANTRA_NON_SCC_FF_ADJUST_RIGHT) * envelope)
  phase = _elantra_non_scc_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  turn_in_boost = 1.0 + (_elantra_non_scc_side_value(desired_lateral_accel,
                                                      ELANTRA_NON_SCC_TURN_IN_BOOST_LEFT,
                                                      ELANTRA_NON_SCC_TURN_IN_BOOST_RIGHT) *
                          turn_in_weight * (0.35 + 0.65 * low_speed_factor))
  unwind_taper = 1.0 - (_elantra_non_scc_side_value(desired_lateral_accel,
                                                     ELANTRA_NON_SCC_UNWIND_TAPER_LEFT,
                                                     ELANTRA_NON_SCC_UNWIND_TAPER_RIGHT) *
                         unwind_weight * (0.35 + 0.65 * low_speed_factor))
  return base_scale * turn_in_boost * max(unwind_taper, 0.0)

def _kia_xceed_sigmoid(x: float) -> float:
  return _sigmoid(x)

def _kia_xceed_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / KIA_XCEED_TRANSITION_SPEED) ** 2)

def _kia_xceed_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / KIA_XCEED_PHASE_SCALE)

def _kia_xceed_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value

def get_kia_xceed_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _kia_xceed_sigmoid((abs_lateral_accel - KIA_XCEED_FF_ONSET) / KIA_XCEED_FF_ONSET_WIDTH)
  cutoff = _kia_xceed_sigmoid((KIA_XCEED_FF_CUTOFF - abs_lateral_accel) / KIA_XCEED_FF_CUTOFF_WIDTH)
  base_reduction = _kia_xceed_side_value(desired_lateral_accel,
                                         KIA_XCEED_FF_REDUCTION_LEFT,
                                         KIA_XCEED_FF_REDUCTION_RIGHT) * onset * cutoff
  phase = _kia_xceed_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  low_speed_factor = _kia_xceed_low_speed_factor(v_ego)
  turn_in_boost = 1.0 + (_kia_xceed_side_value(desired_lateral_accel,
                                                KIA_XCEED_TURN_IN_BOOST_LEFT,
                                                KIA_XCEED_TURN_IN_BOOST_RIGHT) *
                         turn_in_weight * low_speed_factor)
  unwind_taper = 1.0 - (_kia_xceed_side_value(desired_lateral_accel,
                                               KIA_XCEED_UNWIND_TAPER_LEFT,
                                               KIA_XCEED_UNWIND_TAPER_RIGHT) *
                        unwind_weight * (0.35 + 0.65 * low_speed_factor))
  return (1.0 - base_reduction) * turn_in_boost * max(unwind_taper, 0.0)

def get_kia_xceed_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _kia_xceed_sigmoid((v_ego - KIA_XCEED_CENTER_TAPER_SPEED) / KIA_XCEED_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _kia_xceed_sigmoid((KIA_XCEED_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / KIA_XCEED_CENTER_TAPER_LAT_WIDTH)
  reduction = KIA_XCEED_CENTER_TAPER_MAX * speed_weight * center_weight
  return 1.0 - reduction

def get_kia_niro_phev_2022_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _sigmoid((v_ego - KIA_NIRO_PHEV_2022_CENTER_TAPER_SPEED) / KIA_NIRO_PHEV_2022_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _sigmoid((KIA_NIRO_PHEV_2022_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / KIA_NIRO_PHEV_2022_CENTER_TAPER_LAT_WIDTH)
  reduction = KIA_NIRO_PHEV_2022_CENTER_TAPER_MAX * speed_weight * center_weight
  return 1.0 - reduction

def get_kia_niro_phev_2022_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  base_threshold = get_gm_base_friction_threshold(v_ego)
  speed_weight = _sigmoid((v_ego - KIA_NIRO_PHEV_2022_FRICTION_SPEED) / KIA_NIRO_PHEV_2022_FRICTION_SPEED_WIDTH)
  center_weight = _sigmoid((KIA_NIRO_PHEV_2022_FRICTION_CENTER_LAT - abs(desired_lateral_accel)) / KIA_NIRO_PHEV_2022_FRICTION_CENTER_LAT_WIDTH)
  calm_jerk_weight = _sigmoid((KIA_NIRO_PHEV_2022_FRICTION_CALM_JERK - abs(desired_lateral_jerk)) / KIA_NIRO_PHEV_2022_FRICTION_CALM_JERK_WIDTH)
  threshold_scale = 1.0 + (KIA_NIRO_PHEV_2022_FRICTION_THRESHOLD_GAIN * speed_weight * center_weight * calm_jerk_weight)
  return base_threshold * min(max(threshold_scale, 1.0), 1.18)

def _kia_stinger_2022_center_weights(desired_lateral_accel: float, v_ego: float) -> tuple[float, float]:
  speed_weight = _sigmoid((v_ego - KIA_STINGER_2022_CENTER_TAPER_SPEED) / KIA_STINGER_2022_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _sigmoid((KIA_STINGER_2022_CENTER_TAPER_LAT - abs(desired_lateral_accel)) /
                           KIA_STINGER_2022_CENTER_TAPER_LAT_WIDTH)
  return speed_weight, center_weight

def get_kia_stinger_2022_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight, center_weight = _kia_stinger_2022_center_weights(desired_lateral_accel, v_ego)
  return 1.0 - (KIA_STINGER_2022_CENTER_TAPER_MAX * speed_weight * center_weight)

def get_kia_stinger_2022_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0,
                                            desired_lateral_jerk: float = 0.0,
                                            future_lateral_accel: float = 0.0, steering_pressed: bool = False) -> float:
  speed_weight, center_weight = _kia_stinger_2022_center_weights(desired_lateral_accel, v_ego)
  threshold = get_standard_friction_threshold(v_ego) * (1.0 + KIA_STINGER_2022_FRICTION_THRESHOLD_GAIN * speed_weight * center_weight)
  if not steering_pressed:
    highway_weight = np.interp(v_ego, KIA_STINGER_2022_HIGHWAY_FRICTION_SPEED_BP, [0.0, 1.0])
    requested_accel = max(abs(desired_lateral_accel), abs(future_lateral_accel))
    highway_weight *= np.interp(requested_accel, KIA_STINGER_2022_HIGHWAY_FRICTION_LAT_BP, [1.0, 0.0])
    highway_weight *= np.interp(abs(desired_lateral_jerk), KIA_STINGER_2022_HIGHWAY_FRICTION_JERK_BP, [1.0, 0.0])
    threshold += KIA_STINGER_2022_HIGHWAY_FRICTION_INCREMENT * highway_weight
  return float(threshold)

def _kia_carnival_center_weights(desired_lateral_accel: float, v_ego: float) -> tuple[float, float]:
  speed_onset = _sigmoid((v_ego - KIA_CARNIVAL_CENTER_TAPER_SPEED) / KIA_CARNIVAL_CENTER_TAPER_SPEED_WIDTH)
  speed_cutoff = _sigmoid((KIA_CARNIVAL_CENTER_TAPER_SPEED_MAX - v_ego) / KIA_CARNIVAL_CENTER_TAPER_SPEED_MAX_WIDTH)
  speed_weight = speed_onset * speed_cutoff
  center_weight = _sigmoid((KIA_CARNIVAL_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / KIA_CARNIVAL_CENTER_TAPER_LAT_WIDTH)
  return speed_weight, center_weight

def _kia_carnival_highway_center_weights(desired_lateral_accel: float, v_ego: float) -> tuple[float, float]:
  speed_weight = _sigmoid((v_ego - KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_SPEED) /
                          KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _sigmoid((KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_LAT - abs(desired_lateral_accel)) /
                           KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_LAT_WIDTH)
  return speed_weight, center_weight

def get_kia_carnival_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight, center_weight = _kia_carnival_center_weights(desired_lateral_accel, v_ego)
  highway_speed_weight, highway_center_weight = _kia_carnival_highway_center_weights(desired_lateral_accel, v_ego)
  reduction = KIA_CARNIVAL_CENTER_TAPER_MAX * speed_weight * center_weight
  reduction += KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_MAX * highway_speed_weight * highway_center_weight
  return 1.0 - min(reduction, 0.95)

def get_kia_carnival_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  del desired_lateral_jerk
  speed_weight, center_weight = _kia_carnival_center_weights(desired_lateral_accel, v_ego)
  highway_speed_weight, highway_center_weight = _kia_carnival_highway_center_weights(desired_lateral_accel, v_ego)
  gain = KIA_CARNIVAL_FRICTION_THRESHOLD_GAIN * speed_weight * center_weight
  gain += KIA_CARNIVAL_HIGHWAY_FRICTION_THRESHOLD_GAIN * highway_speed_weight * highway_center_weight
  return get_hkg_canfd_base_friction_threshold(v_ego) * (1.0 + gain)

def get_kia_carnival_friction_center_fade_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight, center_weight = _kia_carnival_center_weights(desired_lateral_accel, v_ego)
  highway_speed_weight, highway_center_weight = _kia_carnival_highway_center_weights(desired_lateral_accel, v_ego)
  reduction = KIA_CARNIVAL_FRICTION_CENTER_FADE_MAX * speed_weight * center_weight
  reduction += KIA_CARNIVAL_HIGHWAY_FRICTION_CENTER_FADE_MAX * highway_speed_weight * highway_center_weight
  return 1.0 - min(reduction, 0.95)

def get_kia_carnival_highway_transition_output_scale(desired_lateral_accel: float, desired_lateral_jerk: float,
                                                      v_ego: float) -> float:
  speed_weight = _sigmoid((v_ego - KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_SPEED) /
                          KIA_CARNIVAL_HIGHWAY_CENTER_TAPER_SPEED_WIDTH)
  jerk_weight = _sigmoid((abs(desired_lateral_jerk) - KIA_CARNIVAL_HIGHWAY_TRANSITION_JERK) /
                         KIA_CARNIVAL_HIGHWAY_TRANSITION_JERK_WIDTH)
  lat_weight = _sigmoid((KIA_CARNIVAL_HIGHWAY_TRANSITION_LAT_CUTOFF - abs(desired_lateral_accel)) /
                        KIA_CARNIVAL_HIGHWAY_TRANSITION_LAT_WIDTH)
  return 1.0 - (KIA_CARNIVAL_HIGHWAY_TRANSITION_TAPER_MAX * speed_weight * jerk_weight * lat_weight)

def get_kia_carnival_friction_jerk_deadzone(v_ego: float, desired_lateral_accel: float,
                                            desired_lateral_jerk: float) -> float:
  speed_weight = _sigmoid((v_ego - KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_SPEED) /
                          KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_SPEED_WIDTH)
  speed_cutoff = _sigmoid((KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_SPEED_CUTOFF - v_ego) /
                          KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_SPEED_CUTOFF_WIDTH)
  center_weight = _sigmoid((KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_LAT - abs(desired_lateral_accel)) /
                           KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_LAT_WIDTH)
  jerk_weight = _sigmoid((abs(desired_lateral_jerk) - KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_JERK) /
                         KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_JERK_WIDTH)
  return KIA_CARNIVAL_UNWIND_FRICTION_JERK_DEADZONE_MAX * speed_weight * speed_cutoff * center_weight * jerk_weight

def get_kia_carnival_unwind_ff_scale(setpoint: float, measured_lateral_accel: float,
                                     desired_lateral_jerk: float, v_ego: float) -> float:
  if setpoint * desired_lateral_jerk >= 0.0:
    return 1.0

  overshoot = max(abs(measured_lateral_accel) - abs(setpoint), 0.0)
  if overshoot <= 0.0:
    return 1.0

  speed_weight = (_sigmoid((v_ego - KIA_CARNIVAL_UNWIND_FF_SPEED) /
                           KIA_CARNIVAL_UNWIND_FF_SPEED_WIDTH) *
                  _sigmoid((KIA_CARNIVAL_UNWIND_FF_SPEED_CUTOFF - v_ego) /
                           KIA_CARNIVAL_UNWIND_FF_SPEED_CUTOFF_WIDTH))
  overshoot_weight = _sigmoid((overshoot - KIA_CARNIVAL_UNWIND_FF_OVERSHOOT) /
                              KIA_CARNIVAL_UNWIND_FF_OVERSHOOT_WIDTH)
  jerk_weight = _sigmoid((abs(desired_lateral_jerk) - KIA_CARNIVAL_UNWIND_FF_JERK) /
                         KIA_CARNIVAL_UNWIND_FF_JERK_WIDTH)
  return 1.0 - (KIA_CARNIVAL_UNWIND_FF_REDUCTION_MAX * speed_weight * overshoot_weight * jerk_weight)

def get_kia_carnival_unwind_output_scale(setpoint: float, measured_lateral_accel: float,
                                         desired_lateral_jerk: float, v_ego: float,
                                         *, output_torque: float | None = None) -> float:
  if output_torque is not None and output_torque * setpoint <= 0.0:
    return 1.0
  if (setpoint * desired_lateral_jerk >= 0.0 or
      setpoint * measured_lateral_accel <= 0.0):
    return 1.0

  overshoot = max(abs(measured_lateral_accel) - abs(setpoint), 0.0)
  if overshoot <= 0.0:
    return 1.0

  speed_weight = (_sigmoid((v_ego - KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_SPEED) /
                           KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_SPEED_WIDTH) *
                  _sigmoid((KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_SPEED_CUTOFF - v_ego) /
                           KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_SPEED_CUTOFF_WIDTH))
  overshoot_weight = _sigmoid((overshoot - KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_OVERSHOOT) /
                              KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_OVERSHOOT_WIDTH)
  jerk_weight = _sigmoid((abs(desired_lateral_jerk) - KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_JERK) /
                         KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_JERK_WIDTH)
  return 1.0 - (KIA_CARNIVAL_UNWIND_OUTPUT_DAMPING_MAX * speed_weight * overshoot_weight * jerk_weight)

def _tucson_4th_gen_center_weights(desired_lateral_accel: float, v_ego: float) -> tuple[float, float]:
  speed_weight = _sigmoid((TUCSON_4TH_GEN_CENTER_TAPER_SPEED_MAX - v_ego) / TUCSON_4TH_GEN_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _sigmoid((TUCSON_4TH_GEN_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / TUCSON_4TH_GEN_CENTER_TAPER_LAT_WIDTH)
  return speed_weight, center_weight

def get_tucson_4th_gen_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight, center_weight = _tucson_4th_gen_center_weights(desired_lateral_accel, v_ego)
  return 1.0 - (TUCSON_4TH_GEN_CENTER_TAPER_MAX * speed_weight * center_weight)

def get_tucson_4th_gen_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0,
                                          desired_lateral_jerk: float = 0.0) -> float:
  del desired_lateral_jerk
  speed_weight, center_weight = _tucson_4th_gen_center_weights(desired_lateral_accel, v_ego)
  return get_hkg_canfd_base_friction_threshold(v_ego) * (1.0 + TUCSON_4TH_GEN_FRICTION_THRESHOLD_GAIN * speed_weight * center_weight)

def _kia_forte_sigmoid(x: float) -> float:
  return _sigmoid(x)

def _kia_forte_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / KIA_FORTE_TRANSITION_SPEED) ** 2)

def _kia_forte_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / KIA_FORTE_PHASE_SCALE)

def _kia_forte_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value

def get_kia_forte_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _kia_forte_sigmoid((abs_lateral_accel - KIA_FORTE_FF_ONSET) / KIA_FORTE_FF_ONSET_WIDTH)
  cutoff = _kia_forte_sigmoid((KIA_FORTE_FF_CUTOFF - abs_lateral_accel) / KIA_FORTE_FF_CUTOFF_WIDTH)
  base_reduction = _kia_forte_side_value(desired_lateral_accel, KIA_FORTE_FF_REDUCTION_LEFT, KIA_FORTE_FF_REDUCTION_RIGHT) * onset * cutoff
  phase = _kia_forte_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  low_speed_factor = _kia_forte_low_speed_factor(v_ego)
  turn_in_boost = 1.0 + (_kia_forte_side_value(desired_lateral_accel, KIA_FORTE_TURN_IN_BOOST_LEFT, KIA_FORTE_TURN_IN_BOOST_RIGHT) *
                         turn_in_weight * (0.35 + 0.65 * low_speed_factor))
  unwind_taper = 1.0 - (_kia_forte_side_value(desired_lateral_accel, KIA_FORTE_UNWIND_TAPER_LEFT, KIA_FORTE_UNWIND_TAPER_RIGHT) *
                        unwind_weight * (0.35 + 0.65 * low_speed_factor))
  crawl_turn_in_scale = 0.0
  if desired_lateral_accel * desired_lateral_jerk > 0.0:
    crawl_speed_weight = _kia_forte_sigmoid((KIA_FORTE_CRAWL_TURN_IN_FF_SPEED - max(v_ego, 0.0)) /
                                            KIA_FORTE_CRAWL_TURN_IN_FF_SPEED_WIDTH)
    crawl_lat_weight = _kia_forte_sigmoid((abs_lateral_accel - KIA_FORTE_CRAWL_TURN_IN_FF_LAT) /
                                          KIA_FORTE_CRAWL_TURN_IN_FF_LAT_WIDTH)
    crawl_turn_in_scale = _kia_forte_side_value(desired_lateral_accel, KIA_FORTE_CRAWL_TURN_IN_FF_BOOST_LEFT,
                                                KIA_FORTE_CRAWL_TURN_IN_FF_BOOST_RIGHT) * crawl_speed_weight * crawl_lat_weight
  return ((1.0 - base_reduction) * turn_in_boost * max(unwind_taper, 0.0)) + crawl_turn_in_scale

def get_kia_forte_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _kia_forte_sigmoid((v_ego - KIA_FORTE_CENTER_TAPER_SPEED) / KIA_FORTE_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _kia_forte_sigmoid((KIA_FORTE_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / KIA_FORTE_CENTER_TAPER_LAT_WIDTH)
  reduction = KIA_FORTE_CENTER_TAPER_MAX * speed_weight * center_weight
  return 1.0 - reduction

def get_kia_forte_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  base_threshold = get_gm_base_friction_threshold(v_ego)
  speed_weight = _kia_forte_sigmoid((v_ego - KIA_FORTE_FRICTION_SPEED) / KIA_FORTE_FRICTION_SPEED_WIDTH)
  center_weight = _kia_forte_sigmoid((KIA_FORTE_FRICTION_CENTER_LAT - abs(desired_lateral_accel)) / KIA_FORTE_FRICTION_CENTER_LAT_WIDTH)
  calm_jerk_weight = _kia_forte_sigmoid((KIA_FORTE_FRICTION_CALM_JERK - abs(desired_lateral_jerk)) / KIA_FORTE_FRICTION_CALM_JERK_WIDTH)
  threshold_scale = 1.0 + (KIA_FORTE_FRICTION_THRESHOLD_GAIN * speed_weight * center_weight * calm_jerk_weight)
  return base_threshold * min(max(threshold_scale, 1.0), 1.18)

def _ioniq_5_sigmoid(x: float) -> float:
  return _sigmoid(x)

def _ioniq_5_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / IONIQ_5_TRANSITION_SPEED) ** 2)

def _ioniq_5_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / IONIQ_5_PHASE_SCALE)

def _ioniq_5_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value

def _ioniq_5_transition_envelope(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _ioniq_5_sigmoid((abs_lateral_accel - IONIQ_5_FF_ONSET) / IONIQ_5_FF_ONSET_WIDTH)
  cutoff = _ioniq_5_sigmoid((IONIQ_5_FF_CUTOFF - abs_lateral_accel) / IONIQ_5_FF_CUTOFF_WIDTH)
  return onset * cutoff * _ioniq_5_low_speed_factor(v_ego)

def get_ioniq_5_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  envelope = _ioniq_5_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _ioniq_5_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  low_speed_factor = _ioniq_5_low_speed_factor(v_ego)
  abs_lateral_accel = abs(desired_lateral_accel)

  base_reduction = _ioniq_5_side_value(desired_lateral_accel, IONIQ_5_FF_REDUCTION_LEFT, IONIQ_5_FF_REDUCTION_RIGHT) * envelope
  turn_in_boost = 1.0 + (_ioniq_5_side_value(desired_lateral_accel, IONIQ_5_TURN_IN_BOOST_LEFT, IONIQ_5_TURN_IN_BOOST_RIGHT) *
                          turn_in_weight * (0.35 + 0.65 * low_speed_factor))
  unwind_taper = 1.0 - (_ioniq_5_side_value(desired_lateral_accel, IONIQ_5_UNWIND_TAPER_LEFT, IONIQ_5_UNWIND_TAPER_RIGHT) *
                         unwind_weight * (0.35 + 0.65 * low_speed_factor))
  sustained_turn_in_scale = 0.0
  if desired_lateral_accel * desired_lateral_jerk > 0.0:
    sustained_speed_weight = _ioniq_5_sigmoid((max(v_ego, 0.0) - IONIQ_5_SUSTAINED_TURN_IN_FF_SPEED) /
                                              IONIQ_5_SUSTAINED_TURN_IN_FF_SPEED_WIDTH)
    sustained_lat_onset = _ioniq_5_sigmoid((abs_lateral_accel - IONIQ_5_SUSTAINED_TURN_IN_FF_LAT_START) /
                                           IONIQ_5_SUSTAINED_TURN_IN_FF_LAT_WIDTH)
    sustained_lat_cutoff = _ioniq_5_sigmoid((IONIQ_5_SUSTAINED_TURN_IN_FF_LAT_END - abs_lateral_accel) /
                                            IONIQ_5_SUSTAINED_TURN_IN_FF_LAT_WIDTH)
    sustained_turn_in_scale = (_ioniq_5_side_value(desired_lateral_accel,
                                                   IONIQ_5_SUSTAINED_TURN_IN_FF_BOOST_LEFT,
                                                   IONIQ_5_SUSTAINED_TURN_IN_FF_BOOST_RIGHT) *
                               sustained_speed_weight * sustained_lat_onset * sustained_lat_cutoff)
  return (1.0 + sustained_turn_in_scale) * (1.0 - base_reduction) * turn_in_boost * max(unwind_taper, 0.0)

def get_ioniq_5_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  base_threshold = get_hkg_canfd_base_friction_threshold(v_ego)
  envelope = _ioniq_5_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _ioniq_5_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)

  threshold_scale = 1.0 - (_ioniq_5_side_value(desired_lateral_accel, IONIQ_5_TURN_IN_THRESHOLD_REDUCTION_LEFT, IONIQ_5_TURN_IN_THRESHOLD_REDUCTION_RIGHT) *
                           envelope * turn_in_weight)
  threshold_scale += (_ioniq_5_side_value(desired_lateral_accel, IONIQ_5_UNWIND_THRESHOLD_INCREASE_LEFT, IONIQ_5_UNWIND_THRESHOLD_INCREASE_RIGHT) *
                      envelope * unwind_weight)
  return base_threshold * min(max(threshold_scale, 0.86), 1.18)

def get_ioniq_5_friction_scale(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  if desired_lateral_accel == 0.0 or desired_lateral_jerk == 0.0:
    return 1.0

  envelope = _ioniq_5_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _ioniq_5_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)

  friction_scale = 1.0
  friction_scale += (_ioniq_5_side_value(desired_lateral_accel, IONIQ_5_TURN_IN_FRICTION_BOOST_LEFT, IONIQ_5_TURN_IN_FRICTION_BOOST_RIGHT) *
                     envelope * turn_in_weight)
  friction_scale -= (_ioniq_5_side_value(desired_lateral_accel, IONIQ_5_UNWIND_FRICTION_REDUCTION_LEFT, IONIQ_5_UNWIND_FRICTION_REDUCTION_RIGHT) *
                     envelope * unwind_weight)
  return min(max(friction_scale, 0.86), 1.04)

def get_ioniq_5_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _ioniq_5_sigmoid((v_ego - IONIQ_5_CENTER_TAPER_SPEED) / IONIQ_5_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _ioniq_5_sigmoid((IONIQ_5_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / IONIQ_5_CENTER_TAPER_LAT_WIDTH)
  reduction = IONIQ_5_CENTER_TAPER_MAX * speed_weight * center_weight
  return 1.0 - reduction

def get_ioniq_5_low_speed_output_limit(desired_lateral_accel: float,
                                       desired_lateral_jerk: float, v_ego: float,
                                       desired_curvature: float = 0.0, output_torque: float = 0.0) -> float:
  """Bound stop-transition center chatter without blunting actual turns."""
  speed_weight = _ioniq_5_sigmoid((IONIQ_5_LOW_SPEED_OUTPUT_LIMIT_SPEED - max(v_ego, 0.0)) /
                                  IONIQ_5_LOW_SPEED_OUTPUT_LIMIT_SPEED_WIDTH)
  center_weight = _ioniq_5_sigmoid((IONIQ_5_LOW_SPEED_CENTER_LAT - abs(desired_lateral_accel)) /
                                   IONIQ_5_LOW_SPEED_CENTER_LAT_WIDTH)
  calm_weight = _ioniq_5_sigmoid((IONIQ_5_LOW_SPEED_CENTER_JERK - abs(desired_lateral_jerk)) /
                                 IONIQ_5_LOW_SPEED_CENTER_JERK_WIDTH)
  center_weight *= calm_weight
  center_limit = (IONIQ_5_LOW_SPEED_OUTPUT_LIMIT_BASE +
                  IONIQ_5_LOW_SPEED_OUTPUT_LIMIT_TURN_RELIEF * (1.0 - center_weight))
  limit = 1.0 - speed_weight * (1.0 - center_limit)
  if desired_curvature * output_torque > 0.0:
    turn_weight = np.interp(abs(desired_curvature), IONIQ_5_LOW_SPEED_TURN_CURVATURE_BP, [0.0, 1.0])
    limit += max(IONIQ_5_LOW_SPEED_TURN_OUTPUT_FLOOR - limit, 0.0) * turn_weight
  return float(np.clip(limit, IONIQ_5_LOW_SPEED_OUTPUT_LIMIT_BASE, 1.0))

def get_ioniq_5_friction_jerk_deadzone(v_ego: float, desired_lateral_accel: float) -> float:
  speed_weight = _ioniq_5_sigmoid((max(v_ego, 0.0) - IONIQ_5_FRICTION_JERK_DEADZONE_SPEED) /
                                  IONIQ_5_FRICTION_JERK_DEADZONE_SPEED_WIDTH)
  curve_weight = _ioniq_5_sigmoid((IONIQ_5_FRICTION_JERK_DEADZONE_LAT - abs(desired_lateral_accel)) /
                                   IONIQ_5_FRICTION_JERK_DEADZONE_LAT_WIDTH)
  return IONIQ_5_FRICTION_JERK_DEADZONE_MAX * speed_weight * curve_weight

def _ioniq_ev_old_sigmoid(x: float) -> float:
  return _sigmoid(x)

def _ioniq_ev_old_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / IONIQ_EV_OLD_TRANSITION_SPEED) ** 2)

def _ioniq_ev_old_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / IONIQ_EV_OLD_PHASE_SCALE)

def _ioniq_ev_old_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value

def get_ioniq_ev_old_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _ioniq_ev_old_sigmoid((abs_lateral_accel - IONIQ_EV_OLD_FF_ONSET) / IONIQ_EV_OLD_FF_ONSET_WIDTH)
  cutoff = _ioniq_ev_old_sigmoid((IONIQ_EV_OLD_FF_CUTOFF - abs_lateral_accel) / IONIQ_EV_OLD_FF_CUTOFF_WIDTH)
  base_reduction = _ioniq_ev_old_side_value(desired_lateral_accel, IONIQ_EV_OLD_FF_REDUCTION_LEFT, IONIQ_EV_OLD_FF_REDUCTION_RIGHT) * onset * cutoff
  phase = _ioniq_ev_old_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  low_speed_factor = _ioniq_ev_old_low_speed_factor(v_ego)
  turn_in_boost = 1.0 + (_ioniq_ev_old_side_value(desired_lateral_accel, IONIQ_EV_OLD_TURN_IN_BOOST_LEFT, IONIQ_EV_OLD_TURN_IN_BOOST_RIGHT) *
                         turn_in_weight * (0.35 + 0.65 * low_speed_factor))
  unwind_taper = 1.0 - (_ioniq_ev_old_side_value(desired_lateral_accel, IONIQ_EV_OLD_UNWIND_TAPER_LEFT, IONIQ_EV_OLD_UNWIND_TAPER_RIGHT) *
                        unwind_weight * (0.35 + 0.65 * low_speed_factor))
  return (1.0 - base_reduction) * turn_in_boost * max(unwind_taper, 0.0)

def get_ioniq_ev_old_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _ioniq_ev_old_sigmoid((v_ego - IONIQ_EV_OLD_CENTER_TAPER_SPEED) / IONIQ_EV_OLD_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _ioniq_ev_old_sigmoid((IONIQ_EV_OLD_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / IONIQ_EV_OLD_CENTER_TAPER_LAT_WIDTH)
  reduction = IONIQ_EV_OLD_CENTER_TAPER_MAX * speed_weight * center_weight
  return 1.0 - reduction

def kia_ev6_lateral_testing_ground_active():
  return False

def _kia_ev6_sigmoid(x: float) -> float:
  return _sigmoid(x)

def _kia_ev6_low_speed_factor(v_ego: float) -> float:
  return 1.0 / (1.0 + (max(v_ego, 0.0) / KIA_EV6_TRANSITION_SPEED) ** 2)

def _kia_ev6_transition_phase(desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  return math.tanh((desired_lateral_accel * desired_lateral_jerk) / KIA_EV6_PHASE_SCALE)

def _kia_ev6_side_value(desired_lateral_accel: float, left_value: float, right_value: float) -> float:
  return left_value if desired_lateral_accel >= 0.0 else right_value

def _kia_ev6_transition_envelope(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  lat_factor = 1.0 - math.exp(-abs(desired_lateral_accel) / KIA_EV6_FRICTION_LAT_RISE)
  jerk_factor = 1.0 - math.exp(-abs(desired_lateral_jerk) / KIA_EV6_FRICTION_JERK_RISE)
  return _kia_ev6_low_speed_factor(v_ego) * lat_factor * jerk_factor

def get_kia_ev6_jwarm_phase_confidence(v_ego: float, desired_lateral_jerk: float) -> float:
  low_speed_weight = _kia_ev6_sigmoid(
    (KIA_EV6_JWARM_PHASE_STABILITY_SPEED - v_ego) / KIA_EV6_JWARM_PHASE_STABILITY_SPEED_WIDTH
  )
  abrupt_transition_weight = _kia_ev6_sigmoid(
    (abs(desired_lateral_jerk) - KIA_EV6_JWARM_PHASE_STABILITY_JERK) / KIA_EV6_JWARM_PHASE_STABILITY_JERK_WIDTH
  )
  return 1.0 - (KIA_EV6_JWARM_PHASE_STABILITY_MAX_REDUCTION * low_speed_weight * abrupt_transition_weight)

def get_kia_ev6_ff_scale(desired_lateral_accel: float, desired_lateral_jerk: float, v_ego: float) -> float:
  if desired_lateral_accel == 0.0:
    return 1.0

  gain = _kia_ev6_side_value(
    desired_lateral_accel,
    _flm_vehicle_knob("hyundai_kia_ev6.ff_gain_left", KIA_EV6_FF_GAIN_LEFT),
    _flm_vehicle_knob("hyundai_kia_ev6.ff_gain_right", KIA_EV6_FF_GAIN_RIGHT),
  )
  abs_lateral_accel = abs(desired_lateral_accel)
  onset = _kia_ev6_sigmoid((abs_lateral_accel - KIA_EV6_FF_ONSET) / KIA_EV6_FF_ONSET_WIDTH)
  cutoff = _kia_ev6_sigmoid((KIA_EV6_FF_CUTOFF - abs_lateral_accel) / KIA_EV6_FF_CUTOFF_WIDTH)
  extra_scale = gain * onset * cutoff
  phase = _kia_ev6_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  low_speed_factor = _kia_ev6_low_speed_factor(v_ego)
  turn_in_boost = 1.0 + (_kia_ev6_side_value(
                          desired_lateral_accel,
                          _flm_vehicle_knob("hyundai_kia_ev6.turn_in_boost_left", KIA_EV6_TURN_IN_BOOST_LEFT),
                          _flm_vehicle_knob("hyundai_kia_ev6.turn_in_boost_right", KIA_EV6_TURN_IN_BOOST_RIGHT),
                        ) *
                          turn_in_weight * (0.35 + 0.65 * low_speed_factor))
  unwind_taper = 1.0 - (_kia_ev6_side_value(
                         desired_lateral_accel,
                         _flm_vehicle_knob("hyundai_kia_ev6.unwind_taper_left", KIA_EV6_UNWIND_TAPER_LEFT),
                         _flm_vehicle_knob("hyundai_kia_ev6.unwind_taper_right", KIA_EV6_UNWIND_TAPER_RIGHT),
                       ) *
                         unwind_weight * (0.35 + 0.65 * low_speed_factor))
  jwarm_tune = kia_ev6_lateral_testing_ground_active()
  jwarm_phase_confidence = get_kia_ev6_jwarm_phase_confidence(v_ego, desired_lateral_jerk) if jwarm_tune else 0.0
  base_turn_in_boost = 1.0 + ((_kia_ev6_side_value(
                                desired_lateral_accel,
                                KIA_EV6_JWARM_BASE_TURN_IN_BOOST_LEFT,
                                KIA_EV6_JWARM_BASE_TURN_IN_BOOST_RIGHT,
                              ) if jwarm_tune else 0.0) *
                                jwarm_phase_confidence * turn_in_weight * onset * cutoff)
  base_unwind_taper_left = _flm_vehicle_knob("hyundai_kia_ev6.base_unwind_taper_left", KIA_EV6_BASE_UNWIND_TAPER_LEFT)
  base_unwind_taper_right = _flm_vehicle_knob("hyundai_kia_ev6.base_unwind_taper_right", KIA_EV6_BASE_UNWIND_TAPER_RIGHT)
  if jwarm_tune:
    base_unwind_taper_left += (KIA_EV6_JWARM_BASE_UNWIND_TAPER_LEFT - base_unwind_taper_left) * jwarm_phase_confidence
    base_unwind_taper_right += (KIA_EV6_JWARM_BASE_UNWIND_TAPER_RIGHT - base_unwind_taper_right) * jwarm_phase_confidence
  base_unwind_taper = 1.0 - (_kia_ev6_side_value(
                              desired_lateral_accel,
                              base_unwind_taper_left,
                              base_unwind_taper_right,
                            ) * unwind_weight * onset * cutoff)
  return (base_unwind_taper * base_turn_in_boost) + (extra_scale * turn_in_boost * max(unwind_taper, 0.0))

def get_kia_ev6_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0, desired_lateral_jerk: float = 0.0) -> float:
  base_threshold = get_hkg_canfd_base_friction_threshold(v_ego)
  transition_envelope = _kia_ev6_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _kia_ev6_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  threshold_scale = 1.0 - (_kia_ev6_side_value(
                           desired_lateral_accel,
                           _flm_vehicle_knob("hyundai_kia_ev6.turn_in_threshold_reduction_left", KIA_EV6_TURN_IN_THRESHOLD_REDUCTION_LEFT),
                           _flm_vehicle_knob("hyundai_kia_ev6.turn_in_threshold_reduction_right", KIA_EV6_TURN_IN_THRESHOLD_REDUCTION_RIGHT),
                         ) *
                           transition_envelope * turn_in_weight)
  threshold_scale += (_kia_ev6_side_value(
                      desired_lateral_accel,
                      _flm_vehicle_knob("hyundai_kia_ev6.unwind_threshold_increase_left", KIA_EV6_UNWIND_THRESHOLD_INCREASE_LEFT),
                      _flm_vehicle_knob("hyundai_kia_ev6.unwind_threshold_increase_right", KIA_EV6_UNWIND_THRESHOLD_INCREASE_RIGHT),
                    ) *
                      transition_envelope * unwind_weight)
  center_speed_weight = _kia_ev6_sigmoid((v_ego - KIA_EV6_CENTER_FRICTION_THRESHOLD_SPEED) /
                                         KIA_EV6_CENTER_FRICTION_THRESHOLD_SPEED_WIDTH)
  center_lat_weight = _kia_ev6_sigmoid((KIA_EV6_CENTER_FRICTION_THRESHOLD_LAT - abs(desired_lateral_accel)) /
                                       KIA_EV6_CENTER_FRICTION_THRESHOLD_LAT_WIDTH)
  threshold_scale += (_flm_vehicle_knob("hyundai_kia_ev6.center_friction_threshold_gain",
                                        KIA_EV6_CENTER_FRICTION_THRESHOLD_GAIN) *
                      center_speed_weight * center_lat_weight)
  return base_threshold * min(max(threshold_scale, 0.82), 1.16)

def get_kia_ev6_friction_scale(v_ego: float, desired_lateral_accel: float, desired_lateral_jerk: float) -> float:
  transition_envelope = _kia_ev6_transition_envelope(v_ego, desired_lateral_accel, desired_lateral_jerk)
  phase = _kia_ev6_transition_phase(desired_lateral_accel, desired_lateral_jerk)
  turn_in_weight = max(phase, 0.0)
  unwind_weight = max(-phase, 0.0)
  friction_scale = KIA_EV6_FRICTION_MULT
  friction_scale += (_kia_ev6_side_value(desired_lateral_accel, KIA_EV6_TURN_IN_FRICTION_BOOST_LEFT, KIA_EV6_TURN_IN_FRICTION_BOOST_RIGHT) *
                     transition_envelope * turn_in_weight)
  friction_scale -= (_kia_ev6_side_value(desired_lateral_accel, KIA_EV6_UNWIND_FRICTION_REDUCTION_LEFT, KIA_EV6_UNWIND_FRICTION_REDUCTION_RIGHT) *
                     transition_envelope * unwind_weight)
  return min(max(friction_scale, 0.90), 1.10)

def get_kia_ev6_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _kia_ev6_sigmoid((v_ego - KIA_EV6_CENTER_TAPER_SPEED) / KIA_EV6_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _kia_ev6_sigmoid((KIA_EV6_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / KIA_EV6_CENTER_TAPER_LAT_WIDTH)
  reduction = _flm_vehicle_knob("hyundai_kia_ev6.center_taper_max", KIA_EV6_CENTER_TAPER_MAX) * speed_weight * center_weight
  return 1.0 - reduction

def get_kia_ev6_low_speed_center_taper_scale(desired_lateral_accel: float, v_ego: float) -> float:
  speed_weight = _kia_ev6_sigmoid((KIA_EV6_LOW_SPEED_CENTER_TAPER_SPEED_MAX - v_ego) / KIA_EV6_LOW_SPEED_CENTER_TAPER_SPEED_WIDTH)
  center_weight = _kia_ev6_sigmoid((KIA_EV6_LOW_SPEED_CENTER_TAPER_LAT - abs(desired_lateral_accel)) / KIA_EV6_LOW_SPEED_CENTER_TAPER_LAT_WIDTH)
  reduction = KIA_EV6_LOW_SPEED_CENTER_TAPER_MAX * speed_weight * center_weight
  return 1.0 - reduction

def get_kia_ev6_center_output_scale(desired_lateral_accel: float, v_ego: float,
                                  future_lateral_accel: float | None = None) -> float:
  speed_weight = _kia_ev6_sigmoid((v_ego - KIA_EV6_CENTER_OUTPUT_TAPER_SPEED) /
                                  KIA_EV6_CENTER_OUTPUT_TAPER_SPEED_WIDTH)
  center_weight = _kia_ev6_sigmoid((KIA_EV6_CENTER_OUTPUT_TAPER_LAT - abs(desired_lateral_accel)) /
                                   KIA_EV6_CENTER_OUTPUT_TAPER_LAT_WIDTH)
  reduction = _flm_vehicle_knob("hyundai_kia_ev6.center_output_taper_max", KIA_EV6_CENTER_OUTPUT_TAPER_MAX) * speed_weight * center_weight
  future_accel = abs(desired_lateral_accel) if future_lateral_accel is None else max(abs(desired_lateral_accel), abs(future_lateral_accel))
  future_center_weight = _kia_ev6_sigmoid((KIA_EV6_CENTER_OUTPUT_TAPER_LAT - future_accel) /
                                         KIA_EV6_CENTER_OUTPUT_TAPER_LAT_WIDTH)
  reduction += KIA_EV6_HIGHWAY_CENTER_DAMPING * speed_weight * future_center_weight
  return 1.0 - reduction


IONIQ_5_LOW_SPEED_TURN_CURVATURE_BP = [0.005, 0.020]
IONIQ_5_LOW_SPEED_TURN_OUTPUT_FLOOR = 0.30
KIA_STINGER_2022_HIGHWAY_FRICTION_INCREMENT = 0.20
KIA_STINGER_2022_HIGHWAY_FRICTION_JERK_BP = [0.20, 0.75]
KIA_STINGER_2022_HIGHWAY_FRICTION_LAT_BP = [0.15, 0.40]
KIA_STINGER_2022_HIGHWAY_FRICTION_SPEED_BP = [50.0 * CV.MPH_TO_MS, 60.0 * CV.MPH_TO_MS]


KIA_NIRO_EV_CENTER_FRICTION_SPEED_BP = [60.0 * CV.MPH_TO_MS, 65.0 * CV.MPH_TO_MS]

KIA_NIRO_EV_CENTER_FRICTION_LAT_BP = [0.15, 0.35]

KIA_NIRO_EV_CENTER_FRICTION_JERK_BP = [0.20, 0.50]

KIA_NIRO_EV_CENTER_FRICTION_THRESHOLD_GAIN = 0.35

KIA_CARNIVAL_HEV_CENTER_SPEED_BP = [1.0, 4.0, 20.0, 25.0]

KIA_CARNIVAL_HEV_CENTER_LAT_BP = [0.18, 0.40]

KIA_CARNIVAL_HEV_CENTER_ERROR_BP = [0.12, 0.30]

KIA_CARNIVAL_HEV_CENTER_JERK_BP = [0.25, 0.75]

KIA_CARNIVAL_HEV_CENTER_FRICTION_INCREMENT = 0.20

KIA_CARNIVAL_HEV_CENTER_ERROR_REDUCTION = 0.25

def get_kia_niro_ev_friction_threshold(v_ego: float, desired_lateral_accel: float = 0.0,
                                      desired_lateral_jerk: float = 0.0,
                                      future_lateral_accel: float | None = None) -> float:
  speed_weight = np.interp(v_ego, KIA_NIRO_EV_CENTER_FRICTION_SPEED_BP, [0.0, 1.0])
  requested_accel = max(abs(desired_lateral_accel), abs(future_lateral_accel or 0.0))
  center_weight = np.interp(requested_accel, KIA_NIRO_EV_CENTER_FRICTION_LAT_BP, [1.0, 0.0])
  calm_weight = np.interp(abs(desired_lateral_jerk), KIA_NIRO_EV_CENTER_FRICTION_JERK_BP, [1.0, 0.0])
  gain = KIA_NIRO_EV_CENTER_FRICTION_THRESHOLD_GAIN * speed_weight * center_weight * calm_weight
  return float(get_standard_friction_threshold(v_ego) * (1.0 + gain))

def get_kia_carnival_hev_center_weight(v_ego: float, setpoint: float, measurement: float, jerk: float,
                                       future_lateral_accel: float = 0.0) -> float:
  speed_weight = np.interp(v_ego, KIA_CARNIVAL_HEV_CENTER_SPEED_BP, [0.0, 1.0, 1.0, 0.0])
  center_weight = np.interp(max(abs(setpoint), abs(measurement), abs(future_lateral_accel)), KIA_CARNIVAL_HEV_CENTER_LAT_BP, [1.0, 0.0])
  tracking_weight = np.interp(abs(setpoint - measurement), KIA_CARNIVAL_HEV_CENTER_ERROR_BP, [1.0, 0.0])
  calm_weight = np.interp(abs(jerk), KIA_CARNIVAL_HEV_CENTER_JERK_BP, [1.0, 0.0])
  return float(speed_weight * center_weight * tracking_weight * calm_weight)
