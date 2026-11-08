"""Saved Sentry motion choices, separate from actual parked sensor arming."""

from collections.abc import Callable
from dataclasses import replace

from openpilot.starpilot.sentry_mode.actions import WriteResult, commit
from openpilot.starpilot.sentry_mode.policy import Settings
from openpilot.starpilot.sentry_mode.status import STATES, RuntimeStatus
from openpilot.starpilot.sentry_mode.preferences import Preferences, encode, read_preferences
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, FeatureSettingsState


RESET = "sentry:reset"
ENABLED = "sentry:enabled"
SENSITIVITY = "sentry:sensitivity"
WARNING = "sentry:warning"


class SentryOwner:
  def __init__(self, params, parked: Callable[[], bool], *, status: RuntimeStatus | None = None):
    self.params, self.parked = params, parked
    self.last_write = WriteResult(False, False)
    self.status = status if status is not None else RuntimeStatus()

  def snapshot(self) -> FeatureSettingsState:
    saved = read_preferences(self.params)
    parked = self.parked()
    allowed = parked and saved.readable
    label, reason = self.status.runtime_snapshot(saved)
    if not parked and (label == "Monitoring motion" or label.startswith("Arming")):
      label, reason = "Not armed", "Waiting for confirmation that the car is parked"
    elif not parked and label == "Waiting for monitor":
      label, reason = STATES["disabled_onroad"]
    status = f"{label}: {reason}."
    monitor = f"{status} Motion events can include camera snapshots. Notification settings are in Cameras & Monitoring."
    rows: list[FeatureRow] = []
    if not saved.readable:
      rows.append(FeatureRow("", "Saved motion settings", "Unavailable",
                             reason="Saved source cannot be read; no changes allowed"))
    elif not saved.valid:
      rows.extend((FeatureRow("", "Saved motion settings", "Invalid",
                              reason="Existing saved bytes remain unchanged until explicit reset"),
                   FeatureRow(RESET, "Reset saved motion settings", "Off and defaults", saved.raw,
                              available=allowed, reason="Replaces invalid motion settings with Off and defaults")))
    else:
      settings = saved.preferences.settings
      reason = "See monitor status above" if allowed else "Turn off the vehicle to change Sentry settings"
      rows.extend((FeatureRow(ENABLED, "Use parked motion monitoring", "On" if saved.preferences.enabled else "Off",
                              saved.raw, ("Off", "On"), available=allowed, reason=reason),
                   FeatureRow(SENSITIVITY, "Motion sensitivity", f"{settings.sensitivity:.3f}", saved.raw,
                              step=0.001, minimum=0.005, maximum=1.0, available=allowed,
                              reason="Lower values detect smaller acceleration changes" if allowed else reason),
                   FeatureRow(WARNING, "Warning persistence", f"{settings.warning_time_seconds:.1f}", saved.raw,
                              step=0.1, minimum=0.1, maximum=10.0, unit="s", available=allowed,
                              reason="Controls how much motion is needed before a warning" if allowed else reason)))
    initial = Preferences()
    defaults = {ENABLED: 'Off', SENSITIVITY: f'{initial.settings.sensitivity:.3f}', WARNING: f'{initial.settings.warning_time_seconds:.1f}'}
    rows = [replace(row, default_value=defaults[row.key]) if row.key in defaults else row for row in rows]
    return FeatureSettingsState(page="sentry", title="Sentry motion settings",
                                subtitle=monitor, subtitle_accent=status,
                                subtitle_tone="ok" if label == "Monitoring motion" else "warn",
                                rows=tuple(rows), parked=parked)

  def apply(self, request: FeatureSettingsRequest) -> bool:
    self.last_write = WriteResult(False, False)
    if not request.confirmation or not self.parked():
      return False
    saved = read_preferences(self.params)
    if not saved.readable or saved.raw != request.expected:
      return False
    if request.key == RESET:
      if saved.valid or saved.raw is None or request.value != "confirm":
        return False
      target = Preferences()
    else:
      if not saved.valid:
        return False
      preferences = saved.preferences
      if request.key == ENABLED:
        if request.value not in ("On", "Off"):
          return False
        target = replace(preferences, enabled=request.value == "On")
      elif request.key in (SENSITIVITY, WARNING):
        try:
          number = float(request.value)
          settings = (Settings(number, preferences.settings.warning_time_seconds) if request.key == SENSITIVITY else
                      Settings(preferences.settings.sensitivity, number))
        except (ValueError, TypeError, OverflowError):
          return False
        target = replace(preferences, settings=settings)
      else:
        return False
    self.last_write = commit(self.params, encode(target), saved.raw, self.parked)
    return self.last_write.verified
