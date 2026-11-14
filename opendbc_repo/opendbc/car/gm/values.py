from dataclasses import dataclass, field
from enum import Enum, IntFlag
import numpy as np

from opendbc.car import Bus, PlatformConfig, DbcDict, Platforms, CarSpecs
from opendbc.car.structs import CarParams
from opendbc.car.docs_definitions import CarDocs, CarFootnote, CarHarness, CarParts, Column
from opendbc.car.fw_query_definitions import FwQueryConfig, Request, StdQueries

Ecu = CarParams.Ecu


class CarControllerParams:
  STEER_MAX = 300  # GM limit is 3Nm. Used by carcontroller to generate LKA output
  STEER_STEP = 3  # Active control frames per command (~33hz)
  INACTIVE_STEER_STEP = 10  # Inactive control frames per command (10hz)
  STEER_DELTA_UP = 10  # Delta rates require review due to observed EPS weakness
  STEER_DELTA_DOWN = 15
  STEER_DRIVER_ALLOWANCE = 65
  STEER_DRIVER_MULTIPLIER = 4
  STEER_DRIVER_FACTOR = 100
  NEAR_STOP_BRAKE_PHASE = 0.25  # m/s
  SNG_INTERCEPTOR_GAS = 18. / 255.
  SNG_TIME = 30  # frames until the above is reached

  # Heartbeat for dash "Service Adaptive Cruise" and "Service Front Camera"
  ADAS_KEEPALIVE_STEP = 100
  CAMERA_KEEPALIVE_STEP = 100

  # Allow small margin below -3.5 m/s^2 from ISO 15622:2018 since we
  # perform the closed loop control, and might need some
  # to apply some more braking if we're on a downhill slope.
  # Our controller should still keep the 2 second average above
  # -3.5 m/s^2 as per planner limits
  ACCEL_MAX = 2.  # m/s^2
  ACCEL_MAX_PLUS = 4.  # m/s^2
  ACCEL_MIN = -4.  # m/s^2

  def __init__(self, CP):
    self.STEER_MAX = CarControllerParams.STEER_MAX
    self.STEER_STEP = CarControllerParams.STEER_STEP
    self.INACTIVE_STEER_STEP = CarControllerParams.INACTIVE_STEER_STEP
    self.STEER_DELTA_UP = CarControllerParams.STEER_DELTA_UP
    self.STEER_DELTA_DOWN = CarControllerParams.STEER_DELTA_DOWN
    self.STEER_DRIVER_ALLOWANCE = CarControllerParams.STEER_DRIVER_ALLOWANCE
    self.STEER_DRIVER_MULTIPLIER = CarControllerParams.STEER_DRIVER_MULTIPLIER
    self.STEER_DRIVER_FACTOR = CarControllerParams.STEER_DRIVER_FACTOR

    if CP.carFingerprint == CAR.CHEVROLET_BOLT_CC_2017:
      self.STEER_MAX = 450
      self.STEER_DELTA_UP = 15
      self.STEER_DELTA_DOWN = 34
      self.STEER_DRIVER_ALLOWANCE = 78
      self.STEER_DRIVER_MULTIPLIER = 6
      self.STEER_DRIVER_FACTOR = 100

    # Gas/brake lookups
    self.ZERO_GAS = 6150  # Coasting
    self.MAX_BRAKE = 400  # ~ -4.0 m/s^2 with regen

    kaofui_cars = SDGM_CAR | ASCM_INT | {
      CAR.CHEVROLET_VOLT,
      CAR.CHEVROLET_VOLT_2019,
      CAR.CHEVROLET_VOLT_ASCM,
      CAR.CHEVROLET_VOLT_CAMERA,
      CAR.CHEVROLET_VOLT_CC,
      CAR.CHEVROLET_MALIBU_CC,
      CAR.CHEVROLET_MALIBU_HYBRID_CC,
    }
    volt_like = {
      CAR.CHEVROLET_VOLT,
      CAR.CHEVROLET_VOLT_ASCM,
      CAR.CHEVROLET_VOLT_CAMERA,
      CAR.CHEVROLET_VOLT_CC,
    }

    if CP.carFingerprint in kaofui_cars:
      if (CP.carFingerprint in (CAMERA_ACC_CAR | SDGM_CAR) and
          CP.carFingerprint not in CC_ONLY_CAR and
          CP.carFingerprint != CAR.CHEVROLET_BOLT_ACC_2022_2023):
        self.MAX_GAS = 8848
        self.MAX_GAS_PLUS = 8848
        self.MAX_ACC_REGEN = 5610
        self.INACTIVE_REGEN = 5650
        # Camera ACC vehicles have no regen while enabled.
        # Camera transitions to MAX_ACC_REGEN from ZERO_GAS and uses friction brakes instantly
        max_regen_acceleration = 0.
      else:
        self.MAX_GAS = 8191  # Safety limit, not ACC max. Stock ACC >8192 from standstill.
        self.MAX_GAS_PLUS = 8191
        self.MAX_ACC_REGEN = 5500  # Max ACC regen is slightly less than max paddle regen
        self.INACTIVE_REGEN = 5500
        # ICE has much less engine braking force compared to regen in EVs,
        # lower threshold removes some braking deadzone
        max_regen_acceleration = -1. if CP.carFingerprint in EV_CAR else -0.1

      self.BRAKE_SWITCH_MAX = self.MAX_ACC_REGEN if CP.carFingerprint in EV_CAR else self.ZERO_GAS
      if CP.carFingerprint in volt_like:
        self.BRAKE_LOOKUP_BP = [self.ACCEL_MIN, 0.]
      else:
        self.BRAKE_LOOKUP_BP = [self.ACCEL_MIN, max_regen_acceleration]

    else:
      if CP.carFingerprint in CAMERA_ACC_CAR and CP.carFingerprint not in CC_ONLY_CAR:
        self.MAX_GAS = 8848
        self.MAX_GAS_PLUS = 8848
        self.MAX_ACC_REGEN = 5610
        self.INACTIVE_REGEN = 5650
        # Camera ACC vehicles have no regen while enabled.
        # Camera transitions to MAX_ACC_REGEN from ZERO_GAS and uses friction brakes instantly
        max_regen_acceleration = 0.
        self.BRAKE_SWITCH_MAX = self.MAX_ACC_REGEN if CP.carFingerprint in EV_CAR else self.ZERO_GAS

      elif CP.carFingerprint in SDGM_CAR:
        self.MAX_GAS = 8191
        self.MAX_GAS_PLUS = 8191
        self.MAX_ACC_REGEN = 5500
        self.INACTIVE_REGEN = 5500
        max_regen_acceleration = 0.
        self.BRAKE_SWITCH_MAX = self.ZERO_GAS

      else:
        self.MAX_GAS = 7168  # Safety limit, not ACC max. Stock ACC >8192 from standstill.
        self.MAX_GAS_PLUS = 7168  # Matches StarPilot high range for non-kaofui paths.
        self.MAX_ACC_REGEN = 5500  # Max ACC regen is slightly less than max paddle regen
        self.INACTIVE_REGEN = 5500
        # ICE has much less engine braking force compared to regen in EVs,
        # lower threshold removes some braking deadzone
        max_regen_acceleration = -3. if CP.carFingerprint in EV_CAR else -0.1
        self.BRAKE_SWITCH_MAX = self.MAX_ACC_REGEN if CP.carFingerprint in EV_CAR else self.ZERO_GAS

      self.BRAKE_LOOKUP_BP = [self.ACCEL_MIN, 0.]

    self.max_regen_acceleration = max_regen_acceleration
    self.GAS_LOOKUP_BP = [self.max_regen_acceleration, 0., self.ACCEL_MAX]
    self.GAS_LOOKUP_BP_PLUS = [self.max_regen_acceleration, 0., self.ACCEL_MAX_PLUS]
    self.GAS_LOOKUP_V = [self.MAX_ACC_REGEN, self.ZERO_GAS, self.MAX_GAS]
    self.GAS_LOOKUP_V_PLUS = [self.MAX_ACC_REGEN, self.ZERO_GAS, self.MAX_GAS_PLUS]

    self.BRAKE_LOOKUP_V = [self.MAX_BRAKE, 0.]

    self.BRAKE_SWITCH_LOOKUP_BP = [0.5, 10]
    self.BRAKE_SWITCH_LOOKUP_V = [self.ZERO_GAS, self.BRAKE_SWITCH_MAX]

  # Determined by letting Volt/Bolt EV regen to a stop from speed and at creep.
  EV_GAS_BRAKE_THRESHOLD_BP = [1.29, 1.52, 1.55, 1.6, 1.7, 1.8, 2.0, 2.2, 2.5, 5.52, 9.6, 20.5, 23.5, 35.0]  # [m/s]
  EV_GAS_BRAKE_THRESHOLD_V = [0.0, -0.14, -0.16, -0.18, -0.215, -0.255, -0.32, -0.41, -0.5, -0.72, -0.895, -1.125, -1.145, -1.16]  # [m/s^2]

  def update_ev_gas_brake_threshold(self, v_ego):
    gas_brake_threshold = float(np.interp(v_ego, self.EV_GAS_BRAKE_THRESHOLD_BP, self.EV_GAS_BRAKE_THRESHOLD_V))
    self.GAS_LOOKUP_BP_PLUS = [self.max_regen_acceleration, 0., self.ACCEL_MAX_PLUS]
    self.EV_GAS_LOOKUP_BP = [gas_brake_threshold, max(0., gas_brake_threshold), self.ACCEL_MAX]
    self.EV_GAS_LOOKUP_BP_PLUS = [gas_brake_threshold, max(0., gas_brake_threshold), self.ACCEL_MAX_PLUS]
    self.EV_BRAKE_LOOKUP_BP = [self.ACCEL_MIN, gas_brake_threshold]


class GMSafetyFlags(IntFlag):
  HW_CAM = 1
  HW_CAM_LONG = 2
  # Reuses the retired EV safety bit. EV-specific safety handling is no longer needed.
  FLAG_GM_NO_CAMERA = 4

  FLAG_GM_CC_LONG = 8
  FLAG_GM_GAS_INTERCEPTOR = 16
  FLAG_GM_NO_ACC = 32
  FLAG_GM_PEDAL_LONG = 64
  HW_ASCM_LONG = 128

  # Additional GM hardware and flag values
  HW_ASCM_INT = 256
  FLAG_GM_FORCE_BRAKE_C9 = 512
  HW_SDGM = 1024
  FLAG_GM_BOLT_2017 = 2048
  FLAG_GM_BOLT_2022_PEDAL = 4096
  FLAG_GM_REMOTE_START_BOOTS_COMMA = 8192
  FLAG_GM_PANDA_3D1_SCHED = 16384
  FLAG_GM_PANDA_PADDLE_SCHED = 32768
  FLAG_GM_VOLT_CC_GATEWAY = 16384


class Footnote(Enum):
  SETUP = CarFootnote(
    "See more setup details for <a href=\"https://github.com/commaai/openpilot/wiki/gm\" target=\"_blank\">GM</a>.",
    Column.MAKE, setup_note=True)


@dataclass
class GMCarDocs(CarDocs):
  package: str = "Adaptive Cruise Control (ACC)"

  def init_make(self, CP: CarParams):
    if CP.networkLocation == CarParams.NetworkLocation.fwdCamera:
      if CP.carFingerprint in SDGM_CAR:
        self.car_parts = CarParts.common([CarHarness.gmsdgm])
      else:
        self.car_parts = CarParts.common([CarHarness.gm])
    else:
      self.footnotes.insert(0, Footnote.SETUP)
      self.car_parts = CarParts.common([CarHarness.obd_ii])


@dataclass(frozen=True, kw_only=True)
class GMCarSpecs(CarSpecs):
  tireStiffnessFactor: float = 0.444  # not optimized yet


@dataclass
class GMPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: {
    Bus.pt: 'gm_global_a_powertrain_generated',
    Bus.radar: 'gm_global_a_object',
    Bus.chassis: 'gm_global_a_chassis',
  })


@dataclass
class GMASCMPlatformConfig(GMPlatformConfig):
  pass


@dataclass
class GMSDGMPlatformConfig(GMPlatformConfig):
  pass


class CAR(Platforms):
  HOLDEN_ASTRA = GMASCMPlatformConfig(
    [GMCarDocs("Holden Astra 2017")],
    GMCarSpecs(mass=1363, wheelbase=2.662, steerRatio=15.7, centerToFrontRatio=0.4),
  )
  CHEVROLET_VOLT = GMASCMPlatformConfig(
    [GMCarDocs("Chevrolet Volt 2017-18", min_enable_speed=0, video="https://youtu.be/QeMCN_4TFfQ")],
    GMCarSpecs(mass=1607, wheelbase=2.69, steerRatio=15.7, centerToFrontRatio=0.45, tireStiffnessFactor=1.0, minEnableSpeed=-1), #tire stiffness factor hasn't been updated since 2018 and every other gm is on 1.0
    dbc_dict={
      Bus.pt: "gm_global_a_powertrain_volt",
      Bus.radar: "gm_global_a_object",
      Bus.chassis: "gm_global_a_chassis",
    },
  )
  CHEVROLET_VOLT_ASCM = GMPlatformConfig(
    [GMCarDocs("Chevrolet Volt ASCM Harness 2017-18", min_enable_speed=0, video="https://youtu.be/QeMCN_4TFfQ")],
    CHEVROLET_VOLT.specs,
    dbc_dict=CHEVROLET_VOLT.dbc_dict,
  )
  CHEVROLET_VOLT_CAMERA = GMPlatformConfig(
    [GMCarDocs("Chevrolet Volt Camera Harness 2017-18", "Flashed camera-forward integration with ACC")],
    CHEVROLET_VOLT.specs,
    dbc_dict=CHEVROLET_VOLT.dbc_dict,
  )
  CHEVROLET_VOLT_CC = GMPlatformConfig(
    [GMCarDocs("Chevrolet Volt No-ACC 2016-18 (OBD Harness)", "Redneck ACC", min_enable_speed=0)],
    CHEVROLET_VOLT.specs,
    dbc_dict=CHEVROLET_VOLT.dbc_dict,
  )
  CADILLAC_ATS = GMASCMPlatformConfig(
    [GMCarDocs("Cadillac ATS Premium Performance 2018")],
    GMCarSpecs(mass=1601, wheelbase=2.78, steerRatio=15.3),
  )
  CHEVROLET_MALIBU = GMASCMPlatformConfig(
    [GMCarDocs("Chevrolet Malibu Premier 2017")],
    GMCarSpecs(mass=1496, wheelbase=2.83, steerRatio=15.8, centerToFrontRatio=0.4),
  )
  CHEVROLET_MALIBU_ASCM = GMPlatformConfig(
    [GMCarDocs("Chevrolet Malibu ASCM Harness 2017-19")],
    CHEVROLET_MALIBU.specs,
  )
  GMC_ACADIA = GMASCMPlatformConfig(
    [GMCarDocs("GMC Acadia 2018", video="https://www.youtube.com/watch?v=0ZN6DdsBUZo")],
    GMCarSpecs(mass=1975, wheelbase=2.86, steerRatio=14.4, centerToFrontRatio=0.4),
  )
  GMC_ACADIA_ASCM = GMPlatformConfig(
    [GMCarDocs("GMC Acadia ASCM Harness 2018", video="https://www.youtube.com/watch?v=0ZN6DdsBUZo")],
    GMC_ACADIA.specs,
  )
  BUICK_ENVISION_SDGM = GMSDGMPlatformConfig(
    [GMCarDocs("Buick Envision")],
    GMCarSpecs(mass=1800, wheelbase=2.75, steerRatio=15.6, centerToFrontRatio=0.41),
  dbc_dict={
        Bus.pt: 'gm_global_a_powertrain_envision',
        Bus.radar: 'gm_global_a_object',
        Bus.chassis: 'gm_global_a_chassis',
    },
  )
  BUICK_LACROSSE = GMASCMPlatformConfig(
    [GMCarDocs("Buick LaCrosse 2017-19", "Driver Confidence Package 2")],
    GMCarSpecs(mass=1712, wheelbase=2.91, steerRatio=15.8, centerToFrontRatio=0.4),
  )
  BUICK_LACROSSE_ASCM = GMPlatformConfig(
    [GMCarDocs("Buick LaCrosse ASCM Harness 2017-19")],
    BUICK_LACROSSE.specs,
  )
  BUICK_LACROSSE_ASCM_19US = GMPlatformConfig(
    [GMCarDocs("Buick LaCrosse US ASCM Harness 2019")],
    BUICK_LACROSSE.specs,
  )
  BUICK_REGAL = GMASCMPlatformConfig(
    [GMCarDocs("Buick Regal Essence 2018")],
    GMCarSpecs(mass=1714, wheelbase=2.83, steerRatio=14.4, centerToFrontRatio=0.4),
  )
  CADILLAC_ESCALADE = GMASCMPlatformConfig(
    [GMCarDocs("Cadillac Escalade 2017", "Driver Assist Package")],
    GMCarSpecs(mass=2564, wheelbase=2.95, steerRatio=17.3),
  )
  CADILLAC_ESCALADE_ASCM = GMPlatformConfig(
    [GMCarDocs("Cadillac Escalade ASCM Harness 2018", "Driver Assist Package")],
    CADILLAC_ESCALADE.specs,
  )
  CADILLAC_ESCALADE_ESV = GMASCMPlatformConfig(
    [GMCarDocs("Cadillac Escalade ESV 2016", "Adaptive Cruise Control (ACC) & LKAS")],
    GMCarSpecs(mass=2739, wheelbase=3.302, steerRatio=17.3, tireStiffnessFactor=1.0),
  )
  CADILLAC_ESCALADE_ESV_2019 = GMASCMPlatformConfig(
    [GMCarDocs("Cadillac Escalade ESV 2019", "Adaptive Cruise Control (ACC) & LKAS")],
    CADILLAC_ESCALADE_ESV.specs,
  )
  CADILLAC_ESCALADE_ESV_2019_ASCM = GMPlatformConfig(
    [GMCarDocs("Cadillac Escalade ESV Platinum ASCM Harness 2019", "Adaptive Cruise Control (ACC) & LKAS")],
    CADILLAC_ESCALADE_ESV_2019.specs,
  )
  CHEVROLET_BOLT_ACC_2022_2023 = GMPlatformConfig(
    [
      GMCarDocs("Chevrolet Bolt EV & EUV ACC 2022-23", "Premier or Premier Redline Trim without Super Cruise Package", video="https://youtu.be/xvwzGMUA210"),
    ],
    GMCarSpecs(mass=1669, wheelbase=2.63779, steerRatio=16.8, centerToFrontRatio=0.4, tireStiffnessFactor=1.0),
  )
  CHEVROLET_BOLT_ACC_2022_2023_PEDAL = GMPlatformConfig(
    [GMCarDocs("Chevrolet Bolt EV & EUV ACC w Pedal 2022-23")],
    CHEVROLET_BOLT_ACC_2022_2023.specs,
  )
  CHEVROLET_BOLT_CC_2022_2023 = GMPlatformConfig(
    [GMCarDocs("Chevrolet Bolt EV & EUV No-ACC 2022-23")],
    CHEVROLET_BOLT_ACC_2022_2023.specs,
  )
  CHEVROLET_BOLT_CC_2018_2021 = GMPlatformConfig(
    [GMCarDocs("Chevrolet Bolt EV No-ACC 2018-21")],
    CHEVROLET_BOLT_ACC_2022_2023.specs,
  )
  CHEVROLET_BOLT_CC_2017 = GMPlatformConfig(
    [GMCarDocs("Chevrolet Bolt EV No-ACC 2017")],
    CHEVROLET_BOLT_ACC_2022_2023.specs,
  )

  CHEVROLET_SILVERADO = GMPlatformConfig(
    [
      GMCarDocs("Chevrolet Silverado 1500 2020-21", "Safety Package II"),
      GMCarDocs("GMC Sierra 1500 2020-21", "Driver Alert Package II", video="https://youtu.be/5HbNoBLzRwE"),
    ],
    GMCarSpecs(mass=2994, wheelbase=3.75, steerRatio=16.3, tireStiffnessFactor=1.0),
  )
  CHEVROLET_SILVERADO_CC = GMPlatformConfig(
    [
      GMCarDocs("Chevrolet Silverado 1500 No-ACC 2020-21"),
      GMCarDocs("GMC Sierra 1500 No-ACC 2020-21"),
    ],
    CHEVROLET_SILVERADO.specs,
  )
  CHEVROLET_EQUINOX = GMPlatformConfig(
    [GMCarDocs("Chevrolet Equinox 2019-22")],
    GMCarSpecs(mass=1588, wheelbase=2.72, steerRatio=14.4, centerToFrontRatio=0.4),
  )
  CHEVROLET_TRAILBLAZER = GMPlatformConfig(
    [GMCarDocs("Chevrolet Trailblazer 2021-22")],
    GMCarSpecs(mass=1345, wheelbase=2.64, steerRatio=16.8, centerToFrontRatio=0.4, tireStiffnessFactor=1.0),
  )
  CHEVROLET_SUBURBAN = GMPlatformConfig(
    [GMCarDocs("Chevrolet Suburban Premier 2016-20")],
    CarSpecs(mass=2731, wheelbase=3.302, steerRatio=17.3, centerToFrontRatio=0.49),
  )
  CHEVROLET_SUBURBAN_ASCM = GMPlatformConfig(
    [GMCarDocs("Chevrolet Suburban Premier ASCM Harness 2016-20", "Adaptive Cruise Control (ACC) & LKAS")],
    CHEVROLET_SUBURBAN.specs,
  )
  CHEVROLET_SUBURBAN_CAMERA = GMPlatformConfig(
    [GMCarDocs("Chevrolet Suburban Premier Camera Harness 2016-20", "Adaptive Cruise Control (ACC) & LKAS")],
    CHEVROLET_SUBURBAN.specs,
  )
  GMC_YUKON_CC = GMPlatformConfig(
    [GMCarDocs("GMC Yukon No-ACC 2019-20")],
    CarSpecs(mass=2541, wheelbase=2.95, steerRatio=16.3, centerToFrontRatio=0.4),
  )
  GMC_YUKON = GMPlatformConfig(
    [GMCarDocs("GMC Yukon 2019-20", "Adaptive Cruise Control (ACC) & LKAS")],
    GMCarSpecs(mass=2490, wheelbase=2.94, steerRatio=17.3, centerToFrontRatio=0.5, tireStiffnessFactor=1.0),
  )
  CADILLAC_XT4 = GMSDGMPlatformConfig(
    [GMCarDocs("Cadillac XT4 2023", "Driver Assist Package")],
    GMCarSpecs(mass=1660, wheelbase=2.78, steerRatio=14.4, centerToFrontRatio=0.4),
  )
  CADILLAC_XT4_CC = GMPlatformConfig(
    [GMCarDocs("Cadillac XT4 No-ACC 2023")],
    CADILLAC_XT4.specs,
  )
  CADILLAC_XT5 = GMSDGMPlatformConfig(
    [GMCarDocs("Cadillac XT5 2022", "Driver Assist Package")],
    CarSpecs(mass=1810, wheelbase=2.86, steerRatio=16.34, centerToFrontRatio=0.5),
  )
  CADILLAC_XT6 = GMPlatformConfig(
    [GMCarDocs("Cadillac XT6 2020", "Driver Assist Package")],
    GMCarSpecs(mass=2050, wheelbase=2.86, steerRatio=16.5, centerToFrontRatio=0.4),
  )
  CHEVROLET_BLAZER = GMPlatformConfig(
    [GMCarDocs("Chevrolet Blazer 2019-25", "Driver Assist Package")],
    CarSpecs(mass=1850, wheelbase=3.10, steerRatio=17.9, centerToFrontRatio=0.4),
  )
  CHEVROLET_VOLT_2019 = GMSDGMPlatformConfig(
    [GMCarDocs("Chevrolet Volt 2019", "Adaptive Cruise Control (ACC) & LKAS")],
    GMCarSpecs(mass=1607, wheelbase=2.69, steerRatio=15.7, centerToFrontRatio=0.45),
  )
  CHEVROLET_TRAVERSE = GMSDGMPlatformConfig(
    [GMCarDocs("Chevrolet Traverse 2022-23", "RS, Premier, or High Country Trim")],
    GMCarSpecs(mass=1955, wheelbase=3.07, steerRatio=17.9, centerToFrontRatio=0.4),
  )
  CHEVROLET_MALIBU_SDGM = GMPlatformConfig(
    [GMCarDocs("Chevrolet Malibu 2019", "SDGM Harness (Optional SASCM)")],
    CHEVROLET_MALIBU.specs,
  )
  BUICK_BABYENCLAVE = GMPlatformConfig(
    [GMCarDocs("Buick Baby Enclave 2020-23", "Driver Assist Package")],
    CarSpecs(mass=2050, wheelbase=2.86, steerRatio=16.0, centerToFrontRatio=0.5),
  )
  CADILLAC_CT6_CC = GMPlatformConfig(
    [GMCarDocs("Cadillac CT6 No-ACC 2016-20")],
    CarSpecs(mass=2358, wheelbase=3.11, steerRatio=17.7, centerToFrontRatio=0.4),
  )
  CADILLAC_XT5_CC = GMPlatformConfig(
    [GMCarDocs("Cadillac XT5 No-ACC 2022")],
    CADILLAC_XT5.specs,
  )
  CHEVROLET_EQUINOX_CC = GMPlatformConfig(
    [GMCarDocs("Chevrolet Equinox No-ACC 2019-22")],
    CHEVROLET_EQUINOX.specs,
  )
  CHEVROLET_MALIBU_CC = GMPlatformConfig(
    [GMCarDocs("Chevrolet Malibu No-ACC 2023")],
    CarSpecs(mass=1450, wheelbase=2.8, steerRatio=18.25, centerToFrontRatio=0.4, tireStiffnessFactor=0.997),
  )
  CHEVROLET_MALIBU_HYBRID_CC = GMPlatformConfig(
    [GMCarDocs("Chevrolet Malibu Hybrid No-ACC 2017")],
    CarSpecs(mass=1450, wheelbase=2.8, steerRatio=15.8, centerToFrontRatio=0.4),
  )
  CHEVROLET_SUBURBAN_CC = GMPlatformConfig(
    [GMCarDocs("Chevrolet Suburban Premier No-ACC 2016-20")],
    CHEVROLET_SUBURBAN.specs,
  )
  CHEVROLET_TRAILBLAZER_CC = GMPlatformConfig(
    [GMCarDocs("Chevrolet Trailblazer No-ACC 2021-22")],
    CHEVROLET_TRAILBLAZER.specs,
  )
  CHEVROLET_TRAX = GMPlatformConfig(
    [GMCarDocs("Chevrolet TRAX 2024-25")],
    CarSpecs(mass=1365, wheelbase=2.7, steerRatio=16.4, centerToFrontRatio=0.4),
  )


class CruiseButtons:
  INIT = 0
  UNPRESS = 1
  RES_ACCEL = 2
  DECEL_SET = 3
  MAIN = 5
  CANCEL = 6


class AccState:
  OFF = 0
  ACTIVE = 1
  FAULTED = 3
  STANDSTILL = 4


class CanBus:
  POWERTRAIN = 0
  OBSTACLE = 1
  CAMERA = 2
  CHASSIS = 2
  LOOPBACK = 128
  DROPPED = 192


class GMFlags(IntFlag):
  PEDAL_LONG = 1
  CC_LONG = 2
  NO_CAMERA = 4
  NO_ACCELERATOR_POS_MSG = 8
  FORCE_BRAKE_C9 = 16
  SASCM = 32


# In a Data Module, an identifier is a string used to recognize an object,
# either by itself or together with the identifiers of parent objects.
# Each returns a 4 byte hex representation of the decimal part number. `b"\x02\x8c\xf0'"` -> 42790951
GM_BOOT_SOFTWARE_PART_NUMER_REQUEST = b'\x1a\xc0'  # likely does not contain anything useful
GM_SOFTWARE_MODULE_1_REQUEST = b'\x1a\xc1'
GM_SOFTWARE_MODULE_2_REQUEST = b'\x1a\xc2'
GM_SOFTWARE_MODULE_3_REQUEST = b'\x1a\xc3'

# Part number of XML data file that is used to configure ECU
GM_XML_DATA_FILE_PART_NUMBER = b'\x1a\x9c'
GM_XML_CONFIG_COMPAT_ID = b'\x1a\x9b'  # used to know if XML file is compatible with the ECU software/hardware

# This DID is for identifying the part number that reflects the mix of hardware,
# software, and calibrations in the ECU when it first arrives at the vehicle assembly plant.
# If there's an Alpha Code, it's associated with this part number and stored in the DID $DB.
GM_END_MODEL_PART_NUMBER_REQUEST = b'\x1a\xcb'
GM_END_MODEL_PART_NUMBER_ALPHA_CODE_REQUEST = b'\x1a\xdb'
GM_BASE_MODEL_PART_NUMBER_REQUEST = b'\x1a\xcc'
GM_BASE_MODEL_PART_NUMBER_ALPHA_CODE_REQUEST = b'\x1a\xdc'
GM_FW_RESPONSE = b'\x5a'

GM_FW_REQUESTS = [
  GM_BOOT_SOFTWARE_PART_NUMER_REQUEST,
  GM_SOFTWARE_MODULE_1_REQUEST,
  GM_SOFTWARE_MODULE_2_REQUEST,
  GM_SOFTWARE_MODULE_3_REQUEST,
  GM_XML_DATA_FILE_PART_NUMBER,
  GM_XML_CONFIG_COMPAT_ID,
  GM_END_MODEL_PART_NUMBER_REQUEST,
  GM_END_MODEL_PART_NUMBER_ALPHA_CODE_REQUEST,
  GM_BASE_MODEL_PART_NUMBER_REQUEST,
  GM_BASE_MODEL_PART_NUMBER_ALPHA_CODE_REQUEST,
]

GM_RX_OFFSET = 0x400

FW_QUERY_CONFIG = FwQueryConfig(
  requests=[request for req in GM_FW_REQUESTS for request in [
    Request(
      [StdQueries.SHORT_TESTER_PRESENT_REQUEST, req],
      [StdQueries.SHORT_TESTER_PRESENT_RESPONSE, GM_FW_RESPONSE + bytes([req[-1]])],
      rx_offset=GM_RX_OFFSET,
      bus=0,
      logging=True,
    ),
  ]],
  extra_ecus=[(Ecu.fwdCamera, 0x24b, None)],
)

# TODO: detect most of these sets live
EV_CAR = {
  CAR.CHEVROLET_VOLT,
  CAR.CHEVROLET_VOLT_2019,
  CAR.CHEVROLET_VOLT_ASCM,
  CAR.CHEVROLET_VOLT_CAMERA,
  CAR.CHEVROLET_VOLT_CC,
  CAR.CHEVROLET_BOLT_ACC_2022_2023,
  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
  CAR.CHEVROLET_BOLT_CC_2022_2023,
  CAR.CHEVROLET_BOLT_CC_2018_2021,
  CAR.CHEVROLET_BOLT_CC_2017,
  CAR.CHEVROLET_MALIBU_HYBRID_CC,
}

GM_AUTO_HOLD_CARS = {
  CAR.CHEVROLET_VOLT,
  CAR.CHEVROLET_VOLT_2019,
  CAR.CHEVROLET_VOLT_ASCM,
  CAR.CHEVROLET_VOLT_CAMERA,
  CAR.BUICK_LACROSSE,
}

# We're integrated at the camera with VOACC on these cars (instead of ASCM w/ OBD-II harness)
CAMERA_ACC_CAR = {
  CAR.CHEVROLET_BOLT_ACC_2022_2023,
  CAR.CHEVROLET_SILVERADO,
  CAR.CHEVROLET_EQUINOX,
  CAR.CHEVROLET_TRAILBLAZER,
  CAR.CHEVROLET_SUBURBAN_CAMERA,
  CAR.CHEVROLET_VOLT_CAMERA,
  CAR.CHEVROLET_BLAZER,
  CAR.CHEVROLET_TRAX,
  CAR.GMC_YUKON,
}

# Alt ASCMActiveCruiseControlStatus
ALT_ACCS = {CAR.CHEVROLET_SUBURBAN_CAMERA, CAR.GMC_YUKON, CAR.GMC_YUKON_CC}

# We're integrated at the Safety Data Gateway Module on these cars
SDGM_CAR = {
  CAR.CADILLAC_XT4,
  CAR.CADILLAC_XT5,
  CAR.CADILLAC_XT6,
  CAR.CHEVROLET_TRAVERSE,
  CAR.CHEVROLET_BLAZER,
  CAR.CHEVROLET_MALIBU_SDGM,
  CAR.BUICK_BABYENCLAVE,
  CAR.CHEVROLET_VOLT_2019,
  CAR.BUICK_ENVISION_SDGM,
}

CC_ONLY_CAR = {
  CAR.CHEVROLET_VOLT_CC,
  CAR.CHEVROLET_BOLT_CC_2018_2021,
  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
  CAR.CHEVROLET_BOLT_CC_2022_2023,
  CAR.CHEVROLET_BOLT_CC_2017,
  CAR.CHEVROLET_EQUINOX_CC,
  CAR.CHEVROLET_SUBURBAN_CC,
  CAR.GMC_YUKON_CC,
  CAR.CADILLAC_CT6_CC,
  CAR.CHEVROLET_TRAILBLAZER_CC,
  CAR.CADILLAC_XT5_CC,
  CAR.CHEVROLET_MALIBU_CC,
  CAR.CHEVROLET_MALIBU_HYBRID_CC,
  CAR.CHEVROLET_SILVERADO_CC,
  CAR.CADILLAC_XT4_CC,
}
CC_REGEN_PADDLE_CAR = {
  CAR.CHEVROLET_BOLT_CC_2018_2021,
  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL,
  CAR.CHEVROLET_BOLT_CC_2022_2023,
  CAR.CHEVROLET_BOLT_CC_2017,
}
CAMERA_ACC_CAR.update(CC_ONLY_CAR)

# ASCM-intercept variants preserve stock ACC. SASCM (0x2FF) enables alpha-long where supported.
ASCM_INT = {
  CAR.CHEVROLET_VOLT_ASCM,
  CAR.CHEVROLET_SUBURBAN_ASCM,
  CAR.GMC_ACADIA_ASCM,
  CAR.CHEVROLET_MALIBU_ASCM,
  CAR.CADILLAC_ESCALADE_ASCM,
  CAR.CADILLAC_ESCALADE_ESV_2019_ASCM,
  CAR.BUICK_LACROSSE_ASCM,
  CAR.BUICK_LACROSSE_ASCM_19US,
}

STEER_THRESHOLD = 1.0

DBC = CAR.create_dbc_map()

CAMERA_ACC_CAR.update({CAR.CHEVROLET_TRAX})
