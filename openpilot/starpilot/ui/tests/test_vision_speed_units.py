"""Recognized sign numbers through the Vision wire, SLC, cruise SET and both HUDs."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.cereal import messaging
from openpilot.common.constants import CV
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
from openpilot.starpilot.speed_limits.physical_actions import command_applicable
from openpilot.starpilot.speed_limits.runtime import Action, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM
from openpilot.starpilot.speed_limits.vision import producer
from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
from openpilot.starpilot.ui.onroad_state import OnroadState, speed_limit_from_message
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.tests.test_unified_speed_native import drawing as drawing, fonts, foreground_calls


@pytest.mark.usefixtures('drawing')
@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('confirmation', [False, True])
@pytest.mark.parametrize('metric, number', [(False, 30), (False, 50), (False, 80),
                                         (True, 30), (True, 50), (True, 80), (True, 100), (True, 120)])
def test_vision_sign_and_cruise_set_use_the_configured_units(profile, confirmation, metric, number):
  sent = []
  producer._send(SimpleNamespace(send=lambda service, event: sent.append(event)),
                 session='camera', status='valid', stream='road', is_metric=metric,
                 observed_ns=1_000_000_000, eof_boot_ns=5_000_000_000, frame_id=1,
                 result=(number, .99, 2, 1))
  vision = messaging.log_from_bytes(sent[0].to_bytes()).slcVisionObservation
  expected_mps = number * (CV.KPH_TO_MS if metric else CV.MPH_TO_MS)
  assert vision.vision.speedMps == pytest.approx(expected_mps)

  data, _ = messages()
  car = data['carState']
  car.vCruise = car.vCruiseCluster = 90.
  car.vEgoCluster = car.vEgo
  data['carControl'].enabled = data['carControl'].longActive = True
  dashboard = messaging.new_message('slcDashboardObservation').slcDashboardObservation
  dashboard.status = 'unknown'
  dashboard.producerSessionId = 'card'
  data['slcDashboardObservation'] = dashboard
  data['slcVisionObservation'] = vision
  now = 1_010_000_000
  sm = ReplaySM(data, now)
  cp = SimpleNamespace(brand='hyundai', carFingerprint='IONIQ6', openpilotLongitudinalControl=True, pcmCruise=False)
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCConfirmation': confirmation, 'SLCPriority1': 'Vision'}),
                    session_id='units', vision_enabled=True, vision_control_qualified=True, is_metric=metric)
  output = runtime.step(sm, cp, now_ns=now, now_boot_ns=now + 4_000_000_000)

  def render(state, cruise_kph):
    current = OnroadState(True, False, 20., cruise_kph, speed_limit_from_message(state),
                          metric=metric, longitudinal_active=True, observed_ns=now, drive_frame=1)
    font = fonts(profile)
    if profile == Profile.LARGE:
      widget = UnifiedSpeedWidget(font)
      widget.render(rl.Rectangle(30, 30, 1800, 1020), current)
      widget.sign.render_confirmation(current)
    else:
      widget = CompactHudRenderer(font, Mock())
      widget._speed_limit_sign(current)
      widget._speed_limit_widget.render_confirmation(current)
      widget.render_max_speed(current)
      widget.render_max_speed(replace(current, observed_ns=now + 20_000_000))
    return [call.args[0] for call in foreground_calls(font.draw)]

  texts = render(output.message.slcState, 90.)
  assert 'VISION' in texts and str(number) in texts
  if confirmation:
    assert output.command is None and output.message.slcState.hasPending
    state = output.message.slcState
    now += 20_000_000
    sm.advance(now)
    output = runtime.step(sm, cp, now_ns=now, now_boot_ns=now + 4_000_000_000,
                          request=Action('units', 1, state.decisionId, state.presentationId, 'accept'))
  assert output.command is not None
  command = output.command.slcCruiseCommand
  assert command.targetMps == pytest.approx(expected_mps)
  assert command_applicable(command, output.message.slcState, None, 'card', now, 90 / 3.6,
                            long_active=True, button_event=False)
  cruise = VCruiseHelper(cp)
  cruise.apply_slc_target(command.targetMps, metric)
  expected_kph = number if metric else round(number * CV.MPH_TO_KPH, 1)
  assert cruise.v_cruise_kph == pytest.approx(expected_kph)
  assert cruise.v_cruise_cluster_kph == pytest.approx(expected_kph)
  texts = render(output.message.slcState, cruise.v_cruise_kph)
  assert texts.count(str(number)) >= 2
  assert ('SET' if profile == Profile.LARGE else 'MAX') in texts
