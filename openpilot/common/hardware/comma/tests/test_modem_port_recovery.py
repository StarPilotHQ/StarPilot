from unittest import mock

from openpilot.common.hardware.comma import modem as m


def test_initialization_resyncs_ports_before_at_commands():
  modem = m.Modem()
  with mock.patch.object(m.glob, 'glob', return_value=['/dev/ttyUSB2', '/dev/ttyUSB3']), \
       mock.patch.object(m.os.path, 'exists', return_value=False), \
       mock.patch.object(modem, '_ppp'), \
       mock.patch.object(modem, '_sync_ports', return_value=True, create=True) as sync, \
       mock.patch.object(modem, '_init_at_channel', return_value=False) as init:
    calls = mock.Mock()
    calls.attach_mock(sync, 'sync')
    calls.attach_mock(init, 'init')
    assert modem._do_initializing() == m.State.INITIALIZING
    assert calls.mock_calls == [mock.call.sync(), mock.call.init()]


def test_missing_usb_nodes_waits_without_touching_ports():
  modem = m.Modem()
  with mock.patch.object(m.glob, 'glob', return_value=[]), \
       mock.patch.object(modem, '_sync_ports', create=True) as sync, mock.patch.object(modem, '_ppp') as ppp:
    assert modem._do_initializing() == m.State.INITIALIZING
    sync.assert_not_called()
    ppp.kill.assert_not_called()


def test_busy_or_unidentified_ports_do_not_start_at_initialization():
  modem = m.Modem()
  with mock.patch.object(m.glob, 'glob', return_value=['/dev/ttyUSB2']), \
       mock.patch.object(modem, '_ppp'), mock.patch.object(modem, '_sync_ports', return_value=False, create=True), \
       mock.patch.object(modem, '_init_at_channel') as init:
    assert modem._do_initializing() == m.State.INITIALIZING
    init.assert_not_called()


def test_renumbered_ports_are_sorted_numerically_and_linked():
  modem = m.Modem()
  with mock.patch.object(m.glob, 'glob', return_value=['/dev/ttyUSB9', '/dev/ttyUSB10', '/dev/ttyUSB8']), \
       mock.patch.object(m.subprocess, 'run') as run, mock.patch.object(m.os, 'open', return_value=99), \
       mock.patch.object(m.os, 'close'), mock.patch.object(m.fcntl, 'flock'), \
       mock.patch.object(m, '_answers_at', return_value=True) as answers:
    assert modem._sync_ports()
    assert answers.call_args_list == [mock.call('/dev/ttyUSB10'), mock.call('/dev/ttyUSB9')]
    assert run.call_args_list[-2:] == [
      mock.call(['sudo', 'ln', '-sfn', 'ttyUSB9', m.AT_PORT], capture_output=True),
      mock.call(['sudo', 'ln', '-sfn', 'ttyUSB10', m.PPP_PORT], capture_output=True),
    ]


def test_lpa_lock_prevents_probing():
  modem = m.Modem()
  with mock.patch.object(m.glob, 'glob', return_value=['/dev/ttyUSB2', '/dev/ttyUSB3']), \
       mock.patch.object(m.subprocess, 'run'), mock.patch.object(m.os, 'open', return_value=99), \
       mock.patch.object(m.os, 'close') as close, mock.patch.object(m.fcntl, 'flock', side_effect=BlockingIOError), \
       mock.patch.object(m, '_answers_at') as answers:
    assert not modem._sync_ports()
    close.assert_called_once_with(99)
    answers.assert_not_called()


def test_one_at_port_is_not_linked():
  modem = m.Modem()
  with mock.patch.object(m.glob, 'glob', return_value=['/dev/ttyUSB2', '/dev/ttyUSB3']), \
       mock.patch.object(m.subprocess, 'run') as run, mock.patch.object(m.os, 'open', return_value=99), \
       mock.patch.object(m.os, 'close'), mock.patch.object(m.fcntl, 'flock'), \
       mock.patch.object(m, '_answers_at', side_effect=[True, False]):
    assert not modem._sync_ports()
    assert not any(c.args[0][1] == 'ln' for c in run.call_args_list)
