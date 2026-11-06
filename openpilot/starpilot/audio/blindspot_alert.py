"""Optional blind-spot presentation; never changes lane-change or control authority."""

KEYS = ("LoudBlindspotAlert", "LoudBlindspotAlertWhenDisengaged")
ALERT_TYPE = "blindSpotAdvisory/warning"
EVENT_TYPE = "blindSpotAdvisory"


def blindspot_alert(cs, sm, *, loud: bool, without_lateral: bool, lane_change_blocked: bool, now_ns: int,
                    car_ns: int, car_valid: bool):
  if not loud and not without_lateral:
    return None
  try:
    if not car_valid or not 0 < car_ns <= now_ns or now_ns - car_ns > 200_000_000:
      return None
    if not sm['deviceState'].started or not sm.all_checks(['deviceState', 'carControl']):
      return None
    if any(not 0 < int(sm.logMonoTime[key]) <= now_ns or now_ns - int(sm.logMonoTime[key]) > limit
           for key, limit in (('deviceState', 2_000_000_000), ('carControl', 200_000_000))):
      return None
    if not cs.canValid or cs.canTimeout:
      return None
    matching_signal = (bool(cs.leftBlinker) != bool(cs.rightBlinker) and
                       (cs.leftBlinker and cs.leftBlindspot or cs.rightBlinker and cs.rightBlindspot))
    if not (loud and lane_change_blocked or
            without_lateral and not sm['carControl'].latActive and matching_signal):
      return None
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return None
  from openpilot.selfdrive.selfdrived.events import Alert, AlertSize, AlertStatus, AudibleAlert, Priority, VisualAlert
  alert = Alert("Car Detected in Blindspot", "", AlertStatus.userPrompt, AlertSize.small,
                Priority.LOW, VisualAlert.none, AudibleAlert.warningSoft, .1)
  # Independent of engagement's WARNING filter so the parked/offroad checks,
  # not lateral activation, determine eligibility for this optional advisory.
  alert.alert_type, alert.event_type = ALERT_TYPE, EVENT_TYPE
  return alert
