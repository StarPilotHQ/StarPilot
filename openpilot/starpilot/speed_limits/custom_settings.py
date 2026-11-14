"""SLC limit-change settings, saved in meters, seconds and m/s."""

from dataclasses import dataclass, replace
import math

# When to start changing speed for an upcoming mapped limit. Saved as the index into these tuples.
HIGHER_MODES = ('limit', 'distance', 'time', 'speed')  # at the sign, meters before, seconds before, speed over at the sign
LOWER_MODES = ('limit', 'distance', 'time', 'reach')  # at the sign, meters before, seconds before, reach the limit by
OFFSET_TYPES = ('fixed', 'percentage')  # fixed: one offset per speed range; percentage: one share of every limit
MANUAL_MEMORY = ('forget', 'drive')  # a manual set speed: forgotten at the next limit, or remembered per limit for the drive
MODES = {
  'SLCHigherLimitMode': (HIGHER_MODES, 0),
  'SLCLowerLimitMode': (LOWER_MODES, 3),
  'SLCOffsetType': (OFFSET_TYPES, 0),
  'SLCManualSpeedMemory': (MANUAL_MEMORY, 0),
}

SPECS = {
  'SLCFallbackDelay': (0.0, 10.0, 5.0),  # seconds the last limit is held before the secondary source is used
  'SLCOffsetPercent': (-20.0, 20.0, 0.0),  # percent of the limit
  'SLCHigherLimitDistance': (0.0, 500.0, 50.0),
  'SLCHigherLimitTime': (0.0, 10.0, 3.0),
  'SLCHigherLimitOver': (0.0, 7.0, 2.2352),  # m/s; 5 mph
  'SLCHigherWarningLeadTime': (0.0, 10.0, 2.0),
  'SLCLowerLimitDistance': (0.0, 500.0, 50.0),
  'SLCLowerLimitTime': (0.0, 10.0, 3.0),
  'SLCLowerLimitTiming': (-5.0, 5.0, 0.0),  # Reach by: seconds before the sign
  'SLCLowerWarningLeadTime': (0.0, 10.0, 2.0),
}
BOOLS = ('SLCLimitAcceleration', 'SLCLimitBraking', 'SLCReengageSpeed', 'SLCReengageKeepFaster', 'SLCPedalSetsSpeed', 'SLCSetsSetSpeed')
# Saved key -> CustomSettings field.
FIELDS = {
  'SLCFallbackDelay': 'fallback_delay',
  'SLCManualSpeedMemory': 'manual_memory',
  'SLCReengageKeepFaster': 'keep_faster',
  'SLCPedalSetsSpeed': 'pedal_sets_speed',
  'SLCOffsetType': 'offset_type',
  'SLCOffsetPercent': 'offset_percent',
  'SLCHigherLimitMode': 'higher_mode',
  'SLCHigherLimitDistance': 'higher_distance',
  'SLCHigherLimitTime': 'higher_time',
  'SLCHigherLimitOver': 'higher_over',
  'SLCHigherWarningLeadTime': 'higher_warning_lead',
  'SLCLowerLimitMode': 'lower_mode',
  'SLCLowerLimitDistance': 'lower_distance',
  'SLCLowerLimitTime': 'lower_time',
  'SLCLowerLimitTiming': 'lower_timing',
  'SLCLowerWarningLeadTime': 'lower_warning_lead',
  'SLCLimitAcceleration': 'limit_acceleration',
  'SLCLimitBraking': 'limit_braking',
  'SLCReengageSpeed': 'reengage',
  'SLCSetsSetSpeed': 'sets_set_speed',
}


@dataclass(frozen=True)
class CustomSettings:
  fallback_delay: float = 5.0
  manual_memory: str = 'forget'
  keep_faster: bool = False
  pedal_sets_speed: bool = False
  offset_type: str = 'fixed'
  offset_percent: float = 0.0
  higher_mode: str = 'limit'
  higher_distance: float = 50.0
  higher_time: float = 3.0
  higher_over: float = 2.2352
  higher_warning_lead: float = 2.0
  lower_mode: str = 'reach'
  lower_distance: float = 50.0
  lower_time: float = 3.0
  lower_timing: float = 0.0
  lower_warning_lead: float = 2.0
  # Cap the speed changes SLC makes with each personality's SLC acceleration / braking limit.
  limit_acceleration: bool = False
  limit_braking: bool = False
  reengage: bool = False
  # SLC writes the set speed instead of capping beneath it (cars where StarPilot owns the set speed; cruise.slc_sets_set_speed).
  sets_set_speed: bool = False

  def warning_lead(self, lower: bool) -> float:
    return self.lower_warning_lead if lower else self.higher_warning_lead

  @classmethod
  def parse(cls, values):
    fields = {}
    for key, (names, default) in MODES.items():
      value = values.get(key, default)
      if type(value) is not int or not 0 <= value < len(names):
        raise ValueError('Invalid saved ' + key)
      fields[FIELDS[key]] = names[value]
    for key, (low, high, default) in SPECS.items():
      value = values.get(key, default)
      if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid saved ' + key)
      fields[FIELDS[key]] = float(value)
    for key in BOOLS:
      value = values.get(key, False)
      if type(value) is not bool:
        raise ValueError('Invalid custom SLC preference')
      fields[FIELDS[key]] = value
    return cls(**fields)


def adopt_early_first(custom: CustomSettings, higher_seconds: int, lower_seconds: int) -> CustomSettings:
  """A direction with an Adopt limits early time (1-10 s) uses that timer alone: its start mode becomes At the limit,
  so a limit is never brought forward twice."""
  return replace(custom, higher_mode='limit' if higher_seconds else custom.higher_mode, lower_mode='limit' if lower_seconds else custom.lower_mode)
