import unittest
from unittest.mock import patch

from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, RuntimeSnapshotAdapter, ui_fake


class TestCompactMetricWork(unittest.TestCase):
  def test_only_the_compact_onroad_view_omits_unused_sidebar_work(self):
    for view_only, profile in ((True, 'compact'), (True, 'large'), (False, 'compact'), (False, 'large')):
      with self.subTest(view_only=view_only, profile=profile):
        adapter = RuntimeSnapshotAdapter(ui_fake())
        with patch.object(adapter._developer_metrics, 'observe', wraps=adapter._developer_metrics.observe) as observe:
          state = adapter.build(ShellMode.ONROAD, now_ns=NOW, onroad_view_only=view_only,
                                stopped_timer_profile=profile).onroad
        skipped = view_only and profile == 'compact'
        self.assertEqual(observe.call_count, 0 if skipped else 1)
        self.assertEqual(len(state.developer_metrics), 0 if skipped else 7)


if __name__ == '__main__':
  unittest.main()
