"""Stock Honda source gaps retain every physical and configuration veto."""

from unittest import mock
import pytest
from opendbc.car.structs import car
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests.stock_lateral_test_helpers import honda_family_case as family_case
from openpilot.starpilot.aol.tests.legacy_lag_test_helpers import source_availability_refusals


@pytest.mark.parametrize(
  'veto', ['wrong_make', 'unmarked', 'wrong_config', 'dm_lockout', 'physical_cancel', 'brake', 'main_off', 'speed_too_low', 'mixed_immediate']
)
def test_honda_host_gap_retains_every_non_nominal_veto(veto):
  with OpenpilotPrefix():
    case = family_case('honda', False)
    try:
      sd, controls, step, card, receive = case.family_callers(False)
      baseline, _, command = step(2)
      assert baseline.lateralActive and command.latActive
      accepted = messaging.new_message('aolIntentWire', 0, valid=sd.sm.valid['aolIntentWire'], logMonoTime=sd.sm.logMonoTime['aolIntentWire'])
      accepted.aolIntentWire = sd.sm['aolIntentWire']
      assert accepted.logMonoTime == baseline.sourceCarStateMonoTime
      original_events = sd.update_events

      def events(state):
        result = original_events(state)
        sd.events.add(log.OnroadEvent.EventName.selfdrivedLagging)
        if veto == 'dm_lockout':
          frame = messaging.new_message('driverMonitoringState', valid=True, logMonoTime=10_055_000_000)
          frame.driverMonitoringState = sd.sm['driverMonitoringState']
          frame.driverMonitoringState.lockout = True
          sd.sm.update_msgs(10.055, [frame.as_reader()])
        elif veto == 'speed_too_low':
          sd.events.add(log.OnroadEvent.EventName.speedTooLow)
        elif veto == 'mixed_immediate':
          sd.events.add(log.OnroadEvent.EventName.controlsMismatch)
        return result

      if veto in ('wrong_make', 'unmarked', 'wrong_config'):
        cp = sd.CP.as_builder()
        if veto == 'wrong_make':
          cp.brand = 'hyundai'
        elif veto == 'unmarked':
          cp.alternativeExperience = 0
        else:
          cp.safetyConfigs[0].safetyParam ^= 1
        sd.CP = cp.as_reader()
      elif veto in ('physical_cancel', 'brake', 'main_off'):
        # Deliver a NEW actual serialized CarState envelope through the fixture's
        # receive boundary. Never mutate a previously accepted immutable reader.
        from openpilot.starpilot.aol.tests.stock_lateral_test_helpers import car_state

        physical = car_state()
        if veto == 'physical_cancel':
          physical.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.cancel, pressed=True)]
        elif veto == 'brake':
          physical.brakePressed = True
        else:
          physical.cruiseState.available = False
        physical.cruiseState.available = veto != 'main_off'
        packet = messaging.new_message('carState', valid=True, logMonoTime=10_030_000_000)
        packet.carState = physical
        denied, _, fault_command = step(3, car_state_message=packet)
        witnessed = sd.CS_prev
        if veto == 'physical_cancel':
          assert any(e.type == car.CarState.ButtonEvent.Type.cancel and e.pressed for e in witnessed.buttonEvents)
        elif veto == 'brake':
          assert witnessed.brakePressed
        else:
          assert not witnessed.cruiseState.available
        # Freeze the exact fault-source intent received during this real step.
        accepted = messaging.new_message('aolIntentWire', 0, valid=sd.sm.valid['aolIntentWire'], logMonoTime=sd.sm.logMonoTime['aolIntentWire'])
        accepted.aolIntentWire = sd.sm['aolIntentWire']
      with source_availability_refusals(sd), mock.patch.object(sd, 'update_events', side_effect=events):
        off, _, command = step(6 if veto in ('physical_cancel', 'brake', 'main_off') else 5, timeout=True, offset_ns=5_000_000, intent_message=accepted)
      assert not off.lateralActive and not off.longitudinalActive
      assert not command.latActive and not command.longActive
      assert not sd.aol_ordinary_source_pause, (veto, off.to_dict())
      assert off.sourceCarStateMonoTime == 0 and baseline.validUntilMonoTime < off.observedMonoTime
      assert not sd.events.contains('enable')
      if veto == 'dm_lockout':
        assert sd.sm['driverMonitoringState'].lockout
      elif veto == 'speed_too_low':
        assert log.OnroadEvent.EventName.speedTooLow in sd.events.names
    finally:
      case.doCleanups()


@pytest.mark.parametrize(('mph', 'brake', 'warm', 'eps_fault', 'grant', 'expected'), [
  (25, True, True, False, True, True),
  (16, True, True, False, True, True),
  (25, False, True, False, True, True),
  (25, True, False, False, True, False),
  (16, True, True, True, True, False),
  (16, True, True, False, False, False),
  (0, True, True, False, True, False),
])
def test_fit_armed_lateral_below_pcm_cruise_minimum(mph, brake, warm, eps_fault, grant, expected):
  from opendbc.car.honda.values import CAR
  from openpilot.starpilot.aol.tests.stock_lateral_test_helpers import car_state

  with OpenpilotPrefix():
    case = family_case('honda', False)
    try:
      case.family_cp = case.interface_type.get_non_essential_params(CAR.HONDA_FIT).as_reader()
      assert case.family_cp.minSteerSpeed == 0.0 and case.family_cp.minEnableSpeed > 25 * .44704
      sd, _, step, _, _ = case.family_callers(False)
      assert sd.aol_ever_active
      sd.aol_ever_active = warm
      physical = car_state(brake=brake)
      physical.vEgo, physical.standstill = mph * .44704, mph == 0
      physical.steerFaultTemporary = eps_fault
      physical.cruiseState.available = True
      axis, _, command = step(2, observed_state=physical, allowed=grant)
      assert log.OnroadEvent.EventName.belowEngageSpeed in sd.events.names
      assert command.latActive == expected
      assert not command.longActive and not axis.longitudinalActive
    finally:
      case.doCleanups()
