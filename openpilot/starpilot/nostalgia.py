"""Saved Ioniq left-paddle longitudinal pause, separate from ordinary Cancel."""

from opendbc.car.structs import car

from openpilot.selfdrive.selfdrived.events import ET, EVENTS, EventName
from openpilot.starpilot.longitudinal.ioniq6_start import eligible as ioniq6_long_eligible
from openpilot.starpilot.saved_source import read_saved


LONGITUDINAL_ONLY_EVENTS = (EventName.radarFault, EventName.radarTempUnavailable)


ButtonType = car.CarState.ButtonEvent.Type


def saved_enabled(params) -> bool:
  try:
    raw, readable = read_saved(params, "NostalgiaMode", 8)
    return readable and raw == b"1"
  except (OSError, TypeError, ValueError):
    return False


def _ioniq5_long_eligible(cp) -> bool:
  from opendbc.car.hyundai.left_paddle import IONIQ_5_CARS, has_left_paddle
  from openpilot.starpilot.car.hyundai.aol import policy_for
  return bool(has_left_paddle(cp) and cp.carFingerprint in IONIQ_5_CARS and
              cp.openpilotLongitudinalControl and policy_for(cp).paddle_pause)


def paddle_cancel(cp, cs, *, enabled: bool, saved: bool) -> bool:
  return bool(saved and enabled and (ioniq6_long_eligible(cp) or _ioniq5_long_eligible(cp)) and cs.canValid and not cs.canTimeout and
              any(event.type == ButtonType.altButton2 and event.pressed for event in cs.buttonEvents))


def physical_cancel(cs, cp=None) -> bool:
  from opendbc.car.gm.cancel_remap import enabled as gm_cancel_remap_enabled
  if cp is not None and gm_cancel_remap_enabled(cp):
    return False
  return any(event.type == ButtonType.cancel for event in cs.buttonEvents)


def aol_no_entry(event_names, cs, *, paddle_only_cancel: bool, cruise_main_required: bool = True,
                 allow_below_engage_speed: bool = False, allow_lateral_cancel: bool = False) -> bool:
  """Apply vehicle-owned lateral exceptions without changing ordinary cruise events."""
  return any(event not in (EventName.pedalPressed, EventName.seatbeltNotLatched, EventName.resumeBlocked, EventName.doorOpen,
                          *LONGITUDINAL_ONLY_EVENTS) and
             ET.NO_ENTRY in EVENTS.get(event, {}) and
             (event != EventName.belowEngageSpeed or not allow_below_engage_speed) and
             (event != EventName.wrongCarMode or cruise_main_required) and
             (event != EventName.buttonCancel or
              (not allow_lateral_cancel and (not paddle_only_cancel or physical_cancel(cs))))
             for event in event_names)
