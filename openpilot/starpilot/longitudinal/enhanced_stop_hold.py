"""Next-drive manual release preference for acquired Enhanced Stop holds."""

from openpilot.starpilot.saved_source import read_saved

KEY = "AlwaysHoldEnhancedStops"
LABEL = "Always Hold Enhanced Stops"
DESCRIPTION = "Keep Enhanced Stops held until you manually resume, even if a traffic light appears to turn green."


def selected(params) -> bool:
  raw, readable = read_saved(params, KEY, 8)
  return readable and raw == b"1"
