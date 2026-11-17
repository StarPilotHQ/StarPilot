import subprocess
import time
import datetime
from pathlib import Path
from collections.abc import Callable
from openpilot.common.time_helpers import system_time_valid
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.starpilot.software.preferences import AUTOMATIC_DOWNLOADS, automatic_downloads
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr, trn
from openpilot.system.ui.widgets import Widget, DialogResult
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
from openpilot.system.ui.widgets.list_view import button_item, text_item, toggle_item, ListItem
from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
from openpilot.system.ui.widgets.scroller_tici import Scroller

# TODO: remove this. updater fails to respond on startup if time is not correct
UPDATED_TIMEOUT = 10  # seconds to wait for updated to respond

# Mapping updater internal states to translated display strings
STATE_TO_DISPLAY_TEXT = {
  "checking...": tr("checking..."),
  "downloading...": tr("downloading..."),
  "finalizing update...": tr("finalizing update..."),
}


def time_ago(date: datetime.datetime | None) -> str:
  if not date:
    return tr("never")

  if not system_time_valid():
    return date.strftime("%a %b %d %Y")

  now = datetime.datetime.now(datetime.UTC)
  if date.tzinfo is None:
    date = date.replace(tzinfo=datetime.UTC)

  diff_seconds = int((now - date).total_seconds())
  if diff_seconds < 60:
    return tr("now")
  if diff_seconds < 3600:
    m = diff_seconds // 60
    return trn("{} minute ago", "{} minutes ago", m).format(m)
  if diff_seconds < 86400:
    h = diff_seconds // 3600
    return trn("{} hour ago", "{} hours ago", h).format(h)
  if diff_seconds < 604800:
    d = diff_seconds // 86400
    return trn("{} day ago", "{} days ago", d).format(d)
  return date.strftime("%a %b %d %Y")


class SoftwareLayout(Widget):
  def __init__(self):
    super().__init__()

    self._onroad_label = ListItem(lambda: tr("Updates are only downloaded while the car is off."))
    self._version_item = text_item(lambda: tr("Current Version"), ui_state.params.get("UpdaterCurrentDescription") or "")
    self._auto_updates_toggle = toggle_item(
      lambda: tr("Automatically Download Updates"),
      lambda: tr("Automatically download updates while offroad. Manual checks and downloads remain available."),
      initial_state=automatic_downloads(ui_state.params) is True, callback=self._on_auto_updates_toggle,
      enabled=ui_state.is_offroad,
    )
    from openpilot.starpilot.ui.software_update import NativeSoftwareUpdate
    self._direct_update = NativeSoftwareUpdate(ui_state.is_offroad)
    self._fast_btn = button_item(lambda: tr("Fast Update"), lambda: tr("UPDATE"), callback=lambda: self._confirm_direct_update("fast"))
    self._rollback_btn = button_item(lambda: tr("Previous Version"), lambda: tr("RESTORE"), callback=lambda: self._confirm_direct_update("rollback"))
    self._download_btn = button_item(lambda: tr("Download"), lambda: tr("CHECK"), callback=self._on_download_update)

    # Install button is initially hidden
    self._install_btn = button_item(lambda: tr("Install Update"), lambda: tr("INSTALL"), callback=self._on_install_update)
    self._install_btn.set_visible(False)

    # Track waiting-for-updater transition to avoid brief re-enable while still idle
    self._waiting_for_updater = False
    self._waiting_start_ts: float = 0.0

    # Branch switcher
    self._branch_btn = button_item(lambda: tr("Target Branch"), lambda: tr("SELECT"), callback=self._on_select_branch)
    self._branch_btn.set_visible(not ui_state.params.get_bool("IsTestedBranch"))
    self._branch_btn.action_item.set_value(ui_state.params.get("UpdaterTargetBranch") or "")
    self._branch_dialog: MultiOptionDialog | None = None
    self._uninstall_btn = button_item(lambda: tr("Uninstall"), lambda: tr("UNINSTALL"),
                                      callback=self._on_uninstall, enabled=ui_state.is_offroad)

    self._scroller = self._child(Scroller([
      self._onroad_label,
      self._version_item,
      self._auto_updates_toggle,
      self._install_btn,
      self._download_btn,
      self._fast_btn,
      self._rollback_btn,
      self._branch_btn,
      self._uninstall_btn,
      button_item(lambda: tr("Error Log"), lambda: tr("VIEW"), callback=self._on_error_log),
    ], line_separator=True, spacing=0))

  def _render(self, rect):
    self._scroller.render(rect)

  def _confirm_direct_update(self, action):
    if not self._direct_update.available(action):
      return
    text = (tr("Restore the previous installed version and restart? Automatic downloads will be disabled.")
            if action == "rollback" else tr("Download and install the selected branch, then restart?"))
    gui_app.push_widget(ConfirmDialog(text, tr("RESTORE") if action == "rollback" else tr("UPDATE"),
                                     callback=lambda result: self._direct_update.submit(action) if result == DialogResult.CONFIRM else None))

  def _update_state(self):
    self._direct_update.refresh()
    for action, button in (("fast", self._fast_btn), ("rollback", self._rollback_btn)):
      button.set_visible(ui_state.is_offroad())
      button.action_item.set_enabled(self._direct_update.available(action))
    detail = self._direct_update.detail()
    self._fast_btn.set_description(detail)
    self._fast_btn.action_item.set_value(tr("Use Download for full update") if "use a full validated update" in detail else detail)
    # Show/hide onroad warning
    self._onroad_label.set_visible(ui_state.is_onroad())

    # Update current version and release notes
    current_desc = ui_state.params.get("UpdaterCurrentDescription") or ""
    current_release_notes = (ui_state.params.get("UpdaterCurrentReleaseNotes") or b"").decode("utf-8", "replace")
    self._version_item.action_item.set_text(current_desc)
    self._version_item.set_description(current_release_notes)
    automatic = automatic_downloads(ui_state.params)
    self._auto_updates_toggle.action_item.set_state(automatic is True)
    self._auto_updates_toggle.action_item.set_enabled(ui_state.is_offroad() and automatic is not None)

    # Update download button visibility and state
    self._download_btn.set_visible(ui_state.is_offroad())

    updater_state = ui_state.params.get("UpdaterState") or "idle"
    fast = ui_state.params.get('UpdaterFastState') or {}
    check = fast.get('check', {}) if isinstance(fast, dict) else {}
    checking_metadata = check.get('state') == 'checking'
    if checking_metadata:
      updater_state = 'checking...'
    if check.get('completedAt', 0) >= getattr(self, '_waiting_start_ts', float('inf')) and check.get('state') in ('complete', 'error'):
      self._waiting_for_updater = False
    failed_count = ui_state.params.get("UpdateFailedCount") or 0
    fetch_available = ui_state.params.get_bool("UpdaterFetchAvailable")
    update_available = ui_state.params.get_bool("UpdateAvailable")

    if updater_state != "idle":
      # Updater responded
      self._waiting_for_updater = False
      self._download_btn.action_item.set_enabled(False)
      # Use the mapping, with a fallback to the original state string
      display_text = STATE_TO_DISPLAY_TEXT.get(updater_state, updater_state)
      if checking_metadata:
        display_text = tr('Checking for new versions')
      elif isinstance(fast, dict) and fast.get('detail'):
        display_text = fast['detail']
        if fast.get('background'):
          display_text = tr('Background download') + ': ' + display_text
          self._download_btn.action_item.set_text(tr('CHECK'))
          self._download_btn.action_item.set_enabled(True)
      self._download_btn.action_item.set_value(display_text)
    else:
      if failed_count > 0:
        self._download_btn.action_item.set_value(tr("failed to check for update"))
        self._download_btn.action_item.set_text(tr("CHECK"))
      elif fetch_available:
        self._download_btn.action_item.set_value(tr("update available"))
        self._download_btn.action_item.set_text(tr("DOWNLOAD"))
      else:
        last_update = ui_state.params.get("UpdaterLastCheckTime") or ui_state.params.get("LastUpdateTime")
        if last_update:
          formatted = time_ago(last_update)
          self._download_btn.action_item.set_value(tr("up to date, last checked {}").format(formatted))
        else:
          self._download_btn.action_item.set_value(tr("up to date, last checked never"))
        self._download_btn.action_item.set_text(tr("CHECK"))

      # If we've been waiting too long without a state change, reset state
      if self._waiting_for_updater and (time.monotonic() - self._waiting_start_ts > UPDATED_TIMEOUT):
        self._waiting_for_updater = False

      # Only enable if we're not waiting for updater to flip out of idle
      self._download_btn.action_item.set_enabled(not self._waiting_for_updater)

    # Update target branch button value
    current_branch = ui_state.params.get("UpdaterTargetBranch") or ""
    self._branch_btn.action_item.set_value(current_branch)
    self._branch_btn.set_visible(not ui_state.params.get_bool("IsTestedBranch"))
    self._branch_btn.action_item.set_enabled(ui_state.is_offroad() and bool(ui_state.params.get("UpdaterAvailableBranches")))

    # Update install button
    self._install_btn.set_visible(ui_state.is_offroad() and update_available)
    if update_available:
      new_desc = ui_state.params.get("UpdaterNewDescription") or ""
      new_release_notes = (ui_state.params.get("UpdaterNewReleaseNotes") or b"").decode("utf-8", "replace")
      self._install_btn.action_item.set_text(tr("INSTALL"))
      self._install_btn.action_item.set_value(new_desc)
      self._install_btn.set_description(new_release_notes)
      # Enable install button for testing (like Qt showEvent)
      self._install_btn.action_item.set_enabled(True)
    else:
      self._install_btn.set_visible(False)

  def _on_download_update(self):
    if not ui_state.is_offroad() or self._waiting_for_updater or not self._download_btn.action_item.enabled:
      return
    # Check if we should start checking or start downloading
    self._download_btn.action_item.set_enabled(False)
    if self._download_btn.action_item.text == tr("CHECK"):
      # Start checking for updates
      self._waiting_for_updater = True
      self._waiting_start_ts = time.monotonic()
      subprocess.run("pkill -SIGUSR1 -f openpilot.system.updated.updated", shell=True)
    else:
      # Start downloading
      self._waiting_for_updater = True
      self._waiting_start_ts = time.monotonic()
      subprocess.run("pkill -SIGHUP -f openpilot.system.updated.updated", shell=True)

  def _on_uninstall(self, action_guard: Callable[[], bool] | None = None):
    if not ui_state.is_offroad():
      return
    def handle_uninstall_confirmation(result: DialogResult):
      if result == DialogResult.CONFIRM and ui_state.is_offroad() and (action_guard is None or action_guard()):
        ui_state.params.put_bool("DoUninstall", True, block=True)

    dialog = ConfirmDialog(tr("Are you sure you want to uninstall?"), tr("Uninstall"), callback=handle_uninstall_confirmation)
    gui_app.push_widget(dialog)

  def _on_auto_updates_toggle(self, enabled: bool):
    if ui_state.is_offroad():
      ui_state.params.put_bool(AUTOMATIC_DOWNLOADS, enabled, block=True)

  def _on_error_log(self):
    try:
      text = Path("/data/error_logs/error.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
      text = tr("No error log found.")
    gui_app.push_widget(ConfirmDialog(text, tr("OK"), cancel_text="", rich=True))

  def _on_install_update(self):
    if not ui_state.is_offroad() or not ui_state.params.get_bool("UpdateAvailable"):
      return
    # Trigger reboot to install update
    self._install_btn.action_item.set_enabled(False)
    ui_state.params.put_bool("DoReboot", True, block=True)

  def _on_select_branch(self, action_guard: Callable[[], bool] | None = None):
    if not ui_state.is_offroad() or ui_state.params.get_bool("IsTestedBranch"):
      return
    # Get available branches and order
    current_git_branch = ui_state.params.get("GitBranch") or ""
    branches_str = ui_state.params.get("UpdaterAvailableBranches") or ""
    branches = [b for b in branches_str.split(",") if b]
    if not branches:
      return

    for b in [current_git_branch, "devel-staging", "devel", "nightly", "nightly-dev", "master"]:
      if b in branches:
        branches.remove(b)
        branches.insert(0, b)

    current_target = ui_state.params.get("UpdaterTargetBranch") or ""

    def handle_selection(result: DialogResult):
      # Confirmed selection
      if (result == DialogResult.CONFIRM and ui_state.is_offroad() and
          not ui_state.params.get_bool("IsTestedBranch") and
          self._branch_dialog is not None and self._branch_dialog.selection in branches and
          (action_guard is None or action_guard())):
        selection = self._branch_dialog.selection
        ui_state.params.put("UpdaterTargetBranch", selection, block=True)
        self._branch_btn.action_item.set_value(selection)
        self._download_btn.action_item.set_enabled(False)
        self._waiting_for_updater = True
        self._waiting_start_ts = time.monotonic()
        subprocess.run("pkill -SIGUSR1 -f openpilot.system.updated.updated", shell=True)
      self._branch_dialog = None

    self._branch_dialog = MultiOptionDialog(tr("Select a branch"), branches, current_target, callback=handle_selection)
    gui_app.push_widget(self._branch_dialog)
