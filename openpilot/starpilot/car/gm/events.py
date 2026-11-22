from copy import copy

from opendbc.car.gm.values import is_bolt_cc_profile
from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.events import EVENTS, ET, ImmediateDisableAlert


def qualified(cp) -> bool:
  try:
    return bool(is_bolt_cc_profile(cp))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def current_regen_gear(sm, source_ns: int) -> bool:
  service = 'starpilotCarState'
  if not (source_ns > 0 and sm.valid.get(service, False) and sm.logMonoTime.get(service, 0) == source_ns):
    return False
  companion = sm[service]
  return companion.sourceCarStateMonoTime == source_ns and companion.gmRegenGear


def regen_gear_alert_text(alert):
  if not alert.alert_type.startswith('wrongGear/'):
    return alert
  display = copy(alert)
  if display.alert_text_1 == "Gear not D":
    display.alert_text_1 = "Regen Gear Selected"
  if display.alert_text_2 == "Gear not D":
    display.alert_text_2 = "Regen Gear Selected"
  return display


def event_rules(cp) -> dict:
  if not qualified(cp):
    return {}
  name = log.OnroadEvent.EventName.wrongGear
  return {name: {**EVENTS[name], ET.USER_DISABLE: ImmediateDisableAlert("Gear not D")}}


def unsupported_gear(cp, gear) -> bool:
  return qualified(cp) and gear not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low)
