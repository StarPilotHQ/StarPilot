def seatbelt_lateral_soft_event_allowed(cp, cs, intent, native, event, *, source_ns, now_ns, axis_session_id,
                                       pending_lateral_request=False, gear_pause=False) -> bool:
  """Keep the belt a LONG lock while qualified pauses retain armed LAT intent."""
  from opendbc.car.structs import car
  from openpilot.starpilot.aol.vehicle import policy_for
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS, native_inventory_matches_cp
  try:
    gears = car.CarState.GearShifter
    legacy_manumatic = cp.carFingerprint == 'SUBARU_LEGACY_2025' and cs.gearShifter == gears.manumatic
    paused_gear = bool(gear_pause is True and (cs.gearShifter in
                       (gears.drive, gears.low, gears.sport, gears.brake, gears.neutral, gears.reverse) or
                       legacy_manumatic))
    paused_native = bool(paused_gear and native is not None and
                         not native.requestedLateral and not native.lateralAllowed and
                         not native.requestedLongitudinal and not native.longitudinalAllowed)
    # A temporary EPS restriction withdraws steering independently. The belt
    # remains a LONG lock without making that temporary LAT pause critical.
    paused_eps = bool(cs.steerFaultTemporary and not cs.steerFaultPermanent and native is not None and
                      not native.lateralAllowed and
                      not native.requestedLongitudinal and not native.longitudinalAllowed)
    pending_native = bool(not cs.steerFaultTemporary and not paused_gear and pending_lateral_request and native is not None and
                          not native.requestedLateral and not native.lateralAllowed)
    return bool(policy_for(cp).intent_supported and event.name == log.OnroadEvent.EventName.seatbeltNotLatched and event.softDisable and
      event.noEntry and not event.immediateDisable and not event.userDisable and
      cs.seatbeltUnlatched and cs.canValid and not cs.canTimeout and not cs.accFaulted and not cs.doorOpen and
      (not cs.steerFaultTemporary or paused_eps) and not cs.steerFaultPermanent and cs.cruiseState.available and
      (paused_gear or cs.gearShifter in (gears.drive, gears.low, gears.sport, gears.brake) or legacy_manumatic) and
      intent is not None and intent.settingsQualified and
      ((intent.allowedLatch or paused_gear) and intent.lateralArmed or
       pending_native and not intent.allowedLatch and not intent.lateralArmed) and
      intent.producerSessionId and intent.carStateLogMonoTime == source_ns and
      0 < source_ns <= intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime and
      now_ns - source_ns <= AXIS_MAX_AGE_NS and
      intent.validUntilMonoTime - intent.observedMonoTime <= SAFETY_MAX_AGE_NS and
      native is not None and native.compatible and int(native.protocolVersion) == 1 and
      axis_session_id and native.axisSessionId == axis_session_id and
      (native.requestedLateral and native.lateralAllowed or pending_native or paused_native or paused_eps) and native.pandaInventory and
      native_inventory_matches_cp(native, cp) and
      0 < native.observedMonoTime <= now_ns <= native.validUntilMonoTime and
      native.validUntilMonoTime - native.observedMonoTime <= SAFETY_MAX_AGE_NS)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError):
    return False
