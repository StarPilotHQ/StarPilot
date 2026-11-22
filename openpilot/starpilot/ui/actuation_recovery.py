"""Explicit recovery from a prior-drive actuation alert; never an onroad bypass."""

KEY = "Offroad_ExcessiveActuation"


def acknowledge(params, physical, expected):
  # Recheck at confirmation time. A dialog opened while parked may outlive that state.
  if expected is None or params.get(KEY) != expected:
    return False
  if not params.get_bool("IsOffroad") or not physical.status(require_ignition_off=True)["allowed"]:
    return False
  if physical.effective() is not False:
    return False
  if not params.get_bool("IsOffroad") or params.get(KEY) != expected:
    return False
  params.remove(KEY)
  return True
