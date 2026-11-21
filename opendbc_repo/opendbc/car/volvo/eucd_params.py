from enum import IntEnum

from opendbc.car import DT_CTRL
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.lateral import AngleSteeringLimits


class SteerDirection(IntEnum):
  """LKA steer direction; EUCD waits 8 frames on a direction change."""
  NONE = 0
  RIGHT = 1
  LEFT = 2
  BOTH = 3


class LongMode(IntEnum):
  """FSM3 Byte_2 mode bits; the ECM brakes hydraulically only in BRAKE."""
  BRAKE = 0
  COAST = 1
  DRIVE = 2


class EcmAccState(IntEnum):
  """ECM ACC state, EngineData Byte_4 bits 5:4."""
  OFF = 1
  STANDBY = 2
  ENGAGED = 3


class CarControllerEUCDParams:
  EUCD_ANGLE_LIMITS: AngleSteeringLimits = AngleSteeringLimits(90,
    ([0., 5., 15.], [5., .8, .15]),
    ([0., 5., 15.], [5., 3.5, 0.4]),
  )
  EUCD_STEER_TIMEOUT = 30 / DT_CTRL  # frames of 0 torque before the EPS faults
  EUCD_BLOCK_LEN = 8   # frames to block steering on a direction change
  EUCD_DEADZONE = 0.2  # deg, keep the previous direction inside it
  # Resume held this long on copies of the CEM's CCButtons: a one-shot burst was lost amid its frames (route 89e)
  EUCD_RESUME_HOLD_FRAMES = int(0.3 / DT_CTRL)
  # s of planner resume before SNG: 0.2 let a red-light flicker through, the ECM drove off on its own (route 8a4)
  EUCD_SNG_OP_RESUME_TIME = 0.5
  EUCD_RESUME_BURST_INTERVAL = 1.0  # s between resume bursts at a standstill
  EUCD_RESUME_BURST_MAX = 5  # bursts per standstill
  EUCD_SNG_ACK_TIME = 0.5  # s of FSM3 ACC_Check with the first burst, as the camera's ack (~300 ms after engaging)
  EUCD_MIN_STEER_SPEED = 0.1  # m/s, no steering nor cancel button below this

  # OP's stop to 0 (route 807), with OP owning the ECM
  EUCD_STOPPING_FLAG_SPEED = 2.6 * CV.KPH_TO_MS  # FSM4 b1 F1 -> F9 below this
  EUCD_HOLD_RELEASE_FRAMES = int(0.05 / DT_CTRL)  # brake request released after the car's hold
  EUCD_STANDSTILL_DELAY = 3.0  # s stopped before FSM3 ACC_Standstill
  EUCD_HOLD_ACCEL = -0.08  # m/s^2, FSM3 accel while the car holds (raw 0x7C)
  EUCD_STOPPING_ACCEL = -0.64  # m/s^2, at most while stopping, as the camera from its F9 to the car's hold (route 807)
  EUCD_GO_RETRY_TIME = 2.0  # s still stopped after the resume burst -> standstill again, retry
  EUCD_ECM_READY_TIMEOUT = 3.0  # s after FSM3 ACC_Standstill: resume sent even without the ECM's ready (seen 1.5 s)
  EUCD_EXIT_STANDBY_MAX = 1.0  # s, OP's long ended with the ECM still engaged: camera kept in standby at most this long

  # Longitudinal: FSM1/FSM3 sent at 50 Hz, limits
  EUCD_LONG_TX_PERIOD = 0.02  # s
  EUCD_ACCEL_MIN = -3.5  # m/s^2, as the panda's
  EUCD_ACCEL_MAX = 2.0   # m/s^2

  # FSM3 mode hysteresis on OP accel
  EUCD_BRAKE_ENTER = -0.50  # m/s^2, at and above EUCD_BRAKE_ENTER_BP[-1]
  EUCD_BRAKE_EXIT = -0.15
  # Slower, BRAKE enters earlier like the camera's, as coasting there does not slow the car (routes 0a, 22, 24)
  EUCD_BRAKE_ENTER_BP = [15. * CV.KPH_TO_MS, 40. * CV.KPH_TO_MS]
  EUCD_BRAKE_ENTER_V = [-0.10, EUCD_BRAKE_ENTER]
  EUCD_BRAKE_HYSTERESIS = EUCD_BRAKE_EXIT - EUCD_BRAKE_ENTER  # exit this much above the enter, never above 0
  EUCD_DRIVE_ENTER = 0.08
  EUCD_DRIVE_EXIT = 0.04

  # FSM3 Byte_3 traction = gain * accel + offset, as the camera sends it
  EUCD_TRACTION_COAST = (94., -9.)
  EUCD_TRACTION_DRIVE = (97., -6.)

  # FSM4 14-bit brake target = zero + gain * accel, as the camera's own (248 segments, route 29); no-brake map below
  EUCD_FSM4_BRAKE_ZERO = 13332
  EUCD_FSM4_BRAKE_GAIN = 255.  # counts per m/s^2
  EUCD_FSM4_BRAKE_ACCEL_RANGE = (EUCD_ACCEL_MIN, 0.2)
  EUCD_FSM4_NO_BRAKE_ACCEL_BP = [-0.45, -0.2, 0.0, 0.2, 0.45, 0.8, 1.5]
  EUCD_FSM4_NO_BRAKE_VALUE_V = [13276, 13299, 13318, 13338, 13333, 13377, 13449]

  # BRAKE thresholds shifted by the slope's coast accel
  EUCD_COAST_PITCH_GAIN = 5.65  # m/s^2 per sin(pitch)
  EUCD_DOWNHILL_BRAKE_OFFSET_MAX = 0.15  # m/s^2, downhill shift cap
  # Extra uphill margin before BRAKE
  EUCD_UPHILL_BRAKE_ENTER_EXTRA = 0.6  # fraction of the uphill offset

  # Accel rate limits: falling only against jumps (as Toyota), rising moderate (route 1f, 678 s)
  EUCD_JERK_UP = 4.0     # m/s^3
  EUCD_JERK_DOWN = 12.0  # m/s^3


# Delphi ESR 2.5 on the aux bus, read directly
RADAR_ESR = "ESR"


class CANBUS:
  pt = 0
  body = 1  # aux bus: Delphi ESR radar, CEM dynamics
  cam = 2
