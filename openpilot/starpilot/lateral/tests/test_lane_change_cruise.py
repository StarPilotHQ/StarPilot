"""Actual lane-change state machine with current stock/OP cruise evidence."""
import json
import ast
from pathlib import Path
from dataclasses import replace
import unittest

from opendbc.car import structs
from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
from openpilot.starpilot.lateral.auto_lane_change import auto_trigger_engaged
from openpilot.starpilot.lateral.lane_change_preferences import LaneChangePolicy, decode, to_value

NOW = 10_000_000_000


class Messages:
  def __init__(self, *, stock=False, normal=False):
    self.car = structs.CarState.new_message(vEgo=15., canValid=True)
    self.car.cruiseState.enabled = stock
    self.car.cruiseState.available = True
    self.control = structs.CarControl.new_message(latActive=True, enabled=normal)
    self.seen = dict.fromkeys(('carState', 'carControl'), True)
    self.alive, self.valid = self.seen.copy(), self.seen.copy()
    self.logMonoTime = dict.fromkeys(self.seen, NOW - 10_000_000)

  def __getitem__(self, name):
    return {'carState': self.car, 'carControl': self.control}[name]


class CruiseGateTests(unittest.TestCase):
  def setUp(self):
    self.policy = LaneChangePolicy(auto_lane_change=True, auto_delay_s=0., auto_requires_cruise=True)

  def gate(self, sm, op_long, policy=None):
    return auto_trigger_engaged(sm, policy or self.policy, openpilot_longitudinal=op_long, now_mono_ns=NOW)

  def test_stock_and_openpilot_engagement_have_independent_sources(self):
    for op_long in (False, True):
      for stock in (False, True):
        for normal in (False, True):
          sm = Messages(stock=stock, normal=normal)
          sm.control.longActive = False
          self.assertEqual(self.gate(sm, op_long), normal if op_long else stock)

  def test_option_off_preserves_lateral_only_automatic_changes(self):
    for op_long in (False, True):
      sm = Messages()
      self.assertTrue(self.gate(sm, op_long, replace(self.policy, auto_requires_cruise=False)))
      sm.control.latActive = False
      self.assertFalse(self.gate(sm, op_long, replace(self.policy, auto_requires_cruise=False)))

  def test_missing_invalid_stale_or_future_evidence_never_qualifies(self):
    for op_long in (False, True):
      for service in ('carState', 'carControl'):
        for field in ('seen', 'alive', 'valid', 'stale', 'future'):
          sm = Messages(stock=True, normal=True)
          if field in ('stale', 'future'):
            sm.logMonoTime[service] = NOW - 100_000_000 if field == 'stale' else NOW + 1
          else:
            getattr(sm, field)[service] = False
          self.assertFalse(self.gate(sm, op_long), (op_long, service, field))

  def test_held_signal_cannot_start_when_cruise_engages_but_manual_nudge_works(self):
    for op_long in (False, True):
      self._exercise_signal(op_long)

  def _exercise_signal(self, op_long):
    sm = Messages()
    helper = DesireHelper(self.policy)
    def step():
      helper.update(sm.car, True, 1., engaged=self.gate(sm, op_long), auto_evidence=True)
    step()
    sm.car.rightBlinker = True
    step()
    step()
    self.assertEqual(helper.lane_change_state, log.LaneChangeState.preLaneChange)
    sm.car.cruiseState.enabled = sm.control.enabled = True
    for _ in range(5):
      step()
    self.assertEqual(helper.lane_change_state, log.LaneChangeState.preLaneChange)
    sm.car.rightBlinker = False
    step()
    sm.car.rightBlinker = True
    step()
    step()
    self.assertEqual(helper.lane_change_state, log.LaneChangeState.laneChangeStarting)
    sm.car.cruiseState.enabled = sm.control.enabled = False
    step()
    self.assertEqual(helper.lane_change_state, log.LaneChangeState.laneChangeStarting)
    helper = DesireHelper(self.policy)
    sm.car.rightBlinker = False
    step()
    sm.car.rightBlinker = True
    step()
    sm.car.steeringPressed, sm.car.steeringTorque = True, -1.
    step()
    self.assertEqual(helper.lane_change_state, log.LaneChangeState.laneChangeStarting)

  def test_blindspot_still_blocks_with_cruise_engaged(self):
    sm = Messages(stock=True, normal=True)
    helper = DesireHelper(self.policy)
    helper.update(sm.car, True, 1., engaged=True, auto_evidence=True)
    sm.car.rightBlinker, sm.car.rightBlindspot = True, True
    for _ in range(4):
      helper.update(sm.car, True, 1., engaged=self.gate(sm, True), auto_evidence=True)
    self.assertEqual(helper.lane_change_state, log.LaneChangeState.preLaneChange)

  def test_old_documents_keep_default_and_new_boolean_is_strict(self):
    current = to_value(self.policy)
    self.assertEqual(decode(json.dumps(current).encode()), self.policy)
    old = {k: v for k, v in current.items() if k != 'autoRequiresCruise'}
    old['version'] = 5
    self.assertFalse(decode(json.dumps(old).encode()).auto_requires_cruise)
    for bad in (1, 'true', None, [], {}):
      self.assertIsNone(decode(json.dumps({**current, 'autoRequiresCruise': bad}).encode()))
    with self.assertRaises(ValueError):
      to_value(replace(self.policy, auto_requires_cruise=1))

  def test_actual_modeld_passes_the_gate_into_the_existing_state_machine(self):
    source = Path(__file__).resolve().parents[3] / 'selfdrive/modeld/modeld.py'
    tree = ast.parse(source.read_text())
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and
                isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and
                node.func.value.id == 'DH' and node.func.attr == 'update')
    expression = next(keyword.value for keyword in call.keywords if keyword.arg == 'engaged')
    sm = Messages()
    cp = structs.CarParams.new_message(openpilotLongitudinalControl=True)
    scope = {'auto_trigger_engaged': auto_trigger_engaged, 'sm': sm, 'runtime_lane_policy': self.policy,
             'CP': cp, 'mono_now_ns': NOW, 'engaged': True}
    code = compile(ast.Expression(expression), str(source), 'eval')
    self.assertFalse(eval(code, scope))
    sm.control.enabled = True
    self.assertTrue(eval(code, scope))
