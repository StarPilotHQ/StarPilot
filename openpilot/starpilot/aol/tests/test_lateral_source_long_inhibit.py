from contextlib import ExitStack
from unittest import mock

import pytest

from openpilot.cereal import log
from openpilot.starpilot.aol.tests.test_stock_location_family_intent import stock_family_callers
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE


@pytest.mark.parametrize('family', ['canfd_radar', 'canfd_lka', 'canfd_lka_alt', 'canfd_camera', 'canfd_ccnc_camera'])
@pytest.mark.parametrize('gas', [False, True])
@pytest.mark.parametrize('steering_override', [False, True])
@pytest.mark.parametrize('long_inhibit', [False, True])
def test_longitudinal_inhibit_does_not_revoke_established_lateral_intent(family, gas, steering_override, long_inhibit):
  with ExitStack() as cleanup:
    sd, drive, card, receive, state = stock_family_callers(cleanup, family)
    sd.aol_settings = card.aol_card_intent.settings
    state.gasPressed = gas
    state.steeringPressed = steering_override
    for tick in range(2, 5):
      axis, _, command = drive(tick, observed_state=state, gas=gas,
        native_request_long=False, panda_controls_allowed=False)
      assert axis.lateralActive and command.latActive and not command.longActive
      assert receive(BASE + tick * 10_000_000, state=state,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    original = sd.aol_published_input_proof
    sd.aol_availability_long_inhibit = long_inhibit
    now = BASE + 106_226_823
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now):
      sd.publish_axis_proof(sd.CS_prev)
    paused = sd.pm.messages['aolAxisState'].aolAxisState
    assert not paused.lateralActive and not paused.longitudinalActive
    assert paused.sourceCarStateMonoTime == 0
    assert str(paused.faultReason) == 'availabilityPause'
    assert paused.desiredLateral and paused.nativeAcknowledged
    assert not sd.aol_authority_lost
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    assert original[3] < now
    assert sd.aol_availability_long_inhibit == long_inhibit
    assert receive(now, state=state,
      axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    for tick in range(13, 16):
      recovered, _, command = drive(tick, observed_state=state, gas=gas,
        native_request_long=False, panda_controls_allowed=False)
      assert receive(BASE + tick * 10_000_000, state=state,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      assert recovered.lateralActive and command.latActive
      assert not recovered.longitudinalActive and not command.longActive and not sd.enabled
