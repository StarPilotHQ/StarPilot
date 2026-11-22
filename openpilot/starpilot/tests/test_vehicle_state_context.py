"""Synthetic envelopes exercise the actual Card bridge, not native admission."""
import ast
import inspect
import textwrap
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.selfdrive.car import card
from openpilot.starpilot.vehicle_startup import VehicleStartupOwner


class Envelope(dict):
  def __init__(self):
    super().__init__(carControl=SimpleNamespace(enabled=1), pandaStates=[object(), object()])
    self.seen = dict.fromkeys(self, True)
    self.valid = dict.fromkeys(self, True)
    self.alive = dict.fromkeys(self, True)
    self.logMonoTime = {'carControl': 9_950_000_000, 'pandaStates': 29_950_000_000}
    self.recv_time = dict.fromkeys(self, 9.95)


class TestVehicleStateContext(unittest.TestCase):
  def fixture(self):
    calls = []
    holder = VehicleStartupOwner()
    holder.owner = SimpleNamespace(after_state=lambda **context: calls.append(context))
    instance = SimpleNamespace(vehicle_startup=holder, sm=Envelope(), CI=object())
    return instance, calls

  def invoke(self, instance, clocks=(10_000_000_000, 30_000_000_000)):
    state = object()
    with patch.object(card, 'clock_pair_ns', return_value=clocks):
      card.Car.update_vehicle_state_context(instance, state)
    return state

  def test_separated_clocks_and_exact_owner_forwarding(self):
    instance, calls = self.fixture()
    state = self.invoke(instance)
    self.assertEqual(len(calls), 1)
    result = calls[0]
    self.assertIs(result['state'], state)
    self.assertIs(result['ci'], instance.CI)
    self.assertIs(result['pandas'], instance.sm['pandaStates'])
    self.assertIs(result['control_enabled'], True)
    self.assertIs(result['control_current'], True)
    self.assertIs(result['panda_current'], True)
    self.assertEqual(result['now_ns'], 30_000_000_000)
    self.assertEqual(result['panda_log_ns'], 29_950_000_000)
    self.assertEqual(result['panda_recv_ns'], 29_950_000_000)

  def test_stale_future_absent_and_invalid_evidence(self):
    for service, age in (('carControl', 150_000_001), ('pandaStates', 300_000_001)):
      for evidence in ('producer_stale', 'producer_future', 'producer_absent',
                       'receipt_stale', 'receipt_future', 'receipt_absent', 'seen', 'valid', 'alive'):
        with self.subTest(service=service, evidence=evidence):
          instance, calls = self.fixture()
          producer_now = 10_000_000_000 if service == 'carControl' else 30_000_000_000
          if evidence.startswith('producer'):
            instance.sm.logMonoTime[service] = {'producer_stale': producer_now - age,
                                               'producer_future': producer_now + 1, 'producer_absent': 0}[evidence]
          elif evidence.startswith('receipt'):
            instance.sm.recv_time[service] = {'receipt_stale': (10_000_000_000 - age) / 1e9,
                                             'receipt_future': 10.001, 'receipt_absent': 0}[evidence]
          else:
            getattr(instance.sm, evidence)[service] = False
          self.invoke(instance)
          result = calls[0]
          self.assertIs(result['control_current' if service == 'carControl' else 'panda_current'], False)
          if service == 'pandaStates':
            self.assertEqual(result['panda_recv_ns'], 0)
    instance, calls = self.fixture()
    self.invoke(instance, None)
    self.assertIs(calls[0]['control_current'], False)
    self.assertIs(calls[0]['panda_current'], False)
    self.assertEqual(calls[0]['now_ns'], 0)

  def test_no_holder_or_owner_performs_no_clock_or_envelope_work(self):
    empty = card.Car.__new__(card.Car)
    owned = card.Car.__new__(card.Car)
    owned.vehicle_startup = VehicleStartupOwner()
    for instance in (empty, owned):
      with patch.object(card, 'clock_pair_ns', side_effect=AssertionError('unexpected clock read')):
        card.Car.update_vehicle_state_context(instance, object())
    VehicleStartupOwner().after_state(unused=object())

  def test_state_update_source_order_obligation(self):
    # The full Card fixture requires transport/hardware. Bind the actual caller
    # order explicitly; envelope tests above execute the imported bridge.
    tree = ast.parse(textwrap.dedent(inspect.getsource(card.Car.state_update)))
    calls = []
    for node in ast.walk(tree):
      if isinstance(node, ast.Call):
        calls.append((node.lineno, ast.unparse(node.func)))
    def position(name):
      return min(line for line, function in calls if function == name)
    self.assertLess(position('self.CI.update'), position('self.sm.update'))
    self.assertLess(position('self.sm.update'), position('self.update_vehicle_state_context'))
    self.assertLess(position('self.update_vehicle_state_context'), position('self.update_cruise_speed'))
    helper = ast.parse(textwrap.dedent(inspect.getsource(card.Car.update_cruise_speed)))
    helper_calls = {ast.unparse(node.func): node for node in ast.walk(helper) if isinstance(node, ast.Call)}
    update = helper_calls['self.v_cruise_helper.update_v_cruise']
    initialize = helper_calls['self.v_cruise_helper.initialize_v_cruise']
    self.assertLess(update.lineno, initialize.lineno)
    self.assertEqual(ast.unparse(update.args[0]), 'CS')
    self.assertEqual(ast.unparse(initialize.args[0]), 'self.CS_prev')
    self.assertEqual(ast.unparse(initialize.keywords[0].value), 'self.vehicle_startup.consume_cruise_resume()')
    enable_edge = next(node for node in helper.body[0].body if isinstance(node, ast.If) and initialize in ast.walk(node))
    self.assertEqual(ast.unparse(enable_edge.test), "self.sm['carControl'].enabled and (not self.CC_prev.enabled)")
    self.assertIn(initialize, list(ast.walk(enable_edge)))
    step = inspect.getsource(card.Car.step)
    step_tree = ast.parse(textwrap.dedent(step))
    state_update = next(node for node in ast.walk(step_tree)
                        if isinstance(node, ast.Call) and ast.unparse(node.func) == 'self.state_update')
    self.assertEqual([(kw.arg, ast.unparse(kw.value)) for kw in state_update.keywords], [('defer_radar', 'True')])
    self.assertLess(step.index('self.state_update('), step.index('self.state_publish('))
    self.assertLess(step.index('self.state_publish('), step.index('self.CS_prev = CS'))
