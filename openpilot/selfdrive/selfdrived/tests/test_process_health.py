"""Optional projection cannot acquire driving health authority."""
import pytest

from openpilot.cereal import log, messaging
from openpilot.selfdrive.selfdrived.events import ET, EventName, Events
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.selfdrived.state import StateMachine


def process_health(processes):
  message = messaging.new_message('managerState')
  message.managerState.processes = [{'name': name, 'running': running, 'shouldBeRunning': requested}
                                    for name, running, requested in processes]
  drive = SelfdriveD.__new__(SelfdriveD)
  class Sources:
    recv_frame = {'managerState': 1}

    def __getitem__(self, key):
      assert key == 'managerState'
      return message.managerState.as_reader()

  vars(drive)['sm'] = Sources()
  drive.not_running_prev = None
  drive.events = Events()
  return drive


@pytest.mark.parametrize('name', ['starportd', 'galaxy_hotspot', 'jetlinkd', 'ui'])
@pytest.mark.parametrize('running,requested', [(False, False), (False, True), (True, True)])
def test_optional_lifecycle_does_not_block_engagement(name, running, requested):
  drive = process_health([(name, running, requested)])
  assert not drive.update_process_health()
  assert not drive.events.contains(ET.NO_ENTRY)
  drive.events.add(EventName.buttonEnable)
  state = StateMachine()
  state.update(drive.events)
  assert state.state == log.SelfdriveState.OpenpilotState.enabled
  # Manager still reports the real failed/requested service rather than hiding it.
  assert drive.sm['managerState'].processes[0].running == running
  assert drive.sm['managerState'].processes[0].shouldBeRunning == requested


@pytest.mark.parametrize('critical', ['controlsd', 'pandad', 'modeld', 'card', 'unknown_service'])
def test_projection_failure_does_not_hide_critical_process_failure(critical):
  drive = process_health([('starportd', False, True), (critical, False, True)])
  assert drive.update_process_health()
  assert drive.not_running_prev == {critical}
  assert drive.events.contains(ET.NO_ENTRY)
  assert drive.events.contains(ET.SOFT_DISABLE)
  drive.events.add(EventName.buttonEnable)
  state = StateMachine()
  state.update(drive.events)
  assert state.state == log.SelfdriveState.OpenpilotState.disabled


def test_optional_failure_does_not_soft_disable_existing_drive():
  drive = process_health([('starportd', False, True)])
  assert not drive.update_process_health()
  state = StateMachine()
  state.state = log.SelfdriveState.OpenpilotState.enabled
  state.update(drive.events)
  assert state.state == log.SelfdriveState.OpenpilotState.enabled


def test_missing_starport_service_does_not_block_engagement():
  drive = process_health([])
  assert not drive.update_process_health()
  drive.events.add(EventName.buttonEnable)
  state = StateMachine()
  state.update(drive.events)
  assert state.state == log.SelfdriveState.OpenpilotState.enabled


@pytest.mark.parametrize('critical', ['controlsd', 'selfdrived', 'pandad', 'modeld', 'card', 'camerad', 'soundd', 'unknown_service'])
def test_ui_restart_does_not_hide_driving_failure(critical):
  drive = process_health([('ui', False, True), (critical, False, True)])
  assert drive.update_process_health()
  assert drive.not_running_prev == {critical}
  assert drive.events.contains(ET.SOFT_DISABLE)


def test_ui_restart_preserves_enabled_state_without_warning():
  drive = process_health([('ui', False, True), ('controlsd', True, True), ('selfdrived', True, True)])
  assert not drive.update_process_health()
  state = StateMachine()
  state.state = log.SelfdriveState.OpenpilotState.enabled
  state.update(drive.events)
  assert state.state == log.SelfdriveState.OpenpilotState.enabled
  assert not drive.events.contains(ET.SOFT_DISABLE)
  assert not drive.events.contains(ET.IMMEDIATE_DISABLE)
