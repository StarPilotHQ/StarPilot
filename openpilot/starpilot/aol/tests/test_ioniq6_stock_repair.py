"""Exact stock Ioniq AOL admission and saved-request cleanup regression."""
import ast
from pathlib import Path
from types import SimpleNamespace
import pytest
from opendbc.car.structs import car
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.starpilot.car.hyundai.aol import (policy_for, qualified_ioniq6, ioniq6_settings_capable,
                                               native_profile_supported, native_accepts_cp)
from openpilot.starpilot.aol.intent import AolCardIntent, AolSettings


def stock(alternate):
  cp = car.CarParams.new_message()
  cp.brand = 'hyundai'
  cp.carFingerprint = CAR.HYUNDAI_IONIQ_6
  cp.flags = int(HyundaiFlags.CANFD | HyundaiFlags.EV | HyundaiFlags.CANFD_LKA_STEER_MSG)
  if alternate:
    cp.flags |= int(HyundaiFlags.CANFD_LKA_STEER_MSG_ALT)
  cp.pcmCruise = True
  cp.openpilotLongitudinalControl = False
  cp.radarUnavailable = False
  cp.safetyConfigs = [{'safetyModel': car.CarParams.SafetyModel.hyundaiCanfd,
                       'safetyParam': 0x91 if alternate else 0x11}]
  return cp


@pytest.mark.parametrize('alternate', [False, True])
def test_stock_marker_is_lateral_only_and_native_exact(alternate):
  cp = stock(alternate)
  original = int(cp.safetyConfigs[0].safetyParam)
  policy = policy_for(cp)
  assert policy.intent_supported and policy.runtime_supported and policy.normal_runtime_supported
  assert policy.explicit_latch and policy.safety_param_addition == 0x0800
  assert not policy.distance_personality
  assert ioniq6_settings_capable(cp) and not qualified_ioniq6(cp)
  assert not native_accepts_cp(cp, int(car.CarParams.SafetyModel.hyundaiCanfd), original)
  intent = AolCardIntent(AolSettings(True, 0., 0, 0, (0,0,0), (0,0,0)), explicit_latch=policy.explicit_latch)
  assert intent.explicit_latch and not intent.allowed_latch
  cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
  assert qualified_ioniq6(cp) and ioniq6_settings_capable(cp)
  assert native_profile_supported(int(car.CarParams.SafetyModel.hyundaiCanfd), original | 0x800)
  assert native_accepts_cp(cp, int(car.CarParams.SafetyModel.hyundaiCanfd), original | 0x800)
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
  cp.flags |= int(HyundaiFlags.CANFD_ALT_BUTTONS)
  assert not policy_for(cp).runtime_supported and not qualified_ioniq6(cp)


@pytest.mark.parametrize('alternate', [False, True])
def test_existing_long_profile_retained(alternate):
  cp = stock(alternate)
  cp.pcmCruise = False
  cp.openpilotLongitudinalControl = True
  cp.safetyConfigs[0].safetyParam = 0x8895 if alternate else 0x8815
  assert qualified_ioniq6(cp)
  assert policy_for(cp).intent_supported and policy_for(cp).explicit_latch


def cleanup(cp):
  # Execute the actual saved-request cleanup nodes without initializing processes.
  source = Path(__file__).parents[3] / 'selfdrive/selfdrived/selfdrived.py'
  tree = ast.parse(source.read_text())
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'SelfdriveD')
  method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '__init__')
  # Execute the real startup cleanup condition; constructor side effects stay absent.
  nodes: list[ast.stmt] = [node for node in method.body if isinstance(node, ast.If) and any(
    isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == 'remove'
    and call.args and isinstance(call.args[0], ast.Constant)
    and call.args[0].value in ('AlphaLongitudinalEnabled', 'ExperimentalMode') for call in ast.walk(node))]
  calls = []
  host = SimpleNamespace(CP=cp, params=SimpleNamespace(remove=calls.append))
  exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), {'self': host})
  return calls


def test_actual_cleanup_preserves_request_without_enabling_long():
  cp = stock(True)
  assert cleanup(cp) == []
  cp.passive = True
  cp.dashcamOnly = True
  assert not qualified_ioniq6(cp) and not ioniq6_settings_capable(cp)
  assert cleanup(cp) == []
  cp.carFingerprint = CAR.HYUNDAI_IONIQ_5
  assert cleanup(cp) == []
  cp.openpilotLongitudinalControl = True
  assert cleanup(cp) == []
