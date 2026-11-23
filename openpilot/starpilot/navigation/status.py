import time

from openpilot.starpilot.parked_evidence import RESUME_SKEW_NS

from openpilot.starpilot.navigation.wire import navigation_state

from openpilot.starpilot.gps.source import GPS_SERVICES, GPS_SOURCES, SOURCE_NAMES, SOURCE_SERVICES, bearing, gps_mode, gps_priority, max_age_ns, observation


def boot_time_ns():
  return time.clock_gettime_ns(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))


class NavigationStatusSource:
  def __init__(self, *, mono_clock=time.monotonic_ns, boot_clock=boot_time_ns):
    from openpilot.cereal import messaging
    self.sm = messaging.SubMaster(list(dict.fromkeys(['starpilotNavigation', *GPS_SERVICES, 'deviceState', 'carState', 'carControl', 'modelV2', 'laneChangeAssistWire'])))
    self.mono_clock, self.boot_clock = mono_clock, boot_clock
    self.gps_after_mono_ns = mono_clock()
    self.gps_offset_ns = None

  def snapshot(self) -> dict | None:
    if self.sm is None:
      return None
    self.sm.update(0)
    stamp = self.sm.logMonoTime['starpilotNavigation']
    if not self.sm.valid['starpilotNavigation'] or not 0 < stamp <= time.monotonic_ns() <= stamp + 3_000_000_000:
      return None
    state = navigation_state(self.sm['starpilotNavigation'])
    if state is None:
      return None
    note = self.turn_note()
    return {'turnNote': note, 'revision': state.revision, 'status': state.status,
                'instruction': state.instruction.to_dict() if state.status in ('guiding', 'arrived') else None,
                'route': [row.to_dict() for row in state.route]}

  def turn_note(self):
    from openpilot.starpilot.navigation.intent import current_instruction, matching_turn_signal
    from openpilot.starpilot.lateral.lane_change_status_wire import Direction, Phase, decode, fresh_for_model
    sm, now = self.sm, self.mono_clock()
    if current_instruction(sm, now) is None:
      return None
    cs, cc = sm['carState'], sm['carControl']
    if not cc.latActive or cs.standstill or cs.leftBlinker == cs.rightBlinker:
      return None
    near_turn = matching_turn_signal(sm, now, supported=True)
    if not near_turn:
      service = 'laneChangeAssistWire'
      if not all(sm.valid[name] and sm.alive[name] for name in (service, 'modelV2')):
        return None
      status = decode(sm[service])
      if (status and status.phase == Phase.LANE_UNAVAILABLE and
          status.direction == (Direction.LEFT if cs.leftBlinker else Direction.RIGHT) and
          fresh_for_model(status, sm['modelV2'], now, int(sm.recv_time[service] * 1e9),
                          message_mono_ns=sm.logMonoTime[service])):
        return "steer manually when safe. the adjacent lane is unconfirmed."
      return None
    if (cs.leftBlinker and cs.leftBlindspot) or (cs.rightBlinker and cs.rightBlindspot):
      return "steer manually when safe. a vehicle is in the blind spot."
    if cs.vEgo >= 14:
      return "steer manually. speed is above the turn guidance range."
    if not sm.valid['modelV2'] or not sm.alive['modelV2'] or not 0 <= now - sm.logMonoTime['modelV2'] <= 150_000_000:
      return "steer manually. the turn path is unavailable."
    model = sm['modelV2']
    if str(model.meta.laneChangeState) in ('laneChangeStarting', 'laneChangeFinishing'):
      return None
    direction = -1 if cs.leftBlinker else 1
    if not model.action.desiredCurvature * direction > 0:
      return "steer manually. the planned path has not turned in the signaled direction."
    return None

  def search_position(self) -> tuple[float, float] | None:
    position = self.map_position()
    return (position['longitude'], position['latitude']) if position is not None else None

  def network_status(self) -> str:
    """The comma's connection, independent of the browser's network or downloaded maps."""
    if self.sm is None:
      return 'unknown'
    self.sm.update(0)
    try:
      stamp, now = self.sm.logMonoTime['deviceState'], self.mono_clock()
      if not self.sm.valid['deviceState'] or not 0 < stamp <= now <= stamp + 5_000_000_000:
        return 'unknown'
      kind = str(self.sm['deviceState'].networkType)
      return 'offline' if kind == 'none' else 'unknown'
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
      return 'unknown'

  def map_position(self) -> dict | None:
    """Optional search bias; no last-known or route/control authority."""
    if self.sm is None:
      return None
    before, boot, after = self.mono_clock(), self.boot_clock(), self.mono_clock()
    offset = boot - (before + after) // 2
    if (after < before or after - before > RESUME_SKEW_NS or
        (self.gps_offset_ns is not None and abs(offset - self.gps_offset_ns) > RESUME_SKEW_NS)):
      self.gps_after_mono_ns = max(self.gps_after_mono_ns, after)
      self.gps_offset_ns = offset
      return None
    self.gps_offset_ns = offset
    self.sm.update(0)
    before, boot, after = self.mono_clock(), self.boot_clock(), self.mono_clock()
    offset = boot - (before + after) // 2
    if (after < before or after - before > RESUME_SKEW_NS or
        abs(offset - self.gps_offset_ns) > RESUME_SKEW_NS):
      self.gps_after_mono_ns = max(self.gps_after_mono_ns, after)
      self.gps_offset_ns = offset
      return None
    candidates = []
    mode = gps_mode()
    for service in GPS_SOURCES:
      try:
        priority = gps_priority(service, mode)
        fix = None if priority is None else observation(self.sm, service, after)
        if fix is None:
          continue
        stamp, gps = fix
        carrier, ttl = SOURCE_SERVICES.get(service, service), max_age_ns(service)
        receipt = int(self.sm.recv_time[carrier] * 1e9)
        if (self.sm.seen[carrier] and self.sm.alive[carrier] and self.gps_after_mono_ns < stamp and
            0 <= after - receipt <= ttl):
          direction = bearing(gps)
          candidates.append((priority, stamp,
                             {'longitude': float(gps.longitude), 'latitude': float(gps.latitude), 'source': SOURCE_NAMES[service],
                              **({'bearing': direction} if direction is not None else {}),
                              # Galaxy leases map markers for at most 2.5 seconds, even when
                              # a background phone fix has a longer source timeout.
                              'validForMs': min(2500., (stamp + ttl - after) / 1e6, (receipt + ttl - after) / 1e6)}))
      except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
        continue
    return max(candidates, default=(-1, 0, None), key=lambda row: row[:2])[2]

  def close(self):
    for socket in getattr(self.sm, 'sock', {}).values():
      close = getattr(socket, 'close', None)
      if close is not None:
        close()
    self.sm = None
