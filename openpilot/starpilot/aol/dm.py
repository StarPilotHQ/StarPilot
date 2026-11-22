def armed_lateral_current(cp, cs, intent, native, *, source_ns, now_ns, axis_session_id) -> bool:
  """Retain only a current deliberately armed native-permitted lateral owner."""
  from opendbc.car.structs import car
  from openpilot.starpilot.aol.vehicle import policy_for
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS, native_inventory_matches_cp
  try:
    return bool(policy_for(cp).intent_supported and
      cs.canValid and not cs.canTimeout and not cs.accFaulted and not cs.doorOpen and
      not cs.steerFaultTemporary and not cs.steerFaultPermanent and cs.cruiseState.available and
      (cs.gearShifter in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low,
                         car.CarState.GearShifter.sport, car.CarState.GearShifter.brake) or
       cp.carFingerprint == 'SUBARU_LEGACY_2025' and cs.gearShifter == car.CarState.GearShifter.manumatic) and
      intent is not None and intent.settingsQualified and intent.allowedLatch and intent.lateralArmed and
      intent.producerSessionId and intent.carStateLogMonoTime == source_ns and
      0 < source_ns <= intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime and
      now_ns - source_ns <= AXIS_MAX_AGE_NS and
      intent.validUntilMonoTime - intent.observedMonoTime <= SAFETY_MAX_AGE_NS and
      native is not None and native.compatible and int(native.protocolVersion) == 1 and
      axis_session_id and native.axisSessionId == axis_session_id and
      native.requestedLateral and native.lateralAllowed and native.pandaInventory and
      native_inventory_matches_cp(native, cp) and
      0 < native.observedMonoTime <= now_ns <= native.validUntilMonoTime and
      native.validUntilMonoTime - native.observedMonoTime <= SAFETY_MAX_AGE_NS)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError):
    return False


def lateral_event_context(self, CS, intent, native, now_ns, ordinary_lag):
  from openpilot.selfdrive.selfdrived.events import EventName
  from openpilot.cereal import log
  dm = self.sm['driverMonitoringState']
  needs_exception = bool(dm.alertLevel == log.DriverMonitoringState.AlertLevel.three or
    dm.lockout or dm.alwaysOnLockout or EventName.tooDistracted in self.events.names)
  retained_dm_lateral = bool(needs_exception and intent is not None and
    getattr(self, 'aol_dm_retained_owner', None) == (intent.producerSessionId, self.aol_session_id) and
    self.sm.all_checks(['driverMonitoringState']) and
    armed_lateral_current(self.CP, CS, intent, native, source_ns=self.aol_car_state_log_ns,
                          now_ns=now_ns, axis_session_id=self.aol_session_id))
  lateral_events = [name for name in self.events.names if not (
    name == EventName.selfdrivedLagging and ordinary_lag or
    name == EventName.tooDistracted and retained_dm_lateral)]
  return retained_dm_lateral, lateral_events


def observe_owner(self, intent, previous_intent):
  from openpilot.cereal import log
  AlertLevel = log.DriverMonitoringState.AlertLevel
  if (intent is None or not intent.allowedLatch or previous_intent is None or
      intent.producerSessionId != previous_intent.producerSessionId):
    self.aol_dm_retained_owner = None
  elif self.aol_axis_decision.lateral_active and previous_intent.allowedLatch:
    self.aol_dm_retained_owner = (intent.producerSessionId, self.aol_session_id)
  if (self.sm['driverMonitoringState'].alertLevel == AlertLevel.three or
      self.sm['driverMonitoringState'].lockout or self.sm['driverMonitoringState'].alwaysOnLockout):
    self.aol_availability_long_inhibit = True
    self.aol_dm_lateral_inhibit = False
  elif intent is not None and not intent.allowedLatch:
    self.aol_dm_lateral_inhibit = False


def dm_clear(self):
  from openpilot.cereal import log
  return (self.sm['driverMonitoringState'].alertLevel != log.DriverMonitoringState.AlertLevel.three and
          not self.sm['driverMonitoringState'].lockout and not self.sm['driverMonitoringState'].alwaysOnLockout)
