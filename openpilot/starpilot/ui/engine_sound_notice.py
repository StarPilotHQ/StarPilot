"""Short onroad notice after a wheel button toggles or changes the engine sound."""

from openpilot.starpilot.engine_sound.profiles import PROFILES
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert

NOTICE_NS = 1_500_000_000
ALERT_TYPE = "engineSoundChanged/warning"


def notice_text(toggled: bool, enabled: bool, profile: str) -> tuple[str, str]:
  label = PROFILES[profile].label if profile in PROFILES else profile
  if toggled:
    return ("Engine Sounds On", label) if enabled else ("Engine Sounds Off", "")
  return label, "Engine Sound" if enabled else "Engine Sounds Off"


class EngineSoundNotice:
  def __init__(self) -> None:
    self._until_ns = 0
    self._text = ("", "")

  def show(self, toggled: bool, enabled: bool, profile: str, now_ns: int) -> None:
    self._text = notice_text(toggled, enabled, profile)
    self._until_ns = now_ns + NOTICE_NS

  def update(self, native_alert: OnroadAlert, now_ns: int) -> OnroadAlert:
    if native_alert.size != AlertSize.NONE or not 0 < self._until_ns - now_ns <= NOTICE_NS:
      return native_alert
    return OnroadAlert(AlertSize.MID if self._text[1] else AlertSize.SMALL, self._text[0], self._text[1], alert_type=ALERT_TYPE)
