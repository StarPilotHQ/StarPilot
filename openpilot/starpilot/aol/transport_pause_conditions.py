"""Event and stock-cruise predicates for retaining lateral intent."""
from openpilot.cereal import log


AVAILABILITY_EVENTS = (log.OnroadEvent.EventName.commIssue, log.OnroadEvent.EventName.commIssueAvgFreq)


def availability_only(events):
  faults = [event for event in events if event.immediateDisable or event.softDisable]
  return bool(faults and all(event.name in AVAILABILITY_EVENTS and event.softDisable and
    not event.immediateDisable for event in faults))


HOST_SOURCE_AVAILABILITY_EVENTS = (*AVAILABILITY_EVENTS, log.OnroadEvent.EventName.selfdrivedLagging)


def host_source_availability_only(events):
  faults = [event for event in events if event.immediateDisable or event.softDisable]
  return bool(faults and not any(event.userDisable for event in events) and
    all(event.name in HOST_SOURCE_AVAILABILITY_EVENTS and event.softDisable and
        not event.immediateDisable for event in faults))


def stock_lateral_off(cp, cs, native, *, allow_independent_cancel=False, allow_independent_brake=False,
  allow_independent_gas=False, allow_independent_main=False):
  from openpilot.starpilot.nostalgia import physical_cancel
  from opendbc.car.hyundai.canfd_angle_aol import qualified as angle_qualified

  from opendbc.car.hyundai.canfd_stock_aol import qualified as stock_canfd_qualified

  from openpilot.starpilot.car.toyota.aol import qualified as toyota_qualified

  from opendbc.car.gm.aol import qualified_gm
  from opendbc.car.subaru.stock_aol import qualified as subaru_qualified

  stock_gas = bool(cp.brand == 'gm' and qualified_gm(cp) and cp.pcmCruise and not cp.openpilotLongitudinalControl or
                   cp.brand == 'subaru' and subaru_qualified(cp, marked_only=True))
  from opendbc.car.hyundai.classic_scc_aol import qualified as classic_stock_qualified
  classic_stock = classic_stock_qualified(cp, marked_only=True)
  independent_angle = angle_qualified(cp, marked_only=True)
  independent_brake = independent_angle or (allow_independent_brake and (
    toyota_qualified(cp, marked_only=True) and not cs.cruiseState.enabled or
    cp.brand == 'hyundai' and len(cp.safetyConfigs) == 1 and (stock_canfd_qualified(cp, marked_only=True) or classic_stock)))
  return bool(cp.pcmCruise and (cs.cruiseState.available or allow_independent_main and classic_stock) and
    (not cs.cruiseState.enabled or independent_brake and allow_independent_brake and cs.brakePressed) and
    cs.canValid and not cs.canTimeout and not cs.steerFaultPermanent and not cs.steerFaultTemporary and
    not cs.accFaulted and not cs.doorOpen and not cs.seatbeltUnlatched and
    (not cs.brakePressed or independent_brake and allow_independent_brake) and
    not cs.regenBraking and (not cs.gasPressed or independent_angle or
      allow_independent_gas and (stock_gas or toyota_qualified(cp, marked_only=True) or
        cp.brand == 'hyundai' and (stock_canfd_qualified(cp, marked_only=True) or classic_stock))) and
    (not physical_cancel(cs, cp) or independent_angle and allow_independent_cancel) and
    native is not None and native.requestedLateral and native.lateralAllowed and not native.requestedLongitudinal)


def forward_gear_allowed(cp, gear) -> bool:
  from opendbc.car import structs
  gears = structs.CarState.GearShifter
  return gear in (gears.drive, gears.sport, gears.low, gears.brake) or (
    cp.carFingerprint == 'SUBARU_LEGACY_2025' and gear == gears.manumatic)


def subaru_stop_drive_gear(cp, gear, subaru_stop: bool) -> bool:
  from opendbc.car import structs
  gears = structs.CarState.GearShifter
  return gear == gears.drive or (
    subaru_stop and cp.carFingerprint == 'SUBARU_LEGACY_2025' and gear == gears.manumatic)
