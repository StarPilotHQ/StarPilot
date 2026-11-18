from types import SimpleNamespace as NS
import unittest

from openpilot.cereal import custom
from openpilot.starpilot.lateral.applied_tuning import write_applied_tuning
from openpilot.starpilot.ui.live_developer_sidebar import DeveloperMetrics


class TestAppliedTuning(unittest.TestCase):
  def test_controller_values_win_over_learner_and_pending_manual_target(self):
    controller = NS(torque_params=NS(latAccelFactor=2.7, friction=.17), native_learning_source=None)
    host = NS(settings=NS(valid=True, user_factor=3., user_friction=.2), applied=NS(source='user'))
    parent = custom.StarPilotLateralState.new_message()
    write_applied_tuning(parent, controller, host)
    rows = DeveloperMetrics().observe(drive=1, car=None, delay=None, torque=NS(useParams=True,
      latAccelFactorFiltered=4., frictionCoefficientFiltered=.3), parameters=None, metric=True, applied=parent.torqueTuning)
    self.assertEqual((rows[3].label, rows[3].value), ('LOCKED FRICTION', '0.17000'))
    self.assertEqual((rows[4].label, rows[4].value), ('LOCKED LAT ACCEL', '2.70000'))
    host.settings.user_factor = host.settings.user_friction = None
    host.applied.source = 'learned'
    write_applied_tuning(parent, controller, host)
    self.assertTrue(parent.torqueTuning.learned)
    rows = DeveloperMetrics().observe(drive=1, car=None, delay=None, torque=None, parameters=None,
                                      metric=True, applied=parent.torqueTuning)
    self.assertEqual(rows[3].label, 'LEARNED FRICTION')
    self.assertEqual(rows[4].label, 'LEARNED LAT ACCEL')
    self.assertEqual(controller.torque_params.latAccelFactor, 2.7)

  def test_unknown_non_torque_and_invalid_values_have_no_receipt(self):
    for controller in (NS(), NS(torque_params=NS(latAccelFactor=float('nan'), friction=.2))):
      parent = custom.StarPilotLateralState.new_message()
      write_applied_tuning(parent, controller, None)
      self.assertEqual(parent.torqueTuning.version, 0)

  def test_actual_controller_parameters_are_copied_without_mutating_controller(self):
    from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
    from opendbc.car.car_helpers import interfaces
    identity = 'HYUNDAI_IONIQ_6'
    cp = interfaces[identity].get_non_essential_params(identity)
    controller = LatControlTorque(cp.as_reader(), interfaces[identity](cp), .01)
    before = controller.torque_params.to_dict()
    row = custom.StarPilotLateralState.new_message()
    write_applied_tuning(row, controller, None)
    self.assertEqual(row.torqueTuning.factor, controller.torque_params.latAccelFactor)
    self.assertEqual(row.torqueTuning.friction, controller.torque_params.friction)
    self.assertEqual(controller.torque_params.to_dict(), before)
