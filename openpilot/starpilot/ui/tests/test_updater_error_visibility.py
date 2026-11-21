from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.ui.runtime_software import deliver_software
from openpilot.starpilot.ui.software_state import SoftwareAction, SoftwareRequest, DownloadLabel
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.shell import ShellMode


@pytest.mark.parametrize("allowed", [False, True])
def test_error_log_request_uses_fresh_permission(allowed):
  owner = Mock()
  software = NS(available_actions={SoftwareRequest.OPEN_ERROR_LOG} if allowed else set())
  shell = NS(star=NS(adapter=NS(build=lambda *a, **k: NS(software=software))), _panel_owner=lambda panel: owner)
  assert deliver_software(shell, SoftwareAction(SoftwareRequest.OPEN_ERROR_LOG), shell_mode=ShellMode,
                          destination=Destination, clock=NS(monotonic_ns=lambda: 1),
                          software_request=SoftwareRequest, download_label=DownloadLabel) is allowed
  assert owner._on_error_log.call_count == int(allowed)


@pytest.mark.parametrize("error", ["AGNOS <hash> mismatch\n" + "x" * 5000, b"AGN\xffOS failure"], ids=["long-text", "invalid-utf8"])
def test_native_error_log_prefers_bounded_plain_updater_error(error):
  from openpilot.selfdrive.ui.layouts.settings import software
  expected = error.decode("utf-8", "replace") if isinstance(error, bytes) else error
  with (patch.object(software, "ui_state", NS(params=NS(get=lambda key: error))),
        patch.object(software, "gui_app") as app, patch.object(software, "ConfirmDialog") as dialog,
        patch.object(software.Path, "read_text", side_effect=AssertionError("must not read crash log"))):
    software.SoftwareLayout._on_error_log(software.SoftwareLayout.__new__(software.SoftwareLayout))
    assert dialog.call_args.args[0] == expected[:4096]
    assert dialog.call_args.kwargs["rich"] is False
    app.push_widget.assert_called_once()


def test_native_error_log_preserves_crash_log_fallback():
  from openpilot.selfdrive.ui.layouts.settings import software
  with (patch.object(software, "ui_state", NS(params=NS(get=lambda key: None))),
        patch.object(software, "gui_app"), patch.object(software, "ConfirmDialog") as dialog,
        patch.object(software.Path, "read_text", return_value="Existing crash log")):
    software.SoftwareLayout._on_error_log(software.SoftwareLayout.__new__(software.SoftwareLayout))
    assert dialog.call_args.args[0] == "Existing crash log"
    assert dialog.call_args.kwargs["rich"] is True
