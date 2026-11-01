"""Real SelfdriveD cancellation caller with actual factory CP and typed wire states."""
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
import pytest
from opendbc.car import structs
from opendbc.car.hyundai.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.aol.intent import AolSettings, AOL_TOGGLE
from openpilot.starpilot.aol.runtime import AxisDecision
from openpilot.starpilot.aol.wire import IntentState, SafetyState, PandaSlot
from openpilot.starpilot.aol.tests.test_canfd_stock_profiles import params
from openpilot.starpilot.car.hyundai.aol import policy_for


class Messages(dict):
  ready = True
  def all_checks(self, services=None):
    return self.ready


@pytest.mark.parametrize('identity', [CAR.KIA_EV6, CAR.HYUNDAI_IONIQ_5, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN])
@pytest.mark.parametrize('alternate', [False, True])
@pytest.mark.parametrize('two_panda', [False, True])
@pytest.mark.parametrize('case', ['retain', 'prior_off', 'settings_off', 'configured_cancel', 'intent_off', 'stale_source',
                                 'native_denied', 'native_expired', 'wrong_session', 'wrong_inventory', 'hard_event',
                                 'model', 'dm', 'reverse', 'steer_fault'])
def test_actual_selfdrived_cancel_keeps_only_current_owned_lateral(identity, alternate, two_panda, case):
  now = 4_000_000_000
  cp = params(identity, alternate=alternate)
  cp.safetyConfigs[-1].safetyParam |= policy_for(cp).safety_param_addition
  admitted = True  # Registered stock CAN-FD owners also qualify behind an inert companion.
  if two_panda:
    owner = structs.CarParams.SafetyConfig(safetyModel=str(cp.safetyConfigs[-1].safetyModel),
                                           safetyParam=int(cp.safetyConfigs[-1].safetyParam))
    expected_owner = (str(owner.safetyModel), int(owner.safetyParam))
    cp.safetyConfigs = [structs.CarParams.SafetyConfig(safetyModel='noOutput'), owner]
    assert (str(cp.safetyConfigs[-1].safetyModel), int(cp.safetyConfigs[-1].safetyParam)) == expected_owner
  from openpilot.starpilot.car.hyundai.aol import independent_cruise_buttons_supported
  assert independent_cruise_buttons_supported(cp, marked_only=True) == admitted
  assert cp.alternativeExperience == 0
  config = cp.safetyConfigs[-1]
  model, word = int(config.safetyModel.raw), int(config.safetyParam)
  intent = IntentState('card', 1, now, now, now + 200_000_000, True, False, False, True, True)
  slot = PandaSlot(int(two_panda), 'panda', model, word, cp.alternativeExperience)
  inventory = (PandaSlot(0, 'aux', int(cp.safetyConfigs[0].safetyModel.raw), 0, cp.alternativeExperience), slot) if two_panda else (slot,)
  native = SafetyState(1, True, now, now + 200_000_000, model, word, True, False, True, False,
                       'panda', 'axis', now, inventory)
  cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=10.)
  cs.cruiseState.available = True
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  sm = Messages(driverMonitoringState=SimpleNamespace(alertLevel=0, lockout=False, alwaysOnLockout=False),
                extrinsicsCalibration=SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated))
  sd = SimpleNamespace(CP=cp, active=False, enabled=False, initialized=True, sm=sm,
                       aol_settings=AolSettings(True, 0., AOL_TOGGLE, 0, (0,0,0), (0,0,0)),
                       aol_axis_decision=AxisDecision(lateral_active=True, native_acknowledged=True),
                       aol_car_state_log_ns=now, aol_session_id='axis', aol_dm_lateral_inhibit=False,
                       nostalgia_paddle_cancel=False, events=Events())
  sd.events.add(log.OnroadEvent.EventName.buttonCancel)
  if case == 'prior_off':
    sd.aol_axis_decision = AxisDecision()
  elif case == 'settings_off':
    sd.aol_settings = replace(sd.aol_settings, enabled=False)
  elif case == 'configured_cancel':
    sd.aol_settings = replace(sd.aol_settings, cancel_actions=(3,0,0))
  elif case == 'intent_off':
    intent = replace(intent, allowedLatch=False)
  elif case == 'stale_source':
    sd.aol_car_state_log_ns = now - 30_000_001
    intent = replace(intent, carStateLogMonoTime=sd.aol_car_state_log_ns)
  elif case == 'native_denied':
    native = replace(native, lateralAllowed=False)
  elif case == 'native_expired':
    native = replace(native, validUntilMonoTime=now-1)
  elif case == 'wrong_session':
    native = replace(native, axisSessionId='other')
  elif case == 'wrong_inventory':
    native = replace(native, pandaInventory=(*inventory[:-1], replace(slot, safetyParam=word ^ 0x80)))
  elif case == 'hard_event':
    sd.events.add(log.OnroadEvent.EventName.controlsMismatch)
  elif case == 'model':
    sm.ready = False
  elif case == 'dm':
    sm['driverMonitoringState'].lockout = True
  elif case == 'reverse':
    cs.gearShifter = 'reverse'
  elif case == 'steer_fault':
    cs.steerFaultPermanent = True
  result = SelfdriveD._decide_aol_axes(cast(SelfdriveD, sd), cs, intent, native, now_ns=now)
  assert result.lateral_active == (case == 'retain' and admitted)
  assert not result.longitudinal_active
  assert log.OnroadEvent.EventName.buttonCancel in sd.events.names
