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
# A Tesla scroll: one display unit, or to the next multiple of five for a fast scroll, as the Tesla's own cruise steps.
TESLA_STEPS = (-5, -1, 1, 5)
# How long speed sync can still match a receipt to its scroll (opendbc speed_sync MANUAL_COMPLETION_NS).
TESLA_RECEIPT_NS = 2_000_000_000


def tesla_set_speed(cp) -> bool:
  """A Tesla Model 3/Y where StarPilot may own SET beside the car's own cruise (Comma can set speed on Tesla dashboard).
  Card applies a request only while the switch is on (helper.tesla_set_speed)."""
  from opendbc.car.tesla.speed_sync import selected
  return selected(cp)


def eligible(cp) -> bool:
  return bool(cp is not None and (not cp.pcmCruise or tesla_set_speed(cp)) and cp.openpilotLongitudinalControl and
              not cp.passive and not cp.dashcamOnly and not cp.notCar)


def ui_authority(sm, cp, now_ns: int) -> bool:
  try:
    drive = int(sm["deviceState"].startedMonoTime)
    return bool(eligible(cp) and sm["deviceState"].started and
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

  def dispatch(self, increase: bool, sm, cp, publisher, *, now_ns: int, target_mps: float | None = None,
               step_units: int | None = None) -> bool:
    if (target_mps is not None and (type(target_mps) not in (int, float) or not math.isfinite(target_mps) or
        not V_CRUISE_MIN / 3.6 <= target_mps <= V_CRUISE_MAX / 3.6)) or not ui_authority(sm, cp, now_ns):
      return False
    if step_units is not None and (type(step_units) is not int or step_units not in TESLA_STEPS or target_mps is not None or
                                   not tesla_set_speed(cp)):
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
    if step_units is not None:
      # Which physical scroll this step came from. Metadata accompanies the action; it never creates or alters one.
      from openpilot.starpilot.controllers.tesla_gesture_context import current_source
      source = current_source()
      if source is not None and source.step == step_units and source.drive == int(sm["deviceState"].startedMonoTime):
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
    # Tesla scroll steps this consumer applied, for speed sync to match to the scrolls it saw on the car's bus.
    self.tesla_receipts = []

  def peek_tesla_receipts(self, now_boot_ns: int):
    """The receipts still worth offering. One that speed sync has not taken within its own window never will be
    (it did not see that scroll, or the receipt failed its checks), so it is dropped here."""
    self.tesla_receipts[:] = [item for item in self.tesla_receipts if 0 <= now_boot_ns - item.boot_ns <= TESLA_RECEIPT_NS]
    return tuple(self.tesla_receipts)

  def acknowledge_tesla_receipts(self, receipts):
    # Only discard the exact metadata that the synchronizer accepted.
    if type(receipts) is not tuple or len(receipts) > 16:
      return
    self.tesla_receipts[:] = [item for item in self.tesla_receipts if item not in receipts]

  def _record_tesla_receipt(self, request, cp, helper):
    # Never turn missing or bad optional metadata into a second action or a retry.
    try:
      from openpilot.starpilot.controllers.tesla_gesture_context import parse_source
      from openpilot.starpilot.controllers.wheel_actions import cp_fingerprint
      from opendbc.car.tesla.speed_sync import ManualReceipt
      source = parse_source(request.sourceTeslaGesture.to_dict())
      now_boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
      target = float(helper.v_cruise_kph)
      if (source is None or source.drive != self.drive or source.vehicle != cp_fingerprint(cp) or
          source.step != int(request.stepUnits) or not math.isfinite(target) or not 0 < target < 255 or
          not 0 < source.boot_ns <= now_boot <= source.boot_ns + TESLA_RECEIPT_NS):
        return
      if len(self.tesla_receipts) >= 16:
        return  # speed sync then treats this scroll like one the comma did not act on
      self.tesla_receipts.append(ManualReceipt(source.boot_ns, source.frame_index, source.name, source.fast,
                                               source.worker_session, source.sequence, source.document,
                                               source.vehicle, source.drive, now_boot, target))
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return

  def apply(self, msg, cp, cs, sm, helper, *, now_ns: int, is_metric: bool, enabled: bool) -> bool:
    try:
      kind = str(msg.slcAction.kind)
      if kind not in ("cruiseIncrease", "cruiseDecrease", "cruiseSet", "cruiseStep") or not msg.valid:
        return False
      if kind == "cruiseStep" and not getattr(helper, 'tesla_set_speed', False):
        return False
      request = msg.slcAction.controllerCruise
      drive = int(sm["deviceState"].startedMonoTime)
      if drive != self.drive:
        self.sequences.clear()
        self.drive = drive
        self.last_apply_ns = 0
        self.tesla_receipts.clear()
      session, sequence = str(request.sessionId), int(request.sequence)
      observed, expires = int(request.observedMonoTime), int(request.validUntilMonoTime)
      source = int(request.sourceCarControlMonoTime)
      if not (eligible(cp) and (not cp.pcmCruise or getattr(helper, 'tesla_set_speed', False)) and
              enabled and not cs.buttonEvents and now_ns - self.last_apply_ns >= 100_000_000 and
              cs.canValid and not cs.canTimeout and cs.cruiseState.available and
              sm["deviceState"].started and fresh_service(sm, "deviceState", drive, now_ns, 1_000_000_000) and
              fresh_service(sm, "carControl", drive, now_ns) and sm["carControl"].enabled and
              int(request.version) == 1 and int(request.driveStartMonoTime) == drive and
              str(request.carFingerprint) == cp.carFingerprint and SESSION.fullmatch(session) and
              sequence > self.sequences.get(session, 0) and drive < observed <= now_ns <= expires and
              expires - observed <= LIFETIME_NS and int(msg.logMonoTime) == observed and
              drive < source <= int(sm.logMonoTime["carControl"]) <= now_ns and now_ns - source <= LIFETIME_NS):
        return False
      if len(self.sequences) >= 8 and session not in self.sequences:
        return False
      self.sequences[session] = sequence
      self.last_apply_ns = now_ns
      if kind in ("cruiseSet", "cruiseStep"):
        target = float(request.targetSpeedMps)
        if kind == "cruiseStep":
          step = int(request.stepUnits)
          if step not in TESLA_STEPS or not helper.v_cruise_initialized:
            return False
          unit = 1 / 3.6 if is_metric else .44704
          # Step from the displayed selection, avoiding rounded kph/mph boundary drift.
          displayed = math.floor(helper.v_cruise_kph / 3.6 / unit + .5)
          value = (displayed + step if abs(step) == 1 else
                   (displayed // 5 + 1) * 5 if step > 0 else ((displayed - 1) // 5) * 5)
          target = value * unit
        if not math.isfinite(target) or not V_CRUISE_MIN / 3.6 - 1e-5 <= target <= V_CRUISE_MAX / 3.6 + 1e-5:
          return False
        previous = helper.v_cruise_kph
        helper.apply_slc_target(target, is_metric)
        if helper.v_cruise_kph != previous:
          helper.slc_cruise_change = (float(previous / 3.6), float(helper.v_cruise_kph / 3.6), 'accel' if helper.v_cruise_kph > previous else 'decel', False)
        if kind == "cruiseStep":
          self._record_tesla_receipt(request, cp, helper)
        return True
      button = car.CarState.ButtonEvent.Type.accelCruise if kind == "cruiseIncrease" else car.CarState.ButtonEvent.Type.decelCruise
      return helper.adjust_v_cruise(button, cs, is_metric)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return False
