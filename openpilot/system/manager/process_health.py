"""Driving health requirements for manager-owned services."""

# Projection and local phone access are optional even when requested.
# Their lifecycles remain visible in managerState and their own status.
# A dead Jetlink owner sends the drive back to the small model.
ANCILLARY_PROCESSES = frozenset({'starportd', 'galaxy_hotspot', 'jetlinkd', 'enginesoundd'})


def driving_process_failures(processes) -> set[str]:
  return {process.name for process in processes
          if process.shouldBeRunning and not process.running and process.name not in ANCILLARY_PROCESSES}
