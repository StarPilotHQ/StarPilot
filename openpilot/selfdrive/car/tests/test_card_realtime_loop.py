from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

from openpilot.selfdrive.car.card import Car


def test_control_loop_avoids_temporary_sampling_and_preserves_shutdown():
  calls = []
  stop = RuntimeError('stop private loop')
  keepalive = Mock()
  startup = SimpleNamespace(check=lambda: calls.append('startup'))
  def monitor():
    calls.append('monitor')
    raise stop
  owner = SimpleNamespace(configure_conditional_settings_worker=lambda: calls.append('configure'),
                          params_thread=Mock(), vehicle_startup=startup,
                          step=lambda: calls.append('step'), rk=SimpleNamespace(monitor_time=monitor),
                          ioniq6_keepalive=keepalive, ioniq6_long_lost=False)
  with patch('openpilot.selfdrive.car.card.threading.Thread') as thread, \
       patch.dict('sys.modules', {'openpilot.starpilot.diagnostics.card_timing':
                                 SimpleNamespace(CardTiming=Mock(side_effect=AssertionError('realtime sampling')))}):
    with pytest.raises(RuntimeError) as error:
      Car.card_thread(cast(Car, owner))
    assert error.value is stop
    assert calls == ['configure', 'startup', 'step', 'monitor']
    event = thread.call_args.kwargs['args'][0]
    assert event.is_set()
    thread.return_value.start.assert_called_once_with()
    thread.return_value.join.assert_called_once_with()
  assert owner.ioniq6_long_lost
  keepalive.stop.assert_called_once_with()
