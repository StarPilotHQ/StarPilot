"""Parked software-cruise preference for Dom's Impreza 2020 port."""

from dataclasses import replace

from opendbc.car.subaru.software_cruise import eligible
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.vehicle_selection import read_selection

KEY = 'SubaruRedneckCruise'


def capability(owner):
  cp = owner.vehicle_params()
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
    temp_prefix='.subaru-cruise-',
  ).verified


def rows(owner, parked):
  identity = capability(owner)
  if identity is None:
    return ()
  row = owner._bool_row(KEY, "Software Cruise Control", parked and owner.authority("parked_preferences"),
                        "Adjust stock cruise speed using Resume and Set. Applies after the next startup; factory ACC keeps pedal control.")
  return (replace(row, available=row.available and owner._readable(KEY), capability=identity),)
