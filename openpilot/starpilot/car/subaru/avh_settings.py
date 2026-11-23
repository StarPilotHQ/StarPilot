"""Parked software-cruise preference for the Subaru AVH startup request."""

from opendbc.car.subaru.avh import eligible
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.vehicle_selection import read_selection

KEY = 'SubaruAvhStartup'


def capability(owner):
  cp = owner.vehicle_params()
  from openpilot.starpilot.saved_source import read_saved
  if read_saved(owner.params, "IsReleaseBranch", 8) not in ((None, True), (b"0", True)):
    return None
  selection = read_selection(owner.params)
  return (str(cp.carFingerprint), str(cp.brand), selection.raw) if eligible(cp) and selection.readable and selection.valid else None


def apply(owner, request):
  if not request.confirmation or request.value not in ('Off', 'On') or request.dependencies:
    return False

  def authorized():
    return bool(
      owner.authority('parked_preferences')
      and request.capability is not None
      and request.capability == capability(owner)
      and request.vehicle_fingerprint == owner.vehicle_fingerprint()
      and request.expected in (None, b'0', b'1')
    )

  return commit_exact(
    owner.params,
    key=KEY,
    max_bytes=8,
    expected=request.expected,
    raw=b'1' if request.value == 'On' else b'0',
    authorized=authorized,
    temp_prefix='.subaru-startup-',
  ).verified
