"""Speed sync: read-only admission of the comma's set speed as the Tesla's target; no action dispatch."""
import hashlib
import math
import time

from opendbc.car.tesla.speed_sync import SyncAuthority
from openpilot.starpilot.common.car_control_config import MAX_ASSIGNMENT_BYTES, PARAM
from openpilot.starpilot.saved_source import read_saved

SETTINGS_PERIOD_NS = 1_000_000_000


def _settings(card, now_ns):
  """(Safe Mode off and the saved units matching card's, the saved assignments' hash), read once a second: this runs in
  card's 100 Hz loop. The hash is the one the input worker stamps on each scroll it reports."""
  cached = getattr(card, "_tesla_sync_settings", None)
  if cached is not None and 0 <= now_ns - cached[0] < SETTINGS_PERIOD_NS:
    return cached[1], cached[2]
  metric, metric_readable = read_saved(card.params, "IsMetric", 8)
  good = bool(read_saved(card.params, "SafeMode", 8) in ((None, True), (b"0", True)) and
              metric_readable and metric in (None, b"0", b"1") and
              type(card.is_metric) is bool and card.is_metric == (metric == b"1"))
  raw, readable = read_saved(card.params, PARAM, MAX_ASSIGNMENT_BYTES)
  document = hashlib.sha256(raw or b"").hexdigest() if readable else ""
  card._tesla_sync_settings = (now_ns, good, document)
  return good, document


def _vehicle(card):
  """This car's identity as the input worker reports it; the car's parameters never change while card runs."""
  vehicle = getattr(card, "_tesla_sync_vehicle", None)
  if vehicle is None:
    from openpilot.starpilot.controllers.wheel_actions import cp_fingerprint
    vehicle = card._tesla_sync_vehicle = cp_fingerprint(card.CP)
  return vehicle


def publish_context(card, state, *, now_boot_ns=None):
  controller = card.CI.CC
  if controller is None or getattr(controller, "speed_sync", None) is None:
    return
  # A failed read invalidates the previous frame's admission immediately.
  controller.speed_sync_authority = None
  try:
    cp, helper = card.CP, card.v_cruise_helper
    stamp = time.clock_gettime_ns(time.CLOCK_BOOTTIME) if now_boot_ns is None else now_boot_ns
    if type(stamp) is not int or stamp <= 0 or not helper.tesla_set_speed:
      return
    good, document = _settings(card, stamp)
    if not good:
      return
    target = float(helper.v_cruise_kph)
    if not math.isfinite(target) or not 0 < target < 255 or abs(target - float(state.vCruise)) > 1e-4:
      return
    identity = hashlib.sha256(repr((str(cp.carFingerprint), card.is_metric)).encode()).hexdigest()
    # The driver's own scrolls that card has applied: speed sync matches each to the scroll it saw on the car's bus,
    # and hands back the ones it has taken so they are dropped here.
    consumer = getattr(card, "controller_cruise_consumer", None)
    receipts = ()
    if consumer is not None:
      consumer.acknowledge_tesla_receipts(controller.speed_sync.take_manual_receipt_acks())
      receipts = consumer.peek_tesla_receipts(stamp)
    drive = int(card.sm["deviceState"].startedMonoTime) if getattr(card, "sm", None) is not None else 0
    controller.speed_sync_authority = SyncAuthority(stamp, float(state.vCruise), card.is_metric, identity, True,
                                                    document, _vehicle(card), drive, receipts)
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError, RuntimeError):
    return
