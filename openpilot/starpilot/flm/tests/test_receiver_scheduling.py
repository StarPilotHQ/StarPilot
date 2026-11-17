"""The receiver must reach startup at normal priority; numerical guards stay separate."""
from unittest import mock

import pytest

from openpilot.starpilot.flm import steerlearnerd


class ReachedParams(Exception):
  pass


def test_actual_receiver_main_requests_normal_priority_before_reading_params():
  with mock.patch.object(steerlearnerd.os, 'sched_setscheduler') as scheduler, \
       mock.patch.object(steerlearnerd.os, 'setpriority') as priority, \
       mock.patch.object(steerlearnerd.os, 'nice') as nice, \
       mock.patch.object(steerlearnerd, 'Params', side_effect=ReachedParams):
    with pytest.raises(ReachedParams):
      steerlearnerd.main()
  scheduler.assert_called_once_with(0, steerlearnerd.os.SCHED_OTHER, mock.ANY)
  priority.assert_called_once_with(steerlearnerd.os.PRIO_PROCESS, 0, 0)
  nice.assert_not_called()


@pytest.mark.parametrize('failed_call', ['sched_setscheduler', 'setpriority'])
def test_actual_receiver_main_refuses_unqualified_scheduler_startup(failed_call):
  with mock.patch.object(steerlearnerd.os, 'sched_setscheduler'), \
       mock.patch.object(steerlearnerd.os, 'setpriority'), \
       mock.patch.object(steerlearnerd, 'Params') as params:
    getattr(steerlearnerd.os, failed_call).side_effect = PermissionError('unavailable')
    assert steerlearnerd.main() is None
  params.assert_not_called()
