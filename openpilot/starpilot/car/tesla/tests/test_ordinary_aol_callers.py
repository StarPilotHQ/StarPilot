"""Ordinary harness AOL through real parser, intent, native and axis callers."""
from types import SimpleNamespace
from typing import cast

import pytest

from opendbc.can import CANPacker
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.screen_button import apply_modern_aol
from opendbc.car.tesla.tests.test_screen_button import screen_params
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.runtime import AxisDecision
from openpilot.starpilot.aol.wire import IntentState, PandaSlot, SafetyState
from openpilot.starpilot.car.tesla.screen_intent import TeslaScreenCardIntent


class HealthyPeers(dict):
  def all_checks(self, services):
    return all(service in self for service in services)


@pytest.mark.parametrize('cooperative', [False, True])
@pytest.mark.parametrize('brake_disengage', [False, True])
def test_ordinary_axis_actual_parser_native_and_selfdrived(cooperative, brake_disengage):
  cp = screen_params(harness=False)
  apply_modern_aol(cp, True, False, brake_disengage)
  cp.safetyConfigs[0].safetyParam |= 256 * cooperative
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  owner = TeslaScreenCardIntent(cp, AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  packer = CANPacker('tesla_model3_party')
  peers = HealthyPeers(modelV2=SimpleNamespace(),
                       extrinsicsCalibration=SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated),
                       driverMonitoringState=SimpleNamespace(lockout=False, alwaysOnLockout=False, alertLevel=0))
  sd = SimpleNamespace(CP=cp, active=False, enabled=False, initialized=True, sm=peers, aol_settings=owner.settings,
                       aol_axis_decision=AxisDecision(), aol_car_state_log_ns=0, aol_session_id='screen-test',
                       aol_dm_lateral_inhibit=False, nostalgia_paddle_cancel=False, aol_cruise_main_required=False,
                       events=Events())
  native = libsafety_py.libsafety
  native.init_tests()
  native.set_alternative_experience(32)
  native.set_safety_hooks(10, cp.safetyConfigs[0].safetyParam)
  native.set_aol_test_heartbeat(True)
  try:
    for tick in range(250):
      now = 1_000_000_000 + tick * 10_000_000
      native.set_timer(now // 1000)
      stock_state = 0 if tick < 20 else 1 if 80 <= tick < 140 else 2
      messages: tuple[tuple[str, int, dict[str, float]], ...] = (
        ('DAS_control', 2, {}), ('DAS_steeringControl', 2, {}),
        ('SCCM_steeringAngleSensor', 2, {}), ('DAS_status', 2, {}), ('DAS_settings', 2, {}),
        ('DI_speed', 0, {'DI_vehicleSpeed': 0}),
        ('ESP_B', 0, {'ESP_vehicleSpeed': 0, 'ESP_wheelSpeedsQF': 1, 'ESP_vehicleStandstillSts': 1}),
        ('EPAS3S_sysStatus', 0, {'EPAS3S_internalSAS': 0, 'EPAS3S_eacStatus': 1,
                               'EPAS3S_handsOnLevel': 3 if tick >= 240 else 0, 'EPAS3S_torsionBarTorque': 0}),
        ('DI_systemStatus', 0, {'DI_gear': 4, 'DI_accelPedalPos': 20 if tick % 30 < 15 else 0}),
        ('ESP_status', 0, {'ESP_driverBrakeApply': 2 if 100 <= tick < 120 else 1}),
        ('UI_warning', 0, {'buckleStatus': 1}),
        ('DI_state', 0, {'DI_cruiseState': stock_state}),
      )
      frames = [packer.make_can_msg(name, bus, values) for name, bus, values in messages]
      for address, data, bus in frames:
        assert native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
      native.safety_tick()
      cs = ci.update([(now, frames)]).as_reader()
      if tick > 1:
        assert cs.canValid and not cs.canTimeout
      owner.update(cs, now_ns=now)
      allowed, pause, long_pause = owner.output(cs)
      intention = IntentState('screen-card', tick + 1, now, now, now + 30_000_000, allowed, pause, long_pause, True, allowed)
      permission = native.aol_get_permission_mask()
      request = native.aol_get_request_mask()
      safety = SafetyState(1, True, now, now + 200_000_000, 10, cp.safetyConfigs[0].safetyParam,
                           bool(permission & 1), bool(permission & 2), bool(request & 1), bool(request & 2),
                           'test-panda', 'screen-test', now,
                           (PandaSlot(0, 'test-panda', 10, cp.safetyConfigs[0].safetyParam, 32, bool(native.get_controls_allowed())),))
      sd.aol_car_state_log_ns = now
      sd.events = Events()
      sd.events.add(log.OnroadEvent.EventName.pcmDisable)
      if not cs.cruiseState.available:
        sd.events.add(log.OnroadEvent.EventName.wrongCarMode)
      decision = SelfdriveD._decide_aol_axes(cast(SelfdriveD, sd), cs, intention, safety, now_ns=now)
      sd.aol_axis_decision = decision
      native.aol_set_host_request(int(decision.desired_lateral))
      assert not decision.longitudinal_active
      if brake_disengage and tick == 140:
        assert decision.desired_lateral and not decision.lateral_active
      elif 22 <= tick < 240:
        assert decision.lateral_active is not (brake_disengage and 100 <= tick < 140)
      elif tick >= 240:
        assert not decision.lateral_active
  finally:
    native.init_tests()
    native.set_alternative_experience(0)
    native.set_safety_hooks(0, 0)
