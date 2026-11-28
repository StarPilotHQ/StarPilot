from enum import IntFlag
from dataclasses import dataclass, field

from opendbc.car import Bus, CarSpecs, DbcDict, PlatformConfig, Platforms, uds
from opendbc.car.structs import CarParams
from opendbc.car.docs_definitions import CarHarness, CarDocs, CarParts
from opendbc.car.fw_query_definitions import FwQueryConfig, Request, p16

Ecu = CarParams.Ecu


class ChryslerSafetyFlags(IntFlag):
  RAM_DT = 1
  RAM_HD = 2
  JEEP_BRAKE_HOLD = 4
  AOL_PACIFICA_HYBRID_SET_GUARD = 8
  # JEEP_GRAND_CHEROKEE_2019 only (alpha long). Distinct from JEEP_BRAKE_HOLD.
  LONG_CONTROL = 16


class ChryslerFlags(IntFlag):
  # Detected flags
  HIGHER_MIN_STEERING_SPEED = 1
  HAS_BSM = 2  # blind spot monitoring
  STEERING_SPEED_BYPASS = 4
  JEEP_BRAKE_HOLD_AVAILABLE = 8
  # JEEP_GRAND_CHEROKEE_2019 only: fingerprint admits alpha longitudinal (DAS_3 OP long).
  ALPHA_LONG_AVAILABLE = 16


@dataclass
class ChryslerCarDocs(CarDocs):
  package: str = "Adaptive Cruise Control (ACC)"
  car_parts: CarParts = field(default_factory=CarParts.common([CarHarness.fca]))


@dataclass
class ChryslerPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: {
    Bus.pt: 'chrysler_pacifica_2017_hybrid_generated',
    Bus.radar: 'chrysler_pacifica_2017_hybrid_private_fusion',
  })


@dataclass(frozen=True)
class ChryslerCarSpecs(CarSpecs):
  minSteerSpeed: float = 3.8  # m/s


class CAR(Platforms):
  # Chrysler
  CHRYSLER_PACIFICA_2018_HYBRID = ChryslerPlatformConfig(
    [ChryslerCarDocs("Chrysler Pacifica Hybrid 2017-18")],
    ChryslerCarSpecs(mass=2242., wheelbase=3.089, steerRatio=16.2),
  )
  CHRYSLER_PACIFICA_2019_HYBRID = ChryslerPlatformConfig(
    [ChryslerCarDocs("Chrysler Pacifica Hybrid 2019-25")],
    CHRYSLER_PACIFICA_2018_HYBRID.specs,
  )
  CHRYSLER_PACIFICA_2018 = ChryslerPlatformConfig(
    [ChryslerCarDocs("Chrysler Pacifica 2017-18")],
    CHRYSLER_PACIFICA_2018_HYBRID.specs,
  )
  CHRYSLER_PACIFICA_2020 = ChryslerPlatformConfig(
    [
      ChryslerCarDocs("Chrysler Pacifica 2019-20"),
      ChryslerCarDocs("Chrysler Pacifica 2021-23", package="All"),
    ],
    CHRYSLER_PACIFICA_2018_HYBRID.specs,
  )

  # Dodge
  DODGE_DURANGO = ChryslerPlatformConfig(
    [ChryslerCarDocs("Dodge Durango 2020-21")],
    CHRYSLER_PACIFICA_2018_HYBRID.specs,
  )

  # Jeep
  JEEP_CHEROKEE_5TH_GEN = ChryslerPlatformConfig(
    [ChryslerCarDocs("Jeep Cherokee 2019-23")],
    ChryslerCarSpecs(mass=1747., wheelbase=2.70, steerRatio=17.0, minSteerSpeed=18.5),
    {Bus.pt: 'chrysler_cusw'},
  )
  JEEP_GRAND_CHEROKEE = ChryslerPlatformConfig(  # includes 2017 Trailhawk
    [ChryslerCarDocs("Jeep Grand Cherokee 2016-18", video="https://www.youtube.com/watch?v=eLR9o2JkuRk")],
    ChryslerCarSpecs(mass=1778., wheelbase=2.71, steerRatio=16.7),
  )

  JEEP_GRAND_CHEROKEE_2019 = ChryslerPlatformConfig(  # includes 2020 Trailhawk
    [ChryslerCarDocs("Jeep Grand Cherokee 2019-21")],
    JEEP_GRAND_CHEROKEE.specs,
  )

  # Ram
  RAM_1500_5TH_GEN = ChryslerPlatformConfig(
    [ChryslerCarDocs("Ram 1500 2019-24", car_parts=CarParts.common([CarHarness.ram]))],
    ChryslerCarSpecs(mass=2493., wheelbase=3.88, steerRatio=16.3, minSteerSpeed=14.5),
    {Bus.pt: 'chrysler_ram_dt_generated'},
  )
  RAM_HD_5TH_GEN = ChryslerPlatformConfig(
    [
      ChryslerCarDocs("Ram 2500 2020-24", car_parts=CarParts.common([CarHarness.ram])),
      ChryslerCarDocs("Ram 3500 2019-22", car_parts=CarParts.common([CarHarness.ram])),
    ],
    ChryslerCarSpecs(mass=3405., wheelbase=3.785, steerRatio=15.61, minSteerSpeed=16.),
    {Bus.pt: 'chrysler_ram_hd_generated'},
  )


# JEEP_GRAND_CHEROKEE_2019-only long envelopes. Not for other Chrysler.
# Measured from ~76k stock-ACC-active DAS_3 frames (routes 0000000b / 0000000e):
#   braking (ACC_DECEL_REQ=1): ACC_DECEL -4.00..+0.45 m/s^2, -2.0 while held at standstill
#   not braking (ACC_DECEL_REQ=0): ACC_DECEL is always raw 4094 (+4.0), an idle value, not an accel
#   ENGINE_TORQUE_REQUEST -49..+381 Nm; ENGINE_TORQUE_REQUEST_MAX=1 whenever not braking
WK2_2019_ACCEL_MIN = -3.5  # m/s^2, openpilot standard; stock ACC/FCW reached -4.0
WK2_2019_ACCEL_MAX = 2.0   # m/s^2, openpilot standard; stock ACC p99 aEgo 1.8
WK2_2019_TORQUE_MIN = -52.0  # Nm, coast/brake-phase request
WK2_2019_TORQUE_MAX = 380.0  # Nm, stock peak 381.5
WK2_2019_ACC_DECEL_IDLE = 4.0  # ACC_DECEL value stock sends when it is not requesting braking
WK2_2019_STANDSTILL_DECEL = -2.0  # m/s^2, stock and Jeep brake-hold standstill request
WK2_2019_LONG_STEP = 1  # 100 Hz, the DAS_3 replacement cadence proven by Jeep brake hold
# While moving, stock radar ACC braking firmer than this is a floor on the OP request. Gentle stock
# trims (e.g. stock set speed lagging OP's) are ignored so OP keeps its own speed target.
WK2_2019_STOCK_BRAKE_FLOOR = -1.0  # m/s^2
# Braking/torque mode hysteresis; stock keeps ACC_DECEL_REQ up to +0.45 before switching to torque.
WK2_2019_BRAKE_ENTER = -0.1  # m/s^2
WK2_2019_BRAKE_EXIT = 0.1  # m/s^2
# Torque feedforward fitted to stock ENGINE_TORQUE_REQUEST vs. aEgo 0.4 s later, per speed band:
# torque = base(v) + gain(v) * accel. The longitudinal PID corrects the residual.
WK2_2019_TORQUE_SPEED_BP = [0., 11., 17.]  # m/s
WK2_2019_TORQUE_BASE_V = [55., 71., 124.]  # Nm at zero accel (road load)
WK2_2019_TORQUE_GAIN_V = [100., 130., 165.]  # Nm per m/s^2


class CarControllerParams:
  def __init__(self, CP):
    self.STEER_STEP = 2  # 50 Hz
    self.STEER_ERROR_MAX = 80
    # Longitudinal limits exist only for WK2 2019 alpha-long; other fingerprints leave these unset.
    if CP.carFingerprint == CAR.JEEP_GRAND_CHEROKEE_2019:
      self.ACCEL_MIN = WK2_2019_ACCEL_MIN
      self.ACCEL_MAX = WK2_2019_ACCEL_MAX
      self.TORQUE_MIN = WK2_2019_TORQUE_MIN
      self.TORQUE_MAX = WK2_2019_TORQUE_MAX
      self.LONG_STEP = WK2_2019_LONG_STEP
    if CP.carFingerprint in RAM_HD:
      self.STEER_DELTA_UP = 14
      self.STEER_DELTA_DOWN = 14
      self.STEER_MAX = 361  # higher than this faults the EPS
    elif CP.carFingerprint in RAM_DT:
      self.STEER_DELTA_UP = 6
      self.STEER_DELTA_DOWN = 6
      self.STEER_MAX = 350
    elif CP.carFingerprint in CUSW_CARS:
      self.STEER_STEP = 1  # 100 Hz
      self.STEER_DELTA_UP = 4
      self.STEER_DELTA_DOWN = 4
      self.STEER_MAX = 250  # TODO: Some CUSW will go to 261, some not quite, exact boundaries not yet determined
    else:
      self.STEER_DELTA_UP = 3
      self.STEER_DELTA_DOWN = 3
      self.STEER_MAX = 261  # higher than this faults the EPS


STEER_THRESHOLD = 120

RAM_DT = {CAR.RAM_1500_5TH_GEN, }
RAM_HD = {CAR.RAM_HD_5TH_GEN, }
RAM_CARS = RAM_DT | RAM_HD
CUSW_CARS = {CAR.JEEP_CHEROKEE_5TH_GEN, }
# Port changes in this PR target this fingerprint only (admin may broaden later).
WK2_2019 = {CAR.JEEP_GRAND_CHEROKEE_2019, }


CHRYSLER_VERSION_REQUEST = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER]) + \
  p16(0xf132)
CHRYSLER_VERSION_RESPONSE = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER + 0x40]) + \
  p16(0xf132)

CHRYSLER_SOFTWARE_VERSION_REQUEST = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER]) + \
  p16(uds.DATA_IDENTIFIER_TYPE.SYSTEM_SUPPLIER_ECU_SOFTWARE_NUMBER)
CHRYSLER_SOFTWARE_VERSION_RESPONSE = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER + 0x40]) + \
  p16(uds.DATA_IDENTIFIER_TYPE.SYSTEM_SUPPLIER_ECU_SOFTWARE_NUMBER)

CHRYSLER_RX_OFFSET = -0x280

FW_QUERY_CONFIG = FwQueryConfig(
  fw_version_regex=br"[A-Z0-9_]{10} ?",
  requests=[
    Request(
      [CHRYSLER_VERSION_REQUEST],
      [CHRYSLER_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.abs, Ecu.eps, Ecu.srs, Ecu.fwdRadar, Ecu.combinationMeter],
      rx_offset=CHRYSLER_RX_OFFSET,
      bus=0,
    ),
    Request(
      [CHRYSLER_VERSION_REQUEST],
      [CHRYSLER_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.abs, Ecu.hybrid, Ecu.engine, Ecu.transmission],
      bus=0,
    ),
    Request(
      [CHRYSLER_SOFTWARE_VERSION_REQUEST],
      [CHRYSLER_SOFTWARE_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.engine, Ecu.transmission],
      bus=0,
    ),
  ],
  extra_ecus=[
    (Ecu.abs, 0x7e4, None),  # alt address for abs on hybrids, NOTE: not on all hybrid platforms
  ],
)

DBC = CAR.create_dbc_map()
