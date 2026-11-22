"""Native software request routing for the StarPilot runtime shell."""

from typing import Any

from openpilot.starpilot.ui.software_state import SoftwareAction


def deliver_software(self: Any, request: SoftwareAction, *, shell_mode, destination, clock, software_request, download_label) -> bool:
  from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
  fresh = self.star.adapter.build(shell_mode.SETTINGS, destination.SOFTWARE, now_ns=clock.monotonic_ns()).software
  if request.request not in fresh.available_actions:
    return False
  owner = self._panel_owner(PanelType.SOFTWARE)
  if request.request in (software_request.CHECK_FOR_UPDATES, software_request.DOWNLOAD_UPDATE):
    expected = download_label.DOWNLOAD if request.request == software_request.DOWNLOAD_UPDATE else download_label.CHECK
    owner._update_state()
    if (fresh.download_label != expected or owner._waiting_for_updater or
        not owner._download_btn.action_item.enabled):
      return False
    from openpilot.system.ui.lib.multilang import tr
    if owner._download_btn.action_item.text != tr(expected.value):
      return False
    owner._on_download_update()
  elif request.request in (software_request.FAST_UPDATE, software_request.ROLLBACK):
    owner._update_state()
    owner._confirm_direct_update("rollback" if request.request == software_request.ROLLBACK else "fast")
  elif request.request == software_request.OPEN_BRANCH_CHOOSER:
    owner._on_select_branch(self._confirmed_offroad)
  elif request.request == software_request.OPEN_UNINSTALL_CONFIRMATION:
    owner._on_uninstall(self._confirmed_offroad)
  elif request.request == software_request.OPEN_ERROR_LOG:
    owner._on_error_log()
  else:
    return False
  return True

