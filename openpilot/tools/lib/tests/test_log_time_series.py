import numpy as np

from openpilot.cereal import messaging
from openpilot.tools.lib.log_time_series import msgs_to_time_series


def test_union_columns_keep_rows_aligned_when_controller_changes():
  messages = []
  for stamp, branch, output in [(3, 'pidState', 0.25), (1, 'torqueState', 0.5), (2, 'pidState', 0.75)]:
    message = messaging.new_message('controlsState')
    message.logMonoTime = stamp * 10**9
    message.controlsState.lateralControlState.init(branch).output = output
    messages.append(message)
  group = msgs_to_time_series(messages)['controlsState']
  assert group['t'].tolist() == [1., 2., 3.]
  assert group['lateralControlState/pidState/output'].tolist() == [None, 0.75, 0.25]
  assert group['lateralControlState/torqueState/output'].tolist() == [0.5, None, None]
  assert all(len(column) == 3 for column in group.values())


def test_nested_values_and_ragged_lists_preserve_sort_order():
  messages = []
  for stamp, points in [(2, [1., 2.]), (1, [3.])]:
    message = messaging.new_message('modelV2')
    message.logMonoTime = stamp * 10**9
    message.modelV2.position.x = points
    messages.append(message)
  group = msgs_to_time_series(messages)['modelV2']
  assert group['t'].tolist() == [1., 2.]
  np.testing.assert_array_equal(group['position/x'][0], [3.])
  np.testing.assert_array_equal(group['position/x'][1], [1., 2.])
