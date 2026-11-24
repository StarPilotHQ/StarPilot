"""Resume Speed Limit + Offset from a car button (Tesla right stalk, wheel) or a favorite.

A car button is handled part-way through a screen frame, so by then a 100 Hz message is older than the screen's own
age limit and the action would almost never be available. A car button reads the car's messages with the scroll
wheel's 250 ms limit instead, through its own dispatcher.
"""
import atexit
import tempfile
from types import SimpleNamespace as NS
from typing import Any
from unittest.mock import patch

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.common.car_control_config import NO_LIMIT_REASON, SPEED_RESUME
from openpilot.starpilot.ui.onroad_state import SlcActionKind
from openpilot.starpilot.ui.runtime_snapshot import INPUT_MAX_AGE_NS, current_message, input_message
from openpilot.starpilot.ui.slc_action_dispatch import SlcActionDispatcher
from openpilot.starpilot.ui.slc_resume_action import resume_action
from openpilot.starpilot.ui.tests.test_runtime_snapshot import BOOT_OFFSET_NS, NOW, ui_fake

_param_dir = tempfile.TemporaryDirectory(prefix="starpilot-resume-test-")
atexit.register(_param_dir.cleanup)
_original_params_init = Params.__init__
with patch.object(Params, "__init__", lambda self, d="": _original_params_init(self, _param_dir.name)):
  from openpilot.starpilot.ui import runtime_app

CAR_SERVICES = ("carState", "carControl", "controlsState", "selfdriveState")


class SavedParams:
  def __init__(self, safe=False):
    self.safe = safe

  def get_bool(self, key):
    return self.safe if key == 'SafeMode' else False


def live(**changes):
  state = {'enabled': True, 'observationKind': 'valid', 'hasPending': False, 'automaticAdoptionPending': False,
           'sessionId': 's1', 'decisionId': 3, 'presentationId': 7, 'speedLimit': 22.35}
  state.update(changes)
  return NS(**state)


class Shell:
  def __init__(self, state, display=True, accepted=True):
    self.state, self.display, self.accepted = state, display, accepted
    self.reads, self.sent, self.screen_sent = [], [], []
    self.slc_car_actions: Any = NS(dispatch=self.dispatch)
    self.slc_actions = NS(dispatch=self.screen_sent.append)

  def _live_slc_message(self, now_ns, *, car_input=False):
    self.reads.append(car_input)
    return self.state

  def _favorite_authority(self):
    return self.display

  def dispatch(self, request):
    self.sent.append(request)
    return self.accepted


def ui(safe=False):
  return NS(params=SavedParams(safe), replay_clock=None)


def press(action):
  """Run the action as a car button or a favorite would."""
  assert action.invoke is not None
  return action.invoke()


def test_car_button_adopts_the_limit_reading_with_the_button_age():
  shell = Shell(live())
  action = resume_action(shell, ui(), 'drive', require_display=False)
  assert action.available and action.label == 'Resume Speed Limit + Offset' and action.key == SPEED_RESUME
  assert press(action) is True
  request, = shell.sent
  assert request.kind is SlcActionKind.ADOPT
  assert (request.session_id, request.decision_id, request.presentation_id, request.candidate_speed_mps) == ('s1', 3, 7, 22.35)
  assert shell.reads and all(shell.reads), 'every read is a car-button read'
  assert not shell.screen_sent, "sent through the car-button dispatcher, never the screen's"


def test_a_limit_waiting_for_confirmation_is_accepted():
  shell = Shell(live(hasPending=True))
  assert press(resume_action(shell, ui(), 'drive', require_display=False)) is True
  assert shell.sent[0].kind is SlcActionKind.ACCEPT
  automatic = Shell(live(hasPending=True, automaticAdoptionPending=True))
  assert press(resume_action(automatic, ui(), 'drive', require_display=False)) is True
  assert automatic.sent[0].kind is SlcActionKind.ADOPT, 'a limit about to be adopted by itself is adopted, not confirmed'


def test_the_result_is_what_the_dispatcher_decided():
  shell = Shell(live(), accepted=False)
  assert press(resume_action(shell, ui(), 'drive', require_display=False)) is False
  assert len(shell.sent) == 1


def test_a_car_button_does_not_need_the_screen_but_a_favorite_does():
  shell = Shell(live(), display=False)
  assert resume_action(shell, ui(), 'drive', require_display=False).available
  favorite = resume_action(shell, ui(), 'drive', require_display=True)
  assert not favorite.available and press(favorite) is False and not shell.sent
  shell.display = True
  favorite = resume_action(shell, ui(), 'drive', require_display=True)
  assert favorite.available and press(favorite) is True and len(shell.sent) == 1


def test_safe_mode_replay_and_no_limit_block_it():
  assert not resume_action(Shell(live()), ui(safe=True), 'drive', require_display=False).available
  replay = NS(params=SavedParams(), replay_clock=object())
  assert not resume_action(Shell(live()), replay, 'drive', require_display=False).available
  no_limit = resume_action(Shell(live(observationKind='absent')), ui(), 'drive', require_display=False)
  assert not no_limit.available and no_limit.reason == NO_LIMIT_REASON == 'No current speed limit'
  off = resume_action(Shell(None), ui(), 'drive', require_display=False)
  assert not off.available and off.reason == 'Speed Limit Controller is not running'
  disabled = resume_action(Shell(live(enabled=False)), ui(), 'drive', require_display=False)
  assert not disabled.available and disabled.reason == 'Speed Limit Controller is not running'


def test_without_the_car_button_dispatcher_it_is_unavailable_and_sends_nothing():
  shell = Shell(live())
  shell.slc_car_actions = None
  action = resume_action(shell, ui(), 'drive', require_display=False)
  assert not action.available and press(action) is False
  del shell.slc_car_actions  # the action transport never started
  action = resume_action(shell, ui(), 'drive', require_display=False)
  assert not action.available and press(action) is False
  assert not shell.sent and not shell.screen_sent, "the screen's dispatcher is not a fallback"


def test_things_that_change_after_the_lookup_are_checked_again_at_the_press():
  state = ui()
  shell = Shell(live())
  action = resume_action(shell, state, 'drive', require_display=False)
  state.params.safe = True
  assert press(action) is False and not shell.sent
  state.params.safe = False
  shell.state = None
  assert press(action) is False and not shell.sent


def test_the_token_changes_with_the_drive_and_the_limit_shown():
  tokens = {resume_action(Shell(live()), ui(), drive, require_display=False).token for drive in ('a', 'b')}
  tokens.add(resume_action(Shell(live(presentationId=8)), ui(), 'a', require_display=False).token)
  tokens.add(resume_action(Shell(live(sessionId='s2')), ui(), 'a', require_display=False).token)
  tokens.add(resume_action(Shell(None), ui(), 'a', require_display=False).token)
  assert len(tokens) == 5


class Publisher:
  def __init__(self):
    self.sent = []

  def send(self, topic, message):
    self.sent.append((topic, messaging.log_from_bytes(message.to_bytes())))


def test_the_real_dispatcher_checks_the_limit_again_with_the_car_button_reader():
  state = live(frameMonoTime=NOW - 50_000_000, source='dashboard', pendingSpeedLimit=0., actionSequenceId=0)
  shell = Shell(state)
  publisher = Publisher()
  shell.slc_car_actions = SlcActionDispatcher(publisher, lambda now_ns: shell._live_slc_message(now_ns, car_input=True), clock=lambda: NOW)
  action = resume_action(shell, ui(), 'drive', require_display=False)
  assert press(action) is True
  (topic, message), = publisher.sent
  assert topic == 'slcAction' and message.slcAction.kind == 'adopt'
  assert (message.slcAction.sessionId, message.slcAction.decisionId, message.slcAction.presentationId) == ('s1', 3, 7)
  assert all(shell.reads) and len(shell.reads) >= 3, 'the lookup, the press and the dispatcher each read as a car button'
  assert press(action) is False and len(publisher.sent) == 1, 'the same limit is not adopted twice'


def test_the_real_dispatcher_refuses_a_press_whose_limit_has_changed_or_gone_stale():
  def dispatcher(current):
    return SlcActionDispatcher(Publisher(), lambda now_ns: current, clock=lambda: NOW)
  fresh = {"frameMonoTime": NOW - 50_000_000, "source": 'dashboard', "pendingSpeedLimit": 0., "actionSequenceId": 0}
  for current in (live(speedLimit=25., **fresh), live(presentationId=8, **fresh), live(sessionId='s2', **fresh),
                  live(enabled=False, **fresh), live(**{**fresh, 'frameMonoTime': NOW - 100_000_001}), None):
    shell = Shell(live())  # what the press read
    shell.slc_car_actions = dispatcher(current)
    assert press(resume_action(shell, ui(), 'drive', require_display=False)) is False
    assert not shell.slc_car_actions.publisher.sent


def test_a_car_button_reads_fast_messages_up_to_a_quarter_second_old():
  assert INPUT_MAX_AGE_NS == 250_000_000
  fake = ui_fake()
  for age, button, screen in ((10_000_000, True, True), (150_000_000, True, False), (250_000_000, True, False), (250_000_001, False, False)):
    fake.sm.put("carState", fake.sm.messages["carState"], age_ns=age)
    assert (input_message(fake.sm, "carState", NOW) is not None) is button, age
    assert (current_message(fake.sm, "carState", NOW) is not None) is screen, age
  fake.sm.put("carState", fake.sm.messages["carState"], age_ns=10_000_000)
  assert input_message(fake.sm, "carState", NOW, after_frame=3) is None, 'still needs a message received since the drive started'
  fake.sm.valid["carState"] = False
  assert input_message(fake.sm, "carState", NOW) is None
  fake.sm.valid["carState"] = True
  fake.sm.alive["carState"] = False
  assert input_message(fake.sm, "carState", NOW) is None
  fake.sm.alive["carState"] = True
  fake.sm.logMonoTime["carState"] = NOW + 1
  assert input_message(fake.sm, "carState", NOW) is None, 'never a message from the future'


def test_a_slow_service_keeps_its_own_longer_limit_for_a_car_button():
  fake = ui_fake()
  fake.sm.put("deviceState", fake.sm.messages["deviceState"], age_ns=900_000_000)  # 2 Hz: two periods is one second
  assert input_message(fake.sm, "deviceState", NOW) is not None and current_message(fake.sm, "deviceState", NOW) is not None


def test_live_state_for_a_car_button_allows_a_message_a_frame_old():
  # The real reader: 100 Hz messages 150 ms old pass for a car button; past its limit they are refused for both.
  fake = ui_fake()
  fake.CP = NS(carFingerprint="TESLA_MODEL_3", openpilotLongitudinalControl=True, pcmCruise=True)
  fake.sm.messages["carState"].canValid = True
  fake.sm.messages["carState"].canTimeout = False
  fake.sm.put("controlsState", NS(longControlState="pid"))
  for service in CAR_SERVICES:
    fake.sm.put(service, fake.sm.messages[service], age_ns=150_000_000)
  session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  session.adapter = runtime_app.RuntimeSnapshotAdapter(fake)
  with patch("openpilot.starpilot.ui.runtime_snapshot._clock_pair", return_value=(NOW, NOW + BOOT_OFFSET_NS, BOOT_OFFSET_NS)):
    assert session._live_slc_message(NOW, car_input=True) is fake.sm["slcState"]
    for service in CAR_SERVICES:
      fake.sm.put(service, fake.sm.messages[service], age_ns=230_000_000)
    assert session._live_slc_message(NOW) is None, "past the screen's own limit"
    assert session._live_slc_message(NOW, car_input=True) is fake.sm["slcState"]
    for service in CAR_SERVICES:
      fake.sm.put(service, fake.sm.messages[service], age_ns=300_000_000)
    assert session._live_slc_message(NOW, car_input=True) is None, 'still refused past the button limit'
    assert session._live_slc_message(NOW) is None
    for service in CAR_SERVICES:
      fake.sm.put(service, fake.sm.messages[service], age_ns=10_000_000)
    fake.sm.messages["carState"].canValid = False
    assert session._live_slc_message(NOW, car_input=True) is None, 'the longer age limit changes nothing else'


def test_the_shell_gives_car_buttons_their_own_dispatcher():
  source = open(runtime_app.__file__).read()
  assert "self.slc_car_actions = SlcActionDispatcher(_slc_action_publisher(), lambda now_ns: self._live_slc_message(now_ns, car_input=True))" in source
  assert "self.slc_actions = SlcActionDispatcher(_slc_action_publisher(), self._live_slc_message)" in source
  assert issubclass(runtime_app.StarShellSession, runtime_app.TeslaSpeedActions)
