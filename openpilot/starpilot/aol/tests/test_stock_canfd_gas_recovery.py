from contextlib import ExitStack
from dataclasses import replace

from unittest import mock

import pytest

from openpilot.cereal import log
from openpilot.starpilot.aol.tests.test_stock_location_family_intent import stock_family_callers
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE


FAMILIES = ['canfd_radar', 'canfd_lka', 'canfd_lka_alt', 'canfd_camera', 'canfd_ccnc_camera']


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('interruption', ['source_expiry', 'temporary_eps'])
def test_held_gas_keeps_stock_canfd_aol_intent_through_recoverable_withdrawal(family, interruption):
  with ExitStack() as cleanup:
    sd, drive, card, receive, state = stock_family_callers(cleanup, family)
    sd.aol_settings = card.aol_card_intent.settings
    state.gasPressed = True
    for tick in range(2, 5):
      axis, _, command = drive(tick, observed_state=state, gas=True, native_request_long=False, panda_controls_allowed=False)
      assert axis.lateralActive and command.latActive and not command.longActive
      assert receive(BASE + tick * 10_000_000, state=state,
                     axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    deadline = axis.validUntilMonoTime
    if interruption == 'source_expiry':
      with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=BASE + 70_011_000):
        sd.publish_axis_proof(sd.CS_prev)
      axis = sd.pm.messages['aolAxisState'].aolAxisState
      assert deadline < axis.observedMonoTime
      assert not axis.lateralActive and not axis.longitudinalActive
      assert not sd.aol_authority_lost and str(axis.faultReason) == 'availabilityPause'
      assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      assert receive(BASE + 70_011_000, state=state,
                     axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      recovery_tick = 8
    else:
      state.steerFaultTemporary = True
      for tick in range(5, 21):
        axis, _, command = drive(tick, observed_state=state, gas=True, native_request_lat=False,
          allowed=False, native_request_long=False, panda_controls_allowed=False)
        assert not axis.lateralActive and not command.latActive and not command.longActive
        assert receive(BASE + tick * 10_000_000, state=state,
                       axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      state.steerFaultTemporary = False
      assert state.gasPressed
      assert receive(BASE + 210_000_000, state=state,
                     axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      axis, _, command = drive(21, observed_state=state, gas=True, native_request_lat=False,
        allowed=False, native_request_long=False, panda_controls_allowed=False)
      assert not axis.lateralActive and not command.latActive and not command.longActive
      assert receive(BASE + 210_000_001, state=state,
                     axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      recovery_tick = 22
    assert card.aol_card_intent.allowed_latch
    for tick in range(recovery_tick, recovery_tick + 3):
      axis, _, command = drive(tick, observed_state=state, gas=True, native_request_long=False, panda_controls_allowed=False)
      assert receive(BASE + tick * 10_000_000, state=state,
                     axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      assert axis.lateralActive and command.latActive and not command.longActive and not sd.enabled


@pytest.mark.parametrize('family', FAMILIES)
def test_old_eps_notification_does_not_disarm_recovered_stock_canfd_while_gas_held(family):
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  from openpilot.starpilot.aol.tests.test_stock_eps_recovery import recovered_pause
  from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, sample, change, native_change

  with ExitStack() as cleanup:
    _, _, card, _, state = stock_family_callers(cleanup, family)
    state.cruiseState.available = True
    feedback = TransportPauseFeedback(card.CP)
    def matching_sample(*args, **kwargs):
      peers, native = sample(*args, **kwargs)
      slots = tuple(replace(slot, alternativeExperience=card.CP.alternativeExperience) for slot in native.pandaInventory)
      native = native_change(peers, native, pandaInventory=slots)
      change(peers, 'pandaStates', lambda pandas: setattr(pandas[0], 'alternativeExperience', card.CP.alternativeExperience))
      return peers, native
    sm, _ = matching_sample(card.CP, NOW, NOW, 1)
    assert not feedback.observe(sm, card.CP, state, NOW, latched=True, intent_producer_session=PRODUCER)
    assert feedback.lateral_only_baseline
    state.gasPressed = True
    with mock.patch('openpilot.starpilot.aol.tests.test_stock_eps_recovery.sample', side_effect=matching_sample):
      sm, native, now = recovered_pause(card.CP, state, feedback)
    assert state.gasPressed and not state.steerFaultTemporary
    baseline = (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns)
    assert not feedback.observe(sm, card.CP, state, now, latched=True, intent_producer_session=PRODUCER)
    assert feedback.temporary_host_qualified and not feedback.failed
    assert not native.lateralAllowed and not native.longitudinalAllowed
    assert baseline == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns)
