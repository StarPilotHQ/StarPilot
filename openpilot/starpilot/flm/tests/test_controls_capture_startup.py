"""Fresh process activation has no cold capture imports; overruns keep outputs."""
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from typing import cast

import pytest


@pytest.mark.parametrize('vehicle', ['HYUNDAI_IONIQ_6', 'TOYOTA_RAV4_TSS2'])
def test_first_active_capture_does_not_import_capture_modules(vehicle):
  code = r"""
import sys
from opendbc.car.car_helpers import interfaces
from openpilot.starpilot.flm.tests.test_registered_controls_global import controls_for, frame
identity = sys.argv[1]
cp = interfaces[identity].get_non_essential_params(identity)
with controls_for(cp, 'standard', True) as controls:
  before = set(sys.modules)
  assert {'openpilot.starpilot.flm.' + name for name in
          ('live_trial_wire', 'live_tracking_response')} <= before, 'cold trial dependency left for active Controls'
  for tick in range(30):
    command, state = frame(controls, 400_000_000_000 + tick * 10_000_000, tick)
    controls.publish(command, state)
  assert controls.global_learning is not None
  assert controls.global_learning.snapshot_ns > 0
  assert command.latActive
  capture = {'openpilot.starpilot.flm.' + name for name in
             ('controller_replay', 'normalized_trace_archive', 'physical_input_observation', 'feedback_wire')}
  assert not (set(sys.modules) - before) & capture, 'cold capture import in active Controls'
"""
  result = subprocess.run([sys.executable, '-B', '-c', code, vehicle], capture_output=True, text=True, timeout=15)
  assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('duration_ms', [5, 60])
def test_cycle_timing_reports_after_output_without_changing_control_calls(duration_ms):
  from openpilot.selfdrive.controls.controlsd import Controls

  class Finished(Exception):
    pass

  order = []
  command, state = object(), object()
  controls = SimpleNamespace(update=lambda: order.append('update'),
    state_control=lambda: (order.append('control') or (command, state)),
    publish=lambda cc, log: order.append(('publish', cc, log)))
  base = 10_000_000_000
  stamps = [base, base + 1_000_000, base + duration_ms * 1_000_000, base + duration_ms * 1_000_000]
  with patch('openpilot.selfdrive.controls.controlsd.Ratekeeper') as ratekeeper, \
       patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', side_effect=stamps), \
       patch('openpilot.selfdrive.controls.controlsd.time.thread_time_ns', return_value=1), \
       patch('openpilot.selfdrive.controls.controlsd.cloudlog.event') as event:
    ratekeeper.return_value.monitor_time.side_effect = Finished
    with pytest.raises(Finished):
      Controls.run(cast(Controls, controls))
    assert order == ['update', 'control', ('publish', command, state)]
    assert event.call_count == int(duration_ms > 50)
    if duration_ms > 50:
      assert event.call_args.kwargs['wall_ms'] == duration_ms
      assert event.call_args.kwargs['control_ms'] == duration_ms - 1
