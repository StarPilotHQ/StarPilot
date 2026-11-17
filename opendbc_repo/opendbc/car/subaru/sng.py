"""Dom global Subaru standstill timing; stock ACC retains pedal authority."""

from opendbc.car import structs
from opendbc.car.subaru.stock_aol import IMPREZA_MAIN_AOL
from opendbc.car.subaru.values import CAR, SubaruFlags

STOP_AND_GO = 8
SUPPORTED = frozenset((CAR.SUBARU_ASCENT, CAR.SUBARU_IMPREZA, CAR.SUBARU_IMPREZA_2020, CAR.SUBARU_FORESTER))


def eligible(cp):
  try:
    word = int(cp.safetyConfigs[0].safetyParam) if len(cp.safetyConfigs) == 1 else -1
    if word & IMPREZA_MAIN_AOL:
      if cp.carFingerprint != CAR.SUBARU_IMPREZA_2020 or cp.alternativeExperience != 32:
        return False
      word &= ~IMPREZA_MAIN_AOL
    words = (0, 8, 512, 520) if cp.carFingerprint == CAR.SUBARU_IMPREZA_2020 else (0, 8)
    return bool(
      cp.brand == 'subaru'
      and cp.carFingerprint in SUPPORTED
      and cp.pcmCruise
      and not cp.openpilotLongitudinalControl
      and not cp.alphaLongitudinalAvailable
      and not cp.passive
      and not cp.dashcamOnly
      and not cp.notCar
      and cp.transmissionType != structs.CarParams.TransmissionType.manual
      and cp.alternativeExperience in (0, 32)
      and int(cp.flags) & ~int(SubaruFlags.HAS_BSM | SubaruFlags.SEND_INFOTAINMENT | SubaruFlags.SMOOTHING_BOUND) == int(CAR[cp.carFingerprint].config.flags)
      and len(cp.safetyConfigs) == 1
      and cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.subaru
      and word in words
    )
  except (AttributeError, TypeError, ValueError, IndexError, KeyError):
    return False


def selected(cp):
  return eligible(cp) and bool(cp.safetyConfigs[0].safetyParam & STOP_AND_GO) and cp.autoResumeSng


def prepare(cp, enabled):
  if enabled and eligible(cp):
    cp.safetyConfigs[0].safetyParam |= STOP_AND_GO
    cp.autoResumeSng = True
  return cp


class StopAndGo:
  def __init__(self):
    self.last_standstill_frame = 0
    self.previous_distance = None
    self.departure_seen = False
    self.remaining = 0

  def reset(self, frame):
    self.last_standstill_frame = frame
    self.previous_distance = None
    self.departure_seen = False
    self.remaining = 0

  def update(self, cc, cs, frame):
    state = cs.out
    ready = (
      cc.enabled
      and getattr(cs, 'sng_lead_ready', False)
      and cc.hudControl.leadVisible
      and state.cruiseState.enabled
      and state.cruiseState.available
      and state.canValid
      and not state.canTimeout
      and not state.brakePressed
      and not state.gasPressed
      and not state.accFaulted
      and not state.steerFaultPermanent
      and not state.steerFaultTemporary
      and state.gearShifter == structs.CarState.GearShifter.drive
    )
    if not ready:
      self.reset(frame)
      return False, False
    distance = float(cs.close_distance)
    if not state.standstill:
      self.last_standstill_frame = frame
      self.departure_seen = False
      self.remaining = 0
    duration = frame - self.last_standstill_frame
    speed = state.standstill and duration > 50
    if duration >= 55:
      self.last_standstill_frame = frame
    if state.standstill and self.previous_distance is not None and 3.0 < distance < 4.5 and distance > self.previous_distance and not self.departure_seen:
      self.departure_seen = True
      self.remaining = 15
    throttle = state.standstill and self.remaining > 0
    self.remaining = max(0, self.remaining - 1)
    self.previous_distance = distance
    return throttle, speed
