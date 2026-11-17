"""Resume refusal remains longitudinal while an established lateral owner stays current."""
import pytest

from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import ET, EventName
from openpilot.starpilot.aol.runtime import current_axis, current_native
from openpilot.starpilot.aol.tests.test_ioniq_availability import IoniqAvailabilityTests, car_state


def make_callers():
  case = IoniqAvailabilityTests(methodName='runTest')
  try:
    callers = case._ioniq_availability_callers(False)
  except BaseException:
    case.doCleanups()
    raise
  return case, callers


def deliver(sd, receive, now, order, state):
  proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
  if order == 'proof_first':
    first = receive(now, axis=proof, state=state)
  elif order == 'event_first':
    first = receive(now, event=event, state=state)
  else:
    first = receive(now, axis=proof, event=event, state=state)
  assert first
  assert receive(now + 1_000_000, axis=proof, event=event, state=state)


@pytest.mark.parametrize('order', ['proof_first', 'event_first', 'together'])
def test_resume_before_first_set_retains_established_lateral_owner(order):
  with OpenpilotPrefix():
    case, (sd, controls, drive, card, receive) = make_callers()
    try:
      for tick, pressed in ((2, True), (3, False)):
        state = car_state()
        state.vCruise = state.vCruiseCluster = 255.
        state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.accelCruise, pressed=pressed)]
        state.buttonEnable = card.CI.CS.update_button_enable(state.buttonEvents)
        assert state.buttonEnable == (not pressed)
        axis, selfdrive, command = drive(tick, observed_state=state, gas=True)
        assert EventName.resumeBlocked in sd.events.names
        assert ET.NO_ENTRY in sd.events.event_rules[EventName.resumeBlocked]
        assert sd.events.contains(ET.NO_ENTRY)
        assert sd.state_machine.state == log.SelfdriveState.OpenpilotState.disabled
        assert not sd.enabled and not sd.active and not selfdrive.enabled
        assert not axis.desiredLongitudinal and not axis.longitudinalActive and not command.longActive
        assert not command.cruiseControl.resume and not command.cruiseControl.cancel
        now = 10_000_000_000 + tick * 10_000_000
        # The original source's fresh OFF/no-ACK proof makes actual Card clear here.
        deliver(sd, receive, now, order, state)
        assert card.aol_card_intent.allowed_latch
        assert not card.aol_transport_feedback.failed
        assert axis.desiredLateral and axis.lateralActive and axis.nativeAcknowledged
        assert str(axis.faultReason) == 'none' and command.latActive
        assert axis.sourceCarStateMonoTime == now
        assert axis.validUntilMonoTime == axis.observedMonoTime + 30_000_000
        native = current_native(sd.sm, sd.CP, now_ns=now, axis_session_id=sd.aol_session_id)
        assert native is not None and native.requestedLateral and native.lateralAllowed
        assert not native.requestedLongitudinal and not native.longitudinalAllowed
        assert not selfdrive.active
        assert current_axis(controls.sm, now_ns=axis.validUntilMonoTime + 1) is None
      assert selfdrive.alertType == 'resumeBlocked/noEntry'
      assert selfdrive.alertSound == car.CarControl.HUDControl.AudibleAlert.refuse
      state.buttonEvents = []
      state.buttonEnable = card.CI.CS.update_button_enable(state.buttonEvents)
      assert not state.buttonEnable
      axis, selfdrive, command = drive(4, observed_state=state, gas=True)
      assert not sd.events.contains(ET.ENABLE)
      assert not sd.enabled and not axis.longitudinalActive and not command.longActive
      assert axis.lateralActive and command.latActive
      assert receive(10_040_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=state)
    finally:
      case.doCleanups()


@pytest.mark.parametrize('fault,lateral_allowed', [
  ('unarmed', False), ('pause', False), ('native_denied', False), ('native_invalid', False),
  ('panda_rx', False), ('eps_permanent', False), ('door', True), ('can_invalid', False),
])
def test_resume_refusal_preserves_lateral_only_with_current_native_grant(fault, lateral_allowed):
  with OpenpilotPrefix():
    case, (sd, _, drive, card, receive) = make_callers()
    try:
      state = car_state()
      state.vCruise = state.vCruiseCluster = 255.
      state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.accelCruise, pressed=False)]
      options = {'observed_state': state, 'gas': True}
      if fault == 'unarmed':
        card.aol_card_intent.allowed_latch = False
        options['latched'] = False
      elif fault == 'pause':
        options['pause'] = True
      elif fault == 'native_denied':
        options['allowed'] = False
      elif fault == 'native_invalid':
        options['native_valid'] = False
      elif fault == 'panda_rx':
        options['panda_fault'] = True
      elif fault == 'eps_permanent':
        state.steerFaultPermanent = True
      elif fault == 'door':
        state.doorOpen = True
      elif fault == 'can_invalid':
        state.canValid = False
      axis, selfdrive, command = drive(2, **options)
      assert EventName.resumeBlocked in sd.events.names
      assert not sd.enabled and not selfdrive.enabled and not command.longActive and not axis.longitudinalActive
      assert axis.lateralActive == command.latActive == lateral_allowed
      assert not command.cruiseControl.resume
      if fault in ('native_denied', 'native_invalid', 'panda_rx', 'eps_permanent', 'door', 'can_invalid'):
        assert receive(10_020_000_000, axis=sd.pm.messages['aolAxisState'],
                       event=sd.pm.messages['onroadEvents'], state=state) == lateral_allowed
      if lateral_allowed:
        assert EventName.doorOpen in sd.events.names
        native = current_native(sd.sm, sd.CP, now_ns=10_020_000_000, axis_session_id=sd.aol_session_id)
        assert native is not None and native.requestedLateral and native.lateralAllowed
    finally:
      case.doCleanups()
