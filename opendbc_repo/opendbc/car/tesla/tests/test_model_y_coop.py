"""Model Y's real parser/controller stays within the existing native Tesla limits."""
import os

import pytest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.tesla.carcontroller import CarController
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.screen_button import apply_modern_aol, modern_aol_qualified
from opendbc.car.tesla.tests.test_screen_button import screen_params
from opendbc.car.tesla.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.tesla.coop_preferences import prepare


@pytest.mark.parametrize('mode', ('stock', 'ordinary', 'screen'))
@pytest.mark.parametrize('enabled', (False, True))
@pytest.mark.parametrize('long', (False, True))
@pytest.mark.parametrize('brake_disengage', (False, True))
@pytest.mark.parametrize('speed', (0., 10., 30.))
def test_model_y_controller_frames_and_driver_override(mode, enabled, long, brake_disengage, speed):
  cp = screen_params(CAR.TESLA_MODEL_Y, harness=mode == 'screen', long=long)
  apply_modern_aol(cp, mode != 'stock', mode == 'screen', brake_disengage)
  prepare(cp, enabled)
  assert bool(cp.safetyConfigs[0].safetyParam & 256) is enabled
  cp.alternativeExperience = 32 if mode != 'stock' else 0
  assert modern_aol_qualified(cp, marked_only=True) is (mode != 'stock')
  ci = CarInterface(cp)
  controller = CarController(DBC[CAR.TESLA_MODEL_Y], cp)
  assert controller.coop_enabled is enabled
  assert ci.CS.cooperative_steering is enabled
  packer = CANPacker('tesla_model3_party')
  native = libsafety_py.libsafety
  native.init_tests()
  native.set_alternative_experience(cp.alternativeExperience)
  try:
    result = native.set_safety_hooks(10, cp.safetyConfigs[0].safetyParam)
    release_long = long and os.environ.get('TESLA_SCREEN_NATIVE_RELEASE') == '1'
    if release_long and mode != 'stock':
      assert result == -1
      return
    assert result == 0
    native.set_aol_test_heartbeat(True)
    cc = structs.CarControl(latActive=True, enabled=True)
    offsets = []
    accepted_steering = 0
    for tick in range(220):
      now = 1_000_000_000 + tick * 10_000_000
      native.set_timer(now // 1000)
      torque = 1.5 if tick < 100 else -1.5 if tick < 210 else 5.01
      hands = 3 if tick >= 215 else 0
      messages = (
        ('DAS_control', 2, {}), ('DAS_steeringControl', 2, {}),
        ('SCCM_steeringAngleSensor', 2, {}), ('DAS_status', 2, {}), ('DAS_settings', 2, {}),
        ('DI_speed', 0, {'DI_vehicleSpeed': speed * 3.6}),
        ('ESP_B', 0, {'ESP_vehicleSpeed': speed * 3.6, 'ESP_wheelSpeedsQF': 1, 'ESP_vehicleStandstillSts': int(speed == 0)}),
        ('EPAS3S_sysStatus', 0, {'EPAS3S_internalSAS': 0, 'EPAS3S_eacStatus': 1,
                               'EPAS3S_handsOnLevel': hands, 'EPAS3S_torsionBarTorque': -torque}),
        ('DI_systemStatus', 0, {'DI_gear': 4}), ('ESP_status', 0, {'ESP_driverBrakeApply': 1}),
        ('UI_warning', 0, {'buckleStatus': 1}), ('DI_state', 0, {'DI_cruiseState': 2 if tick >= 12 else 0}),
      )
      frames = [packer.make_can_msg(name, bus, values) for name, bus, values in messages]
      for address, data, bus in frames:
        assert native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
      native.safety_tick()
      cs = ci.update([(now, frames)])
      if tick < 14:
        continue
      assert cs.canValid and not cs.canTimeout
      if mode != 'stock':
        native.aol_set_host_request(1)
      output, sends = controller.update(cc.as_reader(), ci.CS, now)
      for address, data, bus in sends:
        accepted = native.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data))
        assert bool(accepted) is not (release_long and address == 0x2B9), (tick, address, output.steeringAngleDeg)
        accepted_steering += int(address == 0x488)
      if 30 <= tick < 100:
        offsets.append(controller.coop_steer.cooperative_offset_deg)
      if (tick >= 210 and enabled) or tick >= 215:
        assert cs.steeringDisengage
        assert not native.get_controls_allowed()
        assert not controller.steering_limit_info
    assert accepted_steering > 90
    assert any(abs(offset) > 0 for offset in offsets) is enabled
  finally:
    native.init_tests()
    native.set_alternative_experience(0)
    native.set_safety_hooks(0, 0)
