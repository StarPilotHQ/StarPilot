"""Speed sync: read-only Card target/configuration admission; no action dispatch."""
import hashlib
import json
import math
import time

from opendbc.car.tesla.speed_sync import SyncAuthority
from openpilot.starpilot.common.car_control_config import PARAM, SPEED_UP, SPEED_DOWN, parse_assignments, vehicle_supported
from openpilot.starpilot.saved_source import read_saved


def publish_context(card, state, *, now_boot_ns=None):
  controller = card.CI.CC
  if controller is None or getattr(controller, "speed_sync", None) is None:
    return
  # A failed read invalidates the previous frame's admission immediately.
  controller.speed_sync_authority = None
  try:
    cp, helper, params = card.CP, card.v_cruise_helper, card.params
    owned = bool(not cp.pcmCruise or helper.slc_pcm or helper.tesla_controls_pcm)
    if (cp.carFingerprint not in ("TESLA_MODEL_3", "TESLA_MODEL_Y") or not vehicle_supported(cp) or
        not cp.openpilotLongitudinalControl or not owned or
        read_saved(params, "SafeMode", 8) not in ((None, True), (b"0", True))):
      return
    # Vehicle settings: Match the Tesla's set speed, which only applies with Independent set speed on.
    from openpilot.starpilot.ui.tesla_set_speed_feature import INDEPENDENT, SYNC, saved
    if not saved(params, SYNC) or not saved(params, INDEPENDENT):
      return
    raw, readable = read_saved(params, PARAM, 32768)
    document = parse_assignments(raw) if readable else None
    metric, metric_readable = read_saved(params, "IsMetric", 8)
    if (not document or document["vehicle"] != cp.carFingerprint or
        not metric_readable or metric not in (None, b"0", b"1") or
        type(card.is_metric) is not bool or card.is_metric != (metric == b"1")):
      return
    for name, expected in (("right_wheel_up", SPEED_UP), ("right_wheel_down", SPEED_DOWN)):
      binding = document["bindings"].get(name)
      if not binding or binding["key"] != expected or binding["value"] is not None:
        return
    target = float(helper.v_cruise_kph)
    if not math.isfinite(target) or not 0 < target < 255 or abs(target - float(state.vCruise)) > 1e-4:
      return
    # These exact mappings dispatch cruiseStep(+/-1 or a fast +/-5 snap).
    # The general CruiseIntervals sliders do not govern this path. Preserve
    # them unchanged and do not inhibit the mirror for unrelated slider edits.
    identity_data = (document["vehicle"], card.is_metric, bool(helper.slc_pcm),
                     bool(helper.tesla_controls_pcm), bool(cp.pcmCruise),
                     tuple((name, document["bindings"][name]["key"], document["bindings"][name]["value"])
                           for name in ("right_wheel_up", "right_wheel_down")))
    identity = hashlib.sha256(json.dumps(identity_data, separators=(",", ":")).encode()).hexdigest()
    # Do not publish a snapshot whose assignments changed while it was read.
    if (read_saved(params, PARAM, 32768) != (raw, True) or
        read_saved(params, "IsMetric", 8) != (metric, True) or
        read_saved(params, "SafeMode", 8) not in ((None, True), (b"0", True))):
      return
    stamp = time.clock_gettime_ns(time.CLOCK_BOOTTIME) if now_boot_ns is None else now_boot_ns
    if type(stamp) is not int or stamp <= 0:
      return
    consumer = getattr(card, "controller_cruise_consumer", None)
    if consumer is not None:
      consumer.acknowledge_tesla_receipts(controller.speed_sync.take_manual_receipt_acks())
    if consumer is not None and getattr(consumer, "tesla_receipt_overflow", False):
      controller.speed_sync._fail("manual_receipt_overflow")
      return
    receipts = consumer.peek_tesla_receipts() if consumer is not None else ()
    drive = int(card.sm["deviceState"].startedMonoTime) if getattr(card, "sm", None) is not None else 0
    from openpilot.starpilot.controllers.wheel_actions import cp_fingerprint
    controller.speed_sync_authority = SyncAuthority(stamp, float(state.vCruise), card.is_metric, identity, owned,
                                                    hashlib.sha256(raw).hexdigest(), cp_fingerprint(cp),
                                                    drive, receipts)
  except (AttributeError, TypeError, ValueError, OverflowError, OSError, RuntimeError):
    return
