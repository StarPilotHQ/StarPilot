"""Tesla fused road-limit evidence for the 7.0 Dashboard producer."""
import math
from opendbc.car.dashboard_speed_limit import Status, parser_expiry


def update_dashboard_limit(tracker, party, autopilot, speed_units):
  limit_ns, limit_expiry = parser_expiry(autopilot, 'DAS_status', 'DAS_fusedSpeedLimit')
  units_ns, units_expiry = parser_expiry(party, 'DI_state', 'DI_speedUnits')
  stamp = max(limit_ns, units_ns)
  if stamp <= tracker.observation.observed_ns:
    return
  status, speed = Status.UNKNOWN, 0.0
  expiry = min(limit_expiry, units_expiry)
  if (limit_ns > 0 and units_ns > 0 and expiry > stamp and party.can_valid and autopilot.can_valid
      and speed_units in ('MPH', 'KPH')):
    value = float(autopilot.vl['DAS_status']['DAS_fusedSpeedLimit'])
    # The DBC scales raw31/NONE to155; raw0 is UNKNOWN, never zero mph.
    if math.isfinite(value) and 0 < value <= 150:
      status, speed = Status.VALID, value * (0.44704 if speed_units == 'MPH' else 1 / 3.6)
    elif value == 155:
      status = Status.ABSENT
  tracker.update(stamp, status, speed, valid_until_ns=expiry)
