"""Galaxy-only, next-drive developer preferences."""

from dataclasses import replace

from openpilot.starpilot.longitudinal.voacc import KEY, DESCRIPTION
from openpilot.starpilot.ui.calibration_reset import calibration_reset
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.system.starport import head_unit_fixes, new_setup
from openpilot.starpilot.ui.feature_settings_state import HEADING_PREFIX, FeatureRow, FeatureSettingsState


STARPORT_WIFI_KEY = "StarPortDedicatedWifi"
STARPORT_WIFI_DESCRIPTION = ("Experimental. Join the car's Wi-Fi on a second interface so the comma stays on home Wi-Fi or your " +
                       "phone hotspot during wireless StarPort. Applies from the next StarPort connection.")
CALIBRATION_RESET = "reset_calibration"
CALIBRATION_RESET_DESCRIPTION = "Clear camera and steering calibration. The device recalibrates on the next drive."
STARPORT_HEADING_KEY = HEADING_PREFIX + "starport"
STARPORT_NEW_SETUP_KEY = "StarPortNewSetup"
STARPORT_NEW_SETUP_DESCRIPTION = ("Guided StarPort setup with a live checklist of what is being tried. Turn off to use the classic " +
                                  "setup screens. Applies the next time the setup screen opens.")
STARPORT_HEVC_KEY = "StarPortAllowHevc"
STARPORT_HEVC_DESCRIPTION = ("Experimental. Only for a car that offers H.265 and no H.264 video. Cars that offer H.264 are not " +
                             "affected. Applies from the next StarPort connection.")
STARPORT_FIXES_KEY = "StarPortHeadUnitFixes"
STARPORT_FIXES_DESCRIPTION = ("Lets StarPort adjust how it talks to head units it has learned about. Turn off to use the standard " +
                              "connection for every car. Applies from the next StarPort connection.")
STARPORT_EXPERIMENTS_KEY = "StarPortHeadUnitExperiments"
STARPORT_EXPERIMENTS_DESCRIPTION = ("Shows experimental fixes for specific cars and dongles. Each one still needs its own switch, and the " +
                                    "car-specific fixes setting must be on. Applies from the next StarPort connection.")
STARPORT_EXPERIMENT_PREFIX = "StarPortExperiment:"
DRIVER_MONITORING_RESET = "reset_driver_monitoring"
DRIVER_MONITORING_RESET_DESCRIPTION = "Clear saved wheel-side detection. Automatic detection restarts on the next drive."


class DeveloperSettingsOwner:
  def __init__(self, params, authorized, *, panda_wake=None, starport=None):
    self.params = params
    self.authorized = authorized
    self.panda_wake = panda_wake
    self.starport = starport  # returns the StarPort daemon client

  def _starport_status(self):
    if self.starport is None:
      return None
    try:
      status = self.starport().call("status")["status"]
      return status if isinstance(status, dict) else None
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
      return None  # the service is off; there is nothing to configure

  def _starport_rows(self, parked):
    """The StarPort section. The switch back to classic setup is always listed; the daemon's settings only while it runs."""
    new_setup_on = new_setup.enabled()
    held = "Park the vehicle to change this setting."
    rows = [FeatureRow(STARPORT_HEADING_KEY, "StarPort", "", available=False),
            FeatureRow(STARPORT_NEW_SETUP_KEY, "New pairing experience", "On" if new_setup_on else "Off",
                       b"1" if new_setup_on else b"0", ("Off", "On"), available=parked,
                       reason=STARPORT_NEW_SETUP_DESCRIPTION if parked else held, default_value="On")]
    status = self._starport_status()
    if status is not None:
      enabled, hevc = bool(status.get("dedicated_wifi_interface")), bool(status.get("allow_hevc"))
      rows.append(FeatureRow(STARPORT_WIFI_KEY, "Stay on Wi-Fi", "On" if enabled else "Off", b"1" if enabled else b"0",
                             ("Off", "On"), available=parked, reason=STARPORT_WIFI_DESCRIPTION if parked else held, default_value="On"))
      rows.append(FeatureRow(STARPORT_HEVC_KEY, "Allow H.265 video", "On" if hevc else "Off", b"1" if hevc else b"0",
                             ("Off", "On"), available=parked, reason=STARPORT_HEVC_DESCRIPTION if parked else held, default_value="Off"))
      fixes, experiments = bool(status.get("head_unit_fixes", True)), bool(status.get("head_unit_experiments"))
      chosen = set(status.get("head_unit_experiment_rules") or ())
      rows.append(FeatureRow(STARPORT_FIXES_KEY, "Car-specific fixes", "On" if fixes else "Off", b"1" if fixes else b"0",
                             ("Off", "On"), available=parked, reason=STARPORT_FIXES_DESCRIPTION if parked else held, default_value="On"))
      rows.append(FeatureRow(STARPORT_EXPERIMENTS_KEY, "Try experimental fixes", "On" if experiments else "Off",
                             b"1" if experiments else b"0", ("Off", "On"), available=parked,
                             reason=STARPORT_EXPERIMENTS_DESCRIPTION if parked else held, default_value="Off"))
      for entry in head_unit_fixes.experimental_entries() if experiments else ():
        on = entry["id"] in chosen
        rows.append(FeatureRow(STARPORT_EXPERIMENT_PREFIX + entry["id"], entry["label"], "On" if on else "Off", b"1" if on else b"0",
                               ("Off", "On"), available=parked, default_value="Off",
                               reason=f"Experimental. {entry['evidence']} Applies from the next StarPort connection." if parked else held))
    return tuple(rows)

  def _driver_monitoring_reset_allowed(self):
    return self.authorized() and not self.params.get_bool("IsDriverViewEnabled")

  def _calibration_reset_allowed(self):
    return self.authorized() and not calibration_reset.pending and not self.params.get_bool("OnroadCycleRequested")

  def snapshot(self):
    raw, readable = read_saved(self.params, KEY, 8)
    valid = readable and raw in (None, b"0", b"1")
    parked = self.authorized()
    row = FeatureRow(KEY, "Use VOACC", ("On" if raw == b"1" else "Off") if valid else "Invalid saved value",
                     raw, ("Off", "On") if valid else (), available=parked and valid,
                     reason=(DESCRIPTION if parked else "Park the vehicle to change this setting.") if valid else
                            "Invalid saved value", default_value="Off")
    rhd_raw, rhd_readable = read_saved(self.params, "IsRhdDetected", 8)
    reset_available = self._driver_monitoring_reset_allowed() and rhd_readable
    reset_reason = (DRIVER_MONITORING_RESET_DESCRIPTION if reset_available else
                    "Park the vehicle and close driver camera preview before resetting driver monitoring.")
    reset_row = FeatureRow(DRIVER_MONITORING_RESET, "Reset Driver Monitoring", "Ready" if reset_available else "Unavailable",
                           rhd_raw if rhd_readable else None, available=reset_available, reason=reset_reason)
    calibration_raw, calibration_readable = read_saved(self.params, "CalibrationParams", 1024 * 1024)
    calibration_available = self._calibration_reset_allowed() and calibration_readable
    calibration_row = FeatureRow(CALIBRATION_RESET, "Reset Calibration", "Ready" if calibration_available else "Unavailable",
                                 calibration_raw if calibration_readable else None, available=calibration_available,
                                 reason=CALIBRATION_RESET_DESCRIPTION if calibration_available else
                                        "Park the vehicle and wait for any pending reset to finish.")
    firmware_rows = (() if self.panda_wake is None else
                     tuple(replace(item, vehicle_fingerprint=item.capability[0])
                           for item in self.panda_wake.rows(parked) if item.key == "IgnoreIgnitionLine"))
    starport_rows = self._starport_rows(parked)
    summary = "Changes apply next drive. Panda firmware changes require a device restart." if firmware_rows else "Changes apply next drive."
    return FeatureSettingsState("developer", "Developer driving options", summary,
                                (row, reset_row, calibration_row, *firmware_rows, *starport_rows), parked)

  def apply(self, request):
    if request.key == CALIBRATION_RESET:
      if request.value != "confirm" or not self._calibration_reset_allowed():
        return False
      current, readable = read_saved(self.params, "CalibrationParams", 1024 * 1024)
      if not readable or current != request.expected:
        return False
      return calibration_reset.request(self.params)

    if request.key == DRIVER_MONITORING_RESET:
      if request.value != "confirm" or not self._driver_monitoring_reset_allowed():
        return False
      current, readable = read_saved(self.params, "IsRhdDetected", 8)
      if not readable or current != request.expected:
        return False
      try:
        self.params.remove("IsRhdDetected")
        self.params.put_bool("OnroadCycleRequested", True, block=True)
      except (OSError, RuntimeError, TypeError, ValueError):
        return False
      return True
    if request.key == "IgnoreIgnitionLine":
      return self.panda_wake is not None and self.panda_wake.apply(request)
    if request.key == STARPORT_WIFI_KEY:
      return self._apply_starport(request, "dedicated_wifi_interface", "set_dedicated_wifi")
    if request.key == STARPORT_HEVC_KEY:
      return self._apply_starport(request, "allow_hevc", "set_allow_hevc")
    if request.key == STARPORT_FIXES_KEY:
      return self._apply_starport(request, "head_unit_fixes", "set_head_unit_fixes")
    if request.key == STARPORT_EXPERIMENTS_KEY:
      return self._apply_starport(request, "head_unit_experiments", "set_head_unit_experiments")
    if request.key.startswith(STARPORT_EXPERIMENT_PREFIX):
      return self._apply_experiment_rule(request)
    if request.key == STARPORT_NEW_SETUP_KEY:
      return self._apply_new_setup(request)
    if request.key != KEY or request.value not in ("Off", "On") or request.expected not in (None, b"0", b"1"):
      return False
    result = commit_exact(self.params, key=request.key, max_bytes=8, raw=b"1" if request.value == "On" else b"0",
                          expected=request.expected, authorized=self.authorized, temp_prefix=".voacc-")
    return result.committed and result.verified

  def _apply_starport(self, request, field, command):
    status, client = self._starport_status(), self.starport
    enabled = None if status is None else bool(status.get(field))
    if (enabled is None or client is None or request.value not in ("Off", "On") or request.expected != (b"1" if enabled else b"0") or
        not self.authorized()):
      return False
    try:
      client().call(command, enabled=request.value == "On")
    except (OSError, RuntimeError, ValueError):
      return False
    status = self._starport_status()
    return status is not None and bool(status.get(field)) == (request.value == "On")

  def _apply_experiment_rule(self, request):
    rule_id = request.key.removeprefix(STARPORT_EXPERIMENT_PREFIX)
    status, client = self._starport_status(), self.starport
    if status is None or client is None or rule_id not in head_unit_fixes.experiment_ids() or not self.authorized():
      return False
    on = rule_id in (status.get("head_unit_experiment_rules") or ())
    if request.value not in ("Off", "On") or request.expected != (b"1" if on else b"0"):
      return False
    try:
      client().call("set_head_unit_experiment_rule", rule=rule_id, enabled=request.value == "On")
    except (OSError, RuntimeError, ValueError):
      return False
    status = self._starport_status()
    return status is not None and (rule_id in (status.get("head_unit_experiment_rules") or ())) == (request.value == "On")

  def _apply_new_setup(self, request):
    current = new_setup.enabled()
    if request.value not in ("Off", "On") or request.expected != (b"1" if current else b"0") or not self.authorized():
      return False
    return new_setup.set_enabled(request.value == "On")
