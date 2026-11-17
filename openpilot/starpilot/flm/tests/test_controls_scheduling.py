"""A denied optional scheduling preference must not stop driving services."""
from unittest import mock

import pytest

from openpilot.selfdrive.controls import controlsd


@pytest.mark.parametrize('error', [None, PermissionError('priority limit')])
def test_controls_startup_survives_unavailable_normal_priority(error):
  with mock.patch.object(controlsd, 'config_realtime_process'), \
       mock.patch.object(controlsd, 'Controls') as controls, \
       mock.patch.object(controlsd.HARDWARE, 'get_device_type', return_value='mici'), \
       mock.patch.object(controlsd, 'set_core_affinity'), \
       mock.patch.object(controlsd.os, 'sched_setscheduler') as scheduler, \
       mock.patch.object(controlsd.os, 'setpriority', side_effect=error) as priority, \
       mock.patch.object(controlsd.cloudlog, 'warning') as warning:
    controlsd.main()
  controls.return_value.run.assert_called_once_with()
  scheduler.assert_called_once_with(0, controlsd.os.SCHED_OTHER, mock.ANY)
  priority.assert_called_once_with(controlsd.os.PRIO_PROCESS, 0, -5)
  assert warning.call_count == bool(error)
