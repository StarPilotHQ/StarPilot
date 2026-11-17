
from openpilot.common.parameterized import parameterized
from openpilot.common.test import OpenpilotTestCase
from openpilot.system.hardware import fan_controller
from openpilot.system.hardware.fan_controller import FanController

ALL_CONTROLLERS = [FanController]

class TestFanController(OpenpilotTestCase):
  def wind_up(self, controller, ignition=True):
    for _ in range(1000):
      controller.update(100, ignition)

  def wind_down(self, controller, ignition=False):
    for _ in range(1000):
      controller.update(10, ignition)

  @parameterized.expand(ALL_CONTROLLERS)
  def test_hot_onroad(self, controller_class):
    controller = controller_class(2)
    self.wind_up(controller)
    assert controller.update(100, True) >= 70

  @parameterized.expand(ALL_CONTROLLERS)
  def test_offroad_limits(self, controller_class):
    controller = controller_class(2)
    self.wind_up(controller)
    assert controller.update(100, False) <= 30

  @parameterized.expand(ALL_CONTROLLERS)
  def test_no_fan_wear(self, controller_class):
    controller = controller_class(2)
    self.wind_down(controller)
    assert controller.update(10, False) == 0

  @parameterized.expand(ALL_CONTROLLERS)
  def test_limited(self, controller_class):
    controller = controller_class(2)
    self.wind_up(controller, True)
    assert controller.update(100, True) == 100

  @parameterized.expand(ALL_CONTROLLERS)
  def test_windup_speed(self, controller_class):
    controller = controller_class(2)
    self.wind_down(controller, True)
    for _ in range(10):
      controller.update(90, True)
    assert controller.update(90, True) >= 60

  def test_aggressive_runs_more_fan(self):
    for temp in (70., 75., 80.):
      stock, aggressive = FanController(2), FanController(2)
      for _ in range(20):
        stock_out = stock.update(temp, True)
        aggressive_out = aggressive.update(temp, True, True)
      assert aggressive_out > stock_out, temp

  def test_aggressive_keeps_offroad_limit(self):
    controller = FanController(2)
    for _ in range(1000):
      controller.update(100, False, True)
    assert controller.update(100, False, True) <= 30

  def test_mode_switch_starts_from_clean_state(self):
    controller = FanController(2)
    for _ in range(1000):
      controller.update(100, True)
    fresh = FanController(2)
    fresh.update(70, True)
    assert controller.update(70, True, True) == fresh.update(70, True, True)

  def test_mici_ignores_aggressive(self):
    original = fan_controller.IS_MICI
    fan_controller.IS_MICI = True
    try:
      stock, aggressive = FanController(2), FanController(2)
      for _ in range(20):
        stock_out = stock.update(70, True)
        aggressive_out = aggressive.update(70, True, True)
      assert aggressive_out == stock_out
    finally:
      fan_controller.IS_MICI = original
