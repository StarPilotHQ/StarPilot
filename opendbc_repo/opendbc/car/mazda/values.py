from dataclasses import dataclass, field
from enum import Enum, IntFlag

from opendbc.car import Bus, CarSpecs, DbcDict, PlatformConfig, Platforms
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.structs import CarParams
from opendbc.car.docs_definitions import CarFootnote, CarHarness, CarDocs, CarParts, Column
from opendbc.car.fw_query_definitions import FwQueryConfig, Request, StdQueries

Ecu = CarParams.Ecu

class CarControllerParams:
  def __init__(self, CP):
    self.STEER_STEP = 1 # 100 Hz
    if CP.flags & MazdaSafetyFlags.GEN1:
      self.STEER_MAX = 800                # theoretical max_steer 2047
      self.STEER_DELTA_UP = 10             # torque increase per refresh
      self.STEER_DELTA_DOWN = 25           # torque decrease per refresh
      self.STEER_DRIVER_ALLOWANCE = 15     # allowed driver torque before start limiting
      self.STEER_DRIVER_MULTIPLIER = 40     # weight driver torque
      self.STEER_DRIVER_FACTOR = 1         # from dbc
      self.STEER_ERROR_MAX = 350           # max delta between torque cmd and torque motor

      self.TI_STEER_MAX = 600                # theoretical max_steer 2047
      self.TI_STEER_DELTA_UP = 6             # torque increase per refresh
      self.TI_STEER_DELTA_DOWN = 15           # torque decrease per refresh
      self.TI_STEER_DRIVER_ALLOWANCE = 15    # allowed driver torque before start limiting
      self.TI_STEER_DRIVER_MULTIPLIER = 40     # weight driver torque
      self.TI_STEER_DRIVER_FACTOR = 1         # from dbc
      self.TI_STEER_ERROR_MAX = 350           # max delta between torque cmd and torque motor
    if CP.flags & (MazdaSafetyFlags.GEN2 | MazdaSafetyFlags.GEN3):
      self.STEER_MAX = 8000
      self.STEER_DELTA_UP = 45              # torque increase per refresh
      self.STEER_DELTA_DOWN = 80            # torque decrease per refresh
      self.STEER_DRIVER_ALLOWANCE = 1400     # allowed driver torque before start limiting
      self.STEER_DRIVER_MULTIPLIER = 5      # weight driver torque
      self.STEER_DRIVER_FACTOR = 1           # from dbc
      self.STEER_ERROR_MAX = 3500            # max delta between torque cmd and torque motor

class TI_STATE:
  DISCOVER = 0
  OFF = 1
  DRIVER_OVER = 2
  RUN = 3


@dataclass
class MazdaCarDocs(CarDocs):
  package: str = "All"
  car_parts: CarParts = field(default_factory=CarParts.common([CarHarness.mazda]))
  footnotes: list[Enum] = field(default_factory=lambda: [Footnote.COMMA_POWER_REQUIRED])
  requires_comma_power: bool = True


class Footnote(Enum):
  COMMA_POWER_REQUIRED = CarFootnote(
    "Mazda vehicles <b>require</b> comma power installation.",
    Column.MAKE, setup_note=True)


@dataclass(frozen=True, kw_only=True)
class MazdaCarSpecs(CarSpecs):
  tireStiffnessFactor: float = 0.7  # not optimized yet


class MazdaSafetyFlags(IntFlag):
  # Safety flags
  GEN1 = 1
  GEN2 = 2
  GEN3 = 4
  TORQUE_INTERCEPTOR = 8
  RADAR_INTERCEPTOR = 16
  NO_FSC = 32
  NO_MRCC = 64
  MANUAL_TRANSMISSION = 128


@dataclass
class MazdaPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: {Bus.pt: 'mazda_2017'})
  def init(self):
    if self.flags & MazdaSafetyFlags.GEN2:
      self.dbc_dict = {Bus.pt: 'mazda_2019'}
    elif self.flags & MazdaSafetyFlags.GEN1 and self.flags & MazdaSafetyFlags.RADAR_INTERCEPTOR:
      self.dbc_dict = {Bus.pt: 'mazda_2017', Bus.radar: 'mazda_radar'}
    elif self.flags & MazdaSafetyFlags.GEN3:
      self.dbc_dict = {Bus.pt: 'mazda_2023'}


class CAR(Platforms):
  MAZDA_CX5 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda CX-5 2017-21")],
    MazdaCarSpecs(mass=3655 * CV.LB_TO_KG, wheelbase=2.7, steerRatio=15.5, centerToFrontRatio=0.41),
    flags=MazdaSafetyFlags.GEN1,
  )
  MAZDA_CX8 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda CX-8 2018-23")],
    MazdaCarSpecs(mass=4314 * CV.LB_TO_KG, wheelbase=2.930, steerRatio=15.5, centerToFrontRatio=0.41),
    flags=MazdaSafetyFlags.GEN1,
  )
  MAZDA_CX9 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda CX-9 2016-20")],
    MazdaCarSpecs(mass=4217 * CV.LB_TO_KG, wheelbase=3.1, steerRatio=17.6, centerToFrontRatio=0.41),
    flags=MazdaSafetyFlags.GEN1,
  )
  MAZDA_3 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda 3 2017-18")],
    MazdaCarSpecs(mass=2875 * CV.LB_TO_KG, wheelbase=2.7, steerRatio=14.0, centerToFrontRatio=0.41),
    flags=MazdaSafetyFlags.GEN1,
  )
  MAZDA_6 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda 6 2017-20")],
    MazdaCarSpecs(mass=3443 * CV.LB_TO_KG, wheelbase=2.83, steerRatio=15.5, centerToFrontRatio=0.41),
    flags=MazdaSafetyFlags.GEN1,
  )
  MAZDA_CX9_2021 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda CX-9 2021-23", video="https://youtu.be/dA3duO4a0O4")],
    MAZDA_CX9.specs,
    flags=MazdaSafetyFlags.GEN1,
  )
  MAZDA_CX5_2022 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda CX-5 2022-25")],
    MAZDA_CX5.specs,
    flags=MazdaSafetyFlags.GEN1,
  )
  MAZDA_3_2019 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda 3 2019-24")],
    MazdaCarSpecs(mass=3300 * CV.LB_TO_KG, wheelbase=2.725, steerRatio=15.59, centerToFrontRatio=0.38),
    flags=MazdaSafetyFlags.GEN2,
  )
  MAZDA_CX_30 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda CX-30 2019-24")],
    MazdaCarSpecs(mass=3375 * CV.LB_TO_KG, wheelbase=2.814, steerRatio=15.5, centerToFrontRatio=0.38),
    flags=MazdaSafetyFlags.GEN2,
  )
  MAZDA_CX_50 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda CX-50 2022-24")],
    MazdaCarSpecs(mass=3375 * CV.LB_TO_KG, wheelbase=2.814, steerRatio=15.5, centerToFrontRatio=0.41),
    flags=MazdaSafetyFlags.GEN2,
  )
  MAZDA_3_2023 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda 3 2024-26")],
    MazdaCarSpecs(mass=3300 * CV.LB_TO_KG, wheelbase=2.725, steerRatio=15.59, centerToFrontRatio=0.38),
    flags=MazdaSafetyFlags.GEN3,
  )
  MAZDA_CX_30_2023 = MazdaPlatformConfig(
    [MazdaCarDocs("Mazda CX-30 23-26")],
    MazdaCarSpecs(mass=3375 * CV.LB_TO_KG, wheelbase=2.814, steerRatio=15.5, centerToFrontRatio=0.38),
    flags=MazdaSafetyFlags.GEN3,
  )


class LKAS_LIMITS:
  STEER_THRESHOLD = 15
  DISABLE_SPEED = 45    # kph
  ENABLE_SPEED = 52     # kph
  TI_STEER_THRESHOLD = 6
  TI_DISABLE_SPEED = 0    # kph
  TI_ENABLE_SPEED = 0     # kph


class Buttons:
  NONE = 0
  SET_PLUS = 1
  SET_MINUS = 2
  RESUME = 3
  CANCEL = 4
  TURN_ON = 5


FW_QUERY_CONFIG = FwQueryConfig(
  fw_version_regex=br"[A-Z0-9-]{11,16}\x00{8,13}",
  requests=[
    # TODO: check data to ensure ABS does not skip ISO-TP frames on bus 0
    Request(
      [StdQueries.MANUFACTURER_SOFTWARE_VERSION_REQUEST],
      [StdQueries.MANUFACTURER_SOFTWARE_VERSION_RESPONSE],
      bus=0,
    ),
    Request(
      [StdQueries.TESTER_PRESENT_REQUEST, StdQueries.MANUFACTURER_SOFTWARE_VERSION_REQUEST],
      [StdQueries.TESTER_PRESENT_RESPONSE, StdQueries.MANUFACTURER_SOFTWARE_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.engine],
    ),
    Request(
      [StdQueries.TESTER_PRESENT_REQUEST, StdQueries.MANUFACTURER_SOFTWARE_VERSION_REQUEST],
      [StdQueries.TESTER_PRESENT_RESPONSE, StdQueries.MANUFACTURER_SOFTWARE_VERSION_RESPONSE],
      bus=0,
      whitelist_ecus=[Ecu.eps, Ecu.abs, Ecu.fwdRadar, Ecu.fwdCamera],
    )
  ],
)

DBC = CAR.create_dbc_map()
GEN1 = {car for car in CAR if car.config.flags & MazdaSafetyFlags.GEN1}
GEN2 = {car for car in CAR if car.config.flags & MazdaSafetyFlags.GEN2}
GEN3 = {car for car in CAR if car.config.flags & MazdaSafetyFlags.GEN3}
