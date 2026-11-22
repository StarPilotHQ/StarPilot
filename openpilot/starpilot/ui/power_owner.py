"""Parked, source-bound edits to the optional offroad power policy."""

from collections.abc import Callable
from dataclasses import replace

from openpilot.starpilot.power.offroad_preferences import KEY, PowerPolicy, SavedPower, decode, read_saved, to_value
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, FeatureSettingsState, row_change


ENABLED = "power:enabled"
HOURS = "power:hours"
VOLTS = "power:volts"
POWER_DESCRIPTION = ("Stock uses the standard shutdown policy. Custom limits use the maximum parked time and voltage cutoff below; " +
                     "other protections can shut down earlier.")
POWER_KEYS = frozenset((ENABLED, HOURS, VOLTS))
HOUR_CHOICES = tuple(f"{hour} h" for hour in range(1, 31))
VOLT_CHOICES = tuple(f"{tenths / 10:.1f} V" for tenths in range(118, 126))


def power_row_change(row: FeatureRow, direction: int = 1) -> FeatureSettingsRequest | None:
  if row.key in (HOURS, VOLTS) and row.value in row.choices:
    next_index = row.choices.index(row.value) + direction
    if next_index < 0 or next_index >= len(row.choices):
      return None  # a boundary press must never wrap 30 hours to 1 hour
  return row_change(row, direction)


def confirm_question(request: FeatureSettingsRequest) -> str:
  if request.key == ENABLED:
    if request.value == "Custom limits":
      policy = decode(request.expected) if request.expected is not None else PowerPolicy()
      if policy is None:
        return "Review saved parked-power limits before enabling."
      return (f"Use a {policy.delay_hours}-hour maximum and {policy.cutoff_tenths / 10:.1f}-V cutoff while parked? " +
              "Other protections can shut down earlier.")
    return "Return to stock parked-power limits? Older saved choices remain untouched."
  if request.key == HOURS:
    return f"Save a maximum parked time of {request.value}? Other protections can shut down earlier."
  return f"Save a low-voltage cutoff of {request.value}? The device may shut down while parked."


class PowerOwner:
  def __init__(self, params, parked: Callable[[], bool]):
    self.params = params
    self.parked = parked

  def snapshot(self) -> FeatureSettingsState:
    saved = read_saved(self.params)
    parked = self.parked()
    policy = saved.policy
    reason = ("Saved policy cannot be read" if not saved.readable else
              "Invalid saved policy; choose Stock to repair" if not saved.valid else
              "Stock limits apply until a custom policy is saved" if saved.raw is None else "")
    inactive = "Saved; stock power policy applies" if saved.valid and not policy.enabled else ""
    rows = (FeatureRow(ENABLED, "Parked Power", "Invalid" if not saved.valid else "Custom limits" if policy.enabled else "Stock",
                       source=saved.raw, choices=("Stock", "Custom limits"), available=parked and saved.readable,
                       repair_value="Stock" if not saved.valid else "", reason=f"{POWER_DESCRIPTION} {reason}".strip()),
            FeatureRow(HOURS, "Maximum Parked Time", f"{policy.delay_hours} h" if saved.valid else "Invalid",
                       source=saved.raw, choices=HOUR_CHOICES, available=parked and saved.readable and saved.valid,
                       reason=inactive or "Other protections may act sooner"),
            FeatureRow(VOLTS, "Low-Voltage Cutoff", f"{policy.cutoff_tenths / 10:.1f} V" if saved.valid else "Invalid",
                       source=saved.raw, choices=VOLT_CHOICES, available=parked and saved.readable and saved.valid,
                       reason=inactive or "Uses filtered car voltage"))
    initial = PowerPolicy()
    defaults = {ENABLED: 'Stock', HOURS: f'{initial.delay_hours} h', VOLTS: f'{initial.cutoff_tenths / 10:.1f} V'}
    rows = tuple(replace(row, default_value=defaults[row.key]) if row.key in defaults else row for row in rows)
    return FeatureSettingsState(page="power", title="Parked Power",
                                subtitle="Saved limits for automatic offroad shutdown.", rows=rows, parked=parked)

  @staticmethod
  def _desired(saved: SavedPower, request: FeatureSettingsRequest) -> PowerPolicy | None:
    policy = saved.policy
    if request.key == ENABLED:
      if request.value not in ("Stock", "Custom limits"):
        return None
      if not saved.valid and request.value != "Stock":
        return None
      return replace(policy, enabled=request.value == "Custom limits")
    if not saved.valid:
      return None
    if request.key == HOURS and request.value in HOUR_CHOICES:
      return replace(policy, delay_hours=int(request.value[:-2]))
    if request.key == VOLTS and request.value in VOLT_CHOICES:
      return replace(policy, cutoff_tenths=int(round(float(request.value[:-2]) * 10)))
    return None

  def apply(self, request: FeatureSettingsRequest) -> bool:
    if request.key not in POWER_KEYS or not request.confirmation:
      return False
    desired: PowerPolicy | None = None
    for _ in range(2):
      if not self.parked():
        return False
      saved = read_saved(self.params)
      if not saved.readable or saved.raw != request.expected:
        return False
      desired = self._desired(saved, request)
      if desired is None:
        return False
    assert desired is not None
    if not self.parked():
      return False
    try:
      self.params.put(KEY, to_value(desired), block=True)
    except (OSError, KeyError, TypeError, ValueError):
      return False
    observed = read_saved(self.params)
    return observed.readable and observed.valid and observed.policy == desired
