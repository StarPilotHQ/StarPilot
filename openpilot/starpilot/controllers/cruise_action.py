"""Short-lived controller requests; Card owns the software cruise speed."""

import math
import re
import secrets
import time

from openpilot.cereal import messaging
from opendbc.car.structs import car
from openpilot.selfdrive.car.cruise import V_CRUISE_MIN, V_CRUISE_MAX
from openpilot.starpilot.conditional_mode.ui_action import fresh_service

LIFETIME_NS = 250_000_000
SESSION = re.compile(r"[0-9a-f]{32}\Z")


def eligible(cp) -> bool:
  return bool(cp is not None and (not cp.pcmCruise or cp.carFingerprint in ('TESLA_MODEL_3', 'TESLA_MODEL_Y')) and cp.openpilotLongitudinalControl and
              not cp.passive and not cp.dashcamOnly and not cp.notCar)



def software_slc_current(sm, cp, now_ns, *, after_ns=0):
  if not cp.pcmCruise:
    return True
  try:
    stamp = int(sm.logMonoTime['slcState'])
    s = sm['slcState']
    return bool(cp.carFingerprint in ('TESLA_MODEL_3', 'TESLA_MODEL_Y') and sm.valid['slcState'] and sm.alive['slcState'] and
      after_ns < stamp <= now_ns <= stamp + 100_000_000 and after_ns < int(s.frameMonoTime) <= stamp and
      now_ns - int(s.frameMonoTime) <= 100_000_000 and s.enabled and not s.displayOnly)
  except (AttributeError, KeyError, TypeError, ValueError):
    return False

def ui_authority(sm, cp, now_ns: int, *, tesla_controls: bool = False, match_owner: bool = False) -> bool:
  # A saved assignment is a preference, never permission to bypass live SLC. With SLC off only Match the Tesla's set
  # speed (match_owner) gives the comma its set speed; card checks its own live ownership before applying anything.
  try:
    drive = int(sm["deviceState"].startedMonoTime)
    return bool(eligible(cp) and (software_slc_current(sm, cp, now_ns) or match_owner) and sm["deviceState"].started and
                fresh_service(sm, "deviceState", drive, now_ns, 1_000_000_000) and
                all(fresh_service(sm, name, drive, now_ns) for name in ("carState", "carControl", "selfdriveState")) and
                sm["carState"].canValid and not sm["carState"].canTimeout and
                sm["carControl"].enabled and sm["selfdriveState"].enabled)
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return False


class CruiseActionPublisher:
  def __init__(self):
    self.session = secrets.token_hex(16)
    self.sequence = 0

  def dispatch(self, increase: bool, sm, cp, publisher, *, now_ns: int, target_mps: float | None = None, step_units: int | None = None,
               tesla_controls: bool = False, match_owner: bool = False, intervals=None) -> bool:
    if (target_mps is not None and (type(target_mps) not in (int, float) or not math.isfinite(target_mps) or
        not V_CRUISE_MIN / 3.6 <= target_mps <= V_CRUISE_MAX / 3.6)) or not ui_authority(sm, cp, now_ns, tesla_controls=tesla_controls,
                                                                                         match_owner=match_owner):
      return False
    if step_units is not None and (type(step_units) is not int or not 1 <= abs(step_units) <= 99 or target_mps is not None):
      return False
    self.sequence += 1
    msg = messaging.new_message("slcAction", valid=True)
    msg.logMonoTime = now_ns
    msg.slcAction.kind = ("cruiseStep" if step_units is not None else "cruiseSet" if target_mps is not None else
                          "cruiseIncrease" if increase else "cruiseDecrease")
    msg.slcAction.controllerCruise = {
      "version": 1, "sessionId": self.session, "sequence": self.sequence,
      "observedMonoTime": now_ns, "validUntilMonoTime": now_ns + LIFETIME_NS,
      "driveStartMonoTime": int(sm["deviceState"].startedMonoTime),
      "carFingerprint": cp.carFingerprint, "sourceCarControlMonoTime": int(sm.logMonoTime["carControl"]),
    }
    if step_units is not None:
      msg.slcAction.controllerCruise.stepUnits = step_units
    if target_mps is not None:
      msg.slcAction.controllerCruise.targetSpeedMps = float(target_mps)
    # Metadata accompanies the existing action; it never creates or alters one.
    if step_units is not None and any(int(c.safetyParam) & 2048 for c in getattr(cp, "safetyConfigs", ())):
      from openpilot.starpilot.controllers.tesla_gesture_context import current_source
      source = current_source()
      if source is not None and source.matches(step_units, intervals) and source.drive == int(sm["deviceState"].startedMonoTime):
        now_boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        if 0 < source.boot_ns <= now_boot <= source.boot_ns + 500_000_000:
          msg.slcAction.controllerCruise.sourceTeslaGesture = source.to_wire()
    publisher.send("slcAction", msg)
    return True


class CruiseActionConsumer:
  def __init__(self):
    self.sequences = {}
    self.drive = 0
    self.last_apply_ns = 0
    self.tesla_receipts = []
    self.tesla_receipt_overflow = False

  def peek_tesla_receipts(self):
    return tuple(self.tesla_receipts)

  def acknowledge_tesla_receipts(self, receipts):
    # Only discard the exact metadata that the synchronizer accepted. Targets
    # and ordinary action processing remain owned by _apply_existing.
    if type(receipts) is not tuple or len(receipts) > 16:
      return
    self.tesla_receipts[:] = [item for item in self.tesla_receipts if item not in receipts]

  def take_tesla_receipts(self):
    result = tuple(self.tesla_receipts)
    self.tesla_receipts.clear()
    return result

  def apply(self, msg, cp, cs, sm, helper, *, now_ns: int, is_metric: bool, enabled: bool, after_ns: int = 0) -> bool:
    previous_drive = self.drive
    accepted = self._apply_existing(msg, cp, cs, sm, helper, now_ns=now_ns, is_metric=is_metric, enabled=enabled, after_ns=after_ns)
    if self.drive != previous_drive:
      self.tesla_receipts.clear()
      self.tesla_receipt_overflow = False
    if accepted:
      self._record_tesla_receipt(msg, cp, helper)
    return accepted

  def _record_tesla_receipt(self, msg, cp, helper):
    # Never turn missing/bad optional metadata into a second action or a retry.
    try:
      if (str(msg.slcAction.kind) != "cruiseStep" or cp.carFingerprint not in ("TESLA_MODEL_3", "TESLA_MODEL_Y") or
          not any(int(c.safetyParam) & 2048 for c in cp.safetyConfigs)):
        return
      from openpilot.starpilot.controllers.tesla_gesture_context import parse_source
      from openpilot.starpilot.controllers.wheel_actions import cp_fingerprint
      from opendbc.car.tesla.speed_sync import ManualReceipt
      request = msg.slcAction.controllerCruise
      source = parse_source(request.sourceTeslaGesture.to_dict())
      now_boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
      target = float(helper.v_cruise_kph)
      if (source is None or source.drive != self.drive or source.vehicle != cp_fingerprint(cp) or
          not source.matches(int(request.stepUnits), getattr(helper, 'intervals', None)) or not math.isfinite(target) or not 0 < target < 255 or
          not 0 < source.boot_ns <= now_boot <= source.boot_ns + 2_000_000_000):
        return
      if len(self.tesla_receipts) >= 16:
        self.tesla_receipt_overflow = True
        return
      self.tesla_receipts.append(ManualReceipt(source.boot_ns, source.frame_index, source.name, source.fast,
                                             source.worker_session, source.sequence, source.document,
                                             source.vehicle, source.drive, now_boot, target))
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return

  def _apply_existing(self, msg, cp, cs, sm, helper, *, now_ns: int, is_metric: bool, enabled: bool, after_ns: int = 0) -> bool:
    try:
      kind = str(msg.slcAction.kind)
      if kind not in ("cruiseIncrease", "cruiseDecrease", "cruiseSet", "cruiseStep") or not msg.valid:
        return False
      request = msg.slcAction.controllerCruise
      drive = int(sm["deviceState"].startedMonoTime)
      if drive != self.drive:
        self.sequences.clear()
        self.drive = drive
        self.last_apply_ns = 0
      session, sequence = str(request.sessionId), int(request.sequence)
      observed, expires = int(request.observedMonoTime), int(request.validUntilMonoTime)
      source = int(request.sourceCarControlMonoTime)
      # Live SLC owns the set speed, or (SLC off) Tesla controls own it through Match the Tesla's set speed.
      owner = ((software_slc_current(sm, cp, now_ns, after_ns=after_ns) and (not cp.pcmCruise or helper.slc_pcm)) or
               getattr(helper, 'match_pcm', False))
      if not (eligible(cp) and owner and enabled and not cs.buttonEvents and now_ns - self.last_apply_ns >= 100_000_000 and
              cs.canValid and not cs.canTimeout and cs.cruiseState.available and
              sm["deviceState"].started and fresh_service(sm, "deviceState", drive, now_ns, 1_000_000_000) and
              fresh_service(sm, "carControl", drive, now_ns) and sm["carControl"].enabled and
              int(request.version) == 1 and int(request.driveStartMonoTime) == drive and
              str(request.carFingerprint) == cp.carFingerprint and SESSION.fullmatch(session) and
              sequence > self.sequences.get(session, 0) and max(drive, after_ns) < observed <= now_ns <= expires and
              expires - observed <= LIFETIME_NS and int(msg.logMonoTime) == observed and
              drive < source <= int(sm.logMonoTime["carControl"]) <= now_ns and now_ns - source <= LIFETIME_NS):
        return False
      if len(self.sequences) >= 8 and session not in self.sequences:
        return False
      self.sequences[session] = sequence
      self.last_apply_ns = now_ns
      if kind in ("cruiseSet", "cruiseStep"):
        target = float(request.targetSpeedMps)
        if kind == 'cruiseStep':
          step = int(request.stepUnits)
          if not 1 <= abs(step) <= 99 or not helper.v_cruise_initialized:
            return False
          unit = 1 / 3.6 if is_metric else .44704
          current = helper.v_cruise_kph / 3.6
          displayed = math.floor(current / unit + .5)
          interval = abs(step)
          snap = interval % 10 == 0 or (interval % 5 == 0 and cp.carFingerprint in ('TESLA_MODEL_3', 'TESLA_MODEL_Y'))
          # Snap from the displayed selection, avoiding rounded kph/mph boundary drift.
          value = ((displayed // interval + 1) * interval if step > 0 else
                   ((displayed - 1) // interval) * interval) if snap else current / unit + step
          low, high = (8, 145) if is_metric else (5, 90)
          target = min(high, max(low, value)) * unit
        if not math.isfinite(target) or not V_CRUISE_MIN / 3.6 - 1e-5 <= target <= V_CRUISE_MAX / 3.6 + 1e-5:
          return False
        previous = helper.v_cruise_kph
        helper.apply_slc_target(target, is_metric)
        if helper.v_cruise_kph != previous:
          helper.slc_cruise_change = (float(previous / 3.6), float(helper.v_cruise_kph / 3.6), 'accel' if helper.v_cruise_kph > previous else 'decel', False)
        return True
      button = car.CarState.ButtonEvent.Type.accelCruise if kind == "cruiseIncrease" else car.CarState.ButtonEvent.Type.decelCruise
      return helper.adjust_v_cruise(button, cs, is_metric)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return False
