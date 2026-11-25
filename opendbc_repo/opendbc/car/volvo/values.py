from dataclasses import dataclass, field
from enum import IntFlag

from opendbc.car.structs import CarParams
from opendbc.car import Bus, CarSpecs, DbcDict, PlatformConfig, Platforms, uds
from opendbc.car.lateral import AngleSteeringLimits
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.docs_definitions import CarDocs, CarHarness, CarParts
from opendbc.car.fw_query_definitions import FwQueryConfig, Request, p16

# C1 support is adapted from the original dragonpilot V40 port:
# https://github.com/dragonpilot/dragonpilot/commit/773dce507082d931236b64dca8024dce9625446f
# V60 (EUCD) support is adapted from sunnypilot's volvo_oplong port.
Ecu = CarParams.Ecu


class VolvoSafetyFlags(IntFlag):
  C1 = 2
  EUCD = 8


class CarControllerParams:
  ANGLE_ERROR = 3.0
  LCA_AUTH_MAX = 614  # signal saturation
  LCA_AUTH_PLATEAU_COUNTER = 130  # counter-arm magnitude during sustained override
  LCA_AUTH_OVERRIDE_ENTER = 5  # trigger threshold
  LCA_AUTH_OVERRIDE_EXIT = 3  # trigger threshold
  LCA_AUTH_LIGHT_THRESH = 3  # min |drv| to consider as contact
  LCA_AUTH_LIGHT_RISE_DELTA = 1.0  # min per-frame increase in |drv| to count as rising contact
  LCA_AUTH_LIGHT_HOLD_FRAMES = 15  # ~150 ms of yield on fresh light contact
  LCA_AUTH_LIGHT_COOLDOWN_FRAMES = 30  # ~300 ms quiet-time on real_override before light contact re-arms
  LCA_AUTH_YIELD_BASE = 60        # yield-arm magnitude at the override threshold
  LCA_AUTH_YIELD_SLOPE = 8        # counts of yield reduction per unit |drv torque| above threshold
  LCA_AUTH_YIELD_MIN = 0          # yield authority without crossing the safety sign boundary
  LCA_AUTH_YIELD_LP_ALPHA = 0.04  # LP-filter coefficient on |drv| for yield calc (~250 ms tau)
  LCA_AUTH_SPLIT = 200            # symmetric → asymmetric handover
  LCA_AUTH_REBUILD_RATE = 230     # counts/s (≈ 2.7 s rebuild from 0 to 614)
  LCA_AUTH_COLLAPSE_RATE = 2500   # counts/s base (scales with |drv|/THRESH for sharper pothole jolts)

  ANGLE_LIMITS: AngleSteeringLimits = AngleSteeringLimits(
    540, # deg - 1.5 turns to lock
    ([0., 5., 25.], [2.5, 1.5, .2]),  # rate up limits at different speeds
    ([0., 5., 25.], [5., 2., .3]),    # rate down limits at different speeds
  )

  C1_STEER_NO = 0
  C1_STEER = 3
  C1_N_ZERO_TORQUE = 12
  C1_ANGLE_ERROR = 20.0
  C1_ANGLE_DELTA_BP = [0., 8.33, 13.89, 19.44, 25., 30.55, 36.1]
  C1_ANGLE_DELTA_UP = [2., 1.2, .25, .20, .15, .10, .10]
  C1_ANGLE_DELTA_DOWN = [2., 1.2, .25, .20, .15, .10, .10]
  C1_ANGLE_LIMITS: AngleSteeringLimits = AngleSteeringLimits(
    359.9,
    (C1_ANGLE_DELTA_BP, C1_ANGLE_DELTA_UP),
    (C1_ANGLE_DELTA_BP, C1_ANGLE_DELTA_DOWN),
  )


@dataclass
class VolvoCarDocs(CarDocs):
  package: str = "Pilot Assist & Adaptive Cruise Control"
  car_parts: CarParts = field(default_factory=CarParts.common([CarHarness.custom]))


@dataclass
class VolvoSPAPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: {
    Bus.main: 'volvo_mid_1',
    Bus.party: 'volvo_mid_1',
    Bus.pt: 'volvo_front_1_spa',
  })


@dataclass
class VolvoC1PlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: {
    Bus.pt: 'volvo_v40_2017_pt',
    Bus.cam: 'volvo_v40_2017_pt',
  })


@dataclass
class VolvoEUCDPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: {Bus.pt: 'volvo_v60_2015_pt', Bus.radar: 'ESR'})


class CAR(Platforms):
  VOLVO_V40 = VolvoC1PlatformConfig(
    [VolvoCarDocs("Volvo V40 2013-19")],
    CarSpecs(
      mass=1610,
      wheelbase=2.647,
      steerRatio=14.7,
      centerToFrontRatio=0.44,
      minSteerSpeed=1.0 * CV.KPH_TO_MS,
    ),
  )

  VOLVO_XC40_RECHARGE = VolvoSPAPlatformConfig(
    [VolvoCarDocs("Volvo XC40 Recharge 2021-23")],
    CarSpecs(
      mass=2170,
      wheelbase=2.702,
      steerRatio=15.8,
      centerToFrontRatio=0.52,
    ),
  )

  VOLVO_S60_RECHARGE = VolvoSPAPlatformConfig(
    [VolvoCarDocs("Volvo S60 Recharge 2024")],
    CarSpecs(
      mass=2020,
      wheelbase=2.872,
      steerRatio=16.2,
      centerToFrontRatio=0.516,
    ),
  )

  POLESTAR_2 = VolvoSPAPlatformConfig(
    [VolvoCarDocs("Polestar 2 2020-25")],
    CarSpecs(
      mass=2123,
      wheelbase=2.735,
      steerRatio=15.8,
      centerToFrontRatio=0.52,
    ),
  )

  VOLVO_V60 = VolvoEUCDPlatformConfig(
    [VolvoCarDocs("Volvo V60 2010-18")],
    # minSteerSpeed stays 0: EUCD_MIN_STEER_SPEED is the port's limit, above 0 an alert fires at every first stop
    CarSpecs(mass=1750, wheelbase=2.776, steerRatio=15.0, centerToFrontRatio=0.44, minSteerSpeed=0.),
  )

# The V60's CAN fingerprint is not exhaustive: it is identified by its engine FW. The others by CAN only.
VOLVO_VERSION_REQUEST = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER]) + p16(0xf1a2)
VOLVO_VERSION_RESPONSE = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER + 0x40]) + p16(0xf1a2)
FW_QUERY_CONFIG = FwQueryConfig(
  requests=[Request([VOLVO_VERSION_REQUEST], [VOLVO_VERSION_RESPONSE], bus=0, brand_query_only=True)],
  fw_version_regex=br"[0-9]{8} [A-Z]{2}\x00{13}",
)

DBC = CAR.create_dbc_map()
